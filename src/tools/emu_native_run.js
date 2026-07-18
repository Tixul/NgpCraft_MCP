import { runNative } from "./_native_bridge.js";

export const definition = {
  name: "ngpc_emu_native_run",
  description:
    "RUN the game on the native C++ core and report what happened: advance N frames with buttons held, then return the CPU registers, stop status, an optional memory read, and an optional screenshot drawn line-by-line as the beam passed. " +
    "This is the only tool that EXECUTES the machine — every other ngpc_emu_* tool inspects a static image. " +
    "Its headline use is a bug a person can reproduce but not describe: they save a state in NgpCraft Emulator (F2) one frame before the problem, hand over the .s0, and this replays it — `state_path` + `hold` + `frames` answers 'what happens if I press A here?' in one call. " +
    "Needs the compiled core (vendor/emulator/cpp/build/) and, for most commercial games, a real bios.bin the user supplies. Backed by ngpc_native.py `run`.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string", description: "Absolute path to a .ngc/.ngp ROM." },
      bios_path: {
        type: "string",
        description:
          "Absolute path to a real bios.bin. Strongly recommended: without it the interrupt vector table is empty, so the first IRQ sends the PC to address 0 and the game dies while the screen still LOOKS plausible. A few games (Metal Slug 2nd Mission) also check that the console booted through its BIOS.",
      },
      state_path: {
        type: "string",
        description:
          "Optional .s0..s3 save state written by NgpCraft Emulator (F2). Execution starts from it, which is how a user hands over the exact moment a bug happens. Note the format carries no ROM hash, so pass the ROM it was taken from — a mismatch yields nonsense rather than an error.",
      },
      frames: {
        type: "integer",
        minimum: 0,
        default: 1,
        description: "Frames to advance. 1 = just look at the loaded state; 30 ≈ half a second of play.",
      },
      hold: {
        type: "string",
        description:
          "Buttons held for every frame, '+'-separated: UP, DOWN, LEFT, RIGHT, A, B, OPTION. E.g. 'A' or 'LEFT+B'. Held for the whole run, so a game that needs a fresh press-edge may need two calls (one without, one with).",
      },
      screenshot_path: {
        type: "string",
        description: "Optional output path (.png or .ppm) for the resulting frame.",
      },
      peek_address: { type: "integer", description: "Optional address to read after the run." },
      peek_count: { type: "integer", minimum: 1, default: 16, description: "Bytes to read at peek_address." },
    },
    required: ["rom_path"],
  },
};

export async function handler({
  rom_path, bios_path, state_path, frames, hold, screenshot_path, peek_address, peek_count,
}) {
  const args = [rom_path];
  if (bios_path) args.push("--bios", bios_path);
  if (state_path) args.push("--state", state_path);
  if (frames != null) args.push("--frames", String(frames));
  if (hold) args.push("--hold", hold);
  if (screenshot_path) args.push("--shot", screenshot_path);
  if (peek_address != null) {
    args.push("--peek", String(peek_address), String(peek_count ?? 16));
  }
  // Emulation cost scales with frames; give long runs room rather than truncating them.
  const timeoutMs = Math.max(120_000, 400 * (frames ?? 1));
  return await runNative("run", args, { timeoutMs });
}
