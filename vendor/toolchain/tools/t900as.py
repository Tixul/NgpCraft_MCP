#!/usr/bin/env python3
"""
t900as.py — TLCS-900 Assembler v0.1
NGPCraft Toolchain, Jalon 1

Single-file, two-pass assembler for TLCS-900/H (NGPC).
Output: flat binary (for absolute sections) or custom .obj (for relocatable, Jalon 2).

Encoding table (confirmed from reverse-engineering main.ngc + ngdis-master disassembler source):

  FIXED (opcode in first byte):
    NOP               = 00
    EI  n             = 06 n              (n != 7)
    DI                = 06 07             (= EI with mask 7, confirmed ngdis)
    RETI              = 07
    LDB (n8), imm8    = 08 n8 imm8        (3 bytes, I/O byte write, n8 in 0x00-0xFF)
    LDW (n8), #16     = 0A n8 lo hi       (4 bytes, I/O word write)
    RET               = 0E
    RETD d16          = 0F lo hi
    JP  #16           = 1A lo hi
    JP  #24           = 1B lo mid hi      (4 bytes, confirmed)
    CALL #16          = 1C lo hi
    CALL #24          = 1D lo mid hi      (4 bytes)
    CALR d16          = 1E lo hi          (relative call, 3 bytes)
    SWI n             = F8+n              (1 byte, n=0..7, confirmed)

  VARIABLE (first byte encodes reg/size):
    LD R8,  #8        = (0x20+R) imm8     (2 bytes, R: W=0 A=1 B=2 C=3 D=4 E=5 H=6 L=7)
    LD R16, #16       = (0x30+R) lo hi    (3 bytes, R: WA=0 BC=1 DE=2 HL=3 IX=4 IY=5 IZ=6 SP=7)
    LD R32, #32       = (0x40+R) b0..b3   (5 bytes, R: XWA=0 XBC=1 XDE=2 XHL=3 XIX=4 XIY=5 XIZ=6 XSP=7)
    PUSH R16          = 0x28+R            (1 byte)
    PUSH R32          = 0x38+R            (1 byte)
    POP  R16          = 0x48+R            (1 byte)
    POP  R32          = 0x58+R            (1 byte)
    JR  [cc,] d8      = (0x60+cc) d8      (2 bytes, confirmed)
    JRL [cc,] d16     = (0x70+cc) lo hi   (3 bytes)

  B0_MEM (first byte = B0+mem_mode):
    LDW (abs16), R16  = F1 lo hi (0x50+R) (4 bytes, R from R16 table)
    LDW (abs16), #16  = F1 lo hi 02 lo2 hi2 (6 bytes)
    LD  (abs16), R8   = F1 lo hi (0x40+R) (4 bytes)

Condition codes (JR cc, pattern = 0x60 + cc_index):
  F=0  LT=1  LE=2  ULE=3  OV=4  MI=5  Z=6   C=7
  T=8  GE=9  GT=A  UGT=B  NOV=C PL=D  NZ=E  NC=F
  T (= always) = JR without condition
  Confirmed: Z=6 -> 0x66, T=8 -> 0x68, NZ=E -> 0x6E

Sources: main.ngc reverse-engineering (2026-03-16) + ngdis-master source (tlcs900_fixed.c,
         tlcs900_xx.c, tlcs900_b0_mem.c, tlcs900_zz_r.c)
Rev: 2026-03-16
"""

import sys
import os
import re
import argparse

# ---------------------------------------------------------------------------
# Condition code table for JR
# ---------------------------------------------------------------------------
JR_CC = {
    'F':   0x60, 'LT':  0x61, 'LE':  0x62, 'ULE': 0x63,
    'OV':  0x64, 'MI':  0x65, 'Z':   0x66, 'C':   0x67,
    'T':   0x68, 'GE':  0x69, 'GT':  0x6A, 'UGT': 0x6B,
    'NOV': 0x6C, 'PL':  0x6D, 'NZ':  0x6E, 'NC':  0x6F,
    # Aliases
    'EQ':  0x66, 'NE':  0x6E, 'LO':  0x67, 'ULT': 0x67,
    'HS':  0x6F, 'UGE': 0x6F, 'LOS': 0x63,
}

# ---------------------------------------------------------------------------
# Expression evaluator (integer only, no floats)
# ---------------------------------------------------------------------------
def eval_expr(expr: str, symbols: dict, current_address: int = 0) -> int:
    """Evaluate an integer expression with symbol substitution."""
    expr = expr.strip()
    if not expr:
        raise ValueError("Empty expression")

    # Replace symbol references
    # Must do longest-match first to avoid partial substitution
    # Simple approach: replace known symbols (word-boundary match)
    def replace_sym(m):
        name = m.group(0)
        if name.upper() in ('EQ', 'NE', 'Z', 'NZ', 'C', 'NC', 'MI', 'PL',
                             'OV', 'NOV', 'LT', 'GE', 'LE', 'GT',
                             'ULT', 'UGE', 'ULE', 'UGT', 'T', 'F'):
            return name  # keep condition codes
        if name in symbols:
            v = symbols[name]
            if isinstance(v, int):
                return str(v)
            # symbol not yet resolved
            raise UnresolvedSymbol(name)
        if name == '$':
            return str(current_address)
        raise UnresolvedSymbol(name)

    # Normalize hex/binary literals to decimal FIRST so that hex digits (A-F)
    # inside literals are not mistakenly matched as symbol names.
    expr_norm = re.sub(r'0[xX][0-9A-Fa-f]+', lambda m: str(int(m.group(0), 16)), expr)
    expr_norm = re.sub(r'0[yY]([01]+)', lambda m: str(int(m.group(1), 2)), expr_norm)

    resolved = re.sub(r'[.A-Za-z_][A-Za-z0-9_.]*|\$', replace_sym, expr_norm)

    # Evaluate safely (only arithmetic/bitwise)
    try:
        result = eval(resolved, {"__builtins__": {}})
        return int(result) & 0xFFFFFFFF
    except Exception as e:
        raise ValueError(f"Cannot evaluate '{expr}' -> '{resolved}': {e}")


class UnresolvedSymbol(Exception):
    pass


# ---------------------------------------------------------------------------
# Instruction encoding helpers
# ---------------------------------------------------------------------------
def encode_jp(addr: int) -> bytes:
    """JP addr24 = 1B lo mid hi (4 bytes)"""
    return bytes([0x1B, addr & 0xFF, (addr >> 8) & 0xFF, (addr >> 16) & 0xFF])


def encode_jr(cc: str | None, disp: int) -> bytes:
    """JR [cc,] disp8 (2 bytes). disp is relative to instruction after JR."""
    if cc is None or cc.upper() == 'T':
        opcode = 0x68
    else:
        cc_up = cc.upper()
        if cc_up not in JR_CC:
            raise ValueError(f"Unknown condition code: '{cc}'")
        opcode = JR_CC[cc_up]
    disp8 = disp & 0xFF  # signed 8-bit stored as unsigned byte
    if disp < -128 or disp > 127:
        raise ValueError(f"JR displacement {disp} out of range [-128, 127]")
    return bytes([opcode, disp & 0xFF])


def encode_ret() -> bytes:
    return bytes([0x0E])


def encode_reti() -> bytes:
    return bytes([0x07])  # assumed from ISR pattern in ROM


def encode_nop() -> bytes:
    return bytes([0x00])


def encode_ldb_io_imm(addr8: int, imm8: int) -> bytes:
    """LDB (n8), imm8 = 08 n8 imm8 — write byte to I/O address 0x00-0xFF"""
    if not (0 <= addr8 <= 0xFF):
        raise ValueError(f"I/O address 0x{addr8:02X} out of range [0x00, 0xFF]")
    return bytes([0x08, addr8 & 0xFF, imm8 & 0xFF])


def encode_ei(mask: int) -> bytes:
    """EI mask — enable interrupts at given priority level (0-7)"""
    return bytes([0x06, mask & 0xFF])


def encode_di() -> bytes:
    """DI — disable interrupts = EI 7 (confirmed from ngdis tlcs900_fixed.c: 06 07)"""
    return bytes([0x06, 0x07])


def encode_swi(n: int) -> bytes:
    """SWI n — software interrupt = 0xF8+n (1 byte, n=0..7, confirmed ngdis + rom reverse)"""
    if not (0 <= n <= 7):
        raise ValueError(f"SWI operand {n} out of range [0, 7]")
    return bytes([0xF8 + (n & 0x07)])


# ---------------------------------------------------------------------------
# Register tables (from ngdis tlcs900_xx.c + tlcs900_b0_mem.c)
# ---------------------------------------------------------------------------
R8_REGS  = {'W': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'H': 6, 'L': 7}
R16_REGS = {'WA': 0, 'BC': 1, 'DE': 2, 'HL': 3, 'IX': 4, 'IY': 5, 'IZ': 6, 'SP': 7}
R32_REGS = {'XWA': 0, 'XBC': 1, 'XDE': 2, 'XHL': 3, 'XIX': 4, 'XIY': 5, 'XIZ': 6, 'XSP': 7}


def encode_ld_r8_imm(reg: str, imm8: int) -> bytes:
    """LD R8, #8 = (0x20+R) imm8  (2 bytes)"""
    r = R8_REGS[reg.upper()]
    return bytes([0x20 + r, imm8 & 0xFF])


def encode_ld_r16_imm(reg: str, imm16: int) -> bytes:
    """LD R16, #16 = (0x30+R) lo hi  (3 bytes)"""
    r = R16_REGS[reg.upper()]
    return bytes([0x30 + r, imm16 & 0xFF, (imm16 >> 8) & 0xFF])


def encode_ld_r32_imm(reg: str, imm32: int, *, allow_compact: bool = False) -> bytes:
    """LD R32, #32 = (0x40+R) b0 b1 b2 b3  (5 bytes).

    When `allow_compact=True` and 0 <= imm32 <= 7, emit the 2-byte compact
    `ld r, #N` form (E8+R : A8+N) instead. Saves 3 bytes per site.

    Callers MUST keep `allow_compact=False` (the default) when emitting a
    relocation placeholder — the linker patches a 4-byte immediate at
    offset+1 and assumes the full 5-byte instruction layout. The compact
    form is only safe for fully-resolved numeric immediates.

    Catalog reference: t900cc.py jalon 5 (HW-validated).
    """
    r = R32_REGS[reg.upper()]
    if allow_compact and 0 <= imm32 <= 7:
        return bytes([0xE8 + r, 0xA8 + imm32])
    return bytes([0x40 + r,
                  imm32 & 0xFF, (imm32 >> 8) & 0xFF,
                  (imm32 >> 16) & 0xFF, (imm32 >> 24) & 0xFF])


def encode_push_r16(reg: str) -> bytes:
    """PUSH R16 = 0x28+R  (1 byte)"""
    return bytes([0x28 + R16_REGS[reg.upper()]])


def encode_push_r32(reg: str) -> bytes:
    """PUSH R32 = 0x38+R  (1 byte)"""
    return bytes([0x38 + R32_REGS[reg.upper()]])


def encode_pop_r16(reg: str) -> bytes:
    """POP R16 = 0x48+R  (1 byte)"""
    return bytes([0x48 + R16_REGS[reg.upper()]])


def encode_pop_r32(reg: str) -> bytes:
    """POP R32 = 0x58+R  (1 byte)"""
    return bytes([0x58 + R32_REGS[reg.upper()]])


def encode_call_abs24(addr: int) -> bytes:
    """CALL #24 = 1D lo mid hi  (4 bytes)"""
    return bytes([0x1D, addr & 0xFF, (addr >> 8) & 0xFF, (addr >> 16) & 0xFF])


def encode_calr_rel16(disp: int) -> bytes:
    """CALR d16 = 1E lo hi  (3 bytes, signed displacement from instruction end)"""
    return bytes([0x1E, disp & 0xFF, (disp >> 8) & 0xFF])


def encode_ldw_io_imm16(addr8: int, imm16: int) -> bytes:
    """LDW (n8), #16 = 0A n8 lo hi  (4 bytes, I/O word write, addr8 in 0x00-0xFF)"""
    if not (0 <= addr8 <= 0xFF):
        raise ValueError(f"LDW I/O address 0x{addr8:02X} out of range [0x00, 0xFF]")
    return bytes([0x0A, addr8 & 0xFF, imm16 & 0xFF, (imm16 >> 8) & 0xFF])


def encode_ldw_abs16_r16(addr16: int, reg: str) -> bytes:
    """LDW (abs16), R16 = F1 lo hi (0x50+R)  (4 bytes, B0_mem ABS_W form)"""
    r = R16_REGS[reg.upper()]
    return bytes([0xF1, addr16 & 0xFF, (addr16 >> 8) & 0xFF, 0x50 + r])


def encode_ldw_abs16_imm16(addr16: int, imm16: int) -> bytes:
    """LDW (abs16), #16 = F1 lo hi 02 lo2 hi2  (6 bytes, B0_mem ABS_W form)"""
    return bytes([0xF1, addr16 & 0xFF, (addr16 >> 8) & 0xFF,
                  0x02, imm16 & 0xFF, (imm16 >> 8) & 0xFF])


def encode_ld_abs16_r8(addr16: int, reg: str) -> bytes:
    """LD (abs16), R8 = F1 lo hi (0x40+R)  (4 bytes, B0_mem ABS_W form)"""
    r = R8_REGS[reg.upper()]
    return bytes([0xF1, addr16 & 0xFF, (addr16 >> 8) & 0xFF, 0x40 + r])


