# NGPC Template 2026

A modern, open-source development template for the **Neo Geo Pocket Color** handheld console.
Written from scratch using the public hardware specification. No legacy code, no binary blobs.

**License:** MIT (use it for anything, commercial or not)

---

## Sommaire

**Setup**
- [Docs du projet](#project-docs) · [Hardware overview](#hardware-overview) · [Structure du projet](#project-structure) · [Prérequis](#prerequisites) · [Build](#build)

**Modules — Système**
| Module | Rôle |
|---|---|
| [ngpc_sys](#ngpc_sys----system) | Init hardware, VBlank, memcpy/memset |
| [ngpc_vramq](#ngpc_vramq----queued-vram-updates) | File d'attente VRAM (flush auto au VBlank) |
| [ngpc_timing](#ngpc_timing----timing) | vsync, sleep, vitesse CPU |
| [ngpc_input](#ngpc_input----joypad) | Joypad : held / pressed / released / repeat |
| [ngpc_flash](#ngpc_flash----save) | Sauvegarde 256 octets en flash cart |
| [ngpc_rtc](#ngpc_rtc----real-time-clock) | Horloge temps réel (BCD) |

**Modules — Graphismes**
| Module | Rôle |
|---|---|
| [ngpc_gfx](#ngpc_gfx----graphics) | Tiles, tilemap, palettes, scroll, effets écran |
| [ngpc_sprite](#ngpc_sprite----sprites) | Sprites hardware (64 max, 8x8, flip, chain) |
| [ngpc_text](#ngpc_text----text) | Affichage texte/chiffres avec la sysfont |
| [ngpc_bitmap](#ngpc_bitmap----bitmap-mode) | Mode bitmap émulé (380 tiles, pixels directs) |
| [ngpc_metasprite](#ngpc_metasprite----multi-tile-sprites) | Multi-sprites (jusqu'à 16 parts), animation |
| [ngpc_sprmux](#ngpc_sprmux----sprite-multiplexing) | Multiplexage sprites (>64 logiques) via HBlank |
| [ngpc_palfx](#ngpc_palfx----palette-effects) | Fade, flash, cycle couleur (4 effets simultanés) |
| [ngpc_raster](#ngpc_raster----hblank-raster-effects) | Effets raster HBlank (scroll par ligne, parallax) |
| [ngpc_dma](#ngpc_dma----microdma-hardware-validated) | MicroDMA (hardware OK) — tables scanline vers registres |
| [ngpc_dma_raster](#ngpc_dma_raster----raster-via-microdma) | Effets raster via MicroDMA (sans ISR HBlank CPU) |

**Modules — Utilitaires**
| Module | Rôle |
|---|---|
| [ngpc_math](#ngpc_timing----timing) | Sin/cos LUT, RNG, mul32 |
| [ngpc_lut](#ngpc_lut----lookup-tables-fast-math) | atan2, sqrt, distance, division rapide |
| [ngpc_lz](#ngpc_lz----tile-decompression) | Décompression RLE / LZ77 vers tiles |
| [ngpc_debug](#ngpc_debug----cpu-profiler) | Profiler CPU (barre verte/jaune/rouge) |
| [ngpc_log](#ngpc_log----ring-buffer-debug-log) | Ring buffer de logs (sur hardware sans serial) |
| [ngpc_assert](#ngpc_assert----runtime-assert-helper) | Assert runtime (strippé en release) |

**Outils Python**
| Outil | Rôle |
|---|---|
| `ngpc_tilemap.py` | PNG → tiles + tilemap + palettes C |
| `ngpc_sprite_export.py` | Spritesheet → NgpcMetasprite + animations C |
| `ngpc_compress.py` | Compresseur RLE/LZ77 offline → `.c` |
| `ngpc_project_init.py` | Bootstrap nouveau projet depuis le template |

**Patterns**
- [Ajouter des assets](#adding-your-own-assets) · [State machine](#state-machine-pattern) · [Object pool](#object-pool-pattern) · [Contraintes hardware](#hardware-constraints-to-keep-in-mind)

**Modules optionnels** (`optional/` — copier au cas par cas dans `src/`) → [documentation complète](optional/README.md)
| Module | RAM | Rôle |
|---|---|---|
| [`ngpc_fixed`](optional/README.md#ngpc_fixed--math-fixe-point-84) | 0 | Math fixe-point 8.4, `FxVec2`, `FX_LERP`, physique subpixel |
| [`ngpc_aabb`](optional/README.md#ngpc_aabb--collision-rectangles) | 0 | Collision AABB, détection de côté, swept test (projectiles) |
| [`ngpc_camera`](optional/README.md#ngpc_camera--caméra) | ~10 o | Caméra monde→écran, suivi lisse, clamp niveau |
| [`ngpc_tilecol`](optional/README.md#ngpc_tilecol--collision-tilemap) | 0 | `tilecol_move()`, one-way, damage, ladder, distance sol |

**Son**
- [Sound driver](#sound-driver-srcaudio) — Init · BGM · SFX · Opcodes · Debug

---

## Project docs

**Références techniques** (`docs/`) — toute la connaissance hardware condensée, utilisable sans RAG :
- [`docs/NGPC_CC900_GUIDE.md`](docs/NGPC_CC900_GUIDE.md): compilateur cc900, règles C89, far pointers, inline asm
- [`docs/NGPC_BIOS_REF.md`](docs/NGPC_BIOS_REF.md): appels BIOS, conventions bank 3, tous les vecteurs
- [`docs/NGPC_HW_REGISTERS.md`](docs/NGPC_HW_REGISTERS.md): registres K2GE, palette, sprites, tilemap, interruptions

**Autres docs du projet :**
- `ROADMAP.md`: status, phases, priorities, release plan
- `SOURCES.md`: technical source audit (T900/NGPC docs, register proofs, open points)
- `TEST_PLAN_DMA_SPRMUX.md`: validation plan for `ngpc_dma` and `ngpc_sprmux` (to run on a copy)
- `DOC_INTRO_DISPLAY_WORKING_PATH.md`: known-good full-screen intro/background display path (safe VRAM blit)
- `examples/object_pool_example.c`: fixed-size object pool pattern (bullets/entities)
- `examples/ASSET_PIPELINE.md`: end-to-end asset workflow (tilemap -> compress -> runtime load)
- `examples/dma_example.c`: MicroDMA usage patterns (Timer0/Timer1, re-arm, no CHAIN)
- `examples/dma_raster_example.c`: ngpc_dma_raster parallax example (no CPU HBlank ISR)

---

## What is this?

A clean-room reimplementation of an NGPC development framework, organized into
small, independent modules. Each module handles one concern (graphics, input,
sound, etc.) and can be included or excluded from the build as needed.

The template ships with a working **Hello World** demo that shows:
- Hardware initialization and 60 fps game loop
- State machine architecture (title screen / game screen)
- Text rendering with the system font
- Tile loading from external asset files
- Joypad input with edge detection (pressed / released / held)
- Frame counter display
- Flash save/load validation controls (`A=save`, `OPTION=load`) in game state

### Current baseline status (2026-02-14)

- Build pipeline is stable (`make clean`, `make`, `make move_files`).
- Runtime is self-contained (no mandatory external `system.lib`).
- ROM output includes `main.ngc` (color workflow) and `main.ngp` (same bytes).
- Demo text path was stabilized (sysfont mapping, tilemap packing fix, deterministic screen init).
- VRAM queue module added (`ngpc_vramq`), auto-flushed from VBlank ISR.
- Assert/log helpers added (`ngpc_assert`, `ngpc_log`) with compile-time strip mode.
- Next major focus is hardware validation for `ngpc_dma` and `ngpc_sprmux` on a template copy.

---

## Hardware overview

| Component | Spec |
|---|---|
| Main CPU | Toshiba TLCS-900/H, 6.144 MHz |
| Sound CPU | Z80 compatible, 3.072 MHz |
| Sound chip | T6W28 PSG (3 square wave + 1 noise) |
| Display | 160 x 152 pixels, 4096 colors (12-bit RGB) |
| Tiles | 512 tiles, 8x8 px, 2 bits/pixel (4 colors) |
| Scroll planes | 2 independent planes, 32x32 tiles each |
| Sprites | 64 max, 8x8 px, flip/chain/priority |
| Work RAM | 12 KB (tight!) |
| Cart ROM | 2 MB flash |
| Save | 256 bytes in cart flash |
| Frame rate | ~60 Hz (VBlank driven) |

---

## Project structure

```
NGPC_Template__2026/
|
|-- src/                    Engine code (hardware abstraction)
|   |-- main.c              Entry point + demo (state machine)
|   |-- core/               System/base modules (types, hw, sys, input, timing, flash)
|   |-- gfx/                Graphics/gameplay rendering modules
|   |-- fx/                 Advanced modules (raster, DMA, sprmux, LUT, compression)
|   +-- audio/              Sound driver (custom T6W28 PSG driver)
|       |-- sounds.c/h      Z80 driver, BGM streaming, SFX engine
|       +-- sounds_game_sfx_template.c  Example SFX mapping
|
|-- sound/                  Sound assets (BGM streams, SFX, note tables)
|   |-- sound_data.h        Extern declarations for sound arrays
|   +-- sound_data.c        Placeholder melody (replace with your data)
|
|-- GraphX/                 Graphics assets (tiles, sprites, palettes)
|   |-- gfx_data.h          Extern declarations for tile/sprite arrays
|   +-- gfx_data.c          Example tiles (replace with your graphics)
|
|-- ngpc.lcf                Linker script (memory layout)
|-- makefile                Build rules
|-- build.bat               Windows build + emulator launch
|-- build/                  Intermediate objects (.rel in build/obj/)
|-- LICENSE                 MIT
+-- bin/                    Build output (main.ngp + main.ngc alias)
```

**Key principle:** code lives in `src/`, data lives in `sound/` and `GraphX/`.
Adding new assets never requires editing engine code.

---

## Prerequisites

You need the Toshiba TLCS-900/H toolchain:

| Tool | Purpose |
|---|---|
| `cc900` | C compiler for TLCS-900/H |
| `tulink` | Linker |
| `tuconv` | Format converter (ABS to S-record) |
| `s242ngp` | S-record to NGP cartridge ROM |
| `system.lib` | Optional compatibility runtime library |
| `Python 3.11` (`py`) | Build helpers used by make targets |

Install them somewhere (e.g. `C:\ngpcbins\T900`) and edit the path in `build.bat`.

Optional:
- `NGPRomResize.exe` or `.py` in a `utils/` folder (pads ROM to 2 MB for flash carts)
- An NGPC emulator (NeoPop, Mednafen, etc.)

---

## Build

### Windows

1. Edit `build.bat`: set `compilerPath` to your toolchain location
2. Optional: set `systemLibPath` only if you want compatibility mode with external `system.lib`
3. Run `build.bat`
4. Output: `bin/main.ngp` and `bin/main.ngc` (same ROM bytes, different extension)

### Manual (any platform)

```
export THOME=/path/to/T900
export PATH=$PATH:$THOME/bin
make clean && make && make move_files
```

If you want to force an external compatibility library, pass `SYSTEM_LIB` explicitly:

```bash
make clean
make SYSTEM_LIB=/absolute/path/to/system.lib
make move_files
```

Build notes (2026-02-14):
- `make clean` and `make move_files` use `tools/build_utils.py` (cross-platform, no `rm/mv` dependency).
- Compilation is launched from project root (keeps `thc1/thc2` resolution stable) with include roots `-Isrc -Isrc/core -Isrc/gfx -Isrc/fx -Isrc/audio`.
- Template links without external `system.lib` (internal runtime replacement).
- `SYSTEM_LIB` remains available for compatibility when needed.
- `move_files` generates a `.ngc` alias from `.ngp` for color workflow/emulator compatibility.
- `build.bat` now skips ROM resize/emulator launch gracefully if required executables are missing.
- Current demo baseline keeps game text on `SCR1`; `SCR2` is cleared in title/game init.
- Feature flags are centralized in `src/core/ngpc_config.h` and mirrored by make variables.
- Source tree is split by domain (`src/core`, `src/gfx`, `src/fx`, `src/audio`).
- Compiler objects (`.rel`) are emitted to `build/obj/` (keeps `src/` clean).

Feature flag examples:

```bash
# Enable DMA + SPRMUX test build
make NGP_ENABLE_DMA=1 NGP_ENABLE_SPRMUX=1

# Minimal build: no sound, no flash save, no debug helpers
make NGP_ENABLE_SOUND=0 NGP_ENABLE_FLASH_SAVE=0 NGP_ENABLE_DEBUG=0

# Strip assert/log macros for release profile
make NGP_PROFILE_RELEASE=1
```

---

## Module reference

### ngpc_sys -- System

```c
void ngpc_init(void);           // Call first. Sets up interrupts, viewport.
u8   ngpc_is_color(void);       // 1 = NGPC Color, 0 = mono NGP
void ngpc_shutdown(void);       // Power off (BIOS call)
void ngpc_load_sysfont(void);   // Load built-in font into tile RAM
void ngpc_memcpy(dst, src, n);  // Byte copy
void ngpc_memset(dst, val, n);  // Byte fill

extern volatile u8 g_vb_counter; // Frame counter (incremented at 60 Hz)
```

The VBlank interrupt handler is installed automatically by `ngpc_init()`.
It clears the watchdog, checks for shutdown requests, and increments `g_vb_counter`.

### ngpc_vramq -- Queued VRAM Updates

```c
void ngpc_vramq_init(void);                     // Reset queue state
u8   ngpc_vramq_copy(dst, src, len_words);     // Queue u16 copy
u8   ngpc_vramq_fill(dst, value, len_words);   // Queue u16 fill
void ngpc_vramq_flush(void);                    // Flush queued commands
void ngpc_vramq_clear(void);                    // Drop pending commands
u8   ngpc_vramq_pending(void);                  // Pending command count
u8   ngpc_vramq_dropped(void);                  // Rejected command count
void ngpc_vramq_clear_dropped(void);            // Reset drop counter
```

Queue writes in gameplay code, then let VBlank flush them safely.
`ngpc_sys` calls `ngpc_vramq_flush()` automatically each VBlank.

Notes:
- `len_words` is in `u16` units (not bytes).
- Destination must be inside VRAM (`0x8000-0xBFFF`).
- Queue size is fixed (`VRAMQ_MAX_CMDS`), currently 16 commands.

### ngpc_gfx -- Graphics

```c
// Tiles
void ngpc_gfx_load_tiles(tiles, count);             // Load at tile 0
void ngpc_gfx_load_tiles_at(tiles, count, offset);  // Load at tile N

// Scroll plane tile map
void ngpc_gfx_put_tile(plane, x, y, tile, pal);     // Place one tile (no flip)
void ngpc_gfx_put_tile_ex(plane, x, y, tile, pal, hflip, vflip); // With flip
void ngpc_gfx_get_tile(plane, x, y, *tile, *pal);   // Read back tile/palette
void ngpc_gfx_clear(plane);                          // Clear whole plane
void ngpc_gfx_fill(plane, tile, pal);                // Fill with one tile

// Palettes (4 colors each, 16 per plane)
void ngpc_gfx_set_palette(plane, id, c0, c1, c2, c3);

// Scroll
void ngpc_gfx_scroll(plane, x, y);     // Set scroll offset
void ngpc_gfx_swap_planes(void);        // Toggle which plane is in front

// Screen
void ngpc_gfx_set_bg_color(color);      // Background color (RGB macro)
void ngpc_gfx_set_viewport(x, y, w, h); // Window area

// Screen effects (hardware features, new in 2026 template)
void ngpc_gfx_sprite_offset(dx, dy);    // Offset ALL sprites (screen shake)
void ngpc_gfx_lcd_invert(enable);       // Invert LCD (negative photo effect)
void ngpc_gfx_set_outside_color(idx);   // Color outside viewport (letterbox)
u8   ngpc_gfx_char_over(void);          // Detect sprite/tile overload

// Software tile rotation (90 degrees, not available in hardware)
void ngpc_tile_rotate90(src, dst);              // 90 CW in RAM buffer
void ngpc_tile_rotate270(src, dst);             // 90 CCW in RAM buffer
void ngpc_tile_rotate90_to(src, dest_tile_id);  // 90 CW -> direct to VRAM
void ngpc_tile_rotate270_to(src, dest_tile_id); // 90 CCW -> direct to VRAM
```

**Planes:** `GFX_SCR1`, `GFX_SCR2`, `GFX_SPR`
**Colors:** use `RGB(r, g, b)` macro (each channel 0-15)

#### Safe background blit (recommended for full-screen PNG tilemaps)

If you generate a PNG tilemap in `GraphX/` with `tools/ngpc_tilemap.py` and the
background renders corrupted/truncated, prefer the known-good "windjammer-style"
VRAM blit path:

- Include `src/gfx/ngpc_tilemap_blit.h`
- Call `NGP_TILEMAP_BLIT_SCR1(asset_prefix, tile_base)` (or `_SCR2`)

This path writes tiles directly to Character RAM (`0xA000`) and writes the scroll
map entries directly to `HW_SCR1_MAP`/`HW_SCR2_MAP`.

Note: the template also configures cc900 `__far` pointer handling for the tile-load
helpers (see `ngpc_gfx_load_tiles_at()`), but `NGP_TILEMAP_BLIT_*` remains the
most direct/safe path for large static backgrounds.

#### Tile orientations

The NGPC hardware supports H-flip and V-flip for scroll tiles and sprites.
Combined with the software 90-degree rotation, all **8 orientations** are possible:

| Orientation | How |
|---|---|
| 0Â° (original) | Direct tile |
| 90Â° CW | `ngpc_tile_rotate90` |
| 180Â° | Hardware H-flip + V-flip (free, use `put_tile_ex`) |
| 270Â° CW | `ngpc_tile_rotate270` |
| Mirror H | Hardware H-flip |
| Mirror V | Hardware V-flip |
| Mirror H + 90Â° | Rotate 90 then H-flip |
| Mirror V + 90Â° | Rotate 90 then V-flip |

Software rotation rearranges pixel data and costs ~64 iterations per tile.
Best used at **load time** (pre-rotate once), not per-frame on a 6 MHz CPU.

### ngpc_sprite -- Sprites

```c
void ngpc_sprite_set(id, x, y, tile, pal, flags);  // Set all attributes
void ngpc_sprite_move(id, x, y);                    // Move only
void ngpc_sprite_hide(id);                          // Hide one sprite
void ngpc_sprite_hide_all(void);                    // Hide all 64
void ngpc_sprite_set_flags(id, flags);              // Change flip/priority only
void ngpc_sprite_set_tile(id, tile);                // Change tile only
u8   ngpc_sprite_get_pal(id);                       // Read back palette
```

**Flags** (combine with `|`):
- Priority: `SPR_FRONT`, `SPR_MIDDLE`, `SPR_BEHIND`, `SPR_HIDE`
- Flip: `SPR_HFLIP`, `SPR_VFLIP`, `SPR_HVFLIP`
- Chain: `SPR_HCHAIN`, `SPR_VCHAIN`

Note: sprites support hardware H/V flip. For 90-degree rotation of sprites,
pre-rotate the tile data with `ngpc_tile_rotate90` and load it as a separate tile.

### ngpc_text -- Text

```c
void ngpc_text_print(plane, pal, x, y, "string");
void ngpc_text_print_dec(plane, pal, x, y, value, digits);  // 0-65535, zero-padded
void ngpc_text_print_num(plane, pal, x, y, value, digits);  // 0-65535, space-padded
void ngpc_text_print_hex(plane, pal, x, y, value, digits);  // hex 16-bit
void ngpc_text_print_hex32(plane, pal, x, y, value);        // hex 32-bit (8 digits)
void ngpc_text_tile_screen(plane, pal, map);                 // fill 20x19 from array
```

Requires `ngpc_load_sysfont()` to have been called first.
Printable ASCII uses tile indices `0x20-0x7F`. Load custom tiles outside this range
(recommended offset: 128+).

### ngpc_input -- Joypad

```c
void ngpc_input_update(void);   // Call once per frame

extern u8 ngpc_pad_held;        // Buttons currently down
extern u8 ngpc_pad_pressed;     // Buttons just pressed (this frame)
extern u8 ngpc_pad_released;    // Buttons just released (this frame)
extern u8 ngpc_pad_repeat;      // Buttons auto-repeated (menu navigation)

void ngpc_input_set_repeat(u8 delay, u8 rate); // delay/rate in frames
```

**Button masks:** `PAD_UP`, `PAD_DOWN`, `PAD_LEFT`, `PAD_RIGHT`,
`PAD_A`, `PAD_B`, `PAD_OPTION`, `PAD_POWER`

```c
// Example: react to new A press only (not held)
if (ngpc_pad_pressed & PAD_A) { /* fire! */ }

// Example: menu navigation with auto-repeat (after 15f, every 4f)
ngpc_input_set_repeat(15, 4);
if ((ngpc_pad_pressed | ngpc_pad_repeat) & PAD_DOWN) { /* next item */ }
if ((ngpc_pad_pressed | ngpc_pad_repeat) & PAD_UP)   { /* prev item */ }
```

### ngpc_timing -- Timing

```c
void ngpc_vsync(void);          // Wait for next VBlank (~60 Hz)
u8   ngpc_in_vblank(void);      // Check if currently in VBlank
void ngpc_sleep(u8 frames);     // Pause N frames (low power)
void ngpc_cpu_speed(u8 div);    // 0=6MHz, 1=3MHz, ..., 4=384KHz
```

### ngpc_math -- Math

```c
s8   ngpc_sin(u8 angle);        // Sin lookup, angle 0-255, returns -127..127
s8   ngpc_cos(u8 angle);        // Cos lookup (same range)
void ngpc_rng_seed(void);       // Seed RNG from VBCounter
u16  ngpc_random(u16 max);      // Random 0..max (LCG, good quality)
s32  ngpc_mul32(s32 a, s32 b);  // 32-bit signed multiply

// Quick random (table-based, ultra-fast)
void ngpc_qrandom_init(void);   // Shuffle table (call after rng_seed)
u8   ngpc_qrandom(void);        // Returns 0-255 from pre-shuffled table
```

Angles: 0 = 0 deg, 64 = 90 deg, 128 = 180 deg, 192 = 270 deg, 256 wraps to 0.

`ngpc_qrandom()` is a zero-cost random: just a table read + index increment.
Use it for non-critical randomness (particles, screen shake, tile variation).
For proper RNG (game logic, procedural generation), use `ngpc_random()`.

Limite de `ngpc_random` : extrait les bits 16-30 du LCG → résultat dans **0..32767** quel que
soit `max`. Pour `max > 32767`, la valeur ne dépassera jamais 32767. Si tu as besoin de
grands nombres aléatoires, combiner deux appels : `ngpc_random(255) | (ngpc_random(127) << 8)`.

### ngpc_flash -- Save

```c
void ngpc_flash_init(void);             // Call at startup
void ngpc_flash_save(const void *data); // Write 256 bytes to flash
void ngpc_flash_load(void *data);       // Read 256 bytes from flash
u8   ngpc_flash_exists(void);           // Check if valid save exists
```

Flash has limited write cycles. Avoid saving every frame.

**IMPORTANT — magic number** : `ngpc_flash_exists()` vérifie les 4 premiers octets
de la zone de sauvegarde. Le struct de save DOIT commencer par `{ 0xCA, 0xFE, 0x20, 0x26 }`.

```c
// Struct de save avec le magic en premier champ
typedef struct {
    u8 magic[4];   /* toujours { 0xCA, 0xFE, 0x20, 0x26 } */
    u8 hp;
    u8 level;
    /* ... jusqu'a 252 autres octets */
} SaveData;

// Sauvegarder (sur pression d'un bouton, pas chaque frame !)
void save_game(void) {
    SaveData s;
    s.magic[0] = 0xCA; s.magic[1] = 0xFE;
    s.magic[2] = 0x20; s.magic[3] = 0x26;
    s.hp    = player.hp;
    s.level = player.level;
    ngpc_flash_save(&s);
}

// Charger au demarrage
void load_game(void) {
    if (ngpc_flash_exists()) {
        SaveData s;
        ngpc_flash_load(&s);
        player.hp    = s.hp;
        player.level = s.level;
    }
}
```

Implementation status (2026-02-14):
- `ngpc_flash_save()` is fully wired to BIOS flash calls (erase + write).
- Uses `VECT_FLASHERS` (erase block) then `VECT_FLASHWRITE` (write 256 bytes).
- Default save slot for 2MB ROM layout uses flash offset `0x1FA000`
  (CPU-visible address `0x200000 + 0x1FA000 = 0x3FA000`).
- BIOS vector IDs are centralized in `ngpc_hw.h` and used by modules (`sys`, `timing`, `rtc`, `flash`).

### ngpc_bitmap -- Bitmap mode

```c
void ngpc_bmp_init(plane, tile_offset, pal);  // Setup (allocates 380 tiles)
void ngpc_bmp_pixel(x, y, color);             // Set pixel (0-3)
u8   ngpc_bmp_get_pixel(x, y);                // Read pixel back
void ngpc_bmp_clear(void);                    // Clear all pixels
void ngpc_bmp_line(x1, y1, x2, y2, color);   // Bresenham line
void ngpc_bmp_rect(x, y, w, h, color);        // Rectangle outline
void ngpc_bmp_fill_rect(x, y, w, h, color);   // Filled rectangle
void ngpc_bmp_hline(x, y, w, color);          // Fast horizontal line
void ngpc_bmp_vline(x, y, h, color);          // Vertical line
```

The NGPC has no hardware bitmap mode. This module emulates one by assigning
380 unique tiles (20x19) to fill the screen, then writing pixels directly
into tile RAM. No flush needed - pixels appear immediately.

**Tile budget:** 380 of 512 tiles used. 132 remaining for text/sprites.
**RAM cost:** zero (all writes go straight to VRAM).

```c
// Example: draw a diagonal line
ngpc_bmp_init(GFX_SCR1, 0, 0);
ngpc_gfx_set_palette(GFX_SCR1, 0, RGB(0,0,0), RGB(15,0,0), RGB(0,15,0), RGB(15,15,15));
ngpc_bmp_line(0, 0, 159, 151, 1);  // red diagonal
```

### ngpc_rtc -- Real-Time Clock

```c
void ngpc_rtc_get(NgpcTime *t);                     // Read date/time (BCD)
void ngpc_rtc_set_alarm(NgpcAlarm *a);               // Alarm during gameplay
void ngpc_rtc_set_wake(NgpcAlarm *a);                // Wake-up alarm (powers on)
void ngpc_rtc_set_alarm_handler(void (*handler)(void)); // Install ISR

// BCD helpers
u8 bin = BCD_TO_BIN(bcd_value);  // 0x23 -> 23
u8 bcd = BIN_TO_BCD(23);         // 23 -> 0x23
```

The NGPC has a battery-backed RTC. All values are **BCD-encoded** (0x12 = twelve, not 18).
Use `BCD_TO_BIN()` to convert to normal integers.

```c
// Example: display current time
NgpcTime t;
ngpc_rtc_get(&t);
u8 hour = BCD_TO_BIN(t.hour);
u8 min  = BCD_TO_BIN(t.minute);
```

### ngpc_debug -- CPU Profiler

```c
void ngpc_debug_begin(void);                         // Mark start of logic
void ngpc_debug_end(void);                           // Mark end of logic
void ngpc_debug_draw_bar(plane, pal_ok, pal_warn, pal_over); // Color bar
void ngpc_debug_print_pct(plane, pal, x, y);         // Print "XX%"
void ngpc_debug_print_fps(plane, pal, x, y);         // Print "XXFPS"
u8   ngpc_debug_get_lines(void);                     // Raw scanlines used
u8   ngpc_debug_get_pct(void);                       // CPU usage 0-100+
```

Measures game logic time via the hardware raster position register.
The bar turns **green** (< 80%), **yellow** (80-100%), or **red** (> 100% = overflow).

Disable for release: `#define NGPC_DEBUG 0` (all calls become no-ops).

```c
// Typical game loop
ngpc_vsync();
ngpc_input_update();
ngpc_debug_begin();
  game_update();
ngpc_debug_end();
ngpc_debug_draw_bar(GFX_SCR2, PAL_GREEN, PAL_YELLOW, PAL_RED);
```

### ngpc_log -- Ring Buffer Debug Log

```c
void ngpc_log_init(void);
void ngpc_log_clear(void);
void ngpc_log_hex(const char *label, u16 value);
void ngpc_log_str(const char *label, const char *str);
void ngpc_log_dump(plane, pal, x, y);
u8   ngpc_log_count(void);

// Convenience macros
NGPC_LOG_HEX("PAD", ngpc_pad_held);
NGPC_LOG_STR("ST", "RUN ");
```

Stores short diagnostic entries in a fixed-size ring buffer (~288 bytes RAM).
Useful on hardware where stdout/serial isn't available.

### ngpc_assert -- Runtime Assert Helper

```c
NGPC_ASSERT(pointer != 0);
```

In debug builds, assertion failure shows an on-screen fault page and loops
while blinking the background. In release profile, asserts are compiled out.

Build toggle:
- `#define NGP_PROFILE_RELEASE 1` before including headers
  to strip `NGPC_ASSERT` and `NGPC_LOG_*` macros.

### ngpc_metasprite -- Multi-tile Sprites

```c
// Draw a metasprite (returns number of hw sprites used)
u8 ngpc_mspr_draw(spr_start, x, y, def, flags);
void ngpc_mspr_hide(spr_start, count);

// Animation
void ngpc_mspr_anim_start(animator, anim_table, count, loop);
const NgpcMetasprite *ngpc_mspr_anim_update(animator);
u8 ngpc_mspr_anim_done(animator);
```

A `NgpcMetasprite` defines up to **16 parts** (8x8 sprites) with relative offsets.
Group-level `SPR_HFLIP`/`SPR_VFLIP` automatically swaps quad layout and toggles
per-part flips. No need for separate left/right sprite definitions.

```c
// Define a 16x16 character (4 parts)
const NgpcMetasprite player_idle = {
    4,          /* count */
    16, 16,     /* width, height (for flip calculation) */
    {
        { 0, 0, 100, 0, SPR_FRONT },   /* top-left     */
        { 8, 0, 101, 0, SPR_FRONT },   /* top-right    */
        { 0, 8, 102, 0, SPR_FRONT },   /* bottom-left  */
        { 8, 8, 103, 0, SPR_FRONT },   /* bottom-right */
    }
};

// Draw facing right
ngpc_mspr_draw(0, px, py, &player_idle, SPR_FRONT);

// Draw facing left (automatic quad swap)
ngpc_mspr_draw(0, px, py, &player_idle, SPR_FRONT | SPR_HFLIP);
```

### ngpc_sprmux -- Sprite Multiplexing

```c
void ngpc_sprmux_begin(void);                                  // Start frame list
u8   ngpc_sprmux_add(s16 x, s16 y, u16 tile, u8 pal, u8 flags); // Add logical sprite
void ngpc_sprmux_flush(void);                                  // Sort + start HBlank mux
void ngpc_sprmux_disable(void);                                // Stop mux + hide all
u8   ngpc_sprmux_overflow_count(void);                         // Dropped sprites count
```

Allows more than 64 logical sprites by reusing hardware slots while the screen is
being drawn. Sprites are sorted by Y each frame and reassigned on HBlank when they
leave visibility.

Important limitations:
- Assumes 8x8 sprites (no chain handling in the multiplexer core).
- Uses **Timer 0 HBlank interrupt** and therefore conflicts with `ngpc_raster`.
- If too many sprites overlap vertically on the same scanlines, some are dropped;
  read `ngpc_sprmux_overflow_count()` for diagnostics.
- Hardware timing validation is still pending; see `TEST_PLAN_DMA_SPRMUX.md`.

Typical frame usage:

```c
ngpc_sprmux_begin();
/* add all logical sprites for this frame */
ngpc_sprmux_add(px, py, tile, pal, SPR_FRONT);
/* ... */
ngpc_sprmux_flush();
```

### ngpc_palfx -- Palette Effects

```c
// Fade
u8 ngpc_palfx_fade(plane, pal_id, target_colors, speed);
u8 ngpc_palfx_fade_to_black(plane, pal_id, speed);
u8 ngpc_palfx_fade_to_white(plane, pal_id, speed);

// Cycle (water/lava/rainbow)
u8 ngpc_palfx_cycle(plane, pal_id, speed);

// Flash (damage/selection)
u8 ngpc_palfx_flash(plane, pal_id, color, duration);

// Control
void ngpc_palfx_update(void);     // Call once per frame
void ngpc_palfx_stop(slot);       // Stop + restore original
void ngpc_palfx_stop_all(void);
u8   ngpc_palfx_active(slot);     // Check if running
```

Up to **4 simultaneous effects**. Fade interpolates each R/G/B channel independently.
Cycle rotates colors 1-2-3 (color 0 = transparent, untouched).

Cas limites :
- `speed=0` dans fade/cycle → clamped à 1 (minimum). `speed=1` = 1 step par frame (le plus rapide).
- `ngpc_palfx_flash(..., duration=0)` → retourne `0xFF` (aucun effet créé, no-op).

```c
// Fade to black on screen transition
ngpc_palfx_fade_to_black(GFX_SCR1, 0, 2);  // speed 2 = ~0.5s
while (ngpc_palfx_active(0)) {
    ngpc_vsync();
    ngpc_palfx_update();
}

// Flash de dommage : 6 frames en blanc
ngpc_palfx_flash(GFX_SPR, player_pal, RGB(15,15,15), 6);

// Water animation
ngpc_palfx_cycle(GFX_SCR1, WATER_PAL, 8);  // rotate every 8 frames
```

### ngpc_raster -- HBlank Raster Effects

```c
void ngpc_raster_init(void);                          // Install HBlank ISR
void ngpc_raster_disable(void);                       // Remove ISR

// Scroll table mode (per-scanline scroll)
void ngpc_raster_set_scroll_table(plane, table_x, table_y);
void ngpc_raster_clear_scroll(void);

// Callback mode (custom per-line code)
u8   ngpc_raster_set_callback(line, callback);
void ngpc_raster_clear_callbacks(void);

// Convenience: parallax
void ngpc_raster_parallax(plane, bands, count, base_x);
```

Changes video registers **mid-frame** via the Timer 0 HBlank interrupt.
The K2GE applies changes immediately on the next scanline.

**WARNING:** the HBlank ISR must be extremely fast (~5 us per scanline).
Only write 1-2 registers per HBlank.

```c
// 3-layer parallax scrolling
RasterBand layers[] = {
    {   0,  64 },   // sky:    0.25x speed (lines 0-49)
    {  50, 128 },   // trees:  0.50x speed (lines 50-99)
    { 100, 256 },   // ground: 1.00x speed (lines 100-151)
};
ngpc_raster_init();
// In game loop:
ngpc_raster_parallax(GFX_SCR1, layers, 3, camera_x);
```

### ngpc_dma -- MicroDMA (hardware-validated)

```c
void ngpc_dma_init(void);   // Init DMA (optional completion ISRs)

void ngpc_dma_start_table_u8(channel, dst_reg, src_table, count, start_vector);
void ngpc_dma_link_hblank(channel, dst_reg, src_table, count); // start_vector=0x10
void ngpc_dma_link_vblank(channel, dst_reg, src_table, count); // start_vector=0x0B (unsafe; blocked by default)

typedef struct { /* ... */ } NgpcDmaHblankStream; // see src/fx/ngpc_dma.h
void ngpc_dma_hblank_stream_begin(NgpcDmaHblankStream *s,
                                  u8 channel,
                                  volatile u8 NGP_FAR *dst_reg,
                                  const u8 NGP_FAR *src_table,
                                  u16 count);
void ngpc_dma_hblank_stream_rearm(const NgpcDmaHblankStream *s); // call once per frame (during VBlank)
void ngpc_dma_hblank_stream_end(NgpcDmaHblankStream *s);

// Timer helpers (no CPU HBlank ISR):
void ngpc_dma_timer0_hblank_enable(void);
void ngpc_dma_timer0_hblank_disable(void);
void ngpc_dma_timer01_hblank_enable(void);  // Timer0=TI0(HBlank), Timer1=TO0TRG (Timer0 overflow)
void ngpc_dma_timer01_hblank_disable(void);

// Stream helper that stores its start vector:
typedef struct { /* ... */ } NgpcDmaU8Stream; // see src/fx/ngpc_dma.h
void ngpc_dma_stream_begin_u8(NgpcDmaU8Stream *s,
                              u8 channel,
                              volatile u8 NGP_FAR *dst_reg,
                              const u8 NGP_FAR *src_table,
                              u16 count,
                              u8 start_vector);
void ngpc_dma_stream_rearm_u8(const NgpcDmaU8Stream *s); // call once per frame (during VBlank)
void ngpc_dma_stream_end_u8(NgpcDmaU8Stream *s);

void ngpc_dma_stop(channel);
u16  ngpc_dma_remaining(channel);
u8   ngpc_dma_active(channel);

void ngpc_dma_set_done_handler(channel, callback);
u8   ngpc_dma_poll_done(channel);
```

Current implementation scope:
- Configures channels 0-3 with TLCS-900 DMA registers (`DMAS/DMAD/DMAC/DMAM`).
- Uses byte stream mode **src++ -> fixed dst register** (good for scanline register tables).
- Trigger comes from interrupt start vectors (for example HBlank Timer0).
  **Avoid VBlank as a start vector** on NGPC: MicroDMA can consume that IRQ, starving the mandatory VBlank ISR
  (watchdog => power off). In this template, VBlank trigger is blocked by default via `NGP_DMA_ALLOW_VBLANK_TRIGGER=0`.

Limitations:
- Only the streaming mode above is currently exposed (not a full generic DMA abstraction).
- Because the destination address is fixed (no dst++), this is **not** a VRAM bulk upload / memcpy feature.
  If you try to "DMA copy tiles into VRAM", you'll overwrite the same byte repeatedly and graphics will look corrupted.
- `ngpc_dma_link_hblank()` requires Timer 0 to be configured/running.
- Validation campaign and acceptance criteria are documented in `TEST_PLAN_DMA_SPRMUX.md`.

Practical example:
- Run the MicroDMA test screen from a dedicated test copy (see `TEST_PLAN_DMA_SPRMUX.md`).
  The safe baseline test uses Timer0/HBlank to update `HW_SCR1_OFS_X` once per scanline.
 - For 2-channel effects, avoid MicroDMA "CHAIN" by using two different start vectors:
   CH0 on Timer0 (0x10) and CH1 on Timer1 (0x11), with Timer1 clocked from Timer0 overflow (`ngpc_dma_timer01_hblank_enable()`).

### ngpc_dma_raster -- Raster via MicroDMA

`ngpc_dma_raster` est un wrapper haut niveau sur `ngpc_dma` pour faire du **scroll par scanline**
avec **zéro code CPU en HBlank** (MicroDMA fait les writes de registres).

Points clefs:
- Tables en RAM, taille `152` bytes (une valeur par ligne).
- Re-arm **une fois par frame** pendant VBlank, **le plus tôt possible** (juste après `ngpc_vsync()`).
- Exclusif avec `ngpc_raster` / `ngpc_sprmux` (Timer0 partagé).
- Pour X+Y en même temps, utilise **Timer0 + Timer1** pour éviter le CHAIN.

```c
static u8 scr1_x[152];
static u8 scr1_y[152];
static NgpcDmaRaster r;

ngpc_dma_init();
ngpc_dma_raster_begin(&r, GFX_SCR1, scr1_x, scr1_y);
ngpc_dma_raster_enable(&r);

while (1) {
    ngpc_vsync();
    ngpc_dma_raster_rearm(&r);
}
```

### ngpc_lz -- Tile Decompression

```c
// RLE decompression (simple, fast, ~2:1 ratio)
u16 ngpc_rle_decompress(dst, src, src_len);

// LZ77/LZSS decompression (better ratio, ~3:1 to 4:1)
u16 ngpc_lz_decompress(dst, src, src_len);

// Convenience: decompress directly to tile RAM
void ngpc_rle_to_tiles(src, src_len, tile_offset);
void ngpc_lz_to_tiles(src, src_len, tile_offset);
```

Compress assets offline with a companion Python tool, decompress at runtime.
The `_to_tiles` functions use a **2 KB internal buffer** (~128 tiles max per call).
For larger tilesets, decompress in chunks.

```c
// Load a compressed tileset at tile slot 96
extern const u8 level1_tiles_lz[];    // compressed data in GraphX/
extern const u16 level1_tiles_lz_len; // size in bytes
ngpc_lz_to_tiles(level1_tiles_lz, level1_tiles_lz_len, 96);
```

Offline compressor (`tools/ngpc_compress.py`):

```bash
# LZ77 output (default): emits <name>_lz + <name>_lz_len
python tools/ngpc_compress.py GraphX/tiles.bin -o GraphX/tiles_lz.c -n level1_tiles --header

# RLE output: emits <name>_rle + <name>_rle_len
python tools/ngpc_compress.py GraphX/tiles.bin -o GraphX/tiles_rle.c -m rle -n level1_tiles --header

# both: tests RLE and LZ77, picks the smallest output automatically
python tools/ngpc_compress.py GraphX/tiles.bin -o GraphX/tiles_best.c -m both -n level1_tiles --header
```

Notes:
- Naming convention is `_lz` (not `_lz77`) and `_rle`.
- The tool verifies roundtrip integrity by default (compress -> decompress -> compare).
- Input format is raw binary (`.bin` / any byte stream), not image files.
- Generated metadata includes:
  - `<name>_len`: compressed byte size (pass this to `ngpc_*_decompress` / `ngpc_*_to_tiles`)
  - `<name>_raw_len`: decompressed byte size (informational/useful for tooling)

Tilemap converter (`tools/ngpc_tilemap.py`):

```bash
# Standard: PNG -> tiles + tilemap + palettes for GraphX/
python tools/ngpc_tilemap.py assets/level1_bg.png -o GraphX/level1_bg.c -n level1_bg --header \
  --tiles-bin GraphX/level1_bg_tiles.bin

# Full-screen fixed intro style (raw bytes output, legacy-friendly)
python tools/ngpc_tilemap.py assets/title.png -o GraphX/title_intro.c -n title_intro --header \
  --emit-u8-tiles --black-is-transparent --no-dedupe
```

Generated symbols:
- `<name>_tiles[]`, `<name>_tiles_count`
- Optional with `--emit-u8-tiles`: `<name>_tiles_u8[]`, `<name>_tile_count`
- `<name>_map_tiles[]`, `<name>_map_pals[]`, `<name>_map_w`, `<name>_map_h`
- `<name>_palettes[]`, `<name>_palette_count`
- Optional `--tiles-bin`: raw tile words for compression pipeline (`ngpc_compress.py`).

Practical notes:
- `--black-is-transparent` maps opaque RGB444 black to index `0` (transparent), useful for legacy intro pipelines.
- For fixed full-screen images (20x19), prefer `--emit-u8-tiles --no-dedupe` and load with `ngpc_gfx_load_tiles_u8_at(...)`.

Constraints (strict):
- Input image must be tile-aligned: width and height are multiples of 8.
- Tilemap size must fit one NGPC scroll plane: max `32x32` tiles (`256x256` px).
- Unique tile count after dedupe must be `<= 512` (NGPC tile VRAM limit).
- Template palette policy is fixed: index `0` is reserved for transparency.
- Each `8x8` tile can use at most **3 visible colors** (`indices 1..3`) + transparency (`index 0`).
- Global palette budget is `<= 16` palettes (`--max-palettes`, default 16).
- Alpha `< 128` is imported as transparent index value `0`.
- By default opaque black is preserved as a visible color; use `--black-is-transparent` to map it to transparency.
- Hardware is 2bpp: 4 indices per pixel (`0..3`).
- Each tile can use its own palette id (0..15), so in a 16x16 image the two
  8x8 tiles may use different 4-color sets, as long as the image fits in
  the 16-palette global budget.

Metasprite exporter (`tools/ngpc_sprite_export.py`):

```bash
# Spritesheet PNG (grid frames) -> tiles + NgpcMetasprite + MsprAnimFrame
python tools/ngpc_sprite_export.py assets/player_sheet.png -o GraphX/player_mspr.c -n player \
  --frame-w 16 --frame-h 16 --header \
  --tiles-bin GraphX/player_tiles.bin
```

Generated symbols:
- `<name>_tiles[]`, `<name>_tiles_count`, `<name>_tile_base`
- `<name>_palettes[]`, `<name>_palette_count`
- `<name>_frame_0..N` (`NgpcMetasprite`)
- `<name>_anim[]`, `<name>_anim_count` (`MsprAnimFrame`)
- Optional `--tiles-bin`: raw tile words for compression pipeline (`ngpc_compress.py`).

Constraints (strict):
- Input is a grid spritesheet (row-major frames), loaded via Pillow.
- `--frame-w` and `--frame-h` are required, must be multiples of 8.
- Frame size is capped to `<= 128x128` (offset fields are `s8` in `MsprPart`).
- Image width/height must be exact multiples of frame size.
- Template palette policy is fixed: index `0` is reserved for transparency.
- Each `8x8` tile can use at most **3 visible colors** (`indices 1..3`) + transparency (`index 0`).
- Per frame, visible tiles must be `<= 16` (`MSPR_MAX_PARTS`).
- Tile id range must stay valid: `tile_base + unique_tiles - 1 <= 511`.
- Global palette budget is `<= 16` palettes (`--max-palettes`, default 16).
- Alpha `< 128` is imported as transparent index value `0`.
- Opaque black is preserved as a visible color (import keeps it distinct from transparency).

Project bootstrap tool (`tools/ngpc_project_init.py`):

```bash
# Create a new project folder from this template
python tools/ngpc_project_init.py C:/dev/MyNgpcGame --name "My NGPC Game"
```

What it does:
- Copies the template to the destination.
- Skips generated artifacts (`bin/`, `build/`, `__pycache__`, stale `.rel`/ROM outputs).
- Patches:
  - `makefile`: `NAME = <rom_name>`
  - `build.bat`: `SET romName=<rom_name>`
  - `src/core/carthdr.h`: `CartTitle[12] = "............"` (strict 12 chars)

Options:
- `--rom-name`: force output ROM base name (default: derived from `--name`)
- `--cart-title`: force cartridge title (trim/pad to 12 chars)
- `--dry-run`: preview only
- `--force`: overwrite destination if it exists

### ngpc_lut -- Lookup Tables (Fast Math)

```c
u8  ngpc_lut_atan2(dx, dy);       // Angle in 0-255 format (s8 inputs)
u8  ngpc_lut_sqrt16(n);           // Integer sqrt of u16, returns 0-255
u16 ngpc_lut_dist(dx, dy);        // Approx distance (no sqrt, ~4% error)
u16 ngpc_lut_div(n, divisor);     // Fast division (reciprocal multiply)
```

All use fixed-point or integer math. Zero FPU, minimal CPU cost.

```c
// Point-at-target angle for a bullet
s8 dx = target_x - bullet_x;
s8 dy = target_y - bullet_y;
u8 angle = ngpc_lut_atan2(dx, dy);
bullet_vx = ngpc_cos(angle);
bullet_vy = ngpc_sin(angle);
```

---

## Adding your own assets

### Graphics (GraphX/)

1. Create your tiles in a tile editor (8x8 px, 2 bits per pixel, 4 colors)
2. Export as a C array of `u16` words (8 words per tile, 16 bytes)
3. Save the `.c` file in `GraphX/`
4. Add `extern` declarations in `GraphX/gfx_data.h`
5. Add the `.rel` to `OBJS` in the makefile:
   ```makefile
   OBJS += GraphX/my_tileset.rel
   ```
6. In your game code:
   ```c
   #include "../GraphX/gfx_data.h"
   ngpc_gfx_load_tiles_at(MY_TILESET, MY_TILESET_COUNT, 128);
   ```

### Sound (sound/)

1. Compose music in NGPC Sound Creator (or author BGM streams by hand)
2. Export the `.c` data files into `sound/`
3. Add `extern` declarations in `sound/sound_data.h`
4. Add the `.rel` to `OBJS` in the makefile:
   ```makefile
   OBJS += sound/my_song.rel
   ```
6. In your game code:
   ```c
   #include "../sound/sound_data.h"
   Bgm_SetNoteTable(NOTE_TABLE);
   Bgm_StartLoop4Ex(BGM_CH0, BGM_CH0_LOOP, ...);
   ```

Multiple songs: create one `.c` per song (e.g. `song_title.c`, `song_battle.c`),
each with its own streams and note table. Switch between them at runtime.

---

## State machine pattern

The demo `main.c` uses a simple state machine:

```c
typedef enum { STATE_TITLE, STATE_GAME } GameState;

static GameState s_state = STATE_TITLE;

void main(void) {
    GameState prev = STATE_GAME;  // force init on first frame

    ngpc_init();
    ngpc_load_sysfont();

    while (1) {
        ngpc_vsync();
        ngpc_input_update();

        // Run init on state change
        if (s_state != prev) {
            prev = s_state;
            switch (s_state) {
            case STATE_TITLE: title_init(); break;
            case STATE_GAME:  game_init();  break;
            }
        }

        // Run update every frame
        switch (s_state) {
        case STATE_TITLE: title_update(); break;
        case STATE_GAME:  game_update();  break;
        }
    }
}
```

Add more states as needed (`STATE_OPTIONS`, `STATE_GAMEOVER`, etc.).
Each state has an `_init()` (called once on entry) and `_update()` (called every frame).

---

## Object pool pattern (recommended)

Use fixed-size pools with a bitmask for gameplay objects (bullets, particles, enemies).
This avoids malloc/fragmentation and keeps RAM/time deterministic on a 12 KB machine.

Reference example: `examples/object_pool_example.c`

Core idea:

```c
#define MAX_BULLETS 16
static Bullet s_bullets[MAX_BULLETS];
static u16 s_active_mask; /* bit = slot used */

u8  bullet_pool_alloc(void);  /* returns 0xFF if full */
void bullet_pool_free(u8 idx);
```

Tips:
- Keep pool sizes power-of-two where practical for simpler tuning.
- Keep the mask type aligned with pool size (`u16` for <=16, `u32` for <=32).
- Separate `spawn`, `update`, and `render` loops for cache-friendly traversal.

---

## Hardware constraints to keep in mind

| Constraint | Limit | Notes |
|---|---|---|
| Work RAM | 12 KB | All game state must fit. No malloc. |
| Tiles | 512 max | Shared between font, backgrounds, sprites |
| Sprites | 64 max | 8x8 px each, chain for larger |
| Palettes | 16 per plane | 4 colors each (one is transparent) |
| Sound channels | 4 | 3 tone + 1 noise, shared BGM/SFX |
| Cart ROM | 2 MB | Code + all assets must fit |
| Save | 256 bytes | Flash has limited write cycles |
| CPU | No FPU | Integer and fixed-point math only |
| VBlank time | ~3.9 ms | Heavy work in VBI causes glitches |
| Watchdog | ~100 ms | Must be cleared every frame (handled by VBI) |
| Viewport | 160x152 | Origin + size must not exceed these values |

**System font** printable range uses tiles `0x20-0x7F`. Load your game tiles at offset 128 or higher.

---

## Sound driver (src/audio/)

The template includes a custom T6W28 PSG sound driver with embedded Z80 code.
It supports simultaneous BGM streaming (up to 4 voices) and SFX with sweep,
envelope, ADSR, LFO, vibrato, and burst noise.

### Initialization

```c
#include "audio/sounds.h"

void main(void) {
    ngpc_init();
    Sounds_Init();      /* Upload Z80 driver + reset state */
    /* ... */
    while (1) {
        ngpc_vsync();
        Sounds_Update();  /* Advance BGM + SFX every frame */
        /* ... */
    }
}
```

### BGM playback

```c
/* Start a looping 4-voice song (data exported from NGPC Sound Creator) */
extern const u8 SONG_CH0[], SONG_CH1[], SONG_CH2[], SONG_CHN[];
extern const u8 NOTE_TABLE[];

Bgm_SetNoteTable(NOTE_TABLE);
Bgm_StartLoop4(SONG_CH0, SONG_CH1, SONG_CH2, SONG_CHN);

/* With explicit loop offsets */
Bgm_StartLoop4Ex(SONG_CH0, 120, SONG_CH1, 120, SONG_CH2, 120, SONG_CHN, 120);

/* Stop / fade out */
Bgm_Stop();
Bgm_FadeOut(4);          /* speed: smaller = faster */
Bgm_SetTempo(2);         /* global tempo multiplier */
```

### SFX playback

```c
/* Quick one-shot tone */
Sfx_PlayToneCh(0, 240, 2, 6);   /* ch, divider, attn, frames */

/* Full control: tone with sweep + envelope */
Sfx_PlayToneEx(0, 240, 2, 6,    /* ch, divider, attn, frames */
               280, 2, 1, 0, 1, /* sw_end, sw_step, sw_speed, sw_ping, sw_on */
               1, 2, 2);        /* env_on, env_step, env_spd */

/* Noise (explosions, hits) */
Sfx_PlayNoiseEx(1, 1, 6, 8,     /* rate, type, attn, frames */
                0, 1,            /* burst, burst_dur */
                1, 1, 2);       /* env_on, env_step, env_spd */

/* Data-driven presets (table approach) */
static const SfxPreset kSfxTable[] = {
    { SFX_PRESET_TONE,  { .tone  = {0, 240, 2, 6, 280, 2, 1, 0, 1, 1, 2, 2} } },
    { SFX_PRESET_NOISE, { .noise = {1, 1, 6, 8, 0, 1, 0, 1, 2} } },
};
Sfx_PlayPresetTable(kSfxTable, 2, sfx_id);

Sfx_Stop();              /* Silence all SFX */
```

### Custom SFX mapping

Edit `src/audio/sounds_game_sfx_template.c` to implement `Sfx_Play(id)`:
- Table-driven: fill `kSfxTable[]` and call `Sfx_PlayPresetTable()`
- Manual: `switch(id)` with direct `Sfx_PlayToneEx()` calls
- Project export: use arrays from NGPC Sound Creator `project_sfx.c`

### BGM stream opcodes

The BGM stream supports these inline commands (for advanced users):

| Opcode | Name | Description |
|---|---|---|
| `0xF0` | SET_ATTN | Set voice attenuation |
| `0xF1` | SET_ENV | Set envelope |
| `0xF2` | SET_VIB | Set vibrato |
| `0xF3` | SET_SWEEP | Set pitch sweep |
| `0xF4` | SET_INST | Select instrument preset |
| `0xF5` | SET_PAN | Set panning (L/R) |
| `0xF7` | SET_EXPR | Set expression (volume offset) |
| `0xF8` | PITCH_BEND | Pitch bend |
| `0xF9` | SET_ADSR | Set ADSR envelope |
| `0xFA` | SET_LFO | Set LFO modulation |
| `0xFB` | SET_ENV_CURVE | Envelope curve shape |
| `0xFC` | SET_PITCH_CURVE | Pitch curve shape |
| `0xFD` | SET_MACRO | Trigger macro (attn+pitch per step) |

### Debug

```c
#define BGM_DEBUG 1         /* Enable before including sounds.h */
BgmDebug dbg;
Bgm_DebugSnapshot(&dbg);   /* Capture voice state */
u8 fault = Sounds_DebugFault();
u16 drops = Sounds_DebugDrops();
```

---

## Credits

Written from scratch in 2026 using the public NGPC hardware specification.
No code was copied from any existing framework.

Hardware reference: "Everything You Always Wanted To Know About NeoGeo Pocket Color"
by NeeGee (2000), supplemented by the official SNK NGPC SDK documentation.
