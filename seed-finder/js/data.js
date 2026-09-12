/* data.js — UI catalogs: biome groups, structure metadata, presets.
 * Biome ids/names/colors come from data-generated.js (authoritative 26.2
 * data dumped straight from the engine). */

import { BIOMES, STRUCT_TYPES, MC_VERSION_STRING, MC_VERSION_ID } from './data-generated.js';
import { ST } from './wasm-api.js';

export { MC_VERSION_STRING, MC_VERSION_ID };

export const BIOME_BY_ID = new Map(BIOMES.map(b => [b.id, b]));
export const BIOME_BY_NAME = new Map(BIOMES.map(b => [b.name, b.id]));

export function biomeName(id) {
  const b = BIOME_BY_ID.get(id);
  if (!b) return id >= 0 ? `Biome #${id}` : 'unknown';
  return prettyName(b.name);
}

export function biomeColor(id) {
  return BIOME_BY_ID.get(id)?.color || '#333';
}

export function prettyName(snake) {
  return snake.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/* ---- biome groups for the picker ---- */

const GROUP_RULES = [
  ['Plains & Forest', /^(plains|sunflower_plains|forest|flower_forest|birch_forest|old_growth_birch_forest|dark_forest|grove|forest_hills)$/],
  ['Mountains & Windswept', /^(meadow|cherry_grove|stony_peaks|jagged_peaks|frozen_peaks|snowy_slopes|windswept_hills|windswept_gravelly_hills|windswept_forest|windswept_savanna|stony_shore)$/],
  ['Snowy & Cold', /^(snowy_plains|snowy_taiga|snowy_beach|ice_spikes|frozen_ocean|deep_frozen_ocean|frozen_river|cold_ocean|deep_cold_ocean)$/],
  ['Taiga', /^(taiga|old_growth_pine_taiga|old_growth_spruce_taiga|snowy_taiga)$/],
  ['Desert & Badlands', /^(desert|badlands|wooded_badlands|eroded_badlands)$/],
  ['Jungle & Swamp', /^(jungle|sparse_jungle|bamboo_jungle|swamp|mangrove_swamp)$/],
  ['Savanna', /^(savanna|windswept_savanna)$/],
  ['Ocean & Rivers', /^(ocean|deep_ocean|warm_ocean|lukewarm_ocean|deep_lukewarm_ocean|cold_ocean|deep_cold_ocean|frozen_ocean|deep_frozen_ocean|river|frozen_river|beach|snowy_beach|stony_shore)$/],
  ['Mushroom & Void', /^(mushroom_fields|the_void)$/],
  ['Caves (underground)', /^(lush_caves|dripstone_caves|deep_dark|sulfur_caves)$/],
  ['The Nether', /^(nether_wastes|crimson_forest|warped_forest|soul_sand_valley|basalt_deltas)$/],
  ['The End', /^(the_end|end_highlands|end_midlands|end_barrens|small_end_islands)$/],
];

export const BIOME_GROUPS = (() => {
  const groups = GROUP_RULES.map(([label]) => ({ label, biomes: [] }));
  const rest = [];
  for (const b of BIOMES) {
    const gi = GROUP_RULES.findIndex(([, re]) => re.test(b.name));
    if (gi >= 0) groups[gi].biomes.push(b);
    else rest.push(b);
  }
  if (rest.length) groups.push({ label: 'Other', biomes: rest });
  return groups.filter(g => g.biomes.length);
})();

/* Spawn-fitting biomes are decided by the game itself (vanilla spawn
 * locator); this list is only a convenience default for presets. */
export const PLEASANT_SPAWN_BIOMES = [
  'plains', 'sunflower_plains', 'forest', 'flower_forest', 'birch_forest',
  'meadow', 'cherry_grove', 'taiga', 'savanna', 'jungle', 'swamp',
].map(n => BIOME_BY_NAME.get(n)).filter(id => id !== undefined);

/* ---- structures ---- */

export const STRUCTURES = [
  { key: 'village', type: ST.VILLAGE, name: 'Village', icon: '🏘️', dim: 0,
    supportsBiomeFilter: true, hasBlacksmithOption: true,
    note: 'Position and village biome are exact. Individual buildings (e.g. blacksmith) cannot be verified by fast search.' },
  { key: 'ancient_city', type: ST.ANCIENT_CITY, name: 'Ancient City', icon: '🏙️', dim: 0 },
  { key: 'trial_chambers', type: ST.TRIAL_CHAMBERS, name: 'Trial Chambers', icon: '⚔️', dim: 0 },
  { key: 'stronghold', name: 'Stronghold', icon: '🌀', special: 'stronghold', dim: 0,
    hasPortalOption: true,
    note: 'Ring positions are exact; the final ±112-block biome relocation is verified for candidates only.' },
  { key: 'woodland_mansion', type: ST.MANSION, name: 'Woodland Mansion', icon: '🏰', dim: 0, terrainHeuristic: true },
  { key: 'ocean_monument', type: ST.MONUMENT, name: 'Ocean Monument', icon: '🔱', dim: 0 },
  { key: 'pillager_outpost', type: ST.OUTPOST, name: 'Pillager Outpost', icon: '🚩', dim: 0 },
  { key: 'desert_pyramid', type: ST.DESERT_PYRAMID, name: 'Desert Pyramid', icon: '🏜️', dim: 0, terrainHeuristic: true },
  { key: 'jungle_temple', type: ST.JUNGLE_TEMPLE, name: 'Jungle Temple', icon: '🌴', dim: 0, terrainHeuristic: true },
  { key: 'swamp_hut', type: ST.SWAMP_HUT, name: 'Swamp Hut', icon: '🛖', dim: 0 },
  { key: 'igloo', type: ST.IGLOO, name: 'Igloo', icon: '⛄', dim: 0 },
  { key: 'shipwreck', type: ST.SHIPWRECK, name: 'Shipwreck', icon: '🚢', dim: 0 },
  { key: 'ocean_ruins', type: ST.OCEAN_RUIN, name: 'Ocean Ruins', icon: '🏛️', dim: 0 },
  { key: 'ruined_portal', type: ST.RUINED_PORTAL, name: 'Ruined Portal', icon: '🟪', dim: 0 },
  { key: 'trail_ruins', type: ST.TRAIL_RUINS, name: 'Trail Ruins', icon: '🧭', dim: 0 },
  { key: 'buried_treasure', type: ST.TREASURE, name: 'Buried Treasure', icon: '💰', dim: 0 },
  { key: 'mineshaft', type: ST.MINESHAFT, name: 'Mineshaft', icon: '⛏️', dim: 0,
    note: 'Mineshaft attempts are chunk-random; the reported position is the chunk origin.' },
  { key: 'amethyst_geode', type: ST.GEODE, name: 'Amethyst Geode', icon: '🔮', dim: 0 },
  { key: 'nether_fortress', type: ST.FORTRESS, name: 'Nether Fortress', icon: '🔥', dim: -1 },
  { key: 'bastion', type: ST.BASTION, name: 'Bastion Remnant', icon: '🐷', dim: -1 },
  { key: 'end_city', type: ST.END_CITY, name: 'End City', icon: '🌆', dim: 1, terrainHeuristic: true,
    note: 'Requires End islands with enough height — surface check is approximated.' },
];

export const STRUCT_BY_KEY = new Map(STRUCTURES.map(s => [s.key, s]));

/* ---- condition factories ---- */

let _cid = 0;
export function cid() { return 'c' + (++_cid) + '_' + Math.random().toString(36).slice(2, 6); }

export function newStructureCondition(key, anchorKind = 'spawn') {
  const s = STRUCT_BY_KEY.get(key);
  const c = {
    id: cid(),
    kind: s.special === 'stronghold' ? 'stronghold' : 'structure',
    key,
    struct: s.type,
    dim: s.dim,
    required: true,
    countMin: 1,
    countMax: null,
    distMin: 0,
    distMax: s.special === 'stronghold' ? 2500 : 1000,
    anchor: { kind: anchorKind },
    biomes: [],
    excludeBiomes: [],
  };
  return c;
}

export function newSpawnBiomeCondition(biomeIds) {
  return { id: cid(), kind: 'spawn_biome', required: true, biomes: biomeIds };
}

export function newBiomeAtCondition(biomeIds, anchorKind = 'spawn', y = 320) {
  return { id: cid(), kind: 'biome_at', required: true, biomes: biomeIds, anchor: { kind: anchorKind }, y };
}

export function newBiomeAreaCondition(biomeIds, distMax = 500, anchorKind = 'spawn') {
  return { id: cid(), kind: 'biome_area', required: true, biomes: biomeIds, distMax, distMin: 0, anchor: { kind: anchorKind } };
}

export function newOrChild(key = 'desert_pyramid', distMax = 1000) {
  const meta = STRUCT_BY_KEY.get(key);
  return {
    id: cid(), kind: 'structure', key, struct: meta.type, dim: meta.dim,
    countMin: 1, countMax: null, distMin: 0, distMax, anchor: null,
    biomes: [], excludeBiomes: [],
  };
}

export function newOrGroupCondition() {
  return {
    id: cid(), kind: 'or', required: true,
    anchor: { kind: 'spawn' },
    children: [newOrChild('desert_pyramid'), newOrChild('jungle_temple')],
  };
}

/* ---- presets (they just build conditions — always editable) ---- */

function village(count, distMax, opts = {}) {
  const c = newStructureCondition('village');
  c.countMin = count;
  c.distMax = distMax;
  Object.assign(c, opts);
  return c;
}

export const PRESETS = [
  {
    id: 'early',
    name: 'Early Game',
    icon: '🌱',
    blurb: 'Villages close to spawn, easy stronghold access, friendly biome.',
    build: () => [
      (() => { const c = newSpawnBiomeCondition(PLEASANT_SPAWN_BIOMES); c.required = false; return c; })(),
      village(2, 800),
      (() => { const c = newStructureCondition('stronghold'); c.distMax = 2500; return c; })(),
      (() => { const c = newStructureCondition('ruined_portal'); c.distMax = 600; c.required = false; return c; })(),
    ],
  },
  {
    id: 'god',
    name: 'God Seed',
    icon: '⚡',
    blurb: '3 villages, Ancient City and a Stronghold all near spawn.',
    build: () => [
      village(3, 1000, { blacksmith: true }),
      (() => { const c = newStructureCondition('ancient_city'); c.distMax = 1500; return c; })(),
      (() => { const c = newStructureCondition('stronghold'); c.distMax = 2000; c.portal = 'full12'; return c; })(),
      (() => { const c = newSpawnBiomeCondition([BIOME_BY_NAME.get('plains')].filter(x => x !== undefined)); c.required = false; return c; })(),
    ],
  },
  {
    id: 'speedrun',
    name: 'Speedrun',
    icon: '🏃',
    blurb: 'Stronghold and village as close to spawn as possible.',
    build: () => [
      (() => { const c = newStructureCondition('stronghold'); c.distMax = 1500; return c; })(),
      village(1, 500),
      (() => { const c = newSpawnBiomeCondition(PLEASANT_SPAWN_BIOMES); c.required = false; return c; })(),
    ],
  },
  {
    id: 'cherry',
    name: 'Cherry Grove Start',
    icon: '🌸',
    blurb: 'Spawn in a Cherry Grove with an Ancient City nearby.',
    build: () => [
      newSpawnBiomeCondition([BIOME_BY_NAME.get('cherry_grove')].filter(x => x !== undefined)),
      (() => { const c = newStructureCondition('ancient_city'); c.distMax = 1500; c.required = true; return c; })(),
    ],
  },
];

/* ---- human-readable condition labels ---- */

export function anchorLabel(c, conds) {
  const a = c.anchor || { kind: 'spawn' };
  switch (a.kind) {
    case 'spawn': return 'world spawn';
    case 'origin': return '(0, 0)';
    case 'coords': return `(${a.x}, ${a.z})`;
    case 'condition': {
      const ref = conds?.find(x => x.id === a.refId);
      return ref ? describeCondition(ref, conds, true) : 'another condition';
    }
    default: return 'world spawn';
  }
}

export function describeCondition(c, conds, short = false) {
  const biomeList = (ids) => ids.map(biomeName).join(', ');
  switch (c.kind) {
    case 'or': {
      const parts = (c.children || []).map(ch => {
        const meta = STRUCT_BY_KEY.get(ch.key) || { name: ch.key };
        return `${ch.countMin > 1 ? ch.countMin + '\u00d7 ' : ''}${meta.name} \u2264${ch.distMax}b`;
      });
      return `One of: ${parts.join('  OR  ')} (near ${anchorLabel(c, conds)})`;
    }
    case 'spawn_biome':
      return `Spawn biome: ${biomeList(c.biomes)}`;
    case 'biome_at':
      return `${biomeList(c.biomes)} at ${anchorLabel(c, conds)}`;
    case 'biome_area':
      return `${biomeList(c.biomes)} within ${c.distMax} blocks of ${anchorLabel(c, conds)}`;
    case 'stronghold': {
      let s = `${c.countMin > 1 ? c.countMin + ' strongholds' : 'Stronghold'} within ${c.distMax} blocks of ${anchorLabel(c, conds)}`;
      if (c.portal === 'full12') s += ' + full End Portal (unverifiable in fast search)';
      return s;
    }
    case 'structure': {
      const meta = STRUCT_BY_KEY.get(c.key) || { name: c.key };
      const what = c.countMin > 1 ? `${c.countMin}× ${meta.name}` : meta.name;
      let s = `${what} within ${c.distMax} blocks of ${anchorLabel(c, conds)}`;
      if (c.distMin) s = `${what} between ${c.distMin}–${c.distMax} blocks of ${anchorLabel(c, conds)}`;
      if (c.biomes?.length) s += ` [in: ${biomeList(c.biomes)}]`;
      if (c.blacksmith) s += ' + blacksmith (unverifiable)';
      return s;
    }
    default: return 'condition';
  }
}
