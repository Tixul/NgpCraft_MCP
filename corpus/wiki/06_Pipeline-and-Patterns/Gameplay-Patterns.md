# Gameplay Patterns

Game design patterns for NGPC: screen flow state machines, pause/menu systems, difficulty pacing, control mapping strategies, and common genre mechanics.

---


## 1. Screen Flow and State Machines

### 1.1 Typical Flow

Most NGPC homebrew games use a linear state machine across a small set of screens:

```
[INTRO]
  |
  v
[TITLE]
  |-- A pressed -> [GAME]
  |-- OPTION    -> [OPTIONS]
  |
[GAME]
  |-- OPTION    -> [PAUSE]
  |-- died      -> [GAME OVER]
  |-- cleared   -> [STAGE CLEAR] or [GAME OVER]
  |
[GAME OVER]
  |-- check high score -> [HIGH SCORE ENTRY]
  |-- else             -> [TITLE]
  |
[HIGH SCORE ENTRY]
  |-> [HIGH SCORE TABLE]
     |-> [TITLE]
```

### 1.2 Implementation Pattern

Each state = a pair of functions `(init, update)`:

```c
typedef enum {
    STATE_TITLE = 0,
    STATE_GAME,
    STATE_PAUSE,
    STATE_GAME_OVER,
    STATE_HIGH_SCORE,
} GameState;

static GameState s_state;

void game_init(void);
GameState game_update(void);   /* returns next state */

void main(void)
{
    ngpc_init();
    s_state = STATE_TITLE;
    title_init();

    while (1) {
        ngpc_vsync();
        ngpc_input_update();

        switch (s_state) {
            case STATE_TITLE: {
                GameState next = title_update();
                if (next != s_state) {
                    s_state = next;
                    if (next == STATE_GAME) game_init();
                }
            } break;
            case STATE_GAME: { /* ... */ } break;
            /* ... */
        }
    }
}
```

---

## 2. Pause System

### 2.1 OPTION Button Convention

OPTION is the standard pause button on NGPC — it is the only "side button" available
and users expect it to pause/unpause. Use `PAD_PRESSED(PAD_OPTION)` (edge-triggered).

**From OPTION:**
- First press → enter pause
- Second press → resume game
- During pause: A/B = "resume" or context action

**Pause menu from OPTION (second level):**
- D-Pad to navigate menu entries
- A to confirm
- B or OPTION to cancel / return to game

### 2.2 Pause Menu Example

```c
typedef enum { PAUSE_RESUME, PAUSE_RESTART, PAUSE_TITLE } PauseChoice;

static u8 s_pause_cursor;

void pause_init(void)
{
    s_pause_cursor = 0;
    /* draw pause overlay */
    ngpc_text_print(GFX_SCR1, 0, 8, 4, "RESUME");
    ngpc_text_print(GFX_SCR1, 0, 8, 6, "RESTART");
    ngpc_text_print(GFX_SCR1, 0, 8, 8, "QUIT");
}

PauseChoice pause_update(void)
{
    if (PAD_PRESSED(PAD_DOWN))  s_pause_cursor = (s_pause_cursor + 1) % 3;
    if (PAD_PRESSED(PAD_UP))    s_pause_cursor = (s_pause_cursor + 2) % 3;
    if (PAD_PRESSED(PAD_A) || PAD_PRESSED(PAD_OPTION)) {
        switch (s_pause_cursor) {
            case 0: return PAUSE_RESUME;
            case 1: return PAUSE_RESTART;
            case 2: return PAUSE_TITLE;
        }
    }
    return (PauseChoice)-1;   /* no selection yet */
}
```

---

## 3. Difficulty and Pacing

### 3.1 Density vs Speed

When a game feels too easy or too hard, tune in this order:

1. **Speed first** — lower enemy movement speed and fire rate before increasing density.
   Players can handle a few fast enemies better than a swarm of slow ones.
2. **Density second** — after speed feels right, add more simultaneous enemies.
3. **Patterns third** — vary attack patterns to maintain interest without just adding numbers.

> Permanent saturation = visual fatigue + performance pressure.
> Always alternate pressure and breathing room.

### 3.2 Breathing Rhythm

Effective level pacing (shmup example):

