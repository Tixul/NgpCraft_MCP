import { runEmu } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_tick_frame",
  description:
    "Advance the K2GE frame/scanline state model (M3 Phase 0+). No CPU instructions executed — just moves frame_state forward and emits a savestate at the new timing position so Phase 3.1+ hardware reads (RAS.V, BLNK) can consume it. Mutually exclusive --scanlines / --frames. Returns frame_count, scanline, IRQ pending state, and (if --save-state was provided) the output path. Backed by ngpc_emu.py `tick-frame`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngp/.ngc ROM." },
      scanlines: {
        type: "integer",
        minimum: 0,
        description: "Number of scanlines to advance (>=0). Wraps modulo 198 into frame_count. Mutually exclusive with frames.",
      },
      frames: {
        type: "integer",
        minimum: 0,
        description: "Number of full frames to advance (>=0). Snaps scanline to 0 of the n-th next frame. Mutually exclusive with scanlines.",
      },
      seed_from: {
        type: "string",
        description: "Optional savestate JSON path. Starting frame_state is taken from the loaded savestate; CPU + overlay are copied verbatim into the emitted savestate.",
      },
      save_state: {
        type: "string",
        description: "Optional output path for the new savestate (frame_state at the advanced position). When omitted, only stdout reports.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({ rom_path, scanlines, frames, seed_from, save_state }) {
  if (scanlines != null && frames != null) {
    throw new Error("Pass only one of scanlines / frames, not both.");
  }
  const args = [rom_path];
  if (scanlines != null) args.push("--scanlines", String(scanlines));
  if (frames != null) args.push("--frames", String(frames));
  if (seed_from) args.push("--seed-from", seed_from);
  if (save_state) args.push("--save-state", save_state);
  return await runEmu("tick-frame", args);
}
