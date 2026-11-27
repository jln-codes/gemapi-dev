import { createHash } from 'node:crypto';
import sharp from 'sharp';

const MAX_TILE_BYTES = 4 * 1024 * 1024;
const RETRYABLE = new Set([429, 500, 502, 503, 504]);
const MIME = { png: 'image/png', jpeg: 'image/jpeg' };
const EXTENSION = { png: 'png', jpeg: 'jpg' };

export class WmtsError extends Error {
  constructor(message, status = 503) {
    super(message);
    this.status = status;
  }
}

export async function validWmtsImage(bytes, format) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 16 || bytes.length > MAX_TILE_BYTES) return false;
  if (format === 'png') {
    if (!bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
        !bytes.subarray(-8, -4).equals(Buffer.from('IEND'))) return false;
  } else if (format === 'jpeg') {
    if (bytes[0] !== 0xff || bytes[1] !== 0xd8 ||
        bytes[bytes.length - 2] !== 0xff || bytes[bytes.length - 1] !== 0xd9) return false;
  } else return false;
  try {
    const image = sharp(bytes, { failOn: 'error', limitInputPixels: 65_536 });
    const meta = await image.metadata();
    if (meta.format !== format || meta.width !== 256 || meta.height !== 256) return false;
    await image.raw().toBuffer();
    return true;
  } catch { return false; }
}

function validateDefinition(definition, { allowInsecureProvider = false } = {}) {
  const endpoint = new URL(definition.provider_endpoint);
  if ((endpoint.protocol !== 'https:' && !(allowInsecureProvider && endpoint.protocol === 'http:')) ||
      endpoint.username || endpoint.password || endpoint.search || endpoint.hash ||
      !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(definition.id) ||
      !MIME[definition.tile_format] ||
      !definition.provider_layer || !definition.provider_style || !definition.tile_matrix_set ||
      !/^[a-z0-9][a-z0-9-]*$/.test(definition.cache_generation || '')) {
    throw new Error('Invalid WMTS runtime definition');
  }
  const limits = definition.wmts_matrix_limits;
  if (!Array.isArray(limits) || limits.length !==
      definition.native_max_zoom - definition.native_min_zoom + 1) {
    throw new Error('Incomplete WMTS matrix limits');
  }
  for (let i = 0; i < limits.length; i++) {
    const row = limits[i];
    const z = definition.native_min_zoom + i;
    if (!Array.isArray(row) || row.length !== 5 || row.some((value) => !Number.isSafeInteger(value)) ||
        row[0] !== z || row[1] < 0 || row[1] > row[2] || row[2] >= 2 ** z ||
        row[3] < 0 || row[3] > row[4] || row[4] >= 2 ** z) {
      throw new Error('Invalid WMTS matrix bounds');
    }
  }
}

export class WmtsRasterService {
  constructor({ definition, cache, fetchImpl = fetch, allowInsecureProvider = false,
    timeoutMs = 5000, retries = 2, concurrency = 4, queueLimit = 64,
 }) {
    validateDefinition(definition, { allowInsecureProvider });
    if (!cache?.tiles || !cache?.policy || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 ||
        !Number.isSafeInteger(retries) || retries < 0 || retries > 3 ||
        !Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 16 ||
        !Number.isSafeInteger(queueLimit) || queueLimit < 0 || queueLimit > 128) {
      throw new Error('Invalid WMTS service configuration');
    }
    this.definition = definition;
    this.cache = cache;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.retries = retries;
    this.concurrency = concurrency;
    this.queueLimit = queueLimit;
    this.active = 0;
    this.waiters = [];
    this.inflight = new Map();
    this.closing = false;
    const identity = [definition.provider_endpoint, definition.provider_layer,
      definition.provider_style, definition.tile_matrix_set, definition.tile_format,
      definition.wmts_matrix_limits, definition.cache_generation];
    this.cacheVersion = `${definition.cache_generation}-${createHash('sha256')
      .update(JSON.stringify(identity)).digest('hex').slice(0, 16)}`;
    cache.tiles.activeGeneration = this.cacheVersion;
  }

  tilejson() {
    const d = this.definition;
    return { tilejson: '3.0.0', tiles: [
      `/basemap/${d.id}/tiles/${this.cacheVersion}/{z}/{x}/{y}.${EXTENSION[d.tile_format]}`,
    ], minzoom: d.native_min_zoom, maxzoom: d.native_max_zoom, scheme: 'xyz',
    format: d.tile_format, attribution: d.attribution };
  }

  validateTile({ z, x, y }) {
    const d = this.definition;
    if (![z, x, y].every(Number.isSafeInteger) || z < d.native_min_zoom ||
        z > d.native_max_zoom || x < 0 || y < 0 || x >= 2 ** z || y >= 2 ** z) {
      throw new WmtsError('Invalid XYZ coordinate', 400);
    }
    const [, minRow, maxRow, minCol, maxCol] = d.wmts_matrix_limits[z - d.native_min_zoom];
    if (y < minRow || y > maxRow || x < minCol || x > maxCol) {
      throw new WmtsError('Outside advertised WMTS matrix limits', 404);
    }
  }

