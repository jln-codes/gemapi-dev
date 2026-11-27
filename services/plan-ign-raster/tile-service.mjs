import {
  metatileForTile,
  metatileTiles,
} from './geometry.mjs';
import {
  encodeRemaining,
  encodeTile,
  prioritizedTiles,
} from './encoding.mjs';
import { RasterCache, tileKey } from './raster-cache.mjs';

export const MAX_ZOOM = 18;
export const REMAINING_CONCURRENCY = 2;
export const MAX_METATILE_JOBS = 8;

export class ServiceBusyError extends Error {
  constructor() {
    super('PLAN.IGN render queue is full');
  }
}

export function metatileKey(meta) {
  return `${meta.z}/${meta.originX}/${meta.originY}`;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  // Some siblings have no waiter. Keep their rejection from becoming an
  // unhandled rejection while preserving rejection for a later WAIT request.
  promise.catch(() => {});
  return { promise, resolve, reject, settled: false };
}

export function parseTilePath(pathname) {
  const match = /^\/tiles\/([a-z0-9][a-z0-9-]{0,63})\/(\d+)\/(\d+)\/(\d+)\.webp$/.exec(pathname);
  if (!match) return null;
  const version = match[1];
  const [z, x, y] = match.slice(2).map(Number);
  if (![z, x, y].every(Number.isSafeInteger) || z < 0 || z > MAX_ZOOM) return null;
  const world = 2 ** z;
  if (x < 0 || y < 0 || x >= world || y >= world) return null;
  return { version, z, x, y };
}

export class PlanIgnTileService {
  constructor({
    cacheRoot,
    renderer,
    cache = new RasterCache(cacheRoot, renderer.cacheVersion),
    logger = console,
    encode = encodeTile,
    encodeRest = encodeRemaining,
    httpMaxAgeSeconds = 3600,
  }) {
    this.cache = cache;
    this.renderer = renderer;
    this.logger = logger;
    this.encode = encode;
    this.encodeRest = encodeRest;
    this.httpMaxAgeSeconds = httpMaxAgeSeconds;
    this.inflight = new Map();
    this.closing = false;
    this.secondaryActive = 0;
    this.secondaryQueue = [];
  }

  get isHealthy() {
    return !this.closing && this.renderer.isHealthy?.() !== false;
  }

  async getTile(tile, onStatus = () => {}) {
    if (this.closing) throw new ServiceBusyError();
    const cache = this.cache;
    const key = tileKey(tile);
    const cached = await cache.read(tile);
    if (this.closing) throw new ServiceBusyError();
    if (cached) {
      this.logger.log(`HIT  z${key}`);
      onStatus('HIT');
      return cached;
    }
    if (!this.isHealthy) throw new ServiceBusyError();

    const meta = metatileForTile(tile.z, tile.x, tile.y);
    const metaKey = `${this.renderer.cacheVersion}/${metatileKey(meta)}`;
    const pending = this.inflight.get(metaKey);
    if (pending) {
      this.logger.log(`WAIT z${key} meta=${metaKey}`);
      onStatus('WAIT');
      const job = await pending;
      return job.tiles.get(key).promise;
    }

    if (this.inflight.size >= MAX_METATILE_JOBS) throw new ServiceBusyError();

    this.logger.log(`MISS z${key} meta=${metaKey}`);
    this.logger.log(`QUEUED meta=${metaKey}`);
    onStatus('MISS');
    const tiles = new Map(metatileTiles(meta).map((item) => [tileKey(item), deferred()]));
    const job = { tiles, completion: null };
    const jobPromise = Promise.resolve(job);
    this.inflight.set(metaKey, jobPromise);
    const unpin = cache.pinTiles?.(metatileTiles(meta)) || (() => {});
    const prepare = () => this.#prepareMetatile(meta, tile, job, cache);
    job.completion = prepare()
      .catch((error) => {
        this.#rejectUnsettled(job, error);
        this.logger.error(`ERROR meta=${metaKey} ${error.message}`);
      })
      .finally(() => {
        unpin();
        if (this.inflight.get(metaKey) === jobPromise) this.inflight.delete(metaKey);
      });
    return job.tiles.get(key).promise;
  }

  async #prepareMetatile(meta, requested, job, cache) {
    const metaKey = metatileKey(meta);
    let rgba;
    try {
      rgba = await this.renderer.render(meta);
    } catch (error) {
      this.#rejectUnsettled(job, error);
      throw error;
    }

    const ordered = prioritizedTiles(meta, requested);
    const requestedDelivery = job.tiles.get(tileKey(requested));
    try {
      const encoded = await this.encode(rgba, meta, requested);
      await cache.write(requested, encoded.buffer);
      requestedDelivery.settled = true;
      requestedDelivery.resolve(encoded.buffer);
      // The requested buffer is now available to HTTP. Only from this point
      // onward may secondary encoding work be submitted.
    } catch (error) {
      this.#rejectUnsettled(job, error);
      throw error;
    }

    try {
      await this.encodeRest(ordered.slice(1), REMAINING_CONCURRENCY, (tile) =>
        this.#queueSecondary(async () => {
          const delivery = job.tiles.get(tileKey(tile));
          try {
            const encoded = await this.encode(rgba, meta, tile);
            await this.#writeWithoutFailingResponse(tile, encoded.buffer, metaKey, cache);
                  delivery.settled = true;
            delivery.resolve(encoded.buffer);
          } catch (error) {
            delivery.settled = true;
            delivery.reject(error);
            this.logger.error(`ERROR z${tileKey(tile)} meta=${metaKey} ${error.message}`);
          }
          return tile;
        }));
    } catch (error) {
      this.#rejectUnsettled(job, error);
      throw error;
    }
  }

  #queueSecondary(task) {
    return new Promise((resolve, reject) => {
      this.secondaryQueue.push({ task, resolve, reject });
      this.#drainSecondary();
    });
  }

  #drainSecondary() {
    while (this.secondaryActive < REMAINING_CONCURRENCY && this.secondaryQueue.length) {
      const item = this.secondaryQueue.shift();
      this.secondaryActive++;
      Promise.resolve().then(item.task).then(item.resolve, item.reject).finally(() => {
        this.secondaryActive--;
        this.#drainSecondary();
      });
    }
  }

  async #writeWithoutFailingResponse(tile, bytes, metaKey, cache) {
    try {
      await cache.write(tile, bytes);
    } catch (error) {
      this.logger.error(`ERROR cache z${tileKey(tile)} meta=${metaKey} ${error.message}`);
    }
  }

  #rejectUnsettled(job, error) {
    for (const delivery of job.tiles.values()) {
      if (delivery.settled) continue;
      delivery.settled = true;
      delivery.reject(error);
    }
  }

  async close() {
    this.closing = true;
    const jobs = await Promise.all([...this.inflight.values()]);
    await Promise.allSettled(jobs.map((job) => job.completion));
    await this.renderer.release?.();
  }
}
