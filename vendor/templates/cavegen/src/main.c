/*
 * main.c - Procgen Lab ROM (shell)
 *
 * Utilise le module ngpc_dungeongen pour la generation des salles.
 * Ce fichier ne contient que la navigation et la boucle principale.
 *
 * Controles:
 *   A       : cycle style exit pour la salle courante
 *   B       : salle suivante
 *   OPTION  : salle precedente
 *   D-pad   : scroll camera
 *
 * Config principale (injectables par le tool via -D) :
 *   Les parametres de generation sont dans ngpc_dungeongen.h (DUNGEONGEN_*).
 *   N_ROOMS  : nombre de salles navigables (reglable ici).
 *   SCROLL_STEP : vitesse scroll D-pad en pixels.
 */

#include "ngpc_hw.h"
#include "carthdr.h"
#include "ngpc_sys.h"
#include "ngpc_gfx.h"
#include "ngpc_input.h"
#include "ngpc_timing.h"

#include "ngpc_dungeongen/ngpc_dungeongen.h"

/* ---- Navigation (reglable par le tool) ---- */
#ifndef N_ROOMS
#define N_ROOMS     256u
#endif
#ifndef SCROLL_STEP
#define SCROLL_STEP   8    /* pixels par pression D-pad (= 1 NGPC tile) */
#endif

#define _PLANE  GFX_SCR1

/* ---- State navigation ---- */
static u16 s_room_idx;
static u8  s_style_idx;
static s16 s_cam_x;
static s16 s_cam_y;

/* ---- Camera ---- */
static void cam_apply(void)
{
    ngpc_gfx_scroll(_PLANE, (u8)s_cam_x, (u8)s_cam_y);
}

static void cam_update(void)
{
    u8 moved = 0u;
    if (ngpc_pad_held & PAD_RIGHT) {
        if (s_cam_x < ngpc_dgroom.scroll_max_x) { s_cam_x += SCROLL_STEP; moved = 1u; }
    }
    if (ngpc_pad_held & PAD_LEFT) {
        if (s_cam_x > 0) { s_cam_x -= SCROLL_STEP; moved = 1u; }
    }
    if (ngpc_pad_held & PAD_DOWN) {
        if (s_cam_y < ngpc_dgroom.scroll_max_y) { s_cam_y += SCROLL_STEP; moved = 1u; }
    }
    if (ngpc_pad_held & PAD_UP) {
        if (s_cam_y > 0) { s_cam_y -= SCROLL_STEP; moved = 1u; }
    }
    if (moved) { cam_apply(); }
}

/* ---- Enter room ---- */
static void enter_room(u16 idx)
{
    s_room_idx = idx;
    s_cam_x    = 0;
    s_cam_y    = 0;
    ngpc_dungeongen_enter(idx, 0xFFu);
    ngpc_dungeongen_spawn();
    cam_apply();
}

/* ---- Init ---- */
static void lab_init(void)
{
    ngpc_dungeongen_set_rtc_seed();

    ngpc_gfx_scroll(GFX_SCR1, 0, 0);
    ngpc_gfx_scroll(GFX_SCR2, 0, 0);
    ngpc_gfx_clear(GFX_SCR1);
    ngpc_gfx_clear(GFX_SCR2);
    ngpc_gfx_set_bg_color(RGB(0, 0, 0));

    ngpc_dungeongen_init();
    enter_room(0u);
}

/* ---- Update ---- */
static void lab_update(void)
{
    /* A : cycle style exit pour la salle courante */
    if (ngpc_pad_pressed & PAD_A) {
        s_style_idx = (u8)((ngpc_dgroom.style_idx + 1u) % ngpc_dungeongen_n_styles());
        s_cam_x = 0;
        s_cam_y = 0;
        ngpc_dungeongen_enter(s_room_idx, s_style_idx);
        ngpc_dungeongen_spawn();
        cam_apply();
    }

    /* B : salle suivante */
    if (ngpc_pad_pressed & PAD_B) {
        enter_room((u16)((s_room_idx + 1u) % N_ROOMS));
    }

    /* OPTION : salle precedente */
    if (ngpc_pad_pressed & PAD_OPTION) {
        enter_room((u16)((s_room_idx + N_ROOMS - 1u) % N_ROOMS));
    }

    /* D-pad : scroll camera */
    cam_update();

    /* Sync sprites -> positions ecran (chaque frame) */
    ngpc_dungeongen_sync_sprites((u8)s_cam_x, (u8)s_cam_y);
}

/* ---- Entry point ---- */
void main(void)
{
    ngpc_init();
    lab_init();

    while (1) {
        ngpc_vsync();
        ngpc_input_update();
        lab_update();
    }
}
