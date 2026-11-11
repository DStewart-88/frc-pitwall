"""The analysis modules Pitwall runs on every match.

Each module turns one Match into a JSON-ready result (stored in the payload under its name),
and can roll its per-match results up into event figures. Power is the core module: the
event's counting rules (which matches count) and most of the event page come from it, so its
roll-up lives in event.py. Other modules plug in through the hooks below and appear as their
own section on the match and event pages, only when the robot profile sets them up.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import power, scoring


@dataclass(frozen=True)
class Module:
    name: str                       # payload key, e.g. "scoring"
    title: str                      # section title on the pages
    applies: Callable[[Any], bool]  # profile -> does this robot have what the module needs?
    analyze: Callable[[Any], Dict[str, Any]]                                   # Match -> result
    # Event hooks (None for power, whose roll-up is event.py itself):
    summary: Optional[Callable[[Optional[Dict[str, Any]]], Optional[Dict[str, Any]]]] = None
    roll_up: Optional[Callable[[List[Dict[str, Any]]], Dict[str, Any]]] = None
    compare: Optional[Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Optional[Dict[str, Any]]]] = None
    observations: Optional[Callable[[Dict[str, Any]], List[Dict[str, str]]]] = None


POWER = Module("power", "Power", applies=lambda p: True, analyze=power.analyze)
SCORING = Module("scoring", "Scoring", applies=scoring.applies, analyze=scoring.analyze,
                 summary=scoring.match_summary, roll_up=scoring.roll_up, compare=scoring.compare,
                 observations=scoring.observations)

MODULES = (POWER, SCORING)
EXTRA = tuple(m for m in MODULES if m.roll_up is not None)   # modules with their own event section
