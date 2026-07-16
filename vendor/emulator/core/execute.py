"""Minimal real execute-next helpers for NgpCraft Emulator."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from core.cpu import (
    BankedByteRegisters,
    NgpcCpuState,
    StatusFlags,
    decode_sr_to_fields,
    encode_sr_from_state,
)
from core.decode import (
    CC,
    C7_REGISTER_NAMES,
    DecodeResult,
    c7_current_bank_slice,
    decode_instruction_at,
)
from core.fetch import NgpcFetchView, load_fetch_view
from core.frame_timing import ESTIMATED_CYCLES_PER_INSTRUCTION
from core.quirks import KnownQuirkMatch, match_known_silicon_broken


# Per-instruction cycle cost placeholder (Phase 3.2.3a).
#
# Until the per-opcode TLCS-900/H cycle table lands in Phase 3.2.3b,
# every `ExecutionResult` uses `ESTIMATED_CYCLES_PER_INSTRUCTION` (flat 8)
# as its `cycles_consumed`. Phase 3.2.3a wires the field through the
# result chain so the CLI boundary can accumulate cycles directly
# instead of computing `executed_count * 8`. This is the architectural
# prep work — populating real cycle counts per opcode is the next sub-phase.
#
# IRQ entry cost (Toshiba TLCS-900/H spec): ~13 cycles for push PC + SR
# and load vector. We use 13 as the canonical IRQ-delivery cost for the
# `IrqDeliveryResult.cycles_consumed` field.
IRQ_DELIVERY_CYCLES = 13


R8 = ("W", "A", "B", "C", "D", "E", "H", "L")
R16 = ("WA", "BC", "DE", "HL", "IX", "IY", "IZ", "SP")
R32 = ("XWA", "XBC", "XDE", "XHL", "XIX", "XIY", "XIZ", "XSP")
REG32_FIELDS = ("xwa", "xbc", "xde", "xhl", "xix", "xiy", "xiz", "xsp")
SEEDABLE_REGISTERS = dict(zip(R32, REG32_FIELDS))
SEEDED_BANKED_REGISTERS = dict(zip(R32[:4], REG32_FIELDS[:4]))
READ_ONLY_REGION_KINDS = {"rom", "rom-gap", "bios"}
_BANKED_CORE_FIELDS = REG32_FIELDS[:4]


@dataclass(frozen=True)
class MemoryWrite:
    """One contiguous memory write emitted by the current execution subset."""

    address: int
    data: bytes
    note: str


@dataclass(frozen=True)
class MemoryRead:
    """One contiguous memory read observed by the current execution subset.

    Surfaced for read-watchpoint matching. Only executors that opt in
    currently populate this; the default empty tuple is correct for any
    executor that has not been instrumented yet.
    """

    address: int
    data: bytes
    note: str


@dataclass(frozen=True)
class ExecutionResult:
    """Result of one real execution attempt from the current minimal subset."""

    before_cpu: NgpcCpuState
    after_cpu: NgpcCpuState | None
    decode: DecodeResult
    status: str
    written_registers: tuple[str, ...]
    memory_writes: tuple[MemoryWrite, ...]
    after_memory: dict[int, int] | None
    note: str
    matched_quirk: KnownQuirkMatch | None = None
    memory_reads: tuple[MemoryRead, ...] = ()
    # M3 Phase 3.2.3a: per-instruction cycle cost. Defaults to the flat
    # `ESTIMATED_CYCLES_PER_INSTRUCTION` placeholder used by every opcode
    # today. Phase 3.2.3b will set this per-opcode from the TLCS-900/H
    # cycle table. Blocked executions still carry the default — they
    # didn't advance state, but the run loop only sums this when
    # `status == "executed"` so blocked steps don't contribute.
    cycles_consumed: int = ESTIMATED_CYCLES_PER_INSTRUCTION


def build_execute_next(
    view: NgpcFetchView,
    start_pc: int | None = None,
    cpu_state: NgpcCpuState | None = None,
    memory_bytes: dict[int, int] | None = None,
) -> ExecutionResult:
    """Execute one instruction from the current narrow honest subset."""
    before_cpu = view.machine.cpu if cpu_state is None else cpu_state
    if start_pc is not None:
        before_cpu = replace(before_cpu, pc=start_pc)
    before_memory = {} if memory_bytes is None else dict(memory_bytes)

    _STEP_READS.clear()
    result = _dispatch_execute_next(view, before_cpu, before_memory)
    if _STEP_READS and not result.memory_reads:
        result = replace(result, memory_reads=tuple(_STEP_READS))
    elif _STEP_READS:
        # An executor already populated memory_reads explicitly (POP SR).
        # Trust the executor's own bookkeeping in that case.
        pass
    return result


def _dispatch_execute_next(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
) -> ExecutionResult:
    decoded = decode_instruction_at(view.bus, before_cpu.pc)
    if decoded.status != "decoded":
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=decoded.status,
            note=(
                "Execution could not begin because the current instruction could not be "
                "decoded at the requested address."
            ),
        )

    silicon_broken_result = _try_stop_known_silicon_broken(
        before_cpu=before_cpu,
        decoded=decoded,
    )
    if silicon_broken_result is not None:
        return silicon_broken_result

    if decoded.mnemonic == "nop":
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            note=(
                "Executed NOP from the current real execution subset. Only PC advanced to the "
                "next sequential address."
            ),
        )

    load_result = _try_execute_load_immediate(before_cpu, before_memory, decoded)
    if load_result is not None:
        return load_result

    lda_result = _try_execute_lda_absolute(before_cpu, before_memory, decoded)
    if lda_result is not None:
        return lda_result

    b0_memory_result = _try_execute_b0_memory(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if b0_memory_result is not None:
        return b0_memory_result

    abs8_long_memory_result = _try_execute_abs8_long_memory(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if abs8_long_memory_result is not None:
        return abs8_long_memory_result

    cpu_io_result = _try_execute_cpu_io_store(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if cpu_io_result is not None:
        return cpu_io_result

    abs16_word_memory_result = _try_execute_abs16_word_memory(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if abs16_word_memory_result is not None:
        return abs16_word_memory_result

    abs24_word_memory_result = _try_execute_abs24_word_memory(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if abs24_word_memory_result is not None:
        return abs24_word_memory_result

    abs16_byte_memory_result = _try_execute_abs16_byte_memory(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if abs16_byte_memory_result is not None:
        return abs16_byte_memory_result

    prefixed_ld_result = _try_execute_prefixed_register_ld(before_cpu, before_memory, decoded)
    if prefixed_ld_result is not None:
        return prefixed_ld_result

    prefixed_compare_result = _try_execute_prefixed_compare(before_cpu, before_memory, decoded)
    if prefixed_compare_result is not None:
        return prefixed_compare_result

    c7_ext_result = _try_execute_c7_extended_register(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if c7_ext_result is not None:
        return c7_ext_result

    swi_result = _try_execute_swi(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if swi_result is not None:
        return swi_result

    ei_di_result = _try_execute_ei_di(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if ei_di_result is not None:
        return ei_di_result

    ldf_result = _try_execute_ldf(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if ldf_result is not None:
        return ldf_result

    push_pop_sr_result = _try_execute_push_pop_sr(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if push_pop_sr_result is not None:
        return push_pop_sr_result

    reti_result = _try_execute_reti(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if reti_result is not None:
        return reti_result

    arithmetic_result = _try_execute_prefixed_inc_dec(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if arithmetic_result is not None:
        return arithmetic_result

    alu_reg_result = _try_execute_prefixed_alu_register(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if alu_reg_result is not None:
        return alu_reg_result

    link_unlk_result = _try_execute_link_unlk(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if link_unlk_result is not None:
        return link_unlk_result

    shift_imm_result = _try_execute_prefixed_shift_imm(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if shift_imm_result is not None:
        return shift_imm_result

    cp_imm3_result = _try_execute_prefixed_cp_imm3(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if cp_imm3_result is not None:
        return cp_imm3_result

    bit_mutation_result = _try_execute_prefixed_bit_mutation(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if bit_mutation_result is not None:
        return bit_mutation_result

    bit_test_result = _try_execute_prefixed_bit_test(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if bit_test_result is not None:
        return bit_test_result

    alu_imm_result = _try_execute_prefixed_alu_immediate(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if alu_imm_result is not None:
        return alu_imm_result

    divide_imm_result = _try_execute_prefixed_divide_immediate(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if divide_imm_result is not None:
        return divide_imm_result

    multiply_imm_result = _try_execute_prefixed_multiply_immediate(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if multiply_imm_result is not None:
        return multiply_imm_result

    ext_result = _try_execute_prefixed_ext(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if ext_result is not None:
        return ext_result

    reg_indirect_load_result = _try_execute_reg_indirect_load(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if reg_indirect_load_result is not None:
        return reg_indirect_load_result

    reg_indirect_word_result = _try_execute_reg_indirect_word(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if reg_indirect_word_result is not None:
        return reg_indirect_word_result

    reg_indirect_store_result = _try_execute_reg_indirect_store(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if reg_indirect_store_result is not None:
        return reg_indirect_store_result

    indexed_store_result = _try_execute_indexed_store(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_store_result is not None:
        return indexed_store_result

    indexed_imm_store_result = _try_execute_indexed_imm_store(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_imm_store_result is not None:
        return indexed_imm_store_result

    indexed_load_result = _try_execute_indexed_load(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_load_result is not None:
        return indexed_load_result

    secondary_indexed_load_result = _try_execute_secondary_indexed_load(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if secondary_indexed_load_result is not None:
        return secondary_indexed_load_result

    secondary_indexed_jump_result = _try_execute_secondary_indexed_jump(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if secondary_indexed_jump_result is not None:
        return secondary_indexed_jump_result

    indexed_muldiv_result = _try_execute_indexed_word_muldiv(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_muldiv_result is not None:
        return indexed_muldiv_result

    indexed_push_result = _try_execute_indexed_push(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_push_result is not None:
        return indexed_push_result

    post_increment_result = _try_execute_post_increment_byte(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if post_increment_result is not None:
        return post_increment_result

    indexed_word_misc_result = _try_execute_indexed_word_misc(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_word_misc_result is not None:
        return indexed_word_misc_result

    indexed_long_misc_result = _try_execute_indexed_long_misc(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_long_misc_result is not None:
        return indexed_long_misc_result

    indexed_byte_alu_result = _try_execute_indexed_byte_alu(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_byte_alu_result is not None:
        return indexed_byte_alu_result

    indexed_word_alu_result = _try_execute_indexed_word_alu(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_word_alu_result is not None:
        return indexed_word_alu_result

    indexed_long_alu_result = _try_execute_indexed_long_alu(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_long_alu_result is not None:
        return indexed_long_alu_result

    indexed_rmw_add_result = _try_execute_indexed_rmw_add(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_rmw_add_result is not None:
        return indexed_rmw_add_result

    indexed_cp_imm_result = _try_execute_indexed_cp_immediate(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_cp_imm_result is not None:
        return indexed_cp_imm_result

    indexed_compare_result = _try_execute_indexed_compare(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if indexed_compare_result is not None:
        return indexed_compare_result

    stack_result = _try_execute_stack_or_call(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if stack_result is not None:
        return stack_result

    if decoded.control_flow_kind == "jump" and decoded.direct_target is not None:
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.direct_target,
            reg_updates=None,
            note=(
                "Executed a direct unconditional jump from the current real execution subset. "
                "PC now points to the decoded direct target."
            ),
        )

    conditional_branch_result = _try_execute_conditional_branch(before_cpu, before_memory, decoded)
    if conditional_branch_result is not None:
        return conditional_branch_result

    ret_cond_result = _try_execute_ret_conditional(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
    )
    if ret_cond_result is not None:
        return ret_cond_result

    if decoded.control_flow_kind in {"conditional-return", "conditional-branch"}:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-state-required",
            note=(
                "This branch depends on runtime flag state, which the current minimal execution "
                "subset does not model well enough to choose honestly."
            ),
        )

    if decoded.control_flow_kind in {"interrupt", "halt"}:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="unmodeled-side-effects",
            note=(
                "This instruction decoded successfully but requires interrupt, halt or other "
                "side effects that are not modeled in the current real execution subset."
            ),
        )

    return _blocked_result(
        before_cpu=before_cpu,
        decoded=decoded,
        status="unsupported-decoded-instruction",
        note=(
            "The instruction decoded successfully, but its state effects are not implemented in "
            "the current real execution subset yet."
        ),
    )


def load_execute_next(
    path: str | Path,
    start_pc: int | None = None,
    seed_xsp: int | None = None,
    seed_registers: dict[str, int] | None = None,
    bios_path: str | Path | None = None,
) -> ExecutionResult:
    """Load a ROM and execute one instruction from the current minimal subset."""
    view = load_fetch_view(path, bios_path=bios_path)
    cpu_state = view.machine.cpu
    if seed_xsp is not None or seed_registers:
        cpu_state = seed_cpu_state_for_execution(
            cpu_state,
            register_values=seed_registers,
            seed_xsp=seed_xsp,
        )
    return build_execute_next(view=view, start_pc=start_pc, cpu_state=cpu_state)


def _try_execute_load_immediate(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None:
        return None

    if 0x20 <= raw[0] <= 0x27 and len(raw) == 2:
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="byte",
            register_index=raw[0] & 0x07,
            value=raw[1],
        )

    if 0x30 <= raw[0] <= 0x37 and len(raw) == 3:
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="word",
            register_index=raw[0] & 0x07,
            value=int.from_bytes(raw[1:3], "little"),
        )

    if 0x40 <= raw[0] <= 0x47 and len(raw) == 5:
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="long",
            register_index=raw[0] & 0x07,
            value=int.from_bytes(raw[1:5], "little"),
        )

    if raw[0] in range(0xC8, 0xD0) and len(raw) == 3 and raw[1] == 0x03:
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="byte",
            register_index=raw[0] & 0x07,
            value=raw[2],
        )

    # D0..D7 and D8..DF are both WORD register-direct prefixes (16-bit imm,
    # 4-byte total). HW-confirmed 2026-07-03: D8..DF is word, NOT long.
    if raw[0] in range(0xD0, 0xE0) and len(raw) == 4 and raw[1] == 0x03:
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="word",
            register_index=raw[0] & 0x07,
            value=int.from_bytes(raw[2:4], "little"),
        )

    if raw[0] in range(0xE8, 0xF0) and len(raw) == 6 and raw[1] == 0x03:
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="long",
            register_index=raw[0] & 0x07,
            value=int.from_bytes(raw[2:6], "little"),
        )

    # ld r, #3 — compact 2-byte small-immediate load (catalog: C8+zz+r : A8+#3).
    # The 3-bit immediate value (0..7) is embedded in the lower bits of the second byte.
    # C8..CF = byte register, D0..D7 = word register, D8..DF = word register
    # (16-bit, HW-confirmed 2026-07-03 — NOT long), E8..EF = long register.
    if len(raw) == 2 and 0xA8 <= raw[1] <= 0xAF:
        info = _prefixed_register_execute_info(raw[0])
        if info is not None:
            size_kind, register_index = info
            return _execute_register_immediate(
                before_cpu=before_cpu,
                before_memory=before_memory,
                decoded=decoded,
                size_kind=size_kind,
                register_index=register_index,
                value=raw[1] & 0x07,
                note=(
                    "Executed prefixed small-immediate load (ld r, #3) from the current real "
                    "execution subset. The 3-bit value embedded in the opcode was written to "
                    "the destination register."
                ),
            )

    return None


def _try_execute_lda_absolute(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 5:
        return None

    if raw[0] != 0xF2 or not (0x30 <= raw[4] <= 0x37):
        return None

    target_address = int.from_bytes(raw[1:4], "little") & 0xFFFFFF
    return _execute_register_immediate(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
        size_kind="long",
        register_index=raw[4] & 0x07,
        value=target_address,
        note=(
            "Executed LDA absolute from the current real execution subset. The destination "
            "32-bit register now holds the decoded effective address value, not the memory "
            "contents at that address."
        ),
    )


def _try_execute_b0_memory(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    def execute_absolute_bit_operation(
        *,
        target_address: int,
        op_byte: int,
        width_label: str,
    ) -> ExecutionResult:
        source_data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if source_data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    f"This {width_label} bit-manipulation instruction needs a readable source byte, "
                    "but neither the writable runtime overlay nor the current read bus can provide it."
                ),
            )

        old_value = source_data[0]
        bit_index = op_byte & 0x07
        bit_mask = 1 << bit_index
        op_base = op_byte & 0xF8

        if op_base == 0xC8:
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates={
                    "zf": (old_value & bit_mask) == 0,
                    "hf": True,
                    "nf": False,
                },
                note=(
                    f"Executed {width_label} BIT bit test from the current real execution subset. "
                    f"Bit {bit_index} of mem8(0x{target_address:06X}) determined Z, while H=1 and N=0."
                ),
            )

        if op_base == 0xA8:
            new_value = old_value | bit_mask
            flags_updates = {"zf": (old_value & bit_mask) == 0}
            action_name = "TSET"
        elif op_base == 0xB0:
            new_value = old_value & (~bit_mask & 0xFF)
            flags_updates = None
            action_name = "RES"
        elif op_base == 0xB8:
            new_value = old_value | bit_mask
            flags_updates = None
            action_name = "SET"
        elif op_base == 0xC0:
            new_value = old_value ^ bit_mask
            flags_updates = None
            action_name = "CHG"
        else:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="not-yet-modeled",
                note=f"{width_label} bit opcode 0x{op_byte:02X} is not modeled yet.",
            )

        write_status, write_note = _check_writable_range(view, target_address, 1)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=bytes((new_value & 0xFF,)),
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"{width_label} {action_name} destination was unmapped or read-only; write "
                    "silently discarded (open-bus behavior - execution continues)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[target_address] = new_value & 0xFF
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=bytes((new_value & 0xFF,)),
                    note=f"Writable runtime overlay updated by {width_label} {action_name} execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed {width_label} {action_name} bit operation: mem8(0x{target_address:06X}) "
                f"0x{old_value:02X} -> 0x{new_value & 0xFF:02X}."
            ),
        )

    def execute_absolute_cf_operation(
        *,
        target_address: int,
        op_byte: int,
        width_label: str,
    ) -> ExecutionResult:
        source_data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if source_data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    f"This {width_label} carry-flag memory instruction needs a readable source byte, "
                    "but neither the writable runtime overlay nor the current read bus can provide it."
                ),
            )

        mem_value = source_data[0]
        if 0x28 <= op_byte <= 0x2C:
            a_name, a_value = _extract_register_value(before_cpu, "byte", 1)
            if a_value is None:
                return _blocked_result(
                    before_cpu=before_cpu,
                    decoded=decoded,
                    status="requires-known-full-register",
                    note=(
                        f"{a_name} must be known before this {width_label} carry-flag memory "
                        "instruction can derive its dynamic bit index."
                    ),
                )
            bit_index = a_value & 0x0F
        else:
            bit_index = op_byte & 0x07

        if bit_index >= 8:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="silicon-undefined",
                note=(
                    f"{width_label} carry-flag memory bit index {bit_index} is undefined for a "
                    "byte operand on TLCS-900/H."
                ),
            )

        bit_value = (mem_value >> bit_index) & 1
        bit_mask = 1 << bit_index

        if (
            op_byte in (0x28, 0x29, 0x2A, 0x2C)
            or (op_byte & 0xF8) in (0x80, 0x88, 0x90, 0xA0)
        ) and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"This {width_label} carry-flag memory instruction needs the carry flag "
                    "known in the current CPU state."
                ),
            )

        if op_byte in (0x2B,) or (op_byte & 0xF8) == 0x98:
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates={"cf": bool(bit_value)},
                note=(
                    f"Executed {width_label} LDCF from the current real execution subset. "
                    f"CF <- bit {bit_index} of mem8(0x{target_address:06X})."
                ),
            )

        carry = int(before_cpu.flags.cf)
        if op_byte in (0x28,) or (op_byte & 0xF8) == 0x80:
            new_carry = bool(carry & bit_value)
            action_name = "ANDCF"
        elif op_byte in (0x29,) or (op_byte & 0xF8) == 0x88:
            new_carry = bool(carry | bit_value)
            action_name = "ORCF"
        elif op_byte in (0x2A,) or (op_byte & 0xF8) == 0x90:
            new_carry = bool(carry ^ bit_value)
            action_name = "XORCF"
        else:
            new_value = (mem_value & (~bit_mask & 0xFF)) | (bit_mask if carry else 0)
            return _execute_absolute_store(
                view=view,
                before_cpu=before_cpu,
                before_memory=before_memory,
                decoded=decoded,
                target_address=target_address,
                data=bytes((new_value & 0xFF,)),
                note=(
                    f"Executed {width_label} STCF from the current real execution subset. "
                    f"Bit {bit_index} was written from CF into mem8(0x{target_address:06X})."
                ),
                memory_note=f"Writable runtime overlay updated by {width_label} STCF execution.",
            )

        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates={"cf": new_carry},
            note=(
                f"Executed {width_label} {action_name} from the current real execution subset. "
                f"CF updated from prior C={carry} and bit {bit_index} of mem8(0x{target_address:06X})."
            ),
        )

    raw = decoded.raw_bytes
    if raw is None or raw[0] not in (0xC2, 0xF1, 0xF2, 0xF3):
        return None

    if raw[0] == 0xC2 and len(raw) == 5 and 0x20 <= raw[4] <= 0x27:
        # ld R8, (abs24): read one byte from abs24 address, load into R8 register
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs24 byte load needs a readable source byte, but neither the "
                    "writable runtime overlay nor the current read bus can provide it."
                ),
            )
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="byte",
            register_index=raw[4] & 0x07,
            value=data[0],
            note=(
                "Executed abs24 byte load from the current real execution subset. The source "
                "byte was read from the writable runtime overlay or the current read bus."
            ),
        )

    if raw[0] == 0xC2 and len(raw) == 5 and 0x40 <= raw[4] <= 0x47:
        # ld (abs24), R8: store one byte from R8 register to abs24 address
        source_register_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="byte",
            register_index=raw[4] & 0x07,
        )
        if source_value is None:
            owner_name = R32[(raw[4] & 0x07) // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_register_name} cannot be stored honestly until {owner_name} is "
                    "already known in the current CPU state."
                ),
            )
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=bytes((source_value & 0xFF,)),
            note=(
                "Executed abs24 byte store from the current real execution subset. The "
                "source register byte was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by abs24 byte store execution.",
        )

    if raw[0] == 0xC2 and len(raw) == 5 and 0x60 <= raw[4] <= 0x6F:
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        op = raw[4]
        count_code = op & 0x07
        count = 8 if count_code == 0 else count_code
        is_dec = op >= 0x68
        source = _read_runtime_bytes(view, before_memory, target_address, 1)
        if source is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs24 byte inc/dec needs a readable source byte, but neither the "
                    "writable runtime overlay nor the current read bus can provide it."
                ),
            )
        old_value = source[0]
        if is_dec:
            new_value = (old_value - count) & 0xFF
            mnemonic = "decb"
            flags_updates = dict(_compute_subtract_flags("byte", old_value, count))
        else:
            new_value = (old_value + count) & 0xFF
            mnemonic = "incb"
            flags_updates = dict(_compute_add_flags("byte", old_value, count))
        flags_updates.pop("cf", None)

        write_status, write_note = _check_writable_range(view, target_address, 1)
        if write_status is not None and write_status != "write-discarded":
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        if write_status == "write-discarded":
            after_memory = dict(before_memory)
            mem_write = MemoryWrite(
                address=target_address,
                data=bytes((new_value,)),
                note=f"[DISCARDED] {write_note}",
            )
        else:
            after_memory = dict(before_memory)
            after_memory[target_address] = new_value
            mem_write = MemoryWrite(
                address=target_address,
                data=bytes((new_value,)),
                note="Writable runtime overlay updated by abs24 byte inc/dec.",
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(mem_write,),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed {mnemonic} {count}, (0x{target_address:06X}): mem8 0x{old_value:02X} "
                f"-> 0x{new_value:02X}. CF preserved."
            ),
        )

    if raw[0] == 0xC2 and len(raw) == 5 and 0x80 <= raw[4] <= 0xFF:
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        mem_data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if mem_data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs24 byte ALU needs a readable source byte, but neither the "
                    "writable runtime overlay nor the current read bus can provide it."
                ),
            )

        sub_op = raw[4]
        operation = {
            0x8: "add",
            0x9: "adc",
            0xA: "sub",
            0xB: "sbc",
            0xC: "and",
            0xD: "xor",
            0xE: "or",
            0xF: "cp",
        }.get(sub_op >> 4)
        if operation is None:
            return None
        store_to_memory = bool(sub_op & 0x08)
        register_index = sub_op & 0x07
        register_name, register_value = _extract_register_value(before_cpu, "byte", register_index)
        if register_value is None:
            owner_name = R32[register_index // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{register_name} cannot be used honestly until {owner_name} is already "
                    "known in the current CPU state."
                ),
            )
        if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{operation.upper()} on abs24 byte memory requires a known carry flag, "
                    "which is not modeled in the current CPU state."
                ),
            )

        mem_value = mem_data[0]
        carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
        register_is_left = not store_to_memory
        if register_is_left:
            left_value = register_value
            right_value = mem_value
        else:
            left_value = mem_value
            right_value = register_value

        if operation == "add":
            result = (left_value + right_value) & 0xFF
            flags_updates = _compute_add_flags("byte", left_value, right_value)
        elif operation == "adc":
            result = (left_value + right_value + carry) & 0xFF
            flags_updates = _compute_add_flags("byte", left_value, right_value + carry)
        elif operation == "sub":
            result = (left_value - right_value) & 0xFF
            flags_updates = _compute_subtract_flags("byte", left_value, right_value)
        elif operation == "sbc":
            result = (left_value - right_value - carry) & 0xFF
            flags_updates = _compute_subtract_flags("byte", left_value, right_value + carry)
        elif operation == "and":
            result = left_value & right_value
            flags_updates = _compute_logical_flags("byte", result)
        elif operation == "xor":
            result = left_value ^ right_value
            flags_updates = _compute_logical_flags("byte", result)
        elif operation == "or":
            result = left_value | right_value
            flags_updates = _compute_logical_flags("byte", result)
        else:
            result = (left_value - right_value) & 0xFF
            flags_updates = _compute_subtract_flags("byte", left_value, right_value)

        if operation == "cp":
            direction = "register-minus-memory" if register_is_left else "memory-minus-register"
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed abs24 byte compare ({direction}) from the current real execution "
                    "subset. One byte was read at the absolute address and the modeled flag "
                    "subset now reflects the subtraction result."
                ),
            )

        if store_to_memory:
            write_status, write_note = _check_writable_range(view, target_address, 1)
            if write_status == "write-discarded":
                return _executed_result(
                    before_cpu=before_cpu,
                    decoded=decoded,
                    written_registers=("PC",),
                    memory_writes=(
                        MemoryWrite(
                            address=target_address,
                            data=bytes((result,)),
                            note=f"[DISCARDED] {write_note}",
                        ),
                    ),
                    after_memory=before_memory,
                    new_pc=decoded.next_sequential_pc,
                    reg_updates=None,
                    flags_updates=flags_updates,
                    note=(
                        "Abs24 byte ALU destination was unmapped or read-only; write silently "
                        "discarded (open-bus behavior — execution continues)."
                    ),
                )
            if write_status is not None:
                return _blocked_result(
                    before_cpu=before_cpu,
                    decoded=decoded,
                    status=write_status,
                    note=write_note,
                )

            after_memory = dict(before_memory)
            after_memory[target_address] = result
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=bytes((result,)),
                        note=f"Writable runtime overlay updated by abs24 byte {operation.upper()} execution.",
                    ),
                ),
                after_memory=after_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed abs24 byte {operation}: mem(0x{target_address:06X})=0x{mem_value:02X}, "
                    f"{register_name}=0x{register_value:02X} -> mem=0x{result:02X}."
                ),
            )

        result_name, reg_updates = _build_register_update(
            before_cpu=before_cpu,
            size_kind="byte",
            register_index=register_index,
            value=result,
        )
        if reg_updates is None:
            owner_name = R32[register_index // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{register_name} cannot be updated honestly until {owner_name} is "
                    "already known in the current CPU state."
                ),
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(result_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            flags_updates=flags_updates,
            note=(
                f"Executed abs24 byte {operation}: {register_name}=0x{register_value:02X}, "
                f"mem(0x{target_address:06X})=0x{mem_value:02X} -> {result_name}=0x{result:02X}."
            ),
        )

    if raw[0] == 0xC2 and len(raw) == 6 and raw[4] == 0x3F:
        # cp (abs24), imm8: compare abs24 mem byte with immediate; flags = mem - imm8
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        mem_data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if mem_data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs24 byte compare-immediate needs a readable source byte, but "
                    "neither the writable runtime overlay nor the current read bus can provide it."
                ),
            )
        imm8 = raw[5]
        flags_updates = _compute_subtract_flags("byte", mem_data[0], imm8)
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                "Executed abs24 byte compare-immediate from the current real execution subset. "
                f"Flags = mem(0x{target_address:06X})=0x{mem_data[0]:02X} - 0x{imm8:02X}."
            ),
        )

    if raw[0] == 0xF1 and len(raw) == 4 and 0x40 <= raw[3] <= 0x47:
        # ld (abs16), R8: store one byte from R8 register to absolute 16-bit address
        source_register_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="byte",
            register_index=raw[3] & 0x07,
        )
        if source_value is None:
            owner_name = R32[(raw[3] & 0x07) // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_register_name} cannot be stored honestly until {owner_name} is "
                    "already known in the current CPU state."
                ),
            )
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=bytes((source_value & 0xFF,)),
            note=(
                "Executed abs16 byte store from the current real execution subset. The "
                "source register byte was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by abs16 byte store execution.",
        )

    if raw[0] == 0xF1 and len(raw) == 4 and 0x50 <= raw[3] <= 0x57:
        # ldw (abs16), R16: store two bytes (low/high) from R16 register
        # to absolute 16-bit address.
        register_index = raw[3] & 0x07
        source_register_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="word",
            register_index=register_index,
        )
        if source_value is None:
            owner_name = R32[register_index]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_register_name} cannot be stored honestly until {owner_name} is "
                    "already known in the current CPU state."
                ),
            )
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=(source_value & 0xFFFF).to_bytes(2, "little"),
            note=(
                "Executed abs16 word store from the current real execution subset. The "
                "low 16 bits of the source register were written little-endian to the "
                "writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by abs16 word store execution.",
        )

    if raw[0] == 0xF2 and len(raw) == 5 and 0x40 <= raw[4] <= 0x47:
        source_register_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="byte",
            register_index=raw[4] & 0x07,
        )
        if source_value is None:
            owner_name = R32[(raw[4] & 0x07) // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_register_name} cannot be stored honestly until {owner_name} is "
                    "already known in the current CPU state."
                ),
            )

        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=bytes((source_value & 0xFF,)),
            note=(
                "Executed absolute byte store from the current real execution subset. The "
                "source register byte was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by absolute byte store execution.",
        )

    if raw[0] == 0xF2 and len(raw) == 5 and 0x50 <= raw[4] <= 0x57:
        source_register_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="word",
            register_index=raw[4] & 0x07,
        )
        if source_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_register_name} cannot be stored honestly until its current word "
                    "value is known in the CPU state."
                ),
            )

        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=(source_value & 0xFFFF).to_bytes(2, "little"),
            note=(
                "Executed abs24 word store from the current real execution subset. The "
                "low 16 bits of the source register were written little-endian to the "
                "writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by abs24 word store execution.",
        )

    if raw[0] == 0xF2 and len(raw) == 6 and raw[4] == 0x00:
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=bytes((raw[5],)),
            note=(
                "Executed absolute immediate byte store from the current real execution subset. "
                "The decoded immediate byte was written to the writable runtime overlay."
            ),
            memory_note=(
                "Writable runtime overlay updated by absolute immediate byte store execution."
            ),
        )

    if raw[0] == 0xF2 and len(raw) == 7 and raw[4] == 0x02:
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        imm16 = int.from_bytes(raw[5:7], "little")
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=imm16.to_bytes(2, "little"),
            note=(
                "Executed absolute immediate word store from the current real execution subset. "
                "The decoded 16-bit immediate was written to the writable runtime overlay."
            ),
            memory_note=(
                "Writable runtime overlay updated by absolute immediate word store execution."
            ),
        )

    if raw[0] == 0xF2 and len(raw) == 5 and 0x60 <= raw[4] <= 0x67:
        source_register_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="long",
            register_index=raw[4] & 0x07,
        )
        if source_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_register_name} cannot be stored honestly until its current full "
                    "value is known in the CPU state."
                ),
            )

        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=source_value.to_bytes(4, "little"),
            note=(
                "Executed abs24 long store from the current real execution subset. The source "
                "32-bit register value was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by abs24 long store execution.",
        )

    if raw[0] == 0xF1 and len(raw) == 4 and 0x60 <= raw[3] <= 0x67:
        source_register_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="long",
            register_index=raw[3] & 0x07,
        )
        if source_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_register_name} cannot be stored honestly until its current full "
                    "value is known in the CPU state."
                ),
            )

        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=source_value.to_bytes(4, "little"),
            note=(
                "Executed abs16 long store from the current real execution subset. The source "
                "32-bit register value was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by abs16 long store execution.",
        )

    if raw[0] == 0xF1 and len(raw) == 5 and raw[3] == 0x00:
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=bytes((raw[4],)),
            note=(
                "Executed abs16 immediate byte store from the current real execution subset. "
                "The decoded immediate byte was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by abs16 immediate byte store execution.",
        )

    if raw[0] == 0xF1 and len(raw) == 4 and 0xA8 <= raw[3] <= 0xCF:
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        return execute_absolute_bit_operation(
            target_address=target_address,
            op_byte=raw[3],
            width_label="abs16",
        )

    if raw[0] == 0xF1 and len(raw) == 4 and (
        0x28 <= raw[3] <= 0x2C or 0x80 <= raw[3] <= 0xA7
    ):
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        return execute_absolute_cf_operation(
            target_address=target_address,
            op_byte=raw[3],
            width_label="abs16",
        )

    if raw[0] == 0xF2 and len(raw) == 5 and (
        0x28 <= raw[4] <= 0x2C or 0x80 <= raw[4] <= 0xA7
    ):
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        return execute_absolute_cf_operation(
            target_address=target_address,
            op_byte=raw[4],
            width_label="abs24",
        )

    if raw[0] == 0xF2 and len(raw) == 5 and 0xA8 <= raw[4] <= 0xCF:
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        return execute_absolute_bit_operation(
            target_address=target_address,
            op_byte=raw[4],
            width_label="abs24",
        )

    if raw[0] == 0xF3 and len(raw) == 5 and (raw[1] & 0x03) == 0x01 and 0x30 <= raw[4] <= 0x37:
        # ARI secondary mode=1: lda R32, (r32+d16)
        # Encoding: F3 [secondary] [d16-lo] [d16-hi] [0x30+dest_r32]
        # secondary bits[1:0] = 0x01 (mode 1), bits[4:2] = r32_base index
        r32_base_index = (raw[1] >> 2) & 0x07
        r32_base_field = REG32_FIELDS[r32_base_index]
        r32_base_name = R32[r32_base_index]
        dest_r32_index = raw[4] & 0x07

        r32_base_value = getattr(before_cpu.regs, r32_base_field)
        if r32_base_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-address-register",
                note=(
                    f"{r32_base_name} must be known to compute the base for this ARI "
                    "secondary d16 lda."
                ),
            )

        # d16 is signed 16-bit displacement at bytes 2:4
        d16_raw = int.from_bytes(raw[2:4], "little")
        d16 = d16_raw if d16_raw < 0x8000 else d16_raw - 0x10000
        effective_address = _mask_address(r32_base_value + d16)

        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="long",
            register_index=dest_r32_index,
            value=effective_address,
            note=(
                f"Executed ARI secondary d16 lda from the current real execution subset. "
                f"EA = {r32_base_name}(0x{r32_base_value:06X}) + {d16} "
                f"= 0x{effective_address:06X}, stored into {R32[dest_r32_index]}."
            ),
        )

    if raw[0] == 0xF3 and len(raw) == 5 and 0x30 <= raw[4] <= 0x37:
        # ARI secondary indexed: lda R32, (r32+r16)
        # Encoding: F3 [secondary] [r32_base_byte] [r16_index_byte] [0x30+dest_r32]
        # Computes EA = r32_base + r16_index, stores into dest_r32.
        r32_base_index = (raw[2] >> 2) & 0x07
        r16_index_index = (raw[3] >> 2) & 0x07
        dest_r32_index = raw[4] & 0x07

        r32_base_field = REG32_FIELDS[r32_base_index]
        r16_field = REG32_FIELDS[r16_index_index]
        r32_base_name = R32[r32_base_index]
        r16_name = R16[r16_index_index]

        r32_base_value = getattr(before_cpu.regs, r32_base_field)
        r16_full = getattr(before_cpu.regs, r16_field)

        if r32_base_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-address-register",
                note=(
                    f"{r32_base_name} must be known to compute the base for this ARI "
                    "secondary indexed lda."
                ),
            )
        if r16_full is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{r16_name} must be known to compute the index for this ARI secondary "
                    "indexed lda."
                ),
            )

        r16_value = r16_full & 0xFFFF
        effective_address = _mask_address(r32_base_value + r16_value)

        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="long",
            register_index=dest_r32_index,
            value=effective_address,
            note=(
                f"Executed ARI secondary indexed lda from the current real execution subset. "
                f"EA = {r32_base_name}(0x{r32_base_value:06X}) + {r16_name}(0x{r16_value:04X}) "
                f"= 0x{effective_address:06X}, stored into {R32[dest_r32_index]}."
            ),
        )

    if raw[0] == 0xF3 and len(raw) == 6 and raw[4] == 0x00:
        # ARI secondary indexed: ld (r32+r16), imm8
        # Encoding: F3 [secondary] [r32_byte] [r16_byte] 00 [imm8]
        # r32_byte: base r32 = (r32_byte >> 2) & 7
        # r16_byte: index r16 = (r16_byte >> 2) & 7 (lower 16-bit of the r32 register)
        r32_index = (raw[2] >> 2) & 0x07
        r16_index = (raw[3] >> 2) & 0x07
        r32_field = REG32_FIELDS[r32_index]
        r16_field = REG32_FIELDS[r16_index]
        r32_name = R32[r32_index]
        r16_name = R16[r16_index]
        r32_value = getattr(before_cpu.regs, r32_field)
        r16_full = getattr(before_cpu.regs, r16_field)
        if r32_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{r32_name} must be known to compute the effective address for "
                    "this ARI secondary indexed byte store."
                ),
            )
        if r16_full is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{r16_name} must be known to compute the index for "
                    "this ARI secondary indexed byte store."
                ),
            )
        r16_value = r16_full & 0xFFFF
        effective_address = _mask_address(r32_value + r16_value)
        imm8 = raw[5]
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=effective_address,
            data=bytes((imm8,)),
            note=(
                f"Executed ARI secondary indexed byte store from the current real execution "
                f"subset. Address {r32_name}+{r16_name}=0x{effective_address:06X} written "
                f"with immediate 0x{imm8:02X}."
            ),
            memory_note=(
                "Writable runtime overlay updated by ARI secondary indexed byte store execution."
            ),
        )

    if raw[0] == 0xF3 and len(raw) == 7 and raw[4] == 0x02:
        # ARI secondary indexed: ldw (r32+r16), imm16
        # Encoding: F3 [secondary] [r32_byte] [r16_byte] 02 [imm16-lo] [imm16-hi]
        r32_index = (raw[2] >> 2) & 0x07
        r16_index = (raw[3] >> 2) & 0x07
        r32_field = REG32_FIELDS[r32_index]
        r16_field = REG32_FIELDS[r16_index]
        r32_name = R32[r32_index]
        r16_name = R16[r16_index]
        r32_value = getattr(before_cpu.regs, r32_field)
        r16_full = getattr(before_cpu.regs, r16_field)
        if r32_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{r32_name} must be known to compute the effective address for "
                    "this ARI secondary indexed word store."
                ),
            )
        if r16_full is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{r16_name} must be known to compute the index for "
                    "this ARI secondary indexed word store."
                ),
            )
        r16_value = r16_full & 0xFFFF
        effective_address = _mask_address(r32_value + r16_value)
        imm16 = int.from_bytes(raw[5:7], "little")
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=effective_address,
            data=imm16.to_bytes(2, "little"),
            note=(
                f"Executed ARI secondary indexed word store from the current real execution "
                f"subset. Address {r32_name}+{r16_name}=0x{effective_address:06X} written "
                f"with immediate 0x{imm16:04X}."
            ),
            memory_note=(
                "Writable runtime overlay updated by ARI secondary indexed word store execution."
            ),
        )

    if raw[0] == 0xF3 and len(raw) == 5 and 0x40 <= raw[4] <= 0x67:
        # ARI secondary indexed register store: ld/ldw/ld (r32+r16), R8/R16/R32
        # Encoding: F3 [secondary] [r32_byte] [r16_byte] [0x40..0x67]
        r32_index = (raw[2] >> 2) & 0x07
        r16_index = (raw[3] >> 2) & 0x07
        r32_field = REG32_FIELDS[r32_index]
        r16_field = REG32_FIELDS[r16_index]
        r32_name = R32[r32_index]
        r16_name = R16[r16_index]
        r32_value = getattr(before_cpu.regs, r32_field)
        r16_full = getattr(before_cpu.regs, r16_field)
        if r32_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{r32_name} must be known to compute the effective address for "
                    "this ARI secondary indexed register store."
                ),
            )
        if r16_full is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{r16_name} must be known to compute the index for "
                    "this ARI secondary indexed register store."
                ),
            )

        effective_address = _mask_address(r32_value + (r16_full & 0xFFFF))
        op = raw[4]
        if 0x40 <= op <= 0x47:
            size_kind = "byte"
            data_size = 1
            size_label = "byte"
        elif 0x50 <= op <= 0x57:
            size_kind = "word"
            data_size = 2
            size_label = "word"
        else:
            size_kind = "long"
            data_size = 4
            size_label = "long"

        source_name, source_value = _extract_register_value(before_cpu, size_kind, op & 0x07)
        if source_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_name} must be known to execute this ARI secondary indexed "
                    f"{size_label} store."
                ),
            )

        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=effective_address,
            data=source_value.to_bytes(data_size, "little"),
            note=(
                f"Executed ARI secondary indexed {size_label} store from the current real "
                f"execution subset. Address {r32_name}+{r16_name}=0x{effective_address:06X} "
                f"written with {source_name}=0x{source_value:0{data_size * 2}X}."
            ),
            memory_note=(
                "Writable runtime overlay updated by ARI secondary indexed register store execution."
            ),
        )

    return None


def _try_execute_cpu_io_store(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute CPU I/O immediate stores: ldb (n), imm8 and ldw (n), imm16.

    These write to TLCS-900 internal CPU peripheral registers (address space 0x00..0xFF),
    not to the normal memory bus.  No writable-range check is needed.
    The write is recorded in MemoryWrite for observability but does not update after_memory.

    Encoding:
      08 [n] [imm8]        => ldb (n), imm8   (3 bytes)
      0A [n] [imm16-lo] [imm16-hi]  => ldw (n), imm16  (4 bytes)
    """
    raw = decoded.raw_bytes
    if raw is None or raw[0] not in (0x08, 0x0A):
        return None

    if raw[0] == 0x08 and len(raw) == 3:
        io_addr = raw[1]
        imm8 = raw[2]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=io_addr,
                    data=bytes((imm8,)),
                    note=(
                        f"CPU I/O byte store to address 0x{io_addr:02X} with immediate "
                        f"0x{imm8:02X}. This is a TLCS-900 internal peripheral register write; "
                        "the writable overlay is not updated."
                    ),
                ),
            ),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            note=(
                f"Executed CPU I/O byte store ldb (0x{io_addr:02X}), 0x{imm8:02X} from the "
                "current real execution subset. This writes to a TLCS-900 internal peripheral "
                "register. The memory overlay is not affected."
            ),
        )

    if raw[0] == 0x0A and len(raw) == 4:
        io_addr = raw[1]
        imm16 = int.from_bytes(raw[2:4], "little")
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=io_addr,
                    data=imm16.to_bytes(2, "little"),
                    note=(
                        f"CPU I/O word store to address 0x{io_addr:02X} with immediate "
                        f"0x{imm16:04X}. This is a TLCS-900 internal peripheral register write; "
                        "the writable overlay is not updated."
                    ),
                ),
            ),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            note=(
                f"Executed CPU I/O word store ldw (0x{io_addr:02X}), 0x{imm16:04X} from the "
                "current real execution subset. This writes to a TLCS-900 internal peripheral "
                "register. The memory overlay is not affected."
            ),
        )

    return None


