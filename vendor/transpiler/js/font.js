// 8x8 ASCII font — baked at build time by sync_font.py.
//
// The byte data lives in js/font_data.js as hex strings. We parse each entry
// once into a Uint8Array of 16 bytes matching the NGPC 2bpp packed tile layout
// (K2GETechRef §4-3-5-1). Tile data uses color index 1 for lit pixels and 0
// for background (transparent on scroll planes per K2GE).
//
// On real hardware the BIOS SYSFONTSET loads a proprietary font we can't
// redistribute; our substitute is rendered from Liberation Mono at 8px. The
// API contract — `ngpc_load_sysfont` writes ASCII glyphs to tile RAM with
// tile index == ASCII code — is identical to the template's `ngpc_text.c`.

const NGPC_Font = (() => {
  const ASCII_MIN = 0x20;
  const ASCII_MAX = 0x7F;

  // Cache the parsed tiles so we only hex-decode once per page load.
  let cache = null;
  function get() {
    if (cache) return cache;
    cache = new Map();
    for (const [key, hex] of Object.entries(NGPC_FONT_DATA)) {
      const cp = Number(key);
      const bytes = new Uint8Array(16);
      for (let i = 0; i < 16; i++) {
        bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
      }
      cache.set(cp, bytes);
    }
    return cache;
  }

  return { get, ASCII_MIN, ASCII_MAX };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_Font = NGPC_Font;
