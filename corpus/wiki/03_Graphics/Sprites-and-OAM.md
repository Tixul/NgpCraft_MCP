# Sprites and OAM

OAM format, palette indices, chaining, metasprites, budget strategies, the shadow-OAM double-buffer, entity-pool architectures observed in commercial games, and a known-bugs section with confirmed fixes.

> Note: this page uses ASCII only (avoids encoding issues on Windows/PowerShell).

---


## 1. OAM Hardware Format

### 1.1 Sprite VRAM (0x8800)

64 sprites, 4 bytes each. **Byte access.**

```
Sprite n:
  0x8800 + n*4 + 0  : tile index (bits 7-0 of 9-bit index)
  0x8800 + n*4 + 1  : flags
                        bit7    : H flip
                        bit6    : V flip
                        bit4-3  : priority  00=hidden 01=behind 10=middle 11=front
                        bit2    : H chain (extend sprite 8px to the right)
                        bit1    : V chain (extend sprite 8px downward)
                        bit0    : tile index bit 8 (for tiles 256-511)
  0x8800 + n*4 + 2  : X position (pixels)
  0x8800 + n*4 + 3  : Y position (pixels)
```

> **Common mistake:** the struct layout is `tile / flags / x / y` — NOT `y / x / tile / attr`.
> Always write fields in the correct order when building OAM entries manually.

### 1.2 Palette indices (0x8C00)

```
  0x8C00 + n : bits 3-0 = palette number (0-15) for sprite n
```

Palette indices are stored **separately** from OAM flags (unlike some other platforms).
When flushing the shadow OAM, flush both regions: OAM at `0x8800` and palette indices at `0x8C00`.

### 1.3 Flag constants

```c
/* Priority */
SPR_HIDE   = (0 << 3)   /* hidden */
SPR_BEHIND = (1 << 3)   /* behind both scroll planes */
SPR_MIDDLE = (2 << 3)   /* between the two scroll planes */
SPR_FRONT  = (3 << 3)   /* in front of everything */

/* Flip */
SPR_HFLIP  = 0x80
SPR_VFLIP  = 0x40
SPR_HVFLIP = 0xC0

/* Chain (extend sprite size) */
SPR_HCHAIN = 0x04       /* extend 8px right  (use 2 consecutive tile slots) */
SPR_VCHAIN = 0x02       /* extend 8px down   (use 2 consecutive tile slots) */
```

---

## 2. Chaining & Metasprites

### 2.1 H-chain and V-chain

Chain bits extend a sprite beyond 8x8 pixels by consuming consecutive hardware sprite slots:

```
Single 8x8:    slot n  (tile T)
H-chain 16x8:  slot n  (tile T, SPR_HCHAIN) + slot n+1 (tile T+1)
V-chain 8x16:  slot n  (tile T, SPR_VCHAIN) + slot n+1 (tile T+2)
H+V chain:     slot n  (tile T, SPR_HCHAIN|SPR_VCHAIN) + slots n+1, n+2, n+3
```

The hardware fills the additional pixels automatically from consecutive tile indices.
The extra slot's X/Y and flags are ignored by the hardware (only the first slot controls position/flip).

### 2.2 Multi-tile sprites (metasprites)

For sprites larger than 16x16 or needing more than 3 visible colors (per 8x8 tile),
use multiple independent hardware sprite slots with manually computed offsets.

```c
/* 16x16 character = 4 x 8x8 sprites (2 layers of 3 colors each = 6 total colors) */
const NgpcMetasprite player_idle = {
    4,          /* part count */
    16, 16,     /* total width, height (for flip calculation) */
    {
        { 0, 0, TILE_BASE+0, 0, SPR_FRONT },   /* top-left     */
        { 8, 0, TILE_BASE+1, 0, SPR_FRONT },   /* top-right    */
        { 0, 8, TILE_BASE+2, 0, SPR_FRONT },   /* bottom-left  */
        { 8, 8, TILE_BASE+3, 0, SPR_FRONT },   /* bottom-right */
    }
};

/* Draw (slot 0..3 used) */
ngpc_mspr_draw(0, player_x, player_y, &player_idle, SPR_FRONT);

/* Draw facing left (automatic quad swap + per-part flip toggle) */
ngpc_mspr_draw(0, player_x, player_y, &player_idle, SPR_FRONT | SPR_HFLIP);
```

### 2.3 Flip rules for metasprites

When applying H-flip to a multi-tile metasprite, you must:
1. Swap left/right quads: slot pair `(0,1)` <-> `(2,3)` for H-flip
2. Toggle `SPR_HFLIP` on each individual part

**Without the quad swap**, the sprite appears cut in half or mirrored incorrectly.
The `ngpc_mspr_draw()` function handles this automatically.

6-color sprite technique: render the same character twice using two layers
(two sets of sprite slots), each with a 3-color palette. The layers combine
to produce up to 6 visible colors. The sprite exporter supports this
via a two-layer split option.

---

## 3. Budget & Strategy

### 3.1 Hardware limit: 64 sprites

The NGPC OAM holds exactly 64 sprite entries. This is the hard limit.
All 64 are drawn every frame regardless; hiding uses `SPR_HIDE` (priority = 0).

### 3.2 Fixed slot strategy

Assign a permanent slot or slot range to each game object type. Simple and
predictable, no allocation overhead.

```c
#define SLOT_PLAYER    0   /* slots 0-3 (2x2 metasprite) */
#define SLOT_ENEMIES   4   /* slots 4-19 (up to 8 enemies, 2 slots each) */
#define SLOT_BULLETS  20   /* slots 20-35 (up to 16 bullets, 1 slot each) */
#define SLOT_HUD      36   /* slots 36-63 (HUD, score, icons) */
```

