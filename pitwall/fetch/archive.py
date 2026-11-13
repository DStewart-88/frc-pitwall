"""The team log archive: where every fetched log is kept, sorted, plus the index of what's
already been copied. The layout (Doug, Nov 12):

  <archive>/2026/01 events/2026-10-24_NHMAN_RiverRage/        match logs and practice at the event
                                                .../boot/     never enabled: kept, never in a report
                                                .../hoot/     Phoenix and REV logs
  <archive>/2026/02 practice/2026-10-24_after_NHMAN_RiverRage/2026-10-27/   (same boot/ and hoot/)
  <archive>/2026/02 practice/2026-01-01_build-season/...      practice before the season's first event
  <archive>/2026/02 practice/undated/                         no clock and no time in the name
  .../unreadable/                                             a .wpilog Pitwall couldn't open
  <archive>/.pitwall/index.json                               every file already copied (name, size, checksum)

Practice is grouped by the event before it, which is known the day the practice happens, so a
group's name never changes; groups and day folders start with a date, so they sort in time
order. An event folder already in 01 events is found by its code (<date>_<CODE>[_<Name>]); a
new one is named the same way. An event runs from its folder's date (or first match day) to
its last FMS match day, and practice on those days goes with the event.
"""

import datetime as dt
import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .identify import AKIT_STAMP, LogInfo, event_dates, stamp_from_name

INDEX_VERSION = 1
EVENTS, PRACTICE = "01 events", "02 practice"
SEASON_DIR = re.compile(r"^\d{4}$")
EVENT_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})_([A-Za-z0-9]+)(?:_(.+))?$")
DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_MATCH = re.compile(r"_([pqe])(\d+)\.wpilog$", re.I)
NAME_EVENT = re.compile(r"^akit_\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_([a-z0-9]+)(?:_|\.wpilog$)", re.I)


@dataclass
class Event:
    code: str
    start: str                # first day (its folder's date, or its first match day)
    end: str                  # last match day
    folder: Optional[str]


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
        return os.path.join(self.root, date[:4] if date else str(dt.date.today().year))

    def seasons(self) -> List[str]:
        try:
            return sorted(os.path.join(self.root, d) for d in os.listdir(self.root)
                          if SEASON_DIR.match(d) and os.path.isdir(os.path.join(self.root, d)))
        except OSError:
            return []

    def events(self, dates: Dict[str, List[str]], names: Optional[Dict[str, str]] = None,
               spans: Optional[Dict[str, Tuple[str, str]]] = None) -> Dict[str, Event]:
        """Every event the archive knows: its folders in 01 events, the days of its FMS match logs,
        and dates set on the Pitwall event (`spans`). An event runs from its folder's date (or
        first match day) to its last match day, widened to any dates set by hand."""
        names = names or {}
        out: Dict[str, Event] = {}
        for season in self.seasons():
            folder = os.path.join(season, EVENTS)
            try:
                subs = sorted(os.listdir(folder))
            except OSError:
                continue
            for d in subs:
                m = EVENT_DIR.match(d)
                if m and os.path.isdir(os.path.join(folder, d)):
                    code = m.group(2).upper()
                    if code not in out:
                        out[code] = Event(code, m.group(1), m.group(1), os.path.join(folder, d))
        for code, ds in dates.items():
            if not ds:
                continue
            ev = out.get(code)
            if ev is None:
                out[code] = Event(code, ds[0], ds[-1], None)
            else:
                ev.start, ev.end = min(ev.start, ds[0]), max(ev.end, ds[-1])
        for code, (a, b) in (spans or {}).items():
            a, b = a or b, b or a
            if not a:
                continue
            ev = out.get(code)
            if ev is None:
                out[code] = Event(code, a, b, None)
            else:
                ev.start, ev.end = min(ev.start, a), max(ev.end, b)
        for code, ev in out.items():
            if ev.folder is None:
                label = f"{ev.start}_{code}"
                name = names.get(code, "").strip()
                if name and name.upper() != code:
                    label += "_" + re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
                ev.folder = os.path.join(self.season_dir(ev.start), EVENTS, label)
        return out

    def practice_group(self, day: str, events: Dict[str, Event]) -> str:
        """The practice folder for a day: named for the event before it that season, which is
        known the day the practice happens, so the name never has to change."""
        before = [e for e in events.values() if e.start <= day and e.start[:4] == day[:4]]
        if not before:
            return f"{day[:4]}-01-01_build-season"
        e = max(before, key=lambda e: (e.start, e.code))
        label = os.path.basename(e.folder or "")
        m = EVENT_DIR.match(label)
        rest = f"{m.group(2)}_{m.group(3)}" if m and m.group(3) else (m.group(2) if m else e.code)
        return f"{e.start}_after_{rest}"

    def place(self, info: LogInfo, rel: str, dates: Dict[str, List[str]],
              names: Optional[Dict[str, str]] = None,
              events: Optional[Dict[str, Event]] = None) -> Dict[str, Optional[str]]:
        """Where a log goes: {"dir": folder for it, "event": event code it belongs to or None}."""
        evs = events if events is not None else self.events(dates, names)
        day = info.date
        code: Optional[str] = None
        if info.kind == "match" and info.event:
            code = info.event
        elif day:
            here = sorted(c for c, e in evs.items() if e.start <= day <= e.end)
            if info.event in here:
                code = info.event
            elif here:
                code = here[0]
        elif info.event in evs and info.kind in ("hoot", "revlog"):
            code = info.event
        if code and code not in evs:           # a match on a day no event covers yet
            evs = dict(evs)
            evs[code] = Event(code, day or "undated", day or "undated",
                              os.path.join(self.season_dir(day), EVENTS, f"{day or 'undated'}_{code}"))
        if code:
            base = evs[code].folder or ""
        elif day:
            base = os.path.join(self.season_dir(day), PRACTICE, self.practice_group(day, evs), day)
        else:
            base = os.path.join(self.season_dir(None), PRACTICE, "undated")
        sub = {"match": "", "practice": "", "boot": "boot", "hoot": "hoot", "revlog": "hoot"}.get(info.kind,
                                                                                                "unreadable")
        return {"dir": os.path.join(base, sub) if sub else base, "event": code}

    # ------------------------------------------------------------ one-time tidy-up

    def regroup_plan(self) -> List[Tuple[str, str]]:
        """Practice day folders sitting straight in 02 practice, and the group folder each
        belongs in (by the same rule a fetch uses): [(from, to)]."""
        evs = self.events(known_event_dates(self))
        plan = []
        for season in self.seasons():
            prac = os.path.join(season, PRACTICE)
            try:
                subs = sorted(os.listdir(prac))
            except OSError:
                continue
            for d in subs:
                if DAY_DIR.match(d) and os.path.isdir(os.path.join(prac, d)):
                    plan.append((os.path.join(prac, d), os.path.join(prac, self.practice_group(d, evs), d)))
        return plan

    def regroup(self, plan: List[Tuple[str, str]]) -> List[str]:
        """Carry out a regroup plan, keeping the index's paths right. Returns any problems."""
        problems = []
        for src, dst in plan:
            if os.path.exists(dst):
                problems.append(f"{dst} already exists; {src} left where it is")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            a = os.path.relpath(src, self.root).replace(os.sep, "/") + "/"
            b = os.path.relpath(dst, self.root).replace(os.sep, "/") + "/"
            for e in self.files.values():
                if e["path"].startswith(a):
                    e["path"] = b + e["path"][len(a):]
        self.save()
        return problems


