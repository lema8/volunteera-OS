/*
 * seedlab.c — thin C bridge over cubiomes for the browser Seed Laboratory.
 *
 * Compiled two ways:
 *   1. WebAssembly (wasm32-wasi via `zig cc`) — used by the website workers.
 *   2. Native (gcc) — used by sl_test.c for validation/benchmarks.
 *
 * Targets Minecraft Java Edition 26.2 ("Chaos Cubed") through the vendored
 * cubiomes sources in ./cubiomes (MIT license, see cubiomes/LICENSE).
 *
 * Seeds are passed as two 32-bit halves (hi, lo) because WebAssembly has no
 * i64 arguments in the plain JS API. Output buffers are wasm-linear-memory
 * pointers allocated by the caller through sl_alloc().
 */

#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>

#include "cubiomes/finders.h"
#include "cubiomes/generator.h"
#include "cubiomes/util.h"
#include "cubiomes/rng.h"

#ifdef __wasm__
#define EXPORT __attribute__((used))

#include <stdio.h>
#include <stdarg.h>
/* cubiomes uses fprintf(stderr, ...) for error paths. Providing a no-op
 * stub keeps the (large) libc printf machinery out of the wasm binary. */
int fprintf(FILE *stream, const char *fmt, ...) { (void)stream; (void)fmt; return 0; }
int fflush(FILE *stream) { (void)stream; return 0; }

#else
#define EXPORT
#endif

/* result flags written into the 4th slot of each structure record */
enum {
    SL_F_ABANDONED   = 1 << 0,  /* zombie village / abandoned variant   */
    SL_F_HEURISTIC   = 1 << 1,  /* placement relied on terrain heuristic */
};

static Generator gOw;   /* overworld */
static Generator gNet;  /* nether    */
static Generator gEnd;  /* the end   */
static int g_mc = MC_26_2;

/* per-generator seed cache: applySeed() is skipped when the same seed is
 * requested repeatedly (the common case inside one seed's evaluation). */
static uint64_t cSeedOw, cSeedNet, cSeedEnd;
static int cHasOw, cHasNet, cHasEnd;

static void owApply(uint64_t s)
{
    if (!cHasOw || cSeedOw != s) {
        applySeed(&gOw, DIM_OVERWORLD, s);
        cSeedOw = s; cHasOw = 1;
    }
}
static void netApply(uint64_t s)
{
    if (!cHasNet || cSeedNet != s) {
        applySeed(&gNet, DIM_NETHER, s);
        cSeedNet = s; cHasNet = 1;
    }
}
static void endApply(uint64_t s)
{
    if (!cHasEnd || cSeedEnd != s) {
        applySeed(&gEnd, DIM_END, s);
        cSeedEnd = s; cHasEnd = 1;
    }
}

static inline uint64_t mkseed(int shi, int slo)
{
    return ((uint64_t)(uint32_t)shi << 32) | (uint32_t)slo;
}

static Generator *pickGen(int dim)
{
    if (dim == DIM_NETHER) return &gNet;
    if (dim == DIM_END)    return &gEnd;
    return &gOw;
}

/* ------------------------------------------------------------------ */

EXPORT int sl_init(int mcver)
{
    if (mcver <= 0)
        mcver = MC_NEWEST;              /* site default: 26.2 */
    g_mc = mcver;
    setupGenerator(&gOw,  mcver, 0);
    setupGenerator(&gNet, mcver, 0);
    setupGenerator(&gEnd, mcver, 0);
    cHasOw = cHasNet = cHasEnd = 0;
    return mcver;
}

EXPORT int sl_version(void)
{
    return g_mc;
}

EXPORT int sl_version_newest(void)
{
    return MC_NEWEST;
}

EXPORT void *sl_alloc(int n)
{
    if (n <= 0) return 0;
    return malloc((size_t)n);
}

EXPORT void sl_free(void *p)
{
    free(p);
}

/* ------------------------------------------------------------------ */
/* Spawn                                                              */
/* ------------------------------------------------------------------ */

