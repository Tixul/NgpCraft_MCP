"""Minimal NGPC address-space helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.rom import NgpcRomHeader, load_rom_header


@dataclass(frozen=True)
class AddressMapEntry:
    """Named inclusive address range in the current minimal address map."""

    name: str
    start: int
    end: int
    kind: str
    note: str = ""
    backing_file_offset_base: int | None = None

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    def contains(self, address: int) -> bool:
        return self.start <= address <= self.end

    def region_offset(self, address: int) -> int:
        if not self.contains(address):
            raise ValueError(f"address 0x{address:06X} is outside region {self.name}")
        return address - self.start

    def file_offset(self, address: int) -> int | None:
        if self.backing_file_offset_base is None:
            return None
        return self.backing_file_offset_base + self.region_offset(address)


@dataclass(frozen=True)
class AddressProbe:
    """Result of probing one address in the address space."""

    address: int
    status: str
    region: AddressMapEntry | None
    region_offset: int | None
    file_offset: int | None
    note: str


@dataclass(frozen=True)
class NgpcAddressSpace:
    """Minimal address-space description derived from one ROM image."""

    rom_path: Path
    rom_size: int
    regions: tuple[AddressMapEntry, ...]

    def probe(self, address: int) -> AddressProbe:
        for region in self.regions:
            if region.contains(address):
                note = region.note
                if region.name == "CART_ROM_UNLOADED":
                    note = (
                        "Address is inside the cartridge ROM window but beyond the loaded "
                        "ROM image size."
                    )
                return AddressProbe(
                    address=address,
                    status="mapped",
                    region=region,
                    region_offset=region.region_offset(address),
                    file_offset=region.file_offset(address),
                    note=note,
                )
        return AddressProbe(
            address=address,
            status="unmapped",
            region=None,
            region_offset=None,
            file_offset=None,
            note="Address is not covered by the current minimal address map.",
        )


def build_address_space(header: NgpcRomHeader) -> NgpcAddressSpace:
    """Build the current minimal NGPC address-space map."""
    cart_window_end = 0x3FFFFF
    cart_loaded_end = min(0x200000 + max(header.file_size - 1, 0), cart_window_end)
    regions = [
        AddressMapEntry("CPU_IO_PAGE", 0x000000, 0x0000FF, "io", "Internal CPU I/O page."),
        AddressMapEntry("WORK_RAM", 0x004000, 0x006BFF, "ram", "User RAM area."),
        AddressMapEntry(
            "SYSTEM_RAM_RESERVED",
            0x006C00,
            0x006FB7,
            "reserved",
            "System-reserved RAM area.",
        ),
        AddressMapEntry(
            "USER_VECTOR_RAM",
            0x006FB8,
            0x006FFC,
            "ram",
            "User interrupt vector area.",
        ),
        AddressMapEntry(
            "SYSTEM_RAM_RESERVED_TAIL",
            0x006FFD,
            0x006FFF,
            "reserved",
            "System-reserved RAM tail.",
        ),
        AddressMapEntry("SHARED_Z80_RAM", 0x007000, 0x007FFF, "ram", "Shared RAM."),
        AddressMapEntry(
            "K2GE_REGS",
            0x008000,
            0x008FFF,
            "io",
            "Video registers and palette RAM.",
        ),
        AddressMapEntry("SCR1_MAP", 0x009000, 0x0097FF, "vram", "Scroll plane 1 map."),
        AddressMapEntry("SCR2_MAP", 0x009800, 0x009FFF, "vram", "Scroll plane 2 map."),
        AddressMapEntry("CHAR_RAM", 0x00A000, 0x00BFFF, "vram", "Character RAM."),
        AddressMapEntry(
            "CART_ROM_LOADED",
            0x200000,
            cart_loaded_end,
            "rom",
            "Loaded ROM image.",
            backing_file_offset_base=0,
        ),
    ]
    if cart_loaded_end < cart_window_end:
        regions.append(
            AddressMapEntry(
                "CART_ROM_UNLOADED",
                cart_loaded_end + 1,
                cart_window_end,
                "rom-gap",
                (
                    "Cartridge flash window not backed by the current file. The current read "
                    "model treats this range as erased flash (0xFF), which matches the 2 MB "
                    "flash-cart layout used by the local save tooling."
                ),
            )
        )
    regions.append(
        AddressMapEntry("BIOS_ROM", 0xFF0000, 0xFFFFFF, "bios", "Internal BIOS ROM.")
    )
    return NgpcAddressSpace(
        rom_path=header.path,
        rom_size=header.file_size,
        regions=tuple(regions),
    )


def load_address_space(path: str | Path) -> NgpcAddressSpace:
    """Load a ROM and build the current minimal address-space map."""
    return build_address_space(load_rom_header(path))
