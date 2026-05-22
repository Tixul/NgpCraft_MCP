// Loads the NgpCraft Live Editor JS modules into a Node `vm` context so we can
// call interpreter.compile() / interpreter.run() server-side.
//
// The modules are bare IIFEs that assign top-level consts (NGPC_Interp,
// NGPC_Runtime, NGPC_Memory, NGPC_AssetTools, NGPC_Font, NGPC_FONT_DATA,
// NGPC_PROJECT_DATA). We run them all in one shared vm context so the
// interpreter can `typeof NGPC_Runtime !== 'undefined'`, etc.
//
// vdp.js is NOT loaded here — it depends on Canvas. Use the dedicated
// `vdp_render.js` helper for headless framebuffer extraction.
// psg.js is NOT loaded here — depends on AudioContext.

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TRANSPILER_JS = join(
  __dirname,
  "..",
  "..",
  "vendor",
  "transpiler",
  "js"
);

// Order matters: dependencies first.
const LOAD_ORDER = [
  "api.js",        // NGPC_API constant table — interpreter buildEnv depends on it
  "memory.js",
  "runtime.js",
  "psg.js",        // PSG state model — Node-safe (init() guards on `window`); event log usable headless
  "font_data.js",
  "font.js",
  "interpreter.js",
  "asset_tools.js",
  "vdp.js",        // framebuffer renderer; renderToPixels() = headless path (no canvas needed)
];

let cached = null;

export async function loadTranspiler() {
  if (cached) return cached;
  const sandbox = {
    console,
    // Some modules use `globalThis` self-reference.
    globalThis: undefined, // assigned below to point at sandbox itself
    // performance is used in interpreter.js for budget tracking.
    performance: { now: () => Date.now() },
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  for (const file of LOAD_ORDER) {
    const path = join(TRANSPILER_JS, file);
    const src = await readFile(path, "utf8");
    // Each module ends with `if (typeof globalThis !== 'undefined') globalThis.NGPC_X = NGPC_X;`
    // (added in live editor v0.2 cleanup), so bindings propagate to the vm
    // sandbox through globalThis without any rewrite needed.
    vm.runInContext(src, ctx, { filename: file });
  }
  cached = {
    ctx,
    sandbox,
    Interp: sandbox.NGPC_Interp,
    Runtime: sandbox.NGPC_Runtime,
    Memory: sandbox.NGPC_Memory,
    Font: sandbox.NGPC_Font,
    AssetTools: sandbox.NGPC_AssetTools,
    PROJECT_DATA: sandbox.NGPC_PROJECT_DATA,
    VDP: sandbox.NGPC_VDP,
    PSG: sandbox.NGPC_PSG,
  };
  return cached;
}
