"""Self-contained HTML match report: one file, works offline, no external requests.

Charts are SVG drawn here in Python. A little inline JavaScript adds hover read-outs and
the Ah / Wh switch; everything is still readable with JavaScript off.
"""

import base64
import html
import json
import os
from typing import Any, Dict, List, Optional

# Categorical palette: validated (CVD-safe adjacent pairs) for stacked charts on white.
# A subsystem's `color` in the profile picks its slot, so it keeps its color in every report.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

NAVY = "#011945"
GOLD = "#f9a11b"
PAPER = "#fafafb"
INK_LINE = "#e1e5ed"
INK_MUTED = "#5a6479"
RED = "#bd1314"
AREA_BIN_S = 1.0   # the stacked area shows 1 s averages; the hover read-out shows 0.1 s

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _font_face() -> str:
    path = os.path.join(_FONT_DIR, "michroma-latin.woff2")
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return ""
    return ("@font-face{font-family:'Michroma';font-style:normal;font-weight:400;"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');}}")


def esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def color_for(slot: int) -> str:
    return SERIES[slot % len(SERIES)]


def f1(x: Optional[float], unit: str = "", nd: int = 1) -> str:
    if x is None:
        return "—"
    return f"{x:,.{nd}f}{(' ' + unit) if unit else ''}"


# ---------------------------------------------------------------- charts

def budget_svg(subs: List[Dict[str, Any]], unmeasured: List[Dict[str, Any]],
               field: str, unit: str, nd: int) -> str:
    rows = sorted(subs, key=lambda s: -s[field])
    total = sum(s[field] for s in subs)
    vmax = max([s[field] for s in rows] + [1e-9])
    label_w, val_w, row_h, bar_h, top = 220, 170, 34, 20, 8
    width = 1100
    plot_w = width - label_w - val_w - 16
    n_rows = len(rows) + len(unmeasured)
    height = top + n_rows * row_h + 6
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
           f'aria-label="Charge by subsystem in {unit}">']
    for k, s in enumerate(rows):
        y = top + k * row_h
        w = max(0.0, s[field]) / vmax * plot_w
        c = color_for(s["color"])
        share = s[field] / total if total else 0
        tip = f'{s["name"]}: {s[field]:.{nd}f} {unit} ({share:.0%})'
        out.append(f'<g class="hov"><title>{esc(tip)}</title>'
                   f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="transparent"/>'
                   f'<text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" class="lbl">{esc(s["name"])}</text>'
                   f'<rect x="{label_w}" y="{y + (row_h - bar_h) / 2}" width="{plot_w}" height="{bar_h}" fill="{INK_LINE}" opacity="0.35" rx="2"/>')
        if w > 0.5:
            out.append(f'<path d="{_bar_path(label_w, y + (row_h - bar_h) / 2, w, bar_h, 4)}" fill="{c}"/>')
        out.append(f'<text x="{label_w + plot_w + 12}" y="{y + row_h / 2 + 5}" class="val">'
                   f'<tspan class="num">{s[field]:.{nd}f} {unit}</tspan>'
                   f'<tspan class="muted"> · {share:.0%}</tspan></text></g>')
    for j, u in enumerate(unmeasured):
        y = top + (len(rows) + j) * row_h
        out.append(f'<g><title>{esc(u["name"])}: not measured</title>'
                   f'<text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" class="lbl">Control system</text>'
                   f'<rect x="{label_w + 0.75}" y="{y + (row_h - bar_h) / 2 + 0.75}" width="{plot_w - 1.5}" height="{bar_h - 1.5}" '
                   f'fill="none" stroke="{INK_MUTED}" stroke-dasharray="4 4" rx="2"/>'
                   f'<text x="{label_w + 12}" y="{y + row_h / 2 + 5}" class="muted small">'
                   f'roboRIO, radio, Limelights, CANivore, Pigeon</text>'
                   f'<text x="{label_w + plot_w + 12}" y="{y + row_h / 2 + 5}" class="muted">'
                   f'{"Not measured" if not u["estimate_amps"] else "≈ " + f1(u["ah"], "Ah", 2) + " est."}</text></g>')
    out.append("</svg>")
    return "".join(out)


def _bar_path(x: float, y: float, w: float, h: float, r: float) -> str:
    """Horizontal bar, square at the baseline (left), rounded at the data end (right)."""
    r = min(r, w, h / 2)
    return (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
            f"V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z")


