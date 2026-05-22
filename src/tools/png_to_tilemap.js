import { loadTranspiler } from "./_transpiler_loader.js";
import { decodePngBase64 } from "./_png_decode.js";

export const definition = {
  name: "ngpc_png_to_tilemap",
  description:
    "Convert a base64 PNG to NGPC tilemap C/H source via NGPC_AssetTools.exportTilemap. Auto-detects single-layer output when tiles use ≤3 visible colors per palette. Returns the C source (palette + tile set + map_tiles[] + map_pals[]), the H source, and a summary.",
  inputSchema: {
    type: "object",
    properties: {
      png_base64: { type: "string" },
      name: { type: "string" },
      max_palettes: { type: "integer", default: 16, minimum: 1, maximum: 16 },
      dedupe: { type: "boolean", default: true },
      black_is_transparent: {
        type: "boolean",
        default: false,
        description: "Treat pure black pixels as transparent (useful when source uses black as background).",
      },
    },
    required: ["png_base64", "name"],
  },
};

export async function handler({
  png_base64,
  name,
  max_palettes = 16,
  dedupe = true,
  black_is_transparent = false,
}) {
  const { AssetTools } = await loadTranspiler();
  const img = decodePngBase64(png_base64);
  try {
    const r = AssetTools.exportTilemap(img.data, img.width, img.height, {
      name,
      maxPalettes: max_palettes,
      dedupe,
      blackIsTransparent: black_is_transparent,
    });
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
