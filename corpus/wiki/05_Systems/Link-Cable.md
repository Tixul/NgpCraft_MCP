# Link Cable

Everything needed to **understand** and **program** the Neo Geo Pocket / Color link
cable: the serial hardware, the 11 BIOS COM vectors, the CTS/RTS handshake, the
**cable-detect line**, the symmetric programming model, a full session-handshake
walk-through, the gotchas, and how a faithful emulator models it.

---

## 1. What the link cable is

The NGPC link port is **Serial Channel 0 (SC0)** of the Toshiba TLCS-900/H CPU
(TMP95C061) — a plain **UART** exposed on the console's EXT connector. You never touch
the UART registers directly in normal code: the SNK BIOS owns SC0, its two interrupt
handlers, and two 64-byte ring buffers, and gives you **11 subroutines**
(`SYSTEM_CALL` vectors `0x10`–`0x1A`).

```
Line spec (set by COMINIT):  UART, 8 data bits, No parity, 1 stop bit (8N1)
Baud rate:                   19 200 bps
Flow control:                CTS / RTS hardware handshake
TX buffer:                   64-byte ring (BIOS-owned)
RX buffer:                   64-byte ring (BIOS-owned)
```

Two consequences of "it's a UART, not a shared bus":

- **Full-duplex, asynchronous.** Each console sends and receives independently. There
  is **no frame/VBlank synchronisation between the two consoles** — never assume the
  peer is on the same frame as you.
- **The cable is just a byte pipe.** Two consoles each run their own copy of the game
  and share *only* the bytes that cross the wire — no lockstep, no shared state, no
  rollback.

---

## 2. Registers (low I/O page)

| Addr | Name | Role | After COMINIT |
|------|------|------|---------------|
| `0x50` | **SC0BUF** | TX/RX data buffer (write = queue TX, read = RX byte) | — |
| `0x51` | **SC0CR** | Control / status (parity, RX error flags) | `0x00` |
| `0x52` | **SC0MOD** | Mode (UART bits, clock source, CTSE, RXE) | `0x49` |
| `0x53` | **BR0CR** | Baud-rate generator | `0x05` (→ 19 200 bps) |
| `0xB2` bit0 | **RTS** | Request-To-Send — GPIO **output** (see §4) | via COMONRTS/COMOFFRTS |
| `0xB1` bit2 | **cable-detect** | **Input**: 0 = peer connected, 1 = nothing plugged (§5) | hardware line |
| `0x6FE4` / `0x6FE8` | TX / RX ISR vectors (RAM) | BIOS handlers | — |

**SC0MOD bits:** `bit7 TB8 · bit6 CTSE · bit5 RXE · bit4 WU · bits3-2 SM(mode) · bits1-0 SC(clock)`.

- `0x49` = CTSE=1, RXE=0, UART 8-bit, baud generator — what **COMINIT** writes
  (**CTSE always on**).
- `0x69` = the same **plus RXE=1**, after **COMRECIVESTART** (`SC0MOD |= 0x20`).

`SC0CR` holds RX error flags (framing / parity / overrun), cleared on read. There is
**no readable CTS-status bit** — the handshake acts on the transmitter (§4).

---

## 3. The 11 BIOS COM vectors

Call mechanism: **`SYSTEM_CALL`** = `call [0xFFFE00 + vect*4]`, in **register bank 3**.
Use the table — **never `swi 1`** for these.

