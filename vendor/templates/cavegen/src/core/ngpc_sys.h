/*
 * ngpc_sys.h - System initialization, VBI handler, shutdown
 *
 * Part of NgpCraft_base_template (MIT License)
 */

#ifndef NGPC_SYS_H
#define NGPC_SYS_H

#include "ngpc_types.h"

/* Frame counter, incremented by VBI at 60 Hz. */
extern volatile u8 g_vb_counter;

/* Apply power-off bug patch for prototype firmware (OS_Version == 0x00).
 * Safe no-op on all retail hardware. Call once at startup before ngpc_init().
 * Equivalent to SYS_PATCH from system.lib (SNK, 1998). */
void ngpc_sys_patch(void);

/* Initialize NGPC hardware:
 * - Detects mono/color mode
 * - Installs interrupt vectors (VBL mandatory)
 * - Sets viewport to 160x152
 * - Disables BIOS inactivity shutdown; preserves battery/POWER handling
 * - Enables interrupts
 * Call this first in main(). */
void ngpc_init(void);

/* Returns 1 if running on NGPC Color, 0 if monochrome NGP. */
u8 ngpc_is_color(void);

/* Returns LANG_JAPANESE (0) or LANG_ENGLISH (1), the order of SysWork.txt
 * (0x6F87). A two-state console setting, not a language menu: a third
 * language cannot be requested through it.
 * Value is read once from BIOS register 0x6F87 during ngpc_init() and cached. */
u8 ngpc_get_language(void);

/* ONE HOOK, RUN AT THE TOP OF EVERY VBLANK, for work that must happen on every
 * DISPLAYED frame rather than every frame the game manages to finish. A
 * one-shot raster DMA is the case it exists for: re-armed from the game loop,
 * a single missed frame draws the table from mid-screen and splits the
 * picture in two. Keep it short -- it runs before the queued VRAM writes,
 * with the beam about to start. Pass 0 to remove it. */
typedef void (*NgpcVblankFn)(void);
void ngpc_set_vblank_hook(NgpcVblankFn fn);

/* Perform system shutdown via BIOS. Call when USR_SHUTDOWN is set. */
void ngpc_shutdown(void);

/* Call BIOS to load the built-in system font into tile RAM. */
void ngpc_load_sysfont(void);

/* Copy len bytes from src to dst (no alignment required). */
void ngpc_memcpy(u8 *dst, const u8 *src, u16 len);

/* Fill len bytes at dst with val. */
void ngpc_memset(u8 *dst, u8 val, u16 len);

#endif /* NGPC_SYS_H */
