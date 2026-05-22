import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_registers",
  description:
    "Rich CPU register view: 8 R32 with their R16/R8 decomposition (XWA → WA → W/A …), PC, SR, IFF level, RFP bank pointer, and the six modeled flags (S/Z/V/H/C/N). Pairs cleanly with memory-dump and breakpoint check. Use --seed-from to inspect register state from a savestate captured during a run. Backed by ngpc_emu.py `registers`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      seed_from: {
        type: "string",
        description:
          "Optional savestate JSON path. When set, CPU state is loaded from the savestate (ROM hash verified) instead of the bootstrap reset state.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({ rom_path, seed_from }) {
  const args = [rom_path];
  if (seed_from) args.push("--seed-from", seed_from);
  return await runEmu("registers", args);
}
