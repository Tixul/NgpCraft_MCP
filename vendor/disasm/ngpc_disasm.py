#!/usr/bin/env python3
"""
ngpc_disasm.py — NGPC/NGP Disassembler
Part of the NgpCraft open-source toolchain for Neo Geo Pocket Color.

MIT License
Copyright (c) 2026 NgpCraft Tixu
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

Full TLCS-900/H decoder with NGPC memory map annotations.
Handles both homebrew (NgpCraft toolchain) and CC900-compiled binaries.

Usage:
  python ngpc_disasm.py rom.ngc
  python ngpc_disasm.py rom.ngc --base 0x200000
  python ngpc_disasm.py rom.ngc --start 0x200040 --end 0x200200
  python ngpc_disasm.py rom.ngc -o output.asm

Architecture — two-pass linear sweep:
  Pass 1 (disassemble, label collection): decode each instruction, collect all
    CALL/JP target addresses into LabelMap. Unknown bytes → 1-byte step.
  Pass 2 (emit): re-decode with label lookup. Emit address, hex bytes, mnemonic,
    optional annotation comment (HW register name, silicon warning, cross-ref).

Decode dispatch (decode_one):
  1. decode_fixed   — single-byte opcodes + short fixed patterns (NOP, RET, EI…)
  2. decode_xx      — 0x20..0x7F range (LD imm, PUSH, POP, JR, JRL, LD R32 imm32)
  3. decode_B0_mem  — zz==3 prefix (0xB0..0xFF) → abs-store, RET cc, indirect JP/CALL
  4. decode_zz_r    — mem>=23 (C8+zz+r family): ALU on registers, LINK, UNLK, LDC, shifts
  5. decode_zz_mem  — mem<=21: indirect/abs loads and stores, LDIW, LDIRW

NGPC silicon bugs annotated inline:
  D0..D7 as ALU word-register prefix   → broken (all uses)
  LINK XIY, N with N >= 5              → broken
  See also: T900_DENSE_REF.md §41, silicon_bugs.md

LDC encoding (TLCS-900/L1 catalog ALT00146, pattern 1 1 z z 1 r r r):
  C8+r  zz=00 → byte  register (R8,  ldcb)   0xC8+r
  D8+r  zz=01 → word  register (R16, ldcw)   0xD8+r   ← D0+r (zz=00,bit3=0) is broken!
  E8+r  zz=10 → lword register (R32, ldcl)   0xE8+r
  NOTE: in the ALU-register-source context (C8/D8/E8 prefix before ALU sub-byte),
  D8..DF selects R32 operand (not R16). Only the LDC sub-byte (0x2E/0x2F) uses
  D8 to mean R16. The disassembler shows register by _zz_regs() (ALU convention),
  so LDC D8 2E cr appears as "ldc CR, XWA" — functionally correct (WA ⊂ XWA).

Sources: t900as.py (encoder), TMP95C061BFG datasheet (Toshiba, ALT00146),
         HW_REGISTERS.md, BIOS_REF.md, ngpc_romtool.py, silicon notes.
"""

import sys, os, re, argparse

ROM_BASE    = 0x200000
HEADER_SIZE = 64

# ============================================================
# Register tables
# ============================================================
R8  = ['W',  'A',  'B',  'C',  'D',  'E',  'H',  'L' ]
R16 = ['WA', 'BC', 'DE', 'HL', 'IX', 'IY', 'IZ', 'SP']
R32 = ['XWA','XBC','XDE','XHL','XIX','XIY','XIZ','XSP']

# Condition codes — cc index 0..15
CC = ['F','LT','LE','ULE','OV','MI','Z','C',
      'T','GE','GT','UGT','NOV','PL','NZ','NC']

# ============================================================
# NGPC hardware I/O register map (address → name)
# ============================================================
HW_IO = {
    0x0020: 'HW_TRUN',      # Timer run control
    0x0022: 'HW_TREG0',     # Timer 0 reload value
    0x0023: 'HW_TREG1',     # Timer 1 reload value
    0x0024: 'HW_T01MOD',    # Timer 0/1 mode
    0x0025: 'HW_TFFCR',     # Timer flip-flop control
    0x0026: 'HW_TREG2',     # Timer 2 (PWM0)
    0x0027: 'HW_TREG3',     # Timer 3 (audio, PWM1)
    0x0028: 'HW_T23MOD',    # Timer 2/3 mode
    0x006B: 'HW_WATCHDOG_ALT', # Alt watchdog (Metal Slug)
    0x006F: 'HW_WATCHDOG',  # Watchdog — write 0x4E to reset
    0x0073: 'HW_ROM_BANK',  # ROM bank register
    0x007C: 'HW_DMA0V',     # DMA channel 0 vector
    0x007D: 'HW_DMA1V',     # DMA channel 1 vector
    0x007E: 'HW_DMA2V',     # DMA channel 2 vector
    0x007F: 'HW_DMA3V',     # DMA channel 3 vector
}

# Memory-mapped hardware registers
HW_MEM = {
    0x6F80: 'HW_BAT_VOLT',      # Battery voltage (u16)
    0x6F82: 'HW_JOYPAD',        # Joypad state
    0x6F84: 'HW_USR_BOOT',      # Boot reason (0=normal, 1=resume, 2=alarm)
    0x6F85: 'HW_USR_SHUTDOWN',  # OS-requested shutdown flag
    0x6F86: 'HW_USR_ANSWER',    # User response (bit5 must be 0)
    0x6F87: 'HW_LANGUAGE',      # System language
    0x6F91: 'HW_OS_VERSION',    # 0=NGP mono, !=0=NGPC color
    0x6FCC: 'VBL_VECTOR_LO',    # VBlank ISR low word (user sets this)
    0x6FCE: 'VBL_VECTOR_HI',    # VBlank ISR high word
    0x8000: 'K2GE_BASE',
    0x8002: 'K2GE_TRANSPARENCY',
    0x8008: 'K2GE_SCR1_SCROLL_X',
    0x8009: 'K2GE_SCR1_SCROLL_Y',
    0x800A: 'K2GE_SCR2_SCROLL_X',
    0x800B: 'K2GE_SCR2_SCROLL_Y',
    0x8010: 'K2GE_BG_COLOR',
    0x8012: 'K2GE_WIN_X1',
    0x8013: 'K2GE_WIN_Y1',
    0x8014: 'K2GE_WIN_X2',
    0x8015: 'K2GE_WIN_Y2',
    0x8102: 'K2GE_LED',
    0x8800: 'SPR_VRAM_BASE',    # Sprite VRAM (64 sprites × 4 bytes)
    0x8C00: 'SPR_PAL_IDX_BASE', # Sprite palette indices
    0x9000: 'SCR1_MAP_BASE',    # Scroll plane 1 tilemap
    0x9800: 'SCR2_MAP_BASE',    # Scroll plane 2 tilemap
    0xA000: 'TILE_RAM_BASE',    # Character/Tile RAM
}

# DMA control register numbers → names (for LDC instruction)
CR_NAMES = {
    0x00: 'DMAS0', 0x04: 'DMAS1', 0x08: 'DMAS2', 0x0C: 'DMAS3',
    0x10: 'DMAD0', 0x14: 'DMAD1', 0x18: 'DMAD2', 0x1C: 'DMAD3',
    0x20: 'DMAC0', 0x22: 'DMAM0',
    0x24: 'DMAC1', 0x26: 'DMAM1',
    0x28: 'DMAC2', 0x2A: 'DMAM2',
    0x2C: 'DMAC3', 0x2E: 'DMAM3',
    0x30: 'INTNEST',
}

# SWI function names
SWI_NAMES = {
    0: 'BIOS_SHUTDOWN',
    1: 'BIOS_CLOCKGEARSET',
    2: 'BIOS_RTCGET',
    5: 'BIOS_SYSFONTSET',
    6: 'BIOS_FLASHWRITE',
    8: 'BIOS_FLASHERS',
    9: 'BIOS_ALARMSET',
}

# ============================================================
# Address annotation helpers
# ============================================================

def annotate_addr(addr):
    """Return symbolic name for a known address, or None."""
    if addr in HW_IO:
        return HW_IO[addr]
    if addr in HW_MEM:
        return HW_MEM[addr]
    if addr == 0xFFFE00:
        return 'BIOS_VECTOR_TABLE'
    if 0xFF0000 <= addr <= 0xFFFFFF:
        return f'BIOS+0x{addr - 0xFF0000:04X}'
    return None

def fmt_addr(addr):
    """Format address with optional annotation."""
    name = annotate_addr(addr)
    s = f'0x{addr:06X}'
    if name:
        s += f'  ; {name}'
    return s

def fmt_mem(addr, size_hint=''):
    """Format memory operand (addr) with annotation."""
    name = annotate_addr(addr)
    if name:
        return f'({name})', f'= 0x{addr:04X}'
    return f'(0x{addr:04X})', None

# ============================================================
# Byte-level helpers
# ============================================================

def u8(data, i):
    return data[i]

def u16(data, i):
    return data[i] | (data[i+1] << 8)

def u24(data, i):
    return data[i] | (data[i+1] << 8) | (data[i+2] << 16)

def u32(data, i):
    return data[i] | (data[i+1] << 8) | (data[i+2] << 16) | (data[i+3] << 24)

def s8(data, i):
    v = data[i]
    return v - 256 if v >= 128 else v

def s16(data, i):
    v = u16(data, i)
    return v - 65536 if v >= 32768 else v

def _safe(data, i, n=1):
    """True if data[i..i+n-1] is accessible."""
    return i + n <= len(data)

# ============================================================
# Broken opcode detection helpers
# ============================================================

def _is_broken_d0_family(b, context='alu'):
    """D0-D7 as ALU word-register source prefix = BROKEN on NGPC silicon.
    NOTE: this helper is NOT called from the main decode path; the broken flag
    is set inline in _zz_regs() and decode_zz_mem(). Kept as explicit predicate
    for external callers that may need to query broken-ness without decoding."""
    return 0xD0 <= b <= 0xD7 and context == 'alu'

