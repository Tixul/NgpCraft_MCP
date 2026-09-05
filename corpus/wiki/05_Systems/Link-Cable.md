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

### Where 19 200 bps actually comes from ⭐

`BR0CR = 0x05` is quoted everywhere as "19 200 bps"; here is the arithmetic, straight
from the TMP95C061 datasheet §3.11 (figures 3.11(6) SC0MOD, 3.11(8) BR0CR, 3.11(12) the
channel-0 block diagram), so it can be checked rather than trusted.

```
BR0CR bits:  bit7 fixed 0 | bits5-4 BR0CK (input clock) | bits3-0 BR0S (divider)

BR0CK = 00 -> phi-T0  = fc/4     01 -> phi-T2  = fc/16
        10 -> phi-T8  = fc/64    11 -> phi-T32 = fc/256
BR0S  = 0000 means divide by 16; 0001 is "don't set"; 0010..1111 divide by 2..15

BR0CR = 0x05  ->  BR0CK = 00 -> phi-T0 = fc/4
                  BR0S  = 5  -> divide by 5
SC0MOD= 0x69  ->  SM = 10   -> UART, 8 data bits
                  SC = 01   -> clocked by the baud rate generator
UART mode     ->  a further /16 (the transmit and receive counters are labelled
                  "UART only /16" in the block diagram)

baud = fc / 4 / 5 / 16 = fc / 320
```

With `fc = 6.144 MHz` — which you can get from the video timing alone,
`515 cycles/line x 199 lines x 60 Hz = 6 149 100`, so it owes the serial section
nothing — that is **19 200 bps** exactly. And 8N1 is 10 bit-times, so:

```
one byte = 10 / 19 200 = 520.83 us = 3200 CPU cycles at 6.144 MHz
```

**3200 cycles per byte** is the number an emulator needs, and it is derived, not fitted.

⚠️ **The BIOS writes these registers, not the cartridge.** Measured across ten
commercial link-capable cartridges, every one ends up at `SC0MOD = 0x69`, `BR0CR = 0x05`,
`SC0CR = 0x00`, and **every write comes from BIOS code**, never from cartridge space.
There is no per-game serial configuration to look up — which is exactly why the console
needs no per-cartridge table.


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

⚠️ **CTS gates the START of a byte — never one already going out.** The datasheet says
it twice: §3.11 *"when the CTS0 pin goes high, **after completion of the current data
send**, data send is halted"*, and Note 1 of fig 3.11(16) *"if the CTS signal rises
during transmission, the **next** data is not sent after the completion of the current
transmission"*. A shift register that has begun cannot be paused. An emulator that
re-tests CTS every tick and freezes the byte in flight will stall a transfer whenever
the peer pulses RTS mid-byte.

⚠️ **CTSE and the CTS0 pin exist on serial channel 0 only.** The channel-1 block diagram
greys them out with "there isn't in channel 1". The link is channel 0, so this is only a
warning against generalising from channel-1 documentation.

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

### ⚡ MEASURED ON SILICON — it is the PEER'S RTS, not "a cable is plugged in"

Two real consoles, a real cable, six physical states (2026-08-19):

| what is at the other end | `0xB1` bit2 |
|---|---|
| nothing at all | **1** — no peer |
| cable plugged into this console, **far end hanging loose** | **1** — no peer |
| cable to a console that is **switched OFF** | **1** — no peer |
| cable to a console **powered on but sitting in the BIOS** | **1** — no peer |
| cable to a console **running a cartridge that opened its serial port** | **0** — peer |

🔑 **A powered console at the BIOS, cable plugged in, does NOT register.** The bit follows
the peer's **RTS line** — the same signal that drives your `CTS0` — so it means *"the other
side has brought its serial channel up"*, not *"a connector is inserted"*. Nothing you can
do locally makes it read 0; only the peer's software can.

That is also why *Card Fighters' Clash*'s test is sound: `bit2 == 0` is a **rendezvous**,
not a connector check. Waiting on it is waiting for the peer to be **ready**.

