"""PrusaSlicer command line: profiles, option names, resolved configs, slicing, format conversion.

Findings this is built on (PLAN.md, "CLI findings"):
- System presets load by name; `--load x.ini` after them overrides single settings.
- `--merge` (CLI auto-arrange) segfaults, so we always arrange ourselves and pass `--dont-arrange`.
- PrusaSlicer can exit 0 without writing G-code; success = the output file exists.
- The Flatpak only sees $HOME.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from .config import CACHE_DIR, PRUSASLICER
from .osutil import no_window

PROGRESS = re.compile(r"^\d+ => |^Slicing result exported|^Output for your request is written")


class SlicerError(RuntimeError):
    pass


def run(args: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
    return subprocess.run([*PRUSASLICER, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **no_window())


def messages(proc: subprocess.CompletedProcess) -> list[str]:
    """Everything PrusaSlicer printed except progress lines."""
    return [ln.strip() for ln in (proc.stdout + "\n" + proc.stderr).splitlines()
            if ln.strip() and not PROGRESS.match(ln.strip())]


# ---------- profiles ----------

@functools.lru_cache(maxsize=8)
def profiles(printer: str) -> dict:
    """Print profiles for a printer, each with its compatible filaments."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"profiles-{hashlib.sha1(printer.encode()).hexdigest()[:10]}.json"
    out.unlink(missing_ok=True)
    proc = run(["--query-print-filament-profiles", "--printer-profile", printer, "--output", str(out)], timeout=120)
    if not out.exists():
        raise SlicerError(f"could not list profiles for printer {printer!r}: {' '.join(messages(proc)) or proc.returncode}")
    data = json.loads(out.read_text(encoding="utf-8"))
    return {
        "printer": printer,
        "print_profiles": [
            {"name": p["name"],
             "filaments": [f["name"] if isinstance(f, dict) else f for f in p.get("filament_profiles", [])]}
            for p in data.get("print_profiles", [])
        ],
    }


@functools.lru_cache(maxsize=32)
def resolved_config(printer: str, print_profile: str, filament: str) -> dict[str, str]:
    """All settings of a printer + print + filament profile combination (via --save)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{printer}|{print_profile}|{filament}".encode()).hexdigest()[:12]
    out = CACHE_DIR / f"config-{key}.ini"
    out.unlink(missing_ok=True)
    proc = run(["--printer-profile", printer, "--print-profile", print_profile, "--material-profile", filament,
                "--save", str(out)], timeout=120)
    if not out.exists():
        raise SlicerError(f"unknown profile combination (printer {printer!r}, print {print_profile!r}, "
                          f"filament {filament!r}): {' '.join(messages(proc)) or proc.returncode}")
    return parse_ini(out.read_text(encoding="utf-8", errors="replace"))


def parse_ini(text: str) -> dict[str, str]:
    cfg = {}
    for line in text.splitlines():
        if " = " in line and not line.startswith("#"):
            k, v = line.split(" = ", 1)
            cfg[k.strip()] = v
    return cfg


def bed_geometry(cfg: dict[str, str]) -> dict:
    pts = [tuple(float(c) for c in p.split("x")) for p in cfg.get("bed_shape", "0x0,180x0,180x180,0x180").split(",")]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
            "height": float(cfg.get("max_print_height", "180") or 180)}


# ---------- option names ----------

@functools.lru_cache(maxsize=1)
def options() -> dict[str, dict]:
    """All FFF options from `--help-fff`: name (with underscores) -> {section, help, value}."""
    proc = run(["--help-fff"], timeout=60)
    opts: dict[str, dict] = {}
    section, cur = "", None
    for line in proc.stdout.splitlines():
        m = re.match(r"^ --([a-z0-9-]+)(?: ([A-Z,]+|N|X,Y))?(?:\s{2,}(.*))?$", line)
        if m:
            cur = m.group(1).replace("-", "_")
            opts[cur] = {"section": section, "help": (m.group(3) or "").strip(), "value": m.group(2) or "on/off"}
        elif line.startswith("                     ") and cur:
            opts[cur]["help"] = (opts[cur]["help"] + " " + line.strip()).strip()
        elif re.match(r"^[A-Z][^:]*:$", line):
            section, cur = line[:-1], None
    return opts


def check_settings(settings: dict) -> dict[str, str]:
    """Normalize setting names (dashes -> underscores) and values (bool -> 1/0); reject unknown names."""
    known = options()
    out, bad = {}, []
    for k, v in settings.items():
        key = k.strip().lstrip("-").replace("-", "_")
        if key not in known:
            bad.append(k)
            continue
        if isinstance(v, bool):
            v = "1" if v else "0"
        out[key] = str(v)
    if bad:
        hints = {b: [n for n in known if b.replace("-", "_").split("_")[0] in n][:6] for b in bad}
        raise ValueError(f"unknown PrusaSlicer setting(s): {bad}. Similar names: {hints}. "
                         "Use find_setting to search.")
    return out


# ---------- slicing ----------

def slice_3mf(model: Path, ini: Path, gcode: Path, printer: str, print_profile: str, filament: str,
              binary: bool = False) -> dict:
    """Slice one bed. Returns {ok, seconds, exit_code, messages} (+ error).
    Text G-code by default (the viewer reads it); binary=True for the smaller .bgcode that gets sent."""
    gcode.unlink(missing_ok=True)
    t0 = time.monotonic()
    proc = run(["--printer-profile", printer, "--print-profile", print_profile, "--material-profile", filament,
                "--load", str(ini), "--binary-gcode" if binary else "--no-binary-gcode", "--dont-arrange",
                "--export-gcode", "-o", str(gcode), str(model)])
    res = {"ok": gcode.exists(), "seconds": round(time.monotonic() - t0, 1), "exit_code": proc.returncode,
           "messages": messages(proc)}
    if not res["ok"]:
        res["error"] = " ".join(res["messages"])[-800:] or f"PrusaSlicer wrote no G-code (exit {proc.returncode})"
        if proc.returncode == 139:
            res["error"] += " (PrusaSlicer crashed: segfault)"
    return res


def convert_to_stl(src: Path, dst: Path) -> None:
    """Convert anything PrusaSlicer can open (STEP, OBJ, AMF…) to STL. Multiple objects are merged."""
    dst.unlink(missing_ok=True)
    proc = run(["--export-stl", "--dont-arrange", "-o", str(dst), str(src)], timeout=300)
    if not dst.exists():
        raise SlicerError(f"PrusaSlicer could not convert {src.name}: {' '.join(messages(proc)) or proc.returncode}")
