# Forward-View Pseudo-3D Road — two shipped engines, measured

A forward-facing road that fans out in perspective is the single most-asked-for effect on
this machine, and there is more than one way to do it. Two **commercially released**
cartridges solve it differently, and both were read out of a running emulator rather than
guessed: one is a driving game, the other a rail-vehicle simulation. Neither is named here
— what follows is the mechanism, which is what transfers.

Everything below was measured against the running game (per-scanline register logs,
frame-by-frame table dumps) and then re-verified by porting it. Where a figure comes from
our own port rather than from a cartridge, it says so.

> **Prerequisite:** [Effects and Raster](Effects-and-Raster.md) §1.4 for the per-scanline
> scroll mechanism itself, §1.4b for how a *hill* works, §1.4c for how to debug one.

---

## 1. The principle, in one sentence

One background plane is **sheared**: every scanline gets its own horizontal scroll value, so
a plane that holds a straight strip of tarmac bends into a perspective road. The world image
never changes — verified on one cartridge as **zero bytes touched over 600 frames**. What
changes is a table of registers, once per frame.

The two engines differ in **what fills that table**.

---

## 2. Engine A — sample a profile at a quadratic index

The cheaper of the two. It does not *compute* a curve; it **samples a signed profile table
at a quadratic index**.

```
index(k) = (k * k) / DIV                 k = 1..BANDS
scrollX[LINES - k] = profile[base + index(k)] - profile[base]
base = profile_pointer + corner_offset
```

Three things make this work, and all three are easy to get wrong.

**The index is quadratic, and the window is small.** On the cartridge, `index(k) = k²/64`
for 48 bands, so only **37 profile entries are ever read**. A whole "segment type" therefore
fits in 37 signed bytes. Verified **597 times out of 600** against the table the game itself
produced, live.

**The law generalises by keeping that 37-entry window:**

```
DIV = BANDS² / 36
```

48 bands → 64 (the cartridge). 56 bands → 87 (our port).

**The table fills DOWNWARD.** Band `k` lands on line `LINES − k`, so `k` grows toward the
top of the screen: zero deviation under the player, maximum at the horizon. That is what
puts the vanishing point where the player is, and reversing it puts it on the bumper.

**Advance and steering are the SAME control.** A single offset slides the sampling base
along the profile. While the window sits in a flat region the road is straight; it curves as
soon as the window reaches a ramp. There is no separate "corner engine" — *the profile IS
the track*, and a long profile with long ramps and long flats is a real circuit.

**One plane, not two.** Measured over 600 frames of driving: the cartridge **never** writes
the per-line table of the second background plane. There is no line-scroll parallax. The
other plane is a fixed layer (HUD, or a ground plane).

⚠️ **Align the bands to the road you actually drew.** If the bands overrun the painted
tarmac, the part of the fan that falls in the sky is wasted. Our port uses horizon 96 with
56 bands, which is exactly lines 96-151 of the art.

---

## 3. Engine B — a staircase of slices, with a wider per-line table

The other cartridge spends more per line and buys hills with it.

**Eight bytes per scanline**, not one scroll value: `SCY`, `SCX`, and **three 15-bit
colours**. The tarmac that streams past is a background palette entry rewritten every line;
the haze bands come from the same table.

**The road is a staircase.** 48 slices of two map rows each, walked from the bumper line
upward. Each slice is granted **0, 1 or 2 screen lines** by adding a per-gradient *rate*
twice into an 8-bit accumulator and counting the carries. The gradient model is the ratio
described in [Effects and Raster](Effects-and-Raster.md) §1.4b.

**Curvature is linear ramps that sum to a parabola.** Each slice adds a 16-bit word from the
segment's curvature profile into an accumulator; `SCX` is the high byte. The 21 profiles are
straight lines:

```
profile[k][i] = round(0.75 * k * i)
```

so the curvature increment of a slice is **proportional to its distance**. Accumulating from
the bumper upward makes the total grow as **i²** — the parabola of a corner, with **no
multiply at runtime**.

🔑 **The ramp index is the GLOBAL slice index, not an index local to the segment.** A distant
corner is therefore sampled in the strong part of its ramp automatically, and it "arrives" on
its own as the slices come closer. Get this wrong and corners snap into existence instead of
approaching.

---

## 4. The track format that makes both cheap: four bytes per segment

| byte | bits | field |
|---|---|---|
| 0 | 0-4 + sign in 5 | **gradient**, sign-magnitude |
| 0 | 6-7 | high bits of a roadside-scenery code |
| 1 | 0-4 + sign in 5 | **curvature**, sign-magnitude |
| 1 | 6-7 | low bits of that code (one value = finish line) |
| 2 | 0-5 | parameter 1 |
| 3 | 0-5 | parameter 2 |
| 2, 3 | 6-7 | event code (the centred corner sign; one value clears it) |

Two details worth stealing:

⚠️ **Sign-magnitude, not two's complement.** Bit 5 is the sign, `AND 0x1F` the value — so
"+0" and "−0" both exist and the decoder has to mean it. A classic sign-extend reads
garbage.

🔑 **The reversed circuit is FREE.** A mirror flag makes the loader `XOR 0x20` the curvature
field (flipping its sign bit) and take `0x3C − p1` for the parameter. One dataset, two
racing directions.

**And the author writes ramps, never steps.** On a measured track the gradient takes only
eleven distinct values and always arrives as `0, +2, +5, +10, …, +10, +5, +2, 0`; corners
likewise `0, 1, 2, 4, 4, 4, 2, 1, 0`. A hill or a corner is never dropped in as a single
segment — the ramp *is* the shape.

---

## 5. Where the gameplay comes from: the same bytes

The most transferable idea in either engine. **There is no separate physics data.** The
bytes that draw the road are the bytes that drive the car.

**Gradient → speed, and it is deliberately asymmetric.** Reading the gradient of the nearest
segment: climbing decelerates by `1.5 × gradient`, descending accelerates by `8 × |gradient|`,
both added into an 8-bit accumulator that only acts on carry (so the gain is `B/256` speed
units per frame).

> **A descent gives back 5.3 times what a climb costs, at equal gradient.** At −10:
> +0.31 units per frame. At +10: −0.06. That ratio is an arcade choice and it is what makes
> a descent an *event*. Measured against the running game: over the 200 frames of a descent
> ramping −2 to −10, speed rose +0.215/frame, exactly what the gradient ramp predicts.

**The crest jump is a subtraction.** The car takes off when the gradient **drops** from one
segment to the next, and the airtime is `speed × drop`, gated by a minimum. No "bump" test,
no geometry: with a writing scale of 0, ±2, ±5, ±10, ±15, ±20 you need a drop of at least two
steps (`+10 → 0`) and some speed.

**A corner pushes from a distance that depends on speed.** The push is read from the
curvature of segment `n`, where `n = min(speed / 16, 7)`. Faster means the corner starts
pushing from further away — the same table, sampled deeper.

---

## 6. Traps, all of them paid for

- ⛔ **Quantising the gradient without a dead band flickers.** See
  [Effects and Raster](Effects-and-Raster.md) §1.4b.
- ⛔ **A descent gives nothing if the top row of the sky board is not a uniform colour** —
  repeating it reads as a hard-edged stripe, which forces you to clamp descents and costs
  you half the relief. Fix the art before tuning the numbers.