def known_event_dates(archive: Archive, more: Iterable[LogInfo] = ()) -> Dict[str, List[str]]:
    return event_dates(list(archive.infos()) + list(more))


def unclaimed_codes(archive: Archive, claimed: Iterable[str], ignored: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Event codes with FMS match logs in the archive that no Pitwall event takes:
    [{"code", "matches", "first", "last", "folder"}], newest first."""
    skip = {c.upper() for c in claimed} | {c.upper() for c in ignored}
    found: Dict[str, Dict[str, Any]] = {}
    for e in archive.files.values():
        info = LogInfo.from_dict(e.get("info") or {"kind": "other"})
        if info.kind != "match" or not info.event or info.event in skip:
            continue
        d = found.setdefault(info.event, {"code": info.event, "matches": set(), "days": set()})
        d["matches"].add(info.match)
        if info.date:
            d["days"].add(info.date)
    evs = archive.events(known_event_dates(archive))
    out = []
    for code, d in found.items():
        days = sorted(d["days"])
        folder = evs[code].folder if code in evs else None
        out.append({"code": code, "matches": len(d["matches"]), "first": days[0] if days else "",
                    "last": days[-1] if days else "",
                    "folder": os.path.relpath(folder, archive.root).replace(os.sep, "/") if folder else ""})
    return sorted(out, key=lambda x: x["first"], reverse=True)


def event_logs_in_archive(archive: Archive, code: str, span: Optional[Tuple[str, str]] = None) -> List[str]:
    """Archive paths of the logs that belong to the Pitwall event for `code`: its match logs, and
    practice logs from its days."""
    dates = known_event_dates(archive)
    evs = archive.events(dates, spans={code: span} if span else None)
    ev = evs.get(code)
    out = []
    for e in archive.files.values():
        info = LogInfo.from_dict(e.get("info") or {"kind": "other"})
        path = os.path.join(archive.root, *e["path"].split("/"))
        if not e["name"].lower().endswith(".wpilog") or not os.path.isfile(path):
            continue
        if info.kind == "match" and info.event == code:
            out.append(path)
        elif info.kind == "match" and not info.event and ev and info.date and ev.start <= info.date <= ev.end:
            out.append(path)
        elif info.kind == "practice" and ev and info.date and ev.start <= info.date <= ev.end:
            out.append(path)
    return sorted(out)
