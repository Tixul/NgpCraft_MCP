#include "shmup.h"

#include "ngpc_hw.h"
#include "ngpc_gfx.h"
#include "ngpc_input.h"
#include "ngpc_metasprite.h"
#include "ngpc_sprite.h"
#include "ngpc_text.h"
#include "ngpc_tilemap_blit.h"
#include "ngpc_timing.h"
#include "ngpc_math.h"
#include "ngpc_config.h"
#include "shmup_profile.h"
#if NGP_ENABLE_DMA
#include "ngpc_dma.h"
#endif

#if NGP_ENABLE_SOUND
#include "sounds.h"
#include "sound_data.h"
#include "audio/sfx_ids.h"
#endif

#include "shmup_bg.h"
#include "shmup_hudbar.h"
#include "shmup_bullet_mspr.h"
#include "shmup_bullet2_mspr.h"
#include "shmup_enemy1_mspr.h"
#include "shmup_enemy2_mspr.h"
#include "shmup_enemy3_mspr.h"
#include "shmup_enemy5_fat_mspr.h"
#include "shmup_boss1_mspr.h"
#include "shmup_boss2_mspr.h"
#include "shmup_explosion_mspr.h"
#include "shmup_option_a_mspr.h"
#include "shmup_option_b_mspr.h"
#include "shmup_ast1_mspr.h"
#include "shmup_ast2_mspr.h"
#include "shmup_ast3_mspr.h"
#include "shmup_ast4_mspr.h"
#include "shmup_ast5_mspr.h"
#include "shmup_player_a_mspr.h"
#include "shmup_player_b_mspr.h"
#include "shmup_trail_mspr.h"
#include "shmup_drop_final_mspr.h"
#include "ui_start_mspr.h"
#include "ui_game_over_mspr.h"
#include "ui_digits_mspr.h"
#include "ui_lifebar_mspr.h"
#include "ui_powerup_mspr.h"
#include "stage.h"

#define SHMUP_BG_TILE_BASE 128u
#define SHMUP_HUDBAR_TILE_BASE 192u
#define SHMUP_HUDBAR_PAL_BASE  1u  /* keep SCR2 pal 0 for text */

#define HUD_BAR_H_PX 16
#define PLAYFIELD_H_PX (152 - HUD_BAR_H_PX)

#define PLAYER_W 16
#define PLAYER_H 16

#define PLAYER_FIRE_INTERVAL_FRAMES 10

#define GLOBAL_SLOWDOWN_PERIOD 4u
#define WAVE_SPAWN_INTERVAL_BONUS 1u
#define BG_DMA_REBUILD_MASK    0u
#define BG_DMA_WAVE_AMP        56
#define BG_DMA_WAVE_FREQ       8u
#define BG_DMA_PHASE_STEP      1u
#define BG_DMA_PHASE_COUNT     32u
#define BG_DMA_PHASE_SHIFT     3u
#define BG_DMA_LINE_STEP       2u
#define BG_DMA_TABLE_LINES     (SCREEN_H / BG_DMA_LINE_STEP)
#define BG_DMA_TIMER0_TREG0    2u
#define BG_LEVEL2_SCROLL_DIV   2u

#define SHMUP_LEVEL_1   1u
#define SHMUP_LEVEL_2   2u
#define SHMUP_LEVEL_INF 3u /* infinite procedural level */

#define MAX_BULLETS 8
#define MAX_EBULLETS 4
#define MAX_ENEMIES 8
#define MAX_ASTEROIDS 2
#define MAX_FX 7
#define MAX_PICKUPS 3

/* Enemy patterns tuning. */
#define ENEMY1_FLAT_FRAMES  6u
#define ENEMY1_STEP_FRAMES  5u

/* Per-enemy base speeds (pixels per frame). */
#define ENEMY1_VX   (-1)
#define ENEMY2_VX   (-2)
#define ENEMY3_VX_IN  (-3)
#define ENEMY3_VX_OUT (3)

/* Enemy 3 turn tuning. */
#define ENEMY3_TURN_TRIGGER_X  28
#define ENEMY3_TURN_FRAMES     28u
#define ENEMY3_TURN_RADIUS_PX  12
#define ENEMY3_ARC_BUMP_PX      6
#define ENEMY3_SHIFT_TILE_MIN   2
#define ENEMY3_SHIFT_TILE_MAX   3

/* Enemy 4: right-side turret pair. */
#define ENEMY4_PAL_BASE         12u
#define ENEMY4_HP_MAX           2u
#define ENEMY4_X_BASE           136
#define ENEMY4_X_SPREAD          12
#define ENEMY4_Y_SPREAD          24
#define ENEMY4_MOVE_STEP_FR      3u
#define ENEMY4_FIRE_INTERVAL_FR  54u
#define ENEMY4_FIRE_STAGGER_FR   12u

/* Asteroid spawn tuning. */
#define AST_MAX_ACTIVE  2u

/* Fat enemy (16x16) */
#define FAT_STOP_X            136
#define FAT_VX_IN            (-2)
#define FAT_HP_MAX            3u
#define FAT_FIRE_INTERVAL_FR  60u

/* Boss level 1 (24x32). */
#define BOSS1_STOP_X         128
#define BOSS1_SPAWN_Y         40
#define BOSS1_VX_IN          (-1)
#define BOSS1_HP_MAX        100u
#define BOSS1_MOVE_TOP       16
#define BOSS1_MOVE_BOTTOM    88
#define BOSS1_FIRE_AIM_FR    26u
#define BOSS1_FIRE_FAN_FR    44u
#define BOSS1_FIRE_WALL_FR   68u

/* Boss level 2 (32x32). */
#define BOSS2_STOP_X         118
#define BOSS2_SPAWN_Y         34
#define BOSS2_VX_IN          (-2)
#define BOSS2_HP_MAX        120u
#define BOSS2_MOVE_TOP       10
#define BOSS2_MOVE_BOTTOM    86
#define BOSS2_MOVE_LEFT      112
#define BOSS2_MOVE_RIGHT     124
#define BOSS2_FIRE_LANCE_FR   24u
#define BOSS2_FIRE_CROSS_FR   42u
#define BOSS2_FIRE_SWEEP_FR   34u

#define PLAYER_INITIAL_LIVES 3u
#define PLAYER_BASE_SPEED_PX 1u
#define PLAYER_SPEED_MAX_LEVEL 4u
#define PLAYER_DOUBLE_MAX_LEVEL 2u
#define PLAYER_PIERCE_MAX_LEVEL 1u
#define PLAYER_OPTION_MAX_LEVEL 1u
#define STAGE_CLEAR_LIFE_BONUS 1000u
#define PU_DENY_FLASH_FRAMES 6u
#define END_TEXT_PAL 0u

/* HUD sprites (8x8): 5 score digits + 1 power-up cursor + 3 life icons. */
#define SPR_HUD_SCORE_BASE  SPR_UI_GAMEOVER_BASE /* 5 sprites */
#define SPR_HUD_PU_SPR      (SPR_HUD_SCORE_BASE + 5u) /* 1 sprite */
#define SPR_HUD_LIFE_BASE   (SPR_PICKUP_BASE + MAX_PICKUPS) /* 3 sprites, hidden during START */
#define HUD_SCORE_DIGITS    5u
#define HUD_LIFE_SLOTS      3u
#define HUD_SCORE_X         114
#define HUD_SCORE_Y         140
#define HUD_PU_Y            140
#define HUD_LIFE_Y          140

/* OPTION (Nemesis/Gradius-style): exported as 2 overlayed 8x8 sprites (uses player ship palettes A+B). */
#define SPR_OPTION_A        54u
#define SPR_OPTION_B        63u
#define POS_HIST_LEN        64u
#define POS_HIST_MASK       (POS_HIST_LEN - 1u)
#define OPTION_DELAY_FR     20u /* frames of delay behind the player */
#define OPTION_PATH_OFFSET_X 4
#define OPTION_PATH_OFFSET_Y 4

#define STAGE_INTRO_NO_SPAWN_FRAMES 300u /* 5 seconds */

/* Hot-path collision helpers (avoid function call overhead for 8x8 cases). */
#define AABB(ax, ay, aw, ah, bx, by, bw, bh) \
    (!(((s16)(ax) + (s16)(aw)) <= (s16)(bx) || ((s16)(bx) + (s16)(bw)) <= (s16)(ax) || \
       ((s16)(ay) + (s16)(ah)) <= (s16)(by) || ((s16)(by) + (s16)(bh)) <= (s16)(ay)))
#define HIT_8_8(ax, ay, bx, by) \
    (!(((s16)(ax) + 8) <= (s16)(bx) || ((s16)(bx) + 8) <= (s16)(ax) || ((s16)(ay) + 8) <= (s16)(by) || ((s16)(by) + 8) <= (s16)(ay)))

/* Fixed sprite slot layout to minimize VRAM writes:
 * - Spawn: ngpc_sprite_set (tile/pal/flags + pos)
 * - Per-frame: ngpc_sprite_move (pos only)
 *
 * This is much faster on real hardware than re-writing full sprite attrs
 * every frame for every object. */
#define SPR_EXHAUST_BASE   0u
#define SPR_PLAYER_A_BASE  1u  /* 16x16 -> 4 sprites */
#define SPR_PLAYER_B_BASE  5u  /* 16x16 -> 4 sprites */
#define SPR_BULLET_BASE    9u  /* MAX_BULLETS */
#define SPR_EBULLET_BASE   (SPR_BULLET_BASE + MAX_BULLETS) /* MAX_EBULLETS */
#define SPR_ENEMY_BASE     (SPR_EBULLET_BASE + MAX_EBULLETS) /* MAX_ENEMIES */
#define SPR_BOSS_BASE      SPR_ENEMY_BASE /* reuses enemy+fat slots once stage is clear */
#define SPR_FAT_BASE       (SPR_ENEMY_BASE + MAX_ENEMIES)  /* 16x16 -> 4 sprites */
#define SPR_AST_BASE       (SPR_FAT_BASE + 4u)  /* MAX_ASTEROIDS * 4 */
#define SPR_AST_STRIDE     4u
#define SPR_FX_BASE        (SPR_AST_BASE + (MAX_ASTEROIDS * SPR_AST_STRIDE)) /* MAX_FX */
#define SPR_UI_START_BASE  (SPR_FX_BASE + MAX_FX) /* 49 */
#define SPR_PICKUP_BASE    SPR_UI_START_BASE /* share slots while START/GAMEOVER hidden */
#define SPR_UI_GAMEOVER_BASE 55u

typedef struct {
    u8 active;
    u8 spr;
    u8 damage;
    u8 pierce;
    s16 x;
    s16 y;
} Bullet;

typedef struct {
    u8 active;
    u8 spr;
    s16 x;
    s16 y;
    s8 vx;
    s8 vy;
} EBullet;

typedef enum {
    PU_NONE = 0,
    PU_SPEED = 1,
    PU_MISSILE = 2, /* currently reused as the PIERCE slot in the meter */
    PU_DOUBLE = 3,
    PU_LASER = 4,   /* reserved for future REFILL LIFE slot */
    PU_OPTION = 5
} PowerUpKind;

typedef struct {
    u8 active;
    u8 type; /* 1..4 */
    u8 spr;
    u8 hp;
    u8 fire_cd;
    u8 wave_id;
    s16 base_y;
    u8 phase;
    u8 move_phase;
    u8 step_timer;
    s8 step_dir;
    s16 x;
    s16 y;
    s8 vx;
    s16 turn_x0;
    s16 turn_y0;
    s16 turn_y1;
} Enemy;

typedef struct {
    u8 active;
    u8 hp;
    u8 fire_cd;
    s16 x;
    s16 y;
    s8 vx;
} FatEnemy;

typedef struct {
    u8 active;
    u8 state;
    u8 hp;
    u8 hit_flash;
    u8 attack_step;
    u8 move_dir;
    u8 move_timer;
    u8 aim_cd;
    u8 fan_cd;
    u8 wall_cd;
    u8 death_timer;
    s16 x;
    s16 y;
    s8 vx;
} BossIntro;

enum {
    BOSS1_STATE_ENTRY = 0,
    BOSS1_STATE_FIGHT = 1,
    BOSS1_STATE_DYING = 2
};

typedef enum {
    PLAYER_SHOT_NORMAL = 0,
    PLAYER_SHOT_DOUBLE = 1
} PlayerShotKind;

typedef struct {
    u8 active;
    u8 type; /* 1..5 */
    u8 spr_base;
    s16 x;
    s16 y;
    s8 vx;
} Asteroid;

typedef struct {
    u8 active;
    u8 spr;
    s16 x;
    s16 y;
    MsprAnimator anim;
    u16 last_tile;
} Fx;

typedef struct {
    u8 active;
    u8 kind; /* PowerUpKind */
    u8 spr;
    s16 x;
    s16 y;
    s8 vx;
} Pickup;

typedef enum {
    END_SCREEN_NONE = 0,
    END_SCREEN_CONTINUE = 1,
    END_SCREEN_FINAL_GAMEOVER = 2,
    END_SCREEN_NAME_ENTRY = 3
} EndScreenState;

static u8 s_scroll_x = 0;
static u8 s_bg_scroll_x = 0;
static u8 s_bg_scroll_subpx = 0;
#if NGP_ENABLE_DMA
static u8 s_bg_dma_table[BG_DMA_TABLE_LINES];
static NgpcDmaU8Stream s_bg_dma_stream;
static u8 s_bg_dma_active = 0u;
static u8 s_bg_dma_tables_init = 0u;
static s8 s_bg_dma_phase_x[BG_DMA_PHASE_COUNT][BG_DMA_TABLE_LINES];
#endif
static u16 s_frame = 0;
static u32 s_scroll_pos = 0;
static u8 s_scroll_speed = 1;
static u16 s_spawn_timer = 0;
static u8 s_wave_remaining = 0;
static s16 s_wave_center_y = 72;
static u8 s_wave_enemy_type = 1;
static u8 s_wave_spawn_index = 0;
static u8 s_wave_id = 0;

static s16 s_player_x = 20;
static s16 s_player_y = 72;
static u8 s_player_speed_level = 0; /* 0..4 */
static u8 s_player_speed_px = PLAYER_BASE_SPEED_PX;
static u8 s_player_double_level = 0; /* 0..2 */
static u8 s_player_pierce_level = 0; /* 0..1 */
static u8 s_player_shot_kind = (u8)PLAYER_SHOT_NORMAL;
static u8 s_shield_hits = 0; /* 0/1 for now */
static u8 s_fire_cd = 0;
static MsprAnimator s_exhaust_anim;
static u8 s_player_inv = 0;
static s16 s_player_last_x = -32768;
static s16 s_player_last_y = -32768;
static s16 s_exhaust_last_x = -32768;
static s16 s_exhaust_last_y = -32768;
static u8 s_option_fire_toggle = 0;

static u16 s_score = 0;
static u8 s_lives = PLAYER_INITIAL_LIVES;
static u8 s_game_over = 0;
static u8 s_game_over_delay = 0;
static u8 s_stage_clear = 0;
static u16 s_stage_clear_base_score = 0;
static u16 s_stage_clear_bonus = 0;
static u16 s_stage_clear_total = 0;
static u16 s_start_timer = 0;
static u8 s_ui_game_over_visible = 0;
static u8 s_pu_test_toggle = 0;
static u8 s_pu_cursor = 0; /* 0=none, 1..5 = S/M/D/L/O (Nemesis-style meter) */
static u8 s_pu_deny_flash = 0;
static u8 s_current_level = SHMUP_LEVEL_1;
static u8 s_continues_left = 0u;
static u8 s_end_screen = (u8)END_SCREEN_NONE;
static u8 s_continue_choice = 0u;
static char s_name_entry[3] = { 'A', 'A', 'A' };
static u8 s_name_entry_pos = 0u;
static u16 s_last_explosion_sfx_frame = 0xFFFFu;

/* ---- Infinite level 3 ---- */
typedef struct { u8 wave_interval_fr; u8 min_count; u8 max_count; u8 ast_chance; } InfTier;
static const InfTier s_inf_tiers[5] = {
    /* tier 0 (waves  1-10): warm-up  */ { 180u, 3u, 5u,  0u },
    /* tier 1 (waves 11-20): medium   */ { 150u, 4u, 6u, 20u },
    /* tier 2 (waves 21-30): harder   */ { 120u, 4u, 7u, 35u },
    /* tier 3 (waves 31-40): combos   */ { 100u, 5u, 7u, 55u },
    /* tier 4 (waves 41+  ): intense  */ {  80u, 5u, 8u, 70u },
};
static u8  s_inf_wave_timer  = 0u;
static u16 s_inf_wave_count  = 0u;
static u8  s_inf_tier        = 0u;
static u8  s_inf_ast_pending = 0u; /* frames until deferred asteroid spawns */

static Bullet s_bullets[MAX_BULLETS];
static EBullet s_ebullets[MAX_EBULLETS];
static Enemy s_enemies[MAX_ENEMIES];
static FatEnemy s_fat;
static BossIntro s_boss1;
static Asteroid s_asteroids[MAX_ASTEROIDS];
static Fx s_fx[MAX_FX];
static Pickup s_pickups[MAX_PICKUPS];

static u8 s_bullets_active = 0;
static u8 s_ebullets_active = 0;
static u8 s_enemies_active = 0;
static u8 s_asteroids_active = 0;
static u8 s_pickups_active = 0;

static u8 s_bullets_alloc = 0;
static u8 s_ebullets_alloc = 0;
static u8 s_enemies_alloc = 0;
static u8 s_fx_alloc = 0;
static u8 s_pickups_alloc = 0;

static u16 s_hud_score = 0xFFFFu;
static u8 s_hud_lives = 0xFFu;
static u8 s_hud_game_over = 0xFFu;
static u8 s_hud_spd = 0xFFu;
static u8 s_hud_shield = 0xFFu;
static u8 s_hud_pu = 0xFFu;

static u8 s_hud_spr_ready = 0;
static u8 s_hud_spr_visible = 0;
static u16 s_hud_digit_tile[10];
static u16 s_hud_pu_tile[5];
static u16 s_hud_life_tile = 0;
static u8 s_hud_digit_pal = 0;
static u8 s_hud_pu_pal = 0;
static u8 s_hud_life_pal = 0;

static StagePlayer s_stage;
static u8 s_speed_preset = 0; /* 0=normal, 1=preboss slow */
static u8 s_stage_started = 0;
static u8 s_wave_spawn_interval = 10u;
static u8 s_boss_intro_started = 0;

static u8 s_player_frame = 1u;
static u8 s_player_visible = 1u;
static u16 s_exhaust_last_tile = 0xFFFFu;

static u8 s_enemy3_turn_ang[ENEMY3_TURN_FRAMES + 1u];
static u8 s_enemy3_turn_lerp[ENEMY3_TURN_FRAMES + 1u]; /* 0..255 */
static u8 s_enemy3_tables_init = 0;

