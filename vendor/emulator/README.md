# NgpCraft Emulator

Emulateur + debugger NGPC moderne pour l'ecosysteme NgpCraft.

Objectif produit:
- un seul installateur
- zero PATH a regler
- creation -> compilation -> execution -> debug dans le meme outil
- mode standalone, mode integre a `NgpCraft_engine`, et mode headless pour CI

Contrainte produit non negociable:
- toute fonctionnalite majeure doit exister en version standalone
- le meme coeur doit aussi etre integrable proprement dans `NgpCraft_engine`
- l'integration engine ne doit pas creer un fork fonctionnel du standalone

Le projet vise deux usages en meme temps:
- remplacer le "lanceur d'emulateur externe" actuel de `NgpCraft_engine`
- fournir un vrai debugger symbol-aware, utile pour le moteur, la toolchain et le hardware bring-up

Formes de livraison attendues:
- application standalone utilisable seule
- coeur embarquable dans `NgpCraft_engine`
- mode headless/CLI pour automatisation, CI et tests

Exigence de qualite:
- pas de "feature vitrine" a moitie cablee
- une feature marquee comme supportee doit etre reellement exploitable sur NGPC
- le projet vise un niveau de finition superieur aux integrations NGPC generiques des suites multi-systemes
- les opcodes casses, comportements non documentes et bugs silicium connus doivent etre reproduits si le hardware reel les manifeste
- quand le hardware reel plante, l'emulateur doit planter aussi, mais avec un retour de diagnostic utile
- si un jeu tombe a 20 fps sur hardware, le mode de reference doit reproduire ce slowdown au lieu de le lisser artificiellement
- les saves in-game persistantes doivent etre gerees serieusement, pas juste les save states

Principe d'architecture:
- `ngpc_emu_core` : coeur natif, deterministe, sans UI
- `ngpc_emu_frontend` : UI debugger / player
- `ngpc_emu_headless` : mode batch, trace, capture, regression
- `ngpc_emu_bridge` : raccord avec `NgpCraft_engine`
- le sous-systeme audio devra rester assez propre et modulaire pour etre reintegre hors de l'emulateur, notamment comme futur remplacement du core NeoPop encore utilise par l'outil son

Choix technique actuel:
- le vrai coeur final est vise en `C++`
- le bootstrap, les specs executablees, les outils et certains prototypes peuvent vivre en `Python`
- le prototype Python actuel n'est pas le coeur final, il prepare le terrain pour le coeur `C++`

Etat actuel (2026-05-20, **389 tests verts, 0 skipped**) :

Doctrine + roadmap :
- roadmap detaillee dans `ROADMAP.md`
- politique de fidelite materielle dans `HARDWARE_COMPAT_POLICY.md`
- politique de fidelite timing/perf dans `PERF_TIMING_POLICY.md`
- politique de gestion des sauvegardes dans `SAVE_POLICY.md`
- contexte de reprise session dans `AGENT_CONTEXT.md`
- index local des sources dans `DOC_SOURCES_INDEX.md`
- devlog local dans `DEVLOG.md`
- manuel anglais evolutif dans `USER_MANUAL_EN.md`

Strategie BIOS / flash / saves (HLE-only, **jamais de dump SNK distribue**) :
- Master index cross-projets : `../Doc de dev/Final/BIOS_FLASH_SAVES_STRATEGY.md`
- Spec HLE detaillee : `specs/BIOS_HLE.md` (table SWI + workflow gap-filling)
- Politique saves : `SAVE_POLICY.md` (§9 references pour implementation)
- Lib `ngpc_flash` MIT existante (toolchain) sert de reference pour le protocole AMD flash + pattern append-only

Format specs lockes (chaque format en JSON strict, rejet implicit upgrade) :
- savestate v2 (`specs/SAVESTATE.md`)
- event log v2 (`specs/EVENT_LOG.md`, ajoute `memory_reads` aux events)
- watchpoints v3 (`specs/WATCHPOINTS.md`, kind=write/read/access + byte-value filter)
- breakpoints v1 (`specs/BREAKPOINTS.md`)
- symbols .map v1 (`specs/SYMBOLS.md`)
- quirks v3 + matcher v4 (`specs/QUIRKS.md`, `core/quirks_db.json` `2026-05-20.v4`)
- contrat d'integration engine (`specs/ENGINE_INTEGRATION_CONTRACT.md`)
- K2GE inspecteurs (palette / OAM / tilemap / tile pixels, `specs/K2GE_*.md`)

