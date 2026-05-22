# NgpCraft Emulator - User Manual

Status:
- work in progress
- update this file incrementally as features land

## 1. Overview

NgpCraft Emulator aims to be:
- a standalone NGPC emulator and debugger
- an embedded runtime/debug target for `NgpCraft_engine`
- a headless regression and validation runner

The project is designed for accurate NGPC development, not just casual gameplay.

## 2. Product Goals

The emulator is expected to provide:
- faithful NGPC emulation
- useful debugging tools
- accurate slowdown and crash reproduction
- proper save handling
- direct workflow integration with NgpCraft tools

## 3. Reference Behavior

Reference mode is intended to match real hardware behavior as closely as possible.

This means:
- broken opcodes remain broken if hardware does that
- silicon bugs are reproduced when confirmed
- crashes and freezes are not silently fixed
- real hardware slowdowns are not smoothed away

Diagnostics may explain the problem, but must not alter reference execution.

## 4. Save Handling

The emulator treats these as separate features:
- save states
- persistent in-game saves

Both are required.
Persistent game saves must remain stable across sessions.

### 4.0 Event log status

- The event log v1 format is specified in `specs/EVENT_LOG.md` and
  carries a time-ordered stream of instruction-step events produced
  by one execution run.
- The format pins the ROM SHA-256 and the quirk database version,
  and records per-event `matched_quirk` payloads with their source
  attribution.
- The dedicated CLI is now available:
  - `eventlog capture`
  - `eventlog inspect`
  - `eventlog diff`
  - `eventlog check`
  - `eventlog golden-save` / `golden-load` / `golden-list` / `golden-delete` / `golden-check`
- `trace-exec` still exists as the evolving ad-hoc runtime trace,
  but the event log is the locked format intended for diff, CI,
  regression, and reproducible bug reports.

#### 4.0.1 Capture an event log

```text
python ngpc_emu.py eventlog capture <rom> <output.json>
python ngpc_emu.py eventlog capture <rom> <output.json> --count 32 --seed-reg XIZ=0 --seed-xsp 0x6C00
python ngpc_emu.py eventlog capture <rom> <output.json> --run-until <target_pc> --max-steps 1000
python ngpc_emu.py eventlog capture <rom> <output.json> --seed-from <state.json>
python ngpc_emu.py eventlog capture <rom> <output.json> --seed-checkpoint <name>
python ngpc_emu.py eventlog capture <rom> <output.json> --seed-from <state.json> --run-until <target_pc> --auto-tick-addr 0x4000 --auto-tick-period 1
```

- Without `--run-until`, capture records up to `--count` attempted
  instructions and stops with `step-budget-exhausted` if the budget
  is consumed.
- With `--run-until <target_pc>`, capture records every attempted
  instruction until the target is reached, an honest stop occurs,
  or `--max-steps` is exhausted.
- `--seed-reg`, `--seed-xsp`, `--seed-from`, and `--seed-checkpoint` follow the same
  semantics as the execution-oriented commands.
- `--auto-tick-addr` / `--auto-tick-period` expose the same
  diagnostic-only non-reference mode as `run-until-exec`: one writable
  byte counter is incremented every N executed instructions so a
  counter-wait loop (for example `_ngpc_vsync`) can be escaped without
  claiming IRQ/VBlank timing is implemented.

#### 4.0.2 Inspect an event log

```text
python ngpc_emu.py eventlog inspect <input.json>
python ngpc_emu.py eventlog inspect <input.json> --rom <rom> --limit 8
```

- With `--rom`, the loader recomputes the ROM SHA-256 and rejects the
  event log if the hash does not match.
- `--limit <N>` prints the first `N` events after the summary; JSON
  mode emits the full stored payload.

#### 4.0.3 Diff two event logs

```text
python ngpc_emu.py eventlog diff <left.json> <right.json>
```

- The diff command refuses to compare logs captured against different
  ROM hashes.
- The current diff is first-divergence oriented:
  - it reports a run-context mismatch first when the two captures were
    parameterized differently
  - otherwise it reports the first event that differs
  - otherwise it reports that the compared fields are identical

#### 4.0.4 Check one run against a golden event log

```text
python ngpc_emu.py eventlog check <rom> <golden.json> --count 8
python ngpc_emu.py eventlog check <rom> <golden.json> --seed-session <name> --run-until <target_pc> --save-current current.json
```

- `eventlog check` is the first CI-friendly golden-trace wrapper on top
  of the stable event-log format.
- It captures a fresh current run using the same capture options as
  `eventlog capture`, then immediately diffs it against `<golden.json>`.
- Exit code:
  - `0` when the compared logs are identical
  - `1` when a divergence is found
- `--save-current <path>` stores the freshly captured current event log
  before diffing, which is useful when a CI run needs an artefact to
  inspect after failure.
- The command still refuses dishonest compares indirectly, because the
  underlying diff rejects mismatched ROM hashes.

#### 4.0.5 Named golden registry

```text
python ngpc_emu.py eventlog golden-save <rom> <name> --count 8
python ngpc_emu.py eventlog golden-list <rom>
python ngpc_emu.py eventlog golden-load <rom> <name>
python ngpc_emu.py eventlog golden-check <rom> <name> --save-current current.json
python ngpc_emu.py eventlog golden-delete <rom> <name>
```

- Named goldens are stored under `.ngpc_emu/goldens/` next to the ROM.
- They are still plain event-log v1 JSON files; the registry is just a
  stable naming layer on top.
- `golden-save` captures a fresh run and stores it under a human name.
- `golden-check` is the path-free equivalent of `eventlog check`:
  it captures a fresh current run, compares it against the named
  golden, returns `0` on match and `1` on divergence, and can keep the
  current log with `--save-current`.
- This keeps regression workflows anchored on the stable event-log
  schema while removing most manual file-path threading.

#### 4.0.6 First synthetic micro-ROM corpus

- The first M1c slice now exists in the test suite as three stable
  synthetic micro-ROM scenarios validated through named golden checks:
  - `arith-add-wa`
  - `arith-sub-wa-zero`
  - `arith-and-wa-zero`
  - `arith-xor-wa-zero`
  - `arith-or-wa-sign`
  - `arith-add-wa-carry-zero`
  - `arith-add-wa-overflow-sign`
  - `arith-sub-wa-borrow-sign`
  - `arith-sub-wa-overflow`
  - `arith-adc-wa-carry-in`
  - `arith-sbc-wa-borrow-in`
  - `arith-add-wa-half-carry`
  - `arith-sub-wa-half-borrow`
  - `arith-add-w-carry-zero`
  - `arith-or-a-sign`
  - `arith-add-xwa-carry-zero`
  - `arith-sub-xwa-overflow`
  - `arith-adc-w-carry-in`
  - `arith-sbc-w-borrow-in`
  - `arith-adc-xwa-carry-in`
  - `arith-sbc-xwa-borrow-in`
  - `arith-add-w-half-carry`
  - `arith-sub-w-half-borrow`
  - `arith-add-xwa-half-carry`
  - `arith-sub-xwa-half-borrow`
  - `arith-cp-w-zero-no-writeback`
  - `arith-cp-xwa-zero-no-writeback`
  - `arith-cp-w-borrow-sign-no-writeback`
  - `arith-cp-w-overflow-no-writeback`
  - `arith-cp-xwa-borrow-sign-no-writeback`
  - `arith-cp-xwa-overflow-no-writeback`
  - `shift-rlc-w-carry-sign`
  - `shift-sra-w-carry-zero`
  - `shift-rrc-xwa-carry-sign`
  - `shift-srl-xwa-carry-zero`
  - `bitops-res-set-abs16-builtin`
  - `bitops-set-res-abs16-overlay`
  - `memory-ld-abs16-imm8-overlay`
  - `memory-ld-abs16-a-overlay`
  - `memory-ldw-abs24-imm16-overlay`
  - `memory-ld-abs16-xwa-overlay`
  - `stack-push-pop-wa-roundtrip`
  - `stack-push-pop-xiz-roundtrip`
  - `stack-link-unlk-xwa-roundtrip`
  - `stack-link-unlk-xbc-positive-frame`
  - `stack-link-xiy-large-frame-silicon-broken`
  - `stack-call-ret`
  - `control-jr-z`
