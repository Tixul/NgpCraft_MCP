# Game Loop

VBlank sync, ISR design, watchdog, frame budget, state machine, and three reference pipeline architectures for NGPC games.

> Note: All files use ASCII only (avoids encoding issues on Windows/PowerShell).

---


## 1. Frame Sync

The NGPC runs at ~60 Hz driven by the VBlank interrupt. All game logic must be
frame-locked to this signal. Two patterns exist in production games.

### 1.1 halt-based sync

The CPU executes `halt` which pauses execution
until the next interrupt fires. The VBlank ISR sets a flag and returns; the main
loop resumes from after the `halt`.

```c
/* In the template (ngpc_vsync): */
void ngpc_vsync(void) {
    __asm("halt");  /* sleep until VBlank ISR runs */
    /* check shutdown after waking */
    if (*(volatile u8 *)0x6F85) ngpc_shutdown();
}
```

### 1.2 Flag-based sync

The VBlank ISR writes `1` to a RAM flag (`0x4000`)
at the end of its work. The main loop polls this flag instead of using `halt`.

```c
/* Flag-based style (allows non-VBL IRQs to fire during wait) */
void wait_vbl(void) {
    volatile u8 *sync = (volatile u8 *)0x4000;
    while (*sync == 0) {}   /* passive wait */
    *sync = 0;              /* reset for next frame */
}
```

Advantage over `halt`: non-VBlank IRQs (Timer0, DMA completion) continue to fire
during the wait. Advantage over CPU polling: no wasted cycles.

### 1.3 Sync counters vs frame counter

Keep two distinct VBlank-counter roles:

- A **monotonic frame counter** that is **never reset** — used for absolute
  timestamps and cooldowns (e.g. `(u8)(now - event_time) >= cooldown`).
- Separate **resettable sync counters** for frame-pacing waits.

Do not reuse one counter for both purposes — resetting the timestamp base
corrupts any cooldown that referenced it.

**N-frame sync via double-sync:** to resume cleanly at the start of a fresh
frame, wait N VBlanks, reset the sync counter, then wait one more VBlank edge.
The trailing single-edge wait guarantees execution resumes at the very start of
a frame rather than partway through the one in which the Nth edge fired.

```c
void wait_n_frames_aligned(u8 n) {
    u8 start = g_vb_counter;
    while ((u8)(g_vb_counter - start) < n) { HW_WATCHDOG = WATCHDOG_CLEAR; }
    /* re-align: wait one more fresh VBlank edge */
    u8 edge = g_vb_counter;
    while (g_vb_counter == edge) { HW_WATCHDOG = WATCHDOG_CLEAR; }
}
```

### 1.4 The 8-bit counter wrap — phantom "frame drops" in your own FPS meter

An 8-bit VBlank counter wraps every **256 frames ≈ 4.27 s** at 60 Hz. The wrap-safe
idiom above (`(u8)(now - start)`) is correct **only for intervals shorter than 256
frames** — it cannot tell one wrap from two, so any measurement spanning the wrap
silently produces garbage.

This bites hardest in a **home-made FPS/profiler display**: a shipped project chased
"random ~3-second drops to single-digit fps" that did not exist — the counter wrapped
mid-measurement and the arithmetic broke, printing a bogus figure while the game was
in fact running at full speed. **Before optimising a reported slowdown, verify the
measurement itself** (e.g. confirm the drop against emulator instrumentation or a
second, independent timer). Rule of thumb: if a "slowdown" recurs on a period suspiciously
close to 4.27 s, it is the wrap, not your game.

- **Fix:** use a **`u16`** frame counter for anything measuring elapsed time or rate
  (a `u16` wraps at 65 536 frames ≈ 18 min), and keep `u8` only for short cooldowns.
- **When the slowdown is real:** heavy scenes can genuinely exceed the per-object
  budget (§4.1). A stable **locked 30 fps with movement speeds rescaled** reads far
  better than a framerate fluctuating between 40 and 60 — that is the trade a shipped
  NGPC project settled on for scenes with 8-10 enemies plus bullets.

---

## 2. Minimal VBlank ISR

### 2.1 Mandatory operations

The VBlank ISR **must never be disabled**. Minimal mandatory content:

1. Feed the watchdog: `HW_WATCHDOG = 0x4E`
2. Check `HW_USR_SHUTDOWN` and request power-off if set

> **Template note:** shutdown is handled in main loop context (`ngpc_vsync()`)
> to avoid calling BIOS from inside an ISR on certain hardware configurations.

### 2.2 Safe pattern

