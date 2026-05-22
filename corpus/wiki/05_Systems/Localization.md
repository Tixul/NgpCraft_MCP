# Localization

BIOS language detection (English / Japanese) and bilingual NGPC games: string tables, asset selection, system font behavior, and pitfalls.

---


## 1. Overview

The Neo Geo Pocket Color (and the monochrome NGP) lets the user select their language
in the BIOS menu: **English** or **Japanese**.

Games designed for bilingual support adapt automatically — menus, text strings, and
certain visual assets change based on the system language.

Detection requires **no BIOS call** — it is a simple memory read at address `0x6F87`,
written by the BIOS at startup.

```
0x00 = LANG_ENGLISH   (English)
0x01 = LANG_JAPANESE  (Japanese)
```

Read this register once during init and cache it in a static
variable, accessible via `ngpc_get_language()`.

---

## 2. Hardware Register — HW_LANGUAGE

**Address:** `0x6F87`
**Size:** `u8` (8 bits)
**Access:** read-only (set by BIOS at boot, never changes during gameplay)

This register is part of the BIOS System Information area (`0x6F80-0x6F91`):

```
0x6F80  HW_BAT_VOLT_RAW   battery voltage (u16)
0x6F82  HW_JOYPAD         joypad state
0x6F84  HW_USR_BOOT       boot type (0=power, 1=resume, 2=alarm)
0x6F85  HW_USR_SHUTDOWN   BIOS shutdown request flag
0x6F86  HW_USR_ANSWER     user response flags
0x6F87  HW_LANGUAGE       language (0=EN, 1=JP)      <-- here
0x6F91  HW_OS_VERSION     0=NGP mono, !=0=NGPC color
```

Definition in `ngpc_hw.h`:

```c
#define HW_LANGUAGE   (*(volatile u8 *)0x6F87)
#define LANG_ENGLISH  0u    /* HW_LANGUAGE == 0 */
#define LANG_JAPANESE 1u    /* HW_LANGUAGE == 1 */
```

Direct read is possible but prefer the cached API:

```c
u8 lang = HW_LANGUAGE;   /* works, but use ngpc_get_language() instead */
```

---

## 3. Language API

### 3.1 Implementation

The language constants (`LANG_ENGLISH` / `LANG_JAPANESE`) live in `ngpc_hw.h`, and
`ngpc_get_language()` returns the value cached during initialization:

```c
static u8 s_language = 0;   /* cached at init */

void ngpc_init(void)
{
    /* ... */
    /* Cache system language from BIOS register (0x6F87).
     * LANG_ENGLISH=0, LANG_JAPANESE=1. Set by BIOS at boot, read-only. */
    s_language = HW_LANGUAGE;
    /* ... */
}

u8 ngpc_get_language(void)
{
    return s_language;
}
```

Basic usage:

```c
#include "ngpc_sys.h"
#include "ngpc_hw.h"   /* for LANG_ENGLISH / LANG_JAPANESE */

if (ngpc_get_language() == LANG_JAPANESE) {
    /* Japanese text / assets */
} else {
    /* English text / assets (default) */
}
```

### 3.2 Why Cache Instead of Reading Directly?

Same rationale as `ngpc_is_color()`:

- Avoids scattered `volatile` reads throughout game code
- The value never changes during gameplay
- Single source of truth
- Cost: zero (one `u8` in static RAM)

---

## 4. Sysfont and Language

`ngpc_load_sysfont()` (BIOS call `BIOS_SYSFONTSET`) loads the system font
into VRAM tile slots 32-127.

**This font differs depending on the active language.**

### 4.1 English Mode

Slots 32-127 = ASCII glyphs `0x20-0x7F` (space, punctuation, A-Z, a-z, 0-9).

The slot index equals the ASCII character code:

```
slot 32 = ' ' (space)
slot 65 = 'A'
slot 97 = 'a'
```

`ngpc_text_print()` writes ASCII values directly into the tilemap — this works because
tile index == ASCII code.

### 4.2 Japanese Mode

The BIOS loads a different font. Slots 32-127 contain Japanese glyphs
(half-width Katakana) instead of full ASCII.

Standard half-width Katakana encoding (JIS X 0201):

```
0xA1 = small "a" katakana
0xA2 = "a" katakana
0xA3 = small "i" katakana
...
0xBF = "tsu" katakana
0xC4 = "na" katakana
...
0xDF = dakuten (voiced mark)
```

To display Katakana, `ngpc_text_print()` must receive a `const char*` containing
bytes in the range `0xA1-0xDF`.

**Important:** in cc900, string literals use signed `char`. Bytes > `0x7F` must be placed
in a `u8` array or cast explicitly:

```c
/* Katakana "START" — "S-TA-A-TO" in half-width Katakana */
static const u8 jp_start[] = { 0xBD, 0xC0, 0xC3, 0xC4, 0x00 };

ngpc_text_print(GFX_SCR1, 0, 5, 10, (const char *)jp_start);
```

> Basic ASCII characters (A-Z, 0-9) remain displayable in Japanese mode —
> the BIOS preserves ASCII glyphs in the lower portion of the font range (slots ~32-95).
> Verify on target hardware if mixing ASCII and Katakana.

