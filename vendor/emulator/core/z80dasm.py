"""A Z80 disassembler, for the sound CPU.

This project had none. The main CPU has a full TLCS-900 decoder and a listing that
follows PC; the second processor -- the one that runs every note the console plays --
had one line of text saying `pc=1A2F halt`. Two audio bugs (a tempo doubled by a
timer output pin, a Z80 running five times too fast) were found by reasoning about
counters, because nobody could read what the driver was executing.

Decoding is the standard opcode decomposition (`xx yyy zzz`, with `p = y >> 1` and
`q = y & 1`), which covers the whole instruction set -- unprefixed, CB, ED, DD/FD
and the DD CB d op form -- in a table rather than 1500 cases. It describes the
ARCHITECTURE, not our core: an opcode our Z80 does not implement still disassembles
correctly here, because "what is this byte" and "do we run it" are different
questions, and answering the first with the second is how you end up debugging a
listing that agrees with your bug.

Text style follows the TLCS-900 listing next door: lowercase mnemonics, `0x` hex.
"""

from __future__ import annotations

from dataclasses import dataclass

R = ("b", "c", "d", "e", "h", "l", "(hl)", "a")
RP = ("bc", "de", "hl", "sp")
RP2 = ("bc", "de", "hl", "af")
CC = ("nz", "z", "nc", "c", "po", "pe", "p", "m")
ALU = ("add a,", "adc a,", "sub ", "sbc a,", "and ", "xor ", "or ", "cp ")
ROT = ("rlc", "rrc", "rl", "rr", "sla", "sra", "sll", "srl")
IM = ("0", "0", "1", "2", "0", "0", "1", "2")
BLI = (
    ("ldi", "cpi", "ini", "outi"),
    ("ldd", "cpd", "ind", "outd"),
    ("ldir", "cpir", "inir", "otir"),
    ("lddr", "cpdr", "indr", "otdr"),
)
ACC_ROT = ("rlca", "rrca", "rla", "rra", "daa", "cpl", "scf", "ccf")

# Instructions that transfer control, so a listing can show where to and a caller
# can follow them. `call`/`rst` push a return address; `jp`/`jr`/`djnz` do not.
_CALLS = ("call", "rst")
_JUMPS = ("jp", "jr", "djnz")


@dataclass(frozen=True)
class Insn:
    addr: int
    length: int
    text: str
    raw: bytes
    target: int | None = None     # absolute destination, for jumps and calls
    is_call: bool = False
    is_jump: bool = False
    is_return: bool = False

    @property
    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.raw)


def _u8(v: int) -> str:
    return f"0x{v & 0xFF:02X}"


def _u16(v: int) -> str:
    return f"0x{v & 0xFFFF:04X}"


def _d(v: int) -> str:
    """A signed displacement, written the way you would type it back in."""
    s = v - 256 if v > 127 else v
    return f"+{s}" if s >= 0 else f"-{-s}"


class _Stream:
    def __init__(self, read, addr: int):
        self._read = read
        self.start = addr & 0xFFFF
        self.raw = bytearray()

    def byte(self) -> int:
        at = (self.start + len(self.raw)) & 0xFFFF
        try:
            b = self._read(at) & 0xFF
        except Exception:
            b = 0xFF
        self.raw.append(b)
        return b

    def word(self) -> int:
        lo = self.byte()
        return lo | (self.byte() << 8)


def disassemble_at(read, addr: int) -> Insn:
    """One instruction at `addr`. `read(addr) -> int` sees the Z80's address space.

    Never raises and never returns zero length: an unreadable or meaningless byte
    still consumes itself and prints as data, so a listing resyncs and carries on
    instead of stopping dead and hiding the rest of the routine.
    """
    s = _Stream(read, addr)
    text, target, kind = _decode(s, "hl")
    return Insn(s.start, len(s.raw), text, bytes(s.raw), target,
                is_call=kind == "call", is_jump=kind == "jump",
                is_return=kind == "ret")


