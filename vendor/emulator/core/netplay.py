"""Mirror netplay: send the BUTTONS across the network, not the cable.

⛔ THE CEILING THIS EXISTS TO LIFT. The other online mode relays the link cable's
own bytes. A link game writes a byte and BLOCKS on the peer's reply, so it advances
one logic frame per round trip: every millisecond of network latency is taken out of
the GAME's speed and nothing else. MEASURED through the shell (Fatal Fury link match,
the game's own logic counter 0x4B3C, per emulated frame):

    network round trip   0 ms   33 ms   67 ms   134 ms
    cable relayed        1.00    0.80    0.57     0.36

That is the "the fight runs in slow motion but the audio is perfect" report -- audio
comes from the APU, which waits for nobody.

🪞 THE WAY OUT. The NGPC link is two INDEPENDENT consoles that each run their own copy
of the game and share only serial bytes. So run BOTH consoles on BOTH machines: the
cable becomes an in-process byte pipe with no latency at all, and the only thing that
has to cross the network is each player's controller byte. Network latency is then
spent on INPUT DELAY instead of on speed. Same bench, same match, the peer's buttons
delayed instead of the cable:

    input delay      0     2     4     8     16 frames  (~267 ms of network)
    game speed      0.97  0.97  0.97  0.97  0.97

The speed stops depending on the ping. This is how every other emulator does netplay.

⚡ IT ONLY WORKS BECAUSE THE CORE IS DETERMINISTIC, and that was measured before any
of this was written, not assumed: a full link match (menus plus 300 frames of fighting
with varied input) replayed with the same inputs comes out byte-identical in both
consoles' work RAM and in the framebuffer -- including when the second run starts 2.5 s
later on the wall clock, and with the RTC set to a different hour. Capture/restore of
(CPU + AuxState + memory) round-trips exactly too, which is what a later rollback layer
would stand on.

⚠️ WHAT IT COSTS, and none of it is hidden from the player:
  * both sides run two consoles (measured 3.3 ms per frame for the pair, of 16.7);
  * both sides need the SAME cartridge, the same BIOS and the same core -- the
    handshake refuses the session otherwise, because a mismatch does not fail loudly,
    it drifts;
  * the session starts from power-on: nobody can join a game already in progress;
  * an input that has not arrived stalls BOTH sides for that frame. That is the honest
    behaviour of delay-based netplay, and it is what a rollback layer would remove.
  * a desync is detected, not repaired: the two sides compare a checksum of both
    consoles every second and say so.

This module is transport-agnostic on purpose: it talks to anything with `send(bytes)`
and `recv() -> bytes`, so the direct socket and the lobby relay both drive it, and the
tests drive it with a pair of lists.
"""

from __future__ import annotations

import json
import struct
import zlib
from collections import deque
from typing import Protocol

from core.link import run_two_consoles_interleaved

PROTOCOL_VERSION = 1

# Wire: [type:1][frame:4 LE][payload]. Fixed headers, so a partial read never has to
# be guessed at -- the reader keeps whatever it cannot complete yet.
_T_HELLO = 1        # payload = 2-byte length + JSON: what session this is
_T_INPUT = 2        # payload = 1 byte: the pad for that frame
_T_CHECK = 3        # payload = 4 bytes: a checksum of both consoles at that frame
# The cartridge trade that runs BEFORE the session (see CartExchange). Its records
# reuse this header with the LENGTH in the second field rather than a frame number.
_T_CART = 4         # payload = none; the field is the peer's compressed image length
_T_CHUNK = 5        # payload = that many bytes of it
_HDR = struct.Struct("<BI")

# How far ahead inputs are scheduled when nothing better is known. Three frames covers
# a ~50 ms round trip; the host puts its own choice in the handshake.
DEFAULT_DELAY = 3
CHECK_EVERY = 60            # frames between desync checksums (~1 s)


class Pipe(Protocol):
    """A reliable, ordered byte pipe -- what TCP and the lobby relay both are."""

    def send(self, data: bytes) -> None: ...
    def recv(self) -> bytes: ...


