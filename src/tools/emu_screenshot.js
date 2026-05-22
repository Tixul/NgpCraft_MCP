import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runEmu } from "./_emu_bridge.js";
import { ppmFileToPng } from "./_ppm_to_png.js";

export const definition = {
  name: "ngpc_emu_screenshot",
  description:
    "Render a real K2GE color-mode framebuffer (160×152) of the emulator's current state and return PNG base64. This is the EMULATOR-side screenshot — runs the full K2GE compose pipeline (backdrop + SCR1/SCR2 raster + sprite raster with PR.C 4-level composition, chain, global PO offset, flip, palette transparency, window clip with OOWC fill, NEG invert). Use --seed-from to render from a savestate captured during a run. Distinct from `ngpc_screenshot` (transpiler-based JS interp). Backed by ngpc_emu.py `screenshot`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      seed_from: {
        type: "string",
        description:
          "Optional savestate JSON path. When set, its writable overlay is layered on the cold-start image before rendering, so the captured run's BGC, palette and control-register values feed the compose.",
      },
      output: {
        type: "string",
        description:
          "Optional explicit PPM path. When set, the .ppm is persisted alongside the returned PNG.",
      },
      include_png_base64: {
        type: "boolean",
        default: true,
        description:
          "If true (default), return the PNG base64. Set false for metadata-only.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({
  rom_path,
  seed_from,
  output,
  include_png_base64 = true,
}) {
  const td = await mkdtemp(join(tmpdir(), "ngpc-shot-"));
  const ppmPath = output ?? join(td, "screenshot.ppm");
  const args = [rom_path, "--output", ppmPath];
  if (seed_from) args.push("--seed-from", seed_from);
  try {
    const meta = await runEmu("screenshot", args, { timeoutMs: 60_000 });
    let png = null;
    if (include_png_base64) png = await ppmFileToPng(ppmPath);
    return {
      ...meta,
      ppm_path: ppmPath,
      ...(png
        ? { width: png.width, height: png.height, png_base64: png.png_base64 }
        : {}),
    };
  } finally {
    if (!output) {
      try {
        await rm(td, { recursive: true, force: true });
      } catch {}
    }
  }
}