static u8 s_option_active = 0;
static s16 s_option_x = 0;
static s16 s_option_y = 0;
static u8 s_pos_hist_pos = 0;
static s16 s_pos_hist_x[POS_HIST_LEN];
static s16 s_pos_hist_y[POS_HIST_LEN];

#define SEC(s) ((u16)((s) * 60u)) /* with scroll_speed=1 px/frame, 1s == 60px */

/* Stage 1: intro -> pression -> respiration -> pression max -> pre-boss.
 * Scroll-based (Nemesis-style): delay = pixels de scroll (invariant si scroll_speed change).
 * Avec scroll_speed=2 : SEC(s) = s*30 frames reels.
 *
 * Asteroides : 0 en Act1/Act3 (lisibilite), 1 en Act2 debut, 2 ensemble en Act2 fin,
 *              2 puis 3 en Act4 (passage dangereux), 1 isole en pre-boss. */
static const StageEvt s_stage1[] = {
    /* ---- Act 1: INTRO — alternance haut/bas/centre, pas d'asteroides ---- */
    /* scroll_speed=2 : SEC(5)=2.5s, SEC(6)=3s entre vagues                 */
    { SEC(5),  STG_WAVE, 1, 5, 9,  32 },    /* haut */
    { SEC(6),  STG_WAVE, 2, 5, 7, 104 },    /* bas (sinusoide) */
    { SEC(5),  STG_WAVE, 1, 5, 9,  72 },    /* centre */
    { SEC(6),  STG_WAVE, 2, 6, 7,  48 },    /* mi-haut */
    { SEC(5),  STG_WAVE, 1, 6, 9, 112 },    /* bas */

    /* ---- Act 2: PRESSION — 1 asteroide, puis 2 simultanes ---- */
    /* SEC(3)=1.5s, SEC(4)=2s entre vagues                        */
    { SEC(4),  STG_WAVE, 2, 7, 6,  56 },
    { SEC(2),  STG_AST,  3, 96, (u8)(s8)-2, 0 },   /* 1 asteroide (bas) */
    { SEC(3),  STG_WAVE, 1, 7, 8,  40 },            /* haut */
    { SEC(3),  STG_WAVE, 2, 7, 6, 104 },            /* bas */
    { SEC(4),  STG_WAVE, 3, 6, 6,  72 },            /* centre (arc) */
    /* 2 asteroides haut + bas -> couloir centre */
    { SEC(0),  STG_AST,  1, 28,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  4, 108, (u8)(s8)-2, 0 },
    { SEC(3),  STG_WAVE, 1, 7, 8,  88 },            /* mi-bas */
    { SEC(3),  STG_WAVE, 2, 8, 6,  40 },            /* haut */
    { SEC(4),  STG_WAVE, 3, 7, 6,  96 },            /* mi-bas (arc) */
    { SEC(0),  STG_WAIT_CLEAR, 0, 0, 0, 0 },
    { SEC(3),  STG_AST,  1, 16,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  4, 112, (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  5, 56,  (u8)(s8)-2, 0 },
    { SEC(2),  STG_AST,  3, 88,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  2, 32,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  1, 120, (u8)(s8)-3, 0 },
    { SEC(2),  STG_AST,  4, 72,  (u8)(s8)-2, 0 },

    /* ---- Act 3: RESPIRATION — 0 asteroide ---- */
    { SEC(5),  STG_WAVE, 1, 5, 9,  48 },
    { SEC(5),  STG_WAVE, 2, 6, 7, 104 },
    { SEC(5),  STG_WAVE, 1, 5, 9,  72 },

    /* ---- Act 4: PRESSION MAX — 2 puis 3 asteroides ---- */
    { SEC(3),  STG_WAVE, 2, 8, 5,  64 },
    { SEC(3),  STG_WAVE, 1, 7, 7,  32 },
    /* 2 asteroides simultanes (haut + bas, vitesses differentes) */
    { SEC(0),  STG_AST,  2, 40,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  5, 96,  (u8)(s8)-3, 0 },
    { SEC(3),  STG_WAVE, 3, 8, 6,  88 },
    { SEC(3),  STG_WAVE, 2, 8, 5, 112 },
    { SEC(3),  STG_WAVE, 1, 7, 7,  24 },
    /* 3 asteroides en sequence = passage dangereux */
    { SEC(0),  STG_AST,  1, 32,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  3, 72,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  4, 112, (u8)(s8)-2, 0 },
    { SEC(4),  STG_WAVE, 3, 8, 6,  56 },
    { SEC(0),  STG_WAIT_CLEAR, 0, 0, 0, 0 },
    { SEC(3),  STG_AST,  2, 24,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  5, 96,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  4, 120, (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  1, 44,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  3, 76,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  2, 12,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  5, 108, (u8)(s8)-2, 0 },
    { SEC(2),  STG_AST,  4, 60,  (u8)(s8)-3, 0 },

    /* ---- Pre-boss — ralenti + 1 asteroide isole ---- */
    { SEC(0),  STG_SET_SPEED, 1, 0, 0, 0 },
    { SEC(5),  STG_WAVE, 2, 6, 7,  72 },
    { SEC(2),  STG_AST,  2, 56,  (u8)(s8)-2, 0 },  /* 1 seul, milieu */
    { SEC(4),  STG_WAVE, 1, 5, 9,  48 },
    { SEC(5),  STG_WAVE, 3, 6, 6,  96 },

    { 0, STG_END, 0, 0, 0, 0 }
};

static const StageEvt s_stage2[] = {
    /* ---- Act 1: installation / lecture ---- */
    { SEC(5),  STG_WAVE, 1, 5, 9,  40 },
    { SEC(5),  STG_WAVE, 2, 5, 7, 104 },
    { SEC(4),  STG_WAVE, 1, 5, 9,  72 },
    { SEC(2),  STG_PU,   0, 72, (u8)(s8)-1, 0 },
    { SEC(5),  STG_WAVE, 3, 5, 6,  56 },

    /* ---- Act 2: premiere pression ---- */
    { SEC(3),  STG_WAVE, 2, 7, 6,  24 },
    { SEC(2),  STG_AST,  1, 24,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  4, 108, (u8)(s8)-3, 0 },
    { SEC(3),  STG_WAVE, 1, 7, 8,  96 },
    { SEC(3),  STG_WAVE, 3, 6, 6,  56 },
    { SEC(2),  STG_AST,  3, 72,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  2, 40,  (u8)(s8)-2, 0 },
    { SEC(3),  STG_WAVE, 2, 7, 6,  80 },

    /* ---- Act 3: respiration ---- */
    { SEC(0),  STG_WAIT_CLEAR, 0, 0, 0, 0 },
    { SEC(5),  STG_WAVE, 1, 5, 9,  32 },
    { SEC(3),  STG_AST,  2, 64,  (u8)(s8)-2, 0 },  /* 1 asteroide isolé milieu */
    { SEC(2),  STG_WAVE, 2, 5, 7, 112 },
    { SEC(2),  STG_PU,   0, 88, (u8)(s8)-1, 0 },
    { SEC(3),  STG_WAVE, 1, 5, 9,  72 },

    /* ---- Act 4: vraie phase dure / esquive ---- */
    { SEC(0),  STG_WAIT_CLEAR, 0, 0, 0, 0 },
    { SEC(2),  STG_AST,  2, 24,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  5, 104, (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  3, 64,  (u8)(s8)-2, 0 },
    { SEC(3),  STG_WAVE, 4, 2, 18,  56 },          /* duo tireur droit */
    { SEC(3),  STG_WAVE, 1, 8, 7, 104 },
    { SEC(1),  STG_AST,  1, 16,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  4, 120, (u8)(s8)-2, 0 },
    { SEC(3),  STG_WAVE, 3, 7, 5,  72 },
    { SEC(1),  STG_AST,  2, 48,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  5, 84,  (u8)(s8)-2, 0 },
    { SEC(3),  STG_WAVE, 2, 8, 5,  56 },

    /* ---- Act 5: retombee / reset mental ---- */
    { SEC(0),  STG_WAIT_CLEAR, 0, 0, 0, 0 },
    { SEC(4),  STG_WAVE, 1, 5, 9,  48 },            /* ouverture moins longue */
    { SEC(3),  STG_AST,  2, 36,  (u8)(s8)-2, 0 },  /* ast haut pour casser le calme */
    { SEC(2),  STG_WAVE, 3, 5, 6,  96 },
    { SEC(2),  STG_AST,  4, 60,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  5, 108, (u8)(s8)-3, 0 },  /* 2e ast bas → couloir milieu */
    { SEC(3),  STG_WAVE, 1, 5, 9,  24 },

    /* ---- Act 6: reprise longue et nerveuse ---- */
    { SEC(3),  STG_WAVE, 2, 8, 5, 104 },
    { SEC(3),  STG_WAVE, 1, 8, 7,  16 },
    { SEC(2),  STG_AST,  3, 92,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  2, 36,  (u8)(s8)-2, 0 },
    { SEC(3),  STG_WAVE, 3, 7, 5,  72 },
    { SEC(2),  STG_PU,   0, 56, (u8)(s8)-1, 0 },
    { SEC(3),  STG_WAVE, 2, 8, 5,  56 },
    { SEC(3),  STG_WAVE, 1, 8, 7, 112 },
    { SEC(2),  STG_AST,  1, 20,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  5, 116, (u8)(s8)-3, 0 },
    { SEC(3),  STG_WAVE, 3, 8, 5,  40 },
    { SEC(1),  STG_AST,  4, 72,  (u8)(s8)-2, 0 },  /* ast centre avant dernière wave */
    { SEC(2),  STG_WAVE, 2, 8, 5,  88 },            /* réduit à SEC(2) : enchaîne vite */

    /* ---- Act 7: dernier pic avant boss ---- */
    { SEC(0),  STG_WAIT_CLEAR, 0, 0, 0, 0 },
    { SEC(2),  STG_AST,  2, 28,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  3, 96,  (u8)(s8)-2, 0 },
    { SEC(1),  STG_AST,  4, 64,  (u8)(s8)-3, 0 },
    { SEC(3),  STG_WAVE, 4, 2, 18,  72 },          /* duo tireur droit */
    { SEC(3),  STG_WAVE, 2, 8, 5, 104 },
    { SEC(3),  STG_WAVE, 3, 8, 5,  56 },
    { SEC(1),  STG_AST,  1, 12,  (u8)(s8)-3, 0 },
    { SEC(1),  STG_AST,  5, 108, (u8)(s8)-2, 0 },
    { SEC(3),  STG_WAVE, 2, 8, 5,  80 },

    /* ---- Pre-boss: relachement court ---- */
    { SEC(0),  STG_WAIT_CLEAR, 0, 0, 0, 0 },
    { SEC(0),  STG_SET_SPEED, 1, 0, 0, 0 },
    { SEC(5),  STG_WAVE, 2, 6, 6,  72 },
    { SEC(2),  STG_AST,  4, 44,  (u8)(s8)-2, 0 },
    { SEC(4),  STG_WAVE, 1, 6, 8,  40 },
    { SEC(5),  STG_WAVE, 3, 6, 5,  96 },

    { 0, STG_END, 0, 0, 0, 0 }
};

static void stage_print_text(u8 text_id, u8 x, u8 y)
{
    (void)text_id;
    (void)x;
    (void)y;
    /* System font text is disabled for now (hardware artifacts cleanup). */
}

static const StageEvt *stage_script_for_level(u8 level)
{
    if (level == SHMUP_LEVEL_2)   return s_stage2;
    if (level == SHMUP_LEVEL_INF) return 0; /* procedural: no script */
    return s_stage1;
}

static u8 level_uses_dma(u8 level)
{
    return (u8)(level == SHMUP_LEVEL_2);
}

static s8 enemy_vx_apply_preset(u8 enemy_type, s8 vx)
{
    (void)enemy_type;
    if (!s_speed_preset) return vx;
    /* Preboss: reduce speed a bit, but never stop. */
    if (vx > 0) {
        vx = (s8)(vx - 1);
        if (vx < 1) vx = 1;
    } else if (vx < 0) {
        vx = (s8)(vx + 1);
        if (vx > -1) vx = -1;
    }
    return vx;
}

static u8 scroll_dx_apply_global_slowdown(u8 dx)
{
    if (dx > 1u && ((s_frame % GLOBAL_SLOWDOWN_PERIOD) == 0u)) {
        dx--;
    }
    return dx;
}

static u8 bg_scroll_dx_from_gameplay_dx(u8 dx)
{
    if (s_current_level != SHMUP_LEVEL_2) return dx;

    s_bg_scroll_subpx = (u8)(s_bg_scroll_subpx + dx);
    dx = (u8)(s_bg_scroll_subpx / BG_LEVEL2_SCROLL_DIV);
    s_bg_scroll_subpx = (u8)(s_bg_scroll_subpx % BG_LEVEL2_SCROLL_DIV);
    return dx;
}

static s8 motion_dx_apply_global_slowdown(s8 dx)
{
    if ((dx <= -2 || dx >= 2) && ((s_frame % GLOBAL_SLOWDOWN_PERIOD) == 0u)) {
        dx = (dx > 0) ? (s8)(dx - 1) : (s8)(dx + 1);
    }
    return dx;
}

static u8 wave_state_touch(u8 wave_id);
static void fat_try_spawn(u8 wave_enemy_type, u8 wave_count, u8 center_y_px);
static const NgpcMetasprite *boss_def_for_level(u8 level);
static u8 boss_hit_w_for_level(u8 level);
static u8 boss_hit_h_for_level(u8 level);
static void boss1_hide(void);
static void boss1_spawn(void);
static void boss1_start_dying(void);
static void boss1_fire_aimed(void);
static void boss1_fire_fan(void);
static void boss1_fire_wall(void);
static void boss2_fire_lances(void);
static void boss2_fire_cross(void);
static void boss2_fire_sweep(void);
static void stage_clear_enter(void);
static void end_text_palette_init(void);
static void end_screen_prepare(void);
static void end_screen_draw_continue(void);
static void end_screen_draw_final_gameover(void);
static void end_screen_draw_name_entry(void);
static void end_screen_enter_continue(void);
static void end_screen_enter_final_gameover(void);
static void end_screen_enter_name_entry(void);
static char name_entry_char_prev(char c);
static char name_entry_char_next(char c);
static void shmup_start_level(u8 level, u8 keep_progress, u8 refill_lives);
static void shmup_inf_update(void);
static void hud_sprites_init(void);
static void hud_sprites_hide(void);
#if NGP_ENABLE_DMA
static void bg_dma_tables_init(void);
static void bg_repeat_rows_scr1(u8 src_h);
static void bg_dma_disable(void);
static void bg_dma_build_table(u8 *out_table, u8 base_x, u8 phase);
static void bg_dma_enable_for_level(u8 level);
#endif

static void stage_start_wave(u8 enemy_type, u8 count, u8 interval_frames, u8 center_y_px)
{
    u8 spawn_interval;
    if (s_wave_remaining != 0) {
        return;
    }
    s_wave_id++;
    (void)wave_state_touch(s_wave_id);
    s_wave_remaining = count;
    s_wave_center_y = (s16)center_y_px;
    s_wave_enemy_type = enemy_type;
    s_wave_spawn_index = 0;
    s_spawn_timer = 0;
    spawn_interval = (interval_frames == 0u) ? 1u : interval_frames;
    if (spawn_interval < (u8)(255u - WAVE_SPAWN_INTERVAL_BONUS)) {
        spawn_interval = (u8)(spawn_interval + WAVE_SPAWN_INTERVAL_BONUS);
    }
    s_wave_spawn_interval = spawn_interval;

    fat_try_spawn(enemy_type, count, center_y_px);
}

static void ui_show_game_over(void)
{
    if (s_continues_left > 0u) {
        end_screen_enter_continue();
    } else {
        end_screen_enter_final_gameover();
    }
}

static void hudbar_blit_scr2(u16 tile_base, u8 pal_base, u8 y_off_tiles)
{
    u16 i;

    NGP_TILEMAP_LOAD_TILES_VRAM(shmup_hudbar, tile_base);

    for (i = 0; i < (u16)shmup_hudbar_palette_count; i++) {
        ngpc_gfx_set_palette(
            GFX_SCR2,
            (u8)(pal_base + i),
            shmup_hudbar_palettes[(u16)i * 4u + 0u],
            shmup_hudbar_palettes[(u16)i * 4u + 1u],
            shmup_hudbar_palettes[(u16)i * 4u + 2u],
            shmup_hudbar_palettes[(u16)i * 4u + 3u]);
    }

    for (i = 0; i < shmup_hudbar_map_len; i++) {
        u8 x = (u8)(i % (u16)shmup_hudbar_map_w);
        u8 y = (u8)(i / (u16)shmup_hudbar_map_w);
        u16 off = (u16)(y + y_off_tiles) * 32u + (u16)x;
        u16 tile = (u16)(tile_base + shmup_hudbar_map_tiles[i]);
        u16 pal = (u16)(pal_base + (shmup_hudbar_map_pals[i] & 0x0Fu));
        HW_SCR2_MAP[off] = (u16)(tile + (pal << 9));
    }
}

static u8 aabb_hit(s16 ax, s16 ay, u8 aw, u8 ah, s16 bx, s16 by, u8 bw, u8 bh)
{
    if (ax + (s16)aw <= bx) return 0;
    if (bx + (s16)bw <= ax) return 0;
    if (ay + (s16)ah <= by) return 0;
    if (by + (s16)bh <= ay) return 0;
    return 1;
}

static void pickup_spawn_at(s16 x, s16 y, s8 vx);
static void fx_spawn_explosion(s16 x, s16 y);
static void explosion_sfx_play_once(void);
static void bullet_spawn_basic_pal(s16 x, s16 y, u8 pal);
static void bullet_kill(u8 i);
static void ebullet_kill(u8 i);
static void enemy_kill(u8 i);
static void fat_kill(void);
static void asteroid_kill(u8 i);
static void pickup_kill(u8 i);
static void player_reset_upgrades(void);

static void option_hide(void);
static void option_spawn(void);
static void option_update(void);

static void end_text_palette_init(void)
{
    ngpc_gfx_set_palette(
        GFX_SCR1,
        END_TEXT_PAL,
        RGB(0, 0, 0),
        RGB(15, 13, 3),
        RGB(12, 9, 2),
        RGB(8, 6, 2)
    );
}

static void end_screen_prepare(void)
{
#if NGP_ENABLE_DMA
    bg_dma_disable();
#endif
#if NGP_ENABLE_SOUND
    Bgm_Stop();
#endif
    boss1_hide();
    hud_sprites_hide();
    option_hide();
    ngpc_sprite_hide_all();
    s_scroll_speed = 0u;
    s_scroll_x = 0u;
    s_bg_scroll_x = 0u;
    s_bg_scroll_subpx = 0u;
    ngpc_gfx_scroll(GFX_SCR1, 0, 0);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));
    HW_SCR_PRIO = 0x00;
    end_text_palette_init();
}

static void end_screen_draw_continue(void)
{
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 5, 4, "GAME OVER");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 4, 7, "CONTINUE");
    ngpc_text_print_num(GFX_SCR1, END_TEXT_PAL, 14, 7, s_continues_left, 2);
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 7, 11, "YES");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 12, 11, "NO");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 5, 15, "A=SELECT");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 3, 17, "LEFT/RIGHT MOVE");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 5, 11, (s_continue_choice == 0u) ? ">" : " ");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 10, 11, (s_continue_choice == 1u) ? ">" : " ");
}