| Vect | Verified addr | Name | Role |
|------|---------------|------|------|
| `0x10` | `0xFF2BBD` | **COMINIT** | Configure SC0 + install TX/RX ISRs |
| `0x11` | `0xFF2C0C` | **COMSENDSTART** | Kick TX: first ring byte → SC0BUF |
| `0x12` | `0xFF2C44` | **COMRECIVESTART** | Enable RX (`SC0MOD |= 0x20`) + RTS low |
| `0x13` | `0xFF2C86` | **COMCREATEDATA** | Push 1 byte to the TX ring (`rb3`) |
| `0x14` | `0xFF2CB4` | **COMGETDATA** | Pop 1 byte from the RX ring (`rb3`) |
| `0x15` | `0xFF2D27` | **COMONRTS** | RTS **low** — allow peer (`and (0xB2),0xFE`) |
| `0x16` | `0xFF2D33` | **COMOFFRTS** | RTS **high** — block peer (`or (0xB2),0x01`) |
| `0x17` | `0xFF2D3A` | **COMSENDSTATUS** | TX status word (flags \| count) |
| `0x18` | `0xFF2D4E` | **COMRECIVESTATUS** | RX status word; clears error flags |
| `0x19` | — | **COMCREATEBUFDATA** | Block TX (`xhl3`=ptr, `rb3`=size) |
| `0x1A` | — | **COMGETBUFDATA** | Block RX (`xhl3`=ptr, `rb3`=size) |

Return constants (read out of CREATEDATA/GETDATA):

```
COM_BUF_OK = 0x00     COM_BUF_OVER = 0xFF (TX ring full)     COM_BUF_EMPTY = 0x01 (RX ring empty)
```

!!! warning
    `COM_BUF_OVER` is **0xFF**, not 1.

BIOS COM state in RAM (read-only, for debugging):

```
0x6C80 TX ring (64)   0x6CC0 RX ring (64)
0x6D00 TX count       0x6D01 RX count       0x6D02 TX head/tail
0x6D03 RX offset      0x6D04 TX-busy         0x6D05 overflow    0x6D06 RX errors
```

---

## 4. The CTS / RTS hardware handshake

This is what keeps two independent, unsynchronised consoles from overrunning each
other.

- **RTS** ("I am *ready to receive*") = **port `0xB2` bit0**, a GPIO **output** you
  drive via `COMONRTS` (low = ready) / `COMOFFRTS` (high = busy). It is a signal **to
  the peer**, not to your own UART.
- **CTS0** ("the peer is ready") = a dedicated CPU **input pin**. Wiring: **`CTS0` of
  console A is the `RTS` of console B** (and vice-versa).

Because `COMINIT` sets **CTSE = 1**, the transmitter is *gated on CTS0*: a queued byte
**waits** on the wire until `CTS0` goes **low** (the peer pulled its RTS low), then
ships and raises `INTTX0`. No polling — the silicon waits.

```
Peer ready   → its RTS low  → my CTS0 low  → my queued byte ships (INTTX0 fires)
Peer busy    → its RTS high → my CTS0 high → my byte waits in SC0BUF (back-pressure)
```

Practical rule: **wrap any long operation** (V-blank wait, heavy compute) with
`COMOFFRTS … COMONRTS`, so the peer parks its next byte instead of overrunning you.

---

## 5. Cable / peer detection — port `0xB1` bit2

**The piece missing from most references — and the one that decides whether a link
even starts.** Port `0xB1` carries two must-know input bits:

```
bit1 = CR2032 sub-battery present   (1 = OK; 0 → BIOS "SUB BATTERY DEAD" loop)
bit2 = LINK-CABLE DETECT            (1 = nothing plugged / idle, 0 = a peer is connected)
```

A game that arbitrates who starts a session reads `0xB1` bit2 to know **whether a peer
is physically there before it tries to talk**. *Card Fighters' Clash* does exactly this:

```asm
; sub 0x24065A — "is a cable connected?"  → A
    ld   A, (0xB1)      ; read the port
    and  A, 0x04        ; isolate bit2
    srl  2, A           ; A = bit2  (1 = no cable, 0 = cable)
    ret
; ...in the handshake coroutine:
    call 0x24065A
    cp   A, 1
    ret  Z             ; bit2 == 1 (no cable) → WAIT, do not become initiator
    call 0x241F16      ; bit2 == 0 (cable!)  → send the session hello
```

With **no cable, bit2 = 1** → the game waits. With a **cable, bit2 = 0** → it may become
the initiator.

