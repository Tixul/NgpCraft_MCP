# DMA

MicroDMA hardware facts, safe usage rules, copy-paste examples, debug diagnostics, raster-effect integration lessons, and the underlying TLCS-900/H inline-ASM sequences.

> Note: All content uses ASCII only (avoids encoding issues on Windows/PowerShell).

---


## 0. TL;DR — Key Rules

- NGPC MicroDMA = "table in RAM -> fixed hardware register", triggered by a start vector (e.g. Timer0/HBlank).
- MicroDMA is **one-shot**: when `DMACn` reaches 0, the hardware generates INTTCn and **auto-clears `DMAxV=0`**.
  => you must **re-arm** (reprogram `DMAS/DMAC`, then re-write `DMAxV`) to replay each frame.
- **Never use VBlank (`0x0B`) as MicroDMA start vector by default**: on hardware, MicroDMA can consume the
  VBlank request and prevent the CPU VBlank ISR (watchdog) → power-off.
- For 2 parallel streams per line, avoid using the same start vector on 2 channels:
  - use Timer0 (`0x10`) + Timer1 (`0x11`),
  - or use a single channel in **word** mode (`0x09`) to write X+Y together.
- If you rebuild the table while MicroDMA is reading it: use a ping-pong buffer, swap in VBlank.
- Advanced option: auto-rearm inside INTTCn slot (`0x6FF0..0x6FFC`) to close the window
  where `DMAxV` is auto-cleared and the source timer might fall back to CPU interrupt.

---

## 1. MicroDMA Hardware Facts

Source: SNK MicroDMA Reference Manual (rel 0.2, 1998-08-28) and NGPC hardware spec.

### 1.1 Registers per Channel

Each channel n (0..3) has:
- `DMASn` — source address (32-bit)
- `DMADn` — destination address (32-bit)
- `DMACn` — transfer count (16-bit)
- `DMAMn` — mode (8-bit)
- `DMAxV` — start vector register (5 useful bits) — write-only (no read-modify-write).

### 1.2 One-Shot + Auto-Clear of DMAxV

SNK doc summary:
- Each trigger transfers 1 element according to `DMAMn`, decrements `DMACn`.
- When `DMACn` reaches 0:
  - an "end microDMA" interrupt is generated: **INTTCn**.
  - `DMAxV` is **automatically cleared to 0** (start is disabled until `DMAxV` is re-written).

Practical conclusion:
- A per-frame table must be re-armed (in VBlank or in the INTTCn handler).

### 1.3 Priority + MicroDMA Chain

SNK doc:
- If multiple channels are requested simultaneously, CH0 has priority, then CH1, etc.
- If 2 channels configure **the same start vector**, the hardware executes them in **chain**:
  - while the lower-numbered channel has not finished its table (`DMACn`), the other waits.

Practical conclusion:
- Two channels on Timer0=0x10 do not truly run in parallel: risk of starvation on the higher channel.

---

## 2. NGPC Start Vectors and Interrupts

### 2.1 Useful Start Vectors

From the NGPC hardware spec (column "MICRO DMA START VECTOR"):

| Value | Source |
|-------|--------|
| `0x0A` | RTC alarm |
| `0x0B` | VBlank (**avoid — see safe rules**) |
| `0x0C` | Z80 |
| `0x10` | Timer0 (8-bit timer 0) — typically HBlank (TI0) |
| `0x11` | Timer1 |
| `0x12` | Timer2 |
| `0x13` | Timer3 |
| `0x18` | Serial TX |
| `0x19` | Serial RX |

### 2.2 INTTCn Slots (End MicroDMA) — RAM Pointers

32-bit function pointers stored in RAM:

| Address | Constant | Description |
|---------|----------|-------------|
| `0x6FF0` | `HW_INT_DMA0` | End MicroDMA interrupt — channel 0 |
| `0x6FF4` | `HW_INT_DMA1` | End MicroDMA interrupt — channel 1 |
| `0x6FF8` | `HW_INT_DMA2` | End MicroDMA interrupt — channel 2 |
| `0x6FFC` | `HW_INT_DMA3` | End MicroDMA interrupt — channel 3 |

### 2.3 Raster Position for Safe Start

```c
0x8008  /* RAS.H — horizontal position (time remaining on current line) */
0x8009  /* RAS.V — vertical position (current scanline number) */
```

`RAS.V >= 0x98` (152) means VBlank (after the 152 visible lines).
Wait for this condition before (re)starting a timer to avoid a misaligned table start.

---

## 3. DMAM Modes — Beware Octal

The SDK header `HDMA.H` defines mode constants in **octal** (`010`, `011`, `012`...),
which correspond in hex to:

| Hex | SDK name | Description |
|-----|----------|-------------|
| `0x08` | `M_SINC1` | mem → I/O, byte, source++ |
| `0x09` | `M_SINC2` | mem → I/O, word, source++ |
| `0x0A` | `M_SINC4` | mem → I/O, dword, source++ |

The template exposes all three modes (u8/u16/u32) to match the official SNK pattern.

**Important — endianness in practice:**
A word write to `0x8034` writes 2 consecutive bytes:
- low byte → `0x8034` (`SCR2_OFS_X`)
- high byte → `0x8035` (`SCR2_OFS_Y`)

Therefore a "packed" `u16` entry must be `(Y << 8) | X`.

---

## 4. Reverse-Engineering Lessons

This section documents reusable MicroDMA patterns observed in commercial titles, at reference level.

### 4.1 Pattern A — Per-Scanline Scroll (DMA0, word, SCR2)

The core raster scroll effect: Timer0 fires once per HBlank, DMA0 copies the next word
from a per-line table into SCR2_OFS_X/Y (0x8034).

