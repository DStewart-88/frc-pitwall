"""Log fetch: bring new logs in from the robot's USB stick, sort them into the team archive,
and hand match logs to their Pitwall event. Never changes anything on the stick."""

from .archive import Archive
from .fetch import FetchResult, fetch, summary_lines
from .identify import LogInfo, identify
from .source import LocalSource

__all__ = ["Archive", "FetchResult", "LocalSource", "LogInfo", "fetch", "identify", "summary_lines"]
