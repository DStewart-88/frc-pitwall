"""The app's Robot panel behind the scenes: notice when the robot is reachable, see what's new
on its USB stick, and fetch.

  - Every few seconds: is the robot there? (an SSH port check, nothing more)
  - When it shows up, and every couple of minutes while it stays: one listing of the stick,
    plus its free space, to show what's waiting.
  - Automatic fetch only over a wired link (USB, or Ethernet in the pit), never over the radio:
    the laptop can't see whether the robot is enabled, and a fetch while it's driving would
    load the roboRIO and the radio. A Get logs click works on any link.
  - One fetch at a time, from the robot or from a stick plugged into this computer.
Nothing on the robot is ever changed (see pitwall.fetch.robot).
"""

import os
import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional

from ..fetch import Archive, LocalSource, fetch, summary_lines
from ..fetch.archive import EVENT_DIR, unclaimed_codes
from ..fetch.fetch import claim_code
from ..fetch.robot import Robot, RobotError, SftpSource, find_robot, have_sftp, install_hint
from .store import Store

PRESENCE_S = 8.0         # how often to look for the robot
LIST_S = 120.0           # how often to list the stick while the robot stays connected
CLEAR_AT = 1000          # files on the stick before the panel suggests clearing it (Doug, Nov 11)
AUTO_MAX = 40            # more new files than this waits for a click (a stick that was never fetched)
DEFAULT_TEAM = 88