def check_broken_link(n):
    """LINK XIY, N with N >= 5 is broken on NGPC silicon.
    CC900 always uses N=0 (link XIY, 0); our toolchain enforces N<=4 max.
    Bisect validation: z10(N=8)/z11(N=6)/z12(N=5) all failed on real hardware."""
    return n >= 5

# ============================================================
# decode_fixed — simple 1..4 byte opcodes
# Returns (length, mnem, ops, refs, warn) or None
# refs = list of (target_addr, 'call'|'jump')
# ============================================================

def decode_fixed(data, pos, cur_addr, base):
    b = data[pos]
    refs = []
    warn = None

    if b == 0x00:
        return (1, 'nop', '', refs, warn)

    if b == 0x06:
        if not _safe(data, pos, 2): return None
        n = data[pos+1]
        if n == 0x07:
            return (2, 'di', '', refs, warn)
        return (2, 'ei', str(n), refs, warn)

    if b == 0x07:
        return (1, 'reti', '', refs, warn)

    if b == 0x08:
        if not _safe(data, pos, 3): return None
        n8, imm8 = data[pos+1], data[pos+2]
        nm = annotate_addr(n8)
        op1 = f'({nm})' if nm else f'(0x{n8:02X})'
        return (3, 'ldb', f'{op1}, 0x{imm8:02X}', refs, warn)

    if b == 0x09:
        if not _safe(data, pos, 2): return None
        imm8 = data[pos+1]
        return (2, 'push', f'0x{imm8:02X}', refs, warn)

    if b == 0x0A:
        if not _safe(data, pos, 4): return None
        n8 = data[pos+1]
        imm16 = u16(data, pos+2)
        nm = annotate_addr(n8)
        op1 = f'({nm})' if nm else f'(0x{n8:02X})'
        return (4, 'ldw', f'{op1}, 0x{imm16:04X}', refs, warn)

    if b == 0x0B:
        if not _safe(data, pos, 3): return None
        imm16 = u16(data, pos+1)
        return (3, 'pushw', f'0x{imm16:04X}', refs, warn)

    if b == 0x0C:
        return (1, 'incf', '', refs, warn)

    if b == 0x0D:
        return (1, 'decf', '', refs, warn)

    if b == 0x0E:
        return (1, 'ret', '', refs, warn)

    if b == 0x0F:
        if not _safe(data, pos, 3): return None
        d = s16(data, pos+1)
        return (3, 'retd', f'{d}', refs, warn)

    if b == 0x10:
        return (1, 'rcf', '', refs, warn)

    if b == 0x11:
        return (1, 'scf', '', refs, warn)

    if b == 0x12:
        return (1, 'ccf', '', refs, warn)

    if b == 0x13:
        return (1, 'zcf', '', refs, warn)

    if b == 0x14:
        return (1, 'push', 'A', refs, warn)

    if b == 0x15:
        return (1, 'pop', 'A', refs, warn)

    if b == 0x16:
        return (1, 'ex', "F,F'", refs, warn)

    if b == 0x17:
        if not _safe(data, pos, 2): return None
        n = data[pos+1] & 0x07
        return (2, 'ldf', str(n), refs, warn)

    if b == 0x18:
        return (1, 'push', 'F', refs, warn)

    if b == 0x19:
        return (1, 'pop', 'F', refs, warn)

    if b == 0x1A:
        # JP #16
        if not _safe(data, pos, 3): return None
        addr = u16(data, pos+1)
        nm = annotate_addr(addr)
        s = f'0x{addr:04X}'
        if nm: s += f'  ; {nm}'
        refs.append((addr, 'jump'))
        return (3, 'jp', s, refs, warn)

    if b == 0x1B:
        # JP #24
        if not _safe(data, pos, 4): return None
        addr = u24(data, pos+1)
        nm = annotate_addr(addr)
        s = f'0x{addr:06X}'
        if nm: s += f'  ; {nm}'
        refs.append((addr, 'jump'))
        return (4, 'jp', s, refs, warn)

    if b == 0x1C:
        # CALL #16
        if not _safe(data, pos, 3): return None
        addr = u16(data, pos+1)
        nm = annotate_addr(addr)
        s = f'0x{addr:04X}'
        if nm: s += f'  ; {nm}'
        refs.append((addr, 'call'))
        return (3, 'call', s, refs, warn)

    if b == 0x1D:
        # CALL #24
        if not _safe(data, pos, 4): return None
        addr = u24(data, pos+1)
        nm = annotate_addr(addr)
        s = f'0x{addr:06X}'
        if nm: s += f'  ; {nm}'
        refs.append((addr, 'call'))
        return (4, 'call', s, refs, warn)

    if b == 0x1E:
        # CALR d16 (relative call)
        if not _safe(data, pos, 3): return None
        d = s16(data, pos+1)
        addr = (cur_addr + 3 + d) & 0xFFFFFF
        nm = annotate_addr(addr)
        s = f'0x{addr:06X}'
        if nm: s += f'  ; {nm}'
        refs.append((addr, 'call'))
        return (3, 'calr', s, refs, warn)

    if b == 0xF7:
        # LDX (#8),#8 — 6 bytes
        if not _safe(data, pos, 6): return None
        n8  = data[pos+2]
        imm = data[pos+4]
        nm = annotate_addr(n8)
        op1 = f'({nm})' if nm else f'(0x{n8:02X})'
        return (6, 'ldx', f'{op1}, 0x{imm:02X}', refs, warn)

    if 0xF8 <= b <= 0xFF:
        # SWI n
        n = b & 0x07
        nm = SWI_NAMES.get(n, '')
        s = str(n)
        if nm: s += f'  ; {nm}'
        return (1, 'swi', s, refs, warn)

    # 0x02: PUSH SR, 0x03: POP SR, 0x05: HALT
    if b == 0x02:
        return (1, 'push', 'SR', refs, warn)
    if b == 0x03:
        return (1, 'pop', 'SR', refs, warn)
    if b == 0x05:
        return (1, 'halt', '', refs, warn)

    return None  # not a fixed opcode

# ============================================================
# decode_xx — 0x20..0x7F range (LD imm, PUSH, POP, JR, JRL)
# ============================================================

def decode_xx(data, pos, cur_addr, base):
    b = data[pos]
    refs = []
    warn = None

    grp = b & 0xF8
    r   = b & 0x07

    if grp == 0x20:
        # LD R8, imm8
        if not _safe(data, pos, 2): return None
        imm = data[pos+1]
        return (2, 'ld', f'{R8[r]}, 0x{imm:02X}', refs, warn)

    if grp == 0x28:
        # PUSH R16
        return (1, 'push', R16[r], refs, warn)

    if grp == 0x30:
        # LD R16, imm16
        if not _safe(data, pos, 3): return None
        imm = u16(data, pos+1)
        nm = annotate_addr(imm)
        s = f'0x{imm:04X}'
        if nm: s += f'  ; {nm}'
        return (3, 'ld', f'{R16[r]}, {s}', refs, warn)

    if grp == 0x38:
        # PUSH R32
        return (1, 'push', R32[r], refs, warn)

    if grp == 0x40:
        # LD R32, imm32
        if not _safe(data, pos, 5): return None
        imm = u32(data, pos+1)
        nm = annotate_addr(imm)
        s = f'0x{imm:08X}'
        if nm: s = f'{nm}  ; 0x{imm:08X}'
        # Check if this looks like a ROM pointer
        if ROM_BASE <= imm <= 0x3FFFFF:
            refs.append((imm, 'data'))
        return (5, 'ld', f'{R32[r]}, {s}', refs, warn)

    if grp == 0x48:
        # POP R16
        return (1, 'pop', R16[r], refs, warn)

    if grp == 0x50:
        # SCC cc, r  (set-on-condition — not common in NGPC code)
        # Note: datasheet §4.2 table shows 0x50-0x57 as POP R32 in this context
        return (1, 'pop', R32[r], refs, warn)  # actually POP R32 in our assembler

    if grp == 0x58:
        # Undefined opcode range per datasheet Appendix C instruction code map
        return None

    # JR / JRL — handled by bit pattern
    if (b & 0x70) == 0x70:
        # JRL cc, disp16 (3 bytes)
        if not _safe(data, pos, 3): return None
        cc_idx = b & 0x0F
        d = s16(data, pos+1)
        target = (cur_addr + 3 + d) & 0xFFFFFF
        cc_str = '' if cc_idx == 8 else f'{CC[cc_idx]}, '
        refs.append((target, 'jump'))
        nm = annotate_addr(target)
        s = f'0x{target:06X}'
        if nm: s += f'  ; {nm}'
        return (3, 'jrl', f'{cc_str}{s}', refs, warn)

    if (b & 0x60) == 0x60:
        # JR cc, disp8 (2 bytes)
        if not _safe(data, pos, 2): return None
        cc_idx = b & 0x0F
        d = s8(data, pos+1)
        target = (cur_addr + 2 + d) & 0xFFFFFF
        cc_str = '' if cc_idx == 8 else f'{CC[cc_idx]}, '
        refs.append((target, 'jump'))
        nm = annotate_addr(target)
        s = f'0x{target:06X}'
        if nm: s += f'  ; {nm}'
        return (2, 'jr', f'{cc_str}{s}', refs, warn)

    return None