**Init** (`0x0001AD5B`):
```asm
and  (TRUN),0x7E          ; stop Timer0
ld   (T01MOD),0           ; T01MOD = 0
ld   (TREG0),1            ; 1 timer tick per HBlank line
ldc  DMAS_0, 0x62E0       ; source = scroll table base
ldc  DMAD_0, 0x8034       ; dest  = SCR2_OFS_X (word covers OFS_X + OFS_Y)
ldc  DMAM_0, 0x09         ; M_SINC2 (word, mem->I/O)
or   (TRUN),0x81          ; start Timer0 + prescaler
ld   (0x6FF0), 0x1ADBC    ; DMA0_INT = ISR fin DMA0 (re-arm handler)
```

**Arm per-frame** (`0x0001ADA6` — also called from VBL callback):
```asm
ld   XWA, 0x62E0
add  WA,(0x6548)          ; + double-buffer offset (0 or 0x134)
ldc  DMAS_0, XWA
ld   WA, 0x98             ; 152 = screen height
ldc  DMAC_0, WA
ld   (0x007C), 0x10       ; DMA0V = 0x10 (trigger = Timer0)
```

**Auto-rearm ISR** (`0x0001ADBC` — installed at DMA0_INT = 0x6FF0):
```asm
push XWA
; same 3 ldc writes as arm above (DMAS_0, DMAC_0, DMA0V)
pop  XWA
reti
```

Key numbers:
| Parameter | Value | Meaning |
|-----------|-------|---------|
| `DMAM_0` | `0x09` | word mode (M_SINC2) |
| `DMAC_0` | `0x98` = 152 | one transfer per scanline |
| `DMA0V`  | `0x10` | Timer0 vector |
| `TREG0`  | `1`    | 1 tick = 1 HBlank line |

### 4.2 Pattern B — Dynamic Destination (ROM Lookup Table)

A second DMA0 mode lets the game redirect DMA0 to any of 4 hardware registers selected
from a ROM table, without changing the timer or DMAM.

**ROM table** (at `0x21BC2E`, 4 entries of u16):
```
index 0: 0x8002  WIN_X    (word covers WIN_X + WIN_Y)
index 1: 0x8020  SPR_OFS_X (word covers SPR_OFS_X + SPR_OFS_Y)
index 2: 0x8032  SCR1_OFS_X (word covers SCR1_X/Y AND SCR2_X/Y — dword range)
index 3: 0x8034  SCR2_OFS_X (word covers SCR2_X/Y)
```

**Selection code** (`0x0001BBDC`):
```asm
; XHL = 0x6200 (source scroll table)
ldc  DMAS_0, XHL
ld   W,(0x65FC+4)          ; state flags
and  A, W                  ; A = W & 3 = destination index (0..3)
add  A, A                  ; * 2 = byte offset into table
lda  XHL, 0x21BC2E         ; ROM table base
ld   HL,(XHL+A)            ; read u16 dest address
extz XHL                   ; zero-extend to 32-bit
ldc  DMAD_0, XHL           ; set destination
```

**Mode selection** (word vs dword):
```asm
; Default: word mode, 152 triggers
ld   HL, 0x98 ; DMAC
ld   A,  0x09 ; DMAM word
; If bit 2 of state flags is set: dword mode, 76 triggers
ld   HL, 0x4C ; DMAC = 76
ld   A,  0x0A ; DMAM dword
; Then: TREG0 = DMAM & 3  (1 for word, 2 for dword)
and  A, 0x3
ld   (TREG0), A
```

**Safe start** (wait for VBlank before enabling Timer0):
```asm
wait_vbl:
    ld  (0x006F), 0x4E        ; kick watchdog while waiting
    cp  (0x8009), 0x98        ; RAS_V < 152?
    jr  C, wait_vbl           ; loop until in VBlank
or  (TRUN), 0x81              ; start Timer0 only inside VBlank
```

> Never start Timer0 in the middle of a frame — the first DMA trigger would hit a
> wrong scanline and shift the entire raster table.

### 4.3 DMA1 — BG_CTL Audio per-HBlank (byte mode)

DMA1 writes to BG_CTL (`0x8118`) once per HBlank, allowing the PSG channel enable
mask to change per-scanline. This produces audio effects (gating, tremolo) that are
impossible from the CPU alone.

**Init** (`0x0005E012`):
```asm
and  (TRUN),0xFD           ; stop Timer1
ld   (TREG1), 2            ; 1 trigger every 2 lines (76 triggers for 152 lines)
ldc  DMAS_1, 0x6000        ; source: BG_CTL byte table in RAM
ldc  DMAD_1, 0x8118        ; dest: BG_CTL register
ldc  DMAM_1, 0x08          ; M_SINC1 (byte, mem->I/O)
or   (TRUN),0x82           ; start Timer1 + prescaler
ld   (0x5052), 0x261001    ; install per-frame callback (arm DMA0+DMA1)
```

**Arm per-frame** (callback at `0x00061001`):
```asm
; DMA0 arm (same as 4.1):
ldc  DMAS_0, 0x62E0 + offset
ldc  DMAC_0, 0x98
ld   (0x007C), 0x10         ; DMA0V = 0x10

; DMA1 arm:
ldc  DMAS_1, 0x6000
ldc  DMAC_1, 0x4C           ; 76 transfers (1 every 2 lines)
ld   (0x007D), 0x11         ; DMA1V = 0x11
```

**Disable both channels** (`0x0005E33C`):
```asm
ld   (0x5052), 0x2610B0    ; replace callback with stub-ret
ld   (0x007C), 0           ; DMA0V = 0 (stop)
ld   (0x007D), 0           ; DMA1V = 0 (stop)
```

Key numbers:
| Parameter | Value | Meaning |
|-----------|-------|---------|
| `DMAM_1` | `0x08` | byte mode (M_SINC1) |
| `DMAC_1` | `0x4C` = 76 | TREG1=2: every 2nd line |
| `DMA1V`  | `0x11` | Timer1 vector (separate from DMA0=0x10) |

### 4.4 Double-Buffer Toggle via XOR

