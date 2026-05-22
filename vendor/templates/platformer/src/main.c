/*
 * main.c - Platformer perf
 * Map SCR1 (mapstream), ciel SCR2 (statique).
 * Joueur avec physique NgpcPlatform, collision tilemap, animation metasprite.
 * 5 ennemis de patrouille, 17 props (platformes, collectibles, tremplins, picks).
 *
 * Sprite budget: 64 HW sprites
 *   0-3   : joueur (4 sprites)
 *   4-23  : 5 ennemis x 4 sprites
 *   24-63 : pool props (40 slots max)
 */

#include "ngpc_hw.h"
#include "carthdr.h"
#include "ngpc_sys.h"
#include "ngpc_gfx.h"
#include "ngpc_sprite.h"
#include "ngpc_input.h"
#include "ngpc_metasprite.h"
#include "ngpc_tilemap_blit.h"

#include "sky_map.h"
#include "level_map.h"
#include "level_k2ge.h"
#include "joueur_mspr.h"
#include "ennemie_mspr.h"
#include "platforme_mspr.h"
#include "collectible_mspr.h"
#include "tremplin_mspr.h"
#include "pick_mspr.h"

#include "ngpc_mapstream/ngpc_mapstream.h"
#include "ngpc_platform/ngpc_platform.h"
#include "ngpc_camera/ngpc_camera.h"
#include "ngpc_fixed/ngpc_fixed.h"
#include "ngpc_text.h"
#include "fx/ngpc_debug.h"

/* ---- Tile bases ---- */
#define SKY_TILE_BASE    128u
#define LEVEL_TILE_BASE  157u

/* ---- Level dimensions ---- */
#define LEVEL_W_TILES  110
#define LEVEL_H_TILES   50
#define LEVEL_W_PX     880
#define LEVEL_H_PX     400

/* ---- Player hitbox (origin = sprite top-left, sprite 16x16) ---- */
#define PLAYER_HB_LEFT    4
#define PLAYER_HB_RIGHT  12
#define PLAYER_HB_TOP     0
#define PLAYER_HB_BOT    16

/* ---- Player movement ---- */
#define PLAYER_WALK_SPD   INT_TO_FX(2)
#define PLAYER_SPR_START  0u
#define PLAYER_HP_MAX     3
#define PLAYER_INVUL_FRAMES  60

/* ---- Player animations ---- */
#define ANIM_IDLE_START  0u
#define ANIM_IDLE_COUNT  2u
#define ANIM_JUMP_START  2u
#define ANIM_JUMP_COUNT  2u
#define ANIM_FALL_START  4u
#define ANIM_FALL_COUNT  2u
#define ANIM_WALK_START  6u
#define ANIM_WALK_COUNT  6u

/* ---- Player spawn ---- */
#define SPAWN_X_PX  16
#define SPAWN_Y_PX  340

/* ---- Enemies ---- */
#define ENEMY_COUNT       5
#define ENEMY_SPEED       INT_TO_FX(1)
#define ENEMY_SPR_START   4u
#define ENEMY_SPR_PER     4u
#define ENEMY_HB_LEFT     2
#define ENEMY_HB_RIGHT   14
#define ENEMY_HB_TOP      2
#define ENEMY_HB_BOT     14
#define ENEMY_GROUND_Y  368

/* ---- Props ---- */
#define PROP_PLATFORME   0u
#define PROP_COLLECTIBLE 1u
#define PROP_TREMPLIN    2u
#define PROP_PICK        3u
#define PROP_COUNT       17

/* HW sprite pool for props: sprites 24-63 (40 slots) */
#define PROP_SPR_START   24u
#define PROP_SPR_MAX     40u

/* Tremplin bounce: ~12 px/frame -> ~288px (~36 tiles) height */
#define TREMPLIN_BOUNCE  ((fx16)(-192))

/* ---- Prop layout tables ---- */
/* Sizes by kind: [PLATFORME, COLLECTIBLE, TREMPLIN, PICK] */
static const u8 PROP_H[4]       = {  8u, 16u, 16u,  8u };
static const u8 PROP_SPRC[4]    = {  2u,  4u,  4u,  2u };
static const u8 PROP_ANIMAX[4]  = {  1u,  4u,  2u,  1u };