```c
static volatile u8 g_vb_counter = 0;

void __interrupt isr_vblank(void) {
    /* 1. Feed watchdog immediately */
    *(volatile u8 *)0x006F = 0x4E;

    /* 2. Flash OAM shadow -> hardware (256 bytes) */
    /* (LDIRW: 128 u16 words, see Sprites and OAM) */

    /* 3. Push scroll register shadows */
    *(volatile u8 *)0x8032 = scr1_x_shadow;
    *(volatile u8 *)0x8033 = scr1_y_shadow;

    /* 4. Audio tick */
    audio_tick();

    /* 5. Frame counter */
    g_vb_counter++;
}

/* Minimal version if no OAM/scroll update needed: */
void __interrupt isr_vblank_minimal(void) {
    *(volatile u8 *)0x006F = 0x4E;
    g_vb_counter++;
}
```

### 2.3 Startup sequence

```c
void main(void) {
    ngpc_init();            /* installs VBL ISR, inits hardware */
    ngpc_load_sysfont();    /* load ASCII glyphs to tile slots 32-127 */

    /* ... load assets, init game state ... */

    while (1) {
        ngpc_vsync();           /* halt until VBlank */
        ngpc_input_update();    /* snapshot joypad once per frame */
        game_update();
        render();
    }
}
```

**Interrupt setup:**
```c
void init_interrupts(void) {
    __asm("di");
    *(u32 *)0x6FCC = (u32)isr_vblank;   /* VBL vector */
    *(u32 *)0x6FD4 = (u32)isr_hblank;  /* Timer0/HBlank (optional) */
    __asm("ei");
}
```

A common pattern: bulk-copy all 18 ISR pointers in one LDIRW from a ROM table
to `0x6FB8..0x6FFC` — simpler than 18 individual assignments.

---

## 3. Watchdog & Blocking Loops

The NGPC hardware resets the CPU if the watchdog register is not written within
~100ms. The VBlank ISR (60 Hz) normally feeds it. In long blocking operations,
feed it manually.

```c
#define HW_WATCHDOG    (*(volatile u8 *)0x006F)
#define WATCHDOG_CLEAR 0x4E

/* Before any long operation */
HW_WATCHDOG = WATCHDOG_CLEAR;
```

**Example — pool init loop (2952 iterations):**
```c
for (int i = 0; i < 0xB88; i++) {
    pool[i] = 0;
    *(volatile u8 *)0x006B = 0x4E;  /* watchdog kick every iteration */
}
```

> Some commercial games use address `0x006B` (byte 1 of the function at `0x006A`).
> The official SDK uses `0x006F`. Both addresses work on hardware.
> Use `0x006F` in new code (SDK standard).

**Sleep/wait-for-key loops** also need watchdog:
```c
void wait_frames(u8 n) {
    u8 start = g_vb_counter;
    while ((u8)(g_vb_counter - start) < n) {
        HW_WATCHDOG = WATCHDOG_CLEAR;
    }
}
```

---

## 4. Frame Budgets

### 4.1 CPU budget per frame

| Item | Value |
|------|-------|
| CPU frequency | 6.144 MHz |
| Cycles per frame (60 Hz) | ~102,400 cycles |
| VBlank window duration | ~3.94 ms = ~24,200 cycles |
| HBlank duration | ~5 us = ~30 cycles |

Typical distribution:
```
VBlank ISR (watchdog + OAM flush + scroll + audio)  : ~2,000 cycles
Game logic (update + AI + physics)                  : ~50,000 cycles
VRAM updates (outside VBlank, via VRAMQ)            : ~20,000 cycles
Margin                                              : ~30,000 cycles
```

> **IRQ context-switch is not free.** Each IRQ entry costs ≈ 80 cycles (prologue +
> branches + I/O + RETI). A per-scanline raster split (152 IRQ/frame) therefore burns
> ≈ 12,000 cycles/frame (~12 % of budget) — enough to drop a real cart to < 1 fps even
> though some emulators hide the cost. Prefer one split per frame for a HUD freeze; see
> [Effects and Raster §6.6](../03_Graphics/Effects-and-Raster.md). Note also that arming a Timer0 split must happen right
> after `ngpc_vsync()` (scanline ~0), before the variable-length game update, or the
> fire scanline jitters.

### 4.2 RAM budget

| Zone | Size |
|------|------|
| Total work RAM | 12 KB (`0x004000-0x005FFF`) |
| Stack | ~1 KB |
| Template globals | ~200 bytes |
| Audio driver state | ~500 bytes |
| Sprite/metasprite state | ~300 bytes |
| **Available for game** | **~9-10 KB** |

### 4.3 VBlank window budget

