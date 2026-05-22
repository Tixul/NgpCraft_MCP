// Shared helper: spawn `python vendor/emulator/ngpc_emu.py <cmd> <args> --json`,
// parse stdout as JSON, return result. stderr is surfaced as diagnostic text.
//
// Requires Python 3 available as `python` or `python3` on PATH.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
export const EMU_ROOT = resolve(join(__dirname, "..", "..", "vendor", "emulator"));
export const EMU_SCRIPT = join(EMU_ROOT, "ngpc_emu.py");

function pickPython() {
  return process.platform === "win32" ? "python" : "python3";
}

export function runEmu(cmd, args = [], { timeoutMs = 30_000 } = {}) {
  return new Promise((res, rej) => {
    const py = pickPython();
    const child = spawn(py, [EMU_SCRIPT, cmd, ...args, "--json"], {
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
      if (timedOut) {
        return rej(new Error(`Emulator timed out after ${timeoutMs}ms`));
      }
      if (code !== 0) {
        return rej(
          new Error(
            `ngpc_emu.py ${cmd} exited ${code}\nstderr: ${err}\nstdout: ${out}`
          )
        );
      }
      try {
        res(JSON.parse(out));
      } catch (parseErr) {
        rej(
          new Error(
            `Failed to parse emulator JSON: ${parseErr.message}\nstdout:\n${out}\nstderr:\n${err}`
          )
        );
      }
    });
  });
}

// Many tools take optional --seed-xsp / --seed-reg NAME=VALUE (repeatable).
// Since 2026-05-20 the execution-capable tools also accept:
//   --seed-zero-bank0 : software-convention shortcut for XWA/XBC/XDE/XHL/XIX/XIY=0
//   --map <file>      : t900ld .map for symbol-aware final_symbol enrichment
export function buildSeedArgs({
  seed_xsp,
  seed_regs,
  seed_zero_bank0,
  map,
}) {
  const args = [];
  if (seed_xsp != null) args.push("--seed-xsp", String(seed_xsp));
  if (seed_regs && typeof seed_regs === "object") {
    for (const [k, v] of Object.entries(seed_regs)) {
      args.push("--seed-reg", `${k}=${v}`);
    }
  }
  if (seed_zero_bank0) args.push("--seed-zero-bank0");
  if (map) args.push("--map", String(map));
  return args;
}
