# Asset Pipeline

End-to-end workflow for exporting and integrating graphic assets (tilemaps, tiles, palettes, compressed tiles) into an NGPC project.

---


## 1. Export Tilemap C + Optional Tile Binary

Standard background tilemap export:

```bash
python ngpc_tilemap.py assets/level1_bg.png \
  -o GraphX/level1_bg.c -n level1_bg --header \
  --tiles-bin GraphX/level1_bg_tiles.bin
```

For fixed full-screen screens (intro, title) — raw byte tiles, no deduplication:

```bash
python ngpc_tilemap.py assets/title.png \
  -o GraphX/title_intro.c -n title_intro --header \
  --emit-u8-tiles --black-is-transparent --no-dedupe
```

**Outputs:**
- `GraphX/<name>.c` + `GraphX/<name>.h` — tiles array, map array, palettes
- `GraphX/<name>_tiles.bin` (if `--tiles-bin`) — raw tile words, little-endian
- `<name>_tiles_u8[]` + `<name>_tile_count` (if `--emit-u8-tiles`)

**Key flags:**

| Flag | Effect |
|------|--------|
| `--header` | Generate `.h` file alongside `.c` |
| `--tiles-bin FILE` | Also export raw tile binary |
| `--emit-u8-tiles` | Emit tiles as `u8[]` instead of `u16[]` |
| `--black-is-transparent` | Treat opaque black as palette index 0 (transparent) |
| `--no-dedupe` | Disable tile deduplication (for full-screen unique tiles) |

---

## 2. Compress Tiles (Optional)

Optional LZ77 compression for tile data — reduces ROM usage at the cost of
decompression time at load:

```bash
python ngpc_compress.py GraphX/level1_bg_tiles.bin \
  -o GraphX/level1_bg_tiles_lz.c -n level1_bg_tiles -m lz77 --header
```

Resulting runtime symbols:
- `level1_bg_tiles_lz[]` — compressed data array
- `level1_bg_tiles_lz_len` — compressed data size

Call the appropriate decompress routine at load time before uploading to VRAM.

---

## 3. Runtime Loading

Two approaches for loading tile data at runtime:

**Method A — Direct VRAM macro (safe path, recommended when NGP_FAR issues arise):**

Use macros from `ngpc_tilemap_blit.h`. These index the generated symbol
directly within the compilation unit — no pointer passed as a parameter, so no
near/far issue is possible.

```c
#include "ngpc_tilemap_blit.h"

void level_init(void)
{
    NGP_TILEMAP_BLIT_SCR1(level1_bg, TILE_BASE);
}
```

**Method B — Helper functions (standard path):**

Use `ngpc_gfx_load_tiles_at()` and related helpers. Requires `NGP_FAR` on all
ROM pointer parameters.

```c
#include "ngpc_gfx.h"
#include "GraphX/level1_bg.h"

void level_init(void)
{
    /* NGP_FAR required — data is in ROM at 0x200000+ */
    ngpc_gfx_load_tiles_at(level1_bg_tiles, level1_bg_tiles_count, TILE_BASE);
    ngpc_gfx_load_palette_scr1(0, level1_bg_pal);
    /* blit tilemap ... */
}
```

If you see corrupted rendering with Method B: verify `NGP_FAR` is present on all
ROM-pointing parameters, then fall back to Method A if needed.

---

## 4. Integrate into Project

1. Add generated `GraphX/*.c` files to the build object list in the Makefile.
2. Include generated `GraphX/*.h` headers in game code.
3. Call the load function in your state initialization (`title_init`, `level_init`, etc.).
4. Ensure sprites/metasprites are linked **before** tilemaps — see [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) §8.2 (linker order bug).

---

## 5. Tool Notes and Limits

| Constraint | Value | Notes |
|------------|-------|-------|
| Source dimensions | Multiple of 8 | Width and height must divide into 8×8 tiles |
| Max tiles (unique) | 512 | Hardware tile RAM limit (after dedupe) |
| Max tilemap size | 32×32 cells | SCR1/SCR2 are 32×32 maps |
| Colors per tile | 3 visible + 1 transparent | Per 8×8 tile; palette index 0 = transparent |
| Max palettes | 16 | Per plane |
| Alpha threshold | alpha < 128 → transparent | Source pixel becomes palette index 0 |
| Transparent color | Palette index 0 | Use `--black-is-transparent` for legacy behavior |
| Default black | Treated as opaque | Preserved unless flag specified |
| `tiles_count` | Count of `u16` words | = `num_tiles × 8`, NOT number of tiles |
| `map_tiles[]` | Indices 0..N into tile set | Add `TILE_BASE` when writing to VRAM |
| Single-layer output | When all tiles ≤ 3 visible colors | Tool auto-detects; no split needed |

