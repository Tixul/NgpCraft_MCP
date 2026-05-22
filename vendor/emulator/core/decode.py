"""Minimal instruction decode helpers for NgpCraft Emulator."""

from __future__ import annotations

from dataclasses import dataclass

from core.fetch import NgpcFetchView
from core.memory import NgpcReadBus


R8 = ("W", "A", "B", "C", "D", "E", "H", "L")
R16 = ("WA", "BC", "DE", "HL", "IX", "IY", "IZ", "SP")
R32 = ("XWA", "XBC", "XDE", "XHL", "XIX", "XIY", "XIZ", "XSP")
CC = (
    "F",
    "LT",
    "LE",
    "ULE",
    "OV",
    "MI",
    "Z",
    "C",
    "T",
    "GE",
    "GT",
    "UGT",
    "NOV",
    "PL",
    "NZ",
    "NC",
)

CPU_IO_NAMES = {
    0x20: "HW_TRUN",
    0x22: "HW_TREG0",
    0x23: "HW_TREG1",
    0x24: "HW_T01MOD",
    0x25: "HW_TFFCR",
    0x26: "HW_TREG2",
    0x27: "HW_TREG3",
    0x28: "HW_T23MOD",
    0x6B: "HW_WATCHDOG_ALT",
    0x6F: "HW_WATCHDOG",
    0x73: "HW_ROM_BANK",
    0x7C: "HW_DMA0V",
    0x7D: "HW_DMA1V",
    0x7E: "HW_DMA2V",
    0x7F: "HW_DMA3V",
}


@dataclass(frozen=True)
class DecodeResult:
    """Result of a minimal instruction decode attempt."""

    pc: int
    status: str
    raw_bytes: bytes | None
    length: int | None
    mnemonic: str | None
    operands: str | None
    assembly: str | None
    next_sequential_pc: int | None
    control_flow_kind: str | None
    direct_target: int | None
    falls_through: bool | None
    warning: str | None
    note: str


@dataclass(frozen=True)
class _DecodedInstruction:
    """Internally decoded instruction payload."""

    length: int
    mnemonic: str
    operands: str
    control_flow_kind: str | None = None
    direct_target: int | None = None
    falls_through: bool = True
    warning: str | None = None

    @property
    def assembly(self) -> str:
        if not self.operands:
            return self.mnemonic
        return f"{self.mnemonic} {self.operands}"


def _read_prefix(bus: NgpcReadBus, address: int, size: int) -> tuple[bytes, str | None]:
    """Read up to one instruction prefix, stopping at the first unavailable byte."""
    data = bytearray()
    for offset in range(size):
        result = bus.read_bytes(address + offset, size=1)
        if result.status != "ok":
            return bytes(data), result.status
        assert result.data is not None
        data.extend(result.data)
    return bytes(data), None


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _s8(value: int) -> int:
    return value - 0x100 if value >= 0x80 else value


def _s16(data: bytes, offset: int) -> int:
    value = _u16(data, offset)
    return value - 0x10000 if value >= 0x8000 else value


def _format_cpu_io(address: int) -> str:
    name = CPU_IO_NAMES.get(address)
    if name is not None:
        return f"({name})"
    return f"(0x{address:02X})"


def _format_conditional_target(prefix: str, target: int) -> str:
    return f"{prefix}0x{target:06X}"


def _format_displacement(offset: int) -> str:
    sign = "+" if offset >= 0 else ""
    return f"{sign}{offset}"


def _merge_warnings(*warnings: str | None) -> str | None:
    parts = [warning for warning in warnings if warning]
    if not parts:
        return None
    return " | ".join(parts)


def _decode_fixed(pc: int, data: bytes) -> _DecodedInstruction | None:
    first = data[0]

    if first == 0x02:
        return _DecodedInstruction(length=1, mnemonic="push", operands="SR")

    if first == 0x03:
        return _DecodedInstruction(length=1, mnemonic="pop", operands="SR")

    if first == 0x05:
        return _DecodedInstruction(
            length=1,
            mnemonic="halt",
            operands="",
            control_flow_kind="halt",
            falls_through=False,
        )

    if first == 0x00:
        return _DecodedInstruction(length=1, mnemonic="nop", operands="")

    if first == 0x06:
        imm8 = data[1]
        if imm8 == 0x07:
            return _DecodedInstruction(length=2, mnemonic="di", operands="")
        return _DecodedInstruction(length=2, mnemonic="ei", operands=str(imm8))

    if first == 0x07:
        return _DecodedInstruction(
            length=1,
            mnemonic="reti",
            operands="",
            control_flow_kind="return",
            falls_through=False,
        )

    if first == 0x08:
        address = data[1]
        imm8 = data[2]
        return _DecodedInstruction(
            length=3,
            mnemonic="ldb",
            operands=f"{_format_cpu_io(address)}, 0x{imm8:02X}",
        )

    if first == 0x09:
        return _DecodedInstruction(length=2, mnemonic="push", operands=f"0x{data[1]:02X}")

    if first == 0x0A:
        address = data[1]
        imm16 = _u16(data, 2)
        return _DecodedInstruction(
            length=4,
            mnemonic="ldw",
            operands=f"{_format_cpu_io(address)}, 0x{imm16:04X}",
        )

    if first == 0x0B:
        imm16 = _u16(data, 1)
        return _DecodedInstruction(length=3, mnemonic="pushw", operands=f"0x{imm16:04X}")

    if first == 0x0C:
        return _DecodedInstruction(length=1, mnemonic="incf", operands="")

    if first == 0x0D:
        return _DecodedInstruction(length=1, mnemonic="decf", operands="")

    if first == 0x0E:
        return _DecodedInstruction(
            length=1,
            mnemonic="ret",
            operands="",
            control_flow_kind="return",
            falls_through=False,
        )

    if first == 0x0F:
        return _DecodedInstruction(
            length=3,
            mnemonic="retd",
            operands=str(_s16(data, 1)),
            control_flow_kind="return",
            falls_through=False,
        )

    if first == 0x10:
        return _DecodedInstruction(length=1, mnemonic="rcf", operands="")

    if first == 0x11:
        return _DecodedInstruction(length=1, mnemonic="scf", operands="")

    if first == 0x12:
        return _DecodedInstruction(length=1, mnemonic="ccf", operands="")

    if first == 0x13:
        return _DecodedInstruction(length=1, mnemonic="zcf", operands="")

    if first == 0x14:
        return _DecodedInstruction(length=1, mnemonic="push", operands="A")

    if first == 0x15:
        return _DecodedInstruction(length=1, mnemonic="pop", operands="A")

    if first == 0x16:
        return _DecodedInstruction(length=1, mnemonic="ex", operands="F,F'")

    if first == 0x17:
        return _DecodedInstruction(length=2, mnemonic="ldf", operands=str(data[1] & 0x07))

    if first == 0x18:
        return _DecodedInstruction(length=1, mnemonic="push", operands="F")

    if first == 0x19:
        return _DecodedInstruction(length=1, mnemonic="pop", operands="F")

    if first == 0x1A:
        target = _u16(data, 1)
        return _DecodedInstruction(
            length=3,
            mnemonic="jp",
            operands=f"0x{target:04X}",
            control_flow_kind="jump",
            direct_target=target,
            falls_through=False,
        )

    if first == 0x1B:
        target = data[1] | (data[2] << 8) | (data[3] << 16)
        return _DecodedInstruction(
            length=4,
            mnemonic="jp",
            operands=f"0x{target:06X}",
            control_flow_kind="jump",
            direct_target=target,
            falls_through=False,
        )

    if first == 0x1C:
        target = _u16(data, 1)
        return _DecodedInstruction(
            length=3,
            mnemonic="call",
            operands=f"0x{target:04X}",
            control_flow_kind="call",
            direct_target=target,
            falls_through=True,
        )

    if first == 0x1D:
        target = data[1] | (data[2] << 8) | (data[3] << 16)
        return _DecodedInstruction(
            length=4,
            mnemonic="call",
            operands=f"0x{target:06X}",
            control_flow_kind="call",
            direct_target=target,
            falls_through=True,
        )

    if first == 0x1E:
        target = (pc + 3 + _s16(data, 1)) & 0xFFFFFF
        return _DecodedInstruction(
            length=3,
            mnemonic="calr",
            operands=f"0x{target:06X}",
            control_flow_kind="call",
            direct_target=target,
            falls_through=True,
        )

    if 0xF8 <= first <= 0xFF:
        return _DecodedInstruction(
            length=1,
            mnemonic="swi",
            operands=str(first & 0x07),
            control_flow_kind="interrupt",
            falls_through=False,
        )

    return None


def _required_fixed_size(first_opcode: int) -> int | None:
    if first_opcode in (
        0x00,
        0x02,
        0x03,
        0x05,
        0x07,
        0x0C,
        0x0D,
        0x0E,
        0x10,
        0x11,
        0x12,
        0x13,
        0x14,
        0x15,
        0x16,
        0x18,
        0x19,
    ):
        return 1
    if first_opcode == 0x06:
        return 2
    if first_opcode == 0x08:
        return 3
    if first_opcode == 0x09:
        return 2
    if first_opcode == 0x0A:
        return 4
    if first_opcode == 0x0B:
        return 3
    if first_opcode == 0x0F:
        return 3
    if first_opcode == 0x17:
        return 2
    if first_opcode in (0x1A, 0x1C, 0x1E):
        return 3
    if first_opcode in (0x1B, 0x1D):
        return 4
    if 0xF8 <= first_opcode <= 0xFF:
        return 1
    return None


def _decode_xx(pc: int, data: bytes) -> _DecodedInstruction | None:
    first = data[0]
    group = first & 0xF8
    reg = first & 0x07

    if group == 0x20:
        return _DecodedInstruction(length=2, mnemonic="ld", operands=f"{R8[reg]}, 0x{data[1]:02X}")

    if group == 0x28:
        return _DecodedInstruction(length=1, mnemonic="push", operands=R16[reg])

    if group == 0x30:
        imm16 = _u16(data, 1)
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R16[reg]}, 0x{imm16:04X}")

    if group == 0x38:
        return _DecodedInstruction(length=1, mnemonic="push", operands=R32[reg])

    if group == 0x40:
        imm32 = _u32(data, 1)
        return _DecodedInstruction(length=5, mnemonic="ld", operands=f"{R32[reg]}, 0x{imm32:08X}")

    if group == 0x48:
        return _DecodedInstruction(length=1, mnemonic="pop", operands=R16[reg])

    if group == 0x58:
        return _DecodedInstruction(length=1, mnemonic="pop", operands=R32[reg])

    if 0x70 <= first <= 0x7F:
        cc_idx = first & 0x0F
        disp16 = _s16(data, 1)
        target = (pc + 3 + disp16) & 0xFFFFFF
        cond = "" if cc_idx == 8 else f"{CC[cc_idx]}, "
        return _DecodedInstruction(
            length=3,
            mnemonic="jrl",
            operands=_format_conditional_target(cond, target),
            control_flow_kind="jump" if cc_idx == 8 else "conditional-branch",
            direct_target=target,
            falls_through=cc_idx != 8,
        )

    if 0x60 <= first <= 0x6F:
        cc_idx = first & 0x0F
        disp8 = _s8(data[1])
        target = (pc + 2 + disp8) & 0xFFFFFF
        cond = "" if cc_idx == 8 else f"{CC[cc_idx]}, "
        return _DecodedInstruction(
            length=2,
            mnemonic="jr",
            operands=_format_conditional_target(cond, target),
            control_flow_kind="jump" if cc_idx == 8 else "conditional-branch",
            direct_target=target,
            falls_through=cc_idx != 8,
        )

    return None


def _required_xx_size(first_opcode: int) -> int | None:
    group = first_opcode & 0xF8
    if group == 0x20:
        return 2
    if group in (0x28, 0x38, 0x48, 0x58):
        return 1
    if group == 0x30:
        return 3
    if group == 0x40:
        return 5
    if 0x70 <= first_opcode <= 0x7F:
        return 3
    if 0x60 <= first_opcode <= 0x6F:
        return 2
    return None


