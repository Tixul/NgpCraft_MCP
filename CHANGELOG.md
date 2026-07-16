# Changelog — @ngpcraft/mcp

## 2026-07-16 — corpus: OAM despawn discipline + "static plane vs raster split" for HUDs

Documents two footguns that surfaced from a real user's shmup build.

- **OAM slot leak on despawn.** A pool where `kill` only clears a logic flag
  (`active = 0`) never frees the entity's OAM slot, so invisible-to-logic "zombie"
  sprites accumulate and exhaust the 64-slot budget — new spawns silently fail while
  far fewer than 64 sprites are visible. Added the failure mode, how to tell it apart
  from the per-scanline limit (`ngpc_emu_oam_info` slot-vs-live gap), and the two safe
  disciplines (owned-slot + hide-on-kill, or clear-then-redraw) to
  `corpus/wiki/03_Graphics/Sprites-and-OAM.md` (§3.2, §3.3, §10) and
  `corpus/wiki/06_Pipeline-and-Patterns/Gameplay-Patterns.md` (new §6.6).
- **HUD "pop" while scrolling.** Added a decision block at the head of the HUD raster
  pattern (`Effects-and-Raster.md` §6.6): before reaching for a raster split, put the
  HUD on the plane you *don't* scroll (SCR2 static, SCR1 scrolls). Zero interrupts,
  cannot stutter. The split is only needed when *both* planes must scroll. Also warns
  against the per-tile "bake N vertical variants and swap" workaround, which wastes
  tile RAM and reintroduces the pop.
- **Timer0 HBlank IRQ activation recipe.** `Effects-and-Raster.md` new §6.5b shows the
  full `SWI 1 BIOS_INTLVSET` sequence to enable a Timer0/HBlank IRQ. Previously the doc
  only *referenced* it ("`ngpc_raster_init()` does the SWI"); anyone hand-rolling their
  own `__interrupt` handler had no copy-pasteable recipe and hit the #1 silent-failure
  gotcha (writing INTET does nothing; the ISR never fires — invisible on NeoPop).

## 2026-07-06 — quirks_db re-synced (D8..DF mul/div `r+r` HW-cleared)

`vendor/emulator/core/quirks_db.json` re-synced to the emulator source of truth.
A flashed HW test (`hw_test_muldiv`) on real NGPC proved the `D8..DF` word
`mul/muls/div/divs` r+r pocket (`0x40..0x5F`) EXECUTES cleanly and correctly —
`div WA,BC` (`D9 50`): XWA=0x000003E8 / BC=0x000A → XWA=0x00000064 (quotient 100,
remainder 0). It is **not** silicon-broken. `cpu.d8_df_register_to_register` now
safe-lists `0x40..0x5F`, mirroring the `add r+r` clearing (2026-07-05, v10) and
the `ld` copies (v7). In the `D8..DF` WORD prefix, only **shift-by-A**
(`0xF8..0xFF`, separate quirk) and the `0xB8..0xBF` gap remain silicon-broken.
Doc reference corrected in `corpus/DENSE_INDEX.md` (the earlier line below listing
mul/div `0x40..0x5F` as "stay broken" reflects the pre-HW-test v10 belief and is
superseded).

## 2026-07-05 — quirks_db re-synced to v10 (D8..DF `add r+r` HW-cleared)

`vendor/emulator/core/quirks_db.json` re-synced to `2026-07-05.v10`. A flashed
HW test (`hw_test_addrr`) on real NGPC proved the `D8..DF` word arithmetic/logic
r+r family (add/adc/sub/sbc/and/xor/or) EXECUTES cleanly — it is **not**
silicon-broken. `cpu.d8_df_register_to_register` now safe-lists those sub-ops
(only mul/div `0x40..0x5F` and shift-by-A `0xF8..0xFF` stay broken); the `ld`
r+r copies were already cleared (v7). The USER_MANUAL §12.1 blanket
"sub-op 0x80..0xFF hangs" rule is disproven. Doc references corrected across the
corpus (`corpus/DENSE_INDEX.md`, `corpus/wiki/.../Build-Toolchain.md`, `README.md`).

