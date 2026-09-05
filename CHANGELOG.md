# Changelog — @ngpcraft/mcp


## Unreleased

- **Corpus: announcing a token is not deciding a role** (Link-Cable §8.2). The page said
  "each console announces how long it has been searching; the longest search wins" and
  stopped there — which is the right rule and still elects **two hosts**, because a HELLO
  is a *snapshot*. A console weighing its own **live** counter against the peer's snapshot
  compares its present to the other's past: with A starting at 0, B at Δ and d frames of
  announcement lag, A concludes `t > t − Δ − d` (always true) while B concludes
  `t − Δ > t − d` (true as soon as `d > Δ`). Both claim player one, both are right from
  where they stand. Reported from the field by a homebrew author, then reproduced off the
  console: **300 disagreements in 624 runs**, forming an exact `lag > skew` triangle that
  saturates at the announcement interval. Two rules remove it — freeze the search counter
  at first contact, and echo the last token heard so the decision only fires on a pair
  both consoles provably hold. Same sweep after: **0 in 624**. The price is documented
  too: a round trip instead of a one-way announcement, worst-case agreement 25 → 51 frames,
  and a freeze that lands on different frames can still elect the console that opened the
  screen second. Mirrors `NgpCraft_dev_ref/docs/05_Systems/Link-Cable.md`.
