/* ngpc_core.h — flat C ABI for the native NGPC emulation core.
 *
 * This header is the ONLY exported contract. Nothing C++ crosses it: no
 * classes, no exceptions, no std::. Rationale (measured 2026-07-11): CPython
 * here is MSVC-built while the only available compiler is MinGW GCC 13.1, so
 * neither the C++ ABI nor the CPython ABI may cross the boundary. A flat C ABI
 * crosses neither, and loads cleanly under ctypes.
 *
 * Seam granularity: an FFI crossing costs ~292 ns. At 60 calls/s that is
 * nothing; at one call per instruction (~615k/s) it would cost ~17%. So the
 * host drives this core in BATCHES (ngpc_run), never one instruction at a
 * time. Breakpoints therefore live in the core, not in a host-side loop.
 *
 * See specs/CPP_CORE_PORT.md for the chantier plan and the semantic contract.
 */
#ifndef NGPC_CORE_H
#define NGPC_CORE_H

#include <stdint.h>
#include <stddef.h>

#ifdef _WIN32
#  define NGPC_API __declspec(dllexport)
#else
#  define NGPC_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define NGPC_ABI_VERSION 15

/* ---------------------------------------------------------------- status --
 * Execution status of one instruction. The tri-state "requires-known-*"
 * family of the Python core does NOT appear here: the native core is
 * concrete-state (specs/CPP_CORE_PORT.md §2). Everything below is either a
 * normal terminal, a HARDWARE truth, or a COVERAGE GAP that must trap loudly.
 * HARDWARE_COMPAT_POLICY.md §9 forbids a silent fallback.
 */
typedef enum {
    NGPC_OK                 = 0,   /* executed                                  */
    NGPC_HALTED             = 1,   /* cpu-halted (HALT, awaiting an interrupt)  */

    /* --- hardware truths: the real console does this. Reproduce, never hide. */
    NGPC_SILICON_BROKEN     = 10,  /* quirks_db: this encoding breaks silicon   */
    NGPC_SILICON_UNDEFINED  = 11,
    NGPC_DIVISION_BY_ZERO   = 12,
    NGPC_BIOS_SHUTDOWN      = 13,  /* BIOS powered the console off              */
    /* The two hardware-safety findings. Counted, never terminal -- UNLESS the
     * caller arms that kind with ngpc_set_hw_guard. See the safety section. */
    NGPC_SYSTEM_STACK_VIOLATION = 14, /* cart XSP entered 0x6C01..0x6FFF        */
    NGPC_WATCHDOG_RESET     = 15,  /* enabled watchdog expired without 0x4E refresh */

    /* --- decode / bus faults                                                  */
    NGPC_UNKNOWN_OPCODE     = 20,
    NGPC_TRUNCATED          = 21,
    NGPC_UNMAPPED           = 22,

    /* --- coverage gaps: NOT yet ported. Trap with the offending byte + PC.    */
    NGPC_UNIMPLEMENTED      = 30,

    /* --- host-requested stops                                                 */
    NGPC_BREAKPOINT         = 40,
    NGPC_COUNT_REACHED      = 41
} ngpc_status_t;

/* ------------------------------------------------------------------- cpu --
 * Flat POD mirror of NgpcCpuState. Concrete: every field always defined.
 * regs[] order is fixed and matches the Python GeneralRegisters32 order.
 */
enum { NGPC_XWA = 0, NGPC_XBC, NGPC_XDE, NGPC_XHL,
       NGPC_XIX,     NGPC_XIY, NGPC_XIZ, NGPC_XSP, NGPC_NREG };

typedef struct {
    uint32_t regs[NGPC_NREG];  /* currently-banked 32-bit general registers   */
    uint32_t pc;
    uint16_t sr_raw;           /* full SR; flags/iff/rfp are views onto it     */
    uint8_t  flags;            /* F: bit0 C, 1 N, 2 V, 4 H, 6 Z, 7 S           */
    uint8_t  alt_flags;        /* F' (shadow set, swapped by EX F,F')          */
    uint8_t  iff_level;        /* SR[12:14], 0..7                              */
    uint8_t  rfp;              /* SR[8:9], register-file bank 0..3             */
    uint8_t  _pad[2];
    /* Backing store for the 4 banks x 8 registers. The visible window above is
     * bank[rfp]; the core flushes/reloads it on every RFP transition. */
    uint32_t banks[4][NGPC_NREG];

    /* CPU control registers, indexed by the `cr` byte of an LDC instruction:
     *   0x00/04/08/0C  DMAS0..3 (long)      0x10/14/18/1C  DMAD0..3 (long)
     *   0x20/24/28/2C  DMAC0..3 (word)      0x22/26/2A/2E  DMAM0..3 (byte)
     *   0x30           INTNEST  (word)
     * `ldc DMAC0, WA` (D8 2E 20) is what stops 19 of the 66 commercial ROMs:
     * they program the micro-DMA controller during boot. */
    uint32_t cregs[64];
} ngpc_cpu_t;

