"""Turning analyzed logs into event reports on disk. Shared by the command line and the app."""

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .analysis import payloads_of
from .event import attach_comparison, build_event, group_by_event, strip_for_compare
from .report.event_html import render_event
from .report.html import render


# Bump when the report pages change, so the app rebuilds reports made by an older Pitwall.
REPORT_VERSION = 10


def _stem(file_name: str) -> str:
    return os.path.splitext(file_name)[0]


def write_match_report(payload: Dict[str, Any], out_dir: str, nav: Optional[Dict[str, Any]] = None) -> str:
    """Writes <stem>.html and <stem>.power.json; returns the HTML file name. `nav` links the page to
    the previous and next match and back to the event (see report.html.render)."""
    os.makedirs(out_dir, exist_ok=True)
    stem = _stem(payload["source"]["file"])
    saved = {k: v for k, v in payload.items() if k != "source"}
    saved["source"] = {k: v for k, v in payload["source"].items() if k != "path"}
    with open(os.path.join(out_dir, stem + ".power.json"), "w", encoding="utf-8") as f:
        json.dump(saved, f, indent=1)
    with open(os.path.join(out_dir, stem + ".html"), "w", encoding="utf-8") as f:
        f.write(render(payload["power"], payload["identity"], payload["profile"], payload.get("scoring"), nav,
                       payload.get("health")))
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
    pairs = payloads_of(results)
    payloads = [p for _, p in pairs]
    path_of = {p["source"]["file"]: path for path, p in pairs}
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


def practice_results(results: List[Tuple[str, Dict[str, Any]]]):
    """The practice-mode matches in analyzed logs, for a practice report.

    Returns (group, logs_without): group = {"matches": [payload], "other": []}, the matches in time
    order and labelled by day ("Oct 27 #1", "Oct 27 #2", "Oct 29 #1", ...); logs_without = how many
    logs had no practice match (drilling, pit work).
    """
    import datetime as dt
    parts = [p for _, r in results for p in (r.get("parts") or [])]
    parts.sort(key=lambda p: (p["identity"].get("stamp") or "", p["source"]["file"]))
    per_day: Counter = Counter()
    for k, p in enumerate(parts, 1):
        I = p["identity"]
        day = I.get("day") or ""
        per_day[day] += 1
        try:
            nice = dt.date.fromisoformat(day).strftime("%b %-d") if day else "Undated"
        except ValueError:
            nice = day
        I.update(label=f"{nice} #{per_day[day]}", match_type="P", match_number=k)
    without = sum(1 for _, r in results if not r.get("parts"))
    return {"matches": parts, "other": []}, without


def practice_name(group: str) -> str:
    """'2026-10-24_after_NHMAN_RiverRage' -> 'Practice after RiverRage'."""
    m = re.match(r"^(\d{4})-\d{2}-\d{2}_(?:after_([A-Za-z0-9]+)(?:_(.+))?|build-season)$", group)
    if not m:
        return f"Practice {group}"
    if m.group(2) is None:
        return f"Build season practice {m.group(1)}"
    return "Practice after " + (m.group(3).replace("-", " ") if m.group(3) else m.group(2))


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
    links, series, shots = {}, {}, {}
    chosen = {m["file"] for m in ev["matches"]}
    order = [(m["label"], _stem(m["file"]) + ".html") for m in ev["matches"]]   # event order: Q, then E
    navs: Dict[str, Dict[str, Any]] = {}
    for k, (label, html_name) in enumerate(order):
        navs[html_name] = {"prev": order[k - 1] if k > 0 else None,
                           "next": order[k + 1] if k + 1 < len(order) else None, "event": "../index.html"}
    for p in g["matches"] + g["other"]:
        stem_html = _stem(p["source"]["file"]) + ".html"
        nav = navs.get(stem_html) if p["source"]["file"] in chosen else {"event": "../index.html"}
        html_name = write_match_report(p, mdir, nav)
        if p["source"]["file"] in chosen:
            links[p["identity"]["label"]] = "matches/" + html_name
            series[p["identity"]["label"]] = p["power"]["series"]
            if (p.get("scoring") or {}).get("available"):
                shots[p["identity"]["label"]] = p["scoring"]["shots"]
    ev["report_version"] = REPORT_VERSION
    with open(os.path.join(folder, "event.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, indent=1)
    index = os.path.join(folder, "index.html")
    with open(index, "w", encoding="utf-8") as f:
        f.write(render_event(ev, series, links, shots))
    return index


__all__ = ["write_match_report", "name_from_folder", "group_results", "practice_results", "practice_name",
           "make_event", "write_event",
           "attach_comparison", "strip_for_compare"]