/* Estimated world spawn (biome based, as used by vanilla's spawn locator
 * before the terrain height check). Returns 1 on success. */
EXPORT int sl_spawn(int shi, int slo, int *ox, int *oz)
{
    uint64_t seed = mkseed(shi, slo);
    owApply(seed);
    Pos p = estimateSpawn(&gOw, NULL);
    *ox = p.x;
    *oz = p.z;
    return 1;
}

/* ------------------------------------------------------------------ */
/* Biomes                                                             */
/* ------------------------------------------------------------------ */

/* Biome id at a scaled position (scale 1 = blocks, 4 = biome coords). */
EXPORT int sl_biome_at(int shi, int slo, int dim, int scale,
                       int x, int y, int z)
{
    uint64_t seed = mkseed(shi, slo);
    Generator *g = pickGen(dim);
    if (dim == DIM_NETHER) netApply(seed);
    else if (dim == DIM_END) endApply(seed);
    else owApply(seed);
    return getBiomeAt(g, scale, x, y, z);
}

/* Biome ids over a rectangular area at scale 4 (1 sample per 4x4 blocks).
 * (x0,z0) is the north-west corner in *biome* coordinates, (w,h) the size
 * in samples. y is the vertical biome coordinate (block-y / 4).
 * out must hold w*h ints. Returns w*h, or negative on failure. */
EXPORT int sl_biome_grid(int shi, int slo, int dim,
                         int x0, int z0, int w, int h, int y, int *out,
                         int scale)
{
    uint64_t seed = mkseed(shi, slo);
    Generator *g = pickGen(dim);
    if (dim == DIM_NETHER) netApply(seed);
    else if (dim == DIM_END) endApply(seed);
    else owApply(seed);

    if (scale != 4 && scale != 16 && scale != 64 && scale != 256)
        scale = 4;

    Range r;
    memset(&r, 0, sizeof(r));
    r.scale = scale;
    r.x = x0; r.z = z0;
    r.sx = w; r.sz = h;
    r.y = y;  r.sy = 1;

    int *cache = allocCache(g, r);
    if (!cache)
        return -1;
    int ret = genBiomes(g, cache, r);
    if (ret != 0) {
        free(cache);
        return -1;
    }
    memcpy(out, cache, (size_t)w * (size_t)h * sizeof(int));
    free(cache);
    return w * h;
}

/* ------------------------------------------------------------------ */
/* Structures                                                         */
/* ------------------------------------------------------------------ */

static int struct_uses_terrain_heuristic(int type)
{
    /* 1.18+ pyramids/temples/mansions require a sufficiently high surface;
     * cubiomes approximates this with a biome based heuristic. */
    return type == Desert_Pyramid || type == Jungle_Temple ||
           type == Mansion || type == End_City;
}

/* Find instances of a structure type inside the block-coordinate box
 * [x0..x1] x [z0..z1]. Each result is written as (x, z, variant, flags)
 * with stride 4. For villages, variant is the village biome id and
 * flags bit0 marks zombie (abandoned) villages. Returns the number of
 * results. The box is padded by one chunk so structures whose origin sits
 * just outside the box are still found. */
