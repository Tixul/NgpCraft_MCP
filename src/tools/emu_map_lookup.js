import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_map_lookup",
  description:
    "Resolve symbols from a t900ld .map file. Three modes: 'info' (total + per-section counts), 'name' (exact symbol name → address), 'addr' (PC → owning symbol via nearest-symbol-with-addr-<=-PC reverse lookup). Use 'addr' to name an emulator stop frontier, a trace record, or a hot PC. The map file is the authoritative source — the loader never fabricates symbols. Returns JSON with stable shape including the 'note' field documenting the reverse-lookup semantic.",
  inputSchema: {
    type: "object",
    properties: {
      map_path: { type: "string" },
      mode: {
        type: "string",
        enum: ["info", "name", "addr"],
        description:
          "info: section counts; name: symbol → addr; addr: PC → owning symbol",
      },
      query: {
        type: "string",
        description:
          "For mode='name': exact symbol name (e.g. '_shmup_update'). For mode='addr': PC value (decimal or 0x-prefixed hex). Ignored for mode='info'.",
      },
    },
    required: ["map_path", "mode"],
  },
};

export async function handler({ map_path, mode, query }) {
  const sub =
    mode === "info"
      ? ["info", map_path]
      : mode === "name"
      ? ["lookup-name", map_path, query]
      : ["lookup-addr", map_path, query];

  if (mode !== "info" && !query) {
    throw new Error(`mode='${mode}' requires a non-empty query`);
  }
  // ngpc_emu.py expects `map info <path>` / `map lookup-name <path> <name>` etc.
  return await runEmu("map", sub, { timeoutMs: 30_000 });
}
