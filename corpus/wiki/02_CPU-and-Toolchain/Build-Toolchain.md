# Build Toolchain

Build commands, cc900 compiler rules (C89 compliance, NGP_FAR, volatile, ISR, inline ASM, confirmed ABI), the memory model, known compiler/linker pitfalls, the open-source NgpCraft toolchain, and a reference description of the official CC900 toolchain internals.

---


## 1. cc900 C89 Rules

cc900 is Toshiba's C compiler for the TLCS-900/H CPU. It is **strict C89** with
proprietary extensions. The following restrictions apply.

### 1.1 No `//` Comments

```c
/* CORRECT */
x = 1; /* initialize x */

// FORBIDDEN — cc900 rejects C++ comments
```

### 1.2 Declarations at Block Start Only

```c
/* CORRECT */
void foo(void) {
    u8 i;
    u8 j;
    i = 0;
    j = 1;
}

/* FORBIDDEN — declaration after a statement */
void foo(void) {
    i = 0;
    u8 j = 1;  /* compile error */
}
```

### 1.3 No Declaration in `for`

```c
/* CORRECT */
u8 i;
for (i = 0; i < 10; i++) { /* ... */ }

/* FORBIDDEN — C99 only */
for (u8 i = 0; i < 10; i++) { /* ... */ }
```

### 1.4 No 64-bit or Floating Point

```c
/* FORBIDDEN */
long long x;  /* no 64-bit integers */
double y;     /* no FPU */
float z;      /* no FPU */
```

### 1.5 Available Types

Defined in `ngpc_types.h`:

```c
u8   /* unsigned char  — 1 byte */
s8   /* signed char    — 1 byte */
u16  /* unsigned int   — 2 bytes */
s16  /* signed int     — 2 bytes */
u32  /* unsigned long  — 4 bytes */
s32  /* signed long    — 4 bytes */
```

> **Important:** on cc900, `unsigned long` = 32 bits and `unsigned int` = 16 bits.
> Do not rely on `int` being a specific size without typedef.

---

## 2. Far Pointers — NGP_FAR

### 2.1 Why Far Pointers Are Required

cc900 uses **16-bit (near) pointers by default**. Main RAM is at `0x004000-0x005FFF`
(within near range). Cartridge ROM starts at `0x200000` — completely out of near reach.

Without `NGP_FAR`, the compiler generates a truncated 16-bit address -> reads from the
wrong location -> corrupted data, wrong image, or crash.

### 2.2 The NGP_FAR Macro

Defined in `ngpc_types.h`:

```c
#define NGP_FAR  __far
```

### 2.3 When to Use NGP_FAR

**Always** for pointers to ROM data (tiles, palettes, tilemaps, audio, text strings):

```c
/* CORRECT — ROM tile data */
void ngpc_gfx_load_tiles_at(const u16 NGP_FAR *tiles, u16 count, u16 offset);

/* WRONG — compiles but reads from wrong address */
void ngpc_gfx_load_tiles_at(const u16 *tiles, u16 count, u16 offset);
```

Near pointers are fine for RAM data:

```c
static u8 s_buffer[256];      /* static RAM — near OK */
NgpcRect *rect;               /* struct in RAM — near OK */
const u16 NGP_FAR *tiles;     /* ROM data — NGP_FAR required */
```

### 2.4 Near Pointer Extension

When cc900 passes a near pointer to a function expecting `NGP_FAR`, it zero-extends
the 16-bit address to 24 bits. For RAM (base `0x004000`), this gives `0x004000` as
24-bit — correct. For ROM (`0x200000`), a near pointer cannot encode the address at all.

### 2.5 Fallback — Direct VRAM Macros

If display remains corrupted despite correct `NGP_FAR` usage (or for diagnostics),
use the direct tilemap-blit macros:

```c
/* Direct VRAM write without passing a pointer as parameter */
NGP_TILEMAP_BLIT_SCR1(prefix, tile_base)
NGP_TILEMAP_BLIT_SCR2(prefix, tile_base)
```

These macros index the generated symbol directly within the compilation unit —
no pointer parameter, so no near/far problem is possible.

Recommended decision tree:
1. Normal project -> use helpers (`ngpc_gfx_load_tiles_at()`, etc.)
2. Unexplained corrupted rendering -> switch to `NGP_TILEMAP_BLIT_*` to unblock
3. Any new helper reading `const` ROM data -> annotate parameters with `NGP_FAR`

---

## 3. volatile Keyword

Required for all hardware register accesses and all variables modified by ISRs:

