// Grep across vendor/examples/stargunner/src/ (+ windcup_re) for feature-specific patterns.
// v0.1 = keyword-based. Future: curated feature→file/line table.

import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const EXAMPLES_ROOT = join(__dirname, "..", "..", "vendor", "examples");

async function walk(dir) {
  const out = [];
  const entries = await readdir(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) out.push(...(await walk(full)));
    else if (/\.(c|h)$/.test(e.name)) out.push(full);
  }
  return out;
}

let cache = null;
async function getFiles() {
  if (!cache) cache = await walk(EXAMPLES_ROOT);
  return cache;
}

export const definition = {
  name: "ngpc_example",
  description:
    "Search proven example code (StarGunner full shmup, Windcup reverse-eng) for real-world NGPC game patterns. Use to see how a finished game implements: DMA OAM flush, mapstream scroll, AABB collision, state machine, enemy waves, flash save, intro sequence, etc.",
  inputSchema: {
    type: "object",
    properties: {
      feature: {
        type: "string",
        description:
          "Feature keyword (e.g. 'dma', 'aabb', 'wave', 'state_machine', 'flash_save', 'scroll', 'intro')",
      },
      top: {
        type: "integer",
        default: 10,
      },
    },
    required: ["feature"],
  },
};

export async function handler({ feature, top = 10 }) {
  const files = await getFiles();
  const term = feature.toLowerCase();
  const hits = [];
  for (const f of files) {
    const rel = f.substring(EXAMPLES_ROOT.length + 1).replace(/\\/g, "/");
    const text = await readFile(f, "utf8");
    const lines = text.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].toLowerCase().includes(term)) {
        const start = Math.max(0, i - 2);
        const end = Math.min(lines.length, i + 10);
        hits.push({
          file: rel,
          line: i + 1,
          excerpt: lines.slice(start, end).join("\n"),
        });
      }
    }
  }
  return {
    feature,
    hit_count: hits.length,
    results: hits.slice(0, top),
  };
}
