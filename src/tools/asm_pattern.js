import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_PATH = join(__dirname, "..", "data", "asm_patterns.json");

let cache = null;
async function load() {
  if (!cache) cache = JSON.parse(await readFile(DATA_PATH, "utf8"));
  return cache;
}

export const definition = {
  name: "ngpc_asm_pattern",
  description:
    "Retrieve a canonical ASM pattern validated on NGPC hardware. These are the hardware-safe replacements for textbook TLCS-900 sequences (which often hit silicon bugs). Use when emitting ASM. Call with no name to list all pattern names.",
  inputSchema: {
    type: "object",
    properties: {
      name: {
        type: "string",
        description:
          "Pattern name (e.g. 'function_prologue_safe', 'vblank_wait', 'enable_interrupts_vblank', 'block_copy_ldirw'). Omit to list all.",
      },
    },
  },
};

export async function handler({ name }) {
  const db = await load();
  if (!name) {
    return {
      available_patterns: db.patterns.map((p) => ({
        name: p.name,
        context: p.context,
      })),
    };
  }
  const match = db.patterns.find((p) => p.name === name);
  if (!match) {
    return {
      error: `Unknown pattern: ${name}`,
      available: db.patterns.map((p) => p.name),
    };
  }
  return match;
}