- These cases currently exercise:
  - basic arithmetic execution and a first visible slice of flag behavior
  - visible `Z / S / C / V / H` transitions on stable `byte / word / long` immediate cases
  - first `ADC/SBC` carry-in / borrow-in scenarios through seeded savestates on both `word` and `byte / long` cases
  - first explicit half-carry / half-borrow checks on `byte` and `long`
  - first explicit `CP` no-writeback checks on `byte` and `long`
  - first explicit `CP` borrow / sign / overflow checks on `byte` and `long`
  - a first `shift/rotate` slice on `byte` and `long` using immediate counts
  - a first `res/set abs16` memory RMW slice using both builtin and seeded overlay bytes
  - a first `ld/ldw` memory-store slice spanning `abs16/abs24` and immediate/register sources
  - a first explicit `push/pop` stack roundtrip slice on word and long registers
  - a first explicit `link/unlk` frame-management slice
  - that `link/unlk` slice now includes an honest `LINK XIY, N>=5` silicon-broken stop case
  - stack / call / return behavior
  - conditional control flow
- They are not packaged as external ROM assets yet; for now they live as
  generated ROMs inside the regression tests so the corpus stays small,
  deterministic, and easy to evolve with the executor.

### 4.1 Savestate status

- The savestate v1 format is specified in `specs/SAVESTATE.md` and
  carries CPU state, writable memory overlay, ROM identity hash and
  quirk-database version.
- Savestates are strictly separate from cartridge persistent saves; a
  savestate loader must never overwrite a cart save file.

#### 4.1.1 Save a state

```
python ngpc_emu.py savestate save <rom> <output.json>
python ngpc_emu.py savestate save <rom> <output.json> --run-until <target_pc> [--seed-reg XIZ=0 --seed-xsp 0x6C00 --note "label"]
python ngpc_emu.py savestate save <rom> <output.json> --seed-from <state.json> --run-until <target_pc> --auto-tick-addr 0x4000 --auto-tick-period 1
```

- Without `--run-until`, the savestate reflects the bootstrap reset
  state only (PC from the ROM header, all other registers `null`).
- With `--run-until <target_pc>`, the CLI runs the current execution
  subset until the target is reached or an honest stop happens, then
  captures the final CPU state and writable memory overlay.
- `--seed-reg NAME=VALUE` and `--seed-xsp` are the same seed flags as
  `run-until-exec`.
- `--auto-tick-addr` / `--auto-tick-period` are also available when
  `--run-until` is used. This is diagnostic-only and intentionally
  non-reference.
- `--note "..."` stores a free-form operator note in the savestate.

#### 4.1.2 Load a state

```
python ngpc_emu.py savestate load <input.json>
python ngpc_emu.py savestate load <input.json> --rom <rom>
```

- Without `--rom`, the loader validates format and version but does NOT
  verify the ROM content hash; a warning is printed.
- With `--rom`, the loader computes the ROM's SHA-256 and rejects the
  savestate if it does not match the hash captured at save time. This
  is the recommended mode.
- Unknown format or unknown format_version are rejected explicitly.

#### 4.1.3 Resume a run from a savestate

```
python ngpc_emu.py run-until-exec <rom> <target_pc> --seed-from <state.json>
```

- The CLI loads `<state.json>`, verifies the ROM hash against `<rom>`,
  then runs the current execution subset from the captured CPU state
  and writable overlay until `<target_pc>` is reached, an honest stop
  occurs, or the step budget is exhausted.
- `--seed-reg` / `--seed-xsp` may still be combined with `--seed-from`
  to override specific registers on top of the loaded state; everything
  else from the savestate survives.
- A direct N-step run and a split run that saves after M<N steps and
  resumes must land at the same final state. This round-trip invariant
  is covered by `tests/test_savestate.py`.

#### 4.1.4 Persist final execution state directly from the execution commands

```
python ngpc_emu.py run-steps <rom> --count 2 --save-state step1.json
python ngpc_emu.py run-steps <rom> --count 2 --seed-from step1.json --save-state step2.json
python ngpc_emu.py run-until-exec <rom> <target_pc> --save-state frontier.json
```

- `run-steps` now accepts both:
  - `--seed-from <state.json>`
  - `--save-state <next.json>`
- `step-exec` now exists for the one-instruction version of that workflow:
  - `--seed-from <state.json>`
  - `--save-state <next.json>`
- `trace-exec` now also supports the same persistence pattern:
  - `--seed-from <state.json>`
  - `--save-state <next.json>`
- `run-until-exec` now also accepts:
  - `--save-state <next.json>`
- `run-until-exec`, `eventlog capture`, and the `--run-until` variants
  of `savestate save`, `checkpoint save`, and `session save` now also
  accept:
  - `--auto-tick-addr <addr>`
  - `--auto-tick-period <n>`
  - this increments one writable byte counter every N executed
    instructions
  - use it to escape counter-wait frontiers such as `_ngpc_vsync` when
    IRQ/VBlank delivery is still unmodeled
  - this is a diagnostic convenience, not reference emulation
- Named checkpoints now exist on top of raw savestate files:
  - `checkpoint save <rom> <name>`
  - `checkpoint list <rom>`
  - `checkpoint load <rom> <name>`
  - `checkpoint delete <rom> <name>`
  - execution runners also accept `--seed-checkpoint <name>` and `--save-checkpoint <name>`
- Named sessions now exist on top of managed checkpoint frontiers:
  - `session save <rom> <name>`
  - `session list <rom>`
  - `session load <rom> <name>`
  - `session delete <rom> <name>`
  - execution runners also accept `--seed-session <name>` and `--save-session <name>`
- Sessions now also support lightweight snapshots:
  - `session snapshot save <rom> <session> <snapshot>`
  - `session snapshot list <rom> <session>`
  - `session snapshot load <rom> <session> <snapshot>`
  - `session snapshot restore <rom> <session> <snapshot>`
  - `session snapshot delete <rom> <session> <snapshot>`
- `eventlog capture` now also accepts `--seed-checkpoint <name>`, so
  diff/regression captures can stay on the named-checkpoint workflow
  without threading raw JSON paths manually.
- `eventlog capture` now also accepts `--seed-session <name>` for the
  same reason on the session workflow.
- This is still a minimal session manager, not a full debugger
  workspace:
  session frontier -> execute/capture -> update session frontier.

#### 4.1.5 Named session workflow

```text
python ngpc_emu.py session save <rom> <name> --seed-xsp 0x4100
python ngpc_emu.py step-exec <rom> --seed-session <name> --save-session <name>
python ngpc_emu.py eventlog capture <rom> <output.json> --seed-session <name> --count 8
python ngpc_emu.py session load <rom> <name>
```

- A session stores one managed "current frontier" checkpoint plus a
  small metadata file under `.ngpc_emu/sessions/`.
- `--save-session <name>` creates or updates that current frontier in
  place.
- `--seed-session <name>` resumes from that current frontier.
- This is intended to remove the manual checkpoint-name juggling from
  the current headless debugger workflow; it is not yet a full history,
  branching, or multi-frontier session model.
- `checkpoint list` hides the managed session frontiers and snapshots so
  generic checkpoint listings stay user-facing instead of exposing
  session plumbing.

#### 4.1.6 Session snapshots

```text
python ngpc_emu.py session snapshot save <rom> <session> <snapshot>
python ngpc_emu.py session snapshot list <rom> <session>
python ngpc_emu.py session snapshot restore <rom> <session> <snapshot>
```

- Snapshots are a thin manual history layer above one session's current
  frontier.
- `save` captures the session's current frontier into a separate managed
  checkpoint.
- `restore` copies that snapshot back into the session's current
  frontier.
- This is still not branching history or rewind; it is just enough to
  keep a few useful waypoints while staying inside the named-session
  workflow.

## 5. Modes

The target product includes:
- standalone application
- `NgpCraft_engine` integration
- headless / CI mode

All three should rely on the same emulation core.

### 5.1 Engine integration contract status

- The first engine/emulator contract is now specified in
  `specs/ENGINE_INTEGRATION_CONTRACT.md`.
- v1 formalizes the preferred first shipping mode as
  `controlled-standalone`:
  - `NgpCraft_engine` launches the emulator as a separate process
  - request/response ownership, artifact paths, and save-root policy
    are explicitly defined
- This contract does NOT mean the full engine-side wiring is already
  implemented.
- A first bridge entry point now exists:
  - `python ngpc_emu.py engine-bridge <request.json>`
  - it emits a structured JSON response on `stdout`
  - currently useful for headless engine actions first
- At the current prototype stage:
  - `smoke-run`, `capture-eventlog`, and `capture-savestate` are
    implemented through the bridge
  - `run`, `debug`, and `profile` still return `partial` while the
    standalone GUI/debugger is not wired yet

## 6. Implementation Direction

The planned final emulation core targets `C++`.
Early bootstrap tools and prototypes may exist in `Python`, especially for:
- headless experiments
- format validation
- tooling
- engine bridge work

The Python bootstrap is not intended to define the final core language.

## 7. Documentation Policy

This manual is intentionally built over time.

Whenever a user-visible feature lands, document:
- what it does
- how to use it
- any limits or caveats

## 8. Current State

