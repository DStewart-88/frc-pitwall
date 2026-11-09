"""Background work for the app: bring in new logs and keep each event's report up to date.

One thread watches every event's source folders (a USB stick, the folder the driver station
copies logs to, ...). A log is copied into the event once its size has stopped changing, so
a file still being copied is never read half-written. A second thread rebuilds event reports,
one at a time; the analysis cache means a rebuild after one new match only reads that match.
"""

import json
import os
import re
import shutil
import tempfile
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple

from ..analysis import analyze_logs
from ..event import attach_comparison, strip_for_compare
from ..eventrun import REPORT_VERSION, group_results, make_event, write_event
from ..profile import ProfileError, load_profile
from .store import Store

POLL_S = 4.0          # how often watched folders are checked
SETTLE_S = 2.0        # a file must be unchanged this long before it's copied


def _now() -> float:
    return time.time()


class Worker:
    def __init__(self, store: Store, cache_dir: Optional[str], jobs: int = 2, poll_s: float = POLL_S):
        self.store = store
        self.cache_dir = cache_dir
        self.jobs = jobs
        self.poll_s = poll_s
        self.status: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._queue: List[str] = []
        self._seen: Dict[str, Tuple[int, float, float]] = {}   # source path -> (size, mtime, first seen)
        self._stop = threading.Event()
        self._import_lock = threading.Lock()     # one import at a time (watcher vs. "Add logs")
        self._threads: List[threading.Thread] = []

    # ------------------------------------------------------------ control

    def start(self) -> None:
        for ev in self.store.list_events():
            if self._needs_build(ev["id"]):
                self.request_build(ev["id"])
        for target in (self._build_loop, self._watch_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        with self._wake:
            self._wake.notify_all()

    def request_build(self, eid: str) -> None:
        with self._wake:
            if eid not in self._queue:
                self._queue.append(eid)
            st = self._st(eid)
            if st["state"] in ("idle", "error", "waiting"):
                st.update(state="queued", detail="Waiting to update")
            self._wake.notify_all()

    def get_status(self, eid: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._st(eid))

    def all_status(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self.status.items()}

    def _st(self, eid: str) -> Dict[str, Any]:
        if eid not in self.status:
            self.status[eid] = {"state": "idle", "detail": "", "done": 0, "total": 0, "version": 0,
                                "built_at": None, "error": None, "copied": 0, "last_copy": None,
                                "watching": [], "missing_sources": []}
        return self.status[eid]

    def _set(self, eid: str, **kw) -> None:
        with self._lock:
            self._st(eid).update(kw)

    def _needs_build(self, eid: str) -> bool:
        summary = os.path.join(self.store.report_dir(eid), "event.json")
        if not os.path.exists(summary):
            return bool(self.store.event_logs(eid))
        try:
            with open(summary, encoding="utf-8") as f:
                if json.load(f).get("report_version") != REPORT_VERSION:
                    return True                    # made by an older Pitwall: redraw it
        except (OSError, ValueError):
            return True
        built = os.path.getmtime(summary)
        cfg = os.path.join(self.store.event_dir(eid), "config.json")
        newest = max([os.path.getmtime(p) for p in self.store.event_logs(eid)] + [os.path.getmtime(cfg)])
        return newest > built

    # ------------------------------------------------------------ importing logs

    def import_from_sources(self, eid: str, wait_settle: bool = True) -> int:
        """Copy new or grown logs from the event's source folders. Returns how many were copied."""
        with self._import_lock:
            return self._import(eid, wait_settle)

    def _import(self, eid: str, wait_settle: bool) -> int:
        ev = self.store.get_event(eid)
        if not ev:
            return 0
        dest = self.store.logs_dir(eid)
        os.makedirs(dest, exist_ok=True)
        have = {f: os.path.getsize(os.path.join(dest, f)) for f in os.listdir(dest) if f.lower().endswith(".wpilog")}
        copied = 0
        missing = []
        for src in ev["sources"]:
            if not os.path.isdir(src):
                missing.append(src)
                continue
            for root, dirs, files in os.walk(src):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if not f.lower().endswith(".wpilog"):
                        continue
                    path = os.path.join(root, f)
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    if st.st_size == 0 or have.get(f) == st.st_size:
                        continue
                    if have.get(f, 0) > st.st_size:
                        continue            # we already hold a longer copy of this log
                    if wait_settle and not self._settled(path, st):
                        continue
                    self._copy(path, os.path.join(dest, f))
                    have[f] = st.st_size
                    copied += 1
        if copied:
            with self._lock:
                s = self._st(eid)
                s["copied"] += copied
                s["last_copy"] = _now()
        self._set(eid, watching=[s for s in ev["sources"] if s not in missing], missing_sources=missing)
        return copied

    def _settled(self, path: str, st: os.stat_result) -> bool:
        prev = self._seen.get(path)
        now = _now()
        if prev is None or prev[0] != st.st_size or prev[1] != st.st_mtime:
            self._seen[path] = (st.st_size, st.st_mtime, now)
            return now - st.st_mtime > 60          # an old file on a stick is already complete
        return now - prev[2] >= SETTLE_S

    @staticmethod
    def _copy(src: str, dst: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), suffix=".partial")
        os.close(fd)
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            for ev in self.store.list_events():
                if not ev["watch"] or not ev["sources"]:
                    continue
                try:
                    if self.import_from_sources(ev["id"]):
                        self.request_build(ev["id"])
                except OSError as e:
                    self._set(ev["id"], detail=f"Couldn't read a watched folder: {e}")
            self._stop.wait(self.poll_s)

    # ------------------------------------------------------------ building reports

    def _build_loop(self) -> None:
        while not self._stop.is_set():
            with self._wake:
                while not self._queue and not self._stop.is_set():
                    self._wake.wait()
                if self._stop.is_set():
                    return
                eid = self._queue.pop(0)
            try:
                self.build(eid)
            except Exception as e:                       # keep the app alive; show the problem
                traceback.print_exc()
                self._set(eid, state="error", detail="", error=f"{type(e).__name__}: {e}")

    def build(self, eid: str) -> None:
        ev_cfg = self.store.get_event(eid)
        if not ev_cfg:
            return
        logs = self.store.event_logs(eid)
        if not logs:
            self._set(eid, state="waiting", detail="No logs yet", error=None)
            return
        profile_path = self.store.profile_path(ev_cfg["profile"])
        try:
            profile = load_profile(profile_path)
        except (ProfileError, OSError, ValueError) as e:
            self._set(eid, state="error", error=f"Robot profile problem: {e}")
            return
        self._set(eid, state="analyzing", detail="Reading logs", done=0, total=len(logs), error=None)

        def progress(_path: str, _r: Dict[str, Any]) -> None:
            with self._lock:
                self._st(eid)["done"] += 1

        results = analyze_logs(logs, profile_path, None, self.cache_dir, self.jobs, progress)
        pairs = list(zip(logs, results))
        errors = [{"file": os.path.basename(p), "reason": r["error"]} for p, r in pairs if "error" in r]
        groups, skipped, path_of, _ = group_results(pairs)
        groups = {c: g for c, g in groups.items() if g["matches"]}
        if not groups:
            self._set(eid, state="waiting", detail="No match logs with motor current yet", error=None)
            return
        code = max(groups, key=lambda c: len(groups[c]["matches"]))
        g = groups[code]
        sk = skipped.get(code, []) + [{"file": e["file"], "reason": "couldn't be read: " + e["reason"]} for e in errors]
        for other, og in groups.items():
            if other != code:
                sk += [{"file": p["source"]["file"], "reason": f"from another event ({other})"} for p in og["matches"]]
        self._set(eid, state="building", detail="Writing the report")
        ev = make_event(code, g, profile, name=ev_cfg["name"] or None, include=ev_cfg["include"],
                        exclude=ev_cfg["exclude"], skipped=sk, path_of=path_of)
        prev = self._baseline(eid, ev_cfg, ev)
        if prev:
            attach_comparison(ev, strip_for_compare(prev))
        ev["app_event_id"] = eid

        rdir = self.store.report_dir(eid)
        before = None
        try:
            with open(os.path.join(rdir, "event.json"), encoding="utf-8") as f:
                b = json.load(f)
            before = (b.get("name"), b.get("first_stamp"), b.get("averages"))
        except (OSError, ValueError):
            pass
        tmp, old = rdir + ".new", rdir + ".old"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        write_event(ev, g, tmp)
        if not self.store.get_event(eid):          # deleted while we were building
            shutil.rmtree(tmp, ignore_errors=True)
            return
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(rdir):
            os.replace(rdir, old)
        os.replace(tmp, rdir)
        shutil.rmtree(old, ignore_errors=True)
        with self._lock:
            s = self._st(eid)
            s.update(state="idle", detail="", error=None, built_at=_now())
            s["version"] += 1
        if not ev_cfg["name"]:
            self.store.save_event(eid, {"name": ev["name"]})
        after = json.loads(json.dumps((ev["name"], ev["first_stamp"], ev["averages"])))
        self._rebuild_dependents(eid, ev_cfg["profile"], ev,
                                 changed=list(after) != (list(before) if before else None))

    def _summaries(self) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        out = []
        for e in self.store.list_events():
            try:
                with open(os.path.join(self.store.report_dir(e["id"]), "event.json"), encoding="utf-8") as f:
                    out.append((e, json.load(f)))
            except (OSError, ValueError):
                continue
        return out

    def _baseline(self, eid: str, cfg: Dict[str, Any], ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The event to compare with: the one chosen, or the latest earlier event for this robot."""
        if cfg["compare"] == "none":
            return None
        cands = [(e, s) for e, s in self._summaries() if e["id"] != eid and s.get("averages", {}).get("n")]
        if cfg["compare"] not in ("auto", ""):
            return next((s for e, s in cands if e["id"] == cfg["compare"]), None)
        earlier = [(e, s) for e, s in cands if e["profile"] == cfg["profile"]
                   and s.get("first_stamp", "") < ev.get("first_stamp", "")]
        if not earlier:
            return None
        return max(earlier, key=lambda es: es[1].get("first_stamp", ""))[1]

    def _rebuild_dependents(self, eid: str, profile: str, ev: Dict[str, Any], changed: bool) -> None:
        """Later events that compare with this one need their comparison refreshed."""
        if not changed:
            return
        for e, s in self._summaries():
            if e["id"] == eid:
                continue
            if e["compare"] == eid or (e["compare"] in ("auto", "") and e["profile"] == profile
                                       and s.get("first_stamp", "") > ev.get("first_stamp", "")):
                self.request_build(e["id"])


def drives() -> List[Dict[str, str]]:
    """Removable drives and other likely places for logs, for the folder picker."""
    out: List[Dict[str, str]] = []
    home = os.path.expanduser("~")
    out.append({"label": "Home", "path": home})
    for name in ("Documents", "Desktop", "Downloads"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            out.append({"label": name, "path": p})
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if letter not in "AB" and os.path.exists(root):
                out.append({"label": f"Drive {letter}:", "path": root})
        frc = "C:\\Users\\Public\\Documents\\FRC"
        if os.path.isdir(frc):
            out.append({"label": "FRC (driver station)", "path": frc})
    else:
        user = os.path.basename(home)
        for base in ("/media/" + user, "/run/media/" + user, "/Volumes", "/media"):
            try:
                for d in sorted(os.listdir(base)):
                    p = os.path.join(base, d)
                    if os.path.isdir(p) and not d.startswith(".") and d != user and p != "/Volumes/Macintosh HD":
                        out.append({"label": d + " (drive)", "path": p})
            except OSError:
                continue
    seen: Set[str] = set()
    uniq = []
    for d in out:
        if d["path"] not in seen:
            seen.add(d["path"])
            uniq.append(d)
    return uniq


def list_folder(path: str, kind: str = "logs") -> Dict[str, Any]:
    """Subfolders of `path`, for the folder picker. kind="logs" counts .wpilog files; kind="code"
    counts .java files and notes a build.gradle (the top of a robot project)."""
    path = os.path.abspath(os.path.expanduser(path or "~"))
    entries = []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except OSError as e:
        return {"path": path, "parent": os.path.dirname(path), "folders": [], "logs": 0, "logs_below": 0,
                "error": str(e), "kind": kind}
    ext = ".java" if kind == "code" else ".wpilog"
    here = 0
    for n in names:
        if n.startswith("."):
            continue
        p = os.path.join(path, n)
        if os.path.isdir(p):
            entries.append({"name": n, "path": p})
        elif n.lower().endswith(ext):
            here += 1
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else None, "folders": entries, "logs": here,
            "logs_below": _count(path, ext, depth=12 if kind == "code" else 3), "error": None, "kind": kind,
            "gradle": os.path.isfile(os.path.join(path, "build.gradle"))}


def _count(path: str, ext: str, limit: int = 2000, depth: int = 3) -> int:
    n = 0
    base = path.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "bin", "node_modules")]
        n += sum(1 for f in files if f.lower().endswith(ext))
        if n >= limit or root.count(os.sep) - base >= depth:
            dirs[:] = []
    return n


def logs_in(path: str, limit: int = 400) -> List[Dict[str, Any]]:
    """.wpilog files under `path`, newest first, for picking a sample log."""
    out = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(".wpilog"):
                p = os.path.join(root, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append({"path": p, "name": f, "bytes": st.st_size, "mtime": st.st_mtime,
                            "match": bool(re.search(r"_[qe]\d+\.wpilog$", f, re.I))})
        if len(out) >= limit:
            break
    # A real match log is the best sample: it exercises every mechanism
    def stamp(x):          # the robot's own date and time in the file name, else the file's time
        m = re.search(r"(\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", x["name"])
        return m.group(1) if m else str(x["mtime"])
    out.sort(key=lambda x: stamp(x), reverse=True)
    out.sort(key=lambda x: not x["match"])            # stable: match logs first, newest first
    return out
