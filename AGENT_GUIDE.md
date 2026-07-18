# Agent guide — how to actually use this server

You are an LLM with ~40 NGPC tools. This tells you which one answers which question,
in what order, and — just as important — what each one **cannot** tell you.

---

## ⚠️ Read this first: without a BIOS, the emulator lies to you

Every tool that runs or renders a game needs a real `bios.bin`, supplied by the user.
It is not shipped here and never will be.

**Without it the picture is wrong in a way that still looks plausible.** The interrupt
vector table lives in the BIOS. With no BIOS it is all zeroes, so the first interrupt
sends the PC to address 0 and the game is dead — while the screen keeps showing the last
thing it drew. You will see a normal-looking frame and conclude the game works.

This is not hypothetical: it is the first mistake made while building these tools, and it
cost an hour of chasing an "input bug" that was a dead CPU.

So: **always pass `bios_path`, and if the user has no BIOS, say so before showing them
any conclusion about rendering or behaviour.** Some games go further — *Metal Slug — 2nd
Mission* checks the console really booted through its BIOS and silently disables fire and
jump if not, so the game runs, looks perfect, and is unplayable.

---

## The three backends, and the question each answers

| Backend | Question it answers | Tools |
|---|---|---|
| **Native core** (C++) | *What happens when the game runs?* | `ngpc_emu_native_run` |
| **Inspector** (Python) | *What is in this ROM / this frozen state?* | `ngpc_emu_*` (~20) |
| **Interpreter** (JS) | *Does this C source I just wrote behave?* | `ngpc_quickrun`, `ngpc_lint`, `ngpc_screenshot`, `ngpc_visual_diff`, `ngpc_psg_trace` |

Plus knowledge/build tools: `ngpc_doc_search`, `ngpc_bug_check`, `ngpc_asm_pattern`,
`ngpc_api_lookup`, `ngpc_example`, `ngpc_disasm`, `ngpc_new_project`,
`ngpc_validate_project`, `ngpc_compile_homemade`, `ngpc_compile_official`,
`ngpc_png_to_sprite`, `ngpc_png_to_tilemap`.

**The distinction that matters:** only `ngpc_emu_native_run` *executes* the machine.
Everything named `ngpc_emu_*` other than that one reads a **static image** — a ROM at
reset, or a save state. They will happily describe a frozen moment; they cannot tell you
what happens next.

---

## 🎯 The workflow that makes bug reports solvable

A user can nearly always reproduce a bug but rarely describe it in the terms you need.
So don't ask them to describe it. Ask for a **save state**:

> "In NgpCraft Emulator, get to just before it goes wrong, press **F2**, and send me the
> `.s0` file from `savestates/` — plus which ROM it was taken from."

Then you can replay their exact machine:

```jsonc
// "what happens if I press A right here?"
ngpc_emu_native_run {
  rom_path: "…/GAME.ngc",
  bios_path: "…/bios.bin",          // ← never omit
  state_path: "…/GAME.s0",
  hold: "A",
  frames: 30,
  screenshot_path: "…/after.png"
}
```

Run it twice — once with `hold` empty, once with the button — and compare. That single
comparison is what separates "the input never arrives" from "the game ignores it", and it
is the difference between guessing and knowing.

Every inspector tool also accepts the same `.s0` via `seed_from`, so once you have their
state you can open it with `ngpc_emu_oam_info`, `ngpc_emu_palette_info`,
`ngpc_emu_tilemap_info`, `ngpc_emu_memory_dump`, `ngpc_emu_screenshot`.

⚠️ **The `.s0` format carries no ROM hash.** Loading it against the wrong cartridge gives
nonsense rather than an error. Always ask which game it came from.

---

## Symptom → tool

| The user says | Reach for |
|---|---|
| "it crashes / freezes" | `ngpc_emu_native_run` (read `stop_status`, `pc`), then `ngpc_disasm` around that PC |
| "the graphics are wrong" | `ngpc_emu_native_run` with `screenshot_path`, then `ngpc_emu_palette_info` / `_oam_info` / `_tilemap_info` on the same state |
| "the controls don't work" | `ngpc_emu_native_run` twice, with and without `hold` — compare the frames |
| "does my C code compile / run?" | `ngpc_lint`, then `ngpc_quickrun` |
| "is this ASM safe on silicon?" | `ngpc_bug_check`, `ngpc_asm_pattern` |
| "how do I do X on NGPC?" | `ngpc_doc_search`, `ngpc_example`, `ngpc_api_lookup` |
| "build me a ROM that must run" | `ngpc_compile_official` — the Toshiba chain. **Not** `ngpc_compile_homemade`, see below |
| "start a new game" | `ngpc_new_project`, then `ngpc_validate_project` |

---

## ⚠️ The homemade toolchain is a teaching tool, not a compiler you ship with

`ngpc_compile_homemade` runs a clean-room TLCS-900 pipeline (t900cc / t900as / t900ld)
written from scratch. **It is unfinished and unstable.** It exists so someone can read it
and understand how a compiler for this CPU works end to end — not to produce a ROM anyone
intends to flash.

Two failure modes to hold in mind:

- **It mis-compiles.** Silent wrong codegen is possible, so a ROM it produces running
  badly is not evidence about the user's C.
- **It rejects valid code.** Never tell a user their source is broken because this
  refused it — check with `ngpc_compile_official` first.

For anything that must actually run on hardware or in the emulator, use
`ngpc_compile_official` (Toshiba cc900 / asm900 / tulink, user-installed).

## Honesty boundaries — what these tools will not tell you

- **`ngpc_emu_screenshot` (inspector) composes from memory**, not from the beam. For a
  static screen it is right; for a scrolling game or a raster split it can be wrong,
  because the hardware latches scroll per line and games rewrite it mid-frame. When the
  picture matters, use `ngpc_emu_native_run`'s `screenshot_path`, which is drawn line by
  line as the beam passes.
- **Inspector tools do not execute.** `ngpc_emu_tick_frame` advances the *timing model*,
  not the CPU.
- **`ngpc_quickrun` is a JS interpreter, not the console.** It is for checking your own C
  quickly; it is not evidence about hardware behaviour.
- **`ngpc_font_bake` is a stub** and returns `not_implemented`.
- **A decoder that refuses is telling the truth.** `ngpc_emu_decode` and
  `ngpc_emu_opcode_coverage` report what they cannot decode rather than inventing it —
  an "unimplemented" answer is data, not a failure.

---

## Requirements

- **Python 3** on PATH — the emulator backends are Python.
- **The compiled core** at `vendor/emulator/cpp/build/ngpc_core.dll` for
  `ngpc_emu_native_run`. A prebuilt Windows x64 binary ships here; on other platforms:
  `cmake -S vendor/emulator/cpp -B vendor/emulator/cpp/build && cmake --build vendor/emulator/cpp/build`
- **A real `bios.bin`** from the user, for anything that runs or renders. See the warning
  at the top — this is the single most common cause of a confident wrong answer.
- **Node 18+** for the server itself.
