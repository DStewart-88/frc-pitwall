"""The team log archive: where every fetched log is kept, sorted, plus the index of what's
already been copied.

  <archive>/<season> Logs/<YYYY-MM-DD>_<CODE>[_<Name>]/       match logs
                                 .../practice/                practice at the event
                                 .../boot/                    never enabled: kept, never in a report
                                 .../hoot/<session>/          Phoenix and REV logs from the event
  <archive>/<season> Logs/practice/<YYYY-MM-DD>/              practice away from events (same boot/ and hoot/)
  <archive>/<season> Logs/undated/                            no clock and no time in the name
  .../unreadable/                                             a .wpilog Pitwall couldn't open
  <archive>/.pitwall/index.json                               every file already copied (name, size, checksum)

An event folder that's already in the archive is reused when its name contains the event
code (2026-10-24_NHMAN_RiverRage, Newton Logs), so the fetch fits the archive as it is.
A log's "event dates" are the days its event's FMS match logs were recorded; a practice log
on one of those days goes with the event, any other practice log goes by its date.
"""

import datetime as dt
import json
import os
import re
import tempfile
from typing import Any, Dict, Iterable, List, Optional

from .identify import AKIT_STAMP, LogInfo, event_dates, stamp_from_name

INDEX_VERSION = 1
NAME_MATCH = re.compile(r"_([pqe])(\d+)\.wpilog$", re.I)
NAME_EVENT = re.compile(r"^akit_\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_([a-z0-9]+)(?:_|\.wpilog$)", re.I)


def file_key(name: str, size: int) -> str:
    return f"{name}|{size}"


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def info_from_name(name: str) -> LogInfo:
    """A rough identity from an AdvantageKit file name, for logs already in the archive that
    were never opened by a fetch. Good enough to know which days an event ran."""
    low = name.lower()
    if not low.endswith(".wpilog"):
        kind = "hoot" if low.endswith(".hoot") else "revlog" if low.endswith(".revlog") else "other"
        return LogInfo(kind=kind, when=stamp_from_name(name), when_from="file name")
    when = stamp_from_name(name) if AKIT_STAMP.search(name) else None
    m, e = NAME_MATCH.search(name), NAME_EVENT.match(name)
    event = e.group(1).upper() if e and not re.fullmatch(r"[pqe]\d+", e.group(1), re.I) else ""
    if m:
        return LogInfo(kind="match", event=event, match=f"{m.group(1).upper()}{int(m.group(2))}", when=when,
                       when_from="file name" if when else "")
    return LogInfo(kind="unknown", event=event, when=when, when_from="file name" if when else "")


class Archive:
    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.index_path = os.path.join(self.root, ".pitwall", "index.json")
        self.staging = os.path.join(self.root, ".pitwall", "staging")
        self.files: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------ index

    def _load(self) -> None:
        try:
            with open(self.index_path, encoding="utf-8") as f:
                data = json.load(f)
            self.files = data.get("files", {})
        except (OSError, ValueError):
            self.files = {}

    def save(self) -> None:
        _write_json(self.index_path, {"version": INDEX_VERSION, "files": self.files})

    def has(self, name: str, size: int) -> bool:
        return file_key(name, size) in self.files

    def record(self, name: str, size: int, rel_path: str, info: LogInfo, sha256: str = "",
               source: str = "", seeded: bool = False) -> None:
        self.files[file_key(name, size)] = {
            "name": name, "size": size, "path": rel_path.replace(os.sep, "/"), "sha256": sha256,
            "info": info.to_dict(), "source": source, "seeded": seeded,
            "at": dt.datetime.now().isoformat(timespec="seconds")}

    def infos(self) -> List[LogInfo]:
        return [LogInfo.from_dict(e["info"]) for e in self.files.values() if e.get("info")]

    def seed(self) -> int:
        """Index every log already in the archive (by name and size, from the file name), so a
        fetch from a stick that still holds them doesn't copy them again. Returns how many
        were added."""
        added = 0
        for top, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if not f.lower().endswith((".wpilog", ".hoot", ".revlog")):
                    continue
                p = os.path.join(top, f)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                if self.has(f, size):
                    continue
                self.record(f, size, os.path.relpath(p, self.root), info_from_name(f), seeded=True)
                added += 1
        if added:
            self.save()
        return added

    # ------------------------------------------------------------ where things go

    def season_dir(self, date: Optional[str]) -> str:
        year = date[:4] if date else str(dt.date.today().year)
        return os.path.join(self.root, f"{year} Logs")

    def event_dir(self, code: str, first_date: Optional[str], name: str = "") -> str:
        season = self.season_dir(first_date)
        pat = re.compile(rf"(^|[_ .-]){re.escape(code)}([_ .-]|$)", re.I)
        try:
            existing = sorted(d for d in os.listdir(season)
                              if os.path.isdir(os.path.join(season, d)) and pat.search(d))
        except OSError:
            existing = []
        if existing:
            return os.path.join(season, existing[0])
        label = f"{first_date or 'undated'}_{code}"
        if name and name.strip().upper() != code:
            label += "_" + re.sub(r"[^A-Za-z0-9]+", "-", name.strip()).strip("-")
        return os.path.join(season, label)

    def place(self, info: LogInfo, rel: str, dates: Dict[str, List[str]],
              names: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
        """Where a log goes: {"dir": folder for it, "event": event code it belongs to or None}."""
        names = names or {}
        day = info.date
        code: Optional[str] = None
        if info.kind == "match" and info.event:
            code = info.event
        elif day:
            here = [c for c, ds in dates.items() if day in ds]
            if info.event in here:
                code = info.event
            elif here:
                code = here[0]
        elif info.event in dates and info.kind in ("hoot", "revlog"):
            code = info.event
        if code:
            base = self.event_dir(code, (dates.get(code) or [day])[0], names.get(code, ""))
        elif day:
            base = os.path.join(self.season_dir(day), "practice", day)
        else:
            base = os.path.join(self.season_dir(None), "undated")
        if info.kind in ("hoot", "revlog"):
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            sub = os.path.join("hoot", *parent.split("/")) if parent else "hoot"
            return {"dir": os.path.join(base, sub), "event": code}
        if info.kind == "match":
            return {"dir": base, "event": code}
        if info.kind == "practice":
            return {"dir": os.path.join(base, "practice") if code else base, "event": code}
        if info.kind == "boot":
            return {"dir": os.path.join(base, "boot"), "event": code}
        return {"dir": os.path.join(base, "unreadable"), "event": code}


def known_event_dates(archive: Archive, more: Iterable[LogInfo] = ()) -> Dict[str, List[str]]:
    return event_dates(list(archive.infos()) + list(more))