static void end_screen_draw_final_gameover(void)
{
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 5, 4, "GAME OVER");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 5, 8, "SCORE");
    ngpc_text_print_num(GFX_SCR1, END_TEXT_PAL, 11, 8, s_score, 5);
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 6, 15, "PRESS A");
}

static void end_screen_draw_name_entry(void)
{
    char cbuf[2];

    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 4, 3, "ENTER NAME");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 5, 6, "SCORE");
    ngpc_text_print_num(GFX_SCR1, END_TEXT_PAL, 11, 6, s_score, 5);

    cbuf[1] = '\0';
    cbuf[0] = s_name_entry[0];
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 10, 10, cbuf);
    cbuf[0] = s_name_entry[1];
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 12, 10, cbuf);
    cbuf[0] = s_name_entry[2];
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 14, 10, cbuf);
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 9, 13, "OK");

    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 8, 10, (s_name_entry_pos == 0u) ? ">" : " ");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 11, 10, (s_name_entry_pos == 1u) ? ">" : " ");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 13, 10, (s_name_entry_pos == 2u) ? ">" : " ");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 7, 13, (s_name_entry_pos == 3u) ? ">" : " ");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 1, 16, "UP/DOWN CHANGE");
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 1, 17, "A=OK L/R=SEL");
}

static void end_screen_enter_continue(void)
{
    s_end_screen = (u8)END_SCREEN_CONTINUE;
    s_continue_choice = 0u;
    s_ui_game_over_visible = 1u;
    end_screen_prepare();
    end_screen_draw_continue();
}

static void end_screen_enter_final_gameover(void)
{
    s_end_screen = (u8)END_SCREEN_FINAL_GAMEOVER;
    s_ui_game_over_visible = 1u;
    end_screen_prepare();
    end_screen_draw_final_gameover();
}

static void end_screen_enter_name_entry(void)
{
    s_end_screen = (u8)END_SCREEN_NAME_ENTRY;
    s_name_entry[0] = 'A';
    s_name_entry[1] = 'A';
    s_name_entry[2] = 'A';
    s_name_entry_pos = 0u;
    end_screen_prepare();
    end_screen_draw_name_entry();
}