Snapshot 2026-05-21 — **744 tests passed, 0 skipped**.

The emulator is no longer "minimal": M0 + M1a + M1c + M1d are closed,
M1b SR is at Phase 2 partial (PUSH/POP SR opcodes wired), and M2 is
**fully feature-complete for K2GE color mode**: Phase 0 inspectors,
Phase 0.5 single-tile rasterizer, and a Phase 1.3 full-compose
framebuffer renderer (backdrop + SCR1/SCR2 + sprites with PR.C
4-level composition + chain + global PO offset + window clip with
OOWC fill + NEG invert) with binary PPM export. **ROADMAP §8 P0 is
now 9/9 green** — every priority-0 debugger bullet ships.

CLI surface (grouped by intent):

ROM / bootstrap:
- `python ngpc_emu.py info <rom>` — ROM header summary
- `python ngpc_emu.py reset-info <rom>` — bootstrap machine state
- `python ngpc_emu.py addr-info <rom> <addr>` — qualify one address
- `python ngpc_emu.py cpu-info <rom>` — minimal CPU container

Memory access:
- `python ngpc_emu.py peek <rom> <addr> [--count N]` — read bytes via the bus
- `python ngpc_emu.py memory-dump <rom> <addr> [--count N] [--width W] [--seed-from state.json] [--json]` — hexdump + ASCII column
- `python ngpc_emu.py fetch-next <rom>` — raw byte window at the bootstrap PC

Decode / disassembly:
- `python ngpc_emu.py decode-next <rom> [--address <addr>]` — one-instruction decode
- `python ngpc_emu.py trace-preview <rom> [--address] [--count]` — static linear preview
- `python ngpc_emu.py opcode-coverage <rom> [--start <addr>] [--bytes N] [--top N] [--stop-on-silicon-broken] [--stop-on-non-fallthrough] [--follow-direct-control-flow]` — decoder-gap census / static coverage lens
- `python ngpc_emu.py step-preview <rom>` / `next-preview <rom>` / `run-until-preview <rom> <target>` — static stepping previews
- (range disassembly with symbol annotation: use the sister
  `NgpCraft_Disasm/ngpc_disasm.py` CLI; the emulator delegates that
  surface and keeps `decode-next` for single-instruction work)

Execution:
- `python ngpc_emu.py execute-next <rom> [--seed-...]` — execute one instruction
- `python ngpc_emu.py step-exec <rom> [--seed-from/--save-state/--seed-checkpoint/--seed-session]`
- `python ngpc_emu.py run-steps <rom> [--count] [--seed-...]`
- `python ngpc_emu.py trace-exec <rom> [--count] [--seed-...]`
- `python ngpc_emu.py run-until-exec <rom> <target_pc> [--seed-...] [--auto-tick-addr ... --auto-tick-period N]`

Persistence (savestates, checkpoints, sessions, snapshots):
- `python ngpc_emu.py savestate save <rom> <output.json> [--run-until ...] [--seed-...]`
- `python ngpc_emu.py savestate load <input.json> [--rom]`
- `python ngpc_emu.py checkpoint save|list|load|delete ...`
- `python ngpc_emu.py session save|list|load|delete ...`
- `python ngpc_emu.py session snapshot save|list|load|restore|delete ...`

Event log:
- `python ngpc_emu.py eventlog capture <rom> <output.json> [...]`
- `python ngpc_emu.py eventlog inspect <input.json> [--rom] [--limit]`
- `python ngpc_emu.py eventlog diff <a.json> <b.json>`
- `python ngpc_emu.py eventlog check <rom> <golden.json>`
- `python ngpc_emu.py eventlog golden-save|golden-load|golden-list|golden-delete|golden-check ...`
- `python ngpc_emu.py eventlog golden-check-all <rom> [--count N | --run-until ADDR] [--seed-from] [--stop-on-fail] [--save-current PATH] [--map MAP] [--json]`
  CI single-command trace regression — capture one run and diff
  against every stored event-log golden. Exit `0` only when all
  match. Mirrors the `frame golden-check-all` workflow for the
  Niveau B test pyramid layer.
- `python ngpc_emu.py eventlog profile <log> --map <map>` — per-symbol bucketing

Symbols:
- `python ngpc_emu.py map info <map>` / `map lookup-name <map> <name>` / `map lookup-addr <map> <addr>`

Debugger (M4 P0):
- `python ngpc_emu.py registers <rom> [--seed-from state.json] [--json]` — rich CPU view
- `python ngpc_emu.py breakpoint add <rom> <addr> [--label] [--json]`
- `python ngpc_emu.py breakpoint add-symbol <rom> <name> --map <map> [--label] [--json]`
- `python ngpc_emu.py breakpoint list|remove|clear|check <rom> [...]`
- `python ngpc_emu.py watchpoint add <rom> <addr> [--kind write|read|access] [--size N] [--label] [--value BYTE] [--json]`
- `python ngpc_emu.py watchpoint list|remove|clear|check <rom> [...]`

M2 Phase 0 inspectors (K2GE):
- `python ngpc_emu.py palette-info <rom> [--kind all|sprite|scr1|scr2|background|window] [--seed-from] [--json]`
- `python ngpc_emu.py oam-info <rom> [--visible-only] [--seed-from] [--json]`
- `python ngpc_emu.py tilemap-info <rom> [--plane scr1|scr2] [--non-empty] [--list] [--seed-from] [--json]`

M3 Phase 0 — frame/scanline state model (pass 34):
- `python ngpc_emu.py tick-frame <rom> [--scanlines N | --frames N] [--seed-from] [--save-state] [--json]`
  Advances the K2GE frame/scanline counter (198 scanlines/frame,
  152 visible + 46 VBlank) without running CPU instructions.
  Mutually exclusive `--scanlines` / `--frames`; default = 1
  scanline. Reports VBlank enter/leave transitions during the
  advance. Savestate v3 carries `frame_state` so subsequent
  Phase 3.1+ commands (read bus driven by frame timing, IRQ
  delivery) can consume it. Bumped savestate format to v3 with
  backward compat for v2 (missing `frame_state` defaults to
  the documented reset state — scanline 0, frame 0).

M3 Phase 3.1 — RAS.V + BLNK live everywhere `--seed-from` flows (passes 35-36):
- The K2GE registers `RAS.V` (`0x008009`) and `2D Status` bit 6
  BLNK (`0x008010`) are now driven by the seed savestate's
  `frame_state` across the **full** CLI surface — both the
  read-only inspector chain (`memory-dump`, `palette-info`,
  `oam-info`, `tilemap-info`, `tile-view`, `tiles-view`,
  `screenshot`, `frame *`) AND the executor chain (`step-exec`,
  `run-steps`, `trace-exec`, `run-until-exec`, `eventlog capture`,
  `eventlog check`, `eventlog golden-check`, `eventlog
  golden-check-all`). The engine bridge "render *" / "check *" /
  `capture-eventlog` / `smoke-run` actions do the same forwarding.
  Workflow: `tick-frame --save-state` to advance to scanline N,
  then any subsequent command with `--seed-from` (or
  `--seed-checkpoint` / `--seed-session`) observes RAS.V=N during
  CPU reads of 0x8009 and the BLNK bit live at 0x8010. Output
  savestates preserve `frame_state` verbatim through executor
  chains (Phase 3.1 doesn't advance frame_state per-instruction;
  Phase 3.2 will). Game polling loops on the BLNK bit can be
  stepped through by seeding at scanline ≥ 152.

M3 Phase 3.2.0 + 3.2.1 — frame_state advances during CPU exec (pass 37):
- Every CPU instruction now counts a flat ~8 cycles (placeholder),
  rolling up to a scanline every ~64 instructions
  (`CYCLES_PER_SCANLINE = 517`). The executor chain emits output
  savestates whose `frame_state` reflects `seed.frame_state +
  (executed_count × 8 / 517) scanlines`. Active for `step-exec`,
  `run-steps`, `trace-exec`, `run-until-exec`, plus
  `savestate save --run-until`, `checkpoint save --run-until`,
  `session save --run-until`. The flat estimate is documented as a
  non-reference-mode divergence in `HARDWARE_COMPAT_POLICY.md § 4.3`
  — Phase 3.2.3 replaces it with the proper TLCS-900 per-opcode
  cycle table.

UI — PyQt6 debugger shell with floating inspector windows (passes 41-46):
- **Launch**: `python ngpc_emu.py ui` opens the PyQt6 window. Pass
  an optional ROM path (`python ngpc_emu.py ui my.ngc`) to load it
  immediately, or use File → Open ROM… (Ctrl+O) once the window
  is up. Requires PyQt6 (`pip install PyQt6`). The frontend is
  PyQt6 per ROADMAP §4 + §Mode 2 + §298 ; the pass-41 Tkinter
  prototype was off-spec and was rewritten in PyQt6 during pass 43.
