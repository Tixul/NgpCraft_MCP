# Mirror netplay — send the BUTTONS, not the cable

`core/netplay.py`, wired into the shell by `Shell._begin_mirror` /
`PlayPage.attach_mirror`. This is a **second online mode**, alongside the cable relay
of [LINK_CABLE.md](LINK_CABLE.md); neither replaces the other.

## 0. The ceiling this exists to lift

The cable mode relays the link cable's own bytes. A link game writes a byte and
**blocks on the peer's reply**, so it advances one logic frame per round trip: every
millisecond of network latency comes out of the GAME's speed and nothing else. The
tell is that the audio stays perfect while the action crawls — audio comes from the
APU, which waits for nobody.

Measured through the shell, Fatal Fury link match, the game's own logic counter
(`0x4B3C`) per emulated frame:

| network round trip | cable relayed | mirror |
|---|---|---|
| 0 ms | 0.97 | 0.97 |
| 33 ms | 0.78 | **0.97** |
| 67 ms | 0.56 | **0.97** |
| 134 ms | 0.35 | **0.97** |
| 267 ms | 0.21 | **0.97** |

Two things were tried first and **measured worse or useless** — do not re-try them
without new evidence:

- **relaying the cable in instruction slices to go FASTER**: it does not (1.000 →
  0.957 with no delay). Each `PlayPage` already pumps after its OWN frame and the two
  tick one after the other, so player 1's bytes are in player 2's receive FIFO before
  player 2 runs. There is no frame of latency to win *for player 1's traffic*.
  ⚠️ A bench that steps both consoles and THEN pumps once shows a bigger win than the
  shell has, and that part is an artefact of the bench.
  🔧 **CORRECTION (2026-07-30): we now slice anyway, for CORRECTNESS, not speed.**
  Player 2's *answer* does wait a frame, and The Last Blade's handshake times out on
  it. `PlayPage.LINK_SLICE = 400` — see [LINK_CABLE.md](LINK_CABLE.md) for the table.
  What stays true here is the cost: slicing is not free, so an unlinked console does
  not do it.
- **dephasing the two consoles** on a shared timeline: sub-frame relaying buys 2-4 %.

## 1. The model

The NGPC link is two INDEPENDENT consoles running their own copy of the game, sharing
only serial bytes. So **run both consoles on both PCs**: the cable becomes an
in-process `InProcessLink` with no latency, and the network carries only each player's
**controller byte, numbered by frame**, played with a fixed **input delay**. Latency
is spent on input lag instead of on speed. This is how every other emulator does
netplay.