static char name_entry_char_prev(char c)
{
    static const char k_name_chars[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-";
    u8 i;

    for (i = 0u; i < (u8)(sizeof(k_name_chars) - 1u); i++) {
        if (k_name_chars[i] == c) {
            return (i == 0u) ? k_name_chars[(sizeof(k_name_chars) - 2u)] : k_name_chars[i - 1u];
        }
    }
    return 'A';
}

static char name_entry_char_next(char c)
{
    static const char k_name_chars[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-";
    u8 i;

    for (i = 0u; i < (u8)(sizeof(k_name_chars) - 1u); i++) {
        if (k_name_chars[i] == c) {
            return (k_name_chars[i + 1u] == '\0') ? k_name_chars[0] : k_name_chars[i + 1u];
        }
    }
    return 'A';
}

/* Track wave states in a tiny ring so we can award 1 capsule per cleared wave
 * (and avoid awarding if some enemies were missed/offscreen). */
#define WAVE_STATE_SLOTS 16u
static u8 s_wave_state_id[WAVE_STATE_SLOTS];
static u8 s_wave_state_awarded[WAVE_STATE_SLOTS];
static u8 s_wave_state_missed[WAVE_STATE_SLOTS];
static u8 s_wave_state_pos = 0;
static u8 s_wave_state_filled = 0;

/*
 * Niveau 2 fixed drop pattern — bitmask, bit (wave_id-1) = 1 → drop capsule.
 * 20 drops / 32 waves = 62.5%.  Pattern par acte :
 *   Act1(W1-4)  : D D . D  (75%)   Act2(W5-8)  : D . D D  (75%)
 *   Act3(W9-11) : D . D    (67%)   Act4(W12-15): . D . D  (50%)
 *   Act5(W16-18): D . D    (67%)   Act6(W19-25): D . D . D D .  (57%)
 *   Act7(W26-29): . D D D  (75%)   Pre-boss(W30-32): . D .  (33%)
 */
static const u8 s_lv2_drop_mask[4] = { 0xDB, 0xD5, 0xD6, 0x5C };

static u8 player_speed_step(void)
{
    switch (s_player_speed_level) {
    case 0u: return 1u;
    case 1u: return (u8)(((s_frame & 3u) == 0u) ? 2u : 1u);
    case 2u: return (u8)(((s_frame & 1u) == 0u) ? 2u : 1u);
    case 3u: return (u8)(((s_frame & 3u) == 0u) ? 1u : 2u);
    default: return 2u;
    }
}

static u8 pu_s_is_max(void)
{
    return (u8)(s_player_speed_level >= PLAYER_SPEED_MAX_LEVEL);
}

static u8 pu_d_is_max(void)
{
    return (u8)(s_player_double_level >= PLAYER_DOUBLE_MAX_LEVEL);
}

static u8 pu_p_is_max(void)
{
    return (u8)(s_player_pierce_level >= PLAYER_PIERCE_MAX_LEVEL);
}

static u8 pu_o_is_max(void)
{
    return (u8)(s_option_active >= PLAYER_OPTION_MAX_LEVEL);
}

static void pu_deny_feedback(void)
{
    s_pu_deny_flash = PU_DENY_FLASH_FRAMES;
#if NGP_ENABLE_SOUND
    Sfx_Play(SFX_MENU_MOVE);
#endif
}

static u8 wave_state_find(u8 wave_id)
{
    u8 i;
    u8 n = s_wave_state_filled ? (u8)WAVE_STATE_SLOTS : s_wave_state_pos;
    for (i = 0; i < n; i++) {
        if (s_wave_state_id[i] == wave_id) return i;
    }
    return 0xFFu;
}

static u8 wave_state_touch(u8 wave_id)
{
    u8 idx = wave_state_find(wave_id);
    if (idx != 0xFFu) return idx;

    idx = s_wave_state_pos;
    s_wave_state_id[idx] = wave_id;
    s_wave_state_awarded[idx] = 0u;
    s_wave_state_missed[idx] = 0u;
    s_wave_state_pos++;
    if (s_wave_state_pos >= (u8)WAVE_STATE_SLOTS) {
        s_wave_state_pos = 0;
        s_wave_state_filled = 1u;
    }
    return idx;
}

static void wave_state_mark_missed(u8 wave_id)
{
    u8 idx = wave_state_touch(wave_id);
    s_wave_state_missed[idx] = 1u;
}

static u8 wave_has_active_enemies(u8 wave_id)
{
    u8 i;
    for (i = 0; i < MAX_ENEMIES; i++) {
        if (s_enemies[i].active && s_enemies[i].wave_id == wave_id) return 1u;
    }
    return 0u;
}

static void wave_try_award_capsule(u8 wave_id, s16 x, s16 y)
{
    u8 idx = wave_state_touch(wave_id);

    /* Don't award until this wave finished spawning. */
    if (wave_id == s_wave_id && s_wave_remaining != 0u) return;
    if (s_wave_state_awarded[idx]) return;
    if (s_wave_state_missed[idx]) return;
    if (wave_has_active_enemies(wave_id)) return;

    s_wave_state_awarded[idx] = 1u;

    /* Niveau 2: fixed drop pattern — same waves always drop (memorisable). */
    if (s_current_level == SHMUP_LEVEL_2) {
        u8 bit     = (u8)(s_wave_state_id[idx] - 1u);
        u8 b_idx   = (u8)(bit >> 3u);
        u8 b_mask  = (u8)(1u << (bit & 7u));
        if (b_idx >= 4u || !(s_lv2_drop_mask[b_idx] & b_mask)) return;
    }

    /* Niveau INF: random ~55% drop rate. */
    if (s_current_level == SHMUP_LEVEL_INF) {
        if ((ngpc_qrandom() % 20u) < 9u) return; /* 9/20 = 45% skip → 55% drop */
    }

    pickup_spawn_at(x, y, (s8)-1);
}

static void load_sprite_palettes(const u16 *pals, u8 pal_base, u8 pal_count)
{
    u8 i;
    for (i = 0; i < pal_count; i++) {
        u16 c0 = pals[(u16)i * 4u + 0u];
        u16 c1 = pals[(u16)i * 4u + 1u];
        u16 c2 = pals[(u16)i * 4u + 2u];
        u16 c3 = pals[(u16)i * 4u + 3u];
        ngpc_gfx_set_palette(GFX_SPR, (u8)(pal_base + i), c0, c1, c2, c3);
    }
}

static void enemy4_palette_load(void)
{
    ngpc_gfx_set_palette(
        GFX_SPR,
        ENEMY4_PAL_BASE,
        RGB(0, 0, 0),
        RGB(15, 15, 15),
        RGB(15, 10, 3),
        RGB(12, 3, 1));
}

static const MsprPart *player_bullet_part(void)
{
    if (s_player_shot_kind == (u8)PLAYER_SHOT_DOUBLE) {
        return &shmup_bullet2_frame_0.parts[0];
    }
    return &shmup_bullet_frame_0.parts[0];
}

static u8 player_bullet_damage(void)
{
    if (s_player_shot_kind == (u8)PLAYER_SHOT_DOUBLE) {
        if (s_player_double_level >= 2u) return 3u;
        return 2u;
    }
    return 1u;
}

static void enemy3_tables_ensure_init(void)
{
    u8 t;
    if (s_enemy3_tables_init) return;
    for (t = 0; t <= (u8)ENEMY3_TURN_FRAMES; t++) {
        s_enemy3_turn_ang[t] = (u8)(((u16)t * 128u) / (u16)ENEMY3_TURN_FRAMES);
        s_enemy3_turn_lerp[t] = (u8)(((u16)t * 255u) / (u16)ENEMY3_TURN_FRAMES);
    }
    s_enemy3_tables_init = 1;
}

static void fx_spawn_explosion(s16 x, s16 y);

static void player_hide(void)
{
    u8 i;
    for (i = 0; i < 4u; i++) {
        ngpc_sprite_hide((u8)(SPR_PLAYER_A_BASE + i));
        ngpc_sprite_hide((u8)(SPR_PLAYER_B_BASE + i));
    }
    s_player_visible = 0;
    s_player_last_x = -32768;
    s_player_last_y = -32768;
}

static void player_show_at(u8 frame_idx, s16 x, s16 y)
{
    u8 i;
    const NgpcMetasprite *a =
        (frame_idx == 0u) ? &shmup_player_a_frame_0 :
        (frame_idx == 2u) ? &shmup_player_a_frame_2 : &shmup_player_a_frame_1;
    const NgpcMetasprite *b =
        (frame_idx == 0u) ? &shmup_player_b_frame_0 :
        (frame_idx == 2u) ? &shmup_player_b_frame_2 : &shmup_player_b_frame_1;

    for (i = 0; i < 4u; i++) {
        const MsprPart *pa = &a->parts[i];
        const MsprPart *pb = &b->parts[i];
        ngpc_sprite_set((u8)(SPR_PLAYER_A_BASE + i), (u8)(x + (s16)pa->ox), (u8)(y + (s16)pa->oy), pa->tile, pa->pal, (u8)(SPR_FRONT | (u8)pa->flags));
        ngpc_sprite_set((u8)(SPR_PLAYER_B_BASE + i), (u8)(x + (s16)pb->ox), (u8)(y + (s16)pb->oy), pb->tile, pb->pal, (u8)(SPR_FRONT | (u8)pb->flags));
    }

    s_player_frame = frame_idx;
    s_player_visible = 1;
    s_player_last_x = x;
    s_player_last_y = y;
}

static void player_update_sprites(void)
{
    u8 frame_idx = 1u;
    u8 visible;
    u8 i;
    const NgpcMetasprite *a;
    const NgpcMetasprite *b;
    u8 pos_changed;

    if (s_game_over) {
        return;
    }

    if (ngpc_pad_held & PAD_UP) frame_idx = 0u;
    else if (ngpc_pad_held & PAD_DOWN) frame_idx = 2u;

    visible = (u8)((s_player_inv == 0u) || ((s_frame & 2u) == 0u));
    pos_changed = (u8)((s_player_x != s_player_last_x) || (s_player_y != s_player_last_y));

    /* Exhaust: animated single sprite, always attached. */
    {
        const NgpcMetasprite *exh = ngpc_mspr_anim_update(&s_exhaust_anim);
        const MsprPart *p = &exh->parts[0];
        s16 ex = (s16)(s_player_x - 4);
        s16 ey = (s16)(s_player_y + 4);
        if (s_exhaust_last_tile == 0xFFFFu) {
            ngpc_sprite_set((u8)SPR_EXHAUST_BASE, (u8)ex, (u8)ey, p->tile, p->pal, (u8)SPR_MIDDLE);
        } else {
            if (pos_changed || ex != s_exhaust_last_x || ey != s_exhaust_last_y) {
                ngpc_sprite_move((u8)SPR_EXHAUST_BASE, (u8)ex, (u8)ey);
                s_exhaust_last_x = ex;
                s_exhaust_last_y = ey;
            }
            if (s_exhaust_last_tile != p->tile) {
                ngpc_sprite_set_tile((u8)SPR_EXHAUST_BASE, p->tile);
            }
        }
        s_exhaust_last_tile = p->tile;
        if (s_exhaust_last_x == -32768) {
            s_exhaust_last_x = ex;
            s_exhaust_last_y = ey;
        }
    }

    if (!visible) {
        if (s_player_visible) {
            player_hide();
        }
        return;
    }

    if (!s_player_visible) {
        player_show_at(frame_idx, s_player_x, s_player_y);
        return;
    }

    if (frame_idx != s_player_frame) {
        a = (frame_idx == 0u) ? &shmup_player_a_frame_0 :
            (frame_idx == 2u) ? &shmup_player_a_frame_2 : &shmup_player_a_frame_1;
        b = (frame_idx == 0u) ? &shmup_player_b_frame_0 :
            (frame_idx == 2u) ? &shmup_player_b_frame_2 : &shmup_player_b_frame_1;
        for (i = 0; i < 4u; i++) {
            ngpc_sprite_set_tile((u8)(SPR_PLAYER_A_BASE + i), a->parts[i].tile);
            ngpc_sprite_set_tile((u8)(SPR_PLAYER_B_BASE + i), b->parts[i].tile);
        }
        s_player_frame = frame_idx;
    }

    if (pos_changed) {
        /* Move-only updates for the ship (8 sprites). */
        a = (s_player_frame == 0u) ? &shmup_player_a_frame_0 :
            (s_player_frame == 2u) ? &shmup_player_a_frame_2 : &shmup_player_a_frame_1;
        b = (s_player_frame == 0u) ? &shmup_player_b_frame_0 :
            (s_player_frame == 2u) ? &shmup_player_b_frame_2 : &shmup_player_b_frame_1;
        for (i = 0; i < 4u; i++) {
            const MsprPart *pa = &a->parts[i];
            const MsprPart *pb = &b->parts[i];
            ngpc_sprite_move((u8)(SPR_PLAYER_A_BASE + i), (u8)(s_player_x + (s16)pa->ox), (u8)(s_player_y + (s16)pa->oy));
            ngpc_sprite_move((u8)(SPR_PLAYER_B_BASE + i), (u8)(s_player_x + (s16)pb->ox), (u8)(s_player_y + (s16)pb->oy));
        }
        s_player_last_x = s_player_x;
        s_player_last_y = s_player_y;
    }
}

static void player_damage(void)
{
    if (s_player_inv != 0u) return;

    if (s_shield_hits != 0u) {
        s_shield_hits--;
        fx_spawn_explosion(s_player_x, s_player_y);
        s_player_inv = 60;
        return;
    }

    fx_spawn_explosion(s_player_x, s_player_y);
    if (s_lives > 0) s_lives--;
    if (s_lives == 0) {
        /* Game over: explode the ship, clear all gameplay sprites, keep only background + FX + GAME OVER. */
        {
            u8 i;
            s_game_over = 1;
            s_game_over_delay = 30u;

            /* HUD uses the same sprite slots as GAME OVER, so hide it once here. */
            hud_sprites_hide();
            option_hide();

            /* Hide ship immediately (we show an explosion FX instead). */
            ngpc_sprite_hide((u8)SPR_EXHAUST_BASE);
            for (i = 0; i < 4u; i++) {
                ngpc_sprite_hide((u8)(SPR_PLAYER_A_BASE + i));
                ngpc_sprite_hide((u8)(SPR_PLAYER_B_BASE + i));
            }
            s_player_visible = 0;

            /* Stop stage spawning. */
            s_wave_remaining = 0;
            s_spawn_timer = 0;

            /* Clear projectiles. */
            for (i = 0; i < MAX_BULLETS; i++) bullet_kill(i);
            for (i = 0; i < MAX_EBULLETS; i++) ebullet_kill(i);

            /* Clear enemies + fat. */
            for (i = 0; i < MAX_ENEMIES; i++) enemy_kill(i);
            fat_kill();
            boss1_hide();

            /* Clear asteroids. */
            for (i = 0; i < MAX_ASTEROIDS; i++) asteroid_kill(i);

            /* Clear pickups. */
            for (i = 0; i < MAX_PICKUPS; i++) pickup_kill(i);

            /* Clear FX except the ship explosion we just spawned. */
            for (i = 0; i < MAX_FX; i++) {
                if (!s_fx[i].active) continue;
                s_fx[i].active = 0;
                s_fx[i].last_tile = 0xFFFFu;
                ngpc_sprite_hide(s_fx[i].spr);
            }
            fx_spawn_explosion(s_player_x, s_player_y);
            explosion_sfx_play_once();

            /* Hide UI and re-show later after a short delay. */
            s_ui_game_over_visible = 0;
            ngpc_mspr_hide((u8)SPR_UI_GAMEOVER_BASE, ui_game_over_frame_0.count);
            ngpc_mspr_hide((u8)SPR_UI_START_BASE, ui_start_frame_0.count);
        }
    }
    s_player_inv = 60;
}

static void bullet_kill(u8 i)
{
    if (!s_bullets[i].active) return;
    s_bullets[i].active = 0;
    if (s_bullets_active) s_bullets_active--;
    ngpc_sprite_hide(s_bullets[i].spr);
}

static void ebullet_kill(u8 i)
{
    if (!s_ebullets[i].active) return;
    s_ebullets[i].active = 0;
    if (s_ebullets_active) s_ebullets_active--;
    ngpc_sprite_hide(s_ebullets[i].spr);
}

static void enemy_kill(u8 i)
{
    if (!s_enemies[i].active) return;
    s_enemies[i].active = 0;
    if (s_enemies_active) s_enemies_active--;
    ngpc_sprite_hide(s_enemies[i].spr);
}

static void asteroid_kill(u8 i)
{
    if (!s_asteroids[i].active) return;
    s_asteroids[i].active = 0;
    if (s_asteroids_active) s_asteroids_active--;
    ngpc_mspr_hide(s_asteroids[i].spr_base, (u8)SPR_AST_STRIDE);
}

static void pickup_kill(u8 i)
{
    if (!s_pickups[i].active) return;
    s_pickups[i].active = 0;
    if (s_pickups_active) s_pickups_active--;
    ngpc_sprite_hide(s_pickups[i].spr);
}

static void player_reset_upgrades(void)
{
    s_player_speed_level = 0u;
    s_player_double_level = 0u;
    s_player_pierce_level = 0u;
    s_player_shot_kind = (u8)PLAYER_SHOT_NORMAL;
    s_player_speed_px = player_speed_step();
    s_pu_cursor = 0u;
    s_pu_deny_flash = 0u;
    option_hide();
}

static void pu_advance(void)
{
    if (s_pu_cursor == (u8)PU_SPEED) s_pu_cursor = (u8)PU_MISSILE;
    else if (s_pu_cursor == (u8)PU_MISSILE) s_pu_cursor = (u8)PU_DOUBLE;
    else if (s_pu_cursor == (u8)PU_DOUBLE) s_pu_cursor = (u8)PU_LASER;
    else if (s_pu_cursor == (u8)PU_LASER) s_pu_cursor = (u8)PU_OPTION;
    else s_pu_cursor = (u8)PU_SPEED;
}

static void pu_activate(void)
{
    if (s_pu_cursor == (u8)PU_SPEED) {
        if (pu_s_is_max()) {
            pu_deny_feedback();
        } else {
            s_player_speed_level++;
            s_player_speed_px = player_speed_step();
            s_pu_cursor = 0u;
        }
    } else if (s_pu_cursor == (u8)PU_DOUBLE) {
        if (pu_d_is_max()) {
            pu_deny_feedback();
        } else {
            s_player_double_level++;
            s_player_shot_kind = (u8)PLAYER_SHOT_DOUBLE;
            s_pu_cursor = 0u;
        }
    } else if (s_pu_cursor == (u8)PU_MISSILE) {
        if (pu_p_is_max()) {
            pu_deny_feedback();
        } else {
            s_player_pierce_level = 1u;
            s_pu_cursor = 0u;
        }
    } else if (s_pu_cursor == (u8)PU_LASER) {
        if (s_lives >= PLAYER_INITIAL_LIVES) {
            pu_deny_feedback();
        } else {
            s_lives++;
            s_pu_cursor = 0u;
        }
    } else if (s_pu_cursor == (u8)PU_OPTION) {
        if (pu_o_is_max()) {
            pu_deny_feedback();
        } else {
            option_spawn();
            s_pu_cursor = 0u;
        }
    } else if (s_pu_cursor != 0u) {
        pu_deny_feedback();
    }
}

static void pickup_spawn_at(s16 x, s16 y, s8 vx)
{
    u8 k;
    const MsprPart *p = &shmup_drop_final_frame_0.parts[0];

    if (s_start_timer != 0u) return; /* avoid sprite slot overlap with START banner */
    if (s_pickups_active >= (u8)MAX_PICKUPS) return;

    for (k = 0; k < MAX_PICKUPS; k++) {
        u8 i = (u8)(s_pickups_alloc + k);
        if (i >= (u8)MAX_PICKUPS) i -= (u8)MAX_PICKUPS;
        if (!s_pickups[i].active) {
            s_pickups[i].active = 1;
            s_pickups_active++;
            s_pickups[i].kind = 0u; /* capsule */
            s_pickups[i].spr = (u8)(SPR_PICKUP_BASE + i);
            s_pickups[i].x = x;
            s_pickups[i].y = y;
            s_pickups[i].vx = (vx == 0) ? (s8)-1 : vx;
            if (s_pickups[i].y < 8) s_pickups[i].y = 8;
            if (s_pickups[i].y > PLAYFIELD_H_PX - 8) s_pickups[i].y = PLAYFIELD_H_PX - 8;

            ngpc_sprite_set(s_pickups[i].spr, (u8)s_pickups[i].x, (u8)s_pickups[i].y, p->tile, p->pal, (u8)SPR_FRONT);
            s_pickups_alloc = (u8)(i + 1u);
            if (s_pickups_alloc >= (u8)MAX_PICKUPS) s_pickups_alloc = 0;
            return;
        }
    }
}

static void pickup_spawn_scripted(u8 kind, u8 y_px, s8 vx)
{
    (void)kind;
    pickup_spawn_at(168, (s16)y_px, vx);
}

static void shmup_assets_load(void)
{
    /* Background (scroll plane 1). */
    NGP_TILEMAP_BLIT_SCR1(shmup_bg, SHMUP_BG_TILE_BASE);
#if NGP_ENABLE_DMA
    bg_repeat_rows_scr1(shmup_bg_map_h);
#endif

    /* Sprite tiles (character RAM). */
    ngpc_gfx_load_tiles_at(shmup_player_a_tiles, shmup_player_a_tiles_count, shmup_player_a_tile_base);
    ngpc_gfx_load_tiles_at(shmup_player_b_tiles, shmup_player_b_tiles_count, shmup_player_b_tile_base);
    ngpc_gfx_load_tiles_at(shmup_enemy1_tiles, shmup_enemy1_tiles_count, shmup_enemy1_tile_base);
    ngpc_gfx_load_tiles_at(shmup_enemy2_tiles, shmup_enemy2_tiles_count, shmup_enemy2_tile_base);
    ngpc_gfx_load_tiles_at(shmup_enemy3_tiles, shmup_enemy3_tiles_count, shmup_enemy3_tile_base);
    ngpc_gfx_load_tiles_at(shmup_enemy5_fat_tiles, shmup_enemy5_fat_tiles_count, shmup_enemy5_fat_tile_base);
    ngpc_gfx_load_tiles_at(shmup_boss1_tiles, shmup_boss1_tiles_count, shmup_boss1_tile_base);
    ngpc_gfx_load_tiles_at(shmup_boss2_tiles, shmup_boss2_tiles_count, shmup_boss2_tile_base);
    ngpc_gfx_load_tiles_at(shmup_bullet_tiles, shmup_bullet_tiles_count, shmup_bullet_tile_base);
    ngpc_gfx_load_tiles_at(shmup_bullet2_tiles, shmup_bullet2_tiles_count, shmup_bullet2_tile_base);
    ngpc_gfx_load_tiles_at(shmup_option_a_tiles, shmup_option_a_tiles_count, shmup_option_a_tile_base);
    ngpc_gfx_load_tiles_at(shmup_option_b_tiles, shmup_option_b_tiles_count, shmup_option_b_tile_base);
    ngpc_gfx_load_tiles_at(shmup_explosion_tiles, shmup_explosion_tiles_count, shmup_explosion_tile_base);
    ngpc_gfx_load_tiles_at(shmup_trail_tiles, shmup_trail_tiles_count, shmup_trail_tile_base);
    ngpc_gfx_load_tiles_at(shmup_drop_final_tiles, shmup_drop_final_tiles_count, shmup_drop_final_tile_base);
    ngpc_gfx_load_tiles_at(shmup_ast1_tiles, shmup_ast1_tiles_count, shmup_ast1_tile_base);
    ngpc_gfx_load_tiles_at(shmup_ast2_tiles, shmup_ast2_tiles_count, shmup_ast2_tile_base);
    ngpc_gfx_load_tiles_at(shmup_ast3_tiles, shmup_ast3_tiles_count, shmup_ast3_tile_base);
    ngpc_gfx_load_tiles_at(shmup_ast4_tiles, shmup_ast4_tiles_count, shmup_ast4_tile_base);
    ngpc_gfx_load_tiles_at(shmup_ast5_tiles, shmup_ast5_tiles_count, shmup_ast5_tile_base);
    ngpc_gfx_load_tiles_at(ui_start_tiles, ui_start_tiles_count, ui_start_tile_base);
    ngpc_gfx_load_tiles_at(ui_game_over_tiles, ui_game_over_tiles_count, ui_game_over_tile_base);
    ngpc_gfx_load_tiles_at(ui_digits_tiles, ui_digits_tiles_count, ui_digits_tile_base);
    ngpc_gfx_load_tiles_at(ui_lifebar_tiles, ui_lifebar_tiles_count, ui_lifebar_tile_base);
    ngpc_gfx_load_tiles_at(ui_powerup_tiles, ui_powerup_tiles_count, ui_powerup_tile_base);

    /* Sprite palettes (palette RAM bank). */
    load_sprite_palettes(shmup_player_a_palettes, shmup_player_a_pal_base, shmup_player_a_palette_count);
    load_sprite_palettes(shmup_player_b_palettes, shmup_player_b_pal_base, shmup_player_b_palette_count);
    load_sprite_palettes(shmup_enemy1_palettes, shmup_enemy1_pal_base, shmup_enemy1_palette_count);
    load_sprite_palettes(shmup_enemy2_palettes, shmup_enemy2_pal_base, shmup_enemy2_palette_count);
    load_sprite_palettes(shmup_enemy3_palettes, shmup_enemy3_pal_base, shmup_enemy3_palette_count);
    enemy4_palette_load();
    load_sprite_palettes(shmup_boss1_palettes, shmup_boss1_pal_base, shmup_boss1_palette_count);
    load_sprite_palettes(shmup_boss2_palettes, shmup_boss2_pal_base, shmup_boss2_palette_count);
    load_sprite_palettes(shmup_bullet_palettes, shmup_bullet_pal_base, shmup_bullet_palette_count);
    load_sprite_palettes(shmup_bullet2_palettes, shmup_bullet2_pal_base, shmup_bullet2_palette_count);
    load_sprite_palettes(shmup_explosion_palettes, shmup_explosion_pal_base, shmup_explosion_palette_count);
    load_sprite_palettes(shmup_trail_palettes, shmup_trail_pal_base, shmup_trail_palette_count);
    load_sprite_palettes(shmup_ast1_palettes, shmup_ast1_pal_base, shmup_ast1_palette_count);
    /* Asteroids share the same palette slot to save sprite palettes. */
    load_sprite_palettes(ui_start_palettes, ui_start_pal_base, ui_start_palette_count);
    load_sprite_palettes(ui_game_over_palettes, ui_game_over_pal_base, ui_game_over_palette_count);
    load_sprite_palettes(ui_lifebar_palettes, ui_lifebar_pal_base, ui_lifebar_palette_count);
}

#if NGP_ENABLE_DMA
static void bg_dma_tables_init(void)
{
    u8 phase_idx;
    u8 line;

    if (s_bg_dma_tables_init) return;

    for (phase_idx = 0u; phase_idx < BG_DMA_PHASE_COUNT; phase_idx++) {
        u8 phase = (u8)((u16)phase_idx * (256u / BG_DMA_PHASE_COUNT));

        for (line = 0u; line < (u8)BG_DMA_TABLE_LINES; line++) {
            u8 raster_line = (u8)(line * BG_DMA_LINE_STEP);
            u8 a = (u8)(phase + (u8)(raster_line * BG_DMA_WAVE_FREQ));
            s8 sx = ngpc_sin(a);
            s_bg_dma_phase_x[phase_idx][line] = (s8)(((s16)BG_DMA_WAVE_AMP * (s16)sx) >> 7);
        }
    }

    s_bg_dma_tables_init = 1u;
}

static void bg_repeat_rows_scr1(u8 src_h)
{
    u8 y;
    u8 x;

    if (src_h == 0u) return;

    for (y = src_h; y < 32u; y++) {
        u8 src_y = (u8)(y % src_h);
        for (x = 0; x < 32u; x++) {
            HW_SCR1_MAP[(u16)y * 32u + (u16)x] = HW_SCR1_MAP[(u16)src_y * 32u + (u16)x];
        }
    }
}

static void bg_dma_disable(void)
{
    if (!s_bg_dma_active) return;
    ngpc_dma_stream_end_u8(&s_bg_dma_stream);
    ngpc_dma_timer0_hblank_disable();
    s_bg_dma_active = 0u;
}

void shmup_abort(void)
{
#if NGP_ENABLE_SOUND
    Bgm_Stop();
#endif
#if NGP_ENABLE_DMA
    bg_dma_disable();
#endif
}

static void bg_dma_build_table(u8 *out_table, u8 base_x, u8 phase)
{
    u8 line;
    u8 phase_idx;

    if (!out_table) return;
    bg_dma_tables_init();
    phase_idx = (u8)((phase >> BG_DMA_PHASE_SHIFT) & (BG_DMA_PHASE_COUNT - 1u));

    for (line = 0; line < (u8)BG_DMA_TABLE_LINES; line++) {
        out_table[line] = (u8)((s16)base_x + (s16)s_bg_dma_phase_x[phase_idx][line]);
    }
}

static void bg_dma_enable_for_level(u8 level)
{
    bg_dma_disable();
    if (!level_uses_dma(level)) return;

    bg_dma_build_table(s_bg_dma_table, s_bg_scroll_x, 0u);
    ngpc_dma_stream_begin_u8(&s_bg_dma_stream,
                             NGPC_DMA_CH0,
                             (volatile u8 NGP_FAR *)&HW_SCR1_OFS_X,
                             (const u8 NGP_FAR *)s_bg_dma_table,
                             (u16)BG_DMA_TABLE_LINES,
                             NGPC_DMA_VEC_TIMER0);
    ngpc_dma_timer0_hblank_enable_treg0(BG_DMA_TIMER0_TREG0);
    s_bg_dma_active = 1u;
}
#endif

static void shmup_reset_gameplay(void)
{
    u8 i;
    enemy3_tables_ensure_init();
    s_scroll_x = 0;
    s_bg_scroll_x = 0;
    s_bg_scroll_subpx = 0;
    s_frame = 0;
    s_scroll_pos = 0;
    s_scroll_speed = 2;
    s_spawn_timer = 0;
    s_wave_remaining = 0;
    s_wave_center_y = 72;
    s_wave_enemy_type = 1;
    s_wave_spawn_index = 0;
    s_wave_spawn_interval = 10u;
    s_wave_id = 0;
    s_wave_state_pos = 0;
    s_wave_state_filled = 0;
    s_speed_preset = 0;
    s_stage_started = 0;
    s_boss_intro_started = 0;
    s_player_x = 20;
    s_player_y = 72;
    s_player_speed_level = 0;
    s_player_speed_px = PLAYER_BASE_SPEED_PX;
    s_player_double_level = 0;
    s_player_pierce_level = 0;
    s_player_shot_kind = (u8)PLAYER_SHOT_NORMAL;
    s_shield_hits = 0;
    s_fire_cd = 0;
    s_score = 0;
    s_lives = PLAYER_INITIAL_LIVES;
    s_game_over = 0;
    s_stage_clear = 0;
    s_stage_clear_base_score = 0;
    s_stage_clear_bonus = 0;
    s_stage_clear_total = 0;
    s_player_inv = 0;
    s_option_fire_toggle = 0;
    s_start_timer = 120;
    s_ui_game_over_visible = 0;
    s_pu_test_toggle = 0;
    s_pu_cursor = 0;
    s_pu_deny_flash = 0;
    s_end_screen = (u8)END_SCREEN_NONE;
    s_continue_choice = 0u;
    s_name_entry[0] = 'A';
    s_name_entry[1] = 'A';
    s_name_entry[2] = 'A';
    s_name_entry_pos = 0u;
    s_last_explosion_sfx_frame = 0xFFFFu;
    s_inf_wave_timer  = 0u;
    s_inf_wave_count  = 0u;
    s_inf_tier        = 0u;
    s_inf_ast_pending = 0u;
    option_hide();
    s_pos_hist_pos = 0;
    {
        s16 hx = (s16)(s_player_x + OPTION_PATH_OFFSET_X);
        s16 hy = (s16)(s_player_y + OPTION_PATH_OFFSET_Y);
        if (hx < 0) hx = 0;
        if (hx > (160 - 8)) hx = (160 - 8);
        if (hy < 0) hy = 0;
        if (hy > (PLAYFIELD_H_PX - 8)) hy = (PLAYFIELD_H_PX - 8);
        for (i = 0; i < (u8)POS_HIST_LEN; i++) {
            s_pos_hist_x[i] = hx;
            s_pos_hist_y[i] = hy;
        }
    }

    for (i = 0; i < MAX_BULLETS; i++) s_bullets[i].active = 0;
    for (i = 0; i < MAX_EBULLETS; i++) s_ebullets[i].active = 0;
    for (i = 0; i < MAX_ENEMIES; i++) s_enemies[i].active = 0;
    for (i = 0; i < MAX_ASTEROIDS; i++) s_asteroids[i].active = 0;
    for (i = 0; i < MAX_FX; i++) s_fx[i].active = 0;
    for (i = 0; i < MAX_PICKUPS; i++) s_pickups[i].active = 0;
    s_fat.active = 0;
    s_fat.hp = 0;
    s_fat.fire_cd = 0;
    s_boss1.active = 0;
    s_boss1.state = BOSS1_STATE_ENTRY;
    s_boss1.hp = 0;
    s_boss1.hit_flash = 0;
    s_boss1.attack_step = 0;
    s_boss1.move_dir = 0;
    s_boss1.move_timer = 0;
    s_boss1.aim_cd = 0;
    s_boss1.fan_cd = 0;
    s_boss1.wall_cd = 0;
    s_boss1.death_timer = 0;
    s_boss1.x = 0;
    s_boss1.y = 0;
    s_boss1.vx = 0;

    s_bullets_active = 0;
    s_ebullets_active = 0;
    s_enemies_active = 0;
    s_asteroids_active = 0;
    s_pickups_active = 0;
    s_bullets_alloc = 0;
    s_ebullets_alloc = 0;
    s_enemies_alloc = 0;
    s_fx_alloc = 0;
    s_pickups_alloc = 0;

    for (i = 0; i < MAX_BULLETS; i++) ngpc_sprite_hide((u8)(SPR_BULLET_BASE + i));
    for (i = 0; i < MAX_EBULLETS; i++) ngpc_sprite_hide((u8)(SPR_EBULLET_BASE + i));
    for (i = 0; i < MAX_ENEMIES; i++) ngpc_sprite_hide((u8)(SPR_ENEMY_BASE + i));
    ngpc_mspr_hide((u8)SPR_FAT_BASE, shmup_enemy5_fat_frame_0.count);
    ngpc_mspr_hide((u8)SPR_BOSS_BASE, shmup_boss1_frame_0.count);
    for (i = 0; i < MAX_ASTEROIDS; i++) ngpc_mspr_hide((u8)(SPR_AST_BASE + i * SPR_AST_STRIDE), (u8)SPR_AST_STRIDE);
    for (i = 0; i < MAX_FX; i++) ngpc_sprite_hide((u8)(SPR_FX_BASE + i));
    ngpc_mspr_hide((u8)SPR_PICKUP_BASE, (u8)MAX_PICKUPS);
    ngpc_sprite_hide((u8)SPR_EXHAUST_BASE);
    for (i = 0; i < HUD_LIFE_SLOTS; i++) {
        ngpc_sprite_hide((u8)(SPR_HUD_LIFE_BASE + i));
    }
    for (i = 0; i < 4u; i++) {
        ngpc_sprite_hide((u8)(SPR_PLAYER_A_BASE + i));
        ngpc_sprite_hide((u8)(SPR_PLAYER_B_BASE + i));
    }
    ngpc_mspr_hide((u8)SPR_UI_START_BASE, ui_start_frame_0.count);
    ngpc_mspr_hide((u8)SPR_UI_GAMEOVER_BASE, ui_game_over_frame_0.count);

    ngpc_mspr_anim_start(&s_exhaust_anim, shmup_trail_anim, shmup_trail_anim_count, 1);
    s_exhaust_last_tile = 0xFFFFu;
    s_player_visible = 0;
    player_show_at(1u, s_player_x, s_player_y);
    s_hud_score = 0xFFFFu;
    s_hud_lives = 0xFFu;
    s_hud_game_over = 0xFFu;
    s_hud_spd = 0xFFu;
    s_hud_shield = 0xFFu;

}

/*
 * shmup_inf_update — infinite level 3 procedural director.
 * Called each frame (inside STAGE_INTRO_NO_SPAWN_FRAMES guard).
 * Difficulty increases every 10 waves via s_inf_tiers[].
 */
static void shmup_inf_update(void)
{
    const InfTier *t;
    u8 etype, count, cy;

    /* Deferred asteroid spawn (20 frames after the wave). */
    if (s_inf_ast_pending > 0u) {
        s_inf_ast_pending--;
        if (s_inf_ast_pending == 0u && s_asteroids_active < (u8)AST_MAX_ACTIVE) {
            cy = (u8)(8u + (ngpc_qrandom() % (u8)(PLAYFIELD_H_PX - 24u)));
            asteroid_spawn_scripted(0u, cy, (s8)-2); /* type 0 = random */
        }
    }

    /* Count-down between waves. */
    if (s_inf_wave_timer > 0u) {
        s_inf_wave_timer--;
        return;
    }

    /* Wait for previous wave to finish spawning. */
    if (s_wave_remaining != 0u) return;

    /* Update tier (0..4) based on wave count. */
    s_inf_tier = (u8)(s_inf_wave_count / 10u);
    if (s_inf_tier >= 5u) s_inf_tier = 4u;
    t = &s_inf_tiers[s_inf_tier];

    /* Pick random enemy type (1..4). */
    etype = (u8)(1u + (ngpc_qrandom() % 4u));

    /* Pick random count within tier range. */
    count = (u8)(t->min_count + (ngpc_qrandom() % (u8)(t->max_count - t->min_count + 1u)));

    /* Type 4 (right turret pair) is tough — cap at 2 until tier 3+. */
    if (etype == 4u && s_inf_tier < 3u && count > 2u) count = 2u;

    /* Pick random vertical position. */
    cy = (u8)(8u + (ngpc_qrandom() % (u8)(PLAYFIELD_H_PX - 24u)));

    stage_start_wave(etype, count, 8u, cy);
    s_inf_wave_count++;

    /* Tier 1+: chance to queue a deferred asteroid combo. */
    if (t->ast_chance > 0u && s_asteroids_active < (u8)AST_MAX_ACTIVE && s_inf_ast_pending == 0u) {
        if ((ngpc_qrandom() % 100u) < (u16)t->ast_chance) {
            s_inf_ast_pending = 20u;
        }
    }

    s_inf_wave_timer = t->wave_interval_fr;
}

static void shmup_start_level(u8 level, u8 keep_progress, u8 refill_lives)
{
    u16 saved_score = 0;
    u8 saved_lives = PLAYER_INITIAL_LIVES;
    u8 saved_speed_level = 0;
    u8 saved_double_level = 0;
    u8 saved_pierce_level = 0;
    u8 saved_shot_kind = (u8)PLAYER_SHOT_NORMAL;
    u8 saved_option_active = 0;

#if NGP_ENABLE_DMA
    bg_dma_disable();
#endif

    if (keep_progress) {
        saved_score = s_score;
        saved_lives = s_lives;
        saved_speed_level = s_player_speed_level;
        saved_double_level = s_player_double_level;
        saved_pierce_level = s_player_pierce_level;
        saved_shot_kind = s_player_shot_kind;
        saved_option_active = s_option_active;
    }

    ngpc_sprite_hide_all();

    ngpc_gfx_scroll(GFX_SCR1, 0, 0);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    shmup_assets_load();
    hud_init();
    shmup_reset_gameplay();

    s_current_level = level;
    stage_init(&s_stage, stage_script_for_level(level));

    if (keep_progress) {
        s_score = saved_score;
        s_lives = refill_lives ? PLAYER_INITIAL_LIVES : saved_lives;
        s_player_speed_level = saved_speed_level;
        s_player_double_level = saved_double_level;
        s_player_pierce_level = saved_pierce_level;
        s_player_shot_kind = saved_shot_kind;
        s_player_speed_px = player_speed_step();
        if (saved_option_active) {
            option_spawn();
        }
    }

#if NGP_ENABLE_DMA
    bg_dma_enable_for_level(level);
#endif

#if NGP_ENABLE_SOUND
    Bgm_Stop();
    if (level == SHMUP_LEVEL_2) {
        Bgm_SetNoteTable(BGM_NIVEAU_2_NOTE_TABLE);
        Bgm_StartLoop4Ex(BGM_NIVEAU_2_CH0, BGM_NIVEAU_2_CH0_LOOP,
                         BGM_NIVEAU_2_CH1, BGM_NIVEAU_2_CH1_LOOP,
                         BGM_NIVEAU_2_CH2, BGM_NIVEAU_2_CH2_LOOP,
                         BGM_NIVEAU_2_CHN, BGM_NIVEAU_2_CHN_LOOP);
    } else {
        /* Level 1 and level INF (3) share BGM niveau 1. */
        Bgm_SetNoteTable(BGM_NIVEAU_1_NOTE_TABLE);
        Bgm_StartLoop4Ex(BGM_NIVEAU_1_CH0, BGM_NIVEAU_1_CH0_LOOP,
                         BGM_NIVEAU_1_CH1, BGM_NIVEAU_1_CH1_LOOP,
                         BGM_NIVEAU_1_CH2, BGM_NIVEAU_1_CH2_LOOP,
                         BGM_NIVEAU_1_CHN, BGM_NIVEAU_1_CHN_LOOP);
    }
#endif

    /* Show START banner centered for a short time. */
    ngpc_mspr_draw((u8)SPR_UI_START_BASE, 56, 60, &ui_start_frame_0, (u8)SPR_FRONT);
}

static void fx_spawn_explosion(s16 x, s16 y)
{
    u8 k;

    if (s_enemies_active >= 5u) {
        for (k = 0; k < MAX_FX; k++) {
            if (s_fx[k].active) {
                return;
            }
        }
    }

    for (k = 0; k < MAX_FX; k++) {
        u8 i = (u8)(s_fx_alloc + k);
        if (i >= (u8)MAX_FX) i -= (u8)MAX_FX;
        if (!s_fx[i].active) {
            s_fx[i].active = 1;
            s_fx[i].spr = (u8)(SPR_FX_BASE + i);
            s_fx[i].x = x;
            s_fx[i].y = y;
            ngpc_mspr_anim_start(&s_fx[i].anim, shmup_explosion_anim, shmup_explosion_anim_count, 0);
            s_fx[i].last_tile = 0xFFFFu;
            s_fx_alloc = (u8)(i + 1u);
            if (s_fx_alloc >= (u8)MAX_FX) s_fx_alloc = 0;
            return;
        }
    }
}

static void explosion_sfx_play_once(void)
{
#if NGP_ENABLE_SOUND
    if (s_last_explosion_sfx_frame == s_frame) return;
    s_last_explosion_sfx_frame = s_frame;
    Sfx_Play(SFX_EXPLOSION);
#endif
}

static void option_hide(void)
{
    ngpc_sprite_hide((u8)SPR_OPTION_A);
    ngpc_sprite_hide((u8)SPR_OPTION_B);
    s_option_active = 0u;
}

static void option_spawn(void)
{
    const MsprPart *pa = &shmup_option_a_frame_0.parts[0];
    const MsprPart *pb = &shmup_option_b_frame_0.parts[0];
    s16 x = (s16)(s_player_x + OPTION_PATH_OFFSET_X);
    s16 y = (s16)(s_player_y + OPTION_PATH_OFFSET_Y);
    u8 i;

    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (y > (PLAYFIELD_H_PX - 8)) y = (PLAYFIELD_H_PX - 8);

    s_option_active = 1u;
    s_option_x = x;
    s_option_y = y;

    /* Reset the delayed path so the option starts from its expected trailing slot. */
    for (i = 0; i < (u8)POS_HIST_LEN; i++) {
        s_pos_hist_x[i] = x;
        s_pos_hist_y[i] = y;
    }
    s_pos_hist_pos = 0;

    ngpc_sprite_set((u8)SPR_OPTION_A, (u8)x, (u8)y, pa->tile, pa->pal, (u8)SPR_FRONT);
    ngpc_sprite_set((u8)SPR_OPTION_B, (u8)x, (u8)y, pb->tile, pb->pal, (u8)SPR_FRONT);
}

static void option_update(void)
{
    s16 tx = (s16)(s_player_x + OPTION_PATH_OFFSET_X);
    s16 ty = (s16)(s_player_y + OPTION_PATH_OFFSET_Y);
    s16 ox, oy;

    if (tx < 0) tx = 0;
    if (tx > (160 - 8)) tx = (160 - 8);
    if (ty < 0) ty = 0;
    if (ty > (PLAYFIELD_H_PX - 8)) ty = (PLAYFIELD_H_PX - 8);

    if (!s_option_active) return;
    if (tx == s_pos_hist_x[s_pos_hist_pos] && ty == s_pos_hist_y[s_pos_hist_pos]) return;

    {
        u8 write = (u8)((s_pos_hist_pos + 1u) & (u8)POS_HIST_MASK);
        u8 read;
        s_pos_hist_x[write] = tx;
        s_pos_hist_y[write] = ty;
        s_pos_hist_pos = write;

        read = (u8)((s_pos_hist_pos - (u8)OPTION_DELAY_FR) & (u8)POS_HIST_MASK);
        ox = s_option_x;
        oy = s_option_y;
        s_option_x = s_pos_hist_x[read];
        s_option_y = s_pos_hist_y[read];
        if (s_option_x != ox || s_option_y != oy) {
            ngpc_sprite_move((u8)SPR_OPTION_A, (u8)s_option_x, (u8)s_option_y);
            ngpc_sprite_move((u8)SPR_OPTION_B, (u8)s_option_x, (u8)s_option_y);
        }
    }
}

static void ebullet_spawn_xy(s16 x, s16 y, s8 vx, s8 vy)
{
    u8 k;
    for (k = 0; k < MAX_EBULLETS; k++) {
        u8 i = (u8)(s_ebullets_alloc + k);
        if (i >= (u8)MAX_EBULLETS) i -= (u8)MAX_EBULLETS;
        if (!s_ebullets[i].active) {
            /* Use explosion_f1 tile as enemy bullet for better readability. */
            const MsprPart *p = &shmup_explosion_frame_0.parts[0];
            s_ebullets[i].active = 1;
            s_ebullets_active++;
            s_ebullets[i].spr = (u8)(SPR_EBULLET_BASE + i);
            s_ebullets[i].x = x;
            s_ebullets[i].y = y;
            s_ebullets[i].vx = (vx == 0) ? (s8)-3 : vx;
            s_ebullets[i].vy = vy;
            ngpc_sprite_set(s_ebullets[i].spr, (u8)x, (u8)y, p->tile, p->pal, (u8)SPR_FRONT);
            s_ebullets_alloc = (u8)(i + 1u);
            if (s_ebullets_alloc >= (u8)MAX_EBULLETS) s_ebullets_alloc = 0;
            return;
        }
    }
}

static void ebullet_spawn(s16 x, s16 y, s8 vx)
{
    ebullet_spawn_xy(x, y, vx, 0);
}

static void bullet_spawn_ex(s16 x, s16 y, u16 tile, u8 pal, u8 damage, u8 pierce)
{
    u8 k;
    for (k = 0; k < MAX_BULLETS; k++) {
        u8 i = (u8)(s_bullets_alloc + k);
        if (i >= (u8)MAX_BULLETS) i -= (u8)MAX_BULLETS;
        if (!s_bullets[i].active) {
            s_bullets[i].active = 1;
            s_bullets_active++;
            s_bullets[i].spr = (u8)(SPR_BULLET_BASE + i);
            s_bullets[i].damage = damage;
            s_bullets[i].pierce = pierce;
            s_bullets[i].x = x;
            s_bullets[i].y = y;
            ngpc_sprite_set(s_bullets[i].spr, (u8)x, (u8)y, tile, pal, (u8)SPR_FRONT);
            s_bullets_alloc = (u8)(i + 1u);
            if (s_bullets_alloc >= (u8)MAX_BULLETS) s_bullets_alloc = 0;
            return;
        }
    }
}

static void bullet_spawn_basic_pal(s16 x, s16 y, u8 pal)
{
    const MsprPart *p = &shmup_bullet_frame_0.parts[0];
    bullet_spawn_ex(x, y, p->tile, pal, 1u, 0u);
}

static void bullet_spawn(s16 x, s16 y)
{
    const MsprPart *p = player_bullet_part();
    bullet_spawn_ex(x, y, p->tile, p->pal, player_bullet_damage(), s_player_pierce_level);
}

static void fat_kill(void)
{
    if (!s_fat.active) return;
    s_fat.active = 0;
    ngpc_mspr_hide((u8)SPR_FAT_BASE, shmup_enemy5_fat_frame_0.count);
}

static void boss1_hide(void)
{
    s_boss1.active = 0;
    s_boss1.state = BOSS1_STATE_ENTRY;
    ngpc_mspr_hide((u8)SPR_BOSS_BASE, MSPR_MAX_PARTS);
}

static void boss1_spawn(void)
{
    u8 si;
    const NgpcMetasprite *def = boss_def_for_level(s_current_level);

    if (s_boss1.active) return;

    s_boss1.active = 1u;
    s_boss_intro_started = 1u;
    s_boss1.state = BOSS1_STATE_ENTRY;
    s_boss1.hit_flash = 0u;
    s_boss1.attack_step = 0u;
    s_boss1.move_dir = 1u;
    s_boss1.death_timer = 0u;
    s_boss1.x = 168;
    if (s_current_level >= SHMUP_LEVEL_2) {
        s_boss1.hp = BOSS2_HP_MAX;
        s_boss1.move_timer = 1u; /* horizontal direction: 0=left, 1=right */
        s_boss1.aim_cd = BOSS2_FIRE_LANCE_FR;
        s_boss1.fan_cd = BOSS2_FIRE_CROSS_FR;
        s_boss1.wall_cd = BOSS2_FIRE_SWEEP_FR;
        s_boss1.y = BOSS2_SPAWN_Y;
        s_boss1.vx = BOSS2_VX_IN;
    } else {
        s_boss1.hp = BOSS1_HP_MAX;
        s_boss1.move_timer = 0u;
        s_boss1.aim_cd = BOSS1_FIRE_AIM_FR;
        s_boss1.fan_cd = BOSS1_FIRE_FAN_FR;
        s_boss1.wall_cd = BOSS1_FIRE_WALL_FR;
        s_boss1.y = BOSS1_SPAWN_Y;
        s_boss1.vx = BOSS1_VX_IN;
    }
    s_scroll_speed = 0u;

    for (si = 0; si < def->count; si++) {
        const MsprPart *p = &def->parts[si];
        s16 px = (s16)(s_boss1.x + (s16)p->ox);
        s16 py = (s16)(s_boss1.y + (s16)p->oy);
        ngpc_sprite_set((u8)(SPR_BOSS_BASE + si), (u8)px, (u8)py, p->tile, p->pal, (u8)SPR_FRONT);
    }
}

static void boss1_start_dying(void)
{
    u8 i;
    if (!s_boss1.active || s_boss1.state == BOSS1_STATE_DYING) return;

    s_boss1.state = BOSS1_STATE_DYING;
    s_boss1.death_timer = 36u;
    s_boss1.vx = 0;
    for (i = 0; i < MAX_EBULLETS; i++) ebullet_kill(i);
    fx_spawn_explosion(
        (s16)(s_boss1.x + ((s16)boss_def_for_level(s_current_level)->width / 2)),
        (s16)(s_boss1.y + ((s16)boss_def_for_level(s_current_level)->height / 2)));
    explosion_sfx_play_once();
}

static void boss1_fire_aimed(void)
{
    s16 src_y = (s16)(s_boss1.y + 16);
    s16 dy = (s16)((s_player_y + 8) - src_y);
    s8 vy = 0;
    s8 vx = (s_boss1.hp <= (BOSS1_HP_MAX / 2u)) ? (s8)-4 : (s8)-3;

    if (dy < -18) vy = -2;
    else if (dy < -6) vy = -1;
    else if (dy > 18) vy = 2;
    else if (dy > 6) vy = 1;

    ebullet_spawn_xy((s16)(s_boss1.x - 2), (s16)(s_boss1.y + 8), vx, (s8)(vy - 1));
    ebullet_spawn_xy((s16)(s_boss1.x - 4), src_y, vx, vy);
    ebullet_spawn_xy((s16)(s_boss1.x - 2), (s16)(s_boss1.y + 24), vx, (s8)(vy + 1));
}

static void boss1_fire_fan(void)
{
    s16 x = (s16)(s_boss1.x - 2);
    s16 y = (s16)(s_boss1.y + 16);

    ebullet_spawn_xy(x, y, -3, -2);
    ebullet_spawn_xy(x, y, -3, 0);
    ebullet_spawn_xy(x, y, -3, 2);
}

static void boss1_fire_wall(void)
{
    s8 bias = s_boss1.move_dir ? 1 : -1;
    s16 x = (s16)(s_boss1.x - 2);

    ebullet_spawn_xy(x, (s16)(s_boss1.y + 6), -2, (s8)(-1 + bias));
    ebullet_spawn_xy(x, (s16)(s_boss1.y + 14), -2, bias);
    ebullet_spawn_xy(x, (s16)(s_boss1.y + 22), -2, (s8)(1 + bias));
}

static void boss2_fire_lances(void)
{
    s16 x = (s16)(s_boss1.x + 2);
    s16 y0 = (s16)(s_boss1.y + 7);
    s16 y1 = (s16)(s_boss1.y + 25);
    s16 target = (s16)(s_player_y + 8);
    s8 vy0 = 0;
    s8 vy1 = 0;

    if (target < y0 - 10) vy0 = -2;
    else if (target < y0 - 2) vy0 = -1;
    else if (target > y0 + 10) vy0 = 2;
    else if (target > y0 + 2) vy0 = 1;

    if (target < y1 - 10) vy1 = -2;
    else if (target < y1 - 2) vy1 = -1;
    else if (target > y1 + 10) vy1 = 2;
    else if (target > y1 + 2) vy1 = 1;

    ebullet_spawn_xy(x, y0, -3, vy0);
    ebullet_spawn_xy(x, y1, -3, vy1);
}

static void boss2_fire_cross(void)
{
    s16 x = (s16)(s_boss1.x + 2);

    ebullet_spawn_xy(x, (s16)(s_boss1.y + 6), -2, -2);
    ebullet_spawn_xy(x, (s16)(s_boss1.y + 12), -3, -1);
    ebullet_spawn_xy(x, (s16)(s_boss1.y + 20), -3, 1);
    ebullet_spawn_xy(x, (s16)(s_boss1.y + 26), -2, 2);
}

static void boss2_fire_sweep(void)
{
    s8 bias = s_boss1.move_dir ? 1 : -1;
    s16 x = (s16)(s_boss1.x + 2);

    ebullet_spawn_xy(x, (s16)(s_boss1.y + 8), -2, (s8)(-2 + bias));
    ebullet_spawn_xy(x, (s16)(s_boss1.y + 16), -3, bias);
    ebullet_spawn_xy(x, (s16)(s_boss1.y + 24), -2, (s8)(2 + bias));
}

static void stage_clear_enter(void)
{
    s_stage_clear = 1u;
    s_stage_clear_base_score = s_score;
    s_stage_clear_bonus = (u16)((u16)s_lives * STAGE_CLEAR_LIFE_BONUS);
    s_stage_clear_total = (u16)(s_stage_clear_base_score + s_stage_clear_bonus);
    s_score = s_stage_clear_total;
    end_screen_prepare();

    if (s_current_level == SHMUP_LEVEL_1) {
        ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 4, 4,  "LEVEL 1 CLEAR");
    } else {
        ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 4, 4,  "LEVEL 2 CLEAR");
    }
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 4, 8,  "SCORE");
    ngpc_text_print_num(GFX_SCR1, END_TEXT_PAL, 12, 8, s_stage_clear_base_score, 5);
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 4, 10, "LIFE BONUS");
    ngpc_text_print_num(GFX_SCR1, END_TEXT_PAL, 12, 10, s_stage_clear_bonus, 5);
    ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 4, 12, "TOTAL");
    ngpc_text_print_num(GFX_SCR1, END_TEXT_PAL, 12, 12, s_stage_clear_total, 5);
    if (s_current_level == SHMUP_LEVEL_1) {
        ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 1, 16, "PRESS A FOR LEVEL 2");
    } else {
        ngpc_text_print(GFX_SCR1, END_TEXT_PAL, 1, 16, "PRESS A FOR LEVEL 3");
    }
}

