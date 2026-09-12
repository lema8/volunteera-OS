/* app.js — Seed Lab boot & glue. */

import { SearchRunner } from './search.js';
import { SeedLab, parseSeedInput, partsToSeedString } from './wasm-api.js';
import {
  PRESETS, describeCondition, MC_VERSION_ID, MC_VERSION_STRING,
} from './data.js';
import { initBiomePicker, initConditionBuilder, el } from './ui-builder.js';
import { initResults } from './ui-results.js';
import { initAi } from './ai.js';

/* ---------------- persistent settings ---------------- */

const LS_SETTINGS = 'seedlab_settings_v1';
const LS_HISTORY = 'seedlab_history_v1';

function defaultWorkerCount() {
  const n = (navigator.hardwareConcurrency || 4) - 1;
  return Math.max(1, Math.min(16, n));
}

const settings = Object.assign({
  workers: defaultWorkerCount(),
  chunkSize: 1024,
  stopOnFirstMatch: true,
  debug: false,
  defaultStart: '0',
}, JSON.parse(localStorage.getItem(LS_SETTINGS) || '{}'));

function saveSettings() { localStorage.setItem(LS_SETTINGS, JSON.stringify(settings)); }

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add('hidden'), 2600);
}

/* ---------------- state ---------------- */

let conditions = [];
let mode = 'count';
let mapLab = null;
let searchStartInfo = null;

const runner = new SearchRunner({
  workers: settings.workers,
  chunkSize: settings.chunkSize,
  stopOnFirstMatch: settings.stopOnFirstMatch,
});

/* ---------------- number helpers ---------------- */

function parseAmount(text) {
  text = String(text).trim().toLowerCase().replace(/[,\s]/g, '');
  const m = /^(\d+(?:\.\d+)?)([km]?)$/.exec(text);
  if (!m) return NaN;
  let v = parseFloat(m[1]);
  if (m[2] === 'k') v *= 1e3;
  if (m[2] === 'm') v *= 1e6;
  return Math.round(v);
}

function fmt(n) { return Number(n).toLocaleString('en-US'); }

function fmtTime(sec) {
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return [h, m, s].map(x => String(x).padStart(2, '0')).join(':');
}

/* ---------------- UI modules ---------------- */

const openBiomePicker = initBiomePicker();

const builder = initConditionBuilder({
  getConditions: () => conditions,
  setConditions: (c) => { conditions = c; builder.render(); },
  openBiomePicker,
  onDirty: () => builder.render(),
});

const results = initResults({
  getConditions: () => conditions,
  getMapLab: () => mapLab,
});

initAi({
  getConditions: () => conditions,
  setConditions: (c) => { conditions = c; builder.render(); },
  toast,
});

builder.render();

/* ---------------- mode switching ---------------- */

const MODE_INPUTS = {
  count: 'mi-count', infinite: 'mi-infinite', random: 'mi-random',
  range: 'mi-range', specific: 'mi-specific',
};

document.querySelectorAll('#mode-pills button').forEach(btn => {
  btn.addEventListener('click', () => {
    mode = btn.dataset.mode;
    document.querySelectorAll('#mode-pills button').forEach(b =>
      b.classList.toggle('active', b === btn));
    for (const [m, id] of Object.entries(MODE_INPUTS)) {
      document.getElementById(id).classList.toggle('hidden', m !== mode);
    }
  });
});

document.querySelectorAll('.quick-amounts button').forEach(b => {
  b.addEventListener('click', () => {
    document.getElementById('in-count').value = fmt(parseInt(b.dataset.n, 10));
  });
});

/* ---------------- search control ---------------- */

const btnFind = document.getElementById('btn-find');
const btnStop = document.getElementById('btn-stop');

function setSearchingUI(on) {
  document.body.classList.toggle('searching', on);
  btnFind.disabled = on;
  btnStop.disabled = !on;
}

btnFind.addEventListener('click', startSearch);
btnStop.addEventListener('click', () => runner.stop());