Two ping-pong buffers in RAM at stride 0x134:
```
Buffer A: 0x62E0..0x640F  (0x130 bytes used)  padding: 0x6410..0x6413
Buffer B: 0x6414..0x6543  (0x130 bytes used)  padding: 0x6544..0x6547
Offset variable at 0x6548: 0 (use A) or 0x134 (use B)
```

**Toggle** (called at end of per-frame buffer build):
```asm
xor  (0x6548), 0x134      ; flips between 0 and 0x134
```

CPU builds into the "back" buffer (current offset XOR 0x134), then re-arms DMA
pointing to the newly written buffer. Prevents tearing when CPU and DMA access
different buffers simultaneously.

> The 4 padding bytes at 0x6410/0x6544 are **not spare RAM** — the source title reuses them
> for auxiliary data. Do not treat padding in a stride buffer as free.

### 4.5 Stop-All DMA Function

**Full stop function** at `0x000186D2` — stops all 4 channels atomically:
```asm
push WA
xor  WA, WA                ; WA = 0
ld   (0x007C), A           ; DMA0V = 0
ld   (0x007D), A           ; DMA1V = 0
ld   (0x007E), A           ; DMA2V = 0
ld   (0x007F), A           ; DMA3V = 0
ldc  DMAC_0, WA            ; DMAC_0 = 0
ldc  DMAC_1, WA            ; DMAC_1 = 0
ldc  DMAC_2, WA            ; DMAC_2 = 0
ldc  DMAC_3, WA            ; DMAC_3 = 0
pop  WA
ret
```

This is called at VBlank entry (before `swi WAIT_VBLANK`) when the DMA enable flag
at `0x6F85` is non-zero. After VBlank work, the VBL callback re-arms the channels.

See [Game-Loop §8.3](../05_Systems/Game-Loop.md) for the VBL handler context.

### 4.6 VBL Callback Pointer Dispatcher

An indirect function pointer at `0x5052` can serve as a per-frame hook called
from the VBL handler. This allows enabling/disabling DMA without touching the ISR:

```asm
; VBL dispatcher (0x00000AB9):
ld   XIX,(0x5052)          ; load function pointer
call T, XIX                ; call it unconditionally

; Install DMA arm function:
ld   (0x5052), 0x261001    ; -> arm DMA0+DMA1 each frame

; Disable (swap to stub):
ld   (0x5052), 0x2610B0    ; -> stub that just does ret
```

C equivalent:
```c
typedef void (*VblCallback)(void);
volatile VblCallback vbl_hook;   /* at 0x5052 */
/* In VBL ISR: */ vbl_hook();
/* Enable DMA: */ vbl_hook = arm_dma_both;
/* Disable:   */ vbl_hook = null_stub;
```

This pattern avoids modifying the VBL ISR itself — essential when the ISR is in ROM
or when the DMA is dynamically turned on/off between game states.

---

## 5. Template Implementation

The template provides an optional DMA module: a C API (`u8`/`u16`/`u32`), an ASM helper for
the `LDC` control-register sequences, and a raster scroll wrapper.

### 5.1 Feature Flags (Makefile)

| Flag | Default | Description |
|------|---------|-------------|
| `NGP_ENABLE_DMA=1` | off | Enables the DMA module + raster wrapper |
| `NGP_DMA_ALLOW_VBLANK_TRIGGER=0/1` | 0 | Allow/forbid VBlank as start vector in the API |
| `NGP_DMA_INSTALL_DONE_ISR=0/1` | 0 | Install INTTCn handlers that set a "done" flag + callback |
| `NGP_DMA_INSTALL_REARM_ISR=0/1` | 0 | Install INTTCn handlers capable of auto-rearm |

Build examples:
```
make NGP_ENABLE_DMA=1
make NGP_ENABLE_DMA=1 NGP_DMA_INSTALL_REARM_ISR=1
```

### 5.2 Main API (u8/u16/u32)

**Direct programming:**
```c
ngpc_dma_start_table_u8 (channel, dst_reg, src_u8,  count, start_vector)
ngpc_dma_start_table_u16(channel, dst_reg, src_u16, count, start_vector)
ngpc_dma_start_table_u32(channel, dst_reg, src_u32, count, start_vector)
```

**Notes on `count`** — number of MicroDMA transfers (not bytes):
- u8: `count` = number of bytes
- u16: `count` = number of words (2 bytes each)
- u32: `count` = number of dwords (4 bytes each)

**Streams** (store params, provide rearm):
```c
NgpcDmaU8Stream  + ngpc_dma_stream_begin_u8()  + ngpc_dma_stream_rearm_u8()
NgpcDmaU16Stream + ngpc_dma_stream_begin_u16() + ngpc_dma_stream_rearm_u16()
NgpcDmaU32Stream + ngpc_dma_stream_begin_u32() + ngpc_dma_stream_rearm_u32()
```

**Timer helpers:**
```c
ngpc_dma_timer0_hblank_enable()                    /* treg0=1 */
ngpc_dma_timer0_hblank_enable_treg0(treg0)         /* treg0=1 or 2 typically */
ngpc_dma_timer01_hblank_enable()                   /* Timer0 TI0 + Timer1 TO0TRG */
ngpc_dma_timer1_from_timer0_enable_treg1(treg1)
```

> **Debug note:** On some configurations, reading back `TREG0/TREG1` at runtime does not reflect
> the value you wrote — it shows the live counter state at that instant (e.g. `0x04`).
> `T01MOD` also contains Timer1 bits (`T1CLK`, `PWMMOD`). The template helpers only modify the
> bits needed for Timer0/Timer1 DMA use (they clear `TMOD(7-6)` + `T0CLK(1-0)`), so `T01MOD`
> may retain `0x04` without Timer0 being misconfigured.
> **To verify DMA, rely on the visual effect + `DMACn/DMAxV` + expected count values
> (e.g. 152 or 76) rather than TREG0/TREG1 readback.**

