# Quirks v0

Purpose:
- centralize the first hardware-quirk matches used by the current reference
  executor and decode-only helpers
- keep silicon-broken stop decisions explicit and reviewable
- avoid scattering hardware-policy guesses across the execution helpers

Current source references:
- `../../NgpCraft_toolchain/USER_MANUAL_EN.md`
- `../../NgpCraft_toolchain/DECISIONS.md`
- `../../NgpCraft_toolchain/DEVLOG.md`
- `../../NgpCraft_toolchain/DISASM_CROSSCHECK.md`

Current implementation:
- `core/quirks.py`
- `core/quirks_db.json`

Current model:
- quirks are matched from an already decoded instruction
- the current executor only consumes the subset needed for explicit
  `silicon-broken` stops
- matched quirk metadata is now exposed by both execution-facing and decode-only
  CLI / JSON helpers
- the current local knowledge base is now stored as a first versioned external
  quirk database file consumed by `core/quirks.py`
- the current database version is `2026-04-22.v3`
- matched quirk payloads now also carry that database version
- each database entry carries a non-empty `sources` list, and matched-quirk
  payloads surface that attribution in both JSON and text output

Database entry schema:
- `quirk_id` (string, required)
- `category` (string, required)
- `confidence` (string, required)
- `summary` (string, required)
- `note` (string, required)
- `execution_stop_status` (string, optional)
- `sources` (non-empty list, required, `v2`+)
  - each entry is an object with:
    - `document` (string, required) — relative path or canonical name
    - `section` (string, optional) — anchor, heading, or section id
    - `quote` (string, optional) — short excerpt anchoring the rule
- `matcher` (object, required)
  - `kind` (string, required)
  - remaining fields depend on the matcher kind

Matched-quirk payload schema:
- `database_version`
- `quirk_id`
- `category`
- `confidence`
- `summary`
- `note`
- `sources` (list of `{document, section, quote}` objects; `section` and
  `quote` may be `null`)

Current matched silicon-broken cases:
- `cpu.d0_d7_non_immediate`
  - matches `D0..D7` word-prefix forms except the currently documented
    immediate-safe forms
  - current immediate-safe exception set:
    - `ld r, imm`
    - `multu/muls r, imm`
    - `ld r, #3`
    - ALU-immediate `add/adc/sub/sbc/and/xor/or/cp r, imm`
- `cpu.d8_df_register_to_register`
  - matches `D8..DF` working-bank prefix forms with an `r+r` ALU sub-op
  - safe set covers:
    - any sub-op in `0x00..0x7F` (inc/dec/scc, non-r+r loads, extz/exts,
      multu/muls imm, ldc, cpl/neg)
    - `A8..AF` compact `ld r, #3` (compact immediate load)
    - `C8..CF` ALU-immediate (add/adc/sub/sbc/and/xor/or/cp r, imm)
    - `D8..DF` `cp r, imm3` (immediate compare)
    - `E8..EF` shift/rotate with immediate count
    - `F0..F7` `CP r+r` (documented USER_MANUAL exception)
    - `F8..FF` shift-by-A — deferred to a future dedicated quirk for the
      count=0 edge case (see `MEMORY.md` Bug J11 silicon)
- `cpu.link_xiy_large_frame`
  - matches `LINK XIY, N` when the existing decode warning already marks the
    large-frame form as broken

Important:
- this file records the current project choice when local sources are partially
  conflicting
- the goal is to keep the reference executor conservative without silently
  over-blocking documented-safe immediate forms

Not implemented yet:
- richer matcher kinds beyond the two currently validated local rules

## `D8..DF` / `E8..EF` policy — reconciliation notes (2026-04-22, implemented v3)

The previous `v1` snapshot flagged the `D8..DF` / `E8..EF` policy as
"contradictory local sources, revisit later". The v2 source-attribution work
made the sources reviewable at the data-file level, and v3 landed the
reconciled rule. This section captures the reasoning so future quirk
additions do not re-read three docs from scratch.

