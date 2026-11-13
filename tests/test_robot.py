"""Fetching from the robot over SFTP, against a stand-in roboRIO (tests/fake_rio.py)."""

import datetime as dt
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from pitwall.app.robotwatch import RobotWatch, archive_ok
from pitwall.app.store import Store
from pitwall.fetch import Archive, fetch
from pitwall.fetch import robot as R
from pitwall.fetch.source import still_growing

from .test_fetch import akit_name, at_local, write_log

try:
    from .fake_rio import FakeRio
    HAVE = True
except ImportError:
    HAVE = False


def fill(logs):
    """A Battle of the Bay match, a practice log and a Phoenix session on the robot's stick."""
    day = at_local(2026, 11, 7, 9, 7, 10)
    q2 = akit_name(day, "nhalt", "q2")
    write_log(os.path.join(logs, q2), start_utc=day, event="NHALT", match_type=2, number=2, fms=True,
              enabled_s=150)
    t = day + dt.timedelta(hours=1)
    write_log(os.path.join(logs, akit_name(t, "nhalt")), start_utc=t, event="NHALT", enabled_s=40)
    os.makedirs(os.path.join(logs, "NHALT_Q2"))
    with open(os.path.join(logs, "NHALT_Q2", "NHALT_Q2_rio_2026-11-07_14-07-05.hoot"), "wb") as f:
        f.write(b"hoot" * 1000)
    return q2


@unittest.skipUnless(HAVE, "paramiko isn't installed")
class SftpTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.rio_dir = os.path.join(self.dir, "rio")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def robot(self, rio, wired=True):
        return R.Robot("127.0.0.1", "USB", wired, port=rio.port)

    def test_logs_in_however_the_rio_asks_and_reads(self):
        for auth in ("none", "password", "interactive"):
            with FakeRio(os.path.join(self.dir, auth), auth=auth) as rio:
                fill(rio.logs)
                with R.SftpSource(self.robot(rio)) as src:
                    files = src.list()
                    self.assertEqual(src.login, {"interactive": "keyboard-interactive"}.get(auth, auth))
                    self.assertEqual(sorted(f.rel for f in files)[0], "NHALT_Q2/NHALT_Q2_rio_2026-11-07_14-07-05.hoot")
                    self.assertEqual(len(files), 3)
                    with src.open("NHALT_Q2/NHALT_Q2_rio_2026-11-07_14-07-05.hoot") as f:
                        self.assertEqual(f.read(), b"hoot" * 1000)
                    self.assertEqual(src.stick_space(), {"total": 16000000000, "free": 10240000000})
                    with self.assertRaises(ValueError):
                        src.open("../../etc/passwd")
                self.assertEqual(rio.writes, [])

    def test_fetch_from_the_robot_changes_nothing_on_it(self):
        with FakeRio(self.rio_dir) as rio:
            q2 = fill(rio.logs)
            store = Store(os.path.join(self.dir, "data"))
            eid = store.create_event("Battle of the Bay", store.add_builtin_profile(), [])
            store.save_event(eid, {"code": "NHALT"})
            archive = Archive(os.path.join(self.dir, "FRC Logs"))
            with R.SftpSource(self.robot(rio)) as src:
                res = fetch(src, archive, store, settle_s=0)
            self.assertEqual(len(res.fetched), 3)
            self.assertEqual(sorted(res.handed[eid]), ["Q2", "practice"])
            ev = os.path.join(archive.root, "2026", "01 events", "2026-11-07_NHALT_Battle-of-the-Bay")
            self.assertTrue(os.path.isfile(os.path.join(ev, q2)))
            self.assertTrue(os.path.isfile(os.path.join(ev, "hoot", "NHALT_Q2_rio_2026-11-07_14-07-05.hoot")))
            # Only listings and read-only opens ever reached the robot.
            self.assertEqual(rio.writes, [])
            self.assertEqual({c[0] for c in rio.calls}, {"list", "open"})
            self.assertTrue(all(c[2] == os.O_RDONLY for c in rio.calls if c[0] == "open"))
            with R.SftpSource(self.robot(rio)) as src:
                again = fetch(src, Archive(archive.root), store, settle_s=0)
            self.assertEqual((again.already, len(again.fetched)), (3, 0))

    def test_the_log_being_written_is_left_whatever_its_time_says(self):
        with FakeRio(self.rio_dir) as rio:
            live = os.path.join(rio.logs, "akit_9a3c1e0b2f4d5a6c.wpilog")
            with open(live, "wb") as f:
                f.write(b"WPILOG" + b"\0" * 100)
            os.utime(live, (0, 0))                # the roboRIO clock was a placeholder: looks ancient
            stop = threading.Event()

            def robot_writes():
                while not stop.is_set():
                    with open(live, "ab") as f:
                        f.write(b"\0" * 64)
                    os.utime(live, (0, 0))
                    time.sleep(0.05)
            t = threading.Thread(target=robot_writes, daemon=True)
            t.start()
            try:
                with R.SftpSource(self.robot(rio)) as src:
                    files = src.list()
                    self.assertEqual(still_growing(src, files, wait_s=0.5), {"akit_9a3c1e0b2f4d5a6c.wpilog"})
            finally:
                stop.set()
                t.join()

    def test_no_stick_in_the_rio(self):
        with FakeRio(self.rio_dir) as rio:
            shutil.rmtree(rio.logs)
            with R.SftpSource(self.robot(rio)) as src:
                with self.assertRaises(FileNotFoundError):
                    src.list()


