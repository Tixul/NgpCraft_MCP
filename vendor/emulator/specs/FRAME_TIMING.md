# NGPC Frame / Scanline Timing v1 (M3 Phase 0)

Purpose:
- model the K2GE frame and scanline counters so HW reads of `RAS.V`
  (`0x8009`) and `2D Status` bit 6 BLNK (`0x8010`) can return live
  values driven by emulated time (Phase 3.1 — pending)
- expose a deterministic, savestate-persisted `FrameState` so the
  `tick-frame` CLI (M3 Phase 0) and downstream M3 sub-phases share
  one source of truth for "where are we in the frame?"
- foundation for the IRQ delivery work in M3 Phase 3.2+ (VBlank
  vector, HBlank, raster-position-triggered IRQ)

Hardware source:
- `01_SDK/docs/K2GETechRef.txt` § 4-7 "FRAME RATE REGISTER" + § 4-8
  "Raster Position Register"
- Direct quote: *"signal generation for the 0th line occurs at the
  beginning of line 198"* → scanlines cycle `0..197`, total **198**
  scanlines per frame
- *"H_INT signal is not generated at line 151"* → visible region is
  lines `0..151` (152 scanlines, matching the 160 × 152 LCD), VBlank
  occupies lines `152..197` (46 scanlines)

These values are HW-canonical and do not depend on the `REF`
register (`0x8006`, reset `0xC6`); REF is documented as locked /
"do not modify".

## 1. Scope (Phases 0 + 3.1a + 3.1b)

**Phase 0** ships pure state — no read-bus wiring, no IRQ delivery.
**Phase 3.1a** adds the read-bus override for `RAS.V` (`0x008009`)
and the BLNK bit of `2D Status` (`0x008010`), plus the consumer
plumbing for `memory-dump` and the K2GE inspectors (`palette-info`,
`oam-info`, `tilemap-info`, `tile-view`, `tiles-view`, `screenshot`,
`frame *`). **Phase 3.1b** plumbs `frame_state` through the
executor chain — `step-exec`, `run-steps`, `trace-exec`,
`run-until-exec`, `eventlog capture`, `eventlog check`, the engine
bridge "render *" / "check *" / `capture-eventlog` / `smoke-run`
actions. CPU reads of `RAS.V` / `0x008010` during execution now
reflect the seeded timing, and the output savestate preserves
`frame_state` across chained commands. IRQ delivery is Phase 3.2.

- `FrameState(scanline, frame_count)` carries the model
- `advance_scanlines(state, n)` / `advance_frames(state, n)`
  arithmetic, with wrap into `frame_count` modulo 2³²
- `detect_vblank_transitions(state, n)` enumerates enter / leave
  VBlank events that would fire during a scanline advance
- Savestate format **v3** carries `frame_state` per save
- CLI `tick-frame` advances the model and emits an updated savestate

Out of scope for Phase 0:
- `RAS.V` reads returning `frame_state.scanline` (Phase 3.1 — read
  bus wiring)
- BLNK bit driven by `frame_state.in_vblank` in `2D Status` reads
  (Phase 3.1)
- VBlank IRQ delivery at the visible→VBlank boundary (Phase 3.2)
- HBlank IRQ + raster position trigger (Phase 3.3)
- Mid-frame palette / OAM / tilemap swaps via raster IRQ — orthogonal,
  depends on Phase 3.2+

## 2. Data model

`core/frame_timing.py` exposes:

| Constant                | Value | Source                                    |
|-------------------------|-------|-------------------------------------------|
| `SCANLINES_PER_FRAME`   | 198   | K2GETechRef quote                         |
| `VISIBLE_SCANLINES`     | 152   | LCD height + H_INT line 151 quote         |
| `VBLANK_SCANLINES`      | 46    | derived: 198 − 152                         |
| `FRAMES_PER_SECOND`     | 60    | K2GETechRef § 4-7 FRAME RATE              |

```python
@dataclass(frozen=True)
class FrameState:
    scanline: int          # 0..197
    frame_count: int       # 32-bit wrap

    @property
    def in_vblank(self) -> bool:
        return self.scanline >= VISIBLE_SCANLINES

    @property
    def in_visible_region(self) -> bool:
        return self.scanline < VISIBLE_SCANLINES
```

Helpers (all pure, return a new `FrameState`):

- `initial_frame_state()` → `FrameState(scanline=0, frame_count=0)` —
  the documented HW reset.
- `advance_scanlines(state, n)` — `n >= 0`, monotone. Wraps
  `state.scanline + n` modulo `SCANLINES_PER_FRAME` and carries the
  overflow into `frame_count`. Frame count wraps modulo 2³² (≈ 2
  years of continuous 60 fps).