function buildStartParams() {
  const common = { conditions, onStats: updateStats, onMatch, onState };
  switch (mode) {
    case 'count': {
      const n = parseAmount(document.getElementById('in-count').value);
      if (!Number.isFinite(n) || n <= 0) throw new Error('Enter how many seeds to search (e.g. 1,000,000).');
      return { ...common, mode: 'count', count: n, startSeed: settings.defaultStart || '0' };
    }
    case 'infinite':
      return { ...common, mode: 'infinite', count: Infinity, startSeed: settings.defaultStart || '0' };
    case 'random': {
      const raw = document.getElementById('in-random-count').value.trim();
      const n = raw === '' ? Infinity : parseAmount(raw);
      if (!Number.isFinite(n) || n <= 0) throw new Error('Enter how many random seeds to try, or leave blank for “until a match”.');
      return { ...common, mode: 'random', count: n };
    }
    case 'range': {
      let a, b;
      try {
        a = BigInt(document.getElementById('in-range-from').value.trim() || '0');
        b = BigInt(document.getElementById('in-range-to').value.trim() || '0');
      } catch { throw new Error('Seed range must be whole numbers.'); }
      if (b < a) throw new Error('Range: “to” must be ≥ “from”.');
      if (b - a > (1n << 56n)) throw new Error('Range too large — max 2^56 seeds.');
      return { ...common, mode: 'range', count: Infinity, startSeed: a.toString(), endSeed: b.toString() };
    }
    case 'specific': {
      const tokens = document.getElementById('in-specific').value.split(/[\s,;]+/).filter(Boolean);
      if (!tokens.length) throw new Error('Enter at least one seed (or a text seed to hash).');
      const seeds = tokens.slice(0, 64).map(parseSeedInput);
      return { ...common, mode: 'specific', seeds };
    }
  }
}

async function startSearch() {
  if (!conditions.length) {
    toast('Add at least one condition — or pick a preset.');
    document.getElementById('modal-presets').classList.remove('hidden');
    return;
  }
  let params;
  try { params = buildStartParams(); }
  catch (e) { toast('⚠ ' + e.message); return; }

  params.stopOnMatch = settings.stopOnFirstMatch && mode !== 'count' && mode !== 'specific';
  results.update([], []);
  document.getElementById('results-empty').style.display = 'none';
  setSearchingUI(true);
  progressFill.classList.add('indeterminate');
  progressFill.style.width = '35%';
  progressCaption.textContent = 'Starting search workers…';
  searchStartInfo = {
    ts: Date.now(),
    modeLabel: mode,
    conditions: JSON.parse(JSON.stringify(conditions)),
  };

  try {
    await runner.start(params);
  } catch (e) {
    setSearchingUI(false);
    searchStartInfo = null;
    progressFill.classList.remove('indeterminate');
    progressFill.style.width = '0%';
    progressCaption.textContent = '⚠ Search failed to start: ' + e.message;
    toast('⚠ ' + e.message);
  }
}

/* ---------------- stats ---------------- */

const stChecked = document.getElementById('st-checked');
const stRate = document.getElementById('st-rate');
const stElapsed = document.getElementById('st-elapsed');
const stMatches = document.getElementById('st-matches');
const stBest = document.getElementById('st-best');
const progressFill = document.getElementById('progress-fill');
const progressCaption = document.getElementById('progress-caption');

let lastBoardKey = '';

function updateStats(s) {
  stChecked.textContent = fmt(s.checked);
  stRate.textContent = fmt(s.rate);
  stElapsed.textContent = fmtTime(s.elapsed);
  stMatches.textContent = s.matches;
  stBest.textContent = s.bestScore ? s.bestScore + '%' : '—';

  /* live leaderboard refresh (only when it actually changed) */
  const key = runner.matches.length + '|' +
    runner.partials.slice(0, 5).map(p => p.seed + p.score).join(',');
  if (key !== lastBoardKey && (runner.matches.length || runner.partials.length)) {
    lastBoardKey = key;
    results.update(runner.matches, runner.partials);
  }

  const isFinite = mode === 'count' || mode === 'range' || (mode === 'random' && Number.isFinite(currentCount()));
  if (isFinite && s.mode !== 'idle') {
    progressFill.classList.remove('indeterminate');
    const total = mode === 'range' && s.totalRange ? s.totalRange : currentCount();
    const pct = total ? Math.min(100, (s.checked / total) * 100) : 0;
    progressFill.style.width = pct + '%';
    progressCaption.textContent = `${fmt(s.checked)} / ${fmt(total)} seeds (${pct.toFixed(1)}%)`;
  } else if (s.state === 'running') {
    progressFill.classList.add('indeterminate');
    progressFill.style.width = '35%';
    progressCaption.textContent = mode === 'infinite'
      ? 'Searching until a match is found… (press STOP anytime)'
      : 'Searching…';
  } else if (s.state === 'idle' && s.checked > 0) {
    progressFill.classList.remove('indeterminate');
    progressFill.style.width = '100%';
    progressCaption.textContent = `Done — ${fmt(s.checked)} seeds checked in ${fmtTime(s.elapsed)}.`;
  }
}

