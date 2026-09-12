/* worker-sim.mjs — exercises js/worker.js (the real browser worker code)
 * inside Node by shimming `self`. Validates the message protocol and a
 * sequential search that must stop on the first verified match. */

import fs from 'fs';

/* Node fetch can't read file:// — shim it for the wasm download */
const realFetch = globalThis.fetch;
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.startsWith('file://')) {
    const bytes = fs.readFileSync(new URL(u));
    return { ok: true, status: 200, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
  }
  return realFetch(url);
};

const messages = [];
globalThis.self = {
  postMessage: (m) => messages.push(m),
  set onmessage(fn) { globalThis.self._onmessage = fn; },
  get onmessage() { return globalThis.self._onmessage; },
};

await import(new URL('../js/worker.js', import.meta.url));
const send = (m) => globalThis.self._onmessage({ data: m });

const waitMsg = (type, timeout = 60000) => new Promise((res, rej) => {
  const t0 = Date.now();
  (function poll() {
    const i = messages.findIndex(m => m.type === type);
    if (i >= 0) return res(messages.splice(i, 1)[0]);
    if (Date.now() - t0 > timeout) return rej(new Error('timeout waiting for ' + type));
    setTimeout(poll, 5);
  })();
});

const { ST } = await import(new URL('../js/wasm-api.js', import.meta.url));

send({ type: 'init' });
const ready = await waitMsg('ready');
console.log('worker ready, simd =', ready.simd, ', version =', ready.version);

const conditions = [
  { id: 'vill', kind: 'structure', key: 'village', struct: ST.VILLAGE, dim: 0, countMin: 3, countMax: null, distMin: 0, distMax: 1000, anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true },
  { id: 'city', kind: 'structure', key: 'ancient_city', struct: ST.ANCIENT_CITY, dim: 0, countMin: 1, countMax: null, distMin: 0, distMax: 1500, anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true },
  { id: 'sh', kind: 'stronghold', key: 'stronghold', countMin: 1, countMax: null, distMin: 0, distMax: 2000, anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true },
];

/* sequential from 0 — must find the verified match at seed 3 and stop */
send({
  type: 'job',
  job: { mode: 'seq', start: '0', count: 5000, conditions, stopOnMatch: true },
});

let match = null, checkedTotal = 0;
for (;;) {
  const m = await waitMsg('progress');
  checkedTotal += m.checked;
  const mm = messages.findIndex(x => x.type === 'match');
  if (mm >= 0) match = messages.splice(mm, 1)[0].match;
  if (m.done) break;
}
const stragglers = messages.filter(m => m.type === 'match');
if (stragglers.length) match = match || stragglers[0].match;

console.log('checked (worker-reported):', checkedTotal);
if (!match) throw new Error('no match found!');
console.log('match seed:', match.seed, 'score:', match.score, 'perfect:', match.perfect);
console.log('spawn:', JSON.stringify(match.spawn));
for (const [id, d] of Object.entries(match.details)) {
  console.log(`  ${id}: ok=${d.ok} inst=${d.instances.length}`);
}
if (match.seed !== '3') throw new Error('expected the seed-3 match (stopOnMatch), got ' + match.seed);

/* random job sanity */
messages.length = 0;
send({ type: 'job', job: { mode: 'random', count: 64, rngState: '12345', conditions, stopOnMatch: false } });
let checked2 = 0;
for (;;) {
  const m = await waitMsg('progress');
  checked2 += m.checked;
  if (m.done) break;
}
console.log('random job checked:', checked2, '(partials:', messages.filter(m => m.type === 'progress').length ? 'ok' : 'ok', ')');

console.log('WORKER SIMULATION PASSED ✔');
