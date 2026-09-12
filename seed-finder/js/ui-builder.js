/* ui-builder.js — the dynamic condition builder. */

import {
  STRUCTURES, STRUCT_BY_KEY, BIOME_GROUPS, BIOME_BY_ID, BIOME_BY_NAME,
  describeCondition, anchorLabel, biomeName, prettyName, newStructureCondition,
  newOrGroupCondition, newOrChild,
} from './data.js';
import { ST } from './wasm-api.js';

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v !== null && v !== undefined && v !== false) {
      node.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

/* ---------------- biome picker (shared modal) ---------------- */

let pickerState = null;

export function initBiomePicker() {
  const backdrop = document.getElementById('modal-biomes');
  const groupsEl = document.getElementById('biome-groups');
  const searchEl = document.getElementById('biome-search');

  const render = (filter = '') => {
    groupsEl.innerHTML = '';
    const f = filter.trim().toLowerCase();
    for (const g of BIOME_GROUPS) {
      const biomes = g.biomes.filter(b => !f || b.name.replace(/_/g, ' ').includes(f));
      if (!biomes.length) continue;
      const grid = el('div', { class: 'biome-grid' });
      for (const b of biomes) {
        const sel = pickerState.selected.has(b.id);
        grid.append(el('div', {
          class: 'biome-chip' + (sel ? ' selected' : ''),
          onClick: (e) => {
            const id = b.id;
            if (pickerState.selected.has(id)) pickerState.selected.delete(id);
            else pickerState.selected.add(id);
            e.currentTarget.classList.toggle('selected');
          },
        }, el('i', { style: `background:${b.color}` }), prettyName(b.name)));
      }
      groupsEl.append(
        el('div', { class: 'biome-group-label' }, g.label),
        grid,
      );
    }
  };

  searchEl.addEventListener('input', () => render(searchEl.value));
  document.getElementById('biome-ok').addEventListener('click', () => {
    pickerState?.onDone([...pickerState.selected]);
    backdrop.classList.add('hidden');
  });
  document.getElementById('biome-clear').addEventListener('click', () => {
    pickerState.selected.clear();
    render(searchEl.value);
  });
  backdrop.querySelector('.modal-close').addEventListener('click', () =>
    backdrop.classList.add('hidden'));
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) backdrop.classList.add('hidden');
  });

  return function openBiomePicker({ title, selected, onDone }) {
    pickerState = { selected: new Set(selected), onDone };
    document.getElementById('biome-picker-title').textContent = title || 'Choose biomes';
    searchEl.value = '';
    render();
    backdrop.classList.remove('hidden');
  };
}

/* ---------------- condition list rendering ---------------- */