  async #slot(task) {
    if (this.closing) throw new WmtsError('WMTS service shutting down');
    if (this.active >= this.concurrency) {
      if (this.waiters.length >= this.queueLimit) throw new WmtsError('WMTS queue full');
      await new Promise((resolve) => this.waiters.push(resolve));
    } else this.active++;
    try { return await task(); } finally {
      const next = this.waiters.shift();
      if (next) next();
      else this.active--;
    }
  }

  #providerUrl({ z, x, y }) {
    const d = this.definition;
    const url = new URL(d.provider_endpoint);
    const params = { SERVICE: 'WMTS', REQUEST: 'GetTile', VERSION: '1.0.0',
      LAYER: d.provider_layer, STYLE: d.provider_style, FORMAT: MIME[d.tile_format],
      TILEMATRIXSET: d.tile_matrix_set, TILEMATRIX: String(z),
      TILEROW: String(y), TILECOL: String(x) };
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
    return url;
  }

  async #fetchOnce(tile) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(this.#providerUrl(tile), {
        signal: controller.signal, redirect: 'manual', headers: { Accept: MIME[this.definition.tile_format] },
      });
      if (response.status === 404) throw new WmtsError('WMTS tile absent', 404);
      if (!response.ok) throw new WmtsError(`WMTS provider HTTP ${response.status}`,
        RETRYABLE.has(response.status) ? 503 : 502);
      if (response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase() !==
          MIME[this.definition.tile_format]) throw new WmtsError('WMTS MIME mismatch');
      const chunks = [];
      let size = 0;
      for await (const chunk of response.body) {
        size += chunk.length;
        if (size > MAX_TILE_BYTES) throw new WmtsError('WMTS tile too large');
        chunks.push(chunk);
      }
      const bytes = Buffer.concat(chunks);
      if (!(await validWmtsImage(bytes, this.definition.tile_format))) {
        throw new WmtsError('Invalid WMTS image');
      }
      return bytes;
    } catch (error) {
      controller.abort();
      if (error instanceof WmtsError) throw error;
      throw new WmtsError(`WMTS transport unavailable: ${error.message}`);
    } finally { clearTimeout(timer); }
  }

  async #fetchWithRetries(tile) {
    for (let attempt = 0; ; attempt++) {
      try { return await this.#fetchOnce(tile); } catch (error) {
        if (error.status !== 503 || attempt >= this.retries) throw error;
        await new Promise((resolve) => setTimeout(resolve, 100 * 2 ** attempt));
      }
    }
  }

  async getTile(tile, report = () => {}) {
    this.validateTile(tile);
    const cache = this.cache;
    cache.tiles.activeGeneration = this.cacheVersion;
    const key = `${this.cacheVersion}/${tile.z}/${tile.x}/${tile.y}.${EXTENSION[this.definition.tile_format]}`;
    const pendingKey = `${key}`;
    const valid = (bytes) => validWmtsImage(bytes, this.definition.tile_format);
    const cached = await cache.tiles.read(key, valid);
    if (cached && !cached.stale) { report('HIT'); return cached.bytes; }
    if (this.inflight.has(pendingKey)) { report('WAIT'); return this.inflight.get(pendingKey); }
    report('MISS');
    const unpin = cache.tiles.pin([key]);
    const work = this.#slot(async () => {
      try {
        const bytes = await this.#fetchWithRetries(tile);
        await cache.tiles.write(key, bytes, valid);
        return bytes;
      } catch (error) {
        if (cached && cache.policy.retain_on_error && error instanceof WmtsError &&
            error.status === 503) { report('STALE'); return cached.bytes; }
        throw error;
      }
    });
    this.inflight.set(pendingKey, work);
    try { return await work; } finally { unpin(); this.inflight.delete(pendingKey); }
  }

  async close() {
    this.closing = true;
    await Promise.allSettled([...this.inflight.values()]);
  }
}

export function createWmtsServices(catalogue, caches, options = {}) {
  const services = new Map();
  for (const [id, definition] of catalogue.definitions) {
    if (definition.adapter !== 'wmts_raster' || !definition.enabled) continue;
    const cache = caches.get(id);
    if (definition.wmts_publication !== 'authorized' ||
        cache?.policy.policy !== 'persistent-reconstructible' ||
        cache.policy.tile_max_age_seconds < 1 ||
        cache.policy.http_max_age_seconds > cache.policy.tile_max_age_seconds ||
        definition.public_tilejson_url !== `/basemap/${id}.json`) {
      throw new Error(`WMTS ${id} is not authorized or its cache is not operational`);
    }
    services.set(id, new WmtsRasterService({ definition, cache, ...options }));
  }
  return services;
}