Historical note: earlier entries below that call `D8 8B` / D8..DF r+r
"silicon-broken" reflect the pre-HW-test belief and are superseded — `D8..DF` is
the 16-bit WORD prefix (the real 32-bit form is `E8..EF`), and its `ld`/arith/logic
r+r forms execute on hardware.

## 2026-05-20 (later) — v0.8 inspector + visual + debugger surface

### `vendor/emulator/` re-synced (UI 0.6 + BIOS hand-off + opcode-coverage CLI)

Re-syncs `ngpc_emu.py` + `core/` + `specs/` from upstream
`NgpCraft_emulator` (passes 34..50, **651 → 704 tests upstream**). Adds CLI
surface: `opcode-coverage`, `registers`, `memory-dump`, `palette-info`,
`oam-info`, `tilemap-info`, `tile-view`, `tiles-view`, `screenshot`,
`tick-frame`, `frame ...`, `watchpoint ...`, `breakpoint ...`, `ui`.

### Added (MCP tools)

- `ngpc_emu_opcode_coverage` — linear-walk a ROM, report unhandled opcodes
  to prioritize executor work. Backed by `opcode-coverage`.
- `ngpc_emu_registers` — rich 8 R32 + R16/R8 + PC/SR/IFF/RFP/flags view.
- `ngpc_emu_memory_dump` — hexdump-style multi-row inspector with optional
  savestate overlay.
- `ngpc_emu_palette_info` — decode the K2GE palette RAM (5 planes).
- `ngpc_emu_oam_info` — decode the 64-sprite OAM + CP.C palette strip.
- `ngpc_emu_tilemap_info` — decode SCR1/SCR2 tilemaps (grid or list view).
- `ngpc_emu_tile_view` — render one 8×8 CHAR_RAM tile as ASCII (first
  visual lens).
- `ngpc_emu_tiles_view` — render a tile atlas as PPM, returned as PNG
  base64 (via the new `_ppm_to_png.js` helper).
- `ngpc_emu_screenshot` — real K2GE color-mode compose (160×152), PNG
  base64. Distinct from the existing `ngpc_screenshot` (transpiler-based).
- `ngpc_emu_tick_frame` — advance K2GE frame/scanline state (M3 Phase 0+),
  emit savestate at the new timing position.
- `ngpc_emu_watchpoint` — omnibus wrapper over the watchpoint sub-actions
  (add / list / remove / clear / check) — write/read/access kinds + byte-
  value filter.
- `ngpc_emu_breakpoint` — omnibus wrapper over breakpoint sub-actions
  (add, add-symbol via t900ld .map, list, remove, clear, check).
- `ngpc_compile_homemade` — counterpart to `ngpc_compile_official`. Drives
  the HOMEMADE Python pipeline at `NgpCraft_toolchain/tools/`:
  `t900cc` (C→ASM) → `t900as` (→ .t9obj) → `t900ld` (→ flat .bin, with
  optional `.map`) → `ngpc_romtool` (→ .ngc/.ngp). No .exe dependency,
  no Makefile required.

## 2026-05-20 — v0.7 symbol-aware diagnostic stack + StarGunner reaches main loop

### `vendor/emulator/` re-synced after a huge upstream session

- Upstream test count: 174 → **238 (+64 tests)**.
- Honest-execution depth on StarGunner: previous frontier `_ngpc_mul32 + 0xB5`
  (silicon-broken `D8 8B = ld XHL, XWA`, 25 072 instr) now bypassed by:
  - **A toolchain fix** in `t900cc.py` (helper `_emit_copy_xwa_to_xhl`) —
    discovered by the emulator itself, 114 emissions of the broken opcode
    eliminated in one commit. **First concrete payoff of the toolchain ↔
    emulator feedback loop.**
  - **Extended opcode coverage** : ADC/SBC r8r8 (carry-aware), `link Rxx, d16`
    / `unlk Rxx`, F1 `ldw (abs16), R16`, 0x9F indexed `mul/div` (HW-validated
    catalog), `inc/dec N, (abs16)` byte + full Z/S/V/H flag modeling,
    D1 `cpw (abs16), imm16` + `ld R16, (abs16)`, `lda Rdst, (Rbase)`.
