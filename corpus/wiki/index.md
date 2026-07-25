# NGPC Dev Wiki

A developer reference for programming the **Neo Geo Pocket Color** (NGPC) and its
TLCS-900/H CPU: hardware registers, graphics, audio, the CPU/toolchain, and reusable
game-programming patterns. Hardware-focused and engine-agnostic — every page documents
the machine and the techniques, not any single game or project.

> Conventions: addresses are hex (`0x8800`), CPU is the Toshiba TLCS-900/H at 6.144 MHz,
> the display is 160×152 visible with 152 visible scanlines per frame at ~60 Hz.

---

## Hardware

- **[Hardware Registers](01_Hardware/Hardware-Registers.md)** — Complete register
  reference: CPU spec table, memory map, timers (prescaler/modes/Timer3-Z80), interrupts
  (with MicroDMA), OAM, tilemaps, audio, IRQ, and the critical hardware gotchas.
- **[BIOS](01_Hardware/BIOS.md)** — BIOS calls (`SWI`), conventions, vectors, register
  bank 3, and the system library functions.

## CPU and Toolchain

- **[TLCS-900/H Reference](02_CPU-and-Toolchain/TLCS900-Reference.md)** — High-density
  CPU reference: registers, ABI / calling convention, data types, memory model, NGPC
  memory layout, opcode encoding, and the memory-form ALU encoding table.
- **[Assembly](02_CPU-and-Toolchain/Assembly.md)** — TLCS-900/H assembly: syntax,
  gotchas, `LDIRW` patterns, calling convention.
- **[Build Toolchain](02_CPU-and-Toolchain/Build-Toolchain.md)** — C compiler rules
  (C89, far pointers, `volatile`, ISR, inline ASM), memory model, the confirmed ABI,
  known compiler/linker bugs, and the CC900 toolchain internals (pipeline, TAC IR,
  runtime helpers, assembler/linker syntax).

## Graphics

- **[Graphics Overview](03_Graphics/Overview.md)** — The K2GE graphics pipeline at a glance.
- **[Sprites and OAM](03_Graphics/Sprites-and-OAM.md)** — OAM (`0x8800`), sprite palettes
  (`0x8C00`), metasprites, sprite budget, performance.
- **[Tilemaps and Scrolling](03_Graphics/Tilemaps-and-Scrolling.md)** — SCR1/SCR2 scroll
  planes, tilemaps, scrolling, HUD-as-tilemap.
- **[Colors and Palettes](03_Graphics/Colors-and-Palettes.md)** — Color/palette
  reference, indices, validation notes.
- **[Effects and Raster](03_Graphics/Effects-and-Raster.md)** — Raster/HBlank effects,
  palette FX, bitmap mode, text rendering, raster split performance.
- **[DMA](03_Graphics/DMA.md)** — DMA usage, MicroDMA, raster DMA, performance,
  pitfalls, and the inline-ASM DMA sequences.
- **[VRAM Queue](03_Graphics/VRAM-Queue.md)** — Queued VRAM updates and the `LDIRW`
  copy contract.

## Audio

- **[Audio](04_Audio/Audio.md)** — Sound hardware, the audio driver, and playback patterns.

## Systems

- **[Game Loop](05_Systems/Game-Loop.md)** — Main loop, VBlank sync, watchdog, frame
  budget, state-machine patterns.
- **[Input](05_Systems/Input.md)** — Joypad polling, edge detection, menu/game input.
- **[Link Cable](05_Systems/Link-Cable.md)** — Serial channel 0, the 11 BIOS COM
  vectors, CTS/RTS handshake, cable-detect (`0xB1` bit2), and session handshakes.
- **[Storage and Saves](05_Systems/Storage-and-Saves.md)** — Flash save and RTC, save
  struct design, and flash hardware pitfalls.
- **[Collision](05_Systems/Collision.md)** — AABB and tile collision, shmup patterns,
  codegen pitfalls.
- **[Fixed-Point Math](05_Systems/Fixed-Point-Math.md)** — Fixed-point (8.4), LUTs,
  compression.
- **[Localization](05_Systems/Localization.md)** — BIOS language detection (EN/JP),
  bilingual ROM, string tables, system font.
- **[Debug Tools](05_Systems/Debug-Tools.md)** — On-device CPU profiler, ring-buffer
  log, runtime assert.

## Pipeline and Patterns

- **[Asset Pipeline](06_Pipeline-and-Patterns/Asset-Pipeline.md)** — PNG export,
  compression, runtime loading.
- **[Gameplay Patterns](06_Pipeline-and-Patterns/Gameplay-Patterns.md)** — State
  machines, pacing, and genre patterns (shmup, platformer, puzzle, grid, racing,
  adventure, roguelike/procedural dungeon).

---

## Quick start by task

| I want to… | Start here |
|------------|-----------|
| Look up a hardware register or the memory map | [Hardware Registers](01_Hardware/Hardware-Registers.md) |
| Set up the build / understand the compiler | [Build Toolchain](02_CPU-and-Toolchain/Build-Toolchain.md) |
| Write or read TLCS-900/H assembly | [Assembly](02_CPU-and-Toolchain/Assembly.md) · [TLCS-900/H Reference](02_CPU-and-Toolchain/TLCS900-Reference.md) |
| Draw sprites / scroll a background | [Sprites and OAM](03_Graphics/Sprites-and-OAM.md) · [Tilemaps and Scrolling](03_Graphics/Tilemaps-and-Scrolling.md) |
| Move data fast to VRAM | [DMA](03_Graphics/DMA.md) · [VRAM Queue](03_Graphics/VRAM-Queue.md) |
| Build a stable main loop | [Game Loop](05_Systems/Game-Loop.md) |
| Add save support | [Storage and Saves](05_Systems/Storage-and-Saves.md) |
| Link two consoles (multiplayer) | [Link Cable](05_Systems/Link-Cable.md) |
| Do a raster / HUD split | [Effects and Raster](03_Graphics/Effects-and-Raster.md) |