### 5.3 Ping-Pong (Double Buffer)

Objective: write into a "back buffer" while MicroDMA reads the "front buffer"; swap in VBlank,
then re-arm with the new address.

```c
NgpcDmaPingPong + ngpc_dma_pp_init() / ngpc_dma_pp_front() / ngpc_dma_pp_back() / ngpc_dma_pp_swap()
```

Useful stride constant: `NGPC_DMA_PP_STRIDE_WORD152 = 0x0134`

### 5.4 Auto-Rearm on INTTCn

Objective: when `DMACn` reaches 0, the hardware clears `DMAxV=0`. Auto-rearm immediately
reprograms `DMAS/DMAC` and re-writes `DMAxV` inside the INTTCn ISR, closing the window where
the source timer (Timer0/Timer1) could fire a CPU interrupt instead of a DMA trigger.

```c
ngpc_dma_autorearm_begin_u8/u16/u32(...)            /* configure the recipe */
ngpc_dma_autorearm_set_src_u8/u16/u32(channel, src) /* change table (ping-pong) */
ngpc_dma_autorearm_enable(channel)                  /* arm for the first time */
ngpc_dma_autorearm_disable(channel)                 /* stop auto-rearm */
```

Requires: compile with `NGP_DMA_INSTALL_REARM_ISR=1` and call `ngpc_dma_init()`.
The template also enables INTTC interrupts via `INTETC01/23` (low priority level)
when these handlers are installed.

### 5.5 Raster Scroll Wrapper

High-level wrapper for per-scanline scroll tables:

**`NgpcDmaRaster`** — 2 separate `u8` tables: X and/or Y
```c
ngpc_dma_raster_begin() / ngpc_dma_raster_enable() / ngpc_dma_raster_rearm() / ngpc_dma_raster_disable()
```
- Timer0 alone for X-only; Timer0+Timer1 for X+Y (avoids CHAIN).

**`NgpcDmaRasterXY`** — 1 packed `u16` table XY, single channel + Timer0
```c
ngpc_dma_raster_xy_begin() / ngpc_dma_raster_xy_enable() / ngpc_dma_raster_xy_rearm() / ngpc_dma_raster_xy_disable()
```
- Table entry format: `(Y << 8) | X` (low byte = X, high byte = Y)
- Single channel, Timer0 (0x10) only — closest to the per-scanline word pattern.

**Table builder helpers:**
```c
ngpc_dma_raster_build_parallax_table(u8* out_x, ...)          /* X-only */
ngpc_dma_raster_build_parallax_table_xy(u16* out_xy, ..., u8 y) /* XY, constant Y */
```

---

## 6. Safe Rules

**1) Never break the CPU VBlank ISR:**
- VBlank is mandatory (watchdog clear + system calls).
- Avoid `start_vector=0x0B` unless you have a proven watchdog-safe strategy.

**2) Tables in RAM:**
- Avoid using cartridge ROM as DMA source until validated on your hardware/flashcart.

**3) Rearm timing:**
- Rearm as early as possible in VBlank if using the "manual rearm" pattern.
- If auto-rearm INTTCn is active, verify that alignment stays stable.

**4) Timer0/Timer1 conflicts:**
- Timer0 is shared with `ngpc_raster` (CPU HBlank ISR) and some sprmux modes.
- Two MicroDMA channels on the same start vector → CHAIN (not true parallel).

---

## 7. Usage Examples

### 7.1 Example A: 1 channel u8, Timer0 (X-only)

Writes `SCR1_OFS_X` once per scanline from a 152-byte table.

```c
#include "ngpc_sys.h"
#include "ngpc_dma.h"
#include "ngpc_hw.h"

static u8 g_tbl_x[SCREEN_H];
static NgpcDmaU8Stream g_dma_x;

static void build_table(void)
{
    u8 i;
    for (i = 0; i < (u8)SCREEN_H; i++) {
        g_tbl_x[i] = i; /* demo ramp */
    }
}

void demo_dma_u8(void)
{
    ngpc_dma_init();
    build_table();

    ngpc_dma_timer0_hblank_enable(); /* TI0 (HBlank), treg0=1 */
    ngpc_dma_stream_begin_u8(&g_dma_x, NGPC_DMA_CH0,
                             (volatile u8 NGP_FAR *)&HW_SCR1_OFS_X,
                             g_tbl_x, (u16)SCREEN_H,
                             NGPC_DMA_VEC_TIMER0);

    while (1) {
        ngpc_vsync();
        ngpc_dma_stream_rearm_u8(&g_dma_x); /* as early as possible in VBlank */
    }
}
```

### 7.2 Example B: 2 channels u8, Timer0 + Timer1 (X+Y, no CHAIN)

```c
static u8 g_tbl_x[SCREEN_H];
static u8 g_tbl_y[SCREEN_H];
static NgpcDmaU8Stream g_dma_x, g_dma_y;

void demo_dma_u8_xy(void)
{
    ngpc_dma_init();

    ngpc_dma_timer01_hblank_enable(); /* Timer0=TI0, Timer1=TO0TRG */

    ngpc_dma_stream_begin_u8(&g_dma_x, NGPC_DMA_CH0,
                             (volatile u8 NGP_FAR *)&HW_SCR1_OFS_X,
                             g_tbl_x, (u16)SCREEN_H,
                             NGPC_DMA_VEC_TIMER0);
    ngpc_dma_stream_begin_u8(&g_dma_y, NGPC_DMA_CH1,
                             (volatile u8 NGP_FAR *)&HW_SCR1_OFS_Y,
                             g_tbl_y, (u16)SCREEN_H,
                             NGPC_DMA_VEC_TIMER1);

    while (1) {
        ngpc_vsync();
        ngpc_dma_stream_rearm_u8(&g_dma_x);
        ngpc_dma_stream_rearm_u8(&g_dma_y);
    }
}
```

