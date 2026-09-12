/* ai.js — optional natural-language search assistant.
 * The AI only PRODUCES structured conditions; it never runs a search.
 * The user's API key lives in localStorage and is sent only to the
 * provider the user selects. */

import { el } from './ui-builder.js';
import {
  STRUCTURES, STRUCT_BY_KEY, BIOME_BY_NAME, describeCondition, cid,
} from './data.js';

const LS_KEY = 'seedlab_ai_v1';

function loadSettings() {
  try {
    return Object.assign({
      provider: 'openrouter',
      baseUrl: 'https://openrouter.ai/api/v1',
      apiKey: '',
      model: '',
    }, JSON.parse(localStorage.getItem(LS_KEY) || '{}'));
  } catch { return { provider: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1', apiKey: '', model: '' }; }
}

function saveSettings(s) { localStorage.setItem(LS_KEY, JSON.stringify(s)); }

function systemPrompt() {
  const structs = STRUCTURES.map(s => s.key).join(', ');
  const biomes = [...BIOME_BY_NAME.keys()].join(', ');
  return `You convert a Minecraft seed request into JSON search conditions for a seed finder website.
Minecraft version: Java 26.2. Respond with ONLY a JSON object, no markdown fences, matching this schema:

{"conditions":[ ... ]}

Each condition is one of:
1. Structure: {"type":"structure","structure":KEY,"countMin":int,"distMax":int,"distMin":int?,"anchor":"spawn","biomes":[biome names]?,"blacksmith":bool?,"required":bool}
   structure keys: ${structs}
   "stronghold" is its own type: {"type":"stronghold","countMin":1,"distMax":2000,"anchor":"spawn","portal":"any|full12","required":true}
2. Spawn biome: {"type":"spawn_biome","biomes":[biome names],"required":true}
3. Nearby biome: {"type":"biome_area","biomes":[biome names],"distMax":500,"anchor":"spawn","required":true}
4. Biome at point: {"type":"biome_at","biomes":[biome names],"anchor":"spawn","y":320,"required":true}
5. OR alternatives (any one satisfies): {"type":"or","children":[structure conditions without anchors],"required":true}
   e.g. desert pyramid OR jungle temple: {"type":"or","children":[{"structure":"desert_pyramid","distMax":1200},{"structure":"jungle_temple","distMax":1200}]}

Rules:
- Default anchor is "spawn". Distances are blocks. Default distMax 1000 for structures, 2500 for strongholds.
- Use required:false only for clearly optional wishes ("preferably", "ideally", "would be nice").
- blacksmith:true is allowed on villages; portal:"full12" on strongholds — these are honored as in-game verification notes.
- biome names: ${biomes}
- Keep at most 8 conditions. Output ONLY the JSON object.`;
}

function fuzzyBiome(name) {
  const key = String(name).toLowerCase().trim().replace(/[\s-]+/g, '_');
  if (BIOME_BY_NAME.has(key)) return BIOME_BY_NAME.get(key);
  for (const [k, v] of BIOME_BY_NAME) {
    if (k.includes(key) || key.includes(k)) return v;
  }
  return null;
}

function fuzzyStruct(key) {
  const k = String(key).toLowerCase().replace(/[\s-]+/g, '_');
  if (STRUCT_BY_KEY.has(k)) return k;
  for (const s of STRUCTURES) {
    if (s.key.includes(k) || k.includes(s.key)) return s.key;
    if (s.name.toLowerCase().replace(/\s+/g, '_').includes(k)) return s.key;
  }
  return null;
}

/* Validate + coerce AI output into real builder conditions. */
export function validateAiConditions(raw) {
  const notes = [];
  const out = [];
  if (!raw || !Array.isArray(raw.conditions)) {
    throw new Error('The AI did not return a valid condition list.');
  }
  for (const item of raw.conditions.slice(0, 8)) {
    if (!item || typeof item !== 'object') continue;
    const required = item.required !== false;
    const biomes = Array.isArray(item.biomes)
      ? [...new Set(item.biomes.map(fuzzyBiome).filter(x => x !== null))]
      : [];

    if (item.type === 'spawn_biome') {
      if (!biomes.length) { notes.push('Skipped a spawn-biome condition with unknown biomes.'); continue; }
      out.push({ id: cid(), kind: 'spawn_biome', required, biomes });
      continue;
    }
    if (item.type === 'biome_area') {
      if (!biomes.length) { notes.push('Skipped a nearby-biome condition with unknown biomes.'); continue; }
      out.push({
        id: cid(), kind: 'biome_area', required, biomes,
        distMax: Math.min(1024, Math.max(32, item.distMax | 0 || 500)),
        distMin: Math.max(0, item.distMin | 0 || 0),
        anchor: { kind: 'spawn' },
      });
      continue;
    }
    if (item.type === 'biome_at') {
      if (!biomes.length) { notes.push('Skipped a biome-at-point condition with unknown biomes.'); continue; }
      out.push({
        id: cid(), kind: 'biome_at', required, biomes,
        anchor: { kind: 'spawn' },
        y: typeof item.y === 'number' ? Math.max(-64, Math.min(320, item.y)) : 320,
      });
      continue;
    }
    if (item.type === 'stronghold') {
      const c = {
        id: cid(), kind: 'stronghold', key: 'stronghold', required,
        countMin: Math.max(1, item.countMin | 0 || 1),
        countMax: null,
        distMin: Math.max(0, item.distMin | 0 || 0),
        distMax: Math.min(20000, Math.max(0, item.distMax | 0 || 2500)),
        anchor: { kind: 'spawn' },
        biomes: [], excludeBiomes: [],
      };
      if (item.portal === 'full12') {
        c.portal = 'full12';
        notes.push('Full 12/12 End portals cannot be verified by fast search — matches will be flagged for in-game verification.');
      }
      out.push(c);
      continue;
    }
    if (item.type === 'or') {
      const kids = [];
      for (const sub of (item.children || []).slice(0, 4)) {
        const key = fuzzyStruct(sub?.structure);
        if (!key) continue;
        const meta = STRUCT_BY_KEY.get(key);
        kids.push({
          id: cid(), kind: 'structure', key, struct: meta.type, dim: meta.dim,
          countMin: Math.max(1, sub.countMin | 0 || 1), countMax: null,
          distMin: 0,
          distMax: Math.min(20000, Math.max(0, sub.distMax | 0 || 1000)),
          anchor: null, biomes: [], excludeBiomes: [],
        });
      }
      if (kids.length < 2) { notes.push('OR groups need at least 2 valid alternatives — skipped one.'); continue; }
      out.push({ id: cid(), kind: 'or', required, anchor: { kind: 'spawn' }, children: kids });
      continue;
    }
    if (item.type === 'structure') {
      const key = fuzzyStruct(item.structure);
      if (!key) { notes.push(`Skipped unknown structure “${item.structure}”.`); continue; }
      const meta = STRUCT_BY_KEY.get(key);
      const c = {
        id: cid(), kind: 'structure', key, struct: meta.type, dim: meta.dim, required,
        countMin: Math.max(1, item.countMin | 0 || 1),
        countMax: null,
        distMin: Math.max(0, item.distMin | 0 || 0),
        distMax: Math.min(20000, Math.max(0, item.distMax | 0 || 1000)),
        anchor: { kind: 'spawn' },
        biomes, excludeBiomes: [],
      };
      if (item.blacksmith === true && meta.hasBlacksmithOption) {
        c.blacksmith = true;
        notes.push('Blacksmith presence cannot be verified by fast search — matches will be flagged for in-game verification.');
      }
      out.push(c);
      continue;
    }
    notes.push(`Skipped a condition of unknown type “${item.type}”.`);
  }
  if (!out.length) throw new Error('No usable conditions could be extracted.');
  return { conditions: out, notes };
}

function extractJson(text) {
  const cleaned = String(text).replace(/```json|```/g, '').trim();
  const start = cleaned.indexOf('{');
  const end = cleaned.lastIndexOf('}');
  if (start < 0 || end <= start) throw new Error('The AI response did not contain JSON.');
  return JSON.parse(cleaned.slice(start, end + 1));
}

export function initAi({ getConditions, setConditions, toast }) {
  const backdrop = document.getElementById('modal-ai');
  const providerSel = document.getElementById('ai-provider');
  const baseRow = document.getElementById('ai-base-row');
  const baseUrlInp = document.getElementById('ai-base-url');
  const keyInp = document.getElementById('ai-key');
  const modelInp = document.getElementById('ai-model');
  const promptInp = document.getElementById('ai-prompt');
  const statusEl = document.getElementById('ai-status');
  const previewEl = document.getElementById('ai-preview');
  const previewList = document.getElementById('ai-preview-list');
  const notesEl = document.getElementById('ai-notes');
  const genBtn = document.getElementById('ai-generate');
  let pending = null;

  const applyToForm = (s) => {
    providerSel.value = s.provider;
    baseUrlInp.value = s.baseUrl;
    keyInp.value = s.apiKey;
    modelInp.value = s.model;
    baseRow.classList.toggle('hidden', s.provider !== 'custom');
  };
  applyToForm(loadSettings());

  const persist = () => saveSettings({
    provider: providerSel.value,
    baseUrl: baseUrlInp.value.trim() || 'https://openrouter.ai/api/v1',
    apiKey: keyInp.value.trim(),
    model: modelInp.value.trim(),
  });
  [providerSel, baseUrlInp, keyInp, modelInp].forEach(x => x.addEventListener('change', () => {
    baseRow.classList.toggle('hidden', providerSel.value !== 'custom');
    persist();
  }));

  backdrop.querySelector('.modal-close').addEventListener('click', () => backdrop.classList.add('hidden'));
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.classList.add('hidden'); });

  document.getElementById('btn-ai').addEventListener('click', () => {
    applyToForm(loadSettings());
    backdrop.classList.remove('hidden');
  });

  genBtn.addEventListener('click', async () => {
    const s = loadSettings();
    const text = promptInp.value.trim();
    statusEl.textContent = '';
    previewEl.classList.add('hidden');
    if (!text) { statusEl.textContent = 'Describe the seed you want first.'; return; }
    if (!s.apiKey) { statusEl.textContent = 'Add your API key above (stored only in this browser).'; return; }

    const model = s.model || (s.provider === 'openrouter' ? 'openai/gpt-4o-mini' : 'gpt-4o-mini');
    const base = (s.baseUrl || 'https://openrouter.ai/api/v1').replace(/\/$/, '');
    const headers = {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${s.apiKey}`,
    };
    if (s.provider === 'openrouter') {
      headers['HTTP-Referer'] = location.origin;
      headers['X-Title'] = 'Seed Lab';
    }

    genBtn.disabled = true;
    statusEl.textContent = 'Talking to the AI…';
    try {
      const resp = await fetch(base + '/chat/completions', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model,
          temperature: 0,
          messages: [
            { role: 'system', content: systemPrompt() },
            { role: 'user', content: text },
          ],
        }),
      });
      if (!resp.ok) {
        const body = await resp.text().catch(() => '');
        throw new Error(`Provider error ${resp.status}: ${body.slice(0, 200)}`);
      }
      const data = await resp.json();
      const content = data?.choices?.[0]?.message?.content || '';
      const parsed = extractJson(content);
      const { conditions, notes } = validateAiConditions(parsed);
      pending = conditions;
      previewList.innerHTML = '';
      for (const c of conditions) {
        previewList.append(el('li', {},
          describeCondition(c, conditions) + (c.required === false ? '  (optional)' : '')));
      }
      notesEl.textContent = notes.length ? notes.join(' ') : '';
      previewEl.classList.remove('hidden');
      statusEl.textContent = `Generated ${conditions.length} conditions — review, then apply.`;
    } catch (err) {
      statusEl.textContent = '⚠ ' + (err.message || String(err));
    } finally {
      genBtn.disabled = false;
    }
  });

  document.getElementById('ai-apply').addEventListener('click', () => {
    if (!pending?.length) return;
    setConditions(pending);
    backdrop.classList.add('hidden');
    toast('AI conditions loaded into the builder — review and press FIND SEED.');
  });
}