def encode_r16_abs16(reg: str, addr16: int) -> bytes:
    """LD R16, (abs16) = D1 lo hi (0x20+R)  (4 bytes, B0_mem abs16 load form)
    Encoding: 0x98+0x39=0xD1 (word load prefix + abs16 mem mode index 0x39).
    D1 is standalone SAFE per silicon testing (D1..D7 safe, only D0 prefix broken).
    """
    r = R16_REGS[reg.upper()]
    return bytes([0xD1, addr16 & 0xFF, (addr16 >> 8) & 0xFF, 0x20 + r])


def encode_r8_abs16(reg: str, addr16: int) -> bytes:
    """LD R8, (abs16) = C1 lo hi (0x20+R)  (4 bytes, B0_mem abs16 load form)
    Confirmed: c1 82 6f 27 = ld L,(0x6F82) from CC900 dessab (DISASM_CROSSCHECK.md).
    Encoding: 0x88+0x39=0xC1 (byte load prefix + abs16 mem mode index 0x39).
    """
    r = R8_REGS[reg.upper()]
    return bytes([0xC1, addr16 & 0xFF, (addr16 >> 8) & 0xFF, 0x20 + r])


_ABS16_PREFIX_BY_SIZE = {1: 0xC1, 2: 0xD1, 4: 0xE1}
_MEM_IMM_OP = {
    'ADD': 0x38, 'ADC': 0x39, 'SUB': 0x3A, 'SBC': 0x3B,
    'AND': 0x3C, 'XOR': 0x3D, 'OR':  0x3E, 'CP':  0x3F,
}
_MEM_REG_BASE = {
    'ADD': 0x88, 'ADC': 0x98, 'SUB': 0xA8, 'SBC': 0xB8,
    'AND': 0xC8, 'XOR': 0xD8, 'OR':  0xE8, 'CP':  0xF8,
}


def _abs16_mem_prefix(size: int) -> int:
    if size not in _ABS16_PREFIX_BY_SIZE:
        raise ValueError(f"abs16 mem op: unsupported size {size} (expected 1/2/4)")
    return _ABS16_PREFIX_BY_SIZE[size]


def _reg_table_for_size(size: int) -> dict[str, int]:
    if size == 1:
        return R8_REGS
    if size == 2:
        return R16_REGS
    if size == 4:
        return R32_REGS
    raise ValueError(f"register table: unsupported size {size}")


def encode_mem_abs16_alu_imm(mnem: str, size: int, addr16: int, imm: int) -> bytes:
    """ALU (abs16), #imm — zz_mem ABS_W form with explicit operand width."""
    mnem = mnem.upper()
    if mnem not in _MEM_IMM_OP:
        raise ValueError(f"mem abs16 ALU imm: unsupported mnemonic '{mnem}'")
    prefix = _abs16_mem_prefix(size)
    op = _MEM_IMM_OP[mnem]
    data = [prefix, addr16 & 0xFF, (addr16 >> 8) & 0xFF, op]
    if size == 1:
        data.append(imm & 0xFF)
    elif size == 2:
        data.extend([imm & 0xFF, (imm >> 8) & 0xFF])
    else:
        data.extend([
            imm & 0xFF, (imm >> 8) & 0xFF,
            (imm >> 16) & 0xFF, (imm >> 24) & 0xFF,
        ])
    return bytes(data)


def encode_mem_abs16_alu_reg(mnem: str, size: int, addr16: int, reg: str) -> bytes:
    """ALU (abs16), R — zz_mem ABS_W form with register source."""
    mnem = mnem.upper()
    if mnem not in _MEM_REG_BASE:
        raise ValueError(f"mem abs16 ALU reg: unsupported mnemonic '{mnem}'")
    reg_tab = _reg_table_for_size(size)
    reg_up = reg.upper()
    if reg_up not in reg_tab:
        raise ValueError(f"mem abs16 ALU reg: register '{reg}' does not match size {size}")
    prefix = _abs16_mem_prefix(size)
    op = _MEM_REG_BASE[mnem] + reg_tab[reg_up]
    return bytes([prefix, addr16 & 0xFF, (addr16 >> 8) & 0xFF, op])


def encode_mem_abs16_inc_dec(mnem: str, size: int, addr16: int, count: int) -> bytes:
    """INC/DEC n,(abs16) — zz_mem ABS_W form."""
    if count < 1 or count > 8:
        raise ValueError(f"{mnem}: count {count} out of range 1..8")
    prefix = _abs16_mem_prefix(size)
    count_code = 0 if count == 8 else count
    base = 0x60 if mnem.upper() == 'INC' else 0x68
    return bytes([prefix, addr16 & 0xFF, (addr16 >> 8) & 0xFF, base + count_code])


# ---------------------------------------------------------------------------
# C8+zz+r family — arithmetic, logic, shifts, bit ops
# Source: Toshiba TLCS-900/L1 Datasheet (900L1 Instruction Lists 1-10/10)
# ---------------------------------------------------------------------------

# zz offsets for the C8+zz+r prefix
_ZZ_BYTE = 0x00   # C8..CF
_ZZ_WORD = 0x08   # D0..D7
_ZZ_LONG = 0x10   # D8..DF

# Phase 5 (2026-06-22): opt-in re-base of 32-bit r+r ALU from the silicon-broken
# D8..DF encoding to the CC900-proven E8..EF encoding. Default OFF → byte-
# identical output. Gated by the same env var as t900cc's native-32-bit codegen
# so the build environment turns BOTH on together. See encode_alu_r_r.
_ALU32_E_ENCODING = os.environ.get('T900CC_C5_ALU32', '0') != '0'

# combined R-tables for lookup
_ALL_REGS = {}
for _n, _i in R8_REGS.items():
    _ALL_REGS[_n] = (_i, _ZZ_BYTE, 1)   # (index, zz, imm_size)
for _n, _i in R16_REGS.items():
    _ALL_REGS[_n] = (_i, _ZZ_WORD, 2)
for _n, _i in R32_REGS.items():
    _ALL_REGS[_n] = (_i, _ZZ_LONG, 4)


def _c8_prefix(reg: str) -> tuple[int, int]:
    """Return (prefix_byte, imm_size) for C8+zz+r prefix given a register name.
    imm_size = 1/2/4 bytes for byte/word/long immediates.
    """
    key = reg.upper()
    if key not in _ALL_REGS:
        raise ValueError(f"Unknown register '{reg}' for C8+zz+r encoding")
    idx, zz, imm_sz = _ALL_REGS[key]
    return (0xC8 + zz + idx, imm_sz)


def _emit_imm(val: int, size: int) -> bytes:
    """Little-endian immediate, size=1/2/4 bytes."""
    return val.to_bytes(size, 'little', signed=False)


def encode_alu_r_imm(sub_op: int, reg: str, imm: int) -> bytes:
    """C8+zz+r : sub_op : imm[size]  — ALU immediate operations.
    sub_op: C8=ADD, C9=ADC, CA=SUB, CB=SBC, CC=AND, CD=XOR, CE=OR, CF=CP
    """
    prefix, imm_sz = _c8_prefix(reg)
    imm &= (1 << (imm_sz * 8)) - 1
    return bytes([prefix, sub_op]) + _emit_imm(imm, imm_sz)


def encode_alu_r_r(sub_op_base: int, dest: str, src: str) -> bytes:
    """C8+zz+src : (sub_op_base + R_dest)  — ALU register-register.
    sub_op_base: 80=ADD, 90=ADC, A0=SUB, B0=SBC, C0=AND, D0=XOR, E0=OR, F0=CP
    First byte encodes the SOURCE register (src), second byte dest.
    """
    src_key = src.upper()
    dest_key = dest.upper()
    if src_key not in _ALL_REGS or dest_key not in _ALL_REGS:
        raise ValueError(f"Unknown register in ALU R,r: dest='{dest}' src='{src}'")
    src_idx, src_zz, _ = _ALL_REGS[src_key]
    dest_idx, dest_zz, _ = _ALL_REGS[dest_key]
    if src_zz != dest_zz:
        raise ValueError(f"Size mismatch: {dest}({src_zz}) vs {src}({dest_zz})")
    prefix = 0xC8 + src_zz + src_idx
    if (_ALU32_E_ENCODING and src_zz == _ZZ_LONG and sub_op_base != 0xF0):
        # Phase 5 (2026-06-22): 32-bit r+r ALU at the D8..DF base (0xC8+0x10)
        # HANGS the CPU on real NGPC silicon (USER_MANUAL_EN.md §12.1 + emulator
        # quirk cpu.d8_df_register_to_register). CC900 emits the IDENTICAL ops
        # at the E8..EF base and ships them in commercial ROMs (verified:
        # `add XWA,XDE` = EA 80, `xor XWA,XBC` = E9 D0, decoded clean by the
        # emulator). Re-base D8..DF -> E8..EF (+0x10) = the silicon-safe form.
        # CP (sub_op_base 0xF0) is EXCLUDED: `cp XWA,XHL` at D8 is the documented
        # SAFE exception (§12.1) and is ALREADY emitted by t900cc for 32-bit
        # comparisons — re-basing it would needlessly change existing output.
        # GATED behind T900CC_C5_ALU32 (default OFF) so default builds stay
        # byte-identical: a handwritten `add xde,xix` in ngpc_flash_asm.asm
        # currently assembles to the broken DC 82 — re-basing it is a fix but
        # changes bytes, so it ships only with the opt-in alu32 build (for HW
        # validation alongside t900cc's native 32-bit codegen).
        prefix += 0x10
    return bytes([prefix, sub_op_base + dest_idx])


def encode_ld_r_r(dest: str, src: str) -> bytes:
    """LD dest, src (register to register).
    LD R, r = C8+zz+r : 88+R  (source in first byte, dest in second)
    LD r, R = C8+zz+r : 98+R  (dest in first byte, src in second)
    We always use LD R, r form (88+R).
    """
    src_key = src.upper()
    dest_key = dest.upper()
    if src_key not in _ALL_REGS or dest_key not in _ALL_REGS:
        raise ValueError(f"Unknown register in LD r,r: dest='{dest}' src='{src}'")
    src_idx, src_zz, _ = _ALL_REGS[src_key]
    dest_idx, dest_zz, _ = _ALL_REGS[dest_key]
    if src_zz != dest_zz:
        raise ValueError(f"Size mismatch: {dest}({src_zz}) vs {src}({dest_zz})")
    prefix = 0xC8 + src_zz + src_idx
    return bytes([prefix, 0x88 + dest_idx])


def encode_inc_r(reg: str, n: int = 1) -> bytes:
    """INC n, r = C8+zz+r : 0x60+n  (n=1..8, 0→8)"""
    if not (0 <= n <= 8):
        raise ValueError(f"INC step {n} out of range [0, 8] (0 means 8)")
    if n == 8:
        n = 0
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, 0x60 + n])


def encode_dec_r(reg: str, n: int = 1) -> bytes:
    """DEC n, r = C8+zz+r : 0x68+n  (n=1..8, 0→8)"""
    if not (0 <= n <= 8):
        raise ValueError(f"DEC step {n} out of range [0, 8] (0 means 8)")
    if n == 8:
        n = 0
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, 0x68 + n])


def encode_neg_r(reg: str) -> bytes:
    """NEG r = C8+zz+r : 07  (r = 0 - r)"""
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, 0x07])


def encode_cpl_r(reg: str) -> bytes:
    """CPL r = C8+zz+r : 06  (r = NOT r)"""
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, 0x06])


def encode_extz_r(reg: str) -> bytes:
    """EXTZ r = zero-extend.
    For R32 (XWA..XSP): uses E8+r form confirmed safe from cc900 reverse-engineering.
      extz XWA=E8 12, extz XHL=EB 12, etc.
    For R8 (byte→word): uses C8+zz+r : 12 (C8..CF range, safe).
    NOTE: R16 form (word→long) would be C8+0x08+r = D0..D7, broken on NGPC — avoid.
    """
    key = reg.upper()
    if key in R32_REGS:
        return bytes([0xE8 + R32_REGS[key], 0x12])
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, 0x12])


def encode_exts_r(reg: str) -> bytes:
    """EXTS r = C8+zz+r : 13  (sign-extend)"""
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, 0x13])


def encode_shift_r_imm(sub_op: int, reg: str, count: int) -> bytes:
    """Shift/rotate by immediate count: C8+zz+r : sub_op : count
    sub_op: E8=RLC, E9=RRC, EA=RL, EB=RR, EC=SLA, ED=SRA, EE=SLL, EF=SRL
    count is 4-bit (0=16 shifts, 1..15)
    """
    prefix, _ = _c8_prefix(reg)
    count = count & 0x0F
    return bytes([prefix, sub_op, count])


def encode_shift_r_a(sub_op: int, reg: str) -> bytes:
    """Shift/rotate by A register: C8+zz+r : sub_op
    sub_op: F8=RLC A, F9=RRC A, FA=RL A, FB=RR A, FC=SLA A, FD=SRA A, FE=SLL A, FF=SRL A
    """
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, sub_op])


def encode_djnz_r(reg: str, disp8: int) -> bytes:
    """DJNZ r, $+3+d8 = C8+zz+r : 1C : d8  (r--, jump if r!=0)"""
    prefix, _ = _c8_prefix(reg)
    return bytes([prefix, 0x1C, disp8 & 0xFF])


