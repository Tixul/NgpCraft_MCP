# Agent guide — how to actually use this server

> **Inactivity policy (2026-10-01):** disable the BIOS ten-minute idle request
> by clearing `User_Answer` / `HW_USR_ANSWER` (0x6F86) bit 6; keep reserved bit 5
> clear (`HW_USR_ANSWER &= (u8)0x9F;`). Continue handling every nonzero
> `HW_USR_SHUTDOWN` (0x6F85), including battery and POWER requests. This is a
> precaution pending investigation, not a confirmed hardware-defect diagnosis.

You are an LLM with 43 NGPC tools. This tells you which one answers which question,
in what order, and — just as important — what each one **cannot** tell you.

---

## ⚠️ Read this first: BIOS requirements for commercial-game checks

For commercial-game native runs and compatibility checks, use a real `bios.bin`,
supplied by the user. It is not shipped here and never will be. `ngpc_studio_run`
is the exception: Studio games use the dedicated clean-room HLE BIOS bundled here;
the JS interpreter tools do not need a real BIOS.

**Without the BIOS services a game expects, the picture can still look plausible.** The interrupt
vector table lives in the BIOS. With no BIOS it is all zeroes, so the first interrupt
sends the PC to address 0 and the game is dead — while the screen keeps showing the last
thing it drew. You will see a normal-looking frame and conclude the game works.

This is not hypothetical: it is the first mistake made while building these tools, and it
cost an hour of chasing an "input bug" that was a dead CPU.

So: **for commercial games, pass `bios_path`, and if the user has no real BIOS, say so
before drawing compatibility conclusions.** *Metal Slug — 2nd Mission* checks a boot-time
BIOS fingerprint in char RAM. The current native hand-off reset restores it from the
cartridge when missing (`vendor/emulator/core/bios_fingerprint.py`), preventing the
disabled fire/jump behaviour on a fresh reset. Older save states may retain the previous
disabled-controls state; retest from a fresh reset.

---

## The three backends, and the question each answers

| Backend | Question it answers | Tools |
|---|---|---|
| **Native core** (C++) | *What happens when the game runs?* | `ngpc_emu_native_run`, `ngpc_studio_run` (Studio games) |
| **Inspector** (Python) | *What is in this ROM / this frozen state?* | `ngpc_emu_*` (~20) |
| **Interpreter** (JS) | *Does this C source I just wrote behave?* | `ngpc_quickrun`, `ngpc_lint`, `ngpc_screenshot`, `ngpc_visual_diff`, `ngpc_psg_trace` |

Plus knowledge/build tools: `ngpc_doc_search`, `ngpc_bug_check`, `ngpc_asm_pattern`,
`ngpc_api_lookup`, `ngpc_example`, `ngpc_disasm`, `ngpc_new_project`,
`ngpc_validate_project`, `ngpc_compile_homemade`, `ngpc_compile_official`,
`ngpc_png_to_sprite`, `ngpc_png_to_tilemap`.

**The distinction that matters:** `ngpc_emu_native_run` and `ngpc_studio_run` (for Studio
games) *execute* the machine on the native core.
Everything named `ngpc_emu_*` other than `ngpc_emu_native_run` reads a **static image** — a ROM at
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
  bios_path: "…/bios.bin",          // ← use for commercial-game compatibility checks
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

## NgpCraft Studio (GB Studio projects) — a different workflow

When the game is an **NgpCraft Studio** project (`.gbsproj`: GB Studio 4.3.2 compiled for the NGPC by the
NgpCraft V3), you do not write C: you write GB Studio resources (`project/**/*.gbsres`, JSON). Four tools:

| You want to | Tool |
|---|---|
| write an event (its args, its child scripts, its NGPC status) | `ngpc_studio_event` — never guess an event's JSON |
| know whether the project is valid, fits, compiles, builds; what the save can hold | `ngpc_studio_check` (after every edit) |
| see it work: a scene, a scenario of buttons, variables by name, screenshots | `ngpc_studio_run` |
| understand a save that "does not work" | `ngpc_studio_save` |

Rules that cost real time on these games (trigger scripts freeze the player, one Switch Scene per shared
script, text redrawn as a whole, saves = one slot for a big game...): `ngpc_doc_search` "NgpCraft Studio"
→ `corpus/studio/NGPCRAFT_STUDIO_RULES.md`.

`ngpc_studio_run` boots with the clean-room **HLE BIOS** shipped in `vendor/emulator/hle_bios/`: the code the
V3 generates only uses the BIOS services it implements, and it is what the editor's own player runs. The
warning above (no BIOS = a lying picture) is about commercial games and C code calling other services; pass
`bios_path` to run a real BIOS. It counts **video frames** (60 a second) with the cartridge's wait-states:
a slow scene init shows as frames of the same picture — look at the screenshots, not only at the variables.

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
- **A real `bios.bin`** from the user, recommended for native commercial-game runs and
  compatibility checks. `ngpc_studio_run` uses the bundled dedicated HLE BIOS by default;
  the JS interpreter tools do not need a real BIOS. See the warning at the top.
- **Node 20+** for the server itself.