# ============================================================
# decode_zz_r — C8+zz+r prefix family (ALU on registers, LINK, UNLK, LDC, shifts)
#
# Prefix byte layout in ALU-register context:
#   C8..CF  (1100 1rrr) : byte  operand — R8  source  → sz='b', safe
#   D0..D7  (1101 0rrr) : word  operand — R16 source  → sz='w', BROKEN on NGPC silicon
#   D8..DF  (1101 1rrr) : lword operand — R32 source  → sz='l', safe
#   E8..EF  (1110 1rrr) : lword (alt)   — R32 source  → sz='l', safe (extz, LDIRW…)
#   C7      (1100 0111) : extended bank register prefix (bank_idx byte follows)
#
# LDC sub-encoding note (when second byte is 0x2E/0x2F):
#   In the LDC instruction encoding (catalog ALT00146 §4, pattern 1 1 z z 1 r r r),
#   D8+r means R16 (word) and E8+r means R32 (long word). This differs from the ALU
#   register context where D8+r selects R32. The disassembler uses _zz_regs() for both
#   and will therefore show "ldc CR, XWA" for a D8 2E cr sequence (CC900 ldcw DMAC0,WA).
#   Functionally equivalent: WA is the low word of XWA. For correctness, use E8 (ldcl
#   with R32=XWA) for 32-bit registers (DMAS/DMAD), and D8 (ldcw with WA displayed as
#   XWA by this disassembler) is safe for 16-bit registers (DMAC/DMAM).
# ============================================================

def _zz_regs(b):
    """Returns (size_str, reg_name, is_broken) for C8+zz+r / E8+r prefix byte.

    In ALU-register context:
      C8..CF → ('b', R8[r],  False/True)  byte register; CB specifically broken
      D0..D7 → ('w', R16[r], True)        word register, BROKEN on NGPC silicon
      D8..DF → ('l', R32[r], False)       lword register, safe (R32, not R16!)
      E8..EF → ('l', R32[r], False)       lword register, safe
      C7     → ('b', None,   False)       extended bank-reg prefix, r=None sentinel

    Confirmed broken instructions on NGPC silicon (validated bisect 2026-03-22):
      CB xx → byte ALU using R8[3] = C as source. Example: `add A, C` = CB 81
              hangs in an infinite loop. Fix: route through HL — `add A, L` = CF 81.
      D0..D7 xx → word ALU using R16 as source. Example: `cpl WA` = D0 06 hangs.
                  Fix: extz XWA + use D8 (lword source) family.

    See module docstring for the LDC context asymmetry (D8 = R16 for LDC encoding
    but this function returns R32 — both naming conventions refer to the same bits).
    """
    if 0xC8 <= b <= 0xCF:
        # CB specifically broken on silicon (CB family — byte ALU with C source).
        # The other C8..CF variants are confirmed safe in CC900 production code.
        return ('b', R8[b & 7], b == 0xCB)
    if 0xD0 <= b <= 0xD7:
        return ('w', R16[b & 7], True)   # BROKEN on NGPC silicon (D0..D7 ALU prefix)
    if 0xD8 <= b <= 0xDF:
        return ('l', R32[b & 7], False)  # ALU lword source — R32 (XWA…XSP)
    if 0xE8 <= b <= 0xEF:
        return ('l', R32[b & 7], False)  # E8 family: extz, LDIRW, etc.
    # 0xC7 — extended bank register prefix; _getmem(0xC7)=23 routes here
    if b == 0xC7:
        return ('b', None, False)   # None = extended, next byte = bank_idx
    return (None, None, False)

def _imm_for_size(sz):
    return {'b': 1, 'w': 2, 'l': 4}.get(sz, 1)

def decode_zz_r(data, pos, cur_addr, base):
    if not _safe(data, pos, 2): return None
    b  = data[pos]
    c  = data[pos+1]
    sz, reg, broken = _zz_regs(b)

    refs = []
    if broken:
        if b == 0xCB:
            warn = '!BROKEN CB family (byte ALU C-source) — silicon hang, use CF (L-source) instead'
        else:
            warn = '!BROKEN D0..D7 ALU (word-reg prefix)'
    else:
        warn = None
    is_e8 = (0xE8 <= b <= 0xEF)

    # ---- Extended register prefix (0xC7) — next byte is bank reg index ----
    if b == 0xC7:
        if not _safe(data, pos, 3): return None
        bank_idx = data[pos+1]
        bank_num = bank_idx >> 4
        reg_num  = bank_idx & 0x07
        ext_reg  = f'r{R8[reg_num]}{bank_num}'   # e.g. rA2, rW3
        c2 = data[pos+2]
        if c2 == 0x04: return (3, 'push', ext_reg, refs, warn)
        if c2 == 0x05: return (3, 'pop',  ext_reg, refs, warn)
        if c2 == 0x06: return (3, 'cpl',  ext_reg, refs, warn)
        if c2 == 0x07: return (3, 'neg',  ext_reg, refs, warn)
        if c2 == 0x12: return (3, 'extz', ext_reg, refs, warn)
        if c2 == 0x13: return (3, 'exts', ext_reg, refs, warn)
        if c2 == 0x03:
            if not _safe(data, pos, 4): return None
            return (4, 'ld', f'{ext_reg}, 0x{data[pos+3]:02X}', refs, warn)
        if 0x60 <= c2 <= 0x67:
            return (3, 'inc', f'{(c2 & 7) or 8}, {ext_reg}', refs, warn)
        if 0x68 <= c2 <= 0x6F:
            return (3, 'dec', f'{(c2 & 7) or 8}, {ext_reg}', refs, warn)
        if 0xA8 <= c2 <= 0xAF:
            return (3, 'ld',  f'{ext_reg}, {c2 & 7}', refs, warn)
        if 0xC8 <= c2 <= 0xCF:
            if not _safe(data, pos, 4): return None
            return (4, 'add', f'{ext_reg}, 0x{data[pos+3]:02X}', refs, warn)
        if 0xCF == c2:
            if not _safe(data, pos, 4): return None
            return (4, 'cp',  f'{ext_reg}, 0x{data[pos+3]:02X}', refs, warn)
        if 0xD8 <= c2 <= 0xDF:
            return (3, 'cp',  f'{ext_reg}, {c2 & 7}', refs, warn)
        for base_op, mnem in [(0x80,'add'),(0x88,'ld'),(0x90,'adc'),(0x98,'ld'),
                               (0xA0,'sub'),(0xB0,'sbc'),(0xC0,'and'),
                               (0xD0,'xor'),(0xE0,'or'),(0xF0,'cp')]:
            if base_op <= c2 < base_op + 8:
                return (3, mnem, f'{R8[c2 & 7]}, {ext_reg}' if c2 >= 0x98 else
                                 f'{ext_reg}, {R8[c2 & 7]}', refs, warn)
        return (3, 'db', f'0x{b:02X}, 0x{bank_idx:02X}, 0x{c2:02X}', refs,
                f'ext-reg bank{bank_num} op=0x{c2:02X}')

    if reg is None:
        return None

    # ---- Single-op instructions (second byte is exact) ----
    # ---- MUL/DIV immediate (second byte 0x08-0x0B, imm follows) ----
    # Dest = wide register (one size up), from prefix r field
    if c in (0x08, 0x09, 0x0A, 0x0B):
        mnem = ('mul', 'muls', 'div', 'divs')[c - 0x08]
        dr = R16[b & 7] if sz == 'b' else R32[b & 7]
        if sz == 'b':
            if not _safe(data, pos, 3): return None
            imm = data[pos + 2]
            return (3, mnem, f'{dr}, 0x{imm:02X}', refs, warn)
        else:
            if not _safe(data, pos, 4): return None
            imm = u16(data, pos + 2)
            return (4, mnem, f'{dr}, 0x{imm:04X}', refs, warn)

    if c == 0x04:
        return (2, 'push', reg, refs, warn)
    if c == 0x05:
        return (2, 'pop', reg, refs, warn)
    if c == 0x06:
        return (2, 'cpl', reg, refs, warn)
    if c == 0x07:
        return (2, 'neg', reg, refs, warn)
    if c == 0x10:
        return (2, 'daa', reg, refs, warn)
    if c == 0x12:
        return (2, 'extz', reg, refs, warn)
    if c == 0x13:
        return (2, 'exts', reg, refs, warn)

    if c == 0x0C:
        # LINK r32, d16 (E8+r, 4 bytes total)
        if not _safe(data, pos, 4): return None
        n = s16(data, pos+2)
        w = ''
        if check_broken_link(n):
            w = f'!BROKEN link N={n}>=5 (silicon bug)'
        return (4, 'link', f'{reg}, {n}', refs, w)

    if c == 0x0D:
        # UNLK r32
        return (2, 'unlk', reg, refs, warn)

    if c == 0x1C:
        # DJNZ r, d8
        if not _safe(data, pos, 3): return None
        d = s8(data, pos+2)
        target = (cur_addr + 3 + d) & 0xFFFFFF
        refs.append((target, 'jump'))
        return (3, 'djnz', f'{reg}, 0x{target:06X}', refs, warn)

    if c == 0x2E:
        # LDC cr, r  (write CR) — encoding: [prefix][0x2E][cr_num]
        # prefix: C8+r=ldcb(R8), D8+r=ldcw(R16→shown as R32 here), E8+r=ldcl(R32)
        # D0+r (broken!) was the old t900as.py bug (zz=0x08 → D0). Fixed: zz=0x10→D8, zz=0x20→E8.
        # CC900 uses: E8 2E cr for DMAS/DMAD (32-bit source/dest addr)
        #             D8 2E cr for DMAC (16-bit count, displayed as XWA — see module docstring)
        #             C9 2E cr for DMAM (8-bit mode, A register)
        if not _safe(data, pos, 3): return None
        cr = data[pos+2]
        cr_nm = CR_NAMES.get(cr, f'CR_0x{cr:02X}')
        return (3, 'ldc', f'{cr_nm}, {reg}', refs, warn)

    if c == 0x2F:
        # LDC r, cr  (read CR) — encoding: [prefix][0x2F][cr_num]
        # Reads CR into register. D0 2F cr (read with R16/WA) is also broken on NGPC
        # if D0 prefix is used. Safe: use ldcb for R8, or ldcl+extz for R32.
        if not _safe(data, pos, 3): return None
        cr = data[pos+2]
        cr_nm = CR_NAMES.get(cr, f'CR_0x{cr:02X}')
        return (3, 'ldc', f'{reg}, {cr_nm}', refs, warn)

    if c == 0x03:
        # LD r, imm
        isz = _imm_for_size(sz)
        if not _safe(data, pos, 2 + isz): return None
        if isz == 1: imm = u8(data, pos+2)
        elif isz == 2: imm = u16(data, pos+2)
        else: imm = u32(data, pos+2)
        nm = annotate_addr(imm)
        s = f'0x{imm:0{isz*2}X}'
        if nm: s += f'  ; {nm}'
        return (2 + isz, 'ld', f'{reg}, {s}', refs, warn)

    # ---- INC / DEC ----
    if 0x60 <= c <= 0x67:
        n = c & 0x07
        if n == 0: n = 8
        return (2, 'inc', f'{n}, {reg}', refs, warn)
    if 0x68 <= c <= 0x6F:
        n = c & 0x07
        if n == 0: n = 8
        return (2, 'dec', f'{n}, {reg}', refs, warn)

    # ---- LD r, imm3 (3-bit immediate packed in opcode byte)  0xA8..0xAF ----
    if 0xA8 <= c <= 0xAF:
        imm3 = c & 0x07
        return (2, 'ld', f'{reg}, {imm3}', refs, warn)

    # ---- CP r, imm3  0xD8..0xDF ----
    if 0xD8 <= c <= 0xDF:
        imm3 = c & 0x07
        return (2, 'cp', f'{reg}, {imm3}', refs, warn)

    # ---- ALU R, r (second byte = op_base + R_dest) ----
    # Determine destination register set based on source register size
    def dest_reg(idx):
        if sz == 'b': return R8[idx]
        if sz == 'w': return R16[idx]
        return R32[idx]

    alu_r_ops = {
        0x80: 'add', 0x88: 'ld',  0x90: 'adc', 0x98: None,  # 0x98 = LD r, R (reverse)
        0xA0: 'sub', 0xB0: 'sbc', 0xB8: 'ex',
        0xC0: 'and', 0xD0: 'xor', 0xE0: 'or',  0xF0: 'cp',
    }
    for base_op, mnem in alu_r_ops.items():
        if base_op <= c < base_op + 8:
            R_idx = c & 0x07
            # `adc W, B` (CA 90) fails on NGPC silicon when W > 0 — see
            # bugs_silicon.json ADC_W_B_W_GT_0. Static disasm can't know W's
            # runtime value, so flag the instruction for review.
            if (mnem == 'adc' and sz == 'b'
                    and reg == 'B' and dest_reg(R_idx) == 'W'):
                warn = '!BROKEN adc W,B fails when W>0 (silicon) — keep W==0 or restructure'
            if base_op == 0x98:
                # LD r, R  (reverse direction)
                return (2, 'ld', f'{reg}, {dest_reg(R_idx)}', refs, warn)
            if mnem:
                return (2, mnem, f'{dest_reg(R_idx)}, {reg}', refs, warn)

    # ---- ALU r, imm ----
    alu_imm_ops = {
        0xC8: 'add', 0xC9: 'adc', 0xCA: 'sub', 0xCB: 'sbc',
        0xCC: 'and', 0xCD: 'xor', 0xCE: 'or',  0xCF: 'cp',
    }
    if c in alu_imm_ops:
        isz = _imm_for_size(sz)
        if not _safe(data, pos, 2 + isz): return None
        if isz == 1: imm = u8(data, pos+2)
        elif isz == 2: imm = u16(data, pos+2)
        else: imm = u32(data, pos+2)
        nm = annotate_addr(imm)
        s = f'0x{imm:0{isz*2}X}'
        if nm: s += f'  ; {nm}'
        return (2 + isz, alu_imm_ops[c], f'{reg}, {s}', refs, warn)

    # ---- Shifts / rotates by immediate ----
    shift_ops = {
        0xE8: 'rlc', 0xE9: 'rrc', 0xEA: 'rl',  0xEB: 'rr',
        0xEC: 'sla', 0xED: 'sra', 0xEE: 'sll', 0xEF: 'srl',
    }
    if c in shift_ops:
        if not _safe(data, pos, 3): return None
        count = data[pos+2] & 0x0F
        if count == 0: count = 16
        return (3, shift_ops[c], f'{count}, {reg}', refs, warn)

    # ---- Shifts / rotates by A ----
    shift_a_ops = {
        0xF8: 'rlc', 0xF9: 'rrc', 0xFA: 'rl',  0xFB: 'rr',
        0xFC: 'sla', 0xFD: 'sra', 0xFE: 'sll', 0xFF: 'srl',
    }
    if c in shift_a_ops:
        return (2, shift_a_ops[c], f'A, {reg}', refs, warn)

    # ---- Bit operations ----
    if 0x20 <= c <= 0x24:
        bit_ops = {0x20:'andcf', 0x21:'orcf', 0x22:'xorcf', 0x23:'ldcf', 0x24:'stcf'}
        if not _safe(data, pos, 3): return None
        bit = data[pos+2] & 0x0F
        return (3, bit_ops[c], f'{bit}, {reg}', refs, warn)
    if 0x28 <= c <= 0x2C:
        bit_a_ops = {0x28:'andcf', 0x29:'orcf', 0x2A:'xorcf', 0x2B:'ldcf', 0x2C:'stcf'}
        return (2, bit_a_ops[c], f'A, {reg}', refs, warn)
    if 0x30 <= c <= 0x34:
        bit2_ops = {0x30:'res', 0x31:'set', 0x32:'chg', 0x33:'bit', 0x34:'tset'}
        if not _safe(data, pos, 3): return None
        bit = data[pos+2] & 0x0F
        return (3, bit2_ops[c], f'{bit}, {reg}', refs, warn)

    # ---- SCC cc, r ----
    if 0x70 <= c <= 0x7F:
        cc_idx = c & 0x0F
        return (2, 'scc', f'{CC[cc_idx]}, {reg}', refs, warn)

    # ---- MUL/DIV (register form) — dest is one size wider than source ----
    # sz='b' → R16 dest, sz='w' → R32 dest (datasheet §5 MUL/DIV)
    def wide_reg(idx): return R16[idx] if sz == 'b' else R32[idx]
    if 0x40 <= c <= 0x47: return (2, 'mul',  f'{wide_reg(c & 7)}, {reg}', refs, warn)
    if 0x48 <= c <= 0x4F: return (2, 'muls', f'{wide_reg(c & 7)}, {reg}', refs, warn)
    if 0x50 <= c <= 0x57: return (2, 'div',  f'{wide_reg(c & 7)}, {reg}', refs, warn)
    if 0x58 <= c <= 0x5F: return (2, 'divs', f'{wide_reg(c & 7)}, {reg}', refs, warn)

    return None  # unrecognized