def _try_execute_abs16_word_memory(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute the abs16 word-memory subset (prefix 0xD1).

    Currently implemented:
      - cpw (abs16), imm16  (D1 lo hi 3F imm_lo imm_hi, 6 bytes)
        Loads 2 bytes from the absolute address, computes the subtraction
        with the 16-bit immediate, and updates the modeled flag subset.
        No write-back (compare only).
    """
    raw = decoded.raw_bytes
    if raw is None or raw[0] != 0xD1:
        return None

    if len(raw) == 4 and 0x20 <= raw[3] <= 0x27:
        # ld R16, (abs16): load 2 bytes from abs16, write to R16 destination.
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        data = _read_runtime_bytes(view, before_memory, target_address, 2)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs16 word load needs 2 readable source bytes, but neither the "
                    "writable runtime overlay nor the current read bus can provide them."
                ),
            )
        value = int.from_bytes(data, "little")
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="word",
            register_index=raw[3] & 0x07,
            value=value,
            note=(
                f"Executed abs16 word load ld R16, (0x{target_address & 0xFFFF:04X}): "
                f"value=0x{value:04X} into target R16."
            ),
        )

    if len(raw) == 6 and raw[3] == 0x3F:
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        data = _read_runtime_bytes(view, before_memory, target_address, 2)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs16 word compare needs 2 readable source bytes, but neither the "
                    "writable runtime overlay nor the current read bus can provide them."
                ),
            )
        mem_value = int.from_bytes(data, "little")
        imm = int.from_bytes(raw[4:6], "little")
        flags_updates = _compute_subtract_flags("word", mem_value, imm)
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                "Executed abs16 word compare-immediate from the current real execution subset. "
                f"Flags = mem16(0x{target_address & 0xFFFF:04X})=0x{mem_value:04X} - "
                f"imm=0x{imm:04X}."
            ),
        )

    return None


def _try_execute_abs24_word_memory(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute the abs24 word-memory subset (prefix 0xD2)."""
    raw = decoded.raw_bytes
    if raw is None or raw[0] != 0xD2:
        return None

    if len(raw) == 5 and 0x20 <= raw[4] <= 0x27:
        target_address = _mask_address(int.from_bytes(raw[1:4], "little"))
        data = _read_runtime_bytes(view, before_memory, target_address, 2)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs24 word load needs 2 readable source bytes, but neither the "
                    "writable runtime overlay nor the current read bus can provide them."
                ),
            )
        value = int.from_bytes(data, "little")
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="word",
            register_index=raw[4] & 0x07,
            value=value,
            note=(
                f"Executed abs24 word load ld R16, (0x{target_address:06X}): "
                f"value=0x{value:04X} into target R16."
            ),
        )

    return None


def _try_execute_abs8_long_memory(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or raw[0] != 0xE0 or len(raw) != 3:
        return None

    if 0x20 <= raw[2] <= 0x27:
        target_address = _mask_address(raw[1])
        data = _read_runtime_bytes(view, before_memory, target_address, 4)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs8 long load needs 4 readable source bytes, but neither the "
                    "writable runtime overlay nor the current read bus can provide them."
                ),
            )
        value = int.from_bytes(data, "little")
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="long",
            register_index=raw[2] & 0x07,
            value=value,
            note=(
                f"Executed abs8 long load ld R32, (0x{target_address:02X}): "
                f"value=0x{value:08X} into target R32."
            ),
        )

    return None


def _try_execute_abs16_byte_memory(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or raw[0] != 0xC1 or len(raw) not in (4, 5):
        return None

    # inc/dec N, (abs16) byte form is 4 bytes total: C1 lo hi op.
    # Catalog: encode_mem_abs16_inc_dec — count_code 0 means 8.
    if len(raw) == 4 and 0x60 <= raw[3] <= 0x6F:
        target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
        op = raw[3]
        count_code = op & 0x07
        count = 8 if count_code == 0 else count_code
        is_dec = op >= 0x68
        source = _read_runtime_bytes(view, before_memory, target_address, 1)
        if source is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs16 byte inc/dec needs a readable source byte, but neither the "
                    "writable runtime overlay nor the current read bus can provide it."
                ),
            )
        old_value = source[0]
        if is_dec:
            new_value = (old_value - count) & 0xFF
            mnemonic = "decb"
        else:
            new_value = (old_value + count) & 0xFF
            mnemonic = "incb"

        write_status, write_note = _check_writable_range(view, target_address, 1)
        if write_status is not None and write_status != "write-discarded":
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        # Flags: TLCS-900 updates Z/S/V/H on inc/dec mem; CF is preserved
        # (unlike ADD/SUB which update CF). We reuse the standard add/sub
        # flag helpers but strip CF so the existing carry stays unmodified.
        # N (negative/sub) is documented but not tracked in this CPU model.
        if is_dec:
            flags_updates = dict(_compute_subtract_flags("byte", old_value, count))
        else:
            flags_updates = dict(_compute_add_flags("byte", old_value, count))
        flags_updates.pop("cf", None)

        if write_status == "write-discarded":
            after_memory = dict(before_memory)
            mem_write = MemoryWrite(
                address=target_address,
                data=bytes((new_value,)),
                note=f"[DISCARDED] {write_note}",
            )
        else:
            after_memory = dict(before_memory)
            after_memory[target_address] = new_value
            mem_write = MemoryWrite(
                address=target_address,
                data=bytes((new_value,)),
                note="Writable runtime overlay updated by abs16 byte inc/dec.",
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(mem_write,),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed {mnemonic} {count}, (0x{target_address & 0xFFFF:04X}): "
                f"mem8 0x{old_value:02X} -> 0x{new_value:02X}. ZF/SF updated; "
                "VF/HF/NF intentionally left unchanged."
            ),
        )

    target_address = _mask_address(int.from_bytes(raw[1:3], "little"))
    op = raw[3]

    if len(raw) == 4 and 0x20 <= op <= 0x27:
        data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs16 byte load needs a readable source byte, but neither the "
                    "writable runtime overlay nor the current read bus can provide it."
                ),
            )

        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="byte",
            register_index=op & 0x07,
            value=data[0],
            note=(
                "Executed abs16 byte load from the current real execution subset. The source "
                "byte was read from the writable runtime overlay or the current read bus."
            ),
        )

    if len(raw) == 5 and op == 0x3F:
        data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs16 byte compare needs a readable source byte, but neither the "
                    "writable runtime overlay nor the current read bus can provide it."
                ),
            )

        flags_updates = _compute_subtract_flags("byte", data[0], raw[4])
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                "Executed abs16 byte compare-immediate from the current real execution subset. "
                "The source byte came from the writable runtime overlay or the current read "
                "bus and the modeled flag subset now reflects the subtraction result."
            ),
        )

    if len(raw) == 5 and 0x38 <= op <= 0x3E:
        data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This abs16 byte ALU-immediate needs a readable source byte, but neither the "
                    "writable runtime overlay nor the current read bus can provide it."
                ),
            )

        imm8 = raw[4]
        mem_value = data[0]
        operation = {
            0x38: "add",
            0x39: "adc",
            0x3A: "sub",
            0x3B: "sbc",
            0x3C: "and",
            0x3D: "xor",
            0x3E: "or",
        }[op]
        carry = 0
        if operation in ("adc", "sbc"):
            if before_cpu.flags.cf is None:
                return _blocked_result(
                    before_cpu=before_cpu,
                    decoded=decoded,
                    status="runtime-state-required",
                    note=(
                        f"{operation.upper()} on abs16 byte memory requires a known carry flag, "
                        "which is not modeled in the current CPU state."
                    ),
                )
            carry = int(before_cpu.flags.cf)

        if operation == "add":
            result = (mem_value + imm8) & 0xFF
            flags_updates = _compute_add_flags("byte", mem_value, imm8)
        elif operation == "adc":
            result = (mem_value + imm8 + carry) & 0xFF
            flags_updates = _compute_add_flags("byte", mem_value, imm8 + carry)
        elif operation == "sub":
            result = (mem_value - imm8) & 0xFF
            flags_updates = _compute_subtract_flags("byte", mem_value, imm8)
        elif operation == "sbc":
            result = (mem_value - imm8 - carry) & 0xFF
            flags_updates = _compute_subtract_flags("byte", mem_value, imm8 + carry)
        elif operation == "and":
            result = mem_value & imm8
            flags_updates = _compute_logical_flags("byte", result)
        elif operation == "xor":
            result = mem_value ^ imm8
            flags_updates = _compute_logical_flags("byte", result)
        else:
            result = mem_value | imm8
            flags_updates = _compute_logical_flags("byte", result)

        write_status, write_note = _check_writable_range(view, target_address, 1)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=bytes((result,)),
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    "Abs16 byte ALU-immediate destination was unmapped or read-only; write "
                    "silently discarded (open-bus behavior - execution continues)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[target_address] = result
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=bytes((result,)),
                    note=f"Writable runtime overlay updated by abs16 byte {operation.upper()} immediate execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed abs16 byte {operation.upper()}-immediate from the current real "
                f"execution subset. mem8(0x{target_address:04X})=0x{mem_value:02X}, "
                f"imm=0x{imm8:02X} -> 0x{result:02X}."
            ),
        )

    return None


def _execute_absolute_store(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    target_address: int,
    data: bytes,
    note: str,
    memory_note: str,
) -> ExecutionResult:
    write_status, write_note = _check_writable_range(view, target_address, len(data))
    if write_status == "write-discarded":
        # Real hardware silently discards writes to unmapped / ROM addresses.
        # Execution continues; the destination memory is not updated.
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=data,
                    note=f"[DISCARDED] {write_note}",
                ),
            ),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            note=(
                f"{note} The destination address was unmapped or read-only; the write was "
                "silently discarded (open-bus behavior — execution continues)."
            ),
        )
    if write_status is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=write_status,
            note=write_note,
        )

    after_memory = dict(before_memory)
    for offset, value in enumerate(data):
        after_memory[_mask_address(target_address + offset)] = value

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(
            MemoryWrite(
                address=target_address,
                data=data,
                note=memory_note,
            ),
        ),
        after_memory=after_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        note=note,
    )


