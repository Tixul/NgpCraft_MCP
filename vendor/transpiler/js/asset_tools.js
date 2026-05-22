// In-browser ports of tools/ngpc_sprite_export.py and tools/ngpc_tilemap.py.
//
// Same algorithms, same invariants, same C output format — so a file
// generated here is binary-identical (modulo timestamp comments) to one
// produced by the Python tools. We only cover the default single-layer
// paths that the editor needs; advanced modes (--layer2, --scr2,
// --fixed-palette, --no-dedupe, --black-is-transparent, --emit-u8-tiles)
// are not exposed but the helpers are structured to accept them later.
//
// Invariants mirrored from the Python reference:
//   - RGB444 packed as  R (low 4) | G (shifted 4) | B (shifted 8).
//   - Alpha < 128 → transparent (palette index 0, encoded as 0x0000).
//   - Opaque black in the source is represented by the sentinel 0x1000
//     during palette planning so it doesn't collide with the transparent
//     "0", then rewritten to 0x0000 only when emitted to the C palette
//     array (matches the Python exporter's behaviour byte-for-byte).
//   - Per 8x8 tile: at most 3 visible colours + transparent.

const NGPC_AssetTools = (() => {
  const OPAQUE_BLACK  = 0x1000;
  const MSPR_MAX_PARTS = 16;
  const MSPR_MAX_OFFSET_DIM = 128;

  function sanitizeCIdentifier(name) {
    let s = String(name || '').replace(/[^0-9A-Za-z_]/g, '_');
    if (!s) s = 'asset';
    if (/^\d/.test(s)) s = 'asset_' + s;
    return s;
  }

  function rgbaToRgb444(r, g, b, a, blackIsTransparent = false) {
    if (a < 128) return 0;
    const v = ((r >> 4) & 0x0F) | (((g >> 4) & 0x0F) << 4) | (((b >> 4) & 0x0F) << 8);
    if (blackIsTransparent && v === 0) return 0;
    if (v === 0) return OPAQUE_BLACK;
    return v;
  }

  // 64 pixel indices (0..3) → 8 u16 words (K2GE tile layout §4-3-5-1).
  function tileWordsFromIndices(indices) {
    const words = new Array(8);
    for (let row = 0; row < 8; row++) {
      let w = 0;
      const base = row * 8;
      for (let col = 0; col < 8; col++) {
        const idx = indices[base + col] & 0x03;
        w |= idx << (14 - col * 2);
      }
      words[row] = w;
    }
    return words;
  }

  // Sort a colour set by (frequency in this palette desc, colour value asc).
  function sortColorsByFreq(set, freq) {
    return Array.from(set).sort((a, b) => {
      const fa = freq.get(a) || 0;
      const fb = freq.get(b) || 0;
      if (fa !== fb) return fb - fa;
      return a - b;
    });
  }

  function setEquals(a, b) {
    if (a.size !== b.size) return false;
    for (const v of a) if (!b.has(v)) return false;
    return true;
  }
  function setKey(s) {
    // Deterministic string key for using a Set-of-Sets via Map.
    return Array.from(s).sort((x, y) => x - y).join(',');
  }
  function isSubset(small, big) {
    for (const v of small) if (!big.has(v)) return false;
    return true;
  }
  function setUnion(a, b) {
    const out = new Set(a);
    for (const v of b) out.add(v);
    return out;
  }

  // Greedy palette packing — same logic as tools/ngpc_sprite_export.py's
  // assign_palettes + tools/ngpc_tilemap.py's assign_palettes. Differ only
  // in whether they count the transparent entry: sprite uses "<= 4"
  // total per palette, tilemap uses "<= 3 visible (excluding 0)". Pass
  // `excludeTransparentFromCap` for the tilemap variant.
  function assignPalettes(tileSets, maxPalettes, excludeTransparentFromCap) {
    const setIds = new Map();      // key string → id
    const uniqueSets = [];         // Set<int> in insertion order
    for (const s of tileSets) {
      const k = setKey(s);
      if (!setIds.has(k)) {
        setIds.set(k, uniqueSets.length);
        uniqueSets.push(s);
      }
    }
    const setFreq = new Map();     // id → count
    for (const s of tileSets) {
      const id = setIds.get(setKey(s));
      setFreq.set(id, (setFreq.get(id) || 0) + 1);
    }
    const order = Array.from({ length: uniqueSets.length }, (_, i) => i).sort(
      (a, b) => {
        const la = uniqueSets[a].size, lb = uniqueSets[b].size;
        if (la !== lb) return lb - la;
        const fa = setFreq.get(a) || 0, fb = setFreq.get(b) || 0;
        if (fa !== fb) return fb - fa;
        return a - b;
      }
    );

    const palettes = [];           // Set<int>[]
    const setToPal = new Map();

    for (const sid of order) {
      const colors = new Set(uniqueSets[sid]);

      // 1. Exact-subset match: colors ⊂ existing palette.
      let exactIdx = -1, bestSubsetSize = 99;
      for (let i = 0; i < palettes.length; i++) {
        if (isSubset(colors, palettes[i]) && palettes[i].size < bestSubsetSize) {
          bestSubsetSize = palettes[i].size;
          exactIdx = i;
        }
      }
      if (exactIdx >= 0) { setToPal.set(sid, exactIdx); continue; }

      // 2. Expand an existing palette if union still fits.
      let expandIdx = -1, expandCost = 99;
      for (let i = 0; i < palettes.length; i++) {
        const union = setUnion(palettes[i], colors);
        const cap = excludeTransparentFromCap
          ? (union.has(0) ? union.size - 1 : union.size)
          : union.size;
        if (cap <= (excludeTransparentFromCap ? 3 : 4)) {
          const cost = union.size - palettes[i].size;
          if (cost < expandCost) { expandCost = cost; expandIdx = i; }
        }
      }
      if (expandIdx >= 0) {
        palettes[expandIdx] = setUnion(palettes[expandIdx], colors);
        setToPal.set(sid, expandIdx);
        continue;
      }

      // 3. Brand-new palette if budget left.
      if (palettes.length < maxPalettes) {
        palettes.push(new Set(colors));
        setToPal.set(sid, palettes.length - 1);
        continue;
      }

      throw new Error(
        `Need more than ${maxPalettes} palettes. Reduce sprite colour variety.`);
    }

    const tilePalIds = tileSets.map(s => setToPal.get(setIds.get(setKey(s))));
    return { palettes, tilePalIds };
  }

  function buildPaletteIndexMaps(palettes, tileColors, tilePalIds) {
    const palFreq = palettes.map(() => new Map());
    for (let i = 0; i < tileColors.length; i++) {
      const pid = tilePalIds[i];
      const freq = palFreq[pid];
      for (const c of tileColors[i]) freq.set(c, (freq.get(c) || 0) + 1);
    }
    const paletteColors = [];
    const paletteIdxMaps = [];
    for (let pid = 0; pid < palettes.length; pid++) {
      let colors = sortColorsByFreq(palettes[pid], palFreq[pid]);
      colors = colors.filter(c => c !== 0);
      colors = [0].concat(colors);
      if (colors.length > 4) {
        throw new Error(`Palette ${pid} needs ${colors.length} entries (>4). Reduce colours.`);
      }
      while (colors.length < 4) colors.push(0);
      colors = colors.slice(0, 4);
      const idxMap = new Map();
      for (let i = 0; i < 4; i++) {
        if (!idxMap.has(colors[i])) idxMap.set(colors[i], i);
      }
      paletteColors.push(colors);
      paletteIdxMaps.push(idxMap);
    }
    return { paletteColors, paletteIdxMaps };
  }

  // ImageData helper — consume RGBA pixel at (x, y) in a Uint8ClampedArray.
  function pixelAt(data, width, x, y, blackIsTransparent) {
    const i = (y * width + x) * 4;
    return rgbaToRgb444(data[i], data[i + 1], data[i + 2], data[i + 3], blackIsTransparent);
  }

  function readSpriteFrames(data, width, height, frameW, frameH, frameCount) {
    if ((frameW % 8) !== 0 || (frameH % 8) !== 0) {
      throw new Error('Frame size must be a multiple of 8.');
    }
    if (frameW <= 0 || frameH <= 0) throw new Error('Frame size must be > 0.');
    if (frameW > MSPR_MAX_OFFSET_DIM || frameH > MSPR_MAX_OFFSET_DIM) {
      throw new Error(
        `Frame size must be <= ${MSPR_MAX_OFFSET_DIM} px (MsprPart offsets are s8).`);
    }
    if ((width % frameW) !== 0 || (height % frameH) !== 0) {
      throw new Error(
        `Image size ${width}x${height} must be a multiple of frame size ${frameW}x${frameH}.`);
    }
    const framesX = Math.floor(width  / frameW);
    const framesY = Math.floor(height / frameH);
    const total = framesX * framesY;
    const use = (frameCount == null) ? total
      : Math.min(Math.max(1, frameCount), total);

    const tileColors = [];
    const tileSets   = [];
    const tileMeta   = [];

    let frameIndex = 0;
    outer: for (let fy = 0; fy < framesY; fy++) {
      for (let fx = 0; fx < framesX; fx++) {
        if (frameIndex >= use) break outer;
        const ox0 = fx * frameW, oy0 = fy * frameH;
        const tilesX = frameW / 8, tilesY = frameH / 8;
        for (let ty = 0; ty < tilesY; ty++) {
          for (let tx = 0; tx < tilesX; tx++) {
            const colors = new Array(64);
            let allTransparent = true;
            for (let py = 0; py < 8; py++) {
              const sy = oy0 + ty * 8 + py;
              for (let px = 0; px < 8; px++) {
                const sx = ox0 + tx * 8 + px;
                const c = pixelAt(data, width, sx, sy, false);
                colors[py * 8 + px] = c;
                if (c !== 0) allTransparent = false;
              }
            }
            if (allTransparent) continue;
            const cset = new Set(colors);
            const visible = new Set(cset); visible.delete(0);
            if (visible.size > 3) {
              throw new Error(
                `Frame ${frameIndex} tile (${tx},${ty}) uses ${visible.size} ` +
                `visible colours (>3). Template reserves index 0 for transparency.`);
            }
            tileColors.push(colors);
            tileSets.push(cset);
            tileMeta.push({ frame: frameIndex, ox: tx * 8, oy: ty * 8 });
          }
        }
        frameIndex++;
      }
    }
    return { useFrames: use, tileColors, tileSets, tileMeta };
  }

  function readTilemap(data, width, height, blackIsTransparent) {
    // Auto-pad to a multiple of 8, matching the Python tool.
    let w = width, h = height, padded = data;
    if ((w % 8) !== 0 || (h % 8) !== 0) {
      const pw = Math.ceil(w / 8) * 8, ph = Math.ceil(h / 8) * 8;
      padded = new Uint8ClampedArray(pw * ph * 4);
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const src = (y * w + x) * 4, dst = (y * pw + x) * 4;
          padded[dst]     = data[src];
          padded[dst + 1] = data[src + 1];
          padded[dst + 2] = data[src + 2];
          padded[dst + 3] = data[src + 3];
        }
      }
      w = pw; h = ph;
    }
    const tw = w / 8, th = h / 8;
    const tiles = [];
    const tileSets = [];
    for (let ty = 0; ty < th; ty++) {
      for (let tx = 0; tx < tw; tx++) {
        const colors = new Array(64);
        for (let y = 0; y < 8; y++) {
          const sy = ty * 8 + y;
          for (let x = 0; x < 8; x++) {
            const sx = tx * 8 + x;
            colors[y * 8 + x] = pixelAt(padded, w, sx, sy, blackIsTransparent);
          }
        }
        const cset = new Set(colors);
        const visible = new Set(cset); visible.delete(0);
        if (visible.size > 3) {
          throw new Error(
            `Tile (${tx},${ty}) uses ${visible.size} visible colours (>3). ` +
            `Template reserves index 0 for transparency.`);
        }
        tiles.push(colors);
        tileSets.push(cset.size === 0 ? new Set([0]) : cset);
      }
    }
    return { tileW: tw, tileH: th, tiles, tileSets };
  }

  // --- Emit helpers -----------------------------------------------------
  function fmtU16Rows(values, perLine = 12) {
    const out = [];
    for (let i = 0; i < values.length; i += perLine) {
      const chunk = values.slice(i, i + perLine)
        .map(v => '0x' + v.toString(16).toUpperCase().padStart(4, '0')).join(', ');
      out.push('    ' + chunk + ((i + perLine < values.length) ? ',' : ''));
    }
    return out;
  }
  function fmtU8Rows(values, perLine = 24) {
    const out = [];
    for (let i = 0; i < values.length; i += perLine) {
      const chunk = values.slice(i, i + perLine)
        .map(v => '0x' + v.toString(16).toUpperCase().padStart(2, '0')).join(', ');
      out.push('    ' + chunk + ((i + perLine < values.length) ? ',' : ''));
    }
    return out;
  }

  // --- Sprite export (ImageData → C/H) ----------------------------------
  function exportSprite(imageData, width, height, opts) {
    const frameW = opts.frameW | 0;
    const frameH = opts.frameH | 0;
    const frameCount = (opts.frameCount != null) ? (opts.frameCount | 0) : null;
    const tileBase = opts.tileBase | 0;
    const palBase  = opts.palBase  | 0;
    const animDuration = Math.max(1, Math.min(255, (opts.animDuration | 0) || 6));
    const maxPalettes  = Math.max(1, Math.min(16, opts.maxPalettes || 16));
    const dedupe = opts.dedupe !== false;
    const name = sanitizeCIdentifier(opts.name || 'sprite');

    if (tileBase < 0 || tileBase > 511) throw new Error('tile-base must be 0..511');
    if (palBase  < 0 || palBase  > 15)  throw new Error('pal-base must be 0..15');

    const { useFrames, tileColors, tileSets, tileMeta } =
      readSpriteFrames(imageData, width, height, frameW, frameH, frameCount);
    if (tileColors.length === 0) {
      throw new Error('No visible sprite tiles found (all transparent?).');
    }

    const { palettes, tilePalIds } = assignPalettes(tileSets, maxPalettes, false);
    const { paletteColors, paletteIdxMaps } =
      buildPaletteIndexMaps(palettes, tileColors, tilePalIds);
    if (palBase + paletteColors.length > 16) {
      throw new Error(
        `Palette overflow: pal-base(${palBase}) + palettes(${paletteColors.length}) > 16.`);
    }

    const frameParts = Array.from({ length: useFrames }, () => []);
    const uniqueTiles = [];
    const tileToIndex = new Map();
    const keyOf = w => w.join(',');

    for (let i = 0; i < tileColors.length; i++) {
      const idxMap = paletteIdxMaps[tilePalIds[i]];
      const indices = tileColors[i].map(c => idxMap.get(c));
      const words = tileWordsFromIndices(indices);
      let tileIdx;
      if (dedupe) {
        const k = keyOf(words);
        if (tileToIndex.has(k)) {
          tileIdx = tileToIndex.get(k);
        } else {
          tileIdx = uniqueTiles.length;
          uniqueTiles.push(words);
          tileToIndex.set(k, tileIdx);
        }
      } else {
        tileIdx = uniqueTiles.length;
        uniqueTiles.push(words);
      }
      const finalTileId = tileBase + tileIdx;
      if (finalTileId > 511) {
        throw new Error('Tile index overflow (>511). Reduce tiles or tile-base.');
      }
      frameParts[tileMeta[i].frame].push([
        tileMeta[i].ox, tileMeta[i].oy, finalTileId, palBase + tilePalIds[i],
      ]);
    }
    for (let fi = 0; fi < frameParts.length; fi++) {
      if (frameParts[fi].length > MSPR_MAX_PARTS) {
        throw new Error(
          `Frame ${fi} has ${frameParts[fi].length} visible tiles (> ${MSPR_MAX_PARTS}).`);
      }
    }

    const cSource = formatSpriteC(name, useFrames, frameW, frameH,
      tileBase, palBase, animDuration, paletteColors, uniqueTiles, frameParts);
    const hSource = formatSpriteH(name, useFrames);
    return {
      cSource, hSource,
      summary: {
        frames: useFrames, frameW, frameH,
        palettes: paletteColors.length, tiles: uniqueTiles.length,
      },
    };
  }

  function formatSpriteC(name, frameCount, frameW, frameH,
      tileBase, palBase, animDuration, paletteColors, uniqueTiles, frameParts) {
    const tileWords = uniqueTiles.flat();
    const palWords = paletteColors.flatMap(p => p.map(c => (c === OPAQUE_BLACK ? 0 : c)));
    const lines = [];
    lines.push('/* Generated by ngpc_sprite_export.py - do not edit */');
    lines.push('');
    lines.push('#include "ngpc_types.h"');
    lines.push('#include "ngpc_metasprite.h"');
    lines.push('');
    lines.push('/* Runtime note: call ngpc_mspr_draw(..., SPR_FRONT|optional flips). */');
    lines.push('');
    lines.push(`const u16 ${name}_tiles_count = ${tileWords.length}u;`);
    lines.push(`const u16 ${name}_tiles[] = {`);
    lines.push(...fmtU16Rows(tileWords));
    lines.push('};');
    lines.push('');
    lines.push(`const u8 ${name}_palette_count = ${paletteColors.length}u;`);
    lines.push(`const u16 ${name}_palettes[] = {`);
    lines.push(...fmtU16Rows(palWords));
    lines.push('};');
    lines.push('');
    lines.push(`const u8 ${name}_pal_base = ${palBase}u;`);
    lines.push('');
    lines.push(`const u16 ${name}_tile_base = ${tileBase}u;`);
    lines.push('');
    for (let fi = 0; fi < frameCount; fi++) {
      const parts = frameParts[fi];
      lines.push(`const NgpcMetasprite ${name}_frame_${fi} = {`);
      lines.push(`    ${parts.length}u, ${frameW}u, ${frameH}u,`);
      lines.push('    {');
      for (let i = 0; i < parts.length; i++) {
        const [ox, oy, tile, pal] = parts[i];
        const tail = (i + 1 < parts.length) ? ',' : '';
        lines.push(`        { ${ox}, ${oy}, ${tile}, ${pal}, 0 }${tail}`);
      }
      lines.push('    }');
      lines.push('};');
      lines.push('');
    }
    lines.push(`const MsprAnimFrame ${name}_anim[] = {`);
    for (let fi = 0; fi < frameCount; fi++) {
      const tail = (fi + 1 < frameCount) ? ',' : '';
      lines.push(`    { &${name}_frame_${fi}, ${animDuration} }${tail}`);
    }
    lines.push('};');
    lines.push('');
    lines.push(`const u8 ${name}_anim_count = ${frameCount}u;`);
    lines.push('');
    return lines.join('\n');
  }

  function formatSpriteH(name, frameCount) {
    const guard = (name + '_MSPR_H').toUpperCase();
    const out = [];
    out.push('/* Generated by ngpc_sprite_export.py - do not edit */');
    out.push('');
    out.push(`#ifndef ${guard}`);
    out.push(`#define ${guard}`);
    out.push('');
    out.push('#include "ngpc_types.h"');
    out.push('#include "ngpc_metasprite.h"');
    out.push('');
    out.push(`extern const u16 ${name}_tiles_count;`);
    out.push(`extern const u16 NGP_FAR ${name}_tiles[];`);
    out.push('');
    out.push(`extern const u8 ${name}_palette_count;`);
    out.push(`extern const u16 NGP_FAR ${name}_palettes[];`);
    out.push(`extern const u8 ${name}_pal_base;`);
    out.push(`extern const u16 ${name}_tile_base;`);
    out.push('');
    for (let fi = 0; fi < frameCount; fi++) {
      out.push(`extern const NgpcMetasprite ${name}_frame_${fi};`);
    }
    out.push('');
    out.push(`extern const MsprAnimFrame ${name}_anim[];`);
    out.push(`extern const u8 ${name}_anim_count;`);
    out.push('');
    out.push(`#endif /* ${guard} */`);
    out.push('');
    return out.join('\n');
  }

  // --- Tilemap export (ImageData → C/H) ---------------------------------
  function exportTilemap(imageData, width, height, opts) {
    const maxPalettes = Math.max(1, Math.min(16, opts.maxPalettes || 16));
    const dedupe = opts.dedupe !== false;
    const blackIsTransparent = !!opts.blackIsTransparent;
    const name = sanitizeCIdentifier(opts.name || 'tilemap');

    const { tileW, tileH, tiles, tileSets } =
      readTilemap(imageData, width, height, blackIsTransparent);
    const { palettes, tilePalIds } = assignPalettes(tileSets, maxPalettes, true);
    const { paletteColors, paletteIdxMaps } =
      buildPaletteIndexMaps(palettes, tiles, tilePalIds);

    const uniqueTiles = [];
    const tileToIndex = new Map();
    const mapTileIds = [];
    const mapPalIds  = [];
    const keyOf = w => w.join(',');
    for (let i = 0; i < tiles.length; i++) {
      const idxMap = paletteIdxMaps[tilePalIds[i]];
      const indices = tiles[i].map(c => idxMap.get(c));
      const words = tileWordsFromIndices(indices);
      let idx;
      if (dedupe) {
        const k = keyOf(words);
        if (tileToIndex.has(k)) idx = tileToIndex.get(k);
        else {
          idx = uniqueTiles.length;
          tileToIndex.set(k, idx);
          uniqueTiles.push(words);
        }
      } else {
        idx = uniqueTiles.length;
        uniqueTiles.push(words);
      }
      mapTileIds.push(idx);
      mapPalIds.push(tilePalIds[i]);
    }
    if (uniqueTiles.length > 512) {
      throw new Error(
        `Tile pool has ${uniqueTiles.length} unique tiles (>512 VRAM slots).`);
    }

    const cSource = formatTilemapC(name, tileW, tileH, paletteColors,
      uniqueTiles, mapTileIds, mapPalIds);
    const hSource = formatTilemapH(name);
    return {
      cSource, hSource,
      summary: {
        tileW, tileH, tiles: uniqueTiles.length,
        palettes: paletteColors.length, mapLen: mapTileIds.length,
      },
    };
  }

  function formatTilemapC(name, tileW, tileH, paletteColors,
      uniqueTiles, mapTileIds, mapPalIds) {
    const tileWords = uniqueTiles.flat();
    const palWords = paletteColors.flatMap(p => p.map(c => (c === OPAQUE_BLACK ? 0 : c)));
    const lines = [];
    lines.push('/* Generated by ngpc_tilemap.py - do not edit */');
    lines.push('');
    lines.push('#include "ngpc_types.h"');
    lines.push('');
    lines.push(`/* Image: ${tileW * 8}x${tileH * 8} px (${tileW}x${tileH} tiles) */`);
    lines.push(`/* Tiles: ${uniqueTiles.length} unique (${tileWords.length} words) */`);
    lines.push(`/* Palettes: ${paletteColors.length} */`);
    lines.push('');
    lines.push(`const u16 ${name}_map_w = ${tileW}u;`);
    lines.push(`const u16 ${name}_map_h = ${tileH}u;`);
    lines.push(`const u16 ${name}_map_len = ${mapTileIds.length}u;`);
    lines.push('');
    lines.push(`const u8 ${name}_palette_count = ${paletteColors.length}u;`);
    lines.push(`const u16 ${name}_palettes[] = {`);
    lines.push(...fmtU16Rows(palWords));
    lines.push('};');
    lines.push('');
    lines.push(`const u16 ${name}_tiles_count = ${tileWords.length}u;`);
    lines.push(`const u16 ${name}_tiles[] = {`);
    lines.push(...fmtU16Rows(tileWords));
    lines.push('};');
    lines.push('');
    lines.push(`const u16 ${name}_map_tiles[] = {`);
    lines.push(...fmtU16Rows(mapTileIds));
    lines.push('};');
    lines.push('');
    lines.push(`const u8 ${name}_map_pals[] = {`);
    lines.push(...fmtU8Rows(mapPalIds));
    lines.push('};');
    lines.push('');
    return lines.join('\n');
  }

  function formatTilemapH(name) {
    const guard = (name + '_TILEMAP_H').toUpperCase();
    const out = [];
    out.push('/* Generated by ngpc_tilemap.py - do not edit */');
    out.push('');
    out.push(`#ifndef ${guard}`);
    out.push(`#define ${guard}`);
    out.push('');
    out.push('#include "ngpc_types.h"');
    out.push('');
    out.push(`extern const u16 ${name}_map_w;`);
    out.push(`extern const u16 ${name}_map_h;`);
    out.push(`extern const u16 ${name}_map_len;`);
    out.push('');
    out.push(`extern const u8 ${name}_palette_count;`);
    out.push(`extern const u16 NGP_FAR ${name}_palettes[];`);
    out.push('');
    out.push(`extern const u16 ${name}_tiles_count;`);
    out.push(`extern const u16 NGP_FAR ${name}_tiles[];`);
    out.push('');
    out.push(`extern const u16 NGP_FAR ${name}_map_tiles[];`);
    out.push(`extern const u8 NGP_FAR ${name}_map_pals[];`);
    out.push('');
    out.push(`#endif /* ${guard} */`);
    out.push('');
    return out.join('\n');
  }

  // Async helper: turn a File OR Blob into { data, width, height } by
  // drawing the PNG onto a hidden canvas and reading its ImageData.
  async function decodePng(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.decoding = 'async';
      img.src = url;
      await img.decode();
      const canvas = document.createElement('canvas');
      canvas.width  = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext('2d');
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(img, 0, 0);
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      return { data: imageData.data, width: canvas.width, height: canvas.height };
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  // Sibling entry that accepts raw bytes (ArrayBuffer / Uint8Array) instead of
  // a File. Useful from Web Workers (no File object on hand), from extensions
  // that already have the bytes in memory, and from any non-browser host that
  // can polyfill Blob + URL + Image (or, more realistically, swap the whole
  // function via its own decoder — see the MCP server's _png_decode.js for a
  // pure-pngjs alternative used in Node).
  async function decodePngFromBytes(bytes) {
    if (typeof Blob === 'undefined') {
      throw new Error(
        'decodePngFromBytes requires Blob (browser/Worker only). ' +
        'In Node, decode the PNG with pngjs and call exportSprite/exportTilemap directly.');
    }
    const buf = bytes instanceof ArrayBuffer ? bytes : bytes.buffer ?? bytes;
    return decodePng(new Blob([buf], { type: 'image/png' }));
  }

  return {
    decodePng,
    decodePngFromBytes,
    exportSprite,
    exportTilemap,
    sanitizeCIdentifier,
  };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_AssetTools = NGPC_AssetTools;
