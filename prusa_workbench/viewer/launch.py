"""Start the viewer in the background (if it isn't running) and open it in the browser.

Used from the MCP server, whose stdout is the MCP protocol: nothing here may write to stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from ..config import CACHE_DIR, JOBS_DIR, VIEWER_HOST, VIEWER_PORT

URL = f"http://{VIEWER_HOST}:{VIEWER_PORT}/"
REPO = Path(__file__).resolve().parents[2]


def running() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=1) as r:
            return json.loads(r.read()).get("app") == "prusa-workbench-viewer"
    except (OSError, ValueError):
        return False


def ensure_running() -> bool:
    """Returns True if it had to be started."""
    if running():
        return False
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log = (CACHE_DIR / "viewer.log").open("ab")
    env = dict(os.environ, PYTHONPATH=str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    subprocess.Popen([sys.executable, "-m", "prusa_workbench.viewer", "--port", str(VIEWER_PORT),
                      "--host", VIEWER_HOST, "--jobs", str(JOBS_DIR)],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=REPO, env=env, start_new_session=True)
    for _ in range(50):
        if running():
            return True
        time.sleep(0.1)
    raise RuntimeError(f"viewer did not start, see {CACHE_DIR / 'viewer.log'}")


APP_ID = "io.github.mostodont01.PrusaWorkbench"
GTK_PYTHON = os.environ.get("PRUSA_WORKBENCH_GTK_PYTHON", "/usr/bin/python3")


def open_ui() -> None:
    """Open the viewer: the app window if it's installed (app_window --install), else the browser.
    The app window is single-instance, so this just raises it when it's already open.
    PRUSA_WORKBENCH_OPEN=browser forces the browser."""
    data = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    if os.environ.get("PRUSA_WORKBENCH_OPEN") != "browser" and (data / "applications" / f"{APP_ID}.desktop").exists():
        try:
            subprocess.Popen([GTK_PYTHON, "-m", "prusa_workbench.app_window"], cwd=REPO,
                             env=dict(os.environ, PYTHONPATH=str(REPO)), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
        except OSError:
            pass
    open_browser()


def open_browser(url: str = URL) -> None:
    for cmd in (["xdg-open", url], ["open", url]):
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return
        except FileNotFoundError:
            continue
