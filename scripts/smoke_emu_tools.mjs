// Smoke-test every emulator-backed MCP tool against a real ROM.
//
// WHY THIS EXISTS: re-vendoring the emulator changes the CLI that `_emu_bridge.js`
// and `_native_bridge.js` spawn. A tool whose flag was renamed upstream still LOOKS
// fine -- the module loads, the schema is valid -- and only fails when an agent
// actually calls it. This calls all of them.
//
// Usage:
//   node scripts/smoke_emu_tools.mjs <rom.ngc> [bios.bin]
//
// A tool that needs a BIOS and gets none is reported as SKIP, not FAIL.

import { toolHandlers } from "../src/tools/index.js";

const [rom, bios] = process.argv.slice(2);
if (!rom) {
  console.error("usage: node scripts/smoke_emu_tools.mjs <rom.ngc> [bios.bin]");
  process.exit(2);
}

// Each entry: [tool name, arguments]. Kept explicit rather than generated from the
// schemas, because a plausible-looking auto-filled argument is how a smoke test ends
// up proving nothing.
const CASES = [
  ["ngpc_emu_rom_info", { rom_path: rom }],
  ["ngpc_emu_registers", { rom_path: rom }],
  ["ngpc_emu_peek", { rom_path: rom, address: 0x200000, count: 16 }],
  ["ngpc_emu_memory_dump", { rom_path: rom, address: 0x200000, count: 64 }],
  ["ngpc_emu_decode", { rom_path: rom }],
  ["ngpc_emu_step_trace", { rom_path: rom, steps: 8 }],
  ["ngpc_emu_trace_exec", { rom_path: rom, steps: 8 }],
  ["ngpc_emu_run_until", { rom_path: rom, target_pc: "0x200046", max_steps: 200 }],
  ["ngpc_emu_palette_info", { rom_path: rom }],
  ["ngpc_emu_oam_info", { rom_path: rom }],
  ["ngpc_emu_tilemap_info", { rom_path: rom }],
  ["ngpc_emu_tile_view", { rom_path: rom, tile_id: 0 }],
  ["ngpc_emu_tiles_view", { rom_path: rom }],
  ["ngpc_emu_screenshot", { rom_path: rom }],
  ["ngpc_emu_tick_frame", { rom_path: rom }],
  ["ngpc_emu_opcode_coverage", { rom_path: rom }],
  // ngpc_emu_map_lookup needs a linker .map, which a commercial ROM has no counterpart
  // for -- it is exercised by the toolchain smoke, not here.
  ["ngpc_emu_native_run", { rom_path: rom, bios_path: bios, frames: 30 }],
];

let pass = 0, fail = 0, skip = 0;
for (const [name, args] of CASES) {
  const fn = toolHandlers[name];
  if (!fn) { console.log(`MISSING ${name}`); fail++; continue; }
  if (name === "ngpc_emu_native_run" && !bios) {
    console.log(`SKIP    ${name}  (no bios given)`); skip++; continue;
  }
  try {
    const out = await fn(args);
    const size = JSON.stringify(out).length;
    console.log(`ok      ${name}  (${size} B)`);
    pass++;
  } catch (e) {
    console.log(`FAIL    ${name}\n        ${String(e.message).split("\n")[0]}`);
    fail++;
  }
}
console.log(`\n${pass} ok, ${fail} failed, ${skip} skipped`);
process.exit(fail ? 1 : 0);
