import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_peek",
  description:
    "Read bytes from the emulated NGPC memory bus at a given address. Useful to inspect ROM contents, header fields, or known addresses. Backed by vendor/emulator/ngpc_emu.py — reliable for ROM-backed reads in the current minimal bus model.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string" },
      address: {
        type: "string",
        description: "Address as decimal or 0x-prefixed hex (e.g. '0x200000').",
      },
      count: { type: "integer", default: 16, minimum: 1, maximum: 4096 },
    },
    required: ["rom_path", "address"],
  },
};

export async function handler({ rom_path, address, count = 16 }) {
  const args = [rom_path, address, "--count", String(count)];
  return await runEmu("peek", args);
}
