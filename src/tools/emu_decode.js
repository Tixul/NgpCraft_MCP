import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_decode",
  description:
    "Decode one TLCS-900 instruction from the ROM at the given address (or current bootstrap PC if omitted). Returns mnemonic, operand bytes, length, and any decoder annotations. Backed by vendor/emulator/ngpc_emu.py — opcode coverage is partial but honest (the decoder either decodes correctly or refuses).",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string" },
      address: {
        type: "string",
        description:
          "Optional address (decimal or 0x-prefixed hex). Defaults to bootstrap PC.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({ rom_path, address }) {
  const args = [rom_path];
  if (address) args.push("--address", address);
  return await runEmu("decode-next", args);
}
