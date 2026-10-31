"""What we rely on from the PrusaSlicer CLI. If a PrusaSlicer update breaks one of these, slicing beds with
their own settings, per-object settings or painted supports silently stops doing what Claude asked for."""
import pytest

from conftest import PRINT, PRINTER, box, support_xs, tee
from prusa_workbench import bgcode, gcode, sendfile, slicer, threemf
from prusa_workbench.job import Job

pytestmark = pytest.mark.slicer


def _slice(tmp, objects, filament, ini_text="# none\n", binary=False):
    threemf.write(tmp / "b.3mf", objects)
    (tmp / "b.ini").write_text(ini_text)
    out = tmp / ("b.bgcode" if binary else "b.gcode")
    return slicer.slice_3mf(tmp / "b.3mf", tmp / "b.ini", out, PRINTER, PRINT, filament, binary=binary), out


def _at(mesh, x, y):
    m = mesh.copy()
    lo, hi = m.bounds
    m.apply_translation([x - (lo[0] + hi[0]) / 2, y - (lo[1] + hi[1]) / 2, -lo[2]])
    return m


def test_bed_ini_overrides_the_profile(jobs_dir, filament):
    res, out = _slice(jobs_dir, [("cube", _at(box((10, 10, 4), (0, 0, 0)), 90, 90), {})], filament,
                      "perimeters = 5\nfill_density = 33%\n")
    assert res["ok"], res
    s = gcode.parse_gcode(out)["settings"]
    assert (s["perimeters"], s["fill_density"], s["print_settings_id"]) == ("5", "33%", PRINT)


def test_per_object_supports(jobs_dir, filament):
    """Supports switched on for one object only (3MF object metadata)."""
    objs = [("tee", _at(tee(), 60, 90), {"support_material": "1", "support_material_auto": "1"}),
            ("tee2", _at(tee(), 120, 90), {"support_material": "0"})]
    res, out = _slice(jobs_dir, objs, filament)
    assert res["ok"], res
    xs = support_xs(out)
    assert xs and max(xs) < 90, "supports must only be under the first tee"


def test_painted_blocker(jobs_dir, filament):
    """Blocking the left arm's underside (custom_supports='8') leaves supports only under the right arm."""
    t = _at(tee(), 90, 90)
    c, n = t.triangles_center, t.face_normals
    left_under = [i for i in range(len(t.faces)) if c[i, 0] < 87.4 and abs(c[i, 2] - 20) < 0.01 and n[i, 2] < -0.9]
    assert len(left_under) == 2
    sup = {"support_material": "1", "support_material_auto": "1"}
    res, out = _slice(jobs_dir, [("tee", t, sup)], filament)
    assert res["ok"] and min(support_xs(out)) < 80          # control: supports under both arms
    res, out = _slice(jobs_dir, [("tee", t, sup, {"block": left_under})], filament)
    assert res["ok"] and min(support_xs(out)) > 88          # left arm blocked


def test_off_the_bed_means_no_gcode_even_with_exit_0(jobs_dir, filament):
    res, out = _slice(jobs_dir, [("far", _at(box((10, 10, 4), (0, 0, 0)), 400, 400), {})], filament)
    assert not res["ok"] and not out.exists() and "error" in res


def test_binary_gcode_layout_and_thumbnails(jobs_dir, filament):
    """PrusaSlicer's .bgcode still has the layout our thumbnail insertion expects."""
    res, out = _slice(jobs_dir, [("cube", _at(box((10, 10, 4), (0, 0, 0)), 90, 90), {})], filament, binary=True)
    assert res["ok"], res
    _, blocks = bgcode.blocks(out.read_bytes())
    assert bgcode.PRINTER_META in [b.type for b in blocks]
    bgcode.add_thumbnails(out, [(16, 16, "PNG", b"\x89PNG fake")])
    _, blocks = bgcode.blocks(out.read_bytes())
    types = [b.type for b in blocks]
    assert types.index(bgcode.THUMBNAIL) == types.index(bgcode.PRINTER_META) + 1


def test_setting_names_are_checked():
    assert slicer.check_settings({"fill-density": "20%", "support_material": True}) == \
        {"fill_density": "20%", "support_material": "1"}
    with pytest.raises(ValueError, match="brim_widht"):
        slicer.check_settings({"brim_widht": 3})


def test_job_slice_and_send_file(jobs_dir, filament):
    """The whole path: job -> slice (text G-code with thumbnails) -> binary file to send, thumbnails copied."""
    j = Job.create("t", PRINTER, PRINT, filament)
    j.add_mesh("cube", box((20, 20, 5), (0, 0, 0)), "test", "bed-1")
    j.arrange("bed-1")
    j.save()
    [r] = j.slice()
    assert not r.get("error"), r
    text = j.dir / "bed-1.gcode"
    assert len(gcode.read_thumbnails(text)) == len(gcode.thumbnail_specs(text)) > 0
    f, kind, note = sendfile.prepare(j.dir, "bed-1", ["bgcode", "gcode"])
    assert kind == "binary", note
    _, blocks = bgcode.blocks(f.read_bytes())
    assert sum(b.type == bgcode.THUMBNAIL for b in blocks) == len(gcode.thumbnail_specs(text))
    assert f.stat().st_size < text.stat().st_size