function currentCount() {
  if (mode === 'count') return parseAmount(document.getElementById('in-count').value);
  if (mode === 'random') return parseAmount(document.getElementById('in-random-count').value);
  if (mode === 'range') {
    try {
      const a = BigInt(document.getElementById('in-range-from').value.trim() || '0');
      const b = BigInt(document.getElementById('in-range-to').value.trim() || '0');
      return Number(b - a + 1n);
    } catch { return 0; }
  }
  return 0;
}

function onMatch(m) {
  results.update(runner.matches, runner.partials);
  results.openSeed(m.seed, runner.matches);
  if (settings.debug) console.log('match', m);
}

runner._onStateHook = null;

/* runner state changes */
const origOnState = () => {};
async function onState(state) {
  if (state === 'idle' && searchStartInfo) {
    setSearchingUI(false);
    results.update(runner.matches, runner.partials);
    if (runner.totalChecked > 0) saveHistory();
    searchStartInfo = null;
  }
}

/* patch start() callbacks: wrap to include our onState */
const _origStart = runner.start.bind(runner);
runner.start = function (params) {
  const wrapped = { ...params, onState };
  return _origStart(wrapped);
};

/* ---------------- history ---------------- */

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(LS_HISTORY) || '[]'); }
  catch { return []; }
}

function saveHistory() {
  if (!searchStartInfo) return;
  const h = loadHistory();
  h.unshift({
    ts: searchStartInfo.ts,
    mode: searchStartInfo.modeLabel,
    conditions: searchStartInfo.conditions,
    checked: runner.totalChecked,
    durationS: Math.round((Date.now() - searchStartInfo.ts) / 1000),
    matches: runner.matches.map(m => ({ seed: m.seed, score: m.score, perfect: m.perfect })),
    best: runner.matches.length ? Math.max(...runner.matches.map(m => m.score))
      : (runner.partials.length ? runner.partials[0].score : 0),
  });
  if (h.length > 25) h.length = 25;
  localStorage.setItem(LS_HISTORY, JSON.stringify(h));
  renderHistory();
}

function renderHistory() {
  const listEl = document.getElementById('history-list');
  const h = loadHistory();
  listEl.innerHTML = '';
  if (!h.length) {
    listEl.append(el('p', { class: 'hint' }, 'No searches yet.'));
    return;
  }
  for (const item of h) {
    const summary = item.conditions.slice(0, 3).map(c => describeCondition(c, item.conditions)).join(' · ');
    listEl.append(el('div', { class: 'history-item' },
      el('div', { class: 'h-top' },
        el('strong', {}, `${item.matches.length} seed${item.matches.length === 1 ? '' : 's'} found`),
        el('span', { class: 'h-date' }, new Date(item.ts).toLocaleString() + ` · ${fmt(item.checked)} checked · ${fmtTime(item.durationS)}`),
      ),
      el('div', { class: 'h-conds' }, summary + (item.conditions.length > 3 ? ` (+${item.conditions.length - 3} more)` : '')),
      el('div', { class: 'h-actions' },
        el('button', { class: 'secondary small', onClick: () => {
          conditions = JSON.parse(JSON.stringify(item.conditions));
          builder.render();
          document.getElementById('modal-history').classList.add('hidden');
          toast('Conditions restored — press FIND SEED to run again.');
        } }, '↻ Run again'),
        el('button', { class: 'ghost small', onClick: (e) => {
          const arr = loadHistory().filter(x => x.ts !== item.ts);
          localStorage.setItem(LS_HISTORY, JSON.stringify(arr));
          renderHistory();
        } }, 'Delete'),
      ),
    ));
  }
}

/* ---------------- presets modal ---------------- */

function renderPresets() {
  const wrap = document.getElementById('preset-list');
  wrap.innerHTML = '';
  for (const p of PRESETS) {
    wrap.append(el('button', {
      class: 'preset',
      onClick: () => {
        conditions = p.build();
        builder.render();
        document.getElementById('modal-presets').classList.add('hidden');
        toast(`Preset “${p.name}” loaded — tweak anything, then FIND SEED.`);
      },
    },
      el('div', { class: 'p-icon' }, p.icon),
      el('div', { class: 'p-name' }, p.name),
      el('div', { class: 'p-blurb' }, p.blurb),
    ));
  }
  wrap.append(el('button', {
    class: 'preset',
    onClick: () => {
      conditions = [];
      builder.render();
      document.getElementById('modal-presets').classList.add('hidden');
      toast('Custom search — start from an empty builder.');
    },
  },
    el('div', { class: 'p-icon' }, '🛠️'),
    el('div', { class: 'p-name' }, 'Custom'),
    el('div', { class: 'p-blurb' }, 'Start empty and build your own conditions.'),
  ));
}

