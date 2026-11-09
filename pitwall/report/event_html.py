"""Self-contained HTML event report: every match from one event, and the change since the last.

Same rules as the match report: one file, no external requests, SVG drawn in Python, and
readable with JavaScript off. Match names link to the match reports next to it.
"""

import datetime
from typing import Any, Dict, List, Optional

from .html import INK_LINE, NAVY, RED, SHORT, _bar_path, base_css, color_for, esc

PREV_GREY = "#b4bccb"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def nice_date(stamp: str) -> str:
    try:
        d = datetime.date.fromisoformat(stamp[:10])
    except ValueError:
        return ""
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def fmt(v: Optional[float], nd: int, unit: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:,.{nd}f}" + (f" {unit}" if unit else "")


def fmt_delta(d: Optional[float], nd: int, unit: str = "") -> str:
    if d is None:
        return "—"
    if round(d, nd) == 0:
        return "no change"
    return f"{'+' if d > 0 else '−'}{abs(d):,.{nd}f}" + (f" {unit}" if unit else "")


def fmt_pct(p: Optional[float]) -> str:
    if p is None:
        return ""
    if round(p * 100) == 0:
        return "(0%)"
    return f"({'+' if p > 0 else '−'}{abs(p):.0%})"


# ---------------------------------------------------------------- charts

def _stack(x0: float, y: float, h: float, parts: List[tuple], scale: float, opacity: float = 1.0) -> str:
    """Stacked horizontal bar; parts = [(value, color, tooltip)]. Rounded only at the end."""
    out = []
    x = x0
    visible = [p for p in parts if p[0] > 0]
    for k, (v, c, tip) in enumerate(visible):
        w = v * scale
        if w < 0.3:
            continue
        last = k == len(visible) - 1
        shape = (f'<path d="{_bar_path(x, y, w, h, 3)}"' if last else
                 f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}"')
        out.append(f'<g><title>{esc(tip)}</title>{shape} fill="{c}" opacity="{opacity}" '
                   f'stroke="#ffffff" stroke-width="1"/></g>')
        x += w
    return "".join(out)


