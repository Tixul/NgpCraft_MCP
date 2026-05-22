# Hardware Registers

Complete hardware register documentation for the Neo Geo Pocket Color: memory map, OAM, tilemaps, palettes, audio, interrupts, and representative RAM variable maps.

> Note: All content uses ASCII only (avoids encoding issues on Windows/PowerShell).

---


## 0. CPU / System Specs

<a id="src-00"></a>

| Component | Value |
|-----------|-------|
| Main CPU | Toshiba TLCS-900/H (16-bit) |
| CPU Frequency | 384 KHz – 6.144 MHz (5 levels, via BIOS_CLOCKGEARSET) |
| Main RAM | 12 KB (0x4000–0x6BFF) |
| Internal ROM (BIOS) | 64 KB (0xFF0000–0xFFFFFF) |
| Cart ROM | up to 2 MB, 8-bit flash, 0-wait (0x200000–0x3EFFFF) |
| Sound CPU | Z80-compatible, 3.072 MHz fixed |
| Z80 RAM | 4 KB (0x7000–0x7FFF, shared with main CPU) |
| Resolution | 160x152 px, 60 fps |
| Virtual tilemap area | 256x256 px (cyclic wrap) |
| Max simultaneous colors | 146 (out of 4096, K2GE mode) |
| Sprites | 64 sprites 8x8, max 64 per scanline |
| Scroll planes | 2 (SCR1 / SCR2, 32x32 tiles each) |
| Character RAM | 8 KB (0xA000–0xBFFF), 512 tiles 8x8x2bpp |
| Palette RAM | 312 bytes (16-bit access ONLY — byte access = undefined) |
| PSG | T6W28 — 3 square tone channels + 1 noise, stereo |

---

## 1. Memory Map

<a id="src-01"></a>

```
0x000000 - 0x0000FF   Internal I/O registers (CPU, timers, DMA, watchdog)
0x004000 - 0x005FFF   Main RAM (8 KB)
0x006000 - 0x006BFF   Battery-backed RAM (3 KB) — for lightweight saves
0x007000 - 0x007FFF   Z80 RAM (4 KB, shared with the sound CPU)
0x006F80 - 0x006FFF   BIOS system zone (variables, ISR vectors)
0x008000 - 0x0087FF   K2GE video registers
0x008800 - 0x008BFF   Sprite VRAM (64 sprites x 4 bytes)
0x008C00 - 0x008C3F   Sprite palette indices (64 bytes)
0x009000 - 0x0097FF   Scroll plane 1 — tilemap (32x32 x 2 bytes = 2 KB)
0x009800 - 0x009FFF   Scroll plane 2 — tilemap (32x32 x 2 bytes = 2 KB)
0x00A000 - 0x00BFFF   Character/tile RAM (512 tiles x 16 bytes = 8 KB)
0x200000 - 0x3FFFFF   Cartridge ROM (2 MB — FAR access only)
0xFF0000 - 0xFFFFFF   Internal BIOS ROM (64 KB)
0xFFFE00              BIOS vector table (system functions)
```

---

## 2. CPU / Internal Timers

<a id="src-02"></a>

| Register | Address | Size | Description |
|----------|---------|------|-------------|
| `HW_WATCHDOG` | `0x006F` | u8 | Watchdog — write `0x4E` to reset |
| `HW_TRUN` | `0x0020` | u8 | Timer run control (bit0=TIM0, bit1=TIM1...) |
| `HW_TREG0` | `0x0022` | u8 | Timer 0 reload value |
| `HW_TREG1` | `0x0023` | u8 | Timer 1 reload value |
| `HW_T01MOD` | `0x0024` | u8 | Timer 0/1 mode (clock source) |
| `HW_TFFCR` | `0x0025` | u8 | Timer flip-flop control |
| `HW_TREG2` | `0x0026` | u8 | Timer 2 (PWM0) |
| `HW_TREG3` | `0x0027` | u8 | Timer 3 (PWM1, audio) |
| `HW_T23MOD` | `0x0028` | u8 | Timer 2/3 mode |
| `HW_DMA0V` | `0x007C` | u8 | DMA channel 0 start vector |
| `HW_DMA1V` | `0x007D` | u8 | DMA channel 1 start vector |
| `HW_DMA2V` | `0x007E` | u8 | DMA channel 2 start vector |
| `HW_DMA3V` | `0x007F` | u8 | DMA channel 3 start vector |

