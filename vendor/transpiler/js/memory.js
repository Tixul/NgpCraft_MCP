// Emulated NGPC address space.
//
// Doc refs (HW_REGISTERS.md §1 + K2GETechRef §2 Memory Map):
//   0x000000-0x0000FF  Internal I/O registers (CPU, timers, DMA, watchdog)
//   0x004000-0x005FFF  Main RAM (8 KB)
//   0x006000-0x006BFF  Battery-backed RAM (3 KB)
//   0x006F80-0x006FFF  BIOS system zone (variables + ISR vectors)
//   0x007000-0x007FFF  Z80 RAM (4 KB, shared with sound CPU)
//   0x008000-0x0087FF  K2GE video registers
//   0x008800-0x008BFF  Sprite VRAM (64 sprites x 4 bytes)
//   0x008C00-0x008C3F  Sprite palette indices (64 bytes)
//   0x009000-0x0097FF  Scroll plane 1 tilemap (32x32 u16 = 2 KB)
//   0x009800-0x009FFF  Scroll plane 2 tilemap (32x32 u16 = 2 KB)
//   0x00A000-0x00BFFF  Character RAM (512 tiles x 16 bytes = 8 KB)
//   0x200000-0x3FFFFF  Cartridge ROM (up to 2 MB)
//   0xFF0000-0xFFFFFF  Internal BIOS ROM (64 KB)
//
// We allocate only the regions user code routinely touches for graphics.
// Other accesses fall into a 64 KB catch-all scratch buffer — avoids crashing
// the interpreter on stray pointers, at the cost of not flagging bad writes.