⚡ **Unplugging is visible live.** Yank the cable mid-session and bit2 goes back to 1 within
a frame; plug it back in and it returns to 0 and bytes flow again. The **wire** recovers on
its own — but a game's own state machine may not: *Match of the Millennium* raises
`LINK ERROR` and drops to its main menu, and never retries. If you want a recoverable
session, design for it; the hardware will not do it for you.

Measured values, same session: `B1 = 0x03` linked, `0x07` unlinked (bit0 and bit1 are set
throughout while a cartridge runs).

A game that arbitrates who starts a session reads `0xB1` bit2 to know **whether the peer
is ready before it tries to talk**. *Card Fighters' Clash* does exactly this:

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

⛔ **ANNOUNCING IS NOT DECIDING — and this is where a real link game broke.** A HELLO is a
*snapshot*. By the time the peer's game code reads it, it is two frames old at best (§8.3)
and more behind a queued burst. A console that weighs its own **live** search counter against
that snapshot is comparing its present to the other's past, and the arithmetic is merciless:
with A starting at 0, B at Δ, and d frames of announcement lag, A concludes `t > t − Δ − d`
(always true) while B concludes `t − Δ > t − d` (true as soon as `d > Δ`). **Both consoles
claim player one, and both are right from where they stand.**

📏 Measured on two instances of the reference module, sweeping start skew against
announcement lag: **300 disagreements in 624 runs**, forming an exact `lag > skew` triangle
that saturates at the announcement interval. Two rules remove it:

1. **Freeze the search counter at first contact** — what you announce and what you compare
   must be the same number.
2. **Echo the last token you heard in every HELLO, and decide only when that echo comes back
   as your own.** At that instant both consoles provably hold the same pair, and a comparison
   on one pair cannot disagree.

Same sweep after: **0 disagreements in 624 runs.** The price is a round trip instead of a
one-way announcement — worst-case agreement went from 25 to 51 frames. An agreed role half a
second later beats two hosts.

- **Resolution is one frame, deliberately.** Counting in seconds looks tidier but leaves
  half a second of ambiguity in which the winner is random again — a measured gate at 30
  frames of head start failed with seconds and passes with frames.
- **The freeze costs a little accuracy, on purpose.** The two consoles do not freeze on the
  same frame, so a start-time difference under one announcement interval can still elect the
  one that opened the screen second. That was never resolvable to better than the
  announcement rate anyway, and an agreed role is what a session actually needs.
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
- ⛔ **DO NOT CALL THE COM ROUTINES FROM A TIGHT LOOP — IT WEDGES YOUR OWN RECEPTION.**
  Measured on silicon and in emulation: a probe calling `COMCREATEDATA` + `COMSENDSTART`
  every pass of its main loop (thousands of times a frame) stopped receiving entirely —
  `COMGETDATA` answered EMPTY for a whole second while the cable was demonstrably carrying
  ~4000 bytes and `COMRECIVESTATUS` still claimed a byte was pending. Feeding the ring
  **once per frame** (a batch of ~40 bytes, comfortably above the 32 the wire carries in a
  frame) makes the same run work. The COM routines share state with the receive ISR and are
  not built to be called at that rate. ⚠️ Eliminated as causes by experiment: it is not
  `COMRECIVESTATUS`, not draining in a tight loop (that survives), and not a missing
  `COMINIT`.
- ⛔ **`0x6D01` is NOT a plain "bytes waiting" count.** It is widely quoted as the RX ring
  count and games do read it, but dumped from a live run on the retail BIOS it reads `0x83` —
  there is a **flag bit** on top of the count. A drain loop bounded by that byte drains the
  wrong number, and after a ring overflow it was seen stuck at `0x00` with the head/tail
  bytes desynchronised, so the loop drained nothing at all while the cable worked fine.
  **Bound your drain on `COMGETDATA` returning `COM_BUF_EMPTY`**, never on that address.
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
- **SC0BUF is TWO registers on one address.** A write loads the TRANSMIT buffer; a read
  returns the RECEIVE buffer. The CPU **cannot** read back what it transmitted. An
  emulator that returns the received byte only while a "new data" flag is set, and then
  falls through to the I/O page, hands back **the last byte the game sent** — and the
  retail BIOS's COM ISR touches SC0BUF more than once per byte, so it stores that wrong
  byte in its ring. One corrupted byte fails a packet checksum, the peer silently drops
  the packet and never answers, and both consoles wait for each other for ever. It is
  phase-dependent, so the same game links on one attempt and hangs on the next.
