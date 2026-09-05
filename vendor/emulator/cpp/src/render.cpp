/* render.cpp — the K2GE, DRAWING ONE SCANLINE AT A TIME.
 *
 * WHY THIS EXISTS, and it is two reasons, not one.
 *
 * 1. THE PICTURE WAS COMPOSED AT THE END OF THE FRAME, FROM THE FINAL VRAM.
 *    The silicon draws each line as the beam passes it. A scrolling game -- Metal Slug,
 *    say -- STREAMS new tiles and map rows into VRAM *while the frame is being drawn*,
 *    very often by DMA on the horizontal blank. So the top of the screen is drawn from
 *    the old data and the bottom from the new, and that is not a glitch: it is the whole
 *    technique. Composing from one end-of-frame snapshot paints every line with the
 *    FINAL state, which tears a band straight through the tilemap. The user saw exactly
 *    that ("une partie de la tilemap qui glitch") before any instrument here did.
 *
 * 2. IT WAS IN PYTHON, AND IT COST 13.5 ms OF A 16.67 ms FRAME.
 *    Measured on Metal Slug: emulation 0.75 ms, video-memory copy 0.91 ms, RENDER
 *    13.49 ms. The C++ core retires the whole machine in a twentieth of the time the
 *    shell took to colour it in. Any hiccup -- audio, GC, the scheduler -- dropped a
 *    frame, which is the other half of what the user reported ("des sauts d'image").
 *
 * ⚖️ THE PYTHON RENDERER STAYS, AS THE REFERENCE. It is the one with the citations, and
 * `tests/test_render_native.py` holds the two against each other pixel for pixel. A fast
 * renderer that quietly disagrees with the slow one is not an optimisation, it is a
 * second implementation of the machine -- and this project has exactly one.
 *
 * WHICH REGISTERS A LINE IS DRAWN WITH. The LATCHED part of the display block
 * (0x8000..0x803F: scroll, sprite offset, plane priority, 2D control) is taken from the
 * RASTER SNAPSHOT of this line -- the values standing as the line opened -- because the
 * Tech Ref says in as many words that a write lands on the NEXT line. Everything else
 * (palettes, the backdrop register, VRAM, the OAM -- and the WINDOW, see regs_of_line)
 * is read LIVE, because that is what the beam sees.
 */
#include "machine.hpp"

