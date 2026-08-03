# Audio

Reference for the Neo Geo Pocket Color audio hardware (T6W28 PSG, Z80 sound CPU, memory map) and the Z80 sound-driver integration patterns, including the BGM/SFX API, stream format, direct PSG access, and known pitfalls.

> Note: This page uses ASCII only (avoids encoding issues on Windows/PowerShell).

---


## 1. Hardware Overview

### 1.1 PSG: T6W28

- Sound CPU: Z80-compatible at **3.072 MHz**
- PSG chip: T6W28 — **3 square-wave tone generators + 1 noise channel** (4 voices total)
- Z80 internal RAM: **4 KB**, shared with the main TLCS-900 CPU
- Tone frequency: 10-bit divider
- Attenuation: 4-bit (0 = loudest, 15 = silent)
- Stereo output: 2 PSG ports — `0x4000` (Right channel), `0x4001` (Left channel) in Z80 I/O space
  - For mono: write the same data to both ports

### 1.2 Memory Map — Audio Registers

**TLCS-900 side (main CPU):**

| Address | Name | Description |
|---------|------|-------------|
| `0x7000..0x7FFF` | `HW_Z80_RAM` | Shared Z80 RAM (4 KB window) |
| `0x00B8` | `HW_SOUNDCPU_CTRL` | Z80 control: `0xAAAA` = stop/reset, `0x5555` = start |
| `0x00B9` | — | Sound chip activation: `0x55` = ON, `0xAA` = OFF |
| `0x00BA` | `HW_Z80_NMI` | Trigger Z80 NMI (data ignored) |
| `0x00BC` | `HW_Z80_COMM` | Dual-port comm register (main CPU ↔ Z80) |
| `0x00A0` | — | Direct noise write to T6W28 (bypasses Z80) |
| `0x00A1` | — | Direct tone write to T6W28 (bypasses Z80) |
| `0x00A2/0x00A3` | — | DAC L/R output |

**Z80 side:**

| Z80 address | Description |
|-------------|-------------|
| `0x0000..0x0FFF` | Shared RAM (Z80 view of 0x7000..0x7FFF) |
| `0x4000` | PSG write — Right channel (noise side) |
| `0x4001` | PSG write — Left channel (tone side) |
| `0x8000` | Comm register (mirrors TLCS-900 `0x00BC`) |
| `0xC000` | Write to trigger interrupt on TLCS-900 |

### 1.3 T6W28 Register Map

| Reg | First byte | Function |
|-----|------------|----------|
| 0 | `0x80` | Tone1 frequency low (latch) |
| 1 | `0x90` | Tone1 attenuation |
| 2 | `0xA0` | Tone2 frequency low (latch) |
| 3 | `0xB0` | Tone2 attenuation |
| 4 | `0xC0` | Tone3 frequency low (latch) |
| 5 | `0xD0` | Tone3 attenuation |
| 6 | `0xE0` | Noise control |
| 7 | `0xF0` | Noise attenuation |

### 1.4 T6W28 Byte Encoding

**Tone frequency** (2 bytes, same value written to both PSG ports):
```
Byte 1 (latch): 1 RRR DDDD   — R = register (0/2/4), D = freq bits 3..0
Byte 2 (data):  0 0 DDDDDD   — D = freq bits 9..4
```

**Attenuation** (1 byte):
```
1 RRR VVVV   — R = register (1/3/5/7), V = attenuation (0 = loudest, 0xF = silent)
```

> **Warning:** the latch byte takes effect immediately. A partial write before the data byte
> can briefly produce a wrong frequency.

### 1.5 Frequency Formula

```
F = 3,072,000 / (32 * n)    where n = 10-bit divider (1..1023)
```

Common values:

| n | Frequency | Note |
|---|-----------|------|
| 109 | ~880 Hz | A5 |
| 146 | ~658 Hz | E5 |
| 218 | ~440 Hz | A4 |
| 291 | ~330 Hz | E4 |
| 436 | ~220 Hz | A3 |

The driver's `NOTE_TABLE` covers notes 0..50 (A2 to B6) with pre-computed register values.

### 1.6 Noise Control

Byte format for register 6:
- Bits 1..0: rate select — N/512, N/1024, N/2048, or Tone3 output
- Bit 2: noise type — 0 = periodic, 1 = white
- Changing the control register resets the shift register.

### 1.7 Timer3 — Z80 Interrupt Source

Timer3 (T03) is reserved as the interrupt source for the Z80 CPU (IM1 mode, ~7.8 kHz).
Stopping the Timer3 prescaler is prohibited (disrupts serial communication).
When using the SNK or Z80 sound driver, do not use Timer3 for gameplay purposes.

