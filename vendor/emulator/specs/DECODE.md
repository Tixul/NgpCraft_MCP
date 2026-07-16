# Decode v0

Purpose:
- define the first instruction decode helper built on top of the bootstrap fetch path
- keep the current scope honest while preparing later execution work

Current source references:
- `../../NgpCraft_toolchain/T900_DENSE_REF.md`
- `../../NgpCraft_toolchain/DISASM_CROSSCHECK.md`
- `../../NgpCraft_Disasm/ngpc_disasm.py`
- `FETCH.md`

Current behavior:
- decode starts from one explicit address, or from the current bootstrap `PC`
- decode is read-only and execution-neutral
- only a small bootstrap-focused TLCS-900 subset is currently implemented
- unsupported opcodes are reported as unsupported instead of guessed
- incomplete byte sequences are reported as truncated instead of decoded loosely
- known silicon-risk patterns can now surface explicit warnings without changing the decode result
- decoded control-flow metadata is now exposed when the current subset can classify it

Current result statuses:
- `decoded`
- `unknown-opcode`
- `truncated`
- read failures forwarded from the current read bus:
  - `unmapped`
  - `unbacked`
  - `out-of-file`

Current decoded metadata:
- `next sequential PC`
- `control_flow_kind` when recognized
- `direct_target` when statically known
- `falls_through` when the current decode subset can state it honestly

Currently decoded subset:
- `NOP`
- `DI`
- `EI n`
- `RETI`
- `RET`
- `LDB (n8), imm8`
- `LD R8, imm8`
- `LD R16, imm16`
- `LD R32, imm32`
- `PUSH R16`
- `PUSH R32`
- `POP R16`
- `POP R32`
- `JR cc, d8`
- `JRL cc, d16`
- first prefixed register-family subset:
  - register ALU forms such as `OR A, W`, `ADD A, L`, `ADC W, H`
  - `INC n, r`
  - `DEC n, r`
  - `LD r, imm`
  - `PUSH r`
  - `POP r`
  - `CPL r`
  - `NEG r`
  - `DAA r`
  - `EXTZ r`
  - `EXTS r`
  - `LINK`
  - `UNLK`
  - `DJNZ`
  - `LDC`
- first indexed `(r32+d8)` load/store subset:
  - `LD R8, (r32+d8)`
  - `LD R16, (r32+d8)`
  - `LD R32, (r32+d8)`
  - `LD (r32+d8), R8`
  - `LD (r32+d8), R16`
  - `LD (r32+d8), R32`
  - `CP (r32+d8), R32`
- first post-increment byte-memory subset:
  - `LD R8, (r32+)`
  - `LD (r32+), R8`
  - `LD (r32+), imm8`
- first abs16 byte-memory subset:
  - `LD R8, (abs16)`
  - `CP (abs16), imm8`
- first B0 memory-family absolute-address subset:
  - `LDA R32, (abs24)`
  - `LD (abs24), R8`
  - `LD (abs24), imm8`
  - `LD (abs16), R32`
  - `LD (abs16), imm8`
  - `RES bit, (abs16)`
  - `SET bit, (abs16)`

Current warning coverage:
- broken `D0..D7` ALU word-register prefix family
- `LINK XIY, N` when `N >= 5`
- `ADC W, B` static risk annotation for the known silicon issue triggered when `W > 0`

Current quirk metadata exposure:
- decode payloads now also carry `matched_quirk` when the current instruction
  matches one known local hardware quirk
- the nested quirk object now includes the quirk database version and a
  non-empty `sources` list documenting the local attribution
- this metadata is diagnostic only and does not upgrade decode status into
  execution support

Important prefix note:
- HW-confirmed 2026-07-03: in ALU-register context, `D8..DF` are WORD (16-bit)
  register forms (ngdis getzz(0xD8)=1=word), NOT 32-bit. The genuine 32-bit
  (long) register prefix is `E8..EF` (getzz=2).
- this matters for sequences such as `DB C8 <imm16>`, which decode as
  `ADD SP, imm16` (word); the long form `EF C8 <imm32>` = `ADD XSP, imm32`
- this is now aligned with the corrected local disassembler reference
- the current `post-increment` register-code extraction is still narrow and evidence-driven around the official StarGunner bootstrap loop, not a full ARI_PI family claim

Current CLI user:
- `python ngpc_emu.py decode-next <rom>`
- `python ngpc_emu.py decode-next <rom> --address 0x200043`

Examples:
- decode the first bootstrap instruction at the header entry point:
  - `python ngpc_emu.py decode-next game.ngc`
- decode one later instruction at an explicit ROM address:
  - `python ngpc_emu.py decode-next game.ngc --address 0x200043`

Not implemented yet:
- prefix-family decode beyond the minimal subset above
- richer memory forms beyond the first `(r32+d8)` subset
- operand side effects
- cycle timing
- CPU state updates
- stepping or execution
