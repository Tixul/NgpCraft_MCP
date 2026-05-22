import { loadTranspiler } from "./_transpiler_loader.js";
import { decodePngBase64 } from "./_png_decode.js";

export const definition = {
  name: "ngpc_png_to_sprite",
  description:
    "Convert a base64 PNG to NGPC sprite C/H source via NGPC_AssetTools.exportSprite. Handles palette assignment + tile dedup + metasprite parts. Returns generated C source, H source, and a summary of frames/palettes/tiles.",
  inputSchema: {
    type: "object",
    properties: {
      png_base64: { type: "string" },
      name: {
        type: "string",
        description: "Symbol prefix (e.g. 'player', 'enemy_01'). Sanitized to a valid C identifier.",
      },
      frame_w: { type: "integer", description: "Frame width in pixels (typically 8/16/24/32)." },
      frame_h: { type: "integer", description: "Frame height in pixels." },
      frame_count: {
        type: "integer",
        description: "Number of animation frames laid out left-to-right. Omit for single-frame.",
      },
      tile_base: { type: "integer", default: 0, minimum: 0, maximum: 511 },
      pal_base: { type: "integer", default: 0, minimum: 0, maximum: 15 },
      anim_duration: { type: "integer", default: 6, minimum: 1, maximum: 255 },
      max_palettes: { type: "integer", default: 16, minimum: 1, maximum: 16 },
      dedupe: { type: "boolean", default: true },
    },
    required: ["png_base64", "name", "frame_w", "frame_h"],
  },
};

export async function handler({
  png_base64,
  name,
  frame_w,
  frame_h,
  frame_count,
  tile_base = 0,
  pal_base = 0,
  anim_duration = 6,
  max_palettes = 16,
  dedupe = true,
}) {
  const { AssetTools } = await loadTranspiler();
  const img = decodePngBase64(png_base64);
  const opts = {
    name,
    frameW: frame_w,
    frameH: frame_h,
    tileBase: tile_base,
    palBase: pal_base,
    animDuration: anim_duration,
    maxPalettes: max_palettes,
    dedupe,
  };
  if (frame_count != null) opts.frameCount = frame_count;
  try {
    const r = AssetTools.exportSprite(img.data, img.width, img.height, opts);
    return {
      ok: true,
      name,
      source_dimensions: { width: img.width, height: img.height },
      summary: r.summary,
      c_source: r.cSource,
      h_source: r.hSource,
    };
  } catch (err) {
    return { ok: false, message: err.message };
  }
}
