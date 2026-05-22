# Tilemaps and Scrolling

SCR1/SCR2 VRAM layout, tilemap entry format, scroll registers, upload patterns, utility functions, large-map streaming, asset pipeline, and known bugs for NGPC tilemap rendering.

---


## 1. VRAM and Tilemap Format

### 1.1 Memory Map

| Address | Name | Description |
|---------|------|-------------|
| `0x9000` | `HW_SCR1_MAP` | SCR1 tilemap — 32×32 u16 entries (2 KB) |
| `0x9800` | `HW_SCR2_MAP` | SCR2 tilemap — 32×32 u16 entries (2 KB) |
| `0xA000` | Character RAM | Tile data — 8×8 px tiles, 2bpp, 16 bytes/tile, 512 tiles max |
| `0x8280` | SCR1 palettes | 16 palettes × 4 colors × 2 bytes (u16 RGB444) |
| `0x8300` | SCR2 palettes | same layout |
| `0x8200` | Sprite palettes | same layout |
| `0x8032` | `SCR1_OFS_X` | SCR1 horizontal scroll offset |
| `0x8033` | `SCR1_OFS_Y` | SCR1 vertical scroll offset |
| `0x8034` | `SCR2_OFS_X` | SCR2 horizontal scroll offset |
| `0x8035` | `SCR2_OFS_Y` | SCR2 vertical scroll offset |

### 1.2 Tilemap Entry (u16) Format

```
bit 15     : H flip
bit 14     : V flip
bits 12-9  : palette number (0..15)
bit 8      : tile index bit 8 (for tiles 256..511)
bits 7-0   : tile index bits 7..0
```

```c
/* Construction macro (ngpc_hw.h) */
#define SCR_ENTRY(tile, pal, hflip, vflip) \
    ((u16)((tile) & 0xFF) | \
     (((u16)(hflip)  & 1) << 15) | \
     (((u16)(vflip)  & 1) << 14) | \
     (((u16)(pal)    & 0xF) << 9) | \
     (((u16)(((tile) >> 8) & 1)) << 8))

#define SCR_TILE(tile, pal)  SCR_ENTRY((tile), (pal), 0, 0)

/* Example: place tile 200, palette 3, at position (5, 2) */
HW_SCR1_MAP[2 * 32 + 5] = SCR_TILE(200, 3);
```

### 1.3 Tilemap Constraints

| Item | Value |
|------|-------|
| Tilemap size | 32 × 32 tiles |
| Visible screen | 20 × 19 tiles (160 × 152 px) |
| Tile slots reserved | 0..31 (hardware) |
| BIOS system font | 32..127 (after `BIOS_SYSFONTSET`) |
| Free tile slots | 128..511 |
| Character RAM total | 512 tiles × 16 bytes = 8 KB |
| SCR palettes | 16 palettes × 4 colors, `0x0BGR` format |
| Palette 0 color 0 | Transparent (scroll planes) |
| Tilemap entry access | 16-bit word writes only |

---

## 2. Scroll Registers

### 2.1 Scroll Offset Registers

```c
*(volatile u8*)0x8032 = x_offset;   /* SCR1 X */
*(volatile u8*)0x8033 = y_offset;   /* SCR1 Y */
*(volatile u8*)0x8034 = x_offset;   /* SCR2 X */
*(volatile u8*)0x8035 = y_offset;   /* SCR2 Y */
```

- Scroll wraps at 32 tiles (256 pixels) for both X and Y.
- Best updated in VBlank ISR, just after OAM flush.

### 2.2 Word-Write Trick (Packed X/Y)

The X and Y registers are adjacent in memory — a 16-bit word write updates both at once.
Reverse-engineered commercial games use this for efficient dual-axis scroll updates:

```c
/* Write SCR1 X and Y in a single 16-bit write */
*(volatile u16*)0x8032 = (u16)((y_off << 8) | x_off);

/* Same for SCR2 */
*(volatile u16*)0x8034 = (u16)((y_off << 8) | x_off);
```

This is also the format used by the MicroDMA raster scroll tables (see [DMA.md](DMA.md)).

---

## 3. Upload Patterns

### 3.1 Single Tile Write

```c
/* Using the template helper */
ngpc_gfx_put_tile(GFX_SCR1, x, y, tile_index, palette);

/* Direct write (use (u16) cast — see §8.1) */
HW_SCR1_MAP[(u16)y * 32u + x] = SCR_TILE(tile_index, palette);
```

> **Critical:** always cast `y` to `u16` before the multiplication to avoid 8-bit overflow
> (see §8.1 for the confirmed bug).

### 3.2 Rectangle Blit — LDIRW + Stride

For uploading a viewport-sized region into a 32-column tilemap, use the stride pattern
(observed in reverse-engineered commercial games):

```
Source: contiguous tile entries (20 columns × 19 rows)
Destination: HW_SCR1_MAP (0x9000), 32 columns wide

For each row:
  LDIRW BC=0x14 (copy 20 words = 20 columns)
  ADD VRAM_PTR, 0x18 (skip remaining 12 columns to reach next row start)
```

```asm
; IX = 0x9000 (SCR1 base)
; DE = source address
; D  = 19 (row count)
row_loop:
    ld BC, 0x14        ; 20 columns per row
    ldirw [(IX+), (DE+)]
    add IX, 0x18       ; skip 12 columns (12 * 2 = 0x18 bytes) to next row
    djnz row_loop
```

20 words written + 12 words skipped = 32 words = exact tilemap stride.
This pattern also works for viewports narrower than 32 columns.

### 3.3 Full Screen Upload

Use the `NGP_TILEMAP_BLIT_SCR1` / `NGP_TILEMAP_BLIT_SCR2` macros.
These write the full 20×19 visible area from a pre-generated C array:

```c
NGP_TILEMAP_BLIT_SCR1(prefix, TILE_BASE);
NGP_TILEMAP_BLIT_SCR2(prefix, TILE_BASE);
```

The macro: copies tiles to Character RAM (0xA000), writes the u16 tilemap to HW_SCR1_MAP,
and loads palettes via `ngpc_gfx_set_palette()`.

### 3.4 Palette Upload — Burst Copy

Palettes (16 × 4 = 64 entries, each 2 bytes = 128 bytes) can be uploaded via a single LDIRW:

```asm
; SCR1 palettes: LDIRW src=palette_data, dst=0x8280, BC=64
; SCR2 palettes: LDIRW src=palette_data, dst=0x8300, BC=64
; SPR palettes:  LDIRW src=palette_data, dst=0x8200, BC=64 (16-bit)
; SPR indices:   LDIR  src=index_data,   dst=0x8C00, BC=count (8-bit)
```

In C, the template's `ngpc_gfx_set_palette()` handles individual palette loads.

### 3.5 Wrap-Safe Address Arithmetic

For low-level code or ISR (byte-level map access with 32-column wrap):

```c
/* Coordinate to byte offset */
u16 addr = (u16)y * 0x40u + (u16)x * 2u;

/* Advance column (wrap at 32 cols = 64 bytes) */
addr = (addr & 0xFFC0u) | ((u16)(addr + 2u) & 0x003Fu);

/* Advance row (wrap at 2 KB) */
addr = (addr & 0xF800u) | ((u16)(addr + 0x40u) & 0x07FFu);
```

