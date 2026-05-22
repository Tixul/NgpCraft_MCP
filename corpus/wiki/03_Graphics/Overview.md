# Graphics Overview

Practical guide to the NGPC graphics pipeline: from PNG source to on-screen display, the helper-function and direct-VRAM display paths, common bug classes, and hardware constraints.

---


## 1. Pipeline Overview

```
PNG source
    |
    v
tilemap export tool
    |
    v
generated foo.c + foo.h
(tiles u16/u8, tilemap, palettes)
    |
    v
C code (Method A or B)
    |
    v
Display — 160x152 px screen
```

---

## 2. Tilemap Tool Commands

### 2.1 Full-Screen (intro/title)

Raw byte tiles, no deduplication — for screens that use all 380 unique tiles:

```bash
python tilemap_export.py assets/title.png \
  -o title_intro.c -n title_intro --header \
  --emit-u8-tiles --black-is-transparent --no-dedupe
```

### 2.2 Standard Background

```bash
python tilemap_export.py assets/level1_bg.png \
  -o level1_bg.c -n level1_bg --header
```

### 2.3 Dual-Layer (SCR1 + SCR2)

```bash
python tilemap_export.py scr1.png --scr2 scr2.png \
  -o level1.c -n level1 --header --emit-u8-tiles
```

### 2.3.1 SCR2 as Dialog / HUD Overlay

SCR2 can be used as a full-screen overlay above the gameplay tilemap (SCR1).
This is the approach used by some commercial NGPC games and by an optional
dialog module.

**Priority control — `HW_SCR_PRIO` bit 7:**
```c
HW_SCR_PRIO = (u8)(HW_SCR_PRIO | 0x80u);   /* SCR2 in front of SCR1 */
HW_SCR_PRIO = (u8)(HW_SCR_PRIO & 0x7Fu);   /* SCR1 in front (default) */
```

**Rules:**
- SCR2 tiles with color index 0 are transparent — the corresponding SCR1 tile shows through.
  This is how the tilemap remains visible around the dialog box edges.
- The dialog box interior must be filled with tiles using color index 2 or 3 for all
  background pixels. Using color index 0 causes a "black grid" transparency glitch.
  See [Colors-and-Palettes.md](Colors-and-Palettes.md) §3 for details and the fix formula.
- On close, clear the SCR2 rect (write tile 0 / pal 0) to restore transparency and let
  SCR1 show through cleanly again.

**Scroll:** call `ngpc_gfx_scroll(GFX_SCR2, 0, 0)` on dialog open to reset SCR2 position
if the parallax scroll system may have moved it.

### 2.4 Important Notes on Output Symbols

| Symbol | Meaning |
|--------|---------|
| `<name>_tiles[]` | Tile data (u16 or u8 words) |
| `<name>_tiles_count` | Count of **u16 words** (= num_tiles × 8), NOT tile count |
| `<name>_map_tiles[]` | Tile indices 0..N into the deduped tile set |
| `<name>_map_pals[]` | Palette index per tile cell |
| `<name>_palettes[]` | Flat palette data (4 colors per palette) |

> `tiles_count` counts `u16` words, not tiles. One tile = 8 words.
> To get tile count: `tiles_count / 8`.

> `map_tiles[]` holds indices into the unique tile set. Add `TILE_BASE` when writing
> tile indices to the tilemap VRAM.

---

## 3. Method A — Helper Functions (Normal Path)

Recommended approach. Requires helpers compiled with correct `NGP_FAR` signatures.

```c
#include "ngpc_gfx.h"
#include "../GraphX/intro_image.h"

#define INTRO_TILE_BASE 128u  /* avoid overwriting BIOS sysfont (tiles 32-127) */

static void intro_init(void)
{
    u16 i;

    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    /* Tiles — NGP_FAR handled internally by the helper */
    ngpc_gfx_load_tiles_at(intro_image_tiles,
                           intro_image_tiles_count,
                           INTRO_TILE_BASE);

    /* Palettes */
    for (i = 0; i < (u16)intro_image_palette_count; ++i) {
        u16 off = (u16)i * 4u;
        ngpc_gfx_set_palette(GFX_SCR1, (u8)i,
            intro_image_palettes[off + 0],
            intro_image_palettes[off + 1],
            intro_image_palettes[off + 2],
            intro_image_palettes[off + 3]);
    }

    /* Tilemap */
    for (i = 0; i < intro_image_map_len; ++i) {
        u8  x    = (u8)(i % intro_image_map_w);
        u8  y    = (u8)(i / intro_image_map_w);
        u16 tile = (u16)(INTRO_TILE_BASE + intro_image_map_tiles[i]);
        u8  pal  = (u8)(intro_image_map_pals[i] & 0x0Fu);
        ngpc_gfx_put_tile(GFX_SCR1, x, y, tile, pal);
    }
}
```

