"""Download parts from Onshape as STL (REST API, Basic auth with API keys). Stdlib only.

Keys: ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY from the environment or from ~/onshape-mcp/.env.
The STL endpoint answers with a 307 to another host; the Authorization header has to be sent again there.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .config import ONSHAPE_ENV_FILE


class OnshapeError(RuntimeError):
    pass


def _keys() -> tuple[str, str, str]:
    env = dict(os.environ)
    if not (env.get("ONSHAPE_ACCESS_KEY") and env.get("ONSHAPE_SECRET_KEY")) and ONSHAPE_ENV_FILE.exists():
        for line in ONSHAPE_ENV_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Z_]+)\s*=\s*['\"]?([^'\"\n]*)['\"]?\s*$", line)
            if m:
                env.setdefault(m.group(1), m.group(2))
    a, s = env.get("ONSHAPE_ACCESS_KEY"), env.get("ONSHAPE_SECRET_KEY")
    if not (a and s):
        raise OnshapeError(f"no Onshape API keys (set ONSHAPE_ACCESS_KEY/ONSHAPE_SECRET_KEY or put them in {ONSHAPE_ENV_FILE})")
    return a, s, env.get("ONSHAPE_URL", env.get("ONSHAPE_BASE_URL", "https://cad.onshape.com")).rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _get(path_or_url: str, accept: str = "application/json") -> bytes:
    access, secret, base = _keys()
    url = path_or_url if path_or_url.startswith("http") else base + path_or_url
    auth = "Basic " + base64.b64encode(f"{access}:{secret}".encode()).decode()
    for _ in range(4):
        req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": accept})
        try:
            with _opener.open(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            body = e.read()[:300].decode(errors="replace")
            hint = {401: " (API keys missing or wrong)", 403: " (no access to this document)",
                    404: " (document/element not found)"}.get(e.code, "")
            raise OnshapeError(f"Onshape {e.code}{hint}: {body}") from None
    raise OnshapeError("too many redirects")


def parse_url(url: str) -> dict:
    """https://cad.onshape.com/documents/<did>/<w|v|m>/<wvmid>/e/<eid>  (workspace/element optional)."""
    m = re.search(r"documents/([0-9a-f]{24})(?:/([wvm])/([0-9a-f]{24}))?(?:/e/([0-9a-f]{24}))?", url)
    if not m:
        if re.fullmatch(r"[0-9a-f]{24}", url.strip()):
            return {"did": url.strip(), "wvm": None, "wvmid": None, "eid": None}
        raise OnshapeError(f"not an Onshape document URL: {url}")
    return {"did": m.group(1), "wvm": m.group(2), "wvmid": m.group(3), "eid": m.group(4)}


def list_parts(url: str) -> dict:
    """Resolve the URL to a part studio and list its parts."""
    ref = parse_url(url)
    did = ref["did"]
    if not ref["wvm"]:
        doc = json.loads(_get(f"/api/v9/documents/{did}"))
        ref["wvm"], ref["wvmid"] = "w", doc["defaultWorkspace"]["id"]
        ref["doc_name"] = doc.get("name")
    base = f"d/{did}/{ref['wvm']}/{ref['wvmid']}"
    if not ref["eid"]:
        els = json.loads(_get(f"/api/v9/documents/{base}/elements?elementType=PARTSTUDIO"))
        if not els:
            raise OnshapeError("the document has no part studio")
        if len(els) > 1:
            names = ", ".join(f"{e['name']} (/e/{e['id']})" for e in els)
            raise OnshapeError(f"the document has several part studios, pass a URL with /e/<id>: {names}")
        ref["eid"], ref["element_name"] = els[0]["id"], els[0]["name"]
    parts = json.loads(_get(f"/api/v9/parts/{base}/e/{ref['eid']}"))
    ref["parts"] = [{"name": p["name"], "partId": p["partId"]} for p in parts
                    if p.get("bodyType", "solid") == "solid"]
    return ref


def download_stl(ref: dict, part_id: str) -> bytes:
    q = urllib.parse.urlencode({"partIds": part_id, "units": "millimeter", "mode": "binary", "grouping": "true"})
    path = f"/api/v9/partstudios/d/{ref['did']}/{ref['wvm']}/{ref['wvmid']}/e/{ref['eid']}/stl?{q}"
    data = _get(path, accept="application/vnd.onshape.v1+octet-stream,application/octet-stream,*/*")
    if len(data) < 84:
        raise OnshapeError(f"empty STL for part {part_id}")
    return data