# ============================================================
# decode_zz_mem — indirect and abs16 load/store forms
#
# For bytes 0x80..0xFF (excluding the zz==3/B0_mem group), the first byte encodes
# both the memory addressing mode and the data size (zz):
#
#   First byte layout: s z z m m m m m
#     s      = bit 7 (always 1 in this group)
#     zz     = bits [5:4] — data size: 00=byte, 01=word, 10=lword
#     mmmmm  = _getmem() — addressing mode index 0..21
#
# _getmem(b): extracts the 5-bit mem mode from bits [6,3:0]:
#   mm = b & 0x4F  (mask out bits [5:4] = zz, keep bit6 and bits[3:0])
#   mem = ((mm & 0x40) >> 2) | (mm & 0x0F)  → 0..21
#
# mem mode table (see _retmem_info):
#   0..7   → (r32)          ARI: indirect via XWA..XSP
#   8..15  → (r32+d8)       ARID: indirect with signed 8-bit displacement
#   16     → (abs8)         ABS_B: 1-byte absolute address
#   17     → (abs16)        ABS_W: 2-byte absolute address (C1, D1, E1 prefixes)
#   18     → (abs24)        ABS_L: 3-byte absolute address
#   19     → ARI secondary  various r32+offset/index modes
#   20     → (-r32)         ARI_PD: pre-decrement
#   21     → (r32+)         ARI_PI: post-increment
#   >=23   → C8+zz+r        register source → routed to decode_zz_r
#
# Special hard-coded patterns (checked before general dispatch):
#   0x84 0x10        → LDIW  (XDE+),(XHL+)  — single word copy (NGPC: XIY/XIX)
#   0x95 0x11        → LDIRW (XDE+),(XHL+)  — repeat word copy (NGPC: XIY/XIX)
#   0xD2..0xD5 LD    → safe CC900 abs-load patterns (tried before broken D0-D7 handler)
#   0xD0..0xD7 other → broken D0..D7 ALU word-register prefix handler
#   0xC1/0xD1/0xE1   → abs16 byte/word/lword loads (safe — these are mem forms, not ALU prefix)
# ============================================================

def _getmem(b):
    """Extract 5-bit memory mode from first byte of an indirect instruction.
    Layout: bit6 → bit4 of result, bits[3:0] → bits[3:0] of result.
    Values 0..21 = memory addressing modes; >=23 = register source (→ decode_zz_r)."""
    mm = b & 0x4F
    return ((mm & 0x40) >> 2) | (mm & 0x0F)

def _getzz_mem(b):
    """Extract 2-bit data-size field from first byte: bits[5:4] → 00=byte, 01=word, 10=lword."""
    return (b & 0x30) >> 4

