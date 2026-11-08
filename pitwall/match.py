"""The shared match model every analysis module works on.

One Match is built per log: a fixed time grid across the match window, the enabled / auto /
teleop masks, match time remaining, battery voltage, the robot state at every tick, quality
flags, and the log's identity (event, match, build).

AdvantageKit writes a value only when it changes, so every signal is put on the grid by
sample-and-hold.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .profile import Profile, ProfileCheck, check_profile, first_present
from .wpilog import WPILog, hold

MATCH_TYPES = {1: "P", 2: "Q", 3: "E"}
PAD_S = 2.0   # grid starts this long before the first enable and ends this long after the last


@dataclass
class Identity:
    file: str
    event: str = ""
    match_type: str = ""      # P / Q / E, or "" for a practice log
    match_number: int = 0
    project: str = ""
    git_sha: str = ""
    git_branch: str = ""
    git_dirty: bool = False
    build_date: str = ""

    @property
    def is_fms(self) -> bool:
        return bool(self.match_type and self.match_number)

    @property
    def label(self) -> str:
        if self.is_fms:
            return f"{self.match_type}{self.match_number}"
        m = re.search(r"(\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", self.file)
        return f"Practice {m.group(1)}" if m else "Practice"


@dataclass
class Block:
    start: float
    end: float
    mode: str   # "auto" or "teleop"


@dataclass
class Match:
    log: WPILog
    profile: Profile
    check: ProfileCheck
    identity: Identity
    dt: float
    grid: List[float]
    enabled: List[bool]
    auto: List[bool]
    blocks: List[Block]
    vbat: List[float]
    time_left: List[Optional[float]]
    state: List[str]
    flags: List[str] = field(default_factory=list)
    silent: Dict[str, str] = field(default_factory=dict)   # motor id -> why it looks silent
    _cache: Dict[str, List[Any]] = field(default_factory=dict, repr=False)

    @property
    def t0(self) -> float:
        """Time of the first enable; reports show time relative to it."""
        return self.blocks[0].start if self.blocks else self.grid[0]

    @property
    def enabled_idx(self) -> List[int]:
        return [i for i, e in enumerate(self.enabled) if e]

    def held(self, key: Optional[str]) -> List[Any]:
        """Values of `key` held on the grid (all None if the key is None or absent)."""
        if key is None or not self.log.has(key):
            return [None] * len(self.grid)
        cached = self._cache.get(key)
        if cached is None:
            t, v = self.log.series(key)
            cached = hold(t, v, self.grid)
            self._cache[key] = cached
        return cached

    def median_interval_ms(self, key: Optional[str]) -> Optional[float]:
        """Median time between samples of `key` inside the match window, in ms."""
        if key is None or not self.log.has(key):
            return None
        t, _ = self.log.series(key)
        lo, hi = self.grid[0], self.grid[-1]
        ts = [x for x in t if lo <= x <= hi]
        if len(ts) < 3:
            return None
        d = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
        return d[len(d) // 2] * 1000.0


def _identity(log: WPILog) -> Identity:
    ident = Identity(file=os.path.basename(log.path))
    ident.event = (log.last_value("/DriverStation/EventName", "") or "").strip()
    mt = log.last_value("/DriverStation/MatchType", 0) or 0
    ident.match_type = MATCH_TYPES.get(int(mt), "")
    ident.match_number = int(log.last_value("/DriverStation/MatchNumber", 0) or 0)
    ident.project = log.last_value("/RealMetadata/ProjectName", "") or ""
    ident.git_sha = log.last_value("/RealMetadata/GitSHA", "") or ""
    ident.git_branch = log.last_value("/RealMetadata/GitBranch", "") or ""
    dirty = log.last_value("/RealMetadata/GitDirty", "") or ""
    ident.git_dirty = dirty.strip().lower() not in ("", "all changes committed", "0", "false")
    ident.build_date = log.last_value("/RealMetadata/BuildDate", "") or ""
    return ident


def _transitions(log: WPILog, key: Optional[str]) -> List[Tuple[float, bool]]:
    if key is None:
        return []
    t, v = log.series(key)
    out: List[Tuple[float, bool]] = []
    for ti, vi in zip(t, v):
        b = bool(vi)
        if not out or out[-1][1] != b:
            out.append((ti, b))
    return out


def build_match(log: WPILog, profile: Profile) -> Match:
    check = check_profile(profile, log)
    ident = _identity(log)
    flags: List[str] = []

    en_key = first_present(log, profile.enabled)
    auto_key = first_present(log, profile.autonomous)
    en_tr = _transitions(log, en_key)
    auto_tr = _transitions(log, auto_key)

    # Enabled blocks, each tagged auto/teleop by the autonomous flag at its start.
    blocks: List[Block] = []
    span_end = log.t_span[1]
    for i, (t, on) in enumerate(en_tr):
        if not on:
            continue
        end = next((t2 for t2, on2 in en_tr[i + 1:] if not on2), span_end)
        auto_on = False
        for ta, va in auto_tr:
            if ta <= t + 1e-6:
                auto_on = va
        blocks.append(Block(t, end, "auto" if auto_on else "teleop"))
    if not blocks:
        raise ValueError(f"{ident.file}: the robot was never enabled in this log")

    dt = profile.grid_ms / 1000.0
    start = max(log.t_span[0], blocks[0].start - PAD_S)
    end = min(span_end, blocks[-1].end + PAD_S)
    n = int((end - start) / dt) + 1
    grid = [start + i * dt for i in range(n)]

    enabled = [False] * n
    auto = [False] * n
    bi = 0
    for i, g in enumerate(grid):
        while bi < len(blocks) and blocks[bi].end <= g:
            bi += 1
        if bi < len(blocks) and blocks[bi].start <= g < blocks[bi].end:
            enabled[i] = True
            auto[i] = blocks[bi].mode == "auto"

    vkey = first_present(log, profile.battery_voltage)
    if vkey is None:
        raise ValueError(f"{ident.file}: no battery voltage key from the profile is in this log")
    tv, vv = log.series(vkey)
    vbat = [x if x is not None else 12.0 for x in hold(tv, vv, grid)]

    # Match time remaining: from the nominal period lengths, so it's continuous (the DS
    # MatchTime signal is whole seconds and jitters).
    time_left: List[Optional[float]] = [None] * n
    if ident.is_fms:
        auto_b = next((b for b in blocks if b.mode == "auto"), None)
        tele_b = next((b for b in blocks if b.mode == "teleop"), None)
        for i, g in enumerate(grid):
            if not enabled[i]:
                continue
            if auto[i] and auto_b is not None:
                time_left[i] = max(0.0, auto_b.start + profile.auto_s - g) + profile.teleop_s
            elif tele_b is not None:
                time_left[i] = max(0.0, tele_b.start + profile.teleop_s - g)
        teleop_blocks = [b for b in blocks if b.mode == "teleop"]
        if len(teleop_blocks) > 1:
            flags.append(f"Robot was disabled and re-enabled {len(teleop_blocks) - 1} time(s) during teleop")
        enabled_s = sum(enabled) * dt
        nominal = profile.auto_s + profile.teleop_s
        if enabled_s < 0.9 * nominal:
            flags.append(f"Enabled for only {enabled_s:.0f} s of a {nominal:.0f} s match")
    else:
        flags.append("Not an FMS match (practice or test log)")

    m = Match(log=log, profile=profile, check=check, identity=ident, dt=dt, grid=grid,
              enabled=enabled, auto=auto, blocks=blocks, vbat=vbat, time_left=time_left,
              state=["Other"] * n, flags=flags)
    m.state = _states(m)
    if log.truncated:
        flags.append("Log ends mid-record (robot power was likely cut)")
    m.silent = _silent_motors(m)
    return m


def _silent_motors(m: Match) -> Dict[str, str]:
    """Motors that look absent from the CAN bus this match.

    A disconnected flag held false for most of the enabled time is decisive. Otherwise, a
    supply key with no new samples during the match is suspicious: AdvantageKit logs only
    changes, and a running motor's supply current is never perfectly constant.
    """
    out: Dict[str, str] = {}
    lo, hi = m.blocks[0].start, m.blocks[-1].end
    idx = m.enabled_idx
    for mo in m.profile.motors:
        keys = m.check.resolved[mo.id]
        ck = keys.get("connected")
        if ck:
            held = m.held(ck)
            down = sum(1 for i in idx if held[i] is False)
            if idx and down / len(idx) > 0.5:
                out[mo.id] = "reported disconnected"
                continue
        sk = keys.get("supply")
        if sk is None:
            continue
        t, _ = m.log.series(sk)
        if not any(lo <= x <= hi for x in t):
            out[mo.id] = "logged no supply current during the match"
    return out


def _states(m: Match) -> List[str]:
    """One robot state per tick: the highest-priority state that is on, else 'Other'."""
    n = len(m.grid)
    out = ["Other"] * n
    assigned = [False] * n
    for st in m.profile.states:
        on = [False] * n
        for k in st.when:
            if m.log.has(k):
                for i, v in enumerate(m.held(k)):
                    if v:
                        on[i] = True
        for k in st.any_above:
            if m.log.has(k):
                for i, v in enumerate(m.held(k)):
                    if v is not None and abs(v) > st.threshold:
                        on[i] = True
        for i in range(n):
            if on[i] and not assigned[i]:
                out[i] = st.name
                assigned[i] = True
    return out
