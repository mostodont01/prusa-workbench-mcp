"""Loading models and the geometry math for placing them (trimesh + numpy)."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import trimesh

from . import slicer

TRIMESH_FORMATS = {".stl", ".3mf", ".obj", ".ply", ".off", ".glb", ".gltf"}
SLICER_FORMATS = {".step", ".stp", ".amf"}


def load(path: Path, tmp_dir: Path) -> list[tuple[str, trimesh.Trimesh]]:
    """Load a model file into (name, mesh) pairs, one per object. Units are taken as mm."""
    ext = path.suffix.lower()
    if ext in SLICER_FORMATS:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        src = tmp_dir / path.name           # copy first: the Flatpak only sees $HOME
        if src.resolve() != path.resolve():
            shutil.copyfile(path, src)
        stl = tmp_dir / f"{path.stem}.converted.stl"
        slicer.convert_to_stl(src, stl)     # PrusaSlicer reads STEP itself
        return [(path.stem, _clean(trimesh.load_mesh(stl, file_type="stl")))]
    if ext not in TRIMESH_FORMATS:
        raise ValueError(f"unsupported file type {ext!r} (use STL, 3MF, OBJ, STEP)")
    loaded = trimesh.load(path, force=None)
    if isinstance(loaded, trimesh.Scene):
        out = []
        for node in loaded.graph.nodes_geometry:
            transform, geom_name = loaded.graph[node]
            geom = loaded.geometry[geom_name]
            if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
                continue
            m = geom.copy()
            m.apply_transform(transform)
            name = node if node and node != geom_name else geom_name
            out.append((str(name), _clean(m)))
        if not out:
            raise ValueError(f"{path.name} contains no meshes")
        return out if len(out) > 1 else [(path.stem, out[0][1])]
    return [(path.stem, _clean(loaded))]


def _clean(m: trimesh.Trimesh) -> trimesh.Trimesh:
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.remove_unreferenced_vertices()
    return m


# ---------- transforms ----------
# An object = original mesh + 3x3 linear part (rotation/scale) + xy position of its footprint center.
# The placed mesh always sits on z = 0.

def placed(mesh: trimesh.Trimesh, linear: np.ndarray, position: tuple[float, float]) -> trimesh.Trimesh:
    v = mesh.vertices @ np.asarray(linear, dtype=float).T
    lo, hi = v.min(axis=0), v.max(axis=0)
    v = v + np.array([position[0] - (lo[0] + hi[0]) / 2, position[1] - (lo[1] + hi[1]) / 2, -lo[2]])
    faces = mesh.faces if np.linalg.det(linear) > 0 else mesh.faces[:, ::-1]  # mirrored -> keep normals outward
    return trimesh.Trimesh(v, faces, process=False)


def size(mesh: trimesh.Trimesh, linear: np.ndarray) -> np.ndarray:
    v = mesh.vertices @ np.asarray(linear, dtype=float).T
    return v.max(axis=0) - v.min(axis=0)


def rotation(x: float = 0, y: float = 0, z: float = 0) -> np.ndarray:
    """Rotation about the global X, then Y, then Z axis, in degrees."""
    rx, ry, rz = np.radians([x, y, z])
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]])
    Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
    Rz = np.array([[np.cos(rz), -np.sin(rz), 0], [np.sin(rz), np.cos(rz), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def flat_faces(mesh: trimesh.Trimesh, linear: np.ndarray, count: int = 5) -> list[dict]:
    """Candidate faces to lay the object on: flat areas of its convex hull, largest first.
    Each has the hull area, the outward normal (in current orientation) and whether the center of mass
    is above the face (stable)."""
    v = mesh.vertices @ np.asarray(linear, dtype=float).T
    hull = trimesh.Trimesh(v, mesh.faces, process=False).convex_hull
    groups = [list(f) for f in hull.facets]
    in_facet = set(i for g in groups for i in g)
    groups += [[i] for i in range(len(hull.faces)) if i not in in_facet]
    com = trimesh.Trimesh(v, mesh.faces, process=False).center_mass if mesh.is_watertight else v.mean(axis=0)
    cands = []
    for g in groups:
        area = float(hull.area_faces[g].sum())
        n = hull.face_normals[g[0]]
        # Stable if the center of mass projects inside this face (checked on the face's triangles).
        stable = False
        for fi in g:
            tri = hull.vertices[hull.faces[fi]]
            p = com - np.dot(com - tri[0], n) * n
            bary = trimesh.triangles.points_to_barycentric(tri[None], p[None])[0]
            if (bary >= -1e-6).all():
                stable = True
                break
        cands.append({"area": area, "normal": n, "stable": stable})
    cands.sort(key=lambda c: (-c["stable"], -c["area"]))
    return cands[:count]


def lay_flat_rotation(normal: np.ndarray) -> np.ndarray:
    """Rotation that turns the given outward face normal to point straight down."""
    return trimesh.geometry.align_vectors(np.asarray(normal, dtype=float), np.array([0.0, 0.0, -1.0]))[:3, :3]
