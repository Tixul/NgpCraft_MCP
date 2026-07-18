# NgpCraft Emulator - Feature Matrix

Ce document definit le niveau de fonctionnalite attendu.
Le but n'est pas d'accumuler des features "presentes dans l'UI".
Le but est d'avoir un emulateur/debugger NGPC full-featured, mais surtout fiable et utile.

## 1. Regle generale

Une feature marquee comme "supportee" doit:
- marcher vraiment sur NGPC
- etre testable sur au moins une ROM reelle du corpus
- donner une information exploitable
- ne pas etre reservee a un seul frontend si elle releve du coeur
- respecter les comportements defectueux connus du hardware quand ils sont documentes
- respecter aussi les comportements de slowdown et de cadence quand ils sont connus

Statuts autorises:
- `planned`
- `partial`
- `working`
- `release-ready`

`working` signifie:
- utile en pratique
- pas juste branchee partiellement

`release-ready` signifie:
- stable
- testee
- documentee
- exploitable a la fois par utilisateur avance et workflow NgpCraft

Snapshot prototype Python au 2026-05-20 (post pass 19, M2 Phase 0.5 visual lens) :
- **389 tests verts, 0 skipped** (passes 3 a 19 toutes ship dans la meme session, +130 tests cumules vs baseline 259+4 skipped)
- executor: 25 072 honest steps on StarGunner smoke (frontier `D8 89 ld XBC, XWA` a `0x0020D180`, silicon-broken — stop honnete, pas un bug emulateur)
- format envelopes locked (rejet implicit upgrade) :
  - savestate v2 (`2026-05-20.v2`) — ajoute `nf` flag, `iff_level`, `rfp`
  - event-log v2 (`2026-05-20.v2`) — ajoute `memory_reads` aux events
  - watchpoints v3 (`2026-05-20.v3`) — `kind=write|read|access` + byte-value filter
  - breakpoints v1 (`2026-05-20.v1`) — PC-address + symbol-name shortcut via `.map`
  - quirks v3 + matcher v4 (`2026-05-20.v4`) — `D0 C8..CF` ALU-imm HW crash + `D8..DF` r+r broken (sauf `cp r,r` exception)
- SR Phase 1+2 partial : 6 ALU flags (S/Z/V/H/C/N), `iff_level` 3-bit mask, `rfp` 2-bit bank pointer, PUSH SR / POP SR (0x02/0x03) opcodes consume `encode_sr_from_state` / `decode_sr_to_fields` end-to-end
- M1d Phase 1 : 32 256 B de RAM/VRAM on-chip pre-init `0x00` au cold-start (Work RAM, system page + override `0x6F91=mode_raw`, Z80 RAM, K2GE, SCR1/2, CHAR_RAM). CPU I/O page intentionnellement `unbacked`.
- Debugger (P0 ROADMAP §8) — 8/9 livres : memory-dump, registers, watchpoint (addr range + kind + value), breakpoint (addr + symbol), savestates, .map loader, disasm via NgpCraft_Disasm sister. Seul `screenshots` reste (depend M2 Phase 1).
- M2 Phase 0 inspecteurs livres : palette-info (5 plans 0BGR 12-bit), oam-info (64 sprites + CP.C strip), tilemap-info (32×32 grid SCR1/SCR2). Specs `K2GE_PALETTE.md`, `K2GE_OAM.md`, `K2GE_TILEMAP.md`.
- M2 Phase 0.5 premier rendu visuel : tile-view (CHAR_RAM 2bpp → 8×8 ASCII grayscale 4-niveaux). Rasterizer kernel reutilisable pour Phase 1 framebuffer. Spec `K2GE_TILES.md`.
- Sync sister projects coordonnee 2026-05-20 : NgpCraft_Disasm (local + GitHub) + NgpCraft_live_editor (HW-5 lint rule) tous resynced avec `quirks_db.json` v4 (`D0 C8..CF` + `D8..DF r+r` annotes broken avec messages per-sub-op + recommandation byte-split).
Snapshot prototype Python au 2026-04-22 (D8..DF r+r rule session):

Snapshot prototype Python au 2026-05-26 (post passes 96-97, `D2 abs24` word-memory catch-up) :
- **934 tests verts**
- decoder/executor:
  - `D2 abs24` now covers the next confirmed word-memory forms:
    - `cp R16, (abs24)`
    - `pushw (abs24)`
  - this closes the real StarGunner patterns:
    - `D2 FC 5E 00 F0` -> `cp WA, (0x005EFC)`
    - `D2 CC 2D 20 F6` -> `cp IZ, (0x202DCC)`
    - `D2 02 5F 00 04` -> `pushw (0x005F02)`
  - the earlier `F6` / `0x04` misses in that ROM were only fallout bytes from unresolved `D2` widths
- corpus coverage (`opcode-coverage --bytes 4096`) :
  - `StarGunner_save_lib_test/bin/main.ngc` = `4089 / 4096` bytes decoded (`99.8%`), `7` unknowns
  - remaining misses have moved away from `D2` and now cluster around the separate `D7 FA 04` / `D9 50` pockets

Snapshot prototype Python au 2026-05-26 (post pass 98, prefixed long r+r `mul/div` pocket) :
- **937 tests verts**
- decoder / quirk stop:
  - `D8..DF` prefixed long register families now decode the remaining
    register-to-register arithmetic pocket `0x40..0x5F`:
    - `mul`
    - `muls`
    - `div`
    - `divs`
  - the real `StarGunner_save_lib_test` pattern
    `D9 50` is now decoded as `div XWA, XBC`
  - the `cpu.d8_df_register_to_register` quirk matcher was tightened so
    these decoded forms stop honestly as `silicon-broken`
- corpus coverage (`opcode-coverage --bytes 4096`) :
  - `StarGunner_save_lib_test/bin/main.ngc` = `4093 / 4096` bytes decoded (`99.9%`), `3` unknowns
  - remaining misses are now only three standalone `0x04` frontiers

Snapshot prototype Python au 2026-05-26 (post pass 99, coverage fallout split) :
- **938 tests verts**
- tooling:
  - `opcode-coverage` now separates immediate fallthrough bytes after a
    decoded `silicon-broken` instruction from real decoder gaps
  - JSON output adds:
    - `silicon_broken_fallout_total`
    - `top_silicon_broken_fallout`
- corpus coverage (`opcode-coverage --bytes 4096`) :
  - `StarGunner_save_lib_test/bin/main.ngc` still = `4093 / 4096` bytes decoded (`99.9%`)
  - the remaining gap is now classified honestly as:
    - `0` unknown opcodes
    - `0` unsupported-decoded
    - `3` immediate post-silicon-broken fallout bytes (`0x04` after `D7 FA`)

