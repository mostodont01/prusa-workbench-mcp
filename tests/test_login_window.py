"""The login window catches the prusaslicer:// redirect (without a Prusa account: a local server redirects).
Opens a real window, so it only runs with PRUSA_WORKBENCH_GUI_TESTS=1 (set on the Windows CI runner)."""
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not os.environ.get("PRUSA_WORKBENCH_GUI_TESTS"),
                                reason="opens a window; set PRUSA_WORKBENCH_GUI_TESTS=1")


def _python_with_browser_window():
    if sys.platform.startswith("linux") and os.path.exists("/usr/bin/python3"):
        ok = subprocess.run(["/usr/bin/python3", "-c", "import gi; gi.require_version('WebKit2', '4.1')"],
                            capture_output=True).returncode == 0
        if ok:
            return "/usr/bin/python3"
    try:
        import webview  # noqa: F401
        return sys.executable
    except ImportError:
        pytest.skip("neither GTK/WebKit2 nor pywebview available")


def test_redirect_is_caught():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/":   # like Prusa Account after sign-in: a page, then a redirect to the custom scheme
                body = b"<html><body>signing in...<script>setTimeout(()=>location.href='/go', 300)</script></body></html>"
                self.send_response(200); self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            else:
                self.send_response(302); self.send_header("Location", "prusaslicer://login?code=abc123&state=s1")
                self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    py = _python_with_browser_window()
    code = ("from prusa_workbench.connect_login import capture_redirect; "
            f"print(capture_redirect('http://127.0.0.1:{srv.server_address[1]}/', timeout=60))")
    p = subprocess.Popen([py, "-c", code], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env={**os.environ, "PYTHONPATH": str(REPO), "PRUSA_WORKBENCH_LOGIN_DEBUG": "1"})
    try:
        out, err = p.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        pytest.fail(f"login window hung. stdout: {out!r} stderr: {err[-2000:]}")
    finally:
        srv.shutdown()
    lines = out.strip().splitlines()
    assert lines and lines[-1] == "prusaslicer://login?code=abc123&state=s1", f"stdout: {out!r} stderr: {err[-2000:]}"
