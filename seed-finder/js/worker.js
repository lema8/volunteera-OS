/* worker.js — one search worker. Owns its own SeedLab (wasm) instance and
 * evaluates chunks of seeds, streaming progress + candidates back. */

import { SeedLab, partsToSeedString } from './wasm-api.js';
import { Evaluator, orderConditions, summarize } from './engine.js';

let lab = null;
let evaluator = null;
let stopFlag = false;

/* xorshift64* — deterministic per-worker random stream */
function makeRng(state) {
  let s = BigInt.asUintN(64, BigInt(state));
  if (s === 0n) s = 0x9e3779b97f4a7c15n;
  return function () {
    s ^= (s >> 12n); s = BigInt.asUintN(64, s);
    s ^= (s << 25n); s = BigInt.asUintN(64, s);
    s ^= (s >> 27n); s = BigInt.asUintN(64, s);
    return BigInt.asUintN(64, s * 0x2545f4914f6cdd1dn);
  };
}

function parts(v64) {
  const i = BigInt.asIntN(64, v64);
  return {
    hi: Number(BigInt.asUintN(32, i >> 32n)) | 0,
    lo: Number(BigInt.asUintN(32, i)) | 0,
  };
}

self.onmessage = async (e) => {
  const msg = e.data;
  if (msg.type === 'init') {
    try {
      lab = await SeedLab.load();
      evaluator = new Evaluator(lab);
      self.postMessage({ type: 'ready', simd: lab.simd, version: lab.version() });
    } catch (err) {
      self.postMessage({ type: 'error', message: String(err?.message || err) });
    }
    return;
  }
  if (msg.type === 'stop-current') { stopFlag = true; return; }
  if (msg.type === 'job') { runJob(msg.job); return; }
};

function runJob(job) {
  stopFlag = false;
  const ordered = orderConditions(job.conditions);
  const opts = { exact: !!job.exact };

  const partials = [];          /* best near-misses (by required-score) */
  const pushPartial = (seedStr, res) => {
    partials.push({ seed: seedStr, score: res.score, reqMet: res.reqMet, reqTotal: res.reqTotal });
    partials.sort((a, b) => b.score - a.score);
    if (partials.length > 8) partials.length = 8;
  };

  let checked = 0;
  const report = (done) => {
    self.postMessage({ type: 'progress', checked, partials: partials.splice(0), done });
  };

  const emitVerified = (seedStr, hi, lo, res) => {
    const spawn = lab.spawn(hi, lo);
    const spawnBiome = lab.biomeAt(hi, lo, 0, 1, spawn.x, 320, spawn.z);
    self.postMessage({
      type: 'match',
      match: {
        seed: seedStr,
        score: res.score,
        perfect: res.perfect,
        spawn: { x: spawn.x, z: spawn.z, biome: spawnBiome },
        details: summarize(res, ordered).details,
      },
    });
  };

  const checkSeed = (hi, lo, seedStr) => {
    const res = evaluator.evaluate(hi, lo, ordered, { exact: false });
    checked++;
    if (res.match) {
      /* Stage 3: exact verification pass (costly stronghold biome checks). */
      const ver = evaluator.evaluate(hi, lo, ordered, { exact: true });
      if (ver.match) {
        emitVerified(seedStr, hi, lo, ver);
        return 'match';
      }
      pushPartial(seedStr, ver);
    } else {
      pushPartial(seedStr, res);
    }
    return null;
  };

  if (job.mode === 'list') {
    for (const s of job.seeds) {
      if (stopFlag) break;
      const r = checkSeed(s.hi, s.lo, s.str);
      if (r === 'match' && job.stopOnMatch) break;
    }
    report(true);
    return;
  }

  if (job.mode === 'random') {
    const rng = makeRng(job.rngState);
    for (let i = 0; i < job.count; i++) {
      if (stopFlag) break;
      const v = rng();
      const { hi, lo } = parts(v);
      const r = checkSeed(hi, lo, partsToSeedString(hi, lo));
      if (r === 'match' && job.stopOnMatch) break;
      if ((i & 0xff) === 0xff) report(false);
    }
    report(true);
    return;
  }

  /* sequential / range */
  let cur = BigInt.asUintN(64, BigInt(job.start));
  const end = job.end != null ? BigInt(job.end) : null;
  for (let i = 0; i < job.count; i++) {
    if (stopFlag) break;
    if (end != null && BigInt.asIntN(64, cur) > end) break;
    const { hi, lo } = parts(cur);
    const r = checkSeed(hi, lo, partsToSeedString(hi, lo));
    if (r === 'match' && job.stopOnMatch) break;
    cur = BigInt.asUintN(64, cur + 1n);
    if ((i & 0xff) === 0xff) report(false);
  }
  report(true);
}
