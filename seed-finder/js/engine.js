/* engine.js — evaluates a seed against a set of conditions.
 *
 * Pipeline (see spec "Search performance"):
 *   Stage 1: cheap checks first (biome-at-point, structure positions).
 *   Stage 2: stronghold rings (approximate ±112 blocks during bulk search).
 *   Stage 3: exact verification (stronghold biome checks) for candidates.
 *
 * Required conditions short-circuit: the first failure rejects the seed.
 * Optional conditions are only evaluated once all required ones pass. */

import { ST, DIM_OVERWORLD, DIM_NETHER, DIM_END, FLAG_HEURISTIC } from './wasm-api.js';

export const BIOME_AT_SURFACE_Y = 320;

export function condCost(c) {
  if (c.kind === 'or') {
    const kids = (c.children || []).map(condCost);
    return kids.length ? Math.min(...kids) : 10;
  }
  switch (c.kind) {
    case 'biome_at': return c.anchor && c.anchor.kind === 'spawn' ? 6 : 1;
    case 'spawn_biome': return 6;
    case 'structure':
      if (c.struct === ST.MINESHAFT) return 7;
      return 6 + Math.min(6, Math.floor((c.distMax ?? 1000) / 500));
    case 'stronghold': return 9;
    case 'biome_area': return 20 + Math.min(10, Math.floor((c.distMax ?? 500) / 200));
    default: return 10;
  }
}

/* Topological order: anchor references first, then cheap conditions first. */
export function orderConditions(conds) {
  const remaining = [...conds];
  const done = new Map();
  const ordered = [];
  let guard = 0;
  while (remaining.length && guard++ < 10000) {
    remaining.sort((a, b) => condCost(a) - condCost(b));
    let progressed = false;
    for (let i = 0; i < remaining.length; i++) {
      const c = remaining[i];
      const ref = c.anchor && c.anchor.kind === 'condition' ? c.anchor.refId : null;
      if (ref && !done.has(ref)) continue;
      ordered.push(c);
      done.set(c.id, true);
      remaining.splice(i, 1);
      progressed = true;
      break;
    }
    if (!progressed) { /* cycle — evaluate in whatever order */
      ordered.push(...remaining);
      break;
    }
  }
  return ordered;
}

function dist2(ax, az, bx, bz) {
  const dx = ax - bx, dz = az - bz;
  return Math.sqrt(dx * dx + dz * dz);
}

export class Evaluator {
  constructor(lab) {
    this.lab = lab;
  }

  /* Evaluate one seed. conds must be ordered (orderConditions).
   * opts: { exact: boolean } — exact=true runs the costly stronghold
   * verification (stage 3). Returns {match, perfect, score, reqMet, reqTotal, details}. */
  evaluate(hi, lo, orderedConds, opts = {}) {
    const ctx = { hi, lo, spawn: null, results: new Map() };
    const required = orderedConds.filter(c => c.required !== false);
    const optional = orderedConds.filter(c => c.required === false);

    let reqMet = 0;
    for (const c of required) {
      let r;
      try {
        r = this._evalCond(c, ctx, opts);
      } catch {
        r = { ok: false, instances: [], error: true };
      }
      ctx.results.set(c.id, r);
      if (r.ok) reqMet++;
      else {
        return {
          match: false, perfect: false,
          score: Math.round(70 * reqMet / required.length),
          reqMet, reqTotal: required.length, details: ctx.results,
        };
      }
    }

    let optMet = 0;
    for (const c of optional) {
      let r;
      try {
        r = this._evalCond(c, ctx, opts);
      } catch {
        r = { ok: false, instances: [], error: true };
      }
      ctx.results.set(c.id, r);
      if (r.ok) optMet++;
    }
    const optRatio = optional.length ? optMet / optional.length : 1;
    return {
      match: true, perfect: optRatio === 1,
      score: Math.round(70 + 30 * optRatio),
      reqMet, reqTotal: required.length, details: ctx.results,
    };
  }

  _spawn(ctx) {
    if (!ctx.spawn) ctx.spawn = this.lab.spawn(ctx.hi, ctx.lo);
    return ctx.spawn;
  }

  _anchorPoint(c, ctx) {
    const a = c.anchor || { kind: 'spawn' };
    switch (a.kind) {
      case 'spawn': return this._spawn(ctx);
      case 'origin': return { x: 0, z: 0 };
      case 'coords': return { x: a.x | 0, z: a.z | 0 };
      case 'condition': {
        const r = ctx.results.get(a.refId);
        if (!r || !r.ok || !r.instances.length) return null;
        return r.anchorPoint || r.instances[0];
      }
      default: return this._spawn(ctx);
    }
  }