> **tiles_count gotcha:** the generated `<name>_tiles_count` constant counts `u16` words,
> not tiles. A tile is 8 words (16 bytes). Divide by 8 to get tile count.

---

## 6. Large Map (Streaming) Export — Y-Inverted Row Layout

> See also: [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) §6.12 for runtime details.

Maps exported for streaming-style scrolling use a **Y-inverted row layout**.
The ROM tile lookup formula is `base[col - row * map_w]`, which means row 0 of the visual
level is at the highest address in the array.

### 6.1 Required Export Format

```python
# Tool must store rows in REVERSE order (bottom row first, top row last in memory)
# Visual row 0 (top of level) = last row stored = base_ptr offset (H-1)*W

map_words = []
for row in range(H - 1, -1, -1):       # bottom → top in ROM
    for col in range(W):
        map_words.append(tilewords[row][col])   # u16 tileword

# In generated C:
# const u16 my_map[H * W] = { /* row H-1, row H-2, ..., row 1, row 0 */ };
# The "base" pointer passed to mapstream = &my_map[(H-1) * W]
```

### 6.2 C Array Declaration

```c
/* Generated by export tool: rows stored reversed (bottom first, top last) */
const uint16_t my_map[MAP_H * MAP_W] NGP_FAR = {
    /* row MAP_H-1 (bottom of level): */
    0x0100, 0x0100, 0x0101, ...,
    /* row MAP_H-2: */
    ...
    /* row 0 (top of level, visible at cam_y=0): */
    0x0102, 0x0103, ...
};

/* Pass to mapstream: base ptr = top row */
#define MY_MAP_BASE  (&my_map[(MAP_H - 1u) * MAP_W])
```

### 6.3 Why Y Is Inverted

On NGPC, `SCR_Y = -cam_y + correction`. As cam_y increases (camera moves down visually),
the scroll register decreases, and the hardware shows tiles from earlier (lower VRAM row
offset) addresses. The row-reversed ROM storage is the natural consequence: tile(row=0)
at the highest address matches cam_y=0 (top of screen showing top of level).

### 6.4 Tile Lookup Recap (for tool verification)

```python
def get_tileword(map_array, map_w, map_h, col, row):
    """
    map_array: flat list, stored bottom-first (row H-1 at index 0)
    base index (in the C array) for visual row 0 = (map_h-1)*map_w
    """
    base_idx = (map_h - 1) * map_w
    idx = base_idx + col - row * map_w
    return map_array[idx]
    # Equivalent to: map_array[(map_h - 1 - row) * map_w + col]
```

---

## 7. Custom Font Export

Converts a font PNG tilesheet into NGPC 2bpp tile data compatible with the `ngpc_dialog`
module when `NO_SYSFONT` is defined.

### 7.1 PNG Format

| Dimension | Cols × Rows | Tiles | Default tile_base |
|-----------|-------------|-------|-------------------|
| 128×48    | 16 × 6      | 96    | 32 (ASCII 32–127) |
| 256×24    | 32 × 3      | 96    | 32 (ASCII 32–127) |
| 256×32    | 32 × 4      | 128   | 0  (ASCII 0–127)  |
| Any N×8 width | N/8 × floor(H/8) | auto | 32 |

Tile order: left→right, top→bottom. ASCII code = tile slot (direct mapping,
identical to the BIOS system font).

**Black pixels (`RGB(0,0,0)`) are treated as transparent** (same convention as
the sprite export tool). Use near-black (e.g. `RGB(1,1,1)`) for character pixels
if the source PNG uses black strokes.

### 7.2 Usage

```bash
python ngpc_font_export.py font.png -o GraphX/gen/ngpc_custom_font -n ngpc_custom_font
python ngpc_font_export.py font.png -o GraphX/gen/ngpc_custom_font --tile-base 32
python ngpc_font_export.py font.png -o GraphX/gen/ngpc_custom_font --no-outline
```

