"""Link-cable transport: wire two NGPC machines together over the serial channel.

The NGPC link cable is TLCS-900 serial channel 0, driven by the SNK BIOS COM
routines. From the emulator's side the cable is nothing but a byte pipe: each
machine transmits bytes (drained with ``NativeMachine.serial_read_tx``) and
receives bytes (fed with ``NativeMachine.serial_write_rx``). Because the two
consoles each run their own copy of the game and share *only* those bytes, there
is no shared simulation to keep deterministic -- no rollback, no lockstep. A link
is just a reliable, ordered byte relay that honours the RTS flow-control line.

This module provides that relay in two shapes behind one tiny interface:

* :class:`InProcessLink` -- two machines in this process (two players on one PC).
* :class:`TcpLink`       -- one local machine + a TCP socket to a remote peer
                            (LAN / online). TCP because the cable never drops or
                            reorders bytes, which is exactly TCP's guarantee.

Both are pumped once per emulated frame, AFTER stepping the machine(s):

    link.pump()                      # in-process: moves bytes both directions
    # or, per machine, for the TCP case:
    link.pump()                      # sends local TX, delivers received RX

Enable the hardware path on each machine first (``machine.serial_set_enabled(
True)``); disabled, the serial registers stay inert and the cable reads as
unplugged -- the pre-link behaviour.
"""

from __future__ import annotations

import socket
from typing import Protocol

from core.link_debug import deliver_injected


class _SerialMachine(Protocol):
    """The slice of NativeMachine a link needs (see core/native.py)."""

    def serial_read_tx(self, max_bytes: int = ...) -> bytes: ...
    def serial_write_rx(self, data: bytes) -> None: ...
    def serial_rts(self) -> bool: ...
    def serial_set_cts(self, high: bool) -> None: ...
    def serial_set_enabled(self, on: bool) -> None: ...


# One TLCS-900 SC0 byte at 19200 bps is ~3200 CPU cycles; a frame is ~102 000,
# so ~32 bytes cross per frame at most. Drain generously past that so a burst is
# never left stranded in the FIFO for a frame.
_DRAIN_CHUNK = 256

# ⚡ HOW OFTEN TWO CONSOLES IN ONE PROCESS MUST BE INTERLEAVED, in instructions.
#
# Running one console's whole frame while the other stands still puts a frame of
# latency on the answer, in one direction, always -- and that is enough to lose Card
# Fighters' Clash's VS handshake (its packet reader gives up when the next byte is not
# already in the BIOS ring). Both places that own two consoles use this: the shell's
# local 2-player cable (PlayPage.LINK_SLICE) and mirror netplay
# (core.netplay.run_two_consoles_interleaved).
#
# ⚠️ THIS NUMBER IS A CORRECTNESS FIGURE, NOT A TUNING KNOB -- The Last Blade needs it
# no coarser than 400 (2000 already fails it). See PlayPage.LINK_SLICE for that table.
CABLE_SLICE = 400

# A frame is a few thousand instructions, so this is a runaway backstop with a wide
# margin, not a target: whatever happens, the caller finishes the frame the plain way.
_MAX_SLICES = 256


def run_two_consoles_interleaved(first, second, link) -> None:
    """Advance both consoles by one frame, a slice at a time, relaying between slices.

    `first` runs first within each slice round -- callers that must agree bit for bit
    across two PCs (mirror netplay) have to pass the SAME console first on both.

    Machines without the sliced `run` interface (test doubles) fall back to a whole
    frame each, which is what this code did everywhere before.
    """
    if not hasattr(first, "run") or not hasattr(second, "run"):
        first.run_frames(1)
        link.pump()
        second.run_frames(1)
        link.pump()
        return
    pair = (first, second)
    starts = [m.run(0, record=False)[0].frame_count for m in pair]
    done = [False, False]
    for _ in range(_MAX_SLICES):
        for i, m in enumerate(pair):
            if done[i]:
                continue
            summ, _ = m.run(CABLE_SLICE, record=False)
            if summ.executed == 0 or summ.frame_count != starts[i]:
                done[i] = True          # this console's frame is finished
        link.pump()
        if all(done):
            return
    for i, m in enumerate(pair):        # never leave a frame half-run
        if not done[i]:
            m.run_frames(1)
    link.pump()