---

## 4. HUD Pattern

Reserve a horizontal strip in the tilemap for the HUD (score, lives, etc.):

```c
/* Reserve the bottom 2 rows as HUD — fill with a HUD background tile */
ngpc_gfx_fill_rect(GFX_SCR1, 0, 17, 20, 2, TILE_HUD_BG, PAL_HUD);
```

Rules:
- Keep gameplay logic (enemies, bullets) confined to the non-HUD rows (0..16).
- Write HUD digits to specific tile positions using `ngpc_gfx_put_tile()`.
- Always cast `y` to `u16` in the index calculation.

---

## 5. Template Utility Functions

### 5.1 ngpc_gfx_fill_rect

Fill a W×H tile rectangle with a single tile entry (tile index + palette).
Wrap-safe: coordinates wrap at 32 columns/rows.

```c
/* Fill entire 32×32 map with a sky tile */
ngpc_gfx_fill_rect(GFX_SCR1, 0, 0, 32, 32, TILE_SKY, 0);

/* HUD band: 20 tiles wide, 2 rows, from (0, 17) */
ngpc_gfx_fill_rect(GFX_SCR1, 0, 17, 20, 2, TILE_HUD_BG, 1);

/* Clear a block: tile=0, pal=0 */
ngpc_gfx_fill_rect(GFX_SCR1, 8, 5, 4, 3, 0, 0);
```

> Difference from `ngpc_gfx_fill()`: fill() covers the entire 32×32 plane.
> fill_rect() covers any sub-region at any position.

### 5.2 ngpc_gfx_set_rect_pal

Change the palette of tile entries in a W×H region without touching tile index, H.flip, or V.flip.
Mask pattern: `entry = (entry & 0xE1FFu) | ((pal & 0x0F) << 9)`

```c
/* Damage flash: switch enemy area to red palette */
ngpc_gfx_set_rect_pal(GFX_SCR1, enemy_tx, enemy_ty, 2, 2, PAL_RED);

/* Restore after flash */
ngpc_gfx_set_rect_pal(GFX_SCR1, enemy_tx, enemy_ty, 2, 2, PAL_NORMAL);
```

### 5.3 ngpc_gfx_set_color_direct

Write a color directly to the hardware palette register (no software shadow).
Effect is immediate on the current frame. Restore with `ngpc_gfx_set_palette()` next frame.

```c
/* Hit flash: white on sprite palette 0, color 1 */
ngpc_gfx_set_color_direct(GFX_SPR, 0, 1, RGB(15, 15, 15));

/* Next frame: restore original color */
ngpc_gfx_set_palette(GFX_SPR, 0, c0, c1_orig, c2, c3);
```

When to use:
- `set_color_direct`: instant effects (flash, fast blink)
- `set_palette`: normal loading (scene init, theme change)

### 5.4 ngpc_tileblitter (optional module)

`optional/ngpc_tileblitter/` — blit a W×H rectangle of tile words from ROM.
Wrap-safe, supports horizontal mirror.

```c
#include "ngpc_tileblitter/ngpc_tileblitter.h"

/* ROM tile words (NGP_FAR mandatory) */
extern const u16 NGP_FAR room_door[12];   /* 4*3 tile grid */

ngpc_tblit      (GFX_SCR1, 10, 5, 4, 3, room_door);  /* normal */
ngpc_tblit_hflip(GFX_SCR1, 16, 5, 4, 3, room_door);  /* H-mirrored */
```

### 5.5 Function Selection Guide

| Use case | Function |
|----------|----------|
| Single tile | `ngpc_gfx_put_tile()` |
| Uniform block | `ngpc_gfx_fill_rect()` |
| ROM scene from data | `ngpc_tblit()` |
| ROM scene, H-mirrored | `ngpc_tblit_hflip()` |
| Full screen (20×19) | `NGP_TILEMAP_BLIT_SCR1()` macro |
| Palette-only update | `ngpc_gfx_set_rect_pal()` |
| Instant color flash | `ngpc_gfx_set_color_direct()` |

---

## 6. Large Map Streaming

Architecture confirmed by binary reverse engineering of a commercial side-scrolling platformer.
Template module: `optional/ngpc_mapstream/`

### 6.1 ScrollCtx Structure

> **Note:** an early reading of the disassembly merged two separate structs. The platformer
> actually uses **two distinct structs**: a MapCtx (passed via XIX register) and a
> ScrollCtx (at fixed RAM addresses). See §6.11 for the complete split.

**ScrollCtx** — per-plane camera state, in RAM at `0x5056` (SCR1) and `0x506C` (SCR2):

```c
typedef struct {
    int16_t  cam_x, cam_y;    /* +0x00/+0x02 : current camera (pixels) */
    uint8_t  _pad[2];         /* +0x04/+0x05 : reserved/zero at init */
    int8_t   vel_x;           /* +0x06 : X velocity / delta */
    uint8_t  dir_x;           /* +0x07 : bit7 = X scroll direction flag */
    int8_t   vel_y;           /* +0x08 : Y velocity / delta */
    uint8_t  dir_y;           /* +0x09 : bit7 = Y scroll direction flag */
    int16_t  last_x, last_y;  /* +0x0A/+0x0C : last loaded column/row (px) */
    int16_t  min_x, max_x;   /* +0x0E/+0x10 : camera clamp bounds */
    int16_t  min_y, max_y;   /* +0x12/+0x14 : camera clamp bounds */
} ScrollCtx;                  /* total = 0x16 bytes */
```

**MapCtx** — map metadata + VRAM target, passed as XIX to all streaming functions:

```c
typedef struct {
    const uint16_t* rom_data;  /* +0x00 : FAR pointer to ROM map array */
    uint8_t  map_w, map_h;    /* +0x04/+0x05 : map dimensions in tiles */
    uint8_t  origin_tile_x;   /* +0x06 : tile X origin offset (subtracted from cam tile) */
    uint8_t  origin_tile_y;   /* +0x07 : tile Y origin offset */
    uint16_t plane;            /* +0x08 : 0x9000 (SCR1) or 0x9800 (SCR2) */
} MapCtx;
```

Both planes use the same streaming code — only the context structs differ.

### 6.2 Trigger Algorithm

Called each frame in the **main loop** (not VBlank). X streaming:

```c
/* step direction determined by dir_x bit7 */
int16_t step_x = (scroll_ctx->dir_x & 0x80) ? -8 : +8;
while ((cam_x & ~7) != (last_x & ~7)) {
    last_x += step_x;
    /* right edge: pixel_x = last_x + 0xA0 (160px = screen width) */
    /* left edge:  pixel_x = last_x (no offset) */
    load_column(map_ctx, scroll_ctx, last_x + (step_x > 0 ? 0xA0 : 0));
}
```

Y streaming is symmetric, using cam_y vs last_y:
```c
int16_t step_y = (scroll_ctx->dir_y & 0x80) ? -8 : +8;
while ((cam_y & ~7) != (last_y & ~7)) {
    last_y += step_y;
    /* bottom edge: pixel_y = last_y + 0x98 (152px = screen height) */
    /* top edge:    pixel_y = last_y (no offset) */
    load_row(map_ctx, scroll_ctx, last_y + (step_y > 0 ? 0x98 : 0));
}
```

