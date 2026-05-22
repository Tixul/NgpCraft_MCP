// Minimal C-to-JS transpiler for the NgpCraft Live Editor.
//
// Strategy: we don't run a real C parser. We rewrite a curated subset of C
// syntax into equivalent JavaScript and execute inside a Function() with the
// NGPC API and memory helpers exposed. Covers most of what a beginner needs
// for NGPC graphics code.
//
// Supported:
//   - Types u8/u16/u32/s8/s16/s32/int/char/short/long/bool/void
//     (declarations are stripped and become `let`).
//   - `const` / `static` storage-class prefixes on declarations (passed through).
//   - Function definitions: `void main(void) {...}` -> `function main() {...}`.
//   - Pointer-cast memory access:
//       *(u8*)ADDR = VAL   ->  W8(ADDR, VAL)
//       *(u16*)ADDR        ->  R16(ADDR)    ... etc.
//   - RGB(r,g,b) exposed as a JS function with the same semantics as ngpc.h.
//   - Control flow: for/while/do/if/else/switch/break/continue.
//   - All JS-compatible arithmetic and bitwise operators.
//   - `#include` / `#define` lines and `/* */` // comments are stripped.
//
// Not supported (explicit, so errors are predictable):
//   - struct, union, user-defined typedef.
//   - Array declarations with `[N]` sizing.
//   - Pointer arithmetic beyond `*(type*)expr`.
//   - Macros with arguments beyond the RGB() builtin.