class RobotWatch:
    def __init__(self, store: Store, finder: Callable[..., Optional[Robot]] = find_robot,
                 presence_s: float = PRESENCE_S, list_s: float = LIST_S,
                 on_logs: Optional[Callable[[str], None]] = None):
        self.store = store
        self.on_logs = on_logs or (lambda eid: None)     # an event's logs changed: rebuild it
        self.finder = finder
        self.presence_s = presence_s
        self.list_s = list_s
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._want: Optional[Dict[str, Any]] = None      # a fetch someone asked for
        self._robot: Optional[Robot] = None
        self._listed_at = 0.0
        self._polls = 0
        self.status: Dict[str, Any] = {
            "sftp": have_sftp(), "install_hint": install_hint(), "connected": False, "robot": None,
            "stick": None, "waiting": None, "fetching": False, "progress": None, "last": None,
            "waiting_bytes": None, "error": None, "clear_at": CLEAR_AT, "auto_max": AUTO_MAX,
            "new_codes": []}
        self._codes_checked = False

    # ------------------------------------------------------------ settings

    def settings(self) -> Dict[str, Any]:
        s = self.store.settings()
        return {"archive": s.get("archive") or "", "team": int(s.get("team") or DEFAULT_TEAM),
                "auto_fetch": bool(s.get("auto_fetch", True))}

    def get_status(self) -> Dict[str, Any]:
        if not self._codes_checked:
            self.refresh_codes()
        with self._lock:
            out = dict(self.status)
        out.update(self.settings())
        if out["new_codes"]:            # what the "new event code" prompt needs to offer
            out["profiles"] = [{"id": p["id"], "name": p["name"]} for p in self.store.list_profiles() if not p["error"]]
            out["events"] = [{"id": e["id"], "name": e["name"] or e["id"], "profile": e["profile"]}
                             for e in self.store.list_events()]
        return out

    # ------------------------------------------------------------ new event codes

    def refresh_codes(self) -> None:
        """Note the event codes with match logs in the archive that no Pitwall event takes."""
        self._codes_checked = True
        cfg = self.settings()
        if not cfg["archive"] or not os.path.isdir(cfg["archive"]):
            self._set(new_codes=[])
            return
        claimed = [c for e in self.store.list_events() for c in self.store.event_codes(e["id"])]
        ignored = self.store.settings().get("ignored_codes") or []
        found = unclaimed_codes(Archive(cfg["archive"]), claimed, ignored)
        for f in found:
            m = EVENT_DIR.match(os.path.basename(f["folder"])) if f["folder"] else None
            f["name"] = m.group(3).replace("-", " ") if m and m.group(3) else f["code"]
        self._set(new_codes=found)

    def answer_code(self, code: str, action: str, name: str = "", profile: str = "", event: str = "") -> Dict[str, Any]:
        """What to do with a new event code: "create" an event for it, "add" its logs to `event`,
        or "ignore" it (not an event, such as a scrimmage). Returns {"event": id, "copied": n}."""
        code = code.strip().upper()
        cfg = self.settings()
        if not code:
            raise ValueError("No event code given.")
        if action == "ignore":
            ign = list(self.store.settings().get("ignored_codes") or [])
            if code not in ign:
                self.store.save_settings({"ignored_codes": ign + [code]})
            self.refresh_codes()
            return {"event": None, "copied": 0}
        if not cfg["archive"]:
            raise ValueError("Choose the log archive folder first.")
        info = next((c for c in self.status["new_codes"] if c["code"] == code), {})
        if action == "create":
            if not profile:
                raise ValueError("Pick the robot for this event.")
            eid = self.store.create_event(name.strip() or info.get("name") or code, profile, [], watch=False)
            self.store.save_event(eid, {"code": code, "start": info.get("first", ""), "end": info.get("last", "")})
        elif action == "add":
            ev = self.store.get_event(event)
            if not ev:
                raise ValueError("Pick the event to add these logs to.")
            eid = ev["id"]
            if self.store.event_code(eid):
                self.store.save_event(eid, {"codes": list(dict.fromkeys((ev.get("codes") or []) + [code]))})
            else:
                self.store.save_event(eid, {"code": code})
        else:
            raise ValueError(f"Unknown choice {action!r}.")
        copied = claim_code(Archive(cfg["archive"]), self.store, eid, code)
        self.on_logs(eid)
        self.refresh_codes()
        return {"event": eid, "copied": copied}

    def _set(self, **kw) -> None:
        with self._lock:
            self.status.update(kw)

    # ------------------------------------------------------------ control

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_fetch(self, folder: Optional[str] = None) -> None:
        """Fetch now: from the robot, or from `folder` (a stick plugged into this computer)."""
        if not self.settings()["archive"]:
            raise ValueError("Choose the log archive folder first.")
        if folder is None and not self.status["connected"]:
            raise ValueError("No robot found. Connect by USB or Ethernet (or the radio), or fetch from a USB stick.")
        if self.status["fetching"]:
            raise ValueError("A fetch is already running.")
        self._want = {"folder": folder}
        self._wake.set()

    # ------------------------------------------------------------ the loop

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as e:                     # keep the app alive; show the problem
                traceback.print_exc()
                self._set(error=f"{type(e).__name__}: {e}", fetching=False)
            self._wake.wait(self.presence_s)
            self._wake.clear()

    def poll(self) -> None:
        """One look: find the robot, list it when due, and run any fetch that's wanted or due."""
        want, self._want = self._want, None
        if want and want.get("folder"):
            self._run_fetch(LocalSource(want["folder"]))
            return
        cfg = self.settings()
        if not have_sftp():
            self._set(connected=False, robot=None)
            return
        self._polls += 1
        robot = self.finder(cfg["team"], link_local=(self._polls % 4 == 1 or self._robot is not None))
        if robot is None:
            if self._robot is not None:
                self._set(connected=False, robot=None, stick=None, waiting=None)
            self._robot = None
            return
        appeared = self._robot is None or self._robot.host != robot.host
        self._robot = robot
        self._set(connected=True, robot={"host": robot.host, "via": robot.via, "wired": robot.wired}, error=None)
        due = appeared or time.time() - self._listed_at >= self.list_s or want is not None
        if not due:
            return
        try:
            with SftpSource(robot) as src:
                waiting = self._look(src, cfg)
                auto = cfg["auto_fetch"] and robot.wired and 0 < waiting <= AUTO_MAX and cfg["archive"]
                if want is not None or auto:
                    self._run_fetch(src)
                    self._look(src, cfg)
        except (RobotError, OSError) as e:
            self._set(error=str(e))

    def _look(self, src: SftpSource, cfg: Dict[str, Any]) -> int:
        files = [f for f in src.list() if f.size > 0]
        self._listed_at = time.time()
        waiting = waiting_bytes = None
        if cfg["archive"]:
            archive = Archive(cfg["archive"])
            new = [f for f in files if not archive.has(f.name, f.size)]
            waiting, waiting_bytes = len(new), sum(f.size for f in new)
        stick = {"files": len(files), "bytes": sum(f.size for f in files)}
        space = src.stick_space()
        if space:
            stick.update(space)
        self._set(stick=stick, waiting=waiting, waiting_bytes=waiting_bytes, error=None)
        return waiting or 0

    def _run_fetch(self, source) -> None:
        cfg = self.settings()
        if not cfg["archive"]:
            self._set(error="Choose the log archive folder first.")
            return
        with self._fetch_lock:
            self._set(fetching=True, progress={"stage": "list", "done": 0, "total": 0, "name": ""}, error=None)

            def progress(stage: str, done: int, total: int, name: str) -> None:
                self._set(progress={"stage": stage, "done": done, "total": total, "name": name})
            try:
                res = fetch(source, Archive(cfg["archive"]), self.store, progress)
                handed = {eid: labels for eid, labels in res.handed.items()}
                names = {e["id"]: e["name"] or e["id"] for e in self.store.list_events()}
                self._set(last={"at": time.time(), "source": source.label, "lines": summary_lines(res),
                                "copied": len(res.fetched), "errors": len(res.errors),
                                "handed": [{"id": k, "name": names.get(k, k), "labels": v} for k, v in handed.items()],
                                "no_event": res.no_pitwall_event, "growing": res.growing})
            except (RobotError, OSError, ValueError) as e:
                self._set(error=f"Fetch stopped: {e}")
            finally:
                self._set(fetching=False, progress=None)
                self.refresh_codes()


def archive_ok(path: str) -> Optional[str]:
    """Why `path` can't be the archive, or None if it can."""
    if not path or not os.path.isdir(path):
        return "That folder doesn't exist."
    if os.path.isdir(os.path.join(path, "01 events")) or os.path.isdir(os.path.join(path, "02 practice")):
        return ("That looks like one season's folder. Pick the folder that holds the season folders "
                "(2026, 2027, ...), so next season goes beside it.")
    return None