**Despawn discipline (mandatory).** With fixed slots, killing an object's *logic*
does not touch its OAM slot. The slot keeps its last tile/flags/position and stays
on screen until you explicitly hide it. Every despawn path must call
`ngpc_sprite_hide(slot)` (or `ngpc_soam_hide(slot)` in shadow-OAM builds):

```c
void enemy_kill(u8 i) {
    enemy[i].alive = 0;
    ngpc_sprite_hide((u8)(SLOT_ENEMIES + i * 2));      /* free BOTH OAM slots */
    ngpc_sprite_hide((u8)(SLOT_ENEMIES + i * 2 + 1));
}
```

Forget this and the sprite becomes a "zombie": invisible to your game logic but
still occupying an OAM slot (see §10, *"Sprites vanish while fewer than 64 are
on screen"*). The alternative is the clear-then-redraw discipline of §3.3.

### 3.3 Pool strategy

For variable-count objects, maintain a bitmask and allocate slots dynamically.
A pool needs a matching **free** that both releases the bit **and** hides the slot —
releasing the bit alone leaves a visible zombie:

```c
static u16 s_spr_mask = 0; /* bit N = slot N is taken */

s8 spr_alloc(u8 count) {
    u8 i;
    for (i = 0; i <= 64 - count; i++) {
        /* check 'count' consecutive free bits */
        u16 mask = ((1 << count) - 1) << i;
        if (!(s_spr_mask & mask)) {
            s_spr_mask |= mask;
            return (s8)i;
        }
    }
    return -1; /* no space */
}

void spr_free(u8 slot, u8 count) {
    u8 k;
    for (k = 0; k < count; k++) {
        s_spr_mask &= (u16)~(1u << (slot + k));
        ngpc_sprite_hide((u8)(slot + k));   /* hide, or the slot stays on screen */
    }
}
```

> **Two safe disciplines — pick one, never neither:**
> 1. **Fixed slot / pool + explicit hide on despawn** (above): the object owns its
>    slot and hides it when it dies.
> 2. **Clear-then-redraw:** call `ngpc_sprite_hide_all()` (or a range clear) at the
>    top of every frame, then re-emit only live objects. A killed object simply
>    isn't re-emitted, so its slot was already cleared — nothing leaks.
>
> The shadow-OAM tail-clear (§4, §5) is discipline 2 applied to the used range only.
> The common failure is doing *neither*: kill only flips an `alive` flag, and the
> draw loop skips dead objects without ever hiding their slot → zombies accumulate.

### 3.4 Sprite multiplexing (sprmux) — ABANDONED

> **Not recommended — do not use.**
> Hardware tests confirmed that the HBlank ISR budget (~30 cycles) is too short
> to flush OAM on the fly for any realistic sprite count.
>
> **Validated alternative:** DMA-based mass OAM update — see [DMA.md](DMA.md).

The original approach recycled hardware OAM slots each HBlank via Timer0 interrupt
(Y-sort + reassign as sprites leave scanline visibility).
The concept works in principle but fails in practice on NGPC hardware: the Timer0
HBlank window cannot accommodate the required memory operations before the next
scanline begins, causing visual corruption at any non-trivial sprite count.

For >64 logical sprites, the validated solution is a single DMA copy from shadow OAM
to `0x8800` in VBlank — zero HBlank pressure, production-safe, hardware-confirmed.

---

## 4. Performance Checklist

- **Shadow OAM:** build the full OAM state in RAM during main loop,
  flush atomically in VBlank. Never write individual sprite registers mid-frame.
- **Move-only writes:** if only X/Y changed, update only `0x8800+n*4+2` and `+3`.
  Skipping the tile/flags bytes saves cycles when animating large sprite counts.
- **Tail-clear:** after flush, clear the remaining slots (slots `used..63`) once per frame.
  `ngpc_soam_flush_partial()` does this automatically.
- **Palette index flush:** upload the full 64-byte `shadow_col[]` array to `0x8C00`
  once per frame using LDIRW (32 words). Do not update palette indices mid-frame.
- **Tile upload:** only re-upload tile data when the asset changes.
  Tile RAM is persistent; avoid unnecessary re-uploads in the main loop.
- **Tile base conflicts:** ensure sprite tile base does not overlap with
  tilemap tile base or system font (slots 32-127). Tile VRAM is shared.
- **Character Over:** if `HW_STATUS & 0x80` fires, too many sprites overlap
  on one scanline. Reduce overlap or use priority to hide lower-priority sprites.

---

## 5. Shadow OAM Double-Buffer

Optional module: `ngpc_soam`.

### 5.1 Principle

Build the full OAM state in a RAM shadow during game logic,
then push atomically to hardware (`0x8800` + `0x8C00`) during VBlank via LDIRW.
No screen tearing: hardware is updated in one burst.

- Shadow buffers: `shadow_oam[256 bytes]` + `shadow_col[64 bytes]` = 320 bytes RAM
- Confirmed by reverse engineering of a commercial action title:
  shadow OAM in RAM, flush to `0x8800` via LDIRW + tail-clear.
  Palette indices to `0x8C00` as a separate 64-byte copy.
- Implementation note: some commercial routines pack the palette ID into unused flag bits
  in the shadow, then extract it during flush (`pal = (flags >> 1) & 0x0F`).
  Keeping `shadow_col[]` separate is simpler.

### 5.2 API

```c
#include "ngpc_soam/ngpc_soam.h"

void ngpc_soam_begin(void);
/* Start of frame: reset internal slot counter */

void ngpc_soam_put(u8 slot, u8 x, u8 y, u16 tile, u8 flags, u8 pal);
/* Write one sprite to shadow RAM (does NOT write hardware) */

void ngpc_soam_hide(u8 slot);
/* Hide one slot in shadow (does not advance the slot counter) */

void ngpc_soam_flush(void);
/* Push shadow -> hardware: LDIRW all 64 slots (128 u16 OAM + 32 u16 pal idx)
   Call ONLY from VBlank ISR. */

void ngpc_soam_flush_partial(void);
/* Performance variant: LDIRW only slots 0..used-1, then hardware-clear the rest.
   Saves time when few sprites are active. */

void ngpc_soam_hide_all(void);
/* Immediately clear all 64 hardware sprite slots (priority = SPR_HIDE) */

u8 ngpc_soam_used(void);
/* Returns the high-water-mark (number of slots used this frame) */
```

### 5.3 VBlank integration

```c
/* VBlank ISR: */
extern void ngpc_soam_flush(void);

static void __interrupt isr_vblank(void) {
    HW_WATCHDOG = WATCHDOG_CLEAR;
    ngpc_soam_flush();    /* push shadow OAM to hardware */
    ngpc_vramq_flush();   /* flush queued VRAM writes */
    g_vb_counter++;
}

/* In game loop — build shadow during logic: */
void game_render(void) {
    ngpc_soam_begin();
    for (i = 0; i < enemy_count; i++) {
        ngpc_soam_put(i, enemy[i].x, enemy[i].y,
                      TILE_BASE + enemy[i].tile, SPR_FRONT, enemy[i].pal);
    }
    /* flush happens automatically in next VBlank ISR */
}
```

Drop-in replacement for `ngpc_sprite_set()`:
```c
/* Before (direct HW write, risk of tearing): */
ngpc_sprite_set(0, px, py, TILE_BASE, 0, SPR_FRONT);

/* After (buffered, tear-free): */
ngpc_soam_begin();
ngpc_soam_put(0, px, py, TILE_BASE, SPR_FRONT, 0);
/* ngpc_soam_flush() called automatically in VBlank ISR */
```

### 5.4 ASM flush implementation

For performance, `flush()` and `flush_partial()` are implemented in TLCS-900H assembly.
The C implementation uses byte-by-byte loops (256 + 64 iterations), which is too slow
for VBlank. The ASM port replaces these with two LDIRW instructions:

```
flush()         : LDIRW 128 words (OAM)  + LDIRW 32 words (pal idx)
flush_partial() : LDIRW N words (used slots) + hardware-clear tail
```

Module split:
```
ngpc_soam/
    ngpc_soam_c.c        compiled C  : begin / put / hide / hide_all / used + variables
    ngpc_soam_flush.asm  compiled ASM: flush / flush_partial via LDIRW
```

Variables `s_oam[]`, `s_col[]`, `s_used`, `s_used_prev` are declared non-static
in the C source so the linker makes them visible to the `.asm` file via `extern`.

TLCS-900H assembly pitfalls encountered during the ASM port:
- `INC r` is invalid — mandatory form is `INC 1, r` (MAXIMUM strict mode)
- `LD (HL), n` does not exist — store via intermediate register
- `LD (XHL), c` — `c` is ambiguous with the Carry condition code — use `e` + `(XHL+d)`
- `(XHL+d)` required for indirect addressing (not `(HL)` directly)
- Upper bits of XHL must be zero: `LD XHL, 0` before use
- LDIRW with BC=0 = 65536 iterations — always guard at runtime if BC is variable
- Warning 501 on `ld hl, extern_symbol`: harmless, linker resolves correctly

### 5.5 Hardware validation

Validated on real hardware. Four test cases passed:
- `ngpc_soam_put()` + D-pad: 2x2-tile player sprite moves correctly
- Tail-clear: cycling enemies 8->4->0->8, extra slots hidden automatically
- `ngpc_soam_hide_all()`: everything hidden immediately
- `ngpc_soam_used()`: live slot counter displayed correctly

---

## 6. Entity Pool & Spawn Table

Architecture proven by reverse engineering a commercial platformer binary.

### 6.1 Pool layout

```c
/* Main loop:
   XIX = pool RAM base
   XIZ = spawn table pointer (ROM, current level) */

for each entity at XIX {
    u16 type = *(XIX+0x00);   /* 0xFFFF = end of pool (sentinel) */
    if (type == 0xFFFF) break;
    if (*(XIZ+0x00) == 0) skip; /* entity inactive */

    /* +0x04, +0x06 : X/Y position offsets (world base) */
    /* +0x08 : u32 FAR ptr to spawn data in ROM */
    /* +0x0A : flags: bit6=flipX, bit7=flipY, high bits=tile attr */
    /* +0x0B : palette byte (copied to shadow_col[]) */
}
```

### 6.2 Spawn table ROM

Each entry in the current level ROM table (4 bytes):
```
+0x00  u16  type (0xFFFF = end of table)
+0x02  s8   spawn_x (relative to map)
+0x03  s8   spawn_y (relative to map)
```

The current level ID is stored in a fixed ROM byte (0xFF = last level / end of game).

### 6.3 Shadow OAM builder

```c
/* Setup at start of frame: */
camera_x     = *(s16*)0x801E;           /* SCR1_X HW -> stored to RAM */
camera_y     = -(*(u8*)0x5085 + 1);    /* SCR1_Y shadow inverted -> RAM */
sprite_count = 0;
XIY = 0x4B6E;   /* shadow OAM destination */
XIZ = 0x4C6E;   /* shadow palette indices destination */

for each active entity {
    screen_x = spawn_x + camera_x;   /* world -> screen projection */
    screen_y = spawn_y + camera_y;

    if (screen_x > 0xA6) continue;   /* clip right (166px) */
    if (screen_y > 0x9E) continue;   /* clip bottom (158px) */
    if (sprite_count >= 64) break;   /* hardware limit */

    tile    = 0x1BF + sprite_count;       /* sequential tile slot in VRAM */
    attr_hi = flags | (0x1BF >> 8);      /* high bits + flags */

    (XIY+) = tile_lo | (attr_hi << 8);  /* OAM word0 */
    (XIY+) = x | (y << 8);              /* OAM word1 */
    (XIZ+) = palette_byte;              /* palette index */
    sprite_count++;
}

/* Clear tail [sprite_count..63] in shadow, then flush in VBlank: */
/* LDIRW [0x8800] <- [0x4B6E], BC=0x80  (OAM, 256 bytes = 128 words) */
/* LDIRW [0x8C00] <- [0x4CF0], BC=0x20  (pal idx, 64 bytes = 32 words) */
```

**Tile allocation pattern:** this engine allocates a fresh sequential tile slot per entity
each frame (`tile = base + sprite_count`). Different from the fixed-slot approach
(which assigns a permanent slot per entity), but simpler to implement.

### 6.4 CPU Budget Throttle

This engine caps entity updates at **30 per frame** to prevent VBlank overruns during entity-heavy scenes.

**Frame reset** (called once per frame before pool loop):
```asm
ld  (0x4cef), 0x1E   ; reset budget counter to 30
; also resets: pool list head, OAM slot counter, sprite_count, etc.
```

**Per-entity check** (called at start of each entity's update function):
```asm
cp  (0x4cef), 0x0    ; budget exhausted?
jr  Z, skip_update   ; yes — skip this entity (no movement, no draw)
dec (0x4cef)         ; no  — consume one update slot
; entity update code follows
```

**Result:** when more than 30 entities are active, entities beyond the cap hold their
last position until budget is available next frame. At 60fps this is generally invisible.

**C equivalent:**
```c
/* main_loop_frame_start(): */
entity_budget = 30;

/* update_entity() entry: */
if (entity_budget == 0) return;
entity_budget--;
/* ... update logic ... */
```

Set the cap to match the game's expected entity count under normal load; adjust up or
down based on profiling.

---

## 7. Entity Struct & State Machine

Architecture proven by reverse engineering a commercial run-and-gun action binary.

### 7.1 Pool layout

```
Base address : 0x4000
Pool size    : 0xB88 words = 11,792 bytes
Entry size   : ~0x90 bytes (144 bytes per entity)
Active list head : address 0x5A00
Sentinel tail    : address 0x5950
entity_count     : address 0x65BD (decremented in entity_free)
```

Linked list pointers per entity:
- `+0x04` / `+0x06` : next/prev in **active** list
- `+0x20` / `+0x22` : next/prev in **free** list

### 7.2 Entity struct (key offsets)

```
+0x00  ptr32   State function pointer (state machine, called every frame)
+0x04  u16     Next ptr (active list)
+0x06  u16     Prev ptr (active list)
+0x10  u16     Sprite flags (bit6 = "live")
+0x11  byte    Direction (bit0 = flip X)
+0x13  byte    Control flags (bit6 = OAM active, bit7 = culled)
+0x16  s16     Screen X position (camera-relative)
+0x1E  u16     Timer (decremented per frame)
+0x20  ptr32   Next free (free list)
+0x26  u16     World X position (absolute)
+0x2A  u16     World Y position
+0x30  s16     Velocity X (fixpoint)
+0x36  s16     Velocity Y
+0x3C  byte    State flags (bits 2/3/6)
+0x3E  u16     Death/flash timer
+0x44  s16     Next state address (dispatcher)
+0x46  u16     Animation counter
+0x48  byte    HP / life points
+0x54  ptr32   Parent entity pointer (e.g., boss segment -> boss)
+0x58  s16     Screen X (camera-projected)
+0x5A  s16     Screen Y (camera-projected)
+0x60  byte    Sprite width in tiles
+0x62  byte    Sprite height in tiles
+0x66  u16     Angle / heading (8:8 fixpoint)
+0x6A  byte    Current mode (0xFF = just spawned)
+0x74  u16     Frame delay / animation timer
+0x76  u16     Tile base index
+0x7A  ptr16   Metasprite pointer (near, low ROM)
+0x7E  u16     Metasprite flags
```

### 7.3 Function-pointer state machine

```c
/* Change state: store new function pointer at +0x00 */
entity->state = &state_walk;

/* Dispatcher, called every frame: */
(*(void(*)())entity->state)();   /* call current state handler */

/* Global scheduler: */
next_state_fn  = ptr_next_function;  /* next global state */
action_flags  |= 8;                  /* flag "new action" */
wait_frames    = N;                  /* frames to wait */
bios_return_hook = 0x82C0;           /* BIOS return hook */
```

Benefit: no dispatch table, no switch/case. Cost: 4 bytes per entity for the state pointer.
This is the universal NGPC game-object pattern observed across multiple commercial titles.

---

## 8. Object Struct & Script Engine

This architecture pushes the "state machine via function pointer" pattern to its maximum,
as observed in a commercial NGPC title. Register XIZ serves as `this` (equivalent to the
C++ `this` pointer). Nearly every game object follows this schema.

### 8.1 XIZ object struct (~104 bytes)

```
Offset  | Size  | Role
--------+-------+--------------------------------------------------
+0x00   | ptr32 | State function pointer (state machine)
+0x09   | byte  | Active / enable flag
+0x1C   | byte  | Mode flags (bits 0..3)
+0x1D   | byte  | Object-local frame counter
+0x20   | ptr32 | Current data pointer (XIY ptr)
+0x22   | byte  | Animation frame counter
+0x24   | ptr32 | Secondary animation data pointer
+0x28   | byte  | Current animation frame ID
+0x2A   | byte  | Sprite flags (H/V flip, etc.)
+0x2B   | byte  | Render mode (0x30 = 2-layer sprite)
+0x2C   | ptr32 | Current position in script sequence
+0x2F   | byte  | Palette index override
+0x30   | byte  | Current frame data
+0x31   | byte  | Repeat count
+0x32   | byte  | Speed scale (1=100%)
+0x33   | byte  | Direction flags (bit7 = mirror X)
+0x34   | byte  | X position (camera-relative)
+0x35   | byte  | Y position (camera-relative)
+0x38   | ptr32 | Main animation data pointer
+0x3A   | s8    | Velocity X
+0x3B   | s8    | Velocity Y
+0x3C   | s8    | Acceleration X
+0x3D   | s8    | Acceleration Y
+0x3E   | byte  | Hitbox / camera width
+0x3F   | byte  | Hitbox / camera height
+0x40   | byte  | Timer reset value
+0x42   | byte  | World X position (clamped, for camera)
+0x44   | byte  | World Y position (clamped)
+0x48   | byte  | Screen X position (post-clamp)
+0x4A   | byte  | Screen Y position (post-clamp)
+0x4C   | byte  | Current animation frame index
+0x4D   | byte  | Current animation ID
+0x4E   | byte  | General-purpose countdown timer
+0x4F   | byte  | End-of-sequence flag
+0x50   | byte  | Current state ID
+0x52   | byte  | Next state ID
+0x64   | ptr32 | State machine pointer (read-only backup)
```

### 8.2 Function-pointer state machine (XIZ+0x00)

Identical to the entity-pool pattern, but used for ALL objects (more systematic):

```asm
; Change state: write new handler into XIZ+0x0
ld (XIZ+0x0), XIX    ; XIX = address of new state function

; Dispatcher (called every frame):
call (XIZ+0x0)       ; indirect call through state pointer
```

```c
/* C equivalent: */
typedef void (*StateFunc)(void);
((StateFunc)(obj->state_ptr))();        /* execute current state */
obj->state_ptr = (u32)&new_state_func; /* transition to new state */
```

The pointer IS the state. No dispatch table, no switch/case. Cost: 4 bytes per object.

### 8.3 Script engine bytecode

This title implements a mini-interpreter for animation and behavior sequences.
Bytecode is stored in ROM, pointed to by `XIZ+0x2C`.

Format of one script element (1 word + 1 dword):
```
(ptr+0) word  : { B=type, C=frame_id }
(ptr+2) dword : sprite data address for this frame
```

Opcodes (value in B):
```
B > 0    : frame duration (show this frame for B ticks)
B == 0   : end of sequence
B == -1  : loop back to start
B == -2  : jump offset -2
B == -3  : special event
B == -5  : callback
```

Main interpreter routine:
1. Read BC from `(XWA)` — XWA = current sequence pointer
2. If B > 0: load `XIY = (XWA+2)` (sprite data), increment frame counter
3. If B <= 0: dispatch via a 12-entry jump table

Compact format: 6 bytes per frame (1 word header + 1 dword data pointer).
Used universally for animations, behaviors, and cutscenes.

### 8.4 Sprite format conversion

A converter routine maps the internal sprite format to NGPC OAM hardware:
```
Source (1 word, internal):
  bits 11..8 : palette index (4 bits)
  bits  7..0 : tile index

Destination (NGPC OAM format):
  QD = tile index low byte
  QE = palette index (shifted right 4)
```

---

## 9. Graphics Pipeline: PNG to Screen

### 9.1 Method A: helper functions

```c
#include "ngpc_gfx.h"
#include "../GraphX/my_tileset.h"

#define TILE_BASE 128u   /* avoid overwriting sysfont (slots 32-127) */

static void scene_init(void) {
    u16 i;

    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    /* Load tiles (NGP_FAR handled internally by the helper) */
    ngpc_gfx_load_tiles_at(my_tileset_tiles,
                           my_tileset_tiles_count,
                           TILE_BASE);

    /* Load palettes */
    for (i = 0; i < (u16)my_tileset_palette_count; ++i) {
        u16 off = (u16)i * 4u;
        ngpc_gfx_set_palette(GFX_SCR1, (u8)i,
            my_tileset_palettes[off + 0],
            my_tileset_palettes[off + 1],
            my_tileset_palettes[off + 2],
            my_tileset_palettes[off + 3]);
    }

    /* Write tilemap */
    for (i = 0; i < my_tileset_map_len; ++i) {
        u8  x   = (u8)(i % my_tileset_map_w);
        u8  y   = (u8)(i / my_tileset_map_w);
        u16 tile = (u16)(TILE_BASE + my_tileset_map_tiles[i]);
        u8  pal  = (u8)(my_tileset_map_pals[i] & 0x0Fu);
        ngpc_gfx_put_tile(GFX_SCR1, x, y, tile, pal);
    }
}
```

### 9.2 Method B: direct VRAM blit macro

Use when debugging near/far pointer issues or when Method A produces corrupted output:

```c
#include "ngpc_tilemap_blit.h"
#include "../GraphX/my_tileset.h"

#define TILE_BASE 128u

static void scene_init(void) {
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    NGP_TILEMAP_BLIT_SCR1(my_tileset, TILE_BASE);
}
```

This macro writes tiles directly to Character RAM (`0xA000`), the tilemap directly
to `HW_SCR1_MAP` (`0x9000`), and loads palettes via `ngpc_gfx_set_palette()`.
No pointer argument passing — avoids near/far issues completely.

Diagnostic rule: if Method B renders correctly but Method A does not,
the issue is a near/far pointer problem in the helper call.
If both fail, the asset itself is corrupted or video init is wrong.

### 9.3 Tilemap constraints

| Constraint | Value |
|-----------|-------|
| Scroll plane map size | 32x32 tiles |
| Visible screen area | 20x19 tiles (160x152 px) |
| Free tile slots | 128-511 (0-31 reserved, 32-127 = BIOS sysfont) |
| Palettes per plane | 16 palettes x 4 colors, format `0x0BGR` |
| Palette 0, color 0 | Always transparent on scroll planes |
| Total tile VRAM | 512 tiles (Character RAM = 8 KB) |
| Max colors per tile | 3 visible + 1 transparent |
| Max palettes (budget) | 16 per plane |

`tiles_count` from the tilemap export tool = number of `u16` words (= `nb_tiles * 8`),
**not** the number of tiles.

### 9.4 Debug checklist

1. **Palettes:** loaded on the correct plane? SCR1 and SCR2 have separate palette RAM.
2. **Tile base:** not overwriting sysfont? Use `tile_base >= 128`.
3. **NGP_FAR:** all pointers to ROM (`0x200000+`) declared with `NGP_FAR`?
4. **Method B test:** does `NGP_TILEMAP_BLIT_SCR1` render correctly?
   - Yes -> asset is fine, problem is near/far in helper
   - No  -> check asset generation or video init
5. **Raw data check:** verify generated `tiles[]`, `map_tiles[]`, `palettes[]`
   byte-by-byte before blaming the C pipeline

---

## 10. Known Bugs & Solutions

**[ngpc_soam] Blank screen at startup / watchdog reset loop (hardware)**
- Symptom: ROM boots, white screen, resets after ~100ms, repeats.
- Root cause: `for (u8 i = 0; i < SPR_MAX * 4; i++)` with `SPR_MAX=64`
  gives limit 256. `u8` overflows `255->0` -> infinite loop in `isr_vblank()` ->
  watchdog never fed -> hardware reset.
- Fix: use `u16 b` for the OAM loop counter (256 iterations minimum).
- Rule: **never use `u8` for a loop counter if iteration count can reach 256.**
  Same family as `u16 y * SCR_MAP_W` overflow.
```c
/* WRONG: infinite loop */
u8 i;
for (i = 0; i < SPR_MAX * 4; i++) hw_oam[i] = s_oam[i];

/* CORRECT: u16 counter */
u16 b;
for (b = 0u; b < (u16)SPR_MAX * 4u; b++) hw_oam[b] = s_oam[b];
```

**[ngpc_soam] Sysfont text invisible on dark background**
- Symptom: sprites visible and working, zero text on screen.
- Root cause: palette 0 of GFX_SCR1 never initialized -> color 1 (sysfont foreground)
  = `0x000` (black) -> black text on dark background.
- Fix: always call `ngpc_gfx_set_palette(GFX_SCR1, 0u, ...)` in scene init,
  after `ngpc_gfx_fill()`. Mandatory for any project using text rendering.
```c
/* After ngpc_gfx_fill(GFX_SCR1, ' ', 0u): */
ngpc_gfx_set_palette(GFX_SCR1, 0u,
    RGB(0,  0,  0),    /* color 0: transparent */
    RGB(15, 15, 15),   /* color 1: white (sysfont foreground) */
    RGB(8,  8,  10),   /* color 2: light gray */
    RGB(3,  5,  15));  /* color 3: blue accent */
```

**Sprites appear as tilemap tiles / tile base conflict**
- Symptom: sprite slots show terrain tiles or garbage from another asset.
- Root cause: sprite tile base overlaps with tilemap or background tile base.
  When backgrounds are re-uploaded (state transition, level load), they overwrite sprite tiles.
- Fix: plan tile VRAM layout explicitly. Assign separate, non-overlapping ranges:
  tile 0-31 = reserved, 32-127 = sysfont, 128-N = backgrounds, N+1..511 = sprites.
  Regenerate all assets if the layout changes.

**Sprite appears "cut in half" when H-flipped**
- Symptom: multi-tile sprite appears mirrored on one side only, or two halves don't align.
- Root cause: H-flip toggled on each part but left/right quad positions not swapped.
- Fix: for H-flip on a multi-tile sprite, swap the left and right columns of parts
  AND toggle `SPR_HFLIP` on each individual part. Same logic applies for V-flip
  (swap top/bottom rows). The `ngpc_mspr_draw()` function handles this correctly.

**Stale sprites from previous state visible on screen**
- Symptom: sprites from title screen, menu, or previous game state remain visible
  after a state transition.
- Root cause: sprite slots not explicitly cleared on state exit.
- Fix: call `ngpc_soam_hide_all()` or `ngpc_sprite_hide_all()` in every `_init()`
  before setting up the new state's sprites.

**Sprites vanish while fewer than 64 are on screen (OAM slot leak)**
- Symptom: new sprites fail to appear — enemies, bullets, or pickups stop spawning —
  even though you can visibly count far fewer than 64 on screen (e.g. "it caps out at
  ~45"). Often paired with a one-frame flicker when a slot is reused.
- Root cause: **killing an entity does not free its OAM slot.** A despawn that only
  clears a logic flag (`e->alive = 0`, `e->active = 0`) or drops the entity from an
  update list leaves its last-written OAM entry in `0x8800` untouched. The slot is
  still "occupied" as far as the hardware and any allocator are concerned: it keeps
  drawing the dead object's old tile at its old position. These invisible-to-logic
  "zombie" slots accumulate until the pool is full — so you exhaust the 64-slot budget
  with only a handful of *live* objects actually visible.
- Distinguishing it from the per-scanline limit: the per-scanline cap (`HW_STATUS &
  0x80`, "Character Over", §4) drops sprites that *overlap on the same line* and they
  reappear when they separate. A slot leak is permanent and independent of position —
  the count only ever goes up. Dump OAM (`ngpc_emu_oam_info`) and compare occupied
  slots against the number of entities your logic thinks are alive; a large gap = leak.
- Fix: enforce one of the two despawn disciplines in §3.2/§3.3 — either hide the slot
  on every kill path (`ngpc_sprite_hide` / `ngpc_soam_hide`), or clear-then-redraw each
  frame so dead objects are simply never re-emitted. Never let a kill flip a flag
  without also releasing the slot.

**"Bullet time" / frame lag with many sprites and projectiles**
- Symptom: game slows down noticeably when several sprites and bullets are active.
- Root cause: full OAM write (tile + flags + X + Y) for every slot every frame,
  even when only position changed.
- Fix: use move-only writes when only X/Y changed. Use shadow OAM with
  `flush_partial()` (only active slots). Avoid unnecessary tile re-uploads.

**Corrupted graphics / "garbage" tiles**
- Symptom: tile or sprite data looks like random noise or wrong asset data.
- Root cause: near/far pointer mismatch. ROM assets at `0x200000+` accessed without
  `NGP_FAR` -> the address is truncated to 16 bits -> reads from wrong location.
- Fix: always declare ROM asset pointers with `NGP_FAR`.
  Use Method B (direct VRAM blit macro) to isolate the issue.

---

## 11. OAM Watermark & Dynamic Tile Upload

This section documents an OAM watermark and dynamic-tile-upload technique observed in a
commercial pseudo-3D simulation title. All addresses, byte values, and patterns are
confirmed from reverse engineering.

### 11.1 Context

The title's sprite engine manages up to 64 OAM slots for a variable number of objects each
frame (objects appear and disappear as the view moves forward). It uses a **dynamic
watermark** instead of fixed slot ranges, which is a cleaner allocation model for scenes
with variable sprite counts.

### 11.2 OAM Pool Init

```asm
oam_pool_init:
  ld  XWA, 0x8800          ; HW OAM base
  ld  (0x44A0), XWA        ; watermark = start of OAM
  ld  WA,  0x1186          ; starting tile index (4486)
  ld  BC,  0xF8F8          ; X=0xF8, Y=0xF8 (off-screen, hidden)
  ld  XIX, 0x8800
  ld  E,   0x40            ; 64 sprites
loc_:
  ldw (XIX+), WA           ; write tile + flags
  ldw (XIX+), BC           ; write X=0xF8, Y=0xF8
  inc 1, XWA               ; next tile
  djnz E, loc_
  ret
```

All 64 slots initialized to consecutive tile indices starting at 0x1186, hidden at
(0xF8, 0xF8). The watermark `(0x44A0)` tracks where the used portion ends.

### 11.3 OAM Watermark — Dynamic Cursor

Instead of assigning fixed slot ranges per object type, this engine uses a single
advancing cursor:

- **RAM `0x44A0`**: current OAM write pointer (byte address inside `0x8800..0x88FF`).
  Incremented by 4 each time a sprite is submitted. Reset to `0x8800` each frame.
- **RAM `0x44AA`**: number of sprites used this frame (slot count, not byte count).
  Used to size the LDIRW flush in VBlank.
- No per-type slot budgets. All 64 slots are available to any object type.

C equivalent:
```c
static u8 oam_cursor = 0;   /* slot index, reset to 0 each frame */

u8 oam_submit(u8 x, u8 y, u16 tile, u8 flags) {
    volatile u8 *p;
    if (oam_cursor >= 64) return 0xFF;   /* overflow guard (threshold 0x41) */
    p = (volatile u8 *)(0x8800u + (u16)oam_cursor * 4u);
    p[0] = (u8)tile;
    p[1] = flags | (u8)((tile >> 8) & 1u);
    p[2] = x;
    p[3] = y;
    return oam_cursor++;
}
```

### 11.4 Tail-Clear — XY Only

After flushing the active sprites for this frame, slots that were used last frame but are
no longer needed are cleared by writing **only the XY bytes** (offset +2 in the 4-byte
slot). The tile_id and flags are left intact.

```asm
; Tail-clear: from old watermark down to new watermark
ld   WA, 0xF8F8           ; X=0xF8, Y=0xF8
ld   XIZ, (0x44A0)        ; old watermark (frame N-1)
ld   (0x44A0), XIX        ; update watermark to frame N
loop:
  cp   XIX, XIZ
  jr   PL, done
  ldw  (XIX+2), WA        ; write ONLY bytes +2,+3 (X and Y)
  inc  4, XIX             ; advance by full slot size
  jr   loop
```

**Why XY only:** writing 2 bytes instead of 4 halves the memory traffic for the
tail-clear pass. The tile_id retained in the slot also lets the hardware recycle
the slot without re-initializing it. Confirmed: `ldw (XIX+2), WA` targets byte
offset +2 within the 4-byte OAM entry (the X byte), which is consistent with the
documented layout `tile / flags / x / y`.

### 11.5 Pre-Baked Scaling — Frame List Format

No hardware scaling on NGPC. The depth-scaling illusion is achieved by selecting from a
set of pre-drawn sprite sizes depending on the object's computed zoom level.

Each frame_list entry is **5 bytes**:
```
+0  u16  tile_index   ; index into ROM tile bank (0 = end-of-list sentinel)
+2  u8   x_off        ; X offset from entity screen origin
+3  u8   y_off        ; Y offset
+4  u8   flags        ; SPR_HFLIP, SPR_VFLIP, priority bits
```

Consumer loop:
```asm
loc_:
  ld   IY, (XIY+)      ; IY = tile_index (u16), XIY advances +2
  and  XIY, XIY
  jrl  Z, done         ; tile_index=0 → end of list
  inc  1, E            ; E = slot counter (max 0x40 = 64)
  ld   BC, (XIY+)      ; C = x_off, B = y_off
  ld   A, (XIY+)       ; A = flags
  add  C, W            ; C += screen base X (W register)
  add  B, W            ; B += screen base Y
  ld   (XIX+), D       ; write attr (D = palette+priority, preloaded)
  ldw  (XIZ+), BC      ; write X,Y to shadow palette region
  jr   loc_
```

The `D` register carries the palette+priority byte for all parts of one object
(preloaded before calling the renderer). Its bits encode priority (bits 4-3) and
palette (bits 2-0) in the same format as the OAM flags byte.

### 11.6 Dynamic Tile Upload Accumulator

This engine does **not** keep static tile data in VRAM. Instead, it uploads only the
tiles that will be visible this frame, into a fixed VRAM window. Each entity's
renderer appends its tile data to a RAM accumulator buffer during game logic,
then a single `ldirw` flushes the whole batch before VBlank:

```asm
tile_upload_flush:
  ld   BC, (0x5630)          ; BC = number of tiles queued this frame
  sll  3, XBC                ; * 8 words per tile (8 u16 = 16 bytes = 1 tile 8x8 2bpp)
  lda  XIX, (0xB010)         ; XIX = VRAM tile write ptr (stored in RAM)
  lda  XIY, (0x4D70)         ; XIY = RAM accumulator buffer base
  ldw  (0x5630), 0x0000      ; reset tile counter to 0
  ldirw (XDE+),(XHL+)        ; flush: copy accumulator → VRAM in one pass
  ret
```

Key addresses:
- `0x4D70` — RAM tile upload buffer (accumulator)
- `0x5630` — tile count queued this frame (reset to 0 after flush)
- `0xB010` — VRAM tile write pointer (stored in RAM, advances each frame)

**When to use this pattern:**
This is appropriate when total unique tiles across all visible objects exceeds the
available VRAM tile window, and objects change each frame. For static scenes or games
with a fixed sprite set, a one-time VRAM upload at level load is simpler and faster.

---

## Quick Reference

| Item | Address / Value | Notes |
|------|----------------|-------|
| OAM base | `0x8800` | 64 sprites x 4 bytes |
| Palette indices | `0x8C00` | 64 bytes, 1 per sprite |
| OAM entry layout | tile / flags / x / y | byte order within 4-byte entry |
| Priority hidden | `SPR_HIDE = 0x00` | bits 4-3 = 00 |
| Priority behind | `SPR_BEHIND = 0x08` | bits 4-3 = 01 |
| Priority middle | `SPR_MIDDLE = 0x10` | bits 4-3 = 10 |
| Priority front | `SPR_FRONT = 0x18` | bits 4-3 = 11 |
| H-flip | `SPR_HFLIP = 0x80` | bit 7 |
| V-flip | `SPR_VFLIP = 0x40` | bit 6 |
| H-chain | `SPR_HCHAIN = 0x04` | bit 2 |
| V-chain | `SPR_VCHAIN = 0x02` | bit 1 |
| Tile index bit 8 | flag bit 0 | for tiles 256-511 |
| Shadow OAM RAM | 320 bytes | `s_oam[256]` + `s_col[64]` |
| OAM flush | LDIRW 128 words | in VBlank ISR only |
| Pal idx flush | LDIRW 32 words | with OAM flush |
| Free tile slots | 128-511 | 0-31 reserved, 32-127 sysfont |
| Max sprites HW | 64 | hard limit |
| Max visible colors / tile | 3 + transparent | 2bpp, index 0 = transparent |
| Character Over | `HW_STATUS & 0x80` | too many sprites on one scanline |

---

## See Also

- [Hardware-Registers.md](../01_Hardware/Hardware-Registers.md) — OAM and palette index register addresses
- [Game-Loop.md](../05_Systems/Game-Loop.md) — VBlank ISR, OAM flush timing
- [DMA.md](DMA.md) — MicroDMA for sprite updates
- [Tilemaps-and-Scrolling.md](Tilemaps-and-Scrolling.md) — Tile VRAM layout, tilemap pipeline
- [Colors-and-Palettes.md](Colors-and-Palettes.md) — Palette RAM, shadow palette, palette effects
- [Asset-Pipeline.md](../06_Pipeline-and-Patterns/Asset-Pipeline.md) — PNG export, sprite bundling, tile tools
- [Assembly.md](../02_CPU-and-Toolchain/Assembly.md) — TLCS-900H assembly, LDIRW patterns