/* ---------------------------------------------------------------- record --
 * One executed instruction, as the debugger / event-log / watchpoints need it.
 * Memory traffic is reported as DELTAS. The Python core copied the entire
 * memory dict per instruction; that cannot cross a C ABI and is the single
 * biggest cost in the current hot path.
 */
#define NGPC_MAX_RAW      8   /* longest TLCS-900 encoding we decode           */
#define NGPC_MAX_ACCESS   4   /* memory accesses recorded per instruction      */

typedef struct {
    uint32_t address;
    uint8_t  size;            /* 1, 2 or 4 bytes                               */
    uint8_t  discarded;       /* 1 = write landed on ROM/BIOS/unmapped.        */
                              /*     This is what drives the flash model.      */
    uint8_t  _pad[2];
    uint8_t  data[4];
} ngpc_access_t;

typedef struct {
    uint32_t pc;
    uint32_t next_pc;
    uint8_t  raw[NGPC_MAX_RAW];
    uint8_t  raw_len;
    uint8_t  status;          /* ngpc_status_t                                 */
    uint8_t  n_writes;
    uint8_t  n_reads;
    uint16_t cycles;
    uint16_t quirk_id;        /* 0 = none                                      */
    uint32_t written_regs;    /* bitmask over NGPC_NREG (+ flags at bit 31)    */
    ngpc_access_t writes[NGPC_MAX_ACCESS];
    ngpc_access_t reads[NGPC_MAX_ACCESS];
} ngpc_record_t;

typedef struct {
    uint32_t executed;        /* instructions actually retired                 */
    uint32_t emitted;         /* records written to the caller's buffer        */
    uint64_t total_cycles;
    uint32_t irq_deliveries;
    uint8_t  stop_status;     /* ngpc_status_t that ended the batch            */
    uint8_t  _pad[3];
    uint32_t stop_pc;         /* PC of the offending instruction on a trap     */
    uint8_t  stop_opcode;     /* first byte, so a trap names its opcode        */
    uint8_t  _pad2[3];
    /* frame pacing lives in the CORE, not the host (see §4 hazard 4) */
    uint32_t scanline;
    uint32_t frame_count;
    /* These USED to be a second, private scanline counter inside timer_tick,
     * exposed so the differential gate could see its phase drift against the
     * raster. That drift is gone: TI0 now pulses on the raster's own clock (the
     * private counter's arbitrary phase + the 13 delivery cycles it never saw
     * made Metal Slug's raster split flicker one line up and down). The fields
     * stay for ABI shape and now report the shared raster phase itself. */
    uint32_t timer_hblank_cycles;   /* = the raster's sub-line cycle residue */
    uint32_t timer_hblank_line;     /* = the raster's current scanline       */
} ngpc_summary_t;

/* ------------------------------------------------------------- lifecycle -- */
typedef struct ngpc_machine ngpc_t;

NGPC_API uint32_t    ngpc_abi_version(void);
NGPC_API ngpc_t*     ngpc_create(void);
NGPC_API void        ngpc_destroy(ngpc_t*);

NGPC_API int         ngpc_load_rom (ngpc_t*, const uint8_t* data, size_t len);
NGPC_API int         ngpc_load_bios(ngpc_t*, const uint8_t* data, size_t len); /* 65536 */

/* How the machine comes up. This used to be a bool, and the third case was hiding
 * inside it: "no hand-off" ALSO started at the cartridge's entry point, so the BIOS's
 * own boot code had never run in either mode.
 *
 *   0 RAW       PC = cart entry, nothing seeded. The synthetic-ROM / fuzz mode both
 *               cores run in for the differential gate.
 *   1 HANDOFF   PC = cart entry + the state the BIOS boot leaves behind. THE DEFAULT.
 *   2 BIOS BOOT The console POWERING ON: PC = the hardware reset vector, and the real
 *               BIOS runs. Needs a BIOS image. If the battery-backed RAM says the
 *               console has booted before, it goes to VECT_SHUTDOWN instead -- so hand
 *               that RAM over with ngpc_set_battery_ram BEFORE calling this.
 */
#define NGPC_RESET_RAW       0
#define NGPC_RESET_HANDOFF   1
#define NGPC_RESET_BIOS_BOOT 2
NGPC_API void        ngpc_reset(ngpc_t*, int reset_mode);

/* The console's 12 KiB of work RAM is kept alive by a coin cell: that is why the BIOS
 * remembers your language and the date, and why pulling the batteries wipes it. Pass
 * NULL/0 for a dead cell (a blank RAM, and a BIOS that boots as if brand new). */
NGPC_API void        ngpc_set_battery_ram(ngpc_t*, const uint8_t* data, uint32_t len);