```
[Wave 1: 3 enemies, sparse]  ← easy entry
[Breather: 2-3 seconds, no enemies]
[Wave 2: 5 enemies, mixed patterns]  ← moderate pressure
[Breather: 1 second]
[Wave 3: 8 enemies + formation]  ← high pressure
[BOSS]
[Post-boss: empty field, health drop]  ← reward / recovery
```

Rhythm rule: **calm → pressure → calm → pressure**. Two consecutive intense waves
without breathing room cause frustration, not challenge.

### 3.3 Difficulty Scaling

Common patterns:

| Parameter | Easy → Hard progression |
|-----------|------------------------|
| Enemy speed | Slower → faster |
| Fire rate | Longer reload → shorter |
| Enemy count | 2-4 → 8-12+ simultaneously |
| Enemy HP | 1 hit → 2-5 hits |
| AI reaction | Delayed → immediate |
| Pattern complexity | Straight lines → curves + spread |

Recommended approach: define constants, not magic numbers.

```c
static const u8 SPEED_BY_LEVEL[] = { 1, 1, 2, 2, 3, 3 };
static const u8 FIRE_RATE_BY_LEVEL[] = { 90, 75, 60, 45, 30, 20 };
u8 g_level;

u8 enemy_speed(void) { return SPEED_BY_LEVEL[g_level]; }
u8 fire_cooldown(void) { return FIRE_RATE_BY_LEVEL[g_level]; }
```

### 3.4 Fixed vs self-timed frame rate (60 vs 30 fps)

Not every game runs at a fixed 60 fps. Some (Cool Boarders Pocket, Densha de Go)
**self-time**: the VBlank ISR does the full per-frame work only if the main loop
already consumed the previous frame (a per-frame `vsync` byte, e.g. `0x4016`). If the
per-frame work exceeds one VBlank period, the ISR skips every other VBlank and the game
runs at **30 fps** automatically.

```
VBlank ISR:  cp (vsync),0xFF ; jr Z, skip   ; last frame not consumed -> skip
             ...full work...  ; ld (vsync),0xFF
main loop:   ld (vsync),0    ; ...update... ; spin until (vsync)!=0
```
The `0x4016` flag with the `cp ...,0xFF ; jr Z` guard is a real reverse-engineered
pattern; whether it *sets* the frame rate depends on the per-frame load.

> **Emulator bug — SOLVED (silicon-calibrated, 2026-07).** These games run at **30 fps on
> silicon** but at **60 fps (2x too fast) on ares / BizHawk / this emulator** — the timer
> counts double and the whole game is too fast. A genuine bug common to **all** NGPC
> emulators. **Root cause: unmodelled cartridge-flash wait-states.** Every instruction is
> fetched from the cart and data tables are read from it; that flash bus is slow, but
> emulators fetch it for free, so cart code runs **~3.4x too fast**. The self-timed games'
> per-frame work then fits inside one VBlank (60 fps) instead of spilling into a second
> (30 fps). VBlank-locked games (Fatal Fury) are unaffected — their work fits in one frame
> regardless.
>
> A calibration ROM run on **real hardware** (`hw_calibration/cpu_calib_v1.ngc`) measured
> the per-class slowdown: short/fetch-bound ops ~3.4x, MUL/DIV ~2.5x, frame length (RASV)
> correct — the exact signature of a fetch/access wait-state, not an execution-cost bug.
> (The earlier "VRAM adjustment-circuitry wait-state" idea was **disproven** first: Cool
> Boarders spins ~62% of every frame, so it is not CPU-bound and no VRAM cost could cause
> the 2x. The slow bus is the **cart**, not VRAM. Also ruled out: clock gear, TI0 rate,
> prescaler timers, global pacing.)
>
> **Fix (four causes).** (1) **3 cycles/byte for instruction FETCH from the cart**
> (`Machine::cart_wait`) — silicon-confirmed. (2) Cart DATA reads are **NOT** wait-stated
> (`data=0`) — a ROM found `CRND==RRND`, refuting an earlier `data=5` guess. (3) **MUL/DIV
> were under-costed** (datasheet is a floor) — fixed to the silicon numbers. (4) The residual
> ~51→30 fps is the game's big per-frame **LDIR** into a RAM buffer: the datasheet 7 cycles/byte
> is also a floor, and **14 cycles/byte** puts Cool Boarders at 30 fps while Fatal Fury stays
> at 60 — one fix, both games, confirmed by comparing the in-game timer to a stopwatch. (A
> cart-flash write is also throttled in active display, but this game writes VRAM in vblank, so
> that is a separate real effect, not its bottleneck.) **Any accurate NGPC emulator needs the
> cart-flash FETCH wait-state and the silicon LDIR/MUL/DIV costs — the datasheet under-states them.**