/* ---- Types ---- */
typedef struct {
    fx16 x, y;
    fx16 vel_x;
    s16  move_min;
    s16  move_max;
    u8   kind;
    u8   active;
    u8   anim_frame;
    u8   anim_tick;
} Prop;

typedef struct {
    fx16 x, y;
    s16  patrol_min;
    s16  patrol_max;
    s8   dir;
    u8   alive;
    MsprAnimator anim;
} Enemy;

/*
 * PROP_DATA[17][6]: {x, y, vel_x_int, move_min, move_max, kind}
 *   vel_x_int: 0 = static, 1 = moving right at 1 px/frame
 *   move_min/max: patrol bounds in world px (only for moving platforms)
 *
 * Layout per zone:
 *   Platformes  (5): 3 static + 2 moving
 *   Collectibles(5): above each platform
 *   Tremplins   (4): mid-air bounce pads (one-way from above)
 *   Picks       (3): HP restores near ground
 */
static const s16 PROP_DATA[PROP_COUNT][6] = {
    /* Platformes */
    { 120, 272,  0,   0,   0, (s16)PROP_PLATFORME },
    { 256, 216,  0,   0,   0, (s16)PROP_PLATFORME },
    { 432, 248,  0,   0,   0, (s16)PROP_PLATFORME },
    { 352, 200,  1, 304, 432, (s16)PROP_PLATFORME },
    { 640, 232,  1, 608, 720, (s16)PROP_PLATFORME },
    /* Collectibles */
    { 128, 256,  0,   0,   0, (s16)PROP_COLLECTIBLE },
    { 264, 200,  0,   0,   0, (s16)PROP_COLLECTIBLE },
    { 440, 232,  0,   0,   0, (s16)PROP_COLLECTIBLE },
    { 360, 184,  0,   0,   0, (s16)PROP_COLLECTIBLE },
    { 648, 216,  0,   0,   0, (s16)PROP_COLLECTIBLE },
    /* Tremplins (one-way from above, bounce on land) */
    {  64, 320,  0,   0,   0, (s16)PROP_TREMPLIN },
    { 352, 320,  0,   0,   0, (s16)PROP_TREMPLIN },
    { 528, 320,  0,   0,   0, (s16)PROP_TREMPLIN },
    { 768, 320,  0,   0,   0, (s16)PROP_TREMPLIN },
    /* Picks */
    { 176, 352,  0,   0,   0, (s16)PROP_PICK },
    { 464, 352,  0,   0,   0, (s16)PROP_PICK },
    { 704, 352,  0,   0,   0, (s16)PROP_PICK }
};

/* Enemy spawn data: {x, patrol_min, patrol_max}, ground y = ENEMY_GROUND_Y */
static const s16 ENEMY_SPAWNS[ENEMY_COUNT][3] = {
    {  48,   8,  160 },
    { 200, 176,  320 },
    { 380, 320,  480 },
    { 576, 528,  640 },
    { 808, 768,  872 }
};

/* ---- Global state ---- */
static NgpcMapStream g_ms;
static NgpcCamera    g_cam;
static NgpcPlatform  g_plat;
static MsprAnimator  g_anim;
static u8            g_anim_state;
static u8            g_facing;
static s8            g_player_hp;
static u8            g_invul;
static u8            g_score;

static Enemy g_enemies[ENEMY_COUNT];
static Prop  g_props[PROP_COUNT];

/* ---- Tile collision ---- */
static u8 tile_solid(s16 wx, s16 wy)
{
    if (wx < 0 || (u16)wx >= (u16)LEVEL_W_TILES) return 0u;
    if (wy < 0 || (u16)wy >= (u16)LEVEL_H_TILES) return 0u;
    return level_k2ge_tiles[(u16)((u16)wy * (u16)LEVEL_W_TILES + (u16)wx)] != 0u;
}

/* ---- Player animation ---- */
static void set_anim(u8 state)
{
    if (state == g_anim_state) return;
    g_anim_state = state;
    switch (state) {
    case 0: ngpc_mspr_anim_start(&g_anim, &joueur_anim[ANIM_IDLE_START], ANIM_IDLE_COUNT, 1u); break;
    case 1: ngpc_mspr_anim_start(&g_anim, &joueur_anim[ANIM_WALK_START], ANIM_WALK_COUNT, 1u); break;
    case 2: ngpc_mspr_anim_start(&g_anim, &joueur_anim[ANIM_JUMP_START], ANIM_JUMP_COUNT, 1u); break;
    case 3: ngpc_mspr_anim_start(&g_anim, &joueur_anim[ANIM_FALL_START], ANIM_FALL_COUNT, 1u); break;
    default: break;
    }
}