- New CLI/feature surface exposed:
  - **Symbol map loader** (`map info / lookup-name / lookup-addr`) — first
    debugger-grade symbol resolution layer.
  - **`--map <file>` flag** wired into 5 execution commands (`run-until-exec`,
    `step-exec`, `run-steps`, `trace-exec`, `eventlog capture`). When set,
    output includes a `final_symbol` block.
  - **`eventlog profile`** subcommand: per-symbol bucketing of a captured
    event log — first dynamic-profile primitive.
  - **`--seed-zero-bank0`** shortcut on every seed-aware command (XWA/XBC/XDE/
    XHL/XIX/XIY = 0, software-convention crt0 default).
  - **`--auto-tick-addr` / `--auto-tick-period`** on `run-until-exec`:
    simulate a vblank counter ISR so code spinning on it (e.g. `_ngpc_vsync`)
    exits without IRQ modeling. Non-reference mode, explicitly opt-in.
  - **Engine-bridge auto-enrichment**: when a request carries `build.map_path`,
    responses include `final_symbol` and a top-5 `event_log_profile_excerpt`
    automatically.
  - **BIOS RAM 0x6F80..0x6FFF** now backed read-as-zero by default (consistent
    with real-silicon power-on state) — unblocks `_ngpc_vsync` post-loop reads.
  - **Quirk matcher fix**: `prefixed_range_non_immediate` no longer
    misclassifies abs16 mem-form opcodes (`D1 lo hi op` etc.) as r+r broken
    just because they share a prefix byte. Filters on `len(raw) == 2`.

### Added (MCP tools)

- **`ngpc_emu_run_until`** — bridge to `run-until-exec`. Long-bootstrap
  oriented (only keeps final state + last record). Supports `target_pc`,
  `max_steps`, `address`, all seed flags, `map`, `auto_tick_addr`, and
  `auto_tick_period`. Returns `stop_reason`, `executed_count`, `final_cpu`,
  optional `final_symbol`. **The right tool when you want to advance far
  without retaining a per-step trace.**
- **`ngpc_emu_map_lookup`** — bridge to `map` subcommand. Three modes:
  `info` (section totals), `name` (symbol → addr), `addr` (PC → owning
  symbol via nearest-symbol-with-addr-<=-PC reverse lookup). The simplest
  tool to name a PC or check whether a build defines a given symbol.
- **`ngpc_emu_eventlog_profile`** — bridge to `eventlog profile`. Buckets
  a captured event-log JSON file by owning symbol (requires a `.map`),
  returning per-symbol counters and a halted-status breakdown. **The
  diagnostic primitive for "which function consumed the cycles?"**

### Changed (MCP tools — existing)

- `ngpc_emu_step_trace` and `ngpc_emu_trace_exec` now accept optional
  `seed_zero_bank0: boolean` and `map: string` arguments. When `map` is
  set, the returned JSON includes a `final_symbol` block resolving the
  final PC. **Strictly additive — pre-2026-05-20 callers see no change.**
- `_emu_bridge.js::buildSeedArgs` extended to forward `seed_zero_bank0`
  and `map` to the underlying Python CLI.

### Validated

- `python -m unittest discover -s tests -q` inside the synced
  `vendor/emulator/` → **238 / 238 passing**.
- End-to-end smoke from the MCP test harness: `ngpc_emu_map_lookup` with
  `mode='addr'` and `query='0x0020D180'` resolves to
  `_ngpc_mul32 + 100` (the historical silicon-broken frontier address).
