"""Write a 3MF that PrusaSlicer reads with objects already placed and per-object settings."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
import trimesh

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                 '</Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')


# Painted supports, the way PrusaSlicer stores them: a per-triangle attribute holding its TriangleSelector state.
# A whole (unsplit) triangle is one hex digit: enforcer "4", blocker "8".
PAINT_CODES = {"enforce": "4", "block": "8"}


def write(path: Path, objects: list[tuple]) -> None:
    """objects: (name, mesh in bed coordinates, PrusaSlicer per-object settings[, paint]) where paint maps
    "block"/"enforce" to triangle indices (painted supports)."""
    model = io.StringIO()
    model.write('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xml:lang="en-US" '
                'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
                'xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06"><resources>')
    for oid, obj in enumerate(objects, start=1):
        name, mesh = obj[0], obj[1]
        paint = obj[3] if len(obj) > 3 and obj[3] else {}
        codes: dict[int, str] = {}
        for kind, idx in paint.items():
            for i in idx:
                codes[int(i)] = PAINT_CODES[kind]
        model.write(f'<object id="{oid}" name={quoteattr(name)} type="model"><mesh><vertices>')
        v = np.asarray(mesh.vertices, dtype=float)
        model.write("".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in v))
        model.write("</vertices><triangles>")
        model.write("".join(
            f'<triangle v1="{a}" v2="{b}" v3="{c}"' + (f' slic3rpe:custom_supports="{codes[i]}"/>' if i in codes else "/>")
            for i, (a, b, c) in enumerate(np.asarray(mesh.faces))))
        model.write("</triangles></mesh></object>")
    model.write("</resources><build>")
    for oid in range(1, len(objects) + 1):
        model.write(f'<item objectid="{oid}"/>')   # vertices are already in bed coordinates
    model.write("</build></model>")

    # PrusaSlicer's own metadata: object names (otherwise a single object is named after the file)
    # and per-object setting overrides.
    cfg = io.StringIO()
    cfg.write('<?xml version="1.0" encoding="UTF-8"?><config>')
    for oid, (name, mesh, settings, *_) in enumerate(objects, start=1):
        cfg.write(f'<object id="{oid}" instances_count="1">'
                  f'<metadata type="object" key="name" value={quoteattr(name)}/>')
        for k, val in settings.items():
            cfg.write(f'<metadata type="object" key={quoteattr(k)} value={quoteattr(str(val))}/>')
        cfg.write(f'<volume firstid="0" lastid="{len(mesh.faces) - 1}">'
                  f'<metadata type="volume" key="name" value={quoteattr(name)}/></volume></object>')
    cfg.write("</config>")

    tmp = path.with_suffix(".3mf.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model.getvalue())
        z.writestr("Metadata/Slic3r_PE_model.config", cfg.getvalue())
    tmp.replace(path)