def encode_link(reg: str, n: int) -> bytes:
    """LINK R32, N = (0xE8+R) 0x0C lo hi  (confirmed NGPC_REVERSE_REFERENCE §11.4, 8 games)
    Pushes R32, sets R32=XSP, then XSP -= N (allocates N bytes of locals).
    """
    r = R32_REGS[reg.upper()]
    return bytes([0xE8 + r, 0x0C, n & 0xFF, (n >> 8) & 0xFF])


def encode_unlk(reg: str) -> bytes:
    """UNLK R32 = (0xE8+R) 0x0D  (confirmed NGPC_REVERSE_REFERENCE §11.4, 8 games)
    Restores XSP=R32, then pops R32.
    """
    r = R32_REGS[reg.upper()]
    return bytes([0xE8 + r, 0x0D])


def encode_retd(d16: int) -> bytes:
    """RETD d16 = 0F lo hi  (return + add XSP, d16 — callee-cleans-up variant)"""
    return bytes([0x0F, d16 & 0xFF, (d16 >> 8) & 0xFF])


def encode_ldiw() -> bytes:
    """LDIW — single word copy: (XDE+) <- (XHL+), BC--.
    Encoding: 0x84, 0x10 (confirmed hardware bisect j8j).
    Uses: XHL=src, XDE=dst, BC=count (not used for single step)."""
    return bytes([0x84, 0x10])


def encode_ldirw() -> bytes:
    """LDIRW — repeat word copy until BC=0: (XDE+) <- (XHL+), BC-- while BC!=0.
    Encoding: 0x95, 0x11 (confirmed hardware: Ganbare 0x13B1, Pocket Tennis OAM).
    WARNING: BC=0 at entry copies 65536 words — guard with zero-check before calling.
    Uses: XHL=src, XDE=dst, BC=word count."""
    return bytes([0x95, 0x11])


# TLCS-900/H control register numbers (from ngdis tlcs900statics.c cr_names[])
# LDC cr, r  encoding: [C8+zz+r_idx][2E][cr_number]
#   zz=0x00 byte  (r in R8_REGS)  → C8..CF (ldcb)
#   zz=0x10 word  (r in R16_REGS) → D8..DF (ldcw)  ⚠ only 16-bit write; old zz=0x08 gave D0..D7 (broken silicon)
#   zz=0x20 lword (r in R32_REGS) → E8..EF (ldcl)  ✓ CC900 uses E8 2E 00 for DMAS0/DMAD0
_CR_NAMES: dict[str, int] = {
    # DMA source address (32-bit)
    'DMAS0': 0x00, 'DMAS1': 0x04, 'DMAS2': 0x08, 'DMAS3': 0x0C,
    # DMA destination address (32-bit)
    'DMAD0': 0x10, 'DMAD1': 0x14, 'DMAD2': 0x18, 'DMAD3': 0x1C,
    # DMA count (16-bit) and mode (8-bit)
    'DMAC0': 0x20, 'DMAM0': 0x22,
    'DMAC1': 0x24, 'DMAM1': 0x26,
    'DMAC2': 0x28, 'DMAM2': 0x2A,
    'DMAC3': 0x2C, 'DMAM3': 0x2E,
    # Interrupt nesting level
    'INTNEST': 0x30,
}


def encode_ldc(op1: str, op2: str) -> bytes:
    """LDC cr, r  (write CR, opcode 0x2E)  or  LDC r, cr  (read CR, opcode 0x2F).
    Encoding: [C8+zz+r_idx][dir][cr_number]
      dir=0x2E → write: ldc cr, r  (cr first, reg second)
      dir=0x2F → read:  ldc r, cr  (reg first, cr second)
    zz values (from TLCS-900/L1 catalog 1 1 z z 1 r r r pattern):
      R8  → zz=0x00 → C8..CF (byte)      e.g. ldcb dmam0, a   → C9 2E 22
      R16 → zz=0x10 → D8..DF (word)      e.g. ldcw dmac0, wa  → D8 2E 20  (⚠ only 16-bit write; old bug: zz=0x08 gave D0=broken)
      R32 → zz=0x20 → E8..EF (long word) e.g. ldcl dmas0, xwa → E8 2E 00  (✓ CC900 uses E8 2E 00)
    Fix #27 (2026-04-01): DMACn is a 16-bit CR → ldcw (D8 2E xx) is correct.  CC900 uses D8 2E 20.
    ldcl (E8) on DMACn silently fails on NGPC silicon (count=0, no DMA transfers)."""
    op1u = op1.strip().upper()
    op2u = op2.strip().upper()
    # Determine direction: whichever operand is a CR name
    if op1u in _CR_NAMES and op2u not in _CR_NAMES:
        cr_up, reg_up, direction = op1u, op2u, 0x2E   # write: ldc cr, r
    elif op2u in _CR_NAMES and op1u not in _CR_NAMES:
        reg_up, cr_up, direction = op1u, op2u, 0x2F   # read: ldc r, cr
    else:
        raise ValueError(f"LDC: cannot determine direction from '{op1}', '{op2}'")
    cr_num = _CR_NAMES[cr_up]
    if reg_up in R8_REGS:
        zz = 0x00; r_idx = R8_REGS[reg_up]
    elif reg_up in R16_REGS:
        zz = 0x10; r_idx = R16_REGS[reg_up]
        # NGPC silicon bug: R16 write prefix byte = C8+0x10+r = 0xD8..0xDF.
        # WA (r=0) → D8+0=D8, which is safe. Old wrong zz=0x08 gave D0=broken. Now fixed.
        # Fix #27: ldcw is CORRECT for DMACn (16-bit count register) — matches CC900 D8 2E xx.
        #          Only DMAS/DMAD (24-bit address registers) require ldcl (R32, E8).
        _32BIT_CRS = {'DMAS0','DMAS1','DMAS2','DMAS3','DMAD0','DMAD1','DMAD2','DMAD3'}
        if direction == 0x2E and cr_up in _32BIT_CRS:
            import warnings
            warnings.warn(
                f"LDC write with R16 register '{reg_up}' to 32-bit CR '{cr_up}': "
                f"only low 16 bits written — address will be truncated. "
                f"Use R32 (ldcl): extz xwa + ldcl {op1}, xwa",
                stacklevel=3
            )
    elif reg_up in R32_REGS:
        zz = 0x20; r_idx = R32_REGS[reg_up]
        # Fix #27: DMACn is a 16-bit count register.  CC900 writes it with ldcw (D8 2E xx).
        # Using ldcl (E8 2E xx) on DMACn silently fails on NGPC silicon → count=0 → no DMA.
        # When the source says `ldcl dmacN, xrr`, force the 16-bit encoding (zz=0x10).
        # R32 and R16 registers share the same index (XWA→WA idx=0, XBC→BC idx=1, …) so
        # r_idx stays the same; only zz changes from 0x20 to 0x10.
        _16BIT_CRS = {'DMAC0','DMAC1','DMAC2','DMAC3'}
        if direction == 0x2E and cr_up in _16BIT_CRS:
            zz = 0x10   # emit D8..DF (ldcw) — matches CC900 D8 2E xx for DMACn
    else:
        raise ValueError(f"Unknown register '{reg_up}' for LDC")
    return bytes([0xC8 + zz + r_idx, direction, cr_num])


