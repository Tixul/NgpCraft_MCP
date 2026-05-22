# BIOS Reference

BIOS services, calling conventions, vector table, register bank 3, watchdog, and interrupt vector assignments for the Neo Geo Pocket Color.

> Note: All content uses ASCII only (avoids encoding issues on Windows/PowerShell).

---


## 1. Calling Convention — Vector Table (0xFFFE00)

Most BIOS functions are accessible via a function table at `0xFFFE00`.
Each entry is a 32-bit pointer. Index = vector number.

**Call sequence:**

```asm
; 1. Load the vector number into RW3
ld rw3, <VECTOR>

; 2. Load parameters into bank 3 registers (XHL3, XDE3, RB3, RC3...)

; 3. Switch to bank 3
ldf 3

; 4. Compute offset in the table (vector * 4)
add w, w        ; w *= 2
add w, w        ; w *= 2  =>  w = vector * 4

; 5. Load address from table and call
ld xix, 0xFFFE00
ld xix, (xix+w)
call xix
```

**C example (RTC get):**
```c
__asm(" ld rw3, " NGPC_STR(BIOS_RTCGET));
__asm(" ld xde, (xsp+4)");    /* NgpcTime* from stack */
__asm(" ld xhl3, xde");       /* XHL3 = pointer to struct */
__asm(" ldf 3");
__asm(" add w, w");
__asm(" add w, w");
__asm(" ld xix, 0xfffe00");
__asm(" ld xix, (xix+w)");
__asm(" call xix");
```

---

## 2. Calling Convention — SWI 1

Some BIOS functions use the `swi 1` (software interrupt 1) instruction,
with parameters loaded into bank 3 registers before the `swi`.

```asm
; Load parameters into bank 3 WITHOUT switching (swi reads them directly)
ldb ra3, <function_code>
ldb rw3, <VECTOR>
swi 1
```

**C example (shutdown):**
```c
__asm("ldb ra3, 3");
__asm("ldb rw3, " NGPC_STR(BIOS_SHUTDOWN));
__asm("swi 1");
```

---

## 3. Register Bank 3

BIOS calls use registers from "bank 3" (dedicated system call registers).

| Register | Alias | Size | Role |
|----------|-------|------|------|
| `RW3` | W of bank 3 | 16-bit | Vector number |
| `RA3` | A of bank 3 | 8-bit | Function code / parameter |
| `RB3` | B of bank 3 | 8-bit | Integer parameter (e.g., flash block number) |
| `RC3` | C of bank 3 | 8-bit | Integer parameter (e.g., auto-speedup flag) |
| `RBC3` | BC of bank 3 | 16-bit | 16-bit parameter (e.g., number of flash pages) |
| `XHL3` | HL of bank 3 | 24-bit | Source pointer (e.g., data address) |
| `XDE3` | DE of bank 3 | 24-bit | Destination pointer / offset |

---

## 4. BIOS Vector Table

Defined in `ngpc_hw.h` as `BIOS_*` constants.

| Constant | Value | Description | Convention |
|----------|-------|-------------|------------|
| `BIOS_SHUTDOWN` | 0 | Power off the console | SWI 1, ra3=3 |
| `BIOS_CLOCKGEARSET` | 1 | Change CPU speed | Table 0xFFFE00 |
| `BIOS_RTCGET` | 2 | Read time/date (BCD) | Table 0xFFFE00 |
| `BIOS_INTLVSET` | 4 | Configure interrupt levels | Table 0xFFFE00 |
| `BIOS_SYSFONTSET` | 5 | Load system font into tile RAM | SWI 1, ra3=3 |
| `BIOS_FLASHWRITE` | 6 | Write to flash | SWI 1 |
| `BIOS_FLASHALLERS` | 7 | Erase all flash blocks | SWI 1 |
| `BIOS_FLASHERS` | 8 | Erase one flash block | SWI 1, ra3=0, rb3=block |
| `BIOS_ALARMSET` | 9 | Set an alarm (console on) | Table 0xFFFE00 |
| `BIOS_ALARMDOWNSET` | 11 | Set a wake-up alarm (console off) | Table 0xFFFE00 |
| `BIOS_FLASHPROTECT` | 13 | Protect flash blocks | Table 0xFFFE00 |
| `BIOS_GEMODESET` | 14 | Switch K1GE/K2GE mode | Table 0xFFFE00 |

---

## 5. BIOS Call Details

### 5.1 BIOS_SHUTDOWN (0) — Power off

```c
/* ra3=3 = shutdown function code */
__asm("ldb ra3, 3");
__asm("ldb rw3, " NGPC_STR(BIOS_SHUTDOWN));
__asm("swi 1");
```

### 5.2 BIOS_CLOCKGEARSET (1) — CPU speed

