"""NGPC frame / scanline timing model (M3 Phase 0).

Foundational model for the M3 milestone: track the scanline counter
and frame count for one CPU run, and expose the VBlank predicate so
downstream code can decide whether `RAS.V` reads return a visible
or VBlank line and whether bit 6 of `2D Status` (0x8010) is set.

Phase 0 ships the **state** only — Phase 3.1 will wire it through the
read bus so `_read_runtime_bytes(0x008009)` returns the live scanline
and `0x008010` exposes the BLNK bit driven by `in_vblank`. Phase 3.2
adds IRQ delivery at VBlank/HBlank boundaries.

Source for the scanline budget:
- `01_SDK/docs/K2GETechRef.txt` § 4-7 Frame Rate Register + § 4-8
  Raster Position Register
- Hardware quote: "signal generation for the 0th line occurs at the
  beginning of line 198" → scanlines cycle 0..197 (198 total per
  frame)
- "H_INT signal is not generated at line 151" → visible region is
  lines 0..151 (152 scanlines), VBlank is lines 152..197 (46
  scanlines), matching the 152-pixel-tall NGPC LCD

These values are HW-canonical and do not depend on the REF register
at reset (`0x8006 = 0xC6`); REF is locked and not meant to be
modified by software.
"""

from __future__ import annotations

from dataclasses import dataclass


# Scanline budget per `01_SDK/docs/K2GETechRef.txt` (60 fps NGPC).
SCANLINES_PER_FRAME = 198
VISIBLE_SCANLINES = 152
VBLANK_SCANLINES = SCANLINES_PER_FRAME - VISIBLE_SCANLINES  # 46

# Frame rate at the canonical reset gear (CPU 6.144 MHz, REF=0xC6).
FRAMES_PER_SECOND = 60

# Cycles per scanline at canonical reset gear (6.144 MHz, REF=0xC6).
#
# Derivation: K2GETechRef.txt § 4-8 documents the horizontal drawing
# operation as "internally 515 clock". The full scanline period
# including HBlank rounds to 517 cycles per scanline (CPU clock
# 6.144 MHz / (60 fps × 198 scanlines) ≈ 517.17 clk/sl). At gear 0
# this is the cycle budget the K2GE raster counter advances one
# step in.
#
# We use 517 as the integer constant — the fractional 0.17 averages
# out over many scanlines and doesn't affect the per-step advancement
# arithmetic in Phase 3.2.0/3.2.1.
CYCLES_PER_SCANLINE = 517

# Estimated CPU cycles per executed instruction (Phase 3.2.0 placeholder).
#
# **Non-reference-mode approximation** — see HARDWARE_COMPAT_POLICY.md
# § 4.3. The true TLCS-900 cycle count varies per opcode (typically
# 2..14 cycles for the operations actually executed today). We use a
# flat 8-cycle estimate to drive `frame_state` advancement during
# Phase 3.2.0/3.2.1 so the M3 chain can be built end-to-end before
# the proper TLCS-900 timing table lands.
#
# Phase 3.2.3 (pending) will replace this with a per-opcode table
# read from the executor. Until then, any reference-mode hardware
# fidelity claim that depends on cycle count is approximate.
ESTIMATED_CYCLES_PER_INSTRUCTION = 8


def scanlines_elapsed_from_cycles(cycles: int) -> int:
    """Return the integer number of scanlines elapsed for `cycles` CPU clocks.

    Uses the canonical `CYCLES_PER_SCANLINE` (517 at gear 0). Negative
    input is rejected — advancement is monotone in Phase 3.x; rewind
    belongs to M5.
    """
    if cycles < 0:
        raise ValueError(
            f"scanlines_elapsed_from_cycles requires cycles >= 0; got {cycles}"
        )
    return cycles // CYCLES_PER_SCANLINE


