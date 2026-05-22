import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_tilemap_info",
  description:
    "Decode one K2GE scroll-plane tilemap (SCR1 @ 0x9000 or SCR2 @ 0x9800, 32×32 tiles × 2 bytes/cell). Default returns a compact ASCII 32-wide grid; use --list for full per-tile decode (tile index, palette, h/v flip, priority). M2 Phase 0 read-only inspector. Use --seed-from to inspect tilemap state captured during a run. Backed by ngpc_emu.py `tilemap-info`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      plane: {
        type: "string",
        enum: ["scr1", "scr2"],
        default: "scr1",
        description: "Which scroll plane to inspect (default: scr1).",
      },
      non_empty: {
        type: "boolean",
        default: false,
        description: "Filter out tile-0 entries (the NGPC transparent / unused slot).",
      },
      list: {
        type: "boolean",
        default: false,
        description: "Print one line per tile instead of the compact grid view.",
      },
      seed_from: {
        type: "string",
        description:
          "Optional savestate JSON path. When set, its writable overlay is layered on top of the cold-start tilemap so cells written during the captured run are decoded.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({
  rom_path,
  plane = "scr1",
  non_empty = false,
  list = false,
  seed_from,
}) {
  const args = [rom_path, "--plane", plane];
  if (non_empty) args.push("--non-empty");
  if (list) args.push("--list");
  if (seed_from) args.push("--seed-from", seed_from);
  return await runEmu("tilemap-info", args);
}
