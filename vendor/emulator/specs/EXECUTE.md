# Execute v0

Purpose:
- define the first real execution-shaped output above the current decode helper
- mutate CPU state honestly for a narrow subset before a full interpreter exists

Current source references:
- `CPU_STATE.md`
- `DECODE.md`
- `STEP.md`
- `../../NgpCraft_toolchain/T900_DENSE_REF.md`

Current behavior:
- execute-next starts from one explicit address, or from the current bootstrap `PC`
- execute-next performs one real instruction application when the current subset can defend it
- execution is still narrow and deliberately incomplete
- unsupported side effects stop explicitly instead of being guessed
- locally confirmed silicon-broken forms now stop explicitly with status `silicon-broken`
  instead of falling through to the generic unsupported bucket
- silicon-broken matching is now centralized through `core/quirks.py`
- the matched-quirk metadata exposed by execution payloads now includes the
  current quirk-database version loaded from `core/quirks_db.json` and the
  non-empty per-rule `sources` attribution list

Current supported execution subset:
- `NOP`
- direct unconditional jumps when the decoder already exposes one direct target:
  - `JP`
  - unconditional `JR`
  - unconditional `JRL`
- immediate register loads already covered by the decoder:
  - `LD R32, imm32`
  - `LD R16, imm16` only when the owning `R32` is already known
  - `LD R8, imm8` only when the owning `R32` is already known
- the same immediate-load rule currently applies to prefixed immediate register forms when the write is representable
- first prefixed register arithmetic forms on currently safe decoded families:
  - `INC n, R8`
  - `INC n, R32`
  - `DEC n, R8`
  - `DEC n, R32`
  - these currently execute only when the current register view can be read honestly from the CPU model
  - known broken D0..D7 word-register forms stop with `silicon-broken`
- first writable-stack instructions when `XSP` is known and the target range is writable in the current address map:
  - `PUSHW imm16`
  - `PUSH R16`
  - `PUSH R32`
  - `POP R16`
  - `POP R32`
  - `CALL`
  - `RET`
  - `RETD`
- first address-oriented execution slice guided by the official-toolchain disassembly:
  - `LDA R32, (abs24)` as "effective address -> destination register"
  - prefixed register-to-register `CP`
  - indexed memory compare `CP (r32+d8), R32`
  - first abs16 byte compare-immediate: `CP (abs16), imm8`
  - `LD (r32+d8), R32`
  - `LD R32, (r32+d8)`
  - first prefixed register-to-register `LD`
  - first absolute memory stores from the stable official bootstrap:
    - `LD (abs24), R8`
    - `LD (abs24), imm8`
    - `LD (abs16), R32`
    - `LD (abs16), imm8`
    - `RES bit, (abs16)`
    - `SET bit, (abs16)`
- first post-increment byte-memory slice from the stable bootstrap loops:
  - `LD R8, (r32+)`
  - `LD (r32+), R8`
  - `LD (r32+), imm8`
- compact small-immediate register load (catalog `C8+zz+r : A8+#3`):
  - `LD R32, #3` — 2-byte encoding, value 0..7 embedded in the opcode
  - `LD R16, #3` — only when the owning R32 is already known
  - `LD R8, #3` — only when the owning R32 is already known
- first flag-driven control-flow slice:
  - conditional `JR`
  - conditional `JRL`
  - only when the required modeled flags are known
- documented immediate-safe word-prefix forms remain executable even when they use
  the `D0..D7` prefix:
  - `ld r, imm`
  - `multu/muls r, imm`
  - `ld r, #3`
  - ALU-immediate `add/adc/sub/sbc/and/xor/or/cp r, imm`

Current representation rule:
- the current CPU model stores concrete general-register values only at 32-bit owner granularity
- this means:
  - full 32-bit writes are always representable
  - 16-bit and 8-bit writes are only executable when the owning 32-bit register is already known
- example:
  - `ld XSP, 0x00006000` is executable from bootstrap
  - `ld WA, 0x1234` is not executable honestly while `XWA` is still unknown

Current writable stack rule:
- the execution helper can maintain a small in-memory overlay of bytes written by the current instruction
- stack writes still use the address-space map as the authority for "can this target be written at all"
- the current subset explicitly rejects stack targets that land in:
  - unmapped space
  - cartridge ROM
  - cartridge ROM gaps
  - BIOS ROM
- the current CLI can accept manual one-shot register seeding while reset-time values remain unknown:
  - repeatable `--seed-reg XWA=...` .. `--seed-reg XSP=...`
  - `--seed-xsp` remains as a convenience alias for the most common stack-only case
- stack state is not persisted across separate CLI invocations yet

Current minimal writable-memory rule beyond pure stack ops:
- the same writable runtime overlay now also carries the first representable indexed stores near the current stable bootstrap path
- that overlay also carries the first post-increment byte-copy / zero-fill forms used by the stable official bootstrap loops
- that overlay now also carries the first absolute stores and bit-manipulation writes used by the official bootstrap and tiny init subroutine
- this is still a very small subset, not a general RAM or IO write model

Current minimal readable-system rule:
- the read bus now exposes a tiny built-in readable slice for the current stable bootstrap:
  - `0x6F86` defaults to `0x00`
  - `0x6F91` mirrors the ROM header mode byte for the current invocation
- this is intentionally narrow and should not be treated as a general RAM or IO implementation

Current explicit non-goals:
- no general memory writes beyond the current minimal writable overlay for stack, nearby indexed stores and the first post-increment byte loop forms
- no IO writes yet
- no full flags/SR mutation yet
- no full condition evaluation yet
- no persistent multi-step session state from the CLI yet

Current result fields:
- decoded instruction payload
- matched quirk metadata when the current instruction hits one known local quirk
- before-CPU snapshot
- after-CPU snapshot when execution succeeded
- explicit execution status
- list of architectural registers/views written by the instruction
- explicit flag changes when the current subset updates modeled flags
- list of memory-write chunks emitted by the instruction
- current after-memory overlay when the command has one
- per-register before/after changes in CLI/JSON output

Current CLI user:
- `python ngpc_emu.py execute-next <rom>`
- `python ngpc_emu.py execute-next <rom> --address 0x200043`
- `python ngpc_emu.py execute-next <rom> --address 0x20009B`
- `python ngpc_emu.py execute-next <rom> --address 0x2079C6 --seed-xsp 0x4100`
- `python ngpc_emu.py execute-next <rom> --address 0x20D06C --seed-xsp 0x40F4 --seed-reg XIZ=0x12345678`
- `python ngpc_emu.py execute-next <rom> --address 0x20D06D`
- `python ngpc_emu.py execute-next <rom> --address 0x20D098 --seed-reg XIZ=0x00005EBC --seed-reg XBC=0xAABBCC42`

Important:
- this is the first real state-mutation helper, not a full interpreter
- a decoded instruction can still be non-executable if its side effects are not modeled honestly
- writable runtime memory is now partially modeled, but only for a narrow subset and only within the current command
- interrupts, halts, most flags/SR updates, general memory/IO writes and most branch-condition evaluation remain outside the current subset
- the command does not keep state across invocations; each run starts from the current bootstrap model plus an optional explicit `PC`

Not implemented yet:
- full fetch/decode/execute loop
- full stack and call semantics, including the remaining alias cases around `SP` / `XSP`
- full flags and condition evaluation
- general memory and IO writes
- multi-step run control
- true debugger-grade stepping