- `advance_frames(state, n)` — snap scanline to 0 and add `n` to
  `frame_count`. Discards sub-frame position.
- `detect_vblank_transitions(state, n)` — enumerate `enter` /
  `leave` events while advancing `n` scanlines. Each event reports
  the scanline + frame_count **at** the boundary:
  - `enter`: visible → VBlank crossing, scanline = `VISIBLE_SCANLINES` = 152
  - `leave`: VBlank → next frame's visible region, scanline = 0,
    frame_count = post-increment value

Phase 0 is monotone (no rewind). Negative `n` raises `ValueError` —
reverse stepping belongs to M5.

## 3. Savestate integration

Bump: `SAVESTATE_FORMAT_VERSION = "2026-05-20.v3"`.

Backward compat: v2 saves continue to load. The version check
accepts both `v3` (current) and the `SAVESTATE_BACKWARD_COMPAT_VERSIONS`
tuple. v2 saves missing the `frame_state` section default to
`initial_frame_state()` — matching the documented HW reset state.

Payload shape (additive):

```json
{
  "format_version": "2026-05-20.v3",
  "...": "...",
  "frame_state": {
    "scanline": 152,
    "frame_count": 0
  }
}
```

`build_savestate_payload(*, frame_state=None, ...)` defaults
`frame_state` to `initial_frame_state()` so every existing call site
transparently emits a v3 payload with the documented reset state.
Only the `tick-frame` CLI (and future Phase 3.1+ commands) pass an
explicit `FrameState`.

The loader returns `SavestateDocument.frame_state: FrameState`
(always present, never `None`) — internal default-on-missing keeps
the calling contract simple.

## 3.5 Bus override (Phase 3.1a)

`load_read_bus(path, *, frame_state=None)` and
`_build_builtin_readable_bytes(header, *, frame_state=None)` accept
an optional `frame_state`. When provided, the cold-start image gets
two HW-faithful overrides:

| Address | Field          | Override                                          |
|---------|----------------|---------------------------------------------------|
| 0x008009 | RAS.V         | `frame_state.scanline & 0xFF` (0..197)            |
| 0x008010 | 2D Status     | `0x40` when `frame_state.in_vblank`, else `0x00`  |

C.OVR (sprite overflow, bit 7 of 0x008010) is **not modeled** —
stays 0 always. Other bits of 0x008010 also stay 0 since they're
reserved.

`frame_state=None` (the default) is byte-identical to
`frame_state=initial_frame_state()` (scanline 0, in_vblank=False),
so every caller that doesn't forward a frame_state observes the
same bytes as before Phase 3.1a — backward compat is automatic.

Consumer plumbing (read-only chain — Phase 3.1a):
- `_build_palette_memory_view(rom_path, seed_from)` extracts
  `doc.frame_state` and forwards it to `load_read_bus`. Used by
  `palette-info`, `oam-info`, `tilemap-info`, `tile-view`,
  `tiles-view`, `screenshot`, `frame golden-save`,
  `frame golden-check`, `frame golden-check-all`.
- `memory-dump --seed-from` does the same forward directly.

Executor chain (Phase 3.1b — **done**): `step-exec`, `run-steps`,
`trace-exec`, `run-until-exec`, `eventlog capture` / `eventlog check`
all extract `seed_from_doc.frame_state` and forward it through
`load_run_steps(initial_frame_state=…)` / `load_run_until(…)` /
`load_execution_trace(…)` / `load_fetch_view(…)`. Engine bridge
"render *" / "check *" / `capture-eventlog` / `smoke-run` actions
do the same forwarding via `seed_from_doc.frame_state` when
`start_mode == "savestate"`. CPU reads of `RAS.V` / `0x8010`
during these commands now reflect the live seeded timing.

Phase 3.1 doesn't advance `frame_state` during execution — it
remains static during one run. `_save_execution_savestate(*,
final_frame_state=…)` carries the seed's value forward into the
output savestate so `step-exec --seed-from A --save-state B` makes
B's `frame_state` == A's `frame_state`. Phase 3.2 will introduce
per-instruction advancement based on emulated cycle count.

**Known divergence — write to read-only register**: on real HW
0x008009 (RAS.V) and the read-only bits of 0x008010 ignore CPU
writes. Our model lets the writable overlay shadow the bus override
(the overlay always wins on read). Software that writes to those
addresses sees its own write back instead of the HW value — wrong,
but only matters if a game does the (HW-invalid) thing.

## 3.6 IRQ pending state (Phase 3.2.2a)