> If rendering is corrupted with this method, verify all ROM-pointing parameters
> have `NGP_FAR`, then try Method B to isolate the cause.

---

## 4. Method B — Direct VRAM Macros (Fallback Path)

Uses a direct-blit macro header. Writes directly to VRAM without passing pointers
as parameters — eliminates near/far pointer issues entirely.

Use when:
- Suspecting a pointer issue in the helpers
- Wanting a 100% direct display path for diagnostics

```c
#include "ngpc_tilemap_blit.h"
#include "../GraphX/intro_image.h"

#define INTRO_TILE_BASE 128u

static void intro_init(void)
{
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    NGP_TILEMAP_BLIT_SCR1(intro_image, INTRO_TILE_BASE);
}
```

What the macro does:
1. Copies tiles to Character RAM (`0xA000`) — 16 bytes per tile
2. Writes the tilemap (`u16` entries) directly into `HW_SCR1_MAP` (`0x9000`)
3. Loads palettes via `ngpc_gfx_set_palette()`

Works with any prefix generated by the tilemap export tool as long as the symbols follow
the contract: `prefix_tiles`, `prefix_map_tiles`, `prefix_palettes`, etc.

---

## 5. Two Bug Classes

### 5.1 Class 1 — Video Register Init

**General rule:** never overwrite a video hardware register with a blanket `0` or `0xFF`
if you do not know what all bits do.

Prefer bitwise operations (`|=`, `&=`, `^=`) on documented bits only.

Example: `HW_SCR_PRIO` (`0x8030`) — typical init forces only bit 7 (SCR1/SCR2 priority),
leaving other bits untouched.

Writing `0` to an undocumented register field may disable an unknown hardware feature
and cause subtle visual glitches that are hard to trace.

### 5.2 Class 2 — Near/Far Pointers + ROM Assets

**Context:** the ROM is linked at `0x200000`. All `const` generated assets live at
`0x200000+`. The C compiler uses a near/far pointer model: if a pointer is treated as near
(16-bit), the address is truncated → reading from the wrong location.

**Typical symptom:** the export tool generates correct data, but rendering through
certain helpers produces garbage or a shifted image.

**Fix:** ensure all helpers use `NGP_FAR` in their ROM-pointer parameter signatures.
See [Build-Toolchain.md](../02_CPU-and-Toolchain/Build-Toolchain.md) §2 for full NGP_FAR rules and decision tree.

**Diagnostic:** if Method B (`NGP_TILEMAP_BLIT_*`) renders correctly but Method A does not,
the asset data is sound — the problem is in the helper parameter signatures (near/far).

---

## 6. Debug Checklist

When graphics are corrupted or not displayed:

1. **Palettes** — are they loaded on the correct plane (`GFX_SCR1` vs `GFX_SCR2`)?
2. **Tile base** — did you avoid overwriting the font zone (`tile_base >= 128` for **every** asset, including sprite metasprites)? Symptoms of collision: text with garbled characters, "space" erase leaving debris, digits correct but letters corrupted. Check both scroll-plane tilemaps AND the `<sprite>_tile_base` constants in generated metasprite source files.
3. **Helpers** — do all ROM-pointer signatures define `NGP_FAR`?
4. **Fallback test** — does `NGP_TILEMAP_BLIT_SCR1/_SCR2` render correctly?
   - **Yes** → asset is valid; problem is in the helpers (near/far pointer issue)
   - **No** → asset may be corrupted, or video init is wrong
5. **Raw data** — verify generated tiles/map/palettes byte-by-byte before suspecting
   the C pipeline

---

## 7. Hardware Constraints