- 25 tools registered in total (was 22 before this version).

### Strategic significance

This release is the first time the MCP stack exposes a **true symbol-aware
diagnostic loop** for callers (LLMs and humans alike). The combination of:
loader + 5-command `--map` wiring + dynamic profile + automatic bridge
enrichment lets a caller answer "which function is responsible for this
PC / event log / stop frontier?" in one tool call. It is what unlocked the
detection of the `ld XHL, XWA` toolchain bug — a class of issue that
non-hardware-aware NGPC emulators cannot surface by construction.

## 2026-04-20 — v0.5.1 emulator: SCC cc, r family

### Changed

- **`vendor/emulator/` re-synced** after a productive upstream session on
  the NgpCraft emulator: decoder + executor extended to handle the
  `0x70..0x7F` **SCC cc, r** (Set on Condition) instruction family across
  all prefix sizes (byte / word / long). Previous StarGunner honest
  frontier was `0x0020E27F  DB 7E` (= `scc NZ, XHL`) — now executes.
- **+5 honest steps** on the StarGunner smoke baseline (27 551 → 27 556).
  New frontier: `0x0020CD4D  D7 FA` (= silicon-broken shift-by-A using the
  `D0..D7` ALU prefix). Whether to execute-as-hang or stop-with-broken-
  reason is a hardware-compat-policy decision deferred to the next session.
- **6 new unit tests** (decode + execute of SCC true / false / unknown-flags
  / always-true paths). Upstream test count: 138 → 144, all passing.
- No MCP-side code changes — all existing `ngpc_emu_*` tools benefit
  automatically. Verified end-to-end: `ngpc_emu_decode` on `DB 7E` returns
  `scc NZ, XHL`; `ngpc_emu_trace_exec` correctly blocks with
  `stopped-on-requires-known-flags` when flags aren't seeded.

## 2026-04-20 — v0.5 emulator sync (quick win)

After the upstream NgpCraft emulator's 2026-04-20 session (cart flash
erased-read fallback + new `trace-exec` command + 174 new executed steps,
27 551 total), synced the vendor snapshot and exposed the new capability.

### Added

- **`ngpc_emu_trace_exec(rom_path, address?, count?, seed_xsp?, seed_regs?)`**
  — wraps the upstream `trace-exec` command. Returns a per-record execution
  trace: every instruction carries its decode payload + CPU before/after +
  flag changes + written registers + memory writes + execution status.
  Richer than `ngpc_emu_step_trace` (which wraps `run-steps` and gives a
  condensed final state). Use when you need forensic depth (golden traces,
  diff between two ROMs, deep debug).

### Changed

- **`vendor/emulator/` re-synced** to the 2026-04-20 emulator state. Side
  effects visible from existing MCP tools:
  - `ngpc_emu_peek` now returns `0xFF` bytes on the erased-cart region
    beyond the ROM file (e.g. the default save block at `0x3FBE00` on a
    2 MB-padded cart) instead of surfacing the address as unbacked. Save-
    magic probes (`cp (XWA), 0xCA`) now execute honestly.
  - Cart flash window extended from `0x3EFFFF` to `0x3FFFFF` — covers the
    full 2 MB flash layout used by `StarGunner_save_lib_test`.
  - `+174` honest executed steps on the StarGunner smoke baseline (27 551
    total). The new honest frontier is `0x0020E27F  DB 7E` → unknown-opcode
    (next decoder extension).
- `vendor/emulator/specs/` now includes the new `TRACE_EXEC.md`, plus
  updates to `ADDRESS_SPACE.md`, `MEMORY_READ.md`, `TRACE.md`.

### Tool count

- v0.4: 21 tools.
- v0.5: **22 tools** (adds `ngpc_emu_trace_exec`).

### Notes