- **File menu** (passes 44-45) — classic editor entries with
  standard shortcuts :
  - Open ROM…           Ctrl+O
  - Close ROM           Ctrl+W
  - --- separator ---
  - Load Savestate…     Ctrl+L
  - Save Savestate      Ctrl+S       (quick-save to last path)
  - Save Savestate As…  Ctrl+Shift+S (always prompt)
  - --- separator ---
  - Load Symbol Map…    (pass 45)    — loads a t900ld .map file
  - --- separator ---
  - Quit                Ctrl+Q
  When no ROM is loaded, the LCD shows "(no ROM loaded)", the
  registers panel is "—", the disasm + memory panels show
  "(no ROM loaded)", and every session-dependent menu action +
  execution button is greyed out.
- **Symbol annotations** (pass 45) — once a `.map` is loaded,
  exact-address symbols are appended to disasm lines as
  `; symbol_name` ; the status bar prepends `PC=0xH (name+0xN)`.
- **Breakpoints panel** (pass 45) — independent floating window
  (see Floating inspector windows below) :
  - Address input accepts hex (`0x…`), decimal, OR a symbol name
    when a `.map` is loaded.
  - `Add` button (Enter also works) / `@PC` button to add a BP
    at the current PC.
  - List sorted by address ; double-click a row to remove ;
    `Remove` / `Clear` action buttons.
  - Disasm lines that match a BP are prefixed with `●`.
  - During continuous Run, the loop pauses automatically on BP
    hit (status bar shows `last: breakpoint-hit`). Press `Step`
    once to leave the BP before resuming.
- **Watchpoints panel** (pass 47) — independent floating window :
  - Address input (hex / decimal / symbol name).
  - Kind dropdown : write / read / access.
  - Optional value filter (`0xFF`, `0x42`, etc.) — fire only when
    the first byte of the accessed range matches.
  - Same Add / list / double-click-to-remove / Remove / Clear
    workflow as Breakpoints.
  - List displays `W` / `R` / `A` kind letter + address +
    optional size + optional `=value` + optional label.
  - During Run / Step / Step Frame, the loop pauses on the first
    matching memory access (write or read per kind). Status bar
    surfaces the hit : `watch: write 0x00000010=[AB]`.
- **Floating inspector windows** (pass 46-47, updated pass 52) —
  classic IDE / MAME-debugger layout. The 5 inspector panels (CPU
  Registers, Disassembly, Memory, Breakpoints, Watchpoints) are
  independent floating windows, **hidden by default** (pass 52)
  so the main window stays uncluttered ; open each on demand via
  View → <name>. The main window contains only the LCD game view +
  execution buttons + status bar.
- **Directory memory** (pass 52) — the File dialogs (Open ROM /
  Load Savestate / Save Savestate As / Load Symbol Map) remember
  the last directory you picked for each kind, so subsequent
  dialogs open at the same folder. Persisted via QSettings under
  `%APPDATA%/NgpCraft/Emulator.ini` (Windows) or the platform
  equivalent.
- **View menu** (pass 46) next to File :
  - One checkable entry per inspector (CPU Registers,
    Disassembly, Memory, Breakpoints). Closing a window via [X]
    auto-unchecks the entry ; clicking the entry toggles it back.
  - Show All Inspector Windows / Hide All Inspector Windows
    convenience actions.
  - Reset Window Layout — re-shows all inspectors and re-arranges
    them at their default positions around the main window.
  Inspector windows can also be dragged back onto the main
  window's dock areas if you prefer a docked layout — free
  benefit from QDockWidget.
- **LCD canvas** (160×152 scaled 3× to 480×456) shows the live K2GE
  composed frame, refreshed after every action. PyQt's
  `QImage.fromData(ppm, "PPM")` consumes the renderer's P6 PPM
  bytes directly ; scaled with nearest-neighbour
  (`FastTransformation`) so pixel-art stays crisp.
- **CPU registers panel** : PC, XSP, 7 R32 (XWA/XBC/XDE/XHL/XIX/XIY/XIZ),
  iff_level, rfp, flags (SZVHCN — uppercase = bit set, lowercase =
  bit clear, `·` = unknown).
- **Buttons** : Step (1), Step 10, Step 1000, Step Frame (runs until
  the next frame boundary), Run/Pause (continuous), Reset.
- **Continuous run loop** (pass 42) : click Run, the emulator
  ticks 1000 instructions every 16 ms via `QTimer.timeout` and
  the LCD + registers + status bar refresh in real time. Click
  Pause to stop. Runs at ~ 1/12 of real-HW speed under CPython —
  fine for debugging, not for play. Auto-stops on blocked
  execution.
- **Disassembly panel** (pass 43) : walks 14 instructions forward
  from the current PC. Each line shows
  `0xADDR  raw_bytes  mnem operands` ; the PC line is highlighted
  yellow + bold. Decode failures render as `<status>` in red.
- **Memory panel** (pass 43) : hex view, 12 rows × 16 bytes (192
  bytes). Address bar accepts `0xHHHHHHHH`, decimal, or auto-prefixed
  ints. Buttons : Go (refresh at typed address), @PC (jump to current
  PC), @XSP (jump to stack pointer). The writable overlay shadows
  the read bus, matching what the executor sees ; unbacked bytes
  render as `??` / `.`.
- **File menu** : Open ROM, Load Savestate, Save Savestate, Quit.
- **Status bar** : frame count, scanline, visible/VBLANK indicator,
  IRQ pending mask, accumulated cycles, last stop reason +
  exec/IRQ-delivery counters.
- `EmulatorSession` (core/, tech-neutral) auto-folds VBlank pending
  when a step's cycle-driven advance crosses scanline 152 → the
  next step delivers the IRQ through the executor's
  `try_deliver_pending_irq` path. Real-HW loop closes
  interactively. Cycle residue is tracked across small batches,
  so 65 single-Step clicks correctly advance one scanline.

M3 Phase 3.2.3a — per-instruction cycle accounting infrastructure (pass 40):
- Every `ExecutionResult` carries a `cycles_consumed` field (default
  `ESTIMATED_CYCLES_PER_INSTRUCTION = 8` — the flat placeholder).
  `IrqDeliveryResult.cycles_consumed = IRQ_DELIVERY_CYCLES = 13`
  on successful delivery (Toshiba TLCS-900/H IRQ entry cost).
- Run results (`RunStepsResult`, `RunUntilResult`,
  `ExecutionTraceResult`) accumulate `total_cycles_consumed` across
  all executed instructions + delivered IRQs.
- `_advance_frame_state_for_run` now consumes the exact cycle total
  when provided. Result: `tick-frame → step-exec` chained workflows
  that deliver an IRQ correctly advance `frame_state` by the IRQ's
  13-cycle entry cost (the prior `executed_count × 8` math missed
  this).
- This is **architectural prep work** for Phase 3.2.3b (populate
  per-opcode cycle counts from the Toshiba spec table). The
  user-visible change today is small ; the value is that 3.2.3b
  becomes a per-opcode default-value change rather than a
  CLI-surface rewire.

M3 Phase 3.2.2b — VBlank IRQ delivered by the executor (pass 39):
- The executor now samples the IRQ controller between instructions
  and **delivers** pending VBlank IRQs end-to-end. When the seed
  savestate's `irq_state.pending_mask` has bit 4 set AND the seed
  CPU's `iff_level < 4`, the next `step-exec` (or `run-steps` /
  `trace-exec` / `run-until-exec`) iteration pushes PC + SR onto
  the stack (6 bytes total, PC on top), sets PC to
  `VBLANK_VECTOR_ADDRESS = 0x006FCC`, raises iff_level to 4, and
  clears the pending bit. The output savestate persists the new
  CPU state (PC at vector + 1 instruction worth, XSP - 6) and the
  cleared `irq_state`.
- `RETI` opcode `0x07` is now executed: pops PC (4B at XSP), then
  SR (2B at XSP+4), advances XSP by 6, restores all six flags +
  iff_level + rfp atomically.
- Chained workflow `tick-frame --scanlines 160 --save-state pre.json`
  then `step-exec --seed-from pre.json --save-state post.json`
  delivers the VBlank IRQ on step 1 ; `post.json` shows
  `cpu.pc = 0x006FCD` and `irq_state.pending_mask = 0`.
- Known limitation: instruction fetch reads the read bus only (not
  the writable overlay), so software relying on a BIOS-installed
  JMP at the vector RAM (cold-start 0x00 = NOP) won't go through
  the BIOS shim. The executor jumps to the vector address directly.
  Phase 3.2.2c (deferred) or BIOS HLE will close that gap.

