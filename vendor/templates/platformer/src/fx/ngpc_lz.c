/*
 * ngpc_lz.c - Tile data decompression (RLE + LZ77)
 *
 * Part of NgpCraft_base_template (MIT License)
 *
 * Standard decompression algorithms. The compression is done offline
 * by a companion Python tool (ngpc_compress.py).
 *
 * RLE: ~50 bytes of code, ~2 cycles per output byte.
 * LZ77: ~120 bytes of code, ~5-10 cycles per output byte.
 * Both are fast enough to decompress a full tileset during loading.
 */

#include "ngpc_hw.h"
#include "ngpc_gfx.h"
#include "ngpc_lz.h"

/* ---- RLE ---- */

u16 ngpc_rle_decompress(u8 *dst, const u8 *src, u16 src_len)
{
    const u8 *end = src + src_len;
    u8 *out = dst;

    while (src < end) {
        u8 ctrl = *src++;

        if (ctrl & 0x80) {
            /* Run: repeat next byte N+1 times. */
            u8 count = (ctrl & 0x7F) + 1;
            u8 val = *src++;
            while (count--)
                *out++ = val;
        } else {
            /* Literal: copy next N+1 bytes. */
            u8 count = ctrl + 1;
            while (count--)
                *out++ = *src++;
        }
    }

    return (u16)(out - dst);
}

/* ---- LZ77 (LZSS) ---- */

u16 ngpc_lz_decompress(u8 *dst, const u8 *src, u16 src_len)
{
    const u8 *end = src + src_len;
    u8 *out = dst;

    while (src < end) {
        u8 flags = *src++;
        u8 bit;

        for (bit = 0; bit < 8 && src < end; bit++) {
            if (flags & 0x80) {
                /* Match: copy from earlier in the output. */
                u8 b0 = *src++;
                u8 b1 = *src++;
                u16 offset = ((u16)(b0 >> 4) << 8) | (u16)b1;
                u8  length = (b0 & 0x0F) + 3;
                u8 *match  = out - offset;

                while (length--)
                    *out++ = *match++;
            } else {
                /* Literal: copy one byte. */
                *out++ = *src++;
            }

            flags <<= 1;
        }
    }

    return (u16)(out - dst);
}

/* ---- Convenience ---- */

/*
 * Corrige campagne 2026-11-08 : decompress STRAIGHT into Character RAM.
 * The old 2 KB static buffer (s_decomp_buf) was linked into every ROM
 * (ngpc_lz.rel is always built: 17 % of the 12 KB RAM, used or not) and
 * *_to_tiles() overflowed it without a check above 2048 bytes. Tile RAM is
 * byte-addressable and readable, so the LZ back-references can read it, and
 * the byte order is the one ngpc_gfx_load_tiles_at() wrote anyway.
 * Same rule as any VRAM write: do it with the screen off or in VBlank.
 */
#define LZ_TILE_RAM(tile) ((u8 *)(0xA000u + (u16)((tile) * 16u)))

void ngpc_rle_to_tiles(const u8 *src, u16 src_len, u16 tile_offset)
{
    (void)ngpc_rle_decompress(LZ_TILE_RAM(tile_offset), src, src_len);
}

void ngpc_lz_to_tiles(const u8 *src, u16 src_len, u16 tile_offset)
{
    (void)ngpc_lz_decompress(LZ_TILE_RAM(tile_offset), src, src_len);
}
