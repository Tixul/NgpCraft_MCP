#!/usr/bin/env python3
"""
ngpc_romtool.py  — NGPC ROM packager
NGPCraft Toolchain, Jalon 1

Takes a flat binary (assembled code) and produces a .ngc / .ngp ROM file
with the correct 64-byte SNK/Toshiba cartridge header.

Header layout (64 bytes, from ROM inspection + ngpcspec):
  0x00-0x1B  28 bytes  Copyright string (see --licensed / --copyright)
  0x1C-0x1F   4 bytes  Entry point address (little-endian, 32-bit / 24-bit used)
  0x20-0x21   2 bytes  Software ID BCD   (default 0x0000 = dev)
  0x22        1 byte   Software sub-code (default 0x00)
  0x23        1 byte   System compat     (0x00=mono only, 0x10=color)
  0x24-0x2F  12 bytes  Title ASCII, space-padded
  0x30-0x3F  16 bytes  Reserved zeros

Confirmed from NGPC_Template__2026/bin/main.ngc (reverse-engineered 2026-03-16).
"""

import argparse
import os
import sys

COPYRIGHT_OFFICIAL  = b"COPYRIGHT BY SNK CORPORATION"   # 28 bytes
COPYRIGHT_LICENSED  = b" LICENSED BY SNK CORPORATION"   # 28 bytes
HEADER_SIZE = 64
ROM_BASE    = 0x200000
DEFAULT_CODE_START = ROM_BASE + HEADER_SIZE  # 0x200040


def make_header(
    entry_point: int,
    title: str     = "HELLO",
    software_id: int = 0x0000,
    subcode: int    = 0x00,
    color: bool     = True,
    licensed: bool  = True,
) -> bytes:
    """Build the 64-byte cartridge header."""
    hdr = bytearray(HEADER_SIZE)

    # 0x00: copyright string (28 bytes)
    copyright_str = COPYRIGHT_LICENSED if licensed else COPYRIGHT_OFFICIAL
    hdr[0x00:0x1C] = copyright_str

    # 0x1C: entry point (32-bit LE)
    ep = entry_point & 0xFFFFFFFF
    hdr[0x1C] = (ep      ) & 0xFF
    hdr[0x1D] = (ep >>  8) & 0xFF
    hdr[0x1E] = (ep >> 16) & 0xFF
    hdr[0x1F] = (ep >> 24) & 0xFF

    # 0x20: software ID (2 bytes BCD, LE)
    hdr[0x20] = (software_id     ) & 0xFF
    hdr[0x21] = (software_id >> 8) & 0xFF

    # 0x22: sub-code
    hdr[0x22] = subcode & 0xFF

    # 0x23: system compatibility
    hdr[0x23] = 0x10 if color else 0x00

    # 0x24: title (12 bytes, space-padded, truncated)
    title_bytes = title.encode("ascii", errors="replace")[:12].ljust(12, b" ")
    hdr[0x24:0x30] = title_bytes

    # 0x30-0x3F: reserved zeros (already zero from bytearray init)

    return bytes(hdr)


def pad_to_power_of_two(data: bytes) -> bytes:
    """Pad ROM to the next power of 2 (minimum 64 KB for NGPC)."""
    sizes = [64*1024, 128*1024, 256*1024, 512*1024, 1024*1024, 2*1024*1024]
    for s in sizes:
        if len(data) <= s:
            return data + b'\xFF' * (s - len(data))
    raise ValueError(f"ROM too large: {len(data)} bytes (max 2 MB)")


def build_rom(
    body: bytes,
    entry_point: int,
    title: str    = "HELLO",
    software_id: int = 0x0000,
    subcode: int  = 0x00,
    color: bool   = True,
    licensed: bool = True,
    pad: bool     = False,
) -> bytes:
    """Assemble the full ROM image: header + body."""
    if len(body) > 2 * 1024 * 1024 - HEADER_SIZE:
        raise ValueError("Body too large for 2 MB cartridge")

    hdr = make_header(entry_point, title, software_id, subcode, color, licensed)
    rom = hdr + body

    if pad:
        rom = pad_to_power_of_two(rom)

    return rom