!!! danger "Emulator note (critical)"
    Model `0xB1` bit2 from the *cable state*, never from a constant. Hard-forcing
    bit2 = 1 makes every peer-arbitrating game hang forever ("waiting for the other
    console"); hard-forcing bit2 = 0 makes single-console play report a spurious link
    error (e.g. *SNK Gals' Fighters*). The rule is **bit2 = (cable connected) ? 0 : 1**,
    and bit2 is an *input* — never let a value the game wrote into the I/O page decide
    it.

---

## 6. Programming model — the symmetric loop

**The same binary runs on both consoles.** Each sends its own state and reads the
other's; the CTS/RTS handshake (§4) does the low-level sync. There is no "server".

```c
#include "ngpc.h"

int main(void) {
    /* ... normal init ... */
    com_init();          /* 1. configure SC0 (8N1 / CTS-RTS / 19200 / IRQs)     */
    com_recv_start();    /* 2. arm reception — BOTH consoles must be powered on */

    while (1) {
        while (com_create_data(my_state) == COM_BUF_OVER)  /* ring full: spin */
            ;
        com_send_start();                                  /* flush TX onto the wire */

        if (com_rx_ok(com_recv_status()))
            while (com_get_data(&rx) == COM_BUF_OK)
                use(rx);

        com_rts_off();  WaitVsync();  com_rts_on();         /* yield the line */
    }
}
```

### The 5 rules (break one and it breaks)

1. `com_init()` **before** anything else.
2. `com_recv_start()` **only when both consoles are on** — else garbage data + runaway
   interrupts.
3. Wrap every **long operation** with `com_rts_off() … com_rts_on()`.
4. Call `com_get_data` / `com_get_block` **after** V-blank — they block interrupts.
5. Use the vector **table**, **never `swi 1`**, and **do not hook** the serial TX/RX
   ISRs (`0x6FE4` / `0x6FE8`) — the BIOS owns them.

---

## 7. Session handshake — a real example

For a simple game (exchanging a position) the symmetric loop is enough — blast a few
bytes each frame. Games that set up a **session** before a large transfer (a card bank,
a player profile) run a short **rendezvous** first. *Card Fighters' Clash*, reverse-
engineered, is the canonical pattern:

1. Both consoles reach the *"IF PREPARATIONS COMPLETE, EITHER PLAYER MUST PUSH A"*
   screen, each having done `COMINIT` + `COMRECIVESTART` (SC0MOD = `0x69`), each
   **listening**.
2. A handshake coroutine loops: *try to receive the peer's hello; if nothing arrived
   AND the cable is present (`0xB1` bit2 == 0) AND the local player pressed **A**,
   become the **initiator***.
3. **Initiator** sends the hello **`0x55 0x77`**.
4. **Responder** receives it and replies with the ack **`0xAA 0x22`**.
5. Both set "preparations complete" and stream the card data; the screen advances to
   *"EXHIBITION MATCH BEGINS."*

Takeaways for your own protocol:

- **Pick an initiator explicitly.** "Either player pushes A" means *one* side breaks the
  symmetry. If both push at once you get two colliding initiators and the exchange
  aborts — arbitrate (a button, a coin-flip byte, a role screen).
- **Gate the first send on cable-detect** (`0xB1` bit2 == 0). Sending into an unplugged
  port is how you hang.
- **Use a recognisable hello + ack** (here `0x55 0x77` → `0xAA 0x22`; `0x55`/`0xAA` are
  the classic alternating-bit UART sync patterns) so each side confirms a real peer,
  not line noise.

---

## 8. A reusable session layer (`ngpc_link`)

Everything above is the transport. A game needs one layer more: find the peer, decide
who is who, frame the bytes, notice a disconnection. That layer exists as a drop-in
module in the base template, `optional/ngpc_link/`, and is used by a complete two-player
game (`04_MY_PROJECTS/Fini/NeoGeo_Windcup`, doc `LINK_2P.md`).

### 8.1 What it adds over the BIOS calls

```
0xA5 | type | seq | body | checksum       (checksum = (type + seq + body) XOR 0x5A)

  HELLO  version, payload size, token hi/lo, "I already have a session"
  DATA   the game's payload, NGPC_LINK_PAYLOAD bytes
  BYE    the peer is leaving
```

- The `0xA5` magic lets the parser latch back on after noise or a mid-session restart:
  one lost byte costs one packet, not the session.
- A payload-size mismatch is reported (`NGPC_LINK_MISMATCH`) instead of quietly
  shuffling bytes: two builds with different `NGPC_LINK_PAYLOAD` refuse to play.
- Silence for ~2 s reports `NGPC_LINK_LOST` while still announcing, so a peer that
  comes back rebuilds the session on its own.
- Nothing ever blocks: one call per frame, bounded work.

### 8.2 Who is host — the search-time rule ⭐

Both consoles run the same binary, and the NGPC link is an **asynchronous UART with no
master** (unlike the Game Boy, where the console driving the clock *is* the master). So
the roles cannot come from the hardware — and they must not come from a coin toss the
player cannot predict.

The rule that works, and the one cable games have always used in spirit:

> **Whoever opened the link screen first plays player one.**

Each console announces how long it has been searching; the longest search wins. In the
module the announced token is `(frames_searching << 4) | 4 random bits`.

- **Resolution is one frame, deliberately.** Counting in seconds looks tidier but leaves
  half a second of ambiguity in which the winner is random again — a measured gate at 30
  frames of head start failed with seconds and passes with frames.
- The four random bits only separate consoles that entered within a few frames
  (~80 ms — two people never press start that close together); identical draws are
  simply re-drawn.
- Two perfectly identical machines (emulators, same frame, nobody touching anything)
  stay in "searching" and settle the instant **any** button is touched, because the pad
  state feeds the draw.
- `ngpc_link_set_role()` remains for games whose menu already asked ("create" / "join").

**Do not ask both players to press the same button.** It is the intuitive design and it
is wrong: both press at once and nothing is decided. And never put "go back to the menu"
on a button the link screen also uses — a stray press sends a `BYE`, which drops *both*
consoles out of the session.

### 8.3 Input lockstep — the recipe that keeps 60 Hz ⭐

For a game where both consoles must simulate the same match (a versus game), the cheap
and exact scheme is **input lockstep**: each console sends its own controller for step N
and neither advances until it has the other one; both simulate both players. No game
state on the wire, so there cannot be two versions of the truth.

Two things decide whether this runs at 60 Hz or at 20:

- **The round trip is TWO frames, not one.** A packet sent during frame `f` is only
  presented to the peer's CPU during `f+1`, and the peer's game code reads it at the
  start of `f+2`. Measured on two consoles: sending then waiting drops the game to
  20 fps; one step of input delay still leaves 44 % of frames waiting; **two steps of
  delay leaves 13 waiting frames out of 600** — a full 60 Hz, for ~33 ms between press
  and effect. That is the classic fixed-delay netplay trade-off.
- **Queue the received packets.** A session layer that keeps only the last packet
  (`ngpc_link_in[]`) is right for exchanging state, but wrong here: two packets landing
  in the same frame overwrite each other and the lost step desynchronises the match
  *silently*. Set `NGPC_LINK_RX_QUEUE` to 4 or 8 and pop with `ngpc_link_recv()`.

Carry a **step number** in each packet. It is what turns a desync into a message instead
of two consoles quietly playing different games.

Determinism is a property of the game, not of the link: check that no random number is
drawn on a path both consoles run. In the reference game every `QRandom()` sits in an
AI-only branch, which is exactly why lockstep on inputs is enough.

### 8.4 Settings must cross the wire too

Anything that changes the simulation has to be identical on both sides. In the reference
game the host sends arena, points, match duration **and difficulty** before the first
step — difficulty because the engine derives the *human* P2's speed from the opponent
definition. Two consoles configured differently would simulate two different matches
while each believing it was right.

---

## 9. Gotchas & field notes

- **`COMSENDSTATUS` can return garbage under compiler optimisation.** Building a link
  stub with `-O` produced a "No return value" path where `com_send_status()` was
  unreliable — do **not** gate your send on it; send paced/unconditional and let the
  ring's `COM_BUF_OVER` be your only back-pressure signal.
- **`com_rts_off()` executes `ei 6`.** A robust low-level alternative to "RTS low" is
  `*(volatile u8*)0xB2 &= 0xFE;` with your own `ei`.
- **Drain RX until empty.** Read `COMGETDATA` until `COM_BUF_EMPTY (0x01)`; don't trust
  a single status guess.
- **No inter-console frame sync.** Tolerate the peer being any number of frames
  ahead/behind.
- **`COMINIT` installs the BIOS serial handlers itself** (`0x6FE4` / `0x6FE8`). An SDK
  init routine that fills every user vector with a dummy handler is harmless as long as
  it runs *before* the link is opened. Saving those two pointers at boot to "restore"
  them afterwards overwrites the working handlers: exactly one byte leaves and the TX
  ring stays full, which reads as "cable unplugged".
- **Two different game versions can link** (CFC SNK ↔ Capcom): complementary roles,
  identical transport — the bridge is at the BIOS-COM byte level.

---

## 10. How a faithful emulator models the cable

- **Transport = a byte pipe.** Drain each machine's TX FIFO into the other; in-process
  for two players on one host, or over TCP for online (TCP because the cable never
  drops or reorders).
- **Honour RTS back-pressure.** Don't push bytes at a receiver holding RTS high.
- **Cross-wire CTS0 ↔ RTS.** Each console's `CTS0` = the *other* console's RTS, so a
  CTSE-gated transmitter is held until the peer is ready (§4).
- **Drive cable-detect from link state.** `0xB1` bit2 = `cable_connected ? 0 : 1` (§5).
  This one line is the difference between a peer-arbitrating game linking and hanging.
- **Do not** synchronise the two machines' clocks or frames — the real link is async.

---

## 11. Known gaps & what is *not* yet verified

None of these block a working link, but a programmer or emulator author should know
they are open:

- **Cable-detect polarity is inferred, not silicon-measured with two consoles.** A
  single-console HW probe reads `0xB1` = `0x07` (bit2 = 1 = *no cable*). "Cable present
  ⇒ bit2 = 0" is a strong inference — it makes *Card Fighters' Clash* link in-emulator
  and was confirmed live — but it has **not** been measured on two real consoles with a
  cable. A 2-console probe would confirm the exact level (pull-up idle vs driven-low).
- **The *other* GPIO input bits on `0xB0`–`0xBF` are still modelled loosely.** A single-
  console probe measured real read-only input bits a naive core does not reproduce:
  `B3` bit2 (input), `B6 = 0x05`, `BC = 0xFE`, `B1 = 0x07`. Only `0xB1` bit2 is modelled
  from the cable state so far. *Fatal Fury*'s detection writes `0xFF` to `0xBC` and reads
  it back (it passes today only because a naive core echoes writes) — a game relying on
  the *true* `B3`/`B6`/`BC` input behaviour could still misread. Open (fidelity, not
  blocking).
- **Block-transfer vectors `COMCREATEBUFDATA` (0x19) / `COMGETBUFDATA` (0x1A)**: BIOS
  entry addresses not individually verified; the `xhl3`=ptr / `rb3`=size ABI is from the
  manual, not re-confirmed by trace.
- **`BR0CR = 0x05 → 19 200 bps`**: the line works, but the exact divider→bit-rate
  computation has not been re-derived.
- **BIOS RX counter `0x6D01`** can lag in emulation (the ring still fills). Prefer the
  drain-until-`COM_BUF_EMPTY` idiom.
- **Online (`TcpLink`) initiator/responder sync** not re-tested since the cable-detect
  fix. It depends on the local cable-detect flag (armed by the link layer), so it
  *should* work, but the cross-host role rendezvous is untested.
- **`SC0CR` error flags** (framing / parity / overrun) are documented from the manual;
  emulation fidelity under real line conditions is unvalidated.
- **Role collision is no longer an open question**: see §8.2. Do not arbitrate roles by
  asking both players for the same button; elect the console that has been searching
  longest, at one frame of resolution.
- **Vector `0x0F`** (between `GEMODESET` and `COMINIT`) is a presumed reserved stub, not
  disassembled.

---

## See also

- [BIOS](../01_Hardware/BIOS.md) — the COM vectors alongside the rest of the BIOS.
- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — the full low-page I/O map.
- [Input](Input.md) — reading the controller (the initiator "push A" gate).
