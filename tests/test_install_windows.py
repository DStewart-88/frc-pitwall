"""The Windows installer's logic, with PowerShell replaced by a fake (these tests run anywhere)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import install_windows as iw  # noqa: E402


class FakeShell:
    def __init__(self, programs, desktop):
        self.programs, self.desktop = programs, desktop
        self.scripts = []

    def __call__(self, script):
        self.scripts.append(script)
        if "GetFolderPath" in script:
            out = [self.programs] + ([self.desktop] if "'Desktop'" in script else [])
            return "\n".join(out)
        return ""


class InstallWindowsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.programs = os.path.join(self.dir, "Start Menu", "Programs")
        self.desktop = os.path.join(self.dir, "Desktop")
        self.shell = FakeShell(self.programs, self.desktop)

    def run_main(self, *args):
        with mock.patch.object(iw.os, "name", "nt"), mock.patch.object(iw, "powershell", self.shell), \
                mock.patch.object(iw.sys, "executable", os.path.join("Python313", "python.exe")), \
                mock.patch.object(iw.os.path, "isfile", lambda p: True):
            return iw.main(list(args))

    def test_quote(self):
        self.assertEqual(iw.ps_quote("C:\\Team's Laptop\\x"), "'C:\\Team''s Laptop\\x'")

    def test_install_makes_both_shortcuts(self):
        self.assertEqual(self.run_main(), 0)
        made = [s for s in self.shell.scripts if "CreateShortcut" in s]
        self.assertEqual(len(made), 2)
        s = made[0]
        self.assertIn("CreateShortcut('" + os.path.join(self.programs, "Pitwall.lnk") + "')", s)
        self.assertIn("$s.TargetPath = " + iw.ps_quote(os.path.join("Python313", "pythonw.exe")), s)  # no console
        self.assertIn("$s.Arguments = '-m pitwall app'", s)
        self.assertIn("$s.WorkingDirectory = " + iw.ps_quote(iw.REPO), s)
        self.assertIn("icon.ico,0", s)

    def test_no_desktop(self):
        self.assertEqual(self.run_main("--no-desktop"), 0)
        made = [s for s in self.shell.scripts if "CreateShortcut" in s]
        self.assertEqual(len(made), 1)
        self.assertIn("Programs", made[0])

    def test_uninstall(self):
        os.makedirs(self.programs)
        lnk = os.path.join(self.programs, "Pitwall.lnk")
        open(lnk, "w").close()
        with mock.patch.object(iw.os, "name", "nt"), mock.patch.object(iw, "powershell", self.shell):
            self.assertEqual(iw.main(["--uninstall"]), 0)
        self.assertFalse(os.path.exists(lnk))

    def test_refuses_off_windows(self):
        with mock.patch.object(iw.os, "name", "posix"):
            self.assertEqual(iw.main([]), 1)


if __name__ == "__main__":
    unittest.main()
