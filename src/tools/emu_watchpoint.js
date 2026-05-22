import { runEmu } from "./_emu_bridge.js";

const ACTIONS = ["add", "list", "remove", "clear", "check"];
const KINDS = ["write", "read", "access"];

export const definition = {
  name: "ngpc_emu_watchpoint",
  description:
    "Per-ROM memory watchpoint registry + event-log match (v3 format). Five sub-actions: add (kind=write/read/access, optional byte-value filter), list, remove (by id), clear (all), check (match the registry against the memory writes/reads captured in an event-log JSON). Watchpoints persist on disk under .ngpc_emu/watchpoints/. v1 is a post-run filter, not a live break-on-hit. Backed by ngpc_emu.py `watchpoint`.",
  inputSchema: {
    type: "object",
    properties: {
      action: { type: "string", enum: ACTIONS, description: "Sub-action to run." },
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      address: {
        type: "string",
        description: "[add] Address to watch (decimal or 0x-prefixed hex).",
      },
      kind: {
        type: "string",
        enum: KINDS,
        default: "write",
        description: "[add] Access kind to watch (default: write).",
      },
      size: {
        type: "integer",
        minimum: 1,
        default: 1,
        description: "[add] Byte range starting at address (default 1).",
      },
      label: {
        type: "string",
        description: "[add] Optional human label for the watchpoint.",
      },
      value: {
        type: "string",
        description:
          "[add] Optional byte-value filter (decimal or 0x-prefixed hex, 0..255). Only fires when the first byte of the accessed range equals this value.",
      },
      id: {
        type: "integer",
        description: "[remove] Watchpoint id to remove.",
      },
      event_log: {
        type: "string",
        description: "[check] Path to one event-log v1+ JSON file.",
      },
    },
    required: ["action", "rom_path"],
  },
};

export async function handler({
  action,
  rom_path,
  address,
  kind = "write",
  size = 1,
  label,
  value,
  id,
  event_log,
}) {
  if (!ACTIONS.includes(action)) {
    throw new Error(`Unknown action '${action}'. Expected one of: ${ACTIONS.join(", ")}.`);
  }
  const args = [action, rom_path];
  switch (action) {
    case "add":
      if (!address) throw new Error("watchpoint add requires `address`.");
      args.push(address, "--kind", kind, "--size", String(size));
      if (label) args.push("--label", label);
      if (value != null) args.push("--value", String(value));
      break;
    case "remove":
      if (id == null) throw new Error("watchpoint remove requires `id`.");
      args.push(String(id));
      break;
    case "check":
      if (!event_log) throw new Error("watchpoint check requires `event_log`.");
      args.push(event_log);
      break;
    case "list":
    case "clear":
      break;
  }
  return await runEmu("watchpoint", args);
}