class ListPipe:
    """Two of these, cross-wired, are a network with no network in it (for tests)."""

    def __init__(self) -> None:
        self.peer: "ListPipe | None" = None
        self.delay_pumps = 0
        self._held: deque[tuple[int, bytes]] = deque()
        self._t = 0

    @staticmethod
    def pair(delay_pumps: int = 0) -> tuple["ListPipe", "ListPipe"]:
        a, b = ListPipe(), ListPipe()
        a.peer, b.peer = b, a
        a.delay_pumps = b.delay_pumps = delay_pumps
        return a, b

    def send(self, data: bytes) -> None:
        if not data or self.peer is None:
            return
        self._t += 1
        self.peer._held.append((self.peer._t + self.delay_pumps, bytes(data)))

    def recv(self) -> bytes:
        self._t += 1
        out = bytearray()
        while self._held and self._held[0][0] <= self._t:
            out += self._held.popleft()[1]
        return bytes(out)


class SocketPipe:
    """A TCP socket as a Pipe. Non-blocking, and it never loses a byte.

    ⛔ `sendall` is not usable here and the link cable learned it the hard way: on a
    non-blocking socket it raises BlockingIOError the moment the kernel buffer fills,
    WITHOUT saying how much it already handed over, so the caller cannot know what to
    resend. `send` reports what it took; the rest waits for the next call. A netplay
    stream that loses a byte does not degrade -- it desyncs.
    """

    def __init__(self, sock) -> None:
        self.sock = sock
        self.sock.setblocking(False)
        self._out = bytearray()
        self.lost: str | None = None

    def send(self, data: bytes) -> None:
        if self.lost is not None:
            return
        self._out += data
        try:
            sent = self.sock.send(self._out)
            del self._out[:sent]
        except (BlockingIOError, InterruptedError):
            pass                                    # buffer full: keep it, try again
        except OSError as e:
            self._lose(str(e))

    def recv(self) -> bytes:
        if self.lost is not None:
            return b""
        out = bytearray()
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    self._lose("peer closed the connection")
                    break
                out += chunk
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as e:
            self._lose(str(e))
        return bytes(out)

    def _lose(self, why: str) -> None:
        if self.lost is None:
            self.lost = why or "peer closed"
        try:
            self.sock.close()
        except OSError:
            pass


class Handshake:
    """What both sides must agree on before a single frame is simulated.

    Not politeness: the two consoles are simulated twice, once per PC, and anything
    that differs between the copies -- a different dump of the cartridge, a different
    BIOS, a core built with different timing -- makes them drift apart silently, mid
    match, with no error anywhere. Cheaper to refuse than to debug.

    ⚡ THE CARTRIDGE IS NOT ONE OF THOSE THINGS -- not any more. Requiring the same
    image meant requiring the same SAVE, since a save lives inside the cartridge
    image, and two players almost never have the same save. It also made Card
    Fighters' Clash impossible in this mode, and SNK-versus-Capcom is the whole point
    of that game. The images are TRADED instead (see :class:`CartExchange`), so each
    PC builds the other player's console from the other player's cartridge. What both
    sides still have to share is the BIOS and the build, because those decide how the
    code runs rather than what it is.
    """

    def __init__(self, *, rom_hash: str, bios_hash: str, core_version: str,
                 delay: int = DEFAULT_DELAY, host: bool = False) -> None:
        # Our own cartridge fingerprint: announced, never compared for equality. The
        # receiving side checks the image it was SENT against it, which catches a
        # truncated or corrupted transfer -- the failure that would otherwise show up
        # as a desync twenty seconds into a match.
        self.rom_hash = rom_hash
        self.bios_hash = bios_hash
        self.core_version = core_version
        self.delay = int(delay)
        self.host = bool(host)

    def payload(self) -> bytes:
        return json.dumps({
            "v": PROTOCOL_VERSION, "rom": self.rom_hash, "bios": self.bios_hash,
            "core": self.core_version, "delay": self.delay,
        }, sort_keys=True).encode("utf-8")

    def check(self, raw: bytes) -> str | None:
        """None when the peer's session matches ours; otherwise why it does not."""
        try:
            them = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return "unreadable_hello"
        if int(them.get("v", -1)) != PROTOCOL_VERSION:
            return "protocol_version"
        self.peer_rom_hash = them.get("rom")     # what their image must hash to
        if them.get("bios") != self.bios_hash:
            return "bios"
        if them.get("core") != self.core_version:
            return "core_version"
        # Both sides schedule their own input the SAME number of frames ahead, so a
        # different delay means the two PCs play different input streams from frame
        # zero. Adopting the host's would be nicer, but the opening frames are already
        # pre-filled by then -- refusing is the version that cannot be subtly wrong.
        if int(them.get("delay", -1)) != self.delay:
            return "input_delay"
        return None