- ⚠️ **A short hill is not a weak hill.** The intuition that a segment shorter than the
  look-ahead only unfolds a fraction of the relief is **wrong**: measured, a 400-unit bump
  reaches the same full deflection as an 1800-unit plateau. It just takes ~300 units to get
  there.
- ⚠️ **A hill sells itself by how much road it removes**, so it needs road to take. The same
  numbers read as timid on a 32-slice strip and strong on a 48-slice one. If your relief
  looks weak, check the strip before the constants.
- ⚠️ **Clamp the table index.** Gradient tables are indexed `centre + gradient`; a track
  byte outside the intended range walks straight off the end.

---

## 7. Corners that ARRIVE — per-scanline look-ahead

Everything above describes the *shape* of the shear. This section is about its *content*:
where the curvatures you put into it come from. It is the fix for the most common complaint
about a homemade forward-view racer — "the corner appears all at once, you never see it
coming" — and it was built and measured on a complete game.

### 7.1 Why one curvature number cannot work

A shear built from **one number** — the curvature of the segment the car is on — tilts the
whole ribbon the same way, horizon to bumper. A corner does not approach, it **switches on**.
Smoothing that number over *time* makes it worse to describe than to watch: a straight road
that slowly becomes a curved one, in one block, at the exact moment you enter it.

⚠️ **No smoothing value fixes this.** Temporal smoothing is a substitute for geometry, and
what is missing is geometric: nothing in the drawing knows what lies beyond the car.

The two corrections are independent and you need both. A correct anchoring (two terms
anchored at opposite ends, see §3) with a single curvature gives a pretty corner that
switches on; a correct look-ahead with a wrong anchoring gives a road that slides under the
wheels.

### 7.2 Each scanline looks at ITS distance

Ground at distance `d` projects toward the horizon as `1/d` — the same law that places the
sprites (§10.1). Inverted, the scanline of depth `p` (0 at the horizon, 255 at the bumper)
looks at:

```
d(p) = LOOK_NEAR * (255 - p) / p          clamped to LOOK_MAX for p = 0
```

`LOOK_NEAR` is **the** knob: it sets how much road is on screen, therefore how long a corner
takes to come down the screen. Measured at 8 profile units per frame, full throttle:

| `LOOK_NEAR` | top line looks at | first pixel of corner | shear at mid-approach |
|---|---|---|---|
| 30 | 926 units | 33 frames before | −16 px |
| **60** | 1852, clamped to 1600 | 43 frames before | **−27 px** |

What changes most is not *when* the corner appears (ten frames) but **how fast it deepens
once it has appeared**. At 30 it points and stays discreet until the entry; at 60 it digs
in while you arrive, and that is what reads as a corner coming.

⚠️ **Anchor on DEPTH, not on scanline number.** A road board that samples its image
unevenly (88 image rows squeezed into a 48-line band, say) crushes the whole approach into
the bottom of the screen if distances are built from the line index `k`.

### 7.3 Sample the profile by a WALK, never by searches

Distances only grow going up the screen, so the segment cursor never moves backward: start
from the current segment and walk forward.

```c
cursor = seg_index;  into = seg_pos;  walked = 0;
for (k = lines - 1; ; --k) {                 /* bumper -> horizon */
    want = ahead[k] - walked;
    while (into + want >= span[cursor]) {
        want  -= span[cursor] - into;
        cursor = (cursor + 1) % TRACK_LEN;   /* the walk WRAPS, see below */
        into   = 0;
    }
    into  += want;
    walked = ahead[k];
    bend[k] = blended_curvature(cursor, into);   /* §7.5 */
    if (k == 0) break;
}
```

~48 comparisons per frame, not one division. **The walk must wrap** past the end of the
track: the top of the band sees beyond the finish line, and a profile that stopped there
would draw the last few hundred units of every lap perfectly straight.

⚠️ **The band samples very unevenly.** In `1/d`, the top lines cover hundreds of units and
the bottom ones a few. On a 32-line band with `LOOK_NEAR` 60:

| line | 0 | 1 | 2 | 3 | 8 | 16 | 31 |
|---|---|---|---|---|---|---|---|
| looks at | 1600 (clamped) | 1600 (clamped) | 896 | 577 | 175 | 56 | 0 |

A 300-unit segment can fall **between two sample lines** and be seen by neither. The blend of
§7.5 makes that harmless — the profile is continuous, so something crossing the gap fades in
instead of blinking. **Without a blend, a sampled look-ahead makes distant corners
flicker** — a defect you naturally blame on the display when it lives in the profile read.

### 7.4 Double integration, anchored at the BUMPER

Curvature is a change of direction: the heading reached at a distance is the sum of the
curvatures up to it, and the lateral offset of the tarmac is the sum of that heading.

```c
slope = 0; acc = 0;                               /* 1/256 px per line, 1/256 px */
for (k = lines - 1; ; --k) {
    slope += signed((|bend[k]| * cm + 128) >> 8); /* round the MAGNITUDE, re-sign */
    acc   += slope;
    dx[k]  = acc >> 8;
    if (k == 0) break;
}
```

Two properties make the change safe:

1. **A road of CONSTANT curvature comes out exactly as before.** The double sum is a
   quadratic — the ramp the engine used to make by squaring. What is new only shows on a
   MIXED profile: straight near, curved far.
2. **Summing from the bumper puts the zero under the car**, where the car really is.
   Anchoring at the horizon makes the road slide under the wheels every time something
   changes a second ahead.

The gain `cm` brings a constant curvature `B` back to `dx(horizon) = B`:

```
cm = 512 * 256 / (N * (N + 1))        N = lines in the band: 124 for 32, 56 for 48
```

⚠️ **`512 * 256` is 131072 and a cc900 `int` is SIXTEEN bits.** Written as is, the
constant wraps to zero, the gain is zero and **every corner in the game is drawn straight**
— with nothing that looks like an overflow, since a table full of zeros looks like nothing.
Compute it in two steps (half, rounded, then times four) or in `u32`. It also depends on the
band height: **recompute it when a track with a different band loads**.

⚠️ **Round the gain multiplication.** With `cm = 124` a 6-unit kink truncates to 2 and comes
out a third too flat. `+128` before `>> 8`, on the magnitude, then re-sign — otherwise the
rounding is not symmetric.

**The 16-bit budget — redo it before changing anything:**

| quantity | worst case | `s16` ceiling |
|---|---|---|
| `mag * cm` | 62 × 124 = 7 688 | 32 767 |
| `slope` (accumulated curvature) | ~30 × 48 = 1 440 | 32 767 |
| `acc` (accumulated offset, 1/256 px) | 256 × 62 = 15 872 | 32 767 |

There is a factor of two of headroom, not ten. **Doubling the curvature range or the band
height overflows `acc` silently** — and an overflow there does not crash, it flips the road
in the middle of a corner.

⚠️ **Check the SIGN on screen, not in your head.** With the display showing
`plane_pixel − scroll`, a positive `dx` accumulated at the horizon slides the road LEFT; the
drift must agree (a curvature that bends the road one way throws the car toward the
*outside*). Control: enter a right-hand corner and check the car is pushed left. One port
wrote this sentence backwards, believed it, and its rival took every hairpin leaning the
wrong way.

### 7.5 Blend in DISTANCE: a corner is not a point