/* ---------------- generic modal plumbing ---------------- */

function wireModal(id, openerId) {
  const backdrop = document.getElementById(id);
  if (openerId) document.getElementById(openerId).addEventListener('click', () => {
    if (id === 'modal-presets') renderPresets();
    if (id === 'modal-history') renderHistory();
    backdrop.classList.remove('hidden');
  });
  backdrop.querySelector('.modal-close')?.addEventListener('click', () => backdrop.classList.add('hidden'));
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.classList.add('hidden'); });
}
wireModal('modal-presets', 'btn-presets');
wireModal('modal-history', 'btn-history');
wireModal('modal-settings', 'btn-settings');

document.getElementById('history-clear').addEventListener('click', () => {
  localStorage.removeItem(LS_HISTORY);
  renderHistory();
});

/* ---------------- advanced settings ---------------- */

const advToggle = document.getElementById('advanced-toggle');
const advBody = document.getElementById('advanced-body');
advToggle.addEventListener('click', () => {
  advBody.classList.toggle('hidden');
  document.getElementById('advanced-card').classList.toggle('open');
});

const setWorkers = document.getElementById('set-workers');
const setChunk = document.getElementById('set-chunk');
const setStopFirst = document.getElementById('set-stop-first');
setWorkers.value = settings.workers;
setChunk.value = settings.chunkSize;
setStopFirst.checked = settings.stopOnFirstMatch;
setWorkers.addEventListener('change', () => {
  settings.workers = Math.max(1, Math.min(32, parseInt(setWorkers.value, 10) || 1));
  setWorkers.value = settings.workers;
  runner.settings.workers = settings.workers;
  saveSettings();
});
setChunk.addEventListener('change', () => {
  settings.chunkSize = Math.max(128, Math.min(16384, parseInt(setChunk.value, 10) || 1024));
  setChunk.value = settings.chunkSize;
  runner.settings.chunkSize = settings.chunkSize;
  saveSettings();
});
setStopFirst.addEventListener('change', () => {
  settings.stopOnFirstMatch = setStopFirst.checked;
  runner.settings.stopOnFirstMatch = setStopFirst.checked;
  saveSettings();
});

document.getElementById('btn-benchmark').addEventListener('click', async () => {
  const btn = document.getElementById('btn-benchmark');
  const out = document.getElementById('bench-result');
  if (runner.state !== 'idle') { toast('Stop the current search first.'); return; }
  btn.disabled = true;
  out.textContent = 'Measuring… (~3s)';
  try {
    const conds = conditions.length ? conditions : PRESETS[1].build();
    const r = await runner.benchmark(conds, 2500);
    out.textContent = `≈ ${fmt(r.rate)} seeds/sec with current conditions (${fmt(r.checked)} seeds in ${r.seconds.toFixed(1)}s on ${r.workers} workers)`;
  } catch (e) {
    out.textContent = '⚠ ' + e.message;
  } finally {
    btn.disabled = false;
  }
});

/* ---------------- settings modal ---------------- */

const setDebug = document.getElementById('set-debug');
setDebug.checked = settings.debug;
setDebug.addEventListener('change', () => { settings.debug = setDebug.checked; saveSettings(); });
const setDefaultStart = document.getElementById('set-default-start');
setDefaultStart.value = settings.defaultStart;
setDefaultStart.addEventListener('change', () => {
  settings.defaultStart = setDefaultStart.value.trim() || '0';
  saveSettings();
});

/* ---------------- boot ---------------- */

(async function boot() {
  document.getElementById('mc-version-label').textContent = MC_VERSION_STRING;
  try {
    mapLab = await SeedLab.load();
    const vid = mapLab.version();
    const info = `cubiomes WASM${mapLab.simd ? ' + SIMD' : ''} · targets Minecraft ${MC_VERSION_STRING} (engine version id ${vid})`;
    document.getElementById('engine-info').textContent = info;
    if (vid !== MC_VERSION_ID) {
      const w = document.getElementById('version-warning');
      w.textContent = `⚠ Engine version mismatch: this site targets Minecraft ${MC_VERSION_STRING}, but the loaded engine reports a different version. Results may not match ${MC_VERSION_STRING}.`;
      w.classList.remove('hidden');
    }
  } catch (e) {
    document.getElementById('engine-info').textContent = 'failed to load: ' + e.message;
    const w = document.getElementById('version-warning');
    w.textContent = '⚠ The WebAssembly engine failed to load (' + e.message + '). Reload the page, or update your browser.';
    w.classList.remove('hidden');
  }
  renderPresets();
  renderHistory();
  if (settings.debug) console.log('Seed Lab booted', settings);
})();
