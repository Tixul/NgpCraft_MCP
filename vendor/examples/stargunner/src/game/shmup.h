#ifndef SHMUP_H
#define SHMUP_H

#include "ngpc_types.h"

void shmup_init(void);
void shmup_vblank(void);

/* Returns 0=running, 1=exit to menu, 2=pause requested. */
u8 shmup_update(void);

/* Stop BGM + DMA. Call before leaving the game from the pause screen. */
void shmup_abort(void);

#endif /* SHMUP_H */
