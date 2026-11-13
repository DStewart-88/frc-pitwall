"""What a log is and when it was recorded, from what's inside it rather than its file name.

AdvantageKit's file name carries the event code, but the Driver Station keeps sending that
code into practice sessions after an event until someone clears it, and the roboRIO clock
reads a placeholder until the Driver Station syncs it. So:

  kind     "match" (FMS attached, with a match type and number), "practice" (enabled at least
           MIN_ENABLED_S), "boot" (never enabled, or only for a moment), "hoot" / "revlog"
           (motor-controller logs, by file type), or "other"
  when     the local time the log started, from the roboRIO's epoch clock once the log says
           the clock is valid; else from the UTC time stamp in the file name; else unknown
"""

import datetime as dt
import os
import re
import struct
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from ..match import MATCH_TYPES, MIN_ENABLED_S
from ..wpilog import WPILog, WPILogError

# akit_26-11-07_14-07-10_nhalt_q2.wpilog: yy-mm-dd_HH-MM-SS, in UTC (the roboRIO's clock)
AKIT_STAMP = re.compile(r"(\d{2})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")
# Phoenix: NEWTON_Q23_rio_2026-04-30_16-15-32.hoot, folder 2026-04-30_16-15-32 (UTC)
LONG_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")
# REV: REV_20260430_161532.revlog (UTC)
REV_STAMP = re.compile(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})")
# A Phoenix session folder named by the FMS: NEWTON_Q23, NHALT_E7
HOOT_MATCH_DIR = re.compile(r"^([A-Za-z0-9]+)_([PQE])(\d+)$")

EARLIEST = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)   # a clock reading before this isn't real


@dataclass
class LogInfo:
    kind: str                      # match / practice / boot / hoot / revlog / other
    event: str = ""                # event code inside the log (upper case), "" if none
    match: str = ""                # "Q14", "E7", "" for non-match logs
    enabled_s: float = 0.0
    when: Optional[str] = None     # local start time, ISO format, or None when unknown
    when_from: str = ""            # "log clock", "file name" or ""
    error: str = ""                # why a log couldn't be read, if it couldn't

    @property
    def date(self) -> Optional[str]:
        return self.when[:10] if self.when else None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LogInfo":
        return LogInfo(**{k: d.get(k, LogInfo.__dataclass_fields__[k].default)
                          for k in LogInfo.__dataclass_fields__})


def _local(utc: dt.datetime) -> str:
    return utc.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def stamp_from_name(name: str) -> Optional[str]:
    """Local start time from the UTC time stamp in a file or folder name."""
    m = LONG_STAMP.search(name) or REV_STAMP.search(name)
    if m:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
    else:
        m = AKIT_STAMP.search(name)
        if not m:
            return None
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        y += 2000
    try:
        return _local(dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc))
    except ValueError:
        return None


def _enabled_seconds(log: WPILog) -> float:
    t, v = log.series("/DriverStation/Enabled")
    total, on = 0.0, None
    for ti, vi in zip(t, v):
        if vi and on is None:
            on = ti
        elif not vi and on is not None:
            total += ti - on
            on = None
    if on is not None:
        total += log.t_span[1] - on
    return total


def _clock_start(log: WPILog) -> Optional[str]:
    """Local time when the log's clock first reads valid, stepped back to the log's start."""
    tv, vv = log.series("/SystemStats/SystemTimeValid")
    te, ve = log.series("/SystemStats/EpochTimeMicros")
    if not te:
        return None
    t_valid = next((a for a, b in zip(tv, vv) if b), None)
    if t_valid is None:
        return None
    for a, us in zip(te, ve):
        if a >= t_valid and us:
            utc = dt.datetime.fromtimestamp(us / 1e6, dt.timezone.utc)
            if utc < EARLIEST:
                return None
            start = utc - dt.timedelta(seconds=a - log.t_span[0])
            return _local(start)
    return None


def identify_wpilog(path: str, name: str = "") -> LogInfo:
    name = name or os.path.basename(path)
    try:
        log = WPILog(path)
    except (WPILogError, OSError, IndexError, ValueError, struct.error) as e:
        when = stamp_from_name(name)
        return LogInfo(kind="other", error=f"couldn't read it: {e}", when=when,
                       when_from="file name" if when else "")
    event = (log.last_value("/DriverStation/EventName", "") or "").strip().upper()
    mt = MATCH_TYPES.get(int(log.last_value("/DriverStation/MatchType", 0) or 0), "")
    mn = int(log.last_value("/DriverStation/MatchNumber", 0) or 0)
    _, fms_v = log.series("/DriverStation/FMSAttached")
    enabled = _enabled_seconds(log)
    when, src = _clock_start(log), "log clock"
    if when is None:
        when, src = stamp_from_name(name), "file name"
    if when is None:
        src = ""
    if (any(fms_v) or not log.has("/DriverStation/FMSAttached")) and mt and mn:
        kind, match = "match", f"{mt}{mn}"
    else:
        kind, match = ("practice" if enabled >= MIN_ENABLED_S else "boot"), ""
    return LogInfo(kind=kind, event=event, match=match, enabled_s=round(enabled, 1), when=when, when_from=src)


def identify(path: str, rel: str = "") -> LogInfo:
    """Identify a log file. `rel` is its path on the stick: its name and folder are used for
    the file type and time stamps (the file at `path` may be a staging copy)."""
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1] if rel else os.path.basename(path)
    low = name.lower()
    if low.endswith(".wpilog"):
        return identify_wpilog(path, name)
    kind = "hoot" if low.endswith(".hoot") else "revlog" if low.endswith(".revlog") else "other"
    parent = rel.rsplit("/", 2)[-2] if rel.count("/") else ""
    when = stamp_from_name(name) or stamp_from_name(parent)
    info = LogInfo(kind=kind, when=when, when_from="file name" if when else "")
    m = HOOT_MATCH_DIR.match(parent) or HOOT_MATCH_DIR.match(name.split("_rio_")[0].split(".")[0])
    if m and kind == "hoot":
        info.event, info.match = m.group(1).upper(), f"{m.group(2)}{int(m.group(3))}"
    return info


def event_dates(infos: Iterable[LogInfo]) -> Dict[str, List[str]]:
    """{event code: sorted dates of its FMS match logs}: the days the robot was at that event."""
    out: Dict[str, set] = {}
    for info in infos:
        if info.kind == "match" and info.event and info.date:
            out.setdefault(info.event, set()).add(info.date)
    return {k: sorted(v) for k, v in out.items()}
