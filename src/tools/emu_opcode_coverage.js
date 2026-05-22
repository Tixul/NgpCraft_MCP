import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_opcode_coverage",
  description:
    "Linear-walk a ROM from its entry point and report which leading-byte opcodes the current TLCS-900/H decoder does NOT yet handle. Used to prioritize executor expansion work for HW fidelity ('measure before implementing' doctrine). Returns coverage byte %, top unknown-opcode entries, and per-byte census. Backed by ngpc_emu.py `opcode-coverage`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      start: {
        type: "string",
        description:
          "Start address (decimal or 0x-prefixed hex). Defaults to the cart header entry point.",
      },
      bytes: {
        type: "integer",
        default: 2048,
        minimum: 1,
        maximum: 65536,
        description: "Walk budget in bytes (default 2048).",
      },
      top: {
        type: "integer",
        default: 15,
        minimum: 1,
        maximum: 256,
        description: "Number of top unknown-opcode entries to return (default 15).",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({ rom_path, start, bytes = 2048, top = 15 }) {
  const args = [rom_path, "--bytes", String(bytes), "--top", String(top)];
  if (start) args.push("--start", start);
  return await runEmu("opcode-coverage", args);
}