def decode_zz_mem(data, pos, cur_addr, base):
    b = data[pos]
    refs = []
    warn = None

    mem = _getmem(b)
    zz  = _getzz_mem(b)

    # ============================================================
    # D2-D5: abs-load forms used safely by CC900 (like D1 abs16)
    # D2=abs24, D3=ARI, D4=ARI_PD, D5=ARI_PI — SAFE when op=0x20+R (LD R16, (mem))
    # Must be tried BEFORE the BROKEN handler below.
    # ============================================================
    if 0xD2 <= b <= 0xD5:
        mem2 = _getmem(b)
        n2, mem_str2, addr_val2 = _retmem_info(data, pos, mem2)
        if n2 is not None and _safe(data, pos, n2 + 1):
            op2 = data[pos + n2]
            if 0x20 <= op2 <= 0x27:          # LD R16, (mem) — safe load
                r_idx2 = op2 & 0x07
                ann2 = ''
                if addr_val2 is not None:
                    _, anm2 = fmt_mem(addr_val2)
                    if anm2: ann2 = f'  ; {anm2}'
                return (n2+1, 'ld', f'{R16[r_idx2]}, {mem_str2}{ann2}', refs, warn)

    # ============================================================
    # BROKEN word-reg ALU prefix (D0-D7) falling into decode_zz_mem path
    # D0: all uses broken. D2-D5 ALU sub-ops also broken (non-load ops).
    # ============================================================
    if 0xD0 <= b <= 0xD7 and b != 0xD1:
        warn = f'!BROKEN D{b-0xD0} word-reg ALU prefix (NGPC silicon bug)'
        if not _safe(data, pos, 2): return None
        c = data[pos+1]
        reg = R16[b & 7]
        # Try to show what op it attempts: second byte = operation
        if c == 0x06: return (2, 'cpl', reg, refs, warn)
        if c == 0x07: return (2, 'neg', reg, refs, warn)
        if c == 0x12: return (2, 'extz', reg, refs, warn)
        if c == 0x0C and _safe(data, pos, 4):
            n = s16(data, pos+2)
            return (4, 'link', f'{reg}, {n}', refs, warn)
        if 0x60 <= c <= 0x67:
            n = c & 7
            return (2, 'inc', f'{n or 8}, {reg}', refs, warn)
        if 0x68 <= c <= 0x6F:
            n = c & 7
            return (2, 'dec', f'{n or 8}, {reg}', refs, warn)
        # ALU r, imm
        alu_w = {0xC8:'add', 0xC9:'adc', 0xCA:'sub', 0xCB:'sbc',
                 0xCC:'and', 0xCD:'xor', 0xCE:'or', 0xCF:'cp'}
        if c in alu_w and _safe(data, pos, 4):
            imm = u16(data, pos+2)
            return (4, alu_w[c], f'{reg}, 0x{imm:04X}', refs, warn)
        # ALU R, r
        for base_op, mnem in [(0x80,'add'),(0x88,'ld'),(0x90,'adc'),
                               (0xA0,'sub'),(0xB0,'sbc'),(0xC0,'and'),
                               (0xD0,'xor'),(0xE0,'or'),(0xF0,'cp')]:
            if base_op <= c < base_op + 8:
                return (2, mnem, f'{R16[c&7]}, {reg}', refs, warn)
        return (2, 'db', f'0x{b:02X}, 0x{c:02X}', refs, warn)

    # ============================================================
    # Special hard-coded patterns from our toolchain (confirmed HW)
    # ============================================================

    # LDIW  (XDE+),(XHL+)  — single word copy — 0x84 0x10
    if b == 0x84 and _safe(data, pos, 2) and data[pos+1] == 0x10:
        return (2, 'ldiw', '(XDE+),(XHL+)', refs, warn)

    # LDIRW (XDE+),(XHL+)  — repeat word copy — 0x95 0x11
    if b == 0x95 and _safe(data, pos, 2) and data[pos+1] == 0x11:
        return (2, 'ldirw', '(XDE+),(XHL+)', refs, warn)

    # ============================================================
    # (r32+d8) forms — load
    # 0x88..0x8F d8 op  → LD R8, (r32+d8)
    # 0x98..0x9F d8 op  → LD R16, (r32+d8)
    # 0xA8..0xAF d8 op  → LD R32, (r32+d8)
    # ============================================================
    if 0x88 <= b <= 0x8F:
        if not _safe(data, pos, 3): return None
        r32_idx = b & 0x07
        d = s8(data, pos+1)
        op = data[pos+2]
        if 0x20 <= op <= 0x27:
            r_idx = op & 0x07
            sign = '+' if d >= 0 else ''
            return (3, 'ld', f'{R8[r_idx]}, ({R32[r32_idx]}{sign}{d})', refs, warn)

    if 0x98 <= b <= 0x9F:
        if not _safe(data, pos, 3): return None
        r32_idx = b & 0x07
        d = s8(data, pos+1)
        op = data[pos+2]
        if 0x20 <= op <= 0x27:
            r_idx = op & 0x07
            sign = '+' if d >= 0 else ''
            return (3, 'ld', f'{R16[r_idx]}, ({R32[r32_idx]}{sign}{d})', refs, warn)

    if 0xA8 <= b <= 0xAF:
        if not _safe(data, pos, 3): return None
        r32_idx = b & 0x07
        d = s8(data, pos+1)
        op = data[pos+2]
        if 0x20 <= op <= 0x27:
            r_idx = op & 0x07
            sign = '+' if d >= 0 else ''
            return (3, 'ld', f'{R32[r_idx]}, ({R32[r32_idx]}{sign}{d})', refs, warn)

    # ============================================================
    # (r32+d8) forms — store
    # 0xB8..0xBF d8 op
    # 0x30..0x37 → LD (r32+d8), R16 or R32
    # 0x31..0x38 → LD (r32+d8), R8 (different base in our assembler)
    # 0x50..0x57 → LD (r32+d8), R16 (datasheet §5 instruction map)
    # 0x60..0x67 → LD (r32+d8), R32
    # ============================================================
    if 0xB8 <= b <= 0xBF:
        if not _safe(data, pos, 3): return None
        r32_idx = b & 0x07
        d = s8(data, pos+1)
        op = data[pos+2]
        sign = '+' if d >= 0 else ''
        mem_str = f'({R32[r32_idx]}{sign}{d})'

        if 0x40 <= op <= 0x47:
            return (3, 'ld', f'{mem_str}, {R8[op & 7]}', refs, warn)
        if 0x50 <= op <= 0x57:
            return (3, 'ld', f'{mem_str}, {R16[op & 7]}', refs, warn)
        if 0x60 <= op <= 0x67:
            return (3, 'ld', f'{mem_str}, {R32[op & 7]}', refs, warn)
        # Our assembler uses 0x30/0x31 for store (alternate encoding)
        if 0x30 <= op <= 0x37:
            # Ambiguous: could be R16 or R32 — use R16 (common case)
            return (3, 'ld', f'{mem_str}, {R16[op & 7]}', refs, warn)
        if 0x38 <= op <= 0x3F:
            return (3, 'ld', f'{mem_str}, {R32[op & 7]}', refs, warn)
        # Byte store variant 0x31+R8 (our toolchain)
        # Already covered by 0x30-0x37 above; handle specifically if needed

    # ============================================================
    # abs16 byte load — C1 lo hi (0x20+R8)
    # ============================================================
    if b == 0xC1:
        if not _safe(data, pos, 4): return None
        addr = u16(data, pos+1)
        op   = data[pos+3]
        if 0x20 <= op <= 0x27:
            r_idx = op & 0x07
            mem_s, anm = fmt_mem(addr)
            s = f'{R8[r_idx]}, {mem_s}'
            if anm: s += f'  ; {anm}'
            return (4, 'ld', s, refs, warn)

    # ============================================================
    # abs16 word load — D1 lo hi (0x20+R16)  (SAFE — not D0 ALU)
    # ============================================================
    if b == 0xD1:
        if not _safe(data, pos, 4): return None
        addr = u16(data, pos+1)
        op   = data[pos+3]
        if 0x20 <= op <= 0x27:
            r_idx = op & 0x07
            mem_s, anm = fmt_mem(addr)
            s = f'{R16[r_idx]}, {mem_s}'
            if anm: s += f'  ; {anm}'
            return (4, 'ld', s, refs, warn)
        # If NOT in 0x20-0x27, fall through to warn as D0-family ALU
        warn = '!BROKEN? D1 as word-BC ALU prefix'
        return (2, 'db', f'0xD1, 0x{op:02X}', refs, warn)

    # ============================================================
    # abs16 long load — E1 lo hi (0x20+R32)
    # ============================================================
    if b == 0xE1:
        if not _safe(data, pos, 4): return None
        addr = u16(data, pos+1)
        op   = data[pos+3]
        if 0x20 <= op <= 0x27:
            r_idx = op & 0x07
            mem_s, anm = fmt_mem(addr)
            s = f'{R32[r_idx]}, {mem_s}'
            if anm: s += f'  ; {anm}'
            return (4, 'ld', s, refs, warn)

    # ============================================================
    # Post-increment store — C5 r32 (0x40+R8)
    # Encoding: [0xC5, r32_idx, 0x40+R8_idx]
    # ============================================================
    if b == 0xC5:
        if not _safe(data, pos, 3): return None
        r32_idx = data[pos+1]
        op = data[pos+2]
        if r32_idx <= 7 and 0x40 <= op <= 0x47:
            r8_idx = op & 0x07
            return (3, 'ld', f'({R32[r32_idx]}+), {R8[r8_idx]}', refs, warn)

    # ============================================================
    # Post-increment load — 0x80 0x15 r32 (0x20+R8)
    # Our toolchain: [0x80, 0x15, r32_idx, 0x20+r8_idx]
    # ============================================================
    if b == 0x80 and _safe(data, pos, 4) and data[pos+1] == 0x15:
        r32_idx = data[pos+2]
        op = data[pos+3]
        if r32_idx <= 7 and 0x20 <= op <= 0x27:
            r8_idx = op & 0x07
            return (4, 'ld', f'{R8[r8_idx]}, ({R32[r32_idx]}+)', refs, warn)

    # ============================================================
    # General fallback using _retmem_info — covers all other mem modes
    # (ARI_XWA..XSP, ARI_PD, ARI_PI, ARI, ABS_B, ABS_L, etc.)
    # ============================================================
    n, mem_str, addr_val = _retmem_info(data, pos, mem)
    if n is None:
        return None
    if not _safe(data, pos, n + 1):
        return None
    op = data[pos + n]
    ann = ''
    if addr_val is not None:
        _, anm = fmt_mem(addr_val)
        if anm: ann = f'  ; {anm}'

    # Select register set from zz
    if zz == 0:   reg_set = R8
    elif zz == 1: reg_set = R16
    else:         reg_set = R32

    # LD R, (mem) — 0x20+R
    if 0x20 <= op <= 0x27:
        return (n+1, 'ld', f'{reg_set[op & 7]}, {mem_str}{ann}', refs, warn)
    # ALU R, (mem)
    for base_op, mnem in [(0x80,'add'),(0x90,'adc'),(0xA0,'sub'),(0xB0,'sbc'),
                           (0xC0,'and'),(0xD0,'xor'),(0xE0,'or'),(0xF0,'cp')]:
        if base_op <= op < base_op + 8:
            return (n+1, mnem, f'{reg_set[op & 7]}, {mem_str}{ann}', refs, warn)
    # EX (mem), R — 0x30+R
    if 0x30 <= op <= 0x37:
        return (n+1, 'ex', f'{mem_str}, {reg_set[op & 7]}{ann}', refs, warn)
    # ADD/SUB/AND/XOR/OR/CP (mem), R — 0x88+R range
    for base_op, mnem in [(0x88,'add'),(0x98,'adc'),(0xA8,'sub'),(0xB8,'sbc'),
                           (0xC8,'and'),(0xD8,'xor'),(0xE8,'or'),(0xF8,'cp')]:
        if base_op <= op < base_op + 8:
            return (n+1, mnem, f'{mem_str}, {reg_set[op & 7]}{ann}', refs, warn)
    # INC/DEC n, (mem) — 0x60+n / 0x68+n
    if 0x60 <= op <= 0x67:
        cnt = (op & 7) or 8
        return (n+1, 'inc', f'{cnt}, {mem_str}{ann}', refs, warn)
    if 0x68 <= op <= 0x6F:
        cnt = (op & 7) or 8
        return (n+1, 'dec', f'{cnt}, {mem_str}{ann}', refs, warn)
    # PUSH (mem) — 0x04
    if op == 0x04:
        return (n+1, 'push', f'{mem_str}{ann}', refs, warn)
    # RLD/RRD [A], (mem) — 0x06/0x07
    if op == 0x06: return (n+1, 'rld', f'[A], {mem_str}{ann}', refs, warn)
    if op == 0x07: return (n+1, 'rrd', f'[A], {mem_str}{ann}', refs, warn)
    # Rotates/shifts (mem) — 0x78..0x7F
    shift_mem = {0x78:'rlc',0x79:'rrc',0x7A:'rl',0x7B:'rr',
                 0x7C:'sla',0x7D:'sra',0x7E:'sll',0x7F:'srl'}
    if op in shift_mem:
        return (n+1, shift_mem[op], f'{mem_str}{ann}', refs, warn)
    # ALU (mem), #imm — 0x38..0x3F
    alu_imm = {0x38:'add',0x39:'adc',0x3A:'sub',0x3B:'sbc',
               0x3C:'and',0x3D:'xor',0x3E:'or',0x3F:'cp'}
    if op in alu_imm:
        if zz == 0:
            if not _safe(data, pos, n + 2): return None
            imm = data[pos + n + 1]
            return (n+2, alu_imm[op], f'{mem_str}, 0x{imm:02X}{ann}', refs, warn)
        elif zz == 1:
            if not _safe(data, pos, n + 3): return None
            imm = u16(data, pos + n + 1)
            return (n+3, alu_imm[op], f'{mem_str}, 0x{imm:04X}{ann}', refs, warn)
    # MUL/MULS/DIV/DIVS RR, (mem) — 0x40..0x5F (dest one size wider than zz)
    if 0x40 <= op <= 0x5F:
        mul_ops = {0x40:'mul', 0x48:'muls', 0x50:'div', 0x58:'divs'}
        for base, mnem in mul_ops.items():
            if base <= op < base + 8:
                dr = R16[op & 7] if zz == 0 else R32[op & 7]
                return (n+1, mnem, f'{dr}, {mem_str}{ann}', refs, warn)
    # LDI/LDIR/LDD/LDDR/CPI/CPIR/CPD/CPDR — 0x10..0x17
    _block_ops = {0x10:'ldi', 0x11:'ldir', 0x12:'ldd', 0x13:'lddr',
                  0x14:'cpi', 0x15:'cpir', 0x16:'cpd', 0x17:'cpdr'}
    if op in _block_ops:
        return (n+1, _block_ops[op], mem_str, refs, warn)
    # LD (nn), (mem) — 0x19  (dest=nn abs16, src=mem indirect, per datasheet §5)
    if op == 0x19:
        if not _safe(data, pos, n + 3): return None
        nn = u16(data, pos + n + 1)
        nn_s, nn_anm = fmt_mem(nn)
        ann2 = f'  ; {nn_anm}' if nn_anm else ''
        return (n+3, 'ld', f'{nn_s}, {mem_str}{ann2}', refs, warn)

    return None

