"""Games that check the console booted from the BIOS, by fingerprinting char RAM.

Metal Slug 2nd Mission carries its own 64-byte copy of a piece of the SNK BIOS's
boot-time char RAM and sweeps 0xA000..0xC000 for it. Miss, and it wipes the magic
"MET2" at 0x6A88; a routine then zeroes the key configuration at 0x46DC/DD every
other frame, so `and A,<mask>` is always `and A,0` -- the game runs, it looks
perfect, and shoot and jump never fire again. A deliberately quiet punishment for
what it takes to be a pirate copy.

The retail BIOS leaves that data at 0xA1C0 as a by-product of its own boot. Our
clean-room HLE image cannot: those bytes are SNK glyphs, and the check is exactly
a demand for SNK's own expression. So we ship none of it -- **the sixty-four bytes
come out of the player's cartridge**, which already contains them, and go into the
player's char RAM. This module stores facts (a title, an offset, an address), not
data.

It is BEHAVIOUR-gated, not BIOS-gated: nothing happens if the fingerprint is
already in char RAM, which is the case with a real bios.bin. Under the real BIOS
this code is a no-op by construction, so it cannot regress that path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

CHAR_RAM_BASE = 0x00A000
CHAR_RAM_SIZE = 0x2000


@dataclass(frozen=True)
class _Fingerprint:
    """One game's boot-time check of the BIOS's char RAM."""

    title: bytes        # header name at 0x24, as stored (16 bytes, NUL-padded)
    src: int            # where the game keeps its own copy, as a ROM offset
    length: int         # how many bytes it compares
    dest: int           # where to put them -- where the retail BIOS has them


# Only entries proven on hardware-faithful measurement belong here. The scan is a
# SEARCH over the whole 8 KiB, so `dest` is free; 0xA1C0 is where the retail BIOS
# happens to leave it, and it lands on tiles 0x1C-0x1F -- control codes, blank in
# our font -- so nothing visible is overwritten.
FINGERPRINTS: tuple[_Fingerprint, ...] = (
    _Fingerprint(title=b"METALSLUG2ND\0\0\0\0", src=0x08DCC4, length=64, dest=0x00A1C0),
)


def _match(rom: bytes) -> _Fingerprint | None:
    if len(rom) < 0x40:
        return None
    name = rom[0x24:0x34]
    for fp in FINGERPRINTS:
        if name == fp.title and len(rom) >= fp.src + fp.length:
            return fp
    return None


def restore(rom: bytes,
            read: Callable[[int, int], bytes],
            write: Callable[[int, bytes], None]) -> _Fingerprint | None:
    """Put this game's expected fingerprint in char RAM if it is not there already.

    Returns the entry that was applied, or None (no match, or the running BIOS
    already satisfied the check). Call right after a hand-off reset: the game
    makes its check within the first two frames.
    """
    fp = _match(rom)
    if fp is None:
        return None
    want = rom[fp.src:fp.src + fp.length]
    if want in read(CHAR_RAM_BASE, CHAR_RAM_SIZE):
        return None                      # real BIOS -- nothing to do
    write(fp.dest, want)
    return fp
