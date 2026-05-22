/*
 * stage.h - Simple timeline stage scripting (deterministic spawns)
 *
 * Part of NGPC Template 2026 (MIT License)
 */

#ifndef STAGE_H
#define STAGE_H

#include "ngpc_types.h"

typedef enum {
    STG_END = 0,
    STG_WAVE = 1,       /* a=enemy_type, b=count, c=spawn_interval_frames, d=center_y_px */
    STG_WAIT_CLEAR = 2, /* a=max_enemies_active, delay=post-clear delay (frames) */
    STG_AST = 3,        /* a=ast_type (1..5, 0=random), b=y_px, c=vx (s8), delay=post-spawn delay */
    STG_SET_SPEED = 4,  /* a=speed_preset (0=normal,1=preboss slow), delay=post-set delay */
    STG_TEXT = 5,       /* a=x_tile, b=y_tile, c=len (<=15), d=text_id, delay=post-print delay */
    STG_PU = 6          /* a=kind (0=capsule,1=speed icon,2=shield icon), b=y_px, c=vx(s8, optional), delay=post-spawn delay */
} StageCmd;

typedef struct {
    u16 delay; /* frames to wait after executing this event */
    u8 cmd;
    u8 a, b, c, d;
} StageEvt;

typedef struct {
    const StageEvt *script;
    u16 wait;
    u16 index;
    u8 active;
} StagePlayer;

void stage_init(StagePlayer *p, const StageEvt *script);

/* Returns pointer to an event to execute now, or NULL if none.
 * Timing model: delay is measured in scroll units (pixels). Each frame the
 * caller provides scroll_dx (how many pixels the stage advanced this frame).
 * WAIT_CLEAR blocks until (enemies_active <= evt->a) and wave_spawning==0. */
const StageEvt *stage_update(StagePlayer *p, u8 scroll_dx, u8 enemies_active, u8 wave_spawning);

#endif /* STAGE_H */
