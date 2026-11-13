"""Command line. The graphical app comes later and calls the same functions."""

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

from . import __version__
from .analysis import analyze_logs, expand_paths
from .cache import default_dir
from .event import attach_comparison, strip_for_compare
from .eventrun import group_results, make_event, write_event, write_match_report
from .profile import ProfileError, check_profile, load_profile, resolve_profile
from .wpilog import WPILog


def _cache_dir(a) -> Optional[str]:
    return None if a.no_cache else (a.cache_dir or default_dir())


def _run(a, paths: List[str]) -> List[Dict[str, Any]]:
    """Analyze with progress lines; returns [(path, result)]."""
    t0 = time.time()
    done = [0]
    total = len(paths)

    def progress(path: str, r: Dict[str, Any]) -> None:
        done[0] += 1
        name = os.path.basename(path)
        tag = "cached " if r.get("cached") else ""
        if "ok" in r:
            T = r["ok"]["power"]["totals"]
            msg = (f'{r["ok"]["identity"]["label"]:>10}  {T["ah"]:.2f} Ah  mean {T["mean_a"]:.0f} A  '
                   f'min {T["min_v"]:.2f} V')
        elif "skip" in r:
            msg = f"   skipped  {r['skip']}"
        else:
            msg = f"     error  {r['error']}"
        print(f"[{done[0]:>{len(str(total))}}/{total}] {tag:7s}{msg}   {name}", flush=True)

    res = analyze_logs(paths, a.profile, "abs" if a.abs else None, _cache_dir(a),
                       max(1, a.jobs), progress)
    print(f"{total} log(s) in {time.time() - t0:.1f} s")
    return list(zip(paths, res))


def cmd_report(a) -> int:
    rc = 0
    for path, r in _run(a, expand_paths(a.logs)):
        if "ok" in r:
            write_match_report(r["ok"], a.out)
        elif "error" in r:
            rc = 1
    print(f"reports in {a.out}")
    return rc


