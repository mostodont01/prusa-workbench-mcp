"""Paths and defaults. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

# Job folders. Must be under $HOME: the PrusaSlicer Flatpak cannot see /tmp.
JOBS_DIR = Path(os.environ.get("PRUSA_WORKBENCH_JOBS", "~/print-jobs")).expanduser()
CACHE_DIR = JOBS_DIR / "_cache"

PRUSASLICER = shlex.split(os.environ.get(
    "PRUSA_WORKBENCH_SLICER", "flatpak run --command=prusa-slicer com.prusa3d.PrusaSlicer"))
PRUSASLICER_CONFIG = Path(os.environ.get(
    "PRUSA_WORKBENCH_SLICER_CONFIG", "~/.var/app/com.prusa3d.PrusaSlicer/config/PrusaSlicer")).expanduser()

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
        for line in ini.read_text(errors="replace").splitlines():
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
        "filament": out.get("filament") or "Prusament PLA",
    }
