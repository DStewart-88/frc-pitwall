"""Power budget module: where the battery's charge went in one match.

Every logged motor sits alone on its power-distribution channel, so its supply current is
its draw from the battery. Charge (Ah) adds up supply current over enabled time; energy (Wh)
multiplies by battery voltage first.

Supply current is negative while a motor brakes and pushes charge back onto the power bus.
In the default "signed" mode that returned charge is subtracted (it mostly feeds other
motors at the same instant) and reported separately. "abs" mode counts it as draw, which
reproduces the hand analyses done before Pitwall.
"""

import math
from typing import Any, Dict, List, Optional

from .match import Match

BROWNOUT_V = 6.75
PEAK_WINDOW_S = 1.0
SLOW_SAMPLE_MS = 50.0          # a supply key sampled slower than this can't show short spikes
SERIES_STEP_S = 0.1


def _p(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _peak_window_mean(values: List[float], enabled: List[bool], w: int) -> float:
    """Highest mean of `values` over any `w` consecutive grid ticks that are all enabled."""
    best = 0.0
    run_sum = 0.0
    run_len = 0
    for i, v in enumerate(values):
        if not enabled[i]:
            run_sum, run_len = 0.0, 0
            continue
        run_sum += v
        run_len += 1
        if run_len > w:
            run_sum -= values[i - w]
            run_len = w
        if run_len == w:
            best = max(best, run_sum / w)
    return best


def analyze(m: Match) -> Dict[str, Any]:
    p = m.profile
    dt = m.dt
    n = len(m.grid)
    en = m.enabled
    idx = m.enabled_idx
    enabled_s = len(idx) * dt
    if enabled_s <= 0:
        raise ValueError("no enabled time in this match")
    to_h = dt / 3600.0
    w = max(1, round(PEAK_WINDOW_S / dt))
    absmode = p.current_mode == "abs"
    state_names = [s.name for s in p.states] + ["Other"]

    total_i = [0.0] * n
    sub_i: Dict[str, List[float]] = {s.id: [0.0] * n for s in p.subsystems}
    motors: List[Dict[str, Any]] = []

    for mo in p.motors:
        keys = m.check.resolved[mo.id]
        raw = m.held(keys["supply"])
        cur = [abs(x) if (absmode and x is not None) else (x or 0.0) for x in raw]
        volts = m.held(keys["voltage"]) if keys["voltage"] else None
        vel = m.held(keys["velocity"]) if keys["velocity"] else None
        ah = wh = drawn = returned = stalled = 0.0
        active = sat = 0
        by_state = {s: 0.0 for s in state_names}
        en_vals = []
        for i in idx:
            c = cur[i]
            en_vals.append(c)
            ah += c * to_h
            wh += c * m.vbat[i] * to_h
            if c >= 0:
                drawn += c * to_h
            else:
                returned -= c * to_h
            by_state[m.state[i]] += c * to_h
            if c > p.active_amps:
                active += 1
                if volts is not None and volts[i] is not None and m.vbat[i] > 1.0:
                    if abs(volts[i]) / m.vbat[i] >= p.saturation:
                        sat += 1
                if vel is not None and vel[i] is not None and abs(vel[i]) < mo.stall_below:
                    stalled += c * to_h
        for i in range(n):
            total_i[i] += cur[i]
            sub_i[mo.subsystem][i] += cur[i]
        interval = m.median_interval_ms(keys["supply"])
        motors.append({
            "id": mo.id, "name": mo.name, "subsystem": mo.subsystem, "can_id": mo.can_id,
            "bus": mo.bus, "key": keys["supply"],
            "ah": ah, "wh": wh, "ah_drawn": drawn, "ah_returned": returned,
            "mean_a": ah * 3600.0 / enabled_s,
            "p95_a": _p(en_vals, 0.95), "peak_1s_a": _peak_window_mean(cur, en, w),
            "max_a_logged": max(en_vals) if en_vals else 0.0,
            "active_s": active * dt,
            "saturated_share": (sat / active) if (active and volts is not None) else None,
            "stalled_ah": stalled if vel is not None else None,
            "sample_interval_ms": interval,
            "peaks_reliable": interval is not None and interval <= SLOW_SAMPLE_MS,
            "ah_by_state": by_state,
        })

    total_p = [m.vbat[i] * total_i[i] for i in range(n)]
    ah = sum(total_i[i] for i in idx) * to_h
    wh = sum(total_p[i] for i in idx) * to_h

    # Subsystems
    subsystems = []
    for s in p.subsystems:
        ms = [x for x in motors if x["subsystem"] == s.id]
        s_ah = sum(x["ah"] for x in ms)
        by_state = {st: sum(x["ah_by_state"][st] for x in ms) for st in state_names}
        idle = None
        if s.needed_in:
            idle = sum(v for st, v in by_state.items() if st not in s.needed_in)
        subsystems.append({
            "id": s.id, "name": s.name, "color": s.color, "motors": [x["id"] for x in ms],
            "ah": s_ah, "wh": sum(x["wh"] for x in ms),
            "ah_returned": sum(x["ah_returned"] for x in ms),
            "share": s_ah / ah if ah else 0.0,
            "mean_a": s_ah * 3600.0 / enabled_s,
            "peak_1s_a": _peak_window_mean(sub_i[s.id], en, w),
            "ah_by_state": by_state, "idle_ah": idle, "needed_in": s.needed_in,
        })

    # Phases and quarters of enabled time
    def span(ix: List[int]) -> Dict[str, float]:
        if not ix:
            return {"s": 0.0, "ah": 0.0, "wh": 0.0, "mean_a": 0.0, "mean_w": 0.0}
        a = sum(total_i[i] for i in ix) * to_h
        e = sum(total_p[i] for i in ix) * to_h
        s_ = len(ix) * dt
        return {"s": s_, "ah": a, "wh": e, "mean_a": a * 3600 / s_, "mean_w": e * 3600 / s_}

    auto_ix = [i for i in idx if m.auto[i]]
    tele_ix = [i for i in idx if not m.auto[i]]
    q = len(idx) / 4.0
    quarters = [span(idx[round(k * q):round((k + 1) * q)]) for k in range(4)]
    state_s = {st: sum(1 for i in idx if m.state[i] == st) * dt for st in state_names}

    # Brownouts: rising edges of the robot's brownout flag inside the match window
    bkey = next((k for k in p.brownout if m.log.has(k)), None)
    onsets: List[float] = []
    if bkey:
        t, v = m.log.series(bkey)
        prev = False
        for ti, vi in zip(t, v):
            b = bool(vi)
            if b and not prev and m.grid[0] <= ti <= m.grid[-1]:
                onsets.append(ti)
            prev = b
    episodes = [o for k, o in enumerate(onsets) if k == 0 or o - onsets[k - 1] > 1.0]

    def left_at(t: float) -> Optional[float]:
        i = min(n - 1, max(0, round((t - m.grid[0]) / dt)))
        for j in range(i, max(-1, i - 50), -1):
            if m.time_left[j] is not None:
                return m.time_left[j]
        return None

    first = episodes[0] if episodes else None
    en_v = [m.vbat[i] for i in idx]
    brownout = {
        "key": bkey, "onsets": len(onsets), "episodes": len(episodes),
        "first_t": (first - m.t0) if first is not None else None,
        "first_time_left_s": left_at(first) if first is not None else None,
        "episode_times": [o - m.t0 for o in episodes],
        "episode_time_left": [left_at(o) for o in episodes],
        "seconds_below_threshold": sum(1 for i in idx if m.vbat[i] < BROWNOUT_V) * dt,
        "threshold_v": BROWNOUT_V,
    }

    unmeasured = [{"name": u.name, "estimate_amps": u.estimate_amps,
                   "ah": u.estimate_amps * enabled_s / 3600.0} for u in p.unmeasured]

    totals = {
        "enabled_s": enabled_s, "ah": ah, "wh": wh,
        "mean_a": ah * 3600 / enabled_s, "mean_w": wh * 3600 / enabled_s,
        "p95_a": _p([total_i[i] for i in idx], 0.95),
        "peak_1s_a": _peak_window_mean(total_i, en, w),
        "peak_1s_w": _peak_window_mean(total_p, en, w),
        "min_v": min(en_v), "ah_returned": sum(x["ah_returned"] for x in motors),
        "auto": span(auto_ix), "teleop": span(tele_ix), "quarters": quarters,
        "state_s": state_s, "current_mode": p.current_mode,
    }

    # Closure check: subsystems add up to the total (they must, by construction)
    closure = abs(sum(s["ah"] for s in subsystems) - ah)

    result = {
        "module": "power",
        "totals": totals, "subsystems": subsystems, "motors": motors,
        "brownout": brownout, "unmeasured": unmeasured,
        "checks": {"subsystem_sum_error_ah": closure},
        "series": _series(m, sub_i, total_i, onsets),
    }
    result["observations"] = observations(m, result)
    return result


def _series(m: Match, sub_i: Dict[str, List[float]], total_i: List[float],
            onsets: List[float]) -> Dict[str, Any]:
    step = max(1, round(SERIES_STEP_S / m.dt))
    n = len(m.grid)
    t = []
    vb = []
    subs: Dict[str, List[float]] = {k: [] for k in sub_i}
    tot = []
    for a in range(0, n - step + 1, step):
        t.append(round(m.grid[a] - m.t0, 3))
        vb.append(round(min(m.vbat[a:a + step]), 3))
        for k, vals in sub_i.items():
            subs[k].append(round(sum(vals[a:a + step]) / step, 2))
        tot.append(round(sum(total_i[a:a + step]) / step, 2))
    return {
        "t": t, "vbat": vb, "subsystem_a": subs, "total_a": tot,
        "blocks": [{"start": b.start - m.t0, "end": b.end - m.t0, "mode": b.mode} for b in m.blocks],
        "brownout_onsets": [o - m.t0 for o in onsets],
    }


def observations(m: Match, r: Dict[str, Any]) -> List[Dict[str, str]]:
    """Short factual flags. Severity: act / watch / info."""
    out: List[Dict[str, str]] = []

    def add(sev: str, rule: str, text: str) -> None:
        out.append({"severity": sev, "rule": rule, "text": text})

    if m.silent:
        names = {mo.id: mo.name for mo in m.profile.motors}
        add("act", "silent-motor",
            f"{len(m.silent)} motor(s) look absent from the CAN bus: "
            + "; ".join(f"{names[k]} ({why})" for k, why in m.silent.items()) + ".")
    for key in m.check.missing:
        add("watch", "missing-key", f"Profile key not in this log: {key}.")
    for key in m.check.unassigned:
        add("watch", "unassigned-key", f"Supply current {key} isn't assigned to any motor in the profile.")
    for f in m.flags:
        add("info", "match-flag", f + ".")

    b = r["brownout"]
    if b["episodes"]:
        left = b["first_time_left_s"]
        when = f" with {left:.1f} s left" if left is not None else ""
        add("act" if (left or 0) > 20 else "watch", "brownout",
            f"{b['episodes']} brownout episode(s); the first started{when}.")

    for mo in r["motors"]:
        sat = mo["saturated_share"]
        if sat is not None and mo["active_s"] >= 10 and sat >= 0.75:
            add("info", "saturated",
                f"{mo['name']} ran at full output for {sat:.0%} of its active time "
                f"({mo['active_s']:.0f} s): its setpoint is above what it reaches.")
    for s in r["subsystems"]:
        if s["idle_ah"] is not None and s["idle_ah"] >= 0.1:
            add("info", "idle-draw",
                f"{s['name']} used {s['idle_ah']:.2f} Ah outside {', '.join(s['needed_in'])}.")
    slow = [mo["name"] for mo in r["motors"] if mo["sample_interval_ms"] and not mo["peaks_reliable"]]
    if slow:
        add("info", "slow-samples",
            f"{len(slow)} motor(s) report current slower than every {SLOW_SAMPLE_MS:.0f} ms, so their "
            f"short spikes are missed (totals are still right): {', '.join(slow)}.")
    t = r["totals"]
    if t["current_mode"] == "signed" and t["ah_returned"] > 0.05:
        add("info", "regen",
            f"Braking motors returned {t['ah_returned']:.2f} Ah to the power bus; it's subtracted from the totals.")
    order = {"act": 0, "watch": 1, "info": 2}
    out.sort(key=lambda o: order[o["severity"]])
    return out
