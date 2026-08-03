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

`MirrorSession.step(pad)` returns `"ran"`, `"waiting"` (the peer's input for this
frame has not arrived — the caller must not advance anything else either) or
`"rejected"`. It never invents a button: a guess would desync the two PCs silently,
which is the one failure this design exists to avoid.

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
   session clock — that is the price, and it is visible. The peer console's flash size
   is derived from the IMAGE, never from this PC's flash setting: that setting is
   per-player, and sizing by it would build a different machine on each side.
3. **The same timing.** `PlayPage.start` applies the silicon-calibrated cart
   wait-states AFTER building its machine; a mirror without them runs the same code at
   a different speed and parts company inside the first frame. The wait-state setting
   is therefore part of the handshake fingerprint.
4. **The same input delay.** It decides which frame an input is played on, so two
   different values are two different matches. Refused, not adopted: the opening
   frames are pre-filled by the time a hello arrives.

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
fix, so the DLL's own bytes answer "same core?") plus the wait-state flag, and the
input delay. The **cartridge is announced, not compared**: the images are traded (§3b),
and the announcement is what the received image is checked against.

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

The debugger's Link tab tap is handed to the mirror's in-process cable
(`set_link_monitor`), or it would read zero bytes on a busy cable.

## 7b. Reaching it: direct address **or the lobby**

Two ways in, and they end in the same session:

- **Direct** — host on a port / join `host:port`. `Shell._host_mirror` / `_join_mirror`.
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

Same BIOS and same build on both sides (the cartridges may differ, and each player's
save rides along inside their image) · the session starts
from power-on, nobody joins a match in progress · an input that has not arrived stalls
BOTH sides for that frame (this is what a rollback layer would remove) · the local
2-player mode gains nothing, it is already at full speed · the **console schedule is in
the handshake fingerprint** (`+ilv<slice>`), because `core_fingerprint()` hashes the DLL
and would not move for a change to how Python steps the pair · the lobby's `mode` field
needs the **server redeployed**; without it every room reads as a cable room.

## 9. Validation

`tests/test_netplay_mirror.py` — 22 test functions, 24 cases with parametrisation: the
session logic against a list-based pipe (no ROM needed), **four real consoles** (two
PCs' worth of session, the probe ROM, a delayed wire) proving both PCs stay
byte-identical while every frame still runs, the **real `Shell`** in mirror mode over a
real socket, the cable being stepped a slice at a time rather than a frame (with the
test doubles as the control group), and a lobby room starting the link it advertised
(with a room that advertises none as the control). The room protocol itself is proved
against the **real server** in `tests/test_lobby.py`. Every behavioural fix in this
spec was checked by removing it and watching its test fail.
