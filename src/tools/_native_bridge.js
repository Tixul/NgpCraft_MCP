// Shared helper: spawn `python vendor/emulator/ngpc_native.py <cmd> ... --json`.
//
// The sibling `_emu_bridge.js` drives ngpc_emu.py, which INSPECTS a ROM or a save
// state. This one drives the NATIVE core, which RUNS the machine: it advances real
// frames, holds real buttons, and draws the picture line by line as the beam passes.
// Two different questions, two different backends.
//
// Requires Python 3 on PATH and the compiled core at vendor/emulator/cpp/build/.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
export const EMU_ROOT = resolve(join(__dirname, "..", "..", "vendor", "emulator"));
export const NATIVE_SCRIPT = join(EMU_ROOT, "ngpc_native.py");

function pickPython() {
  return process.platform === "win32" ? "python" : "python3";
}

// Emulation is slower than inspection: a few hundred frames is seconds, not
// milliseconds, so the default budget is wider than the inspector bridge's.
export function runNative(cmd, args = [], { timeoutMs = 120_000 } = {}) {
  return new Promise((res, rej) => {
    const py = pickPython();
    const child = spawn(py, [NATIVE_SCRIPT, cmd, ...args, "--json"], {
      cwd: EMU_ROOT,
      windowsHide: true,
    });
    let out = "";
    let err = "";
    let timedOut = false;
    const to = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, timeoutMs);
    child.stdout.on("data", (d) => (out += d.toString()));
    child.stderr.on("data", (d) => (err += d.toString()));
    child.on("error", (e) => {
      clearTimeout(to);
      rej(new Error(`Failed to spawn Python (${py}): ${e.message}`));
    });
    child.on("close", (code) => {
      clearTimeout(to);
      if (timedOut) return rej(new Error(`Native core timed out after ${timeoutMs}ms`));
      if (code !== 0) {
        // The CLI says plainly when the core is not built; pass that through rather
        // than burying it, because it is the one failure a user can act on.
        return rej(new Error(`ngpc_native.py ${cmd} exited ${code}\nstderr: ${err}\nstdout: ${out}`));
      }
      try {
        res(JSON.parse(out));
      } catch (parseErr) {
        rej(new Error(`Failed to parse native JSON: ${parseErr.message}\nstdout:\n${out}\nstderr:\n${err}`));
      }
    });
  });
}