def _prefixed_register_info(first_opcode: int) -> tuple[str, str, str | None] | None:
    if 0xC8 <= first_opcode <= 0xCF:
        return ("byte", R8[first_opcode & 0x07], None)
    if 0xD0 <= first_opcode <= 0xD7:
        return (
            "word",
            R16[first_opcode & 0x07],
            "!BROKEN D0..D7 ALU word-register prefix on NGPC silicon",
        )
    if 0xD8 <= first_opcode <= 0xDF:
        return ("long", R32[first_opcode & 0x07], None)
    if 0xE8 <= first_opcode <= 0xEF:
        return ("long", R32[first_opcode & 0x07], None)
    return None


def _imm_size(size_kind: str) -> int:
    return {"byte": 1, "word": 2, "long": 4}[size_kind]


def _dest_registers(size_kind: str) -> tuple[str, ...]:
    return {"byte": R8, "word": R16, "long": R32}[size_kind]


def _required_prefixed_register_size(first_opcode: int, second_opcode: int) -> int | None:
    info = _prefixed_register_info(first_opcode)
    if info is None:
        return None

    size_kind = info[0]
    if second_opcode in (0x04, 0x05, 0x06, 0x07, 0x10, 0x12, 0x13, 0x0D):
        return 2
    if second_opcode in (0x08, 0x09, 0x0A):
        # multu/muls r, imm: imm is half the register size
        # long prefix → imm16 (4 bytes); word/byte prefix → imm8 (3 bytes)
        return 4 if size_kind == "long" else 3
    if second_opcode == 0x0C:
        return 4
    if second_opcode in (0x1C, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34):
        return 3
    if second_opcode == 0x03:
        return 2 + _imm_size(size_kind)
    if 0x60 <= second_opcode <= 0x6F:
        return 2
    if 0x70 <= second_opcode <= 0x7F:
        return 2  # SCC cc, r — set-on-condition (2 bytes: prefix + sub-op)
    if 0xA8 <= second_opcode <= 0xAF:
        return 2
    if 0xD8 <= second_opcode <= 0xDF:
        return 2

    register_op_bases = (0x80, 0x88, 0x90, 0x98, 0xA0, 0xB0, 0xB8, 0xC0, 0xD0, 0xE0, 0xF0)
    if any(base <= second_opcode < base + 8 for base in register_op_bases):
        return 2

    if 0xC8 <= second_opcode <= 0xCF:
        return 2 + _imm_size(size_kind)

    if 0xE8 <= second_opcode <= 0xEF:
        return 3  # shift/rotate with 4-bit immediate count: [prefix] [op] [count]

    if 0xF8 <= second_opcode <= 0xFF:
        return 2  # shift/rotate by register A (count in A)

    return None


def _decode_prefixed_register(pc: int, data: bytes) -> _DecodedInstruction | None:
    first = data[0]
    second = data[1]

    info = _prefixed_register_info(first)
    if info is None:
        return None

    size_kind, reg, base_warning = info
    warning = base_warning
    dest_registers = _dest_registers(size_kind)

    if second == 0x03:
        if size_kind == "byte":
            imm = data[2]
            imm_text = f"0x{imm:02X}"
        elif size_kind == "word":
            imm = _u16(data, 2)
            imm_text = f"0x{imm:04X}"
        else:
            imm = _u32(data, 2)
            imm_text = f"0x{imm:08X}"
        return _DecodedInstruction(length=len(data), mnemonic="ld", operands=f"{reg}, {imm_text}", warning=warning)

    if second == 0x04:
        return _DecodedInstruction(length=2, mnemonic="push", operands=reg, warning=warning)

    if second == 0x05:
        return _DecodedInstruction(length=2, mnemonic="pop", operands=reg, warning=warning)

    if second == 0x06:
        return _DecodedInstruction(length=2, mnemonic="cpl", operands=reg, warning=warning)

    if second == 0x07:
        return _DecodedInstruction(length=2, mnemonic="neg", operands=reg, warning=warning)

    if second in (0x08, 0x09):
        mnemonic = "multu" if second == 0x08 else "muls"
        if size_kind == "long":
            imm = _u16(data, 2)
            imm_text = f"0x{imm:04X}"
        else:
            imm = data[2]
            imm_text = f"0x{imm:02X}"
        return _DecodedInstruction(length=len(data), mnemonic=mnemonic, operands=f"{reg}, {imm_text}", warning=warning)

    if second == 0x0A:
        if size_kind == "long":
            imm = _u16(data, 2)
            imm_text = f"0x{imm:04X}"
            div_reg = R32[first & 0x07]
        else:
            imm = data[2]
            imm_text = f"0x{imm:02X}"
            div_reg = R16[first & 0x07]
        return _DecodedInstruction(
            length=len(data),
            mnemonic="div",
            operands=f"{div_reg}, {imm_text}",
            warning=warning,
        )

    if second == 0x0C:
        disp16 = _s16(data, 2)
        if reg == "XIY" and disp16 >= 5:
            warning = _merge_warnings(
                warning,
                "!BROKEN LINK XIY, N when N >= 5 on NGPC silicon",
            )
        return _DecodedInstruction(length=4, mnemonic="link", operands=f"{reg}, {disp16}", warning=warning)

    if second == 0x0D:
        return _DecodedInstruction(length=2, mnemonic="unlk", operands=reg, warning=warning)

    if second == 0x10:
        return _DecodedInstruction(length=2, mnemonic="daa", operands=reg, warning=warning)

    if second == 0x12:
        return _DecodedInstruction(length=2, mnemonic="extz", operands=reg, warning=warning)

    if second == 0x13:
        return _DecodedInstruction(length=2, mnemonic="exts", operands=reg, warning=warning)

    if second == 0x1C:
        disp8 = _s8(data[2])
        target = (pc + 3 + disp8) & 0xFFFFFF
        return _DecodedInstruction(
            length=3,
            mnemonic="djnz",
            operands=f"{reg}, 0x{target:06X}",
            control_flow_kind="conditional-branch",
            direct_target=target,
            falls_through=True,
            warning=warning,
        )

    if second == 0x2E:
        return _DecodedInstruction(
            length=3,
            mnemonic="ldc",
            operands=f"CR_0x{data[2]:02X}, {reg}",
            warning=warning,
        )

    if second == 0x2F:
        return _DecodedInstruction(
            length=3,
            mnemonic="ldc",
            operands=f"{reg}, CR_0x{data[2]:02X}",
            warning=warning,
        )

    if 0x30 <= second <= 0x34:
        bit_index = data[2] & 0x0F
        if size_kind == "byte" and bit_index >= 8:
            warning = _merge_warnings(
                warning,
                "!UNDEFINED bit index >= 8 on byte-register bit-manipulation form",
            )
        mnemonic = {
            0x30: "res",
            0x31: "set",
            0x32: "chg",
            0x33: "bit",
            0x34: "tset",
        }[second]
        return _DecodedInstruction(
            length=3,
            mnemonic=mnemonic,
            operands=f"{bit_index}, {reg}",
            warning=warning,
        )

    if 0x60 <= second <= 0x67:
        count = second & 0x07
        if count == 0:
            count = 8
        return _DecodedInstruction(length=2, mnemonic="inc", operands=f"{count}, {reg}", warning=warning)

    if 0x68 <= second <= 0x6F:
        count = second & 0x07
        if count == 0:
            count = 8
        return _DecodedInstruction(length=2, mnemonic="dec", operands=f"{count}, {reg}", warning=warning)

    if 0x70 <= second <= 0x7F:
        # SCC cc, r — set register to 1 if condition cc is true, else 0.
        # The sub-op low nibble carries the CC index (0..15). Register size is
        # determined by the prefix (byte/word/long) — here `reg` already holds
        # the correct textual name.
        cc_idx = second & 0x0F
        return _DecodedInstruction(
            length=2,
            mnemonic="scc",
            operands=f"{CC[cc_idx]}, {reg}",
            warning=warning,
        )

    if 0xA8 <= second <= 0xAF:
        return _DecodedInstruction(length=2, mnemonic="ld", operands=f"{reg}, {second & 0x07}", warning=warning)

    if 0xD8 <= second <= 0xDF:
        return _DecodedInstruction(length=2, mnemonic="cp", operands=f"{reg}, {second & 0x07}", warning=warning)

    shift_imm_ops = {
        0xE8: "rlc", 0xE9: "rrc", 0xEA: "rl", 0xEB: "rr",
        0xEC: "sla", 0xED: "sra", 0xEE: "sll", 0xEF: "srl",
    }
    if second in shift_imm_ops and len(data) == 3:
        count = data[2] & 0x0F  # 4-bit immediate count (#4 in catalog)
        return _DecodedInstruction(
            length=3,
            mnemonic=shift_imm_ops[second],
            operands=f"{count}, {reg}",
            warning=warning,
        )

    shift_reg_ops = {
        0xF8: "rlc", 0xF9: "rrc", 0xFA: "rl", 0xFB: "rr",
        0xFC: "sla", 0xFD: "sra", 0xFE: "sll", 0xFF: "srl",
    }
    if second in shift_reg_ops:
        return _DecodedInstruction(
            length=2,
            mnemonic=shift_reg_ops[second],
            operands=f"A, {reg}",
            warning=warning,
        )

    register_ops = {
        0x80: "add",
        0x88: "ld",
        0x90: "adc",
        0x98: "ld",
        0xA0: "sub",
        0xB0: "sbc",
        0xB8: "ex",
        0xC0: "and",
        0xD0: "xor",
        0xE0: "or",
        0xF0: "cp",
    }
    for base_opcode, mnemonic in register_ops.items():
        if base_opcode <= second < base_opcode + 8:
            other_reg = dest_registers[second & 0x07]
            if first == 0xCA and second == 0x90:
                warning = _merge_warnings(
                    warning,
                    "!RISK adc W, B is known broken on NGPC silicon when W > 0",
                )
            if base_opcode == 0x98:
                operands = f"{reg}, {other_reg}"
            else:
                operands = f"{other_reg}, {reg}"
            return _DecodedInstruction(length=2, mnemonic=mnemonic, operands=operands, warning=warning)

    immediate_ops = {
        0xC8: "add",
        0xC9: "adc",
        0xCA: "sub",
        0xCB: "sbc",
        0xCC: "and",
        0xCD: "xor",
        0xCE: "or",
        0xCF: "cp",
    }
    if second in immediate_ops:
        if size_kind == "byte":
            imm = data[2]
            imm_text = f"0x{imm:02X}"
        elif size_kind == "word":
            imm = _u16(data, 2)
            imm_text = f"0x{imm:04X}"
        else:
            imm = _u32(data, 2)
            imm_text = f"0x{imm:08X}"
        return _DecodedInstruction(
            length=len(data),
            mnemonic=immediate_ops[second],
            operands=f"{reg}, {imm_text}",
            warning=warning,
        )

    return None


