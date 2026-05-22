/*
 * main.c - NGPC Template 2026 - Hello World example
 *
 * Part of NGPC Template 2026 (MIT License)
 *
 * Displays text on screen and responds to button presses.
 * This serves as a starting point for your own NGPC project.
 */

/* ---- Engine modules ---- */
#include "ngpc_hw.h"
#include "carthdr.h"
#include "ngpc_sys.h"
#include "ngpc_gfx.h"
#include "ngpc_sprite.h"
#include "ngpc_text.h"
#include "ngpc_input.h"
#include "ngpc_timing.h"
#include "ngpc_tilemap_blit.h"
#include "ngpc_config.h"
#if NGP_ENABLE_DMA
#include "ngpc_dma.h"
#endif
#include "game/shmup.h"
#include "game/shmup_profile.h"
#include "ngpc_log.h"

/* ---- Assets (external data, not embedded in code) ---- */
#include "../GraphX/intro_ngpc_craft_png.h"
#include "../GraphX/menu_principal.h"
#include "../GraphX/menu_phase2.h"
#include "../GraphX/shmup_player_a_mspr.h"
#include "../GraphX/shmup_player_b_mspr.h"

#if NGP_ENABLE_SOUND
#include "sounds.h"
#include "sound_data.h"
#include "audio/sfx_ids.h"
#endif

/* ---- Game states ---- */

typedef enum {
    STATE_INTRO,
    STATE_MENU,
    STATE_OPTIONS,
    STATE_HIGHSCORES,
    STATE_GAME,
    STATE_PAUSE
} GameState;

static GameState s_state = STATE_INTRO;

/* ---- State: Intro screen ---- */

/* Tile slots 0-31 are reserved, 32-127 = system font (loaded by BIOS).
 * Start image tiles at slot 128 to avoid overwriting the font. */
#define INTRO_TILE_BASE 128u

static void intro_init(void)
{
    /* Ensure SCR1 is in front (game HUD uses SCR2). */
    HW_SCR_PRIO = 0x00;

    ngpc_gfx_scroll(GFX_SCR1, 0, 0);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_sprite_hide_all();
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    /* SAFE PATH (recommended):
     * Use macro-based blit that directly indexes ROM symbols (windjammer-style)
     * instead of passing pointers to generic helpers (near/far model hazard). */
    NGP_TILEMAP_BLIT_SCR1(intro_ngpc_craft_png, INTRO_TILE_BASE);
}

static void intro_update(void)
{
    if (ngpc_pad_pressed & (PAD_A | PAD_B | PAD_OPTION)) {
        s_state = STATE_MENU;
    }
}

/* ---- State: Main menu ---- */

#define MENU_TILE_BASE 128u
#define MENU_TEXT_PAL  15u

/* Cursor (player ship) uses 2 layers (A+B), 16x16, 8 sprites total.
 * We can't use the shmup tile bases (256+) because the menu tilemap is big and
 * would overwrite them, so we load the ship tiles at a safe high base. */
#define MENU_CURSOR_TILE_BASE_A 400u
#define MENU_CURSOR_TILE_BASE_B (MENU_CURSOR_TILE_BASE_A + 12u) /* 12 tiles per layer */
#define MENU_CURSOR_SPR_A_BASE 0u
#define MENU_CURSOR_SPR_B_BASE 4u

/* "Nose" alignment: use the shmup bullet origin (x+14,y+4) as a practical tip
 * reference. We align the nose around mid-height (y+8). */
#define MENU_CURSOR_NOSE_OFF_X 14
#define MENU_CURSOR_NOSE_OFF_Y 8

#define MENU_NOSE_START_X 52
#define MENU_NOSE_START_Y 71
#define MENU_NOSE_OPT_X   43
#define MENU_NOSE_OPT_Y   104
static u8 s_menu_sel = 0; /* 0=START, 1=OPTIONS */
static u8 s_options_sel = 0; /* 0=CONTINUES, 1=HIGH SCORES, 2=BACK */
static u8 s_menu_music_active = 0u;
/* Which state highscores should return to (STATE_MENU or STATE_OPTIONS). */
static GameState s_highscores_back = STATE_OPTIONS;

