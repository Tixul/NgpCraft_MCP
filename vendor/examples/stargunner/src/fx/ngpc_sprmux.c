/*
 * ngpc_sprmux.c - Sprite multiplexing
 *
 * Part of NGPC Template 2026 (MIT License)
 * Written from NGPC hardware behavior and TLCS-900/H interrupt model.
 */

#include "ngpc_hw.h"
#include "ngpc_sprmux.h"

#define INVALID_INDEX    0xFF
#define SPRITE_HEIGHT    8

typedef struct {
    s16 x;
    s16 y;
    u16 tile;
    u8  pal;
    u8  flags;
} SprmuxSprite;

/* Logical frame list (rebuilt every frame). */
static SprmuxSprite s_sprites[SPRMUX_MAX_LOGICAL];
static u8 s_sprite_count;
static u8 s_overflow;

/* Per-line linked lists:
 * start_head[line] -> sprites starting visibility at this line
 * end_head[line]   -> sprites ending visibility before this line */
static u8 s_start_head[SCREEN_H + 1];
static u8 s_end_head[SCREEN_H + 1];
static u8 s_start_next[SPRMUX_MAX_LOGICAL];
static u8 s_end_next[SPRMUX_MAX_LOGICAL];

/* Runtime mapping logical sprite <-> hardware slot. */
static u8 s_sprite_slot[SPRMUX_MAX_LOGICAL];
static u8 s_free_stack[SPR_MAX];
static u8 s_free_top;

/* ---- Low-level helpers ---- */

static void hw_hide_slot(u8 slot)
{
    volatile u8 *s = HW_SPR_DATA + ((u16)slot << 2);
    s[1] &= (u8)~(3 << 3);  /* SPR_HIDE */
}

static void hw_program_slot(u8 slot, const SprmuxSprite *sp)
{
    volatile u8 *s = HW_SPR_DATA + ((u16)slot << 2);
    u8 flags = sp->flags;

    /* If caller forgot priority bits, default to front. */
    if ((flags & (3 << 3)) == SPR_HIDE)
        flags = (u8)((flags & (u8)~(3 << 3)) | SPR_FRONT);

    s[0] = (u8)(sp->tile & 0xFF);
    s[1] = (u8)(flags | ((sp->tile >> 8) & 1));
    s[2] = (u8)sp->x;
    s[3] = (u8)sp->y;
    HW_SPR_PAL[slot] = sp->pal & 0x0F;
}

static u8 alloc_slot(void)
{
    if (s_free_top == 0)
        return INVALID_INDEX;
    s_free_top--;
    return s_free_stack[s_free_top];
}

static void free_slot(u8 slot)
{
    if (s_free_top < SPR_MAX) {
        s_free_stack[s_free_top] = slot;
        s_free_top++;
    }
}

static void sprite_start(u8 idx)
{
    u8 slot = alloc_slot();
    if (slot == INVALID_INDEX) {
        s_overflow++;
        return;
    }

    s_sprite_slot[idx] = slot;
    hw_program_slot(slot, &s_sprites[idx]);
}

static void sprite_stop(u8 idx)
{
    u8 slot = s_sprite_slot[idx];
    if (slot == INVALID_INDEX)
        return;

    hw_hide_slot(slot);
    free_slot(slot);
    s_sprite_slot[idx] = INVALID_INDEX;
}

/* ---- HBlank ISR ---- */

static void __interrupt isr_sprmux_hblank(void)
{
    u8 line = HW_RAS_V;
    u8 next_line;
    u8 idx;

    if (line >= SCREEN_H)
        return;

    /* HBlank occurs between lines: update allocations for next line. */
    next_line = (u8)(line + 1);

    /* Release sprites that are no longer visible from next_line onward. */
    idx = s_end_head[next_line];
    while (idx != INVALID_INDEX) {
        u8 next = s_end_next[idx];
        sprite_stop(idx);
        idx = next;
    }

    /* Assign freed slots to sprites that start on next_line. */
    if (next_line < SCREEN_H) {
        idx = s_start_head[next_line];
        while (idx != INVALID_INDEX) {
            u8 next = s_start_next[idx];
            sprite_start(idx);
            idx = next;
        }
    }
}