def advance_frame_state_by_cycles(
    state: "FrameState", cycles: int,
) -> "FrameState":
    """Advance `state` by the integer scanlines implied by `cycles` CPU clocks.

    Wraps the scanline counter modulo `SCANLINES_PER_FRAME` and carries
    into `frame_count`. See `scanlines_elapsed_from_cycles` for the
    cycles → scanlines conversion. Backward-compatible default for
    callers that don't yet track cycles: pass `cycles=0` for a no-op.
    """
    return advance_scanlines(state, scanlines_elapsed_from_cycles(cycles))


# --- IRQ controller state model (Phase 3.2.2a) ---
#
# NGPC IRQ source → level mapping per `01_SDK/docs/NGPC_HW_QUICKREF.md`
# § 4 "VECTEURS D'INTERRUPTION UTILISATEUR" and § 8 "MICRO DMA".

# NGPC IRQ levels (priority on the TMP95C061 interrupt controller).
# Only `VBlank` (level 4) is modeled in Phase 3.2.2a; the others
# (RTC alarm, Z80 IRQ, Timer 0..3) get state-only support so future
# sub-phases can wire them through the same `IrqState`.
IRQ_LEVEL_VBLANK = 4

# Vector RAM addresses installed by the BIOS (system page 0x6Fxx).
# These are JMP entry points the CPU reads after pushing PC+SR on an
# interrupt. The actual `irq_vector_address` for VBlank is `0x006FCC`.
VBLANK_VECTOR_ADDRESS = 0x006FCC


@dataclass(frozen=True)
class IrqState:
    """Pending-interrupt bitmask snapshot.

    Each bit `n` in `pending_mask` represents one pending IRQ source.
    Phase 3.2.2a tracks only `IRQ_LEVEL_VBLANK` (bit 4); future
    sub-phases set bits 0..7 as their sources land. Cleared bits mean
    "no pending request at that level".

    The mask is **runtime state**, not configuration: software writes
    to `0x008010` etc. don't change it. The executor (Phase 3.2.2b)
    will clear bits as it delivers IRQs.
    """

    pending_mask: int = 0

    def is_vblank_pending(self) -> bool:
        return bool(self.pending_mask & (1 << IRQ_LEVEL_VBLANK))

    def with_vblank_pending(self) -> "IrqState":
        return IrqState(pending_mask=self.pending_mask | (1 << IRQ_LEVEL_VBLANK))

    def with_vblank_cleared(self) -> "IrqState":
        return IrqState(pending_mask=self.pending_mask & ~(1 << IRQ_LEVEL_VBLANK))


def initial_irq_state() -> IrqState:
    """Return the post-reset IRQ state: nothing pending."""
    return IrqState(pending_mask=0)


def fold_vblank_irq_pending(
    irq_state: IrqState,
    transitions: tuple,
) -> IrqState:
    """Update `irq_state` for any `enter` VBlank transition in `transitions`.

    Pure folder: when a transition with `kind == "enter"` is observed
    in the advancement event list (from `detect_vblank_transitions`),
    set the VBlank pending bit. `leave` transitions don't clear the
    bit — the executor clears it on IRQ delivery (Phase 3.2.2b), or
    via explicit ack at the IRQ controller (Phase 3.2.2c).

    Returns the new (possibly identical) `IrqState`.
    """
    for transition in transitions:
        if transition.kind == "enter":
            irq_state = irq_state.with_vblank_pending()
    return irq_state


@dataclass(frozen=True)
class FrameState:
    """K2GE frame/scanline state at one observable moment.

    `scanline` is the current scanline counter `0..197`. Values
    `0..151` are visible (the 160×152 LCD region). Values `152..197`
    are VBlank.

    `frame_count` is the running count of completed frames since
    reset — wraps modulo 2**32 to keep savestate payloads compact;
    overflow happens after ~2 years of continuous 60 fps emulation.
    """

    scanline: int
    frame_count: int

    @property
    def in_vblank(self) -> bool:
        """True when the current scanline is in the VBlank region."""
        return self.scanline >= VISIBLE_SCANLINES

    @property
    def in_visible_region(self) -> bool:
        """True when the current scanline is in the visible LCD region."""
        return self.scanline < VISIBLE_SCANLINES