Snapshot prototype Python au 2026-07-01 (post pass 126, TLCS-900/H control-register file + real `LDC` execution) :
- **1082 tests verts**
- timing / executor:
  - prefixed `push/pop r` now execute for the safe register-prefix subset:
    - byte `C8..CF : 04/05`
    - long `D8..DF : 04/05`
    - long `E8..EF : 04/05`
  - `C7 <reg> 04/05` now performs real 1-byte stack traffic on byte-slices
  - prefixed byte `DAA r` now executes for the safe register-prefix subset
  - `C7 <reg> 10` now mirrors `daa` on current-bank byte-slices
  - prefixed `PAA r` now executes for the defined word/long forms:
    - `D8..DF : 14`
    - `E8..EF : 14`
  - prefixed byte `PAA r` now stops honestly as `silicon-undefined`
  - prefixed byte `DJNZ r, d8` now executes for the safe byte forms
    (`C8..CF : 1C`)
  - prefixed long `DJNZ r, d8` now stops honestly as `silicon-undefined`
  - prefixed `MIRR r` now decodes/executes as the documented word-only
    special case `D8..DF : 16`
  - prefixed `BS1F/BS1B` now decode/execute as the documented word-only
    special cases `D8..DF : 0E/0F`
  - zero-source `BS1F/BS1B` now stop honestly as `silicon-undefined`
  - prefixed `MULA rr` now decodes/executes as the documented long-register
    special case `D8..DF : 19`
  - `MULA` now reads signed 16-bit words from `(XDE)` and `(XHL)`, adds the
    product into the selected 32-bit destination, then decrements `XHL` by `2`
  - prefixed `MINC1/2/4` and `MDEC1/2/4` now decode/execute as the documented
    word-only special cases `D8..DF : 38/39/3A/3C/3D/3E`
  - those modulo-adjust forms keep the encoded imm16 payload visible in the
    decode (`# - step`), then reconstruct and validate the actual modulo window
    `#` before executing
  - prefixed byte-register `ANDCF/ORCF/XORCF/LDCF/STCF` now decode/execute for
    the safe `C8..CF` family, with both immediate `#4` and dynamic `A` bit-index
    forms
  - byte out-of-range register forms stay honest:
    - `STCF` with bit index `8..15` leaves the byte operand unchanged
    - `ANDCF/ORCF/XORCF/LDCF` with bit index `8..15` stop as
      `silicon-undefined`
  - `C7 <reg> 20..24 / 28..2C` now mirrors that carry-flag family on
    current-bank byte-slices
  - impossible byte-only decoded forms now stop honestly as
    `silicon-undefined` instead of the generic executor fallback:
    - prefixed byte `EXTZ/EXTS`
    - `C7 <reg> 0D/12/13` (`UNLK/EXTZ/EXTS` on byte-slices)
  - the CPU state now models the locally verified TLCS-900/H control-register
    file subset:
    - `DMAS0..3`
    - `DMAD0..3`
    - `DMAC0..3`
    - `DMAM0..3`
    - `INTNEST`
  - prefixed `LDC` now executes for that subset instead of stopping at a
    control-register frontier:
    - `LDC cr, r`
    - `LDC r, cr`
  - prefixed `LDC` reads stop honestly as `requires-known-control-register`
    when the selected control-register source is still unknown
  - `C7 <reg> 2E/2F` now executes the real byte control-register subset
    (`DMAMn`) on current-bank byte-slices
  - `C7` byte-slice `LDC` targeting non-byte control registers now stops
    honestly as `silicon-undefined`
  - the current IRQ entry / `RETI` subset now also updates `INTNEST` when
    it is already known in the CPU control-register state
  - real Toshiba timing now also covers that stack subset:
    - prefixed `push r` = `4 / 4 / 6` (byte / word / long)
    - prefixed `pop r` = `5 / 5 / 7`
    - `C7 <reg> 04/05` byte-slice stack traffic = `4 / 5`
    - prefixed byte `djnz r, d8` = `6` taken / `4` not taken
    - prefixed / `C7` carry-flag register forms = `3`
    - prefixed / `C7` `LDC` = `3`
    - prefixed word-only `mirr r` = `3`
    - prefixed word-only `bs1f/bs1b` = `2`
    - prefixed `mula rr` = `19`
    - prefixed `minc1/2/4` = `5`
    - prefixed `mdec1/2/4` = `4`
  - `ExecutionResult.cycles_consumed` now also uses real Toshiba timing for
    the currently executed register/immediate subset:
    - `LD R,r`
    - `LD r,R`
    - `LD r,#3`
    - `LD R,#`
    - `LD r,#`
    - `LDA R,mem`
    - `ADD/ADC/SUB/SBC/AND/XOR/OR/CP`
    - `INC/DEC #3,r`
    - `DAA`
    - `PAA`
    - `EXTZ`
    - `EXTS`
  - the currently executed memory subset now also uses real Toshiba timing:
    - `LD R,(mem)`
    - `LD (mem),R`
    - `LD (mem),#8`
    - `LDW (mem),#16`
    - `CP` register/memory forms
    - `PUSHW (mem)`
  - the currently executed ALU-memory subset now also uses real Toshiba timing:
    - `ADD/ADC/SUB/SBC/AND/XOR/OR R,(mem)`
    - `ADD/ADC/SUB/SBC/AND/XOR/OR (mem),R`
    - `ADD/SUB/AND/XOR/OR (mem),#` for byte/word
    - `CP (mem),#` for byte/word
    - `INC/DEC #3,(mem)` for byte/word
  - the currently executed memory bit/carry subset now also uses real Toshiba timing:
    - `BIT #3,(mem)`
    - `LDCF/ANDCF/ORCF/XORCF` on `(mem)`
    - `STCF` on `(mem)`
    - `RES/SET/CHG/TSET` on `(mem)`
  - the currently executed memory rotate/shift subset now also uses real Toshiba timing:
    - `RLC/RRC/RL/RR (mem)`
    - `SLA/SRA/SLL/SRL (mem)`
  - the currently executed byte-register bit-op subset now also uses real Toshiba timing:
    - `BIT/RES/SET/CHG #4,r`
    - `TSET #4,r`
  - `INCF` and `DECF` are now executed for real and use their Toshiba
    `2`-cycle cost instead of the shared fallback
  - the currently executed prefixed shift-immediate register subset now
    also uses the Toshiba `3 + n/4` cycle formula:
    - `RLC/RRC/RL/RR/SLA/SRA/SLL/SRL #4,r`
  - the currently executed prefixed shift-by-A register subset now also
    executes for real and uses Toshiba timing:
    - `RLC/RRC/RL/RR/SLA/SRA/SLL/SRL A,r`
    - count source = low nibble of `A`
    - `RL` / `RR` still block honestly when `CF` is unknown
  - `LDX (#8), #` is now decoded/executed and uses its Toshiba `8`-cycle cost
- execution frontier widened slightly:
  - `RL #4,r` / `RR #4,r` now execute when `CF` is known
  - when `CF` is unknown they still stop honestly with
    `requires-known-flags`
  - `LDX (#8), #` no longer stops as `unknown-opcode`
- the matching `C7` current-bank byte-slice mirrors of those families now
  execute for the shift-immediate and shift-by-A subsets too, and use the
  same timing table instead of the shared 8-cycle fallback
- safe prefixed byte `CPL` / `NEG` now execute for real, and the matching
  `C7` current-bank byte-slice mirrors use the same Toshiba `2`-cycle timing
- product impact:
  - execution frontier widened slightly: bank-rotation instructions
    `INCF` / `DECF` now execute honestly instead of stopping as
    unsupported-decoded
  - `step-exec` / `run-steps` / `trace-exec` / frame-state advancement now
    accumulate more realistic cycle totals on the already modeled
    bootstrap/runtime paths

Snapshot prototype Python au 2026-05-26 (post pass 95, `F0 abs8` B0-memory catch-up) :
- **930 tests verts**
- decoder/executor:
  - `F0 abs8` now covers the confirmed B0-memory store forms:
    - `ld (abs8), imm8`
    - `ldw (abs8), imm16`
    - `ld (abs8), (abs16)`
    - `ldw (abs8), (abs16)`
  - this closes the real StarGunner flash-helper pattern `F0 66 02 D9 A9`
    as `ldw (0x66), 0xA9D9`
- corpus coverage (`opcode-coverage --bytes 4096`) :
  - `StarGunner_save_lib_test/bin/main.ngc` = `4075 / 4096` bytes decoded (`99.49%`), `21` unknowns
  - the remaining `0xF0` bytes in that ROM are a different unresolved sub-op pair, not the immediate/copy forms above

Snapshot prototype Python au 2026-05-26 (post pass 94, StarGunner abs24 memory follow-up) :
- **926 tests verts**
- decoder/executor:
  - `F2 abs24` now covers indirect memory calls:
    - `call (abs24)`
    - `call CC, (abs24)`
  - `C2 abs24` byte memory-immediate ALU now covers `0x38..0x3F`:
    - `add/adc/sub/sbc/and/xor/or/cp (abs24), imm8`
  - conditional indirect calls evaluate flags before touching memory or stack; false conditions fall through honestly
- corpus coverage (`opcode-coverage --bytes 4096`) :
  - `StarGunner_save_lib_test/bin/main.ngc` = `4074 / 4096` bytes decoded (`99.46%`), `22` unknowns
  - remaining misses are now mostly data/fallout bytes, the known broken `D2` prefix paths, and a smaller `F0 abs8` word-immediate pocket

Snapshot prototype Python au 2026-05-22 (post passes 58+59, byte-memory ALU catch-up) :
- **802 tests verts**
- decoder/executor:
  - `(r32+d8)` byte ALU `R8 <-> mem` complete sur `0x80..0xFF`
  - `C2` abs24 byte ALU `R8 <-> mem` complete sur `0x80..0xFF`
  - meme modele de blocage honnete conserve pour carry inconnu, source inconnue, memoire illisible, cible non writable
- corpus coverage (`opcode-coverage --bytes 2048`) :
  - `NGPC_Template__2026 - learn/bin/main.ngc` = `2035 / 2048` bytes decoded (`99.4%`), `14` unknowns
  - `MRROBOT.ngp` = `2025 / 2048` bytes decoded (`98.9%`), `24` unknowns
