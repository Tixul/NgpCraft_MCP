/*
 * main.c - ngpc_racer module test homebrew
 *
 * Tests pseudo-3D perspective scaling with 3 colored cars at different depths.
 * No ROM assets -- all graphics are hand-coded 2bpp tiles (racer_tiles.c).
 * No sound.
 *
 * Controls:
 *   A       -- accelerate (held)
 *   B       -- brake (held)
 *   LEFT    -- curve left (held)
 *   RIGHT   -- curve right (held)
 *   OPTION  -- reset (camera to 0, speed to 0)
 *
 * Screen layout:
 *   Row  0: "SP:xx  VP:xxx"   (speed, vanishing point X)
 *   Row  1: "CA:xxxxx"        (camera Z, low 16 bits)
 *   Rows 2..19: road BG (animates with forward scroll)
 *   Sprites: 3 colored cars (red/green/blue) at varying depths
 *
 * ngpc_racer_oam_flush() is hooked into the VBlank ISR in ngpc_sys.c.
 */

#include "ngpc_hw.h"
#include "carthdr.h"
#include "ngpc_sys.h"
#include "ngpc_gfx.h"
#include "ngpc_text.h"
#include "ngpc_input.h"
#include "ngpc_timing.h"
#include "ngpc_sprite.h"

#include "../../optional/ngpc_racer/ngpc_racer.h"
#include "../GraphX/racer_tiles.h"
#include "fx/ngpc_raster.h"

/* =========================================================================
 * CONSTANTS
 * ========================================================================= */

/* Car depth offsets from camera (world units ahead of camera).
 * Range = RACER_RANGE_FAR = 6400 / ZOOM_STEP 25 = 256 entries.
 * Zoom index = (range - dist) / 25.  s_persp_x[] calibrated for 0..255.
 *   dist  250 -> closeness 6150 -> zoom 246 -> near tile (index 220..255)
 *   dist 2000 -> closeness 4400 -> zoom 176 -> med  tile (index 160..219)
 *   dist 4000 -> closeness 2400 -> zoom  96 -> dot  tile (index  70..99) */
#define CAR0_DIST    250u
#define CAR1_DIST   2000u
#define CAR2_DIST   4000u

#define SPEED_MIN    0u
#define SPEED_MAX   60u
#define SPEED_STEP   3u

/* Car palette indices (sprite palette table) */
#define PAL_RED    0u
#define PAL_GREEN  1u
#define PAL_BLUE   2u

/* SCR1 palettes */
#define PAL_ROAD    3u   /* gris route (inner, always road-colored) */
#define PAL_GRASS   1u   /* vert herbe (côtés) */
#define PAL_ROAD_B  4u   /* mid  band (cols 6-7, 12-13) — swapped per-scanline by raster ISR */
#define PAL_ROAD_A  5u   /* outer band (cols 4-5, 14-15) — swapped per-scanline by raster ISR */

/* SPR palette for road edge markers (pylons) */
#define PAL_MARKER  6u   /* moved to pal 6 (pals 4+5 now used for road bands) */

/* Scanline thresholds for perspective road narrowing (palette swap technique).
 * Below mid_thr  : mid+outer bands = grass color -> road looks narrow (horizon)
 * mid_thr..out_thr: mid band = road, outer still grass -> medium width
 * Below out_thr  : both bands = road color -> full width (near camera)
 * Values derived from ngpc_racer persp_x table:
 *   persp_x[184]=16 -> road half-width 16 px at zoom=184 -> screen y~106
 *   persp_x[226]=32 -> road half-width 32 px at zoom=226 -> screen y~128 */
#define ROAD_THR_MID  106u
#define ROAD_THR_OUT  128u

/* Tile indices in VRAM */
#define TILE_ROAD    (RACER_TILE_BASE + 4u)
#define TILE_STRIPE  (RACER_TILE_BASE + 5u)

/* Largeur tilemap : 4 cols herbe | 12 cols route | 4 cols herbe = 20 cols */
#define GRASS_COLS  4u
#define ROAD_COLS  12u