The VBlank window is ~24,200 cycles. Operations safe inside VBlank:
- OAM flush (shadow -> `0x8800`): 256 bytes = 128 LDIRW words = ~1,000 cycles
- Scroll register push: 4 byte writes = ~20 cycles
- Palette index flush (64 bytes): ~500 cycles
- VRAMQ flush: variable, depends on pending commands

**NEVER inside VBlank:**
- DMA operations (hardware watchdog power-off — confirmed by commercial game analysis)
- Long computation loops without watchdog feeds

> If raster DMA is active (channels streaming scroll registers per-scanline),
> stop all DMA channels before any VBlank work, then re-arm after.
> See [DMA](../03_Graphics/DMA.md) for the full stop/re-arm pattern.

---

## 5. State Machine Pattern

### 5.1 Basic enum state machine

```c
typedef enum { STATE_TITLE, STATE_GAME, STATE_GAMEOVER } GameState;
static GameState s_state = STATE_TITLE;

void main(void) {
    GameState prev = STATE_GAMEOVER;  /* force init on first frame */

    ngpc_init();
    ngpc_load_sysfont();

    while (1) {
        ngpc_vsync();
        ngpc_input_update();

        /* Call _init() once on state entry */
        if (s_state != prev) {
            prev = s_state;
            switch (s_state) {
            case STATE_TITLE:    title_init();    break;
            case STATE_GAME:     game_init();     break;
            case STATE_GAMEOVER: gameover_init(); break;
            }
        }

        /* Call _update() every frame */
        switch (s_state) {
        case STATE_TITLE:    title_update();    break;
        case STATE_GAME:     game_update();     break;
        case STATE_GAMEOVER: gameover_update(); break;
        }
    }
}
```

### 5.2 State transitions and cleanup

Each `_init()` should:
- Clear sprites: `ngpc_sprite_hide_all()`
- Clear tilemaps: `ngpc_gfx_clear(GFX_SCR1)` and/or `ngpc_gfx_clear(GFX_SCR2)`
- Reset scroll: `ngpc_gfx_scroll(GFX_SCR1, 0, 0)`
- Load new assets, set new palettes

### 5.3 Object pool pattern

Fixed-size pools are recommended for bullets, particles, and enemies.
No malloc, no fragmentation, deterministic timing on a 12 KB machine.

```c
#define MAX_BULLETS 16
static Bullet s_bullets[MAX_BULLETS];
static u16    s_active_mask;   /* bit N = slot N is used */

/* Spawn: find first free slot */
s8 bullet_spawn(s16 x, s16 y) {
    u8 i;
    for (i = 0; i < MAX_BULLETS; i++) {
        if (!(s_active_mask & (1 << i))) {
            s_active_mask |= (1 << i);
            s_bullets[i].x = x;
            s_bullets[i].y = y;
            return (s8)i;
        }
    }
    return -1;  /* pool full */
}

/* Update: iterate only active slots */
void bullets_update(void) {
    u8 i;
    for (i = 0; i < MAX_BULLETS; i++) {
        if (!(s_active_mask & (1 << i))) continue;
        s_bullets[i].x += s_bullets[i].vx;
        if (s_bullets[i].x > 200) {
            s_active_mask &= ~(1 << i);  /* free slot */
        }
    }
}
```

---

## 6. Pipeline A — Map Streaming + Main Loop

A scrolling action-game architecture proven by reverse engineering a commercial
side-scroller. Use this when the game uses large tilemap streaming.

### 6.1 VBlank ISR (short)

```
VBlank ISR (~2,000 cycles — stays short):
  1. HW_WATCHDOG = 0x4E                      feed watchdog
  2. LDIRW shadow_oam -> 0x8800              flush OAM (256 bytes = 128 words)
  3. LDIRW shadow_pal_idx -> 0x8C00          flush palette indices (64 bytes)
  4. write scroll regs from shadow values    0x8032 / 0x8033 / 0x8034 / 0x8035
```

### 6.2 Main loop

```
Main loop (game logic + streaming — uses remaining ~100,000 cycles):
  1. update input         snapshot joypad
  2. update camera_x/y   player physics / scroll logic
  3. clamp camera         between (min_x/y, max_x/y) map bounds
  4. MAP STREAMING X      if cam_x changed: load edge columns
  5. MAP STREAMING Y      if cam_y changed: load edge rows
  6. update scroll shadow cam_x & 0xFF, cam_y & 0xFF
  7. game logic           enemies, collision, SFX, score, etc.
  8. build shadow OAM     world -> screen projection for all entities
  9. halt                 wait for next VBlank
```

### 6.3 Critical rules

