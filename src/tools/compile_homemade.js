// Compile NGPC sources with the HOMEMADE NgpCraft toolchain
// (t900cc + t900as + t900ld + ngpc_romtool) — a pure-Python pipeline, no .exe.
//
// Point it at your NgpCraft_toolchain checkout via the NGPCRAFT_TOOLCHAIN_ROOT
// environment variable or the `toolchain_root` argument. Defaults to a copy under
// this package's vendor/toolchain/.
//
// Complement to ngpc_compile_official: same goal (project sources → .ngc),
// different stack. The homemade toolchain has no .exe dependency and no
// Makefile contract — sources are compiled / assembled / linked individually
// by this wrapper.
//
// Pipeline:
//   .c  --t900cc-->  .asm
//   .asm --t900as--> .t9obj   (or .bin, depending on stage)
//   *.t9obj --t900ld--> .bin  (+ optional .map)
//   .bin --ngpc_romtool--> .ngc/.ngp
//
// All inputs are written/read from `work_dir` (defaults to a temp dir).
// On success, returns the produced ROM path + size + optional base64.

import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_TOOLCHAIN_ROOT =
  process.env.NGPCRAFT_TOOLCHAIN_ROOT ??
  join(dirname(fileURLToPath(import.meta.url)), "..", "..", "vendor", "toolchain");

function pickPython() {
  return process.platform === "win32" ? "python" : "python3";
}

