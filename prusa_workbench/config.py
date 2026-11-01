"""Paths and defaults. Everything can be overridden with environment variables.

PrusaSlicer is found per platform (first match wins):
- PRUSA_WORKBENCH_SLICER: path to the executable (or, on Linux/macOS, a whole command line)
- Linux: the Flatpak com.prusa3d.PrusaSlicer, else `prusa-slicer` / `PrusaSlicer` on PATH
- Windows: prusa-slicer-console.exe (the console build; prusa-slicer.exe prints nothing) in Program Files or on PATH
- macOS: /Applications/PrusaSlicer.app
Its config folder (for the presets selected in the PrusaSlicer window): PRUSA_WORKBENCH_SLICER_CONFIG, else the
platform's usual place.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
HOME = Path.home()

# Job folders. Under $HOME by default: the PrusaSlicer Flatpak cannot see /tmp.
JOBS_DIR = Path(os.environ.get("PRUSA_WORKBENCH_JOBS", "~/print-jobs")).expanduser()
CACHE_DIR = JOBS_DIR / "_cache"

FLATPAK_ID = "com.prusa3d.PrusaSlicer"


def _flatpak_installed() -> bool:
    return bool(shutil.which("flatpak")) and any(
        (d / "app" / FLATPAK_ID).exists()
        for d in (Path("/var/lib/flatpak"), HOME / ".local/share/flatpak"))


def _find_prusaslicer() -> tuple[list[str], Path]:
    """(command, config folder)."""
    env_cmd = os.environ.get("PRUSA_WORKBENCH_SLICER")
    env_cfg = os.environ.get("PRUSA_WORKBENCH_SLICER_CONFIG")
    cmd: list[str] | None = None
    cfg: Path | None = None
    if env_cmd:
        # A plain existing path (Windows paths have spaces and backslashes) or a command line to split.
        cmd = [env_cmd] if Path(env_cmd).exists() else shlex.split(env_cmd, posix=not WINDOWS)
    if WINDOWS:
        if cmd is None:
            roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA")]
            candidates = [Path(r) / sub / "prusa-slicer-console.exe" for r in roots if r
                          for sub in ("Prusa3D/PrusaSlicer", "PrusaSlicer", "Programs/PrusaSlicer")]
            found = next((c for c in candidates if c.exists()), None) or shutil.which("prusa-slicer-console")
            cmd = [str(found or "prusa-slicer-console.exe")]
        cfg = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming")) / "PrusaSlicer"
    elif MACOS:
        cmd = cmd or [shutil.which("PrusaSlicer") or "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"]
        cfg = HOME / "Library/Application Support/PrusaSlicer"
    else:
        flatpak_cfg = HOME / ".var/app" / FLATPAK_ID / "config/PrusaSlicer"
        if cmd is None and _flatpak_installed():
            cmd = ["flatpak", "run", "--command=prusa-slicer", FLATPAK_ID]
            cfg = flatpak_cfg
        if cmd is None:
            cmd = [shutil.which("prusa-slicer") or shutil.which("PrusaSlicer") or "prusa-slicer"]
        if cfg is None:
            cfg = flatpak_cfg if cmd[0] == "flatpak" else Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "PrusaSlicer"
    return cmd, Path(env_cfg).expanduser() if env_cfg else cfg


PRUSASLICER, PRUSASLICER_CONFIG = _find_prusaslicer()

VIEWER_HOST = "127.0.0.1"
VIEWER_PORT = int(os.environ.get("PRUSA_WORKBENCH_VIEWER_PORT", "8765"))

ONSHAPE_ENV_FILE = Path(os.environ.get("PRUSA_WORKBENCH_ONSHAPE_ENV", "~/onshape-mcp/.env")).expanduser()

SPACING = 6.0  # default gap between arranged objects, mm


def selected_presets() -> dict[str, str]:
    """The printer / print / filament currently selected in the PrusaSlicer window (PrusaSlicer.ini [presets])."""
    out: dict[str, str] = {}
    ini = PRUSASLICER_CONFIG / "PrusaSlicer.ini"
    try:
        section = None
        for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif section == "presets" and " = " in line:
                k, v = line.split(" = ", 1)
                out[k] = v
    except OSError:
        pass
    return {
        "printer": out.get("printer") or "Original Prusa MINI & MINI+ Input Shaper",
        "print": out.get("print") or "0.20mm SPEED @MINIIS 0.4",
        "filament": out.get("filament") or "Prusament PLA @MINIIS",
    }