/* (pas de constantes supplémentaires nécessaires -- les marqueurs utilisent
 * directement la table raster pour la synchronisation avec la route) */

/* =========================================================================
 * ASSET DATA (RAM -- near pointers for cc900 near default)
 *
 * All RacerPart arrays are non-const so cc900 puts them in RAM.
 * RacerAsset.zoom_table uses plain pointer (no NGP_FAR) matching
 * the near element type in the test version of ngpc_racer.h.
 * ========================================================================= */

static RacerPart s_part_dot[2];
static RacerPart s_part_small[2];
static RacerPart s_part_med[2];
static RacerPart s_part_near[2];

/* Zoom table: 256 entries (6400 / 25 = 256). Index 0 = farthest. */
static const RacerPart *s_zoom_table[256];

/* Depth offsets for the 3 cars (fixed, file-scope to avoid C89 local-init). */
static u32 s_car_offsets[3];

/* Asset shared by all cars (same tile LODs, different palette per car). */
static RacerAsset s_car_asset;

/* The 3 renderable cars. */
static RacerObj s_cars[3];

/* =========================================================================
 * GAME STATE
 * ========================================================================= */

static u8  s_speed;
static s8  s_curve;

/* Per-scanline OFS_Y : accumulateur indépendant par ligne (wrap u8 = seamless). */
static u8  s_raster_y[152];

/* Per-scanline OFS_X : décalage latéral quadratique pour les virages.
 * Horizon=0 (droit), bas écran=amplitude max. Rebuilt chaque frame depuis vp_x. */
static u8  s_raster_x[152];

/* =========================================================================
 * PART + ZOOM TABLE INIT
 * ========================================================================= */

static void init_parts(void)
{
    /* dot: tile RACER_TILE_BASE+0 (2x2 px, visible > 1000 units away) */
    s_part_dot[0].tile_index = (u16)(RACER_TILE_BASE + 0u);
    s_part_dot[0].x_off = 0; s_part_dot[0].y_off = 0; s_part_dot[0].flags = 0;
    s_part_dot[1].tile_index = 0;
    s_part_dot[1].x_off = 0; s_part_dot[1].y_off = 0; s_part_dot[1].flags = 0;

    /* small: tile RACER_TILE_BASE+1 (4x4 px, 500..1000 units) */
    s_part_small[0].tile_index = (u16)(RACER_TILE_BASE + 1u);
    s_part_small[0].x_off = 0; s_part_small[0].y_off = 0; s_part_small[0].flags = 0;
    s_part_small[1].tile_index = 0;
    s_part_small[1].x_off = 0; s_part_small[1].y_off = 0; s_part_small[1].flags = 0;

    /* med: tile RACER_TILE_BASE+2 (6x6 px, 250..500 units) */
    s_part_med[0].tile_index = (u16)(RACER_TILE_BASE + 2u);
    s_part_med[0].x_off = 0; s_part_med[0].y_off = 0; s_part_med[0].flags = 0;
    s_part_med[1].tile_index = 0;
    s_part_med[1].x_off = 0; s_part_med[1].y_off = 0; s_part_med[1].flags = 0;

    /* near: tile RACER_TILE_BASE+3 (8x8 solid, 0..250 units) */
    s_part_near[0].tile_index = (u16)(RACER_TILE_BASE + 3u);
    s_part_near[0].x_off = 0; s_part_near[0].y_off = 0; s_part_near[0].flags = 0;
    s_part_near[1].tile_index = 0;
    s_part_near[1].x_off = 0; s_part_near[1].y_off = 0; s_part_near[1].flags = 0;
}

