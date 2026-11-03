"""Soft-patching: apply an IPS or BPS patch to the ROM as it loads, never to the file.

A fan translation, a bug fix or a hack ships as a patch, and the scene convention --
RetroArch, mGBA, Mesen -- is to drop `Game.ips` (or `.bps`) beside `Game.ngc` and have
the emulator apply it in memory. The ROM on disk stays the clean dump, so the patch can
be removed, swapped or updated without keeping three copies of the game.

⛔ THE ONE THING THAT CAN GO WRONG FOR REAL. This emulator saves a game INTO its ROM
file by default (the cartridge is the save). A patched image written back there would
turn the clean dump into the patched one, silently and for good. So a patched load is
treated like an archive: read-only source, save in the `.flash` sidecar. The sidecar
holds only the blocks the game wrote, so the same save follows the game patched or not.

Formats:
  * **IPS** -- offset/length records, RLE runs, optional truncate after `EOF`. No
    checksum at all: a patch made for another revision applies "fine" and breaks the
    game. Nothing can be done about that except say which patch is loaded.
  * **BPS** -- carries CRC32s of the source, the target and itself. A source mismatch
    means the patch was made for a different dump, and it is REFUSED: half-translated
    menus over the wrong ROM are worse than the clean game.
"""

from __future__ import annotations

import zlib
from pathlib import Path

PATCH_EXTS = (".bps", ".ips")     # order = preference when both are present


class PatchError(Exception):
    """The patch is unreadable, or was not made for this ROM."""


def find_patch(rom_path: str | Path) -> Path | None:
    """The patch beside a ROM, or None.

    `Game.ngc` -> `Game.bps` / `Game.ips` in the same folder. For a game inside an
    archive (`Pack.zip/Game.ngc`) the patch sits beside the ARCHIVE and is named after
    the member, because nothing can be dropped inside a .zip without rewriting it.
    """
    from core.rom_loader import split_member
    archive, member = split_member(rom_path)
    if member:
        folder, stem = archive.parent, Path(member).stem
    else:
        folder, stem = archive.parent, archive.stem
    for ext in PATCH_EXTS:
        for candidate in (folder / f"{stem}{ext}", folder / f"{stem}{ext.upper()}"):
            if candidate.is_file():
                return candidate
    return None


def apply(rom: bytes, patch: bytes) -> bytes:
    """Return `rom` with `patch` applied. Raises PatchError."""
    if patch.startswith(b"PATCH"):
        return apply_ips(rom, patch)
    if patch.startswith(b"BPS1"):
        return apply_bps(rom, patch)
    raise PatchError("not an IPS or BPS patch")


def apply_ips(rom: bytes, patch: bytes) -> bytes:
    out = bytearray(rom)
    pos = 5
    end = len(patch)
    while True:
        if pos + 3 > end:
            raise PatchError("IPS ends without its EOF marker")
        if patch[pos:pos + 3] == b"EOF":
            pos += 3
            break
        if pos + 5 > end:
            raise PatchError("IPS record is cut short")
        offset = int.from_bytes(patch[pos:pos + 3], "big")
        size = int.from_bytes(patch[pos + 3:pos + 5], "big")
        pos += 5
        if size == 0:                       # RLE: count, then one byte repeated
            if pos + 3 > end:
                raise PatchError("IPS run is cut short")
            count = int.from_bytes(patch[pos:pos + 2], "big")
            data = bytes([patch[pos + 2]]) * count
            pos += 3
        else:
            if pos + size > end:
                raise PatchError("IPS record runs past the end of the patch")
            data = patch[pos:pos + size]
            pos += size
        if offset + len(data) > len(out):
            out.extend(b"\xFF" * (offset + len(data) - len(out)))   # erased flash
        out[offset:offset + len(data)] = data
    if pos + 3 <= end:                      # the truncate extension
        out = out[:int.from_bytes(patch[pos:pos + 3], "big")]
    return bytes(out)


def _bps_number(patch: bytes, pos: int) -> tuple[int, int]:
    data, shift = 0, 1
    while True:
        if pos >= len(patch):
            raise PatchError("BPS number runs past the end of the patch")
        x = patch[pos]
        pos += 1
        data += (x & 0x7F) * shift
        if x & 0x80:
            return data, pos
        shift <<= 7
        data += shift


def apply_bps(rom: bytes, patch: bytes) -> bytes:
    if len(patch) < 4 + 3 + 12:
        raise PatchError("BPS patch is too short")
    body_end = len(patch) - 12
    src_crc = int.from_bytes(patch[body_end:body_end + 4], "little")
    dst_crc = int.from_bytes(patch[body_end + 4:body_end + 8], "little")
    own_crc = int.from_bytes(patch[body_end + 8:], "little")
    if zlib.crc32(patch[:-4]) != own_crc:
        raise PatchError("BPS patch is damaged (its own checksum does not match)")
    if zlib.crc32(rom) != src_crc:
        raise PatchError(
            f"BPS patch was made for a different ROM (expects CRC32 {src_crc:08X}, "
            f"this one is {zlib.crc32(rom):08X})")

    pos = 4
    src_size, pos = _bps_number(patch, pos)
    dst_size, pos = _bps_number(patch, pos)
    meta_size, pos = _bps_number(patch, pos)
    pos += meta_size
    if src_size != len(rom):
        raise PatchError("BPS patch was made for a ROM of another size")

    out = bytearray(dst_size)
    out_pos = src_rel = dst_rel = 0
    while pos < body_end:
        data, pos = _bps_number(patch, pos)
        command, length = data & 3, (data >> 2) + 1
        if out_pos + length > dst_size:
            raise PatchError("BPS action writes past the end of the target")
        # ⚠️ Every copy is bounds-checked first: a short slice assigned into a bytearray
        # does not fail, it RESIZES the target -- a damaged patch would come out as a
        # ROM of the wrong length instead of an error.
        if command == 0:                    # SourceRead
            if out_pos + length > len(rom):
                raise PatchError("BPS reads past the end of the source")
            out[out_pos:out_pos + length] = rom[out_pos:out_pos + length]
        elif command == 1:                  # TargetRead
            if pos + length > body_end:
                raise PatchError("BPS data runs past the end of the patch")
            out[out_pos:out_pos + length] = patch[pos:pos + length]
            pos += length
        else:
            delta, pos = _bps_number(patch, pos)
            delta = -(delta >> 1) if delta & 1 else delta >> 1
            if command == 2:                # SourceCopy
                src_rel += delta
                if src_rel < 0 or src_rel + length > len(rom):
                    raise PatchError("BPS copies from outside the source")
                out[out_pos:out_pos + length] = rom[src_rel:src_rel + length]
                src_rel += length
            else:                           # TargetCopy -- may overlap, byte by byte
                dst_rel += delta
                if dst_rel < 0 or dst_rel >= out_pos:
                    raise PatchError("BPS copies from target bytes not yet written")
                for i in range(length):
                    out[out_pos + i] = out[dst_rel + i]
                dst_rel += length
        out_pos += length
    if zlib.crc32(out) != dst_crc:
        raise PatchError("BPS result does not match the patch's target checksum")
    return bytes(out)
