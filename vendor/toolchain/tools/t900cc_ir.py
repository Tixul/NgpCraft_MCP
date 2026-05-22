"""t900cc_ir — Intermediate representation for the t900cc codegen backend.

Phase P-1 of Chantier 4 (backend refactor). See BACKEND_DESIGN.md.

This module defines the IR types used by t900cc.py to buffer code
emission between `gen_function` and the final text output. In P-1 the
IR is intentionally minimal: a single `EmitRaw` op that wraps the
existing `emit_instr` text, plus a buffer + lowering function that
writes the text out verbatim. The result is functionally identical
to the pre-refactor behavior — the output `.asm` must be
byte-for-byte the same.

Later phases will progressively replace `EmitRaw` call sites with
structured ops (`Add`, `Load`, `Cmp`, …) and add optimization passes
between buffering and lowering.

The IR types below are **stubs** — they are not yet emitted by
t900cc.py. They are documented here so that:
1. The design is reviewable before code that uses them lands
2. Future phases can add fields without breaking existing call sites
3. Tests can import and validate type shapes from day 1

Type widths are tracked on every value that holds data. Width values:
- `'u8'`, `'s8'`   — 8-bit unsigned / signed
- `'u16'`, `'s16'` — 16-bit
- `'u32'`, `'s32'` — 32-bit
- `'ptr'`          — 32-bit far pointer (treated like u32 for arithmetic)

Virtual registers are strings like `'%t0'`, `'%t1'`, …  In P-1 they
are unused (EmitRaw owns its register names directly inside the text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------
# IR op types (Phase P-1: only EmitRaw is actually emitted. Other ops
# are stubs documented for future phases.)
# ---------------------------------------------------------------------


@dataclass(slots=True)
class EmitRaw:
    """Pass-through op holding a literal line of asm text.

    Used in P-1 to wrap every existing `emit_instr` / `emit_label` /
    `emit_comment` call. The IR buffer holds a list of these; the
    lowering pass writes their `text` field out verbatim.

    Later phases will progressively replace `EmitRaw` instances with
    structured ops at chosen call sites. `EmitRaw` remains as an
    escape hatch for inline `__asm()` blocks and exotic sequences
    that don't fit the structured IR cleanly.
    """
    text: str


# Stubs below — not used in P-1, but defined for design review.

@dataclass(slots=True)
class LoadLocal:
    """Load a local variable from the frame at XIY+offset."""
    dest: str          # virtual register, e.g. '%t0'
    offset: int        # byte offset from XIY (signed)
    width: str         # 'u8', 's8', 'u16', 's16', 'u32', 's32', 'ptr'


@dataclass(slots=True)
class StoreLocal:
    """Store a value back to a local at XIY+offset."""
    offset: int
    src: str
    width: str


@dataclass(slots=True)
class LoadGlobal:
    """Load a global by symbol name."""
    dest: str
    sym: str
    width: str


@dataclass(slots=True)
class StoreGlobal:
    """Store to a global by symbol name."""
    sym: str
    src: str
    width: str


@dataclass(slots=True)
class LoadImm:
    """Load a constant value."""
    dest: str
    value: int
    width: str


@dataclass(slots=True)
class LoadIndirect:
    """Load *(ptr + offset)."""
    dest: str
    ptr_src: str
    offset: int
    width: str


@dataclass(slots=True)
class StoreIndirect:
    """Store to *(ptr + offset)."""
    ptr_src: str
    offset: int
    src: str
    width: str


@dataclass(slots=True)
class BinOp:
    """Binary ALU op (add, sub, and, or, xor, mul, div, mod)."""
    dest: str
    src_a: str
    src_b: str
    op: str            # 'add', 'sub', 'and', 'or', 'xor', 'mul', 'div', 'mod'
    width: str
    signed: bool = False


@dataclass(slots=True)
class Shift:
    """Shift op (sll, sra, srl)."""
    dest: str
    src: str
    n: int             # shift amount (may itself be a virtual reg in a later extension)
    op: str            # 'shl', 'shr_a', 'shr_l'
    width: str


@dataclass(slots=True)
class Compare:
    """Set virtual flags for a subsequent Branch."""
    src_a: str
    src_b: str          # may be a virtual reg or '#imm' for inline literal
    signed: bool
    width: str


@dataclass(slots=True)
class Branch:
    """Conditional branch on virtual flag."""
    cond: str          # 'Z', 'NZ', 'C', 'NC', 'LT', 'LE', 'GT', 'GE', 'EQ', 'NE'
    target: str        # label name


@dataclass(slots=True)
class Jump:
    """Unconditional branch."""
    target: str


@dataclass(slots=True)
class Label:
    """Emission point for a label (target of branch/jump)."""
    name: str


@dataclass(slots=True)
class Call:
    """Function call."""
    sym: str
    args: list[str] = field(default_factory=list)
    ret_dest: Optional[str] = None
    abi: str = 'cdecl'     # 'cdecl' or 'adecl'


@dataclass(slots=True)
class Ret:
    """Function return."""
    src: Optional[str] = None


@dataclass(slots=True)
class Trunc:
    """Width-narrowing cast (e.g. u32 → u8)."""
    dest: str
    src: str
    src_width: str
    dest_width: str


@dataclass(slots=True)
class Extz:
    """Zero-extension cast (e.g. u8 → u32)."""
    dest: str
    src: str
    src_width: str
    dest_width: str


@dataclass(slots=True)
class Exts:
    """Sign-extension cast (e.g. s8 → s32)."""
    dest: str
    src: str
    src_width: str
    dest_width: str


# ---------------------------------------------------------------------
# IR containers (block-level structure introduced in Chantier 5 P-5.1)
# ---------------------------------------------------------------------


class BasicBlock:
    """A basic block: a contiguous run of ops with a single entry point.

    Introduced in Chantier 5 Phase P-5.1 (2026-05-20). The first block
    of a function has `label=None` (the function entry — its
    address is implicitly the function symbol). Subsequent blocks have
    a `label` (the asm label that branch/jump ops target).

    Successors / predecessors are filled in by a later CFG-construction
    pass (P-5.2 timing). For P-5.1 they remain empty — block-level
    structure is in place, but flow edges aren't computed yet.

    Ops are a flat list: `EmitRaw` in P-5.1 (legacy compatibility),
    structured ops (`LoadLocal`, `BinOp`, etc.) progressively wired in
    P-5.6+.
    """

    __slots__ = ('label', 'ops', 'succ', 'pred')

    def __init__(self, label: Optional[str] = None) -> None:
        self.label: Optional[str] = label
        self.ops: list = []
        self.succ: list = []  # filled in P-5.2 by CFG builder
        self.pred: list = []

    def append(self, op) -> None:
        self.ops.append(op)

    def __len__(self) -> int:
        return len(self.ops)

    def __iter__(self):
        return iter(self.ops)


class IRFunction:
    """All IR for a single C function: a sequence of basic blocks.

    Use `current_block` to append ops to the most-recently-opened
    block. Use `start_block(label)` to close the current block and
    open a new one (called when the codegen emits a label).

    Backward-compat: `ops` property flattens all blocks' ops in
    emission order. This is what `lower_to_asm` walks for P-1..P-5.1
    where ops are still mostly `EmitRaw`.

    Each block's LABEL (if present) is metadata, not an op. The
    lowering pass emits `<label>:` BEFORE the block's ops, so the
    asm output is identical to a flat `EmitRaw`-of-the-label model.
    """

    __slots__ = ('name', 'blocks')

    def __init__(self, name: str = '') -> None:
        self.name: str = name
        # Always have at least an entry block (label=None). The
        # codegen appends to current_block before the first
        # start_block() call.
        self.blocks: list[BasicBlock] = [BasicBlock(label=None)]

    @property
    def current_block(self) -> BasicBlock:
        return self.blocks[-1]

    def start_block(self, label: str) -> BasicBlock:
        """Close the current block (no terminator added) and open a new
        block with the given label. Returns the new block."""
        new_blk = BasicBlock(label=label)
        self.blocks.append(new_blk)
        return new_blk

    def append(self, op) -> None:
        """Append an op to the current block."""
        self.current_block.append(op)

    @property
    def ops(self) -> list:
        """Flat view of all ops in emission order. Labels are NOT
        included here — they are block metadata. For an iter that
        also emits labels at block starts, see `lower_to_asm`."""
        flat = []
        for blk in self.blocks:
            flat.extend(blk.ops)
        return flat

    def __len__(self) -> int:
        return sum(len(blk) for blk in self.blocks)


class IRBuffer:
    """Legacy flat IR buffer kept for backward-compat during P-5.1 migration.

    The Chantier 5 native container is `IRFunction` (block-level). New
    code should target `IRFunction` directly; `IRBuffer` is retained
    only for the pre-C5 round-trip check in t900cc.py during the
    transition. Will be removed when P-5.6+ wiring completes.
    """

    __slots__ = ('ops',)

    def __init__(self) -> None:
        self.ops: list = []

    def append(self, op) -> None:
        self.ops.append(op)

    def extend(self, ops) -> None:
        self.ops.extend(ops)

    def __len__(self) -> int:
        return len(self.ops)

    def __iter__(self):
        return iter(self.ops)


# ---------------------------------------------------------------------
# Lowering
# ---------------------------------------------------------------------


# P-5.6.1 wiring: legacy-default convention for round-trip lowering.
# t900cc historically uses XWA as the universal accumulator, so when
# `lower_to_asm` lowers a structured op WITHOUT a real allocation, it
# assumes the vreg "would have" been placed in XWA. This keeps the IR
# round-trip check consistent with `self.lines` (the legacy text the
# codegen emitted alongside the structured op) on migrated patterns.
#
# When the real allocator runs (`lower_ir_with_allocation`), the
# `pref='XWA'` hint normally aligns with this assumption — but the
# allocator is free to diverge when XWA is live for another vreg,
# in which case the shadow-mode comparison legitimately fires.
_LEGACY_DEFAULT_PHYS = 'XWA'
_LEGACY_R16_FOR_PHYS = {'XWA': 'WA', 'XBC': 'BC', 'XDE': 'DE', 'XHL': 'HL'}
_LEGACY_LDW_SUBOP = {'XWA': 0x50, 'XBC': 0x51, 'XDE': 0x52, 'XHL': 0x53}
_LEGACY_LDW_LOAD_SUBOP = {'XWA': 0x20, 'XBC': 0x21, 'XDE': 0x22, 'XHL': 0x23}
_LEGACY_STACK_PREFIX = {'XWA': 0xB8, 'XBC': 0xB9, 'XDE': 0xBA, 'XHL': 0xBB,
                       'XIX': 0xBC, 'XIY': 0xBD, 'XIZ': 0xBE, 'XSP': 0xBF}
_LEGACY_STACK_LOAD_PREFIX = {'XWA': 0x98, 'XBC': 0x99, 'XDE': 0x9A, 'XHL': 0x9B,
                            'XIX': 0x9C, 'XIY': 0x9D, 'XIZ': 0x9E, 'XSP': 0x9F}


def _lower_load_imm_default(op: 'LoadImm') -> str:
    """Round-trip helper for LoadImm using the legacy XWA default."""
    if op.width not in ('u16', 's16'):
        raise NotImplementedError(
            f'_lower_load_imm_default: width {op.width!r} not yet supported '
            f"in P-5.6.1 round-trip. Only 'u16'/'s16' are wired."
        )
    r16 = _LEGACY_R16_FOR_PHYS[_LEGACY_DEFAULT_PHYS]
    return f'    ld   {r16}, {op.value}'


def _phys_from_vreg_name(vreg: str) -> str:
    """Convention de naming pour vregs HL-bound : `%hlN` → XHL.

    P-5.6.3 (2026-05-20). Permet aux helpers qui ont besoin de placer
    un vreg en XHL (e.g. src_b d'un BinOp byte-split) de communiquer
    cette contrainte au round-trip lowering via le NOM du vreg, sans
    avoir besoin d'un side-channel dict. Le pipeline allocator
    (`_c5_run_pipeline`) lit aussi cette convention via
    `self._c5_vreg_cls`.

    Returns the legacy default phys reg (XWA) unless the name signals
    otherwise.
    """
    if vreg.startswith('%hl'):
        return 'XHL'
    return _LEGACY_DEFAULT_PHYS


def _lower_load_global_default(op: 'LoadGlobal') -> str:
    """Round-trip helper for LoadGlobal using legacy XWA default, with
    `%hl*` naming convention overriding to XHL (P-5.6.4 mirror of
    LoadLocal lowering convention).
    """
    if op.width not in ('u16', 's16'):
        raise NotImplementedError(
            f'_lower_load_global_default: width {op.width!r} not yet supported '
            f"in P-5.6.4 round-trip. Only 'u16'/'s16' are wired."
        )
    phys = _phys_from_vreg_name(op.dest)
    r16 = _LEGACY_R16_FOR_PHYS[phys]
    return f'    ld   {r16}, ({op.sym})'


def _lower_load_local_default(op: 'LoadLocal', frame_reg: str = 'XIY') -> str:
    """Round-trip helper for LoadLocal using the legacy XWA default,
    overridden to XHL when vreg name follows the `%hlN` convention.
    """
    if op.width not in ('u16', 's16'):
        raise NotImplementedError(
            f'_lower_load_local_default: width {op.width!r} not yet supported '
            f"in P-5.6.2 round-trip. Only 'u16'/'s16' are wired."
        )
    phys = _phys_from_vreg_name(op.dest)
    sub_op = _LEGACY_LDW_LOAD_SUBOP[phys]
    r16 = _LEGACY_R16_FOR_PHYS[phys]
    prefix = _LEGACY_STACK_LOAD_PREFIX[frame_reg]
    d = op.offset & 0xFF
    return (
        f'    db 0x{prefix:02X}, 0x{d:02X}, 0x{sub_op:02X}  '
        f'; LDW {r16}, ({frame_reg}{op.offset:+d})'
    )


# P-5.6.3b (2026-05-20) extension : same 5 byte-split ops as alloc.py.
# Kept synchronized with `_BINOP_BYTE_SPLIT_EMIT` in t900cc_alloc.py.
_BINOP_DEFAULT_EMIT = {
    'add': ('add  A,  L', 'adc  W,  H'),
    'sub': ('sub  A,  L', 'sbc  W,  H'),
    'and': ('and  A,  L', 'and  W,  H'),
    'or':  ('or   A,  L', 'or   W,  H'),
    'xor': ('xor  A,  L', 'xor  W,  H'),
}
_BINOP_DEFAULT_COMMENT = {
    'add': ('CF 81 — low byte', 'CE 90 — high byte + carry'),
    'sub': ('CF A1 — low byte', 'CE B0 — high byte - borrow'),
    'and': ('CF C1 — low byte', 'CE C0 — high byte'),
    'or':  ('CF E1 — low byte', 'CE E0 — high byte'),
    'xor': ('CF D1 — low byte', 'CE D0 — high byte'),
}


def _lower_binop_default(op: 'BinOp') -> list:
    """Round-trip helper for BinOp using legacy WA-LHS / HL-RHS / WA-dest
    convention. Mirrors `_lower_binop` in t900cc_alloc.py.
    """
    if op.op not in _BINOP_DEFAULT_EMIT:
        raise NotImplementedError(
            f'_lower_binop_default: op {op.op!r} not yet supported '
            f"in P-5.6.3b round-trip. Wired ops: {sorted(_BINOP_DEFAULT_EMIT)}."
        )
    if op.width not in ('u16', 's16'):
        raise NotImplementedError(
            f'_lower_binop_default: width {op.width!r} not yet supported '
            f"in P-5.6.3b round-trip. Only 'u16'/'s16' are wired."
        )
    lo_mnem, hi_mnem = _BINOP_DEFAULT_EMIT[op.op]
    lo_cmt, hi_cmt = _BINOP_DEFAULT_COMMENT[op.op]
    return [
        f'    {lo_mnem}           ; {lo_cmt}',
        f'    {hi_mnem}           ; {hi_cmt}',
    ]


def _lower_store_local_default(op: 'StoreLocal', frame_reg: str = 'XIY') -> str:
    """Round-trip helper for StoreLocal using the legacy XWA default."""
    if op.width not in ('u16', 's16'):
        raise NotImplementedError(
            f'_lower_store_local_default: width {op.width!r} not yet supported '
            f"in P-5.6.1 round-trip. Only 'u16'/'s16' are wired."
        )
    phys = _LEGACY_DEFAULT_PHYS
    sub_op = _LEGACY_LDW_SUBOP[phys]
    r16 = _LEGACY_R16_FOR_PHYS[phys]
    prefix = _LEGACY_STACK_PREFIX[frame_reg]
    d = op.offset & 0xFF
    return (
        f'    db 0x{prefix:02X}, 0x{d:02X}, 0x{sub_op:02X}  '
        f'; LDW ({frame_reg}{op.offset:+d}), {r16}'
    )


def lower_to_asm(ir) -> list[str]:
    """Lower an IR container (IRFunction or legacy IRBuffer) to asm text.

    For `IRFunction`: walks each block, emitting `<label>:` before the
    block's ops if the block has a non-None label. The entry block
    (label=None) emits no extra line — its ops flow directly.

    For `IRBuffer` (legacy): walks the flat ops list, emitting each
    `EmitRaw.text` as a line.

    Phase P-5.1: only `EmitRaw` ops were handled. P-5.6.1 wiring adds
    `LoadImm` and `StoreLocal` lowering using the legacy XWA default
    (= the placement convention the codegen used before structured ops
    were introduced). This keeps the round-trip check consistent with
    `self.lines` when the codegen emits BOTH legacy text and structured
    ops at a migrated site. Other structured ops still raise
    NotImplementedError until their P-5.6.x phase wires them in.
    """
    if isinstance(ir, IRFunction):
        out: list[str] = []
        for blk in ir.blocks:
            if blk.label is not None:
                out.append(f'{blk.label}:')
            for op in blk.ops:
                if isinstance(op, EmitRaw):
                    out.append(op.text)
                elif isinstance(op, LoadImm):
                    out.append(_lower_load_imm_default(op))
                elif isinstance(op, LoadLocal):
                    out.append(_lower_load_local_default(op))
                elif isinstance(op, LoadGlobal):
                    out.append(_lower_load_global_default(op))
                elif isinstance(op, StoreLocal):
                    out.append(_lower_store_local_default(op))
                elif isinstance(op, BinOp):
                    out.extend(_lower_binop_default(op))
                else:
                    raise NotImplementedError(
                        f"lower_to_asm: structured op {type(op).__name__} "
                        f"not yet supported in round-trip. Add a "
                        f"_lower_*_default helper in a later P-5.6.x phase."
                    )
        return out
    if isinstance(ir, IRBuffer):
        out = []
        for op in ir.ops:
            if isinstance(op, EmitRaw):
                out.append(op.text)
            else:
                raise NotImplementedError(
                    f"IRBuffer lowering: got {type(op).__name__}. "
                    f"Structured ops require IRFunction (P-5.1+ container)."
                )
        return out
    raise TypeError(f"lower_to_asm: expected IRFunction or IRBuffer, got {type(ir).__name__}")
