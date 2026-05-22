Date: 2026-02-22

Ce document couvre:
- Tout ce qui a ete fait pour demarrer un shmup horizontal dans `NGPC_Template__2026 - Copie`.
- Les changements importants apportes au template (ceux qui valent le coup d'etre portes dans ton template de base).
- Les problemes rencontres sur vrai hardware + solutions (et pourquoi).

---

## 1) Etat actuel (resume)

Code principal:
- `src/game/shmup.c`

Contenu du shmup:
- Background qui scroll (SCR1).
- HUD sur SCR2 + "hud bar" vide en bas (zone interdite au gameplay).
- Vaisseau player 16x16 en 2 layers (A + B) pour rester dans les limites de couleurs.
- Trainee animee (frames `traine_f_1..3`).
- Tir avec autofire (cadence moyenne), bullets 8x8.
- Bullets ennemies (pour `ennemi_5_fat`), 8x8 (tile `explosion_f1` pour etre bien visibles).
- Collisions bullets/enemy + explosions (`explosion_f_1..3`).
- Base power-ups prete:
  - SPEED: augmente la vitesse du player.
  - SHIELD: absorbe 1 hit (sans perdre de vie).
  - OPTION: satellite (Nemesis) qui suit avec delai et tire.
- Ennemis en vagues:
  - Pas d'ennemis/asteroides pendant les 5 premieres secondes.
  - Vagues de 4..8.
  - Vague = un seul type de sprite (pas de melange dans une vague).
  - Type 1: "escalier" smooth.
  - Type 2: "file indienne + vague" (sinus) avec dephasage.
  - Type 3 (`ennemi_3.png`): file indienne, demi-tour en arc + monte/descend de 2-3 tiles pendant le demi-tour, puis repart vers la droite.
  - Type 5 (`ennemi_5_fat.png`): 16x16, arrive avec les petites vagues, s'arrete pres du bord droit, 3 hits, tire toutes les 1s (1 seul a la fois).
- Asteroides: obstacles non destructibles, rares (pas toujours a l'ecran).
- UI sprites:
  - START centre en debut de partie.
  - GAME OVER centre en fin de partie (et nettoyage des sprites: on laisse le background + explosion + banner).
- Son:
  - musique hybride NGPC Sound Creator integree (NOTE_TABLE + instruments + streams)
  - SFX (NGPC Sound Creator) branches: menu_move/menu_select, tir_vaisseau, explosion

ROMs:
- `bin/main.ngp`
- `bin/main.ngc`

---

## 2) Commandes utiles (regen + build)

Depuis `NGPC_Template__2026 - Copie/`:

```sh
python tools/shmup_export_background.py
python tools/shmup_export_hudbar.py
python tools/shmup_export_sprites.py
make clean
make
```

---

## 3) Pipeline assets (PNG -> C) + conventions VRAM

### Dossier source attendu

Les scripts shmup lisent ici:
- `GraphX/tile 8x8/sprites/`

### Background (tilemap)

Script:
- `tools/shmup_export_background.py`

Sorties:
- `GraphX/shmup_bg.c/.h`

Runtime:
```c
NGP_TILEMAP_BLIT_SCR1(shmup_bg, 128);
```

### HUD bar (tilemap en bas)

Objectif:
- HUD vide en bas de l'ecran (sprite/ennemis interdits par dessus).

Probleme rencontre:
- Le PNG du HUD avait trop de couleurs pour un export tilemap "single plane", le pipeline le split en 2 layers.

Solution:
- `tools/shmup_export_hudbar.py` remappe une couleur pour retomber a 3 couleurs, puis exporte la tilemap.

Sorties:
- `GraphX/shmup_hudbar.c/.h`

Runtime (SCR2, tout en bas):
```c
hudbar_blit_scr2(SHMUP_HUDBAR_TILE_BASE, SHMUP_HUDBAR_PAL_BASE, 17);
```

### Sprites (metasprites) + palettes

Script:
- `tools/shmup_export_sprites.py`

Genere (entre autres):
- `GraphX/shmup_player_a_mspr.c/.h`
- `GraphX/shmup_player_b_mspr.c/.h`
- `GraphX/shmup_enemy1_mspr.c/.h`
- `GraphX/shmup_enemy2_mspr.c/.h`
- `GraphX/shmup_enemy3_mspr.c/.h`
- `GraphX/shmup_enemy5_fat_mspr.c/.h`
- `GraphX/shmup_option_a_mspr.c/.h` (OPTION layer A, palette ship A)
- `GraphX/shmup_option_b_mspr.c/.h` (OPTION layer B, palette ship B)
- `GraphX/shmup_ast*_mspr.c/.h`
- `GraphX/ui_start_mspr.c/.h`
- `GraphX/ui_game_over_mspr.c/.h`
- `GraphX/ui_digits_mspr.c/.h` (digits 0-9)
- `GraphX/ui_powerup_mspr.c/.h` (S/M/D/L/O)

Important:
- Les exports utilisent un `tile_base` global qui s'incremente (VRAM tiles).
- Les exports utilisent un `pal_base` global qui s'incremente (palettes sprites).
- Pour les assets qui doivent reutiliser *exactement* une palette existante (meme indices de couleurs),
  l'exporteur supporte `--fixed-palette` (ajoute dans `tools/ngpc_sprite_export.py`).
  C'est utilise pour OPTION afin de matcher les palettes du vaisseau (A+B) sans re-map.
- Dans l'etat actuel (apres unification de la palette asteroides), on est redescendu a ~12 palettes au total.
  Ca laisse de la marge pour ajouter des drops/powerups/UI.
  - `ennemi_5_fat` partage la palette de `shmup_trail` (meme couleurs).
  - `ui_digits` + `ui_powerup` partagent la palette de `ui_start` (vert HUD).
  Attention: `shmup_trail2` est exporte si present sur disque, et consomme 1 palette meme si pas utilise en runtime.
  Si on veut maximiser la marge, on pourra desactiver son export par defaut.

Si on re-atteint la limite, ajouter de nouveaux sprites demandera:
  - partager des palettes entre assets, ou
  - reduire le nombre d'assets exportes, ou
  - regenerer/ordonner diffremment les exports (en gardant l'absence d'overlap).

### Layout VRAM (regles simples)

Rappel NGPC:
- Tiles 0..31 reserves
- Sysfont BIOS: 32..127

Dans ce shmup:
- Background tiles: base 128 (SCR1)
- HUD bar tiles: base 192 (SCR2)
- Sprites tiles: base 256 (character RAM)

---

## 4) Optimisations perf (hardware-proof)

### Probleme observe (hardware)

Des que tu as:
- 1-2 ennemis + 1 asteroide + tir,
sur hardware reel ca ressemblait a du "bullet time" (lag) si on reecrit trop d'attributs sprites.

### Solution

Pattern retenu:
- Slots sprites fixes.
- Au spawn: `ngpc_sprite_set()` (tile/pal/flags + pos).
- En update: `ngpc_sprite_move()` (pos seulement).
- En anim: `ngpc_sprite_set_tile()` uniquement quand la frame change.
- Player + trainee: meme logique (on evite `ngpc_mspr_draw()` a chaque frame pour le vaisseau).
- Collisions: boucle bullets unique (move + collisions + sprite_move) au lieu de 3 passes separees.
- Enemy3 demi-tour: tables precalculees pour eviter des divisions en boucle.
- Allocation pools: pointeurs d'allocation circulaires (`*_alloc`) pour eviter de scanner 0..N a chaque spawn.
- VRAM writes: skip des `sprite_move` inutiles quand le player/OPTION ne bougent pas (reel gain sur hardware).
- **Option perf (inspire d'une technique visible dans le dump Cotton NGPC)**: backend sprites "shadow+flush".
  - Au lieu d'ecrire sur `0x8800/0x8C00` tout au long du frame, on ecrit dans un buffer RAM, puis on copie en bloc en VBlank.
  - Activation: `make NGP_ENABLE_SPR_SHADOW=1`
  - Implement: `src/gfx/ngpc_sprite.c` + flush dans `src/core/ngpc_sys.c` (VBlank ISR).
  - Protection lag: `main.c` encadre chaque frame avec `ngpc_sprite_frame_begin()/end()`; si un frame deborde sur le VBlank suivant, l'ISR skip le flush plutot que de copier un buffer a moitie update.
- Collisions hot-path: macro 8x8 (`HIT_8_8`) pour eviter l'overhead de call dans la boucle bullets/enemies.
- OPTION: cadence de tir reduite (1 tir sur 2) pour limiter le nombre de bullets actifs et stabiliser le framerate.

### Ce que montre le dump Cotton (Ghidra) / ce qu'on en tire

Dans `04_MY_PROJECTS/decompil/cotton ngpc.txt`, on voit des acces directs a l'OAM NGPC:
- `0x8800` = table attributs sprites (64 sprites * 4 octets).
- `0x8C00` = table "palette index" des sprites (1 octet par sprite).

Patterns visibles:
- Copie en bloc vers `0x8800` avec `ldirw` (copie mots, donc debit plus haut qu'une boucle C):
  - Extrait: `ld XIX,0x8800` puis `ldirw (XIX+),(XIY+)` (ex: autour de `000d8632`).
- Mise a jour "metasprite" via stride 4:
  - Extrait: `ld XIX,0x8800` puis boucle `djnz 4` avec `inc 0x4,XIX` et ecriture sur `(XIX)` (ex: autour de `000d93ad`).
  - Lecture: on remplit rapidement les 4 sous-sprites d'un 16x16 en sautant de 4 bytes (1 entree sprite) a chaque iteration.

Interprétation (ce qu'on applique au template):
- L'idee n'est pas de "copier Cotton", mais de reprendre la technique: ecrire les sprites dans une structure RAM (shadow),
  puis pousser les donnees vers `0x8800/0x8C00` en VBlank, en une ou deux copies contigues.
- Avantage hardware: tu evites de faire 50-200 petites ecritures VRAM-map dans le milieu du frame (souvent la cause du "bullet time").
- Dans notre implementation, on flush uniquement l'intervalle `min_id..max_id` touche (dirty range), pas forcement les 64 sprites.

Comment activer / regler:
```sh
make NGP_ENABLE_SPR_SHADOW=1
```

Fichiers:
- `src/gfx/ngpc_sprite.c`: buffer shadow + dirty range + `ngpc_sprite_flush()`
- `src/core/ngpc_sys.c`: appel de `ngpc_sprite_flush()` en VBlank ISR (avant `ngpc_vramq_flush()`)
- `src/main.c`: `ngpc_sprite_frame_begin()/end()` pour eviter un flush sur un buffer en cours de modif si un frame depasse.

Notes:
- Quand `NGP_ENABLE_SPR_SHADOW=0`, rien ne change: on ecrit directement sur `0x8800/0x8C00` (mode actuel).
- Prochaine etape si tu veux pousser encore plus (optionnel): remplacer la boucle de copie C par une routine ASM TLCS-900 (type `ldirw`),
  pour se rapprocher du debit vu dans Cotton.

Exemple (layout slots):
```c
#define SPR_PLAYER_A_BASE  1u  /* 16x16 -> 4 sprites */
#define SPR_PLAYER_B_BASE  5u  /* 16x16 -> 4 sprites */
#define SPR_BULLET_BASE    9u
#define SPR_EBULLET_BASE   (SPR_BULLET_BASE + MAX_BULLETS)
#define SPR_ENEMY_BASE     (SPR_EBULLET_BASE + MAX_EBULLETS)
#define SPR_FAT_BASE       (SPR_ENEMY_BASE + MAX_ENEMIES)  /* 16x16 -> 4 sprites */
#define SPR_AST_BASE       (SPR_FAT_BASE + 4u)  /* stride=4 */
#define SPR_FX_BASE        (SPR_AST_BASE + (MAX_ASTEROIDS * 4u))
#define SPR_UI_START_BASE  (SPR_FX_BASE + MAX_FX)
```

Resultat:
- Le framerate sur hardware est stable meme quand ca bouge + tirs.
- Ca laisse plus de marge pour augmenter `MAX_ENEMIES` / `MAX_BULLETS` plus tard.
- Note (budget 64 sprites):
  - ajout des bullets ennemies + `ennemi_5_fat` => `MAX_ASTEROIDS` reduit a 2 et `MAX_FX` reduit a 7 pour garder les UI sprites.
  - power-up **OPTION** (Nemesis) = 2 sprites overlay (palette ship A+B) -> consomme 2 slots sprites en permanence.
  - avec un futur HUD en sprites (~7-9), on reste dans les clous tant qu'on garde ces caps (et `MAX_PICKUPS=3`).

---

## 4bis) Fin de run / high scores / continues (etude d'integration)

Objectif:
- Garder un flow arcade propre en fin de partie.
- Ajouter `GAME OVER`, `TRY AGAIN`, `CONTINUE`, `HIGH SCORES` et la saisie de nickname sans casser l'architecture actuelle.

Constat actuel:
- `src/game/shmup.c` gere deja les ecrans de fin de run en interne:
  - `GAME OVER` en sprites
  - `STAGE CLEAR` en system font
- Mais `src/main.c` ne voit le shmup qu'en mode binaire:
  - `shmup_update()` retourne seulement "sortir au menu ou non".
- Ce contrat est trop pauvre pour gerer proprement:
  - retry
  - continue
  - game over detaille
  - transition future vers `Level 2`
  - saisie de nom / insertion high score

Decision d'architecture recommandee:
- Laisser les ecrans de fin de run "proches du gameplay" dans `src/game/shmup.c`.
- Garder l'ecran consultable `HIGH SCORES` comme vrai state applicatif dans `src/main.c`.
- Faire evoluer plus tard `shmup_update()` pour renvoyer un vrai code de sortie, par exemple:
  - retour menu
  - retry
  - stage clear / next stage
  - score a enregistrer

Flow recommande:
1. Mort sans vies -> ecran `CONTINUE?` si au moins 1 continue restant.
2. Si refuse ou plus de continue -> ecran `GAME OVER` avec score.
3. Ecran `GAME OVER` avec options:
   - `TRY AGAIN`
   - `MENU`
4. Si score eligible top 10:
   - lancer `NAME ENTRY`
   - sauvegarder
   - puis appliquer la sortie choisie

Important:
- `TRY AGAIN` != `CONTINUE`
- `TRY AGAIN`:
  - nouvelle partie propre
  - score remis a zero
- `CONTINUE`:
  - consomme un credit
  - reprend la run en cours

Continues:
- Bonne option produit:
  - 3 continues par defaut
  - reglable dans les options de `0` a `10`
- Point de design a trancher:
  - soit un score avec continue entre dans le top 10
  - soit il est exclu (recommande pour garder un tableau "arcade")
  - variante: l'accepter mais le marquer visuellement

High scores / nickname:
- Recommandation NGPC:
  - initiales 3 lettres plutot qu'un nom long
  - saisie au pad beaucoup plus lisible / rapide
- Un ecran `HIGH SCORES` peut vivre dans `main.c` sans pression temps reel:
  - rang
  - score
  - initiales
  - eventuellement un marqueur `NEW`

Save system:
- Le backend `ngpc_flash` deja present suffit largement (256 bytes).
- Il faut construire au-dessus une save dediee au shmup avec:
  - magic
  - version
  - options persistantes
  - top 10
  - checksum simple
- Important:
  - ne pas sauvegarder trop souvent
  - ecrire seulement quand une option change ou quand un score est valide

Contraintes techniques identifiees:
- Le score actuel est en `u16`; a reevaluer si le jeu s'allonge beaucoup.
- Le menu actuel n'a que `START` / `OPTIONS` en art fixe:
  - le plus simple est d'ajouter `HIGH SCORES` d'abord depuis `OPTIONS`
  - puis plus tard de refaire le visuel du menu principal si besoin
- Le futur `Level 2` pousse dans le meme sens:
  - il faudra de toute facon sortir du simple retour bool de `shmup_update()`

---

## 5) HUD / zones interdites (important)

Le HUD bar est en bas (16 px). Le gameplay est clamped pour ne pas passer dessus:

```c
#define HUD_BAR_H_PX 16
#define PLAYFIELD_H_PX (152 - HUD_BAR_H_PX)
```

Regle:
- pas de player/enemy/asteroid sur la zone du HUD bar.

Score / HUD text:
- Systeme font (`ngpc_text_print*`): desactive en gameplay (artefacts observes sur hardware).
- HUD en **sprites 8x8** (economique, pixel-perfect):
  - Score: 5 sprites digits (0-9) aux pixels `x=114..146, y=140` (5 chiffres).
    - slots sprites: `SPR_HUD_SCORE_BASE..+4` (reutilise la zone GAME OVER)
    - tiles: `GraphX/ui_digits_mspr.c/.h`
  - "Roue" / curseur power-up (1 seul sprite): positions (pixels) `x={26,38,50,62,74}, y=140`
    - affiche S/M/D/L/O selon `s_pu_cursor`, et se deplace d'une case a chaque capsule ramassee
    - tile: `GraphX/ui_powerup_mspr.c/.h`
  - Palette: digits + power-up letters partagent la palette verte de `ui_start` (pas de palettes sprites en plus).

---

## 6) Vagues et patterns d'ennemis

### Spawn / timing

Constantes actuelles (apres tuning 2026-02-22):
```c
#define STAGE_INTRO_NO_SPAWN_FRAMES 300u  /* 5s */
#define AST_MAX_ACTIVE              2u
#define ENEMY1_FLAT_FRAMES          6u    /* etait 8 */
#define ENEMY1_STEP_FRAMES          5u    /* etait 6 */
/* Dans shmup_reset_gameplay() : */
s_scroll_speed = 2;  /* etait 1 — influence le timing SEC() */
```

- No-spawn pendant 5 secondes (`STAGE_INTRO_NO_SPAWN_FRAMES = 300`).
- Les vagues sont maintenant scriptées (Nemesis-style) via une timeline deterministe:
  - moteur: `src/game/stage.c` + `src/game/stage.h`
  - script: `s_stage1[]` dans `src/game/shmup.c`
- Timing stage: `SEC(s)` = `s * 60` pixels de scroll. Avec `scroll_speed=2`, `SEC(s)` = `s*30` frames reels.
  - `SEC(5)` = 2.5 secondes reelles, `SEC(6)` = 3 secondes reelles.
- Rythme du level 1 (s_stage1[]):
  - **Act 1** (intro): 5 vagues, alternance haut/bas/centre, intervals SEC(5..6), spawn interval 7-9, 0 asteroide.
  - **Act 2** (pression): 5 vagues + 3 asteroides (1 seul, puis 2 simultanees en couloir), intervals SEC(3..4), spawn interval 6-8.
  - **Act 3** (respiration): 3 vagues, SEC(5), 0 asteroide.
  - **Act 4** (pression max): 5 vagues + 5 asteroides (2 simultanees puis 3 en sequence), intervals SEC(3), spawn 5-7.
  - **Pre-boss**: STG_SET_SPEED(1) + 2 vagues + 1 asteroide isole, intervals SEC(4..5).
- Les vagues sont declenchees par la progression du scroll (delay en pixels), ce qui est Nemesis-style:
  - a vitesse stable, c'est equivalent au temps (60 fps)
  - si on change la vitesse de scroll plus tard (transition boss, etc.), le timing s'adapte mais les spawns restent colles au decor
- Chaque event declenche une vague avec (type, count, spacing, center_y), puis le spawn est etale (timer) pour lire la formation.

Regles asteroides dans le script:
- Act 1 et 3: **0 asteroide** (lisibilite, respiration)
- Act 2 debut: **1 asteroide** isole
- Act 2 fin: **2 simultanees** (haut + bas = couloir centre)
- Act 4 debut: **2 simultanees** (vitesses differentes)
- Act 4 milieu: **3 en sequence** (passage dangereux)
- Pre-boss: **1 seul** (milieu ecran)

### Vitesses distinctes par type

```c
#define ENEMY1_VX     (-1)
#define ENEMY2_VX     (-2)
#define ENEMY3_VX_IN  (-2)
#define ENEMY3_VX_OUT (2)
```

### Type 1 (ennemi_1) - escalier smooth

Probleme:
- "saccade" si on fait des sauts de 6 px d'un coup.

Solution:
- Alternance "flat" + "step" avec steps a 1 px/frame (beaucoup plus smooth).

### Type 2 (ennemi_2) - file indienne + vague

Solution:
- Sinus autour d'un `base_y` avec un `phase` different par ennemi (file indienne).

### Type 3 (ennemi_3) - demi-tour en arc + shift vertical

Objectif:
- arrive en file indienne (alignes).
- a gauche: demi-tour en arc (pas un angle sec).
- pendant l'arc: monte/descend de 2-3 tiles selon la position (haut/bas).
- ensuite: repart vers la droite (HFLIP).

Implementation:
- `phase=0`: arrive a gauche
- `phase=1`: turning (arc + drift vertical)
- `phase=2`: repart a droite

Note importante (template sans system.lib):
- Eviter les ops 32-bit (division/mul long), sinon le link cherche `C9H_divls`/`C9H_mulls`.
- Le turn utilise donc une interpolation en 16-bit.

### Type 5 (ennemi_5_fat) - "fat" stopper + tir

- Sprite: `GraphX/tile 8x8/sprites/ennemi_5_fat.png` (16x16).
- Palette: partagee avec la trainee (`shmup_trail`) pour economiser des palettes sprites.
- Spawn: auto, uniquement sur "petites vagues" (count <= 6) et si aucun fat n'est deja vivant.
- Comportement:
  - arrive depuis la droite, puis stop a `x ~= 136`
  - HP=3 (3 tirs)
  - tire un projectile toutes les 1 seconde (60 frames)
  - 1 seul a la fois

---

## 7) Asteroides (non destructibles) - plus rares

Objectif:
- Pas oblige d'en avoir tout le temps a l'ecran.
- Servent d'obstacles statiques (non destructibles).

Tuning (dans `src/game/shmup.c`):
- Cap max actif: 2 (`MAX_ASTEROIDS=2`, `AST_MAX_ACTIVE=2`)
- Les asteroides sont declenches par le stage script (events `STG_AST`).
- Le cap max actif evite la saturation si plusieurs events tombent trop vite.

### Palette unique (important pour drops/UI)

Les 5 sprites d'asteroides utilisent la meme palette (meme couleurs). Du coup:
- on force **1 seule palette sprite partagee** pour `shmup_ast1..shmup_ast5`
- on charge uniquement `shmup_ast1_palettes` au runtime

Changements:
- `tools/shmup_export_sprites.py`: `export_sheet_reuse_palette(...)` pour `shmup_ast2..5`
- `src/game/shmup.c`: on ne charge plus `shmup_ast2..5_palettes` (gain net: 4 slots palettes)

---

## 8) UI sprites: START / GAME OVER

- START: affiche au debut, puis hide apres un timer.
- GAME OVER: affiche en fin, hide START si besoin.
  - En game over: on fait exploser le vaisseau puis on efface bullets/ennemis/asteroides/pickups/fat (ne reste que le background + FX + banner).

Ces elements sont des sprites (metasprites) pour etre independants des tilemaps.

---

## 9) Power-ups (SPEED + SHIELD + OPTION)

Objectif:
- Ajouter des power-ups sans encore implementer les drops ennemis.
- Garder la perf (1 sprite 8x8 par pickup).

Assets (exports):
- `GraphX/shmup_drop_final_mspr.c/.h` (capsule a ramasser)
  - palette partagee avec `ui_start` (meme couleurs) pour economiser des palettes sprites
- `GraphX/shmup_option_a_mspr.c/.h` + `GraphX/shmup_option_b_mspr.c/.h` (OPTION)
  - 2 sprites overlay car l'asset utilise des couleurs presentes sur les 2 palettes ship (A+B)

HUD:
- Texte HUD (system font) actuellement desactive (voir section 5).

Runtime (dans `src/game/shmup.c`):
- `s_player_speed_px`: vitesse actuelle (2..4 selon `s_player_speed_level`)
- `s_shield_hits`: 1 hit absorbe (ne decremente pas les vies)
- `s_option_active`: OPTION actif (satellite)
- `Pickup[]`: entites pickups + collision player
- Mode Nemesis-style (roulette):
  - ramasser une capsule = avance la selection d'une case (S->M->D->L->O)
  - affichage: 1 seul sprite "lettre" se deplace sur le HUD (voir section 5)
  - `B` = active le power-up selectionne (puis reset la selection)
    - actuellement:
      - `S` = SPEED
      - `O` = OPTION (satellite Nemesis: suit le vaisseau avec delai et tire en meme temps)
        - si OPTION deja actif: `O` donne un SHIELD 1-hit (temporaire)
        - Note: le follow est base sur les deltas de mouvement; quand le joueur s'arrete, on flush la file de deltas pour que l'OPTION s'arrete immediatement aussi (pas de "rattrapage").

Drop rule (Nemesis-like):
- une vague d'ennemis entierement eliminee => le dernier ennemi tue lache une capsule
- si un ennemi de la vague sort de l'ecran (rate), pas de capsule pour cette vague

Important (limite 64 sprites):
- Les pickups re-utilisent les slots sprites de `START` (indices 49..)
  car START/GAME OVER occupent a eux seuls la fin de la sprite RAM.
- Dans le code actuel, on a limite `MAX_PICKUPS` a 3 (rarement plus de 2-3 a l'ecran).

Test (temporaire):
- En build debug (`NGP_ENABLE_DEBUG=1`), `OPTION+UP` spawn une capsule de test apres l'intro.

---

## 10) Son / musique (NGPC Sound Creator - export hybride)

### Integration runtime

- `Sounds_Init()` une fois au boot (dans `src/main.c`).
- `Sounds_Update()` une fois par frame (apres vsync/input).
- La musique est fournie par `sound/sound_data.c` qui inclut l'export:

```c
#include "sound_data.h"
#include "battle_1.c"
#include "project_sfx.c"
/* Note: cc900 exige un init constant global, donc SFX_COUNT est un literal. */
const u8 SFX_COUNT = 4u;
```

### SFX (project_sfx.c)

- Fichier: `sound/project_sfx.c` (export NGPC Sound Creator)
- Inclusion: `sound/sound_data.c` inclut `project_sfx.c` pour linker les tables
- Si tu changes `PROJECT_SFX_COUNT`, pense a mettre a jour `SFX_COUNT` (literal) dans `sound/sound_data.c`
- Mapping ids: `src/audio/sfx_ids.h`
- Lecture des tables + `Sfx_Play(id)`: `src/audio/sounds_game_sfx.c`
- Activation du mapping externe: `-DSFX_PLAY_EXTERNAL=1` (dans `makefile`)
- Triggers actuels:
  - Tir player: `Sfx_Play(SFX_TIR_VAISSEAU)`
  - Explosion (kill ennemi): `Sfx_Play(SFX_EXPLOSION)`
  - Menu: `SFX_MENU_MOVE` / `SFX_MENU_SELECT`

### Pourquoi le driver a ete ajuste (important a porter)

Dans `NGPC_SOUND_CREATOR/driver_custom_latest`, le driver "canonical" utilise `VBCounter` (BIOS).
Dans ce template, on a une ISR VBlank custom + un compteur `g_vb_counter`.
Donc le driver dans `src/audio/sounds.c` doit rester base sur `g_vb_counter` pour etre frame-locked.

### Problemes entendus (differences) + solution

Avec l'export hybride, `sound/battle_1_instruments.c` reference:
- `env_curve_id` jusqu'a 2
- `pitch_curve_id` jusqu'a 7
- `macro_id` jusqu'a 3

Si le driver n'a pas assez de courbes/macros, ces ids tombent hors-range -> rendu different.

Solution appliquee dans `src/audio/sounds.c`:
- Ajout d'un `env_curve_id = 2`.
- Ajout de `pitch_curve_id = 5..7` (aliases des courbes existantes pour matcher les ids exportes).
- Ajout de placeholders macros 2..3 (count=0) pour ne pas sortir de table.

---

## 10) Problemes rencontres + solutions (liste utile)

### A) Lag / "bullet time" en tirant
- Cause: trop de `ngpc_sprite_set()` par frame.
- Fix: slots fixes + `ngpc_sprite_move()` (move-only) + tile update seulement quand necessaire.

### B) Artefacts "START" qui se baladent a l'ecran
- Cause: collision VRAM (tile_base/pal_base) quand la liste d'assets exportes changeait.
- Fix:
  - export sprites deterministe (asteroides + UI exportes par defaut).
  - regen sprites apres ajout/retrait d'assets.

### C) Deplacement ennemi_1 pas joli (saccade)
- Cause: sauts verticaux trop grands et trop espacés.
- Fix: "escalier" smooth (1 px/frame pendant une courte phase).

### D) Link errors `C9H_divls` / `C9H_mulls`
- Cause: ops 32-bit (division/mul long) sans system.lib.
- Fix: eviter 32-bit dans le gameplay, faire l'interpolation en 16-bit.

### E) Chiffres/texte qui flash en haut (score, etc.)
- Constat: sur hardware, des digits pouvaient apparaitre brievement en haut (ex: apres un kill).
- Fix (temporaire): desactivation totale du rendu system font en gameplay (plus aucun `ngpc_text_print*` dans `shmup.c`).
- Fix actuel: HUD en sprites 8x8 (digits + roue power-up) + correction du bug u8 overflow dans `ngpc_gfx_put_tile` (voir G).

### F) (Legacy) Score SCR2 (system font) — obsolete
- Note: plus utilise (HUD en sprites). Garde ici uniquement pour historique/debug.

### G) Score position incorrecte (haut-droite au lieu de bas-droite) — RESOLU
- Constat: tile (14, 17) s'affiche "en haut a droite" sur emulateur ET hardware reel (portrait standard).
- Investigation:
  - `put_tile(plane, x=14, y=17)` devrait ecrire `SCR2_MAP[17*32+14] = SCR2_MAP[558]`.
  - La hudbar (qui s'affiche correctement en bas) utilise dans `hudbar_blit_scr2`:
    `(u16)(y + y_off_tiles) * 32u + (u16)x` — cast u16 explicite.
  - `ngpc_gfx_put_tile` utilise: `map[y * SCR_MAP_W + x]` ou `y` est `u8`.
  - Avec cc900, `u8 * int_literal` peut rester en arithmetique 8-bit:
    y=17: `17 * 32 = 544` → tronque en u8 = **32** → `map[32 + 14] = map[46]` = ligne 1, colonne 14 = **haut-droite**.
  - Cela explique aussi le bug historique "chiffres flashaient en haut" (tout y >= 8 etait affecte).
- Cause racine: **overflow u8 dans `ngpc_gfx_put_tile`** (et `put_tile_ex`, `get_tile`).
- Fix applique (2026-02-22) dans `src/gfx/ngpc_gfx.c`:
  ```c
  /* Avant (BUG): */
  map[y * SCR_MAP_W + x] = make_entry(...);
  /* Apres (FIX): */
  map[(u16)y * SCR_MAP_W + x] = make_entry(...);
  ```
  Les trois fonctions `put_tile`, `put_tile_ex`, `get_tile` ont ete corrigees.

---

## 11) Changements importants a porter dans ton template de base

### 1) Driver son (hybride) + docs
- Clarifier le point `VBCounter` (BIOS) vs `g_vb_counter` (ISR custom).
- Supporter les ids hybrides (courbes + macros) dans le driver:
  - env_curve_id: 0..2
  - pitch_curve_id: 0..7 (ou au moins mapper 5..7)
  - macro_id: 0..3 (placeholders OK si macros non utilisees)
- Garder une section "integration quickstart" type `NGPC_SOUND_CREATOR/driver_custom_latest/INTEGRATION_QUICKSTART.md`.
- Pattern SFX propre (a porter):
  - garder `src/audio/sounds.c` clean, et implementer `Sfx_Play(id)` dans un fichier game (`src/audio/sounds_game_sfx.c`)
  - activer via `-DSFX_PLAY_EXTERNAL=1`
  - si utilisation NGPC Sound Creator: inclure `sound/project_sfx.c` dans `sound/sound_data.c` + exposer des ids (`src/audio/sfx_ids.h`)

### 2) Export metasprites: `--pal-base` + export deterministe
- Avoir un export "bundle" (comme `tools/shmup_export_sprites.py`) qui:
  - incremente `tile_base` et `pal_base`,
  - refuse depassement (tiles > 512, palettes > 16),
  - genere des `*_pal_base` dans les headers,
  - exporte toujours le meme set d'assets (sinon collisions possibles).

### 3) Pattern perf officiel: sprite slots fixes
- Mettre dans la doc/template un exemple officiel:
  - spawn => `set`
  - update => `move`
  - anim => `set_tile` uniquement quand frame change

### 4) Pattern HUD: SCR2 overlay + clamp playfield
- Recommandation pour shmup/platformer:
  - HUD sur SCR2
  - clamp gameplay pour ne jamais passer sur la zone UI

---

## 12) TODO si on continue

- Tirs ennemis (ennemi_shoot_t_*) + patterns de tir.
- Power-ups: implementer les slots M/D/L (missile/double/laser) + drops (capsules).
- Script de stage (timeline) pour mini-boss/boss (STG_WAIT_CLEAR avant boss, STG_SET_SPEED).
- Palettes: on etait a 16/16, mais les asteroides partagent maintenant 1 seule palette (gain: 4 slots).
- Affichage vies (s_lives) sur le HUD une fois le score valide.

---

## 13) Derniers changements (2026-03-08)

### Tuning stage / rythme

- Les vagues ont ete legerement espacees via le moteur de stage (`src/game/stage.c`).
- Le niveau 1 contient maintenant deux passages "dodge" sans ennemis, centres sur les asteroides.
- Les asteroides ont ete redistribues pour mieux varier les couloirs d'esquive.

### Ennemi type 3 (`ennemi_3`)

- Le pattern a ete remis en etat:
  - entree franche vers la gauche,
  - demi-tour en arc,
  - shift vertical pendant l'arc,
  - sortie vers la droite.
- Une tentative intermediaire avec une "ondulation" d'entree a ete retiree car elle donnait visuellement l'impression que l'ennemi restait sur place.

### Boss niveau 1

- Nouveau sprite boss integre:
  - source: `GraphX/tile 8x8/sprites/boss-1.png`
  - export runtime: `GraphX/shmup_boss1_mspr.c/.h`
- Le boss arrive maintenant dans une boss room dediee:
  - fin de script stage,
  - scroll stoppe,
  - entree du boss depuis la droite,
  - placement fixe avant le combat.
- Boss actuel:
  - HP = 15
  - hit flash simple
  - phase 2 sous 50% HP
  - plusieurs patterns de tir:
    - salve visee
    - petit fan
    - "mur" / ligne de tirs decales
  - sequence de mort avec explosions successives

### Ecran post-boss / fin de stage

- Un ecran `STAGE CLEAR` a ete ajoute.
- Contenu affiche:
  - score de base,
  - bonus de vies restantes,
  - total,
  - `PRESS A TO CONTINUE`
- Pour l'instant, `A` renvoie au menu principal.
- Le rendu texte ici utilise volontairement la system font, car cet ecran est statique (hors gameplay) et n'est donc pas soumis aux memes contraintes que le HUD temps reel.

### Roadmap vitrine template 2026

- Ajout dans `ROADMAP.md` d'un bloc dedie:
  - high scores,
  - save flash persistante,
  - top 10,
  - ecran `HIGH SCORES`,
  - preparation de la transition vers un niveau 2.

### OPTION (suivi / tir / lisibilite)

- L'OPTION a ete recadree pour suivre la vraie trajectoire du joueur avec un delai lisible, au lieu d'un comportement flou.
- Version retenue actuellement:
  - suivi base sur un historique de positions absolues,
  - lecture avec delai fixe,
  - l'OPTION ne bouge que si le joueur a vraiment bouge,
  - a l'arret du joueur, l'OPTION s'arrete aussi et ne "rattrape" pas sa position,
  - tir simple uniquement, meme si le joueur est en `DOUBLE`.
- Reglage actuel:
  - petit offset visuel local (`+4,+4`) par rapport au vaisseau,
  - distance principalement obtenue par le delai (`OPTION_DELAY_FR`), pas par un gros decalage artificiel en X.
- Tuning:
  - le delai a ete monte pour mieux lire la separation joueur/OPTION,
  - puis legerement redescendu pour rapprocher un peu le satellite sans casser la logique du suivi.

### HUD vies

- Une premiere representation des vies a ete ajoutee en HUD sprite:
  - source: `GraphX/tile 8x8/sprites/barre_vie.png`
  - export runtime: `GraphX/ui_lifebar_mspr.c/.h`
- Placement actuel:
  - `x=5, y=140`
  - `x=11, y=140`
  - `x=17, y=140`
- Le HUD affiche 3 icones, alignees sur le cap de vies actuel du joueur (`PLAYER_INITIAL_LIVES = 3`).
- La palette retenue est la meme que celle du tir joueur, pour rester coherente visuellement et eviter une palette sprite de plus.
- Cette integration sert de base en attendant une vraie barre/gestion HP finale.

### Power-ups (etat actuel)

- Le meter utilise maintenant uniquement les slots actuellement jouables:
  - `SPEED`
  - `REFILL LIFE`
  - `DOUBLE`
  - `PIERCE`
  - `OPTION`
- La capsule fait maintenant tourner la selection entre `S -> P -> D -> L -> O`.
- Le meter graphique est maintenant aligne sur cette logique:
  - `P` remplace l'ancien `M`,
  - `L` sert maintenant au `REFILL LIFE`.
- Progression retenue:
  - `SPEED x4`
  - `DOUBLE x2`
  - `PIERCE x1`
  - `OPTION x1`
- Detail:
  - `SPEED` utilise maintenant 4 paliers plus fins, au lieu d'un simple bond brutal de vitesse.
  - `DOUBLE 1` = tir double, degats x2.
  - `DOUBLE 2` = tir double, degats x3.
  - `PIERCE` fait traverser les petits ennemis aux tirs du joueur, mais pas les gros ennemis, le boss ni les asteroides.
  - `REFILL LIFE` rend `+1` vie et refuse l'activation si la reserve est deja pleine.
  - `OPTION` reste limitee a 1, avec tir simple uniquement.
- Le cap de vies actuel est maintenant fixe a `3`, ce qui aligne:
  - le gameplay,
  - le HUD 3 icones,
  - le `REFILL LIFE`.
- Quand un slot est deja maxe:
  - il reste selectionnable dans la rotation,
  - `B` ne le consomme pas,
  - la lettre clignote brievement,
  - un petit SFX de refus est joue.
- Un vrai SFX de capsule (`catch_power_up`) est maintenant joue a la collecte.

---

## 14) Problemes recents rencontres + solutions

### H) Regression massive UI/HUD/START/pickups apres ajout du boss

Symptomes observes:
- banner `START` tronquee,
- HUD score corrompu,
- pickups affichant de mauvais digits,
- OPTION visuellement "sombre" ou incoherent.

Cause racine:
- Le boss en lui-meme n'etait pas le vrai probleme.
- La regression venait du **layout global des slots sprites**:
  - augmentation de `MAX_EBULLETS`,
  - augmentation de `MAX_ASTEROIDS`,
  - donc decalage en cascade de `SPR_ENEMY_BASE`, `SPR_FAT_BASE`, `SPR_AST_BASE`, `SPR_FX_BASE`, `SPR_UI_START_BASE`, `SPR_UI_GAMEOVER_BASE`.
- Comme le HUD, START, GAME OVER et pickups reutilisent des plages fixes de sprite RAM, tout decalage de cap casse leur mapping.

Solution retenue:
- Remettre les caps globaux "safe":
  - `MAX_EBULLETS=4`
  - `MAX_ASTEROIDS=2`
  - `AST_MAX_ACTIVE=2`
- Garder le boss en boss room, mais **sans** changer le budget global du shmup standard.
- Compacter les patterns boss pour rentrer dans ce budget.

Regle a retenir:
- Sur NGPC, une boss room peut reutiliser des slots liberes,
  mais il ne faut pas changer a la legere les caps globaux si l'UI depend d'un layout sprite fixe.

### I) Export du boss et limite des 16 palettes sprites

Symptome:
- `tools/shmup_export_sprites.py --all` finissait par depasser la limite palette NGPC.

Cause:
- Le bundle `--all` exporte aussi des assets "bonus" non utilises en runtime.
- Une fois tous les assets supplementaires inclus, `pal_base` sort de la plage `0..15`.

Solution retenue:
- Export du boss **separement** au lieu de regenirer tout le bundle `--all`.
- Utilisation d'une palette libre (`pal_base=15`) pour `shmup_boss1`.
- Remap leger d'une couleur d'accent du PNG boss pour retomber dans une palette 3 couleurs + transparence compatible NGPC.

Lecon:
- Pour les nouveaux gros sprites, preferer un export cible et verifier explicitement:
  - tile budget,
  - palette budget,
  - compatibilite avec le layout runtime.

### J) Pattern `ennemi_3` qui semblait "bouger sur place"

Symptome:
- Les `ennemi_3` ondulaient mais donnaient l'impression de rester au meme endroit.

Cause:
- Une tentative de wobble vertical d'entree nuisait a la lisibilite du pattern.
- Le feedback visuel du mouvement horizontal etait trop faible par rapport a l'oscillation.

Solution:
- Suppression du wobble d'entree.
- Retour a une entree simple et lisible avant le demi-tour en arc.
- Conservation du shift vertical pendant l'arc, qui reste la signature du pattern.

### K) Texte system font : interdit en gameplay, acceptable hors gameplay

Rappel:
- En gameplay, le system font a deja provoque des artefacts/hud glitches sur hardware.

Decision actuelle:
- HUD gameplay = sprites 8x8 uniquement.
- Ecran `STAGE CLEAR` = system font autorisee, car:
  - ecran statique,
  - pas de saturation sprite,
  - pas de mise a jour temps reel du HUD.

Regle:
- Continuer a eviter `ngpc_text_print*` pendant l'action.
- Utiliser la system font pour les ecrans calmes:
  - stage clear,
  - high scores,
  - options simples,
  - ecrans inter-niveaux.

### L) OPTION: suivi vertical correct mais horizontal faux

Symptome:
- Verticalement, l'OPTION suivait bien.
- Horizontalement, elle semblait mal suivre ou "dormir" dans une trajectoire qui ne correspondait pas a celle du joueur.

Causes successives identifiees:
- Premiere version problematique:
  - l'OPTION lisait un historique alimente a chaque frame, meme quand le joueur ne bougeait pas,
  - resultat: elle finissait par rattraper le joueur a l'arret.
- Deuxieme version problematique:
  - un gros decalage fixe en X etait utilise pour creer de la distance,
  - resultat: l'OPTION suivait une trajectoire artificiellement decalee, surtout visible sur les deplacements horizontaux.

Solution retenue:
- Historique de positions absolues, mis a jour uniquement quand la cible change vraiment.
- Pas de "rattrapage" a l'arret.
- Distance geree par le delai de lecture de l'historique, pas par un gros offset X.
- Offset local garde tres faible pour caler visuellement le sprite de l'OPTION sur le vaisseau.
- Tir de l'OPTION force en tir simple, pour eviter le chaos visuel quand le joueur passe en `DOUBLE`.

Regle a retenir:
- Si le suivi horizontal redevient bizarre, ne pas compenser avec un gros offset X.
- Le bon levier de tuning est d'abord `OPTION_DELAY_FR`.

### M) Barre de vie HUD: bon placement, mais regression sur `START`

Symptome:
- La barre de vie etait bien placee,
- mais le banner `START` redevenait corrompu (seul le `S` restait visible).

Cause:
- Le probleme ne venait ni du PNG, ni de la palette.
- Les 3 sprites de vies occupaient des slots qui se superposent a la zone reutilisee par `START`.
- Pendant l'intro, `hud_sprites_hide()` cachait aussi ces slots, donc effacait une partie de `START`.

Solution retenue:
- Garder les icones de vies dans cette zone reutilisable, mais ne plus les manipuler comme du HUD actif pendant le timer `START`.
- Cacher explicitement les sprites de vies au reset/init.
- Eviter de les rehider via `hud_sprites_hide()` tant que `START` est encore affiche.

Lecon:
- Sur ce projet, plusieurs elements UI reutilisent volontairement les memes slots sprites.
- Toute nouvelle brique HUD doit etre verifiee non seulement en gameplay, mais aussi pendant:
  - intro `START`
  - pickups
  - `GAME OVER`

### N) Palette de la barre de vie

Constat:
- `barre_vie.png` utilise les memes verts que le tir joueur.

Decision:
- Re-exporter `ui_lifebar` sur la palette du tir joueur plutot que garder une palette dediee.

Interet:
- coherence visuelle immediate,
- economie de palettes sprites,
- moins de risque de pression inutile sur le budget palette global.

### O) Note DMA pour le futur niveau 2

Contexte:
- Un prototype DMA a ete valide dans `Shmup_StarGunner - Copie` pour donner au fond
  du stage 1 un wobble par scanline.
- La cible visuelle venait du homebrew:
  `NGPC_Template__2026 - test_dma-raster`,
  surtout le rendu du mode `AUTO-REARM u16 XY (INTTC0)`.

Essais utiles faits dans la copie:
- `u32` stream: pas le bon rendu pour ce fond.
- `auto-rearm u16 XY + ping-pong + safe start`: integration propre, mais pas d'effet visible
  dans le vrai gameplay du shmup.
- Symptome cle:
  - si on coupait le fallback CPU, on perdait meme le scroll de base,
  - donc la formule visuelle n'etait pas le vrai probleme, c'etait le declenchement
    de l'auto-rearm dans ce contexte de jeu.

Solution retenue dans la copie:
- Revenir sur un mode stable:
  - `u16 packed XY`
  - `ngpc_dma_raster_xy_begin()/enable()/rearm()/disable()`
  - `rearm` manuel juste apres `ngpc_vsync()`
- Garder une formule visuelle proche du test app:
  - wobble horizontal principal
  - wobble vertical plus faible
  - table packed `Y << 8 | X`
- Repeter les lignes du fond tilemap sur `SCR1`,
  sinon le wobble vertical montre les lignes non initialisees du plan.

Tuning final retenu dans la copie:
- amplitude: `64`
- frequence: `8`
- phase step: `1`
- composante verticale reduite a `amp/4`

Regle a retenir:
- Pour un vrai niveau jouable, partir d'abord sur `u16 XY + rearm manuel`.
- Ne retenter `auto-rearm INTTC0` que plus tard, quand le rendu manuel est stable
  et si on veut fermer la fenetre de rearm.

Usage prevu:
- Cette piste DMA est une bonne candidate pour le fond du `Level 2`,
  pas necessairement pour retrofiter tout le niveau 1 principal.

### P) Ecrans noirs system font: continue / game over final / name entry

Contexte:
- Le flow de fin de run a ete remplace par de vrais ecrans texte:
  - `CONTINUE`
  - `GAME OVER`
  - `NAME ENTRY`
  - `STAGE CLEAR`
- Tous ces ecrans utilisent un fond noir + texte jaune via la system font.

Ce qui a ete ajoute:
- `CONTINUE` avec compteur de continues restants et choix `YES / NO`
- `GAME OVER` final avec score
- saisie de nickname sur 3 caracteres + `OK`
- sauvegarde du score puis retour menu
- `HIGH SCORES` reel dans le menu principal/options
- option `CONTINUES` persistante via la save flash

Regle de structure retenue:
- la preparation lourde de l'ecran (`clear`, palettes, scroll a zero, hide sprites, stop BGM)
  doit etre faite une seule fois a l'entree de l'ecran
- les changements de selection/curseur ne doivent redessiner que le texte utile

Pourquoi:
- refaire un `clear + redraw complet` a chaque input provoquait un clignotement tres laid.

### Q) Scroll parasite sur les ecrans de fin

Symptome:
- sur `GAME OVER` / `CONTINUE`, le texte pouvait encore "scroller" avec le fond
  au lieu de rester fixe.

Cause:
- le gameplay etait bien coupe, mais l'etat de scroll global restait encore actif.

Solution retenue:
- figer explicitement le scroll dans la preparation des ecrans de fin:
  - `s_scroll_speed = 0`
  - `s_scroll_x = 0`

Lecon:
- pour tout ecran texte plein ecran hors gameplay, ne pas se contenter de cacher les sprites:
  il faut aussi neutraliser le scroll du stage.

### R) Clignotement des ecrans noirs texte

Symptome:
- chaque changement de choix (`YES/NO`) ou de lettre dans le nickname faisait
  clignoter l'ecran une fois.

Cause:
- l'ecran etait reprepare entierement a chaque update:
  - clear du plan
  - reset scroll
  - reaffichage complet

Solution retenue:
- separer:
  - `prepare once on enter`
  - `redraw light on update`
- les fonctions `draw_*` ne doivent plus rappeler la preparation lourde.

Resultat:
- navigation beaucoup plus propre
- ecrans de fin plus stables visuellement

### S) Ajustements d'alignement menu / high scores / game over

Corrections appliquees:
- `OPTIONS` recentre et bloc texte decale vers la gauche pour mieux equilibrer l'ecran
- titre `HIGH SCORES` du menu recentre
- `PRESS A` du `GAME OVER` final recentre

Pourquoi:
- avec la system font sur fond noir ou fond fixe, le moindre decalage se voit tout de suite
- ces ecrans ont peu d'elements, donc l'alignement devient une grosse partie du polish

Regle:
- sur les ecrans systeme simples, verifier visuellement:
  - centrage du titre
  - centrage du prompt principal
  - equilibre global gauche/droite

### T) Save flash: bug hardware reel + durcissement

Le probleme de save n'etait pas seulement un probleme de `checksum`.
Sur hardware reel, deux points etaient dangereux:

- une ecriture flash pouvait partir des le boot si la save etait absente ou invalide;
- le wrapper `ngpc_flash` travaillait dans le bloc `0x1F`, c'est-a-dire le
  dernier bloc flash de 64 KB, alors que la spec SDK rappelle que les
  derniers `16 KB` de cartouche sont reserves systeme.

Corrections retenues:

- `shmup_profile_init()` ne fait plus jamais de commit flash automatique au demarrage;
- les options modifient d'abord la save en RAM, puis la flash n'est ecrite
  qu'en sortie d'ecran `OPTIONS`;
- l'insertion de high score reste un vrai point de commit, car c'est un
  evenement rare et explicite;
- `ngpc_flash` a ete deplace sur le bloc `0x1E`, dedie entierement a la save,
  pour ne plus effacer le bloc haut sensible;
- `ngpc_flash_save()` fait maintenant une verification en lecture apres write
  et retente plusieurs fois en cas d'echec.

Regle de travail retenue:

- ne jamais faire de save flash "par confort" au boot;
- considerer le save comme une operation lourde et rare;
- reserver un bloc flash complet au save, meme si seulement `256 bytes`
  sont utilises effectivement, car l'effacement se fait au niveau bloc.

### U) Save flash: bilan hardware reel actuel

Etat actuel:
- le jeu boote correctement sur hardware reel;
- modifier `CONTINUES` dans `OPTIONS` ne fait plus eteindre la console;
- en revanche, aucune persistance n'est observee apres reboot:
  - score a `0`
  - continues revenus a la valeur par defaut

Ce qui a ete essaye:

1. Ecriture flash au boot retiree
- avant: `shmup_profile_init()` pouvait ecrire une save par defaut si la save
  etait absente/invalide;
- symptome: extinction hardware au lancement;
- resultat: retire -> boot redevenu stable.

2. Save differree cote jeu
- options modifiees d'abord en RAM;
- commit flash seulement en sortie de `OPTIONS`;
- high score garde comme commit explicite;
- resultat: plus sain, mais pas de persistance constatee.

3. Variante backend `0x1E0000`
- offset `0x1E0000`
- bloc `30`
- variante inspiree d'anciens exemples/template;
- symptome: sur la cart cible, cette variante etait plus dangereuse et a pu
  conduire a une corruption obligeant a reflasher.

4. Variante backend `0x1FA000`
- offset `0x1FA000`
- bloc BIOS `0x21`
- variante reprise par plusieurs homebrews NGPC;
- resultat: plus de crash, mais toujours pas de persistance.

5. Taille d'ecriture BIOS
- essai en `rbc3=1` (`256 bytes`);
- puis alignement sur un jeu recent (`ddmr.ngp`) en `rbc3=2` (`512 bytes`);
- resultat: pas de persistance visible apres reboot dans les deux cas.

Conclusion actuelle:
- le probleme principal "console qui s'eteint" a ete contourne;
- le probleme "save effectivement persistante" n'est pas encore resolu;
- le wrapper `ngpc_flash` ne doit pas encore etre considere comme valide sur
  hardware reel pour ce projet.

Hypothese de travail restante:
- incompatibilite ou particularite de la cart flash cible vis-a-vis des appels
  BIOS flash en contexte jeu reel;
- il faudra probablement un mini binaire de test save isole, sans le reste du
  shmup, pour valider:
  - offset
  - block number
  - taille de write
  - lecture du magic apres reboot
