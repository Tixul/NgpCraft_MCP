#ifndef APP_H
#define APP_H

#include "ngpc.h"

typedef enum {
    APP_STATE_INTRO = 0,
    APP_STATE_TITLE,
    APP_STATE_PLAYER_SELECT,
    APP_STATE_ARENA_SELECT,
    APP_STATE_MATCH,
    APP_STATE_OPTIONS,
    APP_STATE_COUNT
} AppStateID;

void App_Init(void);
void App_Update(void);
void App_Render(void);

void App_ChangeState(AppStateID id);
AppStateID App_GetState(void);

#endif