A segment table makes a corner start at a POINT. Two corners that touch meet as a **step**
(−62 to +62): drawn, the road changes shape in one frame; driven, the car is thrown the
other way in one frame. A real road enters a corner in a spiral — and a run of alternating
hairpins with nothing in between is exactly where this gets reported first.

Hence a blend **in distance**, centred on each joint:

```c
/* the nearer half decides, otherwise a segment shorter than the blend
   would be pulled by both ends at once */
if (rem <= into && rem < HALF)  { other = next;     w = HALF - rem;  }
else if (into < HALF)           { other = previous; w = HALF - into; }
else                             return bend[cursor];
return bend[cursor] + (((bend[other] - bend[cursor]) * w) >> 8);
```

`BEND_BLEND = 256` units (`HALF = 128`) — 256 so the blend divides by a shift. `w` rises to
128, so the blend reaches exactly half on the joint, where the next segment's blend picks
it up at the same value.

**In distance, never in frames**, and that is the point: the image and the physics read
**the same function**, one at the scanline's distance, the other at zero. They cannot
disagree. A blend counted in frames re-creates a gap between what is drawn and what pushes
the car, and lasts the same at 20 km/h as at 200 — wrong in both cases.

### 7.6 The physics reads the same thing, at distance zero

The term that drifts the car becomes `blended_curvature(current segment, position)`. What
remains of temporal smoothing is only a **damper**: a blend sweeps at most ~124 units over
256 of road, about 4 per frame at full throttle, so a cap of 6 per frame is never reached on
a normal joint — it only catches what a blend cannot round, a segment shorter than the
blend itself.

*Why this matters:* with the smoothing at 2 per frame (from the days when that variable also
drew the road) and hairpins coming in pairs, the car spent four fifths of the second corner
pushed by a number still on its way to the one displayed. Reported as "at the car, entry and
exit feel wrong".

### 7.7 What rides on the road follows for free — and must not cache

Everything placed on the road (roadside posts, the rival, props) takes its X from
`centre_at(line) = art_centre − shear[line]`. As soon as the table is curved, an object at
mid-screen stands on the tarmac **as drawn**, not on an imaginary straight line.

⚠️ **Never cache `centre_at()` between frames**, and never recompute an object position from
"the current corner's" curvature. Both give an object floating beside its road during
transitions — visible only in chains of corners, which is where nobody reads the code.

**The parallax follows the HEADING, not the far curvature.** The bands above the road (sky,
ridges, tree line) integrate `current_bend × speed`: they move by what the nose of the car
actually swept. Wired to the far curvature, the scenery would start turning before the car
does — which reads as scenery sliding.

### 7.8 Per-line clamps apply to the SUM

Each line has a minimum and maximum `dx`, computed from the art when the track loads: the
road is a triangle, its far end is a few pixels wide, and past some offset the 256-px ring
brings the opposite side of the image in from the edge. **Clamp the sum** of the two terms
(lateral + curve), not each one.

Consequence to know before touching `LOOK_NEAR`: the further you look, the more the double
integration accumulates, and a distant hairpin plus a car already at the edge can
**saturate**. The symptom is clean — the horizon freezes at one value — and it comes from the
clamp, not from the maths. Read `dx` at the horizon on an established corner: if it sticks
to the clamp, that is the cause.

### 7.9 The order inside the frame is not negotiable

```
drive()                    physics reads the current (blended) curvature
track_advance()            advance along the profile, recompute the current curvature
build_road_ahead()         sample the profile AT the line distances
road_frame_ahead()         integrate and write the shear table
rival / scenery / props    read centre_at()
```

⚠️ Sampling before advancing draws the road **one frame late**: invisible when stopped, and
at speed a permanent eight-unit gap between what you see and what you drive.
⚠️ Placing sprites before the shear is written puts them on **last frame's** road — again
only visible in chains of corners.

### 7.10 What it changes in the GAMEPLAY

The blend is not cosmetic. Curvature now starts 128 units **before** the joint, so **the
corner bites earlier**: a flat-out driver who turned in at the last moment now runs wide.
With the same crude autopilot, one descent went from 3 809 to 7 629 frames — half of that is
the bot's clumsiness, but the direction is right: **the track got harder**. `BEND_BLEND` is a
difficulty setting as much as a rendering one — shorter gives back dry joints, longer
softens chains and asks you to anticipate.

**Corner warning signs became unnecessary** — they existed because the shear could only show
a corner already begun. The road now announces itself. ⛔ **What must stay:** the read of the
*next* corner one segment ahead (≈900 profile units). It is the only number that knows what
is coming, and it is what tells the AI rival to lift before a bend; without it the rival
takes every corner flat out.

### 7.11 Dead ends, all tried

| Tempting move | What it gives |
|---|---|
| Smooth the curvature in TIME | A corner that still switches on, only softer. Smoothing cannot show what lies beyond the car. |
| Keep temporal smoothing **on top of** the distance blend | Pure latency between image and car. The blend already limits the sweep rate. |
| Sample the profile **without** a blend | Two touching hairpins meet as a step: the road changes shape in one frame and the car is thrown the other way. |
| Anchor the integration at the **horizon** | The road slides under the wheels whenever something changes a second ahead. |
| Build distances from the **scanline number** | Correct only while a scanline is a fixed slice of road — wrong as soon as a board samples its image unevenly. |
| Compute the gain `cm` **once for all** | It depends on the band height, which changes with the track. Recompute on load. |
| Trust "no error" from the build | The toolchain accepted a gain of zero. Check the BYTES and the screen. |

### 7.12 How to verify

Read the per-scanline register log of the emulator (the plane-2 X scroll, `0x8034`) — see
[Effects and Raster](Effects-and-Raster.md) §1.4c. `dx = (log[line] − base) & 255`, signed.

⚠️ **First check WHERE the band is in the log.** A bias inherited from another harness
(logging 20 lines late) once made the HUD band — flat by construction — look like the road,
and nearly concluded "the ramp does not descend". The identification that needs no prior
knowledge: dump the 152 entries and look at the RANGES — constant values (sky, ridges,
trees), then N values climbing one by one (the road), then a constant (the HUD). If the
climbing part is not there, do not read further. And the road band may start **one line
above** your `road_top` variable: a one-line offset reads a value that exists and is wrong.

What to expect, frame after frame, on a whole descent: `|dx|` non-zero at the horizon while
the car's own curvature is still 0 (the corner forms far away); frame-to-frame change ≤ ~4;
on an established corner, a ramp from −62 at the top of the band to 0 at the bumper.

---

## 8. Hills — the complete staircase recipe (verified against the cartridge)

§3 described Engine B's hills in one paragraph. This is the full recipe, with constants, the
algorithm, the tables, and a bench that tells you whether your port is right. Every figure
was obtained by a **controlled experiment**: breakpoint just before the road builder runs,
overwrite the eight segment gradients in RAM, read what the engine does with them — which
sweeps the whole range without waiting for the track to climb and without patching the ROM.

### 8.1 The model in six equations

Reference geometry: **48 slices**, always; one slice = **2 rows** of the road board (a
96-row board); a **103-line** band served by the raster; 56 rows of scenery above. For a
gradient `g`, **clamped to ±20**:

```
(1) rate          = 128*(1+g/20)   if g >= 0      41-entry table, 128 = flat
                  = 128/(1-g/20)   if g <= 0      (fits every entry to 1 unit)
(2) road_lines    = SLICES * rate / 128           24 .. 96, 48 when flat
(3) horizon       = BOTTOM + 1 - road_lines       the bottom is NAILED
(4) scenery_lines = BAND - road_lines
(5) clip          = max(0, SLICES - road_lines)   sky lines repeated
(6) scenery_shift = -clip                         the scenery comes DOWN by as much
```

And the one reading law that matters:

> **A slice ALWAYS advances 2 rows of the board. What the gradient changes is how many
> screen lines (0, 1 or 2) that slice occupies.**

On screen that gives a vertical step of **1, 2 or 4 rows per line — never 3** (a slice on
two lines = 1 row each; on one line = 2 rows; a skipped slice makes the next one swallow 4).

### 8.2 The tables

```c
/* index = 20 + gradient, gradient in [-20, +20]. 128 = flat. */
static const u8 hill_rate[41] = {
     64, 65, 67, 69, 71, 73, 75, 77,  80, 82, 85, 88, 91, 94, 98,102,
    106,111,116,121,128,134,140,147, 153,160,166,172,179,185,192,198,
    204,211,217,224,230,236,243,249, 255
};
```

Not an exponential (an exponential fit is off by 11): a **reciprocal pair**. With
`k = 1 + |g|/20`, **climbing, the road takes k times more lines; descending, each line
swallows k times more rows.** The ends are ×0.5 and ×2.

**Scrolling is a re-partition, not a scroll.** The 48 slices are shared between the eight
visible segments by a small table indexed by a sub-phase (the top three bits of the
sub-position): every row sums to 48, the nearest segment melts from 12 slices to 1, and a
new one enters at the far end. The road never moves; its slices change owner.

**Curvature profiles are linear ramps** — `profile[k][i] = round(0.75 * k * i)` — so
compute them (`(3*k*i) >> 2`) instead of storing them. **`i` is the GLOBAL slice index**, not
local to the segment: that is what makes a distant corner weigh more and "arrive" by itself.

### 8.3 The builder, in C89

It fills one entry per scanline; this table is what the MicroDMA pushes.

```c
static u8  line_row[BAND];     /* board row to show on this line */
static s16 line_x[BAND];       /* shear */

void road_build(const seg_t seg[8], u8 phase, s16 lateral, s16 heading)
{
    u8 i = 0, acc = 0, line = BOTTOM, row = BOARD_BOTTOM, n, k, c, rate, a, len;
    s16 bend = 0;
    /* acc is reset HERE, once per frame -- that keeps the dither identical
       from one frame to the next (pitfall 6) */

    for (n = 0; n < 8; n++) {                           /* the road */
        len = seg_len[phase][n];
        if (len == 0) break;
        rate = hill_rate[20 + CLAMP(seg[n].grade, -20, 20)];
        for (k = 0; k < len; k++) {
            /* 1. how many screen LINES this slice takes: 0, 1 or 2.
                  The rate is added TWICE; the carries count. */
            c = 0;
            a = acc + rate; if (a < acc) c++; acc = a;
            a = acc + rate; if (a < acc) c++; acc = a;
            /* 2. curvature accumulates EVEN IF the slice takes no line */
            bend += curve_ramp(seg[n].curve, i);
            /* 3. write. The two lines of a stretched slice show
                  CONSECUTIVE rows -- not the same row twice. */
            if (c >= 1) { line_row[line] = row;     line_x[line] = (bend >> 8) - lateral; line--; }
            if (c == 2) { line_row[line] = row - 1; line_x[line] = (bend >> 8) - lateral; line--; }
            /* 4. the slice ALWAYS advances two rows, even when skipped */
            row -= 2;
            i++;
        }
    }

    if (line >= TOP + (BAND - TOP - SLICES)) {
        /* CASE A -- short road: the scenery comes DOWN and sticks under the
           horizon 1:1, then its top repeats its highest row. */
        u8 sky = line_row[line + 1] - 1;
        while (line >= TOP) {
            line_row[line] = (sky > SKY_TOP) ? sky : SKY_TOP;   /* clip */
            line_x[line]   = heading - lateral;                 /* scenery follows HEADING */
            if (sky > SKY_TOP) sky--;
            line--;
        }
    } else {
        /* CASE B -- long road: the scenery does NOT move, the road eats it
           from below. */
        while (line >= TOP) {
            line_row[line] = line;
            line_x[line]   = heading - lateral;
            line--;
        }
    }
}
```

Five points are non-negotiable, each paid for by a visible defect:

* **A two-line slice shows `row` then `row − 1`.** The reference engine gets there by
  copying the *offset*, not the row: since the displayed row is `line + SCY`, the same
  offset on two lines gives two different rows. **Copying the row makes a staircase** — and
  this is precisely where the board's reserve of vertical resolution is spent: climbing
  shows the intermediate rows that flat road throws away.
* **`row -= 2` happens in every case**, even for a skipped slice. That creates the step of 4.
* **`bend` accumulates even when `c == 0`.** A skipped slice still exists for the corner;
  forgetting it "loses" curvature over crests.
* **`acc` is not reset between segments** — only at the start of the frame. The dither runs
  across segment borders, otherwise the joints show.
* **Scenery follows the HEADING, the road follows the CURVATURE** — two different shears in
  the same frame.

### 8.4 The scenery, exactly

| | condition | what the scenery does |
|---|---|---|
| **case A** | `road_lines ≤ 47` | 55 **rigid** lines (1:1) stuck under the horizon, then `48 − road_lines` lines **repeating the highest sky row**. The scenery has come down by `48 − road_lines` px. |
| **case B** | `road_lines ≥ 48` | `103 − road_lines` lines at their **natural place** (zero offset). The scenery does not move; the road eats it from below. |

Verified over the whole range: `clip = 48 − road_lines` to the pixel, and
`55 + clip + road_lines = 103` in every case. Two consequences to accept, because they are
the rendering: climbing, the scenery/road seam **hides** `road_lines − 45` rows (tree line,
foot of the mountains) — that is what a hill does; and **the row repeated by the clip must be
plain sky** — any detail in the board's first row gets combed over 18 lines.

### 8.5 The shear is computed per SLICE, not per line

Same curvature forced under three gradients:

| gradient | road lines | horizon → bumper shear | amplitude |
|---|---|---|---|
| +10 | 71 | 18 → 44 | **26 px** |
| 0 | 47 | 18 → 44 | **26 px** |
| −10 | 30 | 19 → 44 | **25 px** |

> **A corner has the same total amplitude whatever the hill.** There are always 48 slices,
> curvature accumulates per slice, and the hill only redistributes the sum over more or fewer
> lines. Computed **per screen line**, a climb exaggerates the corner and a crest flattens it
> — wrong both ways and impossible to tune.

### 8.6 Twelve pitfalls, all hit

1. **Clamp the gradient to ±20.** The index is `20 + g`; outside, the engine reads the
   neighbouring tables. Measured: +31 → the road drops to **5 lines** with **14-row** steps.
   It does not crash, it becomes nonsense.
2. **An end-of-line interrupt paints the NEXT line.** In HBlank mode the entry for line `L`
   serves `L+1`. The MicroDMA has its own offset — measure it, do not assume it.
3. **Never scale the scenery.** It is 1:1, always. All the perspective is in the road.
4. **Anchor at the bumper**, never at the horizon. The horizon is a RESULT.
5. **A step of 3 does not exist.** If you get 3-row steps, the accumulator is wrong: the
   rate is added **twice** per slice and the carry is 0, 1 or 2 lines.