static void init_zoom_table(void)
{
    u16 i;  /* u16: iterates to 256 (u8 would wrap at 255 -> infinite loop) */

    /* 0..69: invisible (dist > ~4600 world units, persp_x table all-zero here) */
    for (i = 0u; i < 70u; i++) s_zoom_table[i] = 0;

    /* 70..99: dot  (dist ~3900..4600) */
    for (i = 70u; i < 100u; i++) s_zoom_table[i] = s_part_dot;

    /* 100..159: small (dist ~2400..3900) */
    for (i = 100u; i < 160u; i++) s_zoom_table[i] = s_part_small;

    /* 160..219: med  (dist ~900..2400) */
    for (i = 160u; i < 220u; i++) s_zoom_table[i] = s_part_med;

    /* 220..255: near (dist 0..900) */
    for (i = 220u; i < 256u; i++) s_zoom_table[i] = s_part_near;
}

/* =========================================================================
 * SCENE SETUP
 * ========================================================================= */

static void setup_palettes(void)
{
    /* SCR1 pal 0: text (sysfont) */
    ngpc_gfx_set_palette(GFX_SCR1, 0u,
        RGB(0, 0, 0), RGB(15, 15, 15), RGB(8, 8, 8), RGB(4, 4, 4));

    /* SCR2 pal 0: text (sysfont) -- same colors, SCR2 is the HUD layer */
    ngpc_gfx_set_palette(GFX_SCR2, 0u,
        RGB(0, 0, 0), RGB(15, 15, 15), RGB(8, 8, 8), RGB(4, 4, 4));

    /* SCR1 pal 1: herbe (côtés) -- c2=vert moyen, c3=vert clair (bande) */
    ngpc_gfx_set_palette(GFX_SCR1, PAL_GRASS,
        RGB(0, 0, 0),   /* c0: unused */
        RGB(0, 4, 0),   /* c1: vert sombre (unused dans tiles actuels) */
        RGB(1, 8, 1),   /* c2: herbe verte principale */
        RGB(3, 12, 2)   /* c3: herbe claire (bande de défilement) */
    );

    /* SCR1 pal 3: route -- c2=gris asphalte, c3=marquage (moins blanc) */
    ngpc_gfx_set_palette(GFX_SCR1, PAL_ROAD,
        RGB(0, 0, 5),  /* c0: unused */
        RGB(3, 3, 3),  /* c1: unused */
        RGB(5, 5, 5),  /* c2: asphalte gris */
        RGB(9, 9, 9)   /* c3: marquage gris clair (moins agressif que 12) */
    );

    /* SPR pal 0: car 0 = red */
    ngpc_gfx_set_palette(GFX_SPR, PAL_RED,
        RGB(0, 0, 0), RGB(15, 2, 2), RGB(8, 1, 1), RGB(15, 15, 15));

    /* SPR pal 1: car 1 = green */
    ngpc_gfx_set_palette(GFX_SPR, PAL_GREEN,
        RGB(0, 0, 0), RGB(2, 14, 2), RGB(1, 7, 1), RGB(15, 15, 15));

    /* SPR pal 2: car 2 = blue */
    ngpc_gfx_set_palette(GFX_SPR, PAL_BLUE,
        RGB(0, 0, 0), RGB(2, 4, 15), RGB(1, 2, 8), RGB(15, 15, 15));

    /* SCR1 pal 4 (PAL_ROAD_B): mid road band.
     * c2 is swapped per-scanline by raster ISR between grass and road color.
     * Initial value = grass (ISR takes over immediately after raster_init). */
    ngpc_gfx_set_palette(GFX_SCR1, PAL_ROAD_B,
        RGB(0, 0, 0),   /* c0: unused */
        RGB(0, 4, 0),   /* c1: unused */
        RGB(1, 8, 1),   /* c2: grass (overwritten per-scanline by ISR) */
        RGB(5, 5, 5)    /* c3: unused */
    );

    /* SCR1 pal 5 (PAL_ROAD_A): outer road band.
     * Same swap as PAL_ROAD_B but at a later scanline threshold. */
    ngpc_gfx_set_palette(GFX_SCR1, PAL_ROAD_A,
        RGB(0, 0, 0),
        RGB(0, 4, 0),
        RGB(1, 8, 1),   /* c2: grass initially */
        RGB(5, 5, 5)
    );

    /* SPR pal 6: pylon markers -- c1=dark outline, c2=mid grey, c3=white */
    ngpc_gfx_set_palette(GFX_SPR, PAL_MARKER,
        RGB(0, 0, 0), RGB(3, 3, 3), RGB(9, 9, 9), RGB(15, 15, 15));
}

