"""Scoring module: how many balls the robot shot, at what, how fast, and when.

There is no ball sensor, so shots are counted from the flywheel. Each ball pulls the measured
speed down by about 5-6 rps for roughly 40 ms, and the velocity loop brings it back within
about 0.1 s. While the shoot command runs and the feeder gate is open, a shot is counted each
time the tracking error (velocity - setpoint) drops sharply. This is the detector from the
October shot-counter work (shot_counter.py / ShotDetector.java) with one change: the
"overspeed" and "below setpoint" rules are measured against the flywheel's own running level
(the median tracking error over the last second, never below zero) instead of the setpoint.
From October 2026 the flywheel runs 1.5-2 rps above its setpoint while feeding, and the
setpoint-based rules threw most real dips away (BOB Q7: 72 shots counted vs about 270).

These are shots, not scored balls: misses and blocked shots count. Without video to calibrate
against, absolute counts carry about +/-25% uncertainty; comparisons between matches and
events are much better than that, because every match uses the same detector.

Each shot is filed by what the robot was aiming at when it left:
  hub           aiming at our hub (scoring)
  shuttle_mid   shuttling from the neutral zone
  shuttle_full  shuttling from the opponent's zone (fixed 100 rps setpoint in 2026)
"""

import statistics
from collections import deque
from typing import Any, Dict, List, Optional

from .match import Match
from .profile import first_present

CATS = ("hub", "shuttle_mid", "shuttle_full")
CAT_NAMES = {"hub": "Hub", "shuttle_mid": "Shuttle mid", "shuttle_full": "Shuttle full"}
GROUPS = {"hub": ("hub",), "shuttle": ("shuttle_mid", "shuttle_full")}
FLOW_MIN_BALLS = 4          # a burst needs this many balls to count toward flowing BPS
SLOW_VELOCITY_MS = 45.0     # a velocity key logged slower than this can miss short dips


def applies(profile) -> bool:
    return profile.scoring is not None


# ---------------------------------------------------------------- detection

def detect(t: List[float], v: List[float], sp: List[float], armed_on: List[bool], gate: List[bool],
           cfg) -> List[int]:
    """Indices of counted shots. `armed_on` = shoot command running (and robot enabled);
    `gate` = feeder gate open while shooting."""
    shots: List[int] = []
    peak_buf: deque = deque()     # (t, error) over the last window_s, for the recent peak
    level_buf: deque = deque()    # (t, error) over the last level_s, for the running level
    in_dip = False
    trough = 0.0
    last_shot = -1e9
    last_gate = -1e9
    for i in range(len(t)):
        if gate[i]:
            last_gate = t[i]
        armed = armed_on[i] and (t[i] - last_gate) <= cfg.hangover_s and v[i] > cfg.min_speed
        if not armed:
            peak_buf.clear()
            level_buf.clear()
            in_dip = False
            continue
        e = v[i] - sp[i]
        while level_buf and level_buf[0][0] < t[i] - cfg.level_s:
            level_buf.popleft()
        level = statistics.median(x[1] for x in level_buf) if len(level_buf) >= 5 else 0.0
        level = max(0.0, level)   # never looser than the setpoint rule when the flywheel runs low
        level_buf.append((t[i], e))
        if in_dip:
            trough = min(trough, e)
            if e - trough >= cfg.rearm:
                in_dip = False
                peak_buf.clear()
            else:
                continue
        while peak_buf and peak_buf[0][0] < t[i] - cfg.window_s:
            peak_buf.popleft()
        peak = max([x[1] for x in peak_buf] + [e])
        peak_buf.append((t[i], e))
        if (peak - e >= cfg.dip and peak <= level + cfg.overspeed and e <= level - cfg.below
                and t[i] - last_shot >= cfg.lockout_s):
            shots.append(i)
            last_shot = t[i]
            in_dip = True
            trough = e
    return shots


def bursts_of(times: List[float], gap: float) -> List[List[int]]:
    """Group sorted shot times into bursts (gaps under `gap` s); returns lists of positions."""
    out: List[List[int]] = []
    for k, x in enumerate(times):
        if out and x - times[out[-1][-1]] < gap:
            out[-1].append(k)
        else:
            out.append([k])
    return out


def flow_bps(times: List[float], gap: float) -> Optional[float]:
    """Shots per second inside bursts of at least FLOW_MIN_BALLS: the shooter's rate once balls flow."""
    bs = [b for b in bursts_of(times, gap) if len(b) >= FLOW_MIN_BALLS]
    span = sum(times[b[-1]] - times[b[0]] for b in bs)
    if span <= 0.5:
        return None
    return sum(len(b) - 1 for b in bs) / span


