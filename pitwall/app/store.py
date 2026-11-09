"""Where the app keeps its things: robot profiles and events, in a per-install data folder.

  Linux    ~/.local/share/pitwall        (or $XDG_DATA_HOME/pitwall)
  macOS    ~/Library/Application Support/Pitwall
  Windows  %APPDATA%\\Pitwall

  profiles/<id>.toml           robot profiles made or imported on this computer
  events/<id>/config.json      name, profile, watched folders, which matches count, comparison
  events/<id>/logs/            copies of the event's logs (safe when the USB stick is unplugged)
  events/<id>/report/          the event report and match reports (index.html, matches/)

Nothing here needs the robot's code repository.
"""

import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from ..profile import ProfileError, builtin_profiles, load_profile


def default_data_dir() -> str:
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming"), "Pitwall")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Pitwall")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "pitwall")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "item"


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


EVENT_DEFAULTS = {
    "name": "", "profile": "", "sources": [], "watch": True,
    "include": [], "exclude": [], "compare": "auto", "created": 0.0,
}


class Store:
    def __init__(self, root: Optional[str] = None):
        self.root = root or default_data_dir()
        self.profiles_dir = os.path.join(self.root, "profiles")
        self.events_dir = os.path.join(self.root, "events")
        os.makedirs(self.profiles_dir, exist_ok=True)
        os.makedirs(self.events_dir, exist_ok=True)
        self.lock = threading.RLock()

    # ------------------------------------------------------------ profiles

    def profile_path(self, pid: str) -> str:
        return os.path.join(self.profiles_dir, slug(pid) + ".toml")

    def list_profiles(self) -> List[Dict[str, Any]]:
        out = []
        for f in sorted(os.listdir(self.profiles_dir), key=lambda n: n[:-5]):
            if not f.endswith(".toml"):
                continue
            pid = f[:-5]
            path = os.path.join(self.profiles_dir, f)
            try:
                p = load_profile(path)
                out.append({"id": pid, "name": p.name, "motors": len(p.motors),
                            "subsystems": len(p.subsystems), "path": path, "error": None})
            except (ProfileError, ValueError, KeyError, OSError) as e:
                out.append({"id": pid, "name": pid, "motors": 0, "subsystems": 0, "path": path, "error": str(e)})
        return out

    def save_profile(self, text: str, name_hint: str = "") -> str:
        """Check a profile's TOML and save it; returns its id. Raises ProfileError if it's invalid."""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
            f.write(text)
            tmp = f.name
        try:
            p = load_profile(tmp)
        except ProfileError:
            raise
        except Exception as e:                 # TOML syntax errors and missing fields
            raise ProfileError(f"this isn't a valid Pitwall profile: {e}") from e
        finally:
            os.unlink(tmp)
        with self.lock:
            base = slug(p.name if p.name and not p.name.endswith(".toml") else name_hint or "robot")
            pid, k = base, 2
            while os.path.exists(self.profile_path(pid)):
                pid, k = f"{base}-{k}", k + 1
            with open(self.profile_path(pid), "w", encoding="utf-8") as f:
                f.write(text)
        return pid

    def add_builtin_profile(self, name: str = "2026-comp") -> str:
        for path in builtin_profiles():
            if os.path.basename(path)[:-5] == name:
                with open(path, encoding="utf-8") as f:
                    return self.save_profile(f.read(), name)
        raise ProfileError(f"no built-in profile named {name!r}")

    def delete_profile(self, pid: str) -> None:
        in_use = [e["name"] for e in self.list_events() if e["profile"] == pid]
        if in_use:
            raise ProfileError(f"in use by {', '.join(in_use)}")
        os.remove(self.profile_path(pid))

    # ------------------------------------------------------------ events

    def event_dir(self, eid: str) -> str:
        return os.path.join(self.events_dir, slug(eid))

    def logs_dir(self, eid: str) -> str:
        return os.path.join(self.event_dir(eid), "logs")

    def report_dir(self, eid: str) -> str:
        return os.path.join(self.event_dir(eid), "report")

    def get_event(self, eid: str) -> Optional[Dict[str, Any]]:
        cfg = _read_json(os.path.join(self.event_dir(eid), "config.json"), None)
        if cfg is None:
            return None
        return {**EVENT_DEFAULTS, **cfg, "id": slug(eid)}

    def list_events(self) -> List[Dict[str, Any]]:
        out = []
        for d in os.listdir(self.events_dir):
            ev = self.get_event(d)
            if ev:
                summary = _read_json(os.path.join(self.report_dir(d), "event.json"), None)
                ev["summary"] = _event_card(summary) if summary else None
                out.append(ev)
        out.sort(key=lambda e: ((e["summary"] or {}).get("first_stamp") or "", e["created"]), reverse=True)
        return out

    def create_event(self, name: str, profile: str, sources: List[str], watch: bool = True) -> str:
        if not os.path.exists(self.profile_path(profile)):
            raise ValueError(f"unknown profile {profile!r}")
        with self.lock:
            base = slug(name or "event")
            eid, k = base, 2
            while os.path.exists(self.event_dir(eid)):
                eid, k = f"{base}-{k}", k + 1
            os.makedirs(self.logs_dir(eid))
            os.makedirs(self.report_dir(eid))
            self.save_event(eid, {"name": name.strip(), "profile": profile, "sources": sources,
                                  "watch": watch, "created": time.time()})
        return eid

    def save_event(self, eid: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            cur = self.get_event(eid) or dict(EVENT_DEFAULTS)
            cur.update({k: v for k, v in changes.items() if k in EVENT_DEFAULTS})
            cur.pop("id", None)
            cur.pop("summary", None)
            _write_json(os.path.join(self.event_dir(eid), "config.json"), cur)
            return {**cur, "id": slug(eid)}

    def delete_event(self, eid: str) -> None:
        shutil.rmtree(self.event_dir(eid), ignore_errors=True)

    def event_logs(self, eid: str) -> List[str]:
        d = self.logs_dir(eid)
        try:
            return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".wpilog"))
        except OSError:
            return []


def _event_card(ev: Dict[str, Any]) -> Dict[str, Any]:
    """The few numbers the home page shows for an event."""
    A, B = ev.get("averages", {}), ev.get("brownout", {})
    return {"code": ev.get("code"), "first_stamp": ev.get("first_stamp"), "matches": len(ev.get("matches", [])),
            "counted": A.get("n", 0), "ah": A.get("ah"), "mean_a": A.get("mean_a"),
            "with_brownout": B.get("with_brownout"), "brownout_matches": B.get("matches"),
            "median_first_left_s": B.get("median_first_left_s")}


def new_token() -> str:
    return secrets.token_urlsafe(24)
