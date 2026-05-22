# BIOS HLE Strategy

Spec d'implémentation **High-Level Emulation** du BIOS NGPC dans
l'émulateur, sans jamais distribuer ni dépendre du dump propriétaire
SNK.

Document écrit 2026-05-20. Référence master :
`../../Doc de dev/Final/BIOS_FLASH_SAVES_STRATEGY.md` (point d'entrée
unique cross-projets).

---

## 1. Principe

Le BIOS NGPC est mappé `0xFF0000..0xFFFFFF` (64 KB) dans
`core/bus.py` (région `BIOS_ROM`, kind `bios`). Cette région est
actuellement `unbacked` : lire les bytes retourne le status
`unbacked` et l'executor stoppe honnêtement si une instruction
prétend lire le BIOS.

**Pour l'exécution des BIOS calls**, l'émulateur intercepte les
opcodes `SWI n` (encoding `0xF8 + n`) **avant** que le CPU ne saute
vers le vecteur BIOS. Le handler Python applique le side-effect
attendu, écrit les valeurs de retour dans les registres bank-3
appropriés (RA3, XBC3, …), et avance PC à l'instruction qui suit le
SWI — exactement comme si le BIOS avait exécuté + RETI.

C'est de **l'HLE pur** : aucun byte du BIOS n'est référencé.

L'alternative LLE (charger un dump dans `0xFF0000..0xFFFFFF` et
laisser le décodeur TLCS-900/H exécuter le BIOS lui-même) est
**optionnelle**, gated derrière `--bios <file.bin>`, et n'embarque
jamais le dump dans le repo.

---

## 2. Statut actuel

`core/execute.py::_try_execute_swi` existe et fait un **no-op stub
silencieux** : PC avance, registres inchangés, side-effect non
appliqué, note `Executed SWI {n}: BIOS call not modeled. PC advanced
to next instruction as if the BIOS returned normally. Side effects
of the BIOS call are omitted.`

Effet observable : la plupart des ROMs cc900 du toolchain
**continuent** sans incident car elles n'observent pas activement les
side-effects BIOS (le bytecode généré utilise les BIOS calls pour
des effets globaux comme "charge la font" ou "sauvegarde", pas pour
des valeurs de retour synchrones).

---

## 3. Vecteur d'invocation

Deux mécanismes d'appel BIOS (per `BIOS_REF.md §1-2`) :

### Méthode 1 — SYSTEM_CALL via vecteur (non-bloquant sur IRQ)

```asm
ldb  rw3, VECT_xxx    ; numéro vecteur dans RW3 (bank 3)
; set params dans bank 3 (XBC3, XDE3, etc.)
call SYSTEM_CALL
```

### Méthode 2 — SWI 1 directe (DI pendant exécution, recommandé pour flash/shutdown)

```asm
ldb  rw3, VECT_xxx    ; numéro vecteur dans RW3 (bank 3)
swi  1
```

Dans les deux cas, **RW3 (= reg W bank 3) porte le numéro de vecteur**
et les paramètres passent par les registres bank-3.

### Côté émulateur

Bank 3 = quand `RFP == 3` (Register File Pointer bit dans SR[8..10]).
L'émulateur a déjà :
- `NgpCraft_emulator.core.cpu.NgpcCpuState.rfp` (2-bit value 0..3)
- `_try_execute_swi` qui lit `decoded.raw_bytes[0]` pour le numéro