def _decode_reg_indirect_load(data: bytes) -> _DecodedInstruction | None:
    """Decode (r32) byte-indirect instructions: [0x80+r] [op] [optional extra].

    First byte 0x80..0x87: r32 index = byte & 7, size = byte (zz=0).
    Sub-op map (source: ngdis/tlcs900_zz_mem.c) :
      op=0x20..0x27 LD  R8, (r32)              — 2 bytes
      op=0x30..0x37 EX  (r32), R8              — 2 bytes (pass 55)
      op=0x38..0x3E (r32), imm8 ADD/ADC/SUB/   — 3 bytes (pass 55)
                    SBC/AND/XOR/OR
      op=0x3F       CP  (r32), imm8            — 3 bytes
      op=0x60..0x67 INC #n, (r32) (n=0→8)      — 2 bytes (pass 56)
      op=0x68..0x6F DEC #n, (r32) (n=0→8)      — 2 bytes (pass 56)
      op=0x78..0x7F shift family on (r32) :    — 2 bytes (pass 56)
                    RLC/RRC/RL/RR/SLA/SRA/SLL/SRL
      op=0x80..0x87 ADD R8, (r32)              — 2 bytes (pass 54)
      op=0x88..0x8F ADD (r32), R8              — 2 bytes (pass 54)
      op=0x90..0x97 ADC R8, (r32)              — 2 bytes (pass 55)
      op=0x98..0x9F ADC (r32), R8              — 2 bytes (pass 55)
      op=0xA0..0xA7 SUB R8, (r32)              — 2 bytes (pass 54)
      op=0xA8..0xAF SUB (r32), R8              — 2 bytes (pass 54)
      op=0xB0..0xB7 SBC R8, (r32)              — 2 bytes (pass 55)
      op=0xB8..0xBF SBC (r32), R8              — 2 bytes (pass 55)
      op=0xC0..0xC7 AND R8, (r32)              — 2 bytes (pass 53)
      op=0xC8..0xCF AND (r32), R8              — 2 bytes (pass 53)
      op=0xD0..0xD7 XOR R8, (r32)              — 2 bytes (pass 53)
      op=0xD8..0xDF XOR (r32), R8              — 2 bytes (pass 53)
      op=0xE0..0xE7 OR  R8, (r32)              — 2 bytes (pass 53)
      op=0xE8..0xEF OR  (r32), R8              — 2 bytes (pass 53)
      op=0xF0..0xF7 CP  R8, (r32)              — 2 bytes (pass 51)
      op=0xF8..0xFF CP  (r32), R8              — 2 bytes (pass 55)
    """
    first = data[0]
    r32_name = f"({R32[first & 0x07]})"
    op = data[1]

    if 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=2, mnemonic="ld", operands=f"{R8[op & 0x07]}, {r32_name}")

    if op == 0x3F and len(data) == 3:
        return _DecodedInstruction(length=3, mnemonic="cp", operands=f"{r32_name}, 0x{data[2]:02X}")

    if 0xF0 <= op <= 0xF7:
        return _DecodedInstruction(length=2, mnemonic="cp", operands=f"{R8[op & 0x07]}, {r32_name}")

    # Pass 53 — logical ALU sub-ops (AND/OR/XOR, both directions).
    # All 2-byte encodings, verified against NgpCraft_Disasm oracle.
    if 0xC0 <= op <= 0xC7:
        return _DecodedInstruction(length=2, mnemonic="and", operands=f"{R8[op & 0x07]}, {r32_name}")
    if 0xC8 <= op <= 0xCF:
        return _DecodedInstruction(length=2, mnemonic="and", operands=f"{r32_name}, {R8[op & 0x07]}")
    if 0xD0 <= op <= 0xD7:
        return _DecodedInstruction(length=2, mnemonic="xor", operands=f"{R8[op & 0x07]}, {r32_name}")
    if 0xD8 <= op <= 0xDF:
        return _DecodedInstruction(length=2, mnemonic="xor", operands=f"{r32_name}, {R8[op & 0x07]}")
    if 0xE0 <= op <= 0xE7:
        return _DecodedInstruction(length=2, mnemonic="or", operands=f"{R8[op & 0x07]}, {r32_name}")
    if 0xE8 <= op <= 0xEF:
        return _DecodedInstruction(length=2, mnemonic="or", operands=f"{r32_name}, {R8[op & 0x07]}")

    # Pass 54 — arithmetic ALU sub-ops (ADD/SUB, both directions).
    # All 2-byte encodings, verified against NgpCraft_Disasm oracle.
    if 0x80 <= op <= 0x87:
        return _DecodedInstruction(length=2, mnemonic="add", operands=f"{R8[op & 0x07]}, {r32_name}")
    if 0x88 <= op <= 0x8F:
        return _DecodedInstruction(length=2, mnemonic="add", operands=f"{r32_name}, {R8[op & 0x07]}")
    if 0xA0 <= op <= 0xA7:
        return _DecodedInstruction(length=2, mnemonic="sub", operands=f"{R8[op & 0x07]}, {r32_name}")
    if 0xA8 <= op <= 0xAF:
        return _DecodedInstruction(length=2, mnemonic="sub", operands=f"{r32_name}, {R8[op & 0x07]}")

    # Pass 55 — ADC/SBC R8 ↔ (R32) (both directions, with carry-in).
    # Verified against NgpCraft_Disasm oracle.
    # Sub-op layout (per ngdis/tlcs900_zz_mem.c) :
    #   0x90..0x97 = ADC R8, (R32)    — R8 ← R8 + mem + C
    #   0x98..0x9F = ADC (R32), R8    — mem ← mem + R8 + C
    #   0xB0..0xB7 = SBC R8, (R32)    — R8 ← R8 - mem - C
    #   0xB8..0xBF = SBC (R32), R8    — mem ← mem - R8 - C
    if 0x90 <= op <= 0x97:
        return _DecodedInstruction(length=2, mnemonic="adc", operands=f"{R8[op & 0x07]}, {r32_name}")
    if 0x98 <= op <= 0x9F:
        return _DecodedInstruction(length=2, mnemonic="adc", operands=f"{r32_name}, {R8[op & 0x07]}")
    if 0xB0 <= op <= 0xB7:
        return _DecodedInstruction(length=2, mnemonic="sbc", operands=f"{R8[op & 0x07]}, {r32_name}")
    if 0xB8 <= op <= 0xBF:
        return _DecodedInstruction(length=2, mnemonic="sbc", operands=f"{r32_name}, {R8[op & 0x07]}")

    # Pass 55 — CP (R32), R8 (compare with operand order reversed vs CP R8,(R32)).
    #   0xF8..0xFF = CP (R32), R8     — flags = mem - R8 ; no write
    if 0xF8 <= op <= 0xFF:
        return _DecodedInstruction(length=2, mnemonic="cp", operands=f"{r32_name}, {R8[op & 0x07]}")

    # Pass 55 — EX (R32), R8 (swap mem byte with R8 ; flags unchanged).
    #   0x30..0x37 = EX (R32), R8
    if 0x30 <= op <= 0x37:
        return _DecodedInstruction(length=2, mnemonic="ex", operands=f"{r32_name}, {R8[op & 0x07]}")

    # Pass 56 — INC/DEC #n, (R32) — 3-bit embedded immediate (n=0 → 8).
    # Sub-op layout per ngdis/tlcs900_zz_mem.c :
    #   0x60..0x67 = INC #n, (R32)  — n = (op & 0x07) ; 0 → 8
    #   0x68..0x6F = DEC #n, (R32)
    if 0x60 <= op <= 0x67:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=2, mnemonic="inc", operands=f"{n}, {r32_name}")
    if 0x68 <= op <= 0x6F:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=2, mnemonic="dec", operands=f"{n}, {r32_name}")

    # Pass 56 — shift/rotate on (R32) byte memory operand. 8 dedicated opcodes.
    # Sub-op layout per ngdis/tlcs900_zz_mem.c (each = single-byte op, RMW) :
    #   0x78 RLC (R32)   0x79 RRC (R32)   0x7A RL  (R32)   0x7B RR  (R32)
    #   0x7C SLA (R32)   0x7D SRA (R32)   0x7E SLL (R32)   0x7F SRL (R32)
    if 0x78 <= op <= 0x7F:
        shift_mnem = {
            0x78: "rlc", 0x79: "rrc", 0x7A: "rl",  0x7B: "rr",
            0x7C: "sla", 0x7D: "sra", 0x7E: "sll", 0x7F: "srl",
        }[op]
        return _DecodedInstruction(length=2, mnemonic=shift_mnem, operands=f"{r32_name}")

    # Pass 55 — ALU (R32), imm8 (3-byte forms ; mem ← mem op imm8).
    # Verified against NgpCraft_Disasm oracle.
    #   0x38 imm8 = ADD (R32), imm8     0x3B imm8 = SBC (R32), imm8
    #   0x39 imm8 = ADC (R32), imm8     0x3C imm8 = AND (R32), imm8
    #   0x3A imm8 = SUB (R32), imm8     0x3D imm8 = XOR (R32), imm8
    #                                   0x3E imm8 = OR  (R32), imm8
    # (0x3F imm8 = CP (R32), imm8 already handled above.)
    if 0x38 <= op <= 0x3E and len(data) == 3:
        imm_mnem = {
            0x38: "add", 0x39: "adc", 0x3A: "sub", 0x3B: "sbc",
            0x3C: "and", 0x3D: "xor", 0x3E: "or",
        }[op]
        return _DecodedInstruction(length=3, mnemonic=imm_mnem, operands=f"{r32_name}, 0x{data[2]:02X}")

    return None