def _try_execute_reg_indirect_load(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute (r32) byte-indirect instructions.

    Encoding: [0x80+r32_idx] [op] [optional extra]
    op=0x20..0x27: ld R8, (r32)         — 2 bytes
    op=0x3F:       cp (r32), imm8       — 3 bytes
    op=0xF0..0xF7: cp R8, (r32)         — 2 bytes (pass 51)
                  Compare R8 with mem byte ; flags = R8 - mem ;
                  no memory write.
                  Source : ngdis/tlcs900_zz_mem.c case 0xF0
    """
    raw = decoded.raw_bytes
    if raw is None or not (0x80 <= raw[0] <= 0x87):
        return None
    op = raw[1] if len(raw) >= 2 else None
    if op is None:
        return None
    is_2byte_supported = (
        (0x20 <= op <= 0x27)            # LD R8, (R32) load
        or (0x30 <= op <= 0x37)         # EX (R32), R8 (pass 55)
        or (0x60 <= op <= 0x6F)         # INC/DEC #n, (R32) (pass 56)
        or (0x78 <= op <= 0x7F)         # shift family on (R32) (pass 56)
        or (0x80 <= op <= 0x8F)         # ADD R8/(R32) both directions (pass 54)
        or (0x90 <= op <= 0x9F)         # ADC R8/(R32) both directions (pass 55)
        or (0xA0 <= op <= 0xAF)         # SUB R8/(R32) both directions (pass 54)
        or (0xB0 <= op <= 0xBF)         # SBC R8/(R32) both directions (pass 55)
        or (0xC0 <= op <= 0xEF)         # AND/OR/XOR R8/(R32) both directions
        or (0xF0 <= op <= 0xFF)         # CP R8/(R32) both directions (pass 51 + 55)
    )
    is_3byte_supported = 0x38 <= op <= 0x3F  # (R32), imm8 ALU + CP imm (pass 55)
    if len(raw) == 2 and not is_2byte_supported:
        return None
    if len(raw) == 3 and not is_3byte_supported:
        return None
    if len(raw) not in (2, 3):
        return None

    r32_index = raw[0] & 0x07
    r32_field = REG32_FIELDS[r32_index]
    r32_name = R32[r32_index]
    r32_value = getattr(before_cpu.regs, r32_field)
    if r32_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{r32_name} must be known before this register-indirect load can compute "
                "its source address honestly."
            ),
        )

    source_address = _mask_address(r32_value)
    data_bytes = _read_runtime_bytes(view, before_memory, source_address, 1)
    if data_bytes is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This register-indirect load needs a readable byte at ({r32_name})="
                f"0x{source_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide it."
            ),
        )

    if 0x38 <= raw[1] <= 0x3F:
        # Pass 55 : ALU (R32), imm8 — 3-byte RMW byte family.
        # Sub-op map (per ngdis/tlcs900_zz_mem.c) :
        #   0x38 = ADD   0x39 = ADC   0x3A = SUB   0x3B = SBC
        #   0x3C = AND   0x3D = XOR   0x3E = OR    0x3F = CP (no write)
        # ADC/SBC need a known C flag (block honestly if unknown).
        sub_op = raw[1]
        imm8 = raw[2]
        mem_byte = data_bytes[0]
        op_name = {
            0x38: "add", 0x39: "adc", 0x3A: "sub", 0x3B: "sbc",
            0x3C: "and", 0x3D: "xor", 0x3E: "or",  0x3F: "cp",
        }[sub_op]
        needs_carry = sub_op in (0x39, 0x3B)
        if needs_carry and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{op_name.upper()} ({r32_name}), imm8 requires a known carry "
                    "flag, which is not modeled in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf) if needs_carry else 0
        if sub_op in (0x38, 0x39):  # ADD / ADC
            result = (mem_byte + imm8 + carry) & 0xFF
            flags_updates = _compute_add_flags("byte", mem_byte, imm8 + carry)
        elif sub_op in (0x3A, 0x3B, 0x3F):  # SUB / SBC / CP
            result = (mem_byte - imm8 - carry) & 0xFF
            flags_updates = _compute_subtract_flags("byte", mem_byte, imm8 + carry)
        elif sub_op == 0x3C:  # AND
            result = mem_byte & imm8
            flags_updates = _compute_logical_flags("byte", result)
        elif sub_op == 0x3D:  # XOR
            result = mem_byte ^ imm8
            flags_updates = _compute_logical_flags("byte", result)
        else:  # 0x3E OR
            result = mem_byte | imm8
            flags_updates = _compute_logical_flags("byte", result)

        if sub_op == 0x3F:
            # CP : flags only, no write.
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed cp ({r32_name})=0x{source_address:06X}=0x{mem_byte:02X}, "
                    f"0x{imm8:02X}."
                ),
            )
        after_memory = dict(before_memory)
        after_memory[_mask_address(source_address)] = result
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=_mask_address(source_address),
                    data=bytes((result,)),
                    note=f"{op_name.upper()} ({r32_name}), imm8 : mem byte updated.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed {op_name} ({r32_name}=0x{source_address:06X})=0x{mem_byte:02X}, "
                f"0x{imm8:02X} → mem=0x{result:02X}."
            ),
        )

    if 0xF0 <= raw[1] <= 0xFF:
        # cp R8, (r32) [0xF0..0xF7]   — pass 51 — flags = R8 - mem
        # cp (r32), R8 [0xF8..0xFF]   — pass 55 — flags = mem - R8
        # Both : no register or memory write.
        # Source : ngdis/tlcs900_zz_mem.c case 0xF0 "CP R,(mem)" + case 0xF8 "CP (mem),R".
        sub_op = raw[1]
        mem_on_left = sub_op >= 0xF8
        r8_index = sub_op & 0x07
        r8_name, r8_value = _extract_register_value(
            before_cpu, "byte", r8_index,
        )
        if r8_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-source-register",
                note=(
                    f"CP {r8_name}, ({r32_name}) needs the value of {r8_name} "
                    f"(owner = {R32[r8_index // 2]}) to be modeled."
                ),
            )
        if mem_on_left:
            flags_updates = _compute_subtract_flags(
                "byte", data_bytes[0], r8_value,
            )
            note = (
                f"Executed cp ({r32_name}), {r8_name}. Flags = "
                f"mem({r32_name}=0x{source_address:06X})=0x{data_bytes[0]:02X} - "
                f"{r8_name}=0x{r8_value:02X}."
            )
        else:
            flags_updates = _compute_subtract_flags(
                "byte", r8_value, data_bytes[0],
            )
            note = (
                f"Executed cp {r8_name}, ({r32_name}). Flags = "
                f"{r8_name}=0x{r8_value:02X} - mem({r32_name}=0x{source_address:06X})"
                f"=0x{data_bytes[0]:02X}."
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=note,
        )

    if 0x30 <= raw[1] <= 0x37:
        # Pass 55 : EX (R32), R8 — swap mem byte with R8 ; flags unchanged.
        # Source : ngdis/tlcs900_zz_mem.c case 0x30 "EX (mem),R".
        r8_index = raw[1] & 0x07
        r8_name, r8_value = _extract_register_value(
            before_cpu, "byte", r8_index,
        )
        if r8_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-source-register",
                note=(
                    f"EX ({r32_name}), {r8_name} needs the value of {r8_name} "
                    f"(owner = {R32[r8_index // 2]}) to be modeled."
                ),
            )
        mem_byte = data_bytes[0]
        # mem ← old R8 ; R8 ← old mem.
        result_name, reg_updates = _build_register_update(
            before_cpu, "byte", r8_index, mem_byte,
        )
        if reg_updates is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"EX ({r32_name}), {r8_name} needs the owner register of "
                    f"{r8_name} fully known to write back."
                ),
            )
        after_memory = dict(before_memory)
        after_memory[_mask_address(source_address)] = r8_value
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(result_name, "PC"),
            memory_writes=(
                MemoryWrite(
                    address=_mask_address(source_address),
                    data=bytes((r8_value,)),
                    note=f"EX ({r32_name}), {r8_name} : mem byte ← old R8.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            flags_updates=None,
            note=(
                f"Executed ex ({r32_name}=0x{source_address:06X})=0x{mem_byte:02X}, "
                f"{r8_name}=0x{r8_value:02X} : swapped values "
                f"(mem ← 0x{r8_value:02X}, {result_name} ← 0x{mem_byte:02X})."
            ),
        )

    if 0x60 <= raw[1] <= 0x6F:
        # Pass 56 : INC #n, (R32) / DEC #n, (R32) — RMW with 3-bit immediate.
        # n = (sub_op & 0x07) ; 0 → 8 (Toshiba spec quirk).
        # Direction by sub_op range : 0x60..0x67 = INC, 0x68..0x6F = DEC.
        # Flags : updates S/Z/V/H ; N depends on direction ; **CF preserved**
        # (per existing abs16 INC/DEC pattern, line ~1438).
        sub_op = raw[1]
        is_dec = sub_op >= 0x68
        op_name = "dec" if is_dec else "inc"
        count = sub_op & 0x07
        if count == 0:
            count = 8
        mem_byte = data_bytes[0]
        if is_dec:
            new_value = (mem_byte - count) & 0xFF
            flags_updates = dict(_compute_subtract_flags("byte", mem_byte, count))
        else:
            new_value = (mem_byte + count) & 0xFF
            flags_updates = dict(_compute_add_flags("byte", mem_byte, count))
        flags_updates.pop("cf", None)  # INC/DEC mem preserves carry.
        after_memory = dict(before_memory)
        after_memory[_mask_address(source_address)] = new_value
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=_mask_address(source_address),
                    data=bytes((new_value,)),
                    note=f"{op_name.upper()} #{count}, ({r32_name}) : mem byte updated.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed {op_name} {count}, ({r32_name}=0x{source_address:06X}) : "
                f"mem 0x{mem_byte:02X} → 0x{new_value:02X} (CF preserved)."
            ),
        )

    if 0x78 <= raw[1] <= 0x7F:
        # Pass 56 : shift/rotate (R32) — 8-bit byte memory RMW, count=1.
        # Sub-op layout per ngdis/tlcs900_zz_mem.c :
        #   0x78 RLC   0x79 RRC   0x7A RL   0x7B RR
        #   0x7C SLA   0x7D SRA   0x7E SLL   0x7F SRL
        # Reuses the rotate/shift logic from the register-form shift family
        # (line ~4025) but operates on the byte at (R32). Carry handling :
        #   RLC : C ← MSB ; bit0 ← MSB
        #   RRC : C ← LSB ; bit7 ← LSB
        #   RL  : new C ← MSB ; bit0 ← old C
        #   RR  : new C ← LSB ; bit7 ← old C
        #   SLA/SLL : C ← MSB ; bit0 ← 0
        #   SRA : C ← LSB ; bit7 ← sign (preserved)
        #   SRL : C ← LSB ; bit7 ← 0
        # RL/RR require a known CF (rotate through carry).
        sub_op = raw[1]
        op_name = {
            0x78: "rlc", 0x79: "rrc", 0x7A: "rl",  0x7B: "rr",
            0x7C: "sla", 0x7D: "sra", 0x7E: "sll", 0x7F: "srl",
        }[sub_op]
        needs_carry = sub_op in (0x7A, 0x7B)  # RL / RR through carry
        if needs_carry and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{op_name.upper()} ({r32_name}) is a rotate-through-carry that "
                    "requires a known C flag, which is not modeled in the current CPU state."
                ),
            )
        mem_byte = data_bytes[0]
        msb = (mem_byte >> 7) & 1
        lsb = mem_byte & 1
        sign_bit = msb
        if sub_op == 0x78:    # RLC : bit0 ← MSB
            new_value = ((mem_byte << 1) | msb) & 0xFF
            carry_out = bool(msb)
        elif sub_op == 0x79:  # RRC : bit7 ← LSB
            new_value = ((mem_byte >> 1) | (lsb << 7)) & 0xFF
            carry_out = bool(lsb)
        elif sub_op == 0x7A:  # RL through carry : bit0 ← old C
            old_c = int(before_cpu.flags.cf)
            new_value = ((mem_byte << 1) | old_c) & 0xFF
            carry_out = bool(msb)
        elif sub_op == 0x7B:  # RR through carry : bit7 ← old C
            old_c = int(before_cpu.flags.cf)
            new_value = ((mem_byte >> 1) | (old_c << 7)) & 0xFF
            carry_out = bool(lsb)
        elif sub_op in (0x7C, 0x7E):  # SLA / SLL : bit0 ← 0 (identical for byte)
            new_value = (mem_byte << 1) & 0xFF
            carry_out = bool(msb)
        elif sub_op == 0x7D:  # SRA : sign-extending
            new_value = ((mem_byte >> 1) | (sign_bit << 7)) & 0xFF
            carry_out = bool(lsb)
        else:                 # 0x7F SRL : logical right, bit7 ← 0
            new_value = (mem_byte >> 1) & 0xFF
            carry_out = bool(lsb)
        flags_updates = {
            "sf": bool(new_value >> 7),
            "zf": new_value == 0,
            "vf": False,         # parity for shift ops — not modeled (per register form)
            "hf": False,
            "cf": carry_out,
            "nf": False,
        }
        after_memory = dict(before_memory)
        after_memory[_mask_address(source_address)] = new_value
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=_mask_address(source_address),
                    data=bytes((new_value,)),
                    note=f"{op_name.upper()} ({r32_name}) : mem byte updated.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed {op_name} ({r32_name}=0x{source_address:06X}) : "
                f"mem 0x{mem_byte:02X} → 0x{new_value:02X}, C ← {int(carry_out)}."
            ),
        )

    if (0x90 <= raw[1] <= 0x9F) or (0xB0 <= raw[1] <= 0xBF):
        # Pass 55 : ADC/SBC R8 ↔ (R32) — carry/borrow propagation.
        # Sub-op layout (verified against NgpCraft_Disasm oracle) :
        #   0x90..0x97 = ADC R8, (R32)   — R8 ← R8 + mem + C
        #   0x98..0x9F = ADC (R32), R8   — mem ← mem + R8 + C
        #   0xB0..0xB7 = SBC R8, (R32)   — R8 ← R8 - mem - C
        #   0xB8..0xBF = SBC (R32), R8   — mem ← mem - R8 - C
        # Direction by bit 3 of op : 0=R8←, 1=mem←
        sub_op = raw[1]
        is_adc = 0x90 <= sub_op <= 0x9F
        op_name = "adc" if is_adc else "sbc"
        store_to_memory = bool(sub_op & 0x08)
        r8_index = sub_op & 0x07
        r8_name, r8_value = _extract_register_value(
            before_cpu, "byte", r8_index,
        )
        if r8_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-source-register",
                note=(
                    f"{op_name.upper()} on {r8_name}/({r32_name}) needs "
                    f"{r8_name} value (owner = {R32[r8_index // 2]}) modeled."
                ),
            )
        if before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{op_name.upper()} on byte (R32) memory requires a known carry "
                    "flag, which is not modeled in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf)
        mem_byte = data_bytes[0]
        if store_to_memory:
            left, right = mem_byte, r8_value
        else:
            left, right = r8_value, mem_byte
        if is_adc:
            result = (left + right + carry) & 0xFF
            flags_updates = _compute_add_flags("byte", left, right + carry)
        else:
            result = (left - right - carry) & 0xFF
            flags_updates = _compute_subtract_flags("byte", left, right + carry)
        if store_to_memory:
            after_memory = dict(before_memory)
            after_memory[_mask_address(source_address)] = result
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=_mask_address(source_address),
                        data=bytes((result,)),
                        note=(
                            f"{op_name.upper()} ({r32_name}), {r8_name} (carry={carry}) : "
                            f"mem byte updated."
                        ),
                    ),
                ),
                after_memory=after_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed {op_name} ({r32_name}=0x{source_address:06X})=0x{mem_byte:02X}, "
                    f"{r8_name}=0x{r8_value:02X}, C={carry} → mem=0x{result:02X}."
                ),
            )
        result_name, reg_updates = _build_register_update(
            before_cpu, "byte", r8_index, result,
        )
        if reg_updates is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{op_name.upper()} {r8_name}, ({r32_name}) needs the owner "
                    f"register of {r8_name} fully known to write back."
                ),
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(result_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            flags_updates=flags_updates,
            note=(
                f"Executed {op_name} {r8_name}=0x{r8_value:02X}, "
                f"({r32_name}=0x{source_address:06X})=0x{mem_byte:02X}, C={carry} → "
                f"{result_name}=0x{result:02X}."
            ),
        )

    if (0x80 <= raw[1] <= 0x8F) or (0xA0 <= raw[1] <= 0xAF):
        # Pass 54 : arithmetic ALU on byte (R32) memory operand.
        # Sub-op layout (verified against NgpCraft_Disasm oracle) :
        #   0x80..0x87 = ADD R8, (R32)  — R8 ← R8 + mem
        #   0x88..0x8F = ADD (R32), R8  — mem ← mem + R8
        #   0xA0..0xA7 = SUB R8, (R32)  — R8 ← R8 - mem
        #   0xA8..0xAF = SUB (R32), R8  — mem ← mem - R8
        # Direction by bit 3 of op : 0=R8←, 1=mem←
        sub_op = raw[1]
        is_add = 0x80 <= sub_op <= 0x8F
        op_name = "add" if is_add else "sub"
        store_to_memory = bool(sub_op & 0x08)
        r8_index = sub_op & 0x07
        r8_name, r8_value = _extract_register_value(
            before_cpu, "byte", r8_index,
        )
        if r8_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-source-register",
                note=(
                    f"{op_name.upper()} on {r8_name}/({r32_name}) needs "
                    f"{r8_name} value (owner = {R32[r8_index // 2]}) modeled."
                ),
            )
        mem_byte = data_bytes[0]
        # ADD/SUB direction sets the "left = right op right" semantics.
        # For ADD (R8 ← R8+mem)   : flags from (R8 + mem), result = R8+mem mod 256
        # For ADD ((R32) ← mem+R8) : flags from (mem + R8), result = mem+R8 mod 256
        # For SUB (R8 ← R8-mem)   : flags from (R8 - mem)
        # For SUB ((R32) ← mem-R8) : flags from (mem - R8)
        if is_add:
            if store_to_memory:
                left, right = mem_byte, r8_value
            else:
                left, right = r8_value, mem_byte
            result = (left + right) & 0xFF
            flags_updates = _compute_add_flags("byte", left, right)
        else:  # sub
            if store_to_memory:
                left, right = mem_byte, r8_value
            else:
                left, right = r8_value, mem_byte
            result = (left - right) & 0xFF
            flags_updates = _compute_subtract_flags("byte", left, right)
        if store_to_memory:
            after_memory = dict(before_memory)
            after_memory[_mask_address(source_address)] = result
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=_mask_address(source_address),
                        data=bytes((result,)),
                        note=(
                            f"{op_name.upper()} ({r32_name}), {r8_name} : "
                            f"mem byte updated."
                        ),
                    ),
                ),
                after_memory=after_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed {op_name} ({r32_name}={source_address:#08x}), "
                    f"{r8_name}={r8_value:#04x} : "
                    f"mem {mem_byte:#04x} → {result:#04x}."
                ),
            )
        # R8 ← result
        result_name, reg_updates = _build_register_update(
            before_cpu, "byte", r8_index, result,
        )
        if reg_updates is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{op_name.upper()} {r8_name}, ({r32_name}) needs the "
                    f"owner register of {r8_name} fully known to write back."
                ),
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(result_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            flags_updates=flags_updates,
            note=(
                f"Executed {op_name} {r8_name}={r8_value:#04x}, "
                f"({r32_name}={source_address:#08x})={mem_byte:#04x} → "
                f"{result_name}={result:#04x}."
            ),
        )

    if 0xC0 <= raw[1] <= 0xEF:
        # Pass 53 : logical ALU on byte (R32) memory operand.
        # Sub-op layout (verified against NgpCraft_Disasm oracle) :
        #   0xC0..0xC7 = AND R8, (R32)  — direction: R8 ← R8 & mem
        #   0xC8..0xCF = AND (R32), R8  — direction: mem ← mem & R8
        #   0xD0..0xD7 = XOR R8, (R32)
        #   0xD8..0xDF = XOR (R32), R8
        #   0xE0..0xE7 = OR  R8, (R32)
        #   0xE8..0xEF = OR  (R32), R8
        # Operation by high nibble of (op - 0xC0) >> 4 :
        #   0,1 → AND ; 2,3 → XOR ; 4,5 → OR.
        # Direction by bit 3 of op : 0=R8←mem, 1=mem←R8.
        sub_op = raw[1]
        op_idx = (sub_op - 0xC0) >> 4   # 0..2
        op_name = ("and", "xor", "or")[op_idx]
        store_to_memory = bool(sub_op & 0x08)
        r8_index = sub_op & 0x07
        r8_name, r8_value = _extract_register_value(
            before_cpu, "byte", r8_index,
        )
        if r8_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-source-register",
                note=(
                    f"{op_name.upper()} on {r8_name}/({r32_name}) needs "
                    f"{r8_name} value (owner = {R32[r8_index // 2]}) modeled."
                ),
            )
        mem_byte = data_bytes[0]
        if op_name == "and":
            result = r8_value & mem_byte
        elif op_name == "or":
            result = r8_value | mem_byte
        else:  # xor
            result = r8_value ^ mem_byte
        flags_updates = _compute_logical_flags("byte", result)
        if store_to_memory:
            # mem ← result : update writable overlay at (R32).
            after_memory = dict(before_memory)
            after_memory[_mask_address(source_address)] = result & 0xFF
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=_mask_address(source_address),
                        data=bytes((result & 0xFF,)),
                        note=(
                            f"{op_name.upper()} ({r32_name}), {r8_name} : "
                            f"mem byte updated."
                        ),
                    ),
                ),
                after_memory=after_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed {op_name} ({r32_name}={source_address:#08x}), "
                    f"{r8_name}={r8_value:#04x} → mem={result & 0xFF:#04x}."
                ),
            )
        # else: R8 ← result.
        result_name, reg_updates = _build_register_update(
            before_cpu, "byte", r8_index, result & 0xFF,
        )
        if reg_updates is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{op_name.upper()} {r8_name}, ({r32_name}) needs the "
                    f"owner register of {r8_name} fully known to write back."
                ),
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(result_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            flags_updates=flags_updates,
            note=(
                f"Executed {op_name} {r8_name}={r8_value:#04x}, "
                f"({r32_name}={source_address:#08x})={mem_byte:#04x} → "
                f"{result_name}={result & 0xFF:#04x}."
            ),
        )

    dest_r8_index = raw[1] & 0x07
    dest_r8_name, reg_updates = _build_register_update(
        before_cpu, "byte", dest_r8_index, data_bytes[0]
    )
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"The owner register of {dest_r8_name} must be fully known to write the "
                "loaded byte back honestly."
            ),
        )

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(dest_r8_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        note=(
            f"Executed register-indirect byte load from the current real execution subset. "
            f"Byte 0x{data_bytes[0]:02X} read from ({r32_name})=0x{source_address:06X} "
            f"and written to {dest_r8_name}."
        ),
    )


def _try_execute_reg_indirect_word(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute word `(r32)` instructions on the 0x90..0x97 family."""
    raw = decoded.raw_bytes
    if raw is None or len(raw) not in (2, 4) or not (0x90 <= raw[0] <= 0x97):
        return None

    op = raw[1]
    muldiv_mode = {
        0x40: "mul",
        0x48: "muls",
        0x50: "div",
        0x58: "divs",
    }.get(op & 0xF8)
    if muldiv_mode is not None:
        base_register_index = raw[0] & 0x07
        base_register_name = R32[base_register_index]
        base_address = getattr(before_cpu.regs, REG32_FIELDS[base_register_index])
        if base_address is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-address-register",
                note=(
                    f"{base_register_name} must be known before this word register-indirect {muldiv_mode} "
                    "can compute its effective address honestly."
                ),
            )

        destination_index = op & 0x07
        destination_name = R32[destination_index]
        destination_value = getattr(before_cpu.regs, REG32_FIELDS[destination_index])
        if destination_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{destination_name} must be known before this word register-indirect {muldiv_mode} "
                    "can read its operand half honestly."
                ),
            )

        source_address = _mask_address(base_address)
        mem_bytes = _read_runtime_bytes(view, before_memory, source_address, 2)
        if mem_bytes is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    f"This word register-indirect {muldiv_mode} needs 2 readable bytes at "
                    f"({base_register_name})=0x{source_address:06X}, but neither the writable "
                    "runtime overlay nor the current read bus can provide them."
                ),
            )

        return _execute_word_memory_muldiv_common(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            mode=muldiv_mode,
            destination_index=destination_index,
            destination_value=destination_value,
            memory_word=int.from_bytes(mem_bytes, "little"),
            operand_description=f"({base_register_name})",
        )

    r32_index = raw[0] & 0x07
    r32_name = R32[r32_index]
    r32_field = REG32_FIELDS[r32_index]
    r32_value = getattr(before_cpu.regs, r32_field)
    if r32_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{r32_name} must be known before this word register-indirect form can compute "
                "its effective address honestly."
            ),
        )

    source_address = _mask_address(r32_value)
    mem_bytes = _read_runtime_bytes(view, before_memory, source_address, 2)
    if mem_bytes is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This word register-indirect form needs 2 readable bytes at ({r32_name})="
                f"0x{source_address:06X}, but neither the writable runtime overlay nor the "
                "current read bus can provide them."
            ),
        )

    op = raw[1]
    mem_value = int.from_bytes(mem_bytes, "little")

    if 0x20 <= op <= 0x27:
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="word",
            register_index=op & 0x07,
            value=mem_value,
            note=(
                "Executed register-indirect word load from the current real execution subset. "
                "Two bytes were read from the writable runtime overlay or the current read bus."
            ),
        )

    if 0x30 <= op <= 0x37:
        register_index = op & 0x07
        reg_name, reg_value = _extract_register_value(before_cpu, "word", register_index)
        if reg_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-source-register",
                note=(
                    f"EX ({r32_name}), {reg_name} needs the value of {reg_name} modeled "
                    "before it can swap honestly."
                ),
            )
        result_name, reg_updates = _build_register_update(before_cpu, "word", register_index, mem_value)
        if reg_updates is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"EX ({r32_name}), {reg_name} needs the owner register of {reg_name} fully "
                    "known to write back."
                ),
            )
        after_memory = dict(before_memory)
        stored = reg_value.to_bytes(2, "little")
        after_memory[source_address] = stored[0]
        after_memory[_mask_address(source_address + 1)] = stored[1]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(result_name, "PC"),
            memory_writes=(
                MemoryWrite(
                    address=source_address,
                    data=stored,
                    note=f"EX ({r32_name}), {reg_name} : mem word updated.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            flags_updates=None,
            note=(
                f"Executed ex ({r32_name}=0x{source_address:06X})=0x{mem_value:04X}, "
                f"{reg_name}=0x{reg_value:04X}."
            ),
        )

    if len(raw) == 4 and 0x38 <= op <= 0x3F:
        operation = {
            0x38: "add",
            0x39: "adc",
            0x3A: "sub",
            0x3B: "sbc",
            0x3C: "and",
            0x3D: "xor",
            0x3E: "or",
            0x3F: "cp",
        }[op]
        if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{operation.upper()} on word register-indirect memory requires a known carry "
                    "flag, which is not modeled in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
        imm = int.from_bytes(raw[2:4], "little")
        if operation == "add":
            result = (mem_value + imm) & 0xFFFF
            flags_updates = _compute_add_flags("word", mem_value, imm)
        elif operation == "adc":
            result = (mem_value + imm + carry) & 0xFFFF
            flags_updates = _compute_add_flags("word", mem_value, imm + carry)
        elif operation == "sub":
            result = (mem_value - imm) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", mem_value, imm)
        elif operation == "sbc":
            result = (mem_value - imm - carry) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", mem_value, imm + carry)
        elif operation == "and":
            result = mem_value & imm
            flags_updates = _compute_logical_flags("word", result)
        elif operation == "xor":
            result = mem_value ^ imm
            flags_updates = _compute_logical_flags("word", result)
        elif operation == "or":
            result = mem_value | imm
            flags_updates = _compute_logical_flags("word", result)
        else:
            result = (mem_value - imm) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", mem_value, imm)

        if operation == "cp":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    "Executed word register-indirect compare-immediate from the current real "
                    "execution subset."
                ),
            )

        result_bytes = result.to_bytes(2, "little")
        write_status, write_note = _check_writable_range(view, source_address, 2)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(address=source_address, data=result_bytes, note=f"[DISCARDED] {write_note}"),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note="Word register-indirect immediate ALU write was discarded.",
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )
        after_memory = dict(before_memory)
        after_memory[source_address] = result_bytes[0]
        after_memory[_mask_address(source_address + 1)] = result_bytes[1]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=source_address,
                    data=result_bytes,
                    note=f"Word register-indirect {operation.upper()} immediate updated memory.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=f"Executed {operation} ({r32_name}), imm16.",
        )

    if 0x60 <= op <= 0x6F:
        count = op & 0x07
        if count == 0:
            count = 8
        is_dec = op >= 0x68
        if is_dec:
            result = (mem_value - count) & 0xFFFF
            flags_updates = dict(_compute_subtract_flags("word", mem_value, count))
        else:
            result = (mem_value + count) & 0xFFFF
            flags_updates = dict(_compute_add_flags("word", mem_value, count))
        flags_updates.pop("cf", None)
        result_bytes = result.to_bytes(2, "little")
        write_status, write_note = _check_writable_range(view, source_address, 2)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(MemoryWrite(address=source_address, data=result_bytes, note=f"[DISCARDED] {write_note}"),),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note="Word register-indirect INC/DEC write was discarded.",
            )
        if write_status is not None:
            return _blocked_result(before_cpu=before_cpu, decoded=decoded, status=write_status, note=write_note)
        after_memory = dict(before_memory)
        after_memory[source_address] = result_bytes[0]
        after_memory[_mask_address(source_address + 1)] = result_bytes[1]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(MemoryWrite(address=source_address, data=result_bytes, note="Word register-indirect INC/DEC updated memory."),),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=f"Executed {'dec' if is_dec else 'inc'} {count}, ({r32_name}).",
        )

    if 0x80 <= op <= 0xFF:
        operation = {
            0x8: "add",
            0x9: "adc",
            0xA: "sub",
            0xB: "sbc",
            0xC: "and",
            0xD: "xor",
            0xE: "or",
            0xF: "cp",
        }.get(op >> 4)
        if operation is None:
            return None
        register_index = op & 0x07
        register_name, register_value = _extract_register_value(before_cpu, "word", register_index)
        if register_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-source-register",
                note=(
                    f"{operation.upper()} on {register_name}/({r32_name}) needs {register_name} "
                    "modeled in the current CPU state."
                ),
            )
        if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{operation.upper()} on word register-indirect memory requires a known carry flag, "
                    "which is not modeled in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
        store_to_memory = bool(op & 0x08)
        register_is_left = not store_to_memory
        left_value = register_value if register_is_left else mem_value
        right_value = mem_value if register_is_left else register_value

        if operation == "add":
            result = (left_value + right_value) & 0xFFFF
            flags_updates = _compute_add_flags("word", left_value, right_value)
        elif operation == "adc":
            result = (left_value + right_value + carry) & 0xFFFF
            flags_updates = _compute_add_flags("word", left_value, right_value + carry)
        elif operation == "sub":
            result = (left_value - right_value) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", left_value, right_value)
        elif operation == "sbc":
            result = (left_value - right_value - carry) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", left_value, right_value + carry)
        elif operation == "and":
            result = left_value & right_value
            flags_updates = _compute_logical_flags("word", result)
        elif operation == "xor":
            result = left_value ^ right_value
            flags_updates = _compute_logical_flags("word", result)
        elif operation == "or":
            result = left_value | right_value
            flags_updates = _compute_logical_flags("word", result)
        else:
            result = (left_value - right_value) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", left_value, right_value)

        if operation == "cp":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=f"Executed {operation} on word register-indirect memory.",
            )

        if store_to_memory:
            result_bytes = result.to_bytes(2, "little")
            write_status, write_note = _check_writable_range(view, source_address, 2)
            if write_status == "write-discarded":
                return _executed_result(
                    before_cpu=before_cpu,
                    decoded=decoded,
                    written_registers=("PC",),
                    memory_writes=(MemoryWrite(address=source_address, data=result_bytes, note=f"[DISCARDED] {write_note}"),),
                    after_memory=before_memory,
                    new_pc=decoded.next_sequential_pc,
                    reg_updates=None,
                    flags_updates=flags_updates,
                    note="Word register-indirect ALU write was discarded.",
                )
            if write_status is not None:
                return _blocked_result(before_cpu=before_cpu, decoded=decoded, status=write_status, note=write_note)
            after_memory = dict(before_memory)
            after_memory[source_address] = result_bytes[0]
            after_memory[_mask_address(source_address + 1)] = result_bytes[1]
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(MemoryWrite(address=source_address, data=result_bytes, note=f"Word register-indirect {operation.upper()} updated memory."),),
                after_memory=after_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=f"Executed {operation} ({r32_name}), {register_name}.",
            )

        result_name, reg_updates = _build_register_update(before_cpu, "word", register_index, result)
        if reg_updates is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{operation.upper()} {register_name}, ({r32_name}) needs the owner register "
                    f"of {register_name} fully known to write back."
                ),
            )
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(result_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            flags_updates=flags_updates,
            note=f"Executed {operation} {register_name}, ({r32_name}).",
        )

    return None


