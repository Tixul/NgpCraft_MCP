# Colors and Palettes

RGB444 color encoding, palette organization (SCR and sprite banks), the 3-colors-per-tile constraint, shadow palette patterns, and palette effects.

---


## 1. Color Encoding — RGB444

### 1.1 Format

The K2GE uses **12-bit RGB444** colors, stored as `u16` with the high nibble always zero:

```
Bit 15-12: always 0
Bit 11- 8: Blue  (0-15)
Bit  7- 4: Green (0-15)
Bit  3- 0: Red   (0-15)

Format: 0x0BGR
```

The `RGB(r, g, b)` macro is provided for convenience:

```c
/* RGB macro (r, g, b each 0-15) */
#define RGB(r, g, b)  ((u16)(((b) << 8) | ((g) << 4) | (r)))

RGB(15,  0,  0)   /* 0x000F — bright red */
RGB( 0, 15,  0)   /* 0x00F0 — bright green */
RGB( 0,  0, 15)   /* 0x0F00 — bright blue */
RGB(15, 15, 15)   /* 0x0FFF — white */
RGB( 0,  0,  0)   /* 0x0000 — black */
RGB( 8,  8,  8)   /* 0x0888 — mid-gray */
```

### 1.2 Predefined Colors

```c
/* Common palette colors */
#define COLOR_BLACK    RGB( 0,  0,  0)   /* 0x0000 */
#define COLOR_WHITE    RGB(15, 15, 15)   /* 0x0FFF */
#define COLOR_RED      RGB(15,  0,  0)   /* 0x000F */
#define COLOR_GREEN    RGB( 0, 15,  0)   /* 0x00F0 */
#define COLOR_BLUE     RGB( 0,  0, 15)   /* 0x0F00 */
#define COLOR_YELLOW   RGB(15, 15,  0)   /* 0x00FF */
#define COLOR_CYAN     RGB( 0, 15, 15)   /* 0x0FF0 */
#define COLOR_MAGENTA  RGB(15,  0, 15)   /* 0x0F0F */
```

---

## 2. Hardware Palette Layout

### 2.1 Palette Register Map

```
0x8200 - 0x827F   Sprite palettes (16 palettes × 4 colors × 2 bytes = 128 bytes)
0x8280 - 0x82FF   SCR1 palettes   (same layout)
0x8300 - 0x837F   SCR2 palettes   (same layout)
0x83E0            Background palette (4 colors)
0x83F0            Window palette     (4 colors)
```

Each palette bank:

```
palette[0]:  offset +0x00  color0 (transparent for scroll planes)
             offset +0x02  color1
             offset +0x04  color2
             offset +0x06  color3
palette[1]:  offset +0x08  color0
             ...
palette[15]: offset +0x78  color0..color3
```

One palette entry = one `u16` in `0x0BGR` format.

### 2.2 Palette Planes

| Plane | Address | GFX_* constant | Notes |
|-------|---------|----------------|-------|
| Sprite | `0x8200` | `GFX_SPR` | Sprite palettes |
| SCR1 | `0x8280` | `GFX_SCR1` | Scroll plane 1 palettes |
| SCR2 | `0x8300` | `GFX_SCR2` | Scroll plane 2 palettes |

> Always use `GFX_SCR1`, `GFX_SCR2`, `GFX_SPR` constants and `ngpc_gfx_set_palette()`
> rather than raw addresses. Some older documentation lists wrong values (0x8100,
> 0x8200, 0x8300 for SCR1/SCR2/SPR) — the correct addresses are those above, confirmed
> against `ngpc_hw.h` (`HW_PAL_SPR`, `HW_PAL_SCR1`, `HW_PAL_SCR2`).

Setting a palette:

```c
/* ngpc_gfx_set_palette(plane, pal_id, color0, color1, color2, color3) */
ngpc_gfx_set_palette(GFX_SCR1, 0,
    RGB(0, 0, 0),       /* color0 — transparent for scroll planes */
    RGB(15, 0, 0),      /* color1 — red */
    RGB(0, 15, 0),      /* color2 — green */
    RGB(15, 15, 15));   /* color3 — white */
```

---

## 3. 3 Colors per Tile Constraint

Each 8×8 tile uses **2 bits per pixel** (4 possible indices per pixel):

| Index | Meaning for scroll planes | Meaning for sprites |
|-------|--------------------------|---------------------|
| 0 | **Transparent** (shows through to lower layer) | Transparent |
| 1 | Color 1 of the tile's palette | Color 1 |
| 2 | Color 2 | Color 2 |
| 3 | Color 3 | Color 3 |

Each tile references one palette ID (0-15). All 4 pixels of a tile can use only the
4 colors from that single palette — not more.

