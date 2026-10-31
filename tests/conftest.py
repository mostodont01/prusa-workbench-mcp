"""Test setup.

Tests marked `slicer` run the real PrusaSlicer (Flatpak) and take a few seconds each. They are the ones that
catch a PrusaSlicer update changing behavior we rely on. Skip them with `pytest -m "not slicer"`.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pytest
import trimesh

from prusa_workbench import config


def _have_slicer() -> bool:
    try:
        return subprocess.run([*config.PRUSASLICER, "--help"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


HAVE_SLICER = _have_slicer()


def pytest_configure(config):
    config.addinivalue_line("markers", "slicer: runs the real PrusaSlicer CLI (slow)")


def pytest_runtest_setup(item):
    if "slicer" in item.keywords and not HAVE_SLICER:
        pytest.skip("PrusaSlicer Flatpak not available")


@pytest.fixture
def jobs_dir(monkeypatch):
    """A throwaway jobs folder. Under $HOME because the Flatpak can't see /tmp."""
    d = config.JOBS_DIR / "_cache" / f"test-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True)
    import prusa_workbench.job as job_mod
    monkeypatch.setattr(job_mod, "JOBS_DIR", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


PRINTER = "Original Prusa MINI & MINI+ Input Shaper"
PRINT = "0.20mm SPEED @MINIIS 0.4"


@pytest.fixture
def filament():
    return config.selected_presets()["filament"]


def box(size, center) -> trimesh.Trimesh:
    b = trimesh.creation.box(size)
    b.apply_translation(center)
    return b


def tee() -> trimesh.Trimesh:
    """A T: stem plus two separate arms (each arm has its own underside triangles). Arms need supports."""
    return trimesh.util.concatenate([box((5, 5, 20), (0, 0, 10)), box((15, 5, 3), (-10, 0, 21.5)),
                                     box((15, 5, 3), (10, 0, 21.5))])


def support_xs(gcode: Path) -> list[float]:
    """X of every support extrusion move in a text G-code."""
    import re
    typ, xs = None, []
    for line in gcode.read_text().splitlines():
        if line.startswith(";TYPE:"):
            typ = line[6:]
        elif typ and typ.startswith("Support") and line.startswith(("G1", "G2", "G3")) and " E" in line:
            m = re.search(r"X([-\d.]+)", line)
            if m:
                xs.append(float(m.group(1)))
    return xs


__all__ = ["box", "tee", "support_xs", "PRINTER", "PRINT", "np"]
