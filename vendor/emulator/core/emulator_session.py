"""Stateful emulator session — drives execution forward for the UI.

The CLI surface (step-exec, run-steps, etc.) is stateless: each call
re-loads the ROM, seeds from a savestate, runs once, emits an output.
That contract is great for batch / CI / engine-bridge work but
awkward for a GUI that needs to step interactively from a live state.

`EmulatorSession` holds the live CPU + memory overlay + frame_state +
irq_state in memory across calls, exposing simple verbs (`reset`,
`step`, `load_savestate`, `save_savestate`, `render_lcd`,
`snapshot`). It composes the same underlying primitives the CLI uses
(`build_run_steps`, `render_frame`, `build_savestate_payload`) so
the live behavior matches CLI output byte-for-byte at the same seed.

Auto VBlank IRQ pending: after each `step`, the session detects
VBlank transitions in the cycle-driven frame_state advance and
folds them into `irq_state` via `fold_vblank_irq_pending`. The next
step then samples the pending bit through the executor's
`try_deliver_pending_irq` and may deliver the IRQ — closing the
real-HW loop in a UI-driven run.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from core.cpu import NgpcCpuState, StatusFlags
from core.decode import DecodeResult, decode_instruction_at
from core.fetch import load_fetch_view
from core.frame_timing import (
    CYCLES_PER_SCANLINE,
    FrameState,
    IrqState,
    advance_scanlines,
    detect_vblank_transitions,
    fold_vblank_irq_pending,
    initial_frame_state,
    initial_irq_state,
)
from core.machine import load_machine_state
from core.renderer import frame_to_ppm_bytes, render_frame
from core.run_steps import RunStepsResult, build_run_steps
from core.savestate import (
    build_savestate_payload,
    load_savestate,
    save_savestate,
)
from core.symbols import SymbolTable, load_map
from core.watchpoints import WATCHPOINT_KINDS, Watchpoint


@dataclass(frozen=True)
class SessionSnapshot:
    """Read-only view of the live session for UI panels.

    Frozen so UI code can hold a reference without worrying about
    the session mutating it mid-render.
    """

    cpu: NgpcCpuState
    memory: dict[int, int]
    frame_state: FrameState
    irq_state: IrqState
    total_cycles_consumed: int
    last_stop_reason: str | None
    last_executed_count: int
    last_irq_deliveries: int


class EmulatorSession:
    """Live emulator state held in memory, mutated by `step` / `reset`.

    Construction loads the ROM and resets to bootstrap. The session
    can also be re-seeded from a savestate via `load_savestate(path)`.

    All execution flows through `build_run_steps` with the live
    `cpu_state`, `memory_bytes`, and `irq_state`. After each batch:
    - `frame_state` advances by `result.total_cycles_consumed`.
    - VBlank transitions across the advance are folded into
      `irq_state` so the next batch can deliver the pending IRQ.
    """

    # ----- BIOS hand-off (UI 0.7) -----
    #
    # The real NGPC BIOS initializes a handful of CPU registers before
    # transferring control to the cart entry-point. Without this, even
    # the first instruction of a real ROM typically blocks because
    # `CALL` / `PUSH` etc. need a known XSP. The values below are
    # sourced from official docs (not invented), so we can apply them
    # at session construction without violating the
    # `RESET_STATE.md` doctrine.
    #
    # Sources :
    # - `01_SDK/docs/NGPC_HW_QUICKREF.md §2 (MEMORY MAP)` documents
    #   `0x004000–0x006BFF` as 12 KB user RAM and
    #   `0x006C00–0x006FFF` as system-reserved. The BIOS places the
    #   user stack at the top of the user RAM, growing downward —
    #   so `XSP = 0x00006C00` is the canonical hand-off value.
    # - `01_SDK/docs/ngpcspec.txt §INTERRUPT STATE` : *"The software
    #   starts up with interrupts prohibited (DI)"* → `iff_level = 7`
    #   (the TLCS-900/H "all maskable IRQs blocked" mask).
    # - Default register bank is 0 (`rfp = 0`) — the BIOS uses bank 3
    #   for its own state and hands off in bank 0.
    # - Six ALU flags are typically cleared on cold start ;
    #   `nf=0/zf=0/vf=0/hf=0/cf=0/sf=0` is the safest BIOS-equivalent.
    BIOS_HANDOFF_XSP = 0x00006C00
    BIOS_HANDOFF_IFF_LEVEL = 7  # DI per ngpcspec.txt INTERRUPT STATE
    BIOS_HANDOFF_RFP = 0        # user bank
    BIOS_HANDOFF_FLAGS = StatusFlags(
        sf=False, zf=False, vf=False, hf=False, cf=False, nf=False,
    )

    def __init__(
        self, rom_path: Path, *,
        apply_bios_handoff: bool = True,
    ) -> None:
        self.rom_path = Path(rom_path)
        self.machine = load_machine_state(self.rom_path)
        # Whether reset() reapplies the BIOS hand-off seed. Kept as
        # an instance attribute so a UI command could toggle it later
        # (e.g. "strict bootstrap, no BIOS HLE").
        self._apply_bios_handoff = apply_bios_handoff
        # CPU / memory / timing / IRQ — the live mutable state.
        # Bootstrap CPU is augmented with BIOS hand-off state below.
        self.cpu: NgpcCpuState = self._seed_bios_handoff_state(
            self.machine.cpu,
        ) if apply_bios_handoff else self.machine.cpu
        self.memory: dict[int, int] = {}
        self.frame_state: FrameState = initial_frame_state()
        self.irq_state: IrqState = initial_irq_state()
        # UI 0.4 — debugger-state attached to the session:
        # - `symbol_table` : optional t900ld .map symbols for annotation
        # - `_breakpoints` : PC-address → label dict ; the run loop
        #   stops as soon as the CPU lands on one of these addresses
        self.symbol_table: SymbolTable | None = None
        self._breakpoints: dict[int, str] = {}
        # UI 0.6 — watchpoints scanned after each step batch ; on
        # hit, `last_stop_reason` flips to "watchpoint-hit" and the
        # Run loop pauses. `last_watch_hit` carries the details (
        # `(watchpoint, access_kind, address, data_bytes)`) for the
        # UI to surface in the status bar.
        self._watchpoints: list[Watchpoint] = []
        self._next_watchpoint_id: int = 1
        self.last_watch_hit: tuple[Watchpoint, str, int, bytes] | None = None
        # Sub-scanline cycle residue. The session accumulates cycles
        # across small batches and converts to scanlines once the
        # residue reaches CYCLES_PER_SCANLINE — otherwise, single-
        # instruction steps (8 cycles each) would dropdown to zero
        # under integer division and the frame would never advance.
        # Not persisted in savestates: this is intra-session state.
        self._cycle_residue: int = 0
        # Run telemetry — last batch's result, for UI status display.
        self.total_cycles_consumed: int = 0
        self.last_stop_reason: str | None = None
        self.last_executed_count: int = 0
        self.last_irq_deliveries: int = 0

    def _seed_bios_handoff_state(self, cpu: NgpcCpuState) -> NgpcCpuState:
        """Apply the documented BIOS-equivalent register values.

        Returns a new `NgpcCpuState` that matches what the real NGPC
        BIOS posts to the cart entry point. See the class-level
        `BIOS_HANDOFF_*` constants for sourcing. Other R32 registers
        (XWA, XBC, XDE, XHL, XIX, XIY, XIZ) remain unknown — the
        BIOS doesn't guarantee specific values for them on hand-off.
        """
        return replace(
            cpu,
            regs=replace(cpu.regs, xsp=self.BIOS_HANDOFF_XSP),
            flags=self.BIOS_HANDOFF_FLAGS,
            iff_level=self.BIOS_HANDOFF_IFF_LEVEL,
            iff_enabled=(self.BIOS_HANDOFF_IFF_LEVEL < 7),
            rfp=self.BIOS_HANDOFF_RFP,
        )

    def reset(self) -> None:
        """Reset to the documented HW bootstrap state.

        When `apply_bios_handoff` was True at construction (the UI
        default), the reset CPU includes the BIOS-equivalent seed
        (XSP / iff_level / rfp / flags) so the cart entry-point can
        execute its first `CALL` / `PUSH` without blocking.

        Debugger state (`symbol_table`, `_breakpoints`,
        `_watchpoints`) intentionally survives — the user wired them
        in and a Reset shouldn't blow them away. Use the explicit
        `clear_breakpoints` / `clear_watchpoints` to drop them.
        """
        bootstrap = self.machine.cpu
        self.cpu = (
            self._seed_bios_handoff_state(bootstrap)
            if self._apply_bios_handoff
            else bootstrap
        )
        self.memory = {}
        self.frame_state = initial_frame_state()
        self.irq_state = initial_irq_state()
        self._cycle_residue = 0
        self.total_cycles_consumed = 0
        self.last_stop_reason = None
        self.last_executed_count = 0
        self.last_irq_deliveries = 0
        self.last_watch_hit = None

    # Inner-batch sizes for the breakpoint check loop in `step`.
    # When no breakpoints are set we use the large batch and the
    # `step` body collapses to a single `_step_single_batch(count)`
    # call (no perf overhead). When breakpoints exist we drop to 1
    # so every PC transition can be checked — debugger correctness
    # over throughput. The Run-loop tick at 1000 instr/16ms becomes
    # 1000 inner-batches/tick (~62k inner-batches/sec) which is
    # tolerable under CPython for the typical debug-with-BPs use
    # case ; remove all BPs for full Run-loop speed.
    _BREAKPOINT_CHECK_BATCH_FAST = 50
    _BREAKPOINT_CHECK_BATCH_WITH_BPS = 1

    def step(self, count: int = 1) -> RunStepsResult:
        """Execute up to `count` instructions, then advance timing + IRQs.

        Returns the underlying `RunStepsResult` (the LAST inner-batch's
        result) so callers can inspect per-step records if needed.

        Inner-batches of at most `_BREAKPOINT_CHECK_BATCH` instructions
        are used so the loop can check the breakpoint table after
        every batch. If the CPU lands on a breakpoint, the loop stops
        and reports `last_stop_reason = "breakpoint-hit"`.
        Single-instruction calls (`count=1`) effectively never break
        on entry — they always advance one instruction first, then
        check ; this is the standard debugger "step over a BP"
        semantic.
        """
        if count < 1:
            raise ValueError(f"step count must be >= 1; got {count}")
        # Pick the inner-batch size : both breakpoints and
        # watchpoints need per-batch checks. Either armed → use the
        # tight batch (1 instr) so we don't overshoot. Neither armed →
        # one big batch (zero overhead vs the pre-UI-0.4 behavior).
        if self._breakpoints or self._watchpoints:
            batch_size = self._BREAKPOINT_CHECK_BATCH_WITH_BPS
        else:
            batch_size = self._BREAKPOINT_CHECK_BATCH_FAST
        # Clear "last watch hit" at the start of every public step
        # call — only surface a hit that fired during THIS step.
        self.last_watch_hit = None
        last_result: RunStepsResult | None = None
        executed_total = 0
        irq_deliveries_total = 0
        remaining = count
        while remaining > 0:
            inner_count = min(batch_size, remaining)
            result = self._step_single_batch(inner_count)
            last_result = result
            executed_total += result.executed_count
            irq_deliveries_total += result.irq_deliveries
            remaining -= inner_count
            # Inner batch was blocked — propagate and stop.
            if result.stop_reason != "count-reached":
                self.last_stop_reason = result.stop_reason
                self.last_executed_count = executed_total
                self.last_irq_deliveries = irq_deliveries_total
                return result
            # Watchpoint check : did any memory access in this batch
            # match a watchpoint ? (Scanned before BP so the user
            # sees the data-side event first.)
            if self._watchpoints:
                hit = self._scan_watchpoint_hit(result)
                if hit is not None:
                    self.last_watch_hit = hit
                    self.last_stop_reason = "watchpoint-hit"
                    self.last_executed_count = executed_total
                    self.last_irq_deliveries = irq_deliveries_total
                    from dataclasses import replace as _replace
                    return _replace(result, stop_reason="watchpoint-hit")
            # Breakpoint check : did we land on one ?
            if self._breakpoints and self.cpu.pc in self._breakpoints:
                self.last_stop_reason = "breakpoint-hit"
                self.last_executed_count = executed_total
                self.last_irq_deliveries = irq_deliveries_total
                from dataclasses import replace as _replace
                return _replace(result, stop_reason="breakpoint-hit")
        # All inner batches completed cleanly. Use the totals across
        # batches for the session-level telemetry.
        assert last_result is not None
        self.last_executed_count = executed_total
        self.last_irq_deliveries = irq_deliveries_total
        self.last_stop_reason = last_result.stop_reason
        return last_result

    def _scan_watchpoint_hit(
        self, result: RunStepsResult,
    ) -> tuple[Watchpoint, str, int, bytes] | None:
        """Return the first `(watchpoint, kind, address, data)` hit in
        `result.records`'s memory accesses, or None.

        Iteration is one record at a time, writes-then-reads, so the
        earliest temporal hit wins. `kind` is `"write"` or `"read"`
        (the access kind, NOT the watchpoint's `kind`).
        """
        for record in result.records:
            execution = record.execution
            for write in execution.memory_writes:
                for wp in self._watchpoints:
                    if wp.kind not in ("write", "access"):
                        continue
                    if not wp.overlaps_range(write.address, len(write.data)):
                        continue
                    if wp.value is not None:
                        if not write.data or (write.data[0] & 0xFF) != (wp.value & 0xFF):
                            continue
                    return (wp, "write", write.address, write.data)
            for read in execution.memory_reads:
                for wp in self._watchpoints:
                    if wp.kind not in ("read", "access"):
                        continue
                    if not wp.overlaps_range(read.address, len(read.data)):
                        continue
                    if wp.value is not None:
                        if not read.data or (read.data[0] & 0xFF) != (wp.value & 0xFF):
                            continue
                    return (wp, "read", read.address, read.data)
        return None

    def _step_single_batch(self, count: int) -> RunStepsResult:
        """One indivisible step batch — no BP check inside."""
        # Rebuild the fetch view with the live frame_state so the CPU
        # reads of RAS.V (0x8009) and BLNK (0x8010) reflect where we
        # currently are in the frame.
        view = load_fetch_view(self.rom_path, frame_state=self.frame_state)
        result = build_run_steps(
            view=view,
            count=count,
            cpu_state=self.cpu,
            memory_bytes=self.memory,
            irq_state=self.irq_state,
        )

        # Compute advance + fold pending across the run's cycle cost.
        # Accumulate cycle residue across batches so sub-scanline
        # step calls (e.g. UI clicking "Step" once = 8 cycles) still
        # add up to scanline boundaries over time.
        self._cycle_residue += result.total_cycles_consumed
        scanlines_advanced = self._cycle_residue // CYCLES_PER_SCANLINE
        self._cycle_residue %= CYCLES_PER_SCANLINE
        transitions = detect_vblank_transitions(
            self.frame_state, scanlines_advanced,
        )
        new_frame_state = advance_scanlines(
            self.frame_state, scanlines_advanced,
        )
        carried_irq = (
            result.final_irq_state
            if result.final_irq_state is not None
            else self.irq_state
        )
        new_irq_state = fold_vblank_irq_pending(carried_irq, transitions)

        self.cpu = result.final_cpu
        self.memory = dict(result.final_memory)
        self.frame_state = new_frame_state
        self.irq_state = new_irq_state
        self.total_cycles_consumed += result.total_cycles_consumed
        return result

    # ----- Symbols (UI 0.4) -----

    def load_symbol_map(self, path: Path) -> int:
        """Load a t900ld .map file into the session's `symbol_table`.

        Returns the number of symbols loaded. Raises FileNotFoundError
        if the path doesn't exist.
        """
        self.symbol_table = load_map(str(Path(path)))
        return len(self.symbol_table)

    def resolve_symbol(self, address: int) -> str | None:
        """Return `"name+offset"` for the nearest symbol ≤ `address`.

        Returns `None` when no symbol table is loaded or `address`
        precedes the lowest symbol. Exact-address matches drop the
        `+offset` suffix.
        """
        if self.symbol_table is None:
            return None
        sym = self.symbol_table.lookup_address(address)
        if sym is None:
            return None
        offset = address - sym.address
        if offset == 0:
            return sym.name
        return f"{sym.name}+0x{offset:X}"

    # ----- Breakpoints (UI 0.4) -----

    def add_breakpoint(self, address: int, label: str = "") -> None:
        """Set or update a PC breakpoint at `address`."""
        self._breakpoints[address & 0xFFFFFF] = label

    def remove_breakpoint(self, address: int) -> bool:
        """Remove the breakpoint at `address`. Returns True if removed."""
        return self._breakpoints.pop(address & 0xFFFFFF, None) is not None

    def clear_breakpoints(self) -> None:
        """Remove every breakpoint."""
        self._breakpoints.clear()

    def list_breakpoints(self) -> list[tuple[int, str]]:
        """Return `[(address, label), …]` sorted by address."""
        return sorted(self._breakpoints.items(), key=lambda kv: kv[0])

    def has_breakpoint(self, address: int) -> bool:
        """True if `address` is a breakpoint."""
        return (address & 0xFFFFFF) in self._breakpoints

    # ----- Watchpoints (UI 0.6) -----

    def add_watchpoint(
        self, start: int, kind: str = "write", *,
        size: int = 1, value: int | None = None,
        label: str | None = None,
    ) -> Watchpoint:
        """Register a watchpoint. Returns the created `Watchpoint`.

        `kind` must be one of `"write"`, `"read"`, `"access"`.
        `size` defaults to 1 byte ; raise on size < 1. `value` is the
        optional byte-value filter (first byte of the accessed range
        must equal `value` for the hit to fire).
        """
        if kind not in WATCHPOINT_KINDS:
            raise ValueError(
                f"watchpoint kind must be one of {WATCHPOINT_KINDS!r}, "
                f"got {kind!r}"
            )
        if size < 1:
            raise ValueError(f"watchpoint size must be >= 1; got {size}")
        wp = Watchpoint(
            id=self._next_watchpoint_id,
            kind=kind,
            start=start & 0xFFFFFF,
            size=size,
            label=label,
            value=value,
        )
        self._next_watchpoint_id += 1
        self._watchpoints.append(wp)
        return wp

    def remove_watchpoint(self, wp_id: int) -> bool:
        """Remove the watchpoint with the given id. Returns True if removed."""
        for i, wp in enumerate(self._watchpoints):
            if wp.id == wp_id:
                del self._watchpoints[i]
                return True
        return False

    def clear_watchpoints(self) -> None:
        """Remove every watchpoint."""
        self._watchpoints.clear()

    def list_watchpoints(self) -> tuple[Watchpoint, ...]:
        """Return the watchpoint list as a tuple (in insertion order)."""
        return tuple(self._watchpoints)

    def step_until_frame_advance(
        self, *, batch: int = 1000, max_steps: int = 50_000,
    ) -> int:
        """Run in batches until `frame_state.frame_count` increases by one.

        Returns the total `executed_count` across the run. Stops early
        on a non-`count-reached` stop reason (e.g. blocked execution)
        or when `max_steps` is reached (whichever comes first).

        Intended for the "Step Frame" UI button — equivalent to "step
        the emulator until the next frame boundary." From scanline 0
        that's ~12,800 NOPs at flat 8 cycles/instr ; from mid-frame
        proportionally fewer.
        """
        if batch < 1:
            raise ValueError(f"batch must be >= 1; got {batch}")
        if max_steps < 1:
            raise ValueError(f"max_steps must be >= 1; got {max_steps}")
        starting_frame = self.frame_state.frame_count
        total_executed = 0
        remaining = max_steps
        while remaining > 0:
            this_batch = min(batch, remaining)
            result = self.step(this_batch)
            total_executed += result.executed_count
            remaining -= this_batch
            if self.frame_state.frame_count != starting_frame:
                return total_executed
            if result.stop_reason != "count-reached":
                # Execution blocked — don't loop forever.
                return total_executed
        return total_executed

    def render_lcd_ppm(self) -> bytes:
        """Render the current frame to a P6 PPM byte string (160×152)."""
        frame = render_frame(self.memory)
        return frame_to_ppm_bytes(frame)

    # ----- Inspector helpers (UI 0.3) -----

    def read_memory_range(
        self, address: int, count: int,
    ) -> list[int | None]:
        """Read `count` consecutive bytes from `address` via overlay + bus.

        Each entry is the byte value (0..255) or `None` when the
        address is unbacked / unmapped. The writable overlay shadows
        the read bus the same way the executor sees memory.
        """
        if count < 0:
            raise ValueError(f"count must be >= 0; got {count}")
        view = load_fetch_view(self.rom_path, frame_state=self.frame_state)
        out: list[int | None] = []
        for i in range(count):
            addr = (address + i) & 0xFFFFFF
            if addr in self.memory:
                out.append(self.memory[addr])
                continue
            result = view.bus.read_bytes(addr, size=1)
            if result.status == "ok" and result.data:
                out.append(result.data[0])
            else:
                out.append(None)
        return out

    def disassemble_around_pc(
        self, *, count: int = 12,
    ) -> list[tuple[int, DecodeResult]]:
        """Return `count` decoded instructions starting at the current PC.

        Walks forward via `decoded.next_sequential_pc` ; stops early
        if decode fails or hits a control-flow instruction without a
        next-sequential-pc. Each entry is `(pc, DecodeResult)`. The
        first entry's PC == `self.cpu.pc`.
        """
        if count < 1:
            raise ValueError(f"count must be >= 1; got {count}")
        view = load_fetch_view(self.rom_path, frame_state=self.frame_state)
        instructions: list[tuple[int, DecodeResult]] = []
        pc = self.cpu.pc
        for _ in range(count):
            decoded = decode_instruction_at(view.bus, pc)
            instructions.append((pc, decoded))
            if (
                decoded.status != "decoded"
                or decoded.next_sequential_pc is None
            ):
                break
            pc = decoded.next_sequential_pc
        return instructions

    def load_savestate(self, path: Path) -> None:
        """Replace the live state from a savestate file."""
        doc = load_savestate(Path(path), expected_rom_path=self.rom_path)
        self.cpu = doc.cpu
        self.memory = dict(doc.writable_overlay)
        self.frame_state = doc.frame_state
        self.irq_state = doc.irq_state
        # Counters are reset — the savestate is the new "zero point".
        # Cycle residue is not persisted (it's intra-session state) ;
        # treat the loaded frame_state as a clean scanline boundary.
        self._cycle_residue = 0
        self.total_cycles_consumed = 0
        self.last_stop_reason = "loaded-savestate"
        self.last_executed_count = 0
        self.last_irq_deliveries = 0

    def save_savestate(self, path: Path, *, note: str | None = None) -> None:
        """Persist the live state to disk as a v3 savestate."""
        payload = build_savestate_payload(
            rom_path=self.rom_path,
            rom_header=self.machine.header,
            cpu=self.cpu,
            writable_overlay=self.memory,
            note=note,
            frame_state=self.frame_state,
            irq_state=self.irq_state,
        )
        save_savestate(Path(path), payload)

    def snapshot(self) -> SessionSnapshot:
        """Return a frozen read-only view for UI panels."""
        return SessionSnapshot(
            cpu=self.cpu,
            memory=dict(self.memory),
            frame_state=self.frame_state,
            irq_state=self.irq_state,
            total_cycles_consumed=self.total_cycles_consumed,
            last_stop_reason=self.last_stop_reason,
            last_executed_count=self.last_executed_count,
            last_irq_deliveries=self.last_irq_deliveries,
        )