**Timer 0 -> HBlank:**
```c
HW_T01MOD &= (u8)~0xC3;  /* clock source = TI0, 8-bit mode */
HW_TREG0   = 0x01;        /* reload = 1 (fire every HBlank) */
HW_TRUN   |= 0x01;        /* start timer 0 */
```

**Timer prescaler** (base clock fc = 384 KHz fixed):

| Symbol | Divisor | Period |
|--------|---------|--------|
| T1 | fc/8 | 20.83 µs |
| T4 | fc/32 | 83.33 µs |
| T16 | fc/128 | 333.3 µs |
| T256 | fc/2048 | 5.333 ms |

**Timer modes** (configured as pairs T01/T23):
- Interval 8-bit (4 channels) — standard timer/counter
- Interval 16-bit cascade (2 channels)
- PWM 8-bit (2 channels)
- Square wave programmable (2 channels)

**Critical timer rules:**
- `NEVER stop the prescaler` — corrupts serial communication
- `Timer 3` = reserved for Z80 audio clock when the SNK audio driver is active

**Timer 3 setup for Z80 IM1 (~7800 Hz, validated homebrew pattern):**
```c
/* Using direct register writes */
HW_T23MOD &= 0x33;        /* T2/T3 = 8-bit interval */
HW_T23MOD |= 0x04;        /* clock = T1 (20.83 us) */
HW_TFFCR  &= 0x0F;        /* clear T3 flip-flop settings */
HW_TFFCR  |= 0xB0;        /* flip-flop enable + clear */
HW_TREG3   = 0x62;        /* comparator -> ~7800 IRQ/s */
HW_TRUN   |= 0x88;        /* start timer 3 + prescaler */
```

---

## 3. BIOS System Variables (0x6F80)

<a id="src-03"></a>

### 3.1 Watchdog address note

Two addresses are observed depending on the title:
- `0x006F` — official SDK address (used by most titles and templates)
- `0x006B` — alternate address seen in some titles (byte 1 of function at `0x006A`)

Both accept value `0x4E`. In practice use `0x006F` (SDK standard).

### 3.2 Hardware type detection

`0x6F91` = hardware type:
- value >= `0x10` = NGPC color
- value < `0x10` = NGP monochrome (BW)

### 3.3 Register table

| Register | Address | Size | Description |
|----------|---------|------|-------------|
| `HW_BAT_VOLT` | `0x6F80` | u16 | Battery voltage (10-bit, 0-1023) |
| `HW_JOYPAD` | `0x6F82` | u8 | Joypad state (bits = buttons) |
| `HW_USR_BOOT` | `0x6F84` | u8 | Boot reason (0=normal, 1=resume, 2=alarm) |
| `HW_USR_SHUTDOWN` | `0x6F85` | u8 | OS-requested shutdown flag |
| `HW_USR_ANSWER` | `0x6F86` | u8 | User response — bit5 must be 0 |
| `HW_LANGUAGE` | `0x6F87` | u8 | System language |
| `HW_OS_VERSION` | `0x6F91` | u8 | 0=NGP monochrome, !=0=NGPC color |

### 3.4 Joypad bitmasks

```c
PAD_UP     = 0x01
PAD_DOWN   = 0x02
PAD_LEFT   = 0x04
PAD_RIGHT  = 0x08
PAD_A      = 0x10
PAD_B      = 0x20
PAD_OPTION = 0x40
PAD_POWER  = 0x80
```

---

## 4. Interrupts

<a id="src-04"></a>

### 4.1 ISR vector table

ISR vectors stored in RAM as 32-bit pointers to handler functions.

