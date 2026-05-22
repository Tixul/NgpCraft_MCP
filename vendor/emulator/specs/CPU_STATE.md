# CPU State v1

Purpose:
- define the modeled CPU state container for TLCS-900/H on NGPC
- separate the architectural register model from reset-value knowledge
- carry the full SR (status register) shape so future opcodes that read
  or write SR (PUSH SR, POP SR, LDF, EX F,F') can round-trip honestly

Current source references:
- `../NgpCraft_toolchain/T900_DENSE_REF.md` §31 (SR bit layout)
- `../NgpCraft_toolchain/NGPC_REVERSE_REFERENCE.md`
- `../../01_SDK/docs/ngpcspec.txt`

## 1. Architectural state

Modeled architecture:
- 32-bit registers:
  - `XWA`, `XBC`, `XDE`, `XHL`, `XIX`, `XIY`, `XIZ`, `XSP`
- `PC` (24-bit address bus, stored as int)
- `SR` raw 16-bit (optional cache; canonical state lives in the individual
  fields below)
- flags subset, with all six TLCS-900/H ALU flags:
  - `SF` (sign), `ZF` (zero), `VF` (parity / overflow),
  - `HF` (half-carry), `CF` (carry),
  - `NF` (add/subtract, set after subtractive ops, cleared after additive
    or logical ops)
- `iff_level` — interrupt mask level 0..7 (`SR[12:14]`)
- `iff_enabled` — derived legacy convenience: `True` iff `iff_level < 7`
- `rfp` — Register File Pointer 0..3 (`SR[8:10]`); selects the active
  register bank

## 2. SR (status register) bit layout

TLCS-900/H NGPC silicon, per `T900_DENSE_REF.md` §31:

```
Bit  Name   Description
 0   C      Carry flag
 1   N      Add/Subtract (BCD)
 2   V      Parity / Overflow
 4   H      Half Carry
 6   Z      Zero
 7   S      Sign
 8-10 RFP   Register File Pointer (bank 0..3)
11   MAX    Maximum mode (always 1 on NGPC TLCS-900/H)
12-14 IFF   Interrupt mask level (0..7)
15   SYSM   System Mode (always 1 on NGPC TLCS-900/H)
```

Helpers in `core/cpu.py`:
- `encode_sr_from_state(state)` — encode the canonical fields into a
  16-bit raw value, returns `None` if any required field is unknown
- `decode_sr_to_fields(sr_raw)` — decode a raw value into individual
  fields (`sf/zf/vf/hf/cf/nf/iff_level/rfp`); `MAX` and `SYSM` are not
  returned (on NGPC they are read as 1 and not separately modeled)

## 3. NGPC-specific simplifications vs. full TLCS-900 family

NGPC uses TLCS-900/H (TMP95C061). Per `T900_DENSE_REF.md` §32:
- `MAX` mode is permanent (no `MIN` mode)
- System privilege only (no User mode separation)
- single IFF mask level (no IFF1/IFF2 distinction like Z80)
- vector-based interrupt method (not Restart)
- `INTNEST` register exists (not yet modeled)

These simplifications mean the emulator does not need:
- a `supervisor_mode` boolean (always System)
- an `iff2` shadow field (a single 3-bit `iff_level` is canonical)
- a `min_mode` flag (always MAX)

## 4. Bootstrap truth level

- `PC` is derived from the ROM header entry point
- all other register values remain unknown until verified by a real
  reset sequence or seeded explicitly via `--seed-reg`
- `SR`, individual flags, `iff_level` and `rfp` are intentionally left
  unknown at bootstrap
- the bootstrap convention `--seed-zero-bank0` populates `XWA/XBC/XDE/XHL/XIX/XIY=0`
  (cc900 / cdecl / adecl crt0 convention) but does not touch SR

## 5. Why the model is acceptable as v1

- the project now has a stable CPU state shape that matches the documented
  SR layout, so future PUSH SR / POP SR / LDF / EX F,F' implementations
  can round-trip without revisiting the data model
- reset-value accuracy will come later from docs and tests
- the model avoids fake precision: unknown fields stay `None`

## 6. What is not yet modeled

- `LDF imm` (load new RFP value) — needs RFP write semantics + bank
  switching effects on the visible register window
- `EX F, F'` (exchange flag set with shadow) — needs the shadow `F'`
  register set
- bank switching effects on the visible register window when RFP
  changes (currently RFP can be set via POP SR but the eight visible
  32-bit registers do not yet swap)
- `INTNEST` register
- alternate flag register (`F'`)

## 7. What is modeled in addition to the architectural state

- `PUSH SR` (0x02) / `POP SR` (0x03) opcodes — fully wired through
  `_try_execute_push_pop_sr`. PUSH SR encodes the six flags +
  `iff_level` + `rfp` via `encode_sr_from_state` and writes the
  16-bit value little-endian onto the stack. POP SR reads 2 bytes
  from the stack, runs `decode_sr_to_fields` and applies the new SR
  state atomically. Both block honestly when the SR shape or XSP is
  not yet modeled.

## 8. CLI

- `python ngpc_emu.py cpu-info <rom>` — prints the bootstrap CPU state
