"""Practice matches: Driver Station practice-mode runs found inside shop logs, and their reports."""

import datetime as dt
import json
import os
import shutil
import tempfile
import unittest

from pitwall.analysis import analyze_logs
from pitwall.app.robotwatch import RobotWatch
from pitwall.app.store import Store
from pitwall.app.worker import Worker
from pitwall.eventrun import practice_name, practice_results
from pitwall.match import build_match, practice_windows
from pitwall.profile import load_profile
from pitwall.wpilog import WPILog
from tests.test_app import logs_into
from tests.test_pitwall import PROFILE
from tests.wpilog_writer import LogWriter

AUTO, GAP, TELE = 20.0, 1.0, 140.0


def practice_log(path, matches=(), drills=(), timer=True, start=None, m1=10.0, end_early=None):
    """A shop log: practice-mode matches starting at the times in `matches`, plain enables
    (drilling) at (time, length) in `drills`. The epoch clock starts at `start` (local time)."""
    events = []                                   # (t, key, value)
    for t0 in matches:
        t_end = t0 + AUTO + GAP + (end_early if end_early else TELE)
        events += [(t0 - 3, "/DriverStation/MatchTime", float(AUTO)), (t0, "/DriverStation/Autonomous", True),
                   (t0, "/DriverStation/Enabled", True), (t0 + AUTO, "/DriverStation/Enabled", False),
                   (t0 + AUTO + 0.2, "/DriverStation/Autonomous", False),
                   (t0 + AUTO + GAP, "/DriverStation/MatchTime", float(TELE)),
                   (t0 + AUTO + GAP, "/DriverStation/Enabled", True),
                   (t_end, "/DriverStation/Enabled", False), (t_end + 0.5, "/DriverStation/MatchTime", -1.0)]
        for k in range(1, int(AUTO)):
            events.append((t0 + k, "/DriverStation/MatchTime", float(AUTO - k)))
        for k in range(1, int(TELE)):
            if t0 + AUTO + GAP + k < t_end:
                events.append((t0 + AUTO + GAP + k, "/DriverStation/MatchTime", float(TELE - k)))
    for t0, length in drills:
        events += [(t0, "/DriverStation/Enabled", True), (t0 + length, "/DriverStation/Enabled", False)]
    if not timer:
        events = [e for e in events if e[1] != "/DriverStation/MatchTime"]
    events.sort(key=lambda e: e[0])
    t_last = max([e[0] for e in events] + [10.0]) + 5
    w = LogWriter(path)
    w.put("/DriverStation/EventName", 0.0, "")
    w.put("/DriverStation/MatchType", 0, 0)
    w.put("/DriverStation/MatchNumber", 0, 0)
    w.put("/DriverStation/FMSAttached", 0.0, False)
    w.put("/DriverStation/Enabled", 0.0, False)
    w.put("/DriverStation/Autonomous", 0.0, False)
    if timer:
        w.put("/DriverStation/MatchTime", 0.0, -1.0)
    w.put("/SystemStats/BrownedOut", 0.0, False)
    w.put("/M1/Connected", 0.0, True)
    w.put("/Shooting", 0.0, False)
    w.put("/SystemStats/SystemTimeValid", 0.0, True)
    ei, t, k = 0, 0.0, 0
    while t <= t_last:
        while ei < len(events) and events[ei][0] <= t:
            _, key, val = events[ei]
            w.put(key, events[ei][0], val)
            ei += 1
        e = 1e-9 * (k % 2)
        w.put("/SystemStats/BatteryVoltage", t, 12.0 + e)
        w.put("/M1/SupplyCurrent", t, m1 + e)
        w.put("/M2/SupplyCurrent", t, 5.0 + e)
        if start and k % 50 == 0:
            w.put("/SystemStats/EpochTimeMicros", t, int((start.timestamp() + t) * 1e6))
        t += 0.02
        k += 1
    w.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pfile = os.path.join(self.dir, "p.toml")
        with open(self.pfile, "w") as f:
            f.write(PROFILE.format(mode="signed"))
        self.profile = load_profile(self.pfile)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def log(self, name="akit_26-10-27_22-00-00.wpilog", **kw):
        p = os.path.join(self.dir, name)
        practice_log(p, **kw)
        return p


class DetectTest(Base):
    def test_finds_practice_matches_not_drilling(self):
        p = self.log(matches=(30.0, 260.0), drills=((200.0, 25.0), (440.0, 60.0)))
        w = practice_windows(WPILog(p), self.profile)
        self.assertEqual(len(w), 2)
        self.assertAlmostEqual(w[0][0], 30.0, delta=0.05)
        self.assertAlmostEqual(w[0][1], 30.0 + AUTO + GAP + TELE, delta=0.05)
        self.assertAlmostEqual(w[1][0], 260.0, delta=0.05)

    def test_auto_then_teleop_without_the_timer_is_drilling(self):
        p = self.log(drills=((30.0, 20.0), (51.0, 140.0)))       # plain enables, timer at -1
        self.assertEqual(practice_windows(WPILog(p), self.profile), [])

    def test_no_timer_in_the_log_falls_back_to_the_auto_length(self):
        p = self.log(matches=(30.0,), timer=False)
        self.assertEqual(len(practice_windows(WPILog(p), self.profile)), 1)

    def test_a_match_ended_early_is_still_a_match(self):
        p = self.log(matches=(30.0,), end_early=40.0)
        w = practice_windows(WPILog(p), self.profile)
        self.assertEqual(len(w), 1)
        m = build_match(WPILog(p), self.profile, w[0], 1)
        self.assertTrue(m.identity.is_match)
        self.assertTrue(any("Enabled for only" in f for f in m.flags))

    def test_each_practice_match_is_its_own_match(self):
        p = self.log(matches=(30.0, 260.0), drills=((200.0, 25.0),))
        log = WPILog(p)
        w = practice_windows(log, self.profile)
        m = build_match(log, self.profile, w[1], 2)
        self.assertEqual(m.identity.label, "Practice match 2")
        self.assertAlmostEqual(m.blocks[0].start, 260.0, delta=0.05)
        self.assertEqual(len(m.blocks), 2)                   # the drill at 200 s isn't in it
        self.assertAlmostEqual(sum(m.enabled) * m.dt, AUTO + TELE, delta=0.5)
        self.assertIsNotNone(m.time_left[m.enabled.index(True)])