`core/frame_timing.py` adds an `IrqState` carrying the pending-IRQ
bitmask, separated from `FrameState` so the IRQ controller model can
grow without churning the timing core.

| Constant                | Value     | Source                                |
|-------------------------|-----------|---------------------------------------|
| `IRQ_LEVEL_VBLANK`      | 4         | NGPC_HW_QUICKREF.md § 4               |
| `VBLANK_VECTOR_ADDRESS` | 0x006FCC  | BIOS install (system page 0x6Fxx)     |

```python
@dataclass(frozen=True)
class IrqState:
    pending_mask: int = 0

    def is_vblank_pending(self) -> bool:
        return bool(self.pending_mask & (1 << IRQ_LEVEL_VBLANK))

    def with_vblank_pending(self) -> "IrqState": ...
    def with_vblank_cleared(self) -> "IrqState": ...
```

`fold_vblank_irq_pending(irq_state, transitions)` walks an
advancement's `VBlankTransition` tuple and sets bit 4 on every
`"enter"` event. `"leave"` transitions do **not** clear the bit —
the executor will clear on IRQ delivery (Phase 3.2.2b), or via
explicit ack at the IRQ controller (future).

Savestate v3 carries an additive `"irq_state": {"pending_mask": int}`
section. Missing → defaults to `initial_irq_state()` (no pending
IRQs). Format version is unchanged (still `2026-05-20.v3`) because
the field is additive; v2 saves continue to load and default both
`frame_state` and `irq_state` to their reset values.

`tick-frame` now consumes/produces `IrqState`:
- `--seed-from <state>` extracts the seed's `irq_state` as the
  "before" snapshot.
- Detected VBlank transitions feed through
  `fold_vblank_irq_pending` to produce the "after" `IrqState`.
- The output savestate persists the new mask.
- JSON includes `irq_before` / `irq_after` (`pending_mask` +
  `vblank_pending` boolean) and `constants.vblank_irq_level` +
  `constants.vblank_vector_address_hex`.
- Human-readable adds an `IRQ pending: 0xNN (VBlank: YES/NO)` line.

Phase 3.2.2a is **state-only**. No CPU work happens at the
visible→VBlank boundary; the bit just becomes set. Phase 3.2.2b
(pass 39) wires the executor — see § 3.7 below.

## 3.7 Executor-side IRQ delivery (Phase 3.2.2b)

`core/execute.py` adds two pieces:

1. **`_try_execute_reti`** — opcode `0x07` (RETI). Pops a 4-byte PC
   at XSP (top of stack), then a 2-byte SR at XSP+4, advances XSP by
   6. Decodes the popped SR into all six flags + `iff_level` + `rfp`
   atomically. Mirrors the existing `_try_execute_push_pop_sr` block.

