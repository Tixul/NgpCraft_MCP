# Collision

AABB rectangle collision, tilemap collision (move + resolve), bullet pool, bitmap collision, performance rules, and known pitfalls.

> Note: All files use ASCII only (avoids encoding issues on Windows/PowerShell).

---


## 1. AABB Rectangle Collision

### 1.1 ngpc_aabb API

Module: `ngpc_aabb` — pure functions, zero global state, no dynamic allocation.

| Function | Description |
|----------|-------------|
| `ngpc_rect_overlap(a, b)` | Returns 1 if the two rects overlap |
| `ngpc_rect_contains(r, px, py)` | Returns 1 if point (px, py) is inside rect |
| `ngpc_rect_test(a, b, *out)` | Overlap + hit sides + push MTV in `NgpcCollResult` |
| `ngpc_rect_test_many(moving, list, n, *rx, *ry, *sides)` | One moving rect vs N static rects |
| `ngpc_swept_aabb(a, vx, vy, b, *out)` | Swept test for fast-moving projectiles |
| `ngpc_rect_push_x(a, b)` | Penetration depth on X axis only |
| `ngpc_rect_push_y(a, b)` | Penetration depth on Y axis only |
| `ngpc_rect_intersect(a, b, *out)` | Intersection rectangle |
| `ngpc_rect_offset(r, dx, dy)` | Shift a rect by (dx, dy) |

**Collision side flags:** `COL_LEFT`, `COL_RIGHT`, `COL_TOP`, `COL_BOTTOM`, `COL_ANY`

### 1.2 Usage Examples

**Player vs platform (rectangle test + push):**
```c
NgpcCollResult cr;
if (ngpc_rect_test(&player_rect, &platform_rect, &cr)) {
    player.x += cr.push_x;
    player.y += cr.push_y;
    if (cr.hit & COL_BOTTOM) on_ground = 1;
    if (cr.hit & COL_TOP)    vel_y = 0;
}
```

**Fast projectile vs enemy (swept AABB):**
```c
NgpcSweptResult sr;
ngpc_swept_aabb(&bullet_rect, bullet_vx, bullet_vy, &enemy_rect, &sr);
if (sr.hit) {
    /* sr.t  = exact collision time [0..FX_ONE] */
    /* sr.nx / sr.ny = collision normal */
    enemy_hp--;
}
```

---

## 2. Tilemap Collision

### 2.1 Tile Types

| Constant | Value | Behavior |
|----------|-------|----------|
| `TILE_PASS` | 0 | Passable, no collision |
| `TILE_SOLID` | 1 | Solid on all sides |
| `TILE_ONE_WAY` | 2 | One-way platform — solid from top only |
| `TILE_DAMAGE` | 3 | Passable, sets `result.in_damage` |
| `TILE_LADDER` | 4 | Ladder zone, sets `result.in_ladder` |
| 5..15 | — | Free for project use |

### 2.2 ngpc_tilecol API

Module: `ngpc_tilecol` — depends on `ngpc_aabb`. Zero bytes RAM (collision map is in the game).

| Function | Description |
|----------|-------------|
| `ngpc_tilecol_move(col, *rx, *ry, w, h, dx, dy, *res)` | **Move + resolve**, fills `NgpcMoveResult` |
| `ngpc_tilecol_on_ground(col, rx, ry, w, h)` | 1 if standing on solid or one-way tile |
| `ngpc_tilecol_on_ceiling(col, rx, ry, w, h)` | 1 if touching a ceiling |
| `ngpc_tilecol_on_wall_left(col, rx, ry, w, h)` | 1 if against a left wall |
| `ngpc_tilecol_on_wall_right(col, rx, ry, w, h)` | 1 if against a right wall |
| `ngpc_tilecol_ground_dist(col, rx, ry, w, h, max)` | Distance to the floor below |
| `ngpc_tilecol_rect_solid(col, wx, wy, w, h)` | Raw test: area vs solid tiles |
| `ngpc_tilecol_type_at(col, wx, wy)` | Tile type at a pixel position |

> **Constraint:** `|dx|` and `|dy|` must be ≤ 8 pixels/frame to avoid tunnel-through.
> At 60fps, 8px/frame = 480px/s — more than enough for any NGPC game.

### 2.3 NgpcMoveResult Fields

