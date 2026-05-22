// NGPC VDP simulator — reads emulated memory and draws a 160x152 frame.
//
// Doc references (every bit layout below is verified against these):
//   - /home/Tixu/Bureau/ngpc/01_SDK/docs/K2GETechRef.txt
//     §4-3-3 (Sprite VRAM format), §4-3-5-1 (Character data layout),
//     §4-4-4 (Scroll VRAM format), §4-4-8 (Scroll offsets).
//   - /home/Tixu/Bureau/ngpc/04_MY_PROJECTS/Doc de dev/Final/Doc final uniformisé eng/
//     TILEMAPS_SCROLL.md §1.2 (tilemap u16 entry bit layout),
//     SPRITES_OAM.md §1.1 (OAM 4-byte layout + flags byte).
//
// Known simplifications (explicit, documented):
//   - Sprite priority mixing is now applied per K2GE §4-3-3-2:
//     PR.C = 01 behind both planes, PR.C = 10 between planes, PR.C = 11
//     in front of everything. SCR1/SCR2 ordering still honors
//     HW_SCR_PRIO (0x8030) bit 7.
//   - Window register WIN_X/Y/W/H (0x8002..0x8005) now applied: pixels
//     outside the viewport get the border color from HW_LCD_CTL bits 2-0
//     indexed into HW_PAL_BG (K2GETechRef §4-5 / ngpc_gfx.c:273-280).
//   - Raster / Hblank effects ignored.
//   - Sprite H-chain / V-chain now applied per SPRITES_OAM.md §2.1.
//   - Priority 00 (hidden) is skipped; 01/10/11 all render identically for now.