def _decode_arid_d8(data: bytes) -> _DecodedInstruction | None:
    first = data[0]
    displacement = _s8(data[1])
    mem = f"({R32[first & 0x07]}{_format_displacement(displacement)})"
    op = data[2]

    if 0x88 <= first <= 0x8F and op == 0x04:
        return _DecodedInstruction(length=3, mnemonic="push", operands=mem)

    if 0x98 <= first <= 0x9F and op == 0x04:
        return _DecodedInstruction(length=3, mnemonic="pushw", operands=mem)

    if 0xA8 <= first <= 0xAF and op == 0x04:
        return _DecodedInstruction(length=3, mnemonic="pushl", operands=mem)

    if 0x88 <= first <= 0x8F and 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R8[op & 0x07]}, {mem}")

    if 0x98 <= first <= 0x9F and 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R16[op & 0x07]}, {mem}")

    if 0xA8 <= first <= 0xAF and 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R32[op & 0x07]}, {mem}")

    if 0x10 <= op <= 0x17:
        block_mnemonic = {
            0x10: "ldi",
            0x11: "ldir",
            0x12: "ldd",
            0x13: "lddr",
            0x14: "cpi",
            0x15: "cpir",
            0x16: "cpd",
            0x17: "cpdr",
        }[op]
        return _DecodedInstruction(length=3, mnemonic=block_mnemonic, operands=mem)

    # Word-indexed memory MUL/MULS/DIV/DIVS: prefix 0x98..0x9F, op 0x40..0x5F.
    if 0x98 <= first <= 0x9F and 0x40 <= op <= 0x5F:
        mnemonic = {
            0x40: "mul",
            0x48: "muls",
            0x50: "div",
            0x58: "divs",
        }.get(op & 0xF8)
        if mnemonic is not None:
            return _DecodedInstruction(length=3, mnemonic=mnemonic, operands=f"{R32[op & 0x07]}, {mem}")

    # Byte-indexed memory ALU (3-byte forms).
    # Verified family per ngdis/tlcs900_zz_mem.c:
    #   0x80..0x87 = ADD R8,  (mem)   ; 0x88..0x8F = ADD (mem), R8
    #   0x90..0x97 = ADC R8,  (mem)   ; 0x98..0x9F = ADC (mem), R8
    #   0xA0..0xA7 = SUB R8,  (mem)   ; 0xA8..0xAF = SUB (mem), R8
    #   0xB0..0xB7 = SBC R8,  (mem)   ; 0xB8..0xBF = SBC (mem), R8
    #   0xC0..0xC7 = AND R8,  (mem)   ; 0xC8..0xCF = AND (mem), R8
    #   0xD0..0xD7 = XOR R8,  (mem)   ; 0xD8..0xDF = XOR (mem), R8
    #   0xE0..0xE7 = OR  R8,  (mem)   ; 0xE8..0xEF = OR  (mem), R8
    #   0xF0..0xF7 = CP  R8,  (mem)   ; 0xF8..0xFF = CP  (mem), R8
    if 0x88 <= first <= 0x8F and 0x80 <= op <= 0xFF:
        operation_names = {
            0x8: "add",
            0x9: "adc",
            0xA: "sub",
            0xB: "sbc",
            0xC: "and",
            0xD: "xor",
            0xE: "or",
            0xF: "cp",
        }
        op_group = op >> 4
        mnemonic = operation_names.get(op_group)
        if mnemonic is not None:
            register_name = R8[op & 0x07]
            if op & 0x08:
                return _DecodedInstruction(length=3, mnemonic=mnemonic, operands=f"{mem}, {register_name}")
            return _DecodedInstruction(length=3, mnemonic=mnemonic, operands=f"{register_name}, {mem}")

    # Word-indexed memory ALU (3-byte forms).
    # Verified family per ngdis/tlcs900_zz_mem.c:
    #   0x80..0x87 = ADD R16, (mem)   ; 0x88..0x8F = ADD (mem), R16
    #   0x90..0x97 = ADC R16, (mem)   ; 0x98..0x9F = ADC (mem), R16
    #   0xA0..0xA7 = SUB R16, (mem)   ; 0xA8..0xAF = SUB (mem), R16
    #   0xB0..0xB7 = SBC R16, (mem)   ; 0xB8..0xBF = SBC (mem), R16
    #   0xC0..0xC7 = AND R16, (mem)   ; 0xC8..0xCF = AND (mem), R16
    #   0xD0..0xD7 = XOR R16, (mem)   ; 0xD8..0xDF = XOR (mem), R16
    #   0xE0..0xE7 = OR  R16, (mem)   ; 0xE8..0xEF = OR  (mem), R16
    #   0xF0..0xF7 = CP  R16, (mem)   ; 0xF8..0xFF = CP  (mem), R16
    if 0x98 <= first <= 0x9F and 0x80 <= op <= 0xFF:
        operation_names = {
            0x8: "add",
            0x9: "adc",
            0xA: "sub",
            0xB: "sbc",
            0xC: "and",
            0xD: "xor",
            0xE: "or",
            0xF: "cp",
        }
        op_group = op >> 4
        mnemonic = operation_names.get(op_group)
        if mnemonic is not None:
            register_name = R16[op & 0x07]
            if op & 0x08:
                return _DecodedInstruction(length=3, mnemonic=mnemonic, operands=f"{mem}, {register_name}")
            return _DecodedInstruction(length=3, mnemonic=mnemonic, operands=f"{register_name}, {mem}")

    if 0xB8 <= first <= 0xBF and 0x40 <= op <= 0x47:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{mem}, {R8[op & 0x07]}")

    if 0xB8 <= first <= 0xBF and 0x50 <= op <= 0x57:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{mem}, {R16[op & 0x07]}")

    if 0xB8 <= first <= 0xBF and 0x60 <= op <= 0x67:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{mem}, {R32[op & 0x07]}")

    if 0xA8 <= first <= 0xAF and 0x80 <= op <= 0xFF:
        operation_names = {
            0x8: "add",
            0x9: "adc",
            0xA: "sub",
            0xB: "sbc",
            0xC: "and",
            0xD: "xor",
            0xE: "or",
            0xF: "cp",
        }
        op_group = op >> 4
        mnemonic = operation_names.get(op_group)
        if mnemonic is not None:
            register_name = R32[op & 0x07]
            if op & 0x08:
                return _DecodedInstruction(length=3, mnemonic=mnemonic, operands=f"{mem}, {register_name}")
            return _DecodedInstruction(length=3, mnemonic=mnemonic, operands=f"{register_name}, {mem}")

    if 0xB8 <= first <= 0xBF and 0x30 <= op <= 0x37:
        return _DecodedInstruction(length=3, mnemonic="lda", operands=f"{R32[op & 0x07]}, {mem}")

    if 0xB8 <= first <= 0xBF and op == 0x00 and len(data) == 4:
        # ld (r32+d8), imm8  — 4-byte form: [B8+r] [d8] [00] [imm8]
        imm8 = data[3]
        return _DecodedInstruction(length=4, mnemonic="ld", operands=f"{mem}, 0x{imm8:02X}")

    if 0xB8 <= first <= 0xBF and op == 0x02 and len(data) == 5:
        # ldw (r32+d8), imm16  — 5-byte form: [B8+r] [d8] [02] [lo] [hi]
        imm16 = _u16(data, 3)
        return _DecodedInstruction(length=5, mnemonic="ldw", operands=f"{mem}, 0x{imm16:04X}")

    # cp (r32+d8), imm — compare indexed memory with immediate
    # Catalog: 80+zz+mem : 3F : # (byte/word/long)
    if 0x88 <= first <= 0x8F and op == 0x3F and len(data) == 4:
        return _DecodedInstruction(length=4, mnemonic="cp", operands=f"{mem}, 0x{data[3]:02X}")

    if 0x98 <= first <= 0x9F and op == 0x3F and len(data) == 5:
        imm16 = _u16(data, 3)
        return _DecodedInstruction(length=5, mnemonic="cp", operands=f"{mem}, 0x{imm16:04X}")

    if 0xA8 <= first <= 0xAF and op == 0x3F and len(data) == 7:
        imm32 = int.from_bytes(data[3:7], "little")
        return _DecodedInstruction(length=7, mnemonic="cp", operands=f"{mem}, 0x{imm32:08X}")

    if 0x88 <= first <= 0x8F and 0x38 <= op <= 0x3E and len(data) == 4:
        imm_mnem = {
            0x38: "add", 0x39: "adc", 0x3A: "sub", 0x3B: "sbc",
            0x3C: "and", 0x3D: "xor", 0x3E: "or",
        }[op]
        return _DecodedInstruction(length=4, mnemonic=imm_mnem, operands=f"{mem}, 0x{data[3]:02X}")

    if 0x98 <= first <= 0x9F and 0x38 <= op <= 0x3E and len(data) == 5:
        imm_mnem = {
            0x38: "add", 0x39: "adc", 0x3A: "sub", 0x3B: "sbc",
            0x3C: "and", 0x3D: "xor", 0x3E: "or",
        }[op]
        imm16 = _u16(data, 3)
        return _DecodedInstruction(length=5, mnemonic=imm_mnem, operands=f"{mem}, 0x{imm16:04X}")

    if 0xA8 <= first <= 0xAF and 0x38 <= op <= 0x3E and len(data) == 7:
        imm_mnem = {
            0x38: "add", 0x39: "adc", 0x3A: "sub", 0x3B: "sbc",
            0x3C: "and", 0x3D: "xor", 0x3E: "or",
        }[op]
        imm32 = int.from_bytes(data[3:7], "little")
        return _DecodedInstruction(length=7, mnemonic=imm_mnem, operands=f"{mem}, 0x{imm32:08X}")

    if 0x88 <= first <= 0x8F and 0x60 <= op <= 0x67:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=3, mnemonic="inc", operands=f"{n}, {mem}")

    if 0x88 <= first <= 0x8F and 0x68 <= op <= 0x6F:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=3, mnemonic="dec", operands=f"{n}, {mem}")

    if 0x98 <= first <= 0x9F and 0x60 <= op <= 0x67:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=3, mnemonic="inc", operands=f"{n}, {mem}")

    if 0x98 <= first <= 0x9F and 0x68 <= op <= 0x6F:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=3, mnemonic="dec", operands=f"{n}, {mem}")

    if 0xA8 <= first <= 0xAF and 0x60 <= op <= 0x67:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=3, mnemonic="inc", operands=f"{n}, {mem}")

    if 0xA8 <= first <= 0xAF and 0x68 <= op <= 0x6F:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=3, mnemonic="dec", operands=f"{n}, {mem}")

    return None


def _required_reg_indirect_size(first_opcode: int, op_byte: int) -> int | None:
    """Return the total instruction length for the (r32) register-indirect family (0xB0..0xB7).

    First byte encodes the address register (r32 = first_byte & 0x07).
    Second byte (op_byte) encodes the operation.

    Currently supported operations from the official StarGunner bootstrap:
    - 0x00 : ld (r32), imm8   — 3 bytes total
    - 0x02 : ldw (r32), imm16 — 4 bytes total
    - 0x40..0x47 : ld (r32), R8  — 2 bytes total
    - 0x50..0x57 : ldw (r32), R16 — 2 bytes total
    - 0x60..0x67 : ld (r32), R32  — 2 bytes total
    """
    if not (0xB0 <= first_opcode <= 0xB7):
        return None
    # ret CC special case: B0 [F0..FF] = ret CC (2 bytes)
    if first_opcode == 0xB0 and 0xF0 <= op_byte <= 0xFF:
        return 2
    if first_opcode == 0xB4 and op_byte == 0xE8:
        return 2
    if op_byte == 0x00:
        return 3
    if op_byte == 0x02:
        return 4
    if 0x30 <= op_byte <= 0x37:
        # lda Rdst, (Rbase) — load effective address, 2 bytes
        return 2
    if 0x40 <= op_byte <= 0x47:
        return 2
    if 0x50 <= op_byte <= 0x57:
        return 2
    if 0x60 <= op_byte <= 0x67:
        return 2
    return None


def _required_reg_indirect_word_size(first_opcode: int, op_byte: int) -> int | None:
    """Return the total instruction length for the word `(r32)` family (0x90..0x97)."""
    if not (0x90 <= first_opcode <= 0x97):
        return None
    if 0x10 <= op_byte <= 0x17:
        return 2
    if 0x40 <= op_byte <= 0x5F:
        return 2
    if 0x38 <= op_byte <= 0x3F:
        return 4
    if (
        0x20 <= op_byte <= 0x27
        or 0x30 <= op_byte <= 0x37
        or 0x60 <= op_byte <= 0x6F
        or 0x80 <= op_byte <= 0xFF
    ):
        return 2
    return None


def _decode_reg_indirect(data: bytes) -> _DecodedInstruction | None:
    """Decode the (r32) register-indirect family (0xB0..0xB7).

    Confirmed from ngpc_disasm.py decode_B0_mem + _retmem_info (mem 0..7 = ARI).
    """
    first = data[0]
    if not (0xB0 <= first <= 0xB7):
        return None
    r32 = R32[first & 0x07]
    mem = f"({r32})"
    op = data[1]

    # ret CC special case: B0 [F0..FF] (only valid when first == 0xB0)
    if first == 0xB0 and 0xF0 <= op <= 0xFF and len(data) == 2:
        cc_idx = op & 0x0F
        if cc_idx == 8:
            return _DecodedInstruction(
                length=2,
                mnemonic="ret",
                operands="",
                control_flow_kind="return",
                falls_through=False,
            )
        return _DecodedInstruction(
            length=2,
            mnemonic="ret",
            operands=CC[cc_idx],
            control_flow_kind="conditional-return",
            falls_through=True,
        )

    if op == 0x00 and len(data) == 3:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{mem}, 0x{data[2]:02X}")

    if op == 0x02 and len(data) == 4:
        imm16 = _u16(data, 2)
        return _DecodedInstruction(length=4, mnemonic="ldw", operands=f"{mem}, 0x{imm16:04X}")

    if first == 0xB4 and op == 0xE8 and len(data) == 2:
        return _DecodedInstruction(
            length=2,
            mnemonic="call",
            operands="(XIX)",
            control_flow_kind="call",
            falls_through=True,
        )

    if 0x30 <= op <= 0x37 and len(data) == 2:
        # lda Rdst, (Rbase): load effective address (Rbase value into Rdst)
        return _DecodedInstruction(length=2, mnemonic="lda", operands=f"{R32[op & 0x07]}, {mem}")

    if 0x40 <= op <= 0x47 and len(data) == 2:
        return _DecodedInstruction(length=2, mnemonic="ld", operands=f"{mem}, {R8[op & 0x07]}")

    if 0x50 <= op <= 0x57 and len(data) == 2:
        return _DecodedInstruction(length=2, mnemonic="ldw", operands=f"{mem}, {R16[op & 0x07]}")

    if 0x60 <= op <= 0x67 and len(data) == 2:
        return _DecodedInstruction(length=2, mnemonic="ld", operands=f"{mem}, {R32[op & 0x07]}")

    return None


