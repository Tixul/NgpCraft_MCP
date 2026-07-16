# NgpCraft_Disasm — DEVLOG

## Session 1 — 2026-03-31 : Analyse et démarrage du chantier

### Objectif
Construire un désassembleur NGPC/NGP plus utile que Ghidra, en exploitant notre
connaissance complète du silicium, de la toolchain, et des registres hardware.

---

## Architecture du décodeur TLCS-900

### Flux de dispatch (d'après TMP95C061BFG datasheet §4-5 + t900as.py)

```
decode_one(data, pos, base_addr):
  1. decode_fixed()      → opcodes fixes 0x00-0x1F + 0xF7-0xFF
  2. if b < 0x80:        → decode_xx()     PUSH/POP/LD-imm/JR/JRL
  3. if b >= 0x80:
       zz = (b & 0x30) >> 4       ← bits 5:4
       mem = ((b & 0x4F) → bits 6,3:0 → remappé)
       if zz == 3:        → decode_B0_mem()   (F1/B0/etc abs-store + RET cc + JP/CALL indirect)
       elif mem >= 23:    → decode_zz_r()    (C8+zz+r ALU, E8+r LINK/UNLK/EXTZ)
       elif mem <= 21:    → decode_zz_mem()  (C1/D1 abs16 load, (r32+d8) indirect)
```

### Encodage zz dans la famille C8+zz+r (IMPORTANT — corrigé HW 2026-07-03)

La formule `getzz = (b & 0x30) >> 4` (bits[5:4]) est CORRECTE. La vraie
correspondance, confirmée sur vrai NGPC + ngdis masker.h getzz() le 2026-07-03,
est :

| Plage       | Offset/C8 | Registres  | Status NGPC |
|-------------|-----------|------------|-------------|
| 0xC8..0xCF  | +0x00     | R8 (byte)  | SAFE        |
| 0xD0..0xD7  | +0x08     | R16 (word) | **BROKEN**  |
| 0xD8..0xDF  | +0x10     | R16 (word) | SAFE        |
| 0xE8..0xEF  | +0x20     | R32 (long) | SAFE (LINK/UNLK/EXTZ/EXTS + ALU R32) |

**D8-DF = R16 (word), PAS R32** : `getzz(0xD8)=1=word` est exact ; l'ancienne
révision qui forçait `(b - 0xC8) // 8` pour classer D8-DF en R32/long était
FAUSSE. `D8 89` = `ld BC, WA` (copie 16 bits, high16 préservé), et le vrai
préfixe long (R32) est E8-EF : `E8 89` = `ld XBC, XWA`. Preuve HW : `D8 89`
XWA=0x11223344, XBC=0xAAAAAAAA → XBC=0xAAAA3344 (high16 intact).

**D0-D7 BROKEN sur silicium NGPC** : le hardware exécute n'importe quoi.
On les détecte et on ajoute un warning `; !BROKEN`.

### Plages d'opcodes et leur décode