- frontier note:
  - le template restant ressemble surtout a des bytes de donnees / operandes apres `reti`
  - le prochain decode ROI s'est deplace vers `MRROBOT` et les formes bloc / word-memory (`0x91`, `0x95`, `0x99`, `0x88`)

Snapshot prototype Python au 2026-05-22 (post passes 60..69, static coverage lenses + collision cleanup across 4 corpora) :
- **859 tests verts**
- decoder/executor:
  - `F2 abs24` and `F1 abs16` now cover memory bit operations:
    - `bit`
    - `tset`
    - `res`
    - `set`
    - `chg`
  - `F2 abs24` and `F1 abs16` now cover memory carry-flag operations:
    - `andcf`
    - `orcf`
    - `xorcf`
    - `ldcf`
    - `stcf`
    - both `#bit,(mem)` and `A,(mem)` forms
  - `C1 abs16` byte immediate-memory ALU now covers `0x38..0x3F`:
    - `add/adc/sub/sbc/and/xor/or/cp (abs16), imm8`
  - word-memory multiply/divide families now cover:
    - `(r32)` `mul/muls/div/divs -> XR32`
    - `(r32+d8)` `mul/muls/div/divs -> XR32`
    - observed oracle case `94 5F` now decodes/executes as `divs XSP, (XIX)`
  - `D2 abs24 word` collision is now fixed for the observed load subset:
    - `ld R16, (abs24)`
    - real template case `D2 06 4F 00 20` now decodes/executes as `ld WA, (0x004F06)`
  - `D3/F3` secondary-indexed collision slice is now fixed for the observed `StarGunner` patterns:
    - `ld R16, (r32+r8/r16)`
    - `jp (r32+r8/r16)`
    - real cases `D3 07 F0 E0 20` and `F3 07 F0 E0 D8` now decode/execute correctly
- static tooling:
  - `trace-preview` now stops on a locally known `silicon-broken` instruction instead of decoding unreachable downstream noise
  - `opcode-coverage` adds an optional strict mode:
    - `--stop-on-silicon-broken`
    - useful for execution-faithful coverage, but intentionally not the default census mode
  - `opcode-coverage` also adds an optional structural stop mode:
    - `--stop-on-non-fallthrough`
    - useful when dead bytes after `ret` / `jp` / `halt` should not pollute the walk
  - `opcode-coverage` also adds an optional conservative direct-CFG mode:
    - `--follow-direct-control-flow`
    - worklist walk over decoded fallthrough edges plus known direct targets
    - useful when a linear census is too noisy but a pure stop-at-frontier lens is too narrow
- corpus coverage:
  - default `opcode-coverage --bytes 4096` on `NGPC_Template__2026/bin/main.ngc`:
    - `4091 / 4096` bytes decoded (`99.9%`)
    - `7` unknowns
  - strict `opcode-coverage --bytes 4096 --stop-on-silicon-broken` on the same ROM:
    - `652 / 4096` bytes decoded
    - `0` unknowns after the hardware-fatal stop
  - structural `opcode-coverage --bytes 4096 --stop-on-non-fallthrough` on the same ROM:
    - `363 / 4096` bytes decoded
    - `0` unknowns after the first non-fallthrough frontier
  - CFG-style `opcode-coverage --bytes 4096 --follow-direct-control-flow` on the same ROM:
    - `941 / 4096` bytes decoded (`23.0%`)
    - `0` unknowns after reachable worklist exhaustion
- frontier note:
  - the remaining default-template misses are now mostly likely data starts (`0x04`) or bytes downstream of a known `D7 F2` silicon-broken frontier
  - direct-edge CFG coverage is now at `0` unknowns on the four current reference ROMs:
    - `NGPC_Template__2026`

Snapshot prototype Python au 2026-05-22 (post pass 70, banked-register execution slice) :
- **861 tests verts**
- CPU / executor:
  - minimal banked byte-register backing store added for the explicit-bank `C7` byte slices on `XWA/XBC/XDE/XHL`
  - `LDF n` now flushes the visible core bank and reloads `XWA/XBC/XDE/XHL` from the selected bank
  - `C7` execution now supports:
    - explicit-bank byte targets (`RA0..QH3`)
    - previous-bank byte targets (`A'..QH'`)
  - current-bank byte-slot knowledge is reused even when the whole owner `XWA/XBC/XDE/XHL` is unknown:
    - prefixed byte register-register ALU
    - secondary-indexed byte-index effective-address computation
- tooling / persistence:
  - savestates now persist the banked byte-register backing store
  - CLI CPU diff/render now understands `RFP` and bank-qualified register views like `XWA@bank3`
- execution frontier:
  - `MRROBOT.ngp` run-steps frontier moved from `requires-register-banks` to a real memory/runtime limit
  - `run-steps --count 80 --seed-xsp 0x6C00 --seed-reg XIZ=0` now reaches `39` executed instructions
  - current stop: `runtime-memory-unavailable` on `ld XIX, (XIX+W)` at `0x00269274`
    - `NgpCraft_base_template`
    - `StarGunner_save_lib_test`
    - `MRROBOT`
  - the next ROI is therefore no longer "close the next reachable unknown" on these corpora, but either a new ROM corpus, indirect-control-flow-aware static walking, or deeper execution-frontier work

Snapshot prototype Python au 2026-05-22 (post passes 71+72, BIOS-backed execution + bank-qualified seeds) :
- **869 tests verts**
- CPU / executor:
  - optional external 64 KB BIOS backing can now feed reads in `0xFF0000..0xFFFFFF`
  - generic register extraction now reuses current-bank backing-store knowledge for:
    - byte reads
    - low-word reads
    - full `XWA/XBC/XDE/XHL` long reads
  - this is enough to unblock generic consumers like `push BC` / `push XBC` after `LDF`
  - execution seeds now support bank-qualified names:
    - `XWA@bank0..3`
    - `XBC@bank0..3`
    - `XDE@bank0..3`
    - `XHL@bank0..3`
- tooling / CLI:
  - `peek`, `decode-next`, `execute-next`, `step-exec`, and `run-steps` now accept `--bios <64KB image>`
  - bank-qualified `--seed-reg` values are persisted into the CPU bank backing store and become visible automatically when that bank is selected by `LDF`
- execution frontier:
  - `MRROBOT.ngp` with `--bios` now reaches BIOS code instead of stopping on an unbacked read:
    - `41` executed instructions
    - honest stop at `push XBC` (`0x00FF8D8A`)
  - with explicit caller-context seeds
    - `--seed-reg XBC@bank3=0 --seed-reg XDE@bank3=0 --seed-reg XHL@bank3=0`
    - the same path reaches `43` executed instructions
    - new honest stop at `push XIY` (`0x00FF8D8C`)
  - the remaining blocker in this BIOS path is now caller ABI knowledge, not missing bus plumbing or missing decode

Snapshot prototype Python au 2026-05-22 (post pass 73, BIOS-call seed preset) :
- **870 tests verts**
- tooling / CLI:
  - new exploratory shortcut:
    - `--seed-zero-bios-call-context`
  - it seeds the current practical BIOS-call context:
    - `XBC@bank3 = 0`
    - `XDE@bank3 = 0`
    - `XHL@bank3 = 0`
    - `XIY = 0`
    - `XIZ = 0`
  - explicit `--seed-reg` values still override the preset
- execution frontier:
  - `MRROBOT.ngp` with:
    - `--seed-xsp 0x6C00`
    - `--seed-zero-bios-call-context`
    - `--bios <bios_v10.bin>`
    now reaches the same BIOS frontier as the longer manual seed list
  - `44` executed instructions
  - honest stop:
    - `silicon-broken`
    - `0x00FF8D8D`
    - `D7 E6 = or IZ, SP`
  - local toolchain and disassembler references agree that this is a real `D0..D7` broken-family stop, not a decode collision

Snapshot prototype Python au 2026-05-22 (post pass 74, toolchain-derived caller-saved seed preset) :
- **871 tests verts**
- tooling / CLI:
  - new ABI-oriented shortcut:
    - `--seed-zero-caller-saved`
  - expands to the current toolchain-v2 observed caller-saved set:
    - `XWA = 0`
    - `XBC = 0`
    - `XDE = 0`
    - `XHL = 0`
    - `XIX = 0`
    - `XIZ = 0`
  - intentionally does not seed `XIY`
  - explicit `--seed-reg NAME=VALUE` still overrides the preset