### 7.3 Example C: 1 channel u16 word — X+Y in one trigger

Writes `SCR2_OFS_X/Y` as a single word per scanline.
Table entry: `u16 entry = (Y << 8) | X`.

```c
static u16 g_tbl_xy[SCREEN_H];
static NgpcDmaU16Stream g_dma_xy;

void demo_dma_u16_xy(void)
{
    u16 i;

    ngpc_dma_init();

    for (i = 0; i < (u16)SCREEN_H; i++) {
        u8 x = (u8)i;
        u8 y = 0;
        g_tbl_xy[i] = (u16)((u16)y << 8) | (u16)x;
    }

    ngpc_dma_timer0_hblank_enable_treg0(1);
    ngpc_dma_stream_begin_u16(&g_dma_xy, NGPC_DMA_CH0,
                              (volatile u8 NGP_FAR *)&HW_SCR2_OFS_X,
                              g_tbl_xy, (u16)SCREEN_H,
                              NGPC_DMA_VEC_TIMER0);

    while (1) {
        ngpc_vsync();
        ngpc_dma_stream_rearm_u16(&g_dma_xy);
    }
}
```

### 7.4 Example D: Auto-rearm INTTC0 + ping-pong

Requires: `NGP_ENABLE_DMA=1` + `NGP_DMA_INSTALL_REARM_ISR=1`

```c
static u16 g_pp_storage[2][(NGPC_DMA_PP_STRIDE_WORD152 / 2)];
static NgpcDmaPingPong g_pp;

static void build_back_buffer(void)
{
    u16 *buf = (u16 *)ngpc_dma_pp_back(&g_pp);
    u16 i;
    for (i = 0; i < (u16)SCREEN_H; i++) {
        u8 x = (u8)(i + 3);
        u8 y = 0;
        buf[i] = (u16)((u16)y << 8) | (u16)x;
    }
}

void demo_dma_autorearm_pingpong(void)
{
    ngpc_dma_init();

    ngpc_dma_pp_init(&g_pp, (u8 NGP_FAR *)g_pp_storage[0], NGPC_DMA_PP_STRIDE_WORD152);
    build_back_buffer();
    ngpc_dma_pp_swap(&g_pp);

    ngpc_dma_timer0_hblank_enable_treg0(1);

    ngpc_dma_autorearm_begin_u16(NGPC_DMA_CH0,
                                 (volatile u8 NGP_FAR *)&HW_SCR2_OFS_X,
                                 (const u16 NGP_FAR *)ngpc_dma_pp_front(&g_pp),
                                 (u16)SCREEN_H,
                                 NGPC_DMA_VEC_TIMER0);
    ngpc_dma_autorearm_enable(NGPC_DMA_CH0);

    while (1) {
        ngpc_vsync();
        build_back_buffer();
        ngpc_dma_pp_swap(&g_pp);
        ngpc_dma_autorearm_set_src_u16(NGPC_DMA_CH0,
                                       (const u16 NGP_FAR *)ngpc_dma_pp_front(&g_pp));
    }
}
```

> In this pattern the ISR (INTTC0) re-arms automatically. In VBlank: only rebuild back
> buffer + swap + update source pointer.

---

## 8. Interactions with Other Modules

### 8.1 With ngpc_raster (CPU ISR Timer0)

`ngpc_raster` consumes Timer0 as a CPU ISR. MicroDMA on `start_vector=Timer0` consumes the
Timer0 request for DMA. In practice: `ngpc_raster` and MicroDMA Timer0 are **mutually exclusive**
unless you design around it explicitly.

### 8.2 With ngpc_sprmux

Possible: MicroDMA Timer0 (X-only) + sprmux on Timer1 CPU ISR, if your sprmux is configured on
Timer1 and MicroDMA does not use Timer1 as start vector.

Avoid: MicroDMA Timer1 + sprmux Timer1 CPU ISR — direct conflict.

> Note: ngpc_sprmux was abandoned for production (hardware HBlank budget too short).
> See [Sprites-and-OAM](Sprites-and-OAM.md) for context.

### 8.3 With VRAMQ

VRAMQ is flushed in the VBlank ISR and must not be starved. Do not trigger MicroDMA on VBlank
by default.

---

## 9. Debug — Symptoms — Quick Fixes

### 9.1 Power-off / Reset on Hardware

**Cause:** MicroDMA triggered on VBlank consumes the VBlank request → watchdog not cleared.

**Fix:** Use Timer0 (`0x10`) and let the VBlank ISR run normally.
Keep `NGP_DMA_ALLOW_VBLANK_TRIGGER=0` until proven otherwise.

### 9.2 Effect Visible Only on Lower Screen

**Cause:** Re-arm happens too late in the frame (after significant CPU work).

**Fix:** Re-arm immediately after `ngpc_vsync()` (as early as possible in VBlank).
Or use auto-rearm INTTCn if validated on your setup.

### 9.3 Second Channel Never Moves

**Cause:** CHAIN starvation — 2 channels configured with the same start vector.

**Fix:** Use Timer0 + Timer1 (0x10 + 0x11), or a single channel in word mode for X+Y.

### 9.4 asm900 "Illegal source file format"

**Cause:** asm900 is sensitive to text file format.

**Fix:** Keep the DMA programming `.asm` file in CRLF line endings (ASCII/ANSI, no UTF-8 BOM).

### 9.5 Incoherent TR0/TR1/T01MOD Readback (e.g. `0x04`)

**Symptom:** You read `TR0=04` / `TR1=04` / `T01MOD=04` when you expected `TREG0=01` or `02`.

**Explanation:**
- `TREG0/TREG1` can behave as "live" registers (live counter), not simple latches.
- `T01MOD` contains bits for Timer1. If you did not touch Timer1, those bits may be non-zero.