# ---------------------------------------------------------------------------
# Section
# ---------------------------------------------------------------------------
class Section:
    def __init__(self, name: str, stype: str, org: int | None = None, displacement: str = 'large'):
        self.name = name
        self.stype = stype          # 'code', 'data', 'romdata'
        self.org = org              # absolute start address (None = relocatable)
        self.displacement = displacement
        self.data = bytearray()
        self.patches = []           # list of (offset, type, sym_name, instr_end_offset)

    @property
    def current_address(self) -> int:
        if self.org is None:
            return len(self.data)
        return self.org + len(self.data)

    def emit(self, *args):
        for a in args:
            if isinstance(a, (bytes, bytearray)):
                self.data.extend(a)
            elif isinstance(a, int):
                self.data.append(a & 0xFF)
            else:
                raise TypeError(f"Cannot emit {type(a)}")

    def reserve(self, n: int):
        self.data.extend(b'\x00' * n)

    def align_to(self, boundary: int):
        rem = len(self.data) % boundary
        if rem:
            self.data.extend(b'\x00' * (boundary - rem))

    def add_patch(self, offset: int, ptype: str, sym: str, instr_end: int):
        """Register a relocation patch for pass 2."""
        self.patches.append((offset, ptype, sym, instr_end))


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------
class Assembler:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.sections: dict[str, Section] = {}
        self.section_order: list[str] = []
        self.current_section: Section | None = None
        self.symbols: dict[str, int] = {}       # name -> absolute address
        self.sym_sections: dict[str, str] = {}  # name -> section name (for linker disambiguation)
        self.publics: set[str] = set()
        self.externs: dict[str, str] = {}       # name -> displacement
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._filename = "<unknown>"
        self._lineno = 0

    # -----------------------------------------------------------------------
    # Error / warning
    # -----------------------------------------------------------------------
    def _error(self, msg: str):
        self.errors.append(f"{self._filename}:{self._lineno}: ERROR: {msg}")

    def _warn(self, msg: str):
        self.warnings.append(f"{self._filename}:{self._lineno}: WARNING: {msg}")

    def _require_section(self) -> bool:
        if self.current_section is None:
            self._error("Instruction or data outside any section")
            return False
        return True

    # -----------------------------------------------------------------------
    # Number parsing
    # -----------------------------------------------------------------------
    def _parse_int(self, s: str) -> int:
        try:
            return eval_expr(s, self.symbols, self.current_section.current_address if self.current_section else 0)
        except UnresolvedSymbol as e:
            raise
        except Exception as e:
            raise ValueError(f"Cannot parse '{s}': {e}")

    # -----------------------------------------------------------------------
    # Pass 1 + 2 combined (two-pass via placeholder)
    # -----------------------------------------------------------------------
    def assemble_file(self, filename: str, obj_mode: bool = False):
        self._filename = filename
        with open(filename, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        self._pass1(lines)
        if self.errors:
            return
        self._pass2(obj_mode=obj_mode)

    def serialize_obj(self, source_path: str) -> dict:
        """Serialize assembled state to .t9obj dict (JSON-serialisable).

        Format v1:
          version    : 1
          source     : original .asm path
          sections   : list of section dicts
          symbols    : {name: abs_addr}  — locals + publics (not externs)
          publics    : [name, ...]
          externs    : {name: displacement}
        """
        import os
        secs = []
        for name in self.section_order:
            sec = self.sections[name]
            patches = [
                {"offset": off, "ptype": pt, "sym": sym, "instr_end": ie}
                for (off, pt, sym, ie) in sec.patches
            ]
            secs.append({
                "name": sec.name,
                "stype": sec.stype,
                "org": sec.org,          # None = relocatable
                "displacement": sec.displacement,
                "data": sec.data.hex(),  # hex string, little-endian bytes
                "patches": patches,
            })
        return {
            "version": 1,
            "source": os.path.basename(source_path),
            "sections": secs,
            "symbols": dict(self.symbols),
            "sym_secs": dict(self.sym_sections),   # name -> section_name for linker
            "publics": sorted(self.publics),
            "externs": dict(self.externs),
        }

    def _pass1(self, lines: list[str]):
        """Pass 1: collect symbols, emit code with placeholders for forward refs."""
        for lineno, raw_line in enumerate(lines, 1):
            self._lineno = lineno
            self._process_line(raw_line, pass_num=1)

    def _expand_jr_patches(self) -> bool:
        """Pre-pass: expand out-of-range 'jrl cc, label' to 'jrl ~cc, +4; jp label' (6 bytes).
        A conditional JR has ±127 byte range.  When a forward label is farther away,
        the JR is replaced with an inverted-condition short skip + absolute JP.

        Inverted condition: opcode in [0x60,0x6F]; bit 3 inverts the condition
        (e.g. Z=0x66 → NZ=0x6E, C=0x67 → NC=0x6F, T=0x68 → F=0x60, etc.)

        Layout of the 6-byte replacement at <offset>:
          offset+0 : jrl ~cc opcode
          offset+1 : 0x04  (skip 4 bytes forward = skip the JP below)
          offset+2 : 0x1B  (JP opcode)
          offset+3..5 : target address (patched by JP_ABS24 later)

        After expansion, all patch offsets and section-relative symbols that lie
        AFTER the insertion point are shifted by +4.  Iterates until stable
        (one expansion can bring a previously in-range JR out of range).
        Only runs on relocatable sections (sec.org is None).
        """
        any_changed = False
        for sec in self.sections.values():
            if sec.org is not None:
                continue  # absolute sections: layout is fixed, don't touch
            changed = True
            while changed:
                changed = False
                for (offset, ptype, sym, ier) in sec.patches:
                    if ptype != 'JR_REL8' or sym not in self.symbols:
                        continue
                    target = self.symbols[sym]
                    # sec.org is None → instr_end_abs = 0 + ier
                    disp = target - ier
                    if -128 <= disp <= 127:
                        continue  # still in range
                    # Out of range: expand this JR
                    cc_byte = sec.data[offset]
                    if not (0x60 <= cc_byte <= 0x6F):
                        self._error(
                            f"JR expansion: opcode 0x{cc_byte:02X} at offset {offset:#x} "
                            f"is not a conditional JR (range 0x60-0x6F) — cannot expand"
                        )
                        break
                    inv_cc = 0x60 | ((cc_byte - 0x60) ^ 8)
                    # Replace 2 bytes with 6: [inv_cc, 0x04, 0x1B, 0, 0, 0]
                    sec.data[offset:offset + 2] = bytearray(
                        [inv_cc, 0x04, 0x1B, 0x00, 0x00, 0x00]
                    )
                    # Rebuild patches: replace current JR_REL8 → JP_ABS24 at offset+2;
                    # shift all other patches that lie after the insertion point by +4.
                    new_patches = []
                    for (po, ptp, ps, pier) in sec.patches:
                        if po == offset and ptp == 'JR_REL8' and ps == sym:
                            new_patches.append((offset + 2, 'JP_ABS24', sym, offset + 6))
                        else:
                            new_po   = po   + (4 if po   > offset else 0)
                            new_pier = pier + (4 if pier > offset else 0)
                            new_patches.append((new_po, ptp, ps, new_pier))
                    sec.patches = new_patches
                    # Shift section-relative symbol addresses that lie after offset by +4
                    for name, sn in list(self.sym_sections.items()):
                        if sn == sec.name:
                            sv = self.symbols.get(name)
                            if sv is not None and sv > offset:
                                self.symbols[name] = sv + 4
                    any_changed = True
                    changed = True
                    break  # restart scan from scratch after modification
        return any_changed

    def _expand_calr_patches(self) -> bool:
        """Relax out-of-range CALR to CALL in relocatable sections."""
        any_changed = False
        for sec in self.sections.values():
            if sec.org is not None:
                continue
            changed = True
            while changed:
                changed = False
                for (offset, ptype, sym, ier) in sec.patches:
                    if ptype != 'CALR_REL16' or sym not in self.symbols:
                        continue
                    target = self.symbols[sym]
                    disp = target - ier
                    if -32768 <= disp <= 32767:
                        continue
                    sec.data[offset:offset + 3] = bytearray([0x1D, 0x00, 0x00, 0x00])
                    new_patches = []
                    for (po, ptp, ps, pier) in sec.patches:
                        if po == offset and ptp == 'CALR_REL16' and ps == sym:
                            new_patches.append((offset, 'CALL_ABS24', sym, offset + 4))
                        else:
                            new_po = po + (1 if po > offset else 0)
                            new_pier = pier + (1 if pier > offset else 0)
                            new_patches.append((new_po, ptp, ps, new_pier))
                    sec.patches = new_patches
                    for name, sn in list(self.sym_sections.items()):
                        if sn == sec.name:
                            sv = self.symbols.get(name)
                            if sv is not None and sv > offset:
                                self.symbols[name] = sv + 1
                    any_changed = True
                    changed = True
                    break
        return any_changed

    def _relax_jp_patches(self) -> bool:
        """Relax same-section JP to JRL/JR in relocatable sections."""
        any_changed = False
        for sec in self.sections.values():
            if sec.org is not None:
                continue
            changed = True
            while changed:
                changed = False
                for (offset, ptype, sym, ier) in sec.patches:
                    if ptype != 'JP_ABS24' or sym not in self.symbols:
                        continue
                    if self.sym_sections.get(sym) != sec.name:
                        continue
                    target = self.symbols[sym]
                    disp8 = target - (offset + 2)
                    if -128 <= disp8 <= 127:
                        sec.data[offset:offset + 4] = bytearray([0x68, 0x00])
                        delta = -2
                        repl = (offset, 'JR_REL8', sym, offset + 2)
                    else:
                        disp16 = target - (offset + 3)
                        if disp16 < -32768 or disp16 > 32767:
                            continue
                        sec.data[offset:offset + 4] = bytearray([0x78, 0x00, 0x00])
                        delta = -1
                        repl = (offset, 'JRL_REL16', sym, offset + 3)
                    new_patches = []
                    for (po, ptp, ps, pier) in sec.patches:
                        if po == offset and ptp == 'JP_ABS24' and ps == sym:
                            new_patches.append(repl)
                        else:
                            new_po = po + (delta if po > offset else 0)
                            new_pier = pier + (delta if pier > offset else 0)
                            new_patches.append((new_po, ptp, ps, new_pier))
                    sec.patches = new_patches
                    for name, sn in list(self.sym_sections.items()):
                        if sn == sec.name:
                            sv = self.symbols.get(name)
                            if sv is not None and sv > offset:
                                self.symbols[name] = sv + delta
                    any_changed = True
                    changed = True
                    break
        return any_changed

    def _relax_jrl_to_jr_patches(self) -> bool:
        """P3 (2026-04-20): relax same-section JRL (3 B) to JR (2 B) when the
        signed displacement fits in ±127. Mirror of _relax_jp_patches.

        Encoding:
          JRL cc d16 = (0x70+cc) lo hi    — 3 bytes, disp = target - (offset+3)
          JR  cc d8  = (0x60+cc) disp8    — 2 bytes, disp = target - (offset+2)

        CC codes are identical in JR and JRL (low 4 bits), so new_opcode =
        jrl_opcode - 0x10.
        """
        any_changed = False
        for sec in self.sections.values():
            if sec.org is not None:
                continue  # only relax in relocatable sections
            changed = True
            while changed:
                changed = False
                for (offset, ptype, sym, ier) in sec.patches:
                    if ptype != 'JRL_REL16' or sym not in self.symbols:
                        continue
                    if self.sym_sections.get(sym) != sec.name:
                        continue
                    target = self.symbols[sym]
                    # Disp from new (2-byte) instruction end would be target-(offset+2)
                    disp8 = target - (offset + 2)
                    if not (-128 <= disp8 <= 127):
                        continue
                    jrl_opcode = sec.data[offset]
                    if not (0x70 <= jrl_opcode <= 0x7F):
                        continue  # not a JRL opcode as expected — skip safely
                    jr_opcode = jrl_opcode - 0x10
                    # Shrink from 3 B to 2 B: [opcode, disp_placeholder]
                    sec.data[offset:offset + 3] = bytearray([jr_opcode, 0x00])
                    delta = -1
                    repl = (offset, 'JR_REL8', sym, offset + 2)
                    new_patches = []
                    for (po, ptp, ps, pier) in sec.patches:
                        if po == offset and ptp == 'JRL_REL16' and ps == sym:
                            new_patches.append(repl)
                        else:
                            new_po = po + (delta if po > offset else 0)
                            new_pier = pier + (delta if pier > offset else 0)
                            new_patches.append((new_po, ptp, ps, new_pier))
                    sec.patches = new_patches
                    for name, sn in list(self.sym_sections.items()):
                        if sn == sec.name:
                            sv = self.symbols.get(name)
                            if sv is not None and sv > offset:
                                self.symbols[name] = sv + delta
                    any_changed = True
                    changed = True
                    break
        return any_changed

    def _relax_call_to_calr_patches(self) -> bool:
        """P3 (2026-04-20): relax same-section CALL abs24 (4 B) to CALR rel16
        (3 B) when the signed displacement fits in ±32 767. Mirror of
        _relax_jp_patches for calls.

        Encoding:
          CALL addr24 = 0x1D lo mid hi   — 4 bytes
          CALR  d16   = 0x1E lo hi       — 3 bytes, disp = target - (offset+3)
        """
        any_changed = False
        for sec in self.sections.values():
            if sec.org is not None:
                continue
            changed = True
            while changed:
                changed = False
                for (offset, ptype, sym, ier) in sec.patches:
                    if ptype != 'CALL_ABS24' or sym not in self.symbols:
                        continue
                    if self.sym_sections.get(sym) != sec.name:
                        continue
                    target = self.symbols[sym]
                    disp16 = target - (offset + 3)
                    if not (-32768 <= disp16 <= 32767):
                        continue
                    # Shrink from 4 B to 3 B: [0x1E, disp_lo, disp_hi]
                    sec.data[offset:offset + 4] = bytearray([0x1E, 0x00, 0x00])
                    delta = -1
                    repl = (offset, 'CALR_REL16', sym, offset + 3)
                    new_patches = []
                    for (po, ptp, ps, pier) in sec.patches:
                        if po == offset and ptp == 'CALL_ABS24' and ps == sym:
                            new_patches.append(repl)
                        else:
                            new_po = po + (delta if po > offset else 0)
                            new_pier = pier + (delta if pier > offset else 0)
                            new_patches.append((new_po, ptp, ps, new_pier))
                    sec.patches = new_patches
                    for name, sn in list(self.sym_sections.items()):
                        if sn == sec.name:
                            sv = self.symbols.get(name)
                            if sv is not None and sv > offset:
                                self.symbols[name] = sv + delta
                    any_changed = True
                    changed = True
                    break
        return any_changed

    def _pass2(self, obj_mode: bool = False):
        """Pass 2: resolve all patches.
        In obj_mode, EXTERN symbols are left unresolved (patched with 0-placeholder).
        """
        while True:
            changed = False
            if self._expand_calr_patches():
                changed = True
            if self._expand_jr_patches():
                changed = True
            if not changed:
                break
        # P3 fixed-point relaxation loop: each shrink may bring other branches
        # into a shorter-form range. Run all three relaxations until stable.
        while True:
            changed = False
            if self._relax_jp_patches():
                changed = True
            if self._relax_jrl_to_jr_patches():
                changed = True
            if self._relax_call_to_calr_patches():
                changed = True
            if not changed:
                break
        for sec in self.sections.values():
            for (offset, ptype, sym, instr_end_rel) in sec.patches:
                if sym not in self.symbols:
                    if obj_mode and sym in self.externs:
                        # Leave placeholder zeros; linker will fill in
                        continue
                    self._error(f"Undefined symbol '{sym}' in section '{sec.name}' at offset {offset:#x}")
                    continue
                target = self.symbols[sym]
                if ptype in ('JP_ABS24', 'CALL_ABS24'):
                    # Patch 3-byte address at offset+1 (JP or CALL absolute 24-bit)
                    addr = target & 0xFFFFFF
                    sec.data[offset + 1] = addr & 0xFF
                    sec.data[offset + 2] = (addr >> 8) & 0xFF
                    sec.data[offset + 3] = (addr >> 16) & 0xFF
                elif ptype in ('CALR_REL16', 'JRL_REL16'):
                    # Patch 2-byte signed displacement at offset+1
                    instr_end_abs = (sec.org or 0) + instr_end_rel
                    disp = target - instr_end_abs
                    if disp < -32768 or disp > 32767:
                        self._error(
                            f"{ptype} displacement to '{sym}' = {disp} out of range [-32768, 32767]"
                        )
                        continue
                    sec.data[offset + 1] = disp & 0xFF
                    sec.data[offset + 2] = (disp >> 8) & 0xFF
                elif ptype in ('JR_REL8', 'DJNZ_REL8'):
                    # Patch 1-byte displacement at offset+1 (JR) or offset+2 (DJNZ)
                    instr_end_abs = (sec.org or 0) + instr_end_rel
                    disp = target - instr_end_abs
                    if disp < -128 or disp > 127:
                        self._error(
                            f"{ptype} displacement to '{sym}' = {disp} out of range [-128, 127] "
                            f"(from 0x{instr_end_abs:06X} to 0x{target:06X})"
                        )
                        continue
                    if ptype == 'JR_REL8':
                        sec.data[offset + 1] = disp & 0xFF
                    else:  # DJNZ_REL8: disp is at offset+2
                        sec.data[offset + 2] = disp & 0xFF
                elif ptype in ('ABS8', 'ABS16', 'ABS24', 'ABS32'):
                    # Generic absolute address patches from DB/DW/DD directives
                    nbytes = int(ptype[3:]) // 8
                    addr = target & ((1 << (nbytes * 8)) - 1)
                    for i in range(nbytes):
                        sec.data[offset + i] = (addr >> (8 * i)) & 0xFF
                elif ptype == 'LD_R32_SYM':
                    # LD r32, imm32 — patch 4-byte immediate (same layout as ABS32)
                    addr = target & 0xFFFFFFFF
                    for i in range(4):
                        sec.data[offset + i] = (addr >> (8 * i)) & 0xFF
                elif ptype == 'LD_R16_SYM':
                    # LD r16, imm16 — patch 2-byte immediate (same layout as ABS16)
                    addr = target & 0xFFFF
                    sec.data[offset]     = addr & 0xFF
                    sec.data[offset + 1] = (addr >> 8) & 0xFF
                else:
                    self._error(f"Unknown patch type '{ptype}'")

    # -----------------------------------------------------------------------
    # Line processor
    # -----------------------------------------------------------------------
    def _process_line(self, raw_line: str, pass_num: int):
        # Strip comment and whitespace
        line = raw_line
        # Double-semicolon: strip entirely
        line = re.sub(r';;.*$', '', line)
        # Single semicolon: strip comment
        line = re.sub(r';.*$', '', line)
        line = line.strip()
        if not line:
            return

        # Check for label (line starts with a token followed by ':')
        # Local labels can start with '.' (e.g. .Lloop:)
        label = None
        m = re.match(r'^([.A-Za-z_][A-Za-z0-9_.]*)\s*:(.*)', line)
        if m:
            label = m.group(1)
            line = m.group(2).strip()

        # Record label
        if label:
            if self.current_section is None:
                # Label before any section — store as None until section defined
                pass
            else:
                addr = self.current_section.current_address
                if label in self.symbols and self.symbols[label] != addr:
                    self._warn(f"Label '{label}' redefined (was 0x{self.symbols[label]:06X}, now 0x{addr:06X})")
                self.symbols[label] = addr
                self.sym_sections[label] = self.current_section.name
                if self.verbose:
                    print(f"  LABEL {label} = 0x{addr:06X}")

        if not line:
            return

        # Split into tokens
        tokens = line.split(None, 1)
        if not tokens:
            return
        directive = tokens[0].upper()
        rest = tokens[1].strip() if len(tokens) > 1 else ""

        self._dispatch(directive, rest, pass_num)

    # -----------------------------------------------------------------------
    # Directive / instruction dispatcher
    # -----------------------------------------------------------------------
    def _dispatch(self, directive: str, rest: str, pass_num: int):
        d = directive

        # --- Assembler control ---
        if d in ('$MAXIMUM', 'MODULE', 'END'):
            return  # ignored in flat binary mode

        # --- Section definition: "name section type [...]" ---
        # Toshiba syntax has the section NAME as first token, then keyword 'section'
        if rest.upper().startswith('SECTION ') or rest.upper() == 'SECTION':
            # directive = section_name, rest = "section type [...]"
            rest_after = rest.split(None, 1)[1].strip() if ' ' in rest else ''
            self._handle_section(directive + ' ' + rest_after)
            return

        if d == 'PUBLIC':
            for sym in re.split(r'[,\s]+', rest):
                sym = sym.strip()
                if sym:
                    self.publics.add(sym)
            return

        if d == 'EXTERN':
            parts = re.split(r'[,\s]+', rest)
            disp = 'large'
            symbols = []
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                if p.lower() in ('tiny', 'small', 'medium', 'large'):
                    disp = p.lower()
                else:
                    symbols.append(p)
                    self.externs[p] = disp
            return

        # --- Section definition ---
        if d == 'SECTION':
            self._handle_section(rest)
            return

        # --- EQU ---
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_.]*)$', directive)
        # (handled as stand-alone directive below)

        if d == 'EQU':
            # Should not reach here — equ is parsed as "NAME equ VALUE" where
            # NAME is already consumed as a 'label'. But handle both forms.
            self._error("EQU without a name — use: NAME equ VALUE")
            return

        # Handle "NAME equ VALUE" where NAME was parsed as directive
        # (when there's no colon after name, e.g. "CONST equ 5")
        if rest.upper().startswith('EQU ') or rest.upper() == 'EQU':
            val_str = rest[3:].strip() if len(rest) > 3 else ""
            try:
                val = self._parse_int(val_str)
                self.symbols[directive] = val
                # Also check if originally from label parse
            except UnresolvedSymbol as e:
                self._warn(f"EQU '{directive}': symbol '{e}' not yet defined (forward ref not supported for EQU)")
            except Exception as e:
                self._error(f"EQU '{directive}': {e}")
            return

        # --- Alignment / origin ---
        if d == 'ALIGN':
            if not self._require_section():
                return
            try:
                boundary = self._parse_int(rest)
                self.current_section.align_to(boundary)
            except Exception as e:
                self._error(f"ALIGN: {e}")
            return

        if d == 'ORG':
            if not self._require_section():
                return
            try:
                new_org = self._parse_int(rest)
                if self.current_section.org is None:
                    # Relocatable section: offset
                    offset = new_org
                    cur = len(self.current_section.data)
                    if offset < cur:
                        self._error(f"ORG 0x{new_org:X} would move backwards (current offset {cur:#x})")
                    else:
                        self.current_section.data.extend(b'\x00' * (offset - cur))
                else:
                    # Absolute section: fill to new address
                    cur = self.current_section.current_address
                    if new_org < cur:
                        self._error(f"ORG 0x{new_org:X} would move backwards (current addr 0x{cur:06X})")
                    else:
                        self.current_section.data.extend(b'\x00' * (new_org - cur))
            except Exception as e:
                self._error(f"ORG: {e}")
            return

        # --- Data directives ---
        if d in ('DB', 'DW', 'DD', 'DL', 'DP'):
            self._handle_data(d, rest)
            return

        if d in ('DSB', 'DSW', 'DSD', 'DSL', 'DSP'):
            self._handle_reserve(d, rest)
            return

        # --- Instructions ---
        self._handle_instruction(directive, rest)

    # -----------------------------------------------------------------------
    # Section handler
    # -----------------------------------------------------------------------
    def _handle_section(self, rest: str):
        """Parse: NAME section TYPE [DISPLACEMENT] [abs=N | align=S,A]"""
        # Already split: rest = "NAME TYPE [...]" OR directive was inline
        # Actually at call site, 'directive' was 'SECTION' and 'rest' is the rest.
        # Format: <name> <code|data|romdata> [large|medium|small] [abs=N] [align=...]
        parts = rest.split()
        if len(parts) < 2:
            self._error(f"SECTION syntax: name type [displacement] [abs=addr] [align=s,a]")
            return

        sec_name = parts[0]
        sec_type = parts[1].lower()
        if sec_type not in ('code', 'data', 'romdata'):
            self._error(f"Unknown section type '{sec_type}' (expected code|data|romdata)")
            return

        displacement = 'large'
        org = None

        for part in parts[2:]:
            part_lower = part.lower()
            if part_lower in ('large', 'medium', 'small', 'tiny'):
                displacement = part_lower
            elif part_lower.startswith('abs='):
                try:
                    org = int(part[4:], 0)
                except ValueError:
                    self._error(f"Invalid abs address in: {part}")
            elif part_lower.startswith('align='):
                pass  # ignore align spec in pass1 (handled separately)

        if sec_name in self.sections:
            # Re-enter existing section
            self.current_section = self.sections[sec_name]
        else:
            sec = Section(sec_name, sec_type, org, displacement)
            self.sections[sec_name] = sec
            self.section_order.append(sec_name)
            self.current_section = sec

        if self.verbose:
            org_str = f"abs=0x{org:06X}" if org is not None else "relocatable"
            print(f"  SECTION {sec_name} ({sec_type}, {org_str})")

    # -----------------------------------------------------------------------
    # Data directives
    # -----------------------------------------------------------------------
    def _handle_data(self, directive: str, rest: str):
        if not self._require_section():
            return
        sizes = {'DB': 1, 'DW': 2, 'DP': 3, 'DD': 4, 'DL': 4}
        size = sizes[directive]

        # Parse comma-separated values; handle strings for DB
        items = self._split_data_args(rest)
        for item in items:
            item = item.strip()
            if not item:
                continue
            # String literal for DB
            if directive == 'DB' and item.startswith('"'):
                s = self._parse_string(item)
                self.current_section.emit(s.encode('ascii', errors='replace'))
            else:
                try:
                    patch_off = len(self.current_section.data)
                    val = self._parse_int(item)
                    self._emit_int(val, size)
                    if (self.current_section.org is None
                            and re.match(r'^[_A-Za-z][A-Za-z0-9_.]*$', item)):
                        self.current_section.add_patch(
                            patch_off, f'ABS{size*8}', item, patch_off + size
                        )
                except UnresolvedSymbol as sym_name:
                    # Forward reference — emit placeholder + patch
                    patch_off = len(self.current_section.data)
                    self._emit_int(0, size)
                    self.current_section.add_patch(patch_off, f'ABS{size*8}', str(sym_name), patch_off + size)
                except Exception as e:
                    self._error(f"{directive}: {e}")

    def _handle_reserve(self, directive: str, rest: str):
        if not self._require_section():
            return
        sizes = {'DSB': 1, 'DSW': 2, 'DSP': 3, 'DSD': 4, 'DSL': 4}
        unit = sizes[directive]
        try:
            count = self._parse_int(rest.strip())
            self.current_section.reserve(count * unit)
        except Exception as e:
            self._error(f"{directive}: {e}")

    def _emit_int(self, val: int, size: int):
        val &= (1 << (size * 8)) - 1
        for i in range(size):
            self.current_section.data.append((val >> (8 * i)) & 0xFF)

    def _split_data_args(self, s: str) -> list[str]:
        """Split comma-separated args, respecting string literals."""
        args = []
        current = ""
        in_string = False
        i = 0
        while i < len(s):
            c = s[i]
            if c == '"' and not in_string:
                in_string = True
                current += c
            elif c == '"' and in_string:
                in_string = False
                current += c
            elif c == ',' and not in_string:
                args.append(current)
                current = ""
            else:
                current += c
            i += 1
        if current.strip():
            args.append(current)
        return args

    def _parse_string(self, s: str) -> str:
        """Parse a double-quoted string with escape sequences."""
        s = s.strip()
        if not (s.startswith('"') and s.endswith('"')):
            raise ValueError(f"Not a string literal: {s}")
        inner = s[1:-1]
        result = ""
        i = 0
        ESC = {
            '0': '\x00', 'n': '\n', 't': '\t', 'r': '\r',
            'b': '\x08', 'f': '\x0c', 'v': '\x0b', 'a': '\x07',
            '"': '"', "'": "'", '?': '?', '\\': '\\',
        }
        while i < len(inner):
            if inner[i] == '\\' and i + 1 < len(inner):
                nc = inner[i + 1]
                if nc in ESC:
                    result += ESC[nc]
                    i += 2
                elif nc == 'x' and i + 3 < len(inner):
                    result += chr(int(inner[i+2:i+4], 16))
                    i += 4
                else:
                    result += inner[i]
                    i += 1
            else:
                result += inner[i]
                i += 1
        return result

    # -----------------------------------------------------------------------
    # Instruction handler
    # -----------------------------------------------------------------------
    def _handle_instruction(self, mnemonic: str, operands: str):
        if not self._require_section():
            return

        sec = self.current_section
        m = mnemonic.upper()

        try:
            if m == 'NOP':
                sec.emit(encode_nop())

            elif m == 'RET':
                sec.emit(encode_ret())

            elif m == 'RETI':
                sec.emit(encode_reti())

            elif m == 'EI':
                mask = self._parse_int(operands) if operands else 7
                sec.emit(encode_ei(mask))

            elif m == 'DI':
                sec.emit(encode_di())

            elif m == 'SWI':
                n = self._parse_int(operands) if operands else 0
                sec.emit(encode_swi(n))

            elif m == 'JP' or m in ('JP.L',):
                self._emit_jp(operands)

            elif m == 'JR' or m == 'J':
                self._emit_jr(operands, absolute=(m == 'J'))

            elif m == 'JRL':
                self._emit_jrl(operands)

            elif m == 'CALL':
                self._emit_call(operands)

            elif m == 'CALR':
                self._emit_calr(operands)

            elif m in ('LDB', 'LDB.B'):
                self._emit_ldb(operands)

            elif m == 'LDW':
                self._emit_ldw(operands)

            elif m == 'LD':
                self._emit_ld(operands)

            elif m == 'LDA':
                self._emit_lda(operands)

            elif m == 'PUSH':
                self._emit_push_pop('PUSH', operands)

            elif m == 'POP':
                self._emit_push_pop('POP', operands)

            elif re.match(r'^(ADD|ADC|SUB|SBC|AND|OR|XOR|CP)(B|W|L)?$', m):
                mm = re.match(r'^(ADD|ADC|SUB|SBC|AND|OR|XOR|CP)(B|W|L)?$', m)
                base_m = mm.group(1)
                size_s = mm.group(2)
                mem_size = {'B': 1, 'W': 2, 'L': 4}.get(size_s) if size_s else None
                self._emit_alu2(base_m, operands, mem_size=mem_size)

            elif re.match(r'^(INC|DEC)(B|W|L)?$', m):
                mm = re.match(r'^(INC|DEC)(B|W|L)?$', m)
                base_m = mm.group(1)
                size_s = mm.group(2)
                mem_size = {'B': 1, 'W': 2, 'L': 4}.get(size_s) if size_s else None
                self._emit_inc_dec(base_m, operands, mem_size=mem_size)

            elif m == 'NEG':
                sec.emit(encode_neg_r(operands.strip()))

            elif m == 'CPL':
                sec.emit(encode_cpl_r(operands.strip()))

            elif m in ('EXTZ', 'EXTS'):
                reg = operands.strip()
                if m == 'EXTZ':
                    sec.emit(encode_extz_r(reg))
                else:
                    sec.emit(encode_exts_r(reg))

            elif m in ('RLC', 'RRC', 'RL', 'RR', 'SLA', 'SRA', 'SLL', 'SRL'):
                self._emit_shift(m, operands)

            elif m == 'DJNZ':
                self._emit_djnz(operands)

            elif m == 'LINK':
                self._emit_link(operands)

            elif m == 'UNLK':
                reg = operands.strip().upper()
                if reg not in R32_REGS:
                    self._error(f"UNLK: unknown register '{reg}' (expected R32)")
                else:
                    sec.emit(encode_unlk(reg))

            elif m == 'RETD':
                d = self._parse_int(operands.strip()) if operands.strip() else 0
                sec.emit(encode_retd(d))

            elif m == 'LDIW':
                # LDIW (XDE+),(XHL+) — single word copy, no explicit operands needed.
                # Encoding: 0x84, 0x10 (confirmed hardware bisect j8j).
                sec.emit(encode_ldiw())

            elif m == 'LDIRW':
                # LDIRW — repeat word copy: (XDE+)<-(XHL+), BC-- until BC=0.
                # Encoding: 0x95, 0x11 (confirmed hardware: Ganbare + Pocket Tennis).
                # WARNING: guard BC!=0 before calling (BC=0 → 65536 copies).
                sec.emit(encode_ldirw())

            elif m in ('LDC', 'LDCB', 'LDCW', 'LDCL'):
                # LDC cr, r — load control register from CPU register.
                # Syntax: ldcl dmas0,xwa  /  ldcw dmac0,wa  /  ldcb dmam0,a
                # Encoding: [C8+zz+r_idx][2E][cr_number]
                parts = [p.strip() for p in operands.split(',')]
                if len(parts) != 2:
                    self._error(f"LDC: expected 'cr, reg' or 'reg, cr', got '{operands}'")
                else:
                    sec.emit(encode_ldc(parts[0], parts[1]))

            else:
                self._error(f"Unknown or unimplemented mnemonic '{mnemonic}' "
                            f"(operands: '{operands}')")

        except NotImplementedError as e:
            self._error(str(e))
        except UnresolvedSymbol as sym:
            # Forward reference — need deferred emit; handled per-instruction
            self._error(f"Forward reference to undefined symbol '{sym}' in {mnemonic} — "
                        f"forward refs currently require label before use (or use JP/JR only)")
        except Exception as e:
            self._error(f"Encoding error for '{mnemonic} {operands}': {e}")

    # -----------------------------------------------------------------------
    # JP instruction
    # -----------------------------------------------------------------------
    @staticmethod
    def _is_sym_ref(s: str) -> bool:
        """True if s looks like a symbol reference (not a pure numeric literal)."""
        s = s.strip()
        return bool(s) and (s[0].isalpha() or s[0] in '._')

    def _emit_jp(self, operands: str):
        """JP addr24 = 1B lo mid hi"""
        sec = self.current_section
        op = operands.strip()
        patch_off = len(sec.data)
        # Emit placeholder
        sec.emit(bytes([0x1B, 0x00, 0x00, 0x00]))
        # Try to resolve immediately
        try:
            addr = self._parse_int(op) & 0xFFFFFF
            sec.data[patch_off + 1] = addr & 0xFF
            sec.data[patch_off + 2] = (addr >> 8) & 0xFF
            sec.data[patch_off + 3] = (addr >> 16) & 0xFF
            # Relocatable section + symbol target: keep patch for linker (addr is section-relative)
            if sec.org is None and self._is_sym_ref(op):
                sec.add_patch(patch_off, 'JP_ABS24', op.strip(), patch_off + 4)
        except UnresolvedSymbol as sym:
            sec.add_patch(patch_off, 'JP_ABS24', str(sym), patch_off + 4)
        except Exception as e:
            self._error(f"JP: cannot resolve '{op}': {e}")

    # -----------------------------------------------------------------------
    # JR / J instruction
    # -----------------------------------------------------------------------
    def _emit_jr(self, operands: str, absolute: bool = False):
        """JR [cc,] label"""
        sec = self.current_section
        op = operands.strip()

        # Split at first comma to check for condition code
        cc = None
        target_str = op
        if ',' in op:
            parts = op.split(',', 1)
            possible_cc = parts[0].strip().upper()
            if possible_cc in JR_CC or possible_cc in ('T', 'F'):
                cc = possible_cc
                target_str = parts[1].strip()

        if absolute:
            # 'j' instruction: assembler maps to JP (we use JP always for 'j')
            self._emit_jp_conditional(cc, target_str)
            return

        # JR: relative 8-bit
        patch_off = len(sec.data)
        # Emit placeholder (2 bytes)
        opcode = JR_CC.get(cc.upper() if cc else 'T', 0x68)
        sec.emit(bytes([opcode, 0x00]))
        instr_end = len(sec.data)  # = patch_off + 2

        try:
            target = self._parse_int(target_str)
            instr_end_abs = (sec.org or 0) + instr_end
            disp = target - instr_end_abs
            if disp < -128 or disp > 127:
                # Out of range: replace the 2-byte JR placeholder with a 6-byte trampoline
                # [inv_cc, 0x04, 0x1B, addr_lo, addr_mid, addr_hi]
                inv_cc = 0x60 | ((opcode - 0x60) ^ 8)
                addr = target & 0xFFFFFF
                sec.data[patch_off:patch_off + 2] = bytearray([
                    inv_cc, 0x04,
                    0x1B, addr & 0xFF, (addr >> 8) & 0xFF, (addr >> 16) & 0xFF,
                ])
                return
            sec.data[patch_off + 1] = disp & 0xFF
        except UnresolvedSymbol as sym:
            sec.add_patch(patch_off, 'JR_REL8', str(sym), instr_end)

    def _emit_jp_conditional(self, cc: str | None, target_str: str):
        """Emit conditional JP as 'jrl ~cc, +4; jp target' trampoline (6 bytes).
        Unconditional (T/empty): plain 4-byte JP.
        """
        if not cc or cc.upper() in ('T', ''):
            self._emit_jp(target_str)
            return
        cc_up = cc.upper()
        if cc_up not in JR_CC:
            self._error(f"Unknown condition code '{cc}' for conditional JP")
            return
        opcode  = JR_CC[cc_up]
        inv_cc  = 0x60 | ((opcode - 0x60) ^ 8)
        sec = self.current_section
        # jrl ~cc, +4  — skip over the JP if condition NOT taken
        sec.emit(bytes([inv_cc, 0x04]))
        # jp target  — 4 bytes; patched by _emit_jp
        self._emit_jp(target_str)

    # -----------------------------------------------------------------------
    # LDB instruction
    # -----------------------------------------------------------------------
    def _emit_ldb(self, operands: str):
        """LDB (n8), imm8 — I/O byte write"""
        sec = self.current_section
        op = operands.strip()

        # Parse: (addr8), imm8
        m = re.match(r'^\((.+?)\)\s*,\s*(.+)$', op)
        if not m:
            self._error(f"LDB: expected '(addr), imm8', got '{op}'")
            return

        addr_str = m.group(1).strip()
        imm_str  = m.group(2).strip()

        try:
            addr = self._parse_int(addr_str) & 0xFFFF
            imm  = self._parse_int(imm_str) & 0xFF
        except UnresolvedSymbol as sym:
            self._error(f"LDB: unresolved symbol '{sym}' (forward refs not supported here)")
            return
        except Exception as e:
            self._error(f"LDB: {e}")
            return

        if addr > 0xFF:
            self._error(f"LDB: I/O address 0x{addr:04X} > 0xFF (only tiny/I/O range supported in v1)")
            return

        sec.emit(encode_ldb_io_imm(addr, imm))

    # -----------------------------------------------------------------------
    # JRL instruction (relative 16-bit jump)
    # -----------------------------------------------------------------------
    def _emit_jrl(self, operands: str):
        """JRL [cc,] label — relative 16-bit jump"""
        sec = self.current_section
        op = operands.strip()

        cc = None
        target_str = op
        if ',' in op:
            parts = op.split(',', 1)
            possible_cc = parts[0].strip().upper()
            if possible_cc in JR_CC or possible_cc in ('T', 'F'):
                cc = possible_cc
                target_str = parts[1].strip()

        opcode = JR_CC.get(cc.upper() if cc else 'T', 0x68) + 0x10  # 0x70+cc
        patch_off = len(sec.data)
        sec.emit(bytes([opcode, 0x00, 0x00]))
        instr_end = len(sec.data)

        try:
            target = self._parse_int(target_str)
            instr_end_abs = (sec.org or 0) + instr_end
            disp = target - instr_end_abs
            if disp < -32768 or disp > 32767:
                self._error(f"JRL target out of relative range (disp={disp})")
                return
            sec.data[patch_off + 1] = disp & 0xFF
            sec.data[patch_off + 2] = (disp >> 8) & 0xFF
        except UnresolvedSymbol as sym:
            sec.add_patch(patch_off, 'JRL_REL16', str(sym), instr_end)

    # -----------------------------------------------------------------------
    # CALL instruction (absolute 24-bit)
    # -----------------------------------------------------------------------
    def _emit_call(self, operands: str):
        """CALL addr24 = 1D lo mid hi  (4 bytes)"""
        sec = self.current_section
        op = operands.strip()
        patch_off = len(sec.data)
        sec.emit(bytes([0x1D, 0x00, 0x00, 0x00]))
        try:
            addr = self._parse_int(op) & 0xFFFFFF
            sec.data[patch_off + 1] = addr & 0xFF
            sec.data[patch_off + 2] = (addr >> 8) & 0xFF
            sec.data[patch_off + 3] = (addr >> 16) & 0xFF
            # Relocatable section + symbol target: keep patch for linker
            if sec.org is None and self._is_sym_ref(op):
                sec.add_patch(patch_off, 'CALL_ABS24', op.strip(), patch_off + 4)
        except UnresolvedSymbol as sym:
            sec.add_patch(patch_off, 'CALL_ABS24', str(sym), patch_off + 4)
        except Exception as e:
            self._error(f"CALL: cannot resolve '{op}': {e}")

    # -----------------------------------------------------------------------
    # CALR instruction (relative 16-bit call)
    # -----------------------------------------------------------------------
    def _emit_calr(self, operands: str):
        """CALR d16 = 1E lo hi  (3 bytes, displacement from end of instruction)"""
        sec = self.current_section
        op = operands.strip()
        patch_off = len(sec.data)
        sec.emit(bytes([0x1E, 0x00, 0x00]))
        instr_end = len(sec.data)
        if sec.org is None and self._is_sym_ref(op):
            sec.add_patch(patch_off, 'CALR_REL16', op.strip(), instr_end)
            try:
                target = self._parse_int(op)
                instr_end_abs = (sec.org or 0) + instr_end
                disp = target - instr_end_abs
                if -32768 <= disp <= 32767:
                    sec.data[patch_off + 1] = disp & 0xFF
                    sec.data[patch_off + 2] = (disp >> 8) & 0xFF
            except UnresolvedSymbol:
                pass
            except Exception as e:
                self._error(f"CALR: cannot resolve '{op}': {e}")
            return
        try:
            target = self._parse_int(op)
            instr_end_abs = (sec.org or 0) + instr_end
            disp = target - instr_end_abs
            if disp < -32768 or disp > 32767:
                self._error(f"CALR: displacement {disp} out of range [-32768, 32767]")
                return
            sec.data[patch_off + 1] = disp & 0xFF
            sec.data[patch_off + 2] = (disp >> 8) & 0xFF
        except UnresolvedSymbol as sym:
            sec.add_patch(patch_off, 'CALR_REL16', str(sym), instr_end)
        except Exception as e:
            self._error(f"CALR: cannot resolve '{op}': {e}")

    # -----------------------------------------------------------------------
    # LDA instruction (effective address calculation)
    # -----------------------------------------------------------------------
    def _emit_lda(self, operands: str):
        """LDA dest, src

        Minimal forms needed by the toolchain:
          LDA R16/R32, (r32)
          LDA R16/R32, (r32+d8)
          LDA R16/R32, r32
          LDA R16/R32, r32+d8
        """
        sec = self.current_section
        op = operands.strip()
        m = re.match(r'^(.+?)\s*,\s*(.+)$', op)
        if not m:
            self._error(f"LDA: expected 'dest, src', got '{op}'")
            return

        dest = m.group(1).strip().upper()
        src_raw = m.group(2).strip()
        src = src_raw
        if src.startswith('(') and src.endswith(')'):
            src = src[1:-1].strip()

        if dest in R32_REGS:
            dest_code = 0x30 + R32_REGS[dest]
        elif dest in R16_REGS:
            dest_code = 0x20 + R16_REGS[dest]
        else:
            self._error(f"LDA: unsupported destination '{dest}' (expected R16/R32)")
            return

        m_disp = re.match(r'^([A-Za-z]{3})\s*([+-])\s*(\d+)$', src)
        if m_disp:
            base = m_disp.group(1).upper()
            sign = 1 if m_disp.group(2) == '+' else -1
            disp = int(m_disp.group(3)) * sign
            if base not in R32_REGS:
                self._error(f"LDA: unknown base register '{base}'")
                return
            if disp < -128 or disp > 127:
                self._error(f"LDA: displacement {disp} out of range [-128, 127]")
                return
            sec.emit(bytes([0xB8 + R32_REGS[base], disp & 0xFF, dest_code]))
            return

        base = src.upper()
        if base in R32_REGS:
            sec.emit(bytes([0xB0 + R32_REGS[base], dest_code]))
            return

        self._error(
            f"LDA: unsupported source '{src_raw}' "
            f"(expected r32 or r32+disp, optionally parenthesized)"
        )

    # -----------------------------------------------------------------------
    # LD instruction (register loads)
    # -----------------------------------------------------------------------
    def _emit_ld(self, operands: str):
        """LD dest, src — register loads + post-increment indirect (Jalon 3)

        Forms supported:
          LD R, r          register-to-register
          LD R8/R16/R32, # immediate (EXTERN symbols supported via LD_R32_SYM / LD_R16_SYM patches)
          LD (r32+), R8    post-increment byte store  e.g. LD (XDE+), A
          LD R8, (r32+)    post-increment byte load   e.g. LD A, (XHL+)
          LD (abs16), R8   direct byte store to abs16 address  e.g. LD (_var), A
          LD R16, (abs16)  direct word load from abs16 address  e.g. LD WA, (_var)
          LD R8,  (abs16)  direct byte load from abs16 address  e.g. LD A,  (_var)
        """
        sec = self.current_section
        op = operands.strip()

        # ------------------------------------------------------------------
        # Post-increment forms: LD (r32+), R8  and  LD R8, (r32+)
        # ------------------------------------------------------------------
        _POST_INC = re.compile(r'^\(([A-Za-z]{3})\+\)$')

        m = re.match(r'^(.+?)\s*,\s*(.+)$', op)
        if m:
            dest_raw = m.group(1).strip()
            src_raw  = m.group(2).strip()

            # LD (r32+), R8  — store byte register to (r32), then r32++
            mi = _POST_INC.match(dest_raw)
            if mi:
                r32 = mi.group(1).upper()
                r8  = src_raw.upper()
                if r32 not in R32_REGS:
                    self._error(f"LD (r32+),R8: unknown r32 '{r32}'")
                    return
                if r8 not in R8_REGS:
                    self._error(f"LD (r32+),R8: unknown R8 source '{r8}'")
                    return
                r32_idx = R32_REGS[r32]
                r8_idx  = R8_REGS[r8]
                # C5 r32_idx (0x40 + r8_idx)
                sec.emit(bytes([0xC5, r32_idx, 0x40 + r8_idx]))
                return

            # LD R8, (r32+)  — load byte from (r32) into R8, then r32++
            mi = _POST_INC.match(src_raw)
            if mi:
                r32 = mi.group(1).upper()
                r8  = dest_raw.upper()
                if r32 not in R32_REGS:
                    self._error(f"LD R8,(r32+): unknown r32 '{r32}'")
                    return
                if r8 not in R8_REGS:
                    self._error(f"LD R8,(r32+): unknown R8 dest '{r8}'")
                    return
                r32_idx = R32_REGS[r32]
                r8_idx  = R8_REGS[r8]
                # 80  15  r32_idx  (0x20 + r8_idx)
                sec.emit(bytes([0x80, 0x15, r32_idx, 0x20 + r8_idx]))
                return

        # ------------------------------------------------------------------
        # Register-indirect with offset: LD R, (r32+d)  [and store (r32+d), R]
        # Encoding: [base + r32_idx] [d8] [0x20 + dest_reg_idx]
        #   R8  load/store: base=0x88/0xB8
        #   R16 load/store: base=0x98/0xB8 (same 0xB8 with different dest idx offset)
        #   R32 load/store: base=0xA8
        # Confirmed from t900cc.py:
        #   LD XWA,(XIY+d) = 0xAD d 0x20  (0xA8+XIY(5), 0x20+XWA(0))
        #   LDW WA,(XIY+d) = 0x9D d 0x20  (0x98+XIY(5), 0x20+WA(0))
        #   LDB A,(XIY+d)  = 0x8D d 0x21  (0x88+XIY(5), 0x20+A(1))
        # ------------------------------------------------------------------
        _OFFSET_INDIR = re.compile(
            r'^\(([A-Za-z]{3})\s*([+-])\s*(\d+)\)$'
        )
        if m:
            dest_raw = m.group(1).strip()
            src_raw  = m.group(2).strip()
            dest_up  = dest_raw.upper()
            src_up2  = src_raw.upper()

            # LD R, (r32+d) — load from r32-relative address
            mi = _OFFSET_INDIR.match(src_raw)
            if mi:
                r32   = mi.group(1).upper()
                sign  = 1 if mi.group(2) == '+' else -1
                disp  = int(mi.group(3)) * sign
                d8    = disp & 0xFF
                if r32 not in R32_REGS:
                    self._error(f"LD R,(r32+d): unknown r32 '{r32}'"); return
                r32_idx = R32_REGS[r32]
                if dest_up in R32_REGS:
                    sec.emit(bytes([0xA8 + r32_idx, d8, 0x20 + R32_REGS[dest_up]])); return
                elif dest_up in R16_REGS:
                    sec.emit(bytes([0x98 + r32_idx, d8, 0x20 + R16_REGS[dest_up]])); return
                elif dest_up in R8_REGS:
                    sec.emit(bytes([0x88 + r32_idx, d8, 0x20 + R8_REGS[dest_up]])); return
                else:
                    self._error(f"LD R,(r32+d): unknown dest '{dest_raw}'"); return

            # LD (r32+d), R — store to r32-relative address
            mi = _OFFSET_INDIR.match(dest_raw)
            if mi:
                r32   = mi.group(1).upper()
                sign  = 1 if mi.group(2) == '+' else -1
                disp  = int(mi.group(3)) * sign
                d8    = disp & 0xFF
                if r32 not in R32_REGS:
                    self._error(f"LD (r32+d),R: unknown r32 '{r32}'"); return
                r32_idx = R32_REGS[r32]
                if src_up2 in R32_REGS:
                    sec.emit(bytes([0xB8 + r32_idx, d8, 0x30 + R32_REGS[src_up2]])); return
                elif src_up2 in R16_REGS:
                    sec.emit(bytes([0xB8 + r32_idx, d8, 0x30 + R16_REGS[src_up2]])); return
                elif src_up2 in R8_REGS:
                    sec.emit(bytes([0xB8 + r32_idx, d8, 0x31 + R8_REGS[src_up2]])); return
                else:
                    self._error(f"LD (r32+d),R: unknown src '{src_raw}'"); return

        # ------------------------------------------------------------------
        # Standard forms: register immediate / register-to-register
        # ------------------------------------------------------------------
        if not m:
            self._error(f"LD: expected 'dest, src', got '{op}'")
            return

        dest   = m.group(1).strip().upper()
        dest_raw = m.group(1).strip()
        src_str = m.group(2).strip()
        src_up  = src_str.upper()

        # ------------------------------------------------------------------
        # LD (sym/addr), R8 — direct byte store to abs16 address
        # LD (abs16), R8 = F1 lo hi (0x40+R)
        # Reuses LD_R16_SYM because the relocation payload is the same 16-bit addr field.
        # ------------------------------------------------------------------
        if dest_raw.startswith('(') and dest_raw.endswith(')') and src_up in R8_REGS:
            inner = dest_raw[1:-1].strip()
            patch_off = len(sec.data)
            sec.emit(encode_ld_abs16_r8(0, src_up))    # F1 00 00 (0x40+R) placeholder
            try:
                addr = self._parse_int(inner) & 0xFFFF
                sec.data[patch_off + 1] = addr & 0xFF
                sec.data[patch_off + 2] = (addr >> 8) & 0xFF
                if sec.org is None and self._is_sym_ref(inner):
                    sec.add_patch(patch_off + 1, 'LD_R16_SYM', inner.strip(), patch_off + 4)
            except UnresolvedSymbol as sym:
                sec.add_patch(patch_off + 1, 'LD_R16_SYM', str(sym), patch_off + 4)
            except Exception as e:
                self._error(f"LD (addr),R8: cannot parse '{inner}': {e}")
            return

        # ------------------------------------------------------------------
        # LD R, (sym/addr) — direct memory load from abs16 address
        # LD R16, (abs16) = D1 lo hi (0x20+R)  confirmed: D1=0x98+0x39 (word load + abs16)
        # LD R8,  (abs16) = C1 lo hi (0x20+R)  confirmed: C1=0x88+0x39 (byte load + abs16)
        # Uses LD_R16_SYM patch (2 bytes at offset+1) for symbol resolution.
        # ------------------------------------------------------------------
        if src_str.startswith('(') and src_str.endswith(')'):
            inner = src_str[1:-1].strip()
            if dest in R16_REGS:
                patch_off = len(sec.data)
                sec.emit(encode_r16_abs16(dest, 0))   # D1 00 00 (0x20+R) placeholder
                try:
                    addr = self._parse_int(inner) & 0xFFFF
                    sec.data[patch_off + 1] = addr & 0xFF
                    sec.data[patch_off + 2] = (addr >> 8) & 0xFF
                    if sec.org is None and self._is_sym_ref(inner):
                        sec.add_patch(patch_off + 1, 'LD_R16_SYM', inner.strip(), patch_off + 4)
                except UnresolvedSymbol as sym:
                    sec.add_patch(patch_off + 1, 'LD_R16_SYM', str(sym), patch_off + 4)
                except Exception as e:
                    self._error(f"LD R16,(addr): cannot parse '{inner}': {e}")
                return
            elif dest in R8_REGS:
                patch_off = len(sec.data)
                sec.emit(encode_r8_abs16(dest, 0))    # C1 00 00 (0x20+R) placeholder
                try:
                    addr = self._parse_int(inner) & 0xFFFF
                    sec.data[patch_off + 1] = addr & 0xFF
                    sec.data[patch_off + 2] = (addr >> 8) & 0xFF
                    if sec.org is None and self._is_sym_ref(inner):
                        sec.add_patch(patch_off + 1, 'LD_R16_SYM', inner.strip(), patch_off + 4)
                except UnresolvedSymbol as sym:
                    sec.add_patch(patch_off + 1, 'LD_R16_SYM', str(sym), patch_off + 4)
                except Exception as e:
                    self._error(f"LD R8,(addr): cannot parse '{inner}': {e}")
                return
            else:
                self._error(f"LD R,(addr): dest '{dest}' not R8 or R16 — only R8/R16 abs16 loads supported")
                return

        try:
            # Register-to-register: LD R, r (both operands are registers)
            if dest in _ALL_REGS and src_up in _ALL_REGS:
                sec.emit(encode_ld_r_r(dest, src_up))
            elif dest in R8_REGS:
                imm = self._parse_int(src_str) & 0xFF
                sec.emit(encode_ld_r8_imm(dest, imm))
            elif dest in R16_REGS:
                imm = self._parse_int(src_str) & 0xFFFF
                sec.emit(encode_ld_r16_imm(dest, imm))
            elif dest in R32_REGS:
                imm = self._parse_int(src_str) & 0xFFFFFFFF
                # If src looks like a symbol name (starts with _ or letter), emit a
                # LD_R32_SYM relocation even when the symbol is locally resolved.
                # Section-relative offsets are NOT final ROM addresses; the linker must
                # patch the 4-byte immediate with the absolute address from full_symbols.
                _src = src_str.strip()
                if re.match(r'^[_a-zA-Z]', _src):
                    patch_off = len(sec.data)
                    sec.emit(encode_ld_r32_imm(dest, imm))   # placeholder (sec-relative) — 5 bytes required for linker patch
                    sec.add_patch(patch_off + 1, 'LD_R32_SYM', _src, patch_off + 5)
                else:
                    # Pure numeric immediate — safe to use the compact 2-byte
                    # form for small values (0..7). Saves 3 bytes per site.
                    sec.emit(encode_ld_r32_imm(dest, imm, allow_compact=True))
            else:
                self._error(f"LD: unsupported destination '{dest}' "
                            f"(v1 supports R8/R16/R32 immediate and register-to-register loads)")
        except UnresolvedSymbol as sym:
            # EXTERN symbol in LD R32/R16 immediate — emit placeholder + patch
            if dest in R32_REGS:
                patch_off = len(sec.data)
                sec.emit(encode_ld_r32_imm(dest, 0))          # 5 bytes: 40+R, 00,00,00,00
                sec.add_patch(patch_off + 1, 'LD_R32_SYM', str(sym), patch_off + 5)
            elif dest in R16_REGS:
                patch_off = len(sec.data)
                sec.emit(encode_ld_r16_imm(dest, 0))          # 3 bytes: 30+R, 00,00
                sec.add_patch(patch_off + 1, 'LD_R16_SYM', str(sym), patch_off + 3)
            else:
                self._error(f"LD: EXTERN '{sym}' in unsupported destination '{dest}'")
        except Exception as e:
            self._error(f"LD '{op}': {e}")

    # -----------------------------------------------------------------------
    # LDW instruction (word memory writes)
    # -----------------------------------------------------------------------
    def _emit_ldw(self, operands: str):
        """LDW (addr), src — word store to memory (abs16 or I/O)"""
        sec = self.current_section
        op = operands.strip()

        m = re.match(r'^\((.+?)\)\s*,\s*(.+)$', op)
        if not m:
            self._error(f"LDW: expected '(addr), src', got '{op}'")
            return

        addr_str = m.group(1).strip()
        src_str  = m.group(2).strip()

        src_up = src_str.upper()

        # Register source: support both numeric abs16 and symbolic abs16 with relocation.
        if src_up in R16_REGS:
            patch_off = len(sec.data)
            sec.emit(encode_ldw_abs16_r16(0, src_up))   # F1 00 00 (0x50+R) placeholder
            try:
                addr = self._parse_int(addr_str) & 0xFFFF
                sec.data[patch_off + 1] = addr & 0xFF
                sec.data[patch_off + 2] = (addr >> 8) & 0xFF
                if sec.org is None and self._is_sym_ref(addr_str):
                    sec.add_patch(patch_off + 1, 'LD_R16_SYM', addr_str.strip(), patch_off + 4)
            except UnresolvedSymbol as sym:
                sec.add_patch(patch_off + 1, 'LD_R16_SYM', str(sym), patch_off + 4)
            except Exception as e:
                self._error(f"LDW (abs16),R16: cannot parse '{addr_str}': {e}")
            return

        try:
            addr = self._parse_int(addr_str)
        except Exception as e:
            self._error(f"LDW: cannot parse address '{addr_str}': {e}")
            return

        # I/O range (addr <= 0xFF): only immediate form
        if addr <= 0xFF and src_up not in R16_REGS:
            try:
                imm = self._parse_int(src_str) & 0xFFFF
                sec.emit(encode_ldw_io_imm16(addr, imm))
            except Exception as e:
                self._error(f"LDW (n8),#16: {e}")
            return

        # ABS_W form (addr <= 0xFFFF)
        if addr <= 0xFFFF:
            try:
                imm = self._parse_int(src_str) & 0xFFFF
                sec.emit(encode_ldw_abs16_imm16(addr, imm))
            except Exception as e:
                self._error(f"LDW (abs16),#16: {e}")
            return

        self._error(f"LDW: address 0x{addr:06X} > 0xFFFF not yet supported (Jalon 2)")

    # -----------------------------------------------------------------------
    # PUSH / POP instructions
    # -----------------------------------------------------------------------
    def _emit_push_pop(self, mnem: str, operands: str):
        """PUSH/POP R16 or R32"""
        sec = self.current_section
        reg = operands.strip().upper()
        try:
            if mnem == 'PUSH':
                if reg in R16_REGS:
                    sec.emit(encode_push_r16(reg))
                elif reg in R32_REGS:
                    sec.emit(encode_push_r32(reg))
                else:
                    self._error(f"PUSH: unknown register '{reg}'")
            else:  # POP
                if reg in R16_REGS:
                    sec.emit(encode_pop_r16(reg))
                elif reg in R32_REGS:
                    sec.emit(encode_pop_r32(reg))
                else:
                    self._error(f"POP: unknown register '{reg}'")
        except Exception as e:
            self._error(f"{mnem} '{reg}': {e}")

    # -----------------------------------------------------------------------
    # ALU two-operand: ADD, ADC, SUB, SBC, AND, OR, XOR, CP
    # -----------------------------------------------------------------------
    # Sub-opcode table for register-to-register ALU (C8+zz+src : base+dest)
    _ALU_RR_BASE = {
        'ADD': 0x80, 'ADC': 0x90, 'SUB': 0xA0, 'SBC': 0xB0,
        'AND': 0xC0, 'XOR': 0xD0, 'OR':  0xE0, 'CP':  0xF0,
    }
    # Sub-opcode for immediate ALU (C8+zz+dest : sub_op : imm)
    _ALU_IMM_OP = {
        'ADD': 0xC8, 'ADC': 0xC9, 'SUB': 0xCA, 'SBC': 0xCB,
        'AND': 0xCC, 'XOR': 0xCD, 'OR':  0xCE, 'CP':  0xCF,
    }

    def _emit_alu2(self, mnem: str, operands: str, mem_size: int | None = None):
        """ADD/SUB/AND/OR/XOR/CP/ADC/SBC dest, src"""
        sec = self.current_section
        op = operands.strip()
        m = re.match(r'^(.+?)\s*,\s*(.+)$', op)
        if not m:
            self._error(f"{mnem}: expected 'dest, src', got '{op}'")
            return
        dest_raw = m.group(1).strip()
        dest = dest_raw.upper()
        src_str = m.group(2).strip()

        src_up = src_str.upper()
        try:
            if dest_raw.startswith('(') and dest_raw.endswith(')'):
                inner = dest_raw[1:-1].strip()
                patch_off = len(sec.data)
                if src_up in _ALL_REGS:
                    _, _, inferred_size = _ALL_REGS[src_up]
                    use_size = mem_size or inferred_size
                    sec.emit(encode_mem_abs16_alu_reg(mnem, use_size, 0, src_up))
                    patch_end = patch_off + 4
                else:
                    if mem_size is None:
                        self._error(
                            f"{mnem}: memory-immediate form requires explicit size suffix "
                            f"(use {mnem}B/{mnem}W/{mnem}L)"
                        )
                        return
                    imm = self._parse_int(src_str)
                    sec.emit(encode_mem_abs16_alu_imm(mnem, mem_size, 0, imm))
                    patch_end = patch_off + (5 if mem_size == 1 else 6 if mem_size == 2 else 8)
                try:
                    addr = self._parse_int(inner) & 0xFFFF
                    sec.data[patch_off + 1] = addr & 0xFF
                    sec.data[patch_off + 2] = (addr >> 8) & 0xFF
                    if sec.org is None and self._is_sym_ref(inner):
                        sec.add_patch(patch_off + 1, 'ABS16', inner.strip(), patch_end)
                except UnresolvedSymbol as sym:
                    sec.add_patch(patch_off + 1, 'ABS16', str(sym), patch_end)
                except Exception as e:
                    self._error(f"{mnem} (addr),src: cannot parse '{inner}': {e}")
                return

            if dest not in _ALL_REGS:
                self._error(f"{mnem}: unknown destination register '{dest}'")
                return
            if src_up in _ALL_REGS:
                # Register-to-register: C8+zz+src : base+dest
                sec.emit(encode_alu_r_r(self._ALU_RR_BASE[mnem], dest, src_up))
            else:
                # Immediate: C8+zz+dest : sub_op : imm
                imm = self._parse_int(src_str)
                sec.emit(encode_alu_r_imm(self._ALU_IMM_OP[mnem], dest, imm))
        except UnresolvedSymbol as sym:
            self._error(f"{mnem}: unresolved symbol '{sym}' (forward refs not supported here)")
        except Exception as e:
            self._error(f"{mnem} '{op}': {e}")

    # -----------------------------------------------------------------------
    # INC / DEC
    # -----------------------------------------------------------------------
    def _emit_inc_dec(self, mnem: str, operands: str, mem_size: int | None = None):
        """INC [n,] r  or  INC r  (default n=1)
        Toshiba official syntax: INC #3, r  (count before register)
        Also accept: INC r  (implicit n=1)
        """
        sec = self.current_section
        op = operands.strip()
        n = 1
        reg_str = op

        if ',' in op:
            parts = op.split(',', 1)
            try:
                n = self._parse_int(parts[0].strip())
                reg_str = parts[1].strip()
            except Exception:
                # May be "INC HL, 2" form (count after reg) — try other order
                try:
                    reg_str = parts[0].strip()
                    n = self._parse_int(parts[1].strip())
                except Exception:
                    self._error(f"{mnem}: cannot parse operands '{op}'")
                    return

        try:
            if reg_str.startswith('(') and reg_str.endswith(')'):
                inner = reg_str[1:-1].strip()
                if mem_size is None:
                    self._error(
                        f"{mnem}: memory form requires explicit size suffix "
                        f"(use {mnem}B/{mnem}W/{mnem}L)"
                    )
                    return
                patch_off = len(sec.data)
                sec.emit(encode_mem_abs16_inc_dec(mnem, mem_size, 0, n))
                try:
                    addr = self._parse_int(inner) & 0xFFFF
                    sec.data[patch_off + 1] = addr & 0xFF
                    sec.data[patch_off + 2] = (addr >> 8) & 0xFF
                    if sec.org is None and self._is_sym_ref(inner):
                        sec.add_patch(patch_off + 1, 'ABS16', inner.strip(), patch_off + 4)
                except UnresolvedSymbol as sym:
                    sec.add_patch(patch_off + 1, 'ABS16', str(sym), patch_off + 4)
                except Exception as e:
                    self._error(f"{mnem} (addr): cannot parse '{inner}': {e}")
                return
            if mnem == 'INC':
                sec.emit(encode_inc_r(reg_str, n))
            else:
                sec.emit(encode_dec_r(reg_str, n))
        except Exception as e:
            self._error(f"{mnem}: {e}")

    # -----------------------------------------------------------------------
    # Shift / rotate instructions
    # -----------------------------------------------------------------------
    _SHIFT_IMM_OP = {
        'RLC': 0xE8, 'RRC': 0xE9, 'RL': 0xEA, 'RR': 0xEB,
        'SLA': 0xEC, 'SRA': 0xED, 'SLL': 0xEE, 'SRL': 0xEF,
    }
    _SHIFT_A_OP = {
        'RLC': 0xF8, 'RRC': 0xF9, 'RL': 0xFA, 'RR': 0xFB,
        'SLA': 0xFC, 'SRA': 0xFD, 'SLL': 0xFE, 'SRL': 0xFF,
    }

    def _emit_shift(self, mnem: str, operands: str):
        """RLC/RRC/RL/RR/SLA/SRA/SLL/SRL [count/A,] r"""
        sec = self.current_section
        op = operands.strip()
        count_or_a = None
        reg_str = op

        if ',' in op:
            parts = op.split(',', 1)
            first = parts[0].strip().upper()
            reg_str = parts[1].strip()
            if first == 'A':
                count_or_a = 'A'
            else:
                try:
                    count_or_a = self._parse_int(first)
                except Exception:
                    self._error(f"{mnem}: cannot parse shift count '{first}'")
                    return
        else:
            count_or_a = 1  # default: shift by 1

        try:
            if count_or_a == 'A':
                sec.emit(encode_shift_r_a(self._SHIFT_A_OP[mnem], reg_str))
            else:
                sec.emit(encode_shift_r_imm(self._SHIFT_IMM_OP[mnem], reg_str, count_or_a))
        except Exception as e:
            self._error(f"{mnem}: {e}")

    # -----------------------------------------------------------------------
    # DJNZ
    # -----------------------------------------------------------------------
    def _emit_djnz(self, operands: str):
        """DJNZ [r,] label  — decrement r (default B), jump if nonzero"""
        sec = self.current_section
        op = operands.strip()
        reg = 'B'
        target_str = op

        if ',' in op:
            parts = op.split(',', 1)
            possible_reg = parts[0].strip().upper()
            if possible_reg in R8_REGS or possible_reg in R16_REGS:
                reg = possible_reg
                target_str = parts[1].strip()

        patch_off = len(sec.data)
        sec.emit(bytes([0x00, 0x1C, 0x00]))  # placeholder: prefix, DJNZ, disp
        instr_end = len(sec.data)            # = patch_off + 3

        # Set prefix byte
        prefix, _ = _c8_prefix(reg)
        sec.data[patch_off] = prefix

        try:
            target = self._parse_int(target_str)
            instr_end_abs = (sec.org or 0) + instr_end
            disp = target - instr_end_abs
            if disp < -128 or disp > 127:
                self._error(f"DJNZ: displacement {disp} out of range [-128, 127]")
                return
            sec.data[patch_off + 2] = disp & 0xFF
        except UnresolvedSymbol as sym:
            sec.add_patch(patch_off, 'DJNZ_REL8', str(sym), instr_end)
        except Exception as e:
            self._error(f"DJNZ: cannot resolve '{target_str}': {e}")

    # -----------------------------------------------------------------------
    # LINK instruction (frame allocation)
    # -----------------------------------------------------------------------
    def _emit_link(self, operands: str):
        """LINK R32, N = (0xE8+R) 0x0C lo hi"""
        sec = self.current_section
        m = re.match(r'^(\w+)\s*,\s*(.+)$', operands.strip())
        if not m:
            self._error(f"LINK: expected 'R32, N', got '{operands}'")
            return
        reg = m.group(1).strip().upper()
        n_str = m.group(2).strip()
        if reg not in R32_REGS:
            self._error(f"LINK: unknown register '{reg}' (expected XWA/XBC/XDE/XHL/XIX/XIY/XIZ/XSP)")
            return
        try:
            n = self._parse_int(n_str) & 0xFFFF
            sec.emit(encode_link(reg, n))
        except Exception as e:
            self._error(f"LINK: {e}")