M3 Phase 3.2.2a — VBlank IRQ pending state observable (pass 38):
- `tick-frame` now reports VBlank IRQ pending status alongside the
  scanline advance. JSON payload gains `irq_before` / `irq_after`
  (each `pending_mask: int` + `vblank_pending: bool`) and
  `constants.vblank_irq_level = 4` + `constants.vblank_vector_address_hex
  = "0x006FCC"`. Human output adds an `IRQ pending: 0xNN (VBlank:
  YES/NO)` line.
- The VBlank pending bit (bit 4 of the mask) becomes **set** when
  emulated time crosses scanline 152 (visible → VBlank) and stays
  set across `"leave"` transitions — the executor will clear it on
  IRQ delivery (Phase 3.2.2b). `--seed-from` carries an existing
  pending bit forward, and the output savestate persists the new
  mask in an additive `irq_state` section (v3 format unchanged ;
  v3-pre-3.2.2a saves continue to load with `pending_mask=0`).
- This is **state-only observability** — the CPU is not yet
  interrupted on VBlank. Phase 3.2.2b wires push PC + SR / JMP via
  `0x006FCC` / iff_level gating / RETI (opcode 0x07) into the
  executor.

M2 Phase 0.5 — first visual lens:
- `python ngpc_emu.py tile-view <rom> <tile-id> [--plane sprite|scr1|scr2 --palette N] [--seed-from] [--json]`
  Renders one 8×8 CHAR_RAM tile as 4-level grayscale ASCII art.

M2 Phase 1 extension — CHAR_RAM-wide tile atlas (pass 24):
- `python ngpc_emu.py tiles-view <rom> [--range N..M] [--cols C] [--plane sprite|scr1|scr2 --palette N] [--seed-from] [--output PATH.ppm] [--json]`
  Renders a grid of CHAR_RAM tiles as a binary P6 PPM atlas. Bridge
  between the single-tile `tile-view` ASCII inspector and the full
  K2GE compose `screenshot`. Default range `0..511` (full CHAR_RAM,
  128×256 px in 16 cols × 32 rows), grayscale 4-level by default,
  optional palette colorisation with the same contract as
  `tile-view`. Atlas pixels do NOT go through scroll / flip /
  priority / window / NEG — it's a pure "show me CHAR_RAM"
  inspector keyed off the same `--seed-from` overlay every other
  M2 Phase 0 lens uses.

Frame diff + named frame goldens (pass 25 — Niveau C visual regression):
- `python ngpc_emu.py frame diff <ppm_a> <ppm_b> [--json]`
  Byte-compare two P6 PPM files. Exit `0` if every pixel matches,
  `1` if any pixel differs (with first-diff coordinate + counts).
- `python ngpc_emu.py frame golden-save <rom> <name> [--seed-from] [--label] [--json]`
  Render the current frame and store it as a named visual golden
  under `.ngpc_emu/goldens-frame/`. Mirrors the eventlog `golden-*`
  workflow but for PPMs. Manifest carries SHA-256 of both the ROM
  and the PPM, dimensions, ISO timestamp, `renderer_pass`, and a
  full K2GE control-register snapshot.
- `python ngpc_emu.py frame golden-check <rom> <name> [--seed-from] [--save-current PATH.ppm] [--json]`
  Re-render and byte-compare against the stored golden. Exit `0`
  on match, `1` on diff. `--save-current` writes the new frame
  for manual triage.
- `python ngpc_emu.py frame golden-check-all <rom> [--seed-from] [--stop-on-fail] [--save-current-dir DIR] [--json]`
  CI single-command visual regression — renders the current frame
  once and byte-compares against every stored golden. Exit `0` only
  when all match. `--stop-on-fail` short-circuits on the first
  diff; `--save-current-dir` writes `<rom-stem>.current.ppm` for
  side-by-side triage.
- `python ngpc_emu.py frame golden-list <rom> [--json]` /
  `python ngpc_emu.py frame golden-delete <rom> <name> [--json]`
  Registry management. Per-ROM, same slug-derivation rule as the
  checkpoint / session / event-log golden layers.

M2 Phase 1.3 — full K2GE color-mode composite (closes §8 P0 9/9):
- `python ngpc_emu.py screenshot <rom> [--seed-from] [--output PATH.ppm] [--json]`
  Composes one 160×152 NGPC frame and writes a binary P6 PPM file
  (default `./screenshot.ppm`, 72 975 bytes). Pass 1.3 ships the
  **full K2GE color-mode pipeline**: backdrop + SCR1/SCR2 scroll
  planes + sprites with PR.C 4-level composition + window clip
  with OOWC fill + NEG invert. The 8-step compose order is:
  backdrop → sprites behind → SCR back → sprites middle → SCR
  front → sprites front → window clip → NEG invert. The JSON
  payload exposes the full K2GE control-register snapshot plus
  `backdrop_color` and `oowc_color` resolved through the backdrop
  block, so `screenshot` doubles as a control-register inspector.
  Cold-start ROM loads pre-populate `WSI.H = WSI.V = 0xFF` and
  `REF = 0xC6` to match documented HW reset values, so a freshly
  loaded ROM produces a full-screen no-clip backdrop image rather
  than the strict "empty window → all OOWC" branch.

Engine bridge:
- `python ngpc_emu.py engine-bridge <request.json>`

The full per-pass timeline (passes 3 to 20 of 2026-05-20) lives in
`DEVLOG.md`. Read it bottom-up for the implementation order, or
top-down for the most recent shape of each feature.

Example:

```text
python ngpc_emu.py info path/to/game.ngc
python ngpc_emu.py reset-info path/to/game.ngc
python ngpc_emu.py addr-info path/to/game.ngc 0x200040
python ngpc_emu.py cpu-info path/to/game.ngc
python ngpc_emu.py peek path/to/game.ngc 0x200024 --count 12
python ngpc_emu.py fetch-next path/to/game.ngc --count 8
python ngpc_emu.py decode-next path/to/game.ngc
python ngpc_emu.py decode-next path/to/game.ngc --address 0x200043
python ngpc_emu.py execute-next path/to/game.ngc --address 0x200043
python ngpc_emu.py execute-next path/to/game.ngc --address 0x20009B
python ngpc_emu.py execute-next path/to/game.ngc --address 0x2079C6 --seed-xsp 0x4100
python ngpc_emu.py execute-next path/to/game.ngc --address 0x20D06C --seed-xsp 0x40F4 --seed-reg XIZ=0x12345678
python ngpc_emu.py run-steps path/to/game.ngc --address 0x2079C6 --seed-xsp 0x4100 --seed-reg XIZ=0x12345678 --count 5
python ngpc_emu.py step-exec path/to/game.ngc --seed-xsp 0x4100 --save-state step1.json
python ngpc_emu.py step-exec path/to/game.ngc --seed-from step1.json --save-state step2.json
python ngpc_emu.py trace-exec path/to/game.ngc --count 2 --seed-from step2.json --save-state trace_frontier.json
python ngpc_emu.py checkpoint save path/to/game.ngc boss-door --run-until 0x210000
python ngpc_emu.py step-exec path/to/game.ngc --seed-checkpoint boss-door --save-checkpoint boss-door-next
python ngpc_emu.py session save path/to/game.ngc dev-loop --seed-xsp 0x4100
python ngpc_emu.py step-exec path/to/game.ngc --seed-session dev-loop --save-session dev-loop
python ngpc_emu.py session snapshot save path/to/game.ngc dev-loop after-call
python ngpc_emu.py session snapshot restore path/to/game.ngc dev-loop after-call
python ngpc_emu.py eventlog capture path/to/game.ngc dev-loop.eventlog.json --seed-session dev-loop --count 8
python ngpc_emu.py eventlog capture path/to/game.ngc boss-door.eventlog.json --seed-checkpoint boss-door --count 8
python ngpc_emu.py run-steps path/to/game.ngc --count 2 --save-state step1.json
python ngpc_emu.py run-steps path/to/game.ngc --count 2 --seed-from step1.json --save-state step2.json
python ngpc_emu.py eventlog capture path/to/game.ngc demo_eventlog.json --count 8 --seed-xsp 0x6C00 --seed-reg XIZ=0
python ngpc_emu.py eventlog inspect demo_eventlog.json --rom path/to/game.ngc --limit 4
python ngpc_emu.py eventlog diff left_eventlog.json right_eventlog.json
python ngpc_emu.py engine-bridge bridge_request.json
python ngpc_emu.py trace-preview path/to/game.ngc --count 12
python ngpc_emu.py trace-preview path/to/game.ngc --address 0x200050 --count 8
python ngpc_emu.py step-preview path/to/game.ngc
python ngpc_emu.py step-preview path/to/game.ngc --address 0x200094
python ngpc_emu.py next-preview path/to/game.ngc --address 0x200094
python ngpc_emu.py run-until-preview path/to/game.ngc 0x200098 --address 0x200094
python ngpc_emu.py run-until-preview path/to/game.ngc 0x20912F --address 0x200094 --mode into
python ngpc_emu.py run-until-exec path/to/game.ngc 0x210000 --seed-reg XIZ=0 --seed-xsp 0x6000
python ngpc_emu.py run-until-exec path/to/game.ngc 0x210000 --seed-from step2.json --save-state frontier.json
```