def _decode_reg_indirect_word(data: bytes) -> _DecodedInstruction | None:
    """Decode word `(r32)` instructions: [0x90+r] [op] [optional imm16]."""
    first = data[0]
    if not (0x90 <= first <= 0x97):
        return None
    r32_name = f"({R32[first & 0x07]})"
    op = data[1]

    if first == 0x95 and op == 0x11:
        return _DecodedInstruction(length=2, mnemonic="ldirw", operands="(XDE+),(XHL+)")

    if 0x10 <= op <= 0x17:
        block_mnemonic = {
            0x10: "ldi",
            0x11: "ldir",
            0x12: "ldd",
            0x13: "lddr",
            0x14: "cpi",
            0x15: "cpir",
            0x16: "cpd",
            0x17: "cpdr",
        }[op]
        return _DecodedInstruction(length=2, mnemonic=block_mnemonic, operands=r32_name)

    if 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=2, mnemonic="ld", operands=f"{R16[op & 0x07]}, {r32_name}")

    if 0x30 <= op <= 0x37:
        return _DecodedInstruction(length=2, mnemonic="ex", operands=f"{r32_name}, {R16[op & 0x07]}")

    if 0x38 <= op <= 0x3E and len(data) == 4:
        imm_mnem = {
            0x38: "add", 0x39: "adc", 0x3A: "sub", 0x3B: "sbc",
            0x3C: "and", 0x3D: "xor", 0x3E: "or",
        }[op]
        return _DecodedInstruction(length=4, mnemonic=imm_mnem, operands=f"{r32_name}, 0x{_u16(data, 2):04X}")

    if op == 0x3F and len(data) == 4:
        return _DecodedInstruction(length=4, mnemonic="cp", operands=f"{r32_name}, 0x{_u16(data, 2):04X}")

    if 0x40 <= op <= 0x5F:
        mnemonic = {
            0x40: "mul",
            0x48: "muls",
            0x50: "div",
            0x58: "divs",
        }.get(op & 0xF8)
        if mnemonic is not None:
            return _DecodedInstruction(length=2, mnemonic=mnemonic, operands=f"{R32[op & 0x07]}, {r32_name}")

    if 0x60 <= op <= 0x67:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=2, mnemonic="inc", operands=f"{n}, {r32_name}")

    if 0x68 <= op <= 0x6F:
        n = op & 0x07
        if n == 0:
            n = 8
        return _DecodedInstruction(length=2, mnemonic="dec", operands=f"{n}, {r32_name}")

    if 0x80 <= op <= 0xFF:
        operation_names = {
            0x8: "add",
            0x9: "adc",
            0xA: "sub",
            0xB: "sbc",
            0xC: "and",
            0xD: "xor",
            0xE: "or",
            0xF: "cp",
        }
        mnemonic = operation_names.get(op >> 4)
        if mnemonic is not None:
            register_name = R16[op & 0x07]
            if op & 0x08:
                return _DecodedInstruction(length=2, mnemonic=mnemonic, operands=f"{r32_name}, {register_name}")
            return _DecodedInstruction(length=2, mnemonic=mnemonic, operands=f"{register_name}, {r32_name}")

    return None


def _build_c7_register_names() -> tuple[str, ...]:
    """Authoritative TLCS-900/H byte-register-code table (`r8_names`).

    The C7 extended-register prefix carries an 8-bit register selector in
    its second byte.  ngdis indexes a 256-entry table with it directly
    (`tlcs900statics.c r8_names` + `getregs(r<0)` in `tlcs900helper.c`).
    The earlier heuristic (`high nibble = bank`) was wrong: it produced
    nonsense like `rH14` for code 0xE6, whose real name is `QC` (the
    bits-16..23 byte slice of XBC in the current bank).
    """
    names = ["?"] * 256
    banked = (
        "RA0", "RW0", "QA0", "QW0", "RC0", "RB0", "QC0", "QB0",
        "RE0", "RD0", "QE0", "QD0", "RL0", "RH0", "QL0", "QH0",
        "RA1", "RW1", "QA1", "QW1", "RC1", "RB1", "QC1", "QB1",
        "RE1", "RD1", "QE1", "QD1", "RL1", "RH1", "QL1", "QH1",
        "RA2", "RW2", "QA2", "QW2", "RC2", "RB2", "QC2", "QB2",
        "RE2", "RD2", "QE2", "QD2", "RL2", "RH2", "QL2", "QH2",
        "RA3", "RW3", "QA3", "QW3", "RC3", "RB3", "QC3", "QB3",
        "RE3", "RD3", "QE3", "QD3", "RL3", "RH3", "QL3", "QH3",
    )
    for i, n in enumerate(banked):  # 0x00..0x3F : explicit-bank byte regs
        names[i] = n
    prev = (
        "A'", "W'", "QA'", "QW'", "C'", "B'", "QC'", "QB'",
        "E'", "D'", "QE'", "QD'", "L'", "H'", "QL'", "QH'",
    )
    for i, n in enumerate(prev):  # 0xD0..0xDF : previous-bank byte regs
        names[0xD0 + i] = n
    current = (
        "A", "W", "QA", "QW", "C", "B", "QC", "QB",
        "E", "D", "QE", "QD", "L", "H", "QL", "QH",
    )
    for i, n in enumerate(current):  # 0xE0..0xEF : current-bank XWA..XHL slices
        names[0xE0 + i] = n
    index = (
        "IXL", "IXH", "QIXL", "QIXH", "IYL", "IYH", "QIYL", "QIYH",
        "IZL", "IZH", "QIZL", "QIZH", "SPL", "SPH", "QSPL", "QSPH",
    )
    for i, n in enumerate(index):  # 0xF0..0xFF : current-bank XIX..XSP slices
        names[0xF0 + i] = n
    return tuple(names)


# Display names indexed by the C7 extension byte (Toshiba register code).
C7_REGISTER_NAMES = _build_c7_register_names()


def c7_current_bank_slice(reg_byte: int) -> tuple[int, int] | None:
    """Map a C7 extension byte to a current-bank (R32 index, byte position).

    Codes 0xE0..0xFF address byte slices of the eight current-bank 32-bit
    registers XWA..XSP, four bytes each in ascending order:
      pos 0 = bits 0..7, 1 = bits 8..15, 2 = bits 16..23, 3 = bits 24..31.
    Returns None for explicit-bank (0x00..0x3F), previous-bank (0xD0..0xDF),
    and invalid (0x40..0xCF) codes, which the current single-bank CPU model
    cannot resolve.
    """
    if 0xE0 <= reg_byte <= 0xFF:
        return (reg_byte - 0xE0) // 4, (reg_byte - 0xE0) % 4
    return None


# C7 sub-op decode tables (Toshiba `decode_zz_r` second byte `c`, zz=0/byte).
_C7_ALU_R = {
    0x80: "add", 0x88: "ld", 0x90: "adc", 0x98: "ld",
    0xA0: "sub", 0xB0: "sbc", 0xB8: "ex", 0xC0: "and",
    0xD0: "xor", 0xE0: "or", 0xF0: "cp",
}
_C7_ALU_IMM = {
    0xC8: "add", 0xC9: "adc", 0xCA: "sub", 0xCB: "sbc",
    0xCC: "and", 0xCD: "xor", 0xCE: "or", 0xCF: "cp", 0x03: "ld",
}
_C7_SHIFT_IMM = {
    0xE8: "rlc", 0xE9: "rrc", 0xEA: "rl", 0xEB: "rr",
    0xEC: "sla", 0xED: "sra", 0xEE: "sll", 0xEF: "srl",
}
_C7_SINGLE = {
    0x04: "push", 0x05: "pop", 0x06: "cpl", 0x07: "neg",
    0x0D: "unlk", 0x10: "daa", 0x12: "extz", 0x13: "exts",
}


def c7_required_length(op_byte: int) -> int:
    """Total instruction length (bytes) of a C7 instruction by sub-op.

    C7 instructions are `C7 <reg> <op> [extra]`.  zz is always 0 (byte) for
    the C7 prefix, so immediate / count / displacement extras are one byte.
    """
    if op_byte in _C7_ALU_IMM:
        return 4   # + imm8
    if op_byte in _C7_SHIFT_IMM or (0x20 <= op_byte <= 0x24) or (0x30 <= op_byte <= 0x34):
        return 4   # + count/bit byte
    return 3


def _decode_c7_extended_register(data: bytes) -> _DecodedInstruction | None:
    """Decode the C7 extended-register prefix family.

    Encoding: ``C7 <reg_byte> <op_byte> [extra]`` where ``reg_byte`` indexes
    the authoritative `r8_names` register-code table and ``op_byte`` is the
    standard `decode_zz_r` sub-op (byte size).  Mirrors the ngdis decode for
    every modeled sub-op; returns None for sub-ops we do not decode yet.
    """
    if len(data) < 3 or data[0] != 0xC7:
        return None
    reg_byte = data[1]
    op = data[2]
    reg = C7_REGISTER_NAMES[reg_byte]
    if reg == "?":
        return None

    # Register-source / register-dest ALU family (op & 0xF8 selects the op),
    # plus INC/DEC/LD-imm3/CP-imm3/SCC in the same first-if branch of ngdis.
    in_reg_range = (0x40 <= op < 0xC8) or (0xD0 <= op < 0xE8) or (0xF0 <= op < 0xF8)
    if in_reg_range:
        hi = op & 0xF8
        if hi in _C7_ALU_R:
            mnem = _C7_ALU_R[hi]
            other = R8[op & 0x07]
            if hi == 0x98:  # LD r, R — extended reg is the destination
                operands = f"{reg}, {other}"
            else:           # ADD/LD/ADC/SUB/SBC/EX/AND/XOR/OR/CP R, r
                operands = f"{other}, {reg}"
            return _DecodedInstruction(length=3, mnemonic=mnem, operands=operands)
        if hi == 0x60 or hi == 0x68:
            n = (op & 0x07) or 8
            mnem = "inc" if hi == 0x60 else "dec"
            return _DecodedInstruction(length=3, mnemonic=mnem, operands=f"{n}, {reg}")
        if hi == 0xA8:  # LD r, #3
            return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{reg}, {op & 0x07}")
        if hi == 0xD8:  # CP r, #3
            return _DecodedInstruction(length=3, mnemonic="cp", operands=f"{reg}, {op & 0x07}")
        return None

    if op in _C7_ALU_IMM:
        if len(data) < 4:
            return None
        imm = data[3]
        return _DecodedInstruction(
            length=4, mnemonic=_C7_ALU_IMM[op], operands=f"{reg}, 0x{imm:02X}",
        )
    if op in _C7_SHIFT_IMM:
        if len(data) < 4:
            return None
        count = data[3] & 0x0F
        return _DecodedInstruction(
            length=4, mnemonic=_C7_SHIFT_IMM[op], operands=f"{count}, {reg}",
        )
    if op in _C7_SINGLE:
        return _DecodedInstruction(length=3, mnemonic=_C7_SINGLE[op], operands=reg)
    return None


def _required_b0_mem_prefix_size(first_opcode: int) -> int | None:
    if first_opcode == 0xC2:
        # abs24 byte ops: C2 [addr24: 3 bytes] [op: 1 byte] = 5 bytes base
        return 5
    if first_opcode == 0xF1:
        return 4
    if first_opcode == 0xF2:
        return 5
    if first_opcode == 0xF3:
        # ARI secondary indexed (mem=19, mode=3): F3 + secondary + r32_byte + r16_byte + op_byte
        return 5
    return None