```c
res.sides      /* COL_* flags: which sides were blocked */
res.tile_x     /* type of the tile that blocked on X axis */
res.tile_y     /* type of the tile that blocked on Y axis */
res.in_ladder  /* 1 if inside a TILE_LADDER zone */
res.in_damage  /* 1 if inside a TILE_DAMAGE zone */
```

### 2.4 Full Platformer Example

```c
/* Collision map (1 byte per tile: 0=pass, 1=solid, 2=one-way) */
static const u8 s_map[MAP_W * MAP_H] = {
    1, 1, 1, 1, 1, 1, 1, 1,
    1, 0, 0, 0, 0, 0, 0, 1,
    1, 0, 0, 2, 2, 0, 0, 1,   /* 2 = one-way platform */
    1, 0, 0, 0, 0, 0, 0, 1,
    1, 1, 1, 1, 1, 1, 1, 1,
};
NgpcTileCol col = { s_map, MAP_W, MAP_H };

/* Per-frame update: */
vel.y = FX_MIN(FX_ADD(vel.y, GRAVITY), MAX_FALL);
s16 dx = FX_TO_INT(vel.x);
s16 dy = FX_TO_INT(vel.y);

NgpcMoveResult res;
ngpc_tilecol_move(&col, &px, &py, PW, PH, dx, dy, &res);

if (res.sides & COL_BOTTOM) { on_ground = 1; vel.y = 0; }
if (res.sides & COL_TOP)    { vel.y = 0; }
if (res.sides & (COL_LEFT | COL_RIGHT)) { vel.x = 0; }
if (res.in_damage) { player_hurt(); }
if (res.in_ladder) { can_climb = 1; }
```

### 2.5 RAM Cost

- Full screen (20×19): **380 bytes**
- Full 32×32 map: **1024 bytes** (watch the 12 KB budget)

---

## 3. Bullet Pool with Collision

### 3.1 ngpc_bullet API

Module: `ngpc_bullet` — depends on `ngpc_pool`, `ngpc_fixed`, `ngpc_aabb`.
Default RAM: `BULLET_POOL_SIZE × 12` bytes (default BULLET_POOL_SIZE = 16 → 192 bytes).

| Element | Description |
|---------|-------------|
| `NGPC_BULLET_POOL_INIT(pool)` | Initialize pool |
| `ngpc_bullet_spawn(pool, x, y, vx, vy, w, h, tile, pal, life, flags)` | Spawn a bullet; returns index or `POOL_NONE` |
| `ngpc_bullet_update(pool)` | Move + expire bullets (OOB or TTL=0); call once per frame |
| `ngpc_bullet_hits(pool, idx, target_rect)` | Test bullet vs target rect (AABB) |
| `ngpc_bullet_kill(pool, idx)` | Free a bullet after collision |
| `ngpc_bullet_px(b)` / `ngpc_bullet_py(b)` | Pixel position for `ngpc_sprite_set` |
| `BULLET_PLAYER` / `BULLET_ENEMY` | Flags to distinguish factions |

`life = 0` → bullet only expires when it goes off-screen.

### 3.2 Usage Example

```c
static NgpcBulletPool bullets;
NGPC_BULLET_POOL_INIT(&bullets);

/* Fire: */
ngpc_bullet_spawn(&bullets, hero.pos.x, hero.pos.y,
                  4, 0, 4, 4, BULLET_TILE, 1, 60, BULLET_PLAYER);

/* Per-frame: */
ngpc_bullet_update(&bullets);
POOL_EACH(&bullets.hdr, i) {
    NgpcBullet *b = &bullets.items[i];

    /* Render: */
    ngpc_sprite_set(SPR_BULLET + i, ngpc_bullet_px(b), ngpc_bullet_py(b),
                    b->tile, b->pal, SPR_FRONT);

    /* Enemy collision: */
    NgpcRect er = { ex, ey, 16, 16 };
    if ((b->flags & BULLET_PLAYER) && ngpc_bullet_hits(&bullets, i, &er)) {
        enemy_hurt();
        ngpc_bullet_kill(&bullets, i);
    }
}
```

**Optimization tip:** combine the move + collision + sprite_move passes into a single loop
instead of three separate passes. This avoids iterating the pool 3× per frame and reduces
VRAM writes — important for maintaining a stable frame rate on hardware.

---

## 4. Performance Rules

### Avoid sqrt()

`sqrt()` is very expensive on TLCS-900H. Use squared distance comparisons instead:

```c
/* Bad: */
if (sqrtf(dx*dx + dy*dy) < radius) { ... }

/* Good: */
u16 dist2 = (u16)(dx*dx) + (u16)(dy*dy);
if (dist2 < (u16)(radius * radius)) { ... }
```

