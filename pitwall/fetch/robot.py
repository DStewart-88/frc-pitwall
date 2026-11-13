"""The robot as a log source: find the roboRIO, and read its USB stick over SFTP.

Like every source this can only list and read. It logs in the way AdvantageScope does (user
lvuser, blank password) and never sends a write, rename or delete. The only command it runs
is `df` on the stick, to show free space.

Finding the robot, first answer wins in this order:
  10.TE.AM.2               radio or Ethernet
  172.22.11.2              USB
  roboRIO-<team>-FRC.local mDNS, either way
  fe80::280:2fff:fe..      USB, by IPv6 link-local address: a roboRIO whose USB link is waiting
                           for an address it never gets (the spare roboRIO 2.0, Nov 12) still
                           answers here. 00:80:2F is National Instruments' prefix, so its
                           link-local address always has 280:2fff:fe in it.
"""

import ipaddress
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import BinaryIO, Dict, List, Optional, Tuple

from .source import RemoteFile, Source

try:
    import paramiko
except ImportError:           # Pitwall still runs; only fetching from the robot needs it
    paramiko = None

LOG_FOLDER = "/U/logs"
USER = "lvuser"
SSH_PORT = 22
NI_LINK_LOCAL = re.compile(r"fe80::280:2fff:fe[0-9a-f]{2}:[0-9a-f]{1,4}(%[\w.\-]+)?", re.I)


def have_sftp() -> bool:
    return paramiko is not None


def install_hint() -> str:
    if sys.platform.startswith("win"):
        return "py -m pip install paramiko"
    if sys.platform == "darwin":
        return "python3 -m pip install paramiko"
    return "sudo apt install python3-paramiko"


@dataclass
class Robot:
    host: str
    via: str              # "USB", "Ethernet or radio"
    wired: Optional[bool]  # this computer's link to it is wired (USB or Ethernet); None = can't tell
    port: int = SSH_PORT

    @property
    def label(self) -> str:
        return f"roboRIO at {self.host} ({self.via})"


def team_address(team: int) -> str:
    return f"10.{team // 100}.{team % 100}.2"


def _port_open(host: str, timeout: float) -> bool:
    try:
        infos = socket.getaddrinfo(host, SSH_PORT, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    for family, kind, proto, _, addr in infos:
        s = socket.socket(family, kind, proto)
        s.settimeout(timeout)
        try:
            s.connect(addr)
            return True
        except OSError:
            continue
        finally:
            s.close()
    return False


def _run(cmd: List[str], timeout: float) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def link_local_rios(timeout: float = 1.5) -> List[str]:
    """IPv6 link-local addresses of roboRIOs on this computer's links ("fe80::...%iface")."""
    found: Dict[str, None] = {}
    if sys.platform.startswith("linux"):
        names = [n for _, n in socket.if_nameindex() if n != "lo"] if hasattr(socket, "if_nameindex") else []
        ping = shutil.which("ping")
        if ping:
            threads = []
            outs: Dict[str, str] = {}

            def one(n: str) -> None:
                outs[n] = _run([ping, "-6", "-c", "2", "-i", "0.3", "-W", "1", f"ff02::1%{n}"], timeout + 1)
            for n in names:
                t = threading.Thread(target=one, args=(n,), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout + 1.5)
            for n, out in outs.items():
                for m in NI_LINK_LOCAL.finditer(out):
                    found[m.group(0).split("%")[0] + "%" + n] = None
        for line in _run(["ip", "-6", "neigh", "show"], timeout).splitlines():
            m = NI_LINK_LOCAL.match(line.strip())
            dev = re.search(r"\bdev (\S+)", line)
            if m and dev and "FAILED" not in line:
                found[m.group(0).split("%")[0] + "%" + dev.group(1)] = None
    elif sys.platform.startswith("win"):
        iface = None
        for line in _run(["netsh", "interface", "ipv6", "show", "neighbors"], timeout + 2).splitlines():
            m = re.search(r"Interface (\d+)", line)
            if m:
                iface = m.group(1)
            a = NI_LINK_LOCAL.search(line)
            if a and iface and "Unreachable" not in line:
                found[a.group(0).split("%")[0] + "%" + iface] = None
    return list(found)


def _is_usb_address(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return host.startswith("172.22.11.") or (ip.version == 6 and ip.is_link_local)


def link_is_wired(host: str) -> Optional[bool]:
    """Is this computer's link to `host` wired? None when it can't tell."""
    if _is_usb_address(host):
        return True
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, SSH_PORT))
            local = s.getsockname()[0]
    except OSError:
        return None
    if sys.platform.startswith("linux"):
        for line in _run(["ip", "-o", "-4", "addr", "show"], 2).splitlines():
            parts = line.split()
            if len(parts) > 3 and parts[3].split("/")[0] == local:
                dev = parts[1]
                return not (os.path.exists(f"/sys/class/net/{dev}/wireless") or dev.startswith("wl"))
        return None
    if sys.platform.startswith("win"):
        ps = ("$a=(Get-NetIPAddress -IPAddress '%s' -ErrorAction SilentlyContinue).InterfaceAlias;"
              "if($a){(Get-NetAdapter -Name $a).PhysicalMediaType}" % local)
        out = _run(["powershell", "-NoProfile", "-Command", ps], 6).strip().lower()
        if not out:
            return None
        return "802.3" in out and "802.11" not in out and "wireless" not in out
    return None


