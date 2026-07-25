"""Load a ROM from a bare file OR from a `.zip` / `.7z` archive.

Real-world NGPC collections ship ROMs zipped, and often 7-zipped. This module
turns any supported path into the raw ROM bytes, so the rest of the emulator
never has to care whether the game arrived loose or inside an archive. It is the
single choke point `NativeSession` reads through, so archive support reaches the
library thumbnails, the player and the CLI in one place.

Back-ends:
  * **ZIP** -- Python's standard library. Always available.
  * **7z**  -- the pure-Python `py7zr` if installed, else a `7z`/`7za` command-
    line tool (PATH or a standard 7-Zip install). Opening a `.7z` with neither
    present raises a clear, actionable error -- we never silently fail to find the
    ROM and boot an empty cartridge.

When an archive holds several files we pick the NGPC ROM by extension and, if
more than one qualifies, the largest (the real ROM, not a bundled read-me).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# What counts as a raw NGPC ROM, whether loose or inside an archive.
ROM_EXTS = (".ngc", ".ngp")
ARCHIVE_EXTS = (".zip", ".7z")


class RomArchiveError(Exception):
    """An archive could not be opened, or held nothing loadable."""


def is_rom(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ROM_EXTS


def is_archive(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_EXTS


def is_loadable(path: str | Path) -> bool:
    """True for anything the library/open-dialog should offer: a ROM or an archive."""
    return is_rom(path) or is_archive(path)


@dataclass(frozen=True)
class LoadedRom:
    data: bytes          # the raw ROM image
    name: str            # the ROM's own file name (inner name for archives)
    source: Path         # the path the user actually opened
    from_archive: bool   # True -> the source is read-only, saves go to a sidecar


def load(path: str | Path) -> LoadedRom:
    """Return the ROM bytes for `path`, transparently unpacking a zip / 7z."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".zip":
        return _load_zip(p)
    if ext == ".7z":
        return _load_7z(p)
    # A bare ROM (or anything else): read the bytes and let the core judge them.
    return LoadedRom(p.read_bytes(), p.name, p, False)


def read_rom_bytes(path: str | Path) -> bytes:
    return load(path).data


# -- choosing the ROM inside an archive ------------------------------------
def _pick_entry(entries: list[tuple[str, int]]) -> str | None:
    """`entries` = [(name, uncompressed_size)]. Prefer a .ngc/.ngp entry; when
    several qualify (or none do) take the largest, which is the real ROM rather
    than a bundled read-me. Returns None for an empty archive."""
    if not entries:
        return None
    roms = [e for e in entries if Path(e[0]).suffix.lower() in ROM_EXTS]
    pool = roms or entries
    return max(pool, key=lambda e: e[1])[0]


# -- zip (stdlib) ----------------------------------------------------------
def _load_zip(p: Path) -> LoadedRom:
    import zipfile

    try:
        with zipfile.ZipFile(p) as zf:
            entries = [(i.filename, i.file_size) for i in zf.infolist() if not i.is_dir()]
            name = _pick_entry(entries)
            if name is None:
                raise RomArchiveError(f"{p.name} contains no file to load")
            data = zf.read(name)
    except zipfile.BadZipFile as exc:
        raise RomArchiveError(f"{p.name} is not a valid zip archive: {exc}") from exc
    return LoadedRom(data, Path(name).name, p, True)


# -- 7z (py7zr, else the 7-Zip CLI) ----------------------------------------
def _load_7z(p: Path) -> LoadedRom:
    try:
        import py7zr
    except ImportError:
        py7zr = None

    if py7zr is not None:
        try:
            with py7zr.SevenZipFile(p, "r") as z:
                entries = [(i.filename, i.uncompressed) for i in z.list()
                           if not i.is_directory]
            name = _pick_entry(entries)
            if name is None:
                raise RomArchiveError(f"{p.name} contains no file to load")
            # This py7zr has no in-memory read(); extract just the chosen entry to
            # a scratch dir and read it back. `targets` keeps it to one file even in
            # a solid archive; the temp dir is removed on the way out.
            with tempfile.TemporaryDirectory(prefix="ngpc7z_") as td:
                with py7zr.SevenZipFile(p, "r") as z:
                    z.extract(path=td, targets=[name])
                data = (Path(td) / name).read_bytes()
        except py7zr.exceptions.ArchiveError as exc:
            raise RomArchiveError(f"{p.name} is not a valid 7z archive: {exc}") from exc
        return LoadedRom(data, Path(name).name, p, True)

    exe = _seven_zip_cli()
    if exe is not None:
        return _load_7z_cli(p, exe)

    raise RomArchiveError(
        f"cannot open {p.name}: 7-Zip support needs the 'py7zr' package "
        "(pip install py7zr) or the 7-Zip command-line tool (7z/7za) on PATH."
    )


def _seven_zip_cli() -> str | None:
    for name in ("7z", "7za", "7zr"):
        exe = shutil.which(name)
        if exe:
            return exe
    for candidate in (
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _load_7z_cli(p: Path, exe: str) -> LoadedRom:
    # Extract to a scratch dir, pick the ROM out, read it back. Simpler and more
    # robust than driving per-entry extraction through the CLI's listing format.
    with tempfile.TemporaryDirectory(prefix="ngpc7z_") as td:
        try:
            subprocess.run(
                [exe, "x", "-y", f"-o{td}", str(p)],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            raise RomArchiveError(f"7-Zip failed to extract {p.name}: {exc}") from exc
        files = [f for f in Path(td).rglob("*") if f.is_file()]
        entries = [(str(f), f.stat().st_size) for f in files]
        name = _pick_entry(entries)
        if name is None:
            raise RomArchiveError(f"{p.name} contains no file to load")
        data = Path(name).read_bytes()
    return LoadedRom(data, Path(name).name, p, True)