Pattern from NGPC homebrews (radius-based sprite collision):
```c
bool SpriteCollision(u8 id1, u8 id2, u16 radius) {
    u16 x1 = sprites[id1].x + 1024;   /* +1024 centers on sprite midpoint */
    u16 x2 = sprites[id2].x + 1024;
    if (HorizontalDistance(x1, x2) < radius &&
        VerticalDistance(sprites[id1].y, sprites[id2].y) < radius) {
        return true;
    }
    return false;
}
```

Typical radius constants (in fixed-point units, 256ths or 128ths):
- Tight collision: `512`
- Normal collision: `1024..2048`
- Danger zone: `3072`

Keep the radius test entirely in fixed-point: compare per-axis absolute deltas
against the radius in fixed-point units. Never convert to pixels for collision —
the `>> 7` / `>> 8` truncation throws away sub-pixel precision and causes jitter at
contact boundaries.

### Broadphase

- Sort entities or use a spatial grid for broadphase before running AABB tests.
- Limit the number of collision pairs checked per frame.
- Prefer checking only active objects (skip idle/inactive pool entries).

### Separate Move and Resolve

Keep "move" (compute new position) and "resolve" (push out of overlap) clearly separated.
Mixing them in a loop can cause double-resolve and wasted CPU cycles.
`ngpc_tilecol_move()` does both in one atomic call — prefer it over manual two-pass resolution.

### Fixed Sprite Slots for Hot Paths

Avoid calling `ngpc_sprite_set()` every frame for objects that haven't changed tile or palette:
- **Spawn:** `ngpc_sprite_set()` — sets tile, palette, flags, position
- **Move only:** `ngpc_sprite_move()` — position update only (much cheaper)
- **Animation:** `ngpc_sprite_set_tile()` — only when the animation frame actually changes

This pattern eliminates redundant VRAM writes and is critical for stable 60fps with many active objects.

---

## 5. Bitmap Collision

Architecture confirmed by reverse engineering a commercial virtual-pet engine.
Simple O(n²) algorithm with a bitmap-encoded result — efficient for ≤ 64 objects.

### 5.1 Collision Entry Layout

Base address in the analyzed engine: `0x6200`. Each entry is 6 bytes:

```
+0x0  byte  Object ID (0..63)
+0x1  byte  Collision flags (bitmap: bit N = collided with object N)
+0x2  byte  Right edge  (X + width)
+0x3  byte  Left edge   (X)
+0x4  byte  Bottom edge (Y + height)
+0x5  byte  Top edge    (Y)
```

Active object count: `0x6280` (max 0x40 = 64).

### 5.2 Algorithm (4 AABB Tests)

```asm
; For each pair (A in XIZ, B in XIY):
cp (XIY+0x2), B    ; A.right >= B.left ?   if not -> no collision
cp (XIY+0x3), C    ; A.left <= B.right ?   if not -> no collision
cp (XIY+0x4), B    ; A.bottom >= B.top ?   if not -> no collision
cp (XIY+0x5), C    ; A.top <= B.bottom ?   if not -> no collision

; Collision detected: encode result in bitmap
ld C, 0x1
ld A, (XIZ+0x0)     ; ID_A
rlc A, C            ; C = 1 << ID_A
or (XIY+0x1), C     ; set collision bit in object B
or (XIZ+0x1), C     ; set collision bit in object A
```

### 5.3 C Equivalent Pattern

```c
typedef struct {
    u8 id;
    u8 col_flags;   /* bitmap: bit N = collided with object N */
    u8 right, left, bottom, top;
} ColEntry;

#define COL_MAX 64
static ColEntry col_list[COL_MAX];
static u8 col_count;

void col_clear(void) {
    u8 i;
    for (i = 0; i < col_count; i++)
        col_list[i].col_flags = 0;
}

void col_test_all(void) {
    u8 a, b;
    for (a = 0; a < col_count; a++) {
        ColEntry *ea = &col_list[a];
        for (b = (u8)(a + 1); b < col_count; b++) {
            ColEntry *eb = &col_list[b];
            if (ea->right  >= eb->left  &&
                ea->left   <= eb->right &&
                ea->bottom >= eb->top   &&
                ea->top    <= eb->bottom) {
                u8 bit_a = (u8)(1u << ea->id);
                eb->col_flags |= bit_a;
                ea->col_flags |= (u8)(1u << eb->id);
            }
        }
    }
}

/* Query after col_test_all(): */
if (ea->col_flags & (1u << PLAYER_ID)) {
    /* object A hit the player */
}
```

