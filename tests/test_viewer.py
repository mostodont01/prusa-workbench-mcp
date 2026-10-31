"""Viewer: the HTTP server's request rules, and the browser G-code parser (run in Node)."""
import json
import math
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from prusa_workbench.viewer.server import serve

WORKER = Path(__file__).resolve().parents[1] / "prusa_workbench/viewer/static/gcode-worker.js"


@pytest.fixture
def viewer(tmp_path):
    job = tmp_path / "2026-01-01-job"
    job.mkdir()
    (job / "bed-1.gcode").write_text(";LAYER_CHANGE\n;Z:0.2\n;TYPE:Perimeter\nG1 X10 Y10 E1\n")
    (job / "bed-2.json").write_text(json.dumps({"bed": "bed-2", "error": "boom"}))   # failed bed
    httpd = serve(tmp_path, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _req(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_jobs_and_failed_beds(viewer):
    st, body = _req(viewer + "/api/jobs/2026-01-01-job")
    beds = {b["name"]: b for b in json.loads(body)["beds"]}
    assert st == 200 and beds["bed-1"]["gcode"] and beds["bed-2"]["gcode"] is None and beds["bed-2"]["error"] == "boom"


def test_no_path_tricks(viewer):
    assert _req(viewer + "/files/2026-01-01-job/..%2F..%2Fetc%2Fpasswd")[0] == 404
    assert _req(viewer + "/files/../2026-01-01-job/bed-1.gcode")[0] == 404
    assert _req(viewer + "/files/2026-01-01-job/bed-1.json")[0] == 404     # only .gcode/.png
    assert _req(viewer + "/../../etc/passwd")[0] == 404


def test_send_needs_header_and_local_host(viewer):
    body = json.dumps({"job": "2026-01-01-job", "bed": "bed-1", "printer_uuid": "x"}).encode()
    assert _req(viewer + "/api/send", "POST", {}, body)[0] == 403                       # cross-site form post
    assert _req(viewer + "/api/send", "POST", {"X-Workbench": "1", "Host": "evil.example"}, body)[0] == 403
    assert _req(viewer + "/api/connect", headers={"Host": "evil.example"})[0] == 403     # DNS rebinding


NODE_HARNESS = """
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
globalThis.self = { postMessage() {} };
const parse = new Function(src + '; return parse;')();
const out = parse(fs.readFileSync(0, 'utf8'), () => {});
console.log(JSON.stringify({segs: Array.from(out.segs), travels: Array.from(out.travels), types: out.types,
  layerZ: Array.from(out.layerZ), layerFirst: Array.from(out.layerFirst), layerEnd: Array.from(out.layerEnd), nSeg: out.nSeg}));
"""


def _parse_js(gcode_text):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    p = subprocess.run([node, "-e", NODE_HARNESS, str(WORKER)], input=gcode_text, capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_js_parser_layers_types_travel():
    out = _parse_js("\n".join([
        "M83", "G1 X0 Y0 Z0.2", "G1 X5 Y0 E0.5 ; purge before the first layer",
        ";LAYER_CHANGE", ";Z:0.2", ";HEIGHT:0.2", ";TYPE:Perimeter", ";WIDTH:0.45",
        "G1 X10 Y0 E1", "G1 X20 Y5", "G1 X30 Y5 E1",
        ";LAYER_CHANGE", ";Z:0.4", "G1 Z0.4", ";TYPE:Solid infill", "G1 X40 Y5 E1",
    ]))
    S = 10
    segs = [out["segs"][i * S:(i + 1) * S] for i in range(out["nSeg"])]
    assert out["nSeg"] == 4
    assert [out["types"][int(s[8])] for s in segs] == ["Custom", "Perimeter", "Perimeter", "Solid infill"]
    assert [int(s[9]) for s in segs] == [0, 0, 0, 1]                 # purge line counts as layer 1
    assert out["layerZ"] == pytest.approx([0.2, 0.4]) and out["layerFirst"] == [0, 3] and out["layerEnd"] == [3, 4]
    assert len(out["travels"]) // 8 == 1                              # the non-extruding move


def test_js_parser_arcs():
    # G2 (clockwise) half circle from (10,0) to (-10,0) around (0,0) passes through (0,10)? No: clockwise from
    # angle 0 to 180 goes through angle -90 = (0,-10). G3 goes through (0,10).
    for cmd, mid_y in (("G2", -10), ("G3", 10)):
        out = _parse_js(f"M83\nG1 X10 Y0 Z0.2\n;LAYER_CHANGE\n;Z:0.2\n;TYPE:Perimeter\n{cmd} X-10 Y0 I-10 J0 E2\n")
        S = 10
        pts = [(out["segs"][i * S + 3], out["segs"][i * S + 4]) for i in range(out["nSeg"])]
        assert out["nSeg"] > 10
        assert all(math.hypot(x, y) == pytest.approx(10, abs=1e-3) for x, y in pts)
        assert pts[-1] == pytest.approx((-10, 0), abs=1e-4)
        assert min(pts, key=lambda p: abs(p[0]))[1] == pytest.approx(mid_y, abs=0.5)
