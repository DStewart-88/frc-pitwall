# Pitwall

Pitwall turns FRC robot logs into match and event reports that anyone on the team can read in a minute. It runs on a pit laptop with no network, reads AdvantageKit `.wpilog` files, and learns the robot's motors and subsystems from a saved robot profile.

Each report has a section per module:

- **Power**: how much charge the battery delivered each match, which subsystems and motors used it, and when the battery gave out.
- **Scoring**: how many balls the robot shot (hub and shuttle shots separately), how fast, and in which shift. Shots are counted from the flywheel speed, so no robot code change is needed.

A module only appears when the robot profile sets it up.

Built by FRC Team 88 (TJ²) for our own robot, and open for any team to use or build on.

## Status

Phase 3 in progress: the app works on Linux (install, robot setup from the code, events, folder
watching, reports). A Windows installer is in, not yet tried on a Windows machine. The command
line still works too.

## The app

**Install on Linux** (Python 3.11 or newer; no other packages, no root):

```sh
git clone https://github.com/DStewart-88/frc-pitwall
cd frc-pitwall
./install-linux.sh
```

**Install on Windows** (for the pit laptop):

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/) (tick
   "Add python.exe to PATH").
2. Get Pitwall: `git clone https://github.com/DStewart-88/frc-pitwall`, or download the ZIP from
   GitHub and unzip it somewhere permanent, such as Documents.
3. Double-click **install-windows.bat** in the frc-pitwall folder. It adds Pitwall to the Start
   menu and the Desktop (`--no-desktop` for the Start menu only; `--uninstall` removes them).
   If Windows says it protected your PC, choose More info, then Run anyway.

Pitwall opens without a console window; its messages go to `%APPDATA%\Pitwall\pitwall.log`. To
update, `git pull` (or unzip a newer download over the folder), then open Pitwall.

On either system, open **Pitwall** from the menu. It opens in your browser, runs only on this computer and
needs no internet. Opening it again while it's running just brings the page back; **Quit** in the
page stops it. To update, `git pull` in the frc-pitwall folder, then open Pitwall: a running copy
with older code is restarted on the new code, and older reports are redrawn. `./install-linux.sh --uninstall`
removes the menu entry and keeps your data. (Without installing: `python3 -m pitwall app`.)

1. **Set up your robot** once: pick the robot code folder and a folder of its logs. Pitwall reads
   the Java to find every motor (name, CAN ID, bus) and the log keys its data is saved under,
   checks them against a log, and shows you a table to confirm. Rename motors, group them the way
   the team talks about the robot, untick anything that isn't on it, and save. Motors whose keys
   it couldn't trace from the code are matched in the log by name and marked so you can check them.
2. **New event**: pick the folder with the logs (the USB stick from the robot, or the folder the
   logs are copied to). Pitwall copies the logs, builds the event report, and keeps watching the
   folder. Each new match shows up on its own, usually within ten seconds of the file landing.
3. On the event page: **Add logs** from another folder, **Settings** (name, which matches count,
   which event to compare with), and **Download report** (a zip you can send anyone; it opens in
   any browser).

The app keeps its profiles, copied logs and reports in your user data folder
(`~/.local/share/pitwall` on Linux, `%APPDATA%\Pitwall` on Windows,
`~/Library/Application Support/Pitwall` on a Mac). Nothing is written to the robot code.

## Command line

Needs Python 3.11 or newer. No other packages. (On Windows, type `py` instead of `python3`.)

**Event report**: point it at one or more event folders.

```sh
python3 -m pitwall event "logs/River Rage" "logs/NERD" "logs/Battle of the Bay" -p 2026-comp -o events/
```

Each event gets a folder, such as `events/NHALT/`, holding:
- `index.html`: the event report. Open it in any browser; it works offline.
- `matches/`: one report per log. The event report links to them.
- `event.json`: the numbers, which a later event uses for its comparison.

Events in the same run are compared with the event before them. To compare with an event from
an earlier run, add `--compare events/NHALT`.

**Match reports only**: `python3 -m pitwall report path/to/logs -p 2026-comp -o reports/`

You can pass log files, folders (searched for `.wpilog` files), or both. Logs where the robot
was never enabled, or that don't contain motor current for this profile, are listed as
"skipped" and left out.

Each log is analyzed once and kept in a cache, so running again after the next match only
reads the new log. `--no-cache` turns that off.

### Which matches count

