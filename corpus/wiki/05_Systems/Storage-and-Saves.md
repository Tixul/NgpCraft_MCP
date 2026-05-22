# Storage and Saves

Flash save (standalone AMD stubs plus legacy BIOS/system.lib reference) and real-time clock for NGPC homebrew.

---


## 1. Memory Map — Storage Regions

```
0x004000 - 0x005FFF   Main RAM (8 KB)
0x006000 - 0x006BFF   Battery-backed RAM (3 KB) — lightweight saves (volatile, no erase)
0x007000 - 0x007FFF   Z80 audio RAM (4 KB, shared with sound CPU)
0x006F80 - 0x006FFF   BIOS system area (variables, ISR vectors)
0x200000 - 0x3FFFFF   Cartridge ROM (2 MB, FAR access only)
0x3FA000              Default flash save offset (block 0x21, within 8 KB block)
0x3FC000 - 0x3FFFFF   Reserved by system — do NOT use
```

**Key constraint:** the last 16 KB (`0x3FC000-0x3FFFFF`) is reserved by the system.
If your flash backend erases a 64 KB block, use block 0x21 (offset `0x1FA000`) — not
block 0x1F, which overlaps the reserved area.

---

## 2. Flash Save — ngpc_flash

### 2.1 API

```c
void ngpc_flash_init(void);              /* Call at startup */
void ngpc_flash_save(const void *data);  /* Write 256 bytes to flash */
void ngpc_flash_load(void *data);        /* Read 256 bytes from flash */
u8   ngpc_flash_exists(void);            /* Check if valid save exists (magic check) */
```

Flash has **limited write cycles**. Never save every frame.

### 2.2 Magic Number Requirement

`ngpc_flash_exists()` validates the first 4 bytes of the save area.
Your save struct **must** begin with `{ 0xCA, 0xFE, 0x20, 0x26 }`.

```c
typedef struct {
    u8 magic[4];   /* always { 0xCA, 0xFE, 0x20, 0x26 } */
    u8 version;
    u8 hp;
    u8 level;
    /* ... up to 251 more bytes */
} SaveData;
```

### 2.3 Full Save/Load Example

```c
void save_game(void)
{
    SaveData s;
    s.magic[0] = 0xCA; s.magic[1] = 0xFE;
    s.magic[2] = 0x20; s.magic[3] = 0x26;
    s.version  = 1;
    s.hp       = player.hp;
    s.level    = player.level;
    ngpc_flash_save(&s);
}

void load_game(void)
{
    if (ngpc_flash_exists()) {
        SaveData s;
        ngpc_flash_load(&s);
        if (s.version == 1) {
            player.hp    = s.hp;
            player.level = s.level;
        }
    } else {
        /* No valid save — initialize defaults in RAM only */
        player.hp    = 3;
        player.level = 1;
    }
}
```

---

## 3. Real-Time Clock — ngpc_rtc

### 3.1 API

```c
void ngpc_rtc_get(NgpcTime *t);                          /* Read date/time (BCD) */
void ngpc_rtc_set_alarm(NgpcAlarm *a);                   /* Alarm during gameplay */
void ngpc_rtc_set_wake(NgpcAlarm *a);                    /* Wake-up alarm (powers on) */
void ngpc_rtc_set_alarm_handler(void (*handler)(void));  /* Install alarm ISR */
```

### 3.2 BCD Encoding

The NGPC RTC uses **BCD encoding** for all time values: `0x12` = twelve (not 18).

```c
/* BCD helpers */
u8 bin = BCD_TO_BIN(bcd_value);   /* 0x23 -> 23 */
u8 bcd = BIN_TO_BCD(23);          /* 23  -> 0x23 */

/* Example: display current time */
NgpcTime t;
ngpc_rtc_get(&t);
u8 hour = BCD_TO_BIN(t.hour);
u8 min  = BCD_TO_BIN(t.minute);
```

---

## 4. Save Design Patterns

### 4.1 Recommended Save Struct Layout

`ngpc_flash_exists()` only validates the magic number — a partial or stale save may still
pass this check. Use full application-level validation:

```c
typedef struct {
    u8  magic[4];    /* { 0xCA, 0xFE, 0x20, 0x26 } */
    u8  version;     /* layout version — reject mismatches */
    /* game data */
    u8  options;
    u8  continues;
    /* high scores */
    u16 score_hi[10];
    u16 score_lo[10];
    u8  name[10][4]; /* 3 chars + null */
    /* validation */
    u8  checksum;
    u8  _pad[...];
} SaveData;
```

Full validation checklist on load:
1. Magic matches `{ 0xCA, 0xFE, 0x20, 0x26 }`
2. `version` matches current layout
3. Fields within valid bounds
4. Checksum passes