class AnalyzeTest(Base):
    def test_parts_named_timed_and_labelled_by_day(self):
        day1 = dt.datetime(2026, 10, 27, 18, 0).astimezone()
        day2 = dt.datetime(2026, 10, 29, 18, 0).astimezone()
        a = self.log("a.wpilog", matches=(30.0, 260.0), start=day1)
        b = self.log("b.wpilog", matches=(30.0,), start=day2)
        c = self.log("c.wpilog", drills=((30.0, 50.0),), start=day2)
        results = analyze_logs([a, b, c], self.pfile)
        self.assertEqual([len(r.get("parts") or []) for r in results], [2, 1, 0])
        files = [p["source"]["file"] for p in results[0]["parts"]]
        self.assertEqual(files, ["a.pm1.wpilog", "a.pm2.wpilog"])
        self.assertEqual(results[0]["parts"][1]["identity"]["stamp"],
                         (day1 + dt.timedelta(seconds=260)).strftime("%Y-%m-%d %H:%M:%S"))
        g, without = practice_results(list(zip([a, b, c], results)))
        self.assertEqual([p["identity"]["label"] for p in g["matches"]], ["Oct 27 #1", "Oct 27 #2", "Oct 29 #1"])
        self.assertEqual(without, 1)

    def test_names(self):
        self.assertEqual(practice_name("2026-10-24_after_NHMAN_RiverRage"), "Practice after RiverRage")
        self.assertEqual(practice_name("2026-10-10_after_MABIL1"), "Practice after MABIL1")
        self.assertEqual(practice_name("2026-04-29_after_NEWTON_FIRST-Championship"),
                         "Practice after FIRST Championship")
        self.assertEqual(practice_name("2026-01-01_build-season"), "Build season practice 2026")


class AppTest(Base):
    def test_practice_reports_from_the_archive(self):
        store = Store(os.path.join(self.dir, "data"))
        pid = store.save_profile(PROFILE.format(mode="signed"))
        # An event first, so the practice report has something to compare with.
        stick = os.path.join(self.dir, "stick")
        logs_into(stick, "AAA", [1, 2, 3], "20")
        ev = store.create_event("Week One", pid, [stick])
        w = Worker(store, os.path.join(self.dir, "cache"), jobs=1)
        w.import_from_sources(ev, wait_settle=False)
        w.build(ev)
        # The archive's practice group after it, with two days of practice.
        archive = os.path.join(self.dir, "FRC Logs")
        group = os.path.join(archive, "2026", "02 practice", "2026-01-20_after_AAA_Week-One")
        for day, matches in (("2026-01-27", (30.0, 260.0)), ("2026-01-29", (30.0,))):
            os.makedirs(os.path.join(group, day, "boot"))
            start = dt.datetime.fromisoformat(day + "T18:00:00").astimezone()
            practice_log(os.path.join(group, day, f"akit_{day}.wpilog"), matches=matches, start=start, m1=12.0)
            practice_log(os.path.join(group, day, "boot", "akit_b.wpilog"))
        store.save_settings({"archive": archive})
        built = []
        rw = RobotWatch(store, finder=lambda team, link_local=True: None, on_logs=built.append)
        rw.refresh_codes()
        prac = [e for e in store.list_events() if e.get("kind") == "practice"]
        self.assertEqual(len(prac), 1)
        pe = prac[0]
        self.assertEqual((pe["name"], pe["profile"], pe["group"]),
                         ("Practice after Week One", pid, "2026/02 practice/2026-01-20_after_AAA_Week-One"))
        self.assertEqual(built, [pe["id"]])
        self.assertEqual(len(store.event_logs(pe["id"])), 2)              # read in place; boot/ left out
        self.assertFalse(os.listdir(store.logs_dir(pe["id"])))            # nothing copied
        rw.refresh_codes()
        self.assertEqual(len([e for e in store.list_events() if e.get("kind") == "practice"]), 1)

        w.build(pe["id"])
        with open(os.path.join(store.report_dir(pe["id"]), "event.json")) as f:
            rep = json.load(f)
        self.assertEqual(rep["kind"], "practice")
        self.assertEqual([m["label"] for m in rep["matches"]], ["Jan 27 #1", "Jan 27 #2", "Jan 29 #1"])
        self.assertEqual(rep["averages"]["n"], 3)                         # counted like event matches
        self.assertEqual(rep["compare"]["name"], "Week One")              # compared with the event before
        self.assertEqual(store.get_event(pe["id"])["code"], "")           # it never claims an event code
        with open(os.path.join(store.report_dir(pe["id"]), "index.html")) as f:
            html = f.read()
        self.assertIn("Practice average", html)
        self.assertIn('class="dayrow"', html)
        self.assertEqual(len(os.listdir(os.path.join(store.report_dir(pe["id"]), "matches"))), 6)  # 3 x html+json
        # An event built later still compares with the event, not the practice report.
        later = w._baseline("x", {"compare": "auto", "profile": pid}, {"first_stamp": "2027-01-01"})
        self.assertEqual(later["name"], "Week One")


if __name__ == "__main__":
    unittest.main()