class InProcessLink:
    """Cable between two machines living in the same process.

    ``pump()`` drains each machine's transmit FIFO and feeds it to the other,
    honouring the receiver's RTS: a machine holding RTS high (COMOFFRTS, "not
    ready") does not get bytes pushed at it, so they wait in the sender's FIFO --
    the same back-pressure the real handshake provides. Call once per frame,
    after stepping both machines.
    """

    def __init__(self, machine_a: _SerialMachine, machine_b: _SerialMachine, *,
                 monitor_a=None, monitor_b=None):
        self.a = machine_a
        self.b = machine_b
        # Optional core.link_debug.LinkMonitor per console: watches the bytes
        # (and, if the user asked for it, delays or drops them). None = a perfect
        # wire nobody is looking at, which is the normal case.
        self.monitor_a = monitor_a
        self.monitor_b = monitor_b
        self.a.serial_set_enabled(True)
        self.b.serial_set_enabled(True)
        self.bytes_ab = 0
        self.bytes_ba = 0

    @staticmethod
    def _relay(src: _SerialMachine, dst: _SerialMachine,
               tx_monitor=None, rx_monitor=None) -> int:
        if not dst.serial_rts():        # receiver is holding the sender off
            return 0
        buf = bytearray()
        while True:
            data = src.serial_read_tx(_DRAIN_CHUNK)
            if not data:
                break
            buf += data
        # One monitor call per pump, even for an empty drain: a monitor holding
        # bytes back for latency releases them on the call, so skipping it when
        # nothing new was sent would strand them until the next byte moved.
        out = tx_monitor.on_tx(bytes(buf)) if tx_monitor is not None else bytes(buf)
        if not out:
            return 0
        dst.serial_write_rx(out)
        if rx_monitor is not None:
            rx_monitor.on_rx(out)
        return len(out)

    def pump(self) -> None:
        # Cross-wire the hardware handshake: each console's CTS0 pin is the OTHER
        # console's RTS line (datasheet 3.11: RTS is any GPIO -> the peer's CTS0).
        # A machine ready to receive (RTS low) pulls the peer's CTS0 low, letting the
        # peer's CTSE-gated transmitter send. Without this the transmitter was never
        # held and a game that leans on the handshake (Card Fighters' Clash) could not
        # keep the two consoles in step. serial_rts() is True when RTS is LOW.
        self.a.serial_set_cts(not self.b.serial_rts())
        self.b.serial_set_cts(not self.a.serial_rts())
        self.bytes_ab += self._relay(self.a, self.b, self.monitor_a, self.monitor_b)
        self.bytes_ba += self._relay(self.b, self.a, self.monitor_b, self.monitor_a)
        # Bytes the debugger typed in by hand reach the console they were aimed
        # at, peer or no peer (core.link_debug.deliver_injected).
        deliver_injected(self.a, self.monitor_a)
        deliver_injected(self.b, self.monitor_b)

    def disconnect(self) -> None:
        self.a.serial_set_enabled(False)
        self.b.serial_set_enabled(False)