CPU + memoire (M1 + M1d livres) :
- SR Phase 1+2 partielle : 6 flags TLCS-900/H complets (S/Z/V/H/C/N), `iff_level` 3-bit mask, `rfp` 2-bit bank pointer, helpers `encode_sr_from_state`/`decode_sr_to_fields` (layout `T900_DENSE_REF.md` §31). `ei n` / `di` cables sur `iff_level`. PUSH SR / POP SR (0x02/0x03) opcodes consomment encode/decode SR end-to-end.
- M1d Phase 1 : pre-init cold-start 32 256 B de RAM/VRAM on-chip a `0x00` (Work RAM 0x4000..0x6FFF, system page, Z80 RAM, K2GE, SCR1/2, CHAR_RAM). CPU I/O page laissee `unbacked` intentionnellement.
- Frontier StarGunner stable : **25 072 instructions honnetes** depuis bootstrap → silicon-broken `D8 89 ld XBC, XWA` a 0x20D180.

Debugger (P0 ROADMAP §8) — **8/9 livres, seul `screenshots` depend M2 Phase 1** :
- step/run/pause/reset, breakpoints adresse+symbole, watchpoints memoire/IO (kind=write/read/access + byte-value filter), affichage registres CPU, disasm live (single via `decode-next` + range delegate a `NgpCraft_Disasm`), chargement `.map`, inspecteur memoire brut (`memory-dump`), savestates (v2), inspecteurs VRAM/OAM/palettes (palette + OAM + tilemap statiques + tile pixels rendus en ASCII grayscale 4-niveaux).

Visuel (M2 Phase 0.5, pass 19) :
- **Premier rendu graphique** via `tile-view <rom> <tile-id>` qui decode CHAR_RAM 2bpp + rend 8×8 en ASCII grayscale. `--plane <sprite|scr1|scr2> --palette N` colorise chaque pixel via la palette K2GE. Sert de rasterizer kernel reutilisable pour M2 Phase 1 framebuffer.