def _required_b0_mem_size(first_opcode: int, op_byte: int) -> int | None:
    if first_opcode == 0xC2:
        if 0x20 <= op_byte <= 0x27:
            return 5   # ld R8, (abs24)
        if 0x40 <= op_byte <= 0x47:
            return 5   # ld (abs24), R8
        if 0x60 <= op_byte <= 0x6F:
            return 5   # inc/dec N, (abs24)
        if 0x80 <= op_byte <= 0xFF:
            return 5   # ALU/CP byte abs24
        if op_byte == 0x3F:
            return 6   # cp (abs24), imm8
        return None

    if first_opcode == 0xF1:
        if op_byte == 0x00:
            return 5
        if 0x28 <= op_byte <= 0x2C:
            return 4
        if 0x40 <= op_byte <= 0x47:
            return 4   # ld (abs16), R8
        if 0x50 <= op_byte <= 0x57:
            return 4   # ldw (abs16), R16
        if 0x60 <= op_byte <= 0x67:
            return 4
        if 0x80 <= op_byte <= 0xA7:
            return 4
        if 0xA8 <= op_byte <= 0xCF:
            return 4
        return None

    if first_opcode == 0xF2:
        if op_byte == 0x00:
            return 6
        if op_byte == 0x02:
            return 7   # ldw (abs24), imm16
        if 0x28 <= op_byte <= 0x2C:
            return 5
        if 0x30 <= op_byte <= 0x37:
            return 5
        if 0x40 <= op_byte <= 0x47:
            return 5
        if 0x50 <= op_byte <= 0x57:
            return 5   # ldw (abs24), R16
        if 0x60 <= op_byte <= 0x67:
            return 5   # ld (abs24), R32
        if 0x80 <= op_byte <= 0xA7:
            return 5   # carry-flag mem ops
        if 0xA8 <= op_byte <= 0xCF:
            return 5   # bit-manip abs24
        return None

    if first_opcode == 0xF3:
        # ARI secondary indexed (mem=19, mode=3): 4-byte prefix + op_byte + operands
        if 0x30 <= op_byte <= 0x37:
            return 5   # lda R32, (r32+r16) — no extra operand bytes
        if 0x40 <= op_byte <= 0x47:
            return 5   # ld (r32+r16), R8
        if 0x50 <= op_byte <= 0x57:
            return 5   # ldw (r32+r16), R16
        if 0x60 <= op_byte <= 0x67:
            return 5   # ld (r32+r16), R32
        if op_byte == 0xD8:
            return 5   # jp (r32+r8/r16)
        if op_byte == 0x00:
            return 6   # + 1 imm8
        if op_byte == 0x02:
            return 7   # + 2 imm16
        return None

    return None


def _required_abs16_byte_mem_size(first_opcode: int, op_byte: int) -> int | None:
    if first_opcode != 0xC1:
        return None
    if 0x20 <= op_byte <= 0x27:
        return 4
    if 0x38 <= op_byte <= 0x3F:
        return 5
    if op_byte == 0x3F:
        return 5
    if 0x60 <= op_byte <= 0x6F:
        # inc/dec N, (abs16) byte — 4 bytes total: C1 lo hi op
        return 4
    return None


def _required_abs16_word_mem_size(first_opcode: int, op_byte: int) -> int | None:
    """Length of an abs16 word-memory opcode (prefix 0xD1).

    Mirror of `_required_abs16_byte_mem_size` for word-form abs16 memory ops.
    Only the sub-opcodes the bootstrap-and-init flow reaches are listed here.
    """
    if first_opcode != 0xD1:
        return None
    if 0x20 <= op_byte <= 0x27:
        return 4   # ld R16, (abs16)
    if op_byte == 0x3F:
        return 6   # cpw (abs16), imm16
    return None


def _required_abs24_word_mem_size(first_opcode: int, op_byte: int) -> int | None:
    """Length of an abs24 word-memory opcode (prefix 0xD2)."""
    if first_opcode != 0xD2:
        return None
    if 0x20 <= op_byte <= 0x27:
        return 5   # ld R16, (abs24)
    return None


def _post_increment_r32_index(encoded: int) -> int:
    # In the TLCS-900/H ARI_PI encoding, the register is carried in bits[4:2] of the
    # memory-form byte.  The full register code uses bits[7:2] as an index into the
    # banked register table (each register occupies a slot of 4: 0xE0=XWA, 0xE4=XBC,
    # ..., 0xF8=XIZ, 0xFC=XSP).  Extracting bits[4:2] gives the correct 0..7 index
    # for the current-bank registers used by the official bootstrap.
    # Reference: ngdis-master/tlcs900statics.c r32_names table + tlcs900helper.c ARI_PI.
    return (encoded >> 2) & 0x07


def _required_post_increment_byte_size(first_opcode: int, op_byte: int) -> int | None:
    if first_opcode == 0xC5 and 0x20 <= op_byte <= 0x27:
        return 3
    if first_opcode == 0xD5 and 0x20 <= op_byte <= 0x27:
        return 3
    if first_opcode == 0xE5 and 0x20 <= op_byte <= 0x27:
        return 3
    if first_opcode == 0xF5 and 0x40 <= op_byte <= 0x47:
        return 3
    if first_opcode == 0xF5 and 0x50 <= op_byte <= 0x57:
        return 3
    if first_opcode == 0xF5 and 0x60 <= op_byte <= 0x67:
        return 3
    if first_opcode == 0xF5 and op_byte == 0x00:
        return 4
    if first_opcode == 0xF5 and op_byte == 0x02:
        return 5
    return None


def _decode_post_increment_byte(data: bytes) -> _DecodedInstruction | None:
    first = data[0]
    r32 = R32[_post_increment_r32_index(data[1])]
    op = data[2]

    if first == 0xC5 and 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R8[op & 0x07]}, ({r32}+)")

    if first == 0xD5 and 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R16[op & 0x07]}, ({r32}+)")

    if first == 0xE5 and 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R32[op & 0x07]}, ({r32}+)")

    if first == 0xF5 and 0x40 <= op <= 0x47:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"({r32}+), {R8[op & 0x07]}")

    if first == 0xF5 and 0x50 <= op <= 0x57:
        return _DecodedInstruction(length=3, mnemonic="ldw", operands=f"({r32}+), {R16[op & 0x07]}")

    if first == 0xF5 and 0x60 <= op <= 0x67:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"({r32}+), {R32[op & 0x07]}")

    if first == 0xF5 and op == 0x00 and len(data) == 4:
        return _DecodedInstruction(length=4, mnemonic="ld", operands=f"({r32}+), 0x{data[3]:02X}")

    if first == 0xF5 and op == 0x02 and len(data) == 5:
        return _DecodedInstruction(length=5, mnemonic="ldw", operands=f"({r32}+), 0x{_u16(data, 3):04X}")

    return None


def _decode_b0_mem(data: bytes) -> _DecodedInstruction | None:
    first = data[0]

    if first == 0xC2:
        target = _u24(data, 1)
        op = data[4]
        if 0x20 <= op <= 0x27:
            return _DecodedInstruction(
                length=5,
                mnemonic="ld",
                operands=f"{R8[op & 0x07]}, (0x{target:06X})",
            )
        if 0x40 <= op <= 0x47:
            return _DecodedInstruction(
                length=5,
                mnemonic="ld",
                operands=f"(0x{target:06X}), {R8[op & 0x07]}",
            )
        if 0x60 <= op <= 0x6F:
            count = op & 0x07
            if count == 0:
                count = 8
            mnemonic = "dec" if op >= 0x68 else "inc"
            return _DecodedInstruction(
                length=5,
                mnemonic=mnemonic,
                operands=f"{count}, (0x{target:06X})",
            )
        if 0x80 <= op <= 0xFF:
            operation_names = {
                0x8: "add",
                0x9: "adc",
                0xA: "sub",
                0xB: "sbc",
                0xC: "and",
                0xD: "xor",
                0xE: "or",
                0xF: "cp",
            }
            mnemonic = operation_names.get(op >> 4)
            if mnemonic is not None:
                register_name = R8[op & 0x07]
                if op & 0x08:
                    return _DecodedInstruction(
                        length=5,
                        mnemonic=mnemonic,
                        operands=f"(0x{target:06X}), {register_name}",
                    )
                return _DecodedInstruction(
                    length=5,
                    mnemonic=mnemonic,
                    operands=f"{register_name}, (0x{target:06X})",
                )
        if op == 0x3F and len(data) == 6:
                return _DecodedInstruction(
                    length=6,
                    mnemonic="cp",
                    operands=f"(0x{target:06X}), 0x{data[5]:02X}",
                )

    if first == 0xD2:
        target = _u24(data, 1)
        op = data[4]
        if 0x20 <= op <= 0x27:
            return _DecodedInstruction(
                length=5,
                mnemonic="ld",
                operands=f"{R16[op & 0x07]}, (0x{target:06X})",
            )

    if first == 0xF1:
        target = _u16(data, 1)
        op = data[3]
        if 0x40 <= op <= 0x47:
            return _DecodedInstruction(
                length=4,
                mnemonic="ld",
                operands=f"(0x{target:04X}), {R8[op & 0x07]}",
            )
        if 0x50 <= op <= 0x57:
            return _DecodedInstruction(
                length=4,
                mnemonic="ldw",
                operands=f"(0x{target:04X}), {R16[op & 0x07]}",
            )
        if 0x60 <= op <= 0x67:
            return _DecodedInstruction(
                length=4,
                mnemonic="ld",
                operands=f"(0x{target:04X}), {R32[op & 0x07]}",
            )
        if op == 0x00 and len(data) == 5:
            return _DecodedInstruction(
                length=5,
                mnemonic="ld",
                operands=f"(0x{target:04X}), 0x{data[4]:02X}",
            )
        a_bit_op_names = {
            0x28: "andcf",
            0x29: "orcf",
            0x2A: "xorcf",
            0x2B: "ldcf",
            0x2C: "stcf",
        }
        mnemonic = a_bit_op_names.get(op)
        if mnemonic is not None:
            return _DecodedInstruction(
                length=4,
                mnemonic=mnemonic,
                operands=f"A, (0x{target:04X})",
            )
        bit_cf_op_names = {
            0x80: "andcf",
            0x88: "orcf",
            0x90: "xorcf",
            0x98: "ldcf",
            0xA0: "stcf",
        }
        mnemonic = bit_cf_op_names.get(op & 0xF8)
        if mnemonic is not None:
            return _DecodedInstruction(
                length=4,
                mnemonic=mnemonic,
                operands=f"{op & 0x07}, (0x{target:04X})",
            )
        bit_op_names = {
            0xA8: "tset",
            0xB0: "res",
            0xB8: "set",
            0xC0: "chg",
            0xC8: "bit",
        }
        mnemonic = bit_op_names.get(op & 0xF8)
        if mnemonic is not None:
            return _DecodedInstruction(
                length=4,
                mnemonic=mnemonic,
                operands=f"{op & 0x07}, (0x{target:04X})",
            )

    if first == 0xF2:
        target = _u24(data, 1)
        op = data[4]
        if 0x30 <= op <= 0x37:
            return _DecodedInstruction(
                length=5,
                mnemonic="lda",
                operands=f"{R32[op & 0x07]}, (0x{target:06X})",
            )
        if 0x40 <= op <= 0x47:
            return _DecodedInstruction(
                length=5,
                mnemonic="ld",
                operands=f"(0x{target:06X}), {R8[op & 0x07]}",
            )
        if 0x50 <= op <= 0x57:
            return _DecodedInstruction(
                length=5,
                mnemonic="ldw",
                operands=f"(0x{target:06X}), {R16[op & 0x07]}",
            )
        if 0x60 <= op <= 0x67:
            return _DecodedInstruction(
                length=5,
                mnemonic="ld",
                operands=f"(0x{target:06X}), {R32[op & 0x07]}",
            )
        if op == 0x00 and len(data) == 6:
            return _DecodedInstruction(
                length=6,
                mnemonic="ld",
                operands=f"(0x{target:06X}), 0x{data[5]:02X}",
            )
        if op == 0x02 and len(data) == 7:
            imm16 = _u16(data, 5)
            return _DecodedInstruction(
                length=7,
                mnemonic="ldw",
                operands=f"(0x{target:06X}), 0x{imm16:04X}",
            )
        a_bit_op_names = {
            0x28: "andcf",
            0x29: "orcf",
            0x2A: "xorcf",
            0x2B: "ldcf",
            0x2C: "stcf",
        }
        mnemonic = a_bit_op_names.get(op)
        if mnemonic is not None:
            return _DecodedInstruction(
                length=5,
                mnemonic=mnemonic,
                operands=f"A, (0x{target:06X})",
            )
        bit_cf_op_names = {
            0x80: "andcf",
            0x88: "orcf",
            0x90: "xorcf",
            0x98: "ldcf",
            0xA0: "stcf",
        }
        mnemonic = bit_cf_op_names.get(op & 0xF8)
        if mnemonic is not None:
            return _DecodedInstruction(
                length=5,
                mnemonic=mnemonic,
                operands=f"{op & 0x07}, (0x{target:06X})",
            )
        bit_op_names = {
            0xA8: "tset",
            0xB0: "res",
            0xB8: "set",
            0xC0: "chg",
            0xC8: "bit",
        }
        mnemonic = bit_op_names.get(op & 0xF8)
        if mnemonic is not None:
            return _DecodedInstruction(
                length=5,
                mnemonic=mnemonic,
                operands=f"{op & 0x07}, (0x{target:06X})",
            )

    if first == 0xF3:
        # ARI secondary indexed mode (mem=19):
        # secondary bits[1:0] = mode, bits[4:2] = r32_idx
        # mode 0x01: (r32+d16) — F3 [secondary] [d16-lo] [d16-hi] [op] — 5 bytes
        # mode 0x03: (r32+R8/R16) — F3 [secondary] [r32_byte] [r16_byte] [op] — 5 bytes
        # Reference: ngpc_disasm.py _retmem_info (mem=19) + catalog_en_20010831_ALT00146.txt
        secondary = data[1]
        mode = secondary & 0x03
        r32_idx = (secondary >> 2) & 0x07
        if mode == 0x01 and len(data) == 5:
            # (r32+d16): signed 16-bit displacement
            r32_base = R32[r32_idx]
            d16 = _s16(data, 2)
            disp_str = f"+{d16}" if d16 >= 0 else str(d16)
            mem = f"({r32_base}{disp_str})"
            op = data[4]
            if 0x30 <= op <= 0x37:
                dest_r32 = R32[op & 0x07]
                return _DecodedInstruction(
                    length=5,
                    mnemonic="lda",
                    operands=f"{dest_r32}, {mem}",
                )
        if mode == 0x03:
            r32_base = R32[(data[2] >> 2) & 0x07]
            is_r16 = bool(secondary & 0x04)
            if is_r16:
                r_index = R16[(data[3] >> 2) & 0x07]
            else:
                r_index = R8[(data[3] >> 2) & 0x07]
            op = data[4]
            if 0x30 <= op <= 0x37 and len(data) == 5:
                dest_r32 = R32[op & 0x07]
                return _DecodedInstruction(
                    length=5,
                    mnemonic="lda",
                    operands=f"{dest_r32}, ({r32_base}+{r_index})",
                )
            if 0x40 <= op <= 0x47 and len(data) == 5:
                return _DecodedInstruction(
                    length=5,
                    mnemonic="ld",
                    operands=f"({r32_base}+{r_index}), {R8[op & 0x07]}",
                )
            if 0x50 <= op <= 0x57 and len(data) == 5:
                return _DecodedInstruction(
                    length=5,
                    mnemonic="ldw",
                    operands=f"({r32_base}+{r_index}), {R16[op & 0x07]}",
                )
            if 0x60 <= op <= 0x67 and len(data) == 5:
                return _DecodedInstruction(
                    length=5,
                    mnemonic="ld",
                    operands=f"({r32_base}+{r_index}), {R32[op & 0x07]}",
                )
            if op == 0xD8 and len(data) == 5:
                return _DecodedInstruction(
                    length=5,
                    mnemonic="jp",
                    operands=f"({r32_base}+{r_index})",
                    control_flow_kind="jump",
                    falls_through=False,
                )
            if op == 0x00 and len(data) == 6:
                return _DecodedInstruction(
                    length=6,
                    mnemonic="ld",
                    operands=f"({r32_base}+{r_index}), 0x{data[5]:02X}",
                )
            if op == 0x02 and len(data) == 7:
                imm16 = _u16(data, 5)
                return _DecodedInstruction(
                    length=7,
                    mnemonic="ldw",
                    operands=f"({r32_base}+{r_index}), 0x{imm16:04X}",
                )

    return None


