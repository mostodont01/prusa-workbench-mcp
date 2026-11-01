"""Prusa Account login window.

    Linux:          /usr/bin/python3 -m prusa_workbench.connect_login   (GTK 3 + WebKit2 4.1 from the system)
    Windows/macOS:  python -m prusa_workbench.connect_login             (pywebview: Edge WebView2 / WKWebView)

Opens the Prusa Account sign-in page in a small window (a real browser engine, so 2FA etc. work). After sign-in
Prusa Account redirects to prusaslicer://login?code=...; the window catches that before the system would hand it
to PrusaSlicer, exchanges the code (PKCE) and saves the tokens. Prints one JSON line: {"ok": true} or an error.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse

from . import connect

SCHEME = "prusaslicer://"
TIMEOUT = 600


def capture_redirect(start_url: str, prefix: str = SCHEME, title: str = "Prusa Connect – sign in",
                     timeout: float = TIMEOUT) -> str | None:
    """Open start_url in a small browser window and return the first URL navigated to that starts with `prefix`
    (without letting the system open it). None if the window was closed or timed out."""
    try:
        return _capture_gtk(start_url, prefix, title, timeout)
    except (ImportError, ValueError):
        pass   # no GTK/WebKit in this Python: try pywebview
    return _capture_pywebview(start_url, prefix, title, timeout)


def _capture_gtk(start_url, prefix, title, timeout):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import GLib, Gtk, WebKit2

    got: dict = {}
    win = Gtk.Window(title=title)
    win.set_default_size(520, 780)
    view = WebKit2.WebView.new_with_context(WebKit2.WebContext.new_ephemeral())   # no cookies left behind
    win.add(view)

    def on_policy(_view, decision, kind):
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri() or ""
        if not uri.startswith(prefix):
            return False
        decision.ignore()                                 # never let the system open PrusaSlicer with it
        got["url"] = uri
        win.destroy()
        return True

    view.connect("decide-policy", on_policy)
    win.connect("destroy", lambda *_: Gtk.main_quit())
    GLib.timeout_add_seconds(int(timeout), lambda: win.destroy() or False)
    view.load_uri(start_url)
    win.show_all()
    Gtk.main()
    return got.get("url")


def _debug(*a):
    if os.environ.get("PRUSA_WORKBENCH_LOGIN_DEBUG"):
        print("[login]", *a, file=sys.stderr, flush=True)


def _capture_pywebview(start_url, prefix, title, timeout):
    try:
        import webview
    except ImportError:
        raise RuntimeError("no browser window available: install pywebview (pip install pywebview), "
                           "or on Linux run with the system python3 that has GTK + WebKit2") from None
    got: dict = {}
    done = threading.Event()
    win = webview.create_window(title, start_url, width=520, height=780)

    def close_soon():
        threading.Thread(target=lambda: _safe(win.destroy), daemon=True).start()

    def attach_webview2():
        """Windows (Edge WebView2): runs on the UI thread right after the browser control exists. Catch the
        custom-scheme navigation as it starts (also when it comes as a redirect) and cancel it."""
        try:
            control = win.native.browser.webview          # pywebview's WinForms WebView2 control
        except AttributeError as e:
            _debug("no WebView2 control:", e)
            return

        def on_start(_sender, args):
            uri = str(args.Uri or "")
            _debug("navigation starting:", uri[:80])
            if uri.startswith(prefix) and not done.is_set():
                args.Cancel = True                        # don't let Windows hand it to PrusaSlicer
                got["url"] = uri
                done.set()
                close_soon()

        control.NavigationStarting += on_start
        _debug("WebView2 hook attached")

    def watch():
        """Fallback for other backends (macOS WKWebView): poll the current URL; also the timeout."""
        deadline = time.monotonic() + timeout
        while not done.is_set() and time.monotonic() < deadline:
            url = _safe(win.get_current_url) or ""
            if url.startswith(prefix):
                got["url"] = url
                break
            done.wait(0.2)
        close_soon()

    win.events.before_show += attach_webview2
    win.events.closed += done.set
    webview.start(watch, private_mode=True)
    _debug("window closed, got:", got.get("url"))
    return got.get("url")


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def main() -> int:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = connect.AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": connect.CLIENT_ID, "response_type": "code", "redirect_uri": connect.REDIRECT_URI,
        "scope": connect.SCOPE, "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    try:
        uri = capture_redirect(url)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    if not uri:
        result = {"ok": False, "error": "login window closed"}
    else:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(uri).query)
        if q.get("error"):
            result = {"ok": False, "error": f"{q['error'][0]}: {q.get('error_description', [''])[0]}"}
        elif q.get("state", [None])[0] != state:
            result = {"ok": False, "error": "state mismatch, login aborted"}
        elif not q.get("code"):
            result = {"ok": False, "error": "no code in the login reply"}
        else:
            try:
                connect.exchange_code(q["code"][0], verifier)
                result = {"ok": True}
            except Exception as e:
                result = {"ok": False, "error": str(e)}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