**Verification approach:**
- u8 per-scanline mode: `DMAC0` must count down to 0 over one frame.
- u16 XY 1ch mode: `DMAC0` must count down to 0 (152 transfers).
- u32 (T0/2) mode: `DMAC0` must count down to 0 (76 transfers), effect = "every other line" (normal).

### 9.6 Works in Test App, Breaks in Real Game

**Symptom:** Auto-rearm preset worked perfectly in a standalone test app; once ported into a
real game there is no visible effect, or no scroll at all when the CPU fallback is removed.

**Interpretation:** The issue is not the visual formula or the XY table — it is the effective
and stable triggering of the auto-rearm in this specific integration context.

**Recommended recovery approach:**
1. Return to a stable mode first: `u16 packed XY` / `ngpc_dma_raster_xy_begin()/enable()/rearm()/disable()`
   with manual `rearm()` immediately after `ngpc_vsync()`.
2. Reuse the visual formula from the test app (X wobble, Y softer wobble).
3. Keep a temporary `ngpc_gfx_scroll()` CPU baseline to distinguish "DMA inactive" from "scroll frozen".
4. Only retry auto-rearm INTTCn after visual validation in manual mode.

**Lesson:** The best visual preset and the best integration preset are not always the same.
For a real game, `manual rearm u16 XY` is often the correct production choice even if
auto-rearm looks more elegant on paper.

---

## 10. Hardware Validation Checklist

1. Start with Example A (u8 X-only, Timer0).
2. Verify:
   - `DMA0V` goes to `0x10` when armed, then returns to 0 when the table finishes (one-shot).
   - `DMAC0` counts down to 0 at the end.
3. Test manual rearm:
   - Rearm in VBlank → stable full-screen effect.
4. Test dual channel:
   - Timer0 + Timer1 → simultaneous X and Y, no starvation.
5. Test word mode:
   - u16 → `SCR2_OFS_X/Y` (pack Y<<8|X) → 1 channel, 1 trigger per line.
6. (Optional) Enable auto-rearm:
   - `NGP_DMA_INSTALL_REARM_ISR=1`
   - Verify no lockups and alignment is stable.

---

## 12. Real-Game Integration Lessons

The following lessons come from validating the MicroDMA module in an actual game (scanline wobble
effect on a background layer). The value of this experience is that a mode was validated in a
dedicated test app, compared to a real gameplay integration, and then reduced to the production
path that actually holds in-game.

### 12.1 Production-Retained Setup

**Target effect:** per-scanline background wobble on a stage with scrolling.

**Final production path:**
```c
ngpc_dma_raster_xy_begin();
ngpc_dma_raster_xy_enable();

/* In main game loop (VBlank hook): */
ngpc_vsync();
ngpc_dma_raster_xy_rearm();   /* called as early as possible after vsync */

/* On leaving gameplay (state clear / game over / menus): */
ngpc_dma_raster_xy_disable();
```

**Why this choice:**
- Most robust in real gameplay integration.
- Readable and easy to debug.
- Allows simple visual tuning without an additional experimental ISR.

### 12.2 Why Auto-Rearm Was Not Used

In the standalone test app, `AUTO-REARM u16 XY (INTTC0)` gave an excellent result. In the
real game: no visible effect, and sometimes no scroll at all once the CPU fallback was removed.

The problem was not the XY formula. The problem was the effective and stable triggering of the
rearm in that specific execution context.

**Production decision:** do not insist on theoretical elegance — return to the validated manual path.

**Lesson:** a "superb" preset in a test app is not automatically the right production preset.

### 12.3 Visual Formula That Worked

Best visual/readability compromise:
- amplitude: `64`
- frequency: `8`
- phase step: `1`
- vertical component significantly weaker than horizontal (`amp/4`)

Structure:
- `X` = primary wobble
- `Y` = subtle breathing to avoid the effect feeling too rigid

**Why:**
- Too much vertical = cluttered image.
- Too high frequency = agitated, "dirty" look.
- Too fast phase = visually tiring.

### 12.4 Critical Point: Tilemap Line Repetition

**Problem:** if the DMA modifies `SCR1_OFS_Y`, lines outside the tilemap's useful height can
become visible → black gaps or uninitialized lines appear on screen.

**Fix:** tile the background tilemap on `SCR1` all the way down to the bottom of the 32×32 map.

**Conclusion:** for any XY raster effect with a vertical component, also prepare the tilemap
accordingly. The DMA effect is not just the table.

### 12.5 Clean Disable Outside Gameplay

**Validated production rule:** disable the DMA when leaving gameplay. Always disable at entry of:
- Stage Clear
- Game Over
- Continue
- Name Entry
- Return to menu

**Why:** these screens use the system font and/or stable scenes; keeping DMA armed outside
gameplay unnecessarily complicates debugging.

### 12.6 Interaction with CPU Scroll

**Useful pattern:** temporarily keep a `ngpc_gfx_scroll()` CPU call during debugging to
distinguish between "DMA inactive" and "scroll actually frozen".

In final production: if DMA takes over line by line, the CPU scroll can remain as a simple
base value. DMA then *modulates* the background, rather than replacing all scroll logic.

### 12.7 Recommended Integration Order

1. Validate the effect in a minimal standalone test app.
2. Port it as `manual rearm u16 XY`.
3. Validate in a real game.
4. Only then retry `auto-rearm` if there is a real need.

**For a production template, the recommended default path is:**
- `u16 XY packed`
- manual rearm
- called very early in VBlank

---

## 13. Raster Table Build Cost

### 13.1 The Problem: Sinusoidal Table Has CPU Cost

MicroDMA offloads HBlank register writes (152 × ~8-10 cycles ≈ 1.5% of frame budget).
**But it does NOT offload the table construction**, which must happen in CPU code each frame.

Typical per-scanline sinusoidal table build:

