"""MCP server: Claude prepares beds and slices them with the PrusaSlicer CLI. It never starts a print.

Run: python -m prusa_workbench.mcp_server   (stdio)
"""
from __future__ import annotations

import functools
import io
import json
from pathlib import Path
from typing import Any

import trimesh
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import config, onshape, slicer, trash
from . import mesh as mz
from .job import Job, supports_settings
from .viewer import launch

INSTRUCTIONS = """\
Prepare 3D prints and slice them with PrusaSlicer's command line. Each bed is sliced separately with its own
print profile, filament and settings; objects can also have their own settings (e.g. supports).

Workflow: new_job (or open_job) -> add_model / add_from_onshape -> transform / arrange / move_to_bed ->
set_bed / set_object_settings -> preview (look at it!) -> slice. Slicing writes the job folder and the viewer
shows it; the user checks it there and sends it to the printer themselves.

Rules:
- Never claim something was printed or sent. This server cannot start a print; sending is the user's call in the viewer.
- Units are mm. Bed coordinates: origin at the front-left corner, X to the right, Y to the back, Z up.
  Object positions are the center of the object's footprint. Objects always sit on the bed (z = 0).
- Models keep the orientation they were exported in. Look at preview and use transform(lay_flat=True) or
  rotate if a part is standing on a bad face. Prefer orientations that avoid supports.
- Settings use PrusaSlicer option names (find_setting searches them), e.g. layer_height, fill_density ("20%"),
  perimeters, brim_width, top_solid_layers. Supports: off | auto | build_plate_only | painted.
- After slicing, report print time, filament and any warnings per bed.
"""

server = MCPServer("prusa-workbench", instructions=INSTRUCTIONS)
_job: Job | None = None
_viewer_opened = False