Prototype actuel:
- `ngpc_emu.py info <rom>` : lit le header minimal d'une ROM `.ngp/.ngc`
- `ngpc_emu.py reset-info <rom>` : construit l'etat machine bootstrap minimal
- `ngpc_emu.py addr-info <rom> <address>` : sonde une adresse dans l'espace memoire minimal
- `ngpc_emu.py cpu-info <rom>` : affiche le premier conteneur d'etat CPU
- `ngpc_emu.py peek <rom> <address>` : lit des octets via le bus read-only minimal
- `ngpc_emu.py fetch-next <rom>` : lit une fenetre brute a partir du `PC` bootstrap
- `ngpc_emu.py decode-next <rom>` : decode une instruction dans le sous-ensemble TLCS-900 minimal actuellement supporte
- `ngpc_emu.py execute-next <rom>` : execute une instruction du premier sous-ensemble reel actuellement representable par l'etat CPU
- `ngpc_emu.py step-exec <rom>` : execute exactement une instruction reelle avec support explicite de reprise `--seed-from` et persistance `--save-state`
- `ngpc_emu.py step-exec <rom> --seed-checkpoint <name> --save-checkpoint <name>` : meme workflow mais via checkpoints nommes au lieu de chemins JSON bruts
- `ngpc_emu.py step-exec <rom> --seed-session <name> --save-session <name>` : meme workflow mais avec un frontier courant persistant gere comme session nommee
- `ngpc_emu.py run-steps <rom>` : execute un petit nombre d'instructions en conservant l'etat CPU et l'overlay memoire writable entre les pas
- `ngpc_emu.py trace-exec <rom> --seed-from <state.json> --save-state <next.json>` : capture un petit bloc d'execution reelle puis persiste directement le frontier final
- `ngpc_emu.py run-steps <rom> --seed-from <state.json> --save-state <next.json>` : reprend un petit run depuis un savestate et persiste directement l'etat final capture
- `ngpc_emu.py checkpoint save|list|load|delete ...` : couche de checkpoints nommes au-dessus des savestates v1 stockes sous `.ngpc_emu/checkpoints/`
- `ngpc_emu.py session save|list|load|delete ...` : couche de sessions nommees au-dessus d'un checkpoint courant gere automatiquement sous `.ngpc_emu/sessions/`
- `ngpc_emu.py session snapshot save|list|load|restore|delete ...` : snapshots legers d'une session pour garder quelques frontiers utiles sans quitter le workflow nomme
- `ngpc_emu.py savestate save <rom> <output.json> [--run-until <target_pc>] [--seed-reg ...] [--note ...]` : capture un snapshot machine-state v1
- `ngpc_emu.py savestate load <input.json> [--rom <rom>]` : relit un savestate v1, valide format/version et (si --rom) verifie le hash ROM
- `ngpc_emu.py run-until-exec <rom> <target_pc> --seed-from <state.json>` : reprend l'execution depuis un savestate (CPU + overlay) au lieu du bootstrap
- `ngpc_emu.py run-until-exec <rom> <target_pc> --save-state <output.json>` : persiste directement l'etat final d'un run-until comme nouveau savestate v1
- `ngpc_emu.py run-until-exec <rom> <target_pc> --auto-tick-addr <addr> --auto-tick-period <n>` : mode diagnostic non-reference qui incremente un compteur writable pour laisser sortir des waits type `_ngpc_vsync`
- `ngpc_emu.py eventlog capture <rom> <output.json> [--run-until <target_pc>] [--seed-from <state.json> | --seed-checkpoint <name> | --seed-session <name>]` : capture un event log v1 stable pour diff/CI/regression
- `ngpc_emu.py eventlog inspect <input.json> [--rom <rom>] [--limit <N>]` : recharge un event log v1, verifie optionnellement le hash ROM et affiche son resume
- `ngpc_emu.py eventlog diff <left.json> <right.json>` : signale la premiere divergence entre deux logs captures sur le meme hash ROM
- `ngpc_emu.py eventlog check <rom> <golden.json> [...]` : capture un run frais puis le compare immediatement a un golden event-log ; code de sortie `0` si identique, `1` si divergence, avec `--save-current` pour archiver le log courant
- `ngpc_emu.py eventlog golden-save|golden-load|golden-list|golden-delete|golden-check ...` : registre nomme de goldens event-log sous `.ngpc_emu/goldens/`, pour sortir du bricolage de chemins JSON en regression/CI
- premiere tranche M1c livree dans les tests : 3 micro-ROMs CPU synthetiques stables (arithmetique, pile, controle de flux), chacune validee par `eventlog golden-save/golden-check`
- `ngpc_emu.py engine-bridge <request.json>` : execute une requete bridge `NgpCraft_engine` et repond en JSON structure sur `stdout`
- `ngpc_emu.py trace-preview <rom>` : affiche une premiere trace lineaire statique basee sur le decodeur courant
- `ngpc_emu.py step-preview <rom>` : affiche une premiere prevision statique de `step into` basee sur les metadonnees du decodeur
- `ngpc_emu.py next-preview <rom>` : affiche une premiere prevision statique de `step over` / `next`
- `ngpc_emu.py run-until-preview <rom> <target>` : affiche une premiere prevision statique de `run until` en chainant `step into` ou `next`

Debugger M4 P0 (passes 11-14) :
- `ngpc_emu.py memory-dump <rom> <addr> [--count N] [--width W] [--seed-from state.json] [--json]` : hexdump-style multi-row inspector avec ASCII column ; `--seed-from` overlay savestate writable cells
- `ngpc_emu.py registers <rom> [--seed-from state.json] [--json]` : vue rich des 8 R32 (décomposition R16/R8 — XWA → WA → W/A), PC, SR raw, IFF level, RFP, 6 flags
- `ngpc_emu.py watchpoint add <rom> <addr> [--kind write|read|access] [--size N] [--label L] [--value BYTE] [--json]` : registre per-ROM `.ngpc_emu/watchpoints/`. Workflow "find opcode writing V to A" = `add --value V` + `check`.
- `ngpc_emu.py watchpoint list|remove|clear|check <rom> [...]`
- `ngpc_emu.py breakpoint add <rom> <addr> [--label L] [--json]` ou `add-symbol <rom> <name> --map <map>` : registre per-ROM `.ngpc_emu/breakpoints/`. Symbol-name shortcut résout via `.map`, stocke address pure.
- `ngpc_emu.py breakpoint list|remove|clear|check <rom> [...]`

