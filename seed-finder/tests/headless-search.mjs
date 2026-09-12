/* headless-search.mjs — runs the SAME engine code the website uses
 * (js/wasm-api.js + js/engine.js + wasm/seedlab.wasm) inside Node, and
 * performs a real staged search. No DOM needed. */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');

/* --- manual SeedLab instantiation (no fetch in node) --- */
const bytes = fs.readFileSync(path.join(root, 'wasm/seedlab.wasm'));
const WASI_STUB = new Proxy({}, { get: () => () => 0 });

const api = await import(path.join(root, 'js/wasm-api.js'));
const { instance } = await WebAssembly.instantiate(bytes, {
  wasi_snapshot_preview1: WASI_STUB, env: {},
});
const lab = new api.SeedLab(instance);
lab.init(0);
console.log('engine version id:', lab.version());

const { Evaluator, orderConditions, summarize } = await import(path.join(root, 'js/engine.js'));
const { ST } = api;

/* --- build the "God Seed" condition set, exactly like the UI presets --- */
function C(extra) { return { biomes: [], excludeBiomes: [], required: true, countMin: 1, countMax: null, distMin: 0, ...extra }; }
const conditions = [
  C({ id: 'vill', kind: 'structure', key: 'village', struct: ST.VILLAGE, dim: 0, countMin: 3, distMax: 1000, anchor: { kind: 'spawn' } }),
  C({ id: 'city', kind: 'structure', key: 'ancient_city', struct: ST.ANCIENT_CITY, dim: 0, distMax: 1500, anchor: { kind: 'spawn' } }),
  C({ id: 'sh',   kind: 'stronghold', key: 'stronghold', distMax: 2000, anchor: { kind: 'spawn' } }),
  C({ id: 'plns', kind: 'spawn_biome', required: false, biomes: [1] }),
];
const ordered = orderConditions(conditions);
const ev = new Evaluator(lab);

/* --- search: sequential from 0, stop at first verified match --- */
const N_MAX = 200000;
const t0 = Date.now();
let found = null, checked = 0;
let seed = 0n;
while (checked < N_MAX) {
  const u = BigInt.asUintN(64, seed);
  const i = BigInt.asIntN(64, u);
  const hi = Number(BigInt.asUintN(32, i >> 32n)) | 0;
  const lo = Number(BigInt.asUintN(32, i)) | 0;

  const fast = ev.evaluate(hi, lo, ordered, { exact: false });
  checked++;
  if (fast.match) {
    const ver = ev.evaluate(hi, lo, ordered, { exact: true });
    if (ver.match) { found = { hi, lo, seed: api.partsToSeedString(hi, lo), res: ver }; break; }
  }
  seed++;
}
const dt = (Date.now() - t0) / 1000;
console.log(`checked ${checked} seeds in ${dt.toFixed(1)}s (${Math.round(checked / dt)}/s, god-seed conditions, single thread)`);

if (!found) { console.log('no match within budget'); process.exit(0); }

const spawn = lab.spawn(found.hi, found.lo);
const spawnBiome = lab.biomeAt(found.hi, found.lo, 0, 1, spawn.x, 320, spawn.z);
console.log('\n==== VERIFIED MATCH ====');
console.log('seed:', found.seed, ' score:', found.res.score, ' perfect:', found.res.perfect);
console.log('spawn:', spawn.x, spawn.z, 'biome', spawnBiome);
const sum = summarize(found.res, ordered);
for (const c of ordered) {
  const d = sum.details[c.id];
  console.log(`- [${c.id}] ok=${d.ok} instances=${d.instances.length}`);
  for (const inst of d.instances.slice(0, 4)) {
    console.log(`    (${inst.x}, ${inst.z}) dist=${inst.dist ?? '-'}${inst.biome !== undefined ? ' biome#' + inst.biome : ''}`);
  }
}

/* sanity assertions */
const vill = sum.details['vill'];
if (!(vill.ok && vill.instances.length >= 3)) throw new Error('village condition not met?!');
const sh = sum.details['sh'];
if (!sh.ok || !sh.instances.length) throw new Error('stronghold condition not met?!');
for (const inst of vill.instances) {
  const d = Math.hypot(inst.x - spawn.x, inst.z - spawn.z);
  if (d > 1000.5) throw new Error('village outside radius');
}
const dSh = Math.hypot(sh.instances[0].x - spawn.x, sh.instances[0].z - spawn.z);
if (dSh > 2000.5) throw new Error('stronghold outside radius');
console.log('\nAll assertions passed ✔');
