/*
 * ngpc_lut.c - Precomputed lookup tables
 *
 * Part of NgpCraft_base_template (MIT License)
 *
 * Tables computed mathematically (pure math, no copyright concern).
 */

#include "ngpc_lut.h"

/* ---- atan2 lookup ---- */

/*
 * Octant lookup table: 32 entries covering one octant (0-45 degrees),
 * indexed by the ratio k = (min * 31) / max. Other octants by symmetry.
 * Values 0-32 (0 to 45 degrees in 256 units).
 */
/* Corrige campagne 2026-11-08 : the old table ended at 18 (diagonal = 18
 * instead of 32, up to ~20 degrees off). Entry k = round(atan(k/31)*128/PI). */
static const u8 atan_oct[32] = {
     0,  1,  3,  4,  5,  7,  8,  9,
    10, 12, 13, 14, 15, 16, 17, 18,
    19, 20, 21, 22, 23, 24, 25, 26,
    27, 28, 28, 29, 30, 31, 31, 32
};

u8 ngpc_lut_atan2(s8 dx, s8 dy)
{
    u8 ax, ay, angle, idx;

    /* Handle zero vector. */
    if (dx == 0 && dy == 0) return 0;

    /* Absolute values. */
    ax = (dx < 0) ? (u8)(-dx) : (u8)dx;
    ay = (dy < 0) ? (u8)(-dy) : (u8)dy;

    /* Scale to 0-31 range for table lookup.
     * Use the ratio ay/ax (or ax/ay) scaled to 0-31. */
    if (ax >= ay) {
        /* First octant (0-45 deg): index = (ay * 31) / ax */
        idx = (ax > 0) ? (u8)(((u16)ay * 31) / (u16)ax) : 0;
        angle = atan_oct[idx];
    } else {
        /* Second octant (45-90 deg): 64 - table[(ax*31)/ay] */
        idx = (ay > 0) ? (u8)(((u16)ax * 31) / (u16)ay) : 0;
        angle = 64 - atan_oct[idx];
    }

    /* Map to full 0-255 range based on quadrant.
     * Convention: 0=right, 64=down, 128=left, 192=up. */
    if (dx >= 0 && dy >= 0) return angle;         /* Q1: 0-63   */
    if (dx < 0  && dy >= 0) return 128 - angle;   /* Q2: 64-128 */
    if (dx < 0  && dy < 0)  return 128 + angle;   /* Q3: 128-192 */
    return 256 - angle;                            /* Q4: 192-255 */
}

/* ---- Integer square root ---- */

u8 ngpc_lut_sqrt16(u16 n)
{
    /*
     * Binary search for floor(sqrt(n)).
     * 8 iterations for 16-bit input (result 0-255).
     * No table needed, pure computation, ~80 cycles.
     */
    u8 result = 0;
    u8 bit = 128;   /* Start from highest bit */

    while (bit > 0) {
        u8 test = result | bit;
        if ((u16)test * (u16)test <= n)
            result = test;
        bit >>= 1;
    }

    return result;
}

/* ---- Approximate distance ---- */

u16 ngpc_lut_dist(s16 dx, s16 dy)
{
    /*
     * Alpha max + beta min approximation.
     * alpha = 1.0, beta = 0.414 ~= 106/256
     * dist ~= max + (min * 106) >> 8
     * Max error: ~4% (overestimates).
     */
    u16 ax = (dx < 0) ? (u16)(-dx) : (u16)dx;
    u16 ay = (dy < 0) ? (u16)(-dy) : (u16)dy;
    u16 mn, mx;

    if (ax > ay) { mx = ax; mn = ay; }
    else         { mx = ay; mn = ax; }

    /* Corrige campagne 2026-11-08 : mn * 106 overflowed 16 bits (cc900 int)
     * as soon as mn > 618: (1000,1000) gave 1158 instead of 1414. */
    return (u16)(mx + (u16)(((u32)mn * 106u) >> 8));
}

/* ---- Fast division by small constant ---- */


u16 ngpc_lut_div(u16 n, u8 divisor)
{
    if (divisor == 0) return 0xFFFF;    /* saturate on /0 */

    /* Corrige campagne 2026-11-08 : the truncated reciprocal 65536/d gave
     * exact multiples one too low ((6,3) -> 1, (100,10) -> 9), and computing
     * it was itself a 32-bit division. A u16/u16 divide is one hardware DIV
     * on the TLCS-900 and is exact for every n. */
    return (u16)(n / (u16)divisor);
}
