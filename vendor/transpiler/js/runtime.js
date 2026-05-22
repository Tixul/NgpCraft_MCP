// Runtime implementations of NgpCraft's high-level C APIs, in JS.
//
// Each function writes to the emulated NGPC memory the same way the real C
// function would on hardware — so user code that calls these here and later
// compiles through the real toolchain behaves identically (minus the things
// we don't emulate yet: interrupts, DMA, sound).
//
// Doc references used to implement each function are cited inline.

const NGPC_Runtime = (() => {
  const PLANE_BASE = {
    0: { map: 0x9000, pal: 0x8280 },  // GFX_SCR1
    1: { map: 0x9800, pal: 0x8300 },  // GFX_SCR2
    2: { map: null,   pal: 0x8200 },  // GFX_SPR (palette only)
  };
  const MAP_STRIDE = 32; // 32 tiles wide, entries are 2 bytes each

  // Host log sink — wired by main.js so ngpc_log_* routes into the HTML log
  // pane. Default is console.log so unit/smoke tests still see output.
  let hostLog = (msg) => console.log(msg);
  function setHostLog(fn) { hostLog = fn || ((m) => console.log(m)); }

  // ---- Pointers (C-pedagogical model backed by emulated memory) ---------
  //
  // A pointer is a Proxy over { addr, width } that responds to array indexing
  // by reading/writing the emulated NGPC memory. This lets `p[i]` work
  // natively while `*p` (transpiled to `p[0]`), `p++`, `p += N`, and
  // `PADD(p, N)` match real C semantics: the underlying address advances by
  // `width` bytes per element, so the student can watch memory change
  // exactly as it would on hardware.
  function memRead(addr, width) {
    if (width === 1) return NGPC_Memory.read8(addr & 0xFFFFFF);
    if (width === 2) return NGPC_Memory.read16(addr & 0xFFFFFF);
    return NGPC_Memory.read32(addr & 0xFFFFFF);
  }
  function memWrite(addr, width, v) {
    if (width === 1) return NGPC_Memory.write8(addr & 0xFFFFFF, v);
    if (width === 2) return NGPC_Memory.write16(addr & 0xFFFFFF, v);
    return NGPC_Memory.write32(addr & 0xFFFFFF, v);
  }

  const PTR_HANDLER = {
    get(t, prop) {
      if (prop === 'addr')   return t.addr;
      if (prop === 'width')  return t.width;
      if (prop === Symbol.toPrimitive) return () => {
        throw new Error(
          `NGPC pointer used as a number. Use p[i] for indexing, ` +
          `PADD(p, n) for a new offset pointer, or PINC(p, n) to advance p in place.`
        );
      };
      const n = Number(prop);
      if (Number.isInteger(n)) return memRead(t.addr + n * t.width, t.width);
      return undefined;
    },
    set(t, prop, value) {
      if (prop === 'addr')  { t.addr = value | 0; return true; }
      if (prop === 'width') { t.width = value | 0; return true; }
      const n = Number(prop);
      if (Number.isInteger(n)) { memWrite(t.addr + n * t.width, t.width, value); return true; }
      return false;
    },
  };

  function PTR(addr, width) {
    // Accept either a numeric address or an existing pointer (so casts like
    // `(u8*)HW_SPR_DATA` after rewritePointerCasts = `PTR(HW_SPR_DATA, 1)`
    // unwrap the inner address instead of NaN-ing).
    const a = (addr && typeof addr === 'object' && 'addr' in addr)
              ? (addr.addr | 0) : (addr | 0);
    return new Proxy({ addr: a, width: (width | 0) || 1 }, PTR_HANDLER);
  }
  // Advance pointer in place by `n` elements. Returns the same pointer so
  // `p = PINC(p, 1)` is valid even though the object is mutated.
  function PINC(p, n) { p.addr = (p.addr + n * p.width) | 0; return p; }
  // Offset by `n` elements without mutating the original — matches `p + n`.
  function PADD(p, n) { return PTR(p.addr + n * p.width, p.width); }

  // Array-pointer register macros from ngpc_hw.h — exposed as fresh PTR
  // objects per run so user code can write `HW_PAL_SCR1[i] = RGB(...);` the
  // same way the template does. Recreated each call so mutations (e.g.
  // accidental `HW_PAL_BG++`) don't leak between runs.
  function makeSystemPointers() {
    return {
      // u16 palette pointers (ngpc_hw.h §229-232)
      HW_PAL_SPR:   PTR(0x8200, 2),
      HW_PAL_SCR1:  PTR(0x8280, 2),
      HW_PAL_SCR2:  PTR(0x8300, 2),
      HW_PAL_BG:    PTR(0x83E0, 2),
      // u8 OAM + palette-index pointers
      HW_SPR_DATA:  PTR(0x8800, 1),
      HW_SPR_PAL:   PTR(0x8C00, 1),
      // u16 tilemap pointers (SCR1 / SCR2)
      HW_SCR1_MAP:  PTR(0x9000, 2),
      HW_SCR2_MAP:  PTR(0x9800, 2),
      // u16 character RAM pointer (8 words per tile)
      HW_TILE_DATA: PTR(0xA000, 2),
      // u8 Z80 RAM pointer
      HW_Z80_RAM:   PTR(0x7000, 1),
    };
  }

  // ngpc_sys_patch: on real hw, applies the power-off bug patch for prototype
  // firmware (OS_Version == 0). Safe no-op on all retail units. Emulator: no-op,
  // but we stub it so template-style bring-up compiles and runs unchanged.
  function ngpc_sys_patch() { /* no-op in emulator */ }

  // ngpc_init — mirrors src/core/ngpc_sys.c:88-160 except the ISR install block
  // (we don't emulate TLCS-900/H interrupts). Performs the register resets the
  // template does so scroll offsets, sprite offsets, LCD inversion and viewport
  // all start in a known sane state.
  function ngpc_init() {
    // Viewport full screen (template lines 129-132).
    NGPC_Memory.write8(0x8002, 0);
    NGPC_Memory.write8(0x8003, 0);
    NGPC_Memory.write8(0x8004, 160);
    NGPC_Memory.write8(0x8005, 152);
    // Scroll offsets + sprite offsets reset (template lines 136-144).
    NGPC_Memory.write8(0x8020, 0); NGPC_Memory.write8(0x8021, 0);
    NGPC_Memory.write8(0x8032, 0); NGPC_Memory.write8(0x8033, 0);
    NGPC_Memory.write8(0x8034, 0); NGPC_Memory.write8(0x8035, 0);
    // Clear HW_SCR_PRIO bit 7 — SCR1 in front, SCR2 behind (template line 142).
    const prio = NGPC_Memory.read8(0x8030);
    NGPC_Memory.write8(0x8030, prio & ~0x80);
    // Clear LCD inversion (line 145).
    const lcd = NGPC_Memory.read8(0x8012);
    NGPC_Memory.write8(0x8012, lcd & ~0x80);
    // Zero tile 0 pixel data (lines 151-155) so ngpc_gfx_clear() yields
    // transparent tiles — scroll maps cleared to tile 0 must show nothing.
    for (let i = 0; i < 16; i++) NGPC_Memory.write8(0xA000 + i, 0);
    // Seed ngpc_input repeat-timer defaults.
    NGPC_Memory.write8(0xFB11, 15);
    NGPC_Memory.write8(0xFB12, 4);
  }

  // ngpc_shutdown: BIOS-driven power-off. Emulator: halt user code by signaling
  // the host loop to stop (we throw a sentinel the driver catches cleanly).
  function ngpc_shutdown() {
    throw new Error('NGPC_SHUTDOWN');
  }

  // NGPC BIOS color-detect byte lives at 0x6F91 (0 = mono NGP, 1 = NGPC).
  // ngpc_init() writes it during boot on real hardware; the emulator does
  // the same in memory.reset() so user code that branches on mono can test
  // both paths by tweaking that byte via HW_OS_VERSION-style helpers.
  function ngpc_is_color() { return NGPC_Memory.read8(0x6F91) ? 1 : 0; }
  // Language byte at 0x6F87 (0 = EN, 1 = JP) per HW_REGISTERS.md §1.1.
  // Read-through so `ngpc_gfx_set_language()` style hacks work in-editor.
  function ngpc_get_language() { return NGPC_Memory.read8(0x6F87) & 1; }

  // ---- Hardware-fidelity guards -----------------------------------------
  // Routed through NGPC_Memory.warnOnce so the host's log sink catches
  // them (same place palette / ROM / viewport violations land). Each helper
  // returns true if the call should proceed — `false` means "silently drop
  // because the real hardware wouldn't do anything useful here either".
  function requirePlane(fn, plane) {
    if (plane === 0 || plane === 1) return true;
    NGPC_Memory.warnOnce(`plane:${fn}:${plane}`,
      `${fn}: invalid plane ${plane} (valid: 0 = SCR1, 1 = SCR2). ` +
      `Hardware would ignore — check GFX_SCR1/GFX_SCR2 passed in.`);
    return false;
  }
  function requireSpriteId(fn, id) {
    if (id >= 0 && id < 64) return true;
    NGPC_Memory.warnOnce(`oam:${fn}:${id}`,
      `${fn}: sprite id ${id} out of range (K2GE OAM has 64 slots 0..63). ` +
      `Would corrupt adjacent SCR1 palette RAM on real hardware.`);
    return false;
  }
  function requireTileId(fn, tile) {
    if (tile >= 0 && tile < 512) return true;
    NGPC_Memory.warnOnce(`tile:${fn}:${tile}`,
      `${fn}: tile index ${tile} out of range (K2GE has 512 tiles 0..511). ` +
      `Value will be masked to 9 bits — likely not the tile you wanted.`);
    return true;       // proceed with mask so visible output still draws
  }
  function requireMapXY(fn, x, y) {
    if (x >= 0 && x < 32 && y >= 0 && y < 32) return true;
    NGPC_Memory.warnOnce(`mapxy:${fn}:${x},${y}`,
      `${fn}: tile coord (${x},${y}) out of 32x32 scroll map. ` +
      `Real hw wraps the low 5 bits — probably a logic bug.`);
    return true;
  }
  function requireBmpXY(fn, x, y) {
    if (x >= 0 && x < 160 && y >= 0 && y < 152) return true;
    NGPC_Memory.warnOnce(`bmp:${fn}:${x},${y}`,
      `${fn}: pixel (${x},${y}) outside the 160x152 bitmap area. ` +
      `Real hardware would corrupt an unrelated tile.`);
    return false;
  }

  // ngpc_gfx_set_bg_color(u16 color)
  // Matches NgpCraft template ngpc_gfx.c: writes the color to HW_PAL_BG[0]
  // (0x83E0) and enables background output via HW_BG_CTL (0x8118, bit7=1).
  // Without the enable bit the real K2GE does not display the BG color.
  function ngpc_gfx_set_bg_color(color) {
    NGPC_Memory.write16(0x83E0, color);
    NGPC_Memory.write8(0x8118, 0x80);
  }

  // ngpc_gfx_set_palette(plane, pal_id, c0, c1, c2, c3)
  // Writes 4 consecutive u16 color entries at plane_palette + pal_id*8.
  // 16 palettes x 4 colors x 2 bytes = 128 bytes per plane.
  function ngpc_gfx_set_palette(plane, palId, c0, c1, c2, c3) {
    const base = PLANE_BASE[plane].pal + (palId & 0x0F) * 8;
    NGPC_Memory.write16(base + 0, c0);
    NGPC_Memory.write16(base + 2, c1);
    NGPC_Memory.write16(base + 4, c2);
    NGPC_Memory.write16(base + 6, c3);
  }

  // Build a tilemap u16 entry per TILEMAPS_SCROLL.md §1.2 / K2GE §4-4-4:
  //   bit 15     : H flip
  //   bit 14     : V flip
  //   bits 12-9  : palette (0..15)
  //   bit 8      : tile index bit 8
  //   bits 7-0   : tile index bits 7..0
  function scrEntry(tile, pal, hflip, vflip) {
    return (
      (tile & 0xFF) |
      (((tile >>> 8) & 1) << 8) |
      ((pal & 0x0F) << 9) |
      ((vflip & 1) << 14) |
      ((hflip & 1) << 15)
    ) & 0xFFFF;
  }

  // ngpc_gfx_put_tile(plane, x, y, tile, pal)
  function ngpc_gfx_put_tile(plane, x, y, tile, pal) {
    if (!requirePlane('ngpc_gfx_put_tile', plane)) return;
    requireMapXY('ngpc_gfx_put_tile', x, y);
    requireTileId('ngpc_gfx_put_tile', tile);
    const addr = PLANE_BASE[plane].map + ((y & 31) * MAP_STRIDE + (x & 31)) * 2;
    NGPC_Memory.write16(addr, scrEntry(tile, pal, 0, 0));
  }

  // ngpc_gfx_put_tile_ex(plane, x, y, tile, pal, hflip, vflip)
  function ngpc_gfx_put_tile_ex(plane, x, y, tile, pal, hflip, vflip) {
    if (!requirePlane('ngpc_gfx_put_tile_ex', plane)) return;
    requireMapXY('ngpc_gfx_put_tile_ex', x, y);
    requireTileId('ngpc_gfx_put_tile_ex', tile);
    const addr = PLANE_BASE[plane].map + ((y & 31) * MAP_STRIDE + (x & 31)) * 2;
    NGPC_Memory.write16(addr, scrEntry(tile, pal, hflip, vflip));
  }

  // ngpc_gfx_clear(plane): zero out the entire 32x32 tilemap of a plane.
  function ngpc_gfx_clear(plane) {
    if (!requirePlane('ngpc_gfx_clear', plane)) return;
    const base = PLANE_BASE[plane].map;
    for (let i = 0; i < MAP_STRIDE * MAP_STRIDE; i++) {
      NGPC_Memory.write16(base + i * 2, 0);
    }
  }

  // ngpc_gfx_fill(plane, tile, pal): fill all 32x32 tiles with one entry.
  function ngpc_gfx_fill(plane, tile, pal) {
    if (!requirePlane('ngpc_gfx_fill', plane)) return;
    requireTileId('ngpc_gfx_fill', tile);
    const base = PLANE_BASE[plane].map;
    const entry = scrEntry(tile, pal, 0, 0);
    for (let i = 0; i < MAP_STRIDE * MAP_STRIDE; i++) {
      NGPC_Memory.write16(base + i * 2, entry);
    }
  }

  // ngpc_gfx_fill_rect(plane, x, y, w, h, tile, pal): wraps at 32 per map edge.
  // Sonic disassembly §3.2 (TILEMAPS_SCROLL.md) uses wrap-safe addressing.
  function ngpc_gfx_fill_rect(plane, x, y, w, h, tile, pal) {
    if (!requirePlane('ngpc_gfx_fill_rect', plane)) return;
    requireTileId('ngpc_gfx_fill_rect', tile);
    const base = PLANE_BASE[plane].map;
    const entry = scrEntry(tile, pal, 0, 0);
    for (let dy = 0; dy < h; dy++) {
      const ty = (y + dy) & 31;
      for (let dx = 0; dx < w; dx++) {
        const tx = (x + dx) & 31;
        NGPC_Memory.write16(base + (ty * MAP_STRIDE + tx) * 2, entry);
      }
    }
  }

  // ngpc_gfx_scroll(plane, x, y)
  // Regs: SCR1_OFS 0x8032/0x8033, SCR2_OFS 0x8034/0x8035 (K2GE §4-4-8).
  function ngpc_gfx_scroll(plane, x, y) {
    if (plane === 0) {
      NGPC_Memory.write8(0x8032, x);
      NGPC_Memory.write8(0x8033, y);
    } else if (plane === 1) {
      NGPC_Memory.write8(0x8034, x);
      NGPC_Memory.write8(0x8035, y);
    }
  }

  // ngpc_gfx_scroll_parallax — port of ngpc_gfx.c:216-234 verbatim.
  // 0xFF = freeze plane at 0, otherwise apply a right-shift to cam_x/y.
  function ngpc_gfx_scroll_parallax(camX, camY, scr1Shift, scr2Shift) {
    if (scr1Shift === 0xFF) {
      NGPC_Memory.write8(0x8032, 0);
      NGPC_Memory.write8(0x8033, 0);
    } else {
      NGPC_Memory.write8(0x8032, (camX >>> scr1Shift) & 0xFF);
      NGPC_Memory.write8(0x8033, (camY >>> scr1Shift) & 0xFF);
    }
    if (scr2Shift === 0xFF) {
      NGPC_Memory.write8(0x8034, 0);
      NGPC_Memory.write8(0x8035, 0);
    } else {
      NGPC_Memory.write8(0x8034, (camX >>> scr2Shift) & 0xFF);
      NGPC_Memory.write8(0x8035, (camY >>> scr2Shift) & 0xFF);
    }
  }

  // ngpc_gfx_swap_planes — toggles HW_SCR_PRIO bit 7 (ngpc_gfx.c:236-240).
  function ngpc_gfx_swap_planes() {
    const v = NGPC_Memory.read8(0x8030);
    NGPC_Memory.write8(0x8030, v ^ 0x80);
  }

  // ngpc_gfx_set_viewport — ngpc_gfx.c:242-248.
  function ngpc_gfx_set_viewport(x, y, w, h) {
    NGPC_Memory.write8(0x8002, x);
    NGPC_Memory.write8(0x8003, y);
    NGPC_Memory.write8(0x8004, w);
    NGPC_Memory.write8(0x8005, h);
  }

  // Screen-shake offset (ngpc_gfx.c:252-260 / K2GE §4-3-4).
  function ngpc_gfx_sprite_offset(dx, dy) {
    NGPC_Memory.write8(0x8020, dx);
    NGPC_Memory.write8(0x8021, dy);
  }

  // LCD inversion toggle (ngpc_gfx.c:262-271, HW_LCD_CTL bit7).
  function ngpc_gfx_lcd_invert(enable) {
    const v = NGPC_Memory.read8(0x8012);
    NGPC_Memory.write8(0x8012, enable ? (v | 0x80) : (v & ~0x80));
  }

  // Outside-window color (ngpc_gfx.c:273-280, HW_LCD_CTL bits 2-0).
  function ngpc_gfx_set_outside_color(palIndex) {
    const v = NGPC_Memory.read8(0x8012);
    NGPC_Memory.write8(0x8012, (v & 0xF8) | (palIndex & 0x07));
  }

  // Character Over status (ngpc_gfx.c:282-289, HW_STATUS bit7).
  function ngpc_gfx_char_over() {
    return (NGPC_Memory.read8(0x8010) & 0x80) ? 1 : 0;
  }

  // Direct palette write bypassing shadow state (ngpc_gfx.c:291-295).
  function ngpc_gfx_set_color_direct(plane, palId, colorIdx, color) {
    const base = PLANE_BASE[plane].pal + palId * 8 + colorIdx * 2;
    NGPC_Memory.write16(base, color);
  }

  // Read back tile + palette at (x,y) (ngpc_gfx.c:109-116).
  // We return an object rather than C-style out-params; user code can
  // destructure: `const { tile, pal } = ngpc_gfx_get_tile(...)`.
  function ngpc_gfx_get_tile(plane, x, y) {
    const addr = PLANE_BASE[plane].map + (y * MAP_STRIDE + x) * 2;
    const entry = NGPC_Memory.read16(addr);
    return {
      tile: (entry & 0xFF) | (((entry >>> 8) & 1) << 8),
      pal:  (entry >>> 9) & 0x0F,
    };
  }

  // Repaint palette of a W×H tilemap rect without touching tile / flip bits.
  // ngpc_gfx.c:154-177. Mask 0xE1FF keeps H.F, V.F, tile bit 8, and low byte.
  function ngpc_gfx_set_rect_pal(plane, x, y, w, h, pal) {
    if (!requirePlane('ngpc_gfx_set_rect_pal', plane)) return;
    const base = PLANE_BASE[plane].map;
    const palBits = (pal & 0x0F) << 9;
    for (let row = 0; row < h; row++) {
      const yr = (y + row) & 31;
      for (let col = 0; col < w; col++) {
        const xc = (x + col) & 31;
        const a = base + (yr * MAP_STRIDE + xc) * 2;
        const entry = NGPC_Memory.read16(a);
        NGPC_Memory.write16(a, (entry & 0xE1FF) | palBits);
      }
    }
  }

  // ---- Sprites: verbatim port of src/gfx/ngpc_sprite.c -----------------
  // Every id is bounds-checked against OAM's 64-slot limit. On real hardware
  // `ngpc_sprite_set(64, …)` would silently walk past the end of sprite VRAM
  // (0x8800..0x88FF, 256 bytes = 64×4) into the sprite-palette area at
  // 0x8C00, corrupting palette indices that the VDP consults on the next
  // frame — exactly the kind of far-action bug the editor should flag.

  function ngpc_sprite_set(id, x, y, tile, pal, flags) {
    if (!requireSpriteId('ngpc_sprite_set', id)) return;
    requireTileId('ngpc_sprite_set', tile);
    const s = 0x8800 + (id << 2);
    NGPC_Memory.write8(s + 0, tile & 0xFF);
    NGPC_Memory.write8(s + 1, flags | ((tile >>> 8) & 1));
    NGPC_Memory.write8(s + 2, x);
    NGPC_Memory.write8(s + 3, y);
    NGPC_Memory.write8(0x8C00 + id, pal & 0x0F);
  }

  function ngpc_sprite_move(id, x, y) {
    if (!requireSpriteId('ngpc_sprite_move', id)) return;
    const s = 0x8800 + (id << 2);
    NGPC_Memory.write8(s + 2, x);
    NGPC_Memory.write8(s + 3, y);
  }

  function ngpc_sprite_hide(id) {
    if (!requireSpriteId('ngpc_sprite_hide', id)) return;
    const s = 0x8800 + (id << 2);
    const flags = NGPC_Memory.read8(s + 1);
    NGPC_Memory.write8(s + 1, flags & ~(3 << 3));
  }

  function ngpc_sprite_hide_all() {
    for (let i = 0; i < 64; i++) ngpc_sprite_hide(i);
  }

  function ngpc_sprite_set_flags(id, flags) {
    if (!requireSpriteId('ngpc_sprite_set_flags', id)) return;
    const s = 0x8800 + (id << 2);
    const cur = NGPC_Memory.read8(s + 1);
    NGPC_Memory.write8(s + 1, (cur & 0x01) | flags);
  }

  function ngpc_sprite_set_tile(id, tile) {
    if (!requireSpriteId('ngpc_sprite_set_tile', id)) return;
    requireTileId('ngpc_sprite_set_tile', tile);
    const s = 0x8800 + (id << 2);
    NGPC_Memory.write8(s + 0, tile & 0xFF);
    const cur = NGPC_Memory.read8(s + 1);
    NGPC_Memory.write8(s + 1, (cur & 0xFE) | ((tile >>> 8) & 1));
  }

  function ngpc_sprite_get_pal(id) {
    if (!requireSpriteId('ngpc_sprite_get_pal', id)) return 0;
    return NGPC_Memory.read8(0x8C00 + id) & 0x0F;
  }

  // ---- Metasprites (verbatim port of src/gfx/ngpc_metasprite.c) ---------
  //
  // A NgpcMetasprite reaches user code in one of two shapes depending on how
  // the author wrote the initializer:
  //   - Named struct form (designated init):
  //       { count, width, height, parts: [{ox, oy, tile, pal, flags}, ...] }
  //   - Positional array form (what tools/ngpc_sprite_export.py emits):
  //       [count, width, height, [[ox, oy, tile, pal, flags], ...]]
  // Same for MsprAnimFrame: `{frame, duration}` or `[frameRef, duration]`.
  // We normalise at the point of access so either shape works unchanged.

  function mspr_def(def) {
    if (Array.isArray(def)) {
      return { count: def[0], width: def[1], height: def[2], parts: def[3] };
    }
    return def;
  }
  function mspr_part(p) {
    if (Array.isArray(p)) {
      return { ox: p[0], oy: p[1], tile: p[2], pal: p[3], flags: p[4] };
    }
    return p;
  }
  function mspr_anim_item(item) {
    if (Array.isArray(item)) {
      return { frame: item[0], duration: item[1] };
    }
    return item;
  }

  function ngpc_mspr_draw(spr_start, x, y, def, flags) {
    const d = mspr_def(def);
    const groupH = (flags & 0x80) ? 1 : 0; // SPR_HFLIP
    const groupV = (flags & 0x40) ? 1 : 0; // SPR_VFLIP
    const priority = flags & 0x18;         // bits 4-3
    for (let i = 0; i < d.count; i++) {
      const p = mspr_part(d.parts[i]);
      // signed 16-bit arithmetic
      let px = groupH ? (x + (d.width  - 8) - p.ox) : (x + p.ox);
      let py = groupV ? (y + (d.height - 8) - p.oy) : (y + p.oy);
      if (px < -7 || px > 159 || py < -7 || py > 151) {
        ngpc_sprite_hide(spr_start + i);
        continue;
      }
      let part_flags = p.flags;
      if (groupH) part_flags ^= 0x80;
      if (groupV) part_flags ^= 0x40;
      part_flags = (part_flags & ~0x18) | priority;
      ngpc_sprite_set(spr_start + i, px & 0xFF, py & 0xFF, p.tile, p.pal, part_flags);
    }
    return d.count;
  }

  function ngpc_mspr_hide(spr_start, count) {
    for (let i = 0; i < count; i++) ngpc_sprite_hide(spr_start + i);
  }

  function ngpc_mspr_anim_start(a, anim, count, loop) {
    a.anim = anim;
    a.frame_count = count;
    a.current = 0;
    a.timer = mspr_anim_item(anim[0]).duration;
    a.loop = loop;
  }

  function ngpc_mspr_anim_update(a) {
    if (a.timer > 0) a.timer--;
    if (a.timer === 0) {
      if (a.current < a.frame_count - 1) {
        a.current++;
        a.timer = mspr_anim_item(a.anim[a.current]).duration;
      } else if (a.loop) {
        a.current = 0;
        a.timer = mspr_anim_item(a.anim[0]).duration;
      }
    }
    return mspr_anim_item(a.anim[a.current]).frame;
  }

  function ngpc_mspr_anim_done(a) {
    return (!a.loop && a.current >= a.frame_count - 1 && a.timer === 0) ? 1 : 0;
  }

  // ---- Software bitmap (verbatim port of src/gfx/ngpc_bitmap.c) -------
  //
  // Assigns 380 unique tiles (20x19) to a scroll plane, then writes pixels
  // directly into tile RAM. Template comment: pixel at column c in row r
  // lives at bits (15-c*2) and (14-c*2) of tile[r] (u16-view, little-endian
  // matches byte-level format K2GETechRef §4-3-5-1).

  const BMP = { W: 160, H: 152, TW: 20, TH: 19, TILES: 380 };
  // s_tile_offset lives at scratch 0xFC40 so it survives across ngpc_bmp_*
  // calls without JS-side state that would desync on module reloads.
  const BMP_OFFSET_ADDR = 0xFC40;

  function bmp_pixel_addr_u16(x, y) {
    const offset = NGPC_Memory.read16(BMP_OFFSET_ADDR);
    const tx = x >>> 3, ty = y >>> 3;
    const tileId = offset + ty * BMP.TW + tx;
    const row = y & 7;
    // u16 address = tile RAM base + tileId*16 + row*2
    return 0xA000 + tileId * 16 + row * 2;
  }
  function bmp_pxshift(col) { return 14 - (col & 7) * 2; }
  function bmp_pxmask(col) { return 0x03 << bmp_pxshift(col); }

  function ngpc_bmp_init(plane, tileOffset, pal) {
    NGPC_Memory.write16(BMP_OFFSET_ADDR, tileOffset & 0xFFFF);
    // Fill the scroll plane map with consecutive tile indices.
    const mapBase = PLANE_BASE[plane].map;
    for (let ty = 0; ty < BMP.TH; ty++) {
      for (let tx = 0; tx < BMP.TW; tx++) {
        const tileId = tileOffset + ty * BMP.TW + tx;
        const entry = scrEntry(tileId, pal, 0, 0);
        NGPC_Memory.write16(mapBase + (ty * MAP_STRIDE + tx) * 2, entry);
      }
    }
    // Clear all bitmap tile bytes (380 * 16 bytes = 6080).
    const base = 0xA000 + tileOffset * 16;
    for (let i = 0; i < BMP.TILES * 16; i++) {
      NGPC_Memory.write8(base + i, 0);
    }
  }

  function ngpc_bmp_pixel(x, y, color) {
    if (!requireBmpXY('ngpc_bmp_pixel', x, y)) return;
    const addr = bmp_pixel_addr_u16(x, y);
    const shift = bmp_pxshift(x);
    const mask  = bmp_pxmask(x);
    const word = NGPC_Memory.read16(addr);
    NGPC_Memory.write16(addr, (word & ~mask) | ((color & 3) << shift));
  }

  function ngpc_bmp_get_pixel(x, y) {
    if (!requireBmpXY('ngpc_bmp_get_pixel', x, y)) return 0;
    const word = NGPC_Memory.read16(bmp_pixel_addr_u16(x, y));
    return (word >>> bmp_pxshift(x)) & 3;
  }

  function ngpc_bmp_clear() {
    const offset = NGPC_Memory.read16(BMP_OFFSET_ADDR);
    const base = 0xA000 + offset * 16;
    for (let i = 0; i < BMP.TILES * 16; i++) {
      NGPC_Memory.write8(base + i, 0);
    }
  }

  function ngpc_bmp_hline(x, y, w, color) {
    // Flag the call if the *intended* span exits the screen — still clip to
    // the real bitmap so the student sees partial output instead of silence.
    if (y < 0 || y >= BMP.H || x < 0 || x + w > BMP.W) {
      NGPC_Memory.warnOnce(`bmp:hline:${x},${y},${w}`,
        `ngpc_bmp_hline: span (${x},${y}..${x+w}) leaves the 160x152 area. ` +
        `Clipping; real hardware would corrupt adjacent tile bytes.`);
    }
    if (y < 0 || y >= BMP.H) return;
    let i = Math.max(0, x);
    const end = Math.min(BMP.W, x + w);
    for (; i < end; i++) ngpc_bmp_pixel(i, y, color);
  }

  function ngpc_bmp_vline(x, y, h, color) {
    if (x < 0 || x >= BMP.W || y < 0 || y + h > BMP.H) {
      NGPC_Memory.warnOnce(`bmp:vline:${x},${y},${h}`,
        `ngpc_bmp_vline: span (${x},${y}..${y+h}) leaves the 160x152 area. ` +
        `Clipping; real hardware would corrupt adjacent tile bytes.`);
    }
    if (x < 0 || x >= BMP.W) return;
    let j = Math.max(0, y);
    const end = Math.min(BMP.H, y + h);
    for (; j < end; j++) ngpc_bmp_pixel(x, j, color);
  }

  function ngpc_bmp_line(x1, y1, x2, y2, color) {
    // Bresenham — verbatim port of template lines 115-145.
    let dx = x2 - x1; if (dx < 0) dx = -dx;
    let dy = y2 - y1; if (dy < 0) dy = -dy;
    dy = -dy;
    const sx = (x1 < x2) ? 1 : -1;
    const sy = (y1 < y2) ? 1 : -1;
    let err = dx + dy;
    while (true) {
      ngpc_bmp_pixel(x1, y1, color);
      if (x1 === x2 && y1 === y2) break;
      const e2 = err * 2;
      if (e2 >= dy) { err += dy; x1 += sx; }
      if (e2 <= dx) { err += dx; y1 += sy; }
    }
  }

  function ngpc_bmp_rect(x, y, w, h, color) {
    if (w === 0 || h === 0) return;
    if (x < 0 || y < 0 || x + w > BMP.W || y + h > BMP.H) {
      NGPC_Memory.warnOnce(`bmp:rect:${x},${y},${w},${h}`,
        `ngpc_bmp_rect: (${x},${y},${w}x${h}) exits the 160x152 area. ` +
        `Clipping; real hw would touch tiles outside the bitmap area.`);
    }
    ngpc_bmp_hline(x, y, w, color);
    ngpc_bmp_hline(x, y + h - 1, w, color);
    if (h > 2) {
      ngpc_bmp_vline(x, y + 1, h - 2, color);
      ngpc_bmp_vline(x + w - 1, y + 1, h - 2, color);
    }
  }

  function ngpc_bmp_fill_rect(x, y, w, h, color) {
    if (x < 0 || y < 0 || x + w > BMP.W || y + h > BMP.H) {
      NGPC_Memory.warnOnce(`bmp:fill_rect:${x},${y},${w},${h}`,
        `ngpc_bmp_fill_rect: (${x},${y},${w}x${h}) exits the 160x152 area.`);
    }
    let j = Math.max(0, y);
    const end = Math.min(BMP.H, y + h);
    for (; j < end; j++) ngpc_bmp_hline(x, j, w, color);
  }

  // ---- Log & assert ----------------------------------------------------
  //
  // Emulator divergence (documented): the template's ngpc_log_* dumps entries
  // onto a scroll plane because NGPC has no console. We have a real HTML log
  // pane visible at all times, so routes the same API there instead — the
  // function contracts stay the same from the user's POV (label + value/str).
  //
  // `ngpc_log_dump` is a no-op in the emulator since messages are already
  // visible as they're logged.

  function fmtHex4(v) { return '0x' + ((v & 0xFFFF).toString(16).padStart(4, '0').toUpperCase()); }

  function ngpc_log_init()  { hostLog('[log init]'); }
  function ngpc_log_clear() { hostLog('[log clear]'); }
  function ngpc_log_hex(label, value) { hostLog(`${label || ''}  ${fmtHex4(value)}`); }
  function ngpc_log_str(label, str)   { hostLog(`${label || ''}  ${str || ''}`); }
  function ngpc_log_dump(/* plane, pal, x, y */) { /* no-op in emulator */ }
  function ngpc_log_count() { return 0; }

  function ngpc_assert_fail(file, line) {
    throw new Error(`ASSERT failed at ${file || '?'}:${line | 0}`);
  }

  // ngpc_gfx_load_tiles(tiles, count)
  // tiles: array of u16 words (8 words per tile).
  // count: number of u16 words to copy (= num_tiles * 8).
  // Writes consecutively into character RAM starting at tile 0.
  function ngpc_gfx_load_tiles(tiles, count) {
    ngpc_gfx_load_tiles_at(tiles, count, 0);
  }

  // ngpc_gfx_load_tiles_at(tiles, count, offset)
  // offset: destination tile index (0-511).
  function ngpc_gfx_load_tiles_at(tiles, count, offset) {
    const base = 0xA000 + (offset & 0x1FF) * 16;
    for (let i = 0; i < count; i++) {
      NGPC_Memory.write16(base + i * 2, tiles[i] & 0xFFFF);
    }
  }

  // ngpc_gfx_load_tiles_u8(tiles, tile_count): bytes instead of u16 words.
  // Each tile = 16 bytes (NGPC native layout, K2GETechRef §4-3-5-1).
  function ngpc_gfx_load_tiles_u8(tiles, tileCount) {
    ngpc_gfx_load_tiles_u8_at(tiles, tileCount, 0);
  }
  function ngpc_gfx_load_tiles_u8_at(tiles, tileCount, offset) {
    const base = 0xA000 + (offset & 0x1FF) * 16;
    for (let i = 0; i < tileCount * 16; i++) {
      NGPC_Memory.write8(base + i, tiles[i] & 0xFF);
    }
  }

  // ngpc_load_sysfont: mimic the BIOS SYSFONTSET call. Writes the 96 printable
  // ASCII glyphs into tile RAM with tile index = ASCII code, matching the
  // convention `ngpc_text_print` uses (template ngpc_text.c).
  function ngpc_load_sysfont() {
    const glyphs = NGPC_Font.get();
    for (const [ch, bytes] of glyphs) {
      const base = 0xA000 + ch * 16;
      for (let i = 0; i < 16; i++) {
        NGPC_Memory.write8(base + i, bytes[i]);
      }
    }
  }

  // ngpc_text_print: verbatim port of NgpCraft ngpc_text.c.
  // Writes ASCII codes directly as tile indices into the selected plane.
  function ngpc_text_print(plane, pal, x, y, str) {
    let cx = x;
    for (let i = 0; i < str.length; i++) {
      const ch = str.charCodeAt(i);
      if (ch >= 0x20 && ch < 0x80) {
        ngpc_gfx_put_tile(plane, cx, y, ch, pal);
      }
      cx++;
      if (cx >= 20) break; // SCREEN_TW = 20 tiles visible
    }
  }

  function ngpc_text_print_dec(plane, pal, x, y, value, digits) {
    const buf = new Array(5);
    let v = value & 0xFFFF;
    for (let i = 0; i < 5; i++) { buf[4 - i] = v % 10; v = Math.floor(v / 10); }
    for (let i = 5 - digits; i < 5; i++) {
      ngpc_gfx_put_tile(plane, x, y, buf[i] + 0x30, pal);
      x++;
    }
  }

  function ngpc_text_print_hex(plane, pal, x, y, value, digits) {
    for (let i = 0; i < digits; i++) {
      const shift = (digits - 1 - i) * 4;
      const nibble = (value >>> shift) & 0xF;
      const tile = nibble < 10 ? (nibble + 0x30) : (nibble - 10 + 0x41);
      ngpc_gfx_put_tile(plane, x + i, y, tile, pal);
    }
  }

  // Space-padded decimal (ngpc_text.c:79-104). Leading zeros become spaces.
  function ngpc_text_print_num(plane, pal, x, y, value, digits) {
    const buf = new Array(5);
    let v = value & 0xFFFF;
    for (let i = 0; i < 5; i++) { buf[4 - i] = v % 10; v = Math.floor(v / 10); }
    let leading = 1;
    for (let i = 5 - digits; i < 5; i++) {
      let tile;
      if (leading && buf[i] === 0 && i < 4) tile = 0x20; // space
      else { leading = 0; tile = buf[i] + 0x30; }
      ngpc_gfx_put_tile(plane, x, y, tile, pal);
      x++;
    }
  }

  // 32-bit hex = high 16 bits + low 16 bits (ngpc_text.c:106-111).
  function ngpc_text_print_hex32(plane, pal, x, y, value) {
    ngpc_text_print_hex(plane, pal, x,     y, (value >>> 16) & 0xFFFF, 4);
    ngpc_text_print_hex(plane, pal, x + 4, y,  value         & 0xFFFF, 4);
  }

  // Write a full visible-screen array of tile indices (ngpc_text.c:113-125).
  function ngpc_text_tile_screen(plane, pal, map) {
    let idx = 0;
    for (let ty = 0; ty < 19; ty++) {
      for (let tx = 0; tx < 20; tx++) {
        ngpc_gfx_put_tile(plane, tx, ty, map[idx++], pal);
      }
    }
  }

  // ---- Input ------------------------------------------------------------
  //
  // Verbatim port of NgpCraft src/core/ngpc_input.c. HW_JOYPAD at 0x6F82 is
  // kept current by the keyboard listener in main.js; `ngpc_input_update`
  // consumes it, computes edges vs the previous frame, and runs the same
  // per-bit repeat timer the template uses.
  //
  // Scratch-region virtual addresses (must match interpreter.js EXTERNS):
  //   0xFB00       g_vb_counter
  //   0xFB01       ngpc_pad_held
  //   0xFB02       ngpc_pad_pressed
  //   0xFB03       ngpc_pad_released
  //   0xFB04       ngpc_pad_repeat
  //   0xFB10       internal: s_pad_prev (held state from previous frame)
  //   0xFB11       internal: s_repeat_delay (default 15, per template)
  //   0xFB12       internal: s_repeat_rate  (default 4,  per template)
  //   0xFB13..1A   internal: s_repeat_timer[8]
  function ngpc_input_update() {
    const raw       = NGPC_Memory.read8(0x6F82);
    const prev      = NGPC_Memory.read8(0xFB10);
    const pressed   = raw & ~prev & 0xFF;
    const released  = ~raw & prev & 0xFF;
    const delay     = NGPC_Memory.read8(0xFB11);
    const rate      = NGPC_Memory.read8(0xFB12);
    let   repeat    = 0;

    for (let i = 0; i < 8; i++) {
      const mask = 1 << i;
      const tAddr = 0xFB13 + i;
      if (raw & mask) {
        if (pressed & mask) {
          NGPC_Memory.write8(tAddr, delay);
        } else {
          const t = NGPC_Memory.read8(tAddr);
          if (t > 0) {
            NGPC_Memory.write8(tAddr, t - 1);
          } else {
            repeat |= mask;
            NGPC_Memory.write8(tAddr, rate);
          }
        }
      } else {
        NGPC_Memory.write8(tAddr, 0);
      }
    }

    NGPC_Memory.write8(0xFB01, raw);
    NGPC_Memory.write8(0xFB02, pressed);
    NGPC_Memory.write8(0xFB03, released);
    NGPC_Memory.write8(0xFB04, repeat);
    NGPC_Memory.write8(0xFB10, raw);
  }

  function ngpc_input_set_repeat(delay, rate) {
    NGPC_Memory.write8(0xFB11, delay & 0xFF);
    NGPC_Memory.write8(0xFB12, rate & 0xFF);
  }

  // ---- Math (verbatim port of src/core/ngpc_math.c) ---------------------
  //
  // Sin table: 256 entries, s8 range -127..+127, period = 256 (so 64 = 90°).
  // Table copied byte-for-byte from template lines 17-50.
  const SIN_TABLE = new Int8Array([
      0,   3,   6,   9,  12,  16,  19,  22,
     25,  28,  31,  34,  37,  40,  43,  46,
     49,  51,  54,  57,  60,  63,  65,  68,
     71,  73,  76,  78,  81,  83,  85,  88,
     90,  92,  94,  96,  98, 100, 102, 104,
    106, 107, 109, 111, 112, 113, 115, 116,
    117, 118, 120, 121, 122, 122, 123, 124,
    125, 125, 126, 126, 126, 127, 127, 127,
    127, 127, 127, 127, 126, 126, 126, 125,
    125, 124, 123, 122, 122, 121, 120, 118,
    117, 116, 115, 113, 112, 111, 109, 107,
    106, 104, 102, 100,  98,  96,  94,  92,
     90,  88,  85,  83,  81,  78,  76,  73,
     71,  68,  65,  63,  60,  57,  54,  51,
     49,  46,  43,  40,  37,  34,  31,  28,
     25,  22,  19,  16,  12,   9,   6,   3,
      0,  -3,  -6,  -9, -12, -16, -19, -22,
    -25, -28, -31, -34, -37, -40, -43, -46,
    -49, -51, -54, -57, -60, -63, -65, -68,
    -71, -73, -76, -78, -81, -83, -85, -88,
    -90, -92, -94, -96, -98,-100,-102,-104,
   -106,-107,-109,-111,-112,-113,-115,-116,
   -117,-118,-120,-121,-122,-122,-123,-124,
   -125,-125,-126,-126,-126,-127,-127,-127,
   -127,-127,-127,-127,-126,-126,-126,-125,
   -125,-124,-123,-122,-122,-121,-120,-118,
   -117,-116,-115,-113,-112,-111,-109,-107,
   -106,-104,-102,-100, -98, -96, -94, -92,
    -90, -88, -85, -83, -81, -78, -76, -73,
    -71, -68, -65, -63, -60, -57, -54, -51,
    -49, -46, -43, -40, -37, -34, -31, -28,
    -25, -22, -19, -16, -12,  -9,  -6,  -3,
  ]);

  function ngpc_sin(angle) { return SIN_TABLE[angle & 0xFF]; }
  function ngpc_cos(angle) { return SIN_TABLE[(angle + 64) & 0xFF]; }

  // 32-bit LCG state kept in scratch memory so re-runs reset alongside
  // emulated RAM. 0xFB20..0xFB23 = s_rng_state (u32 little-endian).
  function rng_read()  { return NGPC_Memory.read32(0xFB20); }
  function rng_write(v) { NGPC_Memory.write32(0xFB20, v >>> 0); }

  function ngpc_rng_seed() {
    const vb = NGPC_Memory.read8(0xFB00);
    let v = ((vb * 1103515245) >>> 0) + 12345;
    v = v >>> 0;
    if (v === 0) v = 1;
    rng_write(v);
  }

  function ngpc_random(max) {
    let s = rng_read();
    if (s === 0) s = 1;
    // Numerical Recipes LCG constants; match template line 81.
    s = (Math.imul(s, 1103515245) + 12345) >>> 0;
    rng_write(s);
    const result = (s >>> 16) & 0x7FFF;
    if (max === 0) return 0;
    return result % ((max + 1) >>> 0);
  }

  // QRandom: Fisher-Yates shuffle of 256 bytes, then sequential reads
  // (ngpc_math.c:101-124). State lives at 0xFB30..0xFC2F (table) and
  // 0xFC30 (index).
  function ngpc_qrandom_init() {
    for (let i = 0; i < 256; i++) NGPC_Memory.write8(0xFB30 + i, i);
    for (let i = 255; i > 0; i--) {
      const j = ngpc_random(i);
      const tmp = NGPC_Memory.read8(0xFB30 + i);
      NGPC_Memory.write8(0xFB30 + i, NGPC_Memory.read8(0xFB30 + j));
      NGPC_Memory.write8(0xFB30 + j, tmp);
    }
    NGPC_Memory.write8(0xFC30, 0);
  }
  function ngpc_qrandom() {
    const idx = NGPC_Memory.read8(0xFC30);
    const val = NGPC_Memory.read8(0xFB30 + idx);
    NGPC_Memory.write8(0xFC30, (idx + 1) & 0xFF);
    return val;
  }

  // 32x32 multiply — template uses explicit 16x16 partial products to avoid
  // the missing mul32 on T900. In JS we have Math.imul, same result.
  function ngpc_mul32(a, b) { return Math.imul(a | 0, b | 0); }

  // ---- Timing (partial port of src/core/ngpc_timing.c) -----------------
  //
  // ngpc_vsync itself is handled by the interpreter rewrite (-> yield).
  // The other helpers are safe to expose as JS stubs:

  // HW_STATUS bit 6 (BLNK) per K2GETechRef §4-10: reads 1 during the V-blank
  // window, cleared at the end of VBlank. The host sets and clears this
  // around the simulated VBI fire so polling from user code reflects the
  // real state — useful for code that uses `if (HW_STATUS & STATUS_VBLANK)`
  // busy-waits instead of the ngpc_vsync ISR path.
  function ngpc_in_vblank() { return (NGPC_Memory.read8(0x8010) & 0x40) ? 1 : 0; }

  // BIOS clock-gear isn't modelled; we keep the signature for API parity
  // so template code that tweaks CPU speed compiles unchanged.
  function ngpc_cpu_speed(/* divider */) { /* no-op */ }

  // Simple helpers that don't need vsync: `memcpy` and `memset` for the
  // emulated address space. Template uses them for RAM<->RAM copies. The
  // plain C-stdlib names are aliased so user code using `memcpy(dst, src, n)`
  // compiles without an extra include.
  function ngpc_memcpy(dst, src, len) {
    for (let i = 0; i < len; i++) {
      NGPC_Memory.write8(dst + i, NGPC_Memory.read8(src + i));
    }
  }
  function ngpc_memset(dst, val, len) {
    for (let i = 0; i < len; i++) NGPC_Memory.write8(dst + i, val & 0xFF);
  }
  const memcpy = ngpc_memcpy;
  const memset = ngpc_memset;

  // ---- Sprite shadow buffer (ngpc_sprite.c:35-47) ----------------------
  //
  // Real hardware path: `ngpc_sprite_set` writes to a RAM shadow while
  // `s_spr_frame_busy` is 1, then the VBI flushes dirty range → OAM. This
  // avoids the classic "torn sprite during active display" glitch (K2GE
  // reads OAM asynchronously during rendering). Our emulator writes
  // straight to the OAM region so we never see tearing — the frame_begin
  // / frame_end pair is a no-op here but kept for source compatibility
  // with NgpCraft_base_template/src/gfx/ngpc_sprite.c and StarGunner.
  function ngpc_sprite_frame_begin() { /* no-op: emulator has no OAM race */ }
  function ngpc_sprite_frame_end()   { /* no-op */ }
  function ngpc_sprite_flush()       { /* no-op */ }

  // ---- VRAMQ (ngpc_vramq.h — deferred VRAM writes) ----------------------
  //
  // Real purpose: queue u16 writes during active display, drain them in
  // VBlank so the K2GE line buffer never sees CPU VRAM contention (cause
  // of the Character Over glitch). In the emulator there's no contention,
  // so `copy` and `fill` apply immediately and the queue stays empty; the
  // `pending` / `dropped` counters and `flush` / `clear` keep API parity
  // with StarGunner-style code that calls these every frame.
  const VRAMQ_MAX_CMDS = 16;
  let s_vramq_dropped = 0;
  function _vramqValidDst(dst) {
    const a = (typeof dst === 'object' && dst !== null && 'addr' in dst) ? dst.addr : dst;
    return a >= 0x8000 && a <= 0xBFFF;
  }
  function _vramqAddr(dst) {
    return (typeof dst === 'object' && dst !== null && 'addr' in dst) ? dst.addr : dst;
  }
  function ngpc_vramq_init() { s_vramq_dropped = 0; }
  function ngpc_vramq_copy(dst, src, lenWords) {
    if (!_vramqValidDst(dst) || lenWords === 0) {
      NGPC_Memory.warnOnce('vramq:copy:oob',
        `ngpc_vramq_copy: dst must be 0x8000..0xBFFF (VRAM range, ngpc_vramq.h).`);
      s_vramq_dropped = (s_vramq_dropped + 1) & 0xFF;
      return 0;
    }
    const dstAddr = _vramqAddr(dst);
    const srcAddr = _vramqAddr(src);
    for (let i = 0; i < lenWords; i++) {
      NGPC_Memory.write16(dstAddr + i * 2, NGPC_Memory.read16(srcAddr + i * 2));
    }
    return 1;
  }
  function ngpc_vramq_fill(dst, value, lenWords) {
    if (!_vramqValidDst(dst) || lenWords === 0) {
      NGPC_Memory.warnOnce('vramq:fill:oob',
        `ngpc_vramq_fill: dst must be 0x8000..0xBFFF (VRAM range, ngpc_vramq.h).`);
      s_vramq_dropped = (s_vramq_dropped + 1) & 0xFF;
      return 0;
    }
    const dstAddr = _vramqAddr(dst);
    for (let i = 0; i < lenWords; i++) {
      NGPC_Memory.write16(dstAddr + i * 2, value & 0xFFFF);
    }
    return 1;
  }
  function ngpc_vramq_flush()      { /* immediate apply — nothing to drain */ }
  function ngpc_vramq_clear()      { /* queue is always empty */ }
  function ngpc_vramq_pending()    { return 0; }
  function ngpc_vramq_dropped()    { return s_vramq_dropped; }
  function ngpc_vramq_clear_dropped() { s_vramq_dropped = 0; }

  // ---- Sound API (sounds.h) --------------------------------------------
  //
  // Real hardware: T6W28 PSG (3 tones + 1 noise), driven by the Z80
  // coprocessor. The live editor emulates the PSG directly via WebAudio
  // (js/psg.js = NGPC_PSG) and hooks the Sfx_* / Bgm_* high-level API
  // straight into setTone / setAttn / setNoise. The Z80 is NOT emulated —
  // we intercept above the byte protocol.
  //
  // SFX engine — mirrors the template driver's per-frame modulation:
  //   - Pitch sweep: divider walks `sw_step * sw_dir` every `sw_speed` frames
  //     toward `sw_end`. If `sw_ping`, bounce between base and end; else
  //     stop when reaching the endpoint.
  //   - Envelope: attenuation increments by `env_step` every `env_spd`
  //     frames (0 = loud, 15 = silent → natural decay).
  //   - Frame timer: when it hits zero, voice is silenced.
  // State blocks are allocated per channel (0..2 = tones, 3 = noise).
  const psg = () => (typeof NGPC_PSG !== 'undefined') ? NGPC_PSG : null;
  const sfxTone = [0, 1, 2].map(() => ({
    timer: 0, attn: 15, div: 1023,
    sw_on: 0, sw_step: 0, sw_speed: 1, sw_counter: 0,
    sw_end: 0, sw_base: 0, sw_ping: 0, sw_dir: 1,
    env_on: 0, env_step: 0, env_spd: 1, env_counter: 0,
  }));
  const sfxNoise = {
    timer: 0, attn: 15,
    env_on: 0, env_step: 0, env_spd: 1, env_counter: 0,
    burst: 0, burst_dur: 0, burst_counter: 0, burst_ctrl: 0,
  };
  const sfxTimer = [0, 0, 0, 0];  // compat view for Sounds_ResetState etc.

  function Sounds_Init() {
    hostLog('[audio] Sounds_Init');
    const p = psg();
    if (p) p.init();
  }
  function Sounds_ResetState() {
    for (const s of sfxTone) { s.timer = 0; s.sw_on = 0; s.env_on = 0; s.attn = 15; }
    sfxNoise.timer = 0; sfxNoise.env_on = 0; sfxNoise.attn = 15;
    for (let i = 0; i < 4; i++) sfxTimer[i] = 0;
    const p = psg();
    if (p) p.reset();
  }
  function Sounds_Update() {
    // Sfx timers tick down first (matches template order: SFX then BGM).
    Sfx_Update();
    Bgm_Update();
  }
  function Sounds_DebugFault()   { return 0; }
  function Sounds_DebugDrops()   { return 0; }
  function Sounds_DebugLastSfx() { return 0; }
  function _sfxToneTick(ch) {
    const s = sfxTone[ch];
    if (s.timer <= 0) return;
    const p = psg();
    let dirty = false;
    // Pitch sweep — walks `div` toward `sw_end` by `sw_step` per `sw_speed`.
    if (s.sw_on) {
      if (s.sw_counter <= 0) {
        let v = s.div + s.sw_step * s.sw_dir;
        if (s.sw_ping) {
          const lo = Math.min(s.sw_base, s.sw_end);
          const hi = Math.max(s.sw_base, s.sw_end);
          if      (v <= lo) { v = lo; s.sw_dir = +1; }
          else if (v >= hi) { v = hi; s.sw_dir = -1; }
        } else {
          if (s.sw_dir < 0 && v <= s.sw_end) { v = s.sw_end; s.sw_on = 0; }
          else if (s.sw_dir > 0 && v >= s.sw_end) { v = s.sw_end; s.sw_on = 0; }
        }
        if (v < 1)    v = 1;
        if (v > 1023) v = 1023;
        s.div = v;
        s.sw_counter = s.sw_speed;
        dirty = true;
      } else {
        s.sw_counter--;
      }
    }
    // Attenuation envelope — increments attn toward 15 (silence) per env_spd.
    if (s.env_on) {
      if (s.env_counter <= 0) {
        if (s.attn < 15) {
          s.attn = Math.min(15, s.attn + s.env_step);
          dirty = true;
        }
        s.env_counter = s.env_spd;
      } else {
        s.env_counter--;
      }
    }
    if (dirty && p) { p.setTone(ch, s.div); p.setAttn(ch, s.attn); }
    s.timer--;
    if (s.timer === 0 && p) { p.setAttn(ch, 15); s.sw_on = 0; s.env_on = 0; }
  }
  function _sfxNoiseTick() {
    const s = sfxNoise;
    if (s.timer <= 0) return;
    const p = psg();
    let dirty = false;
    if (s.env_on) {
      if (s.env_counter <= 0) {
        if (s.attn < 15) {
          s.attn = Math.min(15, s.attn + s.env_step);
          dirty = true;
        }
        s.env_counter = s.env_spd;
      } else {
        s.env_counter--;
      }
    }
    if (dirty && p) p.setAttn(3, s.attn);
    s.timer--;
    if (s.timer === 0 && p) { p.setAttn(3, 15); s.env_on = 0; }
  }
  function Sfx_Update() {
    _sfxToneTick(0);
    _sfxToneTick(1);
    _sfxToneTick(2);
    _sfxNoiseTick();
    // compat mirror for any external caller reading sfxTimer[] directly
    for (let ch = 0; ch < 3; ch++) sfxTimer[ch] = sfxTone[ch].timer;
    sfxTimer[3] = sfxNoise.timer;
  }
  // Default `Sfx_Play(id)` dispatcher — audible fallback when the user's
  // project does NOT define its own implementation (§5.2 AUDIO.md reference
  // impl). Each id maps to a distinct arcade-style preset built from
  // Sfx_PlayToneEx / Sfx_PlayNoiseEx (pitch sweep + attn envelope), so the
  // dev hears a sound that at least suggests the event type (shoot / hit /
  // explode / pickup / player-hit). A project that imports the real
  // `PROJECT_SFX_*` tables + writes its own `Sfx_Play` will shadow this
  // automatically (user-scope function wins over runtime-spread env).
  //
  // Preset cycle (id mod 6):
  //   0 shoot      — high→low pitch sweep on ch0, short attn decay
  //   1 hit        — white noise burst, fast attn decay
  //   2 explode    — long white noise with slower envelope
  //   3 pickup     — low→high up-sweep on ch1, medium decay (arcade "powerup")
  //   4 player-hit — low ping-pong sweep on ch2, long duration
  //   5 clear      — two-tone arpeggio (start ch0 now + ch1 3 frames later —
  //                  frame 2 is the closest we get without a scheduler)
  function Sfx_Play(id) {
    const n = id & 0xFF;
    hostLog(`[sfx] play id=${n}`);
    const preset = n % 6;
    switch (preset) {
      case 0: // shoot — laser
        Sfx_PlayToneEx(0, 140, 0, 18,
                       600, 25, 1, 0, 1,   // sw_end=600, step 25 down, speed 1, no ping, on
                       1, 1, 2);           // env on, +1 attn per 2 frames
        break;
      case 1: // hit
        Sfx_PlayNoiseEx(0, 1, 4, 8,        // rate 0, white, attn 4, 8 frames
                        0, 0,
                        1, 2, 1);          // env on, +2 attn per frame
        break;
      case 2: // explode
        Sfx_PlayNoiseEx(1, 1, 2, 28,       // rate 1, white, louder, longer
                        0, 0,
                        1, 1, 2);          // env on, slower decay
        break;
      case 3: // pickup / clear — up-sweep
        Sfx_PlayToneEx(1, 400, 0, 16,
                       180, -18, 1, 0, 1,  // sw_end=180, step -18 up, on
                       1, 1, 3);
        break;
      case 4: // player-hit — low ping-pong
        Sfx_PlayToneEx(2, 500, 3, 24,
                       300, 15, 2, 1, 1,   // ping-pong on, slow
                       1, 1, 4);
        break;
      case 5: // arpeggio open-note
        Sfx_PlayToneEx(0, 291, 0, 10,      // ~E4
                       218, -10, 1, 0, 1,  // up-sweep to ~A4
                       1, 1, 3);
        break;
    }
  }
  function Sfx_PlayPreset(_preset)     { hostLog('[sfx] preset'); }
  function Sfx_PlayPresetTable(_t, _c, id) { hostLog(`[sfx] preset table id=${id & 0xFF}`); }

  // Short tone helper — flat pitch, no modulation.
  function Sfx_PlayToneCh(ch, div, attn, frames) {
    if (ch < 0 || ch > 2) return;
    const s = sfxTone[ch];
    s.div = Math.max(1, Math.min(1023, div | 0));
    s.attn = Math.max(0, Math.min(15, attn | 0));
    s.timer = Math.max(0, frames | 0);
    s.sw_on = 0; s.env_on = 0;
    const p = psg();
    if (p) { p.setTone(ch, s.div); p.setAttn(ch, s.attn); }
  }
  // Extended tone — full template signature with pitch sweep + attn envelope.
  // Stored in the channel state; `Sfx_Update()` (→ _sfxToneTick) applies the
  // per-frame modulation. `sw_step` is signed: negative = down-sweep, positive
  // = up-sweep. `sw_ping` makes the sweep ping-pong between base and end.
  function Sfx_PlayToneEx(ch, div, attn, frames,
                          sw_end, sw_step, sw_speed, sw_ping, sw_on,
                          env_on, env_step, env_spd) {
    if (ch < 0 || ch > 2) return;
    const s = sfxTone[ch];
    s.div = Math.max(1, Math.min(1023, div | 0));
    s.attn = Math.max(0, Math.min(15, attn | 0));
    s.timer = Math.max(0, frames | 0);
    s.sw_base = s.div;
    s.sw_end  = Math.max(1, Math.min(1023, sw_end | 0));
    // Preserve the caller's sign on sw_step (s16). JS bitwise `| 0` keeps it.
    s.sw_step = Math.abs((sw_step | 0));
    s.sw_dir  = (sw_step | 0) < 0 ? -1 : +1;
    s.sw_speed = Math.max(1, sw_speed | 0);
    s.sw_counter = 0;
    s.sw_on = sw_on ? 1 : 0;
    s.sw_ping = sw_ping ? 1 : 0;
    s.env_on = env_on ? 1 : 0;
    s.env_step = Math.max(0, env_step | 0);
    s.env_spd  = Math.max(1, env_spd  | 0);
    s.env_counter = 0;
    const p = psg();
    if (p) { p.setTone(ch, s.div); p.setAttn(ch, s.attn); }
  }
  // Noise (raw ctrl byte form). `val` follows the T6W28 register encoding:
  // bits 1..0 = rate, bit 2 = type (0 periodic / 1 white).
  function Sfx_PlayNoise(val, attn, frames) {
    sfxNoise.attn = Math.max(0, Math.min(15, attn | 0));
    sfxNoise.timer = Math.max(0, frames | 0);
    sfxNoise.env_on = 0;
    const p = psg();
    if (p) { p.setNoise(val & 0xFF); p.setAttn(3, sfxNoise.attn); }
  }
  // Extended noise — rate + type + optional envelope.
  function Sfx_PlayNoiseEx(rate, type, attn, frames,
                           _burst, _burst_dur,
                           env_on, env_step, env_spd) {
    const ctrl = ((type & 1) << 2) | (rate & 3);
    sfxNoise.attn = Math.max(0, Math.min(15, attn | 0));
    sfxNoise.timer = Math.max(0, frames | 0);
    sfxNoise.env_on = env_on ? 1 : 0;
    sfxNoise.env_step = Math.max(0, env_step | 0);
    sfxNoise.env_spd  = Math.max(1, env_spd  | 0);
    sfxNoise.env_counter = 0;
    const p = psg();
    if (p) { p.setNoise(ctrl); p.setAttn(3, sfxNoise.attn); }
  }
  function Sfx_SendBytes(_b1, _b2, _b3) { /* no-op */ }
  function Sfx_BufferBegin()            { /* no-op */ }
  function Sfx_BufferPush(_b1, _b2, _b3){ /* no-op */ }
  function Sfx_BufferCommit()           { /* no-op */ }
  function Sfx_Stop() {
    hostLog('[sfx] stop');
    for (const s of sfxTone) { s.timer = 0; s.sw_on = 0; s.env_on = 0; }
    sfxNoise.timer = 0; sfxNoise.env_on = 0;
    const p = psg();
    if (p) for (let ch = 0; ch < 4; ch++) p.setAttn(ch, 15);
    for (let i = 0; i < 4; i++) sfxTimer[i] = 0;
  }
  // --- BGM stream interpreter ------------------------------------------
  // Stream format (AUDIO.md §4): each of the 4 channels (CH0-CH2 = tones,
  // CHN = noise) is a u8 array of note/opcode bytes.
  //   1..51   = note index (looked up in NOTE_TABLE, divider for tone chans,
  //             raw noise-ctrl byte 0..7 for the noise chan)
  //   0xFF    = REST (silence this tick only)
  //   0x00    = END of stream (or loop-back if a loop offset is set)
  //   0xF0..9 = FX opcodes with fixed parameter counts
  //
  // `Bgm_Update()` advances each active channel by ONE tick per frame:
  //   - opcodes consume their parameter bytes WITHOUT advancing a tick
  //   - notes / REST / END consume exactly one tick
  // Frame-lock is provided by the user's main loop calling `Sounds_Update()`
  // (→ `Bgm_Update()`) once per `ngpc_vsync()`.
  //
  // Default note table covers A2 (110 Hz) to B6 (1975.5 Hz) in equal
  // temperament — approximation of the driver's canonical NOTE_TABLE.
  // `Bgm_SetNoteTable()` overrides with the project-exported table when
  // the user wires it up (standard template pattern).
  const BGM_DEFAULT_NOTE_TABLE = (() => {
    const t = [];
    // Notes 0..50 = indices 0..50 → 51 entries. A2 = 110 Hz at index 0.
    for (let i = 0; i < 51; i++) {
      const hz = 110 * Math.pow(2, i / 12);
      t.push(Math.round(96000 / hz));
    }
    return t;
  })();
  // Opcode parameter counts — matches the template driver's sounds.h
  // opcode table (SET_ATTN through SET_MACRO + EXT). v1 only reacts to
  // SET_ATTN / HOST_CMD (fade); the rest are consumed to keep the stream
  // pointer aligned without producing the effect.
  const BGM_OPCODE_PARAMS = {
    0xF0: 1, 0xF1: 2, 0xF2: 3, 0xF3: 4, 0xF4: 1,
    0xF5: 0, 0xF6: 2, 0xF7: 1, 0xF8: 2, 0xF9: 4,
    0xFA: 3, 0xFB: 1, 0xFC: 1, 0xFD: 1,
    // 0xFE EXT: variable length (sub byte + per-sub payload). v1 drops
    // the stream on EXT — see _bgmParamCount below.
  };
  const bgm = {
    streams: [null, null, null, null],
    offsets: [0, 0, 0, 0],
    loops:   [0, 0, 0, 0],   // 0 = no loop, else byte offset to jump to on END
    active:  false,
    noteTable: null,
    fadeStep: 0,             // frames remaining before next fade attn increment
    fadeSpeed: 0,            // 0 = no fade
    fadeAttn: 0,             // current fade-out attenuation offset (0..15)
    baseAttn: [0, 0, 0, 0],  // per-channel SET_ATTN accumulator
  };
  function _bgmNotes() { return bgm.noteTable || BGM_DEFAULT_NOTE_TABLE; }
  function _bgmApplyAttn(ch) {
    const p = psg();
    if (!p) return;
    const eff = Math.min(15, bgm.baseAttn[ch] + bgm.fadeAttn);
    p.setAttn(ch, eff);
  }
  function _bgmSilenceAll() {
    const p = psg();
    if (!p) return;
    for (let i = 0; i < 4; i++) p.setAttn(i, 15);
  }

  function _isStream(x) {
    return Array.isArray(x) || ArrayBuffer.isView(x);
  }
  function Bgm_Start(ch0, ch1, ch2, chn) {
    // Some projects call `Bgm_Start(0)` or `Bgm_Start(track_id)` expecting a
    // multi-song dispatcher. The template API requires 4 stream pointers —
    // pass anything else through as a helpful log and no-op rather than
    // silently swallowing it.
    if (!_isStream(ch0) && ch1 === undefined && ch2 === undefined && chn === undefined) {
      hostLog(`[bgm] Bgm_Start(${ch0}) — expected 4 channel streams, got a bare argument. ` +
              `Use Bgm_StartLoop4Ex(CH0, L0, CH1, L1, CH2, L2, CHN, LN) with the exported streams.`);
      return;
    }
    bgm.streams = [
      _isStream(ch0) ? ch0 : null,
      _isStream(ch1) ? ch1 : null,
      _isStream(ch2) ? ch2 : null,
      _isStream(chn) ? chn : null,
    ];
    bgm.offsets = [0, 0, 0, 0];
    bgm.loops   = [0, 0, 0, 0];
    bgm.baseAttn = [0, 0, 0, 0];
    bgm.fadeStep = bgm.fadeSpeed = bgm.fadeAttn = 0;
    bgm.active = bgm.streams.some(s => s && s.length);
    _bgmSilenceAll();
  }
  function Bgm_StartEx(stream, loopOffset) {
    Bgm_Start(stream, null, null, null);
    bgm.loops[0] = loopOffset | 0;
  }
  function Bgm_StartLoop(stream)                    { Bgm_StartEx(stream, 0); }
  function Bgm_StartLoop2(a, b)                     { Bgm_Start(a, b, null, null); }
  function Bgm_StartLoop2Ex(a, la, b, lb)           { Bgm_Start(a, b, null, null); bgm.loops[0]=la|0; bgm.loops[1]=lb|0; }
  function Bgm_StartLoop3(a, b, c)                  { Bgm_Start(a, b, c, null); }
  function Bgm_StartLoop3Ex(a, la, b, lb, c, lc)    { Bgm_Start(a, b, c, null); bgm.loops[0]=la|0; bgm.loops[1]=lb|0; bgm.loops[2]=lc|0; }
  function Bgm_StartLoop4(a, b, c, n)               { Bgm_Start(a, b, c, n); }
  function Bgm_StartLoop4Ex(a, la, b, lb, c, lc, n, ln) {
    Bgm_Start(a, b, c, n);
    bgm.loops = [la|0, lb|0, lc|0, ln|0];
  }
  function Bgm_SetNoteTable(t) { bgm.noteTable = (t && t.length) ? t : null; }
  function Bgm_Stop() {
    bgm.active = false;
    bgm.streams = [null, null, null, null];
    _bgmSilenceAll();
  }
  function Bgm_FadeOut(speed) {
    bgm.fadeSpeed = Math.max(0, speed | 0);
    bgm.fadeStep = bgm.fadeSpeed;
    if (bgm.fadeSpeed === 0) bgm.fadeAttn = 0;  // §3.7: speed 0 cancels fade
  }
  function Bgm_SetTempo(_t)  { /* v1: 1 tick/frame only */ }
  function Bgm_SetSpeed(_m)  { /* v1: 1 tick/frame only */ }
  function Bgm_SetGate(_p)   { /* v1: no gate modulation */ }
  function Bgm_DebugReset()       { /* no-op */ }
  function Bgm_DebugSnapshot(_o)  { /* no-op */ }

  function Bgm_Update() {
    if (!bgm.active) return;
    // Fade-out: every `fadeSpeed` frames increment `fadeAttn` until 15.
    if (bgm.fadeSpeed > 0) {
      if (--bgm.fadeStep <= 0) {
        bgm.fadeStep = bgm.fadeSpeed;
        if (bgm.fadeAttn < 15) bgm.fadeAttn++;
        for (let ch = 0; ch < 4; ch++) _bgmApplyAttn(ch);
        if (bgm.fadeAttn >= 15) { Bgm_Stop(); return; }
      }
    }
    const notes = _bgmNotes();
    const p = psg();
    let anyActive = false;
    for (let ch = 0; ch < 4; ch++) {
      const stream = bgm.streams[ch];
      if (!stream) continue;
      anyActive = true;
      // Consume opcodes + exactly one tick-consuming byte (note / REST / END).
      let guard = 32;
      while (guard-- > 0) {
        const idx = bgm.offsets[ch];
        if (idx >= stream.length) {
          bgm.streams[ch] = null;
          if (p) p.setAttn(ch, 15);
          break;
        }
        const b = stream[idx] & 0xFF;
        if (b === 0x00) {
          // END — loop back if configured, else drop channel.
          if (bgm.loops[ch] > 0) { bgm.offsets[ch] = bgm.loops[ch]; continue; }
          bgm.streams[ch] = null;
          if (p) p.setAttn(ch, 15);
          break;
        }
        if (b === 0xFF) {
          // REST — silence this tick, advance.
          if (p) p.setAttn(ch, 15);
          bgm.offsets[ch]++;
          break;
        }
        if (b >= 1 && b <= 51) {
          // Note — tone channels look up NOTE_TABLE; noise channel treats
          // byte 1..8 as noise control register (0..7 per §4.3).
          if (ch < 3) {
            const noteIdx = b - 1;
            const div = notes[noteIdx] || 1023;
            if (p) p.setTone(ch, div);
          } else {
            if (p) p.setNoise((b - 1) & 0xFF);
          }
          _bgmApplyAttn(ch);
          bgm.offsets[ch]++;
          break;
        }
        // FX opcode: consume parameters without advancing tick.
        // 0xFE EXT has variable-length payload — v1 drops the stream
        // instead of risking misalignment.
        if (b === 0xFE) {
          bgm.streams[ch] = null;
          if (p) p.setAttn(ch, 15);
          break;
        }
        const params = BGM_OPCODE_PARAMS[b];
        if (params === undefined) { bgm.offsets[ch]++; break; }
        if (b === 0xF0) {
          // SET_ATTN — base attenuation for this channel.
          bgm.baseAttn[ch] = stream[idx + 1] & 0x0F;
          _bgmApplyAttn(ch);
        } else if (b === 0xF6) {
          // HOST_CMD — type byte, data byte. Only handle fade-out in v1.
          const type = stream[idx + 1] & 0xFF;
          const data = stream[idx + 2] & 0xFF;
          if (type === 0) Bgm_FadeOut(data);
        }
        // F1 env / F2 vib / F3 sweep / F4 inst / F5 pan / F7 expr / F8
        // pitch-bend / F9 adsr: skip parameters in v1 (see roadmap 0bis-6).
        bgm.offsets[ch] += 1 + params;
      }
    }
    if (!anyActive) bgm.active = false;
  }

  // ---- Palette FX (ngpc_palfx.h) ---------------------------------------
  //
  // Real implementation (not a stub): up to PALFX_MAX_SLOTS concurrent
  // effects. Each slot snapshots the plane+pal's 4 u16 entries on start so
  // `stop` / `stop_all` can restore the original palette. Fade lerps each
  // RGB channel independently toward the target per doc §30 "Fade". Cycle
  // rotates entries 1..3 (entry 0 = transparent per NGPC convention).
  // Flash sets all 4 entries to `color` then restores at duration end.
  const PALFX_MAX_SLOTS = 4;
  const PALFX_NONE = 0, PALFX_FADE = 1, PALFX_CYCLE = 2, PALFX_FLASH = 3;
  const palfxSlots = Array.from({ length: PALFX_MAX_SLOTS }, () => ({
    type: PALFX_NONE, plane: 0, palId: 0, speed: 1, tick: 0,
    orig: [0, 0, 0, 0], target: [0, 0, 0, 0], duration: 0, elapsed: 0,
  }));

  function _palAddr(plane, palId) {
    // PLANE_BASE is defined earlier in this IIFE (runtime's gfx section).
    return PLANE_BASE[plane].pal + (palId & 0x0F) * 8;
  }
  function _palRead(plane, palId) {
    const base = _palAddr(plane, palId);
    return [0, 1, 2, 3].map(i => NGPC_Memory.read16(base + i * 2));
  }
  function _palWrite(plane, palId, arr) {
    const base = _palAddr(plane, palId);
    for (let i = 0; i < 4; i++) NGPC_Memory.write16(base + i * 2, arr[i] & 0xFFFF);
  }
  function _palfxAlloc() {
    for (let i = 0; i < PALFX_MAX_SLOTS; i++) {
      if (palfxSlots[i].type === PALFX_NONE) return i;
    }
    return 0xFF;
  }
  function _lerpChannel(a, b, t, steps) {
    return a + Math.trunc(((b - a) * t) / steps);
  }
  function _lerpPacked(a, b, t, steps) {
    const ar = a & 0x00F, ag = (a >>> 4) & 0x0F, ab = (a >>> 8) & 0x0F;
    const br = b & 0x00F, bg = (b >>> 4) & 0x0F, bb = (b >>> 8) & 0x0F;
    const r = _lerpChannel(ar, br, t, steps) & 0x0F;
    const g = _lerpChannel(ag, bg, t, steps) & 0x0F;
    const bC = _lerpChannel(ab, bb, t, steps) & 0x0F;
    return r | (g << 4) | (bC << 8);
  }

  function ngpc_palfx_fade(plane, palId, target, speed) {
    const s = _palfxAlloc();
    if (s === 0xFF) return 0xFF;
    const slot = palfxSlots[s];
    slot.type  = PALFX_FADE;
    slot.plane = plane; slot.palId = palId;
    slot.speed = Math.max(1, speed & 0xFF);
    slot.tick  = 0; slot.elapsed = 0;
    slot.orig  = _palRead(plane, palId);
    slot.target = [target[0], target[1], target[2], target[3]];
    // Use speed as both "frames per step" and "total frames" — template
    // behaviour is frames-per-step so a speed=1 fade of 15 steps = 15
    // frames. We interpret it that way too: 15 steps total.
    slot.duration = 15;
    return s;
  }
  function ngpc_palfx_fade_to_black(plane, palId, speed) {
    return ngpc_palfx_fade(plane, palId, [0, 0, 0, 0], speed);
  }
  function ngpc_palfx_fade_to_white(plane, palId, speed) {
    return ngpc_palfx_fade(plane, palId, [0xFFF, 0xFFF, 0xFFF, 0xFFF], speed);
  }
  function ngpc_palfx_cycle(plane, palId, speed) {
    const s = _palfxAlloc();
    if (s === 0xFF) return 0xFF;
    const slot = palfxSlots[s];
    slot.type = PALFX_CYCLE;
    slot.plane = plane; slot.palId = palId;
    slot.speed = Math.max(1, speed & 0xFF);
    slot.tick = 0;
    slot.orig = _palRead(plane, palId);
    return s;
  }
  function ngpc_palfx_flash(plane, palId, color, duration) {
    const s = _palfxAlloc();
    if (s === 0xFF) return 0xFF;
    const slot = palfxSlots[s];
    slot.type = PALFX_FLASH;
    slot.plane = plane; slot.palId = palId;
    slot.duration = Math.max(1, duration & 0xFF);
    slot.elapsed = 0;
    slot.orig = _palRead(plane, palId);
    _palWrite(plane, palId, [color, color, color, color]);
    return s;
  }
  function ngpc_palfx_update() {
    for (const slot of palfxSlots) {
      if (slot.type === PALFX_NONE) continue;
      if (slot.type === PALFX_FADE) {
        slot.tick++;
        if (slot.tick >= slot.speed) {
          slot.tick = 0;
          slot.elapsed++;
          const t = Math.min(slot.elapsed, slot.duration);
          const out = [0, 0, 0, 0];
          for (let i = 0; i < 4; i++) {
            out[i] = _lerpPacked(slot.orig[i], slot.target[i], t, slot.duration);
          }
          _palWrite(slot.plane, slot.palId, out);
          if (slot.elapsed >= slot.duration) slot.type = PALFX_NONE;
        }
      } else if (slot.type === PALFX_CYCLE) {
        slot.tick++;
        if (slot.tick >= slot.speed) {
          slot.tick = 0;
          const cur = _palRead(slot.plane, slot.palId);
          _palWrite(slot.plane, slot.palId, [cur[0], cur[2], cur[3], cur[1]]);
        }
      } else if (slot.type === PALFX_FLASH) {
        slot.elapsed++;
        if (slot.elapsed >= slot.duration) {
          _palWrite(slot.plane, slot.palId, slot.orig);
          slot.type = PALFX_NONE;
        }
      }
    }
  }
  function ngpc_palfx_active(slot) {
    if (slot >= PALFX_MAX_SLOTS) return 0;
    return palfxSlots[slot].type !== PALFX_NONE ? 1 : 0;
  }
  function ngpc_palfx_stop(slot) {
    if (slot >= PALFX_MAX_SLOTS) return;
    const s = palfxSlots[slot];
    if (s.type === PALFX_NONE) return;
    _palWrite(s.plane, s.palId, s.orig);
    s.type = PALFX_NONE;
  }
  function ngpc_palfx_stop_all() {
    for (let i = 0; i < PALFX_MAX_SLOTS; i++) ngpc_palfx_stop(i);
  }

  return {
    ngpc_init,
    ngpc_sys_patch,
    ngpc_shutdown,
    ngpc_is_color,
    ngpc_get_language,
    ngpc_gfx_set_bg_color,
    ngpc_gfx_set_palette,
    ngpc_gfx_put_tile,
    ngpc_gfx_put_tile_ex,
    ngpc_gfx_clear,
    ngpc_gfx_fill,
    ngpc_gfx_fill_rect,
    ngpc_gfx_scroll,
    ngpc_gfx_scroll_parallax,
    ngpc_gfx_swap_planes,
    ngpc_gfx_set_viewport,
    ngpc_gfx_sprite_offset,
    ngpc_gfx_lcd_invert,
    ngpc_gfx_set_outside_color,
    ngpc_gfx_char_over,
    ngpc_gfx_set_color_direct,
    ngpc_gfx_get_tile,
    ngpc_gfx_set_rect_pal,
    ngpc_gfx_load_tiles,
    ngpc_gfx_load_tiles_at,
    ngpc_gfx_load_tiles_u8,
    ngpc_gfx_load_tiles_u8_at,
    ngpc_sprite_set,
    ngpc_sprite_move,
    ngpc_sprite_hide,
    ngpc_sprite_hide_all,
    ngpc_sprite_set_flags,
    ngpc_sprite_set_tile,
    ngpc_sprite_get_pal,
    ngpc_mspr_draw,
    ngpc_mspr_hide,
    ngpc_mspr_anim_start,
    ngpc_mspr_anim_update,
    ngpc_mspr_anim_done,
    ngpc_bmp_init,
    ngpc_bmp_pixel,
    ngpc_bmp_get_pixel,
    ngpc_bmp_clear,
    ngpc_bmp_line,
    ngpc_bmp_rect,
    ngpc_bmp_fill_rect,
    ngpc_bmp_hline,
    ngpc_bmp_vline,
    ngpc_log_init,
    ngpc_log_clear,
    ngpc_log_hex,
    ngpc_log_str,
    ngpc_log_dump,
    ngpc_log_count,
    ngpc_assert_fail,
    /* Pointer helpers (used by the interpreter's pointer rewrite). */
    PTR, PINC, PADD,
    makeSystemPointers,
    /* host-only helper */ _setHostLog: setHostLog,
    ngpc_load_sysfont,
    ngpc_text_print,
    ngpc_text_print_dec,
    ngpc_text_print_hex,
    ngpc_text_print_num,
    ngpc_text_print_hex32,
    ngpc_text_tile_screen,
    ngpc_input_update,
    ngpc_input_set_repeat,
    ngpc_sin,
    ngpc_cos,
    ngpc_rng_seed,
    ngpc_random,
    ngpc_qrandom,
    ngpc_qrandom_init,
    ngpc_mul32,
    ngpc_in_vblank,
    ngpc_cpu_speed,
    ngpc_memcpy,
    ngpc_memset,
    memcpy, memset,   /* plain stdlib names for user ergonomics */
    ngpc_sprite_frame_begin,
    ngpc_sprite_frame_end,
    ngpc_sprite_flush,
    ngpc_vramq_init,
    ngpc_vramq_copy,
    ngpc_vramq_fill,
    ngpc_vramq_flush,
    ngpc_vramq_clear,
    ngpc_vramq_pending,
    ngpc_vramq_dropped,
    ngpc_vramq_clear_dropped,
    /* Audio stubs (sounds.h + audio/sfx_ids.h) */
    Sounds_Init, Sounds_ResetState, Sounds_Update,
    Sounds_DebugFault, Sounds_DebugDrops, Sounds_DebugLastSfx,
    Sfx_Update, Sfx_Play, Sfx_PlayPreset, Sfx_PlayPresetTable,
    Sfx_PlayToneCh, Sfx_PlayToneEx, Sfx_PlayNoise, Sfx_PlayNoiseEx,
    Sfx_SendBytes, Sfx_BufferBegin, Sfx_BufferPush, Sfx_BufferCommit, Sfx_Stop,
    Bgm_Start, Bgm_StartEx, Bgm_StartLoop, Bgm_StartLoop2, Bgm_StartLoop2Ex,
    Bgm_StartLoop3, Bgm_StartLoop3Ex, Bgm_StartLoop4, Bgm_StartLoop4Ex,
    Bgm_SetNoteTable, Bgm_Stop, Bgm_FadeOut, Bgm_SetTempo, Bgm_Update,
    Bgm_SetSpeed, Bgm_SetGate, Bgm_DebugReset, Bgm_DebugSnapshot,
    /* Palette FX (ngpc_palfx.h) */
    ngpc_palfx_fade, ngpc_palfx_fade_to_black, ngpc_palfx_fade_to_white,
    ngpc_palfx_cycle, ngpc_palfx_flash, ngpc_palfx_update,
    ngpc_palfx_active, ngpc_palfx_stop, ngpc_palfx_stop_all,
  };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_Runtime = NGPC_Runtime;