static void draw_road_bg(void)
{
    u8   r;
    u16  road_tile, grass_tile;

    /* Layout horizontal (20 cols visibles, 32 cols tilemap) :
     *
     *   cols  0-3  : herbe gauche    (PAL_GRASS)
     *   cols  4-5  : bande extérieure gauche (PAL_ROAD_A=5, TILE_ROAD)
     *   cols  6-7  : bande médiane   gauche  (PAL_ROAD_B=4, TILE_ROAD)
     *   cols  8-11 : route intérieure        (PAL_ROAD=3,   TILE_ROAD/STRIPE)
     *   cols 12-13 : bande médiane   droite  (PAL_ROAD_B=4, TILE_ROAD)
     *   cols 14-15 : bande extérieure droite (PAL_ROAD_A=5, TILE_ROAD)
     *   cols 16-19 : herbe droite    (PAL_GRASS)
     *
     * Le raster ISR change c2 de PAL_ROAD_B et PAL_ROAD_A chaque scanline :
     *   y < ROAD_THR_MID (106) : outer+mid = herbe  → route étroite à l'horizon
     *   106 <= y < ROAD_THR_OUT (128) : mid = route, outer = herbe → largeur moyenne
     *   y >= 128 : outer+mid = route → pleine largeur près caméra
     *
     * Tiles extérieures/médianes : TILE_ROAD seulement (c2 solid = effet palette).
     * Bande de marquage (TILE_STRIPE) réservée à la route intérieure. */
    for (r = 0u; r < 32u; r++) {
        road_tile  = ((r & 3u) == 0u) ? (u16)TILE_STRIPE : (u16)TILE_ROAD;
        grass_tile = ((r & 1u) == 0u) ? (u16)TILE_ROAD   : (u16)TILE_STRIPE;

        /* Herbe gauche */
        ngpc_gfx_fill_rect(GFX_SCR1, 0u,  r, 4u, 1u, grass_tile, PAL_GRASS);
        /* Bande extérieure gauche (ISR swap pal) */
        ngpc_gfx_fill_rect(GFX_SCR1, 4u,  r, 2u, 1u, (u16)TILE_ROAD, PAL_ROAD_A);
        /* Bande médiane gauche (ISR swap pal) */
        ngpc_gfx_fill_rect(GFX_SCR1, 6u,  r, 2u, 1u, (u16)TILE_ROAD, PAL_ROAD_B);
        /* Route intérieure (toujours gris route) */
        ngpc_gfx_fill_rect(GFX_SCR1, 8u,  r, 4u, 1u, road_tile,  PAL_ROAD);
        /* Bande médiane droite */
        ngpc_gfx_fill_rect(GFX_SCR1, 12u, r, 2u, 1u, (u16)TILE_ROAD, PAL_ROAD_B);
        /* Bande extérieure droite */
        ngpc_gfx_fill_rect(GFX_SCR1, 14u, r, 2u, 1u, (u16)TILE_ROAD, PAL_ROAD_A);
        /* Herbe droite visible */
        ngpc_gfx_fill_rect(GFX_SCR1, 16u, r, 4u, 1u, grass_tile, PAL_GRASS);
        /* Colonnes "fantômes" 20-31 : remplies d'herbe pour éviter le trou noir
         * quand OFS_X est élevé (virage) et que le tilemap wrappe.
         * Sans ça : virage droite max (OFS_X~209) montre bg-color (bleu) sur
         * la gauche au lieu d'herbe → ressemble à un glitch, pas à une courbe. */
        ngpc_gfx_fill_rect(GFX_SCR1, 20u, r, 12u, 1u, grass_tile, PAL_GRASS);
    }

    ngpc_gfx_clear(GFX_SCR2);
}

