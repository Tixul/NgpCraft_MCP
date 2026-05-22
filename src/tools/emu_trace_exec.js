import { runEmu, buildSeedArgs } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_trace_exec",
  description:
    "Run up to N real (not preview) instructions from an address and return a per-record execution trace. Each record carries: decode payload, execution status, CPU before/after, flag changes, written registers, memory writes. Use for deep debug ('what happens step by step?'), golden-trace baselines, or diff between two ROM versions. Backed by ngpc_emu.py `trace-exec` — richer than `run-steps` when you need full per-instruction forensics. NOT a full game emulator (no VDP, no PSG, no IRQ).",
  inputSchema: {
    type: "object",
    properties: {
      rom_path: { type: "string" },
      address: {
        type: "string",
        description: "Optional start address. Defaults to bootstrap PC.",
      },
      count: {
        type: "integer",
        default: 8,
        minimum: 1,
        maximum: 4096,
        description: "Maximum number of execution records to emit.",
      },
      seed_xsp: {
        type: "string",
        description: "Optional XSP seed (decimal or 0x-prefixed hex).",
      },
      seed_regs: {
        type: "object",
        additionalProperties: { type: "string" },
        description:
          "Optional register seeds, e.g. {\"XWA\":\"0x003FBE00\",\"XIZ\":\"0\"}.",
      },
      seed_zero_bank0: {
        type: "boolean",
        description:
          "If true, seed XWA/XBC/XDE/XHL/XIX/XIY to 0 (software-convention shortcut matching most cc900/cdecl/adecl crt0 behavior). Use to bypass 'requires-known-full-register' stops on early bootstrap instructions like `ld WA, imm`. NOT hardware-verified power-on state — opt-in only.",
      },
      map: {
        type: "string",
        description:
          "Optional path to a t900ld .map file. When set, the result includes a 'final_symbol' block resolving the final PC to its owning function name + offset.",
      },
    },
    required: ["rom_path"],
  },
};

export async function handler({
  rom_path,
  address,
  count = 8,
  seed_xsp,
  seed_regs,
  seed_zero_bank0,
  map,
}) {
  const args = [rom_path, "--count", String(count)];
  if (address) args.push("--address", address);
  args.push(...buildSeedArgs({ seed_xsp, seed_regs, seed_zero_bank0, map }));
  return await runEmu("trace-exec", args, { timeoutMs: 60_000 });
}