def initial_frame_state() -> FrameState:
    """Return the post-reset frame state: scanline 0, frame 0."""
    return FrameState(scanline=0, frame_count=0)


def advance_scanlines(state: FrameState, n: int) -> FrameState:
    """Advance the scanline counter by `n` scanlines.

    Wrapping is automatic: when the counter passes `SCANLINES_PER_FRAME - 1`
    it resets to 0 and `frame_count` is incremented by the appropriate
    amount. Negative `n` is rejected (the timing model is monotonic in
    Phase 0 — rewind belongs to M5).
    """
    if n < 0:
        raise ValueError(f"advance_scanlines requires n >= 0; got {n}")
    if n == 0:
        return state
    total = state.scanline + n
    new_frame_count = (state.frame_count + total // SCANLINES_PER_FRAME) & 0xFFFFFFFF
    new_scanline = total % SCANLINES_PER_FRAME
    return FrameState(scanline=new_scanline, frame_count=new_frame_count)


def advance_frames(state: FrameState, n: int) -> FrameState:
    """Advance `n` full frames, snapping the scanline back to 0.

    Semantically: "skip ahead `n` frame boundaries from the current
    state's frame". The scanline within the current frame is
    discarded; the result always lands at `scanline=0` of the n-th
    next frame. Negative `n` is rejected (see `advance_scanlines`).
    """
    if n < 0:
        raise ValueError(f"advance_frames requires n >= 0; got {n}")
    if n == 0:
        return state
    new_frame_count = (state.frame_count + n) & 0xFFFFFFFF
    return FrameState(scanline=0, frame_count=new_frame_count)


@dataclass(frozen=True)
class VBlankTransition:
    """One enter/leave-VBlank event observed during a scanline advance.

    `kind` is `"enter"` (visible → VBlank, scanline crosses 151→152)
    or `"leave"` (VBlank → visible, scanline crosses 197→0 i.e. frame
    boundary).

    `scanline` is the scanline AT the event — for `"enter"` it's the
    first VBlank scanline (152). For `"leave"` it's 0 (the first
    visible scanline of the new frame).

    `frame_count` is the frame count AT the event — `"leave"` events
    report the new frame's count (post-increment).
    """

    kind: str
    scanline: int
    frame_count: int


def detect_vblank_transitions(
    state: FrameState, n: int,
) -> tuple[VBlankTransition, ...]:
    """Enumerate VBlank enter/leave events that would occur while
    advancing `state` by `n` scanlines.

    The pure-state model doesn't need to fire IRQs (that's Phase 3.2)
    but reporting transitions makes the `tick-frame` CLI useful for
    diagnostics and lets future raster IRQ code consume the same
    sequence.

    Each emitted transition reports the state AT the boundary; the
    final post-advance state is `advance_scanlines(state, n)`.
    """
    if n < 0:
        raise ValueError(f"detect_vblank_transitions requires n >= 0; got {n}")
    transitions: list[VBlankTransition] = []
    current = state
    remaining = n
    while remaining > 0:
        if current.in_visible_region:
            # Next enter-VBlank is at scanline VISIBLE_SCANLINES of the
            # current frame.
            steps_to_enter = VISIBLE_SCANLINES - current.scanline
            if steps_to_enter <= remaining:
                current = advance_scanlines(current, steps_to_enter)
                transitions.append(
                    VBlankTransition(
                        kind="enter",
                        scanline=current.scanline,
                        frame_count=current.frame_count,
                    )
                )
                remaining -= steps_to_enter
            else:
                break
        else:
            # In VBlank — next leave is at frame wrap (scanline 0 of
            # next frame).
            steps_to_leave = SCANLINES_PER_FRAME - current.scanline
            if steps_to_leave <= remaining:
                current = advance_scanlines(current, steps_to_leave)
                transitions.append(
                    VBlankTransition(
                        kind="leave",
                        scanline=current.scanline,
                        frame_count=current.frame_count,
                    )
                )
                remaining -= steps_to_leave
            else:
                break
    return tuple(transitions)