- Granularity: **8 px per step** → 1 column or 1 row loaded per 8-pixel camera move.
- Each column/row loads **21 tiles** (19 visible + 1 margin on each side).

### 6.3 VRAM Address Formulas

**Column VRAM start address** (used by the column loaders):

```c
/* tile_col_offset = (pixel_x & 0xF8) >> 2  (= tile_col * 2, byte offset in row) */
/* row_offset      = (0x480 - (pixel_y * 8)) & 0x7FF  (inverted Y, wraps at 2KB) */
uint16_t col_vram_addr(uint16_t plane_base, uint16_t pixel_x, uint16_t pixel_y) {
    uint8_t  col_off = (uint8_t)((pixel_x & 0xF8u) >> 2);     /* tile_col * 2 */
    uint16_t row_off = (uint16_t)(0x480u - (pixel_y * 8u)) & 0x07FFu;
    return plane_base | (row_off & 0xFFC0u) | (col_off & 0x3Fu);
}
```

**Row VRAM start address** (used by the row loaders):
Same formula — the loaders use identical VRAM address computation, only the
inner loop direction differs (+2 horizontal for rows, +0x40 vertical for columns).

**ROM tile lookup** (both column and row inner loops):
```c
/* Confirmed from disassembly of both row and column inner loops */
/* tile_col and tile_row are relative to MapCtx origin (origin_tile subtracted) */
uint16_t tileword = rom_base[tile_col - tile_row * map_ctx->map_w];
/* Note: rom_base POINTS TO ROW 0 (top row) — see §6.12 for Y-inverted storage */
```

### 6.4 Camera Clamp Macros

Clamp the camera BEFORE triggering streaming to avoid loading out-of-bounds tiles
(which produce empty/black tile edges):

```c
/* Max camera position before edge of screen exceeds the map */
NGPC_MS_CAM_MAX_X(ms)        /* (map_w - 20) * 8 px */
NGPC_MS_CAM_MAX_Y(ms)        /* (map_h - 19) * 8 px */

/* Clamp (returns s16) */
NGPC_MS_CLAMP_X(ms, px)
NGPC_MS_CLAMP_Y(ms, py)
```

Standard usage:
```c
cam_px = NGPC_MS_CLAMP_X(&g_ms, cam_px);
cam_py = NGPC_MS_CLAMP_Y(&g_ms, cam_py);
ngpc_mapstream_update(&g_ms, g_bg_map, cam_px, cam_py);
```

The PNG Manager generates literal defines in the scene header when SCR1 uses ngpc_mapstream:
```c
#define SCENE_LEVEL1_CAM_MAX_X  864   /* px, map 128 tiles wide */
#define SCENE_LEVEL1_CAM_MAX_Y  104   /* px, map 32 tiles tall */
```

### 6.5 Streaming Frame Pipeline (Critical Order)

```
VBlank ISR:
  push OAM shadow  -> 0x8800/0x8C00 (LDIRW)
  push scroll regs -> 0x8032..0x8035

Main loop (each frame):
  1) update camera_x/y + clamp between min/max
  2) stream columns if camera_x changed vs last_x
  3) stream rows    if camera_y changed vs last_y
  4) update scroll register shadow (0x8032/0x8033/0x8034/0x8035)
  5) game logic (sprites, enemies, etc.)
```

Streaming is **synchronous in the main loop**. VBlank only does the hardware push.

### 6.6 Two-Pass Pipeline: Tile Cache then Tilemap Blit

The platformer always does two passes:
1. Pass 1: stream tokens/macros → populate `tile_map[]` + Character RAM (tile data)
2. Pass 2: blit tile words into the scroll plane VRAM

This avoids visual glitches where the hardware renders before the tile data arrives.
Always load tile graphics before writing tilemap entries.

### 6.7 Blank Tile Init Pattern

```c
/* Fill entire scroll plane (32x32 = 2048 bytes) with a "blank" tile entry */
/* Value 0x01FF = tile index 0x1FF = safe blank tile */
/* Trick: write first entry, then LDIRW src=dst-1 to propagate it */
*dst++ = 0x01FF;
/* LDIRW BC=0x3FF, (XDE+)<-(XDE-2) => propagates to entire plane */
```

Call this for both SCR1 and SCR2 during initialization.

### 6.8 Shadow Buffer + Dirty Flag

A commercial run-and-gun action game uses a different approach from the platformer above:
it does NOT stream column by column. Instead it maintains compact shadow buffers in RAM
and flushes to VRAM in VBlank.

Shadow buffers (confirmed from disassembly):
```c
/* RAM layout (contiguous) */
0x5D04  /* Tile manifest table: 128 bytes = 64 x u16 tile ROM indices */
0x5D84  /* SCR1 shadow: 484 words = 22 cols x 22 rows (pitch = 22 words = 44 bytes) */
0x614C  /* SCR2 shadow: same size (= 0x5D84 + 968 bytes) */
```

The shadow SCR format is **identical to VRAM**: each word = one tilemap entry (tile index +
palette + flip bits). The VBlank flush is a plain memcpy with pitch correction.

Dirty flags at a byte in RAM `0x59E4`:
```c
dirty_flags |= 0x40;    /* mark SCR1 dirty (bit 6) */
dirty_flags |= 0x80;    /* mark SCR2 dirty (bit 7) */

/* In VBlank ISR: */
if (dirty_flags & 0x40) { flush_scr1_shadow(); dirty_flags &= ~0x40; }
if (dirty_flags & 0x80) { flush_scr2_shadow(); dirty_flags &= ~0x80; }
```

The dirty flag is set per-entity (motion detection compares current vs previous screen
position — see §6.19). When nothing moves, the flush is skipped entirely.

### 6.9 LDIRW with Inter-Line Stride

Key technique: flush 18 rows of 22 words into a 32-word-wide VRAM.
After each LDIRW, `add DE, IY` skips the remaining VRAM bytes to reach the next row start.

Confirmed constants from disassembly (address 0x0CA8-0x0CC7):

```asm
; IX  = 0x16 = 22        (word count per LDIRW -- BC is set from IX each iteration)
; XIY = 0x14 = 20        (BYTES to add to DE after LDIRW -- NOT words)
; XHL = 0x5D84           (shadow SCR1 source)
; XDE = 0x9000           (VRAM SCR1 destination)
; WA  = 0x12 = 18        (row count)

ld  IX, 0x16           ; IX = 22 (word count)
ld  XIY, 0x14          ; IY = 20 (byte skip after LDIRW)
bit 0x6, (dirty_flags)      ; check SCR1 dirty bit
jr  Z, skip            ; skip if clean
lda XHL, shadow_scr1        ; source = shadow SCR1
lda XDE, 0x9000        ; dest   = VRAM SCR1
ld  WA, 0x12           ; 18 rows
loop:
ld  BC, IX             ; BC = 22
ldirw [(XDE+),(XHL+)]  ; copy 22 words (44 bytes), XDE += 44
add DE, IY             ; DE += 20 bytes  -> total advance = 64 bytes = 32 words
djnz WA, loop
and (dirty_flags), 0xbf     ; clear SCR1 dirty bit
```

