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

## Jalon : le modèle matériel repose désormais sur les docs constructeur (2026-07-10)

Passes 180-186. **1314 tests verts.** Le sous-système périphérique/BIOS est passé
d'un modèle bâti sur des inférences à un modèle où **chaque constante est citée
d'un document Toshiba ou SNK** (voir `DOC_SOURCES_INDEX.md` § 0).

**Livré :**
- **BIOS `swi 1` (SYSTEM_CALL)** — dispatch sur RW3, tous les vecteurs
  déterministes (SHUTDOWN, CLOCKGEARSET, INTLVSET, RTCGET, FLASHWRITE,
  SYSFONTSET, SYS_SUCCESS, comms sans peer). → `specs/BIOS_HLE.md`
- **Sauvegardes flash** — les **deux** chemins : BIOS-médié *et* direct (séquence
  AMD + `/WE`, celui qu'utilise la lib maison du projet). Non-volatile.
  → `specs/FLASH.md`
- **SYSFONTSET** — la **vraie font SNK**, lue dans le BIOS attaché. Rien
  d'embarqué : zéro souci de licence *et* pixel-exact.
- **Contrôleur d'interruptions multi-source** — table de vecteurs HW
  (`0xFFFF00`) → handler BIOS, puis chaînage vers le hook RAM (`0x6FB8 + i*4`).
  → `specs/FRAME_TIMING.md`
- **A/D converter (la jauge batterie)** et **timers 8 bits 0..3**.
  → `specs/ADC.md`, `specs/TIMERS.md`

**Trois erreurs de fidélité corrigées** par la lecture des docs — dont deux vrais
bugs : la règle de masque IRQ était *off-by-one* (`L > IFF` au lieu de
`L >= IFF`), le masque post-acceptation était faux (`IFF = L` au lieu de
`min(L+1, 7)`), et la page I/O comme les registres K2GE ne resettent **pas** à
zéro. Détail et rétractation : `HARDWARE_COMPAT_POLICY.md`.

**Non-régression de fidélité** vérifiée contre l'oracle (`oracle_tools/cosim_diff.py`) :
Big Bang / Cotton / Crush Roller = **0 divergence sur 3 000 pas**.

**Vitesse** : deux optimisations *behaviour-neutral* (cache de la fetch view,
mémoïsation de `probe()`) → **1 123 → 1 706 instr/s (×1,5)**. Le coût restant est
structurel et documenté comme cahier des charges du futur **cœur C++** :
`PERF_TIMING_POLICY.md` § 10.

## Jalon : première image de cartouche (2026-07-08)

`Engine_test_project/menu_test_project` s'exécute désormais de bout en bout (init
complète -> boucle principale, 5 000 000+ instructions sans honest-stop) et **rend son
écran de menu** : le splash "NGPC" (rouge) + "craft" (jaune) sur fond noir (SCR1 tilemap
+ glyphes CHAR + palette). C'est la **première frame de cartouche** produite par
l'émulateur, et elle est obtenue **100 % en fidélité hardware** : chaque déblocage a été
tranché sur la vraie NGPC de Wilfried (ROMs de test flashées) ou contre la source
ground-truth ngdis, honest-stop conservé pour l'état réellement indisponible.

Le déclencheur principal a été le **modèle open-bus** : `hw_test_openbus` (flashé sur
vrai HW) a prouvé que le TLCS-900/H n'a pas de bus-fault -> une lecture d'adresse
non-mappée renvoie `0x0000`, une écriture est ignorée, sans hang. L'émulateur modélise
maintenant ce comportement mesuré au lieu de honest-stopper, ce qui laisse le runtime de
la cartouche franchir un déréférencement de pointeur vers l'espace non-mappé et
continuer jusqu'au rendu. Cf. DEVLOG pass 168 pour la chaîne complète (open-bus,
`mul/div/muls/divs, imm16`, `pushw (r32+d16)`, `inc/dec byte (r32+d8)`).

