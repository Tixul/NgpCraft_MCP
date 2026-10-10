/*
 * ngpc_flash.c - Cartridge flash save system (two-bank journal)
 *
 * Part of NgpCraft_base_template (MIT License)
 * Ported from the OVER REV v31 journal, made game-independent.
 * Layout, rules and usage: ngpc_flash.h. History: docs/NGPC_FLASH_SAVE_GUIDE.md.
 *
 * WHY THIS REPLACED THE APPEND-ONLY DRIVER (each point cost a real console):
 *
 *  1. `di` was missing around the flash operations. A chip being programmed
 *     answers with status; any interrupt fetches its handler from it. Hard
 *     crash on hardware, invisible in an emulator that keeps serving ROM.
 *  2. The block address was hard-coded to 0x3FA000 (16 Mbit). On a smaller
 *     chip the erase wiped 8 KB elsewhere - possibly the running code.
 *  3. A slot was "empty" when its FIRST byte was 0xFF. Programming over a
 *     partly written slot (1 over 0 is impossible) never succeeded and hid
 *     ~18 s of retries. Now every byte of a slot must read 0xFF.
 *  4. One bank: when it filled up, the only copy of the save was erased
 *     before the new one was written. Now the bank holding the newest valid
 *     record is NEVER erased; the other one is, only when needed.
 *  5. No integrity check: a write cut by a power loss loaded as valid.
 *     Now CRC16 + sequence + a commit byte programmed last.
 *  6. Write went through the AMD stub, which disabled the watchdog and wrote
 *     its refresh code while it was off. Now VECT_FLASHWRITE (BIOS), with the
 *     watchdog refreshed right before and after (StarGunner path).
 *
 * VECT_FLASHERS cannot erase blocks 32/33/34 (SysCall.txt p.299): the erase
 * keeps the StarGunner stub, run from RAM, interrupts masked.
 */

#include "ngpc_hw.h"
#include "ngpc_flash.h"

#define HW_CART_SIZE  (*(volatile u8 *)0x6C58)   /* BIOS: 1=4M 2=8M 3=16M */
#define BANK_BYTES    8192UL
#define NONE          255u

#if   SAVE_SIZE == 256
#define SAVE_SHIFT 8
#elif SAVE_SIZE == 512
#define SAVE_SHIFT 9
#elif SAVE_SIZE == 1024
#define SAVE_SHIFT 10
#elif SAVE_SIZE == 2048
#define SAVE_SHIFT 11
#elif SAVE_SIZE == 4096
#define SAVE_SHIFT 12
#elif SAVE_SIZE == 8192
#define SAVE_SHIFT 13
#else
#error SAVE_SIZE_must_be_a_power_of_two_from_256_to_8192
#endif

#define SLOTS      ((u8)(8192u >> SAVE_SHIFT))   /* slots per bank */
#define PAGES      ((u32)(SAVE_SIZE >> 8))       /* 256-byte BIOS units */
#define SEQ_AT     ((u16)(SAVE_SIZE - 8))
#define CRC_AT     ((u16)(SAVE_SIZE - 4))
#define TAG_AT     ((u16)(SAVE_SIZE - 2))
#define COMMIT_AT  ((u16)(SAVE_SIZE - 1))
#define TAG        0x4Au

#if !NGP_ENABLE_FLASH_SAVE

void ngpc_flash_init(void)                 { }
u8   ngpc_flash_ready(void)                { return 0u; }
u8   ngpc_flash_cart_size(void)            { return 0u; }
u8   ngpc_flash_exists(void)               { return 0u; }
void ngpc_flash_load(void *data)           { (void)data; }
u8   ngpc_flash_load_legacy(void *data)    { (void)data; return 0u; }
u8   ngpc_flash_save(void *data)           { (void)data; return 0u; }
u16  ngpc_flash_verify(const void *data)   { (void)data; return 0xFFFFu; }
u8   ngpc_flash_slots_used(void)           { return 0u; }
u8   ngpc_flash_erase(void)                { return 0u; }

#else /* NGP_ENABLE_FLASH_SAVE */

/* ngpc_flash_asm.asm. Both mask interrupts and return at level 0. */
extern void ngpc_flash_erase_asm(u32 abs_block);
extern void ngpc_flash_write_bios(const void *src, u32 offset, u32 pages);

/* End of the linked image (ngpc.lcf). It must stay below BOTH banks, which
 * on a 4 or 8 Mbit cart sit well inside the 0x1F0000 the linker allows. */
extern u8 NGP_FAR _DataROM_END;