> **Put `checksum` at a fixed offset, before the terminal `_pad[]` — never last.**
> If the struct ends with `u8 _pad[SAVE_SIZE - N]; u8 checksum;`, cc900 may pad the
> whole struct to align `sizeof`, shifting the checksum's actual offset in the flash
> image away from `SAVE_SIZE-1`. With `checksum` ahead of the terminal padding, its
> offset is deterministic and the trailing `_pad[]` absorbs any compiler padding. This
> layout is validated on hardware in a shipped shmup profile struct.
>
> A simple self-referential checksum (computed over every byte except itself):
> ```c
> u8 sum = 0u;
> for (i = 0u; i < SAVE_SIZE - 1u; i++) sum ^= ((u8 *)&save)[i];
> save.checksum = sum ^ 0x5Au;
> ```
> Keep one save struct in RAM as the single source of truth; modify it in place,
> recompute the checksum, then `ngpc_flash_save(&save)`. This singleton + dirty-commit
> pattern avoids the scratch-buffer load/clear/rebuild cycles that invite corruption.

### 4.2 Append-Only Slot Pattern (Validated)

**Validated solution** — avoids erase-per-save and eliminates mid-session CLR_FLASH_RAM issues:

The validated configuration (confirmed on real hardware after extensive testing) uses
**16 slots × 512 bytes** (`SAVE_SIZE=512`, `rbc3=2`) inside the 8 KB block at offset `0x1FA000`:

```
Slot 0:  0x1FA000 .. 0x1FA1FF  (512 bytes)
Slot 1:  0x1FA200 .. 0x1FA3FF
...
Slot 15: 0x1FC000 .. 0x1FC1FF
```

> **Template default:** `ngpc_flash.h` ships with `SAVE_SIZE 256` (32 slots × 256 bytes,
> `rbc3=1`) as a lighter starting point. **Always change to `SAVE_SIZE 512` for production.**
> See §5.1 for why `rbc3=1` is unreliable on real hardware.

**Write:** find the first empty slot (first byte == `0xFF`), write there.
Never erase mid-session — only erase when the entire block is full. The template handles
erase via `ngpc_flash_erase_asm()` (standalone AMD stub, no `system.lib` required).
`CLR_FLASH_RAM` (system.lib) is the legacy alternative; it works on the first call per
session but silently fails if called a second time.

**Read at boot:** scan slots 15 -> 0, return the last slot with a valid magic.

```c
SaveData *ngpc_flash_find_valid_slot(void)
{
    for (s8 i = MAX_SLOTS - 1; i >= 0; i--) {
        SaveData *p = (SaveData *)(SAVE_OFFSET + i * SLOT_SIZE);
        if (p->magic[0] == 0xCA && p->magic[1] == 0xFE
         && p->magic[2] == 0x20 && p->magic[3] == 0x26)
            return p;
    }
    return NULL;  /* no valid save */
}
```

**Why this works:** each save appends to a new slot — no erase required until all 16 slots
are used. The flash erase only happens at that point, and `CLR_FLASH_RAM` is reliable
on first call. This avoids the corruption risk of erase-on-every-save.

### 4.3 When to Save

| Event | Save? |
|-------|-------|
| Boot / startup | **No** — load defaults into RAM only |
| Every frame | **Never** — flash write cycles are finite |
| Button press in options | **No** — mark dirty in RAM only |
| Leave options screen | Yes — flush dirty RAM to flash |
| High score validated | Yes — explicit commit |
| Save point (RPG/platformer) | Yes — explicit commit |

> "No crash" does not mean "valid save." To confirm a flash backend works:
> 1. Boot normally
> 2. Trigger a save
> 3. Power off
> 4. Power on
> 5. Verify the magic is present and data is correct

### 4.4 Default Initialization at Boot

```c
void save_init(void)
{
    if (ngpc_flash_exists()) {
        ngpc_flash_load(&g_save);
        if (!save_validate()) {
            save_reset_defaults();
        }
    } else {
        save_reset_defaults();
    }
    /* Never write to flash here — RAM-only initialization */
}

void save_reset_defaults(void)
{
    g_save.magic[0] = 0xCA; g_save.magic[1] = 0xFE;
    g_save.magic[2] = 0x20; g_save.magic[3] = 0x26;
    g_save.version    = 1;
    g_save.continues  = 3;
    for (u8 i = 0; i < 10; i++) {
        g_save.score[i] = 0;
        memcpy(g_save.name[i], "---", 4);
    }
}
```

> Writing to flash at boot risks powering off the console on real hardware
> if the write triggers the watchdog. Always initialize RAM defaults only.

### 4.5 High Score Integration

