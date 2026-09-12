/* ui-results.js — result cards, detail pane, map, copy & export. */

import { el } from './ui-builder.js';
import { STRUCT_BY_KEY, describeCondition, biomeName, biomeColor } from './data.js';
import { seedToParts } from './wasm-api.js';

export function initResults({ getConditions, getMapLab, onOpenAIHint }) {
  const listEl = document.getElementById('results-list');
  const emptyEl = document.getElementById('results-empty');
  const detailBody = document.getElementById('detail-body');
  const detailPlaceholder = document.getElementById('detail-placeholder');
  let selectedSeed = null;
  let openAIHintShown = false;

  /* ---------- clipboard ---------- */

  async function copy(text, label = 'Copied') {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${label}: ${text.length > 46 ? text.slice(0, 46) + '…' : text}`);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.append(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
      toast(label);
    }
  }

  function toast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.remove('hidden');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.add('hidden'), 2400);
  }

  /* ---------- result cards ---------- */

  function scoreClass(score) {
    if (score >= 100) return 'score-100';
    if (score >= 90) return 'score-90';
    if (score >= 70) return 'score-70';
    return 'score-low';
  }

  function renderMatches(matches, partials) {
    listEl.innerHTML = '';
    const hasMatches = matches.length > 0;
    emptyEl.style.display = (hasMatches || partials.length) ? 'none' : '';
    if (hasMatches || partials.length) listEl.append(emptyEl);

    for (const m of matches) {
      const card = el('div', {
        class: 'result' + (selectedSeed === m.seed ? ' selected' : ''),
        onClick: () => { selectedSeed = m.seed; openDetail(m); renderMatches(matches, partials); },
      },
        el('div', { class: 'result-top' },
          el('span', {}, m.perfect ? '🎉' : '✅'),
          el('span', { class: 'result-seed' }, m.seed),
          el('span', { class: 'score-badge ' + scoreClass(m.score) }, m.score + '%'),
        ),
        el('div', { class: 'result-meta' },
          m.perfect ? 'Perfect match — every condition verified. ' : 'Verified match. ',
          `Spawn (${m.spawn.x}, ${m.spawn.z}) · ${Object.values(m.details).reduce((a, d) => a + d.instances.length, 0)} features located`),
      );
      listEl.append(card);
    }

    if (!hasMatches && partials.length) {
      listEl.append(el('p', { class: 'hint', style: 'margin:4px 2px' },
        'Best near-misses so far (required conditions not fully met):'));
      for (const p of partials.slice(0, 10)) {
        listEl.append(el('div', { class: 'result', style: 'cursor:default;opacity:.85' },
          el('div', { class: 'result-top' },
            el('span', {}, '🔎'),
            el('span', { class: 'result-seed' }, p.seed),
            el('span', { class: 'score-badge ' + scoreClass(p.score) }, p.score + '%'),
          ),
          el('div', { class: 'result-meta' },
            `Partial match — ${p.reqMet}/${p.reqTotal} required conditions. Keep searching for a full match.`),
        ));
      }
    }
  }

  /* ---------- detail pane ---------- */

  function featureRows(m) {
    const conds = getConditions();
    const rows = [];

    rows.push({
      icon: '🧭', name: 'World spawn (estimated)',
      x: m.spawn.x, z: m.spawn.z, dist: 0,
      sub: `Biome: ${biomeName(m.spawn.biome)} · biome-based estimate, exact block may shift a little`,
    });

    for (const c of conds) {
      const d = m.details[c.id];
      if (!d || !d.instances.length) continue;
      const meta = STRUCT_BY_KEY.get(c.key);
      const icon = c.kind === 'spawn_biome' ? '🌍' : c.kind === 'biome_at' ? '📍'
        : c.kind === 'biome_area' ? '🗺️' : c.kind === 'or' ? '🔀' : (meta?.icon || '❓');
      const name = c.kind === 'spawn_biome' ? 'Spawn biome'
        : c.kind === 'biome_at' ? 'Biome check'
        : c.kind === 'biome_area' ? 'Biome area'
        : c.kind === 'or' ? 'OR group (matched)'
        : (meta?.name || c.key);
      d.instances.forEach((inst, i) => {
        rows.push({
          icon,
          name: d.instances.length > 1 ? `${name} #${i + 1}` : name,
          x: inst.x, z: inst.z, dist: inst.dist,
          flags: inst.flags, approx: inst.approxGrid,
          sub: [
            inst.biome !== undefined ? `Biome: ${biomeName(inst.biome)}` : null,
            d.approx ? 'position ±112 blocks (approximate mode)' : null,
            (inst.flags & 2) ? 'terrain heuristic — extra verification advised' : null,
          ].filter(Boolean).join(' · '),
        });
      });
      if (!d.ok) {
        rows.push({ icon, name: name + ' — optional, not met', missing: true });
      }
    }
    return rows;
  }

  function unverifiedNotes(m) {
    const conds = getConditions();
    const notes = [];
    for (const c of conds) {
      const d = m.details[c.id];
      if (!d?.ok) continue;
      if (c.blacksmith) {
        notes.push('Blacksmith presence was requested but cannot be verified by fast search — check the village in-game.');
      }
      if (c.portal === 'full12') {
        notes.push('A fully filled End Portal (12/12) was requested. Portal frame eyes cannot be computed without full terrain generation — this needs additional verification in-game. This result is NOT confirmed 12/12.');
      }
    }
    return notes;
  }

  function openDetail(m) {
    detailPlaceholder.classList.add('hidden');
    detailBody.classList.remove('hidden');
    detailBody.innerHTML = '';

    const rows = featureRows(m);
    const notes = unverifiedNotes(m);
    const tpFor = (x, z, y = 320) => `/tp ${x} ${y} ${z}`;

    detailBody.append(
      el('div', { class: 'detail-head' },
        el('span', {}, m.perfect ? '🎉' : '✅'),
        el('span', { class: 'detail-seed' }, m.seed),
        el('span', { class: 'score-badge ' + scoreClass(m.score) }, m.score + '%'),
      ),
      el('p', { class: 'hint' },
        m.perfect ? 'Perfect match: all required and optional conditions verified for Minecraft Java 26.2.'
                  : 'Verified match: all required conditions verified (some optional conditions not met).'),
      el('div', { class: 'detail-actions' },
        el('button', { class: 'secondary small', onClick: () => copy(m.seed, 'Seed copied') }, '📋 Copy seed'),
        el('button', { class: 'secondary small', onClick: () => copy(rows.filter(r => !r.missing).map(r => `${r.name}: ${r.x} ${r.z}`).join('\n'), 'Coordinates copied') }, '🧾 Copy all coordinates'),
        el('button', { class: 'secondary small', onClick: () => download(`seed-${m.seed}.json`, JSON.stringify(exportSeedJSON(m), null, 2), 'application/json') }, '⬇ JSON'),
      ),
    );

    if (notes.length) {
      detailBody.append(el('div', { class: 'note-unverified' },
        el('strong', {}, '⚠ Requires additional verification: '),
        ...notes.map(n => el('div', {}, '• ' + n))));
    }

    const section = el('div', { class: 'detail-section' }, el('h3', {}, 'Located features'));
    for (const r of rows) {
      if (r.missing) {
        section.append(el('div', { class: 'feature-row', style: 'opacity:.6' },
          el('div', { class: 'fr-top' },
            el('span', {}, r.icon), el('span', { class: 'fr-name' }, r.name))));
        continue;
      }
      section.append(el('div', { class: 'feature-row' },
        el('div', { class: 'fr-top' },
          el('span', {}, r.icon),
          el('span', { class: 'fr-name' }, r.name),
          r.dist !== undefined && r.dist > 0
            ? el('span', { class: 'fr-dist' }, `${r.dist} blocks`) : null,
        ),
        el('div', { class: 'fr-coords' }, `X: ${r.x}   Z: ${r.z}   (Y: not computed)`),
        r.sub ? el('div', { class: 'hint' }, r.sub) : null,
        el('div', { class: 'fr-actions' },
          el('button', { class: 'ghost small', onClick: () => copy(`${r.x} ${r.z}`, 'Coords copied') }, 'copy coords'),
          el('button', { class: 'ghost small', onClick: () => copy(tpFor(r.x, r.z), 'Teleport command copied') }, 'copy /tp'),
        ),
      ));
    }
    detailBody.append(section);
    detailBody.append(el('p', { class: 'hint' },
      'Y coordinates depend on terrain height, which the fast search does not generate. Teleport commands use Y=320 (top of the world) so you land safely near the spot.'));

    /* map */
    detailBody.append(el('div', { class: 'detail-section' }, el('h3', {}, 'Map')));
    const canvas = el('canvas', { id: 'detail-map', width: 10, height: 10 });
    detailBody.append(canvas, el('div', { class: 'map-legend', id: 'map-legend' }));
    setTimeout(() => renderMap(canvas, m), 30);
  }

  async function renderMap(canvas, m) {
    const lab = getMapLab();
    if (!lab) return;
    const pts = [{ x: m.spawn.x, z: m.spawn.z }];
    for (const d of Object.values(m.details)) {
      for (const i of d.instances) pts.push({ x: i.x, z: i.z });
    }
    let minX = Math.min(...pts.map(p => p.x)), maxX = Math.max(...pts.map(p => p.x));
    let minZ = Math.min(...pts.map(p => p.z)), maxZ = Math.max(...pts.map(p => p.z));
    const pad = Math.max(120, (Math.max(maxX - minX, maxZ - minZ) * 0.18));
    minX -= pad; maxX += pad; minZ -= pad; maxZ += pad;

    /* cap resolution */
    let spanX = maxX - minX, spanZ = maxZ - minZ;
    const maxSpan = 8192;
    if (spanX > maxSpan || spanZ > maxSpan) {
      const cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
      const s = Math.min(maxSpan, Math.max(spanX, spanZ));
      minX = cx - s / 2; maxX = cx + s / 2; minZ = cz - s / 2; maxZ = cz + s / 2;
      spanX = s; spanZ = s;
    }
    const N = 200; /* samples per side */
    const w = N, h = N;
    /* choose engine scale so the generated grid stays small */
    const scale = Math.max(spanX, spanZ) <= 2400 ? 4 : 16;
    const bx0 = Math.floor(minX / scale), bz0 = Math.floor(minZ / scale);
    const bw = Math.ceil(spanX / scale), bh = Math.ceil(spanZ / scale);
    const stepX = Math.max(1, Math.floor(bw / w)), stepZ = Math.max(1, Math.floor(bh / h));

    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    const { hi, lo } = seedToParts(m.seed);

    try {
      /* fetch full grid once (capped), then downsample */
      const gw = Math.min(bw, w * stepX), gh = Math.min(bh, h * stepZ);
      const grid = lab.biomeGrid(hi, lo, 0, bx0, bz0, gw, gh, 80, scale);
      const px = ctx.createImageData(w, h);
      for (let j = 0; j < h; j++) {
        for (let i = 0; i < w; i++) {
          const gi = Math.min(i * stepX, gw - 1), gj = Math.min(j * stepZ, gh - 1);
          const id = grid ? grid[gj * gw + gi] : -1;
          const col = hexToRgb(biomeColor(id));
          const o = (j * w + i) * 4;
          px.data[o] = col[0]; px.data[o + 1] = col[1]; px.data[o + 2] = col[2]; px.data[o + 3] = 255;
        }
      }
      ctx.putImageData(px, 0, 0);
    } catch {
      ctx.fillStyle = '#111';
      ctx.fillRect(0, 0, w, h);
    }

    const X = (x) => Math.round((x - minX) / spanX * w);
    const Z = (z) => Math.round((z - minZ) / spanZ * h);

    /* distance rings around spawn */
    ctx.strokeStyle = 'rgba(255,255,255,0.25)';
    ctx.lineWidth = 0.6;
    for (const d of [500, 1000, 2000, 4000]) {
      if (d > spanX) break;
      ctx.beginPath();
      ctx.arc(X(m.spawn.x), Z(m.spawn.z), d / spanX * w, 0, Math.PI * 2);
      ctx.stroke();
    }

    /* markers */
    const legend = document.getElementById('map-legend');
    legend.innerHTML = '';
    const seen = new Set();
    const drawMarker = (x, z, emoji, label, color) => {
      const px2 = X(x), pz2 = Z(z);
      ctx.beginPath();
      ctx.arc(px2, pz2, 3.4, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.55)'; ctx.lineWidth = 1; ctx.stroke();
      ctx.font = '7px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(emoji, px2, pz2 - 3);
      if (!seen.has(label)) {
        seen.add(label);
        legend.append(el('span', {}, el('i', { style: `background:${color}` }), label));
      }
    };

    const conds = getConditions();
    for (const c of conds) {
      const d = m.details[c.id];
      if (!d) continue;
      const meta = STRUCT_BY_KEY.get(c.key);
      const emoji = c.kind === 'biome_area' || c.kind === 'biome_at' ? '🌿' : (meta?.icon || '•');
      const name = meta?.name || (c.kind.startsWith('biome') ? 'Biome' : c.kind);
      const color = condColor(c.id);
      for (const inst of d.instances) drawMarker(inst.x, inst.z, emoji, name, color);
    }
    drawMarker(m.spawn.x, m.spawn.z, '🧭', 'Spawn', '#ffffff');
  }

  function condColor(id) {
    let h = 0;
    for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) | 0;
    const hue = Math.abs(h) % 360;
    return `hsl(${hue} 75% 62%)`;
  }

  function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return [60, 60, 60];
    const v = parseInt(m[1], 16);
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
  }

  /* ---------- export ---------- */

  function exportSeedJSON(m) {
    return {
      seed: m.seed,
      score: m.score,
      perfect: m.perfect,
      minecraft: 'Java 26.2',
      spawn: m.spawn,
      conditions: Object.fromEntries(Object.entries(m.details).map(([id, d]) => {
        const c = getConditions().find(x => x.id === id);
        return [id, { description: c ? describeCondition(c, getConditions()) : id, ...d }];
      })),
      unverified: unverifiedNotes(m),
    };
  }

  function download(name, text, type) {
    const blob = new Blob([text], { type });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  function exportAll(matches, format) {
    if (!matches.length) return toast('No results to export yet');
    if (format === 'json') {
      download('seedlab-results.json', JSON.stringify(matches.map(exportSeedJSON), null, 2), 'application/json');
    } else if (format === 'csv') {
      const lines = ['seed,score,feature,x,z,distance'];
      for (const m of matches) {
        lines.push(`${m.seed},${m.score},spawn,${m.spawn.x},${m.spawn.z},0`);
        for (const c of getConditions()) {
          const d = m.details[c.id];
          if (!d) continue;
          const meta = STRUCT_BY_KEY.get(c.key);
          const name = meta?.name || c.kind;
          for (const i of d.instances) lines.push(`${m.seed},${m.score},${name},${i.x},${i.z},${i.dist ?? ''}`);
        }
      }
      download('seedlab-results.csv', lines.join('\n'), 'text/csv');
    } else {
      const parts = matches.map(m => {
        const lines = [`Seed: ${m.seed} (${m.score}%)`, `Spawn: ${m.spawn.x}, ${m.spawn.z} (${biomeName(m.spawn.biome)})`];
        for (const r of featureRows(m)) {
          if (r.missing) continue;
          lines.push(`  ${r.name}: X ${r.x} Z ${r.z}${r.dist ? ` (${r.dist} blocks)` : ''}`);
        }
        for (const n of unverifiedNotes(m)) lines.push('  ⚠ ' + n);
        return lines.join('\n');
      });
      download('seedlab-results.txt', parts.join('\n\n'), 'text/plain');
    }
  }

  document.querySelectorAll('#export-buttons button').forEach(b => {
    b.addEventListener('click', () => exportAll(stateRef.matches, b.dataset.export));
  });

  const stateRef = { matches: [], partials: [] };

  return {
    update(matches, partials) {
      stateRef.matches = matches;
      stateRef.partials = partials;
      renderMatches(matches, partials);
    },
    openSeed(seed, matches) {
      const m = matches.find(x => x.seed === seed);
      if (m) { selectedSeed = seed; openDetail(m); }
    },
    toast,
  };
}