static void menu_text_pal_init(void)
{
    ngpc_gfx_set_palette(GFX_SCR1, MENU_TEXT_PAL,
        RGB(1, 1, 3),
        RGB(15, 13, 3),
        RGB(12, 9, 2),
        RGB(8, 6, 2)
    );
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

static void menu_cursor_set_nose(s16 nose_x, s16 nose_y)
{
    s16 x = (s16)(nose_x - MENU_CURSOR_NOSE_OFF_X);
    s16 y = (s16)(nose_y - MENU_CURSOR_NOSE_OFF_Y);
    u8 i;

    /* Use the NORMAL frame (frame_1): tiles 4..7 in each layer set. */
    for (i = 0; i < 4u; i++) {
        s16 ox = (s16)((i & 1u) ? 8 : 0);
        s16 oy = (s16)((i & 2u) ? 8 : 0);
        ngpc_sprite_move((u8)(MENU_CURSOR_SPR_A_BASE + i), (u8)(x + ox), (u8)(y + oy));
        ngpc_sprite_move((u8)(MENU_CURSOR_SPR_B_BASE + i), (u8)(x + ox), (u8)(y + oy));
    }
}

static void menu_main_bg_init(void)
{
    /* Ensure SCR1 is in front (menu auto-split labels SCR1 as front). */
    HW_SCR_PRIO = 0x00;

    ngpc_gfx_scroll(GFX_SCR1, 0, 0);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_sprite_hide_all();

    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    /* Load original main menu tilemap. */
    NGP_TILEMAP_BLIT_SPLIT(menu_principal, MENU_TILE_BASE);
}

static void menu_sub_bg_init(void)
{
    /* Ensure SCR1 is in front (menu auto-split labels SCR1 as front). */
    HW_SCR_PRIO = 0x00;

    ngpc_gfx_scroll(GFX_SCR1, 0, 0);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_sprite_hide_all();

    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    /* Load submenu tilemap (phase 2 planet + stars). */
    NGP_TILEMAP_BLIT_SPLIT(menu_phase2, MENU_TILE_BASE);
    menu_text_pal_init();
}

static void menu_cursor_init(void)
{
    u8 i;

    /* Load ship tiles at safe bases for the menu. */
    ngpc_gfx_load_tiles_at(shmup_player_a_tiles, shmup_player_a_tiles_count, MENU_CURSOR_TILE_BASE_A);
    ngpc_gfx_load_tiles_at(shmup_player_b_tiles, shmup_player_b_tiles_count, MENU_CURSOR_TILE_BASE_B);

    /* Load sprite palettes (same as shmup player). */
    load_sprite_palettes(shmup_player_a_palettes, shmup_player_a_pal_base, shmup_player_a_palette_count);
    load_sprite_palettes(shmup_player_b_palettes, shmup_player_b_pal_base, shmup_player_b_palette_count);

    /* Spawn 8 sprites (A then B) once. */
    for (i = 0; i < 4u; i++) {
        u16 tile_a = (u16)(MENU_CURSOR_TILE_BASE_A + 4u + i);
        u16 tile_b = (u16)(MENU_CURSOR_TILE_BASE_B + 4u + i);
        ngpc_sprite_set((u8)(MENU_CURSOR_SPR_A_BASE + i), 0, 0, tile_a, shmup_player_a_pal_base, (u8)SPR_FRONT);
        ngpc_sprite_set((u8)(MENU_CURSOR_SPR_B_BASE + i), 0, 0, tile_b, shmup_player_b_pal_base, (u8)SPR_FRONT);
    }
}

static void menu_init(void)
{
    menu_main_bg_init();
#if NGP_ENABLE_DEBUG
    ngpc_log_dump(GFX_SCR1, MENU_TEXT_PAL, 1, 13); /* show flash log from main menu too */
#endif

    /* Cursor selection default. */
    s_menu_sel = 0;
    menu_cursor_init();
    menu_cursor_set_nose(MENU_NOSE_START_X, MENU_NOSE_START_Y);

#if NGP_ENABLE_SOUND
    if (!s_menu_music_active) {
        Bgm_SetNoteTable(MENU_NOTE_TABLE);
        Bgm_StartLoop4Ex(
            MENU_BGM_CH0, MENU_BGM_CH0_LOOP,
            MENU_BGM_CH1, MENU_BGM_CH1_LOOP,
            MENU_BGM_CH2, MENU_BGM_CH2_LOOP,
            MENU_BGM_CHN, MENU_BGM_CHN_LOOP);
        s_menu_music_active = 1u;
    }
#endif
}

static void menu_update(void)
{
    if (ngpc_pad_pressed & PAD_A) {
#if NGP_ENABLE_SOUND
        Sfx_Play(SFX_MENU_SELECT);
#endif
        if (s_menu_sel == 0u) {
#if NGP_ENABLE_SOUND
            Bgm_Stop();
            s_menu_music_active = 0u;
#endif
            s_state = STATE_GAME;
        } else {
            s_state = STATE_OPTIONS;
        }
    }

    if (ngpc_pad_pressed & PAD_B) {
#if NGP_ENABLE_SOUND
        Bgm_Stop();
        s_menu_music_active = 0u;
#endif
        s_state = STATE_INTRO;
    }

    if (ngpc_pad_pressed & PAD_UP) {
        if (s_menu_sel > 0u) {
            s_menu_sel = 0u;
            menu_cursor_set_nose(MENU_NOSE_START_X, MENU_NOSE_START_Y);
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
        }
    } else if (ngpc_pad_pressed & PAD_DOWN) {
        if (s_menu_sel < 1u) {
            s_menu_sel = 1u;
            menu_cursor_set_nose(MENU_NOSE_OPT_X, MENU_NOSE_OPT_Y);
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
        }
    }
}

/* ---- State: Options ---- */

static void options_draw(void)
{
    u8 continue_setting = shmup_profile_continue_setting_get();

    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 6, 2,  "OPTIONS");
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 4, 6,  "CONTINUES");
    ngpc_text_print_num(GFX_SCR1, MENU_TEXT_PAL, 15, 6, continue_setting, 2);
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 4, 9,  "HIGH SCORES");
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 8, 12, "BACK");
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 2, 15, "LEFT/RIGHT CHANGE");
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 2, 16, "A=SELECT  B=RETURN");

    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 2, 6,  (s_options_sel == 0u) ? ">" : " ");
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 2, 9,  (s_options_sel == 1u) ? ">" : " ");
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 6, 12, (s_options_sel == 2u) ? ">" : " ");
}

