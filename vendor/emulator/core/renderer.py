"""K2GE framebuffer renderer (M2 Phase 1).

Pass 1.0 deliverable: backdrop-only frame compose + binary P6 PPM
export. Subsequent passes add scroll-plane raster (1.1), sprite raster
with PR.C composition (1.2), and window clip + NEG invert (1.3).

The renderer reads through the same merged cold-start + savestate
memory view that the M2 Phase 0 inspectors consume (`palette-info`,
`oam-info`, `tilemap-info`, `tile-view`), so a single `--seed-from
<state.json>` workflow drives both inspection and visual export.

Source references:
- `01_SDK/docs/NGPC_HW_QUICKREF.md` § 5 "REGISTRES VIDÉO K2GE"
- `core/k2ge.py` — `K2geControlRegisters`, palette decoders, tile decoder

NGPC screen: 160 × 152 pixels, 60 fps. The renderer does not model
timing yet — it produces a single frame from a static memory snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.k2ge import (
    K2GE_PALETTE_BG_COLORS_BASE,
    K2GE_PALETTE_SCR1_BASE,
    K2GE_PALETTE_SCR2_BASE,
    K2GE_PALETTE_SPRITE_BASE,
    K2geColor,
    K2geControlRegisters,
    K2geSprite,
    decode_color,
    read_control_registers,
    read_oam_sprites,
    read_plane_palettes,
    read_tile,
    read_tilemap,
)

NGPC_SCREEN_WIDTH = 160
NGPC_SCREEN_HEIGHT = 152

# Scroll plane geometry: 32 tiles × 32 tiles × 8-pixel tiles = 256×256 pixel
# plane. Scroll offsets wrap modulo this size.
_SCR_PLANE_PIXEL_SIZE = 256
_TILE_SIZE = 8


@dataclass(frozen=True)
class RenderedFrame:
    """One composed framebuffer plus the control-register snapshot.

    `pixels` is a tuple of `height` rows; each row is a tuple of
    `width` `K2geColor` entries. `K2geColor` carries 4-bit RGB
    components (0..15) matching the K2GE 12-bit 0BGR encoding.

    `control` is the `K2geControlRegisters` snapshot read at compose
    time — preserved for JSON diagnostics and for tests that need to
    assert which scroll / window / priority bits drove the output.
    """

    width: int
    height: int
    pixels: tuple[tuple[K2geColor, ...], ...]
    control: K2geControlRegisters
    backdrop_color: K2geColor


def _read_byte(memory: dict[int, int], address: int) -> int:
    return memory.get(address & 0xFFFFFF, 0) & 0xFF


def resolve_oowc_color(
    memory: dict[int, int], control: K2geControlRegisters,
) -> K2geColor:
    """Resolve the out-of-window color from the OOWC index + backdrop block.

    `control.oowc` is bits 2..0 of register `0x8012` (2D Control); it
    indexes the 8-entry backdrop block at `0x83E0..0x83EF` (same block
    BGC consumes). On HW the OOWC color fills every screen pixel that
    falls outside the active window `[WBA, WBA+WSI[`.
    """
    color_base = K2GE_PALETTE_BG_COLORS_BASE + control.oowc * 2
    low = _read_byte(memory, color_base)
    high = _read_byte(memory, color_base + 1)
    return decode_color(low, high)


def resolve_backdrop_color(
    memory: dict[int, int], control: K2geControlRegisters,
) -> K2geColor:
    """Resolve the screen-wide backdrop color from BGC + backdrop palette.

    When BGC is disabled (bit 7 = 0 or bit 6 = 1), the cold-start path
    on real hardware leaves the screen black; the renderer follows
    that — returns `K2geColor(0, 0, 0)`. Otherwise the backdrop block
    at `0x83E0..0x83EF` is indexed by BGC bits 2..0 (0..7).
    """
    if not control.bgc_enabled:
        return K2geColor(raw=0, r=0, g=0, b=0)
    color_base = K2GE_PALETTE_BG_COLORS_BASE + control.bgc_index * 2
    low = _read_byte(memory, color_base)
    high = _read_byte(memory, color_base + 1)
    return decode_color(low, high)


def _scroll_offset_for_plane(
    control: K2geControlRegisters, plane: str,
) -> tuple[int, int]:
    if plane == "scr1":
        return control.s1so_h, control.s1so_v
    if plane == "scr2":
        return control.s2so_h, control.s2so_v
    raise ValueError(f"plane must be 'scr1' or 'scr2'; got {plane!r}")


def _palette_base_for_plane(plane: str) -> int:
    if plane == "scr1":
        return K2GE_PALETTE_SCR1_BASE
    if plane == "scr2":
        return K2GE_PALETTE_SCR2_BASE
    raise ValueError(f"plane must be 'scr1' or 'scr2'; got {plane!r}")


def _render_scroll_plane(
    framebuffer: list[list[K2geColor]],
    memory: dict[int, int],
    control: K2geControlRegisters,
    plane: str,
    tile_cache: dict[int, tuple[tuple[int, ...], ...]] | None = None,
) -> None:
    """Composite one K2GE scroll plane onto a mutable framebuffer.

    Iterates per screen pixel — for each (sx, sy), wraps through the
    256×256 plane via the plane's 8-bit scroll offset, decodes the
    32×32 tilemap entry, applies H.F/V.F flip, reads the 2bpp tile
    pixel value, and writes the palette-resolved color when both
    `entry.c_c != 0` (tile-0 transparent convention) and `value != 0`
    (per-pixel palette transparency).

    `tile_cache` can be shared with sibling sprite-layer calls in the
    same frame so a tile referenced by both a sprite and a tilemap
    cell is decoded once per frame; a fresh local cache is allocated
    when the caller passes `None`.
    """
    soh, sov = _scroll_offset_for_plane(control, plane)
    palette_base = _palette_base_for_plane(plane)
    palettes = read_plane_palettes(memory, palette_base, plane)
    tilemap = read_tilemap(memory, plane)
    if tile_cache is None:
        tile_cache = {}

    for sy in range(NGPC_SCREEN_HEIGHT):
        wy = (sy + sov) & (_SCR_PLANE_PIXEL_SIZE - 1)
        ty = wy >> 3
        py = wy & (_TILE_SIZE - 1)
        row_base = ty * 32
        fb_row = framebuffer[sy]
        for sx in range(NGPC_SCREEN_WIDTH):
            wx = (sx + soh) & (_SCR_PLANE_PIXEL_SIZE - 1)
            tx = wx >> 3
            entry = tilemap[row_base + tx]
            if entry.c_c == 0:
                continue  # tile-0 = transparent (NGPC convention)
            tile_pixels = tile_cache.get(entry.c_c)
            if tile_pixels is None:
                tile_pixels = read_tile(memory, entry.c_c).pixels
                tile_cache[entry.c_c] = tile_pixels
            px = wx & (_TILE_SIZE - 1)
            px_eff = (_TILE_SIZE - 1 - px) if entry.h_flip else px
            py_eff = (_TILE_SIZE - 1 - py) if entry.v_flip else py
            value = tile_pixels[py_eff][px_eff]
            if value == 0:
                continue  # palette index 0 = transparent
            fb_row[sx] = palettes[entry.cp_c].colors[value]


def resolve_sprite_positions(
    memory: dict[int, int], control: K2geControlRegisters,
) -> list[tuple[K2geSprite, int, int]]:
    """Iterate OAM, fold chain offsets + global PO.H/V offset.

    Returns a list of `(sprite, screen_x, screen_y)` tuples in OAM
    order. `screen_x` and `screen_y` are 8-bit wrapped (`0..255`) —
    callers that want to draw clip per-pixel against the
    `NGPC_SCREEN_WIDTH × NGPC_SCREEN_HEIGHT` window; sprites at high
    coordinates simply stay off-screen rather than wrapping.

    Chain semantics per `NGPC_HW_QUICKREF.md` § 5: when `H.ch` is set
    on sprite N, its `H.P` field is treated as a delta from sprite
    N-1's effective position (same for `V.ch`/`V.P`). The chain
    state advances for **every** OAM entry, including hidden
    (`PR.C == 0`) sprites, so that placing a hidden anchor at the
    head of a chain group still positions its tail correctly.

    The global sprite offset `PO.H/V` (`0x8020`/`0x8021`) is added
    last to every sprite. Cold-start values are 0, so a frame that
    never writes those registers behaves exactly like a pure OAM
    list.
    """
    sprites = read_oam_sprites(memory)
    prev_h = 0
    prev_v = 0
    positioned: list[tuple[K2geSprite, int, int]] = []
    for sprite in sprites:
        if sprite.h_chain:
            h = (prev_h + sprite.h_pos) & 0xFF
        else:
            h = sprite.h_pos
        if sprite.v_chain:
            v = (prev_v + sprite.v_pos) & 0xFF
        else:
            v = sprite.v_pos
        prev_h = h
        prev_v = v
        screen_x = (h + control.po_h) & 0xFF
        screen_y = (v + control.po_v) & 0xFF
        positioned.append((sprite, screen_x, screen_y))
    return positioned


def _render_sprite_layer(
    framebuffer: list[list[K2geColor]],
    memory: dict[int, int],
    positioned_sprites: list[tuple[K2geSprite, int, int]],
    target_pr_c: int,
    palettes: tuple,
    tile_cache: dict[int, tuple[tuple[int, ...], ...]],
) -> None:
    """Draw every positioned sprite whose `pr_c == target_pr_c`.

    Hidden sprites (`pr_c == 0`) are never drawn because no layer
    targets pr_c=0. Each sprite is one 8×8 tile resolved through
    `read_tile(memory, sprite.c_c)`; the in-tile coordinates honor
    `H.F`/`V.F` flip; palette is `palettes[sprite.cp_c]` (sprite
    palette plane base `0x8200`); per-pixel value 0 is transparent
    so the layer below shows through.
    """
    for sprite, screen_x, screen_y in positioned_sprites:
        if sprite.pr_c != target_pr_c:
            continue
        tile = tile_cache.get(sprite.c_c)
        if tile is None:
            tile = read_tile(memory, sprite.c_c).pixels
            tile_cache[sprite.c_c] = tile
        palette_colors = palettes[sprite.cp_c].colors
        for py in range(_TILE_SIZE):
            sy = screen_y + py
            if sy >= NGPC_SCREEN_HEIGHT:
                continue
            py_eff = (_TILE_SIZE - 1 - py) if sprite.v_flip else py
            row = tile[py_eff]
            fb_row = framebuffer[sy]
            for px in range(_TILE_SIZE):
                sx = screen_x + px
                if sx >= NGPC_SCREEN_WIDTH:
                    continue
                px_eff = (_TILE_SIZE - 1 - px) if sprite.h_flip else px
                value = row[px_eff]
                if value == 0:
                    continue
                fb_row[sx] = palette_colors[value]


def _apply_window_clip(
    framebuffer: list[list[K2geColor]],
    control: K2geControlRegisters,
    oowc_color: K2geColor,
) -> None:
    """Replace every pixel outside the active window with `oowc_color`.

    The window region is half-open `[WBA.H, WBA.H + WSI.H[` in X and
    `[WBA.V, WBA.V + WSI.V[` in Y. Cold-start `WSI = 0xFF` + `WBA = 0`
    yields `[0, 255[` which covers the entire 160×152 screen so this
    pass is a no-op on a fresh reset — exactly matching real silicon.

    Software that initialises a sub-window (e.g. menu region) drives
    every other pixel through this fill. The renderer does NOT enforce
    the documented `WBA + WSI ≤ 160 / 152` software constraint — if a
    game violates it, the fill simply doesn't kick in for those pixels
    (still HW-faithful).
    """
    x_min = control.wba_h
    x_max = control.wba_h + control.wsi_h
    y_min = control.wba_v
    y_max = control.wba_v + control.wsi_v
    for sy in range(NGPC_SCREEN_HEIGHT):
        if y_min <= sy < y_max:
            fb_row = framebuffer[sy]
            for sx in range(NGPC_SCREEN_WIDTH):
                if not (x_min <= sx < x_max):
                    fb_row[sx] = oowc_color
        else:
            framebuffer[sy] = [oowc_color] * NGPC_SCREEN_WIDTH


def _apply_neg_invert(framebuffer: list[list[K2geColor]]) -> None:
    """Invert every 4-bit RGB component (`c → c ^ 0x0F`) in place.

    K2GE 2D Control bit 7 (`0x8012`) flips the entire visible output —
    in-window composed pixels and the OOWC fill alike. The inversion
    runs last so the OOWC fill (from `_apply_window_clip`) is also
    inverted, matching the order in which real silicon delivers pixels
    to the LCD.
    """
    for sy in range(NGPC_SCREEN_HEIGHT):
        fb_row = framebuffer[sy]
        for sx in range(NGPC_SCREEN_WIDTH):
            c = fb_row[sx]
            inv_r = c.r ^ 0x0F
            inv_g = c.g ^ 0x0F
            inv_b = c.b ^ 0x0F
            fb_row[sx] = K2geColor(
                raw=(inv_b << 8) | (inv_g << 4) | inv_r,
                r=inv_r,
                g=inv_g,
                b=inv_b,
            )


def render_frame(memory: dict[int, int]) -> RenderedFrame:
    """Compose one NGPC frame from a merged memory view.

    Pass 1.3 final pipeline (back → front, then two post-process passes):
      1. backdrop fill (BGC-resolved color or black)
      2. sprites with PR.C = 01 (behind both scroll planes)
      3. back scroll plane (SCR2 by default, SCR1 when bit 7 of
         `0x8030` is set)
      4. sprites with PR.C = 10 (between the two scroll planes)
      5. front scroll plane (SCR1 by default, SCR2 when prio flips)
      6. sprites with PR.C = 11 (in front of everything)
      7. window clip — pixels outside `[WBA, WBA+WSI[` replaced by OOWC
         color (bits 2..0 of `0x8012` indexing the backdrop block)
      8. NEG invert — when bit 7 of `0x8012` is set, every component
         of every pixel is inverted (`c ^= 0x0F`), including OOWC fill

    Hidden sprites (`PR.C == 00`) are never drawn but still advance
    the chain state in `resolve_sprite_positions`.
    """
    control = read_control_registers(memory)
    backdrop = resolve_backdrop_color(memory, control)
    framebuffer: list[list[K2geColor]] = [
        [backdrop] * NGPC_SCREEN_WIDTH for _ in range(NGPC_SCREEN_HEIGHT)
    ]
    tile_cache: dict[int, tuple[tuple[int, ...], ...]] = {}
    sprite_palettes = read_plane_palettes(
        memory, K2GE_PALETTE_SPRITE_BASE, "sprite",
    )
    positioned_sprites = resolve_sprite_positions(memory, control)

    if control.scr2_in_front:
        back_plane, front_plane = "scr1", "scr2"
    else:
        back_plane, front_plane = "scr2", "scr1"

    _render_sprite_layer(
        framebuffer, memory, positioned_sprites, 1, sprite_palettes, tile_cache,
    )
    _render_scroll_plane(framebuffer, memory, control, back_plane, tile_cache)
    _render_sprite_layer(
        framebuffer, memory, positioned_sprites, 2, sprite_palettes, tile_cache,
    )
    _render_scroll_plane(framebuffer, memory, control, front_plane, tile_cache)
    _render_sprite_layer(
        framebuffer, memory, positioned_sprites, 3, sprite_palettes, tile_cache,
    )

    oowc_color = resolve_oowc_color(memory, control)
    _apply_window_clip(framebuffer, control, oowc_color)
    if control.neg:
        _apply_neg_invert(framebuffer)

    pixels = tuple(tuple(row) for row in framebuffer)
    return RenderedFrame(
        width=NGPC_SCREEN_WIDTH,
        height=NGPC_SCREEN_HEIGHT,
        pixels=pixels,
        control=control,
        backdrop_color=backdrop,
    )


def pixels_to_ppm_bytes(width: int, height: int, pixels) -> bytes:
    """Serialize an arbitrary `width × height` `K2geColor` grid as P6 PPM.

    Generic over the source pixel grid: consumed by both the
    `RenderedFrame` screen renderer (via `frame_to_ppm_bytes`) and the
    tile-atlas inspector in `core/atlas.py`. Each K2GE 4-bit component
    is expanded to 8 bits by nibble replication (`0x5 → 0x55`),
    matching `K2geColor.hex_rgb24()`.

    `pixels` is any iterable yielding `height` rows of `width`
    `K2geColor`-like objects (must expose `r`, `g`, `b` 4-bit fields).
    Tuples-of-tuples and lists-of-lists both work.
    """
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    body = bytearray(width * height * 3)
    cursor = 0
    for row in pixels:
        for color in row:
            body[cursor] = (color.r << 4) | color.r
            body[cursor + 1] = (color.g << 4) | color.g
            body[cursor + 2] = (color.b << 4) | color.b
            cursor += 3
    return header + bytes(body)


def frame_to_ppm_bytes(frame: RenderedFrame) -> bytes:
    """Serialize a `RenderedFrame` as binary P6 PPM (RGB888).

    Thin wrapper over `pixels_to_ppm_bytes` preserved as the public
    convenience for the screen renderer; new callers should prefer
    `pixels_to_ppm_bytes(width, height, pixels)` directly when they
    don't already have a `RenderedFrame`.
    """
    return pixels_to_ppm_bytes(frame.width, frame.height, frame.pixels)
