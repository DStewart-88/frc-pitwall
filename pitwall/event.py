"""Event roll-up: every match from one event, summed up and compared with the event before.

Input is the list of match payloads from `analysis.analyze_logs`. Output is one event
summary per event (plain JSON-ready dicts, no time series), which the event report draws and
which is saved as event.json so a later event can compare against it.

Which matches count toward the event averages
---------------------------------------------
A match counts unless something says it wasn't a normal match:
  - it's a practice match (P), or
  - the robot was enabled for less than 90% of the match, or
  - one or more motors looked absent from the CAN bus, or
  - it drew under half the median charge of the event's other matches (a dead or stuck
    robot that stayed enabled), or
  - the user excluded it by name.
Every match is still shown in the report, with the reason it wasn't counted. The user can
force a match in by name too. When one match has more than one log (a replay, or a robot
reboot mid-match), the log with the most enabled time is used and the others are listed.
"""

import statistics
from typing import Any, Dict, Iterable, List, Optional, Set

from . import __version__
from .cache import ANALYSIS_VERSION

SCHEMA = 1
TYPE_ORDER = {"P": 0, "Q": 1, "E": 2}
LOW_CHARGE_SHARE = 0.5         # below this share of the event median, a match looks dead
CHANGE_SHARE = 0.15            # a subsystem change this large vs the previous event is noted
CHANGE_MIN_AH = 0.10


# ---------------------------------------------------------------- per-match summary

def match_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    P = payload["power"]
    T, B, I = P["totals"], P["brownout"], payload["identity"]

    def span(d):
        return {"s": d["s"], "ah": d["ah"], "mean_a": d["mean_a"], "mean_w": d["mean_w"]}

    return {
        "label": I["label"], "type": I["match_type"], "number": I["match_number"],
        "event": (I.get("event") or "").strip(), "is_fms": I["is_fms"],
        "file": payload["source"]["file"], "stamp": I.get("stamp", ""),
        "build": (I.get("git_sha") or "")[:7], "dirty": bool(I.get("git_dirty")),
        "flags": payload.get("flags", []),
        "silent": payload.get("silent", {}),
        "enabled_s": T["enabled_s"], "ah": T["ah"], "wh": T["wh"],
        "mean_a": T["mean_a"], "mean_w": T["mean_w"], "p95_a": T["p95_a"],
        "peak_1s_a": T["peak_1s_a"], "peak_1s_w": T["peak_1s_w"], "min_v": T["min_v"],
        "ah_returned": T["ah_returned"],
        "auto": span(T["auto"]), "teleop": span(T["teleop"]),
        "quarters": [span(q) for q in T["quarters"]],
        "state_s": T["state_s"],
        "brownout": {"episodes": B["episodes"], "onsets": B["onsets"],
                     "first_time_left_s": B["first_time_left_s"],
                     "episode_time_left": B["episode_time_left"],
                     "seconds_below_threshold": B["seconds_below_threshold"]},
        "subsystems": {s["id"]: {"ah": s["ah"], "wh": s["wh"], "mean_a": s["mean_a"],
                                 "idle_ah": s["idle_ah"]} for s in P["subsystems"]},
        "motors": {mo["id"]: {"name": mo["name"], "ah": mo["ah"], "active_s": mo["active_s"],
                              "saturated_share": mo["saturated_share"],
                              "peaks_reliable": mo["peaks_reliable"]} for mo in P["motors"]},
        "observations": P["observations"],
    }


def _order(m: Dict[str, Any]):
    return (TYPE_ORDER.get(m["type"], 9), m["number"], m["stamp"])


# ---------------------------------------------------------------- grouping