class CartExchange:
    """Trade cartridge images, so each PC can build the OTHER player's console.

    ⛔ WHAT THIS UNBLOCKS. Building the mirror from the LOCAL image forced both players
    to hold the same cartridge -- and, because a save lives inside that image, the same
    SAVE. Two players almost never do. It also barred SNK-versus-Capcom in Card
    Fighters' Clash, which is the reason that game has a link at all.

    Runs BEFORE the session, on the same pipe, and both ends do the same thing, so
    there is no leader: each sends its image in chunks while reading the other's. It
    finishes when both directions are complete.

    ⚡ THE LEFTOVER MATTERS. Whoever finishes first starts sending SESSION records
    while the other is still receiving cartridge chunks, so those bytes land in this
    reader's buffer. They are kept in `leftover` and handed to the session, or the
    first inputs of the match would be eaten here.
    """

    # 32 KiB keeps a 4 MiB cartridge to ~128 sends without making one pump write
    # megabytes into a socket that has to stay responsive.
    CHUNK = 32 * 1024

    def __init__(self, pipe: Pipe, image: bytes, handshake: "Handshake", *,
                 chunk: int = CHUNK) -> None:
        self.pipe = pipe
        self.hs = handshake
        self.expect_hash: str | None = None  # filled from their hello, checked at the end
        self.greeted = False
        self._out = zlib.compress(bytes(image), 1)   # level 1: a cartridge is mostly
        self._sent = 0                               # already-compressed data anyway
        self._chunk = max(1024, int(chunk))
        self._in = bytearray()
        self._want: int | None = None       # their compressed length, once announced
        self._rx = bytearray()
        self.peer_image: bytes | None = None
        self.leftover = b""
        self.failed: str | None = None
        # Hello first, then our length: one record stream, so there is no ordering
        # between "who greets" and "who starts sending" to get wrong.
        js = self.hs.payload()
        self.pipe.send(_HDR.pack(_T_HELLO, 0) + struct.pack("<H", len(js)) + js)
        self.pipe.send(_HDR.pack(_T_CART, len(self._out)))

    @property
    def done(self) -> bool:
        return (self.greeted and self.peer_image is not None
                and self._sent >= len(self._out))

    @property
    def progress(self) -> tuple[float, float]:
        """(sent, received) as fractions, for something honest to put on screen."""
        up = self._sent / len(self._out) if self._out else 1.0
        down = (len(self._in) / self._want) if self._want else 0.0
        return (min(1.0, up), min(1.0, down))

    def pump(self) -> None:
        if self.failed is not None:
            return
        lost = getattr(self.pipe, "lost", None)
        if lost:
            self.failed = str(lost)
            return
        if self._sent < len(self._out):
            piece = self._out[self._sent:self._sent + self._chunk]
            self.pipe.send(_HDR.pack(_T_CHUNK, len(piece)) + piece)
            self._sent += len(piece)
        self._rx += self.pipe.recv()
        while self.peer_image is None and len(self._rx) >= _HDR.size:
            kind, n = _HDR.unpack_from(self._rx, 0)
            if kind == _T_HELLO:
                if len(self._rx) < _HDR.size + 2:
                    return
                (ln,) = struct.unpack_from("<H", self._rx, _HDR.size)
                if len(self._rx) < _HDR.size + 2 + ln:
                    return
                body = bytes(self._rx[_HDR.size + 2:_HDR.size + 2 + ln])
                del self._rx[:_HDR.size + 2 + ln]
                why = self.hs.check(body)
                if why:
                    self.failed = why
                    return
                self.expect_hash = self.hs.peer_rom_hash
                self.greeted = True
                continue
            if kind == _T_CART:
                del self._rx[:_HDR.size]
                self._want = int(n)
                continue
            if kind != _T_CHUNK:
                self.failed = "unexpected_record"
                return
            if len(self._rx) < _HDR.size + n:
                return
            self._in += self._rx[_HDR.size:_HDR.size + n]
            del self._rx[:_HDR.size + n]
            if self._want is not None and len(self._in) >= self._want:
                self._finish()
        if self.peer_image is not None:
            self.leftover += bytes(self._rx)
            self._rx.clear()

    def _finish(self) -> None:
        try:
            image = zlib.decompress(bytes(self._in))
        except zlib.error as e:
            self.failed = f"corrupt_transfer: {e}"
            return
        if self.expect_hash is not None and _image_hash(image) != self.expect_hash:
            # The peer told us what their cartridge hashes to in the hello. A transfer
            # that arrives different is a desync twenty seconds into the match; this
            # turns it into a refusal now.
            self.failed = "cartridge_transfer_mismatch"
            return
        self.peer_image = image


