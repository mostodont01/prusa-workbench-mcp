"""Pack object footprints (bounding rectangles) onto beds. MaxRects, best-short-side-fit."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    key: str
    w: float
    h: float


@dataclass
class Placement:
    key: str
    cx: float          # center of the footprint
    cy: float
    rotated: bool      # turned 90 degrees about Z to fit


def _pack(items: list[Item], W: float, H: float, gap: float, allow_rotate: bool):
    """Place as many items as fit. Each item is padded by `gap` (half on each side, none at the bed edge)."""
    free = [(0.0, 0.0, W + gap, H + gap)]   # +gap: padding is not needed against the bed edge
    placed, left = [], []
    for it in sorted(items, key=lambda i: -(i.w * i.h)):
        best = None
        for fx, fy, fw, fh in free:
            for rot in ((False, True) if allow_rotate and it.w != it.h else (False,)):
                w, h = (it.h, it.w) if rot else (it.w, it.h)
                w, h = w + gap, h + gap
                if w <= fw + 1e-9 and h <= fh + 1e-9:
                    score = (min(fw - w, fh - h), max(fw - w, fh - h), fy, fx)
                    if best is None or score < best[0]:
                        best = (score, fx, fy, w, h, rot)
        if best is None:
            left.append(it)
            continue
        _, x, y, w, h, rot = best
        placed.append((it, x, y, w - gap, h - gap, rot))
        # split every free rect that overlaps the new one
        new_free = []
        for fx, fy, fw, fh in free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                new_free.append((fx, fy, fw, fh))
                continue
            if x > fx: new_free.append((fx, fy, x - fx, fh))
            if x + w < fx + fw: new_free.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy: new_free.append((fx, fy, fw, y - fy))
            if y + h < fy + fh: new_free.append((fx, y + h, fw, fy + fh - y - h))
        free = [a for a in new_free if not any(
            a is not b and a[0] >= b[0] and a[1] >= b[1] and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3]
            for b in new_free)]
    return placed, left


def arrange(items: list[Item], bed: dict, gap: float, allow_rotate: bool = True) -> tuple[list[Placement], list[Item]]:
    """Arrange items on one bed, centered as a group. Returns placements and the items that did not fit."""
    W, H = bed["x1"] - bed["x0"], bed["y1"] - bed["y0"]
    placed, left = _pack(items, W, H, gap, allow_rotate)
    if not placed:
        return [], left
    # center the whole group on the bed
    x_lo = min(p[1] for p in placed); x_hi = max(p[1] + p[3] for p in placed)
    y_lo = min(p[2] for p in placed); y_hi = max(p[2] + p[4] for p in placed)
    dx = bed["x0"] + (W - (x_hi - x_lo)) / 2 - x_lo
    dy = bed["y0"] + (H - (y_hi - y_lo)) / 2 - y_lo
    return [Placement(it.key, x + w / 2 + dx, y + h / 2 + dy, rot) for it, x, y, w, h, rot in placed], left