- Map streaming is **synchronous in main loop** — this architecture never streams during VBlank.
- Scroll registers are pushed from shadow values written in step 6.
- OAM is built **after** streaming (step 8) because entities need the updated camera.
- DMA is **not used**. LDIRW handles OAM and tilemap transfers.

### 6.4 Representative Exact Call Order

A representative main loop with a watchdog panic guard after it
(infinite `jp T` = safe hang on overrun):

```
main_loop:
          call vblank_wait    ; VBlank wait — polls frame counter > 0x1E
          call input_read     ; input — SWI call to BIOS joypad read
          call pool_reset     ; entity pool reset + budget counter = 0x1E (30)
          call window_reset   ; window register reset (160x152)
          call oam_build      ; shadow OAM builder — world->screen projection + clip
          call game_logic     ; (game logic — enemies, camera, collision)
          call scroll_update  ; scroll camera update — sub-pixel integration + clamp (SCR1+SCR2)
          call vblank_flush   ; VBlank flush — optional palette scatter, then LDIRW OAM+palette
          call game_state     ; (game state / scoring)
          call scroll_write   ; scroll register write — SCR1_X/Y -> 0x8032/0x8033, SCR2 -> 0x8034/0x8035
          call window_flush   ; window shadow flush — LDIRW shadow -> 0x8002-0x8005 (4 bytes)
          jp   main_loop      ; loop
```

Key notes from this order:
- **VBlank wait first** (`vblank_wait`): halts until the frame counter increments.
- **Input immediately after VBlank**: fresh joypad state before any logic.
- **Pool reset before OAM build** (`pool_reset`): clears slot counter and sets entity budget.
- **OAM builder before game logic** (`oam_build` before `game_logic`): shadow is built from last
  frame's positions, then game logic mutates positions for *next* frame.
  Sprite positions are 1-frame behind logic updates by design (double-buffer pattern).
- **Scroll camera update** (`scroll_update`): sub-pixel integration + clamp for SCR1 and SCR2.
  Uses a mutex flag to prevent re-entry.
- **VBlank flush** (`vblank_flush`): writes shadow OAM -> 0x8800 (128 words) + conditionally
  palette indices -> 0x8c00. Also writes PO.V (0x8021) = neg camera Y.
- **Scroll regs written late** (`scroll_write`): after all position math is done.
  Writes SCR1 (0x8032/0x8033) and SCR2 (0x8034/0x8035) from the scroll context values.
- **Window flush last** (`window_flush`): 9-byte routine — `LDIRW shadow -> 0x8002, BC=2`.
  Writes window right/bottom bounds and control flags from RAM shadow.

---

## 7. Pipeline B — All Logic in VBlank

A different architecture: **all game logic runs inside the VBlank ISR**.
The main loop is just an infinite `halt` loop.

### 7.1 VBlank ISR (all logic here)

```
VBL ISR — 8 steps, all logic here:
  1. BIOS WAIT_VBLANK call                    sync to K2GE signal
  2. joypad read + edge detection             just_pressed / just_released
  3. music timer update                       note_timer--, advance sequence if 0
  4. joypad snapshot                          prev_pad = cur_pad
  5. entity update loop                       ALL game logic: AI, physics, spawning
  6. OAM build                                shadow -> screen projection
  7. post-update / effects                    screen shake, palette flash, etc.
  8. OAM flush -> 0x8800                      LDIRW shadow OAM to hardware

Main loop:
  infinite loop: halt (wait for VBL)
```

Pipeline B vs Pipeline A:
- **Pipeline A:** game logic in main loop, VBL = only flush OAM + scroll registers
- **Pipeline B:** game logic IN VBL ISR, main loop = infinite halt

Both approaches are valid. Template 2026 follows the Pipeline A model (main loop logic),
which is safer for VBlank budget management.

### 7.2 Watchdog in long loops

