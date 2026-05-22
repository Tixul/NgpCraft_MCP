import { runEmu, buildSeedArgs } from "./_emu_bridge.js";

export const definition = {
  name: "ngpc_emu_step_trace",
  description:
    "Execute up to N TLCS-900 instructions from a starting address (defaults to bootstrap PC), preserving CPU state and a writable stack overlay between steps. Returns the executed trace + final CPU register state. Useful for: 'where does crt0 actually go', 'which opcodes ran before crash', 'verify a specific routine'. Backed by vendor/emulator/ngpc_emu.py run-steps. NOT a full game emulator — no VDP, no PSG, no IRQ. Honest single-stepper with bounded execution.",
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
        default: 16,
        minimum: 1,
        maximum: 4096,
        description: "Maximum instructions to execute.",
      },
      seed_xsp: {
        type: "string",
        description: "Optional XSP seed (decimal or 0x-prefixed hex).",
      },
      seed_regs: {
        type: "object",
        additionalProperties: { type: "string" },
        description:
          "Optional register seeds, e.g. {\"XWA\":\"0x1234\",\"XHL\":\"0x6F00\"}.",
      },
      seed_zero_bank0: {
        type: "boolean",
        description:
          "If true, seed XWA/XBC/XDE/XHL/XIX/XIY to 0 (software-convention shortcut for most cc900/cdecl/adecl crt0). Lets early bootstrap instructions like `ld WA, imm` execute instead of blocking on requires-known-full-register.",
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
  count = 16,
  seed_xsp,
  seed_regs,
  seed_zero_bank0,
  map,
}) {
  const args = [rom_path, "--count", String(count)];
  if (address) args.push("--address", address);
  args.push(...buildSeedArgs({ seed_xsp, seed_regs, seed_zero_bank0, map }));
  return await runEmu("run-steps", args, { timeoutMs: 60_000 });
}