Current output includes:
- ROM path
- file size
- title
- copyright text
- entry point
- game ID
- version
- mono/color mode

`reset-info` currently exposes:
- the bootstrap model status
- the initial `PC` derived from the ROM header
- the list of memory regions currently modeled

`addr-info` currently exposes:
- whether one address is mapped or not in the current minimal address space
- the region name and kind
- the offset inside the region
- the ROM file offset when the address points inside the loaded cartridge image

`cpu-info` currently exposes:
- the first architectural CPU state container
- `PC`
- placeholder fields for `SR`, flags and register bank
- the 32-bit general register set shape

`peek` currently exposes:
- read-only byte access through the minimal bus model
- actual bytes when the address is backed by the loaded cartridge ROM
- explicit `unbacked` / `unmapped` status otherwise

`fetch-next` currently exposes:
- a raw byte fetch window starting from the current bootstrap `PC`
- the fetched bytes when the current `PC` points to ROM-backed data
- a simple sequential `next PC` helper when the read succeeds

Important:
- `fetch-next` is not a decoder yet
- it does not claim the real instruction length
- it is only a first fetch helper to prepare later decode work

`decode-next` currently exposes:
- one first execution-neutral instruction decode helper
- a small bootstrap-focused TLCS-900 subset only
- the decoded mnemonic, operands, raw bytes and sequential `next PC` when the opcode family is supported
- explicit `unknown-opcode` or `truncated` status when the current minimal decoder cannot decode honestly
- first support for prefixed register-family instructions and indexed `(r32+d8)` memory forms used by real bootstrap code
- explicit warning text for a first set of known silicon-risk patterns
- matched local quirk metadata in text and JSON output when the current decoded instruction hits one known local hardware quirk
- that quirk payload now also includes the local quirk-database version and a non-empty `sources` attribution list
- corrected ALU-register handling for the `D8..DF` family so real prologue code such as `add XSP, imm32` decodes with the right width
- first control-flow metadata such as kind, direct target and fall-through information when the current subset can classify them

Important:
- `decode-next` is not a full TLCS-900 decoder
- unsupported instructions stay unsupported instead of being guessed
- this command does not execute or mutate CPU state
- the optional `--address` flag lets you inspect one explicit ROM address without changing the bootstrap machine state
- warnings and matched quirk metadata are diagnostic only and do not change the decoded result

`execute-next` currently exposes:
- one first real instruction application helper built on top of the current decoder
- a before/after CPU-state view when the current minimal execution subset can apply the instruction honestly
- a narrow real execution subset:
  - `NOP`
  - direct unconditional jumps with a known target
  - immediate register loads when the write is representable by the current CPU state model
  - first absolute-address `LDA R32, (abs24)` forms
  - first prefixed register-to-register `LD`
  - first prefixed register-to-register `CP`
  - first indexed memory compare: `CP (r32+d8), R32`
  - first abs16 byte compare-immediate: `CP (abs16), imm8`
  - first prefixed register `inc` / `dec` forms when the current register view is representable
    and the decoded prefix family is not locally confirmed silicon-broken
  - first conditional `JR` / `JRL` execution when the required modeled flags are known
  - first indexed load slice: `LD R32, (r32+d8)`
  - first post-increment byte-memory slice:
    - `LD R8, (r32+)`
    - `LD (r32+), R8`
    - `LD (r32+), imm8`
  - a first writable stack overlay for:
    - `pushw`
    - `push`
    - `pop`
    - `call`
    - `ret`
    - `retd`
  - one first indexed writable-store slice: `LD (r32+d8), R32`
- explicit statuses when execution is blocked by decode failure, runtime-dependent control flow, unknown stack state, read-only stack targets, unmodeled side effects, a partial-register representation gap, or a locally confirmed silicon-broken family
- JSON output now also carries a `matched_quirk` object when the current instruction matches one known local hardware quirk
- that nested quirk object now also includes the local quirk-database version and a non-empty `sources` attribution list (`document`, optional `section`, optional `quote`)
- memory-write records in text and JSON output when the current instruction mutates the current writable runtime overlay
- flag-change records in text and JSON output when the current instruction updates the modeled flag subset
- optional manual register seeds for one-shot validation while bootstrap reset still leaves state unknown:
  - repeatable `--seed-reg XWA=...` .. `--seed-reg XSP=...`
  - `--seed-xsp` kept as a convenience shortcut for the common stack-only case

The full supported execution subset as of 2026-05-22:
- `NOP`
- direct unconditional jumps (`JP`, `JR`, `JRL`, `CALR`) with a known target
- immediate register loads (`LD R32, imm32`, `LD R32, #3` compact)
- absolute-address `LDA R32, (abs24)`
- prefixed register-to-register `LD`
- prefixed register-to-register `CP` and `CP (r32+d8), R32` indexed
- abs16 byte compare-immediate: `CP (abs16), imm8`
- prefixed register `INC` / `DEC` forms on currently safe decoded families
- conditional `JR` / `JRL` execution when the required modeled flags are known
- indexed load: `LD R32, (r32+d8)`, `LD R8, (r32+d8)`
- **absolute word loads - widened 2026-05-22 (pass 68)**:
  - `LD R16, (abs16)`
  - `LD R16, (abs24)` for the observed `D2 abs24 word` subset
- **secondary-indexed word/control-flow slice - widened 2026-05-22 (pass 69)**:
  - `LD R16, (r32+r8/r16)` on the observed `D3` family
  - `JP (r32+r8/r16)` on the observed `F3 ... D8` family
- **banked register-file execution slice - widened 2026-05-22 (pass 70)**:
  - `LDF n` now performs a real visible-core bank switch for `XWA/XBC/XDE/XHL`
  - `C7` explicit-bank and previous-bank byte targets on `XWA/XBC/XDE/XHL` now execute
  - banked byte slots persist in savestates
  - current-bank byte-slot knowledge can be consumed even when the full owner `XWA/XBC/XDE/XHL` is still unknown
- **optional BIOS-backed execution - widened 2026-05-22 (pass 71)**:
  - `peek`, `decode-next`, `execute-next`, `step-exec`, and `run-steps` accept `--bios <64KB image>`
  - reads in `0xFF0000..0xFFFFFF` can now be backed by a real external BIOS dump instead of stopping as unbacked
  - this moves BIOS-path frontiers from `runtime-memory-unavailable` to the next real register / ABI blocker when the image is available
- **bank-qualified execution seeds - widened 2026-05-22 (pass 72)**:
  - `--seed-reg` now accepts:
    - `XWA@bank0..3`
    - `XBC@bank0..3`
    - `XDE@bank0..3`
    - `XHL@bank0..3`
  - these seeds populate the persistent banked backing store and become visible automatically if the selected bank later becomes current via `LDF`
  - this is the intended way to drive BIOS-bank entry code honestly when the caller context lives in bank 3 instead of in the default visible bank
- **BIOS-call seed shortcut - widened 2026-05-22 (pass 73)**:
  - new CLI convenience flag:
    - `--seed-zero-bios-call-context`
  - expands to the current exploratory BIOS-call seed set:
    - `XBC@bank3 = 0`
    - `XDE@bank3 = 0`
    - `XHL@bank3 = 0`
    - `XIY = 0`
    - `XIZ = 0`
  - useful when stepping BIOS entry code that immediately saves the caller context
  - intentionally documented as an analysis shortcut, not as a verified hardware reset contract
  - explicit `--seed-reg NAME=VALUE` still overrides this preset
- **toolchain-derived caller-saved seed shortcut - widened 2026-05-22 (pass 74)**:
  - new CLI convenience flag:
    - `--seed-zero-caller-saved`
  - expands to the current toolchain-v2 observed caller-saved set:
    - `XWA = 0`
    - `XBC = 0`
    - `XDE = 0`
    - `XHL = 0`
    - `XIX = 0`
    - `XIZ = 0`
  - intentionally leaves `XIY` untouched:
    - the local toolchain models `XIY` as the frame-pointer / callee-preserved register
    - this makes the preset more precise than `--seed-zero-bank0` for ordinary call-boundary exploration
  - intended use:
    - stepping around ordinary function calls where caller-clobbered temporaries can be zero-seeded
    - avoiding an invented `XIY` value when the callee-preserved frame context is not actually known
  - this is an ABI/toolchain convention shortcut, not a hardware reset contract
  - explicit `--seed-reg NAME=VALUE` still overrides this preset