---

## 4. Control Mapping Patterns

### 4.1 Edge vs Continuous

| Input type | Technique | Use case |
|------------|-----------|----------|
| Edge-triggered (just pressed) | `PAD_PRESSED()` | Fire, jump, confirm, toggle pause |
| Held/continuous | `PAD_HELD()` | Movement, accelerate, hold shoot |
| Auto-repeat | `ngpc_pad_repeat` (delay=15, rate=4) | Menu navigation |

Avoid using `PAD_HELD` for one-shot actions — it causes rapid repeats.
Avoid `PAD_PRESSED` for movement — it causes jerky stepping.

### 4.2 Context-Sensitive Controls

Same button, different meaning depending on state:

| State | A | B | OPTION |
|-------|---|---|--------|
| Title | Start game | — | Options |
| Gameplay | Fire/action | Secondary | Pause |
| Pause | Resume | Back | Toggle pause |
| Menu | Confirm | Back/Cancel | Pause |
| High score entry | Confirm char | Delete char | — |

Define behavior per-state in each `state_update()` function — do not branch on state
inside a generic input handler.

---

## 5. Genre-Specific Patterns

### 5.1 Shoot-em-up

Core loop:

```
halt (VBlank)
→ poll input
→ move player (PAD_HELD direction)
→ fire bullet (PAD_PRESSED or PAD_HELD with cooldown)
→ update enemies (wave system, formation)
→ update bullets (all pools in one pass)
→ collision (bullet vs enemy, enemy vs player)
→ render
```

Fire rate: use a per-entity cooldown counter rather than `PAD_PRESSED`:

```c
if (fire_cooldown == 0 && (ngpc_pad_held & PAD_A)) {
    spawn_bullet();
    fire_cooldown = FIRE_RATE;
}
if (fire_cooldown > 0) fire_cooldown--;
```

Enemy waves: delayed spawn using a monotonically increasing counter:

```c
/* Delays must be in ascending order */
static const WaveEntry wave[] = {
    { 30,  ENEMY_STRAIGHT },    /* frame 30 */
    { 84,  ENEMY_DIAGONAL },    /* frame 84 (spacing >= 54 to avoid overlap) */
    { 138, ENEMY_ZIGZAG },
};
```

Minimum spacing between spawns at speed_x=1: 54 frames to avoid visual overlap.

### 5.2 Platformer

Core rules:
- Gravity: `vy += GRAVITY` each frame (typical GRAVITY = 1-2 units)
- Jump: `vy = -JUMP_STRENGTH` on press, only when grounded
- Coyote time: allow jump for 4-6 frames after leaving a ledge
- Max fall speed: clamp `vy <= FALL_MAX` to avoid tunneling

Kill zone: use map bounds (`world_x < -24 || world_x > map_w * 8 + 24`)
not camera-relative bounds — enemies must survive offscreen while scrolling.

### 5.3 Puzzle / Match-3

Core events:
1. Player selects a piece
2. Player swaps with an adjacent piece
3. Check for matches (3+ in a row/column)
4. Remove matched pieces (with animation frame)
5. Apply gravity (pieces fall)
6. Check for chain combos (repeat from step 3)
7. Check win/lose condition

Two common modes:
- **Move limit** — fixed number of swaps; combos return credits.
- **Time trial** — decreasing timer; clearing blocks adds time.

Scoring tip: chain combos multiply points — reward skilled play, not just any match.

### 5.4 Snake / Grid-Based

Key rules:
- Movement on a tile grid — update direction on `PAD_PRESSED`, not held
- Ignore reverse direction input (`UP` when moving `DOWN` = no-op)
- Wrap-around edges: `x = (x + 1) % GRID_W`
- Speed control: update position every N frames (N decreases as score increases)