| Constraint | Value | Notes |
|------------|-------|-------|
| Tilemap size | 32×32 tiles | SCR1 and SCR2 are both 32×32 |
| Visible screen | 20×19 tiles (160×152 px) | |
| Tile slots 0-31 | Reserved by hardware | |
| Tile slots 32-127 | **Font zone** (BIOS sysfont OR custom font) | The sysfont loader and custom font exporters (default tile base 32) both load here |
| Tile slots 128-511 | Available for game assets | **All** tilemaps and sprites must use `tile_base >= 128` — including sprites whose `<name>_tile_base` is embedded in the generated metasprite source (re-export with `--tile-base 128+` if the generated value is < 128) |
| Max unique tiles | 512 total | Character RAM = 8 KB (512 × 16 bytes) |
| SCR palettes | 16 palettes × 4 colors | Separate sets for SCR and sprite planes |
| Palette color format | `0x0BGR` (12-bit RGB444) | Each channel 0-15 |
| Palette 0, color 0 | **Transparent** for scroll planes | Always |
| Colors per tile | 3 visible + 1 transparent | Color index 0 = transparent |

---

## 8. Pseudo-3D Perspective Scaling

This section documents a pseudo-3D forward-perspective technique observed in a
commercial NGPC racing/simulation title. All values are confirmed from reverse
engineering, with no extrapolations.

### 8.1 Overview

The technique simulates a forward-facing perspective view using depth-based sprite
substitution and two lookup tables for screen X and Y placement. There is no hardware
scaling on NGPC — the scaling illusion is entirely pre-baked.

The system uses one central formula and two tables:
```
zoom_index = (range - (entity_z - camera_z)) / 25

screen_X = 80 - persp_x[zoom_index]
screen_Y = (0x50 - persp_y[zoom_index]) & 0xFF
```

Both axes use the same `zoom_index`. One division by 25 drives both.

### 8.2 Depth → Zoom Index

```asm
ld   XBC, (XWA+8)         ; XBC = entity Z (world depth)
sub  XBC, (camera_z)      ; XBC -= camera Z
jrl  MI, skip             ; behind camera → skip
sub  XBC, range           ; XBC -= range_max
jrl  GT, skip             ; too far → skip
neg  XBC                  ; XBC = range - dist  (0 = farthest visible)
extz XBC
div  XBC, 0x0019          ; zoom_index = XBC / 25  (0x19 = 25 decimal)
```

**Divisor 25 is intentional and constant** across all entity types.
It equals NGPC screen height (200px) divided by tile size (8px).
Each zoom level corresponds to one tile-height of apparent size change.
Range determines the total number of levels: `range / 25 = level count`.

Ranges observed:
| Range | Level count | Object type |
|-------|-------------|-------------|
| 6400 (0x1900) | 256 | Large main body |
| 5600 (0x15E0) | 224 | Platform/building |
| 5000 (0x1388) | 200 | Distant scenery |
| 1600 (0x0640) | 64  | Close object |
|  800 (0x0320) | 32  | Sign / small detail |

### 8.3 Perspective X Table

256 u8 entries. `screen_X = 80 - persp_x[zoom_index]`

Screen center = 80px (half of 160px screen width).
- `zoom_index 0..70`: all 0 — object below appearance threshold, no lateral spread
- `zoom_index 255`: value 54 — maximum lateral offset at closest distance

Application for road edges:
```c
left_edge  = 80 - persp_x[zoom_index];   /* converges to center at distance */
right_edge = 80 + persp_x[zoom_index];   /* same, mirrored */
```

The 256-entry table allows indexing directly without bounds check as long as
`zoom_index` is clamped to `range/25 - 1` before use.

### 8.4 Perspective Y Table

256 u8 entries. `screen_Y = (0x50 - persp_y[zoom_index]) & 0xFF`

Uses unsigned 8-bit wraparound for the subtraction (same as the hardware arithmetic).

Bounds check before use:
- `Y < 8 (0x08)` → above horizon → cull
- `Y >= 144 (0x90)` → below screen → cull
- **Valid road Y range: [8 .. 143]**

The Y table pointer is stored in a RAM variable and initialized from the ROM table.

Representative assembly:
```asm
ld   XWA, (y_table_ptr) ; XWA = Y table base ptr
ld   C,   (XWA+BC)      ; C = persp_y[zoom_index]
neg  C                  ; negate
add  C, 0x50            ; C = 0x50 - persp_y[zoom_index] (8-bit wrap)
ld   (screen_y), C      ; store computed screen Y
```

### 8.5 Pre-Baked Sprite Selection

Each object type has a table of frame_list pointers, one per zoom level.
A frame_list describes the set of 8x8 OAM parts that compose the object at
that size. `NULL` (or tile_index=0) means invisible at that distance.

Frame_list entry format (5 bytes, confirmed from the consumer loop):
```
+0  u16  tile_index   — ROM tile bank index (0 = end sentinel)
+2  u8   x_off        — X offset from entity screen origin
+3  u8   y_off        — Y offset
+4  u8   flags        — SPR_HFLIP, SPR_VFLIP, priority bits
```