| Byte(s)         | Decode path     | Description |
|-----------------|-----------------|-------------|
| 0x00            | fixed           | NOP |
| 0x06 n          | fixed           | EI n (n=7 → DI) |
| 0x07            | fixed           | RETI |
| 0x08 n8 imm8    | fixed           | LDB (n8), imm8 |
| 0x0A n8 lo hi   | fixed           | LDW (n8), #imm16 |
| 0x0E            | fixed           | RET |
| 0x0F lo hi      | fixed           | RETD d16 |
| 0x17 n          | fixed           | LDF n |
| 0x1A lo hi      | fixed           | JP #16 |
| 0x1B lo mid hi  | fixed           | JP #24 |
| 0x1C lo hi      | fixed           | CALL #16 |
| 0x1D lo mid hi  | fixed           | CALL #24 |
| 0x1E lo hi      | fixed           | CALR d16 (rel 16) |
| 0x20-0x27 imm8  | decode_xx       | LD R8, imm8 |
| 0x28-0x2F       | decode_xx       | PUSH R16 |
| 0x30-0x37 lo hi | decode_xx       | LD R16, imm16 |
| 0x38-0x3F       | decode_xx       | PUSH R32 |
| 0x40-0x47 x4    | decode_xx       | LD R32, imm32 |
| 0x48-0x4F       | decode_xx       | POP R16 |
| 0x50-0x57       | decode_xx       | POP R32 |
| 0x58-0x5F       | —               | **Indéfini** (datasheet Appendice C — case vide) |
| 0x60-0x6F d8    | decode_xx       | JR cc, disp8 |
| 0x70-0x7F lo hi | decode_xx       | JRL cc, disp16 |
| 0x84 0x10       | special         | LDIW (XDE+),(XHL+) |
| 0x95 0x11       | special         | LDIRW (XDE+),(XHL+) |
| 0x88-0x8F d8 op | decode_zz_mem   | LD R8, (r32+d8) |
| 0x98-0x9F d8 op | decode_zz_mem   | LD R16, (r32+d8) |
| 0xA8-0xAF d8 op | decode_zz_mem   | LD R32, (r32+d8) |
| 0xB8-0xBF d8 op | decode_zz_mem   | LD (r32+d8), R |
| 0xC1 lo hi op   | decode_zz_mem   | LD R8, (abs16) — SAFE |
| 0xC5 r32 op     | decode_zz_mem   | LD (r32+), R8 — post-increment store |
| 0xD1 lo hi op   | decode_zz_mem   | LD R16, (abs16) — SAFE |
| 0xC8-0xCF       | decode_zz_r     | C8+byte+r prefix (R8 ALU) |
| 0xD0-0xD7       | decode_zz_r     | C8+word+r prefix (R16 ALU) **BROKEN** |
| 0xD8-0xDF       | decode_zz_r     | C8+word+r prefix (R16 ALU) SAFE — word, not long (HW 2026-07-03) |
| 0xE8-0xEF       | decode_zz_r     | E8+r: LINK/UNLK/EXTZ/EXTS + ALU R32 (long) |
| 0xB0 0xF0+cc    | decode_B0_mem   | RET cc |
| 0xF1 lo hi op   | decode_B0_mem   | LD/LDW (abs16), R/imm |
| 0xF8-0xFF       | fixed           | SWI 0-7 |

### Opérations dans la famille C8+zz+r (second byte)

| 2e byte    | Opération |
|------------|-----------|
| 0x03 imm   | LD r, imm |
| 0x04       | PUSH r |
| 0x05       | POP r |
| 0x06       | CPL r |
| 0x07       | NEG r |
| 0x0C lo hi | LINK r32, d16 (E8+r seulement) |
| 0x0D       | UNLK r32 (E8+r seulement) |
| 0x12       | EXTZ r |
| 0x13       | EXTS r |
| 0x1C d8    | DJNZ r, d8 |
| 0x2E cr    | LDC cr, r |
| 0x2F cr    | LDC r, cr |
| 0x60+n     | INC n, r (n=0→8) |
| 0x68+n     | DEC n, r |
| 0x80+R     | ADD R, r |
| 0x88+R     | LD R, r |
| 0x90+R     | ADC R, r |
| 0x98+R     | LD r, R |
| 0xA0+R     | SUB R, r |
| 0xB0+R     | SBC R, r |
| 0xC0+R     | AND R, r |
| 0xC8 imm   | ADD r, imm |
| 0xC9 imm   | ADC r, imm |
| 0xCA imm   | SUB r, imm |
| 0xCB imm   | SBC r, imm |
| 0xCC imm   | AND r, imm |
| 0xCD imm   | XOR r, imm |
| 0xCE imm   | OR r, imm |
| 0xCF imm   | CP r, imm |
| 0xD0+R     | XOR R, r |
| 0xE0+R     | OR R, r |
| 0xE8+n cnt | RLC/RRC/RL/RR/SLA/SRA/SLL/SRL count, r |
| 0xF0+R     | CP R, r |
| 0xF8-0xFF  | RLC/…/SRL A, r |

### Opérations dans decode_zz_mem (après mem address bytes)

| Op byte    | Instruction |
|------------|-------------|
| 0x20+R     | LD R, (mem) — load into register |
| 0x40+R8    | LD (mem), R8 |
| 0x50+R16   | LD (mem), R16 |
| 0x60+R32   | LD (mem), R32 |
| 0x80+R     | ADD R, (mem) |
| 0x88+R     | ADD (mem), R |
| 0xA0+R     | SUB R, (mem) |
| 0xC0+R     | AND R, (mem) |
| 0xD0+R     | XOR R, (mem) |
| 0xE0+R     | OR R, (mem) |
| 0xF0+R     | CP R, (mem) |

