import { runEmu } from "./_emu_bridge.js";

const KINDS = ["all", "sprite", "scr1", "scr2", "background", "window"];

export const definition = {
  name: "ngpc_emu_palette_info",
  description:
    "Decode the K2GE palette RAM (0x8200..0x83FF) into a human view: 16 palettes × 4 entries (12-bit 0BGR) for each plane (sprite / SCR1 / SCR2 / background / window). M2 Phase 0 read-only inspector — no rendering. Use --seed-from to inspect palette state captured during a run. Backed by ngpc_emu.py `palette-info`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      kind: {
        type: "string",
        enum: KINDS,
        default: "all",
        description: "Which palette plane to print (default: all five).",
      },
      seed_from: {
        type: "string",
        description:
          "Optional savestate JSON path. When set, its writable overlay is layered on top of the cold-start palette image so cells written during the captured run are decoded.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({ rom_path, kind = "all", seed_from }) {
  const args = [rom_path, "--kind", kind];
  if (seed_from) args.push("--seed-from", seed_from);
  return await runEmu("palette-info", args);
}