Pitch arithmetic:
- LDIRW of 22 words advances XDE by `22 * 2 = 44 bytes`.
- `add DE, IY` with IY=20 adds **20 bytes** to DE.
- Total per row: `44 + 20 = 64 bytes = 32 words` = exact VRAM row stride. CORRECT.

The shadow SCR row pitch (44 bytes) matches the VRAM row advance after LDIRW, so XHL
automatically aligns to the next shadow row without any correction. Only XDE needs the
`add DE, IY` to skip the hidden VRAM columns (22..31).

22 words written + 10 words skipped = 32 words = exact tilemap row stride.
This pattern works for any viewport narrower than 32 columns.

### 6.10 Streaming vs Shadow Buffer: When to Use Which

| Approach | When to use |
|----------|-------------|
| Column streaming | Large map, few VRAM writes per frame, slow camera |
| Full shadow buffer | Map fits in RAM (~1.5 KB), atomic VBlank flush needed |

### 6.11 Dual Struct: MapCtx vs ScrollCtx

> column/row loaders.

An early reading incorrectly merged the two structs. Corrected layout:

| Field | MapCtx (XIX reg) | ScrollCtx (XIY reg, 0x5056/0x506C) |
|-------|-----------------|--------------------------------------|
| cam_x/y | — | +0x00/+0x02 (s16) |
| map_w/h | +0x04/+0x05 (u8) | — |
| origin_tile_x/y | +0x06/+0x07 (u8) | — |
| plane (0x9000/0x9800) | +0x08 (u16) | — |
| dir_x/dir_y (bit7=direction) | — | +0x07/+0x09 (u8) |
| last_x/last_y | — | +0x0A/+0x0C (s16) |
| clamp min/max x/y | — | +0x0E..+0x14 (s16) |

The streaming dispatcher receives XIX=MapCtx, sets XIY=ScrollCtx by
comparing `(XIX+0x8)` to 0x9000 to select the right ScrollCtx (SCR1 or SCR2).

The origin_tile_x/y fields allow maps that don't start at tile 0: the streaming
loaders subtract them from the computed tile index before the ROM lookup.
At origin_tile = 0 (most cases), the subtraction is a no-op.

### 6.12 ROM Map Layout — Y-Inverted Row Storage


**Critical for the export tool.** The platformer's ROM map access formula is:

```
rom_addr = base_ptr + col*2 - row*map_w*2   (byte offsets)
```

This means row 0 (top of visual level) is at `base_ptr`, row 1 at `base_ptr - map_w*2`,
etc. **Rows decrease in address as row index increases** — rows are stored in
reverse order relative to a standard C array.

**What the tool must generate:**

```python
# Export: store rows from LAST to FIRST in the array
# data[0] = bottom row of level (highest row index)
# data[(H-1)*W + col] = top row of level (row 0, what you see at cam_y=0)
map_bytes = bytearray()
for row in range(H - 1, -1, -1):      # reversed: bottom first, top last
    for col in range(W):
        map_bytes += struct.pack('<H', tilewords[row][col])

# In C, base_ptr = &map_data[(H-1)*W]  (pointer to top row = last element range)
```

**C array access:**
```c
/* map_data stored with rows reversed (row 0 = last in array) */
const uint16_t map_data[MAP_H * MAP_W] = { /* row H-1, row H-2, ..., row 0 */ };

/* Base pointer points to the top row (= &map_data[(H-1)*MAP_W]) */
const uint16_t* base = &map_data[(MAP_H - 1u) * MAP_W];

/* Lookup: tile at (tile_col, tile_row) */
uint16_t tw = base[tile_col - tile_row * MAP_W];
/* = map_data[(H-1)*W + tile_col - tile_row*W] = map_data[(H-1-tile_row)*W + tile_col] */
```

**Equivalence check:** at tile_row=0 → `base[tile_col]` = `map_data[(H-1)*W + tile_col]` =
last stored row = visual top row. At tile_row=H-1 → `base[tile_col - (H-1)*W]` =
`map_data[tile_col]` = first stored row = visual bottom row.

> This also means `map_w` and `map_h` in MapCtx refer to the **visual** dimensions
> (standard tile coordinates), not the storage layout.

### 6.13 Scroll Register Y Inversion


Confirmed exact formula for updating the scroll registers per frame:

```asm
; For SCR1: XDE=0x8032. For SCR2: XDE=0x8034.
; XIX = MapCtx (cam_x at +0x00, cam_y at +0x02)
ld   A, (XIX+0x00)         ; A = cam_x & 0xFF
ld   (XDE+), A             ; write to 0x8032 (SCR1_X) or 0x8034 (SCR2_X)
                           ; XDE auto-increments to 0x8033 / 0x8035
ld   A, (XIX+0x02)         ; A = cam_y & 0xFF
neg  A                     ; A = -cam_y
add  A, (0x5086)           ; A += level_y_correction (global, updated per level)
ld   (XDE), A             ; write to 0x8033 (SCR1_Y) or 0x8035 (SCR2_Y)
```

**Y is physically inverted on NGPC** in this game's coordinate convention:
- SCR_X = `cam_x & 0xFF` (direct)
- SCR_Y = `(-cam_y + level_y_correction) & 0xFF`

`level_y_correction` at `0x5086` is a per-level constant that maps cam_y=0 to
the correct scroll position for the level's entry point. It is updated when the
camera crosses a 256-pixel Y boundary (full scroll wrap).

**In C (simplified for a map starting at cam_y=0):**
```c
*(volatile uint8_t*)0x8032 = (uint8_t)cam_x;
*(volatile uint8_t*)0x8033 = (uint8_t)(-cam_y + level_y_correction);
*(volatile uint8_t*)0x8034 = (uint8_t)cam_x;   /* SCR2 if used for parallax */
*(volatile uint8_t*)0x8035 = (uint8_t)(-cam_y + level_y_correction);
```

### 6.14 Init Sequence — Blit 20x19 Only (Not 32x32)


The platformer's level init sequence:

```asm
; 1) Clear both scroll planes with blank tile 0x01FF
call clear_plane(A=0)       ; clear SCR1 (0x9000) — full 32x32
call clear_plane(A=1)       ; clear SCR2 (0x9800) — full 32x32

; 2) Load tile graphics into Character RAM
XIX  = <ROM tile descriptor ptr>
XHL  = 0x24d648              ; tile data in ROM
call upload_tiles           ; upload tiles to 0xA000

; 3) Blit ONLY the visible 20x19 area (not the full 32x32 map)
BC   = 0x9800                ; target plane
DE   = 0                     ; start at top-left of VRAM
L    = 0x14 (20 tiles)       ; viewport width
H    = 0x13 (19 tiles)       ; viewport height
XIX  = <ROM map data ptr>
call rect_blit               ; 2D rect blitter (wrap-safe, H-flip capable)
```

