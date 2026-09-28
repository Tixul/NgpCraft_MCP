# Measuring Performance

How to get a performance number on NGPC that means something — and the traps that
produce numbers which look fine and are worthless.

Every figure on this page was **measured**, on an emulator with silicon-calibrated
wait-states or on hardware, in real projects (one of them a vertical shmup, ~330 KB ROM,
20 fps, heavy sprite and tilemap load). Where something is inferred rather than measured
it says so. Effect sizes are of course project-specific — the **methods and the traps**
are not.

> `Debug-Tools.md` documents the on-device profiler *API*. This page is about the
> *method*: what to measure, in which scene, and how to tell a real number from a
> broken one.

> **Credit:** this page was contributed by
> [Napsterix](https://github.com/Napsterix), from measurements taken while building a
> full NGPC game. Figures kept as measured; instruction-cost claims re-sourced against
> the Toshiba manual (see [TLCS-900/H Reference §37](../02_CPU-and-Toolchain/TLCS900-Reference.md)).

---

## 1. The one number that must be right first

**Wait-states.** An emulator that fetches cartridge code for free runs fetch-bound code
roughly **3.4x too fast**, and the whole cost model inverts: code that is expensive on
silicon looks free, and optimizations that help measure as "exactly zero".

Measured on one project, same scene, same build:

| | no wait-states | with wait-states | factor |
|---|---|---|---|
| cycles/frame | 99 638 | 286 958 | **2.88** |

See [Gameplay Patterns §3](../06_Pipeline-and-Patterns/Gameplay-Patterns.md) for the
silicon calibration itself (`cart_wait=3`, cart data `0`, `ldir_cost=14`, `vram_wait=3`).

**Consequence for the work, not just the number:** with `cart_wait=3` every *instruction
byte* costs 3 extra cycles. Shorter code is faster code, directly. That is why some
classic size-vs-speed trades reverse on this machine (§4.2).

> Before optimizing anything, verify your emulator applies wait-states. Several
> plausible optimizations were measured at "exactly 0%" for weeks because the saving
> was in instruction fetch, which was not being billed.

---

## 2. Measurement discipline

### 2.1 The scene is part of the measurement

The single most expensive lesson. One optimization pass, three scenes:

| scene | result |
|---|---|
| dense tilemap band (the tuning scene) | **−18.1 %** |
| 10 enemies on screen | **−2.9 %** |
| level start | **−8.3 %** |

Same code. On hardware, in the scene the player actually complained about: **1 %**.

The changes were not wrong — they hit the wrong bottleneck. Related: one technique
(padding structs to powers of two) was worth **1.2 %** in an empty scene and **12.9 %**
in a dense one.

**Rule: decide which scene actually drops frames *before* optimizing, and measure in
that one. A block distribution or a cycle count without naming the scene is worthless.**

### 2.2 Never measure at the frame cap

If the game waits for N VBlanks per frame, a per-frame VBlank counter **cannot go below
N**. At 3 VBlanks/frame the display floors at that value and every frame that would have
needed less is dragged up to it.

Two test ROMs were once handed to a tester with predictions that were both below the
floor — unreachable by construction.

**Always measure with the cap lifted** (an `UNCAP_FPS` build flag), or better with the
VBlank wait removed entirely so you get pure compute time.

### 2.3 A/B/A, not A/B

At effect sizes around 1 %, a single pair does not separate signal from noise. The third
run measures the *repeatability of the measurement*, which is the thing you actually
need to know. In frozen scenes, an A→B→A once returned **269 / 265 / 269** — noise below
one unit, so a 4-unit difference is real.

### 2.4 On-device A/B needs a frozen scene

Free play is not repeatable; an 8 % change cannot be separated from how the run went.
Build explicit benchmark scenes: fixed number of enemies at fixed positions, speed 0,
no spawns, scrolling stopped.

⚠️ **Verify the frozen scene actually contains the thing you are measuring.** A worm
benchmark scene looked ideal for testing a worm-related bug and reported "no difference"
— the scene sets `spawn_pending = 0`, so the code path under test never ran at all.

⚠️ **A more expensive scene must not measure cheaper.** Once a "10 enemies + 3 crates"
scene read *faster* than "10 enemies" (176 vs 234). Cause: the crate flag only takes
effect together with the freeze flag, so the second ROM was plain gameplay with
scrolling and spawns, not a frozen scene at all. **If a heavier scene measures lighter,
the scene is built wrong.**

### 2.5 The display resolves whole units — lengthen the window, not the load

A 1.5 % effect at a displayed value of 049 is *one unit*, indistinguishable from noise
on hardware. Raising the measurement window from 30 to 120 frames multiplies the number
by four at the same ratio: one unit becomes four. Check the accumulator width and the
digit count first.

Read the **average**, not the instantaneous value — in frozen scenes the latter jumped
by 18 units between two states, far more than the effect being measured.

### 2.6 You cannot measure a block by switching it off

Disabling a spawn-distribution loop reported **11.9 %**. The real cost was **1.3 %**.
The disabled loop also stopped spawning enemies, so the measurement included everything
those enemies would have caused: movement, drawing, collisions.

**For any block that *creates work*, replace only the mechanism and keep the effect
identical.**

### 2.7 A guard must be cheaper than what it prevents

A visibility check that skipped off-screen map objects was **48 % slower than no guard
at all** (375 778 vs 253 650 cycles/frame) because the guard itself used a linear search.
Rewritten as two integer comparisons against the known ring range, the same idea became
the single biggest win of that pass (−25 %).

### 2.8 Identical numbers are a warning, not a confirmation

A real code change never produces byte-identical cycle *and* instruction counts. Twice
this exposed a broken measurement rather than a neutral change:

- `make` did not rebuild a second translation unit when only one `.rel` was deleted —
  the "new" ROM was the old one (identical 275 855 cycles **and** 20 875 instructions).
- A constant was changed and the audio output stayed identical to the decimal place —
  because the effect that constant scaled was never being triggered (§3.2).

**Always check the ROM checksum before and after, and scan the linker output for
errors.** On a RAM overflow the linker aborts, the *old* ROM stays on disk, and the
measurement cheerfully reports "+0.0 %".

---

## 3. Writing emulator probes

Automated probes are how you measure behaviour rather than eyeball it. They fail in
their own specific ways.

### 3.1 Wait for an event, never for a fixed number of frames

A probe that waits "300 VBlanks and then reads the value" reads it at an arbitrary point
— possibly ~100 game frames after the moment of interest, by which time the world has
moved on. One such probe reported a bug in the first two checkpoints of a game that did
not exist: at those two positions the terrain happened to be open, so the world scrolled
on during the fixed wait while at the other six it was blocked.

**Wait for the condition itself** (a counter becoming non-zero, a state variable
changing), then read within one frame.

### 3.2 Verify the start condition reaches the state you think it does

A sound probe waited for "the scroll register changes" to detect gameplay. The hardware
scroll register also moves on the **title screen**, because a starfield scrolls there.
The condition was satisfied after **8 frames**; entering gameplay actually takes **254**.
Twelve seconds of "gameplay audio" were recorded that contained not a single shot, and
led to a wrong conclusion about the sound engine.

**Assert that the thing you want to observe is actually happening** — count the events
(shots fired, enemies killed) and abort the probe if the count is zero. A recording with
no shots in it sounds exactly like a recording whose shots are inaudible.

### 3.3 Check struct offsets against the source

Reading `active` at offset 0 of a bullet struct returned the *x coordinate* — which for
a shot is always non-zero, hence always "true". The counter reported 2 shots where there
were 28. The struct was `{ u8 x, y, active; ... }`.

Padding matters too: if structs were padded to a power of two for indexing speed (§4.2),
the stride is the padded size, not `sizeof` of the fields.

### 3.4 Input timing must match the game's frame rate

At 20 fps a game frame spans **three** VBlanks. A key held for two VBlanks can fall
entirely between two `input_update()` calls and never produce an edge. Hold for at least
one full game frame, or hold continuously if the game supports it.

### 3.5 Never compare behaviour using no-wait benchmark ROMs

A behaviour probe comparing two `BENCH_NOWAIT` builds reported 798 differing samples.
The difference *was the speed-up*: without VBlank waiting, execution speed depends on
compute time, so the faster ROM simply advanced further in the same emulated time.

**Behaviour comparisons use normal builds; only cycle measurements use no-wait builds.**

### 3.6 A metric that does not isolate the effect is not counter-evidence

A probe measuring shot range read the y coordinates of *all* sprites in the pool —
enemies and stars included. Both ROMs reported y = 0 and it looked like the bug did not
exist. Reading the bullet array directly showed the real difference (20 → 0).

---

### 3.7 Bench traps met on a finished game

- **The BIOS copies the joypad port into `0x6F82` once per VBlank, one frame late.** A value
  is only seen if TWO VBlanks pass between writing the port and the game's read. Writing
  `0x6F82` yourself makes it flicker; writing the port just before the read makes the bench
  depend on the ROM's speed. Correct: decide the input at loop turn *t*, write only the port,
  right after the VBlank that opens the turn — read at *t+1*.
- **Benches can depend on LOST presses.** A menu walk had its "right" prefix only because
  five A presses in a row were being lost on one page. Once presses stopped being lost (see
  [Input](Input.md) §7.5) the walk broke. Every menu walk must follow the page it READS and
  wait for the press to be consumed before sending the next.
- **Write the choice, do not navigate to it.** On a fresh save only one track is unlocked:
  N presses to the right all land on the first one, and a profiler measured the same track
  ten times — which the ten identical cycle counts betrayed. Write the menu's selection
  variable, then **check the result** (read the track length) before concluding.
- **A probe build must be a CLEAN build.** When the probe is a `-D` compile flag and object
  files share one directory, building the probe without `clean` links objects from the normal
  build: a hybrid ROM that half-probes.
- **A probe block has owners.** A 32-byte debug block with bytes beyond it "unused" is not
  free — those addresses belong to nobody in particular. Values written there read back right
  once in 150 frames. Allocate probe fields explicitly and list their readers.
- **Date samples by the GAME's tick, not the emulator's frame.** With the loop running once
  per three frames, a lateral position that stays frozen three frames then moves looks
  exactly like an impact (three steering steps at once): 58 false "overshoots".
- **Take thresholds from a measurement, never from a comment.** One comment's lateral budget
  was wrong by a factor 1.7.
- **An emulator that writes flash into the ROM file** makes benches read the player's save:
  wipe the save block in memory at boot in every bench.
- **Two computations of the same thing always drift apart.** Tools that keep their own copy
  of a list (tracks, thumbnails) must REFUSE to run when the game has an entry they do not
  know — a tool that silently measures 9 tracks out of 10 says "all green"; one that
  computes quantiles over 9 while the ROM uses 10 accuses a healthy track. **A gate that
  accuses an innocent costs more than a silent one.** And break every gate on purpose once:
  a gate that has never failed has not been shown to work.

## 4. Techniques with measured outcomes

Sizes are project-specific; the *sign* and the reasoning generalize.

### 4.1 Skip work that is off-screen — but check cheaply

Per-frame updates that iterate every object regardless of position are the classic find.
In one project, six animated objects placed near the end of the level cost their full
price across the *entire* level because their update was position-independent and each
one did a linear search per cell. Gating on a numeric range comparison: **−25 %**.

### 4.2 Struct padding to powers of two — and when it backfires

A non-power-of-two `sizeof` makes every `array[i].field` emit a multiply. On TLCS-900/H a
word `MUL RR,r` is **14 states** against **2** for a register-register `LD` — so a struct
index costs about **7x** a move, and more from memory (`MUL RR,(mem)` = 16). See
[TLCS-900/H Reference §37](../02_CPU-and-Toolchain/TLCS900-Reference.md) for the full
cost table. Padding the hot structs took multiply sites from **1143 to 642**: **−12.9 %**
in a dense scene, −1.2 % in an empty one.

⚠️ **It reverses.** Padding an 18-byte struct to 32 measured **+1.2 % slower**: the
field offsets no longer fit the 8-bit displacement form (`(r32+d8)` → `(r32+d16)`), and
with `cart_wait=3` every extra instruction byte costs 3 cycles. Padding also costs RAM,
and on a machine with 8 KB the linker will eventually refuse.

**Pad the hot arrays, measure each one, and watch the displacement boundary.**

### 4.3 Pointer walking instead of index arithmetic — measure each loop

The same transformation, two loops in the same project:

| loop | result |
|---|---|
| turret-shot **draw** loop | **−2.5 %** |
| enemy **update** loop | **+1.9 % (slower)** |

The difference is register pressure: in the update loop the pointer does not stay in a
register and the compiler spills it. This was re-measured twice, once with wait-states,
and stayed slower both times.

**Rule: pointer walking only in loops with few live values, and measure each one
separately.**

### 4.4 Cheap early-outs beat clever data structures

A per-frame loop over 16 projectile slots ran even when none were active. A single
`if (!any_active) return;` on a flag that already existed: **−1.5 %** at level start.

The same project measured that a *full* pass over all 79 object slots costs **4.0 %**
(9 394 cycles/frame) — so a bitset for free-slot search was evaluated and **deliberately
not built**: the cheap early-out had already taken most of it, and in the scene that
actually dropped frames the slots were *not* empty, so it was worth only 0.4 % there.

> If a bitset replaces an `active` field, it must **replace** it, not accompany it.
> Two sets of bookkeeping drift apart silently. Deleting the field turns every missed
> site into a compile error.

### 4.5 Measured and rejected

Kept here because "we tried that" is worth as much as "that worked":

| idea | measured |
|---|---|
| spawn list as index-sorted table | −1.3 %, but changed behaviour (a milestone shifted 12 VBlanks) — **reverted** |
| halving the enemy update rate | **+9.6 %**, and enemies stopped despawning |
| drawing only every other frame | the separately halvable parts totalled 1.7 % |
| flicker-multiplexing player shots | **+1.4 %** |
| shadow OAM buffer | slower, and never again |

**A 1.3 % gain is not worth one unexplained behaviour change.** If a regression run
shifts by 12 VBlanks and you cannot say why, the change is not ready.

---

### 4.6 What an instruction costs when the code runs from the cartridge

Code executes from the cartridge, with its wait states: **the price of an instruction follows
the number of BYTES it makes the CPU fetch**, not its apparent complexity. Measured
instruction by instruction in a race loop:

| form | bytes | cycles |
|---|---|---|
| register-register (`ld wa,de`, `add hl,bc`) | 2 | 8 |
| `ld wa,(xix)`, `dec 0x2,xix` | 2 | 8–10 |
| `(xiz+d)`, `(xsp+d)`: a field reached through register + displacement | 3 | 14–16 |
| `ld wa,imm16`, `cp wa,0x80` | 4 | 16 |
| **register-indexed** `(xhl+bc)`, `(xiy+wa)` | **5** | **22** |
| **absolute address** `ld a,(_sym)` (24-bit) | **5** | ~22 |
| `mul xwa,de` | 2 | 25 |
| call with stacked parameters | — | ~100 and up; **~300** with several |

Observed average: **11–14 cycles per instruction.** Consequences:

* you win by executing FEWER instructions, not cleverer ones;
* **assembly only pays where the compiler emitted far too many instructions** — and where
  everything fits in registers: a first assembly walk that re-read its parameter block every
  slice saved 4 000 cycles out of 49 000; the rewrite that kept everything in registers saved
  21 000;
* **work done for nothing pays as much as assembly**: look first for UNEXPECTED CALLS in a
  per-function profile (two accessors called thirty times per turn in a loop, three calls
  per roadside post, a HUD label rewritten every turn, 72 tile writes to clear one text);
* **a per-segment cache** beats a shorter computation: a value that depends only on the
  current segment does not change while you stay in it;
* **a multi-parameter call costs ~300 cycles before doing anything**: a sprite-put function
  called ~20 times per turn became a macro inside the race loops.

### 4.7 Look at the WORST turn, block by block, on every scene

* **A mean of 2.00 VBlanks per turn can hide turns at 3** that the pacing catches up on the
  next one — a stutter. Count VBlanks **turn by turn** (a jitter histogram), on every track,
  for the whole race, not the first 600 frames.
* **An optimisation that wins on average can lose where it matters.** Computing a band colour
  once per slice instead of once per line "always wins" — measured, it was WORSE on flat road
  (+1 700 cycles) and barely better on climbs. A "same corner as the previous slice" cache
  (71–76 % repeats) won NOTHING: the memory compare and taken jump cost what the multiply
  cost. Both removed.
* **Profile the worst turn by basic block**: a band-fade loop, nine instructions per word and
  only on climbs, weighed 6 400 cycles in the heaviest turn — invisible in the average.
* **Put public twin labels on every function, basic block and static variable** (they emit no
  byte) and refuse the profiling build if the ROM differs by one byte from the shipped one:
  then every cycle is attributed by name on the real ROM.

### 4.8 An averaged lap does not measure a rate

The same bench driver covered 10 908 units in 6 000 frames before AND after a scroll-rate
change: the closed loop compensates (it reaches corners earlier, leaves the road more, spends
more time slow). What the player feels is the INSTANTANEOUS ratio at a READ speed — measure
units per frame bucketed by speed, and separately frames per loop turn on the shipped ROM.
Neither alone says what the eye sees.

## 5. Regression: prove behaviour did not change

Speed work is only safe with an independent behaviour check. A scripted self-player that
drives the game through a fixed route and records the VBlank count at each milestone
(shop entered, boss reached, level end) catches what a cycle counter cannot.

Two things this catches that nothing else does:

- **Unexplained behaviour drift** from an "obviously neutral" optimization.
- **Your own stale baseline.** When milestone numbers were compared against figures from
  an older build, a harmless change looked like a 123-VBlank regression. Rebuilding the
  previous state produced a **byte-identical ROM** — proving the code was fine and the
  *baseline* was stale.

> **A baseline you do not refresh after every change to the run itself becomes an alarm
> that gets ignored.**

⚠️ **A self-player with invulnerability enabled will not find damage, collision or
projectile bugs** — frozen enemy shots cost nothing, so no milestone moves. Changes in
those areas need a probe that reads the objects themselves.

---

### 5.1 The equivalence gate: prove an optimisation computes the same thing

A faster ROM is not at the same place at the same frame, so **do not compare images: compare
loop turn by loop turn.** A breakpoint on the prologue of the frame-pacing function stops the
machine once per turn; dump all working RAM (stack excluded), the sprite table and its
palettes, on the reference and on the candidate, with the same joypad turn by turn.

* **What depends on time is not guessed, it is measured:** run the reference at TWO cartridge
  speeds; every byte that differs between the two runs is excluded, **by name** (sound, clock,
  VBlank counter, DMA pointers, HUD sprites — ~300 bytes; no road table or driving variable).
  **Always read the exclusion list after freezing a reference**: one frozen on a fast ROM
  excluded the lateral position and the shear tables as "time-dependent" because the bench
  wrote the joypad at the wrong moment — a blind gate.
* **Compare by symbol name** (`symbol+offset` from each ROM's map), so adding a variable does
  not shift the comparison. Translate pointers — into ROM *and into RAM* (a pointer to one of
  two buffers) — to symbols first.
* **Aliases:** an array regrouped into a struct for assembly changes NAME, not content; map
  old name → new location and say so on screen.
* **Run it on every track**: a defect broken on purpose in the SLOPE path passes on flat
  tracks. And it only covers the start of each race (400 turns): it is a regression gate for
  the computation, not an acceptance test of the whole track.
* It does not see VRAM: changes that write the planes are checked by looking.
* **Break it on purpose** after any change of mechanism (a flipped bit in a table must be
  caught at turn 0, under its OLD name).
* Keep the readable C version of every assembly routine behind a switch: it is the reference
  the gate compares against.

A bench of an emulator core may ignore a breakpoint on the FIRST instruction of each call, or
slice frames down to one instruction: turns then skip at random. Step with the lowest-level
run call and test the PC against the breakpoint yourself.

### 5.2 Stack depth in the heaviest scene, by witness

Fill the free zone between the last variable and the stack top with a witness byte (`0xA5`),
play the heaviest scene (race, several tracks, thousands of frames), and read how deep it was
overwritten. On a finished racer: **~300 bytes deep**, where a note claimed "~64" — and a
first optimisation that added 168 bytes of arrays had the stack overwrite the last variables
(menu, cable, engine sound), which the equivalence gate saw as SOUND variables changing.
**Every RAM byte you add comes out of that margin: measure it after each addition**, per
scene (race, pause, menus, saving) — margins of 24–60 bytes were measured on different
scenes of the same game.

**Free RAM you already own:** a 512-byte save buffer that is kept in RAM is 512 bytes whatever
it contains — a new *field* there costs no RAM, a new `static` does. Transient state (a HUD
cache, a 12-car pack's state, a ghost reader) can live in the buffer's unused tail, guarded
by compile-time asserts on offsets — see [Storage](Storage-and-Saves.md) §4.6.

### 5.3 Prove a ROM is fresh by its BYTES

A build that says nothing proves nothing (see [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) §8.5b —
header changes are not tracked). Search the ROM for a string you just added (must be there)
and one you just removed (must not be). A dead string still present = stale ROM, and every
measurement taken on it goes in the bin.

## 6. Hardware traps the emulator cannot show

These produce a working emulator build and a broken cartridge.

### 6.1 RAM that is not what you assumed at power-on

The emulator clears RAM at reset. **Hardware does not.** A `static u8 flag;` read before
first write returns whatever was in the chip at power-on.

One such byte cost an entire title sequence: a "high scores already shown" flag was
non-zero at power-on, so the intro tick never ran, one text band stayed at its initial
2-scanline height, and the high-score page never appeared. Two symptoms, one byte.

**Set every static that is read before it is written explicitly at a deterministic point
in `main()`.**

⚠️ **And verify your own startup code actually initializes `.data` and clears `.bss`.**
In one project a `static u8 x = 2u;` arrived as **0** on hardware — that is not a CPU
quirk, it is a `crt0` that never copied the initialized-data image out of ROM. Check the
bootstrap before blaming the silicon; see
[TLCS-900/H Reference §10](../02_CPU-and-Toolchain/TLCS900-Reference.md) for the exact
crt0 sequence.

**Reproduce, do not guess:** fill RAM with a pattern right after reset
(`0xA5` over `0x4000`–`0x6BFF`) and boot. That turns "works here" into a measurement.

⚠️ When probing this, remember that whatever you *wait for* is also garbage. A loop
"until `lives != 0`" exits immediately on a 0xA5 fill, before the game has executed
anything.

### 6.2 VRAM writes during active display

The VBlank window is about **24 200 cycles** (47 non-visible lines x 515 cycles/line at
6.144 MHz). A glyph upload measured at ~24 200 cycles ran *guaranteed* into active
display, where the hardware is reading tile data — invisible on the emulator, "the intro
doesn't work" on hardware.

Any bulk VRAM write outside the normal game frame needs its **own** VBlank sync. And a
counter that paces those waits must be **reset per batch**: one that kept counting across
text blocks started each new batch at an arbitrary phase, sometimes with only one or two
glyphs of room left in the window.

### 6.3 Work inside the VBlank ISR

A raster-split table rebuild inside the VBlank ISR overran the window whenever the table
changed, arming the MicroDMA too late: wrong scroll on the topmost scanlines *and*
dropped frames — one cause, two symptoms that look unrelated. Measured **45 VBlanks** per
zoom phase against an ideal 24; rebuilding only the changed 16-line window brought it
to **32**.

Note this was only visible **with wait-states enabled** — without them the same test
showed 27 vs 24, which reads as noise.

---

## 8. Measuring the CONSOLE against an emulator — a frame-counting recipe ⭐

You do not need a logic analyser, and you cannot use a stopwatch: the 8-bit timer
up-counters on this CPU are **not readable**, so a start/stop chronometer on an event
needs an ISR that perturbs what it measures. Count **frames** instead.

One frame is 16.67 ms (515 cycles × 199 lines × 60 Hz). Run a fixed workload for a fixed
number of frames and count how many times it completes; the same ROM in an emulator gives
the comparison. Bake the emulator's own figure into the ROM next to the live one and a
photograph of the screen becomes the whole report.

### What this turned up on real consoles (2026-08-19)

Four workloads, 30 frames each, two consoles — the counts agreed to within one:

| workload | what it isolates | emulator vs silicon |
|---|---|---|
| unrolled register ops, no memory | the raw core | **+9 %** |
| 16 reads from a `const` array in **cartridge** | cart data access | **+7 %** |
| 16 reads/writes in **work RAM** | RAM access | **+7 %** |
| the real BIOS-call turnaround (`COMGETDATA` ×4) | the BIOS COM path | **+23 to +30 %** |

Two results worth carrying:

- ✅ **A cartridge data read costs the SAME as a RAM read.** The ROM/RAM ratio is
  **1.034 on silicon and 1.034 in the emulator** — identical. Instruction *fetch* from
  cartridge is wait-stated; a data read is not, and that is now measured rather than
  assumed.
- ⚠️ **Long instructions are where fetch wait dominates.** Tracing the BIOS-call loop
  showed **81 % of its time in cartridge code**, not in the BIOS, and for the 4-to-6-byte
  instructions that call machinery uses, **fetch wait is up to 69 % of the instruction's
  cost**. Short 1-2 byte loops barely feel it. That is why the same machine looks 7 %
  fast on a simple loop and 30 % fast on a call-heavy one — and why a single global
  "wait states" number cannot describe both.

⚠️ **Trap for anyone building such a bench:** a headless harness must switch the cartridge
wait states ON explicitly. Left at their back-compat default of zero, cartridge code runs
about **three times too fast** and every figure the bench produces is measured against a
machine nobody actually runs.

---

### 8.1 A failure that only happens on the console — one binary, one-byte variants

When a player reports "the console powers off / crashes at X" and no emulator reproduces it,
the fastest way to a cause is a controlled set of builds, not more rewrites:

1. **Freeze one binary** — the exact ROM that fails. Rebuilding changes addresses (a path
   string embedded by an assert is enough), so derive every variant from that image.
2. **Make variants that differ by one byte or one patch**, each testing ONE hypothesis:
   the feature disabled (e.g. the save function returns immediately), candidate fix A, candidate
   fix B. Put added code in free ROM space and jump to it, so nothing else moves.
3. **Check the variants in the emulator first** for control and data flow only (the path runs,
   the stack returns balanced, the data is written) — not as proof of the hardware behaviour.
4. **Test each variant separately on the same cartridge and the same initial save state.**
   Record cartridge model/capacity, the flashing tool and how it treats the save sectors, the
   exact moment of failure, and whether it is a real power-off or a black/frozen screen.
5. **Verify the outcome after a power cycle** (e.g. the new best score is still there).

Before blaming padding: padding with `0xFF` leaves the prefix identical — compare two images
byte by byte; two "A/B" ROMs that differ in thousands of bytes of code are not a padding test.

What emulators typically do NOT reproduce, and therefore what to suspect first: interrupts
during a flash busy window, watchdog timing, low-battery shutdown requests
([Storage](Storage-and-Saves.md) §5.0d, §5.2b), narrow-divide overflow
([Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) §8.1f), and bits the emulator never sets (a joypad bit
used as "power", [Input](Input.md) §4.3).

## 7. Checklist

Before trusting a performance number:

- [ ] Wait-states enabled in the emulator
- [ ] Measured in the scene that actually drops frames, and the scene is named
- [ ] Frame cap lifted
- [ ] A→B→A, noise below the effect size
- [ ] ROM checksum changed; linker output free of errors
- [ ] Behaviour regression run, baseline current
- [ ] For behaviour probes: normal build, not a no-wait build
- [ ] Probes wait for events, and assert the event actually occurred

---

## See Also

- [Gameplay Patterns](../06_Pipeline-and-Patterns/Gameplay-Patterns.md) — the silicon
  wait-state calibration
- [TLCS-900/H Reference](../02_CPU-and-Toolchain/TLCS900-Reference.md) — instruction
  cycle costs (§37)
- [Debug Tools](Debug-Tools.md) — on-device profiler
- [Game Loop](Game-Loop.md) — frame pacing, frame budgets
