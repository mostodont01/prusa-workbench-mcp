"""The viewer in its own window (GTK 3 + WebKit2 4.1 from the system python3; the venv has no `gi`).

    /usr/bin/python3 -m prusa_workbench.app_window            # opens the window (one instance; a 2nd run raises it)
    /usr/bin/python3 -m prusa_workbench.app_window --install  # app menu entry + icon (~/.local/share)

Starts the viewer server if it isn't running. Links to other sites (Prusa Connect) open in the default browser.
Firefox on Linux can't install web apps (PWAs), so this is the "app" version of the viewer.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

APP_ID = "io.github.mostodont01.PrusaWorkbench"
NAME = "Prusa Workbench"
REPO = Path(__file__).resolve().parents[1]
ICON = Path(__file__).parent / "viewer" / "static" / "icon.png"


def install() -> None:
    """App menu entry (+ icon) for this user. Exec uses this python and this checkout of the repo."""
    data = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    icon_dir = data / "icons" / "hicolor" / "256x256" / "apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, icon_dir / f"{APP_ID}.png")
    apps = data / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    desktop = apps / f"{APP_ID}.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        "Comment=Check sliced beds and send them to the printer\n"
        f"Exec=env PYTHONPATH={REPO} {sys.executable} -m prusa_workbench.app_window\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Graphics;3DGraphics;Engineering;\n"
        f"StartupWMClass={APP_ID}\n"
        "StartupNotify=true\n")
    for cmd in (["update-desktop-database", str(apps)], ["gtk-update-icon-cache", "-q", "-t", str(data / "icons" / "hicolor")]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
    print(f"installed {desktop}")


def uninstall() -> None:
    data = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    for f in (data / "applications" / f"{APP_ID}.desktop", data / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"):
        f.unlink(missing_ok=True)
    print("removed the app menu entry")


def main() -> int:
    ap = argparse.ArgumentParser(prog="prusa_workbench.app_window")
    ap.add_argument("--install", action="store_true", help="add to the app menu")
    ap.add_argument("--uninstall", action="store_true", help="remove from the app menu")
    ap.add_argument("--snapshot", metavar="PNG", help="(testing) render offscreen, save a picture, exit")
    args = ap.parse_args()
    if args.install:
        install()
        return 0
    if args.uninstall:
        uninstall()
        return 0

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gio, GLib, Gtk, WebKit2

    from .viewer import launch

    GLib.set_prgname(APP_ID)
    GLib.set_application_name(NAME)
    try:
        launch.ensure_running()
    except Exception as e:
        print(f"could not start the viewer server: {e}", file=sys.stderr)
        return 1
    url = launch.URL
    local = urlsplit(url).netloc

    def make_view() -> WebKit2.WebView:
        view = WebKit2.WebView()
        s = view.get_settings()
        s.set_enable_webgl(True)
        s.set_enable_developer_extras(True)            # right click -> Inspect, if ever needed

        def on_policy(_v, decision, kind):
            if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
                return False
            target = decision.get_navigation_action().get_request().get_uri() or ""
            if urlsplit(target).netloc == local and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                return False
            decision.ignore()                          # other sites / new windows: default browser
            if target.startswith(("http://", "https://")):
                Gio.AppInfo.launch_default_for_uri(target, None)
            return True

        view.connect("decide-policy", on_policy)
        view.load_uri(url)
        return view

    if args.snapshot:
        return _snapshot(make_view(), args.snapshot, Gtk, GLib, WebKit2)

    app = Gtk.Application(application_id=APP_ID)

    def on_activate(a):
        if a.get_windows():
            a.get_windows()[0].present()
            return
        win = Gtk.ApplicationWindow(application=a, title=NAME)
        win.set_default_size(1440, 900)
        if ICON.exists():
            win.set_icon_from_file(str(ICON))
        win.add(make_view())
        win.show_all()

    app.connect("activate", on_activate)
    return app.run([sys.argv[0]])


def _snapshot(view, path, Gtk, GLib, WebKit2) -> int:
    """For tests: open the window, wait until the toolpaths are in (legend filled), save a PNG, close.
    (A real window: WebKitGTK needs OpenGL, which an offscreen window doesn't have.)"""
    win = Gtk.Window(title=f"{NAME} (test)")
    win.set_default_size(1440, 900)
    win.add(view)
    win.show_all()
    state = {"tries": 0, "rc": 1}

    def check():
        state["tries"] += 1
        if state["tries"] > 120:
            print("timed out waiting for the viewer to render", file=sys.stderr)
            Gtk.main_quit()
            return False
        view.evaluate_javascript("document.querySelector('#legend') && document.querySelector('#legend').children.length > 0"
                                 " ? 'ready:' + !!document.querySelector('canvas').getContext : 'wait'",
                                 -1, None, None, None, on_js)
        return False

    def on_js(v, res):
        try:
            val = v.evaluate_javascript_finish(res).to_string()
        except GLib.Error as e:
            val = f"error {e}"
        if not val.startswith("ready"):
            GLib.timeout_add(250, check)
            return
        # The 3D view as a data URL from the page (saving a WebKit snapshot needs python3-gi-cairo).
        GLib.timeout_add(800, lambda: v.evaluate_javascript("window.__viewerSnapshot()", -1, None, None, None,
                                                            on_snap) and False)

    def on_snap(v, res):
        import base64
        data = v.evaluate_javascript_finish(res).to_string()
        Path(path).write_bytes(base64.b64decode(data.split(",", 1)[1]))
        state["rc"] = 0
        Gtk.main_quit()

    GLib.timeout_add(500, check)
    Gtk.main()
    return state["rc"]


if __name__ == "__main__":
    sys.exit(main())
