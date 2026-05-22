/*
 * ngpc_flash.h - Cartridge flash save system
 *
 * Part of NGPC Template 2026 (MIT License)
 *
 * Provides 512 bytes of persistent save storage in cart flash.
 * Save location: block 33 (F16_B33), offset 0x1FA000, 8 KB.
 *
 * Uses an append-only slot strategy (16 slots × 512 bytes = 8 KB).
 * Each call to ngpc_flash_save() writes to the next empty slot —
 * no erase is needed within a session. Block erase (CLR_FLASH_RAM)
 * is triggered only when all 16 slots are full, which cannot happen
 * twice in a single power-on session under normal gameplay.
 *
 * ── IMPORTANT : magic number ─────────────────────────────────────
 * ngpc_flash_exists() vérifie les 4 premiers octets de la zone de
 * sauvegarde. Ils doivent valoir exactement : 0xCA, 0xFE, 0x20, 0x26
 * Sans ce magic, ngpc_flash_exists() retourne toujours 0.
 *
 * Patron recommandé :
 *
 *   typedef struct {
 *       u8 magic[4];   // DOIT valoir { 0xCA, 0xFE, 0x20, 0x26 }
 *       u8 hp;
 *       u8 level;
 *       // ... jusqu'à 252 autres octets
 *   } SaveData;
 *
 *   // Sauvegarder :
 *   SaveData save;
 *   save.magic[0] = 0xCA; save.magic[1] = 0xFE;
 *   save.magic[2] = 0x20; save.magic[3] = 0x26;
 *   save.hp    = player_hp;
 *   save.level = player_level;
 *   ngpc_flash_save(&save);
 *
 *   // Charger (si une sauvegarde existe) :
 *   if (ngpc_flash_exists()) {
 *       SaveData save;
 *       ngpc_flash_load(&save);
 *       player_hp    = save.hp;
 *       player_level = save.level;
 *   }
 * ─────────────────────────────────────────────────────────────────
 */

#ifndef NGPC_FLASH_H
#define NGPC_FLASH_H

#include "ngpc_types.h"

/* Maximum save data size in bytes. */
#define SAVE_SIZE   512

/* Initialize the flash save system. Call once at startup. */
void ngpc_flash_init(void);

/* Write 512 bytes to flash.
 * data: pointer to 512 bytes of save data. */
void ngpc_flash_save(const void *data);

/* Read 512 bytes from flash into buffer.
 * data: pointer to receive buffer (must be >= 512 bytes). */
void ngpc_flash_load(void *data);

/* Check if valid save data exists (magic number check).
 * Returns 1 if save exists, 0 if not. */
u8 ngpc_flash_exists(void);

/* Read back flash and compare against data. Returns 0 if match, >0 = error count. */
u16 ngpc_flash_verify(const void *data);

#endif /* NGPC_FLASH_H */
