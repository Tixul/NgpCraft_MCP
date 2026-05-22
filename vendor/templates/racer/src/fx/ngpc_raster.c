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

/* Software line counter: reset to 0 at VBlank by ngpc_raster_vsync(),
 * incremented once per Timer0 ISR call.  Replaces HW_RAS_V which
 * does not reliably change per-scanline inside the Timer0 ISR. */
static volatile u8 s_hblank_cnt;

/* Diagnostic: counts ISR fires per frame (reset in ngpc_raster_vsync).
 * Read from main after vsync to display.  Value 152 = correct rate. */
volatile u8 ngpc_dbg_isr_fires;

/* Scroll table pointers (NULL = not active). */
static const u8 *s_scroll_x;
static const u8 *s_scroll_y;
static u8        s_scroll_plane;

/* Per-line callbacks. */
typedef struct {
    u8              line;
    RasterCallback  cb;
} RasterCbEntry;

static RasterCbEntry s_callbacks[RASTER_MAX_CB];
static u8            s_cb_count;

/* Parallax scroll buffer (filled by ngpc_raster_parallax). */
static u8 s_parallax_buf[152];
static u8 s_parallax_active;

/* Flag set inside the ISR when HW_RAS_V >= 152 (VBlank).
 * On the first active-display ISR that follows, s_hblank_cnt is reset to 0.
 * This corrects for the ~46 Timer0 fires that occur during the VBlank period
 * after ngpc_raster_vsync() resets the counter. */
static u8 s_was_vblank;

/* Per-scanline palette swap (road perspective narrowing).
 * Each frame the ISR writes c2 of PAL_ROAD_B (idx 18) and PAL_ROAD_A (idx 22)
 * switching between grass and road color at the two threshold scanlines. */
static u8            s_pal_active;
static volatile u16  s_pal_grass_c2;
static volatile u16  s_pal_road_c2;
static u8            s_pal_mid_thr;   /* scanline where mid band turns road-gray  */
static u8            s_pal_out_thr;   /* scanline where outer band turns road-gray */

/* ---- HBlank ISR ---- */

static void __interrupt isr_hblank(void)
{
    u8 line;

    ngpc_dbg_isr_fires = (u8)(ngpc_dbg_isr_fires + 1u);

    /* VBlank detection: HW_RAS_V is reliable for the large VBlank/active
     * distinction even if it is not perfectly per-scanline during active display. */
    if (HW_RAS_V >= 152u) {
        s_was_vblank = 1u;
        return;
    }

    /* First active-display ISR after VBlank: re-synchronize software counter.
     * This discards the ~46 spurious increments that would have accumulated
     * during the VBlank Timer0 fires since ngpc_raster_vsync() ran. */
    if (s_was_vblank) {
        s_hblank_cnt = 0u;
        s_was_vblank = 0u;
    }

    line = s_hblank_cnt;
    s_hblank_cnt = (u8)(s_hblank_cnt + 1u);

    if (line >= 152u) return;

    /* Per-scanline palette swap: road perspective narrowing.
     * mid band (HW_PAL_SCR1[18] = pal4.c2) and outer band ([22] = pal5.c2)
     * are written every scanline, switching grass <-> road color at thresholds. */
    if (s_pal_active) {
        HW_PAL_SCR1[18u] = (line < s_pal_mid_thr) ? s_pal_grass_c2 : s_pal_road_c2;
        HW_PAL_SCR1[22u] = (line < s_pal_out_thr) ? s_pal_grass_c2 : s_pal_road_c2;
    }

    if (s_scroll_x) {
        if (s_scroll_plane == GFX_SCR1)
            HW_SCR1_OFS_X = s_scroll_x[line];
        else
            HW_SCR2_OFS_X = s_scroll_x[line];
    }
    if (s_scroll_y) {
        if (s_scroll_plane == GFX_SCR1)
            HW_SCR1_OFS_Y = s_scroll_y[line];
        else
            HW_SCR2_OFS_Y = s_scroll_y[line];
    }
}

/* ---- Public API ---- */

/* Called from VBlank ISR (ngpc_sys.c) every frame.
 * Resets the per-line counter so Timer0 ISR starts at line 0 each frame. */
void ngpc_raster_vsync(void)
{
    s_hblank_cnt = 0u;
    ngpc_dbg_isr_fires = 0u;
}