2. **`try_deliver_pending_irq(view, cpu, memory, irq_state)`** —
   module-level helper sampling the IRQ controller between
   instructions. Public because the run loops (`build_run_steps`,
   `build_run_until`) call it before each `build_execute_next`.

   Gating per Toshiba TLCS-900/H spec: a pending IRQ at level `L` is
   delivered when `L > cpu.iff_level`. For VBlank (`L = 4`), that's
   `cpu.iff_level < 4`. `iff_level == 7` masks everything.

   Stack frame layout (matches RETI's pop order — PC on top):
   - Push SR first (2 bytes) at `XSP-2..XSP-1`
   - Push PC second (4 bytes) at `XSP-6..XSP-3` (lowest addr = top)
   - `new_xsp = XSP - 6`

   After delivery:
   - `cpu.pc = VBLANK_VECTOR_ADDRESS` (0x006FCC)
   - `cpu.iff_level = IRQ_LEVEL_VBLANK` (4) — masks same-and-lower
     priority IRQs while in the ISR
   - VBlank pending bit cleared via `with_vblank_cleared`

   Returns an `IrqDeliveryResult` carrying `delivered: bool`,
   `after_cpu`, `after_memory`, `after_irq_state`, and an optional
   `blocked_reason`. Three behavioral classes:
   - **Not pending / masked**: `delivered=False`,
     `blocked_reason=None`. Normal "nothing to do" path.
   - **Soft defer** (`iff_level/xsp/SR partially modeled`):
     `delivered=False`, `blocked_reason=None`. The run continues
     normally — the IRQ stays pending, software may model the
     missing fields in subsequent instructions and the next sample
     can deliver. Note carries the deferral reason.
   - **Hard block** (writable-range failure during push):
     `delivered=False`, `blocked_reason` set. The IRQ controller
     decided to deliver but the bus refused the push ; the run
     loops surface this as `stopped-on-<reason>`.

### Run-loop integration

`build_run_steps(count=N, irq_state=...)` and
`build_run_until(target_pc=…, irq_state=...)` accept an optional
`IrqState`. When provided, each iteration:

1. Samples the IRQ controller via `try_deliver_pending_irq` at the
   current `(cpu, memory, irq_state)`.
2. If `delivered`: updates state in place ; `irq_deliveries += 1` ;
   does NOT increment `executed_count` (IRQ delivery isn't a
   fetched instruction) ; continues into the same iteration.
3. If `blocked_reason` is set: stops the loop with that reason.
4. Falls through to the regular `build_execute_next` call for the
   fetched instruction.

Both result dataclasses gain two fields:
- `final_irq_state: IrqState | None`
- `irq_deliveries: int`

`irq_state=None` (the legacy default) skips sampling entirely —
byte-identical pre-3.2.2b behavior for every existing caller.

### Savestate persistence

The 4 executor CLI handlers (`step-exec`, `run-steps`, `trace-exec`,
`run-until-exec`) extract `seed_doc.irq_state`, forward it through
`initial_irq_state=...`, then save `final_irq_state` in the output
savestate. Same for `savestate save --run-until`,
`checkpoint save --run-until`, `session save --run-until`.

Chained workflow now closes the loop:

```
# Advance to VBlank (sets pending bit).
tick-frame rom.ngc --scanlines 160 --save-state /tmp/pre_irq.json

# Step one instruction with iff_level<4 — IRQ delivers, PC jumps to
# 0x006FCC, pending bit cleared.
step-exec rom.ngc --seed-from /tmp/pre_irq.json --save-state /tmp/in_isr.json

# (Inside ISR — modeled cleanup happens here in the game code.)

# Execute RETI from the ISR — PC + SR restored.
step-exec rom.ngc --seed-from /tmp/in_isr.json
```

### Known limitations

- **Fetch from writable RAM not modeled**: instruction fetch goes
  through the read bus only, not the writable overlay. The vector
  page (0x6Fxx) is in cold-start RAM (0x00 = NOP) ; software relying
  on a BIOS-installed JMP at the vector won't find it. Workaround for
  now: vector address resolves directly to the ISR entry. Phase
  3.2.2c (or BIOS HLE) will install the JMP byte sequence.
- **Other IRQ sources**: only VBlank modeled. RTC alarm, timer 0..3,
  Z80 IRQ all sit dormant in `IrqState.pending_mask` until their
  source detectors land.

## 3.8 Per-instruction cycle accounting (Phase 3.2.3a)

Phase 3.2.3a wires explicit `cycles_consumed` fields through the
executor result chain. The architecture is in place ; the per-opcode
table (Phase 3.2.3b) will populate real values.

| Constant                            | Value | Note                                |
|-------------------------------------|-------|-------------------------------------|
| `ESTIMATED_CYCLES_PER_INSTRUCTION`  | 8     | Flat placeholder, every opcode      |
| `IRQ_DELIVERY_CYCLES`               | 13    | Toshiba TLCS-900/H IRQ entry cost   |

### Plumbing

- `ExecutionResult.cycles_consumed: int = ESTIMATED_CYCLES_PER_INSTRUCTION`
  — every executor returns this default ; per-opcode handlers will
  override it in Phase 3.2.3b.
- `IrqDeliveryResult.cycles_consumed: int = 0` — set to
  `IRQ_DELIVERY_CYCLES` only on successful delivery ; zero otherwise.
- `RunStepsResult.total_cycles_consumed`,
  `RunUntilResult.total_cycles_consumed`,
  `ExecutionTraceResult.total_cycles_consumed` — sum of executed
  `cycles_consumed` + delivered IRQ cycles across the run.
- Run loops only sum the `cycles_consumed` of *executed* steps
  (`status == "executed"`) — blocked attempts don't contribute.

### Frame-state advancement

`_advance_frame_state_for_run(initial, executed_count, *,
total_cycles_consumed=None)` now has two modes:
- **Cycle total mode** (preferred, Phase 3.2.3a): pass
  `run_result.total_cycles_consumed`. The helper uses the exact
  accumulated cycles, including IRQ entry costs.
- **Executed-count fallback** (legacy, 3.2.0/3.2.1): when
  `total_cycles_consumed=None`, multiply `executed_count` by 8.
  Used by the bootstrap-only `_cmd_savestate_save` path that
  doesn't gather a run result.

All 4 executor CLI handlers + the 3 chained-save commands pass
`total_cycles_consumed=run_result.total_cycles_consumed`. Result:
IRQ deliveries advance `frame_state` by their actual 13-cycle cost
even though they don't fetch an instruction.

### Why this isn't 3.2.3 (the full table) yet

Populating the per-opcode TLCS-900/H cycle table is a separate
sub-pass with its own risk profile (touching every executor branch).
3.2.3a stabilizes the contract — every callsite uses the cycle
total, every result dataclass carries it, the helper handles both
modes — so 3.2.3b's per-opcode work becomes "change a default value
in N executor returns" rather than "rewire the whole CLI surface."

## 4. CLI

### `tick-frame <rom> [--scanlines N | --frames N] [--seed-from STATE] [--save-state OUT] [--json]`

Advances the timing model and emits an updated savestate. **No CPU
instructions execute** — the CPU section is copied verbatim from the
seed state (or the bootstrap machine when no `--seed-from` is given).

`--scanlines N` and `--frames N` are mutually exclusive. Default
when neither is set: advance one scanline (cheapest meaningful tick).

Human-readable output:

```
ROM: …/main.ngc
Advance: 200 scanline(s)
Before: scanline   0 / frame 0  (in_vblank=False)
After:  scanline   2 / frame 1  (in_vblank=False)
VBlank transitions: 2
  enter  scanline=152  frame=0
  leave  scanline=  0  frame=1
Saved-state: /tmp/after_tick.state.json
```

JSON payload (`--json`):

```json
{
  "rom": "…",
  "seed_from": "…" | null,
  "before": {"scanline": 0, "frame_count": 0, "in_vblank": false, "in_visible_region": true},
  "after":  {"scanline": 2, "frame_count": 1, "in_vblank": false, "in_visible_region": true},
  "advance": {"scanlines": 200, "frames": 0},
  "vblank_transitions": [
    {"kind": "enter", "scanline": 152, "frame_count": 0},
    {"kind": "leave", "scanline": 0,   "frame_count": 1}
  ],
  "save_state": "…" | null,
  "constants": {
    "scanlines_per_frame": 198,
    "visible_scanlines":   152,
    "vblank_scanlines":     46,
    "frames_per_second":    60
  }
}
```

Workflow with `step-exec` (illustrative; full integration lands in
Phase 3.2 when IRQ delivery wires into the executor):

```
# Run some CPU steps, capture frontier.
step-exec rom.ngc --count 1000 --save-state /tmp/run.state.json

# Advance to start of next frame (no CPU work).
tick-frame rom.ngc --seed-from /tmp/run.state.json \
                   --frames 1 \
                   --save-state /tmp/next_frame.state.json

# Continue CPU execution from the new frame.
step-exec rom.ngc --seed-from /tmp/next_frame.state.json --count 1000
```

## 5. M3 sub-phase plan

| Phase | Scope                                                       | Status  |
|-------|-------------------------------------------------------------|---------|
| 3.0   | `FrameState` model + savestate v3 + `tick-frame` CLI       | done    |
| 3.1a  | Bus override for `RAS.V` + BLNK ; inspectors / memory-dump | done    |
| 3.1b  | Executor chain plumbing (step-exec / run-* / eventlog +    | done    |
|       | bridge "render *" + "check *" + capture-eventlog)          |         |
| 3.2.0 | Cycle estimate per instruction + scanline conversion       | done    |
| 3.2.1 | frame_state advancement at CLI save-state boundary         | done    |
| 3.2.2a| `IrqState` pending model + `fold_vblank_irq_pending`       | done    |
|       | + savestate v3 additive `irq_state` + tick-frame obs       |         |
| 3.2.2b| Executor-side IRQ delivery (push PC/SR + vector + RETI)    | done    |
| 3.2.3a| Per-instruction `cycles_consumed` plumbing + IRQ delivery  | done    |
|       | cost (13) ; cycle-total `_advance_frame_state_for_run`     |         |
| 3.2.3b| Populate per-opcode cycle counts from TLCS-900/H table     | pending |
| 3.3   | HBlank IRQ + raster-position trigger                        | pending |

Phase 3.0 ships the state model in isolation so the JSON contract
and savestate format can stabilize before the read bus and IRQ
pipelines start consuming them.

## 6. Not modeled

- REF register (`0x8006`) behavior — locked / "do not modify" per
  HW spec; the model assumes the canonical 60 fps + 198-scanline
  budget unconditionally
- CPU clock gear adjustments (`VECT_CLOCKGEARSET` 0..4) — the
  timing model is independent of CPU clock; gear changes affect
  cycle counts per scanline, not the scanline budget itself
- Mid-frame raster IRQ handlers that swap palettes / OAM mid-line
  — depends on Phase 3.3
- Reverse stepping (negative `n`) — M5