**Key insight:** at init, only the visible screen (20x19) is blitted into VRAM.
The remaining 12 "hidden" VRAM columns and 13 rows fill in automatically via streaming
as the camera moves. This saves ~4 ms of init time for large maps.

**Init then stream pattern (recommended):**
```c
ngpc_mapstream_clear_plane(plane);      /* fill 32x32 with blank tile */
ngpc_mapstream_upload_tiles(map_ctx);   /* CharRAM upload */
ngpc_mapstream_blit_screen(map_ctx, scroll_ctx); /* 20x19 initial blit */
/* Sync last_x/last_y = cam_x/cam_y so first frame triggers no extra streaming */
scroll_ctx->last_x = scroll_ctx->cam_x;
scroll_ctx->last_y = scroll_ctx->cam_y;
```

### 6.15 Four Streaming Loaders


The platformer has four separate loader functions (two for columns, two for rows):

| Direction | Edge loaded | VRAM stride | Inner loop var |
|-----------|-------------|-------------|----------------|
| Scroll right (+8) | Right: `last_x + 0xA0` | +0x40 (vertical) | BC++ |
| Scroll left (-8) | Left: `last_x` | +0x40 (vertical) | BC-- |
| Scroll down (+8) | Bottom: `last_y + 0x98` | +2 (horizontal) | WA++ |
| Scroll up (-8) | Top: `last_y` | +2 (horizontal) | WA++ |

All four loaders share the **same inner loop body** — only the VRAM start address
calculation differs.

**Inner loop count: always E=0x15 = 21 tiles** for both columns and rows.

**Note:** the row loaders use a horizontal +2 stride; the column loaders use a vertical
+0x40 stride. An early reading had these swapped.

### 6.16 Tile Manifest + Character RAM Upload


This action game separates tile *graphics* upload from tile *index* management.

**RAM layout around 0x5D00:**

| Address | Size | Content |
|---------|------|---------|
| `0x5D04` | 128 bytes | Tile manifest: 64 x u16 tile ROM indices |
| `0x5D84` | 968 bytes | Shadow SCR1 tilemap (22 cols x 22 rows) |
| `0x614C` | 968 bytes | Shadow SCR2 tilemap |

The manifest at `0x5D04` holds up to 64 logical tile indices. The upload routine reads
each index, computes the ROM address, and loads the 16-byte tile into Character RAM:

```asm
; Entry: XIX = 0x5D04 (manifest table), WA = tile count, XDE = 0xA000 (CharRAM)
; ROM tile data base: 0x3666A2 (far cartridge address)
; Each tile: 8 words = 16 bytes (8x8 px, 4bpp)

copy_loop:
    ld   QBC, 0x0                ; clear high bytes of XBC
    ld   BC, (XIX+)              ; read tile index from manifest (auto-increment XIX)
    sll  0x4, XBC                ; XBC = tile_index * 16 (byte offset)
    lda  XHL, 0x3666a2           ; ROM base of tile graphics
    add  XHL, XBC                ; XHL = ROM_base + tile_index * 16
    ld   BC, 0x8                 ; 8 words = 1 tile
    ldirw [(XDE+),(XHL+)]        ; copy 1 tile from ROM to CharRAM
    djnz WA, copy_loop          ; repeat WA times
```

Two entry points:
- Full entry — sets `XIX = 0x5D04`, then falls into the loop.
- Partial entry — skips the `XIX` init (for bulk consecutive uploads).

**Bulk level tile upload:** an alternative path copies 0xC00 words (6 KB = 384
tiles) in a single LDIRW from ROM into CharRAM at `0xA800` (tile slot 128+):

```asm
    ld   BC, 0xc00          ; 3072 words = 6144 bytes = 384 tiles
    lda  XDE, 0xa800        ; dest = CharRAM + 0x800 (tile 128+)
    ldirw [(XDE+),(XHL+)]   ; bulk copy from XHL (set by caller, ROM)
```

This is the level init path: all background tile graphics loaded at once, then the
manifest/per-entity path handles dynamic or foreground tiles.

**Design principle (reusable):**
- Stage 1 (level init): bulk LDIRW from ROM to CharRAM (fast, one shot).
- Stage 2 (per-frame): update shadow SCR with tile indices, set dirty flag.
- Stage 3 (VBlank): LDIRW shadow -> VRAM with pitch correction.
No scatter-writes, no per-frame CharRAM updates.

---

### 6.17 Shadow SCR Geometry


Precise dimensions confirmed by cross-referencing three code sites:

| Parameter | Value | Source instruction |
|-----------|-------|--------------------|
| Columns per row (flush width) | **22 words** | `ld IX, 0x16` (IX = 22) |
| Row stride in shadow buffer | **44 bytes = 22 words** | `add XIZ, 0x2c` (0x2C = 44) |
| Total buffer size | **484 words = 968 bytes** | clear loop: BC = 0x1E4 = 484 |
| Rows in buffer | **22 rows** (484 / 22 = 22) | from buffer size |
| Rows flushed to VRAM per VBL | **18 rows** | `ld WA, 0x12` (WA = 18) |
| VRAM bytes skipped after LDIRW | **20 bytes = 10 words** | `ld XIY, 0x14` (IY = 20) |
| SCR1 shadow base | **0x5D84** | `lda XHL, shadow_scr1` |
| SCR2 shadow base | **0x614C** | `add XIZ, 0x3c8` (= 0x5D84 + 968) |

Note on 22 vs 18: the shadow holds 22 rows but only 18 are flushed each VBL. The extra 4
rows act as a write margin for entity tile projections that extend slightly off-screen.

**Shadow row stride = 44 bytes = 22 words = one VRAM LDIRW iteration.**
This is not a coincidence: it means XHL auto-aligns to the next shadow row after each
22-word LDIRW, with no correction needed on the source pointer.

---

### 6.18 Shadow SCR Blank Tile Values and Clear Patterns

Two different blank tile values are used depending on context:

| Context | Value | Meaning |
|---------|-------|---------|
| Level init (0x0C90) | `0x0000` | Tile 0 = hardware-transparent on fresh init |
| During gameplay (0x0FA4, 0x10A2) | `0x0080` | Tile 128 = dedicated blank tile in CharRAM |

During gameplay, tile 0 may contain graphics (BIOS font tile 0), so the safe blank is
tile 128 (first free slot after the BIOS sysfont). The game uses 0x0080 as the blank
sentinel throughout its game logic.

**Init clear — zeroes 484 words:**
```asm
    ld   XDE, shadow_scr1      ; dest = shadow SCR1
    ld   XWA, 0x0         ; blank value = 0
clear_loop:
    ld   (XDE+), XWA      ; write 4 bytes (2 words) and advance XDE
    djnz BC, clear_loop   ; BC = 0x1E4 = 484 iterations -> 968 bytes
```
Uses `ld (XDE+), XWA` (32-bit word write) for speed: 2 words per iteration.

**Gameplay clear — fills 484 words with 0x0080:**
```asm
fill_blank:
    lda  XIZ, shadow_scr1
    ld   XWA, 0x800080    ; WA = 0x0080, A = 0x0080 (two blank tiles at once)
    ld   BC, 0xf2         ; 242 iterations x 4 bytes = 968 bytes
fill_loop:
    ld   (XIZ+), XWA      ; write 4 bytes = 2 blank tiles
    djnz BC, fill_loop
```
`0x800080` as a 32-bit value = `{W=0x0080, A=0x0080}` = two consecutive u16 of 0x0080.

