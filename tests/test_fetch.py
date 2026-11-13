"""Log fetch: identifying logs, sorting them into the archive, and the fetch itself."""

import datetime as dt
import hashlib
import os
import shutil
import tempfile
import unittest

from pitwall.app.store import Store
from pitwall.app.worker import Worker
from pitwall.fetch import Archive, LocalSource, fetch, identify, summary_lines
from pitwall.fetch.archive import info_from_name
from pitwall.fetch.identify import LogInfo, event_dates, stamp_from_name

from .wpilog_writer import LogWriter


def utc(y, mo, d, h=12, mi=0, s=0):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc)


def local_day(t):
    return t.astimezone().date().isoformat()


def at_local(y, mo, d, h, mi=0, s=0):
    """A time of day where the tests run, as UTC (event days are local calendar days)."""
    return dt.datetime(y, mo, d, h, mi, s).astimezone().astimezone(dt.timezone.utc)


def akit_name(t, event="", match=""):
    name = "akit_" + t.astimezone(dt.timezone.utc).strftime("%y-%m-%d_%H-%M-%S")
    if event:
        name += "_" + event.lower()
    if match:
        name += "_" + match.lower()
    return name + ".wpilog"


def write_log(path, start_utc=None, event="", match_type=0, number=0, fms=False, enabled_s=0.0,
              clock_valid=True):
    """A small AdvantageKit-style log: DS fields, an enable of `enabled_s`, and the epoch clock."""
    w = LogWriter(path)
    w.put("/DriverStation/EventName", 0.0, event)
    w.put("/DriverStation/MatchType", 0.0, match_type)
    w.put("/DriverStation/MatchNumber", 0.0, number)
    w.put("/DriverStation/FMSAttached", 0.0, False)
    w.put("/DriverStation/Enabled", 0.0, False)
    w.put("/SystemStats/SystemTimeValid", 0.0, False)
    placeholder = int(utc(2026, 4, 21).timestamp() * 1e6)
    if fms:
        w.put("/DriverStation/FMSAttached", 1.0, True)
    if enabled_s:
        w.put("/DriverStation/Enabled", 5.0, True)
        w.put("/DriverStation/Enabled", 5.0 + enabled_s, False)
    t = 0.0
    while t <= 10.0 + enabled_s:
        if start_utc and clock_valid and t >= 2.0:
            if t == 2.0:
                w.put("/SystemStats/SystemTimeValid", t, True)
            w.put("/SystemStats/EpochTimeMicros", t, int((start_utc.timestamp() + t) * 1e6))
        else:
            w.put("/SystemStats/EpochTimeMicros", t, placeholder + int(t * 1e6))
        t += 1.0
    w.close()


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class IdentifyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def log(self, name, **kw):
        p = os.path.join(self.dir, name)
        write_log(p, **kw)
        return p

    def test_kinds(self):
        t = utc(2026, 11, 7, 14, 7, 10)
        m = identify(self.log(akit_name(t, "nhalt", "q2"), start_utc=t, event="NHALT", match_type=2,
                              number=2, fms=True, enabled_s=150))
        self.assertEqual((m.kind, m.event, m.match), ("match", "NHALT", "Q2"))
        self.assertEqual(m.when_from, "log clock")
        self.assertEqual(m.when, t.astimezone().replace(tzinfo=None).isoformat(timespec="seconds"))
        p = identify(self.log("p.wpilog", start_utc=t, event="NHALT", enabled_s=30))
        self.assertEqual((p.kind, p.event, p.match), ("practice", "NHALT", ""))
        b = identify(self.log("b.wpilog", start_utc=t, enabled_s=1.0))
        self.assertEqual(b.kind, "boot")

    def test_unsynced_clock_falls_back_to_the_file_name_then_unknown(self):
        t = utc(2026, 10, 24, 21, 47, 7)
        named = identify(self.log(akit_name(t, "", "e16"), start_utc=t, clock_valid=False))
        self.assertEqual(named.when_from, "file name")
        self.assertEqual(named.date, local_day(t))
        random = identify(self.log("akit_2ccc1db378128c5e.wpilog", clock_valid=False))
        self.assertIsNone(random.when)
        self.assertEqual(random.kind, "boot")

    def test_unreadable_log(self):
        p = os.path.join(self.dir, "akit_26-11-07_14-07-10.wpilog")
        with open(p, "wb") as f:
            f.write(b"not a log at all")
        i = identify(p)
        self.assertEqual(i.kind, "other")
        self.assertTrue(i.error)

    def test_phoenix_and_names(self):
        i = identify(os.path.join(self.dir, "x"), "NEWTON_Q23/NEWTON_Q23_rio_2026-04-30_16-15-32.hoot")
        self.assertEqual((i.kind, i.event, i.match), ("hoot", "NEWTON", "Q23"))
        self.assertEqual(i.date, local_day(utc(2026, 4, 30, 16, 15, 32)))
        j = identify(os.path.join(self.dir, "x"), "2026-10-19_22-49-01/rio_2026-10-19_22-49-01.hoot")
        self.assertEqual((j.kind, j.event), ("hoot", ""))
        self.assertEqual(stamp_from_name("REV_20261024_140000.revlog")[:10], local_day(utc(2026, 10, 24, 14)))
        n = info_from_name("akit_26-10-24_15-19-17_nhman_q12.wpilog")
        self.assertEqual((n.kind, n.event, n.match), ("match", "NHMAN", "Q12"))
        self.assertEqual(info_from_name("akit_26-10-24_21-47-07_e16.wpilog").event, "")


class PlaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.a = Archive(self.dir)
        self.events = os.path.join(self.dir, "2026", "01 events")
        self.practice = os.path.join(self.dir, "2026", "02 practice")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def rel(self, where):
        return os.path.relpath(where["dir"], self.dir).replace(os.sep, "/")

    def test_rules(self):
        dates = {"NHALT": ["2026-11-07"], "NHMAN": ["2026-10-24"]}
        names = {"NHALT": "Battle of the Bay"}
        match = LogInfo("match", event="NHALT", match="Q2", when="2026-11-07T09:07:10")
        self.assertEqual(self.rel(self.a.place(match, "x.wpilog", dates, names)),
                         "2026/01 events/2026-11-07_NHALT_Battle-of-the-Bay")
        # The log says NHALT, but it was recorded at the shop a week later: practice after NHALT.
        shop = LogInfo("practice", event="NHALT", when="2026-11-14T18:00:00")
        w = self.a.place(shop, "x.wpilog", dates, names)
        self.assertEqual((self.rel(w), w["event"]),
                         ("2026/02 practice/2026-11-07_after_NHALT_Battle-of-the-Bay/2026-11-14", None))
        between = LogInfo("practice", when="2026-10-27T18:00:00")
        self.assertEqual(self.rel(self.a.place(between, "x.wpilog", dates)),
                         "2026/02 practice/2026-10-24_after_NHMAN/2026-10-27")
        early = LogInfo("practice", when="2026-02-14T18:00:00")
        self.assertEqual(self.rel(self.a.place(early, "x.wpilog", dates)),
                         "2026/02 practice/2026-01-01_build-season/2026-02-14")
        # No event code at all, but on an event day: it goes with that event.
        floor = LogInfo("practice", when="2026-10-24T10:00:00")
        self.assertEqual(self.rel(self.a.place(floor, "x.wpilog", dates)), "2026/01 events/2026-10-24_NHMAN")
        # An FMS match whose log lost its event name is placed by its date.
        e16 = LogInfo("match", match="E16", when="2026-10-24T17:46:00")
        self.assertEqual(self.a.place(e16, "x.wpilog", dates)["event"], "NHMAN")
        boot = LogInfo("boot", when="2026-11-14T18:00:00")
        self.assertTrue(self.rel(self.a.place(boot, "x.wpilog", dates)).endswith("/2026-11-14/boot"))
        self.assertTrue(self.rel(self.a.place(LogInfo("boot"), "x.wpilog", dates)).endswith(
            "02 practice/undated/boot"))
        hoot = LogInfo("hoot", event="NHALT", match="E7", when="2026-11-07T15:54:00")
        self.assertEqual(self.rel(self.a.place(hoot, "NHALT_E7/rio.hoot", dates, names)),
                         "2026/01 events/2026-11-07_NHALT_Battle-of-the-Bay/hoot")
        # Next season starts its own build-season group, not "after" last fall's event.
        jan = LogInfo("practice", when="2027-01-20T18:00:00")
        self.assertEqual(self.rel(self.a.place(jan, "x.wpilog", dates)),
                         "2027/02 practice/2027-01-01_build-season/2027-01-20")

    def test_existing_event_folders(self):
        for d in ("2026-04-29_NEWTON_FIRST-Championship", "2026-10-24_NHMAN_RiverRage", "2026-10-10_MABIL1"):
            os.makedirs(os.path.join(self.events, d, "hoot"))
        dates = {"NEWTON": ["2026-04-30"], "NHMAN": ["2026-10-24"]}
        q = LogInfo("match", event="NEWTON", match="Q23", when="2026-04-30T12:15:00")
        self.assertEqual(os.path.basename(self.a.place(q, "x.wpilog", dates)["dir"]),
                         "2026-04-29_NEWTON_FIRST-Championship")
        # The folder's date starts the event: practice on the day before the first match goes with it.
        thu = LogInfo("practice", when="2026-04-29T15:00:00")
        self.assertEqual(self.a.place(thu, "x.wpilog", dates)["event"], "NEWTON")
        # A folder alone (no match logs indexed) still marks the event and names the group after it.
        after = LogInfo("practice", when="2026-10-14T18:00:00")
        self.assertEqual(self.rel(self.a.place(after, "x.wpilog", dates)),
                         "2026/02 practice/2026-10-10_after_MABIL1/2026-10-14")
        may = LogInfo("practice", when="2026-05-11T18:00:00")
        self.assertEqual(self.rel(self.a.place(may, "x.wpilog", dates)),
                         "2026/02 practice/2026-04-29_after_NEWTON_FIRST-Championship/2026-05-11")

    def test_regroup_existing_practice_days(self):
        os.makedirs(os.path.join(self.events, "2026-04-29_NEWTON_FIRST-Championship"))
        os.makedirs(os.path.join(self.events, "2026-05-30_MANOR_MA-State-Championship"))
        for d in ("2026-04-20", "2026-05-11", "2026-06-01"):
            os.makedirs(os.path.join(self.practice, d, "hoot"))
            with open(os.path.join(self.practice, d, f"akit_{d}.wpilog"), "wb") as f:
                f.write(b"x")
        self.a.seed()
        plan = self.a.regroup_plan()
        got = [(os.path.basename(s), os.path.relpath(d, self.practice).replace(os.sep, "/")) for s, d in plan]
        self.assertEqual(got, [("2026-04-20", "2026-01-01_build-season/2026-04-20"),
                               ("2026-05-11", "2026-04-29_after_NEWTON_FIRST-Championship/2026-05-11"),
                               ("2026-06-01", "2026-05-30_after_MANOR_MA-State-Championship/2026-06-01")])
        self.assertEqual(self.a.regroup(plan), [])
        self.assertEqual(self.a.regroup_plan(), [])
        moved = os.path.join(self.practice, "2026-04-29_after_NEWTON_FIRST-Championship", "2026-05-11")
        self.assertTrue(os.path.isfile(os.path.join(moved, "akit_2026-05-11.wpilog")))
        paths = sorted(e["path"] for e in Archive(self.dir).files.values())
        self.assertIn("2026/02 practice/2026-04-29_after_NEWTON_FIRST-Championship/2026-05-11/akit_2026-05-11.wpilog",
                      paths)
        self.assertTrue(all(os.path.exists(os.path.join(self.dir, p)) for p in paths))

    def test_event_dates(self):
        infos = [LogInfo("match", event="A", when="2026-03-01T10:00:00"),
                 LogInfo("match", event="A", when="2026-03-02T10:00:00"),
                 LogInfo("practice", event="A", when="2026-03-09T10:00:00"),
                 LogInfo("match", event="", when="2026-03-05T10:00:00")]
        self.assertEqual(event_dates(infos), {"A": ["2026-03-01", "2026-03-02"]})


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.stick = os.path.join(self.dir, "ROBOT")
        self.logs = os.path.join(self.stick, "logs")
        os.makedirs(os.path.join(self.logs, "NHALT_Q2"))
        self.archive_dir = os.path.join(self.dir, "FRC Logs")
        self.store = Store(os.path.join(self.dir, "data"))
        self.pid = self.store.add_builtin_profile()
        day = at_local(2026, 11, 7, 9, 7, 10)
        self.q2 = akit_name(day, "nhalt", "q2")
        write_log(os.path.join(self.logs, self.q2), start_utc=day, event="NHALT", match_type=2, number=2,
                  fms=True, enabled_s=150)
        t = day + dt.timedelta(hours=1)
        self.prac = akit_name(t, "nhalt")
        write_log(os.path.join(self.logs, self.prac), start_utc=t, event="NHALT", enabled_s=40)
        t = day + dt.timedelta(hours=2)
        write_log(os.path.join(self.logs, akit_name(t, "nhalt")), start_utc=t, event="NHALT")
        shop = at_local(2026, 11, 14, 17)
        write_log(os.path.join(self.logs, akit_name(shop, "nhalt")), start_utc=shop, event="NHALT", enabled_s=60)
        t = at_local(2026, 11, 7, 15, 2, 42)
        write_log(os.path.join(self.logs, akit_name(t, "mabil1", "e2")), start_utc=t, event="MABIL1",
                  match_type=3, number=2, fms=True, enabled_s=150)
        self.hoot = "NHALT_Q2_rio_" + (day - dt.timedelta(seconds=5)).strftime("%Y-%m-%d_%H-%M-%S") + ".hoot"
        with open(os.path.join(self.logs, "NHALT_Q2", self.hoot), "wb") as f:
            f.write(b"hoot" * 100)
        with open(os.path.join(self.logs, "notes.txt"), "w") as f:
            f.write("not a log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def snapshot(self):
        out = {}
        for top, dirs, files in os.walk(self.stick):
            for f in files:
                p = os.path.join(top, f)
                st = os.stat(p)
                out[os.path.relpath(p, self.stick)] = (st.st_size, st.st_mtime_ns, sha(p))
        return out

    def test_fetch_sorts_hands_off_and_never_touches_the_stick(self):
        eid = self.store.create_event("Battle of the Bay", self.pid, [])
        self.store.save_event(eid, {"code": "NHALT"})
        before = self.snapshot()
        archive = Archive(self.archive_dir)
        res = fetch(LocalSource(self.stick), archive, self.store, settle_s=0)
        self.assertEqual(self.snapshot(), before)                    # the stick is exactly as it was
        self.assertEqual(res.listed, 6)                              # notes.txt isn't a log
        self.assertEqual({k: res.count(k) for k in ("match", "practice", "boot", "hoot")},
                         {"match": 2, "practice": 2, "boot": 1, "hoot": 1})
        ev_dir = os.path.join(self.archive_dir, "2026", "01 events", "2026-11-07_NHALT_Battle-of-the-Bay")
        self.assertEqual(sorted(os.listdir(ev_dir)),
                         sorted([self.q2, self.prac, "boot", "hoot"]))      # practice at the event sits with it
        self.assertEqual(len(os.listdir(os.path.join(ev_dir, "boot"))), 1)
        self.assertTrue(os.path.isfile(os.path.join(ev_dir, "hoot", self.hoot)))
        shop = os.path.join(self.archive_dir, "2026", "02 practice", "2026-11-07_after_NHALT_Battle-of-the-Bay",
                            "2026-11-14")
        self.assertEqual(len(os.listdir(shop)), 1)
        # The Pitwall event got its match log and the practice log from its day, nothing else.
        self.assertEqual(sorted(res.handed[eid]), ["Q2", "practice"])
        self.assertEqual(len(self.store.event_logs(eid)), 2)
        self.assertEqual(res.no_pitwall_event, {"MABIL1": 1})
        self.assertTrue(any("MABIL1" in line for line in summary_lines(res)))
        self.assertFalse(os.listdir(archive.staging))
        # A second fetch finds nothing new; so does a fresh Archive object (the index is on disk).
        again = fetch(LocalSource(self.stick), Archive(self.archive_dir), self.store, settle_s=0)
        self.assertEqual((again.listed, again.already, len(again.fetched)), (6, 6, 0))
        self.assertEqual(self.snapshot(), before)

    def test_interrupted_fetch_is_picked_up_without_a_second_copy(self):
        archive = Archive(self.archive_dir)
        fetch(LocalSource(self.stick), archive, settle_s=0)
        os.remove(archive.index_path)                                # filed, but the index was lost
        res = fetch(LocalSource(self.stick), Archive(self.archive_dir), settle_s=0)
        self.assertEqual(len(res.fetched), 6)
        self.assertTrue(all(f.duplicate for f in res.fetched))
        copies = [f for _, _, fs in os.walk(self.archive_dir) for f in fs if " (2)" in f]
        self.assertEqual(copies, [])

    def test_seeding_skips_logs_already_in_the_archive(self):
        dest = os.path.join(self.archive_dir, "2026", "01 events", "2026-11-07_NHALT_BOB")
        os.makedirs(dest)
        shutil.copy2(os.path.join(self.logs, self.q2), dest)
        archive = Archive(self.archive_dir)
        self.assertEqual(archive.seed(), 1)
        self.assertEqual(archive.seed(), 0)
        res = fetch(LocalSource(self.stick), archive, settle_s=0)
        self.assertEqual(res.already, 1)
        self.assertNotIn(self.q2, [f.name for f in res.fetched])
        self.assertEqual(len(os.listdir(dest)), 4)       # Q2 + the practice log + boot/ + hoot/, in the reused folder

    def test_a_log_still_being_written_is_left(self):
        res = fetch(LocalSource(self.stick), Archive(self.archive_dir), settle_s=0)
        live = os.path.join(self.logs, "akit_live.wpilog")
        with open(live, "wb") as f:
            f.write(b"WPILOG" + b"\0" * 10)

        class Growing(LocalSource):
            def list(self):
                with open(live, "ab") as f:              # the robot writes between listings
                    f.write(b"\0" * 16)
                return super().list()

        res = fetch(Growing(self.stick), Archive(self.archive_dir), settle_s=0)
        self.assertEqual(res.growing, ["akit_live.wpilog"])
        self.assertEqual(res.fetched, [])

    def test_source_reads_only_inside_its_folder(self):
        src = LocalSource(self.stick)
        self.assertEqual(src.root, self.logs)
        with self.assertRaises(ValueError):
            src.open("../../data/settings.json")
        self.assertFalse(hasattr(src, "remove") or hasattr(src, "delete") or hasattr(src, "rename"))


class WorkerPickupTest(unittest.TestCase):
    def test_logs_added_by_a_fetch_rebuild_the_event(self):
        d = tempfile.mkdtemp()
        try:
            store = Store(os.path.join(d, "data"))
            pid = store.add_builtin_profile()
            eid = store.create_event("E", pid, [])
            w = Worker(store, None)
            self.assertFalse(w._logs_changed(eid))       # first look only takes note
            with open(os.path.join(store.logs_dir(eid), "a.wpilog"), "wb") as f:
                f.write(b"x")
            self.assertTrue(w._logs_changed(eid))
            self.assertFalse(w._logs_changed(eid))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