| Register | Address | Description | MicroDMA start |
|----------|---------|-------------|----------------|
| `HW_INT_SWI3` | `0x6FB8` | Software interrupt 3 | — |
| `HW_INT_SWI4` | `0x6FBC` | Software interrupt 4 | — |
| `HW_INT_SWI5` | `0x6FC0` | Software interrupt 5 | — |
| `HW_INT_SWI6` | `0x6FC4` | Software interrupt 6 | — |
| `HW_INT_RTC` | `0x6FC8` | RTC alarm | `0x0A` |
| `HW_INT_VBL` | `0x6FCC` | **VBlank 60 Hz — MANDATORY** | `0x0B` |
| `HW_INT_Z80` | `0x6FD0` | Z80 interrupt (audio) | `0x0C` |
| `HW_INT_TIM0` | `0x6FD4` | Timer 0 -> HBlank (raster, sprmux) | `0x10` |
| `HW_INT_TIM1` | `0x6FD8` | Timer 1 | `0x11` |
| `HW_INT_TIM2` | `0x6FDC` | Timer 2 | `0x12` |
| `HW_INT_TIM3` | `0x6FE0` | Timer 3 (audio clock) | `0x13` |
| `HW_INT_SER_TX` | `0x6FE4` | Serial TX (reserved) | — |
| `HW_INT_SER_RX` | `0x6FE8` | Serial RX (reserved) | — |
| `HW_INT_DMA0` | `0x6FF0` | DMA channel 0 complete | — |
| `HW_INT_DMA1` | `0x6FF4` | DMA channel 1 complete | — |
| `HW_INT_DMA2` | `0x6FF8` | DMA channel 2 complete | — |
| `HW_INT_DMA3` | `0x6FFC` | DMA channel 3 complete | — |

Enable interrupts: `__asm("ei");`

### 4.2 VBlank rules

**VBlank must NEVER be disabled.** The minimal VBlank handler must:
1. Write `HW_WATCHDOG = 0x4E`
2. Check `HW_USR_SHUTDOWN` and request a shutdown if set.

> A common pattern is to execute the shutdown in main loop context (inside the vsync routine)
> rather than from the ISR, to avoid calling BIOS from an ISR on certain hardware setups.

---

## 5. Video K2GE (0x8000)

<a id="src-05"></a>

### 5.1 Display control

| Register | Address | Size | Description |
|----------|---------|------|-------------|
| `HW_DISP_CTL` | `0x8000` | u8 | Display enable |
| `HW_WIN_X` | `0x8002` | u8 | Window origin X |
| `HW_WIN_Y` | `0x8003` | u8 | Window origin Y |
| `HW_WIN_W` | `0x8004` | u8 | Window width (max 160) |
| `HW_WIN_H` | `0x8005` | u8 | Window height (max 152) |
| `HW_FRAME_RATE` | `0x8006` | u8 | **DO NOT MODIFY** (0xC6 at reset) |
| `HW_RAS_H` | `0x8008` | u8 | Horizontal raster position (read-only) |
| `HW_RAS_V` | `0x8009` | u8 | **Current raster line** (0-151 visible, 152+ VBlank) |
| `HW_STATUS` | `0x8010` | u8 | Bit7=CharOver, Bit6=VBlank (read-only) |
| `HW_LCD_CTL` | `0x8012` | u8 | Bit7=LCD inversion, Bit2-0=border color |
| `HW_GE_MODE` | `0x87E2` | u8 | **DO NOT MODIFY** (0=K2GE color) |

```c
/* Check if in VBlank */
if (HW_STATUS & STATUS_VBLANK) { /* in vblank */ }  /* STATUS_VBLANK = 0x40 */

/* Read current scanline (for CPU profiling) */
u8 line = HW_RAS_V;  /* 0=top, 151=bottom, 152+=vblank */
```

### 5.2 Sprites

| Register | Address | Size | Description |
|----------|---------|------|-------------|
| `HW_PO_H` | `0x8020` | u8 | Global horizontal Position Offset (PO.H) — shifts entire display; reset 0 |
| `HW_PO_V` | `0x8021` | u8 | Global vertical Position Offset (PO.V) — shifts entire display; reset 0 |

