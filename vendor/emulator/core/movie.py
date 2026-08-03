"""Input recording and replay -- a bug you can hand to someone else.

Every playtest report in this project has so far been a sentence: "the text windows
get stuck halfway", "the HUD flickers". Sentences cannot be re-run. The person who
saw it has to see it again, on demand, while someone else watches -- and half the
findings in the log were chased from a description because there was no other way.

A movie fixes that. The console takes ONE BYTE of input per frame, at 0x00B0, and
that byte plus a starting state is the entire difference between two runs of the
same cartridge. So a recording is a snapshot and a list of bytes: sixty bytes a
second, a few kilobytes a minute, and it replays the session exactly.

The format is deliberately boring and self-describing:

    "NGPCMOV1"  u32 header_len  header(JSON, utf-8)  u32 state_len  state  inputs

`inputs` is one byte per frame, low 7 bits (0x80 is POWER and is not a button). The
header carries the identity of the ROM it was recorded against, because replaying a
movie on the wrong cartridge produces something that looks exactly like a bug --
`check` exists so that never gets mistaken for one.

⚠️ WHAT A MOVIE DOES NOT CAPTURE: the cartridge flash. A savestate deliberately
excludes it (it is a save, not a snapshot), so a game that reads its save file mid-
session can diverge from the recording. That is stated rather than papered over --
a replay that silently drifts is worse than no replay at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from core.hwregs import JOYPAD_BUTTON_MASK

MAGIC = b"NGPCMOV1"
FORMAT_VERSION = 1
# 0x80 is POWER, not a button. Taken from the one place the joypad layout lives
# rather than written as 0x7F here, so a movie can never record a power press
# because two files disagreed about which bit that was.
BUTTON_MASK = JOYPAD_BUTTON_MASK


def rom_fingerprint(data: bytes) -> str:
    """Short, stable identity for a cartridge image."""
    return hashlib.sha1(data).hexdigest()[:16]


@dataclass
class Movie:
    header: dict
    state: bytes = b""                       # the machine at frame 0; may be empty
    inputs: bytearray = field(default_factory=bytearray)

    @property
    def frames(self) -> int:
        return len(self.inputs)

    @property
    def seconds(self) -> float:
        return self.frames / 60.0

    @property
    def rom_name(self) -> str:
        return str(self.header.get("rom_name", ""))

    @property
    def rom_sha(self) -> str:
        return str(self.header.get("rom_sha", ""))


def dump(movie: Movie) -> bytes:
    head = dict(movie.header)
    head["version"] = FORMAT_VERSION
    head["frames"] = movie.frames
    blob = json.dumps(head, sort_keys=True).encode("utf-8")
    return b"".join((
        MAGIC,
        len(blob).to_bytes(4, "little"), blob,
        len(movie.state).to_bytes(4, "little"), bytes(movie.state),
        bytes(movie.inputs),
    ))


class BadMovie(ValueError):
    """The bytes are not a movie, or not one this build can read."""


def load(blob: bytes) -> Movie:
    if len(blob) < len(MAGIC) + 8 or not blob.startswith(MAGIC):
        raise BadMovie("not a movie file")
    at = len(MAGIC)
    head_len = int.from_bytes(blob[at:at + 4], "little"); at += 4
    if head_len > len(blob) - at:
        raise BadMovie("truncated header")
    try:
        header = json.loads(blob[at:at + head_len].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BadMovie(f"unreadable header: {e}") from e
    at += head_len
    if not isinstance(header, dict):
        raise BadMovie("header is not an object")
    if int(header.get("version", 0)) > FORMAT_VERSION:
        raise BadMovie(f"recorded by a newer build (format v{header.get('version')})")
    state_len = int.from_bytes(blob[at:at + 4], "little"); at += 4
    if state_len > len(blob) - at:
        raise BadMovie("truncated state")
    state = blob[at:at + state_len]; at += state_len
    return Movie(header, state, bytearray(blob[at:]))


@dataclass(frozen=True)
class Problem:
    fatal: bool
    text: str


def check(movie: Movie, *, rom_name: str = "", rom_sha: str = "",
          state_len: int = 0) -> list[Problem]:
    """Is this movie safe to replay here? Fatal problems must stop a replay.

    A movie played against the wrong cartridge produces garbage that looks exactly
    like an emulation bug, and a state blob of the wrong length would be applied
    field-by-field onto a different struct. Both are worth refusing outright; a
    mere name difference is not, because people rename files.
    """
    out: list[Problem] = []
    if rom_sha and movie.rom_sha and rom_sha != movie.rom_sha:
        out.append(Problem(True, (
            f"recorded against a different cartridge "
            f"({movie.rom_name or 'unknown'}). Replaying it here would produce "
            f"nonsense that looks like an emulation bug.")))
    elif rom_name and movie.rom_name and rom_name != movie.rom_name:
        out.append(Problem(False, (
            f"recorded as “{movie.rom_name}”, playing “{rom_name}” — same bytes, "
            f"different file name.")))
    if movie.state and state_len and len(movie.state) != state_len:
        out.append(Problem(True, (
            f"the saved machine state is {len(movie.state)} bytes and this build "
            f"expects {state_len} — recorded by a different version of the core.")))
    if not movie.state:
        out.append(Problem(False, (
            "no starting state: this movie replays from wherever the machine "
            "happens to be, so it only reproduces anything if you reset first.")))
    if not movie.frames:
        out.append(Problem(True, "the movie has no frames in it."))
    return out


class Recorder:
    """Collects one input byte per frame."""

    def __init__(self, header: dict, state: bytes = b"") -> None:
        self.movie = Movie(dict(header), bytes(state), bytearray())

    def record(self, byte: int) -> None:
        self.movie.inputs.append(byte & BUTTON_MASK)

    @property
    def frames(self) -> int:
        return self.movie.frames


class Player:
    """Hands back one input byte per frame, then reports that it is done.

    `next()` returns None past the end rather than looping or holding the last
    byte: a replay that quietly keeps pressing whatever was held on the final frame
    would leave the game walking into a wall and call it a reproduction.
    """

    def __init__(self, movie: Movie) -> None:
        self.movie = movie
        self.position = 0

    def next(self) -> int | None:
        if self.position >= self.movie.frames:
            return None
        b = self.movie.inputs[self.position]
        self.position += 1
        return b

    @property
    def done(self) -> bool:
        return self.position >= self.movie.frames

    @property
    def progress(self) -> float:
        return (self.position / self.movie.frames) if self.movie.frames else 1.0


def buttons_text(byte: int) -> str:
    """A frame's input, readable. The bit layout is the console's own and lives in
    ONE place -- `core.hwregs.JOYPAD_BITS` -- because a second copy drifts."""
    from core.hwregs import JOYPAD_BITS
    names = [n for n, bit in JOYPAD_BITS if (byte >> bit) & 1]
    return "+".join(names) if names else "—"


def summary(movie: Movie) -> str:
    lines = [f"{movie.frames} frames ({movie.seconds:.1f} s at 60 fps)"]
    if movie.rom_name:
        lines.append(f"cartridge: {movie.rom_name}")
    if movie.header.get("created"):
        lines.append(f"recorded: {movie.header['created']}")
    if movie.header.get("note"):
        lines.append(str(movie.header["note"]))
    held = sum(1 for b in movie.inputs if b)
    lines.append(f"{held} frames with a button held "
                 f"({100.0 * held / movie.frames:.0f}%)" if movie.frames
                 else "no input")
    return "\n".join(lines)
