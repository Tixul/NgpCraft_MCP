/*
 * ngpc_raster.c - HBlank raster effects
 *
 * Part of NgpCraft_base_template (MIT License)
 * Written from hardware specification (ngpcspec.txt).
 *
 * Timer 0 on the TLCS-900/H is connected to the HBlank signal.
 * When configured properly, it fires an interrupt at each scanline.
 * We read HW_RAS_V to know which line we're on and apply the effect.
 *
 * Timer 0 setup:
 *   - TRUN bit 0 = enable Timer 0
 *   - T01MOD bits 1-0 = clock source (we use the HBlank signal)
 *   - TREG0 = reload value (1 = fire every HBlank)
 *   - Interrupt vector at 0x6FD4 (HW_INT_TIM0)
 */

#include "ngpc_hw.h"
#include "ngpc_gfx.h"
#include "ngpc_raster.h"

/* ---- State ---- */

/* Scroll table pointers (NULL = not active). */
static const u8 *s_scroll_x;
static const u8 *s_scroll_y;
static u8        s_scroll_plane;

/* Per-line callbacks (parallel arrays: no multiply in the ISR). */
static u8             s_cb_line[RASTER_MAX_CB];
static RasterCallback s_cb_fn[RASTER_MAX_CB];
static u8             s_cb_count;
/* Dispatch map indexed DIRECTLY by RAS_V at the H-int (0..198): slot+1 of
 * the first callback for the line that H-int announces (RAS_V+1, and line 0
 * for RAS_V 198), 0 = none. s_cb_next chains several callbacks on one line.
 * No index arithmetic, no search in the ISR: the user's write must land
 * before the next line is latched (see isr_cb). 199 + 8 bytes of RAM. */
#define RASTER_HINT_LINES 199u
static u8             s_cb_map[RASTER_HINT_LINES];
static u8             s_cb_next[RASTER_MAX_CB];

/* Parallax scroll buffer (filled by ngpc_raster_parallax). */
static u8 s_parallax_buf[152];
static u8 s_parallax_active;

/* What the ISRs actually use (set by raster_select()). */
static const u8 *s_tab_x;
static const u8 *s_tab_y;
static volatile u8 *s_reg_x;
static volatile u8 *s_reg_y;

/* ---- HBlank ISRs ----
 *
 * Corrige campagne 2026-11-08 :
 * 1. LIGNE : the H-int seen with RAS_V = N fires at the START of line N and
 *    announces line N+1 (ngpcspec: "signal generation begins 1 H before";
 *    no H-int at line 151, the one for line 0 comes at line 198). A write
 *    made during line N only shows from line N+1. The old ISR wrote
 *    table[RAS_V] -> every line got its neighbour's value and line 0 was
 *    never served. Each ISR now prepares the NEXT line.
 * 2. DUREE : the old all-in-one C ISR took ~543 cycles in emulation, more
 *    than a line (515): 145 IRQs/frame instead of 152, lines skipped,
 *    callbacks at some lines never called. One small ISR per mode is now
 *    installed by raster_select(); the generic one (with callbacks) is only
 *    used when callbacks are registered. Keep callbacks few (1-2). */

#define RASTER_NEXT(v) ((u8)(((v) == 198u) ? 0u : (u8)((v) + 1u)))

static void __interrupt isr_idle(void) { }

static void __interrupt isr_x(void)
{
    u8 l = HW_RAS_V;
    l = RASTER_NEXT(l);
    if (l < 152u) *s_reg_x = s_tab_x[l];
}

static void __interrupt isr_y(void)
{
    u8 l = HW_RAS_V;
    l = RASTER_NEXT(l);
    if (l < 152u) *s_reg_y = s_tab_y[l];
}

static void __interrupt isr_xy(void)
{
    u8 l = HW_RAS_V;
    l = RASTER_NEXT(l);
    if (l < 152u) {
        *s_reg_x = s_tab_x[l];
        *s_reg_y = s_tab_y[l];
    }
}

