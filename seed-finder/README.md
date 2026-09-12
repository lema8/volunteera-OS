# ⛰️ Seed Lab — Minecraft Java 26.2 Seed Finder

A fast, modern, **install-free** seed-finding website for **Minecraft Java Edition 26.2
("Chaos Cubed")**. Describe the world you want — structures, biomes, distances, and the
relationships between them — press **FIND SEED**, and watch millions of seeds get filtered
live in your browser.

No Java. No Minecraft. No Python. No accounts. The entire search runs locally:
a C world-generation engine compiled to **WebAssembly**, driven by a pool of Web Workers.

---

## Quick start (just open it)

Serve this folder with any static web server, e.g.:

```bash
python3 server.py 8000        # tiny dev server with correct .wasm MIME type
# → http://localhost:8000/
```

(or host `index.html` + the rest on any static host — GitHub Pages, Netlify, …).

For users with slow Wi-Fi: the whole app is ≈ **180 KB gzipped of JavaScript/CSS** plus a
≈ **170 KB gzipped WebAssembly engine** (downloaded once, then cached). No other downloads.

## What it does

* **Search modes** — fixed count, indefinite (until a match), custom seed range,
  random seeds, or specific seed(s) (numeric or text seeds, hashed like the game does).
* **Fully customizable conditions** — not hard-coded to one recipe:
  * Structures: Village, Stronghold, Ancient City, Trial Chambers, Woodland Mansion,
    Pillager Outpost, Ocean Monument, Desert Pyramid, Jungle Temple, Swamp Hut, Igloo,
    Shipwreck, Ocean Ruins, Ruined Portal, Trail Ruins, Buried Treasure, Mineshaft,
    Amethyst Geode, Nether Fortress, Bastion, End City.
  * Per structure: minimum/maximum count, min/max distance, and **anchor** —
    relative to world spawn, `(0,0)`, specific coordinates, or *another condition*
    (e.g. “2 villages within 500 blocks of the Ancient City”).
  * Village biome filtering (plains/desert/savanna/taiga/snowy variants).
  * Biomes: spawn-inside-biome, biome at a point (with Y level — including underground
    biomes like **Sulfur Caves**, new in 26.2), and “biome within N blocks of X”.
  * **OR groups** (“Desert Pyramid OR Jungle Temple”), excluded biomes (NOT),
    required vs. optional conditions with live **match scoring/ranking**.
* **Staged pipeline** — cheap checks (structure placement) reject seeds first;
  expensive checks (stronghold biome relocation, biome-area grids) run only on
  survivors; candidates get an **exact verification pass** before being reported.
* **Live stats** — seeds searched, seeds/sec, elapsed time, candidates found, best
  match %, progress bar, stop-at-any-time without reloading.
* **Results** — exact X/Z for every located feature, distances, copy buttons,
  `/tp` commands, JSON/CSV/TXT export, and a biome-colored map of the find.
* **Presets** — Early Game, God Seed, Speedrun, Cherry Grove Start, Custom.
* **Search history** (local only), re-runnable and deletable.
* **Optional AI assistant** — bring your own OpenRouter / OpenAI-compatible key;
  natural language → structured conditions you review and edit.
  The AI never runs a search by itself, and the key is stored only in your browser.

## Accuracy policy (non-negotiables)

| What | Status |
|---|---|
| Structure & stronghold placement, village biomes, biome layout | **Exact** for 26.2, computed by [cubiomes] with the final 26.2 climate table (`sulfur_caves`, id 187) and structure-set data verified unchanged from 1.21.1→26.2 |
| World spawn | **Estimated** — vanilla biome-fitness algorithm; the final grass/terrain height nudge is not modeled |
| Stronghold positions during bulk search | ±112 m approximation; **re-verified exactly** (stage 3) for every candidate before it is reported |
| Desert pyramid / jungle temple / mansion surface-height rule | Heuristic biome filter — flagged with ⚠ in results |
| Village **blacksmiths**, **End-portal eyes / 12-12 portals**, Y coordinates | **Not computable** by fast search — the UI lets you request them but results are always labeled “requires additional verification”, never claimed as verified |
| Version | The engine reports its version id; the UI warns loudly if it doesn't match 26.2. Nothing is silently substituted |

The engine never invents seeds or coordinates: every reported position comes from an
actual computation, and benchmark numbers in the UI are measured live.

## Repository layout

```
index.html            single-page UI (no build step, no framework)
styles.css            dark responsive theme
js/
  app.js              boot, search modes, settings, history, presets
  search.js           worker-pool orchestrator, stats, ranking
  worker.js           search worker (one wasm instance each)
  engine.js           staged condition evaluator (shared by workers)
  wasm-api.js         wasm loader + typed API over the C exports
  data.js             UI catalogs, condition factories, presets
  data-generated.js   biome names/colors + structure configs dumped from cubiomes
  ui-builder.js       dynamic condition builder (AND/OR/optional, anchors)
  ui-results.js       result cards, detail pane, map, export
  ai.js               optional NL → conditions assistant
wasm/
  seedlab.wasm        engine (SIMD) — ≈ 503 KB, ≈ 170 KB gzipped
  seedlab-nosimd.wasm fallback for old browsers
engine/
  seedlab.c           thin C bridge over cubiomes (the WASM API)
  cubiomes/           vendored cubiomes with 26.2 support (MIT, © Cubitect)
  build.sh            rebuilds the wasm (zig cc → wasm32-wasi, LTO)
  sl_test.c           native validation harness (gcc)
  sl_bench.c          native benchmark
  sl_dump.c           regenerates js/data-generated.js
tests/                headless Node tests over the real wasm + engine code
server.py             optional dev server
```

## Rebuilding the engine (optional — the wasm is committed)

```bash
pip install ziglang                        # provides clang targeting wasm
cd engine && ./build.sh                    # writes ../wasm/*.wasm
gcc -O3 -fwrapv -o sl_test sl_test.c cubiomes/*.c -lm && ./sl_test   # native checks
```

Note: `-fwrapv` is required (cubiomes relies on wrap-around arithmetic), and
`quadbase.c` is intentionally excluded (it needs pthreads and isn't used).

## Performance (measured, single core)

Native (gcc -O3) / WASM (Node) with the “God Seed” pipeline
(spawn + ≥3 villages ≤1000 + ancient city ≤1500 + stronghold ≤2000):

* native ≈ 290 seeds/s · wasm ≈ 240 seeds/s **per core**
* structure-only conditions (no spawn dependence) run several times faster
* the in-app **Benchmark engine** button measures your own hardware live;
  the worker count defaults to CPU cores − 1

Honest caveat: spawn-dependent searches are dominated by vanilla's spawn-fitness
algorithm (~3 ms); that cost is inherent to accuracy.

## Testing

```bash
node tests/headless-search.mjs   # full pipeline, finds & verifies a real match
node tests/worker-sim.mjs        # exercises the actual browser worker via a shim
node tests/stress.mjs            # 3000 seeds, every condition type, 0 errors expected
node tests/or-group.mjs          # OR-group semantics
```

[cubiomes]: https://github.com/Cubitect/cubiomes