def main():
    p = argparse.ArgumentParser(
        description="NGPC ROM packager — NGPCraft Toolchain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Pack assembled binary, entry at 0x200040 (right after header)
  python ngpc_romtool.py hello.bin --output hello.ngc

  # Custom entry point, title, color ROM
  python ngpc_romtool.py code.bin --entry 0x200040 --title "MYGAME" --color --output mygame.ngc

  # Pack with padding to 64 KB
  python ngpc_romtool.py code.bin --pad --output game.ngc
""",
    )
    p.add_argument("binary",           help="Input flat binary (assembled code, without header)")
    p.add_argument("--output",  "-o",  default=None, help="Output ROM file (.ngc or .ngp)")
    p.add_argument("--entry",   "-e",  default=None,
                   help=f"Entry point address (hex or dec, default: 0x{DEFAULT_CODE_START:06X})")
    p.add_argument("--title",   "-t",  default="HELLO",   help="Game title (max 12 chars)")
    p.add_argument("--id",             default="0x0000",  help="Software ID BCD (default 0x0000)")
    p.add_argument("--subcode",        default="0x00",    help="Sub-code / version (default 0x00)")
    p.add_argument("--color",  "-c",   action="store_true", default=True,
                   help="Color-compatible ROM (0x10, default)")
    p.add_argument("--mono",           action="store_true", default=False,
                   help="Monochrome-only ROM (0x00)")
    p.add_argument("--copyright",      action="store_true", default=False,
                   help="Use 'COPYRIGHT BY SNK' header (default: ' LICENSED BY SNK')")
    p.add_argument("--pad",            action="store_true", default=False,
                   help="Pad ROM to next power-of-two size (min 64 KB)")
    p.add_argument("--info",           action="store_true", default=False,
                   help="Print parsed header of existing ROM (no build)")

    args = p.parse_args()

    # --info mode: parse existing ROM
    if args.info:
        with open(args.binary, "rb") as f:
            data = f.read()
        print(f"ROM size: {len(data)} bytes ({len(data)//1024} KB)")
        if len(data) < HEADER_SIZE:
            print("ERROR: file smaller than header (64 bytes)")
            return
        copyright_str = data[0x00:0x1C].decode("ascii", errors="replace")
        entry = int.from_bytes(data[0x1C:0x20], "little")
        sw_id = int.from_bytes(data[0x20:0x22], "little")
        subcode = data[0x22]
        compat = data[0x23]
        title = data[0x24:0x30].decode("ascii", errors="replace").rstrip()
        print(f"Copyright  : '{copyright_str}'")
        print(f"Entry point: 0x{entry:06X}")
        print(f"Software ID: 0x{sw_id:04X}")
        print(f"Sub-code   : 0x{subcode:02X}")
        print(f"Compat     : 0x{compat:02X} ({'color' if compat == 0x10 else 'mono' if compat == 0x00 else 'unknown'})")
        print(f"Title      : '{title}'")
        return

    # Build mode
    if args.output is None:
        p.error("--output is required for build mode")
    with open(args.binary, "rb") as f:
        body = f.read()

    # Parse entry point
    if args.entry is None:
        entry_point = DEFAULT_CODE_START
    else:
        entry_point = int(args.entry, 0)

    # Validate: entry point must be inside ROM
    if not (ROM_BASE <= entry_point < ROM_BASE + 2*1024*1024):
        print(f"WARNING: entry point 0x{entry_point:06X} is outside cart ROM range "
              f"(0x{ROM_BASE:06X}–0x{ROM_BASE+2*1024*1024-1:06X})", file=sys.stderr)

    color = not args.mono
    licensed = not args.copyright
    software_id = int(args.id, 0)
    subcode = int(args.subcode, 0)

    rom = build_rom(
        body,
        entry_point=entry_point,
        title=args.title,
        software_id=software_id,
        subcode=subcode,
        color=color,
        licensed=licensed,
        pad=args.pad,
    )

    with open(args.output, "wb") as f:
        f.write(rom)

    # Print summary
    ext = os.path.splitext(args.output)[1].lower()
    rom_type = "NGC (color)" if ext == ".ngc" else "NGP (mono)"
    print(f"[ngpc_romtool] {rom_type} ROM written: {args.output}")
    print(f"  Entry   : 0x{entry_point:06X}")
    print(f"  Title   : '{args.title}'")
    print(f"  Body    : {len(body)} bytes")
    print(f"  ROM     : {len(rom)} bytes total")


if __name__ == "__main__":
    main()