def group_by_event(payloads: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """{event code: {"matches": [payload...], "other": [payload...]}}; non-FMS logs with no
    event name go under the code ""."""
    out: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for p in payloads:
        code = (p["identity"].get("event") or "").strip().upper()
        g = out.setdefault(code, {"matches": [], "other": []})
        (g["matches"] if p["identity"]["is_fms"] else g["other"]).append(p)
    return out


# ---------------------------------------------------------------- one event

def build_event(code: str, payloads: List[Dict[str, Any]], others: List[Dict[str, Any]],
                profile, name: Optional[str] = None, include: Set[str] = frozenset(),
                exclude: Set[str] = frozenset(), skipped: Optional[List[Dict[str, str]]] = None
                ) -> Dict[str, Any]:
    nominal = profile.auto_s + profile.teleop_s
    include = {x.upper() for x in include}
    exclude = {x.upper() for x in exclude}

    # One log per match: the one with the most enabled time.
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for p in payloads:
        by_label.setdefault(p["identity"]["label"], []).append(match_summary(p))
    matches: List[Dict[str, Any]] = []
    for label, ms in by_label.items():
        ms.sort(key=lambda m: -m["enabled_s"])
        best = ms[0]
        best["other_logs"] = [{"file": m["file"], "enabled_s": m["enabled_s"]} for m in ms[1:]]
        matches.append(best)
    matches.sort(key=_order)

    # Which matches count
    for m in matches:
        why = None
        if m["type"] == "P":
            why = "Practice match"
        elif m["enabled_s"] < 0.9 * nominal:
            why = f"Enabled for only {m['enabled_s']:.0f} s"
        elif m["silent"]:
            why = f"{len(m['silent'])} motor(s) absent from the CAN bus"
        m["why_not"] = why
    normal = [m for m in matches if m["why_not"] is None]
    for m in normal:
        rest = [x["ah"] for x in normal if x is not m]
        if len(rest) >= 2:
            med = statistics.median(rest)
            if med > 0 and m["ah"] < LOW_CHARGE_SHARE * med:
                m["why_not"] = f"Drew {m['ah'] / med:.0%} of the event's typical charge (dead or stuck robot?)"
    for m in matches:
        if m["label"].upper() in exclude:
            m["why_not"] = "Excluded by hand"
        elif m["label"].upper() in include and m["why_not"]:
            m["why_not"] = None
            m["included_by_hand"] = True
        m["counted"] = m["why_not"] is None
    counted = [m for m in matches if m["counted"]]

    subs = [{"id": s.id, "name": s.name, "color": s.color, "needed_in": s.needed_in}
            for s in profile.subsystems]
    states = [s.name for s in profile.states] + ["Other"]
    stamps = sorted(m["stamp"] for m in matches if m["stamp"])

    ev = {
        "schema": SCHEMA, "pitwall": __version__, "analysis_version": ANALYSIS_VERSION,
        "code": code, "name": name or code, "profile": profile.name,
        "current_mode": profile.current_mode, "nominal_s": nominal,
        "auto_s": profile.auto_s, "teleop_s": profile.teleop_s,
        "first_stamp": stamps[0] if stamps else "", "last_stamp": stamps[-1] if stamps else "",
        "subsystem_info": subs, "states": states,
        "matches": matches, "counted": [m["label"] for m in counted],
        "averages": averages(counted, subs, states),
        "brownout": brownout_summary(counted),
        "builds": sorted({m["build"] for m in matches if m["build"]}),
        "other_logs": [{"label": p["identity"]["label"], "file": p["source"]["file"],
                        "enabled_s": p["power"]["totals"]["enabled_s"], "ah": p["power"]["totals"]["ah"]}
                       for p in sorted(others, key=lambda p: p["identity"].get("stamp", ""))],
        "skipped": skipped or [],
        "compare": None,
    }
    ev["observations"] = observations(ev)
    return ev


def _mean(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else None


def averages(ms: List[Dict[str, Any]], subs: List[Dict[str, Any]], states: List[str]) -> Dict[str, Any]:
    if not ms:
        return {"n": 0}
    a: Dict[str, Any] = {"n": len(ms)}
    for k in ("ah", "wh", "mean_a", "mean_w", "p95_a", "peak_1s_a", "peak_1s_w", "min_v",
              "ah_returned", "enabled_s"):
        a[k] = _mean([m[k] for m in ms])
    a["lowest_v"] = min(m["min_v"] for m in ms)
    for ph in ("auto", "teleop"):
        a[ph] = {k: _mean([m[ph][k] for m in ms]) for k in ("s", "ah", "mean_a", "mean_w")}
    a["quarters"] = [{k: _mean([m["quarters"][q][k] for m in ms]) for k in ("ah", "mean_a", "mean_w")}
                     for q in range(4)]
    a["state_s"] = {st: _mean([m["state_s"].get(st, 0.0) for m in ms]) for st in states}
    total = a["ah"] or 0.0
    a["subsystems"] = {}
    for s in subs:
        vals = [m["subsystems"][s["id"]] for m in ms if s["id"] in m["subsystems"]]
        ah = _mean([v["ah"] for v in vals]) or 0.0
        idle = [v["idle_ah"] for v in vals if v["idle_ah"] is not None]
        a["subsystems"][s["id"]] = {
            "ah": ah, "wh": _mean([v["wh"] for v in vals]) or 0.0,
            "mean_a": _mean([v["mean_a"] for v in vals]) or 0.0,
            "share": ah / total if total else 0.0,
            "idle_ah": _mean(idle) if idle else None,
        }
    return a


def brownout_summary(ms: List[Dict[str, Any]]) -> Dict[str, Any]:
    firsts = [m["brownout"]["first_time_left_s"] for m in ms if m["brownout"]["episodes"]]
    firsts_known = [f for f in firsts if f is not None]
    return {
        "matches": len(ms),
        "with_brownout": len(firsts),
        "median_first_left_s": statistics.median(firsts_known) if firsts_known else None,
        "earliest_first_left_s": max(firsts_known) if firsts_known else None,
        "episodes_total": sum(m["brownout"]["episodes"] for m in ms),
    }


# ---------------------------------------------------------------- comparison

METRICS = [
    # key path, label, unit, decimals
    (("ah",), "Charge per match", "Ah", 2),
    (("mean_a",), "Mean current", "A", 0),
    (("peak_1s_a",), "Peak 1 s current", "A", 0),
    (("p95_a",), "95th percentile current", "A", 0),
    (("auto", "mean_a"), "Mean current in auto", "A", 0),
    (("teleop", "mean_a"), "Mean current in teleop", "A", 0),
    (("quarters", 0, "mean_a"), "Mean current, 1st quarter", "A", 0),
    (("quarters", 1, "mean_a"), "Mean current, 2nd quarter", "A", 0),
    (("quarters", 2, "mean_a"), "Mean current, 3rd quarter", "A", 0),
    (("quarters", 3, "mean_a"), "Mean current, last quarter", "A", 0),
    (("min_v",), "Lowest battery voltage (average)", "V", 2),
    (("wh",), "Energy per match", "Wh", 1),
]


def _dig(d: Any, path) -> Optional[float]:
    for k in path:
        if d is None:
            return None
        try:
            d = d[k]
        except (KeyError, IndexError, TypeError):
            return None
    return d


def _row(label, unit, nd, cur, prev, key) -> Dict[str, Any]:
    delta = (cur - prev) if (cur is not None and prev is not None) else None
    pct = (delta / prev) if (delta is not None and prev) else None
    return {"key": key, "label": label, "unit": unit, "nd": nd, "cur": cur, "prev": prev,
            "delta": delta, "pct": pct}


def compare(cur: Dict[str, Any], prev: Dict[str, Any]) -> Dict[str, Any]:
    A, P = cur["averages"], prev["averages"]
    rows = [_row(label, unit, nd, _dig(A, path), _dig(P, path), ".".join(map(str, path)))
            for path, label, unit, nd in METRICS]
    for st in cur["states"]:
        if st == "Other":
            continue
        rows.append(_row(f"Time {st.lower()}", "s", 0, _dig(A, ("state_s", st)),
                         _dig(P, ("state_s", st)), f"state_s.{st}"))
    CB, PB = cur["brownout"], prev["brownout"]
    brown = {
        "cur": CB, "prev": PB,
        "first_left": _row("Time left at first brownout (median)", "s", 1,
                           CB["median_first_left_s"], PB["median_first_left_s"], "brownout.median"),
    }
    prev_names = {s["id"]: s["name"] for s in prev.get("subsystem_info", [])}
    sub_rows = []
    for s in cur["subsystem_info"]:
        c = _dig(A, ("subsystems", s["id"], "ah"))
        p = _dig(P, ("subsystems", s["id"], "ah")) if s["id"] in prev_names else None
        r = _row(s["name"], "Ah", 2, c, p, s["id"])
        r["color"] = s["color"]
        sub_rows.append(r)
    notes = []
    if cur["current_mode"] != prev.get("current_mode"):
        notes.append(f"{prev['name']} was analyzed with {prev.get('current_mode')} current and this event "
                     f"with {cur['current_mode']} current, so charge figures don't compare directly.")
    if cur["profile"] != prev.get("profile"):
        notes.append(f"{prev['name']} used the robot profile \"{prev.get('profile')}\"; this event uses "
                     f"\"{cur['profile']}\".")
    missing = [s["name"] for s in cur["subsystem_info"] if s["id"] not in prev_names]
    if missing:
        notes.append(f"No {', '.join(missing)} figures at {prev['name']}.")
    return {"code": prev["code"], "name": prev["name"], "first_stamp": prev.get("first_stamp", ""),
            "matches": P.get("n", 0), "rows": rows, "subsystems": sub_rows, "brownout": brown,
            "notes": notes}


def attach_comparison(cur: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> None:
    if not prev or not prev.get("averages", {}).get("n") or not cur["averages"].get("n"):
        return
    cur["compare"] = compare(cur, prev)
    cur["observations"] = observations(cur)


def strip_for_compare(ev: Dict[str, Any]) -> Dict[str, Any]:
    """What a later event needs from this one (keeps event.json files small and stable)."""
    return {k: ev[k] for k in ("schema", "pitwall", "code", "name", "profile", "current_mode",
                               "first_stamp", "last_stamp", "subsystem_info", "states",
                               "averages", "brownout", "counted")}


# ---------------------------------------------------------------- observations

def observations(ev: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    def add(sev: str, rule: str, text: str) -> None:
        out.append({"severity": sev, "rule": rule, "text": text})

    ms = ev["matches"]
    counted = [m for m in ms if m["counted"]]
    for m in ms:
        if m["silent"]:
            names = [ev_motor_name(m, k) for k in m["silent"]]
            add("act", "silent-motor",
                f"{m['label']}: {len(names)} motor(s) looked absent from the CAN bus ({', '.join(names)}).")

    B = ev["brownout"]
    if B["matches"]:
        if B["with_brownout"]:
            med = B["median_first_left_s"]
            sev = "act" if (med or 0) > 20 else "watch"
            add(sev, "brownout",
                f"Browned out in {B['with_brownout']} of {B['matches']} counted matches; the first brownout "
                f"came with {med:.1f} s left (median), {B['earliest_first_left_s']:.1f} s left at the earliest.")
        else:
            add("info", "brownout", f"No brownouts in {B['matches']} counted matches.")

    C = ev.get("compare")
    if C:
        for n in C["notes"]:
            add("watch", "compare-note", n)
        cb, pb = C["brownout"]["cur"], C["brownout"]["prev"]
        add("info", "compare-brownout",
            f"Matches with a brownout: {cb['with_brownout']} of {cb['matches']} here, "
            f"{pb['with_brownout']} of {pb['matches']} at {C['name']}.")
        fl = C["brownout"]["first_left"]
        if fl["delta"] is not None and abs(fl["delta"]) >= 1.0:
            add("info", "compare-brownout",
                f"The first brownout came {abs(fl['delta']):.1f} s {'earlier' if fl['delta'] > 0 else 'later'} "
                f"in the match than at {C['name']} (median {fl['cur']:.1f} vs {fl['prev']:.1f} s left).")
        for r in C["subsystems"]:
            if r["delta"] is None or r["pct"] is None:
                continue
            if abs(r["delta"]) >= CHANGE_MIN_AH and abs(r["pct"]) >= CHANGE_SHARE:
                add("info", "compare-subsystem",
                    f"{r['label']} used {abs(r['delta']):.2f} Ah {'more' if r['delta'] > 0 else 'less'} per match "
                    f"than at {C['name']} ({r['pct']:+.0%}).")

    excluded = [m for m in ms if not m["counted"]]
    if excluded:
        add("info", "not-counted", "Not counted in the averages: "
            + "; ".join(f"{m['label']} ({m['why_not'][0].lower() + m['why_not'][1:]})" for m in excluded) + ".")
    for m in ms:
        if m.get("other_logs"):
            add("info", "duplicate-log",
                f"{m['label']} has {len(m['other_logs']) + 1} logs (replayed, or the robot restarted); "
                f"using {m['file']} ({m['enabled_s']:.0f} s enabled).")

    if counted:
        sat: Dict[str, int] = {}
        names: Dict[str, str] = {}
        for m in counted:
            for mid, mo in m["motors"].items():
                names[mid] = mo["name"]
                if mo["saturated_share"] is not None and mo["active_s"] >= 10 and mo["saturated_share"] >= 0.75:
                    sat[mid] = sat.get(mid, 0) + 1
        for mid, k in sat.items():
            if k >= max(2, (len(counted) + 1) // 2):
                add("info", "saturated",
                    f"{names[mid]} ran at full output for most of its active time in {k} of {len(counted)} matches.")
        for s in ev["subsystem_info"]:
            idle = ev["averages"]["subsystems"][s["id"]]["idle_ah"]
            if idle is not None and idle >= 0.1:
                add("info", "idle-draw",
                    f"{s['name']} used {idle:.2f} Ah per match outside {', '.join(s['needed_in'])}.")
        keys: Set[str] = set()
        for m in counted:
            for o in m["observations"]:
                if o["rule"] in ("missing-key", "unassigned-key"):
                    keys.add(o["text"])
        for t in sorted(keys):
            add("watch", "profile-key", t)
    if len(ev["builds"]) > 1:
        add("info", "builds", f"{len(ev['builds'])} code builds ran at this event: {', '.join(ev['builds'])}.")

    order = {"act": 0, "watch": 1, "info": 2}
    out.sort(key=lambda o: order[o["severity"]])
    return out


def ev_motor_name(m: Dict[str, Any], mid: str) -> str:
    mo = m["motors"].get(mid)
    return mo["name"] if mo else mid
