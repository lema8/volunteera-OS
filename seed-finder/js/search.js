/* search.js — orchestrates a pool of search workers, dispatches seed
 * chunks, aggregates live stats and the candidate leaderboard. */

const WORKER_URL = new URL('./worker.js', import.meta.url).href;

export class SearchRunner {
  constructor(settings) {
    this.settings = settings;         /* { workers, chunkSize, stopOnFirstMatch } */
    this.workers = [];
    this.state = 'idle';                /* idle | starting | running | stopping */
    this.totalChecked = 0;
    this.matches = [];                  /* verified matches */
    this.matchSeeds = new Set();
    this.partials = [];                 /* leaderboard */
    this._remaining = 0;
    this._dispatched = 0;
    this._jobCounter = 0;
    this._mode = null;
    this._conditions = null;
    this._startSeed = '0';
    this._endSeed = null;
    this._nextSeq = 0n;
    this._onStats = null;
    this._onMatch = null;
    this._onState = null;
    this._rateTimer = null;
    this._lastChecked = 0;
    this._lastT = 0;
    this._rate = 0;
    this._startTime = 0;
  }

  async start({ conditions, mode, count, startSeed = '0', endSeed = null, seeds = null,
                onStats, onMatch, onState, stopOnMatch }) {
    if (this.state === 'running' || this.state === 'starting') return;
    this.state = 'starting';
    this._onStats = onStats; this._onMatch = onMatch; this._onState = onState;
    this._mode = mode; this._conditions = conditions;
    this._startSeed = startSeed; this._endSeed = endSeed;
    this._remaining = count;
    this._seedList = seeds;
    this._stopOnMatch = stopOnMatch;
    this._dispatched = 0;
    this._nextSeq = BigInt(startSeed);
    this.totalChecked = 0;
    this._lastChecked = 0;
    this._rate = 0;
    this._startTime = performance.now();
    onState?.('starting');

    const n = Math.max(1, this.settings.workers | 0);
    this.workers = [];
    let ready = 0;
    await new Promise((resolve, reject) => {
      for (let i = 0; i < n; i++) {
        const w = new Worker(WORKER_URL, { type: 'module' });
        w._busy = false;
        w._idx = i;
        w.onmessage = (e) => this._onWorkerMsg(w, e.data, () => {
          ready++;
          if (ready === n) resolve();
        });
        w.onerror = (e) => reject(new Error('Worker failed: ' + e.message));
        w.postMessage({ type: 'init' });
        this.workers.push(w);
      }
    });

    this.state = 'running';
    onState?.('running');
    this._lastT = performance.now();
    this._rateTimer = setInterval(() => this._tickStats(), 500);
    for (const w of this.workers) this._giveWork(w);
  }

  _finiteTotal() {
    if (this._mode === 'count' || this._mode === 'random') return this._remaining0 ?? (this._remaining0 = this._remaining);
    if (this._mode === 'range') return null; /* computed lazily */
    if (this._mode === 'specific') return this._seedList.length;
    return null; /* infinite */
  }

  _makeJob(w) {
    const c = this.settings.chunkSize | 0 || 1024;
    const base = { conditions: this._conditions, stopOnMatch: !!this._stopOnMatch };

    if (this._mode === 'specific') {
      if (this._dispatched >= this._seedList.length) return null;
      const seed = this._seedList[this._dispatched++];
      return { ...base, mode: 'list', seeds: [seed] };
    }
    if (this._mode === 'random') {
      if (this._remaining <= 0) return null;
      const n = Math.min(c, this._remaining);
      this._remaining -= n; this._dispatched += n;
      const buf = new BigUint64Array(1);
      crypto.getRandomValues(buf);
      const state = BigInt.asUintN(64, buf[0] + BigInt(w._idx * 7919));
      return { ...base, mode: 'random', count: n, rngState: state.toString() };
    }
    /* sequential / range / infinite */
    const job = { ...base, mode: 'seq', start: this._nextSeq.toString() };
    if (this._mode === 'range') {
      job.end = this._endSeed;
    }
    let n = c;
    if (this._mode === 'count') {
      if (this._remaining <= 0) return null;
      n = Math.min(c, this._remaining);
      this._remaining -= n;
    }
    this._nextSeq = BigInt.asUintN(64, this._nextSeq + BigInt(n));
    this._dispatched += n;
    job.count = n;
    return job;
  }