const NGPC_VDP = (() => {
  const W = 160, H = 152;
  const TILE_BYTES = 16;          // 8x8 tiles, 2bpp
  const TILE_PX = 8;

  // Viewport clipping bounds — set at the top of render() and consulted by
  // the block-writer helpers below. Default covers full screen.
  let clipX0 = 0, clipY0 = 0, clipX1 = W, clipY1 = H;

  // Convert an NGPC packed 12-bit RGB (RRRR GGGG BBBB) to a canvas RGBA pixel.
  // Each channel is 4 bits; we scale to 8 bits by replicating.
  function rgbFromPacked(packed) {
    const r = (packed & 0x00F);
    const g = (packed & 0x0F0) >>> 4;
    const b = (packed & 0xF00) >>> 8;
    return [r * 17, g * 17, b * 17]; // *17 == (x<<4)|x
  }

  function readPaletteColor(base, palIdx, colorIdx) {
    const addr = base + (palIdx * 4 + colorIdx) * 2;
    return NGPC_Memory.read16(addr);
  }

  // Decode pixel (px, py) of tile `tileIdx` to its 2-bit color index (0..3).
  // NGPC tile format (K2GE): 2bpp packed chunky, 2 bytes per row.
  //   byte 0 at offset+0 : dots 4..7 (D7D6=dot4, D5D4=dot5, D3D2=dot6, D1D0=dot7)
  //   byte 1 at offset+1 : dots 0..3 (D7D6=dot0, D5D4=dot1, D3D2=dot2, D1D0=dot3)
  // Ref: /home/Tixu/Bureau/ngpc/01_SDK/docs/K2GETechRef.txt section 4-3-5-1.
  function readTilePixel(tileIdx, px, py) {
    const base = 0xA000 + tileIdx * TILE_BYTES;
    const byteOffset = (px < 4) ? 1 : 0;
    const byte = NGPC_Memory.read8(base + py * 2 + byteOffset);
    const dotInByte = px & 3;
    const shift = (3 - dotInByte) * 2;
    return (byte >>> shift) & 0x3;
  }

  // Draw a single 8x8 block of a sprite (or of a chained extension) at
  // absolute (sx, sy) using a specific tile index, H/V flip, and palette.
  // The caller resolves tile offset and position for chain extensions.
  function drawSpriteBlock(fb, sx, sy, tileIdx, hFlip, vFlip, palIdx) {
    for (let dy = 0; dy < TILE_PX; dy++) {
      const py = vFlip ? (7 - dy) : dy;
      const py_abs = sy + dy;
      if (py_abs < clipY0 || py_abs >= clipY1) continue;
      for (let dx = 0; dx < TILE_PX; dx++) {
        const px = hFlip ? (7 - dx) : dx;
        const px_abs = sx + dx;
        if (px_abs < clipX0 || px_abs >= clipX1) continue;
        const c = readTilePixel(tileIdx, px, py);
        if (c === 0) continue; // color 0 is transparent for sprites (K2GE)
        const packed = readPaletteColor(0x8200, palIdx, c);
        const [r, g, b] = rgbFromPacked(packed);
        const fi = (py_abs * W + px_abs) * 4;
        fb[fi] = r; fb[fi + 1] = g; fb[fi + 2] = b; fb[fi + 3] = 255;
      }
    }
  }

  // Return the priority class (0..3) of an OAM slot plus the chain width
  // (how many slots it absorbs — 1, 2 or 4) so the render loop can mark
  // consumed slots without re-drawing them.
  function spriteHeader(idx) {
    const b1 = NGPC_Memory.read8(0x8800 + idx * 4 + 1);
    const hChain = (b1 & 0x04) !== 0;
    const vChain = (b1 & 0x02) !== 0;
    const consumed = (hChain && vChain) ? 4 : (hChain || vChain) ? 2 : 1;
    return { priority: (b1 >>> 3) & 0x3, consumed };
  }

  // Draw the slot at `idx` (head) plus any chained extension blocks, honouring
  // SPRITES_OAM.md §2.1: H-chain adds T+1 at +8,0, V-chain adds T+2 at 0,+8,
  // H+V chain forms a 2x2 T+0..T+3. Head palette/flip applies to all blocks.
  function drawSpriteHead(fb, idx) {
    const base = 0x8800 + idx * 4;
    const b0 = NGPC_Memory.read8(base + 0);
    const b1 = NGPC_Memory.read8(base + 1);
    // Sprite positions are added to HW_SPR_OFS_X/Y (0x8020/0x8021) —
    // K2GE §4-3-3 / ngpc_gfx.c:254 "H = H.P + PO.H, V = V.P + PO.V".
    const offX = NGPC_Memory.read8(0x8020);
    const offY = NGPC_Memory.read8(0x8021);
    const x  = (NGPC_Memory.read8(base + 2) + offX) & 0xFF;
    const y  = (NGPC_Memory.read8(base + 3) + offY) & 0xFF;

    const hFlip   = (b1 & 0x80) !== 0;
    const vFlip   = (b1 & 0x40) !== 0;
    const hChain  = (b1 & 0x04) !== 0;
    const vChain  = (b1 & 0x02) !== 0;
    const tileIdx = (b0 | ((b1 & 0x01) << 8)) & 0x1FF;
    const palIdx  = NGPC_Memory.read8(0x8C00 + idx) & 0x0F;

    drawSpriteBlock(fb, x, y, tileIdx, hFlip, vFlip, palIdx);
    if (hChain && vChain) {
      drawSpriteBlock(fb, x + 8, y,     (tileIdx + 1) & 0x1FF, hFlip, vFlip, palIdx);
      drawSpriteBlock(fb, x,     y + 8, (tileIdx + 2) & 0x1FF, hFlip, vFlip, palIdx);
      drawSpriteBlock(fb, x + 8, y + 8, (tileIdx + 3) & 0x1FF, hFlip, vFlip, palIdx);
    } else if (hChain) {
      drawSpriteBlock(fb, x + 8, y, (tileIdx + 1) & 0x1FF, hFlip, vFlip, palIdx);
    } else if (vChain) {
      drawSpriteBlock(fb, x, y + 8, (tileIdx + 2) & 0x1FF, hFlip, vFlip, palIdx);
    }
  }

  // Draw a 32x32-tile scroll plane with (scrollX, scrollY) offset.
  // Tilemap u16 entry format (TILEMAPS_SCROLL.md §1.2, K2GE §4-4-4):
  //   bit 15    : H flip (H.F)
  //   bit 14    : V flip (V.F)
  //   bits 12-9 : CP.C palette number (0..15)
  //   bit 8     : tile index bit 8 (C.C high bit)
  //   bits 7-0  : tile index bits 7-0
  // Bit 13 is unused in K2GE mode; bit 5 (P.C) is K1GE-compat only.
  function drawScrollPlane(fb, planeBase, paletteBase, scrollX, scrollY) {
    const PLANE_TILES = 32;
    const PLANE_PX = PLANE_TILES * TILE_PX; // 256

    for (let sy = clipY0; sy < clipY1; sy++) {
      const worldY = (sy + scrollY) & (PLANE_PX - 1);
      const ty = (worldY >>> 3) & 31;
      const py = worldY & 7;
      for (let sx = clipX0; sx < clipX1; sx++) {
        const worldX = (sx + scrollX) & (PLANE_PX - 1);
        const tx = (worldX >>> 3) & 31;
        const px = worldX & 7;

        const tmAddr = planeBase + (ty * PLANE_TILES + tx) * 2;
        const entry = NGPC_Memory.read16(tmAddr);
        const tileIdx = entry & 0x01FF;
        const hFlip = (entry & 0x8000) !== 0;
        const vFlip = (entry & 0x4000) !== 0;
        const palIdx = (entry >>> 9) & 0x0F;

        const tpx = hFlip ? (7 - px) : px;
        const tpy = vFlip ? (7 - py) : py;
        const c = readTilePixel(tileIdx, tpx, tpy);
        if (c === 0) continue;
        const packed = readPaletteColor(paletteBase, palIdx, c);
        const [r, g, b] = rgbFromPacked(packed);
        const fi = (sy * W + sx) * 4;
        fb[fi] = r; fb[fi + 1] = g; fb[fi + 2] = b; fb[fi + 3] = 255;
      }
    }
  }

  // Pure-pixel renderer — returns a Uint8ClampedArray of W*H*4 RGBA bytes.
  // No DOM / no canvas dependency. Use this from headless hosts (Node, Workers,
  // tests) and from `render(ctx)` below as the single source of truth.
  function renderToPixels() {
    const fb = new Uint8ClampedArray(W * H * 4);
    renderInto(fb);
    return fb;
  }

  // Browser-facing canvas renderer — thin adapter around renderToPixels().
  function render(canvasCtx) {
    const img = canvasCtx.createImageData(W, H);
    renderInto(img.data);
    canvasCtx.putImageData(img, 0, 0);
  }

  // The actual pixel-painting work lives here so both renderToPixels() and
  // render(ctx) share the same implementation. `fb` is any RGBA byte buffer
  // of length W*H*4 (Uint8ClampedArray or compatible).
  function renderInto(fb) {

    // Background: only displayed when HW_BG_CTL (0x8118) bit7 = 1 per
    // HW_REGISTERS.md §5.3. Otherwise fall back to black.
    let br = 0, bg = 0, bb = 0;
    if ((NGPC_Memory.read8(0x8118) & 0x80) !== 0) {
      const bgPacked = NGPC_Memory.read16(0x83E0);
      [br, bg, bb] = rgbFromPacked(bgPacked);
    }
    // Outside-window color = BG palette entry indexed by HW_LCD_CTL bits 2-0
    // (HW_REGISTERS.md §5.1, ngpc_gfx.c:273-280). Falls back to the main BG
    // color if that palette slot is 0 (empty).
    const lcdCtl = NGPC_Memory.read8(0x8012);
    const borderIdx = lcdCtl & 0x07;
    const borderPacked = NGPC_Memory.read16(0x83E0 + borderIdx * 2);
    let [obr, obg, obb] = rgbFromPacked(borderPacked);
    if (!borderPacked) { obr = br; obg = bg; obb = bb; }

    // Viewport window (HW_REGISTERS.md §5.1, K2GETechRef §4-5 registers
    // 0x8002-0x8005). Real hw behaviour we mirror + flag:
    //   - WSI.V = 0 → Vint fires at line WBA.V (§4-5-1), breaking the
    //     normal once-per-frame VBI cadence. Reset value of WSI.V is 0xFF,
    //     so this only happens if code wrote 0 to 0x8005 without replacing
    //     it with a real size. We keep the "full screen" fallback for
    //     visibility but warn once so bring-up bugs are discoverable.
    //   - origin + size > 256 produces undefined raster + Vint per the
    //     same section; we clamp and warn.
    let winX = NGPC_Memory.read8(0x8002);
    let winY = NGPC_Memory.read8(0x8003);
    let winW = NGPC_Memory.read8(0x8004);
    let winH = NGPC_Memory.read8(0x8005);
    if (winW === 0 || winH === 0) {
      NGPC_Memory.warnOnce('winzero',
        `HW_WIN_W/H is zero (0x8004=${winW}, 0x8005=${winH}). Real K2GE ` +
        `would fire Vint at WBA.V instead of end-of-frame (§4-5-1). ` +
        `Call ngpc_gfx_set_viewport() during init.`);
      winX = 0; winY = 0; winW = W; winH = H;
    }
    if (winX + winW > 256 || winY + winH > 256) {
      NGPC_Memory.warnOnce('winoverflow',
        `K2GE viewport origin+size overflows 256 (x+w=${winX + winW}, ` +
        `y+h=${winY + winH}). Hardware behaviour is undefined (§4-5-1).`);
    }
    if (winX + winW > W) winW = W - winX;
    if (winY + winH > H) winH = H - winY;
    clipX0 = winX; clipY0 = winY;
    clipX1 = winX + winW; clipY1 = winY + winH;

    // Fill: inside window gets BG color, outside gets border color.
    for (let y = 0; y < H; y++) {
      const inRowWindow = (y >= winY && y < winY + winH);
      for (let x = 0; x < W; x++) {
        const inside = inRowWindow && x >= winX && x < winX + winW;
        const fi = (y * W + x) * 4;
        fb[fi]     = inside ? br : obr;
        fb[fi + 1] = inside ? bg : obg;
        fb[fi + 2] = inside ? bb : obb;
        fb[fi + 3] = 255;
      }
    }

    // Layer order from bottom to top (K2GE §4-3-3-2 "Scroll Screen and
    // Sprite Priority"):
    //   1. Background color (already filled)
    //   2. Sprites with PR.C = 01 (behind both scroll planes)
    //   3. Back scroll plane
    //   4. Sprites with PR.C = 10 (between planes)
    //   5. Front scroll plane
    //   6. Sprites with PR.C = 11 (front of everything)
    //
    // HW_SCR_PRIO (0x8030) bit 7: 0 = SCR1 in front (SCR2 behind), 1 = swap.
    const prioReg = NGPC_Memory.read8(0x8030);
    const scr1Front = (prioReg & 0x80) === 0;
    const backMap  = scr1Front ? 0x9800 : 0x9000;
    const backPal  = scr1Front ? 0x8300 : 0x8280;
    const backXA   = scr1Front ? 0x8034 : 0x8032;
    const backYA   = scr1Front ? 0x8035 : 0x8033;
    const frontMap = scr1Front ? 0x9000 : 0x9800;
    const frontPal = scr1Front ? 0x8280 : 0x8300;
    const frontXA  = scr1Front ? 0x8032 : 0x8034;
    const frontYA  = scr1Front ? 0x8033 : 0x8035;

    // Precompute which OAM slots are chain heads so each pass iterates them
    // once. Slots consumed by a chain are marked non-head and skipped.
    const isHead = new Uint8Array(64).fill(1);
    for (let i = 0; i < 64; i++) {
      if (!isHead[i]) continue;
      const { consumed } = spriteHeader(i);
      for (let k = 1; k < consumed && i + k < 64; k++) isHead[i + k] = 0;
    }

    const drawSpritesAtPriority = (want) => {
      for (let i = 0; i < 64; i++) {
        if (!isHead[i]) continue;
        const { priority } = spriteHeader(i);
        if (priority !== want) continue;
        drawSpriteHead(fb, i);
      }
    };

    drawSpritesAtPriority(1);   // behind both planes
    drawScrollPlane(fb, backMap, backPal,
                    NGPC_Memory.read8(backXA), NGPC_Memory.read8(backYA));
    drawSpritesAtPriority(2);   // between planes
    drawScrollPlane(fb, frontMap, frontPal,
                    NGPC_Memory.read8(frontXA), NGPC_Memory.read8(frontYA));
    drawSpritesAtPriority(3);   // in front of everything

    // Character Over detection (K2GETechRef §4-10): the K2GE sprite system
    // uses a per-scanline line-buffer and runs out of budget when too many
    // sprite cells overlap one scanline. Per the tech ref "normally
    // Character Over will not occur" — but heavy OAM use with >32 sprites
    // crossing a single scanline reliably trips it. We count the sprite
    // *cells* (post chain-expansion) that intersect each screen scanline
    // and latch the flag if any line exceeds the threshold. The latch
    // clears at the end of the next VBlank (same doc), done by main.js.
    {
      const CELLS_PER_LINE_LIMIT = 32;
      const perLine = new Uint8Array(H);
      const spriteOffY = NGPC_Memory.read8(0x8021);
      for (let i = 0; i < 64; i++) {
        if (!isHead[i]) continue;
        const { priority, consumed } = spriteHeader(i);
        if (priority === 0) continue;  // hidden slots don't touch the line buffer
        const base = 0x8800 + i * 4;
        const y0 = (NGPC_Memory.read8(base + 3) + spriteOffY) & 0xFF;
        // chain expansion: H-chain = 2 cells on the same row; V-chain adds
        // an 8-row extension; H+V = 2x2 cells.
        const rows = consumed >= 4 ? 16 : (NGPC_Memory.read8(base + 1) & 0x02 ? 16 : 8);
        const cellsPerRow = consumed === 4 ? 2 : (NGPC_Memory.read8(base + 1) & 0x04 ? 2 : 1);
        for (let dy = 0; dy < rows; dy++) {
          const sy = (y0 + dy) & 0xFF;
          if (sy >= H) continue;
          perLine[sy] = Math.min(255, perLine[sy] + cellsPerRow);
        }
      }
      let maxLine = 0;
      for (let y = 0; y < H; y++) if (perLine[y] > maxLine) maxLine = perLine[y];
      if (maxLine > CELLS_PER_LINE_LIMIT) {
        NGPC_Memory.setCharOver(true);
      }
    }

    // HW_LCD_CTL (0x8012) bit 7 = NEG: inverted display (HW_REGISTERS.md §5.1).
    if ((NGPC_Memory.read8(0x8012) & 0x80) !== 0) {
      for (let i = 0; i < fb.length; i += 4) {
        fb[i]     = 255 - fb[i];
        fb[i + 1] = 255 - fb[i + 1];
        fb[i + 2] = 255 - fb[i + 2];
      }
    }
  }

  return { render, renderToPixels, W, H };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_VDP = NGPC_VDP;
