"""A stand-in roboRIO for tests: an SSH server on 127.0.0.1 that serves a folder as /U over
SFTP, logs lvuser in with a blank password (by "none", password or keyboard-interactive, like
different roboRIO images), answers `df`, and records every SFTP request so a test can check
that nothing was ever written, renamed or deleted."""

import os
import socket
import threading
import time

import paramiko
from paramiko import SFTP_NO_SUCH_FILE, SFTP_PERMISSION_DENIED

_KEY = None


def _host_key():
    global _KEY
    if _KEY is None:
        _KEY = paramiko.RSAKey.generate(1024)
    return _KEY


class _Auth(paramiko.ServerInterface):
    def __init__(self, rio):
        self.rio = rio

    def get_allowed_auths(self, username):
        return {"none": "none", "password": "password", "interactive": "keyboard-interactive"}[self.rio.auth]

    def check_auth_none(self, username):
        ok = self.rio.auth == "none" and username == "lvuser"
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        ok = self.rio.auth == "password" and username == "lvuser" and password == ""
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_auth_interactive(self, username, submethods):
        if self.rio.auth != "interactive" or username != "lvuser":
            return paramiko.AUTH_FAILED
        q = paramiko.InteractiveQuery("", "")
        q.add_prompt("Password: ", False)
        return q

    def check_auth_interactive_response(self, responses):
        return paramiko.AUTH_SUCCESSFUL if list(responses) == [""] else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        self.rio.calls.append(("exec", command.decode()))

        def answer():
            time.sleep(0.1)              # let the server reply to the exec request first
            if command.decode().startswith("df"):
                channel.sendall(b"Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                                b"/dev/sda1 15625000 5625000 10000000 36% /U\n")
                channel.send_exit_status(0)
            else:
                channel.send_exit_status(127)
            channel.close()
        threading.Thread(target=answer, daemon=True).start()
        return True


class _Handle(paramiko.SFTPHandle):
    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))


def _sftp_class(rio):
    class _SFTP(paramiko.SFTPServerInterface):
        def _real(self, path):
            path = os.path.normpath("/" + path.lstrip("/"))
            if not (path == "/U" or path.startswith("/U/")):
                return None
            return os.path.join(rio.root, path.lstrip("/"))

        def list_folder(self, path):
            rio.calls.append(("list", path))
            real = self._real(path)
            if real is None or not os.path.isdir(real):
                return SFTP_NO_SUCH_FILE
            out = []
            for n in os.listdir(real):
                a = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(real, n)))
                a.filename = n
                out.append(a)
            return out

        def stat(self, path):
            real = self._real(path)
            if real is None or not os.path.exists(real):
                return SFTP_NO_SUCH_FILE
            return paramiko.SFTPAttributes.from_stat(os.stat(real))

        lstat = stat

        def open(self, path, flags, attr):
            rio.calls.append(("open", path, flags))
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                rio.writes.append(("open-write", path))
                return SFTP_PERMISSION_DENIED
            real = self._real(path)
            if real is None or not os.path.isfile(real):
                return SFTP_NO_SUCH_FILE
            h = _Handle(flags)
            h.readfile = open(real, "rb")
            h.filename = real
            return h

        def _denied(self, what, *a):
            rio.writes.append((what,) + a)
            return SFTP_PERMISSION_DENIED

        def remove(self, path):
            return self._denied("remove", path)

        def rename(self, old, new):
            return self._denied("rename", old, new)

        def posix_rename(self, old, new):
            return self._denied("rename", old, new)

        def mkdir(self, path, attr):
            return self._denied("mkdir", path)

        def rmdir(self, path):
            return self._denied("rmdir", path)

        def chattr(self, path, attr):
            return self._denied("chattr", path)

        def symlink(self, target, path):
            return self._denied("symlink", path)

        def canonicalize(self, path):
            return os.path.normpath("/" + path.lstrip("/"))

    return _SFTP


class FakeRio:
    """with FakeRio(folder) as rio: ...  rio.port; files go in folder/U/logs."""

    def __init__(self, root, auth="none"):
        self.root = root
        self.auth = auth
        self.calls = []
        self.writes = []
        os.makedirs(os.path.join(root, "U", "logs"), exist_ok=True)
        self.logs = os.path.join(root, "U", "logs")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._transports = []
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass
        for t in self._transports:
            t.close()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(_host_key())
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, _sftp_class(self))
            self._transports.append(t)
            try:
                t.start_server(server=_Auth(self))
            except (paramiko.SSHException, EOFError, OSError):
                continue