**Constraint:** index 0 is always transparent for scroll planes (and most sprites).
This means each tile has only **3 visible colors** (indices 1-3) plus the palette 0
color 0 = `0x0000` is transparent.

> `palette[N].color0 = 0` must be set to `0x0000` (or any value — it is ignored for
> scroll planes since index 0 is treated as transparent regardless).

**Exception:** sprite color 0 is transparent per-sprite (handled via the OAM entry).

The export tools enforce this: each 8×8 tile must contain at most 3 distinct visible
colors (+ transparent/black). Tiles with more than 3 visible colors fail at export.

### Dialog / Text Overlay Implication

When rendering a **font or dialog box on a BG plane (SCR2 overlay)**, all background
pixels inside the box must use **color index 2 or 3** — never color 0.

If a font PNG is exported with background pixels as color 0 (the conventional "fully
transparent" value), those pixels will be transparent on NGPC hardware even though
the palette slot is loaded with an opaque color. The result is a visible grid of
transparency holes showing the layer below — a "black grid" glitch.

**Fix:** post-process the tile data to replace all color-0 pixels with color-2:
```python
# Applied to every u16 word in the tile array (NGPC 2bpp packed format)
new_w = (val | (((~val & 0x5555) << 1) & 0xFFFF)) & 0xFFFF
```
This converts `00` bit-pairs (color 0) to `10` (color 2) while leaving `01` (color 1
ink pixels) unchanged. The space/blank tile must be `0xAAAA` (all color 2), not
`0x0000` (all transparent).

Validated by disassembly of a commercial NGPC title that uses the same SCR2 overlay
approach with opaque fill tiles.

---

## 4. Shadow Palette Pattern

Rather than writing individual color entries scattered across frames, a more robust
approach is to maintain a **palette shadow** (RAM copy) and push the entire shadow
to hardware once per state change.

Pattern (derived from reverse-engineered commercial games):

```c
/* RAM shadow — one u16 per color, 16 palettes × 4 colors per bank */
static u16 s_pal_shadow_scr1[16 * 4];   /* SCR1 palette shadow */
static u16 s_pal_shadow_spr[16 * 4];    /* sprite palette shadow */

/* Modify shadow during gameplay */
void set_color(u8 pal, u8 idx, u16 color)
{
    s_pal_shadow_scr1[pal * 4 + idx] = color;
}

/* Push shadow to hardware (call in VBlank or state transition) */
void push_palette_scr1(void)
{
    u16 *hw = (u16 *)0x8100;
    u16 i;
    for (i = 0; i < 16 * 4; i++)
        hw[i] = s_pal_shadow_scr1[i];
}
```

**For large palette sets** (many levels, full-screen art):
```
1) Load new level's colors into RAM shadow
2) Restore fixed UI/text palette slots (always the same)
3) Push full shadow to hardware in one burst at level load
```

This avoids per-frame palette writes and ensures a clean, atomic palette update.

**Region split (fixed UI vs per-level tiles):** partition the shadow into a fixed UI region
`[0 .. K)` and a per-level tile region `[K .. end)`. On level load, rewrite only the tile
region and always restore the UI region from its known-good copy. HUD/UI colors are then
never corrupted by a level-specific palette load.

---

## 5. Palette Effects

### 5.1 Nibble-Level Manipulation

Because each color is `0x0BGR` with 4-bit channels, palette effects can be performed
with simple nibble arithmetic — no floats required.

```c
/* Fade toward black: shift each channel right by 1 */
u16 darken(u16 color)
{
    u16 r = (color & 0x000F) >> 1;
    u16 g = (color & 0x00F0) >> 1;   /* keep nibble position */
    u16 b = (color & 0x0F00) >> 1;
    return (b & 0x0F00) | (g & 0x00F0) | (r & 0x000F);
}

/* Mix two colors (average) */
u16 blend(u16 a, u16 b)
{
    u16 r = (((a & 0x000F) + (b & 0x000F)) >> 1);
    u16 g = (((a & 0x00F0) + (b & 0x00F0)) >> 1) & 0x00F0;
    u16 bv= (((a & 0x0F00) + (b & 0x0F00)) >> 1) & 0x0F00;
    return bv | g | r;
}
```

Important: always operate on **color indices 1-3** (not 0 — transparent must stay intact).

### 5.2 Direct HW Flash (Instant Effect)

For instant effects (damage flash, invincibility blink): write directly to hardware
palette RAM for 1-2 frames, then restore from shadow.

```c
void sprite_damage_flash(u8 pal_id)
{
    /* Write white to all 3 visible colors of the sprite palette */
    u16 *hw_spr = (u16 *)0x8300;
    u16 base = pal_id * 4;
    hw_spr[base + 1] = RGB(15, 15, 15);   /* color1 */
    hw_spr[base + 2] = RGB(15, 15, 15);   /* color2 */
    hw_spr[base + 3] = RGB(15, 15, 15);   /* color3 */
}

/* In game update: */
if (entity_is_damaged) {
    if ((damage_timer & 1) == 0)
        sprite_damage_flash(PLAYER_PAL);
    else
        push_sprite_palette_shadow(PLAYER_PAL);  /* restore */
}
```

### 5.3 Palette FX Helper

For smooth fades, cycles, and flash effects managed automatically, the `ngpc_palfx`
helper provides:

```c
ngpc_palfx_fade_to_black(GFX_SCR1, 0, 2);   /* fade palette 0 to black, speed 2 */
ngpc_palfx_flash(GFX_SPR, PLAYER_PAL, RGB(15,15,15), 6);  /* 6-frame white flash */
ngpc_palfx_cycle(GFX_SCR1, WATER_PAL, 8);   /* rotate water palette every 8 frames */
ngpc_palfx_update();                          /* call once per frame */
```

See [Effects-and-Raster.md](Effects-and-Raster.md) for the full ngpc_palfx API.

---

## 6. Pitfalls

| Pitfall | Consequence | Fix |
|---------|-------------|-----|
| Player/enemy share same palette | Bullets/entities visually indistinguishable | Use separate palette IDs |
| Tile artifacts after asset re-export | Wrong colors after `tile_base`/`pal_base` mismatch | Re-check export offsets after any GraphX rebuild |
| Writing color 0 as a visible color | Scroll plane transparency holes | Reserve color0 = transparent for all SCR palettes |
| Using wrong plane constant | Colors on SCR1 displayed by SCR2 | Match `GFX_SCR1/SCR2/SPR` to the plane actually used |
| Direct HW write without restoring | Palette permanently changed | Always restore from shadow the next frame |
| Mixed palette for sprite + tilemap | Confusing palette IDs | SCR palettes (0-15) and sprite palettes (0-15) are separate |
| `RGB()` built from `u8` components | **Blue nibble lost**: an orange background renders green, while the data and the writes are both correct | Widen each component before shifting: `((u16)(r) & 15) \| (((u16)(g) & 15) << 4) \| (((u16)(b) & 15) << 8)`. cc900 evaluates `(u8 & 0xF) << 8` in 8-bit width |
| Palette write helper written as a macro | Colours keep their low byte, high nibble lost: palette RAM takes **16-bit accesses only** | Keep the write in a *function* taking `u16` parameters — that is what makes cc900 emit word stores |

---

## Quick Reference

| Item | Value | Notes |
|------|-------|-------|
| Color format | `0x0BGR` (12-bit RGB444) | B/G/R each 0-15 |
| RGB macro | `RGB(r, g, b)` | `((b<<8)\|(g<<4)\|r)` |
| Sprite palette RAM | `0x8200` | 16 palettes x 4 colors x 2 bytes |
| SCR1 palette RAM | `0x8280` | same layout |
| SCR2 palette RAM | `0x8300` | same layout |
| Background / window | `0x83E0` / `0x83F0` | 4 colors each |
| GFX constants | `GFX_SCR1`, `GFX_SCR2`, `GFX_SPR` | use in API calls |
| Colors per tile | 3 visible + 1 transparent | index 0 = always transparent on scroll |
| Max palettes | 16 per plane | indices 0-15 |
| Set-palette API | `ngpc_gfx_set_palette(plane, id, c0..c3)` | |
| White | `RGB(15,15,15)` = `0x0FFF` | |
| Black | `RGB(0,0,0)` = `0x0000` | |
| Damage flash | Direct HW write then restore from shadow | 1-2 frames |
| Smooth fade | `ngpc_palfx_fade_to_black(plane, pal, speed)` | see Effects-and-Raster |
| Palette cycle | `ngpc_palfx_cycle(plane, pal, speed)` | for water/lava |

---

## See Also

- [Hardware-Registers.md](../01_Hardware/Hardware-Registers.md) — Palette RAM addresses, full K2GE register map
- [Effects-and-Raster.md](Effects-and-Raster.md) — ngpc_palfx API (fade, flash, cycle), VBlank update
- [Tilemaps-and-Scrolling.md](Tilemaps-and-Scrolling.md) — `set_color_direct()`, palette slot in tilemap entry
- [Sprites-and-OAM.md](Sprites-and-OAM.md) — Sprite palette index in OAM, per-sprite transparency
- [Asset-Pipeline.md](../06_Pipeline-and-Patterns/Asset-Pipeline.md) — Export tool palette generation, `--fixed-palette` flag