static void scene_init(void)
{
    ngpc_gfx_scroll(GFX_SCR1, 0u, 0u);
    ngpc_gfx_scroll(GFX_SCR2, 0u, 0u);
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);

    /* Background color (shows through transparent tiles = sky). */
    ngpc_gfx_set_bg_color(RGB(0, 0, 5));

    /* Load BIOS sysfont (tiles 32..127) for debug text. */
    ngpc_load_sysfont();

    /* Load racer car LOD tiles at VRAM slot 128.
     * 6 tiles x 8 words = 48 u16 words. */
    ngpc_gfx_load_tiles_at(RACER_TILES, RACER_TILES_COUNT, RACER_TILE_BASE);

    setup_palettes();
    draw_road_bg();

    /* Clear hardware sprite OAM (BIOS logo may leave sprites active). */
    ngpc_sprite_hide_all();

    /* Priority: sprites > SCR2 (HUD text) > SCR1 (road). bit7=1 = SCR2 front. */
    HW_SCR_PRIO |= 0x80u;

    /* Init racer module state. */
    ngpc_racer_init();

    /* Build part lists and zoom table. */
    init_parts();
    init_zoom_table();

    s_car_asset.zoom_table = s_zoom_table;
    s_car_asset.range      = RACER_RANGE_FAR;  /* 6400 -- aligns with s_persp_x[256] */

    /* Car depth offsets (file-scope array, filled here). */
    s_car_offsets[0] = (u32)CAR0_DIST;
    s_car_offsets[1] = (u32)CAR1_DIST;
    s_car_offsets[2] = (u32)CAR2_DIST;

    /* Car 0: center of road, red. */
    s_cars[0].z         = 0u;
    s_cars[0].world_x   = 0;
    s_cars[0].asset     = &s_car_asset;
    s_cars[0].pal       = PAL_RED;
    s_cars[0].spr_flags = SPR_FRONT;
    s_cars[0].active    = 1u;
    s_cars[0].next      = 0;

    /* Car 1: left lane, green. */
    s_cars[1].z         = 0u;
    s_cars[1].world_x   = -150;
    s_cars[1].asset     = &s_car_asset;
    s_cars[1].pal       = PAL_GREEN;
    s_cars[1].spr_flags = SPR_FRONT;
    s_cars[1].active    = 1u;
    s_cars[1].next      = 0;

    /* Car 2: right lane, blue. */
    s_cars[2].z         = 0u;
    s_cars[2].world_x   = 150;
    s_cars[2].asset     = &s_car_asset;
    s_cars[2].pal       = PAL_BLUE;
    s_cars[2].spr_flags = SPR_FRONT;
    s_cars[2].active    = 1u;
    s_cars[2].next      = 0;

    s_speed = 16u;  /* vitesse initiale visible -- A pour accélérer, B pour freiner */
    s_curve = 0;

    /* Raster: per-scanline OFS_Y for perspective road compression.
     * Table is rebuilt every frame in build_raster_y().
     * The pointer is set once; ISR always reads from s_raster_y[]. */
    ngpc_raster_init();
    ngpc_raster_set_scroll_table(GFX_SCR1, s_raster_x, s_raster_y);

    /* Enable per-scanline road narrowing (palette swap in HBlank ISR).
     * grass color = RGB(1,8,1) = SCR1 pal1 c2 (same as PAL_GRASS c2).
     * road  color = RGB(5,5,5) = SCR1 pal3 c2 (same as PAL_ROAD c2). */
    ngpc_raster_set_road_pal(RGB(1u, 8u, 1u), RGB(5u, 5u, 5u),
                              ROAD_THR_MID, ROAD_THR_OUT);
}

/* =========================================================================
 * PER-FRAME UPDATE
 * ========================================================================= */

