"""Health: the robot's own health checks, wear tracking, CAN bus and loop timing, per match.

Everything here comes from what the robot already logs (its health monitor, the tread tracker,
AdvantageKit's system stats); Pitwall only reads it back and lays it out.

  devices  each health-monitor check that was active while the robot was enabled: how long and
           how many times, its alert text (which names the device and CAN ID) and whether the
           robot calls it an error or a warning. Power checks (brownout, battery sag) are left to
           the Power tab. Logs from before the health monitor have no device section.
  wear     each wear tracker (2026: swerve tread per module): distance since replacement, distance
           this match, the next inspection point and whether it's due, as the robot reports them.
  can      roboRIO CAN bus utilization and error counters while enabled.
  loop     robot loop time while enabled: median, 95th percentile, worst, share over the 20 ms
           target, and garbage-collection time.
"""

import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from .match import Match
from .profile import first_present



def applies(profile) -> bool:
    return profile.health is not None


def _pct(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _part_words(part: str) -> List[str]:
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", part)]


def _match_text(rel: str, texts: List[str]) -> Optional[str]:
    """The alert text that names this check, or None if no text clearly does.

    A check's key has parts ('Drive/BackLeft/TurnEncoder'). A part is found in a text when its
    letters appear run together ('backleft' in 'Back-left module (2) ...') or all its words do.
    Every part after the first must be found; the first (usually just the subsystem) only
    breaks ties. Two texts still tied: no match.
    """
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return None
    head, rest = (parts[0], parts[1:]) if len(parts) > 1 else ("", parts)

    def found(p: str, flat: str, words: set) -> bool:
        return _flat(p) in flat or all(w in words for w in _part_words(p))
    best: List[Tuple[int, str]] = []
    for t in texts:
        flat = _flat(t)
        words = set(re.findall(r"[a-z]+|\d+", t.lower()))
        if all(found(p, flat, words) for p in rest):
            best.append((1 if head and found(head, flat, words) else 0, t))
    if not best:
        return None
    top = max(b[0] for b in best)
    winners = [t for sc, t in best if sc == top]
    return winners[0] if len(set(winners)) == 1 else None


def _devices(m: Match, H) -> Dict[str, Any]:
    keys = [n for n, typ in m.log.entries(H.faults) if typ == "boolean" and n.startswith(H.faults)]
    keys = [k for k in keys if not any(k[len(H.faults):].startswith(s) for s in H.skip)]
    if not keys:
        return {"available": False, "checks": 0, "faults": []}
    en, dt, grid, t0 = m.enabled, m.dt, m.grid, m.t0
    hi = grid[-1]

    def alert_texts(key: str) -> List[Tuple[float, List[str]]]:
        k = first_present(m.log, [key])
        if not k:
            return []
        t, v = m.log.series(k)
        return [(ti, list(vi or [])) for ti, vi in zip(t, v) if ti <= hi + 5]
    errs = [x for k in H.alerts_errors for x in alert_texts(k)]
    warns = [x for k in H.alerts_warnings for x in alert_texts(k)]

    faults = []
    for key in keys:
        held = m.held(key)
        active_s, count, prev, spans, start = 0.0, 0, False, [], None
        for i, g in enumerate(grid):
            on = bool(held[i]) and en[i]
            if on:
                active_s += dt
                # a new fault: it just came on, or it was already on at the first enable
                if (i == 0 or not held[i - 1]) or (count == 0 and not prev):
                    count += 1
                if not prev:
                    start = g
            elif prev and start is not None:
                spans.append((round(start - t0, 2), round(g - t0, 2)))
            prev = on
        if prev and start is not None:
            spans.append((round(start - t0, 2), round(grid[-1] - t0, 2)))
        if active_s <= 0:
            continue
        rel = key[len(H.faults):]
        # Alert texts showing while this check was active name the device (and its CAN ID).
        windows = [(a + t0 - 0.5, b + t0 + 1.0) for a, b in spans]

        def during(samples):
            """Texts showing at any time in the windows (including what was showing as each began)."""
            out: List[str] = []
            for a, b in windows:
                before = [lst for ti, lst in samples if ti < a]
                inside = [lst for ti, lst in samples if a <= ti <= b]
                for lst in (before[-1:] + inside):
                    out += [x for x in lst if x not in out]
            return out
        e_txt, w_txt = during(errs), during(warns)
        text = _match_text(rel, e_txt)
        sev = "error" if text else None
        if text is None:
            text = _match_text(rel, w_txt)
            sev = "warning" if text else None
        if sev is None:
            sev = "warning" if any(rel.startswith(w) for w in H.warnings) else "error"
        faults.append({"key": rel, "text": text or rel.replace("/", " · "), "severity": sev,
                       "active_s": round(active_s, 1), "count": count, "spans": spans[:40]})
    faults.sort(key=lambda f: (f["severity"] != "error", -f["active_s"], f["key"]))
    return {"available": True, "checks": len(keys), "faults": faults}


def _last_at(m: Match, key: str, t: float) -> Any:
    ts, vs = m.log.series(key)
    out = None
    for ti, vi in zip(ts, vs):
        if ti > t:
            break
        out = vi
    return out


def _wear(m: Match, H) -> Dict[str, Any]:
    pre = H.wear
    parts = sorted({n[len(pre):].split("/")[0] for n, _ in m.log.entries(pre)
                    if n.startswith(pre) and n.endswith("/SinceReplacementMeters")})
    if not parts:
        return {"available": False, "name": H.wear_name, "parts": []}
    lo, hi = m.grid[0], m.grid[-1]
    out = []
    for p in parts:
        k = f"{pre}{p}/"

        def last(name, t=hi):
            return _last_at(m, k + name, t) if m.log.has(k + name) else None
        since_end, since_start = last("SinceReplacementMeters"), last("SinceReplacementMeters", lo)
        if since_start is None and m.log.has(k + "SinceReplacementMeters"):
            since_start = m.log.series(k + "SinceReplacementMeters")[1][0]
        out.append({
            "part": p,
            "since_m": since_end, "match_m": (since_end - since_start) if None not in (since_end, since_start) else None,
            "lifetime_m": last("LifetimeMeters"), "next_inspection_m": last("NextInspectionAtMeters"),
            "due": bool(last("InspectionDue")), "grade": last("LastGrade") or "", "label": last("Label") or ""})
    return {"available": True, "name": H.wear_name, "parts": out}


def _samples_enabled(m: Match, key: Optional[str]) -> List[float]:
    if not key:
        return []
    t, v = m.log.series(key)
    g0, dt, en = m.grid[0], m.dt, m.enabled
    out = []
    for ti, vi in zip(t, v):
        i = int(round((ti - g0) / dt))
        if 0 <= i < len(en) and en[i] and isinstance(vi, (int, float)):
            out.append(float(vi))
    return out


def _window_values(m: Match, key: Optional[str]) -> List[float]:
    """Values in the match window, plus the value already held when it began (AdvantageKit logs
    only changes, so a counter that never moved has no sample inside the window)."""
    if not key:
        return []
    t, v = m.log.series(key)
    lo, hi = m.grid[0], m.grid[-1]
    before = [x for ti, x in zip(t, v) if ti < lo][-1:]
    return [float(x) for x in before + [x for ti, x in zip(t, v) if lo <= ti <= hi] if isinstance(x, (int, float))]


def _can(m: Match, H) -> Dict[str, Any]:
    util = _samples_enabled(m, first_present(m.log, [H.can + "Utilization"]))
    if util and max(util) <= 1.0001:
        util = [u * 100 for u in util]                 # AdvantageKit logs a fraction
    counts = {}
    for name in ("ReceiveErrorCount", "TransmitErrorCount", "TxFullCount", "OffCount"):
        xs = _window_values(m, first_present(m.log, [H.can + name]))
        counts[name] = max(xs) if xs else None
    if not util and all(v is None for v in counts.values()):
        return {"available": False}
    return {"available": True, "util_median": statistics.median(util) if util else None,
            "util_p95": _pct(util, 0.95), "util_max": max(util) if util else None,
            "rx_errors_max": counts["ReceiveErrorCount"], "tx_errors_max": counts["TransmitErrorCount"],
            "tx_full_max": counts["TxFullCount"], "bus_off_max": counts["OffCount"]}


def _loop(m: Match, H) -> Dict[str, Any]:
    cyc = _samples_enabled(m, first_present(m.log, H.loop))
    if not cyc:
        return {"available": False}
    user = _samples_enabled(m, first_present(m.log, H.user_code))
    gc = _samples_enabled(m, first_present(m.log, H.gc))
    tgt = H.loop_target_ms
    return {"available": True, "target_ms": tgt, "cycles": len(cyc),
            "median_ms": statistics.median(cyc), "p95_ms": _pct(cyc, 0.95), "max_ms": max(cyc),
            "over_share": sum(1 for c in cyc if c > tgt) / len(cyc),
            "over_2x": sum(1 for c in cyc if c > 2 * tgt),
            "user_median_ms": statistics.median(user) if user else None,
            "gc_ms": sum(gc) if gc else None}


def analyze(m: Match) -> Dict[str, Any]:
    H = m.profile.health
    out = {"available": True, "devices": _devices(m, H), "wear": _wear(m, H), "can": _can(m, H),
           "loop": _loop(m, H)}
    out["observations"] = _match_observations(out)
    return out


def _match_observations(h: Dict[str, Any]) -> List[Dict[str, str]]:
    obs = []
    for f in h["devices"]["faults"]:
        sev = "act" if f["severity"] == "error" else "watch"
        times = f" {f['count']} times," if f["count"] > 1 else ""
        obs.append({"severity": sev, "rule": "device",
                    "text": f"{f['text'].rstrip('.')}:{times} {f['active_s']:.1f} s in all."})
    due = [p["part"] for p in h["wear"]["parts"] if p["due"]]
    if due:
        obs.append({"severity": "info", "rule": "wear-due",
                    "text": f"{h['wear']['name']} inspection due ({', '.join(due)}), as the robot reports it."})
    return obs


# ---------------------------------------------------------------- event

def match_summary(h: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not h or not h.get("available"):
        return None
    D, W, C, L = h["devices"], h["wear"], h["can"], h["loop"]
    return {
        "devices": D["available"], "checks": D["checks"],
        "faults": [{k: f[k] for k in ("key", "text", "severity", "active_s", "count")} for f in D["faults"]],
        "errors": sum(1 for f in D["faults"] if f["severity"] == "error"),
        "warnings": sum(1 for f in D["faults"] if f["severity"] == "warning"),
        "wear_name": W["name"],
        "wear": [{k: p[k] for k in ("part", "since_m", "match_m", "next_inspection_m", "due", "grade")}
                 for p in W["parts"]],
        "can_median": C.get("util_median"), "can_max": C.get("util_max"),
        "can_rx_max": C.get("rx_errors_max"), "can_tx_max": C.get("tx_errors_max"),
        "loop_median": L.get("median_ms"), "loop_p95": L.get("p95_ms"), "loop_max": L.get("max_ms"),
        "loop_over": L.get("over_share"), "loop_target": L.get("target_ms"), "user_median": L.get("user_median_ms"),
        "gc_ms": L.get("gc_ms"),
    }


def _median(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _mean(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def roll_up(ms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Event figures over the counted matches with health data (the device grid uses every match)."""
    S = [m["health"] for m in ms if m.get("health")]
    if not S:
        return {"n": 0}
    dev = [s for s in S if s["devices"]]
    return {
        "n": len(S), "device_n": len(dev),
        "with_errors": sum(1 for s in dev if s["errors"]),
        "with_errors_share": (sum(1 for s in dev if s["errors"]) / len(dev)) if dev else None,
        "loop_median": _median([s["loop_median"] for s in S]), "loop_p95": _median([s["loop_p95"] for s in S]),
        "loop_over": _mean([s["loop_over"] for s in S]), "loop_target": next((s["loop_target"] for s in S
                                                                              if s["loop_target"]), 20.0),
        "user_median": _median([s["user_median"] for s in S]),
        "can_median": _median([s["can_median"] for s in S]),
        "can_max": max([s["can_max"] for s in S if s["can_max"] is not None], default=None),
    }


METRICS = [
    ("with_errors_share", "Matches with a device error", "pct"),
    ("loop_median", "Loop time, median (ms)", 1),
    ("loop_p95", "Loop time, 95th percentile (ms)", 1),
    ("loop_over", "Loops over the target", "pct"),
    ("can_median", "CAN utilization, median (%)", 0),
]


def compare(cur: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not prev or not prev.get("n") or not cur.get("n"):
        return None
    rows = []
    for key, label, nd in METRICS:
        c, p = cur.get(key), prev.get(key)
        d = (c - p) if (c is not None and p is not None) else None
        rows.append({"key": key, "label": label, "nd": nd, "cur": c, "prev": p, "delta": d})
    return {"n": prev["n"], "rows": rows}


def observations(ev: Dict[str, Any]) -> List[Dict[str, str]]:
    """Device faults across every match (counted or not), recurring devices, wear, loop timing."""
    out: List[Dict[str, str]] = []

    def add(sev: str, rule: str, text: str) -> None:
        out.append({"severity": sev, "rule": rule, "text": text})

    ms = [m for m in ev["matches"] if m.get("health") and m["health"]["devices"]]
    by_key: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for m in ms:
        errs = [f for f in m["health"]["faults"] if f["severity"] == "error"]
        if len(errs) >= 4:
            longest = max(f["active_s"] for f in errs)
            add("act", "many-devices", f"{m['label']}: {len(errs)} devices off the bus in the same match "
                                       f"(up to {longest:.0f} s), which points at a bus or its wiring, not one device.")
        for f in m["health"]["faults"]:
            by_key.setdefault(f["key"], []).append((m["label"], f))
    for key, hits in by_key.items():
        f0 = hits[0][1]
        if len(hits) >= 2:
            sev = "act" if f0["severity"] == "error" else "watch"
            add(sev, "recurring", f"{f0['text'].rstrip('.')}: in {len(hits)} matches "
                                  f"({', '.join(lab for lab, _ in hits)}).")
        elif f0["severity"] == "error" and f0["active_s"] >= 1.0 and not any(
                o["rule"] == "many-devices" and o["text"].startswith(hits[0][0] + ":") for o in out):
            add("act", "device", f"{hits[0][0]}: {f0['text'].rstrip('.')} ({f0['active_s']:.0f} s).")
    last = next((m for m in reversed(sorted(ev["matches"], key=lambda m: m.get("stamp") or ""))
                 if m.get("health") and m["health"]["wear"]), None)
    if last:
        due = [w["part"] for w in last["health"]["wear"] if w["due"]]
        if due:
            add("info", "wear-due", f"{last['health']['wear_name']} inspection due on {', '.join(due)} "
                                    f"(as the robot reports it at {last['label']}).")
    H = ev.get("health") or {}
    A = H.get("averages") or {}
    if A.get("loop_median") is not None:
        add("info", "loop", f"Robot loop: median {A['loop_median']:.0f} ms, 95th percentile {A['loop_p95']:.0f} ms; "
                            f"{A['loop_over']:.0%} of loops over {A['loop_target']:.0f} ms.")
    C = H.get("compare")
    if C:
        name = ev["compare"]["name"] if ev.get("compare") else "the previous event"
        for r in C["rows"]:
            if r["key"] == "loop_p95" and r["delta"] is not None and abs(r["delta"]) >= 10:
                add("watch" if r["delta"] > 0 else "info", "compare-loop",
                    f"Loop time 95th percentile: {r['cur']:.0f} ms here, {r['prev']:.0f} ms at {name}.")
    order = {"act": 0, "watch": 1, "info": 2}
    out.sort(key=lambda o: order[o["severity"]])
    return out