- **toolchain-derived `__adecl` arg-register shortcut - widened 2026-05-22 (pass 75)**:
  - new CLI convenience flag:
    - `--seed-zero-adecl-args`
  - expands to the current toolchain-v2 observed ABI-v2 argument registers:
    - `XWA = 0`
    - `XBC = 0`
    - `XDE = 0`
  - intentionally does not seed:
    - `XHL`
    - `XIX`
    - `XIY`
    - `XIZ`
  - intended use:
    - stepping into register-argument entry paths that follow the observed `__adecl` mapping
    - keeping the assumption narrower than `--seed-zero-caller-saved` when only argument registers are justified by local toolchain evidence
  - this is an ABI/toolchain convention shortcut, not a hardware reset contract
  - explicit `--seed-reg NAME=VALUE` still overrides this preset
- **word-memory multiply/divide into XR32 - widened 2026-05-22 (pass 67)**:
  - `(r32)` `MUL/MULS/DIV/DIVS XR32, (r32)`
  - `(r32+d8)` `MUL/MULS/DIV/DIVS XR32, (r32+d8)`
  - signed forms (`MULS` / `DIVS`) now share the same honest runtime rules as the unsigned slice
  - divide-by-zero still blocks honestly instead of inventing a packed quotient/remainder state
- **(r32+d8) byte-indexed ALU R8 ↔ mem, both directions — new 2026-05-22 (pass 58)**:
  - `ADD/ADC/SUB/SBC/AND/XOR/OR R8, (r32+d8)` and symmetric memory-destination forms
  - `CP R8, (r32+d8)` and `CP (r32+d8), R8`
  - exact sub-op table now mirrors the byte `(r32)` family on `0x80..0xFF`
  - ADC/SBC block honestly on `runtime-state-required` when CF is unknown
- **(r32) byte-indirect load: `LD R8, (r32)` — new 2026-04-09**
- **current-bank partial byte reuse - widened 2026-05-22 (pass 70)**:
  - byte register reads/writes can now reuse current-bank slot knowledge from the banked backing store
  - this already unblocks cases like prefixed `ADD W, W` after `LDF 3` even when full `XWA` is still unknown
  - the same byte-slot fallback is now used for secondary-indexed effective addresses when the index register is `W/A/B/C/D/E/H/L`
- **generic current-bank register fallback - widened 2026-05-22 (pass 72)**:
  - generic `R8` reads can fall back to the current-bank byte slot even if the owning `XWA/XBC/XDE/XHL` field is still unknown
  - generic `R16` reads can reconstruct the low 16 bits from the current-bank backing store
  - generic `R32` reads can reconstruct the full owner from the four current-bank slots when all four are known
  - this widens the benefit from the earlier banked backing store beyond `C7`-specific helpers to ordinary consumers like `push BC` and `push XBC`
- **(r32) byte-indirect ALU R8 ↔ mem, both directions — new 2026-05-20/21 (passes 51/53/54/55)**:
  - `CP R8, (r32)` (pass 51, sub-op `0xF0..0xF7`)
  - `AND/OR/XOR R8, (r32)` and `AND/OR/XOR (r32), R8` (pass 53, sub-ops `0xC0..0xEF`)
  - `ADD/SUB R8, (r32)` and `ADD/SUB (r32), R8` (pass 54, sub-ops `0x80..0x8F` + `0xA0..0xAF`)
  - **pass 56 widening** — 16 new sub-op ranges (single-byte fixed-code subset) :
    - `INC #n, (r32)` / `DEC #n, (r32)` (sub-ops `0x60..0x6F`, n=0→8 Toshiba quirk, CF preserved per Toshiba spec)
    - 8-op shift/rotate family on memory : `RLC` / `RRC` / `RL` / `RR` / `SLA` / `SRA` / `SLL` / `SRL (r32)` (sub-ops `0x78..0x7F`)
    - `RL`/`RR` block honestly on `runtime-state-required` when CF is unknown (rotate-through-carry)
  - **pass 55 widening** — 13 new sub-op ranges on the same prefix :
    - `EX (r32), R8` (sub-op `0x30..0x37`, swap mem byte ↔ R8, flags unchanged)
    - `ADC R8, (r32)` and `ADC (r32), R8` (sub-ops `0x90..0x9F`, carry-in propagation)
    - `SBC R8, (r32)` and `SBC (r32), R8` (sub-ops `0xB0..0xBF`, borrow-in propagation)
    - `CP (r32), R8` (sub-op `0xF8..0xFF`, operand order reversed — flags = mem − R8)
    - `ADD/ADC/SUB/SBC/AND/XOR/OR (r32), imm8` 3-byte ALU-immediate forms (sub-ops `0x38..0x3E`)
  - all set the 6 flags (S/Z/V/H/C/N) via `_compute_add_flags` / `_compute_subtract_flags` / `_compute_logical_flags`
  - ADC/SBC block honestly on `runtime-state-required` when CF is unknown
  - oracle-verified against `NgpCraft_Disasm/ngpc_disasm.py::decode_one` before adding to executor (104/104 broad sweep clean for pass 55)
- post-increment byte-memory: `LD R8, (r32+)`, `LD (r32+), R8`, `LD (r32+), imm8`
- post-increment long-word store: `LD (r32+), R32`
- stack: `PUSHW`, `PUSH`, `POP`, `CALL`, `RET`, `RETD`
- indexed register stores: `LD (r32+d8), R8/R16/R32`
- **(r32+d8) immediate stores: `LD (r32+d8), imm8`, `LDW (r32+d8), imm16` — new 2026-04-09**
- **abs24 byte-memory ALU R8 ↔ mem, both directions — new 2026-05-22 (pass 59)**:
  - `ADD/ADC/SUB/SBC/AND/XOR/OR R8, (abs24)` and symmetric memory-destination forms
  - `CP R8, (abs24)` and `CP (abs24), R8`
  - same honest blocking rules as other byte-memory ALU families for unknown CF / unknown source / unreadable memory
- **absolute-memory bit / carry-flag / immediate-ALU slice — widened 2026-05-22 (passes 60..62)**:
  - `BIT/TSET/RES/SET/CHG bit, (abs24)` and `BIT/TSET/RES/SET/CHG bit, (abs16)`
  - `ANDCF/ORCF/XORCF/LDCF/STCF bit, (abs24|abs16)`
  - `ANDCF/ORCF/XORCF/LDCF/STCF A, (abs24|abs16)` with dynamic bit index `A & 0x0F`
  - `ADD/ADC/SUB/SBC/AND/XOR/OR/CP (abs16), imm8`
  - honest blocking remains in place for unknown CF, unknown `A` source, undefined byte bit index `8..15`, unreadable memory, and unwritable targets
- absolute-memory stores: `LD (abs24), R8/imm8`, `LDW (abs24), imm16`, `LDW (abs16), R32/imm8/imm16`
- **ARI secondary indexed stores: `LD (r32+r16), imm8`, `LDW (r32+r16), imm16` — new 2026-04-09**
- **(r32) register-indirect stores: `LD (r32), imm8`, `LDW (r32), imm16`**
- **CPU I/O immediate stores: `LDB (n), imm8` (0x08), `LDW (n), imm16` (0x0A) — new 2026-04-09**
- ALU-immediate: `ADD/ADC/SUB/SBC/AND/XOR/OR/CP r, #N` via prefixed family
- **ALU register-register: `ADD/SUB R, r` all sizes — new 2026-04-09**
- `EXTS r` / `EXTZ r` (sign/zero-extend)
- `EI n` / `DI` (IFF tracked, interrupt dispatch not modeled)
- `SWI` (basic)
- confirmed broken `D0..D7` word-register prefix instructions now stop explicitly with
  `silicon-broken` instead of falling back to a generic unsupported status
  - the current reference model keeps the documented immediate-safe forms executable
- confirmed broken `D8..DF` working-bank `r+r` ALU forms now also stop with
  `silicon-broken`; the safe set covers all immediate families
  (`A8..AF` compact `ld`, `C8..CF` ALU-imm, `D8..DF` `cp imm3`, `E8..EF` shift-imm,
  everything in `0x00..0x7F`, plus the `F0..F7` `CP r+r` exception and the
  `F8..FF` shift-by-A family deferred to a dedicated future quirk)

Important:
- `execute-next` is the first real state-mutation command, but it is not a full interpreter
- stack execution is still narrow and local to one command invocation
- interrupts, halts, general memory/IO writes, full flag/SR evaluation and multi-step persistence are still outside the current subset
- 8-bit and 16-bit register writes only execute when the owning 32-bit register is already known
- each CLI invocation still starts from the bootstrap state, so this command does not yet preserve multi-step execution state across separate runs