---

## 2. Integration Checklist

- **Init:** upload Z80 driver (stop → copy → start).
- **VBlank ISR:** call watchdog + frame counter + audio tick only (no heavy logic).
- **Per-frame update:** call `Sounds_Update()` once per frame from the main loop (after vsync + input).
- **Timer3:** verify no conflict if the Z80 driver uses it as its interrupt source.
- **Z80 RAM (4 KB):** monitor space usage (driver + data + channel state blocks + buffers).
- **Frame-lock:** use the game's own VBlank counter as the driver tick base, not the BIOS `VBCounter`.

---

## 3. Path A — Z80 Sound Driver

This is the recommended path for production games needing full BGM + SFX support.

The driver is split into a core module (`sounds.c` / `sounds.h`) plus a game-specific SFX
mapping stub.

### 3.1 Z80 Driver Load Sequence (Stop / Copy / Start)

```c
/* 1. Stop Z80 */
HW_SOUNDCPU_CTRL = 0xAAAA;

/* 2. Copy driver binary into Z80 RAM */
/* (copy sounds_z80_drv[] into (u8*)HW_Z80_RAM) */

/* 3. Start Z80 */
HW_SOUNDCPU_CTRL = 0x5555;
```

`Sounds_Init()` encapsulates this sequence. Call it once at startup.

### 3.2 Tick Model — Frame-Locked

```c
/* In main game loop (after ngpc_vsync + input): */
Sounds_Update();   /* = Sfx_Update() + Bgm_Update() */
```

- Call `Sounds_Update()` **once per frame**, locked to the game's VBlank counter.
- Do NOT call from the VBlank ISR itself (keep ISR minimal).
- 1 frame = 1 audio packet (SFX first, then BGM).

### 3.3 Main Driver API

```c
/* Init */
void Sounds_Init(void);        /* stop Z80, upload driver, start Z80 */

/* Per-frame update */
void Sounds_Update(void);      /* = Sfx_Update() + Bgm_Update() */

/* Direct sub-updates (if needed) */
void Sfx_Update(void);
void Bgm_Update(void);
```

### 3.4 BGM API

```c
/* Start BGM (no loop) */
void Bgm_Start(const u8* ch0, const u8* ch1, const u8* ch2, const u8* chn);

/* Start BGM with explicit loop points */
void Bgm_StartLoop4Ex(const u8* ch0, u16 loop0,
                      const u8* ch1, u16 loop1,
                      const u8* ch2, u16 loop2,
                      const u8* chn, u16 loopn);

/* Multi-song project export helper */
void NgpcProject_BgmStartLoop4ByIndex(u8 index);
/* calls Bgm_SetNoteTable(song->note_table) + Bgm_StartLoop4Ex(...) */

/* Stop */
void Bgm_Stop(void);

/* Fade out — speed: 0 = cancel, >0 = frames between fade steps */
void Bgm_FadeOut(u8 speed);

/* Change tempo at runtime (speed multiplier) */
void Bgm_SetTempo(u8 speed);

/* Switch note table (for multi-song with different tunings) */
void Bgm_SetNoteTable(const u16* note_table);
```

### 3.5 SFX Low-Level API

```c
/* Tone SFX with sweep and envelope */
void Sfx_PlayToneEx(u8 ch, u16 div, u8 attn, u8 frames,
                    u16 sw_end, s16 sw_step, u8 sw_speed, u8 sw_ping, u8 sw_on,
                    u8 env_on, u8 env_step, u8 env_spd);

/* Noise SFX with burst and envelope */
void Sfx_PlayNoiseEx(u8 rate, u8 type, u8 attn, u8 frames,
                     u8 burst, u8 burst_dur,
                     u8 env_on, u8 env_step, u8 env_spd);

/* Data-driven SFX bank */
void Sfx_PlayPresetTable(u8 id);

/* Silence a channel */
void Sfx_Stop(u8 ch);
```

Example tone SFX (sweep + envelope):
```c
Sfx_PlayToneEx(0, 300, 0, 20, 180, -6, 1, 0, 1, 1, 2, 2);
```

Example noise burst SFX:
```c
Sfx_PlayNoiseEx(0, 1, 0, 20, 1, 6, 1, 3, 1);
```

> **`Sfx_Play(u8 id)` is a no-op in the base driver.** You must define `SFX_PLAY_EXTERNAL 1`
> and implement it yourself in a game-specific file. See §5 for the full pattern.

### 3.6 Runtime Notes and Known Limits