def find_robot(team: int, timeout: float = 0.8, link_local: bool = True) -> Optional[Robot]:
    """The robot, if this computer can reach it now."""
    tries: List[Tuple[str, str]] = [(team_address(team), "Ethernet or radio"), ("172.22.11.2", "USB"),
                                    (f"roboRIO-{team}-FRC.local", "mDNS")]
    results: Dict[str, bool] = {}

    def probe(host: str) -> None:
        results[host] = _port_open(host, timeout)
    threads = [threading.Thread(target=probe, args=(h,), daemon=True) for h, _ in tries]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 2.5)            # mDNS lookups can be slow
    for host, via in tries:
        if results.get(host):
            if via == "mDNS":
                addr = _resolve(host)
                via = "USB" if addr and _is_usb_address(addr) else "Ethernet or radio"
                host = addr or host
            return Robot(host, via, link_is_wired(host))
    if link_local:
        for addr in link_local_rios():
            if _port_open(addr, timeout):
                return Robot(addr, "USB", True)
    return None


def _resolve(host: str) -> Optional[str]:
    try:
        for *_, addr in socket.getaddrinfo(host, SSH_PORT, type=socket.SOCK_STREAM):
            a = addr[0]
            if len(addr) >= 4 and addr[3]:            # IPv6 scope
                a = f"{a.split('%')[0]}%{socket.if_indextoname(addr[3])}" if hasattr(socket, "if_indextoname") else a
            return a
    except (OSError, UnicodeError):
        pass
    return None


class RobotError(Exception):
    pass