def _try_execute_reg_indirect_store(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute (r32) register-indirect stores.

    Encoding: B0..B7 = prefix (address r32 = byte & 0x07), then op byte:
      40..47 xx    => ld  (r32), R8   — 2 bytes
      50..57       => ldw (r32), R16  — 2 bytes
      60..67       => ld  (r32), R32  — 2 bytes
      00 xx        => ld  (r32), imm8 — 3 bytes
      02 xx xx     => ldw (r32), imm16 — 4 bytes
    """
    raw = decoded.raw_bytes
    if raw is None or not (0xB0 <= raw[0] <= 0xB7):
        return None

    register_index = raw[0] & 0x07
    addr_r32_name = R32[register_index]
    addr_r32_field = REG32_FIELDS[register_index]
    addr_value = getattr(before_cpu.regs, addr_r32_field)
    if addr_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{addr_r32_name} must be known to resolve the target address for this "
                "register-indirect store."
            ),
        )

    target_address = _mask_address(addr_value)

    if len(raw) == 2 and 0x30 <= raw[1] <= 0x37:
        # lda Rdst, (Rbase): Rdst = current Rbase value (effective address).
        # No memory access, no flag update.
        dest_index = raw[1] & 0x07
        dest_name = R32[dest_index]
        dest_field = REG32_FIELDS[dest_index]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(dest_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates={dest_field: addr_value & 0xFFFFFFFF},
            note=(
                f"Executed lda {dest_name}, ({addr_r32_name}): "
                f"{dest_name}={addr_r32_name}=0x{addr_value:08X}."
            ),
        )

    if len(raw) == 2 and 0x40 <= raw[1] <= 0x47:
        src_name, src_value = _extract_register_value(before_cpu, "byte", raw[1] & 0x07)
        if src_value is None:
            owner = R32[(raw[1] & 0x07) // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{src_name} cannot be stored honestly until {owner} is already known "
                    "in the current CPU state."
                ),
            )
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=bytes((src_value & 0xFF,)),
            note=(
                f"Executed register-indirect byte store ld ({addr_r32_name}), {src_name}. "
                "The source byte was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by register-indirect byte store.",
        )

    if len(raw) == 2 and 0x50 <= raw[1] <= 0x57:
        src_name, src_value = _extract_register_value(before_cpu, "word", raw[1] & 0x07)
        if src_value is None:
            owner = R32[raw[1] & 0x07]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{src_name} cannot be stored honestly until {owner} is already known "
                    "in the current CPU state."
                ),
            )
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=(src_value & 0xFFFF).to_bytes(2, "little"),
            note=(
                f"Executed register-indirect word store ldw ({addr_r32_name}), {src_name}. "
                "The source word was written to the writable runtime overlay (little-endian)."
            ),
            memory_note="Writable runtime overlay updated by register-indirect word store.",
        )

    if len(raw) == 2 and 0x60 <= raw[1] <= 0x67:
        src_name, src_value = _extract_register_value(before_cpu, "long", raw[1] & 0x07)
        if src_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{src_name} cannot be stored honestly until its current full value is "
                    "known in the CPU state."
                ),
            )
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=src_value.to_bytes(4, "little"),
            note=(
                f"Executed register-indirect long store ld ({addr_r32_name}), {src_name}. "
                "The 32-bit source value was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by register-indirect long store.",
        )

    if len(raw) == 3 and raw[1] == 0x00:
        imm8 = raw[2]
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=bytes((imm8,)),
            note=(
                "Executed register-indirect byte store (ld (r32), imm8). "
                "The immediate byte was written to the writable runtime overlay."
            ),
            memory_note="Writable runtime overlay updated by register-indirect byte store.",
        )

    if len(raw) == 4 and raw[1] == 0x02:
        imm16 = int.from_bytes(raw[2:4], "little")
        return _execute_absolute_store(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_address=target_address,
            data=imm16.to_bytes(2, "little"),
            note=(
                "Executed register-indirect word store (ldw (r32), imm16). "
                "The immediate word was written to the writable runtime overlay (little-endian)."
            ),
            memory_note="Writable runtime overlay updated by register-indirect word store.",
        )

    return None


def _try_execute_prefixed_register_ld(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    size_kind, first_register_index = info
    second = raw[1]
    if 0x88 <= second <= 0x8F:
        destination_index = second & 0x07
        source_index = first_register_index
    elif 0x98 <= second <= 0x9F:
        destination_index = first_register_index
        source_index = second & 0x07
    else:
        return None

    source_register_name, source_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=source_index,
    )
    if source_value is None:
        owner_name = R32[source_index // 2] if size_kind == "byte" else R32[source_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{source_register_name} cannot be copied honestly until {owner_name} is already "
                "known in the current CPU state."
            ),
        )

    destination_register_name, reg_updates = _build_register_update(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=destination_index,
        value=source_value,
    )
    if reg_updates is None:
        owner_name = R32[destination_index // 2] if size_kind == "byte" else R32[destination_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{destination_register_name} cannot be updated honestly until {owner_name} is "
                "already known in the current CPU state."
            ),
        )

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(destination_register_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        note=(
            "Executed prefixed register-to-register load from the current real execution "
            "subset. The destination register view now mirrors the known source register value."
        ),
    )


def _try_execute_prefixed_compare(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    size_kind, right_index = info
    second = raw[1]
    if not (0xF0 <= second <= 0xF7):
        return None

    left_index = second & 0x07
    left_name, left_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=left_index,
    )
    if left_value is None:
        owner_name = R32[left_index // 2] if size_kind == "byte" else R32[left_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{left_name} cannot be compared honestly until {owner_name} is already known "
                "in the current CPU state."
            ),
        )

    right_name, right_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=right_index,
    )
    if right_value is None:
        owner_name = R32[right_index // 2] if size_kind == "byte" else R32[right_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{right_name} cannot be compared honestly until {owner_name} is already known "
                "in the current CPU state."
            ),
        )

    flags_updates = _compute_subtract_flags(size_kind, left_value, right_value)
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags_updates,
        note=(
            "Executed prefixed compare from the current real execution subset. No register "
            "value changed, but the modeled flag subset now reflects the subtraction-style "
            "compare result."
        ),
    )


def _try_execute_stack_or_call(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None:
        return None

    first = raw[0]

    if first == 0x0B and len(raw) == 3:
        imm16 = raw[1:3]
        return _execute_push_bytes(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            data=imm16,
            note=(
                "Executed PUSHW immediate from the current real execution subset. The immediate "
                "word was written to the current writable stack model."
            ),
        )

    if 0x28 <= first <= 0x2F and len(raw) == 1:
        register_index = first & 0x07
        if register_index == 7:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-stack-pointer-alias",
                note=(
                    "PUSH SP is not implemented yet because the current subset does not model "
                    "the self-referential stack-pointer alias semantics carefully enough."
                ),
            )
        register_name, value = _extract_register_value(before_cpu, "word", register_index)
        if value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{register_name} cannot be pushed honestly until its owning 32-bit register "
                    "is known in the current CPU state."
                ),
            )
        return _execute_push_bytes(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            data=value.to_bytes(2, "little"),
            note=(
                "Executed PUSH R16 from the current real execution subset. The current register "
                "value was written to the writable stack model."
            ),
        )

    if 0x38 <= first <= 0x3F and len(raw) == 1:
        register_index = first & 0x07
        if register_index == 7:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-stack-pointer-alias",
                note=(
                    "PUSH XSP is not implemented yet because the current subset does not model "
                    "the self-referential stack-pointer semantics carefully enough."
                ),
            )
        register_name, value = _extract_register_value(before_cpu, "long", register_index)
        if value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{register_name} cannot be pushed honestly until its current full value is "
                    "known in the CPU state."
                ),
            )
        return _execute_push_bytes(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            data=value.to_bytes(4, "little"),
            note=(
                "Executed PUSH R32 from the current real execution subset. The current register "
                "value was written to the writable stack model."
            ),
        )

    if 0x48 <= first <= 0x4F and len(raw) == 1:
        register_index = first & 0x07
        if register_index == 7:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-stack-pointer-alias",
                note=(
                    "POP SP is not implemented yet because the current subset does not model "
                    "that stack-pointer alias case carefully enough."
                ),
            )
        return _execute_pop_register(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="word",
            register_index=register_index,
        )

    if 0x58 <= first <= 0x5F and len(raw) == 1:
        register_index = first & 0x07
        if register_index == 7:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-stack-pointer-alias",
                note=(
                    "POP XSP is not implemented yet because the current subset does not model "
                    "that stack-pointer alias case carefully enough."
                ),
            )
        return _execute_pop_register(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="long",
            register_index=register_index,
        )

    if first in (0x1C, 0x1D, 0x1E) and decoded.direct_target is not None:
        if decoded.next_sequential_pc is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unsupported-decoded-instruction",
                note="CALL-like instruction has no sequential return site in the current decode payload.",
            )
        return _execute_call(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_pc=decoded.direct_target,
            return_pc=decoded.next_sequential_pc,
        )

    if first == 0xB4 and len(raw) == 2 and raw[1] == 0xE8:
        if decoded.next_sequential_pc is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unsupported-decoded-instruction",
                note="Indirect CALL via XIX has no sequential return site in the current decode payload.",
            )
        target_pc = before_cpu.regs.xix
        if target_pc is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    "CALL (XIX) needs XIX modeled in the current CPU state so the indirect "
                    "target can be computed honestly."
                ),
            )
        return _execute_call(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            target_pc=_mask_address(target_pc),
            return_pc=decoded.next_sequential_pc,
        )

    if first == 0x0E:
        return _execute_return(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            stack_adjust=0,
            note=(
                "Executed RET from the current real execution subset. PC was restored from the "
                "writable stack model."
            ),
        )

    if first == 0x0F and len(raw) == 3:
        stack_adjust = _signed_u16(raw[1:3])
        return _execute_return(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            stack_adjust=stack_adjust,
            note=(
                "Executed RETD from the current real execution subset. PC was restored from the "
                "writable stack model and XSP was adjusted by the decoded immediate."
            ),
        )

    return None


def _try_execute_indexed_store(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None

    first = raw[0]
    if not (0xB8 <= first <= 0xBF):
        return None

    store_opcode = raw[2]
    if 0x40 <= store_opcode <= 0x47:
        size_kind = "byte"
    elif 0x50 <= store_opcode <= 0x57:
        size_kind = "word"
    elif 0x60 <= store_opcode <= 0x67:
        size_kind = "long"
    else:
        return None

    address_register_index = first & 0x07
    base_address = getattr(before_cpu.regs, REG32_FIELDS[address_register_index])
    address_register_name = R32[address_register_index]
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{address_register_name} must be known before this indexed store can compute "
                "its effective address honestly."
            ),
        )

    source_register_index = store_opcode & 0x07
    source_register_name, source_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=source_register_index,
    )
    if source_value is None:
        owner_name = R32[source_register_index // 2] if size_kind == "byte" else R32[source_register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{source_register_name} cannot be stored honestly until {owner_name} is already "
                "known in the current CPU state."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = (base_address + displacement) & 0xFFFFFFFF
    target_address = _mask_address(effective_address)
    width = {"byte": 1, "word": 2, "long": 4}[size_kind]
    data = source_value.to_bytes(width, "little")
    write_status, write_note = _check_writable_range(view, target_address, width)
    if write_status == "write-discarded":
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=data,
                    note=f"[DISCARDED] {write_note}",
                ),
            ),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            note=(
                "Indexed store destination was unmapped or read-only; write silently "
                "discarded (open-bus behavior — execution continues)."
            ),
        )
    if write_status is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=write_status,
            note=write_note,
        )

    after_memory = dict(before_memory)
    for offset, value in enumerate(data):
        after_memory[_mask_address(target_address + offset)] = value

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(
            MemoryWrite(
                address=target_address,
                data=data,
                note="Writable runtime overlay updated by indexed store execution.",
            ),
        ),
        after_memory=after_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        note=(
            "Executed indexed store from the current real execution subset. The effective "
            "address was computed from the known address register plus displacement and the "
            "bytes were written to the writable runtime overlay."
        ),
    )


def _try_execute_indexed_imm_store(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute (r32+d8) immediate stores: ld (r32+d8), imm8 and ldw (r32+d8), imm16.

    Encoding:
      [B8+r] [d8] [00] [imm8]        => ld  (r32+d8), imm8   (4 bytes)
      [B8+r] [d8] [02] [lo] [hi]     => ldw (r32+d8), imm16  (5 bytes)
    """
    raw = decoded.raw_bytes
    if raw is None or not (0xB8 <= raw[0] <= 0xBF):
        return None

    op = raw[2] if len(raw) >= 3 else None
    if op == 0x00 and len(raw) == 4:
        width, imm = 1, raw[3]
        data_bytes = bytes((imm,))
    elif op == 0x02 and len(raw) == 5:
        width, imm = 2, int.from_bytes(raw[3:5], "little")
        data_bytes = imm.to_bytes(2, "little")
    else:
        return None

    r32_index = raw[0] & 0x07
    r32_field = REG32_FIELDS[r32_index]
    r32_name = R32[r32_index]
    base_address = getattr(before_cpu.regs, r32_field)
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{r32_name} must be known before this indexed immediate store can compute "
                "its effective address honestly."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = (base_address + displacement) & 0xFFFFFFFF
    target_address = _mask_address(effective_address)
    return _execute_absolute_store(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
        target_address=target_address,
        data=data_bytes,
        note=(
            f"Executed indexed immediate {'byte' if width == 1 else 'word'} store from the "
            f"current real execution subset. Address {r32_name}+{displacement}="
            f"0x{target_address:06X} written with immediate 0x{imm:0{width*2}X}."
        ),
        memory_note=(
            "Writable runtime overlay updated by indexed immediate store execution."
        ),
    )


def _try_execute_indexed_load(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None

    first = raw[0]

    # lda R32, (r32+d8) — effective address form (B8..BF, op 30..37)
    if 0xB8 <= first <= 0xBF:
        load_opcode = raw[2]
        if not (0x30 <= load_opcode <= 0x37):
            return None
        address_register_index = first & 0x07
        address_register_name = R32[address_register_index]
        base_address = getattr(before_cpu.regs, REG32_FIELDS[address_register_index])
        if base_address is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-address-register",
                note=(
                    f"{address_register_name} must be known before this indexed lda can "
                    "compute its effective address honestly."
                ),
            )
        displacement = _signed_u8(raw[1])
        effective_address = (base_address + displacement) & 0xFFFFFF
        destination_index = load_opcode & 0x07
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind="long",
            register_index=destination_index,
            value=effective_address,
            note=(
                "Executed indexed lda (load effective address) from the current real execution "
                "subset. The effective address was computed from the base register plus "
                "displacement and stored directly as a 32-bit value."
            ),
        )

    if 0x88 <= first <= 0x8F:
        size_kind = "byte"
    elif 0x98 <= first <= 0x9F:
        size_kind = "word"
    elif 0xA8 <= first <= 0xAF:
        size_kind = "long"
    else:
        return None

    load_opcode = raw[2]
    if not (0x20 <= load_opcode <= 0x27):
        return None

    address_register_index = first & 0x07
    base_address = getattr(before_cpu.regs, REG32_FIELDS[address_register_index])
    address_register_name = R32[address_register_index]
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{address_register_name} must be known before this indexed load can compute "
                "its effective address honestly."
            ),
        )

    destination_index = load_opcode & 0x07
    displacement = _signed_u8(raw[1])
    effective_address = (base_address + displacement) & 0xFFFFFFFF
    width = {"byte": 1, "word": 2, "long": 4}[size_kind]
    data = _read_runtime_bytes(view, before_memory, _mask_address(effective_address), width)
    if data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                "This indexed load needs readable bytes at its effective address, but neither "
                "the writable runtime overlay nor the current read bus can provide them."
            ),
        )

    return _execute_register_immediate(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
        size_kind=size_kind,
        register_index=destination_index,
        value=int.from_bytes(data, "little"),
        note=(
            "Executed indexed load from the current real execution subset. The effective "
            "address was computed from the known address register plus displacement and the "
            "loaded bytes came from the writable runtime overlay or read bus."
        ),
    )


def _try_execute_secondary_indexed_load(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 5 or raw[0] not in (0xC3, 0xD3, 0xE3):
        return None

    secondary = raw[1]
    mode = secondary & 0x03
    if mode != 0x03 or not (0x20 <= raw[4] <= 0x27):
        return None

    base_index = (raw[2] >> 2) & 0x07
    base_name = R32[base_index]
    base_value = getattr(before_cpu.regs, REG32_FIELDS[base_index])
    if base_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_name} must be known before this secondary-indexed load can "
                "compute its effective address honestly."
            ),
        )

    if secondary & 0x04:
        index_kind = "word"
        index_index = (raw[3] >> 2) & 0x07
    else:
        index_kind = "byte"
        index_index = (raw[3] >> 2) & 0x07

    index_name, index_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind=index_kind,
        register_index=index_index,
    )
    if index_value is None and index_kind == "byte":
        index_value = _extract_current_banked_r8_value(before_cpu, index_index)
    if index_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-source-register",
            note=(
                f"{index_name} must be known before this secondary-indexed load can "
                "compute its effective address honestly."
            ),
        )

    destination_index = raw[4] & 0x07
    effective_address = _mask_address((base_value + index_value) & 0xFFFFFFFF)
    if raw[0] == 0xC3:
        size_kind = "byte"
        width = 1
    elif raw[0] == 0xD3:
        size_kind = "word"
        width = 2
    else:
        size_kind = "long"
        width = 4

    data = _read_runtime_bytes(view, before_memory, effective_address, width)
    if data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This secondary-indexed {size_kind} load needs {width} readable byte(s) at its effective "
                "address, but neither the writable runtime overlay nor the current read bus "
                "can provide them."
            ),
        )

    return _execute_register_immediate(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
        size_kind=size_kind,
        register_index=destination_index,
        value=int.from_bytes(data, "little"),
        note=(
            f"Executed secondary-indexed {size_kind} load from the current real execution subset. "
            f"EA = {base_name}(0x{base_value:06X}) + {index_name}(0x{index_value:X}) = "
            f"0x{effective_address:06X}; {width} byte(s) were read from the writable runtime overlay "
            "or current read bus into the destination register."
        ),
    )


def _try_execute_secondary_indexed_jump(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 5 or raw[0] != 0xF3 or raw[4] != 0xD8:
        return None

    secondary = raw[1]
    if (secondary & 0x03) != 0x03:
        return None

    base_index = (raw[2] >> 2) & 0x07
    base_name = R32[base_index]
    base_value = getattr(before_cpu.regs, REG32_FIELDS[base_index])
    if base_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_name} must be known before this secondary-indexed jump can compute "
                "its target honestly."
            ),
        )

    if secondary & 0x04:
        index_kind = "word"
        index_index = (raw[3] >> 2) & 0x07
    else:
        index_kind = "byte"
        index_index = (raw[3] >> 2) & 0x07

    index_name, index_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind=index_kind,
        register_index=index_index,
    )
    if index_value is None and index_kind == "byte":
        index_value = _extract_current_banked_r8_value(before_cpu, index_index)
    if index_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-source-register",
            note=(
                f"{index_name} must be known before this secondary-indexed jump can compute "
                "its target honestly."
            ),
        )

    target_address = _mask_address((base_value + index_value) & 0xFFFFFFFF)
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=target_address,
        reg_updates=None,
        flags_updates=None,
        note=(
            f"Executed secondary-indexed jump from the current real execution subset. "
            f"Target = {base_name}(0x{base_value:06X}) + {index_name}(0x{index_value:X}) = "
            f"0x{target_address:06X}."
        ),
    )


def _execute_word_memory_muldiv_common(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    *,
    mode: str,
    destination_index: int,
    destination_value: int,
    memory_word: int,
    operand_description: str,
) -> ExecutionResult:
    """Execute word-memory MUL/MULS/DIV/DIVS into a 32-bit destination register."""

    def _to_signed(value: int, bits: int) -> int:
        sign_bit = 1 << (bits - 1)
        mask = (1 << bits) - 1
        value &= mask
        if value & sign_bit:
            return value - (1 << bits)
        return value

    destination_name = R32[destination_index]
    destination_field = REG32_FIELDS[destination_index]

    if mode == "mul":
        left = destination_value & 0xFFFF
        right = memory_word
        raw_result = left * right
        result = raw_result & 0xFFFFFFFF
        flags_updates = {
            "zf": result == 0,
            "sf": bool(result & 0x80000000),
            "cf": raw_result > 0xFFFFFFFF,
            "vf": raw_result > 0xFFFFFFFF,
        }
        note = (
            f"Executed {mode}: ({destination_name} & 0xFFFF)=0x{left:04X} * "
            f"{operand_description}=0x{right:04X} -> 0x{result:08X}."
        )
    elif mode == "muls":
        left_signed = _to_signed(destination_value, 16)
        right_signed = _to_signed(memory_word, 16)
        raw_result = left_signed * right_signed
        result = raw_result & 0xFFFFFFFF
        flags_updates = {
            "zf": result == 0,
            "sf": bool(result & 0x80000000),
            "cf": False,
            "vf": False,
        }
        note = (
            f"Executed {mode}: signed16({destination_name})={left_signed} * "
            f"signed16({operand_description})={right_signed} -> 0x{result:08X}."
        )
    elif mode in ("div", "divs"):
        if memory_word == 0:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="division-by-zero",
                note=(
                    f"{mode.upper()} by zero is not modeled honestly: TLCS-900/H sets VF and "
                    "the packed destination result is not something this emulator should guess."
                ),
            )

        if mode == "div":
            quotient_full = destination_value // memory_word
            remainder_full = destination_value % memory_word
            overflow = quotient_full > 0xFFFF
        else:
            signed_dividend = _to_signed(destination_value, 32)
            signed_divisor = _to_signed(memory_word, 16)
            quotient_full = int(signed_dividend / signed_divisor)
            remainder_full = signed_dividend - (quotient_full * signed_divisor)
            overflow = quotient_full < -0x8000 or quotient_full > 0x7FFF

        quotient_packed = quotient_full & 0xFFFF
        remainder_packed = remainder_full & 0xFFFF
        result = (remainder_packed << 16) | quotient_packed
        flags_updates = {
            "zf": quotient_packed == 0,
            "sf": bool(quotient_packed & 0x8000),
            "cf": False,
            "vf": overflow,
        }
        note = (
            f"Executed {mode}: {destination_name}=0x{destination_value:08X} / "
            f"{operand_description}=0x{memory_word:04X} -> quot=0x{quotient_packed:04X}, "
            f"rem=0x{remainder_packed:04X}, packed=0x{result:08X}."
        )
    else:
        raise ValueError(f"Unsupported word-memory mul/div mode: {mode}")

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(destination_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates={destination_field: result},
        flags_updates=flags_updates,
        note=note,
    )


def _try_execute_indexed_word_muldiv(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Word-indexed memory multiply and unsigned divide.

    Encoding (3 bytes):
      [0x98+r_base] [d8] [op]
        op in 0x40..0x47 → mul XRdst, (Rbase+d8)
        op in 0x50..0x57 → div XRdst, (Rbase+d8)

    Semantics:
      mul: XRdst = (HLdst(low16) * mem_word) & 0xFFFFFFFF.
           Both operands unsigned. The 32-bit product replaces XRdst.
      div: dividend = XRdst (full 32-bit), divisor = mem_word.
           quotient (low 16) and remainder (upper 16) merge into XRdst.
           Division by zero blocks honestly: the CPU sets VF and skips
           the write-back on real hardware, but this implementation
           refuses rather than guessing which guard cc900 relied on.

    Catalog reference: t900cc.py jalon 6 (HW-validated) lists these
    encodings as the safe replacement for the broken D8+r+r 32-bit
    register-register multiplications.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None
    first = raw[0]
    if not (0x98 <= first <= 0x9F):
        return None
    op = raw[2]
    mode = {
        0x40: "mul",
        0x48: "muls",
        0x50: "div",
        0x58: "divs",
    }.get(op & 0xF8)
    if mode is None:
        return None

    base_register_index = first & 0x07
    base_register_name = R32[base_register_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[base_register_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_register_name} must be known before this indexed {mode} can compute "
                "its effective address honestly."
            ),
        )

    destination_index = op & 0x07
    dest_long_name = R32[destination_index]
    dest_field = REG32_FIELDS[destination_index]
    dest_long_value = getattr(before_cpu.regs, dest_field)
    if dest_long_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{dest_long_name} must be known before this indexed {mode} can read its "
                "operand half honestly."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = (base_address + displacement) & 0xFFFFFFFF
    data = _read_runtime_bytes(view, before_memory, _mask_address(effective_address), 2)
    if data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed {mode} needs 2 readable bytes at its effective address, but "
                "neither the writable runtime overlay nor the read bus can provide them."
            ),
        )
    mem_word = int.from_bytes(data, "little")

    return _execute_word_memory_muldiv_common(
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
        mode=mode,
        destination_index=destination_index,
        destination_value=dest_long_value,
        memory_word=mem_word,
        operand_description=f"mem16(0x{effective_address:06X})",
    )


def _try_execute_indexed_push(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute push/pushw/pushl (r32+d8) — push memory-indexed value onto stack.

    Encoding: [80+zz+mem_r] [d8] [04]
      - 0x88..0x8F + op=0x04 → push  (byte,  1 byte)
      - 0x98..0x9F + op=0x04 → pushw (word,  2 bytes)
      - 0xA8..0xAF + op=0x04 → pushl (long,  4 bytes)

    Catalog: 80 + zz + mem : 04 → (−XSP) ← (mem)
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None

    first = raw[0]
    if 0x88 <= first <= 0x8F:
        width = 1
    elif 0x98 <= first <= 0x9F:
        width = 2
    elif 0xA8 <= first <= 0xAF:
        width = 4
    else:
        return None

    if raw[2] != 0x04:
        return None

    address_register_index = first & 0x07
    address_register_name = R32[address_register_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[address_register_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{address_register_name} must be known before this indexed push can compute "
                "its effective address honestly."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    data = _read_runtime_bytes(view, before_memory, effective_address, width)
    if data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                "This indexed push needs readable bytes at its effective address, but neither "
                "the writable runtime overlay nor the current read bus can provide them."
            ),
        )

    return _execute_push_bytes(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
        data=data,
        note=(
            "Executed indexed push from the current real execution subset. The effective "
            "address was computed from the known address register plus displacement; the "
            "loaded bytes were pushed onto the stack."
        ),
    )


def _try_execute_post_increment_byte(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) not in (3, 4, 5) or raw[0] not in (0xC5, 0xD5, 0xE5, 0xF5):
        return None

    address_register_index = _post_increment_r32_index(raw[1])
    address_register_name = R32[address_register_index]
    address_field = REG32_FIELDS[address_register_index]
    base_address = getattr(before_cpu.regs, address_field)
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{address_register_name} must be known before this post-increment memory form "
                "can compute its effective address honestly."
            ),
        )

    target_address = _mask_address(base_address)
    advanced_address = (base_address + 1) & 0xFFFFFFFF

    if raw[0] in (0xC5, 0xD5, 0xE5) and len(raw) == 3 and 0x20 <= raw[2] <= 0x27:
        size_kind = {0xC5: "byte", 0xD5: "word", 0xE5: "long"}[raw[0]]
        width = {"byte": 1, "word": 2, "long": 4}[size_kind]
        destination_index = raw[2] & 0x07
        if size_kind == "byte":
            destination_field = REG32_FIELDS[destination_index // 2]
            destination_name = R8[destination_index]
        else:
            destination_field = REG32_FIELDS[destination_index]
            destination_name = R16[destination_index] if size_kind == "word" else R32[destination_index]
        if destination_field == address_field:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-register-alias-side-effects",
                note=(
                    f"{destination_name} aliases {address_register_name}, and this post-increment "
                    f"{size_kind} load would need alias ordering the current subset does not model yet."
                ),
            )

        data = _read_runtime_bytes(view, before_memory, target_address, width)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    f"This post-increment {size_kind} load needs readable source bytes, but neither "
                    "the writable runtime overlay nor the current read bus can provide them."
                ),
            )

        destination_name, reg_updates = _build_register_update(
            before_cpu=before_cpu,
            size_kind=size_kind,
            register_index=destination_index,
            value=int.from_bytes(data, "little"),
        )
        if reg_updates is None:
            owner_name = R32[destination_index // 2] if size_kind == "byte" else R32[destination_index]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{destination_name} cannot be updated honestly until {owner_name} is "
                    "already known in the current CPU state."
                ),
            )

        reg_updates[address_field] = (base_address + width) & 0xFFFFFFFF
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(destination_name, address_register_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            note=(
                f"Executed post-increment {size_kind} load from the current real execution subset. "
                "Source bytes were loaded from the readable runtime view and the address register "
                "was advanced after the access."
            ),
        )

    if raw[0] == 0xC5 and len(raw) == 3 and 0x20 <= raw[2] <= 0x27:
        destination_index = raw[2] & 0x07
        destination_field = REG32_FIELDS[destination_index // 2]
        destination_name = R8[destination_index]
        if destination_field == address_field:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-register-alias-side-effects",
                note=(
                    f"{destination_name} aliases {address_register_name}, and this post-increment "
                    "byte load would need alias ordering the current subset does not model yet."
                ),
            )

        data = _read_runtime_bytes(view, before_memory, target_address, 1)
        if data is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-memory-unavailable",
                note=(
                    "This post-increment byte load needs a readable source byte, but neither "
                    "the writable runtime overlay nor the current read bus can provide it."
                ),
            )

        destination_name, reg_updates = _build_register_update(
            before_cpu=before_cpu,
            size_kind="byte",
            register_index=destination_index,
            value=data[0],
        )
        if reg_updates is None:
            owner_name = R32[destination_index // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{destination_name} cannot be updated honestly until {owner_name} is "
                    "already known in the current CPU state."
                ),
            )

        reg_updates[address_field] = advanced_address
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(destination_name, address_register_name, "PC"),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            note=(
                "Executed post-increment byte load from the current real execution subset. One "
                "byte was loaded from the readable runtime view and the address register was "
                "advanced after the access."
            ),
        )

    if raw[0] == 0xF5 and len(raw) == 3 and 0x40 <= raw[2] <= 0x47:
        source_index = raw[2] & 0x07
        source_field = REG32_FIELDS[source_index // 2]
        source_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="byte",
            register_index=source_index,
        )
        if source_field == address_field:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-register-alias-side-effects",
                note=(
                    f"{source_name} aliases {address_register_name}, and this post-increment "
                    "byte store would need alias ordering the current subset does not model yet."
                ),
            )

        if source_value is None:
            owner_name = R32[source_index // 2]
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_name} cannot be stored honestly until {owner_name} is already "
                    "known in the current CPU state."
                ),
            )

        data = bytes((source_value & 0xFF,))
        write_status, write_note = _check_writable_range(view, target_address, 1)
        if write_status == "write-discarded":
            # Register advances even when the write is discarded (hardware behavior).
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=(address_register_name, "PC"),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=data,
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates={address_field: advanced_address},
                note=(
                    "Post-increment byte store destination was unmapped or read-only; write "
                    "silently discarded. Address register still advanced (open-bus behavior)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[target_address] = data[0]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(address_register_name, "PC"),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=data,
                    note="Writable runtime overlay updated by post-increment byte store execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates={address_field: advanced_address},
            note=(
                "Executed post-increment byte store from the current real execution subset. The "
                "source byte was written to the writable runtime overlay and the address "
                "register advanced after the access."
            ),
        )

    if raw[0] == 0xF5 and len(raw) == 3 and 0x50 <= raw[2] <= 0x57:
        source_index = raw[2] & 0x07
        source_field = REG32_FIELDS[source_index]
        source_name, source_value = _extract_register_value(
            before_cpu=before_cpu,
            size_kind="word",
            register_index=source_index,
        )
        if source_field == address_field:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-register-alias-side-effects",
                note=(
                    f"{source_name} aliases {address_register_name}, and this post-increment "
                    "word store would need alias ordering the current subset does not model yet."
                ),
            )

        if source_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_name} cannot be stored honestly until its current word value is "
                    "known in the current CPU state."
                ),
            )

        advanced_address_word = (base_address + 2) & 0xFFFFFFFF
        data = (source_value & 0xFFFF).to_bytes(2, "little")
        write_status, write_note = _check_writable_range(view, target_address, 2)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=(address_register_name, "PC"),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=data,
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates={address_field: advanced_address_word},
                note=(
                    "Post-increment word store destination was unmapped or read-only; write "
                    "silently discarded. Address register still advanced by 2 (open-bus behavior)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[target_address] = data[0]
        after_memory[_mask_address(target_address + 1)] = data[1]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(address_register_name, "PC"),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=data,
                    note="Writable runtime overlay updated by post-increment word store execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates={address_field: advanced_address_word},
            note=(
                "Executed post-increment word store from the current real execution subset. The "
                "source 16-bit register value was written to the writable runtime overlay and "
                "the address register was advanced by 2 after the access."
            ),
        )

    if raw[0] == 0xF5 and len(raw) == 3 and 0x60 <= raw[2] <= 0x67:
        source_index = raw[2] & 0x07
        source_field = REG32_FIELDS[source_index]
        source_name = R32[source_index]
        source_value = getattr(before_cpu.regs, source_field)
        if source_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{source_name} cannot be stored honestly until its current full value is "
                    "known in the current CPU state."
                ),
            )

        if source_field == address_field:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="unmodeled-register-alias-side-effects",
                note=(
                    f"{source_name} aliases {address_register_name}, and this post-increment "
                    "long store would need alias ordering the current subset does not model yet."
                ),
            )

        advanced_address_long = (base_address + 4) & 0xFFFFFFFF
        data = source_value.to_bytes(4, "little")
        write_status, write_note = _check_writable_range(view, target_address, 4)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=(address_register_name, "PC"),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=data,
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates={address_field: advanced_address_long},
                note=(
                    "Post-increment long store destination was unmapped or read-only; write "
                    "silently discarded. Address register still advanced by 4 (open-bus behavior)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        for offset in range(4):
            after_memory[target_address + offset] = data[offset]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(address_register_name, "PC"),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=data,
                    note="Writable runtime overlay updated by post-increment long store execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates={address_field: advanced_address_long},
            note=(
                "Executed post-increment long store from the current real execution subset. The "
                "source 32-bit register value was written to the writable runtime overlay and "
                "the address register was advanced by 4 after the access."
            ),
        )

    if raw[0] == 0xF5 and len(raw) == 5 and raw[2] == 0x02:
        advanced_address_word = (base_address + 2) & 0xFFFFFFFF
        data = raw[3:5]
        write_status, write_note = _check_writable_range(view, target_address, 2)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=(address_register_name, "PC"),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=data,
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates={address_field: advanced_address_word},
                note=(
                    "Post-increment immediate word store destination was unmapped or read-only; "
                    "write silently discarded. Address register still advanced by 2 (open-bus behavior)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[target_address] = data[0]
        after_memory[_mask_address(target_address + 1)] = data[1]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(address_register_name, "PC"),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=data,
                    note="Writable runtime overlay updated by post-increment immediate word store execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates={address_field: advanced_address_word},
            note=(
                "Executed post-increment immediate word store from the current real execution subset. "
                "The decoded 16-bit immediate was written to the writable runtime overlay and the "
                "address register was advanced by 2 after the access."
            ),
        )

    if raw[0] == 0xF5 and len(raw) == 4 and raw[2] == 0x00:
        data = bytes((raw[3],))
        write_status, write_note = _check_writable_range(view, target_address, 1)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=(address_register_name, "PC"),
                memory_writes=(
                    MemoryWrite(
                        address=target_address,
                        data=data,
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates={address_field: advanced_address},
                note=(
                    "Post-increment immediate byte store destination was unmapped or read-only; "
                    "write silently discarded. Address register still advanced (open-bus behavior)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[target_address] = raw[3]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(address_register_name, "PC"),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=data,
                    note="Writable runtime overlay updated by post-increment immediate store execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates={address_field: advanced_address},
            note=(
                "Executed post-increment immediate byte store from the current real execution "
                "subset. The decoded immediate byte was written to the writable runtime overlay "
                "and the address register advanced after the access."
            ),
        )

    return None


def _try_execute_indexed_word_misc(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute indexed word `(R32+d8)` immediate-ALU and INC/DEC forms."""
    raw = decoded.raw_bytes
    if raw is None or len(raw) not in (3, 5):
        return None
    if not (0x98 <= raw[0] <= 0x9F):
        return None

    sub_op = raw[2]
    is_inc_dec = len(raw) == 3 and 0x60 <= sub_op <= 0x6F
    is_imm_alu = len(raw) == 5 and 0x38 <= sub_op <= 0x3F
    if not (is_inc_dec or is_imm_alu):
        return None

    base_r32_index = raw[0] & 0x07
    base_r32_name = R32[base_r32_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[base_r32_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_r32_name} must be known before this indexed word form can compute "
                "its effective address honestly."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    mem_data = _read_runtime_bytes(view, before_memory, effective_address, 2)
    if mem_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed word form needs 2 readable bytes at "
                f"({base_r32_name}{'+' if displacement >= 0 else ''}{displacement})="
                f"0x{effective_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide them."
            ),
        )
    mem_value = int.from_bytes(mem_data, "little")

    if is_inc_dec:
        count = sub_op & 0x07
        if count == 0:
            count = 8
        is_dec = sub_op >= 0x68
        if is_dec:
            result = (mem_value - count) & 0xFFFF
            flags_updates = dict(_compute_subtract_flags("word", mem_value, count))
            operation = "dec"
        else:
            result = (mem_value + count) & 0xFFFF
            flags_updates = dict(_compute_add_flags("word", mem_value, count))
            operation = "inc"
        flags_updates.pop("cf", None)
    else:
        operation = {
            0x38: "add",
            0x39: "adc",
            0x3A: "sub",
            0x3B: "sbc",
            0x3C: "and",
            0x3D: "xor",
            0x3E: "or",
            0x3F: "cp",
        }[sub_op]
        if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{operation.upper()} on indexed word memory requires a known carry flag, "
                    "which is not modeled in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
        imm = int.from_bytes(raw[3:5], "little")
        if operation == "add":
            result = (mem_value + imm) & 0xFFFF
            flags_updates = _compute_add_flags("word", mem_value, imm)
        elif operation == "adc":
            result = (mem_value + imm + carry) & 0xFFFF
            flags_updates = _compute_add_flags("word", mem_value, imm + carry)
        elif operation == "sub":
            result = (mem_value - imm) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", mem_value, imm)
        elif operation == "sbc":
            result = (mem_value - imm - carry) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", mem_value, imm + carry)
        elif operation == "and":
            result = mem_value & imm
            flags_updates = _compute_logical_flags("word", result)
        elif operation == "xor":
            result = mem_value ^ imm
            flags_updates = _compute_logical_flags("word", result)
        elif operation == "or":
            result = mem_value | imm
            flags_updates = _compute_logical_flags("word", result)
        else:
            result = (mem_value - imm) & 0xFFFF
            flags_updates = _compute_subtract_flags("word", mem_value, imm)
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed indexed word compare-immediate: mem[{base_r32_name}"
                    f"{'+' if displacement >= 0 else ''}{displacement}]=0x{mem_value:04X} - "
                    f"imm=0x{imm:04X}."
                ),
            )

    result_bytes = result.to_bytes(2, "little")
    write_status, write_note = _check_writable_range(view, effective_address, 2)
    if write_status == "write-discarded":
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(address=effective_address, data=result_bytes, note=f"[DISCARDED] {write_note}"),
            ),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Indexed word {operation} destination was unmapped or read-only; write "
                "silently discarded (open-bus behavior — execution continues)."
            ),
        )
    if write_status is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=write_status,
            note=write_note,
        )

    after_memory = dict(before_memory)
    after_memory[effective_address] = result_bytes[0]
    after_memory[_mask_address(effective_address + 1)] = result_bytes[1]
    detail = (
        f"imm=0x{int.from_bytes(raw[3:5], 'little'):04X}"
        if is_imm_alu
        else f"count={8 if (sub_op & 0x07) == 0 else (sub_op & 0x07)}"
    )
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(
            MemoryWrite(
                address=effective_address,
                data=result_bytes,
                note=f"Writable runtime overlay updated by indexed word {operation.upper()} execution.",
            ),
        ),
        after_memory=after_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags_updates,
        note=(
            f"Executed indexed word {operation}: mem[{base_r32_name}"
            f"{'+' if displacement >= 0 else ''}{displacement}]=0x{mem_value:04X}, "
            f"{detail} -> mem=0x{result:04X}."
        ),
    )


def _try_execute_indexed_long_misc(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute indexed long `(R32+d8)` immediate-ALU and INC/DEC forms."""
    raw = decoded.raw_bytes
    if raw is None or len(raw) not in (3, 7):
        return None
    if not (0xA8 <= raw[0] <= 0xAF):
        return None

    sub_op = raw[2]
    is_inc_dec = len(raw) == 3 and 0x60 <= sub_op <= 0x6F
    is_imm_alu = len(raw) == 7 and 0x38 <= sub_op <= 0x3F
    if not (is_inc_dec or is_imm_alu):
        return None

    base_r32_index = raw[0] & 0x07
    base_r32_name = R32[base_r32_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[base_r32_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_r32_name} must be known before this indexed long form can compute "
                "its effective address honestly."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    mem_data = _read_runtime_bytes(view, before_memory, effective_address, 4)
    if mem_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed long form needs 4 readable bytes at "
                f"({base_r32_name}{'+' if displacement >= 0 else ''}{displacement})="
                f"0x{effective_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide them."
            ),
        )
    mem_value = int.from_bytes(mem_data, "little")

    if is_inc_dec:
        count = sub_op & 0x07
        if count == 0:
            count = 8
        is_dec = sub_op >= 0x68
        if is_dec:
            result = (mem_value - count) & 0xFFFFFFFF
            flags_updates = dict(_compute_subtract_flags("long", mem_value, count))
            operation = "dec"
        else:
            result = (mem_value + count) & 0xFFFFFFFF
            flags_updates = dict(_compute_add_flags("long", mem_value, count))
            operation = "inc"
        flags_updates.pop("cf", None)
    else:
        operation = {
            0x38: "add",
            0x39: "adc",
            0x3A: "sub",
            0x3B: "sbc",
            0x3C: "and",
            0x3D: "xor",
            0x3E: "or",
            0x3F: "cp",
        }[sub_op]
        if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"{operation.upper()} on indexed long memory requires a known carry flag, "
                    "which is not modeled in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
        imm = int.from_bytes(raw[3:7], "little")
        if operation == "add":
            result = (mem_value + imm) & 0xFFFFFFFF
            flags_updates = _compute_add_flags("long", mem_value, imm)
        elif operation == "adc":
            result = (mem_value + imm + carry) & 0xFFFFFFFF
            flags_updates = _compute_add_flags("long", mem_value, imm + carry)
        elif operation == "sub":
            result = (mem_value - imm) & 0xFFFFFFFF
            flags_updates = _compute_subtract_flags("long", mem_value, imm)
        elif operation == "sbc":
            result = (mem_value - imm - carry) & 0xFFFFFFFF
            flags_updates = _compute_subtract_flags("long", mem_value, imm + carry)
        elif operation == "and":
            result = mem_value & imm
            flags_updates = _compute_logical_flags("long", result)
        elif operation == "xor":
            result = mem_value ^ imm
            flags_updates = _compute_logical_flags("long", result)
        elif operation == "or":
            result = mem_value | imm
            flags_updates = _compute_logical_flags("long", result)
        else:
            result = (mem_value - imm) & 0xFFFFFFFF
            flags_updates = _compute_subtract_flags("long", mem_value, imm)
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    f"Executed indexed long compare-immediate: mem[{base_r32_name}"
                    f"{'+' if displacement >= 0 else ''}{displacement}]=0x{mem_value:08X} - "
                    f"imm=0x{imm:08X}."
                ),
            )

    result_bytes = result.to_bytes(4, "little")
    write_status, write_note = _check_writable_range(view, effective_address, 4)
    if write_status == "write-discarded":
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(address=effective_address, data=result_bytes, note=f"[DISCARDED] {write_note}"),
            ),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Indexed long {operation} destination was unmapped or read-only; write silently "
                "discarded (open-bus behavior - execution continues)."
            ),
        )
    if write_status is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=write_status,
            note=write_note,
        )

    after_memory = dict(before_memory)
    for offset, value in enumerate(result_bytes):
        after_memory[_mask_address(effective_address + offset)] = value

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(
            MemoryWrite(
                address=effective_address,
                data=result_bytes,
                note="Writable runtime overlay updated by indexed long immediate/inc-dec execution.",
            ),
        ),
        after_memory=after_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags_updates,
        note=(
            f"Executed indexed long {operation}: mem[{base_r32_name}"
            f"{'+' if displacement >= 0 else ''}{displacement}]=0x{mem_value:08X} -> "
            f"0x{result:08X}."
        ),
    )