```c
u8 is_high_score(u32 score)
{
    return score > g_save.score[9];  /* beats 10th place */
}

void insert_high_score(u32 score, const char *name_3)
{
    /* Find insertion point */
    u8 pos = 9;
    while (pos > 0 && score > g_save.score[pos - 1])
        pos--;

    /* Shift down */
    for (u8 i = 9; i > pos; i--) {
        g_save.score[i] = g_save.score[i - 1];
        memcpy(g_save.name[i], g_save.name[i - 1], 4);
    }

    /* Insert */
    g_save.score[pos] = score;
    memcpy(g_save.name[pos], name_3, 3);
    g_save.name[pos][3] = 0;

    /* Commit */
    ngpc_flash_save(&g_save);
}
```

Design note: whether scores from continued runs count toward the top 10 is a game-design
decision, not a technical one.

---

## 5. Flash Hardware Details

### 5.1 Confirmed BIOS Parameters

Parameters confirmed by cross-analysis of multiple working NGPC homebrews with
persistent saves on real hardware:

```c
#define SAVE_BLOCK    0x21       /* NOT 0x1F — overlaps reserved area      */
#define SAVE_OFFSET   0x1FA000   /* CPU address: 0x200000 + 0x1FA000 = 0x3FA000 */
#define SAVE_SIZE     512        /* rbc3=2 — validated on real hardware     */
```

> **`BC=1` (256 bytes) is unreliable on real hardware** — writes complete without error
> but data may not persist after power-off. Always use `BC=2` (512 bytes) in production.
> The template ships with `SAVE_SIZE 256` as a configurable starting point;
> change it to `512` before shipping any game.

BIOS vector IDs used:

| Vector | ID | Operation |
|--------|----|-----------|
| `VECT_FLASHERS` | `rw3=8` | Erase block |
| `VECT_FLASHWRITE` | `rw3=6` | Write N×256 bytes |

### 5.2 Raw ASM Sequence (Erase + Write)

The template uses **standalone AMD stubs** — no `system.lib` needed.
The stubs are position-independent byte sequences (115 bytes write, 98 bytes erase) extracted
by disassembly from a hardware-validated ROM. They are copied to RAM at `0x6E00` and executed
from there (a flash chip cannot execute code while being programmed).

**Key insight — I/O register `0x6E` (flash bus control):**
User code CAN write this register. `(0x6E) = 0x14` asserts `/WE` on the cartridge slot,
enabling flash write cycles. `(0x6E) = 0xF0` deasserts it. This register is what
`WRITE_FLASH_RAM` / `CLR_FLASH_RAM` set internally — missing it was the root cause of all
previously failed manual flash attempts.

**Standalone sequence (template default):**

```asm
; --- Common prologue ---
ld  (0x6E), 0x14        ; assert /WE on cart bus  (CRITICAL — I/O reg, user-writable)
ld  (0x6F), 0xB1        ; watchdog: extended mode for long flash operation

; --- Erase block 33 (F16_B33, 8 KB, abs 0x3FA000) ---
ld  xde, 0x6E00         ; destination: RAM
ld  xhl, _erase_stub    ; source: 98-byte AMD erase sequence in ROM
ld  bc,  98
; ldir — copy stub to RAM (stubs cannot run from the flash chip itself)
ld  xix, 0x200000       ; AMD unlock base = CS0
ld  xiy, 0              ; XIY = 0 (erase stub parameter)
ld  a,   0              ; A   = 0 (erase stub parameter)
ld  xde, 0x3FA000       ; block address (absolute)
call 0x6E00             ; execute stub from RAM
ld  (0x6F), 0x4E        ; restore watchdog
ld  (0x6E), 0xF0        ; deassert /WE

; --- Write 512 bytes ---
ld  xhl, (xsp+4)        ; source pointer (from C stack, bank-0 — no bank-3 promotion needed)
ld  xde, (xsp+8)        ; relative offset (e.g. 0x1FA000 + slot*512)
ld  xix, 0x200000
add xde, xix            ; make destination absolute (0x3FA000 + slot*512)
; [push xde / push xhl — preserve across ldir copy]
ld  xde, 0x6E00
ld  xhl, _write_stub    ; source: 115-byte AMD write sequence in ROM
ld  bc,  115
; ldir — copy stub to RAM
; [pop xhl / pop xde — restore src and dest]
ld  bc,  0x0002         ; 2 pages × 256 = 512 bytes
call 0x6E00
ld  (0x6F), 0x4E
ld  (0x6E), 0xF0
```

**Legacy sequence (system.lib path, kept as reference):**

```asm
; Erase block 0x21 via CLR_FLASH_RAM (system.lib)
ld ra3,   0
ld rb3,   0x21
ld (rWDCR), 0x4E
calr CLR_FLASH_RAM      ; reliable on first call only per session

; Write 512 bytes via WRITE_FLASH_RAM (system.lib)
ld ra3,   0
ld rbc3,  2             ; 2 * 256 = 512 bytes
ld xhl,   (xsp+4)
ld xhl3,  xhl           ; bank-3 promotion required for BIOS path
ld xde,   (xsp+8)
ld xde3,  xde
ld (rWDCR), 0x4E
calr WRITE_FLASH_RAM
```