- Max 5 PSG commands processed per frame.
- Noise channel is monophonic (shared noise register — overlapping hits may be dropped).
- `NOTE_TABLE` covers note indices 0..50 (NOTE_MAX_INDEX = 50).
- This is a **polling Z80 driver** (no NMI/COMM protocol).
- Tone voices cannot switch into noise mode.
- `BGM_OP_SET_PAN (0xF5)` is parsed as no-op (reserved for future stereo).
- No native PCM/DAC sample playback in this PSG driver.
- No built-in VGM parser.
- `SOUNDS_MAX_CATCHUP` can clamp long frame catch-up loops (optional).

### 3.7 Attenuation Model

PSG attenuation is **inverse volume**: `0 = loudest`, `15 = silent`.

Final voice attenuation is built as:
1. Base attenuation (`SET_ATTN` opcode or instrument default)
2. Instrument-time modulation (ADSR / env / macro)
3. Expression offset (`SET_EXPR`)
4. Global fade attenuation

Final value is clamped to `0..15`.

**Fade behavior:**
- Starting a new BGM resets fade state.
- Stopping BGM resets fade state.
- `Bgm_FadeOut(0)` cancels fade and resets fade attenuation.

### 3.8 Driver Customization: Frame-Lock Fix

The original "canonical" driver uses the BIOS `VBCounter` for its tick. This causes tempo drift
when the game uses a custom VBlank ISR.

**Fix:** in `sounds.c`, replace the `VBCounter` reference with the game's own VBlank counter
(`g_vb_counter` or equivalent). This keeps the driver frame-locked with the rest of the game.

### 3.9 Multi-Song Guidance

- The driver uses **one active instrument table at runtime**.
- Recommended: maintain one shared/global instrument bank with stable IDs across all tracks.
- If each song has its own instrument bank:
  - Keep files paired: `songX.c` + `songX_instruments.c`.
  - Always switch AND sync the driver table before starting the new song.
  - `Bgm_SetNoteTable(...)` handles note table switching at runtime.
  - The exported audio API source provides a weak `NOTE_TABLE` fallback symbol for link compatibility.

### 3.10 Build Flags (Slim Build)

Set to 0 to disable optional features and reduce code size:
```c
SOUNDS_ENABLE_MACROS          /* BGM macro support */
SOUNDS_ENABLE_ENV_CURVES      /* Envelope curves */
SOUNDS_ENABLE_PITCH_CURVES    /* Pitch curves */
SOUNDS_ENABLE_EXAMPLE_PRESETS /* Example preset table */
```

---

## 4. BGM Stream Format

### 4.1 Note Events

| Byte value | Meaning |
|------------|---------|
| `1..51` | Note index — looked up in `NOTE_TABLE` |
| `0xFF` | REST (silence for 1 tick) |
| `0x00` | End-of-stream marker |

> Always end streams with a `0x00` byte to avoid a stuck last note.
> Do NOT collapse REST events to 1 frame — their duration matters for rhythm.

### 4.2 FX Opcodes

| Opcode | Parameters | Description |
|--------|-----------|-------------|
| `0xF0 <attn>` | attn 0..15 | `SET_ATTN` — set absolute attenuation |
| `0xF1 <step> <speed>` | step 0..4, speed 1..10 | `SET_ENV` — envelope |
| `0xF2 <depth> <speed> <delay>` | | `SET_VIB` — vibrato |
| `0xF3 <end_lo> <end_hi> <step> <speed>` | div 1..1023, step signed | `SET_SWEEP` — pitch sweep |
| `0xF4 <id>` | | `SET_INST` — select instrument preset |
| `0xF5` | | `SET_PAN` — no-op (reserved) |
| `0xF6 <type> <data>` | | `HOST_CMD` — fade/tempo command |
| `0xF7 <expr>` | expr 0..15 | `SET_EXPR` — expression (attenuation offset) |
| `0xF8 <lo> <hi>` | s16 LE | `PITCH_BEND` |
| `0xF9 <a> <d> <s> <r>` | | `SET_ADSR` |

### 4.3 Channel Assignment

| Stream symbol | PSG voice |
|---------------|-----------|
| `BGM_CH0` | Tone1 |
| `BGM_CH1` | Tone2 |
| `BGM_CH2` | Tone3 |
| `BGM_CHN` | Noise (note index 1..8 → noise register) |

### 4.4 Looping

- Loop via `Bgm_StartLoop4Ex()` with explicit byte offset per channel:
  ```c
  Bgm_StartLoop4Ex(CH0_data, CH0_loop_offset,
                   CH1_data, CH1_loop_offset,
                   CH2_data, CH2_loop_offset,
                   CHN_data, CHN_loop_offset);
  ```
- For a clean loop break, place the loop point on a REST byte.
- Re-entry is direct (no forced silence). Silence before a loop = add a REST.

