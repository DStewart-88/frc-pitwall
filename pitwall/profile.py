"""Robot profile: what each logged motor is, and which log keys hold its data.

A profile is a TOML file (see profiles/2026-comp.toml). Every key field may be a single
string or a list of strings: the first key present in a log is used, so one profile can
cover logs from before and after a code change renamed a key.
"""

import tomllib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .wpilog import WPILog

KeyList = List[str]


def _keys(value) -> KeyList:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


@dataclass
class Motor:
    id: str
    name: str
    subsystem: str
    supply: KeyList
    voltage: KeyList = field(default_factory=list)       # applied (output) volts
    velocity: KeyList = field(default_factory=list)
    stator: KeyList = field(default_factory=list)
    connected: KeyList = field(default_factory=list)
    can_id: Optional[int] = None
    bus: str = ""
    model: str = ""
    stall_below: float = 1.0   # |velocity| below this (in the key's own units) counts as stalled


@dataclass
class Subsystem:
    id: str
    name: str
    color: int = 0                     # palette slot, fixed so a subsystem looks the same everywhere
    needed_in: List[str] = field(default_factory=list)  # states where drawing current is expected


@dataclass
class State:
    name: str
    priority: int
    when: KeyList = field(default_factory=list)   # boolean keys; state is on when any is true
    any_above: KeyList = field(default_factory=list)  # numeric keys; on when any |value| > threshold
    threshold: float = 0.0


HUB_RULES = ("active", "inactive_if_named", "active_if_named")


@dataclass
class Period:
    """A stretch of the match the game defines (2026: Auto, Transition, Shifts 1-4, End Game).

    Bounds are match time left in seconds: the period covers to_left < time left <= from_left.
    `hub` says whether our goal is active in it:
      "active"             always
      "inactive_if_named"  inactive when the game data names our alliance, else active
      "active_if_named"    active when the game data names our alliance, else inactive
    """
    name: str
    from_left: float
    to_left: float
    hub: str = "active"


@dataclass
class Unmeasured:
    name: str
    estimate_amps: float = 0.0


@dataclass
class Profile:
    name: str
    path: str
    project: str = ""
    battery_voltage: KeyList = field(default_factory=lambda: ["/SystemStats/BatteryVoltage"])
    enabled: KeyList = field(default_factory=lambda: ["/DriverStation/Enabled"])
    autonomous: KeyList = field(default_factory=lambda: ["/DriverStation/Autonomous"])
    brownout: KeyList = field(default_factory=lambda: ["/SystemStats/BrownedOut"])
    auto_s: float = 20.0
    teleop_s: float = 140.0
    periods: List[Period] = field(default_factory=list)   # empty: reports use quarters
    alliance_key: KeyList = field(default_factory=lambda: ["/DriverStation/AllianceStation"])
    game_data_key: KeyList = field(default_factory=lambda: ["/DriverStation/GameSpecificMessage"])
    grid_ms: float = 20.0
    active_amps: float = 2.0
    saturation: float = 0.98
    current_mode: str = "signed"   # "signed" (correct) or "abs" (reproduces older hand analyses)
    subsystems: List[Subsystem] = field(default_factory=list)
    motors: List[Motor] = field(default_factory=list)
    states: List[State] = field(default_factory=list)
    unmeasured: List[Unmeasured] = field(default_factory=list)

    def subsystem(self, sid: str) -> Subsystem:
        for s in self.subsystems:
            if s.id == sid:
                return s
        raise KeyError(sid)


class ProfileError(Exception):
    pass