**Pattern for C code (32-bit clear, faster than word loop):**
```c
/* Clear shadow SCR with blank tile 0x0080 */
u32 *p = (u32 *)shadow_buf;
u16 i;
for (i = 0u; i < 242u; i++) *p++ = 0x00800080ul;
/* 242 * 4 bytes = 968 bytes = 22 * 22 * 2 */
```
If cc900 generates `ld (XDE+), XWA` from this pattern, throughput doubles vs u16 loop.
Verify in the compiled output; otherwise use inline ASM.

---

### 6.19 Entity to Shadow SCR Projection (Flip Encoding)


This action game projects entity sprites into the shadow SCR buffer (not the OAM).
This is the "BW mode" path where entity tiles are written directly as tilemap entries.

**Motion detection + dirty flag setter:**
```asm
; XIX = entity, A = 0 (SCR1) or != 0 (SCR2)
    ld   BC, (XIX+0x58)     ; X screen current
    ld   DE, (XIX+0x5a)     ; Y screen current
    cp   BC, (XIX+0x5c)     ; compare with X prev
    jr   NZ, update         ; different -> update
    cp   DE, (XIX+0x5e)
    jr   NZ, update
    ret                      ; no change -> return (no dirty set)

update:
    ld   (XIX+0x5c), BC     ; save new X as prev
    ld   (XIX+0x5e), DE     ; save new Y as prev
    cp   A, 0x0
    jr   NZ, mark_scr2
    or   (dirty_flags), 0x40     ; set SCR1 dirty (bit 6)
    jr   T, continue
mark_scr2:
    or   (dirty_flags), 0x80     ; set SCR2 dirty (bit 7)
```

**Two identical instances** exist (one for SCR1, one for SCR2). A third variant uses
`srl 0x3` instead of `srl 0x2` for a different sprite tile resolution.

**Tile projection into shadow SCR (BW path):**
```asm
; XIY = entity metasprite ROM pointer (from XIX+0x54)
; XIZ = shadow SCR base (0x5D84 or 0x614C)

; X position -> metasprite column offset:
    ld   WA, (XIX+0x58)    ; X screen (pixels)
    and  WA, 0xfff0        ; align to 16px grid (2-tile width)
    srl  0x2, WA           ; WA / 4 = metasprite word stride offset
    add  XIY, XWA          ; advance ROM metasprite pointer

; Y position -> row offset in metasprite:
    ld   L, (XIX+0x60)     ; sprite width W (in tiles)
    ld   WA, (XIX+0x5a)    ; Y screen (pixels)
    srl  0x4, WA           ; WA / 16 = tile row Y
    ld   BC, HL            ; BC = W
    sll  0x2, BC           ; BC = W * 4 (bytes per ROM row)
    mul  XBC, WA           ; XBC = row_Y * (W * 4)
    add  XIY, XBC          ; XIY = start of entity's tile row in ROM
```

**Flip bit encoding per 2x2 tile block:**
```asm
; Inner loop (B = row count, C = column count):
    ld   WA, (XIY)         ; tile index from ROM metasprite
    add  WA, 0x80          ; add tile base (128 = first free CharRAM slot)
    ld   E, (XIY+0x2)      ; flags byte
    inc  0x4, XIY          ; advance 4 bytes (one metasprite entry)
    and  E, 0x3            ; keep bits 0-1 = flip flags

; Dispatch on E:
; E = 0 : normal       -> no flags set
; E = 1 : H-flip only  -> WA |= 0x4000  (tileword bit 14 = H-flip)
; E = 2 : V-flip only  -> WA |= 0x8000  (tileword bit 15 = V-flip)
; E = 3 : both flips   -> WA |= 0xC000

; Write 2x2 tile block into shadow SCR (XIZ = current position):
(XIZ + 0x00) = WA        ; top-left tile
(XIZ + 0x02) = WA + 1    ; top-right tile (next tile in row)
(XIZ + 0x2c) = WA + stride  ; bottom-left (0x2C = 44 bytes = 22 words = 1 shadow row)
(XIZ + 0x2e) = WA + stride + 1  ; bottom-right

    inc  0x4, XIZ          ; advance 4 bytes = 2 words = 2 tile columns
; after B inner iterations: add XIZ, 0x2c  (next shadow row)
```

**Key: the tileword format in the shadow SCR is identical to VRAM.**
Bits 14-15 in the shadow tileword are directly the NGPC H-flip/V-flip hardware bits.
The VBlank LDIRW flush copies them verbatim to VRAM -- no transformation needed.

---

### 6.20 ngpc_mapstream: Improvement Implications

Lessons from the shadow-buffer action game for improving `optional/ngpc_mapstream/`:

**1. Address computation bottleneck.**
The naive `ms_put()` computes `idx = (u16)vr * 0x20u + vc` for every tile write.
The multiply repeats 21 times per streamed column. Alternative: compute start address
once, then advance by `+0x40` bytes per row (VRAM row stride). Handle the 32-row
wrap-around explicitly at `vr = 31 -> 0`.

**2. Shadow buffer + LDIRW flush (for full-viewport updates).**
The action game avoids scatter-writes entirely by maintaining a compact linear shadow SCR
(22 x 18 = 396 words = 792 bytes per plane) and flushing with one LDIRW + pitch loop.
This is faster than 396 individual indexed writes when the camera moves every frame.
Trade-off: 792 bytes RAM per plane vs 0 bytes RAM for streaming.

**3. Dirty flag — skip flush when camera is static.**
Add a `u8 dirty` field to `NgpcMapStream`. Set in `ngpc_mapstream_update` when
`dx != 0 || dy != 0`. Check and clear in the VBlank flush function. Saves 18 LDIRW
iterations every frame during menus, cutscenes, or stopped camera.

**4. Flip bits in map_tiles[].**
The ROM `map_tiles[]` array can encode flip bits in bits 14-15 of each u16 tileword.
The column/row streaming and the LDIRW flush both pass these bits through unchanged.
No extra processing needed; the tool generator just includes them in the word.

**5. Blank tile value.**
Use `0x0000` for init clears (tile 0 = transparent). Use `0x0080` (tile 128) during
gameplay for "empty" cells if tile 0 is occupied by game graphics.

**Stride constants (confirmed, for any LDIRW-based flush):**
```c
#define NGPC_SHADOW_COLS     22u   /* columns stored and flushed */
#define NGPC_SHADOW_ROWS     18u   /* rows flushed per VBL */
#define NGPC_SHADOW_PITCH    44u   /* bytes per shadow row (= 22 * 2) */
#define NGPC_VRAM_SKIP       20u   /* bytes to add after LDIRW (= (32-22)*2) */
```

---

### 6.21 Parallax and Camera Smooth Follow


**Camera shadow (RAM 0x621D–0x6222):**

```c
/* Camera state in RAM */
s16 cam_x;          /* 0x621D — world X of camera (SCR2 — main layer) */
s16 cam_y;          /* 0x621F — world Y of camera (SCR2) */
s16 cam_x_scr1;     /* 0x6221 — parallax X written to SCR1 */
```