Objects only appear in the frame_list from their appearance threshold outward:
- Zoom 0–25: NULL (invisible, too far)
- Zoom 26: first visible entry (small tile at `x=0x00, y=0xF8`)
- Zoom 255: largest representation (8+ OAM parts for the closest view)

### 8.6 Forward Motion — Background Scroll

The impression of moving forward comes from advancing the BG scroll register each
frame. The NGPC tilemap is 32 tiles tall (256px), wrapping seamlessly.

Scroll registers:
```c
/* Packed 16-bit write: Y in high byte, X in low byte */
*(volatile u16 *)0x8032u = (u16)((u16)scr1_y << 8) | x_off;  /* SCR1 road layer */
*(volatile u16 *)0x8034u = (u16)((u16)scr2_y << 8) | x_off;  /* SCR2 horizon */
```

Parallax depth cue: scroll the horizon layer at a fraction of the road speed.
This technique does not use a raster/scanline scroll for the road itself — the perspective
effect comes entirely from the sprite depth system, not from a warped BG.

### 8.7 Z Culling

Three culling conditions, all confirmed:
```c
/* (a) Object passed behind camera — z_extent allows large objects to stay
        visible even when their origin Z is behind the camera plane */
if ((s32)(entity_z + z_extent) < camera_z) → cull;

/* (b) Object origin is behind the camera */
if ((entity_z - camera_z) < 0) → cull;

/* (c) Object is beyond the far plane */
if ((entity_z - camera_z) > range) → cull;
```

`z_extent` is at entity struct offset +16 (confirmed from the entity pool layout).

### 8.8 Full Per-Frame Pipeline (confirmed architecture)

```
[GAME LOGIC]
  camera_z += speed                         ; advance camera
  For each entity:
    dist = entity_z - camera_z
    cull check (a)(b)(c)
    zoom_index = (range - dist) / 25
    screen_x = 80 - persp_x[zoom_index]
    screen_y = (0x50 - persp_y[zoom_index]) & 0xFF
    if screen_y not in [8..143]: cull
    submit parts from frame_list[zoom_index] → OAM cursor

  tile_accum_flush()    ; one ldirw: RAM buffer → VRAM

[VBLANK]
  LDIRW shadow_oam (0x48BC) → HW_OAM (0x8800)   ; count = oam_used * 4
  LDIR  shadow_pal (0x493E) → HW_PAL (0x8C00)
  tail-clear: write 0xF8,0xF8 to XY bytes of slots [oam_used..prev_used-1]
```

---

## Quick Reference

| Task | Method |
|------|--------|
| Full-screen image | `--emit-u8-tiles --black-is-transparent --no-dedupe` |
| Dual-layer | `--scr2 scr2.png` |
| `tiles_count` unit | u16 words (divide by 8 for tile count) |
| Tile offset in map | Add `TILE_BASE` to `map_tiles[]` value |
| Avoid sysfont | `TILE_BASE >= 128` |
| Normal load path | Method A — `ngpc_gfx_load_tiles_at()` with `NGP_FAR` |
| Near/far-safe path | Method B — `NGP_TILEMAP_BLIT_SCR1(prefix, base)` |
| Diagnostic split | Method B OK + Method A wrong → near/far bug in helpers |
| Video register safety | Use `\|=`, `&=` — never write `0` to unknown bits |
| Palette plane | `GFX_SCR1`, `GFX_SCR2`, `GFX_SPR` — do not mix |
| Transparent color | Palette slot 0 color 0 — never assign visible color |

---

## See Also

- [Asset-Pipeline.md](../06_Pipeline-and-Patterns/Asset-Pipeline.md) — Export workflow, tool commands, compression
- [Tilemaps-and-Scrolling.md](Tilemaps-and-Scrolling.md) — VRAM layout, tilemap entry format, upload patterns
- [Build-Toolchain.md](../02_CPU-and-Toolchain/Build-Toolchain.md) — NGP_FAR rules, compiler pitfalls, linker order
- [Colors-and-Palettes.md](Colors-and-Palettes.md) — RGB444 format, palette layout, color manipulation
- [Sprites-and-OAM.md](Sprites-and-OAM.md) — Sprite asset pipeline, metasprite format, OAM watermark
- [Gameplay-Patterns.md](../06_Pipeline-and-Patterns/Gameplay-Patterns.md) — Pseudo-3D racing patterns, Z-sort, entity culling
