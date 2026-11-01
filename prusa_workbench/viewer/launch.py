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
from ..osutil import LINUX, detached, open_url

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
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=REPO, env=env, **detached())
    for _ in range(50):
        if running():
            return True
        time.sleep(0.1)
    raise RuntimeError(f"viewer did not start, see {CACHE_DIR / 'viewer.log'}")


def show_job(job: str) -> None:
    """Tell open viewer pages to switch to this job (e.g. the one Claude just sliced)."""
    req = urllib.request.Request(URL + "api/show", data=json.dumps({"job": job}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Workbench": "1"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except OSError:
        pass


APP_ID = "io.github.mostodont01.PrusaWorkbench"
GTK_PYTHON = os.environ.get("PRUSA_WORKBENCH_GTK_PYTHON", "/usr/bin/python3")


def app_installed() -> bool:
    """The Linux app window (GTK). On Windows/macOS the viewer is installed as a PWA from the browser instead."""
    if not LINUX or os.environ.get("PRUSA_WORKBENCH_OPEN") == "browser":
        return False
    data = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    return (data / "applications" / f"{APP_ID}.desktop").exists()


def open_ui() -> None:
    """Open the viewer: the app window if it's installed (app_window --install), else the browser.
    The app window is single-instance, so this just brings it to the front when it's already open.
    PRUSA_WORKBENCH_OPEN=browser forces the browser."""
    if app_installed():
        try:
            subprocess.Popen([GTK_PYTHON, "-m", "prusa_workbench.app_window"], cwd=REPO,
                             env=dict(os.environ, PYTHONPATH=str(REPO)), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached())
            return
        except OSError:
            pass
    open_browser()


def open_browser(url: str = URL) -> None:
    open_url(url)