### 4.5 Exposed Symbols

From the exported stream source:
```c
extern const u16 NOTE_TABLE[];   /* divider table, 51 entries */
extern const u8  BGM_CH0[];      /* tone 1 stream */
extern const u8  BGM_CH1[];      /* tone 2 stream */
extern const u8  BGM_CH2[];      /* tone 3 stream */
extern const u8  BGM_CHN[];      /* noise stream */
```

---

## 5. SFX Integration

### 5.1 Project SFX Export

A project SFX export produces a single source file (e.g. `project_sfx.c`).
This is a **single global bank** for all project SFX (not one file per SFX).

Add the exported file to your game build and define:
```c
#define SFX_PLAY_EXTERNAL 1
```

Then implement `Sfx_Play(u8 id)` in your game code (see §5.2).

Arrays provided by the export:
```c
PROJECT_SFX_COUNT
/* Tone SFX: */
PROJECT_SFX_TONE_ON[], PROJECT_SFX_TONE_CH[], PROJECT_SFX_TONE_DIV[]
PROJECT_SFX_TONE_ATTN[], PROJECT_SFX_TONE_FRAMES[]
PROJECT_SFX_TONE_SW_ON[], PROJECT_SFX_TONE_SW_END[], PROJECT_SFX_TONE_SW_STEP[]
PROJECT_SFX_TONE_SW_SPEED[], PROJECT_SFX_TONE_SW_PING[]
PROJECT_SFX_TONE_ENV_ON[], PROJECT_SFX_TONE_ENV_STEP[], PROJECT_SFX_TONE_ENV_SPD[]
/* Noise SFX: */
PROJECT_SFX_NOISE_ON[], PROJECT_SFX_NOISE_RATE[], PROJECT_SFX_NOISE_TYPE[]
PROJECT_SFX_NOISE_ATTN[], PROJECT_SFX_NOISE_FRAMES[], PROJECT_SFX_NOISE_BURST[]
PROJECT_SFX_NOISE_BURST_DUR[], PROJECT_SFX_NOISE_ENV_ON[]
PROJECT_SFX_NOISE_ENV_STEP[], PROJECT_SFX_NOISE_ENV_SPD[]
```

### 5.2 Sfx_Play Reference Implementation

```c
#include "sounds.h"

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

void Sfx_Play(u8 id)
{
    if (id >= PROJECT_SFX_COUNT) return;

    if (PROJECT_SFX_TONE_ON[id]) {
        Sfx_PlayToneEx(PROJECT_SFX_TONE_CH[id], PROJECT_SFX_TONE_DIV[id],
                       PROJECT_SFX_TONE_ATTN[id], PROJECT_SFX_TONE_FRAMES[id],
                       PROJECT_SFX_TONE_SW_END[id], PROJECT_SFX_TONE_SW_STEP[id],
                       PROJECT_SFX_TONE_SW_SPEED[id], PROJECT_SFX_TONE_SW_PING[id],
                       PROJECT_SFX_TONE_SW_ON[id],
                       PROJECT_SFX_TONE_ENV_ON[id], PROJECT_SFX_TONE_ENV_STEP[id],
                       PROJECT_SFX_TONE_ENV_SPD[id]);
    }

    if (PROJECT_SFX_NOISE_ON[id]) {
        Sfx_PlayNoiseEx(PROJECT_SFX_NOISE_RATE[id], PROJECT_SFX_NOISE_TYPE[id],
                        PROJECT_SFX_NOISE_ATTN[id], PROJECT_SFX_NOISE_FRAMES[id],
                        PROJECT_SFX_NOISE_BURST[id], PROJECT_SFX_NOISE_BURST_DUR[id],
                        PROJECT_SFX_NOISE_ENV_ON[id], PROJECT_SFX_NOISE_ENV_STEP[id],
                        PROJECT_SFX_NOISE_ENV_SPD[id]);
    }
}
```

Usage:
```c
Sfx_Play(SFX_SHOOT);    /* tone SFX */
Sfx_Play(SFX_EXPLODE);  /* noise SFX */
```

### 5.3 SFX Behavior

- SFX uses frame timers per channel (0..3).
- Tone SFX: supports sweep, envelope, vibrato.
- Noise SFX: supports envelope and burst (gated on/off pulses).
- When SFX ends: channel is silenced, BGM restore is triggered.
- If multiple SFX requests on same channel: current SFX wins (new one dropped).

---

## 6. BGM / SFX Coexistence

### 6.1 Channel Preemption

- SFX **preempts BGM** on the same channel (channel 1..4).
- When the SFX ends, the driver restores the last known BGM state on that channel.
- "Clean restore" does NOT mean "no audible interruption" — if BGM and SFX both want
  the same voice simultaneously, there will be competition.