NB perf : le core CPython est un **prototype/oracle de référence** (fidélité HW =
la moat du projet), pas le moteur temps réel. ~1000 instr/s aujourd'hui (interprétation
pure + état immuable copié par instruction) vs ~1 M instr/s nécessaires pour du 60 fps
-> le cœur natif visé (`ngpc_emu_core`, état mutable) fera le temps réel ; le Python
reste l'oracle qui valide le comportement opcode par opcode.

Choix technique actuel:
- le vrai coeur final est vise en `C++`
- le bootstrap, les specs executablees, les outils et certains prototypes peuvent vivre en `Python`
- le prototype Python actuel n'est pas le coeur final, il prepare le terrain pour le coeur `C++`

Etat actuel (2026-07-02, **1116 tests verts, 0 skipped**) :

- derniers decode/execute follow-ups :
  - `push/pop r` executes maintenant pour les formes safe du
    sous-ensemble prefixed register, y compris le byte family `C8..CF`
    et les formes long `D8..DF` / `E8..EF` qui passent deja le filtre
    quirk
  - `push/pop` executes maintenant aussi pour les byte-slices `C7`,
    avec lecture/ecriture sur la stack writable en largeur 1 byte
  - `cycles_consumed` utilise maintenant aussi les valeurs Toshiba pour
    `push/pop r` prefixed et pour `C7 <reg> 04/05`
  - `cpl` / `neg` executes maintenant pour les formes prefixed byte
    safe et pour les mirroirs `C7` current-bank byte-slice
  - `daa` executes maintenant pour les formes prefixed byte safe et
    pour le miroir `C7` current-bank byte-slice, avec blocage honnete
    si les flags d'entree `C/H/N` ne sont pas connus
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba `DAA r`
    (`4` cycles) pour les formes prefixed byte et pour `C7 <reg> 10`
  - `paa` executes maintenant pour les formes prefixed `word/long`
    definies (`D8..DF` / `E8..EF : 14`), avec stop honnete
    `silicon-undefined` sur les formes byte non definies
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba `PAA r`
    (`4` cycles) pour les formes `word/long` executees
  - `djnz` executes maintenant pour le sous-ensemble prefixed byte safe
    (`C8..CF : 1C : d8`), avec split branche prise / non prise
  - les formes prefixed long `djnz` non definies stoppent maintenant
    honnetement en `silicon-undefined`
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba `DJNZ`
    (`6` si branche prise, `4` sinon) pour ce sous-ensemble execute
  - `mirr` decode/execute maintenant comme cas special `D8..DF : 16`
    sur registres 16-bit (`WA..SP`), avec reversal des 16 bits et
    drapeaux inchanges
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba `MIRR r`
    (`3` cycles) pour ce cas special word-only
  - `bs1f` / `bs1b` decode/execute maintenant comme cas speciaux
    `D8..DF : 0E/0F` sur source 16-bit et destination fixe `A`
  - si la source vaut zero, `bs1f/bs1b` stoppent honnetement en
    `silicon-undefined` car la doc locale rend `A` indefini
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba
    `BS1F/BS1B` (`2` cycles)
  - `mula` decode/execute maintenant comme cas special `D8..DF : 19`
    sur destination 32-bit (`XWA..XSP`), avec lecture signee 16-bit
    depuis `(XDE)` et `(XHL)` puis decrement de `XHL`
  - le cas de recouvrement `mula XHL` suit l'ordre documente:
    somme ecrite dans `XHL`, puis `XHL -= 2`
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba
    `MULA rr` (`19` cycles)
  - `minc1/2/4` et `mdec1/2/4` decode/execute maintenant comme cas
    speciaux word-only `D8..DF : 38/39/3A/3C/3D/3E`
  - l'immediat desassemble reste la valeur encodee `# - step`, puis
    l'execution reconstruit la vraie fenetre modulo `#` et la valide
    comme puissance de deux documentee avant d'agir
  - `cycles_consumed` utilise maintenant aussi les lignes Toshiba
    `MINC*` (`5` cycles) et `MDEC*` (`4` cycles)
  - `andcf/orcf/xorcf/ldcf/stcf` sur registres prefixes byte
    (`C8..CF`) decode/execute maintenant, avec index immediat `#4`
    ou dynamique `A & 0x0F`
  - les formes byte hors plage (`bit >= 8`) restent honnetes:
    `stcf` ne change rien, les autres stoppent en `silicon-undefined`
  - les miroirs `C7` current-bank byte-slice de cette famille
    decode/execute maintenant avec la meme semantique
  - les formes registre long restent non definies, et les formes word
    `D0..D7` continuent a etre arretees par le garde-fou
    `silicon-broken` deja documente
  - `extz/exts` sur registre byte prefixe et `unlk/extz/exts` sur
    byte-slices `C7` ne tombent plus sur un stop generique:
    ils s'arretent maintenant honnetement en `silicon-undefined`
  - `ldc` prefixed decode maintenant les CR connus avec leurs noms
    symboliques locaux (`DMAS0/DMAD0/DMAC0/DMAM0/INTNEST`) au lieu de
    laisser seulement `CR_0xNN`
  - le fichier des control registers TLCS-900/H est maintenant modele
    dans l'etat CPU (`DMAS0..3`, `DMAD0..3`, `DMAC0..3`, `DMAM0..3`,
    `INTNEST`) avec valeurs inconnues tant qu'aucun chemin execute ne
    les initialise
  - `ldc` prefixed execute maintenant pour ce sous-ensemble:
    - ecritures et lectures reelles sur `DMAS/DMAD/DMAC/DMAM/INTNEST`
    - stop honnete `requires-known-control-register` si une lecture vise
      un CR encore inconnu
  - le miroir `C7` byte-slice de `ldc` execute maintenant aussi pour les
    CR byte (`DMAMn`) ; les cibles non-byte par ce chemin restent
    arretees honnetement en `silicon-undefined`
  - `INTNEST` suit maintenant aussi les chemins IRQ deja modeles:
    increment sur delivery si sa valeur etait connue, decrement sur
    `reti` si sa valeur etait connue
  - la couche BIOS hand-off de `EmulatorSession` initialise maintenant
    aussi `INTNEST=0` comme invariant de session UI ; le bootstrap brut
    garde toujours ce control register inconnu
  - savestate v5 persiste maintenant aussi ce fichier TLCS-900/H des
    control registers
  - `cpu-info` et `registers` exposent maintenant aussi ce fichier de
    control registers en sortie humaine et JSON
  - `--seed-reg` accepte maintenant aussi le sous-ensemble modele de
    control registers TLCS-900/H (`DMAS*`, `DMAD*`, `DMAC*`, `DMAM*`,
    `INTNEST`) dans les commandes CLI et via l'engine bridge
  - l'engine bridge accepte maintenant aussi `runtime.seed_presets` avec
    `bios-handoff-minimal`, pour reproduire le handoff minimal
    `XSP=0x6C00` + `INTNEST=0` sans recopier ces seeds a la main
  - `halt` execute maintenant dans un modele borne:
    `PC` avance a l'adresse sequentielle suivante, puis l'execution
    s'arrete explicitement en `cpu-halted` jusqu'a modelisation d'une
    vraie reprise par interruption
  - `rcf/scf/ccf/zcf` executes maintenant aussi:
    `cf` suit la semantique Toshiba, `n` est remis a `0`, et
    `ccf/zcf` gardent le `H` documente comme indetermine sous forme
    inconnue (`None`) au lieu d'inventer une valeur
  - les formes fixes `push/pop A` et `push/pop F` executes maintenant
    aussi, avec timings Toshiba `3/4` cycles et un encodage honnete du
    byte `F` depuis les six flags modeles
  - le premier sous-ensemble block-memory non-repeat execute maintenant
    aussi sur les formes word decodees:
    `LDI/LDD` avec paires implicites `(XDE+/-, XHL+/-)` ou
    `(XIX+/-, XIY+/-)`, et `CPI/CPD` avec accumulateur implicite
    `WA` sur `(R32+/-)`
  - `CPI/CPD` preservent honnetement `CF`, mettent `V` a `1` tant que
    `BC != 0` apres decrementation, et bloquent encore les cas alias
    `XBC` ou ordre de side effects non source
  - le sous-ensemble block-memory *repeat* execute maintenant aussi:
    `LDIR/LDDR` recopient `BC` items sur les memes paires implicites
    jusqu'a `BC == 0` (`H/N/V=0`, `S/Z/C` preserves), et `CPIR/CPDR`
    comparent `A/WA` jusqu'a un match (`Z=1`) ou `BC == 0`
    (`S/Z/H` du dernier compare, `V = BC != 0`, `N=1`, `CF` preserve).
    Le repeat est applique atomiquement: si un acces memoire manque avant
    le point d'arret honnete, l'instruction bloque
    (`runtime-memory-unavailable`) sans muter d'etat. `BC=0` au depart
    boucle sur le pass complet `0x10000` (ordre decrement-puis-test)
  - correction fidelite decodeur: `0x95 0x11` etait decode `ldirw
    (XDE+),(XHL+)`; la source autoritative ngdis selectionne la paire via
    `w = first & 7`, donc `0x95` (w=5) est `(XIX+),(XIY+)`
  - la famille `0xF3` ARI secondary-indexed **mode=1** `(r32+d16)` decode
    et execute maintenant tous les stores (immediate `imm8`/`imm16` +
    registre `R8`/`R16`/`R32`), plus juste `LDA`; miroir du mode=3
    `(r32+r16)`. `EA = base + signed(d16)`, bloque honnetement sur base ou
    source inconnue. Debloque ~6.5k instructions de plus au boot de
    `a_test_battle.ngc` (arret honnete ensuite sur `ld XBC,XWA` silicon-broken)
  - `CALL [cc,] (r32)` register-indirect decode+execute pour toute la famille
    `0xB0..0xB7` (op `0xE0..0xEF`), conditionnel inclus; avant seul `B4 E8`
    = `call (XIX)` etait gere. Taken => push return + `PC = r32`; conditionnel
    faux => fall-through. Miroir de `JP (r32)`
  - `0xC3`/`0xD3`/`0xE3` ARI secondary-indexed **mode=1** `(r32+d16)` decode
    et execute maintenant les loads `ld R8/R16/R32, (r32+d16)` + `cp
    (r32+d16), imm8` (miroir lecture du pass 138, avant seul mode=3
    `(r32+r16)`)
  - `cp R8/R16/R32, (mem)` (op `0xF0..0xF7`) secondary-indexed decode+execute
    pour les deux modes (`(r32+d16)` et `(r32+r16)`) : compare `R - mem`,
    pose les flags de soustraction, n'ecrit rien
  - `inc/dec #n, (mem)` (op `0x60..0x6F`, `n=0->8`) secondary-indexed RMW
    decode+execute pour les deux modes : lit, applique `+/- n`, reecrit ;
    pose `S/Z/V/H`+`N`, preserve la retenue. Ferme entierement la famille
    secondary-indexed mode=1 sur les prefixes `C3`/`D3`/`E3`
  - `ld R32, (r32)` long register-indirect (`0xA0..0xA7` op `0x20..0x27`)
    decode+execute : lit 4 octets a `(r32)` dans R32. Complete la taille long
    a cote des familles byte (`0x80`)/word (`0x90`) deja gerees
  - `bit #n, (r32+d8)` (`0xB8..0xBF` op `0xC8..0xCF`) decode+execute : lit un
    octet a `r32 + signed(d8)`, pose `Z = NOT bit` (`H=1`, `N=0`), n'ecrit rien
  - `bit #n` sur l'addressing F3 secondary-indexed `(r32+d16)`/`(r32+r16)`
    (op `0xC8..0xCF`) decode+execute : meme semantique read-only. menu_test
    decode maintenant proprement jusqu'a son frontier d'execution reel
  - **RENVERSEMENT HW (2026-07-02, quirk DB v7)** : les copies `ld r+r` 32-bit
    (`D8..DF` sub-op `0x88..0x8F`/`0x98..0x9F`) ne sont **PAS** silicon-broken
    et s'executent maintenant (copie registre). Contre-preuve : la cartouche
    commerciale **mr_robot boote sur vraie NGPC** en executant `ld XBC, XWA`
    (`D8 89`), et le compilateur officiel CC900 emet la meme famille. Les ALU
    r+r restent bloquees (conservateur) ; les copies `ld` 16-bit (`D0..D7`)
    restent bloquees mais marquees « meme famille, tres probablement sures ».
    ⚠️ **Doute ouvert** (banniere en tete de `HARDWARE_COMPAT_POLICY.md`) :
    pourquoi le `D8 8B` avait-il ete attribue crash ? Mini-ROM de test HW du
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba
    `ANDCF/ORCF/XORCF/LDCF/STCF r` (`3` cycles), y compris en miroir `C7`
  - `cycles_consumed` utilise maintenant aussi la ligne Toshiba `LDC`
    (`3` cycles) pour les formes prefixed et `C7` executees
  - les mirroirs `C7` current-bank byte-slice de
    `rlc/rrc/rl/rr/sla/sra/sll/srl #4,r` et `A,r` executent
    maintenant aussi, avec le meme blocage honnete sur `CF`
  - `rlc/rrc/rl/rr/sla/sra/sll/srl A,r` execute maintenant,
    avec count = low nibble de `A` et blocage honnete sur `CF`
    inconnu pour `rl` / `rr`
  - `ldx (#8), #` decode + execute maintenant comme store direct byte,
    avec tolérance Toshiba sur les bytes de padding
  - `rl #4,r` / `rr #4,r` execute maintenant quand `CF` est connu,
    avec blocage honnete si `CF` reste inconnu
  - `incf` / `decf` executes maintenant pour la rotation de banque
    visible, via le meme flush/reload que `ldf`
  - `ld R8/R16/R32, (-R32)` pour la tranche pre-decrement simple
  - `call (abs24)` / `call CC, (abs24)` via `F2`
  - `add/adc/sub/sbc/and/xor/or/cp (abs24), imm8` via `C2`
  - `ld/ldw (abs8), #imm` et `ld[w] (abs8), (abs16)` via `F0`
  - `cp R16, (abs24)` et `pushw (abs24)` via `D2`
  - exécution des formes prefixed WORD r+r `mul/muls/div/divs` via `D8..DF 0x40..0x5F`
    (HW-cleared 2026-07-06, `hw_test_muldiv` : `div WA, BC` / `D9 50` s'exécute et est
    correct — ne restent silicon-broken que shift-by-A `0xF8..0xFF` et le trou `0xB8..0xBF`)
  - **OPEN BUS modélisé (HW-mesuré, `hw_test_openbus` 2026-07-08)** : une lecture
    d'adresse hors de toute région mappée renvoie `0x0000`, une écriture est
    silencieusement ignorée, et rien ne hang (le TLCS-900/H n'a pas de trap de
    bus-fault). Honest-stop conservé pour l'in-region non-backé (région BIOS sans
    image BIOS = seule classe qui bloque encore). C'est mesuré sur silicium, pas inventé.
  - exécution word `mul/muls/div/divs R, imm16` via `D8..DF 0x08/0x09/0x0A/0x0B`
    (réparé 2026-07-08 : cassé depuis le resize D8..DF→word ; `divs HL, 0x64`)
  - `pushw (r32+d16)` via `D3` (famille word-mémoire ARI, op 0x04)
  - `inc/dec byte (r32+d8)` RMW indexé via `0x88..0x8F` op 0x60..0x6F
  - `opcode-coverage` distingue maintenant les vrais trous de decode des
    bytes immediatement apres une instruction deja connue `silicon-broken`