static void fat_spawn(u8 y_px)
{
    u8 si;
    const NgpcMetasprite *def;

    if (s_fat.active) return;

    s_fat.active = 1;
    s_fat.hp = FAT_HP_MAX;
    s_fat.fire_cd = FAT_FIRE_INTERVAL_FR;
    s_fat.x = 168;
    s_fat.y = (s16)y_px;
    s_fat.vx = FAT_VX_IN;
    if (s_fat.y < 8) s_fat.y = 8;
    if (s_fat.y > PLAYFIELD_H_PX - 16) s_fat.y = PLAYFIELD_H_PX - 16;

    def = &shmup_enemy5_fat_frame_0;
    for (si = 0; si < def->count; si++) {
        const MsprPart *p = &def->parts[si];
        s16 px = (s16)(s_fat.x + (s16)p->ox);
        s16 py = (s16)(s_fat.y + (s16)p->oy);
        ngpc_sprite_set((u8)(SPR_FAT_BASE + si), (u8)px, (u8)py, p->tile, p->pal, (u8)SPR_FRONT);
    }
    for (si = def->count; si < 4u; si++) {
        ngpc_sprite_hide((u8)(SPR_FAT_BASE + si));
    }
}

static void fat_try_spawn(u8 wave_enemy_type, u8 wave_count, u8 center_y_px)
{
    if (s_fat.active) return;
    if (wave_count > 6u) return;
    if (!(wave_enemy_type == 1u || wave_enemy_type == 2u)) return;
    fat_spawn(center_y_px);
}

