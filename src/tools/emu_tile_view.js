import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_tile_view",
  description:
    "Render one 8×8 tile from CHAR_RAM as 4-level grayscale ASCII art (` ░▒█`). M2 Phase 0.5 — first visual lens on the emulator. Optional --plane + --palette annotates each pixel with the resolved K2GE RGB color. Pairs cleanly with palette-info (pick the palette index first). Backed by ngpc_emu.py `tile-view`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      tile_id: {
        type: "string",
        description: "Tile index in CHAR_RAM (decimal or 0x-prefixed hex, 0..511).",
      },
      plane: {
        type: "string",
        enum: ["sprite", "scr1", "scr2"],
        description: "Palette plane to colorise pixels with (default: no colorisation).",
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
          "Optional savestate JSON path. When set, its writable overlay is layered on top of the cold-start CHAR_RAM image so tiles loaded by the captured run are decoded.",
      },
    },
    required: ["rom_path", "tile_id"],
  },
};

export async function handler({ rom_path, tile_id, plane, palette, seed_from }) {
  const args = [rom_path, String(tile_id)];
  if (plane) args.push("--plane", plane);
  if (palette != null) args.push("--palette", String(palette));
  if (seed_from) args.push("--seed-from", seed_from);
  return await runEmu("tile-view", args);
}
