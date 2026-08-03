"""Where the frame goes: cycles per function, from a real instruction trace.

Not a sampler. The core can already retire instructions while recording every one
of them -- PC, length, memory accesses AND the cycles it cost -- so a profile here
is an exact accounting of a captured window, not a statistical guess about one. A
sampler at the refresh rate of a debug window would take eight samples a second out
of six hundred thousand instructions and call the result a profile.

CYCLES, not instruction counts, are the unit. On this machine the cartridge bus is
slow and instruction cost varies by a factor of ten; ranking functions by how many
instructions they ran puts a tight `djnz` loop above the routine that is actually
eating the frame.

Attribution, in order of preference:
  * a symbol from the toolchain's `.map`, when one is loaded;
  * otherwise an aligned block of the address space, named by its range -- because
    "0x2044C0..0x2044FF took 31% of the frame" is still an answer, and refusing to
    say anything without symbols would make the tool useless on every commercial
    cartridge, which is most of the corpus.

The frame budget (515 cycles x 199 lines) turns the numbers into something you can
act on: not "412 000 cycles" but "four frames' worth", and per function, "18% of a
frame". That is the figure that says whether a routine can stay where it is.
"""

from __future__ import annotations

from dataclasses import dataclass

CYCLES_PER_FRAME = 515 * 199        # 102 485 -- mirrors cpp/src/machine.hpp

# Where a PC lives. A profile that says "40% of this frame is inside the BIOS" has
# told you something no function list can.
REGIONS = (
    (0x000000, 0x0000FF, "I/O page"),
    (0x004000, 0x006FFF, "work RAM"),
    (0x007000, 0x007FFF, "shared Z80 RAM"),
    (0x008000, 0x00BFFF, "video RAM"),
    (0x200000, 0x3FFFFF, "cartridge"),
    (0x800000, 0x9FFFFF, "cartridge (2nd chip)"),
    (0xFF0000, 0xFFFFFF, "BIOS"),
)

DEFAULT_BLOCK = 256


def region_of(addr: int) -> str:
    for lo, hi, name in REGIONS:
        if lo <= addr <= hi:
            return name
    return "unmapped"


@dataclass
class Bucket:
    name: str
    lo: int                  # lowest PC seen in this bucket
    hi: int                  # highest PC seen
    instructions: int = 0
    cycles: int = 0
    reads: int = 0
    writes: int = 0

    @property
    def region(self) -> str:
        return region_of(self.lo)

    @property
    def cycles_per_instruction(self) -> float:
        return self.cycles / self.instructions if self.instructions else 0.0


@dataclass
class Report:
    buckets: list[Bucket]
    total_instructions: int
    total_cycles: int
    resolved: int            # instructions attributed to a named symbol
    by_region: dict[str, int]        # region -> cycles
    symbols_used: bool

    @property
    def frames(self) -> float:
        return self.total_cycles / CYCLES_PER_FRAME

    def share(self, bucket: Bucket) -> float:
        return (100.0 * bucket.cycles / self.total_cycles) if self.total_cycles else 0.0

    def per_frame(self, bucket: Bucket) -> float:
        """The bucket's cost in cycles PER FRAME, out of a budget of 102 485.

        ⚠️ Not a second percentage. A bucket's share of one frame is arithmetically
        the SAME number as its share of the capture (the capture's cycles ARE its
        frames times the budget), so showing both would be showing one number twice
        and inviting you to compare it with itself. What the percentage cannot say
        is the absolute cost, and that is the figure that decides whether a routine
        can stay where it is: 18 400 cycles a frame out of 102 485 is a fact about
        the console, not about how long you happened to record.
        """
        if not self.total_cycles:
            return 0.0
        return bucket.cycles / self.frames


def _block_name(addr: int, block: int) -> str:
    lo = addr - (addr % block)
    return f"{lo:06X}..{lo + block - 1:06X}"


def profile(records, symbols=None, *, block: int = DEFAULT_BLOCK) -> Report:
    """Bucket an instruction trace by symbol (or by address block without one).

    `records` are `core.native.Record` values -- anything with `pc`, `cycles`,
    `n_reads` and `n_writes` will do, which is what keeps this testable.
    """
    buckets: dict[str, Bucket] = {}
    by_region: dict[str, int] = {}
    total_instr = total_cycles = resolved = 0
    used_symbols = False

    for rec in records:
        pc = int(rec.pc) & 0xFFFFFF
        cycles = int(getattr(rec, "cycles", 0))
        sym = symbols.lookup_address(pc) if symbols is not None else None
        if sym is not None:
            key = sym.name
            resolved += 1
            used_symbols = True
        else:
            key = _block_name(pc, block)

        b = buckets.get(key)
        if b is None:
            b = buckets[key] = Bucket(key, pc, pc)
        else:
            b.lo = min(b.lo, pc)
            b.hi = max(b.hi, pc)
        b.instructions += 1
        b.cycles += cycles
        b.reads += int(getattr(rec, "n_reads", 0))
        b.writes += int(getattr(rec, "n_writes", 0))

        total_instr += 1
        total_cycles += cycles
        region = region_of(pc)
        by_region[region] = by_region.get(region, 0) + cycles

    ordered = sorted(buckets.values(),
                     key=lambda x: (x.cycles, x.instructions), reverse=True)
    return Report(ordered, total_instr, total_cycles, resolved, by_region,
                  used_symbols)


def format_report(report: Report, top: int = 40) -> str:
    lines = [
        f"{report.total_instructions:,} instructions, {report.total_cycles:,} cycles "
        f"= {report.frames:.2f} frames",
    ]
    if not report.symbols_used:
        lines.append("no symbols loaded — buckets are address blocks, not functions")
    elif report.resolved < report.total_instructions:
        missing = report.total_instructions - report.resolved
        lines.append(f"{missing:,} instructions fell outside every known symbol "
                     f"(BIOS, library or data-driven code)")
    lines.append("")
    lines.append("by region")
    for name, cycles in sorted(report.by_region.items(), key=lambda kv: -kv[1]):
        pct = 100.0 * cycles / report.total_cycles if report.total_cycles else 0
        lines.append(f"  {name:<22} {cycles:>12,}  {pct:5.1f}%")
    lines.append("")
    lines.append(f"{'where':<28} {'cycles':>12} {'share':>7} {'cyc/frame':>10} "
                 f"{'instr':>10} {'c/i':>6}  reads/writes")
    for b in report.buckets[:top]:
        lines.append(f"  {b.name:<26} {b.cycles:>12,} {report.share(b):6.1f}% "
                     f"{report.per_frame(b):10,.0f} {b.instructions:>10,} "
                     f"{b.cycles_per_instruction:6.1f}  {b.reads}/{b.writes}")
    if len(report.buckets) > top:
        lines.append(f"  … and {len(report.buckets) - top} more")
    return "\n".join(lines)