/* ---- Build step ---- */

static void sort_logical_sprites(void)
{
    /* Stable insertion sort by Y, then X. */
    u8 i;

    for (i = 1; i < s_sprite_count; i++) {
        SprmuxSprite key = s_sprites[i];
        u8 j = i;

        while (j > 0) {
            SprmuxSprite *prev = &s_sprites[j - 1];
            if (prev->y > key.y ||
                (prev->y == key.y && prev->x > key.x)) {
                s_sprites[j] = *prev;
                j--;
            } else {
                break;
            }
        }
        s_sprites[j] = key;
    }
}

static void clear_line_lists(void)
{
    u8 line;
    for (line = 0; line <= SCREEN_H; line++) {
        s_start_head[line] = INVALID_INDEX;
        s_end_head[line] = INVALID_INDEX;
    }
}

static void reset_slot_state(void)
{
    u8 i;

    s_free_top = 0;
    for (i = 0; i < SPR_MAX; i++) {
        hw_hide_slot(i);
        s_free_stack[s_free_top] = i;
        s_free_top++;
    }

    for (i = 0; i < s_sprite_count; i++)
        s_sprite_slot[i] = INVALID_INDEX;
}

static void build_line_lists(void)
{
    u8 i;

    clear_line_lists();

    for (i = 0; i < s_sprite_count; i++) {
        s16 y0 = s_sprites[i].y;
        s16 y1 = (s16)(y0 + SPRITE_HEIGHT); /* end is exclusive */
        u8 start;
        u8 end;

        if ((s_sprites[i].flags & (3 << 3)) == SPR_HIDE)
            continue;

        if (y1 <= 0 || y0 >= SCREEN_H)
            continue;

        start = (y0 < 0) ? 0 : (u8)y0;
        end = (y1 > SCREEN_H) ? SCREEN_H : (u8)y1;

        s_start_next[i] = s_start_head[start];
        s_start_head[start] = i;

        s_end_next[i] = s_end_head[end];
        s_end_head[end] = i;
    }
}

static void prime_first_line(void)
{
    u8 idx = s_start_head[0];

    while (idx != INVALID_INDEX) {
        u8 next = s_start_next[idx];
        sprite_start(idx);
        idx = next;
    }
}

/* ---- Public API ---- */

void ngpc_sprmux_begin(void)
{
    s_sprite_count = 0;
    s_overflow = 0;
}

u8 ngpc_sprmux_add(s16 x, s16 y, u16 tile, u8 pal, u8 flags)
{
    u8 idx;

    if (s_sprite_count >= SPRMUX_MAX_LOGICAL)
        return INVALID_INDEX;

    idx = s_sprite_count;
    s_sprite_count++;

    s_sprites[idx].x = x;
    s_sprites[idx].y = y;
    s_sprites[idx].tile = tile;
    s_sprites[idx].pal = pal;
    s_sprites[idx].flags = flags;

    return idx;
}

void ngpc_sprmux_flush(void)
{
    sort_logical_sprites();
    reset_slot_state();
    build_line_lists();
    prime_first_line();

    /* Uses Timer 0 HBlank interrupt (shared resource with ngpc_raster). */
    HW_INT_TIM0 = (IntHandler *)isr_sprmux_hblank;
    HW_T01MOD &= (u8)~0xC3; /* Timer0 clock = TI0, 8-bit mode */
    HW_TREG0 = 0x01;
    HW_TRUN |= 0x01;
}

void ngpc_sprmux_disable(void)
{
    u8 i;

    HW_TRUN &= (u8)~0x01;
    /* Do not clear HW_INT_TIM0 to zero: a pending interrupt could jump to
     * address 0 and crash.  The stale handler is harmless once the timer
     * is stopped. */

    for (i = 0; i < SPR_MAX; i++)
        hw_hide_slot(i);
}

u8 ngpc_sprmux_overflow_count(void)
{
    return s_overflow;
}
