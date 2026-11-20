"""Health sections of the match and event pages: device status, wear, CAN bus and loop timing.

Device status comes first and is the only loud part. Wear is shown as the robot reports it,
with "inspection due" kept to a quiet line (it stays on for many matches until an inspection
is recorded, and a loud message nobody acts on teaches people to ignore alerts). Brownouts
and battery sag are left to the Power tab.
"""

from typing import Any, Dict, List, Optional

from .event_html import fmt
from .html import INK_LINE, INK_MUTED, NAVY, SHORT, esc

ERR = "#bd1314"
WARN = "#c77700"
SEV_COLOR = {"error": ERR, "warning": WARN}

HEALTH_CSS = """
.hdev{list-style:none;margin:0;padding:0}
.hdev li{display:flex;gap:12px;align-items:baseline;padding:7px 0;border-bottom:1px solid #eef0f4}
.hdev .sevtag{font-family:Michroma,sans-serif;font-size:10px;letter-spacing:.12em;text-transform:uppercase;
  padding:2px 7px;border-radius:3px;color:#fff;flex:none}
.hdev .sevtag.error{background:#bd1314}.hdev .sevtag.warning{background:#c77700}
.hdev .txt{flex:1}.hdev .num{white-space:nowrap}
.allgood{font-size:18px;color:#1a7f37;font-weight:700}
.quiet{color:var(--muted);font-size:14px}
table.grid td.c{text-align:center;padding:4px 6px;font-size:12px}
table.grid th.c{text-align:center}
table.grid td.k{max-width:360px}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:28px}
@media (max-width:800px){.cols2{grid-template-columns:1fr}}
"""


