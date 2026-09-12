#!/usr/bin/env bash
# Builds the Seed Lab WASM engine from the vendored cubiomes sources.
#
# Toolchain: zig cc (wasm32-wasi). Install with:  pip install ziglang
#   (on this machine the binary is provided by the `ziglang` wheel:
#    ~/.local/lib/python3.11/site-packages/ziglang/zig)
#
# Output: ../wasm/seedlab.wasm   (~200 KB, no JS glue needed)
#
# The same sources are also compiled natively for the test harness:
#   gcc -O3 -fwrapv -o sl_test sl_test.c cubiomes/*.c -lm   (see tests.sh)
set -euo pipefail
cd "$(dirname "$0")"

ZIG="${ZIG:-zig}"
command -v "$ZIG" >/dev/null 2>&1 || \
    ZIG="$HOME/.local/lib/python3.11/site-packages/ziglang/zig"

OUT="../wasm/seedlab.wasm"
mkdir -p ../wasm

EXPORTS=""
for fn in sl_init sl_version sl_version_newest sl_spawn sl_biome_at \
          sl_biome_grid sl_structs sl_mineshafts sl_strongholds \
          sl_alloc sl_free; do
    EXPORTS="$EXPORTS -Wl,--export=$fn"
done

build_one() {
    local out="$1"; shift
    "$ZIG" cc -O3 -fwrapv -flto "$@" -target wasm32-wasi \
        seedlab.c \
        cubiomes/noise.c cubiomes/biomes.c cubiomes/layers.c \
        cubiomes/biomenoise.c cubiomes/generator.c cubiomes/finders.c \
        cubiomes/util.c \
        -Wl,--no-entry -Wl,--gc-sections -Wl,-O2 -Wl,--strip-all \
        -Wl,--initial-memory=33554432 -Wl,--max-memory=268435456 \
        $EXPORTS \
        -o "$out"
}

build_one "$OUT" -msimd128
build_one "../wasm/seedlab-nosimd.wasm"

ls -la ../wasm/
echo "build OK"
