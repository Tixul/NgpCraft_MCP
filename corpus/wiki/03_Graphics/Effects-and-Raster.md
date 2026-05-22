# Effects and Raster

Visual effect systems for NGPC homebrew: HBlank raster effects, palette fades and flash, bitmap mode emulation, sysfont text rendering, deferred VRAM updates, and window animation.

---


## 1. Raster / HBlank Effects

### 1.1 Stop-All-DMA Rule at VBlank Entry

Reverse engineering of a commercial action game confirms that all DMA channels are stopped at the very start of the VBlank handler, before any work:

```asm
ld (0x007c), 0    ; stop DMA0V
ld (0x007d), 0    ; stop DMA1V
ld (0x007e), 0    ; stop DMA2V
ld (0x007f), 0    ; stop DMA3V
ldc DMAC_0, WA    ; clear DMAC0 (WA=0)
ldc DMAC_1, WA    ; clear DMAC1
ldc DMAC_2, WA    ; clear DMAC2
ldc DMAC_3, WA    ; clear DMAC3
```

DMA channels are re-armed *after* all VBlank work is complete.

This confirms the hardware rule documented in [DMA.md](DMA.md): **an active DMA during VBlank
powers off the watchdog**. Never leave a raster DMA (Timer0/1-triggered) active at VBlank entry.

### 1.2 ngpc_raster API

```c
void ngpc_raster_init(void);               /* Install HBlank ISR */
void ngpc_raster_disable(void);            /* Remove ISR */

/* Scroll table mode (per-scanline scroll) */
void ngpc_raster_set_scroll_table(plane, table_x, table_y);
void ngpc_raster_clear_scroll(void);

/* Callback mode (custom per-line code) */
u8   ngpc_raster_set_callback(line, callback);
void ngpc_raster_clear_callbacks(void);

/* Convenience: parallax bands */
void ngpc_raster_parallax(plane, bands, count, base_x);
```

Video registers are updated mid-frame via the Timer0 HBlank interrupt.
The K2GE applies changes immediately on the next scanline.

Parallax example:

```c
/* 3-layer parallax scrolling */
RasterBand layers[] = {
    {   0,  64 },   /* sky:    0.25x speed (lines 0-49)   */
    {  50, 128 },   /* trees:  0.50x speed (lines 50-99)  */
    { 100, 256 },   /* ground: 1.00x speed (lines 100-151)*/
};
ngpc_raster_init();

/* In game loop: */
ngpc_raster_parallax(GFX_SCR1, layers, 3, camera_x);
```

### 1.3 HBlank Timing Constraints

- The HBlank ISR must be **extremely fast** (~5 µs per scanline at 6.144 MHz ≈ 30 cycles).
- Only write 1-2 registers per HBlank.
- Timer0 is a shared resource — see §6.5 for conflict rules.

---

## 2. Palette Effects — ngpc_palfx

### 2.1 API

```c
/* Fade */
u8 ngpc_palfx_fade(plane, pal_id, target_colors, speed);
u8 ngpc_palfx_fade_to_black(plane, pal_id, speed);
u8 ngpc_palfx_fade_to_white(plane, pal_id, speed);

/* Cycle (water / lava / rainbow) */
u8 ngpc_palfx_cycle(plane, pal_id, speed);

/* Flash (damage / selection) */
u8 ngpc_palfx_flash(plane, pal_id, color, duration);

/* Control */
void ngpc_palfx_update(void);     /* Call once per frame */
void ngpc_palfx_stop(slot);       /* Stop + restore original palette */
void ngpc_palfx_stop_all(void);
u8   ngpc_palfx_active(slot);     /* Returns 1 if effect is running */
```

Supports up to **4 simultaneous effects**.

- **Fade**: interpolates each R/G/B channel independently.
- **Cycle**: rotates colors 1-2-3; color 0 (transparent) is never touched.
- **Flash**: holds a solid color for `duration` frames, then restores.

### 2.2 Usage Examples

```c
/* Fade to black on scene transition (speed 2 = ~0.5 s) */
ngpc_palfx_fade_to_black(GFX_SCR1, 0, 2);
while (ngpc_palfx_active(0)) {
    ngpc_vsync();
    ngpc_palfx_update();
}

/* Damage flash: 6 frames in white */
ngpc_palfx_flash(GFX_SPR, player_pal, RGB(15, 15, 15), 6);

/* Water animation: rotate palette every 8 frames */
ngpc_palfx_cycle(GFX_SCR1, WATER_PAL, 8);
```

