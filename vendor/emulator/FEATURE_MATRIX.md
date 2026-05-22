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