### 5.4 Typed Group-Enable Matrix

To gate which object types test against which, add an 8x8 (type x type) enable
table. Before testing a pair, look up `enable[type_a][type_b]` — if 0, skip the
pair entirely. Each object then records its hits as a **bit-per-type** mask
(distinct from the bit-per-object mask in §5.1).

```c
#define COL_TYPES 8
static u8 col_enable[COL_TYPES][COL_TYPES];  /* enable[a][b] = 1 -> test a vs b */

/* In the pair loop, before the AABB test: */
if (!col_enable[ea->type][eb->type]) continue;

/* On hit, record by type rather than by object id: */
ea->hit_types |= (u8)(1u << eb->type);
eb->hit_types |= (u8)(1u << ea->type);
```

**Trade-offs:**
- Caps at 8 types (8-bit mask), but keeps the O(N^2) inner loop cheap.
- Collision rules become data — the matrix can be swapped per scene without
  touching the test code (e.g. enable player-vs-pickup in one scene, disable it
  in another).
- Pairs of types that never interact are rejected with one table read, before any
  edge comparison.

### 5.5 Trade-offs

**Advantages:**
- No dynamic allocation — static array
- Result is queryable at any point (no callbacks)
- Max 64 objects comfortably fits NGPC games

**Limits:**
- O(n²) — fine for n ≤ 20 active simultaneously, monitor if n > 30
- 8-bit flags: IDs must be 0..7 (if `col_flags` is u8) or 0..63 (if 8 bytes of flags)

---

## 6. Known Bugs and Pitfalls

### Pitfall 1 — sqrt() Kills Performance

Never use `sqrt()`, `sqrtf()`, or `hypot()` in the game loop on TLCS-900H.
Always use squared distance comparisons (see §4).

### Pitfall 2 — u8 Overflow in Tilemap Index

If the tilemap collision map uses `u8 * width` for indexing and `y` is a `u8`:

```c
/* Bug: y=10, width=40: 10*40 = 400 -> truncated to u8 = 144 (wrong tile!) */
u8 idx = y * map_width + x;

/* Fix: */
u16 idx = (u16)y * map_width + x;
```

Always cast `y` to `u16` before multiplying by map width.
This is the same bug as in the tilemap render path (see [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) §8.1).

### Pitfall 3 — 32-bit Operations: Link Error C9H_divls / C9H_mulls

cc900 emits calls to `C9H_divls` and `C9H_mulls` for 32-bit division and multiplication.
These runtime helpers are **not linked** without `system.lib`.

**Do not use 32-bit operations in collision math.** Keep all arithmetic in 16-bit.
If a value could overflow 16 bits (e.g. `y * 100` for large coordinates), restructure
the calculation to divide first (see §4 and [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) §8.2).

### Pitfall 4 — Double-Resolve

Resolving the same collision twice in the same frame (once for X, once for Y, then re-running)
can push objects into walls or produce jitter.
Use `ngpc_tilecol_move()` which handles X and Y in one atomic pass.

### Pitfall 5 — Bullets: Single-Pass Update

For bullet collision, use a **single combined loop** (move + check collision + update sprite)
instead of three separate passes. Multiple passes over the bullet pool:
- Triple the VRAM writes
- Can cause "bullet time" lag on real hardware with moderate numbers of bullets

### Pitfall 6 — cc900/codegen: Initialized Declarations in Nested Blocks

**Symptom:** player apparently surrounded by an invisible 10×10 wall of solid tiles —
cannot move in any direction — after a seemingly unrelated camera or scene change
(observed in a top-down dungeon scene).

**Cause:** cc900 has a confirmed codegen bug when local variables are both **declared
and initialized inside a nested block** (e.g. `if (...) { s16 _dgn_dzx; _dgn_dzx = ...; }`).
The same bug is independently confirmed on the homemade `t900cc` codegen (crash on
`s8 != s8 || s8 != s8` and on decls initialized in nested blocks). In practice the
nested decl's stack slot can alias another local, so the camera or collision query
reads a stale / wrong coordinate.

