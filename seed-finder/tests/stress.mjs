/* stress.mjs — kitchen-sink condition stress test over the real engine. */
import fs from 'fs';
const root = new URL('..', import.meta.url);
const bytes = fs.readFileSync(new URL('wasm/seedlab.wasm', root));
const api = await import(new URL('js/wasm-api.js', root));
const { instance } = await WebAssembly.instantiate(bytes, {
  wasi_snapshot_preview1: new Proxy({}, { get: () => () => 0 }),
});
const lab = new api.SeedLab(instance); lab.init(0);
const { Evaluator, orderConditions } = await import(new URL('js/engine.js', root));
const { ST } = api;

const conds = [
  { id: 'v', kind: 'structure', key: 'village', struct: ST.VILLAGE, dim: 0, countMin: 1, countMax: null, distMin: 0, distMax: 1500, anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true },
  { id: 'm', kind: 'structure', key: 'mineshaft', struct: ST.MINESHAFT, dim: 0, countMin: 1, countMax: null, distMin: 0, distMax: 800, anchor: { kind: 'condition', refId: 'v' }, biomes: [], excludeBiomes: [], required: false },
  { id: 'p', kind: 'structure', key: 'ruined_portal_n', struct: ST.RUINED_PORTAL_N, dim: -1, countMin: 1, countMax: null, distMin: 0, distMax: 1000, anchor: { kind: 'origin' }, biomes: [], excludeBiomes: [], required: false },
  { id: 'f', kind: 'structure', key: 'fortress', struct: ST.FORTRESS, dim: -1, countMin: 1, countMax: null, distMin: 0, distMax: 1200, anchor: { kind: 'origin' }, biomes: [], excludeBiomes: [], required: false },
  { id: 'b', kind: 'biome_area', required: true, biomes: [2, 17], distMax: 600, distMin: 0, anchor: { kind: 'spawn' } },
  { id: 'ba', kind: 'biome_at', required: false, biomes: [], excludeBiomes: [10], anchor: { kind: 'spawn' }, y: 320 },
  { id: 'sb', kind: 'spawn_biome', required: false, biomes: [1, 4, 29] },
  { id: 'sh', kind: 'stronghold', key: 'stronghold', countMin: 1, countMax: null, distMin: 0, distMax: 3000, anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true },
];
const ordered = orderConditions(conds);
const ev = new Evaluator(lab);
let s = 123456789n;
let errors = 0, matches = 0, checked = 0, flips = 0;
const t0 = Date.now();
for (let i = 0; i < 3000; i++) {
  s = BigInt.asUintN(64, s * 6364136223846793005n + 1442695040888963407n);
  const v = BigInt.asIntN(64, s);
  const hi = Number(BigInt.asUintN(32, v >> 32n)) | 0, lo = Number(BigInt.asUintN(32, v)) | 0;
  try {
    const r = ev.evaluate(hi, lo, ordered, { exact: false });
    checked++;
    if (r.match) {
      matches++;
      const ver = ev.evaluate(hi, lo, ordered, { exact: true });
      if (!ver.match) flips++;
    }
  } catch (e) { errors++; if (errors < 3) console.log('ERR', e.stack); }
}
const dt = (Date.now() - t0) / 1000;
console.log(`stress: ${checked} seeds, ${matches} fast-matches (${flips} flipped on exact verification), ${errors} errors, ${dt.toFixed(1)}s (${Math.round(checked / dt)}/s single-thread)`);
if (errors) process.exit(1);
