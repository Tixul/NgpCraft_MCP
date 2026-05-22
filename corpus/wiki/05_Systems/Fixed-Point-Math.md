# Fixed-Point Math

Fixed-point arithmetic, precomputed lookup tables, and tile decompression for NGPC homebrew.

---


## 1. Fixed-Point Conventions

### 1.1 Formats Used

Fixed-point is the standard technique for sub-pixel positions and smooth velocities on NGPC
(no FPU, no `float` on TLCS-900H).

Common formats in NGPC homebrew:

| Format | Precision | Typical use |
|--------|-----------|-------------|
| 8.8 (u16, 256ths) | 1/256 pixel per unit | Fine-grained position |
| 1.7 (u8, 128ths) | 1/128 pixel per unit | Compact position/velocity |
| 9.7 (u16, 128ths) | 1/128 pixel per unit | Most common: fits screen+off-screen |

Typical velocity range: 64..384 units/frame = 0.5..3 pixels/frame (at 128ths resolution).

### 1.2 Common Operations

```c
/* Convert fixed-point to screen pixel */
s16 screen_x = fixed_x >> 7;    /* 128ths -> pixels */
s16 screen_x = fixed_x >> 8;    /* 256ths -> pixels */

/* Move entity */
fixed_x += velocity_x;          /* velocity in same units */

/* Collision: compare in fixed-point units, not pixels */
s16 dx = (s16)(a_x - b_x);      /* both in fixed units */
if (dx < 0) dx = -dx;
if (dx < HITBOX_W << 7) { /* hit */ }

/* Integer multiply (constant divisor = shift) */
s16 half  = x >> 1;   /* x / 2 */
s16 sixth = x / 6;    /* division — cc900 calls C9H_divlu (software) */
```

### 1.3 Overflow and Promotion

- `u16 * u16` with the same type → hardware MUL (fast).
- `u16 * u8` mixed → software `C9H_mullu` (slow-ish).
- If the result can exceed `u16`, cast to `u32` **before** multiplying:

```c
/* Safe: intermediate result in u32 */
u32 r = (u32)a * b;
s16 px = (s16)(r >> 7);

/* WRONG: overflow if a * b > 65535 */
s16 px = (s16)((a * b) >> 7);
```

> Never use `float` — there is no FPU. Every floating-point operation would call a
> software emulation library that does not exist on NGPC.

---

## 2. Math API

### 2.1 Trigonometry

```c
s8 ngpc_sin(u8 angle);   /* sine lookup, returns -127..127 */
s8 ngpc_cos(u8 angle);   /* cosine lookup, same range */
```

Angle encoding: 8-bit circle — 0-255 maps to 0°-360° (wraps).

| Value | Degrees |
|-------|---------|
| 0 | 0° |
| 64 | 90° |
| 128 | 180° |
| 192 | 270° |
| 256 | wraps to 0 |

Usage:

```c
/* Move in direction `angle` at speed 1 (in 1/127 pixel units) */
entity_x += ngpc_cos(angle);
entity_y += ngpc_sin(angle);
```

### 2.2 Random Number Generation

```c
void ngpc_rng_seed(void);        /* seed from VBlank counter */
u16  ngpc_random(u16 max);       /* returns 0..max (LCG, good quality) */
void ngpc_qrandom_init(void);    /* shuffle pre-built table (call after rng_seed) */
u8   ngpc_qrandom(void);         /* ultra-fast table read, returns 0..255 */
```

`ngpc_random()` is a u32 LCG. It extracts bits 16-30 of the state → result
should modulo down to `0..max`, but **on cc900 the `result % ((u32)max + 1)`
step is broken** (u32 modulo runtime helper miscompiles). In practice the
returned value stays in **0..32767** regardless of `max`.

**Consequence — hardware confirmed:**
- `(u8)ngpc_random(6)` → value ≥ 5 in ~98% of calls (expected 2/7)
- `(u8)ngpc_random(1)` → random 0..255 instead of 0..1
- Any gameplay roll using `ngpc_random(max)` with small `max` will ignore `max`

