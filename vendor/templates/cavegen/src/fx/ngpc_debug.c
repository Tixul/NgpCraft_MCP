/*
 * ngpc_debug.c - CPU profiler and debug overlay
 *
 * Part of NgpCraft_base_template (MIT License)
 * Written from hardware specification (ngpcspec.txt).
 *
 * Uses HW_RAS_V (0x8009) to read the current vertical raster position.
 * The visible area is 152 lines (0-151), then VBlank (~46 lines).
 * Total ~198 lines per frame at 60 Hz.
 *
 * Active display budget: 152 scanlines.
 * If game logic uses more than 152 lines, it's overflowing into VBlank.
 */

#include "ngpc_hw.h"
#include "ngpc_sys.h"     /* g_vb_counter */
#include "ngpc_gfx.h"
#include "ngpc_text.h"
#include "ngpc_debug.h"

#if NGPC_DEBUG

/* ---- State ---- */

static u8 s_start_line;    /* Raster line at begin() */
static u8 s_start_vb;      /* g_vb_counter at begin() */
static u8 s_fps_vb;        /* g_vb_counter when the FPS window opened */
static u8 s_used_lines;    /* Lines consumed last frame */
static u8 s_pct;           /* Percentage of frame budget used */
static u8 s_frame_count;   /* Frames counted this second */
static u8 s_fps;           /* FPS from last second */

/* Budget: visible area = 152 lines. */
#define FRAME_BUDGET 152

/* ---- Public API ---- */

/* Corrige campagne 2026-11-08 : positions are counted in lines since the
 * start of VBlank (line 152, where g_vb_counter is incremented), plus 199
 * lines per VBlank between begin() and end(). The old code only compared
 * two RAS_V values: a frame longer than one refresh was measured as a few
 * lines (green bar), and the wrap used 198 lines instead of 199. */
#define LINES_PER_FRAME 199u

/* Read (VBlank count, line) as one coherent pair. */
static void read_pos(u8 *vb, u8 *line)
{
    u8 v1, l, v2;
    for (;;) {
        v1 = g_vb_counter;
        l  = HW_RAS_V;
        v2 = g_vb_counter;
        if (v1 != v2) continue;
        if (l == 152u) { while (HW_RAS_V == 152u) ; continue; }  /* ISR may not have run yet */
        break;
    }
    *vb = v1;
    *line = l;
}

/* Lines elapsed since the last VBlank start. */
static u8 rel_line(u8 l)
{
    return (u8)((l >= 152u) ? (u8)(l - 152u) : (u8)(l + (LINES_PER_FRAME - 152u)));
}

void ngpc_debug_begin(void)
{
    read_pos(&s_start_vb, &s_start_line);
}

void ngpc_debug_end(void)
{
    u8 end_vb, end_line, frames;
    u16 used;

    read_pos(&end_vb, &end_line);
    frames = (u8)(end_vb - s_start_vb);
    used = (u16)((u16)frames * LINES_PER_FRAME + rel_line(end_line)) - rel_line(s_start_line);
    s_used_lines = (used > 255u) ? 255u : (u8)used;

    /* Calculate percentage: (lines / 152) * 100 */
    s_pct = (u8)(((u16)s_used_lines * 100) / FRAME_BUDGET);

    /* FPS (corrige campagne 2026-11-08) : end() calls per 60 REAL VBlanks.
     * The old counter counted end() calls as VBlanks, so it always said 60. */
    s_frame_count++;
    frames = (u8)(end_vb - s_fps_vb);
    if (frames >= 60u) {
        s_fps = (u8)(((u16)s_frame_count * 60u) / frames);
        s_frame_count = 0;
        s_fps_vb = end_vb;
    }
}

void ngpc_debug_draw_bar(u8 plane, u8 pal_ok, u8 pal_warn, u8 pal_over)
{
    /*
     * Draw a bar on the bottom tile row (y=18) of the screen.
     * Bar length = (used_lines / 152) * 20 tiles.
     * Color depends on usage: green < 80%, yellow 80-100%, red > 100%.
     */
    u8 bar_len;
    u8 pal;
    u8 i;
    u16 solid_tile = (u16)NGP_PROFILER_TILE;

    /* Load the solid block (8 rows, colour 3 everywhere). Done every call:
     * 8 word writes, and it survives a game that reloads its tiles. */
    for (i = 0; i < TILE_WORDS; i++)
        HW_TILE_DATA[(u16)(solid_tile * TILE_WORDS) + i] = 0xFFFFu;

    /* Calculate bar length in tiles (0-20). */
    bar_len = (u8)(((u16)s_used_lines * SCREEN_TW) / FRAME_BUDGET);
    if (bar_len > SCREEN_TW) bar_len = SCREEN_TW;

    /* Choose color based on budget usage. */
    if (s_pct > 100)
        pal = pal_over;     /* red: overflowed */
    else if (s_pct > 80)
        pal = pal_warn;     /* yellow: tight */
    else
        pal = pal_ok;       /* green: OK */

    /* Draw the bar. */
    for (i = 0; i < SCREEN_TW; i++) {
        if (i < bar_len)
            ngpc_gfx_put_tile(plane, i, SCREEN_TH - 1, solid_tile, pal);
        else
            ngpc_gfx_put_tile(plane, i, SCREEN_TH - 1, 0, 0); /* clear */
    }
}

void ngpc_debug_print_pct(u8 plane, u8 pal, u8 x, u8 y)
{
    /* Print "XX%" using text functions. */
    ngpc_text_print_num(plane, pal, x, y, (u16)s_pct, 3);
    ngpc_text_print(plane, pal, x + 3, y, "%");
}

void ngpc_debug_print_fps(u8 plane, u8 pal, u8 x, u8 y)
{
    ngpc_text_print_num(plane, pal, x, y, (u16)s_fps, 2);
    ngpc_text_print(plane, pal, x + 2, y, "FPS");
}

u8 ngpc_debug_get_lines(void)
{
    return s_used_lines;
}

u8 ngpc_debug_get_pct(void)
{
    return s_pct;
}

#endif /* NGPC_DEBUG */