- **Corpus: external contribution from [Napsterix](https://github.com/Napsterix)**, from
  measurements taken while building a full NGPC game. New page
  `wiki/05_Systems/Measuring-Performance.md` — the measurement *method*, which the corpus
  had nowhere (`Debug-Tools.md` only documented the profiler API): wait-states before any
  number, naming the scene, A/B/A, the six ways an emulator probe lies, techniques with
  measured outcomes including the ones that backfired, and the hardware traps that produce
  a working emulator build and a broken cartridge. Also folded in: `-w3` catches missing
  prototypes that make cc900 compare the full `HL` (Build-Toolchain §8.0), `cc900 -S`
  (§8.0b), music-vs-effects arbitration and the T6W28's ~94 Hz floor (Audio §6.4–6.6),
  chain slots cannot be skipped so overlays go after the base run (Sprites-and-OAM §2.4),
  bulk VRAM writes and ISR-side table rebuilds (Game-Loop §4.3–4.4), and RAM not being
  zero at power-on (§11.1).
- **Corpus: flash save geometry is per-cartridge-size** (Storage-and-Saves §5.0). The page
  documented only the 16 Mbit case; 4 / 8 / 16 Mbit are blocks `0x09` / `0x11` / `0x21` at
  `0x07A000` / `0x0FA000` / `0x1FA000`, and *the same block number means a different address
  on each size* — get it wrong and you erase your own ROM rather than "fail to save". The
  BIOS publishes the answer at **`0x6C58`** (`0`/`1`/`2`/`3`); read it, never hardcode.
  Includes the 32 Mbit Flash Masta report, deliberately kept marked **OPEN** because its
  interaction with `0x6C58` is not verified on device. Contributed by Napsterix,
  corroborated against the SDK block map. Fixes a wrong comment in `BIOS.md` that named
  block `0x1F` for offset `0x1FA000`.
- **Corpus: instruction cost table** (TLCS900-Reference §37) — the corpus had none.
  Transcribed from the Toshiba TLCS-900/L1 manual Appendix B, which applies to the NGPC's
  900/H (the manual's own core-differences table puts 900/H and 900/L1 in the same column):
  `MUL` 11.14 / `MULS` 9.12 / `DIV` 15.23 / `DIVS` 18.26 register-register, `LDIR` `7n+1`,
  `MINC`/`MDEC` word-only at 5 / 4 states, shifts at `3 + n/4`, plus the addressing-mode
  surcharge. ⚠️ Flagged as a **floor**, with the silicon calibration printed next to it:
  every instruction byte costs ~3 more cycles on fetch from the cartridge, `MUL`/`DIV`
  measure slower than a pure fetch-wait predicts, and `LDIR` lands nearer 14 cycles/byte
  than the datasheet's `7n+1`. Also carries an explicit **variant warning** — Toshiba
  shipped five TLCS-900 cores and figures headed plain "TLCS-900" are not this CPU.
  Contributed by Napsterix.
- **Emulator re-vendored** (upstream `23c5507`) and the native core rebuilt from the
  vendored sources. Not cosmetic: the old snapshot only understood **v1 save states**
  (`NGPCST01`), while the app now writes **v2** (`NGPCST02` — carrying the sound CPU, the
  T6W28 and the timers). Any recent `.s0` a user handed over was rejected outright, which
  is the *headline* workflow of `ngpc_emu_native_run`. Both formats load now.
- `ngpc_emu_native_run` is **timed like hardware** by default. The raw core defaults to
  free cartridge fetch for backward compatibility (~2.9× too fast); the bridge now applies
  the silicon-calibrated set — `cart_wait = 3`, `cart_data_wait = 0`, `ldir_cost = 14` —
  and names the model it used in a new `timing` field. New `timing: "free"` opts back out,
  for reproducing a pre-wait-state measurement and nothing else. ⚠️ **Cycle figures from
  this tool change with this release, and the new ones are the correct ones.** The
  practical consequence of the old default: with instruction fetch unbilled, any
  optimisation whose gain was *fewer instruction bytes* measured as exactly zero.
- `ngpc_emu_native_run` now returns **`hw_safety`** on every run — a starved watchdog (the
  BIOS hands the console over with it armed, so a cart that never writes `0x4E` to `0x006F`
  makes a real console reset itself) and a stack that crossed into the BIOS's own page
  `0x6C01..0x6FFF`. Counted, never fatal, exactly as on hardware. New `hw_guard: true`
  stops the run at the first one, for a gate that wants a verdict or the exact PC.
- `ngpc_emu_native_run` accepts **archives**: `Pack.zip` — or `Pack.zip/Game.ngc` to name
  one title inside a multi-game archive — and `.7z`. A bare `.ngc` is unaffected.
- `scripts/smoke_emu_tools.mjs` covers **21 cases instead of 17**: the breakpoint and
  watchpoint registries were never exercised, and the two new `native_run` flags are run
  both ways. Each case may now assert a **contract** rather than only that an answer
  arrived — a tool replying without the field that matters used to count as a pass.
- Docs (`AGENT_GUIDE.md`, `README.md`): which timing model produced a number and when it
  may be quoted, and how to report a hardware-safety finding without overclaiming —
  `clean` covers two specific faults, it is not a verdict on the build.

- Docs: the link-cable page now **derives** `BR0CR = 0x05 -> 19 200 bps` instead of
  quoting it. `BR0CK = 00` picks phi-T0 = fc/4, `BR0S = 5` divides by 5, UART adds a /16,
  and `fc = 6.144 MHz` is cross-checked from the video timing (515 x 199 x 60), giving
  19 200 bps and **3200 CPU cycles per byte** exactly. The "not re-derived" entry in the
  known-gaps list is closed; what remains open is that nobody has timed a byte on real
  silicon.
- Docs: three facts added for emulator authors -- **SC0BUF is two registers on one
  address** (a read must always return the RX buffer; falling through to the I/O page
  hands back the byte the game just sent and silently corrupts the BIOS ring), **CTS
  gates the start of a byte and never one already shifting** (datasheet 3.11 and Note 1
  of fig 3.11(16)), and **CTSE/CTS0 exist on serial channel 0 only**.
- Docs: the serial block (`0x50`-`0x53`, `0xB1` bit2, `0xB2` bit0) is now listed under
  the low I/O page in the register map, pointing at the link-cable page.
- ⚠️ Docs correction: **there is no platform-wide exchange cadence.** The earlier note
  that "the wire round trip is two frames" holds for Samurai Shodown! 2 and The Last
  Blade at idle, but Fatal Fury drives the cable on *every* frame. It is a property of a
  given game's link library in a given state, not of the console -- do not design against
  it as a constant.
- Docs: measured on ten commercial link cartridges, **the BIOS programs the serial
  registers identically for all of them** (`SC0MOD = 0x69`, `BR0CR = 0x05`,
  `SC0CR = 0x00`) and every write comes from BIOS code, never from cartridge space. There
  is no per-game serial configuration.
- `ngpc_bug_check`: four measured traps added to the corpus -- `RGB()` built from `u8`
  components loses the blue nibble on cc900; palette RAM is 16-bit only and entry 0 must
  be left alone; sprite priority 0 means hidden; `COMINIT` installs the BIOS serial
  handlers and they must not be reinstalled afterwards.
- Docs: the link-cable page gains a session-layer section -- the reusable `ngpc_link`
  module, the input-lockstep recipe (the wire round trip is two frames, so two steps of
  input delay are what keeps 60 Hz), and host election by search time rather than by
  asking both players to press the same button.
- Docs: the palette quick-reference addresses were wrong (`0x8100/0x8200/0x8300`);
  corrected to sprites `0x8200`, SCR1 `0x8280`, SCR2 `0x8300`.

## 2026-07-25 — emulator re-vendored, broken homemade-link fixed, PRNG fix picked up

### `vendor/emulator/` re-synced to upstream `6380728`
Both cores, the specs and the two CLI entry points now match the emulator repo byte for
byte. The C++ core was **rebuilt from the vendored sources** (MinGW, Release) rather than
copied, so `cpp/build/ngpc_core.dll` provably matches `cpp/src/`.

What the snapshot gains — every item silicon-measured upstream, not guessed:
- the second ROM chip at `0x800000` (4 MiB carts: SvC MotM, Metal Slug 2nd Mission,
  Densha de Go! 2 read their data as zero without it);
- K1GE compatibility mode + the BIOS grey ramp (mono cartridges draw instead of black);
- the OOWC window palette at `0x83F0`, the unconditional backdrop;
- the H-int raster anchor (split-line jitter), `199` scanlines/frame, timer tap `128`;
- silicon-calibrated instruction costs (cart fetch wait-states, MUL/DIV, LDIR);
- the secondary-byte rcode decode, `ldir` register masking, RTC, micro-DMA INTTC.

New vendored modules the entry points now need: `core/rom_loader.py` (zip/7z ROMs),
`expr.py`, `link*.py`, `pointers.py`, `romcheck.py`, `romdiff.py`, `texttable.py`.
`core/lobby.py` is deliberately **not** vendored — it is the one core module that imports
PyQt6, and nothing in the CLI import closure touches it.

### Fixed
- **`ngpc_compile_homemade` could not link.** The bundled default linker script
  `vendor/toolchain/tools/ngpc.lcf` was still in the old Toshiba/tulink syntax while the
  vendored `t900ld.py` had moved on, so every build died with
  `section 'f_code' not placed (no matching LCF rule)`. Re-synced from the toolchain; it
  also brings the `SYSPATCH` / `VRAMQ_ASM` / `dma_prog` / `FLASH` rules whose absence used
  to leave `.asm` symbols silently resolved to 0.
- **`vendor/templates/base` shipped the broken PRNG.** `ngpc_random()` used a u32 LCG whose
  modulo never reduced (cc900's u32 runtime helpers are buggy on hardware — confirmed on
  silicon: 98 % crit instead of 2/7), so every project scaffolded by `ngpc_new_project`
  inherited it. Synced `core/ngpc_math.{c,h}` (u16 LCG, full period) and
  `fx/ngpc_raster.{c,h}` (the HBlank write budget is ~30 cycles, **not** the 515-cycle
  scanline period). `ngpc_api_lookup` now reports `u16 ngpc_random(u16 max)`.

### Added
- `scripts/sync_vendor_emulator.sh` — reproducible re-vendoring of the emulator snapshot.
- `scripts/smoke_emu_tools.mjs` — calls every emulator-backed tool against a real ROM.
  A re-vendor changes the CLI the bridges spawn, and a renamed flag still *looks* fine
  until an agent calls the tool. 17/17 green after this sync.

### Deliberately NOT synced
- **`vendor/disasm/` is AHEAD of `NgpCraft_Disasm`, not behind.** The vendored copy carries
  `_rcode_r32_name()` (the secondary addressing byte is `rrrrrrmm` — a 6-bit *extended
  register code*, not a bare 3-bit index; reading it as `(b >> 2) & 7` is right only by
  accident for current-bank codes). Upstream **never** received that fix and still does the
  accidental read. Re-vendoring from it would reintroduce the Densha de Go! 2 mis-decode.
  The fix needs to travel upstream, not the other way round.
- `vendor/templates/base/src/core/ngpc_flash.*` — vendor and upstream disagree in
  **comments only** (standalone AMD stub vs `CLR_FLASH_RAM`/system.lib) over identical code.
  Direction unclear, so left alone.


## 2026-07-18 — emulator re-vendored (both cores), player save states, agent guide

### Added
- `ngpc_emu_native_run` — **runs** the game on the native C++ core: load a player save
  state (`.s0` from NgpCraft Emulator, F2), hold buttons, advance frames, return CPU
  registers + a screenshot drawn line by line as the beam passes. Backed by the new
  `vendor/emulator/ngpc_native.py`. Every other `ngpc_emu_*` tool inspects static state;
  this is the only one that executes the machine.
- `AGENT_GUIDE.md`, served as the MCP resource `ngpc://doc/agent_guide` (listed first):
  which tool answers which question, the save-state workflow for reproducing a user's
  bug, and the BIOS requirement.

### Changed
- `vendor/emulator/` re-vendored from the current emulator: Python core refreshed
  (was 2+ months stale), `core/quirks_db.json` added — it was **missing**, which made
  `ngpc_emu_opcode_coverage` fail outright — plus the C++ core sources and a prebuilt
  `cpp/build/ngpc_core.dll`.
- Save states written by the emulator (`NGPCST01`) are now readable by
  `core/savestate.py`, so **all** `--seed-from` consumers accept a `.s0` a user hands
  over. No tool change was needed: they all funnel through one loader.
- `vendor/transpiler/` re-synced from NgpCraft_live_editor: picks up the 2026-07-05
  removal of a **false lint rule** that flagged `ld <XR>, XWA` as silicon-broken when it
  is not (hardware-confirmed). `ngpc_lint` no longer condemns correct code.
- `vendor/toolchain/` re-synced from NgpCraft_toolchain: constant folding for shifts and
  the native 32-bit reg-reg ALU path.
- `ngpc_compile_homemade` now states plainly that the homemade toolchain is
  **experimental and unstable** — a teaching pipeline, not a compiler to ship with.
- README: removed the claim that no full-game emulation tool is exposed. It is now.

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
