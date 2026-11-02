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
0x200100: CB 81    add   A, C     ; !BROKEN add A,C (CB 81) confirmed silicon hang — use CF (L-source) instead
```

> **`D0..D7` is no longer flagged (fixed 2026-11-03).** `D0..D5` are the **word** members of
> the memory-addressing family, decoded exactly like `C0..C5` (byte) and `E0..E5` (long):
> `D2 AF 4D 00 61` = `incw 1, (0x004DAF)`, `D1 16 42 3F 02 00` = `cpw (0x4216), 0x0002`.
> `D7` is the word extended-register escape (`D7 FA 04` = `push QIZ`). The word
> register-direct prefix is `D8..DF`. Older versions printed these bytes as a
> `!BROKEN … word-reg ALU prefix` and lost the instruction length, which desynced the code
> that followed.

### Known broken opcodes on NGPC silicon

| Opcode(s) | Issue | Suggested fix |
|-----------|-------|---------------|
| `D0 C8..CF lo hi` (2026-05-20 crash) | The `stargunner_j16_C4_phase4_BROKEN_HW_20260520.ngc` **HW crash on 2026-05-20 was real**, but its cause was a toolchain **mis-emission**: it emitted `D0` intending a word-register-immediate (`<alu> WA, imm16`) — however `D0` is a **memory-addressing mode**, so the bytes executed as garbage. This is a code-generation bug, **not** a silicon defect. CC900 emits 0 instances of `<alu> WA, imm` and instead byte-splits. | Byte-split: `ld HL, imm; <alu> A, L; <carry> W, H` (the CC900 pattern, used for 25+ years) |
| `CB xx` (non-mul/div sub-ops) | Byte ALU using R8[3]=C as source. `add A, C` (CB 81) hangs. **SUB-OP-SPECIFIC:** only the byte-ALU sub-ops hang — byte **MUL/DIV** with C source (`CB` sub-op `0x40..0x5F`, e.g. `CB 51` = `div A, C`) is **HW-cleared, not broken** (see correction note below). | Route through HL: `add A, L` (CF 81). Other byte-ALU prefixes (C8 W, C9 A, CA B, CC D, CD E, CE H, CF L) are confirmed safe |
| `LINK XIY, N` where N≥5 | Stack frame too large — corrupts SP | Use `link XIY, 0` then `add XSP, -N` to allocate locals |
| `adc W, B` when W > 0 | High-byte add produces wrong result silently | Keep W=0 (count ≤ 255) or split the add manually |

> **Note:** `D0..D5` (word memory forms: `LD R16, (abs16)`, `incw 1, (abs24)`, `cpw (mem), #16`, …) are ordinary instructions and decode without a warning. The CB warning fires only on prefix `0xCB` **and only for its byte-ALU sub-ops** — the byte mul/div sub-ops (`0x40..0x5F`, e.g. `CB 51` = `div A, C`) decode without a warning (HW-cleared, see the correction note below). The surrounding C8..CF family (W/A/B/D/E/H/L sources) is confirmed safe by CC900 production code.

> **Correction (HW-confirmed 2026-07-08):** the **byte** mul/div register-to-register sub-family (`CB` sub-op `0x40..0x5F`; canonical `CB 51` = `div A, C`) is **not broken** — test ROM `hw_test_bytediv` on a real NGPC: `div A, C` with `WA=0x1F64`, `C=0x64` produced `WA=0x2450` (quotient `0x50`, remainder `0x24`), and the instruction is used by retail `mr_robot`. This narrows the `CB xx` broken-set: **only `add A, C` (CB 81) and the other non-mul/div CB byte-ALU sub-ops hang**; the byte mul/div sub-ops are HW-cleared, paralleling the WORD mul/div clearing (`D8..DF` sub-op `0x40..0x5F`, `hw_test_muldiv` 2026-07-06). The decoder still decodes these bytes as `div A, C`/`mul`/`divs`; only the `!BROKEN` annotation was narrowed.

