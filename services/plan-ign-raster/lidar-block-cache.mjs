import { createHash } from 'node:crypto';
import { blockGeometry, LIDAR_GRID_V1, lidarGrid } from './lidar-grid.mjs';
import { MAX_GEOTIFF_BYTES, validateLidarGeoTiff } from './lidar-geotiff.mjs';

export const LIDAR_PRODUCT_ID = 'ign-lidar-hd-mnt';
export const LIDAR_SOURCE_V1 = Object.freeze({ endpoint: 'https://data.geopf.fr/wms-r/wms',
  service: 'WMS', version: '1.3.0', layer: 'IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93',
  style: 'normal', format: 'image/geotiff', crs: 'EPSG:2154' });
const MAX_ACTIVE = 2;
const MAX_WAITING = 16;

function boundedWork() {
  let active = 0;
  const waiting = [];
  return async (task) => {
    if (active >= MAX_ACTIVE) {
      if (waiting.length >= MAX_WAITING) throw new Error('LiDAR block work queue full');
      await new Promise((resolve) => waiting.push(resolve));
    } else active++;
    try { return await task(); }
    finally {
      if (waiting.length) waiting.shift()(); // transfer the occupied slot
      else active--;
    }
  };
}

function coordKey(index) { return `${index < 0 ? 'n' : 'p'}${Math.abs(index)}`; }

export function lidarBlockIdentity(product, block, grid = lidarGrid(block?.level),
  source = LIDAR_SOURCE_V1) {
  const { bbox } = blockGeometry(block, grid);
  if (product?.artifact?.format !== 'geotiff-float32' ||
      product.artifact.grid_id !== grid.id ||
      !/^[a-z][a-z0-9-]+$/.test(product.artifact.generation)) {
    throw new Error('LiDAR product and grid differ');
  }
  const fingerprint = createHash('sha256').update(JSON.stringify({ grid, source })).digest('hex').slice(0, 24);
  const window = block.window ? `-${block.window.join('-')}` : '';
  return { key: `${product.artifact.generation}/${fingerprint}/${coordKey(block.ix)}/${coordKey(block.iy)}${window}.tif`,
    bbox, generation: product.artifact.generation, fingerprint };
}

class LidarBlockCache {
  constructor(entry, product, schedule) {
    this.entry = entry;
    this.product = product;
    this.schedule = schedule;
    this.inFlight = new Map();
  }

  #share(key, task) {
    if (this.inFlight.has(key)) return this.inFlight.get(key);
    const promise = Promise.resolve().then(task);
    this.inFlight.set(key, promise);
    void promise.finally(() => {
      if (this.inFlight.get(key) === promise) this.inFlight.delete(key);
    }).catch(() => {});
    return promise;
  }

  async #read(block, key) {
    let validation;
    const check = async (bytes) => {
      try { validation = await validateLidarGeoTiff(bytes, block); return true; }
      catch { return false; }
    };
    const existing = await this.entry.tiles.read(key, check, { maxBytes: MAX_GEOTIFF_BYTES });
    return existing ? { ...existing, validation } : null;
  }

  async read(block) {
    const { key } = lidarBlockIdentity(this.product, block);
    return this.#share(`read:${key}`, () => this.schedule(() => this.#read(block, key)));
  }

  async write(block, bytes) {
    const { key } = lidarBlockIdentity(this.product, block);
    return this.schedule(async () => {
      await validateLidarGeoTiff(bytes, block);
      await this.entry.tiles.write(key, bytes, () => true);
      return { key, bytes: bytes.length };
    });
  }

  async getOrCreate(block, producer, { timings, signal } = {}) {
    if (typeof producer !== 'function') throw new Error('A trusted block producer is required');
    const identity = lidarBlockIdentity(this.product, block);
    return this.#share(`produce:${identity.key}`, () => this.schedule(async () => {
      const unpin = this.entry.tiles.pin([identity.key]);
      try {
      signal?.throwIfAborted();
      const readStarted = performance.now();
      const existing = await this.#read(block, identity.key);
      if (timings) timings.readMs = performance.now() - readStarted;
      if (existing) return { ...existing, key: identity.key, cache: 'HIT' };
      const bytes = await producer({ ...block, bbox: identity.bbox });
      const validateStarted = performance.now();
      let validation;
      try { validation = await validateLidarGeoTiff(bytes, block); }
      catch (cause) {
        const error = new Error('Invalid acquired LiDAR GeoTIFF', { cause });
        error.code = 'INVALID_GEOTIFF';
        throw error;
      }
      if (timings) timings.validationMs = performance.now() - validateStarted;
      const writeStarted = performance.now();
      await this.entry.tiles.write(identity.key, bytes, () => true, { signal });
      if (timings) timings.writeMs = performance.now() - writeStarted;
      return { bytes, key: identity.key, validation, cache: 'CREATED' };
      } finally { unpin(); }
    }));
  }
}

export function createLidarBlockCacheFactory(registry, catalogue) {
  const product = catalogue.products.get(LIDAR_PRODUCT_ID);
  if (product?.scope !== 'global' || product.artifact?.grid_id !== LIDAR_GRID_V1.id ||
      product.artifact?.format !== 'geotiff-float32') throw new Error('Missing global numeric LiDAR cache product');
  const cache = new LidarBlockCache(registry.get(LIDAR_PRODUCT_ID), product, boundedWork());
  return { async get() { await registry.resolve(LIDAR_PRODUCT_ID); return cache; } };
}