# ---------------------------------------------------------------- one match

def _bools(vals: List[Any]) -> List[bool]:
    return [bool(x) for x in vals]


def analyze(m: Match) -> Dict[str, Any]:
    cfg = m.profile.scoring
    log = m.log
    keys = {name: first_present(log, getattr(cfg, name))
            for name in ("velocity", "setpoint", "shooting", "feeding", "targeting_hub", "full_field")}
    missing = [n for n in ("velocity", "setpoint", "shooting") if keys[n] is None]
    if missing:
        return {"available": False,
                "why": "The log has no " + ", ".join(missing) + " key from the profile's [scoring] section."}
    for n in ("feeding", "targeting_hub"):
        if getattr(cfg, n) and keys[n] is None:
            missing.append(n)

    n = len(m.grid)
    dt = m.dt
    v = [x if isinstance(x, (int, float)) else 0.0 for x in m.held(keys["velocity"])]
    sp = [x if isinstance(x, (int, float)) else 0.0 for x in m.held(keys["setpoint"])]
    shooting = _bools(m.held(keys["shooting"]))
    feeding = _bools(m.held(keys["feeding"])) if keys["feeding"] else shooting
    hub = _bools(m.held(keys["targeting_hub"])) if keys["targeting_hub"] else [True] * n
    if keys["full_field"]:
        full = _bools(m.held(keys["full_field"]))
    else:
        full = [s >= cfg.full_field_setpoint for s in sp]
    on = [m.enabled[i] and shooting[i] for i in range(n)]
    gate = [on[i] and feeding[i] for i in range(n)]

    def cat(i: int) -> str:
        return "hub" if hub[i] else ("shuttle_full" if full[i] else "shuttle_mid")

    idx = detect(m.grid, v, sp, on, gate, cfg)
    t0 = m.t0
    shots = [{"t": round(m.grid[i] - t0, 3),
              "left": None if m.time_left[i] is None else round(m.time_left[i], 2),
              "cat": cat(i), "auto": m.auto[i],
              "period": m.period[i] if m.period else None} for i in idx]

    counts = {c: {"auto": 0, "teleop": 0, "total": 0} for c in CATS}
    for s in shots:
        c = counts[s["cat"]]
        c["auto" if s["auto"] else "teleop"] += 1
        c["total"] += 1
    trigger_s = {c: 0.0 for c in CATS}
    feed_s = {c: 0.0 for c in CATS}
    for i in range(n):
        if on[i]:
            trigger_s[cat(i)] += dt
            if gate[i]:
                feed_s[cat(i)] += dt

    rates = {}
    for g, cs in GROUPS.items():
        times = [s["t"] for s in shots if s["cat"] in cs]
        trig = sum(trigger_s[c] for c in cs)
        feed = sum(feed_s[c] for c in cs)
        rates[g] = {"shots": len(times), "trigger_s": trig, "feed_s": feed,
                    "bps_flow": flow_bps(times, cfg.burst_gap_s),
                    "bps_trigger": len(times) / trig if trig >= 1.0 else None,
                    "bps_feed": len(times) / feed if feed >= 1.0 else None}

    # Bursts (hopper dumps), any target
    times = [s["t"] for s in shots]
    bursts = []
    groups = bursts_of(times, cfg.burst_gap_s)
    for k, b in enumerate(groups):
        first, last = shots[b[0]], shots[b[-1]]
        cats = [shots[j]["cat"] for j in b]
        main = max(CATS, key=cats.count)
        dur = last["t"] - first["t"]
        nxt = groups[k + 1] if k + 1 < len(groups) else None
        bursts.append({"t": first["t"], "left": first["left"], "cat": main, "balls": len(b),
                       "mixed": len(set(cats)) > 1, "dur_s": round(dur, 2),
                       "bps": (len(b) - 1) / dur if (len(b) >= FLOW_MIN_BALLS and dur > 0) else None,
                       "gap_next_s": round(shots[nxt[0]]["t"] - last["t"], 2) if nxt else None})

    # Game periods (2026 shifts)
    periods = []
    if m.profile.periods and m.period:
        for k, per in enumerate(m.profile.periods):
            ticks = [i for i in range(n) if m.period[i] == k]
            row = {"name": per.name, "s": len(ticks) * dt,
                   "hub_active": m.period_hub[k] if k < len(m.period_hub) else None,
                   "conditional": per.hub != "active",
                   "trigger_s": sum(dt for i in ticks if on[i])}
            for c in CATS:
                row[c] = sum(1 for s in shots if s["period"] == k and s["cat"] == c)
            periods.append(row)

    obs = []
    iv = m.median_interval_ms(keys["velocity"])
    if iv is not None and iv > SLOW_VELOCITY_MS:
        obs.append({"severity": "watch", "rule": "slow-velocity",
                    "text": f"Flywheel speed was logged every {iv:.0f} ms (median), so short dips can be missed "
                            "and the shot count may be low."})
    for name in missing:
        obs.append({"severity": "watch", "rule": "scoring-key",
                    "text": f"The profile's scoring {name.replace('_', ' ')} key isn't in this log; "
                            + ("shots are counted whenever the shoot command runs." if name == "feeding" else
                               "every shot is counted as a hub shot.")})

    return {"available": True, "keys": keys, "counts": counts, "trigger_s": trigger_s, "feed_s": feed_s,
            "rates": rates, "bursts": bursts, "periods": periods, "shots": shots,
            "velocity_interval_ms": iv, "observations": obs,
            "settings": {k: getattr(cfg, k) for k in ("dip", "rearm", "lockout_s", "min_speed", "overspeed",
                                                     "below", "hangover_s", "window_s", "level_s", "burst_gap_s")}}


