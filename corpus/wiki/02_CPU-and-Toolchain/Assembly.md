# TLCS-900/H Assembly

Practical notes for writing TLCS-900/H assembly with the Toshiba asm900 assembler: module structure, register set, the confirmed ABI, known assembler gotchas, the LDIRW block-copy pattern, and decision criteria for porting C to assembly.

---


## 1. ASM Module Structure

### 1.1 Template

```asm
        module  ngpc_my_module              ; module name (one per file)

        public  _my_function                ; export to C (underscore prefix required)
        extern  _c_variable                 ; import from .c (must be non-static)

MY_CODE         section code large          ; far code section (cartridge ROM)

_my_function:
        ; ... code ...
        ret

        end                                 ; mandatory at end of file
```

### 1.2 Sections

| asm900 section | LCF type | Destination |
|----------------|----------|-------------|
| `section code large` | `f_code` | Cartridge ROM (`0x200000+`) |
| `section code near` | `f_code near` | RAM (rarely used) |
| `section data near` | `f_data` | Initialized RAM (ROM -> RAM copy at boot) |
| `section bss near` | `f_area` | Zero-initialized RAM (zeroed by bootstrap) |

C variables compiled with cc900 -> `f_area` section.
For variables shared between C and ASM: declare them in the `.c` file as non-static
globals, then reference them via `extern` in the `.asm` file.

### 1.3 The `_` Prefix for C Symbols

All C functions and variables have an underscore prefix in the linker:

- C: `void ngpc_soam_flush(void)` -> ASM: `_ngpc_soam_flush`
- C: `u8 s_oam[]` (non-static global) -> ASM: `extern _s_oam`

`static` C symbols are **not** exported and are inaccessible from ASM.

---

## 2. TLCS-900H Registers

```
XWA (32-bit): W (high 8-bit), A (low 8-bit, accumulator)
XBC (32-bit): B (high 8-bit), C (low 8-bit)
XDE (32-bit): D (high 8-bit), E (low 8-bit)
XHL (32-bit): H (high 8-bit), L (low 8-bit)
XIX, XIY    : index registers
XSP         : stack pointer
```

**ABI-confirmed register usage:**

| Purpose | Register |
|---------|----------|
| Return `u8` | `L` (not `A`) |
| Return `u16` | `HL` (not `WA`) |
| Return `u32` | `XHL` (not `XWA`) |
| General accumulator | `A` |
| LDIRW/LDIR counter | `BC` |
| Caller-saved | `XWA`, `XBC`, `XDE`, `XHL` |
| Callee-saved | `XIX`, `XIY` |

> **Important:** do not use `A` or `WA` to return values to C code — cc900 reads
> the return value from `L` / `HL` / `XHL` depending on type. Any code using `WA`
> for a return value is incorrect.

---

## 3. asm900 Gotchas

### 3.1 INC/DEC Require Explicit Count

```asm
; WRONG (Error 231 "Too few or many operands" in MAXIMUM mode):
inc     a

; CORRECT:
inc     1, a            ; increment A by 1
dec     1, b            ; decrement B by 1
```

On TLCS-900H, INC/DEC are encoded `INC n, r` with n = 1..8.
asm900 MAXIMUM mode rejects the short form without the count.

### 3.2 No LD (HL), Immediate

```asm
; WRONG (Error 230 "Operand type mismatch"):
ld      (hl), 0

; CORRECT: load into a register, then store:
ld      e, 0
ld      (xhl+0), e
```

There is no "store-immediate-to-indirect-register" instruction on TLCS-900H.
Always route through an intermediate register.

### 3.3 Register C vs Carry Condition Code Ambiguity

```asm
; PROBLEMATIC: 'c' = Carry condition code OR register C?
; asm900 reads 'c' as the Carry condition code -> wrong instruction generated,
; the next instruction is consumed in cascade -> Error 231 on the following line.
ld      (xhl), c        ; AVOID

; CORRECT: use E (never a condition code):
ld      e, 0
ld      (xhl+0), e
```

TLCS-900H condition codes: `T F LT LE ULE OV MI Z C GE GT UGT NOV PL NZ NC`

Registers to avoid as the source in `(dest), src` patterns: `C` (= Carry), `Z` (= Zero).
Safe alternatives: `E`, `D`, `A`, `B`, `H`, `L`, `W`.

### 3.4 Indirect Addressing Uses Extended Registers

