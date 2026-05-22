// Simple grep-based doc search across corpus/. Each markdown file is split into
// ~200-line chunks; matches are scored by term count. v0.1 is intentionally simple —
// upgrade path: sqlite FTS5 or local ONNX embeddings (see scripts/build_index.js).

import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CORPUS_ROOT = join(__dirname, "..", "..", "corpus");

let index = null;

async function walk(dir) {
  const out = [];
  const entries = await readdir(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) out.push(...(await walk(full)));
    else if (e.name.endsWith(".md")) out.push(full);
  }
  return out;
}

function chunk(text, size = 60) {
  const lines = text.split("\n");
  const chunks = [];
  for (let i = 0; i < lines.length; i += size) {
    chunks.push({
      start_line: i + 1,
      end_line: Math.min(i + size, lines.length),
      text: lines.slice(i, i + size).join("\n"),
    });
  }
  return chunks;
}

async function buildIndex() {
  const files = await walk(CORPUS_ROOT);
  const docs = [];
  for (const f of files) {
    const rel = f.substring(CORPUS_ROOT.length + 1).replace(/\\/g, "/");
    const text = await readFile(f, "utf8");
    for (const c of chunk(text)) {
      docs.push({
        file: rel,
        start_line: c.start_line,
        end_line: c.end_line,
        text: c.text,
      });
    }
  }
  return docs;
}

async function getIndex() {
  if (!index) index = await buildIndex();
  return index;
}

function scoreChunk(text, terms) {
  const low = text.toLowerCase();
  let score = 0;
  for (const t of terms) {
    const re = new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
    const matches = low.match(re);
    if (matches) score += matches.length;
  }
  return score;
}

export const definition = {
  name: "ngpc_doc_search",
  description:
    "Full-text search across the NGPC dev wiki corpus (wiki/ — hardware registers, CPU/toolchain, graphics, audio, systems, patterns; plus DENSE_INDEX.md). Returns top-ranked chunks with file path and line numbers. Use when you need hardware-specific knowledge (DMA, palettes, interrupts, BIOS, audio format, collision, etc.).",
  inputSchema: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: "Keywords or short phrase to search",
      },
      top: {
        type: "integer",
        default: 5,
        description: "Max number of chunks to return",
      },
      file_filter: {
        type: "string",
        description:
          "Optional glob-style substring filter on file path (e.g. 'wiki/', '03_Graphics', 'DMA')",
      },
    },
    required: ["query"],
  },
};

export async function handler({ query, top = 5, file_filter }) {
  const idx = await getIndex();
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const filtered = file_filter
    ? idx.filter((d) => d.file.toLowerCase().includes(file_filter.toLowerCase()))
    : idx;
  const scored = filtered
    .map((d) => ({ ...d, score: scoreChunk(d.text, terms) }))
    .filter((d) => d.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, top);
  return {
    query,
    hit_count: scored.length,
    results: scored.map((s) => ({
      file: s.file,
      lines: `${s.start_line}-${s.end_line}`,
      score: s.score,
      excerpt: s.text.length > 2000 ? s.text.slice(0, 2000) + "\n...[truncated]" : s.text,
    })),
  };
}
