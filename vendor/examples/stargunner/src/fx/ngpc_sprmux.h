/*
 * ngpc_sprmux.h - Sprite multiplexing (more than 64 logical sprites)
 *
 * Part of NGPC Template 2026 (MIT License)
 *
 * The NGPC has only 64 hardware sprite slots. This module reuses slots
 * during HBlank: when a sprite is no longer visible on lower scanlines,
 * its slot is reassigned to another sprite that starts lower on screen.
 *
 * Notes:
 * - Sprite size is assumed 8x8 (no chain handling in the mux core).
 * - Uses Timer 0 HBlank interrupt (same resource as ngpc_raster).
 * - Call begin/add/flush once per frame.
 */

#ifndef NGPC_SPRMUX_H
#define NGPC_SPRMUX_H

#include "ngpc_types.h"

/* Logical sprite capacity stored by the multiplexer. */
#define SPRMUX_MAX_LOGICAL  128

/* Begin a new logical sprite list for the current frame. */
void ngpc_sprmux_begin(void);

/* Add one logical sprite to the current frame list.
 * Returns logical index, or 0xFF if the list is full. */
u8 ngpc_sprmux_add(s16 x, s16 y, u16 tile, u8 pal, u8 flags);

/* Build line tables, install HBlank ISR and start multiplexing for the frame. */
void ngpc_sprmux_flush(void);

/* Stop the multiplexer and hide all hardware sprite slots. */
void ngpc_sprmux_disable(void);

/* Number of logical sprites dropped because no hardware slot was free. */
u8 ngpc_sprmux_overflow_count(void);

#endif /* NGPC_SPRMUX_H */