def _required_secondary_indexed_load_size(data: bytes) -> int | None:
    if len(data) < 5 or data[0] not in (0xC3, 0xD3, 0xE3):
        return None

    secondary = data[1]
    mode = secondary & 0x03
    op = data[4]
    if mode == 0x03 and 0x20 <= op <= 0x27:
        return 5
    return None


def _decode_secondary_indexed_load(data: bytes) -> _DecodedInstruction | None:
    if len(data) != 5 or data[0] not in (0xC3, 0xD3, 0xE3):
        return None

    secondary = data[1]
    mode = secondary & 0x03
    if mode != 0x03:
        return None

    r32_base = R32[(data[2] >> 2) & 0x07]
    if secondary & 0x04:
        r_index = R16[(data[3] >> 2) & 0x07]
    else:
        r_index = R8[(data[3] >> 2) & 0x07]

    op = data[4]
    if 0x20 <= op <= 0x27:
        if data[0] == 0xC3:
            dest_name = R8[op & 0x07]
        elif data[0] == 0xD3:
            dest_name = R16[op & 0x07]
        else:
            dest_name = R32[op & 0x07]
        return _DecodedInstruction(
            length=5,
            mnemonic="ld",
            operands=f"{dest_name}, ({r32_base}+{r_index})",
        )

    return None


def _required_abs8_long_mem_size(first_opcode: int, op_byte: int) -> int | None:
    if first_opcode != 0xE0:
        return None
    if 0x20 <= op_byte <= 0x27:
        return 3   # ld R32, (abs8 cpu-io)
    return None


def _decode_abs8_long_mem(data: bytes) -> _DecodedInstruction | None:
    if data[0] != 0xE0 or len(data) != 3:
        return None
    address = data[1]
    op = data[2]
    if 0x20 <= op <= 0x27:
        return _DecodedInstruction(length=3, mnemonic="ld", operands=f"{R32[op & 0x07]}, (0x{address:02X})")
    return None


def _decode_abs16_word_mem(data: bytes) -> _DecodedInstruction | None:
    if data[0] != 0xD1 or len(data) < 4:
        return None
    address = _u16(data, 1)
    mem = f"(0x{address:04X})"
    op = data[3]
    if 0x20 <= op <= 0x27 and len(data) == 4:
        return _DecodedInstruction(length=4, mnemonic="ld", operands=f"{R16[op & 0x07]}, {mem}")
    if op == 0x3F and len(data) == 6:
        imm16 = _u16(data, 4)
        return _DecodedInstruction(length=6, mnemonic="cpw", operands=f"{mem}, 0x{imm16:04X}")
    return None


def _decode_abs16_byte_mem(data: bytes) -> _DecodedInstruction | None:
    if data[0] != 0xC1 or len(data) < 4:
        return None

    address = _u16(data, 1)
    mem = f"(0x{address:04X})"
    op = data[3]

    if 0x20 <= op <= 0x27 and len(data) == 4:
        return _DecodedInstruction(length=4, mnemonic="ld", operands=f"{R8[op & 0x07]}, {mem}")

    if 0x38 <= op <= 0x3E and len(data) == 5:
        imm_mnem = {
            0x38: "add", 0x39: "adc", 0x3A: "sub", 0x3B: "sbc",
            0x3C: "and", 0x3D: "xor", 0x3E: "or",
        }[op]
        return _DecodedInstruction(length=5, mnemonic=imm_mnem, operands=f"{mem}, 0x{data[4]:02X}")

    if op == 0x3F and len(data) == 5:
        return _DecodedInstruction(length=5, mnemonic="cp", operands=f"{mem}, 0x{data[4]:02X}")

    # inc/dec N, (abs16) byte form: count_code = op & 0x07; count_code 0 = 8.
    # See toolchain `encode_mem_abs16_inc_dec`: base 0x60 for INC, 0x68 for DEC.
    if 0x60 <= op <= 0x67 and len(data) == 4:
        count_code = op & 0x07
        n = 8 if count_code == 0 else count_code
        return _DecodedInstruction(length=4, mnemonic="incb", operands=f"{n}, {mem}")
    if 0x68 <= op <= 0x6F and len(data) == 4:
        count_code = op & 0x07
        n = 8 if count_code == 0 else count_code
        return _DecodedInstruction(length=4, mnemonic="decb", operands=f"{n}, {mem}")

    return None


def _decoded_result(pc: int, decoded: _DecodedInstruction, raw_bytes: bytes) -> DecodeResult:
    return DecodeResult(
        pc=pc,
        status="decoded",
        raw_bytes=raw_bytes,
        length=decoded.length,
        mnemonic=decoded.mnemonic,
        operands=decoded.operands,
        assembly=decoded.assembly,
        next_sequential_pc=pc + decoded.length,
        control_flow_kind=decoded.control_flow_kind,
        direct_target=decoded.direct_target,
        falls_through=decoded.falls_through,
        warning=decoded.warning,
        note=(
            "Decoded using the current bootstrap-focused minimal TLCS-900 subset. This is "
            "not yet a full instruction decoder."
        ),
    )


def _truncated_result(pc: int, raw_bytes: bytes, failure_status: str, note: str) -> DecodeResult:
    return DecodeResult(
        pc=pc,
        status="truncated",
        raw_bytes=raw_bytes,
        length=None,
        mnemonic=None,
        operands=None,
        assembly=None,
        next_sequential_pc=None,
        control_flow_kind=None,
        direct_target=None,
        falls_through=None,
        warning=None,
        note=f"{note} Read stopped with bus status '{failure_status}'.",
    )


def _unknown_result(pc: int, raw_bytes: bytes, note: str) -> DecodeResult:
    return DecodeResult(
        pc=pc,
        status="unknown-opcode",
        raw_bytes=raw_bytes,
        length=None,
        mnemonic=None,
        operands=None,
        assembly=None,
        next_sequential_pc=None,
        control_flow_kind=None,
        direct_target=None,
        falls_through=None,
        warning=None,
        note=note,
    )


