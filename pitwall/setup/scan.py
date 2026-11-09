"""Read a robot's Java code and list its motors, with the log keys that hold their data.

Written for WPILib + CTRE Phoenix 6 + AdvantageKit code in the style Team 88 used in 2026:
  - each subsystem class builds its motors:  new TalonFX(Constants.SHOOTER_MAIN, CANBus.roboRIO())
  - CAN IDs are int constants (Constants.java, TunerConstants.java)
  - motor data is logged by @AutoLogOutput getters, e.g. getMainSupplyCurrent() -> /RealOutputs/Shooter/MainSupplyCurrent,
    or by Logger.recordOutput("Key", motor.getSupplyCurrent()...)
  - the swerve drive is the AdvantageKit TalonFX swerve template: ModuleIOTalonFX(TunerConstants.FrontLeft)
    with inputs logged under /Drive/Module0..3
Other styles still give the motor list (names, CAN IDs, buses); their keys are then matched from a log by name.

This is a pattern reader, not a Java compiler: it reads what it recognizes and says what it couldn't.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CONTROLLERS = ("TalonFXS", "TalonFX", "SparkFlex", "SparkMax", "CANSparkFlex", "CANSparkMax")
SIGNALS = {                      # field -> CTRE / REV getter names that read it
    "supply": ("getSupplyCurrent",),
    "voltage": ("getMotorVoltage", "getAppliedOutput"),
    "velocity": ("getVelocity",),
    "stator": ("getStatorCurrent", "getOutputCurrent"),
    "connected": ("isConnected",),
}
SWERVE_POSITIONS = ("FrontLeft", "FrontRight", "BackLeft", "BackRight")
SWERVE_SHORT = {"FrontLeft": "FL", "FrontRight": "FR", "BackLeft": "BL", "BackRight": "BR"}

_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*.*?\*/', re.S)


@dataclass
class FoundMotor:
    var: str                      # field name in the code, e.g. "shooterMain"
    cls: str                      # class it's built in, e.g. "Shooter"
    controller: str               # TalonFX, TalonFXS, SparkMax, ...
    can_id: Optional[int]
    bus: str
    file: str
    line: int
    name: str = ""
    subsystem: str = ""
    keys: Dict[str, str] = field(default_factory=dict)       # field -> log key traced from the code
    key_source: Dict[str, str] = field(default_factory=dict)  # field -> where in the code

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


@dataclass
class ScanResult:
    root: str
    motors: List[FoundMotor]
    notes: List[str]
    files: int


# ---------------------------------------------------------------- text helpers

def strip_comments(text: str) -> str:
    """Blank out comments, keeping strings and line numbers."""
    def repl(m: re.Match) -> str:
        s = m.group(0)
        return s if s.startswith('"') else "\n" * s.count("\n") + " "
    return _COMMENT_OR_STRING.sub(repl, text)


def _match_paren(text: str, i: int, open_c: str = "(", close_c: str = ")") -> int:
    """Index just past the bracket that closes the one at text[i]."""
    depth = 0
    in_str = False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _split_args(s: str) -> List[str]:
    out, depth, cur = [], 0, ""
    for c in s:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def humanize(ident: str) -> str:
    """'m_intakeRollerMainLeft' -> 'Intake roller main left'; 'HotTub' -> 'Hot tub'."""
    s = re.sub(r"^(m_|k_?|s_)", "", ident)
    s = s.replace("_", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    words = s.split()
    if not words:
        return ident
    out = [words[0][:1].upper() + words[0][1:].lower() if not words[0].isupper() else words[0]]
    out += [w if (w.isupper() and len(w) > 1) else w.lower() for w in words[1:]]
    return " ".join(out)


def autolog_key(cls: str, method: str, annotation_args: str) -> str:
    """The key AdvantageKit gives an @AutoLogOutput member."""
    m = re.search(r'key\s*=\s*"([^"]+)"', annotation_args or "")
    if m:
        return "/RealOutputs/" + m.group(1).lstrip("/")
    name = method
    if name.startswith("get") and len(name) > 3 and name[3].isupper():
        name = name[3:]
    return f"/RealOutputs/{cls}/{name[:1].upper() + name[1:]}"


# ---------------------------------------------------------------- scanning

def java_files(root: str) -> List[str]:
    base = os.path.join(root, "src", "main", "java")
    if not os.path.isdir(base):
        base = root
    out = []
    for r, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ("build", "bin", ".gradle", "test", "vendordeps") and not d.startswith(".")]
        out += [os.path.join(r, f) for f in files if f.endswith(".java")]
    return sorted(out)


def scan(root: str) -> ScanResult:
    files = java_files(root)
    notes: List[str] = []
    texts: Dict[str, str] = {}
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                texts[f] = strip_comments(fh.read())
        except OSError:
            continue
    if not texts:
        return ScanResult(root, [], ["No Java files found. Pick the folder that holds build.gradle."], 0)

    # Int constants and CAN bus definitions, by bare and Class-qualified name
    ints: Dict[str, int] = {}
    buses: Dict[str, str] = {}
    class_of: Dict[str, str] = {}
    for f, t in texts.items():
        cm = re.search(r"\b(?:class|interface|enum)\s+(\w+)", t)
        cls = cm.group(1) if cm else os.path.basename(f)[:-5]
        class_of[f] = cls
        for m in re.finditer(r"\bfinal\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;", t):
            ints.setdefault(m.group(1), int(m.group(2)))
            ints[f"{cls}.{m.group(1)}"] = int(m.group(2))
        for m in re.finditer(r"\bCANBus\s+(\w+)\s*=\s*new\s+CANBus\s*\(\s*\"([^\"]*)\"", t):
            buses[m.group(1)] = m.group(2) or "rio"
            buses[f"{cls}.{m.group(1)}"] = m.group(2) or "rio"

    def resolve_id(expr: str) -> Optional[int]:
        expr = expr.strip()
        if re.fullmatch(r"-?\d+", expr):
            return int(expr)
        if expr in ints:
            return ints[expr]
        tail = expr.split(".")[-1]
        return ints.get(tail)

    def resolve_bus(expr: Optional[str]) -> str:
        if not expr:
            return "rio"
        e = expr.strip()
        if "roboRIO" in e or e in ('"rio"', '""'):
            return "rio"
        m = re.fullmatch(r'"([^"]*)"', e)
        if m:
            return m.group(1) or "rio"
        m = re.search(r'new\s+CANBus\s*\(\s*"([^"]*)"', e)
        if m:
            return m.group(1) or "rio"
        if e in buses:
            return buses[e]
        return buses.get(e.split(".")[-1], e)

    motors: List[FoundMotor] = []
    swerve_io_classes = set()
    for f, t in texts.items():
        cls = class_of[f]
        for m in re.finditer(r"\bnew\s+(" + "|".join(CONTROLLERS) + r")\s*\(", t):
            start = m.end() - 1
            end = _match_paren(t, start)
            args = _split_args(t[start + 1:end - 1])
            before = t[:m.start()]
            vm = re.search(r"(\w+)\s*=\s*$", before)
            if not vm or not args:
                continue
            var = vm.group(1)
            line = t.count("\n", 0, m.start()) + 1
            if re.search(r"\bconstants\.(Drive|Steer)MotorId\b", args[0]):
                swerve_io_classes.add(cls)       # swerve template IO: expanded per module below
                continue
            bus_arg = args[1] if len(args) > 1 and not re.fullmatch(r"\s*MotorType\.\w+\s*", args[1]) else None
            fm = FoundMotor(var=var, cls=cls, controller=m.group(1), can_id=resolve_id(args[0]),
                            bus=resolve_bus(bus_arg), file=os.path.relpath(f, root), line=line)
            if fm.can_id is None:
                if re.fullmatch(r"[a-z]\w*", args[0].strip()):
                    continue        # a helper that builds a motor from a parameter, not a motor itself
                notes.append(f"{fm.file}:{line}: couldn't work out the CAN ID of {var} ({args[0]}).")
            fm.name = humanize(var)
            fm.subsystem = humanize(cls)
            _trace_keys(fm, t, cls)
            motors.append(fm)

    motors += _swerve(texts, class_of, ints, buses, swerve_io_classes, root, resolve_bus)

    # Same CAN ID twice on one bus is a code or wiring problem worth saying out loud
    seen: Dict[Tuple[str, int], FoundMotor] = {}
    for fm in motors:
        if fm.can_id is None:
            continue
        k = (fm.bus, fm.can_id)
        if k in seen:
            notes.append(f"CAN ID {fm.can_id} on bus {fm.bus} is used by both {seen[k].name} and {fm.name}.")
        seen[k] = fm
    # Unique names
    counts: Dict[str, int] = {}
    for fm in motors:
        counts[fm.name] = counts.get(fm.name, 0) + 1
    for fm in motors:
        if counts[fm.name] > 1:
            fm.name = f"{fm.subsystem} {fm.name[0].lower() + fm.name[1:]}"
    return ScanResult(root, motors, notes, len(texts))


def _methods(text: str):
    """(annotation args or None, method name, body) for every @AutoLogOutput method."""
    pat = re.compile(r"@AutoLogOutput\s*(\(([^)]*)\))?\s*(?:@\w+\s*)*(?:(?:public|private|protected|static|final|synchronized)\s+)*"
                     r"([\w<>\[\],.? ]+?)\s+(\w+)\s*\(\s*\)\s*(?:throws\s+[\w., ]+)?\{")
    for m in pat.finditer(text):
        body_start = m.end() - 1
        body_end = _match_paren(text, body_start, "{", "}")
        yield (m.group(2) or "", m.group(4), text[body_start:body_end], text.count("\n", 0, m.start()) + 1,
               m.group(3).strip().split()[-1])


def _trace_keys(fm: FoundMotor, text: str, cls: str) -> None:
    """Find the log keys that read this motor's signals."""
    var = re.escape(fm.var)
    cands: Dict[str, List[Tuple[int, str, str]]] = {}
    other_vars = set(re.findall(r"(\w+)\s*=\s*new\s+(?:" + "|".join(CONTROLLERS) + r")\s*\(", text)) - {fm.var}

    def consider(key: str, code: str, where: str, rtype: str = "") -> None:
        uses_other = any(re.search(r"\b" + re.escape(o) + r"\s*\.", code) for o in other_vars)
        is_bool = rtype in ("boolean", "Boolean")
        for fld, getters in SIGNALS.items():
            for g in getters:
                if re.search(r"\b" + var + r"\s*\.\s*" + g + r"\s*\(", code):
                    if fld == "connected":
                        # Only a flag that is exactly "this motor is connected": a combined flag, or one
                        # that also needs isAlive(), would make a working motor look missing.
                        calls = re.findall(r"\.\s*(\w+)\s*\(", code)
                        if uses_other or any(c != "isConnected" for c in calls):
                            continue
                    elif is_bool:
                        continue                     # e.g. IsStalled: a test on the signal, not the signal
                    # Rank: a plain read of this one signal beats a computed value
                    others = sum(1 for g2 in [x for v in SIGNALS.values() for x in v] if g2 != g and g2 in code)
                    score = (5 if not uses_other else 0) + (3 if others == 0 else 0) - len(code) / 400
                    leaf = key.rsplit("/", 1)[-1].lower()
                    hint = {"supply": "supply", "voltage": "volt", "velocity": "veloc", "stator": "current",
                            "connected": "connect"}[fld]
                    if hint in leaf:
                        score += 2
                    if fld == "stator" and "supply" in leaf:
                        score -= 10
                    cands.setdefault(fld, []).append((score, key, where))

    for ann, name, body, line, rtype in _methods(text):
        consider(autolog_key(cls, name, ann), body, f"{fm.file}:{line}", rtype)
    for m in re.finditer(r"Logger\.recordOutput\s*\(", text):
        end = _match_paren(text, m.end() - 1)
        args = _split_args(text[m.end():end - 1])
        if len(args) >= 2:
            km = re.fullmatch(r'"([^"]+)"', args[0])
            if km:
                consider("/RealOutputs/" + km.group(1).lstrip("/"), args[1],
                         f"{fm.file}:{text.count(chr(10), 0, m.start()) + 1}")
    for fld, lst in cands.items():
        lst.sort(key=lambda x: -x[0])
        fm.keys[fld] = lst[0][1]
        fm.key_source[fld] = lst[0][2]


