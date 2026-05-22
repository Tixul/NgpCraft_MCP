"""Minimal read-only memory access helpers for NgpCraft Emulator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.bus import AddressProbe, NgpcAddressSpace, load_address_space
from core.rom import NgpcRomHeader, load_rom_header

if TYPE_CHECKING:
    # Forward-only import to keep memory.py free of an unconditional
    # dependency on frame_timing (the M3 module). The runtime import
    # happens locally inside `_build_builtin_readable_bytes` below.
    from core.frame_timing import FrameState


@dataclass(frozen=True)
class RomImage:
    """Loaded ROM file image."""

    path: Path
    data: bytes
    header: NgpcRomHeader

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class MemoryReadResult:
    """Result of a minimal read-only memory probe."""

    address: int
    width: int
    status: str
    probe: AddressProbe
    data: bytes | None
    note: str


@dataclass(frozen=True)
class NgpcReadBus:
    """Current minimal read-only bus model."""

    rom: RomImage
    address_space: NgpcAddressSpace
    builtin_bytes: dict[int, int]
    bios_bytes: bytes | None = None

    def read_bytes(self, address: int, size: int = 1) -> MemoryReadResult:
        if size <= 0:
            raise ValueError("size must be >= 1")

        chunks: list[int] = []
        first_probe: AddressProbe | None = None
        for offset in range(size):
            cur_addr = address + offset
            probe = self.address_space.probe(cur_addr)
            if first_probe is None:
                first_probe = probe
            if probe.region is None:
                return MemoryReadResult(
                    address=address,
                    width=size,
                    status="unmapped",
                    probe=probe,
                    data=None,
                    note="Read touches an unmapped address.",
                )
            if cur_addr in self.builtin_bytes:
                chunks.append(self.builtin_bytes[cur_addr])
                continue
            if probe.region.name == "CART_ROM_UNLOADED":
                chunks.append(0xFF)
                continue
            if probe.region.kind == "bios" and self.bios_bytes is not None:
                if probe.region_offset is None or probe.region_offset >= len(self.bios_bytes):
                    return MemoryReadResult(
                        address=address,
                        width=size,
                        status="out-of-file",
                        probe=probe,
                        data=None,
                        note="Computed BIOS file offset is outside the loaded BIOS image.",
                    )
                chunks.append(self.bios_bytes[probe.region_offset])
                continue
            if probe.file_offset is None:
                return MemoryReadResult(
                    address=address,
                    width=size,
                    status="unbacked",
                    probe=probe,
                    data=None,
                    note=(
                        "Address is mapped in the current model but not backed by readable "
                        "data yet."
                    ),
                )
            if probe.file_offset >= self.rom.size:
                return MemoryReadResult(
                    address=address,
                    width=size,
                    status="out-of-file",
                    probe=probe,
                    data=None,
                    note="Computed ROM file offset is outside the loaded file.",
                )
            chunks.append(self.rom.data[probe.file_offset])

        assert first_probe is not None
        return MemoryReadResult(
            address=address,
            width=size,
            status="ok",
            probe=first_probe,
            data=bytes(chunks),
            note=(
                "Read satisfied from the loaded ROM image, erased-cart fallback and/or the "
                "current minimal built-in system-memory backing."
            ),
        )


def _build_builtin_readable_bytes(
    header: NgpcRomHeader,
    *,
    frame_state: "FrameState | None" = None,
) -> dict[int, int]:
    """Return the built-in readable system-memory cold-start slice.

    Per `MEMORY_READ.md`, all writable on-chip regions (Work RAM, system
    page, shared Z80 RAM, K2GE registers, scroll maps, character RAM) are
    pre-initialised to `0x00` to match the documented power-on state.
    The `_check_writable_range` guard in `core/execute.py` still routes
    writes through the runtime overlay, which shadows these defaults on
    read (`_read_runtime_bytes` prefers the overlay over this builtin
    map).

    The single non-zero cell is `0x006F91` (HW_SYSTEM_MODE), which the
    BIOS reads from the ROM header mode byte at power-on.

    Cold-start invariants (matching real NGPC silicon at reset):
    - `0x004000..0x006BFF` : Work RAM, read as 0 at power-on
    - `0x006C00..0x006FFF` : system RAM page (including system-reserved
      slices), read as 0 at power-on; `0x006F91` carries the ROM header
      mode byte
    - `0x007000..0x007FFF` : shared Z80 RAM, read as 0 at power-on
    - `0x008000..0x008FFF` : K2GE registers and palette RAM, mostly 0
      at power-on with three documented non-zero K2GE control-register
      reset values (per `NGPC_HW_QUICKREF.md` § 5):
        * `0x008004` WSI.H : 0xFF (window width — full screen)
        * `0x008005` WSI.V : 0xFF (window height — full screen)
        * `0x008006` REF   : 0xC6 (frame rate — DO NOT MODIFY)
    - `0x009000..0x0097FF` : SCR1 map, read as 0
    - `0x009800..0x009FFF` : SCR2 map, read as 0
    - `0x00A000..0x00BFFF` : character RAM, read as 0

    The CPU I/O page (`0x000000..0x0000FF`) is deliberately NOT
    pre-populated here: those addresses gate timers, DMA channels and
    interrupt controller registers whose reset values are subsystem-
    specific and not yet modeled.

    When `frame_state` is provided (M3 Phase 3.1+), the K2GE raster
    position register `RAS.V` (`0x008009`) is overridden with the
    current scanline value and the `2D Status` register
    (`0x008010`) gets bit 6 (BLNK) set when `frame_state.in_vblank`
    is True. Other bits of `2D Status` stay 0 (C.OVR sprite overflow
    not modeled yet). With `frame_state=None`, both bytes default to
    `0x00` — equivalent to the documented HW reset (`initial_frame_state()`
    has `scanline=0`, `in_vblank=False`).
    """
    builtin: dict[int, int] = {}
    # Work RAM + system page
    for addr in range(0x004000, 0x007000):
        builtin[addr] = 0x00
    builtin[0x006F91] = header.mode_raw & 0xFF
    # Shared Z80 RAM
    for addr in range(0x007000, 0x008000):
        builtin[addr] = 0x00
    # K2GE register + palette RAM
    for addr in range(0x008000, 0x009000):
        builtin[addr] = 0x00
    # K2GE control-register reset overrides (HW-documented non-zero values).
    builtin[0x008004] = 0xFF  # WSI.H — window width, full screen at reset
    builtin[0x008005] = 0xFF  # WSI.V — window height, full screen at reset
    builtin[0x008006] = 0xC6  # REF   — frame rate (never modified)
    # M3 Phase 3.1: frame_state-derived raster + VBlank bit. Defaults
    # to 0x00 when no frame_state is given (HW reset state).
    if frame_state is not None:
        builtin[0x008009] = frame_state.scanline & 0xFF      # RAS.V
        builtin[0x008010] = 0x40 if frame_state.in_vblank else 0x00  # 2D Status bit 6 BLNK
    # SCR1 / SCR2 / character RAM
    for addr in range(0x009000, 0x00C000):
        builtin[addr] = 0x00
    return builtin


def load_rom_image(path: str | Path) -> RomImage:
    """Load ROM bytes and parse the header once."""
    rom_path = Path(path)
    data = rom_path.read_bytes()
    header = load_rom_header(rom_path)
    return RomImage(path=rom_path, data=data, header=header)


def load_read_bus(
    path: str | Path,
    *,
    frame_state: "FrameState | None" = None,
    bios_path: str | Path | None = None,
) -> NgpcReadBus:
    """Load the current minimal read-only bus model.

    M3 Phase 3.1+: an optional `frame_state` is forwarded to
    `_build_builtin_readable_bytes` so reads of `RAS.V` (`0x8009`) and
    the BLNK bit of `2D Status` (`0x8010`) reflect the live frame
    timing. Callers without a frame_state (most CLI commands at
    bootstrap) get the documented HW reset (scanline 0, BLNK=0) which
    is byte-identical to the pre-Phase 3.1 behavior.
    """
    rom = load_rom_image(path)
    bios_bytes = None
    if bios_path is not None:
        bios_bytes = Path(bios_path).read_bytes()
        if len(bios_bytes) != 0x10000:
            raise ValueError(
                f"BIOS image must be exactly 65536 bytes; got {len(bios_bytes)} from {bios_path}"
            )
    return NgpcReadBus(
        rom=rom,
        address_space=load_address_space(path),
        builtin_bytes=_build_builtin_readable_bytes(rom.header, frame_state=frame_state),
        bios_bytes=bios_bytes,
    )
