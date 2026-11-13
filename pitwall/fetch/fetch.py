"""One fetch: copy the new logs from a source, sort them into the archive, and hand match
logs to their Pitwall event.

  1. List the source once.
  2. Skip what the archive's index already has (same name and size).
  3. Leave alone any file still growing: the log the robot is writing right now.
  4. Copy each new file into the archive's staging folder, check its size, note its checksum.
  5. Identify it from its contents.
  6. File it in the archive (see archive.py) and record it in the index.
  7. Match logs, and practice logs from an event's days, also go into the Pitwall event
     with that event code, whose report then rebuilds.

Nothing on the source is changed (sources can only list and read). Each step can stop
partway without losing anything: a file is only indexed once it's in its archive folder,
and a file that was filed but not indexed is recognised by its checksum next time.
"""

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .archive import Archive, known_event_dates
from .identify import LogInfo, identify
from .source import RemoteFile, Source, still_growing

CHUNK = 1 << 20
SAVE_EVERY = 20


@dataclass
class Fetched:
    name: str
    path: str                     # where it went, relative to the archive
    info: LogInfo
    pitwall_event: Optional[str] = None
    duplicate: bool = False       # identical file was already in that archive folder


@dataclass
class FetchResult:
    source: str
    listed: int = 0
    already: int = 0
    growing: List[str] = field(default_factory=list)
    fetched: List[Fetched] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)
    no_pitwall_event: Dict[str, int] = field(default_factory=dict)   # event code -> match logs
    handed: Dict[str, List[str]] = field(default_factory=dict)       # Pitwall event id -> labels

    def count(self, kind: str) -> int:
        return sum(1 for f in self.fetched if f.info.kind == kind)


Progress = Callable[[str, int, int, str], None]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def _copy_in(source: Source, f: RemoteFile, dest: str) -> str:
    """Copy one file from the source to `dest`; returns its sha256. Raises if the size is off."""
    h = hashlib.sha256()
    n = 0
    with source.open(f.rel) as src, open(dest, "wb") as out:
        for b in iter(lambda: src.read(CHUNK), b""):
            out.write(b)
            h.update(b)
            n += len(b)
    if n != f.size:
        raise IOError(f"copied {n} bytes but the file is {f.size} bytes (did it change while copying?)")
    return h.hexdigest()


def _free_name(folder: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    k = 2
    while os.path.exists(os.path.join(folder, name)):
        name = f"{stem} ({k}){ext}"
        k += 1
    return name


def _copy_atomic(src: str, dst: str) -> None:
    tmp = dst + ".partial"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def fetch(source: Source, archive: Archive, store: Any = None, progress: Optional[Progress] = None,
          settle_s: float = 3.0) -> FetchResult:
    """Fetch new logs from `source` into `archive`; with a Pitwall `store`, hand match logs to
    their events. Returns what happened."""
    say = progress or (lambda stage, done, total, name: None)
    res = FetchResult(source=source.label)
    files = [f for f in source.list() if f.size > 0]
    res.listed = len(files)
    new = [f for f in files if not archive.has(f.name, f.size)]
    res.already = len(files) - len(new)
    growing = still_growing(source, new, settle_s) if new else set()
    res.growing = sorted(growing)
    new = [f for f in new if f.rel not in growing]

    # Copy and identify everything first, so practice logs can be sorted by the event dates
    # of match logs from the same fetch.
    os.makedirs(archive.staging, exist_ok=True)
    staged: List[Tuple[RemoteFile, str, str, LogInfo]] = []
    for i, f in enumerate(new):
        say("copy", i, len(new), f.rel)
        tmp = os.path.join(archive.staging, f"{i:05d}_{f.name}.partial")
        try:
            sha = _copy_in(source, f, tmp)
        except (OSError, ValueError) as e:
            res.errors.append((f.rel, str(e)))
            if os.path.exists(tmp):
                os.remove(tmp)
            continue
        staged.append((f, tmp, sha, identify(tmp, f.rel)))
    say("copy", len(new), len(new), "")

    dates = known_event_dates(archive, [s[3] for s in staged])
    names, by_code = _pitwall_events(store)
    events = archive.events(dates, names)
    try:
        for i, (f, tmp, sha, info) in enumerate(staged):
            say("file", i, len(staged), f.rel)
            where = archive.place(info, f.rel, dates, names, events)
            folder = where["dir"]
            os.makedirs(folder, exist_ok=True)
            dup = False
            target = os.path.join(folder, f.name)
            if os.path.exists(target):
                if os.path.getsize(target) == f.size and _sha256(target) == sha:
                    dup = True
                    os.remove(tmp)
                else:
                    target = os.path.join(folder, _free_name(folder, f.name))
            if not dup:
                os.replace(tmp, target)
            rel = os.path.relpath(target, archive.root)
            archive.record(f.name, f.size, rel, info, sha256=sha, source=source.label)
            item = Fetched(f.name, rel.replace(os.sep, "/"), info, duplicate=dup)
            code = where["event"]
            if info.kind in ("match", "practice") and code:
                eid = by_code.get(code)
                if eid:
                    dst_dir = store.logs_dir(eid)
                    os.makedirs(dst_dir, exist_ok=True)
                    dst = os.path.join(dst_dir, os.path.basename(target))
                    if not (os.path.exists(dst) and os.path.getsize(dst) == f.size):
                        _copy_atomic(target, dst)
                    item.pitwall_event = eid
                    res.handed.setdefault(eid, []).append(info.match or "practice")
                elif info.kind == "match":
                    res.no_pitwall_event[code] = res.no_pitwall_event.get(code, 0) + 1
            res.fetched.append(item)
            if (i + 1) % SAVE_EVERY == 0:
                archive.save()
        say("file", len(staged), len(staged), "")
    finally:
        archive.save()
        for _, tmp, _, _ in staged:
            if os.path.exists(tmp):
                os.remove(tmp)
    return res


def _pitwall_events(store: Any) -> Tuple[Dict[str, str], Dict[str, str]]:
    """({event code: event name}, {event code: Pitwall event id}) from the app's events."""
    if store is None:
        return {}, {}
    names, ids = {}, {}
    for ev in store.list_events():
        code = store.event_code(ev["id"])
        if code and code not in ids:
            ids[code] = ev["id"]
            if ev.get("name"):
                names[code] = ev["name"]
    return names, ids


def summary_lines(res: FetchResult) -> List[str]:
    """What happened, in a few lines for a person."""
    out = [f"{res.listed} log file(s) on {res.source}: {res.already} already in the archive, "
           f"{len(res.fetched)} copied."]
    if res.fetched:
        parts = [f"{res.count(k)} {label}" for k, label in
                 (("match", "match"), ("practice", "practice"), ("boot", "boot-only"),
                  ("hoot", "Phoenix"), ("revlog", "REV"), ("other", "unreadable")) if res.count(k)]
        out.append("  " + ", ".join(parts))
    for eid, labels in sorted(res.handed.items()):
        matches = [x for x in labels if x != "practice"]
        prac = len(labels) - len(matches)
        bits = (", ".join(matches) if matches else "") + (f"{' and ' if matches else ''}{prac} practice log(s)"
                                                          if prac else "")
        out.append(f"  added to Pitwall event {eid}: {bits}")
    for code, n in sorted(res.no_pitwall_event.items()):
        out.append(f"  {n} {code} match log(s) are in the archive but no Pitwall event has code {code}: "
                   f"make an event for them in the app")
    if res.growing:
        out.append(f"  left for next time (still being written): {', '.join(res.growing)}")
    for rel, err in res.errors:
        out.append(f"  couldn't copy {rel}: {err}")
    return out
