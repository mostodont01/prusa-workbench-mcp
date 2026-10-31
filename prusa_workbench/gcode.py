"""Read the summary PrusaSlicer writes into its G-code (time, filament, objects, settings used)."""
from __future__ import annotations

import json
import math
from pathlib import Path

# Settings shown in the viewer's info panel (and stored in bed-N.json), in display order.
SETTINGS_KEYS = [
    "printer_settings_id", "print_settings_id", "filament_settings_id",
    "layer_height", "first_layer_height", "perimeters", "top_solid_layers", "bottom_solid_layers",
    "fill_density", "fill_pattern", "support_material", "support_material_auto",
    "support_material_buildplate_only", "support_material_threshold", "brim_width", "skirts",
    "temperature", "first_layer_temperature", "bed_temperature", "filament_type", "nozzle_diameter",
    "bed_shape", "max_print_height",
]


def _float(v: str | None) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def parse_gcode(path: Path) -> dict:
    """Pull the summary and the settings used out of PrusaSlicer's comments."""
    meta: dict[str, str] = {}
    types: dict[str, int] = {}
    layers = 0
    with path.open(errors="replace") as f:
        for line in f:
            if line.startswith(";TYPE:"):
                t = line[6:].strip()
                types[t] = types.get(t, 0) + 1
            elif line.startswith(";LAYER_CHANGE"):
                layers += 1
            elif line.startswith("; ") and " = " in line:
                k, v = line[2:].rstrip("\n").split(" = ", 1)
                meta[k] = v
    filament_mm = _float(meta.get("filament used [mm]"))
    try:
        objects = [o["name"] for o in json.loads(meta.get("objects_info", "{}")).get("objects", [])]
    except (ValueError, AttributeError):
        objects = []
    return {
        "print_time": meta.get("estimated printing time (normal mode)"),
        "filament_g": _float(meta.get("total filament used [g]")),
        "filament_m": round(filament_mm / 1000, 2) if filament_mm is not None else None,
        "layers": layers,
        "feature_types": types,
        "objects": objects,
        "settings": {k: meta[k].strip('"') for k in SETTINGS_KEYS if k in meta},
    }
