#!/usr/bin/env python3
"""Step 1 of PLAN.md: slice several beds with the PrusaSlicer CLI, each with its own settings, in parallel.

Stdlib only. We arrange the models ourselves and hand PrusaSlicer a 3MF with the positions baked in,
sliced with --dont-arrange, because PrusaSlicer 2.9.6's own CLI arrange (--merge) segfaults.

Usage: scripts/cli_test.py            (slices the robocars fit test into ~/print-jobs/<date>-fit-test)
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import re
import struct
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PRUSASLICER = ["flatpak", "run", "--command=prusa-slicer", "com.prusa3d.PrusaSlicer"]
PRINTER = "Original Prusa MINI & MINI+ Input Shaper"
FILAMENT = "Alzament Basic PLA"
BED = (180.0, 180.0)  # MINI bed, mm
SPACING = 6.0  # gap between objects, mm

# The Flatpak sandbox only sees $HOME (not /tmp), so everything it reads or writes must live under it.
JOBS = Path.home() / "print-jobs"
MODELS = Path.home() / "Programming/robots/robocars/modelling/fit_test"


# ---------- meshes ----------

@dataclass
class Mesh:
    name: str
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]

    def bbox(self):
        xs, ys, zs = zip(*self.vertices)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def read_stl(path: Path) -> Mesh:
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + 50 * n:
        raise ValueError(f"{path}: not a binary STL (ASCII STL not supported yet)")
    index: dict[tuple[float, float, float], int] = {}
    vertices, triangles = [], []
    for i in range(n):
        vals = struct.unpack_from("<12f", data, 84 + 50 * i)
        tri = []
        for k in range(3):
            v = vals[3 + 3 * k: 6 + 3 * k]
            if v not in index:
                index[v] = len(vertices)
                vertices.append(v)
            tri.append(index[v])
        triangles.append(tuple(tri))
    return Mesh(path.stem, vertices, triangles)


# ---------- arrange ----------

def arrange(meshes: list[Mesh]) -> list[tuple[float, float, float]]:
    """Simple shelf packing by bounding box, centered on the bed.
    Returns, per mesh, the translation that moves it into place (and its bottom to z=0)."""
    boxes = [m.bbox() for m in meshes]
    order = sorted(range(len(meshes)), key=lambda i: -(boxes[i][1][1] - boxes[i][0][1]))
    placed: dict[int, tuple[float, float]] = {}
    x = y = shelf_h = 0.0
    for i in order:
        (x0, y0, _), (x1, y1, _) = boxes[i]
        w, h = x1 - x0, y1 - y0
        if x > 0 and x + w > BED[0]:
            x, y, shelf_h = 0.0, y + shelf_h + SPACING, 0.0
        placed[i] = (x, y)
        x += w + SPACING
        shelf_h = max(shelf_h, h)
    used_w = max(placed[i][0] + boxes[i][1][0] - boxes[i][0][0] for i in placed)
    used_h = max(placed[i][1] + boxes[i][1][1] - boxes[i][0][1] for i in placed)
    if used_w > BED[0] or used_h > BED[1]:
        raise ValueError(f"objects don't fit on one bed ({used_w:.0f} x {used_h:.0f} mm)")
    ox, oy = (BED[0] - used_w) / 2, (BED[1] - used_h) / 2
    return [(ox + placed[i][0] - boxes[i][0][0], oy + placed[i][1] - boxes[i][0][1], -boxes[i][0][2])
            for i in range(len(meshes))]


# ---------- 3MF ----------

def write_3mf(path: Path, meshes: list[Mesh], offsets: list[tuple[float, float, float]]) -> None:
    objs, items = [], []
    for oid, (m, (tx, ty, tz)) in enumerate(zip(meshes, offsets), start=1):
        vs = "".join(f'<vertex x="{x:.6g}" y="{y:.6g}" z="{z:.6g}"/>' for x, y, z in m.vertices)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.triangles)
        objs.append(f'<object id="{oid}" name="{m.name}" type="model"><mesh><vertices>{vs}</vertices>'
                    f'<triangles>{ts}</triangles></mesh></object>')
        items.append(f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {tx:.6g} {ty:.6g} {tz:.6g}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(objs)}</resources><build>{"".join(items)}</build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)
        # PrusaSlicer's own metadata; without it a single-object 3MF gets named after the file.
        cfg = "".join(f'<object id="{oid}" instances_count="1">'
                      f'<metadata type="object" key="name" value="{m.name}"/>'
                      f'<volume firstid="0" lastid="{len(m.triangles) - 1}">'
                      f'<metadata type="volume" key="name" value="{m.name}"/></volume></object>'
                      for oid, m in enumerate(meshes, start=1))
        z.writestr("Metadata/Slic3r_PE_model.config",
                   f'<?xml version="1.0" encoding="UTF-8"?><config>{cfg}</config>')


# ---------- slicing ----------

@dataclass
class Bed:
    name: str
    models: list[Path]
    print_profile: str
    overrides: dict[str, str] = field(default_factory=dict)  # PrusaSlicer option -> value


def slice_bed(job: Path, bed: Bed) -> dict:
    meshes = [read_stl(p) for p in bed.models]
    model_3mf = job / f"{bed.name}.3mf"
    write_3mf(model_3mf, meshes, arrange(meshes))

    gcode, log = job / f"{bed.name}.gcode", job / f"{bed.name}.log"
    gcode.unlink(missing_ok=True)
    cmd = [*PRUSASLICER, "--printer-profile", PRINTER, "--material-profile", FILAMENT,
           "--print-profile", bed.print_profile, "--no-binary-gcode", "--dont-arrange", "--export-gcode"]
    for k, v in bed.overrides.items():
        cmd += [f"--{k}", v]
    cmd += ["-o", str(gcode), str(model_3mf)]

    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log.write_text(proc.stdout + proc.stderr)
    info = {"bed": bed.name, "models": [p.name for p in bed.models], "print_profile": bed.print_profile,
            "overrides": bed.overrides, "slice_seconds": round(time.monotonic() - t0, 1),
            "exit_code": proc.returncode}
    # PrusaSlicer can exit 0 without writing anything (e.g. "All objects are outside of the print volume"),
    # so success = the G-code file exists.
    if not gcode.exists():
        info["error"] = (proc.stdout + proc.stderr).strip()[-500:] or f"no G-code (exit {proc.returncode})"
    else:
        info.update(parse_gcode(gcode))
    (job / f"{bed.name}.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def parse_gcode(path: Path) -> dict:
    """Pull summary + the settings used out of PrusaSlicer's trailing comments."""
    meta: dict[str, str] = {}
    types: dict[str, int] = {}
    layers = 0
    with path.open() as f:
        for line in f:
            if line.startswith(";TYPE:"):
                t = line[6:].strip()
                types[t] = types.get(t, 0) + 1
            elif line.startswith(";LAYER_CHANGE"):
                layers += 1
            elif line.startswith("; ") and " = " in line:
                k, v = line[2:].rstrip("\n").split(" = ", 1)
                meta[k] = v
    keys = ["printer_settings_id", "print_settings_id", "filament_settings_id", "layer_height",
            "first_layer_height", "perimeters", "fill_density", "fill_pattern", "top_solid_layers",
            "bottom_solid_layers", "support_material", "brim_width", "temperature", "bed_temperature"]
    return {
        "print_time": meta.get("estimated printing time (normal mode)"),
        "filament_g": float(meta.get("total filament used [g]", "nan")),
        "filament_m": round(float(meta.get("filament used [mm]", "nan")) / 1000, 2),
        "layers": layers,
        "feature_types": types,
        "objects": [o["name"] for o in json.loads(meta.get("objects_info", '{"objects":[]}'))["objects"]],
        "settings": {k: meta[k].strip('"') for k in keys if k in meta},
    }


def main() -> None:
    job = JOBS / f"{dt.date.today()}-fit-test"
    job.mkdir(parents=True, exist_ok=True)
    beds = [
        Bed("bed-1", [MODELS / "fit_plate.stl"], "0.20mm SPEED @MINIIS 0.4"),
        Bed("bed-2", [MODELS / f"pin_{d}_mm.stl" for d in (3, 5, 8)], "0.15mm STRUCTURAL @MINIIS 0.4",
            {"perimeters": "4", "fill-density": "40%"}),
    ]
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor() as ex:
        results = list(ex.map(lambda b: slice_bed(job, b), beds))
    print(f"job: {job}  (wall {time.monotonic() - t0:.1f}s)")
    for r in results:
        if "error" in r:
            print(f"  {r['bed']}: FAILED  {r['error']}")
        else:
            s = r["settings"]
            print(f"  {r['bed']}: {r['print_time']}, {r['filament_g']} g, {r['layers']} layers, "
                  f"{s.get('print_settings_id')} | layer {s.get('layer_height')} perim {s.get('perimeters')} "
                  f"infill {s.get('fill_density')} | objects {r['objects']}  ({r['slice_seconds']}s)")


if __name__ == "__main__":
    main()
