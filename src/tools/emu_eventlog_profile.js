import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_eventlog_profile",
  description:
    "Bucket an event-log v1 JSON file by owning symbol via a t900ld .map. Returns per-symbol counters: total_events, executed_events, halted_events, first/last PC observed, min/max offset inside the symbol. Sorted by descending total. Halted statuses are summarized separately. Use case: given a captured execution trace, see WHICH functions consumed the cycles — the first concrete diagnostic primitive for compiler hot-spot identification. The event-log file is produced upstream by `eventlog capture`; this command is a static, read-only analysis on top of it.",
  inputSchema: {
    type: "object",
    properties: {
      input: {
        type: "string",
        description: "Path to an event-log v1 JSON file (from `eventlog capture`).",
      },
      map: {
        type: "string",
        description: "Path to the t900ld .map for the build that produced the event log.",
      },
      rom: {
        type: "string",
        description:
          "Optional path to the ROM. When set, the ROM's sha256 is verified against the value stored in the event log.",
      },
      top: {
        type: "integer",
        default: 20,
        minimum: 0,
        description:
          "How many top buckets to return (default 20). Pass 0 for all distinct symbols hit.",
      },
    },
    required: ["input", "map"],
  },
};

export async function handler({ input, map, rom, top = 20 }) {
  const args = ["profile", input, "--map", map, "--top", String(top)];
  if (rom) args.push("--rom", rom);
  return await runEmu("eventlog", args, { timeoutMs: 60_000 });
}