6. **Reset the accumulator every frame, and only there.** Measured: **117 frames out of 120
   give exactly the same pattern** at constant gradient. A free-running accumulator shimmers.
7. **A two-line slice shows two CONSECUTIVE rows**, not the same one twice.
8. **Accumulate curvature on skipped slices too.**
9. **The board must hold two rows per slice.** THE lock: flat, you drop one row out of two,
   and that reserve is what lets you stretch up to ×2 without duplicating a row. A board
   shown 1:1 when flat can only staircase.
10. **The first row of the scenery board must be uniform** (§8.4).
11. **The player's car is part of the hill.** It does not sit on the bumper line but a few
    slices up the band, so the re-sampling moves it: measured on the reference OAM, **+8 px
    on a full climb, −5 on a full descent**, one pixel at a time. There is no curve to embed:
    the car stands **on a slice of road, like a post** — `bumper − band_lines/6` (a sixth:
    47 lines of horizon travel for 8 px of car):
    ```c
    lift = -line_shift(bumper - band_lines / 6);   /* clamp to +8 / -5 */
    ```
    where `line_shift()` is the function that already tells where an image line landed — the
    one posts use. Forget it and the car stays nailed while the road tilts under it.
12. **The lift moves the IMAGE, never the wheel line.** The car takes its X from the road
    axis read at the line of the bottom of its box, and that axis **shears** with the corner.
    Raise the box and the car drifts sideways as it climbs — reported on the very first try.
    Pass the wheel line and the image top separately to the draw.

### 8.7 The acceptance bench

The reference engine's transfer function, measured by forcing the gradient. **A correct port
reproduces these columns**, scaled to its own band.

| grad. | rate | road lines | horizon | rows/line | scenery shift | clip | seam |
|---|---|---|---|---|---|---|---|
| −20 | 64 | 23 | 88 | 4.00 | −25 | 25 | 3 |
| −12 | 84 | 29 | 82 | 3.14 | −19 | 19 | 3 |
| −4 | 111 | 38 | 73 | 2.43 | −10 | 10 | 3 |
| **0** | **128** | **47** | **64** | **2.00** | **0** | **0** | **2** |
| +4 | 147 | 56 | 55 | 1.67 | 0 | 0 | 11 |
| +12 | 194 | 75 | 36 | 1.26 | 0 | 0 | 30 |
| +20 | 255 | 94 | 17 | 1.00 | 0 | 0 | 49 |

Invariants: `road_lines ≈ 48·rate/128` (± the seam); the per-line step is only **{1,2}**
climbing, **{2,4}** descending, **{2}** exactly when flat; `road_lines + scenery_lines = 103`.
Expected dither patterns, horizon → bumper:

```
gradient +6 : 1 1 2 2 2 1 1 2 2 1 1 2 2 1 1 2 2 2 1 1 2 2 1 1 2 2 2 1 ...
gradient -6 : 4 2 2 4 2 2 4 2 2 4 2 2 2 4 2 2 4 2 2 4 2 2 4 2 2 2 4 2 ...
```

The recipe above, run as a script against the measured sweep: **0 differences over 21
gradients**, maximum error on the mean step 0.03 row.

### 8.8 What changes on NGPC

| | reference (other handheld) | NGPC / K2GE |
|---|---|---|
| per-line table | 1 byte `SCY` + 1 byte `SCX` | **2 words**: X and Y of SCR2 (`0x8034`/`0x8035`) — the table doubles |
| push | HBlank interrupt, ~109 cycles/line | **MicroDMA**, 0 CPU cycles per line |
| per-line palette | 3 colours rewritten each line (haze + bands) | a second DMA channel can push one palette word per line; three is out of budget — paint the haze into the board |
| board | 96 rows for 48 flat lines | **same rule**: `2 × flat_lines` rows |
| cadence | logic at ~24 Hz, raster at 60 | **rebuild the table at the loop's rate**, once per loop turn, not per frame |

### 8.9 Three defects found by looking, not by computing

- **The lines a descent gives back to the scenery must repeat the scenery's LAST row**, not
  get the scenery's scroll value: the scroll is an OFFSET, so line 72 with offset zero shows
  row 72 of the plane — which is where the road image starts. The road appeared twice.
- **The first road line (the vanishing point) belongs to the scenery.** Nine pixels wide,
  its shear is zero by construction; squeezed between a scenery that follows the heading and
  a road that follows the corner, it followed neither — one line jumping on its own.
- **Roadside posts must use the SAME line mapping as the road loop.** An approximation of
  "where did this image line land" derived from the gradient was exact for the previous
  mechanism and described nothing after it. Derive it from the same mapping and it is exact
  by construction.

And one about amplitude: **a hill sells itself by the road it removes**. The mechanism can
be right and the effect weak because the band is short (a 32-line band moves its top 5 px
where a 76-line band moves 39). Check the board's height before the constants.

---

## 9. "The top of the road flickers" — one symptom, five causes

One symptom reported from the console — "the top of the road flickers", "the horizon line
jumps" — had **five independent causes**, found one after the other over several days. Each
was announced as THE cause; four times the answer was "it still does it". The main lesson:
**on this symptom, a cause found is not the symptom gone.**

| # | cause | where | fix |
|---|---|---|---|
| 1 | per-scanline table in a SINGLE buffer, read by the DMA while the loop rewrites it | everywhere | two buffers, swapped at VBlank |
| 2 | the top of the band steps back one line when one slice changes gradient by one notch | climbs | one-line hysteresis |
| 3 | band colour chosen per SLICE while the slice→line mapping changes every frame | climbs | fade the bands on the lines the climb added |
| 4 | gradient notch derived by a **plain division** | climbs | half-notch dead band ([Effects and Raster](Effects-and-Raster.md) §1.4b) |
| 5 | horizontal pan of the panorama = two terms rounded separately | turning | the panorama follows the heading only |

### 9.1 Cause 1 — the table is torn

If the DMA is re-armed from a VBlank hook, the channel reads the table **during the whole
image**, one entry per scanned line — while the game loop rewrites that very table (and it
has time to: the loop takes more than one frame). Lines already scanned keep last frame's
values, the next ones get the new values: **the table is torn**, and the seam wanders with
the loop's duration. It falls, by construction, near the TOP of the screen (scanned first),
and it is worse on climbs, where the frame costs more. Measured as pixels changing between
two game frames on lines 31..38: **305–480 before, 28–64 after**.

**Fix:** double the table (and any per-line palette table). The loop writes one, the channel
reads the other, and the re-arm swaps them — the only moment the transfer is stopped and
nobody writes. Swapping is two pointer assignments. A `ready()` call after the draw says a
buffer is complete: **if a frame overran and no draw finished, there is no swap** and the
channel shows the last COMPLETE image — exactly what you want instead of a torn one. Cost:
+608 bytes of RAM (two tables of 152 words), no measurable time.

⛔ **The trap: whatever is written ONCE.** Two buffers break everything not rewritten every
frame: a HUD band written when the track loads, the "flat road" reset used by the menus —
both must now be written into BOTH buffers. And a **cache** that skips rewriting a block
when its inputs did not change is wrong with one key for two buffers: the frame after a
change would serve a buffer whose block is one frame older. Give the cache **one key per
buffer** (a cache invalidated on every swap is dead weight: it cost 0.3 VBlank per loop
turn here).