def _idx_r(index: str, i: int, displaced: bool) -> str:
    """`r[i]` under a DD/FD prefix.

    H and L become IXh/IXl -- EXCEPT in an instruction that already carries a
    displacement, where they stay the plain registers. Getting this backwards
    prints `ld ixh,(ix+3)`, an instruction that does not exist.
    """
    if i == 6:
        return None            # the caller substitutes (IX+d)
    if i in (4, 5) and not displaced:
        return index + ("h" if i == 4 else "l")
    return R[i]


def _decode(s: _Stream, hl: str) -> tuple[str, "int | None", str]:
    op = s.byte()

    if op in (0xDD, 0xFD) and hl == "hl":
        return _decode(s, "ix" if op == 0xDD else "iy")
    if op == 0xCB:
        return _decode_cb(s, hl)
    if op == 0xED:
        return _decode_ed(s)

    x, y, z = op >> 6, (op >> 3) & 7, op & 7
    p, q = y >> 1, y & 1
    indexed = hl != "hl"

    def mem(i: int) -> str:
        """r[i], turning (HL) into (IX+d) and eating the displacement byte."""
        if i == 6 and indexed:
            return f"({hl}{_d(s.byte())})"
        if i == 6:
            return "(hl)"
        return _idx_r(hl, i, False) if indexed else R[i]

    if x == 0:
        if z == 0:
            if y == 0:
                return "nop", None, ""
            if y == 1:
                return "ex af,af'", None, ""
            d = s.byte()
            dest = (s.start + len(s.raw) + (d - 256 if d > 127 else d)) & 0xFFFF
            if y == 2:
                return f"djnz {_u16(dest)}", dest, "jump"
            if y == 3:
                return f"jr {_u16(dest)}", dest, "jump"
            return f"jr {CC[y - 4]},{_u16(dest)}", dest, "jump"
        if z == 1:
            rp = hl if p == 2 else RP[p]
            if q == 0:
                return f"ld {rp},{_u16(s.word())}", None, ""
            return f"add {hl},{rp}", None, ""
        if z == 2:
            if q == 0:
                if p == 0:
                    return "ld (bc),a", None, ""
                if p == 1:
                    return "ld (de),a", None, ""
                if p == 2:
                    return f"ld ({_u16(s.word())}),{hl}", None, ""
                return f"ld ({_u16(s.word())}),a", None, ""
            if p == 0:
                return "ld a,(bc)", None, ""
            if p == 1:
                return "ld a,(de)", None, ""
            if p == 2:
                return f"ld {hl},({_u16(s.word())})", None, ""
            return f"ld a,({_u16(s.word())})", None, ""
        if z == 3:
            rp = hl if p == 2 else RP[p]
            return (f"{'inc' if q == 0 else 'dec'} {rp}", None, "")
        if z in (4, 5):
            return f"{'inc' if z == 4 else 'dec'} {mem(y)}", None, ""
        if z == 6:
            dst = mem(y)                      # the displacement comes BEFORE the value
            return f"ld {dst},{_u8(s.byte())}", None, ""
        return ACC_ROT[y], None, ""

    if x == 1:
        if y == 6 and z == 6:
            return "halt", None, ""
        # In a displaced LD the OTHER operand keeps its plain register name.
        if indexed and (y == 6 or z == 6):
            disp = f"({hl}{_d(s.byte())})"
            src = disp if z == 6 else R[z]
            dst = disp if y == 6 else R[y]
            return f"ld {dst},{src}", None, ""
        return f"ld {mem(y)},{mem(z)}", None, ""

    if x == 2:
        return f"{ALU[y]}{mem(z)}", None, ""

    # x == 3
    if z == 0:
        return f"ret {CC[y]}", None, "ret"
    if z == 1:
        if q == 0:
            return f"pop {hl if p == 2 else RP2[p]}", None, ""
        if p == 0:
            return "ret", None, "ret"
        if p == 1:
            return "exx", None, ""
        if p == 2:
            return f"jp ({hl})", None, "jump"
        return f"ld sp,{hl}", None, ""
    if z == 2:
        nn = s.word()
        return f"jp {CC[y]},{_u16(nn)}", nn, "jump"
    if z == 3:
        if y == 0:
            nn = s.word()
            return f"jp {_u16(nn)}", nn, "jump"
        if y == 2:
            return f"out ({_u8(s.byte())}),a", None, ""
        if y == 3:
            return f"in a,({_u8(s.byte())})", None, ""
        if y == 4:
            return f"ex (sp),{hl}", None, ""
        if y == 5:
            return "ex de,hl", None, ""
        return ("di" if y == 6 else "ei"), None, ""
    if z == 4:
        nn = s.word()
        return f"call {CC[y]},{_u16(nn)}", nn, "call"
    if z == 5:
        if q == 0:
            return f"push {hl if p == 2 else RP2[p]}", None, ""
        nn = s.word()
        return f"call {_u16(nn)}", nn, "call"
    if z == 6:
        return f"{ALU[y]}{_u8(s.byte())}", None, ""
    return f"rst {_u8(y * 8)}", y * 8, "call"