### 2.3 Edge Cases

- `speed=0` in fade/cycle is clamped to 1 (minimum 1 step per frame).
- `ngpc_palfx_flash(..., duration=0)` returns `0xFF` — no effect created (no-op).

---

## 3. Bitmap Mode — ngpc_bitmap

### 3.1 Overview

The NGPC has no hardware bitmap mode. `ngpc_bitmap` emulates one by assigning
380 unique tiles (filling the 20×19 tile screen) and writing pixels directly into tile RAM.
No flush needed — pixels appear immediately.

### 3.2 API

```c
void ngpc_bmp_init(plane, tile_offset, pal); /* Setup (allocates 380 tiles) */
void ngpc_bmp_pixel(x, y, color);            /* Set pixel (color 0-3) */
u8   ngpc_bmp_get_pixel(x, y);               /* Read pixel back */
void ngpc_bmp_clear(void);                   /* Clear all pixels to 0 */
void ngpc_bmp_line(x1, y1, x2, y2, color);  /* Bresenham line */
void ngpc_bmp_rect(x, y, w, h, color);       /* Rectangle outline */
void ngpc_bmp_fill_rect(x, y, w, h, color);  /* Filled rectangle */
void ngpc_bmp_hline(x, y, w, color);         /* Fast horizontal line */
void ngpc_bmp_vline(x, y, h, color);         /* Vertical line */
```

Usage example:

```c
ngpc_bmp_init(GFX_SCR1, 0, 0);
ngpc_gfx_set_palette(GFX_SCR1, 0, RGB(0,0,0), RGB(15,0,0), RGB(0,15,0), RGB(15,15,15));
ngpc_bmp_line(0, 0, 159, 151, 1);  /* red diagonal */
```

### 3.3 Tile Budget

| Resource | Amount |
|----------|--------|
| Tiles consumed | 380 of 512 |
| Tiles remaining for text/sprites | 132 |
| RAM cost | 0 (all writes go directly to VRAM) |

> Not compatible with tilemap-based gameplay — use only in dedicated bitmap screens
> (title art, debug overlays, etc.).

---

## 4. Text Rendering — ngpc_text

### 4.1 API

```c
void ngpc_text_print(plane, pal, x, y, "string");
void ngpc_text_print_dec(plane, pal, x, y, value, digits);  /* zero-padded */
void ngpc_text_print_num(plane, pal, x, y, value, digits);  /* space-padded */
void ngpc_text_print_hex(plane, pal, x, y, value, digits);  /* hex 16-bit */
void ngpc_text_print_hex32(plane, pal, x, y, value);        /* hex 32-bit (8 digits) */
void ngpc_text_tile_screen(plane, pal, map);                 /* fill 20x19 from array */
```

### 4.2 Usage Notes

- Requires `ngpc_load_sysfont()` to have been called first.
- Printable ASCII maps to tile indices `0x20-0x7F` (tiles 32-127).
- Tile slots 32-127 are reserved for the system font. Load custom tiles at 128+.
- Use tilemap-based text via `ngpc_text_print` rather than bitmap mode when possible —
  it uses far fewer tiles and allows mixing text with sprite/tilemap gameplay.

---

## 5. VRAM Queue — ngpc_vramq

### 5.1 Purpose

Queue VRAM writes during gameplay, then flush them all safely during VBlank.
This prevents visual glitches caused by writing to VRAM mid-frame while the K2GE
is rendering.

### 5.2 API

```c
void ngpc_vramq_init(void);                      /* Reset queue state */
u8   ngpc_vramq_copy(dst, src, len_words);        /* Queue u16 copy */
u8   ngpc_vramq_fill(dst, value, len_words);      /* Queue u16 fill */
void ngpc_vramq_flush(void);                      /* Flush all pending commands */
void ngpc_vramq_clear(void);                      /* Drop pending commands */
u8   ngpc_vramq_pending(void);                    /* Count pending commands */
u8   ngpc_vramq_dropped(void);                    /* Count rejected commands */
void ngpc_vramq_clear_dropped(void);              /* Reset drop counter */
```

`ngpc_sys` calls `ngpc_vramq_flush()` automatically each VBlank — no manual flush needed
in most setups.

### 5.3 Implementation Notes