/* THE CALENDAR IC, at I/O 0x90-0x97 -- and it runs off THE SAME COIN CELL as the RAM
 * above. That is not a detail: one cell keeps both alive, so a console that remembers
 * your language necessarily remembers the time too, and the two must be saved and
 * restored TOGETHER. The clock is machine state, not memory, so `ngpc_read_mem` cannot
 * reach it and a plain RAM dump silently leaves it behind -- which is exactly how it
 * came to be re-seeded to a hardcoded date at every launch.
 *
 * MEASURED against the retail BIOS (both paths):
 *   - blank cell   -> the BIOS REWRITES the chip to 1998-01-01 00:00:00 at 0xFF20FD
 *                     (stop clock, set fields, restart) -- a dead battery, reset the date.
 *   - configured   -> the BIOS does not touch it. NOT ONE WRITE.
 * So on a configured console whatever we hand over is what the console believes, forever;
 * the BIOS will never correct it. Restoring the real one is the whole fix.
 *
 * All fields are packed BCD, exactly as the registers read. `counter` is the sub-second
 * cycle accumulator -- internal, not visible to software, carried so a round-trip through
 * a save is lossless. Hand the clock over BEFORE `ngpc_reset` in BIOS-boot mode, like the
 * battery RAM: the BIOS reads it during its own boot. */
typedef struct {
    uint8_t  enable;                       /* register 0x90 bit 0 */
    uint8_t  year, month, day;             /* 0x91 0x92 0x93 */
    uint8_t  hour, minute, second;         /* 0x94 0x95 0x96 */
    uint8_t  weekday;                      /* 0x97 bits 0-3 (the leap phase is derived) */
    /* The alarm is coin-cell state too: a real console you set an alarm on still has it
     * set tomorrow. Same chip, same battery, same save. 0x90 bit1 + 0x98/0x99/0x9A. */
    uint8_t  alarm_enable;
    uint8_t  alarm_day, alarm_hour, alarm_minute;
    uint32_t counter;                      /* cycles accumulated toward the next second */
} ngpc_rtc_t;

NGPC_API void        ngpc_get_rtc(ngpc_t*, ngpc_rtc_t* out);
NGPC_API void        ngpc_set_rtc(ngpc_t*, const ngpc_rtc_t* in);

/* Wind the clock forward by whole seconds, through the same BCD carry chain the running
 * clock ticks through -- month ends and leap years included. This is how time the console
 * spent SWITCHED OFF gets caught up: a real coin cell keeps the calendar running while the
 * machine is dark, so a save restored a week later should come back a week later. */
NGPC_API void        ngpc_rtc_advance(ngpc_t*, uint32_t seconds);

/* THE PICTURE. 160 x 152 raw 12-bit 0BGR colours, drawn ONE LINE AT A TIME as the beam
 * passed -- so a game that streams VRAM mid-frame comes out the way the silicon draws it,
 * not smeared with the frame's final state. Copies out; returns the number of pixels. */
#define NGPC_SCREEN_W 160
#define NGPC_SCREEN_H 152
NGPC_API uint32_t    ngpc_get_framebuffer(ngpc_t*, uint16_t* out, uint32_t max_pixels);

/* -------------------------------------------------------------- hot path --
 * Run up to max_instrs. If out_records is NULL (or cap 0) the core runs in
 * FAST mode and records nothing — that is the real-speed path. Stops early on
 * a trap, a halt, or a breakpoint; the reason lands in summary->stop_status.
 */
/* Run until the core's own FRAME COUNTER has advanced by `frames`.
 *
 * The run path needs a frame boundary, and only the core knows where one is: it
 * owns the raster. Asking for a fixed number of INSTRUCTIONS and hoping it lands
 * near a frame edge is how a shell ends up re-implementing the video clock -- the
 * exact hazard §4.4 of CPP_CORE_PORT.md is about.
 *
 * `max_instrs` is a runaway backstop, not a target: a frame is ~102 000 cycles,
 * so a few tens of thousands of instructions. If the core burns through the whole
 * budget without completing the frames, it stops and says COUNT_REACHED. Any trap
 * (an un-ported opcode, a terminal HALT) stops it too, exactly as `ngpc_run` does.
 *
 * The summary reports what happened; `frame_count` is the core's counter, not a
 * delta. ABI v3. */
NGPC_API int ngpc_run_frames(ngpc_t*, uint32_t frames, uint32_t max_instrs,
                             ngpc_summary_t* out_summary);

NGPC_API int ngpc_run(ngpc_t*, uint32_t max_instrs,
                      ngpc_record_t* out_records, uint32_t records_cap,
                      ngpc_summary_t* out_summary);

/* ------------------------------------------------------------------ state */
/* ------------------------------------------------------------------- SAVES --
 * The cartridge IS the save medium: a NOR flash the game erases and programs in
 * place. `ngpc_flash_dirty` is 1 once anything has actually changed -- a front end
 * uses it to know there is a save worth writing. `ngpc_flash_restore` puts bytes back
 * into the cart window, past the read-only check, which is what re-inserting the
 * cartridge does. ABI v11. */
NGPC_API void ngpc_bus_write(ngpc_t*, uint32_t address, uint8_t value);
NGPC_API int  ngpc_flash_dirty(ngpc_t*);
NGPC_API void ngpc_flash_clear_dirty(ngpc_t*);
NGPC_API int  ngpc_flash_restore(ngpc_t*, uint32_t address,
                                 const uint8_t* data, uint32_t len);

NGPC_API void ngpc_get_cpu(ngpc_t*, ngpc_cpu_t* out);

