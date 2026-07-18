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

- The HBlank ISR must be **extremely fast**: the safe HBlank *write window* is
  ~5 µs ≈ 30 cycles at 6.144 MHz. (That is the ISR budget, not the scanline period —
  a full scanline is ~515 cycles / ~84 µs; see §6.2.)
- Only write 1-2 registers per HBlank.
- Timer0 is a shared resource — see §6.5 for conflict rules.

### 1.4 Polled raster (no IRQ) — emulator-robust fallback

**Emulator caveat — NeoPop is not a reference.** NeoPop's Timer-0/HBlank interrupt
emulation does **not** match hardware, and reports go both ways: the IRQ silently
never firing (a pseudo-3D road stays perfectly straight while the frame loop runs
fine), *and* firing **without** the BIOS `INTLVSET` that real hardware requires
(§6.5b) — so a split that works in NeoPop can be dead on cartridge. Either way, do
not trust NeoPop to reproduce Timer-0 raster behaviour; validate on real hardware or
a cycle-accurate emulator. For emulator-only prototyping of the per-scanline *look*,
drive it by polling the beam instead of by interrupt:

**Fallback that works on any emulator that updates `RAS.V` (0x8009):** drive the per-scanline writes
by **polling the beam position** from the main loop instead of from an ISR — no Timer-0, no vector:

```c
#define HW_RAS_V    (*(volatile u8 *)0x8009)   /* current scanline (read-only) */
#define HW_SCR1_X   (*(volatile u8 *)0x8032)   /* SCR1 X scroll                */

/* Apply a 152-entry per-line X scroll table by polling. Call once per frame
   from the main loop (do NOT install the HBlank ISR in this mode). */
static void raster_apply_polled(const u8 *table_x)
{
    u8 y;
    while (HW_RAS_V <  152u) { }   /* ensure we're in VBlank first      */
    while (HW_RAS_V >= 152u) { }   /* wait for line 0 of active display  */
    for (y = 0u; y < 152u; ++y) {
        while (HW_RAS_V < y) { }   /* spin until the beam reaches line y */
        HW_SCR1_X = table_x[y];
    }
}
```

- **Pro:** works on NeoPop and anything with a working `RAS.V`; no interrupt setup; deterministic.
- **Con:** the spin loop consumes the whole active-display period — game logic must run in VBlank.
  Fine for a tech demo / single-effect screen; for a full game prefer the HBlank IRQ or the MicroDMA
  path (§8.6 / [DMA.md](DMA.md)), validated on a faithful emulator (Mednafen) or hardware.

> Rule of thumb: prototype the per-scanline look with **polled raster** (instant, emulator-proof),
> then switch to the **Timer-0 IRQ** or **MicroDMA** for the shipping build.

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

### 6.2 What TREG0 counts — HBlanks, not CPU cycles

