"""Instrumentation for the link cable: watch it, poke it, and break it on purpose.

Everything the cable carries passes through Python -- the in-process relay for
two players on one PC, the TCP relay for LAN/online, the lobby relay for the
server. That makes the byte stream observable without touching the core: a
:class:`LinkMonitor` sits in the relay and sees every byte in both directions.

Three jobs, all of them things the 2-player hunt had to do by guesswork:

* **Watch.** A timestamped ring of every byte that crossed, with totals. "Are
  bytes moving at all?" stops being a question you answer by staring at a
  window title.
* **Poke.** Feed bytes to a console by hand, or plug it into ITSELF
  (:class:`LoopbackLink`), so a link-capable game can be exercised with no
  second console, no second window and no peer.
* **Break.** Delay, drop or cut the cable deliberately. The relay is reliable
  and instant; a real online session is neither, and a game that only works on
  a perfect wire is a game that will desync on someone's connection.

The core stays the authority on flow control: this layer never invents bytes at
the hardware level, it only decides which of the host's bytes get handed over
and when. Pair it with ``NativeMachine.serial_state()`` (core/native.py), which
answers the other half of the question -- where a byte that is NOT crossing is
stuck.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

# Directions, as recorded in the log. TX is "this console put it on the wire",
# RX is "this console was handed it" -- always from the point of view of the
# machine the monitor is attached to.
TX = "TX"
RX = "RX"


@dataclass(frozen=True)
class LinkEvent:
    """One handover of bytes, in one direction, at one frame."""

    frame: int
    direction: str          # TX or RX
    data: bytes

    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    def ascii(self) -> str:
        return "".join(chr(b) if 32 <= b < 127 else "." for b in self.data)


@dataclass
class Impairment:
    """A deliberately imperfect cable.

    ``delay_frames`` holds every byte back that many pumps (latency), ``drop``
    is the per-byte loss probability 0.0-1.0, and ``cut`` is the cable yanked
    out -- bytes are consumed and discarded, which is not the same as a link
    that was never established (the console still thinks it is plugged in).
    Defaults are a perfect wire, so an untouched monitor changes nothing.
    """

    delay_frames: int = 0
    drop: float = 0.0
    cut: bool = False

    @property
    def active(self) -> bool:
        return self.cut or self.drop > 0.0 or self.delay_frames > 0


class LinkMonitor:
    """Sits in a relay and sees the cable: log, counters, injection, impairment.

    A relay calls :meth:`on_tx` with the bytes it just drained from the local
    machine and hands on whatever comes back (possibly nothing, possibly bytes
    held from earlier frames), then calls :meth:`on_rx` with what arrived for
    the local machine, and :meth:`take_injected` for bytes the user typed in by
    hand. All three are no-ops on a fresh monitor bar the logging, so wiring one
    in permanently costs a deque append per frame.
    """

    def __init__(self, capacity: int = 4096, *, seed: int | None = None) -> None:
        self.log: deque[LinkEvent] = deque(maxlen=capacity)
        self.frame = 0                  # host sets this; events are stamped with it
        self.bytes_tx = 0               # totals SINCE THIS MONITOR STARTED, not since boot
        self.bytes_rx = 0
        self.bytes_dropped = 0
        self.bytes_injected = 0
        self.impair = Impairment()
        self._held: deque[tuple[int, int]] = deque()   # (release_frame, byte)
        self._inject = bytearray()
        self._rng = random.Random(seed)

    # --- what the relay calls ----------------------------------------------
    def on_tx(self, data: bytes) -> bytes:
        """Log outgoing bytes and return what the peer should get THIS pump.

        With a perfect cable that is `data` unchanged. With an impairment it is
        `data` minus what was dropped, plus anything held from earlier frames
        whose delay has expired -- order preserved, because every byte waits the
        same number of frames.
        """
        if data:
            self.log.append(LinkEvent(self.frame, TX, bytes(data)))
            self.bytes_tx += len(data)
        imp = self.impair
        if not imp.active:
            return bytes(data)
        if imp.cut:
            self.bytes_dropped += len(data)
            self._held.clear()
            return b""
        keep = bytearray()
        for b in data:
            if imp.drop > 0.0 and self._rng.random() < imp.drop:
                self.bytes_dropped += 1
                continue
            keep.append(b)
        if imp.delay_frames <= 0:
            return bytes(keep)
        release = self.frame + imp.delay_frames
        self._held.extend((release, b) for b in keep)
        out = bytearray()
        while self._held and self._held[0][0] <= self.frame:
            out.append(self._held.popleft()[1])
        return bytes(out)

    def on_rx(self, data: bytes) -> None:
        """Log bytes delivered to the local machine. Never alters them: by the
        time they are here they have already crossed a (possibly broken) cable,
        and impairing both ends would count the same fault twice."""
        if data:
            self.log.append(LinkEvent(self.frame, RX, bytes(data)))
            self.bytes_rx += len(data)

    def take_injected(self) -> bytes:
        """Bytes the user asked to send to the local machine, if any."""
        if not self._inject:
            return b""
        out = bytes(self._inject)
        self._inject.clear()
        return out

    # --- what the debugger calls -------------------------------------------
    def inject(self, data: bytes) -> None:
        """Queue bytes to be handed to the local machine as if a peer sent them.

        This is a FAKE PEER, not a fake cable: the bytes go into the receive
        FIFO and travel the real path from there -- RTS gate, SC0BUF, INTRX0,
        the BIOS ring. If the game reacts, the receive path works.
        """
        self._inject += bytes(data)
        self.bytes_injected += len(data)

    def clear(self) -> None:
        """Reset the log and the totals; leaves the impairment settings alone."""
        self.log.clear()
        self.bytes_tx = self.bytes_rx = 0
        self.bytes_dropped = self.bytes_injected = 0

    def dump(self, *, group: int = 16) -> str:
        """The log as a hex+ASCII listing, one line per handover."""
        lines = []
        for ev in self.log:
            raw = ev.data
            for off in range(0, len(raw), group):
                chunk = raw[off:off + group]
                cell = LinkEvent(ev.frame, ev.direction, chunk)
                lines.append(f"{ev.frame:8d}  {ev.direction}  "
                             f"{cell.hex():<{group * 3}}  {cell.ascii()}")
        return "\n".join(lines)

    def raw(self, direction: str) -> bytes:
        """Every byte logged in one direction, concatenated -- for saving a
        capture to a file and diffing two sessions."""
        return b"".join(e.data for e in self.log if e.direction == direction)


class LoopbackLink:
    """Plug a console into ITSELF: what it transmits, it receives.

    Same shape as :class:`core.link.TcpLink` (``pump`` / ``disconnect`` /
    ``bytes_out`` / ``bytes_in``), so anything that accepts a network link
    accepts this one. It is not a peer -- a game expecting a partner's protocol
    will not be fooled for long -- but it exercises the whole hardware path
    (SC0BUF -> INTTX0 -> host -> receive FIFO -> RTS gate -> INTRX0 -> the BIOS
    ring) with one console, which is what you want when the question is "does
    the serial path work at all" and there is no second machine to hand.

    ``echo=False`` makes it a sink instead: the transmit FIFO is drained and
    discarded, so a game can talk to a cable that never answers -- the way to
    see how it handles a partner that has gone away.
    """

    def __init__(self, machine, *, monitor: LinkMonitor | None = None,
                 echo: bool = True) -> None:
        self.machine = machine
        self.monitor = monitor
        self.echo = echo
        self.bytes_out = 0
        self.bytes_in = 0
        self.machine.serial_set_enabled(True)

    def pump(self) -> None:
        # A loopback has no peer to hold us off, so CTS0 is always low (ready).
        self.machine.serial_set_cts(False)
        data = self.machine.serial_read_tx(256)
        if data:
            self.bytes_out += len(data)
        out = self.monitor.on_tx(data) if self.monitor is not None else data
        if not self.echo:
            out = b""
        if self.monitor is not None:
            out += self.monitor.take_injected()
        if out:
            self.machine.serial_write_rx(out)
            self.bytes_in += len(out)
            if self.monitor is not None:
                self.monitor.on_rx(out)

    def disconnect(self) -> None:
        self.machine.serial_set_enabled(False)


def relay(src, dst, *, tx_monitor: LinkMonitor | None = None,
          rx_monitor: LinkMonitor | None = None, chunk: int = 256) -> int:
    """Move one pump's bytes from `src`'s transmit FIFO to `dst`'s receive FIFO.

    A monitor is per-CONSOLE, so the two ends of one handover are watched by two
    different monitors: `tx_monitor` belongs to `src` (and is the one allowed to
    delay or drop, because the fault is on the wire leaving it), `rx_monitor`
    belongs to `dst` and only records. Either may be None. Returns how many bytes
    were handed over. Delivery is unconditional -- the core's serial_tick is the
    flow-control gate, and holding bytes back here can strand a handshake byte
    and read as "no cable".
    """
    data = src.serial_read_tx(chunk)
    out = tx_monitor.on_tx(data) if tx_monitor is not None else data
    if not out:
        return 0
    dst.serial_write_rx(out)
    if rx_monitor is not None:
        rx_monitor.on_rx(out)
    return len(out)


def deliver_injected(machine, monitor: LinkMonitor | None) -> int:
    """Hand the machine any bytes the user injected by hand, as a peer would.

    Kept apart from :func:`relay` because injection has no sender: it is the
    debugger standing in for the other console, so it happens on the LOCAL pump
    whether or not a peer exists.
    """
    if monitor is None:
        return 0
    data = monitor.take_injected()
    if not data:
        return 0
    machine.serial_write_rx(data)
    monitor.on_rx(data)
    return len(data)
