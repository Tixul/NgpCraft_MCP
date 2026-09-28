# Storage and Saves

Flash save (standalone AMD stubs plus legacy BIOS/system.lib reference) and real-time clock for NGPC homebrew.

---


## 1. Memory Map — Storage Regions

```
0x004000 - 0x005FFF   Main RAM (8 KB)
0x006000 - 0x006BFF   Battery-backed RAM (3 KB) — lightweight saves (volatile, no erase)
0x007000 - 0x007FFF   Z80 audio RAM (4 KB, shared with sound CPU)
0x006F80 - 0x006FFF   BIOS system area (variables, ISR vectors)
0x200000 - 0x3FFFFF   Cartridge ROM (2 MB, FAR access only)
0x3FA000              Default flash save offset (block 0x21, within 8 KB block)
0x3FC000 - 0x3FFFFF   Reserved by system — do NOT use
```

**Key constraint:** the last 16 KB (`0x3FC000-0x3FFFFF`) is reserved by the system.
If your flash backend erases a 64 KB block, use block 0x21 (offset `0x1FA000`) — not
block 0x1F, which overlaps the reserved area.

---

## 2. Flash Save — ngpc_flash

### 2.1 API

```c
void ngpc_flash_init(void);              /* Call at startup */
void ngpc_flash_save(const void *data);  /* Write 256 bytes to flash */
void ngpc_flash_load(void *data);        /* Read 256 bytes from flash */
u8   ngpc_flash_exists(void);            /* Check if valid save exists (magic check) */
```

Flash has **limited write cycles**. Never save every frame.

### 2.2 Magic Number Requirement

`ngpc_flash_exists()` validates the first 4 bytes of the save area.
Your save struct **must** begin with `{ 0xCA, 0xFE, 0x20, 0x26 }`.

```c
typedef struct {
    u8 magic[4];   /* always { 0xCA, 0xFE, 0x20, 0x26 } */
    u8 version;
    u8 hp;
    u8 level;
    /* ... up to 251 more bytes */
} SaveData;
```

### 2.3 Full Save/Load Example

```c
void save_game(void)
{
    SaveData s;
    s.magic[0] = 0xCA; s.magic[1] = 0xFE;
    s.magic[2] = 0x20; s.magic[3] = 0x26;
    s.version  = 1;
    s.hp       = player.hp;
    s.level    = player.level;
    ngpc_flash_save(&s);
}

void load_game(void)
{
    if (ngpc_flash_exists()) {
        SaveData s;
        ngpc_flash_load(&s);
        if (s.version == 1) {
            player.hp    = s.hp;
            player.level = s.level;
        }
    } else {
        /* No valid save — initialize defaults in RAM only */
        player.hp    = 3;
        player.level = 1;
    }
}
```

---

## 3. Real-Time Clock — ngpc_rtc

### 3.1 API

```c
void ngpc_rtc_get(NgpcTime *t);                          /* Read date/time (BCD) */
void ngpc_rtc_set_alarm(NgpcAlarm *a);                   /* Alarm during gameplay */
void ngpc_rtc_set_wake(NgpcAlarm *a);                    /* Wake-up alarm (powers on) */
void ngpc_rtc_set_alarm_handler(void (*handler)(void));  /* Install alarm ISR */
```

### 3.2 BCD Encoding

The NGPC RTC uses **BCD encoding** for all time values: `0x12` = twelve (not 18).

```c
/* BCD helpers */
u8 bin = BCD_TO_BIN(bcd_value);   /* 0x23 -> 23 */
u8 bcd = BIN_TO_BCD(23);          /* 23  -> 0x23 */

/* Example: display current time */
NgpcTime t;
ngpc_rtc_get(&t);
u8 hour = BCD_TO_BIN(t.hour);
u8 min  = BCD_TO_BIN(t.minute);
```

---

## 4. Save Design Patterns

### 4.1 Recommended Save Struct Layout

`ngpc_flash_exists()` only validates the magic number — a partial or stale save may still
pass this check. Use full application-level validation:

```c
typedef struct {
    u8  magic[4];    /* { 0xCA, 0xFE, 0x20, 0x26 } */
    u8  version;     /* layout version — reject mismatches */
    /* game data */
    u8  options;
    u8  continues;
    /* high scores */
    u16 score_hi[10];
    u16 score_lo[10];
    u8  name[10][4]; /* 3 chars + null */
    /* validation */
    u8  checksum;
    u8  _pad[...];
} SaveData;
```

Full validation checklist on load:
1. Magic matches `{ 0xCA, 0xFE, 0x20, 0x26 }`
2. `version` matches current layout
3. Fields within valid bounds
4. Checksum passes