### 6.2 Design Recommendations

**For UI SFX:**
- Prefer very short SFX (short `frames` count).
- Avoid long tails on menu sounds.
- If possible, route UI SFX to the PSG voice least prominent in the BGM.
- Accept that a tone UI SFX may briefly "color" the music if voices are shared.

**For gameplay SFX (e.g. shots):**
- If perceived rate seems wrong, verify **perceived sound length**, not only trigger rate.
- A long-tail SFX triggered rapidly can mask the next trigger.

**For BGM:**
- Write BGM arrangements that can tolerate one voice being momentarily stolen.
- Leave the noise channel or one tone channel with lower musical priority for SFX.

### 6.3 Debug Checklist for Audio Conflicts

When a BGM/SFX conflict is suspected:
1. Verify that the SFX trigger follows the correct game logic (correct condition, not over-triggering).
2. Verify the voice (channel) mapping — which PSG voice does this SFX target?
3. Verify the SFX duration — is the tail longer than expected?
4. Check if both BGM and SFX are trying to use the same voice at the same time.

Do not conclude "driver bug" before verifying all four points.

### 6.4 Effects lose to music unless the driver arbitrates

With four PSG channels shared between music and effects, an effect written to a channel
the music also uses is overwritten on the next music update **if the driver updates music
last**. The effect is not "quiet" — it is being erased.

Measured in one project: the player's shot sat **11.3 dB** below the music and was
inaudible in play. Two things made that measurable at all:

- **Total RMS is the wrong metric.** The shot is narrowband (165–210 Hz) and vanishes in
  a full-mix RMS. Measuring only its band against the music level made it quantifiable.
- **Compare two runs from the same reset**, one firing and one not. The music runs
  identically in both (correlation r ≈ 1.00 across 11 s), so the difference *is* the
  effects.

### 6.5 A music-only master attenuation is the right knob

Raising effect volume runs out of room fast — in the case above the shot was already at
`attn 1` of 15, and its own envelope faded it a further 8 dB while playing. Lowering the
**music** is the lever that still has headroom.

A global attenuation offset applied to the music voices **after** instrument, envelope,
LFO and fade — and **not** to the sound effects — is the balance control between music
and effects. 2 dB per step on the T6W28.

Two implementation notes:

- **A per-voice volume setter is not enough** if it writes an absolute value: the next
  note overwrites it from the instrument table. The offset must be applied at the point
  where the final attenuation is computed.
- **Do not reuse the fade offset** for this. Every `Bgm_Start` resets fade state, so the
  setting would silently disappear on the next tune.

Measured against theory (2 dB/step): steps 2/4/6/8 gave −4.2 / −8.4 / −12.1 / −16.2 dB.

### 6.6 T6W28 low-frequency limit

With `F = 3 072 000 / (32 x n)` and n ≤ 1023, the lowest tone the chip can produce is
`96000 / 1023` ≈ **94 Hz**. Effects ported from other hardware that sit below this must be
transposed up by whole octaves; there is no way to reach them.

Converting from PC-speaker pitches (`f = 1193182 / divisor`), the divisor maps as
`n = divisor x 0.080457`.