static void update_controls(void)
{
    /* A = gaz (tenu), B = frein (tenu) */
    if (ngpc_pad_held & PAD_A) {
        if (s_speed < (u8)SPEED_MAX) s_speed = (u8)(s_speed + (u8)SPEED_STEP);
    } else if (ngpc_pad_held & PAD_B) {
        if (s_speed >= (u8)SPEED_STEP) {
            s_speed = (u8)(s_speed - (u8)SPEED_STEP);
        } else {
            s_speed = (u8)SPEED_MIN;
        }
    }

    /* LEFT/RIGHT = direction (courbe) */
    s_curve = 0;
    if (ngpc_pad_held & PAD_LEFT)  s_curve = (s8)-12;
    if (ngpc_pad_held & PAD_RIGHT) s_curve = (s8) 12;

    /* OPTION = reset total */
    if (ngpc_pad_pressed & PAD_OPTION) {
        u8 ry;
        ngpc_racer_init();
        s_speed = 0u;
        s_curve = 0;
        for (ry = 0u; ry < 152u; ry++) { s_raster_y[ry] = 0u; s_raster_x[ry] = 0u; }
    }
}

static void update_cars(void)
{
    u32 cam;
    u8  i;

    cam = ngpc_racer_camera_get();

    /* Rebuild Z-sorted list each frame.
     * Cars maintain constant depth ahead of camera. */
    ngpc_racer_obj_clear();
    for (i = 0u; i < 3u; i++) {
        s_cars[i].z    = cam + s_car_offsets[i];
        s_cars[i].next = 0;
        ngpc_racer_obj_insert(&s_cars[i]);
    }
}

static void update_debug_text(void)
{
    /* Written to SCR2 (HUD layer, fixed, in front of scrolling SCR1 road). */

    /* Row 0: speed and vanishing point X. */
    ngpc_text_print(GFX_SCR2, 0u, 0u, 0u, "SP:");
    ngpc_text_print_dec(GFX_SCR2, 0u, 3u, 0u, (u16)s_speed, 2u);
    ngpc_text_print(GFX_SCR2, 0u, 6u, 0u, "VP:");
    ngpc_text_print_dec(GFX_SCR2, 0u, 9u, 0u, (u16)ngpc_racer_vp_x(), 3u);

    /* Row 1: camera Z (low 16 bits). */
    ngpc_text_print(GFX_SCR2, 0u, 0u, 1u, "CA:");
    ngpc_text_print_dec(GFX_SCR2, 0u, 3u, 1u,
                        (u16)(ngpc_racer_camera_get() & 0xFFFFu), 5u);

    /* Row 2: ISR fire count last frame.  152=correct, 0=never, 1=once/frame.
     * Row 3: raster table horizon vs near (XH should be non-zero on turn). */
    ngpc_text_print(GFX_SCR2, 0u, 0u, 2u, "ISR:");
    ngpc_text_print_dec(GFX_SCR2, 0u, 4u, 2u, (u16)ngpc_dbg_isr_fires, 3u);
    ngpc_text_print(GFX_SCR2, 0u, 0u, 3u, "XH:");
    ngpc_text_print_dec(GFX_SCR2, 0u, 3u, 3u, (u16)s_raster_x[16], 3u);
    ngpc_text_print(GFX_SCR2, 0u, 7u, 3u, "XN:");
    ngpc_text_print_dec(GFX_SCR2, 0u, 10u, 3u, (u16)s_raster_x[143], 3u);
}

/* =========================================================================
 * RASTER PERSPECTIVE TABLE
 * ========================================================================= */

