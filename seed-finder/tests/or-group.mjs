import fs from 'fs';
const root = new URL('..', import.meta.url);
const bytes = fs.readFileSync(new URL('wasm/seedlab.wasm', root));
const api = await import(new URL('js/wasm-api.js', root));
const { instance } = await WebAssembly.instantiate(bytes, { wasi_snapshot_preview1: new Proxy({}, { get: () => () => 0 }) });
const lab = new api.SeedLab(instance); lab.init(0);
const { Evaluator, orderConditions } = await import(new URL('js/engine.js', root));
const { ST } = api;

const orGroup = {
  id: 'grp', kind: 'or', required: true, anchor: { kind: 'spawn' },
  children: [
    { id: 'dp', kind: 'structure', key: 'desert_pyramid', struct: ST.DESERT_PYRAMID, dim: 0, countMin: 1, countMax: null, distMin: 0, distMax: 1500, anchor: null, biomes: [], excludeBiomes: [] },
    { id: 'jt', kind: 'structure', key: 'jungle_temple', struct: ST.JUNGLE_TEMPLE, dim: 0, countMin: 1, countMax: null, distMin: 0, distMax: 1500, anchor: null, biomes: [], excludeBiomes: [] },
  ],
};
const ordered = orderConditions([orGroup]);
const ev = new Evaluator(lab);
let matches = 0, checked = 0, sample = null;
for (let i = 0; i < 400; i++) {
  const u = BigInt.asUintN(64, BigInt(i) * 0x9E3779B97F4A7C15n + 7n);
  const v = BigInt.asIntN(64, u);
  const hi = Number(BigInt.asUintN(32, v >> 32n)) | 0, lo = Number(BigInt.asUintN(32, v)) | 0;
  const r = ev.evaluate(hi, lo, ordered, { exact: false });
  checked++;
  if (r.match) { matches++; if (!sample) sample = { seed: api.partsToSeedString(hi, lo), r }; }
}
console.log(`OR-group: ${matches}/${checked} seeds matched`);
if (!sample) throw new Error('no OR matches at all — suspicious');
const d = sample.r.details.get('grp');
console.log('sample match seed', sample.seed, '->', JSON.stringify(d.instances[0]));
if (!d.ok || !d.instances.length) throw new Error('or result malformed');
const sp = lab.spawn(...(() => { const u = BigInt.asIntN(64, BigInt(sample.seed)); return [Number(BigInt.asUintN(32, u >> 32n)) | 0, Number(BigInt.asUintN(32, u)) | 0]; })());
const dist = Math.hypot(d.instances[0].x - sp.x, d.instances[0].z - sp.z);
console.log('distance from spawn:', Math.round(dist));
if (dist > 1500.5) throw new Error('OR instance outside radius');
console.log('OR GROUP TEST PASSED ✔');