`run-steps` currently exposes:
- one first bounded stateful execution helper built on top of the current `execute-next` subset
- real CPU-state carry between records inside one invocation
- carry of the current writable runtime overlay between instructions
- a first real ability to progress through simple register arithmetic in the carried state, not just loads/jumps/calls
- a first real ability to enter and iterate the official-toolchain byte-copy loop, and to execute the sibling zero-fill loop with direct seeded entry
- an explicit stop reason when execution stops on an unsupported or blocked instruction before the requested count
- a per-record execution log with:
  - decoded instruction
  - execution status
  - register changes
  - memory-write chunks
- the same manual register seeding model used by `execute-next`

Important:
- `run-steps` is still not a full debugger loop
- it only chains the currently implemented execution subset
- it is bounded by `--count` and stops as soon as one step cannot be applied honestly
- it does not yet preserve session state across separate CLI invocations
- it is the first real `run N` slice, not the final trace/run control promised by the roadmap
- the current stable official-toolchain ROM now crosses:
  - `cp (0x6F91), 0x00`
  - `ld (0x005F80), A`
  - `res/set` on `0x6F86`
  - the tiny init subroutine at `0x20D21D`
  - vector initialization through `0x6FFC`
  - the first `ld (abs16), imm8` writes on `0x8002..0x8035`
- the K2GE register region `0x8000..0x8FFF` is now backed with power-on-default `0x00`, enabling
  read-modify-write operations (`res`/`set`) on K2GE control registers during bootstrap
- the stable ROM now also crosses:
  - `res 7, (0x8030)`, `ld (0x8020), 0x00`, `ld (0x8021), 0x00`, `res 7, (0x8012)`
  - `ld XWA, 0` (compact 2-byte small-immediate load, catalog `C8+zz+r : A8+#3`)
  - `ld XBC, XWA`
- `exts r` / `extz r` (sign/zero-extend 16→32 or 8→16 bits)
- ALU-immediate forms: `add/sub/and/xor/or/cp r, #N` via prefixed family
- `(r32)` register-indirect stores:
  - `ld (r32), imm8`
  - `ldw (r32), imm16`
- `ei n` / `di` execution: IFF tracked as `bool | None` in `NgpcCpuState`; interrupt dispatch not yet modeled
- the current stable smoke frontier is `0x0020CD4D: rl A, SP`
  (`stopped-on-silicon-broken`) - confirmed broken `D0..D7` family on NGPC silicon

`run-until-exec` currently exposes:
- a stateful execution helper that chains `execute-next` steps forward until a target `PC` is reached or execution is blocked
- real CPU-state carry and writable runtime overlay carry between each step, identical to `run-steps`
- a final stopped state showing the last instruction executed, the stop reason, and the step count reached
- the same manual register seeding model used by `execute-next` and `run-steps`

Stop reasons:
- `target-reached` - the target `PC` was reached and execution stopped
- `stopped-on-unsupported-decoded-instruction` - the next instruction decoded but has no executor yet
- `stopped-on-silicon-broken` - the next instruction belongs to a locally confirmed broken family and reference execution refuses to invent a fake post-state
- when that happens, JSON output now includes the current local quirk metadata:
  - database version
  - quirk id
  - category
  - confidence
  - summary
  - non-empty `sources` attribution list with per-source `document` plus optional `section` and `quote`
- `stopped-on-unknown-opcode` - the next byte could not be decoded at all
- `stopped-on-*` - any other honest executor block (missing register, missing memory, read-only target, etc.)

Important:
- `run-until-exec` does not preserve state across separate CLI invocations
- it is bounded by the current execution subset; it stops at the first honest block regardless of step count
- it is a smoke-run and bisect helper, not a full debugger `run` command
- the current stable smoke target (`StarGunner_save_lib_test/bin/main.ngc`) reaches **25 072 honest steps** from the bootstrap entry point with seed `--seed-xsp 0x6C00 --seed-reg XIZ=0` (as of 2026-04-22, after the D8..DF `r+r` rule landed)
- the current honest blocker is now `0x0020D180: ld XBC, XWA` (`D8 89`, `stopped-on-silicon-broken`) — confirmed broken `D8..DF` `r+r` ALU family per USER_MANUAL_EN.md §12.1
- the previous blocker `0x0020CD4D: rl A, SP` (`D0..D7` family, step 27 556) is still correct, it just sits further along the trajectory

`trace-preview` currently exposes:
- a first linear trace-shaped preview built on top of the current decoder
- multiple sequential records starting from the bootstrap `PC` or one explicit address
- per-record bytes, decoded assembly and warning text when available
- per-record matched local quirk metadata when one decoded instruction hits a known local quirk
- an explicit stop reason when the preview stops before the requested record count
- an optional `--stop-on-control-flow` mode to stop after the first decoded branch/jump/call/return-like instruction
- by default, it now also stops on a locally known `silicon-broken` instruction instead of continuing into downstream bytes that are not execution-reachable on real hardware

Important:
- `trace-preview` is not an execution trace
- it walks by sequential `next PC` only
- it does not evaluate branch conditions or follow taken control flow
- it is a bootstrap inspection tool, not the final trace format promised by the roadmap
- `--stop-on-control-flow` is useful when you want a cleaner static preview of the current block instead of a longer linear walk
- the silicon-broken stop is a hardware-faithful safety stop, not a decode failure

`opcode-coverage` currently exposes:
- a linear decoder-gap census over a fixed byte budget from one start PC
- decoded-byte percentage, unknown-opcode totals and top leading-byte offenders
- the historical default mode: continue through the whole byte budget even if some bytes decode as unknown, because the goal is broad decoder-gap discovery
- an optional strict mode:
  - `--stop-on-silicon-broken`
  - counts the current silicon-broken instruction as decoded, then stops the walk instead of letting downstream bytes pollute the unknown census
- an optional structural stop mode:
  - `--stop-on-non-fallthrough`
  - stops after the first decoded instruction with `falls_through = False`
  - useful when bytes after `ret/reti/jp/halt/swi` should not be treated as meaningful frontier bytes for the current walk
- an optional conservative direct-CFG mode:
  - `--follow-direct-control-flow`
  - replaces the pure linear sweep with a worklist over in-budget decoded instruction starts
  - follows sequential fallthrough edges plus known decoded `direct_target` edges
  - useful when you want a more reachable-oriented static lens without pretending to solve indirect jumps/calls

Important:
- default mode and strict mode answer slightly different questions
- default mode is better for broad decoder-gap hunting
- strict mode is better when you want an execution-faithful static frontier
- non-fallthrough mode is better when you want a pure sequential-fallthrough frontier and do not want dead bytes after an explicit control-flow terminator to pollute the census
- direct-control-flow mode is better when you want a conservative static reachable set over direct edges and do not want unconditional branches to leave linear dead bytes in the census
- `--follow-direct-control-flow` cannot be combined with `--stop-on-silicon-broken` or `--stop-on-non-fallthrough`

`step-preview` currently exposes:
- one first static `step into` preview built on top of the current decode metadata
- a resolved next target for non-control-flow instructions
- a resolved direct target for direct `call` / `jp` style cases
- the nested decode payload, including matched local quirk metadata when relevant
- an explicit unresolved result for conditional branches and runtime-dependent control flow

Important:
- `step-preview` is not real execution
- it does not mutate the machine state
- it does not evaluate flags
- it only reports what the current static decode model can defend honestly

`next-preview` currently exposes:
- one first static `step over` / `next` preview built on top of the same decode metadata
- sequential next `PC` for non-control-flow instructions
- return-site preview for direct calls, assuming the call returns normally
- the nested decode payload, including matched local quirk metadata when relevant
- explicit unresolved results when runtime state is needed

Important:
- `next-preview` is still not execution
- it is a static convenience preview, not a debugger-grade guarantee of where runtime will actually stop

`run-until-preview` currently exposes:
- one first static `run until` preview built by chaining the current `step-preview` or `next-preview` rules
- one explicit target `PC`
- a default `over` mode that assumes direct calls return normally
- an optional `--mode into` that follows direct call targets instead
- per-step decode payloads inherit matched local quirk metadata when relevant
- explicit stop reasons for target reached, unresolved control flow, decode failure, cycle detection or exhausted step budget

Important:
- `run-until-preview` is not real run control
- it does not execute instructions
- it does not evaluate branch conditions
- it only follows the current static stepping model, so the reported path is a defended preview rather than a runtime guarantee

Important:
- this command does not yet claim a full hardware-accurate reset state
- unknown values stay unknown until verified

Detailed usage sections will be expanded as new features land.