> **Position Offset (PO.H / PO.V)** at `0x8020` / `0x8021` shift the **entire display**
> (sprites + both scroll planes together), not a single plane. Distinct from the per-plane
> scroll registers at `0x8032`-`0x8035`. A single write to either is an efficient one-shot
> screen-shake. Do not confuse with SCR1 scroll (which lives at `0x8032` / `0x8033`).

### 5.3 Scroll planes

| Register | Address | Size | Description |
|----------|---------|------|-------------|
| `HW_SCR_PRIO` | `0x8030` | u8 | Bit7: 0=plane1 in front, 1=plane2 in front |
| `HW_SCR1_OFS_X` | `0x8032` | u8 | Scroll plane 1 — X offset (0-255, 8-bit) |
| `HW_SCR1_OFS_Y` | `0x8033` | u8 | Scroll plane 1 — Y offset (0-255, 8-bit) |
| `HW_SCR2_OFS_X` | `0x8034` | u8 | Scroll plane 2 — X offset |
| `HW_SCR2_OFS_Y` | `0x8035` | u8 | Scroll plane 2 — Y offset |
| `HW_BG_CTL` | `0x8118` | u8 | Background color (Bit7-6=10 to enable) — **Color mode only** |

> **`HW_BG_CTL` (0x8118) must NOT be written on a mono NGP** — write it only in Color mode.
> Writing it on monochrome hardware can cause undefined behavior. Gate the write on the
> hardware-type check at `0x6F91` (`>= 0x10` = NGPC color).

> **Important:** Scroll registers are **8-bit** (tilemap wraps at 256x256 px).
> For levels wider than 256px, update the tilemap dynamically (ring buffer).
> To convert a world `s16` coordinate: `(u8)(cam_x & 0xFF)`.

---

## 6. Palette RAM

<a id="src-06"></a>

**16-bit access only.** Entry format: `0x0BGR` (4 bits per channel, 4096 colors).

| Zone | Address | Palettes |
|------|---------|----------|
| `HW_PAL_SPR` | `0x8200` | Sprites: palettes 0-15 (64 entries x u16) |
| `HW_PAL_SCR1` | `0x8280` | Scroll plane 1: palettes 0-15 |
| `HW_PAL_SCR2` | `0x8300` | Scroll plane 2: palettes 0-15 |
| `HW_PAL_BG` | `0x83E0` | Background color |
| `HW_PAL_WIN` | `0x83F0` | Border/window color |

```c
#define RGB(r, g, b)  ((u16)((r)&0xF) | (((g)&0xF)<<4) | (((b)&0xF)<<8))
/* 0x0BGR : bits 11-8=B, 7-4=G, 3-0=R */

/* Read/write a palette entry */
HW_PAL_SCR1[pal_id * 4 + color_idx] = RGB(15, 0, 0);  /* bright red */
```

> **Color index 0:** always transparent on scroll planes. Never put a visible color here.

---

## 7. Sprite VRAM (0x8800)

<a id="src-07"></a>

64 sprites, 4 bytes each. **Byte access.**

```
Sprite n:
  0x8800 + n*4 + 0  : tile (bits 7-0 of 9-bit index)
  0x8800 + n*4 + 1  : flags
                        bit7    : H flip
                        bit6    : V flip
                        bit4-3  : priority (00=hidden, 01=behind, 10=middle, 11=front)
                        bit2    : H chain (extend sprite right)
                        bit1    : V chain (extend sprite down)
                        bit0    : tile bit 8 (for tiles 256-511)
  0x8800 + n*4 + 2  : X position (pixels)
  0x8800 + n*4 + 3  : Y position (pixels)

Sprite n palette:
  0x8C00 + n        : bits 3-0 = palette number (0-15)
```

```c
/* Priority / flip constants (ngpc_hw.h) */
SPR_HIDE   = (0 << 3)  /* hidden */
SPR_BEHIND = (1 << 3)  /* behind both scroll planes */
SPR_MIDDLE = (2 << 3)  /* between scroll planes */
SPR_FRONT  = (3 << 3)  /* in front of everything */
SPR_HFLIP  = 0x80
SPR_VFLIP  = 0x40
SPR_HCHAIN = 0x04      /* extend sprite 8px to the right */
SPR_VCHAIN = 0x02      /* extend sprite 8px downward */
```

