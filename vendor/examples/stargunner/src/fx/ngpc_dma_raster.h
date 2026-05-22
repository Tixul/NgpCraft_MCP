/* ngpc_dma_raster.h - Raster effects using MicroDMA (no CPU HBlank ISR)
 *
 * Part of NGPC Template 2026 (MIT License)
 *
 * This module is a high-level wrapper on top of `ngpc_dma` for the most common
 * use-case on NGPC: per-scanline scroll tables.
 *
 * Compared to `ngpc_raster`:
 * - `ngpc_raster` uses a CPU HBlank interrupt handler (fast but time-budgeted).
 * - `ngpc_dma_raster` uses MicroDMA to write the scroll registers each scanline,
 *   so the CPU does not run code during HBlank (more CPU time for gameplay).
 *
 * Constraints / rules:
 * - Tables should live in RAM.
 * - MicroDMA is one-shot: call `ngpc_dma_raster_rearm()` once per frame during
 *   VBlank, as early as possible (right after `ngpc_vsync()`).
 * - Uses Timer0 (HBlank) and optionally Timer1 (Timer0 overflow). Timer0 is a
 *   shared resource with `ngpc_raster` (mutually exclusive).
 *   With `ngpc_sprmux`, you can coexist by running sprmux on Timer1 (CPU ISR)
 *   while MicroDMA uses Timer0 as its trigger (i.e. use X-only DMA raster, or
 *   otherwise ensure Timer1 is not used as a MicroDMA start vector).
 */
 
#ifndef NGPC_DMA_RASTER_H
#define NGPC_DMA_RASTER_H

#include "ngpc_types.h"
#include "ngpc_gfx.h"
#include "ngpc_dma.h"
#include "ngpc_raster.h"

typedef struct {
    u8 plane;
    u8 enabled;
    const u8 NGP_FAR *table_x;
    const u8 NGP_FAR *table_y;
    NgpcDmaU8Stream stream_x;
    NgpcDmaU8Stream stream_y;
} NgpcDmaRaster;

typedef struct {
    u8 plane;
    u8 enabled;
    const u16 NGP_FAR *table_xy;
    NgpcDmaU16Stream stream_xy;
} NgpcDmaRasterXY;

void ngpc_dma_raster_begin(NgpcDmaRaster *r,
                           u8 plane,
                           const u8 NGP_FAR *table_x,
                           const u8 NGP_FAR *table_y);
void ngpc_dma_raster_begin_ex(NgpcDmaRaster *r,
                              u8 plane,
                              u8 ch_x,
                              const u8 NGP_FAR *table_x,
                              u8 ch_y,
                              const u8 NGP_FAR *table_y);
void ngpc_dma_raster_enable(NgpcDmaRaster *r);
void ngpc_dma_raster_rearm(const NgpcDmaRaster *r);
void ngpc_dma_raster_disable(NgpcDmaRaster *r);
void ngpc_dma_raster_build_parallax_table(u8 *out_table,
                                          const RasterBand *bands,
                                          u8 count,
                                          u16 base_x);

void ngpc_dma_raster_xy_begin(NgpcDmaRasterXY *r,
                              u8 plane,
                              const u16 NGP_FAR *table_xy);
void ngpc_dma_raster_xy_begin_ex(NgpcDmaRasterXY *r,
                                 u8 plane,
                                 u8 channel,
                                 const u16 NGP_FAR *table_xy);
void ngpc_dma_raster_xy_enable(NgpcDmaRasterXY *r);
void ngpc_dma_raster_xy_rearm(const NgpcDmaRasterXY *r);
void ngpc_dma_raster_xy_disable(NgpcDmaRasterXY *r);
void ngpc_dma_raster_build_parallax_table_xy(u16 *out_table_xy,
                                             const RasterBand *bands,
                                             u8 count,
                                             u16 base_x,
                                             u8 y);

#endif /* NGPC_DMA_RASTER_H */
