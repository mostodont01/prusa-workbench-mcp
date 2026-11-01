"""Small local HTTP server for the viewer. Stdlib only.

A job is a folder in the jobs dir; each `*.gcode` in it is a bed. `bed-N.json` (written by the slicer side) adds
the summary, overrides and warnings; for G-code dropped in by hand the summary is read from the G-code itself.

    GET /                         the viewer page (static/)
    GET /api/ping                 {"app": "prusa-workbench-viewer", ...}  (lets the MCP server find a running viewer)
    GET /api/jobs                 jobs, newest first
    GET /api/jobs/<job>           beds of a job with their summaries
    GET /files/<job>/<bed>.gcode  raw G-code (and <bed>.png thumbnails)

    Prusa Connect (sending is the user's decision, made here; nothing else in the project sends):
    GET  /api/connect             logged in? printers
    POST /api/connect/login       open the Prusa Account login window (GTK, system python3)
    GET  /api/connect/login       is the login window still open, last result
    POST /api/connect/logout
    POST /api/jobs/delete         {names: [...]} -> job folders go to the desktop trash
    POST /api/send                {job, bed, printer_uuid, filename, action: upload|queue|print}
                                  upload (binary G-code when possible), then optionally queue it (print = first
                                  in the queue: starts once the file is on a printer a person has set READY)
POST requests need the X-Workbench header (blocks cross-site form posts) and every /api request a local Host
header (blocks DNS rebinding).
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import connect, sendfile, trash
from ..gcode import parse_gcode

APP = "prusa-workbench-viewer"
STATIC = Path(__file__).parent / "static"
_NAME = re.compile(r"^[^/\\]+$")
REPO = Path(__file__).resolve().parents[2]
GTK_PYTHON = os.environ.get("PRUSA_WORKBENCH_GTK_PYTHON", "/usr/bin/python3")


class Login:
    """The Prusa Account login window, run as a separate process (it needs GTK/WebKit from the system python)."""
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.result: dict | None = None
        self._lock = threading.Lock()

    def start(self) -> dict:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return {"started": False, "running": True}
            env = dict(os.environ, PYTHONPATH=str(REPO))
            self.result = None
            self.proc = subprocess.Popen([GTK_PYTHON, "-m", "prusa_workbench.connect_login"], cwd=REPO, env=env,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, start_new_session=True)
            threading.Thread(target=self._wait, args=(self.proc,), daemon=True).start()
            return {"started": True, "running": True}

    def _wait(self, proc):
        out = proc.communicate()[0] or ""
        line = out.strip().splitlines()[-1] if out.strip() else ""
        try:
            res = json.loads(line)
        except ValueError:
            res = {"ok": False, "error": f"login window exited ({proc.returncode})"}
        with self._lock:
            self.result = res

    def state(self) -> dict:
        with self._lock:
            return {"running": bool(self.proc and self.proc.poll() is None), "result": self.result}


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._") or "print"
    if not name.lower().endswith(".gcode"):
        name += ".gcode"
    return name[-80:]


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class Jobs:
    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[Path, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def job_dir(self, name: str) -> Path | None:
        if not _NAME.match(name) or name.startswith("."):
            return None
        d = self.root / name
        return d if d.is_dir() else None

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                if not d.is_dir() or d.name.startswith((".", "_")):
                    continue
                beds = self._bed_files(d)
                if not beds:
                    continue
                out.append({"name": d.name, "mtime": max(p.stat().st_mtime for p in beds.values()),
                            "beds": list(beds)})
        return sorted(out, key=lambda j: -j["mtime"])

    def job(self, name: str) -> dict | None:
        d = self.job_dir(name)
        if d is None:
            return None
        beds = []
        for stem, f in self._bed_files(d).items():
            st = f.stat()
            if f.suffix == ".gcode":
                bed = {"name": stem, "gcode": f"/files/{name}/{f.name}", "mtime": st.st_mtime, "size": st.st_size}
                bed.update(self._summary(f, st.st_mtime))
                png = f.with_suffix(".png")
                if png.exists() and png.stat().st_mtime >= st.st_mtime - 5:
                    bed["thumbnail"] = f"/files/{name}/{png.name}?v={int(png.stat().st_mtime)}"
            else:  # failed bed: only the .json with the error
                bed = {"name": stem, "gcode": None, "mtime": st.st_mtime, "size": 0}
                bed.update(self._read_json(f) or {"error": "slicing failed"})
            beds.append(bed)
        return {"name": name, "beds": beds}

    def _bed_files(self, d: Path) -> dict[str, Path]:
        """Bed name -> its .gcode, or its .json when slicing failed (json has an "error" and there is no G-code)."""
        beds = {g.stem: g for g in d.glob("*.gcode")}
        for j in d.glob("*.json"):
            if j.stem not in beds and "error" in (self._read_json(j) or {}):
                beds[j.stem] = j
        return dict(sorted(beds.items(), key=lambda kv: _natural(kv[0])))

    @staticmethod
    def _read_json(f: Path) -> dict | None:
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _summary(self, gcode: Path, mtime: float) -> dict:
        side = gcode.with_suffix(".json")
        if side.exists() and side.stat().st_mtime >= mtime:
            info = self._read_json(side)
            if info and "settings" in info:
                return info
        with self._lock:
            hit = self._cache.get(gcode)
            if hit and hit[0] == mtime:
                return hit[1]
        info = parse_gcode(gcode)
        with self._lock:
            self._cache[gcode] = (mtime, info)
        return info


def make_handler(jobs: Jobs, login: Login):
    class Handler(BaseHTTPRequestHandler):
        server_version = APP

        def _local(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                if not self._local() or self.headers.get("X-Workbench") != "1":
                    return self._error(HTTPStatus.FORBIDDEN)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                if path == "/api/connect/login":
                    return self._json(login.start())
                if path == "/api/connect/logout":
                    connect.logout()
                    return self._json({"logged_in": False})
                if path == "/api/send":
                    return self._send(body)
                if path == "/api/jobs/delete":
                    names = body.get("names")
                    if not isinstance(names, list) or not names:
                        return self._json({"ok": False, "error": "no jobs given"}, HTTPStatus.BAD_REQUEST)
                    done, errors = {}, {}
                    for n in names:
                        try:
                            done[str(n)] = trash.delete_job(jobs.root, str(n))
                        except Exception as e:
                            errors[str(n)] = str(e)
                    return self._json({"ok": not errors, "deleted": done, "errors": errors})
                return self._error(HTTPStatus.NOT_FOUND)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except ValueError as e:
                self._json({"ok": False, "error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)

        def _send(self, body: dict):
            d = jobs.job_dir(str(body.get("job", "")))
            bed = str(body.get("bed", ""))
            g = d / f"{bed}.gcode" if d and _NAME.match(bed + ".gcode") else None
            if not g or not g.is_file():
                return self._json({"ok": False, "error": "no such G-code"}, HTTPStatus.NOT_FOUND)
            uuid = str(body.get("printer_uuid") or "")
            if not uuid:
                return self._json({"ok": False, "error": "no printer chosen"}, HTTPStatus.BAD_REQUEST)
            action = str(body.get("action") or "upload")
            if action not in ("upload", "queue", "print"):
                return self._json({"ok": False, "error": f"unknown action {action}"}, HTTPStatus.BAD_REQUEST)
            try:
                p = connect.printer(uuid)
                f, kind, note = sendfile.prepare(d, bed, p.get("extensions"))
                name = _safe_filename(str(body.get("filename") or f"{d.name}-{bed}"))
                name = re.sub(r"\.(b?gcode)$", "", name, flags=re.I) + f.suffix
                res = connect.upload(f, uuid, name, team_id=p.get("team_id"))
                res.update({"format": kind, "note": note, "printer_state": p.get("state"), "action": action})
                if action in ("queue", "print"):
                    try:
                        res["queue"] = connect.enqueue(uuid, res["team_id"], res.get("hash"), first=(action == "print"))
                    except Exception as e:   # the upload itself worked; say so
                        res["queue_error"] = str(e)
            except connect.LoginRequired as e:
                return self._json({"ok": False, "login_required": True, "error": str(e)})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
            connect.save_prefs(printer_uuid=uuid, action=action)
            return self._json({"ok": True, **res})

        def log_message(self, fmt, *args):  # quiet; errors still go through log_error
            pass

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/api/ping":
                    return self._json({"app": APP, "jobs_dir": str(jobs.root)})
                if path.startswith("/api/") and not self._local():
                    return self._error(HTTPStatus.FORBIDDEN)
                if path == "/api/connect":
                    return self._json(connect.status())
                if path == "/api/connect/login":
                    return self._json(login.state())
                if path == "/api/jobs":
                    return self._json(jobs.list())
                if path.startswith("/api/jobs/"):
                    job = jobs.job(path[len("/api/jobs/"):])
                    return self._json(job) if job else self._error(HTTPStatus.NOT_FOUND)
                if path.startswith("/files/"):
                    parts = path[len("/files/"):].split("/")
                    if len(parts) == 2 and parts[1].endswith((".gcode", ".png")) and _NAME.match(parts[1]):
                        d = jobs.job_dir(parts[0])
                        if d and (d / parts[1]).is_file():
                            ctype = "image/png" if parts[1].endswith(".png") else "text/plain; charset=utf-8"
                            return self._file(d / parts[1], ctype)
                    return self._error(HTTPStatus.NOT_FOUND)
                rel = "index.html" if path in ("/", "") else path.lstrip("/")
                f = (STATIC / rel).resolve()
                if f.is_relative_to(STATIC.resolve()) and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if f.suffix == ".js":
                        ctype = "text/javascript"
                    return self._file(f, ctype)
                return self._error(HTTPStatus.NOT_FOUND)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, obj, status: HTTPStatus = HTTPStatus.OK):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, f: Path, ctype: str):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(f.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with f.open("rb") as fh:
                while chunk := fh.read(1 << 20):
                    self.wfile.write(chunk)

        def _error(self, status: HTTPStatus):
            self.send_error(status)

    return Handler


def serve(jobs_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(Jobs(jobs_dir), Login()))