def _image_hash(image: bytes) -> str:
    import hashlib

    return hashlib.sha1(image).hexdigest()[:16]


class MirrorSession:
    """Two consoles, here, driven by two controllers -- one of them across a network.

    `local` is the console this player sees and plays; `peer` is the mirror of the
    other player's console, simulated here so the cable between them is a local byte
    pipe. Both machines are stepped in the order the shell's own relay uses (each
    console's frame is followed by a relay), because that order is what measured 1.00.
    """

    def __init__(self, local, peer, link, pipe: Pipe, handshake: Handshake,
                 prime: bytes = b"") -> None:
        self.local = local          # NativeMachine this player controls
        self.peer = peer            # NativeMachine mirroring the other player
        self.link = link            # core.link.InProcessLink between the two
        self.pipe = pipe
        self.hs = handshake
        self.delay = max(0, int(handshake.delay))
        self.frame = 0
        # Inputs are scheduled `delay` frames ahead, so the opening frames have no
        # input to wait for -- pre-filled, identically on both sides.
        self.local_inputs: dict[int, int] = {f: 0 for f in range(self.delay)}
        self.peer_inputs: dict[int, int] = {f: 0 for f in range(self.delay)}
        # ⚡ `prime` is what the bring-up read past the end of the cartridge trade:
        # whoever finished first was already sending session records. Dropping them
        # would eat the opening inputs of the match.
        self._rx = bytearray(prime)
        self._checks: dict[int, int] = {}       # frame -> the peer's checksum
        self._mine: dict[int, int] = {}         # frame -> ours, until the peer's lands
        self.desync_at: int | None = None
        self.rejected: str | None = None        # why the handshake failed, if it did
        self.greeted = False
        self.stalls = 0                         # frames we could not run: the ping, seen
        self.frames_run = 0
        self.bytes_out = 0
        self.bytes_in = 0
        js = self.hs.payload()
        self._send(_T_HELLO, 0, struct.pack("<H", len(js)) + js)

    # --- wire ---------------------------------------------------------------
    def _send(self, kind: int, frame: int, payload: bytes = b"") -> None:
        data = _HDR.pack(kind, frame & 0xFFFFFFFF) + payload
        self.pipe.send(data)
        self.bytes_out += len(data)

    def _drain(self) -> None:
        chunk = self.pipe.recv()
        if chunk:
            self._rx += chunk
            self.bytes_in += len(chunk)
        while len(self._rx) >= _HDR.size:
            kind, frame = _HDR.unpack_from(self._rx, 0)
            need = {_T_INPUT: 1, _T_CHECK: 4}.get(kind)
            if kind == _T_HELLO:
                if len(self._rx) < _HDR.size + 2:
                    return
                (n,) = struct.unpack_from("<H", self._rx, _HDR.size)
                need = 2 + n
            if need is None:
                # An unknown record type cannot be skipped safely (we do not know its
                # length), and guessing would desync the reader as well as the game.
                self.rejected = self.rejected or "unknown_record"
                self._rx.clear()
                return
            if len(self._rx) < _HDR.size + need:
                return
            body = bytes(self._rx[_HDR.size:_HDR.size + need])
            del self._rx[:_HDR.size + need]
            if kind == _T_INPUT:
                self.peer_inputs[frame] = body[0]
            elif kind == _T_CHECK:
                self._checks[frame] = struct.unpack("<I", body)[0]
            else:
                self.greeted = True
                why = self.hs.check(body[2:])
                if why:
                    self.rejected = why

    # --- state ---------------------------------------------------------------
    def checksum(self) -> int:
        """A cheap fingerprint of BOTH consoles, in the same order on both PCs.

        Player 1's console first, always -- otherwise the two sides would checksum
        their own console first and never agree even when perfectly in step.
        """
        p1, p2 = (self.local, self.peer) if self.hs.host else (self.peer, self.local)
        crc = zlib.crc32(p1.read(0x4000, 0x2C00))
        return zlib.crc32(p2.read(0x4000, 0x2C00), crc) & 0xFFFFFFFF

    # --- the frame ------------------------------------------------------------
    def step(self, pad: int) -> str:
        """Advance one frame if the peer's input for it has arrived.

        Returns "ran", "waiting" (the peer's input is not here yet -- the caller must
        NOT advance anything else either), or "rejected".
        """
        self._drain()
        if self.rejected:
            return "rejected"
        # Our own input is scheduled for a frame in the future, and played when that
        # frame comes round -- the SAME delay the peer's input gets, so both PCs
        # simulate identical input streams.
        at = self.frame + self.delay
        if at not in self.local_inputs:
            self.local_inputs[at] = pad & 0xFF
            self._send(_T_INPUT, at, bytes([pad & 0xFF]))
        if self.frame not in self.peer_inputs:
            self.stalls += 1
            return "waiting"

        mine = self.local_inputs[self.frame]
        theirs = self.peer_inputs[self.frame]
        self.local.write(0x00B0, bytes([mine]))
        self.peer.write(0x00B0, bytes([theirs]))
        # ⚡ PLAYER 1'S CONSOLE ALWAYS RUNS FIRST, on both PCs -- not "ours first".
        #
        # ⛔ THE DESYNC THIS ENDS, and the four-console test caught it on the first run:
        # each console's frame is followed by a relay (the order the shell's local cable
        # uses, and the one that measures full speed), so WHICH console runs first
        # decides whether a byte crosses this frame or the next. Ordering by "local,
        # then mirror" means the host runs P1,P2 and the joiner runs P2,P1 -- the same
        # match, simulated in two different orders, drifting by one received byte within
        # a few hundred frames.
        #
        # 🔑 ...AND THE TWO ARE INTERLEAVED, not run a whole frame each. This used to be
        # `first.run_frames(1); pump; second.run_frames(1); pump`, which freezes one
        # console for the whole of the other's frame -- a frame of latency on every
        # answer, in one direction, always. Card Fighters' Clash loses its VS handshake
        # to exactly that: measured through the shell's local cable, which had the same
        # shape, the HP exchange that starts a match dies after 118/102 bytes with
        # "LINK ERROR. CHECK CONNECTIONS AND SETTINGS." on one console and "CHOOSING
        # HP." for ever on the other. Mirror play is the answer for that game ONLINE --
        # its cable is local, so it carries no network latency at all -- but only if
        # the cable is stepped like a cable.
        #
        # Deterministic by construction: a fixed slice, a fixed order, the same code on
        # both PCs. Which console goes first still matters as much as it did above.
        first, second = ((self.local, self.peer) if self.hs.host
                         else (self.peer, self.local))
        run_two_consoles_interleaved(first, second, self.link)

        if self.frame % CHECK_EVERY == 0:
            # Fingerprint AFTER the frame ran, labelled with the frame that produced it,
            # and kept until the peer's copy for that same frame turns up -- it will
            # arrive a round trip later, not now.
            self._mine[self.frame] = self.checksum()
            self._send(_T_CHECK, self.frame, struct.pack("<I", self._mine[self.frame]))
        for f in sorted(set(self._mine) & set(self._checks)):
            if self._mine.pop(f) != self._checks.pop(f) and self.desync_at is None:
                self.desync_at = f
        # A checksum whose partner never turns up (a peer that stopped sending them)
        # must not accumulate for the length of the session. Keep a few rounds' worth --
        # enough to cover any round trip -- and forget the rest.
        for book in (self._mine, self._checks):
            while len(book) > 8:
                del book[min(book)]

        # Frames already played are never needed again; keeping them would grow without
        # bound over a long session.
        self.local_inputs.pop(self.frame, None)
        self.peer_inputs.pop(self.frame, None)
        self.frame += 1
        self.frames_run += 1
        return "ran"
