"""What the sound CPU is doing: its address space, its registers, and why it stopped.

The Z80 is a whole second processor and it had one line of text in this debugger.
Two audio bugs -- a tempo doubled by a timer output pin, a Z80 running five times
too fast -- were found by reasoning about counters, because nobody could read what
the driver was executing.

Two things live here, both pure (a `read(addr, n) -> bytes` on the MAIN bus, no Qt,
no core):

  * the **address map**. The Z80 does not see the main CPU's memory. It sees its own
    4 KiB at 0x0000 (which is the shared RAM the two processors talk through, at
    0x7000 on the other side), a write-only sound chip at 0x4000, the mailbox at
    0x8000 and an interrupt-request register at 0xC000. Reading Z80 addresses as
    main-bus addresses gives you the video registers -- plausible, and wrong.

  * the **stop reason**. `trapped` on its own says "something", which is the least
    useful true statement available. With the trap PC and opcode it becomes "our
    core does not implement THIS instruction", and the disassembler names it.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import z80dasm
from core.hwregs import (SHARED_RAM, SHARED_RAM_SIZE, T6W28_RESET, Z80_COMM,
                         Z80_NMI, Z80_RESET)

# The Z80's view, from cpp/src/z80.cpp z80_read / z80_write. MEASURED, not assumed:
# the CPU was asked which 4 KiB pages it writes and answered four distinct regions,
# not one mirrored one.
#
# The ADDRESSES come from `core/hwregs.py`, which is the one place they are written
# down. Two copies of a hardware fact drift, and the drift is invisible until the
# day one of them is wrong.
SHARED_RAM_MAIN = SHARED_RAM
COMM_REGISTER_MAIN = Z80_COMM

REGIONS = (
    (0x0000, 0x3FFF, "work RAM", "4 KiB, mirrored 4x — shared with the main CPU at 0x7000"),
    (0x4000, 0x7FFF, "T6W28", "the sound chip: WRITE-ONLY, reads give 0xFF"),
    (0x8000, 0xBFFF, "comm", "the mailbox both processors read and write"),
    (0xC000, 0xFFFF, "INT to main CPU", "write-only: a write raises INT5 on the main CPU"),
)


def region_of(addr: int) -> tuple[str, str]:
    a = addr & 0xFFFF
    for lo, hi, name, note in REGIONS:
        if lo <= a <= hi:
            return name, note
    return "?", ""


def make_reader(read_main):
    """A `read(addr) -> int` over the Z80's address space, built on the main bus.

    Mirrors `z80_read` exactly, including the two regions that do not read back
    what was written: the sound chip is write-only (0xFF) and everything from
    0x8000 up reads the comm latch.
    """
    def read(addr: int) -> int:
        a = addr & 0xFFFF
        try:
            if a < 0x4000:
                return read_main(SHARED_RAM_MAIN + (a % SHARED_RAM_SIZE), 1)[0]
            if a < 0x8000:
                return 0xFF
            return read_main(COMM_REGISTER_MAIN, 1)[0]
        except Exception:
            return 0xFF
    return read


FLAG_BITS = (("S", 7), ("Z", 6), ("5", 5), ("H", 4), ("3", 3), ("P/V", 2), ("N", 1), ("C", 0))


def flags_text(f: int) -> str:
    """The documented flags, spelled out. Bits 5 and 3 are undocumented copies and
    are shown as their numbers rather than given names they do not have."""
    return "".join(n for n, bit in FLAG_BITS if (f >> bit) & 1) or "-"


@dataclass(frozen=True)
class StopReason:
    stopped: bool
    title: str
    detail: str


def stop_reason(aux, read_main) -> StopReason:
    """Why the sound CPU is not running, in words that name the next action.

    `halted` and `trapped` are NOT the same thing and must never be shown as one:
    a halt is the driver waiting for an interrupt, which is normal and is where it
    spends most of its life. A trap is our core refusing an opcode, which is a
    hole in the emulator with an address on it.
    """
    if getattr(aux, "z80_trapped", 0):
        pc = aux.z80_trap_pc
        prefix, op = aux.z80_trap_prefix, aux.z80_trap_opcode
        raw = (f"{prefix:02X} {op:02X}" if prefix else f"{op:02X}")
        try:
            insn = z80dasm.disassemble_at(make_reader(read_main), pc)
            what = insn.text
        except Exception:
            what = "?"
        return StopReason(
            True, f"TRAPPED at 0x{pc:04X} on opcode {raw}",
            f"That instruction is `{what}` — our core does not implement it. This is a "
            f"hole in the emulator, not in the game.")
    if not getattr(aux, "z80_running", 0):
        return StopReason(
            True, "held in reset",
            f"Software releases it by writing 0x55 to 0x{Z80_RESET:04X} (and the sound "
            f"chip itself at 0x{T6W28_RESET:04X}). Until then the console is silent by "
            f"design, not by fault.")
    if getattr(aux, "z80_halted", 0):
        return StopReason(
            False, "halted — waiting for an interrupt",
            "Normal: the driver sleeps between timer ticks. It wakes on the timer-3 "
            f"output or on an NMI (a write to 0x{Z80_NMI:04X}).")
    return StopReason(False, "running", "")


@dataclass(frozen=True)
class RegisterView:
    pairs: tuple[tuple[str, str], ...]      # (label, value) for the main set
    shadow: tuple[tuple[str, str], ...]
    control: tuple[tuple[str, str], ...]


def registers(aux) -> RegisterView:
    def pair(hi, lo):
        return f"{hi:02X}{lo:02X}"

    main = (
        ("AF", f"{aux.z80_a:02X}{aux.z80_f:02X}  [{flags_text(aux.z80_f)}]"),
        ("BC", pair(aux.z80_b, aux.z80_c)),
        ("DE", pair(aux.z80_d, aux.z80_e)),
        ("HL", pair(aux.z80_h, aux.z80_l)),
        ("IX", f"{aux.z80_ix:04X}"),
        ("IY", f"{aux.z80_iy:04X}"),
        ("SP", f"{aux.z80_sp:04X}"),
        ("PC", f"{aux.z80_pc:04X}"),
    )
    shadow = (
        ("AF'", pair(aux.z80_a2, aux.z80_f2)),
        ("BC'", pair(aux.z80_b2, aux.z80_c2)),
        ("DE'", pair(aux.z80_d2, aux.z80_e2)),
        ("HL'", pair(aux.z80_h2, aux.z80_l2)),
    )
    control = (
        ("I", f"{aux.z80_i:02X}"),
        ("R", f"{aux.z80_r:02X}"),
        ("IM", str(aux.z80_im)),
        ("IFF1/2", f"{aux.z80_iff1}/{aux.z80_iff2}"),
        ("NMI pending", "yes" if aux.z80_nmi_pending else "no"),
        ("INT pending", "yes" if aux.z80_int_pending else "no"),
        # SIGNED on purpose: an instruction that overruns its budget BORROWS from
        # the next tick. An unsigned counter threw the overrun away and made the
        # Z80 run five times too fast (pass 229).
        ("cycle credit", str(aux.z80_cycle_credit)),
        ("executed", f"{aux.z80_executed:,}"),
    )
    return RegisterView(main, shadow, control)


def stack(read_main, sp: int, depth: int = 8) -> list[tuple[int, int]]:
    """(address, word) pairs from SP upward -- the return addresses, most recent
    first. Words are little-endian, like every push this CPU makes."""
    read = make_reader(read_main)
    out = []
    for i in range(depth):
        a = (sp + i * 2) & 0xFFFF
        out.append((a, read(a) | (read((a + 1) & 0xFFFF) << 8)))
    return out


def format_report(aux, read_main, *, around: int = 24) -> str:
    """The whole view as text, for the Export button and for scripts."""
    regs = registers(aux)
    why = stop_reason(aux, read_main)
    lines = [f"Z80 sound CPU — {why.title}"]
    if why.detail:
        lines.append(f"  {why.detail}")
    lines.append("")
    lines.append("  " + "   ".join(f"{k} {v}" for k, v in regs.pairs))
    lines.append("  " + "   ".join(f"{k} {v}" for k, v in regs.shadow))
    for k, v in regs.control:
        lines.append(f"  {k:<14} {v}")
    lines.append("")
    lines.append("stack")
    for addr, word in stack(read_main, aux.z80_sp):
        lines.append(f"  {addr:04X}  {word:04X}")
    lines.append("")
    lines.append("disassembly")
    read = make_reader(read_main)
    for insn in z80dasm.disassemble(read, aux.z80_pc, around):
        mark = ">" if insn.addr == aux.z80_pc else " "
        lines.append(f" {mark} {insn.addr:04X}  {insn.hex:<12} {insn.text}")
    return "\n".join(lines)
