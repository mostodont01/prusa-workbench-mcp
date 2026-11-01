"""Prusa Connect: login tokens, printer list, G-code upload. Stdlib only.

Prusa Connect has no official public upload API. This uses what PrusaSlicer itself does:
- Prusa Account OAuth2 + PKCE with PrusaSlicer's public client id (redirect prusaslicer://login, scope basic_info);
  the login window is connect_login.py.
- POST /app/users/teams/<team>/uploads {filename, size, printer_uuid}  -> {"id", "path", "state", ...}
  PUT  /app/teams/<team>/files/raw?upload_id=<id>  (body = the file, Content-Type text/x.gcode)
Facts taken from PrusaSlicer's PrintHostPrusaConnect.cpp and gioelemo/prusa-mcp (no code copied).

After the upload the file can go into the printer's queue (POST /app/printers/<uuid>/queue {hash, team_id, position};
from BogdanStamenovic/prusa-connect, MIT). Position 0 = "print now": Connect starts it once the file is on a
READY printer, i.e. after a person pressed "Set ready" (bed clear). We never set a printer ready.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ACCOUNT_URL = os.environ.get("PRUSA_ACCOUNT_URL", "https://account.prusa3d.com").rstrip("/")
CONNECT_URL = os.environ.get("PRUSA_CONNECT_URL", "https://connect.prusa3d.com").rstrip("/")
CLIENT_ID = "oamhmhZez7opFosnwzElIgE2oGgI2iJORSkw587O"   # PrusaSlicer's public OAuth client
REDIRECT_URI = "prusaslicer://login"
SCOPE = "basic_info"
AUTHORIZE_URL = f"{ACCOUNT_URL}/o/authorize/"
TOKEN_URL = f"{ACCOUNT_URL}/o/token/"
USER_AGENT = "prusa-workbench/0.1 (+https://github.com/mostodont01/prusa-workbench-mcp)"

STATE_DIR = Path(os.environ.get("PRUSA_WORKBENCH_STATE", "~/.config/prusa-workbench")).expanduser()
TOKEN_FILE = STATE_DIR / "connect-tokens.json"
PREFS_FILE = STATE_DIR / "connect.json"

_lock = threading.Lock()


class LoginRequired(RuntimeError):
    pass


class ConnectError(RuntimeError):
    pass


# ---------- token storage ----------

def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    tmp.replace(path)


def save_tokens(tokens: dict) -> None:
    tokens = dict(tokens)
    tokens["saved_at"] = time.time()
    _write_private(TOKEN_FILE, tokens)


def load_tokens() -> dict | None:
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return data if isinstance(data, dict) and data.get("refresh_token") else None
    except (OSError, ValueError):
        return None


def logout() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def prefs() -> dict:
    try:
        return json.loads(PREFS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_prefs(**kw) -> None:
    p = prefs()
    p.update(kw)
    _write_private(PREFS_FILE, p)


def _jwt_exp(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def _post_form(url: str, form: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        raise ConnectError(f"Prusa Account {e.code}: {body}") from None


def exchange_code(code: str, verifier: str) -> dict:
    tokens = _post_form(TOKEN_URL, {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
                                    "client_id": CLIENT_ID, "code_verifier": verifier})
    save_tokens(tokens)
    return tokens


def access_token() -> str:
    with _lock:
        tokens = load_tokens()
        if not tokens:
            raise LoginRequired("Not logged in to Prusa Connect.")
        access = tokens.get("access_token", "")
        exp = _jwt_exp(access) if access else None
        if exp is None and access:   # not a JWT: use expires_in
            exp = tokens.get("saved_at", 0) + float(tokens.get("expires_in", 0))
        if access and exp and time.time() < exp - 60:
            return access
        try:
            new = _post_form(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                         "client_id": CLIENT_ID})
        except ConnectError as e:
            raise LoginRequired(f"Prusa Connect login expired, please log in again ({e}).") from None
        if not new.get("refresh_token"):
            new["refresh_token"] = tokens["refresh_token"]
        save_tokens(new)   # refresh tokens rotate: always keep the newest
        return new["access_token"]


# ---------- API ----------

def _request(method: str, path: str, *, json_body=None, data=None, headers=None, timeout=60):
    url = path if path.startswith("http") else f"{CONNECT_URL}/app{path}"
    h = {"Authorization": f"Bearer {access_token()}", "Accept": "application/json", "User-Agent": USER_AGENT}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode(errors="replace")
        if e.code == 401:
            raise LoginRequired(f"Prusa Connect refused the login (401): {body}") from None
        raise ConnectError(f"Prusa Connect {e.code} on {method} {urllib.parse.urlsplit(url).path}: {body}") from None
    return json.loads(body) if body.strip() else {}


def printers() -> list[dict]:
    """Only the fields we use (the printer records also contain API keys; those never leave this function)."""
    data = _request("GET", "/printers?limit=100")
    items = data.get("printers", data if isinstance(data, list) else [])
    out = []
    for p in items:
        out.append({"uuid": p.get("uuid"), "name": p.get("name") or p.get("uuid"),
                    "model": p.get("printer_type_name") or p.get("printer_model") or p.get("printer_type"),
                    "printer_model": p.get("printer_model"),
                    "state": p.get("connect_state") or p.get("printer_state"),
                    "team_id": p.get("team_id"),
                    "extensions": p.get("printable_extension"),
                    "queue": "queue" in (p.get("allowed_functionalities") or [])})
    return out


def printer(uuid: str) -> dict:
    for p in printers():
        if p["uuid"] == uuid:
            return p
    raise ConnectError(f"printer {uuid} not found in your Prusa Connect account")


def _team_id(printer_uuid: str) -> int | str:
    for p in printers():
        if p["uuid"] == printer_uuid and p.get("team_id"):
            return p["team_id"]
    d = _request("GET", f"/printers/{printer_uuid}")
    tid = d.get("team_id") or (d.get("printer") or {}).get("team_id")
    if not tid:
        raise ConnectError("could not find the printer's team id")
    return tid


def upload(gcode: Path, printer_uuid: str, filename: str | None = None, team_id=None) -> dict:
    """Upload a G-code / bgcode file to Connect for the printer. Connect then copies it to the printer in the
    background (over the printer's own network link, which is the slow part on a MINI's Wi-Fi)."""
    t0 = time.monotonic()
    body = gcode.read_bytes()   # one write instead of http.client's 8 KB blocks
    name = filename or gcode.name
    team = team_id or _team_id(printer_uuid)
    reg = _request("POST", f"/users/teams/{team}/uploads",
                   json_body={"filename": name, "size": len(body), "printer_uuid": printer_uuid})
    if "id" not in reg:
        raise ConnectError(f"upload registration returned no id: {reg}")
    resp = _request("PUT", f"/teams/{team}/files/raw?upload_id={reg['id']}", data=body,
                    headers={"Content-Type": "text/x.gcode", "Upload-Size": str(len(body))}, timeout=600)
    file_hash = (resp.get("hash") if isinstance(resp, dict) else None) or reg.get("hash")
    return {"filename": name, "path": reg.get("path"), "state": reg.get("state"), "upload_id": reg["id"],
            "hash": file_hash, "team_id": team, "size": len(body), "seconds": round(time.monotonic() - t0, 1),
            "printer_page": f"{CONNECT_URL}/printer/{printer_uuid}/dashboard"}


def queue_length(printer_uuid: str) -> int:
    q = _request("GET", f"/printers/{printer_uuid}/queue")
    return int((q.get("pager") or {}).get("total", len(q.get("planned_jobs", []))))


def enqueue(printer_uuid: str, team_id, file_hash: str, position: int | None = None) -> dict:
    """Put an uploaded file into the printer's queue at `position` (0 = prints next), or at the end."""
    if not file_hash:
        raise ConnectError("Connect did not return a file hash, so the file can't be queued (it is uploaded)")
    if position is None:
        position = queue_length(printer_uuid)
    res = _request("POST", f"/printers/{printer_uuid}/queue",
                   json_body={"hash": file_hash, "team_id": team_id, "position": position})
    return {"position": position, "response": res if isinstance(res, dict) else None}


def status() -> dict:
    """Logged in? Which printers? Never raises."""
    if not load_tokens():
        return {"logged_in": False}
    try:
        pr = prefs()
        return {"logged_in": True, "printers": printers(), "default_printer": pr.get("printer_uuid"),
                "default_action": pr.get("action", "upload")}
    except LoginRequired as e:
        return {"logged_in": False, "error": str(e)}
    except Exception as e:  # network etc.
        return {"logged_in": True, "printers": [], "error": str(e)}