```c
/* Hardware registers: always volatile (defined in ngpc_hw.h) */
#define HW_RAS_V  (*(volatile u8 *)0x8009)

/* Variables modified by ISR: volatile */
volatile u8 g_vb_counter;   /* incremented in isr_vblank */

/* DMA counter: volatile, modified by hardware */
static volatile u8 s_done_flag[4];
```

Without `volatile`, the compiler may cache the value in a CPU register and never re-read
memory -> a wait loop never terminates.

---

## 4. Interrupt Functions

Use the `__interrupt` keyword for interrupt handlers. cc900 automatically generates
the RETI prologue/epilogue:

```c
static void __interrupt isr_vblank(void)
{
    HW_WATCHDOG = WATCHDOG_CLEAR;
    g_vb_counter++;
}

/* Install the vector */
HW_INT_VBL = isr_vblank;
```

ISR rules:
- Keep ISRs as short as possible (budget is tight, especially HBlank ~ 30 cycles)
- Never call `ngpc_vsync()` from an ISR (deadlock)
- Shared variables must be declared `volatile`

---

## 5. Inline Assembly

### 5.1 Basic Syntax

One instruction per `__asm()` call:

```c
__asm("ei");       /* enable interrupts */
__asm("di");       /* disable interrupts */
__asm("halt");     /* wait for interrupt */
__asm("swi 1");    /* BIOS call */
```

### 5.2 Stringifying C Constants

C constants are not directly visible in inline ASM. Use `NGPC_STR(x)` (defined in `ngpc_hw.h`):

```c
#define BIOS_SHUTDOWN  0
#define NGPC_STR(x) #x

__asm("ldb rw3, " NGPC_STR(BIOS_SHUTDOWN));
/* generates: ldb rw3, 0 */
```

> `ld xhl, (symbol_c)` does NOT compile with asm900 (Error-221: Undefined symbol).
> C symbols are not visible from inline ASM. Access arguments via the stack instead.

### 5.3 Stack Convention for Arguments

In a function with `(void)arg` to suppress unused warnings, arguments are at predictable
stack offsets (far call convention):

```c
void my_func(const u8 NGP_FAR *ptr, u16 count)
{
    (void)ptr;    /* suppress "unused" warning */
    (void)count;
    __asm("ld xwa, (xsp+4)");  /* xwa = ptr (24-bit far)  */
    __asm("ld wa,  (xsp+8)");  /* wa  = count (16-bit)    */
    /* ... */
}
```

Keep such functions **minimal** — no local variables, no other C statements —
to ensure the stack prologue remains predictable for the `xsp+N` offset.

---

## 6. Memory Model and Sections

| Region | Address | Size | Usage |
|--------|---------|------|-------|
| Main RAM | `0x004000-0x005FFF` | 8 KB | C variables, stack, BSS |
| Battery RAM | `0x006000-0x006BFF` | 3 KB | Lightweight saves (volatile) |
| Z80 RAM | `0x007000-0x007FFF` | 4 KB | Audio driver (shared) |
| Cartridge ROM | `0x200000-0x3FFFFF` | 2 MB | Code + assets (near pointers forbidden) |

The linker script (`ngpc.lcf`) defines all sections. The runtime bootstrap (in `ngpc_sys.c`)
copies initialized data ROM -> RAM and zeroes BSS at startup.

Useful linker symbols:

```c
extern const u8 _DataROM_START;  /* start of initialized data in ROM */
extern const u8 _DataROM_END;
extern u8       _DataRAM_START;  /* destination in RAM */
extern u8       _Bss_START;      /* start of BSS */
extern u8       _Bss_END;
```

**RAM budget reality:**
- Main RAM: 8 KB total
- 2 tilemap stream buffers (SCR1 + SCR2, 32x32 x `u16`): 4096 bytes (50% of RAM, untouchable)
- Remaining for stack + entities + other buffers: ~4 KB

---

## 7. Confirmed ABI

ABI confirmed by disassembly analysis of cc900-compiled code.

### 7.1 Return Registers

| C type | Return register |
|--------|----------------|
| `u8` / `s8` | `L` (NOT `A`) |
| `u16` / `s16` | `HL` (NOT `WA`) |
| `u32` / `s32` | `XHL` (NOT `XWA`) |

### 7.2 Stack Layout (far call)

```
(XSP+0) = far return address (4 bytes)
(XSP+4) = arg0  (u8 uses 2 bytes on stack; u16=2B; far ptr=4B)
(XSP+6) = arg1  (if arg0 is u8 or u16)
```

### 7.3 Frame Allocation

