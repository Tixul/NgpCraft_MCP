"""The two scroll planes, rendered as whole maps -- and where the screen sits on them.

The Tiles tab shows the character RAM: 512 tiles in the order they happen to be
stored, which is not a picture of anything. This module builds the other view: the
32x32 map each plane actually draws, as one 256x256 image, plus the region of it
the screen is currently showing.

That second part is the reason this exists. The plane is CYCLICAL 256x256 and the
screen is a 160x152 window onto it whose position is re-read BY EVERY SCANLINE --
games drive parallax by rewriting the scroll register from an H-blank handler, so
the "camera" is not a rectangle at all. Drawing it per line turns line-scroll into
something you can see: a straight edge means one scroll value for the frame, a wavy
one means the game is scrolling per line, and a torn one means it meant to and got
the timing wrong.

Everything here is pure: it takes a `read(addr, n) -> bytes` callable and numpy,
so the same code serves the debug window, a script and a test. The colour rules are
the renderer's, kept deliberately in step with `cpp/src/render.cpp` -- if the two
ever disagree, this viewer is the one that is wrong, and the test file says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

# Mirrors cpp/src/render.cpp.
SCR1, SCR2 = 0, 1
MAP_BASE = (0x009000, 0x009800)
PALETTE_BASE = (0x008280, 0x008300)      # K2GE: base + code*8 + value*2
K1GE_LUT = (0x008108, 0x008110)          # compat: level look-up, per plane
K1GE_PAL = (0x0083A0, 0x0083C0)          # compat: the 12-bit colours
SCROLL_REG = ((0x32, 0x33), (0x34, 0x35))   # (H, V) inside the 0x8000 register block
CHAR_RAM = 0x00A000
CHAR_RAM_SIZE = 0x2000
TILES = CHAR_RAM_SIZE // 16
MAP_TILES = 32                            # 32x32 entries
PLANE_PX = MAP_TILES * 8                  # 256x256, cyclical
SCREEN_W, SCREEN_H = 160, 152
MODE_REGISTER = 0x0087E2

# The mono NGP's eight greys, as the retail BIOS programs them. There is no colour
# RAM on that machine to hold a ramp, so none is read (render.cpp kK1geGrey).
K1GE_GREY = (0x0FFF, 0x0DDD, 0x0BBB, 0x0999, 0x0666, 0x0444, 0x0222, 0x0000)

PLANE_NAMES = ("Plane 1 (SCR1)", "Plane 2 (SCR2)")


def _rgb_from_u16(colors: np.ndarray) -> np.ndarray:
    """12-bit BGR words -> RGB888. x*17 maps 0..15 onto 0..255 exactly."""
    r = (colors & 0x0F).astype(np.uint8) * 17
    g = ((colors >> 4) & 0x0F).astype(np.uint8) * 17
    b = ((colors >> 8) & 0x0F).astype(np.uint8) * 17
    return np.stack((r, g, b), axis=-1)


def tile_pixel_values(char_bytes: bytes) -> np.ndarray:
    """N*16 bytes of 2bpp character data -> (N, 8, 8) of pixel VALUES 0..3.

    Value 0 is transparent -- there is no "tile 0 is blank" rule on this hardware,
    transparency is per pixel (render.cpp, pass 242).
    """
    n = len(char_bytes) // 16
    if n == 0:
        return np.zeros((0, 8, 8), np.uint8)
    data = np.frombuffer(char_bytes[: n * 16], dtype=np.uint8).reshape(n, 8, 2)
    even, odd = data[:, :, 0], data[:, :, 1]
    px = np.empty((n, 8, 8), np.uint8)
    px[:, :, 0] = (odd >> 6) & 3
    px[:, :, 1] = (odd >> 4) & 3
    px[:, :, 2] = (odd >> 2) & 3
    px[:, :, 3] = odd & 3
    px[:, :, 4] = (even >> 6) & 3
    px[:, :, 5] = (even >> 4) & 3
    px[:, :, 6] = (even >> 2) & 3
    px[:, :, 7] = even & 3
    return px


@dataclass(frozen=True)
class EntryInfo:
    """One map entry, decoded. Everything you need to find it and change it."""

    tx: int
    ty: int
    addr: int              # the 2-byte entry's address in VRAM
    tile: int              # 9-bit character number
    tile_addr: int         # where those 16 bytes live in character RAM
    palette: int           # CP.C (K2GE) or P.C (compat)
    h_flip: bool
    v_flip: bool
    raw: int               # the two bytes, little-endian


@dataclass(frozen=True)
class PlaneView:
    plane: int
    rgb: np.ndarray            # (256, 256, 3) uint8
    transparent: np.ndarray    # (256, 256) bool -- pixel value 0
    tiles: np.ndarray          # (32, 32) uint16
    attribs: np.ndarray        # (32, 32) uint8
    compat: bool               # rendered down the K1GE path

    def distinct_tiles(self) -> int:
        return int(np.unique(self.tiles).size)


def is_compat(read, *, k1ge_console: bool = False) -> bool:
    """Which colour path the picture is going down.

    ⚠️ On a real mono NGP there IS no mode bit -- 0x87E2 is a K2GE register the
    machine does not have, so anything clearing the video page leaves it zero. The
    console setting decides; the register only speaks for a K2GE imitating one.
    """
    if k1ge_console:
        return True
    try:
        return bool(read(MODE_REGISTER, 1)[0] & 0x80)
    except Exception:
        return False


def _read_exact(read, addr: int, n: int) -> bytes:
    """`n` bytes, whatever the bus does. A detached window, a torn-down core or a
    stub reads short or raises; a viewer that then throws is a viewer that
    disappears exactly when something has gone wrong, which is the worst moment."""
    try:
        raw = bytes(read(addr, n))
    except Exception:
        raw = b""
    return raw[:n] + b"\x00" * max(0, n - len(raw))


def read_plane(read, plane: int, *, k1ge_console: bool = False) -> PlaneView:
    """Render a whole scroll plane to a 256x256 picture."""
    base = MAP_BASE[plane]
    raw = np.frombuffer(_read_exact(read, base, MAP_TILES * MAP_TILES * 2), np.uint8)
    raw = raw.reshape(MAP_TILES * MAP_TILES, 2)
    low, attrib = raw[:, 0], raw[:, 1]
    tiles = (low.astype(np.uint16) | ((attrib & 1).astype(np.uint16) << 8))

    px = tile_pixel_values(_read_exact(read, CHAR_RAM, CHAR_RAM_SIZE))
    if px.shape[0] < TILES:                       # short read: pad rather than crash
        px = np.concatenate([px, np.zeros((TILES - px.shape[0], 8, 8), np.uint8)])
    block = px[np.minimum(tiles, px.shape[0] - 1)].copy()   # (1024, 8, 8)

    v_flip = (attrib >> 6) & 1
    h_flip = (attrib >> 7) & 1
    block[v_flip == 1] = block[v_flip == 1][:, ::-1, :]
    block[h_flip == 1] = block[h_flip == 1][:, :, ::-1]

    compat = is_compat(read, k1ge_console=k1ge_console)
    if compat:
        code = ((attrib >> 5) & 1).astype(np.uint8)          # P.C -- ONE bit
        lut = np.frombuffer(_read_exact(read, K1GE_LUT[plane], 8), np.uint8) & 0x07
        level = lut.reshape(2, 4)[code[:, None, None], block]      # (1024, 8, 8)
        if k1ge_console:
            # A real K1GE stops at the level: it goes straight to the panel, and no
            # cartridge write can flatten the ramp because none is read.
            colors = np.asarray(K1GE_GREY, np.uint16)[level]
        else:
            pal = np.frombuffer(_read_exact(read, K1GE_PAL[plane], 32), np.uint8)
            pal = (pal[0::2].astype(np.uint16) | (pal[1::2].astype(np.uint16) << 8))
            colors = pal[code[:, None, None] * 8 + level]
    else:
        code = ((attrib >> 1) & 0x0F).astype(np.uint8)       # CP.C -- four bits
        pal = np.frombuffer(_read_exact(read, PALETTE_BASE[plane], 16 * 4 * 2), np.uint8)
        pal = (pal[0::2].astype(np.uint16) | (pal[1::2].astype(np.uint16) << 8))
        colors = pal.reshape(16, 4)[code[:, None, None], block]

    rgb = _rgb_from_u16(colors)                                   # (1024, 8, 8, 3)
    rgb = (rgb.reshape(MAP_TILES, MAP_TILES, 8, 8, 3)
              .transpose(0, 2, 1, 3, 4).reshape(PLANE_PX, PLANE_PX, 3))
    clear = (block == 0).reshape(MAP_TILES, MAP_TILES, 8, 8)
    clear = clear.transpose(0, 2, 1, 3).reshape(PLANE_PX, PLANE_PX)
    return PlaneView(plane, np.ascontiguousarray(rgb), clear,
                     tiles.reshape(MAP_TILES, MAP_TILES),
                     attrib.reshape(MAP_TILES, MAP_TILES), compat)


def entry_at(view: PlaneView, tx: int, ty: int) -> EntryInfo:
    attrib = int(view.attribs[ty, tx])
    tile = int(view.tiles[ty, tx])
    return EntryInfo(
        tx=tx, ty=ty,
        addr=MAP_BASE[view.plane] + (ty * MAP_TILES + tx) * 2,
        tile=tile,
        tile_addr=CHAR_RAM + tile * 16,
        palette=((attrib >> 5) & 1) if view.compat else ((attrib >> 1) & 0x0F),
        h_flip=bool(attrib >> 7 & 1), v_flip=bool(attrib >> 6 & 1),
        raw=(tile & 0xFF) | (attrib << 8),
    )


# ------------------------------------------------------------------ the camera
@dataclass(frozen=True)
class LineSpan:
    """Where one scanline reads from in plane space.

    `x` is the leftmost column and the span runs 160 pixels to the right, WRAPPING
    at 256 -- the plane is cyclical, so a span really can start at 200 and end at
    104.
    """

    line: int
    x: int
    y: int


def camera_spans(raster_log: "Sequence[bytes] | None", plane: int,
                 fallback: tuple[int, int] = (0, 0)) -> list[LineSpan]:
    """One span per visible scanline, from the registers THAT LINE was drawn with.

    `raster_log` is `NativeMachine.raster_log()`: 152 rows of the 0x8000 block, row
    N being what line N opened with. Without it we can only report the end-of-frame
    scroll for every line -- which is exactly the single-snapshot mistake this view
    exists to expose, so the caller is told (`from_raster_log`).
    """
    h_reg, v_reg = SCROLL_REG[plane]
    spans: list[LineSpan] = []
    for line in range(SCREEN_H):
        if raster_log is not None and line < len(raster_log):
            row = raster_log[line]
            soh, sov = row[h_reg], row[v_reg]
        else:
            soh, sov = fallback
        spans.append(LineSpan(line, int(soh) & 0xFF, (line + int(sov)) & 0xFF))
    return spans


def span_mask(spans: "Sequence[LineSpan]") -> np.ndarray:
    """(256, 256) bool: the plane pixels the screen is showing right now."""
    mask = np.zeros((PLANE_PX, PLANE_PX), bool)
    for s in spans:
        xs = (np.arange(SCREEN_W) + s.x) & (PLANE_PX - 1)
        mask[s.y, xs] = True
    return mask


# The overlay colours are DATA, not decoration, so they do not follow the theme --
# the same way the tile atlas's consumer frames do not. Amber reads on every
# background this map can produce, because the console's palette cannot make it.
EDGE_COLOUR = (255, 196, 0)
GRID_COLOUR = (90, 90, 110)
TRANSPARENT_COLOUR = (26, 26, 32)


def compose(view: PlaneView, spans: "Sequence[LineSpan] | None" = None, *,
            grid: bool = False, mark_transparent: bool = False,
            dim: float = 0.42) -> np.ndarray:
    """The picture as the tab draws it: plane, plus what is being read off it.

    The on-screen region keeps its true colours and everything else is DIMMED,
    rather than tinting the region -- a tint changes the colours you came here to
    judge, which would make the tool lie about the thing it is for.
    """
    img = view.rgb.copy()
    if mark_transparent:
        img[view.transparent] = TRANSPARENT_COLOUR
    if grid:
        img[0::8, :] = GRID_COLOUR
        img[:, 0::8] = GRID_COLOUR
    if spans:
        mask = span_mask(spans)
        outside = ~mask
        img[outside] = (img[outside].astype(np.uint16) * int(dim * 256) >> 8).astype(np.uint8)
        for s in spans:
            img[s.y, s.x] = EDGE_COLOUR
            img[s.y, (s.x + SCREEN_W - 1) & (PLANE_PX - 1)] = EDGE_COLOUR
    return img


def line_scroll_spread(spans: "Sequence[LineSpan]") -> int:
    """How far the horizontal scroll moves WITHIN one frame, in pixels.

    A non-zero answer is the signature of line-scroll (raster parallax): the game
    is rewriting the scroll register mid-frame. Measured AROUND THE CIRCLE, because
    the plane wraps -- a scroll wobbling between 254 and 2 has moved four pixels,
    not 252, and reporting 252 would invent a defect out of arithmetic.
    """
    xs = sorted({s.x for s in spans})
    if len(xs) <= 1:
        return 0
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    gaps.append(xs[0] + PLANE_PX - xs[-1])       # the gap that crosses 255 -> 0
    return PLANE_PX - max(gaps)
