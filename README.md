# NgpCraft MCP

**Model Context Protocol server for NgpCraft — gives any MCP-compatible LLM the hardware-validated knowledge, API, examples, and validation tooling to write working NGPC homebrew.**

Install once, and every Claude Code / Cursor / Claude Desktop session gains the ability to look up real NGPC hardware facts, retrieve canonical ASM patterns, search a proven game's source by feature, and scaffold a new project — instead of hallucinating half-correct code that crashes on silicon.


> **Using this from an AI agent? Read [AGENT_GUIDE.md](AGENT_GUIDE.md) first.**
> It maps symptom → tool, explains the save-state workflow for reproducing a user's bug,
> and covers the BIOS requirement — without a real `bios.bin` the emulator renders a
> plausible but **wrong** picture. It is also served as the MCP resource
> `ngpc://doc/agent_guide`.

---

## Why this exists

Writing NGPC code with an LLM today without context = hallucinated C99 constructs the compiler rejects, toolchain mis-encodes (a `D0` prefix wrongly emitted for a register op) and broken silicon opcodes (`add A,C` = `CB 81` C-source ALU), wrong ABI, forgotten `NGP_FAR` on ROM data, etc. The user ends up debugging on real hardware what should have been caught before code was written.


---

## Quick start

### Install (local dev)

```bash
cd /path/to/NgpCraft_MCP
npm install
```

### Configure Claude Code

Add to `~/.claude/mcp.json` (or equivalent for your client):

```json
{
  "mcpServers": {
    "ngpcraft": {
      "command": "node",
      "args": ["/absolute/path/to/NgpCraft_MCP/src/server.js"]
    }
  }
}
```

Restart your MCP client. Tools will appear prefixed with `mcp__ngpcraft__*`.

### Quick test

Any of these prompts should now work:

- *"Use the NGPC bug checker to look up D0 prefix."*
- *"Search NGPC docs for DMA VBlank restrictions."*
- *"Show me StarGunner's state machine implementation."*
- *"Give me the canonical ASM for enabling VBlank interrupts."*

---

## Tools (v0.2)