⛔ **And a cached block must not skip what the road wrote UNDER it.** The scenery block was
written after the road walk and covered the top 3 road lines ("tip hidden"). Skipping the
block on a cache hit left those lines as ROAD, so the border jumped 3 lines up then back:
**97 jumps of exactly 3 lines** — the tip size. Fix: the loop always starts at the tip; only
the part above it depends on the cache.

### 9.2 Cause 2 — the band top steps back one line

At a constant +11 gradient, the sum of the slice rates went down then up by 19 — one notch
on ONE slice (0.17 % of the sum) — because the sampled profile is not monotonic while a
sample point crosses a segment blend. Enough to lose one line of road. **Fix: hysteresis.**
The band top is a RESULT, so do not pin it; forbid it only to step back by a single line
without insisting for 16 frames. Growing is immediate; a recession of two lines or more
passes immediately (a real end of climb). 16 is measured: at 6 a request that insisted 7
frames still got through. Bascules over 140 climbing frames: 4 → 1 (hold 6) → **0 (hold 16)**.

### 9.3 Cause 3 — band colours chosen per slice

The tarmac bands (a palette entry pushed one per line by a second DMA channel) are chosen per
slice and copied to the line(s) that slice took. Flat, a slice always takes the same line:
stable. On a climb it takes 0, 1 or 2 lines depending on the carry, and that repartition
changes every frame, so a given line gets a different slice's colour each frame. Half of all
changing pixels were swaps between the two ends of the band ramp. The metric that finally
decided it needs no threshold: **what share of the road's width, on a given line, changes
from one game frame to the next** — flat 0–4.6 %, climbing 23–58 %.

Indexing bands by distance instead was tried and rejected (far bands go thinner than a pixel
and shimmer for another reason). **Fix: fade the bands on exactly the lines the climb added**
(`resting_road_top − current_top`, capped at 24): flat, nothing is faded. After: 3.8–11.3 %,
no shade swap.

### 9.4 Cause 5 — the panorama pan, two terms rounded separately

The panorama's X was `−heading × 2 + lateral / 4`, both terms already rounded to the pixel by
the caller. In a corner they move in OPPOSITE directions, each crossing its own rounding at
its own moment, so the sum advanced, retreated, advanced (6 round-trips, three of them of 3
px). Rounding once (passing sixteenths) did **not** change a single number. **The real fix:
the panorama follows the heading only**, not the car's lateral position — which is also the
correct physics: a distant background does not move because the car changes lane, it moves
because the car changes direction.

⛔ Found on the way: for a lateral value decreasing steadily (−1008, −1023, −1038) the
computed offset came out 30, 33, 32 — non-monotonic on a monotonic input. Do not trust an
expression mixing shifts and signs on this toolchain: measure it.

### 9.5 How to measure it (this is what cost the most)

- **Do not measure the image.** One image per game frame hides what moves between
  VBlanks; "first line with tarmac" catches roadside posts (use **three CONSECUTIVE** pixels:
  hundreds of false jumps drop to 29); "image returns identical" matches by coincidence on
  repetitive backgrounds (building windows); sprites change mid-scan and fake a tear.
- **Measure the TABLE** the transfer pushes: the horizon is the first line whose plane-2
  vertical offset is not zero. No colour, no sprite, no threshold.
- **The player's save state carries the scene.** The dead band of cause 4 changed *nothing*
  on the automated probe (the range it could reach did not contain the case) and removed the
  defect on the console. A save state only works with the ROM that produced it — any added
  variable moves everything — so rebuild the exact ROM first.
- **Check which track you are on** by reading its length before concluding: a menu walk
  that "presses right N times" silently lands on another track.

---

## 10. Objects on the road

### 10.1 Projection is 1/d, never linear

```c
line  = SURFACE_TOP + (SURFACE_H * NEAR) / (NEAR + d);
scale = (64 * NEAR) / (NEAR + d);                  /* 0..64 */
```

Linear placement in distance looks exactly like scenery **falling from the sky**. Precompute
both as tables (no division per object). A single division, without rounding the distance
first, keeps sizes continuous: one port used `size_step = distance / 17` and **skipped four
of thirteen sizes**; `height * 51 / (distance + 51)` gives the native size at 110 units and
reaches every step. When an object moves from a "far" slot to a "near" slot, make the two
laws meet at the same size, or it visibly restarts.

### 10.2 Distances must be allowed to go negative

Let `d` go **negative** for a short window after the camera passes. Clamped at 0, an object
crawls to the last scanline, stays there while the last units run out, then vanishes: the
"object pinned to the bottom of the screen". With `d < 0` the projection explodes and the
object sweeps off screen in two frames, as in a real car.

### 10.3 Positions on a looped track, not decremented distances

Give each object a **position** on the looped track and compute
`d = (obj_pos − player_pos) mod LENGTH` (length a power of two: the modulo is a mask).
Decrementing a distance has two visible failure modes: an object moving exactly at the
closing speed **freezes** on screen, and a quantised step (`speed >> n`) **freezes the whole
world** below some speed while the car is moving. Use fractional accumulators:
`frac += speed; pos += frac >> S; frac &= (1 << S) - 1`.

### 10.4 Anchor by the FOOT, draw dense ladders

Every frame of a zoom ladder is **bottom-aligned** in its box: the bottom edge is the ground
contact whatever the size. Anchored by the top, scenery goes into orbit. Four hand-drawn
sizes pop visibly; 6–7 steps work, the in-between ones resampled **nearest-neighbour** from
the closest drawn size (a filtered resize invents colours a 4-entry palette cannot hold). The
block of cells is usually wider than the drawing: place it by the drawing's centre, not the
block's left corner, or one side of the road is offset.

**Reference distance of a ladder.** The target width is `half_road_at_line × 16 / ref`. With
`ref` taken at contact distance, the target climbed to 33 for a largest body of 16: the ladder
**saturated** — the big body held 30 lines, the next ones 2, 6, 8, 1. With `ref` at the
shortest distance the table really uses: 22 / 3 / 7 / 7 / 7. What remains is structural (the
biggest body cannot grow further without a bigger drawing).

### 10.5 The player's car stands on a slice

The player's car is not drawn at the bumper but a few slices up the band, so its image
already corresponds to a distance. Two consequences, both measured on the OAM (bottom of
body against bottom of body):

- **Grid alignment:** a rival at logical gap 0 appeared 9 px lower (closer); the gap that
  aligns the two cars had to be measured (12 units in one port), and later the right fix was
  structural: **project the rival from the depth of the player's wheel line**, read in the
  road's distance table, and start at logical equality.
- **The finish verdict must use the same gap as the image.** The race was judged before the
  rival's update for the frame, i.e. on last frame's gap: a rival going from +2 to −8 on the
  last update lost the player a race they visibly won. Judge after the rival moves.

### 10.6 Two cars crossing: priority group first, then the lowest slot

The K2GE resolves overlap in two steps: first the **priority group** (PR.C: front > middle >
behind), then, **within a group, the lowest OAM number** wins. A two-layer player car (lights
in 0..11, body in 52..63) and a rival in 12..19, all front: the rival slides **between** the
two layers whatever car is nearer. The recipe, decided every frame on the **displayed**
bottoms (the lower bottom on screen is the nearer car):