  _biomeOk(id, c) {
    if (id < 0) return false;
    if (c.biomes && c.biomes.length && !c.biomes.includes(id)) return false;
    if (c.excludeBiomes && c.excludeBiomes.length && c.excludeBiomes.includes(id)) return false;
    return true;
  }

  _structureBiome(hi, lo, dim, inst) {
    if (inst.variant >= 0) return inst.variant;
    return this.lab.biomeAt(hi, lo, dim, 4, inst.x >> 2, 80, inst.z >> 2);
  }

  _evalCond(c, ctx, opts) {
    const lab = this.lab;
    const { hi, lo } = ctx;
    switch (c.kind) {

      case 'spawn_biome': {
        const sp = this._spawn(ctx);
        const id = lab.biomeAt(hi, lo, DIM_OVERWORLD, 1, sp.x, BIOME_AT_SURFACE_Y, sp.z);
        const ok = this._biomeOk(id, c);
        return { ok, instances: [{ x: sp.x, z: sp.z, biome: id }], anchorPoint: sp };
      }

      case 'biome_at': {
        const p = this._anchorPoint(c, ctx);
        if (!p) return { ok: false, instances: [] };
        const y = (typeof c.y === 'number') ? c.y : BIOME_AT_SURFACE_Y;
        const id = lab.biomeAt(hi, lo, DIM_OVERWORLD, 1, p.x, y, p.z);
        const ok = this._biomeOk(id, c);
        return { ok, instances: [{ x: p.x, z: p.z, biome: id }], anchorPoint: p };
      }

      case 'or': {
        let last = { ok: false, instances: [] };
        for (const childRaw of c.children || []) {
          const child = { ...childRaw, anchor: childRaw.anchor || c.anchor };
          let r;
          try { r = this._evalCond(child, ctx, opts); }
          catch { r = { ok: false, instances: [] }; }
          if (r.ok) return { ...r, ok: true, orWinner: child.id };
          last = r;
        }
        return last;
      }
      case 'structure': return this._evalStructure(c, ctx, opts);
      case 'stronghold': return this._evalStronghold(c, ctx, opts);
      case 'biome_area': return this._evalBiomeArea(c, ctx);
      default:
        return { ok: false, instances: [] };
    }
  }

  _evalStructure(c, ctx, opts) {
    const lab = this.lab;
    const { hi, lo } = ctx;
    const p = this._anchorPoint(c, ctx);
    if (!p) return { ok: false, instances: [] };

    const rMax = c.distMax ?? 1500;
    const rMin = c.distMin ?? 0;
    const pad = 64;
    const dim = c.dim ?? DIM_OVERWORLD;
    let raw;

    if (c.struct === ST.MINESHAFT) {
      const cx0 = Math.floor((p.x - rMax - pad) / 16);
      const cz0 = Math.floor((p.z - rMax - pad) / 16);
      const cx1 = Math.floor((p.x + rMax + pad) / 16);
      const cz1 = Math.floor((p.z + rMax + pad) / 16);
      raw = lab.mineshafts(hi, lo, cx0, cz0, cx1 - cx0 + 1, cz1 - cz0 + 1);
    } else {
      raw = lab.structs(hi, lo, dim, c.struct,
        p.x - rMax - pad, p.z - rMax - pad,
        p.x + rMax + pad, p.z + rMax + pad);
    }

    const insts = [];
    for (const inst of raw) {
      const d = dist2(inst.x, inst.z, p.x, p.z);
      if (d < rMin || d > rMax) continue;
      if (c.biomes?.length || c.excludeBiomes?.length) {
        const b = this._structureBiome(hi, lo, dim, inst);
        inst.biome = b;
        if (!this._biomeOk(b, c)) continue;
      }
      insts.push({ x: inst.x, z: inst.z, dist: Math.round(d),
                   biome: inst.biome, variant: inst.variant, flags: inst.flags });
    }
    insts.sort((a, b) => a.dist - b.dist);

    const need = c.countMin ?? 1;
    const maxc = c.countMax;
    const ok = insts.length >= need && (maxc == null || insts.length <= maxc);
    return {
      ok,
      instances: insts.slice(0, Math.max(need, Math.min(insts.length, 12))),
      anchorPoint: insts.length ? { x: insts[0].x, z: insts[0].z } : null,
      heuristic: insts.some(i => i.flags & FLAG_HEURISTIC),
    };
  }