---

## 8. Tilemap Scroll Planes (0x9000 / 0x9800)

<a id="src-08"></a>

Two 32x32 tile planes. Each entry = 1 `u16` word. **16-bit access.**

```
HW_SCR1_MAP[ty * 32 + tx] = entry
HW_SCR2_MAP[ty * 32 + tx] = entry

Entry format (u16):
  bit15      : H flip
  bit14      : V flip
  bit12-9    : palette number (0-15)  [bits 4-1 of high byte]
  bit8       : tile index bit 8 (for tiles 256-511)
  bit7-0     : tile index bits 7-0
```

```c
/* Construction macro (ngpc_hw.h) */
#define SCR_ENTRY(tile, pal, hflip, vflip) \
    ((u16)((tile)&0xFF) | \
     (((u16)(hflip)&1)<<15) | (((u16)(vflip)&1)<<14) | \
     (((u16)(pal)&0xF)<<9)  | (((u16)(((tile)>>8)&1))<<8))

#define SCR_TILE(tile, pal)  SCR_ENTRY((tile), (pal), 0, 0)

/* Example: place tile 200, palette 3, at position (5, 2) */
HW_SCR1_MAP[2 * 32 + 5] = SCR_TILE(200, 3);
```

---

## 9. Tile / Character RAM (0xA000)

<a id="src-09"></a>

512 tiles, 8x8 pixels, 2bpp (4 colors). 8 `u16` words per tile = 16 bytes.

```
HW_TILE_DATA[tile_id * 8 + row] = u16 representing one row of 8 pixels

Row format (u16, 8 pixels at 2 bits each, big-endian):
  bits 15-14 : pixel column 0 (leftmost)
  bits 13-12 : pixel column 1
  ...
  bits  1-0  : pixel column 7 (rightmost)

Color index 0 = transparent on scroll planes.
```

```c
/* Access a specific pixel at column c, row r of tile id */
volatile u16 *row_ptr = HW_TILE_DATA + tile_id * 8 + r;
u8 shift = 14 - c * 2;
u8 color = (*row_ptr >> shift) & 3;

/* Write */
*row_ptr = (*row_ptr & ~(3 << shift)) | ((u16)(new_color & 3) << shift);
```

---

## 10. Audio — Z80

<a id="src-10"></a>

| Register | Address | Description |
|----------|---------|-------------|
| `HW_SOUNDCPU_CTRL` | `0x00B8` | Z80 control — `0xAAAA`=stop, `0x5555`=start |
| `HW_Z80_NMI` | `0x00BA` | Trigger Z80 NMI |
| `HW_Z80_COMM` | `0x00BC` | CPU<->Z80 communication byte |
| `HW_Z80_RAM` | `0x7000` | Z80 RAM base (4 KB, shared with Z80) |

The Z80 accesses the T6W28 via its I/O ports: port `0x4000` (right), `0x4001` (left).
A typical audio driver handles this transparently.

**Z80 driver binary (representative commercial engine):**
Stored in ROM and loaded to Z80 RAM (NGPC 0x7000) by the boot sequence.
Z80 entry: `DI; LD SP,0x00C0; JP 0x024B`. Interrupt handler: `JP 0x035A` (RST 8).
Code region: 0x9F5 bytes (size from the ROM driver header). Data area starts at Z80[0x09F5].

**Z80 NMI mailbox protocol (representative commercial engine):**
1. Write command byte to `0x00BC`
2. Write `1` to `0x00BA` → triggers Z80 NMI
3. Z80 NMI handler processes the command and echoes `cmd XOR 0xFF` back to `0x00BC`
4. Main CPU polls `0x00BC` until the echo appears (up to 100 iterations)

