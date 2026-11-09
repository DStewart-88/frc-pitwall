"""Turn a code scan (and a sample log) into a draft robot profile, and a reviewed draft into TOML.

The draft is plain JSON so the setup page can show it, let the user change it, and send it back.
Each motor key carries a status:
  traced     read from the robot code, and the key is in the sample log
  named      not traced from the code; picked from the log because its name matches the motor
  not-in-log traced from the code, but the sample log doesn't have it (renamed since?)
  unchecked  traced from the code; no sample log to check against
  missing    nothing found; the user picks a key or leaves it out
"""

import re
from typing import Any, Dict, List, Optional, Set

from ..profile import SUPPLY_SUFFIXES
from ..wpilog import WPILog
from .scan import ScanResult

FIELDS = ("supply", "voltage", "velocity", "stator", "connected")
FIELD_SUFFIX = {"supply": SUPPLY_SUFFIXES, "voltage": ("Voltage", "AppliedVolts", "MotorVoltage"),
                "velocity": ("Velocity", "VelocityRotPerSec", "VelocityRadPerSec"),
                "stator": ("StatorCurrent", "StatorCurrentAmps", "CurrentAmps"),
                "connected": ("Connected",)}

GAMES: Dict[str, Dict[str, Any]] = {
    "rebuilt-2026": {
        "label": "2026 REBUILT: Auto, Transition, Shifts 1-4, End Game",
        "auto_s": 20, "teleop_s": 140,
        "periods": [
            {"name": "Auto", "from_left": 160, "to_left": 140, "hub": "active"},
            {"name": "Transition", "from_left": 140, "to_left": 130, "hub": "active"},
            {"name": "Shift 1", "from_left": 130, "to_left": 105, "hub": "inactive_if_named"},
            {"name": "Shift 2", "from_left": 105, "to_left": 80, "hub": "active_if_named"},
            {"name": "Shift 3", "from_left": 80, "to_left": 55, "hub": "inactive_if_named"},
            {"name": "Shift 4", "from_left": 55, "to_left": 30, "hub": "active_if_named"},
            {"name": "End Game", "from_left": 30, "to_left": 0, "hub": "active"},
        ],
    },
    "quarters": {"label": "No game periods: split matches into quarters", "auto_s": 15, "teleop_s": 135, "periods": []},
}
DEFAULT_GAME = "rebuilt-2026"


def _tokens(s: str) -> Set[str]:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    words = re.split(r"[^A-Za-z0-9]+", s.lower())
    stop = {"realoutputs", "supply", "current", "amps", "m", "the", "motor", ""}
    return {w for w in words if w not in stop}


def make_draft(scan: ScanResult, log: Optional[WPILog] = None, robot_name: str = "") -> Dict[str, Any]:
    log_keys: Dict[str, str] = dict(log.entries()) if log else {}
    has = (lambda k: k in log_keys) if log else (lambda k: True)
    claimed: Set[str] = set()
    motors: List[Dict[str, Any]] = []
    for fm in scan.motors:
        keys, status = {}, {}
        for f in FIELDS:
            k = fm.keys.get(f)
            if k:
                keys[f] = k
                status[f] = ("traced" if has(k) else "not-in-log") if log else "unchecked"
                if not log or has(k):
                    claimed.add(k)
        motors.append({"include": True, "name": fm.name, "subsystem": fm.subsystem, "controller": fm.controller,
                       "can_id": fm.can_id, "bus": fm.bus, "keys": keys, "status": status,
                       "source": f"{fm.file}:{fm.line}" if fm.line else fm.file, "key_source": fm.key_source})

    # Drive and steer first, so they get the first palette colors as in every Pitwall report
    rank = {"Drive": 0, "Steer": 1}
    motors.sort(key=lambda m: rank.get(m["subsystem"], 2))

    supply_in_log = sorted(k for k in log_keys if k.endswith(SUPPLY_SUFFIXES) and not k.startswith("/ReplayOutputs"))
    if log:
        # Motors without a usable supply key: try the log's unclaimed supply keys by name
        for m in motors:
            if m["status"].get("supply") == "traced":
                continue
            want = _tokens(m["name"] + " " + m["subsystem"])
            best, best_score = None, 0.0
            for k in supply_in_log:
                if k in claimed:
                    continue
                kt = _tokens(k)
                score = len(want & kt) / max(1, len(want))
                if score > best_score:
                    best, best_score = k, score
            if best and best_score >= 0.5:
                m["keys"]["supply"] = best
                m["status"]["supply"] = "named"
                claimed.add(best)
                stem = best[: -len(next(s for s in SUPPLY_SUFFIXES if best.endswith(s)))]
                for f in ("voltage", "velocity", "stator", "connected"):
                    if m["status"].get(f) == "traced":
                        continue
                    for suf in FIELD_SUFFIX[f]:
                        cand = stem + suf
                        if cand in log_keys and cand not in claimed:
                            m["keys"][f] = cand
                            m["status"][f] = "named"
                            break
            elif "supply" not in m["status"] or m["status"]["supply"] == "not-in-log":
                m["status"]["supply"] = m["status"].get("supply", "missing")
        for m in motors:
            m["status"].setdefault("supply", "missing")
    else:
        for m in motors:
            m["status"].setdefault("supply", "missing")

    unclaimed = [k for k in supply_in_log if k not in claimed]

    # Subsystems: one per code subsystem, in a sensible order (drive first)
    order: List[str] = []
    for m in sorted(motors, key=lambda m: (0 if m["subsystem"] == "Drive" else 1 if m["subsystem"] == "Steer" else 2)):
        if m["subsystem"] not in order:
            order.append(m["subsystem"])
    subsystems = [{"name": s, "needed_in": []} for s in order]

    # Robot states: a "shooting" flag, and driving from the drive wheel speeds
    bools = sorted(k for k, t in log_keys.items() if t == "boolean")
    shoot = [k for k in bools if re.search(r"is_?shooting$", k, re.I)]
    shoot.sort(key=lambda k: (0 if "RobotContainer" in k else 1, len(k)))
    drive_vel = [m["keys"]["velocity"] for m in motors if m["subsystem"] == "Drive" and m["keys"].get("velocity")
                 and has(m["keys"]["velocity"])]
    game = DEFAULT_GAME if (not log or "/DriverStation/GameSpecificMessage" in log_keys) else "quarters"
    return {
        "robot_name": robot_name or "Robot",
        "code_root": scan.root, "log_file": log.path if log else None,
        "motors": motors, "unclaimed": unclaimed, "supply_keys": supply_in_log,
        "subsystems": subsystems,
        "shooting_key": shoot[0] if shoot else "", "shooting_choices": shoot or [k for k in bools if "shoot" in k.lower()],
        "driving_keys": drive_vel,
        "game": game, "games": {k: v["label"] for k, v in GAMES.items()},
        "notes": scan.notes,
        "battery_key_ok": (not log) or ("/SystemStats/BatteryVoltage" in log_keys),
    }


