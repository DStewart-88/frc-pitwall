import os
import tempfile
import unittest

from pitwall import power
from pitwall.match import SkipLog, build_match
from pitwall.profile import check_profile, load_profile
from pitwall.report.html import render
from pitwall.wpilog import WPILog, hold
from tests.wpilog_writer import LogWriter

PROFILE = """
[robot]
name = "Test bot"
[game]
auto_s = 20
teleop_s = 140
[settings]
current_mode = "{mode}"
[[state]]
name = "Shooting"
priority = 1
when = "/Shooting"
[[subsystem]]
id = "a"
name = "Alpha"
color = 0
[[subsystem]]
id = "b"
name = "Beta"
color = 1
needed_in = ["Shooting"]
[[motor]]
id = "m1"
name = "Motor 1"
subsystem = "a"
supply = ["/New/M1Supply", "/M1/SupplyCurrent"]
connected = "/M1/Connected"
[[motor]]
id = "m2"
name = "Motor 2"
subsystem = "b"
supply = "/M2/SupplyCurrent"
voltage = "/M2/Voltage"
[[unmeasured]]
name = "Control system"
"""

AUTO0, AUTO1, TELE0, TELE1 = 10.0, 30.0, 33.0, 173.0


def make_log(path, brownout_at=None, m1_connected=True, extra_key=False, event="TEST", match_type=2,
             number=12, m1_amps=10.0, tele_end=TELE1, station=None, game_data=None, extra=None):
    w = LogWriter(path)
    w.put("/DriverStation/EventName", 0.0, event)
    w.put("/DriverStation/MatchType", 0.0, match_type)
    w.put("/DriverStation/MatchNumber", 0.0, number)
    w.put("/DriverStation/Enabled", 0.0, False)
    w.put("/DriverStation/Autonomous", 0.0, False)
    w.put("/SystemStats/BrownedOut", 0.0, False)
    w.put("/M1/Connected", 0.0, m1_connected)
    if station is not None:
        w.put("/DriverStation/AllianceStation", 0.0, station)
    if game_data is not None:
        w.put("/DriverStation/GameSpecificMessage", 0.0, "")
        w.put("/DriverStation/GameSpecificMessage", TELE0 + 3.0, game_data)
    w.put("/Shooting", 0.0, False)
    w.put("/DriverStation/Autonomous", AUTO0, True)
    w.put("/DriverStation/Enabled", AUTO0, True)
    w.put("/DriverStation/Enabled", AUTO1, False)
    w.put("/DriverStation/Autonomous", TELE0, False)
    w.put("/DriverStation/Enabled", TELE0, True)
    w.put("/DriverStation/Enabled", tele_end, False)
    w.put("/Shooting", TELE0 + 100, True)
    w.put("/Shooting", TELE0 + 120, False)
    mid = TELE0 + 60
    t = 0.0
    k = 0
    while t <= 180.0:
        # tiny alternating offset so AdvantageKit-style "log on change" still logs every sample
        e = 1e-9 * (k % 2)
        w.put("/SystemStats/BatteryVoltage", t, 12.0 + e)
        w.put("/M1/SupplyCurrent", t, m1_amps + e)
        on = AUTO0 <= t < AUTO1 or TELE0 <= t < tele_end
        before_mid = t < mid
        w.put("/M2/SupplyCurrent", t, (5.0 if before_mid else -1.0) + e)
        w.put("/M2/Voltage", t, 12.0 if on else 0.0)
        if extra_key:
            w.put("/M3/SupplyCurrent", t, 0.0 + e)
        t += 0.01
        k += 1
    if brownout_at is not None:
        w.put("/SystemStats/BrownedOut", brownout_at, True)
        w.put("/SystemStats/BrownedOut", brownout_at + 0.1, False)
    if extra is not None:
        extra(w)
    w.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def profile(self, mode="signed"):
        p = os.path.join(self.dir, "p.toml")
        with open(p, "w") as f:
            f.write(PROFILE.format(mode=mode))
        return load_profile(p)

    def log(self, **kw):
        path = os.path.join(self.dir, "akit_26-01-01_00-00-00_test_q12.wpilog")
        make_log(path, **kw)
        return WPILog(path)


class ReaderTest(Base):
    def test_roundtrip_and_hold(self):
        log = self.log()
        t, v = log.series("/DriverStation/Enabled")
        self.assertEqual(v, [False, True, False, True, False])
        self.assertEqual(log.last_value("/DriverStation/EventName"), "TEST")
        self.assertEqual(hold([1.0, 2.0], ["a", "b"], [0.5, 1.0, 1.5, 3.0]), [None, "a", "a", "b"])


