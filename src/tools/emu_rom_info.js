import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_rom_info",
  description:
    "Parse an NGPC ROM (.ngp/.ngc) header and return the bootstrap reset state. Useful to verify a ROM is well-formed, read the cart title, entry point, and initial machine state. Backed by vendor/emulator/ngpc_emu.py — reliable for ROM parsing + reset state.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: {
        type: "string",
        description: "Absolute path to a .ngp or .ngc ROM file.",
      },
      include_reset: {
        type: "boolean",
        default: true,
        description: "If true, also return the bootstrap machine state (reset-info).",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({ rom_path, include_reset = true }) {
  const header = await runEmu("info", [rom_path]);
  if (!include_reset) return { header };
  const reset = await runEmu("reset-info", [rom_path]);
  return { header, reset };
}