const NGPC_Interp = (() => {
  const TYPE_KEYWORDS = [
    'u_char', 'u_short', 'u_long',
    'u8', 'u16', 'u32', 's8', 's16', 's32',
    'char', 'short', 'int', 'long', 'bool', 'void',
  ];
  // Types defined in template headers (not visible to user `main.c` since the
  // transpiler doesn't process `#include`). Adding them here lets declarations
  // like `const NgpcMetasprite foo = {…};` strip the leading type correctly.
  // Each entry traces back to its defining header in NgpCraft_base_template/.
  const TEMPLATE_TYPES = [
    'NgpcMetasprite', 'MsprPart', 'MsprAnimator', 'MsprAnimFrame',   // ngpc_metasprite.h
    'FuncPtr', 'IntHandler',                                           // ngpc_types.h
  ];
  // Template struct types whose uninit-declared instances must become `= {}`
  // in JS so field writes (`a.anim = anim;`) don't hit `undefined`. In C they
  // are zero-initialized by the compiler when declared at function scope;
  // JS `let a;` is `undefined`, which breaks property access.
  const STRUCT_DEFAULT_INIT_TYPES = ['MsprAnimator'];
  const STORAGE_KEYWORDS = ['const', 'static', 'volatile', 'register', 'extern'];

  // Map of identifier -> { addr, width } for bareword register / extern access.
  // Covers:
  //   - `extern` variables from NgpCraft headers (g_vb_counter, ngpc_pad_*),
  //     backed by scratch-region bytes the runtime updates each frame.
  //   - Scalar hardware-register macros from ngpc.h (short form) and
  //     NgpCraft_base_template/src/core/ngpc_hw.h (HW_* form), which in real C
  //     expand to `*(volatile TYPE*)ADDR`. We rewrite bareword reads/writes
  //     directly to R{w}() / W{w}() calls so user code can use the macros
  //     without a preprocessor.
  //
  // Pointer-array macros like HW_PAL_SCR1 ((volatile u16*)0x8280) are NOT in
  // this table — they're real pointer values, used via array indexing in C
  // (e.g. `HW_PAL_SCR1[i] = ...`), handled by api.js as numeric constants.
  const REGISTERS = {
    /* --- Runtime externs (scratch region) ---------------------------- */
    g_vb_counter:      { addr: 0xFB00, width: 8 },
    ngpc_pad_held:     { addr: 0xFB01, width: 8 },
    ngpc_pad_pressed:  { addr: 0xFB02, width: 8 },
    ngpc_pad_released: { addr: 0xFB03, width: 8 },
    ngpc_pad_repeat:   { addr: 0xFB04, width: 8 },

    /* --- CPU / Timers (ngpc_hw.h §CPU) ------------------------------- */
    HW_WATCHDOG: { addr: 0x006F, width: 8 },
    WATCHDOG:    { addr: 0x006F, width: 8 },
    HW_TRUN:     { addr: 0x0020, width: 8 }, TRUN:    { addr: 0x0020, width: 8 },
    HW_TREG0:    { addr: 0x0022, width: 8 },
    HW_TREG1:    { addr: 0x0023, width: 8 },
    HW_T01MOD:   { addr: 0x0024, width: 8 },
    HW_TFFCR:    { addr: 0x0025, width: 8 }, TFFCR:   { addr: 0x0025, width: 8 },
    HW_TREG2:    { addr: 0x0026, width: 8 },
    HW_TREG3:    { addr: 0x0027, width: 8 }, TREG3:   { addr: 0x0027, width: 8 },
    HW_T23MOD:   { addr: 0x0028, width: 8 }, T23MOD:  { addr: 0x0028, width: 8 },
    HW_TRDC:     { addr: 0x0029, width: 8 },
    HW_DMA0V:    { addr: 0x007C, width: 8 },
    HW_DMA1V:    { addr: 0x007D, width: 8 },
    HW_DMA2V:    { addr: 0x007E, width: 8 },
    HW_DMA3V:    { addr: 0x007F, width: 8 },
    HW_INTETC01: { addr: 0x0079, width: 8 },
    HW_INTETC23: { addr: 0x007A, width: 8 },

    /* --- Z80 / Sound CPU --------------------------------------------- */
    HW_SOUNDCPU_CTRL: { addr: 0x00B8, width: 16 },
    SOUNDCPU_CTRL:    { addr: 0x00B8, width: 16 },
    HW_Z80_NMI:       { addr: 0x00BA, width: 8 },  Z80_NMI:  { addr: 0x00BA, width: 8 },
    HW_Z80_COMM:      { addr: 0x00BC, width: 8 },  Z80_COMM: { addr: 0x00BC, width: 8 },

    /* --- BIOS system zone (ngpc_hw.h §BIOS, HW_REGISTERS.md §3) ------- */
    HW_BAT_VOLT_RAW:  { addr: 0x6F80, width: 16 }, BAT_VOLT: { addr: 0x6F80, width: 8 },
    HW_JOYPAD:        { addr: 0x6F82, width: 8 },  JOYPAD:     { addr: 0x6F82, width: 8 },
    SYS_LEVER:        { addr: 0x6F82, width: 8 },
    HW_USR_BOOT:      { addr: 0x6F84, width: 8 },  USR_BOOT:     { addr: 0x6F84, width: 8 },
    HW_USR_SHUTDOWN:  { addr: 0x6F85, width: 8 },  USR_SHUTDOWN: { addr: 0x6F85, width: 8 },
    HW_USR_ANSWER:    { addr: 0x6F86, width: 8 },  USR_ANSWER:   { addr: 0x6F86, width: 8 },
    HW_LANGUAGE:      { addr: 0x6F87, width: 8 },  LANGUAGE:     { addr: 0x6F87, width: 8 },
    HW_OS_VERSION:    { addr: 0x6F91, width: 8 },  OS_VERSION:   { addr: 0x6F91, width: 8 },

    /* --- K2GE display (ngpc_hw.h §K2GE, HW_REGISTERS.md §5) ---------- */
    HW_DISP_CTL:   { addr: 0x8000, width: 8 },  DISP_CTL0: { addr: 0x8000, width: 8 },
    HW_WIN_X:      { addr: 0x8002, width: 8 },  WIN_X:     { addr: 0x8002, width: 8 },
    HW_WIN_Y:      { addr: 0x8003, width: 8 },  WIN_Y:     { addr: 0x8003, width: 8 },
    HW_WIN_W:      { addr: 0x8004, width: 8 },  WIN_W:     { addr: 0x8004, width: 8 },
    HW_WIN_H:      { addr: 0x8005, width: 8 },  WIN_H:     { addr: 0x8005, width: 8 },
    HW_FRAME_RATE: { addr: 0x8006, width: 8 },  REF:       { addr: 0x8006, width: 8 },
    HW_RAS_H:      { addr: 0x8008, width: 8 },  RAS_H:     { addr: 0x8008, width: 8 },
    HW_RAS_V:      { addr: 0x8009, width: 8 },  RAS_Y:     { addr: 0x8009, width: 8 },
    HW_STATUS:     { addr: 0x8010, width: 8 },  STATUS_2D: { addr: 0x8010, width: 8 },
    STS_RG:        { addr: 0x8010, width: 8 },
    HW_LCD_CTL:    { addr: 0x8012, width: 8 },  CONTROL_2D:{ addr: 0x8012, width: 8 },
    LCD_CTR:       { addr: 0x8012, width: 8 },
    HW_SPR_OFS_X:  { addr: 0x8020, width: 8 },  SPR_X:     { addr: 0x8020, width: 8 },
    HW_SPR_OFS_Y:  { addr: 0x8021, width: 8 },  SPR_Y:     { addr: 0x8021, width: 8 },
    HW_SCR_PRIO:   { addr: 0x8030, width: 8 },  SCRL_PRIO: { addr: 0x8030, width: 8 },
    SCR_PRIORITY:  { addr: 0x8030, width: 8 },
    HW_SCR1_OFS_X: { addr: 0x8032, width: 8 },  SCR1_X:    { addr: 0x8032, width: 8 },
    HW_SCR1_OFS_Y: { addr: 0x8033, width: 8 },  SCR1_Y:    { addr: 0x8033, width: 8 },
    HW_SCR2_OFS_X: { addr: 0x8034, width: 8 },  SCR2_X:    { addr: 0x8034, width: 8 },
    HW_SCR2_OFS_Y: { addr: 0x8035, width: 8 },  SCR2_Y:    { addr: 0x8035, width: 8 },
    HW_BG_CTL:     { addr: 0x8118, width: 8 },  BG_COL:    { addr: 0x8118, width: 8 },
    HW_GE_MODE:    { addr: 0x87E2, width: 8 },  GE_MODE:   { addr: 0x87E2, width: 8 },
    RESET:         { addr: 0x87E0, width: 8 },
    VERSION:       { addr: 0x87FE, width: 8 },
  };

  const PTR_TYPE_RE = /\*\s*\(\s*(?:volatile\s+)?(u8|u16|u32|u_char|u_short|u_long|s8|s16|s32|unsigned\s+char|unsigned\s+short|unsigned\s+long|char|short|int|long)\s*\*\s*\)\s*/;

  // cc900 type widths (T900_DENSE_REF.md §3). Note: `int` is 16-bit on
  // cc900 — NOT the 32-bit default of most modern C compilers.
  //   char / unsigned char / u8 / s8 / u_char : 1 byte
  //   short / unsigned short / u16 / s16 / int / unsigned int / u_short : 2 bytes
  //   long / unsigned long / u32 / s32 / u_long : 4 bytes
  //   pointer (far): 4 bytes
  function typeWidth(t) {
    const x = t.replace(/\s+/g, ' ').trim();
    if (/^(u8|u_char|unsigned char|char|s8)$/.test(x)) return 8;
    if (/^(u16|u_short|unsigned short|short|s16|int|unsigned int|signed int)$/.test(x)) return 16;
    if (/^(u32|u_long|unsigned long|long|signed long|s32)$/.test(x)) return 32;
    return 8;
  }
  function typeBytes(t) { return typeWidth(t) / 8; }

  // Minimal C89 preprocessor — handles `#define` object-like and function-like
  // macros before the rest of the `#…` lines are stripped. Supports multi-line
  // continuation (C89 §6.10.3.5) so the classic NgpCraft pattern works:
  //
  //   #define SCR_ENTRY(tile, pal, hflip, vflip) \
  //       ((u16)((tile) & 0xFF) | \
  //        (((u16)(hflip) & 1) << 15) | \
  //        (((u16)(vflip) & 1) << 14) | \
  //        (((u16)(pal)   & 0xF) << 9) | \
  //        (((u16)(((tile) >> 8) & 1)) << 8))
  //   (TILEMAPS_SCROLL.md §1.2)
  //
  // Limitations: no macro-calling-macro (single-pass expansion), no `#undef`,
  // no token-pasting (##) / stringising (#). `#include`, `#ifndef`, `#ifdef`,
  // `#pragma` are stripped by the subsequent stripPreprocessor pass
  // (T900_DENSE_REF.md §14 pragmas are cc900-specific, non-runtime).
  function expandUserMacros(src) {
    const macros = [];
    // Match #define (optionally spanning multiple lines via `\`-continuation).
    // We preserve line count by replacing the match with an equal number of
    // blank lines so later error traces line up with the original C source.
    // Pattern: `#define` + (continuation line + `\` + newline)* + final line.
    // Grouping the backslash INSIDE the continuation alternative stops the
    // initial `[^\n]*` from greedily swallowing the trailing `\`.
    src = src.replace(
      /^[ \t]*#define(?:[^\n]*\\\r?\n)*[^\n]*/gm,
      (match) => {
        const lines = countLines(match);
        const joined = match.replace(/\\\r?\n\s*/g, ' ');
        const m = joined.match(/^[ \t]*#define\s+(\w+)(\([^)]*\))?\s*(.*)$/);
        if (m) {
          const [, name, paramsRaw, body] = m;
          // Reserved qualifier names. The NGPC template headers contain
          // `#ifndef NGP_FAR / #define NGP_FAR / #endif` to keep user code
          // portable when no qualifier is needed. If we let that macro into
          // the expansion table, every `NGP_FAR` in the source gets stripped
          // BEFORE the HW-1 lint runs, making every ROM pointer look near
          // and defeating the whole check. We leave the tokens alone here;
          // `stripCC900Qualifiers` removes them at the end of `compile()`.
          if (name === 'NGP_FAR' || name === 'NGP_NEAR') {
            // fall through to blank-line replacement only
          } else if (paramsRaw !== undefined) {
            const params = paramsRaw.slice(1, -1).split(',')
              .map(p => p.trim()).filter(Boolean);
            macros.push({ name, params, body: body.trim(), fn: true });
          } else {
            macros.push({ name, body: body.trim(), fn: false });
          }
        }
        return blankLines(lines);
      }
    );
    // Apply longer names first so e.g. `FOO_BAR` expands before a `FOO` macro
    // accidentally matches inside it (word-boundary check already prevents this
    // but explicit ordering is safer).
    macros.sort((a, b) => b.name.length - a.name.length);

    // Iterate to a fixed point (max 8 passes) so macro-calling-macro works.
    // Target: the `NGP_TILEMAP_BLIT_SCR1` family from the template's
    // `ngpc_tilemap_blit.h` — the outer macro expands to three inner macros
    // (`LOAD_TILES_VRAM`, `LOAD_PALETTES_*`, `PUT_MAP_*`), each of which uses
    // `prefix##_tiles` / `prefix##_map_pals` etc. Single-pass expansion
    // leaves the inner macros literal, so the JS interpreter throws "X is
    // not a function" at runtime — and this is the safe alternative path
    // recommended when NGP_FAR helpers produce corrupted output.
    //
    // Cap at 8 iterations to contain pathological `#define X X+1`-style
    // self-reference. 8 is well above observed nesting depth (2–3).
    const MAX_ITERS = 8;
    for (let iter = 0; iter < MAX_ITERS; iter++) {
      const before = src;
      for (const macro of macros) {
        if (macro.fn) {
          // Function-like: match `NAME(args)` with balanced-paren arg list.
          const re = new RegExp(`\\b${macro.name}\\s*\\(([^()]*(?:\\([^()]*\\)[^()]*)*)\\)`, 'g');
          src = src.replace(re, (_m, argsStr) => {
            // Split args on top-level commas.
            const args = [];
            let depth = 0, buf = '';
            for (const ch of argsStr) {
              if (ch === '(') depth++;
              else if (ch === ')') depth--;
              if (ch === ',' && depth === 0) { args.push(buf.trim()); buf = ''; }
              else buf += ch;
            }
            if (buf.trim() || args.length > 0) args.push(buf.trim());
            let body = macro.body;
            // Substitute each parameter name with the corresponding argument.
            for (let i = 0; i < macro.params.length; i++) {
              body = body.replace(new RegExp(`\\b${macro.params[i]}\\b`, 'g'),
                                  args[i] || '');
            }
            // Token pasting (C99 §6.10.3.3): `A ## B` joins the surrounding
            // tokens into one. Real preprocessors paste post-substitution,
            // which is what we do here (parameter subst already ran). Used
            // by e.g. `NGP_TILEMAP_BLIT_SCR1(sym, base)` → `sym##_tiles`.
            body = body.replace(/\s*##\s*/g, '');
            return body;
          });
        } else {
          // Object-like: bareword replacement, still honour ## in the body.
          let body = macro.body.replace(/\s*##\s*/g, '');
          src = src.replace(new RegExp(`\\b${macro.name}\\b`, 'g'), body);
        }
      }
      if (src === before) break;  // fixed point reached
    }
    return src;
  }

  function stripPreprocessor(src) { return src.replace(/^[ \t]*#.*$/gm, ''); }

  // Fast path for large numeric asset arrays.
  //
  // Problem: generated asset files (from the NGPC asset pipeline) emit
  // declarations like
  //   `const u16 NGP_FAR bg_map_tiles[1024] = { 0x0100, 0x0101, …, 0x010F };`
  // with 500–8000 numeric literals. Every downstream rewrite pass then walks
  // the entire source; passes that iterate character-by-character
  // (rewriteInitializers, the deref marker, etc.) are O(N) on that bulk for
  // no useful work — the body is pure data with nothing to transform.
  //
  // Fix: detect such declarations before the main pipeline, replace the
  // `{literal, literal, …}` body with a short opaque placeholder token. The
  // skeleton `const TYPE name[N] = {__ARR_BODY_K__};` remains in place so
  // the HW-1 lint still sees the decl. At the end of `compile()`, swap the
  // placeholder back to the real comma-separated list — downstream has
  // already turned `{…}` into `[…]` (rewriteInitializers), so the result is
  // a clean JS array literal.
  //
  // Safety: only extract if the body is purely numeric (hex / decimal,
  // optional leading `-`, `u`/`U`/`l`/`L` integer suffixes, commas,
  // whitespace). Anything else — macro ref, identifier, arithmetic — keeps
  // the original path.
  const ARR_FAST_MIN_ELEMENTS = 256;
  function extractLargeArrays(src) {
    const extracted = [];
    // Capture declarations of the shape `...const TYPE [NGP_FAR] name[...]
    // [NGP_FAR] = { BODY };`. BODY is `[^{}]*` to exclude nested braces
    // (which indicate a non-flat initializer we shouldn't touch).
    const RE = /(const\s+(?:(?:extern|static)\s+)?(?:NGP_FAR\s+)?\w+(?:\s+NGP_FAR)?\s+(?:NGP_FAR\s+)?(\w+)\s*\[[^\]]*\]\s*(?:NGP_FAR\s*)?=\s*)\{([^{}]*)\}(\s*;)/g;
    const PURE_NUMERIC = /^(?:\s|,|-|0x[0-9a-fA-F]+|\d+|[uUlL])*$/;
    return {
      src: src.replace(RE, (match, head, _name, body, tail) => {
        if (!PURE_NUMERIC.test(body)) return match;
        // Count elements (tokens that look like numbers) cheaply.
        const elementMatches = body.match(/0x[0-9a-fA-F]+|-?\d+/g);
        if (!elementMatches || elementMatches.length < ARR_FAST_MIN_ELEMENTS) return match;
        // Normalise: drop u/U/l/L suffixes (JS doesn't accept them).
        const elements = elementMatches.join(',');
        const idx = extracted.length;
        extracted.push(elements);
        return `${head}{__ARR_BODY_${idx}__}${tail}`;
      }),
      extracted,
    };
  }

  function reinjectArrays(src, extracted) {
    if (!extracted || extracted.length === 0) return src;
    // rewriteInitializers has converted `{__ARR_BODY_K__}` into
    // `[__ARR_BODY_K__]` for array-shaped inits. Swap the placeholder token
    // for the real list — surrounding `[` / `]` stay put.
    return src.replace(/__ARR_BODY_(\d+)__/g,
      (_m, k) => extracted[Number(k)] || '');
  }

  // Strip inline TLCS-900/H assembly. Real projects (e.g. fast RAM copies
  // in sound drivers) use `__asm__("…")`, `asm("…")`, or the cc900
  // `_asm { … }` block form (T900_DENSE_REF.md §14). The emulator can't
  // run target assembly, so we drop these with a one-shot warning so the
  // student knows the hot-path was skipped. Line count is preserved so
  // error traces still map back to the original source.
  function stripInlineAsm(src) {
    let warned = false;
    // Parenthesised forms: __asm__ ("..."), asm("..."), __asm("...")
    src = src.replace(
      /\b(?:__asm__|__asm|asm)\s*\(\s*(?:"(?:\\.|[^"\\])*"\s*(?::\s*[^)]*)?\s*)*\)\s*;?/g,
      (m) => {
        if (!warned && typeof NGPC_Memory !== 'undefined') {
          NGPC_Memory.warnOnce('asm',
            'Inline __asm__ block detected and skipped — emulator cannot ' +
            'execute TLCS-900/H instructions. The surrounding C still runs.');
          warned = true;
        }
        return blankLines(countLines(m));
      }
    );
    // Block form: _asm { ... } / __asm { ... }  (cc900 extension).
    src = src.replace(
      /\b(?:_asm|__asm)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g,
      (m) => {
        if (!warned && typeof NGPC_Memory !== 'undefined') {
          NGPC_Memory.warnOnce('asm',
            'Inline _asm {…} block detected and skipped — emulator cannot ' +
            'execute TLCS-900/H instructions.');
          warned = true;
        }
        return blankLines(countLines(m));
      }
    );
    return src;
  }
  // Line-preserving strip: replace each removed block with the same number
  // of newlines it spanned, so an error reported on line N of the transpiled
  // JS maps back to line N of the original C source.
  function countLines(s) { return (s.match(/\n/g) || []).length; }
  function blankLines(n) { return '\n'.repeat(n); }

  // Resolve `#include "file.h"` against a caller-provided lookup. Each included
  // file is pasted inline with a leading newline so line numbers from the
  // *referring* file after the include still map roughly to the original
  // source (included content adds lines, which is the standard CPP behaviour).
  // Cycles are broken by a `seen` set — if a header is included twice we emit
  // blank lines the second time instead of re-inlining. Headers not known to
  // the resolver are left as-is (stripPreprocessor drops them later).
  function resolveIncludes(src, resolver, seen = new Set(), depth = 0) {
    if (!resolver) return src;
    if (depth > 32) throw new Error('#include nested too deeply (cycle?)');
    return src.replace(
      /^[ \t]*#include\s+"([^"]+)"\s*$/gm,
      (whole, name) => {
        if (seen.has(name)) return blankLines(1);
        const content = resolver(name);
        if (content == null) return whole;
        const child = new Set(seen);
        child.add(name);
        const inlined = resolveIncludes(content, resolver, child, depth + 1);
        // Wrap with begin/end markers so downstream passes and error traces
        // have a clue where the extra lines came from. Keeps line count
        // predictable for anyone counting manually.
        return `/* === begin include: ${name} === */\n` +
               inlined +
               `\n/* === end include: ${name} === */`;
      }
    );
  }

  // Evaluate `#if`/`#ifdef`/`#ifndef`/`#elif`/`#else`/`#endif` conditional
  // compilation, blanking out inactive branches so the rest of the transpile
  // only sees live code. Tracks `#define`d names in active regions so
  // subsequent `#if NGP_ENABLE_DMA` etc. resolve correctly — this keeps
  // feature-flagged modules (StarGunner-style NGP_ENABLE_SOUND / DEBUG)
  // compilable even when their dependency headers aren't loaded. Expression
  // evaluation is pragmatic: `defined(X)` / `defined X` become 1 or 0, known
  // macro names are substituted with their body, unknown identifiers become
  // 0 (matching C99 §6.10.1). C integer suffixes are stripped; the surviving
  // expression is handed to JS `new Function` for arithmetic/logic eval.
  function evalConditionals(src) {
    const defines = Object.create(null);
    // Stack of frames: { active, seenTrue, hasElse, parentActive }.
    // parentActive tracks whether the surrounding scope is itself active;
    // needed so an `#elif` inside an outer-disabled block stays disabled.
    const stack = [{ active: true, seenTrue: true, hasElse: false, parentActive: true }];

    const out = [];
    const lines = src.split('\n');

    function currentActive() { return stack[stack.length - 1].active; }
    function parentActive() {
      return stack.length <= 1 ? true : stack[stack.length - 2].active;
    }

    function evalExpr(expr) {
      // `defined(X)` and `defined X` → 1/0.
      let e = expr.replace(/\bdefined\s*\(\s*(\w+)\s*\)/g,
                           (_m, n) => (n in defines) ? '1' : '0');
      e = e.replace(/\bdefined\s+(\w+)/g,
                    (_m, n) => (n in defines) ? '1' : '0');
      // Substitute known object-like macros with their body (one pass — good
      // enough for the numeric flags real projects use in #if).
      e = e.replace(/\b([A-Za-z_]\w*)\b/g, (_m, n) => {
        if (n === 'true')  return '1';
        if (n === 'false') return '0';
        if (n in defines) return defines[n] === '' ? '1' : defines[n];
        return '0';
      });
      // Strip C int suffixes — otherwise JS barfs on `1u`.
      e = e.replace(/\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b/g, '$1');
      try {
        return !!(new Function('return (' + (e.trim() || '0') + ')')());
      } catch (_) {
        return false;
      }
    }

    for (const line of lines) {
      const active = currentActive();
      const m = line.match(/^[ \t]*#\s*(if|ifdef|ifndef|elif|else|endif|define|undef)\b(.*)$/);
      if (!m) {
        out.push(active ? line : '');
        continue;
      }
      const directive = m[1];
      const rest = m[2];

      if (directive === 'if' || directive === 'ifdef' || directive === 'ifndef') {
        let val;
        if (active) {
          if (directive === 'if')      val = evalExpr(rest);
          else if (directive === 'ifdef')  val = rest.trim().split(/\s+/)[0] in defines;
          else /* ifndef */                val = !(rest.trim().split(/\s+/)[0] in defines);
        } else {
          val = false;
        }
        stack.push({
          active: active && val,
          seenTrue: val,
          hasElse: false,
          parentActive: active,
        });
        out.push('');
      } else if (directive === 'elif') {
        const top = stack[stack.length - 1];
        if (top.hasElse) { out.push(''); continue; } // malformed, ignore
        let val = false;
        if (top.parentActive && !top.seenTrue) {
          val = evalExpr(rest);
          top.seenTrue = top.seenTrue || val;
        }
        top.active = top.parentActive && val;
        out.push('');
      } else if (directive === 'else') {
        const top = stack[stack.length - 1];
        top.hasElse = true;
        top.active = top.parentActive && !top.seenTrue;
        if (top.active) top.seenTrue = true;
        out.push('');
      } else if (directive === 'endif') {
        if (stack.length > 1) stack.pop();
        out.push('');
      } else if (directive === 'define') {
        if (active) {
          // Record name → body (body may be empty = "defined with no value").
          // Function-like defines are recorded as empty ("1") for #if purposes;
          // expandUserMacros does the real expansion later.
          const dm = rest.match(/^\s*(\w+)(?:\([^)]*\))?\s*(.*)$/);
          if (dm) {
            const body = dm[2].trim();
            defines[dm[1]] = body;
          }
          out.push(line);      // keep for expandUserMacros
        } else {
          out.push('');
        }
      } else if (directive === 'undef') {
        if (active) {
          const un = rest.trim().split(/\s+/)[0];
          if (un) delete defines[un];
          out.push(line);
        } else {
          out.push('');
        }
      } else {
        out.push(active ? line : '');
      }
    }
    return out.join('\n');
  }
  function stripComments(src) {
    return src
      .replace(/\/\*[\s\S]*?\*\//g, (m) => blankLines(countLines(m)))
      .replace(/\/\/[^\n]*/g, '');
  }

  // Hardware-fidelity error system.
  //
  // The live editor runs a JS-based interpreter that is far more permissive
  // than cc900 + real NGPC silicon. Classes of bugs that would silently
  // corrupt display, hang the CPU, or fail to compile on the target
  // toolchain can execute cleanly here. That defeats the debug-value of the
  // editor.
  //
  // Each rule (see HW-x below) pushes an `HwError` to a shared accumulator
  // threaded through `compile()`. A single `HwFidelityError` carrying all
  // collected errors is thrown at the end, so the user gets every violation
  // in one pass instead of fix-one-rerun-repeat.
  //
  // Design rules:
  //  - No escape hatch: if the code breaks on silicon, it breaks in-editor.
  //  - Each error carries: rule ID, title, location, why (hardware symptom),
  //    fix (concrete code change).
  //  - Messages are actionable — a dev reading one knows exactly what to edit.
  class HwFidelityError extends Error {
    constructor(errors) {
      const header = errors.length === 1
        ? 'Hardware-fidelity error — code would break on real hardware:'
        : `Hardware-fidelity — ${errors.length} error(s) would break on real hardware:`;
      const body = errors.map(formatHwError).join('\n\n');
      super(header + '\n\n' + body);
      this.hwErrors = errors;
      this.name = 'HwFidelityError';
    }
  }
  function formatHwError(e) {
    const out = [`[${e.rule}] ${e.title}`];
    if (e.lines) e.lines.forEach(l => out.push('  ' + l));
    if (e.extra) e.extra.forEach(l => out.push('  ' + l));
    if (e.why)   out.push('', '  On hardware: ' + e.why);
    if (e.fix)   out.push('  Fix: ' + e.fix);
    return out.join('\n');
  }

  // HW-3b — loop variable declared inside `for (…)`.
  //
  // Hardware context: the `for (TYPE name = …; …; …)` init-declaration form
  // is a C99 extension. cc900 is C89 strict and rejects it with a syntax
  // error at compile time. The JS interpreter accepts it without warning,
  // so the bug only surfaces when the user moves the code to a real build.
  //
  // Detection: regex on `for (` followed by any recognised type keyword
  // (primitive or storage-class-qualified) followed by a name and `=`.
  function lintForDecl(src, errors) {
    const PRIM = '(?:u8|u16|u32|s8|s16|s32|u_char|u_short|u_long|char|short|int|long|bool)';
    const STORAGE = '(?:const|static|register|volatile|unsigned|signed)';
    const RE = new RegExp(
      '\\bfor\\s*\\(\\s*(?:' + STORAGE + '\\s+)*' + PRIM + '\\s+(\\w+)\\s*=',
      'g'
    );
    let m;
    while ((m = RE.exec(src)) !== null) {
      const line = src.slice(0, m.index).split('\n').length;
      errors.push({
        rule: 'HW-3b',
        title: `loop variable declared inside for() — "${m[1]}"`,
        lines: [`Line ${line}: ${m[0]} …`],
        why: 'cc900 rejects declarations in the for-init clause (C99 extension). ' +
             'All locals must be declared at the top of the enclosing block.',
        fix: `Declare \`${m[1]}\` at the start of the block, then use \`for (${m[1]} = 0; …)\`.`,
      });
    }
  }

  // HW-2 — `volatile` missing on ISR-shared variable.
  //
  // Hardware context: cc900 will cache a file-scope global in a CPU register
  // inside a non-ISR loop and never re-read memory. If an `__interrupt`
  // function writes the memory location, the main-loop register stays stale
  // → `while (!g_flag) { }` becomes an infinite wait on real silicon. The
  // `volatile` qualifier forces every access through memory.
  //
  // Detection shape: find every `__interrupt` function body, collect the
  // bareword scalar lvalues it writes (assignment / ++ / -- / compound ops),
  // cross-reference with file-scope scalar globals. Any global that is
  // written by an ISR and declared without `volatile` is an HW-2 error.
  //
  // Scope v1 (precision > recall): scalar globals only. Arrays, struct
  // fields, and pointer-dereference writes (`p->x`, `a.b`, `arr[i]`) are
  // ignored — their coherence needs more analysis than a regex pass gives.
  function lintIsrVolatile(src, errors) {
    const PRIM = '(?:u8|u16|u32|s8|s16|s32|u_char|u_short|u_long|char|short|int|long|bool)';

    // --- Pass 1: gather file-scope scalar globals.
    // Match `[storage]* [volatile] [const] TYPE name [= …];` anchored to the
    // start of a line. We skip array-shaped (`name[…]`) and pointer-shaped
    // (`*name`) decls so the detector stays false-positive-free.
    const globals = new Map();  // name → { line, hasVolatile }
    const GLOBAL_RE = new RegExp(
      '(?:^|\\n)[ \\t]*' +
      '((?:(?:extern|static|register)\\s+)*)' +
      '(volatile\\s+)?' +
      '(?:const\\s+)?' +
      PRIM + '\\s+' +
      '(\\w+)\\s*' +
      '(?:=[^;]*)?;',
      'g'
    );
    let gm;
    while ((gm = GLOBAL_RE.exec(src)) !== null) {
      const name = gm[3];
      const hasVolatile = !!gm[2];
      const line = src.slice(0, gm.index).split('\n').length;
      if (!globals.has(name)) globals.set(name, { line, hasVolatile });
    }
    if (globals.size === 0) return;

    // --- Pass 2: find every `__interrupt` function and its body range.
    // Supports both orderings: `void __interrupt name(…)` and
    // `__interrupt void name(…)`. The `(?:\s+\w+)*` consumes return-type /
    // qualifier words between `__interrupt` and the function name; the
    // trailing `\s+(\w+)\s*\(` pins the capture to the identifier directly
    // before the argument list.
    const ISR_RE = /\b__interrupt\b(?:\s+\w+)*\s+(\w+)\s*\([^()]*\)\s*\{/g;
    let im;
    while ((im = ISR_RE.exec(src)) !== null) {
      const isrName = im[1];
      // Brace-match to find the closing `}`. ISR bodies tend to be short;
      // a naive counter is fine.
      let depth = 1;
      let k = ISR_RE.lastIndex;
      while (k < src.length && depth > 0) {
        const ch = src[k];
        if (ch === '{') depth++;
        else if (ch === '}') depth--;
        k++;
      }
      const body = src.slice(ISR_RE.lastIndex, k - 1);

      // --- Pass 3: find bareword scalar lvalues written in the body.
      // Matches `name = …` (but not `==`), `name++`, `name--`, `name +=`,
      // `name -=`, etc. Qualified writes like `p->x =`, `a.b =`, `arr[i] =`
      // are ignored — v1 targets scalar globals only.
      const writes = new Set();
      const LVAL_RE = /(?<![.\]\w>])\b(\w+)\s*(?:=(?!=)|\+\+|--|\+=|-=|\*=|\/=|%=|&=|\|=|\^=|<<=|>>=)/g;
      let lm;
      while ((lm = LVAL_RE.exec(body)) !== null) {
        writes.add(lm[1]);
      }

      // --- Pass 4: cross-reference.
      for (const name of writes) {
        const g = globals.get(name);
        if (!g || g.hasVolatile) continue;
        // Dedupe: same (name, isr) only reported once per compile.
        const key = `HW-2|${name}|${isrName}`;
        if (errors.some(e => e._key === key)) continue;
        errors.push({
          _key: key,
          rule: 'HW-2',
          title: `volatile missing on ISR-shared variable "${name}"`,
          lines: [`Declared line ${g.line} without volatile`],
          extra: [`Written by __interrupt function "${isrName}"`],
          why: 'cc900 caches the variable in a CPU register inside non-ISR code. ' +
               "The ISR's write to memory never reaches the register → stale reads " +
               'and infinite wait loops like `while (!flag) {}` on real hardware.',
          fix: `volatile <type> ${name};`,
        });
      }
    }
  }

  // HW-1 — NGP_FAR mismatch.
  //
  // Hardware context: cc900 uses 16-bit (near) pointers by default. Main RAM
  // sits at 0x004000–0x005FFF (near-reachable); cartridge ROM starts at
  // 0x200000, completely out of near range. `const` file-scope data lives in
  // ROM. Passing a near pointer to a parameter declared `NGP_FAR *` makes
  // cc900 zero-extend 16→24 bits: fine for RAM (0x004000 → 0x004000), broken
  // for ROM (near only encodes low 16 bits, so the pointer resolves to
  // 0x00xxxx instead of 0x20xxxx) → reads wrong memory → corrupted image.
  //
  // Detection shape: if a call site passes a `const`-without-NGP_FAR
  // identifier to a parameter declared `NGP_FAR *`, that's a silent hw bug.
  // The JS interpreter strips NGP_FAR and treats all pointers as JS numbers,
  // so this bug class is INVISIBLE in-editor until a ROM is flashed.
  //
  // Scope (precision > recall): we only flag the unambiguous case. `const`
  // declarations at file scope with no NGP_FAR (either position) passed to a
  // NGP_FAR pointer parameter. Non-const arguments (RAM → zero-extend OK)
  // and declarations that already carry NGP_FAR are skipped.
  function lintNgpFar(src, errors) {
    const PRIM = '(?:u8|u16|u32|s8|s16|s32|u_char|u_short|u_long|char|short|int|long)';

    // --- Pass 1: collect function prototypes / definitions with NGP_FAR params.
    // Shape: `TYPE name(PARAMS) ;|{`. We require `NGP_FAR` + `*` inside PARAMS
    // so the prototype table stays tight (calls without NGP_FAR in the
    // signature are never in the map, making call-site scanning cheap).
    const farParams = new Map();  // fnName → Set<paramIndex>
    const PROTO_RE = /(\w+)\s*\(([^()]*)\)\s*[;{]/g;
    const CTL_KW = /^(?:if|while|for|switch|return|sizeof|do|else)$/;
    let pm;
    while ((pm = PROTO_RE.exec(src)) !== null) {
      const fnName = pm[1];
      if (CTL_KW.test(fnName)) continue;
      const paramStr = pm[2];
      if (!/\bNGP_FAR\b/.test(paramStr)) continue;
      const params = paramStr.split(',');
      const farSet = farParams.get(fnName) || new Set();
      for (let i = 0; i < params.length; i++) {
        // A NGP_FAR pointer parameter needs both the qualifier and a `*`.
        if (/\bNGP_FAR\b[^,]*\*/.test(params[i])) farSet.add(i);
      }
      if (farSet.size > 0) farParams.set(fnName, farSet);
    }
    if (farParams.size === 0) return;

    // --- Pass 2: collect `const` declarations, classify as with/without NGP_FAR.
    // We match file-scope const arrays / structs (no `*` between type and
    // name → excludes `const TYPE *name` which is a pointer in RAM, not the
    // data). Two qualifier positions accepted:
    //   (a) pre-name  : `const u16 NGP_FAR name[N] = {...}`
    //   (b) post-name : `const u16 name[N] NGP_FAR = {...}`
    // cc900 accepts both — the qualifier annotates the storage, not the name.
    const romNoFar = new Map();   // name → line number of first decl without NGP_FAR
    const romWithFar = new Set(); // names declared WITH NGP_FAR somewhere
    const lines = src.split('\n');
    const DECL_RE = new RegExp(
      '(?:^|[;{}\\s])' +                     // boundary
      '(?:(?:extern|static)\\s+)*' +         // optional storage class
      'const\\s+(?:' + PRIM + '|\\w+)\\s+' + // const TYPE (primitive or user type)
      '(NGP_FAR\\s+)?' +                     // (1) optional pre-name NGP_FAR
      '(\\w+)\\s*' +                         // (2) name
      '(\\[[^\\]]*\\])?' +                   // (3) optional [N]
      '([^=;]*)' +                           // (4) tail up to = or ;
      '(?:=|;)'
    );
    for (let li = 0; li < lines.length; li++) {
      const m = DECL_RE.exec(lines[li]);
      if (!m) continue;
      const preFar = !!m[1];
      const name = m[2];
      const tail = m[4] || '';
      const postFar = /\bNGP_FAR\b/.test(tail);
      const hasFar = preFar || postFar;
      // Skip if the matched "name" is itself a type keyword — the outer
      // `const TYPE name` regex can misfire on lines like `const u16 name`
      // when `name` happens to be another type keyword.
      if (new RegExp('^' + PRIM + '$').test(name)) continue;
      if (hasFar) romWithFar.add(name);
      else if (!romNoFar.has(name)) romNoFar.set(name, li + 1);
    }
    if (romNoFar.size === 0) return;

    // --- Pass 3: scan call sites, cross-reference against both tables.
    const CALL_RE = /(\w+)\s*\(([^()]*)\)/g;
    const seen = new Set();  // dedupe (sym, fn, argIdx) per compile
    for (let li = 0; li < lines.length; li++) {
      const line = lines[li];
      let cm;
      CALL_RE.lastIndex = 0;
      while ((cm = CALL_RE.exec(line)) !== null) {
        const fnName = cm[1];
        const farSet = farParams.get(fnName);
        if (!farSet) continue;
        // Skip if this occurrence is a prototype / definition, not a call.
        // Prototypes contain NGP_FAR inside the parens; calls don't.
        if (/\bNGP_FAR\b/.test(cm[2])) continue;
        const args = cm[2].split(',').map(a => a.trim());
        for (const idx of farSet) {
          const arg = args[idx];
          if (!arg) continue;
          // Lead identifier: strip leading `&` / `*` / whitespace, take first word.
          const lead = /^[&*]?\s*([A-Za-z_]\w*)/.exec(arg);
          if (!lead) continue;
          const sym = lead[1];
          if (romWithFar.has(sym)) continue;
          if (!romNoFar.has(sym)) continue;
          // Skip if arg is an array-subscript expression that doesn't take
          // the address (e.g. `tiles[i]` passes a u16 value, type error
          // the user will hit separately). Keep `&tiles[i]`.
          if (/\[/.test(arg) && !/^&/.test(arg)) continue;
          const key = `${sym}|${fnName}|${idx}`;
          if (seen.has(key)) continue;
          seen.add(key);
          const declLi = romNoFar.get(sym);
          errors.push({
            rule: 'HW-1',
            title: `NGP_FAR missing on ROM data "${sym}"`,
            lines: [`Declared line ${declLi} without NGP_FAR`],
            extra: [`Passed to ${fnName}() as argument #${idx + 1} at line ${li + 1}`],
            why: 'cc900 emits a 16-bit near pointer here. When zero-extended to 24 bits ' +
                 'it resolves to 0x00xxxx instead of 0x20xxxx in cartridge ROM — the live ' +
                 'editor strips the qualifier so execution looks fine, but on real hardware ' +
                 'the image will be corrupted.',
            fix: `const TYPE NGP_FAR ${sym}[...]`,
          });
        }
      }
    }
  }

  // HW-3c — Toshiba cc900 crash on `s8 != s8 || s8 != s8` chains.
  //
  // Hardware context: validated cc900 internal-error pattern (see NGPC bug DB
  // entry CC900_S8_OR_NESTED_DECL). Two signed-byte inequality comparisons
  // joined by `||` make the original Toshiba compiler crash. The JS interpreter
  // accepts it; the user discovers the bug when migrating to cc900.
  //
  // Detection: heuristic — find a `||` whose two operands both look like
  // `<expr> != <expr>` and at least one operand is parenthesised or short.
  // Conservative regex (false negatives possible, false positives unlikely).
  function lintS8OrChain(src, errors) {
    const RE = /([A-Za-z_]\w*\s*!=\s*[^|;\n]{1,40})\|\|(\s*[A-Za-z_]\w*\s*!=\s*[^|;\n]{1,40})/g;
    let m;
    const seen = new Set();
    while ((m = RE.exec(src)) !== null) {
      const line = src.slice(0, m.index).split('\n').length;
      const key = `HW-3c|${line}`;
      if (seen.has(key)) continue;
      seen.add(key);
      errors.push({
        rule: 'HW-3c',
        title: '`a != b || c != d` chain — Toshiba cc900 internal error',
        lines: [`Line ${line}: ${m[0].slice(0, 80).replace(/\s+/g, ' ')}`],
        why: 'Toshiba cc900 crashes on signed-byte inequality chains joined by ||. ' +
             'The JS interpreter accepts it but the real compiler will fail.',
        fix: 'Split into separate if statements, or cast operands to int: ' +
             '`if (a != b) { ... } else if (c != d) { ... }`.',
      });
    }
  }

  // HW-3d (REMOVED) — Initialised declaration inside nested block.
  //
  // The original bug DB note (CC900_S8_OR_NESTED_DECL) was too vague. Pure
  // depth >= 2 detection produces false positives on valid C89 like:
  //
  //     for (i = 0; i < N; i++) {
  //         u16 c0 = src[i];        // VALID — at top of inner block
  //         do_thing(c0);
  //     }
  //
  // The actual cc900 issue is most likely "mixed declarations and statements"
  // (decl after a non-decl statement in the same block = C99 extension), but
  // we don't yet have a precise reproducer captured in the bug DB. Until then
  // the rule stays out — false positives on flagship examples are worse than
  // missing the case. To re-enable: capture an exact failing snippet on cc900,
  // narrow the detection to that shape.

  // HW-4 — Large static array declared inside a function (stack overflow risk).
  //
  // Hardware context: NGPC stack is small (~256 bytes typical, frame-overlay
  // dependent). Declaring `static u8 buf[N]` inside a function with N > 256
  // is fine on cc900 (static = file-scope BSS) but WITHOUT `static` the array
  // lands on the stack and overflows on real hardware. Validated by Fix #23
  // (s_bg_dma_phase_x[32][76] = 2432 bytes BSS eliminated saved 2400 stack
  // bytes) — see bugs_silicon.json STACK_OVERFLOW_LARGE_BSS_LOCAL.
  //
  // Detection: any local (non-static) array `TYPE name[N]` declared inside a
  // function body where N is a literal integer > 256 (rough threshold).
  function lintLargeStackArray(src, errors) {
    const PRIM = '(?:u8|u16|u32|s8|s16|s32|char|short|int|long)';
    // Find `TYPE name[N]` not preceded by `static` on the same line.
    const RE = new RegExp(
      '(^|[^\\w])((?:const\\s+)?(?:unsigned\\s+|signed\\s+)?' + PRIM + ')\\s+(\\w+)\\s*\\[\\s*(\\d+)\\s*\\]',
      'gm'
    );
    let m;
    while ((m = RE.exec(src)) !== null) {
      const N = parseInt(m[4], 10);
      if (N <= 256) continue;
      // Check this isn't a static / extern / typedef decl by looking at the
      // line context — skip if any of those keywords appears on the same line
      // before our match.
      const lineStart = src.lastIndexOf('\n', m.index) + 1;
      const lineEnd = src.indexOf('\n', m.index);
      const line = src.slice(lineStart, lineEnd === -1 ? src.length : lineEnd);
      if (/\b(?:static|extern|typedef)\b/.test(line.slice(0, m.index - lineStart))) continue;
      // Walk back from the match position counting braces. Depth >= 1 = inside
      // a function body. (Walk from m.index, not lineStart, so single-line code
      // is handled correctly.)
      let depth = 0;
      for (let i = m.index - 1; i >= 0; i--) {
        const c = src[i];
        if (c === '}') depth--;
        else if (c === '{') depth++;
      }
      if (depth < 1) continue;  // file scope — fine
      const lineNum = src.slice(0, m.index).split('\n').length;
      const bytesEstimate =
        N * (m[2].includes('16') || m[2] === 'short' ? 2
            : m[2].includes('32') || m[2] === 'long' ? 4
            : 1);
      errors.push({
        rule: 'HW-4',
        title: `large stack array — "${m[3]}[${N}]" (~${bytesEstimate} bytes)`,
        lines: [`Line ${lineNum}: ${line.trim().slice(0, 80)}`],
        why: 'NGPC stack is small (typically <512 bytes). A local array this size ' +
             'will overflow the stack and corrupt return addresses on real hardware. ' +
             'The JS interpreter has effectively unlimited stack so the bug is invisible here.',
        fix: `Make it \`static ${m[2]} ${m[3]}[${N}]\` (file-scope BSS — costs zero stack), ` +
             'or move the declaration to file scope.',
      });
    }
  }

  // Strip cc900-specific qualifier keywords so the remaining passes don't
  // have to know about them. All are compile-time hints that don't affect
  // runtime semantics in a JS-transpiled emulator:
  //   __far / __near / __tiny      — pointer size qualifiers (T900_DENSE_REF §4)
  //   __cdecl / __adecl            — calling convention tags
  //   __interrupt                  — ISR marker (unused here, we don't fire IRQs)
  //   __regbank(N)                 — register bank selector
  //   NGP_FAR / NGP_NEAR           — template-provided expanders for __far/__near
  //                                  (ngpc_gfx.h:17 `#ifndef NGP_FAR #define NGP_FAR`)
  function stripCC900Qualifiers(src) {
    return src
      .replace(/\b__far\b|\b__near\b|\b__tiny\b/g, '')
      .replace(/\b__cdecl\b|\b__adecl\b/g, '')
      .replace(/\b__interrupt\b/g, '')
      .replace(/\b__regbank\s*\(\s*-?\d+\s*\)/g, '')
      .replace(/\bNGP_FAR\b|\bNGP_NEAR\b/g, '');
  }

  // Rewrite `enum { NAME = VALUE, OTHER, … };` declarations to a series of
  // JS `const` assignments. Values can be implicit (previous + 1) or explicit
  // (`NAME = 10`). Handles typedef-enum form too: `typedef enum { A, B } Name;`.
  function rewriteEnums(src) {
    const enumBlock = /(?:typedef\s+)?enum(?:\s+\w+)?\s*\{([^{}]*)\}\s*(\w+)?\s*;/g;
    return src.replace(enumBlock, (match, body) => {
      const entries = body.split(',').map(s => s.trim()).filter(Boolean);
      const out = [];
      let current = 0;
      for (const e of entries) {
        const eq = e.indexOf('=');
        if (eq !== -1) {
          const name = e.slice(0, eq).trim();
          const val  = e.slice(eq + 1).trim();
          out.push(`const ${name} = (${val});`);
          const n = Number(val);
          if (!Number.isNaN(n)) current = n + 1;
          else current++;
        } else {
          out.push(`const ${e} = ${current};`);
          current++;
        }
      }
      // Preserve the number of lines the enum block spanned: emit the
      // constants on one logical line then pad with matching newlines.
      return out.join(' ') + blankLines(countLines(match));
    });
  }

  // Expand `sizeof(TYPE)` to a literal byte count per cc900 widths
  // (T900_DENSE_REF §3). For user struct types we can't compute a size
  // without layout tracking, so we leave `sizeof(UserStruct)` alone — the
  // runtime throws "sizeof is not defined" giving a clear signal.
  function rewriteSizeof(src) {
    const widthMap = {
      'u8': 1, 'u_char': 1, 'char': 1, 'signed char': 1, 'unsigned char': 1, 's8': 1, 'bool': 1,
      'u16': 2, 'u_short': 2, 'short': 2, 'signed short': 2, 'unsigned short': 2,
      'int': 2, 'signed int': 2, 'unsigned int': 2, 's16': 2,
      'u32': 4, 'u_long': 4, 'long': 4, 'signed long': 4, 'unsigned long': 4, 's32': 4,
    };
    return src.replace(
      /\bsizeof\s*\(\s*([a-zA-Z_][a-zA-Z0-9_\s*]*?)\s*\)/g,
      (whole, type) => {
        const t = type.replace(/\s+/g, ' ').trim();
        // Pointer type: cc900 far pointer = 4 bytes (v1, T900_DENSE_REF §3).
        if (t.endsWith('*')) return '4';
        if (widthMap[t] !== undefined) return String(widthMap[t]);
        return whole;  // unknown type — leave as-is
      }
    );
  }

  // Hoist function-local `static TYPE name [= init];` to module scope so the
  // state survives across calls (C99 §6.2.4.3). JS `let`/`var` inside a
  // function body resets every invocation, which silently breaks shmup-style
  // one-shot init flags (`static u8 s_once = 0;`) and per-function counters.
  // Each hoisted decl is renamed `__s_<func>_<name>` to avoid collisions
  // between same-named statics in different functions, and references inside
  // the body are rewritten with the same name. Scan happens before
  // rewriteFunctions / rewriteDeclarations so the C syntax is still present.
  function hoistFunctionStatics(src, userTypes) {
    const typeKWs = [
      'u8','u16','u32','s8','s16','s32',
      'u_char','u_short','u_long',
      'char','short','int','long','bool','void',
    ];
    const allTypes = [...typeKWs, ...userTypes];
    // Function signature probe: `(static )?<type-tokens> <name>(...) {`
    // — where type-tokens may be `unsigned TYPE`, `const TYPE`, plain TYPE.
    const sigRe = new RegExp(
      `\\b(?:static\\s+)?(?:const\\s+)?(?:(?:unsigned|signed)\\s+)?` +
      `(?:${allTypes.join('|')})\\b\\s*\\*?\\s*(\\w+)\\s*\\(([^)]*)\\)\\s*\\{`,
      'g'
    );
    // Capture the C type prefix too — hoisting it back out means
    // collectIntVarWidths / collectPointers / stripArraySizes can still
    // classify the hoisted decl correctly (widths, pointer-ness, array size).
    const staticRe = new RegExp(
      `\\bstatic\\s+((?:const\\s+)?(?:(?:unsigned|signed)\\s+)?` +
      `(?:${allTypes.join('|')})\\b\\s*\\*?\\s*)` +
      `(\\w+)(\\s*\\[[^\\]]*\\])?\\s*(?:=\\s*([^;]+))?\\s*;`,
      'g'
    );
    const hoisted = [];
    const edits = [];  // {start, end, replacement}
    let m;
    while ((m = sigRe.exec(src)) !== null) {
      const fnName = m[1];
      // Locate matching `}` with balanced braces.
      const bodyStart = m.index + m[0].length;
      let depth = 1, i = bodyStart;
      while (i < src.length && depth > 0) {
        const c = src[i];
        if      (c === '{') depth++;
        else if (c === '}') depth--;
        i++;
      }
      const bodyEnd = i - 1;
      const body = src.slice(bodyStart, bodyEnd);
      let newBody = body;
      let mutated = false;
      // Replace each matching static decl. We do it in two-stage: first
      // collect all names, then rewrite the decl + references.
      const found = [];
      staticRe.lastIndex = 0;
      let sm;
      while ((sm = staticRe.exec(body)) !== null) {
        found.push({
          full: sm[0], typePrefix: sm[1], name: sm[2],
          arraySuffix: sm[3] || '', init: sm[4],
        });
      }
      for (const s of found) {
        const hoistedName = `__s_${fnName}_${s.name}`;
        newBody = newBody.replace(
          s.full,
          `/* hoisted: static ${s.name} */`
        );
        newBody = newBody.replace(
          new RegExp(`\\b${s.name}\\b`, 'g'),
          hoistedName
        );
        const init = s.init !== undefined
          ? ` = ${s.init.trim()}`
          : (s.arraySuffix ? '' : ' = 0');
        hoisted.push(`${s.typePrefix.trim()} ${hoistedName}${s.arraySuffix}${init};`);
        mutated = true;
      }
      if (mutated) {
        edits.push({ start: bodyStart, end: bodyEnd, replacement: newBody });
      }
      // Keep scanning from after the function body so nested defs don't trip
      // the outer regex (cc900 doesn't allow nested C funcs anyway).
      sigRe.lastIndex = i;
    }
    // Apply edits back-to-front so offsets stay valid.
    edits.sort((a, b) => b.start - a.start);
    for (const e of edits) {
      src = src.slice(0, e.start) + e.replacement + src.slice(e.end);
    }
    if (hoisted.length) {
      src = hoisted.join('\n') + '\n' + src;
    }
    return src;
  }

  // Strip C integer-literal suffixes (`u`, `U`, `l`, `L` and combinations like
  // `ul`, `ull`, `LL`). Converter-generated tables use forms like `4u`, `16u`,
  // `256u`; JS would throw "identifier starts immediately after numeric
  // literal". Hex/octal/decimal literals all accept the same suffix set in C99
  // §6.4.4.1; the number grammar before the suffix stays untouched.
  function stripIntLiteralSuffixes(src) {
    return src.replace(/(\b(?:0[xX][0-9a-fA-F]+|\d+))[uUlL]+\b/g, '$1');
  }

  // C char literals (`'X'`) are integer character codes. JS treats `'X'` as a
  // length-1 string, which silently breaks bitwise ops (`'X' & 0xFF` → 0). We
  // convert each char literal to its numeric ASCII value at transpile time.
  // Handles the standard backslash escapes defined by C99 §6.4.4.4.
  //
  // Walks the source tracking whether we're inside a "..." string so an
  // apostrophe embedded in a string literal ("don't") isn't misparsed as a
  // char-literal delimiter. Previously rewrote `"move 'O'"` → `"move 79"`.
  function rewriteCharLiterals(src) {
    const esc = { n: 10, r: 13, t: 9, '0': 0, a: 7, b: 8, f: 12, v: 11,
                  "'": 39, '"': 34, '\\': 92, '?': 63 };
    const charRe = /^'(\\x[0-9a-fA-F]{1,2}|\\[^']|[^'\\])'/;
    function replace(match) {
      const body = match.slice(1, -1);
      if (body.length === 1) return String(body.charCodeAt(0));
      if (body.startsWith('\\x')) return String(parseInt(body.slice(2), 16));
      const c = body[1];
      return String(esc[c] !== undefined ? esc[c] : c.charCodeAt(0));
    }
    let out = '';
    let i = 0;
    while (i < src.length) {
      const c = src[i];
      if (c === '"') {
        // Copy the whole string literal verbatim so embedded `'` doesn't
        // trigger a false char-literal match.
        const start = i;
        i++;
        while (i < src.length && src[i] !== '"') {
          if (src[i] === '\\' && i + 1 < src.length) i += 2;
          else i++;
        }
        if (i < src.length) i++;   // consume closing "
        out += src.slice(start, i);
        continue;
      }
      if (c === "'") {
        const m = src.slice(i).match(charRe);
        if (m) {
          out += replace(m[0]);
          i += m[0].length;
          continue;
        }
      }
      out += c;
      i++;
    }
    return out;
  }

  // Scan for `typedef` declarations and collect the defined names so later
  // passes treat them as types. Handles:
  //   typedef struct|union|enum ... { ... } Name;   (balanced braces)
  //   typedef EXISTING Name;                         (simple alias)
  //   typedef void (*Name)(...);                     (function pointer)
  function extractUserTypes(src) {
    const names = new Set();
    const structRe = /typedef\s+(?:struct|union|enum)(?:\s+\w+)?\s*\{[\s\S]*?\}\s*(\w+)\s*;/g;
    let m;
    while ((m = structRe.exec(src)) !== null) names.add(m[1]);
    const fptrRe = /typedef\s+[^;]+?\(\s*\*\s*(\w+)\s*\)[^;]*;/g;
    while ((m = fptrRe.exec(src)) !== null) names.add(m[1]);
    const simpleRe = /typedef\s+(?:\w+\s+)+(\w+)\s*;/g;
    while ((m = simpleRe.exec(src)) !== null) names.add(m[1]);
    return names;
  }

  // Split comma-separated multi-var declarations into individual statements
  // so every declared name gets its own `TYPE NAME = …;` form.
  //   `u8 x = 76, y = 72;` → `u8 x = 76; u8 y = 72;`
  // Without this, collectIntVarWidths misses the second variable and the
  // wrapIntOps pass greedily consumes across the comma.
  function splitMultiVarDecls(src, userTypes) {
    const typeAlt = [...TYPE_KEYWORDS, ...userTypes].join('|');
    const storageAlt = STORAGE_KEYWORDS.join('|');
    const re = new RegExp(
      `(^|[;{\\n\\r])(\\s*)` +
      `((?:(?:${storageAlt})\\s+)*(?:(?:unsigned|signed)\\s+)?` +
      `(?:${typeAlt})\\b\\s*(?:\\*\\s*)?)` +
      `([A-Za-z_]\\w*(?:\\s*=\\s*[^,;]+)?` +
      `(?:\\s*,\\s*[A-Za-z_]\\w*(?:\\s*=\\s*[^,;]+)?)+)\\s*;`,
      'g'
    );
    return src.replace(re, (_m, pre, ws, typeStr, body) => {
      // Split body by commas outside parens/brackets.
      const items = [];
      let depth = 0, buf = '';
      for (const ch of body) {
        if (ch === '(' || ch === '[') depth++;
        else if (ch === ')' || ch === ']') depth--;
        if (ch === ',' && depth === 0) {
          items.push(buf.trim()); buf = '';
        } else {
          buf += ch;
        }
      }
      if (buf.trim()) items.push(buf.trim());
      return pre + ws + items.map(it => `${typeStr}${it};`).join(' ');
    });
  }

  function stripTypedefs(src) {
    // Balanced-brace strip for struct/union/enum typedefs, preserving the
    // number of newlines they spanned so line numbers stay in sync.
    let out = src;
    while (true) {
      const m = out.match(/typedef\s+(?:struct|union|enum)(?:\s+\w+)?\s*\{/);
      if (!m) break;
      let i = m.index + m[0].length;
      let depth = 1;
      while (i < out.length && depth > 0) {
        if (out[i] === '{') depth++;
        else if (out[i] === '}') depth--;
        i++;
      }
      while (i < out.length && out[i] !== ';') i++;
      if (i < out.length) i++;
      const removed = out.slice(m.index, i);
      out = out.slice(0, m.index) + blankLines(countLines(removed)) + out.slice(i);
    }
    // Simple typedefs (single-line) — replace with empty, line count
    // already preserved since they are one-liners.
    return out.replace(/typedef[^;{]+;/g, '');
  }

  // C-to-JS pointer-syntax rewrite.
  //   `a->b`   → `a.b`    — pointer-to-struct field access (same as `.` in JS
  //                         since all objects are references).
  //   `&name`  → `name`   — take-address-of identifier collapses to a JS ref,
  //                         but only in argument / parenthesized positions
  //                         (preceded by `(`, `,`, `=`, `{`, or start-of-line
  //                         whitespace). Bitwise `&` between expressions keeps
  //                         its meaning because the preceding context is a
  //                         closing token or identifier, not `(`/`,`/`=`/`{`.
  //                         `{` covers initializer lists like
  //                         `{ &frame_0, 6 }` in MsprAnimFrame arrays.
  function rewriteCPointers(src) {
    let out = src.replace(/->/g, '.');
    out = out.replace(/([(,={]\s*)&(?=[A-Za-z_])/g, '$1');
    return out;
  }

  // Scan top-level statements and function signatures for `TYPE *NAME`
  // pointer declarations so later passes know which identifiers are pointers.
  // Tracks bareword function parameters too (`void f(u8 *p) {…}`).
  function collectPointers(src, userTypes) {
    const typeAlt = [...TYPE_KEYWORDS, ...userTypes].join('|');
    const storageAlt = STORAGE_KEYWORDS.join('|');
    const re = new RegExp(
      `(?:^|[;{(,\\n\\r])\\s*(?:(?:${storageAlt})\\s+)*(?:(?:unsigned|signed)\\s+)?` +
      `(?:${typeAlt})\\s*\\*\\s*([A-Za-z_]\\w*)`,
      'g'
    );
    const set = new Set();
    let m;
    while ((m = re.exec(src)) !== null) set.add(m[1]);
    return set;
  }

  // Scan for declarations of concrete integer types (u8/u16/u32/s8/s16/s32
  // and their `_char`/`_short`/`_long` aliases) so later passes can wrap
  // assignments to the declared width. Pointer declarations are excluded
  // (the `*` between type and name would break the `\s+` separator) so the
  // pointer set and this map stay disjoint.
  function collectIntVarWidths(src, userTypes) {
    // cc900 widths per T900_DENSE_REF.md §3: `int` is 16-bit, matching
    // `short`. This is the #1 surprise for devs coming from 32-bit PC C.
    const intTypes = {
      u8: { bits: 8,  signed: false }, u_char: { bits: 8,  signed: false },
      u16:{ bits: 16, signed: false }, u_short:{ bits: 16, signed: false },
      u32:{ bits: 32, signed: false }, u_long: { bits: 32, signed: false },
      s8: { bits: 8,  signed: true  },
      s16:{ bits: 16, signed: true  },
      s32:{ bits: 32, signed: true  },
      char:{ bits: 8,  signed: true  }, // cc900 default char = signed
      short:{ bits: 16, signed: true },
      int:{ bits: 16, signed: true  }, // cc900: int == short (16-bit)
      long:{ bits: 32, signed: true },
    };
    const typeAlt = Object.keys(intTypes).join('|');
    const storageAlt = STORAGE_KEYWORDS.join('|');
    // `\s+` (not `\s*\*\s*`) excludes pointer declarations; the trailing
    // `(?!\s*\[)` excludes array declarations — `u16 stripes[8] = {...};`
    // shouldn't be wrapped like a scalar since the RHS is an aggregate.
    const re = new RegExp(
      `(?:^|[;{(,\\n\\r])\\s*(?:(?:${storageAlt})\\s+)*(?:(unsigned|signed)\\s+)?` +
      `(${typeAlt})\\b\\s+([A-Za-z_]\\w*)(?!\\s*\\[)`,
      'g'
    );
    const map = new Map();
    let m;
    while ((m = re.exec(src)) !== null) {
      const [, signMod, typeName, varName] = m;
      const base = intTypes[typeName];
      // `unsigned int` / `signed char` etc override signedness.
      const signed = signMod ? (signMod === 'signed') : base.signed;
      map.set(varName, { bits: base.bits, signed });
    }
    return map;
  }

  // Wrap assignments + compound ops to respect the declared integer width,
  // so `u8 x = 300; x++; /* x is now 1 on hw */` behaves the same in the
  // emulator. Runs AFTER rewriteDeclarations so `u8 x = …;` has become
  // `let x = …;`.
  function wrapIntOps(src, intMap) {
    const wrap = (expr, info) => {
      if (info.bits === 32 && !info.signed) return `((${expr}) >>> 0)`;
      if (info.bits === 32 && info.signed)  return `((${expr}) | 0)`;
      const mask = info.bits === 16 ? '0xFFFF' : '0xFF';
      if (!info.signed) return `((${expr}) & ${mask})`;
      const shift = 32 - info.bits;  // 24 for s8, 16 for s16
      return `(((${expr}) << ${shift}) >> ${shift})`;
    };

    // NB: `(?<!\.)` guards every scalar match so `foo.x += 1` isn't wrapped
    // as if `x` itself were the tracked scalar. `->` was rewritten to `.` by
    // rewriteCPointers, so the single-char lookbehind covers both forms.
    for (const [name, info] of intMap) {
      const n = name;
      // `name = EXPR;` — simple assign. `=(?!=)` excludes `==` (compound
      // ops like `+=` are naturally excluded because the char before `=`
      // isn't a space in that case, so the `\s*=` chunk misses them).
      src = src.replace(
        new RegExp(`(?<!\\.)(\\b${n}\\b\\s*)=(?!=)(\\s*)([^;]+);`, 'g'),
        (_m, pre, sp, expr) => `${pre}=${sp}${wrap(expr, info)};`
      );
      // name++ / ++name
      src = src.replace(
        new RegExp(`(?<!\\.)\\b${n}\\+\\+|(?<!\\.)\\+\\+${n}\\b`, 'g'),
        `(${n} = ${wrap(`${n} + 1`, info)})`
      );
      // name-- / --name
      src = src.replace(
        new RegExp(`(?<!\\.)\\b${n}--|(?<!\\.)--${n}\\b`, 'g'),
        `(${n} = ${wrap(`${n} - 1`, info)})`
      );
      // name += EXPR; / name -= EXPR;
      src = src.replace(
        new RegExp(`(?<!\\.)\\b${n}\\s*\\+=\\s*([^;]+);`, 'g'),
        (_m, e) => `${n} = ${wrap(`${n} + (${e})`, info)};`
      );
      src = src.replace(
        new RegExp(`(?<!\\.)\\b${n}\\s*-=\\s*([^;]+);`, 'g'),
        (_m, e) => `${n} = ${wrap(`${n} - (${e})`, info)};`
      );
    }
    return src;
  }

  // Wrap an expression per a numeric cast. Used by rewriteScalarCasts.
  // (void) discards the result; any unknown type drops the cast but keeps
  // the expression intact.
  function castWrap(typeStr, expr) {
    const t = typeStr.replace(/\s+/g, ' ').trim();
    if (t === 'void') return `((${expr}), undefined)`;
    const w = typeWidth(t);
    const signed = /^(s8|s16|s32|char|short|int|long|signed)/.test(t);
    if (w === 32 && !signed) return `((${expr}) >>> 0)`;
    if (w === 32 && signed)  return `((${expr}) | 0)`;
    if (w === 16 && !signed) return `((${expr}) & 0xFFFF)`;
    if (w === 16 && signed)  return `(((${expr}) << 16) >> 16)`;
    if (!signed)             return `((${expr}) & 0xFF)`;
    return `(((${expr}) << 24) >> 24)`;
  }

  // Rewrite `(TYPE) EXPR` scalar casts to wrapping JS expressions. Runs
  // AFTER rewritePointerCasts so `(u8*)X` has already been consumed as a
  // pointer cast — any remaining `(TYPE)X` without a `*` in the cast paren
  // is a value cast.
  function rewriteScalarCasts(src) {
    const typeAlt =
      'u8|u16|u32|s8|s16|s32|u_char|u_short|u_long|' +
      'bool|void|char|short|int|long|' +
      'unsigned\\s+char|unsigned\\s+short|unsigned\\s+long|unsigned\\s+int|' +
      'signed\\s+char|signed\\s+short|signed\\s+long|signed\\s+int';
    const castRe = new RegExp(`\\(\\s*(${typeAlt})\\s*\\)\\s*`);

    let out = '';
    let i = 0;
    while (i < src.length) {
      const rest = src.slice(i);
      const m = rest.match(castRe);
      if (!m) { out += rest; break; }
      out += rest.slice(0, m.index);
      i += m.index;

      const after = rest.slice(m.index + m[0].length);
      let expr = '';
      let consumed = 0;
      if (after[0] === '(') {
        let depth = 0;
        for (let k = 0; k < after.length; k++) {
          const ch = after[k];
          expr += ch;
          if (ch === '(') depth++;
          else if (ch === ')') { depth--; if (depth === 0) { consumed = k + 1; break; } }
        }
        expr = expr.slice(1, -1).trim();
      } else if (after[0] === '-' || after[0] === '+' || after[0] === '~') {
        // Unary op followed by simple rvalue (e.g., `(s16)-1`).
        const mm = after.slice(1).match(/^([A-Za-z_]\w*|0x[0-9A-Fa-f]+|\d+)/);
        if (mm) { expr = after[0] + mm[1]; consumed = 1 + mm[1].length; }
      } else {
        const mm = after.match(/^([A-Za-z_]\w*|0x[0-9A-Fa-f]+|\d+)/);
        if (mm) {
          expr = mm[1]; consumed = mm[1].length;
          // If followed by `(`, this is a function call — swallow the args.
          if (after[consumed] === '(') {
            let depth = 0;
            for (let k = consumed; k < after.length; k++) {
              const ch = after[k];
              expr += ch;
              if (ch === '(') depth++;
              else if (ch === ')') { depth--; if (depth === 0) { consumed = k + 1; break; } }
            }
          }
        }
      }
      if (!expr) {
        out += m[0];
        i += m[0].length;
        continue;
      }
      out += castWrap(m[1], expr);
      i += m[0].length + consumed;
    }
    return out;
  }

  // Rewrite `(TYPE*)EXPR` cast-to-pointer (where EXPR is a parenthesised
  // expression, identifier, or number) into `PTR(EXPR, N)`. Mirrors the
  // expression-scanning logic in markDerefs, except it skips occurrences
  // already consumed by `*(TYPE*)` which markDerefs turned into R/W calls.
  // Runs AFTER resolveDerefs so those are already replaced.
  function rewritePointerCasts(src) {
    const typeRe = /\(\s*(?:volatile\s+)?(u8|u16|u32|u_char|u_short|u_long|s8|s16|s32|unsigned\s+char|unsigned\s+short|unsigned\s+long|char|short|int|long)\s*\*\s*\)\s*/;
    let out = '';
    let i = 0;
    while (i < src.length) {
      const rest = src.slice(i);
      const m = rest.match(typeRe);
      if (!m) { out += rest; break; }
      out += rest.slice(0, m.index);
      i += m.index;
      // Skip if preceded by `*` (already a deref — shouldn't happen post
      // resolveDerefs, but be safe).
      if (out[out.length - 1] === '*') {
        out += rest.slice(m.index, m.index + m[0].length);
        i += m[0].length;
        continue;
      }
      const bytes = typeBytes(m[1]);
      const after = rest.slice(m.index + m[0].length);
      let expr = '';
      let consumed = 0;
      if (after[0] === '(') {
        let depth = 0;
        for (let k = 0; k < after.length; k++) {
          const ch = after[k];
          expr += ch;
          if (ch === '(') depth++;
          else if (ch === ')') { depth--; if (depth === 0) { consumed = k + 1; break; } }
        }
        expr = expr.slice(1, -1).trim();
      } else {
        const mm = after.match(/^([A-Za-z_][A-Za-z0-9_]*|0x[0-9A-Fa-f]+|\d+)/);
        if (mm) { expr = mm[1]; consumed = mm[1].length; }
      }
      if (expr) {
        out += `PTR(${expr}, ${bytes})`;
      } else {
        out += m[0];
      }
      i += m[0].length + consumed;
    }
    return out;
  }

  // Rewrite dereference + arithmetic + comparisons for each known pointer
  // name. Runs AFTER rewriteDeclarations so `u8 *p = …;` is already `let p
  // = …;` (so `*p` in the body unambiguously means "read through p").
  //
  // Comparisons use `p.addr` so `if (p < end)` works address-wise rather than
  // comparing two Proxy objects by reference. Null checks via `p == 0` or
  // `p == NULL` (NULL is injected in the runtime env as 0).
  function rewritePointerOps(src, ptrSet) {
    // Right-hand side of binary ops: accept a simple token — another
    // identifier, hex literal, decimal literal, or `NULL`. Complex
    // expressions are left alone; user can lift them into a temp.
    const RHS = `[A-Za-z_]\\w*|0x[0-9A-Fa-f]+|\\d+`;

    for (const name of ptrSet) {
      const n = name;

      // `*NAME = EXPR;` — lvalue deref assign. Exclude compound ops (+=, etc).
      src = src.replace(
        new RegExp(`\\*\\s*\\b${n}\\b\\s*(?![+\\-*/%&|^]?=)=\\s*([^;]+);`, 'g'),
        `${n}[0] = $1;`
      );

      // Comparisons: `NAME op RHS` and `RHS op NAME`. Rewritten to address
      // compare. `===` / `!==` so numeric equality is exact.
      const cmpMap = {'==': '===', '!=': '!==', '<': '<', '>': '>', '<=': '<=', '>=': '>='};
      for (const [cOp, jsOp] of Object.entries(cmpMap)) {
        const esc = cOp.replace(/[=<>!]/g, c => '\\' + c);
        // name <op> rhs
        src = src.replace(
          new RegExp(`\\b${n}\\s*${esc}\\s*(${RHS})`, 'g'),
          (_m, rhs) => `${n}.addr ${jsOp} ${rhsAsAddr(rhs, ptrSet)}`
        );
        // rhs <op> name  (avoid double-rewriting by requiring not preceded by `.addr `)
        src = src.replace(
          new RegExp(`(${RHS})\\s*${esc}\\s*\\b${n}\\.addr\\b`, 'g'),
          (_m, lhs) => `${rhsAsAddr(lhs, ptrSet)} ${jsOp} ${n}.addr`
        );
      }

      // Expression arithmetic: `NAME + RHS` / `NAME - RHS` → new offset ptr.
      // Only when RHS is a simple token (safe matching) AND not when `+` /
      // `-` are part of `++` / `--` (handled separately below).
      src = src.replace(
        new RegExp(`\\b${n}\\s*\\+\\s*(${RHS})(?!\\+)`, 'g'),
        (_m, rhs) => `PADD(${n}, (${rhs}))`
      );
      src = src.replace(
        new RegExp(`\\b${n}\\s*-\\s*(${RHS})(?!-)`, 'g'),
        (_m, rhs) => `PADD(${n}, -(${rhs}))`
      );

      // `NAME++;` / `++NAME;`
      src = src.replace(new RegExp(`\\b${n}\\+\\+|\\+\\+${n}\\b`, 'g'),
                        `PINC(${n}, 1)`);
      src = src.replace(new RegExp(`\\b${n}--|--${n}\\b`, 'g'),
                        `PINC(${n}, -1)`);
      // `NAME += EXPR;` / `NAME -= EXPR;` — these may contain complex
      // expressions; they're only rewritten when they appear as a full
      // statement so the match doesn't greedily consume adjacent code.
      src = src.replace(new RegExp(`\\b${n}\\s*\\+=\\s*([^;]+);`, 'g'),
                        `PINC(${n}, ($1));`);
      src = src.replace(new RegExp(`\\b${n}\\s*-=\\s*([^;]+);`, 'g'),
                        `PINC(${n}, -($1));`);

      // Any remaining `*NAME` rvalue deref.
      src = src.replace(new RegExp(`\\*\\s*\\b${n}\\b`, 'g'), `${n}[0]`);
    }
    return src;
  }

  // For a RHS in a pointer comparison: if it's another known pointer name,
  // rewrite to `name.addr`; if it's `NULL`, rewrite to 0; otherwise leave.
  function rhsAsAddr(rhs, ptrSet) {
    if (ptrSet.has(rhs)) return `${rhs}.addr`;
    if (rhs === 'NULL') return '0';
    return rhs;
  }

  // Recursive walker for initializer expressions `= { ... };`.
  //   `{.name = val, ...}`   → `{name: val, ...}`  (struct literal)
  //   `{val, val, ...}`      → `[val, val, ...]`   (array literal)
  //   Nested `{}` inside these are recursed the same way, so an array of
  //   structs `{{.x=1},{.x=2}}` becomes `[{x: 1}, {x: 2}]`.
  //
  // Heuristic: after `=`, the next `{` opens an initializer. Block statements
  // (function bodies, if/else/for) are preceded by `)` or a keyword, not `=`,
  // so they aren't touched.
  function rewriteInitializers(src) {
    let out = '';
    let i = 0;
    while (i < src.length) {
      // Match `=` (but not `==`, `<=`, `>=`, `!=`) followed by whitespace then `{`.
      if (src[i] === '=' && src[i - 1] !== '=' && src[i - 1] !== '!' &&
          src[i - 1] !== '<' && src[i - 1] !== '>' && src[i + 1] !== '=') {
        // Skip whitespace to see if `{` follows.
        let j = i + 1;
        while (j < src.length && /\s/.test(src[j])) j++;
        if (src[j] === '{') {
          out += src.slice(i, j + 1); // copy `= ` then let convertInit rewrite `{`
          // Actually let's just copy up to (but excluding) the `{`, then call walker
          out = out.slice(0, out.length - 1); // drop the `{` we just copied
          const [converted, consumed] = convertInit(src, j);
          out += converted;
          i = j + consumed;
          continue;
        }
      }
      out += src[i++];
    }
    return out;
  }

  function convertInit(src, start) {
    // src[start] must be '{'. Returns [converted, consumedCharCount].
    let j = start + 1;
    while (j < src.length && /\s/.test(src[j])) j++;
    const isStruct = src[j] === '.';
    const openCh = isStruct ? '{' : '[';
    const closeCh = isStruct ? '}' : ']';

    let body = '';
    let i = start + 1;
    while (i < src.length && src[i] !== '}') {
      if (src[i] === '{') {
        const [inner, consumed] = convertInit(src, i);
        body += inner;
        i += consumed;
      } else if (isStruct && src[i] === '.') {
        // `.name = ` -> `name: `
        const m = src.slice(i).match(/^\.(\w+)\s*=/);
        if (m) {
          body += m[1] + ': ';
          i += m[0].length;
        } else {
          body += src[i++];
        }
      } else {
        body += src[i++];
      }
    }
    // skip closing `}`
    i++;
    return [openCh + body + closeCh, i - start];
  }

  // Strip C-style array size brackets from declarations: `u16 foo[8] = …`
  // becomes `u16 foo = …`. Handles multi-dimensional declarations like
  // `u8 grid[20][15] = {…}` by consuming every consecutive `[…]` after the
  // identifier. We leave the `=` + initializer to rewriteInitializers,
  // which decides whether the `{…}` on the RHS is a struct or an array.
  function stripArraySizes(src, userTypes) {
    const typeAlt = [...TYPE_KEYWORDS, ...userTypes].join('|');
    const storageAlt = STORAGE_KEYWORDS.join('|');
    const re = new RegExp(
      `((?:(?:${storageAlt})\\s+)*(?:(?:unsigned|signed)\\s+)?(?:${typeAlt})\\b\\s*(?:\\*\\s*)?[A-Za-z_]\\w*)(\\s*\\[[^\\]]*\\])+(?=\\s*=)`,
      'g'
    );
    return src.replace(re, '$1');
  }

  function markDerefs(src) {
    let out = '';
    let i = 0;
    while (i < src.length) {
      const rest = src.slice(i);
      const m = rest.match(PTR_TYPE_RE);
      if (!m) { out += rest; break; }
      out += rest.slice(0, m.index);
      i += m.index;

      const width = typeWidth(m[1]);
      const afterCast = rest.slice(m.index + m[0].length);
      let expr = '';
      let consumed = 0;
      if (afterCast[0] === '(') {
        let depth = 0;
        for (let k = 0; k < afterCast.length; k++) {
          const ch = afterCast[k];
          expr += ch;
          if (ch === '(') depth++;
          else if (ch === ')') { depth--; if (depth === 0) { consumed = k + 1; break; } }
        }
        expr = expr.slice(1, -1).trim();
      } else {
        const mm = afterCast.match(/^([A-Za-z_][A-Za-z0-9_]*|0x[0-9A-Fa-f]+|\d+)/);
        if (mm) { expr = mm[1]; consumed = mm[1].length; }
      }
      out += `__DEREF__{${width}}__{${expr || '0'}}__`;
      i += m[0].length + consumed;
    }
    return out;
  }

  function resolveDerefs(src) {
    let out = src.replace(
      /__DEREF__\{(8|16|32)\}__\{([^}]*)\}__\s*=\s*([^;]+);/g,
      (_m, w, addr, val) => `W${w}(${addr}, ${val});`
    );
    return out.replace(
      /__DEREF__\{(8|16|32)\}__\{([^}]*)\}__/g,
      (_m, w, addr) => `R${w}(${addr})`
    );
  }

  // Strip a parameter list (the stuff between `(` and `)` of a function def)
  // down to a JS-friendly comma-separated list of bare parameter names.
  // `void` (the C convention for "no args") maps to empty.
  function stripParams(params, userTypes = new Set()) {
    const trimmed = params.trim();
    if (!trimmed || /^void$/i.test(trimmed)) return '';
    const allTypes = [...TYPE_KEYWORDS, ...userTypes];
    return trimmed.split(',').map(part => {
      const clean = part
        .replace(/\b(?:const|static|volatile|register|unsigned|signed)\b/g, ' ')
        .replace(new RegExp(`\\b(?:${allTypes.join('|')})\\b`, 'g'), ' ')
        .replace(/[*\[\]]/g, ' ')
        .trim();
      const tokens = clean.split(/\s+/).filter(Boolean);
      return tokens[tokens.length - 1] || '';
    }).filter(Boolean).join(', ');
  }

  // Rewrite top-level C function definitions to JS function declarations.
  // `void main(void) { ... }`      -> `function main() { ... }`
  // `int add(int a, int b) { ... }` -> `function add(a, b) { ... }`
  function rewriteFunctions(src, userTypes) {
    const typeAlt = [...TYPE_KEYWORDS, ...userTypes].join('|');
    const storageAlt = STORAGE_KEYWORDS.join('|');
    const re = new RegExp(
      `(^|[;}\\n\\r])(\\s*)(?:(?:${storageAlt})\\s+)?` +
      `(?:(?:unsigned|signed)\\s+)?(?:${typeAlt})\\b\\s*(?:\\*\\s*)?` +
      `([A-Za-z_]\\w*)\\s*\\(([^)]*)\\)\\s*\\{`,
      'g'
    );
    return src.replace(re, (_m, pre, ws, name, params) => {
      return `${pre}${ws}function ${name}(${stripParams(params, userTypes)}) {`;
    });
  }

  // Rewrite variable declarations (not function defs — those are handled above)
  // by stripping the type keyword and turning it into `let`.
  // The `\b\s*(?:\*\s*)?` tail handles all four C spacings between type and
  // name: `u8 p`, `u8 *p`, `u8 * p`, `u8* p`.
  // Drop `extern TYPE name [array];` variable forward declarations — they
  // exist in headers for linkage and are meaningless once everything is
  // compiled into a single JS scope. Importantly, this must run *before*
  // rewriteRegisters, otherwise an `extern u8 ngpc_pad_held;` gets turned
  // into `extern u8 R8(64257);` by the bareword→R8 rewrite, which is
  // invalid JS. Extern function prototypes like `extern void foo(void);`
  // are handled by stripFunctionPrototypes (the `(...)` disambiguates).
  function stripExternVarDecls(src) {
    const typeKWs = [
      'u8','u16','u32','s8','s16','s32','u_char','u_short','u_long',
      'char','short','int','long','bool','void',
    ];
    const re = new RegExp(
      `^[ \\t]*extern\\s+(?:const\\s+)?(?:volatile\\s+)?` +
      `(?:(?:unsigned|signed)\\s+)?(?:${typeKWs.join('|')})\\b\\s*\\*?\\s*` +
      `[A-Za-z_]\\w*\\s*(?:\\[[^\\]]*\\])?\\s*;`,
      'gm'
    );
    return src.replace(re, '');
  }

  // Drop C function prototypes (forward declarations) *before*
  // rewriteDeclarations — otherwise `void foo(u8 x);` gets mangled into
  // `let foo(u8 x);`, which is a JS syntax error. We only want to keep
  // function *definitions* (signature + `{...}` body); prototypes are
  // meaningless in our JS target because every function is hoisted by
  // `new Function(body)`. Matches `[storage] [unsigned] TYPE [*] NAME (
  // ARGS ) ;` with no `{` between `)` and `;`. Balanced-paren ARGS so
  // comma-separated types don't trip the regex.
  function stripFunctionPrototypes(src, userTypes) {
    const typeAlt = [...TYPE_KEYWORDS, ...userTypes].join('|');
    const storageAlt = STORAGE_KEYWORDS.join('|');
    // Use a walker so nested parens inside argument types (function pointer
    // signatures) don't break the match.
    const headerRe = new RegExp(
      `(^|[;{}\\n\\r])(\\s*)(?:(?:${storageAlt})\\s+)*(?:(?:unsigned|signed)\\s+)?` +
      `(?:${typeAlt})\\b\\s*\\*?\\s*[A-Za-z_]\\w*\\s*\\(`,
      'g'
    );
    let m;
    const kills = [];  // {start, end}
    while ((m = headerRe.exec(src)) !== null) {
      // Walk balanced parens from the `(` we just consumed.
      const openIdx = m.index + m[0].length - 1;
      let depth = 1, i = openIdx + 1;
      while (i < src.length && depth > 0) {
        const c = src[i];
        if      (c === '(') depth++;
        else if (c === ')') depth--;
        i++;
      }
      // Skip whitespace after `)`; a `{` means it's a definition, keep it.
      let j = i;
      while (j < src.length && /\s/.test(src[j])) j++;
      if (src[j] === '{') continue;               // definition, leave alone
      if (src[j] === ';') {
        // Keep the leading pre-char (the `[;{}\n\r]` anchor) in the output.
        const keepPre = m[1].length;
        kills.push({ start: m.index + keepPre, end: j + 1 });
        // Reset the regex pointer past the swallowed region.
        headerRe.lastIndex = j + 1;
      }
    }
    kills.sort((a, b) => b.start - a.start);
    for (const k of kills) {
      src = src.slice(0, k.start) + src.slice(k.end);
    }
    return src;
  }

  function rewriteDeclarations(src, userTypes) {
    const typeAlt = [...TYPE_KEYWORDS, ...userTypes].join('|');
    const storageAlt = STORAGE_KEYWORDS.join('|');
    const re = new RegExp(
      `(^|[;{(\\n\\r])(\\s*)(?:(${storageAlt})\\s+)?` +
      `(?:(?:unsigned|signed)\\s+)?(?:${typeAlt})\\b\\s*(?:\\*\\s*)?(?=[A-Za-z_])`,
      'g'
    );
    return src.replace(re, (_m, pre, ws, storage) => {
      const kw = storage === 'const' ? 'const' : 'let';
      return `${pre}${ws}${kw} `;
    });
  }

  // JS `const` requires an initializer; C `const TYPE *p;` transpiles to
  // `const p;` which is a SyntaxError. Downgrade bare const-without-init to
  // `let` so forward declarations of pointers / scalars compile.
  function downgradeConstNoInit(src) {
    return src.replace(/\bconst\s+([A-Za-z_]\w*)\s*;/g, 'let $1;');
  }

  // Pre-pass: `MsprAnimator a;` → `MsprAnimator a = {};` so the later
  // rewriteDeclarations turns it into `let a = {};` (usable for field writes).
  // Only applies to struct types listed in STRUCT_DEFAULT_INIT_TYPES and only
  // to bare declarations (no `=`, no `[` array, no `*` pointer).
  function defaultInitStructs(src) {
    if (STRUCT_DEFAULT_INIT_TYPES.length === 0) return src;
    const typeAlt = STRUCT_DEFAULT_INIT_TYPES.join('|');
    const re = new RegExp(
      `\\b(${typeAlt})\\s+([A-Za-z_]\\w*)\\s*;`,
      'g'
    );
    return src.replace(re, (_m, type, name) => `${type} ${name} = {};`);
  }

  // Turn `ngpc_vsync();` into `yield;` so main() can be driven at 60 Hz by
  // the host runtime. The NgpCraft template treats this call as the standard
  // frame-sync point, which maps cleanly to a generator suspension point.
  //
  // `ngpc_sleep(n);` is expanded inline to a bounded yield loop — the real
  // implementation in src/core/ngpc_timing.c:61-73 calls ngpc_vsync() `n`
  // times, which we mirror exactly (minus the cpu_speed tweak).
  function rewriteVsync(src) {
    src = src.replace(/\bngpc_vsync\s*\(\s*\)\s*;/g, 'yield;');
    src = src.replace(
      /\bngpc_sleep\s*\(([^;)]+)\)\s*;/g,
      (_m, n) => `{ for (let __sleep_i=0; __sleep_i < (${n}); __sleep_i++) yield; }`
    );
    return src;
  }

  // Expand the NgpCraft debug macros from ngpc_log.h / ngpc_assert.h. In the
  // real template these are #define'd and may be compiled to no-ops in
  // release builds; we always expand to the function-call form since we
  // don't have a real preprocessor. __FILE__/__LINE__ aren't tracked, so
  // assert failures report the transpiled location only.
  function rewriteDebugMacros(src) {
    src = src.replace(
      /\bNGPC_ASSERT\s*\(([^;]+)\)\s*;/g,
      `if (!($1)) ngpc_assert_fail("(source)", 0);`
    );
    src = src.replace(
      /\bNGPC_LOG_HEX\s*\(([^,]+),\s*([^)]+)\)\s*;/g,
      `ngpc_log_hex($1, $2);`
    );
    src = src.replace(
      /\bNGPC_LOG_STR\s*\(([^,]+),\s*([^)]+)\)\s*;/g,
      `ngpc_log_str($1, $2);`
    );
    return src;
  }

  // If the source contains `ngpc_vsync`, main() becomes a generator function so
  // the host can step it frame-by-frame via `.next()`. Helper functions with
  // ngpc_vsync aren't supported in V1 — yield in a non-generator is a syntax
  // error, which makes the limitation visible rather than silent.
  function makeMainGenerator(src) {
    return src.replace(/\bfunction\s+main\s*\(\s*\)\s*\{/, 'function* main() {');
  }

  // Rewrite bareword register / extern accesses to memory-bus helpers.
  //
  // We mark every match first, then handle write forms (including compound
  // assignments like `|=`, `&=`, `^=`, etc.) before falling through to reads.
  // Same two-pass pattern as markDerefs/resolveDerefs — keeps the rewrite
  // independent of surrounding context (arithmetic, conditions, arguments).
  function rewriteRegisters(src) {
    // Sort names by length descending so e.g. `HW_SCR1_OFS_X` is matched
    // before `HW_SCR1` (would be overly greedy without \b, but this also
    // guards against any future shorter/longer overlap).
    const names = Object.keys(REGISTERS).sort((a, b) => b.length - a.length);
    for (const name of names) {
      const { addr, width } = REGISTERS[name];
      const re = new RegExp(`\\b${name}\\b`, 'g');
      src = src.replace(re, `__REG__{${width}}__{${addr}}__`);
    }

    // Write form: `marker = VAL;` or `marker OP= VAL;` where OP is one of
    // the C compound-assignment operators.
    src = src.replace(
      /__REG__\{(8|16|32)\}__\{(\d+)\}__\s*(\+|-|\*|\/|%|&|\||\^|<<|>>)?=\s*([^;]+);/g,
      (_m, w, addr, op, val) => {
        if (op) {
          return `W${w}(${addr}, (R${w}(${addr}) ${op} (${val})) & ${w === '8' ? '0xFF' : w === '16' ? '0xFFFF' : '0xFFFFFFFF'});`;
        }
        return `W${w}(${addr}, ${val});`;
      }
    );

    // Read form: any remaining marker.
    src = src.replace(
      /__REG__\{(8|16|32)\}__\{(\d+)\}__/g,
      (_m, w, addr) => `R${w}(${addr})`
    );
    return src;
  }

  function compile(src, opts = {}) {
    // ngpc_sleep internally calls vsync, so a main() that uses sleep is
    // also frame-driven — expand both the same way.
    let out = src;
    // Hardware-fidelity lints accumulate here. `compile()` throws a single
    // HwFidelityError carrying every collected error at the end, so the user
    // sees all violations in one shot instead of fix-one-rerun-repeat. No
    // rule is active yet — see 0-4 / 0-6 / 0-7 for the first populating calls.
    const hwErrors = [];
    out = stripComments(out);
    // Preprocessor stages that depend on multi-file input: resolve #include
    // against the caller-provided resolver, then evaluate #if/#ifdef branches
    // so feature-flagged code disappears before parsing. Both passes preserve
    // line count as much as possible so error contexts stay meaningful.
    if (opts.includeResolver) {
      out = resolveIncludes(out, opts.includeResolver);
      // Second pass: strip comments that arrived via included headers so
      // later numeric/decl rewrites don't latch onto `= 0` or `shift = 1`
      // fragments hiding inside /* explanation text */ blocks.
      out = stripComments(out);
    }
    out = evalConditionals(out);
    // HW-3b: `for (TYPE name = 0; …)` is C99. Runs on post-preproc source so
    // disabled #if branches don't trigger false positives.
    lintForDecl(out, hwErrors);
    // HW-3c: cc900 crash on `s8 != s8 || s8 != s8` chains. Same context as HW-3b.
    lintS8OrChain(out, hwErrors);
    // HW-3d intentionally not called — see comment above its (now-stub) docstring.
    // HW-4: large stack array — would overflow tiny NGPC stack on real hw.
    lintLargeStackArray(out, hwErrors);
    const hasVsync = /\bngpc_vsync\b/.test(out) || /\bngpc_sleep\s*\(/.test(out);
    const userTypes = new Set([...TEMPLATE_TYPES, ...extractUserTypes(out)]);
    out = hoistFunctionStatics(out, userTypes);
    out = stripExternVarDecls(out);
    out = stripInlineAsm(out);
    out = expandUserMacros(out);
    out = stripPreprocessor(out);
    // Fast path: hoist large numeric asset bodies out before the rest of the
    // rewrite pipeline walks them. The skeleton stays in place so the HW
    // lints + the normal passes see a valid decl; the body is reinjected
    // just before `return out`.
    const { src: _fast, extracted: _arrays } = extractLargeArrays(out);
    out = _fast;
    // HW-1: near/far pointer mismatch. Needs NGP_FAR still visible in source,
    // so runs BEFORE stripCC900Qualifiers. Only effective if NGP_FAR is not
    // already stripped by macro expansion — see 0-5 for the preservation fix.
    lintNgpFar(out, hwErrors);
    // HW-2: `volatile` missing on ISR-shared globals. Needs `__interrupt`
    // keyword still visible, so also runs BEFORE stripCC900Qualifiers.
    lintIsrVolatile(out, hwErrors);
    // If any lint populated `hwErrors` earlier in this compile, bail out now
    // with a single structured exception. Caught by `run()` → formatted message
    // shows in the editor log pane under the `err` filter.
    if (hwErrors.length > 0) throw new HwFidelityError(hwErrors);
    out = stripCC900Qualifiers(out);  // __far/__near/__cdecl/__interrupt/NGP_FAR
    out = rewriteEnums(out);          // enum { A, B=5, C } → const lines
    out = rewriteSizeof(out);         // sizeof(u16) → 2, sizeof(int) → 2 (cc900)
    out = stripTypedefs(out);
    out = splitMultiVarDecls(out, userTypes);
    out = stripIntLiteralSuffixes(out);
    out = rewriteCharLiterals(out);
    out = rewriteDebugMacros(out);
    if (hasVsync) out = rewriteVsync(out);
    out = markDerefs(out);
    out = resolveDerefs(out);
    out = rewriteCPointers(out);          // -> becomes . , &ident strip
    // Collect pointer + integer-var types BEFORE rewriteDeclarations strips
    // the type keywords. The two sets are disjoint (collectIntVarWidths uses
    // \s+ between type and name, collectPointers requires `*`).
    const ptrSet = collectPointers(out, userTypes);
    const intMap = collectIntVarWidths(out, userTypes);
    out = rewritePointerCasts(out);       // (u8*)X -> PTR(X, 1)
    out = rewriteScalarCasts(out);        // (u8)X / (s16)X / (void)X
    out = stripArraySizes(out, userTypes);
    out = rewriteInitializers(out);       // struct {.f=v} / array {v,v}
    out = defaultInitStructs(out);        // MsprAnimator a; -> a = {}
                                           // (after rewriteInitializers so the
                                           // inserted `{}` isn't converted to
                                           // `[]` as a positional array)
    out = rewriteFunctions(out, userTypes);
    // Strip prototypes before decl rewrite so `void foo(...)` prototypes
    // don't get munged into `let foo(...)` (invalid JS). Runs after
    // rewriteFunctions so definitions (with `{}` bodies) are already
    // canonicalised to `function` form and won't match.
    out = stripFunctionPrototypes(out, userTypes);
    out = rewriteDeclarations(out, userTypes);
    out = downgradeConstNoInit(out);      // `const p;` -> `let p;`
    out = rewritePointerOps(out, ptrSet); // *p / p++ / p += N, after decls
    out = wrapIntOps(out, intMap);        // u8/u16/etc overflow wrapping
    out = rewriteRegisters(out);
    if (hasVsync) out = makeMainGenerator(out);
    out = reinjectArrays(out, _arrays);  // swap placeholders for bulk data
    return out;
  }

  function buildEnv(onLog) {
    const unwrap = (a) => (a && typeof a === 'object' && 'addr' in a) ? a.addr : a;
    const env = {
      // Memory bus: accept either a number or a PTR object so codegen from
      // pointer-cast rewrites interoperates with register-macro rewrites.
      W8:  (a, v) => NGPC_Memory.write8(unwrap(a), v),
      W16: (a, v) => NGPC_Memory.write16(unwrap(a), v),
      W32: (a, v) => NGPC_Memory.write32(unwrap(a), v),
      R8:  (a)    => NGPC_Memory.read8(unwrap(a)),
      R16: (a)    => NGPC_Memory.read16(unwrap(a)),
      R32: (a)    => NGPC_Memory.read32(unwrap(a)),
      print: (...args) => onLog && onLog(args.join(' ')),
    };
    // Spread NGPC API constants + RGB macro.
    for (const k of Object.keys(NGPC_API)) env[k] = NGPC_API[k];
    // Spread NGPC runtime (high-level function implementations).
    if (typeof NGPC_Runtime !== 'undefined') {
      for (const k of Object.keys(NGPC_Runtime)) env[k] = NGPC_Runtime[k];
      // Create fresh system pointer objects for this run so any mutation
      // (accidental `HW_PAL_BG++`) is scoped to this execution.
      if (typeof NGPC_Runtime.makeSystemPointers === 'function') {
        Object.assign(env, NGPC_Runtime.makeSystemPointers());
      }
    }
    return env;
  }

  // Pick the source line corresponding to a 1-based line number, used to
  // echo context back in error messages. Also shows N lines of padding on
  // each side so the student sees where in the file they are.
  function sourceContext(src, line, context = 1) {
    const lines = src.split('\n');
    const start = Math.max(0, line - 1 - context);
    const end   = Math.min(lines.length, line + context);
    const rows = [];
    for (let i = start; i < end; i++) {
      const n = i + 1;
      const marker = n === line ? '>>>' : '   ';
      rows.push(`${marker} ${String(n).padStart(3)} | ${lines[i]}`);
    }
    return rows.join('\n');
  }

  function run(src, { onLog, entry = 'main', includeResolver } = {}) {
    const jsSrc = compile(src, { includeResolver });
    const env = buildEnv(onLog);
    const paramNames = Object.keys(env);
    const paramVals  = Object.values(env);

    const finalSrc =
      `"use strict";\n${jsSrc}\n;` +
      `return (typeof ${entry} === 'function') ? ${entry}() : undefined;`;

    let fn;
    try {
      fn = new Function(...paramNames, finalSrc);
    } catch (e) {
      // SyntaxError line number from the Function body. new Function() in
      // Firefox reports lines relative to the body (1-based); Chromium
      // reports relative to a synthetic wrapper. Try both interpretations
      // and fall back to a full dump if neither lands on a real line.
      const lineMatch = (e.stack || e.message || '').match(/:(\d+):\d+/);
      const reportedLine = lineMatch ? parseInt(lineMatch[1], 10) : null;
      const jsLines = jsSrc.split('\n');
      // Candidate offsets: [0] raw, [-1] for the "use strict" prefix.
      const candidates = reportedLine !== null
        ? [reportedLine, reportedLine - 1, reportedLine - 2]
        : [];
      let pickedLine = null;
      for (const c of candidates) {
        if (c >= 1 && c <= jsLines.length) { pickedLine = c; break; }
      }
      let ctx = '';
      ctx += `\n\nJS engine reported line: ${reportedLine ?? '(none)'}  ` +
             `(transpiled JS has ${jsLines.length} lines).`;
      if (pickedLine !== null) {
        ctx += '\n\nSource context (original C):\n' +
               sourceContext(src, pickedLine, 3);
        ctx += '\n\nTranspiled JS near the error:\n' +
               sourceContext(jsSrc, pickedLine, 3);
      } else {
        // Reported line lies outside the transpiled JS (engine reported a
        // line inside interpreter.js itself) — dump the full transpile with
        // line numbers so the user can paste it back.
        ctx += '\n\nFull transpiled JS (for debugging):\n';
        ctx += jsLines.map((l, i) =>
          `${String(i + 1).padStart(4)} | ${l}`
        ).join('\n');
      }
      throw new Error(`Compile error: ${e.message}${ctx}`);
    }
    try {
      return fn(...paramVals);
    } catch (e) {
      if (e.message === 'NGPC_SHUTDOWN') throw e;
      const lineMatch = (e.stack || '').match(/:(\d+):\d+/);
      if (lineMatch) {
        const srcLine = parseInt(lineMatch[1], 10) - 1 - 1; // -1 for use-strict prefix
        if (srcLine > 0 && srcLine < src.split('\n').length + 5) {
          e.message += '\n\nSource context (approximate):\n' +
                       sourceContext(src, srcLine);
        }
      }
      throw e;
    }
  }

  // First-class headless entry point. Wraps run() so external callers
  // (Workers, tests, MCP server, CI pipelines) don't have to reimplement the
  // generator-driving + state-capture dance themselves.
  //
  // - Resets memory before each call so successive runs don't bleed state.
  // - If main() uses ngpc_vsync(), iterates the generator up to `frames` ticks.
  // - Returns a self-contained snapshot:
  //     {
  //       ok, kind, errors?, message?,
  //       framesAdvanced, mainCompleted,
  //       logs,
  //       state: { bgColor, scr1OfsX/Y, scr2OfsX/Y },
  //       framebuffer: { width, height, rgba: Uint8ClampedArray | null }
  //     }
  // `framebuffer.rgba` is populated only if vdp is available + captureFramebuffer != false.
  function runFrames(code, opts = {}) {
    const { frames = 60, captureFramebuffer = true, capturePsgEvents = false,
            includeResolver, entry = 'main' } = opts;
    const logs = [];
    const Memory = (typeof NGPC_Memory !== 'undefined') ? NGPC_Memory : null;
    const VDP    = (typeof NGPC_VDP    !== 'undefined') ? NGPC_VDP    : null;
    const PSG    = (typeof NGPC_PSG    !== 'undefined') ? NGPC_PSG    : null;
    if (Memory && typeof Memory.reset === 'function') Memory.reset();
    if (PSG && capturePsgEvents && typeof PSG.getEvents === 'function') {
      PSG.getEvents(true);  // drain any pre-existing events from prior runs
    }
    let result;
    try {
      result = run(code, { onLog: (m) => logs.push(m), entry, includeResolver });
    } catch (err) {
      if (err.name === 'HwFidelityError') {
        return { ok: false, kind: 'hardware_fidelity', errors: err.hwErrors,
                 formatted: err.message, logs };
      }
      return { ok: false, kind: 'transpile_or_runtime_error', message: err.message, logs };
    }
    let framesAdvanced = 0;
    let mainCompleted = true;
    if (result && typeof result === 'object' && typeof result.next === 'function') {
      mainCompleted = false;
      for (let i = 0; i < frames; i++) {
        let step;
        try { step = result.next(); }
        catch (err) {
          return { ok: false, kind: 'runtime_error_in_frame',
                   frame: i, message: err.message, logs, framesAdvanced };
        }
        framesAdvanced++;
        if (step.done) { mainCompleted = true; break; }
      }
    }
    const state = {};
    if (Memory && typeof Memory.read16 === 'function') {
      try {
        state.bgColor   = Memory.read16(0x83E0);
        state.scr1OfsX  = Memory.read16(0x8030);
        state.scr1OfsY  = Memory.read16(0x8032);
        state.scr2OfsX  = Memory.read16(0x8034);
        state.scr2OfsY  = Memory.read16(0x8036);
      } catch {}
    }
    let framebuffer = null;
    if (captureFramebuffer && VDP && typeof VDP.renderToPixels === 'function') {
      framebuffer = { width: VDP.W, height: VDP.H, rgba: VDP.renderToPixels() };
    }
    let psgEvents = null;
    if (capturePsgEvents && PSG && typeof PSG.getEvents === 'function') {
      psgEvents = PSG.getEvents(true);
    }
    return { ok: true, framesAdvanced, mainCompleted, logs, state, framebuffer, psgEvents };
  }

  return { run, compile, runFrames, REGISTERS };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_Interp = NGPC_Interp;
