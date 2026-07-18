"""t900cc_regclass — Register class system + HW constraints for the allocator.

Chantier 5 Phase P-5.3 (2026-05-20). See CHANTIER_5_PLAN.md §P-5.3.

Codifies the HW + ABI constraints that the linear-scan register allocator
must respect when mapping virtual registers to physical registers:

  1. **Pool definition** — which physical regs are allocatable, which
     are reserved (XIY=frame, XSP=stack).
  2. **Caller-saved set** — clobbered across function calls.
  3. **Per-operation constraints** — instructions that require specific
     physical registers (e.g. `_emit_alu16` requires WA + HL,
     mul/div mem-form forces XHL, link/unlk forces XIY, etc.).
  4. **Quirks validation** — every allocation choice is checked against
     `quirks_db` BEFORE emission to prevent silicon-broken encodings.

This module is INFRASTRUCTURE only — it doesn't emit any asm. P-5.4
(allocator) will consume these constraints. P-5.6 (wire to emit_*)
will use them to filter allocator candidates per call site.

References:
  - BACKEND_DESIGN.md §2.5 (physical register file)
  - BACKEND_DESIGN.md §2.6 (register class constraints)
  - DECISIONS.md (ABI v1 / ABI v2 calling conventions)
  - NgpCraft_emulator/core/quirks_db.json (silicon-broken matchers)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Iterable, Optional, Tuple


# ---------------------------------------------------------------------
# Physical register file
# ---------------------------------------------------------------------

# All 32-bit physical registers on TLCS-900H. The 8/16-bit aliases
# (WA / A / W for XWA, BC / B / C for XBC, …) are tracked at use-def
# time in `t900cc_liveness.SUB_TO_PARENT` — here we only care about the
# 32-bit identity.
ALL_PHYS_REGS_32: FrozenSet[str] = frozenset({
    'XWA', 'XBC', 'XDE', 'XHL', 'XIX', 'XIY', 'XIZ', 'XSP',
})

# Allocator pool = all 32-bit regs except those with fixed purposes:
#   - XIY: frame pointer (link XIY,N / unlk XIY)
#   - XSP: stack pointer
# Note: XHL is in the pool but the allocator must reserve it whenever
# the function emits MUL/DIV mem-form (which forces XHL=destination).
ALLOCATOR_POOL: FrozenSet[str] = frozenset({
    'XWA', 'XBC', 'XDE', 'XHL', 'XIX', 'XIZ',
})

# Reserved registers (must NEVER be allocated as scratch):
RESERVED_REGS: FrozenSet[str] = frozenset({
    'XIY',  # frame pointer
    'XSP',  # stack pointer
})

# Caller-saved registers (clobbered across a function call). Anything
# live across a call site must be spilled or moved to a callee-saved
# register. In the current cdecl ABI ALL allocator-pool regs are
# caller-saved; the callee preserves XIY/XSP only.
CALLER_SAVED: FrozenSet[str] = frozenset(ALLOCATOR_POOL)

# Callee-saved registers preserved across calls. Currently none in
# our ABI v1 cdecl — the linker map / convention requires the caller
# to spill anything live across a call.
CALLEE_SAVED: FrozenSet[str] = frozenset()


# ---------------------------------------------------------------------
# Register classes (= the set of physical regs an operand can use)
# ---------------------------------------------------------------------


class RegClass(Enum):
    """Categories of operand position with respect to which physical regs
    are acceptable.

    Each value is a set of physical register names. The allocator
    intersects a virtual register's class with the operand's class
    at each use site to find candidates.

    Note: classes overlap freely (e.g. WORD_GENERAL ⊂ LONG_GENERAL).
    The intent is *additional* constraint, not strict partitioning.
    """

    # Any allocatable physical reg (no specific class constraint).
    ANY = frozenset(ALLOCATOR_POOL)

    # Specific physical regs required by particular emit primitives:
    WA_ONLY = frozenset({'XWA'})       # left side of _emit_alu16
    HL_ONLY = frozenset({'XHL'})       # right side of _emit_alu16, mul/div dest
    XIY_ONLY = frozenset({'XIY'})      # link / unlk
    XSP_ONLY = frozenset({'XSP'})      # lda XSP,…

    # General-purpose 32-bit pointer / address holders (excludes WA
    # which is the accumulator and HL which is ALU scratch). Used for
    # caching far-pointer addresses across statements.
    LONG_PTR = frozenset({'XBC', 'XDE', 'XIX', 'XIZ'})

    # General-purpose 16-bit scratch (excludes XIY/XSP):
    WORD_GENERAL = frozenset({'XWA', 'XBC', 'XDE', 'XHL', 'XIX', 'XIZ'})

    # 16-bit DATA registers only — those that admit the
    # `LDW (mem+disp), R16` 3-byte encoding via sub-op 0x50..0x53
    # (XWA=0x50, XBC=0x51, XDE=0x52, XHL=0x53). Excludes XIX/XIZ
    # which are address-mode regs without the same store form.
    # Used by P-5.6.1 migrated codegen patterns that produce u16
    # values needing a frame-relative store. Distinct frozenset from
    # WORD_GENERAL → Python enum keeps them as separate members.
    WORD_DATA = frozenset({'XWA', 'XBC', 'XDE', 'XHL'})

    # ABI v1 return value: scalars ≤ 4 bytes returned in XHL.
    ABI_V1_RETURN = frozenset({'XHL'})

    # ABI v2 __adecl param positions:
    ABI_V2_ARG0 = frozenset({'XWA'})
    ABI_V2_ARG1 = frozenset({'XBC'})
    ABI_V2_ARG2 = frozenset({'XDE'})


# ---------------------------------------------------------------------
# Operand constraint dataclass
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RegConstraint:
    """A constraint on a single operand position.

    Either a class (`RegClass`) — meaning "any reg in this set",
    or a forced reg (`forced`) — meaning "must be EXACTLY this reg".

    For a forced constraint the allocator must move the value to that
    reg (potentially via push/pop transit). For a class constraint, the
    allocator picks any free reg in the class.
    """
    cls: Optional[RegClass] = None
    forced: Optional[str] = None
    label: str = ''  # human-readable description for error messages

    def __post_init__(self):
        if (self.cls is None) == (self.forced is None):
            raise ValueError(
                f'RegConstraint: must specify exactly one of cls/forced. '
                f'Got cls={self.cls!r} forced={self.forced!r}'
            )

    def allows(self, phys_reg: str) -> bool:
        """Return True iff `phys_reg` satisfies this constraint."""
        if self.forced is not None:
            return phys_reg == self.forced
        return phys_reg in self.cls.value

    def candidates(self) -> FrozenSet[str]:
        """Return the set of physical regs that satisfy this constraint."""
        if self.forced is not None:
            return frozenset({self.forced})
        return self.cls.value


# ---------------------------------------------------------------------
# Known instruction constraint catalog
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class InstructionConstraints:
    """The set of constraints for one IR instruction kind.

    `reads` and `writes` are tuples of `RegConstraint` describing the
    operands. Position 0 = first operand, etc. For ops with variable
    operands (e.g. Call with N args), the constraints list extends.

    `forces_def_reg` / `forces_use_reg` (optional) document a HARD pin:
    the instruction MUST emit with this physical reg regardless of
    the operand value. E.g. `link XIY, N` always uses XIY.

    `clobbers` lists physical regs whose value is destroyed by the
    instruction even when not in the read/write set (e.g. div clobbers
    XHL because the divisor goes there via mem-form).
    """
    name: str
    reads: Tuple[RegConstraint, ...] = ()
    writes: Tuple[RegConstraint, ...] = ()
    clobbers: FrozenSet[str] = frozenset()
    note: str = ''


# Catalog of known constraints. Keys are stylized op names referenced
# by the codegen (typically the helper function name like
# `_emit_alu16`, or the mnemonic like `mul_mem`).

# `_emit_alu16(op)` — 16-bit ALU on WA / HL.
# Pre-condition: WA = left, HL = right. Result in WA for arithmetic
# / bitwise; flags only for comparison.
EMIT_ALU16_CONSTRAINTS = InstructionConstraints(
    name='_emit_alu16',
    reads=(
        RegConstraint(cls=RegClass.WA_ONLY, label='left'),
        RegConstraint(cls=RegClass.HL_ONLY, label='right'),
    ),
    writes=(RegConstraint(cls=RegClass.WA_ONLY, label='dest'),),
    note='LHS=WA, RHS=HL hard pin. Byte-split arithmetic via `add A,L;'
         ' adc W,H` family (CF/CE prefix, safe).',
)

# `mul XHL, (XSP+0)` mem-form — multiplies XHL low half by mem value.
# Result destination: XHL (low = HL = quotient, full XHL = 32-bit result).
MUL_MEM_CONSTRAINTS = InstructionConstraints(
    name='mul_mem',
    reads=(RegConstraint(cls=RegClass.HL_ONLY, label='multiplicand'),),
    writes=(RegConstraint(cls=RegClass.HL_ONLY, label='product_lo'),),
    clobbers=frozenset({'XHL'}),
    note='mul mem-form (db 0x9F 0x00 0x43): XHL = HL * (XSP+0). XHL '
         'forced as destination — allocator must reserve XHL.',
)

# `div XHL, (XSP+0)` mem-form — XHL / mem_word.
# Result: quotient in HL, remainder in upper 16 of XHL.
DIV_MEM_CONSTRAINTS = InstructionConstraints(
    name='div_mem',
    reads=(RegConstraint(cls=RegClass.HL_ONLY, label='dividend_xhl'),),
    writes=(RegConstraint(cls=RegClass.HL_ONLY, label='quotient_lo'),),
    clobbers=frozenset({'XHL'}),
    note='div mem-form (db 0x9F 0x00 0x53): XHL = XHL / (XSP+0). '
         'Both dividend AND quotient in XHL.',
)

# `link XIY, N` — frame setup. XIY forced.
LINK_XIY_CONSTRAINTS = InstructionConstraints(
    name='link_xiy',
    reads=(),
    writes=(RegConstraint(forced='XIY', label='frame_pointer'),),
    clobbers=frozenset({'XSP'}),  # stack pushed/popped
    note='link XIY, N. N must be <= 4 (silicon quirk N>=5 broken). '
         'XIY = XSP after push; XSP -= N.',
)

# `unlk XIY` — frame teardown.
UNLK_XIY_CONSTRAINTS = InstructionConstraints(
    name='unlk_xiy',
    reads=(RegConstraint(forced='XIY', label='frame_pointer'),),
    writes=(RegConstraint(forced='XIY', label='frame_pointer_restore'),),
    clobbers=frozenset({'XSP'}),
    note='unlk XIY. Restores XSP = XIY, then pops XIY from stack.',
)

# `call <sym>` — function call. Clobbers all caller-saved regs.
CALL_CONSTRAINTS = InstructionConstraints(
    name='call',
    reads=(),    # args are on stack via cdecl, return reg is XHL.
    writes=(),   # caller-saved are clobbered (see `clobbers`).
    clobbers=CALLER_SAVED,
    note='cdecl: args pushed on stack; XHL holds return value (sz<=4); '
         'all caller-saved regs clobbered (XWA/XBC/XDE/XHL/XIX/XIZ).',
)

# Return — ABI v1 places return value in XHL. (For void no constraint.)
RETURN_V1_SCALAR_CONSTRAINTS = InstructionConstraints(
    name='return_v1_scalar',
    reads=(RegConstraint(cls=RegClass.ABI_V1_RETURN, label='return_value'),),
    writes=(),
    note='ABI v1 cdecl: scalar <=4 bytes returned in XHL.',
)

# Push X → Pop Y transit (silicon workaround for the broken
# `ld <reg>, <reg>` r+r D0..DF family).
TRANSIT_CONSTRAINTS = InstructionConstraints(
    name='transit_push_pop',
    reads=(RegConstraint(cls=RegClass.WORD_GENERAL, label='src'),),
    writes=(RegConstraint(cls=RegClass.WORD_GENERAL, label='dst'),),
    note='Safe HW substitution for `ld dst, src`. Uses push/pop, no '
         'D0..D7 r+r encoding (which is silicon-broken).',
)


# Master catalog
INSTRUCTION_CATALOG: dict[str, InstructionConstraints] = {
    '_emit_alu16': EMIT_ALU16_CONSTRAINTS,
    'mul_mem': MUL_MEM_CONSTRAINTS,
    'div_mem': DIV_MEM_CONSTRAINTS,
    'link_xiy': LINK_XIY_CONSTRAINTS,
    'unlk_xiy': UNLK_XIY_CONSTRAINTS,
    'call': CALL_CONSTRAINTS,
    'return_v1_scalar': RETURN_V1_SCALAR_CONSTRAINTS,
    'transit_push_pop': TRANSIT_CONSTRAINTS,
}


# ---------------------------------------------------------------------
# Helper functions for the allocator
# ---------------------------------------------------------------------

def is_allocatable(phys_reg: str) -> bool:
    """Return True iff `phys_reg` is allocatable as scratch."""
    return phys_reg in ALLOCATOR_POOL


def is_caller_saved(phys_reg: str) -> bool:
    """Return True iff `phys_reg` is clobbered across function calls."""
    return phys_reg in CALLER_SAVED


def is_reserved(phys_reg: str) -> bool:
    """Return True iff `phys_reg` has a fixed purpose (frame / stack)."""
    return phys_reg in RESERVED_REGS


def get_constraints(op_name: str) -> Optional[InstructionConstraints]:
    """Look up the constraint set for a known instruction kind. Returns
    None if the op isn't in the catalog (allocator should treat as
    unconstrained = RegClass.ANY)."""
    return INSTRUCTION_CATALOG.get(op_name)


def candidates_for_class(cls: RegClass) -> FrozenSet[str]:
    """Return the set of physical regs that satisfy a class."""
    return cls.value


def operand_candidates(constraints: Iterable[RegConstraint]) -> Tuple[FrozenSet[str], ...]:
    """Map each operand constraint to its set of allowed physical regs.

    Returns a tuple parallel to `constraints` for convenient indexing
    by operand position."""
    return tuple(c.candidates() for c in constraints)


# ---------------------------------------------------------------------
# Quirks integration (HARD CONSTRAINT)
# ---------------------------------------------------------------------

# Forbidden register-transfer encodings: `ld <dst>, <src>` directly
# in D0..DF r+r family is silicon-broken. The allocator must NEVER
# emit these; transit must use push <src>; pop <dst> instead.
# Mapping: (dst, src) tuples that decode to broken D0..D7 / D8..DF
# r+r forms.
KNOWN_BROKEN_TRANSFERS: FrozenSet[Tuple[str, str]] = frozenset({
    # 16-bit r+r LD (D0..D7 prefix, sub-op 8B):
    ('HL', 'WA'),   # D0 8B = LD HL, WA (broken)
    ('WA', 'HL'),   # D3 88 family (broken)
    ('BC', 'WA'), ('WA', 'BC'),
    ('DE', 'WA'), ('WA', 'DE'),
    ('HL', 'BC'), ('BC', 'HL'),
    ('HL', 'DE'), ('DE', 'HL'),
    ('BC', 'DE'), ('DE', 'BC'),
    # 32-bit r+r LD (D8..DF prefix, sub-op 8B):
    ('XHL', 'XWA'), ('XWA', 'XHL'),
    ('XBC', 'XWA'), ('XWA', 'XBC'),
    ('XDE', 'XWA'), ('XWA', 'XDE'),
    ('XHL', 'XBC'), ('XBC', 'XHL'),
    ('XHL', 'XDE'), ('XDE', 'XHL'),
    ('XBC', 'XDE'), ('XDE', 'XBC'),
    ('XIX', 'XWA'), ('XWA', 'XIX'),
    ('XIZ', 'XWA'), ('XWA', 'XIZ'),
    # … and many more pairs in the D8..DF family. The complete set
    # is computed via the quirks_db matcher; this hand-list is a
    # readability aid for the common cases.
})

# Forbidden ALU-imm forms: `<op> <r16>, <imm>` (encoding D0..D7 +
# 0xC8..0xCF + lo hi) is silicon-broken per the 2026-05-20 P-4 HW
# crash. The allocator must NEVER emit these; byte-split must be
# used instead (ld HL, imm; add A, L; adc W, H).
FORBIDDEN_ALU_IMM_16BIT = frozenset({
    ('add', 'WA'), ('add', 'BC'), ('add', 'DE'), ('add', 'HL'),
    ('add', 'IX'), ('add', 'IY'), ('add', 'IZ'), ('add', 'SP'),
    ('adc', 'WA'), ('adc', 'BC'), ('adc', 'DE'), ('adc', 'HL'),
    ('adc', 'IX'), ('adc', 'IY'), ('adc', 'IZ'), ('adc', 'SP'),
    ('sub', 'WA'), ('sub', 'BC'), ('sub', 'DE'), ('sub', 'HL'),
    ('sub', 'IX'), ('sub', 'IY'), ('sub', 'IZ'), ('sub', 'SP'),
    ('sbc', 'WA'), ('sbc', 'BC'), ('sbc', 'DE'), ('sbc', 'HL'),
    ('sbc', 'IX'), ('sbc', 'IY'), ('sbc', 'IZ'), ('sbc', 'SP'),
    ('and', 'WA'), ('and', 'BC'), ('and', 'DE'), ('and', 'HL'),
    ('and', 'IX'), ('and', 'IY'), ('and', 'IZ'), ('and', 'SP'),
    ('or', 'WA'), ('or', 'BC'), ('or', 'DE'), ('or', 'HL'),
    ('or', 'IX'), ('or', 'IY'), ('or', 'IZ'), ('or', 'SP'),
    ('xor', 'WA'), ('xor', 'BC'), ('xor', 'DE'), ('xor', 'HL'),
    ('xor', 'IX'), ('xor', 'IY'), ('xor', 'IZ'), ('xor', 'SP'),
    ('cp', 'WA'), ('cp', 'BC'), ('cp', 'DE'), ('cp', 'HL'),
    ('cp', 'IX'), ('cp', 'IY'), ('cp', 'IZ'), ('cp', 'SP'),
})


def validate_transfer(dst: str, src: str) -> bool:
    """Return True iff `ld dst, src` is HW-safe. The allocator should
    call this BEFORE emitting any direct register-to-register move
    so silicon-broken forms (D0/D8 r+r) are caught early."""
    return (dst, src) not in KNOWN_BROKEN_TRANSFERS


def validate_alu_imm(mnemonic: str, reg: str) -> bool:
    """Return True iff `<mnemonic> <reg>, <imm>` is HW-safe.

    The 16-bit ALU-imm family (`add WA, imm16` etc., encoding
    D0..D7 + 0xC8..0xCF + lo hi) is silicon-broken per the
    2026-05-20 P-4 HW crash. Allocator MUST emit byte-split
    (`ld HL, imm; add A, L; adc W, H`) for these.

    8-bit ALU-imm (`add A, imm8` = `C8 C8 imm`) is HW-safe and
    not blocked by this function."""
    return (mnemonic.lower(), reg.upper()) not in FORBIDDEN_ALU_IMM_16BIT


def validate_link_xiy_frame(n: int) -> bool:
    """Return True iff `link XIY, N` is HW-safe. Per the Jalon 8 bisect,
    N >= 5 is silicon-broken. The allocator-driven prologue must cap
    `link XIY, N` to N <= 4 (use multiple link instances or stack-
    pointer arithmetic for larger frames)."""
    return 0 <= n <= 4


# ---------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------

def _self_test() -> None:
    """Internal sanity check. Run with `python tools/t900cc_regclass.py`."""
    # Pool / reserved
    assert is_allocatable('XWA')
    assert is_allocatable('XBC')
    assert not is_allocatable('XIY')
    assert not is_allocatable('XSP')
    assert is_reserved('XIY')
    assert is_reserved('XSP')

    # Caller-saved
    assert is_caller_saved('XWA')
    assert is_caller_saved('XHL')
    assert not is_caller_saved('XIY')   # callee-preserved (frame)

    # Class membership
    assert 'XWA' in RegClass.WA_ONLY.value
    assert 'XHL' in RegClass.HL_ONLY.value
    assert 'XWA' not in RegClass.HL_ONLY.value
    assert 'XIY' in RegClass.XIY_ONLY.value
    assert RegClass.LONG_PTR.value == frozenset({'XBC', 'XDE', 'XIX', 'XIZ'})
    assert 'XWA' not in RegClass.LONG_PTR.value
    assert 'XHL' not in RegClass.LONG_PTR.value

    # RegConstraint
    c = RegConstraint(cls=RegClass.WA_ONLY)
    assert c.allows('XWA')
    assert not c.allows('XBC')
    assert c.candidates() == frozenset({'XWA'})

    c2 = RegConstraint(forced='XIY')
    assert c2.allows('XIY')
    assert not c2.allows('XWA')

    # Instruction catalog
    alu = get_constraints('_emit_alu16')
    assert alu is not None
    assert len(alu.reads) == 2
    assert alu.reads[0].allows('XWA')
    assert alu.reads[1].allows('XHL')
    assert not alu.reads[0].allows('XHL')
    assert not alu.reads[1].allows('XWA')

    link = get_constraints('link_xiy')
    assert link.writes[0].forced == 'XIY'

    call = get_constraints('call')
    assert 'XWA' in call.clobbers
    assert 'XIY' not in call.clobbers

    # Quirks validation
    assert not validate_transfer('HL', 'WA')   # D0 8B broken
    assert not validate_transfer('XHL', 'XWA') # D8 8B broken
    assert validate_transfer('A', 'L')         # byte r+r CF 89 safe
    assert validate_alu_imm('add', 'A')        # byte-form C8 C8 imm safe
    assert not validate_alu_imm('add', 'WA')   # D0 C8 imm broken
    assert not validate_alu_imm('xor', 'BC')   # D1 CD imm broken
    assert validate_link_xiy_frame(0)
    assert validate_link_xiy_frame(4)
    assert not validate_link_xiy_frame(5)
    assert not validate_link_xiy_frame(8)

    print('[t900cc_regclass] All self-tests pass.')


if __name__ == '__main__':
    _self_test()