| case | rival | player car |
|---|---|---|
| rival further | its usual slots, **middle** priority | unchanged, front |
| rival nearer | slots **lower** than the player's, front | layer 1 shifts up accordingly |

Do not put the PLAYER in middle priority to solve it: it would go behind every roadside
sprite. And compare displayed bottoms: the player car rises and falls with the hills, a
constant line is wrong in the dips. The same rule decides roadside posts vs rival: put the
roadside block in higher slots than the rival or posts get drawn in front of it.

### 10.7 Contact is measured in world units, not in sprite overlap

A contact gate calibrated on **sprite overlap** fires metres early in fake 3D: a distant
sprite is drawn higher but not smaller than the player's 24-line body, so boxes overlap while
there is tarmac between the cars. **Compute the scale instead of guessing it:** the rival
advances `speed × 137 / 2048` units per frame, the HUD shows `speed × 2 / 5` MPH, the loop
runs at 30 fps ⇒ 255 = 102 MPH = 45.6 m/s for 17.06 units per frame = 512 units/s ⇒ **1 unit
= 8.9 cm**, a 4.2 m car = **47 units**. The old gate (128) was 11 m; at 48, contacts happen
with the sprites touching. Keep the gate in the same unit as every other rate — when the
scroll rate changed by 10 %, a unit became 9.9 cm and the gate had to follow.

⛔ **A gate can confirm itself.** Each rear hit pushes the cars apart by a fixed amount, so
the player was sent back to exactly the gate distance every time it got there — 780 frames
out of 1 500 between 128 and 160, almost none below. **A histogram of the gap shows it; a
hit counter does not.**

⛔ **Against the AI, side-rubbing may never happen**: measured 0 in 701 frames — the pursuit
keeps the rival ahead and the frames where both are level are exactly those where they are
apart laterally. A side-contact rule is only reachable with two humans (link). And a "glitch"
at contact was a rear hit where only the speed dropped and nothing moved on screen: a small
forward separation fixed it (contact box 146 → 25 frames, rival drawn inside the player 7 → 0).

**Impulse, not force.** Pushing the rival "4 units per frame while contact lasts" is a FORCE:
it never stops and turns the rival into a trailer — rightly rejected once. A reserve that is
spent over a few frames (an IMPULSE) is correct. Before reopening an idea closed by a
measurement, check whether it was the quantity or its SHAPE that was judged. Similarly a side
bump delivered in ONE frame (14 px at once) reads as a teleport; the steering itself moves
~7.5 px per game frame.

### 10.8 Scenery cost is FIXED per object

Two scenery objects are projected, placed and hidden every frame whatever their distance. So
spacing them out plateaus (lost frames 35 → 23 → 20) while removing one object does much
better (21), and removing all scenery leaves 4 — the rest is road and rival. **To go faster,
remove work per frame, not density.** In the same pass, free gains that changed no pixel:
cell column/row without `%` and `/` (two hardware divisions per cell), road top/bottom read
once per frame instead of per object, board sizes read once per object: −842 cycles/turn.

### 10.9 Everything that moves with the road shares ONE rate

Road/profile advance, the rival's own track loop, roadside markers and props each had their
own step derived from speed. Slowing the road by 10 % without the others made the scenery
**slide against the tarmac**. Put the same factor in every counter (and in the contact gate,
§10.7). Keep a list of them — there were four, not two.

---

## 11. The driving model

### 11.1 The camera IS the car — work in screen pixels

The player sprite does not move on screen; the player holds a **lateral position in pixels**
and the world slides under it (added to every entry of the scroll table). Work in **screen
pixels**: the usable half-width of the road is measurable on the art, and any other unit
forces a conversion that ends up wrong (symptom: the off-road warning fires while the car is
visibly on the road).

### 11.2 Steering vs drift — and the one line that kills difficulty

```c
car_lat += (STEER * (speed >> 4)) >> 4;       /* the wheel  */
car_lat += (bend  * (speed >> 4)) >> 4;       /* the corner */
```

**Both proportional to speed ⇒ their ratio does not depend on speed.** The wheel out-pulled
the hardest corner by 30 % at *every* speed: going faster did not make a corner harder, so
slowing down could never be rewarded. Measured: a trivial bang-bang controller with no
look-ahead and no braking kept the car within 18 % of the road width flat out. No tuning
fixes that; it is the shape of the model. Two ways out, both used:

- **Grade corners by the ratio drift/steering at full speed**: ≈0.5 (small taps suffice),
  ≈0.9 (holdable from the right line), ≈1.4 (steering *cannot* hold: lift or leave). Verify
  with a perfect servo pilot: easy and medium stay near the centre, hard hits the stop.
- **Give steering its own speed curve**: lateral authority rising from zero to a moderate
  speed, then decreasing gently at high speed — braking gives authority back (the playtest
  request "slowing down should let me cross the road faster"), and zero at a standstill.
  Share that curve with the corner-limit used by the AI and the grip indicators.

Both forces must run on **fractional accumulators**: in whole pixels per frame they can only
be 1 or 2 px, a gentle and a medium corner come out identical, and there is literally no
room for a difficulty curve.

### 11.3 The scroll rate must follow the speedometer without steps

`speed >> 5` on 0..255 gives 8 steps: two speeds 30 apart scrolled identically and
acceleration was seen as jumps. A step in 4096ths with a carried remainder follows the speed
continuously; the gain measured +12 % at full speed and **+45 % at mid-range** (truncation ate
most in the middle). And a constant base step must not move the world at a standstill: ramp
it in over the first ~16 speed units, or the road and the odometer creep while stopped.

### 11.4 Physics integrated per loop turn

If physics advances once per loop turn, **every performance gain makes the game faster in
real time**, not only smoother (going from 2.33 to 2.00 VBlanks per turn = +16 %). Decide it
knowingly; retune per-frame constants after any big speed-up (acceleration, steering,
centrifugal, scenery speed were all tuned against a loop 5× too slow in one prototype). The
race clock must count real VBlanks, never loop turns. A time is only a time for the track AND
the pace it was set at: bump the save's layout version when either changes.

### 11.5 Gearbox, slope and shop: a stat must reach a byte

- **Automatic shift points written as constants** (`{40, 77, 114, 142}`) for all cars: two
  cars whose 3rd gear topped out at 110 and 100 could never leave 3rd — stuck at 44 and
  40 MPH all race, invisible in manual (the lever has no speed condition). Derive them from
  each car's ratios (up at 230/256 of the current gear's ceiling, down at 166/256 of the
  previous).
- **The slope lowers a gear's ceiling**, so a threshold taken on the flat ceiling becomes
  unreachable on a climb (a gradient of 2 blocked 1st gear). Take the threshold on
  `min(nominal, current)`.
- **Three links, none deducible from the others:** does the shop write the save? does the
  save reach the copy the physics reads every frame? does the driving change? Checking only
  the first and the last leaves the hole where a stat reaches **no byte** (a tyre-noise
  threshold read the catalogue car instead of the tuned one; an upgrade overwrote another
  stat).
- **Top speed cannot be measured downhill** (the slope raises the ceiling to the same cap for
  every car) — but do not conclude "measure on the flat" and stop there: the player drives
  tracks that descend, and saw no difference from the upgrade. Give each car its own downhill
  ceiling. A measurement trap explains a number; it does not excuse ignoring where the
  player plays.