> **Put `checksum` at a fixed offset, before the terminal `_pad[]` — never last.**
> If the struct ends with `u8 _pad[SAVE_SIZE - N]; u8 checksum;`, cc900 may pad the
> whole struct to align `sizeof`, shifting the checksum's actual offset in the flash
> image away from `SAVE_SIZE-1`. With `checksum` ahead of the terminal padding, its
> offset is deterministic and the trailing `_pad[]` absorbs any compiler padding. This
> layout is validated on hardware in a shipped shmup profile struct.
>
> A simple self-referential checksum (computed over every byte except itself):
> ```c
> u8 sum = 0u;
> for (i = 0u; i < SAVE_SIZE - 1u; i++) sum ^= ((u8 *)&save)[i];
> save.checksum = sum ^ 0x5Au;
> ```
> Keep one save struct in RAM as the single source of truth; modify it in place,
> recompute the checksum, then `ngpc_flash_save(&save)`. This singleton + dirty-commit
> pattern avoids the scratch-buffer load/clear/rebuild cycles that invite corruption.

### 4.2 Append-Only Slot Pattern (Validated)

**Validated solution** — avoids erase-per-save and eliminates mid-session CLR_FLASH_RAM issues:

The validated configuration (confirmed on real hardware after extensive testing) uses
**16 slots × 512 bytes** (`SAVE_SIZE=512`, `rbc3=2`) inside the 8 KB block at offset `0x1FA000`:

```
Slot 0:  0x1FA000 .. 0x1FA1FF  (512 bytes)
Slot 1:  0x1FA200 .. 0x1FA3FF
...
Slot 15: 0x1FC000 .. 0x1FC1FF
```

> **Template default:** `ngpc_flash.h` ships with `SAVE_SIZE 256` (32 slots × 256 bytes,
> `rbc3=1`) as a lighter starting point. **Always change to `SAVE_SIZE 512` for production.**
> See §5.1 for why `rbc3=1` is unreliable on real hardware.

**Write:** find the first empty slot (first byte == `0xFF`), write there.
Never erase mid-session — only erase when the entire block is full. The template handles
erase via `ngpc_flash_erase_asm()` (standalone AMD stub, no `system.lib` required).
`CLR_FLASH_RAM` (system.lib) is the legacy alternative; it works on the first call per
session but silently fails if called a second time.

**Read at boot:** scan slots 15 -> 0, return the last slot with a valid magic.

```c
SaveData *ngpc_flash_find_valid_slot(void)
{
    for (s8 i = MAX_SLOTS - 1; i >= 0; i--) {
        SaveData *p = (SaveData *)(SAVE_OFFSET + i * SLOT_SIZE);
        if (p->magic[0] == 0xCA && p->magic[1] == 0xFE
         && p->magic[2] == 0x20 && p->magic[3] == 0x26)
            return p;
    }
    return NULL;  /* no valid save */
}
```

**Why this works:** each save appends to a new slot — no erase required until all 16 slots
are used. The flash erase only happens at that point, and `CLR_FLASH_RAM` is reliable
on first call. This avoids the corruption risk of erase-on-every-save.

> ⚠️ **The "erase when the block is full" rule above has a flaw that only shows late.** It
> puts the one dangerous operation of the system at a moment the game does not choose: the
> first fifteen saves are harmless, the sixteenth erases 8 KB with interrupts masked — in the
> middle of a transition, a menu, whatever is there. And a wrong block address is harmless
> for sixteen saves, then fatal. §4.2b is the design a finished game ended up with.

### 4.2b Two-block journal — the design that survives