static const NgpcMetasprite *boss_def_for_level(u8 level)
{
    if (level >= SHMUP_LEVEL_2) {
        return &shmup_boss2_frame_0;
    }
    return &shmup_boss1_frame_0;
}

static u8 boss_hit_w_for_level(u8 level)
{
    const NgpcMetasprite *def = boss_def_for_level(level);
    return (u8)(def->width - ((level >= SHMUP_LEVEL_2) ? 4u : 0u));
}

static u8 boss_hit_h_for_level(u8 level)
{
    const NgpcMetasprite *def = boss_def_for_level(level);
    return (u8)(def->height - ((level >= SHMUP_LEVEL_2) ? 2u : 0u));
}

static void enemy_spawn(void)
{
    u8 k;
    for (k = 0; k < MAX_ENEMIES; k++) {
        u8 i = (u8)(s_enemies_alloc + k);
        if (i >= (u8)MAX_ENEMIES) i -= (u8)MAX_ENEMIES;
        if (!s_enemies[i].active) {
            const MsprPart *p;
            s_enemies[i].active = 1;
            s_enemies_active++;
            s_enemies[i].type = s_wave_enemy_type;
            s_enemies[i].spr = (u8)(SPR_ENEMY_BASE + i);
            s_enemies[i].hp = 1u;
            s_enemies[i].fire_cd = 0u;
            s_enemies[i].wave_id = s_wave_id;
            s_enemies[i].x = 168;

            if (s_enemies[i].type == 1) {
                /* Stair-step pattern: flat segments + small per-frame vertical steps (smoother than jumps). */
                s_enemies[i].y = (s16)(s_wave_center_y + (s16)((s_wave_spawn_index & 1u) ? 10 : -10));
                if (s_enemies[i].y < 8) s_enemies[i].y = 8;
                if (s_enemies[i].y > PLAYFIELD_H_PX - 8) s_enemies[i].y = PLAYFIELD_H_PX - 8;
                s_enemies[i].base_y = s_enemies[i].y;
                s_enemies[i].phase = 0; /* 0=flat, 1=stepping */
                s_enemies[i].move_phase = 0;
                s_enemies[i].step_timer = (u8)(ENEMY1_FLAT_FRAMES + (s_wave_spawn_index & 3u));
                s_enemies[i].step_dir = (s8)((s_wave_spawn_index & 1u) ? 1 : -1);
                s_enemies[i].vx = enemy_vx_apply_preset(1u, ENEMY1_VX);
            } else if (s_enemies[i].type == 2) {
                /* Wave pattern: "file indienne" following a sine path. */
                s_enemies[i].base_y = s_wave_center_y;
                s_enemies[i].phase = 0;
                s_enemies[i].move_phase = (u8)(s_wave_spawn_index * 20u);
                s_enemies[i].step_timer = 0;
                s_enemies[i].step_dir = 0;
                s_enemies[i].y = s_enemies[i].base_y;
                s_enemies[i].vx = enemy_vx_apply_preset(2u, ENEMY2_VX);
            } else if (s_enemies[i].type == 3u) {
                /* Type 3: single-file, straight line, U-turn at left edge. */
                s_enemies[i].base_y = s_wave_center_y;
                s_enemies[i].y = s_wave_center_y;
                s_enemies[i].phase = 0; /* 0=incoming, 1=turning, 2=outgoing */
                s_enemies[i].move_phase = 0;
                s_enemies[i].step_timer = 0;
                /* step_dir selects vertical direction for the turn (+1 down, -1 up). */
                s_enemies[i].step_dir = (s8)((s_enemies[i].y < (PLAYFIELD_H_PX / 2)) ? 1 : -1);
                s_enemies[i].vx = enemy_vx_apply_preset(3u, ENEMY3_VX_IN);
                s_enemies[i].turn_x0 = 0;
                s_enemies[i].turn_y0 = 0;
                s_enemies[i].turn_y1 = 0;
            } else {
                /* Type 4: slow vertical turret pair that stays on the right side. */
                s_enemies[i].hp = ENEMY4_HP_MAX;
                s_enemies[i].x = (s16)(ENEMY4_X_BASE + ((s_wave_spawn_index & 1u) ? ENEMY4_X_SPREAD : 0));
                s_enemies[i].y = (s16)(s_wave_center_y + ((s_wave_spawn_index & 1u) ? ENEMY4_Y_SPREAD : -ENEMY4_Y_SPREAD));
                if (s_enemies[i].y < 8) s_enemies[i].y = 8;
                if (s_enemies[i].y > PLAYFIELD_H_PX - 8) s_enemies[i].y = PLAYFIELD_H_PX - 8;
                s_enemies[i].base_y = s_enemies[i].y;
                s_enemies[i].phase = 0u;
                s_enemies[i].move_phase = 0u;
                s_enemies[i].step_timer = ENEMY4_MOVE_STEP_FR;
                s_enemies[i].step_dir = (s8)((s_wave_spawn_index & 1u) ? -1 : 1);
                s_enemies[i].vx = 0;
                s_enemies[i].fire_cd = (u8)(ENEMY4_FIRE_INTERVAL_FR + ((s_wave_spawn_index & 1u) ? ENEMY4_FIRE_STAGGER_FR : 0u));
                s_enemies[i].turn_x0 = 0;
                s_enemies[i].turn_y0 = 0;
                s_enemies[i].turn_y1 = 0;
            }

            if (s_enemies[i].type == 2) {
                p = &shmup_enemy2_frame_0.parts[0];
            } else if (s_enemies[i].type == 3) {
                p = &shmup_enemy3_frame_0.parts[0];
            } else if (s_enemies[i].type == 4u) {
                p = &shmup_enemy3_frame_0.parts[0];
            } else {
                p = &shmup_enemy1_frame_0.parts[0];
            }
            ngpc_sprite_set(
                s_enemies[i].spr,
                (u8)s_enemies[i].x,
                (u8)s_enemies[i].y,
                p->tile,
                (u8)((s_enemies[i].type == 4u) ? ENEMY4_PAL_BASE : p->pal),
                (u8)SPR_FRONT);
            s_wave_spawn_index++;
            s_enemies_alloc = (u8)(i + 1u);
            if (s_enemies_alloc >= (u8)MAX_ENEMIES) s_enemies_alloc = 0;
            return;
        }
    }
}

static void asteroid_spawn_scripted(u8 type, u8 y_px, s8 vx)
{
    u8 i;
    if (s_asteroids_active >= AST_MAX_ACTIVE) {
        return;
    }
    for (i = 0; i < MAX_ASTEROIDS; i++) {
        if (!s_asteroids[i].active) {
            s_asteroids[i].active = 1;
            s_asteroids_active++;
            s_asteroids[i].type = (type == 0u) ? (u8)(1u + (ngpc_qrandom() % 5u)) : type;
            s_asteroids[i].spr_base = (u8)(SPR_AST_BASE + i * SPR_AST_STRIDE);
            s_asteroids[i].x = 160;
            s_asteroids[i].y = (s16)y_px;
            s_asteroids[i].vx = vx;
            if (s_asteroids[i].y < 8) s_asteroids[i].y = 8;
            if (s_asteroids[i].y > PLAYFIELD_H_PX - 16) s_asteroids[i].y = PLAYFIELD_H_PX - 16;
            /* Place metasprite once (static tile/pal/flags). */
            {
                u8 si;
                const NgpcMetasprite *def;
                switch (s_asteroids[i].type) {
                default:
                case 1: def = &shmup_ast1_frame_0; break;
                case 2: def = &shmup_ast2_frame_0; break;
                case 3: def = &shmup_ast3_frame_0; break;
                case 4: def = &shmup_ast4_frame_0; break;
                case 5: def = &shmup_ast5_frame_0; break;
                }
                for (si = 0; si < def->count; si++) {
                    const MsprPart *p = &def->parts[si];
                    s16 px = s_asteroids[i].x + (s16)p->ox;
                    s16 py = s_asteroids[i].y + (s16)p->oy;
                    ngpc_sprite_set(
                        (u8)(s_asteroids[i].spr_base + si),
                        (u8)((u16)px & 0xFF),
                        (u8)((u16)py & 0xFF),
                        p->tile,
                        p->pal,
                        (u8)SPR_MIDDLE);
                }
                for (si = def->count; si < (u8)SPR_AST_STRIDE; si++) {
                    ngpc_sprite_hide((u8)(s_asteroids[i].spr_base + si));
                }
            }
            return;
        }
    }
}

static void hud_init(void)
{
    /* Ensure HUD plane is in front. */
    HW_SCR_PRIO = 0x80;

    ngpc_gfx_clear(GFX_SCR2);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);

    /* Default SCR2 palette (neutral). */
    ngpc_gfx_set_palette(GFX_SCR2, 0,
        RGB(0, 0, 0),
        RGB(15, 15, 15),
        RGB(10, 10, 10),
        RGB(5, 5, 5)
    );

    /* Bottom HUD bar (2 tile rows). */
    hudbar_blit_scr2(SHMUP_HUDBAR_TILE_BASE, SHMUP_HUDBAR_PAL_BASE, 17);
}

static void hud_sprites_init(void)
{
    if (s_hud_spr_ready) return;

    s_hud_digit_tile[0] = ui_digits_frame_0.parts[0].tile;
    s_hud_digit_tile[1] = ui_digits_frame_1.parts[0].tile;
    s_hud_digit_tile[2] = ui_digits_frame_2.parts[0].tile;
    s_hud_digit_tile[3] = ui_digits_frame_3.parts[0].tile;
    s_hud_digit_tile[4] = ui_digits_frame_4.parts[0].tile;
    s_hud_digit_tile[5] = ui_digits_frame_5.parts[0].tile;
    s_hud_digit_tile[6] = ui_digits_frame_6.parts[0].tile;
    s_hud_digit_tile[7] = ui_digits_frame_7.parts[0].tile;
    s_hud_digit_tile[8] = ui_digits_frame_8.parts[0].tile;
    s_hud_digit_tile[9] = ui_digits_frame_9.parts[0].tile;
    s_hud_digit_pal = ui_digits_frame_0.parts[0].pal;

    /* Order must match S/P/D/L/O in exporter. */
    s_hud_pu_tile[0] = ui_powerup_frame_0.parts[0].tile; /* S */
    s_hud_pu_tile[1] = ui_powerup_frame_1.parts[0].tile; /* P */
    s_hud_pu_tile[2] = ui_powerup_frame_2.parts[0].tile; /* D */
    s_hud_pu_tile[3] = ui_powerup_frame_3.parts[0].tile; /* L */
    s_hud_pu_tile[4] = ui_powerup_frame_4.parts[0].tile; /* O */
    s_hud_pu_pal = ui_powerup_frame_0.parts[0].pal;

    s_hud_life_tile = ui_lifebar_frame_0.parts[0].tile;
    s_hud_life_pal = ui_lifebar_frame_0.parts[0].pal;

    s_hud_spr_ready = 1;
}

static void hud_sprites_hide(void)
{
    u8 i;
    for (i = 0; i < HUD_SCORE_DIGITS; i++) {
        ngpc_sprite_hide((u8)(SPR_HUD_SCORE_BASE + i));
    }
    ngpc_sprite_hide((u8)SPR_HUD_PU_SPR);
    if (s_start_timer == 0u) {
        for (i = 0; i < HUD_LIFE_SLOTS; i++) {
            ngpc_sprite_hide((u8)(SPR_HUD_LIFE_BASE + i));
        }
    }
    s_hud_spr_visible = 0;
}

static void hud_sprites_show(void)
{
    u8 i;
    static const u8 k_life_x[HUD_LIFE_SLOTS] = { 5u, 11u, 17u };
    hud_sprites_init();

    for (i = 0; i < HUD_SCORE_DIGITS; i++) {
        s16 x = (s16)(HUD_SCORE_X + (s16)(i * 8u));
        ngpc_sprite_set(
            (u8)(SPR_HUD_SCORE_BASE + i),
            (u8)x,
            (u8)HUD_SCORE_Y,
            s_hud_digit_tile[0],
            s_hud_digit_pal,
            (u8)SPR_FRONT);
    }
    ngpc_sprite_hide((u8)SPR_HUD_PU_SPR);
    for (i = 0; i < HUD_LIFE_SLOTS; i++) {
        ngpc_sprite_set(
            (u8)(SPR_HUD_LIFE_BASE + i),
            k_life_x[i],
            (u8)HUD_LIFE_Y,
            s_hud_life_tile,
            s_hud_life_pal,
            (u8)SPR_FRONT);
    }

    s_hud_spr_visible = 1;
    s_hud_score = 0xFFFFu; /* force redraw */
    s_hud_lives = 0xFFu;   /* force redraw */
    s_hud_pu = 0xFFu;      /* force redraw */
}

