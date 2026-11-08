# Pitwall

Pitwall turns FRC robot logs into match and event reports that anyone on the team can read in a minute. It runs on a pit laptop with no network, reads AdvantageKit `.wpilog` files, and learns the robot's motors and subsystems from a saved robot profile.

The first report is a **power budget**: how much charge the battery delivered each match, and which subsystems and motors used it.

Built by FRC Team 88 (TJ²) for our own robot, and open for any team to use or build on.

## Status

Early development. Nothing to run yet.

## License

BSD 3-Clause. See [LICENSE](LICENSE).

## Credits

- [Flashpoint](https://github.com/IgniteRobotics/flashpoint) by Ignite Robotics (FRC 6829), BSD 3-Clause, for ideas on robot description files, hoot-to-wpilog clock alignment, and setup checks. Any code adapted from Flashpoint will carry its copyright notice and be listed in `THIRD_PARTY_LICENSES`.
- [AdvantageKit](https://github.com/Mechanical-Advantage/AdvantageKit) and [AdvantageScope](https://github.com/Mechanical-Advantage/AdvantageScope) by Team 6328, whose log format and tools Pitwall builds on.
