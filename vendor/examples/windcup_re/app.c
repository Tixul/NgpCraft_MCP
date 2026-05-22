#include "app.h"
#include "state.h"

typedef struct {
    void (*Init)(void);
    void (*Update)(void);
    void (*Render)(void);
} AppState;

static AppStateID s_state;
static AppStateID s_pending_state;
static u8 s_has_pending_state;

static const AppState s_states[APP_STATE_COUNT] = {
    { StateIntro_Init,   StateIntro_Update,   StateIntro_Render   },
    { StateTitle_Init,   StateTitle_Update,   StateTitle_Render   },
    { StateSelectPlayer_Init, StateSelectPlayer_Update, StateSelectPlayer_Render },
    { StateSelectArena_Init,  StateSelectArena_Update,  StateSelectArena_Render  },
    { StateMatch_Init,   StateMatch_Update,   StateMatch_Render   },
    { StateOptions_Init, StateOptions_Update, StateOptions_Render },
};

void App_ChangeState(AppStateID id)
{
    if ((u8)id >= (u8)APP_STATE_COUNT) {
        id = APP_STATE_TITLE;
    }

    /* Defer heavy Init() to next App_Update() to avoid mid-frame stalls. */
    s_pending_state = id;
    s_has_pending_state = 1;
}

AppStateID App_GetState(void)
{
    return s_state;
}

void App_Init(void)
{
    /* deterministic start */
    s_state = APP_STATE_INTRO;
    s_pending_state = APP_STATE_INTRO;
    s_has_pending_state = 0;
    s_states[s_state].Init();
}

void App_Update(void)
{
    if (s_has_pending_state) {
        s_state = s_pending_state;
        s_has_pending_state = 0;
        s_states[s_state].Init();
        return;
    }
    s_states[s_state].Update();
}

void App_Render(void)
{
    /* Skip one-frame render while waiting to apply pending state init. */
    if (s_has_pending_state) {
        return;
    }
    s_states[s_state].Render();
}
