"""The app's web server: pages, report files and a small JSON API, on this computer only.

Safety: it listens on 127.0.0.1, answers only requests addressed to localhost (so another
website can't reach it through DNS tricks), and every change (POST) must carry the token that
was put into this run's pages.
"""

import hashlib
import io
import json
import mimetypes
import os
import re
import threading
import time
import urllib.request
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..cache import default_dir
from ..profile import ProfileError, builtin_profiles, load_profile
from . import pages
from .store import Store, new_token, slug
from ..setup.draft import draft_to_toml, make_draft
from ..setup.scan import scan
from ..wpilog import WPILog, WPILogError
from .setup_page import setup_page
from .robotwatch import RobotWatch, archive_ok
from .worker import Worker, drives, list_folder, logs_in

MAX_BODY = 8 * 1024 * 1024


class App:
    def __init__(self, data_dir: Optional[str] = None, cache_dir: Optional[str] = None, jobs: int = 2,
                 poll_s: float = 4.0):
        self.store = Store(data_dir)
        self.token = new_token()
        self.worker = Worker(self.store, cache_dir if cache_dir is not None else default_dir(), jobs, poll_s)
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.build = build_id()
        self.robot = RobotWatch(self.store, on_logs=self.worker.request_build)


class Handler(BaseHTTPRequestHandler):
    app: App = None          # set on the subclass made by serve()
    server_version = "Pitwall"

    def log_message(self, fmt, *args):          # keep the terminal quiet; errors still print
        pass

    # ------------------------------------------------------------ plumbing

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def html(self, text: str, code: int = 200) -> None:
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8")

    def json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode("utf-8"), "application/json")

    def error_json(self, msg: str, code: int = 400) -> None:
        self.json({"error": msg}, code)

    def redirect(self, to: str) -> None:
        self._send(HTTPStatus.FOUND, b"", "text/plain", {"Location": to})

    def not_found(self) -> None:
        self.html(pages.page("Not found", '<section class="card empty"><h2>Not found</h2>'
                             '<p><a class="btn" href="/">All events</a></p></section>', self.app.token), 404)

    def body_json(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("request too large")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    # ------------------------------------------------------------ routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        q = parse_qs(url.query)
        a, s, t = self.app, self.app.store, self.app.token
        try:
            if not parts:
                return self.html(pages.home(s.list_events(), a.worker.all_status(), s.list_profiles(), t))
            if parts == ["icon.svg"]:
                with open(os.path.join(os.path.dirname(__file__), "icon.svg"), "rb") as f:
                    return self._send(200, f.read(), "image/svg+xml")
            if parts == ["api", "ping"]:
                return self.json({"pitwall": __version__, "data": a.store.root, "build": a.build})
            if parts == ["api", "status"]:
                return self.json(a.worker.all_status())
            if parts == ["api", "robot"]:
                return self.json(a.robot.get_status())
            if parts == ["api", "browse"]:
                d = list_folder((q.get("path") or [""])[0] or os.path.expanduser("~"), (q.get("kind") or ["logs"])[0])
                d["places"] = drives()
                return self.json(d)
            if parts == ["events", "new"]:
                return self.html(pages.new_event(s.list_profiles(), t))
            if parts[0] == "events" and len(parts) >= 2:
                return self.get_event(parts[1], parts[2:], url.path.endswith("/"))
            if parts[0] == "api" and len(parts) == 4 and parts[1] == "events" and parts[3] == "status":
                return self.json(a.worker.get_status(slug(parts[2])))
            if parts == ["profiles", "new"]:
                return self.html(setup_page(t))
            if parts == ["api", "setup", "logs"]:
                return self.json(logs_in((q.get("path") or [""])[0]))
            if parts == ["profiles"]:
                used: Dict[str, list] = {}
                for e in s.list_events():
                    used.setdefault(e["profile"], []).append(e["name"] or e["id"])
                return self.html(pages.profiles_page(s.list_profiles(), used,
                                                     [os.path.basename(p)[:-5] for p in builtin_profiles()], t))
            if parts[0] == "profiles" and len(parts) in (2, 3):
                path = s.profile_path(parts[1])
                if not os.path.exists(path):
                    return self.not_found()
                if len(parts) == 3 and parts[2] == "download":
                    with open(path, "rb") as f:
                        return self._send(200, f.read(), "application/toml",
                                          {"Content-Disposition": f'attachment; filename="{slug(parts[1])}.toml"'})
                try:
                    return self.html(pages.profile_view(parts[1], load_profile(path), t, "new" in q))
                except (ProfileError, ValueError, KeyError) as e:
                    return self.html(pages.page("Profile", f'<div class="msg err">{pages.esc(e)}</div>', t), 200)
            return self.not_found()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def get_event(self, eid: str, rest: list, trailing: bool) -> None:
        a, s, t = self.app, self.app.store, self.app.token
        ev = s.get_event(eid)
        if not ev:
            return self.not_found()
        eid = ev["id"]
        if not rest and not trailing:
            return self.redirect(f"/events/{eid}/")
        if rest == ["settings"]:
            summary = _read_summary(s.report_dir(eid))
            return self.html(pages.event_settings(ev, summary, s.list_profiles(), s.list_events(), t))
        if rest == ["download"]:
            return self.download(ev)
        # report files: /events/<id>/  -> report/index.html, /events/<id>/matches/x.html -> report/matches/x.html
        rel = "/".join(rest) or "index.html"
        rdir = s.report_dir(eid)
        path = os.path.realpath(os.path.join(rdir, rel))
        if not path.startswith(os.path.realpath(rdir) + os.sep):
            return self.not_found()
        if not os.path.isfile(path):
            if rel == "index.html":
                return self.html(pages.waiting_page(ev, a.worker.get_status(eid), s.logs_dir(eid), t))
            return self.not_found()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        if ctype == "text/html":
            bar = pages.event_toolbar(ev, a.worker.get_status(eid), t, at_match=rel != "index.html")
            data = pages.inject(data.decode("utf-8"), bar, t).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self._send(200, data, ctype)

    def download(self, ev: Dict[str, Any]) -> None:
        rdir = self.app.store.report_dir(ev["id"])
        if not os.path.isfile(os.path.join(rdir, "index.html")):
            return self.not_found()
        buf = io.BytesIO()
        top = slug(ev["name"] or ev["id"])
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(rdir):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.join(top, os.path.relpath(full, rdir)))
        self._send(200, buf.getvalue(), "application/zip",
                   {"Content-Disposition": f'attachment; filename="{top}-pitwall.zip"'})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Pitwall-Token") != self.app.token:
            return self.error_json("This page is out of date. Reload it and try again.", 403)
        parts = [unquote(p) for p in urlparse(self.path).path.split("/") if p]
        a, s = self.app, self.app.store
        try:
            body = self.body_json()
            if parts == ["api", "quit"]:
                self.json({"ok": True})
                if a.httpd:
                    threading.Thread(target=a.httpd.shutdown, daemon=True).start()
                return None
            if parts == ["api", "events"]:
                sources = [os.path.abspath(p) for p in body.get("sources", []) if p]
                if not sources:
                    return self.error_json("Pick the folder with the logs first.")
                if not body.get("profile"):
                    return self.error_json("Pick the robot.")
                eid = s.create_event(body.get("name", ""), body["profile"], sources, bool(body.get("watch", True)))
                threading.Thread(target=self._first_import, args=(eid,), daemon=True).start()
                return self.json({"id": eid})
            if len(parts) == 4 and parts[:2] == ["api", "events"]:
                ev = s.get_event(parts[2])
                if not ev:
                    return self.error_json("No such event.", 404)
                eid, action = ev["id"], parts[3]
                if action == "settings":
                    changes = {k: body[k] for k in ("name", "profile", "compare", "watch", "include", "exclude",
                                                    "start", "end") if k in body}
                    for k in ("start", "end"):
                        if changes.get(k) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", changes[k]):
                            return self.error_json("Dates are year-month-day, like 2026-11-07.")
                    a_, b_ = changes.get("start", ev.get("start")), changes.get("end", ev.get("end"))
                    if a_ and b_ and a_ > b_:
                        return self.error_json("The event's first day is after its last day.")
                    s.save_event(eid, changes)
                    a.worker.request_build(eid)
                    return self.json({"ok": True})
                if action == "sources":
                    srcs = list(ev["sources"])
                    if body.get("add"):
                        p = os.path.abspath(body["add"])
                        if p not in srcs:
                            srcs.append(p)
                    if body.get("remove") in srcs:
                        srcs.remove(body["remove"])
                    s.save_event(eid, {"sources": srcs, "watch": True if body.get("add") else ev["watch"]})
                    if body.get("add"):
                        threading.Thread(target=self._first_import, args=(eid,), daemon=True).start()
                    return self.json({"ok": True})
                if action == "rebuild":
                    a.worker.request_build(eid)
                    return self.json({"ok": True})
                if action == "delete":
                    s.delete_event(eid)
                    return self.json({"ok": True})
            if parts == ["api", "robot", "fetch"]:
                folder = body.get("folder")
                a.robot.request_fetch(os.path.abspath(folder) if folder else None)
                return self.json({"ok": True})
            if parts == ["api", "robot", "new-code"]:
                r = a.robot.answer_code(body.get("code", ""), body.get("action", ""), body.get("name", ""),
                                        body.get("profile", ""), body.get("event", ""))
                return self.json(r)
            if parts == ["api", "robot", "settings"]:
                changes = {}
                if "archive" in body:
                    path = os.path.abspath(os.path.expanduser(body["archive"] or ""))
                    why = archive_ok(path)
                    if why:
                        return self.error_json(why)
                    changes["archive"] = path
                if "auto_fetch" in body:
                    changes["auto_fetch"] = bool(body["auto_fetch"])
                if "team" in body:
                    team = int(body["team"])
                    if not 1 <= team <= 99999:
                        return self.error_json("That isn't a team number.")
                    changes["team"] = team
                s.save_settings(changes)
                if "archive" in changes:
                    a.robot.refresh_codes()
                a.robot._wake.set()
                return self.json(a.robot.get_status())
            if parts == ["api", "profiles"]:
                if body.get("builtin"):
                    pid = s.add_builtin_profile(body["builtin"])
                else:
                    pid = s.save_profile(body.get("toml", ""), body.get("name", ""))
                return self.json({"id": pid})
            if parts == ["api", "setup", "scan"]:
                code = body.get("code") or ""
                if not os.path.isdir(code):
                    return self.error_json("Pick the robot code folder first.")
                result = scan(code)
                if not result.motors:
                    return self.error_json("No motors found in that code. " + " ".join(result.notes))
                log = WPILog(body["log"]) if body.get("log") else None
                name = os.path.basename(os.path.normpath(code))
                return self.json(make_draft(result, log, name))
            if parts == ["api", "setup", "save"]:
                d = body.get("draft") or {}
                pid = s.save_profile(draft_to_toml(d), d.get("robot_name", ""))
                return self.json({"id": pid})
            if len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "delete":
                s.delete_profile(parts[2])
                return self.json({"ok": True})
            return self.error_json("Unknown request.", 404)
        except (ProfileError, ValueError, KeyError, OSError, WPILogError) as e:
            return self.error_json(str(e))

    def _first_import(self, eid: str) -> None:
        w = self.app.worker
        w._set(eid, state="analyzing", detail="Copying logs", done=0, total=0)
        try:
            w.import_from_sources(eid, wait_settle=False)
        finally:
            w.request_build(eid)


