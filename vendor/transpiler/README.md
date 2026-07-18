
# NgpCraft Live Editor

**Browser-based live C editor for Neo Geo Pocket Color — write code, see the result, hear it too, no toolchain required.**

---

## What is it?

NgpCraft Live Editor lets you write C code targeting the NGPC hardware API and see the emulated 160×152 screen update in real time, with basic sound output. No compiler, no flash cart, no install — open `index.html` and start coding.

The API mirrors the NgpCraft template, so anything that runs in the editor is close to what would run on a real cartridge.

---

## Features

- **Live execution** — code reruns 250 ms after your last keystroke (or on demand with Ctrl+Enter)
- **Multi-file projects** — split code across multiple `src/*.c` files, add/delete from the tree pane
- **Graphics** — SCR1/SCR2 tilemaps, 64 sprites with H/V chaining, 16 palettes × 4 colors, scroll, flip, viewport, LCD invert, palette FX (fade, flash, cycle)
- **Sound** — T6W28 PSG emulated via WebAudio: 3 tone channels + noise, pitch sweep, attenuation envelope, BGM stream player. SFX and BGM calls from the template produce actual audio.
- **In-browser PNG import** — drag a PNG, choose Sprite or Tilemap, get generated C/H files instantly
- **Autocomplete** — signatures + docstrings for the NgpCraft API (Ctrl+Space)
- **Import / export** — JSON bundles or ZIP archives compatible with the template layout
- **11 progressive examples** — from hello-world to a multi-file mini shmup, plus an audio test project
- **Hardware-fidelity lint** — refuses to run code that would break on a real cart. Catches missing `NGP_FAR` on ROM data (HW-1), missing `volatile` on ISR-shared variables (HW-2), C99 for-decl that cc900 rejects (HW-3b), `s8 != s8 || s8 != s8` chains that crash cc900 (HW-3c), and large stack arrays that would overflow the NGPC stack (HW-4). Each error explains the hardware symptom and the fix.
- **Hardware guardrails at runtime** — CPU budget, watchdog, sprite-line overflow, write-to-read-only warnings, DMA register write warnings (DMA isn't emulated; writes to DMA registers are flagged so you don't trust in-editor behaviour for that path).
- **Headless API for tooling** — `NGPC_Interp.runFrames(code, opts)`, `NGPC_VDP.renderToPixels()`, `NGPC_AssetTools.decodePngFromBytes(bytes)`, and a structured PSG event log (`NGPC_PSG.getEvents`). Every module also exposes itself on `globalThis`, so external hosts (Node `vm`, Workers, electron, the NgpCraft MCP server) can drive the transpiler without a browser.
- **Test suite** — `node --test tests/transpile.test.mjs` runs 22 fixtures covering smoke transpile, register rewrites, vsync→generator, every active lint rule, and the headless `runFrames` API. Zero external dependencies.
- **No runtime dependencies** — pure HTML/CSS/JS, no build step, no npm, no server required

---

<img width="2403" height="1170" alt="9" src="https://github.com/user-attachments/assets/48168f82-11d5-4bb7-a5d4-b885add144ba" />

## Quick start

```bash
git clone <repo>
cd NgpCraft_live_editor
python3 -m http.server 8000
# open http://localhost:8000
```

Or just double-click `index.html`. Click the canvas once to give it keyboard focus and enable sound.

Hello world:

```c
#include "ngpc_sys.h"
#include "ngpc_gfx.h"
#include "ngpc_text.h"

void main(void) {
    ngpc_init();
    ngpc_load_sysfont();
    ngpc_gfx_set_bg_color(RGB(0, 0, 6));
    ngpc_gfx_set_palette(GFX_SCR1, 0,
                         RGB(0,0,0), RGB(15,15,15), RGB(0,15,0), RGB(0,15,15));
    ngpc_text_print(GFX_SCR1, 0, 4, 9, "Hello World");
}
```

---

## What works, what doesn't

**Works well:**
- Tilemaps, sprites, palettes, scroll, viewport, LCD effects
- SFX tones and noise with pitch sweep + decay envelope
- Simple BGM (4-channel stream format: notes, rests, loops, volume, fade-out)
- Macro-based asset loading (`NGP_TILEMAP_BLIT_SCR1` and friends)
- Compiles 55k-line projects end-to-end

**Partial:**
- **Audio drivers** — the common opcodes play correctly, but instrument presets, ADSR, vibrato, LFO, pitch curves, and tempo changes are not yet applied (stream stays in sync but the effect is skipped). BGMs exported from advanced projects will sound flatter than on real hardware.
- **C subset** — function-like macros with `##`, multi-line `#define`, macro-calling-macro (up to 8 levels). Not supported: `*p++` in a single expression, pointer arithmetic on struct types, bit-fields, `#undef`, stringification, `__VA_ARGS__`.

**Not implemented:**
- TLCS-900/H CPU emulation — code runs as transpiled JavaScript, timing is approximate
- DMA channels, HBlank interrupts, hardware timers (registers accepted but inert)
- Flash save and RTC (`ngpc_flash_*`, `ngpc_rtc_*`)
- Z80 coprocessor — sound goes through WebAudio directly, not the real Z80 driver protocol
- The BIOS system font is substituted with a bitmap font (glyph shapes differ slightly)

One quirk worth knowing: `int` is 2 bytes in the NGPC toolchain — matches cc900 but surprises devs used to 32-bit `int`.

---

## Project structure

```
index.html          — three-pane shell
js/
  interpreter.js    — C → JS transpiler + hardware-fidelity lint + runFrames() headless API
  psg.js            — T6W28 PSG emulator (WebAudio) + structured event log
  vdp.js            — framebuffer renderer (render / renderToPixels)
  runtime.js        — JS ports of NgpCraft runtime functions
  asset_tools.js    — in-browser PNG → sprite / tilemap converter (decodePng / decodePngFromBytes)
  api.js            — NGPC API constants + register table
  memory.js         — emulated memory bus + hardware-budget tracking
  ...
template/src/       — NgpCraft headers (read-only reference)
tests/              — node --test fixture suite for the transpiler
sync_template.py    — regenerate project_data.js after header changes
CHANGELOG.md        — per-release notes
```

---

## Part of the NgpCraft SDK

NgpCraft Live Editor is the learning and prototyping front-end for the NgpCraft homebrew SDK for the Neo Geo Pocket Color.
