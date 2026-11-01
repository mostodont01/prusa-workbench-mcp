"""A print job: beds (each with its own profiles + settings) and objects placed on them.

Everything lives in the job folder so a later session (or a person) can pick it up:

    <jobs>/<job>/job.json          beds, objects, transforms, settings
    <jobs>/<job>/models/<id>.stl   each object's mesh as loaded (before rotate/scale/move)
    <jobs>/<job>/bed-N.3mf/.ini    what PrusaSlicer got (placed objects + per-object settings / bed settings)
    <jobs>/<job>/bed-N.gcode/.json/.png   results, read by the viewer
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from . import arrange as arr
from . import gcode as gc
from . import mesh as mz
from . import render, slicer, threemf
from .config import JOBS_DIR, SPACING

OUTPUT_SUFFIXES = (".gcode", ".bgcode", ".json", ".3mf", ".ini", ".png", ".log")


@dataclass
class Bed:
    name: str
    print_profile: str
    filament: str
    settings: dict[str, str] = field(default_factory=dict)   # overrides on top of the profiles


@dataclass
class Obj:
    id: str
    name: str
    source: str
    mesh_file: str                        # relative to the job folder
    bed: str
    linear: list[list[float]] = field(default_factory=lambda: np.eye(3).tolist())  # rotation/scale
    position: list[float] = field(default_factory=lambda: [90.0, 90.0])           # footprint center, mm
    settings: dict[str, str] = field(default_factory=dict)   # per-object overrides (e.g. supports)
    paint: dict[str, list[int]] = field(default_factory=dict)  # painted supports: "block"/"enforce" -> triangle ids
    paint_faces: int = 0                  # triangle count the paint was made for (detects a changed mesh)


def supports_settings(mode: str | None, angle: float | None = None) -> dict[str, str]:
    """off | auto | build_plate_only | painted (PrusaSlicer's default: only where painted = none from here)."""
    out: dict[str, str] = {}
    if mode is not None:
        m = mode.strip().lower().replace("-", "_").replace(" ", "_")
        if m in ("off", "none", "no"):
            out = {"support_material": "0"}
        elif m in ("auto", "everywhere", "on"):
            out = {"support_material": "1", "support_material_auto": "1", "support_material_buildplate_only": "0"}
        elif m in ("build_plate_only", "buildplate", "buildplate_only", "touching_build_plate"):
            out = {"support_material": "1", "support_material_auto": "1", "support_material_buildplate_only": "1"}
        elif m in ("painted", "enforcers", "enforcers_only"):
            out = {"support_material": "1", "support_material_auto": "0"}
        else:
            raise ValueError(f"supports must be off, auto, build_plate_only or painted, not {mode!r}")
    if angle is not None:
        out["support_material_threshold"] = str(int(angle))
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:60] or "job"


def _hull2d(p: np.ndarray) -> np.ndarray:
    pts = sorted(set(map(tuple, np.round(p, 4))))
    if len(pts) < 3:
        return np.array(pts)
    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for q in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0: lower.pop()
        lower.append(q)
    for q in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0: upper.pop()
        upper.append(q)
    return np.array(lower[:-1] + upper[:-1])


def _convex_overlap(a: np.ndarray, b: np.ndarray, tol: float = 0.05) -> bool:
    """Separating axis test for two convex polygons; True if they overlap by more than tol."""
    for poly in (a, b):
        for i in range(len(poly)):
            e = poly[(i + 1) % len(poly)] - poly[i]
            n = np.array([-e[1], e[0]])
            ln = np.linalg.norm(n)
            if ln == 0:
                continue
            n /= ln
            pa, pb = a @ n, b @ n
            if pa.max() <= pb.min() + tol or pb.max() <= pa.min() + tol:
                return False
    return True