def _read_summary(rdir: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(rdir, "event.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def make_server(app: App, port: int = 8088) -> Tuple[ThreadingHTTPServer, int]:
    handler = type("PitwallHandler", (Handler,), {"app": app})
    last_err: Optional[OSError] = None
    for p in ([port] + list(range(port + 1, port + 20))) if port else [0]:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), handler)
            httpd.daemon_threads = True
            return httpd, httpd.server_address[1]
        except OSError as e:
            last_err = e
    raise OSError(f"no free port near {port}: {last_err}")


def _instance_file(root: str) -> str:
    return os.path.join(root, "running.json")


def build_id() -> str:
    """Changes whenever Pitwall's code changes (a git pull), even if the version number doesn't."""
    h = hashlib.sha1(__version__.encode())
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root, dirs, files in os.walk(pkg):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f.endswith((".py", ".toml", ".svg")):
                st = os.stat(os.path.join(root, f))
                h.update(f"{os.path.relpath(os.path.join(root, f), pkg)}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:12]


def running_instance(root: str) -> Optional[Dict[str, Any]]:
    """The Pitwall app already running on this data folder, if there is one: {url, build, token}."""
    try:
        with open(_instance_file(root), encoding="utf-8") as f:
            info = json.load(f)
        url = f"http://127.0.0.1:{int(info['port'])}/"
        with urllib.request.urlopen(url + "api/ping", timeout=1.5) as r:
            ping = json.load(r)
        if ping.get("pitwall") and os.path.realpath(ping.get("data", "")) == os.path.realpath(root):
            return {"url": url, "build": ping.get("build"), "token": info.get("token")}
    except (OSError, ValueError, KeyError):
        pass
    return None


def _stop_instance(inst: Dict[str, Any], wait_s: float = 8.0) -> bool:
    """Ask a running Pitwall to quit; True once it has stopped answering."""
    try:
        req = urllib.request.Request(inst["url"] + "api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Pitwall-Token": inst.get("token") or ""})
        urllib.request.urlopen(req, timeout=3).read()
    except OSError:
        pass
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(inst["url"] + "api/ping", timeout=0.5).read()
        except OSError:
            return True
        time.sleep(0.2)
    return False


