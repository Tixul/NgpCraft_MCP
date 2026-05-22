import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_PATH = join(__dirname, "..", "data", "bugs_silicon.json");

let cache = null;
async function load() {
  if (!cache) cache = JSON.parse(await readFile(DATA_PATH, "utf8"));
  return cache;
}

export const definition = {
  name: "ngpc_bug_check",
  description:
    "Check a TLCS-900 opcode, mnemonic, ASM pattern, or C pattern against the NGPC silicon/toolchain bug database. Returns matching validated hardware bugs with symptoms and fixes. Use before emitting ASM or when explaining a crash. Example queries: 'D0', 'cpl WA', 'ei', 'link XIY', 'adc W,B', 'ldcw'.",
  inputSchema: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: "Opcode mnemonic, hex pattern, or keyword to search bug entries",
      },
      category: {
        type: "string",
        enum: [
          "cpu_silicon",
          "cpu_abi",
          "toolchain_compiler",
          "toolchain_assembler",
          "bios_behavior",
          "memory_layout",
          "abi_reference",
          "any",
        ],
        default: "any",
      },
    },
    required: ["query"],
  },
};

export async function handler({ query, category = "any" }) {
  const db = await load();
  const q = query.toLowerCase();
  const matches = db.bugs.filter((bug) => {
    if (category !== "any" && bug.category !== category) return false;
    const haystack = [
      bug.id,
      bug.title,
      bug.opcode_pattern,
      ...(bug.affected_instructions ?? []),
      ...(bug.symptoms ?? []),
      ...(bug.notes ?? []),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
  if (matches.length === 0) {
    return {
      query,
      matches: [],
      hint: "No known bug matches. Either the pattern is safe, or it hasn't been validated on hardware yet.",
    };
  }
  return {
    query,
    match_count: matches.length,
    matches,
  };
}