# ---------------------------------------------------------------------------
# Output generation
# ---------------------------------------------------------------------------
def generate_flat_binary(asm: Assembler) -> tuple[bytes, int]:
    """
    Produce a flat binary from all sections.
    For absolute sections: placed at their org address.
    For relocatable sections: concatenated in order.

    Returns (binary_data, start_address).
    """
    # Find range
    abs_sections = [(s.org, s) for s in asm.sections.values() if s.org is not None]
    rel_sections = [s for s in asm.sections.values() if s.org is None]

    if abs_sections and rel_sections:
        # Mix: place rel sections after last abs section
        pass  # for now, just warn

    if abs_sections:
        # Compute range
        min_addr = min(org for org, _ in abs_sections)
        max_addr = max(org + len(s.data) for org, s in abs_sections)
        size = max_addr - min_addr
        buf = bytearray(b'\xFF' * size)
        for org, sec in abs_sections:
            offset = org - min_addr
            buf[offset:offset + len(sec.data)] = sec.data
        # Append rel sections at end
        for sec in rel_sections:
            buf.extend(sec.data)
        return bytes(buf), min_addr

    else:
        # All relocatable: concatenate
        buf = bytearray()
        for sec in [asm.sections[n] for n in asm.section_order]:
            buf.extend(sec.data)
        return bytes(buf), 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="t900as — TLCS-900 Assembler v0.2 (NGPCraft Toolchain Jalon 1-2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python t900as.py test/hello.asm -o test/hello.bin
  python t900as.py test/hello.asm -o test/hello.bin -v
  python t900as.py test/main.asm  -o test/main.t9obj --format obj