### Formes post-increment encodées dans notre toolchain

- `LD (r32+), R8`  : `[0xC5, r32_idx, 0x40 + r8_idx]` — 3 bytes
  - 0xC5 = getmem → ARI_PI (post-increment), getzz=0 (byte)
- `LD R8, (r32+)`  : `[0x80, 0x15, r32_idx, 0x20 + r8_idx]` — 4 bytes
  - Datasheet §5 documente CPIR à cet opcode, mais notre assembleur
    génère ceci comme séquence de copie byte post-inc

---

## Carte mémoire NGPC

| Plage adresses      | Contenu |
|--------------------|---------|
| 0x000000-0x0000FF  | I/O interne CPU (timers, DMA, watchdog) |
| 0x004000-0x005FFF  | Main RAM (8 KB) |
| 0x006000-0x006BFF  | Battery-backed RAM (3 KB) |
| 0x006F80-0x006FFF  | BIOS system zone (variables, ISR vectors) |
| 0x007000-0x007FFF  | Z80 RAM (partagé, audio) |
| 0x008000-0x0087FF  | K2GE registres vidéo |
| 0x008800-0x008BFF  | Sprite VRAM (64 sprites x 4 bytes) |
| 0x008C00-0x008C3F  | Sprite palette indices |
| 0x009000-0x0097FF  | Scroll plane 1 (tilemap 32x32) |
| 0x009800-0x009FFF  | Scroll plane 2 (tilemap 32x32) |
| 0x00A000-0x00BFFF  | Character/Tile RAM (512 tiles) |
| 0x200000-0x3FFFFF  | Cartouche ROM (FAR access) |
| 0xFF0000-0xFFFFFF  | BIOS ROM interne (64 KB) |
| 0xFFFE00           | Table des vecteurs BIOS |

### Registres hardware importants

| Adresse | Nom          | Description |
|---------|--------------|-------------|
| 0x006F  | HW_WATCHDOG  | Ecrire 0x4E pour reset |
| 0x6F82  | HW_JOYPAD    | Etat joypad |
| 0x6F91  | HW_OS_VERSION| 0=mono, !=0=color |
| 0x6FCC  | VBL_VECTOR   | Vecteur ISR VBlank (32-bit) |

---

## Opcodes BROKEN sur le silicium NGPC

| Opcode(s)         | Instruction | Status |
|-------------------|-------------|--------|
| D0 xx             | CPL WA, NEG WA, SLL WA, ... | BROKEN — hang watchdog |
| D1..D7 (ALU ctx)  | word-reg ALU (BC,DE,HL,...) | BROKEN |
| CB xx (ALU)       | add/adc/sub/sbc/and/xor/or A,C (sub-op 0x80..0xFF) | BROKEN |
| CB 40..5F         | byte mul/muls/div/divs A,C (ex. CB 51 = div A,C) | SAFE (HW-cleared hw_test_bytediv 2026-07-08) |
| link XIY, N≥5     | N = stack frame size | BROKEN si N >= 5 |
| adc W, B avec W>0 | ADC high byte | BROKEN |

Note : D1 comme **abs16 word load** (ld WA,(addr)) est SAFE.
La distinction se fait par le 3e byte : 0x20..0x27 = abs16 load SAFE.

---

## Header ROM NGPC (64 bytes à 0x200000)

| Offset | Taille | Contenu |
|--------|--------|---------|
| 0x00   | 28     | Copyright string (SNK ou licensed) |
| 0x1C   | 4      | Entry point (32-bit LE) |
| 0x20   | 2      | Software ID BCD |
| 0x22   | 1      | Color/mono : 0x00=NGP mono, 0x10=NGPC color |
| 0x23   | 1      | Réservé |
| 0x24   | 12     | Titre ASCII (space-padded) |
| 0x30   | 16     | Réservé (zéros) |

---

## Format de sortie du disassembleur

```
; ============================================================
; ROM: title [color/mono]  entry: 0x200040  size: 65536 bytes
; ============================================================

0x200000:                  ; === ROM HEADER ===
0x200040: entry_point:
0x200040: 1b 40 00 20      jp      0x200040
                                             ; -> entry_point
```

Format par ligne :
```
ADDR: hex_bytes        MNEMONIC  OPERANDS   ; annotation/warning
```