def decode_instruction_at(bus: NgpcReadBus, pc: int) -> DecodeResult:
    """Decode one instruction at an explicit address using the minimal supported subset."""
    first_byte, failure_status = _read_prefix(bus, pc, 1)
    if failure_status is not None:
        return DecodeResult(
            pc=pc,
            status=failure_status,
            raw_bytes=None,
            length=None,
            mnemonic=None,
            operands=None,
            assembly=None,
            next_sequential_pc=None,
            control_flow_kind=None,
            direct_target=None,
            falls_through=None,
            warning=None,
            note=(
                "Could not read the first opcode byte through the current minimal read-only "
                "bus model, so no decode attempt was possible."
            ),
        )

    first_opcode = first_byte[0]

    fixed_size = _required_fixed_size(first_opcode)
    if fixed_size is not None:
        raw_bytes, failure_status = _read_prefix(bus, pc, fixed_size)
        if failure_status is not None:
            return _truncated_result(
                pc,
                raw_bytes,
                failure_status,
                "The opcode belongs to a currently supported fixed instruction family, but not all required bytes were readable.",
            )
        decoded = _decode_fixed(pc, raw_bytes)
        assert decoded is not None
        return _decoded_result(pc, decoded, raw_bytes)

    if 0x20 <= first_opcode <= 0x7F:
        xx_size = _required_xx_size(first_opcode)
        if xx_size is None:
            return _unknown_result(
                pc,
                first_byte,
                "The opcode is inside the current 0x20..0x7F family window, but this specific pattern is not implemented yet.",
            )
        raw_bytes, failure_status = _read_prefix(bus, pc, xx_size)
        if failure_status is not None:
            return _truncated_result(
                pc,
                raw_bytes,
                failure_status,
                "The opcode belongs to a currently supported 0x20..0x7F family, but not all required bytes were readable.",
            )
        decoded = _decode_xx(pc, raw_bytes)
        assert decoded is not None
        return _decoded_result(pc, decoded, raw_bytes)

    if first_opcode == 0xC7:
        raw_bytes, failure_status = _read_prefix(bus, pc, 3)
        if failure_status is not None:
            return _truncated_result(
                pc,
                raw_bytes,
                failure_status,
                "The opcode belongs to the extended-register prefix family (C7), but not all required bytes were readable.",
            )
        total = c7_required_length(raw_bytes[2])
        if total != 3:
            raw_bytes, failure_status = _read_prefix(bus, pc, total)
            if failure_status is not None:
                return _truncated_result(
                    pc,
                    raw_bytes,
                    failure_status,
                    "The extended-register prefix family (C7) is recognized, but not all required bytes were readable.",
                )
        decoded = _decode_c7_extended_register(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The extended-register prefix (C7) is recognized, but this specific sub-opcode is not decoded yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    if first_opcode == 0xE0:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 3)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The abs8 long-memory family is recognized, but not all required bytes were readable.",
            )
        size = _required_abs8_long_mem_size(prefix_bytes[0], prefix_bytes[2])
        if size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The abs8 long-memory family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        decoded = _decode_abs8_long_mem(prefix_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The abs8 long-memory family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, prefix_bytes)

    # 0xD1/0xD2 overlap with the prefixed-register byte family (0xD0..0xD7
    # ALU r+r broken on NGPC silicon) and absolute word-memory families.
    # Try the memory-form dispatch first; fall through to prefixed-register
    # only if the mem form does not match.
    if first_opcode == 0xD1:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 4)
        if failure_status is None:
            size = _required_abs16_word_mem_size(prefix_bytes[0], prefix_bytes[3])
            if size is not None:
                raw_bytes = prefix_bytes
                if size != len(prefix_bytes):
                    raw_bytes, failure_status = _read_prefix(bus, pc, size)
                    if failure_status is not None:
                        return _truncated_result(
                            pc,
                            raw_bytes,
                            failure_status,
                            "The abs16 word-memory family is recognized, but not all required bytes were readable.",
                        )
                decoded = _decode_abs16_word_mem(raw_bytes)
                if decoded is not None:
                    return _decoded_result(pc, decoded, raw_bytes)

    if first_opcode == 0xD2:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 5)
        if failure_status is None:
            size = _required_abs24_word_mem_size(prefix_bytes[0], prefix_bytes[4])
            if size is not None:
                raw_bytes = prefix_bytes
                if size != len(prefix_bytes):
                    raw_bytes, failure_status = _read_prefix(bus, pc, size)
                    if failure_status is not None:
                        return _truncated_result(
                            pc,
                            raw_bytes,
                            failure_status,
                            "The abs24 word-memory family is recognized, but not all required bytes were readable.",
                        )
                decoded = _decode_b0_mem(raw_bytes)
                if decoded is not None:
                    return _decoded_result(pc, decoded, raw_bytes)

    if first_opcode == 0xD3:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 5)
        if failure_status is None:
            size = _required_secondary_indexed_load_size(prefix_bytes)
            if size is not None:
                decoded = _decode_secondary_indexed_load(prefix_bytes)
                if decoded is not None:
                    return _decoded_result(pc, decoded, prefix_bytes)

    if first_opcode == 0xD5:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 3)
        if failure_status is None:
            size = _required_post_increment_byte_size(prefix_bytes[0], prefix_bytes[2])
            if size is not None:
                raw_bytes = prefix_bytes
                if size != len(prefix_bytes):
                    raw_bytes, failure_status = _read_prefix(bus, pc, size)
                    if failure_status is not None:
                        return _truncated_result(
                            pc,
                            raw_bytes,
                            failure_status,
                            "The post-increment family is recognized, but not all required bytes were readable.",
                        )
                decoded = _decode_post_increment_byte(raw_bytes)
                if decoded is not None:
                    return _decoded_result(pc, decoded, raw_bytes)

    if _prefixed_register_info(first_opcode) is not None:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 2)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The opcode belongs to a currently supported prefixed-register family, but the second opcode byte was not readable.",
            )
        size = _required_prefixed_register_size(prefix_bytes[0], prefix_bytes[1])
        if size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The prefixed-register family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        raw_bytes, failure_status = _read_prefix(bus, pc, size)
        if failure_status is not None:
            return _truncated_result(
                pc,
                raw_bytes,
                failure_status,
                "The prefixed-register sub-opcode is recognized, but not all required bytes were readable.",
            )
        decoded = _decode_prefixed_register(pc, raw_bytes)
        assert decoded is not None
        return _decoded_result(pc, decoded, raw_bytes)

    if 0xB0 <= first_opcode <= 0xB7:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 2)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The opcode belongs to the (r32) register-indirect family, but the op byte was not readable.",
            )
        size = _required_reg_indirect_size(prefix_bytes[0], prefix_bytes[1])
        if size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The (r32) register-indirect family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        raw_bytes = prefix_bytes
        if size != len(prefix_bytes):
            raw_bytes, failure_status = _read_prefix(bus, pc, size)
            if failure_status is not None:
                return _truncated_result(
                    pc,
                    raw_bytes,
                    failure_status,
                    "The (r32) register-indirect family is recognized, but not all required bytes were readable.",
                )
        decoded = _decode_reg_indirect(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The (r32) register-indirect family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    if 0x90 <= first_opcode <= 0x97:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 2)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The (r32) word-indirect family is recognized, but not all required bytes were readable.",
            )
        op2 = prefix_bytes[1]
        total_size = _required_reg_indirect_word_size(first_opcode, op2)
        if total_size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The (r32) word-indirect family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        raw_bytes, failure_status = _read_prefix(bus, pc, total_size)
        if failure_status is not None:
            return _truncated_result(
                pc,
                raw_bytes,
                failure_status,
                "The (r32) word-indirect family is recognized, but not all required bytes were readable.",
            )
        decoded = _decode_reg_indirect_word(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The (r32) word-indirect family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    if 0x80 <= first_opcode <= 0x87:
        # (r32) byte-indirect: [0x80+r] [op] [optional extra bytes]
        # Read 2-byte prefix first to determine total size.
        prefix_bytes, failure_status = _read_prefix(bus, pc, 2)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The (r32) byte-indirect family is recognized, but not all required bytes were readable.",
            )
        op2 = prefix_bytes[1]
        if 0x38 <= op2 <= 0x3F:
            # 3-byte ALU immediate forms : ADD/ADC/SUB/SBC/AND/XOR/OR/CP (r32), imm8
            # Sub-ops 0x38..0x3F per ngdis/tlcs900_zz_mem.c.
            total_size = 3
        else:
            total_size = 2  # 2-byte ops: ld R8, (r32) etc.
        raw_bytes, failure_status = _read_prefix(bus, pc, total_size)
        if failure_status is not None:
            return _truncated_result(
                pc,
                raw_bytes,
                failure_status,
                "The (r32) byte-indirect family is recognized, but not all required bytes were readable.",
            )
        decoded = _decode_reg_indirect_load(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The (r32) byte-indirect family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    if 0x88 <= first_opcode <= 0xBF:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 3)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The opcode belongs to a currently supported indexed-memory family, but not all required bytes were readable.",
            )
        # Some ops in the (r32+d8) family have trailing immediates beyond the 3-byte prefix.
        # op=0x00 → ld (r32+d8), imm8   (4 bytes total)
        # op=0x02 → ldw (r32+d8), imm16  (5 bytes total)
        # op=0x3F → cp (r32+d8), imm     (4/5/7 bytes depending on size prefix)
        op_byte = prefix_bytes[2]
        if op_byte == 0x00:
            total_size = 4
        elif op_byte == 0x02:
            total_size = 5
        elif 0x38 <= op_byte <= 0x3F:
            if 0x88 <= first_opcode <= 0x8F:
                total_size = 4   # ALU/CP (byte r32+d8), imm8
            elif 0x98 <= first_opcode <= 0x9F:
                total_size = 5   # ALU/CP (word r32+d8), imm16
            elif 0xA8 <= first_opcode <= 0xAF:
                total_size = 7   # ALU/CP (long r32+d8), imm32
            else:
                total_size = 3   # unsupported, will fall through to unknown
        else:
            total_size = 3
        if total_size != 3:
            raw_bytes, failure_status = _read_prefix(bus, pc, total_size)
            if failure_status is not None:
                return _truncated_result(
                    pc,
                    raw_bytes,
                    failure_status,
                    "The indexed-memory family is recognized, but not all required bytes were readable.",
                )
        else:
            raw_bytes = prefix_bytes
        decoded = _decode_arid_d8(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The indexed-memory family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    if first_opcode == 0xC1:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 4)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The opcode belongs to a currently supported abs16 byte-memory family, but not all required bytes were readable.",
            )
        size = _required_abs16_byte_mem_size(prefix_bytes[0], prefix_bytes[3])
        if size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The abs16 byte-memory family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        raw_bytes = prefix_bytes
        if size != len(prefix_bytes):
            raw_bytes, failure_status = _read_prefix(bus, pc, size)
            if failure_status is not None:
                return _truncated_result(
                    pc,
                    raw_bytes,
                    failure_status,
                    "The abs16 byte-memory family is recognized, but not all required bytes were readable.",
                )
        decoded = _decode_abs16_byte_mem(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The abs16 byte-memory family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    if first_opcode in {0xC5, 0xD5, 0xE5, 0xF5}:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 3)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The opcode belongs to a currently supported post-increment byte family, but not all required bytes were readable.",
            )
        size = _required_post_increment_byte_size(prefix_bytes[0], prefix_bytes[2])
        if size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The post-increment byte family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        raw_bytes = prefix_bytes
        if size != len(prefix_bytes):
            raw_bytes, failure_status = _read_prefix(bus, pc, size)
            if failure_status is not None:
                return _truncated_result(
                    pc,
                    raw_bytes,
                    failure_status,
                    "The post-increment byte family is recognized, but not all required bytes were readable.",
                )
        decoded = _decode_post_increment_byte(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The post-increment byte family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    b0_mem_prefix_size = _required_b0_mem_prefix_size(first_opcode)
    if b0_mem_prefix_size is not None:
        prefix_bytes, failure_status = _read_prefix(bus, pc, b0_mem_prefix_size)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The opcode belongs to a currently supported B0 memory family, but not all required bytes were readable.",
            )
        size = _required_b0_mem_size(prefix_bytes[0], prefix_bytes[-1])
        if size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The B0 memory family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        raw_bytes = prefix_bytes
        if size != len(prefix_bytes):
            raw_bytes, failure_status = _read_prefix(bus, pc, size)
            if failure_status is not None:
                return _truncated_result(
                    pc,
                    raw_bytes,
                    failure_status,
                    "The B0 memory family is recognized, but not all required bytes were readable.",
                )
        decoded = _decode_b0_mem(raw_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                raw_bytes,
                "The B0 memory family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, raw_bytes)

    if first_opcode in {0xC3, 0xD3, 0xE3}:
        prefix_bytes, failure_status = _read_prefix(bus, pc, 5)
        if failure_status is not None:
            return _truncated_result(
                pc,
                prefix_bytes,
                failure_status,
                "The secondary-indexed load family is recognized, but not all required bytes were readable.",
            )
        size = _required_secondary_indexed_load_size(prefix_bytes)
        if size is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The secondary-indexed load family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        decoded = _decode_secondary_indexed_load(prefix_bytes)
        if decoded is None:
            return _unknown_result(
                pc,
                prefix_bytes,
                "The secondary-indexed load family is recognized, but this specific sub-opcode is not implemented yet.",
            )
        return _decoded_result(pc, decoded, prefix_bytes)

    return _unknown_result(
        pc,
        first_byte,
        "The first opcode byte is readable, but the current minimal decoder does not implement this instruction family yet.",
    )


def decode_next_instruction(view: NgpcFetchView) -> DecodeResult:
    """Decode one instruction at the current machine PC."""
    return decode_instruction_at(view.bus, view.machine.cpu.pc)
