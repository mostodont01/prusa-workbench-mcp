"""Small software renderer (Pillow): G-code thumbnails and bed previews. Orthographic, painter's algorithm."""
from __future__ import annotations

import io

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

ORANGE = (237, 107, 33)
PALETTE = [(237, 107, 33), (52, 152, 219), (46, 204, 113), (155, 89, 182), (241, 196, 15), (230, 76, 101),
           (26, 188, 156), (149, 165, 166), (211, 84, 0), (41, 128, 185)]
BG = (29, 31, 35, 255)


def _view(kind: str):
    if kind == "top":
        return np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])
    d = np.array([-0.55, -1.45, 1.15]); d /= np.linalg.norm(d)          # from target towards the camera
    r = np.cross(-d, [0, 0, 1.0]); r /= np.linalg.norm(r)
    u = np.cross(r, -d)
    return r, u, d


def render(meshes: list[tuple[str, trimesh.Trimesh, tuple]], size: tuple[int, int], view: str = "iso",
           bed: dict | None = None, labels: bool = False, background=(0, 0, 0, 0), ss: int = 3,
           fit_bed: bool = True, margin: float | None = None) -> Image.Image:
    """meshes: (label, mesh in bed coordinates, rgb). Fits the objects (and the bed, if fit_bed) into the image."""
    W, H = size[0] * ss, size[1] * ss
    r, u, d = _view(view)
    P = np.stack([r, u, d], axis=1)   # world -> (x, y, depth)

    pts = [m.vertices for _, m, _ in meshes if len(m.vertices)]
    if bed and (fit_bed or not pts):
        pts.append(np.array([[bed["x0"], bed["y0"], 0], [bed["x1"], bed["y0"], 0],
                             [bed["x1"], bed["y1"], 0], [bed["x0"], bed["y1"], 0]]))
    if not pts:
        return Image.new("RGBA", size, background)
    allp = np.concatenate(pts) @ P
    lo, hi = allp[:, :2].min(axis=0), allp[:, :2].max(axis=0)
    if margin is None:
        margin = 0.06 if bed else 0.04
    scale = min(W * (1 - 2 * margin) / max(hi[0] - lo[0], 1e-6), H * (1 - 2 * margin) / max(hi[1] - lo[1], 1e-6))
    cx, cy = (lo + hi) / 2

    def proj(p):
        q = np.asarray(p) @ P
        return np.stack([W / 2 + (q[..., 0] - cx) * scale, H / 2 - (q[..., 1] - cy) * scale], axis=-1), q[..., 2]

    img = Image.new("RGBA", (W, H), background)
    dr = ImageDraw.Draw(img)

    if bed:
        corners = [[bed["x0"], bed["y0"], 0], [bed["x1"], bed["y0"], 0], [bed["x1"], bed["y1"], 0], [bed["x0"], bed["y1"], 0]]
        c2, _ = proj(np.array(corners, dtype=float))
        dr.polygon([tuple(p) for p in c2], fill=(43, 46, 52, 255))
        if min(size) < 64:   # tiny thumbnails: plain plate, no grid
            pass
        else:
          for x in np.arange(np.ceil(bed["x0"] / 10) * 10, bed["x1"] + 1e-6, 10):
            a, _ = proj(np.array([[x, bed["y0"], 0], [x, bed["y1"], 0]]))
            dr.line([tuple(a[0]), tuple(a[1])], fill=(69, 74, 83, 255) if x % 50 == 0 else (54, 58, 65, 255), width=ss)
          for y in np.arange(np.ceil(bed["y0"] / 10) * 10, bed["y1"] + 1e-6, 10):
            a, _ = proj(np.array([[bed["x0"], y, 0], [bed["x1"], y, 0]]))
            dr.line([tuple(a[0]), tuple(a[1])], fill=(69, 74, 83, 255) if y % 50 == 0 else (54, 58, 65, 255), width=ss)
        dr.line([tuple(p) for p in c2] + [tuple(c2[0])], fill=(107, 112, 122, 255), width=ss)

    light = r * 0.35 + u * 0.55 + d * 0.75
    light /= np.linalg.norm(light)
    tris, depth, colors = [], [], []
    for _, m, rgb in meshes:
        if not len(m.faces):
            continue
        n = m.face_normals
        facing = n @ d > 0                       # back-face culling
        if not facing.any():
            continue
        tv = m.vertices[m.faces[facing]]          # (k, 3, 3)
        t2, tz = proj(tv)
        shade = 0.38 + 0.62 * np.clip(n[facing] @ light, 0, 1)
        col = (np.array(rgb)[None, :] * shade[:, None]).clip(0, 255).astype(np.uint8)
        tris.append(t2); depth.append(tz.mean(axis=1)); colors.append(col)
    if tris:
        tris = np.concatenate(tris); depth = np.concatenate(depth); colors = np.concatenate(colors)
        for i in np.argsort(depth):               # far to near
            c = colors[i]
            dr.polygon([tuple(p) for p in tris[i]], fill=(int(c[0]), int(c[1]), int(c[2]), 255))

    out = img.resize(size, Image.LANCZOS)
    if labels:
        _labels(out, meshes, lambda p: proj(p)[0] / ss)
    return out


def _labels(img: Image.Image, meshes, proj2d) -> None:
    dr = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=13)
    except TypeError:
        font = ImageFont.load_default()
    for label, m, rgb in meshes:
        if not label or not len(m.vertices):
            continue
        lo, hi = m.bounds
        p = proj2d(np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2]]))
        box = dr.textbbox((0, 0), label, font=font)
        w, h = box[2] - box[0] + 8, box[3] - box[1] + 6
        x, y = p[0] - w / 2, p[1] - h - 4
        dr.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=(20, 21, 24, 220), outline=rgb + (255,))
        dr.text((x + 4, y + 3 - box[1]), label, fill=(240, 240, 240, 255), font=font)


def png_bytes(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG", optimize=True)
    return b.getvalue()


def encode(img: Image.Image, fmt: str) -> bytes:
    b = io.BytesIO()
    fmt = fmt.upper()
    if fmt == "JPG":
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[3])
        flat.save(b, "JPEG", quality=85)
    elif fmt == "QOI":
        img.save(b, "QOI")
    else:
        img.save(b, "PNG", optimize=True)
    return b.getvalue()


def bed_preview(objects: list[tuple[str, trimesh.Trimesh]], bed: dict, title: str = "") -> bytes:
    """Top view + iso view of one bed, objects colored and labeled. For Claude to check a layout."""
    meshes = [(label, m, PALETTE[i % len(PALETTE)]) for i, (label, m) in enumerate(objects)]
    top = render(meshes, (520, 520), "top", bed=bed, labels=True, background=BG, ss=2)
    iso = render(meshes, (520, 520), "iso", bed=bed, labels=True, background=BG, ss=2, fit_bed=False)
    out = Image.new("RGBA", (1040, 548), BG)
    out.paste(top, (0, 28)); out.paste(iso, (520, 28))
    dr = ImageDraw.Draw(out)
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
    dr.text((10, 7), f"{title}  -  top view (front edge at the bottom)", fill=(200, 200, 205, 255), font=font)
    dr.text((530, 7), "iso view (zoomed to the objects)", fill=(200, 200, 205, 255), font=font)
    return png_bytes(out)