static void hud_update(void)
{
    u8 i;

    /* During START we hide the HUD (sprite slot overlap with START/pickups). */
    if (s_start_timer != 0u) {
        hud_sprites_hide();
        return;
    }

    /* During GAME OVER we must not touch these sprite slots (shared with GAME OVER banner). */
    if (s_game_over) {
        return;
    }

    if (!s_hud_spr_visible) {
        hud_sprites_show();
    }

    /* ---- Score: 5 digits in the HUD box ---- */
    if (s_hud_score != s_score) {
        u16 v = s_score;
        u8 digs[HUD_SCORE_DIGITS];
        for (i = 0; i < HUD_SCORE_DIGITS; i++) {
            digs[(HUD_SCORE_DIGITS - 1u) - i] = (u8)(v % 10u);
            v = (u16)(v / 10u);
        }
        for (i = 0; i < HUD_SCORE_DIGITS; i++) {
            ngpc_sprite_set_tile((u8)(SPR_HUD_SCORE_BASE + i), s_hud_digit_tile[digs[i]]);
        }
        s_hud_score = s_score;
    }

    /* ---- Lives: display up to 3 icons for the current HUD mockup ---- */
    {
        u8 lives_disp = s_lives;
        static const u8 k_life_x[HUD_LIFE_SLOTS] = { 5u, 11u, 17u };

        if (lives_disp > HUD_LIFE_SLOTS) lives_disp = HUD_LIFE_SLOTS;
        if (s_hud_lives != lives_disp) {
            for (i = 0; i < HUD_LIFE_SLOTS; i++) {
                if (i < lives_disp) {
                    ngpc_sprite_set(
                        (u8)(SPR_HUD_LIFE_BASE + i),
                        k_life_x[i],
                        (u8)HUD_LIFE_Y,
                        s_hud_life_tile,
                        s_hud_life_pal,
                        (u8)SPR_FRONT);
                } else {
                    ngpc_sprite_hide((u8)(SPR_HUD_LIFE_BASE + i));
                }
            }
            s_hud_lives = lives_disp;
        }
    }

    /* ---- Power-up meter cursor (single sprite) ---- */
    {
        static const u8 k_pu_x[5] = { 26u, 38u, 50u, 62u, 74u };
        u8 show_pu = (u8)(s_pu_cursor != 0u);

        if (show_pu && s_pu_deny_flash != 0u && (s_pu_deny_flash & 1u)) {
            show_pu = 0u;
        }

        if (!show_pu) {
            if (s_hud_pu != 0u) {
                ngpc_sprite_hide((u8)SPR_HUD_PU_SPR);
                s_hud_pu = 0u;
            }
        } else {
            u8 idx = (u8)(s_pu_cursor - 1u);
            if (idx > 4u) idx = 4u;
            if (s_hud_pu != s_pu_cursor) {
                ngpc_sprite_set(
                    (u8)SPR_HUD_PU_SPR,
                    k_pu_x[idx],
                    (u8)HUD_PU_Y,
                    s_hud_pu_tile[idx],
                    s_hud_pu_pal,
                    (u8)SPR_FRONT);
                s_hud_pu = s_pu_cursor;
            }
        }
    }
}

void shmup_init(void)
{
    /* RNG for spawns/particles, seeded from user timing. */
    ngpc_rng_seed();
    ngpc_qrandom_init();
    s_continues_left = shmup_profile_continue_setting_get();
    shmup_start_level(SHMUP_LEVEL_1, 0u, 0u);
}

void shmup_vblank(void)
{
#if NGP_ENABLE_DMA
    if (s_bg_dma_active) {
        ngpc_dma_stream_rearm_u8(&s_bg_dma_stream);
    }
#endif
}