For commands needing full Z80 completion (play, stop), a **sequence counter** at Z80 RAM
offset `0x7` (`0x7007`) is polled until the Z80 increments it.
For bulk block transfers, `0x70C3` (Z80 RAM offset 0xC3) is polled until it changes.

**Sound state variables (representative commercial engine, main RAM):**

| Address | Variable         | Description |
|---------|------------------|-------------|
| `0x675D`| `z80_wr_ptr`     | Z80 RAM write cursor — ldir destination; init = 0x7000 |
| `0x675F`| `z80_data_ptr`   | Z80 data area base (code_size + 0x7000, from driver header) |
| `0x6773`| `z80_seq_expect` | Expected Z80 sequence counter value |
| `0x6774`| `bgm_active`     | Currently playing BGM index (0xFFFF = none) |
| `0x6776`| `bgm_pending`    | Requested BGM track (set before dispatch) |
| `0x675A`| `comm_save`      | Saved command before comm session |
| `0x675B`| `comm_save_bc`   | Saved 0x00BC before comm session |
| `0x675C`| `comm_echo_exp`  | Expected echo byte (cmd XOR 0xFF) |
| `0x7007`| `z80_seq` (Z80 RAM) | Z80 increments after each processed command (seq-ACK) |
| `0x70C3`| `z80_status` (Z80 RAM) | Z80 toggles after bulk block transfer done (block-ACK) |

**TLCS-900H timer registers used for sound timing:**

| Register | Address | Role |
|----------|---------|------|
| `TRUN`   | `0x20`  | Bit 3 = timer 3 start/stop |
| `T8MOD`  | `0x25`  | Prescaler config upper nibble |
| `TREG1`  | `0x27`  | Timer 3 reload value (high byte) |
| `T16RUN` | `0x28`  | 16-bit timer mode select |

Period formula: `reload = WA * 1000 / 20480` (WA = desired rate in units of 1/1000 s).

**Z80 command bytes (representative commercial engine):**

| Cmd  | Name          | ACK type |
|------|---------------|----------|
| 0xC1 | CMD_INIT      | echo |
| 0xC3 | CMD_START     | echo |
| 0xC5 | CMD_PLAY      | seq  |
| 0xC7 | CMD_STOP_ALL  | seq  |

A BGM track table in ROM holds 4-byte NGPC pointer entries (index × 4 = entry).

---

## 11. Cartridge ROM

<a id="src-11"></a>

| Constant | Value | Description |
|----------|-------|-------------|
| `CART_ROM_BASE` | `0x200000` | CPU address of cartridge ROM |
| `CART_ROM_SIZE` | `0x200000` | Maximum size (2 MB) |
| Last 16 KB | `0x3FC000-0x3FFFFF` | System reserved — do not use |
| Default save area | `0x3FA000` | Default save offset |

```c
/* Always access ROM via NGP_FAR */
const u16 NGP_FAR *my_tiles = (const u16 NGP_FAR *)0x210000;

/* Or via linker symbols (addresses resolved automatically) */
extern const u16 NGP_FAR my_tileset[];
```

---

## 12. Timings & Budgets

<a id="src-12"></a>

### 12.1 CPU budget per frame

| Item | Value |
|------|-------|
| CPU frequency | 6.144 MHz |
| Cycles per frame (60 Hz) | ~102,400 cycles |
| VBlank duration | ~3.94 ms = ~24,200 cycles |
| HBlank duration | ~5 us (~30 cycles) |

Typical distribution:
```
VBlank ISR (watchdog + input + audio) : ~2,000 cycles
Game logic                            : ~50,000 cycles (variable)
VRAM updates                          : ~20,000 cycles (ideally in VBlank)
Margin                                : ~30,000 cycles
```

> **Important:** VRAM access during active render can cause Character Over (graphics glitch).
> Concentrate VRAM writes in VBlank or use the VRAM queue.

### 12.2 RAM budget

| Zone | Size |
|------|------|
| Total RAM | 12 KB (`0x004000-0x005FFF`) |
| Stack | ~1 KB |
| Engine global variables | ~200 bytes |
| Audio driver (voices, state) | ~500 bytes |
| Sprite/metasprite state | ~300 bytes |
| **Available for game state** | **~9-10 KB** |