class Job:
    def __init__(self, folder: Path, data: dict):
        self.dir = folder
        self.name = folder.name
        self.printer: str = data["printer"]
        self.beds = [Bed(**b) for b in data.get("beds", [])]
        self.objects = [Obj(**o) for o in data.get("objects", [])]
        self.next_id: int = data.get("next_id", len(self.objects) + 1)
        self._meshes: dict[str, trimesh.Trimesh] = {}
        self._lock = threading.RLock()

    # ---------- create / open / save ----------

    @classmethod
    def create(cls, name: str, printer: str, print_profile: str, filament: str) -> "Job":
        base = f"{dt.date.today()}-{_slug(name)}"
        folder, n = JOBS_DIR / base, 2
        while folder.exists():
            folder, n = JOBS_DIR / f"{base}-{n}", n + 1
        (folder / "models").mkdir(parents=True)
        job = cls(folder, {"printer": printer, "beds": [asdict(Bed("bed-1", print_profile, filament))]})
        job.save()
        return job

    @classmethod
    def open(cls, name: str) -> "Job":
        folder = JOBS_DIR / name
        f = folder / "job.json"
        if not f.exists():
            raise FileNotFoundError(f"no job {name!r} in {JOBS_DIR} (jobs made by hand have no job.json)")
        return cls(folder, json.loads(f.read_text(encoding="utf-8")))

    @staticmethod
    def list() -> list[dict]:
        out = []
        for f in JOBS_DIR.glob("*/job.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({"name": f.parent.name, "modified": dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes"),
                        "beds": len(d.get("beds", [])), "objects": len(d.get("objects", []))})
        return sorted(out, key=lambda j: j["modified"], reverse=True)

    def save(self) -> None:
        data = {"printer": self.printer, "next_id": self.next_id,
                "beds": [asdict(b) for b in self.beds], "objects": [asdict(o) for o in self.objects]}
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.dir / "job.json")

    # ---------- lookups ----------

    def bed(self, name: str) -> Bed:
        for b in self.beds:
            if b.name == name:
                return b
        raise KeyError(f"no bed {name!r} (beds: {[b.name for b in self.beds]})")

    def obj(self, oid: str) -> Obj:
        for o in self.objects:
            if o.id == oid or o.name == oid:
                return o
        raise KeyError(f"no object {oid!r} (objects: {[o.id + ' ' + o.name for o in self.objects]})")

    def on_bed(self, bed: str) -> list[Obj]:
        return [o for o in self.objects if o.bed == bed]

    def mesh(self, o: Obj) -> trimesh.Trimesh:
        m = self._meshes.get(o.mesh_file)
        if m is None:
            # STL stores every triangle with its own corners; merge them again or PrusaSlicer gets a triangle soup.
            m = trimesh.load_mesh(self.dir / o.mesh_file, file_type="stl", process=True)
            m.merge_vertices()
            self._meshes[o.mesh_file] = m
        return m

    def placed(self, o: Obj) -> trimesh.Trimesh:
        return mz.placed(self.mesh(o), np.array(o.linear), o.position)

    def geometry(self) -> dict:
        b = self.beds[0]
        return slicer.bed_geometry(slicer.resolved_config(self.printer, b.print_profile, b.filament))

    def new_bed(self, like: Bed | None = None) -> Bed:
        """A new bed: a copy of `like` (e.g. overflow from a full bed), or else the first bed's profiles
        without its overrides."""
        nums = [int(m.group(1)) for b in self.beds if (m := re.fullmatch(r"bed-(\d+)", b.name))]
        name = f"bed-{max(nums, default=0) + 1}"
        if like is not None:
            bed = Bed(name, like.print_profile, like.filament, dict(like.settings))
        else:
            bed = Bed(name, self.beds[0].print_profile, self.beds[0].filament, {})
        self.beds.append(bed)
        return bed

    # ---------- objects ----------

    def add_mesh(self, name: str, m: trimesh.Trimesh, source: str, bed: str) -> Obj:
        oid = f"o{self.next_id}"
        self.next_id += 1
        rel = f"models/{oid}.stl"
        (self.dir / "models").mkdir(exist_ok=True)
        m.export(self.dir / rel, file_type="stl")
        g = self.geometry()
        o = Obj(oid, name, source, rel, bed, position=[(g["x0"] + g["x1"]) / 2, (g["y0"] + g["y1"]) / 2])
        self.objects.append(o)
        return o

    def duplicate(self, o: Obj, count: int) -> list[Obj]:
        out = []
        for _ in range(count):
            c = Obj(f"o{self.next_id}", o.name, o.source, o.mesh_file, o.bed,
                    [row[:] for row in o.linear], list(o.position), dict(o.settings),
                    {k: list(v) for k, v in o.paint.items()}, o.paint_faces)
            self.next_id += 1
            self.objects.append(c)
            out.append(c)
        return out

    def transform(self, o: Obj, *, reset=False, scale=None, rotate=None, lay_flat=False,
                  move_to=None, move_by=None) -> None:
        lin = np.eye(3) if reset else np.array(o.linear, dtype=float)
        if scale is not None:
            s = np.array(scale, dtype=float).reshape(-1)
            if s.size == 1:
                s = np.repeat(s, 3)
            if s.size != 3 or (s <= 0).any():
                raise ValueError("scale must be a positive factor or [x, y, z] factors")
            lin = np.diag(s) @ lin
        if rotate is not None:
            r = list(rotate) + [0] * (3 - len(rotate))
            lin = mz.rotation(*r[:3]) @ lin
        if lay_flat:
            best = mz.flat_faces(self.mesh(o), lin, count=1)[0]
            lin = mz.lay_flat_rotation(best["normal"]) @ lin
        o.linear = lin.tolist()
        if move_to is not None:
            o.position = [float(move_to[0]), float(move_to[1])]
        if move_by is not None:
            o.position = [o.position[0] + float(move_by[0]), o.position[1] + float(move_by[1])]

    def paint_supports(self, o: Obj, mode: str, box: list[float] | None = None,
                       small_gaps_mm: float | None = None) -> dict:
        """Paint supports on triangles of an object (stored by triangle index, so it survives moving/rotating).
        mode: block | enforce | clear. Selection: triangles whose center is inside `box` (bed coordinates,
        [x0, y0, z0, x1, y1, z1]) and/or, with small_gaps_mm, overhangs whose support would stand on the object
        itself at most that far below (screw holes, nut traps, slots). clear without a selection clears all."""
        if mode not in ("block", "enforce", "clear"):
            raise ValueError("mode must be block, enforce or clear")
        m = self.placed(o)
        n = len(m.faces)
        sel = np.zeros(n, dtype=bool)
        chosen = False
        if box is not None:
            if len(box) != 6:
                raise ValueError("box is [x0, y0, z0, x1, y1, z1] in bed coordinates (mm)")
            lo, hi = np.minimum(box[:3], box[3:]), np.maximum(box[:3], box[3:])
            c = m.triangles_center
            sel |= ((c >= lo) & (c <= hi)).all(axis=1)
            chosen = True
        if small_gaps_mm is not None:
            c, nz = m.triangles_center, m.face_normals[:, 2]
            cand = np.where((nz < -0.5) & (c[:, 2] > 0.05))[0]      # facing down, not on the bed
            if len(cand):
                origins = c[cand] - np.array([0, 0, 1e-3])
                dirs = np.tile([0.0, 0.0, -1.0], (len(cand), 1))
                locs, ray_idx, _ = m.ray.intersects_location(origins, dirs, multiple_hits=False)
                gap = np.full(len(cand), np.inf)
                gap[ray_idx] = origins[ray_idx, 2] - locs[:, 2]
                sel[cand[gap <= small_gaps_mm]] = True
            chosen = True
        idx = set(np.where(sel)[0].tolist())
        paint = {k: set(v) for k, v in o.paint.items()}
        if mode == "clear":
            for k in paint:
                paint[k] = paint[k] - idx if chosen else set()
        else:
            other = "enforce" if mode == "block" else "block"
            paint.setdefault(mode, set()).update(idx)
            paint[other] = paint.get(other, set()) - idx
        o.paint = {k: sorted(v) for k, v in paint.items() if v}
        o.paint_faces = n
        return {"selected": len(idx), "painted": {k: len(v) for k, v in o.paint.items()}}

    def paint_for(self, o: Obj) -> dict:
        if o.paint and o.paint_faces != len(self.mesh(o).faces):
            return {}   # mesh changed: paint no longer matches its triangles
        return o.paint

    def arrange(self, bed_name: str, gap: float = SPACING, allow_rotate: bool = True, spread: bool = True) -> dict:
        """Arrange one bed. Objects that don't fit go to new beds (spread) or are reported."""
        g = self.geometry()
        report = {"arranged": {}, "did_not_fit": []}
        todo = self.on_bed(bed_name)
        bed = self.bed(bed_name)
        while todo:
            items = []
            for o in todo:
                sz = mz.size(self.mesh(o), np.array(o.linear))
                items.append(arr.Item(o.id, float(sz[0]), float(sz[1])))
            placements, left = arr.arrange(items, g, gap, allow_rotate)
            if not placements:
                report["did_not_fit"] += [i.key for i in left]
                break
            for p in placements:
                o = self.obj(p.key)
                if p.rotated:
                    o.linear = (mz.rotation(z=90) @ np.array(o.linear)).tolist()
                o.position, o.bed = [round(p.cx, 3), round(p.cy, 3)], bed.name
            report["arranged"][bed.name] = [p.key for p in placements]
            todo = [self.obj(i.key) for i in left]
            if todo and spread:
                bed = self.new_bed(like=bed)
            elif todo:
                report["did_not_fit"] += [o.id for o in todo]
                break
        return report

    # ---------- checks ----------

    def issues(self, bed_name: str) -> list[str]:
        g = self.geometry()
        objs = self.on_bed(bed_name)
        out, hulls = [], []
        for o in objs:
            m = self.placed(o)
            lo, hi = m.bounds
            label = f"{o.id} ({o.name})"
            if lo[0] < g["x0"] - 0.01 or lo[1] < g["y0"] - 0.01 or hi[0] > g["x1"] + 0.01 or hi[1] > g["y1"] + 0.01:
                out.append(f"{label} is partly off the bed (x {lo[0]:.1f}..{hi[0]:.1f}, y {lo[1]:.1f}..{hi[1]:.1f}; "
                           f"bed {g['x0']:.0f}..{g['x1']:.0f} x {g['y0']:.0f}..{g['y1']:.0f})")
            if hi[2] > g["height"] + 0.01:
                out.append(f"{label} is {hi[2]:.1f} mm tall, the printer only fits {g['height']:.0f} mm")
            hulls.append((label, _hull2d(m.vertices[:, :2])))
        for i in range(len(hulls)):
            for j in range(i + 1, len(hulls)):
                if len(hulls[i][1]) >= 3 and len(hulls[j][1]) >= 3 and _convex_overlap(hulls[i][1], hulls[j][1]):
                    out.append(f"{hulls[i][0]} and {hulls[j][0]} overlap (footprints)")
        return out

    def input_hash(self, bed: Bed) -> str:
        objs = [(o.id, o.name, o.mesh_file, o.linear, o.position, sorted(o.settings.items()),
                 sorted((k, len(v), sum(v)) for k, v in o.paint.items())) for o in self.on_bed(bed.name)]
        blob = json.dumps([self.printer, asdict(bed), objs], sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]

    def bed_status(self, bed: Bed) -> dict:
        f = self.dir / f"{bed.name}.json"
        if not f.exists():
            return {"status": "not sliced"}
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            return {"status": "not sliced"}
        st = {"status": "failed" if info.get("error") else "sliced"}
        if info.get("input_hash") != self.input_hash(bed):
            st["status"] += ", outdated (changed since)"
        for k in ("print_time", "filament_g", "error"):
            if info.get(k) is not None:
                st[k] = info[k]
        return st

    def summary(self) -> dict:
        g = self.geometry()
        beds = []
        for b in self.beds:
            beds.append({"name": b.name, "print_profile": b.print_profile, "filament": b.filament,
                         "settings": b.settings, "objects": [o.id for o in self.on_bed(b.name)],
                         **self.bed_status(b), "issues": self.issues(b.name)})
        objs = []
        for o in self.objects:
            m = self.placed(o)
            lo, hi = m.bounds
            lin = np.array(o.linear)
            objs.append({"id": o.id, "name": o.name, "bed": o.bed,
                         "size_mm": [round(float(v), 2) for v in hi - lo],
                         "position": [round(v, 2) for v in o.position],
                         "transformed": not np.allclose(lin, np.eye(3)),
                         "settings": o.settings, "source": o.source,
                         **({"painted_supports": {k: len(v) for k, v in o.paint.items()}} if o.paint else {})})
        return {"job": self.name, "folder": str(self.dir), "printer": self.printer,
                "bed_size_mm": [g["x1"] - g["x0"], g["y1"] - g["y0"], g["height"]], "beds": beds, "objects": objs}

    # ---------- slicing ----------

    def _object_names(self, objs: list[Obj]) -> dict[str, str]:
        counts: dict[str, int] = {}
        for o in objs:
            counts[o.name] = counts.get(o.name, 0) + 1
        return {o.id: o.name if counts[o.name] == 1 else f"{o.name} {o.id}" for o in objs}

    def slice(self, bed_names: list[str] | None = None) -> list[dict]:
        targets = [self.bed(n) for n in bed_names] if bed_names else list(self.beds)
        # remove outputs of beds that no longer exist
        names = {b.name for b in self.beds}
        for f in self.dir.glob("bed-*"):
            if f.suffix in OUTPUT_SUFFIXES and f.stem not in names:
                f.unlink()
        work = [b for b in targets if self.on_bed(b.name)]
        results = [{"bed": b.name, "skipped": "no objects on this bed"} for b in targets if not self.on_bed(b.name)]
        with cf.ThreadPoolExecutor(max_workers=max(1, len(work))) as ex:
            results += list(ex.map(self._slice_bed, work))
        order = {b.name: i for i, b in enumerate(self.beds)}
        return sorted(results, key=lambda r: order.get(r["bed"], 99))

    def _slice_bed(self, bed: Bed) -> dict:
        objs = self.on_bed(bed.name)
        names = self._object_names(objs)
        with self._lock:
            placed = [(names[o.id], self.placed(o), o.settings, self.paint_for(o)) for o in objs]
        model, ini, out = (self.dir / f"{bed.name}{s}" for s in (".3mf", ".ini", ".gcode"))
        threemf.write(model, placed)
        ini.write_text("".join(f"{k} = {v}\n" for k, v in bed.settings.items()) or "# no overrides\n", encoding="utf-8")
        for stale in (".png", ".json", ".bgcode"):
            (self.dir / f"{bed.name}{stale}").unlink(missing_ok=True)

        res = slicer.slice_3mf(model, ini, out, self.printer, bed.print_profile, bed.filament)
        info = {"bed": bed.name, "printer": self.printer, "print_profile": bed.print_profile,
                "filament": bed.filament, "overrides": bed.settings,
                "object_settings": {names[o.id]: {**o.settings,
                                                  **({"painted_supports": {k: len(v) for k, v in o.paint.items()}} if o.paint else {})}
                                    for o in objs if o.settings or o.paint},
                "input_hash": self.input_hash(bed), "slice_seconds": res["seconds"], "exit_code": res["exit_code"]}
        issues = self.issues(bed.name)
        if not res["ok"]:
            info["error"] = res["error"]
            info["warnings"] = issues
        else:
            info.update(gc.parse_gcode(out))
            info["warnings"] = issues + res["messages"]
            try:
                self._thumbnails(out, [p[1] for p in placed])
                info["thumbnails"] = True
            except Exception as e:  # thumbnails are nice to have, never fail the slice for them
                info["warnings"].append(f"could not add thumbnails: {e}")
        (self.dir / f"{bed.name}.log").write_text("\n".join(res["messages"]) + "\n", encoding="utf-8")
        tmp = self.dir / f"{bed.name}.json.tmp"
        tmp.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.dir / f"{bed.name}.json")
        return info

    def _thumbnails(self, gcode_path: Path, meshes: list[trimesh.Trimesh]) -> None:
        specs = gc.thumbnail_specs(gcode_path) or [(220, 124, "PNG")]
        colored = [("", m, render.ORANGE) for m in meshes]
        bed = self.geometry()
        thumbs, biggest = [], None
        for w, h, fmt in specs:
            # Like PrusaSlicer: the objects standing on (a cropped piece of) the bed.
            img = render.render(colored, (w, h), "iso", bed=bed, fit_bed=False, margin=0.1,
                                ss=4 if max(w, h) < 64 else 3)
            thumbs.append((w, h, fmt, render.encode(img, fmt)))
            if biggest is None or w * h > biggest.size[0] * biggest.size[1]:
                biggest = img
        gc.insert_thumbnails(gcode_path, thumbs)
        if biggest is not None:
            biggest.save(gcode_path.with_suffix(".png"))

    def preview(self, bed_name: str) -> bytes:
        objs = self.on_bed(bed_name)
        return render.bed_preview([(f"{o.id} {o.name}", self.placed(o), self.paint_for(o)) for o in objs],
                                  self.geometry(), title=f"{self.name} / {bed_name}")
