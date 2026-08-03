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
