# ngpc_disasm — User Manual

**NgpCraft NGPC/NGP Disassembler**  
Single-file Python disassembler for Neo Geo Pocket Color / Neo Geo Pocket ROMs.  
Full TLCS-900/L1 decoder with NGPC-specific hardware register annotations.  
Implements the official Toshiba TMP95C061BFG datasheet (ALT00146).

Released under the **MIT License** — free to use, modify, and redistribute.  
Part of the [NgpCraft](https://github.com/ngpcraft) open-source toolchain.

---

## Requirements

- Python 3.6+
- No external dependencies

---

## Quick Start

```bash
# Disassemble an entire ROM (auto-detects entry point from header)
python ngpc_disasm.py game.ngc

# Disassemble a specific address range
python ngpc_disasm.py game.ngc --start 0x200040 --end 0x200200

# Write output to file
python ngpc_disasm.py game.ngc -o game_disasm.asm

# Raw binary (no ROM header) at a custom base address
python ngpc_disasm.py code.bin --base 0x200040
```

---

## Command-Line Options

| Option | Description |
|--------|-------------|
| `input` | ROM file to disassemble (`.ngc`, `.ngp`, or raw `.bin`) |
| `--base ADDR` | ROM base address in memory (default: `0x200000` for NGPC ROMs) |
| `--start ADDR` | First address to disassemble (default: ROM entry point or base) |
| `--end ADDR` | Last address to disassemble (default: end of file) |
| `-o FILE` | Write output to FILE instead of stdout |

All addresses accept hex with or without the `0x` prefix (`0x200040` or
`200040`).

### Exit codes

- `0` — success
- `1` — ROM file missing, unreadable, or output file cannot be written
- `2` — bad CLI arg (non-hex address, `--start` greater than `--end`)

Errors print a single-line stderr message rather than a Python traceback,
so the tool can be wrapped cleanly in shell scripts and CI pipelines.

---

## Output Format

```
; ============================================================
; NgpCraft Disassembler — game.ngc
; Title    : MYGAME
; System   : NGPC Color  (Licensed)
; ROM size : 65536 bytes (64.0 KB)
; Base     : 0x200000
; Entry    : 0x200040
; ============================================================

; --- ROM Header (64 bytes) ---
0x200000: (header — 64 bytes, entry = 0x200040)

entry_point:
0x200040: 08 6F 4E          ldb      (HW_WATCHDOG), 0x4E
0x200043: 47 00 60 00 00    ld       XSP, 0x00006000
0x200048: 06 00             ei       0
```

### Line format

```
ADDR: hex_bytes        MNEMONIC  OPERANDS    ; comment/annotation
```

- `ADDR` — 24-bit ROM address (always `0x2xxxxx` for cartridge code)
- `hex_bytes` — raw bytes of the instruction, space-separated
- Annotations appear as `; comment` on the same line

---

## Labels

The disassembler runs in **two passes**:

1. **Pass 1** — linear sweep collecting all jump/call targets
2. **Pass 2** — emit assembly with labels resolved

Labels are generated automatically:

| Prefix | Meaning |
|--------|---------|
| `entry_point:` | ROM entry point (from header) |
| `sub_2XXXXX:` | Target of a `call`/`calr` instruction |
| `loc_2XXXXX:` | Target of a `jp`/`jr`/`jrl`/`djnz` instruction |

Labels appear on the line immediately before the labeled instruction, and call/jump operands include a cross-reference comment: `; -> sub_200100`.

---

## Hardware Register Annotations

Any instruction that references a known NGPC hardware address is annotated automatically.

### Examples

```asm
0x200100: C1 82 6F 21    ld     A, (HW_JOYPAD)       ; = 0x6F82
0x200108: F2 CC 6F 40 67 ldw    (VBL_VECTOR_LO), WA  ; = 0x6FCC
0x200114: 08 6F 4E       ldb    (HW_WATCHDOG), 0x4E  ; = 0x006F
```

### Known registers

**CPU I/O (0x0000–0x00FF)**

| Address | Name | Description |
|---------|------|-------------|
| `0x0020` | `HW_TRUN` | Timer run control |
| `0x006F` | `HW_WATCHDOG` | Write `0x4E` to reset watchdog |
| `0x007C`–`0x007F` | `HW_DMA0V`–`HW_DMA3V` | DMA channel vectors |

**Memory-mapped (0x6F80–0xA000)**

| Address | Name | Description |
|---------|------|-------------|
| `0x6F82` | `HW_JOYPAD` | Joypad state (read) |
| `0x6F91` | `HW_OS_VERSION` | `0`=NGP mono, `!0`=NGPC color |
| `0x6FCC` | `VBL_VECTOR_LO` | VBlank ISR vector low word |
| `0x6FCE` | `VBL_VECTOR_HI` | VBlank ISR vector high word |
| `0x8002` | `K2GE_TRANSPARENCY` | K2GE transparency color |
| `0x8008`–`0x800B` | `K2GE_SCR1/2_SCROLL_X/Y` | Scroll plane offsets |
| `0x8800` | `SPR_VRAM_BASE` | Sprite VRAM (64 sprites × 4 bytes) |
| `0x9000` | `SCR1_MAP_BASE` | Scroll plane 1 tilemap |
| `0x9800` | `SCR2_MAP_BASE` | Scroll plane 2 tilemap |
| `0xA000` | `TILE_RAM_BASE` | Character/Tile RAM |

---

## BIOS Call Annotations

`swi` instructions are annotated with BIOS function names:

```asm
0x200040: F9    swi  1    ; BIOS_CLOCKGEARSET
0x200050: FD    swi  5    ; BIOS_SYSFONTSET
0x200060: FE    swi  6    ; BIOS_FLASHWRITE
```

| SWI | Name |
|-----|------|
| 0 | `BIOS_SHUTDOWN` |
| 1 | `BIOS_CLOCKGEARSET` |
| 2 | `BIOS_RTCGET` |
| 5 | `BIOS_SYSFONTSET` |
| 6 | `BIOS_FLASHWRITE` |
| 8 | `BIOS_FLASHERS` |
| 9 | `BIOS_ALARMSET` |

---

## Broken Opcode Detection

Instructions that are **known to crash or hang on NGPC silicon** are annotated with `; !BROKEN`:

```asm
0x200100: D0 61    inc   8, WA    ; !BROKEN D0..D7 ALU (word-reg prefix)
```

### Known broken opcodes on NGPC silicon

| Opcode(s) | Issue | Suggested fix |
|-----------|-------|---------------|
| `D0 xx` | D0 prefix (all sub-ops) — hangs watchdog | Use D8 (R32) family, e.g. `extz xwa; sll N, xwa` instead of `sll N, wa` |
| `D1..D7 xx` (ALU sub-ops) | Word-register ALU — broken (but D1/D2-D5 as abs-address loads are **safe**) | Switch to byte (C8..CF) or lword (D8..DF / E8..EF) source |
| `CB xx` | Byte ALU using R8[3]=C as source. `add A, C` (CB 81) hangs | Route through HL: `add A, L` (CF 81). Other byte-ALU prefixes (C8 W, C9 A, CA B, CC D, CD E, CE H, CF L) are confirmed safe |
| `LINK XIY, N` where N≥5 | Stack frame too large — corrupts SP | Use `link XIY, 0` then `add XSP, -N` to allocate locals |
| `adc W, B` when W > 0 | High-byte add produces wrong result silently | Keep W=0 (count ≤ 255) or split the add manually |

> **Note:** `D1` as abs16 load (`LD R16, (abs16)`) and `D2–D5` used as abs-address memory loads (`LD R16, (abs24)`, `LD R16, (r32)`, etc.) are **safe** and decode normally without a warning. The CB warning fires only on prefix `0xCB` specifically; the surrounding C8..CF family (W/A/B/D/E/H/L sources) is confirmed safe by CC900 production code.

> **About the `adc W, B` warning:** static disassembly cannot know the runtime value of `W`. The flag is unconditional — the reader is expected to verify intent. A safe `adc W, B` with `W==0` will still be flagged.

---

## Instruction Coverage

The decoder handles the full TLCS-900/L1 instruction set as documented in the TMP95C061BFG datasheet:

| Category | Instructions |
|----------|-------------|
| Fixed opcodes | `NOP`, `RET`, `RETI`, `RETD d16`, `EI n`, `DI`, `HALT`, `LDX`, `CALR d16`, `JP`, `CALL` |
| Flag / SR ops | `RCF`, `SCF`, `CCF`, `ZCF`, `INCF`, `DECF`, `LDF n`, `EX F,F'`, `PUSH`/`POP` SR/A/F |
| Load immediate | `LD R8, #imm8` / `LD R16, #imm16` / `LD R32, #imm32` |
| Stack | `PUSH`/`POP` R16/R32 |
| Branches | `JR cc, d8`, `JRL cc, d16`, `DJNZ r, d8`, `SCC cc, r` |
| Register ALU | `ADD`, `ADC`, `SUB`, `SBC`, `AND`, `XOR`, `OR`, `CP` (R,r and R,#imm) |
| Register ops | `LD R,r`, `INC`, `DEC`, `CPL`, `NEG`, `EXTZ`, `EXTS`, `DAA` |
| Stack frame | `LINK r32, d16`, `UNLK r32` |
| Indirect loads | `LD R, (r32)`, `(r32+d8)`, `(-r32)`, `(r32+)`, `(abs8)`, `(abs16)`, `(abs24)`, `(r32+R)`, `(r32+d16)` |
| Indirect stores | `LD (r32+d8), R`, `LD (mem), R` (all register sizes), `LD (mem), #imm` |
| B0+mem forms | `JP/CALL cc, (mem)`, `LDA R, (mem)`, `LDAR R, $+d16`, `POP/POPW (mem)` |
| Bit manipulation | `BIT`, `RES`, `SET`, `CHG`, `TSET`, `ANDCF`, `ORCF`, `XORCF`, `LDCF`, `STCF` |
| Shifts/rotates | `RLC`, `RRC`, `RL`, `RR`, `SLA`, `SRA`, `SLL`, `SRL` (register, immediate count, and memory forms) |
| Block copy/compare | `LDI`, `LDIR`, `LDD`, `LDDR`, `CPI`, `CPIR`, `CPD`, `CPDR`, `LDIW`, `LDIRW` |
| Multiply/Divide | `MUL RR, r` / `MUL RR, #imm`, `MULS`, `DIV`, `DIVS` (register and immediate forms) |
| RLD/RRD | Rotate BCD digit through accumulator |
| LDC | `LDC cr, r` / `LDC r, cr` — DMA registers annotated (`DMAC0`, `DMAS0`, `DMAD0`, `DMAM0`…) |

---

## ROM Header

NGPC ROMs have a 64-byte header at `0x200000`:

| Offset | Size | Content |
|--------|------|---------|
| `0x00` | 28 | Copyright string (`COPYRIGHT BY SNK CORPORATION` or ` LICENSED BY SNK CORPORATION`) |
| `0x1C` | 4 | Entry point (32-bit LE) |
| `0x20` | 2 | Software ID (BCD) |
| `0x22` | 1 | Color/mono flag: `0x00` = NGP mono, `0x10` = NGPC color |
| `0x23` | 1 | Reserved |
| `0x24` | 12 | Title (ASCII, space-padded) |
| `0x30` | 16 | Reserved (zeros) |

The disassembler auto-detects this header and uses the entry point, title, and color flag automatically.

---

## Understanding the Output

### Linear sweep limitations

The disassembler uses **linear sweep** — it decodes bytes sequentially from the start address. This means:

- **Jump tables** may be decoded as instructions (they aren't). Look for runs of `db` bytes between valid instruction sequences.
- **Data sections** embedded in code (e.g., ROM strings) will produce spurious instructions before the next real code.
- Use `--start` and `--end` to focus on known code regions.

### Unknown bytes

Bytes that cannot be decoded appear as:

```asm
0x200100: XX    db    0xXX    ; ?? unknown opcode
```

This usually means:
1. The byte is part of a data table (jump target offsets, strings, graphics data)
2. The preceding instruction was decoded with wrong length, breaking alignment
3. A genuinely rare/unused opcode (undefined slots per datasheet)

### Function boundaries

The disassembler detects `LINK`/`UNLK` prologue/epilogue patterns from the NgpCraft toolchain. These appear as natural function delimiters in the output.

For CC900-compiled code (official SNK toolchain), function boundaries are identified by call/return patterns and label detection.

---

## Working with Real ROMs

### NGPC color or mono ROM

```bash
python ngpc_disasm.py game.ngc
```

The disassembler auto-detects the ROM header and:
- Skips the 64-byte header (shown as a comment)
- Sets base address to `0x200000`
- Uses the header entry point as the start address
- Reports title, color/mono, and software ID

### Raw binary (no header)

```bash
python ngpc_disasm.py code.bin --base 0x200040
```

If no valid ROM header is detected, the file is treated as raw binary starting at `--base`.

### BIOS ROM

```bash
python ngpc_disasm.py ngp.bios --base 0xFF0000
```

The NGPC BIOS occupies `0xFF0000–0xFFFFFF` (64 KB).

---

## Known Limitations

- **Linear sweep only** — no recursive descent. Data sections embedded in code may confuse the decoder.
- **No symbol import** — labels are auto-generated (`sub_XXXXXX`, `loc_XXXXXX`). No way to import a symbol table.
- **D0 family** — decoded for annotation purposes but always flagged as BROKEN (correct behavior for NGPC silicon).
- **ARI mode 3 complex forms** — decoded when r32 index is in valid range; invalid combinations (which are data bytes) fall back to `db`.
- **Undefined opcodes** — slots that are blank in the datasheet Appendix C instruction map (e.g., `0x58–0x5F` in the `b < 0x80` range) are emitted as `db`.

---

## Toolchain Integration

| Tool | Purpose |
|------|---------|
| `t900as.py` | TLCS-900/L1 assembler (source of truth for opcode encoding) |
| `ngpc_romtool.py` | ROM packer / header inspector |
| `ngpc_disasm.py` | This disassembler |

The output format is designed to be compatible with `t900as.py` for round-trip study (disassemble → edit → re-assemble).