static u32 s_offset;            /* bank A offset in the cart, 0 = refused */
static u32 s_sequence;          /* sequence of the current record */
static u8  s_current = NONE;    /* slot 0..2*SLOTS-1 of the current record */
static u8  s_last_slot = NONE;  /* slot of the last save attempt */

/* Slots 0..SLOTS-1 are bank A, SLOTS..2*SLOTS-1 bank B (0x2000 below). */
static u32 slot_offset(u8 slot)
{
    u32 off = s_offset;
    if (slot >= SLOTS) {
        off -= BANK_BYTES;
        slot = (u8)(slot - SLOTS);
    }
    return off + ((u32)slot << SAVE_SHIFT);
}

static volatile u8 NGP_FAR *slot_ptr(u8 slot)
{
    return (volatile u8 NGP_FAR *)(CART_ROM_BASE + slot_offset(slot));
}

static u16 crc_byte(u16 crc, u8 value)
{
    u8 bit;
    crc ^= (u16)((u16)value << 8);
    for (bit = 0u; bit < 8u; bit++)
        crc = (u16)((crc & 0x8000u) ? ((u16)(crc << 1) ^ 0x1021u) : (u16)(crc << 1));
    return crc;
}

/* EVERY byte, not the first one: a slot cut mid-write is not reusable. */
static u8 slot_erased(u8 slot)
{
    volatile u8 NGP_FAR *p = slot_ptr(slot);
    u16 i;
    for (i = 0u; i < (u16)SAVE_SIZE; i++)
        if (p[i] != 0xFFu) return 0u;
    return 1u;
}

/* Cheap test: magic, tag and commit byte. The CRC is checked separately,
 * only for a record that could become the current one. */
static u8 slot_looks_valid(u8 slot)
{
    volatile u8 NGP_FAR *p = slot_ptr(slot);
    return (u8)(p[0] == 0xCAu && p[1] == 0xFEu && p[2] == 0x20u && p[3] == 0x26u
                && p[TAG_AT] == TAG && p[COMMIT_AT] == 0x00u);
}

static u8 slot_crc_ok(u8 slot)
{
    volatile u8 NGP_FAR *p = slot_ptr(slot);
    u16 i, crc = 0xFFFFu;
    for (i = 0u; i < CRC_AT; i++) crc = crc_byte(crc, p[i]);
    return (u8)(p[CRC_AT] == (u8)crc && p[CRC_AT + 1u] == (u8)(crc >> 8));
}

static u32 slot_sequence(u8 slot)
{
    volatile u8 NGP_FAR *p = slot_ptr(slot);
    return (u32)p[SEQ_AT]
         | ((u32)p[SEQ_AT + 1u] << 8)
         | ((u32)p[SEQ_AT + 2u] << 16)
         | ((u32)p[SEQ_AT + 3u] << 24);
}

static u8 bank_of(u8 slot) { return (u8)((slot >= SLOTS) ? SLOTS : 0u); }

void ngpc_flash_init(void)
{
    u8 slot;
    u32 sequence;

    s_current = NONE;
    s_last_slot = NONE;
    s_sequence = 0UL;
    switch (HW_CART_SIZE) {
    case 1u: s_offset = 0x07A000UL; break;
    case 2u: s_offset = 0x0FA000UL; break;
    case 3u: s_offset = 0x1FA000UL; break;
    default: s_offset = 0UL; break;      /* unknown: refuse, never guess */
    }
    if (!s_offset) return;
    if ((u32)&_DataROM_END > CART_ROM_BASE + s_offset - BANK_BYTES) {
        s_offset = 0UL;                  /* the image overlaps bank B */
        return;
    }
    /* Newest valid record wins, sequence compared modulo 2^32. */
    for (slot = 0u; slot < (u8)(SLOTS + SLOTS); slot++) {
        if (!slot_looks_valid(slot)) continue;
        sequence = slot_sequence(slot);
        if (s_current != NONE) {
            u32 delta = sequence - s_sequence;
            if (delta == 0UL || delta >= 0x80000000UL) continue;
        }
        if (slot_crc_ok(slot)) {
            s_current = slot;
            s_sequence = sequence;
        }
    }
}

u8 ngpc_flash_ready(void)     { return (u8)(s_offset != 0UL); }
u8 ngpc_flash_cart_size(void) { return HW_CART_SIZE; }
u8 ngpc_flash_exists(void)    { return (u8)(s_current != NONE); }