- `dst` must be inside VRAM (`0x8000-0xBFFF`).
- `src` can be in RAM or ROM (near or far pointer).
- `len_words` is in `u16` units, not bytes.
- Queue capacity: `VRAMQ_MAX_CMDS` commands (currently 16).
- If the queue is full, the command is rejected and the drop counter increments.
- `CMD_COPY` uses an ASM helper (`ngpc_memcpy_w` — LDIRW) for speed.
- `CMD_FILL` is implemented in C (no hardware FILL equivalent).

### 5.4 Homebrew Tile Queue Pattern

Before a dedicated `ngpc_vramq` module existed, homebrews used a simple manual queue
to batch tilemap updates and avoid mid-frame glitches:

```c
typedef struct TileQueue {
    u8  x, y;
    u8  frame;
    u16 tile;
    u8  palette;
    u8  delete_tile;
} TileQueue;

TileQueue tile_queue[256];
u8 tile_queue_count;

/* End of frame: flush */
for (u8 i = 0; i < tile_queue_count; i++)
    put_tile(SCR2, tile_queue[i].palette, tile_queue[i].x,
             tile_queue[i].y, tile_queue[i].tile);
tile_queue_count = 0;
```

This pattern is functionally equivalent to `ngpc_vramq` — accumulate changes during the
frame, apply them all at the end (close to VBlank) to avoid tearing. The dedicated module
is the preferred solution.

---

## 6. Raster Chain — CPU Splits (Optional)

### 6.1 Overview

A CPU-based split-screen technique derived from platformer reverse engineering.
Splits the scanline via Timer0 IRQ with dynamic `TREG0` reprogramming.
Each IRQ writes scroll registers for the current zone, then reprograms `TREG0` with
the delta to the next split.

**Advantages over MicroDMA raster (`ngpc_dma_raster`):**

| | Raster Chain (CPU) | DMA Raster |
|--|----|----|
| VBlank execution | Normal — watchdog fed | DMA active = watchdog off risk |
| MicroDMA required | No | Yes |
| RAM cost | Low (no 152-entry table) | ~300 bytes for table |
| Precision | ±1 scanline | Per-scanline |
| Max splits/frame | 8 (`RCHAIN_MAX_SPLITS`) | 152 (one per scanline) |

### 6.2 Timer0 Sub-Scanline Values

At 6.144 MHz, 1 scanline ≈ 6.5 µs ≈ 40 cycles. Some platformers perform **sub-scanline splits**
(TREG0 < 40 = less than one full line). The TREG0 value = CPU cycles before the next IRQ.

Values proven by reverse engineering of a commercial platformer:

| TREG0 | Cycles | Duration |
|-------|--------|----------|
| `0x18` (24) | 24 | ~3.9 µs |
| `0x39` (57) | 57 | ~9.3 µs |
| `0x41` (65) | 65 | ~10.6 µs |

### 6.3 API

```c
void ngpc_rchain_init(void);                              /* Init (Timer0 not started) */
void ngpc_rchain_arm(const RChainSplit *splits, u8 count); /* Arm for next frame (call from VBlank) */
void ngpc_rchain_disarm(void);                             /* Stop Timer0 */
```

`RChainSplit` structure:

```c
typedef struct {
    u8 line;    /* Scanline where this split takes effect */
    s16 scr1x, scr1y;
    s16 scr2x, scr2y;
} RChainSplit;
```

### 6.4 Example — 3-Zone Parallax

```c
#include "ngpc_raster_chain/ngpc_raster_chain.h"

/* Parallax: fixed HUD + near plane + far plane */
static const RChainSplit splits[] = {
    /*  line  scr1x      scr1y  scr2x   scr2y */
    {    0,   0,         0,     0,      0     },  /* baseline */
    {   80,   cam_x / 2, 0,     cam_x,  0     },  /* parallax zone */
    {  128,   0,         0,     0,      0     },  /* fixed HUD */
};

/* In VBlank: */
ngpc_rchain_arm(splits, 3);

/* When no longer needed: */
ngpc_rchain_disarm();
```

### 6.5 Resource Conflict with DMA Raster

Timer0 (HBlank) is shared between:
- `ngpc_raster` (callback/scroll table mode)
- `ngpc_dma_raster` (MicroDMA trigger via Timer0)
- `ngpc_raster_chain` (CPU split mode)

**Rule:** only one system can own Timer0 at a time.

Useful exception: MicroDMA on Timer0 + a second effect on Timer1
(Timer1 clocked from Timer0 overflow — see [DMA.md](DMA.md)).