M2 Phase 0 inspecteurs (passes 16-18) — lecture statique de l'état VRAM/OAM/palette :
- `ngpc_emu.py palette-info <rom> [--kind all|sprite|scr1|scr2|background|window] [--seed-from state.json] [--json]` : décode la palette RAM `0x8200..0x83FF` en couleurs 0BGR 12-bit (5 plans : sprite/scr1/scr2/background/window, 16 palettes × 4 couleurs).
- `ngpc_emu.py oam-info <rom> [--visible-only] [--seed-from state.json] [--json]` : 64 sprites × 4 bytes `0x8800..0x88FF` + CP.C `0x8C00..0x8C3F`. Decode tile (9-bit), flip, P.C plane bit, PR.C priority (0=hidden, 1=behind-scr, 2=middle, 3=front), chain bits, palette code.
- `ngpc_emu.py tilemap-info <rom> [--plane scr1|scr2] [--non-empty] [--list] [--seed-from state.json] [--json]` : SCR1 `0x9000` / SCR2 `0x9800`, 32×32 tiles × 2B. Vue par défaut = grille ASCII compacte (`.` empty, `0-9 a-z A-Z +` compress tile #).

M2 Phase 0.5 — premier rendu visuel (pass 19) :
- `ngpc_emu.py tile-view <rom> <tile-id> [--plane sprite|scr1|scr2 --palette N] [--seed-from state.json] [--json]` : rend un tile 8×8 CHAR_RAM en ASCII 4-niveaux grayscale (` ░▒█`). `--plane + --palette` résout chaque pixel via la palette K2GE (hex_rgb24 par pixel dans JSON). Premier vrai rendu graphique de l'émulateur + rasterizer kernel pour la future Phase 1 framebuffer.
- parseur dans `core/rom.py`
- espace d'adresses minimal dans `core/bus.py`
- lecture memoire read-only minimale dans `core/memory.py`
- conteneur CPU minimal dans `core/cpu.py`
- etat machine bootstrap dans `core/machine.py`
- helper de fetch minimal dans `core/fetch.py`
- helper de decode minimal dans `core/decode.py`
- helper de premier executeur minimal dans `core/execute.py`
- premier registre local de quirks hardware dans `core/quirks.py`
- helper de premier run stateful minimal dans `core/run_steps.py`
- helper de premiere trace d'execution reelle dans `core/trace_exec.py`
- helper de premier event log stable dans `core/event_log.py`
- helper de premier bridge engine structure dans `core/engine_bridge.py`
- test unitaire minimal dans `tests/test_rom.py`

Le decodeur minimal couvre maintenant aussi:
- premiers opcodes prefixes registre/ALU utiles sur le bootstrap reel
- premiers acces indexes `(r32+d8)`
- premiers warnings de quirk CPU connus, sans modifier le resultat decode
- correction de largeur pour la famille ALU `D8..DF`, alignee sur la reference locale
- premieres metadonnees de controle de flux: type, cible directe et `falls through`

La trace minimale actuelle couvre maintenant aussi:
- une marche sequentielle sur `N` instructions a partir d'une adresse ou du `PC` bootstrap
- un format de record simple avec bytes, asm, warning et raison d'arret
- un mode strictement statique qui ne pretend pas suivre le vrai flux d'execution
- une option d'arret sur controle de flux pour obtenir des previews de bloc plus lisibles
- une premiere variante runtime `trace-exec`, distincte des previews statiques

Le stepping minimal actuel couvre maintenant aussi:
- un premier `step into` statique
- un premier `step over` / `next` statique
- un premier `run until` statique borne, avec mode `over` ou `into`
- une cible preview resolue pour les cas directs simples
- un resultat explicitement non resolu pour les branches conditionnelles, retours et cas runtime-dependants
- une detection de cycle et une limite de pas pour eviter de pretendre suivre un flux non justifiable

L'execution minimale actuelle couvre maintenant aussi:
- un premier `execute-next` avec vraie mutation d'etat CPU
- un premier `step-exec` dedie pour une seule instruction reelle, utilisable proprement en workflow savestate -> step -> savestate
- `NOP`, sauts directs inconditionnels et chargements immediats representables
- premiers `inc` / `dec` prefixes sur registres quand la vue source est representable par le modele CPU courant
- un premier modele writable minimal pour la pile courante
- `pushw`, `push`, `pop`, `call`, `ret` et `retd` quand le pointeur de pile est connu et que l'effet reste representable
- un seed manuel des 8 registres 32-bit via `--seed-reg`, avec `--seed-xsp` conserve comme raccourci pratique
- un premier `run-steps` borne qui conserve `CPU` et overlay memoire entre instructions dans une seule invocation
- `run-steps` peut maintenant aussi reprendre depuis un savestate (`--seed-from`) et sauvegarder directement son etat final (`--save-state`)
- `trace-exec` peut maintenant aussi reprendre depuis un savestate (`--seed-from`) et sauvegarder directement le frontier final (`--save-state`)
- `run-until-exec` peut maintenant sauvegarder directement son etat final en savestate v1 (`--save-state`)
- premiere couche de checkpoints nommes:
  - `checkpoint save/list/load/delete`
  - `step-exec`, `run-steps`, `trace-exec`, `run-until-exec` acceptent maintenant aussi `--seed-checkpoint` / `--save-checkpoint`
- premiere couche de sessions nommees:
  - `session save/list/load/delete`
  - `step-exec`, `run-steps`, `trace-exec`, `run-until-exec` acceptent maintenant aussi `--seed-session` / `--save-session`
  - `session snapshot save/list/load/restore/delete` ajoute un petit historique manuel au-dessus du frontier courant
- `eventlog capture` accepte maintenant aussi `--seed-checkpoint` pour rester compatible avec les workflows nommes de replay/diff/regression
- `eventlog capture` accepte maintenant aussi `--seed-session` pour rejouer/capturer directement depuis le frontier courant d'une session
- `eventlog check` ajoute une premiere brique golden-trace/CI au-dessus du format stable:
  - reprend les memes seeds/options de capture que `eventlog capture`
  - compare directement contre un golden existant
  - renvoie `0` si aucun ecart, `1` sinon
  - peut sauver le log courant via `--save-current` pour inspection post-echec
- le registre nomme de goldens event-log existe maintenant aussi:
  - `eventlog golden-save <rom> <name> [...]`
  - `eventlog golden-list <rom>`
  - `eventlog golden-load <rom> <name>`
  - `eventlog golden-delete <rom> <name>`
  - `eventlog golden-check <rom> <name> [...]`
  - il repose sur le meme format stable `eventlog v1`, pas sur un format parallele
- premiere tranche du corpus micro-ROMs CPU stable:
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
  - ces cas vivent aujourd'hui dans la suite de tests et passent via le workflow golden nomme
  - le volet arithmetique couvre maintenant une premiere tranche stable de `Z/S/C/V/H` sur variantes `byte/word/long`, y compris `ADC/SBC` avec carry-in/borrow-in seedes
  - le sous-volet `CP` couvre aussi maintenant le non-writeback, plus des premiers cas `borrow/sign/overflow` sur `byte` et `long`
  - un premier sous-corpus `shift/rotate` existe maintenant aussi sur `byte` et `long`
  - un premier sous-corpus `res/set abs16` existe aussi sur la memoire writable/systeme
  - un premier sous-corpus `ld/ldw` store existe aussi sur `abs16/abs24`
  - un premier sous-corpus `push/pop` explicite existe aussi sur pile writable
  - un premier sous-corpus `link/unlk` explicite existe aussi pour les frames de pile
  - ce sous-corpus inclut maintenant un stop quirk honnete sur `link XIY, N>=5`
- le mode diagnostic non-reference `auto-tick` est maintenant coherent sur tout le workflow `run-until` :
  - `run-until-exec`
  - `eventlog capture`
  - `savestate save --run-until`
  - `checkpoint save --run-until`
  - `session save --run-until`
  - utile pour sortir proprement d'une boucle d'attente sur compteur writable sans annoncer a tort que les IRQ/VBlank sont modelises
- `checkpoint list` masque maintenant les checkpoints internes geres par les sessions pour eviter de melanger frontiers utilisateur et artefacts de plomberie
- premiers `lda R32, (abs24)`, `ld (r32+d8), R32` et copies `ld R32, R32` sur le chemin bootstrap officiel outille par `NgpCraft_Disasm`
- premiers `cp` registre-registre et `cp (r32+d8), R32` avec mutation du sous-ensemble de flags modele
- premiers `jr` / `jrl` conditionnels quand les flags requis sont connus
- premier `ld R32, (r32+d8)` alimente par l'overlay writable ou le bus
- premiere tranche `abs16` byte-memory utile sur la ROM stable:
  - `cp (abs16), imm8`
- premiere tranche post-increment byte-memory utile sur la ROM stable:
  - `ld R8, (r32+)`
  - `ld (r32+), R8`
  - `ld (r32+), imm8`
- le bloc de sortie bootstrap decode maintenant aussi `pop XIZ` a `0x0020D0AC`
- premier backing lisible minimal pour la memoire systeme officielle:
  - `0x6F86`
  - `0x6F91`
- premiere lecture honnete de la flash cart non programmee dans la fenetre 2 MB:
  - `0x200000..0x3FFFFF` au-dela de la taille fichier lit maintenant `0xFF`
  - ce fallback couvre la zone de save `0x3FA000..0x3FBFFF` du smoke ROM stable
- premiere tranche de stores absolus utiles sur la ROM officielle:
  - `ld (abs24), R8`
  - `ld (abs24), imm8`
  - `ld (abs16), R32`
  - `ld (abs16), imm8`
  - `res bit, (abs16)`
  - `set bit, (abs16)`
- sur `main.ngc`, `run-steps --address 0x20D0D8 --seed-xsp 0x4100 --seed-reg XWA=0x11223344 --count 64` pousse maintenant jusqu'a `0x0020D16A`
- backing lisible K2GE `0x8000..0x8FFF` (power-on default 0x00) — permet les RMW `res`/`set` sur les registres video:
  - `res 7, (0x8030)`, `ld (0x8020), 0x00`, `ld (0x8021), 0x00`, `res 7, (0x8012)` tous executes
- compact small-immediate register load `ld r, #3` (catalog: C8+zz+r : A8+#3):
  - `ld XWA, 0` (D8 A8) execute; premier cas observe sur la ROM stable
- `exts r` / `extz r` executes (sign/zero-extend 16→32 bits)
- ALU-immediate (`add/sub/and/xor/or/cp r, #N`) executes via prefixed family
- `(r32)` register-indirect stores executes:
  - `ld (r32), imm8` (Bx 00 xx)
  - `ldw (r32), imm16` (Bx 02 xx xx)
- `ei 0` / `di` executent: IFF tracked dans NgpcCpuState, interrupt dispatch non modelise
- les formes `D0..D7` confirmées silicon-broken s'arretent maintenant avec un frontier explicite:
  - `silicon-broken` dans `execute-next`
  - `stopped-on-silicon-broken` dans `run-until-exec` et `trace-exec`
- les formes immediates documentees comme sures restent executables
- le registre local de quirks repose maintenant sur un fichier versionne:
  - `core/quirks_db.json` (`2026-04-22.v3`)
  - 3 entrees : `cpu.d0_d7_non_immediate`, `cpu.d8_df_register_to_register`, `cpu.link_xiy_large_frame`
  - chaque entree porte une liste non vide `sources` (attribution par-rule)
  - les payloads CLI / JSON exposent cette attribution aux cotes de l'ID et de la version
  - la famille D8..DF `r+r` ALU (sauf CP F0..F7 et les immediates) est maintenant
    egalement classee `silicon-broken`, conformement a USER_MANUAL_EN.md §12.1
- sur `main.ngc`, `run-until-exec 0x00220000 --seed-xsp 0x6C00 --seed-reg XIZ=0` atteint maintenant **25 072** instructions honnetes (regression volontaire depuis 27 556 suite a l'ajout du rule D8..DF `r+r`, policy-correct)
- le frontier honnete courant devient:
  - `0x0020D180 D8 89` (`stopped-on-silicon-broken`) - `ld XBC, XWA` silicon-broken D8..DF `r+r` ALU (USER_MANUAL_EN.md §12.1)
- frontiers historiques:
  - `0x0020CD4D D7 FA` — `rl A, SP`, frontier v2 avant le rule D8..DF `r+r` (2026-04-22)
  - `0x0020E27F DB 7E` — `scc NZ, XHL`, execute honnetement depuis la session SCC (2026-04-20)
  - `0x0020D199 ret` — `stack-data-unavailable`, adresse de retour non presente dans l'overlay seede
- une limite honnete quand l'etat CPU ne permet pas encore de representer un write 8/16-bit
- un refus explicite des alias `SP`/`XSP`, des effets memoire/IO generaux, de la majorite des flags/SR et des cas encore non modelises

Sources a exploiter des le depart:
- `../NgpCraft_toolchain`
- `../NgpCraft_Disasm`
- `../NgpCraft_engine`
- `../NgpCraft_gb2ngp`
- `../Doc de dev/Final/Doc final uniformise eng`
- `../../01_SDK`
