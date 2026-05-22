# Changelog — NgpCraft Live Editor

## 2026-04-20

A pass focused on making the editor a real, scriptable building block — not
just a browser playground. Every JS module now exposes itself to non-browser
hosts (Node `vm`, Workers, electron), the lint catches more cc900 footguns,
and the renderer / interpreter expose first-class headless entry points so
external tools (MCP server, CI, regression tests) can use them without hacks.

### Added

- **`globalThis` exports on every module.** All 18 `js/*.js` files now end with
  `if (typeof globalThis !== 'undefined') globalThis.NGPC_X = NGPC_X;`. Top-level
  `const` was script-scoped and never reached embedding hosts; this trivial line
  per file removes a long-standing integration friction.
- **`NGPC_VDP.renderToPixels()`** — pure-pixel renderer that returns a
  `Uint8ClampedArray` of `W * H * 4` RGBA bytes. No DOM, no canvas. The existing
  `render(ctx)` is now a 4-line adapter; both share a private `renderInto(fb)`
  implementation so output stays byte-identical.
- **`NGPC_AssetTools.decodePngFromBytes(bytes)`** — sibling of `decodePng(file)`
  that accepts an `ArrayBuffer` or `Uint8Array`. Useful from Workers (no `File`
  on hand), browser extensions, and any host that already has the bytes in
  memory. Browser-only (uses `Blob` + `Image` + `Canvas`); Node hosts should use
  pngjs directly.
- **`NGPC_Interp.runFrames(code, opts)`** — first-class headless entry point.
  Wraps memory reset + `run()` + generator iteration + state capture into one
  call. Returns `{ ok, kind, errors?, framesAdvanced, mainCompleted, logs,
  state, framebuffer, psgEvents }`. Replaces the boilerplate every external
  caller (MCP server, tests, CI) had to reimplement.
- **PSG event log** — `NGPC_PSG.getEvents()`, `setEventSink(fn)`,
  `setEventBudget(n)`. Every `setTone`/`setAttn`/`setNoise`/`reset` now appends
  a structured event (`{type, ch, divider, freq, attn, silent, ctrl, white}`)
  to a 4096-entry ring buffer. Lets headless tools answer questions like "at
  what frame did channel 0 go silent?" without a WebAudio context.
- **Lint rule HW-3c** — flags `s8 != s8 || s8 != s8` chains. The Toshiba cc900
  compiler crashes on this pattern; now caught in-editor before the user
  migrates the code to a real build.
- **Lint rule HW-4** — flags `TYPE name[N]` (non-static, non-extern, in-function)
  when `N` exceeds 256. The NGPC stack is small (~512 bytes typical); a 2400-byte
  local array silently corrupts return addresses on real silicon. Suggested fix
  is to add `static` (move to BSS) or hoist to file scope. Validated against the
  Fix #23 DMA stack-overflow case from `bugs_silicon.json`.
- **DMA register write warnings.** Writes to the TLCS-900 DMA address ranges
  (0x0030..0x004F, 0x007C..0x007F, 0x0080..0x009F) now emit a one-shot warning
  via `warnOnce`: the editor does not emulate DMA, but real hardware fires the
  transfer — silent acceptance was misleading. Each address warns at most once
  per run.
- **`tests/` directory** — fixture-based regression suite with 22 cases covering
  smoke transpile, register read/write rewrites, vsync→yield + main→generator,
  comment stripping, enum rewrite, pointer rewrites, every active lint rule
  (positive + negative cases), the StarGunner-mini false-positive guard, and
  `runFrames` + PSG event log. Run with `node --test tests/transpile.test.mjs`
  (Node 20+, zero external dependencies).

### Changed

- **`runFrames` + screenshot path consolidation.** Memory reset, generator
  iteration, framebuffer capture and PSG event drain all live inside
  `runFrames` now. External callers no longer reimplement these steps.
- **`renderToPixels` / `render(ctx)` decoupling.** Pixel painting moved into a
  shared private `renderInto(fb)`. The existing browser path is preserved
  (byte-for-byte identical output); the new headless path is a single function
  call without DOM dependencies.
- **`NGPC_PSG.init()` is now Node-safe.** Guarded with
  `typeof window === 'undefined'` so loading PSG into a Node `vm` no longer
  crashes on `window.AudioContext`. Voice state model still updates;
  WebAudio output is simply skipped when no browser context exists.

### Fixed

- **Lint rule HW-3d removed** (after a brief introduction). Initial detection
  flagged any initialised declaration at brace depth ≥ 2, which produced false
  positives on valid C89 — including the StarGunner mini example's
  `for (i=0;…) { u16 c0 = …; u16 c1 = …; … }` pattern. The actual cc900 issue
  is most likely "mixed declarations and statements" (decl after a non-decl
  statement in the same block — C99 extension), but no precise reproducer is
  captured in the bug DB yet. The rule is documented as removed-pending-repro
  in the `interpreter.js` source.

### Notes

- The MCP server (`@ngpcraft/mcp`) consumes these snapshots via
  `vendor/transpiler/js/`. The `globalThis` exports above remove the
  `const → var` rewrite hack that the MCP loader previously needed.
- `runFrames` is the recommended entry point for any non-UI execution
  (tests, CI, MCP, future tooling). `run()` remains for code that needs
  the raw generator.
- `validate_transpile.py` (Python re-implementation, repo root) is unchanged
  and remains complementary to the new fixture suite — Python checks
  *structural* shape, fixtures check *behaviour*.

---

Earlier history not tracked in this file.