/*
 * build_raster_x -- OFS_X par scanline, technique OutRun.
 *
 * Formule QUADRATIQUE (pas linéaire) :
 *   dy  = 151 - y          (0 en bas, 135 à l'horizon)
 *   dy4 = dy >> 2           (0..33, u8, réduit pour tenir en u16)
 *   shift = min((dy4^2 * mag) >> 7, 100)   (cap 100px évite wrap)
 *
 * La courbe quadratique est ESSENTIELLE : une formule linéaire produit un
 * décalage uniforme sur toute la hauteur = la route "glisse en bloc" sans
 * paraître courber. Avec la formule quadratique (>>7, cap 100) :
 *   y=151 (bas)    : shift = 0   (route droite sous le joueur)
 *   y=147 (bas-1)  : shift = 1   (visible dès la base)
 *   y=135 (bas-16) : shift = 6   (début courbure)
 *   y=111 (bas-40) : shift = 37  (courbure nette)
 *   y=83  (milieu) : shift = 100 (plateau, horizon plein virage)
 *
 * Arithmétique pur u16 (pas de u32) : dy4*dy4 ≤ 961, *mag ≤ 46128 < 65535.
 *
 * Virage DROITE : OFS_X = 256-shift (u8) → horizon part à droite. ✓
 * Virage GAUCHE : OFS_X = shift (positif) → horizon part à gauche. ✓
 */
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

    /* Ciel/HUD : pas de décalage horizontal. */
    for (y = 0u; y < 16u; y++) s_raster_x[y] = 0u;

    /* Zone route + near-clip : courbe quadratique de y=16 (horizon, max)
     * jusqu'à y=151 (bas écran, shift=0).
     * dy4 = (151-y)>>2 : 0 en bas, 33 à l'horizon. */
    for (y = 16u; y < 152u; y++) {
        dy4   = (u8)((u8)(151u - y) >> 2u);  /* 0..33, u8 */
        sq    = (u16)dy4 * (u16)dy4;          /* 0..1089, u16 */
        prod  = sq * (u16)mag;                /* 0..52272, u16 */
        shift = (u8)(prod >> 7u);             /* 0..408 px avant cap */
        if (shift > 100u) shift = 100u;       /* cap : évite wrap tilemap */
        s_raster_x[y] = right_turn ? (u8)(0u - shift) : shift;
    }
}

/*
 * build_raster_y -- mise à jour des accumulateurs OFS_Y par scanline.
 *
 * PRINCIPE : chaque scanline y accumule indépendamment son propre OFS_Y.
 *   s_raster_y[y] += speed * sq7(y) / 128   (u8 : wrap seamless = un tour tilemap)
 *
 * Pourquoi par accumulation plutôt que recalcul ?
 *   Recalcul (OFS_Y = base*sq7/128) : quand `base` wrap (u8 : tous les 256/speed
 *   frames), TOUTES les scanlines sautent à 0 simultanément → road "repart en arrière".
 *   Accumulation : chaque y wrap à son propre rythme (les rows du bas wrappent plus
 *   vite que ceux du haut). Pas de saut global → scroll parfaitement continu.
 *
 * Facteur de profondeur quadratique : sq7 = dy^2/128  (dy = y-16, 0..127)
 *   -> row horizon (dy=0)  : sq7=0,   OFS_Y statique (loin = immobile)
 *   -> row bottom  (dy=127): sq7=126, OFS_Y avance à ~speed px/frame (près = vite)
 *
 * Arithmétique : speed(u8)*sq7(u8) <= 60*126 = 7560, tient en u16. >> 7 donne u8.
 */
static void build_raster_y(void)
{
    u8  y, dy, sq7, inc;
    u16 sq;

    /* Ciel / HUD : forcés à 0, pas d'accumulation. */
    for (y = 0u; y < 16u; y++)
        s_raster_y[y] = 0u;

    /* Zone route : accumulation quadratique indépendante par scanline. */
    for (y = 16u; y < 144u; y++) {
        dy  = (u8)(y - 16u);              /* 0..127 */
        sq  = (u16)dy * (u16)dy;          /* 0..16129, u16 */
        sq7 = (u8)(sq >> 7);              /* sq/128, 0..126 */
        inc = (u8)((u16)s_speed * (u16)sq7 >> 7); /* vitesse pondérée, u8 */
        s_raster_y[y] = (u8)(s_raster_y[y] + inc);
    }

    /* Sous near-clip : taux plein (évite artefact bord bas écran). */
    for (y = 144u; y < 152u; y++)
        s_raster_y[y] = (u8)(s_raster_y[y] + s_speed);
}

