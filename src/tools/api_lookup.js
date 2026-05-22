// Extracts function declarations from vendor/templates/base/src/**/*.h on first call.
// Parses C prototypes via regex — adequate for NgpCraft headers (simple style, no macros in signatures).

import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const HEADERS_ROOT = join(
  __dirname,
  "..",
  "..",
  "vendor",
  "templates",
  "base",
  "src"
);

let apiIndex = null;

async function walk(dir) {
  const out = [];
  const entries = await readdir(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) out.push(...(await walk(full)));
    else if (e.name.endsWith(".h")) out.push(full);
  }
  return out;
}

// Regex for a C function prototype ending in ';' — captures return type, name, params.
// Skips typedefs, #defines, macros, comments.
const PROTOTYPE_RE =
  /^([\w\s\*]+?)\s+(\w+)\s*\(([^;{}]*)\)\s*;/gm;

function extractProtos(text, file) {
  // strip block comments + line comments (rough but ok for our headers)
  const stripped = text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
  const out = [];
  let m;
  const re = new RegExp(PROTOTYPE_RE);
  while ((m = re.exec(stripped))) {
    const retType = m[1].trim();
    const name = m[2];
    const params = m[3].trim();
    // Filter obvious non-functions
    if (["if", "while", "for", "switch", "return"].includes(name)) continue;
    if (retType.startsWith("#") || retType.startsWith("typedef")) continue;
    out.push({
      name,
      return_type: retType,
      params,
      signature: `${retType} ${name}(${params});`,
      header: file,
    });
  }
  return out;
}

async function buildIndex() {
  const files = await walk(HEADERS_ROOT);
  const out = [];
  for (const f of files) {
    const rel = f.substring(HEADERS_ROOT.length + 1).replace(/\\/g, "/");
    const text = await readFile(f, "utf8");
    out.push(...extractProtos(text, rel));
  }
  return out;
}

async function getIndex() {
  if (!apiIndex) apiIndex = await buildIndex();
  return apiIndex;
}

export const definition = {
  name: "ngpc_api_lookup",
  description:
    "Look up NgpCraft template API function signatures extracted from vendor/templates/base/src/**/*.h. Returns matching function(s) with their signature and header file. Use to avoid hallucinating parameter order or types.",
  inputSchema: {
    type: "object",
    properties: {
      name: {
        type: "string",
        description:
          "Function name or substring (e.g. 'ngpc_gfx_set_palette', 'dma', 'load_tiles'). Regex-safe.",
      },
      exact: {
        type: "boolean",
        default: false,
        description: "If true, match the function name exactly.",
      },
      limit: {
        type: "integer",
        default: 20,
      },
    },
    required: ["name"],
  },
};

export async function handler({ name, exact = false, limit = 20 }) {
  const idx = await getIndex();
  const matches = idx
    .filter((p) =>
      exact ? p.name === name : p.name.toLowerCase().includes(name.toLowerCase())
    )
    .slice(0, limit);
  return {
    query: name,
    match_count: matches.length,
    api: matches,
  };
}
