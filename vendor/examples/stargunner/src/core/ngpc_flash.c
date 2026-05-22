/*
 * ngpc_flash.c - Cartridge flash save system (append-only slot design)
 *
 * Part of NGPC Template 2026 (MIT License)
 *
 * DESIGN: append-only slots to avoid mid-session double-erase bug
 * ─────────────────────────────────────────────────────────────────
 * Problem: CLR_FLASH_RAM (the only working erase path for block 33)
 * silently fails on its 2nd call within the same power-on session.
 * Direct writes to cart ROM area are ignored by hardware (user code
 * cannot assert /WE on the cart bus; only BIOS/system.lib can).
 *
 * Solution: treat block 33 as an array of NUM_SLOTS append-only slots.
 *   - Each save writes to the NEXT empty slot (first byte 0xFF = empty).
 *   - No erase is needed during normal gameplay.
 *   - Erase is called only when ALL slots are full (block full).
 *     At that point it is guaranteed to be the first CLR_FLASH_RAM call
 *     of the session (16+ unique high scores in one session is impossible).
 *
 * Block layout (block 33, 8 KB):
 *   slot 0   : 0x1FA000 .. 0x1FA1FF  (512 bytes)
 *   slot 1   : 0x1FA200 .. 0x1FA3FF
 *   ...
 *   slot 15  : 0x1FBE00 .. 0x1FBFFF
 *
 * Boot load: scan slots 15→0, return the last slot with valid magic.
 * Save     : find next slot with first byte 0xFF, write there.
 *            If all 16 slots are used, erase block then write to slot 0.
 */

#include "ngpc_hw.h"
#include "ngpc_sys.h"
#include "ngpc_flash.h"
#include "ngpc_log.h"


/* Save location: block 33 (F16_B33), cart CS0 (base 0x200000).
 * NGP_FAR (__far) required: 0x200000 + 0x1FA000 = 0x3FA000 is outside
 * the 16-bit near range. Without it cc900 truncates to 0xA000. */
#define SAVE_OFFSET     0x1FA000UL
#define SAVE_ADDR       ((volatile u8 NGP_FAR *)(CART_ROM_BASE + SAVE_OFFSET))

/* Slot layout.
 * SLOT_SIZE must equal SAVE_SIZE (BIOS write granularity is 256 bytes,
 * and SAVE_SIZE is already a multiple of 256). */
#define SLOT_SIZE       SAVE_SIZE                       /* 512 bytes */
#define NUM_SLOTS       (8192UL / SLOT_SIZE)            /* 16 slots  */

/* Magic bytes that identify a valid save slot (first 4 bytes). */
#define SAVE_MAGIC_0  0xCA
#define SAVE_MAGIC_1  0xFE
#define SAVE_MAGIC_2  0x20
#define SAVE_MAGIC_3  0x26

/* ---- Public API ---- */

#if !NGP_ENABLE_FLASH_SAVE

void ngpc_flash_init(void) { }
void ngpc_flash_save(const void *data) { (void)data; }
void ngpc_flash_load(void *data) { (void)data; }
u8 ngpc_flash_exists(void) { return 0; }
u16 ngpc_flash_verify(const void *data) { (void)data; return 0u; }

#else

/* ASM stubs — erase and write are separate, pure ASM functions. */
extern void ngpc_flash_erase_asm(void);
extern void ngpc_flash_write_asm(const void *data, u32 offset);

/* Tracks the slot index used by the last ngpc_flash_save() call,
 * so ngpc_flash_verify() can inspect the right slot. */
static u8 s_last_slot = 0xFF;

/* Return the slot index (0..NUM_SLOTS-1) of the next empty slot,
 * or NUM_SLOTS if the block is completely full. */
static u8 flash_find_next_slot(void)
{
    u8 i;
    for (i = 0u; i < (u8)NUM_SLOTS; i++) {
        /* An empty slot has its first byte erased to 0xFF. */
        if (SAVE_ADDR[(u32)i * SLOT_SIZE] == 0xFF)
            return i;
    }
    return (u8)NUM_SLOTS;   /* full */
}

/* Return the slot index of the LAST slot that starts with the save magic,
 * or 0xFF if no valid slot is found. */
static u8 flash_find_last_slot(void)
{
    u8 i = (u8)NUM_SLOTS;
    while (i-- > 0u) {
        u32 base = (u32)i * SLOT_SIZE;
        if (SAVE_ADDR[base + 0u] == SAVE_MAGIC_0 &&
            SAVE_ADDR[base + 1u] == SAVE_MAGIC_1 &&
            SAVE_ADDR[base + 2u] == SAVE_MAGIC_2 &&
            SAVE_ADDR[base + 3u] == SAVE_MAGIC_3) {
            return i;
        }
    }
    return 0xFF;    /* no valid slot found */
}

void ngpc_flash_init(void)
{
    /* Flash is memory-mapped; nothing to initialise. */
}

void ngpc_flash_save(const void *data)
{
    u8  slot;
    u32 offset;

    slot = flash_find_next_slot();

    if (slot >= (u8)NUM_SLOTS) {
        /* Block full: erase (first CLR_FLASH_RAM call of session — always works). */
        ngpc_flash_erase_asm();
#if NGP_ENABLE_DEBUG
        NGPC_LOG_HEX("FERA", SAVE_ADDR[0]);   /* 0xFF=ok, 0xCA=erase failed */
#endif
        slot = 0u;
    }

    offset = SAVE_OFFSET + (u32)slot * SLOT_SIZE;
    s_last_slot = slot;
    ngpc_flash_write_asm(data, offset);
}

void ngpc_flash_load(void *data)
{
    u8  slot = flash_find_last_slot();
    u32 base;
    u8 *dst = (u8 *)data;
    u16 i;

    if (slot == 0xFF) return;   /* no valid save found */

    base = (u32)slot * SLOT_SIZE;
    for (i = 0u; i < SAVE_SIZE; i++)
        dst[i] = SAVE_ADDR[base + i];
}

u8 ngpc_flash_exists(void)
{
    return flash_find_last_slot() != 0xFF;
}

/* Read back the last-written slot and count bytes that differ from data.
 * Returns 0 if flash matches exactly, >0 = error byte count. */
u16 ngpc_flash_verify(const void *data)
{
    const u8 *expected = (const u8 *)data;
    u32 base;
    u16 i, errors = 0u;

    if (s_last_slot == 0xFF) return 0xFFFFu;   /* save() not called yet */

    base = (u32)s_last_slot * SLOT_SIZE;
    for (i = 0u; i < SAVE_SIZE; i++) {
        if (SAVE_ADDR[base + i] != expected[i])
            errors++;
    }
    return errors;
}

#endif /* NGP_ENABLE_FLASH_SAVE */
