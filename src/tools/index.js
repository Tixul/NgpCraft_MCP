// Tool registry — imports each tool's definition + handler.

import * as bug_check from "./bug_check.js";
import * as asm_pattern from "./asm_pattern.js";
import * as doc_search from "./doc_search.js";
import * as api_lookup from "./api_lookup.js";
import * as example_lookup from "./example_lookup.js";
import * as new_project from "./new_project.js";
import * as lint from "./lint.js";
import * as quickrun from "./quickrun.js";
import * as screenshot from "./screenshot.js";
import * as png_to_sprite from "./png_to_sprite.js";
import * as png_to_tilemap from "./png_to_tilemap.js";
import * as font_bake from "./font_bake.js";
import * as disasm from "./disasm.js";
import * as emu_rom_info from "./emu_rom_info.js";
import * as emu_peek from "./emu_peek.js";
import * as emu_decode from "./emu_decode.js";
import * as emu_step_trace from "./emu_step_trace.js";
import * as emu_trace_exec from "./emu_trace_exec.js";
import * as emu_run_until from "./emu_run_until.js";
import * as emu_map_lookup from "./emu_map_lookup.js";
import * as emu_eventlog_profile from "./emu_eventlog_profile.js";
import * as psg_trace from "./psg_trace.js";
import * as visual_diff from "./visual_diff.js";
import * as validate_project from "./validate_project.js";
import * as compile_official from "./compile_official.js";
// New emulator wrappers (2026-05-20 sync).
import * as emu_opcode_coverage from "./emu_opcode_coverage.js";
import * as emu_registers from "./emu_registers.js";
import * as emu_memory_dump from "./emu_memory_dump.js";
import * as emu_palette_info from "./emu_palette_info.js";
import * as emu_oam_info from "./emu_oam_info.js";
import * as emu_tilemap_info from "./emu_tilemap_info.js";
import * as emu_tile_view from "./emu_tile_view.js";
import * as emu_tiles_view from "./emu_tiles_view.js";
import * as emu_screenshot from "./emu_screenshot.js";
import * as emu_tick_frame from "./emu_tick_frame.js";
import * as emu_native_run from "./emu_native_run.js";
import * as emu_watchpoint from "./emu_watchpoint.js";
import * as emu_breakpoint from "./emu_breakpoint.js";
// Homemade toolchain pipeline (t900cc + t900as + t900ld + romtool).
import * as compile_homemade from "./compile_homemade.js";

const modules = [
  bug_check,
  asm_pattern,
  doc_search,
  api_lookup,
  example_lookup,
  new_project,
  lint,
  quickrun,
  screenshot,
  png_to_sprite,
  png_to_tilemap,
  font_bake,
  disasm,
  emu_rom_info,
  emu_peek,
  emu_decode,
  emu_step_trace,
  emu_trace_exec,
  emu_run_until,
  emu_map_lookup,
  emu_eventlog_profile,
  psg_trace,
  visual_diff,
  validate_project,
  compile_official,
  emu_opcode_coverage,
  emu_registers,
  emu_memory_dump,
  emu_palette_info,
  emu_oam_info,
  emu_tilemap_info,
  emu_tile_view,
  emu_tiles_view,
  emu_screenshot,
  emu_tick_frame,
  emu_native_run,
  emu_watchpoint,
  emu_breakpoint,
  compile_homemade,
];

export const toolDefinitions = modules.map((m) => m.definition);
export const toolHandlers = Object.fromEntries(
  modules.map((m) => [m.definition.name, m.handler])
);
