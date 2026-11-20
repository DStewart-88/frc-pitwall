"""Health module: device checks, wear, CAN and loop timing, on a synthetic match log."""

import json
import os
import shutil
import tempfile
import unittest

from pitwall.analysis import analyze_logs
from pitwall.eventrun import group_results, make_event, write_event
from pitwall.health import _match_text
from pitwall.profile import load_profile
from tests.test_pitwall import AUTO0, PROFILE, TELE0, TELE1, make_log

HEALTH = """
[health]
skip = ["Power/"]
"""
F = "/RealOutputs/Health/Faults/"


def health_extra(turret_out=None, gyro_out=None, wear_m=(1000.0, 1250.0), due=True, loop_ms=30.0):
    """Health monitor keys: every check false at boot, then the ones given turn on and off."""
    def extra(w):
        for k in ("Turret/Motor", "Drive/Gyro", "Drive/Module1/TurnEncoder", "CAN/BusErrors", "Power/Brownout"):
            w.put(F + k, 0.0, False)
        w.put("/RealOutputs/Alerts/errors", 0.0, [])
        w.put("/RealOutputs/Alerts/warnings", 0.0, [])
        errs = []
        if turret_out:
            a, b = turret_out
            errs.append("Turret motor disconnected (CAN ID 7).")
            w.put(F + "Turret/Motor", a, True)
            w.put("/RealOutputs/Alerts/errors", a, list(errs))
            w.put(F + "Turret/Motor", b, False)
            errs.remove("Turret motor disconnected (CAN ID 7).")
            w.put("/RealOutputs/Alerts/errors", b, list(errs))
        if gyro_out:
            w.put(F + "Drive/Gyro", gyro_out[0], True)
            w.put("/RealOutputs/Alerts/errors", gyro_out[0], ["Disconnected gyro, using kinematics as fallback."])
            w.put(F + "Drive/Gyro", gyro_out[1], False)
        w.put(F + "Power/Brownout", TELE0 + 50, True)            # left to the Power tab
        w.put(F + "Power/Brownout", TELE0 + 51, False)
        for mod in ("FL", "BR"):
            pre = f"/RealOutputs/Usage/Tread/{mod}/"
            w.put(pre + "SinceReplacementMeters", 0.0, wear_m[0])
            w.put(pre + "SinceReplacementMeters", TELE1, wear_m[1])
            w.put(pre + "NextInspectionAtMeters", 0.0, 2000.0)
            w.put(pre + "InspectionDue", 0.0, due)
            w.put(pre + "LastGrade", 0.0, "")
        t = 0.0
        while t <= 180.0:
            w.put("/RealOutputs/LoggedRobot/FullCycleMS", t, loop_ms + (0.001 if int(t * 50) % 2 else 0.0))
            w.put("/SystemStats/CANBus/Utilization", t, 0.6 + (0.001 if int(t * 50) % 2 else 0.0))
            t += 0.02
    return extra


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pfile = os.path.join(self.dir, "p.toml")
        with open(self.pfile, "w") as f:
            f.write(PROFILE.format(mode="signed") + HEALTH)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def log(self, name, number, **kw):
        p = os.path.join(self.dir, name)
        make_log(p, event="AAA", number=number, extra=health_extra(**kw))
        return p

    def test_match(self):
        p = self.log("akit_26-01-01_10-00-00_aaa_q1.wpilog", 1, turret_out=(TELE0 + 10, TELE0 + 13.5),
                     gyro_out=(1.0, AUTO0 + 2))
        h = analyze_logs([p], self.pfile)[0]["ok"]["health"]
        D = h["devices"]
        self.assertEqual(D["checks"], 4)                                    # Power/ left out
        self.assertEqual([f["key"] for f in D["faults"]], ["Turret/Motor", "Drive/Gyro"])
        t = D["faults"][0]
        self.assertEqual((t["text"], t["severity"], t["count"]), ("Turret motor disconnected (CAN ID 7).", "error", 1))
        self.assertAlmostEqual(t["active_s"], 3.5, delta=0.05)
        self.assertAlmostEqual(t["spans"][0][0], TELE0 + 10 - AUTO0, delta=0.05)
        g = D["faults"][1]
        self.assertEqual(g["text"], "Disconnected gyro, using kinematics as fallback.")   # on before the enable
        self.assertAlmostEqual(g["active_s"], 2.0, delta=0.05)                            # counted while enabled
        W = h["wear"]
        self.assertEqual([p["part"] for p in W["parts"]], ["BR", "FL"])
        self.assertEqual((W["parts"][0]["since_m"], W["parts"][0]["match_m"], W["parts"][0]["due"]), (1250.0, 250.0, True))
        self.assertAlmostEqual(h["can"]["util_median"], 60.0, delta=0.2)
        self.assertAlmostEqual(h["loop"]["median_ms"], 30.0, delta=0.01)
        self.assertEqual(h["loop"]["over_share"], 1.0)
        due = [o for o in h["observations"] if o["rule"] == "wear-due"]
        self.assertEqual(due[0]["severity"], "info")                          # quiet, never an alert

    def test_event_and_pages(self):
        a = self.log("akit_26-01-01_10-00-00_aaa_q1.wpilog", 1, turret_out=(TELE0 + 10, TELE0 + 13.5))
        b = self.log("akit_26-01-01_11-00-00_aaa_q2.wpilog", 2, turret_out=(TELE0 + 40, TELE0 + 41), loop_ms=40.0)
        c = self.log("akit_26-01-01_12-00-00_aaa_q3.wpilog", 3)
        res = analyze_logs([a, b, c], self.pfile)
        groups, skipped, path_of, _ = group_results(list(zip([a, b, c], res)))
        prof = load_profile(self.pfile)
        ev = make_event("AAA", groups["AAA"], prof, path_of=path_of)
        A = ev["health"]["averages"]
        self.assertEqual((A["n"], A["with_errors"]), (3, 2))
        self.assertAlmostEqual(A["loop_median"], 30.0, delta=0.01)
        rec = [o for o in ev["health"]["observations"] if o["rule"] == "recurring"]
        self.assertEqual(rec[0]["text"], "Turret motor disconnected (CAN ID 7): in 2 matches (Q1, Q2).")
        self.assertEqual(rec[0]["severity"], "act")
        out = os.path.join(self.dir, "out")
        index = write_event(ev, groups["AAA"], out)
        with open(index) as f:
            html = f.read()
        self.assertIn('id="tab-health"', html)
        self.assertIn("Matches with a device error", html)
        self.assertIn("Turret motor disconnected (CAN ID 7).", html)
        q1 = [n for n in os.listdir(os.path.join(out, "matches")) if n.endswith("q1.html")][0]
        with open(os.path.join(out, "matches", q1)) as f:
            page = f.read()
        self.assertIn('id="tab-health"', page)
        self.assertIn("Power tab", page)
        self.assertNotIn("Brownout", page.split('id="tab-health"')[1].split("</ul>")[0])

    def test_logs_from_before_the_health_monitor(self):
        p = os.path.join(self.dir, "akit_26-01-01_10-00-00_aaa_q1.wpilog")
        make_log(p, event="AAA", number=1)
        h = analyze_logs([p], self.pfile)[0]["ok"]["health"]
        self.assertFalse(h["devices"]["available"])
        self.assertFalse(h["wear"]["available"])

    def test_alert_text_matching(self):
        texts = ["Back-left module (2) drive motor disconnected (CAN ID 11).",
                 "Back-left module (2) turn encoder disconnected (CAN ID 10).",
                 "Back-left module (2) turn motor disconnected (CAN ID 10).",
                 "Disconnected turn encoder on module 1.", "HotTub funnel motor disconnected (CAN ID 21).",
                 "Disconnected gyro, using kinematics as fallback."]
        self.assertEqual(_match_text("Drive/BackLeft/TurnEncoder", texts), texts[1])
        self.assertEqual(_match_text("Drive/BackLeft/DriveMotor", texts), texts[0])
        self.assertEqual(_match_text("Drive/Module1/TurnEncoder", texts), texts[3])
        self.assertEqual(_match_text("HotTub/Funnel", texts), texts[4])
        self.assertEqual(_match_text("Drive/Gyro", texts), texts[5])
        self.assertIsNone(_match_text("Turret/Motor", texts))


if __name__ == "__main__":
    unittest.main()
