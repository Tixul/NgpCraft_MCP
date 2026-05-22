import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_oam_info",
  description:
    "Decode the K2GE OAM (0x8800..0x88FF, 64 sprites × 4 bytes) and the CP.C palette-code strip (0x8C00..0x8C3F). Returns per-sprite tile, position, flip, priority code, chain bits and palette index. M2 Phase 0 read-only inspector — no rendering. Use --seed-from to inspect OAM state captured during a run. Backed by ngpc_emu.py `oam-info`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      visible_only: {
        type: "boolean",
        default: false,
        description: "Filter out sprites whose priority code (PR.C) is 0 (hidden).",
      },
      seed_from: {
        type: "string",
        description:
          "Optional savestate JSON path. When set, its writable overlay is layered on top of the cold-start OAM/CP.C image so cells written during the captured run are decoded.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({ rom_path, visible_only = false, seed_from }) {
  const args = [rom_path];
  if (visible_only) args.push("--visible-only");
  if (seed_from) args.push("--seed-from", seed_from);
  return await runEmu("oam-info", args);
}