**Rule: never rely on `ngpc_random(max)` for a bounded gameplay value.**
Use `ngpc_qrandom()` + u8 modulo instead — cc900 handles u8 arithmetic cleanly:

```c
/* Bounded roll, correct on cc900 */
u8 roll  = ngpc_qrandom() % 7u;          /* 0..6 */
u8 bonus = ngpc_qrandom() % attack_pow;  /* 0..attack_pow-1 */
```

`ngpc_qrandom()` is zero-cost (table index increment + read). The pre-shuffle
is done with `ngpc_random` internally so the permutation is biased, but the
output is still a permutation over 0..255, so `qrandom() % N` distributes
correctly.

For wide ranges where a single `qrandom()` call isn't enough, combine two:

```c
u16 big_rand = (u16)ngpc_qrandom() | ((u16)ngpc_qrandom() << 8); /* 0..65535 */
```

### 2.3 32-bit Multiply

```c
s32 ngpc_mul32(s32 a, s32 b);   /* 32-bit signed multiply */
```

Useful when both operands may be large and the result needs full 32-bit range.

> Note: on cc900, `s16 * s16` calls `C9H_mulls` (software). `u16 * u16` (same width)
> uses the hardware MUL opcode. For best performance, keep operands the same width.

---

## 3. Lookup Table Math

```c
u8  ngpc_lut_atan2(s8 dx, s8 dy);   /* angle in 0-255 format */
u8  ngpc_lut_sqrt16(u16 n);          /* integer sqrt, returns 0-255 */
u16 ngpc_lut_dist(s8 dx, s8 dy);     /* approx distance, ~4% error */
u16 ngpc_lut_div(u16 n, u8 divisor); /* fast division via reciprocal multiply */
```

All use fixed-point or integer arithmetic. Zero FPU. Minimal CPU cost.

Usage example — aim a bullet at a target:

```c
s8 dx = (s8)(target_x - bullet_x);
s8 dy = (s8)(target_y - bullet_y);
u8 angle = ngpc_lut_atan2(dx, dy);   /* 0-255 angle toward target */
bullet_vx = ngpc_cos(angle);
bullet_vy = ngpc_sin(angle);
```

> Use `ngpc_lut_dist()` for range checks instead of `sqrt()`.
> For AABB overlap, avoid distance entirely — compare rect edges directly (see [Collision](Collision.md)).

---

## 4. BCD Conversion

Binary-coded decimal is the cheap way to render scores, timers, and counters as
digits without per-frame division (which calls the slow `C9H_divlu` helper).

### 4.1 Binary to Packed BCD (no division)

Convert a 32-bit binary value to 8-digit packed BCD using a power-of-10 table and
repeated subtract-and-count per digit — no division required. For each decimal
place, subtract the corresponding power of 10 while it fits, counting the
subtractions to get that digit, then shift the digit into the result with
`sla 4,XWA` (shift the accumulator left one nibble). Worst case is ~54
subtractions for a full 8-digit value.

```
for each power-of-10 P (10^7 down to 10^0):
    digit = 0
    while value >= P:  value -= P;  digit++   ; count subtractions
    result = (result << 4) | digit            ; sla 4,XWA then OR in the nibble
```

### 4.2 Byte-Level BCD Helpers

For single packed-BCD bytes (two digits):

```c
/* packed BCD byte -> binary 0..99 */
u8 bcd_to_bin(u8 bcd) { return (u8)((bcd >> 4) * 10 + (bcd & 0x0F)); }

/* binary 0..99 -> packed BCD byte */
u8 bin_to_packed(u8 v) { return (u8)(((v % 10) << 4) | (v / 10)); }
```

---

## 5. Tile Decompression

### 5.1 Runtime Decompression API

```c
/* RLE — simple, fast, ~2:1 ratio */
u16  ngpc_rle_decompress(void *dst, const void NGP_FAR *src, u16 src_len);
void ngpc_rle_to_tiles(const void NGP_FAR *src, u16 src_len, u16 tile_offset);

/* LZ77/LZSS — better ratio, ~3:1 to 4:1 */
u16  ngpc_lz_decompress(void *dst, const void NGP_FAR *src, u16 src_len);
void ngpc_lz_to_tiles(const void NGP_FAR *src, u16 src_len, u16 tile_offset);
```