---

## Session 2 — 2026-03-31 : Implémentation decode_B0_mem général + decode_zz_mem général

### Bugs corrigés

#### 1. decode_B0_mem — réécriture complète
La version initiale ne gérait que 0xF1 (ABS_W) explicitement.
Réécriture avec helper `_retmem_info(data, pos, mem)` qui implémente le tableau des modes d'adressage du datasheet §4 :

```
mem=0..7   → (r32)           — 1 byte consumed
mem=8..15  → (r32+d8)        — 2 bytes consumed
mem=16     → (addr8)  ABS_B  — 2 bytes consumed
mem=17     → (addr16) ABS_W  — 3 bytes consumed
mem=18     → (addr24) ABS_L  — 4 bytes consumed
mem=19     → ARI (complex)   — 2-4 bytes
mem=20     → (-r32)  ARI_PD  — 2 bytes
mem=21     → (r32+)  ARI_PI  — 2 bytes
```

Toutes les opérations B0_mem couvrent maintenant :
`JP cc,(mem)`, `CALL cc,(mem)`, `LD (mem),R8/R16/R32`,
`LDA R16/R32,(mem)`, `LD (mem),#imm8/16`, `POP`, `POPW`,
bit ops (BIT, RES, SET, CHG, TSET, ANDCF, ORCF, XORCF, LDCF, STCF),
`LDAR R, $+4+d16`

#### 2. decode_zz_mem — fallback général
La version initiale ne gérait que des plages de premier byte spécifiques.
Ajout d'un fallback général en fin de fonction qui appelle `_retmem_info` pour
couvrir tous les modes d'adressage non traités explicitement :
`(r32)`, `(r32+d8)`, `ABS_B`, `ABS_L`, `ARI`, `ARI_PD`, `ARI_PI`.

Opérations couvertes : toutes les ALU `R,(mem)`, `(mem),R`, INC/DEC,
PUSH, RLD/RRD, rotates/shifts, ALU `(mem),#imm`.

### Bug D2 (abs24 word load) — ANALYSE EN COURS

Observé dans Stargunner.ngc (CC900-compiled) : `D2 BC 5E 00 20` apparaît 3×.
Notre handler BROKEN pour D0-D7 consomme D2 comme 2 bytes, brisant l'alignement.

Analyse :
- D2 : zz=1 (word), mem=18 (ABS_L = 24-bit absolute)
- La séquence `D2 BC 5E 00 20 DE F0 66 47` se décode comme :
  - `LD WA, (0x005EBC)` [5 bytes] — word load from abs24 addr dans main RAM
  - `cp XWA, XIZ` [2 bytes]
  - `jr Z, 0x207A59` [2 bytes]
- Ce schéma répété 3× est caractéristique d'un épilogue CC900 avec check résultat.

**Conclusion** : D2..D7 utilisés comme load abs (op=0x20+R) sont SAFE sur NGPC,
comme D1 (abs16). Le BROKEN ne s'applique qu'aux sous-ops ALU (CPL, NEG, etc.).
**Fix à faire** : tenter d'abord le décodage normal pour D2..D7 avant BROKEN.

### Bytecodes toujours inconnus (mineurs)

- `0x04` : confirmé `PUSH SR` (datasheet Appendice C, row 0 col 4). Rare en pratique.
- `0xF3 0x07 ...` : ARI mode 3 avec r32_idx hors range → données
  interprétées comme code par le linear sweep. Pas un vrai bug.

---

## Observations / décisions architecturales

1. **Un seul fichier Python** — pas de dépendances, deployable directement.
2. **Deux passes** :
   - Passe 1 : linear sweep pour collecter tous les jump/call targets → labels
   - Passe 2 : output avec les labels résolus
3. **Annotations NGPC** : toute adresse connue (HW regs, BIOS vars, VBL vector)
   remplacée par son nom symbolique dans les commentaires.
4. **SWI annotés** : `swi 5` → `; BIOS_SYSFONTSET`
5. **Broken opcodes** : préfixe `; !BROKEN` pour les séquences D0-D7 ALU.
6. **Pattern recognition** (phase 2) : détecter `link XIY, N` / `unlk XIY`
   comme delimiteurs de fonctions.