void ngpc_flash_load(void *data)
{
    u8 *dst = (u8 *)data;
    volatile u8 NGP_FAR *p;
    u16 i;
    if (s_current == NONE) return;
    p = slot_ptr(s_current);
    for (i = 0u; i < (u16)SAVE_SIZE; i++) dst[i] = p[i];
}

/* The old driver wrote bank A only, slot after slot, newest = highest index
 * carrying the magic. Its records have no journal trailer. */
u8 ngpc_flash_load_legacy(void *data)
{
    u8 *dst = (u8 *)data;
    volatile u8 NGP_FAR *p;
    u8 slot;
    u16 i;
    if (!s_offset) return 0u;
    slot = SLOTS;
    while (slot-- > 0u) {
        p = slot_ptr(slot);
        if (p[0] != 0xCAu || p[1] != 0xFEu || p[2] != 0x20u || p[3] != 0x26u)
            continue;
        if (slot_looks_valid(slot) && slot_crc_ok(slot))
            continue;                    /* a journal record, not a legacy one */
        for (i = 0u; i < (u16)SAVE_SIZE; i++) dst[i] = p[i];
        return 1u;
    }
    return 0u;
}

u16 ngpc_flash_verify(const void *data)
{
    const u8 *src = (const u8 *)data;
    volatile u8 NGP_FAR *p;
    u16 i, errors = 0u;
    if (!s_offset || s_last_slot == NONE) return 0xFFFFu;
    p = slot_ptr(s_last_slot);
    for (i = 0u; i < (u16)SAVE_SIZE; i++) if (p[i] != src[i]) errors++;
    return errors;
}

u8 ngpc_flash_slots_used(void)
{
    u8 i, count = 0u;
    u8 bank = (s_current == NONE) ? 0u : bank_of(s_current);
    if (!s_offset) return 0u;
    for (i = bank; i < (u8)(bank + SLOTS); i++) if (!slot_erased(i)) count++;
    return count;
}

/* Erase, then check every byte: a partial erase must never be programmed. */
static u8 erase_bank(u8 bank)
{
    u8 i;
    ngpc_flash_erase_asm(CART_ROM_BASE + slot_offset(bank));
    for (i = bank; i < (u8)(bank + SLOTS); i++) if (!slot_erased(i)) return 0u;
    return 1u;
}

u8 ngpc_flash_erase(void)
{
    if (!s_offset) return 0u;
    if (!erase_bank(0u) || !erase_bank(SLOTS)) {
        ngpc_flash_init();               /* rescan whatever survived */
        return 0u;
    }
    s_current = NONE;
    s_last_slot = NONE;
    s_sequence = 0UL;
    return 1u;
}

u8 ngpc_flash_save(void *data)
{
    u8 *src = (u8 *)data;
    u8 slot, bank, i;
    u16 at, crc = 0xFFFFu;
    u32 sequence;

    s_last_slot = NONE;      /* a refused attempt must not verify an old one */
    if (!s_offset) return 0u;
    if (src[0] != 0xCAu || src[1] != 0xFEu || src[2] != 0x20u || src[3] != 0x26u)
        return 0u;           /* would be written, then never found again */

    bank = (s_current == NONE) ? 0u : bank_of(s_current);
    slot = (s_current == NONE) ? bank : (u8)(s_current + 1u);
    while (slot < (u8)(bank + SLOTS) && !slot_erased(slot)) slot++;
    if (slot >= (u8)(bank + SLOTS)) {
        /* Bank full: switch. NEVER erase the bank holding the current
         * record; the other one is erased only if it is not blank already. */
        bank = (u8)(bank ^ SLOTS);
        for (i = bank; i < (u8)(bank + SLOTS); i++) if (!slot_erased(i)) break;
        if (i != (u8)(bank + SLOTS) && !erase_bank(bank)) return 0u;
        slot = bank;
    }

    sequence = s_sequence + 1UL;
    for (i = 0u; i < 4u; i++) src[SEQ_AT + i] = (u8)(sequence >> (i * 8u));
    for (at = 0u; at < CRC_AT; at++) crc = crc_byte(crc, src[at]);
    src[CRC_AT] = (u8)crc;
    src[CRC_AT + 1u] = (u8)(crc >> 8);
    src[TAG_AT] = TAG;
    src[COMMIT_AT] = 0x00u;  /* last byte the BIOS programs */

    s_last_slot = slot;
    ngpc_flash_write_bios(src, slot_offset(slot), PAGES);
    if (ngpc_flash_verify(src) != 0u) return 0u;
    s_current = slot;
    s_sequence = sequence;
    return 1u;
}

#endif /* NGP_ENABLE_FLASH_SAVE */