  _evalStronghold(c, ctx, opts) {
    const lab = this.lab;
    const { hi, lo } = ctx;
    const p = this._anchorPoint(c, ctx);
    if (!p) return { ok: false, instances: [] };

    const rMax = c.distMax ?? 3000;
    const rMin = c.distMin ?? 0;
    const pad = 128;
    const exact = opts.exact ? 1 : 0;
    const raw = lab.strongholds(hi, lo,
      p.x - rMax - pad, p.z - rMax - pad,
      p.x + rMax + pad, p.z + rMax + pad, exact);

    const insts = [];
    for (const inst of raw) {
      const d = dist2(inst.x, inst.z, p.x, p.z);
      if (d < rMin || d > rMax) continue;
      insts.push({ x: inst.x, z: inst.z, dist: Math.round(d) });
    }
    insts.sort((a, b) => a.dist - b.dist);

    const need = c.countMin ?? 1;
    const ok = insts.length >= need && (c.countMax == null || insts.length <= c.countMax);
    return {
      ok,
      instances: insts.slice(0, Math.max(need, Math.min(insts.length, 12))),
      anchorPoint: insts.length ? { x: insts[0].x, z: insts[0].z } : null,
      approx: !exact,
    };
  }

  /* Biome-area check, two-pass:
   *  1. coarse scan at scale 16 (1 sample / 16x16 blocks) — ~16x cheaper;
   *  2. refine around the best coarse hits at scale 4 for exact distance.
   * Extremely thin biome strips thinner than 16 blocks can be missed by the
   * coarse scan — the UI labels these results as grid-sampled. */
  _evalBiomeArea(c, ctx) {
    const lab = this.lab;
    const { hi, lo } = ctx;
    const p = this._anchorPoint(c, ctx);
    if (!p) return { ok: false, instances: [] };

    let R = c.distMax ?? 500;
    if (R > 2048) R = 2048;
    const rMin = c.distMin ?? 0;
    const y = (typeof c.y === 'number') ? (c.y >> 2) : 80;

    /* --- pass 1: coarse (scale 16) --- */
    const CS = 16;
    let wC = Math.floor((2 * R) / CS) + 3;
    if (wC > 257) wC = 257;
    const x0C = Math.floor((p.x - R) / CS) - 1;
    const z0C = Math.floor((p.z - R) / CS) - 1;
    const gridC = lab.biomeGrid(hi, lo, DIM_OVERWORLD, x0C, z0C, wC, wC, y, 16);
    if (!gridC) return { ok: false, instances: [] };

    const hits = [];
    for (let j = 0; j < wC; j++) {
      const cz = (z0C + j) * CS + CS / 2;
      const row = j * wC;
      for (let i = 0; i < wC; i++) {
        const id = gridC[row + i];
        if (!this._biomeOk(id, c)) continue;
        const cx = (x0C + i) * CS + CS / 2;
        const d = dist2(cx, cz, p.x, p.z);
        if (d <= R + 32) hits.push({ d, cx, cz, id, i, j });
      }
    }
    if (!hits.length) return { ok: false, instances: [] };
    hits.sort((a, b) => a.d - b.d);

    /* --- pass 2: refine at scale 4 around the nearest coarse hits --- */
    let best = Infinity, bx = 0, bz = 0, foundId = -1;
    const W = 128;                                  /* refine window, blocks */
    const wF = Math.floor(W / 4) + 3;
    for (const h of hits.slice(0, 4)) {
      const x0F = Math.floor((h.cx - W / 2) / 4);
      const z0F = Math.floor((h.cz - W / 2) / 4);
      const gridF = lab.biomeGrid(hi, lo, DIM_OVERWORLD, x0F, z0F, wF, wF, y, 4);
      if (!gridF) continue;
      for (let j = 0; j < wF; j++) {
        const cz = (z0F + j) * 4 + 2;
        const row = j * wF;
        for (let i = 0; i < wF; i++) {
          const id = gridF[row + i];
          if (!this._biomeOk(id, c)) continue;
          const cx = (x0F + i) * 4 + 2;
          const d = dist2(cx, cz, p.x, p.z);
          if (d < best) { best = d; bx = cx; bz = cz; foundId = id; }
        }
      }
    }
    if (best === Infinity) {
      /* refinement found nothing (edge effects) — fall back to coarse hit */
      const h = hits[0];
      best = h.d; bx = h.cx; bz = h.cz; foundId = h.id;
    }
    const ok = best >= Math.max(0, rMin - 8) && best <= R + 8;
    return {
      ok,
      instances: [{ x: bx, z: bz, dist: Math.round(best), biome: foundId, approxGrid: true }],
      anchorPoint: { x: bx, z: bz },
    };
  }
}

/* Trim a result for transport to the main thread. */
export function summarize(res, orderedConds) {
  const details = {};
  for (const c of orderedConds) {
    const r = res.details.get(c.id);
    if (!r) continue;
    details[c.id] = {
      ok: r.ok,
      heuristic: r.heuristic || false,
      approx: r.approx || false,
      instances: r.instances.map(i => ({
        x: i.x, z: i.z, dist: i.dist, biome: i.biome, variant: i.variant, flags: i.flags,
      })),
    };
  }
  return { match: res.match, perfect: res.perfect, score: res.score, details };
}