/* The SOUND CPU's state -- above all, WHERE IT TRAPPED.
 *
 * A Z80 that NOPed what it did not recognise would still "run" and would hand the
 * main CPU a wrong answer with nothing to say so. It traps instead, and this is
 * how the trap is read back: the work-list of opcodes still to port is MEASURED
 * from the real sound drivers, in the order they actually need them, not guessed
 * from a table. ABI v4. */
typedef struct {
    uint8_t  running;        /* 0 while the main CPU holds it in reset */
    uint8_t  halted;
    uint8_t  trapped;
    uint8_t  trap_prefix;    /* 0, or 0xCB / 0xDD / 0xED / 0xFD */
    uint16_t trap_pc;
    uint8_t  trap_opcode;
    uint8_t  _pad;
    uint16_t pc;
    uint16_t sp;
    uint64_t executed;
    uint64_t port_writes;    /* T6W28 writes, counted until the APU is wired up */
} ngpc_z80_t;

/* --------------------------------------------------------------- the APU --
 * Every write aimed at the T6W28, RECORDED rather than merely counted.
 *
 * `kind` says which door the write came through, because we do not yet know
 * which one the real sound drivers use -- and guessing is how you build a chip
 * that plays plausible noise:
 *     NGPC_APU_WRITE_PORT = the Z80 executed `OUT (n), A`   -> `port` = n
 *     NGPC_APU_WRITE_MEM  = the Z80 wrote 0x4000..0x7FFF    -> `port` = A15..A8
 * `cycle` is the machine cycle the write landed on, which is what a mixer needs
 * to place it in time. */
#define NGPC_APU_WRITE_PORT 0
#define NGPC_APU_WRITE_MEM  1

typedef struct {
    uint64_t cycle;
    uint16_t address;   /* the OUT port, or the full Z80 address for a MEM write */
    uint8_t  value;
    uint8_t  kind;
} ngpc_apu_write_t;

/* Copies up to `n` of the most recent APU writes, oldest first, into `out`.
 * Returns how many were copied. The log is a ring buffer; `ngpc_apu_write_count`
 * reports the TOTAL ever seen so a caller can tell when it has dropped some. */
/* Drains up to `frames` STEREO frames (interleaved L,R, signed 16-bit, 44100 Hz)
 * from the chip's ring buffer. Returns how many were copied.
 * `ngpc_audio_dropped` reports frames the host was too slow to collect: silently
 * overwriting them is how an emulator ends up "sounding fine" while losing a
 * third of its output. */
/* The chip's REGISTER state. Exposed so the clean-room Python model in
 * core/apu.py can be driven with the same byte stream and held against it -- a
 * differential harness proves the two AGREE, which is not the same as proving
 * either is RIGHT, so the pitch check in tests/test_apu_native.py stands beside
 * it as independent evidence. */
typedef struct {
    int32_t square_vol_left[3];
    int32_t square_vol_right[3];
    int32_t square_period[3];
    int32_t noise_vol_left;
    int32_t noise_vol_right;
    int32_t noise_shifter;
    int32_t noise_tap;
    int32_t noise_period_select;
    int32_t noise_period_extra;
    uint8_t latch_left;
    uint8_t latch_right;
    uint8_t _pad[2];
} ngpc_apu_state_t;

/* Assert an interrupt line from OUTSIDE the CPU.
 *
 * The one that matters is INT0 (vector index 8): on the NGPC that line is the
 * POWER circuit. The BIOS's power-on code ends with `ei 5 ; halt` and sleeps
 * there until it fires -- a console that is "off" is a CPU parked on that HALT.
 * Without a way to raise it, the real BIOS can never be booted, and every piece
 * of state its power-on code sets (the user vector table, the K2GE mode
 * register, the compatibility palette) has to be hand-synthesised instead. */
NGPC_API void ngpc_raise_irq(ngpc_t*, uint32_t vector_index);

/* --- link cable (serial channel 0) ----------------------------------------
 * The NGPC link cable is TLCS-900 serial channel 0, driven by the BIOS COM
 * routines. The cable is a byte pipe: a host wires two machines together by
 * draining each one's transmit FIFO and pushing it into the other's receive
 * FIFO (in-process for two-players-on-one-PC, or over a socket for online).
 * Enable is off by default (registers inert, cable unplugged). */
NGPC_API void     ngpc_serial_set_enabled(ngpc_t*, int on);
NGPC_API uint32_t ngpc_serial_read_tx(ngpc_t*, uint8_t* out, uint32_t max);
NGPC_API void     ngpc_serial_write_rx(ngpc_t*, const uint8_t* data, uint32_t n);
NGPC_API int      ngpc_serial_rts(ngpc_t*);
NGPC_API void     ngpc_serial_set_cts(ngpc_t*, int high);

/* A read-only snapshot of the serial channel, for the debugger's Link tab.
 * How many bytes crossed is already visible to the host that relays them; what
 * is NOT visible is why they did not -- a byte held by the peer's CTS, a byte
 * queued while our own RTS is high, a byte presented and never read because the
 * BIOS receive interrupt is masked. These counters name the stage each byte is
 * stuck at. Observation only: reading this changes nothing. */
