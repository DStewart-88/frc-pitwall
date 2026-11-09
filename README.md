# Pitwall

Pitwall turns FRC robot logs into match and event reports that anyone on the team can read in a minute. It runs on a pit laptop with no network, reads AdvantageKit `.wpilog` files, and learns the robot's motors and subsystems from a saved robot profile.

The first report is a **power budget**: how much charge the battery delivered each match, and which subsystems and motors used it.

Built by FRC Team 88 (TJ²) for our own robot, and open for any team to use or build on.

## Status

Phase 1: a command-line power report for one match at a time. The graphical app, event
reports and more analyses come next (see the plan in the project docs).

## Quick start

Needs Python 3.11 or newer. No other packages.

```sh
python3 -m pitwall report path/to/event-folder -p profiles/2026-comp.toml -o reports/
```

You can pass log files, folders (searched for `.wpilog` files), or both. Logs where the robot
was never enabled, or that don't contain motor current for this profile, are listed as
"skipped" and left out.

Each log gets two files in `reports/`: an HTML page you open in any browser (it works offline
and can be shared as a single file) and a `.power.json` with every number behind it.

Other commands:

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

## Robot profiles

A profile (`profiles/*.toml`) says what each motor is, which subsystem it belongs to, and
which log keys hold its supply current, applied voltage and speed. A key field can list
several keys; the first one present in a log is used. Robot states (Shooting, Driving, ...)
come from logged signals and let the report split each subsystem's charge by what the robot
was doing.

## Checking the numbers

`tools/validate_bob.py` reruns the Battle of the Bay (Nov 2026) hand analysis and checks
Pitwall against it: per-match energy, charge, mean and peak power, lowest battery voltage,
brownout timing, and subsystem totals. Unit tests run with `python3 -m unittest`.

## License

BSD 3-Clause. See [LICENSE](LICENSE).

## Credits

- [Flashpoint](https://github.com/IgniteRobotics/flashpoint) by Ignite Robotics (FRC 6829), BSD 3-Clause, for ideas on robot description files, hoot-to-wpilog clock alignment, and setup checks. Any code adapted from Flashpoint will carry its copyright notice and be listed in `THIRD_PARTY_LICENSES`.
- [Michroma](https://github.com/googlefonts/Michroma-font), SIL Open Font License, the TJ² display typeface, embedded in reports. See `THIRD_PARTY_LICENSES.md`.
- [AdvantageKit](https://github.com/Mechanical-Advantage/AdvantageKit) and [AdvantageScope](https://github.com/Mechanical-Advantage/AdvantageScope) by Team 6328, whose log format and tools Pitwall builds on.