```c
for (line = 0; line < (u8)SCREEN_H; line++) {   /* 152 iterations */
    u8 a = (u8)(phase + s_bg_dma_line_phase[line]);  /* RAM read */
    s16 wave_x = s_bg_dma_wave_x[a];                 /* RAM read */
    s16 wave_y = s_bg_dma_wave_y[a];                 /* RAM read */
    u8 scr1_x  = (u8)((s16)base_x + wave_x);
    out_table[line] = (u16)(((u16)scr1_y << 8) | scr1_x);  /* RAM write */
}
```

Estimated cost on TLCS-900 at 6.144 MHz:
- ~30-50 cycles per iteration (3 RAM reads + signed 16-bit arithmetic + 1 RAM write)
- 152 iterations → ~4600-7600 cycles
- Frame budget: 6144000 / 60 = 102400 cycles
- **=> ~5-7% of the frame budget consumed by the table build alone**

If this adds overhead to an already-loaded frame, it can push total CPU usage above 100% →
frame drops → visible lag.

### 13.2 DMA Budget vs Overhead

| Operation | Without raster DMA | With raster DMA |
|-----------|-------------------|-----------------|
| HBlank ISR scroll register writes | 0 (no effect) | 0 (DMA does it) |
| Table build per frame | 0 | +5-7% |
| MicroDMA micro-stalls | 0 | +~1.5% |
| **Total delta** | **0%** | **+6-8% per frame** |

DMA does not save anything here compared to having no raster effect at all — it **adds** CPU work
(the table build) without removing anything (it replaces an ISR that did not exist before).

### 13.3 Official SNK Doc Confirmation

The official SNK MicroDMA doc confirms:
- "without any restrictions from the interrupt level set, operates even during a mask-able
  interrupt at the highest interrupt level (level 6)" → MicroDMA bypasses interrupt masks, always executes.
- "Timer 0~3 interrupt level = 1, Other interrupt level = 2~5" → relevant only if Timer0 is also
  used as a software ISR. In the standard usage (`NGP_DMA_INSTALL_REARM_ISR=0`), Timer0 interrupt
  level does not affect MicroDMA. **The Timer0 configuration in the DMA driver is correct.**

The problem is not in the DMA driver. It is in the table build cost.

### 13.4 Rules from This Diagnostic

**1) MicroDMA is not "free" if the raster effect did not exist before.**
Count the table build cost O(SCREEN_H) per frame in your CPU budget.

**2) More complex per-line calculation = more expensive table.**
- Simple parallax bands: O(num_bands) → near-zero cost.
- Sinus per line: O(152) → ~5-7% → measure before adopting in production.

**3) `ngpc_dma_raster_build_parallax_table_xy()` is the recommended approach for a visible
effect without significant CPU cost.**

**4) Rebuild every 2-4 frames if sinus is needed.**
The wave can be animated at 30fps instead of 60fps. Visually imperceptible.
```c
if (s_bg_dma_active && ((s_frame & 1) == 0)) {
    bg_dma_build_table(s_bg_dma_table, s_scroll_x,
                       (u8)(s_frame * BG_DMA_PHASE_STEP));
}
```
This halves the build cost.

**5) For effects where scroll_x changes every frame, the build remains necessary each frame
if the table must include the current X position.**
Alternative: separate the base X (via `ngpc_gfx_scroll()` CPU) and DMA only the wave deltas
→ allows rebuilding the wave less often if the camera is not moving.

### 13.5 Build Order: Before or After Rearm

Common example pattern:
```
build_table() → ngpc_vsync() → rearm()
```

Alternative (in-game) pattern:
```
ngpc_vsync() → rearm() → [game logic] → build_table()
```

In the in-game pattern, the DMA runs during the current frame using the table built in the
*previous* frame. The freshly-built table will only be used after the next rearm.
→ 1-frame visual delay on the animation: acceptable and imperceptible.
→ No impact on lag.

The real impact is the CPU cost of `build_table()`, not its order relative to rearm.

---

## 14. Inline ASM DMA Sequences

This section gives the TLCS-900/H assembly sequences underlying MicroDMA programming, an
asm900-friendly Timer0 setup, the packed `u16` XY pattern, the INTTC0 auto-rearm ISR, and
asm900 file-format pitfalls. The C API in section 5 wraps these `LDC` sequences.

The control registers `DMASn`/`DMADn`/`DMACn`/`DMAMn` are write-only via `ldc cr, reg`; `DMAxV`
is a write-only start vector that the hardware auto-clears when `DMACn` reaches 0.

### 14.1 Control Register Access — LDC

`DMAS/DMAD/DMAC/DMAM` are programmed via `ldc cr, reg` (TLCS-900/H control register access).

```asm
; Load a 24-bit address into XWA
ldl xwa, 0x8034

; Program DMAD0 (destination) from XWA
ldc dmad0, xwa

; Program DMAS0 (source) from XWA
ldc dmas0, xwa

; Program DMAC0 (count) from WA
ldw wa, 152
ldc dmac0, wa

; Program DMAM0 (mode) from A
ldb a, 0x08    ; mem->I/O, byte, src++
ldc dmam0, a
```

### 14.2 Timer0 in HBlank Trigger Mode (ASM)

Based on the official SNK sample:

```asm
; Stop Timer0
andb (TRUN), 0b10001110

; Timer0/1 in 8-bit timer mode, Timer0 clock = TI0 (external HBlank)
ldb (T01MOD), 0x00
ldb (TREG0),  0x01

; Start Timer0
orb (TRUN), 0b00000001
```

**Notes:**
- Depending on your codebase, `T01MOD` may retain bits unrelated to Timer0 (`PWMMOD` / `T1CLK`).
- To verify: rely on the visual effect and `DMACn` counting down to 0,
  rather than reading back `TREG0` (which reflects the live counter value at read time).

### 14.3 Packed u16 XY Pattern (ASM)

**Objective:**
- 1 channel (CH0)
- 1 word transfer per scanline
- Writes 2 consecutive bytes:
  - low byte → `0x8034` (`SCR2_OFS_X`)
  - high byte → `0x8035` (`SCR2_OFS_Y`)

