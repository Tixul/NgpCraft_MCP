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

## ⏱️ Timing: `ngpc_emu_native_run` bills the cartridge bus. Do not turn that off by accident

The cartridge flash is slow, and on this console it is the dominant cost: **every
instruction is fetched across it**. The raw C++ core defaults to *free* fetch for
backward compatibility, which makes a machine measured that way ~2.9× too fast — so this
tool applies the silicon-calibrated set on every run and **tells you which model ran**, in
the `timing` field of the answer.

| Knob | Silicon | Meaning |
|---|---|---|
| `cart_wait` | **3** | cycles per byte of **instruction fetch** from cart |
| `cart_data_wait` | **0** | a cart data read costs the same as RAM (measured; an earlier 5 was refuted) |
| `ldir_cost` | **14** | cycles per byte of `LDIR`/`LDDR` (the datasheet's 7 is a floor) |

`timing: "free"` exists to reproduce a measurement taken before wait-states existed. **It is
not a hardware claim** — never pass it to answer a "how fast is this?" question, and if a
number came out of a free run, say so when you quote it.

**The trap it protects you from.** With fetch unbilled, any optimisation whose gain is
*fewer instruction bytes* measures as **exactly zero**, because the one thing it saves is
the one thing not being charged. On silicon every extra byte of encoding costs 3 ticks, so
**code size is speed** — which is also why padding a struct to a power of two can backfire
(offsets fall out of the 8-bit displacement form and every access grows).

Still true: a cycle figure from one run is a measurement of *this* run. For a breakdown of
where a frame goes, the emulator app's **Profiler** (F1) is the right instrument.

---

## 🩺 Every run reports two hardware faults most emulators never mention

`ngpc_emu_native_run` returns an `hw_safety` block, always:

```jsonc
"hw_safety": {
  "counts": { "watchdog-starved": 1, "system-stack": 0 },
  "clean": false,
  "first": [ { "kind": "watchdog-starved", "pc": 2097216, "detail": 6144000, "cycle": 6144006 } ]
}
```

- **`watchdog-starved`** — the BIOS hands the console over with the watchdog **armed**, so
  from instruction one the cart owes I/O `0x006F` the clear code `0x4E` (roughly every
  100 ms). Starve it and a real console **resets itself**. `detail` is the period it was
  armed for.
- **`system-stack`** — the cartridge's `XSP` wandered into `0x6C01..0x6FFF`, the BIOS's own
  page. Nothing complains at the time; the console dies later, somewhere else entirely.
  `detail` is the offending `XSP`.

Both are **counted, not fatal** — neither halts a real console at the instruction that
commits it, so neither halts this one. That is the point: a homebrew build that resets on
hardware and plays fine in every emulator is exactly the bug this catches. `hw_guard: true`
turns the first one into a stop when you want a verdict or the exact PC.

⚠️ **`"clean": true` means the counters were read and were zero** — it is a measurement, not
a default. Say "no watchdog or stack fault in those N frames", not "the ROM is fine": these
are two specific faults, not an audit.

---

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
- **Timing is only as good as the model you asked for.** `ngpc_emu_native_run` is
  silicon-timed by default, but a run made with `timing: "free"` is ~3× optimistic — check
  the `timing` field of the answer before quoting any cycle figure.
- **`hw_safety: clean` covers two faults, not the whole machine.** It is not a verdict on
  the build.
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
