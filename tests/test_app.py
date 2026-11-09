import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from pitwall.app.server import App, make_server
from pitwall.app.store import Store
from pitwall.app.worker import Worker, list_folder
from pitwall.profile import ProfileError
from tests.test_pitwall import PROFILE, TELE0, make_log


def logs_into(folder, event, numbers, stamp_day="01", m1=10.0, brown=None):
    os.makedirs(folder, exist_ok=True)
    for n in numbers:
        path = os.path.join(folder, f"akit_26-01-{stamp_day}_1{n}-00-00_{event.lower()}_q{n}.wpilog")
        make_log(path, event=event, number=n, m1_amps=m1, brownout_at=brown)
        old = time.time() - 3600                       # looks like a finished file on a stick
        os.utime(path, (old, old))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "data"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_profiles(self):
        pid = self.store.save_profile(PROFILE.format(mode="signed"))
        self.assertEqual(pid, "test-bot")
        self.assertEqual(self.store.save_profile(PROFILE.format(mode="signed")), "test-bot-2")
        self.assertEqual([p["id"] for p in self.store.list_profiles()], ["test-bot", "test-bot-2"])
        with self.assertRaises(ProfileError):
            self.store.save_profile("this is = not [a profile")
        self.assertEqual(self.store.add_builtin_profile("2026-comp"), "2026-comp")

    def test_events(self):
        pid = self.store.save_profile(PROFILE.format(mode="signed"))
        eid = self.store.create_event("Week 1!", pid, ["/nowhere"])
        self.assertEqual(eid, "week-1")
        self.store.save_event(eid, {"exclude": ["Q3"], "bogus": 1})
        ev = self.store.get_event(eid)
        self.assertEqual(ev["exclude"], ["Q3"])
        self.assertNotIn("bogus", ev)
        with self.assertRaises(ProfileError):
            self.store.delete_profile(pid)            # in use
        self.store.delete_event(eid)
        self.assertIsNone(self.store.get_event(eid))


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "data"))
        self.pid = self.store.save_profile(PROFILE.format(mode="signed"))
        self.worker = Worker(self.store, os.path.join(self.dir, "cache"), jobs=1, poll_s=0.2)

    def tearDown(self):
        self.worker.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_import_build_and_compare(self):
        stick_a = os.path.join(self.dir, "stickA")
        stick_b = os.path.join(self.dir, "stickB")
        logs_into(stick_a, "AAA", [1, 2], "01", m1=10.0)
        logs_into(stick_b, "BBB", [1, 2], "02", m1=12.0, brown=TELE0 + 100)
        a = self.store.create_event("", self.pid, [stick_a])
        b = self.store.create_event("Second", self.pid, [stick_b])
        self.assertEqual(self.worker.import_from_sources(a), 2)
        self.assertEqual(self.worker.import_from_sources(a), 0)      # nothing new
        self.worker.import_from_sources(b)
        self.worker.build(a)
        self.worker.build(b)
        self.assertEqual(self.store.get_event(a)["name"], "AAA")         # named from the logs
        with open(os.path.join(self.store.report_dir(b), "event.json")) as f:
            ev = json.load(f)
        self.assertEqual(ev["compare"]["code"], "AAA")
        self.assertEqual(self.worker.get_status(b)["version"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.store.report_dir(b), "index.html")))
        # compare: none
        self.store.save_event(b, {"compare": "none"})
        self.worker.build(b)
        with open(os.path.join(self.store.report_dir(b), "event.json")) as f:
            self.assertIsNone(json.load(f)["compare"])

    def test_watch_picks_up_new_log(self):
        stick = os.path.join(self.dir, "stick")
        logs_into(stick, "AAA", [1], "01")
        eid = self.store.create_event("Watch", self.pid, [stick])
        self.worker.start()
        for _ in range(100):
            if self.worker.get_status(eid)["version"] >= 1:
                break
            time.sleep(0.1)
        self.assertGreaterEqual(self.worker.get_status(eid)["version"], 1)
        # a fresh file: must wait until it stops changing before it's copied
        make_log(os.path.join(stick, "akit_26-01-01_12-00-00_aaa_q2.wpilog"), event="AAA", number=2)
        for _ in range(200):
            with open(os.path.join(self.store.report_dir(eid), "event.json")) as f:
                if len(json.load(f)["matches"]) == 2:
                    break
            time.sleep(0.1)
        with open(os.path.join(self.store.report_dir(eid), "event.json")) as f:
            self.assertEqual(len(json.load(f)["matches"]), 2)

    def test_list_folder(self):
        stick = os.path.join(self.dir, "stick", "inner")
        logs_into(stick, "AAA", [1], "01")
        d = list_folder(os.path.join(self.dir, "stick"))
        self.assertEqual([f["name"] for f in d["folders"]], ["inner"])
        self.assertEqual((d["logs"], d["logs_below"]), (0, 1))


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.app = App(os.path.join(cls.dir, "data"), os.path.join(cls.dir, "cache"), jobs=1, poll_s=0.2)
        cls.httpd, cls.port = make_server(cls.app, 0)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.app.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.app.worker.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def req(self, method, path, body=None, token=True, host=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                   data=None if body is None else json.dumps(body).encode())
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("X-Pitwall-Token", self.app.token)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_flow(self):
        code, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"Welcome to Pitwall", body)
        self.assertEqual(self.req("GET", "/", host="evil.example")[0], 403)
        self.assertEqual(self.req("POST", "/api/profiles", {"builtin": "2026-comp"}, token=False)[0], 403)
        code, body = self.req("POST", "/api/profiles", {"toml": PROFILE.format(mode="signed")})
        self.assertEqual(code, 200)
        pid = json.loads(body)["id"]
        stick = os.path.join(self.dir, "stick")
        logs_into(stick, "AAA", [1, 2], "01")
        self.assertEqual(self.req("POST", "/api/events", {"profile": pid, "sources": []})[0], 400)
        code, body = self.req("POST", "/api/events", {"name": "Test Event", "profile": pid, "sources": [stick]})
        self.assertEqual(code, 200)
        eid = json.loads(body)["id"]
        for _ in range(150):
            st = json.loads(self.req("GET", f"/api/events/{eid}/status")[1])
            if st["version"] >= 1 and st["state"] == "idle":
                break
            time.sleep(0.1)
        code, body = self.req("GET", f"/events/{eid}/")
        self.assertEqual(code, 200)
        self.assertIn(b'id="pw-bar"', body)                 # toolbar injected
        self.assertIn(b"Charge by match", body)
        code, body = self.req("GET", f"/events/{eid}/matches/akit_26-01-01_11-00-00_aaa_q1.html")
        self.assertEqual(code, 200)
        self.assertEqual(self.req("GET", f"/events/{eid}/../../../etc/passwd")[0], 404)
        code, body = self.req("GET", f"/events/{eid}/download")
        self.assertEqual((code, body[:2]), (200, b"PK"))
        self.assertEqual(self.req("GET", f"/events/{eid}/settings")[0], 200)
        self.assertEqual(self.req("POST", f"/api/events/{eid}/settings", {"exclude": ["Q1"]})[0], 200)
        self.assertEqual(self.req("GET", "/profiles")[0], 200)
        self.assertEqual(self.req("GET", f"/profiles/{pid}")[0], 200)
        self.assertEqual(self.req("POST", f"/api/events/{eid}/delete", {})[0], 200)
        self.assertEqual(self.req("GET", f"/events/{eid}/")[0], 404)


if __name__ == "__main__":
    unittest.main()