```c
/* Update only every SPEED_FRAMES */
static u8 s_move_timer;
if (++s_move_timer >= speed_frames) {
    s_move_timer = 0;
    move_snake();
}
```

### 5.5 Racing / Top-Down

Physics pattern:
- Speed: `speed = min(speed + ACCEL, MAX_SPEED)` when A held
- Brake: `speed = max(speed - BRAKE, 0)` when B held
- Steering: modifies `direction` (angle) progressively
- Terrain slowdown: `if (on_grass) speed = max(speed - 2, 0)`

```c
/* Progressive turning */
if (PAD_HELD(PAD_LEFT))  direction = (direction - TURN_RATE) & 0xFF;
if (PAD_HELD(PAD_RIGHT)) direction = (direction + TURN_RATE) & 0xFF;
```

### 5.5b Racing / Forward-Facing Pseudo-3D

> Full technical details: [Effects and Raster](../03_Graphics/Effects-and-Raster.md) §8.

For a forward-facing road with depth-scaling illusion (Mode 7-style, no hardware
scaling on NGPC), a confirmed approach from commercial code:

**Depth → screen position (both axes from one formula):**
```c
#define ZOOM_STEP  25   /* = 200px screen height / 8px tile — constant, all types */

s16 zoom = (range - (entity_z - camera_z)) / ZOOM_STEP;
/* zoom < 0 → cull (behind or beyond far plane) */

u8 sx = 80 - persp_x[zoom];              /* X: road edges converge to center */
u8 sy = (u8)(0x50 - persp_y[zoom]);      /* Y: horizon at top, road at bottom */
/* cull if sy < 8 or sy >= 144 */
```

**Z-sort display list (insertion sort):**
Objects are inserted into a sorted list at activation time (O(n), n≤20).
Traversing in Z order gives correct back-to-front rendering (painters algorithm)
for free, without a separate sort pass each frame.

**Z culling:**
```c
if (entity_z + z_extent < camera_z) → cull;   /* passed behind (large objects) */
if (entity_z - camera_z < 0)        → cull;   /* behind camera origin */
if (entity_z - camera_z > range)    → cull;   /* beyond far plane */
```
`z_extent` allows large objects (long vehicles, buildings) to remain visible even
when their origin Z is behind the camera plane.

**Entity struct (32 bytes):**
```c
struct Entity {
    void  (*render)(Entity *);   /* +0  NULL = free slot */
    u8     _pad[4];
    u32    z;                    /* +8  world Z (depth) */
    u8     type;                 /* +12 zoom table selector */
    u8     visible;              /* +13 0 = skip render */
    u8     _pad2[2];
    u32    z_extent;             /* +16 for back-plane culling */
    u8     data[8];              /* +20 gameplay-specific */
};   /* 32 bytes total */
/* Pool: 20 entities */
```

**Forward motion:** advance `camera_z` by player speed each frame, scroll SCR1
Y register at the same rate. Horizon layer (SCR2) scrolls at a fraction of
road speed for parallax depth cue.
See [Effects and Raster](../03_Graphics/Effects-and-Raster.md) §8.5.

**Road curve / widening (the BG side of the illusion):** beyond the sprite depth system above,
the ground/scenery is warped by a **per-scanline horizontal line-scroll** of both scroll planes
(Timer-0 HBlank ISR, or MicroDMA — ~0 CPU/line). Each visible scanline reads its own SCR1_X/SCR2_X
from a 152-entry double-buffered table; a quadratic curve generator fills the next frame's table.
This is what makes the rails fan out / converge. Full mechanism:
[Effects and Raster](../03_Graphics/Effects-and-Raster.md) §8.1–8.3 and §8.6.

### 5.6 Adventure / Overworld

Screen transition: move off-screen edge → fade → load new screen.

Economy / inventory pattern:
- Store quantities as `u8` or `u16` counters
- Clamp to `MAX_INVENTORY` on pickup
- Display via `ngpc_text_print_num()` on HUD

Event system: array of `{ trigger_type, condition, action }` — evaluate per-frame or
on room entry. Random events: use `ngpc_random()` with probability thresholds.

### 5.7 Roguelike / Procedural Dungeon

