import { blockGeometry } from './lidar-grid.mjs';
import { LIDAR_SOURCE_V1, lidarBlockIdentity, LIDAR_PRODUCT_ID } from './lidar-block-cache.mjs';
import { MAX_GEOTIFF_BYTES } from './lidar-geotiff.mjs';

const RETRYABLE = new Set([429, 500, 502, 503, 504]);
const MAX_ATTEMPTS = 3;
const TIMEOUT_MS = 15_000;
const MAX_RETRY_WAIT_MS = 5_000;

export class LidarWmsError extends Error {
  constructor(code, message, { httpStatus = null, cause = null } = {}) {
    super(message, { cause });
    this.name = 'LidarWmsError';
    this.code = code;
    this.httpStatus = httpStatus;
  }
}

export function lidarGetMapUrl(block) {
  const { bbox, width, height } = blockGeometry(block);
  const url = new URL(LIDAR_SOURCE_V1.endpoint);
  // EPSG:2154 has east/north axes in WMS 1.3.0. Pixel centres are
  // xmin + (col + 0.5) * resolution, ymax - (row + 0.5) * resolution.
  const params = {
    SERVICE: LIDAR_SOURCE_V1.service, VERSION: LIDAR_SOURCE_V1.version,
    REQUEST: 'GetMap', LAYERS: LIDAR_SOURCE_V1.layer, STYLES: LIDAR_SOURCE_V1.style,
    CRS: LIDAR_SOURCE_V1.crs, FORMAT: LIDAR_SOURCE_V1.format,
    BBOX: bbox.join(','), WIDTH: String(width), HEIGHT: String(height),
  };
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  return url.toString();
}

function retryWait(header, attempt) {
  const present = typeof header === 'string' && header.trim() !== '';
  const seconds = present ? Number(header) : NaN;
  const date = present ? Date.parse(header) : NaN;
  const indicated = Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000
    : Number.isFinite(date) ? Math.max(0, date - Date.now()) : 250 * 2 ** (attempt - 1);
  return Math.min(MAX_RETRY_WAIT_MS, indicated);
}

async function waitWithSignal(ms, signal) {
  if (signal?.aborted) throw signal.reason;
  if (ms <= 0) return;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { cleanup(); resolve(); }, ms);
    const abort = () => { clearTimeout(timer); cleanup(); reject(signal.reason); };
    const cleanup = () => signal?.removeEventListener('abort', abort);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

async function boundedBody(response) {
  const length = Number(response.headers.get('content-length'));
  if (Number.isFinite(length) && length > MAX_GEOTIFF_BYTES) {
    await response.body?.cancel();
    throw new LidarWmsError('INVALID_RESPONSE', 'IGN GeoTIFF exceeds byte limit');
  }
  if (!response.body) throw new LidarWmsError('INVALID_RESPONSE', 'IGN response has no body');
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_GEOTIFF_BYTES) {
        await reader.cancel();
        throw new LidarWmsError('INVALID_RESPONSE', 'IGN GeoTIFF exceeds byte limit');
      }
      chunks.push(Buffer.from(value));
    }
  } finally { reader.releaseLock(); }
  return Buffer.concat(chunks, size);
}

export async function fetchLidarWmsBlock(block, {
  fetchImpl = globalThis.fetch, signal, timeoutMs = TIMEOUT_MS, onAttempt,
} = {}) {
  const url = lidarGetMapUrl(block);
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000) {
    throw new Error('Invalid LiDAR WMS timeout');
  }
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    signal?.throwIfAborted();
    const deadline = new AbortController();
    const timer = setTimeout(() => deadline.abort(new Error('IGN WMS timeout')), timeoutMs);
    const combined = signal ? AbortSignal.any([signal, deadline.signal]) : deadline.signal;
    try {
      onAttempt?.();
      const response = await fetchImpl(url, {
        method: 'GET', redirect: 'manual', signal: combined,
        headers: { Accept: 'image/geotiff', 'Accept-Encoding': 'identity' },
      });
      if (response.status === 204) {
        await response.body?.cancel();
        throw new LidarWmsError('NO_COVERAGE', 'IGN reports no elevation coverage',
          { httpStatus: response.status });
      }
      if (!response.ok) {
        const status = response.status;
        const retryAfter = response.headers.get('retry-after');
        await response.body?.cancel();
        if (status >= 300 && status < 400) {
          throw new LidarWmsError('REDIRECT_DENIED', 'IGN WMS redirected the request',
            { httpStatus: status });
        }
        if (RETRYABLE.has(status) && attempt < MAX_ATTEMPTS) {
          await waitWithSignal(retryWait(retryAfter, attempt), signal);
          continue;
        }
        throw new LidarWmsError('HTTP_ERROR', 'IGN WMS returned an error', { httpStatus: status });
      }
      const mime = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
      if (!['image/geotiff', 'image/tiff'].includes(mime)) {
        await response.body?.cancel();
        throw new LidarWmsError('INVALID_RESPONSE', 'IGN WMS did not return GeoTIFF');
      }
      const bytes = await boundedBody(response);
      return { bytes, downloadedBytes: bytes.length, attempts: attempt };
    } catch (error) {
      if (signal?.aborted) throw signal.reason;
      if (deadline.signal.aborted) {
        if (attempt < MAX_ATTEMPTS) {
          await waitWithSignal(retryWait(null, attempt), signal);
          continue;
        }
        throw new LidarWmsError('TIMEOUT', 'IGN WMS request timed out', { cause: error });
      }
      if (error instanceof LidarWmsError) throw error;
      if (attempt < MAX_ATTEMPTS) {
        await waitWithSignal(retryWait(null, attempt), signal);
        continue;
      }
      throw new LidarWmsError('NETWORK_ERROR', 'IGN WMS request failed', { cause: error });
    } finally { clearTimeout(timer); }
  }
}