**Fix — hoist declarations to the enclosing block, assign later:**
```c
/* FORBIDDEN — decl inside nested block */
if (sc->scene_flags & SCENE_FLAG_RUNTIME_DUNGEONGEN) {
    s16 _dgn_dzx; s16 _dgn_dzy; s16 _dgn_dmy;
    _dgn_dzx = (s16)sc->cam_follow_deadzone_x;
    ...
}

/* CORRECT — decl at function/block start, assign in nested block */
s16 player_world_x, player_world_y;
s16 _dgn_dzx, _dgn_dzy, _dgn_dmy;  /* hoisted */
...
if (sc->scene_flags & SCENE_FLAG_RUNTIME_DUNGEONGEN) {
    _dgn_dzx = (s16)sc->cam_follow_deadzone_x;   /* plain assignment OK */
    ...
}
```

### Pitfall 7 — Tilemap Cell Index: Unsigned Cast on Negative World Coordinates

**Symptom:** the player looks blocked as if wrapped by solid tiles at certain screen
positions (especially just after spawn or when moving toward world origin). The effect
only shows when `world_y + player_body_y` transiently goes negative.

**Cause:** a common tile cell lookup is:
```c
_mx0 = (u8)((u16)(_wx / _cell_w_px));
_my0 = (u8)((u16)((_wy + player_body_y) / _cell_h_px));
```
If `_wy + player_body_y` is **negative** (e.g. `_wy = 4`, `player_body_y = -8` → `-4`),
the cast `(u16)(-4)` yields `0xFFFC`. Dividing by `_cell_h_px = 16` gives a huge cell
index, which maps out of bounds. If `ngpc_dungeongen_collision_at()` (or any similar
OOB-unsafe tile query) returns `SOLID` for out-of-range cells, the player appears
trapped.

**Fix — guard the signed sum before casting:**
```c
{
    s16 _tmp = (s16)(_wy + (s16)player_body_y);
    _my0 = (_tmp >= 0) ? (u8)((u16)(_tmp / _cell_h_px)) : 0xFFu;
}
/* Downstream: treat 0xFF as OOB (i.e. SOLID, or pass-through, per project policy). */
```

General rule — any conversion `(u16)(signed_expr)` in a tile-index path must be preceded
by a non-negativity check on the signed result, otherwise negative coordinates silently
become huge unsigned indices.

---

## Quick Reference

| Item | Notes |
|------|-------|
| `ngpc_rect_test(a, b, *out)` | AABB + side flags + MTV push |
| `ngpc_swept_aabb(...)` | Fast-moving projectiles |
| `COL_LEFT/RIGHT/TOP/BOTTOM/ANY` | Side flags in NgpcCollResult |
| `ngpc_tilecol_move(...)` | Move + resolve in one call |
| `|dx|, |dy| <= 8 px/frame` | Anti-tunnel constraint |
| `TILE_PASS=0, SOLID=1, ONE_WAY=2, DAMAGE=3, LADDER=4` | Tile type constants |
| `ngpc_bullet_update()` | Move + expire bullets; call once/frame |
| `ngpc_bullet_hits(pool, idx, rect)` | Bullet vs target AABB |
| `BULLET_PLAYER / BULLET_ENEMY` | Faction flags |
| Avoid `sqrt()` | Use `dist² < radius²` instead |
| Avoid 32-bit ops | No `C9H_divls`/`C9H_mulls` without system.lib |
| Cast `y` to `u16` | Before any tilemap index multiplication |
| Bitmap collision | 6-byte entry, O(n²), max 64 objects |
| Single bullet loop | move + collision + sprite_move in one pass |
| Spawn = `set()`, Move = `move()` | Avoid redundant VRAM writes |
| Hoist nested initialized decls | cc900/codegen bug — decl at block start, assign later |
| Guard `(u16)(s16_expr)` in tile idx | Negative coord → huge OOB index → false SOLID |

---

## See Also

- [Sprites and OAM](../03_Graphics/Sprites-and-OAM.md) — Sprite slots, OAM budget, entity pool patterns
- [Game Loop](Game-Loop.md) — Frame budget, main loop structure, VBlank constraints
- [Tilemaps and Scrolling](../03_Graphics/Tilemaps-and-Scrolling.md) — Tilemap layout, u8 overflow bug (§8.1), large map streaming
- [Fixed-Point Math](Fixed-Point-Math.md) — Fixed-point arithmetic, FX_TO_INT, FX_ADD, etc.
- [Gameplay Patterns](../06_Pipeline-and-Patterns/Gameplay-Patterns.md) — Enemy patterns, wave spawning, hitbox design