The `_to_tiles` functions use a **2 KB internal buffer** (~128 tiles maximum per call).
For larger tilesets, call in chunks with increasing `tile_offset`.

Usage example:

```c
/* Load compressed tileset at tile slot 96 */
extern const u8 NGP_FAR level1_tiles_lz[];     /* compressed, in ROM */
extern const u16 level1_tiles_lz_len;
ngpc_lz_to_tiles(level1_tiles_lz, level1_tiles_lz_len, 96);
```

### 5.2 Offline Compression Tool

The `ngpc_compress` tool compresses raw tile binary data:

```bash
# LZ77 (default) — emits <name>_lz[] + <name>_lz_len
ngpc_compress tiles.bin -o tiles_lz.c -n level1_tiles --header

# RLE — emits <name>_rle[] + <name>_rle_len
ngpc_compress tiles.bin -o tiles_rle.c -m rle -n level1_tiles --header

# Auto-pick smallest
ngpc_compress tiles.bin -o tiles_best.c -m both -n level1_tiles --header
```

Generated symbols:

| Symbol | Meaning |
|--------|---------|
| `<name>_lz[]` / `<name>_rle[]` | Compressed data array |
| `<name>_lz_len` | Compressed size in bytes — pass to runtime functions |
| `<name>_raw_len` | Decompressed size (informational) |

### 5.3 Compression Constraints and Notes

- Input must be a raw binary file (`.bin` or any byte stream — not a PNG).
- The tool verifies roundtrip integrity by default (compress → decompress → compare).
- Naming convention: `_lz` (not `_lz77`), `_rle`.
- Use the tilemap tool's `--tiles-bin` flag to generate the raw binary input:

```bash
ngpc_tilemap level.png -o level.c -n level --header --tiles-bin level_tiles.bin
# then compress:
ngpc_compress level_tiles.bin -o level_tiles_lz.c -n level_tiles --header
```

- `_to_tiles` functions use a 2 KB internal buffer — max ~128 tiles per call.
  For tilesets > 128 tiles, decompress in two or more calls with offset stepping.

---

## Quick Reference

| Item | Value / Pattern |
|------|-----------------|
| Fixed-point 128ths | `>> 7` to get pixels |
| Fixed-point 256ths | `>> 8` to get pixels |
| Typical velocity | 64..384 units/frame (128ths) = 0.5..3 px/frame |
| Safe u16×u16 | Same-width cast: `(u16)a * (u16)b` → HW MUL |
| Safe large multiply | `(u32)a * b` before shifting down |
| Angle range | 0-255 → 0-360° (64=90°, 128=180°, 192=270°) |
| `ngpc_sin/cos` range | Returns -127..127 |
| `ngpc_random` range | **Broken on cc900** — ignores `max`, returns 0..32767. Use `ngpc_qrandom() % N` for bounded rolls |
| `ngpc_qrandom` | Zero-cost: table read, 0-255. Safe to `% N` for any N |
| `ngpc_lut_atan2` | Returns 0-255 angle toward target |
| `ngpc_lut_dist` | ~4% error, no sqrt |
| `ngpc_lut_sqrt16` | Integer sqrt of u16 |
| LZ77 ratio | ~3:1 to 4:1 |
| RLE ratio | ~2:1 |
| `_to_tiles` buffer | 2 KB = ~128 tiles per call max |
| `<name>_lz_len` | Pass to `ngpc_lz_to_tiles()` — compressed size |
| No float | Zero FPU on TLCS-900H — never use `float` / `double` |
| Division by 2^n | Use right-shift (`>> n`) — much faster than `/` |

---

## See Also

- [Collision](Collision.md) — AABB overlap (no sqrt needed), dist² comparison
- [Asset Pipeline](../06_Pipeline-and-Patterns/Asset-Pipeline.md) — Full tilemap export commands, `--tiles-bin` flag
- [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — cc900 MUL/DIV codegen, C9H_mulls/divlu, overflow rules
- [Game Loop](Game-Loop.md) — Frame budget for math-heavy code
