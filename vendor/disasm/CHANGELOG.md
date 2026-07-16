# Changelog — ngpc_disasm

> **Note:** `vendor/*` is a dist mirror. Durable edits belong upstream — this
> entry records a correction applied to the mirror; propagate it to the source repo.

## 2026-07-08

### Fixed

- **CB broken-opcode flag is now sub-op-specific — byte mul/div no longer
  mis-flagged.** The decoder previously flagged **any** `0xCB` prefix as
  `!BROKEN CB family`. Real-hardware test `hw_test_bytediv` (2026-07-08) proved
  the C-source byte **mul/muls/div/divs** reg-reg pocket (`CB` sub-op
  `0x40..0x5F`, e.g. `CB 51 = div A, C`: WA=0x1F64 / C=0x64 → WA=0x2450,
  quotient 0x50, remainder 0x24) executes correctly and is **not** silicon-broken.
  `decode_zz_r` now suppresses the warning when `b == 0xCB and 0x40 <= sub-op <=
  0x5F`; the arith/logic ALU sub-ops (`add A, C = CB 81`, etc.) remain flagged.
  This parallels the WORD mul/div clearing (`D8..DF`, hw_test_muldiv 2026-07-06).
  `_zz_regs` docstring/comments, `MANUAL.md`, `DEVLOG.md`, and bug-DB entry
  `CB_FAMILY_BROKEN` (`bugs_silicon.json`) updated to the nuance.
  The `add A, C` (`CB 81`) broken flag is unchanged.

## 2026-04-20

Audit pass focused on closing the gap between what the README/MANUAL claim and
what the code actually does, plus making the CLI behave correctly under
unhappy-path conditions (missing file, bad hex, reversed range).

### Fixed

- **CB family broken-opcode flag now emitted.** The README has long claimed
  that `CB xx` (byte ALU using the C register as source) hangs on NGPC silicon
  and should be flagged `; !BROKEN`, but the decoder was silently producing
  `add A, C` (and friends) with no warning. `_zz_regs(b)` now returns
  `is_broken=True` when `b == 0xCB`; `decode_zz_r` emits
  `!BROKEN CB family (byte ALU C-source) — silicon hang, use CF (L-source) instead`.
  Other byte-ALU prefixes (C8 W-source, C9 A-source, CA B-source, CC..CF) are
  confirmed safe (CC900 production code uses them).
  Validated against `bisect_j8z13 (2026-03-22)`.
- **`adc W, B` (`CA 90`) broken-instruction detection.** TLCS-900 `adc W, B`
  silently produces wrong high-byte results when `W > 0` on NGPC. The decoder
  now flags this specific `(prefix, sub-op)` pair with
  `!BROKEN adc W,B fails when W>0 (silicon) — keep W==0 or restructure`.
  Static disassembly cannot know the runtime value of `W`, so the warning is
  unconditional — left to the reader to verify intent.
- **Friendly error on missing / unreadable ROM file.** `open(args.rom, 'rb')`
  used to dump a Python traceback on `FileNotFoundError` / `PermissionError`.
  Now: clean stderr message (`[ngpc_disasm] error: ROM file not found: …`)
  and `sys.exit(1)`.
- **Friendly error on bad hex CLI argument.** `int(args.start, 16)` on
  `--start xyz` used to throw `ValueError`. Now routed through a `_parse_hex`
  helper that prints a clear message and exits with code 2.
- **Friendly error on `--start > --end`.** Used to silently produce empty
  output. Now: clear stderr message with both values in hex, exits with
  code 2.
- **Friendly error on `--output` write failure.** Wrapped in `try/except OSError`
  with stderr message + `sys.exit(1)`.
- **Explicit `sys.exit(0)` on success.** `main()` previously fell off the end,
  relying on Python's implicit-zero behaviour. Now the success path is explicit
  so script-level wrappers (CI, MCP) get a reliable signal.

### Documentation

- `_zz_regs` docstring updated to list the two confirmed silicon bugs (CB and
  D0..D7) with fix recipes inline. README claims now match implementation.
- `--base` / `--start` / `--end` help text now mentions "with or without 0x
  prefix" — `int(s, 16)` already handled both, but it was undocumented.

### Notes

- The MCP server (`@ngpcraft/mcp`) consumes this script through
  `vendor/disasm/ngpc_disasm.py`.
- Bug-DB entry `CB_FAMILY_BROKEN` in `bugs_silicon.json` was tightened: the
  scope is "0xCB prefix specifically" (validated `CB 81`); the broader "C-prefix
  family" wording was removed because C8/C9/CA/CC..CF are confirmed safe.

### Skipped (audited but no fix applied)

- **Inline HW-register annotations inside operand strings.** Style remains
  `ld XIY, 0x6FCC  ; HW_VBL_ISR_PTR`. Standard assemblers (including
  `t900as.py`, `asm900.exe`) honour `;` line-comments mid-line, so round-trip
  is preserved in practice. Restructuring output emission to separate
  comments was judged disproportionate.
- **Conditional ALU forms (`adc cc, reg` etc.).** Audit raised this as a
  potential gap, but no failing test case was provided and the Toshiba
  TMP95C061BFG datasheet treatment is ambiguous. Left for a future pass with
  a concrete reproducer.
- **Strict header validation** — homebrew without an SNK header continues to
  fall back silently to `(no header)`. Adding a bounds check on entry-point
  was rejected as a false-positive risk for hand-rolled ROMs.

---

Earlier history not tracked in this file.