def _p(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _m(meters: Optional[float]) -> str:
    return "—" if meters is None else (f"{meters / 1000:.2f} km" if meters >= 1000 else f"{meters:.0f} m")


def power_note() -> str:
    return ('<p class="quiet">Brownouts and battery sag are health checks too; they\'re on the '
            '<a href="#power">Power tab</a>.</p>')


# ---------------------------------------------------------------- match page

def health_tiles(h: Dict[str, Any]) -> List[tuple]:
    D, L, C = h["devices"], h["loop"], h["can"]
    if D["available"]:
        e = sum(1 for f in D["faults"] if f["severity"] == "error")
        w = len(D["faults"]) - e
        dev = (("All clear", "Device checks", f'{D["checks"]} checks passed') if not D["faults"] else
               (f"{e} error{'s' if e != 1 else ''}" if e else f"{w} warning{'s' if w != 1 else ''}", "Device checks",
                (f"{w} warning{'s' if w != 1 else ''} too" if e and w else f'of {D["checks"]} checks')))
    else:
        dev = ("—", "Device checks", "not logged by this robot code")
    loop = (f'{L["median_ms"]:.0f} ms', "Loop time, median",
            f'{L["over_share"]:.0%} of loops over {L["target_ms"]:.0f} ms') if L.get("available") else \
        ("—", "Loop time", "not logged")
    can = (f'{C["util_median"]:.0f}%', "CAN bus use, median", f'peak {C["util_max"]:.0f}%') \
        if C.get("available") and C.get("util_median") is not None else ("—", "CAN bus use", "not logged")
    return [dev, loop, can]


def fault_timeline_svg(faults: List[Dict[str, Any]], series: Dict[str, Any], max_rows: int = 16) -> str:
    """When each check was active, on the power timeline's time axis (same width and margins)."""
    t = series.get("t") or []
    if not t or not faults:
        return ""
    width, left, right, row, top = 1100, 56, 16, 16, 18
    plot_w = width - left - right
    t_lo, t_hi = t[0], t[-1]

    def X(tt: float) -> float:
        return left + (max(t_lo, min(t_hi, tt)) - t_lo) / (t_hi - t_lo or 1) * plot_w
    rows = faults[:max_rows]
    h = len(rows) * row
    out = [f'<svg viewBox="0 0 {width} {top + h + 26}" class="chart" role="img" '
           f'aria-label="When each health check was active through the match">']
    for per in series.get("periods") or []:
        out.append(f'<line x1="{X(per["start"]):.1f}" x2="{X(per["start"]):.1f}" y1="{top - 4}" y2="{top + h}" '
                   f'stroke="{INK_MUTED}" stroke-width="0.75" stroke-dasharray="2 3" opacity="0.6"/>'
                   f'<text x="{(X(per["start"]) + X(per["end"])) / 2:.1f}" y="{top - 6}" text-anchor="middle" '
                   f'class="muted small">{esc(SHORT.get(per["name"], per["name"]))}</text>')
    for b in series.get("blocks") or []:
        out.append(f'<rect x="{X(b["start"]):.1f}" y="{top}" width="{X(b["end"]) - X(b["start"]):.1f}" '
                   f'height="{h}" fill="{NAVY}" opacity="0.04"/>')
    for k, f in enumerate(rows):
        y = top + k * row
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{y + row:.1f}" y2="{y + row:.1f}" stroke="{INK_LINE}"/>')
        for a, b in f["spans"]:
            w = max(2.0, X(b) - X(a))
            out.append(f'<rect x="{X(a):.1f}" y="{y + 3}" width="{w:.1f}" height="{row - 6}" rx="2" '
                       f'fill="{SEV_COLOR[f["severity"]]}"><title>{esc(f["text"])}: {a:.1f}–{b:.1f} s</title></rect>')
        out.append(f'<text x="{left - 6}" y="{y + row - 4}" text-anchor="end" class="muted small">{k + 1}</text>')
    for v in range(0, int(t_hi) + 1, 30):
        if v >= t_lo:
            out.append(f'<text x="{X(v):.1f}" y="{top + h + 18}" text-anchor="middle" class="muted small">{v} s</text>')
    out.append("</svg>")
    more = (f'<p class="quiet">{len(faults) - max_rows} more not drawn; all are listed above.</p>'
            if len(faults) > max_rows else "")
    return "".join(out) + more


def _device_list(faults: List[Dict[str, Any]], numbered: bool = False) -> str:
    items = []
    for k, f in enumerate(faults):
        times = f' · {f["count"]} times' if f["count"] > 1 else ""
        items.append(f'<li><span class="sevtag {f["severity"]}">{"Error" if f["severity"] == "error" else "Warning"}</span>'
                     f'<span class="txt">{f"<b>{k + 1}.</b> " if numbered else ""}{esc(f["text"])}</span>'
                     f'<span class="num">{f["active_s"]:.1f} s{times}</span></li>')
    return f'<ul class="hdev">{"".join(items)}</ul>'


def _wear_html(W: Dict[str, Any], per_match: bool = True) -> str:
    if not W.get("available"):
        return ""
    rows = "".join(
        f'<tr><td>{esc(p["part"])}</td><td class="r num">{_m(p["since_m"])}</td>'
        + (f'<td class="r num">{_m(p["match_m"])}</td>' if per_match else "")
        + f'<td class="r num">{_m(p["next_inspection_m"])}</td><td>{esc(p["grade"] or "—")}</td></tr>'
        for p in W["parts"])
    due = [p["part"] for p in W["parts"] if p["due"]]
    note = (f'<p class="quiet">Inspection due on {esc(", ".join(due))}, as the robot reports it. It stays due until an '
            f'inspection is recorded on the robot.</p>' if due else "")
    head = ("<th>Part</th><th class=r>Since replacement</th>" + ("<th class=r>This match</th>" if per_match else "")
            + "<th class=r>Next inspection at</th><th>Last grade</th>")
    return (f'<section class="card"><h2>Wear: {esc(W["name"].lower())}</h2>'
            f'<p class="lead">Distance as the robot\'s wear tracker counts it.</p>'
            f'<div class="scroll"><table class="narrow"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
            f'{note}</section>')


def _can_loop_html(C: Dict[str, Any], L: Dict[str, Any]) -> str:
    parts = []
    if C.get("available"):
        errs = [f"receive errors up to {C['rx_errors_max']:.0f}" if C.get("rx_errors_max") else "",
                f"transmit errors up to {C['tx_errors_max']:.0f}" if C.get("tx_errors_max") else "",
                f"bus-off {C['bus_off_max']:.0f}" if C.get("bus_off_max") else ""]
        errs = [e for e in errs if e]
        parts.append(
            f'<div><h3>CAN bus (roboRIO)</h3><table class="narrow"><tbody>'
            f'<tr><td>Utilization, median</td><td class="r num">{_p(C.get("util_median"))}</td></tr>'
            f'<tr><td>95th percentile</td><td class="r num">{_p(C.get("util_p95"))}</td></tr>'
            f'<tr><td>Peak</td><td class="r num">{_p(C.get("util_max"))}</td></tr>'
            f'<tr><td>Error counters</td><td class="r">{esc(", ".join(errs) or "none")}</td></tr>'
            f'</tbody></table></div>')
    if L.get("available"):
        parts.append(
            f'<div><h3>Robot loop</h3><table class="narrow"><tbody>'
            f'<tr><td>Loop time, median</td><td class="r num">{L["median_ms"]:.1f} ms</td></tr>'
            f'<tr><td>95th percentile</td><td class="r num">{L["p95_ms"]:.1f} ms</td></tr>'
            f'<tr><td>Worst loop</td><td class="r num">{L["max_ms"]:.0f} ms</td></tr>'
            f'<tr><td>Loops over {L["target_ms"]:.0f} ms</td><td class="r num">{L["over_share"]:.0%}'
            f' <span class="muted small">({L["over_2x"]} over {2 * L["target_ms"]:.0f} ms)</span></td></tr>'
            f'<tr><td>Robot code, median</td><td class="r num">{fmt(L.get("user_median_ms"), 1, "ms")}</td></tr>'
            f'<tr><td>Garbage collection</td><td class="r num">{fmt(L.get("gc_ms"), 0, "ms")} in all</td></tr>'
            f'</tbody></table></div>')
    if not parts:
        return ""
    return (f'<section class="card"><h2>CAN bus and loop timing</h2><p class="lead">While the robot was enabled.</p>'
            f'<div class="cols2">{"".join(parts)}</div></section>')


def match_section(h: Optional[Dict[str, Any]], series: Dict[str, Any]) -> str:
    """The Health tab of a match page ('' if the profile has no [health] section)."""
    if not h:
        return ""
    D = h["devices"]
    if not D["available"]:
        dev = ('<section class="card"><h2>Device status</h2><p class="lead">This log has no health monitor data '
               '(robot code from before the health monitor). CAN bus and loop timing are below.</p></section>')
    elif not D["faults"]:
        dev = (f'<section class="card"><h2>Device status</h2><p class="allgood">All {D["checks"]} checks passed</p>'
               f'<p class="lead">No device left a bus and no CAN or camera warning came on while the robot was enabled.</p>'
               f'{power_note()}</section>')
    else:
        dev = (f'<section class="card"><h2>Device status</h2><p class="lead">The robot\'s own health checks that came on '
               f'while it was enabled, errors first, with how long each was on. The text is the robot\'s alert, which '
               f'names the device and its CAN ID.</p>{_device_list(D["faults"], numbered=True)}'
               f'<h3 style="margin-top:18px">When</h3>{fault_timeline_svg(D["faults"], series)}{power_note()}</section>')
    return dev + _wear_html(h["wear"]) + _can_loop_html(h["can"], h["loop"])


# ---------------------------------------------------------------- event page

def health_event_tiles(ev: Dict[str, Any]) -> List[tuple]:
    H = ev.get("health") or {}
    A = H.get("averages") or {}
    ms = [m for m in ev["matches"] if m.get("health")]
    dev = [m for m in ms if m["health"]["devices"]]
    with_err = [m["label"] for m in dev if m["health"]["errors"]]
    out = [(f"{len(with_err)} of {len(dev)}" if dev else "—", "Matches with a device error",
            (", ".join(with_err[:6]) + ("…" if len(with_err) > 6 else "")) if with_err else
            ("none" if dev else "not logged by this robot code"))]
    if A.get("loop_median") is not None:
        out.append((f'{A["loop_median"]:.0f} ms', "Loop time, median", f'95th percentile {A["loop_p95"]:.0f} ms'))
    if A.get("can_median") is not None:
        out.append((f'{A["can_median"]:.0f}%', "CAN bus use, median", f'peak {_p(A.get("can_max"))}'))
    return out


def _grid_html(ev: Dict[str, Any], links: Dict[str, str]) -> str:
    ms = [m for m in ev["matches"] if m.get("health") and m["health"]["devices"]]
    if not ms:
        return ""
    keys: Dict[str, Dict[str, Any]] = {}
    for m in ms:
        for f in m["health"]["faults"]:
            k = keys.setdefault(f["key"], {"text": f["text"], "severity": f["severity"], "n": 0, "s": 0.0})
            k["n"] += 1
            k["s"] += f["active_s"]
    if not keys:
        return (f'<section class="card"><h2>Device status</h2><p class="allgood">No device faults in {len(ms)} '
                f'match{"es" if len(ms) != 1 else ""}</p>{power_note()}</section>')
    order = sorted(keys, key=lambda k: (keys[k]["severity"] != "error", -keys[k]["n"], -keys[k]["s"]))
    head = "".join(f'<th class="c">{"<a href=" + chr(34) + esc(links[m["label"]]) + "#health" + chr(34) + ">" if m["label"] in links else ""}'
                   f'{esc(m["label"])}{"</a>" if m["label"] in links else ""}</th>' for m in ms)
    rows = []
    for k in order:
        cells = []
        for m in ms:
            f = next((x for x in m["health"]["faults"] if x["key"] == k), None)
            if f is None:
                cells.append('<td class="c"></td>')
                continue
            col = SEV_COLOR[f["severity"]]
            op = 0.25 + 0.75 * min(1.0, f["active_s"] / 30.0)
            cells.append(f'<td class="c" title="{esc(f["text"])}: {f["active_s"]:.1f} s" '
                         f'style="background:{col};color:#fff;opacity:{op:.2f}">'
                         f'{"&lt;1" if f["active_s"] < 0.5 else format(f["active_s"], ".0f")}</td>')
        tag = "Error" if keys[k]["severity"] == "error" else "Warning"
        rows.append(f'<tr><td class="k"><span class="sevtag {keys[k]["severity"]}" style="font-size:9px">{tag}</span> '
                    f'{esc(keys[k]["text"])}</td>{"".join(cells)}</tr>')
    return (f'<section class="card"><h2>Device status</h2><p class="lead">Each health check that came on while the robot '
            f'was enabled, by match (every match, counted or not). The number is seconds on; darker is longer. A row '
            f'with several matches is a device that keeps dropping out.</p>'
            f'<div class="scroll"><table class="list grid"><thead><tr><th>Check</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>{power_note()}</section>')


def _event_wear_html(ev: Dict[str, Any]) -> str:
    ms = sorted([m for m in ev["matches"] if m.get("health") and m["health"]["wear"]], key=lambda m: m.get("stamp") or "")
    if not ms:
        return ""
    first, last = ms[0]["health"], ms[-1]["health"]
    start = {w["part"]: w for w in first["wear"]}
    rows = []
    for w in last["wear"]:
        s = start.get(w["part"], {})
        s_m = (s.get("since_m") or 0) - (s.get("match_m") or 0) if s else None
        here = sum((x["match_m"] or 0) for m in ms for x in m["health"]["wear"] if x["part"] == w["part"])
        rows.append(f'<tr><td>{esc(w["part"])}</td><td class="r num">{_m(s_m)}</td><td class="r num">{_m(w["since_m"])}</td>'
                    f'<td class="r num">{_m(here)}</td><td class="r num">{_m(here / len(ms))}</td>'
                    f'<td class="r num">{_m(w["next_inspection_m"])}</td></tr>')
    due = [w["part"] for w in last["wear"] if w["due"]]
    note = (f'<p class="quiet">Inspection due on {esc(", ".join(due))} at {esc(ms[-1]["label"])}, as the robot reports '
            f'it. It stays due until an inspection is recorded on the robot.</p>' if due else "")
    return (f'<section class="card"><h2>Wear: {esc(last["wear_name"].lower())}</h2>'
            f'<p class="lead">Distance since replacement at the start and end of the event, as the robot\'s wear tracker '
            f'counts it, and how far each part went here.</p>'
            f'<div class="scroll"><table class="narrow"><thead><tr><th>Part</th><th class=r>At the start</th>'
            f'<th class=r>At the end</th><th class=r>This event</th><th class=r>Per match</th>'
            f'<th class=r>Next inspection at</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>{note}</section>')


def _event_timing_html(ev: Dict[str, Any], links: Dict[str, str]) -> str:
    ms = [m for m in ev["matches"] if m.get("health") and m["health"]["loop_median"] is not None]
    if not ms:
        return ""
    H = ev.get("health") or {}
    rows = "".join(
        f'<tr class="{"" if m["counted"] else "dim"}"><td>'
        + (f'<a href="{esc(links[m["label"]])}#health">{esc(m["label"])}</a>' if m["label"] in links else esc(m["label"]))
        + f'</td><td class="r num">{m["health"]["loop_median"]:.1f}</td><td class="r num">{m["health"]["loop_p95"]:.0f}</td>'
        f'<td class="r num">{m["health"]["loop_max"]:.0f}</td><td class="r num">{m["health"]["loop_over"]:.0%}</td>'
        f'<td class="r num">{_p(m["health"]["can_median"])}</td><td class="r num">{_p(m["health"]["can_max"])}</td></tr>'
        for m in ms)
    cmp_rows = ""
    C = H.get("compare")
    if C:
        name = ev["compare"]["name"] if ev.get("compare") else "Previous"

        def f(v, nd):
            return "—" if v is None else (f"{v:.0%}" if nd == "pct" else f"{v:.{nd}f}")
        cmp_rows = ('<h3 style="margin-top:18px">Compared with ' + esc(name) + '</h3><table class="narrow"><thead><tr>'
                    '<th></th><th class=r>Here</th><th class=r>' + esc(name) + '</th></tr></thead><tbody>'
                    + "".join(f'<tr><td>{esc(r["label"])}</td><td class="r num">{f(r["cur"], r["nd"])}</td>'
                              f'<td class="r num">{f(r["prev"], r["nd"])}</td></tr>' for r in C["rows"])
                    + '</tbody></table>')
    target = (H.get("averages") or {}).get("loop_target") or 20
    return (f'<section class="card"><h2>CAN bus and loop timing</h2><p class="lead">Per match, while enabled. '
            f'The robot loop is meant to run every {target:.0f} ms.</p>'
            f'<div class="scroll"><table class="narrow"><thead><tr><th>Match</th><th class=r>Loop median ms</th>'
            f'<th class=r>95th pct ms</th><th class=r>Worst ms</th><th class=r>Over {target:.0f} ms</th>'
            f'<th class=r>CAN median</th><th class=r>CAN peak</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'{cmp_rows}</section>')


def event_section(ev: Dict[str, Any], links: Dict[str, str]) -> str:
    """The Health tab of an event page ('' when the profile has no [health] section)."""
    if not ev.get("health"):
        return ""
    obs = (ev["health"].get("observations") or [])
    obs_html = "".join(f'<li><span class="sev {o["severity"]}">{o["severity"].capitalize()}</span>{esc(o["text"])}</li>'
                       for o in obs)
    return (_grid_html(ev, links) + _event_wear_html(ev) + _event_timing_html(ev, links)
            + (f'<section class="card"><h2>Notes</h2><ul class="obs">{obs_html}</ul></section>' if obs_html else ""))