### 6.6 Performance: Per-Scanline Cost and the One-Split HUD Pattern (HW-Validated)

A full per-scanline scroll table (`ngpc_raster_set_scroll_table()`, `TREG0 = 1`,
ISR every line) is **the classic NGPC performance trap**. It runs fine on the NeoPop
emulator but can drop a real cartridge to **< 1 fps**, because:

- 152 visible scanlines × 60 fps = **9 120 IRQ/s**.
- Each IRQ costs ≈ 80 cycles (cc900 prologue ~30 + `HW_RAS_V` read + branches +
  2 I/O stores + epilogue + RETI), so ≈ **12 000 cycles/frame ≈ 12 % of the CPU budget**
  burned in context-switch alone (frame budget ≈ 102 400 cycles, see
  [Game-Loop.md §4.1](../05_Systems/Game-Loop.md)).
- **NeoPop does not charge the IRQ/context-switch cost** → the overload is invisible in
  the emulator. Validate raster cost on real hardware (or a cycle-accurate emulator).

**If you only need to freeze a HUD band, use a single split — one IRQ per frame.**
This pattern is hardware-validated (HUD band, 60 fps stable):

```c
#define HUD_SCROLL_Y  104u   /* HUD band sits at tile row 30+, physical scanline 136+ */

/* Mini Timer0 ISR: 2 I/O writes + stop. Fits the ~30-cycle HBlank budget (see §1.3). */
static void __interrupt isr_hud_split(void) {
    HW_SCR2_OFS_X = 0u;
    HW_SCR2_OFS_Y = HUD_SCROLL_Y;
    HW_TRUN &= (u8)~0x01u;          /* stop Timer0 — fire once per frame */
}

/* Boot: BIOS_INTLVSET is the ONLY way to enable Timer0 IRQ on NGPC (writing INTET
   registers does nothing). ngpc_raster_init() performs the SWI; then take the vector. */
ngpc_raster_init();                 /* SWI 1 BIOS_INTLVSET, Timer0 level 4 */
ngpc_raster_disable();              /* stop the per-scanline ISR immediately */
HW_INT_TIM0 = isr_hud_split;        /* 0x6FD4 — install the mini ISR */

/* Main loop: arm right after ngpc_vsync() (scanline ~0), BEFORE the game update. */
while (1) {
    ngpc_vsync();                   /* returns at scanline ~0 (system VBlank ISR done) */
    HW_SCR1_OFS_X = shadow_scr1_x;  /* latch shadow scroll vars (K2GE latches at line 0) */
    HW_SCR1_OFS_Y = shadow_scr1_y;
    HW_SCR2_OFS_X = shadow_scr2_x;
    HW_SCR2_OFS_Y = shadow_scr2_y;
    HW_TREG0 = 136u;                /* fire ISR at scanline 136 */
    HW_TRUN |= 0x01u;               /* arm */
    game_update();                  /* writes shadow_* for NEXT frame, not HW directly */
}
```

Why arm **before** the update: `TREG0 = 136` fires 136 lines after the arm point. If you
arm *after* a variable-duration update, the fire scanline jitters frame-to-frame (HUD
flickers, or the split lands in VBlank and is skipped). Arming right after `ngpc_vsync()`
pins the arm at scanline 0 ± 1, so the split always lands on line 136.

Two gotchas that cost real debug time:

- **Keep the ISR to ≤ 2 register writes.** Writing all four scroll registers
  (SCR1_X/Y + SCR2_X/Y) overruns the ~30-cycle HBlank window; the last write lands on
  the *next* scanline → visible tile glitches. Put the camera scroll in the
  scanline-0 latch (above), and only freeze SCR2 in the ISR.
- **Reset the shadow scroll vars when you disable the split**, not just the hardware
  registers. A static screen that never calls the camera update will otherwise re-latch
  the previous scene's `cam_x/cam_y` on its first frame → whole background shifted (but
  collisions still correct, because they read the logical map, not the scroll regs).
- **Do not override `HW_INT_VBL`** for this — a custom VBlank ISR vector was observed to
  be ignored by NeoPop (works on hardware, breaks in the emulator). Driving the arm from
  the main loop after `ngpc_vsync()` works on both.

---

## 7. Window Animation (Optional)

Module: `optional/ngpc_winani/`

### 7.1 Overview