typedef struct {
    uint32_t enabled;         /* link armed (cable "plugged in")                */
    uint32_t tx_depth;        /* bytes transmitted, waiting for the host to relay */
    uint32_t rx_depth;        /* bytes the host queued, not yet presented        */
    uint32_t tx_busy;         /* a byte is shifting out (or held by CTS)         */
    uint32_t rx_pending;      /* a byte sits in SC0BUF, unread by the CPU        */
    uint32_t cts_high;        /* peer says "not ready" (our CTS0 input)          */
    uint32_t rts_low;         /* WE say "ready to receive" (0xB2 bit0 == 0)      */
    uint32_t ctse;            /* game enabled the CTS gate (SC0MOD bit6)         */
    uint32_t tx_count;        /* bytes the CPU wrote to SC0BUF                   */
    uint32_t wire_count;      /* ...that finished shifting out                   */
    uint32_t rx_queued_count; /* bytes the host pushed at us                     */
    uint32_t rx_read_count;   /* ...that the CPU actually read back              */
    uint32_t irq_tx_count;    /* INTTX0 raised (vector 0x19 on this BIOS)        */
    uint32_t irq_rx_count;    /* INTRX0 raised (vector 0x18 on this BIOS)        */
    uint32_t cts_hold_ticks;  /* ticks a byte was held by CTS0 high              */
    uint32_t rts_hold_ticks;  /* ticks RX was held by our own RTS                */
    uint32_t sc0buf;          /* I/O 0x50..0x53 and the two port bits, as read   */
    uint32_t sc0cr;
    uint32_t sc0mod;
    uint32_t br0cr;
    uint32_t port_b1;         /* bit2 = cable-detect the games poll             */
    uint32_t port_b2;         /* bit0 = RTS                                      */
} ngpc_serial_state_t;

NGPC_API void ngpc_serial_state(ngpc_t*, ngpc_serial_state_t* out);

/* The prescaler's phi-T1 period, in CPU cycles. THE SOURCES CONTRADICT EACH OTHER
 * BY A FACTOR OF 32 and neither yields a musical tempo, so this is a knob, not a
 * constant, until an ear or a capture settles it:
 *
 *   - TMP95C061 datasheet:  phi-T1 = 8/fc, and the CPU runs at fc/2  ->  4 cycles
 *   - SNK SDK (8Bit.txt):   "T1 = 20.83 us" MEASURED on the console  ->  128 cycles
 *     ...but the SDK's own formula then requires fc = 384 kHz, which the CPU is not.
 *
 * 128 is what this core ships (the SDK's measured number). The music comes out too
 * fast, so the truth is larger. Set it and listen. */
NGPC_API void ngpc_set_timer_base(ngpc_t*, uint32_t cycles_per_phi_t1);

NGPC_API void ngpc_get_apu_state(ngpc_t*, ngpc_apu_state_t* out);
/* Debug channel mute mask: bit0..2 squares, bit3 noise, bit4 DAC (0x1F = all on). */
NGPC_API void ngpc_set_apu_channel_mask(ngpc_t*, uint32_t mask);

/* Debug LAYER mask -- the video counterpart of the channel mute above.
 *   bit0 SCR1 · bit1 SCR2 · bit2 sprites PR.C=1 · bit3 PR.C=2 · bit4 PR.C=3
 * 0x1F = everything on, which is the default and the only value any fidelity gate
 * may run under. Clearing a bit removes that layer from the composed picture and
 * changes nothing else -- no machine state, no timing, no savestate content. It is
 * how you answer "which plane is this text on?" without editing VRAM. */
#define NGPC_LAYER_ALL 0x1Fu
NGPC_API void ngpc_set_layer_mask(ngpc_t*, uint32_t mask);
NGPC_API uint32_t ngpc_get_layer_mask(ngpc_t*);
NGPC_API uint32_t ngpc_get_audio(ngpc_t*, int16_t* out, uint32_t frames);
NGPC_API uint64_t ngpc_audio_dropped(ngpc_t*);

NGPC_API uint32_t ngpc_get_apu_writes(ngpc_t*, ngpc_apu_write_t* out, uint32_t n);
NGPC_API uint64_t ngpc_apu_write_count(ngpc_t*);

NGPC_API void ngpc_get_z80(ngpc_t*, ngpc_z80_t* out);
NGPC_API void ngpc_set_cpu(ngpc_t*, const ngpc_cpu_t* in);