  _giveWork(w) {
    if (this.state !== 'running') return;
    const job = this._makeJob(w);
    if (!job) {
      /* no more work — if every worker is idle, we're done */
      if (this.workers.every(x => !x._busy)) this._finish();
      return;
    }
    w._busy = true;
    w.postMessage({ type: 'job', job });
  }

  _onWorkerMsg(w, msg, onReady) {
    switch (msg.type) {
      case 'ready':
        onReady();
        break;
      case 'progress': {
        this.totalChecked += msg.checked;
        if (msg.partials?.length) this._mergePartials(msg.partials);
        if (msg.done) {
          w._busy = false;
          if (this.state === 'running') this._giveWork(w);
          else if (this.workers.every(x => !x._busy)) this._finish();
        }
        break;
      }
      case 'match':
        if (!this.matchSeeds.has(msg.match.seed)) {
          this.matchSeeds.add(msg.match.seed);
          this.matches.push(msg.match);
          this._onMatch?.(msg.match);
          if (this._stopOnMatch) this.stop();
        }
        break;
      case 'error':
        console.error('worker error:', msg.message);
        break;
    }
  }

  _mergePartials(list) {
    for (const p of list) {
      if (this.matchSeeds.has(p.seed)) continue;
      this.partials.push(p);
    }
    this.partials.sort((a, b) => b.score - a.score);
    if (this.partials.length > 50) this.partials.length = 50;
  }

  _tickStats() {
    const now = performance.now();
    const dt = (now - this._lastT) / 1000;
    if (dt > 0) {
      const inst = (this.totalChecked - this._lastChecked) / dt;
      this._rate = this._rate ? this._rate * 0.6 + inst * 0.4 : inst;
    }
    this._lastChecked = this.totalChecked;
    this._lastT = now;
    const elapsed = (now - this._startTime) / 1000;
    const best = this.matches.length
      ? Math.max(...this.matches.map(m => m.score))
      : (this.partials.length ? this.partials[0].score : 0);
    this._onStats?.({
      checked: this.totalChecked,
      rate: Math.round(this._rate),
      elapsed,
      matches: this.matches.length,
      bestScore: best,
      state: this.state,
      mode: this._mode,
      remaining: (this._mode === 'count' || this._mode === 'random') ? Math.max(0, this._remaining) : null,
    });
  }

  stop() {
    if (this.state !== 'running') return;
    this.state = 'stopping';
    this._onState?.('stopping');
    for (const w of this.workers) w.postMessage({ type: 'stop-current' });
    /* safety: finalize even if workers go quiet */
    setTimeout(() => { if (this.state === 'stopping') this._finish(); }, 4000);
  }

  _finish() {
    if (this.state === 'idle') return;
    this.state = 'idle';
    clearInterval(this._rateTimer);
    this._rateTimer = null;
    this._tickStats();
    for (const w of this.workers) w.terminate();
    this.workers = [];
    this._onState?.('idle');
  }

  /* One-shot benchmark with the current conditions. Resolves with seeds/s. */
  async benchmark(conditions, ms = 2500) {
    if (this.state !== 'idle') throw new Error('Stop the current search first.');
    const n = Math.max(1, this.settings.workers | 0);
    const workers = [];
    let checked = 0;
    const t0 = performance.now();
    const deadline = t0 + ms;

    await new Promise((resolve, reject) => {
      let ready = 0, finished = 0;
      const give = (w) => {
        const buf = new BigUint64Array(1);
        crypto.getRandomValues(buf);
        w.postMessage({
          type: 'job',
          job: {
            mode: 'random', count: 256,
            rngState: BigInt.asUintN(64, buf[0] + BigInt(w._idx)).toString(),
            conditions, stopOnMatch: false,
          },
        });
      };
      for (let i = 0; i < n; i++) {
        const w = new Worker(WORKER_URL, { type: 'module' });
        w._idx = i;
        w.onmessage = (e) => {
          const m = e.data;
          if (m.type === 'ready') { if (++ready === n) { workers.forEach(give); } }
          else if (m.type === 'progress') {
            checked += m.checked;
            if (m.done) {
              if (performance.now() < deadline) give(w);
              else if (++finished === n) resolve();
            }
          }
        };
        w.onerror = (e) => reject(new Error('Worker failed: ' + e.message));
        w.postMessage({ type: 'init' });
        workers.push(w);
      }
      setTimeout(() => resolve(), ms + 8000);
    });

    const dt = (performance.now() - t0) / 1000;
    workers.forEach(w => w.terminate());
    return { rate: Math.round(checked / dt), checked, seconds: dt, workers: n };
  }
}
