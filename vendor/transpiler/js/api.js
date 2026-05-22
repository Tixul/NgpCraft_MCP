// NGPC constants, macros, and address labels exposed to user code.
// Mirrors /home/Tixu/Bureau/ngpc/01_SDK/headers/ngpc.h where possible, plus
// the higher-level identifiers from NgpCraft_base_template (GFX_SCR1, etc.).

const NGPC_API = {
  SCRN_W: 160,
  SCRN_H: 152,
  SCRN_TX: 20,
  SCRN_TY: 19,

  // Screen / VDP registers
  DISP_CTL0:      0x8000,
  WIN_X:          0x8002,
  WIN_Y:          0x8003,
  WIN_W:          0x8004,
  WIN_H:          0x8005,
  REF:            0x8006,
  RAS_H:          0x8008,
  RAS_Y:          0x8009,
  STATUS_2D:      0x8010,
  CONTROL_2D:     0x8012,
  SPR_X:          0x8020,
  SPR_Y:          0x8021,
  SCR_PRIORITY:   0x8030,
  SCRL_PRIO:      0x8030,
  SCR1_X:         0x8032,
  SCR1_Y:         0x8033,
  SCR2_X:         0x8034,
  SCR2_Y:         0x8035,
  BG_COL:         0x8118,

  // Palette regions (K2GE color mode)
  SPRITE_PALETTE:   0x8200,
  SCROLL_1_PALETTE: 0x8280,
  SCROLL_2_PALETTE: 0x8300,
  BG_PAL:           0x83E0,
  WIN_PAL:          0x83F0,

  // Sprite OAM
  SPRITE_RAM:       0x8800,
  SPRITE_COLOUR:    0x8C00,

  // Tilemaps and tiles
  SCROLL_PLANE_1:   0x9000,
  SCROLL_PLANE_2:   0x9800,
  TILE_RAM:         0xA000,

  // Plane IDs from NgpCraft template (ngpc_gfx.h)
  GFX_SCR1: 0,
  GFX_SCR2: 1,
  GFX_SPR:  2,

  // Joypad bits (ngpc.h J_* + ngpc_hw.h PAD_*).
  J_UP: 0x01, J_DOWN: 0x02, J_LEFT: 0x04, J_RIGHT: 0x08,
  J_A: 0x10, J_B: 0x20, J_OPTION: 0x40, J_POWER: 0x80,
  PAD_UP: 0x01, PAD_DOWN: 0x02, PAD_LEFT: 0x04, PAD_RIGHT: 0x08,
  PAD_A: 0x10, PAD_B: 0x20, PAD_OPTION: 0x40, PAD_POWER: 0x80,

  // Sprite flags (ngpc_hw.h §Sprite constants).
  SPR_MAX:    64,
  SPR_HFLIP:  0x80,
  SPR_VFLIP:  0x40,
  SPR_HVFLIP: 0xC0,
  SPR_HIDE:   0 << 3,
  SPR_BEHIND: 1 << 3,
  SPR_MIDDLE: 2 << 3,
  SPR_FRONT:  3 << 3,
  SPR_HCHAIN: 0x04,
  SPR_VCHAIN: 0x02,

  // 2D status bits (ngpc_hw.h §Status).
  STATUS_CHAR_OVR: 0x80,
  STATUS_VBLANK:   0x40,

  // Visible tile area (ngpc_hw.h:318-319).
  SCREEN_W:  160,
  SCREEN_H:  152,
  SCREEN_TW: 20,
  SCREEN_TH: 19,

  // Bitmap mode (ngpc_bitmap.h §dimensions).
  BMP_W:     160,
  BMP_H:     152,
  BMP_TW:    20,
  BMP_TH:    19,
  BMP_TILES: 380,

  // Macro equivalent of RGB(r,g,b) from ngpc.h — 4 bits per channel, packed 12 bits.
  RGB: (r, g, b) => ((r & 15) | ((g & 15) << 4) | ((b & 15) << 8)) & 0xFFFF,

  // C `NULL` as a plain integer 0. Used by the pointer-comparison rewrite —
  // `p == NULL` becomes `p.addr === 0`, and raw `NULL` in user code (e.g.
  // `func(NULL)`) compares against 0 as expected.
  NULL: 0,
};

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_API = NGPC_API;
