"""Where logs are fetched from. A source can only list and read: there is no way to write,
rename or delete through it, so a fetch can never change the robot's USB stick.

LocalSource is a folder on this computer: the robot's stick plugged into the laptop, or any
folder of logs. (The robot over SFTP is a second source with the same two methods.)
"""

import os
import time
from dataclasses import dataclass
from typing import BinaryIO, List

LOG_TYPES = (".wpilog", ".hoot", ".revlog")


@dataclass(frozen=True)
class RemoteFile:
    rel: str          # path inside the source, "/"-separated
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]


class Source:
    label = "source"
    mtime_reliable = True      # file times are real times (not true on the robot before the DS syncs it)

    def list(self) -> List[RemoteFile]:
        raise NotImplementedError

    def open(self, rel: str) -> BinaryIO:
        raise NotImplementedError


class LocalSource(Source):
    """A folder of logs. Given a stick's top folder, it reads the stick's logs/ folder."""

    def __init__(self, root: str):
        root = os.path.abspath(os.path.expanduser(root))
        for sub in ("logs", "U/logs"):
            if os.path.isdir(os.path.join(root, sub)):
                root = os.path.join(root, sub)
                break
        self.root = root
        self.label = root

    def list(self) -> List[RemoteFile]:
        if not os.path.isdir(self.root):
            raise FileNotFoundError(f"{self.root} isn't a folder (is the stick plugged in?)")
        out: List[RemoteFile] = []
        for top, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if not f.lower().endswith(LOG_TYPES):
                    continue
                p = os.path.join(top, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                rel = os.path.relpath(p, self.root).replace(os.sep, "/")
                out.append(RemoteFile(rel, st.st_size, st.st_mtime))
        return out

    def open(self, rel: str) -> BinaryIO:
        path = os.path.normpath(os.path.join(self.root, *rel.split("/")))
        if os.path.commonpath([path, self.root]) != self.root:
            raise ValueError(f"{rel!r} is outside {self.root}")
        return open(path, "rb")


def still_growing(source: Source, files: List[RemoteFile], wait_s: float = 3.0) -> set:
    """Names of files whose size changes between two listings `wait_s` apart: the log the
    robot is still writing. Where file times can be trusted, only files changed in the last
    minute are checked again; on the robot (whose clock may be a placeholder) all of them are."""
    if getattr(source, "mtime_reliable", True):
        recent = [f for f in files if time.time() - f.mtime < 60]
    else:
        recent = list(files)
    if not recent:
        return set()
    time.sleep(wait_s)
    now = {f.rel: f.size for f in source.list()}
    return {f.rel for f in recent if now.get(f.rel) != f.size}
