/*
 * stage.c - Simple timeline stage scripting (deterministic spawns)
 *
 * Part of NGPC Template 2026 (MIT License)
 */

#include "stage.h"

#define STAGE_WAVE_EXTRA_DELAY_PX 24u

void stage_init(StagePlayer *p, const StageEvt *script)
{
    p->script = script;
    p->wait = 0;
    p->index = 0;
    p->active = (script != 0) ? 1 : 0;
}

const StageEvt *stage_update(StagePlayer *p, u8 scroll_dx, u8 enemies_active, u8 wave_spawning)
{
    const StageEvt *e;

    if (!p->active || p->script == 0) {
        return 0;
    }

    if (p->wait > 0) {
        /* Delay is in scroll units (pixels). */
        if (scroll_dx >= p->wait) {
            p->wait = 0;
        } else {
            p->wait = (u16)(p->wait - scroll_dx);
        }
        return 0;
    }

    e = &p->script[p->index];

    if (e->cmd == (u8)STG_END) {
        p->active = 0;
        return 0;
    }

    if (e->cmd == (u8)STG_WAIT_CLEAR) {
        if (wave_spawning) {
            return 0;
        }
        if (enemies_active > e->a) {
            return 0;
        }
        p->index++;
        p->wait = e->delay;
        return 0;
    }

    p->index++;
    p->wait = e->delay;
    if (e->cmd == (u8)STG_WAVE) {
        /* Slightly relax the cadence between enemy waves. */
        p->wait = (u16)(p->wait + STAGE_WAVE_EXTRA_DELAY_PX);
    }
    return e;
}
