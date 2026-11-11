import copy
import os
import tempfile
import unittest
from types import SimpleNamespace

from pitwall import scoring
from pitwall.analysis import analyze_logs
from pitwall.event import attach_comparison, build_event, strip_for_compare
from pitwall.match import build_match
from pitwall.profile import load_profile
from pitwall.report.event_html import render_event
from pitwall.report.html import render
from pitwall.wpilog import WPILog
from tests.test_pitwall import PERIODS, PROFILE, TELE0, make_log

SCORING = """
[scoring]
velocity = "/Shooter/Velocity"
setpoint = "/Shooter/Setpoint"
shooting = "/Shooting"
feeding = "/Feeding"
targeting_hub = "/TargetingHub"
"""

CFG = SimpleNamespace(dip=1.5, rearm=0.75, lockout_s=0.10, min_speed=10.0, overspeed=2.0, below=0.75,
                      hangover_s=0.30, window_s=0.20, level_s=1.0, burst_gap_s=0.6)


def signal(n_s=10.0, offset=0.0, dips=(), sp=40.0, dt=0.02):
    """A flywheel running `offset` above setpoint, with a two-sample 5-rps-below-setpoint dip at each time in `dips`."""
    t = [i * dt for i in range(int(n_s / dt))]
    v = [sp + offset] * len(t)
    for d in dips:
        k = round(d / dt)
        v[k] = v[k + 1] = sp - 5.0
    return t, v, [sp] * len(t)


def shooter_signals(dips_from=101.0, dips_to=119.0, every=0.3, hub_until=109.85, offset=1.7):
    """Writes shooter keys into a test log: shooting and feeding on from TELE0+100 to +120, a ball every
    `every` s, aiming at the hub until `hub_until`. Returns the writer callback."""
    def extra(w):
        w.put("/Feeding", 0.0, False)
        w.put("/Feeding", TELE0 + 100, True)
        w.put("/Feeding", TELE0 + 120, False)
        w.put("/TargetingHub", 0.0, True)
        w.put("/TargetingHub", TELE0 + hub_until, False)
        dip_ticks = set()
        k = 0
        while dips_from + k * every < dips_to:
            dip_ticks.add(round((TELE0 + dips_from + k * every) / 0.02))
            k += 1
        t, i = 0.0, 0
        while t <= 180.0:
            tick = round(t / 0.02)
            e = 1e-9 * (i % 2)
            w.put("/Shooter/Setpoint", t, 40.0 + e)
            low = tick in dip_ticks or (tick - 1) in dip_ticks
            w.put("/Shooter/Velocity", t, (35.0 if low else 40.0 + offset) + e)
            t = (tick + 1) * 0.02
            i += 1
    return extra


class DetectorTest(unittest.TestCase):
    def test_counts_dips_when_flywheel_runs_over_setpoint(self):
        dips = [2.0 + 0.25 * k for k in range(20)]
        for offset in (0.0, 1.7):
            t, v, sp = signal(offset=offset, dips=dips)
            on = [True] * len(t)
            self.assertEqual(len(scoring.detect(t, v, sp, on, on, CFG)), 20, offset)

    def test_no_balls_no_shots(self):
        t, v, sp = signal(offset=1.7)
        on = [True] * len(t)
        self.assertEqual(scoring.detect(t, v, sp, on, on, CFG), [])

    def test_gate_closed_no_shots(self):
        t, v, sp = signal(dips=[2.0, 3.0, 4.0])
        on = [True] * len(t)
        self.assertEqual(scoring.detect(t, v, sp, on, [False] * len(t), CFG), [])

    def test_setpoint_drop_is_not_a_ball(self):
        # Setpoint falls 40 -> 32 at 5 s; the flywheel coasts down from above: a dip in error, but no ball.
        dt = 0.02
        t = [i * dt for i in range(500)]
        sp = [40.0 if x < 5.0 else 32.0 for x in t]
        v = [40.5 if x < 5.0 else max(32.3, 40.5 - (x - 5.0) * 20) for x in t]
        on = [True] * len(t)
        self.assertEqual(scoring.detect(t, v, sp, on, on, CFG), [])

    def test_bursts_and_flow(self):
        times = [0.0, 0.2, 0.4, 0.6, 0.8, 3.0, 3.25, 10.0]
        self.assertEqual([len(b) for b in scoring.bursts_of(times, 0.6)], [5, 2, 1])
        self.assertAlmostEqual(scoring.flow_bps(times, 0.6), 4 / 0.8)


class ScoringMatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prof = os.path.join(self.dir, "p.toml")
        with open(self.prof, "w") as f:
            f.write(PROFILE.format(mode="signed") + PERIODS + SCORING)

    def make(self, name, **kw):
        path = os.path.join(self.dir, name)
        make_log(path, station=1, game_data="B", extra=shooter_signals(**kw))
        return path

    def test_match(self):
        path = self.make("akit_26-01-01_00-00-00_test_q12.wpilog")
        m = build_match(WPILog(path), load_profile(self.prof))
        sc = scoring.analyze(m)
        self.assertTrue(sc["available"])
        # 60 balls from 101 s to 119 s into teleop; aiming at the hub for the first 30
        self.assertEqual(sc["counts"]["hub"]["total"], 30)
        self.assertEqual(sc["counts"]["shuttle_mid"]["total"], 30)
        self.assertEqual(sc["counts"]["hub"]["auto"], 0)
        P = {p["name"]: p for p in sc["periods"]}
        self.assertEqual(P["Shift 4"]["hub"], 30)          # 40 to 30 s left
        self.assertEqual(P["End Game"]["shuttle_mid"], 30)
        self.assertIs(P["Shift 4"]["hub_active"], False)   # Red; game data names Blue: Red active in 1 and 3
        self.assertEqual(len(sc["bursts"]), 1)             # 0.3 s apart: one long burst
        self.assertAlmostEqual(sc["rates"]["hub"]["bps_flow"], 1 / 0.3, delta=0.05)
        self.assertAlmostEqual(sc["rates"]["hub"]["trigger_s"], 9.85, delta=0.05)

    def test_event_and_pages(self):
        paths = [self.make("akit_26-01-01_00-00-00_test_q12.wpilog"),
                 self.make("akit_26-01-01_01-00-00_test_q13.wpilog", every=0.5)]
        res = analyze_logs(paths, self.prof, cache_dir=os.path.join(self.dir, "cache"))
        pays = [r["ok"] for r in res]
        for i, p in enumerate(pays):
            p["identity"]["match_number"] = 12 + i
            p["identity"]["label"] = f"Q{12 + i}"
        prof = load_profile(self.prof)
        ev = build_event("TEST", pays, [], prof)
        A = ev["scoring"]["averages"]
        self.assertEqual(A["n"], 2)
        self.assertEqual(A["hub"], (30 + 18) / 2)
        self.assertEqual(ev["matches"][0]["scoring"]["hub"], 30)
        prev = copy.deepcopy(strip_for_compare(ev))      # as loaded from an earlier event.json
        prev["name"] = "EARLIER"
        prev["scoring"]["averages"]["hub"] = 20.0
        attach_comparison(ev, prev)
        rows = {r["key"]: r for r in ev["scoring"]["compare"]["rows"]}
        self.assertEqual(rows["hub"]["delta"], 4.0)
        self.assertTrue(any(o["rule"] == "compare-shots" for o in ev["scoring"]["observations"]))
        shots = {p["identity"]["label"]: p["scoring"]["shots"] for p in pays}
        html = render_event(ev, {p["identity"]["label"]: p["power"]["series"] for p in pays}, {}, shots)
        self.assertIn('id="scoring"', html)
        self.assertIn("Shots through each match", html)
        self.assertIn("Compared with EARLIER", html)
        mp = render(pays[0]["power"], pays[0]["identity"], "Test bot", pays[0]["scoring"])
        self.assertIn("Shots by period", mp)
        self.assertIn("Bursts", mp)

    def test_without_scoring_section(self):
        prof = os.path.join(self.dir, "plain.toml")
        with open(prof, "w") as f:
            f.write(PROFILE.format(mode="signed") + PERIODS)
        path = self.make("akit_26-01-01_00-00-00_test_q12.wpilog")
        r = analyze_logs([path], prof, cache_dir=os.path.join(self.dir, "c2"))[0]["ok"]
        self.assertNotIn("scoring", r)
        ev = build_event("TEST", [r], [], load_profile(prof))
        self.assertEqual(ev["scoring"]["averages"], {"n": 0})
        html = render_event(ev, {}, {}, {})
        self.assertNotIn('id="scoring"', html)
        self.assertNotIn('id="scoring"', render(r["power"], r["identity"], "Test bot", None))

    def test_missing_keys(self):
        prof = os.path.join(self.dir, "other.toml")
        with open(prof, "w") as f:
            f.write(PROFILE.format(mode="signed") + PERIODS + SCORING.replace("/Shooter/Velocity", "/Nope"))
        path = self.make("akit_26-01-01_00-00-00_test_q12.wpilog")
        sc = scoring.analyze(build_match(WPILog(path), load_profile(prof)))
        self.assertFalse(sc["available"])
        self.assertIn("velocity", sc["why"])


if __name__ == "__main__":
    unittest.main()