---

## 5. Bilingual String Table Pattern

Recommended pattern for managing text in both languages: a pointer table indexed
by `ngpc_get_language()` (0 or 1).

### 5.1 Basic Pattern

```c
/* String tables — English [0], Japanese [1] */
static const char * const s_msg_title[2] = {
    "NGPC Game",       /* [0] LANG_ENGLISH  */
    "NGPC Game"        /* [1] LANG_JAPANESE (same title here) */
};

static const char * const s_msg_start[2] = {
    "Press A to start",  /* [0] LANG_ENGLISH  */
    "A: START"           /* [1] LANG_JAPANESE */
};

static const char * const s_msg_pause[2] = {
    "PAUSED",            /* [0] LANG_ENGLISH  */
    "PAUSE"              /* [1] LANG_JAPANESE */
};

/* Usage: */
u8 lang = ngpc_get_language();   /* 0 or 1 */
ngpc_text_print(GFX_SCR1, 0, col, row, s_msg_title[lang]);
ngpc_text_print(GFX_SCR1, 0, col, row, s_msg_start[lang]);
```

Advantages:
- **Zero overhead** — `lang` is a `u8`; `[lang]` indexing = 1 instruction
- **Extensible** — adding a language means adding a column
- **Readable** — both versions are side-by-side in the source
- **Safe** — no switch/if, no missing branch
- **cc900-compatible** — `const char * const [2]` tables work correctly

### 5.2 Organization for a Full Game

Group strings by screen or module:

```c
/* strings_title.c */
const char * const g_str_title_name[2]  = { "MY GAME",   "MY GAME"  };
const char * const g_str_title_start[2] = { "Press A",   "A BUTTON" };
const char * const g_str_title_opt[2]   = { "Options",   "OPTIONS"  };

/* strings_game.c */
const char * const g_str_game_score[2]  = { "SCORE",     "SCORE"    };
const char * const g_str_game_life[2]   = { "LIFE",      "LIFE"     };
const char * const g_str_game_over[2]   = { "GAME OVER", "GAME OVER"};

/* strings_menu.c */
const char * const g_str_yes[2]         = { "YES",       "YES"      };
const char * const g_str_no[2]          = { "NO",        "NO"       };
```

```c
/* strings.h — extern declarations */
extern const char * const g_str_title_name[2];
extern const char * const g_str_title_start[2];
/* ... */
```

Usage throughout the codebase:

```c
#include "strings.h"
u8 lang = ngpc_get_language();
ngpc_text_print(GFX_SCR1, 0, 5, 4, g_str_title_name[lang]);
```

### 5.3 Global Language Cache

For larger games, avoid passing `lang` as a parameter everywhere:

```c
/* game_state.h */
extern u8 g_lang;   /* 0=EN, 1=JP — copy of ngpc_get_language() */

/* main.c — after ngpc_init() */
u8 g_lang;

void main(void) {
    ngpc_init();
    g_lang = ngpc_get_language();   /* cache once, use everywhere */
    /* ... */
}

/* Anywhere in the game: */
ngpc_text_print(GFX_SCR1, 0, col, row, s_msg_start[g_lang]);
```

---

## 6. Advanced Patterns

### 6.1 Language-Dependent Asset Selection

Not just strings — some games ship different tiles or sprites per language
(logo tiles with embedded text, etc.):

```c
/* Tile data pointers indexed by language */
/* IMPORTANT: NGP_FAR required for cartridge ROM data at 0x200000+ */
static const u16 NGP_FAR * const s_logo_tiles[2] = {
    logo_en_tiles,    /* [0] LANG_ENGLISH  — defined in assets_en.c */
    logo_jp_tiles     /* [1] LANG_JAPANESE — defined in assets_jp.c */
};

static const u16 s_logo_tile_count[2] = {
    LOGO_EN_TILE_COUNT,
    LOGO_JP_TILE_COUNT
};

void title_init(void)
{
    u8 lang = ngpc_get_language();
    ngpc_gfx_load_tiles_at(s_logo_tiles[lang], s_logo_tile_count[lang], 128);
}
```

> **NGP_FAR reminder:** cartridge ROM data lives at `0x200000+`, out of reach for
> cc900's default 16-bit near pointers. Always use `NGP_FAR` for pointers to ROM tiles,
> palettes, and tilemaps.

### 6.2 Language and Save Data

**General rule: do not save the language.** Read it from the BIOS on every boot.
If the user changes the language in the BIOS menu, the game must adapt automatically.
Saving the language would create a stale override.

**Exception:** if the game provides an in-game language override in its Options screen,
then saving the choice makes sense:

```c
typedef struct {
    u8 magic[4];
    u8 lang_override;   /* 0xFF = use BIOS, 0=EN, 1=JP */
    /* ... */
} SaveData;

/* At init: */
u8 lang;
if (save.lang_override == 0xFF) {
    lang = ngpc_get_language();   /* use BIOS default */
} else {
    lang = save.lang_override;    /* user choice */
}
```