**Smooth follow ("speed/2 lerp") per frame:**

```asm
; cam_x += (target_x - cam_x) / 2
ld  WA, (target_x)
sub WA, (0x621D)        ; WA = target - cam_x
sra 0x1, WA             ; WA >>= 1 (divide by 2, arithmetic)
add (0x621D), WA        ; cam_x += delta/2
```

This halves the distance to the target each frame — the camera converges exponentially,
producing a smooth "lag-behind" feel without requiring a floating-point lerp coefficient.

**C equivalent:**

```c
cam_x += (s16)((target_x - cam_x) >> 1);
cam_y += (s16)((target_y - cam_y) >> 1);
```

At 60fps, half-distance per frame gives ~95% catch-up in 4-5 frames.

**Clamp to world bounds:**

```asm
; Clamp cam_x to [0xA0 .. 0xD0] (160..208 — world boundary)
cp  (0x621D), 0xA0
jr  LT, clamp_lo
cp  (0x621D), 0xD0
jr  GT, clamp_hi
jr  cam_clamp_done
clamp_lo: ld (0x621D), 0xA0 ; jr cam_clamp_done
clamp_hi: ld (0x621D), 0xD0
cam_clamp_done:
```

**Parallax SCR1 — 2:1 ratio:**

```asm
; SCR1 scroll X = cam_x / 2
ld  A, (cam_x)
sra 0x1, A              ; A = cam_x >> 1 (arithmetic right shift)
ld  (cam_x_scr1), A
; Write to scroll registers
ld  (HW_SCR1_OFS_X), A  ; SCR1 moves at half speed → depth illusion
ld  A, (cam_x)
ld  (HW_SCR2_OFS_X), A  ; SCR2 moves at full speed → foreground
```

The `sra 0x1` is a 3-byte signed divide-by-2: it handles negative coordinates correctly
(floor toward −∞), unlike `srl` which would sign-extend wrong on negative cam values.

**Complete per-frame camera update sequence:**

```c
/* 1. Smooth follow */
cam_x += (s16)((target_x - cam_x) >> 1);
cam_y += (s16)((target_y - cam_y) >> 1);

/* 2. Clamp to world edges */
if (cam_x < CAM_MIN_X) cam_x = CAM_MIN_X;
if (cam_x > CAM_MAX_X) cam_x = CAM_MAX_X;

/* 3. Write scroll registers */
HW_SCR2_OFS_X = (u8)cam_x;          /* main layer — full speed */
HW_SCR1_OFS_X = (u8)(cam_x >> 1);   /* parallax layer — half speed */
HW_SCR2_OFS_Y = (u8)cam_y;
HW_SCR1_OFS_Y = (u8)(cam_y >> 1);
```

> `sra 0x1` (arithmetic shift) is required for parallax: `srl 0x1` (logical) would
> corrupt negative scroll values and introduce a 1-pixel jitter at X=0.

> See [DMA.md](DMA.md) for the DMA-based per-scanline scroll variant (raster effects).

---

## 7. Asset Pipeline

### 7.1 ngpc_tilemap.py Commands

**Full-screen scene (intro/menu) — u8 tiles:**
```bash
python tools/ngpc_tilemap.py assets/title.png \
  -o GraphX/title_intro.c -n title_intro --header \
  --emit-u8-tiles --black-is-transparent --no-dedupe
```

**Dual-layer (SCR1 + SCR2) explicit:**
```bash
python tools/ngpc_tilemap.py scr1.png --scr2 scr2.png \
  -o GraphX/level1.c -n level1 --header --emit-u8-tiles
```

**With optional tile binary (for streaming or compression):**
```bash
python tools/ngpc_tilemap.py assets/level1_bg.png \
  -o GraphX/level1_bg.c -n level1_bg --header \
  --tiles-bin GraphX/level1_bg_tiles.bin
```

Key notes:
- `--emit-u8-tiles`: tiles as `u8` (half the size in RAM, `NGP_FAR` still required)
- `tiles_count` = number of u16 words (= num_tiles × 8), **not** the number of tiles
- `map_tiles[]` = indices 0..N in the unique tile set (add `TILE_BASE` when rendering)
- `--no-dedupe`: disables deduplication (useful for full-screen scenes)

### 7.2 Method A — Helpers (Recommended)

Use `ngpc_gfx_load_tiles_at()`, `ngpc_gfx_set_palette()`, `ngpc_gfx_put_tile()`.
All helper functions use `NGP_FAR` in their signatures for ROM pointer safety.

```c
#include "ngpc_gfx.h"
#include "../GraphX/intro_scene_png.h"

#define INTRO_TILE_BASE 128u  /* avoid BIOS sysfont (tiles 32-127) */

static void intro_init(void)
{
    u16 i;

    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    /* Tiles (NGP_FAR handled internally by the helper) */
    ngpc_gfx_load_tiles_at(intro_scene_png_tiles,
                           intro_scene_png_tiles_count,
                           INTRO_TILE_BASE);

    /* Palettes */
    for (i = 0; i < (u16)intro_scene_png_palette_count; ++i) {
        u16 off = (u16)i * 4u;
        ngpc_gfx_set_palette(GFX_SCR1, (u8)i,
            intro_scene_png_palettes[off + 0],
            intro_scene_png_palettes[off + 1],
            intro_scene_png_palettes[off + 2],
            intro_scene_png_palettes[off + 3]);
    }

    /* Tilemap */
    for (i = 0; i < intro_scene_png_map_len; ++i) {
        u8 x   = (u8)(i % intro_scene_png_map_w);
        u8 y   = (u8)(i / intro_scene_png_map_w);
        u16 tile = (u16)(INTRO_TILE_BASE + intro_scene_png_map_tiles[i]);
        u8 pal = (u8)(intro_scene_png_map_pals[i] & 0x0Fu);
        ngpc_gfx_put_tile(GFX_SCR1, x, y, tile, pal);
    }
}
```

### 7.3 Method B — Direct VRAM Macro (Debug / Fallback)

Use the tilemap blit macro header. Writes directly to VRAM without passing pointers —
completely avoids near/far pointer issues.

```c
#include "ngpc_tilemap_blit.h"
#include "../GraphX/intro_scene_png.h"

#define INTRO_TILE_BASE 128u

static void intro_init(void)
{
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    NGP_TILEMAP_BLIT_SCR1(intro_scene_png, INTRO_TILE_BASE);
}
```

What the macro does:
1. Copies tiles to Character RAM (0xA000)
2. Writes the u16 tilemap directly into `HW_SCR1_MAP` (0x9000)
3. Loads palettes via `ngpc_gfx_set_palette()`

Works with any prefix generated by `ngpc_tilemap.py` as long as the symbols follow the
expected naming convention (`prefix_tiles`, `prefix_map_tiles`, `prefix_palettes`, ...).

### 7.4 Debug Checklist (Corrupted Render)

