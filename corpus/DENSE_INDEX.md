# NGPC Dense Index — high-density entry point

Compact map + cheat-sheet of the NGPC documentation corpus, optimized for fast LLM
loading. Inline facts are the must-knows; follow the page link for full detail. All
addresses hex. Console: Neo Geo Pocket Color, Toshiba TLCS-900/H @ 6.144 MHz, 160x152,
~60 Hz. Full pages live under `wiki/`.

---

## Memory map (the landmarks)

| Region | Range | Notes |
|--------|-------|-------|
| Work RAM | `0x004000`–`0x005FFF` | C vars / stack / BSS (8 KB usable) |
| Battery / save RAM | `0x006000`–`0x006BFF` | top user RAM ends `0x6BFF` |
| BIOS reserved | `0x006C00`–`0x006FFF` | incl. BIOS RAM vars at `0x6F80+` |
| Z80 / audio RAM | `0x007000`–`0x007FFF` | sound driver |
| K2GE video regs | `0x008000`–`0x008FFF` | display/scroll/sprite control |
| Palette RAM | `0x008200` (spr) `/8280` (SCR1) `/8300` (SCR2) | 0BGR 12-bit (RGB444) |
| OAM (sprites) | `0x008800` | 64 entries x 4 bytes |
| Sprite palette index | `0x008C00` | 1 byte/sprite |
| Tilemap SCR1 / SCR2 | `0x009000` / `0x009800` | 32x32 cells, 2 bytes/cell |
| Char / tile RAM | `0x00A000` | 2bpp tiles |
| Cartridge ROM | `0x200000`–`0x3FFFFF` | 2 MB, FAR access only (near can't reach) |
| Flash save | block 33 @ `0x1FA000` (CPU `0x3FA000`) | block 34 = system-reserved |

## Key BIOS RAM variables (`0x6F80+`)

`0x6F82` joypad (active-high) · `0x6F85` shutdown-request flags · `0x6F87` language ·
`0x6F91` hardware type (`>=0x10` = Color, else mono NGP).

## K2GE key registers (`0x80xx`)

`0x8000` display control · `0x8009` raster V position (current scanline) ·
`0x8020`/`0x8021` PO.H/PO.V global offset (shifts whole display) ·
`0x8032`/`0x8033` SCR1 scroll X/Y · `0x8034`/`0x8035` SCR2 scroll X/Y ·
`0x8118` BG_CTL (**Color mode only — never write on mono NGP**).
Color index 0 is forced transparent on BG planes → **use index 2** for opaque tile bg
(all-index-2 tile = `0xAAAA`). SCR Y scroll is inverted (camera down = decrease Y reg).

## Timing & frame budget

6.144 MHz · ~102,400 cycles/frame @ 60 Hz · 152 visible scanlines + ~47 VBlank (~199) ·
HBlank ~30 cycles (ISR must be <=1-2 register writes) · VBlank ~24,200 cycles.
**Watchdog:** write `0x4E` (NOP opcode) to the watchdog reg every frame and inside any
loop > ~8000 iterations, or the console resets. IRQ entry ~80 cycles → a per-scanline
raster split (152 IRQ/frame) burns ~12% CPU (emulators often hide this).

## CPU / ABI (TLCS-900/H, cc900)

Return: `u8`->`L`, `u16`->`HL`, `u32`->`XHL`. cc900 args on stack (arg0 @ `XSP+4`).
Caller-saved `XWA/XBC/XDE/XHL`; callee-saved `XIX/XIY`. All ROM pointers must be FAR
(`NGP_FAR`/`__far`). C89 only: no float/double, no `long long`, decls at block start.
Memory-form ALU family `0x80|zz|mem` (compact compound-assign; see TLCS-900/H Reference).

**Silicon-broken opcodes (codegen must avoid):** `D0`-prefix sub-ops; `adc W,B` with W>0;
`CB` family (`add A,C`); `link XIY,N` with N>=5; `inc/dec WA`; `srl A,XDE` with A=0
(zeroes XDE). `ldirw` on NGPC uses XIY(src)/XIX(dst), not XDE/XHL.

## Input (joypad `0x6F82`, active-high)

`UP=01 DOWN=02 LEFT=04 RIGHT=08 A=10 B=20 OPTION=40 POWER=80`.
Edge "just pressed" = `cur & ~prev`. Read once/frame after VBlank sync; mark `volatile`.

## Audio

TLCS-900 never touches the T6W28 PSG directly — the **Z80** owns sound. Mailbox:
`0x00B8` run control (`0xAAAA` stop / `0x5555` start), `0x00BA` write 1 = Z80 NMI,
`0x00BC` 1-byte command (fast cmds echo `cmd XOR 0xFF`).

## Toolchain

Official (proprietary, host-provided): `cc900 -> asm900 -> tulink -> tuconv -> s242ngp`
(driver invokes thc1->thc2 internally; TAC is the thc1->thc2 IR, optimisation-independent).
Open-source: `t900cc` / `t900as` / `t900ld` / `ngpc_romtool`. Runtime helpers `C9H_*`
(32-bit mul/div + float; 16-bit mul/div are native).

## Top gotchas

color-0-transparent (use index 2) · forgot `NGP_FAR` on ROM data · missing watchdog kick ·
`u8 * const` silent overflow (cast to `s16` first) · link sprites `.rel` before maps ·
nested initialized local decl miscompiles (hoist it) · `SAVE_SIZE` must be 512 (256
unreliable) · checksum at fixed offset BEFORE terminal padding · disable the raster/Timer0
ISR before a flash write · install VBL ISR + `ei 0` or the joypad byte never updates.

---

## Page index (full detail under `wiki/`)

**Hardware**
- `wiki/01_Hardware/Hardware-Registers.md` — full register reference, memory map, timers, interrupts, OAM/tilemap/audio tables, gotchas G1-G15.
- `wiki/01_Hardware/BIOS.md` — BIOS `SWI` calls, vectors, register bank 3, system library functions.

**CPU & Toolchain**
- `wiki/02_CPU-and-Toolchain/TLCS900-Reference.md` — registers, ABI, types, memory model, opcode encoding, mem-form ALU table, branch-free idioms.
- `wiki/02_CPU-and-Toolchain/Assembly.md` — asm syntax, gotchas, LDIRW patterns, calling convention.
- `wiki/02_CPU-and-Toolchain/Build-Toolchain.md` — C89 rules, far pointers, volatile, ISR, inline asm, ABI, known bugs, CC900 pipeline + TAC IR + C9H helpers.

**Graphics**
- `wiki/03_Graphics/Overview.md` — K2GE pipeline; pseudo-3D perspective lookup.
- `wiki/03_Graphics/Sprites-and-OAM.md` — OAM, metasprites, flip bits, 64-sprite budget, chaining.
- `wiki/03_Graphics/Tilemaps-and-Scrolling.md` — SCR1/SCR2, 32x32 layout, stride blit, scroll inversion, HUD-as-tilemap.
- `wiki/03_Graphics/Colors-and-Palettes.md` — RGB444, color-0 transparency fix, palette regions.
- `wiki/03_Graphics/Effects-and-Raster.md` — raster/HBlank effects, palette FX, bitmap, text, one-split HUD pattern.
- `wiki/03_Graphics/DMA.md` — MicroDMA, raster DMA, DMAM encoding, INTTC0 auto-rearm, safe start/wait, inline-asm sequences.
- `wiki/03_Graphics/VRAM-Queue.md` — queued VRAM updates, LDIRW CMD_COPY contract.

**Audio**
- `wiki/04_Audio/Audio.md` — sound hardware, Z80 driver, mailbox/ACK protocol, playback patterns.

**Systems**
- `wiki/05_Systems/Game-Loop.md` — main loop, VBlank sync, watchdog, frame budget, state machines, VBlank-counter roles.
- `wiki/05_Systems/Input.md` — joypad polling, edge detection, auto-repeat.
- `wiki/05_Systems/Storage-and-Saves.md` — flash save, RTC, save-struct design, flash pitfalls.
- `wiki/05_Systems/Collision.md` — AABB/tile collision, typed enable matrix, codegen pitfalls.
- `wiki/05_Systems/Fixed-Point-Math.md` — fixed-point (8.x), LUTs, binary->BCD, compression.
- `wiki/05_Systems/Localization.md` — BIOS language detect, bilingual ROM, string tables, system font.
- `wiki/05_Systems/Debug-Tools.md` — on-device CPU profiler, ring-buffer log, runtime assert.

**Pipeline & Patterns**
- `wiki/06_Pipeline-and-Patterns/Asset-Pipeline.md` — PNG export, compression, runtime loading, tool limits.
- `wiki/06_Pipeline-and-Patterns/Gameplay-Patterns.md` — state machines, pacing, genre patterns (shmup/platformer/puzzle/grid/racing/adventure/roguelike-procgen), entity management.

## Keyword router

scroll/parallax/HUD freeze -> Tilemaps, Effects-and-Raster · sprite/OAM/metasprite/flip -> Sprites-and-OAM ·
palette/color/transparent/fade -> Colors-and-Palettes · DMA/MicroDMA/LDIRW -> DMA, VRAM-Queue ·
save/flash/RTC/checksum -> Storage-and-Saves · joypad/button/edge -> Input ·
VBlank/watchdog/frame budget/state machine -> Game-Loop · interrupt/timer/vector -> Hardware-Registers, BIOS ·
opcode/encoding/ABI/register -> TLCS900-Reference, Assembly · compiler/C89/far pointer/bug -> Build-Toolchain ·
PSG/Z80/SFX/music -> Audio · collision/AABB/tile -> Collision · fixed-point/LUT/BCD -> Fixed-Point-Math ·
PNG/tiles/font/asset -> Asset-Pipeline · procgen/dungeon/genre -> Gameplay-Patterns.