/* ------------------------------------------------ THE REST OF THE MACHINE --
 * Machine state a SAVESTATE needs that is NOT in the flat memory image. ABI v15.
 *
 * ⛔ WHY THIS EXISTS. A snapshot used to be "the main CPU struct + memory", and the
 * sound died on every load -- reported on SNK vs. Capcom, seen on several games,
 * and it came back only when the game happened to send its driver a fresh command
 * (a scene change). Both halves of that are explained by what the snapshot MISSED:
 *
 *   - the SOUND CPU's registers. Its RAM is in the memory image (0x7000), its PC
 *     and its pointers are not. Restoring one without the other lands a Z80 that is
 *     halfway through some other song's playback loop on top of a driver state from
 *     the past: it walks off into whatever the old pointers now mean. The main CPU
 *     believes it already asked for that music, so it never asks again -- until the
 *     game changes scene and issues a new command, which is exactly the escape the
 *     report describes.
 *   - the T6W28's own registers (tone periods, volumes, latches, the DAC hold).
 *   - the timer up-counters. Timer 3's output IS the sound CPU's interrupt line;
 *     it is chip state, not memory, so it is invisible to a memory snapshot.
 *
 * ⚠️ ORDER MATTERS ON RESTORE: write the memory image FIRST, then this. Writing the
 * image replays the edge-triggered control registers (0x00BA is a "fire one NMI"
 * door, not storage), so a restore injects a spurious NMI at the sound CPU; putting
 * this state back afterwards is what overrules it.
 *
 * NOT here, on purpose: the audio output ring (host-facing, and stale samples are
 * exactly what must not be replayed), the debug channel/layer masks (UI settings,
 * never machine state), and the cartridge flash (a save, not a snapshot).
 *
 * `version`/`size` are written by the getter and CHECKED by the setter: a blob from
 * a different build is REFUSED (-1), never half-applied. */
#define NGPC_AUX_STATE_VERSION 1

typedef struct {
    uint32_t version;             /* NGPC_AUX_STATE_VERSION            */
    uint32_t size;                /* sizeof(ngpc_aux_state_t)          */

    /* --- the sound CPU (z80.hpp) */
    uint8_t  z80_a,  z80_f,  z80_b,  z80_c,  z80_d,  z80_e,  z80_h,  z80_l;
    uint8_t  z80_a2, z80_f2, z80_b2, z80_c2, z80_d2, z80_e2, z80_h2, z80_l2;  /* shadow */
    uint16_t z80_ix, z80_iy, z80_sp, z80_pc;
    uint8_t  z80_i, z80_r, z80_im, z80_iff1;
    uint8_t  z80_iff2, z80_halted, z80_running, z80_nmi_pending;
    uint8_t  z80_int_pending, z80_trapped, z80_trap_prefix, z80_trap_opcode;
    uint16_t z80_trap_pc;
    uint8_t  z80_int_ack;
    uint8_t  _pad0;
    int32_t  z80_cycle_credit;    /* SIGNED: cycles owed, never forgiven */
    uint64_t z80_executed;

    /* --- the T6W28's registers (apu.hpp) */
    int32_t  square_vol_left[3];
    int32_t  square_vol_right[3];
    int32_t  square_period[3];
    int32_t  square_phase[3];
    int32_t  square_counter[3];
    int32_t  noise_vol_left;
    int32_t  noise_vol_right;
    int32_t  noise_shifter;
    int32_t  noise_tap;
    int32_t  noise_period_select;
    int32_t  noise_period_extra;
    int32_t  noise_counter;
    uint8_t  latch_left, latch_right, dac_left, dac_right;
    uint32_t apu_main_residue;
    uint32_t apu_step_fp;
    uint32_t _pad1;
    uint64_t apu_chip_residue;

    /* --- the timers that pace the sound CPU, and the pending-interrupt mask */
    uint32_t timer_count[4];
    uint32_t timer_clock[4];
    uint32_t to3_half_periods;    /* TO3 is a flip-flop: parity is state  */
    uint32_t ti0_pending_pulses;
    uint64_t irq_pending;
    uint32_t scanline;
    uint32_t frame_count;
    uint32_t cycle_residue;
    uint32_t _pad2;
} ngpc_aux_state_t;

NGPC_API void ngpc_get_aux_state(ngpc_t*, ngpc_aux_state_t* out);
/* Returns 0 on success, -1 if the blob's version/size do not match this build. */
NGPC_API int  ngpc_set_aux_state(ngpc_t*, const ngpc_aux_state_t* in);

NGPC_API int  ngpc_read_mem (ngpc_t*, uint32_t addr, uint8_t* out, uint32_t n);
NGPC_API int  ngpc_write_mem(ngpc_t*, uint32_t addr, const uint8_t* in, uint32_t n);

/* ----------------------------------------------------------- raster log --
 * The K2GE display registers (0x8000..0x803F) as they stood at the START of each
 * of the 152 visible scanlines. ABI v10.
 *
 * A frame is not drawn from one set of registers. Games rewrite the scroll
 * registers while the beam runs -- Sonic's parallax is the micro-DMA writing
 * S2SO.H (0x8034) on every H-blank from a table (pass 206, DMAD0 decoded). A
 * renderer that samples the registers once a frame draws such a game with a
 * single arbitrary offset, and both planes then carry the same offset down the
 * whole screen -- exactly what we measured on Sonic.
 *
 * `out` receives NGPC_RASTER_LINES * NGPC_RASTER_REGS bytes, row-major by line.
 * Returns the number of bytes written, or -1 on a short buffer. */
#define NGPC_RASTER_LINES 152
#define NGPC_RASTER_REGS  0x40
#define NGPC_RASTER_BASE  0x008000
NGPC_API int ngpc_get_raster_log(ngpc_t*, uint8_t* out, uint32_t n);