- **Measure drift at a track POSITION, not after N frames**: a car that accelerates better
  arrives earlier in the corner and seems to handle worse.

### 11.6 Off-road, verge, and comparing strategies with bots

- Off-road braking must stay **below** acceleration, or grass is a trap you never leave.
- "Bouncing off the verge costs less than braking" was settled with **scripted drivers**:
  clean, brakes early, brakes/lifts at three quarters, and one that only corrects at three
  quarters and lets the verge send it back. Braking *too early* was as slow as bouncing —
  which is what a hurried player feels. The fix was an **entry hit** paid at each exit
  (punishes repeated bouncing without making a single exit heavy); a stronger drag had
  already stopped the car dead.
- A car stopped far off the road cannot steer back (steering is zero at a standstill):
  after ~3 s nearly stopped and off-road, re-centre it laterally, no distance gained, speed
  zero, first gear.

### 11.7 Steering pose and steering force: ONE function

The car's steering pose rose notch by notch but snapped back to straight on release —
the return is half the motion a player actually watches (corner exit). Fix: step the pose
back down one notch at a time, **and** apply to the physics the fraction the displayed notch
is worth. **One function computes the notch**; the drawing and the physics read it. Two
formulas for the same thing drift at the first tuning, and the car turns one notch while
drawn in another. Tune the per-notch delay on the OAM: at 4 frames the return took 29 frames
(the car trailed the wheel); at 2, about 20.

---

## 12. Ground, verge and scenery art

- **The verge is the BACKDROP register**, when the road board uses colour index 0 for its
  grass: index 0 of a plane is transparent, so what you see beside the tarmac is the backdrop
  colour — one flat colour across the screen. Writing a sand colour into the road palette
  changes nothing; set the backdrop per track. Measure that colour on the **last visible row
  of the panorama**, or there is a horizontal seam at road level. A texture on the verge
  requires the board to stop using index 0 for grass.
- **At the very top of the band the road is ~9 px of half-width**; depending on rounding a
  line catches none of it and comes out as full-width verge (the backdrop). Give the top 3
  lines to the scenery.
- **Tile 0 of a plane in FRONT must be fully transparent**, or the "cleared" plane is an
  opaque wall in front of the road.
- **Ground plane:** the edge of a painted road is transparent. Either rewrite a palette entry
  per scanline, or put a plain ground on SCR1 with the road on SCR2 in front — SCR1 is never
  sheared, so the ground stays still while the road curves over it.
- **The scenery does not come from the road board.** The board is placed from its road row,
  so its top rows are never read; the scenery must be a separate panorama. A panorama is
  **256 px wide** (the plane width) because it wraps when the car turns: extend drawings to
  256 by measuring slopes and edges, do not centre a 160-px drawing.
- **A tunnel is the cheapest scenery there is**: no horizon, so none of the §9 frontier; flat
  areas and two straight edges, and identical cells cost once — 318 characters of 512 against
  508 for a ripped canyon. The vanishing line of the drawing is not the track's: move the
  opening up to where the road actually starts.
- **Start lights in 4 characters:** the five frames of the light had exactly the same shape
  (only the lamp colour changed), so one image is loaded and **a state is a palette write**.
  Let the generator find the changing pixels by comparing the frames.

---

## 13. What it costs on this CPU

| | CPU ISR on Timer 0 | **MicroDMA** |
|---|---|---|
| cost | one interrupt entry × 152 per frame | **0 CPU cycles** per line |
| measured on a prototype | **~10 fps** | **~55 fps** |

A commercial engine does it with a 3-instruction handler (bank switch, post-incremented
pointers kept in a register bank, `reti`); an ISR written in C costs 20–50× more. **In a C
project, use the MicroDMA.** ⚠️ It is one-shot: re-armed anywhere but at the very start of
VBlank, the top of the screen is drawn with the channel's leftovers (the fan started at
line 98, so a corner arrived at a quarter of its amplitude). Use **one parity source** for
all zones of a double-buffered table: one engine picked the band buffer from one counter and
the horizon buffer from another, and one frame in three wrote the horizon into the displayed
buffer.

The four fixes that took a prototype from ~10 to ~55 fps: MicroDMA instead of the C ISR;
a precomputed band-index table (a `mul` **and a division** per band per frame); precomputed
perspective tables (two more divisions per object per frame); fixed lines written once
(64 useless stores per frame). ⚠️ After such a gain, **re-tune every per-frame constant**.

**The 30 fps budget** is 2 VBlanks ≈ 204 800 cycles per loop turn. On a finished racer the
biggest items were the slice walk, the profile sampling and the rival; rewriting three road
loops in assembly (all in registers) and removing repeated calls took the loop from 3.23 to
2.00 VBlanks per turn on every track. Method, cost table and the equivalence gate that proves
an optimisation changed nothing: [Measuring Performance](../05_Systems/Measuring-Performance.md) §4.6–§5.3.
Stack depth in the heaviest race scene was ~300 bytes — measure it, every RAM byte you add
comes out of that margin.

---

## 14. Symptom → cause checklist

| Symptom | Real cause |
|---|---|
| everything slow, mushy controls, crawling scenery | the loop misses most VBlanks — measure the frame rate first |
| top of screen frozen, corner at a quarter of its value | MicroDMA re-armed too late in the frame |
| sprites invisible | flags = 0 = hidden priority |
| cars drive **under** the road | middle priority while the road plane is in front of the ground |
| a frozen copy of a sprite left on the road | metasprite draw writes only its `count` slots — hide the rest |
| scenery falls from the sky | linear placement instead of 1/d |
| scenery in orbit | sprites anchored by the top of their box |
| object pinned at the bottom of the screen | distance clamped at 0 instead of going negative |
| world freezes at low speed | quantised step `speed >> n` without accumulator |
| an object stays frozen on screen | decremented distance instead of positions on the track |
| off-road warning lies | lateral and half-width in different units |
| grass is a permanent trap | off-road braking ≥ acceleration |
| corner changes sign mid-bend | `u8` table read back as `s8` — keep a signed shadow |
| corners impossible to chain | profile and world advancing at different rates |
| gentle and medium corners identical | per-frame integer forces, no accumulator |
| the corner switches on instead of arriving | one curvature for the whole band — §7 |
| every corner drawn straight | gain constant overflowed a 16-bit `int` — §7.4 |
| the car slides sideways when it lifts | X read at the box bottom line, which shears — pass the wheel line separately (§8.6) |
| the car stays nailed while the road tilts | car placed on the bumper, not on a slice (§8.6) |
| the top of the road flickers | five causes — §9 |
| rival visibly behind but you lose | verdict taken before the rival's update, or projection origin not the player's wheel line (§10.5) |
| contact fires with tarmac between the cars | gate calibrated on sprite overlap, not world units (§10.7) |
| scenery slides against the tarmac | one of the rate counters not scaled with the others (§10.9) |
| two ROMs compared pixel by pixel diverge from the start | the VRAM queue runs at its budget and shifts by one frame — compare the **OAM**, not the screen |

## See Also

- [Effects and Raster](Effects-and-Raster.md) — the per-scanline mechanism, hills, and how to
  debug a raster effect
- [Tilemaps and Scrolling](Tilemaps-and-Scrolling.md) — the plane registers being written
- [DMA](DMA.md) — driving the per-line table at zero CPU cost per line
