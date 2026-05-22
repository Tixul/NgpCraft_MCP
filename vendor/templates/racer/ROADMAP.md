# NgpCraft_base_template - Roadmap

Plan de developpement du template. Chaque section = un module potentiel.
Les features sont classees par priorite et par difficulte d'implementation.

---

## Statut actuel (v1.0-rc - baseline stable)

23 modules + driver son custom, ecrits from scratch depuis la spec hardware.
Phase 1 (parite) TERMINEE. Phase 2 (techniques modernes) quasi TERMINEE.
Driver son custom integre depuis NGPC Sound Creator.
Save flash BIOS finalise (erase + write 256 bytes) le 2026-02-14.
Vecteurs BIOS harmonises et centralises dans `ngpc_hw.h` (sys/timing/rtc/flash).
Pipeline make durci pour Windows (helpers Python, clean/move cross-platform).
Runtime minimal interne ajoute (plus de dependance obligatoire a `system.lib`).
Sprite multiplexing (`ngpc_sprmux`) implemente le 2026-02-14 puis mis de cote : module desormais considere comme abandonne, hors chemin de validation hardware.
MicroDMA (`ngpc_dma`) + raster via MicroDMA (`ngpc_dma_raster`) valides sur hardware le 2026-02-20
(dans `platformmer_test_2` et `Shmup_StarGunner` ; streaming table u8 vers registres fixes + Timer0/Timer1 pour eviter CHAIN).
Audit des sources T900/NGPC formalise le 2026-02-14 (`SOURCES.md`).
Mapping texte sysfont corrige le 2026-02-14 (ASCII direct vers index tile BIOS).
Packing tilemap corrige le 2026-02-14 (`ngpc_gfx`: bit 8 du tile dans l'entree VRAM).
Demo principal stabilise le 2026-02-14 (init ecran deterministe, texte nettoye).
Sortie ROM couleur standardisee `.ngc` (le `.ngp` reste temporaire pendant le build).
Notes de validation MicroDMA documentees dans `dev/DMA.md`.
VRAM queue (`ngpc_vramq`) ajoutee le 2026-02-14 (flush VBlank automatique).
Assert/runtime log (`ngpc_assert` + `ngpc_log`) ajoutes le 2026-02-14.
Feature flags centralises ajoutes le 2026-02-14 (`ngpc_config.h` + makefile).
Input auto-repeat ajoute le 2026-02-14 (`ngpc_input_set_repeat`, `ngpc_pad_repeat`).
Object pool pattern documente le 2026-02-14 (`examples/object_pool_example.c` + README).
Arborescence `src/` simplifiee le 2026-02-14 (`src/core`, `src/gfx`, `src/fx`) + objets `.rel` deplaces vers `build/obj/`.
Outil `ngpc_tilemap.py` ajoute le 2026-02-14 (PNG -> tiles/tilemap/palettes C).
Outil `ngpc_sprite_export.py` ajoute le 2026-02-14 (spritesheet PNG -> metasprites C).
Contraintes hardware explicites ajoutees dans les tools (map 32x32, 512 tiles max, metasprite parts/offsets).
Outil `ngpc_project_init.py` ajoute le 2026-02-14 (bootstrap projet template -> dossier cible).
Exemple pipeline assets ajoute le 2026-02-14 (`examples/ASSET_PIPELINE.md` + `asset_pipeline_example.c`).

### Modules de base (11)
- [x] ngpc_hw.h - Registres hardware (memory-mapped I/O)
- [x] ngpc_sys - Init, VBI, shutdown, memcpy/memset
- [x] ngpc_gfx - Tiles, scroll, palettes, viewport, tile flip H/V, rotation 90°
- [x] ngpc_sprite - 64 sprites (set/move/hide/flags/tile)
- [x] ngpc_text - Print string/decimal/num/hex/hex32/tile_screen
- [x] ngpc_input - Joypad avec edge detection (pressed/released/repeat)
- [x] ngpc_timing - VSync, sleep, CPU speed
- [x] ngpc_math - Sin/cos table, LCG RNG, QRandom, mul32
- [x] ngpc_flash - Save 256 bytes en flash (BIOS FLASHERS/FLASHWRITE finalise)
- [x] ngpc_bitmap - Mode pixel 160x152 (Bresenham, rect, fill)
- [x] ngpc_rtc - Horloge temps reel + alarmes

### Modules avances (8)
- [x] ngpc_debug - CPU profiler (barre scanline, FPS, %)
- [x] ngpc_metasprite - Groupes de sprites + animation
- [x] ngpc_palfx - Fade/cycle/flash palettes (4 slots)
- [x] ngpc_raster - HBlank raster effects (parallax, callbacks)
- [x] ngpc_lz - Decompression RLE + LZ77/LZSS
- [x] ngpc_lut - Tables precalculees (atan2, sqrt, dist, div)
- [x] ngpc_sprmux - Multiplexage sprites (>64 logiques, ISR HBlank)
- [x] ngpc_dma - MicroDMA (valide hardware: streaming table u8 vers registres fixes)
- [x] ngpc_dma_raster - Raster via MicroDMA (scroll par scanline sans ISR HBlank CPU)

### Driver son
- [x] Driver T6W28 custom integre (src/audio/sounds.c/h)
- [x] Z80 driver embarque (upload + polling multi-commande)
- [x] BGM streaming 4 voix + SFX (sweep, envelope, ADSR, LFO, macros)
- [x] Template SFX mapping (sounds_game_sfx_template.c)

### Infrastructure
- [x] Separation code/assets (sound/ et GraphX/)
- [x] State machine dans main.c
- [x] Build system (makefile + build.bat)
- [x] Build helpers Python (tools/build_utils.py) pour clean/move/compile
- [x] Arborescence code modulaire (`src/core`, `src/gfx`, `src/fx`, `src/audio`)
- [x] Objets intermediaires hors `src/` (`build/obj/*.rel`)
- [x] VRAM queue + flush VBlank (`ngpc_vramq.c/h`)
- [x] Assert + ring buffer debug (`ngpc_assert.h`, `ngpc_log.c/h`)
- [x] Feature flags centralises (`src/core/ngpc_config.h` + makefile `-D`)
- [x] Licence MIT
- [x] Screen effects hardware (sprite offset, LCD invert, outside color, char over)
- [x] Code verifie C89 compatible (cc900)

---

## Phase 1 - Completer la parite avec l'ancien template [TERMINEE]

Toutes les features de l'ancien template 2003 sont maintenant portees :

- [x] Mode bitmap 2bpp (ngpc_bitmap.c/h) - pixel, line, rect, fill_rect, hline, vline
- [x] RTC / horloge (ngpc_rtc.c/h) - get, set_alarm, set_wake, BCD helpers
- [x] ngpc_text_print_num() - decimal sans zero-padding
- [x] ngpc_text_print_hex32() - hex 32-bit (8 digits)
- [x] ngpc_text_tile_screen() - remplir 20x19 depuis un tableau
- [x] ngpc_gfx_get_tile() - relire un tile depuis le tilemap
- [x] ngpc_qrandom() - table 256 valeurs, lecture sequentielle ultra-rapide

### Mode bitmap 7 couleurs (futur)

Utilise les 2 scroll planes superposes pour 7 couleurs.
Necessite 380 tiles * 2 planes = 760 tiles (> 512 max).
Solution : viewport reduit (128x128 = 256 tiles/plane = 512 total).
Report en Phase 2 pour etude plus approfondie.

Difficulte : trivial

---

## Phase 2 - Nouvelles features (techniques modernes)

Features qui n'existaient PAS dans le template 2003.
Techniques decouvertes/popularisees par la scene retro depuis.

### [x] Raster effects / HBlank (ngpc_raster.c/h) -- FAIT

Scroll table par scanline, callbacks par ligne, parallax helper.
Timer 0 HBlank ISR avec avertissement sur le budget CPU (~5 us/ligne).

### MicroDMA (ngpc_dma.c/h) [HAUTE PRIORITE] -- VALIDE HARDWARE (2026-02-20)

4 canaux DMA hardware completement ignores par l'ancien template.
Le K2GE peut declencher des transferts DMA sur interrupt.

Spec hardware : vecteurs DMA a 0x6FF0-0x6FFC, declenchement par
micro DMA start vector (0x0A-0x19).

Possibilites :
- **Raster scroll par DMA** : ecrire les scroll offsets automatiquement a
  chaque HBlank (table -> registre, parallax sans code dans l'ISR)
- **Streaming audio** : transfert de donnees vers Z80 RAM par DMA

API implementee (scope actuel, valide hardware) :
- `ngpc_dma_init()` - installe les ISR de fin DMA (0x6FF0-0x6FFC)
- `ngpc_dma_start_table_u8(channel, dst_reg, src_table, count, start_vector)`
- `ngpc_dma_link_hblank(channel, dst_reg, src_table, count)`
- `ngpc_dma_link_vblank(channel, dst_reg, src_table, count)` (unsafe, bloque par defaut)
- `ngpc_dma_stop(channel)` - arret du canal
- `ngpc_dma_remaining()/ngpc_dma_active()` - etat du transfert
- Helpers Timer0/Timer1: `ngpc_dma_timer0_hblank_enable()` et `ngpc_dma_timer01_hblank_enable()`
- Streams avec start_vector: `NgpcDmaU8Stream` + `ngpc_dma_stream_*_u8()`
- Wrapper haut niveau scroll: `ngpc_dma_raster_*()`

Limites actuelles :
- Mode expose: stream byte `src++ -> dst fixe` (table vers registre)
- Pas encore de couche "copy generic mem->mem"
- **Attention hardware (important)** : MicroDMA consomme l'interruption choisie.
  Si on utilise VBlank comme "start vector", le handler VBlank CPU peut ne plus tourner
  => watchdog non clear => power off. Dans ce template, le VBlank trigger est bloque
  par defaut via `NGP_DMA_ALLOW_VBLANK_TRIGGER=0`.

Sources confirmees (audit `SOURCES.md`) :
- Vecteurs start DMA C900/C900_H/C900_L: `DMA0V..DMA3V = 0x7C..0x7F`
  (`02_CODE_PATTERNS/.../T900/INCLUDE/IO900.H`).
- Vecteurs fin micro-DMA cote framework NGPC: `0x6FF0..0x6FFC`
  (`02_CODE_PATTERNS/.../WORKSPACE/Template/ngpc.h` et `Tools/NGPConv/*/HARDWARE.INC`).
- Pseudo-registre compilateur `__DMAC0` documente dans les notes release compiler
  (`02_CODE_PATTERNS/.../T900/README/HIS96CE0.TXT`).
- Attention: macros HDMA (`M_SINC*`, `M_CNT`, etc.) different selon variantes
  `C900/C900_L` vs `C900_H/C900_H2` (ne pas melanger).

Decision projet 2026 :
- Garder `ngpc_dma` limite a un scope maitrise (stream table -> registre fixe),
  car c'est le cas d'usage le plus utile sur NGPC (raster effects) et le plus facile
  a valider sur hardware.
- Pour 2 streams simultanes sans CHAIN: utiliser Timer0+Timer1 (start vectors 0x10 + 0x11).

Difficulte : moyenne-haute (peu de documentation, verification hardware obligatoire)

---

## TODO - Stabilisation pipeline GraphX (2026-02-18)

Constat:
- Une intro plein ecran (PNG -> tilemap) a rendu incorrect via les helpers `ngpc_gfx_*`.
- Le rendu est redevenu correct via une methode "windjammer-style" (ecriture VRAM brute).

Decision court terme (safe):
- Officialiser l'utilisation de `src/gfx/ngpc_tilemap_blit.h` pour afficher des tilemaps PNG.

Solution 2 (a faire, pour corriger les helpers):
- [x] Adapter signatures + flags cc900 (pointeurs `__far` via `NGP_FAR`) pour stabiliser
  `ngpc_gfx_load_tiles_at()` / `ngpc_gfx_load_tiles_u8_at()` sur des assets en ROM (0x200000+).
- [ ] (optionnel) Prouver le bug near/far en runtime (log d'adresses/pointeurs) pour archive/debug.

### [x] Metasprites (ngpc_metasprite.c/h) -- FAIT

16 parts max, auto quad swap H/V flip, systeme d'animation avec
MsprAnimator (start, update, done). Offscreen culling integre.

### [x] Sprite multiplexing (ngpc_sprmux.c/h) -- FAIT (module abandonne)

Reutiliser les 64 slots sprites pendant le HBlank pour afficher
**plus de 64 sprites** a l'ecran. Technique classique NES/SMS/GB.

Principe : trier les sprites par Y, dessiner les 64 premiers,
puis au HBlank quand les sprites du haut sont deja rendus,
reutiliser leurs slots pour des sprites plus bas.

API implementee :
- `ngpc_sprmux_begin()` - debut de frame, vider la liste
- `ngpc_sprmux_add(x, y, tile, pal, flags)` - ajouter un sprite logique
- `ngpc_sprmux_flush()` - trier et allouer les slots hardware
- `ngpc_sprmux_overflow_count()` - detecter les drops si surcharge verticale

Difficulte : haute (timing HBlank critique). Code en place, mais le module
est desormais considere comme abandonne et sort du chemin de validation.

### [x] Compression tiles (ngpc_lz.c/h) -- FAIT

RLE + LZ77/LZSS decompression. Buffer interne 2KB (~128 tiles).
Fonctions directes + convenience _to_tiles(). Outil Python companion
`ngpc_compress.py` finalise (modes `rle` / `lz77` / `both`).

### [x] Debug profiler (ngpc_debug.c/h) -- FAIT

Barre coloree (vert/jaune/rouge), pourcentage CPU, compteur FPS.
Desactivable par `#define NGPC_DEBUG 0` (zero code en release).

### [x] Palette animation (ngpc_palfx.c/h) -- FAIT

4 slots simultanes. Fade (par canal R/G/B), cycle (rotation 1-2-3),
flash (hit/selection). ngpc_palfx_update() chaque frame.

### [x] Tables precalculees (ngpc_lut.c/h) -- FAIT

atan2 (octant table 32 bytes), sqrt16 (binary search), dist (alpha-max-beta-min),
fast div (reciprocal multiply). Zero FPU, minimal ROM.

---

## Phase 2.5 - Robustesse & qualite (post-validation hardware)

Features qui n'ajoutent pas de hardware access mais rendent le template plus
solide, plus configurable, et plus utilisable pour des vrais jeux.
A implementer apres la validation hardware des modules WIP.

### [x] VRAM Queue + flush VBlank (ngpc_vramq.c/h) [HAUTE PRIORITE] -- FAIT (2026-02-14)

Ecrire dans la VRAM pendant le rendu actif cause des Character Over.
Une queue de commandes flushee pendant le VBlank resout ca proprement.

API prevue :
- `ngpc_vramq_copy(dst, src, len)` - copie tiles ou tilemap patches
- `ngpc_vramq_fill(dst, val, len)` - clear de zones VRAM
- `ngpc_vramq_flush()` - appele en VBI, ~24000 cycles disponibles

Contraintes :
- Buffer statique ~200 bytes (commandes compactes : dst_offset + src_ptr + len)
- Limite configurable de commandes par frame (warning debug si depasse)
- Flush doit etre rapide : boucle inline, pas d'appels de fonction

Difficulte : faible-moyenne (~150 lignes)

### [x] Assert + ring buffer debug (ngpc_assert.h / ngpc_log.c/h) -- FAIT (2026-02-14)

Completer `ngpc_debug` avec des diagnostics zero-cost en release.

API prevue :
- `NGPC_ASSERT(condition)` - en debug: freeze + blink palette + affiche position
- `NGPC_LOG_HEX(label, value)` - ecrit dans un ring buffer 256 bytes
- `NGPC_LOG_STR(label, str)` - idem, chaines courtes
- `ngpc_log_dump(plane, pal, x, y)` - affiche les N dernieres entrees a l'ecran

En release (`NGP_PROFILE_RELEASE=1`) : toutes les macros compilent en rien.
Le ring buffer est consultable a l'ecran, utile pour debugger sur hardware reel
sans port serie.

Cout RAM : ~300 bytes (ring buffer + index)
Difficulte : faible (~100 lignes)

### [x] config.h + feature flags -- FAIT (2026-02-14)

Un seul fichier `src/core/ngpc_config.h` qui centralise les toggles du template :

```c
#define NGP_ENABLE_SOUND        1
#define NGP_ENABLE_FLASH_SAVE   1
#define NGP_ENABLE_DEBUG        1
#define NGP_ENABLE_DMA          0
#define NGP_ENABLE_SPRMUX       0
#define NGP_ENABLE_PROFILER     0
#define NGP_PROFILE_RELEASE     0  /* 1 = strip assert/log/debug */
```

Le makefile conditionne les OBJS et les flags `-D` en fonction.
Permet de garder le template modulaire : un petit jeu peut desactiver le son
et le DMA pour gagner en ROM et en RAM.

Difficulte : trivial (1 fichier + quelques `#if` dans les headers)

### [x] Input auto-repeat (extension ngpc_input) -- FAIT (2026-02-14)

Ajouter le repeat pour les menus (indispensable pour l'UX) :

```c
extern u8 ngpc_pad_repeat;  /* actif apres delay, puis toutes les N frames */
void ngpc_input_set_repeat(u8 delay, u8 rate);
```

Utilisation typique : naviguer dans un menu d-pad sans relacher/re-presser.

Difficulte : trivial (~35 lignes dans ngpc_input.c, repeat par bouton)

### [x] Object pool pattern (exemple/documentation) -- FAIT (2026-02-14)

Pas un module a part entiere, mais un pattern documente et un exemple
reutilisable pour les objets de jeu (bullets, particules, entites).

```c
#define MAX_BULLETS 16
static Bullet s_bullets[MAX_BULLETS];
static u16 s_active_mask;  /* bit = slot occupe */

u8   pool_alloc(void);     /* retourne index libre ou 0xFF */
void pool_free(u8 idx);
```

Tableau fixe + bitmask, zero fragmentation, zero malloc.
A documenter dans le README comme pattern recommande.

Difficulte : trivial (pattern, pas de module)

---

## Phase 3 - Communication

### Link cable (ngpc_link.c/h)

Communication serie entre deux NGPC via le cable link.

Spec hardware :
- BIOS vectors : VECT_COMINIT, VECT_COMSENDSTART, VECT_COMRECIVESTART,
  VECT_COMCREATEDATA, VECT_COMGETDATA, VECT_COMONRTS, VECT_COMOFFRTS
- Interrupts : Serial TX (0x6FE4), Serial RX (0x6FE8)
- La spec precise que TX/RX sont asynchrones (pas de sync VBlank entre les 2 consoles)

API possible :
- `ngpc_link_init()` - initialiser le BIOS serie
- `ngpc_link_send(data, len)` - envoyer des octets
- `ngpc_link_recv(buf, max_len)` - recevoir (non bloquant)
- `ngpc_link_status()` - etat de la connexion
- `ngpc_link_is_connected()` - cable branche ?

Considerations :
- Pas de sync VBlank entre les 2 machines (gerer le lag)
- Buffer overflows possible si un cote est plus lent
- Protocole de handshake necessaire (qui est P1, qui est P2)
- La spec dit d'utiliser les appels BIOS en subroutine (SYSTEM_CALL)
  et non en SWI pour eviter de bloquer les interrupts serie

Difficulte : haute (asynchrone, peu d'exemples existants, tests
hardware obligatoires avec 2 consoles + cable)

---

## Phase 4 - Outils PC companion

Outils en Python pour le workflow de developpement.

### ngpc_compress.py
- Compresse les tilesets/tilemaps en LZ77/RLE
- Integration dans le build (pre-processing avant cc900)

### ngpc_tilemap.py -- MVP FAIT (2026-02-14)
- Convertit une image PNG en tileset + tilemap NGPC
- Detecte les tiles dupliquees, optimise la palette
- Exporte en .c directement dans GraphX/

### ngpc_sprite_export.py -- MVP FAIT (2026-02-14)
- Convertit les sprites Piskel/Aseprite en format metasprite
- Genere les struct NgpcMetasprite en .c

### ngpc_project_init.py -- MVP FAIT (2026-02-14)
- Script de creation de projet : copie le template, renomme les fichiers,
  configure le Makefile et carthdr.h automatiquement

---

## Priorites suggerees

### FAIT
1. ~~Phase 1 (parite avec l'ancien template)~~ FAIT
2. ~~HBlank raster effects~~ FAIT
3. ~~Metasprites~~ FAIT
4. ~~Debug profiler~~ FAIT
5. ~~Palette animation~~ FAIT
6. ~~Compression tiles (RLE + LZ77)~~ FAIT
7. ~~Tables precalculees (atan2, sqrt, dist, div)~~ FAIT
8. ~~Integration driver son custom~~ FAIT

### Pour le release v1.0
9. ~~Outil Python `ngpc_compress.py` (compression offline)~~ FAIT (2026-02-14)
   - Modes `rle` / `lz77` / `both` (auto-best), suffixes harmonises (`_rle`, `_lz`)
   - Verification roundtrip integree (compress -> decompress -> compare)
   - Generation optionnelle du header (`--header`) avec `*_len` et `*_raw_len`
10. ~~Tester la compilation avec cc900~~ FAIT (2026-02-14)
    Build complet valide: compile, link, `tuconv`, `s242ngp`, `move_files`.
    Le template linke sans `system.lib` via runtime interne (`ngpc_runtime.c` + alias ASM).
    `SYSTEM_LIB=<path>` reste supporte pour compatibilite si necessaire.
11. Valider save/load flash en conditions reelles (emulateur + hardware)
    Harness de validation ajoute dans `main.c` (A=save, OPTION=load)
12. ~~Nettoyer warnings cc900 restants (ex: `ngpc_palfx.c` conversion type)~~ FAIT (2026-02-14)

### Apres validation hardware (v1.5 - robustesse)
13. ~~VRAM queue + flush VBlank (anti-glitch, ~150 lignes, 200B RAM)~~ FAIT (2026-02-14)
14. ~~Assert + ring buffer debug (zero-cost release, ~100 lignes, 300B RAM)~~ FAIT (2026-02-14)
15. ~~config.h + feature flags (1 fichier, build modulaire)~~ FAIT (2026-02-14)
16. ~~Input auto-repeat (UX menus, ~15 lignes)~~ FAIT (2026-02-14)
17. ~~Object pool pattern (documentation + exemple reutilisable)~~ FAIT (2026-02-14)

### Necessite hardware pour tester (v2.0)
18. MicroDMA (validation hardware + extension API beyond table streaming)
19. Link cable
20. Bitmap 7 couleurs (2 planes, viewport reduit)

---

## Prochaine etape immediate (dans une copie du template)

- [ ] Creer une copie de travail dediee aux tests (garder ce template "baseline" propre)
- [ ] Executer la matrice de tests `ngpc_dma` (HBlank/VBlank, transfert table->registre, robustesse)
- [ ] Journaliser les resultats (emulateur + hardware) et revenir corriger dans la copie
- [ ] Reintegrer uniquement les correctifs valides dans le template de base

---

## Notes techniques

### Budget CPU par frame

A 6.144 MHz, on a ~102400 cycles par frame (6144000 / 60).
Le VBlank dure ~3.94 ms = ~24200 cycles.

Repartition typique :
- VBI (watchdog + input + audio) : ~2000 cycles
- Game logic : ~50000 cycles (variable)
- VRAM updates (tiles, sprites) : ~20000 cycles (idealement pendant VBlank)
- Marge : ~30000 cycles

Un acces VRAM pendant le rendu actif peut causer un Character Over.
Concentrer les ecritures VRAM dans le VBlank ou juste apres.

### Budget RAM

12 KB total. Apres stack et variables systeme, ~9-10 KB disponibles.
Repartition typique :
- Stack : ~1 KB
- Variables globales du template : ~200 bytes
- Driver son (voices, state) : ~500 bytes
- Sprite/metasprite state : ~300 bytes
- Game state : ~8 KB restants

### Registres compiler

Le compilateur cc900 utilise les registres TLCS-900/H :
- Bank 0-2 : disponibles pour le code utilisateur
- Bank 3 : reserve par le systeme (ne pas utiliser sauf BIOS calls)
- Les ISR doivent sauvegarder/restaurer tous les registres utilises