""",
    )
    p.add_argument("source",          help="Input .asm source file")
    p.add_argument("--output", "-o",  required=True, help="Output file (.bin or .t9obj)")
    p.add_argument("--format", "-f",  choices=["bin", "obj"], default="bin",
                   help="Output format: bin=flat binary (default), obj=relocatable .t9obj JSON")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    obj_mode = (args.format == "obj")

    asm = Assembler(verbose=args.verbose)
    asm.assemble_file(args.source, obj_mode=obj_mode)

    if asm.warnings:
        for w in asm.warnings:
            print(w, file=sys.stderr)

    if asm.errors:
        for e in asm.errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    if obj_mode:
        import json
        obj = asm.serialize_obj(args.source)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        total_bytes = sum(len(bytes.fromhex(s["data"])) for s in obj["sections"])
        print(f"[t900as] Object : {args.source} -> {args.output}")
        print(f"  Sections: {[s['name'] for s in obj['sections']]}")
        print(f"  Publics : {obj['publics']}")
        print(f"  Externs : {list(obj['externs'].keys())}")
        print(f"  Data    : {total_bytes} bytes across {len(obj['sections'])} section(s)")
    else:
        binary, start_addr = generate_flat_binary(asm)
        with open(args.output, "wb") as f:
            f.write(binary)
        print(f"[t900as] Assembled: {args.source} -> {args.output}")
        print(f"  Sections: {list(asm.sections.keys())}")
        print(f"  Symbols : {len(asm.symbols)} defined")
        print(f"  Output  : {len(binary)} bytes, start=0x{start_addr:06X}")


if __name__ == "__main__":
    main()
