"""Scoring sections of the match and event pages.

Same rules as the rest of the reports: SVG drawn in Python, hover text in <title> elements,
readable with JavaScript off. Hub shots are blue, shuttle shots orange (mid) and purple (full)
everywhere, so a color means the same thing on every chart.
"""

import statistics
from typing import Any, Dict, List, Optional

from .event_html import PREV_GREY, _stack, fmt, fmt_delta, fmt_pct
from .html import INK_LINE, INK_MUTED, NAVY, SERIES, SHORT, _nice_ticks, esc

CATS = ("hub", "shuttle_mid", "shuttle_full")
CAT_NAME = {"hub": "Hub", "shuttle_mid": "Shuttle mid", "shuttle_full": "Shuttle full"}
CAT_COLOR = {"hub": SERIES[0], "shuttle_mid": SERIES[1], "shuttle_full": SERIES[6]}
MATCH_BIN_S = 2.0      # match page: shots per 2 s, on the same time axis as the power timeline
PREV_GREY_LIGHT = "#d9dee8"
EVENT_BIN_S = 5.0      # event page: shots per 5 s of match time left (shift edges are multiples of 5)

SECTION_CSS = """
.sect{border-top:3px solid var(--navy);padding-top:18px;margin-top:8px}
.sect h1{font-size:24px}
nav.jump{display:flex;gap:18px;font-family:Michroma,sans-serif;font-size:11px;letter-spacing:.14em;text-transform:uppercase}
nav.jump a{color:#0b4fa8;text-decoration:none} nav.jump a:hover{text-decoration:underline}
.tallscroll{max-height:440px;overflow-y:auto}
table.narrow{max-width:760px}
"""

METHOD_NOTE = ("Shots are counted from flywheel speed dips while the shoot command runs and the feeder gate is open. "
               "They are shots, not scored balls: misses count. Without video to check against, counts carry about "
               "±25% uncertainty; comparisons between matches and events are more reliable than the absolute numbers.")


def legend_html(cats=CATS) -> str:
    return "".join(f'<span class="key"><i style="background:{CAT_COLOR[c]}"></i>{CAT_NAME[c]}</span>' for c in cats)


def _stats(stats) -> str:
    return "".join(f'<div class="stat"><div class="big">{esc(a)}</div><div class="lab">{esc(b)}</div>'
                   f'<div class="sub">{esc(c)}</div></div>' for a, b, c in stats)


def _obs(items: List[Dict[str, str]]) -> str:
    sev_label = {"act": "Act", "watch": "Watch", "info": "Info"}
    return "".join(f'<li><span class="sev {o["severity"]}">{sev_label[o["severity"]]}</span>{esc(o["text"])}</li>'
                   for o in items)


def _bars_svg(bins: List[Dict[str, int]], x_of, bar_w: float, top: float, h: float, ymax: float,
              tip_of) -> str:
    """Stacked vertical bars (hub at the bottom). bins[k] = {cat: count}; x_of(k) = bar's left edge."""
    out = []
    for k, b in enumerate(bins):
        tot = sum(b.values())
        if not tot:
            continue
        y = top + h
        parts = []
        for c in CATS:
            v = b.get(c, 0)
            if not v:
                continue
            bh = v / ymax * h
            y -= bh
            parts.append(f'<rect x="{x_of(k):.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
                         f'fill="{CAT_COLOR[c]}" stroke="#ffffff" stroke-width="0.75"/>')
        out.append(f'<g><title>{esc(tip_of(k, b))}</title>{"".join(parts)}</g>')
    return "".join(out)


# ---------------------------------------------------------------- match page

