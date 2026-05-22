# Memory Read v1

Purpose:
- define the read-only memory access path used by the bootstrap tooling
  and the executor
- support byte peeks from the loaded ROM image, the on-chip RAM/VRAM
  cold-start image, and the runtime writable overlay

Current source references:
- `../../01_SDK/docs/ngpcspec.txt`
- `../../01_SDK/docs/NGPC_SDK_MASTER_ENTRY.md`
- `ADDRESS_SPACE.md`
- `../NgpCraft_toolchain/StarGunner_save_lib_test/README.md`
- `../NgpCraft_toolchain/StarGunner_save_lib_test/src/core/ngpc_flash.c`

## 1. Read path

A read at address `A` of size `N` is resolved in order:

1. **Runtime writable overlay** (passed by the executor):
   per-byte if the address has been written during this session.
2. **Read bus** (`core/memory.py::NgpcReadBus.read_bytes`):
   - cartridge ROM image (loaded `.ngc` file) → byte from file offset
   - unloaded cartridge flash window (`0x200000..0x3FFFFF` beyond the
     loaded file size) → erased `0xFF`
   - built-in readable cold-start image (see §2)
   - otherwise `unbacked` / `unmapped` / `out-of-file`

The overlay never invalidates the bus: writing then reading the same
address returns the overlay byte, and unmodified neighbouring addresses
in the same region keep returning their cold-start value.

## 2. Built-in readable cold-start image

`_build_builtin_readable_bytes()` pre-populates the following on-chip
regions with their power-on default `0x00`, matching real NGPC silicon
behaviour at cold reset:

- `0x004000..0x006BFF` — Work RAM (10 752 bytes)
- `0x006C00..0x006FFF` — system RAM page (including system-reserved
  slices and user vector area)
- `0x007000..0x007FFF` — shared Z80 RAM
- `0x008000..0x008FFF` — K2GE registers and palette RAM
- `0x009000..0x0097FF` — SCR1 map
- `0x009800..0x009FFF` — SCR2 map
- `0x00A000..0x00BFFF` — character RAM

One non-zero override:

- `0x006F91` — HW_SYSTEM_MODE, set from the ROM header `mode_raw` byte
  (OS version / color-mono selector). The BIOS reads this at power-on
  and games branch on it.

Deliberately **not** pre-populated:

- `0x000000..0x0000FF` — CPU I/O page. Timers, DMA channels, interrupt
  controller registers, etc. have subsystem-specific reset values that
  the emulator does not yet model. Reads return `unbacked`.

## 3. Write side

Writes do not go through this read path. They are handled by the
executor's runtime overlay (`memory_writes` field of `ExecutionResult`),
with `_check_writable_range` filtering out ROM, BIOS and unmapped
targets per `EXECUTE.md`. Writes to writable regions accumulate in the
overlay, which then shadows the cold-start image on subsequent reads.

## 4. Result statuses

- `ok` — bytes were resolved
- `mapped` — used at the probe layer (`AddressProbe.status`), not at
  the read layer
- `unmapped` — no region contains the address
- `unbacked` — region exists but is not yet backed (e.g. CPU I/O page,
  BIOS ROM with no image loaded)
- `out-of-file` — region is `CART_ROM_LOADED` but the computed file
  offset is past EOF (defensive check; should not happen in practice)

## 5. CLI

- `python ngpc_emu.py peek <rom> <address>`
- `python ngpc_emu.py peek <rom> <address> --count N`
- `python ngpc_emu.py memory-dump <rom> <address> [--count N] [--width W] [--seed-from state.json] [--json]`
  — hexdump-style multi-row inspector. Reads through the same read bus
  as `peek` and optionally overlays a savestate's writable cells.

Examples:
- ROM entry point fetch candidate:
  - `python ngpc_emu.py peek game.ngc 0x200040 --count 8`
- title bytes:
  - `python ngpc_emu.py peek game.ngc 0x200024 --count 12`
- cold-start Work RAM (returns 12 × 0x00 since M1d Phase 1):
  - `python ngpc_emu.py peek game.ngc 0x004000 --count 12`
- browse a captured run's writes:
  - `python ngpc_emu.py memory-dump game.ngc 0x004000 --seed-from sg_at_assert_fail.state.json`

## 6. Not implemented yet

- CPU I/O page reset values (timers, DMA, IRQ controller registers)
- BIOS image loading (`0xFF0000..0xFFFFFF` is mapped but unbacked)
- writable cart flash persistence (save block `0x3FA000..0x3FBFFF`
  shadows over the read bus through the overlay but is not persisted
  on disk yet)
- K2GE side-effect reads (the current backing is power-on-default
  zeros only)
- bus timing