const NGPC_Memory = (() => {
  // Work + BIOS zone + Z80 RAM as a single contiguous 16 KB slab.
  // Real hw has sub-regions but we don't emulate Z80/shutdown/save features
  // that depend on the distinction.
  const WORK_BASE = 0x4000, WORK_SIZE = 0x4000;  // 0x4000-0x7FFF
  const VDP_BASE  = 0x8000, VDP_SIZE  = 0x1000;  // 0x8000-0x8FFF
  const SCR_BASE  = 0x9000, SCR_SIZE  = 0x1000;  // 0x9000-0x9FFF
  const TILE_BASE = 0xA000, TILE_SIZE = 0x2000;  // 0xA000-0xBFFF

  const work = new Uint8Array(WORK_SIZE);
  const vdp  = new Uint8Array(VDP_SIZE);
  const scr  = new Uint8Array(SCR_SIZE);
  const tile = new Uint8Array(TILE_SIZE);
  const scratch = new Uint8Array(0x10000);

  // ---- Budget tracking (for CPU / watchdog simulation) -----------------
  // NGPC runs at 6.144 MHz with ~100 000 cycles visible per frame. Each
  // memory-bus access costs roughly 1-2 cycles; we approximate at 1 and
  // count every read+write the transpiled user code does. The host loop
  // resets `opsThisFrame` each frame and warns when the counter exceeds
  // the frame budget — same failure mode real hw hits when the main loop
  // spills past VBlank (missed sync, watchdog reset).
  // Frame budget: ~100 000 cycles visible per frame on real NGPC (6.144 MHz
  // minus VBlank overhead, HW_REGISTERS.md §7). Surfaced as a setter so the
  // host UI (or user code via an exposed global) can relax the bar for
  // larger projects — keep a 3×/10× toggle in mind rather than unbounded.
  let FRAME_BUDGET = 100000;
  let RUNAWAY_LIMIT = FRAME_BUDGET * 10;
  function setFrameBudget(n) {
    FRAME_BUDGET = Math.max(10000, n | 0);
    RUNAWAY_LIMIT = FRAME_BUDGET * 10;
  }
  const stats = {
    opsThisFrame: 0,
    opsSinceYield: 0,
    totalOps: 0,
    watchdogLastPet: 0,   // frames ago
    frameCounter: 0,
  };
  // `hostMode` disables the CPU-budget counter so host-side work that would
  // not consume TLCS-900/H cycles on real hardware (VDP rendering, the
  // simulated VBI handler) isn't charged to the user's budget. On real hw
  // the K2GE fetches VRAM in parallel with the CPU; only code the user
  // wrote is metered.
  let hostMode = false;
  // `multiByteOp` is set while read16/write16/read32/write32 decompose into
  // byte-level ops so the palette-byte-access check doesn't fire inside them
  // (u16 writes are the *correct* way to touch palette RAM).
  let multiByteOp = false;
  function countOp() {
    if (hostMode) return;
    stats.opsThisFrame++;
    stats.opsSinceYield++;
    stats.totalOps++;
    if (stats.opsSinceYield > RUNAWAY_LIMIT) {
      throw new Error(
        `Runaway loop: ${stats.opsSinceYield} memory ops without ngpc_vsync(). ` +
        `Did you forget to sync at 60 Hz? Real NGPC hardware would have missed ` +
        `VBlank and watchdog-reset long ago.`
      );
    }
  }
  function beginHostOps() { hostMode = true; }
  function endHostOps()   { hostMode = false; }

  // Palette-byte-access warning. Palette RAM at 0x8200..0x83FF is 16-bit-
  // access only per HW_REGISTERS.md §6 and K2GE §2 ("Palette RAM: 312 bytes,
  // 16-bit access ONLY — byte access = undefined"). Real hw silently
  // corrupts the adjacent byte. We log the first occurrence per run so
  // the student sees a clear warning, then silence further warnings to
  // avoid log spam.
  let warnedPaletteByte = false;
  let paletteWarnSink = null;  // host-provided warn callback
  function setPaletteWarnSink(fn) { paletteWarnSink = fn; }
  function checkPaletteByteAccess(addr, rw) {
    if (hostMode || warnedPaletteByte || multiByteOp) return;
    if (addr >= 0x8200 && addr < 0x8400) {
      warnedPaletteByte = true;
      const msg =
        `Palette RAM byte ${rw} at 0x${addr.toString(16).toUpperCase()}: ` +
        `palette registers are 16-bit-access only on real hardware ` +
        `(K2GE §2, HW_REGISTERS.md §6). Use u16 writes or the ` +
        `ngpc_gfx_set_palette helper.`;
      if (paletteWarnSink) paletteWarnSink(msg);
      else console.warn(msg);
    }
  }

  // General hardware-fidelity warning sink. Distinct from the palette-byte
  // sink so the host can route either all HW violations to the same log or
  // split them. `warnOnce` dedupes so a misbehaving loop doesn't flood the
  // console; key it by the message prefix the caller supplies.
  let hwWarnSink = null;
  const hwWarnedKeys = new Set();
  function setHwWarnSink(fn) { hwWarnSink = fn; }
  function warnOnce(key, msg) {
    if (hostMode) return;
    if (hwWarnedKeys.has(key)) return;
    hwWarnedKeys.add(key);
    if (hwWarnSink) hwWarnSink(msg);
    else console.warn(msg);
  }

  // Read-only K2GE registers — writing these on real hardware is either
  // ignored or documented as "do not modify" (K2GETechRef §4-7/§4-9/§4-10
  // and HW_REGISTERS.md §5.1). We flag the first write per register so a
  // bring-up bug (clobbering HW_STATUS while meaning to write to an
  // adjacent byte, for example) is visible rather than silent.
  //
  // Format: addr -> reason string.
  const READ_ONLY_REGS = new Map([
    [0x8006, 'HW_FRAME_RATE — do not modify (K2GETechRef §4-7, stays 0xC6)'],
    [0x8008, 'HW_RAS_H — raster position, read-only (HW_REGISTERS.md §5.1)'],
    [0x8009, 'HW_RAS_V — raster line, read-only (HW_REGISTERS.md §5.1)'],
    [0x8010, 'HW_STATUS — CHAR_OVR/BLNK, read-only (K2GETechRef §4-10)'],
    [0x87E2, 'HW_GE_MODE — do not modify (K2GETechRef, leaves K2GE color mode)'],
  ]);

  // DMA registers — TLCS-900/H DMA channels live in the 0x0080..0x009F I/O
  // range plus the trigger vectors at 0x007C..0x007F. The live editor does
  // NOT emulate DMA channels; writes here are silently absorbed by the I/O
  // overlay. Flag the first write per address so the user knows their DMA
  // setup compiles + executes in-editor but will behave differently on real
  // hardware (where the actual transfer fires). See bugs_silicon.json
  // ENCODE_LDC_ZZ_VALUES + the t900as.py DMA fixes for context.
  const DMA_RANGES = [
    [0x0030, 0x004F, 'DMA channel registers (DMACx/DMASx/DMADx/DMACTRL)'],
    [0x007C, 0x007F, 'DMA interrupt vector registers (HW_DMA0V..HW_DMA3V)'],
    [0x0080, 0x009F, 'DMA configuration / extended channel registers'],
  ];
  function checkDmaWrite(addr) {
    if (hostMode) return;
    for (const [lo, hi, label] of DMA_RANGES) {
      if (addr >= lo && addr <= hi) {
        warnOnce(`dma:${addr}`,
          `[HW] DMA register write at 0x${addr.toString(16).padStart(4, '0')} — ` +
          `${label}. The live editor does NOT emulate DMA; this code compiles + ` +
          `runs in-editor but on real hardware the transfer fires (verify your ` +
          `DMA setup against bugs_silicon.json ENCODE_LDC_ZZ_VALUES).`);
        return;
      }
    }
  }

  function checkReadOnlyWrite(addr) {
    if (hostMode) return;
    const reason = READ_ONLY_REGS.get(addr);
    if (reason) {
      warnOnce(`ro:${addr}`,
        `Write to read-only register 0x${addr.toString(16).toUpperCase()} ` +
        `ignored on real hardware: ${reason}`);
    }
    // Cartridge ROM / BIOS ROM: any write is a no-op on hardware but usually
    // indicates a stray pointer bug.
    if ((addr >= 0x200000 && addr < 0x400000) ||
        (addr >= 0xFF0000 && addr <= 0xFFFFFF)) {
      warnOnce(`rom:${addr >>> 16}`,
        `Write to ROM region 0x${addr.toString(16).toUpperCase()} ` +
        `— real NGPC would silently discard. Likely a bad pointer.`);
    }
    // NGPC interrupt vector table lives at 0x6FCC..0x6FFF (HW_REGISTERS.md
    // §4: VBL at 0x6FCC, timers / DMA above). Stray writes into this range
    // are catastrophic on real hardware. 0x6F80..0x6FCB is the broader BIOS
    // variables zone — user code legitimately reads HW_JOYPAD (0x6F82),
    // writes HW_USR_SHUTDOWN (0x6F85), etc., so we don't warn on those.
    if (addr >= 0x6FCC && addr <= 0x6FFF) {
      warnOnce(`isr:${addr}`,
        `Write to interrupt vector 0x${addr.toString(16).toUpperCase()} ` +
        `(HW_REGISTERS.md §4). Only legal during ngpc_init() ISR install.`);
    }
  }

  function regionFor(addr) {
    const a = addr >>> 0;
    if (a >= WORK_BASE && a < WORK_BASE + WORK_SIZE) return [work, a - WORK_BASE];
    if (a >= VDP_BASE  && a < VDP_BASE  + VDP_SIZE)  return [vdp,  a - VDP_BASE];
    if (a >= SCR_BASE  && a < SCR_BASE  + SCR_SIZE)  return [scr,  a - SCR_BASE];
    if (a >= TILE_BASE && a < TILE_BASE + TILE_SIZE) return [tile, a - TILE_BASE];
    return [scratch, a & 0xFFFF];
  }

  function read8(addr) {
    countOp();
    checkPaletteByteAccess(addr | 0, 'read');
    const [buf, off] = regionFor(addr);
    return buf[off];
  }
  function write8(addr, val) {
    countOp();
    // HW_WATCHDOG is petted by writing 0x4E (HW_REGISTERS.md §2). The
    // template's VBI pets every frame; real hw resets within ~100 ms if
    // the pet is missed, so the host watchdog panel checks this counter
    // against a tight budget — not the generous 1.5 s the first version
    // used.
    if (addr === 0x006F && (val & 0xFF) === 0x4E) stats.watchdogLastPet = 0;
    checkPaletteByteAccess(addr | 0, 'write');
    checkReadOnlyWrite(addr | 0);
    checkDmaWrite(addr | 0);
    const [buf, off] = regionFor(addr);
    buf[off] = val & 0xFF;
  }

  // ---- K2GE status flags (HW_STATUS @ 0x8010) -----------------------------
  // bit 7 = CHAR_OVR, bit 6 = BLNK (V-Blank). Both are read-only to user code
  // but written by the VDP / VBI model. Routed through the raw memory array
  // (bypassing write8's guards) because the register IS read-only from the
  // CPU's perspective — host code legitimately mutates it to reflect hw.
  function setStatusBit(mask, on) {
    const [buf, off] = regionFor(0x8010);
    buf[off] = on ? (buf[off] | mask) : (buf[off] & ~mask);
  }
  function setBlankFlag(on)   { setStatusBit(0x40, on); }
  function setCharOver(on)    { setStatusBit(0x80, on); }
  function clearCharOver()    { setStatusBit(0x80, false); }
  // TLCS-900/H is little-endian (HW_REGISTERS.md §0). The `multiByteOp`
  // guard around each wider op keeps the palette-byte-access warning from
  // firing on u16/u32 writes (which are the *allowed* way to touch palette
  // RAM per K2GE §2).
  function read16(addr) {
    multiByteOp = true;
    const r = read8(addr) | (read8(addr + 1) << 8);
    multiByteOp = false;
    return r;
  }
  function write16(addr, val) {
    multiByteOp = true;
    write8(addr, val & 0xFF);
    write8(addr + 1, (val >>> 8) & 0xFF);
    multiByteOp = false;
  }
  function read32(addr) {
    multiByteOp = true;
    const r = (read16(addr) | (read16(addr + 2) << 16)) >>> 0;
    multiByteOp = false;
    return r;
  }
  function write32(addr, val) {
    multiByteOp = true;
    write16(addr, val & 0xFFFF);
    write16(addr + 2, (val >>> 16) & 0xFFFF);
    multiByteOp = false;
  }

  function reset() {
    work.fill(0);
    vdp.fill(0);
    scr.fill(0);
    tile.fill(0);
    scratch.fill(0);
    stats.opsThisFrame = 0;
    stats.opsSinceYield = 0;
    stats.totalOps = 0;
    stats.watchdogLastPet = 0;
    stats.frameCounter = 0;
    warnedPaletteByte = false;
    hwWarnedKeys.clear();
    shutdownRequested = false;
    powerHoldFrames = 0;
    // Reset-values per K2GETechRef §4-5 (Table 6): window origin = 0x00,
    // window size = 0xFF. A program that forgets to call ngpc_init() would
    // read these reset values; the VDP warns if they look uninitialised.
    vdp[0x0002] = 0x00; vdp[0x0003] = 0x00;
    vdp[0x0004] = 0xFF; vdp[0x0005] = 0xFF;
    vdp[0x0006] = 0xC6;       // HW_FRAME_RATE reset value
    // BIOS variables at 0x6F87 (language) and 0x6F91 (color byte). Scratch
    // catch-all (regionFor returns the scratch slab for addresses outside
    // the known regions). 0 = English / 1 = NGPC Color — matches retail.
    const { buf: langBuf, off: langOff } = regionObj(0x6F87);
    langBuf[langOff] = 0;    // LANG_ENGLISH
    const { buf: colBuf, off: colOff } = regionObj(0x6F91);
    colBuf[colOff]  = 1;     // Neo Geo Pocket Color
  }

  function regionObj(addr) {
    const [buf, off] = regionFor(addr);
    return { buf, off };
  }

  // Host hooks: called by main.js around each generator step.
  function beginFrame() {
    stats.opsThisFrame = 0;
  }
  function endFrame() {
    stats.frameCounter++;
    stats.watchdogLastPet++;
  }
  function resetYieldCounter() { stats.opsSinceYield = 0; }
  function getStats() { return { ...stats, FRAME_BUDGET, RUNAWAY_LIMIT }; }

  // Simulate the template VBI handler + ngpc_vsync post-wait work firing
  // during VBlank. Real hw behaviour (NgpCraft src/core/ngpc_timing.c:18-53):
  //   1. Pet HW_WATCHDOG (ISR does this)
  //   2. Increment g_vb_counter (ISR)
  //   3. Check HW_USR_SHUTDOWN — if set, call ngpc_shutdown (in main context,
  //      after ngpc_vsync's busy-wait finishes)
  //   4. Count PAD_POWER frames held — 30 frames == long press → shutdown
  // These writes don't count against the user CPU budget (equivalent to ISR
  // cycles on real hw which use a separate register bank).
  //
  // `shutdownRequested` is set true when the guard fires so the host can
  // halt the generator cleanly.
  let shutdownRequested = false;
  let powerHoldFrames = 0;
  function simulateVBI() {
    stats.watchdogLastPet = 0;
    // 1: watchdog petted above
    // 2: g_vb_counter increment happens in main.js (has symbolic meaning)
    // 3: USR_SHUTDOWN check
    if (read8(0x6F85) !== 0) shutdownRequested = true;
    // 4: PAD_POWER hold
    const pad = read8(0x6F82);
    if (pad & 0x80) {
      powerHoldFrames++;
      if (powerHoldFrames >= 30) shutdownRequested = true;
    } else {
      powerHoldFrames = 0;
    }
  }
  function consumeShutdown() {
    const s = shutdownRequested;
    shutdownRequested = false;
    return s;
  }

  return {
    read8, write8, read16, write16, read32, write32,
    reset, beginFrame, endFrame, resetYieldCounter, getStats, simulateVBI,
    beginHostOps, endHostOps, setPaletteWarnSink, setHwWarnSink,
    consumeShutdown, setBlankFlag, setCharOver, clearCharOver, warnOnce,
    setFrameBudget,
    regions: { work, vdp, scr, tile },
    get FRAME_BUDGET() { return FRAME_BUDGET; },
    get RUNAWAY_LIMIT() { return RUNAWAY_LIMIT; },
  };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_Memory = NGPC_Memory;
