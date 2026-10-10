/*
 * ngpc_flash.h - Cartridge flash save system (two-bank journal)
 *
 * Part of NgpCraft_base_template (MIT License)
 * Ported from the OVER REV v31 journal (2026-09), made game-independent.
 * Full rationale and failure history: docs/NGPC_FLASH_SAVE_GUIDE.md.
 *
 * ═══════════════════════════════════════════════════════════════════
 * WHERE
 * ═══════════════════════════════════════════════════════════════════
 *
 * Two 8 KB erase blocks at the top of the cartridge, found from the size
 * the BIOS stores at 0x6C58 (1 = 4 Mbit, 2 = 8 Mbit, 3 = 16 Mbit):
 *
 *   cart      bank A (block 33 on 16M)   bank B (block 32 on 16M)
 *   4 Mbit    offset 0x07A000            0x078000
 *   8 Mbit    offset 0x0FA000            0x0F8000
 *   16 Mbit   offset 0x1FA000            0x1F8000
 *
 * Any other size, or a linked image reaching bank B, REFUSES every flash
 * operation (ngpc_flash_ready() == 0). Not saving can be recovered from;
 * erasing the wrong 8 KB cannot. The BIOS block 34 is never touched.
 *
 * ═══════════════════════════════════════════════════════════════════
 * RECORD LAYOUT (SAVE_SIZE bytes, one slot)
 * ═══════════════════════════════════════════════════════════════════
 *
 *   0..3            magic CA FE 20 26                (written by YOU)
 *   4..N-9          your payload: NGPC_FLASH_PAYLOAD - 4 bytes
 *   N-8..N-5        sequence number, u32 LE          (stamped by the driver)
 *   N-4..N-3        CRC16-CCITT (init 0xFFFF) of bytes 0..N-5, LE
 *   N-2             journal tag 0x4A
 *   N-1             commit byte 0x00, programmed LAST
 *
 * A write cut before its last byte leaves the commit byte at 0xFF and is
 * ignored at the next boot; the previous record is still there.
 *
 * ═══════════════════════════════════════════════════════════════════
 * RULES THAT ARE NOT OPTIONAL
 * ═══════════════════════════════════════════════════════════════════
 *
 * - Call only from the main loop, NEVER from an interrupt. Writes and erases
 *   mask interrupts (`di` ... `ei 0`) and leave the machine at level 0.
 * - Pause raster/HBlank DMA effects before calling: interrupts are off.
 * - Save on a SCREEN CHANGE, from a RAM "dirty" flag - never once per button
 *   press. Each bank holds 8192/SAVE_SIZE slots; a full bank rolls over to
 *   the other one, which may need an 8 KB erase (~60 ms, interrupts off).
 * - Keep your "dirty" flag set until ngpc_flash_save() returns 1. A return
 *   of 0 leaves the previous record intact; retry on the next screen change,
 *   and show the failure somewhere (a refusal looks like success until the
 *   console is switched off).
 *
 * ═══════════════════════════════════════════════════════════════════
 * USAGE
 * ═══════════════════════════════════════════════════════════════════
 *
 *   typedef struct {
 *       u8  magic[4];       // { 0xCA, 0xFE, 0x20, 0x26 }
 *       u8  version;        // YOUR layout version, after the magic
 *       u8  level;
 *       u16 score;
 *       u8  pad[NGPC_FLASH_PAYLOAD - 8];      // 8 = the fields above
 *       u8  journal[NGPC_FLASH_TRAILER];      // stamped by the driver
 *   } MySave;               // sizeof(MySave) MUST equal SAVE_SIZE
 *
 *   static MySave save;     // static: SAVE_SIZE bytes do not belong on the stack
 *
 *   ngpc_flash_init();                    // once, after ngpc_init()
 *   if (ngpc_flash_exists()) ngpc_flash_load(&save);
 *   ...
 *   if (dirty && ngpc_flash_save(&save)) dirty = 0;
 *
 * Build with NGP_ENABLE_FLASH_SAVE=1 (links ngpc_flash_asm.rel).
 * Records written by the pre-2026-10 append-only driver are not "current";
 * a shipped game recovers them with ngpc_flash_load_legacy() (see below).
 *
 * NGP_FAR: flash is above 0x200000; the driver uses far pointers internally,
 * your buffers are ordinary RAM pointers.
 */

#ifndef NGPC_FLASH_H
#define NGPC_FLASH_H

#include "ngpc_types.h"
#include "ngpc_config.h"

/* Bytes per record. A power of two from 256 to 8192 (BIOS writes 256-byte
 * pages, a bank is 8192 bytes). Override globally: -DSAVE_SIZE=512.
 * 256 -> 32 slots per bank, 512 -> 16. */
#ifndef SAVE_SIZE
#define SAVE_SIZE   256
#endif

/* Bytes the game owns, magic included. The last 8 belong to the journal. */
#define NGPC_FLASH_TRAILER  8
#define NGPC_FLASH_PAYLOAD  (SAVE_SIZE - NGPC_FLASH_TRAILER)

/* Find the banks and the newest valid record. Call once, after ngpc_init(). */
void ngpc_flash_init(void);

/* 1 if the cartridge size is known and the image ends below both banks.
 * When 0, every other call is a refusal. */
u8 ngpc_flash_ready(void);

/* Raw BIOS cartridge size code (0x6C58): 0 none, 1 = 4M, 2 = 8M, 3 = 16M. */
u8 ngpc_flash_cart_size(void);

/* 1 if a valid record was found (or written this session). */
u8 ngpc_flash_exists(void);

/* Copy the current record (SAVE_SIZE bytes, journal included) into data. */
void ngpc_flash_load(void *data);

/* MIGRATION from the pre-2026-10 append-only driver (block 33, no journal).
 * Copies into data the newest old-format record of bank A (magic CA FE 20 26,
 * not a journal record) and returns 1; 0 if there is none. Only meaningful
 * when ngpc_flash_exists() is 0. The old format has NO integrity check of its
 * own: validate the copy with YOUR checksum before using it. The old slots are
 * never erased while no journal record exists, and the next save goes after
 * them. Typical use:
 *     if (ngpc_flash_exists()) ngpc_flash_load(&s);
 *     else if (!ngpc_flash_load_legacy(&s) || !my_save_valid(&s)) defaults();
 * Keep your checksum away from the last NGPC_FLASH_TRAILER bytes (the
 * journal); old records had them in the padding. */
u8 ngpc_flash_load_legacy(void *data);

/* Write a new record. data must be SAVE_SIZE bytes in RAM and start with the
 * magic; its last NGPC_FLASH_TRAILER bytes are overwritten by the journal.
 * Returns 1 only once the slot reads back identical to data. Returns 0 on
 * refusal (not ready, bad magic, erase failed) or failed readback; the
 * previous record stays current and the call can be retried. */
u8 ngpc_flash_save(void *data);

/* Bytes that differ between the slot of the last save attempt and data.
 * 0 = identical, 0xFFFF = no attempt this session (or refused). */
u16 ngpc_flash_verify(const void *data);

/* Slots used in the bank holding the current record (0..8192/SAVE_SIZE).
 * Occupancy, not a capacity limit: a full bank rolls over. */
u8 ngpc_flash_slots_used(void);

/* DESTRUCTIVE: erase both banks. For an explicit "erase save" screen only.
 * A "new game" should rather save a fresh record. Returns 1 if both banks
 * read back blank. */
u8 ngpc_flash_erase(void);

#endif /* NGPC_FLASH_H */
