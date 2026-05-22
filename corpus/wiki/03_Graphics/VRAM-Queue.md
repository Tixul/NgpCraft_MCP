# VRAM Queue

LDIRW-accelerated VRAM queue copy: replacing the C word-by-word loop in the queue flush (CMD_COPY path) with a hardware LDIRW block transfer for maximum throughput when uploading tiles and tilemap data to VRAM.

---


## 1. Purpose

The VRAM queue flush (`ngpc_vramq_flush()`) processes a queue of pending VRAM writes
each VBlank. The `CMD_COPY` path (bulk word copy) is the hot path: it runs every frame and
transfers tile or tilemap data from RAM/ROM into VRAM.

Replacing the C word loop with the TLCS-900H `LDIRW` instruction (hardware block
word transfer) gives roughly **4× the throughput** for this path.

Three pieces are involved:

| Component | Role |
|-----------|------|
| VRAM queue C module | `CMD_COPY` calls `ngpc_memcpy_w()` instead of a C loop |
| VRAM queue ASM module | Implements `_ngpc_memcpy_w` via `LDIRW` |
| Build (makefile) | Add the ASM object to the `OBJS` list |

`ngpc_memcpy_w` is an **internal helper** — it is not part of the public API.
User code calls `ngpc_vramq_copy()` / `ngpc_vramq_fill()` as before; the ASM
optimization is transparent.

---

## 2. API Contract

```c
/* Internal helper — called by the CMD_COPY handler */
void ngpc_memcpy_w(u32 dst_addr, u32 src_addr, u32 words);
```

| Parameter | Constraint |
|-----------|------------|
| `dst_addr` | VRAM near address: `0x00008000`–`0x0000BFFF` |
| `src_addr` | RAM (near) or ROM (far) source address |
| `words` | Number of **16-bit words** to copy (not bytes) |

> `words` must be non-zero — see [§4.1](#41-ldirw-with-bc0-is-not-a-no-op).
> The caller is responsible for the guard.

---

## 3. Calling Convention — cc900 Large Model

All three parameters are declared `u32` to guarantee a stable 4-byte-per-slot
stack layout in cc900's large memory model (see [§4.2](#42-u16-arguments-and-stack-padding-ambiguity)).

Stack layout at `_ngpc_memcpy_w` entry:

```
(xsp+ 0)  far return address     (4 bytes — large model)
(xsp+ 4)  dst_addr               (u32, 4 bytes)
(xsp+ 8)  src_addr               (u32, 4 bytes)
(xsp+12)  words                  (u32, 4 bytes — only low 16 bits used as BC)
```

ASM implementation outline:

```asm
module ngpc_vramq_asm

    .section code,large

public _ngpc_memcpy_w

_ngpc_memcpy_w:
    ; Load dst_addr -> XDE
    ld      xde,(xsp+4)
    ; Load src_addr -> XHL
    ld      xhl,(xsp+8)
    ; Load words (low 16 bits) -> BC  (guard: caller ensures != 0)
    ld      bc,(xsp+12)
    ; Hardware block word transfer: copies BC words from (XHL) to (XDE), incrementing both
    ldirw
    ret
```

> `LDIRW` transfers BC 16-bit words from `(XHL)` to `(XDE)`, post-incrementing
> both pointers and decrementing BC until BC=0.
> At 6.144 MHz it transfers one word every ~2 cycles — significantly faster than
> an equivalent C loop.

---

## 4. Gotchas and Known Bugs

### 4.1 LDIRW with BC=0 Is Not a No-Op

On TLCS-900H, `LDIRW` with `BC=0` copies **65536 words** — it does not skip.

**Fix:** Guard the call before issuing `LDIRW`:

```c
/* In the CMD_COPY handler */
if (words != 0)
    ngpc_memcpy_w(dst, src, words);
```

The ASM stub itself does not add this guard — the C caller is responsible.

### 4.2 u16 Arguments and Stack Padding Ambiguity

In cc900's large model, `u16` arguments can be packed or padded in ways that make
`(xsp+N)` offsets unpredictable across compiler versions.

**Fix:** Declare all three parameters as `u32`. Each occupies exactly 4 bytes on the
stack, making the offsets `xsp+4`, `xsp+8`, `xsp+12` unambiguous and stable.

### 4.3 Tilemap Debug — Always Use HW_* Symbols

When writing validation tests that display tilemap data, use the hardware constant
symbols rather than hardcoded addresses:

```c
/* SCR1 tilemap base */
#define HW_SCR1_MAP  0x9000u
/* Cell address: HW_SCR1_MAP + y * SCR_MAP_W + x */
u16 *cell = (u16 *)(HW_SCR1_MAP + (u16)(row * SCR_MAP_W + col));
```

Hardcoded addresses are fragile and mask addressing bugs during debugging.

### 4.4 asm900: "Illegal Source File Format"

If asm900 returns:

```
ASM900-Fatal-152 : Illegal source file format
```

the most common cause is a `.asm` file saved as **UTF-8 with BOM** or another
unsupported encoding.

**Fix:** Save all `.asm` files as **ASCII / ANSI** (no BOM). In VS Code: bottom
status bar → select encoding → "Save with Encoding" → "Western (Windows 1252)" or
plain ASCII.

---

## 5. Validation Test

A minimal visual test to confirm the full chain (queue → flush → LDIRW → VRAM):

1. At init, write a label to SCR1 using a direct write: `"VQ:"` at a fixed tile position.
2. Allocate a 2-entry RAM buffer holding two tilemap words (the counter digits).
3. Each frame: increment the counter, update the RAM buffer, issue
   `ngpc_vramq_copy(dst_addr, src_buffer, 2)`.
4. On-screen counter increments every frame → queue + VBlank flush + LDIRW confirmed working.

If the counter is static or shows garbage: verify the `words != 0` guard, check `src_addr`
near/far (ROM source requires far pointer in the higher-level call), and confirm the
VRAM queue ASM object is in `OBJS`.

---

## Quick Reference

| Item | Value / Rule |
|------|--------------|
| Target function | `ngpc_memcpy_w(dst_addr, src_addr, words)` — internal helper |
| Declared types | All `u32` — guarantees stable stack offsets in large model |
| Stack: dst_addr | `(xsp+4)` |
| Stack: src_addr | `(xsp+8)` |
| Stack: words (BC) | `(xsp+12)` — low 16 bits only |
| LDIRW BC=0 | Copies 65536 words — guard `if (words != 0)` in caller |
| Encoding rule | `.asm` files must be ASCII/ANSI — no UTF-8 BOM |
| Tilemap base | `HW_SCR1_MAP = 0x9000`, cell = `base + y*SCR_MAP_W + x` |
| Performance | ~4× faster than C word loop at 6.144 MHz |
| Build | Add the VRAM queue ASM object to `OBJS` |

---

## See Also

- [Effects-and-Raster](Effects-and-Raster.md) — ngpc_vramq public API (`ngpc_vramq_copy`, `ngpc_vramq_fill`), queue flush behavior
- [Assembly](../02_CPU-and-Toolchain/Assembly.md) — asm900 module structure, LDIRW semantics and BC=0 gotcha, calling convention details
- [Game-Loop](../05_Systems/Game-Loop.md) — VBlank budget, when the queue flush runs
- [Build-Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — cc900 large model, stack layout, inline ASM conventions