export function initConditionBuilder({ getConditions, setConditions, openBiomePicker, onDirty }) {
  const listEl = document.getElementById('conditions-list');
  const emptyEl = document.getElementById('conditions-empty');
  let expandedId = null;

  const structSelect = document.getElementById('add-structure-select');
  for (const s of STRUCTURES) {
    structSelect.append(el('option', { value: s.key }, `${s.icon} ${s.name}`));
  }
  document.getElementById('btn-add-structure').addEventListener('click', () => {
    const conds = getConditions();
    conds.push(newStructureCondition(structSelect.value));
    expandedId = conds[conds.length - 1].id;
    setConditions(conds);
  });
  document.getElementById('btn-add-spawn-biome').addEventListener('click', () => {
    openBiomePicker({
      title: 'Spawn directly inside…',
      selected: [],
      onDone: (ids) => {
        if (!ids.length) return toast('Pick at least one biome');
        const conds = getConditions();
        conds.push({ id: 'c' + Date.now() + Math.random().toString(36).slice(2, 5), kind: 'spawn_biome', required: true, biomes: ids });
        setConditions(conds);
      },
    });
  });
  document.getElementById('btn-add-biome-area').addEventListener('click', () => {
    openBiomePicker({
      title: 'Biome that must be nearby…',
      selected: [],
      onDone: (ids) => {
        if (!ids.length) return toast('Pick at least one biome');
        const conds = getConditions();
        conds.push({
          id: 'c' + Date.now() + Math.random().toString(36).slice(2, 5),
          kind: 'biome_area', required: true, biomes: ids,
          distMax: 500, distMin: 0, anchor: { kind: 'spawn' },
        });
        expandedId = conds[conds.length - 1].id;
        setConditions(conds);
      },
    });
  });
  document.getElementById('btn-add-or').addEventListener('click', () => {
    const conds = getConditions();
    conds.push(newOrGroupCondition());
    expandedId = conds[conds.length - 1].id;
    setConditions(conds);
  });
  document.getElementById('btn-clear-conds').addEventListener('click', () => {
    setConditions([]);
  });
  document.getElementById('open-presets-hint')?.addEventListener('click', () =>
    document.getElementById('btn-presets').click());

  function toast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.remove('hidden');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.add('hidden'), 2200);
  }

  function numInput(value, onVal, { min = 0, max = 1_000_000, placeholder = '' } = {}) {
    const inp = el('input', {
      type: 'number', min, max, value: value ?? '', placeholder,
      onChange: (e) => {
        const v = e.target.value === '' ? null : Math.max(0, parseInt(e.target.value, 10) || 0);
        onVal(v);
      },
    });
    return inp;
  }

  function anchorEditor(c) {
    const conds = getConditions();
    const opts = [
      ['spawn', 'World spawn'],
      ['origin', 'Coordinates (0, 0)'],
      ['coords', 'Specific coordinates'],
    ];
    const others = conds.filter(x => x.id !== c.id &&
      (x.kind === 'structure' || x.kind === 'stronghold'));
    for (const o of others) opts.push(['condition:' + o.id, 'After: ' + describeCondition(o, conds, true).slice(0, 42)]);

    const a = c.anchor || { kind: 'spawn' };
    const sel = el('select', {
      onChange: (e) => {
        const v = e.target.value;
        if (v.startsWith('condition:')) c.anchor = { kind: 'condition', refId: v.slice(10) };
        else c.anchor = { kind: v };
        if (v === 'coords') { c.anchor.x = 0; c.anchor.z = 0; }
        onDirty();
      },
    }, opts.map(([v, l]) => el('option', { value: v, selected: (a.kind === 'condition' ? 'condition:' + a.refId : a.kind) === v || undefined }, l)));
    sel.value = a.kind === 'condition' ? 'condition:' + a.refId : a.kind;

    const wrap = el('div', { class: 'full' }, el('label', {}, 'Relative to', sel));
    if (a.kind === 'coords') {
      wrap.append(el('div', { class: 'full', style: 'display:flex;gap:8px' },
        el('label', { style: 'flex:1' }, 'X', numInput(a.x ?? 0, v => { c.anchor.x = v ?? 0; onDirty(); }, { min: -30000000, max: 30000000 })),
        el('label', { style: 'flex:1' }, 'Z', numInput(a.z ?? 0, v => { c.anchor.z = v ?? 0; onDirty(); }, { min: -30000000, max: 30000000 })),
      ));
    }
    return wrap;
  }

  function biomeFilterEditor(c, labelText) {
    const btn = el('button', {
      class: 'secondary small',
      onClick: () => openBiomePicker({
        title: labelText,
        selected: c.biomes || [],
        onDone: (ids) => { c.biomes = ids; onDirty(); },
      }),
    });
    const update = () => {
      btn.textContent = (c.biomes?.length)
        ? `🌿 ${c.biomes.length} biome${c.biomes.length > 1 ? 's' : ''}: ${c.biomes.slice(0, 3).map(biomeName).join(', ')}${c.biomes.length > 3 ? '…' : ''}`
        : 'Any biome';
    };
    update();
    const refresh = () => { update(); };
    return { btn, refresh };
  }

  function buildEditor(c) {
    const ed = el('div', { class: 'cond-editor' });
    const dirty = () => onDirty();

    if (c.kind === 'or') {
      ed.append(anchorEditor(c));
      const kidsWrap = el('div', { class: 'full' });
      const renderKids = () => {
        kidsWrap.innerHTML = '';
        kidsWrap.append(el('label', {}, 'Alternatives (any one of these satisfies the condition)'));
        (c.children || []).forEach((ch, idx) => {
          const meta = STRUCT_BY_KEY.get(ch.key);
          kidsWrap.append(el('div', { style: 'display:flex;gap:6px;align-items:center;margin:4px 0' },
            el('span', { style: 'width:20px;text-align:center' }, meta?.icon || '❓'),
            el('select', {
              style: 'flex:2',
              onChange: (e) => {
                const nm = newOrChild(e.target.value, ch.distMax);
                c.children[idx] = { ...nm, id: ch.id };
                dirty();
              },
            }, STRUCTURES.filter(x => !x.special).map(st =>
              el('option', { value: st.key, ...(st.key === ch.key ? { selected: true } : {}) }, st.name))),
            el('div', { style: 'width:62px' },
              numInput(ch.countMin, v => { ch.countMin = Math.max(1, v ?? 1); dirty(); }, { min: 1, max: 32 })),
            el('input', {
              type: 'number', min: 0, max: 20000, value: ch.distMax, style: 'width:90px',
              title: 'Max distance (blocks)',
              onChange: (e) => { ch.distMax = Math.max(0, parseInt(e.target.value, 10) || 0); dirty(); },
            }),
            el('button', {
              class: 'ghost small', title: 'Remove alternative',
              onClick: () => { c.children.splice(idx, 1); dirty(); },
            }, '✕'),
          ));
        });
        kidsWrap.append(el('button', {
          class: 'secondary small',
          onClick: () => { (c.children ||= []).push(newOrChild()); dirty(); },
        }, '+ Add alternative'));
      };
      renderKids();
      /* re-render kids on dirty so selects stay in sync */
      const origDirty = onDirty;
      ed.append(kidsWrap);
      ed.append(el('p', { class: 'hint full' },
        'The group matches if ANY alternative matches. All alternatives share the anchor above.'));
      return ed;
    }

    if (c.kind === 'spawn_biome') {
      const bf = biomeFilterEditor(c, 'Spawn must be inside…');
      ed.append(el('div', { class: 'full' }, el('label', {}, 'Spawn biomes', bf.btn)));
      return ed;
    }
    if (c.kind === 'biome_at') {
      const bf = biomeFilterEditor(c, 'Biome at the point…');
      ed.append(
        anchorEditor(c),
        el('div', { class: 'full' }, el('label', {}, 'Biomes', bf.btn)),
        el('label', {}, 'Y level (320 = surface, negative = caves)',
          numInput(c.y ?? 320, v => { c.y = v ?? 320; dirty(); }, { min: -64, max: 320 })),
      );
      return ed;
    }
    if (c.kind === 'biome_area') {
      const bf = biomeFilterEditor(c, 'Biome that must appear nearby…');
      ed.append(
        anchorEditor(c),
        el('label', {}, 'Within (blocks)', numInput(c.distMax, v => { c.distMax = Math.min(1024, v ?? 500); dirty(); }, { min: 32, max: 1024 })),
        el('div', { class: 'full' }, el('label', {}, 'Biomes', bf.btn)),
        el('p', { class: 'hint full' }, 'Distance is checked on a 4-block grid (±4 blocks).'),
      );
      return ed;
    }

    /* structure / stronghold */
    const meta = STRUCT_BY_KEY.get(c.key);
    ed.append(
      el('label', {}, 'At least', numInput(c.countMin, v => { c.countMin = Math.max(1, v ?? 1); dirty(); }, { min: 1, max: 64 })),
      el('label', {}, 'At most (blank = ∞)', numInput(c.countMax, v => { c.countMax = v; dirty(); }, { min: 1, max: 64 })),
      el('label', {}, 'Max distance (blocks)', numInput(c.distMax, v => { c.distMax = Math.min(20000, v ?? 1000); dirty(); }, { min: 0, max: 20000 })),
      el('label', {}, 'Min distance (blocks)', numInput(c.distMin, v => { c.distMin = v ?? 0; dirty(); }, { min: 0, max: 20000 })),
      anchorEditor(c),
    );

    if (meta?.supportsBiomeFilter) {
      const bf = biomeFilterEditor(c, `Only ${meta.name.toLowerCase()}s in biome…`);
      ed.append(el('div', { class: 'full' }, el('label', {}, 'Village biome', bf.btn)));
    }
    if (meta?.hasBlacksmithOption) {
      ed.append(el('label', { class: 'checkbox-row full' },
        el('input', {
          type: 'checkbox', ...(c.blacksmith ? { checked: true } : {}),
          onChange: (e) => { c.blacksmith = e.target.checked; dirty(); },
        }),
        'Prefer villages with a blacksmith ',
        el('span', { class: 'badge warn' }, 'needs in-game verification'),
      ));
    }
    if (meta?.hasPortalOption) {
      const sel = el('select', {
        onChange: (e) => { c.portal = e.target.value === 'full12' ? 'full12' : 'any'; dirty(); },
      },
        el('option', { value: 'any' }, 'End portal: any state'),
        el('option', { value: 'full12' }, 'Fully filled End portal (12/12)'),
      );
      sel.value = c.portal === 'full12' ? 'full12' : 'any';
      ed.append(
        el('div', { class: 'full' }, el('label', {}, 'End portal', sel)),
        c.portal === 'full12'
          ? el('p', { class: 'hint full', style: 'color:var(--warn)' },
              'Portal frame eyes depend on terrain generation and cannot be verified by fast search. This seed will be reported as a stronghold match, and the 12/12 claim stays “requires additional verification”.')
          : null,
      );
    }
    if (meta?.note) {
      ed.append(el('p', { class: 'hint full' }, meta.note));
    }
    return ed;
  }

  function render() {
    const conds = getConditions();
    listEl.innerHTML = '';
    emptyEl.style.display = conds.length ? 'none' : '';
    if (conds.length) listEl.append(emptyEl);

    for (const c of conds) {
      const meta = STRUCT_BY_KEY.get(c.key);
      const icon = c.kind === 'spawn_biome' ? '🌍'
        : c.kind === 'biome_at' ? '📍'
        : c.kind === 'biome_area' ? '🗺️'
        : c.kind === 'or' ? '🔀'
        : (meta?.icon || '❓');

      const badges = [
        el('span', {
          class: 'badge ' + (c.required !== false ? 'req' : 'opt'),
          title: 'Click to toggle required/optional',
          onClick: (e) => { e.stopPropagation(); c.required = c.required === false ? true : false; onDirty(); },
        }, c.required !== false ? 'required' : 'optional'),
      ];
      const unverified =
        (c.blacksmith) || (c.portal === 'full12');
      if (unverified) badges.push(el('span', { class: 'badge warn', title: 'Part of this condition cannot be verified by fast search' }, '⚠ verify in-game'));

      const row = el('div', { class: 'cond' + (c.required === false ? ' optional' : '') });
      row.append(el('div', { class: 'cond-top' },
        el('span', { class: 'cond-icon' }, icon),
        el('div', { class: 'cond-label' },
          el('div', {}, describeCondition(c, conds)),
          el('div', { class: 'cond-sub' }, anchorLabel(c, conds) !== 'world spawn' ? 'anchored to ' + anchorLabel(c, conds) : ''),
        ),
        el('div', { class: 'cond-actions' }, ...badges,
          el('button', {
            title: expandedId === c.id ? 'Collapse' : 'Edit',
            onClick: () => { expandedId = expandedId === c.id ? null : c.id; render(); },
          }, expandedId === c.id ? '▴' : '✏️'),
          el('button', {
            title: 'Remove condition',
            onClick: () => { setConditions(getConditions().filter(x => x.id !== c.id)); },
          }, '✕'),
        ),
      ));
      if (expandedId === c.id) row.append(buildEditor(c));
      listEl.append(row);
    }
  }

  return { render };
}
