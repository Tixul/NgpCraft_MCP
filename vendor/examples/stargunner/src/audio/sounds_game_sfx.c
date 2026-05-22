/*
 * sounds_game_sfx.c - Game-specific Sfx_Play() mapping
 *
 * Uses the active NGPC Sound Creator export selected by `sound/sound_data.c`.
 * Game-facing ids stay stable even if the export bank order changes.
 */

#include "ngpc_types.h"
#include "sounds.h"
#include "audio/sfx_ids.h"

/* Exported by `sound/project_sfx.c`. */
extern const unsigned char PROJECT_SFX_COUNT;
extern const unsigned char PROJECT_SFX_TONE_ON[];
extern const unsigned char PROJECT_SFX_TONE_CH[];
extern const unsigned short PROJECT_SFX_TONE_DIV[];
extern const unsigned char PROJECT_SFX_TONE_ATTN[];
extern const unsigned char PROJECT_SFX_TONE_FRAMES[];
extern const unsigned char PROJECT_SFX_TONE_SW_ON[];
extern const unsigned short PROJECT_SFX_TONE_SW_END[];
extern const signed short PROJECT_SFX_TONE_SW_STEP[];
extern const unsigned char PROJECT_SFX_TONE_SW_SPEED[];
extern const unsigned char PROJECT_SFX_TONE_SW_PING[];
extern const unsigned char PROJECT_SFX_TONE_ENV_ON[];
extern const unsigned char PROJECT_SFX_TONE_ENV_STEP[];
extern const unsigned char PROJECT_SFX_TONE_ENV_SPD[];
extern const unsigned char PROJECT_SFX_NOISE_ON[];
extern const unsigned char PROJECT_SFX_NOISE_RATE[];
extern const unsigned char PROJECT_SFX_NOISE_TYPE[];
extern const unsigned char PROJECT_SFX_NOISE_ATTN[];
extern const unsigned char PROJECT_SFX_NOISE_FRAMES[];
extern const unsigned char PROJECT_SFX_NOISE_BURST[];
extern const unsigned char PROJECT_SFX_NOISE_BURST_DUR[];
extern const unsigned char PROJECT_SFX_NOISE_ENV_ON[];
extern const unsigned char PROJECT_SFX_NOISE_ENV_STEP[];
extern const unsigned char PROJECT_SFX_NOISE_ENV_SPD[];

static u8 game_sfx_to_project_sfx(u8 id)
{
    switch (id) {
    case SFX_EXPLOSION:    return 4u; /* explosion_new */
    case SFX_TIR_VAISSEAU: return 1u; /* tir_vaisseau */
    case SFX_MENU_MOVE:    return 2u; /* menu_move */
    case SFX_MENU_SELECT:  return 3u; /* menu_select */
    case SFX_POWERUP:      return 6u; /* catch_power_up */
    default:               return 0xFFu;
    }
}

static void play_project_tone(u8 id, u8 ch_override, u8 frames_override)
{
    u8 ch = PROJECT_SFX_TONE_CH[id];
    u8 frames = PROJECT_SFX_TONE_FRAMES[id];

    if (ch_override != 0xFFu) ch = ch_override;
    if (frames_override != 0u) frames = frames_override;

    Sfx_PlayToneEx(
        ch,
        (u16)PROJECT_SFX_TONE_DIV[id],
        (u8)PROJECT_SFX_TONE_ATTN[id],
        frames,
        (u16)PROJECT_SFX_TONE_SW_END[id],
        (s16)PROJECT_SFX_TONE_SW_STEP[id],
        (u8)PROJECT_SFX_TONE_SW_SPEED[id],
        (u8)PROJECT_SFX_TONE_SW_PING[id],
        (u8)PROJECT_SFX_TONE_SW_ON[id],
        (u8)PROJECT_SFX_TONE_ENV_ON[id],
        (u8)PROJECT_SFX_TONE_ENV_STEP[id],
        (u8)PROJECT_SFX_TONE_ENV_SPD[id]);
}

static void play_project_noise(u8 id, u8 frames_override)
{
    u8 frames = PROJECT_SFX_NOISE_FRAMES[id];

    if (frames_override != 0u) frames = frames_override;

    Sfx_PlayNoiseEx(
        (u8)PROJECT_SFX_NOISE_RATE[id],
        (u8)PROJECT_SFX_NOISE_TYPE[id],
        (u8)PROJECT_SFX_NOISE_ATTN[id],
        frames,
        (u8)PROJECT_SFX_NOISE_BURST[id],
        (u8)PROJECT_SFX_NOISE_BURST_DUR[id],
        (u8)PROJECT_SFX_NOISE_ENV_ON[id],
        (u8)PROJECT_SFX_NOISE_ENV_STEP[id],
        (u8)PROJECT_SFX_NOISE_ENV_SPD[id]);
}

void Sfx_Play(u8 id)
{
    /* Small safety: keep ids stable (compile-time ref only). */
    (void)SFX_EXPLOSION;
    (void)SFX_TIR_VAISSEAU;
    (void)SFX_MENU_MOVE;
    (void)SFX_MENU_SELECT;
    (void)SFX_POWERUP;

    if (id == SFX_MENU_MOVE) {
        id = game_sfx_to_project_sfx(id);
        if (id == 0xFFu || id >= (u8)PROJECT_SFX_COUNT) return;
        if (PROJECT_SFX_TONE_ON[id]) {
            /* Keep the authored menu blip, but route it away from the lead voice. */
            play_project_tone(id, 2u, 3u);
        }
        return;
    }

    if (id == SFX_MENU_SELECT) {
        id = game_sfx_to_project_sfx(id);
        if (id == 0xFFu || id >= (u8)PROJECT_SFX_COUNT) return;
        if (PROJECT_SFX_TONE_ON[id]) {
            play_project_tone(id, 2u, 4u);
        }
        return;
    }

    if (id == SFX_TIR_VAISSEAU) {
        id = game_sfx_to_project_sfx(id);
        if (id == 0xFFu || id >= (u8)PROJECT_SFX_COUNT) return;
        if (PROJECT_SFX_NOISE_ON[id]) {
            /* Shorter tail so the slower fire rate also sounds slower. */
            play_project_noise(id, 6u);
        }
        if (PROJECT_SFX_TONE_ON[id]) {
            play_project_tone(id, 0xFFu, 0u);
        }
        return;
    }

    id = game_sfx_to_project_sfx(id);
    if (id == 0xFFu) return;
    if (id >= (u8)PROJECT_SFX_COUNT) return;

    if (PROJECT_SFX_TONE_ON[id]) {
        play_project_tone(id, 0xFFu, 0u);
    }

    if (PROJECT_SFX_NOISE_ON[id]) {
        play_project_noise(id, 0u);
    }
}