- **Time a byte at 3200 CPU cycles**, and preferably *compute* it from `BR0CR` and
  `SC0MOD` (§2) rather than hardcoding it, so a cartridge that programs a different
  divider is not silently emulated at the wrong rate.
- **Do not assume a fixed exchange cadence.** How often a game drives the cable is a
  property of *that game's* link library in *that state*, not of the console. Measured
  on real cartridges: Samurai Shodown! 2 and The Last Blade exchange once every 2 frames
  when idle, while Fatal Fury drives the cable on **every** frame. There is no platform
  constant to conform to.
- **Do not** synchronise the two machines' clocks or frames — the real link is async.

---

## 10bis. ⚡ How fast a link REALLY goes — measured on two consoles

Counting bytes tells you very little; counting **byte times** tells you everything. At
19 200 bps a byte is 3200 CPU cycles, so a 128-frame window holds **4099 of them**. Express
every measurement that way and the platform's real behaviour falls out:

| what the software does | silicon |
|---|---|
| stream one way, as fast as the ring allows | **1.03 byte times per byte** — the wire is saturated |
| stream **both ways at once** | **3818 bytes**, against **3875** one way — 1.5 % apart |
| ping, wait for the echo, repeat (roles A/B) | **3.74 byte times per round trip** |
| the same, measured three times in a row | **1218 / 1220 / 1217** — 0.25 % apart |
| the CPU cost of one *received* byte | **≈ 96 µs** |

Three things a link programmer can act on:

1. ⚡ **The link is FULL DUPLEX.** Running both directions at once costs about 1.5 %
   against one direction alone (3818 bytes a window vs 3875) — near enough to free that you
   should not serialise a protocol out of caution.

   ⚠️ **An earlier version of this page claimed both-ways was FASTER (3963 vs 3818).** That
   3963 came from a run the probe ROM itself flagged `LINK OK - NOISY!` — its window opened
   while the wire was still carrying handshake traffic, so the count was inflated. Take the
   figure from the console whose banner reads plain `LINK OK`. **A measurement your own
   instrument disowns is not a measurement.**
2. ⛔ **A round trip costs ~3.7 byte times, not 2.** Only two of them are the wire; the rest
   is your software plus the BIOS COM layer turning around. **A lock-step design that waits
   for the peer's reply every frame pays this every frame** — that is the real budget, and
   it is nearly double the naive estimate.
3. **Receiving is not free.** Roughly **96 µs of CPU per byte arrives** — the interrupt, the
   BIOS handler and the ring. At 60 Hz that is ~0.6 % of a frame per byte; a hundred bytes a
   frame is over half of it. Budget reception, not just bandwidth.

⇒ If you need throughput, **stream** and let both directions run. If you need lock-step,
**one exchange per frame** is comfortable and three is not.

### ⛔ How to measure it WITHOUT fooling yourself

A round-trip probe must put one console in role A (pings) and the other in role B (echoes).
Leave **both on role A** and neither echoes — each mistakes the peer's ping for its own
reply, and the counter reports a number about twice as good that means nothing at all.

🔑 **The witness: role B's trip counter is 0 when the roles really were set, and non-zero
when both consoles stayed on role A.** Read it before quoting any figure. (Six runs were
photographed on role A before anyone noticed.)

✅ **And there is a second, stronger check**: role B also reports how many bytes it echoed,
which must equal role A's round trips summed. On a good run it matches to the unit —
`3655 = 1218 + 1220 + 1217`. Two consoles agreeing exactly is worth more than either
number on its own.

⚡ **Two consoles are extremely repeatable**: those three round-trip figures are 0.25 %
apart. If your own numbers move more than that between runs, the instability is in your
setup, not in the console.

## 11. Known gaps & what is *not* yet verified

None of these block a working link, but a programmer or emulator author should know
they are open:

- ✅ **Cable-detect polarity — MEASURED, 2026-08-19, two consoles and a cable.** The
  inference was right about the polarity and wrong about the meaning: `0x03` linked,
  `0x07` unlinked, and the bit follows the **peer's RTS**, not the connector. A console
  powered on at the BIOS reads as no peer. Full six-state table in §5. *(Gap closed.)*