class FindTest(unittest.TestCase):
    def test_order_and_link_types(self):
        up = {"10.0.88.2"}
        with mock.patch.object(R, "_port_open", lambda h, t: h in up), \
                mock.patch.object(R, "link_is_wired", lambda h: h != "10.0.88.2" or None), \
                mock.patch.object(R, "link_local_rios", lambda: ["fe80::280:2fff:fe43:1585%usb0"]):
            self.assertEqual(R.find_robot(88).host, "10.0.88.2")
            up.add("172.22.11.2")
            self.assertEqual(R.find_robot(88).host, "10.0.88.2")      # radio/Ethernet first, as listed
            up.clear()
            up.add("fe80::280:2fff:fe43:1585%usb0")
            r = R.find_robot(88)
            self.assertEqual((r.host, r.via, r.wired), ("fe80::280:2fff:fe43:1585%usb0", "USB", True))
            self.assertIsNone(R.find_robot(88, link_local=False))
        self.assertEqual(R.team_address(88), "10.0.88.2")
        self.assertEqual(R.team_address(1678), "10.16.78.2")
        self.assertTrue(R.link_is_wired("172.22.11.2"))
        self.assertTrue(R.link_is_wired("fe80::280:2fff:fe43:1585%usb0"))

    def test_link_local_addresses_are_recognised(self):
        ping = ("64 bytes from fe80::9b0d:b9f2:a162:8523%enp14s0f3u1u2u1: icmp_seq=1 ttl=64 time=0.031 ms\n"
                "64 bytes from fe80::280:2fff:fe43:1585%enp14s0f3u1u2u1: icmp_seq=1 ttl=64 time=0.544 ms\n")
        self.assertEqual([m.group(0) for m in R.NI_LINK_LOCAL.finditer(ping)],
                         ["fe80::280:2fff:fe43:1585%enp14s0f3u1u2u1"])
        self.assertIsNone(R.NI_LINK_LOCAL.search("fe80::9b0d:b9f2:a162:8523"))


@unittest.skipUnless(HAVE, "paramiko isn't installed")
class WatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "data"))
        self.archive = os.path.join(self.dir, "FRC Logs")
        os.makedirs(self.archive)
        self.store.save_settings({"archive": self.archive})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def watch(self, rio, wired):
        robot = R.Robot("127.0.0.1", "USB" if wired else "Ethernet or radio", wired, port=rio.port)
        return RobotWatch(self.store, finder=lambda team, link_local=True: robot)

    def test_auto_fetch_only_on_a_wired_link(self):
        with FakeRio(os.path.join(self.dir, "rio")) as rio:
            fill(rio.logs)
            radio = self.watch(rio, wired=None)
            radio.poll()
            st = radio.get_status()
            self.assertTrue(st["connected"])
            self.assertEqual((st["waiting"], st["stick"]["files"], st["stick"]["free"]), (3, 3, 10240000000))
            self.assertIsNone(st["last"])                              # not over the radio
            tether = self.watch(rio, wired=True)
            tether.poll()
            st = tether.get_status()
            self.assertEqual(st["last"]["copied"], 3)
            self.assertEqual(st["waiting"], 0)
            self.assertEqual(rio.writes, [])

    def test_a_never_fetched_stick_waits_for_a_click(self):
        with FakeRio(os.path.join(self.dir, "rio")) as rio:
            for i in range(R_AUTO_MAX() + 1):
                with open(os.path.join(rio.logs, f"akit_{i:016x}.wpilog"), "wb") as f:
                    f.write(b"WPILOG" + b"\0" * 10)
            w = self.watch(rio, wired=True)
            w.poll()
            self.assertIsNone(w.get_status()["last"])
            w.request_fetch()
            w.poll()
            self.assertEqual(w.get_status()["last"]["copied"], R_AUTO_MAX() + 1)

    def test_fetch_from_a_stick_on_this_computer(self):
        stick = os.path.join(self.dir, "ROBOT", "logs")
        os.makedirs(stick)
        fill(stick)
        w = RobotWatch(self.store, finder=lambda team, link_local=True: None)
        w.poll()
        self.assertFalse(w.get_status()["connected"])
        with self.assertRaises(ValueError):
            w.request_fetch()                                          # no robot
        w.request_fetch(os.path.join(self.dir, "ROBOT"))
        w.poll()
        self.assertEqual(w.get_status()["last"]["copied"], 3)

    def test_archive_choice(self):
        season = os.path.join(self.archive, "2026")
        os.makedirs(os.path.join(season, "01 events"))
        self.assertIsNone(archive_ok(self.archive))
        self.assertIn("season", archive_ok(season))
        self.assertIn("exist", archive_ok(os.path.join(self.dir, "nope")))


def R_AUTO_MAX():
    from pitwall.app.robotwatch import AUTO_MAX
    return AUTO_MAX


if __name__ == "__main__":
    unittest.main()