# ---------------------------------------------------------------- TOML

def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def draft_to_toml(d: Dict[str, Any]) -> str:
    """The reviewed draft as a Pitwall robot profile."""
    motors = [m for m in d["motors"] if m.get("include", True) and m["keys"].get("supply")]
    if not motors:
        raise ValueError("No motors with a supply-current key: pick at least one.")
    sub_names: List[str] = []          # in motor order, so drive comes first and gets the first color
    for m in motors:
        if m["subsystem"] not in sub_names:
            sub_names.append(m["subsystem"])
    needed = {s["name"]: s.get("needed_in", []) for s in d.get("subsystems", [])}
    sub_id: Dict[str, str] = {}
    for n in sub_names:
        base, k, sid = _slug(n), 2, _slug(n)
        while sid in sub_id.values():
            sid, k = f"{base}-{k}", k + 1
        sub_id[n] = sid

    game = GAMES.get(d.get("game") or DEFAULT_GAME, GAMES[DEFAULT_GAME])
    states = []
    if d.get("shooting_key"):
        states.append("[[state]]\nname = \"Shooting\"\npriority = 1\nwhen = " + _q(d["shooting_key"]) + "\n")
    if d.get("driving_keys"):
        states.append("[[state]]\nname = \"Driving\"\npriority = 2\nany_above = [\n"
                      + "".join(f"  {_q(k)},\n" for k in d["driving_keys"])
                      + "]\nthreshold = 3.0        # wheel speed (log units) above which the robot counts as driving\n")
    state_names = {"Shooting"} if d.get("shooting_key") else set()
    if d.get("driving_keys"):
        state_names.add("Driving")

    out = [f"# Pitwall robot profile, made by the setup in the Pitwall app.\n# Robot code: {d.get('code_root') or '?'}\n",
           "[robot]", f"name = {_q(d.get('robot_name') or 'Robot')}",
           'battery_voltage = "/SystemStats/BatteryVoltage"', 'enabled = "/DriverStation/Enabled"',
           'autonomous = "/DriverStation/Autonomous"', 'brownout = "/SystemStats/BrownedOut"', "",
           "[game]", f"auto_s = {game['auto_s']}", f"teleop_s = {game['teleop_s']}",
           'alliance_key = "/DriverStation/AllianceStation"',
           'game_data_key = "/DriverStation/GameSpecificMessage"', ""]
    for p in game["periods"]:
        out += ["[[period]]", f"name = {_q(p['name'])}", f"from_left = {p['from_left']}", f"to_left = {p['to_left']}"]
        if p["hub"] != "active":
            out.append(f"hub = {_q(p['hub'])}")
        out.append("")
    out += ["[settings]", "grid_ms = 20", "active_amps = 2.0", "saturation = 0.97", 'current_mode = "signed"', ""]
    out += states
    if states:
        out.append("")
    for i, n in enumerate(sub_names):
        out += ["[[subsystem]]", f"id = {_q(sub_id[n])}", f"name = {_q(n)}", f"color = {i}"]
        ni = [x for x in needed.get(n, []) if x in state_names]
        if ni:
            out.append("needed_in = [" + ", ".join(_q(x) for x in ni) + "]")
        out.append("")
    used: Set[str] = set()
    for m in motors:
        base, k, mid = _slug(m["name"]), 2, _slug(m["name"])
        while mid in used:
            mid, k = f"{base}-{k}", k + 1
        used.add(mid)
        out += ["[[motor]]", f"id = {_q(mid)}", f"name = {_q(m['name'])}", f"subsystem = {_q(sub_id[m['subsystem']])}"]
        if m.get("can_id") is not None:
            out.append(f"can_id = {int(m['can_id'])}")
        if m.get("bus"):
            out.append(f"bus = {_q(m['bus'])}")
        if m.get("controller"):
            out.append(f"model = {_q(m['controller'])}")
        for f in FIELDS:
            if m["keys"].get(f):
                out.append(f"{f} = {_q(m['keys'][f])}")
        out.append("")
    out += ["[[unmeasured]]", 'name = "Control system (roboRIO, radio, cameras, CAN hubs)"', ""]
    return "\n".join(out)
