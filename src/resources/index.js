import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CORPUS = join(__dirname, "..", "..", "corpus");

// URI → { file (relative to corpus), name, description }
const RESOURCES = {
  // Listed first on purpose: it is the map for everything below, and it carries the
  // BIOS warning that decides whether any rendering answer can be trusted at all.
  "ngpc://doc/agent_guide": {
    file: "../AGENT_GUIDE.md",
    name: "Agent guide - which tool answers which question",
    description:
      "READ FIRST. How to drive this server's tools: the three emulator backends and what each can and cannot tell you, the save-state workflow for reproducing a user's bug, a symptom-to-tool table, and the BIOS requirement (without a real bios.bin the emulator renders a plausible but WRONG picture).",
  },
  "ngpc://doc/quickstart": {
    file: "DENSE_INDEX.md",
    name: "NGPC Dense Index",
    description:
      "High-density entry point: memory map, key registers, timing, ABI, gotchas, and a page index. Read this first before generating any NGPC code.",
  },
  "ngpc://doc/hw_registers": {
    file: "wiki/01_Hardware/Hardware-Registers.md",
    name: "NGPC Hardware Registers",
    description: "Full hardware register map (VDP, PSG, DMA, input, BIOS).",
  },
  "ngpc://doc/palettes": {
    file: "wiki/03_Graphics/Colors-and-Palettes.md",
    name: "Palettes & Colors",
    description: "RGB444 encoding, 16 palettes, transparent color rules.",
  },
  "ngpc://doc/sprites": {
    file: "wiki/03_Graphics/Sprites-and-OAM.md",
    name: "Sprites & OAM",
    description: "64 sprites, H/V chaining, metasprites, OAM layout.",
  },
  "ngpc://doc/tilemaps": {
    file: "wiki/03_Graphics/Tilemaps-and-Scrolling.md",
    name: "Tilemaps & Scroll",
    description: "SCR1/SCR2 32×32 tilemaps, viewport, scroll, mapstream.",
  },
  "ngpc://doc/dma": {
    file: "wiki/03_Graphics/DMA.md",
    name: "DMA Guide",
    description: "DMA channels, limitations (no VBlank DMA!), OAM streaming.",
  },
  "ngpc://doc/audio": {
    file: "wiki/04_Audio/Audio.md",
    name: "Audio (T6W28 PSG)",
    description: "3 tone + 1 noise, envelopes, BGM stream format, Z80 driver.",
  },
  "ngpc://doc/input": {
    file: "wiki/05_Systems/Input.md",
    name: "Input & Joypad",
    description: "Pad register at 0x6F82, edge detection, repeat timer.",
  },
  "ngpc://doc/bios": {
    file: "wiki/01_Hardware/BIOS.md",
    name: "BIOS Reference",
    description: "BIOS calls (SWI), SYSFONT, power button behavior, RTC.",
  },
  "ngpc://doc/asm": {
    file: "wiki/02_CPU-and-Toolchain/Assembly.md",
    name: "TLCS-900/H ASM Guide",
    description: "Assembly language overview tailored for NGPC.",
  },
  "ngpc://doc/t900_dense_ref": {
    file: "wiki/02_CPU-and-Toolchain/TLCS900-Reference.md",
    name: "TLCS-900/H Dense Reference",
    description:
      "Compact opcode reference for the TLCS-900/H subset used by NGPC.",
  },
  "ngpc://doc/collision": {
    file: "wiki/05_Systems/Collision.md",
    name: "Collision Detection",
    description: "AABB, tilemap collision, pixel-perfect tricks.",
  },
  "ngpc://doc/math": {
    file: "wiki/05_Systems/Fixed-Point-Math.md",
    name: "Fixed-Point Math",
    description: "Q8.8, LUT patterns, sin_table, atan approximations.",
  },
  "ngpc://doc/game_loop": {
    file: "wiki/05_Systems/Game-Loop.md",
    name: "Game Loop Architecture",
    description: "VBlank sync, frame budget, state machine patterns.",
  },
  "ngpc://doc/asset_pipeline": {
    file: "wiki/06_Pipeline-and-Patterns/Asset-Pipeline.md",
    name: "Asset Pipeline",
    description: "PNG → sprite/tilemap/font, naming conventions.",
  },
  "ngpc://doc/storage": {
    file: "wiki/05_Systems/Storage-and-Saves.md",
    name: "Flash Save / RTC",
    description: "Flash chip layout, ngpc_flash_* API, RTC access.",
  },
  "ngpc://doc/language": {
    file: "wiki/05_Systems/Localization.md",
    name: "Localization & C Subset",
    description:
      "BIOS language detection, bilingual ROM, string tables, system font.",
  },
  "ngpc://doc/build_toolchain": {
    file: "wiki/02_CPU-and-Toolchain/Build-Toolchain.md",
    name: "Build & Toolchain",
    description: "C89 rules, far pointers, ABI, known bugs, CC900 pipeline + TAC IR.",
  },
  "ngpc://doc/debug_tools": {
    file: "wiki/05_Systems/Debug-Tools.md",
    name: "Debugging",
    description: "On-device CPU profiler, ring-buffer log, runtime assert.",
  },
  "ngpc://doc/gameplay_patterns": {
    file: "wiki/06_Pipeline-and-Patterns/Gameplay-Patterns.md",
    name: "Gameplay Patterns",
    description: "State machines, pacing, genre patterns, entity management.",
  },
  "ngpc://example/stargunner/shmup_doc": {
    file: "stargunner/SHMUP.md",
    name: "StarGunner Shmup Architecture",
    description: "Full architecture of the reference shmup — read for 'how to build a real NGPC game'.",
  },
  "ngpc://example/stargunner/retex": {
    file: "stargunner/TEMPLATE_RETEX.md",
    name: "StarGunner Template Retex",
    description: "Post-mortem of what worked / broke when porting the shmup to the 2026 template.",
  },
};

export const resourceDefinitions = Object.entries(RESOURCES).map(
  ([uri, meta]) => ({
    uri,
    name: meta.name,
    description: meta.description,
    mimeType: "text/markdown",
  })
);

export async function readResource(uri) {
  const meta = RESOURCES[uri];
  if (!meta) throw new Error(`Unknown resource: ${uri}`);
  return await readFile(join(CORPUS, meta.file), "utf8");
}