/* ---- Tile collision resolution ---- */
static void resolve_tile_collisions(void)
{
    s16 px  = ngpc_platform_px(&g_plat);
    s16 py  = ngpc_platform_py(&g_plat);
    s16 lt  = (s16)((px + PLAYER_HB_LEFT)      >> 3);
    s16 rt  = (s16)((px + PLAYER_HB_RIGHT - 1) >> 3);
    s16 tt  = (s16)((py + PLAYER_HB_TOP)        >> 3);
    s16 bt  = (s16)((py + PLAYER_HB_BOT)        >> 3);

    /* Floor */
    if (g_plat.vel.y >= 0) {
        if (tile_solid(lt, bt) || tile_solid(rt, bt)) {
            g_plat.pos.y = INT_TO_FX((s16)((s16)(bt * 8) - (s16)PLAYER_HB_BOT));
            ngpc_platform_land(&g_plat);
        }
    }

    /* Ceiling */
    if (g_plat.vel.y < 0) {
        if (tile_solid(lt, tt) || tile_solid(rt, tt)) {
            g_plat.vel.y = 0;
            g_plat.pos.y = INT_TO_FX((s16)((s16)((tt + 1) * 8) - (s16)PLAYER_HB_TOP));
        }
    }

    /* Walls */
    px = ngpc_platform_px(&g_plat);
    py = ngpc_platform_py(&g_plat);
    lt = (s16)((px + PLAYER_HB_LEFT)      >> 3);
    rt = (s16)((px + PLAYER_HB_RIGHT - 1) >> 3);
    {
        s16 mid_t = (s16)((py + 8) >> 3);
        if (g_plat.vel.x > 0 && tile_solid(rt, mid_t)) {
            g_plat.pos.x = INT_TO_FX((s16)((s16)(rt * 8) - (s16)PLAYER_HB_RIGHT));
            g_plat.vel.x = 0;
        }
        if (g_plat.vel.x < 0 && tile_solid(lt, mid_t)) {
            g_plat.pos.x = INT_TO_FX((s16)((s16)((lt + 1) * 8) - (s16)PLAYER_HB_LEFT));
            g_plat.vel.x = 0;
        }
    }
}

/* ---- Prop frame lookup ---- */
static const NgpcMetasprite *prop_get_frame(u8 kind, u8 frame_idx)
{
    switch (kind) {
    case PROP_PLATFORME:   return platforme_anim[frame_idx].frame;
    case PROP_COLLECTIBLE: return collectible_anim[frame_idx].frame;
    case PROP_TREMPLIN:    return tremplin_anim[frame_idx].frame;
    case PROP_PICK:        return pick_anim[frame_idx].frame;
    default:               return 0;
    }
}

/*
 * One-way collision for platformes and tremplins (must run BEFORE tile check).
 * Only triggers when falling (vel.y >= 0) and feet cross the surface.
 * Platforme -> normal land; Tremplin -> super bounce.
 */
static void resolve_prop_collisions(void)
{
    u8  i;
    s16 px, py, feet, pl, pr, prev_feet, prop_x, prop_top;

    if (g_plat.vel.y < 0) return;

    px        = ngpc_platform_px(&g_plat);
    py        = ngpc_platform_py(&g_plat);
    feet      = (s16)(py + (s16)PLAYER_HB_BOT);
    pl        = (s16)(px + (s16)PLAYER_HB_LEFT);
    pr        = (s16)(px + (s16)PLAYER_HB_RIGHT);
    /* Approximate previous feet (before this frame's gravity step) */
    prev_feet = (s16)(feet - (s16)(g_plat.vel.y >> 4));

    for (i = 0u; i < (u8)PROP_COUNT; i++) {
        if (!g_props[i].active) continue;
        if (g_props[i].kind != PROP_PLATFORME && g_props[i].kind != PROP_TREMPLIN) continue;

        prop_x   = FX_TO_INT(g_props[i].x);
        prop_top = FX_TO_INT(g_props[i].y);

        /* Horizontal overlap */
        if (pr <= prop_x || pl >= (s16)(prop_x + 16)) continue;
        /* Feet crossed the surface downward this frame */
        if (prev_feet >= prop_top || feet < prop_top) continue;

        if (g_props[i].kind == PROP_TREMPLIN) {
            g_plat.pos.y = INT_TO_FX((s16)(prop_top - (s16)PLAYER_HB_BOT));
            g_plat.vel.y = TREMPLIN_BOUNCE;
            g_plat.flags &= (u8)(~((u8)PLAT_ON_GROUND | (u8)PLAT_JUMPING));
        } else {
            g_plat.pos.y = INT_TO_FX((s16)(prop_top - (s16)PLAYER_HB_BOT));
            ngpc_platform_land(&g_plat);
        }
        break; /* one prop collision per frame */
    }
}