def _swerve(texts, class_of, ints, buses, io_classes, root, resolve_bus) -> List[FoundMotor]:
    """The AdvantageKit TalonFX swerve template: 4 modules x (drive, steer)."""
    if not io_classes:
        return []
    out: List[FoundMotor] = []
    # Which IO class each position uses, in the order the modules are passed to Drive (FL, FR, BL, BR)
    used: Dict[str, str] = {}
    for f, t in texts.items():
        for m in re.finditer(r"new\s+(" + "|".join(map(re.escape, io_classes)) + r")\s*\(\s*\w+\.(\w+)\s*\)", t):
            if m.group(2) in SWERVE_POSITIONS:
                used.setdefault(m.group(2), m.group(1))
    if not used:
        return out
    # Inputs fields from the IO interface (ModuleIOInputs) and the processInputs prefix (Module.java)
    fields: List[str] = []
    prefix = "Drive/Module"
    bus = "rio"
    for f, t in texts.items():
        if re.search(r"class\s+ModuleIOInputs\b", t):
            fields = re.findall(r"public\s+(?:double|boolean)\s+(\w+)\s*=", t)
        pm = re.search(r'processInputs\s*\(\s*"([^"]*Module)"\s*\+', t)
        if pm:
            prefix = pm.group(1)
        if class_of[f] in io_classes:
            bm = re.search(r"new\s+TalonFXS?\s*\(\s*constants\.DriveMotorId\s*,\s*([^)]+?)\)", t)
            if bm:
                bus = resolve_bus(bm.group(1))
    tuner_file = next((os.path.relpath(f, root) for f, c in class_of.items() if c == "TunerConstants"), "TunerConstants.java")

    def keys_for(role: str, idx: int) -> Dict[str, str]:
        k: Dict[str, str] = {}
        role_fields = [x for x in fields if x.startswith(role)]
        for x in role_fields:
            leaf = x[len(role):]
            key = f"/{prefix}{idx}/{x[0].upper() + x[1:]}"
            if "SupplyCurrent" in leaf:
                k["supply"] = key
            elif leaf in ("AppliedVolts", "Voltage", "AppliedVoltage"):
                k["voltage"] = key
            elif leaf.startswith("Velocity"):
                k["velocity"] = key
            elif leaf in ("CurrentAmps", "StatorCurrentAmps"):
                k["stator"] = key
            elif leaf == "Connected":
                k["connected"] = key
        return k

    for idx, pos in enumerate(SWERVE_POSITIONS):
        if pos not in used:
            continue
        for role, kind, rkey in (("drive", "Drive", "Drive"), ("turn", "Steer", "Steer")):
            cid = ints.get(f"k{pos}{rkey}MotorId")
            fm = FoundMotor(var=f"{pos}.{kind}", cls="Drive", controller="TalonFXS" if used[pos].endswith("FXS") else "TalonFX",
                            can_id=cid, bus=bus, file=tuner_file, line=0,
                            name=f"{kind} {SWERVE_SHORT[pos]}", subsystem=kind)
            fm.keys = keys_for(role, idx)
            fm.key_source = {k: f"swerve module {idx} inputs" for k in fm.keys}
            out.append(fm)
    return out