- Unit test suite at the upstream emulator: 138 tests, all passing.
- `ngpc_emu_step_trace` kept alongside `ngpc_emu_trace_exec` — they share
  the same JSON shape but different semantic intents (condensed state vs
  per-step forensic trace). No deprecation.

## 2026-04-20 — v0.4 official toolchain bridge

### Added

- **`ngpc_compile_official(project_dir, target?, thome?, system_lib?, …)`** —
  builds a project using the user's LOCAL Toshiba toolchain (cc900 + asm900 +
  tulink + tuconv + s242ngp) by invoking `make` in the project directory.
  Returns the produced ROM path + size + full build log. Optional
  `include_rom_base64=true` to ship the ROM bytes back inline.
  - Defaults to `THOME = C:\t900` (standard Toshiba install path), overridable
    via the `thome` argument or the `THOME` env var.
  - Auto-locates the ROM in `bin/<rom_name_hint>.{ngc,ngp}` with `main` as
    fallback hint.
  - Maintenance targets (`clean`, `move_files`) skip the ROM-existence check.
  - Pre-flight checks: project dir exists, has a Makefile, `cc900.exe` present
    at `THOME\BIN`. Friendly error messages if any of these are missing.
  - **The `.exe` files stay on the user's PC** — the MCP only invokes them by
    path. They are never bundled in the npm package, never copied, never
    redistributed. Same legal model as a user's own Makefile invoking them.

### Validated

- StarGunner clean+rebuild: 8.2s, ROM 77.9 KB.
- top_down_cave (Engine project) incremental build: 1.6s, ROM 132.3 KB.

### Why this matters

- **Production-quality ROMs available now.** Until `t900cc.py` lands its
  XIZ-ABI refactor (PERF-LAG-5), the open-source toolchain produces laggy
  code. With this wrapper, the AI can build playable, hardware-grade ROMs via
  CC900 directly while the open-source path matures.
- **CC900 as oracle.** Future `ngpc_diff_codegen` becomes trivial: compile
  the same C with both toolchains, diff the asm intermediates. Concrete metric
  for "how close to CC900 is `t900cc.py` getting?"

## 2026-04-20 — v0.3 tools

Two higher-leverage tools that consume the existing `Interp.runFrames` +
`compile` infrastructure without new browser dependencies.

### Added

- **`ngpc_visual_diff(code_a, code_b, frame, include_pngs)`** — renders both
  snippets at the same frame, returns the changed-pixel count + a third PNG
  where unchanged areas are dimmed to ~30 % grey and changed pixels are
  bright magenta. Lint failures on either side are surfaced with `side: "a"`
  or `side: "b"`. Use to verify a refactor is visually equivalent, or to
  prove a visible change in a single call.
- **`ngpc_validate_project(project_dir, include_template_headers, max_files)`**
  — walks the tree for `.c` files, runs `Interp.compile()` on each with an
  `includeResolver` that searches the project's own `src/` plus the bundled
  template headers (`vendor/templates/base/src/`). Returns aggregated counts
  (per file, per rule) and the full per-file error list. Found 4 real HW-2
  violations on first run against `vendor/examples/stargunner/src/` (in the
  abandoned SPRMUX module) and 2 real HW-2 violations in
  `vendor/templates/base/src/fx/ngpc_raster.c`.

### Tool count

- v0.1: 6 working data tools (bug DB, ASM patterns, doc/API/example search,
  scaffolding) + 7 stubs.
- v0.2: 17 working tools (transpiler-backed lint/quickrun/screenshot, asset
  converters, emulator bridges, disasm).
- v0.3: **20 working tools** (this release adds psg_trace, visual_diff,
  validate_project).

### Notes

- Both new tools reuse `runFrames` for execution and `pngjs` for PNG output —
  no new dependencies, no new browser shims.
- `validate_project` uses a recursive-by-basename header search so projects
  that `#include "ngpc_gfx.h"` resolve correctly even though the file lives
  in `template/src/gfx/ngpc_gfx.h`. Cached per call.