> **Correction (HW-confirmed 2026-07-03):** `D8..DF` is a **16-bit WORD** register prefix (`R16`, WA…SP), **not** a 32-bit long-register prefix, and its ALU register-to-register forms are **not broken** — so `D8..DF` no longer appears in this table. Real-hardware test on a flashed NGPC ROM: `D8 89` (= `ld BC, WA`) with `XWA=0x11223344`, `XBC=0xAAAAAAAA` produced `XBC=0xAAAA3344` — a clean word copy (high 16 bits preserved); `D9 1C` (= `djnz BC`) with `XBC=0x00020000` produced `XBC=0x0002FFFF` (word decrement). The genuine 32-bit long prefix is `E8..EF` (`E8 89` = `ld XBC, XWA`). The earlier "broken working-bank long-register" belief was a size-classification error: `D8 8B` was read as the 32-bit `ld XHL, XWA` but is really the 16-bit `ld HL, WA`, which is why the toolchain's `_emit_copy_xwa_to_xhl` byte-split (2026-05-20) appeared necessary — the correct 32-bit copy is simply `E8 8B`. **The mul/div register-to-register sub-family (`D8..DF` sub-op `0x40..0x5F`; canonical `D9 50` = `div WA, BC`) is likewise HW-confirmed *not* broken** — test ROM `hw_test_muldiv` on a real NGPC (2026-07-06): `div WA, BC` with `XWA=0x000003E8`, `BC=0x000A` produced `XWA=0x00000064` (quotient 100 + remainder), disproving the old silicon-hang belief and paralleling the `add r+r` clearing (2026-07-05). Within the `D8..DF` word prefix family only **shift-by-A (`0xF8..0xFF`)** and the **`0xB8..0xBF` gap** remain silicon-broken.

> **Synchronisation:** the broken-set above mirrors the NgpCraft toolchain's hardware-validation history (`stargunner_j16_C4_phase4*` test ROMs) and the NgpCraft_emulator `core/quirks_db.json` `cpu.d0_d7_non_immediate` and `cpu.link_xiy_large_frame` entries. (The former `cpu.d8_df_register_to_register` quirk was retired 2026-07-03 once real hardware proved `D8..DF` is a working 16-bit word prefix — see the correction note above.) When the toolchain or emulator quirk database bumps, this table is the resync target.

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
| Extended-register escapes | `C7` / `D7` / `E7 <register code> <op>` = byte / word / long. Same instruction set as the short `C8+r` / `D8+r` / `E8+r` forms, with the register named by a full 8-bit code: `0xE0..0xFF` current bank, `0xD0..0xDF` previous bank (`XWA'`), `0x00..0xCF` absolute bank (`RA3`, `RWA3`, `XHL3`), plus the Q halves and IX/IY/IZ/SP bytes. Checked against asm900: `C7 31 A9` = `ld RW3,1`, `C7 30 CF 80` = `cp RA3,0x80`, `D7 FA 04` = `push QIZ`, `E7 3C 8D` = `ld XIY,XHL3`. |
| Size suffix | Memory operations with no register operand to carry the size get `w`: `C1 66 40 61` = `inc 1,(0x4066)`, `D1 66 40 61` = `incw 1,(0x4066)`. Same for `push`, shifts, ALU `(mem),#`, `ld (nn),(mem)` and block ops. None of these has a long form (asm900 refuses them), so `E0..E5` + one of these sub-ops decodes as `db`. |

---

## ROM Header

NGPC ROMs have a 64-byte header at `0x200000`:

| Offset | Size | Content |
|--------|------|---------|
| `0x00` | 28 | Copyright string (`COPYRIGHT BY SNK CORPORATION` or ` LICENSED BY SNK CORPORATION`) |
| `0x1C` | 4 | Entry point (32-bit LE) |
| `0x20` | 2 | Software ID (BCD) |
| `0x22` | 1 | Sub-code / version |
| `0x23` | 1 | Compatible system: `0x00` = NGP mono, `0x10` = NGPC color |
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
- **Indexed modes** — `(r32+r8)` / `(r32+r16)` take mode byte `03` / `07` and two extended register codes (`C3 03 EC E0` = `(XHL+A)`); any other mode byte ending in `11` is data and falls back to `db`.
- **Register forms are lenient** — some long-size register forms that do not exist (e.g. `mul` / `ex` / `daa` / bit ops on an `E8+r` register) still decode. They only show up when the sweep runs through data.
- **Undefined opcodes** — slots that are blank in the instruction map (e.g. `0x50–0x57`) are emitted as `db`. `0x58–0x5F` is `pop XWA..XSP` (`5D 5C 0E` = `pop XIY / pop XIX / ret`, every SNK epilogue).

---

## Toolchain Integration

| Tool | Purpose |
|------|---------|
| `t900as.py` | TLCS-900/L1 assembler (source of truth for opcode encoding) |
| `ngpc_romtool.py` | ROM packer / header inspector |
| `ngpc_disasm.py` | This disassembler |

The output format is designed to be compatible with `t900as.py` for round-trip study (disassemble → edit → re-assemble).
