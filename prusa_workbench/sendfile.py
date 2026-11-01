"""Pick the file to send for a bed: a binary .bgcode when possible (about a third of the size, which matters
for the MINI's slow Wi-Fi), else the text G-code. Stdlib only (runs in the viewer).

The .bgcode is made by slicing the bed's own bed-N.3mf + bed-N.ini again with binary output (same inputs, same
print), then our thumbnails are copied into it. It is cached next to the G-code until the G-code changes.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import bgcode, gcode, slicer


def prepare(job_dir: Path, bed: str, printer_extensions: list[str] | None = None) -> tuple[Path, str, str | None]:
    """Returns (file, 'binary'|'text', note). Never raises: falls back to the text G-code."""
    text = job_dir / f"{bed}.gcode"
    if printer_extensions is not None and "bgcode" not in [e.lower() for e in printer_extensions]:
        return text, "text", "the printer does not take binary G-code"
    model, ini, info_f = (job_dir / f"{bed}{s}" for s in (".3mf", ".ini", ".json"))
    if not (model.exists() and ini.exists() and info_f.exists()):
        return text, "text", "no slicing inputs for this bed (G-code added by hand?), sending text G-code"
    binf = job_dir / f"{bed}.bgcode"
    if binf.exists() and binf.stat().st_mtime >= text.stat().st_mtime:
        try:
            bgcode.blocks(binf.read_bytes())
            return binf, "binary", None
        except bgcode.BgcodeError:
            pass
    try:
        info = json.loads(info_f.read_text(encoding="utf-8"))
        res = slicer.slice_3mf(model, ini, binf, info["printer"], info["print_profile"], info["filament"], binary=True)
        if not res["ok"]:
            return text, "text", f"could not make binary G-code ({res.get('error')}), sending text G-code"
        thumbs = gcode.read_thumbnails(text)
        if thumbs:
            bgcode.add_thumbnails(binf, thumbs)
        bgcode.blocks(binf.read_bytes())
        return binf, "binary", None
    except Exception as e:
        binf.unlink(missing_ok=True)
        return text, "text", f"could not make binary G-code ({e}), sending text G-code"