/* ---- Props update: animate, move, carry player, AABB overlap ---- */
static void props_update(void)
{
    u8  i, kind;
    s16 px, py, pl, pr, pb, pt_player;
    s16 prop_x, prop_y, prop_r, prop_b;

    px        = ngpc_platform_px(&g_plat);
    py        = ngpc_platform_py(&g_plat);
    pl        = (s16)(px + (s16)PLAYER_HB_LEFT);
    pr        = (s16)(px + (s16)PLAYER_HB_RIGHT);
    pt_player = (s16)(py + (s16)PLAYER_HB_TOP);
    pb        = (s16)(py + (s16)PLAYER_HB_BOT);

    for (i = 0u; i < (u8)PROP_COUNT; i++) {
        if (!g_props[i].active) continue;
        kind = g_props[i].kind;

        /* Animation */
        if (PROP_ANIMAX[kind] > 1u) {
            g_props[i].anim_tick++;
            if (g_props[i].anim_tick >= 8u) {
                g_props[i].anim_tick = 0u;
                g_props[i].anim_frame++;
                if (g_props[i].anim_frame >= PROP_ANIMAX[kind])
                    g_props[i].anim_frame = 0u;
            }
        }

        /* Moving platforms: patrol + carry player */
        if (kind == PROP_PLATFORME && g_props[i].vel_x != 0) {
            fx16 old_x    = g_props[i].x;
            s16  old_px   = FX_TO_INT(old_x);
            s16  prop_top = FX_TO_INT(g_props[i].y);
            s16  new_px;

            g_props[i].x = (fx16)(g_props[i].x + g_props[i].vel_x);
            new_px = FX_TO_INT(g_props[i].x);

            if (new_px <= g_props[i].move_min) {
                g_props[i].x     = INT_TO_FX(g_props[i].move_min);
                g_props[i].vel_x = (fx16)(-g_props[i].vel_x);
            } else if (new_px >= g_props[i].move_max) {
                g_props[i].x     = INT_TO_FX(g_props[i].move_max);
                g_props[i].vel_x = (fx16)(-g_props[i].vel_x);
            }

            /* Carry player if standing on this platform */
            if (ngpc_platform_on_ground(&g_plat) &&
                pb >= (s16)(prop_top - 1) && pb <= (s16)(prop_top + 2) &&
                pr > old_px && pl < (s16)(old_px + 16)) {
                g_plat.pos.x = (fx16)(g_plat.pos.x + (g_props[i].x - old_x));
            }
        }

        /* AABB overlap: collectible and pick */
        if (kind == PROP_COLLECTIBLE || kind == PROP_PICK) {
            prop_x = FX_TO_INT(g_props[i].x);
            prop_y = FX_TO_INT(g_props[i].y);
            prop_r = (s16)(prop_x + 16);
            prop_b = (s16)(prop_y + (s16)PROP_H[kind]);

            if (pr > prop_x && pl < prop_r &&
                pb > prop_y && pt_player < prop_b) {
                g_props[i].active = 0u;
                if (kind == PROP_COLLECTIBLE) {
                    g_score++;
                } else {
                    if (g_player_hp < (s8)PLAYER_HP_MAX)
                        g_player_hp++;
                }
            }
        }
    }
}