**The local cable is stepped like a cable, not a frame at a time.** `MirrorSession.step`
used to run `first.run_frames(1); pump; second.run_frames(1); pump`, which freezes one
console for the whole of the other's frame — a frame of latency on every answer, in one
direction, which is exactly what this mode exists to avoid. It now calls
`core.link.run_two_consoles_interleaved` (shared with the shell's local 2-player cable).
Deterministic by construction: a fixed slice, a fixed order, the same code on both PCs —
and **which console runs first still matters** for the reason in §4.

Measured through a mirror of a Card Fighters' Clash VS match, everything else equal:

| mirror cable schedule | bytes | screen |
|---|---|---|
| a whole frame each | 118/102 | its **LINK ERROR** |
| interleaved | **610/617** | **a card match in progress, both PCs identical** |

That game is the reason this matters: its VS handshake gives up on latency the cable
mode cannot avoid, so mirror play is the only way to play it online.

## 2. Why it is sound — measured before it was written

- **Determinism.** A full link match (menus plus 300 frames of fighting with varied
  input) replayed with the same inputs is byte-identical in both consoles' work RAM
  and in the framebuffer — including when the second run starts 2.5 s later on the
  wall clock, and with the RTC set to a different hour.
- **Rewind/replay.** Capture (CPU + `AuxState` + memory `0..0xBFFF`), run 6 frames,
  restore, replay the same inputs → identical state. This is what a later rollback
  layer would stand on.
- **Cost.** State = 49 820 bytes, capture 0.05 ms, restore 0.03 ms; the pair of
  consoles costs 3.3 ms per frame of a 16.7 ms budget.

## 3. The protocol

Wire format `[type:1][frame:4 LE][payload]`, over any `send(bytes)`/`recv() -> bytes`
pipe (`SocketPipe` for TCP, `ListPipe` for tests):

| type | payload | meaning |
|---|---|---|
| 1 HELLO | `[len:2][JSON]` | the handshake |
| 2 INPUT | 1 byte | the pad for that frame |
| 3 CHECK | 4 bytes | CRC32 of BOTH consoles at that frame |
| 4 CART | — (the length field is the peer's compressed image size) | opens the trade (§3b) |
| 5 CHUNK | that many bytes | a slice of the image |
| 6 BYE | `[len:2][reason]` | **why this side is refusing**, said before hanging up |

`MirrorSession.step(pad)` returns `"ran"`, `"waiting"` (the peer's input for this
frame has not arrived — the caller must not advance anything else either) or
`"rejected"`. It never invents a button: a guess would desync the two PCs silently,
which is the one failure this design exists to avoid.

### 3a. BYE — a refusal is not a hang-up

Refusing used to mean closing the socket. A TCP socket closed with **unread data in
its receive buffer sends a RESET**, so the player whose settings were fine got
`[WinError 10054]` and nothing else, while the actual reason sat on the *other*
screen. The reason now crosses first.

Some refusals are asymmetric *and* late — only the receiver can see that the cartridge
which arrived is not the one that was announced, and it sees it at the end of the
transfer, by which time the sender has finished its own trade and moved on to its
session. So the goodbye has to survive the handover: it lands in `leftover`, which is
exactly what is handed to `MirrorSession(prime=…)`, and the session reports it. Same
path as the opening inputs of a match.

`core.netplay.plain_network_error()` also turns the socket numbers a player can
actually hit (10054 / 10053 / 10060 / 10061 and their POSIX twins) into
`peer_closed` / `no_answer` / `refused`, keeping the raw text behind them.

### 3c. Flushing: the transport keeps what the kernel would not take

`SocketPipe` holds what a non-blocking socket refuses and retries later — `sendall()`
is unusable here, it raises the moment the buffer fills without saying how much it
already handed over. The rule that makes that safe: **`flush()` exists, and both
layers call it every pump, whether or not they have anything new to say.**

Without it, the tail of a cartridge never left the PC. `CartExchange` stopped talking
to the pipe once its last chunk was *queued*, and `MirrorSession` only writes when it
schedules a new input — which a session waiting for the peer never does. Measured,
2 MiB each way over a real socket with a slow reader: one side reported `done` at
100 % with **1 934 258 bytes still in its own buffer**; the other froze at 7.8 %, and
neither could recover. A loopback socketpair hides it completely, because the reader
drains as fast as the writer fills.

Two consequences in the same place:

- **`done` means posted, not queued** (`_sent >= len(_out)` *and* `pipe.pending == 0`),
  and the progress bar counts posted bytes. A bar that reads 100 % while the peer
  crawls is the bug, not a display detail.
- **Backpressure.** The shell pumps eight 32 KiB chunks per 16 ms tick — 16 MB/s
  offered to a network that will not take it. `CartExchange.IN_FLIGHT = 256 KiB` caps
  what may be outstanding. The lobby needs this as much as the socket does, so
  `LobbyClient.owed` / `LobbyPipe.pending` report how far behind the relay is; without
  it the client's queue is unbounded.

## 3c. The pair is stepped by the CORE (2026-08-15)

`run_two_consoles_interleaved` now calls `ngpc_run_linked`, so a mirror session's two
consoles are interleaved inside the emulation core on the cable's own clock rather than by
a host-side instruction slice. See `specs/LINK_CABLE.md` §3.3.

Three things about it matter here specifically:

- **Determinism is unchanged and still load-bearing.** Ties break towards the first
  console, the step size comes from the machines' own registers, and no wall clock is
  read. Measured: the same pair run three times gives the same CRC over both consoles'
  work RAM.
- **Player 1 still goes first, on both PCs, and it still decides the outcome.** Swapping
  the order changes the CRC — so §4's rule is not decoration, and a test now pins it.
- **The relay rule changed for mirror sessions**: the core pushes bytes unconditionally
  where `InProcessLink._relay` gated on the receiver's RTS. That was measured before the
  switch rather than inherited (probe ROM, 300 frames, identical byte counts either way);
  the limits of that measurement are stated in `specs/LINK_CABLE.md` §3.3.

An armed link monitor keeps the host-side relay, as does `NGPCRAFT_HOST_RELAY=1`.

## 4. The rules that are NOT optional

Each was a measured desync before it was a rule.

1. **Player 1's console runs FIRST, on both PCs** — not "ours first". Each console's
   frame is followed by a relay, so which one runs first decides whether a byte
   crosses this frame or the next. Ordering by "local, then mirror" makes the host run
   P1,P2 and the joiner P2,P1: the same match in two orders, drifting by one received
   byte within a few hundred frames.
2. **Both consoles are built identically on both PCs.** The local one comes from
   `PlayPage.start` with the player's own settings and **this PC's wall clock**; a
   mirror built with defaults desynced at frame **0**. So the mirror boots from the
   image the PEER SENT (see §3b) and both consoles are reset onto one fixed session
   clock (`Shell.MIRROR_CLOCK`, 2000-01-01). A game that shows the date shows the
   session clock — that is the price, and it is visible.
2b. **…and per-player settings are COPIED, not agreed on** — the same principle as the
   traded cartridge. The mirror of the other player's console used to be built from
   *our* settings, so two PCs with the same cartridge, the same BIOS and the same build
   could still be simulating two different machines. None of it was in the fingerprint,
   so nothing was refused: it drifted, and the checksum ended the match about a second
   in saying only "desync". Three of them, all now announced in the hello as
   `cons = {lang, mono}` and read back through `Handshake.peer_console`:
   - **cartridge language** (0x6F87) — per player, and a bilingual cartridge branches on it;
   - **NGP mono vs NGPC** — normally implied by the BIOS, **except with a simulated
     BIOS**: two BIOS-less consoles both fingerprint as `"hle"`, so nothing refused an
     NGP facing an NGPC;
   - **flash capacity** = `len(peer_image)`, full stop. The peer's session already
     padded its cartridge to its own capacity before sending, so the image's length *is*
     that capacity. Passing it back through `flash_capacity_bytes()` re-applied OUR
     setting on top, and an explicit 16 Mbit here turned a peer's 8 Mbit cart into a
     16 Mbit one. The comment said "from the image"; the code still asked this PC.
3. **The same timing.** `PlayPage.start` applies the silicon-calibrated cart
   wait-states AFTER building its machine; a mirror without them runs the same code at
   a different speed and parts company inside the first frame. The wait-state setting
   is therefore part of the handshake fingerprint.
4. **The same input delay** — it decides which frame an input is played on, so two
   different values are two different matches. **Reconciled before the session exists,
   refused after it does**, and the line between the two is `Handshake.locked`, set by
   `MirrorSession.__init__`:
   - during the trade nothing has been simulated yet, so **the joiner adopts the
     host's**. Direct-IP hosting used to ask BOTH players to type a number, and any
     difference killed the session — which, being a hang-up, reached the other player
     as a bare `[WinError 10054]` (§3a). The lobby already worked this way; it is now
     in `Handshake.check`, so both transports get it, and the joiner is no longer asked.
   - once a session is running the delay is baked into frames already played. Adopting
     one then would silently make the two PCs play two different matches, so it is
     refused. The session sends its own hello with whatever it settled on, which is
     where a joiner that failed to adopt is caught — loudly, instead of drifting.

## 3b. The cartridge trade — why the two players may differ

⛔ **WHAT THIS UNBLOCKS.** Building the mirror from the LOCAL image forced both players
to hold the same cartridge — and, because a save lives inside that image, the same
SAVE. Two players essentially never do. It also barred SNK-versus-Capcom in Card
Fighters' Clash, and that pairing is the reason the game has a link at all.

So `CartExchange` runs BEFORE the session, on the same pipe: each side sends its own
image (zlib level 1, 32 KiB chunks) while reading the other's, and both do the same
thing, so there is no leader. It carries the hello too, so one reader handles the whole
bring-up and there is no ordering between "who greets" and "who starts sending" to get
wrong. A few MiB, once, with progress on screen.

⚡ **THE LEFTOVER.** Whoever finishes first starts sending SESSION records while the
other is still receiving chunks, so those bytes land in the bring-up's buffer. They are
kept in `leftover` and handed to `MirrorSession(prime=...)`, or the first inputs of the
match are eaten there.

The image traded is the **loaded** one (`session._rom`) — padded to the flash chip's
capacity, with the save applied — not the file on disk. The hello announces a hash of
exactly that, and the receiver checks what arrived against it: a truncated or corrupted
transfer becomes a refusal now instead of a desync twenty seconds into the match.

## 5. The handshake refuses rather than drifts

A mismatch does not fail loudly by itself — it drifts, mid match, with no error
anywhere. `Handshake.check` compares protocol version, BIOS hash, **core build
fingerprint** (`native.core_fingerprint()` — the ABI number does not move for a timing
fix, so the DLL's own bytes answer "same core?") plus the wait-state flag. Those are
the things that decide how the code RUNS, and a mismatch in them cannot be worked
around.

Two kinds of field are **announced rather than compared**, because they describe what
each side IS and can simply be copied: the **cartridge** hash (the images are traded,
§3b, and the announcement is what the received image is checked against) and the
per-player **console settings** of §4.2b. The **input delay** is a third kind again —
negotiated, per §4.4.

## 6. Desync is detected, never repaired

Every 60 frames each side CRC32s **both** consoles, player 1's first (ordering by
"ours first" would never agree), and compares with the peer's for the same frame. A
mismatch ends the session and says so. Unanswered checksums are forgotten after a few
rounds rather than kept for the length of the match.

## 7. What the shell refuses while mirroring

A savestate, a rewind or a reset applied here and nowhere else puts the two PCs in
different states; the checksum would notice a second later and end the match, which is
a rotten way to answer a keypress. `PlayPage._mirror_blocks()` refuses them and says
why. `Shell._one_link_at_a_time()` keeps the two online modes (and the local cable)
mutually exclusive: both at once is two relays over one FIFO and two writers of one
controller port.

**The reason a match ended stays on screen for `PlayPage.MIRROR_ERROR_MS` = 15 s**, and
goes through `_flash`, not a bare `overlay.setText`. It is the only account the player
has, and a session can die in under a second — faster than a short flash can be read.
⚠️ `_flash` keeps **one** owned timer and restarts it. It used to build a new one per
message and leave the old one running, so an *older* countdown erased a *newer*
message: `attach_mirror` flashes "mirror ready" for 2.5 s, which is exactly the window
in which a session that fails at once dies. The player was told, and then untold.

The debugger's Link tab tap is handed to the mirror's in-process cable
(`set_link_monitor`), or it would read zero bytes on a busy cable.

## 7b. Reaching it: direct address **or the lobby**

Two ways in, and they end in the same session:

- **Direct** — host on a port / join `host:port`. `Shell._host_mirror` / `_join_mirror`.
  Hosting opens `ngpc_lobby.HostInfoDialog` (LAN address, auto-detected public one, a
  line to paste), the same panel the cable mode has always shown: "listening on port
  7789" is not an address, and the other player has no other way to reach this PC.
- **Lobby** — the same rooms, pairing and NAT traversal the cable mode uses. The relay
  carries opaque bytes, so it carries either protocol; what the two clients must agree
  on is what those bytes MEAN, and only the host chose. So a room advertises **`mode`**
  (`cable` / `mirror`; absent means cable, which is what an older client's room says)
  and, for a mirror, the **`delay`** — the input delay must be identical on both PCs, so
  **the joiner adopts the host's** rather than its own setting. The listing shows 🔗 or
  🪞 per room.

`core.lobby.LobbyPipe` presents the relay as a `Pipe`. It carries `lost` because the
lobby loses a peer through a **Qt signal**, not a socket error, and a session never told
would sit at "waiting for the other player" for ever. `Shell._begin_mirror` therefore
takes a **pipe**, not a socket, and `_close_pipe()` releases whatever that pipe owns —
a socket, or a room to leave plus a client thread to stop. An abandoned or failed
cartridge trade closes it too (`_end_mirror_bringup(close=True)`); the one caller that
hands the pipe straight to the session passes `close=False`.

## 8. Limits, stated plainly

Same BIOS and same build on both sides (the cartridges may differ, each player's save
rides along inside their image, and the per-player console settings of §4.2b are copied
rather than required to match) · **the same build means the same EXECUTABLE, and the
fingerprint cannot check that**: `core_fingerprint()` hashes the native core, so two
builds whose Python differs but whose DLL does not accept each other and then fail
anyway. Compare the .exe's date, not a number in a dialog · the session starts
from power-on, nobody joins a match in progress · an input that has not arrived stalls
BOTH sides for that frame (this is what a rollback layer would remove) · the local
2-player mode gains nothing, it is already at full speed · the **console schedule is in
the handshake fingerprint** (`+ilv<slice>`), because `core_fingerprint()` hashes the DLL
and would not move for a change to how Python steps the pair · the lobby's `mode` field
needs the **server redeployed**; without it every room reads as a cable room.

## 9. Validation

`tests/test_netplay_mirror.py` — 34 test functions, 36 cases with parametrisation: the
session logic against a list-based pipe (no ROM needed), **four real consoles** (two
PCs' worth of session, the probe ROM, a delayed wire) proving both PCs stay
byte-identical while every frame still runs, the **real `Shell`** in mirror mode over a
real socket, the cable being stepped a slice at a time rather than a frame (with the
test doubles as the control group), and a lobby room starting the link it advertised
(with a room that advertises none as the control). The room protocol itself is proved
against the **real server** in `tests/test_lobby.py`. Every behavioural fix in this
spec was checked by removing it and watching its test fail.

⚠️ **A loopback socketpair is not a wire.** It is symmetric and drains as fast as it
fills, so it cannot show a full send buffer — which is where §3c's deadlock lived,
invisible to a green suite. The trade tests use `_Trickle`, a socket double that
accepts `cap` bytes per call, and one of them runs the real `Shell._pump_mirror_bringup`
over it. A second trap in the same tests: an "image" of repeating bytes compresses to a
few hundred bytes and fits in any buffer, proving nothing — they use
`random.Random(n).randbytes()`.

🧪 **Two real `Shell`s, one socket** (`scratchpad/two_shells_mirror.py`, outside the
repo): nothing in the suite does this — the far end there is always a bare
`CartExchange`, so `_start_mirror` had only ever run on ONE side of a trade. Card
Fighters' Clash SNK + Capcom: trade plus 600 frames, no desync, no exception. Worth
rebuilding when the bring-up changes.

✅ **Played on two real PCs over a LAN, 2026-08-04** — the first mirror session outside
a bench. Still unproven by hand: the open internet, the lobby transport, and a long
match (a desync has time to appear).
