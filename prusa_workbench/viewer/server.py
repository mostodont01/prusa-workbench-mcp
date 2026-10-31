"""Small local HTTP server for the viewer. Stdlib only.

A job is a folder in the jobs dir; each `*.gcode` in it is a bed. `bed-N.json` (written by the slicer side) adds
the summary, overrides and warnings; for G-code dropped in by hand the summary is read from the G-code itself.

    GET /                         the viewer page (static/)
    GET /api/ping                 {"app": "prusa-workbench-viewer", ...}  (lets the MCP server find a running viewer)
    GET /api/jobs                 jobs, newest first
    GET /api/jobs/<job>           beds of a job with their summaries
    GET /files/<job>/<bed>.gcode  raw G-code
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..gcode import parse_gcode

APP = "prusa-workbench-viewer"
STATIC = Path(__file__).parent / "static"
_NAME = re.compile(r"^[^/\\]+$")


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


def make_handler(jobs: Jobs):
    class Handler(BaseHTTPRequestHandler):
        server_version = APP

        def log_message(self, fmt, *args):  # quiet; errors still go through log_error
            pass

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/api/ping":
                    return self._json({"app": APP, "jobs_dir": str(jobs.root)})
                if path == "/api/jobs":
                    return self._json(jobs.list())
                if path.startswith("/api/jobs/"):
                    job = jobs.job(path[len("/api/jobs/"):])
                    return self._json(job) if job else self._error(HTTPStatus.NOT_FOUND)
                if path.startswith("/files/"):
                    parts = path[len("/files/"):].split("/")
                    if len(parts) == 2 and parts[1].endswith(".gcode") and _NAME.match(parts[1]):
                        d = jobs.job_dir(parts[0])
                        if d and (d / parts[1]).is_file():
                            return self._file(d / parts[1], "text/plain; charset=utf-8")
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

        def _json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(HTTPStatus.OK)
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
    return ThreadingHTTPServer((host, port), make_handler(Jobs(jobs_dir)))