- derniers timing follow-ups :
  - `cycles_consumed` couvre maintenant aussi le sous-ensemble
    registre/immediat deja execute :
    `LD`, `LDA`, `ADD/ADC/SUB/SBC/AND/XOR/OR/CP`, `INC/DEC`, `EXTZ/EXTS`
  - `cycles_consumed` couvre aussi le sous-ensemble memoire deja execute :
    `LD R,(mem)`, `LD (mem),R`, `LD (mem),#8`, `LDW (mem),#16`,
    `CP` registre/memoire et `PUSHW (mem)`
  - `cycles_consumed` couvre aussi les chemins ALU/memoire deja executes :
    `ADD/ADC/SUB/SBC/AND/XOR/OR R,(mem)`, `... (mem),R`,
    `ADD/SUB/AND/XOR/OR (mem),#`, `CP (mem),#`, `INC/DEC #3,(mem)`
  - `cycles_consumed` couvre aussi les operations bit/carry memoire deja
    executees : `BIT`, `LDCF/ANDCF/ORCF/XORCF`, `STCF`,
    `RES/SET/CHG/TSET` sur `(mem)`
  - `cycles_consumed` couvre aussi les rotate/shift memoire deja executes :
    `RLC/RRC/RL/RR/SLA/SRA/SLL/SRL` sur `(mem)`
  - `cycles_consumed` couvre aussi le sous-ensemble bit ops registre byte
    deja execute : `BIT/RES/SET/CHG/TSET #4,r`
  - `cycles_consumed` couvre aussi `INCF` / `DECF` (`2` cycles Toshiba)
  - `cycles_consumed` couvre aussi les shifts immediats registre executes :
    `RLC/RRC/RL/RR/SLA/SRA/SLL/SRL #4,r` via la formule Toshiba `3 + n/4`
  - `cycles_consumed` couvre aussi les formes registre `A,r` de
    `RLC/RRC/RL/RR/SLA/SRA/SLL/SRL` (`2` cycles Toshiba)
  - `cycles_consumed` couvre aussi les mirroirs `C7` byte-slice des
    formes shift immediates et `A,r`, avec les memes couts Toshiba
  - `cycles_consumed` couvre aussi `cpl` / `neg` et leurs mirroirs
    `C7` byte-slice (`2` cycles Toshiba)
  - `cycles_consumed` couvre aussi `LDX (#8), #` (`8` cycles Toshiba)
  - les miroirs `C7` current-bank byte-slice de ces familles suivent la
    meme table Toshiba au lieu du fallback global `8`
- corpus check courant :
  - `python ngpc_emu.py opcode-coverage ..\NgpCraft_toolchain\StarGunner_save_lib_test\bin\main.ngc --bytes 4096 --json`
  - resultat actuel : `4093 / 4096` bytes decoded (`99.9%`), `0` unknowns,
    `0` unsupported-decoded, `3` silicon-broken fallout

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
- quirks v3 + matcher v4 (`specs/QUIRKS.md`, `core/quirks_db.json` `2026-05-26.v6`)
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
- un seed manuel des 8 registres 32-bit et du sous-ensemble modele de control registers TLCS-900/H via `--seed-reg`, avec `--seed-xsp` conserve comme raccourci pratique
- un preset `--seed-bios-handoff-minimal` qui reproduit maintenant le meme contexte minimal que la session UI (`XSP=0x6C00`, `INTNEST=0`) sans inventer l'etat DMA
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
- premiere tranche pre-decrement utile sur le smoke ROM stable:
  - `ld R8, (-r32)`
  - `ld R16, (-r32)`
  - `ld R32, (-r32)`
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