static void options_init(void)
{
    menu_sub_bg_init();
    s_options_sel = 0;
    options_draw();
#if NGP_ENABLE_DEBUG
    ngpc_log_dump(GFX_SCR1, MENU_TEXT_PAL, 1, 13);
#endif
}

static void options_leave(GameState next_state)
{
    shmup_profile_flush();
    s_state = next_state;
}

static void options_update(void)
{
    if (ngpc_pad_pressed & PAD_UP) {
        if (s_options_sel > 0u) {
            s_options_sel--;
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
            options_draw();
        }
    } else if (ngpc_pad_pressed & PAD_DOWN) {
        if (s_options_sel < 2u) {
            s_options_sel++;
#if NGP_ENABLE_SOUND
            Sfx_Play(SFX_MENU_MOVE);
#endif
            options_draw();
        }
    }

    if (s_options_sel == 0u) {
        u8 continue_setting = shmup_profile_continue_setting_get();

        if (ngpc_pad_pressed & PAD_LEFT) {
            if (continue_setting > 0u) {
                shmup_profile_continue_setting_set((u8)(continue_setting - 1u));
#if NGP_ENABLE_SOUND
                Sfx_Play(SFX_MENU_MOVE);
#endif
                options_draw();
            }
        } else if (ngpc_pad_pressed & PAD_RIGHT) {
            if (continue_setting < 10u) {
                shmup_profile_continue_setting_set((u8)(continue_setting + 1u));
#if NGP_ENABLE_SOUND
                Sfx_Play(SFX_MENU_MOVE);
#endif
                options_draw();
            }
        }
    }

    if (ngpc_pad_pressed & PAD_A) {
#if NGP_ENABLE_SOUND
        Sfx_Play(SFX_MENU_SELECT);
#endif
        if (s_options_sel == 1u) {
            s_highscores_back = STATE_OPTIONS;
            options_leave(STATE_HIGHSCORES);
        } else if (s_options_sel == 2u) {
            options_leave(STATE_MENU);
        }
    }

    if (ngpc_pad_pressed & PAD_B) {
#if NGP_ENABLE_SOUND
        Sfx_Play(SFX_MENU_SELECT);
#endif
        options_leave(STATE_MENU);
    }
}

/* ---- State: High Scores ---- */

static void highscores_init(void)
{
    ShmupHighScore entry;
    u8 i;

    menu_sub_bg_init();
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 4, 2,  "HIGH SCORES");
    for (i = 0; i < SHMUP_HIGHSCORE_COUNT; i++) {
        char rank[4];

        shmup_profile_highscore_get(i, &entry);
        rank[0] = (char)((i == 9u) ? '1' : ' ');
        rank[1] = (char)('0' + (char)((i + 1u) % 10u));
        rank[2] = '.';
        rank[3] = '\0';

        ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 2, (u8)(4u + i), rank);
        ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 6, (u8)(4u + i), entry.name);
        ngpc_text_print_num(GFX_SCR1, MENU_TEXT_PAL, 12, (u8)(4u + i), entry.score, 5);
    }
    ngpc_text_print(GFX_SCR1, MENU_TEXT_PAL, 5, 15, "A/B=RETURN");