class PowerTest(Base):
    def run_power(self, mode="signed", **kw):
        m = build_match(self.log(**kw), self.profile(mode))
        return m, power.analyze(m)

    def test_signed_charge(self):
        m, r = self.run_power()
        T = r["totals"]
        self.assertAlmostEqual(T["enabled_s"], 160.0, delta=0.05)
        m1 = next(x for x in r["motors"] if x["id"] == "m1")
        m2 = next(x for x in r["motors"] if x["id"] == "m2")
        self.assertAlmostEqual(m1["ah"], 10 * 160 / 3600, delta=0.001)
        # Motor 2: 5 A for 20 s auto + 60 s teleop, then -1 A for 80 s
        self.assertAlmostEqual(m2["ah"], (5 * 80 - 1 * 80) / 3600, delta=0.001)
        self.assertAlmostEqual(m2["ah_returned"], 80 / 3600, delta=0.001)
        self.assertAlmostEqual(T["wh"], T["ah"] * 12.0, delta=0.01)
        self.assertAlmostEqual(sum(s["ah"] for s in r["subsystems"]), T["ah"], places=9)

    def test_abs_mode(self):
        _, r = self.run_power("abs")
        m2 = next(x for x in r["motors"] if x["id"] == "m2")
        self.assertAlmostEqual(m2["ah"], (5 * 80 + 1 * 80) / 3600, delta=0.001)
        self.assertEqual(m2["ah_returned"], 0.0)

    def test_states_and_idle(self):
        _, r = self.run_power()
        T = r["totals"]
        self.assertAlmostEqual(T["state_s"]["Shooting"], 20.0, delta=0.05)
        beta = next(s for s in r["subsystems"] if s["id"] == "b")
        # Beta draws -1 A for the 20 s of Shooting (all after the midpoint)
        self.assertAlmostEqual(beta["ah_by_state"]["Shooting"], -20 / 3600, delta=0.0005)
        self.assertAlmostEqual(beta["idle_ah"], beta["ah"] - beta["ah_by_state"]["Shooting"], places=9)

    def test_saturation(self):
        _, r = self.run_power()
        m2 = next(x for x in r["motors"] if x["id"] == "m2")
        self.assertAlmostEqual(m2["saturated_share"], 1.0, places=3)

    def test_brownout_time_left(self):
        _, r = self.run_power(brownout_at=TELE0 + 100.0)
        B = r["brownout"]
        self.assertEqual(B["episodes"], 1)
        self.assertAlmostEqual(B["first_time_left_s"], 40.0, delta=0.05)
        # 120 s enabled before it: motor 1 at 10 A, motor 2 at 5 A for 80 s then -1 A for 40 s
        self.assertAlmostEqual(B["ah_before_first"], (1200 + 400 - 40) / 3600, delta=0.002)
        self.assertAlmostEqual(B["ah_after_first"], (400 - 40) / 3600, delta=0.002)
        self.assertAlmostEqual(B["margin_v"], 12.0 - 6.75, delta=0.01)
        _, r = self.run_power(brownout_at=AUTO0 + 5.0)
        self.assertAlmostEqual(r["brownout"]["first_time_left_s"], 155.0, delta=0.05)

    def test_quarters(self):
        _, r = self.run_power()
        q = r["totals"]["quarters"]
        self.assertEqual(len(q), 4)
        self.assertAlmostEqual(sum(x["ah"] for x in q), r["totals"]["ah"], delta=1e-6)

    def test_identity(self):
        m, _ = self.run_power()
        self.assertEqual(m.identity.label, "Q12")
        self.assertEqual(m.identity.event, "TEST")

    def test_report_renders(self):
        m, r = self.run_power()
        page = render(r, {"label": "Q12", "event": "TEST", "file": "x.wpilog"}, "Test bot")
        self.assertIn("<h1>Q12</h1>", page)
        self.assertIn("Where the charge went", page)
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))


PERIODS = """
[[period]]
name = "Auto"
from_left = 160
to_left = 140
[[period]]
name = "Transition"
from_left = 140
to_left = 130
[[period]]
name = "Shift 1"
from_left = 130
to_left = 105
hub = "inactive_if_named"
[[period]]
name = "Shift 2"
from_left = 105
to_left = 80
hub = "active_if_named"
[[period]]
name = "Shift 3"
from_left = 80
to_left = 55
hub = "inactive_if_named"
[[period]]
name = "Shift 4"
from_left = 55
to_left = 30
hub = "active_if_named"
[[period]]
name = "End Game"
from_left = 30
to_left = 0
"""