def _try_execute_indexed_byte_alu(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute indexed byte ALU on `(R32+d8)` memory operands."""
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None
    if not (0x88 <= raw[0] <= 0x8F and 0x80 <= raw[2] <= 0xFF):
        return None

    base_r32_index = raw[0] & 0x07
    base_r32_name = R32[base_r32_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[base_r32_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_r32_name} must be known before this indexed byte ALU can compute "
                "its effective address honestly."
            ),
        )

    sub_op = raw[2]
    op_group = sub_op >> 4
    operation = {
        0x8: "add",
        0x9: "adc",
        0xA: "sub",
        0xB: "sbc",
        0xC: "and",
        0xD: "xor",
        0xE: "or",
        0xF: "cp",
    }.get(op_group)
    if operation is None:
        return None
    store_to_memory = bool(sub_op & 0x08)
    register_index = sub_op & 0x07
    register_name, register_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind="byte",
        register_index=register_index,
    )
    if register_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-source-register",
            note=(
                f"{operation.upper()} on {register_name}/({base_r32_name}+d8) needs "
                f"{register_name} modeled in the current CPU state."
            ),
        )

    if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-state-required",
            note=(
                f"{operation.upper()} on indexed byte memory requires a known carry flag, "
                "which is not modeled in the current CPU state."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    mem_data = _read_runtime_bytes(view, before_memory, effective_address, 1)
    if mem_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed byte ALU needs a readable byte at "
                f"({base_r32_name}{'+' if displacement >= 0 else ''}{displacement})="
                f"0x{effective_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide it."
            ),
        )

    mem_value = mem_data[0]
    carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
    register_is_left = not store_to_memory
    if register_is_left:
        left_value = register_value
        right_value = mem_value
    else:
        left_value = mem_value
        right_value = register_value

    if operation == "add":
        result = (left_value + right_value) & 0xFF
        flags_updates = _compute_add_flags("byte", left_value, right_value)
    elif operation == "adc":
        result = (left_value + right_value + carry) & 0xFF
        flags_updates = _compute_add_flags("byte", left_value, right_value + carry)
    elif operation == "sub":
        result = (left_value - right_value) & 0xFF
        flags_updates = _compute_subtract_flags("byte", left_value, right_value)
    elif operation == "sbc":
        result = (left_value - right_value - carry) & 0xFF
        flags_updates = _compute_subtract_flags("byte", left_value, right_value + carry)
    elif operation == "and":
        result = left_value & right_value
        flags_updates = _compute_logical_flags("byte", result)
    elif operation == "xor":
        result = left_value ^ right_value
        flags_updates = _compute_logical_flags("byte", result)
    elif operation == "or":
        result = left_value | right_value
        flags_updates = _compute_logical_flags("byte", result)
    else:
        result = (left_value - right_value) & 0xFF
        flags_updates = _compute_subtract_flags("byte", left_value, right_value)

    if operation == "cp":
        direction = "register-minus-memory" if register_is_left else "memory-minus-register"
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed indexed byte compare ({direction}) from the current real execution "
                "subset. One byte was read at the effective address and the modeled flag "
                "subset now reflects the subtraction result."
            ),
        )

    if store_to_memory:
        result_bytes = bytes((result,))
        write_status, write_note = _check_writable_range(view, effective_address, 1)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=effective_address,
                        data=result_bytes,
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                note=(
                    "Indexed byte ALU destination was unmapped or read-only; write silently "
                    "discarded (open-bus behavior — execution continues)."
                ),
                flags_updates=flags_updates,
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[effective_address] = result
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=effective_address,
                    data=result_bytes,
                    note=f"Writable runtime overlay updated by indexed byte {operation.upper()} execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed indexed byte {operation}: mem[{base_r32_name}"
                f"{'+' if displacement >= 0 else ''}{displacement}]=0x{mem_value:02X}, "
                f"{register_name}=0x{register_value:02X} -> mem=0x{result:02X}."
            ),
        )

    result_name, reg_updates = _build_register_update(
        before_cpu=before_cpu,
        size_kind="byte",
        register_index=register_index,
        value=result,
    )
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{operation.upper()} {register_name}, ({base_r32_name}+d8) needs the owner "
                f"register of {register_name} fully known to write back."
            ),
        )
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(result_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags_updates,
        note=(
            f"Executed indexed byte {operation}: {register_name}=0x{register_value:02X}, "
            f"mem[{base_r32_name}{'+' if displacement >= 0 else ''}{displacement}]="
            f"0x{mem_value:02X} -> {result_name}=0x{result:02X}."
        ),
    )


def _try_execute_indexed_word_alu(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute indexed word ALU on `(R32+d8)` memory operands.

    Encoding: `[0x98+r_base] [d8] [sub_op]`
      - `0x80..0x87` / `0x88..0x8F` = ADD `R16,(mem)` / `(mem),R16`
      - `0x90..0x97` / `0x98..0x9F` = ADC `R16,(mem)` / `(mem),R16`
      - `0xA0..0xA7` / `0xA8..0xAF` = SUB `R16,(mem)` / `(mem),R16`
      - `0xB0..0xB7` / `0xB8..0xBF` = SBC `R16,(mem)` / `(mem),R16`
      - `0xC0..0xC7` / `0xC8..0xCF` = AND `R16,(mem)` / `(mem),R16`
      - `0xD0..0xD7` / `0xD8..0xDF` = XOR `R16,(mem)` / `(mem),R16`
      - `0xE0..0xE7` / `0xE8..0xEF` = OR  `R16,(mem)` / `(mem),R16`
      - `0xF0..0xF7` / `0xF8..0xFF` = CP  `R16,(mem)` / `(mem),R16`
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None
    if not (0x98 <= raw[0] <= 0x9F and 0x80 <= raw[2] <= 0xFF):
        return None

    base_r32_index = raw[0] & 0x07
    base_r32_name = R32[base_r32_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[base_r32_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_r32_name} must be known before this indexed word ALU can compute "
                "its effective address honestly."
            ),
        )

    sub_op = raw[2]
    op_group = sub_op >> 4
    operation = {
        0x8: "add",
        0x9: "adc",
        0xA: "sub",
        0xB: "sbc",
        0xC: "and",
        0xD: "xor",
        0xE: "or",
        0xF: "cp",
    }.get(op_group)
    if operation is None:
        return None
    store_to_memory = bool(sub_op & 0x08)
    register_index = sub_op & 0x07
    register_name, register_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind="word",
        register_index=register_index,
    )
    if register_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-source-register",
            note=(
                f"{operation.upper()} on {register_name}/({base_r32_name}+d8) needs "
                f"{register_name} modeled in the current CPU state."
            ),
        )

    if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-state-required",
            note=(
                f"{operation.upper()} on indexed word memory requires a known carry flag, "
                "which is not modeled in the current CPU state."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    mem_data = _read_runtime_bytes(view, before_memory, effective_address, 2)
    if mem_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed word ALU needs 2 readable bytes at "
                f"({base_r32_name}{'+' if displacement >= 0 else ''}{displacement})="
                f"0x{effective_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide them."
            ),
        )

    mem_value = int.from_bytes(mem_data, "little")
    carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
    register_is_left = not store_to_memory
    if register_is_left:
        left_value = register_value
        right_value = mem_value
    else:
        left_value = mem_value
        right_value = register_value

    if operation == "add":
        result = (left_value + right_value) & 0xFFFF
        flags_updates = _compute_add_flags("word", left_value, right_value)
    elif operation == "adc":
        result = (left_value + right_value + carry) & 0xFFFF
        flags_updates = _compute_add_flags("word", left_value, right_value + carry)
    elif operation == "sub":
        result = (left_value - right_value) & 0xFFFF
        flags_updates = _compute_subtract_flags("word", left_value, right_value)
    elif operation == "sbc":
        result = (left_value - right_value - carry) & 0xFFFF
        flags_updates = _compute_subtract_flags("word", left_value, right_value + carry)
    elif operation == "and":
        result = left_value & right_value
        flags_updates = _compute_logical_flags("word", result)
    elif operation == "xor":
        result = left_value ^ right_value
        flags_updates = _compute_logical_flags("word", result)
    elif operation == "or":
        result = left_value | right_value
        flags_updates = _compute_logical_flags("word", result)
    else:
        result = (left_value - right_value) & 0xFFFF
        flags_updates = _compute_subtract_flags("word", left_value, right_value)

    if operation == "cp":
        direction = "register-minus-memory" if register_is_left else "memory-minus-register"
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed indexed word compare ({direction}) from the current real execution "
                "subset. Two bytes were read at the effective address and the modeled flag "
                "subset now reflects the subtraction result."
            ),
        )

    if store_to_memory:
        result_bytes = result.to_bytes(2, "little")
        write_status, write_note = _check_writable_range(view, effective_address, 2)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(
                        address=effective_address,
                        data=result_bytes,
                        note=f"[DISCARDED] {write_note}",
                    ),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                note=(
                    "Indexed word ALU destination was unmapped or read-only; write silently "
                    "discarded (open-bus behavior — execution continues)."
                ),
                flags_updates=flags_updates,
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        after_memory[effective_address] = result_bytes[0]
        after_memory[_mask_address(effective_address + 1)] = result_bytes[1]
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=effective_address,
                    data=result_bytes,
                    note=f"Writable runtime overlay updated by indexed word {operation.upper()} execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed indexed word {operation}: mem[{base_r32_name}"
                f"{'+' if displacement >= 0 else ''}{displacement}]=0x{mem_value:04X}, "
                f"{register_name}=0x{register_value:04X} -> mem=0x{result:04X}."
            ),
        )

    result_name, reg_updates = _build_register_update(
        before_cpu=before_cpu,
        size_kind="word",
        register_index=register_index,
        value=result,
    )
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{operation.upper()} {register_name}, ({base_r32_name}+d8) needs the owner "
                f"register of {register_name} fully known to write back."
            ),
        )
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(result_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags_updates,
        note=(
            f"Executed indexed word {operation}: {register_name}=0x{register_value:04X}, "
            f"mem[{base_r32_name}{'+' if displacement >= 0 else ''}{displacement}]="
            f"0x{mem_value:04X} -> {result_name}=0x{result:04X}."
        ),
    )


def _try_execute_indexed_rmw_add(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute indexed memory RMW ADD: add (r32+d8), R32.

    Encoding: [A8+r] [d8] [88+R]  — 3 bytes.
    Reads 4 bytes at (r32+d8), adds source R32, writes result back.
    Catalog: A8+r : 88+R => add (r32+d8), R32.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None
    if not (0xA8 <= raw[0] <= 0xAF and 0x88 <= raw[2] <= 0x8F):
        return None

    base_r32_index = raw[0] & 0x07
    base_r32_name = R32[base_r32_index]
    base_r32_field = REG32_FIELDS[base_r32_index]
    base_address = getattr(before_cpu.regs, base_r32_field)
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_r32_name} must be known before this indexed memory RMW ADD can compute "
                "its effective address honestly."
            ),
        )

    src_r32_index = raw[2] & 0x07
    src_r32_name, src_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind="long",
        register_index=src_r32_index,
    )
    if src_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{src_r32_name} must be known before this indexed memory RMW ADD can read "
                "the addition operand honestly."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    mem_data = _read_runtime_bytes(view, before_memory, effective_address, 4)
    if mem_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed memory RMW ADD needs 4 readable bytes at "
                f"({base_r32_name}{'+' if displacement >= 0 else ''}{displacement})="
                f"0x{effective_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide them."
            ),
        )

    mem_value = int.from_bytes(mem_data, "little")
    result = (mem_value + src_value) & 0xFFFFFFFF
    result_bytes = result.to_bytes(4, "little")

    write_status, write_note = _check_writable_range(view, effective_address, 4)
    if write_status == "write-discarded":
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=effective_address,
                    data=result_bytes,
                    note=f"[DISCARDED] {write_note}",
                ),
            ),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            note=(
                "Indexed RMW ADD destination was unmapped or read-only; write silently "
                "discarded (open-bus behavior — execution continues)."
            ),
        )
    if write_status is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=write_status,
            note=write_note,
        )

    after_memory = dict(before_memory)
    for offset, value in enumerate(result_bytes):
        after_memory[_mask_address(effective_address + offset)] = value

    flags = _compute_add_flags("long", mem_value, src_value)
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(
            MemoryWrite(
                address=effective_address,
                data=result_bytes,
                note="Writable runtime overlay updated by indexed memory RMW ADD execution.",
            ),
        ),
        after_memory=after_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags,
        note=(
            f"Executed indexed memory RMW ADD from the current real execution subset. "
            f"mem[{base_r32_name}{'+' if displacement >= 0 else ''}{displacement}]="
            f"0x{mem_value:08X} + {src_r32_name}=0x{src_value:08X} = "
            f"0x{result:08X} written back to 0x{effective_address:06X}."
        ),
    )


def _try_execute_indexed_long_alu(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute indexed long ALU on `(R32+d8)` memory operands."""
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None
    if not (0xA8 <= raw[0] <= 0xAF and 0x80 <= raw[2] <= 0xFF):
        return None

    sub_op = raw[2]
    operation = {
        0x8: "add",
        0x9: "adc",
        0xA: "sub",
        0xB: "sbc",
        0xC: "and",
        0xD: "xor",
        0xE: "or",
        0xF: "cp",
    }.get(sub_op >> 4)
    if operation is None:
        return None

    base_r32_index = raw[0] & 0x07
    base_r32_name = R32[base_r32_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[base_r32_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_r32_name} must be known before this indexed long ALU can compute "
                "its effective address honestly."
            ),
        )

    register_index = sub_op & 0x07
    register_name, register_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind="long",
        register_index=register_index,
    )
    if register_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{operation.upper()} on {register_name}/({base_r32_name}+d8) needs "
                f"{register_name} modeled in the current CPU state."
            ),
        )

    if operation in ("adc", "sbc") and before_cpu.flags.cf is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-state-required",
            note=(
                f"{operation.upper()} on indexed long memory requires a known carry flag, "
                "which is not modeled in the current CPU state."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    mem_data = _read_runtime_bytes(view, before_memory, effective_address, 4)
    if mem_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed long ALU needs 4 readable byte(s) at "
                f"({base_r32_name}{'+' if displacement >= 0 else ''}{displacement})="
                f"0x{effective_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide them."
            ),
        )

    mem_value = int.from_bytes(mem_data, "little")
    carry = int(before_cpu.flags.cf) if operation in ("adc", "sbc") else 0
    register_is_left = not bool(sub_op & 0x08)
    if register_is_left:
        left_value = register_value
        right_value = mem_value
    else:
        left_value = mem_value
        right_value = register_value

    if operation == "add":
        result = (left_value + right_value) & 0xFFFFFFFF
        flags_updates = _compute_add_flags("long", left_value, right_value)
    elif operation == "adc":
        result = (left_value + right_value + carry) & 0xFFFFFFFF
        flags_updates = _compute_add_flags("long", left_value, right_value + carry)
    elif operation == "sub":
        result = (left_value - right_value) & 0xFFFFFFFF
        flags_updates = _compute_subtract_flags("long", left_value, right_value)
    elif operation == "sbc":
        result = (left_value - right_value - carry) & 0xFFFFFFFF
        flags_updates = _compute_subtract_flags("long", left_value, right_value + carry)
    elif operation == "and":
        result = left_value & right_value
        flags_updates = _compute_logical_flags("long", result)
    elif operation == "xor":
        result = left_value ^ right_value
        flags_updates = _compute_logical_flags("long", result)
    elif operation == "or":
        result = left_value | right_value
        flags_updates = _compute_logical_flags("long", result)
    else:
        result = (left_value - right_value) & 0xFFFFFFFF
        flags_updates = _compute_subtract_flags("long", left_value, right_value)

    if operation == "cp":
        direction = "register-minus-memory" if register_is_left else "memory-minus-register"
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed indexed long compare ({direction}) from the current real execution "
                "subset. Four bytes were read at the effective address and the modeled flag "
                "subset now reflects the subtraction result."
            ),
        )

    if not register_is_left:
        result_bytes = result.to_bytes(4, "little")
        write_status, write_note = _check_writable_range(view, effective_address, 4)
        if write_status == "write-discarded":
            return _executed_result(
                before_cpu=before_cpu,
                decoded=decoded,
                written_registers=("PC",),
                memory_writes=(
                    MemoryWrite(address=effective_address, data=result_bytes, note=f"[DISCARDED] {write_note}"),
                ),
                after_memory=before_memory,
                new_pc=decoded.next_sequential_pc,
                reg_updates=None,
                flags_updates=flags_updates,
                note=(
                    "Indexed long ALU destination was unmapped or read-only; write silently "
                    "discarded (open-bus behavior - execution continues)."
                ),
            )
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )

        after_memory = dict(before_memory)
        for offset, value in enumerate(result_bytes):
            after_memory[_mask_address(effective_address + offset)] = value
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(
                MemoryWrite(
                    address=effective_address,
                    data=result_bytes,
                    note=f"Writable runtime overlay updated by indexed long {operation.upper()} execution.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags_updates,
            note=(
                f"Executed indexed long {operation}: mem[{base_r32_name}"
                f"{'+' if displacement >= 0 else ''}{displacement}]=0x{mem_value:08X}, "
                f"{register_name}=0x{register_value:08X} -> mem=0x{result:08X}."
            ),
        )

    result_name, reg_updates = _build_register_update(
        before_cpu=before_cpu,
        size_kind="long",
        register_index=register_index,
        value=result,
    )
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{operation.upper()} {register_name}, ({base_r32_name}+d8) needs the owner "
                f"register of {register_name} fully known to write back."
            ),
        )
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(result_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags_updates,
        note=(
            f"Executed indexed long {operation}: {register_name}=0x{register_value:08X}, "
            f"mem[{base_r32_name}{'+' if displacement >= 0 else ''}{displacement}]="
            f"0x{mem_value:08X} -> {result_name}=0x{result:08X}."
        ),
    )


def _try_execute_indexed_compare(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None

    first = raw[0]
    # F8..FF = cp (r32+d8), R32 — memory is left operand; flags = mem - R32
    # F0..F7 = cp R32, (r32+d8) — register is left operand; flags = R32 - mem
    if not (0xA8 <= first <= 0xAF and (0xF0 <= raw[2] <= 0xFF)):
        return None

    r32_is_left = 0xF0 <= raw[2] <= 0xF7  # cp R32, (mem): R32 - mem
    reg_index = raw[2] & 0x07

    address_register_index = first & 0x07
    address_register_name = R32[address_register_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[address_register_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{address_register_name} must be known before this indexed compare can compute "
                "its effective address honestly."
            ),
        )

    reg_name, reg_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind="long",
        register_index=reg_index,
    )
    if reg_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} cannot be compared honestly until its current full value is "
                "known in the CPU state."
            ),
        )

    effective_address = _mask_address((base_address + _signed_u8(raw[1])) & 0xFFFFFFFF)
    data = _read_runtime_bytes(view, before_memory, effective_address, 4)
    if data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                "This indexed compare needs readable bytes at its effective address, but "
                "neither the writable runtime overlay nor the current read bus can provide them."
            ),
        )

    mem_value = int.from_bytes(data, "little")
    if r32_is_left:
        # cp R32, (r32+d8): flags = R32 - mem
        flags_updates = _compute_subtract_flags("long", reg_value, mem_value)
        direction_note = "register-minus-memory"
    else:
        # cp (r32+d8), R32: flags = mem - R32
        flags_updates = _compute_subtract_flags("long", mem_value, reg_value)
        direction_note = "memory-minus-register"
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags_updates,
        note=(
            f"Executed indexed memory compare ({direction_note}) from the current real execution "
            "subset. Four bytes were read at the effective address and the modeled flag subset "
            "now reflects the subtraction result."
        ),
    )


def _try_execute_indexed_cp_immediate(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute indexed memory compare with immediate: cp (r32+d8), imm.

    Encoding:
      [88+r] [d8] [3F] [imm8]          — 4 bytes, byte size
      [98+r] [d8] [3F] [lo] [hi]       — 5 bytes, word size
      [A8+r] [d8] [3F] [b0..b3]        — 7 bytes, long size
    Flags = mem - imm (subtract flags).
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) < 4:
        return None

    first = raw[0]
    if raw[2] != 0x3F:
        return None

    if 0x88 <= first <= 0x8F and len(raw) == 4:
        size_kind = "byte"
        width = 1
        imm = raw[3]
    elif 0x98 <= first <= 0x9F and len(raw) == 5:
        size_kind = "word"
        width = 2
        imm = int.from_bytes(raw[3:5], "little")
    elif 0xA8 <= first <= 0xAF and len(raw) == 7:
        size_kind = "long"
        width = 4
        imm = int.from_bytes(raw[3:7], "little")
    else:
        return None

    base_r32_index = first & 0x07
    base_r32_name = R32[base_r32_index]
    base_address = getattr(before_cpu.regs, REG32_FIELDS[base_r32_index])
    if base_address is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-address-register",
            note=(
                f"{base_r32_name} must be known before this indexed compare-immediate can "
                "compute its effective address honestly."
            ),
        )

    displacement = _signed_u8(raw[1])
    effective_address = _mask_address((base_address + displacement) & 0xFFFFFFFF)
    mem_data = _read_runtime_bytes(view, before_memory, effective_address, width)
    if mem_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-memory-unavailable",
            note=(
                f"This indexed compare-immediate needs {width} readable byte(s) at "
                f"({base_r32_name}{'+' if displacement >= 0 else ''}{displacement})="
                f"0x{effective_address:06X}, but neither the writable runtime overlay nor "
                "the current read bus can provide them."
            ),
        )

    mem_value = int.from_bytes(mem_data, "little")
    flags_updates = _compute_subtract_flags(size_kind, mem_value, imm)
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags_updates,
        note=(
            f"Executed indexed compare-immediate from the current real execution subset. "
            f"mem[{base_r32_name}{'+' if displacement >= 0 else ''}{displacement}]="
            f"0x{mem_value:0{width*2}X} vs imm=0x{imm:0{width*2}X}, flags reflect subtraction."
        ),
    )


def _try_execute_prefixed_alu_register(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Prefixed register-register ALU: add/adc/sub/sbc/and/or/xor/cp R, r.

    Catalog encoding (C8+zz+r : op+R):
      0x80 = ADD, 0x90 = ADC, 0xA0 = SUB, 0xB0 = SBC,
      0xC0 = AND, 0xD0 = XOR, 0xE0 = OR,  0xF0 = CP.
    Source register r is identified by the C8+zz+r prefix byte.
    Destination register R is encoded in the op byte (op & 0x07).
    Length: 2 bytes.

    ADC / SBC consume the current carry flag; they block honestly when CF
    is unknown rather than guessing. ADD / OR / etc. emit fresh flags
    (cf is set by ADD/SUB; OR/AND/XOR clear CF/HF and set S/Z based on
    the result). This is what unblocks the cc900/cdecl/adecl crt0 BSS
    init + DataROM copy loops on real builds (CE 90 = `adc W, H`).
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    op = raw[1]
    if 0x80 <= op <= 0x87:
        alu_op = "add"
    elif 0x90 <= op <= 0x97:
        alu_op = "adc"
    elif 0xA0 <= op <= 0xA7:
        alu_op = "sub"
    elif 0xB0 <= op <= 0xB7:
        alu_op = "sbc"
    elif 0xC0 <= op <= 0xC7:
        alu_op = "and"
    elif 0xD0 <= op <= 0xD7:
        alu_op = "xor"
    elif 0xE0 <= op <= 0xE7:
        alu_op = "or"
    elif 0xF0 <= op <= 0xF7:
        alu_op = "cp"
    else:
        return None

    size_kind, src_index = info
    dest_index = op & 0x07

    src_name, src_value = _extract_register_value(before_cpu, size_kind, src_index)
    if src_value is None and size_kind == "byte":
        src_value = _extract_current_banked_r8_value(before_cpu, src_index)
    if src_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{src_name} must be known before this register-register {alu_op} can be "
                "executed honestly."
            ),
        )

    dest_name, dest_value = _extract_register_value(before_cpu, size_kind, dest_index)
    if dest_value is None and size_kind == "byte":
        dest_value = _extract_current_banked_r8_value(before_cpu, dest_index)
    if dest_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{dest_name} must be known before this register-register {alu_op} can be "
                "executed honestly."
            ),
        )

    # ADC / SBC need a known carry flag. Without CF we cannot honestly
    # compute the result — block rather than guess.
    if alu_op in ("adc", "sbc"):
        if before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    f"Register-register {alu_op} requires a known carry flag, "
                    "which is not modeled in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf)
    else:
        carry = 0

    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    mask = (1 << bits) - 1

    if alu_op == "add":
        result = (dest_value + src_value) & mask
        flags = _compute_add_flags(size_kind, dest_value, src_value)
    elif alu_op == "adc":
        result = (dest_value + src_value + carry) & mask
        flags = _compute_add_flags(size_kind, dest_value, src_value + carry)
    elif alu_op == "sub":
        result = (dest_value - src_value) & mask
        flags = _compute_subtract_flags(size_kind, dest_value, src_value)
    elif alu_op == "sbc":
        result = (dest_value - src_value - carry) & mask
        flags = _compute_subtract_flags(size_kind, dest_value, src_value + carry)
    elif alu_op == "and":
        result = dest_value & src_value
        flags = _compute_logical_flags(size_kind, result)
    elif alu_op == "xor":
        result = dest_value ^ src_value
        flags = _compute_logical_flags(size_kind, result)
    elif alu_op == "or":
        result = dest_value | src_value
        flags = _compute_logical_flags(size_kind, result)
    else:  # cp
        result = None  # no write-back for cp
        flags = _compute_subtract_flags(size_kind, dest_value, src_value)

    if result is None:
        # cp: flags only, no register write-back
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            flags_updates=flags,
            note=(
                f"Executed register-register cp from the current real execution subset. "
                f"Flags = {dest_name}(0x{dest_value:0{bits//4}X}) - {src_name}(0x{src_value:0{bits//4}X})."
            ),
        )

    extra_cpu_updates = None
    reg_name, reg_updates = _build_register_update(before_cpu, size_kind, dest_index, result)
    if reg_updates is None and size_kind == "byte":
        reg_updates, extra_cpu_updates = _build_current_banked_r8_update(before_cpu, dest_index, result)
        reg_name = R8[dest_index]
    if reg_updates is None and extra_cpu_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"The owner register of {dest_name} must be fully known to write back the "
                f"{alu_op} result."
            ),
        )

    symbols = {
        "add": "+",
        "adc": "+ (carry)",
        "sub": "-",
        "sbc": "- (borrow)",
        "and": "&",
        "xor": "^",
        "or": "|",
    }
    symbol = symbols[alu_op]
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(reg_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags,
        extra_cpu_updates=extra_cpu_updates,
        note=(
            f"Executed register-register {alu_op} from the current real execution subset. "
            f"{dest_name} = {dest_name} {symbol} {src_name} = 0x{result:0{bits//4}X}."
        ),
    )