1. **Palettes**: loaded on the correct plane (SCR1 vs SCR2)?
2. **Tile base**: avoided overwriting sysfont (`tile_base >= 128`)?
3. **Helpers**: using a template that defines `NGP_FAR` + up-to-date signatures?
4. **Fallback test**: does `NGP_TILEMAP_BLIT_SCR1/_SCR2` render correctly?
   - Yes → asset is healthy, problem is in helpers (near/far pointer)
   - No → asset may be corrupt or video init is incorrect
5. **Two-class bugs:**
   - **Class 1 — Init registers:** never zero-fill an unknown hardware register. Use bitwise ops (`|=`, `&=`) on documented bits only. See `HW_SCR_PRIO` (0x8030) for an example.
   - **Class 2 — Near/far:** ROM is linked at 0x200000+; `const` arrays live there. Pointers passed without `NGP_FAR` are truncated to 16-bit → wrong data read.

---

## 8. Known Bugs and Fixes

### 8.1 u8 Overflow in Tilemap Index

**Symptom:** `ngpc_gfx_put_tile(plane, 14, 17)` displays in the wrong cell
(e.g. top-right instead of bottom-right).

**Root cause:** cc900 may perform `u8 * int_literal` in 8-bit arithmetic.
With `y = 17` (u8): `17 * 32 = 544` → truncated to u8 = 32 → `map[32 + 14] = map[46]` = row 1, col 14 = **top right**.

**Fix:**
```c
/* Bug: */
map[y * SCR_MAP_W + x] = make_entry(...);

/* Fix: */
map[(u16)y * SCR_MAP_W + x] = make_entry(...);
```

Apply to all three functions: `put_tile`, `put_tile_ex`, `get_tile`.

### 8.2 s16 Overflow on Large Maps (Parallax)

**Symptom:** on maps taller than ~41 tiles, the game starts at the correct collision position
but the displayed area is wrong (wrong region of the level). No apparent visual offset — just
the wrong zone.

**Trigger threshold:** `cam_py > 32767 / parallax_pct`. With `pct=100`: `cam_py > 327` (= ~41 tiles).

**Root cause:** `cam_py * parallax_pct` is computed in s16.
With `cam_py=848` and `pct=100`: `848 * 100 = 84800` overflows s16 (max 32767).
Result is truncated to `84800 mod 65536 = 19264`, then `/ 100 = 192` instead of 848.
Both scroll registers and streaming receive this same wrong value — so no misalignment is
visible, just the wrong map zone.

> **Do NOT use `(s32)` casts** to fix this. cc900 will emit calls to `C9H_mulls`/`C9H_divls`
> (32-bit runtime helpers) which are not linked → link error -209.

**Fix:** divide first to stay within 16 bits:
```c
static s16 ngpng_scale_pct(s16 v, s16 pct) {
    s16 q = (s16)(v / (s16)100);
    s16 r = (s16)(v % (s16)100);
    return (s16)(q * pct + r * pct / (s16)100);
}

/* Replace: */
(cam_py * pct) / 100
/* With: */
ngpng_scale_pct(cam_py, pct)
```

Apply in both `ngpng_queue_plane_stream` and `ngpng_apply_plane_scroll`.

---

## Quick Reference

| Item | Value | Notes |
|------|-------|-------|
| SCR1 tilemap | `0x9000` | 32×32 u16 words |
| SCR2 tilemap | `0x9800` | 32×32 u16 words |
| Tile data (Character RAM) | `0xA000` | 16 bytes/tile, 512 tiles max |
| SCR1 palettes | `0x8280` | 16×4 colors, RGB444 |
| SCR2 palettes | `0x8300` | 16×4 colors, RGB444 |
| Scroll X/Y SCR1 | `0x8032/0x8033` | write 8-bit or 16-bit packed |
| Scroll X/Y SCR2 | `0x8034/0x8035` | write 8-bit or 16-bit packed |
| Tilemap entry | `SCR_ENTRY(tile, pal, hflip, vflip)` | or `SCR_TILE(tile, pal)` |
| Tile index formula | `(u16)y * 32u + x` | cast y to u16 |
| BIOS sysfont | tiles 32..127 | reserved after SYSFONTSET |
| User tile base | 128 (recommended) | avoids sysfont |
| Transparent color | palette 0, color 0 | scroll planes only |
| Rectangle blit stride | `BC=0x14, skip=0x18` | 20 cols in 32-col map |
| Camera clamp | before streaming | prevents black tile edges |
| Large map threshold | cam_py > 327 px | s16 overflow risk for parallax |
| u8 y overflow | `(u16)y * 32u` | fix in put_tile/get_tile |
| ROM map row order | rows stored reversed | row 0 last in array, base_ptr = &data[(H-1)*W] |
| ROM tile lookup | `base[col - row*W]` | Y-inverted: row inc = addr dec |
| Scroll Y formula | `-cam_y + correction` | Y physically inverted on NGPC |
| Init blit size | 20×19 tiles only | not 32×32 — streaming fills the rest |
| Streaming loaders | 4 (2 col + 2 row) | right/left/bottom/top, 21 tiles each |
| Loader inner loop | E=0x15 = 21 tiles | col: +0x40 stride; row: +2 stride |
| MapCtx plane field | +0x08 (u16) | 0x9000 or 0x9800, separate from ScrollCtx |
| Origin tile x/y | MapCtx +0x06/+0x07 | subtracted from tile_col/row before lookup |
| Shadow buffer SCR1 | `0x5D84`, 484 words | 22 cols x 22 rows, pitch = 44 bytes |
| Shadow buffer SCR2 | `0x614C` = 0x5D84+968 | same size |
| Tile manifest | `0x5D04`, 128 bytes | 64 x u16 tile ROM indices |
| Shadow flush rows | 18 (WA=0x12) | of 22 stored; 4 rows = write margin |
| Shadow IX (words/row) | 22 (0x16) | LDIRW word count per row |
| Shadow IY (skip bytes) | 20 (0x14) | `add DE, IY` after LDIRW: 64 bytes total |
| Shadow blank tile (init) | `0x0000` | tile 0 = transparent at level load |
| Shadow blank tile (game) | `0x0080` | tile 128 = dedicated blank during play |
| Shadow dirty flag | `0x59E4` bit6/bit7 | SCR1/SCR2, set per-entity motion detect |
| Shadow flip bits | tileword bits 14-15 | same format as VRAM: 14=H-flip, 15=V-flip |
| Shadow CharRAM bulk | 0xC00 words (6 KB) | level init, LDIRW ROM -> 0xA800 |
| Shadow 32-bit clear | `XWA=0x800080`, 242 iter | 4 bytes/iter = 968 bytes total |

---

## See Also

- [Hardware-Registers.md](../01_Hardware/Hardware-Registers.md) — Full register map (0x8030 SCR_PRIO, palette, scroll, tilemap addresses)
- [Sprites-and-OAM.md](Sprites-and-OAM.md) — OAM layout, sprite display alongside tilemaps
- [DMA.md](DMA.md) — MicroDMA for per-scanline scroll raster effects
- [Game-Loop.md](../05_Systems/Game-Loop.md) — VBlank pipeline: when to push scroll registers vs stream tiles
- [Colors-and-Palettes.md](Colors-and-Palettes.md) — RGB444 format, palette management
