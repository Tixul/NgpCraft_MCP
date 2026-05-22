# Reset State v1 (BIOS hand-off applied)

Purpose:
- define the current minimal bootstrap machine state
- distinguish "raw HW reset" (machine state at cold start) from
  "BIOS hand-off state" (what user code actually sees when it
  begins executing)

Current source references:
- `../../01_SDK/docs/ngpcspec.txt`
- `../../01_SDK/docs/NGPC_HW_QUICKREF.md`
- `../../01_SDK/docs/NGPC_SDK_MASTER_ENTRY.md`
- `../../01_SDK/docs/SysWork.txt`
- `../NgpCraft_toolchain/NGPC_REVERSE_REFERENCE.md`

---

## 1. Two layers — raw bootstrap vs BIOS hand-off

The real NGPC always runs the BIOS first ; user code at the cart
entry point starts with a set of register values the BIOS has set
up. Modeling user-code execution from a truly-cold CPU is therefore
NOT what games expect — the very first instruction of a typical
cart is `CALL N`, which needs a valid XSP.

The emulator now exposes BOTH layers :

- **Raw bootstrap state** (`core.machine.create_bootstrap_cpu_state`,
  `EmulatorSession(rom_path, apply_bios_handoff=False)`,
  `core.execute.build_run_steps` directly) : PC from cart header ;
  every other register field stays `None`. This is for the CLI
  and engine bridge, where strict honesty about unmodeled state
  is the doctrine.
- **BIOS hand-off state** (`EmulatorSession(rom_path)` — default
  for the UI) : the bootstrap CPU is augmented with the documented
  BIOS-equivalent register values described in §2 below. This lets
  the UI step real ROMs past their first stack-touching instruction.

The hand-off seed is opt-in at the session level (default ON for
the UI ; off for the CLI). The raw bootstrap is preserved at
`EmulatorSession.machine.cpu` for callers that want to compare.

---

## 2. BIOS hand-off values (sourced)

| Field          | Value         | Source                                            |
|----------------|---------------|---------------------------------------------------|
| `cpu.pc`       | `entry_point` | Cart header byte+0x1C..+0x1F (little-endian)     |
| `regs.xsp`     | `0x00006C00`  | `NGPC_HW_QUICKREF.md §2` — top of 12 KB user RAM `0x004000–0x006BFF` ; `0x006C00..0x006FFF` is system-reserved. Stack grows downward from here. |
| `iff_level`    | `7`           | `ngpcspec.txt §INTERRUPT STATE` : *"The software starts up with interrupts prohibited (DI)"* — IFF = 7 is the TLCS-900/H "all maskable IRQs blocked" mask. User code does `EI 0` later. |
| `iff_enabled`  | `False`       | Derived from `iff_level == 7`.                    |
| `rfp`          | `0`           | The BIOS uses bank 3 for its own state ; hand-off to user code is in bank 0 (= default user bank). |
| `flags.{sf,zf,vf,hf,cf,nf}` | `False` × 6 | Conservative cleared state ; the BIOS doesn't expose well-defined post-hand-off flag values, so `False` is the safest choice that lets value-dependent conditional branches behave deterministically. |

Other R32 registers (`xwa`, `xbc`, `xde`, `xhl`, `xix`, `xiy`, `xiz`)
remain `None`. The BIOS doesn't guarantee specific values for them
on hand-off ; user code that depends on a specific R32 register at
boot is reading garbage on real HW too.

The hand-off values are defined as class-level constants on
`EmulatorSession` (`BIOS_HANDOFF_XSP`, `BIOS_HANDOFF_IFF_LEVEL`,
`BIOS_HANDOFF_RFP`, `BIOS_HANDOFF_FLAGS`) so they are sourced from
a single location and inspectable from tests.

---

## 3. What the BIOS hand-off seed unlocks

Empirical measurement (pass 48, smoke on the local ROM corpus) :

| ROM                       | Before pass 48 | After pass 48 |
|---------------------------|----------------|---------------|
| `minimal_template/main.ngc` | 0 instr (`requires-known-stack-pointer`) | 40 instr ; new blocker at `unsupported-decoded-instruction` |
| `HORATIO.ngp`             | 0 instr (`requires-known-address-register`) | 1 instr ; new blocker at `requires-known-full-register` |
| `POCKETRACE.ngp`          | 0 instr | 1 instr ; new blocker at `requires-known-full-register` |
| `MRROBOT.ngp`             | 0 instr | 1 instr ; new blocker at `requires-known-full-register` |

The hand-off doesn't unlock every ROM (most need additional
state that the BIOS would have initialized, plus more opcodes in
the executor's subset), but it removes the single dominant
"first-instruction" blocker for `minimal_template` and any cc900-
compiled ROM that does `CALL`/`PUSH` immediately.

---

## 4. What's still not modeled (post pass 48)

- Other R32 register defaults (`XIX`, `XIY`, `XIZ` typically used
  by cc900 as global / frame / heap pointers — populated by the
  cc900 `crt0`, not by the BIOS)
- BIOS-initialized RAM contents : font tables, vector table at
  `0x006FB8..0x006FE0`, system work area at `0x006C00..0x006FFF`
- Window / video register reset values as live machine state
  (`WSI.H = WSI.V = 0xFF` is partially modeled via
  `_build_builtin_readable_bytes` ; the rest is cold-start `0x00`)
- BIOS SWI handlers — `_try_execute_swi` is currently a silent
  no-op stub (per `BIOS_HLE.md §2`). Side effects of `SWI 1`
  (flash, shutdown) etc. are not applied.
- Z80 sub-CPU / PSG audio
- TMP95C061 timers, RTC

---

## 5. Doctrine

- Raw bootstrap state stays minimal and intentionally honest. The
  CLI default (used by the engine bridge, CI, batch scripts)
  remains "every R32 is None, no fake reset values" so the
  honesty contract for non-UI consumers is preserved.
- The BIOS hand-off seed is **sourced** (not invented) :
  `0x6C00` from HW QuickRef memory map, `iff_level=7` from
  ngpcspec INTERRUPT STATE quote. Adding a new field to the seed
  requires the same — a citable doc line.
- The hand-off layer is opt-in via the `apply_bios_handoff` kwarg
  so a future "raw bootstrap" UI mode can flip it off without
  touching session callers.