def _try_execute_prefixed_shift_imm(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Shift/rotate register by 4-bit immediate count.

    Catalog encoding: [C8+zz+r] [E8..EF] [count(#4)]  — 3 bytes.
      E8=RLC, E9=RRC, EA=RL, EB=RR, EC=SLA, ED=SRA, EE=SLL, EF=SRL.
    Count is lower nibble of the 3rd byte (4-bit immediate, range 0..15).
    RL/RR (through carry) are not modeled because the carry flag is not tracked.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    op = raw[1]
    if not (0xE8 <= op <= 0xEF):
        return None

    if op in (0xEA, 0xEB):  # RL, RR — need carry flag, not modeled yet
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-flags",
            note=(
                "RL/RR rotate-through-carry requires the CY flag, which is not yet fully "
                "tracked in the current CPU state model."
            ),
        )

    size_kind, register_index = info
    count = raw[2] & 0x0F  # 4-bit immediate count

    reg_name, reg_value = _extract_register_value(before_cpu, size_kind, register_index)
    if reg_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} must be known before this shift/rotate can be executed honestly."
            ),
        )

    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    mask = (1 << bits) - 1

    if op == 0xE8:   # RLC — rotate left, MSB → CY and bit 0
        result = ((reg_value << count) | (reg_value >> (bits - count))) & mask if count else reg_value
    elif op == 0xE9:  # RRC — rotate right, LSB → CY and MSB
        result = ((reg_value >> count) | (reg_value << (bits - count))) & mask if count else reg_value
    elif op == 0xEC:  # SLA — shift left arithmetic (same as SLL for positive counts)
        result = (reg_value << count) & mask
    elif op == 0xED:  # SRA — shift right arithmetic (sign-extending)
        sign_bit = (reg_value >> (bits - 1)) & 1
        result = reg_value >> count
        if sign_bit:
            fill = ((1 << count) - 1) << (bits - count)
            result = (result | fill) & mask
    elif op == 0xEE:  # SLL — shift left logical
        result = (reg_value << count) & mask
    else:             # 0xEF: SRL — shift right logical
        result = reg_value >> count

    op_names = {0xE8: "rlc", 0xE9: "rrc", 0xEC: "sla", 0xED: "sra", 0xEE: "sll", 0xEF: "srl"}
    op_name = op_names[op]

    # Compute carry (MSB shifted out for left shifts, LSB for right shifts)
    if count:
        if op in (0xE8, 0xEC, 0xEE):
            carry_out = bool((reg_value >> (bits - count)) & 1)
        else:
            carry_out = bool((reg_value >> (count - 1)) & 1)
    else:
        carry_out = False

    flags = {
        "sf": bool(result >> (bits - 1)),
        "zf": result == 0,
        "vf": False,  # parity for shift ops — not modeled
        "cf": carry_out,
    }

    reg_update_name, reg_updates = _build_register_update(before_cpu, size_kind, register_index, result)
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"The owner register of {reg_name} must be fully known to write back the "
                f"{op_name} result."
            ),
        )

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(reg_update_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags,
        note=(
            f"Executed {op_name} {count}, {reg_name} from the current real execution subset. "
            f"{reg_name} = 0x{reg_value:0{bits//4}X} << {count} = 0x{result:0{bits//4}X}."
        ),
    )


def _try_execute_prefixed_cp_imm3(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """CP R, imm3 — compare register with 3-bit embedded immediate.

    Catalog encoding: [C8+zz+r] [D8+imm3]  — 2 bytes.
    Immediate = second_byte & 0x07, range 0..7.
    Flags = subtract_flags(reg, imm3). No write-back.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    if not (0xD8 <= raw[1] <= 0xDF):
        return None

    size_kind, register_index = info
    imm3 = raw[1] & 0x07

    reg_name, reg_value = _extract_register_value(before_cpu, size_kind, register_index)
    if reg_value is None:
        owner = R32[register_index] if size_kind == "long" else R32[register_index // 2]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} must be known before this cp-imm3 compare can be executed "
                f"honestly. Owner register {owner} is not yet in the current CPU state."
            ),
        )

    flags_updates = _compute_subtract_flags(size_kind, reg_value, imm3)
    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags_updates,
        note=(
            f"Executed prefixed cp imm3 from the current real execution subset. "
            f"Flags = {reg_name}(0x{reg_value:0{bits//4}X}) - {imm3}."
        ),
    )


def _try_execute_prefixed_bit_test(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """BIT #4, r for prefixed byte/word/long register families.

    Catalog encoding: [C8+zz+r] [33] [#4].
    Modeled flags per TLCS-900/L1 datasheet:
      - Z = not src<bit>
      - H = 1
      - N = 0
      - S/V/C preserved
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None or raw[1] != 0x33:
        return None

    size_kind, register_index = info
    bit_index = raw[2] & 0x0F
    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]

    reg_name, reg_value = _extract_register_value(before_cpu, size_kind, register_index)
    if reg_value is None:
        owner = R32[register_index] if size_kind == "long" else R32[register_index // 2]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} must be known before this BIT test can be executed honestly. "
                f"Owner register {owner} is not yet in the current CPU state."
            ),
        )

    if bit_index >= bits:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="silicon-undefined",
            note=(
                f"BIT {bit_index}, {reg_name} is undefined for {size_kind}-sized register "
                "operands on TLCS-900/H."
            ),
        )

    zf = ((reg_value >> bit_index) & 1) == 0
    flags_updates = {
        "zf": zf,
        "hf": True,
        "nf": False,
    }
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=None,
        flags_updates=flags_updates,
        note=(
            f"Executed prefixed BIT immediate from the current real execution subset. "
            f"Tested {reg_name} bit {bit_index} from 0x{reg_value:0{bits//4}X}."
        ),
    )


def _try_execute_prefixed_bit_mutation(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """RES/SET/CHG/TSET #4, r for prefixed byte/word/long register families."""
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 3:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None or raw[1] not in (0x30, 0x31, 0x32, 0x34):
        return None

    size_kind, register_index = info
    bit_index = raw[2] & 0x0F
    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    reg_name, reg_value = _extract_register_value(before_cpu, size_kind, register_index)
    if reg_value is None:
        owner = R32[register_index] if size_kind == "long" else R32[register_index // 2]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} must be known before this prefixed bit mutation can be executed "
                f"honestly. Owner register {owner} is not yet in the current CPU state."
            ),
        )

    if bit_index >= bits:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="silicon-undefined",
            note=(
                f"{decoded.assembly} is undefined for {size_kind}-sized register operands on "
                "TLCS-900/H."
            ),
        )

    bit_mask = 1 << bit_index
    old_bit_set = bool(reg_value & bit_mask)
    op = raw[1]
    if op == 0x30:
        new_value = reg_value & ~bit_mask
        flags_updates = None
    elif op == 0x31:
        new_value = reg_value | bit_mask
        flags_updates = None
    elif op == 0x32:
        new_value = reg_value ^ bit_mask
        flags_updates = None
    else:
        new_value = reg_value | bit_mask
        flags_updates = {"zf": not old_bit_set}

    result_name, reg_updates = _build_register_update(before_cpu, size_kind, register_index, new_value)
    if reg_updates is None:
        owner = R32[register_index] if size_kind == "long" else R32[register_index // 2]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{result_name} cannot be updated honestly until owner register {owner} is known "
                "in the current CPU state."
            ),
        )

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(result_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags_updates,
        note=(
            f"Executed prefixed {decoded.mnemonic.upper()} immediate from the current real "
            f"execution subset. Updated {reg_name} bit {bit_index}."
        ),
    )


def _try_execute_prefixed_alu_immediate(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Prefixed ALU with immediate: add/adc/sub/sbc/and/xor/or/cp r, #.

    Catalog encoding: C8+zz+r : C8..CF : #<size>.
    The destination register is identified by the C8+zz+r prefix byte.
    The operation is identified by the second byte (0xC8..0xCF).
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) < 3:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    op = raw[1]
    if op not in (0xC8, 0xC9, 0xCA, 0xCB, 0xCC, 0xCD, 0xCE, 0xCF):
        return None

    size_kind, register_index = info
    if size_kind == "byte":
        if len(raw) != 3:
            return None
        imm = raw[2]
    elif size_kind == "word":
        if len(raw) != 4:
            return None
        imm = int.from_bytes(raw[2:4], "little")
    else:
        if len(raw) != 6:
            return None
        imm = int.from_bytes(raw[2:6], "little")

    reg_name, reg_value = _extract_register_value(before_cpu, size_kind, register_index)
    if reg_value is None:
        owner = R32[register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} cannot be used honestly until {owner} is known in the current "
                "CPU state."
            ),
        )

    # ADC and SBC require carry flag — block if unknown.
    if op in (0xC9, 0xCB):
        if before_cpu.flags.cf is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="runtime-state-required",
                note=(
                    "ADC/SBC with immediate requires a known carry flag, which is not modeled "
                    "in the current CPU state."
                ),
            )
        carry = int(before_cpu.flags.cf)
    else:
        carry = 0

    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    mask = (1 << bits) - 1

    if op == 0xC8:  # ADD
        result = (reg_value + imm) & mask
        flags = _compute_add_flags(size_kind, reg_value, imm)
        write_back = True
    elif op == 0xC9:  # ADC
        result = (reg_value + imm + carry) & mask
        flags = _compute_add_flags(size_kind, reg_value, imm + carry)
        write_back = True
    elif op == 0xCA:  # SUB
        result = (reg_value - imm) & mask
        flags = _compute_subtract_flags(size_kind, reg_value, imm)
        write_back = True
    elif op == 0xCB:  # SBC
        result = (reg_value - imm - carry) & mask
        flags = _compute_subtract_flags(size_kind, reg_value, imm + carry)
        write_back = True
    elif op == 0xCC:  # AND
        result = (reg_value & imm) & mask
        flags = _compute_logical_flags(size_kind, result)
        write_back = True
    elif op == 0xCD:  # XOR
        result = (reg_value ^ imm) & mask
        flags = _compute_logical_flags(size_kind, result)
        write_back = True
    elif op == 0xCE:  # OR
        result = (reg_value | imm) & mask
        flags = _compute_logical_flags(size_kind, result)
        write_back = True
    else:  # CP (0xCF)
        result = reg_value
        flags = _compute_subtract_flags(size_kind, reg_value, imm)
        write_back = False

    if write_back:
        _, reg_updates = _build_register_update(before_cpu, size_kind, register_index, result)
        if reg_updates is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-full-register",
                note=(
                    f"{reg_name} cannot be updated honestly until its owning 32-bit register "
                    "is known in the current CPU state."
                ),
            )
        written = (reg_name, "PC")
    else:
        reg_updates = None
        written = ("PC",)

    op_names = {
        0xC8: "ADD", 0xC9: "ADC", 0xCA: "SUB", 0xCB: "SBC",
        0xCC: "AND", 0xCD: "XOR", 0xCE: "OR", 0xCF: "CP",
    }
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=written,
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags,
        note=(
            f"Executed prefixed {op_names[op]} immediate from the current real execution subset."
        ),
    )


def _try_execute_prefixed_divide_immediate(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """DIV rr,# / DIV RR,# for the observed prefixed register families.

    Observed and modeled forms:
      - C8..CF : 0A : imm8   -> div R16[r], imm8
      - D8..DF/E8..EF : 0A : imm16 -> div R32[r], imm16

    Result packing follows the TLCS-900/H datasheet:
      - word destination: low byte = quotient, high byte = remainder
      - long destination: low word = quotient, high word = remainder

    Only VF is documented to change; the current subset preserves S/Z/H/N/C.
    Divide-by-zero and quotient overflow leave the destination undefined, so
    those cases are blocked honestly instead of being guessed.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) < 3:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None or raw[1] != 0x0A:
        return None

    size_kind, register_index = info

    if size_kind == "byte":
        if len(raw) != 3:
            return None
        exec_size_kind = "word"
        reg_name = R16[register_index]
        imm = raw[2]
        quotient_mask = 0xFF
        pack_shift = 8
    elif size_kind == "long":
        if len(raw) != 4:
            return None
        exec_size_kind = "long"
        reg_name = R32[register_index]
        imm = int.from_bytes(raw[2:4], "little")
        quotient_mask = 0xFFFF
        pack_shift = 16
    else:
        return None

    reg_name, reg_value = _extract_register_value(before_cpu, exec_size_kind, register_index)
    if reg_value is None:
        owner = R32[register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} must be known before this divide-immediate can be executed "
                f"honestly. Owner register {owner} is not yet in the current CPU state."
            ),
        )

    if imm == 0:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="silicon-undefined",
            note=(
                f"DIV {reg_name}, 0 is architecturally flagged through VF, but the destination "
                "result is undefined. The current subset does not guess that state."
            ),
        )

    quotient = reg_value // imm
    remainder = reg_value % imm
    if quotient > quotient_mask:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="silicon-undefined",
            note=(
                f"DIV {reg_name}, 0x{imm:0{2 if exec_size_kind == 'word' else 4}X} overflows "
                "the quotient field; the destination result is undefined on TLCS-900/H."
            ),
        )

    result = ((remainder & quotient_mask) << pack_shift) | (quotient & quotient_mask)
    _, reg_updates = _build_register_update(before_cpu, exec_size_kind, register_index, result)
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} cannot be updated honestly until its owning 32-bit register is "
                "known in the current CPU state."
            ),
        )

    flags_updates = {"vf": False}
    bits = 16 if exec_size_kind == "word" else 32
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(reg_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags_updates,
        note=(
            f"Executed prefixed DIV immediate from the current real execution subset. "
            f"{reg_name}=0x{reg_value:0{bits//4}X} / 0x{imm:0{2 if exec_size_kind == 'word' else 4}X} "
            f"-> quot=0x{quotient:0{quotient_mask.bit_length()//4}X}, "
            f"rem=0x{remainder:0{quotient_mask.bit_length()//4}X}."
        ),
    )


def _try_execute_prefixed_multiply_immediate(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """MULTU / MULS with immediate operand.

    Catalog encoding:
      long prefix (D8..DF / E8..EF): r32 : 08/09 : imm16 — 4 bytes
        multu XR32, imm16 → XR32 = unsigned(XR32 & 0xFFFF) * unsigned(imm16)
        muls  XR32, imm16 → XR32 = signed(XR32 & 0xFFFF)  * signed(imm16)
      word prefix (D0..D7): r16 : 08/09 : imm8 — 3 bytes
        multu R16, imm8 → R16 = unsigned(R16 & 0xFF) * unsigned(imm8)
        muls  R16, imm8 → R16 = signed(R16 & 0xFF)   * signed(imm8)

    Result is stored back into the full register (masked to size_kind width).
    Flags: ZF = (result == 0), SF = sign of result, CF = VF = overflow into upper half.
    For simplicity the current implementation sets ZF/SF/CF/VF honestly and NF=0.
    """
    raw = decoded.raw_bytes
    if raw is None:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    op = raw[1] if len(raw) >= 2 else None
    if op not in (0x08, 0x09):
        return None

    size_kind, register_index = info

    if size_kind == "long":
        if len(raw) != 4:
            return None
        imm_raw = int.from_bytes(raw[2:4], "little")
    elif size_kind in ("word", "byte"):
        # byte prefix (C8..CF): semantics are word — R16[r] is destination, lower byte is source
        # word prefix (D0..D7): same structure but with R16 directly
        if len(raw) != 3:
            return None
        imm_raw = raw[2]
        # Normalize to word semantics (R16 access, 8-bit operand, 16-bit result)
        size_kind = "word"
    else:
        return None

    reg_name, reg_value = _extract_register_value(before_cpu, size_kind, register_index)
    if reg_value is None:
        owner = R32[register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} must be known before this multiply-immediate can be executed "
                f"honestly. Owner register {owner} is not yet in the current CPU state."
            ),
        )

    is_signed = (op == 0x09)

    if size_kind == "long":
        # operand width = lower 16 bits of r32
        operand_bits = 16
        result_mask = 0xFFFFFFFF
    else:
        # operand width = lower 8 bits of r16
        operand_bits = 8
        result_mask = 0xFFFF

    operand_sign_bit = 1 << (operand_bits - 1)
    operand_mask = operand_sign_bit - 1 + operand_sign_bit  # 2**operand_bits - 1

    lower = reg_value & operand_mask
    imm = imm_raw & operand_mask

    if is_signed:
        if lower >= operand_sign_bit:
            lower -= 2 * operand_sign_bit
        if imm >= operand_sign_bit:
            imm -= 2 * operand_sign_bit

    raw_result = lower * imm
    result = raw_result & result_mask

    # Overflow: result does not fit in a full-width signed value of the result register size.
    # CF/VF = 1 if the product could not be represented exactly in result_mask bits.
    if is_signed:
        result_bits = result_mask.bit_length()
        result_sign_bit = 1 << (result_bits - 1)
        signed_result = result if result < result_sign_bit else result - (1 << result_bits)
        overflow = raw_result != signed_result
    else:
        overflow = raw_result > result_mask

    zf = result == 0
    result_bits = result_mask.bit_length()
    sf = bool(result >> (result_bits - 1))

    flags_updates = {
        "zf": zf,
        "sf": sf,
        "cf": overflow,
        "vf": overflow,
    }

    _, reg_updates = _build_register_update(before_cpu, size_kind, register_index, result)
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} cannot be updated honestly until its owning 32-bit register "
                "is known in the current CPU state."
            ),
        )

    mnemonic = "muls" if is_signed else "multu"
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(reg_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        flags_updates=flags_updates,
        note=(
            f"Executed prefixed {mnemonic} immediate from the current real execution subset. "
            f"{reg_name} = {lower} * {imm} = {raw_result} (result masked to {size_kind})."
        ),
    )


def _try_execute_prefixed_ext(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """EXTS / EXTZ — extend sign or zero into the upper half of a register.

    Catalog: C8+zz+r : 12 (EXTZ) / C8+zz+r : 13 (EXTS).
    - EXTS: dst<upper half> <- sign_bit of dst<lower half>
    - EXTZ: dst<upper half> <- 0
    Not applicable to byte-size registers (marked × in the catalog).
    Flags: no change.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2:
        return None

    if raw[1] not in (0x12, 0x13):
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    size_kind, register_index = info
    if size_kind == "byte":
        return None

    reg_name, current_value = _extract_register_value(before_cpu, size_kind, register_index)
    if current_value is None:
        owner_name = R32[register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} cannot be sign/zero extended honestly until {owner_name} is "
                "already known in the current CPU state."
            ),
        )

    if size_kind == "long":
        lower_half = current_value & 0xFFFF
        if raw[1] == 0x13:
            upper_fill = 0xFFFF if (lower_half & 0x8000) else 0x0000
            new_value = lower_half | (upper_fill << 16)
            op_note = "EXTS long: upper 16 bits filled with sign of bit 15."
        else:
            new_value = lower_half
            op_note = "EXTZ long: upper 16 bits cleared to zero."
    else:
        lower_half = current_value & 0xFF
        if raw[1] == 0x13:
            upper_fill = 0xFF if (lower_half & 0x80) else 0x00
            new_value = lower_half | (upper_fill << 8)
            op_note = "EXTS word: upper byte filled with sign of bit 7."
        else:
            new_value = lower_half
            op_note = "EXTZ word: upper byte cleared to zero."

    _, reg_updates = _build_register_update(before_cpu, size_kind, register_index, new_value)
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{reg_name} cannot be updated honestly until its owning 32-bit register is "
                "known in the current CPU state."
            ),
        )

    mnemonic = "EXTS" if raw[1] == 0x13 else "EXTZ"
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(reg_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        note=(
            f"Executed {mnemonic} from the current real execution subset. {op_note}"
        ),
    )


def _try_execute_c7_extended_register(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute the C7 extended-register prefix family (current-bank slices).

    The C7 prefix carries an 8-bit register selector. Codes 0xE0..0xFF name
    byte slices of the eight current-bank 32-bit registers (e.g. QC = bits
    16..23 of XBC, QIZH = bits 24..31 of XIZ). These map directly onto the
    modeled R32 state, so we execute LD / ALU / CP / INC / DEC for real.

    Codes 0x00..0x3F (explicit bank N) and 0xD0..0xDF (previous bank) require
    the multi-bank register file, which is not modeled (the LDF / pass-49
    limitation), so those block honestly. The remaining sub-ops we decode but
    do not execute yet (EX, shifts, push/pop, extz/exts, mul/div) also block.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) < 3 or raw[0] != 0xC7:
        return None

    reg_byte = raw[1]
    op = raw[2]
    reg_name = C7_REGISTER_NAMES[reg_byte]
    slice_target = c7_current_bank_slice(reg_byte)
    alt_bank_target = _resolve_c7_alt_bank_target(before_cpu, reg_byte)
    target_r32_name: str | None = None
    if slice_target is not None:
        r32_index, byte_pos = slice_target
        ext_value = _extract_byte_slice(before_cpu, r32_index, byte_pos)
        target_r32_name = R32[r32_index]
    elif alt_bank_target is not None:
        bank_index, r32_index, byte_pos = alt_bank_target
        ext_value = _extract_banked_core_byte(before_cpu, bank_index, r32_index, byte_pos)
        target_r32_name = f"bank{bank_index}:{R32[r32_index]}"
    else:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="unsupported-decoded-instruction",
            note=(
                f"C7 {decoded.assembly}: register code 0x{reg_byte:02X} does not map "
                "to a modeled current-bank, previous-bank or explicit-bank byte register."
            ),
        )
    new_pc = decoded.next_sequential_pc

    def _blocked(status: str, note: str) -> ExecutionResult:
        return _blocked_result(
            before_cpu=before_cpu, decoded=decoded, status=status, note=note,
        )

    def _need_ext() -> ExecutionResult | None:
        if ext_value is None:
            return _blocked(
                "requires-known-source-register",
                f"C7 {decoded.assembly} needs {reg_name} "
                f"(byte {byte_pos} of {target_r32_name}) to be modeled.",
            )
        return None

    def _write_ext(new_value: int) -> tuple[dict[str, int] | None, dict[str, object] | None, str]:
        if slice_target is not None:
            return (
                _build_byte_slice_update(before_cpu, r32_index, byte_pos, new_value),
                None,
                R32[r32_index],
            )
        assert alt_bank_target is not None
        reg_updates, new_banks = _build_banked_core_byte_update(
            before_cpu, bank_index, r32_index, byte_pos, new_value,
        )
        written_name = f"{R32[r32_index]}@bank{bank_index}"
        extra_updates = None if new_banks is None else {"register_banks": new_banks}
        return reg_updates, extra_updates, written_name

    in_reg_range = (0x40 <= op < 0xC8) or (0xD0 <= op < 0xE8) or (0xF0 <= op < 0xF8)
    if in_reg_range:
        hi = op & 0xF8
        # ----- register/register byte family : the "other" operand is a
        # standard current-bank R8 (op & 7), the extended reg is the C7 slice.
        if hi in (0x80, 0x88, 0x90, 0x98, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0, 0xF0):
            r8_index = op & 0x07
            r8_name, r8_value = _extract_register_value(before_cpu, "byte", r8_index)

            if hi == 0x88:  # LD R, r  → R8 = ext
                if (blocked := _need_ext()) is not None:
                    return blocked
                _, reg_updates = _build_register_update(before_cpu, "byte", r8_index, ext_value)
                if reg_updates is None and extra_cpu_updates is None:
                    return _blocked(
                        "requires-known-full-register",
                        f"ld {r8_name}, {reg_name} needs {R32[r8_index // 2]} fully known.",
                    )
                return _executed_result(
                    before_cpu=before_cpu, decoded=decoded,
                    written_registers=("PC", r8_name), memory_writes=(),
                    after_memory=before_memory, new_pc=new_pc,
                    reg_updates=reg_updates, flags_updates=None,
                    note=f"Executed ld {r8_name}, {reg_name}: {r8_name}=0x{ext_value:02X}.",
                )

            if hi == 0x98:  # LD r, R  → ext = R8
                if r8_value is None:
                    return _blocked(
                        "requires-known-source-register",
                        f"ld {reg_name}, {r8_name} needs {r8_name} "
                        f"(owner {R32[r8_index // 2]}) modeled.",
                    )
                reg_updates, extra_cpu_updates, written_name = _write_ext(r8_value)
                if reg_updates is None:
                    return _blocked(
                        "requires-known-full-register",
                        f"ld {reg_name}, {r8_name} needs {target_r32_name} fully known.",
                    )
                return _executed_result(
                    before_cpu=before_cpu, decoded=decoded,
                    written_registers=("PC", written_name), memory_writes=(),
                    after_memory=before_memory, new_pc=new_pc,
                    reg_updates=reg_updates, flags_updates=None, extra_cpu_updates=extra_cpu_updates,
                    note=f"Executed ld {reg_name}, {r8_name}: {reg_name}=0x{r8_value:02X}.",
                )

            # Arithmetic / logical / compare with R8 as destination/accumulator.
            if (blocked := _need_ext()) is not None:
                return blocked
            if r8_value is None:
                return _blocked(
                    "requires-known-source-register",
                    f"{decoded.mnemonic} {r8_name}, {reg_name} needs {r8_name} "
                    f"(owner {R32[r8_index // 2]}) modeled.",
                )
            carry = before_cpu.flags.cf
            if hi in (0x90, 0xB0) and carry is None:  # ADC / SBC
                return _blocked(
                    "runtime-state-required",
                    f"{decoded.mnemonic} {r8_name}, {reg_name} needs the carry flag known.",
                )
            if hi == 0x80:    # ADD
                result = (r8_value + ext_value) & 0xFF
                flags = _compute_add_flags("byte", r8_value, ext_value)
            elif hi == 0x90:  # ADC
                result = (r8_value + ext_value + int(carry)) & 0xFF
                flags = _compute_add_flags("byte", r8_value, ext_value + int(carry))
            elif hi == 0xA0:  # SUB
                result = (r8_value - ext_value) & 0xFF
                flags = _compute_subtract_flags("byte", r8_value, ext_value)
            elif hi == 0xB0:  # SBC
                result = (r8_value - ext_value - int(carry)) & 0xFF
                flags = _compute_subtract_flags("byte", r8_value, ext_value + int(carry))
            elif hi == 0xC0:  # AND
                result = r8_value & ext_value
                flags = _compute_logical_flags("byte", result)
            elif hi == 0xD0:  # XOR
                result = r8_value ^ ext_value
                flags = _compute_logical_flags("byte", result)
            elif hi == 0xE0:  # OR
                result = r8_value | ext_value
                flags = _compute_logical_flags("byte", result)
            else:             # 0xF0 CP — flags only, no write
                flags = _compute_subtract_flags("byte", r8_value, ext_value)
                return _executed_result(
                    before_cpu=before_cpu, decoded=decoded,
                    written_registers=("PC",), memory_writes=(),
                    after_memory=before_memory, new_pc=new_pc,
                    reg_updates=None, flags_updates=flags,
                    note=f"Executed cp {r8_name}, {reg_name}. Flags = "
                         f"0x{r8_value:02X} - 0x{ext_value:02X}.",
                )
            _, reg_updates = _build_register_update(before_cpu, "byte", r8_index, result)
            if reg_updates is None and extra_cpu_updates is None:
                return _blocked(
                    "requires-known-full-register",
                    f"{decoded.mnemonic} {r8_name}, {reg_name} needs {R32[r8_index // 2]} known.",
                )
            return _executed_result(
                before_cpu=before_cpu, decoded=decoded,
                written_registers=("PC", r8_name), memory_writes=(),
                after_memory=before_memory, new_pc=new_pc,
                reg_updates=reg_updates, flags_updates=flags,
                note=f"Executed {decoded.mnemonic} {r8_name}, {reg_name}: {r8_name}=0x{result:02X}.",
            )

        if hi in (0x60, 0x68):  # INC / DEC #3, r  — CF preserved (Toshiba RMW rule)
            if (blocked := _need_ext()) is not None:
                return blocked
            n = (op & 0x07) or 8
            if hi == 0x60:
                result = (ext_value + n) & 0xFF
                flags = _compute_add_flags("byte", ext_value, n)
            else:
                result = (ext_value - n) & 0xFF
                flags = _compute_subtract_flags("byte", ext_value, n)
            flags.pop("cf", None)  # INC/DEC on a register preserve carry.
            reg_updates, extra_cpu_updates, written_name = _write_ext(result)
            if reg_updates is None and extra_cpu_updates is None:
                return _blocked(
                    "requires-known-full-register",
                    f"{decoded.mnemonic} {n}, {reg_name} needs {target_r32_name} fully known.",
                )
            return _executed_result(
                before_cpu=before_cpu, decoded=decoded,
                written_registers=("PC", written_name), memory_writes=(),
                after_memory=before_memory, new_pc=new_pc,
                reg_updates=reg_updates, flags_updates=flags, extra_cpu_updates=extra_cpu_updates,
                note=f"Executed {decoded.mnemonic} {n}, {reg_name}: {reg_name}=0x{result:02X}.",
            )

        if hi == 0xA8:  # LD r, #3
            value = op & 0x07
            reg_updates, extra_cpu_updates, written_name = _write_ext(value)
            if reg_updates is None and extra_cpu_updates is None:
                return _blocked(
                    "requires-known-full-register",
                    f"ld {reg_name}, {value} needs {target_r32_name} fully known.",
                )
            return _executed_result(
                before_cpu=before_cpu, decoded=decoded,
                written_registers=("PC", written_name), memory_writes=(),
                after_memory=before_memory, new_pc=new_pc,
                reg_updates=reg_updates, flags_updates=None, extra_cpu_updates=extra_cpu_updates,
                note=f"Executed ld {reg_name}, {value}: {reg_name}=0x{value:02X}.",
            )

        if hi == 0xD8:  # CP r, #3 — flags only
            if (blocked := _need_ext()) is not None:
                return blocked
            imm = op & 0x07
            flags = _compute_subtract_flags("byte", ext_value, imm)
            return _executed_result(
                before_cpu=before_cpu, decoded=decoded,
                written_registers=("PC",), memory_writes=(),
                after_memory=before_memory, new_pc=new_pc,
                reg_updates=None, flags_updates=flags,
                note=f"Executed cp {reg_name}, {imm}. Flags = 0x{ext_value:02X} - 0x{imm:02X}.",
            )

        return _blocked(
            "unsupported-decoded-instruction",
            f"C7 sub-op 0x{op:02X} ({decoded.assembly}) is decoded but not executed yet.",
        )

    # ----- immediate ALU family : C7 <reg> {03,C8..CF} imm8 -----
    _C7_IMM_ALU = {0xC8: "add", 0xC9: "adc", 0xCA: "sub", 0xCB: "sbc",
                   0xCC: "and", 0xCD: "xor", 0xCE: "or", 0xCF: "cp", 0x03: "ld"}
    if op in _C7_IMM_ALU and len(raw) >= 4:
        imm = raw[3]
        if op == 0x03:  # LD r, #imm8
            reg_updates, extra_cpu_updates, written_name = _write_ext(imm)
            if reg_updates is None:
                return _blocked(
                    "requires-known-full-register",
                    f"ld {reg_name}, 0x{imm:02X} needs {target_r32_name} fully known.",
                )
            return _executed_result(
                before_cpu=before_cpu, decoded=decoded,
                written_registers=("PC", written_name), memory_writes=(),
                after_memory=before_memory, new_pc=new_pc,
                reg_updates=reg_updates, flags_updates=None, extra_cpu_updates=extra_cpu_updates,
                note=f"Executed ld {reg_name}, 0x{imm:02X}.",
            )
        if (blocked := _need_ext()) is not None:
            return blocked
        carry = before_cpu.flags.cf
        if op in (0xC9, 0xCB) and carry is None:  # ADC / SBC
            return _blocked(
                "runtime-state-required",
                f"{_C7_IMM_ALU[op]} {reg_name}, 0x{imm:02X} needs the carry flag known.",
            )
        if op == 0xC8:    # ADD
            result = (ext_value + imm) & 0xFF
            flags = _compute_add_flags("byte", ext_value, imm)
        elif op == 0xC9:  # ADC
            result = (ext_value + imm + int(carry)) & 0xFF
            flags = _compute_add_flags("byte", ext_value, imm + int(carry))
        elif op == 0xCA:  # SUB
            result = (ext_value - imm) & 0xFF
            flags = _compute_subtract_flags("byte", ext_value, imm)
        elif op == 0xCB:  # SBC
            result = (ext_value - imm - int(carry)) & 0xFF
            flags = _compute_subtract_flags("byte", ext_value, imm + int(carry))
        elif op == 0xCC:  # AND
            result = ext_value & imm
            flags = _compute_logical_flags("byte", result)
        elif op == 0xCD:  # XOR
            result = ext_value ^ imm
            flags = _compute_logical_flags("byte", result)
        elif op == 0xCE:  # OR
            result = ext_value | imm
            flags = _compute_logical_flags("byte", result)
        else:             # 0xCF CP — flags only
            flags = _compute_subtract_flags("byte", ext_value, imm)
            return _executed_result(
                before_cpu=before_cpu, decoded=decoded,
                written_registers=("PC",), memory_writes=(),
                after_memory=before_memory, new_pc=new_pc,
                reg_updates=None, flags_updates=flags,
                note=f"Executed cp {reg_name}, 0x{imm:02X}. Flags = "
                     f"0x{ext_value:02X} - 0x{imm:02X}.",
            )
        reg_updates, extra_cpu_updates, written_name = _write_ext(result)
        if reg_updates is None and extra_cpu_updates is None:
            return _blocked(
                "requires-known-full-register",
                f"{_C7_IMM_ALU[op]} {reg_name}, 0x{imm:02X} needs {target_r32_name} fully known.",
            )
        return _executed_result(
            before_cpu=before_cpu, decoded=decoded,
            written_registers=("PC", written_name), memory_writes=(),
            after_memory=before_memory, new_pc=new_pc,
            reg_updates=reg_updates, flags_updates=flags, extra_cpu_updates=extra_cpu_updates,
            note=f"Executed {_C7_IMM_ALU[op]} {reg_name}, 0x{imm:02X}: {reg_name}=0x{result:02X}.",
        )

    return _blocked(
        "unsupported-decoded-instruction",
        f"C7 sub-op 0x{op:02X} ({decoded.assembly}) is decoded but not executed yet.",
    )


