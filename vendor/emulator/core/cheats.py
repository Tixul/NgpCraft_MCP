"""Named cheats: a set of addresses held at a value, every frame.

The Watch tab can already lock ONE address -- that is how you answer "what if HP
never drops". What it cannot do is keep a named, shareable group of them: a cheat
is usually two or three addresses that only mean something together (health AND
the death flag; lives AND the continue counter), and typing them back in one at a
time each session is how a map gets lost.

So this is deliberately NOT a second freezing mechanism. The player loop already
writes locked watches once per emulated frame; enabled cheats ride the same call,
at the same point, with the same semantics. Two mechanisms that both "hold a value"
would eventually disagree about which one won, and the answer would depend on
where in the frame each ran.

The text format is plain on purpose -- a cheat you cannot paste into a message is
a cheat nobody else can try:

    # Infinite health
    4812:1 = 63
    481A:2 = 03E7

Sizes are 1, 2 or 4 bytes and values are written little-endian, like every other
store this machine makes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SIZES = (1, 2, 4)

# Where a write actually lands. Holding a value in a region the CPU cannot write
# is not a cheat that does nothing -- on this machine it is a write into the
# cartridge's FLASH COMMAND LATCH, which is a good deal worse than nothing.
WRITABLE = (
    (0x004000, 0x006FFF, "work RAM"),
    (0x007000, 0x007FFF, "shared Z80 RAM"),
    (0x008000, 0x00BFFF, "video RAM"),
)
IO_PAGE = (0x000000, 0x0000FF)
CART = (0x200000, 0x3FFFFF)


@dataclass
class Entry:
    addr: int
    size: int = 1
    value: int = 0

    def bytes(self) -> bytes:
        size = self.size if self.size in SIZES else 1
        return (self.value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    def text(self) -> str:
        return f"{self.addr:06X}:{self.size} = {self.value:0{self.size * 2}X}"


@dataclass
class Cheat:
    name: str = ""
    entries: list[Entry] = field(default_factory=list)
    enabled: bool = False
    note: str = ""

    def apply(self, machine) -> None:
        for e in self.entries:
            machine.write(e.addr & 0xFFFFFF, e.bytes())

    def to_dict(self) -> dict:
        return {"name": self.name, "enabled": self.enabled, "note": self.note,
                "entries": [{"addr": e.addr, "size": e.size, "value": e.value}
                            for e in self.entries]}

    @classmethod
    def from_dict(cls, d: dict) -> "Cheat":
        return cls(
            name=str(d.get("name", "")),
            enabled=bool(d.get("enabled", False)),
            note=str(d.get("note", "")),
            entries=[Entry(int(e.get("addr", 0)) & 0xFFFFFF,
                           int(e.get("size", 1)), int(e.get("value", 0)))
                     for e in d.get("entries", []) if isinstance(e, dict)],
        )


def region_of(addr: int) -> str:
    for lo, hi, name in WRITABLE:
        if lo <= addr <= hi:
            return name
    if IO_PAGE[0] <= addr <= IO_PAGE[1]:
        return "I/O page"
    if CART[0] <= addr <= CART[1]:
        return "cartridge"
    return "unmapped"


def validate(cheat: Cheat) -> list[str]:
    """Problems worth saying out loud before someone blames the game.

    None of these stop a cheat from running: a debugger that refused an address
    because it looked wrong would be useless the day the address is right. They
    are told, not enforced.
    """
    out: list[str] = []
    if not cheat.entries:
        out.append("no addresses — this cheat does nothing")
    for e in cheat.entries:
        where = region_of(e.addr)
        if where == "cartridge":
            out.append(
                f"{e.addr:06X} is in the cartridge, which is FLASH, not RAM. A write "
                f"there does not change memory — it goes to the chip's command latch, "
                f"which is worse than doing nothing.")
        elif where == "I/O page":
            out.append(
                f"{e.addr:06X} is a hardware register, not a variable. Holding it "
                f"every frame fights the machine rather than the game.")
        elif where == "unmapped":
            out.append(f"{e.addr:06X} is not mapped to anything.")
        if e.size not in SIZES:
            out.append(f"{e.addr:06X}: size {e.size} is not 1, 2 or 4.")
        if e.value >= (1 << (max(1, e.size) * 8)):
            out.append(f"{e.addr:06X}: {e.value:X} does not fit in {e.size} byte(s).")
    return out


class CheatSet:
    def __init__(self) -> None:
        self.cheats: list[Cheat] = []

    def enabled(self) -> list[Cheat]:
        return [c for c in self.cheats if c.enabled and c.entries]

    def apply(self, machine) -> None:
        """Write every enabled cheat. Called once per EMULATED frame, from the same
        place the locked watches are written."""
        for c in self.enabled():
            c.apply(machine)

    # -- per-ROM persistence, same shape as WatchSet --------------------------
    def save(self, path: Path) -> None:
        path = Path(path)
        if not self.cheats:
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([c.to_dict() for c in self.cheats], indent=2),
                        encoding="utf-8")

    def load(self, path: Path) -> None:
        self.cheats = []
        path = Path(path)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.cheats = [Cheat.from_dict(d) for d in raw if isinstance(d, dict)]
        except (ValueError, OSError, TypeError):
            self.cheats = []


# -------------------------------------------------------------- text format
_LINE = re.compile(
    r"^\s*(?:0x)?([0-9A-Fa-f]{1,6})\s*(?::\s*([124]))?\s*=\s*(?:0x)?([0-9A-Fa-f]+)\s*$")


def parse_text(text: str) -> tuple[list[Cheat], list[str]]:
    """Parse the shareable format. Returns (cheats, complaints).

    Unreadable lines are REPORTED with their line number, never skipped in
    silence: a pasted code with one bad character that quietly loads three of its
    four addresses is a cheat that half-works, and half-working is the state that
    wastes the most time.
    """
    cheats: list[Cheat] = []
    problems: list[str] = []
    current: Cheat | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(";"):
            current = Cheat(name=line.lstrip("#; ").strip())
            cheats.append(current)
            continue
        m = _LINE.match(line)
        if not m:
            problems.append(f"line {n}: cannot read “{line}”")
            continue
        addr = int(m.group(1), 16)
        size = int(m.group(2)) if m.group(2) else 1
        value = int(m.group(3), 16)
        if current is None:
            current = Cheat(name="Unnamed")
            cheats.append(current)
        current.entries.append(Entry(addr & 0xFFFFFF, size, value))
    return [c for c in cheats if c.entries or c.name], problems


def format_text(cheats: list[Cheat]) -> str:
    out: list[str] = []
    for c in cheats:
        if out:
            out.append("")
        out.append(f"# {c.name or 'Unnamed'}")
        if c.note:
            out.append(f"# {c.note}")
        out.extend(e.text() for e in c.entries)
    return "\n".join(out)