```asm
; WRONG (Error 230 in MAXIMUM mode):
ld      (hl), a         ; HL = 16-bit register, not accepted for indirect addressing

; CORRECT: use the extended register with displacement:
ld      (xhl+0), a      ; XHL = 32-bit address, d=0 = no displacement
ld      (xhl+1), a      ; XHL+1 (useful for byte[1] of a struct)
ld      a, (xhl+0)      ; read from (XHL+0)
ld      a, (xsp+4)      ; read from stack (function parameter)
```

asm900 always uses extended registers (XHL, XDE, XSP...) for memory addressing.
16-bit registers (HL, DE...) cannot be used directly as pointers.

### 3.5 Upper Bits of XHL/XDE Must Be Zero for Near Addressing

```asm
; XHL is 32-bit. If bits [31:16] are non-zero and (XHL+d) is used,
; the effective address can be completely wrong (outside near space).

; Zero upper bits BEFORE using (XHL+d) for near addresses (0x0000-0xFFFF):
ld      xhl, 0          ; clear all 32 bits of XHL
ld      hl, _s_oam      ; load 16-bit address into HL (bits [31:16] stay 0)

; NOTE: 16-bit ops (ADD HL,HL / ADD HL,DE / LD H,n / LD L,n) do NOT modify
; bits [31:16] of XHL. One LD XHL,0 before the loop is sufficient.
```

### 3.6 LDIRW/LDIR: BC=0 Means 65536 Iterations

```asm
; WARNING: if BC=0 at entry, LDIRW copies 65536 WORDS = disaster.
; The exit condition is "BC == 0 after decrement" (do-while semantics).

; Always guard against BC=0 when BC is a runtime value:
ld      a, (_s_used)
or      a, a
jr      z, skip_copy    ; if s_used == 0, skip
; ... load BC = s_used * 2 ...
ldirw
skip_copy:

; Constant BC values (0x80, 0x20, etc.) are never 0 -> always safe, no guard needed.
```

### 3.7 Warning 501 on extern Symbols Is Normal

```asm
ld      hl, _s_oam      ; Warning 501 "Operand value is out of range"
```

asm900 does not know the value of `_s_oam` at assembly time (it is extern, unknown).
It generates the instruction with a placeholder and marks a relocation entry.
The linker fills in the correct address. Warning 501 on extern symbols is **normal and harmless**.

### 3.8 Kick the Watchdog in Long Loops

```asm
; Inside any loop running more than ~8000 iterations (long clears/copies),
; kick the watchdog mid-loop or the hardware resets the console:
ldb     (0x6f), 0x4e    ; write 0x4E (the NOP opcode) to SYSCR1 = watchdog clear
```

A tight clear/copy loop that exceeds the ~100 ms watchdog timeout will trigger a reset
unless the watchdog is cleared periodically from inside the loop body.

---

## 4. LDIRW Block Copy Pattern

`LDIRW`: copies `BC` words (2 bytes each) from `XHL` to `XDE`.
Increments `XHL` and `XDE` by 2 after each word, decrements `BC`.
Significantly faster than a C byte-by-byte loop.

```asm
; Load near addresses into XHL/XDE (upper bits must be 0):
ld      xhl, 0
ld      hl, _s_oam          ; source: shadow OAM
ld      xde, 0
ld      de, 0x8800           ; destination: HW_SPR_DATA
ld      bc, 0x0080           ; 128 words = 256 bytes
ldirw                        ; execute block copy

; For LDIR (byte-by-byte):
ld      bc, 0x0040           ; 64 bytes
ldir
```

### Constant-fill variant (no value register)

LDIRW can fill a large buffer with a constant by letting the copy lag itself by one word.
Write the first word, then set the source to `dest - 2` (one word behind the destination)
and LDIRW for `N-1` words: each iteration reads the word just written and propagates it
forward. The source must lag the destination by **exactly 2 bytes**.

```asm
ld      xde, 0
ld      de, _buffer          ; destination = start of buffer
ld      (xde), wa            ; seed the first word with the fill value (WA)
ld      xhl, 0
ld      hl, _buffer          ; source = dest - 2 (one word lag)
sub     hl, 2
ld      bc, N-1              ; remaining words
ldirw                        ; each word copies the previous -> whole buffer = fill value
```

**Measured performance (256-byte OAM copy):**

| Method | Cycles |
|--------|--------|
| C loop (byte-by-byte, cc900) | ~2048 cycles |
| LDIRW x 128 words | ~512 cycles |
| Gain | ~4x (plus C loop overhead) |

Practical impact: ~300 cycles freed on the critical VBlank path for a 256-byte OAM flush.

---

## 5. Calling Convention (cc900)

Confirmed ABI — far call stack layout at function entry:

```
(XSP+0) = far return address (4 bytes)
(XSP+4) = arg0 (u8 occupies 2 bytes on stack; u16=2B; far ptr=4B)
(XSP+6) = arg1 (if arg0 is u8 or u16)
```

Parameters are pushed right-to-left (standard C convention).

Return values:
- `u8` -> `L`
- `u16` -> `HL`
- `u32` -> `XHL`

Example — reading the first parameter:

```c
/* C declaration */
void my_func(u8 divider);
```

```asm
_my_func:
        ld      a, (xsp+4)      ; read u8 arg0 from stack
        ; ... use A ...
        ret
```

**Recommendation:** only port functions with **no parameters or simple parameters**
(0 params = ideal, 1-2 `u8`/`u16` OK) to ASM. For complex multi-parameter functions,
keep the C implementation and call it from ASM if needed.

---

## 6. C/ASM Split Architecture — Shadow OAM Example

A validated architecture for a shadow-OAM module:

```
ngpc_soam/
    ngpc_soam.h          -- public API (unchanged)
    ngpc_soam.c          -- reference implementation (not compiled into final build)
    ngpc_soam_c.c        -- compiled: begin / put / hide / hide_all / used + variables
    ngpc_soam_flush.asm  -- compiled: flush / flush_partial via LDIRW
```

**Why the split:** `flush()` and `flush_partial()` are on the hot path — called every VBlank
from the ISR. They perform two block copies (256 + 64 bytes) that LDIRW accelerates
significantly. The other functions (`put`, `hide`, `begin`, `used`) contain no long loops
and are efficient enough in C.

**Sharing variables:** variables `s_oam[]`, `s_col[]`, `s_used`, `s_used_prev` are
declared non-static (exported) in the `.c` file and referenced via `extern` in the
`.asm` file.

Makefile:

```makefile
OBJS += $(OBJ_DIR)/ngpc_soam/ngpc_soam_c.rel
OBJS += $(OBJ_DIR)/ngpc_soam/ngpc_soam_flush.rel
```

Do **not** add both the reference `.rel` and the compiled `_c.rel` — they define
the same symbols and will cause linker conflicts.

---

## 7. Checklist Before Porting a Function to ASM

- [ ] Is the function on a critical path (ISR, tight loop, VBlank)?
- [ ] Does it contain a long loop that LDIRW/LDIR can replace?
- [ ] Are its parameters simple (0 params = ideal, 1-2 `u8`/`u16` OK)?
- [ ] Are all shared variables declared non-static in the `.c` file?
- [ ] Is the C reference implementation kept in the same folder?
- [ ] Does the Makefile replace the C `.rel` with the ASM `.rel` (not both)?
- [ ] Verified: identical behavior to the validated C version?

---

## Quick Reference

| Item | Rule |
|------|------|
| INC/DEC syntax | `inc 1, a` — explicit count required |
| Store to indirect | `ld e, 0` then `ld (xhl+0), e` — no immediate-to-indirect |
| Register C in source | Use `E` instead — `c` = Carry condition code |
| Indirect addressing | `(xhl+d)` — extended registers only, not 16-bit HL |
| Near address setup | `ld xhl, 0` then `ld hl, _sym` — zero upper bits first |
| LDIRW guard | Check `BC != 0` before LDIRW if BC is a runtime value |
| Warning 501 | Normal on extern symbols — linker resolves at link time |
| Return u8 | `L` |
| Return u16 | `HL` |
| Return u32 | `XHL` |
| arg0 at stack | `(xsp+4)` — after 4-byte far return address |
| `_` prefix | All C symbols have underscore prefix in ASM |
| static C var | Not exported — declare non-static to share with ASM |
| LDIRW speed | ~4x faster than C byte loop for block copies |
| LDIRW BC=0 | = 65536 iterations — never pass 0 to LDIRW |
| Callee-saved | `XIX`, `XIY` |
| Caller-saved | `XWA`, `XBC`, `XDE`, `XHL` |

---

## See Also

- [Build Toolchain](Build-Toolchain.md) — cc900 ABI, inline ASM from C, NGPC_STR macro
- [TLCS-900/H Reference](TLCS900-Reference.md) — full instruction set, opcode encodings, addressing modes
- [Game Loop](../05_Systems/Game-Loop.md) — VBlank ISR structure, hot path budget
- [VRAM Queue](../03_Graphics/VRAM-Queue.md) — LDIRW applied to the VRAM queue copy path
- [Sprites and OAM](../03_Graphics/Sprites-and-OAM.md) — shadow OAM flush architecture
