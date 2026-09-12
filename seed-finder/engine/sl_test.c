/* sl_test.c — native validation harness for seedlab.c (built with gcc). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "seedlab.c"

static void seed_parts(int64_t s, int *hi, int *lo)
{
    uint64_t u = (uint64_t)s;
    *hi = (int)(uint32_t)(u >> 32);
    *lo = (int)(uint32_t)u;
}

static void dump_seed(int64_t s)
{
    int hi, lo;
    seed_parts(s, &hi, &lo);
    printf("==== seed %lld ====\n", (long long)s);

    int sx, sz;
    sl_spawn(hi, lo, &sx, &sz);
    int sb = sl_biome_at(hi, lo, DIM_OVERWORLD, 1, sx, 320, sz);
    printf("spawn: %d, %d  biome#%d (%s)\n", sx, sz, sb, biome2str(g_mc, sb));

    int buf[4*400];
    int n = sl_structs(hi, lo, DIM_OVERWORLD, Village,
                       sx-1200, sz-1200, sx+1200, sz+1200, 400, buf);
    printf("villages within ~1200 of spawn: %d\n", n);
    for (int i = 0; i < n && i < 12; i++) {
        int x = buf[i*4], z = buf[i*4+1], var = buf[i*4+2], fl = buf[i*4+3];
        double d = sqrt((double)(x-sx)*(x-sx) + (double)(z-sz)*(z-sz));
        printf("  village %2d: %6d %6d  dist %7.1f  biome#%d%s\n",
               i, x, z, d, var, (fl & SL_F_ABANDONED) ? " ZOMBIE" : "");
    }

    int sh[2*128];
    int ns = sl_strongholds(hi, lo, -6000, -6000, 6000, 6000, 128, sh, 1);
    printf("strongholds in +-6000 box: %d\n", ns);
    for (int i = 0; i < ns && i < 5; i++) {
        double d = sqrt((double)sh[i*2]*sh[i*2] + (double)sh[i*2+1]*sh[i*2+1]);
        printf("  stronghold: %6d %6d  dist %7.1f\n", sh[i*2], sh[i*2+1], d);
    }

    int ac[4*64];
    int na = sl_structs(hi, lo, DIM_OVERWORLD, Ancient_City,
                        -4000, -4000, 4000, 4000, 64, ac);
    printf("ancient cities in +-4000 box: %d\n", na);
    for (int i = 0; i < na && i < 4; i++)
        printf("  ancient city: %6d %6d\n", ac[i*4], ac[i*4+1]);

    int tc[4*64];
    int nt = sl_structs(hi, lo, DIM_OVERWORLD, Trial_Chambers,
                        -4000, -4000, 4000, 4000, 64, tc);
    printf("trial chambers in +-4000 box: %d\n", nt);

    int ms[2*600];
    int nm = sl_mineshafts(hi, lo, -80, -80, 160, 160, 600, ms);
    printf("mineshafts in +-1280 blocks: %d\n", nm);

    int grid[64*64];
    int ng = sl_biome_grid(hi, lo, DIM_OVERWORLD, (sx>>2)-32, (sz>>2)-32,
                           64, 64, 160, grid, 4);
    printf("biome grid 64x64 samples: %d, [0]=%d\n", ng, grid[0]);
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    int v = sl_init(0);
    printf("engine version id=%d (MC_NEWEST=%d)\n", v, sl_version_newest());

    /* structure configs sanity: region sizes and salts must exist for 26.2 */
    const int types[] = { Village, Outpost, Mansion, Monument,
        Desert_Pyramid, Jungle_Temple, Swamp_Hut, Igloo, Ocean_Ruin,
        Shipwreck, Ruined_Portal, Ancient_City, Trail_Ruins, Trial_Chambers,
        Treasure, Fortress, Bastion, Ruined_Portal_N, End_City };
    for (size_t i = 0; i < sizeof(types)/sizeof(types[0]); i++) {
        StructureConfig sc;
        if (!getStructureConfig(types[i], v, &sc)) {
            printf("FAIL: no config for type %d\n", types[i]);
            return 1;
        }
    }
    printf("structure configs OK for all %zu types\n",
           sizeof(types)/sizeof(types[0]));

    dump_seed(12345);
    dump_seed(-4172144997902289642LL); /* random 64-bit */
    dump_seed(0);
    dump_seed(987654321);

    /* deterministic check: same seed twice -> same spawn */
    int ax, az, bx, bz;
    sl_spawn(0, 12345, &ax, &az);
    sl_spawn(0, 12345, &bx, &bz);
    if (ax != bx || az != bz) { printf("FAIL: nondeterministic spawn\n"); return 1; }

    printf("ALL TESTS DONE\n");
    return 0;
}