void ngpc_raster_init(void)
{
    /* Clear state. */
    s_scroll_x = 0;
    s_scroll_y = 0;
    s_scroll_plane = GFX_SCR1;
    s_cb_count = 0;
    s_parallax_active = 0;
    s_hblank_cnt = 0u;
    s_was_vblank = 1u;   /* treat first ISR fire as post-VBlank to force sync */
    s_pal_active = 0u;

    /* Install Timer0 ISR address. */
    HW_INT_TIM0 = (IntHandler *)isr_hblank;

    /*
     * Enable Timer0 interrupt via BIOS system call VECT_INTLVSET.
     *
     * Direct register writes to the vector table level byte and to any
     * INTET register do NOT enable the interrupt on NGPC -- the BIOS owns
     * the interrupt level hardware.  The official way is:
     *   RW3 = BIOS_INTLVSET (4)
     *   RB3 = desired priority level (4 = same as VBlank)
     *   RC3 = interrupt number (2 = 8-bit Timer 0, per SysCall.txt)
     *   SWI 1
     * Source: SysCall.txt "VECT_INTLVSET" + SysPro.txt interrupt table.
     */
    __asm("ldb rb3, 4");   /* priority level 4 (VBlank-level, fires with EI 0) */
    __asm("ldb rc3, 2");   /* interrupt number 2 = 8-bit Timer 0               */
    __asm("ldb rw3, 4");   /* BIOS_INTLVSET = 4                                */
    __asm("swi 1");

    /*
     * Configure Timer 0 for HBlank-rate interrupt.
     *
     * T01MOD bits 1:0 = Timer0 clock source:
     *   01 = T0IN (external pin, connected to K1GE HBlank on NGPC ASIC)
     *   10 = internal fφ/4 (~1.5 MHz -- stalls CPU)
     *   11 = internal fφ/256 (~24 kHz -- imprecise)
     *
     * T0IN fires once per HBlank (9120 Hz at 60 fps × 152 lines).
     * TREG0=1 → overflow every 1 T0IN tick → ISR once per scanline.
     */
    HW_TRUN    &= (u8)~0x01;           /* stop Timer0 before reconfiguring       */
    HW_T01MOD  &= (u8)~0xC3;          /* clear bits 7:6 (Timer1) and 1:0 clk    */
    HW_T01MOD  |= 0x01;                /* bits 1:0 = 01 = T0IN (HBlank pin)      */
    HW_TREG0    = 0x01;                /* reload = 1 → fire every HBlank tick    */
    HW_TRUN    |= 0x01;                /* start Timer0                            */
}

void ngpc_raster_disable(void)
{
    HW_TRUN    &= (u8)~0x01;  /* stop Timer0 */
    /* Do not clear HW_INT_TIM0 to zero: a pending interrupt firing after
     * the timer is stopped would jump to address 0 and crash.  The old
     * handler is harmless if it runs one extra time. */

    s_scroll_x = 0;
    s_scroll_y = 0;
    s_cb_count = 0;
    s_parallax_active = 0;
    s_pal_active = 0u;
}

void ngpc_raster_set_scroll_table(u8 plane, const u8 *table_x, const u8 *table_y)
{
    s_scroll_plane = plane;
    s_scroll_x = table_x;
    s_scroll_y = table_y;
    s_parallax_active = 0;
}

void ngpc_raster_clear_scroll(void)
{
    s_scroll_x = 0;
    s_scroll_y = 0;
    s_parallax_active = 0;
}

u8 ngpc_raster_set_callback(u8 line, RasterCallback cb)
{
    if (s_cb_count >= RASTER_MAX_CB) return 0xFF;

    s_callbacks[s_cb_count].line = line;
    s_callbacks[s_cb_count].cb   = cb;
    s_cb_count++;

    return s_cb_count - 1;
}

void ngpc_raster_clear_callbacks(void)
{
    s_cb_count = 0;
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
}

/* ---- Road palette swap (per-scanline perspective narrowing) ---- */

/*
 * ngpc_raster_set_road_pal -- enable per-scanline palette swap for road bands.
 *
 * grass_c2 : SCR1 palette c2 color used for grass (e.g. RGB(1,8,1))
 * road_c2  : SCR1 palette c2 color used for road  (e.g. RGB(5,5,5))
 * mid_thr  : scanline where mid  band switches grass->road (e.g. 106)
 * out_thr  : scanline where outer band switches grass->road (e.g. 128)
 *
 * The ISR writes HW_PAL_SCR1[18] (pal4.c2) and HW_PAL_SCR1[22] (pal5.c2)
 * every scanline.  Above mid_thr both bands are grass-colored (narrow road).
 * Between mid_thr and out_thr the mid band turns road-gray (medium width).
 * Below out_thr both bands are road-gray (full width near camera).
 */
void ngpc_raster_set_road_pal(u16 grass_c2, u16 road_c2, u8 mid_thr, u8 out_thr)
{
    s_pal_grass_c2 = grass_c2;
    s_pal_road_c2  = road_c2;
    s_pal_mid_thr  = mid_thr;
    s_pal_out_thr  = out_thr;
    /* Pre-initialize palette RAM before first ISR fires. */
    HW_PAL_SCR1[18u] = grass_c2;
    HW_PAL_SCR1[22u] = grass_c2;
    s_pal_active   = 1u;
}