- provenance:
  - derived from `NgpCraft_Toolchain_v2/reused_modules/t900cc_regclass.py`
  - current observed cdecl convention there is:
    - caller-saved/clobbered: `XWA/XBC/XDE/XHL/XIX/XIZ`
    - preserved across calls: `XIY/XSP`
- exploration value:
  - better fit than `--seed-zero-bank0` when resuming around ordinary function calls
  - avoids inventing a frame-pointer value for `XIY`
  - complements, but does not replace, `--seed-zero-bios-call-context` for the BIOS-specific path

Snapshot prototype Python au 2026-05-22 (post pass 75, toolchain-derived `__adecl` arg-register seed preset) :
- **872 tests verts**
- tooling / CLI:
  - new ABI-oriented shortcut:
    - `--seed-zero-adecl-args`
  - expands to the current toolchain-v2 observed `__adecl` argument registers:
    - `XWA = 0`
    - `XBC = 0`
    - `XDE = 0`
  - intentionally does not seed:
    - `XHL`
    - `XIX`
    - `XIY`
    - `XIZ`
  - explicit `--seed-reg NAME=VALUE` still overrides the preset
- provenance:
  - derived from `NgpCraft_Toolchain_v2/reused_modules/t900cc_regclass.py`
  - current observed ABI-v2 mapping there is:
    - `ABI_V2_ARG0 = XWA`
    - `ABI_V2_ARG1 = XBC`
    - `ABI_V2_ARG2 = XDE`
- exploration value:
  - narrower than `--seed-zero-caller-saved`
  - useful when probing register-argument entry paths without inventing wider scratch or frame state
  - complements the cdecl-oriented and BIOS-oriented seed presets instead of replacing them

Snapshot prototype Python au 2026-05-23 (post pass 76, toolchain-derived `XIZ` loop-variable seed preset) :
- **873 tests verts**
- tooling / CLI:
  - new toolchain/codegen shortcut:
    - `--seed-zero-toolchain-loop-iz`
  - expands only to:
    - `XIZ = 0`
  - explicit `--seed-reg NAME=VALUE` still overrides the preset
- provenance:
  - derived from `NgpCraft_Toolchain_v2/docs/09_CODEGEN_PATTERNS.md`
  - thc2 loop/call patterns there explicitly save and reuse `IZ` as the live loop variable
- exploration value:
  - much narrower than `--seed-zero-bank0` or `--seed-zero-caller-saved`
  - directly useful on the existing `StarGunner_save_lib_test` frontier:
    - without extra context, `run-steps --address 0x2079C6` stops on `push XIZ`
    - with `--seed-zero-toolchain-loop-iz`, the same path runs through the deeper copy-loop slice and reaches `0x0020D09E` within 24 executed instructions

Snapshot prototype Python au 2026-05-23 (post pass 77, sourced BIOS-hand-off XSP preset) :
- **874 tests verts**
- tooling / CLI:
  - new sourced reset-layer shortcut:
    - `--seed-bios-handoff-xsp`
  - expands only to:
    - `XSP = 0x00006C00`
  - explicit `--seed-reg XSP=...` or `--seed-xsp ...` still overrides the preset
- provenance:
  - derived from [RESET_STATE.md](C:/Users/wilfr/Desktop/NGPC_RAG/04_MY_PROJECTS/NgpCraft_emulator/specs/RESET_STATE.md:42)
  - local BIOS hand-off contract there sets `regs.xsp = 0x00006C00`
- exploration value:
  - removes the repeated magic-number `--seed-xsp 0x6C00` from the common smoke path
  - combines naturally with `--seed-zero-toolchain-loop-iz`
  - reproduces the historical StarGunner smoke frontier with:
    - `25 072` executed instructions
    - honest stop on `0x0020D180 ld XBC, XWA`
    - `stop_reason = stopped-on-silicon-broken`

Snapshot prototype Python au 2026-05-24 (post pass 79, UI joypad mapping) :
- **880 tests verts**
- UI / debugger:
  - PyQt6 frontend now maps host keyboard input to the documented
    active-high joypad byte `0x006F82`
  - current default mapping:
    - arrows -> D-pad
    - `Z` -> A
    - `X` -> B
    - `Enter` -> Option
  - the UI ignores host auto-repeat and does not capture text-entry
    widgets while typing in debugger fields
  - the status bar now surfaces the live joypad state (`pad=...`)
- core/session:
  - `EmulatorSession` exposes `joypad_state()` +
    `set_joypad_mask(mask, pressed=...)`
  - the joypad state lives in the existing writable overlay at
    `0x006F82`; when no button is pressed the overlay cell is removed
    and reads fall back to the cold-start system-page default `0x00`

Snapshot prototype Python au 2026-05-24 (post pass 80, disassembly go-to navigation) :
- **885 tests verts**
- UI / debugger:
  - Disassembly dock now supports explicit navigation by address or
    symbol
  - `Go to:` accepts:
    - `0x...`
    - decimal addresses
    - loaded symbol names
  - the debugger keeps a disassembly anchor distinct from the live PC:
    - `@PC` = follow the current execution frontier
    - `0x...` = manually anchored static code inspection
  - status bar now surfaces the active disassembly mode
    (`disasm=@PC` or `disasm=0x...`)
- core/session:
  - `EmulatorSession` now exposes `disassemble_from(address, count=...)`
  - `disassemble_around_pc()` delegates to the same walker

Snapshot prototype Python au 2026-05-25 (post pass 81, BP/WP registry persistence in the PyQt6 debugger) :
- **889 tests verts**
- UI / debugger:
  - File menu now exposes:
    - `Load Breakpoints`
    - `Save Breakpoints`
    - `Load Watchpoints`
    - `Save Watchpoints`
  - these actions target the existing ROM-local registries instead of
    inventing a second persistence format:
    - `.ngpc_emu/breakpoints/<rom>.breakpoints.json`
    - `.ngpc_emu/watchpoints/<rom>.watchpoints.json`
  - breakpoint list rows now show stable ids (`#N`) so duplicate-PC
    entries remain distinguishable after a reload
- core/session:
  - `EmulatorSession` now round-trips the shared registry models via:
    - `load_breakpoint_registry()` / `save_breakpoint_registry()`
    - `load_watchpoint_registry()` / `save_watchpoint_registry()`
  - live breakpoints are no longer a plain `address -> label` dict;
    they now keep per-row ids and preserve duplicate addresses like
    the CLI registry does

Snapshot prototype Python au 2026-05-25 (post pass 82, PyQt6 layout persistence) :
- **890 tests verts**
- UI / debugger:
  - the PyQt6 debugger now persists its window geometry and dock
    state through `QSettings`
  - saved layout present:
    - restore previous visibility / docking / floating arrangement
    - skip the first-run `_arrange_floating_docks()` override
  - no saved layout present:
    - preserve the pass-52 first-run contract (inspectors hidden by
      default, default floating placement)
  - `Reset Window Layout` now also clears the persisted layout keys
    before reapplying and re-saving the default arrangement
- test coverage:
  - Qt offscreen suite now guards itself against ambient real-user
    `QSettings` state by clearing `window/*` keys per test
  - one dedicated test proves dock visibility + main-window size
    survive a restart through the persisted layout