u8 shmup_update(void)
{
    u8 i;
    u8 scroll_dx;

    s_frame++;
    if (s_player_inv > 0) s_player_inv--;
    scroll_dx = scroll_dx_apply_global_slowdown(s_scroll_speed);
    s_scroll_pos += (u32)scroll_dx;
    s_scroll_x = (u8)(s_scroll_x + scroll_dx);
    scroll_dx = bg_scroll_dx_from_gameplay_dx(scroll_dx);
    s_bg_scroll_x = (u8)(s_bg_scroll_x + scroll_dx);
    ngpc_gfx_scroll(GFX_SCR1, s_bg_scroll_x, 0);
#if NGP_ENABLE_DMA
    if (s_bg_dma_active && ((s_frame & BG_DMA_REBUILD_MASK) == 0u)) {
        bg_dma_build_table(s_bg_dma_table, s_bg_scroll_x, (u8)(s_frame * BG_DMA_PHASE_STEP));
    }
#endif

    if (s_start_timer > 0) {
        s_start_timer--;
        if (s_start_timer == 0) {
            ngpc_mspr_hide((u8)SPR_UI_START_BASE, ui_start_frame_0.count);
        }
    }

    s_player_speed_px = player_speed_step();
    if (s_pu_deny_flash > 0u) s_pu_deny_flash--;

    if (s_end_screen == (u8)END_SCREEN_CONTINUE) {
        if (ngpc_pad_pressed & (PAD_LEFT | PAD_RIGHT)) {
            s_continue_choice ^= 1u;
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
            end_screen_draw_continue();
        }
        if (ngpc_pad_pressed & PAD_A) {
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_SELECT);
#endif
            if (s_continue_choice == 0u && s_continues_left > 0u) {
                s_continues_left--;
                shmup_start_level(s_current_level, 1u, 1u);
                player_reset_upgrades();
            } else {
                end_screen_enter_final_gameover();
            }
        }
        if (ngpc_pad_pressed & PAD_B) {
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_SELECT);
#endif
            end_screen_enter_final_gameover();
        }
        return 0;
    }

    if (s_end_screen == (u8)END_SCREEN_FINAL_GAMEOVER) {
        if (ngpc_pad_pressed & (PAD_A | PAD_B)) {
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_SELECT);
#endif
            end_screen_enter_name_entry();
        }
        return 0;
    }

    if (s_end_screen == (u8)END_SCREEN_NAME_ENTRY) {
        if ((ngpc_pad_pressed & PAD_LEFT) && s_name_entry_pos > 0u) {
            s_name_entry_pos--;
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
            end_screen_draw_name_entry();
        } else if ((ngpc_pad_pressed & PAD_RIGHT) && s_name_entry_pos < 3u) {
            s_name_entry_pos++;
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
            end_screen_draw_name_entry();
        } else if (ngpc_pad_pressed & PAD_UP) {
            if (s_name_entry_pos < 3u) {
                s_name_entry[s_name_entry_pos] = name_entry_char_next(s_name_entry[s_name_entry_pos]);
#if NGP_ENABLE_SOUND
                Sfx_Play(SFX_MENU_MOVE);
#endif
                end_screen_draw_name_entry();
            }
        } else if (ngpc_pad_pressed & PAD_DOWN) {
            if (s_name_entry_pos < 3u) {
                s_name_entry[s_name_entry_pos] = name_entry_char_prev(s_name_entry[s_name_entry_pos]);
#if NGP_ENABLE_SOUND
                Sfx_Play(SFX_MENU_MOVE);
#endif
                end_screen_draw_name_entry();
            }
        }

        if (ngpc_pad_pressed & PAD_A) {
            if (s_name_entry_pos < 3u) {
                if (s_name_entry_pos < 2u) {
                    s_name_entry_pos++;
#if NGP_ENABLE_SOUND
                    Sfx_Play(SFX_MENU_MOVE);
#endif
                    end_screen_draw_name_entry();
                } else {
                    s_name_entry_pos = 3u;
#if NGP_ENABLE_SOUND
                    Sfx_Play(SFX_MENU_MOVE);
#endif
                    end_screen_draw_name_entry();
                }
            } else {
#if NGP_ENABLE_SOUND
                Sfx_Play(SFX_MENU_SELECT);
#endif
                shmup_profile_highscore_submit(s_name_entry, s_score);
                s_end_screen = (u8)END_SCREEN_NONE;
                return 1;
            }
        }

        if ((ngpc_pad_pressed & PAD_B) && s_name_entry_pos > 0u) {
            s_name_entry_pos--;
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
            end_screen_draw_name_entry();
        }
        return 0;
    }

    if (s_stage_clear) {
        if (ngpc_pad_pressed & PAD_A) {
            if (s_current_level < SHMUP_LEVEL_INF) {
                shmup_start_level((u8)(s_current_level + 1u), 1u, 0u);
                return 0;
            }
            end_screen_enter_name_entry();
        }
        return 0;
    }

    if (s_game_over) {
        if (!s_ui_game_over_visible) {
            if (s_game_over_delay > 0u) {
                s_game_over_delay--;
                if (s_game_over_delay == 0u) {
                    ui_show_game_over();
                }
            } else {
                ui_show_game_over();
            }
        }
        return 0;
    } else {
        /* Player movement. */
        if (ngpc_pad_held & PAD_UP) s_player_y -= (s16)s_player_speed_px;
        if (ngpc_pad_held & PAD_DOWN) s_player_y += (s16)s_player_speed_px;
        if (ngpc_pad_held & PAD_LEFT) s_player_x -= (s16)s_player_speed_px;
        if (ngpc_pad_held & PAD_RIGHT) s_player_x += (s16)s_player_speed_px;

        if (s_player_x < 0) s_player_x = 0;
        if (s_player_y < 0) s_player_y = 0;
        if (s_player_x > 160 - PLAYER_W) s_player_x = 160 - PLAYER_W;
        if (s_player_y > (PLAYFIELD_H_PX - PLAYER_H)) s_player_y = (PLAYFIELD_H_PX - PLAYER_H);

        /* Nemesis-style power-up: press B to activate current selection. */
        if (ngpc_pad_pressed & PAD_B) {
            pu_activate();
        }

        /* OPTION: exit to menu.
         * Debug helpers while OPTION is held:
         * - press UP    : spawn a capsule
         * - press RIGHT : jump to next level (1→2→3)
         */
#if NGP_ENABLE_DEBUG
        if (ngpc_pad_held & PAD_OPTION) {
            if ((ngpc_pad_pressed & PAD_UP) && s_frame >= STAGE_INTRO_NO_SPAWN_FRAMES) {
                pickup_spawn_at(168, (s16)(s_player_y + 4), (s8)-1);
                /* stay in game */
            } else if ((ngpc_pad_pressed & PAD_RIGHT) && s_current_level < SHMUP_LEVEL_INF) {
                shmup_start_level((u8)(s_current_level + 1u), 1u, 0u);
                return 0;
            }
        }
#endif

        if (ngpc_pad_pressed & PAD_OPTION) {
#if NGP_ENABLE_DEBUG
            if ((ngpc_pad_held & PAD_UP) || (ngpc_pad_held & PAD_RIGHT)) {
                /* debug combo handled above, don't pause */
            } else
#endif
            {
                return 2; /* pause requested — caller handles BGM/DMA */
            }
        }

        /* OPTION follows the player's movement with delay (Nemesis-style). */
        option_update();

        /* Fire (autofire on A held). */
        if (s_fire_cd > 0) s_fire_cd--;
        if ((ngpc_pad_held & PAD_A) && s_fire_cd == 0) {
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_TIR_VAISSEAU);
#endif
            bullet_spawn((s16)(s_player_x + 14), (s16)(s_player_y + 4));
            if (s_option_active) {
                const u8 pal = shmup_player_b_frame_1.parts[0].pal;
                /* Keep OPTION but limit its firing rate to reduce CPU+sprite pressure on real hardware. */
                if (s_option_fire_toggle == 0u && s_bullets_active <= (u8)(MAX_BULLETS - 2u)) {
                    bullet_spawn_basic_pal((s16)(s_option_x + 6), (s16)(s_option_y + 2), pal);
                }
                s_option_fire_toggle ^= 1u;
            }
            s_fire_cd = PLAYER_FIRE_INTERVAL_FRAMES;
        }

        /* Stage script (no enemies/asteroids during the first 5 seconds). */
        if (s_frame >= STAGE_INTRO_NO_SPAWN_FRAMES) {
            const StageEvt *e;
            if (!s_stage_started) {
                s_stage_started = 1;
            }

            e = stage_update(&s_stage, scroll_dx, s_enemies_active, (u8)(s_wave_remaining != 0));
            if (e) {
                switch (e->cmd) {
                case STG_WAVE:
                    stage_start_wave(e->a, e->b, e->c, e->d);
                    break;
                case STG_AST:
                    asteroid_spawn_scripted(e->a, e->b, (s8)e->c);
                    break;
                case STG_SET_SPEED:
                    s_speed_preset = e->a;
                    /* Keep scroll stable for now; stage delays are scroll-based. */
                    break;
                case STG_TEXT:
                    stage_print_text(e->d, e->a, e->b);
                    break;
                case STG_PU:
                    pickup_spawn_scripted(e->a, e->b, (s8)e->c);
                    break;
                default:
                    break;
                }
            }

            /* Infinite level procedural director. */
            if (s_current_level == SHMUP_LEVEL_INF) {
                shmup_inf_update();
            }

            if (s_spawn_timer > 0) s_spawn_timer--;

            /* Spawn current scripted wave gradually. */
            if (s_wave_remaining > 0 && s_spawn_timer == 0) {
                enemy_spawn();
                s_wave_remaining--;
                s_spawn_timer = s_wave_spawn_interval;
            }

            /* Boss spawns only on scripted levels (never on INF). */
            if (s_current_level != SHMUP_LEVEL_INF &&
                !s_stage.active &&
                !s_boss_intro_started &&
                s_wave_remaining == 0u &&
                s_enemies_active == 0u &&
                !s_fat.active &&
                s_asteroids_active == 0u) {
                boss1_spawn();
            }
        }

        /* Update power-up pickups. */
        if (s_pickups_active) {
            for (i = 0; i < MAX_PICKUPS; i++) {
                if (!s_pickups[i].active) continue;
                s_pickups[i].x += s_pickups[i].vx;
                if (s_pickups[i].x < -8) {
                    pickup_kill(i);
                    continue;
                }
            if (AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_pickups[i].x, s_pickups[i].y, 8, 8)) {
                pu_advance();
#if NGP_ENABLE_SOUND
                Sfx_Play(SFX_POWERUP);
#endif
                pickup_kill(i);
                continue;
            }
                ngpc_sprite_move(s_pickups[i].spr, (u8)s_pickups[i].x, (u8)s_pickups[i].y);
            }
        }

        /* Fat enemy (single 16x16, stops near right edge, shoots every second). */
        if (s_fat.active) {
            const NgpcMetasprite *def;
            u8 si;
            if (s_fat.vx != 0) {
                s_fat.x += motion_dx_apply_global_slowdown(s_fat.vx);
                if (s_fat.x <= FAT_STOP_X) {
                    s_fat.x = FAT_STOP_X;
                    s_fat.vx = 0;
                }
            }

            if (s_fat.fire_cd > 0) s_fat.fire_cd--;
            else {
                ebullet_spawn((s16)(s_fat.x - 4), (s16)(s_fat.y + 4), (s8)-3);
                s_fat.fire_cd = FAT_FIRE_INTERVAL_FR;
            }

            def = &shmup_enemy5_fat_frame_0;
            for (si = 0; si < def->count; si++) {
                const MsprPart *p = &def->parts[si];
                s16 px = (s16)(s_fat.x + (s16)p->ox);
                s16 py = (s16)(s_fat.y + (s16)p->oy);
                ngpc_sprite_move((u8)(SPR_FAT_BASE + si), (u8)px, (u8)py);
            }

            if (s_player_inv == 0u &&
                AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_fat.x, s_fat.y, 16, 16)) {
                fx_spawn_explosion(s_fat.x, s_fat.y);
                fat_kill();
                player_damage();
            }
        }

        if (s_boss1.active) {
            const NgpcMetasprite *def = boss_def_for_level(s_current_level);
            u8 si;
            u8 fast_phase = (u8)(s_boss1.hp <= ((s_current_level >= SHMUP_LEVEL_2) ? (BOSS2_HP_MAX / 2u) : (BOSS1_HP_MAX / 2u)));

            if (s_boss1.state == BOSS1_STATE_ENTRY) {
                if (s_boss1.vx != 0) {
                    s_boss1.x += motion_dx_apply_global_slowdown(s_boss1.vx);
                    if (s_boss1.x <= ((s_current_level >= SHMUP_LEVEL_2) ? BOSS2_STOP_X : BOSS1_STOP_X)) {
                        s_boss1.x = (s_current_level >= SHMUP_LEVEL_2) ? BOSS2_STOP_X : BOSS1_STOP_X;
                        s_boss1.vx = 0;
                        s_boss1.state = BOSS1_STATE_FIGHT;
                        if (s_current_level >= SHMUP_LEVEL_2) {
                            s_boss1.aim_cd = 12u;
                            s_boss1.fan_cd = 28u;
                            s_boss1.wall_cd = 20u;
                        } else {
                            s_boss1.aim_cd = 18u;
                            s_boss1.fan_cd = 34u;
                            s_boss1.wall_cd = 52u;
                        }
                    }
                }
            } else if (s_boss1.state == BOSS1_STATE_FIGHT) {
                s_scroll_speed = 0u;
                if (s_boss1.hit_flash > 0u) s_boss1.hit_flash--;

                if (s_boss1.aim_cd > 0u) s_boss1.aim_cd--;
                if (s_boss1.fan_cd > 0u) s_boss1.fan_cd--;
                if (s_boss1.wall_cd > 0u) s_boss1.wall_cd--;

                if (s_current_level >= SHMUP_LEVEL_2) {
                    if (((s_frame & 1u) == 0u) || fast_phase) {
                        s_boss1.y += s_boss1.move_dir ? (fast_phase ? 2 : 1) : (fast_phase ? -2 : -1);
                        if (s_boss1.y <= BOSS2_MOVE_TOP) {
                            s_boss1.y = BOSS2_MOVE_TOP;
                            s_boss1.move_dir = 1u;
                        } else if (s_boss1.y >= BOSS2_MOVE_BOTTOM) {
                            s_boss1.y = BOSS2_MOVE_BOTTOM;
                            s_boss1.move_dir = 0u;
                        }
                    }

                    if (((s_frame & 3u) == 0u) || fast_phase) {
                        s_boss1.x += s_boss1.move_timer ? 1 : -1;
                        if (s_boss1.x <= BOSS2_MOVE_LEFT) {
                            s_boss1.x = BOSS2_MOVE_LEFT;
                            s_boss1.move_timer = 1u;
                        } else if (s_boss1.x >= BOSS2_MOVE_RIGHT) {
                            s_boss1.x = BOSS2_MOVE_RIGHT;
                            s_boss1.move_timer = 0u;
                        }
                    }

                    if (s_boss1.fan_cd == 0u && s_ebullets_active == 0u) {
                        boss2_fire_cross();
                        s_boss1.fan_cd = fast_phase ? 26u : BOSS2_FIRE_CROSS_FR;
                    } else if (s_boss1.wall_cd == 0u && s_ebullets_active <= 1u) {
                        boss2_fire_sweep();
                        s_boss1.wall_cd = fast_phase ? 24u : BOSS2_FIRE_SWEEP_FR;
                    } else if (s_boss1.aim_cd == 0u && s_ebullets_active <= 2u) {
                        boss2_fire_lances();
                        s_boss1.aim_cd = fast_phase ? 16u : BOSS2_FIRE_LANCE_FR;
                    }
                } else {
                    if (((s_frame & 1u) == 0u) || fast_phase) {
                        s_boss1.y += s_boss1.move_dir ? (fast_phase ? 2 : 1) : (fast_phase ? -2 : -1);
                        if (s_boss1.y <= BOSS1_MOVE_TOP) {
                            s_boss1.y = BOSS1_MOVE_TOP;
                            s_boss1.move_dir = 1u;
                        } else if (s_boss1.y >= BOSS1_MOVE_BOTTOM) {
                            s_boss1.y = BOSS1_MOVE_BOTTOM;
                            s_boss1.move_dir = 0u;
                        }
                    }

                    if (s_boss1.attack_step == 0u) {
                        if (s_boss1.fan_cd == 0u && s_ebullets_active <= 1u) {
                            boss1_fire_fan();
                            s_boss1.fan_cd = fast_phase ? 30u : BOSS1_FIRE_FAN_FR;
                            s_boss1.attack_step = 1u;
                        }
                    } else if (s_boss1.wall_cd == 0u) {
                        if (s_ebullets_active <= 2u) {
                            boss1_fire_wall();
                            s_boss1.wall_cd = fast_phase ? 44u : BOSS1_FIRE_WALL_FR;
                            s_boss1.attack_step = 0u;
                        }
                    } else if (s_boss1.aim_cd == 0u && s_ebullets_active <= (u8)(MAX_EBULLETS - 3u)) {
                        boss1_fire_aimed();
                        s_boss1.aim_cd = fast_phase ? 18u : BOSS1_FIRE_AIM_FR;
                    }

                    if (s_boss1.attack_step == 0u && s_boss1.fan_cd != 0u &&
                        s_boss1.aim_cd == 0u && s_ebullets_active <= (u8)(MAX_EBULLETS - 3u)) {
                        boss1_fire_aimed();
                        s_boss1.aim_cd = fast_phase ? 18u : BOSS1_FIRE_AIM_FR;
                    }
                }
            } else {
                if (s_boss1.death_timer > 0u) {
                    if ((s_boss1.death_timer & 7u) == 0u) {
                        s16 ex = (s16)(s_boss1.x + 4 + ((s16)(s_boss1.death_timer & 0x0Fu)));
                        s16 ey = (s16)(s_boss1.y + 4 + ((s16)((s_boss1.death_timer >> 1) & 0x0Fu)));
                        fx_spawn_explosion(ex, ey);
                    }
                    s_boss1.death_timer--;
                }
                if (s_boss1.death_timer == 0u) {
                    s_score += 200;
                    stage_clear_enter();
                    return 0;
                }
            }

            for (si = 0; si < def->count; si++) {
                const MsprPart *p = &def->parts[si];
                s16 px = (s16)(s_boss1.x + (s16)p->ox);
                s16 py = (s16)(s_boss1.y + (s16)p->oy);
                if (s_boss1.hit_flash != 0u && (s_boss1.hit_flash & 1u)) {
                    ngpc_sprite_hide((u8)(SPR_BOSS_BASE + si));
                } else {
                    ngpc_sprite_set((u8)(SPR_BOSS_BASE + si), (u8)px, (u8)py, p->tile, p->pal, (u8)(SPR_FRONT | (u8)p->flags));
                }
            }

            if (s_boss1.state != BOSS1_STATE_DYING &&
                s_player_inv == 0u &&
                AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_boss1.x, s_boss1.y,
                     boss_hit_w_for_level(s_current_level), boss_hit_h_for_level(s_current_level))) {
                player_damage();
            }
        }

        /* Enemy bullets (straight). */
        if (s_ebullets_active) {
            for (i = 0; i < MAX_EBULLETS; i++) {
                if (!s_ebullets[i].active) continue;
                s_ebullets[i].x += motion_dx_apply_global_slowdown(s_ebullets[i].vx);
                s_ebullets[i].y += s_ebullets[i].vy;
                if (s_ebullets[i].x < -8 || s_ebullets[i].x > 168 || s_ebullets[i].y < -8 || s_ebullets[i].y > PLAYFIELD_H_PX) {
                    ebullet_kill(i);
                    continue;
                }
                if (s_player_inv == 0u &&
                    AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_ebullets[i].x, s_ebullets[i].y, 8, 8)) {
                    ebullet_kill(i);
                    player_damage();
                    continue;
                }
                ngpc_sprite_move(s_ebullets[i].spr, (u8)s_ebullets[i].x, (u8)s_ebullets[i].y);
            }
        }

        /* Update enemies (movement + player collisions). */
        for (i = 0; i < MAX_ENEMIES; i++) {
            s16 y;
            if (!s_enemies[i].active) continue;

            if (s_enemies[i].type == 3u) {
                if (s_enemies[i].phase == 0u) {
                    /* Incoming (left). */
                    s_enemies[i].x += motion_dx_apply_global_slowdown(s_enemies[i].vx);
                    s_enemies[i].y = s_enemies[i].base_y;
                    if (s_enemies[i].x <= ENEMY3_TURN_TRIGGER_X) {
                        s16 target;
                        u8 shift_tiles = (u8)((s_enemies[i].spr & 1u) ? ENEMY3_SHIFT_TILE_MAX : ENEMY3_SHIFT_TILE_MIN);
                        s16 shift_px = (s16)(shift_tiles * 8);
                        /* Pick direction based on vertical position. */
                        s_enemies[i].step_dir = (s8)((s_enemies[i].y < (PLAYFIELD_H_PX / 2)) ? 1 : -1);
                        target = (s16)(s_enemies[i].y + (s16)(s_enemies[i].step_dir * shift_px));
                        if (target < 8) target = 8;
                        if (target > PLAYFIELD_H_PX - 8) target = PLAYFIELD_H_PX - 8;

                        s_enemies[i].phase = 1u; /* turning */
                        s_enemies[i].step_timer = (u8)ENEMY3_TURN_FRAMES;
                        s_enemies[i].turn_x0 = s_enemies[i].x;
                        s_enemies[i].turn_y0 = s_enemies[i].y;
                        s_enemies[i].turn_y1 = target;
                        /* Keep facing left during the arc; flip at the end. */
                        ngpc_sprite_set_flags(s_enemies[i].spr, (u8)SPR_FRONT);
                    }
                } else if (s_enemies[i].phase == 1u) {
                    /* Turning in an arc (semi-circle-ish + vertical drift). */
                    u8 t = (u8)(ENEMY3_TURN_FRAMES - s_enemies[i].step_timer);
                    u8 a = s_enemy3_turn_ang[t]; /* 0..128 */
                    u8 lerp = s_enemy3_turn_lerp[t]; /* 0..255 */
                    s8 sin_a = ngpc_sin(a);
                    s8 cos_a = ngpc_sin((u8)(a + 64u));
                    s16 x_center = (s16)(s_enemies[i].turn_x0 + ENEMY3_TURN_RADIUS_PX);
                    s16 dx = (s16)(((s16)ENEMY3_TURN_RADIUS_PX * (s16)cos_a) >> 7);
                    s16 dy = (s16)(s_enemies[i].turn_y1 - s_enemies[i].turn_y0);
                    s16 y_lin = (s16)(s_enemies[i].turn_y0 + (s16)(((dy * (s16)lerp)) >> 8));
                    s16 bump = (s16)(((s16)ENEMY3_ARC_BUMP_PX * (s16)sin_a) >> 7);

                    s_enemies[i].x = (s16)(x_center - dx);
                    s_enemies[i].y = (s16)(y_lin + (s16)(s_enemies[i].step_dir * bump));
                    if (s_enemies[i].y < 8) s_enemies[i].y = 8;
                    if (s_enemies[i].y > PLAYFIELD_H_PX - 8) s_enemies[i].y = PLAYFIELD_H_PX - 8;

                    if (s_enemies[i].step_timer > 0) {
                        s_enemies[i].step_timer--;
                        if (s_enemies[i].step_timer == 0) {
                            const MsprPart *p = &shmup_enemy3_frame_0.parts[0];
                            s_enemies[i].phase = 2u; /* outgoing */
                            s_enemies[i].vx = enemy_vx_apply_preset(3u, ENEMY3_VX_OUT);
                            s_enemies[i].base_y = s_enemies[i].turn_y1;
                            s_enemies[i].y = s_enemies[i].base_y;
                            ngpc_sprite_set_flags(s_enemies[i].spr, (u8)(SPR_FRONT | SPR_HFLIP));
                            ngpc_sprite_set_tile(s_enemies[i].spr, p->tile);
                        }
                    }
                } else {
                    /* Outgoing (right). */
                    s_enemies[i].x += motion_dx_apply_global_slowdown(s_enemies[i].vx);
                    s_enemies[i].y = s_enemies[i].base_y;
                    if (s_enemies[i].x > 170) {
                        wave_state_mark_missed(s_enemies[i].wave_id);
                        enemy_kill(i);
                        continue;
                    }
                }

                if (s_enemies[i].active) {
                    ngpc_sprite_move(s_enemies[i].spr, (u8)s_enemies[i].x, (u8)s_enemies[i].y);
                }

                if (s_enemies[i].active && s_player_inv == 0u &&
                    AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_enemies[i].x, s_enemies[i].y, 8, 8)) {
                    enemy_kill(i);
                    player_damage();
                }
                continue;
            }

            if (s_enemies[i].type == 4u) {
                if (s_enemies[i].step_timer > 0u) {
                    s_enemies[i].step_timer--;
                } else {
                    s_enemies[i].step_timer = ENEMY4_MOVE_STEP_FR;
                    s_enemies[i].y += (s16)s_enemies[i].step_dir;
                    if (s_enemies[i].y <= 8) {
                        s_enemies[i].y = 8;
                        s_enemies[i].step_dir = 1;
                    } else if (s_enemies[i].y >= PLAYFIELD_H_PX - 8) {
                        s_enemies[i].y = PLAYFIELD_H_PX - 8;
                        s_enemies[i].step_dir = -1;
                    }
                }

                if (s_enemies[i].fire_cd > 0u) {
                    s_enemies[i].fire_cd--;
                } else if (s_ebullets_active < MAX_EBULLETS) {
                    ebullet_spawn_xy((s16)(s_enemies[i].x - 4), (s16)(s_enemies[i].y + 2), -2, 0);
                    s_enemies[i].fire_cd = ENEMY4_FIRE_INTERVAL_FR;
                }

                ngpc_sprite_move(s_enemies[i].spr, (u8)s_enemies[i].x, (u8)s_enemies[i].y);

                if (s_player_inv == 0u &&
                    AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_enemies[i].x, s_enemies[i].y, 8, 8)) {
                    enemy_kill(i);
                    player_damage();
                }
                continue;
            }

            s_enemies[i].x += motion_dx_apply_global_slowdown(s_enemies[i].vx);
            if (s_enemies[i].x < -7) {
                wave_state_mark_missed(s_enemies[i].wave_id);
                enemy_kill(i);
            } else {
                if (s_enemies[i].type == 1) {
                    if (s_enemies[i].step_timer > 0) {
                        s_enemies[i].step_timer--;
                    }

                    if (s_enemies[i].phase) {
                        /* Stepping segment: 1px per frame. */
                        s_enemies[i].y += (s16)s_enemies[i].step_dir;
                        if (s_enemies[i].y < 8) {
                            s_enemies[i].y = 8;
                            s_enemies[i].step_dir = 1;
                        } else if (s_enemies[i].y > PLAYFIELD_H_PX - 8) {
                            s_enemies[i].y = PLAYFIELD_H_PX - 8;
                            s_enemies[i].step_dir = -1;
                        }
                    }

                    if (s_enemies[i].step_timer == 0) {
                        if (s_enemies[i].phase) {
                            /* Back to flat; flip direction so the next step goes the other way. */
                            s_enemies[i].phase = 0;
                            s_enemies[i].step_timer = ENEMY1_FLAT_FRAMES;
                            s_enemies[i].step_dir = (s8)(-s_enemies[i].step_dir);
                        } else {
                            /* Start stepping. */
                            s_enemies[i].phase = 1;
                            s_enemies[i].step_timer = ENEMY1_STEP_FRAMES;
                        }
                    }

                    y = s_enemies[i].y;
                } else {
                    if (s_enemies[i].type == 2) {
                        /* Wave: sine around base_y, phased per spawn index. */
                        s8 s = ngpc_sin((u8)((s_frame << 1) + s_enemies[i].move_phase));
                        y = (s16)(s_enemies[i].base_y + ((s16)s * 12 >> 7));
                        if (y < 8) y = 8;
                        if (y > PLAYFIELD_H_PX - 8) y = PLAYFIELD_H_PX - 8;
                        s_enemies[i].y = y;
                    } else {
                        /* Type 3 is handled earlier (continue). */
                        s_enemies[i].y = s_enemies[i].base_y;
                    }
                }
                ngpc_sprite_move(s_enemies[i].spr, (u8)s_enemies[i].x, (u8)s_enemies[i].y);

                if (s_player_inv == 0u &&
                    AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_enemies[i].x, s_enemies[i].y, 8, 8)) {
                    enemy_kill(i);
                    player_damage();
                }
            }
        }

        /* Update asteroids (non-destructible obstacles) + player collisions. */
        for (i = 0; i < MAX_ASTEROIDS; i++) {
            const NgpcMetasprite *def;
            u8 si;
            u8 aw = 16, ah = 16;
            if (!s_asteroids[i].active) continue;
            s_asteroids[i].x += motion_dx_apply_global_slowdown(s_asteroids[i].vx);
            if (s_asteroids[i].x < -32) {
                asteroid_kill(i);
                continue;
            }
            switch (s_asteroids[i].type) {
            default:
            case 1: def = &shmup_ast1_frame_0; break;
            case 2: def = &shmup_ast2_frame_0; break;
            case 3: def = &shmup_ast3_frame_0; break;
            case 4: def = &shmup_ast4_frame_0; break;
            case 5: def = &shmup_ast5_frame_0; break;
            }
            for (si = 0; si < def->count; si++) {
                const MsprPart *p = &def->parts[si];
                s16 px = s_asteroids[i].x + (s16)p->ox;
                s16 py = s_asteroids[i].y + (s16)p->oy;
                ngpc_sprite_move(
                    (u8)(s_asteroids[i].spr_base + si),
                    (u8)((u16)px & 0xFF),
                    (u8)((u16)py & 0xFF));
            }
            if (s_asteroids[i].type == 3) { aw = 8; ah = 16; }
            else if (s_asteroids[i].type == 4) { aw = 16; ah = 8; }
            else if (s_asteroids[i].type == 5) { aw = 8; ah = 8; }
            if (s_player_inv == 0u &&
                AABB(s_player_x, s_player_y, PLAYER_W, PLAYER_H, s_asteroids[i].x, s_asteroids[i].y, aw, ah)) {
                player_damage();
            }
        }

        /* Update bullets (movement + collisions). */
        if (s_bullets_active) {
            for (i = 0; i < MAX_BULLETS; i++) {
                u8 j;
                s16 bx, by;
                if (!s_bullets[i].active) continue;

                bx = (s16)(s_bullets[i].x + 4);
                by = s_bullets[i].y;
                s_bullets[i].x = bx;
                if (bx > 170) {
                    bullet_kill(i);
                    continue;
                }

                if (s_boss1.active &&
                    s_boss1.state == BOSS1_STATE_FIGHT &&
                    AABB(bx, by, 8, 8, s_boss1.x, s_boss1.y,
                         boss_hit_w_for_level(s_current_level), boss_hit_h_for_level(s_current_level))) {
                    u8 damage = s_bullets[i].damage;
                    bullet_kill(i);
                    fx_spawn_explosion(bx, by);
                    if (damage > s_boss1.hp) s_boss1.hp = 0u;
                    else s_boss1.hp = (u8)(s_boss1.hp - damage);
                    s_boss1.hit_flash = 4u;
                    if (s_boss1.hp == 0u) {
                        boss1_start_dying();
                    }
                    continue;
                }

                /* Fat enemy (3 hits). */
                if (s_fat.active && AABB(bx, by, 8, 8, s_fat.x, s_fat.y, 16, 16)) {
                    u8 damage = s_bullets[i].damage;
                    bullet_kill(i);
                    if (damage > s_fat.hp) s_fat.hp = 0u;
                    else s_fat.hp = (u8)(s_fat.hp - damage);
                    if (s_fat.hp == 0u) {
                        s_score += 30;
                        explosion_sfx_play_once();
                        fx_spawn_explosion(s_fat.x, s_fat.y);
                        fat_kill();
                    }
                    continue;
                }

                if (s_enemies_active) {
                    for (j = 0; j < MAX_ENEMIES; j++) {
                        if (!s_enemies[j].active) continue;
                        if (HIT_8_8(bx, by, s_enemies[j].x, s_enemies[j].y)) {
                            u8 wave_id = s_enemies[j].wave_id;
                            s16 ex = s_enemies[j].x;
                            s16 ey = s_enemies[j].y;
                            if (s_enemies[j].hp > 1u) {
                                s_enemies[j].hp--;
                                fx_spawn_explosion(bx, by);
                            } else {
                                enemy_kill(j);
                                s_score += (s_enemies[j].type == 4u) ? 20u : 10u;
                                explosion_sfx_play_once();
                                fx_spawn_explosion(ex, ey);
                                wave_try_award_capsule(wave_id, ex, ey);
                            }
                            if (!s_bullets[i].pierce) {
                                bullet_kill(i);
                            }
                            break;
                        }
                    }
                }

                if (s_bullets[i].active && s_asteroids_active) {
                    for (j = 0; j < MAX_ASTEROIDS; j++) {
                        u8 aw = 16, ah = 16;
                        if (!s_asteroids[j].active) continue;
                        if (s_asteroids[j].type == 3) { aw = 8; ah = 16; }
                        else if (s_asteroids[j].type == 4) { aw = 16; ah = 8; }
                        else if (s_asteroids[j].type == 5) { aw = 8; ah = 8; }
                        if (AABB(bx, by, 8, 8, s_asteroids[j].x, s_asteroids[j].y, aw, ah)) {
                            bullet_kill(i);
                            fx_spawn_explosion(bx, by);
                            break;
                        }
                    }
                }

                if (s_bullets[i].active) {
                    ngpc_sprite_move(s_bullets[i].spr, (u8)bx, (u8)by);
                }
            }
        }
    }

    /* ---- Draw ---- */

    /* Explosions FX (single sprite each). */
    for (i = 0; i < MAX_FX; i++) {
        const NgpcMetasprite *def;
        const MsprPart *p;
        u16 tile;
        if (!s_fx[i].active) continue;
        def = ngpc_mspr_anim_update(&s_fx[i].anim);
        p = &def->parts[0];
        tile = p->tile;
        if (s_fx[i].last_tile == 0xFFFFu) {
            ngpc_sprite_set(s_fx[i].spr, (u8)s_fx[i].x, (u8)s_fx[i].y, tile, p->pal, (u8)SPR_MIDDLE);
        } else if (s_fx[i].last_tile != tile) {
            ngpc_sprite_set_tile(s_fx[i].spr, tile);
        }
        s_fx[i].last_tile = tile;
        if (ngpc_mspr_anim_done(&s_fx[i].anim)) {
            s_fx[i].active = 0;
            s_fx[i].last_tile = 0xFFFFu;
            ngpc_sprite_hide(s_fx[i].spr);
        }
    }

    player_update_sprites();

    hud_update();
    return 0;
}