Every match is shown, but the event averages leave out matches that weren't normal: practice
matches, matches where the robot was enabled for under 90% of the time, matches with a motor
missing from the CAN bus, and matches that drew under half the event's typical charge (a dead
robot left enabled). The report says why for each one. `--exclude Q12` and `--include Q2`
override that by hand. If a match has more than one log, the one with the most enabled time is
used.

Event names come from the folder name (`2026-10-07_NHALT_Battle-Of-the-Bay` becomes "Battle
Of the Bay"), or set one with `--name NHALT="Battle of the Bay"`.

### Other commands

```sh
python3 -m pitwall check LOG -p PROFILE   # does the profile match this log?
python3 -m pitwall keys LOG [FILTER]      # list what a log contains
```

## How the power numbers are made

- Every motor is alone on its power-distribution channel, so its logged **supply current**
  is its draw from the battery. Charge (Ah) adds that up over the time the robot is enabled,
  on a 20 ms grid. Energy (Wh) multiplies by battery voltage first.
- A braking motor's supply current is negative: it pushes charge back onto the power bus,
  where other motors use it. Pitwall subtracts it and reports it as "returned". (`--abs`
  counts it as draw instead, which is how the 2026 hand analyses were done.)
- The roboRIO, radio, cameras, CANivore and Pigeon aren't logged, so totals are a few amps low.
- Totals are reliable; peaks are "as logged". A motor whose current is logged slowly misses
  short spikes, and the report flags it.

## How shots are counted

- Each ball pulls the flywheel speed down by about 5-6 rps for roughly 40 ms. While the shoot
  command runs and the feeder gate is open, Pitwall counts a shot each time the tracking error
  (speed minus setpoint) drops sharply. It's the October 2026 shot-counter logic, with one
  change: dips are judged against the flywheel's own running level instead of the setpoint,
  because from October the flywheel runs 1.5-2 rps above its setpoint while feeding.
- Each shot is filed by what the robot was aiming at: hub, shuttle mid (from the neutral zone)
  or shuttle full (from the opponent's zone).
- **These are shots, not scored balls**: misses count. Without video to check against, counts
  carry about ±25% uncertainty; comparisons between matches and events are more reliable.
- BPS is given two ways: while balls are flowing (inside bursts of 4 or more), and per second
  of holding the trigger.

## Robot profiles

A profile (a `.toml` file; the example is `pitwall/profiles/2026-comp.toml`) says what each motor is, which subsystem it belongs to, and
which log keys hold its supply current, applied voltage and speed. A key field can list
several keys; the first one present in a log is used. Robot states (Shooting, Driving, ...)
come from logged signals and let the report split each subsystem's charge by what the robot
was doing.

The `[[period]]` entries describe the game's match periods by match time left. For 2026 these
are Auto, Transition, Alliance Shifts 1-4 and End Game, and each alliance shift says whether
our hub is active in it, based on the game data (which alliance's hub goes inactive first) and
our alliance station. Reports then break current down by shift and by hub active/inactive.
Without periods, reports fall back to quarters of the enabled time.

The `[scoring]` section turns on shot counting: the flywheel speed and setpoint keys, the
"shooting" signal, and optionally the feeder gate and "aiming at our goal" keys. Detector
settings (dip size, lockout, ...) can be changed there too; the defaults are in
`pitwall/profile.py`. Robot setup in the app picks these keys for you.

## Checking the numbers

`tools/validate_bob.py` reruns the Battle of the Bay (Nov 2026) hand analysis and checks
Pitwall against it: per-match energy, charge, mean and peak power, lowest battery voltage and
brownout timing, and the event roll-up (which matches count, averages, subsystem totals,
matches with a brownout, time left at the first brownout). Unit tests run with `python3 -m unittest`.

## License

BSD 3-Clause. See [LICENSE](LICENSE).

## Credits

- [Flashpoint](https://github.com/IgniteRobotics/flashpoint) by Ignite Robotics (FRC 6829), BSD 3-Clause, for ideas on robot description files, hoot-to-wpilog clock alignment, and setup checks. Any code adapted from Flashpoint will carry its copyright notice and be listed in `THIRD_PARTY_LICENSES`.
- [Michroma](https://github.com/googlefonts/Michroma-font), SIL Open Font License, the TJ² display typeface, embedded in reports. See `THIRD_PARTY_LICENSES.md`.
- [AdvantageKit](https://github.com/Mechanical-Advantage/AdvantageKit) and [AdvantageScope](https://github.com/Mechanical-Advantage/AdvantageScope) by Team 6328, whose log format and tools Pitwall builds on.
