# Debug Tools

On-device debug instrumentation for NGPC homebrew: CPU profiler via raster position, ring-buffer diagnostic log, and runtime assertions with on-screen fault display.

---


## 1. CPU Profiler — ngpc_debug

### 1.1 API

```c
void ngpc_debug_begin(void);                                       /* mark start of logic */
void ngpc_debug_end(void);                                         /* mark end of logic */
void ngpc_debug_draw_bar(plane, pal_ok, pal_warn, pal_over);       /* visual CPU bar */
void ngpc_debug_print_pct(plane, pal, x, y);                       /* print "XX%" */
void ngpc_debug_print_fps(plane, pal, x, y);                       /* print "XXFPS" */
u8   ngpc_debug_get_lines(void);                                   /* raw scanlines used */
u8   ngpc_debug_get_pct(void);                                     /* CPU usage 0-100+ */
```

The profiler measures game logic duration using the hardware raster position register
(`HW_RAS_V` at `0x8009`): it reads the current scanline at `begin` and `end`, and
computes the percentage of the 152-scanline frame consumed.

Color bar thresholds:
- **Green:** < 80% — safe
- **Yellow:** 80-100% — tight
- **Red:** > 100% — frame overflow

All calls become no-ops when `NGPC_DEBUG` is defined as `0`.

### 1.2 Typical Game Loop Usage

```c
void main(void)
{
    ngpc_init();
    ngpc_load_sysfont();
    /* ... setup ... */

    while (1) {
        ngpc_vsync();
        ngpc_input_update();

        ngpc_debug_begin();
            game_update();
        ngpc_debug_end();

        ngpc_debug_draw_bar(GFX_SCR2, PAL_GREEN, PAL_YELLOW, PAL_RED);
        /* or: ngpc_debug_print_pct(GFX_SCR1, 0, 18, 0); */
    }
}
```

> `ngpc_debug_begin()` / `ngpc_debug_end()` bracket **only game logic**, not rendering
> or VBlank-triggered code — to get an accurate budget reading.

---

## 2. Ring Buffer Log — ngpc_log

### 2.1 API

```c
void ngpc_log_init(void);
void ngpc_log_clear(void);
void ngpc_log_hex(const char *label, u16 value);   /* log "LABEL: 0x1234" */
void ngpc_log_str(const char *label, const char *str);
void ngpc_log_dump(plane, pal, x, y);              /* render log to tilemap */
u8   ngpc_log_count(void);                         /* number of entries in buffer */

/* Convenience macros */
NGPC_LOG_HEX("PAD", ngpc_pad_held);
NGPC_LOG_STR("ST",  "RUN ");
```

Stores short diagnostic entries in a fixed-size ring buffer (~288 bytes RAM).
New entries overwrite the oldest when the buffer is full.

### 2.2 Usage Notes

- Useful on real hardware where stdout/serial is not available.
- Display with `ngpc_log_dump()` on a spare tilemap plane or overlay.
- `ngpc_log_hex()` formats the value as hexadecimal.
- Macros (`NGPC_LOG_HEX`, `NGPC_LOG_STR`) compile to no-ops in release builds.
- RAM cost: ~288 bytes for the ring buffer — significant on a 8 KB RAM budget.
  Disable in release.

---

## 3. Runtime Assert — ngpc_assert

```c
NGPC_ASSERT(condition);

/* Examples */
NGPC_ASSERT(pointer != 0);
NGPC_ASSERT(index < ENTITY_MAX);
```

In **debug builds:** assertion failure displays an on-screen fault page (file + line,
if available) and enters a blinking loop. The game does not continue.

In **release builds:** `NGPC_ASSERT` is compiled out completely — zero cost.

> Assertion failures loop indefinitely. This is intentional — it forces the developer
> to notice the failure on screen. The watchdog will eventually reset the console,
> but the fault screen should be visible long enough to read.

---

## 4. Build Flags

| Flag | Effect |
|------|--------|
| `#define NGPC_DEBUG 1` | Enable profiler (default in debug profile) |
| `#define NGPC_DEBUG 0` | Disable profiler — all calls become no-ops |
| `#define NGP_PROFILE_RELEASE 1` | Strip `NGPC_ASSERT` and `NGPC_LOG_*` macros |

In the build Makefile, debug vs release is typically controlled by passing
`-DNGP_PROFILE_RELEASE=1` to `CDEFS` for release builds.

---

## Quick Reference

| Item | Details |
|------|---------|
| CPU profiler instrument | `ngpc_debug_begin()` / `ngpc_debug_end()` around game logic only |
| Bar: green | < 80% CPU |
| Bar: yellow | 80-100% CPU |
| Bar: red | > 100% — frame overflow |
| Profiler register | `HW_RAS_V` (`0x8009`) — current scanline |
| Log buffer size | ~288 bytes RAM |
| Log entry format | Hex: `"LABEL: 0x1234"` — String: `"LABEL: str"` |
| Log overflow | Ring buffer — oldest entry overwritten |
| Assert in debug | On-screen fault page + blinking loop |
| Assert in release | Compiled out (zero cost) |
| Disable all debug | `NGPC_DEBUG=0` + `NGP_PROFILE_RELEASE=1` |

---

## See Also

- [Game Loop](Game-Loop.md) — VBlank sync, `ngpc_vsync()`, frame budget structure
- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — `HW_RAS_V` register address, scanline counter
- [Effects and Raster](../03_Graphics/Effects-and-Raster.md) — `ngpc_text_print()` for displaying debug values on-screen
- [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — Makefile CDEFS, release build configuration
