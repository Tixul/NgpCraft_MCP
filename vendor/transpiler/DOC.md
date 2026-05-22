# NgpCraft Live Editor

Browser-based live C editor for Neo Geo Pocket development and learning.
Write code, run it immediately, and inspect the framebuffer, palettes, logs,
and a few hardware-style guardrails without leaving the browser.

The source of truth is the current code in `js/`, `template/`, and the
helper scripts at the repository root.

## Quick start

From this directory:

```bash
python -m http.server 8000
```

Open <http://localhost:8000> in a modern browser.

Notes:

- Click the canvas once so it receives keyboard focus **and enables audio**
  (browsers refuse to start WebAudio until there's a user gesture).
- The default project already contains a starter `src/main.c`.
- A local HTTP server is the safest way to run the editor.

Minimal example:

```c
#include "ngpc_sys.h"
#include "ngpc_gfx.h"
#include "ngpc_text.h"

void main(void)
{
    ngpc_init();
    ngpc_load_sysfont();

    ngpc_gfx_set_bg_color(RGB(0, 0, 6));
    ngpc_gfx_set_palette(GFX_SCR1, 0,
                         RGB(0, 0, 0), RGB(15, 15, 15),
                         RGB(0, 15, 0), RGB(0, 15, 15));
    ngpc_gfx_clear(GFX_SCR1);

    ngpc_text_print(GFX_SCR1, 0, 4, 9, "Hello World");
}
```

Optional URL parameter:

- `?frames=N` limits execution to `N` frames. Useful for screenshots and
  deterministic checks.

## Main features

- Project tree with editable `src/*.c` files and generated or imported
  `src/*.h` files.
- Syntax-highlighted editor with tabs, line numbers, and autocomplete.
- Live run, manual run, pause, single-step, and reset controls.
- Curated built-in examples from `js/examples.js`, including a bundled
  multi-file shmup example and an interactive audio test.
- Framebuffer renderer, palette viewer, log filters with **Copy** button
  (or Ctrl+A inside the log pane) for sharing output.
- PNG importer that converts an image into a metasprite or tilemap source pair.
- Hardware-fidelity lint — refuses to run code that would break on a real cart.
- WebAudio backend for the T6W28 PSG — SFX and BGM from the template produce
  real sound.

## UI overview

- Left pane: project tree, new-file button, import, JSON export, ZIP export.
- Center pane: code editor and example selector.
- Right pane: screen, log, hardware budget, palette inspector, key mapping.

Toolbar actions:

- `Live`: auto-run after edits.
- `Autocomplete`: enable or disable the popup.
- `Pause`, `Step`, `Reset`: control the frame loop.
- `Run (Ctrl+Enter)`: compile and run immediately.
- `?`: keyboard shortcut help.

## Project model

- The entry file is `src/main.c`.
- Every editable `src/*.c` file is included on each run.
- `src/main.c` is processed last so helper code in other `.c` files is already
  available when `main()` runs.
- `#include` resolution searches the project tree and prefers matching headers.
- Files under `template/src/` are bundled as read-only reference headers plus
  the starter entry file.

## Import and export

Tree-pane actions:

- `+` creates a new `src/<name>.c`.
- Import button: imports files.
- `{ }` exports a JSON bundle.
- `zip` exports a ZIP archive.

Import behavior:

- One `.json` file: full-project import through `NGPC_Project.importBundle(...)`.
- One `.zip` file: only `.c` entries are kept, normalized into `src/*.c`, then
  imported as a full project replacement.
- One or more `.c` files: merged into the current project by filename.
- One `.png` file: opens the asset-import modal and generates source files.

PNG import output:

- Sprite import generates `src/<name>_mspr.c` and `src/<name>_mspr.h`.
- Tilemap import generates `src/<name>.c` and `src/<name>.h`.

Export behavior:

- JSON export writes editable `.c` files only.
- ZIP export writes editable `.c` files only.
- Template files from `template/src/` are never exported.

Important current limitation:

- Generated or imported `.h` files can exist in the in-browser project and are
  usable by `#include`, but the export buttons currently save editable `.c`
  files only. If you need a generated header outside the browser session, use
  the file download button while that header is open.

## Runtime coverage

The tool is not a CPU emulator. It transpiles a curated C subset to
JavaScript and executes it against an emulated NGPC-style memory map, VDP,
and PSG.

Implemented at a practical level:

- Memory map and pointer-backed reads and writes in `js/memory.js` and
  `js/runtime.js`.
- Graphics helpers for tilemaps, palettes, scrolling, tile loading, and
  background color.
- Sprite helpers, metasprite helpers, text, bitmap drawing, input, timing,
  simple math helpers, and palette effects.
- `ngpc_vsync()` as the 60 Hz sync point for the host loop.
- Hardware-style warnings for invalid OAM ids, tile indices, map coordinates,
  palette byte writes, runaway loops, CPU budget overflow, watchdog issues,
  and sprite overload.

### Audio

Implemented in `js/psg.js` (T6W28 emulator) + audio section of `js/runtime.js`:

- 3 tone channels (square wave) + 1 noise channel, via WebAudio.
- Frequency formula `F = 3 072 000 / (32 × divider)`, divider 1..1023.
- 4-bit attenuation per voice (0 = loudest, 15 = silent), ~2 dB per step.
- `Sfx_PlayToneEx(ch, div, attn, frames, sw_end, sw_step, sw_speed, sw_ping,
  sw_on, env_on, env_step, env_spd)` — full template signature with pitch
  sweep and attenuation envelope ticked per frame.
- `Sfx_PlayNoiseEx(rate, type, attn, frames, burst, burst_dur, env_on,
  env_step, env_spd)` — noise with envelope. `burst` / `burst_dur` parsed
  but not applied.
- `Bgm_Start*` / `Bgm_StartLoop*Ex` — 4-channel byte streams
  (notes 1..51, REST `0xFF`, END `0x00`, channel loop offsets).
- BGM opcodes actively applied: `SET_ATTN (0xF0)` and `HOST_CMD fade (0xF6)`.
- `Bgm_FadeOut(speed)` — linear fade-out.
- Default A2–B6 equal-temperament note table; override via
  `Bgm_SetNoteTable(table)` with a project-specific exported table.
- AudioContext is allocated at load but stays silent until the first
  click / keydown / touch (browser policy).
- Default `Sfx_Play(id)` has a preset-per-id fallback so SFX events are
  audible even without a project-specific dispatcher. A user-defined
  `Sfx_Play` in the project shadows the fallback.

Audio opcodes currently consumed from the stream but **not applied** (stream
stays in sync, the effect is skipped):

- `SET_ENV`, `SET_VIB`, `SET_SWEEP`, `SET_INST`, `SET_EXPR`, `PITCH_BEND`,
  `SET_ADSR`, `SET_LFO`, `SET_ENV_CURVE`, `SET_PITCH_CURVE`, `SET_MACRO`
- `EXT (0xFE)` currently drops the channel rather than decoding subcommands
- `Bgm_SetTempo` / `Bgm_SetSpeed` / `Bgm_SetGate` — no-op (BGM always runs
  at 1 tick / vsync)

Consequence: simple BGMs that rely on note + rest + attn + fade sound close
to the real driver. BGMs that depend on instrument presets, ADSR, or vibrato
will sound flatter until those opcodes are implemented.

## Hardware-fidelity lint

The transpiler refuses to run code that would break on a real cart. Every
rule prints a concrete fix. A single compile reports every violation at once.

Current rules:

- **HW-1 — NGP_FAR missing on ROM data**: a file-scope `const TYPE name[…]`
  passed to a function whose parameter is declared `NGP_FAR *`. cc900 emits
  a 16-bit near pointer; zero-extended it resolves to `0x00xxxx` instead of
  `0x20xxxx` in cartridge ROM → corrupted image.
- **HW-2 — volatile missing on ISR-shared var**: a file-scope scalar global
  written inside an `__interrupt` function but declared without `volatile`.
  cc900 caches the variable in a register in non-ISR loops → `while (!flag)`
  never terminates.
- **HW-3b — loop variable declared inside `for (…)`**: C99 extension, cc900
  is C89 strict. Declare the loop variable at block start.
- **HW-3c — `s8 != s8 || s8 != s8` chain**: signed-byte inequality chains
  joined by `||` crash the Toshiba cc900 compiler. Split into separate
  `if` statements or cast operands to `int`.
- **HW-4 — large stack array**: a non-static, non-extern in-function array
  whose declared size exceeds 256 bytes. The NGPC stack is small (~512 bytes
  typical); larger locals overflow and corrupt return addresses on real
  silicon. Add `static` (move to BSS) or hoist to file scope.

A violation throws a `HwFidelityError` which the editor logs under the
`err` filter. All bundled examples and the shmup bundle compile clean
against these rules.

In addition to the lint, the runtime emits a one-shot warning whenever
the user code writes to a TLCS-900 DMA register (0x0030..0x004F /
0x007C..0x007F / 0x0080..0x009F): the editor does not emulate DMA, so the
write is absorbed silently — but real hardware fires the transfer. The
warning makes that asymmetry visible.

## Performance notes

- Large numeric asset arrays (≥ 256 elements — tile / map / palette data)
  follow a fast path: the body is hoisted out before the main rewrite
  pipeline and reinjected verbatim after, so the ~20 downstream regex
  passes don't walk 30–50 KB of literals. A 4 096-element `u16` array
  compiles end-to-end in a few milliseconds.
- Macro expansion iterates to a fixed point (max 8 passes) so macro
  families like `NGP_TILEMAP_BLIT_SCR1` (which expand to nested inner
  macros using `prefix##_tiles`) resolve correctly.

## C subset

The transpiler in `js/interpreter.js` supports the subset used by the bundled
template, examples, and asset converters.

In broad terms it handles:

- Integer types, typedefs, arrays, structs, enums, prototypes, and function
  definitions used by the tool's template.
- Pointer variables backed by a memory Proxy model.
- Register-style reads and writes.
- Basic preprocessor handling for `#define`, `#include`, and common conditionals.
- Function-like macros with `##` token pasting, multi-line `\`-continuation,
  and iterative expansion up to 8 levels.
- Stable line mapping for compile and runtime error reporting.

It does not aim to be a full C compiler.

Known non-goals or current limits:

- No TLCS-900 instruction emulation.
- No real interrupts, DMA, or hardware timers.
- No RTC or flash-save emulation.
- Timer registers are not fully simulated.
- One-line forms like `*p++ = 5;` are still not supported.
- Pointer arithmetic on user-defined struct types is not modeled.
- Bit-fields are not implemented with real storage-width semantics.
- Preprocessor: no `#undef`, no `#x` stringification, no `__VA_ARGS__`.
- `const TYPE *const name[]` (double-const on a pointer array) is not
  handled — use `const TYPE *name[]`.

## Files

Top level:

- `index.html`: main UI shell and script loading order.
- `style.css`: application styling.
- `README.md` / `DOC.md` / `CHANGELOG.md`: user-facing docs and per-release notes.
- `sync_template.py`: rebuilds `js/project_data.js` from `template/`.
- `sync_font.py`: rebuilds `js/font_data.js`.
- `validate_transpile.py`: offline Python transpile smoke test.
- `tests/`: `node --test` fixture suite for the transpiler (see `tests/README.md`).

Runtime and UI:

- `js/api.js`: constants and public identifiers exposed to the runtime.
- `js/memory.js`: emulated memory and hardware guardrails.
- `js/psg.js`: T6W28 PSG emulator (WebAudio).
- `js/vdp.js`: framebuffer renderer.
- `js/runtime.js`: JS runtime functions exposed to transpiled C.
- `js/interpreter.js`: C-subset transpiler + hardware-fidelity lint.
- `js/main.js`: UI wiring, run loop, import/export, editor behavior.
- `js/highlight.js`: syntax highlighting.
- `js/autocomplete.js`: autocomplete popup behavior.
- `js/autocomplete_data.js`: bundled symbol catalog.

Project and assets:

- `js/project.js`: virtual filesystem and project import/export logic.
- `js/project_data.js`: generated template payload.
- `js/examples.js`: built-in example list.
- `js/example_bundles.js`: bundled multi-file example data.
- `js/png_import.js`: PNG import modal controller.
- `js/asset_tools.js`: in-browser asset conversion helpers.
- `js/zip.js`: minimal ZIP reader/writer used by import/export.
- `js/font.js`: font decode helpers.
- `js/font_data.js`: generated bitmap font data.

Bundled template:

- `template/src/main.c`: starter entry file.
- `template/src/core/*.h`: core headers exposed in the editor.
- `template/src/gfx/*.h`: graphics headers exposed in the editor.

## Regenerating generated files

After editing files under `template/`:

```bash
python sync_template.py
```

After changing the baked font inputs or parameters:

```bash
python sync_font.py
```

To run a quick offline Python transpile smoke test:

```bash
python validate_transpile.py
```

To run the JS fixture suite (Node 20+, no external deps):

```bash
node --test tests/transpile.test.mjs
```

## Headless API

Every module is exposed on `globalThis`, so non-browser hosts (Node `vm`,
Workers, electron, the NgpCraft MCP server) can drive the transpiler without
loading `index.html`. The recommended entry point is:

```js
NGPC_Interp.runFrames(code, {
  frames: 60,                 // generator iterations to advance (vsync ticks)
  captureFramebuffer: true,   // include RGBA bytes in the result
  capturePsgEvents: true,     // include the PSG event log
});
// → { ok, kind, errors?, framesAdvanced, mainCompleted, logs,
//     state, framebuffer, psgEvents }
```

Other useful headless entry points:

- `NGPC_VDP.renderToPixels()` — `Uint8ClampedArray` of `W*H*4` RGBA bytes,
  no canvas required.
- `NGPC_AssetTools.decodePngFromBytes(bytes)` — decode an `ArrayBuffer` /
  `Uint8Array` instead of a `File` (browser/Worker; Node hosts should use
  `pngjs` directly and call `exportSprite`/`exportTilemap` with the result).
- `NGPC_PSG.getEvents(clear=true)` — drain the structured PSG event log
  (`{type, ch, divider, freq, attn, silent, ctrl, white}` entries).
  `setEventSink(fn)` streams events live; `setEventBudget(n)` sizes the
  ring buffer (default 4096).

## Maintenance note

If this doc and the code disagree, follow the code:

- UI and project behavior: `index.html`, `js/main.js`, `js/project.js`
- Runtime behavior: `js/runtime.js`, `js/memory.js`, `js/vdp.js`, `js/psg.js`
- C subset + hardware lint: `js/interpreter.js`
- Bundled headers and starter project: `template/`, `js/project_data.js`
