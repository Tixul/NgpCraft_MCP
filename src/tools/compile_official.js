// Wrapper around the user's local Toshiba toolchain (cc900 + asm900 + tulink
// + tuconv + s242ngp). The .exe files MUST stay on the user's PC — this tool
// only invokes them by path, never bundles or redistributes them.
//
// Default toolchain location is C:\t900\BIN (the standard Toshiba install
// path). Override via the `thome` arg or the THOME environment variable.

import { spawn } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";

const DEFAULT_THOME = process.env.THOME || "C:\\t900";
// Proprietary system.lib is user-provided; no default path is bundled.
// Set it via the SYSTEM_LIB argument or the NGPC_SYSTEM_LIB env var.
const DEFAULT_SYSTEM_LIB = process.env.NGPC_SYSTEM_LIB ?? "";

function pickMake() {
  // Prefer the make bundled with t900 to avoid version mismatch with system make.
  return process.platform === "win32"
    ? "C:\\t900\\BIN\\make.exe"
    : "make";
}

function runMake(projectDir, target, env, extraArgs, { timeoutMs = 300_000 } = {}) {
  return new Promise((res) => {
    const args = [target, ...extraArgs];
    const child = spawn(pickMake(), args, {
      cwd: projectDir,
      env,
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
      res({ exit: -1, out, err: `${err}\nspawn error: ${e.message}`, timedOut });
    });
    child.on("close", (exit) => {
      clearTimeout(to);
      res({ exit, out, err, timedOut });
    });
  });
}

async function findRom(projectDir, expectedName) {
  // Most NgpCraft Makefiles emit to bin/<NAME>.ngp + bin/<NAME>.ngc.
  // expectedName is a hint — we also fall back to whichever .ngp is present.
  const binDir = join(projectDir, "bin");
  const candidates = [];
  if (expectedName) {
    candidates.push(join(binDir, `${expectedName}.ngc`));
    candidates.push(join(binDir, `${expectedName}.ngp`));
  }
  candidates.push(join(binDir, "main.ngc"));
  candidates.push(join(binDir, "main.ngp"));
  for (const c of candidates) {
    try {
      const s = await stat(c);
      if (s.isFile() && s.size > 0) return { path: c, size: s.size };
    } catch {}
  }
  return null;
}

export const definition = {
  name: "ngpc_compile_official",
  description:
    "Compile an NGPC project using the LOCAL Toshiba toolchain (cc900 + asm900 + tulink + tuconv + s242ngp) by invoking `make` in the given project directory. Returns the produced ROM (size + optional base64) + the full build log. Requires the Toshiba toolchain installed on the host (typically C:\\t900\\BIN). The .exe files stay on the user's PC — never bundled or redistributed by this tool.",
  inputSchema: {
    type: "object",
    properties: {
      project_dir: {
        type: "string",
        description: "Absolute path to the project root (must contain a Makefile that drives the toolchain).",
      },
      target: {
        type: "string",
        default: "all",
        description: "Make target to invoke (default 'all').",
      },
      thome: {
        type: "string",
        description:
          "Override the Toshiba toolchain root (env THOME). Default: C:\\t900 (or env THOME if already set).",
      },
      system_lib: {
        type: "string",
        description:
          "Path to the proprietary system.lib (passed as SYSTEM_LIB= to make). User-provided; defaults to the NGPC_SYSTEM_LIB env var, else none.",
      },
      extra_make_args: {
        type: "array",
        items: { type: "string" },
        description: "Extra arguments appended to the make command (e.g. ['NGP_ENABLE_DMA=0']).",
      },
      include_rom_base64: {
        type: "boolean",
        default: false,
        description:
          "If true, include the produced ROM as base64 in the response. Off by default to keep results small.",
      },
      rom_name_hint: {
        type: "string",
        description:
          "Filename stem to look for in bin/ (e.g. 'stargunner'). Defaults to 'main' (the standard Makefile NAME).",
      },
      timeout_ms: {
        type: "integer",
        default: 300000,
        description: "Hard timeout for the make invocation.",
      },
    },
    required: ["project_dir"],
  },
};