def serve(data_dir: Optional[str] = None, port: int = 8088, open_browser: bool = True, jobs: int = 2,
          cache_dir: Optional[str] = None) -> None:
    app = App(data_dir, cache_dir, jobs)
    existing = running_instance(app.store.root)
    if existing and existing["build"] == app.build:
        print(f"Pitwall is already running at {existing['url']}; opening it.")
        if open_browser:
            webbrowser.open(existing["url"])
        return
    if existing:
        # Pitwall was updated since that copy started: replace it, so the new code is what runs.
        print("Pitwall was updated; restarting the running copy.")
        if not _stop_instance(existing):
            print(f"The running copy at {existing['url']} didn't stop; quit it from the page and open Pitwall again.")
            if open_browser:
                webbrowser.open(existing["url"])
            return
    httpd, port = make_server(app, port)
    app.httpd = httpd
    app.worker.start()
    app.robot.start()
    url = f"http://127.0.0.1:{port}/"
    try:
        path = _instance_file(app.store.root)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # holds the token: owner only
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"port": port, "pid": os.getpid(), "token": app.token, "build": app.build}, f)
    except OSError:
        pass
    print(f"Pitwall is running at {url}\nData folder: {app.store.root}\n"
          "Leave this running; stop it with Quit in the app or Ctrl+C here.", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Pitwall.")
    finally:
        app.worker.stop()
        httpd.server_close()
        try:
            os.remove(_instance_file(app.store.root))
        except OSError:
            pass
