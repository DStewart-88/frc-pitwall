"""Command line for Phase 1. The graphical app comes later and calls the same functions."""

import argparse
import dataclasses
import json
import os
import sys
import time
from typing import List, Optional

from . import __version__, power
from .match import SkipLog, build_match
from .profile import ProfileError, check_profile, load_profile
from .report.html import render
from .wpilog import WPILog, WPILogError


def _identity_dict(m) -> dict:
    d = dataclasses.asdict(m.identity)
    d["label"] = m.identity.label
    d["is_fms"] = m.identity.is_fms
    return d


def run_report(log_path: str, profile_path: str, out_dir: str, current_mode: Optional[str] = None) -> dict:
    profile = load_profile(profile_path)
    if current_mode:
        profile.current_mode = current_mode
    log = WPILog(log_path)
    m = build_match(log, profile)
    result = power.analyze(m)
    ident = _identity_dict(m)
    stem = os.path.splitext(os.path.basename(log_path))[0]
    os.makedirs(out_dir, exist_ok=True)
    payload = {"pitwall": __version__, "profile": profile.name, "identity": ident,
               "flags": m.flags, "power": result}
    with open(os.path.join(out_dir, stem + ".power.json"), "w") as f:
        json.dump(payload, f, indent=1)
    with open(os.path.join(out_dir, stem + ".html"), "w", encoding="utf-8") as f:
        f.write(render(result, ident, profile.name))
    return payload


def _expand(paths: List[str]) -> List[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                out += [os.path.join(root, f) for f in sorted(files) if f.endswith(".wpilog")]
        else:
            out.append(p)
    return out


def cmd_report(a) -> int:
    rc = 0
    for path in _expand(a.logs):
        t = time.time()
        try:
            payload = run_report(path, a.profile, a.out, "abs" if a.abs else None)
        except SkipLog as e:
            print(f"skipped    {os.path.basename(path)}: {e}")
            continue
        except (WPILogError, ValueError, KeyError, ProfileError) as e:
            print(f"error   {path}: {e}", file=sys.stderr)
            rc = 1
            continue
        T = payload["power"]["totals"]
        print(f"{payload['identity']['label']:>10}  {T['ah']:.2f} Ah  {T['wh']:.1f} Wh  "
              f"mean {T['mean_a']:.0f} A  min {T['min_v']:.2f} V  ({time.time() - t:.1f} s)  "
              f"-> {os.path.join(a.out, os.path.splitext(os.path.basename(path))[0])}.html")
    return rc


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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pitwall", description="FRC robot logs in, team-readable reports out.")
    ap.add_argument("-V", "--version", action="version", version=f"pitwall {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="write a power report (HTML + JSON) for each log")
    r.add_argument("logs", nargs="+", help="log files, or folders to search for .wpilog files")
    r.add_argument("-p", "--profile", required=True)
    r.add_argument("-o", "--out", default="pitwall-reports")
    r.add_argument("--abs", action="store_true",
                   help="count braking current as draw (reproduces older hand analyses)")
    r.set_defaults(fn=cmd_report)

    c = sub.add_parser("check", help="check a profile against a log")
    c.add_argument("log")
    c.add_argument("-p", "--profile", required=True)
    c.set_defaults(fn=cmd_check)

    k = sub.add_parser("keys", help="list the keys in a log")
    k.add_argument("log")
    k.add_argument("filter", nargs="?")
    k.set_defaults(fn=cmd_keys)

    a = ap.parse_args(argv)
    return a.fn(a)
