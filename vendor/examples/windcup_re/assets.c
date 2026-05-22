#include "ngpc.h"
#include "library.h"
#include "colors.h"
#include "assets.h"

/* helper RGB444 → RGB() */
#define RGB444_TO_RGB(v) RGB((v)&0xF, ((v)>>4)&0xF, ((v)>>8)&0xF)

/* Tiles layout */
#define GAME_TILE_BASE     256
#define PERSO_TILE_BASE    (GAME_TILE_BASE + 5 + 80)  /* after terrain tiles */

/* perso tiles exported (2 layers x 16 frames x 4 tiles x 16 bytes) */
extern const unsigned char windjam_perso_tiles[2][16][4][16];
extern const unsigned short windjam_perso_pal_rgb444[2][16][4];
extern const unsigned char windjam_perso_pal_count[2];

/* --------------------------------------------------
   Tile 0 : paddle (placeholder plein)
   Tile 1..4 : disc animation frames (8x8, 2bpp)
   -------------------------------------------------- */
static const unsigned short SpriteTiles[5][8] =
{
    /* tile 0 : paddle */
    {
        0xFFFF,0xFFFF,0xFFFF,0xFFFF,
        0xFFFF,0xFFFF,0xFFFF,0xFFFF
    },

    /* frame 0 */
    { 0x0550,0x1AA4,0x6EA9,0x6BA9,0x6AE9,0x6AB9,0x1AA4,0x0550 },
    /* frame 1 */
    { 0x0550,0x1AA4,0x6AA9,0x7FA9,0x6AFD,0x6AA9,0x1AA4,0x0550 },
    /* frame 2 */
    { 0x0550,0x1AA4,0x6AB9,0x6AE9,0x6BA9,0x6EA9,0x1AA4,0x0550 },
    /* frame 3 */
    { 0x0550,0x1AE4,0x6AE9,0x6AE9,0x6BA9,0x6BA9,0x1BA4,0x0550 }
};

/* Install raw 2bpp tiles (8 words per tile) into Tile RAM at baseTile */
static void InstallTiles2BPP_At(const unsigned char* src, unsigned short tileCount, unsigned short baseTile)
{
    volatile unsigned char* dst;
    unsigned long bytes;
    unsigned long i;

    dst = (volatile unsigned char*)(0xA000 + ((unsigned long)baseTile * 16ul));
    bytes = (unsigned long)tileCount * 16ul;

    for (i = 0; i < bytes; i++) {
        dst[i] = src[i];
    }
}

void Assets_Init(void)
{
    SetBackgroundColour(RGB(0,0,0));
    SetWindowColor(RGB(0,0,0));

    /* system font in low tiles */
    SysSetSystemFont();

    /* sprites: paddle + disc (5 tiles) at 256 */
    InstallTileSetAt(SpriteTiles, (unsigned short)(sizeof(SpriteTiles) / 2), GAME_TILE_BASE);

    /* perso: 2 layers * 16 frames * 4 tiles = 128 tiles */
    InstallTiles2BPP_At((const unsigned char*)windjam_perso_tiles, 128, PERSO_TILE_BASE);

    /* sprite palette 0 (disc/paddle) */
    SetPalette(
        SPRITE_PLANE, 0,
        RGB444_TO_RGB(0x0000), /* transparent */
        RGB444_TO_RGB(0x0000),
        RGB444_TO_RGB(0x000B),
        RGB444_TO_RGB(0x0CCD)
    );

    /* Character palettes from export data */
    {
        u8 layer, p;
        for (layer = 0; layer < 2; layer++) {
            for (p = 0; p < windjam_perso_pal_count[layer]; p++) {
                const unsigned short* c = windjam_perso_pal_rgb444[layer][p];
                SetPalette(SPRITE_PLANE, (u8)(1 + layer * 8 + p),
                    RGB444_TO_RGB(c[0]),
                    RGB444_TO_RGB(c[1]),
                    RGB444_TO_RGB(c[2]),
                    RGB444_TO_RGB(c[3]));
            }
        }
    }
}
