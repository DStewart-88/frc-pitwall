"""Install Pitwall for this Windows user: Start-menu and Desktop shortcuts.

Run by install-windows.bat (double-click it). Pitwall runs straight from this folder, so updating
is `git pull` here, or unzipping a newer download over this folder. Nothing needs admin rights.

  install-windows.bat               add the shortcuts
  install-windows.bat --no-desktop  Start menu only
  install-windows.bat --uninstall   remove the shortcuts (your robots and events stay)
"""

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "Pitwall"


def ps_quote(s: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + s.replace("'", "''") + "'"


def powershell(script: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r.stdout.strip()


def windowless_python() -> str:
    """pythonw.exe next to this Python, so Pitwall opens without a console window."""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.isfile(w) else exe


def shortcut_paths(desktop: bool) -> list:
    folders = ["[Environment]::GetFolderPath('Programs')"]
    if desktop:
        folders.append("[Environment]::GetFolderPath('Desktop')")
    out = powershell("; ".join(f"Write-Output ({f})" for f in folders))
    return [os.path.join(line.strip(), NAME + ".lnk") for line in out.splitlines() if line.strip()]


def make_shortcut(path: str, target: str) -> None:
    icon = os.path.join(REPO, "pitwall", "app", "icon.ico")
    script = "; ".join([
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + ps_quote(path) + ")",
        "$s.TargetPath = " + ps_quote(target),
        "$s.Arguments = '-m pitwall app'",
        "$s.WorkingDirectory = " + ps_quote(REPO),       # `-m pitwall` finds the code from here
        "$s.IconLocation = " + ps_quote(icon + ",0"),
        "$s.Description = 'Turn FRC robot logs into match and event reports'",
        "$s.Save()",
    ])
    powershell(script)


def have_paramiko(python: str) -> bool:
    """Fetching logs from the robot over SFTP needs paramiko: install it for this user if it's missing."""
    try:
        if subprocess.run([python, "-c", "import paramiko"], capture_output=True).returncode == 0:
            return True
        print("Installing paramiko (for fetching logs from the robot)...")
        ok = subprocess.run([python, "-m", "pip", "install", "--user", "--quiet", "paramiko"]).returncode == 0
    except OSError:
        ok = False
    if not ok:
        print("  Couldn't install it. Pitwall works without it, except fetching from the robot.")
        print(f"  To add it later: {python} -m pip install paramiko")
        return False
    return True


def main(argv: list) -> int:
    if os.name != "nt":
        print("This installer is for Windows. On Linux, run ./install-linux.sh")
        return 1
    if sys.version_info < (3, 11):
        print(f"Pitwall needs Python 3.11 or newer; this is {sys.version.split()[0]}.")
        print("Get it from https://www.python.org/downloads/ and run this again.")
        return 1
    if not os.path.isfile(os.path.join(REPO, "pitwall", "__init__.py")):
        print("Run install-windows.bat from the frc-pitwall folder.")
        return 1
    uninstall = "--uninstall" in argv
    desktop = "--no-desktop" not in argv
    links = shortcut_paths(desktop=True)
    if uninstall:
        for p in links:
            if os.path.exists(p):
                os.remove(p)
        data = os.path.join(os.environ.get("APPDATA", ""), "Pitwall")
        print(f"Pitwall shortcuts removed. Your robots, events and logs are still in {data}")
        return 0
    if "WindowsApps" in sys.executable:
        print("Note: this is the Microsoft Store Python. Pitwall should work, but the python.org")
        print("installer is the one we test with: https://www.python.org/downloads/")
    target = windowless_python()
    made = []
    for p in links:
        if not desktop and p != links[0]:
            continue
        make_shortcut(p, target)
        made.append(p)
    from pitwall import __version__
    have_paramiko(sys.executable)
    print(f"Pitwall {__version__} installed.")
    for p in made:
        print(f"  shortcut: {p}")
    print("Open Pitwall from the Start menu or the Desktop. It opens in your web browser.")
    print("To update: git pull in this folder (or unzip a newer copy over it), then open Pitwall.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, REPO)
    try:
        raise SystemExit(main(sys.argv[1:]))
    except RuntimeError as e:
        print(f"Couldn't make the shortcuts: {e}")
        raise SystemExit(1)