def charge_by_match_svg(ev: Dict[str, Any], links: Dict[str, str]) -> str:
    subs = sorted(ev["subsystem_info"], key=lambda s: -ev["averages"]["subsystems"][s["id"]]["ah"]) \
        if ev["averages"].get("n") else ev["subsystem_info"]
    rows = []
    for m in ev["matches"]:
        rows.append(("match", m))
    avg_rows = []
    if ev["averages"].get("n"):
        avg_rows.append(("avg", {"label": "Event average", "subs": ev["averages"]["subsystems"],
                                 "ah": ev["averages"]["ah"]}))
    C = ev.get("compare")
    if C:
        avg_rows.append(("prev", {"label": f"{C['name']} average",
                                  "subs": {r["key"]: {"ah": r["prev"] or 0.0} for r in C["subsystems"]},
                                  "ah": next(r["prev"] for r in C["rows"] if r["key"] == "ah")}))
    vmax = max([m["ah"] for _, m in rows] + [r["ah"] or 0 for _, r in avg_rows] + [1e-9])
    label_w, val_w, row_h, bar_h, top, gap = 150, 300, 30, 18, 6, 18
    width = 1100
    plot_w = width - label_w - val_w - 16
    scale = plot_w / vmax
    height = top + len(rows) * row_h + (gap + len(avg_rows) * row_h if avg_rows else 0) + 8
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
           f'aria-label="Charge used in each match, by subsystem">']
    y = top
    for _, m in rows:
        op = 1.0 if m["counted"] else 0.35
        parts = [(m["subsystems"].get(s["id"], {}).get("ah", 0.0), color_for(s["color"]),
                  f'{m["label"]} {s["name"]}: {m["subsystems"].get(s["id"], {}).get("ah", 0.0):.2f} Ah')
                 for s in subs]
        lab = esc(m["label"])
        href = links.get(m["label"])
        lab_el = (f'<a href="{esc(href)}"><text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" '
                  f'class="lbl link">{lab}</text></a>' if href else
                  f'<text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" class="lbl">{lab}</text>')
        note = "" if m["counted"] else f'<tspan class="muted"> · not counted: {esc(m["why_not"][0].lower() + m["why_not"][1:])}</tspan>'
        if not m["counted"] and len(m["why_not"]) > 34:
            note = '<tspan class="muted"> · not counted (see table)</tspan>'
        out.append(lab_el + _stack(label_w, y + (row_h - bar_h) / 2, bar_h, parts, scale, op)
                   + f'<text x="{label_w + plot_w + 12}" y="{y + row_h / 2 + 5}" class="val">'
                   f'<tspan class="num">{m["ah"]:.2f} Ah</tspan>{note}</text>')
        y += row_h
    if avg_rows:
        out.append(f'<line x1="0" x2="{width}" y1="{y + gap / 2:.1f}" y2="{y + gap / 2:.1f}" stroke="{INK_LINE}"/>')
        y += gap
        for kind, r in avg_rows:
            prev_note = f'<tspan class="muted"> · {esc(r["label"])}</tspan>'
            if kind == "avg":
                parts = [(r["subs"][s["id"]]["ah"], color_for(s["color"]),
                          f'Average {s["name"]}: {r["subs"][s["id"]]["ah"]:.2f} Ah') for s in subs]
                bar = _stack(label_w, y + (row_h - bar_h) / 2, bar_h, parts, scale)
            else:
                w = (r["ah"] or 0) * scale
                bar = (f'<g><title>{esc(r["label"])}: {fmt(r["ah"], 2, "Ah")}</title>'
                       f'<path d="{_bar_path(label_w, y + (row_h - bar_h) / 2, w, bar_h, 3)}" fill="{PREV_GREY}"/></g>')
            out.append(f'<text x="{label_w - 12}" y="{y + row_h / 2 + 5}" text-anchor="end" class="lbl">'
                       f'{esc(r["label"] if kind == "avg" else "Previous event")}</text>{bar}'
                       f'<text x="{label_w + plot_w + 12}" y="{y + row_h / 2 + 5}" class="val">'
                       f'<tspan class="num">{fmt(r["ah"], 2, "Ah")}</tspan>'
                       f'{prev_note if kind == "prev" else ""}'
                       f'</text>')
            y += row_h
    out.append("</svg>")
    return "".join(out)


def _time_left_points(series: Dict[str, Any], auto_s: float, teleop_s: float, step_s: float = 0.5):
    """(time left, lowest battery V) every `step_s`, None between enabled stretches."""
    blocks = series["blocks"]
    auto_b = next((b for b in blocks if b["mode"] == "auto"), None)
    tele_b = next((b for b in blocks if b["mode"] == "teleop"), None)
    t, v = series["t"], series["vbat"]
    pts: List[Optional[tuple]] = []
    k = max(1, round(step_s / ((t[1] - t[0]) if len(t) > 1 else 0.1)))
    for a in range(0, len(t), k):
        tt = t[a]
        b = next((b for b in blocks if b["start"] <= tt < b["end"]), None)
        if b is None:
            pts.append(None)
            continue
        if b["mode"] == "auto" and auto_b is not None:
            left = teleop_s + max(0.0, auto_s - (tt - auto_b["start"]))
        elif tele_b is not None:
            left = max(0.0, teleop_s - (tt - tele_b["start"]))
        else:
            pts.append(None)
            continue
        pts.append((left, min(v[a:a + k])))
    return pts