/* ---- Props draw: sprite pool 24-63 ---- */
static void props_draw(void)
{
    u8   i, kind, used;
    u8   spr = PROP_SPR_START;
    u8   slots = 0u;
    s16  sx, sy;
    const NgpcMetasprite *frame;

    for (i = 0u; i < (u8)PROP_COUNT; i++) {
        if (!g_props[i].active) continue;
        kind = g_props[i].kind;
        if (slots + PROP_SPRC[kind] > PROP_SPR_MAX) break; /* pool full */

        if (!ngpc_cam_on_screen(&g_cam,
                FX_TO_INT(g_props[i].x), FX_TO_INT(g_props[i].y), 16u))
            continue;

        ngpc_cam_world_to_screen(&g_cam,
            FX_TO_INT(g_props[i].x), FX_TO_INT(g_props[i].y), &sx, &sy);

        frame = prop_get_frame(kind, g_props[i].anim_frame);
        if (!frame) continue;

        used  = ngpc_mspr_draw(spr, sx, sy, frame, (u8)SPR_FRONT);
        spr   = (u8)(spr + used);
        slots = (u8)(slots + used);
    }

    /* Hide only trailing unused slots — never blank-hide upfront */
    if (spr < (u8)(PROP_SPR_START + PROP_SPR_MAX)) {
        ngpc_mspr_hide(spr, (u8)((u8)(PROP_SPR_START + PROP_SPR_MAX) - spr));
    }
}

/* ---- Props init ---- */
static void props_init(void)
{
    u8 i;
    for (i = 0u; i < (u8)PROP_COUNT; i++) {
        g_props[i].x          = INT_TO_FX(PROP_DATA[i][0]);
        g_props[i].y          = INT_TO_FX(PROP_DATA[i][1]);
        g_props[i].vel_x      = INT_TO_FX(PROP_DATA[i][2]);
        g_props[i].move_min   = PROP_DATA[i][3];
        g_props[i].move_max   = PROP_DATA[i][4];
        g_props[i].kind       = (u8)PROP_DATA[i][5];
        g_props[i].active     = 1u;
        g_props[i].anim_frame = 0u;
        g_props[i].anim_tick  = 0u;
    }
}

/* ---- Enemy init ---- */
static void enemies_init(void)
{
    u8 i;
    for (i = 0u; i < ENEMY_COUNT; i++) {
        g_enemies[i].x          = INT_TO_FX(ENEMY_SPAWNS[i][0]);
        g_enemies[i].y          = INT_TO_FX(ENEMY_GROUND_Y);
        g_enemies[i].patrol_min = ENEMY_SPAWNS[i][1];
        g_enemies[i].patrol_max = ENEMY_SPAWNS[i][2];
        g_enemies[i].dir        = 1;
        g_enemies[i].alive      = 1u;
        ngpc_mspr_anim_start(&g_enemies[i].anim, ennemie_anim,
                             ennemie_anim_count, 1u);
    }
}

/* ---- Enemy update ---- */
static void enemies_update(void)
{
    u8  i;
    s16 ex, px, py;
    s16 el, er, et, eb;
    s16 pl, pr, pt, pb;

    px = ngpc_platform_px(&g_plat);
    py = ngpc_platform_py(&g_plat);
    pl = (s16)(px + ENEMY_HB_LEFT);
    pr = (s16)(px + ENEMY_HB_RIGHT);
    pt = (s16)(py + ENEMY_HB_TOP);
    pb = (s16)(py + ENEMY_HB_BOT);

    for (i = 0u; i < ENEMY_COUNT; i++) {
        if (!g_enemies[i].alive) continue;

        /* Move */
        if (g_enemies[i].dir > 0)
            g_enemies[i].x = (fx16)(g_enemies[i].x + ENEMY_SPEED);
        else
            g_enemies[i].x = (fx16)(g_enemies[i].x - ENEMY_SPEED);

        /* Patrol bounds */
        ex = FX_TO_INT(g_enemies[i].x);
        if (ex <= g_enemies[i].patrol_min) {
            g_enemies[i].x   = INT_TO_FX(g_enemies[i].patrol_min);
            g_enemies[i].dir = 1;
        } else if (ex >= g_enemies[i].patrol_max) {
            g_enemies[i].x   = INT_TO_FX(g_enemies[i].patrol_max);
            g_enemies[i].dir = -1;
        }

        ngpc_mspr_anim_update(&g_enemies[i].anim);

        /* AABB vs player */
        if (g_invul > 0u) continue;

        ex = FX_TO_INT(g_enemies[i].x);
        el = (s16)(ex + ENEMY_HB_LEFT);
        er = (s16)(ex + ENEMY_HB_RIGHT);
        et = (s16)(FX_TO_INT(g_enemies[i].y) + ENEMY_HB_TOP);
        eb = (s16)(FX_TO_INT(g_enemies[i].y) + ENEMY_HB_BOT);

        if (pr > el && pl < er && pb > et && pt < eb) {
            g_player_hp--;
            g_invul = (u8)PLAYER_INVUL_FRAMES;
            if (g_player_hp <= 0) {
                g_player_hp = (s8)PLAYER_HP_MAX;
                ngpc_platform_init(&g_plat,
                    INT_TO_FX(SPAWN_X_PX), INT_TO_FX(SPAWN_Y_PX));
            }
        }
    }
}