Table entry format: `entry = (Y << 8) | X`

**Setup:**

```asm
; Destination: SCR2_OFS_X (0x8034)
ldl xwa, 0x8034
ldc dmad0, xwa

; Mode: word mem->I/O, src++
ldb a, 0x09
ldc dmam0, a

; Source table + count
ldl xwa, table_xy
ldc dmas0, xwa
ldw wa, 152
ldc dmac0, wa

; Start vector = Timer0 (0x10)
ld (DMA0V), 0x10
```

**Rearm:** required each frame (in VBlank, as early as possible), or via INTTC0 (see [§14.4](#144-auto-rearm-via-inttc0-asm)).

### 14.4 Auto-Rearm via INTTC0 (ASM)

When `DMAC0` reaches 0, INTTC0 is generated and `DMA0V` is cleared to 0.
The auto-rearm pattern re-arms inside the INTTC0 handler (RAM slot `0x6FF0`).

**INTTCn RAM slots:**

| Address | Channel |
|---------|---------|
| `0x6FF0` | End MicroDMA 0 (INTTC0) |
| `0x6FF4` | End MicroDMA 1 (INTTC1) |
| `0x6FF8` | End MicroDMA 2 (INTTC2) |
| `0x6FFC` | End MicroDMA 3 (INTTC3) |

**Minimal INTTC0 ISR:**

```asm
isr_inttc0:
    ; (optional) update DMAS0 for ping-pong / new table:
    ; ldl xwa, new_table
    ; ldc dmas0, xwa

    ; Reprogram count
    ldw wa, 152
    ldc dmac0, wa

    ; Re-arm start vector
    ld (DMA0V), 0x10

    reti
```

> The template exposes this pattern in C via `NGP_DMA_INSTALL_REARM_ISR=1`
> and the `ngpc_dma_autorearm_*()` functions — see [§5.4](#54-auto-rearm-on-inttcn).

### 14.5 Safe Start — Wait for VBlank (ASM)

**Problem:** starting Timer0 + DMA mid-frame can miss the top scanlines (effect only visible
in the bottom portion of the screen).

**Solution:** wait until VBlank, then wait for `RAS.V >= 0x98` (152) before enabling
timer + DMA.

```asm
; Wait for VBlank flag
wait_vbl:
    bit 6, (0x8000 + STATUS_2D_OFS)   ; test VBlank status bit
    jr z, wait_vbl

; Wait for end of all 152 visible scanlines
wait_ras:
    ldb a, (RAS_V)
    cp  a, 0x98
    jr c, wait_ras

; Now safe to arm timer and DMA
```

### 14.6 asm900 Common Pitfalls

**"Illegal source file format"**
- **Cause:** asm900 rejects certain line ending formats or text encodings.
- **Fix:** Keep `.asm` files in **CRLF** line endings, **ASCII/ANSI** encoding. Avoid UTF-8 with BOM.

**Labels and identifiers**
- **Fix:** Use only A-Z, 0-9, and underscore in labels and identifiers. Avoid special characters or accented letters.

**C ↔ ASM integration**
- Avoid fragile C inline-asm for DMA control register writes. The DMA module uses dedicated ASM
  routines for the `ldc` sequences, called from the C API wrappers.

---

## Quick Reference

| Item | Value | Notes |
|------|-------|-------|
| MicroDMA channels | 0..3 | CH0 = highest priority |
| Registers per channel | DMAS, DMAD, DMAC, DMAM, DMAxV | DMAxV write-only |
| One-shot | yes | DMAxV auto-cleared when DMACn = 0 |
| VBlank start vector | `0x0B` | **Avoid** — kills CPU VBlank ISR |
| Timer0 start vector | `0x10` | Safe — standard HBlank trigger |
| Timer1 start vector | `0x11` | Safe — use for 2nd channel (no CHAIN) |
| DMAM byte mode | `0x08` | mem→I/O, byte, src++ |
| DMAM word mode | `0x09` | mem→I/O, word, src++ (X+Y in one trigger) |
| DMAM dword mode | `0x0A` | mem→I/O, dword, src++ |
| Word entry format | `(Y << 8) | X` | Low byte = X (SCR_OFS_X), high byte = Y |
| INTTC0 handler | `0x6FF0` | 32-bit ptr — auto-rearm goes here |
| Double-buffer stride | `0x0134` | 152 lines + padding |
| Raster position | `0x8009` (RAS.V) | >= 0x98 (152) = VBlank |
| Table build cost (sinus) | ~5-7% per frame | O(152) — measure before using in prod |
| Parallax band table cost | ~0% | O(num_bands) — recommended |
| Safe production path | u16 XY, manual rearm, early in VBlank | Validated in real game |
| Set destination (ASM) | `ldl xwa, addr` + `ldc dmadN, xwa` | 24-bit address |
| Set source (ASM) | `ldl xwa, addr` + `ldc dmasN, xwa` | 24-bit address |
| Set count (ASM) | `ldw wa, N` + `ldc dmacN, wa` | Number of transfers |
| Arm channel 0 (ASM) | `ld (DMA0V), 0x10` | Start vector = Timer0 |
| Timer0 mode (ASM) | `T01MOD=0x00`, `TREG0=0x01`, `TRUN|=1` | HBlank / TI0 external clock |
| CRLF for asm900 | mandatory | ASCII/ANSI only, no UTF-8 BOM |

---

## See Also

- [Hardware-Registers](../01_Hardware/Hardware-Registers.md) — Full hardware register map (timers, DMA regs, scroll offsets)
- [Game-Loop](../05_Systems/Game-Loop.md) — VBlank ISR structure, watchdog, frame budgets
- [Tilemaps-and-Scrolling](Tilemaps-and-Scrolling.md) — Scroll register usage, tilemap layout
- [Sprites-and-OAM](Sprites-and-OAM.md) — OAM DMA for mass sprite updates
