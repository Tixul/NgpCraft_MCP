// Lint every .c file in a project tree.
//
// Provides an `includeResolver` to NGPC_Interp.compile so headers in src/ and
// vendor/templates/base/src/ resolve correctly — without it, projects that
// `#include "ngpc_gfx.h"` would fail to lint with "missing include".

import { readFile, readdir, stat } from "node:fs/promises";
import { join, resolve, basename, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { loadTranspiler } from "./_transpiler_loader.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEMPLATE_HEADERS = resolve(
  join(__dirname, "..", "..", "vendor", "templates", "base", "src")
);

async function walkC(dir) {
  const out = [];
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (e) {
    return out;
  }
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) {
      out.push(...(await walkC(full)));
    } else if (e.isFile() && e.name.endsWith(".c")) {
      out.push(full);
    }
  }
  return out;
}

async function findHeader(name, searchDirs) {
  for (const d of searchDirs) {
    // Direct path match (preserves any subdir prefix in the include).
    const direct = join(d, name);
    try {
      const s = await stat(direct);
      if (s.isFile()) return direct;
    } catch {}
    // Recursive find by basename — many template headers live in subdirs
    // (core/, gfx/, fx/, audio/) but are included as bare "ngpc_xxx.h".
    const base = basename(name);
    const stack = [d];
    while (stack.length) {
      const cur = stack.pop();
      let entries;
      try { entries = await readdir(cur, { withFileTypes: true }); } catch { continue; }
      for (const e of entries) {
        const full = join(cur, e.name);
        if (e.isDirectory()) stack.push(full);
        else if (e.isFile() && e.name === base) return full;
      }
    }
  }
  return null;
}

function makeIncludeResolver(searchDirs) {
  // Cache resolved paths so the same header doesn't trigger N filesystem walks.
  const cache = new Map();
  return async (name) => {
    if (cache.has(name)) return cache.get(name);
    const path = await findHeader(name, searchDirs);
    let content = null;
    if (path) {
      try { content = await readFile(path, "utf8"); } catch {}
    }
    cache.set(name, content);
    return content;
  };
}

// NGPC_Interp.compile expects a synchronous resolver. We pre-resolve every
// `#include` recursively first, then pass a synchronous Map-backed lookup.
async function preloadIncludes(rootSrcs, searchDirs) {
  const resolver = makeIncludeResolver(searchDirs);
  const seen = new Map();
  const includeRe = /^\s*#\s*include\s+["<]([^">]+)[">]/gm;
  const queue = [...rootSrcs];
  while (queue.length) {
    const text = queue.shift();
    let m;
    const re = new RegExp(includeRe);
    while ((m = re.exec(text)) !== null) {
      const name = m[1];
      if (seen.has(name)) continue;
      const content = await resolver(name);
      seen.set(name, content);
      if (content) queue.push(content);
    }
  }
  return (name) => seen.get(name) ?? null;
}

export const definition = {
  name: "ngpc_validate_project",
  description:
    "Lint every .c file under a project directory. For each file, runs NGPC_Interp.compile() with an include resolver that searches the project's own src/ tree plus the bundled NgpCraft template headers. Returns aggregated counts (per file, per rule) and the full per-file error list. Use to validate StarGunner, the bundled template, or any new project before building.",
  inputSchema: {
    type: "object",
    properties: {
      project_dir: {
        type: "string",
        description:
          "Absolute path to the project root. Will scan recursively for .c files; headers are resolved against project subdirs + template/base/src/.",
      },
      include_template_headers: {
        type: "boolean",
        default: true,
        description:
          "If true, the resolver also searches vendor/templates/base/src/ so projects using the standard NgpCraft headers lint correctly.",
      },
      max_files: {
        type: "integer",
        default: 200,
        description: "Hard cap on .c files scanned (safety net on huge trees).",
      },
    },
    required: ["project_dir"],
  },
};

export async function handler({ project_dir, include_template_headers = true, max_files = 200 }) {
  const root = resolve(project_dir);
  const files = (await walkC(root)).slice(0, max_files);
  if (files.length === 0) {
    return { ok: false, message: `No .c files found under ${root}` };
  }

  const { Interp } = await loadTranspiler();

  const searchDirs = [root, join(root, "src")];
  if (include_template_headers) searchDirs.push(TEMPLATE_HEADERS);

  const reports = [];
  const ruleCounts = {};
  let totalErrors = 0;
  let cleanFiles = 0;

  for (const file of files) {
    const relName = file.substring(root.length + 1).replace(/\\/g, "/");
    let src;
    try { src = await readFile(file, "utf8"); }
    catch (e) {
      reports.push({ file: relName, ok: false, kind: "io_error", message: e.message });
      continue;
    }
    const resolver = await preloadIncludes([src], searchDirs);
    let err = null;
    try {
      Interp.compile(src, { includeResolver: resolver });
    } catch (e) {
      err = e;
    }
    if (!err) {
      reports.push({ file: relName, ok: true, errors: 0 });
      cleanFiles++;
      continue;
    }
    if (err.name === "HwFidelityError") {
      const errs = err.hwErrors ?? [];
      reports.push({
        file: relName,
        ok: false,
        kind: "hardware_fidelity",
        error_count: errs.length,
        rules: [...new Set(errs.map((e) => e.rule))],
        errors: errs,
      });
      totalErrors += errs.length;
      for (const e of errs) ruleCounts[e.rule] = (ruleCounts[e.rule] || 0) + 1;
    } else {
      reports.push({
        file: relName,
        ok: false,
        kind: "transpile_error",
        message: err.message,
      });
    }
  }

  return {
    ok: true,
    project_dir: root,
    files_scanned: files.length,
    files_clean: cleanFiles,
    files_with_errors: files.length - cleanFiles,
    total_lint_errors: totalErrors,
    by_rule: ruleCounts,
    reports,
  };
}