def brownout_svg(ev: Dict[str, Any], series_by_label: Dict[str, Dict[str, Any]],
                 links: Dict[str, str]) -> str:
    auto_s, teleop_s = ev["auto_s"], ev["teleop_s"]
    total = auto_s + teleop_s
    ms = [m for m in ev["matches"]]
    label_w, right_w, row_h, top = 150, 290, 44, 22
    width = 1100
    plot_w = width - label_w - right_w - 16
    height = top + len(ms) * row_h + 30

    def X(left: float) -> float:
        return label_w + (total - left) / total * plot_w

    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
           f'aria-label="Battery voltage and brownouts in each match, by match time left">']
    pinfo = ev.get("period_info") or []
    if pinfo:
        edges = sorted({p["from_left"] for p in pinfo} | {p["to_left"] for p in pinfo}, reverse=True)
        for p in pinfo:
            out.append(f'<text x="{(X(p["from_left"]) + X(p["to_left"])) / 2:.1f}" y="{top - 8}" text-anchor="middle" '
                       f'class="muted small">{esc(SHORT.get(p["name"], p["name"]))}</text>')
    else:
        edges = list(range(int(total), -1, -20))
        out.append(f'<rect x="{X(total):.1f}" y="{top - 4}" width="{X(teleop_s) - X(total):.1f}" '
                   f'height="{len(ms) * row_h + 4}" fill="{NAVY}" opacity="0.05"/>'
                   f'<text x="{X(total) + 6:.1f}" y="{top - 8}" class="muted small">AUTO</text>')
    for left in edges:
        out.append(f'<line x1="{X(left):.1f}" x2="{X(left):.1f}" y1="{top - 4}" y2="{top + len(ms) * row_h}" '
                   f'stroke="{INK_LINE}"/>'
                   f'<text x="{X(left):.1f}" y="{top + len(ms) * row_h + 16}" text-anchor="middle" '
                   f'class="muted small">{left:g}</text>')
    out.append(f'<text x="{label_w + plot_w}" y="{top + len(ms) * row_h + 28}" text-anchor="end" '
               f'class="muted small">seconds left in the match</text>')
    vlo, vhi = 5.0, 13.0
    for k, m in enumerate(ms):
        y0 = top + k * row_h
        op = 1.0 if m["counted"] else 0.4

        def YV(v: float) -> float:
            v = min(max(v, vlo), vhi)
            return y0 + row_h - 6 - (v - vlo) / (vhi - vlo) * (row_h - 12)

        pbn = {p["name"]: p for p in pinfo}
        for per in m.get("periods", []):
            if per["hub"] is False and per["name"] in pbn:
                p = pbn[per["name"]]
                out.append(f'<rect x="{X(p["from_left"]):.1f}" y="{y0 + 1}" width="{X(p["to_left"]) - X(p["from_left"]):.1f}" '
                           f'height="{row_h - 2}" fill="{NAVY}" opacity="0.07"/>')
        href = links.get(m["label"])
        lab = esc(m["label"])
        out.append(f'<a href="{esc(href)}"><text x="{label_w - 12}" y="{y0 + row_h / 2 + 5}" text-anchor="end" '
                   f'class="lbl link">{lab}</text></a>' if href else
                   f'<text x="{label_w - 12}" y="{y0 + row_h / 2 + 5}" text-anchor="end" class="lbl">{lab}</text>')
        out.append(f'<line x1="{label_w}" x2="{label_w + plot_w}" y1="{YV(6.75):.1f}" y2="{YV(6.75):.1f}" '
                   f'stroke="{RED}" stroke-dasharray="3 4" opacity="0.45"/>')
        series = series_by_label.get(m["label"])
        if series:
            segs, cur = [], []
            for p in _time_left_points(series, auto_s, teleop_s):
                if p is None:
                    if cur:
                        segs.append(cur)
                    cur = []
                else:
                    cur.append(f"{X(p[0]):.1f},{YV(p[1]):.1f}")
            if cur:
                segs.append(cur)
            for sg in segs:
                out.append(f'<polyline points="{" ".join(sg)}" fill="none" stroke="{NAVY}" stroke-width="1.3" '
                           f'stroke-linejoin="round" opacity="{op}"/>')
        B = m["brownout"]
        for left in B["episode_time_left"]:
            if left is not None:
                out.append(f'<line x1="{X(left):.1f}" x2="{X(left):.1f}" y1="{y0 + 5}" y2="{y0 + row_h - 5}" '
                           f'stroke="{RED}" stroke-width="2" opacity="{op}"/>')
        first = B["first_time_left_s"]
        if B["episodes"] and first is not None:
            out.append(f'<circle cx="{X(first):.1f}" cy="{y0 + 5}" r="4" fill="{RED}" opacity="{op}"/>')
            where = f' ({esc(B["first_period"])})' if B.get("first_period") else ""
            txt = (f'<tspan class="num">{first:.1f} s left</tspan><tspan class="muted">{where} · '
                   f'{B["episodes"]} episode{"s" if B["episodes"] != 1 else ""}</tspan>')
        else:
            txt = '<tspan class="muted">No brownout</tspan>'
        out.append(f'<text x="{label_w + plot_w + 12}" y="{y0 + row_h / 2 + 5}" class="val">{txt}</text>')
        out.append(f'<line x1="{label_w}" x2="{label_w + plot_w}" y1="{y0 + row_h}" y2="{y0 + row_h}" '
                   f'stroke="{INK_LINE}" opacity="0.6"/>')
    out.append("</svg>")
    return "".join(out)