Reliable on-device procedural rooms come from **structure, not free randomness**. The
robust shape for NGPC (best reliability / RAM / dev-time trade-off) is a two-pass
pipeline backed by presets in ROM and compact masks in RAM:

```
layout (rooms, exits, semantic role)  →  pick interior PRESET  →  furnish (decor/enemies/items)  →  validate + repair
```

**1. Unified ground truth = compact occupancy masks.** Represent a room as bit masks,
one bit per cell, shared by every system (collision, spawn validation, BFS, stair
placement). For a 16×16 metatile room that is 32 bytes each — negligible RAM:

```c
u8 solid_mask[32];        /* blocks movement (walls, pillars, solid decor) */
u8 hazard_mask[32];       /* pits, spikes, water — damage / block differently */
u8 spawn_forbid_mask[32]; /* no-spawn zones (near entry, on hazards, etc.) */
```

**2. Preset library in ROM, not atomic random walls.** A small catalogue of authored
presets per semantic role (`ENTRY`, `TRANSIT`, `COMBAT`, `TREASURE`, `SAFE`, `STAIR`)
is far more reliable than placing random wall rectangles. Each preset embeds its
`exits_mask`, occupancy masks, hazard layout, and **socket lists** (enemy / item /
decor anchor cells). Hazards (pits, water) are part of the preset, not added free-hand,
and each preset guarantees at least one safe path between all required exits. Only allow
explicitly compatible preset pairs (e.g. don't combine interior walls + water + voids
unless that combination is a validated preset).

**3. Furnish via sockets and anchors, not free cells.** Decor snaps to room anchors
(corners, wall edges, near pillars); enemies/items use the preset's socket lists. Follow
the level structure for pacing: loot in dead-ends, enemies at junctions, traps in
corridors. Keep spawns off `solid`/`hazard` and a minimum distance from the entry.

**4. Always validate with BFS, then repair — don't re-roll blindly.** After generation,
run a flood fill on the metatile grid to check: all required exits reachable from entry;
entry reaches the stair; stair not on hazard / in a cul-de-sac; no socket on solid or
hazard; no hazard isolates a required exit. On failure, walk a **repair fallback chain**
(remove blocking decor → simplify the hazard preset → switch to a simpler layout →
plain room) rather than a global re-roll loop.

**5. Determinism + offline testing.** Generate from a seed so a run is reproducible
(replay, debugging). Constraint-based techniques (WFC) are best used **offline** to
help author a bank of valid presets, then ship only the finished masks in ROM — not at
runtime. Run thousands of seeds offline to measure preset frequency, fallback rate, and
difficulty before shipping.

> Related grid mechanics that pair well with this: pushable blocks (keep state in RAM,
> check `collision_at(target)` for both the actor and the block before moving both; a
> push into a pit consumes the block) and lock/trigger gates (a tile held by the player,
> an enemy, or a pushable opens a door; persist the held/open state per room so it
> restores on re-entry — and only place a trigger if the room actually has something able
> to hold it, or you create a softlock).

---

## 6. Entity Management Patterns

Cross-genre techniques for managing pools of game objects (enemies, bullets,
particles, sprites) within a fixed per-frame time budget.

### 6.1 Per-Frame Update Budget

Cap entity updates at a fixed count per frame (e.g. 30/frame). Entities not reached
this frame hold their last state and are processed on a following frame. This bounds
worst-case main-loop cost and prevents dropped VBlanks (frame overruns) at peak entity
load — predictable slowdown instead of a missed sync.

### 6.2 Static Z-Order via Insertion Sort at Spawn

Insertion-sort each entity into a depth-sorted linked list at spawn time (O(n) per
insert, no per-frame sort). The OAM builder then walks the list in order and emits
sprites back-to-front for free — zero per-frame sort cost. Best when an entity's depth
is fixed for its lifetime; re-insert on the rare depth change.

### 6.3 ROM Dispatch Table for Entity State

Replace switch/case state cascades with an O(1) jump through a ROM table:

```
sll  3,index           ; index *= 8 (entry stride)
ld   XBC,(table+index) ; load handler address
call T,XBC             ; indirect call
```

A JP/JRL-table variant works the same way. Constant cost regardless of state count,
versus a switch whose cost grows with the number of cases.

### 6.4 "This"-Pointer Entity Model