Snapshot prototype Python au 2026-05-25 (post pass 83, SR Phase 3.0 shadow flags + EX F,F') :
- **892 tests verts**
- CPU / executor:
  - `NgpcCpuState` now models the alternate TLCS-900/H flag set `F'`
    as `alt_flags`
  - opcode `0x16` / `ex F,F'` now executes honestly:
    - swaps visible flags with `F'`
    - writes `F`, `F'`, and `PC`
    - degrades to an all-unknown shadow set when no incoming `F'`
      was seeded
- persistence / CLI:
  - savestate format is now `2026-05-25.v4`
  - `cpu.alt_flags` round-trips through savestates with backward
    compatible load for existing `v3` / `v2` files
  - `cpu-info` and `registers` now expose the shadow `Flags'` set in
    both human and JSON views
- M1b note:
  - SR Phase 3 no longer lacks `EX F,F'`
  - the remaining gap is visible register-window bank switching on
    `RFP` changes

Snapshot prototype Python au 2026-05-25 (post pass 84, SR Phase 3.1 bank-window reload on POP SR / RETI) :
- **894 tests verts**
- CPU / executor:
  - `POP SR` now reloads visible `XWA/XBC/XDE/XHL` after restoring `rfp`
  - `RETI` now does the same after popping SR from the interrupt stack frame
  - outgoing visible core-register values are flushed into the old bank
    backing store before the target bank is reloaded
- M1b note:
  - the currently modeled `RFP` transition paths are now coherent:
    - `LDF`
    - `POP SR`
    - `RETI`
  - the observable bank-window reload slice is closed for the current
    core register-bank model

Snapshot prototype Python au 2026-05-25 (post pass 85, executor fetch sees the writable runtime overlay) :
- **895 tests verts**
- CPU / executor:
  - instruction decode/fetch inside the executor now consults the
    writable runtime overlay before falling back to the read bus
  - RAM-resident handlers/stubs and vector-seeded `RETI` bytes can now
    execute end-to-end in `build_execute_next` / `build_run_steps`
- M3 note:
  - this closes the old "fetch-from-overlay not modeled" gap inside the
    current IRQ delivery model
  - any future BIOS/vector hand-off fidelity work is now a separate
    semantic layer, not a missing fetch-plumbing issue

Snapshot prototype Python au 2026-05-25 (post pass 92, live UI BIOS bridge) :
- **911 tests verts**
- CPU / executor:
  - `try_deliver_pending_irq` now prefers the 4-byte handler pointer
    stored in the user vector slot itself (`0x6FCC` for VBlank)
  - end-to-end run loops can now traverse `IRQ -> vector slot pointer
    -> RETI in ROM/RAM -> return` in the current model
  - `step-exec` / `run-steps` / `trace-exec` / `run-until-exec` now
    expose `last_irq_delivery` in JSON (slot, raw pointer, resolved
    target, fallback path), and RETI payloads no longer choke on `SR`
  - real Toshiba cycle rows now also cover `PUSHW #16`, `PUSH R16`,
    `PUSH R32`, `POP R16`, and `POP R32`
  - real Toshiba cycle rows now also cover the currently executed
    indirect memory control-flow forms `jp (XIX+WA)` and `call (XIX)`
  - `EX F,F'` now also reports its Toshiba-backed `2` cycles
  - real Toshiba cycle counts are now populated for the common
    control-flow / CPU-control subset (`NOP`, `RETI`, `JP/JR/JRL`,
    `CALL/RET`, `EI/DI`, `LDF`, `LINK/UNLK`, `SWI`)
  - the live debugger UI/session can now consume the same external
    64 KB BIOS image as the CLI `--bios` path, which removes the
    `0xFFxxxx` read gap that kept some ROMs on a flat backdrop
- M3 note:
  - the remaining simplification is only the unset-slot fallback:
    `0x00000000` still falls back to the slot address itself for
    debugger/bootstrap usability

Snapshot prototype Python au 2026-05-26 (post pass 93, pre-decrement load slice) :
- **916 tests verts**
- CPU / executor:
  - first `ARI_PD` / pre-decrement load subset now executes for:
    - `ld R8, (-R32)` (`C4`)
    - `ld R16, (-R32)` (`D4`)
    - `ld R32, (-R32)` (`E4`)
  - the address register is decremented by the access width before the
    read, then the decremented value is persisted back into the source
    `R32`
  - aliasing forms such as `ld XWA, (-XWA)` stop honestly on
    `unmodeled-register-alias-side-effects` instead of guessing an
    update order
- corpus impact:
  - real `StarGunner_save_lib_test` gap at `0x00208103`
    (`E4 E0 21`) now decodes as `ld XBC, (-XWA)`
  - noisy linear `opcode-coverage --bytes 4096` on that ROM improved
    from `3912 / 4096` decoded bytes (`95.51%`) to
    `4054 / 4096` (`98.97%`)

Snapshot prototype Python au 2026-04-22 (D8..DF r+r rule session):
- executor: **25 072** honest steps on StarGunner smoke
  (2026-04-13: 27 377 -> 2026-04-20 flash+trace: 27 551 -> 2026-04-20 SCC: 27 556
   -> 2026-04-22 v3 D8..DF r+r rule: 25 072)
- decoder: 0x70..0x7F SCC cc, r family (all prefix sizes)
- tests: 161 (+2 from 159 after the D8..DF rule: `ld XBC, XWA` broken and
  `cp XWA, XHL` CP-exception stays executable)
- new frontier: `0x0020D180  D8 89` = `ld XBC, XWA`
- reference stop reason at that frontier: `stopped-on-silicon-broken`
- D0..D7 word-register prefix family is surfaced as an explicit hardware-faithful stop
  in `execute-next`, `run-until-exec`, and `trace-exec` instead of a generic
  `unsupported-decoded-instruction`; D8..DF working-bank prefix now gets the same
  treatment on `r+r` sub-ops per USER_MANUAL_EN.md §12.1
- execution-facing and decode-only payloads now expose matched local quirk metadata
  when relevant
- the local quirk registry is now backed by `core/quirks_db.json` version `2026-04-22.v3`
- each quirk rule now carries a non-empty `sources` list and every matched-quirk
  payload exposes that per-rule attribution alongside the existing id / confidence
- the honest frontier regressed from 27 556 to 25 072 on purpose: per
  HARDWARE_COMPAT_POLICY §4.1 the reference mode must stop on documented-broken
  forms instead of inventing a post-state; the loss is a policy win, not a bug

Snapshot prototype Python au 2026-04-13:
- `3.1 ROM loading` = `partial`
- `3.2 CPU` = `partial`
- `3.2.b Broken opcodes and silicon bugs` = `partial`
- `3.3 Memory / bus` = `partial`
- `4.1 Disassembly` = `partial`
- `4.4 Memory tools` = `partial`
- `9.1 CLI` = `partial`
- le reste = `planned`

Progression executor (2026-04-13 session 2, mise a jour):
- **27 377** instructions executees honnetement sur ROM smoke stable (Stargunner)
- nouvelles familles ajoutees depuis derniere maj:
  - Open-bus write-discarded: unmapped + ROM address stores continue execution — `_check_writable_range` redesign
  - push/pushw/pushl (r32+d8): `80+zz+mem : 04` — decode + executor
  - ld (abs16), R8: `F1 [addr16] 40+r` — decode + executor
  - ALU reg-reg expanded: OR, AND, XOR, SUB, CP added (0xA0..0xA7, 0xC0..0xC7, 0xD0..0xD7, 0xE0..0xE7, 0xF0..0xF7)
  - shift/rotate with imm count: `sll/srl/sla/sra/rlc/rrc N, r` (0xE8..0xEF family) — decode + executor
  - (sessions precedentes: 0xC2 abs24, multu/muls, cp R imm3, cp (r32) imm8, ret CC, F3 lda, ARI indexed, CPU I/O stores, (r32+d8) imm stores, (r32) byte-indirect load)

Important:
- ce snapshot decrit le prototype local actuel
- il ne remplace pas les criteres cibles du document
- `partial` couvre ici les helpers bootstrap, statiques ou read-only deja disponibles, pas une validation de jalon complet

## 2. Competitive target

Le projet doit depasser l'experience NGPC typique des suites multi-systemes sur:
- observabilite hardware
- symboles et integration toolchain
- diff entre builds
- profiler
- coherence standalone / engine / headless

Le projet n'a pas besoin de copier chaque fonction "paper feature" d'un gros frontend multi-systeme.
Il doit en revanche battre clairement l'existant sur les fonctions qui comptent reellement pour NGPC.

## 3. Core runtime

### 3.0 Peripheral + BIOS model — statut 2026-07-10 (passes 180-186)

Snapshot consolide. **Chaque constante de ces sous-systemes est citee d'un
document constructeur** (manuel CPU Toshiba, datasheet TMP95C061, SDK officiel
SNK) — voir `DOC_SOURCES_INDEX.md` § 0.

| Sous-systeme | Statut | Spec | Notes |
|---|---|---|---|
| **BIOS `swi 1` (SYSTEM_CALL)** | `done` | `specs/BIOS_HLE.md` | Dispatch sur **RW3**. Tous les vecteurs deterministes implementes : SHUTDOWN, CLOCKGEARSET, INTLVSET, RTCGET, FLASHWRITE, SYSFONTSET, 6× SYS_SUCCESS, comms sans peer. |
| **SYSFONTSET (font systeme)** | `done` | `specs/BIOS_HLE.md` § 4.1 | **Vraie font SNK**, lue dans le BIOS attache (`0xFF8DCF`). Rien d'embarque. Sans BIOS → honest-stop. |
| **Flash / saves** | `done` | `specs/FLASH.md` | **Les deux chemins** : BIOS-medie (`VECT_FLASHWRITE`) **et** direct (sequence AMD + `/WE`, = la lib flash maison du projet). Non-volatile a travers `reset()`. |
| **Controleur d'interruptions** | `done` | `specs/FRAME_TIMING.md` § 3.6-3.7 | **Multi-source.** Table de vecteurs HW (`0xFFFF00`) → handler BIOS ; table RAM (`0x6FB8`) pour le hook user. Regles de masque datasheet (`L >= IFF`, `IFF := L+1`). VBlank = **niveau 4**. |
| **A/D converter (batterie)** | `done` | `specs/ADC.md` | ADMOD/ADREG0, 320 cycles/conversion, leve **INTAD** (vecteur HW 28). C'est lui qui empeche le BIOS de s'eteindre. |
| **Timers 8 bits 0..3** | `done` | `specs/TIMERS.md` | TRUN/TREG/T01MOD/T23MOD, taps T1=128 / T4=512 / T16=2048 / T256=32768 cycles, levent INTT0..3 (vecteurs HW 16..19). |
| **Valeurs de power-on** | `done` | `specs/MEMORY_READ.md` § 2 | Page I/O **et** registres K2GE ne resettent **pas** a zero. Table complete. |
| Timers 16 bits (4/5) | `todo` | — | `INTTR4..7`. Aucune ROM ne les a exerces. |
| Micro-DMA | `todo` | — | Vecteurs connus (`INTTC0..3`), moteur non modelise. |
| Comms data-transfer | `todo` | `specs/BIOS_HLE.md` | Demande un peer connecte + l'IRQ comms. Le chemin "sans cable" est fait. |

**Non-regression de fidelite** : apres tous ces changements d'etat cold-start, le
corpus reste **byte-exact contre l'oracle** (`oracle_tools/cosim_diff.py`) — Big
Bang / Cotton / Crush Roller : 0 divergence sur 3 000 pas ; Neo Turf / Pac-Man /
Magical Drop : 0 divergence. Les 2 seules divergences (Metal Slug, Puzzle Bobble)
sont le decalage HLE-vs-LLE d'un pas sur un `swi 1`, documente.

### 3.1 ROM loading

Statut prototype actuel:
- `partial`

Prototype courant:
- chargement `.ngc` / `.ngp`
- lecture du header via `info`
- bootstrap reset minimal via `reset-info`
- premiere visibilite du mapping cart via `addr-info`

Gaps ouverts:
- pas encore de standalone GUI
- pas encore d'integration engine
- reset encore partiel

Doit couvrir:
- chargement `.ngc` / `.ngp`
- metadata/header viewer
- reset propre
- mapping cart de base

Acceptation minimale:
- la ROM s'ouvre depuis standalone, engine et CLI
- l'identite ROM est exposee dans les logs et captures

### 3.2 CPU

Statut prototype actuel:
- `partial`

Prototype courant:
- conteneur CPU bootstrap minimal
- fetch brut via `fetch-next`
- decodeur TLCS-900 partiel via `decode-next`
- premier executeur reel etroit via `execute-next`
- premier modele writable de pile pour `execute-next`
- premiere tranche officielle `lda abs24` / store indexe / `ld` registre a registre
- premier sous-ensemble de flags modeles via `cp`
- premiere execution conditionnelle `jr` / `jrl` quand les flags sont connus
- premiere tranche `abs16` byte-memory observable sur la ROM stable
- premier backing lisible minimal de memoire systeme pour le bootstrap officiel (`0x6F86`, `0x6F91`)
- premiere tranche de stores absolus utiles (`abs16` / `abs24`, immediats et registres)
- premiere tranche post-increment byte copy / zero-fill utile sur la ROM stable officielle
- seed manuel des registres 32-bit pour les smokes honnetes
- premier `run-steps` stateful borne
- previews statiques `step-preview`, `next-preview` et `run-until-preview`
- trace statique decode-only via `trace-preview`

Gaps ouverts:
- execution reelle encore tres partielle
- mutation d'etat encore limitee a un sous-ensemble etroit
- pile writable encore limitee au sous-ensemble execute courant
- flags/registres/modes encore incomplets
- K2GE lisible encore non backe pour les premieres operations RMW (`0x8030`, `0x8012`, ...)
- pas de single-step fiable

Doit couvrir:
- execution normale
- step instruction
- run until
- etat CPU complet
- flags et registres fiables

Acceptation minimale:
- traces reproductibles
- single-step coherent
- decodeur croise avec le disassembleur maison

### 3.2.b Broken opcodes and silicon bugs

Statut prototype actuel:
- `partial`

Prototype courant:
- premiers warnings explicites dans le decodeur
- premiers cas documentes de risques silicium / familles cassees
- diagnostic expose en CLI et JSON
- premier arret d'execution de reference sur un opcode casse confirme:
  - famille `D0..D7` -> statut `silicon-broken`
  - propagation jusqu'a `run-until-exec` et `trace-exec`
- premier registre local de quirks dans `core/quirks.py`
  - encode deja l'exception "formes immediates documentees comme sures"

Gaps ouverts:
- un seul premier cas execute est modele explicitement a date
- pas encore de base de quirks versionnee separee du decodeur

Doit couvrir:
- opcodes casses connus
- comportements CPU non standards observes
- differences entre comportement "spec idealisee" et comportement reel

Acceptation minimale:
- si le hardware reel plante ou diverge sur un cas connu, le mode de reference ne doit pas le corriger
- le debugger doit expliquer le contexte du plantage: instruction, etat CPU, derniers evenements, source documentaire si connue

### 3.3 Memory / bus

Statut prototype actuel:
- `partial`

Prototype courant:
- espace d'adresses minimal nomme
- lecture ROM-backed en read-only via `peek`
- distinction explicite `ok` / `unbacked` / `unmapped` / `out-of-file`

Gaps ouverts:
- pas d'ecriture memoire
- pas de backing RAM/VRAM/IO reel
- pas de watchpoints

Doit couvrir:
- ROM
- RAM
- VRAM
- IO
- regions distingues clairement

Acceptation minimale:
- lecture/ecriture visibles dans le debugger
- watchpoints exploitables

### 3.4 Timing / frame pacing / slowdown fidelity

Doit couvrir:
- temps emule distinct du temps hote
- budget frame visible
- detection des frames manquees
- cadence de jeu reproduite
- absence de lissage qui masquerait une surcharge reelle

Acceptation minimale:
- si une scene tombe a environ 20 fps sur hardware, le mode de reference doit montrer un comportement comparable
- les outils de profilage expliquent pourquoi le budget frame est depasse

### 3.5 Audio core

Statut prototype actuel:
- `planned`

Doit couvrir:
- generation audio fidele au hardware cible
- stepping deterministe et testable
- separation nette entre coeur audio, sortie host et UI
- integration propre dans le standalone et dans `NgpCraft_engine`
- reintegration possible dans d'autres outils du workspace
- cible explicite: remplacement futur du core NeoPop actuellement utilise par le tool son

Acceptation minimale:
- le coeur audio peut etre utilise sans frontend
- l'API d'integration ne depend pas de widgets ou de logique standalone
- le meme comportement est obtenu a entree egale depuis l'emulateur et depuis un hote externe
- le remplacement du backend NeoPop du tool son est techniquement prevu, pas juste "possible en theorie"

## 4. Debugger - must have

### 4.1 Disassembly

Statut prototype actuel:
- `partial`

Prototype courant:
- decode instruction par instruction par adresse
- preview lineaire statique avec bytes, asm et warnings
- classification minimale du controle de flux

Gaps ouverts:
- pas de vue disasm live
- pas de `.map`
- pas de navigation symbole

Doit couvrir:
- vue disasm live
- PC courant
- labels/symboles `.map`
- navigation par adresse et par symbole
- follow branch / call target

Non acceptable:
- une vue disasm desacouplee de l'etat reel
- symboles non resolus alors qu'un `.map` valide est charge

### 4.2 Breakpoints

Statut prototype actuel:
- `working` (post-run filter v1 ; live pause-on-hit reste M4)

Prototype courant (passes 12 + 14) :
- `breakpoint add <rom> <addr> [--label]` registre per-ROM
- `breakpoint add-symbol <rom> <name> --map <file>` resout via `.map`
- `breakpoint list / remove / clear / check`
- format `ngpc-emu-breakpoints 2026-05-20.v1`, spec `specs/BREAKPOINTS.md`

Doit couvrir:
- breakpoint adresse — `fait`
- breakpoint symbole — `fait` (add-symbol via .map)
- breakpoint execute/read/write — execute = `fait` (PC match) ; read/write = utiliser watchpoints
- enable/disable — `a faire`
- conditions simples ensuite — `a faire` (Phase 3 watchpoints couvre value match)

Acceptation minimale:
- arret fiable — v1 = post-run filter (capture event-log puis matche) ; live pause = M4
- reprise fiable — savestate / checkpoint / session existant
- export/import de sessions plus tard — `fait` via session save/load + snapshots

### 4.3 Watchpoints

Statut prototype actuel:
- `working` (Phase 3 universal read tracking + v3 byte-value filter)

Prototype courant (passes 6 a 10) :
- `watchpoint add <rom> <addr> [--kind write|read|access] [--size N] [--value BYTE]`
- match universel : tous les 22 sites `_read_runtime_bytes` collectent
  via accumulator module-level dans `build_execute_next` (zero call-site
  change). Matche les `events[].memory_writes` ET `memory_reads`.
- format `ngpc-emu-watchpoints 2026-05-20.v3`, spec `specs/WATCHPOINTS.md`
- pair avec event-log v2 (memory_reads ajoutes additivement)

Doit couvrir:
- RAM — `fait`
- VRAM — `fait` (couvert via le bus + overlay)
- IO — `fait`
- taille 8/16/32 si pertinent — `partiel` (size en bytes, pas en mots typés)

Acceptation minimale:
- utile sur vrais cas DMA/VRAM/IRQ — `fait` (workflow "find opcode writing V to A" = one-liner)
- message clair sur la cause de l'arret — `fait` (hit detail = event_index, pc, address, data_hex, assembly)

### 4.4 Memory tools

Statut prototype actuel:
- `working` (passes 11 + 16-19)

Prototype courant :
- `peek <rom> <addr> [--count N]` pour lire des octets bruts via le bus
- `addr-info <rom> <addr>` pour qualifier une adresse et sa region
- `memory-dump <rom> <addr> [--count N] [--width W] [--seed-from state.json] [--json]` hexdump multi-row + ASCII column ; `--seed-from` overlay savestate
- `registers <rom> [--seed-from]` vue rich 8 R32 + decomposition R16/R8 + PC + SR + IFF + RFP + 6 flags
- `palette-info <rom> [--kind] [--seed-from]` decode K2GE palette RAM (5 plans 0BGR 12-bit)
- `oam-info <rom> [--visible-only] [--seed-from]` 64 sprites + CP.C strip
- `tilemap-info <rom> [--plane scr1|scr2] [--non-empty] [--list] [--seed-from]` 32×32 grid SCR1/SCR2, vue ASCII compacte par defaut
- `tile-view <rom> <tile-id> [--plane sprite|scr1|scr2 --palette N] [--seed-from]` rend un tile 8×8 CHAR_RAM en ASCII 4-niveaux grayscale (premier rendu visuel)

Gaps ouverts:
- pas de recherche memoire (`memsearch <rom> <pattern>`)
- pas de poke runtime (les overlays sont visibles via savestates)
- pas de vue memoire interactive (CLI seulement, pas de TUI)

Doit couvrir:
- hexdump memoire — `fait` (memory-dump)
- follow pointer — `a faire`
- goto address — partial via `addr-info`
- poke facultatif plus tard — `a faire`
- recherche memoire — `a faire`

Acceptation minimale:
- lecture stable — `fait`
- regions nommees — `fait` (via `core/bus.py` AddressMapEntry kind/name + `addr-info`)
- changement d'affichage sans perdre le contexte — `fait` (5 commandes du meme savestate `--seed-from`)

### 4.5 Call stack / execution history

Doit couvrir:
- historique recent
- pile visible
- liens vers symboles

Acceptation minimale:
- utile pour remonter un plantage ou une divergence

### 4.6 Crash diagnostics

Doit couvrir:
- capture des dernieres instructions
- contexte CPU complet
- etat memoire/IO pertinent
- derniers evenements IRQ/DMA/HBlank/VBlank
- heuristiques de diagnostic non intrusives

Acceptation minimale:
- quand une ROM freeze ou plante comme sur hardware, le debugger fournit mieux qu'un simple "stopped"
- le rapport n'altere pas l'execution qui a mene au crash

## 5. NGPC-specific visibility

### 5.1 Video state

Doit couvrir:
- framebuffer
- scroll planes
- OAM
- palettes BG/sprites
- tile viewer
- tilemap viewer

Acceptation minimale:
- voir clairement ce que le hardware afficherait
- possibilite d'isoler SCR1, SCR2, sprites

### 5.2 IRQ / timers / scanlines

Doit couvrir:
- VBlank
- HBlank
- timers
- entree/sortie IRQ
- priorites si observables

Acceptation minimale:
- timeline lisible
- correlation avec le frame debugger

### 5.3 DMA

Doit couvrir:
- etat courant DMA
- source/destination/taille
- evenements start/stop/complete
- impact visible sur VRAM ou registres

Acceptation minimale:
- diagnostic utile sur les cas reels du toolchain
- pas juste un bit de statut affiche quelque part

### 5.4 Hardware quirk awareness

Doit couvrir:
- etiquetage des comportements connus comme "silicon quirk", "broken opcode", "undefined but observed"
- lien vers la source doc ou le test de reproduction quand disponible

Acceptation minimale:
- un utilisateur peut comprendre si un crash vient d'un vrai comportement hardware connu

## 6. Profiler - must have

### 6.1 Frame profiler

Doit couvrir:
- temps/cycles par frame
- budget frame
- evenements majeurs

Acceptation minimale:
- expliquer pourquoi une build tombe a 10-20 fps
- exposer explicitement les frames manquees ou le depassement du budget emule

### 6.2 Symbol profiler

Doit couvrir:
- cout par symbole
- top hot paths
- cout IRQ
- possibilite de comparer deux runs

Acceptation minimale:
- exploitable avec les `.map` cc900

### 6.3 Event profiler

Doit couvrir:
- DMA
- IRQ
- HBlank/VBlank
- audio plus tard

Acceptation minimale:
- timeline et stats

## 7. Diff and regression tools

### 7.1 Trace diff

Doit couvrir:
- comparaison run A / run B
- premiere divergence
- export lisible

Acceptation minimale:
- utile sur ROM officielle vs ROM maison

### 7.2 Frame diff

Doit couvrir:
- comparaison image/framebuffer
- heatmap de difference
- seuils configurables

Acceptation minimale:
- utile pour les regressions visuelles

### 7.3 Event diff

Doit couvrir:
- IRQ
- DMA
- ordre d'evenements

Acceptation minimale:
- pointer une divergence temporelle claire

Etat prototype actuel:
- `fait` sur le sous-ensemble deja capture par `eventlog v1`:
  - `eventlog diff <left.json> <right.json>` pour la premiere divergence
  - `eventlog check <rom> <golden.json> [...]` pour le wrapper CI/golden-trace
  - `eventlog check` renvoie `0` si identique, `1` si divergence, et peut
    sauver le log courant via `--save-current`
  - `eventlog golden-save/list/load/delete/check` pour un registre nomme
    local de goldens au-dessus des fichiers JSON bruts

## 8. Determinism and replay

### 8.1 Input replay

Doit couvrir:
- enregistrement inputs
- replay deterministe
- hash ou verification d'etat

Acceptation minimale:
- meme ROM + meme inputs = meme resultat

### 8.2 Save states

Doit couvrir:
- save/load instantane
- versioning format
- compatibilite raisonnable intra-version

Acceptation minimale:
- etat restaure sans corruption evidente

## 8.b Persistent game saves

Les saves in-game ne doivent pas etre confondues avec les save states.

> **Statut 2026-07-10 : `done` (les deux chemins d'ecriture).** Voir
> `specs/FLASH.md`.
>
> Le NGPC ecrit sa flash cartouche (`0x200000..0x3FFFFF`) de **deux** facons, et
> les deux sont modelisees :
> - **BIOS-medie** — `VECT_FLASHWRITE` (`swi 1`, RW3 = 6) : les jeux retail.
> - **Direct** — sequence de commandes AMD contre la fenetre cart, avec `/WE`
>   (I/O `0x6E` = `0x14`) : **c'est ce chemin qu'utilise la lib flash MAISON du
>   projet** (`ngpc_flash_asm.asm`, HW-validee), donc c'est lui qui persiste les
>   saves de nos jeux.
>
> Les octets commits atterrissent dans l'overlay writable de la session, qui
> shadow l'image ROM du cart **exactement comme la NOR flash overlay la
> cartouche** — donc les reads suivants et le savestate voient le save.
> **Non-volatile** : `reset()` efface le latch de commande mais **garde** le
> contenu (un power-cycle cartouche-en-place conserve les sauvegardes).
>
> Reste `todo` (documente, pas maquille) : polling de statut DQ7/DQ5 (on commit
> de facon synchrone → le poll lit direct la valeur finale, le *resultat* est
> correct), block-erase par secteur, et un fichier `.sram` autonome sur disque.

### 8.b.1 Save media support

Doit couvrir:
- support correct du support de sauvegarde associe a la ROM ou au mapper
- persistence sur disque
- chargement automatique au lancement
- ecriture fiable a la fermeture et pendant l'execution si necessaire

Acceptation minimale:
- une sauvegarde creee par le jeu est retrouvee a la session suivante
- pas de comportement aleatoire ou de perte silencieuse

### 8.b.2 Save tooling

Doit couvrir:
- emplacement clair des saves
- import/export
- backup simple
- inspection minimale si utile

Acceptation minimale:
- exploitable en standalone et via `NgpCraft_engine`

### 8.b.3 Save validation

Doit couvrir:
- tests de round-trip
- test sur au moins une ROM de reference avec sauvegarde
- separation claire avec les save states

Acceptation minimale:
- aucune confusion UX entre "charger un save state" et "charger la sauvegarde du jeu"

### 8.3 Reverse debug

Doit couvrir:
- reverse frame d'abord
- reverse step ensuite

Acceptation minimale:
- utile pour un vrai crash/debug, pas juste un proof-of-concept

## 9. Headless and automation

### 9.1 CLI

Statut prototype actuel:
- `partial`

Prototype courant:
- `info`
- `reset-info`
- `addr-info`
- `cpu-info`
- `peek`
- `fetch-next`
- `decode-next`
- `execute-next`
- `run-steps`
- `trace-preview`
- `step-preview`
- `next-preview`
- `run-until-preview`

Gaps ouverts:
- pas encore de `run`
- pas encore de `run-frame`
- pas encore de `profile`
- pas encore de `capture`

Doit couvrir:
- `info`
- `run`
- `run-frame`
- `trace`
- `profile`
- `capture`

Acceptation minimale:
- integrable dans CI sans GUI

### 9.2 Batch regression

Doit couvrir:
- corpus de ROMs
- comparaison automatique
- code retour fiable

Acceptation minimale:
- remplacement credible du `smoke-run` externe actuel

Etat prototype actuel:
- `partiel`:
  - registre nomme de goldens event-log en place
  - `eventlog check` / `eventlog golden-check` donnent un code retour fiable
  - premiere tranche de corpus micro-ROMs synthetiques stable:
    - `arith-add-wa`
    - `arith-sub-wa-zero`
    - `arith-and-wa-zero`
    - `arith-xor-wa-zero`
    - `arith-or-wa-sign`
    - `arith-add-wa-carry-zero`
    - `arith-add-wa-overflow-sign`
    - `arith-sub-wa-borrow-sign`
    - `arith-sub-wa-overflow`
    - `arith-adc-wa-carry-in`
    - `arith-sbc-wa-borrow-in`
    - `arith-add-wa-half-carry`
    - `arith-sub-wa-half-borrow`
    - `arith-add-w-carry-zero`
    - `arith-or-a-sign`
    - `arith-add-xwa-carry-zero`
    - `arith-sub-xwa-overflow`
    - `arith-adc-w-carry-in`
    - `arith-sbc-w-borrow-in`
    - `arith-adc-xwa-carry-in`
    - `arith-sbc-xwa-borrow-in`
    - `arith-add-w-half-carry`
    - `arith-sub-w-half-borrow`
    - `arith-add-xwa-half-carry`
    - `arith-sub-xwa-half-borrow`
    - `arith-cp-w-zero-no-writeback`
    - `arith-cp-xwa-zero-no-writeback`
    - `arith-cp-w-borrow-sign-no-writeback`
    - `arith-cp-w-overflow-no-writeback`
    - `arith-cp-xwa-borrow-sign-no-writeback`
    - `arith-cp-xwa-overflow-no-writeback`
    - `shift-rlc-w-carry-sign`
    - `shift-sra-w-carry-zero`
    - `shift-rrc-xwa-carry-sign`
    - `shift-srl-xwa-carry-zero`
    - `bitops-res-set-abs16-builtin`
    - `bitops-set-res-abs16-overlay`
    - `memory-ld-abs16-imm8-overlay`
    - `memory-ld-abs16-a-overlay`
    - `memory-ldw-abs24-imm16-overlay`
    - `memory-ld-abs16-xwa-overlay`
    - `stack-push-pop-wa-roundtrip`
    - `stack-push-pop-xiz-roundtrip`
    - `stack-link-unlk-xwa-roundtrip`
    - `stack-link-unlk-xbc-positive-frame`
    - `stack-link-xiy-large-frame-silicon-broken`
    - `stack-call-ret`
    - `control-jr-z`

### 9.3 Performance regression

Doit couvrir:
- mesures de cadence emulee
- budget frame
- frames manquees
- comparaison entre builds

Acceptation minimale:
- detecter qu'une build est plus lente ou plus rapide de facon mesurable

## 10. Engine integration

Statut prototype actuel:
- `partial`

Prototype courant:
- contrat d'integration v1 formalise dans
  `specs/ENGINE_INTEGRATION_CONTRACT.md`
- premier point d'entree bridge cote emulateur:
  - `ngpc_emu.py engine-bridge <request.json>`
  - reponse JSON structuree sur `stdout`
  - actions headless utiles deja branchees:
    - `smoke-run`
    - `capture-eventlog`
    - `capture-savestate`
- mode prefere pose:
  - `controlled-standalone` d'abord
  - `embedded` plus tard
- migration attendue documentee:
  - sortie progressive de `run/emulator_path`
  - remplacement futur du smoke-run externe de `NgpCraft_engine`

Gaps ouverts:
- `NgpCraft_engine` lance encore un emulateur tiers dans le workflow reel
- `run` / `debug` / `profile` bridge restent en fallback `partial` tant que la GUI/debugger standalone n'est pas cablee
- pas encore de deep links symbole/asset ni de debugger GUI branche

### 10.1 Run integration

Doit couvrir:
- launch direct depuis `NgpCraft_engine`
- ROM la plus recente auto-detectee
- plus de dependance obligatoire a `run/emulator_path`

### 10.2 Debug integration

Doit couvrir:
- ouverture sur le build courant
- chargement des symboles du projet
- acces rapide aux captures, traces, profiler

### 10.3 Asset-aware tools

Doit couvrir a terme:
- liens depuis palette/tilemap/scene vers les vues debugger utiles
- inspection VRAM/OAM/palettes contextualisee

### 10.4 Save integration

Doit couvrir:
- gestion coherente des saves pour les builds lances depuis `NgpCraft_engine`
- nettoyage minimal des chemins et emplacements
- pas de perte de sauvegarde lors des rebuilds normaux

## 11. Standalone parity

Le standalone ne doit pas etre une coquille vide.
Il doit garder:
- chargement ROM
- debug complet
- profiler
- captures
- replay
- headless associe

L'integration engine peut ajouter:
- deep links projet
- menus contextuels
- chemins automatiques

Mais ne doit pas devenir le seul endroit ou les features critiques existent.

## 12. Release gates par famille de features

Une famille de features ne passe `release-ready` que si:
- doc courte presente
- test ou scenario de validation present
- au moins une ROM du corpus valide le cas
- standalone verifie
- integration engine verifiee si applicable
- mode headless verifie si pertinent
- les cas materiels defectueux associes sont explicitement traites ou notes comme gaps ouverts
- les ecarts de slowdown connus sont explicites ou testes quand la feature touche au timing

## 13. Premier lot "full et utile"

Le premier lot a viser pour deja battre nettement l'existant sur NGPC:

1. CPU + trace + step fiables
2. `.map` loader + disasm live + breakpoints symbole
3. watchpoints RAM/VRAM/IO
4. viewers VRAM/OAM/palettes/tilemaps
5. timeline IRQ/DMA/HBlank/VBlank
6. profiler frame + symbole
7. replay + savestate
8. trace diff et frame diff
9. integration `NgpCraft_engine`
10. CLI/headless pour regression

Le support des saves persistantes doit etre traite tot des qu'une ROM de reference en a besoin.

Si ces 10 points marchent vraiment, le projet sera deja dans une autre categorie que les emulateurs NGPC "pas fous" cote debug.