#if NGP_ENABLE_DEBUG
    ngpc_log_dump(GFX_SCR1, MENU_TEXT_PAL, 1, 13);
#endif
}

static void highscores_update(void)
{
    if (ngpc_pad_pressed & (PAD_A | PAD_B)) {
#if NGP_ENABLE_SOUND
        Sfx_Play(SFX_MENU_SELECT);
#endif
        s_state = s_highscores_back;
    }
}

/* ---- State: Game (demo) ---- */

static void game_init(void)
{
    shmup_init();
}

static void game_update(void)
{
    u8 ret = shmup_update();
    if (ret == 1u) {
        s_state = STATE_MENU;
    } else if (ret == 2u) {
        s_state = STATE_PAUSE;
    }
}

/* ---- State: Pause ---- */

static void pause_cleanup(void)
{
    /* Erase the 3 rows written by pause_init() from SCR2.
     * Called on PAUSE -> GAME transition before resuming. */
    u8 x;
    for (x = 0; x < 20u; x++) {
        ngpc_gfx_put_tile(GFX_SCR2, x,  8u, 0, 0);
        ngpc_gfx_put_tile(GFX_SCR2, x, 11u, 0, 0);
        ngpc_gfx_put_tile(GFX_SCR2, x, 13u, 0, 0);
    }
}

static void pause_init(void)
{
    /* Draw PAUSE overlay on SCR2 (the HUD plane — already in front, does not
     * scroll with the background).  SCR1 (background) is left untouched so
     * resuming the game restores the correct scrolling state without artefacts. */
    ngpc_gfx_set_palette(GFX_SCR2, MENU_TEXT_PAL,
        RGB(1,  1,  3),
        RGB(15, 13, 3),
        RGB(12, 9,  2),
        RGB(8,  6,  2)
    );
    ngpc_text_print(GFX_SCR2, MENU_TEXT_PAL, 7,  8, "  PAUSE  ");
    ngpc_text_print(GFX_SCR2, MENU_TEXT_PAL, 3, 11, "A/B=RESUME");
    ngpc_text_print(GFX_SCR2, MENU_TEXT_PAL, 1, 13, "OPTION=MAIN MENU");
}

static void pause_update(void)
{
    if (ngpc_pad_pressed & (PAD_A | PAD_B)) {
        s_state = STATE_GAME; /* resume */
    } else if (ngpc_pad_pressed & PAD_OPTION) {
        shmup_abort();
        s_state = STATE_MENU;
    }
}

/* ---- Main entry point ---- */

void main(void)
{
    GameState prev_state;

    /* Initialize hardware. */
    ngpc_init();
    ngpc_load_sysfont();

#if NGP_ENABLE_DMA
    ngpc_dma_init();
#endif

#if NGP_ENABLE_SOUND
    Sounds_Init();
    Bgm_SetNoteTable(BGM_NIVEAU_1_NOTE_TABLE);
#endif

    shmup_profile_init();

    /* Enter title screen. */
    prev_state = STATE_GAME; /* Force init on first frame */

    /* Main loop (60 fps). */
    while (1) {
        ngpc_vsync();

        if (s_state == STATE_GAME || s_state == STATE_PAUSE) {
            shmup_vblank();
        }

        ngpc_input_update();

#if NGP_ENABLE_SOUND
        Sounds_Update();
#endif

        /* Mark sprite shadow as "in use" for this frame (if enabled).
         * If a frame ever overruns into the next VBlank on real hardware,
         * the VBlank ISR will skip flushing sprites rather than copying a
         * half-updated buffer. */
        ngpc_sprite_frame_begin();

        /* Detect state transitions and run init. */
        if (s_state != prev_state) {
            GameState from_state = prev_state;
            prev_state = s_state;
            switch (s_state) {
            case STATE_INTRO:       intro_init();       break;
            case STATE_MENU:        menu_init();        break;
            case STATE_OPTIONS:     options_init();     break;
            case STATE_HIGHSCORES:  highscores_init();  break;
            case STATE_GAME:
                /* Resume from pause: shmup state is preserved, skip re-init. */
                if (from_state != STATE_PAUSE) game_init();
                else pause_cleanup();
                break;
            case STATE_PAUSE:       pause_init();       break;
            }
        }

        /* Run current state update. */
        switch (s_state) {
        case STATE_INTRO:      intro_update();      break;
        case STATE_MENU:       menu_update();       break;
        case STATE_OPTIONS:    options_update();    break;
        case STATE_HIGHSCORES: highscores_update(); break;
        case STATE_GAME:       game_update();       break;
        case STATE_PAUSE:      pause_update();      break;
        }

        ngpc_sprite_frame_end();
    }
}
