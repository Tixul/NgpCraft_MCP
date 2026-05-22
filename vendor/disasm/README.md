# ngpc_disasm

**TLCS-900/L1 disassembler for Neo Geo Pocket Color / Neo Geo Pocket ROMs**

Single-file, zero-dependency Python disassembler for NGPC and NGP cartridges.  
Part of the [NgpCraft](https://github.com/ngpcraft) open-source toolchain.

Implements the full TLCS-900/L1 instruction set from the official Toshiba TMP95C061BFG datasheet (ALT00146).

---

## Features

- **Full TLCS-900/L1 decoder** — fixed opcodes, all prefix families (B0/C8/D8/E8), indirect and immediate addressing, bit ops, shifts, block ops, multiply/divide
- **All addressing modes** — register-indirect, pre-decrement, post-increment, abs8/16/24, register+displacement, register-indexed
- **NGPC hardware register annotations** — joypad, VBlank vector, watchdog, K2GE, sprite VRAM, scroll planes, tile RAM
- **BIOS SWI names** — `swi 1` → `BIOS_CLOCKGEARSET`, `swi 5` → `BIOS_SYSFONTSET`, etc.
- **DMA LDC register names** — `DMAC0`, `DMAS0`, `DMAD0`, `DMAM1`…
- **Broken opcode detection** — `D0` prefix, `CB` family (`add A, C` and friends), `LINK XIY, N≥5`, and `adc W, B` (silent wrong result when W>0) all flagged `; !BROKEN <reason — fix>` inline. The CB and `adc W, B` flags were wired up in 2026-04 to match the README's claims.
- **Two-pass label resolution** — `entry_point:`, `sub_2XXXXX:` (call targets), `loc_2XXXXX:` (jump targets) with `; -> sub_XXXXXX` cross-references on every call/jump
- **Auto ROM header parsing** — detects title, entry point, color/mono, software ID from the 64-byte SNK header

---

## Quick Start

```bash
# Disassemble a full NGPC ROM (entry point from header)
python ngpc_disasm.py game.ngc

# Disassemble a specific address range
python ngpc_disasm.py game.ngc --start 0x200040 --end 0x200200

# Write output to file
python ngpc_disasm.py game.ngc -o game.asm

# Raw binary at a custom base address
python ngpc_disasm.py code.bin --base 0x200040

# NGPC BIOS ROM
python ngpc_disasm.py ngp.bios --base 0xFF0000
```

**Requirements:** Python 3.6+, no external dependencies.

---

## Output Example

```asm
; ============================================================
; NgpCraft Disassembler — stargunner_j16.ngc
; Title    : SHMUP
; System   : NGPC Monochrome  (Licensed)
; ROM size : 177420 bytes (173.3 KB)
; Base     : 0x200000
; Entry    : 0x200040
; ============================================================

entry_point:
0x200040: 08 6F 4E          ldb      (HW_WATCHDOG), 0x4E
0x200043: 47 00 60 00 00    ld       XSP, 0x00006000
0x200048: 45 00 40 00 00    ld       XIY, 0x00004000
0x20004D: 30 72 15          ld       WA, 0x1572
0x200050: C8 E1             or       A, W
0x200052: 66 16             jr       Z, 0x20006A   ; -> loc_20006A

loc_200054:
0x200054: 28                push     WA
0x200055: 21 00             ld       A, 0x00
0x200057: BD 00 41          ld       (XIY+0), A
0x20005A: ED 61             inc      1, XIY
```

---

## Validation

Tested against real NGPC cartridges:

| ROM | Size | Notes |
|-----|------|-------|
| Stargunner *(homebrew, NgpCraft)* | 173 KB | Source available — all unknowns are data |
| Dark Arms Beast Buster *(SNK, official)* | 2 MB | CC900-compiled — all code correctly aligned |
| Delta Warp *(official)* | 512 KB | All unknowns are graphics tiles |

Unknown bytes in code regions are always data sections (tiles, strings, jump tables) — no missing code opcodes.

---

## Command-Line Reference

| Option | Description |
|--------|-------------|
| `input` | ROM file (`.ngc`, `.ngp`, or raw `.bin`) |
| `--base ADDR` | ROM base address (default: `0x200000`) |
| `--start ADDR` | First address to disassemble (default: entry point) |
| `--end ADDR` | Last address to disassemble (default: end of file) |
| `-o FILE` | Write to file instead of stdout |

All addresses accept hex with or without the `0x` prefix (`0x200040` or
`200040`). Bad input is rejected up-front with a clear stderr message and
a non-zero exit code: missing ROM (`1`), permission denied (`1`), bad hex
(`2`), `--start` greater than `--end` (`2`). Successful runs exit `0`.

---

## Instruction Coverage

| Category | Instructions |
|----------|-------------|
| Fixed opcodes | `NOP`, `RET`, `RETI`, `RETD`, `EI`, `DI`, `HALT`, `PUSH/POP SR/A/F`, `LDF`, `INCF`, `DECF`, `SWI 0-7`, `LDX`, `CALR`, `JP`, `CALL` |
| Load immediate | `LD R8/R16/R32, #imm` |
| Stack | `PUSH`/`POP` R16/R32 |
| Branches | `JR cc, d8`, `JRL cc, d16`, `CALR d16`, `DJNZ`, `SCC` |
| C8+zz+r ALU | `ADD`, `ADC`, `SUB`, `SBC`, `AND`, `XOR`, `OR`, `CP`, `LD`, `INC`, `DEC`, `CPL`, `NEG`, `EXTZ`, `EXTS`, `DAA` |
| E8+r special | `LINK`, `UNLK`, `EXTZ`, `EXTS` |
| Indirect loads | `LD R, (r32)`, `LD R, (r32+d8)`, `LD R, (abs8/16/24)`, `LD R, (-r32)`, `LD R, (r32+)` |
| Indirect stores | `LD (r32+d8), R`, `LD (abs16/24), R`, `LD (mem), #imm` |
| B0+mem forms | `JP/CALL cc, (mem)`, `LD (mem), R`, `LDA R, (mem)`, `LDAR`, `POP/POPW (mem)` |
| Bit manipulation | `BIT`, `RES`, `SET`, `CHG`, `TSET`, `ANDCF`, `ORCF`, `XORCF`, `LDCF`, `STCF` |
| Shifts/rotates | `RLC`, `RRC`, `RL`, `RR`, `SLA`, `SRA`, `SLL`, `SRL` (register and memory forms) |
| Block ops | `LDI`, `LDIR`, `LDD`, `LDDR`, `CPI`, `CPIR`, `CPD`, `CPDR`, `LDIW`, `LDIRW` |
| Multiply/Divide | `MUL`, `MULS`, `DIV`, `DIVS` (register and immediate forms) |
| LDC | `LDC cr, r` / `LDC r, cr` with DMA register names (`DMAC0`, `DMAS0`…) |
| RLD/RRD | Rotate digit through accumulator |

---

## NgpCraft Toolchain

| Tool | Purpose |
|------|---------|
| `t900as.py` | TLCS-900/L1 assembler |
| `ngpc_romtool.py` | ROM packer / header inspector |
| `ngpc_disasm.py` | This disassembler |

The output format is designed to be compatible with `t900as.py` for round-trip study (disassemble → edit → re-assemble).

---

## Reference

Instruction set from the official Toshiba **TMP95C061BFG** datasheet (ALT00146, publicly available on Mouser and Toshiba). NGPC hardware annotations from `HW_REGISTERS.md` and silicon testing notes.

---

## License

MIT — see [LICENSE](LICENSE).
