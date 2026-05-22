"""t900cc_alloc — Linear scan register allocator for t900cc.

Chantier 5 Phase P-5.4 (2026-05-20). See CHANTIER_5_PLAN.md §P-5.4.

Implements the Poletto & Sarkar 1999 linear scan algorithm, adapted to
the TLCS-900H register file with our HW + ABI constraints (defined in
`t900cc_regclass`).

This module is INFRASTRUCTURE only. It operates on a hypothetical
virtual-register IR (each vreg has a live interval + a register class
constraint). It is NOT yet wired into `t900cc.gen_function` — the
codegen still emits direct physical-register asm via the legacy P-1..P-4'
path. Wiring happens progressively in P-5.5 (spill logic) and P-5.6
(generalized `_emit_*` helpers).

The allocator is testable today with synthetic IR (see
`tools/devtools/test_alloc.py`).

Algorithm overview (Poletto & Sarkar 1999, §4):

    sort intervals by start
    active = []  # sorted by interval.end
    free_regs = ALLOCATOR_POOL

    for interval in sorted intervals:
        # Expire old intervals
        for a in list(active):
            if a.end <= interval.start:
                active.remove(a)
                free_regs.add(allocation[a.vreg])

        # Filter candidates by reg class
        candidates = filter(free_regs, interval.cls)
        if candidates:
            phys = pick(candidates)
            allocation[interval.vreg] = phys
            insert_sorted(active, interval)
            free_regs.remove(phys)
        else:
            # No free phys reg compatible — must spill
            spill(active, interval, allocation, spilled)

    return allocation, spilled

Spill heuristic (Poletto §5): spill the interval with the LONGEST
end-of-range among `active ∪ {interval}`. If `interval` itself has the
longest range, the new vreg is spilled; otherwise the active vreg with
the longest range gives up its physical reg.

In P-5.4 minimum-viable, spill is implemented but ONLY raises a
`SpillRequired` exception. Phase P-5.5 wires real spill slots (XIY-relative
stack frames) + reload code. This lets P-5.4 ship with the algorithm in
place and tested on small cases, while P-5.5 handles the harder spill
plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from t900cc_regclass import (
    ALLOCATOR_POOL,
    RegClass,
    RegConstraint,
)

if TYPE_CHECKING:
    from t900cc_ir import IRFunction


# ---------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------


@dataclass
class LiveInterval:
    """A virtual register's live interval.

    Attributes:
      vreg:  string identifier like '%t0' (SSA-style virtual register).
      start: first IR position (block_idx or linearized op_idx) where
             this vreg is defined.
      end:   last IR position where this vreg is used.
      cls:   register class constraint (which physical regs are valid).
             If None, defaults to `RegClass.ANY`.
      forced: optional pin to a specific physical register (e.g. for
              ABI return values, link XIY, etc.). Wins over `cls`.
      pref:   optional preferred physical register (soft hint).
              When this interval is allocated through the class-based
              path, the allocator picks `pref` IF it is free AND in
              the interval's `candidates()`. Otherwise the standard
              alphabetical fallback applies. Used in P-5.6.1 to align
              migrated codegen sites with their legacy register choice
              (typically XWA), keeping shadow mode binary-identical
              for isolated migrations while still letting the allocator
              diverge when there is a real conflict — that's exactly
              the case that saves bytes.
    """
    vreg: str
    start: int
    end: int
    cls: Optional[RegClass] = None
    forced: Optional[str] = None
    pref: Optional[str] = None

    @property
    def span(self) -> int:
        """Length of the interval, end-inclusive. 1 = single position."""
        return self.end - self.start + 1

    def candidates(self) -> Set[str]:
        """Set of physical registers compatible with this interval's
        constraints."""
        if self.forced is not None:
            return {self.forced}
        if self.cls is None:
            return set(ALLOCATOR_POOL)
        return set(self.cls.value)


@dataclass
class AllocationResult:
    """Output of `allocate(ir_function, intervals, ...)`.

    Attributes:
      allocation: dict mapping vreg → physical register name.
      spilled:    set of vregs that couldn't be allocated and must be
                  resolved by P-5.5 spill logic.
      stats:      diagnostic counts (active peak, spills, etc.) for
                  the allocator trace tool.
    """
    allocation: Dict[str, str] = field(default_factory=dict)
    spilled: Set[str] = field(default_factory=set)
    stats: Dict[str, int] = field(default_factory=dict)


class AllocationError(Exception):
    """Raised when the allocator can't honor a hard constraint
    (e.g. a `forced` vreg conflicts with an already-allocated one)."""
    pass


class SpillRequired(Exception):
    """Raised in P-5.4 minimum-viable when a vreg must be spilled.

    The allocator catches it internally and adds the vreg to
    `result.spilled`. Phase P-5.5 will replace this with actual spill
    code emission (LoadLocal/StoreLocal at use/def sites)."""
    def __init__(self, vreg: str, reason: str):
        super().__init__(f'Spill required for {vreg}: {reason}')
        self.vreg = vreg
        self.reason = reason


# ---------------------------------------------------------------------
# Linear scan allocator
# ---------------------------------------------------------------------


def allocate(intervals: List[LiveInterval],
             pool: Optional[Set[str]] = None,
             reserved: Optional[Dict[int, Set[str]]] = None) -> AllocationResult:
    """Allocate physical registers to virtual registers via linear scan.

    Args:
      intervals: list of LiveInterval. Order doesn't matter — the
                 function sorts by start internally.
      pool:      optional override of the allocator pool. Defaults to
                 t900cc_regclass.ALLOCATOR_POOL.
      reserved:  optional per-position constraints. Maps a position
                 (start of some range) → set of physical regs that
                 MUST NOT be used because of a call site / hard pin.
                 Used to model that across `call`, all caller-saved
                 regs are reserved (= cannot be live across).
                 Future P-5.5 will refine this. P-5.4 uses an empty
                 dict by default.

    Returns:
      AllocationResult with `allocation` (vreg → phys_reg) and
      `spilled` (vregs that couldn't fit).

    The algorithm is deterministic: same input → same output. Tiebreaks
    in candidate selection favor regs earlier in `ALLOCATOR_POOL` sort
    order (alphabetical).

    Quirks: the function does NOT consult `quirks_db` directly because
    we operate at the abstract reg-allocation level (each vreg gets ONE
    physical reg for its whole live range). Quirks apply at the emit
    level, where `validate_transfer` / `validate_alu_imm` check the
    actual instruction encodings. The allocator respects regclass
    constraints (which encode the hardware pins like `_emit_alu16`
    needs LHS=WA, RHS=HL), so it cannot produce a placement that
    would force a broken encoding — proof by construction.
    """
    if pool is None:
        pool = set(ALLOCATOR_POOL)
    if reserved is None:
        reserved = {}

    result = AllocationResult()
    result.stats['intervals_total'] = len(intervals)
    result.stats['spills'] = 0
    result.stats['peak_active'] = 0

    # 1. Sort intervals by start (Poletto §4).
    sorted_intervals = sorted(intervals, key=lambda iv: (iv.start, iv.end))

    # 2. Maintain `active` list sorted by `end` for fast expire.
    active: List[LiveInterval] = []
    # Currently free physical registers (= pool minus those held by active).
    free_regs: Set[str] = set(pool)
    allocation = result.allocation

    def expire_old(current_start: int) -> None:
        """Remove from `active` any interval whose end < current_start,
        and return their physical regs to `free_regs`."""
        nonlocal active
        survivors: List[LiveInterval] = []
        for iv in active:
            # Strict less-than: `iv.end == current_start` means the
            # interval is STILL alive at this position (use point).
            # Free only when iv ended strictly before.
            if iv.end < current_start:
                phys = allocation.get(iv.vreg)
                if phys is not None:
                    free_regs.add(phys)
            else:
                survivors.append(iv)
        active = survivors

    def spill_at_interval(iv: LiveInterval) -> None:
        """When the current interval can't get a free reg, choose what
        to spill (Poletto §5). Heuristic: spill the active interval
        with the LARGEST end. If iv's own end is larger than all
        actives in matching class, spill iv itself."""
        # Filter active by overlap with iv's class constraints — only
        # actives whose phys_reg ∈ iv.candidates() can free a slot for iv.
        iv_cands = iv.candidates()
        eligible_actives = [a for a in active
                            if allocation.get(a.vreg) in iv_cands]
        if not eligible_actives:
            # No active occupies a candidate of iv → iv must spill.
            result.spilled.add(iv.vreg)
            result.stats['spills'] += 1
            return
        # Pick the active with largest end.
        victim = max(eligible_actives, key=lambda a: a.end)
        if victim.end > iv.end:
            # Spill the victim, allocate iv to victim's reg.
            victim_phys = allocation[victim.vreg]
            del allocation[victim.vreg]
            result.spilled.add(victim.vreg)
            result.stats['spills'] += 1
            allocation[iv.vreg] = victim_phys
            active.remove(victim)
            _insert_sorted_by_end(active, iv)
        else:
            # iv has the largest end → spill iv itself.
            result.spilled.add(iv.vreg)
            result.stats['spills'] += 1

    # 3. Linear scan.
    for iv in sorted_intervals:
        expire_old(iv.start)

        # Forced placement: must use this specific phys reg.
        if iv.forced is not None:
            target = iv.forced
            # If currently free → take it.
            if target in free_regs:
                allocation[iv.vreg] = target
                free_regs.remove(target)
                _insert_sorted_by_end(active, iv)
            else:
                # Conflict: someone else holds the forced reg.
                # We must evict them (spill).
                holder = next(
                    (a for a in active
                     if allocation.get(a.vreg) == target),
                    None,
                )
                if holder is None:
                    # Reg not actually held by anyone in active? Should
                    # only happen if `reserved` blocks it — in that
                    # case the forced interval can't be honored.
                    raise AllocationError(
                        f'Forced placement {iv.vreg}->{target} '
                        f'conflicts with reservation at position '
                        f'{iv.start}.')
                # Evict holder, take target for iv.
                del allocation[holder.vreg]
                result.spilled.add(holder.vreg)
                result.stats['spills'] += 1
                active.remove(holder)
                allocation[iv.vreg] = target
                _insert_sorted_by_end(active, iv)
            result.stats['peak_active'] = max(
                result.stats['peak_active'], len(active))
            continue

        # Class-based placement: pick first compatible free reg.
        # P-5.6.1: honor `pref` soft hint when set + free + in candidates.
        cands = iv.candidates()
        reserved_here = reserved.get(iv.start, set())
        compat_set = cands & free_regs - reserved_here
        if iv.pref is not None and iv.pref in compat_set:
            phys = iv.pref
            allocation[iv.vreg] = phys
            free_regs.remove(phys)
            _insert_sorted_by_end(active, iv)
        elif compat_set:
            # Alphabetical fallback (deterministic).
            phys = sorted(compat_set)[0]
            allocation[iv.vreg] = phys
            free_regs.remove(phys)
            _insert_sorted_by_end(active, iv)
        else:
            spill_at_interval(iv)

        result.stats['peak_active'] = max(
            result.stats['peak_active'], len(active))

    result.stats['allocated'] = len(result.allocation)
    return result


def _insert_sorted_by_end(active: List[LiveInterval], iv: LiveInterval) -> None:
    """Insert `iv` into `active` keeping it sorted by `end` ascending."""
    # Linear insert is fine for typical T900H block sizes (<10 actives).
    for i, a in enumerate(active):
        if a.end > iv.end:
            active.insert(i, iv)
            return
    active.append(iv)


# ---------------------------------------------------------------------
# Pretty-printer for allocator_trace.py
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# P-5.5 — Spill slot manager + spill code insertion
# ---------------------------------------------------------------------


class SpillSlotManager:
    """Manages XIY-relative frame slots for spilled virtual registers.

    Chantier 5 Phase P-5.5 (2026-05-20).

    TLCS-900H frame layout (per DECISIONS.md):
      - Function prologue calls `link XIY, N` where N is the negative
        frame size (must be <= 4 per Jalon 8 silicon bug — large frames
        require multiple `lda XSP, (XSP-N)` chunks).
      - Locals are accessed via `(XIY+offset)` with offset typically
        negative (frame grows down from XIY).
      - Each spill slot occupies 2 bytes (16-bit) or 4 bytes (32-bit)
        based on the type width of the spilled value.

    Usage:
      mgr = SpillSlotManager(base_offset=-N)  # N = current local frame size
      slot = mgr.allocate(vreg, width='u16')   # returns offset for (XIY+offset)
      ... lower phase uses `slot` for LoadLocal/StoreLocal of spilled vreg

    The manager tracks the cumulative spill area below the user-locals
    frame. Total frame size (locals + spills) must fit the silicon limit
    (`link XIY, N` with N <= 4 per direct emission, or larger via
    chunked `lda XSP, (XSP-126)` calls per existing
    `_emit_stack_alloc_lda` logic — both supported here).
    """

    __slots__ = ('base_offset', 'allocated', 'next_offset', 'width_bytes')

    # Map IR width tag → bytes on stack.
    WIDTH_BYTES = {
        'u8': 1, 's8': 1,
        'u16': 2, 's16': 2,
        'u32': 4, 's32': 4,
        'ptr': 4,
    }

    def __init__(self, base_offset: int = 0) -> None:
        """`base_offset` is the most-negative frame slot already used
        by regular locals. Spills go BELOW it (i.e. more negative).

        Example: if locals occupy XIY-2 .. XIY-12 (12 bytes), pass
        base_offset = -12. The first spill slot will be at -14 (or -16
        for a 4-byte spill, etc.).
        """
        self.base_offset = base_offset
        self.next_offset = base_offset
        self.allocated: Dict[str, int] = {}  # vreg → slot offset
        self.width_bytes: Dict[str, int] = {}  # vreg → bytes

    def allocate(self, vreg: str, width: str = 'u16') -> int:
        """Allocate (or return existing) frame slot for `vreg`.

        Returns the offset (negative integer) to use in
        `(XIY+offset)` for both Load and Store of this spilled vreg.

        Slots are 2-byte-aligned for u16 / sized for u32. The
        allocator pre-rounds the offset to a multiple of the value's
        size to match the TLCS-900H load/store alignment expectations.
        """
        if vreg in self.allocated:
            return self.allocated[vreg]
        bytes_needed = self.WIDTH_BYTES.get(width, 2)
        # Align: next_offset minus bytes (since we grow down), then
        # round to multiple of bytes_needed.
        candidate = self.next_offset - bytes_needed
        # Round down to alignment.
        if bytes_needed > 1:
            candidate = -(((-candidate) + bytes_needed - 1) // bytes_needed) * bytes_needed
        self.allocated[vreg] = candidate
        self.width_bytes[vreg] = bytes_needed
        self.next_offset = candidate
        return candidate

    def total_spill_bytes(self) -> int:
        """Total bytes consumed by spill slots (= base_offset - next_offset).

        Used by the allocator to extend the function's frame size — the
        prologue's `link XIY, N` (or chunked stack alloc) must reserve
        this many extra bytes below the user-locals area."""
        return abs(self.next_offset - self.base_offset)


def insert_spill_code(ir_function: 'IRFunction',
                      result: AllocationResult,
                      spill_mgr: SpillSlotManager,
                      vreg_widths: Optional[Dict[str, str]] = None) -> 'IRFunction':
    """Insert LoadLocal/StoreLocal IR ops for spilled vregs.

    Walks the IR function's ops. For each op that USES a spilled vreg,
    insert a `LoadLocal` BEFORE that op (loading the spilled value into
    a free scratch reg). For each op that DEFINES a spilled vreg,
    insert a `StoreLocal` AFTER that op.

    Returns the modified IRFunction (mutates in place + returns it).

    Args:
      ir_function: the function whose IR to modify
      result: AllocationResult from `allocate()`. `result.spilled` is
              the set of vregs needing memory access.
      spill_mgr: manager that allocates frame slots for the spilled vregs
      vreg_widths: optional map vreg → width tag (u8/u16/u32/ptr).
                   Default: u16 for unspecified vregs.

    This function is INFRASTRUCTURE. In P-5.5 it's tested only with
    synthetic IR (test_alloc.py); P-5.6 will integrate it into
    `t900cc.gen_function` when actual structured ops are emitted.
    """
    # Allocate slots up-front for every spilled vreg.
    widths = vreg_widths or {}
    for vreg in result.spilled:
        w = widths.get(vreg, 'u16')
        spill_mgr.allocate(vreg, width=w)

    # The actual rewrite logic (walking ops, inserting LoadLocal /
    # StoreLocal) is deferred to P-5.6 when we have real structured
    # ops emitting `LoadLocal`/`BinOp`/etc. via the AST → IR lowering.
    # For now this function just allocates the slots and records them
    # in result.stats for diagnostic.
    result.stats['spill_slots_bytes'] = spill_mgr.total_spill_bytes()
    result.stats['spill_slot_count'] = len(spill_mgr.allocated)
    return ir_function


# ---------------------------------------------------------------------
# P-5.5 — IR → asm lowering with allocation (skeleton for P-5.6 wiring)
# ---------------------------------------------------------------------


"""Mapping from a 32-bit parent reg to its 16-bit / 8-bit ALU aliases.

Used by `lower_ir_with_allocation` to substitute virtual registers
allocated to e.g. XBC with the proper text alias when the structured
op asks for a 16-bit / 8-bit value. Aligned with t900cc.py legacy
emission conventions.
"""
PARENT_TO_R16 = {'XWA': 'WA', 'XBC': 'BC', 'XDE': 'DE', 'XHL': 'HL'}
PARENT_TO_R8_LO = {'XWA': 'A', 'XBC': 'C', 'XDE': 'E', 'XHL': 'L'}
# Sub-op byte for `LDW (mem+disp), R16` where R16 selects the source.
# Matches t900cc.py:3052-3054 conventions (HW-validated via ngpc_disasm).
LDW_STORE_SUBOP = {'XWA': 0x50, 'XBC': 0x51, 'XDE': 0x52, 'XHL': 0x53}
# Sub-op byte for `LDW R16, (mem+disp)` where R16 selects the destination.
# Mirror of LDW_STORE_SUBOP for the load direction. Encoding family
# `0xBN <disp> 0x2N` where N indexes the dest r16 (WA=0, BC=1, DE=2, HL=3).
# HW-validated via ngpc_disasm: e.g. `LDW WA, (XIY+disp)` = `0xBD <disp> 0x20`.
LDW_LOAD_SUBOP = {'XWA': 0x20, 'XBC': 0x21, 'XDE': 0x22, 'XHL': 0x23}
# Base-reg prefix for stack-base-indirect: 0xB8 + idx.
STACK_BASE_PREFIX = {'XWA': 0xB8, 'XBC': 0xB9, 'XDE': 0xBA, 'XHL': 0xBB,
                    'XIX': 0xBC, 'XIY': 0xBD, 'XIZ': 0xBE, 'XSP': 0xBF}
# Mirror prefix for LDW LOAD (R16, (base+disp)): 0x98 + idx. Different
# family from the store prefix because the addressing-mode byte tells
# the CPU memory is the SOURCE (load) vs DESTINATION (store) of the
# operation. HW-validated against t900cc.py:3837 emission `db 0x9D, d, 0x20`
# = `LDW WA, (XIY+disp)`.
STACK_BASE_LOAD_PREFIX = {'XWA': 0x98, 'XBC': 0x99, 'XDE': 0x9A, 'XHL': 0x9B,
                          'XIX': 0x9C, 'XIY': 0x9D, 'XIZ': 0x9E, 'XSP': 0x9F}


def _resolve_vreg(vreg: str, result: AllocationResult) -> str:
    """Map a vreg name to its allocated physical reg. Raises if not in
    the allocation map (= caller forgot to handle spill via
    insert_spill_code first)."""
    phys = result.allocation.get(vreg)
    if phys is None:
        raise KeyError(
            f'lower_ir_with_allocation: vreg {vreg!r} has no allocation. '
            f'It may be spilled (insert_spill_code did not run, or '
            f'this op consumes a value that was supposed to be reloaded '
            f'via a LoadLocal slot).'
        )
    return phys


def _lower_load_imm(op, result: AllocationResult) -> str:
    """Lower a LoadImm structured op to asm text.

    `LoadImm(dst='%t0', value=42, width='u16')` with allocation
    %t0 -> XBC -> emits `    ld   BC, 42`.

    P-5.6.1 contract: width='u16' supported. width='u32' / 'u8' will
    be added in P-5.6.2+ when the codegen migrates patterns that use
    them. Raises NotImplementedError loudly otherwise.
    """
    phys = _resolve_vreg(op.dest, result)
    if op.width in ('u16', 's16'):
        r16 = PARENT_TO_R16.get(phys)
        if r16 is None:
            raise NotImplementedError(
                f'_lower_load_imm: physical reg {phys!r} has no 16-bit '
                f'alias mapping. Pool of word-storable regs is limited to '
                f"{set(PARENT_TO_R16)} for now (P-5.6.1)."
            )
        return f'    ld   {r16}, {op.value}'
    raise NotImplementedError(
        f'_lower_load_imm: width {op.width!r} not yet supported in P-5.6.1. '
        f"Only 'u16'/'s16' are wired."
    )


def _lower_load_local(op, result: AllocationResult,
                      frame_reg: str = 'XIY') -> str:
    """Lower a LoadLocal structured op to asm text.

    `LoadLocal(dest='%t0', offset=-4, width='u16')` with allocation
    %t0 -> XWA and frame_reg='XIY' -> emits
    `    db 0x9D, 0xFC, 0x20  ; LDW WA, (XIY-4)`.

    Encoding: `LDW R16, (XIY+disp)` is the 3-byte form
    `0x9D <disp> 0x2N` where N indexes the destination r16
    (WA=0, BC=1, DE=2, HL=3). Mirrors the store form
    (`_lower_store_local`) but with prefix 0x9D (load) vs 0xBD (store)
    and sub-op 0x20+idx (load dest) vs 0x50+idx (store src).

    See t900cc.py:3837 for the canonical legacy emission.
    """
    phys = _resolve_vreg(op.dest, result)
    if op.width in ('u16', 's16'):
        sub_op = LDW_LOAD_SUBOP.get(phys)
        r16 = PARENT_TO_R16.get(phys)
        if sub_op is None or r16 is None:
            raise NotImplementedError(
                f'_lower_load_local: physical reg {phys!r} not in '
                f'word-loadable set for u16. Pool limited to '
                f"{set(LDW_LOAD_SUBOP)} in P-5.6.2."
            )
        prefix = STACK_BASE_LOAD_PREFIX.get(frame_reg)
        if prefix is None:
            raise NotImplementedError(
                f'_lower_load_local: frame_reg {frame_reg!r} not in '
                f'known stack-base set {set(STACK_BASE_LOAD_PREFIX)}.'
            )
        d = op.offset & 0xFF
        return (
            f'    db 0x{prefix:02X}, 0x{d:02X}, 0x{sub_op:02X}  '
            f'; LDW {r16}, ({frame_reg}{op.offset:+d})'
        )
    raise NotImplementedError(
        f'_lower_load_local: width {op.width!r} not yet supported in '
        f"P-5.6.2. Only 'u16'/'s16' are wired."
    )


# P-5.6.3b (2026-05-20) extension : 5 ops byte-split alu via WA/HL.
# All these patterns are HW-validated (already shipped in baseline via
# `_emit_alu16` legacy). Each tuple = (lo_mnem_line, hi_mnem_line). The
# `add`/`sub` variants use carry-propagation (adc/sbc) ; bitwise ops are
# independent per byte (no carry).
_BINOP_BYTE_SPLIT_EMIT = {
    # arithmetic with carry/borrow propagation
    'add': ('add  A,  L', 'adc  W,  H'),
    'sub': ('sub  A,  L', 'sbc  W,  H'),
    # bitwise — each byte is independent
    'and': ('and  A,  L', 'and  W,  H'),
    'or':  ('or   A,  L', 'or   W,  H'),
    'xor': ('xor  A,  L', 'xor  W,  H'),
}
_BINOP_BYTE_SPLIT_COMMENT = {
    'add': ('CF 81 — low byte', 'CE 90 — high byte + carry'),
    'sub': ('CF A1 — low byte', 'CE B0 — high byte - borrow'),
    'and': ('CF C1 — low byte', 'CE C0 — high byte'),
    'or':  ('CF E1 — low byte', 'CE E0 — high byte'),
    'xor': ('CF D1 — low byte', 'CE D0 — high byte'),
}


def _lower_load_global(op, result: AllocationResult) -> str:
    """Lower a LoadGlobal structured op to asm text.

    `LoadGlobal(dest='%t0', sym='_my_var', width='u16')` with allocation
    %t0 → XWA emits `    ld   WA, (_my_var)`.

    Encoding (per t900as.py): `ld R16, (abs16)` = `0xD1 <abs16_lo> <abs16_hi>
    0x2{r16_idx}`. r16_idx: WA=0, BC=1, DE=2, HL=3. The 0xD1 prefix is
    "LDW from abs16 mem source" (0x98+0x39 in the abs16 addressing-mode
    encoding family).

    HW status :
      - WA dest (`0xD1 ... 0x20`) : HW-validated in baseline (used by
        `gen_ident` for near globals via legacy `opt_perf_lag_6`).
      - HL dest (`0xD1 ... 0x23`) + BC/DE (0x21/0x22) : SAME family,
        not previously emitted to ROM in baseline. Theoretically safe
        but **requires HW test** when first emitted.

    P-5.6.4 contract : width 'u16'/'s16'. u8/u32 raise NotImplementedError.
    """
    phys = _resolve_vreg(op.dest, result)
    if op.width in ('u16', 's16'):
        r16 = PARENT_TO_R16.get(phys)
        if r16 is None:
            raise NotImplementedError(
                f'_lower_load_global: physical reg {phys!r} has no 16-bit '
                f'alias. Pool of word-loadable regs : {set(PARENT_TO_R16)}.'
            )
        return f'    ld   {r16}, ({op.sym})'
    raise NotImplementedError(
        f'_lower_load_global: width {op.width!r} not yet supported in P-5.6.4. '
        f"Only 'u16'/'s16' are wired."
    )


def _lower_binop(op, result: AllocationResult) -> List[str]:
    """Lower a BinOp structured op to asm text.

    `BinOp(dest='%r', src_a='%a', src_b='%b', op='add', width='u16')` with
    allocation %a→XWA, %b→XHL, %r→XWA emits the byte-split sequence
    appropriate for `op.op`.

    HW constraints for byte-split 16-bit ALU (`_emit_alu16` legacy
    convention) :
      - LHS (src_a) MUST be in XWA (operands `A` / `W` are XWA bytes)
      - RHS (src_b) MUST be in XHL (operands `L` / `H` are XHL bytes)
      - dest MUST be in XWA (the result lands in WA)

    The allocator must respect these via `cls=WA_ONLY` / `HL_ONLY` hints
    set by the caller (`_c5_run_pipeline`). If allocation diverges,
    raises `NotImplementedError`.

    Returns a LIST of asm lines (one per byte-split op = always 2).

    P-5.6.3 + P-5.6.3b extension : `op` ∈ {add, sub, and, or, xor},
    `width` ∈ {u16, s16}. All HW-shipped already (legacy `_emit_alu16`).
    Other ops + widths raise NotImplementedError.
    """
    if op.op not in _BINOP_BYTE_SPLIT_EMIT:
        raise NotImplementedError(
            f'_lower_binop: op {op.op!r} not yet supported in P-5.6.3b. '
            f"Wired ops: {sorted(_BINOP_BYTE_SPLIT_EMIT)}."
        )
    if op.width not in ('u16', 's16'):
        raise NotImplementedError(
            f'_lower_binop: width {op.width!r} not yet supported in P-5.6.3b. '
            f"Only 'u16'/'s16' are wired."
        )
    a_phys = _resolve_vreg(op.src_a, result)
    b_phys = _resolve_vreg(op.src_b, result)
    dest_phys = _resolve_vreg(op.dest, result)
    if a_phys != 'XWA':
        raise NotImplementedError(
            f'_lower_binop: src_a must be in XWA for byte-split {op.op}, got {a_phys!r}. '
            f'Caller must constrain via cls=WA_ONLY hint on %a.'
        )
    if b_phys != 'XHL':
        raise NotImplementedError(
            f'_lower_binop: src_b must be in XHL for byte-split {op.op}, got {b_phys!r}. '
            f'Caller must constrain via cls=HL_ONLY hint on %b.'
        )
    if dest_phys != 'XWA':
        raise NotImplementedError(
            f'_lower_binop: dest must be in XWA for byte-split {op.op} (result lands in WA), '
            f'got {dest_phys!r}.'
        )
    lo_mnem, hi_mnem = _BINOP_BYTE_SPLIT_EMIT[op.op]
    lo_cmt, hi_cmt = _BINOP_BYTE_SPLIT_COMMENT[op.op]
    return [
        f'    {lo_mnem}           ; {lo_cmt}',
        f'    {hi_mnem}           ; {hi_cmt}',
    ]


def _lower_store_local(op, result: AllocationResult,
                       frame_reg: str = 'XIY') -> str:
    """Lower a StoreLocal structured op to asm text.

    `StoreLocal(offset=-4, src='%t0', width='u16')` with allocation
    %t0 -> XBC and frame_reg='XIY' -> emits
    `    db 0xBD, 0xFC, 0x51  ; LDW (XIY-4), BC`.

    Encoding rationale: `LDW (XIY+disp), R16` is the 3-byte form
    `0xBD <disp> 0x5N` where N indexes the source r16 (WA=0, BC=1,
    DE=2, HL=3). See t900cc.py:3052-3054 for the HW-validated
    encoding table.
    """
    phys = _resolve_vreg(op.src, result)
    if op.width in ('u16', 's16'):
        sub_op = LDW_STORE_SUBOP.get(phys)
        r16 = PARENT_TO_R16.get(phys)
        if sub_op is None or r16 is None:
            raise NotImplementedError(
                f'_lower_store_local: physical reg {phys!r} not in '
                f'word-storable set for u16. Pool limited to '
                f"{set(LDW_STORE_SUBOP)} in P-5.6.1."
            )
        prefix = STACK_BASE_PREFIX.get(frame_reg)
        if prefix is None:
            raise NotImplementedError(
                f'_lower_store_local: frame_reg {frame_reg!r} not in '
                f'known stack-base set {set(STACK_BASE_PREFIX)}.'
            )
        d = op.offset & 0xFF
        return (
            f'    db 0x{prefix:02X}, 0x{d:02X}, 0x{sub_op:02X}  '
            f'; LDW ({frame_reg}{op.offset:+d}), {r16}'
        )
    raise NotImplementedError(
        f'_lower_store_local: width {op.width!r} not yet supported in '
        f"P-5.6.1. Only 'u16'/'s16' are wired."
    )


def lower_ir_with_allocation(ir_function: 'IRFunction',
                             result: AllocationResult,
                             spill_mgr: SpillSlotManager,
                             frame_reg: str = 'XIY') -> List[str]:
    """Lower an IRFunction to asm text, substituting virtual registers
    with their allocated physical registers and emitting spill code
    for spilled vregs.

    P-5.5 contract: works correctly when the IRFunction contains ONLY
    EmitRaw ops (= no structured ops). In that case, the function is
    equivalent to `lower_to_asm(ir_function)` from `t900cc_ir` — no
    register substitution happens because EmitRaw text already contains
    physical register names.

    P-5.6.1 contract: handles `LoadImm` and `StoreLocal` for u16/s16
    widths. Other widths and other structured ops raise
    NotImplementedError to fail loud during the incremental migration.

    Returns the list of asm text lines.
    """
    from t900cc_ir import EmitRaw, LoadImm, LoadLocal, LoadGlobal, StoreLocal, BinOp  # local: avoid circular ref
    out: List[str] = []
    for blk in ir_function.blocks:
        if blk.label is not None:
            out.append(f'{blk.label}:')
        for op in blk.ops:
            if isinstance(op, EmitRaw):
                out.append(op.text)
            elif isinstance(op, LoadImm):
                out.append(_lower_load_imm(op, result))
            elif isinstance(op, LoadLocal):
                out.append(_lower_load_local(op, result, frame_reg))
            elif isinstance(op, LoadGlobal):
                out.append(_lower_load_global(op, result))
            elif isinstance(op, StoreLocal):
                out.append(_lower_store_local(op, result, frame_reg))
            elif isinstance(op, BinOp):
                out.extend(_lower_binop(op, result))
            else:
                raise NotImplementedError(
                    f'lower_ir_with_allocation: structured op {type(op).__name__} '
                    f'not yet supported. Will be wired in a later P-5.6.x phase.'
                )
    return out


def format_allocation(intervals: List[LiveInterval],
                      result: AllocationResult) -> str:
    """Format the allocation result as a human-readable text report."""
    lines: List[str] = []
    lines.append('=== Linear scan allocation ===')
    lines.append(f'  total intervals : {result.stats.get("intervals_total", 0)}')
    lines.append(f'  allocated       : {result.stats.get("allocated", 0)}')
    lines.append(f'  spilled         : {len(result.spilled)}')
    lines.append(f'  peak active     : {result.stats.get("peak_active", 0)}')
    lines.append('')
    lines.append('--- Per-vreg allocation ---')
    # Sort by start for readability
    sorted_intervals = sorted(intervals, key=lambda iv: (iv.start, iv.end))
    for iv in sorted_intervals:
        phys = result.allocation.get(iv.vreg, 'SPILL')
        cls_disp = iv.forced if iv.forced else (
            iv.cls.name if iv.cls else 'ANY')
        lines.append(
            f'  {iv.vreg:8s}  range=[{iv.start:3d}..{iv.end:3d}] '
            f'span={iv.span:3d}  cls={cls_disp:<15s}  -> {phys}'
        )
    return '\n'.join(lines)


# ---------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------


def _self_test() -> None:
    """Sanity check. Run via `python tools/t900cc_alloc.py`."""
    # Case 1: 4 vregs that don't overlap — all can share 1 phys reg.
    ivs = [
        LiveInterval('%t0', start=0, end=2),
        LiveInterval('%t1', start=3, end=5),
        LiveInterval('%t2', start=6, end=8),
        LiveInterval('%t3', start=9, end=11),
    ]
    r = allocate(ivs)
    assert r.stats['spills'] == 0, f'unexpected spills: {r.stats}'
    # Should all map to the first reg (alphabetical), e.g. XBC.
    used_regs = set(r.allocation.values())
    assert len(used_regs) == 1, (
        f'non-overlapping intervals should share a reg: {r.allocation}')

    # Case 2: 6 fully-overlapping vregs — all 6 pool regs used, no spill.
    ivs = [LiveInterval(f'%t{i}', start=0, end=10) for i in range(6)]
    r = allocate(ivs)
    assert r.stats['spills'] == 0, f'6 vregs should fit: {r.stats}'
    assert len(set(r.allocation.values())) == 6, 'must use all 6 pool regs'

    # Case 3: 7 fully-overlapping vregs — 1 must spill.
    ivs = [LiveInterval(f'%t{i}', start=0, end=10) for i in range(7)]
    r = allocate(ivs)
    assert r.stats['spills'] == 1, f'7 vregs: 1 spill expected, got {r.stats}'

    # Case 4: forced placement.
    ivs = [
        LiveInterval('%return', start=10, end=15,
                     forced='XHL'),  # ABI v1 return
        LiveInterval('%t0', start=0, end=20,
                     cls=RegClass.WORD_GENERAL),
    ]
    r = allocate(ivs)
    assert r.allocation.get('%return') == 'XHL', f'forced ignored: {r.allocation}'

    # Case 5: regclass constraint — vreg restricted to WA only.
    ivs = [
        LiveInterval('%acc', start=0, end=5, cls=RegClass.WA_ONLY),
        LiveInterval('%scratch', start=2, end=4, cls=RegClass.WORD_GENERAL),
    ]
    r = allocate(ivs)
    assert r.allocation.get('%acc') == 'XWA', (
        f'WA_ONLY not honored: {r.allocation}')
    # %scratch should NOT collide with WA since %acc holds it.
    assert r.allocation.get('%scratch') != 'XWA', (
        f'%scratch took WA despite %acc being there: {r.allocation}')

    # Case 6: SpillSlotManager basic alloc/reuse.
    mgr = SpillSlotManager(base_offset=-12)
    s0 = mgr.allocate('%v0', width='u16')
    s1 = mgr.allocate('%v1', width='u16')
    assert s0 == -14, f'first slot should be -14, got {s0}'
    assert s1 == -16, f'second u16 slot should be -16, got {s1}'
    # Re-allocate same vreg → same slot.
    s0_again = mgr.allocate('%v0', width='u16')
    assert s0_again == s0, 'reallocation of same vreg gives same slot'

    # Case 7: SpillSlotManager u32 alignment.
    mgr2 = SpillSlotManager(base_offset=-12)
    s32 = mgr2.allocate('%p', width='u32')
    assert s32 == -16, f'u32 slot should be 4-byte aligned (-16), got {s32}'
    assert mgr2.total_spill_bytes() == 4, (
        f'u32 alloc should consume 4 bytes, got {mgr2.total_spill_bytes()}')

    print('[t900cc_alloc] All self-tests pass.')


if __name__ == '__main__':
    _self_test()