| Tool | Status | Purpose |
|---|---|---|
| `ngpc_bug_check` | working | Query silicon + toolchain bug DB by opcode/keyword |
| `ngpc_asm_pattern` | working | Retrieve hardware-safe ASM patterns (prologue, LDIRW, DMA load, etc.) |
| `ngpc_doc_search` | working | Full-text search across the NGPC dev wiki corpus (wiki/ + DENSE_INDEX + StarGunner docs) |
| `ngpc_api_lookup` | working | Look up template function signatures from vendor/templates/base/src/**/*.h |
| `ngpc_example` | working | Grep StarGunner + Windcup RE by feature keyword |
| `ngpc_new_project` | working | Scaffold from base / cavegen / platformer / racer template |
| `ngpc_lint` | working | Hardware-fidelity lint via NGPC_Interp.compile (HW-1/HW-2/HW-3b) |
| `ngpc_quickrun` | working | Transpile + run main() generator N vsync ticks, return state digest |
| `ngpc_screenshot` | working | PNG of framebuffer at frame N (fake canvas + pngjs) |
| `ngpc_png_to_sprite` | working | PNG → sprite C/H via NGPC_AssetTools.exportSprite |
| `ngpc_png_to_tilemap` | working | PNG → tilemap C/H via NGPC_AssetTools.exportTilemap |
| `ngpc_font_bake` | stub (v0.3) | PNG → NGPC 2bpp font data — bake pipeline lives outside the live editor |
| `ngpc_disasm` | working | Disassemble ROM/byte range via ngpc_disasm.py bridge |
| `ngpc_emu_rom_info` | working | Parse ROM header + bootstrap reset state via NgpCraft emulator |
| `ngpc_emu_native_run` | working | **Runs** the game on the native C++ core: load a player save state (`.s0`), hold buttons, advance frames, return registers + a beam-accurate screenshot. Needs a real `bios.bin`. |
| `ngpc_emu_peek` | working | Read bytes from emulated memory bus |
| `ngpc_emu_decode` | working | Decode one TLCS-900 instruction at address |
| `ngpc_emu_step_trace` | working | Run N instructions from boot/address, return trace + CPU state |
| `ngpc_emu_trace_exec` | working | Per-record execution trace — CPU before/after, flag changes, memory writes per instruction (vs step_trace's condensed result) |
| `ngpc_psg_trace` | working | Run code N frames + return PSG event log (tone/attn/noise writes by frame) |
| `ngpc_visual_diff` | working | Render two C snippets at frame N + return per-pixel diff PNG (magenta on dimmed grey) |
| `ngpc_validate_project` | working | Lint every .c file under a directory, aggregated report (per file, per rule) |
| `ngpc_compile_official` | working (local toolchain) | Build a project via the LOCAL Toshiba toolchain (cc900 + asm900 + tulink + tuconv + s242ngp). Returns produced ROM. The .exe stay on the user's PC — never bundled or redistributed. |

The transpiler-backed tools (`ngpc_lint` / `ngpc_quickrun` / `ngpc_screenshot` / `ngpc_png_to_*`) load the Live Editor JS modules into a Node `vm` context via `src/tools/_transpiler_loader.js` — no browser, no DOM. Audio (PSG) is intentionally not initialised: voice register state is preserved but no WebAudio context is created.

For `ngpc_screenshot`, a fake CanvasRenderingContext2D captures the framebuffer; PNG encoding goes through `pngjs`.

For `ngpc_png_to_sprite` / `ngpc_png_to_tilemap`, PNG decoding goes through `pngjs` (replaces the browser-only `URL.createObjectURL` + `Image` path used inside `asset_tools.decodePng`).

The `ngpc_emu_*` tools spawn `python vendor/emulator/ngpc_emu.py <cmd> --json` (Python 3 required on the host). They cover ROM parsing, memory bus reads, decode/execute and the K2GE inspectors, and they read **static** state — a ROM at reset or a save state.

`ngpc_emu_native_run` is the exception: it spawns `python vendor/emulator/ngpc_native.py run --json` and **executes the machine** on the native C++ core (VDP, PSG, IRQ and timing all modelled), drawing the frame line by line as the beam passes. It needs the compiled core in `vendor/emulator/cpp/build/` and, for most commercial games, a real `bios.bin`.

---

## Requirements

| For | You need |
|---|---|
| the server | **Node 18+** |
| every `ngpc_emu_*` tool | **Python 3** on PATH |
| `ngpc_compile_homemade` | working | ⚠️ EXPERIMENTAL AND UNSTABLE — NOT A PRODUCTION COMPILER |
| `ngpc_emu_breakpoint` | working | Per-ROM PC-address breakpoint registry + event-log match |
| `ngpc_emu_eventlog_profile` | working | Bucket an event-log v1 JSON file by owning symbol via a t900ld .map |
| `ngpc_emu_map_lookup` | working | Resolve symbols from a t900ld .map file |
| `ngpc_emu_memory_dump` | working | Hexdump-style multi-row memory inspector |
| `ngpc_emu_oam_info` | working | Decode the K2GE OAM (0x8800..0x88FF, 64 sprites × 4 bytes) and the CP.C palette-code strip (0x8C00..0x8C3F) |
| `ngpc_emu_opcode_coverage` | working | Linear-walk a ROM from its entry point and report which leading-byte opcodes the current TLCS-900/H decoder does NOT yet handle |
| `ngpc_emu_palette_info` | working | Decode the K2GE palette RAM (0x8200..0x83FF) into a human view: 16 palettes × 4 entries (12-bit 0BGR) for each plane (sprite / SCR1 / SCR2 /… |
| `ngpc_emu_registers` | working | Rich CPU register view: 8 R32 with their R16/R8 decomposition (XWA → WA → W/A …), PC, SR, IFF level, RFP bank pointer, and the six modeled flags… |
| `ngpc_emu_run_until` | working | Run the emulator forward until PC reaches `target_pc`, an honest stop is hit, or `max_steps` is exhausted |
| `ngpc_emu_screenshot` | working | Compose a K2GE framebuffer (160×152) from memory and return PNG base64. Static state only — for a scrolling game or a raster split use `ngpc_emu_native_run`, which draws as the beam passes |
| `ngpc_emu_tick_frame` | working | Advance the K2GE frame/scanline state model (M3 Phase 0+) |
| `ngpc_emu_tile_view` | working | Render one 8×8 tile from CHAR_RAM as 4-level grayscale ASCII art (` ░▒█`) |
| `ngpc_emu_tilemap_info` | working | Decode one K2GE scroll-plane tilemap (SCR1 @ 0x9000 or SCR2 @ 0x9800, 32×32 tiles × 2 bytes/cell) |
| `ngpc_emu_tiles_view` | working | Render a grid of CHAR_RAM tiles as a binary PPM atlas, returned as PNG base64 |
| `ngpc_emu_watchpoint` | working | Per-ROM memory watchpoint registry + event-log match (v3 format) |
| running or rendering a game | a real **`bios.bin`**, supplied by you — never shipped here |

⚠️ **The BIOS is not optional for correct output.** The interrupt vector table lives in it;
with no BIOS that table is all zeroes, so the first interrupt sends the CPU to address 0 and
the game dies **while the screen still shows a plausible frame**. Some games check for it
directly: *Metal Slug — 2nd Mission* silently disables fire and jump if the console did not
boot through its BIOS.

## Resources

Exposed via `ngpc://` URIs — clients that support MCP resources can read them directly without a tool call.

- `ngpc://doc/quickstart` — orientation for AI agents
- `ngpc://doc/hw_registers` — hardware register map
- `ngpc://doc/palettes` — RGB444, 16 palettes, transparency
- `ngpc://doc/sprites` — 64 sprites, OAM, metasprites
- `ngpc://doc/tilemaps` — SCR1/SCR2, scroll, mapstream
- `ngpc://doc/dma` — DMA channels and restrictions
- `ngpc://doc/audio` — T6W28 PSG, BGM format
- `ngpc://doc/input` — joypad register, edge detect
- `ngpc://doc/bios` — BIOS calls, SYSFONT, power button
- `ngpc://doc/asm` — TLCS-900/H ASM guide
- `ngpc://doc/t900_dense_ref` — opcode reference
- `ngpc://doc/collision` — AABB, tilemap collision
- `ngpc://doc/math` — Q8.8, LUT, sin_table
- `ngpc://doc/game_loop` — VBlank sync, frame budget
- `ngpc://doc/asset_pipeline` — PNG → NGPC asset conventions
- `ngpc://doc/storage` — flash save, RTC
- `ngpc://doc/language` — cc900/t900cc C subset + gotchas
- `ngpc://doc/build_toolchain` — Makefile + linker
- `ngpc://doc/debug_tools` — emulator + hw test workflow
- `ngpc://roadmap/ngpc` — current priorities
- `ngpc://example/stargunner/shmup_doc` — full shmup architecture
- `ngpc://example/stargunner/retex` — porting post-mortem

---

## Repository layout

```
NgpCraft_MCP/
├── package.json
├── README.md                      ← you are here
├── src/
│   ├── server.js                  ← MCP stdio entrypoint
│   ├── tools/                     ← one file per tool
│   │   ├── index.js               ← registry
│   │   ├── bug_check.js           ← [working] silicon bug DB query
│   │   ├── asm_pattern.js         ← [working] canonical ASM patterns
│   │   ├── doc_search.js          ← [working] full-text search
│   │   ├── api_lookup.js          ← [working] header prototype lookup
│   │   ├── example_lookup.js      ← [working] StarGunner/Windcup grep
│   │   ├── new_project.js         ← [working] scaffold genre template
│   │   ├── lint.js                ← [working] NGPC_Interp.compile via vm
│   │   ├── quickrun.js            ← [working] generator-driven N-frame run
│   │   ├── screenshot.js          ← [working] fake canvas + pngjs PNG
│   │   ├── png_to_sprite.js       ← [working] NGPC_AssetTools.exportSprite + pngjs decode
│   │   ├── png_to_tilemap.js      ← [working] NGPC_AssetTools.exportTilemap
│   │   ├── font_bake.js           ← [stub v0.3]
│   │   ├── disasm.js              ← [working] spawn ngpc_disasm.py
│   │   ├── _transpiler_loader.js  ← shared vm loader for live-editor JS
│   │   ├── _png_decode.js         ← pngjs wrapper
│   │   ├── _emu_bridge.js         ← shared Python-spawn helper
│   │   ├── _emu_bridge.js         ← shared Python-spawn helper
│   │   ├── emu_rom_info.js        ← [working] ROM header + reset state
│   │   ├── emu_peek.js            ← [working] memory bus read
│   │   ├── emu_decode.js          ← [working] single-instruction decode
│   │   └── emu_step_trace.js      ← [working] bounded N-step execution trace
│   ├── resources/
│   │   └── index.js               ← URI → corpus file mapping
│   └── data/
│       ├── bugs_silicon.json      ← curated from hw-validation sessions
│       └── asm_patterns.json      ← canonical hw-safe ASM
├── corpus/                        ← markdown corpus (doc_search source)
│   ├── DENSE_INDEX.md             ← high-density entry point (map + cheat-sheet)
│   ├── wiki/                      ← the NGPC dev wiki (22 pages: hardware, CPU/toolchain, graphics, audio, systems, patterns)
│   └── stargunner/                ← SHMUP + TEMPLATE_RETEX (reference game architecture)
├── vendor/                        ← bundled NgpCraft sub-projects (all MIT — see vendor/*/LICENSE)
│   ├── toolchain/                 ← t900cc / t900as / t900ld / ngpc_romtool (Python) + ngpc.lcf + runtime/crt0.asm
│   ├── emulator/                  ← ngpc_emu.py + core/ + specs/ (headless CPU / bus / decode / run-steps / K2GE inspectors)
│   ├── disasm/                    ← ngpc_disasm.py + MANUAL
│   ├── transpiler/                ← live-editor JS core (interpreter, runtime, vdp, psg, asset tools)
│   ├── templates/                 ← base + cavegen + platformer + racer
│   └── examples/
│       ├── stargunner/            ← full game src + docs (NgpCraft template + APIs)
│       └── windcup_re/            ← a game on the legacy 2000s template (reference for that older API)
└── test/
```

---

## Scope decisions

### What's included

- **The NGPC dev wiki** (`corpus/wiki/`) + a high-density `DENSE_INDEX.md` — the core knowledge, cleaned and engine-agnostic.
- **Structured knowledge** — `bugs_silicon.json` + `asm_patterns.json`, hand-curated from hardware-validation sessions.
- **The homemade toolchain** (`vendor/toolchain/`: t900cc / t900as / t900ld / ngpc_romtool) — drives `ngpc_compile_homemade`, no `.exe` dependency.
  ⚠️ **Experimental and unstable — a teaching pipeline, not a compiler to build with.** It is
  there so the stages of a TLCS-900 build can be read and understood; expect mis-compiles and
  unimplemented constructs. For a ROM that must actually run, use `ngpc_compile_official`.
- **The headless emulator** (`vendor/emulator/`) — drives the `ngpc_emu_*` tools (ROM parsing, memory bus, decode/execute, bounded run-steps, K2GE inspectors).
- **The disassembler** + the **live-editor transpiler** (lint / quickrun / screenshot / asset converters).
- **Templates** (base + 3 genre variants) and **reference games** (StarGunner on the NgpCraft template; Windcup on the legacy 2000s template).

### Intentionally not included

- **Proprietary Toshiba binaries** (`cc900` / `asm900` / `tulink` / `tuconv` / `s242ngp`, `system.lib`). `ngpc_compile_official` invokes the user's local install — nothing is bundled or redistributed.
- **Full-game emulation is available** via `ngpc_emu_native_run` (native C++ core). The other `ngpc_emu_*` tools remain static inspectors by design — they describe a frozen moment, they do not run the machine.
- **ROM binaries** — no value for code generation.

---

## Hardware validation claim

Every entry in `src/data/bugs_silicon.json` cites the validation reference (jalon / bisect / date). These are not speculative — they were reproduced on a real NGPC flash cart during the NgpCraft toolchain development (WIP). The curated set captures findings that would otherwise only live in long-form dev logs.

---

## License

MIT. Vendored snapshots retain their original licenses (see `vendor/*/LICENSE`).

---

## Part of the NgpCraft SDK

| Project | Role |
|---|---|
| **NgpCraft Live Editor** | Browser playground, transpiler, lint |
| **NgpCraft Toolchain** | Pure-Python cc/as/ld for TLCS-900/H (WIP — not exposed via MCP yet) |
| **NgpCraft Emulator** | NGPC emulator — Python inspectors (`ngpc_emu_*`) plus the native C++ core that runs games (`ngpc_emu_native_run`) |
| **NgpCraft Base Template** | Starter project + 4 genre variants |
| **NgpCraft Disasm** | TLCS-900/H disassembler |
| **NgpCraft Learn** | Bilingual FR/EN course site |
| **NgpCraft MCP** | ← this project — LLM knowledge + validation layer |


