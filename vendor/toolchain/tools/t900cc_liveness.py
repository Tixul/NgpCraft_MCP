"""t900cc_liveness — CFG construction + liveness analysis for IRFunction.

Chantier 5 Phase P-5.2 (2026-05-20). See CHANTIER_5_PLAN.md §P-5.2.

This module operates on an `IRFunction` produced by P-5.1 (block-level
IR). It computes:

  1. **CFG edges** — successor/predecessor sets per block by parsing
     terminator ops (jp / jrl / jr / ret / reti) in each block's
     trailing EmitRaw.
  2. **Use-def sets** per block — which physical registers are read /
     written by the block's ops.
  3. **Liveness** via standard backward dataflow:
       live_out[B] = ∪ live_in[S] for S in succ(B)
       live_in[B]  = uses[B] ∪ (live_out[B] − defs[B])
     iterated until fixed point.
  4. **Live ranges** per physical register: the (first_def_block_idx,
     last_use_block_idx) interval. Used by later phases as a stepping
     stone toward proper virtual-register liveness once structured ops
     are wired in.

The current implementation works on TEXT-LEVEL EmitRaw ops — it parses
the asm mnemonic to identify reads/writes. This is brittle but lets us
do liveness BEFORE the codegen migrates to structured ops. The same
analysis algorithm will adapt to virtual-register liveness in P-5.4+
with minimal changes (just swap the use-def collector).

This module is INFORMATIONAL only — it does not modify the IR or emit
any asm. Phase P-5.2 acceptance: binary-identical build, with the
liveness info available for inspection via `liveness_dump.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from t900cc_ir import BasicBlock, IRFunction


# ---------------------------------------------------------------------
# Physical register set
# ---------------------------------------------------------------------

# All TLCS-900H physical registers we track. The allocator pool is a
# subset (XIY frame + XSP excluded), but liveness tracks them all so
# we can validate constraints like "XIY must remain stable across calls".
PHYS_REGS_32 = ('XWA', 'XBC', 'XDE', 'XHL', 'XIX', 'XIY', 'XIZ', 'XSP')

# Mapping from any sub-register name (8 / 16 / 32 bit alias) to the
# parent 32-bit physical register. Writing to a 16-bit half (e.g. WA)
# clobbers the parent 32-bit value entirely from an allocator-tracking
# point of view, so we coalesce all aliases into the same identity.
SUB_TO_PARENT = {
    # 32-bit (identity)
    'XWA': 'XWA', 'XBC': 'XBC', 'XDE': 'XDE', 'XHL': 'XHL',
    'XIX': 'XIX', 'XIY': 'XIY', 'XIZ': 'XIZ', 'XSP': 'XSP',
    # 16-bit
    'WA': 'XWA', 'BC': 'XBC', 'DE': 'XDE', 'HL': 'XHL',
    'IX': 'XIX', 'IY': 'XIY', 'IZ': 'XIZ', 'SP': 'XSP',
    # 8-bit
    'A': 'XWA', 'W': 'XWA',
    'B': 'XBC', 'C': 'XBC',
    'D': 'XDE', 'E': 'XDE',
    'H': 'XHL', 'L': 'XHL',
    # Banked sub-registers
    'QIZH': 'XIZ', 'QIZL': 'XIZ', 'IZH': 'XIZ', 'IZL': 'XIZ',
    'QIXH': 'XIX', 'QIXL': 'XIX', 'IXH': 'XIX', 'IXL': 'XIX',
    'QIYH': 'XIY', 'QIYL': 'XIY', 'IYH': 'XIY', 'IYL': 'XIY',
}


# ---------------------------------------------------------------------
# CFG terminators: detect end-of-block instructions in EmitRaw text
# ---------------------------------------------------------------------

# Conditional branches: `jr Z, target`, `jrl NC, target`, etc.
COND_BRANCH_RE = re.compile(
    r'^\s+(jr|jrl|jp)\s+'
    r'(Z|NZ|C|NC|PL|MI|OV|NOV|LT|LE|GT|GE|ULE|UGT|EQ|NE|T|F)\s*,\s*(\S+)',
    re.IGNORECASE,
)
# Unconditional branches: `jp target`, `jrl target`, `jr target`
UNCOND_BRANCH_RE = re.compile(
    r'^\s+(jp|jrl|jr)\s+([^,\s][^,;]*?)(?:\s*;.*)?$'
)
# Function returns: ret / reti / retd
RETURN_RE = re.compile(r'^\s+(ret|reti|retd)\b')
# Function calls: caller-saved regs are clobbered across these.
CALL_RE = re.compile(r'^\s+(call|calr)\s+(\S+)')


# ---------------------------------------------------------------------
# Use-def extraction from EmitRaw text
# ---------------------------------------------------------------------

# Mnemonics whose FIRST operand is the destination (written). Most ALU
# ops are read-modify-write but we treat them as "uses src + def dest"
# which is liveness-correct (dest is written) — the use-of-dest before
# the write is captured via src.
WRITE_FIRST_MNEMONICS = {
    'ld', 'ldw', 'ldb', 'lda', 'pop',
    'inc', 'dec',
    'add', 'sub', 'adc', 'sbc',
    'and', 'or', 'xor',
    'sll', 'sra', 'srl', 'rlc', 'rrc', 'rl', 'rr',
    'exts', 'extz', 'mul', 'div', 'mulu', 'divu', 'mula',
    'lda', 'link',  # link writes the named reg + XSP
    # CC900 control-register reads (P-5.6.7 LVT safety) — `ldc R, CR`
    # writes R from control reg. Not used in j16 baseline but added
    # defensively so future migrations using __DMACn/etc. don't bypass
    # LVT cache invalidation.
    'ldc', 'ldcw', 'ldcb',
}

# Mnemonics that READ their first operand (no write to it):
READ_FIRST_MNEMONICS = {
    'push', 'cp', 'cpw', 'cpb', 'bit', 'tst', 'unlk',
}

# Mnemonics that affect ONLY flags / control flow (no reg read/write
# tracked here):
NO_REG_OP_MNEMONICS = {
    'nop', 'halt', 'ei', 'di', 'swi', 'rcf', 'scf', 'ccf', 'zcf',
    'jp', 'jrl', 'jr', 'ret', 'reti', 'retd', 'call', 'calr',
    'db', 'dw',
}


def _normalize_reg(token: str) -> Optional[str]:
    """Map a register token (any case, any alias) to its parent 32-bit
    physical register name. Returns None if the token isn't a recognized
    register."""
    up = token.strip().upper()
    return SUB_TO_PARENT.get(up)


def _extract_uses_defs_from_text(line: str) -> tuple[Set[str], Set[str]]:
    """Parse a single line of asm text and return (uses, defs) sets
    of parent-32-bit register names.

    Conservative: when an op is unrecognized, returns empty sets (no
    use, no def). This errs on the side of MISSING uses/defs which
    could yield incorrect liveness — but a follow-up pass can refine.
    For P-5.2 the precision is good enough on the common patterns t900cc
    emits.

    For `db 0xNN, ...; <mnem hint>`, parse the hint comment to extract
    pseudo-mnemonic info."""
    stripped = line.strip()
    if not stripped or stripped.startswith(';'):
        return set(), set()
    # Strip inline comment.
    instr_part = stripped.split(';', 1)[0].strip()
    if not instr_part:
        return set(), set()

    parts = instr_part.split(None, 1)
    mnem = parts[0].lower()
    operand_part = parts[1] if len(parts) > 1 else ''

    # `db 0xXX, …; <hint>`: parse the hint to know what reg it touches.
    if mnem == 'db':
        # Look at the comment for an actual mnemonic hint.
        cmt = stripped.split(';', 1)
        if len(cmt) < 2:
            return set(), set()
        hint = cmt[1].strip()
        hint_parts = hint.split(None, 1)
        if not hint_parts:
            return set(), set()
        hint_mnem = hint_parts[0].lower()
        hint_operands = hint_parts[1] if len(hint_parts) > 1 else ''
        return _extract_uses_defs_from_mnem(hint_mnem, hint_operands)

    if mnem in NO_REG_OP_MNEMONICS:
        # call/calr: clobber all caller-saved regs.
        if mnem in ('call', 'calr'):
            defs = set()
            for r in ('XWA', 'XBC', 'XDE', 'XHL', 'XIX', 'XIZ'):
                defs.add(r)
            return set(), defs
        return set(), set()

    return _extract_uses_defs_from_mnem(mnem, operand_part)


def _extract_uses_defs_from_mnem(mnem: str, operand_part: str) -> tuple[Set[str], Set[str]]:
    """Inner helper: given mnemonic + operands (already split from the
    line), compute uses/defs."""
    uses: Set[str] = set()
    defs: Set[str] = set()

    operands = [o.strip() for o in operand_part.split(',')]
    if not operands:
        return uses, defs

    # Categorize each operand: reg / memory-via-reg / label / immediate.
    # For memory-via-reg `(XDE+d)` the base reg is USED (read), the
    # memory is the write target if it's the first operand of a store.

    def analyze_operand(op_text: str) -> tuple[Optional[str], list[str]]:
        """Returns (reg_if_direct, list_of_regs_used_as_index_base).

        For `XDE`: returns ('XDE', []).
        For `(XDE+5)`: returns (None, ['XDE']).
        For `(0x4100)`: returns (None, []).
        For `_label`: returns (None, []).
        For `5`: returns (None, []).
        """
        s = op_text.strip()
        if not s:
            return None, []
        if s.startswith('('):
            # Memory operand. Extract reg base from inside the parens.
            inner = s.strip('()')
            # Strip displacement after +/-: `XDE+5` → `XDE`
            base_token = re.split(r'[+\-\s]', inner, maxsplit=1)[0]
            base = _normalize_reg(base_token)
            return None, ([base] if base else [])
        # Possibly a direct register.
        parent = _normalize_reg(s)
        if parent is not None:
            return parent, []
        return None, []

    if mnem in WRITE_FIRST_MNEMONICS:
        first = operands[0] if operands else ''
        dest_reg, dest_base = analyze_operand(first)
        if dest_reg is not None:
            defs.add(dest_reg)
        for r in dest_base:
            uses.add(r)
        # Subsequent operands are sources (uses)
        for src_op in operands[1:]:
            src_reg, src_base = analyze_operand(src_op)
            if src_reg is not None:
                uses.add(src_reg)
            for r in src_base:
                uses.add(r)
        # Read-modify-write mnemonics also USE the dest before writing it
        # (e.g. `add A, L` reads A AND L, then writes A).
        if mnem in ('add', 'sub', 'adc', 'sbc', 'and', 'or', 'xor',
                    'inc', 'dec', 'sll', 'sra', 'srl', 'rl', 'rr',
                    'rlc', 'rrc', 'mul', 'div', 'mulu', 'divu', 'mula'):
            if dest_reg is not None:
                uses.add(dest_reg)
        return uses, defs

    if mnem in READ_FIRST_MNEMONICS:
        for op in operands:
            reg, base = analyze_operand(op)
            if reg is not None:
                uses.add(reg)
            for r in base:
                uses.add(r)
        return uses, defs

    # Unknown mnemonic — return empty (conservative).
    return uses, defs


# ---------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------

@dataclass
class BlockLiveness:
    """Liveness summary for a single basic block."""
    uses: Set[str] = field(default_factory=set)
    defs: Set[str] = field(default_factory=set)
    live_in: Set[str] = field(default_factory=set)
    live_out: Set[str] = field(default_factory=set)


@dataclass
class FunctionLiveness:
    """Complete liveness info for an IRFunction.

    Two views of liveness:

    - `live_ranges` (block-level, convex hull): for each reg, the single
      (lo, hi) block-index range covering every block where the reg is
      either used, defined, live-in, or live-out. This is the historical
      P-5.2 view, kept for back-compat with allocator_trace.py and the
      liveness_dump.py pretty-printer.

    - `live_intervals` (per-op, disjoint ranges): for each reg, the list
      of (start_pos, end_pos) op-position intervals where the reg is
      ACTUALLY OCCUPIED. Added in P-5.6.1 per-op refinement
      (2026-05-20). A phys reg that is written and then dies before
      being read again produces SEVERAL disjoint intervals — the gaps
      between intervals are positions where the reg is dead and free
      for the allocator to grant to a `pref`-hinted vreg.

      Positions are indexes into the linearized op sequence (= the
      flat concatenation of all blocks' op lists, in order). Labels
      are block metadata, not ops, so they don't count.

      With this finer-grained view, `pref='XWA'` actually wins for
      isolated migrations because a structured vreg's tight range
      [pos_of_LoadImm, pos_of_StoreLocal] fits in a gap where XWA is
      dead — even though the convex-hull `live_ranges['XWA']` would
      still show XWA as live across the whole function.
    """
    name: str
    # Per-block analysis, indexed by block position in ir_function.blocks
    blocks: List[BlockLiveness] = field(default_factory=list)
    # Reverse-CFG: block index → set of successor block indices
    successors: Dict[int, List[int]] = field(default_factory=dict)
    predecessors: Dict[int, List[int]] = field(default_factory=dict)
    # Per-register convex-hull range: reg → (first_block_idx, last_block_idx).
    # Kept for back-compat (block-level). Use `live_intervals` for the
    # allocator-grade per-op disjoint view.
    live_ranges: Dict[str, tuple] = field(default_factory=dict)
    # Per-register disjoint per-op intervals: reg → list of (start_pos, end_pos).
    # `start_pos` / `end_pos` are positions in the linearized op sequence
    # (op_positions below maps (block_idx, op_idx_in_block) → global pos).
    # Both endpoints inclusive. Empty list means the reg never occupied.
    live_intervals: Dict[str, List[tuple]] = field(default_factory=dict)
    # Total number of ops across all blocks (= max global pos + 1).
    # Useful for tools and tests that need to walk the position space.
    op_count: int = 0


# ---------------------------------------------------------------------
# CFG construction
# ---------------------------------------------------------------------

def build_cfg(ir_function: 'IRFunction') -> tuple[Dict[int, List[int]], Dict[int, List[int]]]:
    """Compute successor and predecessor sets per block.

    Strategy (refined 2026-05-20): scan ALL ops in each block (not just
    the last) to detect every branch/jump/ret. A single "block" in our
    IR may contain multiple mid-block branches (because `start_block`
    is only called at `emit_label`, not at every branch instruction).
    For each block we find:
      - Every conditional branch → adds the target block as a successor.
        The block still falls through past the branch to subsequent ops.
      - The first unconditional terminator (jp/jrl/jr/ret/reti) →
        stops scanning. Ops after it are unreachable from block entry.
        If terminator is jump: target block is added as successor.
        If terminator is ret/reti: no successor added.
      - If no unconditional terminator found → fall-through to next
        block in linear order.

    Returns (succ_map, pred_map) where each maps block_idx → list of block_idx.
    """
    # First: build a label → block_idx index for jump resolution.
    label_to_idx: Dict[str, int] = {}
    for idx, blk in enumerate(ir_function.blocks):
        if blk.label is not None:
            label_to_idx[blk.label] = idx

    succ: Dict[int, List[int]] = {}
    n_blocks = len(ir_function.blocks)

    from t900cc_ir import EmitRaw

    for idx, blk in enumerate(ir_function.blocks):
        successors: List[int] = []
        has_unconditional_exit = False

        # Walk ops in order, collecting branch targets until an
        # unconditional terminator stops the flow.
        for op in blk.ops:
            if not isinstance(op, EmitRaw):
                continue
            text = op.text
            stripped = text.strip()
            if not stripped or stripped.startswith(';'):
                continue
            # Return / reti / retd: end of block, no successors.
            if RETURN_RE.match(text):
                has_unconditional_exit = True
                break
            # Conditional branch: add successor, continue scanning
            m_cond = COND_BRANCH_RE.match(text)
            if m_cond:
                target_label = m_cond.group(3).strip()
                target_idx = label_to_idx.get(target_label)
                if target_idx is not None and target_idx not in successors:
                    successors.append(target_idx)
                continue
            # Unconditional branch: add successor, stop.
            m_uncond = UNCOND_BRANCH_RE.match(text)
            if m_uncond:
                target_label = m_uncond.group(2).strip()
                target_idx = label_to_idx.get(target_label)
                if target_idx is not None and target_idx not in successors:
                    successors.append(target_idx)
                has_unconditional_exit = True
                break

        if not has_unconditional_exit and idx + 1 < n_blocks:
            # Fall through to next block in linear order.
            if (idx + 1) not in successors:
                successors.append(idx + 1)

        succ[idx] = successors

    # Compute predecessors (reverse of succ)
    pred: Dict[int, List[int]] = {i: [] for i in range(n_blocks)}
    for src, dests in succ.items():
        for dst in dests:
            pred[dst].append(src)

    return succ, pred


# ---------------------------------------------------------------------
# Use-def per block
# ---------------------------------------------------------------------

def _single_op_uses_defs(op) -> tuple[Set[str], Set[str]]:
    """Return (uses, defs) for a single IR op.

    Centralized helper used by both `compute_block_uses_defs` (block
    aggregation) and the per-op liveness pass in `compute_liveness`.

    - `EmitRaw`: parse the asm text for reg refs (phys-reg only).
    - `LoadImm`: defs the destination vreg, no reads.
    - `LoadLocal`: defs the destination vreg, no reg reads (the source
       is a frame-relative memory slot, not a register).
    - `StoreLocal`: reads the source vreg (the value being stored),
       no reg-side defs (the write target is memory).
    - Other structured ops: return empty sets for now; future P-5.6.x
       phases will wire them in.
    """
    from t900cc_ir import EmitRaw, LoadImm, LoadLocal, LoadGlobal, StoreLocal, BinOp
    if isinstance(op, EmitRaw):
        return _extract_uses_defs_from_text(op.text)
    if isinstance(op, LoadImm):
        return set(), {op.dest}
    if isinstance(op, LoadLocal):
        return set(), {op.dest}
    if isinstance(op, LoadGlobal):
        # LoadGlobal defs its dest vreg (loaded from abs16 mem). No reg
        # uses — the source is a global symbol (memory address), not a
        # register.
        return set(), {op.dest}
    if isinstance(op, StoreLocal):
        return {op.src}, set()
    if isinstance(op, BinOp):
        # BinOp reads both source vregs, defs the destination vreg.
        # The HW byte-split ALU also clobbers flags; the IR doesn't model
        # flags as a tracked resource (a future Compare/Branch IR-aware
        # phase will), so no defs are added beyond `op.dest`.
        return {op.src_a, op.src_b}, {op.dest}
    return set(), set()


def compute_block_uses_defs(block: 'BasicBlock') -> tuple[Set[str], Set[str]]:
    """Compute the use/def sets for a single block.

    Liveness convention:
      - `uses` = set of regs (physical OR virtual) READ before being
        WRITTEN in the block (the regs whose value flows IN to be useful).
      - `defs` = set of regs WRITTEN somewhere in the block
        (regardless of whether also used).

    Algorithm: walk the block's ops in order. For each op:
      - For each reg in op.reads:
          if reg not in defs_so_far: add to uses
      - For each reg in op.writes:
          add to defs_so_far

    P-5.6.1 wiring: structured ops (`LoadImm`, `StoreLocal`) participate
    via their virtual-register operands (`%t0`, `%t1`, …). Vregs flow
    into the same liveness analysis as physical regs because the
    allocator treats them uniformly — both end up in `result.allocation`
    keyed by name.
    """
    uses: Set[str] = set()
    defs_so_far: Set[str] = set()
    for op in block.ops:
        op_uses, op_defs = _single_op_uses_defs(op)
        # Reads before any prior write in this block → contribute to uses
        for r in op_uses:
            if r not in defs_so_far:
                uses.add(r)
        # Writes → add to defs_so_far (and total defs)
        defs_so_far |= op_defs
    return uses, defs_so_far


# ---------------------------------------------------------------------
# Dataflow fixed-point liveness
# ---------------------------------------------------------------------

def compute_liveness(ir_function: 'IRFunction') -> FunctionLiveness:
    """Compute backward-dataflow liveness for the given IRFunction.

    Returns a FunctionLiveness with per-block analysis and per-reg
    live ranges.

    Algorithm (textbook backward dataflow):
      Initialize all live_in/live_out to empty.
      Repeat:
        For each block B (reverse post-order helps but iterating any
        order works; we just need fixed point):
          live_out[B] = union of live_in[S] for S in succ(B)
          new_live_in = uses[B] | (live_out[B] - defs[B])
          if new_live_in != live_in[B]:
            live_in[B] = new_live_in
            changed = True
      Until no change.
    """
    n_blocks = len(ir_function.blocks)
    # Build per-block uses/defs.
    blk_info: List[BlockLiveness] = []
    for blk in ir_function.blocks:
        u, d = compute_block_uses_defs(blk)
        blk_info.append(BlockLiveness(uses=u, defs=d))

    # Build CFG.
    succ, pred = build_cfg(ir_function)

    # Iterate to fixed point.
    changed = True
    max_iterations = 100  # safety bound; loops converge fast on small graphs
    iter_count = 0
    while changed and iter_count < max_iterations:
        changed = False
        iter_count += 1
        # Reverse post-order would be optimal; for simplicity iterate
        # in reverse linear order (still converges).
        for i in range(n_blocks - 1, -1, -1):
            new_live_out: Set[str] = set()
            for s in succ.get(i, []):
                new_live_out |= blk_info[s].live_in
            new_live_in = blk_info[i].uses | (new_live_out - blk_info[i].defs)
            if new_live_in != blk_info[i].live_in or new_live_out != blk_info[i].live_out:
                blk_info[i].live_in = new_live_in
                blk_info[i].live_out = new_live_out
                changed = True

    # Compute per-reg live ranges (first block with def OR live-in,
    # last block with use OR live-out). Convex hull view, kept for
    # back-compat with allocator_trace.py / liveness_dump.py.
    live_ranges: Dict[str, tuple] = {}
    for i, bli in enumerate(blk_info):
        all_regs = bli.uses | bli.defs | bli.live_in | bli.live_out
        for r in all_regs:
            if r not in live_ranges:
                live_ranges[r] = (i, i)
            else:
                lo, hi = live_ranges[r]
                live_ranges[r] = (min(lo, i), max(hi, i))

    # --- P-5.6.1 per-op refinement: compute disjoint live intervals ---
    #
    # The block-level convex hull above produces ONE range per reg even
    # when the reg has dead "holes" inside the function. This causes
    # the allocator to treat the reg as occupied across the whole span
    # and refuse `pref` hints from short-lived vregs that would happily
    # fit in a hole. To unblock that case we compute the FINER
    # per-op occupancy below.
    #
    # Algorithm (per block, then stitched globally):
    #   1. Assign each op a global position (`block_start_pos[i] + j`).
    #   2. For each block, walk ops in REVERSE order using the block's
    #      `live_out` as the seed live set. At each op we record
    #      `live_before_op` and `live_after_op`.
    #   3. Forward sweep: at each global position p, "occupied" =
    #      `live_before_op(p) ∪ defs(op_p)`. Dead defs count too — the
    #      reg holds the new value momentarily, so it's not free for
    #      another vreg.
    #   4. For each reg, walk positions 0..op_count-1 collecting
    #      maximal contiguous runs where the reg is occupied → list of
    #      disjoint `(start, end)` tuples (both inclusive).
    #
    # Positions advance by ONE per op, even when the op is a structured
    # `LoadImm` / `StoreLocal` (no asm bytes). Position is a logical
    # index over the op stream, not an asm byte offset.
    block_start_pos: List[int] = []
    cumulative = 0
    for blk in ir_function.blocks:
        block_start_pos.append(cumulative)
        cumulative += len(blk.ops)
    op_count = cumulative

    occupied_at: List[Set[str]] = [set() for _ in range(op_count)]
    for i, blk in enumerate(ir_function.blocks):
        n_ops = len(blk.ops)
        if n_ops == 0:
            continue
        # Pre-compute per-op uses/defs to walk reverse without re-parsing.
        per_op_ud: List[tuple] = [
            _single_op_uses_defs(op) for op in blk.ops
        ]
        # Reverse pass: seed with block's live_out, derive live_before
        # for every op.
        live = set(blk_info[i].live_out)
        live_before_per_op: List[Set[str]] = [set() for _ in range(n_ops)]
        for j in range(n_ops - 1, -1, -1):
            op_uses, op_defs = per_op_ud[j]
            live = op_uses | (live - op_defs)
            live_before_per_op[j] = set(live)
        # Forward: occupied(p) = live_before(p) ∪ defs(p).
        for j in range(n_ops):
            op_uses, op_defs = per_op_ud[j]
            pos = block_start_pos[i] + j
            occupied_at[pos] = live_before_per_op[j] | op_defs

    # Collect every reg that occupies any position.
    seen_regs: Set[str] = set()
    for occ in occupied_at:
        seen_regs |= occ

    live_intervals: Dict[str, List[tuple]] = {}
    for reg in seen_regs:
        runs: List[tuple] = []
        current_start: Optional[int] = None
        for p in range(op_count):
            if reg in occupied_at[p]:
                if current_start is None:
                    current_start = p
            else:
                if current_start is not None:
                    runs.append((current_start, p - 1))
                    current_start = None
        if current_start is not None:
            runs.append((current_start, op_count - 1))
        if runs:
            live_intervals[reg] = runs

    return FunctionLiveness(
        name=ir_function.name,
        blocks=blk_info,
        successors=succ,
        predecessors=pred,
        live_ranges=live_ranges,
        live_intervals=live_intervals,
        op_count=op_count,
    )


# ---------------------------------------------------------------------
# Pretty-printer (used by tools/devtools/liveness_dump.py)
# ---------------------------------------------------------------------

def format_liveness(ir_function: 'IRFunction',
                    liveness: FunctionLiveness,
                    show_ops: bool = False) -> str:
    """Format liveness info as a human-readable text report."""
    lines = []
    lines.append(f'=== Function {liveness.name} ===')
    lines.append(f'  {len(ir_function.blocks)} basic blocks, '
                 f'{len(liveness.live_ranges)} distinct physical regs touched')
    lines.append('')

    # Live ranges summary
    lines.append('--- Live ranges per physical reg ---')
    for reg in sorted(liveness.live_ranges.keys()):
        lo, hi = liveness.live_ranges[reg]
        span = hi - lo + 1
        lines.append(f'  {reg:5s}  blocks [{lo:3d}..{hi:3d}]  span={span} block(s)')
    lines.append('')

    # Per-block
    lines.append('--- Per-block analysis ---')
    for i, (blk, info) in enumerate(zip(ir_function.blocks, liveness.blocks)):
        label_disp = blk.label if blk.label else '(entry)'
        succ_disp = ', '.join(str(s) for s in liveness.successors.get(i, []))
        pred_disp = ', '.join(str(p) for p in liveness.predecessors.get(i, []))
        lines.append(f'block[{i:3d}]  label={label_disp}')
        lines.append(f'           pred={[pred_disp] if pred_disp else "[]"} '
                     f'succ={[succ_disp] if succ_disp else "[]"}')
        lines.append(f'           uses={sorted(info.uses)}')
        lines.append(f'           defs={sorted(info.defs)}')
        lines.append(f'           live_in ={sorted(info.live_in)}')
        lines.append(f'           live_out={sorted(info.live_out)}')
        if show_ops:
            from t900cc_ir import EmitRaw
            for j, op in enumerate(blk.ops):
                if isinstance(op, EmitRaw):
                    lines.append(f'           op[{j:3d}] {op.text.rstrip()}')
                else:
                    lines.append(f'           op[{j:3d}] {type(op).__name__}({op!r})')
        lines.append('')
    return '\n'.join(lines)