```c
/* rb3 = divisor (0=6MHz, 1=3MHz, 2=1.5MHz, 3=768KHz, 4=384KHz) */
/* rc3 = 0 (no joypad auto-speedup), or 1 to enable */
__asm("ld rw3, " NGPC_STR(BIOS_CLOCKGEARSET));
/* load divider from stack: */
__asm("ld xde, (xsp+4)");
__asm("ld b, e");           /* rb3 = divider */
__asm("ld c, 0");           /* rc3 = 0 */
__asm("ldf 3");
__asm("add w, w"); __asm("add w, w");
__asm("ld xix, 0xfffe00");
__asm("ld xix, (xix+w)");
__asm("call xix");
```

### 5.3 BIOS_RTCGET (2) — Read clock

```c
/* xhl3 = pointer to NgpcTime (7 bytes, BCD) */
__asm(" ld rw3, " NGPC_STR(BIOS_RTCGET));
__asm(" ld xde, (xsp+4)");  /* NgpcTime* from stack */
__asm(" ld xhl3, xde");
__asm(" ldf 3");
__asm(" add w, w"); __asm(" add w, w");
__asm(" ld xix, 0xfffe00");
__asm(" ld xix, (xix+w)");
__asm(" call xix");
```

### 5.4 BIOS_SYSFONTSET (5) — Load system font

```c
/* Loads 96 ASCII glyphs (0x20-0x7F) into tile RAM starting at slot 32 */
__asm("ldb ra3, 3");
__asm("ldb rw3, " NGPC_STR(BIOS_SYSFONTSET));
__asm("swi 1");
```

> Tile slots 0-31 = reserved. Slots 32-127 = system font after this call.
> Slots 128+ are free for user tiles.

### 5.5 BIOS_FLASHERS (8) + BIOS_FLASHWRITE (6) — Save to flash

```c
/* Step 1: erase the flash block */
/* ra3=0 (ROM base), rb3=block number (0x1F for offset 0x1FA000), rw3=8 */
__asm("ld ra3,0");
__asm("ld rb3," NGPC_STR(SAVE_BLOCK));
__asm("ld rw3," NGPC_STR(BIOS_FLASHERS));
HW_WATCHDOG = WATCHDOG_CLEAR;   /* mandatory before long flash operation */
__asm("swi 1");

/* Step 2: write 256 bytes */
/* ra3=0, rbc3=1 (1*256=256 bytes), xhl3=source, xde3=dest offset, rw3=6 */
__asm("ld ra3,0");
__asm("ld rbc3,1");
__asm("ld xhl,(xsp+4)");    /* data* from stack */
__asm("ld xhl3,xhl");
__asm("ld xde3," NGPC_STR(SAVE_OFFSET_ASM));
__asm("ld rw3," NGPC_STR(BIOS_FLASHWRITE));
HW_WATCHDOG = WATCHDOG_CLEAR;
__asm("swi 1");
HW_WATCHDOG = WATCHDOG_CLEAR;
```

> See [Storage and Saves](../05_Systems/Storage-and-Saves.md) for the complete flash save strategy
> (append-only slots, block selection, erase timing).

### 5.6 BIOS_ALARMSET (9) — In-game alarm

```c
/* xiy = pointer to NgpcAlarm { u8 day, u8 hour, u8 minute } (BCD) */
__asm(" ld rw3, " NGPC_STR(BIOS_ALARMSET));
__asm(" ld xiy, (xsp+4)");
__asm(" ldf 3");
__asm(" ld h, (xiy +)");  /* day    -> H */
__asm(" ld qc, h");       /* QC = day */
__asm(" ld b, (xiy +)");  /* hour   -> B */
__asm(" ld c, (xiy +)");  /* minute -> C */
__asm(" add w, w"); __asm(" add w, w");
__asm(" ld xix, 0xfffe00");
__asm(" ld xix, (xix+w)");
__asm(" call xix");
```

---

## 6. BCD Values (RTC)

The RTC returns **BCD** (Binary Coded Decimal) values.
`0x23` = twenty-three (not 35).

```c
/* Conversion macros (ngpc_rtc.h) */
#define BCD_TO_BIN(bcd)  (((bcd) >> 4) * 10 + ((bcd) & 0xF))
#define BIN_TO_BCD(bin)  ((((bin) / 10) << 4) | ((bin) % 10))

/* Example */
NgpcTime t;
ngpc_rtc_get(&t);
u8 hour    = BCD_TO_BIN(t.hour);    /* 0x14 -> 14 */
u8 minutes = BCD_TO_BIN(t.minute);  /* 0x30 -> 30 */
```

---

## 7. Watchdog

The console resets the CPU if the watchdog is not fed for ~100ms.
VBlank (60 Hz) feeds it automatically in the typical system layer.

```c
#define HW_WATCHDOG    (*(volatile u8 *)0x006F)
#define WATCHDOG_CLEAR 0x4E

/* Call manually before any long operation (flash erase/write) */
HW_WATCHDOG = WATCHDOG_CLEAR;
```