Alternate the **two 8 KB blocks** reserved for saves (on a 16 Mbit cart: block 33 at
`0x1FA000` and block 32 at `0x1F8000`; the 16 KB block above is the system's — never used).
Records keep the 512-byte slot layout, plus a small trailer:

| offset | field |
|---|---|
| 248..251 | **sequence number**, 32-bit, compared modulo 2³² (wrap-safe) |
| 252..253 | **CRC16-CCITT** (init `0xFFFF`) over bytes 0..510, excluding 252..255 |
| 254..255 | **complement** of the CRC |
| 511 | **end marker**, zero, programmed LAST |

Rules, each one a lesson:

* **Load** = scan both blocks, keep the most recent record whose CRC, complement and end
  marker are valid. A write interrupted by a power cut, a corrupted slot, a partially erased
  block are all simply skipped — the previous record is still there.
* **Never erase the block that holds the last valid record.** When the active block is full,
  erase the OTHER one, then **read back all 8 192 bytes as `0xFF`** before writing into it.
* **A slot is empty only if its 512 bytes are all `0xFF`** — not its first byte. A write that
  died after the first byte leaves a slot that *looks* free and is not; programming over it
  cannot raise a cell back to 1 and the chip reports nothing (§5.0c) — measured: the screen
  froze 70 frames writing into a half-written slot, 3 frames into a clean one. Skip such a
  slot, never "repair" it in place.
* **Derive the block address from the cartridge size** (§5.0); an unknown size or a ROM that
  overlaps the save blocks forbids every flash operation — visibly.
* **A failed save stays pending.** Keep the RAM state dirty, show `NOT SAVED - RETRY`, and
  retry at the next screen change. A "reset save" writes a NEW default record through the
  journal; it never erases the only copy of the profile before writing its replacement.
* **Keep old layouts readable**: an older record (its version in its own field, its older
  checksum rule) is accepted and migrated in RAM without moving existing offsets. Compatibility
  is upward only: an older ROM cannot read the new trailer.
* Test it on the emulator with the ROM's own functions: 80 successive saves, reboots at the
  block boundaries, simulated power cuts in the middle of a write, corrupted records,
  partially erased inactive block, sequence counter wrapping `0xFFFFFFFF → 0`, unknown
  cartridge refused with the flash unchanged. Then on the console — the emulator does not
  prove electrical behaviour or power-cut resistance.

**Why the rework was needed:** the previous driver stopped writing after the 16 slots of its
single block (it no longer erased automatically — see below), and rebooting did not empty the
block. RAM progress could advance for hours and come back to the same chapter at power-on.

**Rule behind it: an erase is requested, never caught.** A single-block design must then
refuse to save when the block is full (and SAY so, by re-reading the block, not from a
remembered flag); a two-block journal instead erases the inactive block at a moment it
controls, under a curtain, never the one holding the data.

### 4.3 When to Save

| Event | Save? |
|-------|-------|
| Boot / startup | **No** — load defaults into RAM only |
| Every frame | **Never** — flash write cycles are finite |
| Button press in options | **No** — mark dirty in RAM only |
| Leave options screen | Yes — flush dirty RAM to flash |
| High score validated | Yes — explicit commit |
| Save point (RPG/platformer) | Yes — explicit commit |

> "No crash" does not mean "valid save." To confirm a flash backend works:
> 1. Boot normally
> 2. Trigger a save
> 3. Power off
> 4. Power on
> 5. Verify the magic is present and data is correct

### 4.3b Mark dirty on every screen, write once when the screen CHANGES

A shop screen that wrote one slot per A press (84 possible purchases for a 16-slot block)
filled the block before the fifteenth part. Now every screen only MARKS; the menu loop writes
once, when the screen changes: same purchases, **4 slots → 0 during the purchases, 1 on
leaving**. ⛔ First attempt flushed on *every frame*: the flag dropped on the next frame, so
every press still wrote — the very defect it was meant to remove.

And **do not lower the dirty flag without reading back**: until the verify passes, the save
stays pending and the next screen change retries on a fresh slot (§5.0b).

⛔ **A "restore defaults" at START can erase what the player just typed.** A title screen
where cheat codes are entered, followed by a `save_defaults()` on START (redundant: boot had
already set the defaults), wiped every code on a blank cartridge — the word appeared, the
sound played, the effect vanished. Never reset the RAM profile on a path the player takes
after acting.

### 4.4 Default Initialization at Boot

```c
void save_init(void)
{
    if (ngpc_flash_exists()) {
        ngpc_flash_load(&g_save);
        if (!save_validate()) {
            save_reset_defaults();
        }
    } else {
        save_reset_defaults();
    }
    /* Never write to flash here — RAM-only initialization */
}

void save_reset_defaults(void)
{
    g_save.magic[0] = 0xCA; g_save.magic[1] = 0xFE;
    g_save.magic[2] = 0x20; g_save.magic[3] = 0x26;
    g_save.version    = 1;
    g_save.continues  = 3;
    for (u8 i = 0; i < 10; i++) {
        g_save.score[i] = 0;
        memcpy(g_save.name[i], "---", 4);
    }
}
```

> Writing to flash at boot risks powering off the console on real hardware
> if the write triggers the watchdog. Always initialize RAM defaults only.

### 4.5 High Score Integration

```c
u8 is_high_score(u32 score)
{
    return score > g_save.score[9];  /* beats 10th place */
}

void insert_high_score(u32 score, const char *name_3)
{
    /* Find insertion point */
    u8 pos = 9;
    while (pos > 0 && score > g_save.score[pos - 1])
        pos--;

    /* Shift down */
    for (u8 i = 9; i > pos; i--) {
        g_save.score[i] = g_save.score[i - 1];
        memcpy(g_save.name[i], g_save.name[i - 1], 4);
    }

    /* Insert */
    g_save.score[pos] = score;
    memcpy(g_save.name[pos], name_3, 3);
    g_save.name[pos][3] = 0;

    /* Commit */
    ngpc_flash_save(&g_save);
}
```

Design note: whether scores from continued runs count toward the top 10 is a game-design
decision, not a technical one.

---

### 4.6 The save buffer is free RAM — and it has a layout to respect

The 512-byte RAM copy of the save costs 512 bytes whatever it contains: **a new FIELD in it is
free, a new `static` is not**. A finished game kept there, besides the profile: a language
choice, HUD preferences, a ghost-car table (8 bytes × 16 tracks), and — in the unused tail —
transient state that never needs flash: a HUD cache at +256, a 12-car race state at +384, a
ghost reader at +448. Each zone is guarded by a compile-time assert on its size and offset
(see [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) §8.5c), and the checksum excludes the journal
trailer and the transient zones.

Two traps that came with it:

* **Offsets are not the sum of sizes.** cc900 rounds array sizes to even (§8.1d of Build
  Toolchain) and aligns; a field planned at +54 was at +56. Find fields by **diffing RAM**
  before and after an action that changes them (entering a code, unlocking something).
* **Bump the layout version** whenever a field moves — and also when the structure does NOT
  move but its meaning does (best times set at a different game pace or on a different track
  length are not comparable). Keep a migration from older versions if players already have
  saves.

**The magic is the driver's, not the game's.** A driver that recognises a slot only by
`CA FE 20 26` does not see a game that writes its own four letters: 16 slots filled, none
recognised, `exists()` returns 0 at every boot (the game restarts blank while seeming to
save) and the block fills until an erase. Put the game's layout version in its own field,
after the magic.

## 5. Flash Hardware Details

### 5.0 Save geometry is per-cartridge-size — get it wrong and you erase your own ROM

The `0x21` / `0x1FA000` pair below is correct **for a 16 Mbit cartridge**. Smaller
cartridges need a different block, and **the same block number means a different address
on each size**. Getting it wrong does not mean "the save fails" — it means **erasing
somewhere else**, and on a small ROM that somewhere else is your own code.

Manufacturer block plan (SDK `FlashMem.txt`, all three sizes): 64 KB blocks up to the
top, with the last 64 KB split **32 / 8 / 8 / 16**. The two 8 KB blocks are where every
game's save lives; the final 16 KB block is reserved for the system program. The save
block conventionally used is the **second 8 KB block from the top** — exactly `0x6000`
below the chip top on all three sizes:

| Cartridge | Save block | Offset | CPU address |
|---|---|---|---|
| 4 Mbit (512 KB) | 9 (`0x09`) | `0x07A000` | `0x27A000` |
| 8 Mbit (1 MB) | 17 (`0x11`) | `0x0FA000` | `0x2FA000` |
| 16 Mbit (2 MB) | 33 (`0x21`) | `0x1FA000` | `0x3FA000` |

*Contributed by [Napsterix](https://github.com/Napsterix), corroborated by two
independent sources: freeplaytech forum reports, and reverse-engineering of the BIOS
flash routine against the SDK block map.*

#### Detect the cartridge size at runtime — do not hardcode it

The BIOS answers this question at power-on and stores the answer at **`0x6C58`** in its
own work RAM:

| `0x6C58` | cartridge |
|---|---|
| `0` | no cartridge |
| `1` | 4 Mbit |
| `2` | 8 Mbit |
| `3` | 16 Mbit |

The BIOS flash routine reads **this same byte** before touching anything (and returns
error `0xFF` if it is zero), so reading it yourself is consistent by construction rather
than a second opinion. `0x6C59` is the same thing for CS1 — the development board's slot,
which is empty on a production console.

An **unknown value must mean "do not save"**, not a guessed default: failing to save is
recoverable, erasing the wrong block is not.

Alternative without the BIOS — CFI/autoselect query of the flash chip itself. After the
`AA @ 5555` / `55 @ 2AAA` / `90 @ 5555` unlock the chip answers `0x98` (Toshiba) then a
**device ID that names the size**: `0xAB` = 4 Mbit, `0x2C` = 8 Mbit, `0x2F` = 16 Mbit.
`F0` returns the chip to being memory.

#### ⚠️ 32 Mbit Flash Masta cartridges — saves reported broken below 16 Mbit

> On 32 Mbit Flash Masta cartridges the save function is reported broken **for all games
> under 16 Mbit**: those games write to block `0x11`, which on that cartridge lies inside
> the game's own address space. The community fix repatches affected commercial games
> from block `0x11` to `0x21` (Cotton, Biomotor Unitron and others).
>
> Note that block `0x11` is exactly the *correct* 8 Mbit block from the table above — so
> a correctly implemented small homebrew game is precisely the affected class.
>
> **OPEN — not verified:** what `0x6C58` reports on a 32 Mbit Flash Masta holding a small
> ROM. If it reports `2`, a correct implementation writes to `0x0FA000` and hits the
> broken block. Until someone checks on the device, **read `0x6C58` on your target
> cartridge before the first save** — the first save attempt is also the test, and the
> stake is your own ROM image.

*Reported by [Napsterix](https://github.com/Napsterix); source: freeplaytech forum, Flash
Masta section, tid=92. Consequence marked OPEN deliberately — do not promote it to fact
without an on-device check.*

### 5.0b Verify the write, and verify the machine survived

**"BIOS returned `SYS_SUCCESS`" and "the bytes are in the cartridge" are two different
claims.** Always read the record back and validate it. Anything that does not verify must
count as "no save data" — a half-written record is worse than none, because it looks like
a working one.

**Check that the machine is still running afterwards.** A wrong unit count made the BIOS
write routine program the requested bytes correctly and then **loop forever** with
interrupts masked by the `swi` — a dead console with a perfect-looking save record in the
cartridge. A save-comparison test passed; only a run-on test (does the game reach the next
screen?) caught it. A VBlank counter that stops incrementing is the tell.

> **Why `swi 1` masking interrupts is not a side effect but the point:** the erase takes
> ~57 ms (§5.0c) and the game's own ISR lives in the same cartridge. If an interrupt could fire, the
> CPU would try to fetch instructions from a chip that is in erase mode. Reset the
> watchdog before each call.

*Contributed by [Napsterix](https://github.com/Napsterix).*

### 5.0c How long the chip is busy — measured, not assumed

A program or an erase takes **real time**, and for its whole duration the chip **answers
status instead of contents** to every read of the cart window — an instruction fetch
included. That window is the reason the stub is copied to RAM and run with interrupts
masked. Everything in §5.0b follows from it.

Measured on a 16 Mbit cartridge (`hw_test_flash_timing`): the shipped AMD stubs already
count their own status-poll turns in `XIY` and still hold that count on return, and the
same loop is timed against `RAS.V`, the scanline counter, which is indifferent to the
interrupt mask.

| operation | time |
|---|---|
| erase, 8 KB block | **57.5 ms** — four measurements, spread 53.0 to 62.5 |
| program, one byte | **33 µs** |
| erase, 64 KB block | **441 ms** — 7.67x the 8 KB one, so scaling is close to linear |

⚠️ **The erase is not a constant.** 18 % between measurements, twice inside a single run.
Design against the spread, never against the mean.

⚠️ **The margin under the watchdog is a factor of two, not an order of magnitude.** The
~100 ms watchdog is why the 8 KB block is the one to use — but 57.5 ms fits with far less
room than the "5–15 ms" that older notes claimed. A 64 KB block does not fit at all, which
is what took the console down in the first hardware trials.

⛔ **A program that CANNOT succeed never reports failure.** Asking a NOR cell to go back up
— a slot reprogrammed without an erase first — draws **no DQ5**: verified with eight times
a driver's normal timeout, about **18 seconds** of polling, and the chip says nothing. The
AMD datasheets describe a DQ5 timeout; this part does not do it. Three consequences:

- **your poll loop's own iteration ceiling is the only way out. It must have one.**
- those seconds pass with interrupts masked — that is a dead console, not a failed save.
- the corruption is silent: the cell ends up holding `old AND new`.

⛔ **The reset command (`F0`) only reaches a chip that has stopped.** It brings back one
stuck on an impossible program; sent to a chip in the middle of a real erase it is
**ignored**, the chip stays busy, and the next instruction fetch reads status bits. Wait
the operation out, *then* reset.

> **For emulator authors.** A flash model that commits the byte inside the bus cycle
> carrying the command has no busy window at all, so a driver's status poll exits on its
> first turn and none of the above can happen. Every save bug in this section then looks
> like a working save. The single unambiguous signature to instrument is **an instruction
> fetched from a chip that is programming or erasing**.

### 5.0d A console that powers off may be OBEYING

After the address and the erase policy were fixed, a console still switched off during an
ordinary save (2 slots of 16 used — no erase involved). **An NGPC that powers off is not
crashing: it obeys.** `0x6F85` (user shutdown request) is a bit field set by the system:

| bit | request |
|---|---|
| 7 | power switch |
| 6 | long inactivity (ten minutes) |
| **5** | **main battery voltage too low** |

**A flash write is the largest current spike the cartridge produces.** On tired batteries it
is the moment the measured voltage crosses the threshold, and a game that honours the request
shuts down. It looks exactly like a software crash — intermittent, tied to saves, more
frequent late in a session — and **no emulator can reproduce it** (none models a battery).
Before hunting a bug: read `0x6F80` (battery voltage, 0..`0x3FF`) and show it somewhere;
before shutting down, show which bit fired (a few seconds of a plain colour — the window
closed to zero paints the whole screen from any screen); retry with fresh batteries.

⛔ **There is no power bit in the joypad byte.** `0x6F82` bit 7 is button D of an external
controller (bit 6 is OPTION, also button C of that controller) — the power switch is only
readable through `0x6F85`. A fallback "if bit 7 is held 30 frames, shut down" switched the
console off whenever that bit read 1 ([Input](Input.md) §4.3).

⚠️ **An emulator counter of "instruction fetched from a busy chip" reading 0 means "not this
time", not "safe".** With interrupts left enabled around a program (a `di` removed on
purpose, bytes checked), the counter still said 0: at real timing the busy window is ~200
cycles per byte and an interrupt rarely lands in it; at an exaggerated timing the fault
appears at the first try. A missing `di` around the flash stub crashes the console and is
invisible in normal emulation.

⚠️ **Writing through the BIOS call vs the stub.** The one hardware-validated implementation
writes through `VECT_FLASHWRITE` (`swi 1`), clearing the watchdog (`ld (0x6F),0x4E`) right
before and right after, and lets the system drive the bus; the erase keeps the RAM stub
(`VECT_FLASHERS` is broken on the save blocks). A stub that DISABLES the watchdog and writes
the clear code while it is off sends that code when it is not recognised.

### 5.1 Confirmed BIOS Parameters

Parameters confirmed by cross-analysis of multiple working NGPC homebrews with
persistent saves on real hardware:

```c
#define SAVE_BLOCK    0x21       /* NOT 0x1F — overlaps reserved area      */
#define SAVE_OFFSET   0x1FA000   /* CPU address: 0x200000 + 0x1FA000 = 0x3FA000 */
#define SAVE_SIZE     512        /* rbc3=2 — validated on real hardware     */
```

> **`BC=1` (256 bytes) is unreliable on real hardware** — writes complete without error
> but data may not persist after power-off. Always use `BC=2` (512 bytes) in production.
> The template ships with `SAVE_SIZE 256` as a configurable starting point;
> change it to `512` before shipping any game.

BIOS vector IDs used:

| Vector | ID | Operation |
|--------|----|-----------|
| `VECT_FLASHERS` | `rw3=8` | Erase block |
| `VECT_FLASHWRITE` | `rw3=6` | Write N×256 bytes |

### 5.2 Raw ASM Sequence (Erase + Write)

The template uses **standalone AMD stubs** — no `system.lib` needed.
The stubs are position-independent byte sequences (115 bytes write, 98 bytes erase) extracted
by disassembly from a hardware-validated ROM. They are copied to RAM at `0x6E00` and executed
from there (a flash chip cannot execute code while being programmed).

**What the prologue really does — I/O `0x6E` is WDMOD and `0x6F` is WDCR (the watchdog).**
Toshiba's register header for this CPU core (`IO900H.H`) names `0x6E` **WDMOD** (watchdog
mode) and `0x6F` **WDCR** (watchdog control: `0x4E` = clear, `0xB1` = disable code). The pair
`ld (0x6E),0x14` / `ld (0x6F),0xB1` is the documented two-step **watchdog disable** (clear the
enable bit in WDMOD, then write the disable code), and `ld (0x6E),0xF0` re-enables it with a
fixed mode. Older notes and template comments described `0x6E` as a cartridge `/WE` control;
no primary source supports that, and headers for other chips of the family (`IO900.H`,
`IO900L.H`, WDMOD at `0x5C`) do not apply to this machine. The sequence works — read it as a
watchdog sequence, and follow the rules of §5.2b around it.

**Standalone sequence (template default):**

```asm
; --- Common prologue ---
ld  (0x6E), 0x14        ; WDMOD: clear the watchdog enable (disable, step 1)
ld  (0x6F), 0xB1        ; WDCR: disable code (step 2) -- clear with 0x4E FIRST, see 5.2b

; --- Erase block 33 (F16_B33, 8 KB, abs 0x3FA000) ---
ld  xde, 0x6E00         ; destination: RAM
ld  xhl, _erase_stub    ; source: 98-byte AMD erase sequence in ROM
ld  bc,  98
; ldir — copy stub to RAM (stubs cannot run from the flash chip itself)
ld  xix, 0x200000       ; AMD unlock base = CS0
ld  xiy, 0              ; XIY = 0 (erase stub parameter)
ld  a,   0              ; A   = 0 (erase stub parameter)
ld  xde, 0x3FA000       ; block address (absolute)
call 0x6E00             ; execute stub from RAM
ld  (0x6F), 0x4E        ; restore watchdog
ld  (0x6E), 0xF0        ; WDMOD: watchdog re-enabled (better: restore the SAVED mode)

; --- Write 512 bytes ---
ld  xhl, (xsp+4)        ; source pointer (from C stack, bank-0 — no bank-3 promotion needed)
ld  xde, (xsp+8)        ; relative offset (e.g. 0x1FA000 + slot*512)
ld  xix, 0x200000
add xde, xix            ; make destination absolute (0x3FA000 + slot*512)
; [push xde / push xhl — preserve across ldir copy]
ld  xde, 0x6E00
ld  xhl, _write_stub    ; source: 115-byte AMD write sequence in ROM
ld  bc,  115
; ldir — copy stub to RAM
; [pop xhl / pop xde — restore src and dest]
ld  bc,  0x0002         ; 2 pages × 256 = 512 bytes
call 0x6E00
ld  (0x6F), 0x4E
ld  (0x6E), 0xF0
```

**Legacy sequence (system.lib path, kept as reference):**

```asm
; Erase block 0x21 via CLR_FLASH_RAM (system.lib)
ld ra3,   0
ld rb3,   0x21
ld (rWDCR), 0x4E
calr CLR_FLASH_RAM      ; reliable on first call only per session

; Write 512 bytes via WRITE_FLASH_RAM (system.lib)
ld ra3,   0
ld rbc3,  2             ; 2 * 256 = 512 bytes
ld xhl,   (xsp+4)
ld xhl3,  xhl           ; bank-3 promotion required for BIOS path
ld xde,   (xsp+8)
ld xde3,  xde
ld (rWDCR), 0x4E
calr WRITE_FLASH_RAM
```

### 5.2b Rules around the stub: interrupts, watchdog, status

The byte sequences of the stubs are fine; what goes wrong is the code around them. Six rules,
each one met in a shipped homebrew:

1. **Interrupts stay masked for the whole erase/program, and the caller's state is restored.**
   Running the stub from RAM does not protect the VBlank ISR, which lives in the cartridge: an
   interrupt during the busy window fetches instructions from a chip that answers status. SNK's
   system-call documentation prohibits interrupts during flash operations (the `swi 1` path
   masks them). Wrap as `push sr / di / … / pop sr` — **not** a closing `ei`, which forces
   interrupts on even when the caller had them off.
2. **Watchdog (`0x6E` WDMOD, `0x6F` WDCR): clear it first, then change the mode.** Write `0x4E`
   to `0x6F`, save WDMOD, disable (`0x14` → `0x6E`, `0xB1` → `0x6F`), do the operation, restore
   the SAVED mode, clear again. Changing the mode first makes the outcome depend on how long
   ago the last VBlank cleared the counter.
3. **Return the stub's status to C and act on it.** An erase that failed must not be followed by
   a write into "slot 0". Verify all 8 KB read `0xFF` after an erase and the 512 bytes after a
   program.
4. **A slot is free only if its 512 bytes are `0xFF`** (§4.2b).
5. **An unknown capacity means no save** (§5.0). Booting an emulator on a small, unpadded image
   typically leaves `0x6C58` at 0: a "default to 16 Mbit" fallback then fails silently while
   the game carries on.
6. **"The game continued" is not "the save happened".** Re-read after a power cycle (or in a
   fresh emulator instance). Most emulators program instantly and do not model the watchdog,
   so none of rules 1–2 can be reproduced there — see
   [Measuring Performance](Measuring-Performance.md) §8.1 for a console test protocol.

### 5.3 cc900 Inline ASM Pointer Rules

**Standalone path** (no bank-3 promotion needed):

```asm
; Load from C stack — stays in bank 0, no ld xhlN,xhl required
ld xhl, (xsp+4)         ; source pointer (first C arg)
ld xde, (xsp+8)         ; flash offset  (second C arg)
ld xix, 0x200000
add xde, xix            ; offset -> absolute address
```

**Legacy system.lib path** (bank-3 promotion required by BIOS convention):

```c
/* CORRECT — load argument from stack, copy to bank 3 */
__asm("ld xhl, (xsp+4)");   /* first arg = source pointer */
__asm("ld xhl3, xhl");      /* promote to bank 3 for BIOS */

/* INCORRECT — cc900/asm900 cannot see C symbols from inline ASM */
/* __asm("ld xhl, (my_c_symbol)"); */  /* Error-221: Undefined symbol */
```

Keep the flash function **minimal** — no local variables, no C statements other than
a guard `(void)data`, to ensure the stack prologue stays predictable for the `xsp+4` offset.

### 5.4 Known Flash Pitfalls

| Pitfall | Consequence | Fix |
|---------|-------------|-----|
| Write at boot | Power-off on real hardware | Load defaults to RAM only at boot |
| `BC=1` (256 bytes) | Unreliable on real hardware — data may not persist after power-off | Use `BC=2` (512 bytes, `SAVE_SIZE=512`) |
| Block 0x1F | Overlaps system reserved area | Use block 0x21 |
| Erase on every save | Flash wear, risks mid-session failure | Append-only slots |
| "No crash" = success | False | Validate with full power-cycle test |
| Watchdog left running across an erase | ~57 ms with interrupts masked and no VBlank to clear it | Clear (`0x4E`→`0x6F`), save WDMOD, disable (`0x14`→`0x6E`, `0xB1`→`0x6F`), restore the saved mode after (§5.2b) |
| Flash wrapper without `di`, or ending with `ei` | An interrupt fetches the ROM ISR from the busy chip; `ei` turns interrupts on for a caller that had them off | `push sr / di / … / pop sr` (§5.2b) |
| Stub status ignored | A failed erase is followed by a write into slot 0 | Return the status; verify 8 KB `0xFF` after erase, 512 bytes after write |
| Executing stub from flash | Undefined behavior (chip busy during program) | Copy stub to RAM at `0x6E00`, execute from there |
| Polling a program with no iteration ceiling | **Hangs forever** — an impossible write never raises DQ5 (§5.0c) | Bound the poll loop yourself; DQ5 is not enough |
| Sending `F0` to a chip mid-erase | Ignored; the chip stays busy and the next fetch reads status | Wait the operation out, then reset |
| `CLR_FLASH_RAM` second call | Silently fails (BIOS internal bug) — legacy path only | Erase only when block full; standalone stubs are not affected |
| Saving while a raster/Timer0 ISR is active | The split ISR runs during the operation (it may clear or reprogram the watchdog `0x6F`/`0x6E` the stub has just disabled, and it fetches from the busy chip) -> corruption | Disable the split timer (e.g. `hud_raster_disable()`) **before** `ngpc_flash_save()`, or save only from a state where raster is already off |
| Checksum field placed last (after terminal padding) | cc900 may pad the whole struct -> the checksum's flash offset is no longer `SAVE_SIZE-1` -> validation drifts | Put `checksum` at a fixed offset **before** the terminal `_pad[]` (see §4.1) |
| Automatic erase when the single block is full | The 8 KB erase happens at a moment the game did not choose; a wrong block address is harmless for 16 saves, then fatal | Two-block journal (§4.2b), or refuse to save and say so |
| Slot judged empty on its first byte | Writing into a half-programmed slot: impossible program, long freeze, `old AND new` data | Empty = all 512 bytes `0xFF`; skip a started slot, never repair it |
| Game writes its own magic | Driver recognises no slot: boots blank, block fills | Driver magic first, game layout version in its own field |
| One write per button press | Block full within minutes of a shop screen | Mark dirty; write once when the screen changes (§4.3b) |
| Console powers off during saves only | Low-battery shutdown request (`0x6F85` bit 5) on the write's current spike | Show `0x6F80` and the request bit before shutting down; fresh batteries (§5.0d) |

---

## Quick Reference

| Item | Value / Pattern | Notes |
|------|-----------------|-------|
| Flash save offset | `0x1FA000` | CPU address: `0x3FA000` |
| Flash block | `0x21` | NOT `0x1F` — reserved area overlap |
| Write size | `BC=2` = 512 bytes | `BC=1` (256 B) unreliable on real hardware — use 512 in production |
| VECT_FLASHERS | `rw3=8` | Erase (broken for blocks 32-34 — use standalone stub) |
| VECT_FLASHWRITE | `rw3=6` | Write (replaced by standalone write stub in template) |
| Magic bytes | `{ 0xCA, 0xFE, 0x20, 0x26 }` | First 4 bytes of save struct |
| Validation | magic + version + bounds + checksum | Magic alone insufficient |
| Save slots | 16 slots × 512 bytes in 8 KB block | Append-only pattern |
| Slot scan at boot | Slots 15 -> 0, last valid = current | |
| Erase policy | **Never automatic** on the block holding the data | Two-block journal erases the INACTIVE block, then verifies 8 KB of `0xFF` (§4.2b); single block: refuse to save when full |
| Save trigger | Options exit / high score commit | Never at boot, never per-frame |
| Boot init | Load to RAM only | Flash write at boot = power-off risk |
| RTC encoding | BCD | `BCD_TO_BIN()` / `BIN_TO_BCD()` |
| Battery-backed RAM | `0x006000-0x006BFF` (3 KB) | No erase needed, volatile (no battery on cart) |

---

## See Also

- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — Full memory map, ROM base address, BIOS area
- [BIOS](../01_Hardware/BIOS.md) — BIOS SWI vectors, system.lib functions (legacy reference)
- [Game Loop](Game-Loop.md) — Watchdog rules, VBlank ISR constraints
- [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — ROM layout, linker sections, flash offset in 2 MB cartridge