namespace ngpc {

namespace {

/* 0BGR, 12 bits: low byte = GGGG RRRR, high byte = 0000 BBBB. */
inline uint16_t color_at(const Machine& m, uint32_t address) {
    return uint16_t((uint32_t(m.mem[address + 1]) << 8) | m.mem[address]);
}

constexpr uint32_t kPaletteSprite = 0x008200;
constexpr uint32_t kPaletteScr1   = 0x008280;
constexpr uint32_t kPaletteScr2   = 0x008300;
constexpr uint32_t kPaletteBg     = 0x0083E0;   /* backdrop (BGC), 8 entries */
/* ⛔ NOT the same block as the backdrop. This core used kPaletteBg for the
 * out-of-window fill too, and Fatal Fury's intro convicted it: the game fills
 * 0x83E0 with WHITE (its backdrop inside the window) and writes a grey RAMP at
 * 0x83F0 whose entry 7 is BLACK -- then sets OOWC=7. One block would make the
 * letterbox WHITE; the intended picture (and the reference map: HW_PAL_BG
 * 0x83E0 "couleur de fond", HW_PAL_WIN 0x83F0 "couleur hors-fenetre") is a
 * BLACK letterbox. A game does not build a ramp in a palette it never uses. */
constexpr uint32_t kPaletteOow    = 0x0083F0;   /* out-of-window, 8 entries */
constexpr uint32_t kOamBase       = 0x008800;
constexpr uint32_t kOamCpcBase    = 0x008C00;
constexpr uint32_t kScr1Map       = 0x009000;
constexpr uint32_t kScr2Map       = 0x009800;
constexpr uint32_t kCharRam       = 0x00A000;
constexpr uint32_t kBgcRegister   = 0x008118;
constexpr uint32_t kK1geMode      = 0x0087E2;

/* K1GE upper-palette-compatible mode: a 3-bit LEVEL look-up, then a 12-bit colour.
 * index = palette_code * 8 + level  (settled in pass 236 by the BIOS's own data). */
constexpr uint32_t kK1geLut[3]    = {0x008100, 0x008108, 0x008110};  /* spr, scr1, scr2 */
constexpr uint32_t kK1gePal[3]    = {0x008380, 0x0083A0, 0x0083C0};

/* One tile row: 8 left-to-right 2-bit values.
 *   odd byte  bits[7:6]=dot0 [5:4]=dot1 [3:2]=dot2 [1:0]=dot3
 *   even byte bits[7:6]=dot4 [5:4]=dot5 [3:2]=dot6 [1:0]=dot7          */
inline void tile_row(const Machine& m, unsigned tile, unsigned row, uint8_t out[8]) {
    const uint32_t base = kCharRam + tile * 16u + row * 2u;
    const uint8_t even = m.mem[base];
    const uint8_t odd  = m.mem[base + 1];
    out[0] = uint8_t((odd  >> 6) & 3); out[1] = uint8_t((odd  >> 4) & 3);
    out[2] = uint8_t((odd  >> 2) & 3); out[3] = uint8_t((odd  >> 0) & 3);
    out[4] = uint8_t((even >> 6) & 3); out[5] = uint8_t((even >> 4) & 3);
    out[6] = uint8_t((even >> 2) & 3); out[7] = uint8_t((even >> 0) & 3);
}

struct Regs {
    uint8_t wba_h, wba_v, wsi_h, wsi_v;
    uint8_t ctl2d;                  /* bit7 NEG, bits2..0 OOWC */
    uint8_t po_h, po_v;
    bool    scr2_in_front;          /* 0x8030 bit 7 */
    uint8_t s1so_h, s1so_v, s2so_h, s2so_v;
};

/* ⚡ THE WINDOW IS NOT LATCHED PER LINE, AND THE MANUFACTURER SAYS SO BY OMISSION.
 *
 * Every display register this renderer reads carries an explicit caution in the K2GE
 * Tech Ref -- 0x8012 (§ 4-11 "Setting in this register is reflected in the next line
 * being drawn"), 0x8020/21 (§ 4-3-4), 0x8030 (§ 4-4-7), 0x8032..35 (§ 4-4-8), 0x8118
 * (§ 4-6). The WINDOW registers (§ 4-5, 0x8002..0x8005) carry a caution too -- and it
 * is about WBA + WSI overflowing 256, not about latching. The one register block whose
 * caution does NOT mention the next line is the one that gates the display area
 * against the raster as it draws.
 *
 * ⚖️ AND A GAME SETTLES IT. Samurai Shodown! 2 hides the seam between its playfield and
 * its bottom HUD by writing WSI.H = 0 (window empty -> the whole line becomes the
 * out-of-window colour) from the H-blank handler, then putting 0xA0 back one line
 * later: a deliberate one-line blank. MEASURED (event log): the 0 lands on line 136 at
 * cycle 73, the 0xA0 on line 137 at cycle 74. Reading the window from the start-of-line
 * snapshot blanks line 137 -- which is already inside the black HUD, so the blank does
 * nothing -- and leaves the junk row that SCR1 draws on line 136 in plain sight. That is
 * the "badly rendered line above the bottom bar" of the bug report, and the layer mask
 * names the culprit: with SCR1 alone, line 136 is 8-colour garbage and line 138 is the
 * HUD. The game is blanking the junk; we were blanking the line after it.
 *
 * Live here means "as the line ENDED": render_scanline runs when the line's cycles have
 * elapsed, exactly like the palettes, the VRAM and the OAM this renderer already reads
 * live. Everything else stays on the snapshot, because for everything else the
 * manufacturer wrote the caution down. */
inline Regs regs_of_line(const Machine& m, uint32_t line) {
    const uint8_t* r = m.raster_log[line];   /* the 0x8000..0x803F block, as the line opened */
    Regs g;
    g.wba_h = m.mem[0x008002]; g.wba_v = m.mem[0x008003];
    g.wsi_h = m.mem[0x008004]; g.wsi_v = m.mem[0x008005];
    g.ctl2d = r[0x12];
    g.po_h  = r[0x20]; g.po_v  = r[0x21];
    g.scr2_in_front = (r[0x30] & 0x80) != 0;
    g.s1so_h = r[0x32]; g.s1so_v = r[0x33];
    g.s2so_h = r[0x34]; g.s2so_v = r[0x35];
    return g;
}

/* The colour a pixel value resolves to, for one plane and one palette code. */
struct PaletteView {
    bool     compat;
    uint32_t base;          /* K2GE: the plane's palette block. */
    uint32_t lut, cpal;     /* K1GE compat: the level LUT and the 12-bit palette. */
};

/* THE MONO NGP'S EIGHT GREYS. On a K1GE the LUT value IS the picture: the Tech Ref's
 * palette LUT (0x8100..) holds a 3-bit COLOUR CODE, "the smallest contrast change being
 * the LSB and the largest the MSB", and the panel turns it into a shade. There is no
 * colour RAM on that machine to hold a ramp -- so we do not read any, and no cartridge
 * write can flatten it. Values are the ones the retail BIOS programs for mono output. */
constexpr uint16_t kK1geGrey[8] = {0x0FFF, 0x0DDD, 0x0BBB, 0x0999,
                                   0x0666, 0x0444, 0x0222, 0x0000};

inline uint16_t resolve(const Machine& m, const PaletteView& pv,
                        unsigned code, unsigned value) {
    if (pv.compat) {
        /* Only the SINGLE P.C bit exists on the old machine: two palettes per plane. */
        const unsigned p_c = code & 1u;
        const unsigned level = m.mem[pv.lut + p_c * 4u + value] & 0x07u;
        /* A REAL K1GE stops here -- level -> grey, straight out of the panel. The
         * K2GE's compat mode instead sends that level through a 12-bit palette, which
         * is what lets an NGPC colourise a mono cartridge. Same LUT, two machines. */
        if (m.k1ge_console) return kK1geGrey[level];
        return color_at(m, pv.cpal + (p_c * 8u + level) * 2u);
    }
    return color_at(m, pv.base + code * 8u + value * 2u);
}

}  // namespace

/* ⚡ ONE LINE OF THE PICTURE, drawn with what the machine holds RIGHT NOW.
 *
 * Back to front (Tech Ref Figure 4):
 *   backdrop · sprites PR.C=1 · back plane · sprites PR.C=2 · front plane · sprites
 *   PR.C=3 · window clip (out-of-window colour) · NEG invert.
 */
void Machine::render_scanline(uint32_t line) {
    if (line >= kVisibleScanlines) return;
    const Regs g = regs_of_line(*this, line);
    /* ⚡ ON A K1GE THERE IS NO MODE BIT: the machine IS this mode.
     *
     * 0x87E2 is a K2GE register -- the mono NGP's own BIOS never writes it (it is not
     * in that console's address map at all, K1GE Tech Ref §3), and anything clearing
     * the video page puts it back to zero. Deriving the mode from that byte therefore
     * ran the MONO console through the COLOUR path, resolving every pixel with palettes
     * nobody had filled: the boot logo came up over the SNK wallpaper and the whole
     * screen was two tones. The console setting decides; the register only speaks for
     * a K2GE that was asked to imitate one. */
    const bool compat = k1ge_console || (mem[kK1geMode] & 0x80) != 0;

    uint16_t* row = &framebuffer[line * kScreenWidth];

    /* 1. THE BACKDROP. The Tech Ref reads "D7=1, D6=0 sets the BGC valid, other
     *    values set it not valid and the background colour is set to black"
     *    (4-6), and this core enforced that. But real games disagree: Ogre
     *    Battle Gaiden's intro writes a blue into 0x83E0[0], sets BGC = 0x00
     *    (D7=0), and expects a blue sky -- a black one would be a broken intro
     *    on the silicon it shipped on. The game is the authority over the manual
     *    here: the `(bgc & 0xC0) == 0x80` gate this core used to apply has to go.
     *    So the backdrop is the palette entry, unconditionally;
     *    a game that wants black simply leaves 0x83E0[index] black (the empty-
     *    memory cold start still resolves to 0, i.e. black). The enable bits do
     *    not gate the colour. */
    const uint8_t bgc = mem[kBgcRegister];
    const uint16_t backdrop = color_at(*this, kPaletteBg + (bgc & 0x07) * 2u);
    for (unsigned x = 0; x < kScreenWidth; ++x) row[x] = backdrop;

    /* --- the sprite line buffer -------------------------------------------------
     * Sprite 0 WINS -- WITHIN ITS PR.C GROUP. "During the write to the line buffer,
     * the hardware checks the priority [...] to avoid writing over previously written
     * data" (Tech Ref 4-3-3-1): a contested pixel belongs to the LOWEST OAM index.
     *
     * ⛔ THE OWNERSHIP USED TO BE ONE BUFFER FOR ALL THREE GROUPS, and a game convicted
     * it. Yahtzee (homebrew) draws its five dice as sprites 0..19 with PR.C = 2 and the
     * RED "this die is held" frame as sprites 20..27 with PR.C = 3, ON THE SAME PIXELS.
     * With one buffer the dice claim every pixel first and the frame is erased outright:
     * the selection indicator the player steers the game with never appears (BizHawk
     * shows it; measured against the player's own save state, savestates/yahtzee_08.s0).
     * A UI element that is invisible is not a rendering nuance, it is an unplayable
     * screen -- so the single buffer is what the evidence rejects, not the index rule.
     *
     * ⚖️ SO: one line buffer PER PR.C GROUP. Inside a group the lowest OAM index still
     * owns the pixel (the Sonic measurement that established that rule is untouched --
     * those sprites share a group); ACROSS groups, PR.C alone decides, which is exactly
     * what Figure 4 lays out and what the three composition passes below already do.
     * The manufacturer's own sentence says the check IS "the priority", and the figure
     * that spells the check out (Figure 3) is an image the text extraction dropped.
     *
     * The chain advances for EVERY entry, including hidden ones (PR.C = 0), so a
     * hidden anchor at the head of a group still positions its tail. */
    uint8_t  owner_value[3][kScreenWidth] = {};   /* [PR.C-1][x], 0 = unclaimed */
    uint16_t owner_color[3][kScreenWidth];

    const PaletteView spr_pv{compat, kPaletteSprite, kK1geLut[0], kK1gePal[0]};

    unsigned prev_h = 0, prev_v = 0;
    for (unsigned i = 0; i < 64; ++i) {
        const uint32_t o = kOamBase + i * 4u;
        const uint8_t attrib = mem[o + 1];
        const unsigned h_pos = mem[o + 2];
        const unsigned v_pos = mem[o + 3];
        const bool v_chain = (attrib >> 1) & 1u;
        const bool h_chain = (attrib >> 2) & 1u;

        const unsigned h = h_chain ? ((prev_h + h_pos) & 0xFFu) : h_pos;
        const unsigned v = v_chain ? ((prev_v + v_pos) & 0xFFu) : v_pos;
        prev_h = h; prev_v = v;

        const unsigned pr_c = (attrib >> 3) & 3u;
        if (pr_c == 0) continue;                       /* hidden -- but it anchored the chain */

        const unsigned screen_y = (v + g.po_v) & 0xFFu;
        /* ⚠️ The world is CYCLICAL: 256x256, of which 160x152 is shown (Tech Ref 3-1).
         * A sprite at y=249 hangs off the TOP and its last rows are ON screen. */
        const unsigned py = (line - screen_y) & 0xFFu;
        if (py >= 8) continue;                         /* this line misses the sprite */

        const unsigned screen_x = (h + g.po_h) & 0xFFu;
        const unsigned tile = (unsigned(attrib & 1u) << 8) | mem[o];
        const bool h_flip = (attrib >> 7) & 1u;
        const bool v_flip = (attrib >> 6) & 1u;
        const unsigned code = compat ? unsigned((attrib >> 5) & 1u)   /* P.C */
                                     : unsigned(mem[kOamCpcBase + i] & 0x0F);  /* CP.C */

        uint8_t px[8];
        tile_row(*this, tile, v_flip ? (7u - py) : py, px);

        for (unsigned i2 = 0; i2 < 8; ++i2) {
            const unsigned sx = (screen_x + i2) & 0xFFu;
            if (sx >= kScreenWidth) continue;
            if (owner_value[pr_c - 1u][sx]) continue;  /* a lower OAM index of THIS group took it */
            const unsigned value = px[h_flip ? (7u - i2) : i2];
            if (value == 0) continue;                  /* transparent: claims nothing */
            owner_value[pr_c - 1u][sx] = 1;
            owner_color[pr_c - 1u][sx] = resolve(*this, spr_pv, code, value);
        }
    }

    /* 🔍 The debug layer mask (machine.hpp) gates COMPOSITION, never the line buffer
     * above: sprite 0 still wins its pixel whether or not its priority group is shown,
     * so hiding the front sprites reveals the SCROLL PLANE underneath -- not whatever
     * sprite lost the pixel. Anything else would be inventing an image the chip cannot
     * produce, and the point of this tool is to show what is really there. */
    auto blit_sprites = [&](unsigned want_prc) {
        if (!(layer_mask & (kLayerSprBack << (want_prc - 1u)))) return;
        for (unsigned x = 0; x < kScreenWidth; ++x)
            if (owner_value[want_prc - 1u][x]) row[x] = owner_color[want_prc - 1u][x];
    };

    auto draw_plane = [&](bool scr1) {
        if (!(layer_mask & (scr1 ? kLayerScr1 : kLayerScr2))) return;
        const uint32_t map  = scr1 ? kScr1Map : kScr2Map;
        const unsigned soh  = scr1 ? g.s1so_h : g.s2so_h;
        const unsigned sov  = scr1 ? g.s1so_v : g.s2so_v;
        const PaletteView pv{compat, scr1 ? kPaletteScr1 : kPaletteScr2,
                             kK1geLut[scr1 ? 1 : 2], kK1gePal[scr1 ? 1 : 2]};

        const unsigned wy = (line + sov) & 0xFFu;      /* the plane is 256x256, cyclical */
        const unsigned ty = wy >> 3;
        const unsigned py = wy & 7u;

        for (unsigned x = 0; x < kScreenWidth; ++x) {
            const unsigned wx = (x + soh) & 0xFFu;
            const uint32_t e = map + ((ty * 32u) + (wx >> 3)) * 2u;
            const uint8_t attrib = mem[e + 1];
            /* ⛔ NO "TILE 0 IS BLANK" RULE. Character 0 is 16 bytes of character RAM
             * like any other; transparency is per-PIXEL (value 0). See pass 242. */
            const unsigned tile = (unsigned(attrib & 1u) << 8) | mem[e];
            const bool h_flip = (attrib >> 7) & 1u;
            const bool v_flip = (attrib >> 6) & 1u;

            uint8_t px[8];
            tile_row(*this, tile, v_flip ? (7u - py) : py, px);
            const unsigned value = px[h_flip ? (7u - (wx & 7u)) : (wx & 7u)];
            if (value == 0) continue;                  /* transparent */

            const unsigned code = compat ? unsigned((attrib >> 5) & 1u)      /* P.C */
                                         : unsigned((attrib >> 1) & 0x0F);   /* CP.C */
            row[x] = resolve(*this, pv, code, value);
        }
    };

    blit_sprites(1);
    draw_plane(g.scr2_in_front);            /* back plane: SCR1 when SCR2 is in front */
    blit_sprites(2);
    draw_plane(!g.scr2_in_front);           /* front plane */
    blit_sprites(3);

    /* 7. OUTSIDE THE WINDOW. Half-open [WBA, WBA+WSI). Cold start is WBA=0, WSI=0xFF,
     *    which covers the whole screen -- so this is a no-op on a fresh reset.
     *    The fill colour comes from the WINDOW palette block (0x83F0), NOT the
     *    backdrop block -- see kPaletteOow above (Fatal Fury's black letterbox). */
    const uint16_t oowc = color_at(*this, kPaletteOow + (g.ctl2d & 0x07) * 2u);
    const unsigned y_in = (line >= g.wba_v) && (line < unsigned(g.wba_v) + g.wsi_v);
    for (unsigned x = 0; x < kScreenWidth; ++x) {
        const bool x_in = (x >= g.wba_h) && (x < unsigned(g.wba_h) + g.wsi_h);
        if (!y_in || !x_in) row[x] = oowc;
    }

    /* 8. NEG. Bit 7 of the 2D control inverts every component of every pixel the LCD
     *    receives -- the out-of-window fill included, which is why it runs last. */
    if (g.ctl2d & 0x80) {
        for (unsigned x = 0; x < kScreenWidth; ++x) {
            const uint16_t c = row[x];
            const uint16_t r = uint16_t((c & 0x0F) ^ 0x0F);
            const uint16_t gg = uint16_t(((c >> 4) & 0x0F) ^ 0x0F);
            const uint16_t b = uint16_t(((c >> 8) & 0x0F) ^ 0x0F);
            row[x] = uint16_t((b << 8) | (gg << 4) | r);
        }
    }
}

}  // namespace ngpc
