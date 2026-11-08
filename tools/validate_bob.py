"""Check Pitwall against the hand analysis of Battle of the Bay (Nov 7, 2026).

The BOB power review was done by hand before Pitwall existed. It summed the absolute value
of supply current, so this check runs Pitwall in "abs" mode and expects the review's
numbers; then it prints the corrected (signed) figures for comparison.

Usage: python3 tools/validate_bob.py <folder with the BOB .wpilog files>
"""

import glob
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pitwall import power                      # noqa: E402
from pitwall.match import build_match          # noqa: E402
from pitwall.profile import load_profile       # noqa: E402
from pitwall.wpilog import WPILog              # noqa: E402

PROFILE = os.path.join(os.path.dirname(__file__), "..", "profiles", "2026-comp.toml")

# From the BOB power review, per match: Wh, Ah, mean W, peak 1 s W, min V,
# s left at first brownout, brownout onsets.
REVIEW = {
    "Q7": (55.2, 5.84, 1242, 2107, 6.0, 31.6, 60),
    "Q14": (54.5, 5.64, 1226, 2138, 5.9, 17.8, 55),
    "Q21": (46.5, 5.03, 1046, 2203, 5.7, 52.8, 142),
}
REVIEW_EVENT = {
    "wh": 52.0, "ah": 5.51, "mean_w": 1171, "mean_a": 124, "auto_w": 1172, "teleop_w": 1171,
    "peak_1s_w": 2149, "p95_a": 237, "quarters_w": (1268, 1415, 1338, 666), "episodes": 16,
    "groups_wh": {"drive": 24.4, "steer": 2.9, "shooter": 6.0, "feeder": 6.3, "floor": 3.7,
                  "intake": 6.8, "aim": 2.0},
}


def close(a, b, rel=0.01, absol=0.0):
    return abs(a - b) <= max(abs(b) * rel, absol)


def run(folder, mode):
    p = load_profile(PROFILE)
    p.current_mode = mode
    out = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.wpilog"))):
        m = build_match(WPILog(path), p)
        if m.identity.label in REVIEW:
            out[m.identity.label] = power.analyze(m)
    return out


def main(folder):
    fails = 0

    def check(name, got, want, rel=0.01, absol=0.0):
        nonlocal fails
        ok = close(got, want, rel, absol)
        fails += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28s} pitwall {got:10.3f}   review {want}")

    res = run(folder, "abs")
    missing = set(REVIEW) - set(res)
    if missing:
        print(f"missing logs for {sorted(missing)}")
        return 1
    print("Per match (abs mode, to match the review's method):")
    for label, (wh, ah, mw, pw, mv, left, onsets) in REVIEW.items():
        r = res[label]
        T, B = r["totals"], r["brownout"]
        print(f" {label}")
        check("energy Wh", T["wh"], wh, absol=0.05)
        check("charge Ah", T["ah"], ah, absol=0.01)
        check("mean power W", T["mean_w"], mw, absol=1)
        # A 1 s peak depends on where the 20 ms grid falls; the review's grid started
        # elsewhere, which moves a peak by up to about 1%.
        check("peak 1 s power W", T["peak_1s_w"], pw, rel=0.015)
        check("lowest battery V", T["min_v"], mv, absol=0.05)
        check("s left at first brownout", B["first_time_left_s"], left, absol=0.15)
        # Pitwall counts onsets on the raw signal; the review counted them on the 20 ms grid,
        # which merges two onsets less than 20 ms apart (Q14 has one such pair).
        check("brownout onsets", B["onsets"], onsets, absol=1)
    print(" Event averages")
    rs = list(res.values())
    avg = lambda f: statistics.mean(f(r) for r in rs)   # noqa: E731
    E = REVIEW_EVENT
    check("energy Wh", avg(lambda r: r["totals"]["wh"]), E["wh"], absol=0.05)
    check("charge Ah", avg(lambda r: r["totals"]["ah"]), E["ah"], absol=0.01)
    check("mean power W", avg(lambda r: r["totals"]["mean_w"]), E["mean_w"], absol=1)
    check("mean current A", avg(lambda r: r["totals"]["mean_a"]), E["mean_a"], absol=0.5)
    check("auto mean power W", avg(lambda r: r["totals"]["auto"]["mean_w"]), E["auto_w"], absol=1)
    check("teleop mean power W", avg(lambda r: r["totals"]["teleop"]["mean_w"]), E["teleop_w"], absol=1)
    check("peak 1 s power W", avg(lambda r: r["totals"]["peak_1s_w"]), E["peak_1s_w"], rel=0.015)
    check("95th pct current A", avg(lambda r: r["totals"]["p95_a"]), E["p95_a"], absol=1)
    for k, want in enumerate(E["quarters_w"]):
        check(f"quarter {k + 1} mean power W", avg(lambda r: r["totals"]["quarters"][k]["mean_w"]), want, absol=2)
    check("brownout episodes (total)", sum(r["brownout"]["episodes"] for r in rs), E["episodes"], rel=0)
    for gid, want in E["groups_wh"].items():
        check(f"{gid} Wh", avg(lambda r: next(s["wh"] for s in r["subsystems"] if s["id"] == gid)), want, absol=0.05)

    signed = run(folder, "signed")
    print("\nCorrected (signed) figures, braking current subtracted:")
    for label in REVIEW:
        T = signed[label]["totals"]
        print(f"  {label:4s} {T['wh']:5.1f} Wh  {T['ah']:.2f} Ah  mean {T['mean_w']:5.0f} W  "
              f"returned {T['ah_returned']:.2f} Ah")
    print(f"\n{'ALL CHECKS PASS' if not fails else f'{fails} CHECK(S) FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