/* =========================================================================
 * ROAD EDGE MARKERS
 * ========================================================================= */

/*
 * draw_road_edges -- marqueurs blancs sur les bords de route.
 *
 * Approche : scanner la table raster s_raster_y[] (déjà construite ce frame)
 * pour trouver chaque transition "route → bande" (OFS_Y franchit un multiple
 * impair de 8 = entrée dans une tile TILE_STRIPE). Placer un point à cet Y.
 *
 * Avantage : synchronisation parfaite avec le scroll route -- impossible de
 * voir les points partir dans la mauvaise direction. Pas de world-Z math.
 *
 * Zoom inverse : ngpc_racer_screen_y(zoom) = 8 + zoom*17/32 = y
 *   => zoom = (y - 8) * 32 / 17
 *
 * Sprites : max ~16 bandes visibles x 2 + 3 voitures = 35 < 64. OK.
 */
static void draw_road_edges(void)
{
    u8 y, zoom, lx, rx;
    u8 prev_row, cur_row;

    /* prev_row = 0 si on est sur une tile road (OFS_Y bits 3..3 = 0)
     *          = 1 si on est sur une tile stripe (OFS_Y bits 3..3 = 1)  */
    prev_row = (s_raster_y[16] >> 3) & 1u;

    for (y = 17u; y < 144u; y++) {
        cur_row = (s_raster_y[y] >> 3) & 1u;

        /* Transition road -> stripe : on entre dans une bande blanche. */
        if (cur_row == 1u && prev_row == 0u) {
            /* Zoom inverse de ngpc_racer_screen_y : zoom = (y-8)*32/17 */
            zoom = (u8)((u16)(y - 8u) * 32u / 17u);

            /* Sauter si persp_x == 0 (pas d'écartement visible). */
            if (zoom >= 70u) {
                u8 mtile;
                /* LOD: pick marker size matching distance (same brackets as cars). */
                if      (zoom >= 220u) mtile = (u8)(TILE_MARKER_BASE + 4u); /* full */
                else if (zoom >= 160u) mtile = (u8)(TILE_MARKER_BASE + 3u); /* large */
                else if (zoom >= 100u) mtile = (u8)(TILE_MARKER_BASE + 2u); /* med */
                else if (zoom >= 80u)  mtile = (u8)(TILE_MARKER_BASE + 1u); /* small */
                else                   mtile = (u8)(TILE_MARKER_BASE + 0u); /* tiny */
                lx = ngpc_racer_road_left(zoom);
                rx = ngpc_racer_road_right(zoom);
                ngpc_racer_oam_put(lx, y, (u16)mtile, (u8)SPR_FRONT, PAL_MARKER);
                ngpc_racer_oam_put(rx, y, (u16)mtile, (u8)SPR_FRONT, PAL_MARKER);
            }
        }
        prev_row = cur_row;
    }
}

/* =========================================================================
 * MAIN
 * ========================================================================= */

void main(void)
{
    ngpc_init();
    scene_init();

    while (1) {
        ngpc_vsync();
        /* Flush shadow OAM while still in VBlank window. */
        ngpc_racer_oam_flush();
        ngpc_input_update();

        update_controls();

        /* Racer frame pipeline (must happen in this order). */
        ngpc_racer_oam_begin();
        ngpc_racer_camera_advance((u32)s_speed);
        ngpc_racer_scroll_update(s_speed);
        HW_SCR2_OFS_Y = 0u;   /* SCR2 = HUD layer, must stay fixed */
        ngpc_racer_curve_update(s_curve);
        update_cars();
        ngpc_racer_render();

        /* Road edge markers: white dots converging to vanishing point. */
        draw_road_edges();

        build_raster_x();   /* OFS_X par scanline : virage quadratique */
        build_raster_y();   /* OFS_Y par scanline : avance perspective */

        /* Debug text (direct tile writes, no VRAM queue needed). */
        update_debug_text();

        /* ngpc_racer_oam_flush() is called at top of next iteration, just
         * after vsync returns (still within the VBlank window). */
    }
}