### 7.3 Outline Auto-Generation

When the source PNG has **exactly 1 visible colour** (body only, no baked outline),
the tool automatically generates a 1-pixel black outline around each glyph:

- Body pixels → color index 2 (`RGB(15,15,15)` white)
- Transparent pixels 8-adjacent to body → color index 1 (`RGB(0,0,0)` black outline)
- Remaining transparent → color index 0 (hardware transparent)

Use `--outline` to force, `--no-outline` to disable.
PNGs with 2+ visible colours skip auto-generation (outline already baked in).

### 7.4 Generated Files

`ngpc_custom_font.c` — tile array `const u16 NGP_FAR ngpc_custom_font_tiles[N * 8]`

`ngpc_custom_font.h` — exposes:
```c
#define NGPC_FONT_TILE_BASE   32u
#define NGPC_FONT_TILE_COUNT  96u
#define ngpc_custom_font_load()   /* loads tiles into VRAM */
#define ngpc_custom_font_set_palette(plane, slot)  /* applies font palette */

/* Palette extracted from PNG (or outline defaults): */
#define NGPC_CUSTOM_FONT_PAL_C0  RGB(0,0,0)    /* transparent, irrelevant */
#define NGPC_CUSTOM_FONT_PAL_C1  RGB(15,15,15) /* body (white) */
#define NGPC_CUSTOM_FONT_PAL_C2  RGB(0,0,0)    /* outline (black) */
#define NGPC_CUSTOM_FONT_PAL_C3  RGB(0,0,0)    /* unused */
```

### 7.5 Palette Slots with NO_SYSFONT

The editor dialogue tab provides 3 colour swatches per scene (slots 1–3):

| Swatch | Index | Role |
|--------|-------|------|
| 1 | 1 | Glyph body colour |
| 2 | 2 | Glyph outline colour |
| 3 | 3 | Accent / cursor |
| T | 0 | Always transparent — hardware, read-only |

These are saved in `dialogue_config.palette` and emitted as `SCENE_DLG_PAL_1/2/3`
in the generated dialog header. Each scene can have a different font colour.

With `NO_SYSFONT`, `ngpc_font_apply_palette()` is **not** called in `ngpc_dialog_open()`
— the palette is fully controlled by the generated per-scene code.

### 7.6 Makefile Integration

```makefile
CDEFS += -DNO_SYSFONT=1
CDEFS += -DFONT_TILE_BASE=350          # must not overlap BG or sprite tiles
OBJS  += $(OBJ_DIR)/GraphX/gen/ngpc_custom_font.rel
OBJS  += $(OBJ_DIR)/optional/ngpc_dialog/ngpc_dialog.rel
OBJS  += $(OBJ_DIR)/optional/ngpc_dialog/ngpc_font.rel
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Export background tilemap | `python ngpc_tilemap.py INPUT.png -o GraphX/OUT.c -n NAME --header` |
| Export intro (no dedupe, u8 tiles) | Add `--emit-u8-tiles --black-is-transparent --no-dedupe` |
| Export + raw tile binary | Add `--tiles-bin GraphX/OUT_tiles.bin` |
| Compress tile binary | `python ngpc_compress.py INPUT.bin -o OUT_lz.c -n NAME -m lz77 --header` |
| Safe load (no far issue) | `NGP_TILEMAP_BLIT_SCR1(prefix, tile_base)` macro |
| Standard load | `ngpc_gfx_load_tiles_at(NGP_FAR *tiles, count, offset)` |
| Link order | Metasprite `.rel` before tilemap `.rel` in Makefile |
| Streaming map rows | Reversed (bottom first) | `base_ptr = &data[(H-1)*W]` |
| Streaming tile lookup | `base[col - row*W]` | Y-inverted — see §6 above |
| Export custom font | `python ngpc_font_export.py font.png -o GraphX/gen/ngpc_custom_font -n ngpc_custom_font` |
| Force/disable outline | Add `--outline` or `--no-outline` |

---

## See Also

- [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) — VRAM layout, tilemap entry format, upload patterns
- [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — NGP_FAR rules, linker order bug, Makefile integration
- [Sprites and OAM](../03_Graphics/Sprites-and-OAM.md) — Sprite asset export, metasprite format
- [Colors and Palettes](../03_Graphics/Colors-and-Palettes.md) — RGB444 palette format, palette slot layout