class SftpSource(Source):
    """The robot's log folder over SFTP. Use as a context manager, or call close()."""

    mtime_reliable = False         # the roboRIO's clock is a placeholder until the DS syncs it

    def __init__(self, robot: Robot, folder: str = LOG_FOLDER, timeout: float = 8.0):
        if paramiko is None:
            raise RobotError(f"Fetching from the robot needs the paramiko package: {install_hint()}")
        self.robot = robot
        self.folder = folder.rstrip("/") or "/"
        self.label = robot.label
        self.timeout = timeout
        self._t = None
        self._sftp = None
        self.login = ""

    def connect(self) -> "SftpSource":
        host, port = self.robot.host, self.robot.port
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            family, kind, proto, _, addr = infos[0]
            sock = socket.socket(family, kind, proto)
            sock.settimeout(self.timeout)
            sock.connect(addr)
            t = paramiko.Transport(sock)
            t.banner_timeout = self.timeout
            t.start_client(timeout=self.timeout)
            self.login = _login(t)
            self._t = t
            self._sftp = paramiko.SFTPClient.from_transport(t)
            self._sftp.get_channel().settimeout(self.timeout * 4)
        except (OSError, paramiko.SSHException) as e:
            self.close()
            raise RobotError(f"couldn't connect to the {self.robot.label}: {e}") from e
        return self

    def __enter__(self) -> "SftpSource":
        return self.connect() if self._sftp is None else self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for x in (self._sftp, self._t):
            try:
                if x is not None:
                    x.close()
            except Exception:
                pass
        self._sftp = self._t = None

    def list(self) -> List[RemoteFile]:
        from .source import LOG_TYPES
        if self._sftp is None:
            self.connect()
        out: List[RemoteFile] = []
        try:
            self._walk(self.folder, "", out, LOG_TYPES, depth=0)
        except FileNotFoundError:
            raise FileNotFoundError(f"{self.folder} isn't on the robot (is the USB stick in the roboRIO?)")
        except (OSError, paramiko.SSHException) as e:
            raise RobotError(f"couldn't list the robot's logs: {e}") from e
        return out

    def _walk(self, path: str, rel: str, out: List[RemoteFile], types, depth: int) -> None:
        for a in sorted(self._sftp.listdir_attr(path), key=lambda a: a.filename):
            name = a.filename
            if name.startswith("."):
                continue
            r = f"{rel}/{name}" if rel else name
            if stat.S_ISDIR(a.st_mode or 0):
                if depth < 3:
                    self._walk(f"{path}/{name}", r, out, types, depth + 1)
            elif name.lower().endswith(types):
                out.append(RemoteFile(r, int(a.st_size or 0), float(a.st_mtime or 0)))

    def open(self, rel: str) -> BinaryIO:
        if self._sftp is None:
            self.connect()
        if ".." in rel.split("/"):
            raise ValueError(f"{rel!r} is outside {self.folder}")
        f = self._sftp.open(f"{self.folder}/{rel}", "rb")
        f.prefetch()
        return f

    def stick_space(self) -> Optional[Dict[str, int]]:
        """{"total": bytes, "free": bytes} for the stick, from `df`, or None."""
        if self._t is None:
            return None
        try:
            ch = self._t.open_session(timeout=self.timeout)
            ch.settimeout(self.timeout)
            ch.exec_command("df -kP /U")
            data = b""
            while True:
                b = ch.recv(4096)
                if not b:
                    break
                data += b
            ch.close()
        except (OSError, paramiko.SSHException):
            return None
        for line in data.decode("utf-8", "replace").splitlines()[1:]:
            p = line.split()
            if len(p) >= 4 and p[1].isdigit() and p[3].isdigit():
                return {"total": int(p[1]) * 1024, "free": int(p[3]) * 1024}
        return None


def _login(t) -> str:
    """Log in as lvuser with no password, however the roboRIO asks for it. Returns how."""
    try:
        t.auth_none(USER)
        if t.is_authenticated():
            return "none"
    except paramiko.BadAuthenticationType as e:
        allowed = e.allowed_types
    except paramiko.SSHException:
        allowed = ["password", "keyboard-interactive"]
    else:
        allowed = ["password", "keyboard-interactive"]
    if "password" in allowed:
        try:
            t.auth_password(USER, "")
            if t.is_authenticated():
                return "password"
        except paramiko.SSHException:
            pass
    if "keyboard-interactive" in allowed:
        t.auth_interactive(USER, lambda title, instr, prompts: ["" for _ in prompts])
        if t.is_authenticated():
            return "keyboard-interactive"
    raise paramiko.AuthenticationException(f"the roboRIO didn't accept {USER} with a blank password")