Pour HLE complet on doit modéliser :
1. Le swap de bank quand `RFP` change (currently RFP est tracked
   mais le swap visible des registres XWA/XBC/XDE/XHL n'est pas
   wired — c'est SR Phase 3)
2. Lire RW3 depuis le shadow bank-3
3. Dispatcher selon RW3 dans une table d'HLE functions

**Court-circuit pragmatique** : pour les BIOS calls qui ne dépendent
pas du contenu bank-3 (`SYSFONTSET`, `CLOCKGEARSET`, `SHUTDOWN`,
`USRSHUTDOWN`), implémenter en HLE direct sans attendre SR Phase 3.
Les BIOS calls bank-3-dependent (`FLASHWRITE`, `RTCGET`) attendent
SR Phase 3 ou sont bypassés par la lib `ngpc_flash` maison.

---

## 4. Table SWI — statut par BIOS call

Référence : `04_MY_PROJECTS/Doc de dev/Final/BIOS_REF.md` §4-5.

| SWI / Vect | Nom | Statut | Implémentation HLE |
|------|-----|--------|---|
| SWI 0 | (reserved) | noop | aucune |
| SWI 1 + RW3=0 | `BIOS_SHUTDOWN` | à faire | stop honnête status=`bios-shutdown`, écrire diag note |
| SWI 1 + RW3=1 | `BIOS_CLOCKGEARSET` | à faire (trivial) | noop documenté : on émule à pleine vitesse, le clock gear n'affecte pas notre référence model |
| SWI 1 + RW3=2 | `BIOS_RTCGET` | à faire (court) | lire `datetime.now()` Python, convertir en format BCD attendu par la NGPC, écrire dans buffer pointé par XHL3 |
| SWI 1 + RW3=3 | `BIOS_RTCSET` | à faire (trivial) | noop (on ne touche pas l'horloge host) |
| SWI 1 + RW3=4 | `BIOS_ALARMSET` | à faire (trivial) | noop (pas d'alarme background sur émulateur) |
| SWI 1 + RW3=5 | `BIOS_SYSFONTSET` | à faire | embarquer notre font 8×8 BSD/MIT, la copier en CHAR_RAM tiles 32..127 |
| SWI 1 + RW3=6 | `BIOS_FLASHWRITE` | **bypassable** | la lib `ngpc_flash_write_asm` du projet ne passe pas par BIOS. HLE à faire uniquement si une ROM ne utilise pas cette lib. (Lib propre au projet → réutilisable librement pour l'HLE Python sans contrainte de licence.) |
| SWI 1 + RW3=7 | `BIOS_FLASHALLERS` | à faire (pas critique) | erase all blocks — non-trivial, mais notre design append-only n'en a pas besoin |
| SWI 1 + RW3=8 | `BIOS_FLASHERS` | **bypassable** | lib `ngpc_flash_erase_asm` du projet. HLE à faire uniquement si nécessaire. **Note : bug HW connu blocs 32-34** — l'émulateur peut reproduire ce bug pour validation HW-faithful (retourner échec). |
| SWI 1 + RW3=9 | `BIOS_ALARMDOWNLOAD` | à faire (trivial) | noop |
| SWI 9 | `BIOS_USRSHUTDOWN` | à faire | stop honnête status=`user-shutdown` |
| SWI 11..13 | divers | à déterminer | trace-only jusqu'à ce qu'une ROM en utilise activement |

---

## 5. Flash HLE — détail

Voir aussi `SAVE_POLICY.md` pour la politique haut niveau et
`Doc de dev/Final/BIOS_FLASH_SAVES_STRATEGY.md` §5 pour le protocole
HW.

### 5.1 Composants à modéliser

1. **Backing flash 8 KB** : un `bytearray` de 8 192 bytes pour le
   block 33 (`0x3FA000..0x3FBFFF`). Initialisé à `0xFF` (état effacé
   de la flash NGPC). Persisté optionnellement sous `<rom>.sram`.
2. **I/O `0x6E` (FLASH_BUS_CTRL)** : flag `flash_we_enabled` toggled
   par writes à cette adresse :
   - `0x14` → `flash_we_enabled = True`
   - `0xF0` → `flash_we_enabled = False`
3. **I/O `0x6F` (FLASH_WD)** : watchdog. `0xB1` = extended, `0x4E` =
   normal. L'émulateur peut le tracker pour validation HW-faithful
   sans appliquer de comportement watchdog (pas de reset auto).
4. **Cart write interception** : pendant `flash_we_enabled`, les
   writes au cart window `0x200000..0x3FFFFF` sont **interceptées** :
   - Pattern AMD unlock detection (séquences à `0x200000` /
     `0x200555` / `0x2002AA`)
   - Erase block command → zero-out le bytearray flash (ou plus
     précisément, set tous les bytes à `0xFF` pour respecter le
     comportement HW de la NOR flash)
   - Write byte command → commit le byte à l'adresse dans le bytearray
5. **Read** : sur read au range `0x3FA000..0x3FBFFF`, le bus retourne
   le byte depuis le bytearray flash (overlay au-dessus de la ROM
   image qui contient peut-être une version initiale).

### 5.2 Persistance disque

- Au `load_machine_state(rom)` : si `<rom>.sram` existe, charger
  dans le bytearray flash.
- À la fin d'un run (ou via CLI explicite `flash-export`), dumper
  le bytearray vers `<rom>.sram`.
- Format : binaire brut 8 KB, byte-for-byte image du block 33.
- Le savestate v2 capture aussi le bytearray flash (via le
  `writable_overlay`), donc on a deux niveaux de persistance :
  cart flash réelle (`.sram`) + savestate snapshot (`.json`).

### 5.3 Bugs HW reproductibles

L'émulateur peut **simuler** les bugs HW connus (référence
`ngpc_flash.h` docstring) pour valider que les programmes user
les évitent correctement :

| Bug | Reproduction émulateur | Pourquoi utile |
|-----|------------------------|----------------|
| `VECT_FLASHERS` ne peut pas effacer blocs 32-34 | Si une ROM appelle SWI 1 RW3=8 avec param block 32/33/34, retourner status échec | Valider que la ROM utilise bien la lib `ngpc_flash` maison qui contourne |
| `CLR_FLASH_RAM` silent-fail au 2e call | Compter les calls par power-on session, 2e call = no-op silencieux | Valider que la ROM utilise le pattern append-only (1 erase max par session) |
| Writes directs sans `(0x6E)=0x14` | Ignorer les writes au cart window quand `flash_we_enabled == False` | Valider que la ROM toggle correctement `/WE` avant d'écrire |

C'est l'extension naturelle de la doctrine HW-faithful déjà
appliquée aux opcodes silicon-broken (D0+ALU-imm, D8 r+r).

---

## 6. Tests attendus quand on shippe HLE

Référence ROM : `04_MY_PROJECTS/NgpCraft_toolchain/StarGunner_save_lib_test/bin/main.ngc`
(HW-validated).

### Tests unitaires (à ajouter dans `tests/test_bios_hle.py`)

1. `test_swi_noop_stub_advances_pc` (déjà couvert dans `test_execute.py`)
2. `test_flash_we_toggle_via_io_0x6e`
3. `test_amd_unlock_sequence_recognition`
4. `test_erase_block_33_zeros_8kb`
5. `test_write_byte_commits_to_flash_backing`
6. `test_writes_without_we_enabled_are_noop` (bug HW reproduit)
7. `test_savestate_v2_captures_flash_overlay`
8. `test_sysfontset_copies_font_to_char_ram_tiles_32_to_127`
9. `test_user_shutdown_stops_honestly`
10. `test_clockgearset_is_noop_documented`

### Tests end-to-end

1. Lancer StarGunner_save_lib_test, exécuter jusqu'à la routine de
   save (post-init savestate), vérifier que le block 33 contient
   bien un slot avec checksum valide.
2. Reload depuis le savestate, vérifier que la flash persiste.
3. Re-save, vérifier que le slot index a augmenté de 1 (pattern
   append-only).

---

## 7. Option LLE complémentaire (M8+)

Une fois HLE stable, ajouter `--bios <file.bin>` :

```
ngpc_emu.py run-until-exec game.ngc 0xFF0100 --bios ~/dumps/ngp.bios
```

Chargement :
1. `argparse` parse `--bios <path>`
2. `load_machine_state(rom_path, bios_path=...)` lit 64 KB du dump
3. La région `BIOS_ROM` dans `core/bus.py` est backed par les bytes
   du dump au lieu d'être `unbacked`
4. Quand l'executor décode `SWI n`, deux options :
   - Mode HLE pur (défaut) : intercepter en Python
   - Mode LLE (avec `--bios`) : laisser le CPU sauter au vecteur
     dans `0xFF0000..` et exécuter le BIOS comme une ROM normale
5. Une option `--bios-mode hle|lle|mixed` permet de choisir

Le dump n'est **jamais distribué** avec l'émulateur. `.gitignore`
inclut `*.bios`, `*.bios.bin`, `**/ngp.bios*` pour éviter tout
commit accidentel.

---

## 8. Workflow gap-filling

Quand une ROM rencontre un BIOS call non implémenté :

1. **Vérifier l'event log** : status `stopped-on-unsupported-decoded-instruction`
   ou note `Executed SWI N: BIOS call not modeled`.
2. **Identifier le BIOS call** : numéro SWI + valeur de RW3 au
   moment du SWI (via `registers --seed-from <state>` à l'event
   précédant).
3. **Vérifier `BIOS_REF.md` §4-5** : description de ce que ce call
   est censé faire.
4. **Implémenter en HLE** dans `_try_execute_swi` :
   - Lire les paramètres depuis bank-3 (XBC3, XDE3, XHL3, …)
   - Appliquer le side-effect sur le runtime overlay
   - Écrire le return value dans RA3 si applicable
   - Avancer PC normalement
5. **Ajouter un test** dans `tests/test_bios_hle.py`.
6. **Mettre à jour ce document** (§4 table SWI) avec le statut.

Si `BIOS_REF.md` est insuffisant : voir le workflow §7 du master
strategy doc (disasm local du BIOS perso, enrichir `BIOS_REF.md`
en prose).

---

## 9. Références

- Master strategy : `04_MY_PROJECTS/Doc de dev/Final/BIOS_FLASH_SAVES_STRATEGY.md`
- BIOS calls doc : `04_MY_PROJECTS/Doc de dev/Final/BIOS_REF.md`
- Lib flash maison : `04_MY_PROJECTS/Doc de dev/NgpCraft_base_template/.../src/core/ngpc_flash.{h,c}` + `ngpc_flash_asm.asm`
- Smoke ROM : `04_MY_PROJECTS/NgpCraft_toolchain/StarGunner_save_lib_test/bin/main.ngc`
- Politique saves : `SAVE_POLICY.md` (dans ce repo)
- Savestate v2 spec : `specs/SAVESTATE.md`
- HW quirks : `core/quirks_db.json` (v `2026-05-20.v4`)
