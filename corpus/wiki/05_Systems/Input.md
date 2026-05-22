# Input

Joypad reading, state tracking, and input patterns for NGPC homebrew on the TLCS-900H.

---


## 1. Hardware Register

### 1.1 Address

| Name | Address | Size | Description |
|------|---------|------|-------------|
| `HW_JOYPAD` | `0x6F82` | `u8` | BIOS RAM variable — joypad state, updated each VBlank |

```c
#define HW_JOYPAD (*(volatile u8*)0x6F82)
```

> **Important:** `0x6F82` is a BIOS internal RAM variable (bank 3), refreshed by the BIOS
> VBlank handler. This is the address to use. Value is **active-high**: bit = 1 means button pressed.

> **Note:** Some code references address `0xB000` with inverted bits (`keys = ~REG_KEYPAD`).
> That is the **raw hardware keypad register** (before BIOS processing).
> Prefer `0x6F82` (BIOS variable): it is already synchronized to VBlank and does not require
> bit inversion.

### 1.2 Button Bitmasks

```c
#define PAD_UP     0x01
#define PAD_DOWN   0x02
#define PAD_LEFT   0x04
#define PAD_RIGHT  0x08
#define PAD_A      0x10
#define PAD_B      0x20
#define PAD_OPTION 0x40
#define PAD_POWER  0x80
```

Alternative naming convention used in several homebrews (equivalent):

```c
#define J_UP     0x01
#define J_DOWN   0x02
#define J_LEFT   0x04
#define J_RIGHT  0x08
#define J_A      0x10
#define J_B      0x20
#define J_OPTION 0x40
#define J_POWER  0x80
```

Both conventions are interchangeable. Pick one and stick to it.

---

## 2. Basic Polling

### 2.1 Simple Read (each frame)

```c
static u8 g_pad;       /* current state */

void poll_input(void)
{
    g_pad = HW_JOYPAD;
}

/* usage */
if (g_pad & PAD_A) { /* A is held */ }
```

Call **once per frame**, typically at the start of the game loop (after the VBlank `halt` sync).
Never read `HW_JOYPAD` multiple times per frame.

### 2.2 Position in the Main Loop

```c
void main(void)
{
    init();
    while (1) {
        halt;               /* sync to VBlank */
        watchdog_clear();
        poll_input();       /* single read per frame */
        update_game();
        render();
    }
}
```

CPU cost for input read: negligible (~10 cycles).

---

## 3. Advanced State Detection

### 3.1 Edge Detection — Button "just pressed"

Essential for one-shot actions (fire, jump, menu confirm) — prevents unwanted key repeat:

```c
static u8 g_pad;       /* current state */
static u8 g_pad_prev;  /* previous frame state */

void poll_input(void)
{
    g_pad_prev = g_pad;
    g_pad      = HW_JOYPAD;
}

/* Utility macros */
#define PAD_PRESSED(btn)   ( (g_pad & (btn)) && !(g_pad_prev & (btn)) )
#define PAD_RELEASED(btn)  ( !(g_pad & (btn)) && (g_pad_prev & (btn)) )
#define PAD_HELD(btn)      ( g_pad & (btn) )
```

Usage example:

```c
if (PAD_PRESSED(PAD_A))   { fire_bullet(); }   /* one bullet per press */
if (PAD_HELD(PAD_RIGHT))  { move_right(); }    /* continuous while held */
```

### 3.2 Directional Mask Read

Efficient approach — single register access, then a switch on the combined mask:

```c
u8 dirs = g_pad & (PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT);
switch (dirs) {
    case PAD_UP:                 /* up              */ break;
    case PAD_DOWN:               /* down            */ break;
    case PAD_LEFT:               /* left            */ break;
    case PAD_RIGHT:              /* right           */ break;
    case PAD_UP   | PAD_RIGHT:   /* diagonal up-right   */ break;
    case PAD_UP   | PAD_LEFT:    /* diagonal up-left    */ break;
    case PAD_DOWN | PAD_RIGHT:   /* diagonal down-right */ break;
    case PAD_DOWN | PAD_LEFT:    /* diagonal down-left  */ break;
    case PAD_LEFT | PAD_RIGHT:   /* cancel (both held)  */ break;
}
```

One `& mask`, then a `switch` — cc900 compiles this to a jump table.

---

## 4. Special Patterns

### 4.1 Blocking Wait (menu / title screen)

Waiting for a button press outside the normal game loop:

```c
/* Wait for release (debounce), then press, then release */
while (HW_JOYPAD & PAD_A);           /* wait for release if already held */
while (!(HW_JOYPAD & PAD_A));        /* wait for press */
while (HW_JOYPAD & PAD_A);           /* wait for release */
/* continue */
```

> Do not forget the watchdog in blocking loops that can span multiple frames
> (risk of hardware reset).

### 4.2 Sleep for N VBlanks

Standard homebrew pattern for transitions and animations:

```c
/* g_vblank_count is incremented in the VBlank handler */
extern volatile u16 g_vblank_count;

void sleep_frames(u16 n)
{
    u16 start = g_vblank_count;
    while ((g_vblank_count - start) < n)
        ; /* busy-wait */
}
```

> Use unsigned subtraction (`u16 - u16`) to handle counter wrap correctly.

### 4.3 OPTION / POWER Buttons

- `PAD_OPTION` (`0x40`): Option button (left side). Useful for pause, settings menu.
- `PAD_POWER` (`0x80`): Power button. Handled by the BIOS (shutdown). Do not intercept
  without a specific reason. Check `HW_USR_SHUTDOWN` (`0x6F85`) if power management is needed.

---

## 5. Integration in the VBlank Handler

The watchdog must be cleared in the VBlank ISR. The joypad can also be updated there,
but **only if the ISR remains lightweight** — do not put game logic inside the ISR:

```c
void vblank_isr(void)
{
    WATCHDOG = WATCHDOG_CLEAR;  /* 0x4E -> 0x006F */
    g_vblank_count++;
    audio_tick();
    /* Do NOT read HW_JOYPAD here — read it in the game loop instead */
}
```

**Rule:** Read `HW_JOYPAD` in the game loop (post-halt), not in the ISR.
The ISR must stay under ~2,000 cycles (watchdog + counter + audio).

---

## 6. Example Button Mappings

### 6.1 Shmup

| Action | Button | Mode |
|--------|--------|------|
| Move | D-Pad | Continuous (`PAD_HELD`) |
| Fire | A | Continuous or edge-triggered depending on fire rate |
| Bomb / Option | B | Edge-triggered (`PAD_PRESSED`) |
| Pause | OPTION | Edge-triggered (`PAD_PRESSED`) |

### 6.2 Sports / Menu Game

| Context | Button | Action |
|---------|--------|--------|
| Match | D-Pad | Movement |
| Match | A | Throw / Catch |
| Match | B | Dash |
| Menu | D-Pad up/down | Change entry |
| Menu | A | Confirm |
| Settings | B | Back |
| Intro | A | Skip to main menu |

---

## 7. Pitfalls and Notes

### 7.1 Read the Register Once per Frame

Never call `HW_JOYPAD` directly from multiple places in the code.
Always read it in `poll_input()` — snapshot into a global variable.
This prevents inconsistent values if the player releases a button mid-frame.

### 7.2 volatile Is Mandatory

```c
/* CORRECT */
#define HW_JOYPAD (*(volatile u8*)0x6F82)

/* INCORRECT — cc900 may cache the value in a register */
#define HW_JOYPAD (*(u8*)0x6F82)
```

Without `volatile`, the compiler is allowed to read the register only once and reuse the cached
value — buttons will appear stuck.

### 7.3 Initialize g_pad_prev at Boot

```c
void input_init(void)
{
    g_pad      = HW_JOYPAD;
    g_pad_prev = g_pad;   /* prevents a false "just pressed" on the first frame */
}
```

Without this, `g_pad_prev` starts at 0 and `g_pad` starts with the actual button state —
any held button on boot triggers a spurious `PAD_PRESSED` event on frame 1.

### 7.4 PAD_POWER and PAD_OPTION Are Not D-Pad Buttons

Both are discrete hardware buttons located on the side of the unit.
`PAD_OPTION` is often the only "menu/pause" button available — use it sparingly and
ensure the player can always find their way back into the game.

---

## 8. Input Module

A reusable input module commonly manages:

- Per-frame pad snapshot
- `held` / `pressed` / `released` state tracking
- Optional `repeat` for menu navigation

### 8.1 API

```c
void ngpc_input_update(void);   /* Call once per frame */

extern u8 ngpc_pad_held;        /* Buttons currently down */
extern u8 ngpc_pad_pressed;     /* Buttons just pressed (this frame) */
extern u8 ngpc_pad_released;    /* Buttons just released (this frame) */
```

Usage:

```c
ngpc_input_update();
if (ngpc_pad_pressed & PAD_A)   { /* one-shot action */ }
if (ngpc_pad_held & PAD_RIGHT)  { /* continuous move */ }
```

### 8.2 Auto-Repeat (Menus)

Per-button auto-repeat is useful for list navigation:

```c
void ngpc_input_set_repeat(u8 delay, u8 rate); /* delay/rate in frames */
extern u8 ngpc_pad_repeat;                     /* repeated presses this frame */
```

Pattern:

```c
ngpc_input_set_repeat(15, 4);   /* 15-frame initial delay, repeat every 4 frames */
if ((ngpc_pad_pressed | ngpc_pad_repeat) & PAD_DOWN) { cursor_next(); }
if ((ngpc_pad_pressed | ngpc_pad_repeat) & PAD_UP)   { cursor_prev(); }
```

The repeat is typically implemented in ~35 lines as a per-button counter.

A representative joypad RAM layout uses one byte per signal:

```
prev_raw      current raw joypad (previous frame, used for delta)
cur_pad       current joypad state (this frame)
released      released buttons (went 1→0 this frame)
pressed       pressed buttons (went 0→1 this frame) / A+B state
repeat_flags  auto-repeat fired flags (one bit per button)
repeat_ctr    auto-repeat down-counter (resets to 12 on any new press)
```

**`ex (addr), R` idiom for current/previous swap:**

```asm
ld  H, (HW_JOYPAD)       ; H = current hardware reading
ex  (prev_raw), H        ; H = prev (old prev_raw); (prev_raw) = current
ld  (cur_pad), H         ; save previous
xor H, (prev_raw)        ; H = prev XOR current = changed bits
and H, (cur_pad)         ; H = changed AND prev = RELEASED (1→0)
ld  (released), H        ; save released
```

For pressed (0→1 rising edge) — separate path using `xor W,A; and W,A`:
```asm
; W = prev A-buttons, A = cur A-buttons
xor W, A      ; W = changed
and W, A      ; W = changed AND cur = PRESSED (0→1)
ld  (pressed), WA
```

**Auto-repeat counter logic:**
```asm
cp  W, 0               ; any new press?
jr  NZ, reset_timer    ; yes: reset timer
dec (repeat_ctr)       ; no: count down
jr  NZ, done           ; timer not zero: nothing
or  (repeat_flags), A  ; timer hit 0: fire auto-repeat for this button
reset_timer:
ld  (repeat_ctr), 0xC  ; reset counter to 12 frames
done: ret
```

The 12-frame initial repeat delay matches typical menu feel at 60fps
(~200 ms before repeat fires).

---

## Quick Reference

| Item | Value / Pattern | Notes |
|------|-----------------|-------|
| BIOS joypad address | `0x6F82` | `u8`, active-high, updated each VBlank |
| Raw hardware address | `0xB000` | Inverted bits — avoid, use BIOS var instead |
| PAD_UP | `0x01` | |
| PAD_DOWN | `0x02` | |
| PAD_LEFT | `0x04` | |
| PAD_RIGHT | `0x08` | |
| PAD_A | `0x10` | |
| PAD_B | `0x20` | |
| PAD_OPTION | `0x40` | Side button, use for pause/menu |
| PAD_POWER | `0x80` | BIOS-managed, use with caution |
| Edge detect — pressed | `(g_pad & btn) && !(g_pad_prev & btn)` | One-shot actions |
| Edge detect — released | `!(g_pad & btn) && (g_pad_prev & btn)` | |
| Held | `g_pad & btn` | Continuous while pressed |
| Blocking wait (debounce) | release → press → release loop on `HW_JOYPAD` | Title screen / menus |
| Sleep N frames | `while (g_vblank_count - start < n) ;` | u16 subtraction (wrap-safe) |
| volatile | mandatory | Without it, cc900 caches the value |
| Init at boot | Set `g_pad = g_pad_prev = HW_JOYPAD` | Prevent frame-1 false press |
| Auto-repeat | `ngpc_input_set_repeat(delay, rate)` | Typical: delay=15, rate=4 |

---

## See Also

- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — BIOS RAM map (`0x6F82` context, watchdog address)
- [Game Loop](Game-Loop.md) — VBlank ISR structure, main loop skeleton, watchdog rules
- [BIOS](../01_Hardware/BIOS.md) — `HW_USR_SHUTDOWN`, `HW_LANGUAGE`, BIOS variable layout
- [Gameplay Patterns](../06_Pipeline-and-Patterns/Gameplay-Patterns.md) — State machines, pause, menu flow
