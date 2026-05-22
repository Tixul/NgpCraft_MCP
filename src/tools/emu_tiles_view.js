import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runEmu } from "./_emu_bridge.js";
import { ppmFileToPng } from "./_ppm_to_png.js";

export const definition = {
  name: "ngpc_emu_tiles_view",
  description:
    "Render a grid of CHAR_RAM tiles as a binary PPM atlas, returned as PNG base64. Multi-tile bridge between tile-view (single ASCII tile) and the full framebuffer compose. Use --range to scope a slice (e.g. '0..63'), --cols for grid width, --plane+--palette for color (else 4-level grayscale). Backed by ngpc_emu.py `tiles-view`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      range: {
        type: "string",
        description:
          "Tile range as 'N..M' (inclusive, decimal or 0x-hex) or a single tile id N. Default: 0..511 (full CHAR_RAM).",
      },
      cols: {
        type: "integer",
        default: 16,
        minimum: 1,
        maximum: 64,
        description: "Tiles per row in the atlas grid (default 16).",
      },
      plane: {
        type: "string",
        enum: ["sprite", "scr1", "scr2"],
        description: "Palette plane for colorisation (default: 4-level grayscale).",
      },
      palette: {
        type: "integer",
        minimum: 0,
        maximum: 15,
        description: "Palette index 0..15 within the selected --plane.",
      },
      seed_from: {
        type: "string",
        description:
          "Optional savestate JSON path. When set, its writable overlay is layered on the cold-start CHAR_RAM image so tiles loaded by the captured run are decoded.",
      },
      output: {
        type: "string",
        description:
          "Optional explicit PPM path. When set, also persisted alongside the returned PNG.",
      },
      include_png_base64: {
        type: "boolean",
        default: true,
        description:
          "If true (default), the returned object includes a PNG base64 of the atlas. Set false for metadata-only.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({
  rom_path,
  range,
  cols = 16,
  plane,
  palette,
  seed_from,
  output,
  include_png_base64 = true,
}) {
  const td = await mkdtemp(join(tmpdir(), "ngpc-tiles-"));
  const ppmPath = output ?? join(td, "tiles.ppm");
  const args = [rom_path, "--cols", String(cols), "--output", ppmPath];
  if (range) args.push("--range", range);
  if (plane) args.push("--plane", plane);
  if (palette != null) args.push("--palette", String(palette));
  if (seed_from) args.push("--seed-from", seed_from);
  try {
    const meta = await runEmu("tiles-view", args, { timeoutMs: 120_000 });
    let png = null;
    if (include_png_base64) png = await ppmFileToPng(ppmPath);
    return {
      ...meta,
      ppm_path: ppmPath,
      ...(png
        ? { width: png.width, height: png.height, png_base64: png.png_base64 }
        : {}),
    };
  } finally {
    // Only sweep the temp dir we created. If the user passed --output we leave their file alone.
    if (!output) {
      try {
        await rm(td, { recursive: true, force: true });
      } catch {}
    }
  }
}