# ============================================================
# _retmem_info — decode the addressing-mode bytes for a given mem index
#
# Called by decode_zz_mem and decode_B0_mem after extracting mem=_getmem(b).
# pos points to the first byte (the prefix byte b), not the byte after it.
#
# Returns (n_consumed, mem_str, addr_val_or_None) or (None, None, None) on error.
#   n_consumed : total bytes consumed INCLUDING the prefix byte b.
#   mem_str    : human-readable address expression, e.g. "(XWA+4)" or "(0x8032)".
#   addr_val   : numeric address if statically known (for HW annotation), else None.
#
# Trailing operation byte is NOT consumed here — callers read data[pos + n_consumed].
# ============================================================

def _retmem_info(data, pos, mem):
    # mem 0..7  — ARI: register-indirect (r32), no extra bytes
    if 0 <= mem <= 7:
        return (1, f'({R32[mem]})', None)

    # mem 8..15 — ARID: register-indirect with signed 8-bit displacement (r32+d8)
    elif 8 <= mem <= 15:
        if not _safe(data, pos, 2): return (None, None, None)
        d = s8(data, pos+1)
        sign = '+' if d >= 0 else ''
        return (2, f'({R32[mem-8]}{sign}{d})', None)

    # mem 16 — ABS_B: 1-byte absolute address (I/O space 0x00..0xFF)
    elif mem == 16:
        if not _safe(data, pos, 2): return (None, None, None)
        addr = data[pos+1]
        return (2, f'(0x{addr:02X})', addr)

    # mem 17 — ABS_W: 2-byte absolute address (0x0000..0xFFFF, covers all HW registers)
    #   Used by C1 (byte), D1 (word), E1 (lword) prefix bytes in decode_zz_mem
    elif mem == 17:
        if not _safe(data, pos, 3): return (None, None, None)
        addr = u16(data, pos+1)
        return (3, f'(0x{addr:04X})', addr)

    # mem 18 — ABS_L: 3-byte absolute address (full 24-bit address space)
    elif mem == 18:
        if not _safe(data, pos, 4): return (None, None, None)
        addr = u24(data, pos+1)
        return (4, f'(0x{addr:06X})', addr)

    # mem 19 — ARI with secondary byte: encodes (r32), (r32+d16), or (r32+reg)
    #   Second byte layout: [r32_upper6:6][mode:2]
    #   r32 index = (byte >> 2) & 0x07  — upper-6-bits of full current-bank code, masked to 0..7
    #   mode 0x00 → (r32)       — same as mem 0..7 but via secondary byte
    #   mode 0x01 → (r32+d16)   — with signed 16-bit displacement (4 bytes total)
    #   mode 0x03 → (r32+R8)    — indexed, bit2 of secondary byte = 0
    #   mode 0x03 → (r32+R16)   — indexed, bit2 of secondary byte = 1
    #   For indexed (mode=3): third byte = r32 base code, fourth byte = r8/r16 code
    #   All register codes use formula (byte >> 2) & 0x07 (current-bank full code → index 0..7)
    elif mem == 19:
        if not _safe(data, pos, 2): return (None, None, None)
        b2 = data[pos+1]
        mode    = b2 & 0x03
        r32_idx = (b2 >> 2) & 0x07
        if mode == 0x00:                 # (r32) — secondary-byte form
            return (2, f'({R32[r32_idx]})', None)
        elif mode == 0x01:               # (r32+d16) — displacement 16
            if not _safe(data, pos, 4): return (None, None, None)
            d = s16(data, pos+2)
            sign = '+' if d >= 0 else ''
            return (4, f'({R32[r32_idx]}{sign}{d})', None)
        elif mode == 0x03:               # (r32+reg) — indexed by R8 or R16
            if not _safe(data, pos, 4): return (None, None, None)
            r32_i2 = (data[pos+2] >> 2) & 0x07
            r_i2   = (data[pos+3] >> 2) & 0x07
            if b2 & 0x04:
                return (4, f'({R32[r32_i2]}+{R16[r_i2]})', None)  # indexed by R16
            else:
                return (4, f'({R32[r32_i2]}+{R8[r_i2]})', None)   # indexed by R8
        return (None, None, None)

    # mem 20 — ARI_PD: pre-decrement (-r32)
    #   r32 index = (secondary >> 2) & 0x07  (upper-6-bits of full current-bank code)
    elif mem == 20:
        if not _safe(data, pos, 2): return (None, None, None)
        r32_idx = (data[pos+1] >> 2) & 0x07
        return (2, f'(-{R32[r32_idx]})', None)

    # mem 21 — ARI_PI: post-increment (r32+)
    #   r32 index = (secondary >> 2) & 0x07  (upper-6-bits of full current-bank code)
    elif mem == 21:
        if not _safe(data, pos, 2): return (None, None, None)
        r32_idx = (data[pos+1] >> 2) & 0x07
        return (2, f'({R32[r32_idx]}+)', None)

    # mem >= 23 — register-source forms (C8+zz+r prefix) — should be routed to decode_zz_r
    # This path means a caller passed an unexpected mem value.
    return (None, None, None)