class PeriodTest(Base):
    def run_periods(self, **kw):
        p = os.path.join(self.dir, "pp.toml")
        with open(p, "w") as f:
            f.write(PROFILE.format(mode="signed") + PERIODS)
        path = os.path.join(self.dir, "akit_26-01-01_00-00-00_test_q12.wpilog")
        make_log(path, **kw)
        m = build_match(WPILog(path), load_profile(p))
        return m, power.analyze(m)

    def test_shifts_and_hub(self):
        # Red (station 1); game data "R": red's hub goes inactive first (Shifts 1 and 3).
        m, r = self.run_periods(station=1, game_data="R", brownout_at=TELE0 + 130)
        T = r["totals"]
        P = {x["name"]: x for x in T["periods"]}
        self.assertEqual(list(P), ["Auto", "Transition", "Shift 1", "Shift 2", "Shift 3", "Shift 4", "End Game"])
        for name, secs in (("Auto", 20), ("Transition", 10), ("Shift 1", 25), ("Shift 4", 25), ("End Game", 30)):
            self.assertAlmostEqual(P[name]["s"], secs, delta=0.05)
        self.assertEqual([P[n]["hub"] for n in ("Shift 1", "Shift 2", "Shift 3", "Shift 4")],
                         [False, True, False, True])
        self.assertTrue(P["End Game"]["hub"])
        # Motor 2 switches from 5 A to -1 A with 80 s left: Shift 1 15 A, Shift 3 9 A.
        self.assertAlmostEqual(P["Shift 1"]["mean_a"], 15.0, delta=0.05)
        self.assertAlmostEqual(P["Shift 3"]["mean_a"], 9.0, delta=0.05)
        self.assertEqual(T["alliance"], "R")
        self.assertEqual(T["game_data"], "R")
        self.assertAlmostEqual(T["hub_split"]["active"]["s"], 50.0, delta=0.1)
        self.assertEqual(r["brownout"]["first_period"], "End Game")
        names = [x["name"] for x in r["series"]["periods"]]
        self.assertEqual(names[0], "Auto")
        html = render(r, {**m.identity.__dict__, "label": "Q12"}, "Test bot")
        self.assertIn("Through the match, by shift", html)

    def test_other_alliance(self):
        _, r = self.run_periods(station=5, game_data="R")       # Blue: active in Shifts 1 and 3
        P = {x["name"]: x for x in r["totals"]["periods"]}
        self.assertEqual([P[n]["hub"] for n in ("Shift 1", "Shift 2")], [True, False])

    def test_no_game_data(self):
        m, r = self.run_periods()
        P = {x["name"]: x for x in r["totals"]["periods"]}
        self.assertIsNone(P["Shift 1"]["hub"])
        self.assertIsNone(r["totals"]["hub_split"])
        self.assertTrue(any("game data" in f for f in m.flags))


class SkipTest(Base):
    def test_never_enabled(self):
        path = os.path.join(self.dir, "idle.wpilog")
        w = LogWriter(path)
        w.put("/DriverStation/Enabled", 0.0, False)
        w.put("/SystemStats/BatteryVoltage", 0.0, 12.5)
        w.put("/M1/SupplyCurrent", 0.0, 0.0)
        w.put("/M2/SupplyCurrent", 0.0, 0.0)
        w.close()
        with self.assertRaises(SkipLog):
            build_match(WPILog(path), self.profile())

    def test_no_current_logged(self):
        path = os.path.join(self.dir, "old.wpilog")
        w = LogWriter(path)
        w.put("/DriverStation/Enabled", 0.0, False)
        w.put("/DriverStation/Enabled", 1.0, True)
        w.put("/DriverStation/Enabled", 5.0, False)
        w.put("/SystemStats/BatteryVoltage", 0.0, 12.5)
        w.close()
        with self.assertRaises(SkipLog):
            build_match(WPILog(path), self.profile())


class ProfileCheckTest(Base):
    def test_fallback_key(self):
        c = check_profile(self.profile(), self.log())
        self.assertEqual(c.resolved["m1"]["supply"], "/M1/SupplyCurrent")
        self.assertTrue(c.ok)

    def test_unassigned_and_missing(self):
        log = self.log(extra_key=True)
        c = check_profile(self.profile(), log)
        self.assertIn("/M3/SupplyCurrent", c.unassigned)

    def test_silent_motor(self):
        m = build_match(self.log(m1_connected=False), self.profile())
        self.assertIn("m1", m.silent)
        r = power.analyze(m)
        self.assertTrue(any(o["rule"] == "silent-motor" for o in r["observations"]))


if __name__ == "__main__":
    unittest.main()