def tool(name: str | None = None):
    """Register a tool; any error reaches Claude with its message (the SDK hides messages of other exceptions)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ToolError:
                raise
            except KeyError as e:
                raise ToolError(str(e.args[0]) if e.args else repr(e)) from e
            except Exception as e:
                raise ToolError(f"{type(e).__name__}: {e}") from e
        server.tool(name=name)(wrapper)
        return wrapper
    return deco


def _out(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, default=str)


def job() -> Job:
    if _job is None:
        recent = [j["name"] for j in Job.list()[:5]]
        raise RuntimeError(f"No job is open. Use new_job, or open_job with one of: {recent}")
    return _job


def _ids(objects: list[str] | str) -> list[str]:
    j = job()
    if isinstance(objects, str):
        objects = [objects]
    if objects == ["all"]:
        return [o.id for o in j.objects]
    return [j.obj(o).id for o in objects]


def _target_bed(bed: str | None) -> str:
    j = job()
    if bed in (None, ""):
        return j.beds[0].name
    if bed == "new":
        return j.new_bed().name
    return j.bed(bed).name


def _after_add(added, bed: str, copies: int, arrange: bool) -> str:
    j = job()
    if copies > 1:
        for o in list(added):
            added += j.duplicate(o, copies - 1)
    report = j.arrange(bed) if arrange else None
    j.save()
    out = {"added": [{"id": o.id, "name": o.name, "bed": o.bed,
                      "size_mm": [round(float(v), 2) for v in j.placed(o).extents]} for o in added]}
    if report:
        out["arranged"] = report["arranged"]
        if report["did_not_fit"]:
            out["did_not_fit"] = report["did_not_fit"]
    issues = {b: j.issues(b) for b in {o.bed for o in added}}
    out["issues"] = {b: i for b, i in issues.items() if i}
    return _out(out)


# ---------------------------------------------------------------- jobs

@tool()
def list_jobs() -> str:
    """List print jobs (newest first)."""
    return _out({"jobs_folder": str(config.JOBS_DIR), "jobs": Job.list(),
                 "open": _job.name if _job else None})


@tool()
def new_job(name: str, print_profile: str | None = None, filament: str | None = None) -> str:
    """Start a new print job with one empty bed and make it the open job.

    Args:
        name: short name, e.g. "robocar wheels". The folder gets today's date in front.
        print_profile: PrusaSlicer print profile for bed-1 (default: the one selected in PrusaSlicer).
        filament: filament profile for bed-1 (default: the one selected in PrusaSlicer).
    """
    global _job
    sel = config.selected_presets()
    pp, fil = print_profile or sel["print"], filament or sel["filament"]
    slicer.resolved_config(sel["printer"], pp, fil)  # validates the names
    _job = Job.create(name, sel["printer"], pp, fil)
    return _out(_job.summary())


@tool()
def open_job(name: str) -> str:
    """Open an existing job (see list_jobs) to keep working on it."""
    global _job
    _job = Job.open(name)
    return _out(_job.summary())


@tool()
def delete_jobs(names: list[str]) -> str:
    """Delete whole jobs (folder with models, beds and slices), e.g. to clean up bad or old slices. They go to the
    desktop trash, so the user can get them back. Only do this when the user asked for it.

    Args:
        names: job names exactly as list_jobs shows them.
    """
    global _job
    out = {}
    for n in names:
        out[n] = trash.delete_job(config.JOBS_DIR, n)
        if _job and _job.name == n:
            _job = None
    return _out({"deleted": out, "note": "'trash' = in the desktop trash (recoverable)"})


@tool()
def get_job() -> str:
    """The open job: beds (profiles, settings, objects, slice status, issues like overlaps or off-bed) and
    objects (id, name, bed, size, position, settings)."""
    return _out(job().summary())


# ---------------------------------------------------------------- profiles / settings

@tool()
def list_profiles() -> str:
    """Print profiles for the printer, and the filaments that go with them."""
    printer = _job.printer if _job else config.selected_presets()["printer"]
    p = slicer.profiles(printer)
    filaments = sorted({f for pp in p["print_profiles"] for f in pp["filaments"]})
    return _out({"printer": printer, "print_profiles": [pp["name"] for pp in p["print_profiles"]],
                 "filaments": filaments, "selected_in_prusaslicer": config.selected_presets()})


@tool()
def find_setting(query: str, bed: str | None = None) -> str:
    """Search PrusaSlicer setting names and descriptions (e.g. "brim", "infill", "seam", "ironing").
    Shows each match's current value on a bed of the open job (default: the first bed).

    Args:
        query: words to look for.
        bed: which bed's current values to show.
    """
    words = query.lower().split()
    opts = slicer.options()
    current: dict[str, str] = {}
    if _job:
        b = _job.bed(bed) if bed else _job.beds[0]
        current = {**slicer.resolved_config(_job.printer, b.print_profile, b.filament), **b.settings}
    hits = []
    for name, o in opts.items():
        text = f"{name} {o['section']} {o['help']}".lower()
        if all(w in text for w in words):
            score = sum(w in name for w in words)
            hits.append((-score, name, o))
    hits.sort()
    return _out([{"name": n, "section": o["section"], "help": o["help"][:300],
                  **({"current": current[n]} if n in current else {})} for _, n, o in hits[:15]])


# ---------------------------------------------------------------- models

@tool()
def add_model(path: str, name: str | None = None, bed: str | None = None, copies: int = 1,
              arrange: bool = True) -> str:
    """Load a model file (STL, 3MF, OBJ, STEP) onto a bed. A 3MF with several objects adds each one.

    Args:
        path: file path (~ allowed).
        name: object name (default: from the file).
        bed: bed name, or "new" for a new bed with bed-1's profiles and no overrides (default: the first bed).
        copies: how many of it.
        arrange: re-arrange the bed afterwards (objects that don't fit go to new beds).
    """
    j = job()
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist")
    bed = _target_bed(bed)
    loaded = mz.load(p, j.dir / "models" / "src")
    added = [j.add_mesh(name if (name and len(loaded) == 1) else (f"{name} {n}" if name else n), m, str(p), bed)
             for n, m in loaded]
    return _after_add(added, bed, copies, arrange)


@tool()
def add_from_onshape(url: str, parts: list[str] | None = None, bed: str | None = None, copies: int = 1,
                     arrange: bool = True) -> str:
    """Download parts from an Onshape part studio (as millimeter STL) onto a bed. Uses the Onshape API keys from
    ~/onshape-mcp/.env (each download counts against the user's Onshape API allocation).

    Args:
        url: Onshape URL (document, or document/w/<id>/e/<part studio id>).
        parts: part names to take (default: all solid parts). Case-insensitive.
        bed: bed name or "new".
        copies: copies of each part.
        arrange: re-arrange the bed afterwards.
    """
    j = job()
    ref = onshape.list_parts(url)
    available = ref["parts"]
    if parts:
        want = {p.lower() for p in parts}
        chosen = [p for p in available if p["name"].lower() in want]
        missing = want - {p["name"].lower() for p in chosen}
        if missing:
            raise ValueError(f"parts not found: {sorted(missing)}; available: {[p['name'] for p in available]}")
    else:
        chosen = available
    if not chosen:
        raise ValueError("the part studio has no solid parts")
    bed = _target_bed(bed)
    added = []
    for p in chosen:
        data = onshape.download_stl(ref, p["partId"])
        m = mz._clean(trimesh.load_mesh(io.BytesIO(data), file_type="stl"))
        src = f"onshape:{ref['did']}/{ref['wvm']}/{ref['wvmid']}/e/{ref['eid']} part {p['name']}"
        added.append(j.add_mesh(p["name"], m, src, bed))
    return _after_add(added, bed, copies, arrange)


@tool()
def transform(objects: list[str], rotate: list[float] | None = None, scale: float | list[float] | None = None,
              lay_flat: bool = False, move_to: list[float] | None = None, move_by: list[float] | None = None,
              reset: bool = False) -> str:
    """Rotate, scale, lay flat or move objects. Steps run in this order: reset, scale, rotate, lay_flat, move.

    Args:
        objects: object ids (or names); ["all"] for every object.
        rotate: degrees about the bed's X, Y, Z axes, e.g. [90, 0, 0] tips it over towards the back,
                [0, 0, 45] turns it on the bed.
        scale: factor (1.1 = 110 %) or [x, y, z] factors.
        lay_flat: put the object on its largest stable flat face.
        move_to: [x, y] of the footprint center on the bed, mm.
        move_by: [dx, dy] mm.
        reset: back to the loaded orientation and size (position is kept).
    """
    j = job()
    ids = _ids(objects)
    for oid in ids:
        j.transform(j.obj(oid), reset=reset, scale=scale, rotate=rotate, lay_flat=lay_flat,
                    move_to=move_to, move_by=move_by)
    j.save()
    out = []
    for oid in ids:
        o = j.obj(oid)
        out.append({"id": oid, "name": o.name, "size_mm": [round(float(v), 2) for v in j.placed(o).extents],
                    "position": [round(v, 2) for v in o.position]})
    issues = {b: j.issues(b) for b in {j.obj(i).bed for i in ids}}
    return _out({"objects": out, "issues": {b: i for b, i in issues.items() if i},
                 "hint": "positions may now overlap; call arrange if needed" if rotate or scale or lay_flat else None})


@tool()
def arrange(bed: str | None = None, spacing: float = config.SPACING, allow_rotate: bool = True,
            spread: bool = True) -> str:
    """Arrange the objects on a bed (or every bed), packed and centered.

    Args:
        bed: bed name (default: every bed).
        spacing: gap between objects, mm.
        allow_rotate: may turn objects 90 degrees about Z to fit better.
        spread: objects that don't fit go to a new bed with the same settings.
    """
    j = job()
    beds = [j.bed(bed).name] if bed else [b.name for b in j.beds]
    reports = {}
    for b in beds:
        r = j.arrange(b, gap=spacing, allow_rotate=allow_rotate, spread=spread)
        reports.update(r["arranged"])
        if r["did_not_fit"]:
            reports.setdefault("did_not_fit", []).extend(r["did_not_fit"])
    j.save()
    return _out({"arranged": reports, "issues": {b.name: j.issues(b.name) for b in j.beds if j.issues(b.name)}})


@tool()
def copy_objects(objects: list[str], count: int = 1, arrange: bool = True) -> str:
    """Add copies of objects (same bed, same settings).

    Args:
        objects: object ids or names.
        count: copies of each.
        arrange: re-arrange the beds afterwards.
    """
    j = job()
    new = []
    for oid in _ids(objects):
        new += j.duplicate(j.obj(oid), count)
    report = {}
    if arrange:
        for b in sorted({o.bed for o in new}):
            report.update(j.arrange(b)["arranged"])
    j.save()
    return _out({"copies": [o.id for o in new], "arranged": report})


@tool()
def remove_objects(objects: list[str]) -> str:
    """Remove objects from the job."""
    j = job()
    ids = set(_ids(objects))
    j.objects = [o for o in j.objects if o.id not in ids]
    j.save()
    return _out({"removed": sorted(ids), "left": [o.id for o in j.objects]})


@tool()
def move_to_bed(objects: list[str], bed: str, arrange: bool = True) -> str:
    """Move objects to another bed.

    Args:
        objects: object ids or names.
        bed: bed name, or "new" for a new bed with the same settings as the objects' current bed.
        arrange: re-arrange the target bed afterwards.
    """
    j = job()
    ids = _ids(objects)
    if bed == "new":
        target = j.new_bed(like=j.bed(j.obj(ids[0]).bed)).name
    else:
        target = j.bed(bed).name
    for oid in ids:
        j.obj(oid).bed = target
    report = j.arrange(target)["arranged"] if arrange else {}
    j.save()
    return _out({"moved": ids, "bed": target, "arranged": report, "issues": j.issues(target)})


# ---------------------------------------------------------------- settings

@tool()
def set_bed(bed: str, print_profile: str | None = None, filament: str | None = None,
            settings: dict[str, str | float | int | bool | None] | None = None,
            supports: str | None = None, support_angle: float | None = None, clear: bool = False) -> str:
    """Change a bed's print profile, filament or settings. bed="new" creates a bed (bed-1's profiles, no overrides).

    Args:
        bed: bed name, or "new".
        print_profile: PrusaSlicer print profile (list_profiles).
        filament: filament profile.
        settings: PrusaSlicer settings to override, e.g. {"layer_height": 0.15, "fill_density": "30%",
                  "perimeters": 4, "brim_width": 5}. A value of null removes that override.
        supports: off | auto | build_plate_only | painted, for the whole bed.
        support_angle: overhang threshold in degrees (supports for overhangs flatter than this; 0 = automatic).
        clear: drop all overrides first.
    """
    j = job()
    b = j.new_bed() if bed == "new" else j.bed(bed)
    pp, fil = print_profile or b.print_profile, filament or b.filament
    if (pp, fil) != (b.print_profile, b.filament):
        slicer.resolved_config(j.printer, pp, fil)   # validates
    b.print_profile, b.filament = pp, fil
    if clear:
        b.settings = {}
    if settings:
        removed = {k.replace("-", "_") for k, v in settings.items() if v is None}
        b.settings.update(slicer.check_settings({k: v for k, v in settings.items() if v is not None}))
        for k in removed:
            b.settings.pop(k, None)
    b.settings.update(supports_settings(supports, support_angle))
    j.save()
    return _out({"bed": b.name, "print_profile": b.print_profile, "filament": b.filament, "settings": b.settings})


@tool()
def remove_bed(bed: str, delete_objects: bool = False) -> str:
    """Remove a bed. It must be empty unless delete_objects is true."""
    j = job()
    b = j.bed(bed)
    objs = j.on_bed(b.name)
    if objs and not delete_objects:
        raise ValueError(f"{b.name} still has objects {[o.id for o in objs]}; move them or pass delete_objects=true")
    if len(j.beds) == 1:
        raise ValueError("a job needs at least one bed")
    j.objects = [o for o in j.objects if o.bed != b.name]
    j.beds = [x for x in j.beds if x.name != b.name]
    j.save()
    return _out({"removed": b.name, "beds": [x.name for x in j.beds]})


@tool()
def set_object_settings(objects: list[str], supports: str | None = None, support_angle: float | None = None,
                        settings: dict[str, str | float | int | bool | None] | None = None,
                        name: str | None = None, clear: bool = False) -> str:
    """Per-object settings, on top of the bed's settings (e.g. supports only for one part, more perimeters
    for another). These go into the 3MF as PrusaSlicer object settings.

    Args:
        objects: object ids or names.
        supports: off | auto | build_plate_only | painted.
        support_angle: overhang threshold in degrees (0 = automatic).
        settings: PrusaSlicer object/region settings, e.g. {"fill_density": "40%", "perimeters": 5,
                  "brim_width": 4}. null removes one.
        name: rename the object(s).
        clear: drop all of the object's own settings first.
    """
    j = job()
    ids = _ids(objects)
    add = slicer.check_settings({k: v for k, v in (settings or {}).items() if v is not None})
    add.update(supports_settings(supports, support_angle))
    removed = {k.replace("-", "_") for k, v in (settings or {}).items() if v is None}
    for oid in ids:
        o = j.obj(oid)
        if clear:
            o.settings = {}
        o.settings.update(add)
        for k in removed:
            o.settings.pop(k, None)
        if name:
            o.name = name
    j.save()
    return _out([{"id": oid, "name": j.obj(oid).name, "settings": j.obj(oid).settings} for oid in ids])


@tool()
def paint_supports(objects: list[str], mode: str = "block", box: list[float] | None = None,
                   small_gaps_mm: float | None = None) -> str:
    """Paint supports on parts of objects, like the support painting in PrusaSlicer's window.
    E.g. keep supports out of screw holes and nut traps, or force them under one feature.
    The paint is stored per triangle, so it stays when objects are moved, rotated or copied. Check with preview
    (blocked = red, enforced = blue).

    Painting only changes where supports go: blocking needs supports on (auto or build_plate_only) for the
    object/bed; enforcing needs supports on in any mode (also "painted").

    Args:
        objects: object ids or names.
        mode: block | enforce | clear.
        box: [x0, y0, z0, x1, y1, z1] in bed coordinates (mm): triangles whose center is inside.
        small_gaps_mm: overhangs whose support would stand on the object itself at most this far below
                       (horizontal holes, nut traps, slots). E.g. 8 blocks supports in holes up to ~8 mm.
                       With box too, both selections are used.
        clear without box/small_gaps_mm removes all paint from the objects.
    """
    j = job()
    if mode != "clear" and box is None and small_gaps_mm is None:
        raise ValueError("give box and/or small_gaps_mm to say where to paint")
    out = []
    for oid in _ids(objects):
        o = j.obj(oid)
        r = j.paint_supports(o, mode, box=box, small_gaps_mm=small_gaps_mm)
        b = j.bed(o.bed)
        eff = {**slicer.resolved_config(j.printer, b.print_profile, b.filament), **b.settings, **o.settings}
        on, auto = eff.get("support_material") == "1", eff.get("support_material_auto") == "1"
        note = None
        if mode == "block" and not (on and auto):
            note = "supports are not on in auto mode for this object, so there is nothing to block yet (supports=auto)"
        elif mode == "enforce" and not on:
            note = "supports are off for this object: enforcing needs supports on (e.g. supports=painted)"
        out.append({"id": oid, "name": o.name, **r, **({"note": note} if note else {})})
    j.save()
    return _out(out)


# ---------------------------------------------------------------- look / slice

@tool()
def preview(bed: str | None = None) -> list:
    """Picture of a bed before slicing: top view and iso view, objects labeled with their ids.
    Use it to check orientation and layout.

    Args:
        bed: bed name (default: bed-1).
    """
    j = job()
    b = j.bed(bed) if bed else j.beds[0]
    issues = j.issues(b.name)
    text = f"{b.name}: {len(j.on_bed(b.name))} objects" + (f"; issues: {issues}" if issues else "; no issues")
    return [text, Image(data=j.preview(b.name), format="png")]


@tool(name="slice")
def slice_beds(beds: list[str] | None = None, open_viewer: bool = True) -> str:
    """Slice beds with PrusaSlicer (in parallel, each with its own settings) into the job folder and show the
    result in the viewer. Does not print anything.

    Args:
        beds: bed names (default: all beds).
        open_viewer: show the result: the app window opens or comes to the front (a browser tab opens only once
                     per session when the app isn't installed).
    """
    global _viewer_opened
    j = job()
    results = j.slice(beds)
    out = []
    for r in results:
        if "skipped" in r:
            out.append(r)
            continue
        item = {"bed": r["bed"], "print_profile": r["print_profile"], "filament": r["filament"],
                "overrides": r["overrides"], "object_settings": r.get("object_settings") or None}
        if r.get("error"):
            item["error"] = r["error"]
        else:
            item.update({"print_time": r.get("print_time"), "filament_g": r.get("filament_g"),
                         "filament_m": r.get("filament_m"), "layers": r.get("layers"),
                         "objects": r.get("objects"), "gcode": str(j.dir / f"{r['bed']}.gcode")})
        if r.get("warnings"):
            item["warnings"] = r["warnings"]
        item["slice_seconds"] = r.get("slice_seconds")
        out.append(item)
    viewer = None
    if open_viewer:
        try:
            started = launch.ensure_running()
            launch.show_job(j.name)
            # The app window opens or comes to the front on every slice; a browser tab only once per session
            # (otherwise every slice would add a tab).
            if launch.app_installed() or started or not _viewer_opened:
                launch.open_ui()
                _viewer_opened = True
            viewer = launch.URL
        except Exception as e:
            viewer = f"could not start the viewer: {e}"
    return _out({"job": j.name, "beds": out, "viewer": viewer,
                 "note": "Nothing was sent to the printer. The user checks the result in the viewer and sends it from there."})


@tool()
def open_viewer() -> str:
    """Start the viewer (if needed) and open it (the app window if installed, else the browser).
    It shows the newest job and follows new slices."""
    global _viewer_opened
    launch.ensure_running()
    if _job:
        launch.show_job(_job.name)
    launch.open_ui()
    _viewer_opened = True
    return _out({"viewer": launch.URL})


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