class TcpLink:
    """Cable between one local machine and a remote peer over TCP.

    Symmetric: run one on each end (one side ``listen()``, the other
    ``connect()``); both then call ``pump()`` once per frame. Local transmit
    bytes go out on the socket; bytes arriving on the socket are delivered to the
    local machine's receive FIFO when its RTS allows. The socket is non-blocking;
    ``pump()`` never stalls the emulation.
    """

    def __init__(self, machine: _SerialMachine, sock: socket.socket, *, monitor=None):
        self.machine = machine
        self.sock = sock
        self.sock.setblocking(False)
        # Optional core.link_debug.LinkMonitor: watches both directions and can
        # impair the outgoing one (latency/loss) to rehearse a bad connection.
        self.monitor = monitor
        self.machine.serial_set_enabled(True)
        self._rx = bytearray()
        self._out = bytearray()     # written but not yet accepted by the kernel
        self.bytes_out = 0
        self.bytes_in = 0
        # ⚡ WHY THE PEER WENT AWAY, once it has -- and None while the cable is good.
        #
        # ⛔ THE CRASH THIS ENDS. "Sometimes, some games lost the connection between
        # users playing in online mode and then, the emulator crashes." pump() caught
        # only BlockingIOError/InterruptedError, which are the "try again" cases. A peer
        # that GOES is a different family -- ConnectionResetError, BrokenPipeError,
        # ConnectionAbortedError -- and those escaped. MEASURED, both ways a peer can
        # vanish (a clean FIN and an RST from a kill/NAT drop): ConnectionResetError out
        # of the FIRST pump after the peer left, every time.
        #
        # That pump runs inside PlayPage._tick, which is a QTimer slot, and PyQt answers
        # an unhandled Python exception in a slot with qFatal(). MEASURED: the process
        # dies with 0xC0000409 and prints no traceback -- which is exactly why this reads
        # as "the emulator crashes" rather than as an error. (Same mechanism the root
        # conftest documents for the test runner.)
        #
        # So the link records the loss instead of raising it, and stops touching a dead
        # socket. The owner polls this and can say so; the emulation is never disturbed.
        self.lost: str | None = None

    # --- connection helpers -------------------------------------------------
    @classmethod
    def listen(cls, machine: _SerialMachine, host: str, port: int) -> "TcpLink":
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(1)
        conn, _ = srv.accept()          # blocks until the peer connects
        srv.close()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return cls(machine, conn)

    @classmethod
    def connect(cls, machine: _SerialMachine, host: str, port: int) -> "TcpLink":
        conn = socket.create_connection((host, port))
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return cls(machine, conn)

    def _lose(self, why: str) -> None:
        """The peer is gone. Record it once and let go of the socket."""
        if self.lost is None:
            self.lost = why or "peer closed"
        try:
            self.sock.close()
        except OSError:
            pass

    # --- per-frame pump -----------------------------------------------------
    def pump(self) -> None:
        if self.lost is not None:
            return                      # a dead cable carries nothing; do not touch it
        # 1) local TX -> socket. Drained into one buffer so the monitor sees one
        # handover per pump (and can release bytes it is holding back for latency
        # even on a pump where the game sent nothing).
        buf = bytearray()
        while True:
            data = self.machine.serial_read_tx(_DRAIN_CHUNK)
            if not data:
                break
            buf += data
        outgoing = self.monitor.on_tx(bytes(buf)) if self.monitor is not None else bytes(buf)
        self._out += outgoing
        if self._out:
            # ⛔ NOT `sendall` ON A NON-BLOCKING SOCKET. It raises BlockingIOError the
            # moment the kernel buffer fills and does NOT say how much of the buffer it
            # already handed over -- so the old code dropped the whole write, mid-stream,
            # and a link cable that loses bytes is a game that desyncs or hangs waiting
            # for a packet that was never sent. (core/lobby.py fixed exactly this on the
            # lobby socket; the direct host/join socket still had it.)
            #
            # `send` reports what it took. Keep the rest and offer it again next pump --
            # the frame is relayed several times now (PlayPage._run_one_frame), so a full
            # buffer costs a fraction of a frame, not a byte.
            try:
                sent = self.sock.send(self._out)
                self.bytes_out += sent
                del self._out[:sent]
            except (BlockingIOError, InterruptedError):
                pass                # buffer full: nothing taken, nothing lost, try again
            except OSError as e:
                # ...whereas this family IS the peer going away. Note it and stop.
                self._lose(str(e))
                return

        # 2) socket -> local buffer
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    # A clean FIN. This used to just leave the loop, so a peer that
                    # quit tidily was indistinguishable from one with nothing to say
                    # and the game sat there waiting for a console that had gone.
                    self._lose("peer closed the connection")
                    break
                self._rx.extend(chunk)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as e:
            self._lose(str(e))

        # 3) buffer -> local RX FIFO. Push unconditionally: the core's serial_tick
        # is the flow-control gate (it only PRESENTS a byte to the CPU once our RTS
        # is low), so delivering early just queues it like a real cable. Gating here
        # on RTS could strand a handshake byte and read as "no cable".
        if self._rx:
            self.machine.serial_write_rx(bytes(self._rx))
            self.bytes_in += len(self._rx)
            if self.monitor is not None:
                self.monitor.on_rx(bytes(self._rx))
            self._rx.clear()

        # 4) anything the debugger typed in by hand, as if the peer had sent it.
        deliver_injected(self.machine, self.monitor)

    def disconnect(self) -> None:
        self.machine.serial_set_enabled(False)
        try:
            self.sock.close()
        except OSError:
            pass