7. **Output format** : compatible avec `t900as.py` pour re-assemblage.
8. **D1..D7 abs-load SAFE** : D1 (abs16) + D2 (abs24) + D3-D5 (ARI/ARI_PD/ARI_PI)
   avec op=0x20+R sont SAFE. Seul D0 est universellement BROKEN. Les D2-D5
   ne doivent être marqués BROKEN que pour les sous-ops ALU (CPL, NEG, INC...).

---

---

## Session 3 — 2026-04-01 : Audit complet vs datasheet + 8 bugs corrigés

### Objectif
Audit de correction exhaustif : vérifier chaque table, chaque décodeur, chaque formule
contre le datasheet officiel TMP95C061BFG (Toshiba, ALT00146).
Validation sur ROMs commerciales : Dark Arms Beast Buster 1999 (USA, 2 MB, CC900).

### Bugs corrigés

| # | Localisation | Description | Impact |
|---|-------------|-------------|--------|
| 1 | `decode_xx` 0x58-0x5F | Décodés en `POP R32` → opcode indéfini per datasheet Appendice C | Fausse instruction |
| 2 | `decode_zz_mem` op=0x19 | Opérandes inversés : sortait `LD (mem),(nn)` au lieu de `LD (nn),(mem)` | Mauvaise sortie |
| 3 | `parse_header` | Offset 0x23 pour le flag color/mono → corrigé en 0x22 | ROM mal identifiée |
| 4 | `decode_zz_r` MUL/DIV reg | `dest_reg()` retournait même taille que source (ex: `MUL A, B` au lieu de `MUL WA, B`) | Mauvais registre |
| 5 | `decode_B0_mem` LDAR | `op & 0x20` toujours vrai → toujours R32, même formes word | Mauvais registre |
| 6 | `decode_zz_r` c=0x08-0x0B | MUL/MULS/DIV/DIVS immediate non décodés → `db` | **Désynchronisation stream** |
| 7 | `decode_zz_mem` op=0x40-0x5F / 0x10-0x17 | MUL/DIV mem + LDI/LDIR/LDD/LDDR/CPI/CPIR/CPD/CPDR non décodés | **Désynchronisation stream** |
| 8 | `_retmem_info` mem=20/21 | Formule `(byte & 0xFC) >> 2` → out-of-range → `None` sur tous les `(r32+)` et `(-r32)` avec byte préfixe ≥ 8 | **Désynchronisation stream critique** |

### Détail bug #8 — ARI_PD / ARI_PI (critique)

Affectait toutes les instructions post-increment et pre-decrement via le fallback `_retmem_info`.
Exemple : `E5 F2 20` = `LD XWA, (XDE+)` → sortait `?? unknown opcode` à cause de :
```python
r32_idx = (data[pos+1] & 0xFC) >> 2  # FAUX — retourne 0x3C (hors range)
```
Fix :
```python
r32_idx = data[pos+1] & 0x07  # bits[2:0] = index registre R32
```
Les formes spéciales `0xC5 r32` (post-inc store) et `0x80 0x15 r32` (post-inc load) 
utilisaient déjà `& 0x07` et fonctionnaient correctement.

### Validation post-fix

- `Dark Arms Beast Buster 1999 (USA)` : toutes les instructions `LD XWA,(XDE+)`,
  `LD (XIZ+),XWA`, `LDW (XBC+),WA` etc. décodées correctement. Stream aligné.
- DJNZ pointent correctement sur leurs labels.
- Aucune désynchronisation détectée dans les zones code.

### Sources confirmation

Toutes les corrections vérifiées contre :
- `TMP95C061BFG_datasheet_en_20110126.pdf` §4 (modes d'adressage), §5 (table instructions), Appendice C (instruction code map)

---

## Sources de référence utilisées

- `TMP95C061BFG_datasheet_en_20110126.pdf` (Toshiba, ALT00146) : spec ISA officielle TLCS-900/L1 — tables opcodes, registres, modes d'adressage, conditions, Appendice C instruction code map
- `t900as.py` : encodeur de notre toolchain (source de vérité pour notre code)
- `BIOS_REF.md` + `HW_REGISTERS.md` : registres hardware et BIOS
- `ngpc_romtool.py` : format header ROM
- Notes silicium dans MEMORY.md : opcodes cassés confirmés hardware
- `Stargunner.ngc` (CC900-compiled, source disponible) : ROM de test réelle