def _try_execute_swi(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute SWI n as PC-advance with unmodeled BIOS side effects.

    On NGPC, SWI n invokes BIOS function n.  The BIOS executes and returns via RETI
    to the instruction immediately after the SWI.  We do not model the BIOS or its
    side effects.  Execution advances PC to the next instruction with a diagnostic note.

    This is honest for bootstrap tracing: the SWI is acknowledged and PC advances,
    but any BIOS-internal state changes (clock gear, video init, etc.) are not reflected.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 1 or not (0xF8 <= raw[0] <= 0xFF):
        return None
    if decoded.mnemonic != "swi":
        return None

    new_pc = decoded.next_sequential_pc
    if new_pc is None:
        return None

    n = raw[0] & 0x07
    modeled_fields = before_cpu.modeled_fields
    if "executed-subset" not in modeled_fields:
        modeled_fields = (*modeled_fields, "executed-subset")

    after_cpu = replace(
        before_cpu,
        pc=new_pc,
        modeled_fields=modeled_fields,
        note=(
            "This CPU state includes effects from the current minimal real execution subset. "
            f"SWI {n}: BIOS call acknowledged; BIOS execution not modeled. "
            "PC advanced to the return address. BIOS side effects are not reflected."
        ),
    )
    return ExecutionResult(
        before_cpu=before_cpu,
        after_cpu=after_cpu,
        decode=decoded,
        status="executed",
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        note=(
            f"Executed SWI {n}: BIOS call not modeled. PC advanced to next instruction "
            "as if the BIOS returned normally. Side effects of the BIOS call are omitted."
        ),
    )


def _try_execute_ei_di(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute ei n (enable interrupts) and di (disable interrupts).

    Encoding:
      06 07 = di  (disable interrupts; equivalent to ei 7 = mask all maskable IRQs)
      06 nn = ei n  (set interrupt mask level to n in [0..7])

    TLCS-900/H model: SR[12:14] is the 3-bit interrupt mask level (IFF).
    `iff_level` is the canonical field; `iff_enabled` stays as a derived
    legacy convenience (True when level < 7, False when level == 7).
    No IRQ servicing is performed: that requires the IRQ/VBlank model
    which is not yet implemented.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2 or raw[0] != 0x06:
        return None
    if decoded.mnemonic not in ("ei", "di"):
        return None

    new_pc = decoded.next_sequential_pc
    if new_pc is None:
        return None

    if raw[1] == 0x07:
        new_level = 7
        new_iff_enabled = False
        written = ("IFF", "PC")
        note = (
            "Executed di: interrupt mask level set to 7 (all maskable IRQs blocked). "
            "Actual interrupt servicing is not modeled yet."
        )
    else:
        new_level = raw[1] & 0b111
        new_iff_enabled = new_level < 7
        written = ("IFF", "PC")
        note = (
            f"Executed ei {new_level}: interrupt mask level set to {new_level}. "
            "Actual interrupt servicing is not modeled yet."
        )

    modeled_fields = before_cpu.modeled_fields
    if "executed-subset" not in modeled_fields:
        modeled_fields = (*modeled_fields, "executed-subset")
    if "IFF" not in modeled_fields:
        modeled_fields = (*modeled_fields, "IFF")

    after_cpu = replace(
        before_cpu,
        pc=new_pc,
        iff_enabled=new_iff_enabled,
        iff_level=new_level,
        modeled_fields=modeled_fields,
        note=(
            "This CPU state includes effects from the current minimal real execution subset. "
            f"{note}"
        ),
    )

    return ExecutionResult(
        before_cpu=before_cpu,
        after_cpu=after_cpu,
        decode=decoded,
        status="executed",
        written_registers=written,
        memory_writes=(),
        after_memory=before_memory,
        note=note,
    )


def _try_execute_ldf(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute LDF n (load register File pointer) — opcode 0x17 imm.

    Sets the 2-bit RFP (bits 8..9 of SR), selecting which physical
    register bank is active (TLCS-900/H has 4 banks 0..3).

    **Known limitation** : we don't model multi-bank register files —
    every R32 (XWA, XBC, …) is a single field on `NgpcCpuState`
    regardless of `rfp`. LDF advances `rfp` here so software that
    uses bank-3 calling conventions (the BIOS SYSTEM_CALL ABI) can
    track which bank is current, but the actual register **contents**
    don't physically swap. For most cc900-compiled user code this is
    harmless (the code rarely reads bank-relative state directly).
    Closing this gap is `SR Phase 3` future work.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2 or raw[0] != 0x17:
        return None
    if decoded.mnemonic != "ldf":
        return None

    new_pc = decoded.next_sequential_pc
    if new_pc is None:
        return None

    old_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
    assert old_bank is not None
    new_rfp = raw[1] & 0b11  # SR layout : RFP is the low 2 bits of imm
    banks = _ensure_register_banks(before_cpu)
    bank_slots = list(banks[old_bank].slots)
    for r32_index, field_name in enumerate(_BANKED_CORE_FIELDS):
        start = r32_index * 4
        slots = _pack_banked_slots_from_value(getattr(before_cpu.regs, field_name))
        for byte_pos, value in enumerate(slots):
            bank_slots[start + byte_pos] = value
    updated_banks = list(banks)
    updated_banks[old_bank] = BankedByteRegisters(slots=tuple(bank_slots))
    banks = tuple(updated_banks)
    new_regs = _load_visible_core_regs_from_bank(before_cpu.regs, banks, new_rfp)
    modeled_fields = before_cpu.modeled_fields
    if "executed-subset" not in modeled_fields:
        modeled_fields = (*modeled_fields, "executed-subset")
    if "RFP" not in modeled_fields:
        modeled_fields = (*modeled_fields, "RFP")

    after_cpu = replace(
        before_cpu,
        pc=new_pc,
        regs=new_regs,
        rfp=new_rfp,
        register_bank=new_rfp,
        register_banks=banks,
        modeled_fields=modeled_fields,
        note=(
            f"{before_cpu.note} Executed LDF {new_rfp}: current bank {old_bank} "
            "was flushed to the banked byte-register backing store, then the "
            f"visible XWA/XBC/XDE/XHL set was reloaded from bank {new_rfp}."
        ),
    )

    return ExecutionResult(
        before_cpu=before_cpu,
        after_cpu=after_cpu,
        decode=decoded,
        status="executed",
        written_registers=("RFP", "PC"),
        memory_writes=(),
        after_memory=before_memory,
        note=(
            f"Executed LDF {new_rfp}: register file pointer set to "
            f"{new_rfp}. Register bank physical swap is NOT modeled — "
            "all R32 fields stay in a single bank-0 view. Documented as "
            "SR Phase 3 known limitation."
        ),
    )


def _try_execute_push_pop_sr(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute PUSH SR (0x02) and POP SR (0x03).

    PUSH SR : encodes the modeled SR from `before_cpu` and writes the
              16-bit value little-endian onto the stack, decrementing XSP
              by 2. Requires every SR-derived field to be modeled (six
              flags + iff_level + rfp); otherwise stops with
              `requires-known-sr`.

    POP SR  : reads 2 bytes from the current XSP, decodes them into the
              individual SR fields and applies them to flags, iff_level
              and rfp atomically. XSP advances by 2.

    Encoding from `T900_DENSE_REF.md` opcode table:
      02  PUSH SR
      03  POP  SR
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 1:
        return None
    if raw[0] not in (0x02, 0x03):
        return None
    if decoded.mnemonic not in ("push", "pop"):
        return None

    new_pc = decoded.next_sequential_pc
    if new_pc is None:
        return None

    if raw[0] == 0x02:
        # PUSH SR
        sr_value = encode_sr_from_state(before_cpu)
        if sr_value is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-sr",
                note=(
                    "PUSH SR needs the full SR shape modeled (six ALU flags "
                    "plus iff_level plus rfp). At least one is still unknown "
                    "in the current CPU state."
                ),
            )
        data = sr_value.to_bytes(2, "little")
        return _execute_push_bytes(
            view=view,
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            data=data,
            note=(
                f"Executed PUSH SR: 16-bit SR=0x{sr_value:04X} written "
                "little-endian to the writable stack model. XSP decremented "
                "by 2."
            ),
        )

    # POP SR
    xsp = before_cpu.regs.xsp
    if xsp is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-stack-pointer",
            note=(
                "POP SR needs XSP, but the current bootstrap CPU state still "
                "leaves the stack pointer unknown."
            ),
        )
    data = _read_runtime_bytes(view, before_memory, _mask_address(xsp), 2)
    if data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="stack-data-unavailable",
            note=(
                "POP SR needs 2 readable bytes at the current XSP, but the "
                "current writable stack model and read bus do not provide them."
            ),
        )
    sr_value = int.from_bytes(data, "little")
    fields = decode_sr_to_fields(sr_value)

    new_flags = StatusFlags(
        sf=bool(fields["sf"]),
        zf=bool(fields["zf"]),
        vf=bool(fields["vf"]),
        hf=bool(fields["hf"]),
        cf=bool(fields["cf"]),
        nf=bool(fields["nf"]),
    )
    new_iff_level = int(fields["iff_level"])
    new_rfp = int(fields["rfp"])
    new_xsp = (xsp + 2) & 0xFFFFFFFF

    modeled_fields = before_cpu.modeled_fields
    if "executed-subset" not in modeled_fields:
        modeled_fields = (*modeled_fields, "executed-subset")
    if "modeled-flags-subset" not in modeled_fields:
        modeled_fields = (*modeled_fields, "modeled-flags-subset")
    if "IFF" not in modeled_fields:
        modeled_fields = (*modeled_fields, "IFF")
    if "RFP" not in modeled_fields:
        modeled_fields = (*modeled_fields, "RFP")

    after_cpu = replace(
        before_cpu,
        pc=new_pc,
        regs=replace(before_cpu.regs, xsp=new_xsp),
        flags=new_flags,
        iff_level=new_iff_level,
        iff_enabled=(new_iff_level < 7),
        rfp=new_rfp,
        sr_raw=sr_value,
        modeled_fields=modeled_fields,
        note=(
            f"{before_cpu.note} Executed POP SR: 16-bit SR=0x{sr_value:04X} "
            "loaded from the writable stack model. All six flags, iff_level "
            f"={new_iff_level} and rfp={new_rfp} are now derived from the "
            "popped value."
        ),
    )

    return ExecutionResult(
        before_cpu=before_cpu,
        after_cpu=after_cpu,
        decode=decoded,
        status="executed",
        written_registers=("SR", "IFF", "RFP", "XSP", "PC"),
        memory_writes=(),
        after_memory=before_memory,
        # memory_reads is populated automatically by build_execute_next
        # from _STEP_READS, which captured the 2-byte read above.
        note=(
            f"Executed POP SR: read 0x{sr_value:04X} from stack at "
            f"0x{xsp:08X}; XSP advanced to 0x{new_xsp:08X}. Six flags + "
            "iff_level + rfp updated atomically."
        ),
    )


@dataclass(frozen=True)
class IrqDeliveryResult:
    """Outcome of an IRQ-delivery attempt between two instructions.

    `delivered` is True if an IRQ was accepted: PC + SR pushed onto
    the stack, PC set to the vector address, iff_level raised to the
    delivered level, and the corresponding `pending_mask` bit cleared.

    `delivered` is False when no IRQ was deliverable (either nothing
    pending, or all pending IRQs are masked by the current iff_level).
    In that case `after_cpu` / `after_memory` / `after_irq_state` are
    identical to the inputs (returned for caller convenience).

    `blocked_reason` is set when the delivery couldn't proceed because
    of missing modeled state (e.g. unknown XSP or unencodable SR).
    The run loop should surface this as a stop reason rather than
    silently skipping IRQ delivery.

    `cycles_consumed` is the IRQ-entry cost (push PC + SR + vector
    load). Per Toshiba TLCS-900/H spec, ~13 cycles. Zero when no
    delivery happened.
    """

    delivered: bool
    after_cpu: NgpcCpuState
    after_memory: dict[int, int]
    after_irq_state: "IrqState"
    blocked_reason: str | None
    note: str
    cycles_consumed: int = 0


def try_deliver_pending_irq(
    view: NgpcFetchView,
    cpu: NgpcCpuState,
    memory: dict[int, int],
    irq_state: "IrqState",
) -> IrqDeliveryResult:
    """Sample IRQ controller between instructions and deliver if possible.

    Phase 3.2.2b currently models only the VBlank source (level 4 at
    `VBLANK_VECTOR_ADDRESS = 0x006FCC`). Gating rule per TLCS-900/H
    interrupt controller: a pending IRQ at level L is delivered when
    `L > cpu.iff_level` (the iff field is the *maximum masked* level,
    so an IRQ above that level interrupts).

    Stack frame layout (matches `_try_execute_reti` per Toshiba spec):
      SR pushed first (2 bytes) — ends up at XSP+4..XSP+5
      PC pushed second (4 bytes) — ends up on top at XSP..XSP+3
    XSP decremented by 6 total. RETI pops PC then SR.

    After delivery: `iff_level` is raised to the delivered IRQ's
    level (so same-or-lower-priority IRQs are masked during the ISR),
    PC is set to the vector address, and the pending bit is cleared.
    """
    from core.frame_timing import (
        IRQ_LEVEL_VBLANK,
        IrqState,
        VBLANK_VECTOR_ADDRESS,
    )

    if not irq_state.is_vblank_pending():
        return IrqDeliveryResult(
            delivered=False,
            after_cpu=cpu,
            after_memory=memory,
            after_irq_state=irq_state,
            blocked_reason=None,
            note="No IRQ pending.",
        )

    if cpu.iff_level is None:
        # Soft fail: we can't decide whether to deliver, so treat it as
        # "don't deliver this iteration". This keeps step-exec usable
        # from bootstrap CPU states (where iff_level is None until
        # software runs `ei`/`di` or pops an SR). NOT a blocked_reason
        # — the run continues normally.
        return IrqDeliveryResult(
            delivered=False,
            after_cpu=cpu,
            after_memory=memory,
            after_irq_state=irq_state,
            blocked_reason=None,
            note=(
                "VBlank IRQ is pending but iff_level is unknown; deferring "
                "delivery until the CPU's interrupt-mask state becomes modeled."
            ),
        )

    if IRQ_LEVEL_VBLANK <= cpu.iff_level:
        return IrqDeliveryResult(
            delivered=False,
            after_cpu=cpu,
            after_memory=memory,
            after_irq_state=irq_state,
            blocked_reason=None,
            note=(
                f"VBlank IRQ pending but masked: iff_level={cpu.iff_level} "
                f">= IRQ_LEVEL_VBLANK={IRQ_LEVEL_VBLANK}."
            ),
        )

    sr_value = encode_sr_from_state(cpu)
    if sr_value is None:
        # Soft defer: SR not fully modeled yet (some flag is None).
        # The run continues — once software touches the flags or
        # pops an SR, the next sample can deliver.
        return IrqDeliveryResult(
            delivered=False,
            after_cpu=cpu,
            after_memory=memory,
            after_irq_state=irq_state,
            blocked_reason=None,
            note=(
                "VBlank IRQ delivery deferred: SR shape not fully modeled "
                "(six flags + iff_level + rfp required to push SR)."
            ),
        )

    xsp = cpu.regs.xsp
    if xsp is None:
        # Soft defer: XSP not modeled yet. The run continues.
        return IrqDeliveryResult(
            delivered=False,
            after_cpu=cpu,
            after_memory=memory,
            after_irq_state=irq_state,
            blocked_reason=None,
            note=(
                "VBlank IRQ delivery deferred: XSP is unknown in the current "
                "CPU state, cannot push PC + SR onto the stack."
            ),
        )

    pc_bytes = (cpu.pc & 0xFFFFFFFF).to_bytes(4, "little")
    sr_bytes = sr_value.to_bytes(2, "little")
    # Toshiba TLCS-900/H convention: PC ends on top of the stack so RETI
    # pops PC first. We achieve that by pushing SR first (high address),
    # then PC second (low address = top of stack after both pushes).
    sr_target = (xsp - 2) & 0xFFFFFFFF
    pc_target = (xsp - 6) & 0xFFFFFFFF

    sr_status, sr_note = _check_writable_range(view, _mask_address(sr_target), 2)
    if sr_status is not None:
        return IrqDeliveryResult(
            delivered=False,
            after_cpu=cpu,
            after_memory=memory,
            after_irq_state=irq_state,
            blocked_reason=sr_status,
            note=f"VBlank IRQ delivery cannot push SR: {sr_note}",
        )
    pc_status, pc_note = _check_writable_range(view, _mask_address(pc_target), 4)
    if pc_status is not None:
        return IrqDeliveryResult(
            delivered=False,
            after_cpu=cpu,
            after_memory=memory,
            after_irq_state=irq_state,
            blocked_reason=pc_status,
            note=f"VBlank IRQ delivery cannot push PC: {pc_note}",
        )

    new_memory = dict(memory)
    for offset, byte in enumerate(sr_bytes):
        new_memory[_mask_address(sr_target + offset)] = byte
    for offset, byte in enumerate(pc_bytes):
        new_memory[_mask_address(pc_target + offset)] = byte

    new_xsp = pc_target
    new_iff_level = IRQ_LEVEL_VBLANK

    modeled_fields = cpu.modeled_fields
    for field in ("executed-subset", "IFF"):
        if field not in modeled_fields:
            modeled_fields = (*modeled_fields, field)

    after_cpu = replace(
        cpu,
        pc=VBLANK_VECTOR_ADDRESS,
        regs=replace(cpu.regs, xsp=new_xsp),
        iff_level=new_iff_level,
        iff_enabled=(new_iff_level < 7),
        modeled_fields=modeled_fields,
        note=(
            f"{cpu.note} Delivered VBlank IRQ: pushed PC=0x{cpu.pc:08X} (4B) "
            f"and SR=0x{sr_value:04X} (2B), set PC to vector 0x{VBLANK_VECTOR_ADDRESS:08X}, "
            f"raised iff_level to {new_iff_level}."
        ),
    )
    after_irq_state = irq_state.with_vblank_cleared()

    return IrqDeliveryResult(
        delivered=True,
        after_cpu=after_cpu,
        after_memory=new_memory,
        after_irq_state=after_irq_state,
        blocked_reason=None,
        note=(
            f"Delivered VBlank IRQ (level {IRQ_LEVEL_VBLANK}) via vector "
            f"0x{VBLANK_VECTOR_ADDRESS:08X}. Stack frame: PC at 0x{pc_target:08X}, "
            f"SR at 0x{sr_target:08X}, XSP advanced from 0x{xsp:08X} to 0x{new_xsp:08X}."
        ),
        cycles_consumed=IRQ_DELIVERY_CYCLES,
    )


def _try_execute_reti(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute RETI (0x07) — return from interrupt.

    TLCS-900/H stack frame on IRQ entry (per Toshiba spec):
      [XSP+0..3] = saved PC (32-bit, little-endian, on top of stack)
      [XSP+4..5] = saved SR (16-bit, little-endian, below PC)

    RETI pops PC first (4 bytes), then SR (2 bytes). XSP advances
    by 6 total. The popped SR restores all six flags + iff_level +
    rfp atomically.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 1 or raw[0] != 0x07:
        return None
    if decoded.mnemonic != "reti":
        return None

    xsp = before_cpu.regs.xsp
    if xsp is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-stack-pointer",
            note=(
                "RETI needs XSP, but the current bootstrap CPU state still "
                "leaves the stack pointer unknown."
            ),
        )

    pc_data = _read_runtime_bytes(view, before_memory, _mask_address(xsp), 4)
    if pc_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="stack-data-unavailable",
            note=(
                "RETI needs 4 readable bytes at XSP for the saved PC, but the "
                "current writable stack model and read bus do not provide them."
            ),
        )
    sr_data = _read_runtime_bytes(view, before_memory, _mask_address(xsp + 4), 2)
    if sr_data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="stack-data-unavailable",
            note=(
                "RETI needs 2 readable bytes at XSP+4 for the saved SR, but "
                "the current writable stack model and read bus do not provide them."
            ),
        )

    new_pc = int.from_bytes(pc_data, "little") & 0xFFFFFFFF
    sr_value = int.from_bytes(sr_data, "little")
    fields = decode_sr_to_fields(sr_value)
    new_flags = StatusFlags(
        sf=bool(fields["sf"]),
        zf=bool(fields["zf"]),
        vf=bool(fields["vf"]),
        hf=bool(fields["hf"]),
        cf=bool(fields["cf"]),
        nf=bool(fields["nf"]),
    )
    new_iff_level = int(fields["iff_level"])
    new_rfp = int(fields["rfp"])
    new_xsp = (xsp + 6) & 0xFFFFFFFF

    modeled_fields = before_cpu.modeled_fields
    for field in ("executed-subset", "modeled-flags-subset", "IFF", "RFP"):
        if field not in modeled_fields:
            modeled_fields = (*modeled_fields, field)

    after_cpu = replace(
        before_cpu,
        pc=new_pc,
        regs=replace(before_cpu.regs, xsp=new_xsp),
        flags=new_flags,
        iff_level=new_iff_level,
        iff_enabled=(new_iff_level < 7),
        rfp=new_rfp,
        sr_raw=sr_value,
        modeled_fields=modeled_fields,
        note=(
            f"{before_cpu.note} Executed RETI: PC=0x{new_pc:08X} popped from "
            f"0x{xsp:08X}, SR=0x{sr_value:04X} popped from 0x{(xsp + 4) & 0xFFFFFFFF:08X}; "
            f"XSP advanced to 0x{new_xsp:08X}."
        ),
    )

    return ExecutionResult(
        before_cpu=before_cpu,
        after_cpu=after_cpu,
        decode=decoded,
        status="executed",
        written_registers=("SR", "IFF", "RFP", "XSP", "PC"),
        memory_writes=(),
        after_memory=before_memory,
        note=(
            f"Executed RETI: popped PC=0x{new_pc:08X} (4B) and SR=0x{sr_value:04X} "
            f"(2B) from stack at 0x{xsp:08X}; XSP advanced by 6 to 0x{new_xsp:08X}."
        ),
    )


def _try_execute_prefixed_inc_dec(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2:
        return None

    info = _prefixed_register_execute_info(raw[0])
    if info is None:
        return None

    size_kind, register_index = info
    second = raw[1]
    count = second & 0x07
    if count == 0:
        count = 8

    if 0x60 <= second <= 0x67:
        return _execute_register_inc_dec(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind=size_kind,
            register_index=register_index,
            count=count,
            operation="inc",
        )

    if 0x68 <= second <= 0x6F:
        return _execute_register_inc_dec(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind=size_kind,
            register_index=register_index,
            count=count,
            operation="dec",
        )

    if 0x70 <= second <= 0x7F:
        # SCC cc, r — set register to 1 if condition cc is true, else 0.
        # CC index is the full low nibble (0..15), not just the low 3 bits.
        cc_idx = second & 0x0F
        if cc_idx == 0:
            condition_result = False
        elif cc_idx == 8:
            condition_result = True
        else:
            condition_result = _evaluate_condition_code(cc_idx, before_cpu.flags)
        if condition_result is None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status="requires-known-flags",
                note=(
                    f"scc {CC[cc_idx]}, r needs flag(s) the current CPU model has not yet "
                    f"tracked. Run this instruction after a prior op that sets the required "
                    f"flags so the condition becomes known."
                ),
            )
        value = 1 if condition_result else 0
        return _execute_register_immediate(
            before_cpu=before_cpu,
            before_memory=before_memory,
            decoded=decoded,
            size_kind=size_kind,
            register_index=register_index,
            value=value,
            note=(
                f"Executed scc {CC[cc_idx]}, r: condition was "
                f"{'true' if condition_result else 'false'}, register was set to {value}."
            ),
        )

    return None


def _execute_register_immediate(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    size_kind: str,
    register_index: int,
    value: int,
    note: str | None = None,
) -> ExecutionResult:
    register_name, reg_updates = _build_register_update(
        before_cpu,
        size_kind=size_kind,
        register_index=register_index,
        value=value,
    )
    if reg_updates is None:
        if size_kind == "byte":
            owner_name = R32[register_index // 2]
            target_name = R8[register_index]
        else:
            owner_name = R32[register_index]
            target_name = R16[register_index] if size_kind == "word" else R32[register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{target_name} is only partially representable in the current CPU model. "
                f"This write can only be applied honestly when {owner_name} is already known."
            ),
        )

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(register_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        note=note
        if note is not None
        else (
            "Executed an immediate register load from the current real execution subset. "
            "PC advanced and the targeted register view is now updated in the CPU state."
        ),
    )


def _try_execute_conditional_branch(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    raw = decoded.raw_bytes
    if (
        decoded.control_flow_kind != "conditional-branch"
        or raw is None
        or decoded.direct_target is None
        or decoded.next_sequential_pc is None
    ):
        return None

    if decoded.mnemonic not in {"jr", "jrl"}:
        return None

    condition_result = _evaluate_condition_code(raw[0] & 0x0F, before_cpu.flags)
    if condition_result is None:
        return None

    new_pc = decoded.direct_target if condition_result else decoded.next_sequential_pc
    branch_text = "taken" if condition_result else "not taken"
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("PC",),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=new_pc,
        reg_updates=None,
        note=(
            "Executed conditional branch from the current real execution subset. The branch "
            f"condition was modeled from the current known flag subset and was {branch_text}."
        ),
    )


def _execute_register_inc_dec(
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    size_kind: str,
    register_index: int,
    count: int,
    operation: str,
) -> ExecutionResult:
    register_name, current_value = _extract_register_value(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=register_index,
    )
    if current_value is None:
        owner_name = R32[register_index // 2] if size_kind == "byte" else R32[register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{register_name} cannot be updated honestly by {operation} until "
                f"{owner_name} is already known in the current CPU state."
            ),
        )

    mask = {"byte": 0xFF, "word": 0xFFFF, "long": 0xFFFFFFFF}[size_kind]
    delta = count if operation == "inc" else -count
    new_value = (current_value + delta) & mask
    register_name, reg_updates = _build_register_update(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=register_index,
        value=new_value,
    )
    if reg_updates is None:
        owner_name = R32[register_index // 2] if size_kind == "byte" else R32[register_index]
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{register_name} cannot be updated honestly by {operation} until "
                f"{owner_name} is already known in the current CPU state."
            ),
        )

    verb = "incremented" if operation == "inc" else "decremented"
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(register_name, "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        note=(
            "Executed prefixed register arithmetic from the current real execution subset. "
            f"The targeted register view was {verb} by {count} and PC advanced "
            "sequentially. Flags are still not modeled."
        ),
    )


def _try_execute_link_unlk(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute link Rxx, d16 / unlk Rxx (frame pointer setup/teardown).

    Encoding (TLCS-900 stack frame instructions):
      link Rxx, d16 = (0xE8+r) 0x0C disp_lo disp_hi   (4 bytes)
      unlk Rxx      = (0xE8+r) 0x0D                    (2 bytes)

    `link Rxx, d16` semantics:
      1. push Rxx (4 bytes) at XSP-4
      2. Rxx = XSP after push
      3. XSP = Rxx + sign_extend(d16)

    `unlk Rxx` semantics:
      1. XSP = Rxx
      2. pop Rxx (4 bytes) from memory at XSP, XSP += 4

    Notes:
      - link/unlk with XSP (register index 7) is forbidden — self-reference
        on the stack pointer is not modeled.
      - link XIY, N >= 5 is silicon-broken on NGPC; the decoder already
        emits a warning. The executor performs the architectural operation
        anyway so traces remain consistent — the matched-quirk plumbing
        records the broken-on-HW status separately.
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) < 2:
        return None
    first = raw[0]
    if not (0xE8 <= first <= 0xEF):
        return None
    second = raw[1]
    if second not in (0x0C, 0x0D):
        return None

    register_index = first & 0x07
    if register_index == 7:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="unmodeled-stack-pointer-alias",
            note=(
                "link/unlk XSP is not modeled because the current subset "
                "does not represent the self-referential stack-pointer case."
            ),
        )

    register_name, current_value = _extract_register_value(
        before_cpu, "long", register_index
    )
    if current_value is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{register_name} must be known before link/unlk can be executed honestly."
            ),
        )

    xsp = before_cpu.regs.xsp
    if xsp is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-stack-pointer",
            note=(
                "link/unlk needs a known XSP, but the current bootstrap CPU "
                "state still leaves the stack pointer unknown."
            ),
        )

    if second == 0x0C:
        if len(raw) != 4:
            return None
        disp16 = int.from_bytes(raw[2:4], "little", signed=True)
        new_frame = (xsp - 4) & 0xFFFFFFFF
        new_xsp = (new_frame + disp16) & 0xFFFFFFFF

        target_address = _mask_address(new_frame)
        push_data = current_value.to_bytes(4, "little")
        write_status, write_note = _check_writable_range(view, target_address, 4)
        if write_status is not None:
            return _blocked_result(
                before_cpu=before_cpu,
                decoded=decoded,
                status=write_status,
                note=write_note,
            )
        after_memory = dict(before_memory)
        for offset, value in enumerate(push_data):
            after_memory[_mask_address(target_address + offset)] = value

        reg_field = REG32_FIELDS[register_index]
        reg_updates = {reg_field: new_frame, "xsp": new_xsp}

        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=(register_name, "XSP", "PC"),
            memory_writes=(
                MemoryWrite(
                    address=target_address,
                    data=push_data,
                    note="Writable stack model updated by LINK push.",
                ),
            ),
            after_memory=after_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=reg_updates,
            note=(
                f"Executed LINK {register_name}, {disp16}: pushed previous "
                f"{register_name}=0x{current_value:08X} at 0x{target_address:06X}, "
                f"{register_name}=0x{new_frame:08X}, XSP=0x{new_xsp:08X}."
            ),
        )

    # second == 0x0D: unlk Rxx
    if len(raw) != 2:
        return None
    pop_address = _mask_address(current_value)
    popped = _read_runtime_bytes(view, before_memory, pop_address, 4)
    if popped is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="stack-data-unavailable",
            note=(
                "UNLK needs readable bytes at the frame pointer address, but the "
                "writable stack model and read bus do not provide them."
            ),
        )
    popped_value = int.from_bytes(popped, "little")
    new_xsp = (current_value + 4) & 0xFFFFFFFF
    reg_field = REG32_FIELDS[register_index]
    reg_updates = {reg_field: popped_value, "xsp": new_xsp}
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(register_name, "XSP", "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        note=(
            f"Executed UNLK {register_name}: XSP set to "
            f"{register_name}(0x{current_value:08X}), popped 4 bytes -> "
            f"{register_name}=0x{popped_value:08X}, XSP=0x{new_xsp:08X}."
        ),
    )


