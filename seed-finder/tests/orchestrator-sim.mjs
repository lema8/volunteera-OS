/* orchestrator-sim.mjs — tests js/search.js (SearchRunner) end-to-end with
 * real isolated workers (node:worker_threads running the real worker.js). */
import { Worker as NodeWorker } from 'node:worker_threads';

const entry = new URL('./fake-worker-entry.mjs', import.meta.url);

class FakeWorker {
  constructor() {
    this._w = new NodeWorker(entry);
    this.onmessage = null;
    this.onerror = null;
    this._w.on('message', (m) => this.onmessage?.({ data: m }));
    this._w.on('error', (e) => this.onerror?.({ message: String(e?.message || e) }));
  }
  postMessage(m) { this._w.postMessage(m); }
  terminate() { this._w.terminate(); }
}
globalThis.Worker = FakeWorker;

const { SearchRunner } = await import(new URL('../js/search.js', import.meta.url));
const { ST } = await import(new URL('../js/wasm-api.js', import.meta.url));

const villageCond = {
  id: 'v', kind: 'structure', key: 'village', struct: ST.VILLAGE, dim: 0,
  countMin: 1, countMax: null, distMin: 0, distMax: 1000,
  anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true,
};
const godConds = [
  { ...villageCond, countMin: 3 },
  { id: 'city', kind: 'structure', key: 'ancient_city', struct: ST.ANCIENT_CITY, dim: 0, countMin: 1, countMax: null, distMin: 0, distMax: 1500, anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true },
  { id: 'sh', kind: 'stronghold', key: 'stronghold', countMin: 1, countMax: null, distMin: 0, distMax: 2000, anchor: { kind: 'spawn' }, biomes: [], excludeBiomes: [], required: true },
];

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/* ---- test 1: finite count mode completes and finds matches ---- */
{
  const runner = new SearchRunner({ workers: 3, chunkSize: 512, stopOnFirstMatch: true });
  let lastStats = null, state = null, matches = 0;
  await runner.start({
    conditions: [villageCond], mode: 'count', count: 3000,
    startSeed: '0',
    stopOnMatch: false,
    onStats: s => { lastStats = s; },
    onMatch: () => { matches++; },
    onState: s => { state = s; },
  });
  const t0 = Date.now();
  while (runner.state !== 'idle' && Date.now() - t0 < 90000) await sleep(100);
  if (runner.state !== 'idle') throw new Error('TEST1: runner did not finish');
  console.log(`TEST1 count-mode: checked=${runner.totalChecked} matches=${runner.matches.length} state=${state} rate=${lastStats?.rate}/s`);
  if (runner.totalChecked < 2900 || runner.totalChecked > 3200) throw new Error('TEST1: checked count off: ' + runner.totalChecked);
  if (!runner.matches.length) throw new Error('TEST1: expected some village matches');
}

/* ---- test 2: infinite mode stops at first verified match (seed 3) ---- */
{
  const runner = new SearchRunner({ workers: 2, chunkSize: 256, stopOnFirstMatch: true });
  let match = null;
  await runner.start({
    conditions: godConds, mode: 'infinite', count: Infinity,
    startSeed: '0',
    stopOnMatch: true,
    onStats: () => {},
    onMatch: m => { match = m; },
    onState: () => {},
  });
  const t0 = Date.now();
  while (runner.state !== 'idle' && Date.now() - t0 < 90000) await sleep(100);
  if (!match) throw new Error('TEST2: no match found');
  console.log(`TEST2 infinite-mode: stopped on seed ${match.seed} score=${match.score} checked=${runner.totalChecked}`);
  if (match.seed !== '3') throw new Error('TEST2: expected seed 3, got ' + match.seed);
  if (runner.state !== 'idle') throw new Error('TEST2: did not return to idle');
}

/* ---- test 3: STOP mid-search ---- */
{
  const runner = new SearchRunner({ workers: 3, chunkSize: 1024, stopOnFirstMatch: true });
  await runner.start({
    conditions: [villageCond], mode: 'count', count: 5_000_000,
    startSeed: '1000000',
    stopOnMatch: false,
    onStats: () => {}, onMatch: () => {}, onState: () => {},
  });
  await sleep(2500);
  runner.stop();
  const t0 = Date.now();
  while (runner.state !== 'idle' && Date.now() - t0 < 20000) await sleep(100);
  console.log(`TEST3 stop: state=${runner.state} checked=${runner.totalChecked} (of 5,000,000)`);
  if (runner.state !== 'idle') throw new Error('TEST3: stop did not finish');
  if (runner.totalChecked >= 5_000_000) throw new Error('TEST3: ran to completion despite stop');
}

console.log('ORCHESTRATOR SIMULATION PASSED ✔');
process.exit(0);
