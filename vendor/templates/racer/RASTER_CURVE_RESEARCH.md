# Raster Curve Effect — Research & Dev Log
## Pseudo-3D road courbe style OutRun — NGPC Timer0 HBlank ISR

Dernière mise à jour : 2026-04-12

---

## Table des matières

1. [Références — ROMs analysées](#1-références--roms-analysées)
2. [Architecture générale Timer0 NGPC](#2-architecture-générale-timer0-ngpc)
3. [Chronologie des essais](#3-chronologie-des-essais)
4. [Bugs découverts et fixes](#4-bugs-découverts-et-fixes)
5. [Formules OFS_X — essais et résultats](#5-formules-ofs_x--essais-et-résultats)
6. [Effet palette swap par scanline (road narrowing)](#6-effet-palette-swap-par-scanline-road-narrowing)
7. [Layout tilemap 5 bandes](#7-layout-tilemap-5-bandes)
8. [État final validé (2026-04-12)](#8-état-final-validé-2026-04-12)
9. [À faire / prochaines expérimentations](#9-à-faire--prochaines-expérimentations)

---

## 1. Références — ROMs analysées

### Game Boy — F-1 Race (World) (Rev 1).gb

**Interrupt vector** : STAT interrupt `$0048`, dispatch vers `Jump_000_1c02`.

**Handler (bank_000.asm ~line 6034)** :
```asm
Jump_000_1c02:
    push af
    push hl
    ld a, [$c342]     ; road-mode active flag
    and a
    jr z, jr_000_1c57 ; skip si inactif

    ldh a, [rLY]      ; scanline courant
    cp $57            ; LY >= 87 ?
    jr nc, jr_000_1c28

    sub $27           ; LY - 39
    jr c, jr_000_1c36 ; LY < 39 : pas dans zone route

    ; LY in [39..86] — 48 lignes route
    ld l, a           ; index 0..47
    ld h, $c2         ; table à $C200
    ld a, [hl]        ; valeur SCX pré-calculée
    ldh [rSCX], a     ; ÉCRITURE OFS_X PAR SCANLINE
    jr nz, jr_000_1c25

    ; Ligne haute : aussi SCY + BGP
    ld hl, $c52d
    ld a, [hl+]
    ldh [rSCY], a
    ld a, [hl+]
    ldh [rBGP], a

jr_000_1c25:
    pop hl
    pop af
    reti
```

**Points clés F-1 Race** :
- Zone route : scanlines 39–86 (48 lignes)
- Table SCX en RAM `$C200` (48 bytes), une valeur par scanline
- SCX[0] = max offset (horizon) → SCX[47] = 0 (bas/caméra)
- LYC est mis à jour dans l'ISR pour chaîner scanline par scanline
- ISR ≈ 8 instructions = très rapide
- Table pré-calculée, ISR ne fait qu'un lookup + write

### NGPC — Sonic Pocket Adventure

**ISR Timer0** à `0x3A59D4` (copié en RAM `0x6FD4` au boot) :
```
push XWA
ld XWA, (0x68E4)   ; state fn-ptr en RAM
jp (XWA)           ; dispatch
```

Architecture state-machine : chaque état fait son travail puis met à jour le fn-ptr.
Utilise **SCR2** (pas SCR1) : `ld (0x8034), W` = SCR2_OFS_X.
TREG0 varie entre 0x01 et 0x0C selon les états.

### NGPC — Densha de Go! 2

Non analysé en détail — snippets consultés mais technique non extraite.

---

## 2. Architecture générale Timer0 NGPC

### Registres Timer0
```
HW_TRUN    (0x80A0) : bit 0 = start/stop Timer0
HW_T01MOD  (0x80A2) : bits 1:0 = source d'horloge Timer0
HW_TREG0   (0x80A6) : valeur de rechargement (overflow = ISR)
HW_INT_TIM0 (0x6FD4): pointeur ISR 32 bits
```

### Source d'horloge T01MOD bits 1:0
```
00 = pas de source / arrêt
01 = T0IN (pin externe = HBlank K2GE) ← spec officielle
02 = horloge interne fφ/4 (~1.5 MHz)
03 = interne fφ/256 (~24 kHz)
```

**DÉCOUVERTE EMPIRIQUE CRITIQUE** :
- `T01MOD |= 0x01` (T0IN externe) = ISR ne fire JAMAIS sur le hardware testé
- `T01MOD |= 0x02` (interne) = ISR fire à ~9120 Hz = taux HBlank (152 × 60fps)

Conclusion : sur ce hardware, 0x02 agit exactement comme le HBlank. Possiblement
l'horloge interne tombe en phase avec la K2GE, ou c'est un quirk du silicium.

**MISE À JOUR (après investigations approfondies)** :
Après analyse de `ngpcspec.txt`, le setup correct validé est :
```c
HW_TRUN    &= ~0x01;    /* stop Timer0 */
HW_T01MOD  &= ~0xC3;    /* clear bits 7:6 (Timer1) et 1:0 */
HW_T01MOD  |= 0x01;     /* T0IN = HBlank pin */
HW_TREG0    = 0x01;     /* reload = 1 → fire chaque HBlank */
HW_TRUN    |= 0x01;     /* start Timer0 */
```
Et activer via BIOS INTLVSET (SWI 1) avec RC3=2, RB3=4.

### Activation interruption Timer0 (BIOS obligatoire)
Les writes directs aux registres INTET ne fonctionnent PAS — le BIOS possède
le hardware d'interruption. Méthode officielle :
```c
__asm("ldb rb3, 4");   /* niveau priorité 4 (= VBlank) */
__asm("ldb rc3, 2");   /* numéro interruption = 8-bit Timer 0 */
__asm("ldb rw3, 4");   /* BIOS_INTLVSET = 4 */
__asm("swi 1");
```
Source : SysCall.txt `VECT_INTLVSET` + SysPro.txt table interruptions.

### Registres scroll SCR1
```
HW_SCR1_OFS_X = 0x8032   (S1SO.H)
HW_SCR1_OFS_Y = 0x8033   (S1SO.V)
```
Depuis ngpcspec.txt : "The result of the values set in these registers is displayed
from the next line being drawn." → écritures per-scanline dans l'ISR = FONCTIONNEL.

---

## 3. Chronologie des essais

### Essai A — Formule linéaire simple

```c
for (y = 16u; y < 144u; y++) {
    prod  = (u16)(143u - y) * (u16)mag;
    shift = (u8)(prod >> 6);   /* max 95px */
    s_raster_x[y] = right_turn ? (u8)(0u - shift) : shift;
}
```

**Résultat** : "tout bouge de gauche à droite, pas de courbe" — la route se déplace
en bloc, aucun effet de courbure visible.

**Diagnostic** : La formule linéaire donne un décalage proportionnel à chaque
scanline → toute la route se déplace de la même manière → cerveau perçoit une
translation, pas une courbure. C'est le même problème qu'un scroll uniforme.

### Essai B — Formule quadratique v1 (dy du bas vers haut)

```c
for (y = 16u; y < 144u; y++) {
    dy4   = (u8)((u8)(143u - y) >> 2u);  /* 0..31 */
    sq    = (u16)dy4 * (u16)dy4;
    prod  = sq * (u16)mag;
    shift = (u8)(prod >> 9u);
    s_raster_x[y] = right_turn ? (u8)(0u - shift) : shift;
}
```

**Résultat** : "bon je vois le concept" — courbe visible mais commence trop haut,
la moitié basse de l'écran (y=120-143) ne bouge presque pas.

**Diagnostic** :
- `(143-y)>>2` = 0 pour y=139-143 → 0px de shift pour les 5 lignes du bas
- `(143-y)>>2` = 1 pour y=135-139 → sq=1, avec mag=48 : prod=48 → >>9 = 0px encore
- En pratique les ~50 scanlines du bas sont inertes → courbe "dans le ciel"

### Essai C — Extension base jusqu'à y=151

Passage de `y < 144u` à `y < 152u` et `(151u - y)` au lieu de `(143u - y)`.

```c
dy4 = (u8)((u8)(151u - y) >> 2u);  /* 0..33 */
```

**Résultat** : amélioration marginale — la courbe s'étend un peu plus bas mais
le problème de fond reste : le `>>2` quantifie les scanlines du bas à dy4=0 ou 1.

### Essai D — Diviseur >>7 avec cap 100px (état actuel)

```c
for (y = 16u; y < 152u; y++) {
    dy4   = (u8)((u8)(151u - y) >> 2u);  /* 0..33, u8 */
    sq    = (u16)dy4 * (u16)dy4;          /* 0..1089, u16 */
    prod  = sq * (u16)mag;                /* 0..52272, u16 */
    shift = (u8)(prod >> 7u);             /* 0..408 avant cap */
    if (shift > 100u) shift = 100u;       /* cap : évite wrap tilemap */
    s_raster_x[y] = right_turn ? (u8)(0u - shift) : shift;
}
```

**Calcul avec mag=48** :
| Scanline | dy4 | sq   | prod  | shift |
|----------|-----|------|-------|-------|
| y=151    | 0   | 0    | 0     | 0 px  |
| y=147    | 1   | 1    | 48    | 0 px  |
| y=143    | 2   | 4    | 192   | 1 px  |
| y=139    | 3   | 9    | 432   | 3 px  |
| y=135    | 4   | 16   | 768   | 6 px  |
| y=127    | 6   | 36   | 1728  | 13 px |
| y=119    | 8   | 64   | 3072  | 24 px |
| y=111    | 10  | 100  | 4800  | 37 px |
| y=99     | 13  | 169  | 8112  | 63 px |
| y=83     | 17  | 289  | 13872 | 100 px (cap) |
| y=16     | 33  | 1089 | 52272 | cap   |

**Résultat** : en attente de test hardware (ROM buildée 2026-04-12).

**Raisonnement du cap** :
- Max OFS_X safe ≈ 100px (route 5 bandes = colonnes 0-19, tilemap 32 cols = 256px)
- Au-delà de 100px de décalage, SCR1 enroule sur colonnes 20-31 (anciennement vides)
- Voir section 7 pour le fix phantom cols

---

## 4. Bugs découverts et fixes

### Bug 1 — s_hblank_cnt offset de ~46

**Symptôme** : "le bg est à moitié glitch et la route n'a pas changé de comportement"
— le fond BG avait des artefacts visuels, la table raster était lue avec un offset
de ~46 lignes par rapport au hardware réel.

**Cause** : `ngpc_raster_vsync()` resetait `s_hblank_cnt = 0` dans le VBlank ISR.
Mais Timer0 continue de fire pendant tout le VBlank (~46 fois entre le reset et
le retour à la ligne 0 active). Au début de l'affichage actif, `s_hblank_cnt` était
déjà à ~46 au lieu de 0.

**Conséquences** :
- `s_raster_x[0..45]` → jamais appliqués aux bonnes lignes
- `s_raster_x[line]` pour line≥152 → entrées invalides de la table
- Lecture de `s_raster_x[152]` = débordement de tableau → valeur aléatoire
- L'ISR arrivait rapidement au `if (line >= 152u) return;` → cessait d'écrire les OFS
  → tout le BG scrollait d'un seul coup → glitch

**Fix** : Variable `s_was_vblank` + resync au premier ISR actif.

```c
/* Dans ISR : */
if (HW_RAS_V >= 152u) {
    s_was_vblank = 1u;
    return;
}
if (s_was_vblank) {
    s_hblank_cnt = 0u;  /* resync : discard les ~46 fires VBlank */
    s_was_vblank = 0u;
}
line = s_hblank_cnt;
s_hblank_cnt = (u8)(s_hblank_cnt + 1u);
if (line >= 152u) return;
```

`ngpc_raster_init()` initialise `s_was_vblank = 1u` pour forcer la resync au
premier fire après le boot.

**`ngpc_raster_vsync()`** ne touche plus `s_hblank_cnt` — reset uniquement
`ngpc_dbg_isr_fires` pour diagnostic.

### Bug 2 — Phantom columns gap (bandes noires/colorées)

**Symptôme** : En virant fortement, apparition d'une bande de couleur (sky/fond)
côté opposé à la courbe.

**Cause** : La tilemap SCR1 est de 32 colonnes (256px) mais l'écran n'en affiche
que 20 (160px). Les colonnes 20-31 n'étaient pas initialisées → contenaient les
tiles du boot BIOS ou 0 (ciel/fond bleu).

Au virage fort, OFS_X=100 → le viewport affiche depuis col_px=100 jusqu'à 260px
→ wrapping sur colonnes 20-31 → bande vide visible.

**Fix** : Remplir les colonnes 20-31 avec les tiles d'herbe dans `draw_road_bg()` :
```c
/* Phantom cols (32-col tilemap, only 20 visible onscreen) */
ngpc_gfx_fill_rect(GFX_SCR1, 20u, r, 12u, 1u, grass_tile, PAL_GRASS);
```

---

## 5. Formules OFS_X — essais et résultats

### Pourquoi linéaire = pas de courbe

Une formule linéaire `shift = (MAX - y) * mag` donne :
- Scanline 16 (horizon) : shift = MAX
- Scanline 151 (bas) : shift ≈ 0

Ce qui est *correct en valeurs* mais *faux perceptuellement* : chaque scanline se
déplace d'un montant proportionnel à sa position. L'œil perçoit ça comme un scroll
uniforme de la route entière, pas comme une courbure.

### Pourquoi quadratique = courbure visible

La courbure d'une route vue en perspective suit une loi en x² :
- Horizon (loin) : grande déviation horizontale
- Base (proche) : faible déviation, route droite devant le joueur
- La DÉRIVÉE du shift par rapport à y doit être nulle en bas et forte en haut

Formule `shift = (dy >> 2)² * mag / N` :
- Plate près du bas (dy petit → shift ≈ 0)
- Forte courbure à l'horizon (dy grand → shift grand)
- Aspect "courbe" crédible

### Problème de quantification avec >>2

Le prescale `>>2` sur dy cause une quantification :
- y=151 → dy=0, dy4=0 → shift=0
- y=150 → dy=1, dy4=0 → shift=0
- y=149 → dy=2, dy4=0 → shift=0
- y=148 → dy=3, dy4=0 → shift=0
- y=147 → dy=4, dy4=1 → shift=mag/N

Avec >>9 comme diviseur et mag~48 : shift pour dy4=1 → 48/512 = 0px encore.
Résultat : les ~16 premières lignes depuis le bas = 0px de shift invariablement.

### Solution retenue : >>7 avec cap

`>>7` donne 4× plus d'amplitude que `>>9` à même dy4.
Cap à 100px pour éviter le wrap tilemap (voir Bug 2).

Valeurs effectives pour mag=48 :
- Bottom zone (y=143-151) : 0-3px → transition douce
- Mid zone (y=111-143) : 3-37px → courbure visible
- Upper zone (y=83-111) : 37-100px → courbure marquée
- Horizon (y=16-83) : plateau à 100px → horizon pleinement courbé

### Alternatives non testées

**Option U32 sans prescale** : `shift = (u32)(151-y)*(u32)(151-y)*mag >> 15`
- Plus précis (pas de quantification)
- Risque : u32 mul dans ISR = cycles lents sur TLCS-900

**Option table sqrt** : pré-calculer `sqrt(y)` dans la table → transition plus
naturelle ("easing"). Non nécessaire pour l'instant.

**Option Densha de Go** : utilise probablement une table de cosinus précalculée.
Snippets consultés mais non analysés en profondeur.

---

## 6. Effet palette swap par scanline (road narrowing)

### Concept

La route perspective doit être plus étroite à l'horizon et plus large en bas.
Avec une tilemap fixe, on ne peut pas changer la largeur des bandes par scanline
— mais on peut changer la COULEUR via l'ISR pour faire paraître les bandes
latérales comme de l'herbe (au lieu de route).

### Layout palettes

```
PAL_GRASS   = 0  (vert herbe)
PAL_ROAD    = 3  (béton route, bandes internes)
PAL_ROAD_B  = 4  (mid band : passe grass↔route via ISR)
PAL_ROAD_A  = 5  (outer band : passe grass↔route via ISR)
```

Indices palette SCR1 (HW_PAL_SCR1[]) :
```
[16] = PAL_ROAD_B.c0    [17] = .c1
[18] = PAL_ROAD_B.c2    ← l'ISR écrit ici (couleur route ou herbe)
[19] = PAL_ROAD_B.c3

[20] = PAL_ROAD_A.c0    [21] = .c1
[22] = PAL_ROAD_A.c2    ← l'ISR écrit ici (couleur route ou herbe)
[23] = PAL_ROAD_A.c3
```

### ISR (fragment)

```c
if (s_pal_active) {
    HW_PAL_SCR1[18u] = (line < s_pal_mid_thr) ? s_pal_grass_c2 : s_pal_road_c2;
    HW_PAL_SCR1[22u] = (line < s_pal_out_thr) ? s_pal_grass_c2 : s_pal_road_c2;
}
```

### Seuils

```c
ROAD_THR_MID = 106u  /* scanline où la mid band passe grass→route */
ROAD_THR_OUT = 128u  /* scanline où l'outer band passe grass→route */
```

Résultat visuel :
- y=0..105 : mid band et outer band = herbe → route étroite (horizon)
- y=106..127 : mid band = route, outer = herbe → route moyenne
- y=128..151 : les deux = route → route large (caméra)

### API

```c
void ngpc_raster_set_road_pal(u16 grass_c2, u16 road_c2, u8 mid_thr, u8 out_thr);
```

Appel depuis `scene_init()` :
```c
ngpc_raster_set_road_pal(RGB(1u,8u,1u), RGB(5u,5u,5u), ROAD_THR_MID, ROAD_THR_OUT);
```

Pre-initialise les entrées palette avant le premier ISR pour éviter un flash.

---

## 7. Layout tilemap 5 bandes

### Structure (par ligne de tiles)

```
Col  0- 3 : herbe gauche        (PAL_GRASS,  grass_tile)
Col  4- 5 : outer left          (PAL_ROAD_A, TILE_ROAD) ← couleur ISR-swapped
Col  6- 7 : mid left            (PAL_ROAD_B, TILE_ROAD) ← couleur ISR-swapped
Col  8-11 : inner (route fixe)  (PAL_ROAD,   road_tile)
Col 12-13 : mid right           (PAL_ROAD_B, TILE_ROAD) ← couleur ISR-swapped
Col 14-15 : outer right         (PAL_ROAD_A, TILE_ROAD) ← couleur ISR-swapped
Col 16-19 : herbe droite        (PAL_GRASS,  grass_tile)
Col 20-31 : phantom (invisible) (PAL_GRASS,  grass_tile) ← FIX bug 2
```

Largeurs en pixels (8px/tile) :
- Herbe L : 32px
- Outer L : 16px
- Mid L : 16px
- Inner : 32px
- Mid R : 16px
- Outer R : 16px
- Herbe R : 32px
- = 160px total = plein écran

### Code draw_road_bg()

```c
for (r = 0u; r < SCR_MAP_H; r++) {
    u8 grass_tile = /* alternance striée selon r */ ...;
    u8 road_tile  = ...;

    ngpc_gfx_fill_rect(GFX_SCR1, 0u,  r, 4u,  1u, grass_tile, PAL_GRASS);
    ngpc_gfx_fill_rect(GFX_SCR1, 4u,  r, 2u,  1u, TILE_ROAD,  PAL_ROAD_A);
    ngpc_gfx_fill_rect(GFX_SCR1, 6u,  r, 2u,  1u, TILE_ROAD,  PAL_ROAD_B);
    ngpc_gfx_fill_rect(GFX_SCR1, 8u,  r, 4u,  1u, road_tile,  PAL_ROAD);
    ngpc_gfx_fill_rect(GFX_SCR1, 12u, r, 2u,  1u, TILE_ROAD,  PAL_ROAD_B);
    ngpc_gfx_fill_rect(GFX_SCR1, 14u, r, 2u,  1u, TILE_ROAD,  PAL_ROAD_A);
    ngpc_gfx_fill_rect(GFX_SCR1, 16u, r, 4u,  1u, grass_tile, PAL_GRASS);
    ngpc_gfx_fill_rect(GFX_SCR1, 20u, r, 12u, 1u, grass_tile, PAL_GRASS);
}
```

---

## 8. État final validé (2026-04-12)

### ngpc_raster.c — état actuel

Variables ajoutées :
```c
static volatile u8  s_hblank_cnt;      /* compteur software ligne */
volatile u8         ngpc_dbg_isr_fires; /* diagnostic fires/frame */
static u8           s_was_vblank;      /* flag resync après VBlank */
static u8           s_pal_active;      /* palette swap actif */
static volatile u16 s_pal_grass_c2;
static volatile u16 s_pal_road_c2;
static u8           s_pal_mid_thr;
static u8           s_pal_out_thr;
```

ISR complet :
```c
static void __interrupt isr_hblank(void)
{
    u8 line;
    ngpc_dbg_isr_fires = (u8)(ngpc_dbg_isr_fires + 1u);

    if (HW_RAS_V >= 152u) { s_was_vblank = 1u; return; }
    if (s_was_vblank) { s_hblank_cnt = 0u; s_was_vblank = 0u; }

    line = s_hblank_cnt;
    s_hblank_cnt = (u8)(s_hblank_cnt + 1u);
    if (line >= 152u) return;

    if (s_pal_active) {
        HW_PAL_SCR1[18u] = (line < s_pal_mid_thr) ? s_pal_grass_c2 : s_pal_road_c2;
        HW_PAL_SCR1[22u] = (line < s_pal_out_thr) ? s_pal_grass_c2 : s_pal_road_c2;
    }
    if (s_scroll_x) HW_SCR1_OFS_X = s_scroll_x[line];
    if (s_scroll_y) HW_SCR1_OFS_Y = s_scroll_y[line];
}
```

### main.c — build_raster_x() état actuel

```c
static void build_raster_x(void)
{
    u8  y, dy4, shift;
    u8  vp, mag;
    u8  right_turn;
    u16 sq, prod;

    vp         = ngpc_racer_vp_x();
    right_turn = (vp > (u8)RACER_SCREEN_CX) ? 1u : 0u;
    mag        = right_turn ? (u8)(vp - (u8)RACER_SCREEN_CX)
                            : (u8)((u8)RACER_SCREEN_CX - vp);

    for (y = 0u; y < 16u; y++) s_raster_x[y] = 0u;

    for (y = 16u; y < 152u; y++) {
        dy4   = (u8)((u8)(151u - y) >> 2u);  /* 0..33, u8 */
        sq    = (u16)dy4 * (u16)dy4;          /* 0..1089, u16 */
        prod  = sq * (u16)mag;                /* 0..52272, u16 */
        shift = (u8)(prod >> 7u);             /* 0..408 avant cap */
        if (shift > 100u) shift = 100u;       /* cap : évite wrap tilemap */
        s_raster_x[y] = right_turn ? (u8)(0u - shift) : shift;
    }
}
```

### Résultats hardware

| Version | Résultat |
|---------|----------|
| Linéaire >>6 | Route glisse en bloc, aucune courbe |
| Quadratique >>9 base 143 | Concept visible, courbe seulement dans la moitié haute |
| Quadratique >>9 base 151 | Légère amélioration, bas toujours plat |
| Quadratique >>7 cap 100 base 151 | **En attente test hardware** |

---

## 9. À faire / prochaines expérimentations

### Immédiat
- Tester ROM sur hardware avec formule >>7+cap (essai D)
- Si courbe toujours trop plate en bas : supprimer le `>>2` prescale et utiliser u32
  ```c
  /* u32 version, plus précis */
  u16 dy  = (u16)(151u - y);     /* 0..135 */
  u32 sq  = (u32)dy * (u32)dy;   /* 0..18225 */
  u32 prd = sq * (u32)mag;       /* 0..874800 */
  shift   = (u8)(prd >> 14u);    /* 0..53px, pas de cap nécessaire */
  ```
  Avec >>14 : max = 18225*48/16384 = 53px à l'horizon. Pas de cap.
  Mais : u32 mul dans build_raster_x (pas dans ISR) = acceptable.

### Moyen terme
- Essayer perspective correcte : `shift = base_shift / (y + perspective_k)`
  (hyperbole = plus fidèle physiquement qu'une parabole)
- Ajouter ondulation de la route (terrain vallonné) via OFS_Y sinusoïdal par bande
- Ajouter bordures/poteaux le long de la route avec sprites

### Tuning thresholds perspective
Les seuils `ROAD_THR_MID` et `ROAD_THR_OUT` pourraient être liés à la vitesse
ou à la courbure pour renforcer l'impression de profondeur dynamique.

---

## Notes générales

- `ngpc_dbg_isr_fires` doit valoir **152** par frame. Valeur < 152 = ISR trop lente ou Timer0 mal configuré.
- Debug affiché ligne 2 : `ISR:152` ✓
- Debug affiché ligne 3 : `XH:nnn XN:nnn` = shift à l'horizon et au bas → doit être non-nul au virage.
- Si `XH=000` au virage : `ngpc_racer_vp_x()` retourne RACER_SCREEN_CX → mag=0.
- Si `XN=000` et `XH>0` : formule quadratique normale (bas ≈ 0 est attendu).
- Tilemap SCR1 = 32×32 tiles = 256×256px. Écran = 160×152px. OFS_X wraps à 256.
- OFS_X = 100 → affiche pixels 100-259, wrap à 0 pour 260-259 (pixels 260-299 → 4-43).
  Donc colonne 20-31 (160-255px) est visible dès OFS_X=0, et phantom cols 20-31
  sont TOUJOURS potentiellement affichées selon le scroll. Remplissage obligatoire.
