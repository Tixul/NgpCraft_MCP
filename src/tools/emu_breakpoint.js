import { runEmu } from "./_emu_bridge.js";

const ACTIONS = ["add", "add-symbol", "list", "remove", "clear", "check"];

export const definition = {
  name: "ngpc_emu_breakpoint",
  description:
    "Per-ROM PC-address breakpoint registry + event-log match. Six sub-actions: add (raw PC), add-symbol (resolve a function/label via a t900ld .map file, then add at the resolved PC), list, remove (by id), clear (all), check (match the registry against the PC values captured in an event-log v2 JSON). Breakpoints persist on disk under .ngpc_emu/breakpoints/. v1 is a post-run filter, not a live pause. Backed by ngpc_emu.py `breakpoint`.",
  inputSchema: {
    type: "object",
    properties: {
      action: { type: "string", enum: ACTIONS, description: "Sub-action to run." },
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      address: {
        type: "string",
        description: "[add] PC address to break on (decimal or 0x-prefixed hex).",
      },
      symbol: {
        type: "string",
        description: "[add-symbol] Exact symbol name to resolve (e.g. '_main', '_vblank').",
      },
      map: {
        type: "string",
        description: "[add-symbol] Path to the t900ld .map file used for symbol resolution.",
      },
      label: {
        type: "string",
        description: "[add | add-symbol] Optional human label. add-symbol defaults to the resolved symbol name.",
      },
      id: {
        type: "integer",
        description: "[remove] Breakpoint id to remove.",
      },
      event_log: {
        type: "string",
        description: "[check] Path to one event-log v2 JSON file.",
      },
    },
    required: ["action", "rom_path"],
  },
};

export async function handler({
  action,
  rom_path,
  address,
  symbol,
  map,
  label,
  id,
  event_log,
}) {
  if (!ACTIONS.includes(action)) {
    throw new Error(`Unknown action '${action}'. Expected one of: ${ACTIONS.join(", ")}.`);
  }
  const args = [action, rom_path];
  switch (action) {
    case "add":
      if (!address) throw new Error("breakpoint add requires `address`.");
      args.push(address);
      if (label) args.push("--label", label);
      break;
    case "add-symbol":
      if (!symbol) throw new Error("breakpoint add-symbol requires `symbol`.");
      if (!map) throw new Error("breakpoint add-symbol requires `map` (.map path).");
      args.push(symbol, "--map", map);
      if (label) args.push("--label", label);
      break;
    case "remove":
      if (id == null) throw new Error("breakpoint remove requires `id`.");
      args.push(String(id));
      break;
    case "check":
      if (!event_log) throw new Error("breakpoint check requires `event_log`.");
      args.push(event_log);
      break;
    case "list":
    case "clear":
      break;
  }
  return await runEmu("breakpoint", args);
}
