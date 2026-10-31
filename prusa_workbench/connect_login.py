"""Prusa Account login window. Run with a Python that has GTK 3 + WebKit2 4.1 (the system python3):

    /usr/bin/python3 -m prusa_workbench.connect_login

Opens the Prusa Account sign-in page in a small window (a real WebKit browser, so 2FA etc. work). After sign-in
Prusa Account redirects to prusaslicer://login?code=...; the window catches that before the system would hand it
to PrusaSlicer, exchanges the code (PKCE) and saves the tokens. Prints one JSON line: {"ok": true} or an error.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import urllib.parse

from . import connect


def main() -> int:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import GLib, Gtk, WebKit2
    except (ImportError, ValueError) as e:
        print(json.dumps({"ok": False, "error": f"GTK/WebKit2 not available in this Python ({e}). "
                                                "Run it with the system python3 (needs gir1.2-webkit2-4.1)."}))
        return 2

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = connect.AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": connect.CLIENT_ID, "response_type": "code", "redirect_uri": connect.REDIRECT_URI,
        "scope": connect.SCOPE, "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    result: dict = {"ok": False, "error": "login window closed"}

    win = Gtk.Window(title="Prusa Connect – sign in (prusa-workbench)")
    win.set_default_size(520, 780)
    ctx = WebKit2.WebContext.new_ephemeral()          # no cookies left behind
    view = WebKit2.WebView.new_with_context(ctx)
    win.add(view)

    def finish(res: dict):
        result.clear(); result.update(res)
        win.destroy()

    def on_policy(_view, decision, kind):
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri() or ""
        if not uri.startswith("prusaslicer://"):
            return False
        decision.ignore()                                # never let the system open PrusaSlicer with it
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(uri).query)
        if q.get("error"):
            finish({"ok": False, "error": f"{q['error'][0]}: {q.get('error_description', [''])[0]}"})
        elif q.get("state", [None])[0] != state:
            finish({"ok": False, "error": "state mismatch, login aborted"})
        elif not q.get("code"):
            finish({"ok": False, "error": "no code in the login reply"})
        else:
            try:
                connect.exchange_code(q["code"][0], verifier)
                finish({"ok": True})
            except Exception as e:
                finish({"ok": False, "error": str(e)})
        return True

    view.connect("decide-policy", on_policy)
    win.connect("destroy", lambda *_: Gtk.main_quit())
    GLib.timeout_add_seconds(600, lambda: finish({"ok": False, "error": "timed out (10 min)"}) or False)
    view.load_uri(url)
    win.show_all()
    Gtk.main()
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