EXPORT int sl_structs(int shi, int slo, int dim, int type,
                      int x0, int z0, int x1, int z1,
                      int maxout, int *out)
{
    uint64_t seed = mkseed(shi, slo);
    Generator *g = pickGen(dim);
    if (dim == DIM_NETHER) netApply(seed);
    else if (dim == DIM_END) endApply(seed);
    else owApply(seed);

    StructureConfig sc;
    if (!getStructureConfig(type, g_mc, &sc))
        return 0;
    if (sc.dim != dim)
        return 0;
    if (maxout <= 0)
        return 0;

    x0 -= 16; z0 -= 16; x1 += 16; z1 += 16;

    int rs = sc.regionSize * 16;             /* region size in blocks */
    int rx0 = floordiv(x0, rs), rx1 = floordiv(x1, rs);
    int rz0 = floordiv(z0, rs), rz1 = floordiv(z1, rs);

    int n = 0;
    Pos pos;
    for (int rz = rz0; rz <= rz1 && n < maxout; rz++) {
        for (int rx = rx0; rx <= rx1 && n < maxout; rx++) {
            if (!getStructurePos(type, g_mc, seed, rx, rz, &pos))
                continue;
            if (pos.x < x0 || pos.x > x1 || pos.z < z0 || pos.z > z1)
                continue;
            if (!isViableStructurePos(type, g, pos.x, pos.z, 0))
                continue;

            int flags = 0;
            if (g_mc >= MC_1_18 && struct_uses_terrain_heuristic(type)) {
                if (type != End_City &&
                    !isViableStructureTerrain(type, g, pos.x, pos.z))
                    continue;
                flags |= SL_F_HEURISTIC;
            }

            int variant = -1;
            if (type == Village) {
                int bid = getBiomeAt(g, 4, pos.x >> 2, 80, pos.z >> 2);
                if (bid >= 0 && isViableFeatureBiome(g_mc, Village, bid)) {
                    StructureVariant sv;
                    if (getVariant(&sv, Village, g_mc, seed,
                                   pos.x, pos.z, bid)) {
                        variant = sv.biome;
                        if (sv.abandoned)
                            flags |= SL_F_ABANDONED;
                    }
                }
            }

            out[n*4+0] = pos.x;
            out[n*4+1] = pos.z;
            out[n*4+2] = variant;
            out[n*4+3] = flags;
            n++;
        }
    }
    return n;
}

/* Mineshafts are chunk-random, not region based. (cx0,cz0)+(cw,ch) describe
 * a chunk area. Outputs block coordinates (chunk * 16) with stride 2. */
EXPORT int sl_mineshafts(int shi, int slo,
                         int cx0, int cz0, int cw, int ch,
                         int maxout, int *out)
{
    uint64_t seed = mkseed(shi, slo);
    Pos tmp[512];
    int n = getMineshafts(g_mc, seed, cx0, cz0, cw, ch, tmp, 512);
    if (n > maxout)
        n = maxout;
    for (int i = 0; i < n; i++) {
        out[i*2+0] = tmp[i].x * 16;
        out[i*2+1] = tmp[i].z * 16;
    }
    return n;
}

/* Stronghold positions whose positions fall inside the padded box
 * [x0..x1] x [z0..z1]. Output stride 2. Iteration stops once stronghold
 * rings move beyond the reach of the box.
 * exact=1 performs the vanilla biome check for each stronghold (accurate
 * position, expensive). exact=0 skips it and returns the ring approximation
 * (+/-112 blocks), which is ~100x cheaper and intended for bulk filtering;
 * candidates must be re-checked with exact=1 before being reported. */
EXPORT int sl_strongholds(int shi, int slo,
                          int x0, int z0, int x1, int z1,
                          int maxout, int *out, int exact)
{
    uint64_t seed = mkseed(shi, slo);
    owApply(seed);

    double cx = (x0 + x1) * 0.5, cz = (z0 + z1) * 0.5;
    double halfw = (x1 - x0) * 0.5 + 128, halfh = (z1 - z0) * 0.5 + 128;
    double reach = sqrt(cx*cx + cz*cz) + sqrt(halfw*halfw + halfh*halfh) + 256;

    StrongholdIter it;
    initFirstStronghold(&it, g_mc, seed);

    int n = 0;
    for (int i = 0; i < 128; i++) {
        int remaining = nextStronghold(&it, exact ? &gOw : NULL);

        if (n < maxout &&
            it.pos.x >= x0 - 128 && it.pos.x <= x1 + 128 &&
            it.pos.z >= z0 - 128 && it.pos.z <= z1 + 128) {
            out[n*2+0] = it.pos.x;
            out[n*2+1] = it.pos.z;
            n++;
        }
        if (remaining <= 0)
            break;
        double nx = it.nextapprox.x, nz = it.nextapprox.z;
        if (sqrt(nx*nx + nz*nz) - 256 > reach)
            break;
    }
    return n;
}
