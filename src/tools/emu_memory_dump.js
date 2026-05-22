import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_memory_dump",
  description:
    "Hexdump-style multi-row memory inspector. Reads through the read bus (cold-start + ROM + erased flash) and optionally overlays a savestate's writable cells. Returns rows of hex + ASCII (with '??'/'.'  for unbacked bytes). Pairs cleanly with watchpoints — when a WP fires, dump cells around the address. Backed by ngpc_emu.py `memory-dump`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      address: {
        type: "string",
        description: "Base address as decimal or 0x-prefixed hex (e.g. '0x200000').",
      },
      count: {
        type: "integer",
        default: 64,
        minimum: 1,
        maximum: 8192,
        description: "Number of bytes to dump (default 64).",
      },
      width: {
        type: "integer",
        default: 16,
        minimum: 1,
        maximum: 64,
        description: "Bytes per row (default 16; 8 or 16 give the nicest grouping).",
      },
      seed_from: {
        type: "string",
        description:
          "Optional savestate JSON path. When set, its writable overlay is layered on top of the read bus so cells written during the captured run shadow the cold-start image.",
      },
    },
    required: ["rom_path", "address"],
  },
};

export async function handler({ rom_path, address, count = 64, width = 16, seed_from }) {
  const args = [
    rom_path,
    address,
    "--count",
    String(count),
    "--width",
    String(width),
  ];
  if (seed_from) args.push("--seed-from", seed_from);
  return await runEmu("memory-dump", args);
}