static void __interrupt isr_generic(void)
{
    u8 i;
    u8 v = HW_RAS_V;
    u8 l = RASTER_NEXT(v);
    if (l >= 152u) return;
    if (s_tab_x) *s_reg_x = s_tab_x[l];
    if (s_tab_y) *s_reg_y = s_tab_y[l];
    /* A callback registered for line L runs during line L-1, so what it
     * writes is in place when line L is drawn. */
    i = s_cb_map[v];
    while (i) {
        s_cb_fn[i - 1u](l);
        i = s_cb_next[i - 1u];
    }
}

static void cb_reset(void)
{
    u8 i;
    s_cb_count = 0;
    for (i = 0; i < RASTER_HINT_LINES; i++) s_cb_map[i] = 0;
}

/* Callbacks only (no table): the shortest path to the user's write.
 * Lookup straight from RAS_V, then the call. TIMING: from the H-int (start
 * of line L-1) to the callback's register write there is one line (515
 * cycles at 6.144 MHz) minus the ISR entry; measured in emulation with a
 * one-store callback: see gfx_raster ph4. A long callback writes late. */
static void __interrupt isr_cb(void)
{
    u8 k = s_cb_map[HW_RAS_V];
    while (k) {
        s_cb_fn[k - 1u](s_cb_line[k - 1u]);
        k = s_cb_next[k - 1u];
    }
}

/* Pick the smallest ISR for the current state and install it. */
static void raster_select(void)
{
    /* Idle first: an HBlank between two pointer updates must not use a
     * half-updated state. */
    HW_INT_TIM0 = (IntHandler *)isr_idle;
    if (s_parallax_active) {
        s_tab_x = s_parallax_buf;
        s_tab_y = 0;
    } else {
        s_tab_x = s_scroll_x;
        s_tab_y = s_scroll_y;
    }
    s_reg_x = (s_scroll_plane == GFX_SCR1) ? &HW_SCR1_OFS_X : &HW_SCR2_OFS_X;
    s_reg_y = (s_scroll_plane == GFX_SCR1) ? &HW_SCR1_OFS_Y : &HW_SCR2_OFS_Y;

    if (s_cb_count > 0 && !s_tab_x && !s_tab_y) HW_INT_TIM0 = (IntHandler *)isr_cb;
    else if (s_cb_count > 0)       HW_INT_TIM0 = (IntHandler *)isr_generic;
    else if (s_tab_x && s_tab_y)   HW_INT_TIM0 = (IntHandler *)isr_xy;
    else if (s_tab_x)              HW_INT_TIM0 = (IntHandler *)isr_x;
    else if (s_tab_y)              HW_INT_TIM0 = (IntHandler *)isr_y;
    else                           HW_INT_TIM0 = (IntHandler *)isr_idle;
}

/* ---- Public API ---- */

