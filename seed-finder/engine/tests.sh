#!/usr/bin/env bash
# Native validation for the engine sources (run from engine/).
set -euo pipefail
cd "$(dirname "$0")"

SRC="cubiomes/noise.c cubiomes/biomes.c cubiomes/layers.c cubiomes/biomenoise.c \
     cubiomes/generator.c cubiomes/finders.c cubiomes/util.c"

echo "== compiling native test harness =="
gcc -O3 -fwrapv -o sl_test sl_test.c $SRC -lm
./sl_test

echo
echo "== native benchmark (20k seeds, god-seed pipeline) =="
gcc -O3 -fwrapv -o sl_bench sl_bench.c $SRC -lm
./sl_bench 20000

echo
echo "== regenerating UI catalog =="
gcc -O2 -fwrapv -o sl_dump sl_dump.c $SRC -lm
./sl_dump > ../js/data-generated.js
head -4 ../js/data-generated.js

echo "ALL NATIVE TESTS PASSED"
