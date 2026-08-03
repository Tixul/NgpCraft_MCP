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

**An archive can hold SEVERAL GAMES.** Collections ship that way, and picking the
largest member then means silently booting one arbitrary game out of forty -- worse
than an error, because nothing says so. So a member can be addressed directly with a
VIRTUAL PATH: `Pack.zip/Game A.ngc`. It is not a path on disk, and that is the point --
every per-game thing in this emulator (the flash save, the savestates, the watches, the
cover, the library entry) is derived from the ROM's `Path`, so a virtual path gives each
game inside an archive its own identity for free, and `Pack.zip` alone would give forty
games ONE shared save file.

`list_roms()` enumerates them; `load()` takes either form.
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


def split_member(path: str | Path) -> tuple[Path, str | None]:
    """Split a possibly-virtual path into (archive-or-file, member-inside-it).

    `Pack.zip/Game A.ngc` -> (`Pack.zip`, `Game A.ngc`); `Pack.zip` -> (`Pack.zip`,
    None); `Game.ngc` -> (`Game.ngc`, None). Members keep `/` separators because that
    is what the archive formats store, whatever the host filesystem writes.
    """
    p = Path(path)
    parts = p.parts
    for i in range(len(parts) - 1, -1, -1):
        if Path(parts[i]).suffix.lower() in ARCHIVE_EXTS:
            member = "/".join(parts[i + 1:])
            return Path(*parts[: i + 1]), (member or None)
    return p, None


def list_roms(path: str | Path) -> list[str]:
    """The ROM members inside an archive, in a stable order. [] for anything else.

    Only real ROM extensions count: a read-me or a cover in the same archive is not a
    game, and a collection with one ROM plus junk must still read as ONE game.
    """
    p = Path(path)
    if p.suffix.lower() not in ARCHIVE_EXTS:
        return []
    names = [n for n, _ in _entries(p) if Path(n).suffix.lower() in ROM_EXTS]
    return sorted(names, key=str.casefold)


def load(path: str | Path) -> LoadedRom:
    """Return the ROM bytes for `path`, transparently unpacking a zip / 7z.

    `path` may be an archive (one member is chosen, see `_pick_entry`) or a virtual
    `archive/member` path, which loads exactly that member.
    """
    archive, member = split_member(path)
    ext = archive.suffix.lower()
    if ext == ".zip":
        return _load_zip(archive, member)
    if ext == ".7z":
        return _load_7z(archive, member)
    # A bare ROM (or anything else): read the bytes and let the core judge them.
    p = Path(path)
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


def _entries(p: Path) -> list[tuple[str, int]]:
    """[(member name, uncompressed size)] for an archive; [] if it cannot be read.

    Listing must never be the thing that breaks a library scan: a corrupt or
    unsupported archive is one card missing, not a scan that stops.
    """
    ext = p.suffix.lower()
    try:
        if ext == ".zip":
            import zipfile
            with zipfile.ZipFile(p) as zf:
                return [(i.filename, i.file_size) for i in zf.infolist() if not i.is_dir()]
        if ext == ".7z":
            try:
                import py7zr
            except ImportError:
                return _entries_7z_cli(p)
            with py7zr.SevenZipFile(p, "r") as z:
                return [(i.filename, i.uncompressed) for i in z.list()
                        if not i.is_directory]
    except Exception:
        return []
    return []


def _entries_7z_cli(p: Path) -> list[tuple[str, int]]:
    """`7z l -slt` when py7zr is absent. Same contract: [] rather than an exception."""
    exe = _seven_zip_cli()
    if exe is None:
        return []
    try:
        out = subprocess.run([exe, "l", "-slt", str(p)],
                             check=True, capture_output=True, text=True,
                             errors="replace").stdout
    except (subprocess.CalledProcessError, OSError):
        return []
    items, name, size, folder = [], None, 0, False
    for line in out.splitlines():
        if line.startswith("Path = "):
            name, size, folder = line[7:].strip(), 0, False
        elif line.startswith("Size = ") and name is not None:
            size = int(line[7:].strip() or 0)
        elif line.startswith("Attributes = ") and "D" in line[13:].split():
            folder = True
        elif not line.strip() and name is not None:
            if not folder:
                items.append((name, size))
            name = None
    if name is not None and not folder:
        items.append((name, size))
    # `7z l` prints the archive's own path in its header before the entry list.
    return [(n, s) for n, s in items if n != str(p)]


def _resolve(p: Path, member: str | None, entries: list[tuple[str, int]]) -> str:
    """Which member to read: the one asked for, or the best guess."""
    if member is not None:
        names = {n for n, _ in entries}
        if member in names:
            return member
        # Written with the host's separator, or listed with a different one.
        alt = member.replace("\\", "/")
        for n in names:
            if n.replace("\\", "/") == alt:
                return n
        raise RomArchiveError(f"{p.name} has no entry named {member!r}")
    name = _pick_entry(entries)
    if name is None:
        raise RomArchiveError(f"{p.name} contains no file to load")
    return name


# -- zip (stdlib) ----------------------------------------------------------
def _load_zip(p: Path, member: str | None = None) -> LoadedRom:
    import zipfile

    try:
        with zipfile.ZipFile(p) as zf:
            entries = [(i.filename, i.file_size) for i in zf.infolist() if not i.is_dir()]
            name = _resolve(p, member, entries)
            data = zf.read(name)
    except zipfile.BadZipFile as exc:
        raise RomArchiveError(f"{p.name} is not a valid zip archive: {exc}") from exc
    return LoadedRom(data, Path(name).name, p, True)


# -- 7z (py7zr, else the 7-Zip CLI) ----------------------------------------
def _load_7z(p: Path, member: str | None = None) -> LoadedRom:
    try:
        import py7zr
    except ImportError:
        py7zr = None

    if py7zr is not None:
        try:
            with py7zr.SevenZipFile(p, "r") as z:
                entries = [(i.filename, i.uncompressed) for i in z.list()
                           if not i.is_directory]
            name = _resolve(p, member, entries)
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
        return _load_7z_cli(p, exe, member)

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


def _load_7z_cli(p: Path, exe: str, member: str | None = None) -> LoadedRom:
    # Extract to a scratch dir and read the file back. Naming the member keeps a
    # 40-game collection from being unpacked in full for one cartridge -- which is
    # the whole point of addressing a member, and on a solid archive the difference
    # between a moment and a minute.
    with tempfile.TemporaryDirectory(prefix="ngpc7z_") as td:
        args = [exe, "x", "-y", f"-o{td}", str(p)]
        if member is not None:
            args.append(member)
        try:
            subprocess.run(args, check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise RomArchiveError(f"7-Zip failed to extract {p.name}: {exc}") from exc
        files = [f for f in Path(td).rglob("*") if f.is_file()]
        if member is not None:
            want = Path(td) / member
            if not want.is_file():
                raise RomArchiveError(f"{p.name} has no entry named {member!r}")
            return LoadedRom(want.read_bytes(), Path(member).name, p, True)
        entries = [(str(f), f.stat().st_size) for f in files]
        name = _pick_entry(entries)
        if name is None:
            raise RomArchiveError(f"{p.name} contains no file to load")
        data = Path(name).read_bytes()
    return LoadedRom(data, Path(name).name, p, True)