// Internal service only: API business authorization remains outside the global cache.
export function createLidarWmsAcquisition(cacheFactory, {
  fetchImpl = globalThis.fetch, timeoutMs = TIMEOUT_MS,
} = {}) {
  const pending = new Map();
  async function getBlock(block, { signal } = {}) {
    signal?.throwIfAborted();
    const cache = await cacheFactory.get();
    const identity = lidarBlockIdentity(cache.product, block);
    const key = `${cache.entry.key}:${identity.key}`;
    let flight = pending.get(key);
    if (flight?.controller.signal.aborted) {
      await flight.promise.catch(() => {});
      if (pending.get(key) === flight) pending.delete(key);
      return getBlock(block, { signal });
    }
    const joined = Boolean(flight);
    if (!flight) {
      const controller = new AbortController();
      const timings = {};
      const started = performance.now();
      const stats = { requests: 0, downloadedBytes: 0 };
      const promise = cache.getOrCreate(block, async () => {
        controller.signal.throwIfAborted();
        const fetchStarted = performance.now();
        const acquired = await fetchLidarWmsBlock(block, {
          fetchImpl, signal: controller.signal, timeoutMs,
          onAttempt: () => { stats.requests++; },
        });
        timings.fetchMs = performance.now() - fetchStarted;
        stats.downloadedBytes = acquired.downloadedBytes;
        return acquired.bytes;
      }, { timings, signal: controller.signal }).then((result) => ({
        ...result, status: result.validation.hasElevation ? 'DATA' : 'NO_DATA',
        timings: { ...timings, totalMs: performance.now() - started }, ...stats,
      }));
      flight = { controller, promise, subscribers: 0 };
      pending.set(key, flight);
      void promise.finally(() => {
        if (pending.get(key) === flight) pending.delete(key);
      }).catch(() => {});
    }
    flight.subscribers++;
    let removeAbort = () => {};
    const aborted = new Promise((_, reject) => {
      if (!signal) return;
      const abort = () => {
        flight.subscribers--;
        if (flight.subscribers === 0) flight.controller.abort(signal.reason);
        reject(signal.reason);
      };
      signal.addEventListener('abort', abort, { once: true });
      removeAbort = () => {
        signal.removeEventListener('abort', abort);
        if (!signal.aborted) flight.subscribers--;
      };
      if (signal.aborted) abort();
    });
    try {
      const result = await Promise.race([flight.promise, aborted]);
      return joined ? { ...result, cache: 'WAIT' } : result;
    } catch (error) {
      if (error?.code === 'INVALID_GEOTIFF') {
        throw new LidarWmsError('INVALID_RESPONSE', 'IGN WMS returned an invalid GeoTIFF',
          { cause: error });
      }
      if (error instanceof LidarWmsError && error.code === 'NO_COVERAGE') {
        return { status: 'NO_COVERAGE', cache: joined ? 'WAIT' : 'MISS',
          key: identity.key, bytes: null, validation: null };
      }
      throw error;
    } finally {
      removeAbort();
      if (!signal && --flight.subscribers === 0 && pending.get(key) === flight) {
        // A completed task needs no cancellation; pending cleanup owns the map.
      }
    }
  }
  return { getBlock, productId: LIDAR_PRODUCT_ID, pendingCount: () => pending.size };
}
