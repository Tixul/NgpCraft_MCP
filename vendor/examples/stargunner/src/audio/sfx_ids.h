/*
 * sfx_ids.h - Game SFX ids (NGPC Sound Creator project_sfx.c)
 *
 * These ids must match the order in the active SFX export included by
 * `sound/sound_data.c` (currently `sound/new/project_sfx.c`).
 */

#ifndef SFX_IDS_H
#define SFX_IDS_H

#include "ngpc_types.h"

enum {
    SFX_EXPLOSION   = 0u,
    SFX_TIR_VAISSEAU = 1u,
    SFX_MENU_MOVE   = 2u,
    SFX_MENU_SELECT = 3u,
    SFX_POWERUP     = 4u
};

#endif /* SFX_IDS_H */
