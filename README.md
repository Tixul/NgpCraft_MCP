# NgpCraft MCP

**Model Context Protocol server for NgpCraft — gives any MCP-compatible LLM the hardware-validated knowledge, API, examples, and validation tooling to write working NGPC homebrew.**

Install once, and every Claude Code / Cursor / Claude Desktop session gains the ability to look up real NGPC hardware facts, retrieve canonical ASM patterns, search a proven game's source by feature, and scaffold a new project — instead of hallucinating half-correct code that crashes on silicon.

---

## Why this exists

Writing NGPC code with an LLM today without context = hallucinated C99 constructs the compiler rejects, broken silicon opcodes (D0 prefix, CB family), wrong ABI, forgotten `NGP_FAR` on ROM data, etc. The user ends up debugging on real hardware what should have been caught before code was written.

This MCP server wraps **two years of curated NGPC knowledge** (the `NGPC_RAG` repo — docs, disasm cross-checks, validated patterns, bug DB) into a standard protocol so any LLM can access it on demand — in chunks, without saturating its context window.

Reference proof-point: the user's StarGunner shmup — a full playable NGPC homebrew with menus, music, SFX, enemy waves — was ~25% one-shot by GPT just from reading this doc corpus. This MCP makes that knowledge queryable, structured, and re-usable across every project.

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

The `ngpc_emu_*` tools spawn `python vendor/emulator/ngpc_emu.py <cmd> --json` (Python 3 required on the host). They expose **only the reliable parts** of the emulator (ROM parsing, memory bus reads, single-instruction decode/execute, bounded run-steps). Per the emulator's `FEATURE_MATRIX.md` policy ("a feature marked as supported must be really exploitable on NGPC"), no full-game emulation tool is exposed — VDP/PSG/IRQ/timing are not implemented yet.

---

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
- **The headless emulator** (`vendor/emulator/`) — drives the `ngpc_emu_*` tools (ROM parsing, memory bus, decode/execute, bounded run-steps, K2GE inspectors).
- **The disassembler** + the **live-editor transpiler** (lint / quickrun / screenshot / asset converters).
- **Templates** (base + 3 genre variants) and **reference games** (StarGunner on the NgpCraft template; Windcup on the legacy 2000s template).

### Intentionally not included

- **Proprietary Toshiba binaries** (`cc900` / `asm900` / `tulink` / `tuconv` / `s242ngp`, `system.lib`). `ngpc_compile_official` invokes the user's local install — nothing is bundled or redistributed.
- **Full-game (frame-accurate) emulation** — the `ngpc_emu_*` tools expose the reliable headless subset; the VDP/PSG/IRQ/timing frame loop is still in development.
- **ROM binaries** — no value for code generation.

---

## Hardware validation claim

Every entry in `src/data/bugs_silicon.json` cites the validation reference (jalon / bisect / date). These are not speculative — they were reproduced on a real NGPC flash cart during the NgpCraft toolchain development. The curated set captures findings that would otherwise only live in long-form dev logs.

---

## License

MIT. Vendored snapshots retain their original licenses (see `vendor/*/LICENSE`).

---

## Part of the NgpCraft SDK

| Project | Role |
|---|---|
| **NgpCraft Live Editor** | Browser playground, transpiler, lint |
| **NgpCraft Toolchain** | Pure-Python cc/as/ld for TLCS-900/H (WIP — not exposed via MCP yet) |
| **NgpCraft Emulator** | Python NGPC emulator (WIP — reliable parts exposed via `ngpc_emu_*` tools) |
| **NgpCraft Base Template** | Starter project + 4 genre variants |
| **NgpCraft Disasm** | TLCS-900/H disassembler |
| **NgpCraft Learn** | Bilingual FR/EN course site |
| **NgpCraft MCP** | ← this project — LLM knowledge + validation layer |

The MCP is the glue that makes AI assistance for NGPC actually work. The others are the ingredients.
