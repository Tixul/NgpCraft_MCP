/*
 * ngpc_sys.h - System initialization, VBI handler, shutdown
 *
 * Part of NGPC Template 2026 (MIT License)
 */

#ifndef NGPC_SYS_H
#define NGPC_SYS_H

#include "ngpc_types.h"

/* Frame counter, incremented by VBI at 60 Hz. */
extern volatile u8 g_vb_counter;

/* Initialize NGPC hardware:
 * - Detects mono/color mode
 * - Installs interrupt vectors (VBL mandatory)
 * - Sets viewport to 160x152
 * - Enables interrupts
 * Call this first in main(). */
void ngpc_init(void);

/* Returns 1 if running on NGPC Color, 0 if monochrome NGP. */
u8 ngpc_is_color(void);

/* Perform system shutdown via BIOS. Call when USR_SHUTDOWN is set. */
void ngpc_shutdown(void);

/* Call BIOS to load the built-in system font into tile RAM. */
void ngpc_load_sysfont(void);

/* Copy len bytes from src to dst (no alignment required). */
void ngpc_memcpy(u8 *dst, const u8 *src, u16 len);

/* Fill len bytes at dst with val. */
void ngpc_memset(u8 *dst, u8 val, u16 len);

#endif /* NGPC_SYS_H */