### 6.3 Inline Condition (No Table)

For occasional one-off strings, a direct conditional is acceptable:

```c
/* Simple, readable, zero overhead on TLCS-900H */
const char *confirm = (ngpc_get_language() == LANG_JAPANESE)
    ? "A: KETTEI"    /* approximate ASCII for Japanese context */
    : "A: Confirm";

/* Or explicit branch: */
if (ngpc_get_language() == LANG_JAPANESE) {
    ngpc_text_print(GFX_SCR1, 0, 2, 5, "A: KETTEI");
} else {
    ngpc_text_print(GFX_SCR1, 0, 2, 5, "A: Confirm");
}
```

Use the string table pattern (§5) for anything more than a few strings.

---

## 7. Pitfalls and Gotchas

### Pitfall 1 — Reading HW_LANGUAGE Before ngpc_init()

`runtime_bootstrap()` (called inside `ngpc_init()`) copies initialized data from ROM to RAM
and zeroes BSS. Static variables initialized to 0 are in BSS and are zeroed by this step.

`s_language = HW_LANGUAGE` is set **after** `runtime_bootstrap()` in `ngpc_init()`,
so the cache is safe. Do not read `HW_LANGUAGE` from user code before `ngpc_init()` completes.

Recommended initialization order:

```c
ngpc_init();           /* 1. reads and caches HW_LANGUAGE into s_language */
ngpc_load_sysfont();   /* 2. loads the font matching the current language */
/* 3. ngpc_get_language() and font tiles are now coherent */
```

### Pitfall 2 — String Literals with Bytes > 0x7F

```c
/* BAD: cc900 treats char as signed — 0xA2 = -94, may generate warnings */
const char *jp = "\xa2\xb3";

/* GOOD: use u8[] and cast */
static const u8 jp_text[] = { 0xA2, 0xB3, 0x00 };
ngpc_text_print(GFX_SCR1, 0, col, row, (const char *)jp_text);
```

### Pitfall 3 — Assuming Language Index Is Always 0 or 1

The hardware guarantees 0 or 1, but a buggy emulator or corrupted ROM could return
another value. Indexing a `[2]` array with a value > 1 causes an out-of-bounds read.

Optional defensive clamp (useful in debug builds):

```c
u8 ngpc_get_language(void)
{
    /* Clamp to maximum 1 to never overrun a [2] table */
    return (s_language <= LANG_JAPANESE) ? s_language : LANG_ENGLISH;
}
```

A minimal build may omit this clamp by default, but it is a valid option
for extra safety.

### Pitfall 4 — Calling ngpc_load_sysfont() Before Language Is Determined

`ngpc_load_sysfont()` loads a font **adapted to the current language**.
As long as `ngpc_init()` has been called first (which caches `HW_LANGUAGE`), the value is
already correct. No ordering problem in practice — just follow the standard init sequence
described in Pitfall 1.

---

## Quick Reference

| Item | Value / File | Notes |
|------|-------------|-------|
| Hardware address | `0x6F87` | Part of BIOS system area |
| Register macro | `HW_LANGUAGE` | `ngpc_hw.h` |
| English constant | `LANG_ENGLISH = 0u` | `ngpc_hw.h` |
| Japanese constant | `LANG_JAPANESE = 1u` | `ngpc_hw.h` |
| Cached API | `ngpc_get_language()` | Returns cached value |
| Cache init | `s_language = HW_LANGUAGE` in `ngpc_init()` | After runtime_bootstrap |
| Sysfont EN | ASCII `0x20-0x7F` in tile slots 32-127 | Tile index == ASCII code |
| Sysfont JP | Half-width Katakana `0xA1-0xDF` in slots 32-127 | JIS X 0201 |
| Japanese bytes | Use `u8[]` + `(const char *)` cast | Signed char issue in cc900 |
| String table | `const char * const msg[2] = { "EN", "JP" };` | Index by `ngpc_get_language()` |
| Asset table | `const u16 NGP_FAR * const tiles[2]` | NGP_FAR required for ROM data |
| Save language? | No — read from BIOS each boot | Unless in-game override needed |
| Out-of-bounds guard | Clamp to 1: `(lang <= 1) ? lang : 0` | Optional, defensive |

Minimal bilingual ROM pattern:

```c
/* After ngpc_init() + ngpc_load_sysfont(): */
u8 lang = ngpc_get_language();   /* 0=EN, 1=JP */

static const char * const s_msg[2] = { "Press A", "A BUTTON" };
ngpc_text_print(GFX_SCR1, 0, col, row, s_msg[lang]);
```

---

## See Also

- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — Full BIOS system area map (`0x6F80-0x6F91`)
- [BIOS](../01_Hardware/BIOS.md) — `BIOS_SYSFONTSET`, system font loading
- [Effects and Raster](../03_Graphics/Effects-and-Raster.md) — `ngpc_text_print()` API, tile slot layout, sysfont usage
- [Storage and Saves](Storage-and-Saves.md) — Flash save, saving language override in SaveData
- [Game Loop](Game-Loop.md) — `ngpc_init()` sequence, runtime_bootstrap, init order
