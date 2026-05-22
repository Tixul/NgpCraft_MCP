#ifndef SHMUP_PROFILE_H
#define SHMUP_PROFILE_H

#include "ngpc_types.h"

#define SHMUP_HIGHSCORE_COUNT 10u

typedef struct {
    char name[4];
    u16 score;
} ShmupHighScore;

void shmup_profile_init(void);

u8 shmup_profile_continue_setting_get(void);
void shmup_profile_continue_setting_set(u8 value);
void shmup_profile_flush(void);

void shmup_profile_highscore_get(u8 index, ShmupHighScore *out_entry);
u8 shmup_profile_highscore_qualifies(u16 score);
u8 shmup_profile_highscore_submit(const char name[3], u16 score);

#endif /* SHMUP_PROFILE_H */
