import { runNative } from "./_native_bridge.js";

export const definition = {
  name: "ngpc_emu_native_run",
  description:
    "RUN the game on the native C++ core and report what happened: advance N frames with buttons held, then return the CPU registers, stop status, an optional memory read, and an optional screenshot drawn line-by-line as the beam passed. " +
    "This is the only tool that EXECUTES the machine — every other ngpc_emu_* tool inspects a static image. " +
    "Its headline use is a bug a person can reproduce but not describe: they save a state in NgpCraft Emulator (F2) one frame before the problem, hand over the .s0, and this replays it — `state_path` + `hold` + `frames` answers 'what happens if I press A here?' in one call. " +
    "Needs the compiled core (vendor/emulator/cpp/build/) and, for most commercial games, a real bios.bin the user supplies. Backed by ngpc_native.py `run`. " +
    "Runs with the SILICON-CALIBRATED cartridge wait-states on (fetch = 3 cycles/byte), so it is timed like hardware rather than the ~3x-too-fast free-fetch default of the raw core; the answer says which model ran, in `timing`. " +
    "Every run also returns `hw_safety`: a starved watchdog or a stack that wandered into the BIOS page — two faults a real console punishes and most emulators never mention.",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: {
        type: "string",
        description:
          "Absolute path to a .ngc/.ngp ROM, or to a .zip/.7z holding one. An archive with several games needs the title named: 'Pack.zip/Game.ngc'.",
      },
      bios_path: {
        type: "string",
        description:
          "Absolute path to a real bios.bin. Strongly recommended: without it the interrupt vector table is empty, so the first IRQ sends the PC to address 0 and the game dies while the screen still LOOKS plausible. A few games (Metal Slug 2nd Mission) also check that the console booted through its BIOS.",
      },
      state_path: {
        type: "string",
        description:
          "Optional .s0..s3 save state written by NgpCraft Emulator (F2). Execution starts from it, which is how a user hands over the exact moment a bug happens. Both formats load: NGPCST02 (current — carries the sound CPU, the T6W28 and the timers) and the older NGPCST01 (CPU + memory only, so the sound will not resume). Note neither carries a ROM hash, so pass the ROM it was taken from — a mismatch yields nonsense rather than an error.",
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
      timing: {
        type: "string",
        enum: ["silicon", "free"],
        default: "silicon",
        description:
          "Cartridge-flash wait-states. 'silicon' (default) bills instruction fetch at the calibrated 3 cycles/byte, so cycle counts and self-timed frame rates match hardware. 'free' is the raw core default — no fetch cost, ~2.9x too fast, and any optimisation that only makes the code SHORTER measures as exactly zero. Use 'free' only to reproduce a measurement taken before wait-states existed.",
      },
      hw_guard: {
        type: "boolean",
        default: false,
        description:
          "STOP the run at the first hardware-safety fault instead of only counting it — `stop_status` then names it and the run ends there. Leave false to see the whole picture: neither fault halts a real console at the instruction that commits it, so counting is the faithful behaviour. Turn it on for a gate ('this build must be clean') or to catch the exact PC.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({
  rom_path, bios_path, state_path, frames, hold, screenshot_path, peek_address, peek_count,
  timing, hw_guard,
}) {
  const args = [rom_path];
  if (bios_path) args.push("--bios", bios_path);
  if (state_path) args.push("--state", state_path);
  if (frames != null) args.push("--frames", String(frames));
  if (hold) args.push("--hold", hold);
  if (screenshot_path) args.push("--shot", screenshot_path);
  if (timing) args.push("--timing", timing);
  if (hw_guard) args.push("--hw-guard");
  if (peek_address != null) {
    args.push("--peek", String(peek_address), String(peek_count ?? 16));
  }
  // Emulation cost scales with frames; give long runs room rather than truncating them.
  const timeoutMs = Math.max(120_000, 400 * (frames ?? 1));
  return await runNative("run", args, { timeoutMs });
}
