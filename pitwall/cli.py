"""Command line. The graphical app comes later and calls the same functions."""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from . import __version__
from .analysis import analyze_logs, expand_paths
from .cache import default_dir
from .event import attach_comparison, build_event, group_by_event, strip_for_compare
from .profile import ProfileError, check_profile, load_profile
from .report.event_html import render_event
from .report.html import render
from .wpilog import WPILog


def _stem(file_name: str) -> str:
    return os.path.splitext(file_name)[0]


def write_match_report(payload: Dict[str, Any], out_dir: str) -> str:
    """Writes <stem>.html and <stem>.power.json; returns the HTML file name."""
    os.makedirs(out_dir, exist_ok=True)
    stem = _stem(payload["source"]["file"])
    saved = {k: v for k, v in payload.items() if k != "source"}
    saved["source"] = {k: v for k, v in payload["source"].items() if k != "path"}
    with open(os.path.join(out_dir, stem + ".power.json"), "w", encoding="utf-8") as f:
        json.dump(saved, f, indent=1)
    with open(os.path.join(out_dir, stem + ".html"), "w", encoding="utf-8") as f:
        f.write(render(payload["power"], payload["identity"], payload["profile"]))
    return stem + ".html"


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


def _name_from_folder(code: str, paths: List[str]) -> Optional[str]:
    """'2026-10-07_NHALT_Battle-Of-the-Bay' -> 'Battle Of the Bay' (text after the event code)."""
    for p in paths:
        for part in reversed(os.path.normpath(os.path.dirname(p)).split(os.sep)):
            m = re.search(re.escape(code) + r"[_\- ]+(.+)$", part, re.IGNORECASE)
            if m:
                return re.sub(r"[_\-]+", " ", m.group(1)).strip() or None
    return None


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
    payloads = [r["ok"] for _, r in results if "ok" in r]
    path_of = {r["ok"]["source"]["file"]: p for p, r in results if "ok" in r}
    groups = group_by_event(payloads)

    # Skipped logs go with the event whose matches share their folder.
    dir_event: Dict[str, Counter] = {}
    for p in payloads:
        code = (p["identity"].get("event") or "").strip().upper()
        if code:
            dir_event.setdefault(os.path.dirname(path_of[p["source"]["file"]]), Counter())[code] += 1
    skipped: Dict[str, List[Dict[str, str]]] = {}
    for path, r in results:
        if "skip" in r:
            c = dir_event.get(os.path.dirname(path))
            if c:
                skipped.setdefault(c.most_common(1)[0][0], []).append(
                    {"file": os.path.basename(path), "reason": r["skip"]})

    # Logs with no event name (the DS never got one, e.g. a robot reboot mid-match) join the
    # event whose logs share their folder.
    loose = groups.pop("", None) or {"matches": [], "other": []}
    unplaced = {"matches": [], "other": []}
    for kind in ("matches", "other"):
        for p in loose[kind]:
            c = dir_event.get(os.path.dirname(path_of[p["source"]["file"]]))
            if c:
                groups.setdefault(c.most_common(1)[0][0], {"matches": [], "other": []})[kind].append(p)
            else:
                unplaced[kind].append(p)
    if unplaced["other"]:
        print(f"{len(unplaced['other'])} practice log(s) have no event name and aren't in any event report.")
    if unplaced["matches"]:
        groups["EVENT"] = {"matches": unplaced["matches"], "other": []}

    events = []
    for code, g in groups.items():
        if not g["matches"]:
            continue
        files = [path_of[p["source"]["file"]] for p in g["matches"]]
        name = names.get(code) or _name_from_folder(code, files)
        ev = build_event(code, g["matches"], g["other"], profile, name=name,
                         include=set(a.include or []), exclude=set(a.exclude or []),
                         skipped=skipped.get(code, []))
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
        mdir = os.path.join(folder, "matches")
        links, series = {}, {}
        chosen = {m["file"] for m in ev["matches"]}
        for p in g["matches"] + g["other"]:
            html_name = write_match_report(p, mdir)
            if p["source"]["file"] in chosen:
                links[p["identity"]["label"]] = "matches/" + html_name
                series[p["identity"]["label"]] = p["power"]["series"]
        with open(os.path.join(folder, "event.json"), "w", encoding="utf-8") as f:
            json.dump(ev, f, indent=1)
        with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
            f.write(render_event(ev, series, links))
        A, B = ev["averages"], ev["brownout"]
        cmp_txt = f"  vs {ev['compare']['name']}" if ev.get("compare") else ""
        print(f"{ev['name']} ({ev['code']}): {len(ev['matches'])} matches, {A.get('n', 0)} counted, "
              f"{A.get('ah', 0) or 0:.2f} Ah per match, brownouts in {B['with_brownout']} of {B['matches']}"
              f"{cmp_txt}\n  -> {os.path.join(folder, 'index.html')}")
    return 0


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
    p.add_argument("-p", "--profile", required=True, help="robot profile (.toml)")
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
        return a.fn(a)
    except ProfileError as ex:
        print(f"profile problem: {ex}", file=sys.stderr)
        return 2