### 12.3 cc900 register banks

- **Banks 0-2:** available for user code
- **Bank 3:** system reserved — do not use except for BIOS calls
- ISRs must save/restore all used registers (the `__interrupt` keyword does this automatically)

---

## Quick Reference

| Item | Address | Value / Notes |
|------|---------|---------------|
| Watchdog kick | `0x006F` | Write `0x4E` |
| VBlank ISR vector | `0x6FCC` | 32-bit ptr to handler |
| Timer0 ISR vector | `0x6FD4` | 32-bit ptr to HBlank handler |
| Current scanline | `0x8009` | 0-151 visible, 152+ = VBlank |
| VBlank flag | `0x8010` bit6 | `STATUS_VBLANK = 0x40` |
| Joypad raw | `0x6F82` | See bitmask table |
| Hardware type | `0x6F91` | `>= 0x10` = NGPC color |
| Scroll X plane 1 | `0x8032` | 8-bit, wraps at 256 |
| Scroll Y plane 1 | `0x8033` | 8-bit, wraps at 256 |
| Scroll X plane 2 | `0x8034` | 8-bit, wraps at 256 |
| Scroll Y plane 2 | `0x8035` | 8-bit, wraps at 256 |
| OAM base | `0x8800` | 64 sprites x 4 bytes |
| Palette indices | `0x8C00` | 64 bytes, 1 per sprite |
| SCR1 tilemap | `0x9000` | 32x32 x u16 |
| SCR2 tilemap | `0x9800` | 32x32 x u16 |
| Tile RAM | `0xA000` | 512 tiles x 16 bytes |
| ROM base | `0x200000` | FAR access only |
| Z80 RAM | `0x7000` | 4 KB shared |
| VBlank budget | — | ~24,200 cycles at 6.144 MHz |

---

## 13. Critical Gotchas

<a id="src-13"></a>

Hardware rules that cause non-obvious failures if violated.

| # | Rule | Consequence if violated |
|---|------|-------------------------|
| G1 | **NEVER mask VBlank (IRQ level 4)** | Power management broken — undefined behavior |
| G2 | **NEVER use the HALT instruction** | Supply voltage unmanaged — unstable power |
| G3 | **Watchdog 0x006F <- 0x4E every < 100 ms** | System reset |
| G4 | **Palette RAM = 16-bit access ONLY** | 8-bit access = undefined result |
| G5 | **NEVER stop the prescaler timer** | Serial communication corrupted |
| G6 | **VECT_FLASHERS broken for blocks 32-34** | Erase silently fails -> data corruption; use standalone AMD stubs (no system.lib) |
| G7 | **NO DMA during VBlank** | Watchdog timeout -> reset (hardware power-off) |
| G8 | **Bank 3 registers = system reserved** | Do not use outside of BIOS calls |
| G9 | **WBA.H + WSI.H <= 160, WBA.V + WSI.V <= 152** | Display corruption / freeze |
| G10 | **DO NOT modify REG 0x8006 (REF) or 0x87E2 (MODE)** | Video timing corruption |
| G11 | **Timer 3 = reserved if SNK audio driver active** | Audio corrupted |
| G12 | **Use SWI 1 (DI) for flash write + shutdown** | Partial flash write if IRQ fires mid-operation |
| G13 | **Resume requires the same cartridge** | Restored state incorrect / crash |
| G14 | **IFF2-IFF0 high outside IRQ handler = FORBIDDEN** | Power management broken |
| G15 | **Block 34 (0x1FC000-0x1FFFFF) = SYSTEM RESERVED** | Writing here = brick / undefined behavior |

---

## See Also

- [BIOS Reference](BIOS.md) — BIOS calls and conventions
- [Sprites and OAM](../03_Graphics/Sprites-and-OAM.md) — Sprite system details
- [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) — Tilemap and scrolling
- [DMA](../03_Graphics/DMA.md) — DMA patterns and pitfalls
- [Game Loop](../05_Systems/Game-Loop.md) — VBlank, watchdog, game loop
