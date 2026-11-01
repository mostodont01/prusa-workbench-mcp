"""The viewer in its own window (GTK 3 + WebKit2 4.1 from the system python3; the venv has no `gi`).

    /usr/bin/python3 -m prusa_workbench.app_window            # opens the window (one instance; a 2nd run raises it)
    /usr/bin/python3 -m prusa_workbench.app_window --install  # app menu entry + icon (~/.local/share)

Two tabs: Workbench (the viewer) and Prusa Connect (connect.prusa3d.com, login kept in
~/.local/share/prusa-workbench/webkit). After a successful send the page asks the app (window.webkit.messageHandlers
.workbench) to show the printer in the Connect tab; when Claude slices, it switches back to the Workbench.
Starts the viewer server if it isn't running. Other sites open in the default browser.
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
CONNECT_HOME = "https://connect.prusa3d.com/"


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
        "StartupNotify=true\n", encoding="utf-8")
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
    if not sys.platform.startswith("linux"):
        print("The app window is for Linux (GTK + WebKit). On Windows/macOS open http://127.0.0.1:8765 in Edge or "
              "Chrome and use 'Install app' (the viewer is a PWA): python -m prusa_workbench.viewer --open",
              file=sys.stderr)
        return 1
    if args.install:
        install()
        return 0
    if args.uninstall:
        uninstall()
        return 0

    os.environ.setdefault("GDK_BACKEND", "x11")   # XWayland: lets a background "show the window" bring it forward
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    try:
        gi.require_version("GdkX11", "3.0")
    except ValueError:
        pass
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

    def is_prusa(u: str) -> bool:
        host = urlsplit(u).hostname or ""
        return host == "prusa3d.com" or host.endswith(".prusa3d.com")

    # One persistent web context for both tabs, so the Prusa Connect login survives restarts.
    data_dir = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "prusa-workbench" / "webkit"
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "prusa-workbench" / "webkit"
    data_dir.mkdir(parents=True, exist_ok=True)
    manager = WebKit2.WebsiteDataManager(base_data_directory=str(data_dir), base_cache_directory=str(cache_dir))
    context = WebKit2.WebContext(website_data_manager=manager)
    context.get_cookie_manager().set_persistent_storage(str(data_dir / "cookies.sqlite"),
                                                        WebKit2.CookiePersistentStorage.SQLITE)

    def new_view(ucm=None) -> WebKit2.WebView:
        view = WebKit2.WebView(web_context=context, user_content_manager=ucm or WebKit2.UserContentManager())
        st = view.get_settings()
        st.set_enable_webgl(True)
        st.set_enable_developer_extras(True)            # right click -> Inspect, if ever needed
        return view

    def external(u: str) -> None:
        if u.startswith(("http://", "https://")):
            Gio.AppInfo.launch_default_for_uri(u, None)

    if args.snapshot:
        view = new_view()
        view.load_uri(url)
        return _snapshot(view, args.snapshot, Gtk, GLib, WebKit2)

    app = Gtk.Application(application_id=APP_ID)
    logf = os.environ.get("PRUSA_WORKBENCH_APP_LOG")
    ui: dict = {}

    def log(msg):
        if logf:
            with open(logf, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

    def show_tab(name: str, connect_url: str | None = None) -> None:
        if name == "connect":
            v = connect_view()
            target = connect_url or (None if v.get_uri() else CONNECT_HOME)
            if target and v.get_uri() != target:
                v.load_uri(target)
        ui["stack"].set_visible_child_name(name)

    def connect_view() -> WebKit2.WebView:
        """The Prusa Connect tab, created the first time it is needed."""
        if "connect" in ui:
            return ui["connect"]
        v = new_view()

        def on_policy(_v, decision, kind):
            if kind != WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
                return False                             # navigation inside Connect / Prusa Account: stay here
            target = decision.get_navigation_action().get_request().get_uri() or ""
            decision.ignore()
            v.load_uri(target) if is_prusa(target) else external(target)
            return True

        v.connect("decide-policy", on_policy)
        v.connect("notify::uri", lambda *_: update_buttons())
        ui["connect"] = v
        ui["stack"].add_titled(v, "connect", "Prusa Connect")
        v.show()
        ph = ui.pop("placeholder", None)
        if ph is not None:
            ui["stack"].remove(ph)                       # the real tab replaces the placeholder
        return v

    def workbench_view() -> WebKit2.WebView:
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("workbench")   # the page: window.webkit.messageHandlers.workbench

        def on_message(_ucm, js_result):
            import json
            try:
                msg = json.loads(js_result.get_js_value().to_string())
            except (ValueError, AttributeError):
                return
            if msg.get("action") == "connect":
                url_ = msg.get("url") or CONNECT_HOME
                show_tab("connect", url_ if is_prusa(url_) else None)
            elif msg.get("action") == "workbench":
                show_tab("workbench")

        ucm.connect("script-message-received::workbench", on_message)
        v = new_view(ucm)

        def on_policy(_v, decision, kind):
            if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
                return False
            target = decision.get_navigation_action().get_request().get_uri() or ""
            if urlsplit(target).netloc == local and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                return False
            decision.ignore()
            if is_prusa(target):
                show_tab("connect", target)              # Prusa Connect links: the Connect tab
            else:
                external(target)                         # anything else: the default browser
            return True

        v.connect("decide-policy", on_policy)
        v.load_uri(url)
        return v

    def update_buttons():
        on_connect = ui["stack"].get_visible_child_name() == "connect"
        ui["back"].set_sensitive(on_connect and "connect" in ui and ui["connect"].can_go_back())
        ui["browser"].set_visible(on_connect)

    def on_activate(a):
        if a.get_windows():
            win = a.get_windows()[0]
            log(f"activate again: active before = {win.is_active()}")
            _bring_to_front(win, Gtk, GLib)
            GLib.timeout_add(1500, lambda: log(f"after present: active = {win.is_active()}") or False)
            return
        win = Gtk.ApplicationWindow(application=a, title=NAME)
        win.set_default_size(1440, 900)
        if ICON.exists():
            win.set_icon_from_file(str(ICON))

        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        ui["stack"] = stack
        wb = workbench_view()
        ui["workbench"] = wb
        stack.add_titled(wb, "workbench", "Workbench")
        # Placeholder so the switcher shows both tabs before Connect is loaded.
        placeholder = Gtk.Box()
        stack.add_titled(placeholder, "connect-placeholder", "Prusa Connect")
        ui["placeholder"] = placeholder

        header = Gtk.HeaderBar(show_close_button=True, title=NAME)
        switcher = Gtk.StackSwitcher(stack=stack)
        header.set_custom_title(switcher)
        back = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        back.set_tooltip_text("Back (Alt+Left)")
        back.connect("clicked", lambda *_: ui["connect"].go_back() if "connect" in ui else None)
        reload_ = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        reload_.set_tooltip_text("Reload (Ctrl+R)")
        reload_.connect("clicked", lambda *_: stack.get_visible_child().reload()
                        if isinstance(stack.get_visible_child(), WebKit2.WebView) else None)
        browser = Gtk.Button.new_from_icon_name("web-browser-symbolic", Gtk.IconSize.BUTTON)
        browser.set_tooltip_text("Open this page in the browser")
        browser.connect("clicked", lambda *_: external(ui["connect"].get_uri() or CONNECT_HOME) if "connect" in ui else None)
        header.pack_start(back)
        header.pack_start(reload_)
        header.pack_end(browser)
        ui.update(back=back, browser=browser)
        win.set_titlebar(header)
        win.add(stack)

        def on_switch(*_):
            if ui.get("ready") and stack.get_visible_child_name() == "connect-placeholder":
                GLib.idle_add(lambda: show_tab("connect") or False)   # first visit: create and load Connect
            update_buttons()

        stack.connect("notify::visible-child-name", on_switch)

        def on_key(_w, ev):
            from gi.repository import Gdk
            ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
            alt = ev.state & Gdk.ModifierType.MOD1_MASK
            if ctrl and ev.keyval == Gdk.KEY_1:
                show_tab("workbench")
            elif ctrl and ev.keyval == Gdk.KEY_2:
                show_tab("connect")
            elif ctrl and ev.keyval in (Gdk.KEY_r, Gdk.KEY_R):
                child = stack.get_visible_child()
                if isinstance(child, WebKit2.WebView):
                    child.reload()
            elif alt and ev.keyval == Gdk.KEY_Left and "connect" in ui and stack.get_visible_child_name() == "connect":
                ui["connect"].go_back()
            else:
                return False
            return True

        win.connect("key-press-event", on_key)
        win.show_all()
        stack.set_visible_child_name("workbench")       # GTK may pick another child while showing
        ui["ready"] = True
        update_buttons()
        if os.environ.get("PRUSA_WORKBENCH_APP_SELFTEST"):
            _selftest(a, ui, log, GLib)

    app.connect("activate", on_activate)
    return app.run([sys.argv[0]])


def _selftest(app, ui, log, GLib) -> None:
    """(testing) What the page does after a send, then after Claude slices; logs the tab state, then quits."""
    def page(js):
        ui["workbench"].evaluate_javascript(js, -1, None, None, None, None)

    def state(label):
        st = ui["stack"]
        c = ui.get("connect")
        log(f"{label}: tab={st.get_visible_child_name()} tabs={[x.get_name() if False else st.child_get_property(x, 'title') for x in st.get_children()]}"
            f" connect_uri={c.get_uri() if c else None} connect_title={c.get_title() if c else None}")

    steps = [
        (4000, lambda: state("start")),
        (100, lambda: ui["stack"].set_visible_child_name("connect-placeholder")),   # a click on the Connect tab
        (6000, lambda: state("after clicking the Connect tab")),
        (100, lambda: page("window.webkit.messageHandlers.workbench.postMessage(JSON.stringify({action: 'workbench'}))")),
        (100, lambda: page("window.webkit.messageHandlers.workbench.postMessage(JSON.stringify("
                            "{action: 'connect', url: 'https://connect.prusa3d.com/'}))")),
        (9000, lambda: state("after send")),
        (100, lambda: page("window.webkit.messageHandlers.workbench.postMessage(JSON.stringify({action: 'workbench'}))")),
        (1500, lambda: state("after slice")),
        (100, lambda: app.quit()),
    ]
    t = 0
    for delay, fn in steps:
        t += delay
        GLib.timeout_add(t, lambda fn=fn: fn() or False)


def _bring_to_front(win, Gtk, GLib) -> None:
    """Raise and focus the window when asked from outside (Claude sliced something).
    On X11/XWayland a request with the X server's current time counts as fresh, so GNOME lets it through.
    (On native Wayland a program can't take focus without an activation token, which a background
    process doesn't have.)"""
    win.deiconify()
    gdk_win = win.get_window()
    ts = 0
    try:
        gi_x11 = __import__("gi.repository.GdkX11", fromlist=["GdkX11"])
        if isinstance(gdk_win, gi_x11.X11Window):
            ts = gi_x11.x11_get_server_time(gdk_win)
    except (ImportError, ValueError, AttributeError):
        pass
    win.set_keep_above(True)
    win.present_with_time(ts) if ts else win.present()
    GLib.timeout_add(300, lambda: win.set_keep_above(False) or False)


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