Animates the K2GE hardware window (`HW_WIN_X/Y/W/H`), producing a wipe open/close
transition effect derived from puzzle/sports-game reverse engineering.

- **Open**: expand from center (or from closed state)
- **Close**: contract toward center

`HW_WIN` defines the **visible area** of the screen:
- Full screen: X=0, Y=0, W=160, H=152
- Fully closed: W=0, H=0

### 7.2 API

```c
void ngpc_win_init(void);                 /* Full screen, no animation */
void ngpc_win_set_full(void);             /* Instant: full screen */
void ngpc_win_set_closed(void);           /* Instant: fully closed */
void ngpc_win_open(u8 speed);             /* Start open animation (px/side/frame) */
void ngpc_win_close(u8 speed);            /* Start close animation (px/side/frame) */
u8   ngpc_win_update(void);              /* Call from VBlank — returns 1 when done */
u8   ngpc_win_busy(void);                /* Returns 1 if animation in progress */
```

### 7.3 Usage Examples

```c
/* Scene transition: close then reopen */
ngpc_win_close(4);            /* ~20 frames to close (4 px/side/frame) */
while (ngpc_win_busy()) {
    ngpc_vsync();
    ngpc_win_update();
}
load_next_scene();
ngpc_win_open(4);

/* Intro: start closed, then open slowly */
ngpc_win_set_closed();
ngpc_win_open(2);             /* ~38 frames to open */

/* In VBlank ISR (or VBlank callback): */
ngpc_win_update();
```

> Speed = pixels expanded/contracted per side per frame.
> Both horizontal and vertical sides move simultaneously.
> speed=4 with 160-wide screen: 160/2/4 = 20 frames to close.

---

## Quick Reference

| Module | Header | Key function | Notes |
|--------|--------|-------------|-------|
| `ngpc_raster` | `ngpc_raster.h` | `ngpc_raster_init()` | Timer0 HBlank ISR |
| `ngpc_palfx` | `ngpc_palfx.h` | `ngpc_palfx_update()` | Max 4 effects |
| `ngpc_bitmap` | `ngpc_bitmap.h` | `ngpc_bmp_init()` | 380 tiles consumed |
| `ngpc_text` | `ngpc_text.h` | `ngpc_text_print()` | Requires sysfont loaded |
| `ngpc_vramq` | `ngpc_vramq.h` | `ngpc_vramq_copy/fill()` | 16-cmd queue, auto-flushed |
| `ngpc_raster_chain` | `optional/ngpc_raster_chain/` | `ngpc_rchain_arm()` | CPU splits, no DMA |
| `ngpc_winani` | `optional/ngpc_winani/` | `ngpc_win_open/close()` | HW_WIN wipe effect |

| Item | Value | Notes |
|------|-------|-------|
| HBlank budget | ~30 cycles (~5 µs) | Write 1-2 regs max |
| DMA + VBlank | Forbidden | Powers off watchdog |
| Timer0 owner | Exclusive | Raster OR DMA raster OR raster chain |
| ngpc_bitmap tiles | 380/512 | Remaining: 132 for text/sprites |
| Sysfont tile range | `0x20-0x7F` (32-127) | Load custom tiles at 128+ |
| VRAMQ capacity | 16 commands | Overflow = silent drop, use `ngpc_vramq_dropped()` |
| VRAMQ len unit | u16 words | Not bytes |
| Window full screen | X=0, Y=0, W=160, H=152 | Some games use 159/151 — HW ambiguity |
| RChain max splits | 8 | RCHAIN_MAX_SPLITS |
| RChain precision | ±1 scanline | vs per-scanline for DMA raster |
| Palette fade speed | 1 = fastest | speed=0 clamped to 1 |
| Palette flash duration=0 | Returns 0xFF, no effect | Safe no-op |
| Palette cycle color 0 | Never touched | Color 0 = transparent |

---

## See Also

- [DMA.md](DMA.md) — MicroDMA raster (Timer0 trigger, INTTC0 re-arm, DMA+VBlank rule)
- [Tilemaps-and-Scrolling.md](Tilemaps-and-Scrolling.md) — SCR1/SCR2 scroll registers, tilemap entry format
- [Hardware-Registers.md](../01_Hardware/Hardware-Registers.md) — HW_WIN registers, Timer0/1 (T01MOD, TREG0, TRUN)
- [Game-Loop.md](../05_Systems/Game-Loop.md) — VBlank ISR structure, DMA stop rules, frame budget