cc900 uses `lda XSP, XSP-N` for stack frame allocation (NOT LINK/UNLK).

### 7.4 MUL/DIV — Hardware vs Library

| Operation | Codegen |
|-----------|---------|
| `u16 * u16` (same width) | HW opcode `MUL` (direct, fast) |
| `u16 * u8` (mixed width) | Software call `C9H_mullu` (shift-and-add) |
| `s16 * s16` | Software call `C9H_mulls` (via `C9H_mullu`) |
| `u16 / u16` | Software call `C9H_divlu` |

`C9H_*` routines are provided by `ngpc_runtime.c` — no `c900ml.lib` needed.

**To force HW MUL:** cast both operands to the same width before multiplying:
```c
u16 result = (u16)a * (u16)b;   /* same type = hardware MUL opcode */
```

### 7.5 Array Stride Codegen

| Array type | Stride instruction |
|------------|-------------------|
| `u8[]` | Direct index, no scaling |
| `u16[]` | `add XWA, XWA` (left shift x1) |
| `u32[]` | `sll 0x2, WA` (left shift x2) |

### 7.6 Miscellaneous Codegen Facts

- `switch` with >=8 dense cases -> cc900 generates a jump table automatically
- cc900 **never** emits `DJNZ` — uses `dec reg; jr NZ` instead
- **Callee-saved:** `XIY`, `XIX` (if used)
- **Caller-saved:** `XWA`, `XBC`, `XDE`, `XHL`

---

## 8. Known Bugs and Pitfalls

### 8.0 Missing prototypes — compile with `-w3`

C89 assumes `int` for a function with no visible prototype. cc900 then operates on the
full `HL` register where the function returns `u8` — the upper byte holds whatever the
previous call left there. This compiles without error and often works, which is what
makes it dangerous: it fails only when the upper byte happens to be non-zero.

    cc900 -c -w3 -O3 source.c

`THC1-Warning-521: Undefined return type` marks every affected call. Fix by declaring
the function before use.

> Measured on a ~15 000-line file: five sites, and fixing them cost **exactly zero**
> cycles (232 299 → 232 299 cycles/frame, 17 675 → 17 675 instructions). The generated
> code differed in exactly one place — `cp L,0xff` instead of `ld WA,HL` / `cp A,0xff` —
> same instruction count, same states. It went well because the definition appears later
> in the same translation unit and cc900 corrects the call. **Do not rely on that.**