- ✅ **The other `0xB0`–`0xBF` input bits — MEASURED with a cable and a live peer.** They
  do **not** move when a peer appears, so none of them is the detect line: `B6 = 0x05` and
  `BC = 0xFE` in every state, and `B3` reads `0x04` on one console and `0x07` on another —
  a **per-console constant**, identical with and without a cable. Only bit2 of `B3` is
  common to both machines. Also measured: `SC0CR` bit 7 is a **latch** (`0x00` until any
  byte has ever crossed, `0x80` for ever after, surviving an unplug), and `BR0CR` reads
  back with **bit 6 set whatever you write** (`0x05` → `0x45`, `0x15` → `0x55`). Only `0xB1` bit2 is modelled
  from the cable state so far. *Fatal Fury*'s detection writes `0xFF` to `0xBC` and reads
  it back (it passes today only because a naive core echoes writes) — a game relying on
  the *true* `B3`/`B6`/`BC` input behaviour could still misread. Open (fidelity, not
  blocking).
- **Block-transfer vectors `COMCREATEBUFDATA` (0x19) / `COMGETBUFDATA` (0x1A)**: BIOS
  entry addresses not individually verified; the `xhl3`=ptr / `rb3`=size ABI is from the
  manual, not re-confirmed by trace.
- ✅ **`BR0CR = 0x05 -> 19 200 bps` is now DERIVED, not just observed.** The divider ->
  bit-rate computation is written out in §2: `phi-T0 = fc/4`, `/5`, `/16` for UART, with
  `fc = 6.144 MHz` cross-checked from the video timing. It gives 19 200 bps and
  **3200 CPU cycles per byte** exactly. *(This gap is closed.)*
- ✅ **THE BYTE TIME IS NOW SILICON-MEASURED.** A frame-counting probe on two real
  consoles carried **3875 bytes in 128 frames** one-way — 2133 ms, i.e. **551 µs per byte**
  against the 520.83 µs derived above, the 6 % sitting on the slow side where the software
  feeding the ring can only put it. Reprogramming `BR0CR` to `0x15` gave **983 bytes against
  3963**, a ratio of **4.03** where the φT0 → φT2 step predicts exactly 4. ⇒ **19 200 bps and
  3200 cycles/byte are confirmed by measurement, not merely derived.** *(Gap closed.)*

  Historical note on how it used to read: nobody had
  print microseconds per byte. The expected answer is **520.83 us**. A probe ROM that
  also programs a different `BR0CR` and reports the ratio would settle the whole
  question in one run.
- ⚠️ **There is no such thing as "the" exchange cadence.** How often a game drives the
  cable is a property of that game's link library in that state — Samurai Shodown! 2 and
  The Last Blade idle at one exchange every 2 frames, Fatal Fury drives it every frame.
  Do not treat any single figure as a platform constant.
- **BIOS RX counter `0x6D01`** can lag in emulation (the ring still fills). Prefer the
  drain-until-`COM_BUF_EMPTY` idiom.
- **Online (`TcpLink`) initiator/responder sync** not re-tested since the cable-detect
  fix. It depends on the local cable-detect flag (armed by the link layer), so it
  *should* work, but the cross-host role rendezvous is untested.
- **`SC0CR` error flags** (framing / parity / overrun) are documented from the manual;
  emulation fidelity under real line conditions is unvalidated.
- **Role collision is no longer an open question**: see §8.2. Do not arbitrate roles by
  asking both players for the same button; elect the console that has been searching
  longest, at one frame of resolution — and **decide on a pair both consoles hold**, never
  on your live counter against their snapshot. That second half was measured wrong once
  (300 both-host sessions in 624 simulated pairings) and is what the echo field fixes.
- **Vector `0x0F`** (between `GEMODESET` and `COMINIT`) is a presumed reserved stub, not
  disassembled.

---

## See also

- [BIOS](../01_Hardware/BIOS.md) — the COM vectors alongside the rest of the BIOS.
- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — the full low-page I/O map.
- [Input](Input.md) — reading the controller (the initiator "push A" gate).