Pipeline B implementations feed the watchdog every iteration of their pool init loop (2952 iterations)
to avoid a reset during startup. See [§3](#3-watchdog--blocking-loops) for the full pattern.

### 7.3 Joypad Edge Detection

The VBL ISR reads the joypad and computes edge-triggered signals
using three RAM bytes:

```
cur_pad     new raw joypad byte (updated each VBL)
prev_pad    copy of previous frame's raw byte
edge_mask   buttons pressed this frame only (rising edge)
```

ASM:
```asm
; A = new joypad read (from BIOS or hardware register)
; W = previous joypad value loaded before the update
ld  (prev_pad),W        ; prev_pad = old cur_pad
ld  (cur_pad),A         ; cur_pad  = new read
and W,A                 ; W = prev_pad & cur_pad  (bits held in both frames)
xor W,0xFF              ; W = ~(prev & cur)
and A,W                 ; rising = new & ~(prev & cur)
                        ;       = new & (new ^ prev) — bits that just went 1
ld  (edge_mask),A       ; store rising-edge mask
```

C equivalent:
```c
u8 prev = cur_pad;
cur_pad = read_joypad();                     /* hardware or BIOS read */
u8 held  = prev & cur_pad;
rising   = cur_pad & (u8)~held;             /* equivalent: cur & (cur ^ prev) */
```

> `rising` has a 1 only for buttons that transitioned 0->1 this frame.
> Use `cur_pad` for held/continuous checks and `rising` for single-press actions.

### 7.4 ROM Dispatch Table Pattern

An animation state machine can call entity-type-specific functions via a ROM
function pointer table. Entry size = 8 bytes (1 far function pointer).

```asm
; BC = state index (0, 1, 2, ...)
; BC *= 8 to get byte offset into table
sll    0x3, BC            ; BC <<= 3  (multiply by 8)
lda    XIY, dispatch_table ; XIY = base of ROM dispatch table
ld     XBC, (XIY+BC)      ; load far function pointer
call   T, XBC             ; call it unconditionally
```

C equivalent:
```c
typedef void (*EntityHandler)(void);
extern const EntityHandler dispatch_table[];   /* in ROM */
dispatch_table[state_index]();
```

Key properties of this pattern:
- Stride = 8 bytes per entry (size of a far pointer on TLCS-900H with cc900 large model)
- No bounds check — caller is responsible for keeping state_index in range
- Table lives in ROM (const) — no RAM overhead per entry
- Compatible with animation states, screen states, enemy-type handlers, etc.

### 7.5 Conditional Return Guards

TLCS-900H supports conditional `ret` in 2 bytes. This pattern is used extensively
as an early-exit guard at function entry:

```asm
; Sprite budget guard:
ld   E,(sprite_count)   ; E = sprite count
cp   E,0x40             ; compare with limit (64)
ret  GE                 ; return if E >= 64  (2 bytes, ~2 cycles)

; Direction guard:
cp   (XIX+0x11),0x0
ret  NZ                 ; return if direction != 0

; Range guard:
cp   WA,(XIX+0x14)
ret  Z                  ; return if already at max position
```

All common condition codes work: `ret GE`, `ret GT`, `ret LE`, `ret LT`,
`ret Z`, `ret NZ`.

Rule: **always use `ret <cond>` as a guard rather than a branch over the
entire function body** — it is 2 bytes vs 3-6 bytes for a jump, and reads
as a precondition check at the call site.

---

## 8. Pipeline C — Flag Sync + DMA Stop/Re-arm

A third architectural variant (virtual-pet / menu engine style): the VBlank handler
runs as a standard ISR but uses a RAM sync flag (`0x4000`) that the main loop waits on.
Game logic stays in the main loop (like Pipeline A) while guaranteeing VBlank atomicity.

### 8.1 VBlank ISR (12 steps)

```
VBL ISR (0x0A62) — 12 steps:
  1.  ld (0x006F),0x4E               watchdog kick (BEFORE raster wait)
  2.  loop: cp (0x8009),0x98         wait for RAS_V >= 0x98 (VBlank start)
  3.  push bank3 registers
  4.  if DMA active (0x6F85 != 0):
        ld (0x007C..0x007F),0        stop DMA0V-DMA3V (shadow enables)
        ldc DMAC_0..DMAC_3, 0       clear all DMAC registers
        call re_arm_dma             re-arm DMA for next frame
  5.  swi 0xFFFF04                   BIOS WAIT_VBLANK (K2GE sync signal)
  6.  callback 1 (ptr at 0x44B0)     if flag set
  7.  callback 2 (ptr at vbl_callback) DMA dispatcher
  8.  callback 3 (ptr at 0x5056)
  9.  call main_game_logic            entity update, physics, etc.
  10. inc (0x456D)                    global frame counter
  11. ld (0x4000),1                   SET VBL sync flag (main loop waits for this)
  12. pop registers + reti
```

### 8.2 Main loop (flag-based sync)

```c
/* Main loop waits for ISR to signal completion */
while (1) {
    /* Wait for VBL ISR to set sync flag */
    volatile u8 *sync = (volatile u8 *)0x4000;
    while (*sync == 0) {}
    *sync = 0;

    /* Post-VBL frame logic: input, per-frame updates */
    input_update();
    game_frame_update();
}
```

Advantage over `halt`: non-VBlank IRQs continue to fire during the wait,
allowing Timer0/DMA events to be processed without interruption.

### 8.3 Stop-all-DMA sequence (CRITICAL)

This architecture stops **all DMA channels** before doing any VBlank work:

```asm
ld (0x007C),0    ; stop DMA0V (shadow enable -> DMA channel 0)
ld (0x007D),0    ; stop DMA1V
ld (0x007E),0    ; stop DMA2V
ld (0x007F),0    ; stop DMA3V
ldc DMAC_0,WA   ; clear DMAC0 (WA=0)
ldc DMAC_1,WA   ; clear DMAC1
ldc DMAC_2,WA   ; clear DMAC2
ldc DMAC_3,WA   ; clear DMAC3
call re_arm_dma ; re-arm for next frame
```

This confirms the NGPC hardware rule: **active DMA during VBlank = watchdog power-off**.
The correct pattern is: stop -> VBL work -> re-arm (as implemented in Template 2026).

### 8.4 VBL Callback Pointer

> Full ASM detail: see [DMA §4.6](../03_Graphics/DMA.md).

This engine installs an indirect VBL callback at a fixed RAM address. The VBL ISR
does not call any game function directly — it calls whatever function pointer is currently
stored at that address. This allows the game to hot-swap the VBL action without modifying the ISR.

**VBL ISR dispatch (inside ISR):**

```asm
; VBL ISR calls the current callback indirectly
ld  xhl, (vbl_callback) ; load current callback pointer (far address)
call T, xhl             ; call it
reti
```

**Install DMA re-arm callback:**

```asm
; Store address of DMA re-arm routine as the VBL callback
ld  xhl, dma_rearm_fn
ld  (vbl_callback), xhl
```

**Disable VBL callback (replace with stub return):**

```asm
; Replace callback with a stub that just returns immediately
ld  xhl, vbl_stub_ret   ; address of: push/pop nothing + ret
ld  (vbl_callback), xhl
```

**C equivalent:**

```c
typedef void (*VblCallback)(void);

/* RAM slot for current VBL callback */
extern VblCallback *vbl_fn_ptr;   /* fixed RAM slot for the current VBL callback */

/* Install */
*vbl_fn_ptr = dma_rearm_fn;

/* Disable */
*vbl_fn_ptr = vbl_nop;   /* void vbl_nop(void) {} */
```

**Why this pattern:**
- The ISR code never changes — only the pointer changes.
- DMA can be enabled mid-game by installing the re-arm callback, and disabled during
  level transitions or cutscenes by swapping to the stub, without touching ISR vectors.
- Safe when the swap happens outside the VBL window (during game logic, not inside ISR).

> This is a simpler alternative to modifying interrupt vectors. Template 2026 achieves
> the same effect via the `stop/re-arm` wrapper in `isr_vblank()`.

---

## 9. Pipeline Comparison

```
                    Pipeline A   Pipeline B   Pipeline C    Template 2026
Game logic        : main loop   VBL ISR      main loop     main loop
Frame sync        : halt        halt         flag 0x4000   halt (ngpc_vsync)
Stop DMA in VBL   : no (no DMA) no (no DMA)  YES (all ch)  YES (stop/re-arm)
Re-arm DMA        : -           -            YES            YES
Watchdog kick     : VBL ISR     VBL ISR      VBL ISR        VBL ISR
OAM flush         : LDIRW VBL   LDIRW in ISR LDIRW callback LDIRW VBL
DMA used          : no          no           YES (raster)   optional
```

Template 2026 follows the "main loop logic + halt" model, closest to Pipeline A / C,
with stop/re-arm DMA conforming to the Pipeline C pattern.

---

## 10. Core Module API

### 10.1 ngpc_sys — System Init

```c
void ngpc_init(void);           /* Call first. Installs VBL ISR, inits viewport. */
u8   ngpc_is_color(void);       /* 1 = NGPC Color, 0 = monochrome NGP */
void ngpc_shutdown(void);       /* Power off (BIOS call) */
void ngpc_load_sysfont(void);   /* Load BIOS font into tile RAM (slots 32-127) */
void ngpc_memcpy(dst, src, n);  /* Byte copy */
void ngpc_memset(dst, val, n);  /* Byte fill */

extern volatile u8 g_vb_counter; /* Frame counter, incremented at 60 Hz */
```

`ngpc_init()` installs the VBL ISR automatically. The ISR clears the watchdog,
checks shutdown requests, increments `g_vb_counter`, and flushes the VRAMQ.

### 10.2 ngpc_vramq — Queued VRAM Updates

Queue tilemap/palette writes during gameplay, then VBlank flushes them safely.

```c
void ngpc_vramq_init(void);
u8   ngpc_vramq_copy(dst, src, len_words);   /* Queue u16 block copy */
u8   ngpc_vramq_fill(dst, value, len_words); /* Queue u16 fill */
void ngpc_vramq_flush(void);                 /* Flush all pending commands */
void ngpc_vramq_clear(void);                 /* Drop pending commands */
u8   ngpc_vramq_pending(void);               /* Number of pending commands */
u8   ngpc_vramq_dropped(void);               /* Commands rejected (queue full) */
```

Notes:
- `len_words` is in `u16` units (not bytes).
- Destination must be inside VRAM (`0x8000-0xBFFF`).
- Queue size is fixed at `VRAMQ_MAX_CMDS` (default 16 commands).
- `ngpc_sys` calls `ngpc_vramq_flush()` automatically each VBlank.

### 10.3 ngpc_timing — Timing

```c
void ngpc_vsync(void);          /* Wait for next VBlank (~60 Hz) */
u8   ngpc_in_vblank(void);      /* Check if currently in VBlank */
void ngpc_sleep(u8 frames);     /* Pause N frames (feeds watchdog) */
void ngpc_cpu_speed(u8 div);    /* 0=6MHz, 1=3MHz, 2=1.5MHz, 3=768KHz, 4=384KHz */
```

### 10.4 ngpc_input — Joypad

```c
void ngpc_input_update(void);         /* Call once per frame (after vsync) */

extern u8 ngpc_pad_held;              /* Buttons currently down */
extern u8 ngpc_pad_pressed;           /* Buttons just pressed this frame */
extern u8 ngpc_pad_released;          /* Buttons just released this frame */
extern u8 ngpc_pad_repeat;            /* Auto-repeat (for menu navigation) */

void ngpc_input_set_repeat(u8 delay, u8 rate); /* delay/rate in frames */
```

Button masks: `PAD_UP`, `PAD_DOWN`, `PAD_LEFT`, `PAD_RIGHT`,
`PAD_A`, `PAD_B`, `PAD_OPTION`, `PAD_POWER`

```c
/* React to a new button press only (not held) */
if (ngpc_pad_pressed & PAD_A) { /* fire! */ }

/* Menu navigation with auto-repeat (15f initial delay, then every 4f) */
ngpc_input_set_repeat(15, 4);
if ((ngpc_pad_pressed | ngpc_pad_repeat) & PAD_DOWN) { /* next item */ }
```

Edge detection formula (confirmed from commercial game reverse engineering):
```c
pressed  = (prev ^ cur) & cur;   /* rising edges */
released = (prev ^ cur) & prev;  /* falling edges */
```

### 10.5 ngpc_math — Math

```c
s8   ngpc_sin(u8 angle);         /* Angle 0-255, returns -127..127 */
s8   ngpc_cos(u8 angle);         /* Same range */
void ngpc_rng_seed(void);        /* Seed RNG from frame counter */
u16  ngpc_random(u16 max);       /* LCG, returns 0..max (max <= 32767) */
s32  ngpc_mul32(s32 a, s32 b);   /* 32-bit signed multiply */

void ngpc_qrandom_init(void);    /* Shuffle table (call after rng_seed) */
u8   ngpc_qrandom(void);         /* Ultra-fast: table read + index increment */
```

Angles: 0=0 deg, 64=90 deg, 128=180 deg, 192=270 deg, 256 wraps to 0.

`ngpc_random` limit: extracts bits 16-30 of LCG — result is always in `0..32767`
regardless of `max`. For large random numbers: `ngpc_random(255) | (ngpc_random(127) << 8)`.

For proper game logic (AI, procedural gen) use `ngpc_random()`.
For non-critical randomness (particles, screen shake) use `ngpc_qrandom()` (zero cost).

### 10.6 ngpc_debug — CPU Profiler

```c
void ngpc_debug_begin(void);                              /* Mark logic start */
void ngpc_debug_end(void);                                /* Mark logic end */
void ngpc_debug_draw_bar(plane, pal_ok, pal_warn, pal_over); /* Visual bar */
void ngpc_debug_print_pct(plane, pal, x, y);              /* Print "XX%" */
void ngpc_debug_print_fps(plane, pal, x, y);              /* Print "XXFPS" */
u8   ngpc_debug_get_lines(void);                          /* Raw scanlines used */
u8   ngpc_debug_get_pct(void);                            /* CPU usage 0-100+ */
```

Measures game logic time via the hardware raster position register `HW_RAS_V`.
Bar turns: **green** (< 80%), **yellow** (80-100%), **red** (> 100% = frame overflow).

```c
/* Typical game loop with profiler */
ngpc_vsync();
ngpc_input_update();
ngpc_debug_begin();
    game_update();
ngpc_debug_end();
ngpc_debug_draw_bar(GFX_SCR2, PAL_GREEN, PAL_YELLOW, PAL_RED);
```

Disable for release: `#define NGPC_DEBUG 0` (all calls become no-ops).

### 10.7 ngpc_log — Ring Buffer Debug Log

```c
void ngpc_log_init(void);
void ngpc_log_clear(void);
void ngpc_log_hex(const char *label, u16 value);
void ngpc_log_str(const char *label, const char *str);
void ngpc_log_dump(plane, pal, x, y);
u8   ngpc_log_count(void);

/* Convenience macros */
NGPC_LOG_HEX("PAD", ngpc_pad_held);
NGPC_LOG_STR("ST",  "RUN ");
```

Stores short entries in a fixed-size ring buffer (~288 bytes RAM).
Useful on hardware where no serial/stdout is available.

### 10.8 ngpc_assert — Runtime Assert

```c
NGPC_ASSERT(pointer != 0);
```

In debug builds: assertion failure shows an on-screen fault page and blinks
the background in a loop. In release profile: compiled out entirely.

Toggle: `#define NGP_PROFILE_RELEASE 1` before including headers.

---

## 11. Safe Rules

```
VRAM writes     : during VBlank window OR via ngpc_vramq. NOT during active render.
                  Active render VRAM access -> Character Over (graphics glitch).
OAM flush       : LDIRW shadow -> 0x8800 in VBlank ISR. Never write OAM mid-frame.
                  Flushing OAM in the main loop AFTER the VBlank sync (rather than
                  inside the ISR) keeps frame timing deterministic; either is valid.
Video registers : shadow ALL video registers in RAM, flush once per VBlank. Never
                  write a video register during game logic. Skip the BG_CTL write on
                  mono hardware; skip scroll writes when a DMA-driven camera owns them.
Scroll regs     : update shadow in main loop; push in VBL ISR. 8-bit, wraps at 256px.
DMA in VBlank   : FORBIDDEN. Active DMA + VBlank = watchdog power-off (hardware).
                  Pattern: stop all DMA -> VBL work -> re-arm DMA.
ISR must be     : short. All heavy work in main loop (Pipeline A / Template model).
                  Long ISR + audio tick = audio glitches and frame budget overflow.
Input snapshot  : call ngpc_input_update() ONCE per frame, after vsync.
                  Multiple calls per frame = inconsistent button state.
Watchdog        : feed at VBL ISR start. Also in any blocking loop > ~5ms.
Timer0 owner    : one user only. raster_chain, raster, and sprmux all compete.
                  ngpc_dma (MicroDMA) + sprmux can coexist: DMA on Timer0,
                  sprmux on Timer1 (ngpc_sprmux_flush_timer1()).
u16 counter     : NEVER use u8 for loop counter if iterations >= 256.
                  (OAM flush = 256 bytes -> u8 counter wraps to 0 = infinite loop.)
State cleanup   : on state transition, hide all sprites + clear tilemap.
                  Leftover sprites from previous state = garbage on screen.
```

---

## Quick Reference

| Item | Details |
|------|---------|
| VBL ISR vector | `0x6FCC` (32-bit ptr) |
| Watchdog register | `0x006F`, write `0x4E` |
| Watchdog alt address | `0x006B` (some commercial games, not SDK) |
| Frame counter | `g_vb_counter` (volatile u8, 60 Hz) |
| Frame budget | ~102,400 cycles total |
| VBlank window | ~24,200 cycles |
| HBlank window | ~30 cycles |
| VBL sync flag pattern | Pipeline C: poll `0x4000`, reset after |
| OAM flush | LDIRW shadow_oam -> `0x8800`, 128 words |
| Scroll push | Write `0x8032`/`0x8033`/`0x8034`/`0x8035` in ISR |
| DMA rule | NEVER during VBlank |
| VRAMQ max cmds | 16 (configurable) |

---

## See Also

- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — Interrupt vectors, register addresses
- [BIOS](../01_Hardware/BIOS.md) — Shutdown, watchdog, BIOS calls
- [Sprites and OAM](../03_Graphics/Sprites-and-OAM.md) — Shadow OAM, OAM flush, sprite budget
- [DMA](../03_Graphics/DMA.md) — DMA patterns, stop/re-arm, VBlank rules
- [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) — Map streaming, scroll register management
- [Input](Input.md) — Full input API reference
- [Audio](../04_Audio/Audio.md) — Audio driver, VBL audio tick