def shots_timeline_svg(sc: Dict[str, Any], series: Dict[str, Any]) -> str:
    """Shots per 2 s through the match, on the power timeline's time axis (same width and margins)."""
    t = series.get("t") or []
    if not t:
        return ""
    width, left, right, top, h = 1100, 56, 16, 18, 170
    plot_w = width - left - right
    t_lo, t_hi = t[0], t[-1]

    def X(tt: float) -> float:
        return left + (tt - t_lo) / (t_hi - t_lo or 1) * plot_w

    nb = int((t_hi - t_lo) // MATCH_BIN_S) + 1
    bins: List[Dict[str, int]] = [dict() for _ in range(nb)]
    for s in sc["shots"]:
        k = int((s["t"] - t_lo) // MATCH_BIN_S)
        if 0 <= k < nb:
            bins[k][s["cat"]] = bins[k].get(s["cat"], 0) + 1
    ticks = _nice_ticks(max([sum(b.values()) for b in bins] + [5]))
    ticks = [x for x in range(0, int(ticks[-1]) + 1, 2 if ticks[-1] <= 12 else 5)]
    ymax = ticks[-1]

    def Y(v: float) -> float:
        return top + h - v / ymax * h

    out = [f'<svg viewBox="0 0 {width} {top + h + 30}" class="chart" role="img" '
           f'aria-label="Shots per 2 seconds through the match, by target">']
    for per in series.get("periods") or []:
        if per["hub"] is False:
            out.append(f'<rect x="{X(per["start"]):.1f}" y="{top}" width="{X(per["end"]) - X(per["start"]):.1f}" '
                       f'height="{h}" fill="{NAVY}" opacity="0.06"/>')
        out.append(f'<line x1="{X(per["start"]):.1f}" x2="{X(per["start"]):.1f}" y1="{top - 4}" y2="{top + h}" '
                   f'stroke="{INK_MUTED}" stroke-width="0.75" stroke-dasharray="2 3" opacity="0.6"/>'
                   f'<text x="{(X(per["start"]) + X(per["end"])) / 2:.1f}" y="{top - 6}" text-anchor="middle" '
                   f'class="muted small">{esc(SHORT.get(per["name"], per["name"]))}</text>')
    for v in ticks:
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{INK_LINE}"/>'
                   f'<text x="{left - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" class="muted small">{v}</text>')
    bw = MATCH_BIN_S / (t_hi - t_lo or 1) * plot_w

    def tip(k, b):
        a = t_lo + k * MATCH_BIN_S
        parts = ", ".join(f"{b[c]} {CAT_NAME[c].lower()}" for c in CATS if b.get(c))
        return f"{a:.0f}–{a + MATCH_BIN_S:.0f} s: {parts}"
    out.append(_bars_svg(bins, lambda k: X(t_lo + k * MATCH_BIN_S) + 0.5, max(1.0, bw - 1), top, h, ymax, tip))
    t0 = int(t_lo // 20 * 20)
    for tt in range(t0, int(t_hi) + 1, 20):
        if tt >= t_lo:
            out.append(f'<text x="{X(tt):.1f}" y="{top + h + 18}" text-anchor="middle" class="muted small">{tt}s</text>')
    out.append("</svg>")
    return "".join(out)


def scoring_tiles(sc: Dict[str, Any]) -> List[tuple]:
    """The match's headline scoring numbers (also used on the Overview tab)."""
    C, R = sc["counts"], sc["rates"]
    shuttle = C["shuttle_mid"]["total"] + C["shuttle_full"]["total"]
    return [
        (str(C["hub"]["total"]), "Hub shots", f'{C["hub"]["auto"]} in auto · {C["hub"]["teleop"]} in teleop'),
        (str(shuttle), "Shuttle shots", f'{C["shuttle_mid"]["total"]} mid · {C["shuttle_full"]["total"]} full'),
        (fmt(R["hub"]["bps_flow"], 2), "Hub BPS while flowing", f'{fmt(R["hub"]["bps_trigger"], 2)} per trigger second'),
    ]


def match_section(sc: Optional[Dict[str, Any]], series: Dict[str, Any], head: bool = True) -> str:
    """The Scoring section of a match page ('' if the profile has no [scoring] section).
    head=False leaves out the section heading (the page's Scoring tab names it)."""
    if not sc:
        return ""
    head = ('<div class="sect" id="scoring"><p class="eyebrow">Scoring</p><h1>Shots</h1></div>') if head else ""
    if not sc.get("available"):
        return head + f'<section class="card"><p class="lead">{esc(sc.get("why", "No scoring data."))}</p></section>'
    C, R = sc["counts"], sc["rates"]
    sizes = [b["balls"] for b in sc["bursts"] if b["balls"] >= 2]
    trig = sc["trigger_s"]
    stats = scoring_tiles(sc)[:2] + [
        (fmt(R["hub"]["bps_flow"], 2), "Hub BPS while flowing", f'{fmt(R["hub"]["bps_trigger"], 2)} per trigger second'),
        (fmt(R["shuttle"]["bps_flow"], 2), "Shuttle BPS while flowing",
         f'{fmt(R["shuttle"]["bps_trigger"], 2)} per trigger second'),
        (f'{sum(trig.values()):.0f} s', "Trigger time",
         f'{R["hub"]["trigger_s"]:.0f} s hub · {R["shuttle"]["trigger_s"]:.0f} s shuttle · {len(sizes)} bursts'
         + (f' (median {statistics.median(sizes):.0f} balls)' if sizes else "")),
    ]

    # by period
    rows = []
    if sc["periods"]:
        def hub_txt(h):
            return "Active" if h else ("Inactive" if h is False else "—")
        for p in sc["periods"]:
            if p["s"] <= 0:
                continue
            rows.append(f'<tr><td>{esc(p["name"])}</td><td>{hub_txt(p["hub_active"])}</td>'
                        f'<td class="r num">{p["hub"]}</td><td class="r num">{p["shuttle_mid"]}</td>'
                        f'<td class="r num">{p["shuttle_full"]}</td><td class="r num">{p["trigger_s"]:.0f}</td></tr>')
    # With game periods, Auto is already a row: add Teleop and Match totals. Without, all three.
    for k, (lab, part) in enumerate((("Auto", "auto"), ("Teleop", "teleop"), ("Match", "total"))):
        if sc["periods"] and part == "auto":
            continue
        cls = "sum" if (k == 1 and sc["periods"]) or part == "total" else ""
        trig_txt = f'{sum(trig.values()):.0f}' if part == "total" else ""
        rows.append(f'<tr class="{cls}"><td colspan="2">{lab}</td><td class="r num">{C["hub"][part]}</td>'
                    f'<td class="r num">{C["shuttle_mid"][part]}</td><td class="r num">{C["shuttle_full"][part]}</td>'
                    f'<td class="r num">{trig_txt}</td></tr>')

    # bursts
    brows = []
    mixed = ' <span class="muted small">(mixed)</span>'
    for k, b in enumerate([b for b in sc["bursts"] if b["balls"] >= 2]):
        left = f'{b["left"]:.0f}' if b["left"] is not None else "—"
        brows.append(f'<tr><td class="r num">{k + 1}</td><td class="r num">{b["t"]:.1f}</td><td class="r num">{left}</td>'
                     f'<td><i class="sw" style="background:{CAT_COLOR[b["cat"]]}"></i>{CAT_NAME[b["cat"]]}'
                     f'{mixed if b["mixed"] else ""}</td>'
                     f'<td class="r num">{b["balls"]}</td><td class="r num">{b["dur_s"]:.1f}</td>'
                     f'<td class="r num">{fmt(b["bps"], 2)}</td><td class="r num">{fmt(b["gap_next_s"], 1)}</td></tr>')
    singles = sum(1 for b in sc["bursts"] if b["balls"] < 2)
    obs = _obs(sc.get("observations", []))
    single_note = (f" {singles} single shot{'s' if singles != 1 else ''} not listed." if singles else "")
    notes_card = f'<section class="card"><h2>Scoring notes</h2><ul class="obs">{obs}</ul></section>' if obs else ""
    return f"""{head}
<section class="stats">{_stats(stats)}</section>
<section class="card"><h2>Shots through the match</h2>
<p class="lead">Shots per 2 s, by what the robot was aiming at, on the same time axis as the power chart. Shaded shifts are the ones where our hub was inactive.</p>
<div class="legend">{legend_html()}</div>
{shots_timeline_svg(sc, series)}
</section>
<section class="card"><h2>Shots by period</h2>
<p class="lead">Shots in each part of the match, and how long the shoot command ran (trigger seconds).</p>
<div class="scroll"><table class="narrow"><thead><tr><th>Period</th><th>Our hub</th><th class="r">Hub</th><th class="r">Shuttle mid</th>
<th class="r">Shuttle full</th><th class="r">Trigger s</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
<section class="card"><h2>Bursts</h2>
<p class="lead">One row per hopper dump: shots less than {sc["settings"]["burst_gap_s"]:g} s apart. BPS is shown for bursts of 4 or more balls.{single_note}</p>
<div class="scroll tallscroll"><table><thead><tr><th class="r">#</th><th class="r">Start s</th><th class="r">Time left</th><th>Target</th>
<th class="r">Balls</th><th class="r">Seconds</th><th class="r">BPS</th><th class="r">Gap to next s</th></tr></thead>
<tbody>{"".join(brows) or '<tr><td colspan="8" class="muted">No bursts.</td></tr>'}</tbody></table></div></section>
{notes_card}
<p class="muted small">{esc(METHOD_NOTE)}</p>"""


# ---------------------------------------------------------------- event page

def shots_by_match_svg(ev: Dict[str, Any], links: Dict[str, str]) -> str:
    S = ev["scoring"]
    A = S["averages"]
    rows = [m for m in ev["matches"] if m.get("scoring")]
    avg_rows = []
    if A.get("n"):
        avg_rows.append(("avg", "Event average", {c: A[c] or 0.0 for c in CATS}))
    PC = S.get("compare")
    if PC:
        prev_tot = sum((PC["averages"].get(c) or 0.0) for c in CATS)
        avg_rows.append(("prev", f'{ev["compare"]["name"]} average' if ev.get("compare") else "Previous event",
                         {"total": prev_tot}))
    tot = [sum(m["scoring"][c] for c in CATS) for m in rows]
    vmax = max(tot + [sum(r[2].get(c, 0) for c in CATS) or r[2].get("total", 0) for r in avg_rows] + [1])
    label_w, val_w, row_h, bar_h, top, gap = 150, 300, 30, 18, 6, 18
    width = 1100
    plot_w = width - label_w - val_w - 16
    scale = plot_w / vmax
    height = top + len(rows) * row_h + (gap + len(avg_rows) * row_h if avg_rows else 0) + 8
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Shots in each match, by target">']
    y = top
    for m, t in zip(rows, tot):
        s = m["scoring"]
        op = 1.0 if m["counted"] else 0.35
        parts = [(s[c], CAT_COLOR[c], f'{m["label"]} {CAT_NAME[c].lower()}: {s[c]} shots') for c in CATS]
        href = links.get(m["label"])
        lab = esc(m["label"])
        out.append(f'<a href="{esc(href)}#scoring"><text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" '
                   f'class="lbl link">{lab}</text></a>' if href else
                   f'<text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" class="lbl">{lab}</text>')
        out.append(_stack(label_w, y + (row_h - bar_h) / 2, bar_h, parts, scale, op))
        note = "" if m["counted"] else '<tspan class="muted"> · not counted</tspan>'
        out.append(f'<text x="{label_w + plot_w + 12}" y="{y + row_h / 2 + 5}" class="val"><tspan class="num">'
                   f'{s["hub"]} hub</tspan><tspan class="muted"> · {s["shuttle"]} shuttle</tspan>{note}</text>')
        y += row_h
    if avg_rows:
        out.append(f'<line x1="0" x2="{width}" y1="{y + gap / 2:.1f}" y2="{y + gap / 2:.1f}" stroke="{INK_LINE}"/>')
        y += gap
        for kind, label, vals in avg_rows:
            if kind == "avg":
                parts = [(vals[c], CAT_COLOR[c], f'Average {CAT_NAME[c].lower()}: {vals[c]:.0f} shots') for c in CATS]
                bar = _stack(label_w, y + (row_h - bar_h) / 2, bar_h, parts, scale)
                txt = (f'<tspan class="num">{vals["hub"]:.0f} hub</tspan><tspan class="muted"> · '
                       f'{vals["shuttle_mid"] + vals["shuttle_full"]:.0f} shuttle</tspan>')
                name = "Event average"
            else:
                ph, ps = PC["averages"].get("hub") or 0.0, PC["averages"].get("shuttle") or 0.0
                bar = _stack(label_w, y + (row_h - bar_h) / 2, bar_h,
                             [(ph, PREV_GREY, f'{label}: {ph:.0f} hub shots'),
                              (ps, PREV_GREY_LIGHT, f'{label}: {ps:.0f} shuttle shots')], scale)
                txt = (f'<tspan class="num">{ph:.0f} hub</tspan><tspan class="muted"> · {ps:.0f} shuttle · '
                       f'{esc(label)}</tspan>')
                name = "Previous event"
            out.append(f'<text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" class="lbl">{name}</text>'
                       f'{bar}<text x="{label_w + plot_w + 12}" y="{y + row_h / 2 + 5}" class="val">{txt}</text>')
            y += row_h
    out.append("</svg>")
    return "".join(out)


def shots_strip_svg(ev: Dict[str, Any], shots_by_label: Dict[str, List[Dict[str, Any]]],
                    links: Dict[str, str]) -> str:
    """One row per FMS match: shots per 5 s of match time left, lined up like the brownout chart."""
    total = ev["auto_s"] + ev["teleop_s"]
    ms = [m for m in ev["matches"] if m.get("scoring") and m["is_fms"]]
    if not ms:
        return ""
    label_w, right_w, row_h, top = 150, 290, 40, 22
    width = 1100
    plot_w = width - label_w - right_w - 16
    height = top + len(ms) * row_h + 30
    nb = int(total // EVENT_BIN_S)

    def X(left: float) -> float:
        return label_w + (total - left) / total * plot_w

    peak = 1
    binned = {}
    for m in ms:
        bins = [dict() for _ in range(nb)]
        for s in shots_by_label.get(m["label"], []):
            if s.get("left") is None:
                continue
            k = min(nb - 1, max(0, int((total - s["left"]) // EVENT_BIN_S)))
            bins[k][s["cat"]] = bins[k].get(s["cat"], 0) + 1
        binned[m["label"]] = bins
        peak = max([peak] + [sum(b.values()) for b in bins])
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
           f'aria-label="Shots through each match, by match time left">']
    pinfo = ev.get("period_info") or []
    edges = sorted({p["from_left"] for p in pinfo} | {p["to_left"] for p in pinfo}, reverse=True) if pinfo \
        else list(range(int(total), -1, -20))
    for p in pinfo:
        out.append(f'<text x="{(X(p["from_left"]) + X(p["to_left"])) / 2:.1f}" y="{top - 8}" text-anchor="middle" '
                   f'class="muted small">{esc(SHORT.get(p["name"], p["name"]))}</text>')
    for left in edges:
        out.append(f'<line x1="{X(left):.1f}" x2="{X(left):.1f}" y1="{top - 4}" y2="{top + len(ms) * row_h}" '
                   f'stroke="{INK_LINE}"/><text x="{X(left):.1f}" y="{top + len(ms) * row_h + 16}" '
                   f'text-anchor="middle" class="muted small">{left:g}</text>')
    out.append(f'<text x="{label_w + plot_w}" y="{top + len(ms) * row_h + 28}" text-anchor="end" '
               f'class="muted small">seconds left in the match</text>')
    pbn = {p["name"]: p for p in pinfo}
    bw = EVENT_BIN_S / total * plot_w
    for k, m in enumerate(ms):
        y0 = top + k * row_h
        op = 1.0 if m["counted"] else 0.4
        for per in m["scoring"]["periods"]:
            if per["hub_active"] is False and per["name"] in pbn:
                p = pbn[per["name"]]
                out.append(f'<rect x="{X(p["from_left"]):.1f}" y="{y0 + 1}" width="{X(p["to_left"]) - X(p["from_left"]):.1f}" '
                           f'height="{row_h - 2}" fill="{NAVY}" opacity="0.07"/>')
        href = links.get(m["label"])
        lab = esc(m["label"])
        out.append(f'<a href="{esc(href)}#scoring"><text x="{label_w - 12}" y="{y0 + row_h / 2 + 5}" text-anchor="end" '
                   f'class="lbl link">{lab}</text></a>' if href else
                   f'<text x="{label_w - 12}" y="{y0 + row_h / 2 + 5}" text-anchor="end" class="lbl">{lab}</text>')

        def tip(j, b, label=m["label"]):
            hi = total - j * EVENT_BIN_S
            parts = ", ".join(f"{b[c]} {CAT_NAME[c].lower()}" for c in CATS if b.get(c))
            return f"{label}, {hi:.0f}–{hi - EVENT_BIN_S:.0f} s left: {parts}"
        out.append(f'<g opacity="{op}">'
                   + _bars_svg(binned[m["label"]], lambda j: X(total - j * EVENT_BIN_S) + 1, max(1.0, bw - 2),
                               y0 + 4, row_h - 8, peak, tip) + '</g>')
        s = m["scoring"]
        out.append(f'<text x="{label_w + plot_w + 12}" y="{y0 + row_h / 2 + 5}" class="val"><tspan class="num">'
                   f'{s["hub"]} hub</tspan><tspan class="muted"> · {s["shuttle"]} shuttle</tspan></text>')
        out.append(f'<line x1="{label_w}" x2="{label_w + plot_w}" y1="{y0 + row_h}" y2="{y0 + row_h}" '
                   f'stroke="{INK_LINE}" opacity="0.6"/>')
    out.append("</svg>")
    return "".join(out)


def _shift_table(ev: Dict[str, Any]) -> str:
    S = ev["scoring"]
    A, PC = S["averages"], S.get("compare")
    prev = (PC or {}).get("periods") or {}
    pname = ev["compare"]["name"] if (PC and ev.get("compare")) else "Previous"
    head = ('<th>Period</th><th class="r">Hub</th><th class="r">Hub,<br>our hub active</th>'
            '<th class="r">Hub,<br>our hub inactive</th><th class="r">Shuttle mid</th><th class="r">Shuttle full</th>'
            '<th class="r">Trigger s</th>')
    if PC:
        head += f'<th class="r">{esc(pname)}<br>hub</th><th class="r">Change</th>'

    def cnt(v, k):
        return "—" if v is None or not k else f'{v:.0f} <span class="muted small">({k})</span>'
    rows = []
    for x in A["periods"]:
        cells = f'<td>{esc(x["name"])}</td><td class="r num">{fmt(x["hub"], 0)}</td>'
        if x["conditional"]:
            cells += (f'<td class="r num">{cnt(x["hub_when_active"], x["active_n"])}</td>'
                      f'<td class="r num">{cnt(x["hub_when_inactive"], x["inactive_n"])}</td>')
        else:
            cells += '<td class="r muted small">always active</td><td></td>'
        cells += (f'<td class="r num">{fmt(x["shuttle_mid"], 0)}</td><td class="r num">{fmt(x["shuttle_full"], 0)}</td>'
                  f'<td class="r num">{fmt(x["trigger_s"], 0)}</td>')
        if PC:
            q = prev.get(x["name"])
            pv = q["hub"] if q else None
            d = (x["hub"] - pv) if (pv is not None and x["hub"] is not None) else None
            cells += (f'<td class="r num">{fmt(pv, 0)}</td><td class="r num">{fmt_delta(d, 0)} '
                      f'<span class="muted">{fmt_pct(d / pv if d is not None and pv else None)}</span></td>')
        rows.append(f"<tr>{cells}</tr>")
    if A.get("per_active_shift") is not None:
        cells = (f'<td>Per alliance shift</td><td></td><td class="r num">{fmt(A["per_active_shift"], 0)}</td>'
                 f'<td class="r num">{fmt(A.get("per_inactive_shift"), 1)}</td><td></td><td></td><td></td>')
        if PC:
            cells += "<td></td><td></td>"
        rows.append(f'<tr class="sum">{cells}</tr>')
    return (f'<section class="card"><h2>Shots by shift</h2><p class="lead">Average per counted match. In the shifts where '
            f'our hub can be active or inactive, the next two columns split the matches by which it was (number of '
            f'matches in brackets).</p><div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def scoring_event_tiles(ev: Dict[str, Any]) -> List[tuple]:
    """The event's headline scoring numbers, with the change from the previous event."""
    S = ev["scoring"]
    A, PC = S["averages"], S.get("compare")
    pname = ev["compare"]["name"] if (PC and ev.get("compare")) else ""
    prow = {r["key"]: r for r in PC["rows"]} if PC else {}

    def vs(key, nd):
        r = prow.get(key)
        return f'{fmt_delta(r["delta"], nd)} vs {pname}' if r and r["delta"] is not None else ""
    return [
        (fmt(A["hub"], 0), "Hub shots per match", vs("hub", 0) or f'mean · median {fmt(A["hub_median"], 0)}'),
        (fmt(A["shuttle"], 0), "Shuttle shots per match",
         f'{fmt(A["shuttle_mid"], 0)} mid · {fmt(A["shuttle_full"], 0)} full'),
        (fmt(A["bps_flow_hub"], 2), "Hub BPS while flowing", vs("bps_flow_hub", 2) or "median"),
    ]


def event_section(ev: Dict[str, Any], shots_by_label: Dict[str, List[Dict[str, Any]]],
                  links: Dict[str, str], head: bool = True) -> str:
    """The Scoring section of an event page ('' if no match has scoring data).
    head=False leaves out the section heading (the page's Scoring tab names it)."""
    S = ev.get("scoring")
    if not S or not any(m.get("scoring") for m in ev["matches"]):
        return ""
    A, PC = S["averages"], S.get("compare")
    n = A.get("n", 0)
    pname = ev["compare"]["name"] if (PC and ev.get("compare")) else ""
    prow = {r["key"]: r for r in PC["rows"]} if PC else {}

    def vs(key, nd):
        r = prow.get(key)
        return f'{fmt_delta(r["delta"], nd)} vs {pname}' if r and r["delta"] is not None else ""

    head = '<div class="sect" id="scoring"><p class="eyebrow">Scoring</p><h1>Shots</h1></div>' if head else ""
    if not n:
        return head + '<section class="card"><p class="lead">No counted match has scoring data.</p></section>'
    stats = [
        (fmt(A["hub"], 0), "Hub shots per match", vs("hub", 0) or f'mean · median {fmt(A["hub_median"], 0)} · {n} matches'),
        (fmt(A["hub_auto"], 0), "Hub shots in auto", vs("hub_auto", 0) or f'mean · {fmt(A["hub_teleop"], 0)} in teleop'),
        (fmt(A["shuttle"], 0), "Shuttle shots per match",
         f'{fmt(A["shuttle_mid"], 0)} mid · {fmt(A["shuttle_full"], 0)} full · {fmt(A["bps_flow_shuttle"], 2)} BPS flowing'),
        (fmt(A["bps_flow_hub"], 2), "Hub BPS while flowing", vs("bps_flow_hub", 2) or
         f'median · {fmt(A["bps_trigger_hub"], 2)} per trigger second'),
        (fmt(A["trigger_s"], 0, "s"), "Trigger time per match", vs("trigger_s", 0) or
         f'{fmt(A["hub_trigger_s"], 0, "s")} hub · {fmt(A["shuttle_trigger_s"], 0, "s")} shuttle'),
    ]
    legend = legend_html()
    if PC:
        legend += f'<span class="key"><i style="background:{PREV_GREY}"></i>{esc(pname)} average</span>'

    compare_html = ""
    if PC:
        crow = "".join(
            f'<tr><td>{esc(r["label"])}</td><td class="r num">{fmt(r["cur"], r["nd"])}</td>'
            f'<td class="r num">{fmt(r["prev"], r["nd"])}</td><td class="r num">{fmt_delta(r["delta"], r["nd"])} '
            f'<span class="muted">{fmt_pct(r["pct"])}</span></td></tr>' for r in PC["rows"])
        compare_html = (f'<section class="card"><h2>Compared with {esc(pname)}</h2><p class="lead">Per counted match: '
                        f'{n} here, {PC["n"]} at {esc(pname)}. Counts are averages; rates are medians.</p>'
                        f'<div class="scroll"><table class="narrow"><thead><tr><th>Average</th><th class="r">Here</th>'
                        f'<th class="r">{esc(pname)}</th><th class="r">Change</th></tr></thead><tbody>{crow}</tbody>'
                        f'</table></div></section>')

    mrows = []
    for m in ev["matches"]:
        s = m.get("scoring")
        if not s:
            continue
        href = links.get(m["label"])
        lab = f'<a href="{esc(href)}#scoring">{esc(m["label"])}</a>' if href else esc(m["label"])
        status = "Counted" if m["counted"] else f'<span class="muted">{esc(m["why_not"])}</span>'
        mrows.append(
            f'<tr class="{"" if m["counted"] else "dim"}"><td>{lab}</td><td class="r num">{s["hub"]}</td>'
            f'<td class="r num">{s["hub_auto"]}</td><td class="r num">{s["hub_teleop"]}</td>'
            f'<td class="r num">{s["shuttle_mid"]}</td><td class="r num">{s["shuttle_full"]}</td>'
            f'<td class="r num">{fmt(s["bps_flow_hub"], 2)}</td><td class="r num">{fmt(s["bps_trigger_hub"], 2)}</td>'
            f'<td class="r num">{s["trigger_s"]:.0f}</td><td class="r num">{s["bursts"]}</td>'
            f'<td class="r num">{fmt(s["burst_median"], 0)}</td><td>{status}</td></tr>')
    obs = _obs(S.get("observations", [])) or '<li class="muted">Nothing flagged.</li>'
    strip = shots_strip_svg(ev, shots_by_label, links)
    strip_card = "" if not strip else (
        '<section class="card"><h2>Shots through each match</h2><p class="lead">Shots per 5 s, lined up by match '
        'time left. Grey shading marks the shifts where our hub was inactive. Hover a bar for its counts.</p>'
        f'<div class="legend">{legend_html()}</div>{strip}</section>')
    shift_card = _shift_table(ev) if A.get("periods") else ""
    return f"""{head}
<section class="stats">{_stats(stats)}</section>
<section class="card"><h2>Shots by match</h2>
<p class="lead">Shots in each match, by what the robot was aiming at. Faded matches aren't counted in the averages (same rules as power). Click a match for its full report.</p>
<div class="legend">{legend}</div>
{shots_by_match_svg(ev, links)}
</section>
{strip_card}
{shift_card}
{compare_html}
<section class="card"><h2>Shots match by match</h2>
<div class="scroll"><table><thead><tr><th>Match</th><th class="r">Hub</th><th class="r">Hub auto</th><th class="r">Hub teleop</th>
<th class="r">Shuttle mid</th><th class="r">Shuttle full</th><th class="r">Hub BPS<br>flowing</th><th class="r">Hub BPS<br>per trigger s</th>
<th class="r">Trigger s</th><th class="r">Bursts</th><th class="r">Balls per<br>burst</th><th>Averages</th></tr></thead>
<tbody>{"".join(mrows)}</tbody></table></div></section>
<section class="card"><h2>Scoring observations</h2><ul class="obs">{obs}</ul></section>
<p class="muted small">{esc(METHOD_NOTE)}</p>"""