*Contributed by [Napsterix](https://github.com/Napsterix); independently reproduced, and
also reported on the freeplaytech forum (Software Development, tid=5494).*

### 8.0b Reading generated assembly

    cc900 -S -O3 source.c        # writes source.asm

⚠️ Do **not** combine `-S` with `-o file.rel` — the driver reports
`cc900-Warning-520: The suffix not fit for output-file` and writes nothing.

*Contributed by [Napsterix](https://github.com/Napsterix).*

Counting `MUL` / `MULS` / `DIV` occurrences in the output is the fastest way to find
non-power-of-two struct indexing; see
[Measuring Performance §4.2](../05_Systems/Measuring-Performance.md) and
[TLCS-900/H Reference §37](TLCS900-Reference.md) for what those instructions cost.

### 8.1 u8 x Constant: Silent 8-bit Overflow

**Symptom:** entities appear at completely wrong positions; coordinate calculations are
corrupted without any compiler warning.

**Cause:** cc900 does not always perform standard C89 integer promotion for `u8 * literal`.
The expression `u8_var * 8` may stay in `u8` and silently overflow for values > 31
(e.g., `32 * 8 = 256` -> truncated to 0 in `u8`).

```c
/* WRONG — overflow: 74 * 8 = 592 -> u8 -> 80 (completely wrong position) */
world_x = (s16)((src->x * 8) - render_off_x);

/* CORRECT — explicit cast before multiplication */
world_x = (s16)(((s16)src->x * 8) - render_off_x);
```

**Rule:** always cast to `s16` or `u16` **before** multiplying a `u8` by a constant
that can produce a result > 255. Common cases: tile coordinates x 8, indices x stride.

### 8.2 Linker Order: Sprites After Maps -> Wrong Bank

**Symptom:** sprites are invisible (only 1 visible out of N), corrupted display for
specific entities only.

**Cause:** the linker places `f_const` section data in ROM in the order `.rel` files appear.
Alphabetical sorting typically puts tilemaps before sprites. If tilemaps are large, they fill
bank `0x20` and sprites end up in bank `0x21` or `0x22`. cc900's 16-bit near pointers can
only address bank `0x20` (`0x200000-0x20FFFF`). Result: a near `NgpcMetasprite *def` pointer
reads the wrong address -> invisible sprites.

**Fix:** in the Makefile (or generated asset Makefile), place `*_mspr.rel` files
**before** `*_map.rel` files. Sprites stay in bank `0x20`.

```makefile
# CORRECT order: sprites first, maps last
OBJS += $(OBJ_DIR)/player_mspr.rel
OBJS += $(OBJ_DIR)/enemy_mspr.rel
OBJS += $(OBJ_DIR)/level_map.rel       # maps come last
```

Alternatively, in the asset export script:

```python
def link_order_key(path):
    name = path.name.lower()
    if name.endswith("_mspr.c"):  return (0, str(path))  # sprites first
    if "_map.c" in name:          return (2, str(path))  # maps last
    return (1, str(path))
```

**General rule:** keep metasprites in bank `0x20`. If the project grows and sprites risk
overflowing, migrate to far pointers in `MsprAnimFrame.frame` and `ngpc_mspr_draw()`.

### 8.3 RAM: Oversized Entity Pools

**Context:** template runtimes allocate fixed-size arrays on `main()`'s stack for entities
(enemies, FX, props) and trigger regions. Generic defaults waste hundreds of bytes from
a total of only 8 KB of RAM.

Typical default values and RAM costs:

| Pool | Default | Struct size | RAM cost |
|------|---------|-------------|----------|
| Max enemies | 16 | ~44 bytes | 704 bytes |
| Max FX | 8 | ~22 bytes | 176 bytes |
| Max props | 16 | ~38 bytes | 608 bytes |
| Max regions | 64 | 1 byte | 64 bytes |
| **Total** | | | **~1552 bytes** |

A minimal scene with 3 enemies and 6 props needs only ~572 bytes for pools.
Savings: ~980 bytes = 12% of total RAM.

Define pool sizes via `CDEFS` in the Makefile:

```makefile
CDEFS += -DNGPNG_AUTORUN_MAX_ENEMIES=4
CDEFS += -DNGPNG_AUTORUN_MAX_FX=4
CDEFS += -DNGPNG_AUTORUN_MAX_PROPS=8
CDEFS += -DNGPNG_AUTORUN_MAX_REG=8
```

> Place pool overrides **after** any auto-generated CDEFS block to take precedence.

### 8.4 Windows Encoding

- Keep `.asm` source files in **CRLF** line endings, **ASCII/ANSI** encoding.
- asm900 rejects files with BOM or non-ASCII characters.
- Keep documentation files in ASCII to avoid "ghost character" issues in PowerShell.
- File paths with spaces require quotes in make/batch commands.

### 8.5 Nested Initialized Declarations

Declaring **and initializing** a local inside a nested block can produce wrong code on
both cc900 and the open-source `t900cc` compiler. The stack slot can alias another
local, so subsequent reads return stale or unrelated values. Observed symptoms include
corrupted camera coordinates, false SOLID tile results in dungeon collision, and
sporadic gameplay bugs that disappear when the declaration is moved.

```c
/* FORBIDDEN — nested initialized decl */
if (runtime_flag) {
    s16 _dgn_dzx = (s16)sc->cam_follow_deadzone_x;
    ...
}

/* CORRECT — hoist decl to enclosing block, assign in the nested block */
s16 _dgn_dzx;
...
if (runtime_flag) {
    _dgn_dzx = (s16)sc->cam_follow_deadzone_x;
    ...
}
```

Non-initialized nested decls are safer but not 100% confirmed. Prefer hoisting.

Same-family bug: `t900cc` also miscompiles `s8 != s8 || s8 != s8`. Store intermediate
comparison results in explicit `u8` variables if the expression mixes multiple `s8`
comparisons.

### 8.6 Full Pitfall Table

| Symptom | Probable cause | Fix |
|---------|---------------|-----|
| Corrupted / shifted image | ROM pointer without `NGP_FAR` | Add `NGP_FAR` to parameter |
| Infinite wait loop | ISR variable not `volatile` | Add `volatile` |
| Compile error | `//` comments or late declarations | Fix per section 1 |
| Crash or random behavior | Loop variable declared in `for` | Declare `u8 i;` before `for` |
| Wrong values from `unsigned long` | Assumed 16-bit, cc900 uses 32-bit | Verify: cc900 `u32 = unsigned long` |
| False struct size assertion | Unexpected padding | Use `u8 _pad[N]` for explicit alignment |
| Entities at completely wrong positions | `u8 * 8` silent overflow | Cast to `(s16)` before multiply: `(s16)src->x * 8` |
| Sprites invisible (1 of N visible) | Sprites in bank 0x21/0x22 (link order) | Put `*_mspr.rel` before `*_map.rel` |
| Low FPS with few entities | Full tilecol run for off-screen entities | Guard: skip physics if `screen_x < -96 || > 256` |
| Enemies vanish as camera advances | Kill condition using camera-relative bounds | Use map bounds: `world_x < -24 || world_x > map_w*8+24` |
| Out-of-RAM / random crash | Pool defaults 16/8/16/64 too large | Size pools to scene entity counts |
| Entity on path stuck and not moving | step_toward stops at `+/-step` without reaching exact target | Snap-to-target: return `diff` when `|diff| <= step` |
| Enemies at wrong position before camera reaches them | Off-screen guard applies velocity to all | Freeze if `screen_x > 256` (ahead), drift only if `screen_x < -96` (behind) |

---

## Quick Reference

| Item | Value / Rule |
|------|-------------|
| Comments | `/* */` only — no `//` |
| Declarations | Block start only — no declaration after a statement |
| Loop variable | Declare before `for` — no `for (u8 i = ...)` |
| No 64-bit | No `long long`, no `double`, no `float` |
| `u32` type | `unsigned long` (4 bytes on cc900) |
| NGP_FAR | `__far` — required for all ROM pointers |
| ROM base | `0x200000` — near pointer cannot reach it |
| volatile | Required for all HW regs + ISR-shared vars |
| `__interrupt` | Generates RETI prologue/epilogue automatically |
| Return u8 | Register `L` |
| Return u16 | Register `HL` |
| Return u32 | Register `XHL` |
| arg0 far call | `(XSP+4)` |
| arg1 far call | `(XSP+6)` |
| Same-width MUL | `(u16)a * (u16)b` -> HW opcode (fast) |
| Mixed-width MUL | `u16 * u8` -> software `C9H_mullu` |
| DIV | Always software `C9H_divlu` |
| `u8 * const` bug | Silent 8-bit overflow — cast to `s16` first |
| Link order | `*_mspr.rel` before `*_map.rel` — sprites in bank 0x20 |
| Main RAM | 8 KB — 4 KB consumed by SCR1+SCR2 stream buffers |
| ASM encoding | CRLF + ASCII/ANSI — no UTF-8 BOM |
| Nested init decls | Hoist to block start — cc900/NgpCraft codegen bug |

---

## 9. NgpCraft Open-Source Toolchain

The **NgpCraft toolchain** is a clean-room, Python-only replacement for Toshiba's
proprietary tools (`cc900`, `asm900`, `tulink`, `tuconv`, `s242ngp`). Its components are
`t900cc` (C compiler), `t900as` (assembler), `t900ld` (linker), and `ngpc_romtool` (ROM
packager). It can compile a full non-trivial game.

### 9.1 Pipeline

```
source.c
  -> cpp -E                               (external preprocessor)
  -> t900cc   source.c   -> source.asm
  -> t900as   source.asm -> source.t9obj
  -> t900as   crt0.asm   -> crt0.t9obj
  -> t900ld  -m ngpc.lcf crt0.t9obj source.t9obj -> program.bin
  -> ngpc_romtool program.bin --title "MYGAME" -> program.ngc
```

### 9.2 ABI `__cdecl` (args on stack)

The NgpCraft `__cdecl` ABI passes all arguments on the stack. It is **not** the same as
CC900's ABI:

| Aspect | NgpCraft ABI (`__cdecl`) | CC900 (reference) |
|--------|--------------------------|-------------------|
| Arg passing | All args on stack, right-to-left | arg1=XWA, arg2=XBC, arg3=XDE, rest stack (`__adecl`) |
| 1-byte args | Extended to 2 bytes on stack | Same |
| Return <= 4 B | `XHL` | `XHL` |
| Return > 4 B | Hidden pointer in XWA | Hidden pointer in XWA |
| Frame | `link XIY, 0` / `unlk XIY` | `lda XSP, XSP-N` (no link on leaf) |
| Locals access | `XIY + disp` | `XSP + disp` or `XIZ` subregs |
| Caller-saved | XWA, XBC, XDE, XIX, XIY, XIZ | XWA, XBC, XDE, XHL |
| Callee-saved | XHL | XIY, XIX |

Consequence: mixing cc900-compiled object files with NgpCraft-compiled objects in the
same ROM **is not supported**. Pick one toolchain per ROM.

### 9.3 Type Sizes and Pointer Model

```c
char            1 B   (signed default)
short / int     2 B
long            4 B
float / double  -- excluded (no FPU, no soft-float)
pointers        4 B  (all pointers are far — `large` addressing mode)
```

Near pointers and the `tiny` / `small` / `medium` addressing qualifiers are **excluded**.
`NGP_FAR` in project source is redundant on NgpCraft (every pointer is far) but
still required when the same code must build with both cc900 and NgpCraft.

### 9.4 Hardware-Validated Codegen — Silicon Quirks

The NgpCraft `t900cc` codegen emits only instructions confirmed safe by hardware bisect on
real NGPC silicon. Known broken opcodes explicitly avoided by codegen:

| Opcode pattern | Status | Workaround |
|----------------|--------|------------|
| `D0` prefix emitted for word register ops (cpl WA, neg WA, sll N WA, sra ...) | **Mis-decode, not silicon** — `D0..D7` is the WORD MEMORY family; word reg-direct is `D8..DF`. Emitting D0 for a word-register op makes the CPU decode it as a memory access (historic "crash"). Codegen must not emit D0 for register ops. | Use D8 prefix (word reg-direct) or `extz XWA` + E8 (32-bit) or HL-based alternative |
| `adc W, B` with W > 0 (`CA 90`) | **Broken** | Loop count capped at 255 (W stays 0) |
| `add A, C` (`CB 81`) — byte ALU add/adc/sub/sbc with C-register source | **Broken** (sub-op-specific — NOT the whole CB family) | Use `add A, L` (`CF 81`) via HL |
| byte reg-reg MUL/DIV (`CB` prefix, sub-op `0x40..0x5F`; e.g. `CB 51` = `div A, C`) | **Safe** — HW-cleared on real NGPC 2026-07-08 (`hw_test_bytediv`: `div A,C` executes correctly, quotient/remainder OK). Parallels the word mul/div clearing (D8..DF, 2026-07-06). | Emit directly |
| `link XIY, N` with N >= 5 | **Broken** | Frame capped at N <= 4, extra locals accessed via separate stack arithmetic |
| `inc WA` (`D0 61`) | **Mis-encode, not silicon** — `D0` is a memory-addressing byte; `inc WA` reg-direct is `D8 61`. Codegen must not emit the `D0` form. | `ld BC, 1; add A, C; adc W, B`, or emit the reg-direct `D8 61` |
| `srl/sll A, XDE` with A=0 | **Zeroes XDE** (not no-op) | `or A, L` guard (`CF E1`) before shift |
| `D1..D7` standalone | **Safe** | Confirmed in CC900 production code |
| `LD R32, imm32` (`0x40+R`) | **Safe** | Used for all 32-bit literal loads (function pointers etc.) |
| `ldirw` / `ldiw` on NGPC | Source = **XIY**, dest = **XIX** (not XDE/XHL) | Required save/restore of XIY through XHL |

All of this is enforced inside the codegen — project C source does not need to know about
individual opcode bans.

### 9.5 Runtime Environment

- **`crt0.asm`** copies initialized data ROM -> RAM and zeroes BSS before `main()`.
- **`ngpc_sys.c`** and **`ngpc_vramq.c`** are both compiled C modules (no hand ASM).
- **ISR install pattern** (mandatory for input to work): install `VBL_ISR` at `0x6FCC`
  and enable with `ei 0`. Without this, the BIOS never updates the joypad byte at
  `0x6F82` and buttons stay silent (see section 4, Interrupt Functions).
- **Power button is BIOS-trapped** — a `ngpc_shutdown = ret` stub does NOT disable the
  hardware power button; it only suppresses `TIME_SHUTDOWN_REQ` (10-min idle) and
  `BAT_SHUTDOWN_REQ` (low battery). The power button is honored regardless.

### 9.6 ABI Tradeoffs vs CC900

The stack-based `__cdecl` ABI is simpler than CC900's register-based `__adecl`, but it
produces larger code and more frequent stack traffic. The structural differences that
account for most of the size delta are the per-call frame setup (`link XIY` / `unlk XIY`),
`XIY + disp` local access instead of `XSP + disp`, and fewer short relative calls (`calr`).
A register-passing `__adecl` ABI, no-frame leaf functions, relaxed `jp`/`calr` selection,
a `u8` zero-extend peephole, and register auto-promotion are the main avenues for closing
the gap.

---

## 10. CC900 Toolchain Internals (Reference)

A full disassembly and dry-run analysis of the six Toshiba toolchain binaries decoded how
the official `cc900` toolchain works. This is reference-grade information for anyone
reverse-engineering CC900 output, matching its codegen, or driving the proprietary tools
directly. It is **observed behaviour and hardware facts**, not Toshiba source.

### 10.1 Official Pipeline and Tool Roles

`cc900.exe` is just a driver. Its `-#` dry-run reveals it spawns two non-trivial stages,
then standard back-end tools:

```
source.c
  +-[thc1.exe]-> TAC      (frontend: lex/parse/typecheck/lower -> three-address-code text)
       +-[thc2.exe]-> .asm (code generator: register allocation + all optimisation)
            +-[asm900.exe]-> .rel   (relocatable object)
                 +-[tulink.exe]-> .abs (absolute object; GNU-ld-style script)
                      +-[tuconv.exe -Fs24]-> .s24 (Motorola S2 record, 24-bit address)
                           +-[s242ngp]-> .ngp/.ngc (raw binary, BASE 0x200000, 0xFF fill)
```

| Tool | Role | Replaced by (NgpCraft) |
|------|------|------------------------|
| `thc1.exe` | C -> TAC (parser/frontend) | NgpCraft frontend |
| `thc2.exe` | TAC -> asm (code generator — where optimisation lives) | NgpCraft backend |
| `asm900.exe` | asm -> `.rel` (assembler / ISA encoder) | `t900as` |
| `tulink.exe` | `.rel` -> `.abs` (linker) | `t900ld` |
| `tuconv.exe` | `.abs` -> Intel HEX / Motorola SREC | `ngpc_romtool` |
| `s242ngp` | S24 SREC -> `.ngp` binary (small open homebrew utility, **not** Toshiba) | `ngpc_romtool` |

Exact stage invocations (the driver passes `-XO -XM` defaults to both stages; `-XM` ~
maximum addressing = `$MAXIMUM`):

```
thc1 -XO -XM source.c <tac>      # frontend -> TAC
thc2 -XO -XM <tac> out.asm       # codegen -> asm
```

- **`-O<n>` (optimisation) is passed to `thc2` only.** The code generator owns all
  optimisation.
- **The TAC is optimisation-independent**: compiling with or without `-O` produces the
  **same** TAC. Useful corollary for reproduction work — a stable TAC can be captured
  once and a code generator developed against it.
- `-g` adds debug info to both stages.

### 10.2 TAC — the thc1->thc2 Intermediate Representation

`thc1.exe` emits a readable three-address-code text on stdout. Each line is a quad
`(OP, A1, A2, Re)` where `Re` is the result/destination and `A1`/`A2` are operands
(the operand order matches thc2's internal `A1`/`A2`/`Re` naming).

Operand forms:

| Form | Meaning |
|------|---------|
| `Tn` | virtual temp n (typed) |
| `In` | named local n (frame slot) |
| `Pn` | parameter n |
| `A` | return register (WA/HL convention) |
| `#U<n>` / `#LU<n>` / `#<n>` | immediate: unsigned 16-bit / unsigned long 32-bit / raw (e.g. byte count) |
| `*Tn` | dereference (load/store via pointer) |
| `@_name` | global / function label |
| `Ln` | branch label |

Opcodes:

| OP | Semantics |
|----|-----------|
| `=` | assign / copy (implicit type conversion via the Re-temp) |
| `+ - * / %` | binary arithmetic |
| `& \| ^` | bitwise |
| `+= -= ...` | compound assign (kept as a single op -> codegen may emit INC/RMW) |
| `== != < <= > >=` | comparisons -> bool temp (type 16) |
| `FALSE,cond,,L` / `TRUE,cond,,L` | conditional branch (if cond == 0 / != 0) |
| `JMP,L` / `LAB,L` | unconditional branch / label definition |
| `CARG,t` | push call argument |
| `CCALL,@_fn,#nbytes,Re` | call (nbytes = arg size, Re = result) |
| `BEGIN,fn` / `END,fn` | function begin / end (return) |
| `{,n` / `},n` | scope open / close |
| `LINE,n[,col]` / `FILE,path` | source markers (debug) |

Type table (numeric IDs used throughout the TAC):

| # | C type | | # | C type |
|---|--------|---|---|--------|
| 1 | void | | 8 | signed long (s32) |
| 2 | signed char (s8) | | 9 | unsigned long (u32) |
| 3 | unsigned char (u8) | | 16 | bool (comparison result) |
| 4 | signed short (s16) | | 19 | pointer to type 3 (`u8*`) |
| 5 | unsigned short (u16) | | 21 | pointer to type 5 (`u16*`) |
| 6 | int (signed 16-bit on NGPC) | | 64+ | user-defined struct types |
| 7 | unsigned int (u16) | | | (pointer types are derived via a symbol-table entry) |

The frontend does the heavy semantic work — `&&`/`||` and `switch` are lowered to
branch chains, `for` to `init; FALSE cond exit; body; step; loop`, `a->x` and `arr[i]`
to address-compute + deref. The code generator receives an already-lowered IR.

### 10.3 Runtime Helpers (`C9H_*`)

The code generator emits library calls for operations the CPU cannot do in hardware.
Naming convention `C9H_<op>l<u|s>` (the `l` = long / 32-bit):

| Helper | Operation |
|--------|-----------|
| `C9H_mullu` / `C9H_mulls` | u32 x u32 / s32 x s32 |
| `C9H_divlu` / `C9H_divls` | u32 / u32 / s32 / s32 |
| `C9H_remlu` / `C9H_remls` | u32 % u32 / s32 % s32 (remainder) |
| `_fadd_`, `_fcmp_`, `_fdiv_`, `_fld_`, ... | float (no FPU on TLCS-900) |

**16-bit `mul`/`div` use native instructions**; only 32-bit and float route through the
runtime. (Compare with section 7.4, which describes the same split from the project side.)
The NgpCraft runtime provides its own `C9H_*` implementations in `ngpc_runtime.c` — no
`c900ml.lib` needed.

### 10.4 asm900 Syntax and Directives

The asm emitted by thc2 (and accepted by `asm900` / `t900as`) is tab-indented,
lowercase-mnemonic, with hex as `0x...` and memory as `(...)`:

```
        ld   WA,(XSP+0x6)
        add  HL,WA
        inc  0x1,HL
        pushw (XSP+0x4)
        cpw  (XSP+0x4),0x0
        j    ne,L1
        cal  _fn
```

Directives:

- `module <name>` — module name; `end` — end of module.
- `<name> section <type> <attrs>` — e.g. `f_code section code large align=1,1`.
  Section types: `code` / `data` / `const` / `area`. Addressing class: `large` /
  `medium` / `small`. Attributes: `align=N,M`, `abs=0xADDR`, `NOLOAD`, `ROMDATA`.
- `public _sym` / `extern large _sym` — symbol visibility.
- `$MAXIMUM` — CPU addressing mode (set by the `-XM` default).
- Data: `db` / `dw` / `dsw` / `ds`.

The mnemonic->byte encoding is a hardware fact (the `ngdis` disassembler is a faithful
port of the Toshiba ISA spec; see [TLCS-900/H Reference](TLCS900-Reference.md)), so the
assembler does not need to be reverse-engineered from the binary.

### 10.5 Linker Script and ROM Packaging

`tulink` uses a GNU-ld-style script. Directives relevant to NGPC:

- `MEMORY { name : ORIGIN=..., LENGTH=... }` — memory regions.
- `SECTIONS { ... }` — section placement; `OVERLAY` for ROM banking; `ALIGN` for
  alignment.
- `NOLOAD` — sections not loaded (BSS).
- **`ROMDATA`** — initialised data resident in ROM and **copied to RAM at boot**. This
  is the near/far data model: the crt0/startup performs the ROM->RAM copy (the same job
  the runtime bootstrap does in the NgpCraft runtime, section 6).
- Targets: `-T900` / `-T9K16` / `-T9K32` (TLCS-900 variants), `-pic900` (PIC).

**ROM packaging (`s242ngp`)**: maps the ROM at `BASE = 0x200000`, fills unused space
with `0xFF`, writes each S2 record's bytes at `addr - BASE`, and dumps the binary up to
the last used address. **No cartridge header is added by the packager** — the 64-byte
NGPC header (`LICENSED BY SNK...` at `0x200000`) lives in the compiled startup code
itself. NgpCraft's `ngpc_romtool` follows the same convention.

---

## See Also

- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — full memory map, hardware register addresses
- [TLCS-900/H Reference](TLCS900-Reference.md) — instruction set, opcode encodings, ABI details
- [Assembly](Assembly.md) — asm900 syntax, gotchas, inline ASM, CRLF requirement
- [Game Loop](../05_Systems/Game-Loop.md) — VBlank ISR structure, watchdog, main loop skeleton
- [Sprites and OAM](../03_Graphics/Sprites-and-OAM.md) — metasprite bank limit, NGP_FAR in sprite API
- [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) — NGP_FAR in tilemap load functions
- [BIOS](../01_Hardware/BIOS.md) — BIOS SWI calling convention, `swi 1` usage