export async function handler({
  project_dir,
  target = "all",
  thome,
  system_lib,
  extra_make_args = [],
  include_rom_base64 = false,
  rom_name_hint,
  timeout_ms = 300_000,
}) {
  // Verify the project dir + Makefile exist before spawning anything.
  try {
    const s = await stat(project_dir);
    if (!s.isDirectory()) {
      return { ok: false, message: `Not a directory: ${project_dir}` };
    }
  } catch {
    return { ok: false, message: `Project directory not found: ${project_dir}` };
  }
  try {
    await stat(join(project_dir, "Makefile"));
  } catch {
    try { await stat(join(project_dir, "makefile")); }
    catch {
      return {
        ok: false,
        message: `No Makefile in ${project_dir}. The official toolchain wrapper requires a Makefile that drives cc900/asm900/tulink/tuconv/s242ngp.`,
      };
    }
  }

  const resolvedThome = thome ?? process.env.THOME ?? DEFAULT_THOME;
  const resolvedSysLib = system_lib ?? process.env.NGPC_SYSTEM_LIB ?? DEFAULT_SYSTEM_LIB;

  // Sanity-check the toolchain is actually present at THOME/BIN.
  try {
    await stat(join(resolvedThome, "BIN", "cc900.exe"));
  } catch {
    return {
      ok: false,
      message:
        `Toshiba cc900.exe not found at ${resolvedThome}\\BIN\\cc900.exe. ` +
        `Pass thome="C:\\path\\to\\t900" or set THOME env var.`,
    };
  }

  const env = {
    ...process.env,
    THOME: resolvedThome,
    PATH: `${join(resolvedThome, "BIN")};${process.env.PATH ?? ""}`,
  };
  // Pass SYSTEM_LIB to make if the user provided one (or we have a default).
  const makeArgs = [...extra_make_args];
  if (resolvedSysLib) makeArgs.push(`SYSTEM_LIB=${resolvedSysLib}`);

  const t0 = Date.now();
  const { exit, out, err, timedOut } = await runMake(
    project_dir,
    target,
    env,
    makeArgs,
    { timeoutMs: timeout_ms }
  );
  const elapsedMs = Date.now() - t0;

  if (timedOut) {
    return {
      ok: false,
      kind: "timeout",
      elapsed_ms: elapsedMs,
      build_log: out + "\n--- stderr ---\n" + err,
    };
  }
  if (exit !== 0) {
    return {
      ok: false,
      kind: "make_failed",
      exit_code: exit,
      elapsed_ms: elapsedMs,
      build_log: out + "\n--- stderr ---\n" + err,
    };
  }

  // Maintenance targets (clean, move_files, etc.) don't produce a ROM —
  // return success without searching for one.
  const maintenanceTargets = new Set(["clean", "move_files", "move", "tmp"]);
  if (maintenanceTargets.has(target)) {
    return {
      ok: true,
      target,
      elapsed_ms: elapsedMs,
      build_log: out,
    };
  }

  const rom = await findRom(project_dir, rom_name_hint ?? "main");
  if (!rom) {
    return {
      ok: false,
      kind: "rom_not_found",
      elapsed_ms: elapsedMs,
      message:
        "Make succeeded but no .ngc / .ngp found in bin/. Check rom_name_hint or the Makefile output.",
      build_log: out + "\n--- stderr ---\n" + err,
    };
  }

  const result = {
    ok: true,
    elapsed_ms: elapsedMs,
    rom_path: rom.path,
    rom_size_bytes: rom.size,
    rom_size_human: `${(rom.size / 1024).toFixed(1)} KB`,
    thome_used: resolvedThome,
    system_lib_used: resolvedSysLib,
    build_log: out,
  };
  if (include_rom_base64) {
    const buf = await readFile(rom.path);
    result.rom_base64 = buf.toString("base64");
  }
  return result;
}