> See also [Hardware Registers — Watchdog address note](Hardware-Registers.md#31-watchdog-address-note)
> for the `0x006B` vs `0x006F` discrepancy observed across titles.

---

## 8. User Interrupt Vectors

Vectors are 32-bit pointers stored in RAM at `0x6FB8-0x6FFC`.

```c
/* Assign a handler (ngpc_hw.h) */
HW_INT_VBL  = isr_vblank;   /* MANDATORY */
HW_INT_TIM0 = isr_hblank;   /* HBlank (for raster / sprmux) */
HW_INT_DMA0 = isr_dma_done; /* DMA channel 0 completion */
```

See the full interrupt vector table in [Hardware Registers — Interrupts](Hardware-Registers.md#4-interrupts).

---

## 9. System Library (system.lib)

All system.lib functions use **bank 3** registers (same convention as BIOS calls).
`system.lib` is distinct from the raw BIOS table — it wraps BIOS calls and provides
additional services not available via direct BIOS vectors.

> **Note:** `WRITE_FLASH_RAM` and `CLR_FLASH_RAM` are not strictly required.
> Standalone AMD flash stubs (`ngpc_flash_asm.asm`) can replicate
> the internal mechanism of both functions — extracted by disassembly and validated on real
> hardware. `system.lib` is kept as an optional legacy path.
> See [Storage and Saves](../05_Systems/Storage-and-Saves.md) for details.

| Function | Parameters | Return | Notes |
|----------|-----------|--------|-------|
| `SYSTEM_CALL` | RW3=vector, params in bank 3 | depends | BIOS gateway, non-blocking on IRQ |
| `sys_PATCH` | — | — | Applies system bug fix — **call at startup before anything else** |
| `WRITE_FLASH_RAM` | same as VECT_FLASHWRITE | RA3 | Legacy — replaced by a standalone write stub |
| `CLR_FLASH_RAM` | RA3=cs(0/1), RB3=block | RA3 | Legacy — **only safe erase for blocks 32-34** (VECT_FLASHERS broken); replaced by a standalone erase stub |
| `INT_LV_set` | RB3=level(0-5 or _INT_CLR_BIT), RC3=irq# | — | Set IRQ level + optionally clear pending |
| `INT_LV_CLR` | — | — | Reset all IRQ levels to defaults |
| `WAVE_OUT` | RA3=timer(0-3), XHL=data address | — | PCM playback, 8 kHz, 8-bit mono — requires 6.144 MHz |
| `LEVER_GET` | — | — | Read joypad (dev/debug only) |
| `FLASH_M_READ` | RA3=cs | RA3 | Detect flash capacity (debug) |

**Critical notes:**
- `sys_PATCH` must be the **first call** after startup — fixes an internal BIOS bug.
- `CLR_FLASH_RAM` on blocks 32-34: works correctly on first call per session;
  calling it a second time in the same session silently fails (BIOS internal bug).
  Avoid this entirely via append-only slots and standalone AMD stubs.
  See [Storage and Saves](../05_Systems/Storage-and-Saves.md).
- `WAVE_OUT` requires CPU at 6.144 MHz (BIOS_CLOCKGEARSET RB3=0x00) — PCM will
  be garbled at lower speeds.
- `INT_LV_CLR` flag `_INT_CLR_BIT` in RB3 causes INT_LV_set to also clear the pending
  IRQ flag for that channel.

---

## Quick Reference

| Item | Value | Notes |
|------|-------|-------|
| BIOS vector table | `0xFFFE00` | 32-bit pointers, index * 4 |
| SHUTDOWN | vector 0 | SWI 1, ra3=3 |
| CLOCKGEARSET | vector 1 | Table call, rb3=divisor |
| RTCGET | vector 2 | Table call, xhl3=NgpcTime* |
| SYSFONTSET | vector 5 | SWI 1, ra3=3 |
| FLASHWRITE | vector 6 | SWI 1 |
| FLASHERS | vector 8 | SWI 1, ra3=0, rb3=block |
| System font tile base | slot 32 | After SYSFONTSET call |
| Tile slots 128+ | free | User tiles |
| Watchdog address | `0x006F` | Write `0x4E` |
| VBlank vector | `0x6FCC` | 32-bit ptr, mandatory |
| Timer0 vector | `0x6FD4` | 32-bit ptr, HBlank |
| RTC values | BCD format | Use BCD_TO_BIN() macro |

---

## See Also

- [Hardware Registers](Hardware-Registers.md) — Full hardware register map
- [Storage and Saves](../05_Systems/Storage-and-Saves.md) — Flash save complete strategy
- [Game Loop](../05_Systems/Game-Loop.md) — VBlank handler, watchdog integration
- [Build Toolchain](../02_CPU-and-Toolchain/Build-Toolchain.md) — `ngpc_hw.h`, compiler setup