/* ------------------------------------------------------------- write log --
 * Who wrote to this address, and from what code? ABI v10.
 *
 * The core had breakpoints on PC and nothing on memory, so "which routine fills this
 * tilemap, and why does it stop" could only be guessed at. Arm a window, run, read
 * back every write that landed inside it. `ngpc_write_log_count` is the TRUE total,
 * so a caller can always tell the ring dropped some rather than trust a partial
 * history. Pass lo > hi to disarm. */
typedef struct {
    uint32_t pc;      /* the PC the core held as the write went through */
    uint32_t addr;
    uint8_t  value;
} ngpc_write_t;

NGPC_API void     ngpc_set_write_log(ngpc_t*, uint32_t lo, uint32_t hi);
NGPC_API uint64_t ngpc_write_log_count(ngpc_t*);
/* Copies up to `n` of the MOST RECENT records, oldest first. Returns how many. */
NGPC_API uint32_t ngpc_get_write_log(ngpc_t*, ngpc_write_t* out, uint32_t n);

/* -------------------------------------------------------------- read log --
 * Who READ this address? ABI v11. The write log's missing half: a debugger that
 * only watches writes can see what sets a flag but never what acts on it.
 *
 * Same shape and same rules as the write log. ONE difference, and it matters:
 * instruction fetches are NOT recorded. They all go through the same read path, so
 * logging them would drown the one data read you are hunting -- and arming a window
 * over ROM would log every instruction in it. Only reads from outside the current
 * fetch window are logged. Pass lo > hi to disarm. */
typedef struct {
    uint32_t pc;      /* the PC the core held as the read went through */
    uint32_t addr;
    uint8_t  value;   /* the byte handed back */
} ngpc_read_t;

NGPC_API void     ngpc_set_read_log(ngpc_t*, uint32_t lo, uint32_t hi);
NGPC_API uint64_t ngpc_read_log_count(ngpc_t*);
/* Copies up to `n` of the MOST RECENT records, oldest first. Returns how many. */
NGPC_API uint32_t ngpc_get_read_log(ngpc_t*, ngpc_read_t* out, uint32_t n);

/* ------------------------------------------------------------ call stack --
 * "How did I get here?" ABI v12.
 *
 * A shadow stack maintained per instruction: a CALL is recognised by SP falling
 * with a return address landing on top, a RET by SP climbing back past a frame's
 * entry. Exact, unlike walking the real stack afterwards -- the T900 keeps no
 * frame pointer, so a stack word that looks like a code address is indistinguish-
 * able from an actual return address once the moment has passed.
 *
 * Off by default; enable only while a debugger is attached. Frame 0 is the
 * OUTERMOST caller, so the innermost is at index (depth - 1). */
typedef struct {
    uint32_t caller_pc;   /* address of the CALL instruction */
    uint32_t entry_pc;    /* the routine it entered */
    uint32_t return_pc;   /* where it will return to */
    uint32_t entry_sp;    /* SP before the call pushed anything */
} ngpc_frame_t;

/* ------------------------------------------------------------ event log --
 * WHEN in the frame did that happen? ABI v12.
 *
 * The write log says a register changed and who changed it; it cannot say at which
 * SCANLINE. For raster work -- a mid-frame scroll split, an HBlank HUD, a palette
 * swap on a given line -- the timing IS the behaviour, and it was invisible.
 *
 * Every event carries its exact raster position, so a debugger can plot a frame as
 * a scanline x cycle grid. Armed over an address window (typically the video
 * registers at 0x8000..0x83FF); interrupt deliveries are logged whenever the window
 * is armed at all, with `addr` holding the vector index. Pass lo > hi to disarm. */
#define NGPC_EVENT_WRITE 0
#define NGPC_EVENT_IRQ   1

typedef struct {
    uint32_t pc;
    uint32_t addr;      /* the address written, or the vector index for an IRQ */
    uint16_t scanline;
    uint16_t cycle;     /* cycles elapsed into that scanline (0..514) */
    uint8_t  value;
    uint8_t  type;      /* NGPC_EVENT_* */
} ngpc_event_t;

NGPC_API void     ngpc_set_event_log(ngpc_t*, uint32_t lo, uint32_t hi);
NGPC_API uint64_t ngpc_event_log_count(ngpc_t*);
NGPC_API uint32_t ngpc_get_event_log(ngpc_t*, ngpc_event_t* out, uint32_t n);

/* -------------------------------------------------------------- hygiene --
 * What a ROM does that hardware tolerates but that is almost always a bug. ABI v13.
 *
 * The core models the machine closely enough to JUDGE a cartridge, not just run it.
 * Two findings need its cooperation:
 *
 *   UNINITIALISED READS -- work RAM comes up holding whatever the previous game
 *   left. A variable that is read before it is ever written is reading noise: fine
 *   on a developer's emulator with zeroed RAM, wrong on a console that has been
 *   playing something else.
 *
 *   LOST WRITES -- a store to unmapped space is discarded by the bus and the program
 *   never learns. (Cart-window writes are NOT counted: those are flash commands.)
 *
 * Off by default. Enabling resets both. */