def _nice_ticks(v: float) -> List[float]:
    """Round tick values from 0 to at least v, 4 to 6 steps."""
    for step in (5, 10, 20, 25, 50, 100, 200):
        k = -(-v // step)
        if k <= 6:
            return [step * i for i in range(int(k) + 1)]
    return [0, v]


def _rebin(t: List[float], vals: List[float], width: float) -> tuple:
    """Average `vals` into bins `width` seconds wide (for a readable stacked area)."""
    bt, bv, acc, cnt, edge = [], [], 0.0, 0, None
    for tt, v in zip(t, vals):
        if edge is None:
            edge = tt
        if tt - edge >= width and cnt:
            bt.append(edge + width / 2)
            bv.append(acc / cnt)
            acc, cnt, edge = 0.0, 0, tt
        acc += v
        cnt += 1
    if cnt:
        bt.append(edge + width / 2)
        bv.append(acc / cnt)
    return bt, bv


def timeline_svg(series: Dict[str, Any], subs: List[Dict[str, Any]]) -> str:
    t = series["t"]
    if not t:
        return ""
    width, left, right = 1100, 56, 16
    h1, h2, gap, top = 260, 110, 34, 10
    plot_w = width - left - right
    t_lo, t_hi = t[0], t[-1]

    def X(tt: float) -> float:
        return left + (tt - t_lo) / (t_hi - t_lo or 1) * plot_w

    order = sorted(subs, key=lambda s: -s["ah"])          # biggest at the bottom
    stacks = []
    ta = None
    base: List[float] = []
    for s in order:
        ta, vals = _rebin(t, [max(0.0, v) for v in series["subsystem_a"][s["id"]]], AREA_BIN_S)
        if not base:
            base = [0.0] * len(ta)
        upper = [b + v for b, v in zip(base, vals)]
        stacks.append((s, list(base), upper))
        base = upper
    ticks = _nice_ticks(max(base + [10.0]))
    amax = ticks[-1]

    def Y(a: float) -> float:
        return top + h1 - a / amax * h1

    y2_top = top + h1 + gap
    vmin, vmax = 5.0, 13.5

    def YV(v: float) -> float:
        v = min(max(v, vmin), vmax)
        return y2_top + h2 - (v - vmin) / (vmax - vmin) * h2

    out = [f'<svg viewBox="0 0 {width} {y2_top + h2 + 30}" class="chart" id="tl" role="img" '
           f'aria-label="Current by subsystem and battery voltage through the match">']
    # auto shading
    for b in series["blocks"]:
        if b["mode"] == "auto":
            out.append(f'<rect x="{X(b["start"]):.1f}" y="{top}" width="{X(b["end"]) - X(b["start"]):.1f}" '
                       f'height="{y2_top + h2 - top}" fill="{NAVY}" opacity="0.05"/>'
                       f'<text x="{X(b["start"]) + 6:.1f}" y="{top + 14}" class="muted small">AUTO</text>')
    # grid + axes
    for a in ticks:
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{Y(a):.1f}" y2="{Y(a):.1f}" stroke="{INK_LINE}"/>'
                   f'<text x="{left - 8}" y="{Y(a) + 4:.1f}" text-anchor="end" class="muted small">{a:.0f}</text>')
    out.append(f'<text x="{left}" y="{top - 0}" class="muted small" dy="-1">A</text>')
    # stacked areas
    for s, lo, hi in stacks:
        pts_hi = " ".join(f"{X(tt):.1f},{Y(v):.1f}" for tt, v in zip(ta, hi))
        pts_lo = " ".join(f"{X(tt):.1f},{Y(v):.1f}" for tt, v in reversed(list(zip(ta, lo))))
        out.append(f'<polygon points="{pts_hi} {pts_lo}" fill="{color_for(s["color"])}" '
                   f'stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>')
    # voltage panel
    for v in (8, 10, 12):
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{YV(v):.1f}" y2="{YV(v):.1f}" stroke="{INK_LINE}"/>'
                   f'<text x="{left - 8}" y="{YV(v) + 4:.1f}" text-anchor="end" class="muted small">{v}</text>')
    out.append(f'<text x="{left}" y="{y2_top - 6}" class="muted small">BATTERY V</text>')
    out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{YV(6.75):.1f}" y2="{YV(6.75):.1f}" '
               f'stroke="{RED}" stroke-dasharray="5 4"/>'
               f'<text x="{left - 8}" y="{YV(6.75) + 4:.1f}" text-anchor="end" class="small" fill="{RED}">6.75</text>')
    vpts = " ".join(f"{X(tt):.1f},{YV(v):.1f}" for tt, v in zip(t, series["vbat"]))
    out.append(f'<polyline points="{vpts}" fill="none" stroke="{NAVY}" stroke-width="1.5" stroke-linejoin="round"/>')
    for o in series["brownout_onsets"]:
        if t_lo <= o <= t_hi:
            out.append(f'<line x1="{X(o):.1f}" x2="{X(o):.1f}" y1="{y2_top + h2 - 8}" y2="{y2_top + h2}" '
                       f'stroke="{RED}" stroke-width="2"/>')
    # time axis
    t0 = int(t_lo // 20 * 20)
    for tt in range(t0, int(t_hi) + 1, 20):
        if tt < t_lo:
            continue
        out.append(f'<text x="{X(tt):.1f}" y="{y2_top + h2 + 18}" text-anchor="middle" class="muted small">{tt}s</text>')
    # hover layer
    out.append(f'<line id="tl-x" x1="0" x2="0" y1="{top}" y2="{y2_top + h2}" stroke="{NAVY}" stroke-width="1" visibility="hidden"/>'
               f'<rect id="tl-hit" x="{left}" y="{top}" width="{plot_w}" height="{y2_top + h2 - top}" fill="transparent"/>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- page

def render(result: Dict[str, Any], ident: Dict[str, Any], profile_name: str) -> str:
    T = result["totals"]
    B = result["brownout"]
    subs = result["subsystems"]
    title = f'{ident.get("event") or "Practice"} {ident["label"]}'.strip()
    ret = T["ah_returned"]

    if B["episodes"]:
        left = B["first_time_left_s"]
        bo_main = f"{left:.1f} s" if left is not None else f'{B["first_t"]:.0f} s in'
        bo_sub = f'left at first brownout · {B["episodes"]} episode{"s" if B["episodes"] != 1 else ""}'
    else:
        bo_main, bo_sub = "None", "no brownout this match"

    stats = [
        (f'{T["ah"]:.2f} Ah', "Charge used", f'{T["wh"]:.1f} Wh'),
        (f'{T["mean_a"]:.0f} A', "Mean current", f'{T["mean_w"]:,.0f} W mean power'),
        (f'{T["peak_1s_a"]:.0f} A', "Peak 1 s current", f'{T["p95_a"]:.0f} A 95th percentile'),
        (f'{T["min_v"]:.2f} V', "Lowest battery", f'{B["seconds_below_threshold"]:.1f} s below 6.75 V'),
        (bo_main, "Brownout", bo_sub),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="big">{esc(a)}</div><div class="lab">{esc(b)}</div>'
        f'<div class="sub">{esc(c)}</div></div>' for a, b, c in stats)

    legend = "".join(
        f'<span class="key"><i style="background:{color_for(s["color"])}"></i>{esc(s["name"])}</span>'
        for s in sorted(subs, key=lambda s: -s["ah"]))

    states = list(T["state_s"].keys())
    state_label = {"Other": "Still"}
    st_head = "".join(f'<th class="r">{esc(state_label.get(s, s))}<br><span class="muted small">'
                      f'{T["state_s"][s]:.0f} s</span></th>' for s in states)
    st_rows = []
    for s in sorted(subs, key=lambda s: -s["ah"]):
        cells = "".join(f'<td class="r num">{s["ah_by_state"][st]:.2f}</td>' for st in states)
        idle = f'{s["idle_ah"]:.2f}' if s["idle_ah"] is not None else "—"
        st_rows.append(f'<tr><td><i class="sw" style="background:{color_for(s["color"])}"></i>{esc(s["name"])}</td>'
                       f'{cells}<td class="r num">{s["ah"]:.2f}</td><td class="r num">{idle}</td></tr>')

    def prow(name: str, d: Dict[str, float]) -> str:
        return (f'<tr><td>{esc(name)}</td><td class="r num">{d["s"]:.0f} s</td><td class="r num">{d["ah"]:.2f}</td>'
                f'<td class="r num">{d["mean_a"]:.0f}</td><td class="r num">{d["mean_w"]:,.0f}</td></tr>')
    phase_rows = [prow("Auto", T["auto"]), prow("Teleop", T["teleop"])]
    phase_rows += [prow(f"Quarter {k + 1}", q) for k, q in enumerate(T["quarters"])]

    sub_name = {s["id"]: s["name"] for s in subs}
    sub_color = {s["id"]: s["color"] for s in subs}
    mrows = []
    for mo in sorted(result["motors"], key=lambda x: -x["ah"]):
        sat = f'{mo["saturated_share"]:.0%}' if mo["saturated_share"] is not None else "—"
        stall = f'{mo["stalled_ah"]:.3f}' if mo["stalled_ah"] is not None else "—"
        interval = mo["sample_interval_ms"]
        iv = "—" if interval is None else f'{interval:.0f}' + ("" if mo["peaks_reliable"] else ' <span class="flag">slow</span>')
        mrows.append(
            f'<tr><td><i class="sw" style="background:{color_for(sub_color[mo["subsystem"]])}"></i>{esc(mo["name"])}</td>'
            f'<td>{esc(sub_name[mo["subsystem"]])}</td><td class="r num">{esc(mo["can_id"] if mo["can_id"] is not None else "")}</td>'
            f'<td class="r num">{mo["ah"]:.3f}</td><td class="r num">{mo["ah_returned"]:.3f}</td>'
            f'<td class="r num">{mo["mean_a"]:.1f}</td><td class="r num">{mo["p95_a"]:.0f}</td>'
            f'<td class="r num">{mo["peak_1s_a"]:.0f}</td><td class="r num">{mo["active_s"]:.0f}</td>'
            f'<td class="r num">{sat}</td><td class="r num">{stall}</td><td class="r num">{iv}</td></tr>')

    sev_label = {"act": "Act", "watch": "Watch", "info": "Info"}
    obs = "".join(f'<li><span class="sev {o["severity"]}">{sev_label[o["severity"]]}</span>{esc(o["text"])}</li>'
                  for o in result["observations"]) or '<li class="muted">Nothing flagged.</li>'

    build = ident.get("git_sha", "")[:7]
    if build and ident.get("git_dirty"):
        build += " (uncommitted changes)"
    mode_note = ("Braking current is subtracted (signed supply current)."
                 if T["current_mode"] == "signed" else
                 "Braking current is counted as draw (absolute supply current), to match older hand analyses.")

    tl_data = json.dumps({
        "t": result["series"]["t"], "v": result["series"]["vbat"],
        "subs": [{"name": s["name"], "color": color_for(s["color"]), "a": result["series"]["subsystem_a"][s["id"]]}
                 for s in sorted(subs, key=lambda s: -s["ah"])],
        "total": result["series"]["total_a"],
    }, separators=(",", ":"))

    return PAGE.format(
        font=_font_face(), title=esc(title), event=esc(ident.get("event") or "Practice"),
        label=esc(ident["label"]), file=esc(ident["file"]), build=esc(build or "unknown"),
        branch=esc(ident.get("git_branch", "")), profile=esc(profile_name),
        stats=stat_html,
        budget_ah=budget_svg(subs, result["unmeasured"], "ah", "Ah", 2),
        budget_wh=budget_svg(subs, result["unmeasured"], "wh", "Wh", 1),
        returned=f"{ret:.2f}", legend=legend,
        timeline=timeline_svg(result["series"], subs),
        st_head=st_head, st_rows="".join(st_rows), phase_rows="".join(phase_rows),
        motor_rows="".join(mrows), observations=obs, mode_note=esc(mode_note),
        enabled_s=f'{T["enabled_s"]:.0f}', tl_data=tl_data,
        navy=NAVY, gold=GOLD, paper=PAPER, line=INK_LINE, muted=INK_MUTED, red=RED,
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Pitwall power report</title>
<style>
{font}
:root{{--navy:{navy};--gold:{gold};--paper:{paper};--line:{line};--muted:{muted};--red:{red}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--navy);font:15px/1.5 Arial,"Helvetica Neue",Helvetica,sans-serif}}
.disp{{font-family:Michroma,Eurostile,"Bank Gothic",sans-serif;font-weight:400}}
header{{background:var(--navy);border-bottom:3px solid var(--gold);color:#fafafb}}
header .in{{max-width:1200px;margin:0 auto;padding:14px 32px;display:flex;flex-wrap:wrap;gap:8px 24px;align-items:baseline;justify-content:space-between}}
header .brand{{font-family:Michroma,sans-serif;font-size:13px;letter-spacing:.14em}}
header .brand b{{color:var(--gold);font-weight:400}}
header .meta{{font-size:13px;color:#d3dae8}}
main{{max-width:1200px;margin:0 auto;padding:28px 32px 64px;display:flex;flex-direction:column;gap:24px}}
h1{{margin:0;font-family:Michroma,sans-serif;font-weight:400;font-size:30px;line-height:1.2}}
h2{{margin:0 0 4px;font-family:Michroma,sans-serif;font-weight:400;font-size:16px}}
.eyebrow{{margin:0 0 6px;font-family:Michroma,sans-serif;font-size:11px;letter-spacing:.22em;color:var(--red);text-transform:uppercase}}
.card{{background:#fff;border:1px solid var(--line);border-radius:6px;box-shadow:0 2px 6px rgba(1,25,69,.10);padding:22px 24px}}
.lead{{margin:0 0 14px;color:var(--muted);font-size:14px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px}}
.stat{{background:#fff;border:1px solid var(--line);border-top:4px solid var(--gold);border-radius:6px;padding:16px 18px}}
.stat .big{{font-size:26px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}}
.stat .lab{{font-family:Michroma,sans-serif;font-size:10px;letter-spacing:.14em;text-transform:uppercase;margin-top:6px}}
.stat .sub{{font-size:13px;color:var(--muted)}}
.chart{{width:100%;height:auto;display:block;font:13px Arial,sans-serif}}
.chart .lbl{{fill:var(--navy);font-weight:700}} .chart .val{{fill:var(--navy)}} .chart .num{{font-weight:700}}
.chart .muted{{fill:var(--muted)}} .chart .small{{font-size:11px}}
.chart .hov:hover rect:first-child{{fill:rgba(1,25,69,.04)}}
.row{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px}}
.seg{{display:inline-flex;border:2px solid var(--navy);border-radius:4px;overflow:hidden}}
.seg button{{min-height:36px;min-width:56px;border:0;background:#fff;color:var(--navy);font:12px Michroma,sans-serif;letter-spacing:.14em;cursor:pointer}}
.seg button[aria-pressed=true]{{background:var(--navy);color:#fafafb}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;margin:4px 0 10px}}
.key i,.sw{{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.tip{{position:fixed;pointer-events:none;background:#fff;border:1px solid var(--line);border-radius:4px;box-shadow:0 6px 18px rgba(1,25,69,.12);padding:8px 10px;font-size:12px;display:none;z-index:5;min-width:170px}}
.tip .r{{display:flex;justify-content:space-between;gap:12px}}
.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}
th{{text-align:left;font-family:Michroma,sans-serif;font-weight:400;font-size:10px;letter-spacing:.12em;text-transform:uppercase;padding:10px 10px;border-bottom:2px solid var(--navy);vertical-align:bottom}}
td{{padding:8px 10px;border-bottom:1px solid var(--line)}}
.r{{text-align:right}} .num{{font-variant-numeric:tabular-nums}}
.muted{{color:var(--muted)}} .small{{font-size:11px}}
.flag{{font-size:10px;font-family:Michroma,sans-serif;letter-spacing:.1em;color:#a96605;background:#fef0d8;border-radius:2px;padding:1px 5px;margin-left:4px}}
.obs{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}}
.obs li{{display:flex;gap:12px;align-items:flex-start;font-size:14px}}
.sev{{flex:0 0 64px;text-align:center;font-family:Michroma,sans-serif;font-size:10px;letter-spacing:.14em;text-transform:uppercase;border-radius:2px;padding:3px 0}}
.sev.act{{background:#fbeaea;color:var(--red)}} .sev.watch{{background:#fef0d8;color:#a96605}} .sev.info{{background:#edf0f6;color:#0b2a61}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:24px}}
footer{{font-size:12px;color:var(--muted);line-height:1.6}}
@media print{{.seg,.tip{{display:none!important}} .card{{box-shadow:none}}}}
</style>
</head>
<body>
<header><div class="in"><span class="brand">TJ² <b>PITWALL</b> · POWER REPORT</span>
<span class="meta">{file} · build {build} {branch} · profile {profile}</span></div></header>
<main>
<div><p class="eyebrow">{event}</p><h1>{label}</h1></div>
<section class="stats">{stats}</section>

<section class="card">
<div class="row"><div><h2>Where the charge went</h2>
<p class="lead">Each subsystem's share of the {enabled_s} s the robot was enabled. Braking returned {returned} Ah to the power bus; it's subtracted.</p></div>
<div class="seg" role="group" aria-label="Units"><button type="button" data-u="ah" aria-pressed="true">AH</button><button type="button" data-u="wh" aria-pressed="false">WH</button></div></div>
<div id="b-ah">{budget_ah}</div><div id="b-wh" hidden>{budget_wh}</div>
</section>

<section class="card">
<h2>Current through the match</h2>
<p class="lead">Supply current by subsystem (1 s averages), stacked, with battery voltage below. Red ticks mark brownouts. Hover for 0.1 s values.</p>
<div class="legend">{legend}</div>
{timeline}
</section>

<div class="grid2">
<section class="card"><h2>What the robot was doing</h2>
<p class="lead">Charge (Ah) by subsystem and robot state. Idle is charge used outside the states a subsystem is needed in.</p>
<div class="scroll"><table><thead><tr><th>Subsystem</th>{st_head}<th class="r">Total</th><th class="r">Idle</th></tr></thead>
<tbody>{st_rows}</tbody></table></div></section>
<section class="card"><h2>Through the match</h2>
<p class="lead">Quarters split the enabled time into four equal parts.</p>
<div class="scroll"><table><thead><tr><th>Period</th><th class="r">Time</th><th class="r">Ah</th><th class="r">Mean A</th><th class="r">Mean W</th></tr></thead>
<tbody>{phase_rows}</tbody></table></div></section>
</div>

<section class="card"><h2>Observations</h2><ul class="obs">{observations}</ul></section>

<section class="card"><h2>Every motor</h2>
<p class="lead">Peaks are as logged: a motor flagged "slow" reports too rarely to catch short spikes, but its charge is still right.</p>
<div class="scroll"><table><thead><tr><th>Motor</th><th>Subsystem</th><th class="r">CAN</th><th class="r">Ah</th><th class="r">Returned Ah</th>
<th class="r">Mean A</th><th class="r">95th pct A</th><th class="r">Peak 1 s A</th><th class="r">Active s</th><th class="r">Full output</th>
<th class="r">Stalled Ah</th><th class="r">Sample ms</th></tr></thead><tbody>{motor_rows}</tbody></table></div></section>

<footer>Charge and energy add up each motor's logged supply current over the time the robot was enabled, on a 20 ms grid; energy multiplies by battery voltage.
{mode_note} The control system (roboRIO, radio, cameras, CANivore, Pigeon) isn't logged, so totals are slightly low.
Made by Pitwall from {file}.</footer>
</main>
<div class="tip" id="tip"></div>
<script>
(function(){{
  document.querySelectorAll('.seg button').forEach(function(b){{
    b.addEventListener('click',function(){{
      var u=b.getAttribute('data-u');
      document.querySelectorAll('.seg button').forEach(function(x){{x.setAttribute('aria-pressed',x===b?'true':'false');}});
      document.getElementById('b-ah').hidden=(u!=='ah');document.getElementById('b-wh').hidden=(u!=='wh');
    }});
  }});
  var D={tl_data};
  var svg=document.getElementById('tl'),hit=document.getElementById('tl-hit'),line=document.getElementById('tl-x'),tip=document.getElementById('tip');
  if(!svg||!hit)return;
  function show(ev){{
    var r=hit.getBoundingClientRect(),f=(ev.clientX-r.left)/r.width;
    if(f<0||f>1){{hide();return;}}
    var i=Math.round(f*(D.t.length-1));
    var x=parseFloat(hit.getAttribute('x'))+f*parseFloat(hit.getAttribute('width'));
    line.setAttribute('x1',x);line.setAttribute('x2',x);line.setAttribute('visibility','visible');
    var h='<div class="r"><b>'+D.t[i].toFixed(1)+' s</b><span>'+D.v[i].toFixed(2)+' V</span></div>';
    h+='<div class="r"><span>Total</span><b>'+D.total[i].toFixed(0)+' A</b></div>';
    D.subs.forEach(function(s){{h+='<div class="r"><span><i class="sw" style="background:'+s.color+'"></i>'+s.name+'</span><span>'+s.a[i].toFixed(1)+' A</span></div>';}});
    tip.innerHTML=h;tip.style.display='block';
    var tx=ev.clientX+16,ty=ev.clientY+12;
    if(tx+tip.offsetWidth>window.innerWidth-8)tx=ev.clientX-tip.offsetWidth-16;
    if(ty+tip.offsetHeight>window.innerHeight-8)ty=window.innerHeight-tip.offsetHeight-8;
    tip.style.left=tx+'px';tip.style.top=ty+'px';
  }}
  function hide(){{tip.style.display='none';line.setAttribute('visibility','hidden');}}
  hit.addEventListener('mousemove',show);hit.addEventListener('mouseleave',hide);
}})();
</script>
</body>
</html>
"""