function runPython(scriptPath, args, { cwd, timeoutMs = 120_000 } = {}) {
  return new Promise((res) => {
    const child = spawn(pickPython(), [scriptPath, ...args], {
      cwd,
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
    child.on("error", (e) =>
      res({ exit: -1, out, err: `${err}\nspawn error: ${e.message}`, timedOut })
    );
    child.on("close", (exit) => {
      clearTimeout(to);
      res({ exit, out, err, timedOut });
    });
  });
}

function summarizeStage(stage, r) {
  return {
    stage,
    exit: r.exit,
    timed_out: r.timedOut === true,
    stdout: r.out,
    stderr: r.err,
  };
}

export const definition = {
  name: "ngpc_compile_homemade",
  description:
    "Compile NGPC sources with the HOMEMADE NgpCraft toolchain (t900cc + t900as + t900ld + ngpc_romtool — the Python pipeline at NgpCraft_toolchain/tools/). Counterpart to ngpc_compile_official: no .exe dependency, no Makefile required. Provide one or more .c/.asm source files and a linker script (.lcf); the tool runs each stage and returns the produced ROM. Reports a per-stage build log (cc → as → ld → romtool).",
  inputSchema: {
    type: "object",
    properties: {
      sources: {
        type: "array",
        items: { type: "string" },
        description:
          "Absolute paths to source files (.c and/or .asm). .c files are compiled with t900cc.py first, then all sources are assembled with t900as.py to .t9obj, then linked.",
      },
      lcf_path: {
        type: "string",
        description:
          "Absolute path to the linker script (.lcf). If omitted, the bundled NgpCraft_toolchain/tools/ngpc.lcf is used.",
      },
      include_dirs: {
        type: "array",
        items: { type: "string" },
        description: "Additional -I include directories passed to t900cc.",
      },
      work_dir: {
        type: "string",
        description:
          "Directory used to write intermediates (.asm, .t9obj, .bin, .map). Defaults to a fresh temp dir.",
      },
      output_rom: {
        type: "string",
        description:
          "Output ROM path. Default: <work_dir>/main.ngc. Extension picks --color (.ngc) or --mono (.ngp).",
      },
      title: {
        type: "string",
        description: "Game title for the ROM header (max 12 chars). Default: 'NGPC_HBREW'.",
      },
      entry: {
        type: "string",
        description: "Entry-point address as hex (e.g. '0x200040'). Default: 0x200040.",
      },
      color: {
        type: "boolean",
        default: true,
        description: "Color-compatible ROM (.ngc, 0x10). When false, monochrome (.ngp, 0x00).",
      },
      pad: {
        type: "boolean",
        default: false,
        description: "Pad ROM to next power-of-two size (min 64 KB).",
      },
      cdecl_legacy: {
        type: "boolean",
        default: false,
        description:
          "Force legacy cdecl ABI for t900cc (all args pushed to stack). Default is __adecl v2 (args in XWA/XBC/XDE).",
      },
      generate_map: {
        type: "boolean",
        default: true,
        description: "Pass --map to t900ld and return the symbol map path.",
      },
      include_rom_base64: {
        type: "boolean",
        default: false,
        description:
          "If true, include the produced ROM as base64 in the response. Off by default to keep results small.",
      },
      toolchain_root: {
        type: "string",
        description:
          "Override path to the NgpCraft_toolchain root (containing tools/t900cc.py etc.). Default: the NGPCRAFT_TOOLCHAIN_ROOT env var, else the bundled vendor/toolchain/.",
      },
      timeout_ms: {
        type: "integer",
        default: 180000,
        description: "Hard timeout for each stage invocation.",
      },
    },
    required: ["sources"],
  },
};

export async function handler({
  sources,
  lcf_path,
  include_dirs = [],
  work_dir,
  output_rom,
  title,
  entry,
  color = true,
  pad = false,
  cdecl_legacy = false,
  generate_map = true,
  include_rom_base64 = false,
  toolchain_root,
  timeout_ms = 180_000,
}) {
  if (!Array.isArray(sources) || sources.length === 0) {
    return { ok: false, message: "Provide at least one source file in `sources`." };
  }

  const root = toolchain_root ?? DEFAULT_TOOLCHAIN_ROOT;
  const cc = join(root, "tools", "t900cc.py");
  const as_ = join(root, "tools", "t900as.py");
  const ld = join(root, "tools", "t900ld.py");
  const romtool = join(root, "tools", "ngpc_romtool.py");
  const defaultLcf = join(root, "tools", "ngpc.lcf");
  for (const [label, p] of [
    ["t900cc.py", cc],
    ["t900as.py", as_],
    ["t900ld.py", ld],
    ["ngpc_romtool.py", romtool],
  ]) {
    try {
      await stat(p);
    } catch {
      return {
        ok: false,
        message: `Homemade toolchain script not found: ${label} at ${p}. Pass toolchain_root='C:\\path\\to\\NgpCraft_toolchain'.`,
      };
    }
  }
  const lcf = lcf_path ?? defaultLcf;
  try {
    await stat(lcf);
  } catch {
    return { ok: false, message: `Linker script not found: ${lcf}` };
  }

  const wd = work_dir ?? (await mkdtemp(join(tmpdir(), "ngpc-build-")));
  await mkdir(wd, { recursive: true });

  const stages = [];
  const t0 = Date.now();

  // Stage 1 — compile every .c to .asm in work_dir.
  const asmInputs = [];
  for (const src of sources) {
    const ext = extname(src).toLowerCase();
    if (ext === ".asm" || ext === ".s") {
      asmInputs.push(resolve(src));
      continue;
    }
    if (ext !== ".c") {
      return {
        ok: false,
        message: `Unsupported source extension '${ext}' for ${src}. Expected .c, .asm or .s.`,
      };
    }
    const asmPath = join(wd, basename(src, ext) + ".asm");
    const ccArgs = [resolve(src), "-o", asmPath];
    for (const inc of include_dirs) ccArgs.push("-I", inc);
    if (cdecl_legacy) ccArgs.push("--cdecl-legacy");
    const r = await runPython(cc, ccArgs, { cwd: wd, timeoutMs: timeout_ms });
    stages.push(summarizeStage(`t900cc:${basename(src)}`, r));
    if (r.exit !== 0 || r.timedOut) {
      return {
        ok: false,
        kind: r.timedOut ? "timeout" : "compile_failed",
        failed_stage: stages[stages.length - 1].stage,
        elapsed_ms: Date.now() - t0,
        stages,
      };
    }
    asmInputs.push(asmPath);
  }

  // Stage 2 — assemble each .asm to .t9obj.
  const objs = [];
  for (const asm of asmInputs) {
    const objPath = join(wd, basename(asm, extname(asm)) + ".t9obj");
    const r = await runPython(
      as_,
      [asm, "-o", objPath, "--format", "obj"],
      { cwd: wd, timeoutMs: timeout_ms }
    );
    stages.push(summarizeStage(`t900as:${basename(asm)}`, r));
    if (r.exit !== 0 || r.timedOut) {
      return {
        ok: false,
        kind: r.timedOut ? "timeout" : "assemble_failed",
        failed_stage: stages[stages.length - 1].stage,
        elapsed_ms: Date.now() - t0,
        stages,
      };
    }
    objs.push(objPath);
  }

  // Stage 3 — link to flat .bin.
  const binPath = join(wd, "main.bin");
  const mapPath = generate_map ? join(wd, "main.map") : null;
  const ldArgs = [...objs, "-o", binPath, "-m", lcf];
  if (mapPath) ldArgs.push("--map", mapPath);
  {
    const r = await runPython(ld, ldArgs, { cwd: wd, timeoutMs: timeout_ms });
    stages.push(summarizeStage("t900ld", r));
    if (r.exit !== 0 || r.timedOut) {
      return {
        ok: false,
        kind: r.timedOut ? "timeout" : "link_failed",
        failed_stage: "t900ld",
        elapsed_ms: Date.now() - t0,
        stages,
      };
    }
  }

  // Stage 4 — pack to .ngc / .ngp.
  const romExt = color ? ".ngc" : ".ngp";
  const romPath = output_rom ?? join(wd, "main" + romExt);
  const romArgs = [binPath, "--output", romPath];
  if (entry) romArgs.push("--entry", entry);
  if (title) romArgs.push("--title", title);
  romArgs.push(color ? "--color" : "--mono");
  if (pad) romArgs.push("--pad");
  {
    const r = await runPython(romtool, romArgs, { cwd: wd, timeoutMs: timeout_ms });
    stages.push(summarizeStage("ngpc_romtool", r));
    if (r.exit !== 0 || r.timedOut) {
      return {
        ok: false,
        kind: r.timedOut ? "timeout" : "pack_failed",
        failed_stage: "ngpc_romtool",
        elapsed_ms: Date.now() - t0,
        stages,
      };
    }
  }

  let romStat;
  try {
    romStat = await stat(romPath);
  } catch {
    return {
      ok: false,
      kind: "rom_not_found",
      message: `ngpc_romtool reported success but no ROM at ${romPath}.`,
      stages,
    };
  }

  const result = {
    ok: true,
    elapsed_ms: Date.now() - t0,
    work_dir: wd,
    rom_path: romPath,
    rom_size_bytes: romStat.size,
    rom_size_human: `${(romStat.size / 1024).toFixed(1)} KB`,
    bin_path: binPath,
    map_path: mapPath,
    lcf_used: lcf,
    toolchain_root_used: root,
    stages,
  };
  if (include_rom_base64) {
    const buf = await readFile(romPath);
    result.rom_base64 = buf.toString("base64");
  }
  return result;
}
