"""What of the cartridge this session actually executed.

The core has recorded this all along -- one bit per byte of the cart window, set
when an instruction STARTS there -- and nothing in the emulator ever looked at it.
`coverage_bitmap()` had no caller outside the binding that defines it. So "does the
game even reach this code?" has been answered by reading disassembly and guessing,
one ROM at a time, for the whole project.

Two things it settles that nothing else can:

  * a routine you are debugging that **never runs**. A breakpoint that does not fire
    proves nothing on its own -- it looks exactly like a breakpoint on the wrong
    address. A cold region says the CPU has not been there.
  * whether an input actually **reached new code**. Press a button, watch the count.
    "Driving the buttons exercised more of the ROM" becomes a number instead of a
    hope.

⚠️ THE BIT MEANS "AN INSTRUCTION STARTED HERE", NOT "THIS BYTE RAN". The bytes
inside an instruction are not marked, so a naive byte count UNDER-reports execution
and a naive gap list OVER-reports dead code. Everything here is built around that:
`gaps()` only reports runs long enough that they cannot be instruction interiors,
and the number it takes for that is a parameter you can see, not a constant hidden
in a loop.

⚠️ AND IT COVERS ONE CHIP. The window is 0x200000..0x3FFFFF; a 4 MiB cartridge has
a second die mapped at 0x800000 which is NOT recorded. `stats()` says so rather
than reporting a percentage of the wrong denominator.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

COVERAGE_LO = 0x200000
COVERAGE_HI = 0x3FFFFF
COVERAGE_SPAN = COVERAGE_HI - COVERAGE_LO + 1        # 2 MiB, one bit per byte
SECOND_CHIP_BASE = 0x800000

# A run shorter than this could be the interior of executed instructions -- the
# longest TLCS-900 encoding is well under it -- so anything below is not evidence of
# anything and is not reported as a gap.
MIN_GAP = 64


def unpack(bitmap: bytes, size: int | None = None) -> np.ndarray:
    """The bitmap as one bool per byte of the cart window, LSB first.

    LSB first is not a choice: `note_exec` uses `1 << (i & 7)`, so bit 0 of a cell
    is the LOWEST address in it. Unpacking big-endian silently shuffles every run
    of eight bytes, which still looks like a plausible coverage map.
    """
    if not bitmap:
        return np.zeros(0, bool)
    bits = np.unpackbits(np.frombuffer(bitmap, np.uint8), bitorder="little")
    if size is not None:
        bits = bits[:size]
    return bits.astype(bool)


@dataclass(frozen=True)
class Stats:
    reached: int                 # instruction addresses executed
    rom_size: int                # the cartridge file, in bytes
    covered_span: int            # how much of the ROM the window can even see
    unreachable_note: str        # non-empty when part of the ROM is not recorded

    @property
    def percent(self) -> float:
        return (100.0 * self.reached / self.covered_span) if self.covered_span else 0.0


def stats(reached: int, rom_size: int) -> Stats:
    """`reached` is the core's own distinct-address count; `rom_size` the file.

    The denominator is what the window can SEE, never the whole file: reporting
    hits against a 4 MiB ROM when half of it is not recorded would show a game
    that runs perfectly as 40 % dead.
    """
    visible = min(rom_size, COVERAGE_SPAN) if rom_size else COVERAGE_SPAN
    note = ""
    if rom_size > COVERAGE_SPAN:
        note = (f"only the first {COVERAGE_SPAN // 1024} KiB are recorded — this "
                f"cartridge has a second chip at 0x{SECOND_CHIP_BASE:06X} that "
                f"coverage does not watch")
    return Stats(reached, rom_size, visible, note)


@dataclass(frozen=True)
class Gap:
    addr: int          # CPU address of the first untouched byte
    length: int

    @property
    def rom_offset(self) -> int:
        return self.addr - COVERAGE_LO

    @property
    def end(self) -> int:
        return self.addr + self.length - 1


def gaps(bits: np.ndarray, rom_size: int, min_len: int = MIN_GAP) -> list[Gap]:
    """Runs of never-executed bytes, longest first.

    Only runs of at least `min_len` are returned: below that a gap can simply be
    the middle of instructions that DID run, and reporting it would manufacture
    dead code out of the encoding.
    """
    n = min(len(bits), rom_size) if rom_size else len(bits)
    if n <= 0:
        return []
    b = bits[:n]
    # Edges of the untouched runs, with sentinels so a run touching either end is
    # not silently dropped.
    cold = np.concatenate(([False], ~b, [False]))
    edges = np.flatnonzero(cold[1:] != cold[:-1])
    out = []
    for start, stop in zip(edges[0::2], edges[1::2]):
        length = int(stop - start)
        if length >= min_len:
            out.append(Gap(COVERAGE_LO + int(start), length))
    out.sort(key=lambda g: g.length, reverse=True)
    return out


# Colours are DATA labels, not decoration: they mean executed / never executed /
# past the end of this cartridge, and they do not follow the theme.
COLOUR_HOT = (74, 222, 128)
COLOUR_COLD = (55, 58, 70)
COLOUR_ABSENT = (18, 18, 22)


def image(bits: np.ndarray, rom_size: int, width: int = 256,
          rows: int = 256) -> np.ndarray:
    """The cartridge as a picture: one pixel per block, green where code ran.

    A block counts as reached if ANY byte in it was an instruction start -- which
    is the right rule at this granularity, because executed code has a start every
    few bytes. The block size is returned to the caller through `block_size()` so
    the view can say what a pixel is worth instead of implying it is a byte.
    """
    size = rom_size or COVERAGE_SPAN
    block = block_size(size, width, rows)
    cells = width * rows
    padded = np.zeros(cells * block, bool)
    take = min(len(bits), size, cells * block)
    padded[:take] = bits[:take]
    hot = padded.reshape(cells, block).any(axis=1)

    present = np.zeros(cells, bool)
    present[: (size + block - 1) // block] = True

    img = np.empty((cells, 3), np.uint8)
    img[:] = COLOUR_ABSENT
    img[present] = COLOUR_COLD
    img[present & hot] = COLOUR_HOT
    return img.reshape(rows, width, 3)


def block_size(rom_size: int, width: int = 256, rows: int = 256) -> int:
    size = rom_size or COVERAGE_SPAN
    return max(1, (size + width * rows - 1) // (width * rows))


def address_at(x: int, y: int, rom_size: int, width: int = 256,
               rows: int = 256) -> tuple[int, int]:
    """(first address, block size) for the pixel at (x, y). For hover."""
    block = block_size(rom_size, width, rows)
    return COVERAGE_LO + (y * width + x) * block, block


def format_report(reached: int, bits: np.ndarray, rom_size: int,
                  min_len: int = MIN_GAP, top: int = 40) -> str:
    st = stats(reached, rom_size)
    lines = [f"reached {st.reached} instruction addresses "
             f"({st.percent:.1f}% of {st.covered_span} bytes)"]
    if st.unreachable_note:
        lines.append(f"⚠ {st.unreachable_note}")
    holes = gaps(bits, rom_size, min_len)
    lines.append("")
    lines.append(f"{len(holes)} never-executed runs of at least {min_len} bytes"
                 + (f" — showing the {top} largest" if len(holes) > top else ""))
    for g in holes[:top]:
        lines.append(f"  {g.addr:06X}..{g.end:06X}  {g.length:7d} bytes"
                     f"   (ROM +0x{g.rom_offset:06X})")
    return "\n".join(lines)
