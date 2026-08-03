# Debug tools v1

Purpose:
- give the nine modules behind the debug window's newer panels one place that
  states their **contracts** and, more importantly, the **claims they are not
  allowed to make**
- record why each one is shaped the way it is, so the next person does not
  "simplify" away a guard that exists because something went wrong once

Current source references:
- `../README.md` §Debugging (what each panel does, from the user's side)
- `../DEVLOG.md` entry 2026-07-31 (how they came about, and what they cost)
- `../cpp/src/render.cpp` (the renderer `core/tilemap_view.py` must agree with)
- `../cpp/src/z80.cpp` (the address map `core/z80_debug.py` mirrors)
- `MICRO_DMA.md`, `TIMERS.md`, `FRAME_TIMING.md` (registers `core/hwregs.py` decodes)

## 0. The rule they all follow

**Pure core, thin panel.** Every module here takes a `read(addr, n) -> bytes`
callable (or plain data) and returns plain values. No Qt, no `core.native`. That
is what lets a test drive them against a dict, a script drive them against a dump,
and the window drive them against the machine — with the same code.

**A tool may not invent a measurement it did not take.** Every "unknown" in these
modules is reported as unknown. The recurring failure this guards against is a
panel that reads zero and prints `0 %`, which is indistinguishable from a real
answer and much more convincing than one.

## 1. `core/hwregs.py` — the register dictionary

- One `Reg` per hardware register, each with `source`. Sources are `SPEC`
  (SDK `ngpcspec.txt`), `TIMERS_DOC` (`8Bit.txt`), `DATASHEET` (TMP95C061), or
  **`REVERSE`** — no manufacturer document defines it.
- ⚠️ **`REVERSE` is load-bearing.** `0x8000` and `0x8400` are inferred from
  behaviour and can be wrong. A table that showed them in the same typography as a
  spec-sourced register would launder a guess into a fact. A test asserts the tag.
- `checks(values)` returns only conditions the documents state outright, each
  carrying the sentence that makes it a defect. **It must not fire on a healthy
  console** — a checker that cries wolf is a checker nobody reads
  (`test_debug_tools_real_core.py` asserts exactly that against a real boot).
- **Single home for shared hardware facts.** `JOYPAD_BITS`, `JOYPAD_POWER_BIT`,
  and the sound-CPU addresses (`Z80_RESET`, `T6W28_RESET`, `Z80_NMI`, `Z80_COMM`,
  `SHARED_RAM`) live here and are imported elsewhere. The joypad table was first
  written from memory with A/B/Option one bit too high; nothing else in the
  emulator would ever have contradicted it. Two copies drift, and the drift is
  invisible until one is wrong.

## 2. `core/tilemap_view.py` — the scroll planes

- Renders a whole 32×32 plane to a 256×256 image. **Three colour paths**, chosen
  the same way the renderer chooses: K2GE (`base + code*8 + value*2`, CP.C four
  bits), K1GE compat (P.C **one** bit → LUT level → 12-bit palette), and a mono
  console (level → grey ramp, no colour RAM read at all).
- ⚠️ **The console flag decides compat, not `0x87E2`.** A real K1GE does not have
  that register, so anything clearing the video page leaves it zero.
- `camera_spans()` is **per scanline**, from `raster_log()`. The scroll registers
  are re-read every line, so the visible region is not a rectangle. Without a
  raster log the caller gets the single end-of-frame scroll **and must say so** —
  that fallback is the mistake this view exists to expose.
- `line_scroll_spread()` measures **around the circle** (the plane wraps). A
  scroll oscillating between 254 and 2 has moved 4 px, not 252; the naive range
  manufactures a defect out of arithmetic.
- If this module and `cpp/src/render.cpp` ever disagree, **this module is wrong**.

## 3. `core/z80dasm.py` + `core/z80_debug.py` — the sound CPU

- A complete Z80 disassembler by `xx yyy zzz` decomposition: unprefixed, CB, ED,
  DD/FD, and `DD CB d op` — where the **displacement sits between the prefix and
  the opcode**. Reading them the other way gets both the operation and the length
  wrong, and every following instruction with them.
- It describes the **architecture, not our core**. An opcode our Z80 traps still
  disassembles; otherwise the listing agrees with the bug.
- Undefined ED opcodes print as `db 0xED,nn` and still consume both bytes, so the
  listing resyncs. Inventing a mnemonic would put a claim in the listing that
  nothing supports.
- `make_reader()` mirrors `z80_read` exactly: `0x0000` is main-bus `SHARED_RAM`
  (mirrored ×4), `0x4000` is the **write-only** sound chip (reads give `0xFF`),
  everything from `0x8000` reads the comm latch. Reading Z80 addresses as main-bus
  addresses gives you the video registers — plausible, and wrong.
- ⚠️ **`halted` and `trapped` are never one message.** A halt is the driver
  sleeping between timer ticks — normal, and where it spends most of its life. A
  trap is our core refusing an opcode: a hole in the emulator with an address on
  it. Merging them turns a real defect into background noise.

## 4. `core/coverage_map.py` — what executed

- Unpacks the core's bitmap **LSB first** (`note_exec` uses `1 << (i & 7)`).
  Big-endian unpacking reverses every group of eight and still looks plausible.
- ⚠️ **A bit means "an instruction STARTED here", not "this byte ran".** Bytes
  inside a multi-byte instruction are cold. So `gaps()` reports only runs of at
  least `MIN_GAP` (64) bytes, and that number is a visible parameter: below it a
  gap is the encoding, not dead code.
- ⚠️ **The window covers ONE chip** (`0x200000..0x3FFFFF`). A 4 MiB cartridge has
  a second die at `0x800000` that is not recorded, so `stats()` takes the
  percentage against what is actually measured and returns a note. The other
  denominator shows a perfectly working game as 40 % dead.
- An empty bitmap means *nobody armed it* and must be reported that way — never
  as 0 % executed.

## 5. `core/profile.py` — where the frame goes

- Exact, not sampled: it consumes the per-instruction records the core already
  produces, including `cycles`.
- ⚠️ **Rank by cycles.** The cartridge bus is slow and instruction cost varies
  tenfold; ranking by instruction count puts a tight `djnz` loop above the routine
  actually eating the frame.
- Buckets are symbols when a `.map` is loaded, address blocks otherwise —
  refusing to answer without symbols would make the tool useless on every
  commercial cartridge.
- ⚠️ **A bucket's share of one frame is the same number as its share of the
  capture.** `per_frame()` therefore returns the **absolute** cycles per frame;
  printing both percentages would be printing one number twice.
- Capturing **advances the machine**. It is a deliberate action, never something a
  refresh does.

## 6. `core/movie.py` — input recording

- Format: `"NGPCMOV1"` · `u32` header length · JSON header · `u32` state length ·
  state · one input byte per frame (low 7 bits; `0x80` is POWER, taken from
  `hwregs.JOYPAD_BUTTON_MASK`).
- The whole feature hangs off **one call site**: the byte written to `0x00B0` once
  per emulated frame. There is no second clock for a replay to drift against.
- The starting snapshot is taken **when recording begins**, not at the first
  frame: a recording that starts one frame late replays one frame out of step
  forever, and that reads as an emulation bug rather than a broken tool.
- `check()` returns `Problem(fatal=…)`. **Fatal must stop a replay**: a movie of
  another cartridge would produce garbage that looks exactly like an emulation
  bug, and this feature exists to make bugs believable. A renamed file is not
  fatal — people rename files.
- ⚠️ **Not captured: the cartridge flash.** A savestate excludes it on purpose (it
  is a save, not a snapshot), so a game that reads its save mid-session can
  diverge. Say so; a replay that silently drifts is worse than none.

## 7. `core/console.py` — the scripting prompt

- `Console.run()` **never raises**. It runs inside a Qt slot, and an exception
  reaching PyQt calls `qFatal()`: the process dies with no message at all.
- The traceback is trimmed to start at the user's line — our own `exec` frame at
  the top of every mistake reads as "the console is broken".
- ⚠️ **Anything placed in the namespace before the first `set_namespace` counts as
  user-defined, and user definitions survive every rebuild.** Poking
  `namespace["m"] = None` to release a machine therefore wins forever; rebuild the
  namespace instead.
- `build_namespace()` tolerates `machine=None` and answers "no game running"
  rather than raising `AttributeError` on `None`.
- `HELP` is checked by a test: every name it mentions must exist.

## 8. `core/cheats.py` — held values

- **Not a second freezing mechanism.** Enabled cheats are written at the same
  point in the frame as the locked watches; two things that both hold a value
  would disagree about which won, and the answer would depend on ordering.
- `parse_text()` returns complaints **with line numbers** and never skips a bad
  line in silence: a pasted code that quietly loads three of four addresses is a
  cheat that half-works, which wastes the most time.
- `validate()` **warns, never refuses** — including for the case that matters on
  this machine, an address in the cartridge window: the cart is NOR flash, so the
  write goes to the chip's command latch rather than to memory. A debugger that
  rejected an address for looking wrong is useless the day it is right.

## 9. The window's own contract (`ngpc_debug.py`)

- Panels are registered as `(category, title, builder, refresher)` in **one
  tuple**. Title and refresher used to be two lists kept in order by hand;
  inserting a panel made every later one refresh a different panel.
- `_TwoRowTabs` keeps `QTabWidget`'s interface indexed by a **flat** panel number.
  Nothing outside it knows panels are grouped, so selecting by name still works
  from anywhere.
- ⚠️ **`refresh()` catches every panel exception**, names it in the status line and
  records it in `last_refresh_error`. Reported, not swallowed: a test asserting
  "every panel refreshed" would otherwise pass on a window full of broken ones.
- `attach()` re-arms every core-side probe the panels own (RAM-search tracking,
  access highlighting, the shadow stack, coverage) because **a new game is a new
  core**, and a tick box that survives the swap otherwise lies. It also releases
  the outgoing machine from the console namespace, so a panel nobody has looked at
  cannot keep a torn-down core — and the DLL behind it — alive.

## 10. Validation

- Per-module unit tests against fakes: `test_hwregs.py`, `test_tilemap_view.py`,
  `test_z80dasm.py`, `test_z80_debug.py`, `test_coverage_map.py`,
  `test_profile.py`, `test_movie.py`, `test_console.py`, `test_cheats.py`.
- **`test_debug_tools_real_core.py`** boots the native core with a real ROM and
  BIOS and checks the answers against facts the project already knows (`INTE45 ==
  0xDC` after hand-off, the unpacked coverage bitmap equalling the core's own
  count, the profiler putting a running game in the cartridge, the Z80 map
  reaching the shared RAM byte for byte), plus every panel refreshing twice with
  assertions on content. Fakes prove the arithmetic; only this proves the wiring.