### 5.3 cc900 Inline ASM Pointer Rules

**Standalone path** (no bank-3 promotion needed):

```asm
; Load from C stack — stays in bank 0, no ld xhlN,xhl required
ld xhl, (xsp+4)         ; source pointer (first C arg)
ld xde, (xsp+8)         ; flash offset  (second C arg)
ld xix, 0x200000
add xde, xix            ; offset -> absolute address
```

**Legacy system.lib path** (bank-3 promotion required by BIOS convention):

```c
/* CORRECT — load argument from stack, copy to bank 3 */
__asm("ld xhl, (xsp+4)");   /* first arg = source pointer */
__asm("ld xhl3, xhl");      /* promote to bank 3 for BIOS */

/* INCORRECT — cc900/asm900 cannot see C symbols from inline ASM */
/* __asm("ld xhl, (my_c_symbol)"); */  /* Error-221: Undefined symbol */
```

Keep the flash function **minimal** — no local variables, no C statements other than
a guard `(void)data`, to ensure the stack prologue stays predictable for the `xsp+4` offset.

### 5.4 Known Flash Pitfalls

| Pitfall | Consequence | Fix |
|---------|-------------|-----|
| Write at boot | Power-off on real hardware | Load defaults to RAM only at boot |
| `BC=1` (256 bytes) | Unreliable on real hardware — data may not persist after power-off | Use `BC=2` (512 bytes, `SAVE_SIZE=512`) |
| Block 0x1F | Overlaps system reserved area | Use block 0x21 |
| Erase on every save | Flash wear, risks mid-session failure | Append-only slots |
| "No crash" = success | False | Validate with full power-cycle test |
| `(0x6E)` not set to `0x14` | Write cycles silently ignored by hardware | Set `(0x6E)=0x14` before stub, `(0x6F)=0xB1`; restore after |
| Executing stub from flash | Undefined behavior (chip busy during program) | Copy stub to RAM at `0x6E00`, execute from there |
| `CLR_FLASH_RAM` second call | Silently fails (BIOS internal bug) — legacy path only | Erase only when block full; standalone stubs are not affected |
| Saving while a raster/Timer0 ISR is active | The split ISR writes `(0x6E)`/`(0x6F)` — the same control bytes the flash stub drives -> corruption | Disable the split timer (e.g. `hud_raster_disable()`) **before** `ngpc_flash_save()`, or save only from a state where raster is already off |
| Checksum field placed last (after terminal padding) | cc900 may pad the whole struct -> the checksum's flash offset is no longer `SAVE_SIZE-1` -> validation drifts | Put `checksum` at a fixed offset **before** the terminal `_pad[]` (see §4.1) |

---

## Quick Reference

| Item | Value / Pattern | Notes |
|------|-----------------|-------|
| Flash save offset | `0x1FA000` | CPU address: `0x3FA000` |
| Flash block | `0x21` | NOT `0x1F` — reserved area overlap |
| Write size | `BC=2` = 512 bytes | `BC=1` (256 B) unreliable on real hardware — use 512 in production |
| VECT_FLASHERS | `rw3=8` | Erase (broken for blocks 32-34 — use standalone stub) |
| VECT_FLASHWRITE | `rw3=6` | Write (replaced by standalone write stub in template) |
| Magic bytes | `{ 0xCA, 0xFE, 0x20, 0x26 }` | First 4 bytes of save struct |
| Validation | magic + version + bounds + checksum | Magic alone insufficient |
| Save slots | 16 slots × 512 bytes in 8 KB block | Append-only pattern |
| Slot scan at boot | Slots 15 -> 0, last valid = current | |
| Erase policy | Only when block full | Standalone AMD stub (`ngpc_flash_erase_asm`); `CLR_FLASH_RAM` legacy only |
| Save trigger | Options exit / high score commit | Never at boot, never per-frame |
| Boot init | Load to RAM only | Flash write at boot = power-off risk |
| RTC encoding | BCD | `BCD_TO_BIN()` / `BIN_TO_BCD()` |
| Battery-backed RAM | `0x006000-0x006BFF` (3 KB) | No erase needed, volatile (no battery on cart) |

---

## See Also

- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — Full memory map, ROM base address, BIOS area
- [BIOS](../01_Hardware/BIOS.md) — BIOS SWI vectors, system.lib functions (legacy reference)
- [Game Loop](Game-Loop.md) — Watchdog rules, VBlank ISR constraints
- [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — ROM layout, linker sections, flash offset in 2 MB cartridge