/* ---- Enemy draw ---- */
static void enemies_draw(void)
{
    u8  i, spr;
    s16 sx, sy;
    u8  flags;
    const NgpcMetasprite *frame;

    for (i = 0u; i < ENEMY_COUNT; i++) {
        spr = (u8)(ENEMY_SPR_START + (u8)(i * ENEMY_SPR_PER));
        if (!g_enemies[i].alive) {
            ngpc_mspr_hide(spr, ENEMY_SPR_PER);
            continue;
        }
        if (!ngpc_cam_on_screen(&g_cam,
                FX_TO_INT(g_enemies[i].x),
                FX_TO_INT(g_enemies[i].y), 16u)) {
            ngpc_mspr_hide(spr, ENEMY_SPR_PER);
            continue;
        }
        ngpc_cam_world_to_screen(&g_cam,
            FX_TO_INT(g_enemies[i].x),
            FX_TO_INT(g_enemies[i].y), &sx, &sy);
        flags = (u8)(SPR_FRONT | (g_enemies[i].dir < 0 ? SPR_HFLIP : 0u));
        frame = g_enemies[i].anim.anim[g_enemies[i].anim.current].frame;
        ngpc_mspr_draw(spr, sx, sy, frame, flags);
    }
}

/* ---- Asset loading helper ---- */
static void load_spr_asset(const u16 NGP_FAR *tiles, u16 tiles_count,
                            u16 tile_base,
                            const u16 NGP_FAR *pals, u8 pal_count, u8 pal_base)
{
    u8 p;
    ngpc_gfx_load_tiles_at(tiles, tiles_count, tile_base);
    for (p = 0u; p < pal_count; p++) {
        u16 off = (u16)((u16)p * 4u);
        ngpc_gfx_set_palette(GFX_SPR, (u8)(pal_base + p),
            pals[off + 0u], pals[off + 1u], pals[off + 2u], pals[off + 3u]);
    }
}

/* ---- Scene init ---- */
static void scene_init(void)
{
    HW_SCR_PRIO = 0x00u;
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_sprite_hide_all();
    ngpc_gfx_set_bg_color(RGB(0, 8, 15));

    /* Sky -> SCR2 */
    NGP_TILEMAP_LOAD_TILES_VRAM(sky_map, SKY_TILE_BASE);
    NGP_TILEMAP_LOAD_PALETTES_SCR2(sky_map);
    NGP_TILEMAP_PUT_MAP_SCR2(sky_map, SKY_TILE_BASE);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);

    /* Level tiles -> SCR1 */
    NGP_TILEMAP_LOAD_TILES_VRAM(level_map, LEVEL_TILE_BASE);
    NGP_TILEMAP_LOAD_PALETTES_SCR1(level_map);

    /* Sprite assets */
    load_spr_asset(joueur_tiles,     joueur_tiles_count,     joueur_tile_base,
                   joueur_palettes,     joueur_palette_count,     joueur_pal_base);
    load_spr_asset(ennemie_tiles,    ennemie_tiles_count,    ennemie_tile_base,
                   ennemie_palettes,    ennemie_palette_count,    ennemie_pal_base);
    load_spr_asset(platforme_tiles,  platforme_tiles_count,  platforme_tile_base,
                   platforme_palettes,  platforme_palette_count,  platforme_pal_base);
    load_spr_asset(collectible_tiles,collectible_tiles_count,collectible_tile_base,
                   collectible_palettes,collectible_palette_count,collectible_pal_base);
    load_spr_asset(tremplin_tiles,   tremplin_tiles_count,   tremplin_tile_base,
                   tremplin_palettes,   tremplin_palette_count,   tremplin_pal_base);
    load_spr_asset(pick_tiles,       pick_tiles_count,       pick_tile_base,
                   pick_palettes,       pick_palette_count,       pick_pal_base);

    /* Physics */
    ngpc_platform_init(&g_plat, INT_TO_FX(SPAWN_X_PX), INT_TO_FX(SPAWN_Y_PX));
    g_player_hp = (s8)PLAYER_HP_MAX;
    g_invul     = 0u;
    g_score     = 0u;

    /* Camera */
    ngpc_cam_init(&g_cam, (s16)LEVEL_W_PX, (s16)LEVEL_H_PX, (u8)CAM_FLAG_CLAMP);
    ngpc_cam_follow(&g_cam, (s16)SPAWN_X_PX, (s16)SPAWN_Y_PX);

    /* MapStream */
    ngpc_mapstream_init(&g_ms, GFX_SCR1,
        level_k2ge_tiles,
        (u16)LEVEL_W_TILES, (u16)LEVEL_H_TILES,
        g_cam.x, g_cam.y);
    ngpc_cam_apply(&g_cam, GFX_SCR1);

    /* Animator */
    g_anim_state = 0u;
    ngpc_mspr_anim_start(&g_anim, &joueur_anim[ANIM_IDLE_START], ANIM_IDLE_COUNT, 1u);
    g_facing = 0u;

    enemies_init();
    props_init();
}

