"""Deleting jobs: to the desktop trash (recoverable), permanently only if there is no trash. Stdlib only."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def job_folder(jobs_root: Path, name: str) -> Path:
    """The folder of a job, refusing anything that isn't a plain job folder directly in the jobs root."""
    if not name or "/" in name or "\\" in name or name.startswith((".", "_")) or name in ("..",):
        raise ValueError(f"not a job name: {name!r}")
    d = jobs_root / name
    if not d.is_dir() or d.resolve().parent != jobs_root.resolve():
        raise FileNotFoundError(f"no job {name!r}")
    return d


def delete_job(jobs_root: Path, name: str) -> str:
    """Returns 'trash' (recoverable from the desktop trash) or 'deleted' (no trash available)."""
    d = job_folder(jobs_root, name)
    try:
        subprocess.run(["gio", "trash", str(d)], check=True, capture_output=True, timeout=60)
        return "trash"
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        if not d.exists():
            return "trash"
        shutil.rmtree(d)
        return "deleted"
