// Bridge to vendor/disasm/ngpc_disasm.py.
// Accepts either a ROM path (preferred, no temp file) or a raw byte string
// (hex / base64) which we write to a temp .bin file before invoking.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { writeFile, unlink, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DISASM_SCRIPT = resolve(
  join(__dirname, "..", "..", "vendor", "disasm", "ngpc_disasm.py")
);

function pickPython() {
  return process.platform === "win32" ? "python" : "python3";
}

function runScript(args, { timeoutMs = 60_000 } = {}) {
  return new Promise((res, rej) => {
    const py = pickPython();
    const child = spawn(py, [DISASM_SCRIPT, ...args], { windowsHide: true });
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
      rej(new Error(`Failed to spawn Python (${py}): ${e.message}`))
    );
    child.on("close", (code) => {
      clearTimeout(to);
      if (timedOut) return rej(new Error(`Disassembler timed out`));
      if (code !== 0) {
        return rej(
          new Error(`ngpc_disasm.py exited ${code}\nstderr: ${err}`)
        );
      }
      res(out);
    });
  });
}

function decodeBytes(input) {
  // Accept hex string ("48656c..." with optional spaces / 0x) or base64.
  const cleaned = input.replace(/\s+/g, "").replace(/^0x/i, "");
  if (/^[0-9a-fA-F]+$/.test(cleaned) && cleaned.length % 2 === 0) {
    return Buffer.from(cleaned, "hex");
  }
  return Buffer.from(input, "base64");
}

export const definition = {
  name: "ngpc_disasm",
  description:
    "Disassemble TLCS-900/H code using the NgpCraft disassembler. Either pass a ROM file path (rom_path) — preferred — or raw bytes (bytes_hex / bytes_base64) which will be written to a temp file. Supports start/end address slice. Returns annotated ASM text. Requires Python 3 on the host.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Path to a .ngc / .ngp / .bin file." },
      bytes_hex: {
        type: "string",
        description: "Raw bytes as hex (with or without spaces / 0x). Used if rom_path absent.",
      },
      bytes_base64: {
        type: "string",
        description: "Raw bytes as base64. Used if rom_path and bytes_hex absent.",
      },
      base: {
        type: "string",
        description: "Override ROM base address as hex (e.g. '200000').",
      },
      start: {
        type: "string",
        description: "Start address as hex (e.g. '200040').",
      },
      end: {
        type: "string",
        description: "End address as hex.",
      },
    },
  },
};

export async function handler({ rom_path, bytes_hex, bytes_base64, base, start, end }) {
  let target = rom_path;
  let tempDir = null;
  if (!target) {
    const raw = bytes_hex ?? bytes_base64;
    if (!raw) {
      throw new Error("Provide rom_path, bytes_hex, or bytes_base64.");
    }
    const buf = bytes_hex ? decodeBytes(bytes_hex) : Buffer.from(raw, "base64");
    tempDir = await mkdtemp(join(tmpdir(), "ngpc-disasm-"));
    target = join(tempDir, "snippet.bin");
    await writeFile(target, buf);
  }
  const args = [target];
  if (base) args.push("--base", base);
  if (start) args.push("--start", start);
  if (end) args.push("--end", end);
  try {
    const text = await runScript(args);
    return {
      target,
      asm: text,
      bytes_used: tempDir ? "raw_bytes_via_tempfile" : "rom_file",
    };
  } finally {
    if (tempDir) {
      try {
        await unlink(target);
      } catch {}
    }
  }
}
