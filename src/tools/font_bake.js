// Font baking is currently NOT exposed as a single library-callable function in
// the Live Editor (font.js only decodes the embedded BIOS font; it doesn't
// generate one from a PNG sheet). The bake pipeline is documented in
// memory/dialog_display_guide.md (NGPC 2bpp + transform format) but no public
// wrapper exists yet.
//
// v0.3 path: extract the bake algorithm from the user's documented script
// (ngpc_font_data.c generation) into a callable function under src/data/ or
// vendor/transpiler/, then wire it here.

export const definition = {
  name: "ngpc_font_bake",
  description:
    "Convert a PNG font sheet to NGPC ngpc_font_data.c (2bpp + transform format). [v0.2 status: stub — the live editor exposes a decoder for the embedded BIOS font but no public encoder. See memory/dialog_display_guide.md for the format spec; baking will land in v0.3.]",
  inputSchema: {
    type: "object",
    properties: {
      png_base64: { type: "string" },
      glyph_width: { type: "integer", default: 8 },
      glyph_height: { type: "integer", default: 8 },
    },
    required: ["png_base64"],
  },
};

export async function handler({ png_base64, glyph_width = 8, glyph_height = 8 }) {
  return {
    ok: false,
    status: "not_implemented_in_v0.2",
    message:
      "Font bake pipeline (PNG → ngpc_font_data.c, 2bpp + transform) is documented in memory/dialog_display_guide.md but has no callable public wrapper yet. Use the documented script directly. Will land in v0.3.",
    received: {
      png_bytes_estimated: Math.floor((png_base64.length * 3) / 4),
      glyph_width,
      glyph_height,
    },
  };
}
