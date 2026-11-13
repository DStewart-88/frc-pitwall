"""New event codes: logs for an event Pitwall doesn't have yet, and what the team does with them."""

import json
import os
import shutil
import tempfile
import unittest

from pitwall.app.robotwatch import RobotWatch
from pitwall.app.store import Store
from pitwall.app.worker import Worker
from pitwall.fetch import Archive, LocalSource, fetch
from pitwall.fetch.identify import LogInfo
from tests.test_app import logs_into
from tests.test_pitwall import PROFILE


class NewCodeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "data"))
        self.pid = self.store.save_profile(PROFILE.format(mode="signed"))
        self.archive = os.path.join(self.dir, "FRC Logs")
        os.makedirs(self.archive)
        self.store.save_settings({"archive": self.archive})
        self.stick = os.path.join(self.dir, "ROBOT", "logs")
        logs_into(self.stick, "AAA", [1, 2], "01")
        logs_into(self.stick, "BBB", [1], "15")
        self.rebuilt = []
        self.watch = RobotWatch(self.store, finder=lambda team, link_local=True: None,
                                on_logs=self.rebuilt.append)
        self.worker = Worker(self.store, os.path.join(self.dir, "cache"), jobs=1)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fetch_stick(self):
        return fetch(LocalSource(self.stick), Archive(self.archive), self.store, settle_s=0)

    def codes(self):
        self.watch.refresh_codes()
        return {c["code"]: c for c in self.watch.get_status()["new_codes"]}

    def test_create_ignore_and_add(self):
        res = self.fetch_stick()
        self.assertEqual(res.no_pitwall_event, {"AAA": 2, "BBB": 1})
        codes = self.codes()
        self.assertEqual(set(codes), {"AAA", "BBB"})
        self.assertEqual((codes["AAA"]["matches"], codes["AAA"]["first"]), (2, "2026-01-01"))
        st = self.watch.get_status()
        self.assertEqual([p["id"] for p in st["profiles"]], [self.pid])      # what the prompt offers

        self.watch.answer_code("BBB", "ignore")
        self.assertEqual(set(self.codes()), {"AAA"})
        self.assertEqual(self.store.settings()["ignored_codes"], ["BBB"])

        r = self.watch.answer_code("AAA", "create", name="Week Zero", profile=self.pid)
        eid = r["event"]
        self.assertEqual(r["copied"], 2)
        self.assertEqual(self.rebuilt, [eid])
        ev = self.store.get_event(eid)
        self.assertEqual((ev["name"], ev["code"], ev["start"], ev["end"]), ("Week Zero", "AAA", "2026-01-01", "2026-01-01"))
        self.assertEqual(self.codes(), {})
        self.worker.build(eid)
        with open(os.path.join(self.store.report_dir(eid), "event.json")) as f:
            self.assertEqual(len(json.load(f)["matches"]), 2)

        # Later logs from the same event under a different code (the DS had another event name):
        # added to the existing event, they're reported with it instead of skipped.
        logs_into(self.stick, "AAX", [3], "01")
        self.fetch_stick()
        self.assertEqual(set(self.codes()), {"AAX"})
        r = self.watch.answer_code("AAX", "add", event=eid)
        self.assertEqual((r["event"], r["copied"]), (eid, 1))
        self.assertEqual(self.store.event_codes(eid), ["AAA", "AAX"])
        self.worker.build(eid)
        with open(os.path.join(self.store.report_dir(eid), "event.json")) as f:
            ev = json.load(f)
        self.assertEqual(sorted(m["label"] for m in ev["matches"]), ["Q1", "Q2", "Q3"])
        self.assertFalse([s for s in ev["skipped"] if "another event" in s["reason"]])
        # and the next fetch hands AAX logs straight to it
        logs_into(self.stick, "AAX", [4], "01")
        res = self.fetch_stick()
        self.assertEqual(res.handed, {eid: ["Q4"]})

    def test_bad_answers(self):
        self.fetch_stick()
        self.codes()
        with self.assertRaises(ValueError):
            self.watch.answer_code("AAA", "create")                 # no robot picked
        with self.assertRaises(ValueError):
            self.watch.answer_code("AAA", "add", event="nope")
        with self.assertRaises(ValueError):
            self.watch.answer_code("AAA", "explode")

    def test_event_dates_place_practice(self):
        a = Archive(self.archive)
        dates = {"AAA": ["2026-03-07"]}
        friday = LogInfo("practice", when="2026-03-06T15:00:00")
        self.assertIsNone(a.place(friday, "x.wpilog", dates)["event"])          # no dates set: by its day
        self.assertEqual(a.place(friday, "x.wpilog", dates, events=a.events(
            dates, spans={"AAA": ("2026-03-06", "2026-03-07")}))["event"], "AAA")


if __name__ == "__main__":
    unittest.main()
