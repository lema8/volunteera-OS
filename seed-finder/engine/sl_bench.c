/* sl_bench.c — honest native benchmark for a typical search pipeline:
 * spawn + >=2 villages within 1000 + stronghold within 2000 of spawn. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>

#include "seedlab.c"

int main(int argc, char **argv)
{
    int n = argc > 1 ? atoi(argv[1]) : 20000;
    int stage2 = 1; /* run stronghold stage on surviving seeds */
    sl_init(0);

    uint64_t s = 0x123456789abcdefULL;
    int matches = 0, stage2hits = 0;
    int buf[4*256];
    int sh[2*128];
    struct timespec t0, t1;

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < n; i++) {
        s = s * 6364136223846793005ULL + 1442695040888963407ULL;
        int hi = (int)(uint32_t)(s >> 32), lo = (int)(uint32_t)s;
        int sx, sz;
        sl_spawn(hi, lo, &sx, &sz);
        int nv = sl_structs(hi, lo, DIM_OVERWORLD, Village,
                            sx-1000, sz-1000, sx+1000, sz+1000, 256, buf);
        int c = 0;
        for (int j = 0; j < nv; j++) {
            int dx = buf[j*4] - sx, dz = buf[j*4+1] - sz;
            if ((int64_t)dx*dx + (int64_t)dz*dz <= 1000LL*1000LL) c++;
        }
        if (c >= 2) {
            stage2hits++;
            if (stage2) {
                int ns = sl_strongholds(hi, lo, sx-2000, sz-2000,
                                        sx+2000, sz+2000, 128, sh, 0);
                if (ns > 0) matches++;
            }
        }
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double dt = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
    printf("checked=%d stage2=%d matches=%d time=%.2fs rate=%.0f seeds/s\n",
           n, stage2hits, matches, dt, n / dt);
    return 0;
}