/* ---- Main ---- */
void main(void)
{
    const NgpcMetasprite *spr_frame;
    s16 sx, sy;
    u8  draw_player;

    ngpc_init();
    ngpc_load_sysfont();
    scene_init();
    ngpc_gfx_set_palette(GFX_SCR2, 15u, RGB(0,0,0), RGB(15,15,15), RGB(10,10,10), RGB(6,6,6));

    while (1) {
        ngpc_vsync();

        ngpc_mapstream_update(&g_ms, level_k2ge_tiles, g_cam.x, g_cam.y);
        ngpc_cam_apply(&g_cam, GFX_SCR1);

        ngpc_input_update();
        ngpc_debug_begin();

        /* Horizontal input */
        if (ngpc_pad_held & PAD_RIGHT) {
            g_plat.vel.x = (fx16)PLAYER_WALK_SPD;
            g_facing = 0u;
        } else if (ngpc_pad_held & PAD_LEFT) {
            g_plat.vel.x = (fx16)(0 - PLAYER_WALK_SPD);
            g_facing = (u8)SPR_HFLIP;
        } else {
            g_plat.vel.x = 0;
        }

        if (ngpc_pad_pressed  & PAD_A) ngpc_platform_press_jump(&g_plat);
        if (ngpc_pad_released & PAD_A) ngpc_platform_release_jump(&g_plat);

        ngpc_platform_update(&g_plat);

        /* Prop collisions (platformes + tremplins) BEFORE tile check */
        resolve_prop_collisions();
        resolve_tile_collisions();

        /* Props: animate, move platforms, carry player, AABB pickups */
        props_update();

        /* Enemies */
        enemies_update();

        /* Invincibility countdown */
        if (g_invul > 0u) g_invul--;

        /* Camera */
        ngpc_cam_follow_smooth(&g_cam,
            (s16)ngpc_platform_px(&g_plat),
            (s16)ngpc_platform_py(&g_plat), 4u);

        /* Player animation */
        if (!ngpc_platform_on_ground(&g_plat)) {
            set_anim(g_plat.vel.y < 0 ? 2u : 3u);
        } else if (g_plat.vel.x != 0) {
            set_anim(1u);
        } else {
            set_anim(0u);
        }
        spr_frame = ngpc_mspr_anim_update(&g_anim);

        /* Flash when invincible */
        draw_player = (u8)(g_invul == 0u || ((g_invul >> 2) & 1u));

        ngpc_cam_world_to_screen(&g_cam,
            (s16)ngpc_platform_px(&g_plat),
            (s16)ngpc_platform_py(&g_plat), &sx, &sy);
        if (draw_player) {
            ngpc_mspr_draw(PLAYER_SPR_START, sx, sy, spr_frame,
                (u8)(SPR_FRONT | g_facing));
        } else {
            ngpc_mspr_hide(PLAYER_SPR_START, 4u);
        }

        enemies_draw();
        props_draw();
        ngpc_debug_end();
        ngpc_debug_print_fps(GFX_SCR2, 15u, 15u, 0u);
        ngpc_debug_print_pct(GFX_SCR2, 15u, 15u, 1u);
    }
}
