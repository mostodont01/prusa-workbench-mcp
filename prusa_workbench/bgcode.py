"""Binary G-code (.bgcode, Prusa's format): read the block list, add thumbnail blocks. Stdlib only.

Format (libbgcode doc/specifications.md): 10-byte header "GCDE", version u32, checksum type u16 (1 = CRC32),
then blocks: header (type u16, compression u16, uncompressed size u32[, compressed size u32 if compressed]),
parameters (2 bytes; thumbnails 6: format, width, height), data, CRC32 over header + parameters + data
(checked against PrusaSlicer 2.9.6 output: 17/17 blocks match).

We let PrusaSlicer write the .bgcode itself (the G-code compression the firmware reads) and only add thumbnail
blocks, which are uncompressed, after the printer metadata block, where PrusaSlicer puts them.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"GCDE"
FILE_META, GCODE, SLICER_META, PRINTER_META, PRINT_META, THUMBNAIL = range(6)
THUMB_FORMATS = {"PNG": 0, "JPG": 1, "QOI": 2}


class BgcodeError(ValueError):
    pass


@dataclass
class Block:
    type: int
    start: int
    end: int          # exclusive, including the checksum


def blocks(data: bytes) -> tuple[int, list[Block]]:
    """Parse and verify every block. Returns (checksum type, blocks)."""
    if len(data) < 10 or data[:4] != MAGIC:
        raise BgcodeError("not a binary G-code file")
    version, ck = struct.unpack_from("<IH", data, 4)
    if version != 1 or ck not in (0, 1):
        raise BgcodeError(f"unsupported bgcode version {version} / checksum {ck}")
    out, pos = [], 10
    while pos < len(data):
        if pos + 8 > len(data):
            raise BgcodeError("truncated block header")
        typ, comp, usize = struct.unpack_from("<HHI", data, pos)
        hdr, size = 8, usize
        if comp:
            size = struct.unpack_from("<I", data, pos + 8)[0]
            hdr = 12
        params = 6 if typ == THUMBNAIL else 2
        body_end = pos + hdr + params + size
        end = body_end + (4 if ck == 1 else 0)
        if typ > THUMBNAIL or end > len(data):
            raise BgcodeError(f"bad block at byte {pos}")
        if ck == 1 and (zlib.crc32(data[pos:body_end]) & 0xFFFFFFFF) != struct.unpack_from("<I", data, body_end)[0]:
            raise BgcodeError(f"checksum mismatch in block at byte {pos}")
        out.append(Block(typ, pos, end))
        pos = end
    return ck, out


def thumbnail_block(fmt: str, width: int, height: int, image: bytes, checksum: int) -> bytes:
    body = struct.pack("<HHI", THUMBNAIL, 0, len(image)) + struct.pack("<HHH", THUMB_FORMATS[fmt], width, height) + image
    return body + (struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF) if checksum == 1 else b"")


def add_thumbnails(path: Path, thumbs: list[tuple[int, int, str, bytes]]) -> None:
    """Insert thumbnails (w, h, 'PNG'|'QOI'|'JPG', image bytes) after the printer metadata block,
    replacing any thumbnails already there. The result is parsed and verified before it replaces the file."""
    data = path.read_bytes()
    ck, bl = blocks(data)
    keep = [b for b in bl if b.type != THUMBNAIL]
    try:
        at = next(i for i, b in enumerate(keep) if b.type == PRINTER_META) + 1
    except StopIteration:
        raise BgcodeError("no printer metadata block") from None
    new = [thumbnail_block(fmt, w, h, img, ck) for w, h, fmt, img in thumbs if fmt in THUMB_FORMATS]
    parts = [data[:10]] + [data[b.start:b.end] for b in keep[:at]] + new + [data[b.start:b.end] for b in keep[at:]]
    out = b"".join(parts)
    blocks(out)   # verify before writing
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(out)
    tmp.replace(path)