void ngpc_raster_init(void)
{
    /* Clear state. */
    s_scroll_x = 0;
    s_scroll_y = 0;
    s_scroll_plane = GFX_SCR1;
    cb_reset();
    s_parallax_active = 0;

    /* Install HBlank interrupt handler (the one matching the state). */
    raster_select();

    /*
     * Configure Timer 0 for HBlank:
     * - TREG0 = 1 (fire every HBlank)
     * - T01MOD: Timer 0 clock source = internal (bits 1-0)
     * - TRUN bit 0 = 1 (start Timer 0)
     *
     * Timing: one scanline is ~515 CPU cycles (~84 us) at 6.144 MHz
     * (silicon-measured: 515 cyc/line x 199 lines/frame ~= 102485 cyc/frame,
     * 59.95 Hz). TREG0 counts HBlanks/scanlines here, NOT CPU cycles, because
     * the clock source is TI0 (the K2GE H-int), one tick per visible line.
     */
    HW_T01MOD &= (u8)~0xC3; /* Timer0 clock = TI0, 8-bit mode */
    HW_TREG0 = 0x01;

    /* CRITICAL: enabling the Timer0 IRQ MUST go through the BIOS. Writing the
     * interrupt-level registers directly does NOT arm the IRQ on NGPC hardware
     * -- the BIOS owns the interrupt-level hardware. Convention per the official
     * Toshiba SDK (8Bit.txt "H-int Setting"): rw3 = VECT_INTLVSET (4),
     * rb3 = priority level, rc3 = interrupt number (2 = 8-bit Timer 0). */
    __asm("ldb rb3, 4");   /* priority level 4 (VBlank-level, fires under EI 0) */
    __asm("ldb rc3, 2");   /* interrupt number 2 = Timer0                       */
    __asm("ldb rw3, 4");   /* rw3 = BIOS_INTLVSET (= 4)                         */
    __asm("swi 1");        /* BIOS installs the level; NOW the IRQ can fire     */

    HW_TRUN |= 0x01;   /* Enable Timer 0 */
}

void ngpc_raster_disable(void)
{
    HW_TRUN &= (u8)~0x01;  /* Stop Timer 0 */
    /* Do not clear HW_INT_TIM0 to zero: a pending interrupt firing after
     * the timer is stopped would jump to address 0 and crash.  The old
     * handler is harmless if it runs one extra time. */

    s_scroll_x = 0;
    s_scroll_y = 0;
    cb_reset();
    s_parallax_active = 0;
    raster_select();   /* idle ISR: a pending request is harmless */
}

void ngpc_raster_set_scroll_table(u8 plane, const u8 *table_x, const u8 *table_y)
{
    s_scroll_plane = plane;
    s_scroll_x = table_x;
    s_scroll_y = table_y;
    s_parallax_active = 0;
    raster_select();
}

void ngpc_raster_clear_scroll(void)
{
    s_scroll_x = 0;
    s_scroll_y = 0;
    s_parallax_active = 0;
    raster_select();
}

u8 ngpc_raster_set_callback(u8 line, RasterCallback cb)
{
    if (s_cb_count >= RASTER_MAX_CB) return 0xFF;

    if (line >= 152) return 0xFF;
    s_cb_line[s_cb_count] = line;
    s_cb_fn[s_cb_count]   = cb;
    s_cb_next[s_cb_count] = 0;
    {
        /* H-int that announces `line`: RAS_V = line-1, or 198 for line 0. */
        u8 v = (line == 0u) ? (u8)(RASTER_HINT_LINES - 1u) : (u8)(line - 1u);
        if (s_cb_map[v] == 0u) {
            s_cb_map[v] = (u8)(s_cb_count + 1u);
        } else {                           /* append to that line's chain */
            u8 k = s_cb_map[v];
            while (s_cb_next[k - 1u]) k = s_cb_next[k - 1u];
            s_cb_next[k - 1u] = (u8)(s_cb_count + 1u);
        }
    }
    s_cb_count++;
    raster_select();

    return s_cb_count - 1;
}

void ngpc_raster_clear_callbacks(void)
{
    cb_reset();
    raster_select();
}

void ngpc_raster_parallax(u8 plane, const RasterBand *bands,
                           u8 count, u16 base_x)
{
    u8 i, line;

    s_scroll_plane = plane;
    s_parallax_active = 1;

    /* Fill the 152-line buffer with per-band scroll values. */
    for (i = 0; i < count; i++) {
        u8 start = bands[i].top_line;
        u8 end   = (i + 1 < count) ? bands[i + 1].top_line : 152;

        /* scroll_x = (base_x * speed) >> 8 */
        u8 sx = (u8)(((u32)base_x * (u32)bands[i].speed) >> 8);

        for (line = start; line < end; line++)
            s_parallax_buf[line] = sx;
    }
    raster_select();
}