def _decode_cb(s: _Stream, hl: str) -> tuple[str, None, str]:
    indexed = hl != "hl"
    disp = _d(s.byte()) if indexed else ""     # DD CB d op -- displacement FIRST
    op = s.byte()
    x, y, z = op >> 6, (op >> 3) & 7, op & 7
    operand = f"({hl}{disp})" if indexed else R[z]
    # An indexed CB also writes the result back into r[z] unless z is 6. That
    # undocumented copy is real hardware behaviour and the listing says so rather
    # than pretending the byte does nothing.
    tail = "" if (not indexed or z == 6) else f",{R[z]}"
    if x == 0:
        return f"{ROT[y]} {operand}{tail}", None, ""
    name = ("bit", "res", "set")[x - 1]
    if x == 1:
        return f"bit {y},{operand}", None, ""
    return f"{name} {y},{operand}{tail}", None, ""


def _decode_ed(s: _Stream) -> tuple[str, "int | None", str]:
    op = s.byte()
    x, y, z = op >> 6, (op >> 3) & 7, op & 7
    p, q = y >> 1, y & 1
    if x == 1:
        if z == 0:
            return (f"in {R[y]},(c)" if y != 6 else "in (c)"), None, ""
        if z == 1:
            return (f"out (c),{R[y]}" if y != 6 else "out (c),0"), None, ""
        if z == 2:
            return f"{'sbc' if q == 0 else 'adc'} hl,{RP[p]}", None, ""
        if z == 3:
            nn = s.word()
            return ((f"ld ({_u16(nn)}),{RP[p]}" if q == 0
                     else f"ld {RP[p]},({_u16(nn)})"), None, "")
        if z == 4:
            return "neg", None, ""
        if z == 5:
            return ("reti" if y == 1 else "retn"), None, "ret"
        if z == 6:
            return f"im {IM[y]}", None, ""
        return (("ld i,a", "ld r,a", "ld a,i", "ld a,r",
                 "rrd", "rld", "nop", "nop")[y], None, "")
    if x == 2 and z <= 3 and y >= 4:
        return BLI[y - 4][z], None, ""
    # Everything else in the ED page is undefined: on hardware it behaves as two
    # NOPs. Naming it as data is honest; inventing a mnemonic would not be.
    return f"db 0xED,{_u8(op)}", None, ""


def disassemble(read, addr: int, count: int) -> list[Insn]:
    """`count` instructions from `addr`, following the bytes."""
    out: list[Insn] = []
    pc = addr & 0xFFFF
    for _ in range(count):
        insn = disassemble_at(read, pc)
        out.append(insn)
        pc = (pc + insn.length) & 0xFFFF
    return out
