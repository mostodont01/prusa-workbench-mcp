"""Copy G-code to a USB stick (for when Prusa Connect is offline). Stdlib only (runs in the viewer).

drives() lists mounted removable drives; copy() writes a file to the root of one (the MINI lists the stick's
root) and flushes it to the stick; eject() unmounts it (and powers it off on Linux) so it can be pulled out.
Only a mount point that drives() reports is ever written to.
"""
from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
from pathlib import Path

from .osutil import LINUX, MACOS, WINDOWS


def _lsblk_drives(data: dict) -> list[dict]:
    """Mounted partitions of removable / hot-plugged / USB disks, from `lsblk -J` output."""
    out = []

    def walk(nodes, removable):
        for n in nodes or []:
            rem = removable or bool(n.get("rm")) or bool(n.get("hotplug")) or n.get("tran") == "usb"
            mount = n.get("mountpoint") or next((m for m in n.get("mountpoints") or [] if m), None)
            if rem and mount and n.get("type") not in ("loop", "rom") and mount != "/" \
                    and not mount.startswith(("/boot", "/snap", "[")):
                out.append({"mount": mount, "label": n.get("label") or Path(mount).name, "device": n.get("path")})
            walk(n.get("children"), rem)

    walk(data.get("blockdevices"), False)
    return out


def _linux() -> list[dict]:
    try:
        res = subprocess.run(["lsblk", "-J", "-o", "PATH,MOUNTPOINT,RM,HOTPLUG,TRAN,LABEL,TYPE"],
                             capture_output=True, timeout=10, check=True, text=True)
        found = _lsblk_drives(json.loads(res.stdout))
    except (OSError, subprocess.SubprocessError, ValueError):
        found = []
    # fallback / extra: whatever the desktop automounted
    user = os.environ.get("USER") or ""
    known = {d["mount"] for d in found}
    for base in (Path("/media") / user, Path("/run/media") / user):
        try:
            for m in base.iterdir():
                if os.path.ismount(m) and str(m) not in known:
                    found.append({"mount": str(m), "label": m.name, "device": None})
        except OSError:
            pass
    return found


def _windows() -> list[dict]:
    import ctypes
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask, out = k32.GetLogicalDrives(), []
    for i, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        if mask >> i & 1 and k32.GetDriveTypeW(root) == 2:   # DRIVE_REMOVABLE
            buf = ctypes.create_unicode_buffer(261)
            ok = k32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
            if ok:   # no medium -> skip
                out.append({"mount": root, "label": buf.value or root, "device": None})
    return out


def _macos() -> list[dict]:
    out = []
    try:
        for m in Path("/Volumes").iterdir():
            if os.path.ismount(m) and m.resolve() != Path("/"):
                out.append({"mount": str(m), "label": m.name, "device": None})
    except OSError:
        pass
    return out


def drives() -> list[dict]:
    """[{mount, label, device, free}] of mounted removable drives we can write to."""
    found = _windows() if WINDOWS else _macos() if MACOS else _linux()
    out = []
    for d in found:
        try:
            d["free"] = shutil.disk_usage(d["mount"]).free
        except OSError:
            continue
        if os.access(d["mount"], os.W_OK):
            out.append(d)
    return out


def find(mount: str) -> dict | None:
    return next((d for d in drives() if d["mount"] == mount), None)


def copy(src: Path, mount: str, name: str) -> Path:
    """Copy src to <mount>/<name> and flush it to the stick. Raises ValueError for an unknown drive."""
    d = find(mount)
    if d is None:
        raise ValueError("that USB drive is not connected any more")
    if Path(name).name != name or name.startswith("."):
        raise ValueError(f"bad file name {name!r}")
    if d["free"] < src.stat().st_size:
        raise ValueError("not enough free space on the USB drive")
    dst = Path(d["mount"]) / name
    tmp = dst.with_name(dst.name + ".part")
    with src.open("rb") as fi, tmp.open("wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
        fo.flush()
        os.fsync(fo.fileno())
    os.replace(tmp, dst)
    if hasattr(os, "sync"):
        os.sync()
    return dst


def eject(mount: str) -> dict:
    """Unmount (and on Linux power off) the drive. Returns {ok, error?}."""
    d = find(mount)
    if d is None:
        return {"ok": True, "note": "already gone"}
    try:
        if LINUX:
            dev = d.get("device")
            if not dev or not shutil.which("udisksctl"):
                return {"ok": False, "error": "can't eject here (no udisksctl); eject it in the file manager"}
            subprocess.run(["udisksctl", "unmount", "-b", dev], capture_output=True, timeout=60, check=True,
                           text=True)
            # power off the whole disk (sdb1 -> sdb); not fatal if it fails, it's unmounted already
            disk = subprocess.run(["lsblk", "-no", "PKNAME", dev], capture_output=True, text=True, timeout=10).stdout
            if disk.strip():
                subprocess.run(["udisksctl", "power-off", "-b", f"/dev/{disk.strip()}"], capture_output=True,
                               timeout=60)
        elif MACOS:
            subprocess.run(["diskutil", "eject", mount], capture_output=True, timeout=60, check=True, text=True)
        else:
            return {"ok": False, "error": "eject it from the taskbar (Safely Remove Hardware)"}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": (e.stderr or str(e)).strip()}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}
