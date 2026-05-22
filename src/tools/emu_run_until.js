import { runEmu, buildSeedArgs } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_run_until",
  description:
    "Run the emulator forward until PC reaches `target_pc`, an honest stop is hit, or `max_steps` is exhausted. Unlike `emu_step_trace` / `emu_trace_exec` which retain every record, this command only keeps the final CPU state and the last record — designed for long bootstraps and bridging gaps between savestates. Returns stop_reason (target-reached / stopped-on-<status> / step-budget-exhausted), executed_count, final_cpu, optional final_symbol when --map is supplied. Supports --auto-tick-addr to simulate a vblank counter ISR so code spinning on it (e.g. _ngpc_vsync) can exit without real IRQ modeling. NOT a full game emulator — no VDP, no PSG, no IRQ (auto-tick is a software-only shortcut).",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string" },
      target_pc: {
        type: "string",
        description:
          "Target PC where execution should stop (decimal or 0x-prefixed hex). Use a value beyond the ROM (e.g. 0x00230000) to mean 'just keep going'.",
      },
      address: {
        type: "string",
        description: "Optional start address. Defaults to bootstrap PC.",
      },
      max_steps: {
        type: "integer",
        default: 100000,
        minimum: 1,
        maximum: 10000000,
        description: "Step budget before giving up (default 100 000).",
      },
      seed_xsp: { type: "string" },
      seed_regs: {
        type: "object",
        additionalProperties: { type: "string" },
      },
      seed_zero_bank0: {
        type: "boolean",
        description:
          "If true, seed XWA/XBC/XDE/XHL/XIX/XIY to 0 (software-convention shortcut for cc900/cdecl/adecl crt0).",
      },
      map: {
        type: "string",
        description:
          "Optional path to a t900ld .map file. The result includes a 'final_symbol' block resolving the final PC.",
      },
      auto_tick_addr: {
        type: "string",
        description:
          "Address of a byte counter in writable memory (decimal or 0x-hex). Incremented every `auto_tick_period` executed instructions. Use to simulate vblank counter so `_ngpc_vsync`-style spins exit. NOT hardware-faithful — opt-in only.",
      },
      auto_tick_period: {
        type: "integer",
        default: 256,
        minimum: 1,
        description:
          "Instructions between auto-tick increments (default 256). Only meaningful with auto_tick_addr.",
      },
    },
    required: ["rom_path", "target_pc"],
  },
};

export async function handler({
  rom_path,
  target_pc,
  address,
  max_steps = 100000,
  seed_xsp,
  seed_regs,
  seed_zero_bank0,
  map,
  auto_tick_addr,
  auto_tick_period = 256,
}) {
  const args = [rom_path, target_pc, "--max-steps", String(max_steps)];
  if (address) args.push("--address", address);
  args.push(...buildSeedArgs({ seed_xsp, seed_regs, seed_zero_bank0, map }));
  if (auto_tick_addr) {
    args.push("--auto-tick-addr", String(auto_tick_addr));
    args.push("--auto-tick-period", String(auto_tick_period));
  }
  return await runEmu("run-until-exec", args, { timeoutMs: 180_000 });
}
