"""Turning analyzed logs into event reports on disk. Shared by the command line and the app."""

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .event import attach_comparison, build_event, group_by_event, strip_for_compare
from .report.event_html import render_event
from .report.html import render


# Bump when the report pages change, so the app rebuilds reports made by an older Pitwall.
REPORT_VERSION = 5


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


def name_from_folder(code: str, paths: List[str]) -> Optional[str]:
    """'2026-10-07_NHALT_Battle-Of-the-Bay' -> 'Battle Of the Bay' (text after the event code)."""
    for p in paths:
        for part in reversed(os.path.normpath(os.path.dirname(p)).split(os.sep)):
            m = re.search(re.escape(code) + r"[_\- ]+(.+)$", part, re.IGNORECASE)
            if m:
                return re.sub(r"[_\-]+", " ", m.group(1)).strip() or None
    return None


def group_results(results: List[Tuple[str, Dict[str, Any]]]):
    """Sort analyzed logs into events.

    Returns (groups, skipped, path_of, unplaced_other):
      groups   {event code: {"matches": [payload], "other": [payload]}}
      skipped  {event code: [{"file", "reason"}]}
      path_of  {log file name: path}
    Logs with no event name, and skipped logs, join the event whose logs share their folder.
    """
    payloads = [r["ok"] for _, r in results if "ok" in r]
    path_of = {r["ok"]["source"]["file"]: p for p, r in results if "ok" in r}
    groups = group_by_event(payloads)

    dir_event: Dict[str, Counter] = {}
    for p in payloads:
        code = (p["identity"].get("event") or "").strip().upper()
        if code:
            dir_event.setdefault(os.path.dirname(path_of[p["source"]["file"]]), Counter())[code] += 1

    def home(path: str) -> Optional[str]:
        c = dir_event.get(os.path.dirname(path))
        return c.most_common(1)[0][0] if c else None

    skipped: Dict[str, List[Dict[str, str]]] = {}
    for path, r in results:
        if "skip" in r and home(path):
            skipped.setdefault(home(path), []).append({"file": os.path.basename(path), "reason": r["skip"]})

    loose = groups.pop("", None) or {"matches": [], "other": []}
    unplaced = {"matches": [], "other": []}
    for kind in ("matches", "other"):
        for p in loose[kind]:
            code = home(path_of[p["source"]["file"]])
            if code:
                groups.setdefault(code, {"matches": [], "other": []})[kind].append(p)
            else:
                unplaced[kind].append(p)
    if unplaced["matches"]:
        groups["EVENT"] = {"matches": unplaced["matches"], "other": []}
    return groups, skipped, path_of, len(unplaced["other"])


def make_event(code: str, g: Dict[str, List[Dict[str, Any]]], profile, *, name: Optional[str] = None,
               include=frozenset(), exclude=frozenset(), skipped=None,
               path_of: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if not name and path_of:
        name = name_from_folder(code, [path_of[p["source"]["file"]] for p in g["matches"]])
    return build_event(code, g["matches"], g["other"], profile, name=name,
                       include=set(include), exclude=set(exclude), skipped=skipped or [])


def write_event(ev: Dict[str, Any], g: Dict[str, List[Dict[str, Any]]], folder: str) -> str:
    """Writes index.html, event.json and matches/ into `folder`; returns the index path."""
    mdir = os.path.join(folder, "matches")
    links, series = {}, {}
    chosen = {m["file"] for m in ev["matches"]}
    for p in g["matches"] + g["other"]:
        html_name = write_match_report(p, mdir)
        if p["source"]["file"] in chosen:
            links[p["identity"]["label"]] = "matches/" + html_name
            series[p["identity"]["label"]] = p["power"]["series"]
    ev["report_version"] = REPORT_VERSION
    with open(os.path.join(folder, "event.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, indent=1)
    index = os.path.join(folder, "index.html")
    with open(index, "w", encoding="utf-8") as f:
        f.write(render_event(ev, series, links))
    return index


__all__ = ["write_match_report", "name_from_folder", "group_results", "make_event", "write_event",
           "attach_comparison", "strip_for_compare"]
