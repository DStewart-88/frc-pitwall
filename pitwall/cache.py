"""Analysis cache: a log is analyzed once, then read back from disk.

The key is the SHA-256 of the log file, the SHA-256 of the robot profile, the analysis
version and the current mode. Change any of them and the log is analyzed again, so a cached
result can never be stale. Logs that can't make a report are cached too ("skip" entries),
so a folder of pit and queue logs doesn't get re-read on every run.

Entries are plain JSON files, one per log, in the user's cache folder:
  Linux   ~/.cache/pitwall          (or $XDG_CACHE_HOME/pitwall)
  macOS   ~/Library/Caches/pitwall
  Windows %LOCALAPPDATA%\\pitwall\\cache
Deleting the folder is always safe.
"""

import hashlib
import json
import os
import sys
import tempfile
from typing import Any, Dict, Optional

# Bump when match or power analysis output changes, so old cache entries are ignored.
ANALYSIS_VERSION = 3


def default_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "pitwall", "cache")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/pitwall")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "pitwall")


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def cache_key(log_sha: str, profile_sha: str, current_mode: str) -> str:
    raw = f"{log_sha}|{profile_sha}|v{ANALYSIS_VERSION}|{current_mode}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


class Cache:
    def __init__(self, folder: Optional[str]):
        """folder=None turns the cache off (every log is analyzed)."""
        self.folder = folder
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> str:
        return os.path.join(self.folder, key[:2], key + ".json")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.folder:
            return None
        try:
            with open(self._path(key), encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            self.misses += 1
            return None
        self.hits += 1
        return entry

    def put(self, key: str, entry: Dict[str, Any]) -> None:
        if not self.folder:
            return
        path = self._path(key)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entry, f, separators=(",", ":"))
            os.replace(tmp, path)        # atomic: a crash never leaves half a file
        except OSError:
            pass                         # a cache that can't be written just means slower runs
