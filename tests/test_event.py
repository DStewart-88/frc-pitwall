import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from pitwall.analysis import analyze_logs
from pitwall.cli import main as _main
from tests.test_pitwall import PROFILE, TELE0, make_log

# Motor 2 contributes (5 A x 80 s - 1 A x 80 s) in every full match.
M2_AH = (5 * 80 - 80) / 3600


def main(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return _main(argv)


def m1_ah(amps, enabled_s=160.0):
    return amps * enabled_s / 3600


class EventTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.prof = os.path.join(cls.dir, "p.toml")
        with open(cls.prof, "w") as f:
            f.write(PROFILE.format(mode="signed"))
        a = os.path.join(cls.dir, "2026-01-01_AAA_First-Event")
        b = os.path.join(cls.dir, "2026-02-01_BBB_Second-Event")
        os.makedirs(a)
        os.makedirs(b)

        def mk(folder, stamp, ev, n, **kw):
            make_log(os.path.join(folder, f"akit_{stamp}_{ev.lower()}_q{n}.wpilog"), event=ev, number=n, **kw)

        # Event AAA: three normal matches, a dead robot (Q4), a short match (Q5),
        # and Q3 logged twice (the shorter log must lose).
        mk(a, "26-01-01_10-00-00", "AAA", 1, m1_amps=10.0)
        mk(a, "26-01-01_11-00-00", "AAA", 2, m1_amps=10.0, brownout_at=TELE0 + 130)
        mk(a, "26-01-01_12-00-00", "AAA", 3, m1_amps=10.0)
        mk(a, "26-01-01_12-30-00", "AAA", 3, m1_amps=10.0, tele_end=TELE0 + 50)
        mk(a, "26-01-01_13-00-00", "AAA", 4, m1_amps=0.0)
        mk(a, "26-01-01_14-00-00", "AAA", 5, m1_amps=10.0, tele_end=TELE0 + 100)
        # Q6 logged with no event name (robot rebooted mid-match): must still join AAA.
        make_log(os.path.join(a, "akit_26-01-01_15-00-00_q6.wpilog"), event="", number=6, m1_amps=10.0,
                 tele_end=TELE0 + 60)
        # Event BBB: three matches drawing 2 A more on motor 1, all with brownouts.
        for n in (1, 2, 3):
            mk(b, f"26-02-01_1{n}-00-00", "BBB", n, m1_amps=12.0, brownout_at=TELE0 + 100)
        cls.a, cls.b = a, b
        cls.out = os.path.join(cls.dir, "out")
        cls.cache = os.path.join(cls.dir, "cache")
        rc = main(["event", a, b, "-p", cls.prof, "-o", cls.out, "--cache-dir", cls.cache, "-j", "1"])
        assert rc == 0
        with open(os.path.join(cls.out, "AAA", "event.json")) as f:
            cls.ev_a = json.load(f)
        with open(os.path.join(cls.out, "BBB", "event.json")) as f:
            cls.ev_b = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_files(self):
        for code in ("AAA", "BBB"):
            self.assertTrue(os.path.exists(os.path.join(self.out, code, "index.html")))
        with open(os.path.join(self.out, "BBB", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("Compared with First Event", html)
        self.assertIn("Where the charge goes", html)
        self.assertIn("Charge before the first brownout", html)
        self.assertEqual(html.count('class="donut"'), 1)                 # one donut: this event outside,
        self.assertIn('data-which="prev"', html)                          # the previous event as the inner ring
        self.assertIn('href="matches/akit_26-02-01_11-00-00_bbb_q1.html"', html)
        self.assertTrue(os.path.exists(os.path.join(self.out, "BBB", "matches", "akit_26-02-01_11-00-00_bbb_q1.html")))

    def test_names_from_folders(self):
        self.assertEqual(self.ev_a["name"], "First Event")
        self.assertEqual(self.ev_b["name"], "Second Event")

    def test_one_log_per_match(self):
        labels = [m["label"] for m in self.ev_a["matches"]]
        self.assertEqual(labels, ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "EVENT")))
        q3 = next(m for m in self.ev_a["matches"] if m["label"] == "Q3")
        self.assertEqual(q3["file"], "akit_26-01-01_12-00-00_aaa_q3.wpilog")
        self.assertEqual(len(q3["other_logs"]), 1)

    def test_counted(self):
        self.assertEqual(self.ev_a["counted"], ["Q1", "Q2", "Q3"])
        why = {m["label"]: m["why_not"] for m in self.ev_a["matches"]}
        self.assertIn("typical charge", why["Q4"])
        self.assertIn("Enabled for only", why["Q5"])

    def test_averages(self):
        A = self.ev_a["averages"]
        self.assertEqual(A["n"], 3)
        self.assertAlmostEqual(A["ah"], m1_ah(10) + M2_AH, delta=0.002)
        self.assertAlmostEqual(A["subsystems"]["a"]["ah"], m1_ah(10), delta=0.002)

    def test_brownout_metrics(self):
        B = self.ev_a["brownout"]
        self.assertEqual((B["with_brownout"], B["matches"]), (1, 3))
        self.assertAlmostEqual(B["median_first_left_s"], 10.0, delta=0.1)
        self.assertAlmostEqual(B["median_ah_before_first"], (1500 + 400 - 70) / 3600, delta=0.003)
        self.assertAlmostEqual(B["max_ah_no_brownout"], m1_ah(10) + M2_AH, delta=0.003)
        Bb = self.ev_b["brownout"]
        self.assertEqual((Bb["with_brownout"], Bb["matches"]), (3, 3))
        self.assertAlmostEqual(Bb["median_first_left_s"], 40.0, delta=0.1)

    def test_comparison(self):
        self.assertIsNone(self.ev_a["compare"])
        C = self.ev_b["compare"]
        self.assertEqual(C["code"], "AAA")
        ah = next(r for r in C["rows"] if r["key"] == "ah")
        self.assertAlmostEqual(ah["delta"], m1_ah(2), delta=0.002)
        sub = next(r for r in C["subsystems"] if r["key"] == "a")
        self.assertAlmostEqual(sub["pct"], 0.2, delta=0.01)
        self.assertAlmostEqual(C["brownout"]["first_left"]["delta"], 30.0, delta=0.2)
        texts = " ".join(o["text"] for o in self.ev_b["observations"])
        self.assertIn("30.0 s earlier", texts)
        self.assertIn("Matches with a brownout: 3 of 3 here, 1 of 3 at First Event", texts)

    def test_compare_from_saved_event(self):
        out2 = os.path.join(self.dir, "out2")
        rc = main(["event", self.b, "-p", self.prof, "-o", out2, "--cache-dir", self.cache, "-j", "1",
                   "--compare", os.path.join(self.out, "AAA")])
        self.assertEqual(rc, 0)
        with open(os.path.join(out2, "BBB", "event.json")) as f:
            ev = json.load(f)
        self.assertEqual(ev["compare"]["name"], "First Event")

    def test_include_exclude(self):
        out3 = os.path.join(self.dir, "out3")
        rc = main(["event", self.a, "-p", self.prof, "-o", out3, "--cache-dir", self.cache, "-j", "1",
                   "--include", "q5", "--exclude", "Q1"])
        self.assertEqual(rc, 0)
        with open(os.path.join(out3, "AAA", "event.json")) as f:
            ev = json.load(f)
        self.assertEqual(ev["counted"], ["Q2", "Q3", "Q5"])

    def test_cache_hits(self):
        res = analyze_logs([os.path.join(self.a, f) for f in sorted(os.listdir(self.a))], self.prof,
                           cache_dir=self.cache)
        self.assertTrue(all(r.get("cached") for r in res))
        res2 = analyze_logs([os.path.join(self.a, sorted(os.listdir(self.a))[0])], self.prof, cache_dir=None)
        self.assertFalse(res2[0]["cached"])
        self.assertAlmostEqual(res2[0]["ok"]["power"]["totals"]["ah"], res[0]["ok"]["power"]["totals"]["ah"])

    def test_parallel_matches_serial(self):
        paths = [os.path.join(self.b, f) for f in sorted(os.listdir(self.b))]
        ser = analyze_logs(paths, self.prof, cache_dir=None, jobs=1)
        par = analyze_logs(paths, self.prof, cache_dir=None, jobs=2)
        self.assertEqual([r["ok"]["power"]["totals"]["ah"] for r in ser],
                         [r["ok"]["power"]["totals"]["ah"] for r in par])


if __name__ == "__main__":
    unittest.main()
