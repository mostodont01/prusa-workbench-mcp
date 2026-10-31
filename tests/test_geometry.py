"""Arranging, placing, lay flat, painting, 3MF contents. No PrusaSlicer needed (except the job's bed size)."""
import itertools
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
import trimesh

from conftest import PRINT, PRINTER, box, tee
from prusa_workbench import arrange, mesh, threemf
from prusa_workbench.job import Job

BED = {"x0": 0.0, "y0": 0.0, "x1": 180.0, "y1": 180.0, "height": 180.0}


def _rects(placements, items):
    size = {i.key: (i.w, i.h) for i in items}
    out = {}
    for p in placements:
        w, h = size[p.key][::-1] if p.rotated else size[p.key]
        out[p.key] = (p.cx - w / 2, p.cy - h / 2, p.cx + w / 2, p.cy + h / 2)
    return out


@pytest.mark.parametrize("seed", range(5))
def test_arrange_inside_bed_and_apart(seed):
    rng = np.random.default_rng(seed)
    items = [arrange.Item(f"o{i}", *rng.uniform(5, 60, 2)) for i in range(25)]
    placed, left = arrange.arrange(items, BED, gap=6)
    rects = _rects(placed, items)
    assert len(placed) + len(left) == len(items) and placed
    for x0, y0, x1, y1 in rects.values():
        assert x0 >= -1e-6 and y0 >= -1e-6 and x1 <= 180 + 1e-6 and y1 <= 180 + 1e-6
    for (a, ra), (b, rb) in itertools.combinations(rects.items(), 2):
        sep = max(rb[0] - ra[2], ra[0] - rb[2], rb[1] - ra[3], ra[1] - rb[3])
        assert sep >= 6 - 1e-6, f"{a} and {b} are closer than the gap"


def test_arrange_rotates_to_fit_and_reports_leftovers():
    placed, left = arrange.arrange([arrange.Item("long", 30, 175)], {**BED, "y1": 100}, gap=6)
    assert placed and placed[0].rotated and not left
    placed, left = arrange.arrange([arrange.Item("big", 190, 190)], BED, gap=6)
    assert not placed and [i.key for i in left] == ["big"]


def test_placed_sits_on_bed_at_position():
    m = box((10, 20, 30), (5, 5, 5))
    p = mesh.placed(m, mesh.rotation(x=90), (50, 60))
    lo, hi = p.bounds
    assert lo[2] == pytest.approx(0) and hi[2] == pytest.approx(20)
    assert ((lo[:2] + hi[:2]) / 2) == pytest.approx([50, 60])


def test_lay_flat_puts_largest_face_down():
    m = box((40, 30, 2), (0, 0, 0))                       # a plate...
    lin = mesh.rotation(x=90)                              # ...standing on its edge
    best = mesh.flat_faces(m, lin, count=1)[0]
    flat = mesh.lay_flat_rotation(best["normal"]) @ lin
    assert mesh.size(m, flat)[2] == pytest.approx(2)


@pytest.mark.slicer   # a job reads the bed size from the printer profile
def test_stored_meshes_come_back_watertight(jobs_dir, filament):
    """Regression: re-reading the STL without merging vertices sent PrusaSlicer a triangle soup."""
    j = Job.create("t", PRINTER, PRINT, filament)
    o = j.add_mesh("pin", trimesh.creation.cylinder(4, 12, sections=40), "test", "bed-1")
    j.save()
    j2 = Job.open(j.name)
    assert j2.placed(j2.obj(o.id)).is_watertight


@pytest.mark.slicer   # a job reads the bed size from the printer profile
def test_paint_follows_the_object(jobs_dir, filament):
    j = Job.create("t", PRINTER, PRINT, filament)
    o = j.add_mesh("tee", tee(), "test", "bed-1")
    lo = j.placed(o).bounds[0]
    # block the underside of the left arm only (left arm = lowest x)
    r = j.paint_supports(o, "block", box=[lo[0] - 1, lo[1] - 1, 19.9, lo[0] + 14, lo[1] + 6, 20.1])
    assert r["selected"] == 2
    before = set(o.paint["block"])
    j.transform(o, rotate=[0, 0, 90], move_to=[40, 40])
    j.save()
    j2 = Job.open(j.name)
    assert set(j2.paint_for(j2.obj(o.id))["block"]) == before
    j.paint_supports(o, "clear")
    assert o.paint == {}


@pytest.mark.slicer   # a job reads the bed size from the printer profile
def test_small_gaps_finds_a_screw_hole(jobs_dir, filament):
    base = box((40, 15, 20), (20, 7.5, 10))
    hole = trimesh.creation.cylinder(2.5, 40, sections=32)
    hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    hole.apply_translation((12, 7.5, 10))
    arm = box((22, 15, 4), (50, 7.5, 18))                 # overhang over the bed: must NOT be painted
    part = trimesh.boolean.union([base.difference(hole), arm])
    j = Job.create("t", PRINTER, PRINT, filament)
    o = j.add_mesh("bracket", part, "test", "bed-1")
    j.paint_supports(o, "block", small_gaps_mm=8)
    m = j.placed(o)
    lo = m.bounds[0]
    centers = m.triangles_center[o.paint["block"]] - lo
    assert len(centers) > 0
    assert (centers[:, 0] < 40).all()                      # only inside the hole, nothing under the arm
    assert ((centers[:, 2] > 7) & (centers[:, 2] < 13)).all()


def test_3mf_has_names_settings_and_paint(tmp_path):
    m = tee()
    path = tmp_path / "a.3mf"
    threemf.write(path, [("tee", m, {"support_material": "1"}, {"block": [3, 4], "enforce": [7]}),
                         ("plain", box((5, 5, 5), (0, 0, 2.5)), {})])
    with zipfile.ZipFile(path) as z:
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        cfg = ET.fromstring(z.read("Metadata/Slic3r_PE_model.config"))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    attr = "{http://schemas.slic3r.org/3mf/2017/06}custom_supports"
    tris = model.findall(".//m:object[@id='1']//m:triangle", ns)
    assert len(tris) == len(m.faces)
    assert {i: t.get(attr) for i, t in enumerate(tris) if t.get(attr)} == {3: "8", 4: "8", 7: "4"}
    meta = {(o.get("id"), md.get("key")): md.get("value") for o in cfg for md in o.findall("metadata")}
    assert meta[("1", "name")] == "tee" and meta[("1", "support_material")] == "1" and meta[("2", "name")] == "plain"
