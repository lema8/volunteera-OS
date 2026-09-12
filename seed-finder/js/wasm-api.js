/* wasm-api.js — loads seedlab.wasm and wraps it in a friendly API.
 * Works both on the main thread and inside workers (each gets its own
 * WebAssembly instance; no SharedArrayBuffer needed). */

export const WASM_BASE = new URL('../wasm/', import.meta.url).href;

/* Standard feature-detection: validates a tiny module using v128. */
export function supportsSimd() {
  try {
    const bytes = [0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123,
                   3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11];
    return WebAssembly.validate(new Uint8Array(bytes));
  } catch { return false; }
}

/* Dimensions (match cubiomes) */
export const DIM_OVERWORLD = 0;
export const DIM_NETHER = -1;
export const DIM_END = 1;

/* Structure type ids (match cubiomes enum StructureType) */
export const ST = {
  DESERT_PYRAMID: 1, JUNGLE_TEMPLE: 2, SWAMP_HUT: 3, IGLOO: 4,
  VILLAGE: 5, OCEAN_RUIN: 6, SHIPWRECK: 7, MONUMENT: 8, MANSION: 9,
  OUTPOST: 10, RUINED_PORTAL: 11, RUINED_PORTAL_N: 12, ANCIENT_CITY: 13,
  TREASURE: 14, MINESHAFT: 15, GEODE: 17, FORTRESS: 18, BASTION: 19,
  END_CITY: 20, TRAIL_RUINS: 23, TRIAL_CHAMBERS: 24,
};

export const FLAG_ABANDONED = 1;
export const FLAG_HEURISTIC = 2;

const WASI_STUB = new Proxy({}, { get: () => () => 0 });

export class SeedLab {
  constructor(instance) {
    this.ex = instance.exports;
    this._structBuf = this.ex.sl_alloc(512 * 16);
    this._shBuf = this.ex.sl_alloc(128 * 8);
    this._mineBuf = this.ex.sl_alloc(2048 * 8);
    this._spawnBuf = this.ex.sl_alloc(8);
    this._gridBuf = 0;
    this._gridCap = 0;
  }

  static async load(base = WASM_BASE) {
    const simd = supportsSimd();
    const url = base + (simd ? 'seedlab.wasm' : 'seedlab-nosimd.wasm');
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Failed to download engine (${resp.status})`);
    const bytes = await resp.arrayBuffer();
    const { instance } = await WebAssembly.instantiate(bytes, {
      wasi_snapshot_preview1: WASI_STUB, env: {},
    });
    const lab = new SeedLab(instance);
    lab.simd = simd;
    lab.init(0); /* 0 = default target version (26.2) */
    return lab;
  }

  _i32(ptr) { return new Int32Array(this.ex.memory.buffer, ptr); }

  init(mcId) { return this.ex.sl_init(mcId); }
  version() { return this.ex.sl_version(); }

  spawn(hi, lo) {
    const p = this._spawnBuf;
    this.ex.sl_spawn(hi, lo, p, p + 4);
    const o = this._i32(p);
    return { x: o[0], z: o[1] };
  }

  biomeAt(hi, lo, dim, scale, x, y, z) {
    return this.ex.sl_biome_at(hi, lo, dim, scale, x, y, z);
  }

  structs(hi, lo, dim, type, x0, z0, x1, z1, max = 512) {
    if (max > 512) max = 512;
    const p = this._structBuf;
    const n = this.ex.sl_structs(hi, lo, dim, type, x0, z0, x1, z1, max, p);
    const o = this._i32(p);
    const out = new Array(n);
    for (let i = 0; i < n; i++) {
      out[i] = { x: o[i * 4], z: o[i * 4 + 1], variant: o[i * 4 + 2], flags: o[i * 4 + 3] };
    }
    return out;
  }

  strongholds(hi, lo, x0, z0, x1, z1, exact, max = 128) {
    if (max > 128) max = 128;
    const p = this._shBuf;
    const n = this.ex.sl_strongholds(hi, lo, x0, z0, x1, z1, max, p, exact ? 1 : 0);
    const o = this._i32(p);
    const out = new Array(n);
    for (let i = 0; i < n; i++) out[i] = { x: o[i * 2], z: o[i * 2 + 1] };
    return out;
  }

  mineshafts(hi, lo, cx0, cz0, cw, ch, max = 2048) {
    if (max > 2048) max = 2048;
    const p = this._mineBuf;
    const n = this.ex.sl_mineshafts(hi, lo, cx0, cz0, cw, ch, max, p);
    const o = this._i32(p);
    const out = new Array(n);
    for (let i = 0; i < n; i++) out[i] = { x: o[i * 2], z: o[i * 2 + 1] };
    return out;
  }

  /* Biome ids over an area. scale: 4 (4x4 blocks per sample), 16, 64, 256.
   * (x0,z0) are in sample coordinates. Returns a copied Int32Array (w*h). */
  biomeGrid(hi, lo, dim, x0, z0, w, h, y, scale = 4) {
    const need = w * h;
    if (need > this._gridCap) {
      if (this._gridBuf) this.ex.sl_free(this._gridBuf);
      this._gridBuf = this.ex.sl_alloc(need * 4);
      this._gridCap = need;
    }
    const p = this._gridBuf;
    const n = this.ex.sl_biome_grid(hi, lo, dim, x0, z0, w, h, y, p, scale);
    if (n < 0) return null;
    return this._i32(p).slice(0, n);
  }
}

/* --- seed helpers (Minecraft seeds are signed 64-bit) --- */

export function seedToParts(seedStr) {
  let v = BigInt(seedStr);
  v = BigInt.asIntN(64, v);
  return {
    hi: Number(BigInt.asUintN(32, v >> 32n)) | 0,
    lo: Number(BigInt.asUintN(32, v)) | 0,
    str: v.toString(),
  };
}

export function partsToSeedString(hi, lo) {
  const v = BigInt.asIntN(64, (BigInt(hi >>> 0) << 32n) | BigInt(lo >>> 0));
  return v.toString();
}

export function javaHashSeed(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) {
    h = (Math.imul(31, h) + text.charCodeAt(i)) | 0;
  }
  return h.toString();
}

export function parseSeedInput(text) {
  text = text.trim();
  if (/^-?\d+$/.test(text)) return seedToParts(text);
  return seedToParts(javaHashSeed(text));
}

export function randomSeedParts(rng = Math.random) {
  const hi = (rng() * 0x100000000) | 0;
  const lo = (rng() * 0x100000000) | 0;
  return { hi, lo, str: partsToSeedString(hi, lo) };
}