# ---------------------------------------------------------------- event roll-up

def match_summary(sc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What the event report keeps from one match (no shot list)."""
    if not sc or not sc.get("available"):
        return None
    C, R = sc["counts"], sc["rates"]
    sizes = [b["balls"] for b in sc["bursts"] if b["balls"] >= 2]
    return {
        "hub": C["hub"]["total"], "hub_auto": C["hub"]["auto"], "hub_teleop": C["hub"]["teleop"],
        "shuttle_mid": C["shuttle_mid"]["total"], "shuttle_full": C["shuttle_full"]["total"],
        "shuttle": C["shuttle_mid"]["total"] + C["shuttle_full"]["total"],
        "trigger_s": sum(sc["trigger_s"].values()),
        "hub_trigger_s": R["hub"]["trigger_s"], "shuttle_trigger_s": R["shuttle"]["trigger_s"],
        "bps_flow_hub": R["hub"]["bps_flow"], "bps_trigger_hub": R["hub"]["bps_trigger"],
        "bps_flow_shuttle": R["shuttle"]["bps_flow"], "bps_trigger_shuttle": R["shuttle"]["bps_trigger"],
        "bursts": len(sizes), "burst_median": statistics.median(sizes) if sizes else None,
        "periods": [{k: p[k] for k in ("name", "hub_active", "conditional", "trigger_s") + CATS}
                    for p in sc["periods"]],
        "velocity_interval_ms": sc.get("velocity_interval_ms"),
        "observations": sc.get("observations", []),
    }


def _mean(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _median(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def roll_up(ms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Averages over the counted matches that have scoring data. `ms` are match summaries."""
    S = [m["scoring"] for m in ms if m.get("scoring")]
    if not S:
        return {"n": 0}
    a: Dict[str, Any] = {"n": len(S)}
    for k in ("hub", "hub_auto", "hub_teleop", "shuttle_mid", "shuttle_full", "shuttle", "trigger_s",
              "hub_trigger_s", "shuttle_trigger_s"):
        a[k] = _mean([s[k] for s in S])
    a["hub_median"] = _median([s["hub"] for s in S])
    a["shuttle_median"] = _median([s["shuttle"] for s in S])
    for k in ("bps_flow_hub", "bps_trigger_hub", "bps_flow_shuttle", "bps_trigger_shuttle", "burst_median"):
        a[k] = _median([s[k] for s in S])
    names: List[str] = []
    for s in S:
        for p in s["periods"]:
            if p["name"] not in names:
                names.append(p["name"])
    a["periods"] = []
    for nm in names:
        ps = [p for s in S for p in s["periods"] if p["name"] == nm]
        on = [p for p in ps if p["hub_active"] is True]
        off = [p for p in ps if p["hub_active"] is False]
        a["periods"].append({
            "name": nm, "n": len(ps), "conditional": any(p["conditional"] for p in ps),
            "hub": _mean([p["hub"] for p in ps]),
            "shuttle_mid": _mean([p["shuttle_mid"] for p in ps]),
            "shuttle_full": _mean([p["shuttle_full"] for p in ps]),
            "shuttle": _mean([p["shuttle_mid"] + p["shuttle_full"] for p in ps]),
            "trigger_s": _mean([p["trigger_s"] for p in ps]),
            "hub_when_active": _mean([p["hub"] for p in on]), "active_n": len(on),
            "hub_when_inactive": _mean([p["hub"] for p in off]), "inactive_n": len(off),
        })
    cond = [p for s in S for p in s["periods"] if p["conditional"]]
    on = [p["hub"] for p in cond if p["hub_active"] is True]
    off = [p["hub"] for p in cond if p["hub_active"] is False]
    a["per_active_shift"] = _mean(on)
    a["per_inactive_shift"] = _mean(off)
    return a


METRICS = [
    ("hub", "Hub shots per match", 0),
    ("hub_auto", "Hub shots in auto", 0),
    ("hub_teleop", "Hub shots in teleop", 0),
    ("per_active_shift", "Hub shots per active shift", 1),
    ("shuttle_mid", "Shuttle mid shots per match", 0),
    ("shuttle_full", "Shuttle full shots per match", 0),
    ("bps_flow_hub", "Hub BPS while flowing (median)", 2),
    ("bps_trigger_hub", "Hub BPS per trigger second (median)", 2),
    ("bps_flow_shuttle", "Shuttle BPS while flowing (median)", 2),
    ("trigger_s", "Trigger time per match", 0),
    ("burst_median", "Balls per burst (median)", 0),
]


def compare(cur: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Rows comparing this event's scoring averages with an earlier event's."""
    if not prev or not prev.get("n") or not cur.get("n"):
        return None
    rows = []
    for key, label, nd in METRICS:
        c, p = cur.get(key), prev.get(key)
        d = (c - p) if (c is not None and p is not None) else None
        rows.append({"key": key, "label": label, "nd": nd, "cur": c, "prev": p, "delta": d,
                     "pct": (d / p) if (d is not None and p) else None})
    return {"n": prev["n"], "rows": rows, "periods": {x["name"]: x for x in prev.get("periods", [])},
            "averages": {k: prev.get(k) for k in ("hub", "shuttle_mid", "shuttle_full", "shuttle")}}


def observations(ev: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    def add(sev: str, rule: str, text: str) -> None:
        out.append({"severity": sev, "rule": rule, "text": text})

    sc = ev.get("scoring") or {}
    A = sc.get("averages") or {}
    ms = [m for m in ev["matches"] if m["counted"] and m.get("scoring")]
    if not A.get("n"):
        return out
    no_auto = [m["label"] for m in ms if m["scoring"]["hub_auto"] == 0]
    if no_auto:
        add("watch", "no-auto-shots", f"No hub shots in auto in {', '.join(no_auto)}.")
    med = A.get("hub_median")
    if med:
        low = [m for m in ms if m["scoring"]["hub"] < 0.5 * med]
        for m in low:
            add("watch", "low-shots", f"{m['label']}: {m['scoring']['hub']} hub shots, under half the event "
                                      f"median of {med:.0f}.")
    if A.get("per_active_shift") is not None and A.get("per_inactive_shift") is not None:
        add("info", "shift-split", f"In the alliance shifts we averaged {A['per_active_shift']:.0f} hub shots per "
                                   f"active shift and {A['per_inactive_shift']:.1f} per inactive shift.")
    if A.get("bps_flow_hub") and A.get("bps_trigger_hub"):
        add("info", "bps-gap", f"Hub BPS was {A['bps_flow_hub']:.2f} while balls were flowing and "
                               f"{A['bps_trigger_hub']:.2f} per second of holding the trigger (medians).")
    slow = [m["label"] for m in ms if (m["scoring"].get("velocity_interval_ms") or 0) > SLOW_VELOCITY_MS]
    if slow:
        add("watch", "slow-velocity", "Flywheel speed was logged slowly in " + ", ".join(slow)
            + ", so their shot counts may be low.")
    C = sc.get("compare")
    if C:
        name = ev["compare"]["name"] if ev.get("compare") else "the previous event"
        for r in C["rows"]:
            if r["delta"] is None or r["pct"] is None:
                continue
            if r["key"] in ("hub", "hub_auto", "hub_teleop", "shuttle_mid", "shuttle_full") and \
                    abs(r["pct"]) >= 0.15 and abs(r["delta"]) >= 3:
                add("info", "compare-shots", f"{r['label']}: {r['cur']:.0f} here, {r['prev']:.0f} at {name} "
                                             f"({r['pct']:+.0%}).")
            if r["key"] == "bps_flow_hub" and abs(r["delta"]) >= 0.3:
                add("info", "compare-bps", f"Hub BPS while flowing: {r['cur']:.2f} here, {r['prev']:.2f} at {name}.")
    order = {"act": 0, "watch": 1, "info": 2}
    out.sort(key=lambda o: order[o["severity"]])
    return out
