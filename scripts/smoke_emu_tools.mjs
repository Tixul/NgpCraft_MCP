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
  // Both registries: `list` needs no prior state and still crosses the whole bridge, so a
  // flag renamed upstream shows up here instead of the first time an agent adds one.
  ["ngpc_emu_breakpoint", { action: "list", rom_path: rom }],
  ["ngpc_emu_watchpoint", { action: "list", rom_path: rom }],
  // ngpc_emu_map_lookup and ngpc_emu_eventlog_profile both want a linker .map, which a
  // commercial ROM has no counterpart for -- they are exercised by the toolchain smoke.
  ["ngpc_emu_native_run", { rom_path: rom, bios_path: bios, frames: 30 }],
  // Timing is a FLAG now, so run it both ways: a rename upstream must not be able to
  // leave the default silently falling back to free fetch.
  ["ngpc_emu_native_run(free)", { rom_path: rom, bios_path: bios, frames: 5, timing: "free" }],
  ["ngpc_emu_native_run(guard)", { rom_path: rom, bios_path: bios, frames: 5, hw_guard: true }],
];

// A case may name a variant as `tool(label)`; the handler is the part before the paren.
const handlerName = (name) => name.replace(/\(.*\)$/, "");

// What a green run must CONTAIN. A tool that answers with the interesting field missing
// is a pass by byte count and a failure in fact -- which is the whole point of this file.
const CONTRACTS = {
  "ngpc_emu_native_run": (o) => o.timing === "silicon" && o.hw_safety != null,
  "ngpc_emu_native_run(free)": (o) => o.timing === "free",
  "ngpc_emu_native_run(guard)": (o) => o.hw_safety != null,
};

let pass = 0, fail = 0, skip = 0;
for (const [name, args] of CASES) {
  const fn = toolHandlers[handlerName(name)];
  if (!fn) { console.log(`MISSING ${name}`); fail++; continue; }
  if (handlerName(name) === "ngpc_emu_native_run" && !bios) {
    console.log(`SKIP    ${name}  (no bios given)`); skip++; continue;
  }
  try {
    const out = await fn(args);
    const size = JSON.stringify(out).length;
    const contract = CONTRACTS[name];
    // The bridges answer {content:[{type:"text",text:"<json>"}]}; look inside for the
    // fields the contract names, rather than asserting on the envelope.
    const payload = (() => {
      try { return JSON.parse(out?.content?.[0]?.text ?? "null") ?? out; }
      catch { return out; }
    })();
    if (contract && !contract(payload)) {
      console.log(`FAIL    ${name}\n        answered, but the contract does not hold`);
      fail++;
      continue;
    }
    console.log(`ok      ${name}  (${size} B)${contract ? " +contract" : ""}`);
    pass++;
  } catch (e) {
    console.log(`FAIL    ${name}\n        ${String(e.message).split("\n")[0]}`);
    fail++;
  }
}
console.log(`\n${pass} ok, ${fail} failed, ${skip} skipped`);
process.exit(fail ? 1 : 0);