Sources consulted:
- `../NgpCraft_toolchain/USER_MANUAL_EN.md`
  - §12.1 Bug #1 (lines 1170-1189) states crisply:
    > "Register-to-register 16-bit (D0..D7) and 32-bit (D8..DF) operations
    > with sub-opcodes 0x80..0xFF hang the CPU on real NGPC hardware.
    > Rule: byte r+r (C8..CF) and all immediates are safe. Word/long r+r =
    > broken. Exception: cp XWA, XHL (D8 F3) is safe as CP has no sub-op
    > ≥ 0x80."
  - explicit table entries:
    - `ld XWA, XDE` (`D8 82`) = BROKEN
    - `cp XWA, XHL` (`D8 F3`) = OK (CP exception)
    - `add WA, 1` (`D0 C8 01 00`) = OK (immediate)
- `../NgpCraft_toolchain/DISASM_CROSSCHECK.md`
  - §2 (lines 66-74) says "D8..DF (bank working courant) : OK" — but the only
    concrete hardware evidence is `d8 61` (`inc 0x1, WA`, sub-op `0x61 < 0x80`),
    which sits inside the USER_MANUAL safe zone anyway
  - §3 (lines 78-91) confirms `E8..EF` is not broken, with direct evidence
    on `inc` forms (sub-ops `0x61..0x64`, all `< 0x80`)
- `../NgpCraft_toolchain/T900_DENSE_REF.md`
  - aligned with USER_MANUAL on the broken `r+r` family

Reconciled reading:
- USER_MANUAL and DISASM_CROSSCHECK agree that `D8..DF` works for the
  `< 0x80` sub-op space (loads with immediate, inc/dec with embedded imm3,
  etc.) and disagree only on whether `D8..DF` `r+r` ALU ops (sub-op
  `0x80..0xEF` or `0xF8..0xFF`) also work.
- DISASM_CROSSCHECK's "OK" claim is not in tension with USER_MANUAL because
  DISASM_CROSSCHECK never tested an `r+r` sub-op on `D8..DF`; its evidence is
  limited to `d8 61` (`inc`).
- `E8..EF` is out of scope for the broken-family rule: DISASM_CROSSCHECK has
  direct hardware evidence confirming it works (inc forms), and USER_MANUAL
  §12.1 does not list it in the broken table.

Landed rule shape (`core/quirks_db.json` v3):
- `quirk_id`: `cpu.d8_df_register_to_register`
- `category`: `broken-opcode-family`
- `confidence`: `documented`
- `matcher.kind`: `prefixed_range_non_immediate`
- `matcher.prefix_start`: `0xD8`, `matcher.prefix_end`: `0xDF`
- `matcher.safe_second_values`: `[]`
- `matcher.safe_second_ranges`:
  - `[0x00, 0x7F]` — non-r+r families (inc/dec/scc, extz/exts, multu/muls imm,
    ldc, cpl/neg, non-r+r `ld` indexed forms)
  - `[0xA8, 0xAF]` — compact `ld r, #3`
  - `[0xC8, 0xCF]` — ALU-immediate family
  - `[0xD8, 0xDF]` — `cp r, imm3`
  - `[0xE8, 0xEF]` — shift/rotate with imm count
  - `[0xF0, 0xFF]` — `CP r+r` (documented USER_MANUAL exception) and
    shift-by-A (`F8..FF`) deferred to a future dedicated quirk for the
    count=0 edge case
- primary source: `USER_MANUAL_EN.md §12.1` with an explicit
  `ld XWA, XDE` / `D8 82` broken-example quote and a `cp XWA, XHL` / `D8 F3`
  CP-exception quote.

Smoke impact actually observed after landing v3:
- previous frontier on StarGunner smoke ROM was step 27 556, PC `0x0020CD4D`
  (`D7 FA`, the `D0..D7` rule)
- new frontier is step 25 072, PC `0x0020D180` (`D8 89`, `ld XBC, XWA`)
- delta: -2 484 honest steps
- the regression is policy-correct per `HARDWARE_COMPAT_POLICY.md` §4.1
  (reference mode must not silently execute documented-broken forms); it is
  not a bug, it is the whole point of the quirk database

Open follow-ups:
- add a dedicated quirk for the `F8..FF` shift-by-A `count=0` edge case once
  the local reference ROM and a tight test case exist (see
  `MEMORY.md` Bug J11 silicon)
- `E8..EF` remains unclaimed: DISASM_CROSSCHECK §3 has direct hardware
  evidence it is not broken (inc forms); no rule needed