**With the clock source these modules use — `T01MOD` bits 1-0 = `00` = TI0 (the
K2GE H-int) — `TREG0` counts HBlanks (scanlines): one tick per visible line.**
`TREG0 = 1` fires every line; `TREG0 = 136` fires 136 lines after the arm point.
This is the official Toshiba pattern (SDK `8Bit.txt`, *"H-int Setting"*: *"generates
H-int every line; if it is to be generated every 4 lines, set TREG0 to 0x04"*).

A full scanline lasts **~515 CPU cycles (~84 µs)** at 6.144 MHz — silicon-measured
(515 cyc/line × 199 lines/frame ≈ 102 485 cyc/frame ≈ 59.95 Hz). Do **not** confuse
this with the ~30-cycle HBlank *write window* (§1.3): 515 cycles is how long a line
lasts; ~30 cycles is only how much of it is safe to spend inside the ISR.

> **On "sub-scanline" splits.** To fire faster than once per line you must switch
> `T01MOD` off TI0 onto a *prescaled internal CPU clock* (φ/T1, T4 or T16) — a
> different configuration from the one `ngpc_raster`/`ngpc_raster_chain` program.
> Note the finest available step is φ/T1 = **128 CPU cycles ≈ 20.8 µs per tick**, so
> genuine single-microsecond intervals are not reachable on NGPC. An earlier
> "TREG0 = CPU-cycle" table here (24→3.9 µs, etc.) implied 1 tick = 1 CPU cycle,
> which no Timer0 clock source produces; it was unverified and has been removed.

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
    u8 scr1x, scr1y;   /* SCR1 X/Y scroll offset written at this split */
    u8 scr2x, scr2y;   /* SCR2 X/Y scroll offset */
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

### 6.5b Enabling the Timer0 HBlank IRQ — the #1 silent-failure gotcha

If you use the template's `ngpc_raster_init()` (or `ngpc_rchain_init()`), it already
does this for you — skip to §6.6. **Read this only if you hand-roll your own
`__interrupt` handler**, because that is where people lose hours.

**Writing the interrupt-enable registers (`HW_INTET01`, etc.) directly does NOT enable
the Timer0 IRQ on NGPC.** The BIOS owns the interrupt-level hardware. You can configure
Timer0 perfectly (it ticks every HBlank), install your ISR at the right vector, and the
ISR still **never fires** — no split, scroll registers keep their initial value, and it
looks like your handler is wrong when the handler is fine. On NeoPop the IRQ doesn't
fire *at all* (§1.4), so you cannot even catch this in that emulator.

The activation must go through the BIOS with `SWI 1` (`BIOS_INTLVSET`). Full sequence
for a HBlank (per-scanline) Timer0 IRQ:

```c
/* 1. Configure Timer0 in HBlank mode (documented in the Toshiba 8Bit.txt "H-int" section). */
HW_TRUN   &= (u8)~0x01u;      /* stop Timer0 before reconfiguring          */
HW_T01MOD &= (u8)~0xC3u;      /* 8-bit timer, clock = TI0 (K2GE H-int)     */
HW_TREG0   = 0x01u;           /* interval = 1 scanline (raise for a split) */

/* 2. CRITICAL: SWI 1 BIOS_INTLVSET actually routes the IRQ to the CPU. */
__asm("ldb rb3, 4");          /* priority level 4 (VBlank-level; fires with EI 0) */
__asm("ldb rc3, 2");          /* interrupt number 2 = Timer0                      */
__asm("ldb rw3, 4");          /* rw3 = BIOS_INTLVSET (= 4)                         */
__asm("swi 1");               /* -> BIOS installs the level; NOW the IRQ can fire  */

/* 3. Point the vector at your ISR and start the timer. */
HW_INT_TIM0 = my_isr;         /* 0x6FD4 — Timer0 interrupt vector */
HW_TRUN    |= 0x01u;          /* start Timer0                     */
```

- `rb3` priority: `0x03` (level 3, per the Toshiba doc) and `0x04` (VBlank-level, fires
  under `EI 0`) both work — pick per your EI mode. If unsure, use `4`.
- Without the `SWI 1` block, the module/ISR stays **silently inactive**. This applies to
  any custom Timer0/HBlank handler, not just raster.
- For a single-band HUD split you normally do **not** want `TREG0 = 1` (every line) —
  see §6.6, which sets `TREG0` to the split scanline and fires the ISR once per frame.

**Table vs `SWI 1` — same mechanism, not a contradiction.** `BIOS_INTLVSET` is listed
in [BIOS.md](../01_Hardware/BIOS.md) as a "Table `0xFFFE00`" call. That is the same
thing: `swi 1` *is* the table dispatcher — it computes `pc = [0xFFFE00 + rw3*4]`, so
loading `rw3 = 4` reaches the identical INTLVSET routine. Use the `swi 1` form above.

**This is not guesswork.** The register convention (`rw3 = 4`, `rb3 = level`,
`rc3 = 2` = Timer0) is the official Toshiba SDK sequence (`8Bit.txt`, "H-int Setting")
and is confirmed on real cartridges — e.g. Fatal Fury enables its H-blank IRQ at boot
with exactly this `INTLVSET` call. As of the current template, `ngpc_raster_init()`
and `ngpc_rchain_init()` perform this `swi 1` for you (validated on hardware).

**Does the TI0/HBlank clock tick during VBlank?** No. The K2GE emits 152 H-int pulses
per frame — one per visible line, plus a single pulse just before line 0 — and is
silent across vertical blanking. So a `TREG0 = N` armed anywhere in VBlank still fires
N lines into the *next* visible frame; the fire line does not drift with where in
VBlank you arm (which is why the §6.6 `TREG0 = 136` recipe is robust).

### 6.6 Performance: Per-Scanline Cost and the One-Split HUD Pattern (HW-Validated)

> **Before you split: do you even need a raster interrupt?**
> The most common reason people reach for a HUD split is a status bar that "pops" or
> smears as the playfield scrolls. In most cases that is self-inflicted: the HUD is
> being scrolled along with the level. The NGPC has **two independent scroll planes**
> (SCR1/SCR2), so the cheapest fix is to **put the HUD on a plane you never scroll**
> and scroll only the other one:
>
> - Playfield → SCR1 (scrolls with the camera).
> - HUD band / score / lives → SCR2 (scroll registers left at 0, sits in front).
>   Keep gameplay objects out of the HUD rows so nothing scrolls under it.
>
> This needs **zero interrupts, zero inline assembly, and cannot stutter** — the HUD
> is simply never in a moving plane. It is the right answer whenever you can spare one
> plane for the HUD (i.e. you are not already using *both* planes for parallax).
> Reach for the raster split below **only** when you genuinely need *both* planes to
> scroll and still want a static band. A common mistake is scrolling both planes and
> then trying to "hold" the HUD with per-tile tricks (e.g. baking several vertical
> variants of each HUD tile and swapping them) — that both wastes tile RAM and
> reintroduces the pop it was meant to cure. Fix the plane assignment first.

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

> **Field report — prefer MicroDMA for a HUD band that must not move.** The CPU
> one-split pattern below is hardware-validated at a steady load, but a shipped
> project reported it **flickering on real hardware during heavy scrolling**: the
> ISR is serviced with variable latency when the CPU is busy, so the split line
> jitters by a scanline or two and the band visibly wobbles. A **MicroDMA raster
> split triggered by Timer0/HBlank** rewrites the scroll register per line with
> **zero CPU cost and no jitter**, and was the fix that made the bar rock-solid.
> Use `ngpc_dma_raster` (§8.6, [DMA.md](DMA.md)) when the band must be perfectly
> static under load; use the CPU split below when the scene is light or you cannot
> spare a DMA channel. Both beat baking pre-shifted tile variants, which wastes
> large amounts of tile RAM (that project recovered ~126 tiles by dropping it).

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

**Shipped reference implementation — `kuroi_dokutsu` (`src/main.c`).** This exact pattern
ships and is hardware-validated in that project; read it there if you want the full
context. Two details worth copying:

- It keeps the scroll values in **`volatile` shadow variables** written by the camera
  code, and pushes them to the registers in a single dedicated
  `hud_apply_scroll_and_arm()` called right after `ngpc_vsync()` — so the camera logic
  never touches hardware registers directly, and the arm point stays pinned to line ~0.
- The boot sequence is the non-obvious part and is exactly the three steps above:
  `ngpc_raster_init()` (performs the BIOS `INTLVSET` that actually enables the IRQ —
  §6.5b), then `ngpc_raster_disable()` (stop the per-line ISR immediately, otherwise it
  fires every scanline and destroys the framerate), then override `HW_INT_TIM0` with the
  2-write mini ISR.

If the band flickers on hardware under heavy scrolling, move it to MicroDMA — see the
field report above and [DMA.md §7.5](DMA.md).

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

## 8. Forward-View Pseudo-3D Road (Perspective Scaling)

A forward-facing road/track view with a depth-scaling illusion (the NGPC has **no hardware
scaling**) combines two independent systems, confirmed by reverse engineering of a commercial
forward-view rail/racing game:

- **Background line-scroll** (§8.1–8.3) — warps the two scroll planes per scanline so the
  ground/scenery fans out in perspective.
- **Sprite depth scaling** (§8.4) — swaps pre-baked metasprites by distance for the relief
  (signs, buildings, rival vehicles). See [Gameplay-Patterns §5.5b](../06_Pipeline-and-Patterns/Gameplay-Patterns.md).

### 8.1 Per-line scroll handler (HBlank ISR)

The road is a flat tilemap; the perspective comes from giving **each scanline a different
horizontal scroll**. A fast interrupt fires once per visible line and copies the next entry of a
per-line scroll table into the plane scroll registers. The K2GE applies the change on the next
scanline (§1.2). Using one table per plane gives parallax between the near ground and the far
scenery.

```c
/* Timer-0 HBlank ISR — register-bank-switched, resident in fast RAM. 1-2 writes MAX (§1.3). */
__interrupt void isr_road_line(void) {
    HW_SCR2_X = *p_far++;    /* far / scenery plane X for this scanline */
    HW_SCR1_X = *p_near++;   /* near / ground  plane X for this scanline */
    /* optional: a per-line palette/backdrop write gives a sky->ground gradient */
}
```

### 8.2 Per-line scroll tables (double-buffered)

- One table of **152 entries** per plane (152 = visible scanlines = window `WSI.V`).
- **Double-buffered (ping-pong):** the line ISR consumes buffer A while the game fills buffer B
  for the next frame; swap the pointers in the VBlank ISR — no tearing.
- Keep the table-pointer registers in CPU **register bank 2** so they persist and auto-advance
  across all the line interrupts of one frame (the ISR does `ldf 2` on entry).

### 8.3 Perspective curve generator

Fill the next buffer from a small per-segment "road-shape" profile, **sampled at a non-linear
(quadratic) index** so near bands spread fast and far bands compress — that is the vanishing-point
fan:

```c
/* band_offset = profile[(step*k)^2] - profile[0]   (k = 1..N bands, N≈48) */
for (u8 k = 1; k <= NBANDS; ++k) {
    u16 idx = (u16)(STEP * k);
    idx = (u16)(idx * idx);                 /* quadratic index into the profile LUT */
    table[NBANDS - k] = profile[idx] - profile[0];
}
/* then interpolate the N band offsets across the 152 scanlines */
```

The base `profile[]` is a ROM LUT chosen by the current **track-segment type** (straight / curve /
slope): a flat profile = straight road, a ramped profile = a curve or a widening. Add the steering
angle as a per-frame bias to lean the whole road left/right.

### 8.4 Sprite relief (depth scaling)

Objects standing above the road (signs, gates, buildings, rival vehicles) are pre-baked
metasprites selected by camera distance. See
[Gameplay-Patterns §5.5b](../06_Pipeline-and-Patterns/Gameplay-Patterns.md) for the depth→zoom
formula, the screen-X/Y convergence tables, the Z-sorted display list and the culling rules.

### 8.5 Forward motion

Advance the camera each frame and scroll **both planes' Y registers** at the (parallaxed) road
speed. The 32-tile-tall tilemap (256 px) wraps seamlessly; recompose the scenery rows when the
track segment changes (stream the new tile rows during VBlank via the VRAM queue, §5).

### 8.6 Arming and cost — two implementations

Arm Timer-0 in HBlank mode (clock source `TI0`, `TREG0 = 1` → every line, vector `0x6FD4`,
`INTET01` level). The **same** per-line scroll can be driven two ways:

| | CPU HBlank ISR | MicroDMA (Timer-0 trigger) |
|--|----|----|
| CPU cost | ~12 % of the frame budget at 152 lines (the raster trap — §6.6) | ~0 cycles per line |
| Setup | install one ISR | program one DMA channel per plane, re-arm in VBlank |
| Best for | few splits / simple effects | a busy racer (CPU stays free for game logic) |

Use one DMA channel per plane (fixed destination register, source table `++`). Re-arm in the
DMA done-IRQ during VBlank and flip the double-buffer there. **Never leave a raster DMA active at
VBlank entry** (§1.1 — it powers off the watchdog). Timer-0 is exclusive (§6.5).

### 8.7 Turns — how the road bends (data-driven)

The lateral bend is a separate, data-driven layer on top of the perspective. In the source game it
is a **train** (scripted track), so the player controls speed, not steering — but the same model
takes a steering input for a free-steer racer.

- **The track is a sequence of segments.** A current-segment index advances as the camera moves
  forward; a track table (segment → curvature, length) drives the sequence.
- **Each segment carries a signed curvature** (and the source game also stores it as a pre-baked
  signed *profile LUT* per segment: e.g. a ramp `+8 … 0 … −26 … back` = an S-bend, a constant
  value = a held curve, zeros = straight).
- **A per-frame "curve offset"** derived from the forward distance slides where the curve is
  sampled, so the bend travels down the road and under the player as you progress.
- **The road builder double-integrates** the curvature down the scanlines (slope, then position) —
  the classic pseudo-3D road. Near scanlines bend more than far ones (perspective), which is what
  produces the visible curve.

### 8.8 Reference implementation (C)

Complete, adaptable reference (structure from a disassembled commercial racer). **C89**, using the
template raster API (`fx/ngpc_raster.h`) and GFX API (`gfx/ngpc_gfx.h`). The `ngpc_raster` module
already owns the Timer-0 HBlank ISR and consumes the per-line table — you only **build the `u8[152]`
tables** and hand them over with `ngpc_raster_set_scroll_table()`. The CPU computes the tables once
per frame.

```c
#include "ngpc.h"
#include "fx/ngpc_raster.h"     /* ngpc_raster_init / ngpc_raster_set_scroll_table */
#include "gfx/ngpc_gfx.h"       /* ngpc_gfx_scroll, GFX_SCR1 / GFX_SCR2            */

#define NLINES    152u      /* visible scanlines (= window WSI.V)            */
#define HORIZON    56u      /* first road scanline; lines above = flat sky   */
#define ROAD_CX    80u      /* screen centre X (160/2)                       */
#define Z_NEAR    256       /* depth covered by the nearest scanline         */
#define DZ_STEP    24       /* extra depth per scanline upward (perspective) */
#define FX_BITS     8       /* fixed-point fractional bits                   */

/* The "turns" are data: a sequence of segments, each with a signed curvature. */
typedef struct { u16 length; s16 curve; } RoadSeg;

static const RoadSeg track[] = {
    { 2000,   0 },   /* straight      */
    { 1500,  18 },   /* gentle right  */
    { 1000,   0 },   /* straight      */
    { 1800, -28 },   /* sharp left    */
    { 1200,  40 }    /* sharp right   */
};
#define TRACK_LEN  ((u8)(sizeof(track) / sizeof(track[0])))

static u8  road_x[NLINES];   /* SCR1 X per scanline (fed to the raster table) */
static u8  sky_x [NLINES];   /* SCR2 X per scanline                           */
static u32 cam_z;            /* distance travelled along the track            */
static u32 seg_base_z;       /* Z where the current segment began             */
static u8  cam_seg;          /* current segment index                         */
static u8  road_y;           /* vertical scroll = forward motion (ground)     */
static u8  sky_y;            /* vertical scroll (scenery, parallax)           */
static s16 steer;            /* player steering bias (omit for a scripted track) */

/* Build the per-line scroll tables for the current camera/steer state.
   Walk from the horizon down; double-integrate curvature (slope, then
   position). Depth per scanline grows downward, so near lines bend more
   than far ones = perspective. */
static void road_build(void)
{
    s32 z;
    s32 dz;
    s32 x;
    s32 dx;
    u8  seg;
    u32 seg_end;
    u8  y;

    z       = (s32)cam_z;
    dz      = Z_NEAR;
    x       = (s32)steer << FX_BITS;
    dx      = 0;
    seg     = cam_seg;
    seg_end = seg_base_z + track[seg].length;

    for (y = 0u; y < HORIZON; ++y) {          /* sky band: flat */
        road_x[y] = (u8)ROAD_CX;
        sky_x[y]  = (u8)ROAD_CX;
    }
    for (y = HORIZON; y < NLINES; ++y) {
        dx += track[seg].curve;               /* 1st integral: slope    */
        x  += dx;                             /* 2nd integral: position */
        road_x[y] = (u8)(ROAD_CX + (x >> FX_BITS));
        sky_x[y]  = (u8)(ROAD_CX + ((x >> FX_BITS) >> 1));   /* half spread = parallax */
        z  += dz;
        dz += DZ_STEP;                        /* perspective foreshortening */
        if ((u32)z >= seg_end && (u8)(seg + 1u) < TRACK_LEN) {
            ++seg;
            seg_end += track[seg].length;
        }
    }
}

/* One-time setup. */
void road_init(void)
{
    cam_z = 0; seg_base_z = 0; cam_seg = 0; road_y = 0; sky_y = 0; steer = 0;
    ngpc_raster_init();                                 /* install Timer-0 HBlank ISR */
    road_build();
    ngpc_raster_set_scroll_table(GFX_SCR1, road_x, 0);  /* per-scanline X tables */
    ngpc_raster_set_scroll_table(GFX_SCR2, sky_x, 0);
}

/* Per-frame update. Call once per frame (e.g. right after ngpc_vsync()), NOT in an ISR. */
void road_frame(u8 speed, s8 steer_input)
{
    cam_z += speed;
    road_y = (u8)(road_y + speed);                 /* 256px map wraps automatically */
    sky_y  = (u8)(sky_y + (u8)(speed >> 1));        /* horizon parallax              */
    steer  = steer_input;

    while (cam_z >= seg_base_z + track[cam_seg].length
           && (u8)(cam_seg + 1u) < TRACK_LEN) {
        seg_base_z += track[cam_seg].length;        /* segment progression */
        ++cam_seg;
    }

    road_build();
    ngpc_raster_set_scroll_table(GFX_SCR1, road_x, 0);
    ngpc_raster_set_scroll_table(GFX_SCR2, sky_x, 0);
    ngpc_gfx_scroll(GFX_SCR1, 0u, road_y);          /* forward motion (vertical scroll) */
    ngpc_gfx_scroll(GFX_SCR2, 0u, sky_y);
}

/* Depth-scaled relief (signs, rivals): pre-baked metasprite chosen by distance.
   persp_x/persp_y = 256-entry vanishing-point convergence LUTs (ROM). */
extern const u8 persp_x[256];
extern const u8 persp_y[256];

void road_relief(u32 obj_z, u16 range, u8 type, s16 lateral)
{
    s32 dist;
    u8  zoom;
    u8  sx;
    u8  sy;

    dist = (s32)(obj_z - cam_z);
    if (dist < 0 || dist > (s32)range) {
        return;                                     /* behind camera / beyond far plane */
    }
    zoom = (u8)(((u32)range - (u32)dist) / 25u);    /* depth -> zoom level */
    sx   = (u8)(ROAD_CX - persp_x[zoom] + (u8)(lateral >> 4));
    sy   = (u8)(0x50u - persp_y[zoom]);
    if (sy < 8u || sy >= 144u) {
        return;
    }
    /* metasprite_submit(road_frame_list(type, zoom), sx, sy);  -- OAM watermark */
}
```

> C89: all declarations at the top of each block, no declarations inside `for`, no `//`, explicit
> casts to avoid integer promotion. The `ngpc_raster` module owns the Timer-0 HBlank ISR — you only
> build the tables and pass them. For a busy racer use the MicroDMA variant
> (`fx/ngpc_dma_raster.h`): `ngpc_dma_raster_begin(&r, GFX_SCR1, road_x, 0)` once, then
> `ngpc_dma_raster_rearm(&r)` per VBlank (one `NgpcDmaRaster` per plane) — 0 CPU cycles per scanline.
> The double integration (`dx` slope, `x` position) is exactly the source engine's per-line curve
> loop. Anti-tearing: rebuild right after `ngpc_vsync()`, or ping-pong two `road_x[2][152]` buffers.

### 8.9 Build notes — what actually worked (empirically validated)

A minimal forward-view road was built on the base template (cc900, C89) and **confirmed running on
the NeoPop emulator**: centred road, fixed vanishing point, scrolling ground, and the road bending
in curves + under D-pad steering. Four findings made it work — each is a real pitfall, and each
qualifies the idealized description in §8.1–8.8.

**1. A horizontal line-scroll does NOT create the vanishing point.** Per-scanline X scroll is a
*shear*: it turns a rectangle into a parallelogram. It can **bend** the road (curves) but can never
make it **converge** — it cannot scale. A flat road tilemap + line-scroll renders as a *top-down
road that can tilt*, never a perspective road. **The vanishing point must be DRAWN into the tilemap**
as a converging trapezoid (wide at the bottom, narrowing to a point at the horizon); the line-scroll
then only bends that drawn perspective. Draw the art with its VP at screen centre (x=80) so a scroll
of 0 is centred.

```python
# asset generation (PIL): converging trapezoid, VP at screen centre
VP_X = 80; VP_Y = 56; SLOPE = 1.05
for y in range(VP_Y, H):
    hw = int((y - VP_Y) * SLOPE)        # half-width grows below the horizon
    draw_road_span(y, VP_X - hw, VP_X + hw)
```

**2. In this NeoPop build the Timer-0 HBlank IRQ never fired → use polled raster.** With the ISR
installed at the correct vector (`HW_INT_TIM0` = `0x6FD4`) and Timer-0 configured, this NeoPop build
never called it, so the per-scanline table was never applied — the road stayed straight while the
rest of the frame loop ran fine. (NeoPop is **not** a reference: its Timer-0/HBlank emulation is
unreliable and other builds have been seen to fire the IRQ *without* the BIOS `INTLVSET` hardware
requires — see §1.4 / §6.5b.) For emulator-only prototyping, drive the writes by polling `RAS.V` from
the main loop (no IRQ) — see §1.4. Do **not** call `ngpc_raster_init()` in that mode.
For a shipping build, use the Timer-0 IRQ or MicroDMA path and validate on a faithful emulator
(Mednafen) / hardware (open question: does the IRQ path work on Mednafen?).

**3. Curve = ONE curvature value, double-integrated bottom→top.** Two attempts that did *not* bend
the road: (a) seeding steering into the *position* `x` (uniform shift, no bend); (b) letting a large
depth step walk through every segment per build (curvatures average to ~0). Working model: bottom
scanline = player = offset 0; walk **up** toward the horizon integrating a **single** curvature
(`slope += curve; x += slope`). Offset grows quadratically toward the horizon → far road swings, near
road stays put = a real bend. `curve = eased(segment.curve) + steer`; ease with `cur += (target-cur)>>3`.
Keep per-segment curvature small (2–4): it is integrated over ~96 lines. Sim-validated (FX_BITS=9):
curve 1 → 8 px bend at the horizon, 3 → 26 px, 6 → 53 px (bottom always 0).

**4. Forward motion without moving the drawn vanishing point.** You cannot vertically scroll a
drawn-perspective trapezoid (that moves its painted VP). Cheap speed cue: paint the road in two
alternating grey bands and swap the two band colours every few frames (`ngpc_gfx_set_color_direct`)
so the bands appear to travel toward the viewer. Limitation: bands are evenly spaced in *pixels*, not
in depth, so they don't accelerate as they approach — fine for a tech demo. The realistic
alternative is to redraw scenery rows per depth band (the full technique).

**Scope of this validated test vs the full technique:** the test proves the principle (drawn
perspective + line-scroll bend + curve integration) on NeoPop. It uses a single static drawn
trapezoid (not per-segment redrawn geometry), plain double-integration (not a pre-baked quadratic
curve profile), polled raster (not the Timer-0 IRQ), a single buffer (no double-buffer), and
palette-cycling for motion. Depth→zoom relief sprites (the real object "scale") are not yet wired.
Roadmap to fidelity: Timer-0 IRQ or MicroDMA (validate on Mednafen) → pre-baked quadratic curve
profile → double-buffer → depth→zoom relief sprites → optional per-band tilemap recompose.

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