def subsystem_compare_svg(C: Dict[str, Any]) -> str:
    rows = sorted(C["subsystems"], key=lambda r: -(r["cur"] or 0))
    vmax = max([r["cur"] or 0 for r in rows] + [r["prev"] or 0 for r in rows] + [1e-9])
    label_w, val_w, row_h, top = 220, 260, 38, 6
    width = 1100
    plot_w = width - label_w - val_w - 16
    height = top + len(rows) * row_h + 4
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
           f'aria-label="Charge per match by subsystem, this event and {esc(C["name"])}">']
    for k, r in enumerate(rows):
        y = top + k * row_h
        wc = (r["cur"] or 0) / vmax * plot_w
        wp = (r["prev"] or 0) / vmax * plot_w
        tip = f'{r["label"]}: {fmt(r["cur"], 2, "Ah")} here, {fmt(r["prev"], 2, "Ah")} at {C["name"]}'
        out.append(f'<g class="hov"><title>{esc(tip)}</title>'
                   f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="transparent"/>'
                   f'<text x="{label_w - 12}" y="{y + 20}" text-anchor="end" class="lbl">{esc(r["label"])}</text>')
        if wc > 0.5:
            out.append(f'<path d="{_bar_path(label_w, y + 6, wc, 16, 3)}" fill="{color_for(r["color"])}"/>')
        if wp > 0.5:
            out.append(f'<path d="{_bar_path(label_w, y + 25, wp, 7, 2)}" fill="{PREV_GREY}"/>')
        out.append(f'<text x="{label_w + plot_w + 12}" y="{y + 20}" class="val"><tspan class="num">'
                   f'{fmt(r["cur"], 2, "Ah")}</tspan><tspan class="muted"> · {fmt_delta(r["delta"], 2)} '
                   f'{fmt_pct(r["pct"])}</tspan></text></g>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- shifts

def shift_card_html(ev: Dict[str, Any], shoot: Optional[str]) -> str:
    A, C = ev["averages"], ev.get("compare")
    prev = (C or {}).get("periods") or {}
    head = ('<th>Period</th><th class="r">Length</th><th class="r">Mean A</th><th class="r">Ah</th>'
            f'<th class="r">{esc(shoot or "")} s</th>'
            '<th class="r">Mean A,<br>our hub active</th><th class="r">Mean A,<br>our hub inactive</th>')
    if C:
        head += f'<th class="r">{esc(C["name"])}<br>mean A</th><th class="r">Change</th>'

    def cnt(v: Optional[float], k: int) -> str:
        return "—" if v is None or not k else f'{v:.0f} <span class="muted small">({k})</span>'
    rows = []
    for x in A["periods"]:
        cells = (f'<td>{esc(x["name"])}</td><td class="r num">{fmt(x["s"], 0, "s")}</td>'
                 f'<td class="r num">{fmt(x["mean_a"], 0)}</td>'
                 f'<td class="r num">{fmt(x["ah"], 2)}</td>'
                 f'<td class="r num">{fmt(x["state_s"].get(shoot), 0) if shoot else "—"}</td>')
        if x["conditional"]:
            cells += (f'<td class="r num">{cnt(x["mean_a_hub_active"], x["hub_active_n"])}</td>'
                      f'<td class="r num">{cnt(x["mean_a_hub_inactive"], x["hub_inactive_n"])}</td>')
        else:
            cells += '<td class="r muted small">always active</td><td></td>'
        if C:
            q = prev.get(x["name"])
            pv = q["mean_a"] if q else None
            d = (x["mean_a"] - pv) if (pv is not None and x["mean_a"] is not None) else None
            cells += (f'<td class="r num">{fmt(pv, 0)}</td><td class="r num">{fmt_delta(d, 0, "A")} '
                      f'<span class="muted">{fmt_pct(d / pv if d is not None and pv else None)}</span></td>')
        rows.append(f"<tr>{cells}</tr>")
    hs = A.get("hub_split")
    if hs:
        phs = (C or {}).get("hub_split")
        for key, lab in (("active", "All alliance shifts, our hub active"),
                         ("inactive", "All alliance shifts, our hub inactive")):
            d = hs[key]
            cells = (f'<td>{lab}</td><td class="r num">{fmt(d["s"], 0, "s")}</td><td class="r num">{fmt(d["mean_a"], 0)}</td><td class="r num">{fmt(d["ah"], 2)}</td>'
                     '<td></td><td></td><td></td>')
            if C:
                pv = phs[key]["mean_a"] if phs else None
                dd = (d["mean_a"] - pv) if pv is not None else None
                cells += (f'<td class="r num">{fmt(pv, 0)}</td><td class="r num">{fmt_delta(dd, 0, "A")} '
                          f'<span class="muted">{fmt_pct(dd / pv if dd is not None and pv else None)}</span></td>')
            rows.append(f'<tr class="sum">{cells}</tr>')
    return (f'<section class="card"><h2>By shift</h2><p class="lead">Average per counted match. In the shifts where our '
            f'hub can be active or inactive, the two right-hand columns split the matches by which it was '
            f'(number of matches in brackets).</p>'
            f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'</section>')


# ---------------------------------------------------------------- page

def render_event(ev: Dict[str, Any], series_by_label: Dict[str, Dict[str, Any]],
                 links: Dict[str, str]) -> str:
    A, B, C = ev["averages"], ev["brownout"], ev.get("compare")
    n = A.get("n", 0)
    prev_rows = {r["key"]: r for r in C["rows"]} if C else {}

    def vs(key: str, nd: int, unit: str) -> str:
        r = prev_rows.get(key)
        if not r or r["delta"] is None:
            return ""
        return f'{fmt_delta(r["delta"], nd, unit)} vs {C["name"]}'

    if B["with_brownout"]:
        bo_left = fmt(B["median_first_left_s"], 1, "s")
        bo_left_sub = f'median · earliest {fmt(B["earliest_first_left_s"], 1, "s")} left'
    else:
        bo_left, bo_left_sub = "None", "no brownouts"
    if C:
        pb = C["brownout"]["prev"]
        bo_sub = f'{C["name"]}: {pb["with_brownout"]} of {pb["matches"]}'
        fl = C["brownout"]["first_left"]
        if fl["delta"] is not None:
            bo_left_sub += f' · {fmt_delta(fl["delta"], 1, "s")} vs {C["name"]}'
    else:
        bo_sub = f'{B["episodes_total"]} episodes in all'
    stats = [
        (fmt(A.get("ah"), 2, "Ah"), "Charge per match",
         vs("ah", 2, "Ah") or f'{fmt(A.get("wh"), 1, "Wh")} · {n} matches counted'),
        (fmt(A.get("mean_a"), 0, "A"), "Mean current", vs("mean_a", 0, "A") or
         f'{fmt(A.get("peak_1s_a"), 0, "A")} peak 1 s'),
        (f'{B["with_brownout"]} of {B["matches"]}', "Matches with a brownout", bo_sub),
        (bo_left, "Time left at first brownout", bo_left_sub),
        (fmt(A.get("min_v"), 2, "V"), "Lowest battery (average)",
         f'lowest {fmt(A.get("lowest_v"), 2, "V")} in one match'),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="big">{esc(a)}</div><div class="lab">{esc(b)}</div>'
        f'<div class="sub">{esc(c)}</div></div>' for a, b, c in stats)

    subs_sorted = sorted(ev["subsystem_info"], key=lambda s: -(A["subsystems"][s["id"]]["ah"] if n else 0))
    legend = "".join(f'<span class="key"><i style="background:{color_for(s["color"])}"></i>{esc(s["name"])}</span>'
                     for s in subs_sorted)
    if C:
        legend += f'<span class="key"><i style="background:{PREV_GREY}"></i>{esc(C["name"])} average</span>'

    # match table
    shoot = next((s for s in ev["states"] if s != "Other"), None)
    mrows = []
    for m in ev["matches"]:
        href = links.get(m["label"])
        lab = f'<a href="{esc(href)}">{esc(m["label"])}</a>' if href else esc(m["label"])
        Bm = m["brownout"]
        first = fmt(Bm["first_time_left_s"], 1) if Bm["episodes"] else "—"
        if Bm["episodes"] and Bm.get("first_period"):
            first += f' <span class="muted small">{esc(Bm["first_period"])}</span>'
        status = ("Counted" + (" (by hand)" if m.get("included_by_hand") else "")) if m["counted"] \
            else f'<span class="muted">{esc(m["why_not"])}</span>'
        extra = f' <span class="flag">{len(m["other_logs"]) + 1} logs</span>' if m.get("other_logs") else ""
        mrows.append(
            f'<tr class="{"" if m["counted"] else "dim"}"><td>{lab}{extra}</td>'
            f'<td class="r num">{m["ah"]:.2f}</td><td class="r num">{m["mean_a"]:.0f}</td>'
            f'<td class="r num">{m["peak_1s_a"]:.0f}</td><td class="r num">{m["min_v"]:.2f}</td>'
            f'<td class="r num">{Bm["episodes"]}</td><td class="r num">{first}</td>'
            f'<td class="r num">{fmt(m["state_s"].get(shoot), 0) if shoot else "—"}</td>'
            f'<td class="r num">{m["enabled_s"]:.0f}</td><td class="muted small">{esc(m["build"])}</td>'
            f'<td>{status}</td></tr>')

    # subsystem table
    srows = []
    sub_prev = {r["key"]: r for r in C["subsystems"]} if C else {}
    for s in subs_sorted:
        a = A["subsystems"][s["id"]] if n else {"ah": None, "share": None, "mean_a": None, "idle_ah": None}
        share = "—" if a["share"] is None else f'{a["share"]:.0%}'
        cells = (f'<td><i class="sw" style="background:{color_for(s["color"])}"></i>{esc(s["name"])}</td>'
                 f'<td class="r num">{fmt(a["ah"], 2)}</td><td class="r num">{share}</td>'
                 f'<td class="r num">{fmt(a["mean_a"], 1)}</td><td class="r num">{fmt(a["idle_ah"], 2)}</td>')
        if C:
            r = sub_prev.get(s["id"])
            cells += (f'<td class="r num">{fmt(r["prev"], 2) if r else "—"}</td>'
                      f'<td class="r num">{fmt_delta(r["delta"], 2) if r else "—"} '
                      f'<span class="muted">{fmt_pct(r["pct"]) if r else ""}</span></td>')
        srows.append(f"<tr>{cells}</tr>")
    s_head = ('<th>Subsystem</th><th class="r">Ah per match</th><th class="r">Share</th>'
              '<th class="r">Mean A</th><th class="r">Idle Ah</th>')
    if C:
        s_head += f'<th class="r">{esc(C["name"])}</th><th class="r">Change</th>'

    # comparison card
    compare_html = ""
    if C:
        crow = []
        for r in C["rows"]:
            if r["key"].startswith(("period.", "hub.")):
                continue
            crow.append(f'<tr><td>{esc(r["label"])}</td><td class="r num">{fmt(r["cur"], r["nd"], r["unit"])}</td>'
                        f'<td class="r num">{fmt(r["prev"], r["nd"], r["unit"])}</td>'
                        f'<td class="r num">{fmt_delta(r["delta"], r["nd"], r["unit"])} '
                        f'<span class="muted">{fmt_pct(r["pct"])}</span></td></tr>')
        cb, pb = C["brownout"]["cur"], C["brownout"]["prev"]
        crow.append(f'<tr><td>Matches with a brownout</td><td class="r num">{cb["with_brownout"]} of {cb["matches"]}</td>'
                    f'<td class="r num">{pb["with_brownout"]} of {pb["matches"]}</td><td></td></tr>')
        fl = C["brownout"]["first_left"]
        crow.append(f'<tr><td>{esc(fl["label"])}</td><td class="r num">{fmt(fl["cur"], 1, "s")}</td>'
                    f'<td class="r num">{fmt(fl["prev"], 1, "s")}</td>'
                    f'<td class="r num">{fmt_delta(fl["delta"], 1, "s")}</td></tr>')
        notes = "".join(f'<p class="lead">{esc(t)}</p>' for t in C["notes"])
        when = nice_date(C["first_stamp"])
        compare_html = f"""<section class="card"><h2>Compared with {esc(C['name'])}</h2>
<p class="lead">Averages per counted match: {n} here, {C['matches']} at {esc(C['name'])}{(' (' + esc(when) + ')') if when else ''}.
For time left at first brownout, a bigger number means the battery gave out earlier.</p>{notes}
<h3>Charge per match by subsystem</h3>{subsystem_compare_svg(C)}
<p class="muted small">Colored bars: this event. Grey bars: {esc(C['name'])}.</p>
<h3>Per match</h3>
<div class="scroll"><table class="narrow"><thead><tr><th>Average</th><th class="r">Here</th><th class="r">{esc(C['name'])}</th>
<th class="r">Change</th></tr></thead><tbody>{''.join(crow)}</tbody></table></div></section>"""

    # through the match (always shown; prev column if comparing)
    def prow(name: str, cur: Optional[float], key: str) -> str:
        return f'<tr><td>{esc(name)}</td><td class="r num">{fmt(cur, 0)}</td></tr>'
    phase = []
    if n:
        phase.append(prow("Auto", A["auto"]["mean_a"], "auto.mean_a"))
        phase.append(prow("Teleop", A["teleop"]["mean_a"], "teleop.mean_a"))
        for q in range(4):
            phase.append(prow(f"Quarter {q + 1}", A["quarters"][q]["mean_a"], f"quarters.{q}.mean_a"))
    p_head = '<th>Period</th><th class="r">Mean A</th>'
    phase_card = "" if (C or not n or A.get("periods")) else (
        '<section class="card"><h2>Through the match</h2><p class="lead">Average current per period. '
        "Quarters split each match's enabled time into four equal parts.</p>"
        f'<div class="scroll"><table class="narrow"><thead><tr>{p_head}</tr></thead>'
        f'<tbody>{"".join(phase)}</tbody></table></div></section>')

    shift_card = shift_card_html(ev, shoot) if n and A.get("periods") else ""

    sev_label = {"act": "Act", "watch": "Watch", "info": "Info"}
    obs = "".join(f'<li><span class="sev {o["severity"]}">{sev_label[o["severity"]]}</span>{esc(o["text"])}</li>'
                  for o in ev["observations"]) or '<li class="muted">Nothing flagged.</li>'

    other = ""
    if ev["other_logs"] or ev["skipped"]:
        items = "".join(f'<li>{esc(o["label"])}: {o["enabled_s"]:.0f} s enabled, {o["ah"]:.2f} Ah '
                        f'<span class="muted small">{esc(o["file"])}</span></li>' for o in ev["other_logs"])
        items += "".join(f'<li class="muted">{esc(s["file"])}: skipped, {esc(s["reason"])}</li>'
                         for s in ev["skipped"])
        other = (f'<section class="card"><h2>Other logs</h2><p class="lead">Logs from this event that '
                 f'aren\'t matches (pit, queue and practice-field runs) or that Pitwall couldn\'t report on.</p>'
                 f'<ul class="plain">{items}</ul></section>')

    mode_note = ("Braking current is subtracted (signed supply current)."
                 if ev["current_mode"] == "signed" else
                 "Braking current is counted as draw (absolute supply current), to match older hand analyses.")
    date = nice_date(ev["first_stamp"])
    last = nice_date(ev["last_stamp"])
    if last and last != date:
        date = f"{date} – {last}"

    return PAGE.format(
        css=base_css(), extra_css=EXTRA_CSS, title=esc(ev["name"]), code=esc(ev["code"]),
        date=esc(date), profile=esc(ev["profile"]), builds=esc(", ".join(ev["builds"]) or "unknown"),
        n_matches=len(ev["matches"]), n=n, stats=stat_html, legend=legend,
        charge_chart=charge_by_match_svg(ev, links),
        brownout_chart=brownout_svg(ev, series_by_label, links),
        match_rows="".join(mrows), shoot_head=esc(shoot or "—"),
        s_head=s_head, sub_rows="".join(srows), compare=compare_html,
        phase_card=phase_card, shift_card=shift_card, observations=obs,
        shade_note=("Grey shading marks the shifts where our hub was inactive." if ev.get("period_info") else ""), other=other,
        mode_note=esc(mode_note),
    )


EXTRA_CSS = """
.chart .link{fill:#0b4fa8;text-decoration:underline}
.chart a:hover .link{fill:var(--navy)}
td a{color:#0b4fa8} tr.dim td{color:var(--muted)}
td.r{white-space:nowrap} tr.sum td{font-weight:700;border-top:2px solid var(--line)} table.narrow{max-width:760px}
h3{margin:18px 0 6px;font-family:Michroma,sans-serif;font-weight:400;font-size:12px;letter-spacing:.08em}
ul.plain{margin:0;padding-left:18px;font-size:14px;display:flex;flex-direction:column;gap:4px}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Pitwall event report</title>
<style>
{css}
{extra_css}
</style>
</head>
<body>
<header><div class="in"><span class="brand">TJ² <b>PITWALL</b> · EVENT REPORT</span>
<span class="meta">{n_matches} matches · builds {builds} · profile {profile}</span></div></header>
<main>
<div><p class="eyebrow">{code} · {date}</p><h1>{title}</h1></div>
<section class="stats">{stats}</section>

<section class="card"><h2>Charge by match</h2>
<p class="lead">Charge each match used, by subsystem. Faded matches aren't counted in the averages; the table below says why. Click a match for its full report.</p>
<div class="legend">{legend}</div>
{charge_chart}
</section>

<section class="card"><h2>When the battery gave out</h2>
<p class="lead">Battery voltage through each match, lined up by match time left. Red marks are brownouts (below 6.75 V the roboRIO cuts motor output); the dot is the first. {shade_note}</p>
{brownout_chart}
</section>

{shift_card}

{compare}

<section class="card"><h2>Match by match</h2>
<div class="scroll"><table><thead><tr><th>Match</th><th class="r">Ah</th><th class="r">Mean A</th><th class="r">Peak 1 s A</th>
<th class="r">Lowest V</th><th class="r">Brownouts</th><th class="r">First brownout<br>s left</th><th class="r">{shoot_head} s</th>
<th class="r">Enabled s</th><th>Build</th><th>Averages</th></tr></thead><tbody>{match_rows}</tbody></table></div>
</section>

<section class="card"><h2>Subsystems</h2>
<p class="lead">Average per counted match. Idle is charge used outside the states a subsystem is needed in.</p>
<div class="scroll"><table><thead><tr>{s_head}</tr></thead><tbody>{sub_rows}</tbody></table></div></section>
{phase_card}

<section class="card"><h2>Observations</h2><ul class="obs">{observations}</ul></section>
{other}
<footer>Averages are per counted match ({n} of {n_matches}). A match isn't counted if it's a practice match, the robot was enabled for under 90% of it,
a motor was missing from the CAN bus, or it drew under half the event's typical charge; Pitwall shows it anyway.
{mode_note} The control system (roboRIO, radio, cameras, CANivore, Pigeon) isn't logged, so totals are slightly low.</footer>
</main>
</body>
</html>
"""