def load_profile(path: str) -> Profile:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    robot = data.get("robot", {})
    game = data.get("game", {})
    settings = data.get("settings", {})
    p = Profile(name=robot.get("name", path), path=path, project=robot.get("project", ""))
    for attr in ("battery_voltage", "enabled", "autonomous", "brownout"):
        if attr in robot:
            setattr(p, attr, _keys(robot[attr]))
    p.auto_s = float(game.get("auto_s", p.auto_s))
    p.teleop_s = float(game.get("teleop_s", p.teleop_s))
    for attr in ("alliance_key", "game_data_key"):
        if attr in game:
            setattr(p, attr, _keys(game[attr]))
    for per in data.get("period", []):
        hub = per.get("hub", "active")
        if hub not in HUB_RULES:
            raise ProfileError(f"{path}: period {per.get('name')!r} hub must be one of {', '.join(HUB_RULES)}")
        p.periods.append(Period(name=per["name"], from_left=float(per["from_left"]),
                                to_left=float(per["to_left"]), hub=hub))
    p.periods.sort(key=lambda x: -x.from_left)
    for a, b in zip(p.periods, p.periods[1:]):
        if b.from_left > a.to_left + 1e-9:
            raise ProfileError(f"{path}: periods {a.name!r} and {b.name!r} overlap")
    p.grid_ms = float(settings.get("grid_ms", p.grid_ms))
    p.active_amps = float(settings.get("active_amps", p.active_amps))
    p.saturation = float(settings.get("saturation", p.saturation))
    p.current_mode = settings.get("current_mode", p.current_mode)
    if p.current_mode not in ("signed", "abs"):
        raise ProfileError(f"{path}: current_mode must be 'signed' or 'abs'")

    for s in data.get("subsystem", []):
        p.subsystems.append(Subsystem(id=s["id"], name=s.get("name", s["id"]),
                                      color=int(s.get("color", 0)),
                                      needed_in=list(s.get("needed_in", []))))
    sub_ids = {s.id for s in p.subsystems}
    seen = set()
    for m in data.get("motor", []):
        mid = m["id"]
        if mid in seen:
            raise ProfileError(f"{path}: motor id {mid!r} used twice")
        seen.add(mid)
        if m["subsystem"] not in sub_ids:
            raise ProfileError(f"{path}: motor {mid!r} names unknown subsystem {m['subsystem']!r}")
        p.motors.append(Motor(
            id=mid, name=m.get("name", mid), subsystem=m["subsystem"],
            supply=_keys(m.get("supply")), voltage=_keys(m.get("voltage")),
            velocity=_keys(m.get("velocity")), stator=_keys(m.get("stator")),
            connected=_keys(m.get("connected")), can_id=m.get("can_id"),
            bus=m.get("bus", ""), model=m.get("model", ""),
            stall_below=float(m.get("stall_below", 1.0))))
        if not p.motors[-1].supply:
            raise ProfileError(f"{path}: motor {mid!r} has no supply key")
    for st in data.get("state", []):
        p.states.append(State(name=st["name"], priority=int(st.get("priority", 99)),
                              when=_keys(st.get("when")), any_above=_keys(st.get("any_above")),
                              threshold=float(st.get("threshold", 0.0))))
    p.states.sort(key=lambda s: s.priority)
    for u in data.get("unmeasured", []):
        p.unmeasured.append(Unmeasured(name=u["name"], estimate_amps=float(u.get("estimate_amps", 0.0))))
    for s in p.subsystems:
        for st in s.needed_in:
            if st not in {x.name for x in p.states} | {"Other"}:
                raise ProfileError(f"{path}: subsystem {s.id!r} needed_in names unknown state {st!r}")
    return p


def first_present(log: WPILog, keys: KeyList) -> Optional[str]:
    for k in keys:
        if log.has(k):
            return k
    return None


@dataclass
class ProfileCheck:
    """How a profile lines up with one log."""
    resolved: Dict[str, Dict[str, Optional[str]]]   # motor id -> field -> key used (or None)
    missing: List[str]          # "motor.field" whose keys are all absent
    unassigned: List[str]       # supply-current keys in the log no motor claims
    silent: List[str]           # motor ids whose supply key exists but has no samples

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unassigned or self.silent)


SUPPLY_SUFFIXES = ("SupplyCurrent", "SupplyCurrentAmps")


def check_profile(profile: Profile, log: WPILog) -> ProfileCheck:
    resolved: Dict[str, Dict[str, Optional[str]]] = {}
    missing: List[str] = []
    silent: List[str] = []
    claimed = set()
    for m in profile.motors:
        r = {}
        for fld in ("supply", "voltage", "velocity", "stator", "connected"):
            keys = getattr(m, fld)
            k = first_present(log, keys)
            r[fld] = k
            if keys and k is None:
                missing.append(f"{m.id}.{fld}")
            for key in keys:
                claimed.add(key)
        if r["supply"] is not None and log.sample_count(r["supply"]) == 0:
            silent.append(m.id)
        resolved[m.id] = r
    unassigned = [n for n, _ in log.entries()
                  if n.endswith(SUPPLY_SUFFIXES) and n not in claimed]
    unassigned = [n for n in unassigned if not n.startswith("/ReplayOutputs")]
    for attr in ("battery_voltage", "enabled"):
        if first_present(log, getattr(profile, attr)) is None:
            missing.append(f"robot.{attr}")
    return ProfileCheck(resolved, missing, unassigned, silent)
