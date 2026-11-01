"""The few things that differ between Linux, Windows and macOS. Stdlib only (send2trash if installed)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
LINUX = not WINDOWS and not MACOS


def detached() -> dict:
    """Popen arguments for a child that outlives us and (on Windows) gets no console window."""
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags}
    return {"start_new_session": True}


def no_window() -> dict:
    """Popen/run arguments so a console program doesn't flash a window on Windows."""
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if WINDOWS else {}


def open_url(url: str) -> None:
    """Open a URL in the default browser without writing anything to our stdout (the MCP protocol)."""
    if WINDOWS:
        os.startfile(url)  # type: ignore[attr-defined]
        return
    cmd = ["open", url] if MACOS else ["xdg-open", url]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **detached())
    except FileNotFoundError:
        pass


def trash(path: Path) -> str:
    """Move a file/folder to the desktop trash. Returns 'trash', or 'deleted' if there is no trash to use."""
    if LINUX and shutil.which("gio"):
        try:
            subprocess.run(["gio", "trash", str(path)], check=True, capture_output=True, timeout=60)
            return "trash"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    try:
        from send2trash import send2trash   # Windows recycle bin, macOS Trash
        send2trash(str(path))
        return "trash"
    except ImportError:
        pass
    except OSError:
        if not path.exists():
            return "trash"
    if path.exists():
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    return "deleted"