Keep the current entity's base address in `XIZ` while updating it. The state handler
is a function pointer stored at offset `+0x00` of the entity struct, so a state
transition is a single store:

```
ld (XIZ+0x00), new_fn   ; switch handler, no parameter threading
```

The handler reads/writes the rest of the struct through `XIZ`, so no entity pointer
needs to be passed as a parameter.

### 6.5 Ring-Buffer Allocation

Allocate entities/OAM slots from a ring buffer: advance a write pointer on each alloc
and wrap at the end of the pool. O(1) allocation with no free-slot scan. Ideal for
short-lived, high-churn objects (bullets, particles) where overwriting the oldest live
slot on wrap is acceptable.

### 6.6 Despawn Discipline — Killing an Entity Does Not Free Its Sprite

A generic entity pool separates *logic* from *rendering*: the pool tracks an
`active`/`alive` flag, and a separate draw pass turns live entities into sprites. A
minimal `kill` is therefore just:

```c
#define entity_kill(e)  ((e)->active = 0)   /* logic only — touches NO sprite */
```

This is correct **only if** the render side has a matching discipline. On its own,
`active = 0` stops the entity from being *updated and drawn*, but its last OAM slot
keeps drawing the dead object and stays allocated — an invisible-to-logic "zombie"
that leaks the 64-slot budget (see [Sprites-and-OAM §10](../03_Graphics/Sprites-and-OAM.md),
*OAM slot leak*). Whenever you free an entity you must **also** release its sprite,
via one of two disciplines:

- **Owned slot + hide on kill:** each entity holds a fixed OAM slot (or slot range);
  `kill` hides it (`ngpc_sprite_hide` / `ngpc_soam_hide`) as well as clearing the flag.
- **Clear-then-redraw:** hide the whole used OAM range at the top of each frame, then
  re-emit only live entities. Dead ones are simply never drawn — nothing to hide.

The trap is shipping a pool where `kill` flips the flag, the draw loop skips inactive
entities, and *neither* side ever hides the slot. It looks correct — dead entities
vanish from logic — but sprites accumulate until spawns silently fail. If your entity
module's `kill` is logic-only, document which discipline the game side must provide.

---

## Quick Reference

| Pattern | Notes |
|---------|-------|
| State machine | Init/update pair per state, switch on enum |
| OPTION = pause | Universal NGPC convention |
| Edge-triggered | `PAD_PRESSED()` — one-shot actions |
| Held | `PAD_HELD()` — movement, hold-fire |
| Menu auto-repeat | `ngpc_input_set_repeat(15, 4)` |
| Pacing rule | calm → pressure → calm → pressure |
| Speed before density | Tune enemy speed first, then add more enemies |
| Fire cooldown | Per-entity counter, not edge detection |
| Wave spawn spacing | ≥ 54 frames at speed_x=1 to avoid visual overlap |
| Kill zone: platformer | Map bounds (not camera-relative) |
| Snake reverse guard | Ignore input opposite to current direction |
| Racing turn | Progressive angle change per frame |
| Chain combo reward | Multiply points per chain step |
| Update budget | Cap at fixed count/frame (e.g. 30); rest hold state |
| Static Z-order | Insertion-sort into depth list at spawn, no per-frame sort |
| State dispatch | `sll 3,index; ld XBC,(table+index); call T,XBC` — O(1) |
| "This" pointer | Entity base in XIZ, handler fn ptr at +0x00 |
| Ring-buffer alloc | Advance + wrap pointer, O(1), no free-slot scan |
| Despawn discipline | `kill` must free the sprite too (hide slot OR clear-then-redraw), else OAM leak |

---

## See Also

- [Input](../05_Systems/Input.md) — `PAD_PRESSED/HELD/RELEASED` macros, ngpc_input module, auto-repeat
- [Collision](../05_Systems/Collision.md) — AABB tests, bullet pool, tilemap collision
- [Game Loop](../05_Systems/Game-Loop.md) — Main loop structure, VBlank sync, state machine integration
- [Storage and Saves](../05_Systems/Storage-and-Saves.md) — High score save, flash write timing
- [Effects and Raster](../03_Graphics/Effects-and-Raster.md) — Pseudo-3D perspective scaling full tech (§8), persp_x/y tables, pipeline