def _load_compare(path: str) -> Dict[str, Any]:
    if os.path.isdir(path):
        path = os.path.join(path, "event.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_event(a) -> int:
    profile = load_profile(a.profile)
    if a.abs:
        profile.current_mode = "abs"
    names = {}
    for item in a.name or []:
        if "=" not in item:
            print(f"--name needs CODE=Name, got {item!r}", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        names[k.strip().upper()] = v.strip()

    results = _run(a, expand_paths(a.logs))
    groups, skipped, path_of, n_unplaced = group_results(results)
    if n_unplaced:
        print(f"{n_unplaced} practice log(s) have no event name and aren't in any event report.")
    events = []
    for code, g in groups.items():
        if not g["matches"]:
            continue
        ev = make_event(code, g, profile, name=names.get(code), include=a.include or [],
                        exclude=a.exclude or [], skipped=skipped.get(code, []), path_of=path_of)
        events.append((ev, g))
    if not events:
        print("no FMS matches with motor current found; no event report written")
        return 1
    events.sort(key=lambda e: e[0]["first_stamp"])

    prev = _load_compare(a.compare) if a.compare else None
    for ev, g in events:
        if not a.no_compare:
            attach_comparison(ev, prev)
        if ev["averages"].get("n"):          # an event with no counted matches can't be a baseline
            prev = strip_for_compare(ev)
        folder = os.path.join(a.out, re.sub(r"[^A-Za-z0-9_-]+", "_", ev["code"]))
        index = write_event(ev, g, folder)
        A, B = ev["averages"], ev["brownout"]
        cmp_txt = f"  vs {ev['compare']['name']}" if ev.get("compare") else ""
        SA = (ev.get("scoring") or {}).get("averages") or {}
        shots = f", {SA['hub']:.0f} hub shots per match" if SA.get("n") else ""
        print(f"{ev['name']} ({ev['code']}): {len(ev['matches'])} matches, {A.get('n', 0)} counted, "
              f"{A.get('ah', 0) or 0:.2f} Ah per match, brownouts in {B['with_brownout']} of {B['matches']}"
              f"{shots}{cmp_txt}\n  -> {index}")
    return 0


def cmd_app(a) -> int:
    from .app.server import serve
    from .app.store import default_data_dir
    if not sys.stdout or not sys.stdout.isatty():
        # Started from the app menu: no terminal, so keep the output in the data folder.
        root = a.data_dir or default_data_dir()
        os.makedirs(root, exist_ok=True)
        log = open(os.path.join(root, "pitwall.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} pitwall {__version__}")
    serve(a.data_dir, a.port, not a.no_browser, max(1, a.jobs))
    return 0


def cmd_fetch(a) -> int:
    from .app.store import Store
    from .fetch import Archive, LocalSource, fetch, summary_lines
    store = None if a.no_pitwall else Store(a.data_dir)
    settings = store.settings() if store else {}
    root = a.archive or settings.get("archive")
    if not root:
        print("Where is the team log archive? Give it once with --archive FOLDER "
              "(Pitwall remembers it).", file=sys.stderr)
        return 2
    root = os.path.abspath(os.path.expanduser(root))
    if store and a.archive:
        store.save_settings({"archive": root})
    archive = Archive(root)
    if a.index_existing:
        n = archive.seed()
        print(f"{n} log file(s) already in {root} added to the index ({len(archive.files)} in all).")
        if not a.source:
            return 0
    if not a.source:
        print("Fetch from where? Give the stick or folder, e.g. pitwall fetch /media/$USER/ROBOT", file=sys.stderr)
        return 2
    last = [""]

    def progress(stage: str, done: int, total: int, name: str) -> None:
        if stage == "copy" and name and sys.stdout.isatty():
            line = f"copying {done + 1}/{total}  {name}"
            print("\r" + line[:100].ljust(len(last[0])), end="", flush=True)
            last[0] = line[:100]
        elif stage == "copy" and not name and last[0]:
            print("\r" + " " * len(last[0]) + "\r", end="", flush=True)

    try:
        res = fetch(LocalSource(a.source), archive, store, progress)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    for line in summary_lines(res):
        print(line)
    print(f"archive: {root}")
    return 1 if res.errors else 0


def cmd_check(a) -> int:
    profile = load_profile(a.profile)
    log = WPILog(a.log)
    c = check_profile(profile, log)
    for mid, keys in c.resolved.items():
        print(f"{mid:20s} {keys['supply'] or '-- missing --'}")
    for label, items in (("missing", c.missing), ("unassigned", c.unassigned), ("silent", c.silent)):
        for i in items:
            print(f"{label:10s} {i}")
    print("profile OK for this log" if c.ok else "profile needs attention")
    return 0 if c.ok else 2


def cmd_keys(a) -> int:
    log = WPILog(a.log)
    for name, typ in log.entries(a.filter):
        print(f"{typ:16s} {name}  ({log.sample_count(name)} samples)")
    return 0


def _common(p) -> None:
    p.add_argument("logs", nargs="+", help="log files, or folders to search for .wpilog files")
    p.add_argument("-p", "--profile", required=True, help="robot profile: a .toml file, or a built-in name such as 2026-comp")
    p.add_argument("--abs", action="store_true",
                   help="count braking current as draw (reproduces older hand analyses)")
    p.add_argument("-j", "--jobs", type=int, default=min(4, os.cpu_count() or 1),
                   help="logs to analyze at once (default: up to 4)")
    p.add_argument("--cache-dir", help=f"where analyzed logs are kept (default {default_dir()})")
    p.add_argument("--no-cache", action="store_true", help="analyze every log again")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pitwall", description="FRC robot logs in, team-readable reports out.")
    ap.add_argument("-V", "--version", action="version", version=f"pitwall {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="write a power report (HTML + JSON) for each log")
    _common(r)
    r.add_argument("-o", "--out", default="pitwall-reports")
    r.set_defaults(fn=cmd_report)

    e = sub.add_parser("event", help="write an event report (and every match report) for each event")
    _common(e)
    e.add_argument("-o", "--out", default="pitwall-events")
    e.add_argument("--compare", help="an earlier event to compare with: its event.json or its output folder "
                                     "(events in the same run are compared with the one before automatically)")
    e.add_argument("--no-compare", action="store_true", help="don't compare with any earlier event")
    e.add_argument("--name", action="append", metavar="CODE=NAME",
                   help='event name to show, e.g. --name NHALT="Battle of the Bay"')
    e.add_argument("--exclude", action="append", metavar="MATCH", help="leave a match out of the averages (e.g. Q12)")
    e.add_argument("--include", action="append", metavar="MATCH",
                   help="count a match Pitwall would leave out (e.g. Q2)")
    e.set_defaults(fn=cmd_event)

    ap_ = sub.add_parser("app", help="start the Pitwall app in your browser")
    ap_.add_argument("--port", type=int, default=8088, help="port on this computer (default 8088, or the next free one)")
    ap_.add_argument("--data-dir", help="where the app keeps profiles and events (default: your user data folder)")
    ap_.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap_.add_argument("-j", "--jobs", type=int, default=min(4, os.cpu_count() or 1), help="logs to analyze at once")
    ap_.set_defaults(fn=cmd_app)

    f = sub.add_parser("fetch", help="copy new logs off the robot's USB stick into the archive and Pitwall events",
                       description="Copy new logs from the robot's USB stick (or any folder) into the team log "
                                   "archive, sorted by event and day, and add match logs to their Pitwall event. "
                                   "Nothing on the stick is changed.")
    f.add_argument("source", nargs="?", help="the stick (or its logs folder), or any folder of logs")
    f.add_argument("--archive", help="the team log archive folder (remembered after the first time)")
    f.add_argument("--index-existing", action="store_true",
                   help="note the logs already in the archive, so a fetch doesn't copy them again")
    f.add_argument("--data-dir", help="the Pitwall app's data folder (default: your user data folder)")
    f.add_argument("--no-pitwall", action="store_true", help="only fill the archive; don't add logs to Pitwall events")
    f.set_defaults(fn=cmd_fetch)

    c = sub.add_parser("check", help="check a profile against a log")
    c.add_argument("log")
    c.add_argument("-p", "--profile", required=True)
    c.set_defaults(fn=cmd_check)

    k = sub.add_parser("keys", help="list the keys in a log")
    k.add_argument("log")
    k.add_argument("filter", nargs="?")
    k.set_defaults(fn=cmd_keys)

    a = ap.parse_args(argv)
    try:
        if getattr(a, "profile", None):
            a.profile = resolve_profile(a.profile)
        return a.fn(a)
    except ProfileError as ex:
        print(f"profile problem: {ex}", file=sys.stderr)
        return 2