def _execute_push_bytes(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    data: bytes,
    note: str,
) -> ExecutionResult:
    xsp = before_cpu.regs.xsp
    if xsp is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-stack-pointer",
            note=(
                "This instruction needs XSP, but the current bootstrap CPU state still leaves "
                "the stack pointer unknown."
            ),
        )

    new_xsp = (xsp - len(data)) & 0xFFFFFFFF
    target_address = _mask_address(new_xsp)
    write_status, write_note = _check_writable_range(view, target_address, len(data))
    if write_status is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=write_status,
            note=write_note,
        )

    after_memory = dict(before_memory)
    for offset, value in enumerate(data):
        after_memory[_mask_address(target_address + offset)] = value

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("XSP", "PC"),
        memory_writes=(
            MemoryWrite(
                address=target_address,
                data=data,
                note="Writable stack model updated by PUSH-like execution.",
            ),
        ),
        after_memory=after_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates={"xsp": new_xsp},
        note=note,
    )


def _execute_pop_register(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    size_kind: str,
    register_index: int,
) -> ExecutionResult:
    xsp = before_cpu.regs.xsp
    if xsp is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-stack-pointer",
            note=(
                "This instruction needs XSP, but the current bootstrap CPU state still leaves "
                "the stack pointer unknown."
            ),
        )

    width = 2 if size_kind == "word" else 4
    data = _read_runtime_bytes(view, before_memory, _mask_address(xsp), width)
    if data is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="stack-data-unavailable",
            note=(
                "This POP-like instruction needs readable bytes at the current XSP, but the "
                "current writable stack model and read bus do not provide them."
            ),
        )

    register_name, reg_updates = _build_register_update(
        before_cpu=before_cpu,
        size_kind=size_kind,
        register_index=register_index,
        value=int.from_bytes(data, "little"),
    )
    if reg_updates is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-full-register",
            note=(
                f"{register_name} is only partially representable in the current CPU model. "
                "The POP destination cannot be updated honestly until its owning 32-bit register "
                "is already known."
            ),
        )

    reg_updates["xsp"] = (xsp + width) & 0xFFFFFFFF
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=(register_name, "XSP", "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=decoded.next_sequential_pc,
        reg_updates=reg_updates,
        note=(
            "Executed POP from the current real execution subset. The destination register was "
            "loaded from the writable stack model or read bus, and XSP advanced accordingly."
        ),
    )


def _execute_call(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    target_pc: int,
    return_pc: int,
) -> ExecutionResult:
    xsp = before_cpu.regs.xsp
    if xsp is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-stack-pointer",
            note=(
                "This CALL-like instruction needs XSP, but the current bootstrap CPU state still "
                "leaves the stack pointer unknown."
            ),
        )

    return_bytes = return_pc.to_bytes(4, "little")
    new_xsp = (xsp - 4) & 0xFFFFFFFF
    target_address = _mask_address(new_xsp)
    write_status, write_note = _check_writable_range(view, target_address, 4)
    if write_status is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status=write_status,
            note=write_note,
        )

    after_memory = dict(before_memory)
    for offset, value in enumerate(return_bytes):
        after_memory[_mask_address(target_address + offset)] = value

    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("XSP", "PC"),
        memory_writes=(
            MemoryWrite(
                address=target_address,
                data=return_bytes,
                note="Writable stack model updated with CALL return address.",
            ),
        ),
        after_memory=after_memory,
        new_pc=target_pc,
        reg_updates={"xsp": new_xsp},
        note=(
            "Executed CALL from the current real execution subset. The sequential return address "
            "was pushed to the writable stack model and PC moved to the direct target."
        ),
    )


def _execute_return(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
    stack_adjust: int,
    note: str,
) -> ExecutionResult:
    xsp = before_cpu.regs.xsp
    if xsp is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="requires-known-stack-pointer",
            note=(
                "This return-like instruction needs XSP, but the current bootstrap CPU state "
                "still leaves the stack pointer unknown."
            ),
        )

    return_bytes = _read_runtime_bytes(view, before_memory, _mask_address(xsp), 4)
    if return_bytes is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="stack-data-unavailable",
            note=(
                "This return-like instruction needs a saved PC at the current XSP, but the "
                "current writable stack model and read bus do not provide it."
            ),
        )

    new_pc = int.from_bytes(return_bytes, "little") & 0xFFFFFFFF
    new_xsp = (xsp + 4 + stack_adjust) & 0xFFFFFFFF
    return _executed_result(
        before_cpu=before_cpu,
        decoded=decoded,
        written_registers=("XSP", "PC"),
        memory_writes=(),
        after_memory=before_memory,
        new_pc=new_pc,
        reg_updates={"xsp": new_xsp},
        note=note,
    )


def _try_execute_ret_conditional(
    view: NgpcFetchView,
    before_cpu: NgpcCpuState,
    before_memory: dict[int, int],
    decoded: DecodeResult,
) -> ExecutionResult | None:
    """Execute conditional return: ret CC.

    Encoding: B0 [F0+CC_idx]  — 2 bytes.
    If condition is true: pop 4 bytes from XSP, jump to that address.
    If condition is false: advance PC (fall through).
    """
    raw = decoded.raw_bytes
    if raw is None or len(raw) != 2:
        return None
    if raw[0] != 0xB0 or not (0xF0 <= raw[1] <= 0xFF):
        return None

    cc_idx = raw[1] & 0x0F
    # CC index 8 = always true (unconditional ret) — handled by existing ret executor
    if cc_idx == 8:
        return None

    condition_result = _evaluate_condition_code(cc_idx, before_cpu.flags)
    if condition_result is None:
        # Condition flag not known — block
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="runtime-state-required",
            note=(
                f"ret {CC[cc_idx]}: the condition flag(s) required to evaluate CC index {cc_idx} "
                "are not currently known in the CPU state."
            ),
        )

    if not condition_result:
        # Condition false: fall through
        return _executed_result(
            before_cpu=before_cpu,
            decoded=decoded,
            written_registers=("PC",),
            memory_writes=(),
            after_memory=before_memory,
            new_pc=decoded.next_sequential_pc,
            reg_updates=None,
            note=(
                f"Executed conditional ret {CC[cc_idx]} (condition false = fall through) "
                "from the current real execution subset."
            ),
        )

    # Condition true: perform actual return (pop 4 bytes from stack)
    return _execute_return(
        view=view,
        before_cpu=before_cpu,
        before_memory=before_memory,
        decoded=decoded,
        stack_adjust=0,
        note=(
            f"Executed conditional ret {CC[cc_idx]} (condition true = return) "
            "from the current real execution subset."
        ),
    )


def _extract_register_value(
    before_cpu: NgpcCpuState,
    size_kind: str,
    register_index: int,
) -> tuple[str, int | None]:
    if size_kind == "byte":
        register_name = R8[register_index]
        owner_value = getattr(before_cpu.regs, REG32_FIELDS[register_index // 2])
        if owner_value is None:
            return register_name, _extract_current_banked_r8_value(before_cpu, register_index)
        shift = 8 if register_index % 2 == 0 else 0
        return register_name, (owner_value >> shift) & 0xFF

    if size_kind == "long":
        register_name = R32[register_index]
        value = getattr(before_cpu.regs, REG32_FIELDS[register_index])
        if value is None:
            return register_name, _extract_current_banked_r32_value(before_cpu, register_index)
        return register_name, value

    register_name = R16[register_index]
    owner_value = getattr(before_cpu.regs, REG32_FIELDS[register_index])
    if owner_value is None:
        return register_name, _extract_current_banked_r16_value(before_cpu, register_index)
    return register_name, owner_value & 0xFFFF


def _build_register_update(
    before_cpu: NgpcCpuState,
    size_kind: str,
    register_index: int,
    value: int,
) -> tuple[str, dict[str, int] | None]:
    if size_kind == "long":
        return (
            R32[register_index],
            {REG32_FIELDS[register_index]: value & 0xFFFFFFFF},
        )

    if size_kind == "word":
        field_name = REG32_FIELDS[register_index]
        current_value = getattr(before_cpu.regs, field_name)
        if current_value is None:
            return (R16[register_index], None)
        new_value = (current_value & 0xFFFF0000) | (value & 0xFFFF)
        return (R16[register_index], {field_name: new_value & 0xFFFFFFFF})

    field_name = REG32_FIELDS[register_index // 2]
    current_value = getattr(before_cpu.regs, field_name)
    if current_value is None:
        return (R8[register_index], None)

    if register_index % 2 == 0:
        new_value = (current_value & 0xFFFF00FF) | ((value & 0xFF) << 8)
    else:
        new_value = (current_value & 0xFFFFFF00) | (value & 0xFF)
    return (R8[register_index], {field_name: new_value & 0xFFFFFFFF})


def _current_register_bank_index(
    cpu: NgpcCpuState,
    *,
    fallback_zero: bool = False,
) -> int | None:
    if cpu.rfp is not None:
        return cpu.rfp & 0b11
    if cpu.register_bank is not None:
        return cpu.register_bank & 0b11
    if fallback_zero:
        return 0
    return None


def _pack_banked_slots_from_value(value: int | None) -> tuple[int | None, ...]:
    if value is None:
        return (None, None, None, None)
    return tuple((value >> (8 * pos)) & 0xFF for pos in range(4))


def _banked_owner_value_from_slots(slots: tuple[int | None, ...]) -> int | None:
    if any(slot is None for slot in slots):
        return None
    assert len(slots) == 4
    return (
        int(slots[0])
        | (int(slots[1]) << 8)
        | (int(slots[2]) << 16)
        | (int(slots[3]) << 24)
    ) & 0xFFFFFFFF


def _ensure_register_banks(cpu: NgpcCpuState) -> tuple[BankedByteRegisters, ...]:
    if cpu.register_banks is not None:
        return cpu.register_banks
    banks = [[None] * 16 for _ in range(4)]
    current_bank = _current_register_bank_index(cpu, fallback_zero=True)
    assert current_bank is not None
    for r32_index, field_name in enumerate(_BANKED_CORE_FIELDS):
        slots = _pack_banked_slots_from_value(getattr(cpu.regs, field_name))
        start = r32_index * 4
        for byte_pos, value in enumerate(slots):
            banks[current_bank][start + byte_pos] = value
    return tuple(BankedByteRegisters(slots=tuple(bank)) for bank in banks)


def _replace_register_bank_slot(
    banks: tuple[BankedByteRegisters, ...],
    bank_index: int,
    slot_index: int,
    value: int | None,
) -> tuple[BankedByteRegisters, ...]:
    bank_slots = list(banks[bank_index].slots)
    bank_slots[slot_index] = None if value is None else (value & 0xFF)
    updated_bank = BankedByteRegisters(slots=tuple(bank_slots))
    updated_banks = list(banks)
    updated_banks[bank_index] = updated_bank
    return tuple(updated_banks)


def _sync_core_reg_updates_into_banks(
    before_cpu: NgpcCpuState,
    reg_updates: dict[str, int] | None,
) -> tuple[BankedByteRegisters, ...] | None:
    if reg_updates is None:
        return before_cpu.register_banks
    touched = [field for field in _BANKED_CORE_FIELDS if field in reg_updates]
    if not touched:
        return before_cpu.register_banks
    current_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
    assert current_bank is not None
    banks = _ensure_register_banks(before_cpu)
    for field_name in touched:
        slots = _pack_banked_slots_from_value(reg_updates[field_name])
        start = _BANKED_CORE_FIELDS.index(field_name) * 4
        bank_slots = list(banks[current_bank].slots)
        for byte_pos, value in enumerate(slots):
            bank_slots[start + byte_pos] = value
        updated_bank = BankedByteRegisters(slots=tuple(bank_slots))
        updated_banks = list(banks)
        updated_banks[current_bank] = updated_bank
        banks = tuple(updated_banks)
    return banks


def _extract_banked_core_byte(
    before_cpu: NgpcCpuState,
    bank_index: int,
    r32_index: int,
    byte_pos: int,
) -> int | None:
    banks = _ensure_register_banks(before_cpu)
    slot_index = (r32_index * 4) + byte_pos
    return banks[bank_index].slots[slot_index]


def _build_banked_core_byte_update(
    before_cpu: NgpcCpuState,
    bank_index: int,
    r32_index: int,
    byte_pos: int,
    value: int,
) -> tuple[dict[str, int] | None, tuple[BankedByteRegisters, ...] | None]:
    banks = _ensure_register_banks(before_cpu)
    slot_index = (r32_index * 4) + byte_pos
    new_banks = _replace_register_bank_slot(banks, bank_index, slot_index, value)
    reg_updates = None
    current_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
    if current_bank == bank_index:
        field_name = _BANKED_CORE_FIELDS[r32_index]
        slots = new_banks[bank_index].slots[r32_index * 4 : (r32_index * 4) + 4]
        owner_value = _banked_owner_value_from_slots(slots)
        if owner_value is None:
            return None, None
        reg_updates = {field_name: owner_value}
    return reg_updates, new_banks


def _load_visible_core_regs_from_bank(
    regs,
    banks: tuple[BankedByteRegisters, ...],
    bank_index: int,
):
    updates: dict[str, int | None] = {}
    for r32_index, field_name in enumerate(_BANKED_CORE_FIELDS):
        slots = banks[bank_index].slots[r32_index * 4 : (r32_index * 4) + 4]
        updates[field_name] = _banked_owner_value_from_slots(slots)
    return replace(regs, **updates)


def _resolve_c7_alt_bank_target(
    before_cpu: NgpcCpuState,
    reg_byte: int,
) -> tuple[int, int, int] | None:
    if 0x00 <= reg_byte <= 0x3F:
        bank_index = reg_byte // 16
        within = reg_byte % 16
        return bank_index, within // 4, within % 4
    if 0xD0 <= reg_byte <= 0xDF:
        current_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
        assert current_bank is not None
        within = reg_byte - 0xD0
        return (current_bank - 1) & 0b11, within // 4, within % 4
    return None


def _extract_current_banked_r8_value(
    before_cpu: NgpcCpuState,
    register_index: int,
) -> int | None:
    owner_index = register_index // 2
    if owner_index >= 4:
        return None
    current_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
    assert current_bank is not None
    slot_index = owner_index * 4 + (1 if register_index % 2 == 0 else 0)
    return _ensure_register_banks(before_cpu)[current_bank].slots[slot_index]


def _extract_current_banked_r16_value(
    before_cpu: NgpcCpuState,
    register_index: int,
) -> int | None:
    if register_index >= 4:
        return None
    current_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
    assert current_bank is not None
    slot_base = register_index * 4
    slots = _ensure_register_banks(before_cpu)[current_bank].slots[slot_base : slot_base + 2]
    if any(slot is None for slot in slots):
        return None
    return (int(slots[0]) | (int(slots[1]) << 8)) & 0xFFFF


def _extract_current_banked_r32_value(
    before_cpu: NgpcCpuState,
    register_index: int,
) -> int | None:
    if register_index >= 4:
        return None
    current_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
    assert current_bank is not None
    slot_base = register_index * 4
    slots = _ensure_register_banks(before_cpu)[current_bank].slots[slot_base : slot_base + 4]
    return _banked_owner_value_from_slots(slots)


def _build_current_banked_r8_update(
    before_cpu: NgpcCpuState,
    register_index: int,
    value: int,
) -> tuple[dict[str, int] | None, dict[str, object] | None]:
    owner_index = register_index // 2
    if owner_index >= 4:
        return None, None
    current_bank = _current_register_bank_index(before_cpu, fallback_zero=True)
    assert current_bank is not None
    slot_index = owner_index * 4 + (1 if register_index % 2 == 0 else 0)
    banks = _ensure_register_banks(before_cpu)
    new_banks = _replace_register_bank_slot(banks, current_bank, slot_index, value)
    slots = new_banks[current_bank].slots[owner_index * 4 : (owner_index * 4) + 4]
    owner_value = _banked_owner_value_from_slots(slots)
    if owner_value is not None:
        return {_BANKED_CORE_FIELDS[owner_index]: owner_value}, {"register_banks": new_banks}
    return None, {"register_banks": new_banks}


def _extract_byte_slice(
    before_cpu: NgpcCpuState,
    r32_index: int,
    byte_pos: int,
) -> int | None:
    """Read one byte (`byte_pos` 0..3) of a current-bank 32-bit register.

    Used by the C7 extended-register family, where the Q-prefixed names
    (QA, QC, QIZH, …) address the upper two bytes of XWA..XSP. Returns
    None when the owning register value is not modeled.
    """
    owner_value = getattr(before_cpu.regs, REG32_FIELDS[r32_index])
    if owner_value is None:
        return None
    return (owner_value >> (8 * byte_pos)) & 0xFF


def _build_byte_slice_update(
    before_cpu: NgpcCpuState,
    r32_index: int,
    byte_pos: int,
    value: int,
) -> dict[str, int] | None:
    """Build a reg-update writing `value` into byte `byte_pos` of an R32.

    Returns None when the owning register is unknown (we cannot preserve
    the other three bytes, so the write would be dishonest).
    """
    field_name = REG32_FIELDS[r32_index]
    owner_value = getattr(before_cpu.regs, field_name)
    if owner_value is None:
        return None
    shift = 8 * byte_pos
    cleared = owner_value & (~(0xFF << shift) & 0xFFFFFFFF)
    return {field_name: (cleared | ((value & 0xFF) << shift)) & 0xFFFFFFFF}


_STEP_READS: list[MemoryRead] = []


def _read_runtime_bytes(
    view: NgpcFetchView,
    memory_bytes: dict[int, int],
    address: int,
    size: int,
) -> bytes | None:
    """Read `size` bytes starting at `address` via the runtime overlay then bus.

    Successful reads are appended to the per-step accumulator
    `_STEP_READS`. `build_execute_next` clears the accumulator at the
    start of each step and folds the collected reads into
    `ExecutionResult.memory_reads` so watchpoint matching covers every
    executor automatically.

    Returns `None` if any byte in the range is unbacked; in that case,
    no entry is appended (a partial read does not surface as a complete
    `MemoryRead`).
    """
    data = bytearray()
    for offset in range(size):
        cur_addr = _mask_address(address + offset)
        if cur_addr in memory_bytes:
            data.append(memory_bytes[cur_addr])
            continue
        read = view.bus.read_bytes(cur_addr, size=1)
        if read.status != "ok" or read.data is None:
            return None
        data.extend(read.data)
    payload = bytes(data)
    _STEP_READS.append(
        MemoryRead(
            address=_mask_address(address),
            data=payload,
            note=(
                "Executor read this contiguous range via the writable runtime "
                "overlay or the read bus to perform the current step."
            ),
        )
    )
    return payload


def _check_writable_range(
    view: NgpcFetchView,
    address: int,
    size: int,
) -> tuple[str | None, str]:
    """Check whether a memory range is writable.

    Return values:
      (None, "")                   -- range is writable, proceed normally
      ("write-discarded", note)    -- unmapped or ROM address; real hardware silently
                                      discards the write (open bus / no WE signal).
                                      Callers that model memory stores MUST continue
                                      execution.  Callers that model stack stores MAY
                                      treat this as a stop condition.
    """
    for offset in range(size):
        cur_addr = _mask_address(address + offset)
        probe = view.bus.address_space.probe(cur_addr)
        if probe.region is None:
            return (
                "write-discarded",
                (
                    f"Write to unmapped address 0x{cur_addr:06X}: silently discarded on "
                    "real hardware (open bus — nothing responds to this address)."
                ),
            )
        if probe.region.kind in READ_ONLY_REGION_KINDS:
            return (
                "write-discarded",
                (
                    f"Write to read-only region '{probe.region.name}' at 0x{cur_addr:06X}: "
                    "silently discarded on real hardware (no write-enable for this region)."
                ),
            )
    return None, ""


def _prefixed_register_execute_info(first_opcode: int) -> tuple[str, int] | None:
    if 0xC8 <= first_opcode <= 0xCF:
        return ("byte", first_opcode & 0x07)
    if 0xD0 <= first_opcode <= 0xD7:
        return ("word", first_opcode & 0x07)
    if 0xD8 <= first_opcode <= 0xDF:
        # HW-confirmed 2026-07-03: D8..DF is WORD (16-bit), not long.
        return ("word", first_opcode & 0x07)
    if 0xE8 <= first_opcode <= 0xEF:
        return ("long", first_opcode & 0x07)
    return None


def seed_cpu_state_for_execution(
    cpu: NgpcCpuState,
    register_values: dict[str, int] | None = None,
    seed_xsp: int | None = None,
) -> NgpcCpuState:
    seed_map: dict[str, int] = {}
    bank_seed_map: dict[tuple[int, str], int] = {}
    if register_values is not None:
        for register_name, value in register_values.items():
            normalized_name = register_name.upper()
            if "@BANK" in normalized_name:
                base_name, _, bank_suffix = normalized_name.partition("@BANK")
                field_name = SEEDED_BANKED_REGISTERS.get(base_name)
                if field_name is None or not bank_suffix.isdigit():
                    raise ValueError(
                        "banked seed register name must use XWA@bank0..3, "
                        "XBC@bank0..3, XDE@bank0..3 or XHL@bank0..3"
                    )
                bank_index = int(bank_suffix)
                if bank_index > 3:
                    raise ValueError("banked seed register bank must be 0..3")
                bank_seed_map[(bank_index, field_name)] = value & 0xFFFFFFFF
                continue
            field_name = SEEDABLE_REGISTERS.get(normalized_name)
            if field_name is None:
                raise ValueError(
                    "seed register name must be one of: "
                    + ", ".join(SEEDABLE_REGISTERS)
                )
            seed_map[field_name] = value & 0xFFFFFFFF

    if seed_xsp is not None:
        seed_xsp_value = seed_xsp & 0xFFFFFFFF
        current_xsp = seed_map.get("xsp")
        if current_xsp is not None and current_xsp != seed_xsp_value:
            raise ValueError("conflicting seed values were provided for XSP")
        seed_map["xsp"] = seed_xsp_value

    if not seed_map and not bank_seed_map:
        return cpu

    modeled_fields = cpu.modeled_fields
    if "user-seeded-registers" not in modeled_fields:
        modeled_fields = (*modeled_fields, "user-seeded-registers")

    note_parts = []
    for register_name, field_name in SEEDABLE_REGISTERS.items():
        if field_name in seed_map:
            note_parts.append(f"{register_name}=0x{seed_map[field_name]:08X}")
    if bank_seed_map:
        for bank_index, field_name in sorted(bank_seed_map):
            note_parts.append(
                f"{field_name.upper()}@bank{bank_index}=0x{bank_seed_map[(bank_index, field_name)]:08X}"
            )

    register_banks = cpu.register_banks
    if bank_seed_map:
        current_bank = _current_register_bank_index(cpu, fallback_zero=False)
        banks = _ensure_register_banks(cpu)
        for (bank_index, field_name), value in bank_seed_map.items():
            owner_index = _BANKED_CORE_FIELDS.index(field_name)
            bank_slots = list(banks[bank_index].slots)
            slot_base = owner_index * 4
            for byte_pos, byte_value in enumerate(_pack_banked_slots_from_value(value)):
                bank_slots[slot_base + byte_pos] = byte_value
            updated_banks = list(banks)
            updated_banks[bank_index] = BankedByteRegisters(slots=tuple(bank_slots))
            banks = tuple(updated_banks)
            if current_bank == bank_index:
                current_value = seed_map.get(field_name)
                if current_value is not None and current_value != value:
                    raise ValueError(
                        f"conflicting seed values were provided for {field_name.upper()} and "
                        f"{field_name.upper()}@bank{bank_index}"
                    )
                seed_map[field_name] = value
        register_banks = banks

    return replace(
        cpu,
        regs=replace(cpu.regs, **seed_map),
        modeled_fields=modeled_fields,
        register_banks=register_banks,
        note=(
            f"{cpu.note} A user-supplied execution seed currently sets "
            + ", ".join(note_parts)
            + " for this command invocation."
        ),
    )


def _executed_result(
    before_cpu: NgpcCpuState,
    decoded: DecodeResult,
    written_registers: tuple[str, ...],
    memory_writes: tuple[MemoryWrite, ...],
    after_memory: dict[int, int],
    new_pc: int | None,
    reg_updates: dict[str, int] | None,
    note: str,
    flags_updates: dict[str, bool | None] | None = None,
    memory_reads: tuple[MemoryRead, ...] = (),
    extra_cpu_updates: dict[str, object] | None = None,
) -> ExecutionResult:
    if new_pc is None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="unsupported-decoded-instruction",
            note=(
                "The instruction decoded successfully, but no next PC is available for honest "
                "execution in the current subset."
            ),
            memory_reads=memory_reads,
        )

    regs = before_cpu.regs
    if reg_updates is not None:
        regs = replace(regs, **reg_updates)

    flags = before_cpu.flags
    if flags_updates is not None:
        flags = replace(flags, **flags_updates)

    modeled_fields = before_cpu.modeled_fields
    if "executed-subset" not in modeled_fields:
        modeled_fields = (*modeled_fields, "executed-subset")
    if flags_updates is not None and "modeled-flags-subset" not in modeled_fields:
        modeled_fields = (*modeled_fields, "modeled-flags-subset")

    cpu_updates = {} if extra_cpu_updates is None else dict(extra_cpu_updates)
    if "register_banks" not in cpu_updates:
        synced_banks = _sync_core_reg_updates_into_banks(before_cpu, reg_updates)
        if synced_banks is not None:
            cpu_updates["register_banks"] = synced_banks
    if "register_bank" not in cpu_updates:
        current_bank = _current_register_bank_index(before_cpu, fallback_zero=False)
        if current_bank is not None:
            cpu_updates["register_bank"] = current_bank

    after_cpu = replace(
        before_cpu,
        pc=new_pc,
        regs=regs,
        flags=flags,
        modeled_fields=modeled_fields,
        note=(
            "This CPU state includes effects from the current minimal real execution subset. "
            "Only instructions whose state changes are representable and implemented are "
            f"applied. {note}"
        ),
        **cpu_updates,
    )
    return ExecutionResult(
        before_cpu=before_cpu,
        after_cpu=after_cpu,
        decode=decoded,
        status="executed",
        written_registers=written_registers,
        memory_writes=memory_writes,
        after_memory=after_memory,
        memory_reads=memory_reads,
        note=note,
    )


def _blocked_result(
    before_cpu: NgpcCpuState,
    decoded: DecodeResult,
    status: str,
    note: str,
    matched_quirk: KnownQuirkMatch | None = None,
    memory_reads: tuple[MemoryRead, ...] = (),
) -> ExecutionResult:
    return ExecutionResult(
        before_cpu=before_cpu,
        after_cpu=None,
        decode=decoded,
        status=status,
        written_registers=(),
        memory_writes=(),
        after_memory=None,
        memory_reads=memory_reads,
        note=note,
        matched_quirk=matched_quirk,
    )


def _try_stop_known_silicon_broken(
    before_cpu: NgpcCpuState,
    decoded: DecodeResult,
) -> ExecutionResult | None:
    match = match_known_silicon_broken(decoded)
    if match is not None:
        return _blocked_result(
            before_cpu=before_cpu,
            decoded=decoded,
            status="silicon-broken",
            note=match.note,
            matched_quirk=match,
        )

    return None


def _mask_address(address: int) -> int:
    return address & 0xFFFFFF


def _signed_u16(data: bytes) -> int:
    value = int.from_bytes(data, "little")
    return value - 0x10000 if value >= 0x8000 else value


def _signed_u8(value: int) -> int:
    return value - 0x100 if value >= 0x80 else value


def _post_increment_r32_index(encoded: int) -> int:
    # In the TLCS-900/H ARI_PI encoding, the register is carried in bits[4:2] of the
    # memory-form byte (each register occupies 4 slots in the full banked table).
    # Extracting bits[4:2] gives the correct 0..7 index for the current-bank registers.
    return (encoded >> 2) & 0x07


def _compute_add_flags(
    size_kind: str,
    left_value: int,
    right_value: int,
) -> dict[str, bool]:
    """Compute the modeled flag subset for an ADD-family result.

    Catalog: ADD/ADC — flags S Z H V N=0 C all modified.
    """
    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    mask = (1 << bits) - 1
    sign_bit = 1 << (bits - 1)
    result = (left_value + right_value) & mask
    return {
        "sf": bool(result & sign_bit),
        "zf": result == 0,
        "vf": bool(((~(left_value ^ right_value)) & (left_value ^ result) & sign_bit) != 0),
        "hf": bool(((left_value ^ right_value ^ result) & 0x10) != 0),
        "cf": (left_value + right_value) > mask,
        "nf": False,
    }


def _compute_logical_flags(size_kind: str, result: int) -> dict[str, bool]:
    """Compute the modeled flag subset for a logical (AND/OR/XOR) result.

    TLCS-900/H semantics: S and Z depend on result, V=0 (logical clears parity
    semantics on TLCS-900/H), H and C are cleared, N is cleared.
    """
    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    sign_bit = 1 << (bits - 1)
    return {
        "sf": bool(result & sign_bit),
        "zf": result == 0,
        "vf": False,
        "cf": False,
        "nf": False,
    }


def _compute_subtract_flags(
    size_kind: str,
    left_value: int,
    right_value: int,
) -> dict[str, bool]:
    bits = {"byte": 8, "word": 16, "long": 32}[size_kind]
    mask = (1 << bits) - 1
    sign_bit = 1 << (bits - 1)
    result = (left_value - right_value) & mask
    return {
        "sf": bool(result & sign_bit),
        "zf": result == 0,
        "vf": bool(((left_value ^ right_value) & (left_value ^ result) & sign_bit) != 0),
        "hf": bool(((left_value ^ right_value ^ result) & 0x10) != 0),
        "cf": left_value < right_value,
        "nf": True,
    }


def _evaluate_condition_code(cc_index: int, flags: StatusFlags) -> bool | None:
    sf = flags.sf
    zf = flags.zf
    vf = flags.vf
    cf = flags.cf

    if cc_index == 0:
        return False
    if cc_index == 8:
        return True
    if cc_index == 1:
        return None if sf is None or vf is None else sf != vf
    if cc_index == 2:
        return None if sf is None or vf is None or zf is None else zf or (sf != vf)
    if cc_index == 3:
        return None if cf is None or zf is None else cf or zf
    if cc_index == 4:
        return vf
    if cc_index == 5:
        return sf
    if cc_index == 6:
        return zf
    if cc_index == 7:
        return cf
    if cc_index == 9:
        return None if sf is None or vf is None else sf == vf
    if cc_index == 10:
        return None if sf is None or vf is None or zf is None else (not zf) and (sf == vf)
    if cc_index == 11:
        return None if cf is None or zf is None else (not cf) and (not zf)
    if cc_index == 12:
        return None if vf is None else not vf
    if cc_index == 13:
        return None if sf is None else not sf
    if cc_index == 14:
        return None if zf is None else not zf
    if cc_index == 15:
        return None if cf is None else not cf
    raise ValueError(f"unsupported condition-code index: {cc_index}")