typedef struct {
    uint32_t pc;      /* the code that did it */
    uint32_t addr;
} ngpc_hygiene_t;

/* ------------------------------------------------------------- coverage --
 * How much of the cartridge actually executed. ABI v13.
 *
 * One bit per byte of the 0x200000..0x3FFFFF window, set at the address of every
 * instruction retired. Turns "the analyzer looked at this ROM" from an unfalsifiable
 * claim into a number -- and makes it possible to tell whether driving the input
 * during an analysis reaches more code or merely takes longer. */
NGPC_API void     ngpc_set_coverage(ngpc_t*, int enabled);
NGPC_API uint32_t ngpc_coverage_hits(ngpc_t*);      /* distinct addresses executed */
/* Copies the raw bitmap (kCovSpan/8 bytes). Pass n=0 to query the size. */
NGPC_API uint32_t ngpc_get_coverage(ngpc_t*, uint8_t* out, uint32_t n);

NGPC_API void     ngpc_set_hygiene(ngpc_t*, int enabled);
NGPC_API uint64_t ngpc_uninit_reads(ngpc_t*);
NGPC_API uint64_t ngpc_lost_writes(ngpc_t*);
/* Up to `n` distinct early samples of each, so a report can name the code. */
NGPC_API uint32_t ngpc_get_uninit_reads(ngpc_t*, ngpc_hygiene_t* out, uint32_t n);
NGPC_API uint32_t ngpc_get_lost_writes(ngpc_t*, ngpc_hygiene_t* out, uint32_t n);

/* ------------------------------------------------------ hardware safety --
 * Two things a real Neo Geo Pocket minds and the running code cannot see. ABI v14.
 *
 *   SYSTEM STACK -- SysPro.txt gives the cartridge 0x4000..0x6BFF. XSP may EQUAL
 *   0x6C00 (the stack descends before it writes), but a cart that leaves it above
 *   that puts its next push or call into the BIOS's own page. Nothing complains;
 *   the console restarts or powers off later, somewhere else entirely.
 *
 *   WATCHDOG -- with the counter armed, software owes I/O 0x006F the clear code
 *   0x4E (SysPro; ngpcspec.txt asks for at least every 100 ms). The retail BIOS
 *   hands the console over with WDMOD=0xF0, i.e. ARMED, so this is the cart's
 *   duty from instruction one. Starve it and the console resets itself.
 *
 * ⚡ COUNTED, NOT ENFORCED. Neither halts a real console where it happens, so
 * neither halts this one: they increment, keep their first samples, and the ROM
 * runs on. This is a diagnostic, in the shape of the hygiene counters above --
 * point it at a build and ask what it did, rather than have the emulator decide
 * the build is dead. `ngpc_set_hw_guard` is the opt-in gate for the cases that
 * DO want a verdict: pass a mask of NGPC_HW_* kinds, and a run that commits one
 * ends with NGPC_SYSTEM_STACK_VIOLATION / NGPC_WATCHDOG_RESET at the offending
 * PC. Both are on for reporting always; only stopping is a choice.
 *
 * ⚠️ The watchdog PERIOD is an assumption: one CPU second at 6.144 MHz, as ares
 * models it. WDMOD's prescaler bits are not decoded, and the SDK's 100 ms is the
 * refresh rate asked of the program, not the counter's period. A wrong period
 * moves WHEN a starved watchdog is reported, never whether the ROM runs. */
#define NGPC_HW_WATCHDOG     0x1u
#define NGPC_HW_SYSTEM_STACK 0x2u

typedef struct {
    uint32_t pc;      /* the instruction that committed it                     */
    uint32_t detail;  /* stack: the XSP value. watchdog: cycles it was armed.  */
    uint64_t cycle;   /* machine cycle count when it happened                  */
    uint32_t kind;    /* NGPC_HW_*                                             */
    uint32_t _pad;
} ngpc_violation_t;

/* Which kinds should STOP a run. 0 (the default) = pure diagnostic. */
NGPC_API void     ngpc_set_hw_guard(ngpc_t*, uint32_t stop_mask);
NGPC_API uint64_t ngpc_hw_violations(ngpc_t*, uint32_t kind);
/* Up to `n` earliest samples, oldest first, both kinds interleaved in time. */
NGPC_API uint32_t ngpc_get_hw_violations(ngpc_t*, ngpc_violation_t* out, uint32_t n);

NGPC_API void     ngpc_set_callstack(ngpc_t*, int enabled);
NGPC_API uint32_t ngpc_callstack_depth(ngpc_t*);
/* Frames dropped because the shadow stack was full -- non-zero means the view is
 * truncated, not wrong. */
NGPC_API uint64_t ngpc_callstack_overflow(ngpc_t*);
NGPC_API uint32_t ngpc_get_callstack(ngpc_t*, ngpc_frame_t* out, uint32_t n);

/* ------------------------------------------------------------- debugging -- */
NGPC_API int ngpc_set_breakpoints(ngpc_t*, const uint32_t* pcs, uint32_t n);

#ifdef __cplusplus
}
#endif
#endif /* NGPC_CORE_H */