# ============================================================
# decode_B0_mem — B0 memory forms (abs stores, RET cc, JP/CALL/LD indirect)
# First byte has getzz == 3 (bits 5:4 = 11), so 0xB0-0xBF or 0xF0-0xFF
# ============================================================

def decode_B0_mem(data, pos, cur_addr, base):
    b = data[pos]
    refs = []
    warn = None

    # ---- RET cc  — 0xB0 + 0xF0+cc ----
    if b == 0xB0 and _safe(data, pos, 2) and data[pos+1] >= 0xF0:
        cc_idx = data[pos+1] & 0x0F
        if cc_idx == 8:
            return (2, 'ret', '', refs, warn)
        return (2, 'ret', CC[cc_idx], refs, warn)

    # ---- LDAR R, $+4+d16  — 0xF3 0x13 d16 op ----
    if b == 0xF3 and _safe(data, pos, 2) and data[pos+1] == 0x13:
        if not _safe(data, pos, 5): return None
        d = s16(data, pos+2)
        target = (cur_addr + 4 + d) & 0xFFFFFF
        op = data[pos+4]
        r_idx = op & 0x07
        if op & 0x10:  # s-bit: 0=word(R16), 1=long(R32) — datasheet §5 LDAR encoding
            return (5, 'ldar', f'{R32[r_idx]}, 0x{target:06X}', refs, warn)
        return (5, 'ldar', f'{R16[r_idx]}, 0x{target:06X}', refs, warn)

    # ---- General retmem dispatch ----
    mem = _getmem(b)
    n, mem_str, addr_val = _retmem_info(data, pos, mem)
    if n is None:
        return None

    # Annotation for known HW addresses
    ann = ''
    if addr_val is not None:
        _, anm = fmt_mem(addr_val)
        if anm: ann = f'  ; {anm}'

    if not _safe(data, pos, n + 1):
        return None
    op = data[pos + n]

    # JP [cc,] (mem) — 0xD0+cc
    if 0xD0 <= op <= 0xDF:
        cc_idx = op & 0x0F
        if cc_idx == 8:
            return (n+1, 'jp', f'{mem_str}{ann}', refs, warn)
        return (n+1, 'jp', f'{CC[cc_idx]}, {mem_str}{ann}', refs, warn)

    # CALL [cc,] (mem) — 0xE0+cc
    if 0xE0 <= op <= 0xEF:
        cc_idx = op & 0x0F
        if cc_idx == 8:
            return (n+1, 'call', f'{mem_str}{ann}', refs, warn)
        return (n+1, 'call', f'{CC[cc_idx]}, {mem_str}{ann}', refs, warn)

    # LD (mem), R8 — 0x40+R8
    if 0x40 <= op <= 0x47:
        return (n+1, 'ld', f'{mem_str}, {R8[op & 7]}{ann}', refs, warn)

    # LD (mem), R16 — 0x50+R16
    if 0x50 <= op <= 0x57:
        return (n+1, 'ldw', f'{mem_str}, {R16[op & 7]}{ann}', refs, warn)

    # LD (mem), R32 — 0x60+R32
    if 0x60 <= op <= 0x67:
        return (n+1, 'ld', f'{mem_str}, {R32[op & 7]}{ann}', refs, warn)

    # LDA R16, (mem) — 0x20+R16
    if 0x20 <= op <= 0x27:
        return (n+1, 'lda', f'{R16[op & 7]}, {mem_str}{ann}', refs, warn)

    # LDA R32, (mem) — 0x30+R32
    if 0x30 <= op <= 0x37:
        return (n+1, 'lda', f'{R32[op & 7]}, {mem_str}{ann}', refs, warn)

    # LD (mem), #imm8 — 0x00 imm8
    if op == 0x00:
        if not _safe(data, pos, n + 2): return None
        imm8 = data[pos + n + 1]
        return (n+2, 'ld', f'{mem_str}, 0x{imm8:02X}{ann}', refs, warn)

    # LDW (mem), #imm16 — 0x02 lo hi
    if op == 0x02:
        if not _safe(data, pos, n + 3): return None
        imm16 = u16(data, pos + n + 1)
        return (n+3, 'ldw', f'{mem_str}, 0x{imm16:04X}{ann}', refs, warn)

    # POP (mem) — 0x04
    if op == 0x04:
        return (n+1, 'pop', f'{mem_str}{ann}', refs, warn)

    # POPW (mem) — 0x06
    if op == 0x06:
        return (n+1, 'popw', f'{mem_str}{ann}', refs, warn)

    # LD (mem), (#16) — 0x14 lo hi
    if op == 0x14:
        if not _safe(data, pos, n + 3): return None
        src = u16(data, pos + n + 1)
        src_s, src_anm = fmt_mem(src)
        ann2 = f'  ; {src_anm}' if src_anm else ''
        return (n+3, 'ld', f'{mem_str}, {src_s}{ann2}', refs, warn)

    # LDW (mem), (#16) — 0x16 lo hi
    if op == 0x16:
        if not _safe(data, pos, n + 3): return None
        src = u16(data, pos + n + 1)
        src_s, src_anm = fmt_mem(src)
        ann2 = f'  ; {src_anm}' if src_anm else ''
        return (n+3, 'ldw', f'{mem_str}, {src_s}{ann2}', refs, warn)

    # Bit manipulation instructions
    if op == 0x28: return (n+1, 'andcf', f'A, {mem_str}{ann}', refs, warn)
    if op == 0x29: return (n+1, 'orcf',  f'A, {mem_str}{ann}', refs, warn)
    if op == 0x2A: return (n+1, 'xorcf', f'A, {mem_str}{ann}', refs, warn)
    if op == 0x2B: return (n+1, 'ldcf',  f'A, {mem_str}{ann}', refs, warn)
    if op == 0x2C: return (n+1, 'stcf',  f'A, {mem_str}{ann}', refs, warn)
    if 0x80 <= op <= 0x87: return (n+1, 'andcf', f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0x88 <= op <= 0x8F: return (n+1, 'orcf',  f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0x90 <= op <= 0x97: return (n+1, 'xorcf', f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0x98 <= op <= 0x9F: return (n+1, 'ldcf',  f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0xA0 <= op <= 0xA7: return (n+1, 'stcf',  f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0xA8 <= op <= 0xAF: return (n+1, 'tset',  f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0xB0 <= op <= 0xB7: return (n+1, 'res',   f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0xB8 <= op <= 0xBF: return (n+1, 'set',   f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0xC0 <= op <= 0xC7: return (n+1, 'chg',   f'{op & 7}, {mem_str}{ann}', refs, warn)
    if 0xC8 <= op <= 0xCF: return (n+1, 'bit',   f'{op & 7}, {mem_str}{ann}', refs, warn)

    return None

# ============================================================
# Main decode dispatcher
# ============================================================

def decode_one(data, pos, cur_addr, base):
    """Decode one instruction at data[pos] (cur_addr = ROM address of this byte).

    Returns (length, mnem, ops, refs, warn) or None on complete failure.
      length : byte count of this instruction (always >= 1)
      mnem   : mnemonic string (lowercase, e.g. 'ld', 'call', 'ldc')
      ops    : operands string (empty string if none)
      refs   : list of (target_addr, 'call'|'jump'|'data') for Pass 1 label collection
      warn   : warning string if broken opcode detected, else None

    Dispatch order (first match wins):
      1. decode_fixed   — exact single-byte opcodes and short patterns (NOP, RET, EI, SWI…)
      2. decode_xx      — 0x20..0x7F range: LD imm8/16/32, PUSH/POP, JR/JRL
      3. >= 0x80 with zz==3 → decode_B0_mem (stores, RET cc, indirect JP/CALL, LDAR)
      4. >= 0x80 with mem>=23 → decode_zz_r (register ALU, LINK, UNLK, LDC, shifts)
      5. >= 0x80 with mem<=21 → decode_zz_mem (indirect/abs loads/stores, LDIW, LDIRW)
      6. Fallback: emit raw 'db 0xXX' with '?? unknown opcode' warning
    """
    if pos >= len(data):
        return None

    b = data[pos]

    # 1. Try fixed opcodes first (catches NOP, RET, RETI, EI, DI, JP/CALL abs, SWI…)
    r = decode_fixed(data, pos, cur_addr, base)
    if r: return r

    # 2. 0x20..0x7F — LD imm, PUSH/POP R16/R32, JR/JRL, LD R32 imm32
    if b < 0x80:
        r = decode_xx(data, pos, cur_addr, base)
        if r: return r

    # 3..5. 0x80..0xFF — decode based on zz (bits[5:4]) and mem (_getmem)
    if b >= 0x80:
        zz  = (b & 0x30) >> 4
        mem = _getmem(b)

        if zz == 3:
            # B0_mem group: absolute stores (LD (abs),R), RET cc, indirect JP/CALL, LDAR
            r = decode_B0_mem(data, pos, cur_addr, base)
            if r: return r
        else:
            if mem >= 23:
                # C8+zz+r: ALU on register, LINK/UNLK, LDC, shifts, bit ops
                # Also handles broken D0..D7 (warn emitted via _zz_regs)
                r = decode_zz_r(data, pos, cur_addr, base)
                if r: return r
            elif mem <= 21:
                # Indirect addressing: (r32), (r32+d8), (abs8/16/24), post-inc/pre-dec
                # Also handles D0..D7 broken ALU prefix with inline warning
                r = decode_zz_mem(data, pos, cur_addr, base)
                if r: return r

    # Fallback: unrecognized byte → emit raw db directive
    return (1, 'db', f'0x{b:02X}', [], '?? unknown opcode')

# ============================================================
# ROM header parser
# ============================================================

def parse_header(data):
    """Parse NGPC ROM header. Returns dict or None."""
    if len(data) < HEADER_SIZE:
        return None
    copyright_s = data[0:28]
    if (copyright_s != b"COPYRIGHT BY SNK CORPORATION" and
        copyright_s != b" LICENSED BY SNK CORPORATION"):
        return None
    licensed = (copyright_s[0:1] == b' ')
    entry    = u32(data, 0x1C) if len(data) >= 0x20 else ROM_BASE + HEADER_SIZE
    sw_id    = u16(data, 0x20) if len(data) >= 0x22 else 0
    color    = (data[0x22] == 0x10) if len(data) >= 0x23 else False
    title    = data[0x24:0x30].rstrip(b' \x00').decode('ascii', errors='replace')
    return {
        'licensed': licensed,
        'entry':    entry,
        'sw_id':    sw_id,
        'color':    color,
        'title':    title,
        'base':     ROM_BASE,
    }

# ============================================================
# Label manager
# ============================================================

class LabelMap:
    def __init__(self):
        self._labels = {}   # addr -> name
        self._calls  = set()
        self._jumps  = set()

    def add_ref(self, addr, kind):
        if kind == 'call':
            self._calls.add(addr)
        elif kind == 'jump':
            self._jumps.add(addr)
        hw = annotate_addr(addr)
        if hw and addr not in self._labels:
            self._labels[addr] = hw

    def add_entry(self, addr):
        self._labels[addr] = 'entry_point'

    def finalize(self):
        """Generate labels for all referenced addresses."""
        for addr in self._calls:
            if addr not in self._labels:
                self._labels[addr] = f'sub_{addr:06X}'
        for addr in self._jumps:
            if addr not in self._labels:
                self._labels[addr] = f'loc_{addr:06X}'

    def get(self, addr):
        return self._labels.get(addr)

    def all_sorted(self):
        return sorted(self._labels.items())

# ============================================================
# Pattern recognition (prologue/epilogue)
# ============================================================

def detect_pattern(data, pos):
    """Return a comment string if a known structural pattern is detected at pos.

    Detected patterns:
      ED 0C d16  → LINK XIY, N  — t900cc.py/CC900 function prologue
                   annotated !BROKEN if N >= 5 (NGPC silicon bug)
      ED 0D      → UNLK XIY     — function epilogue
    Returns None if no pattern matched.
    """
    if pos + 4 <= len(data):
        # LINK XIY, N (ED = E8+5 = XIY prefix, 0C = LINK sub-op, d16 = displacement)
        if data[pos] == 0xED and data[pos+1] == 0x0C:
            n = s16(data, pos+2)
            if n >= 0:
                broken = ' !BROKEN' if n >= 5 else ''
                return f'; --- function prologue (link XIY, {n}){broken} ---'
        # UNLK XIY (ED = XIY prefix, 0D = UNLK sub-op)
        if data[pos] == 0xED and data[pos+1] == 0x0D:
            return '; --- function epilogue (unlk XIY) ---'
    return None

# ============================================================
# Linear sweep disassembler
# ============================================================

def disassemble(data, base, start=None, end=None, init_labels=None):
    """Two-pass linear sweep disassembler.

    Args:
      data        : raw ROM bytes (no header offset adjustment needed)
      base        : ROM base address (default 0x200000 for NGPC cartridges)
      start/end   : address range to disassemble (default = entire ROM)
      init_labels : pre-populated LabelMap (e.g. with entry_point from header)

    Pass 1 — label collection:
      Decode each instruction and record CALL/JP target addresses in LabelMap.
      Unknown bytes advance by 1 byte. Labels are finalized (sub_XXXXXX / loc_XXXXXX)
      after the full pass.

    Pass 2 — emit:
      Re-decode every instruction. For each:
        • emit label line if this address is a known target
        • emit detect_pattern() comment for prologue/epilogue markers
        • emit: ADDR: hex_bytes    mnem operands    ; comment
          comment = silicon warning (if broken opcode) OR cross-ref label (if CALL/JP)
          OR HW register annotation embedded in operand string

    Returns list of strings (one line per instruction + label lines).
    """
    if start is None: start = base
    if end   is None: end   = base + len(data)

    labels = init_labels if init_labels is not None else LabelMap()

    # --- Pass 1: collect labels ---
    pos = start - base
    while pos < len(data) and (base + pos) < end:
        cur_addr = base + pos
        r = decode_one(data, pos, cur_addr, base)
        if r is None:
            pos += 1
            continue
        length, mnem, ops, refs, warn = r
        for (ref_addr, kind) in refs:
            labels.add_ref(ref_addr, kind)
        pos += length if length > 0 else 1

    labels.finalize()

    # --- Pass 2: emit ---
    lines = []
    pos = start - base

    while pos < len(data) and (base + pos) < end:
        cur_addr = base + pos

        # Emit label if this address is a target
        lbl = labels.get(cur_addr)
        if lbl:
            lines.append(f'\n{lbl}:')

        # Pattern detection comment
        pat = detect_pattern(data, pos)
        if pat:
            lines.append(f'  {pat}')

        r = decode_one(data, pos, cur_addr, base)
        if r is None:
            b = data[pos]
            lines.append(f'0x{cur_addr:06X}: {b:02X}              db    0x{b:02X}  ; ?? undecodable')
            pos += 1
            continue

        length, mnem, ops, refs, warn = r

        # Format hex bytes
        end_byte = min(pos + length, len(data))
        hex_bytes = ' '.join(f'{data[i]:02X}' for i in range(pos, end_byte))

        # Build instruction text
        if ops:
            instr = f'{mnem:<8} {ops}'
        else:
            instr = mnem

        # Warning or cross-reference comment
        if warn:
            comment = f'  ; {warn}'
        else:
            comment = ''
            for (ref_addr, kind) in refs:
                lbl = labels.get(ref_addr)
                if lbl:
                    comment = f'  ; -> {lbl}'
                    break

        # Format: ADDR: hex   mnem ops  ; comment
        hex_col = f'{hex_bytes:<16}'
        lines.append(f'0x{cur_addr:06X}: {hex_col}  {instr}{comment}')

        pos += length if length > 0 else 1

    return lines

# ============================================================
# Main
# ============================================================

def _parse_hex(text, name):
    """Parse a CLI hex argument with friendly error reporting."""
    try:
        return int(text, 16)
    except (TypeError, ValueError):
        sys.stderr.write(
            f"[ngpc_disasm] error: --{name} expects a hex value, got: {text!r}\n")
        sys.exit(2)

def main():
    parser = argparse.ArgumentParser(
        description='NgpCraft NGPC/NGP Disassembler — TLCS-900 with NGPC annotations')
    parser.add_argument('rom', help='ROM file (.ngc / .ngp / .bin)')
    parser.add_argument('--base',  default=None, help='Override ROM base address (hex, with or without 0x prefix)')
    parser.add_argument('--start', default=None, help='Start disassembly address (hex, with or without 0x prefix)')
    parser.add_argument('--end',   default=None, help='End disassembly address (hex, with or without 0x prefix)')
    parser.add_argument('-o', '--output', default=None, help='Output file (default: stdout)')
    args = parser.parse_args()

    try:
        with open(args.rom, 'rb') as f:
            data = f.read()
    except FileNotFoundError:
        sys.stderr.write(f"[ngpc_disasm] error: ROM file not found: {args.rom}\n")
        sys.exit(1)
    except PermissionError:
        sys.stderr.write(f"[ngpc_disasm] error: cannot read {args.rom} (permission denied)\n")
        sys.exit(1)
    except OSError as e:
        sys.stderr.write(f"[ngpc_disasm] error: cannot open {args.rom}: {e}\n")
        sys.exit(1)

    # Parse ROM header
    hdr = parse_header(data)
    if hdr:
        base  = hdr['base']
        entry = hdr['entry']
        title = hdr['title']
        color = 'Color' if hdr['color'] else 'Monochrome'
        lic   = 'Licensed' if hdr['licensed'] else 'SNK'
    else:
        base  = ROM_BASE
        entry = ROM_BASE + HEADER_SIZE
        title = '(no header)'
        color = '?'
        lic   = '?'

    if args.base:
        base = _parse_hex(args.base, 'base')
    # Default start: skip header when ROM is detected (entry point or base+HEADER_SIZE)
    if args.start:
        start = _parse_hex(args.start, 'start')
    elif hdr:
        start = base + HEADER_SIZE  # skip header bytes — shown separately in banner
    else:
        start = base
    end = _parse_hex(args.end, 'end') if args.end else base + len(data)

    # Reject reversed range up front — silent empty output is worse than a clear error.
    if start > end:
        sys.stderr.write(
            f"[ngpc_disasm] error: --start (0x{start:X}) is greater than "
            f"--end (0x{end:X}) — nothing to disassemble.\n")
        sys.exit(2)

    labels = LabelMap()
    if hdr:
        labels.add_entry(entry)

    # Header banner
    size_kb = len(data) / 1024
    banner = [
        '; ============================================================',
        f'; NgpCraft Disassembler — {os.path.basename(args.rom)}',
        f'; Title    : {title}',
        f'; System   : NGPC {color}  ({lic})',
        f'; ROM size : {len(data)} bytes ({size_kb:.1f} KB)',
        f'; Base     : 0x{base:06X}',
    ]
    if hdr:
        banner += [
            f'; Entry    : 0x{entry:06X}',
            f'; Soft ID  : 0x{hdr["sw_id"]:04X}',
        ]
    banner += [
        '; ============================================================',
        '',
    ]
    if hdr:
        banner += [
            '; --- ROM Header (64 bytes) ---',
            f'0x{base:06X}: (header — 64 bytes, entry = 0x{entry:06X})',
            '',
        ]

    lines = disassemble(data, base, start, end, init_labels=labels)

    out_lines = banner + lines

    out_text = '\n'.join(out_lines) + '\n'

    if args.output:
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(out_text)
        except OSError as e:
            sys.stderr.write(f"[ngpc_disasm] error: cannot write {args.output}: {e}\n")
            sys.exit(1)
        print(f'[ngpc_disasm] Written {len(out_lines)} lines to {args.output}')
    else:
        sys.stdout.write(out_text)
    sys.exit(0)


if __name__ == '__main__':
    main()