*Sections 6.4 to 6.6 contributed by [Napsterix](https://github.com/Napsterix).*

---

## 7. Path B — Direct PSG Pattern

For simple sequences (sound effects or music) without a full Z80 driver.
Useful for debug builds, minimal demos, or cases where Z80 memory is already full.

### 7.1 PSG Registers (T6W28, 0x810D-0x8118)

These are the direct write addresses on the NGPC main CPU bus (not Z80 I/O):

```
0x810D  CH1 freq lo
0x810E  CH1 freq hi
0x810F  CH1 volume
0x8111  CH2 freq lo
0x8112  CH2 freq hi
0x8113  CH2 volume
0x8115  CH3 freq lo
0x8116  CH3 freq hi
0x8117  CH3 volume
0x8118  Noise / mode
```

### 7.2 Silence Init

```c
/* Silence all channels: freq=3, vol=7 (silent on T6W28) */
*(volatile u8*)0x810D = 3;
*(volatile u8*)0x810F = 7;
*(volatile u8*)0x8111 = 3;
*(volatile u8*)0x8113 = 7;
*(volatile u8*)0x8115 = 3;
*(volatile u8*)0x8117 = 7;
```

### 7.3 Minimal Sequencer Pattern

ROM note sequence format: `{duration_frames, freq_byte}` pairs, terminated by `0xFF`.

```c
typedef struct { uint8_t duration; uint8_t freq; } Note;

/* Sequence terminated by 0xFF sentinel */
const Note melody[] = {
    { 8, 0x40 }, { 8, 0x50 }, { 8, 0x60 }, { 8, 0x70 },
    { 0xFF }     /* sentinel -> loop to start */
};
```

Update in VBlank (call once per frame):
```c
/* Preconditions: audio_flags bit 7 = music active */
if (audio_flags & 0x80) {
    if (note_timer == 0) {
        seq_ptr += 2;              /* advance sequence */
        if (seq_ptr[1] == 0xFF)    /* end of sequence? */
            seq_ptr = seq_start;   /* loop */
        note_timer = seq_ptr[0];   /* load note duration */
    }
    note_timer--;
    write_psg_freq(seq_ptr[1]);    /* write current frequency */
}
```

This pattern writes directly to PSG registers — no Z80 driver required.

### 7.4 RAM Variables

A representative RAM layout for a direct-PSG audio engine, established by reverse engineering
a commercial NGPC title:

```
0x6A20  audio_flags       (bit 7 = music active, bit 0 = trigger SFX)
0x6A22  audio_frame_cnt   (mask &3 for 15fps music)
0x6A25  audio_mode_cur
0x6A44  sfx_pending       (SFX to play this frame)
0x6A46  sfx_timer         (active SFX duration)
0x6A64  music_seq_ptr     (current pointer in ROM sequence)
0x6A68  music_note_timer  (decremented each frame)
```

### 7.5 Minimal Custom Z80 Driver (Polling)

If you need Z80-assisted sound but want a minimal driver (3-byte command protocol):

**Z80 side (assembled at 0x0000, shared data at 0x0003..0x0006):**
```asm
        org   0x0000
        jp    start

trigger: db 0x00       ; 0x0003 — CPU writes 1, Z80 clears to 0
snd_b1:  db 0x00       ; 0x0004
snd_b2:  db 0x00       ; 0x0005
snd_b3:  db 0x00       ; 0x0006

start:
        di
        ld    sp, 0x0FFF
loop:
        ld    a, (trigger)
        or    a
        jr    z, loop

        ld    a, (snd_b1)
        ld    (0x4001), a   ; Tone (L)
        ld    (0x4000), a   ; Tone (R)
        ld    a, (snd_b2)
        ld    (0x4001), a
        ld    (0x4000), a
        ld    a, (snd_b3)
        ld    (0x4001), a
        ld    (0x4000), a

        xor   a
        ld    (trigger), a
        jr    loop
```

**Main CPU side (C):**
```c
#define Z80_RAM_BASE 0x7000
#define SND_TRIGGER  (*(volatile u8*)(Z80_RAM_BASE + 0x0003))
#define SND_BYTE1    (*(volatile u8*)(Z80_RAM_BASE + 0x0004))
#define SND_BYTE2    (*(volatile u8*)(Z80_RAM_BASE + 0x0005))
#define SND_BYTE3    (*(volatile u8*)(Z80_RAM_BASE + 0x0006))

static void WaitTrigger(void) {
    int timeout = 0x1000;
    while (SND_TRIGGER && --timeout) { /* spin */ }
}

static void PlayTone(u8 b1, u8 b2, u8 b3) {
    WaitTrigger();
    SND_BYTE1 = b1;  /* latch byte */
    SND_BYTE2 = b2;  /* data byte */
    SND_BYTE3 = b3;  /* attenuation */
    SND_TRIGGER = 1;
}
```

### 7.6 NMI + ACK Command Protocol (Z80 Mailbox)

An alternative to the polling driver (§3.6) and the 3-byte trigger driver (§7.5): a
command/acknowledge handshake over the 1-byte mailbox at `0x00BC`, driven by the Z80 NMI.

Command sequence (main CPU):
1. Write the command byte to `0x00BC`.
2. Write `1` to `0x00BA` to fire the Z80 NMI.
3. Poll for acknowledgement, with a retry cap (~100 spins) to avoid a hang.

Acknowledgement (Z80 side):
- **Fast commands:** the Z80 echoes `cmd XOR 0xFF` back into `0x00BC`. The main CPU
  polls `0x00BC` until it reads the expected complement.
- **Slower commands:** the Z80 bumps a sequence counter in Z80 RAM (`0x7007`). The
  main CPU polls that counter for a change instead of the complement echo.

Example command bytes:

| Byte | Command |
|------|---------|
| `0xC1` | INIT |
| `0xC3` | START |
| `0xC5` | PLAY |
| `0xC7` | STOP_ALL |

```c
#define HW_Z80_COMM (*(volatile u8*)0x00BC)
#define HW_Z80_NMI  (*(volatile u8*)0x00BA)
#define Z80_SEQ     (*(volatile u8*)0x7007)

static u8 snd_cmd_fast(u8 cmd) {
    int retry = 100;
    HW_Z80_COMM = cmd;
    HW_Z80_NMI  = 1;                    /* trigger Z80 NMI */
    while (retry-- && HW_Z80_COMM != (u8)(cmd ^ 0xFF)) { /* spin */ }
    return retry > 0;                   /* 0 = timed out */
}
```

> The mailbox at `0x00BC` is 1 byte and bidirectional, so the echo overwrites the
> command in place. Always cap the poll loop; a missing ACK must not hang the frame.

### 7.7 PSG 2-Channel VBL Streaming

A fast register-pattern observed in a commercial NGPC title: the VBL ISR writes 2 PSG
channels directly using XIY (data pointer) and XIZ (register pointer) with auto-increment:

```asm
; On entry: XIY = ptr to audio frame data (seq of: vol_byte, freq_word pairs, 1-byte gap per chan)
; XIZ = 0x8111 (PSG register base — Channel 2)
inc    0x1, XIY             ; skip 1 byte (channel gap / padding)
ld     XIZ, 0x8111          ; point to CH2 registers

ld     A, (XIY+)            ; load CH2 volume byte
ld     (XIZ+), A            ; write 0x8111 = CH2 vol
ld     WA, (XIY+)           ; load CH2 freq word (2 bytes)
ld     (XIZ+), WA           ; write 0x8112-13 = CH2 freq lo/hi

inc    0x1, XIY             ; skip 1 byte (channel gap)
inc    0x1, XIZ             ; skip 0x8114 (unused)

ld     A, (XIY+)            ; load CH3 volume byte
ld     (XIZ+), A            ; write 0x8115 = CH3 vol
ld     WA, (XIY+)           ; load CH3 freq word
ld     (XIZ+), WA           ; write 0x8116-17 = CH3 freq lo/hi
```

Register write sequence produced:
```
XIZ addr   Register    Content
0x8111     CH2 vol     volume byte (0-7)
0x8112     CH2 freq lo freq word lo (auto-increment covers both)
0x8113     CH2 freq hi
(0x8114 skipped)
0x8115     CH3 vol     volume byte (0-7)
0x8116     CH3 freq lo
0x8117     CH3 freq hi
```

Audio frame data layout in RAM (one entry = 7 bytes):
```
+0  gap/padding byte
+1  CH2 vol (u8)
+2  CH2 freq lo (u8)
+3  CH2 freq hi (u8)
+4  gap byte
+5  CH3 vol (u8)
+6  CH3 freq lo (u8)
+7  CH3 freq hi (u8)
```

This is the fastest possible PSG update path: 8 memory reads + 6 register writes
using only register-autoincrement — no address calculation per write.

> For CH1 (0x810D-0x810F) update: use a separate call or prepend before the
> above sequence. CH1 is commonly handled separately in a noise/SFX path.

---

## 8. Path C — Official SNK K1Sound Driver

This path uses the binary `K1SND.DRV` (Z80) + `K1SLIB.REL` (TLCS-900 library) from the official
SNK SDK.

### 8.1 API Calls

```c
Sound_Init()                    /* Init driver */
Sound_Buf_Init()                /* Init command buffer */
Sound_List_Dt_Send(list_no)     /* Transfer group data to Z80 RAM */
Put_Sound_Buf(sound_code)       /* Trigger play/stop/control */
Sound_Periodic_Func()           /* Per-frame VBlank tick */
```

A thin wrapper layer can simplify these:
```c
ngpc_sound_init()               /* = Sound_Init + Sound_Buf_Init */
ngpc_sound_load_group(list_no)  /* = Sound_List_Dt_Send */
ngpc_sound_tick()               /* = Sound_Periodic_Func, call from VBlank */
ngpc_sound_play(sound_code)     /* = Put_Sound_Buf */
```

### 8.2 Sound Codes

From `SAMPLE.INC`:

| Constant | Value | Description |
|----------|-------|-------------|
| `BGM_STOP` | `0x007F` | Stop BGM |
| `BGM_DI` | `0x8080` | Disable BGM |
| `SE_DI` | `0x8081` | Disable SFX |
| `ALL_DI` | `0x8082` | Disable all |
| `BGM_EI` | `0x8083` | Enable BGM |
| `SE_EI` | `0x8084` | Enable SFX |
| `ALL_EI` | `0x8085` | Enable all |
| `SE_ALL_STOP` | `0x8086` | Stop all SFX |
| `RESET` | `0xFFFF` | Full reset |

Base offsets used in the sample:
- BGM start base: `0x0020 + id`
- SFX start base: `0x0080 + id`

### 8.3 Group Concept (4 KB Limit)

- Sound data is organized in Groups, each limited to the 4 KB Z80 RAM.
- A group includes: driver + host commands + voice programs + BGM/SE data.
- If total group size > 4 KB, export fails ("SizeOver").
- `Sound_List_Dt_Send(list_no)` transfers a Group into Z80 RAM before playback.

### 8.4 Host Commands

Available from the host CPU via `Put_Sound_Buf()` or embedded in BGM streams:

- **Fade Out:** value 1..31 (approx 1 second per unit)
- **Tempo Change:** value 1..255 — new tempo = value × initial_tempo / 64

### 8.5 MIDI Rules

For MIDI → K1Sound export:

- Type 1 MIDI only.
- Division = 48 (auto-downscale if all events divisible by 2, 4...).
- Tempo track = track 1 only.
- Note range: MIDI 43..120 (G2..C9). Noise uses same range.
- Pitch bend: 0..16383 → 1..126 (~±1 octave).
- Program Change: 0..127, max 32 programs per group.
- BGM: 4 channels usable. SE: 1 channel.
- SE tempo fixed at 120; tempo change applies to BGM only.
- Looping: via NRPN + Data Entry. Loop count: 0 = infinite, 1..31 = finite.

---

## 9. Known Bugs and Fixes

### Bug 1 — Hybrid Export: Curve/Macro IDs Out-of-Range

**Symptom:** music sounds different depending on driver vs export (wrong timbre/envelope).

**Cause:** the MIDI export references `env_curve_id` up to 2, `pitch_curve_id` up to 7, and
`macro_id` up to 3, but the base driver may have fewer curves/macros in its tables — causing
out-of-range reads and a different rendered sound.

**Fix** (in `sounds.c`):
- Add `env_curve_id = 2` to the envelope curve table.
- Add `pitch_curve_id = 5..7` (alias from existing curves to match exported IDs).
- Add placeholder macros 2..3 (`count=0`) to stay within table bounds.

### Bug 2 — Tempo Drift (BIOS VBCounter)

**Symptom:** tempo drifts or does not match the export reference.

**Cause:** the default driver uses the BIOS `VBCounter` as its tick source, but games with a
custom VBlank ISR have their own counter that is not synchronized with the BIOS one.

**Fix:** in `sounds.c`, replace `VBCounter` with the game's own VBlank counter
(`g_vb_counter` or equivalent). This ensures both game and audio tick at the same rate.

### Bug 3 — Stuck Last Note

**Symptom:** the final note of a BGM stream continues to ring after the stream ends.

**Fix:** always add an explicit `0x00` end-of-stream byte at the end of every BGM stream.

---

## Quick Reference

| Item | Value | Notes |
|------|-------|-------|
| Sound CPU | Z80 @ 3.072 MHz | |
| PSG | T6W28 | 3 tone + 1 noise |
| Z80 RAM | 4 KB | 0x7000 (CPU), 0x0000 (Z80) |
| Z80 stop | `0xAAAA` → `HW_SOUNDCPU_CTRL` | |
| Z80 start | `0x5555` → `HW_SOUNDCPU_CTRL` | |
| Z80 NMI | write any byte → `0x00BA` | |
| Z80 comm | `0x00BC` | bidirectional |
| PSG port (Z80) | `0x4000` (R) / `0x4001` (L) | write both for mono |
| Direct PSG (CPU) | `0x00A0` / `0x00A1` | noise / tone |
| Attenuation | 0 = loud, 15 = silent | 4-bit inverse |
| Freq formula | `3072000 / (32 * n)` | n = 1..1023 |
| NOTE_TABLE | 0..50 (A2..B6) | 51 entries |
| BGM stream REST | `0xFF` | silence for 1 tick |
| BGM stream END | `0x00` | required terminator |
| FX opcode range | `0xF0..0xF9` | embedded in stream bytes |
| SFX max buffer | 5 PSG commands/frame | |
| Timer3 | reserved for Z80 | do not use for gameplay |
| Frame-lock | use game's g_vb_counter | not BIOS VBCounter |
| Sfx_Play | no-op unless `SFX_PLAY_EXTERNAL=1` | implement in game code |
| BGM_STOP code | `0x007F` | K1Sound path |
| Group 4 KB limit | driver + voices + data | K1Sound path |

---

## See Also

- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — Full hardware register map (audio section, timer registers)
- [Game Loop](../05_Systems/Game-Loop.md) — VBlank ISR structure, frame-locked update model
- [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — Z80 driver build, `sounds.c` integration
- [Storage and Saves](../05_Systems/Storage-and-Saves.md) — Flash save (unrelated to audio but useful for high score data)
