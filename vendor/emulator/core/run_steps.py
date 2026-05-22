"""Minimal stateful run-steps helpers for NgpCraft Emulator."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from core.cpu import NgpcCpuState
from core.execute import (
    ExecutionResult,
    build_execute_next,
    seed_cpu_state_for_execution,
    try_deliver_pending_irq,
)
from core.fetch import NgpcFetchView, load_fetch_view

if TYPE_CHECKING:
    from core.frame_timing import FrameState, IrqState


@dataclass(frozen=True)
class RunStepsRecord:
    """One stateful execute-next record inside a bounded run-steps session."""

    index: int
    execution: ExecutionResult


@dataclass(frozen=True)
class RunStepsResult:
    """Current minimal bounded stateful run result."""

    start_pc: int
    requested_count: int
    emitted_count: int
    executed_count: int
    stop_reason: str
    final_cpu: NgpcCpuState
    final_memory: dict[int, int]
    records: tuple[RunStepsRecord, ...]
    note: str
    final_irq_state: "IrqState | None" = None
    irq_deliveries: int = 0
    # M3 Phase 3.2.3a: sum of cycles consumed across all executed
    # instructions + IRQ deliveries during this run. Currently every
    # opcode contributes `ESTIMATED_CYCLES_PER_INSTRUCTION` (flat 8)
    # and each IRQ delivery contributes `IRQ_DELIVERY_CYCLES` (13).
    # Phase 3.2.3b will replace the flat estimate with the TLCS-900/H
    # per-opcode cycle table.
    total_cycles_consumed: int = 0


def build_run_steps(
    view: NgpcFetchView,
    start_pc: int | None = None,
    count: int = 8,
    cpu_state: NgpcCpuState | None = None,
    memory_bytes: dict[int, int] | None = None,
    irq_state: "IrqState | None" = None,
) -> RunStepsResult:
    """Execute up to N instructions while carrying CPU and memory state forward.

    `irq_state` (M3 Phase 3.2.2b): when provided, the loop samples the
    IRQ controller between instructions via `try_deliver_pending_irq`.
    A deliverable pending IRQ (today: VBlank only, level 4) consumes
    one step of the budget — PC + SR are pushed, PC jumps to the
    vector, iff_level is raised, and the bit is cleared. When omitted
    or None, the legacy behavior (no IRQ delivery) is preserved.
    """
    if count <= 0:
        raise ValueError("count must be >= 1")

    current_cpu = view.machine.cpu if cpu_state is None else cpu_state
    if start_pc is not None:
        current_cpu = replace(current_cpu, pc=start_pc)
    current_memory = {} if memory_bytes is None else dict(memory_bytes)
    current_irq_state = irq_state
    actual_start_pc = current_cpu.pc

    records: list[RunStepsRecord] = []
    executed_count = 0
    irq_deliveries = 0
    stop_reason = "count-reached"

    irq_blocked = False
    total_cycles_consumed = 0
    for index in range(count):
        if current_irq_state is not None:
            delivery = try_deliver_pending_irq(
                view=view,
                cpu=current_cpu,
                memory=current_memory,
                irq_state=current_irq_state,
            )
            if delivery.delivered:
                current_cpu = delivery.after_cpu
                current_memory = delivery.after_memory
                current_irq_state = delivery.after_irq_state
                irq_deliveries += 1
                total_cycles_consumed += delivery.cycles_consumed
            elif delivery.blocked_reason is not None:
                stop_reason = f"stopped-on-{delivery.blocked_reason}"
                irq_blocked = True
                break

        execution = build_execute_next(
            view=view,
            cpu_state=current_cpu,
            memory_bytes=current_memory,
        )
        records.append(RunStepsRecord(index=index, execution=execution))

        if execution.status != "executed" or execution.after_cpu is None or execution.after_memory is None:
            stop_reason = f"stopped-on-{execution.status}"
            break

        executed_count += 1
        total_cycles_consumed += execution.cycles_consumed
        current_cpu = execution.after_cpu
        current_memory = execution.after_memory

    return RunStepsResult(
        start_pc=actual_start_pc,
        requested_count=count,
        emitted_count=len(records),
        executed_count=executed_count,
        stop_reason=stop_reason,
        final_cpu=current_cpu,
        final_memory=current_memory,
        records=tuple(records),
        note=(
            "This helper chains the current execute-next subset statefully within one command "
            "invocation. CPU state and the minimal writable memory overlay are carried forward "
            "between instructions until the step budget is exhausted or one instruction stops "
            "honest execution."
        ),
        final_irq_state=current_irq_state,
        irq_deliveries=irq_deliveries,
        total_cycles_consumed=total_cycles_consumed,
    )


@dataclass(frozen=True)
class RunUntilResult:
    """Result of a real run-until-address execution."""

    start_pc: int
    target_pc: int
    emitted_count: int
    executed_count: int
    stop_reason: str
    final_cpu: NgpcCpuState
    final_memory: dict[int, int]
    last_record: RunStepsRecord | None
    note: str
    final_irq_state: "IrqState | None" = None
    irq_deliveries: int = 0
    total_cycles_consumed: int = 0


def build_run_until(
    view: NgpcFetchView,
    target_pc: int,
    start_pc: int | None = None,
    cpu_state: NgpcCpuState | None = None,
    memory_bytes: dict[int, int] | None = None,
    max_steps: int = 1_000_000,
    auto_tick_address: int | None = None,
    auto_tick_period: int = 256,
    irq_state: "IrqState | None" = None,
) -> RunUntilResult:
    """Execute instructions until PC reaches target_pc, a blocker is hit, or max_steps is exhausted.

    Unlike build_run_steps, this function does not record every step — it only keeps the
    last executed record to bound memory use for long loops.  The stop reason distinguishes:
      - target-reached: PC == target_pc before executing the instruction there
      - stopped-on-<status>: an instruction could not be executed honestly
      - step-budget-exhausted: max_steps reached without hitting target

    Optional `auto_tick_address`: address of a byte counter in writable memory
    that gets incremented every `auto_tick_period` executed instructions.
    Use case: simulate a vblank/timer counter ISR-incremented in real HW so
    that code spinning on it (e.g. `_ngpc_vsync`) eventually exits without
    needing real IRQ modeling. This is NOT hardware-faithful execution — it
    is an explicit opt-in shortcut that must be flagged in any analysis
    output that uses it. See HARDWARE_COMPAT_POLICY.md §4.3 (non-reference
    modes).
    """
    if max_steps <= 0:
        raise ValueError("max_steps must be >= 1")
    if auto_tick_period <= 0:
        raise ValueError("auto_tick_period must be >= 1")

    current_cpu = view.machine.cpu if cpu_state is None else cpu_state
    if start_pc is not None:
        current_cpu = replace(current_cpu, pc=start_pc)
    current_memory = {} if memory_bytes is None else dict(memory_bytes)
    current_irq_state = irq_state

    actual_start_pc = current_cpu.pc
    executed_count = 0
    irq_deliveries = 0
    total_cycles_consumed = 0
    last_record: RunStepsRecord | None = None
    stop_reason = "step-budget-exhausted"

    for index in range(max_steps):
        if current_cpu.pc == target_pc:
            stop_reason = "target-reached"
            break

        if current_irq_state is not None:
            delivery = try_deliver_pending_irq(
                view=view,
                cpu=current_cpu,
                memory=current_memory,
                irq_state=current_irq_state,
            )
            if delivery.delivered:
                current_cpu = delivery.after_cpu
                current_memory = delivery.after_memory
                current_irq_state = delivery.after_irq_state
                irq_deliveries += 1
                total_cycles_consumed += delivery.cycles_consumed
                # IRQ delivery happens before the instruction in the same
                # iteration. Re-check target_pc since the vector jump may
                # have landed exactly there.
                if current_cpu.pc == target_pc:
                    stop_reason = "target-reached"
                    break
            elif delivery.blocked_reason is not None:
                stop_reason = f"stopped-on-{delivery.blocked_reason}"
                break

        execution = build_execute_next(
            view=view,
            cpu_state=current_cpu,
            memory_bytes=current_memory,
        )
        last_record = RunStepsRecord(index=index, execution=execution)

        if execution.status != "executed" or execution.after_cpu is None or execution.after_memory is None:
            stop_reason = f"stopped-on-{execution.status}"
            break

        executed_count += 1
        total_cycles_consumed += execution.cycles_consumed
        current_cpu = execution.after_cpu
        current_memory = execution.after_memory

        if auto_tick_address is not None and executed_count % auto_tick_period == 0:
            tick_addr = auto_tick_address & 0xFFFFFF
            prev = current_memory.get(tick_addr, 0)
            current_memory[tick_addr] = (prev + 1) & 0xFF

    return RunUntilResult(
        start_pc=actual_start_pc,
        target_pc=target_pc,
        emitted_count=executed_count + (1 if last_record is not None and stop_reason.startswith("stopped-on-") else 0),
        executed_count=executed_count,
        stop_reason=stop_reason,
        final_cpu=current_cpu,
        final_memory=current_memory,
        last_record=last_record,
        note=(
            "This helper runs the current execute-next subset forward until a target PC is "
            "reached or execution is blocked.  Only the final CPU state and last record are "
            "kept — intermediate steps are not retained.  This is not a full debugger run; "
            "it shares all current executor limits and does not model interrupts or full I/O."
        ),
        final_irq_state=current_irq_state,
        irq_deliveries=irq_deliveries,
        total_cycles_consumed=total_cycles_consumed,
    )


def load_run_until(
    path: str | Path,
    target_pc: int,
    start_pc: int | None = None,
    seed_xsp: int | None = None,
    seed_registers: dict[str, int] | None = None,
    max_steps: int = 1_000_000,
    initial_cpu_state: NgpcCpuState | None = None,
    initial_memory_bytes: dict[int, int] | None = None,
    auto_tick_address: int | None = None,
    auto_tick_period: int = 256,
    initial_frame_state: "FrameState | None" = None,
    initial_irq_state: "IrqState | None" = None,
) -> RunUntilResult:
    """Load a ROM and run until target_pc.

    Seeding rules:
    - If `initial_cpu_state` is given, it overrides the bootstrap CPU state.
      `seed_xsp` and `seed_registers` may still be applied on top for
      targeted overrides.
    - If `initial_memory_bytes` is given, it seeds the writable runtime
      overlay before the first step (typically from a loaded savestate).
    - If `initial_frame_state` is given (M3 Phase 3.1b), the executor's
      read bus exposes the matching `RAS.V` (`0x008009`) and BLNK bit
      (`0x008010` bit 6) so CPU reads of those addresses see HW-faithful
      values. Defaults to the documented HW reset (scanline 0, BLNK=0).
    - If `initial_irq_state` is given (M3 Phase 3.2.2b), the executor
      samples the IRQ controller between instructions and may deliver
      a pending VBlank IRQ (push PC + SR, jump to vector 0x006FCC,
      raise iff_level, clear bit). Defaults to no IRQ sampling.
    """
    view = load_fetch_view(path, frame_state=initial_frame_state)
    cpu_state = view.machine.cpu if initial_cpu_state is None else initial_cpu_state
    if seed_xsp is not None or seed_registers:
        cpu_state = seed_cpu_state_for_execution(
            cpu_state,
            register_values=seed_registers,
            seed_xsp=seed_xsp,
        )
    return build_run_until(
        view=view,
        target_pc=target_pc,
        start_pc=start_pc,
        cpu_state=cpu_state,
        memory_bytes=initial_memory_bytes,
        max_steps=max_steps,
        auto_tick_address=auto_tick_address,
        auto_tick_period=auto_tick_period,
        irq_state=initial_irq_state,
    )


def load_run_steps(
    path: str | Path,
    start_pc: int | None = None,
    count: int = 8,
    seed_xsp: int | None = None,
    seed_registers: dict[str, int] | None = None,
    initial_cpu_state: NgpcCpuState | None = None,
    initial_memory_bytes: dict[int, int] | None = None,
    initial_frame_state: "FrameState | None" = None,
    initial_irq_state: "IrqState | None" = None,
    bios_path: str | Path | None = None,
) -> RunStepsResult:
    """Load a ROM and execute up to N instructions with carried state.

    Seeding rules:
    - If `initial_cpu_state` is given, it overrides the bootstrap CPU state.
      `seed_xsp` and `seed_registers` may still be applied on top for
      targeted overrides.
    - If `initial_memory_bytes` is given, it seeds the writable runtime
      overlay before the first step (typically from a loaded savestate).
    - If `initial_frame_state` is given (M3 Phase 3.1b), the executor's
      read bus exposes the matching `RAS.V` + BLNK bit so CPU reads of
      `0x008009` / `0x008010` see HW-faithful values. Defaults to the
      documented HW reset.
    - If `initial_irq_state` is given (M3 Phase 3.2.2b), the executor
      samples the IRQ controller between instructions and may deliver
      a pending VBlank IRQ. Defaults to no IRQ sampling.
    """
    view = load_fetch_view(path, frame_state=initial_frame_state, bios_path=bios_path)
    cpu_state = view.machine.cpu if initial_cpu_state is None else initial_cpu_state
    if seed_xsp is not None or seed_registers:
        cpu_state = seed_cpu_state_for_execution(
            cpu_state,
            register_values=seed_registers,
            seed_xsp=seed_xsp,
        )
    return build_run_steps(
        view=view,
        start_pc=start_pc,
        count=count,
        cpu_state=cpu_state,
        memory_bytes=initial_memory_bytes,
        irq_state=initial_irq_state,
    )
