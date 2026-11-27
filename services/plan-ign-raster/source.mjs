import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';

// Official Géoplateforme URLs, verified against the downloaded style and
// PLAN.IGN metadata on 2026-09-24. No IGN resource is redistributed here.
export const STYLE_URL = 'https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/epure.json';
export const METADATA_URL = 'https://data.geopf.fr/tms/1.0.0/PLAN.IGN/metadata.json';
export const MAX_RESOURCE_BYTES = 8 * 1024 * 1024;
const MAX_REDIRECTS = 3;

export function assertOfficialUrl(value) {
  const url = new URL(value);
  const allowed = url.origin === 'https://data.geopf.fr' && (
    url.pathname.startsWith('/annexes/ressources/vectorTiles/') ||
    url.pathname.startsWith('/tms/1.0.0/PLAN.IGN/'));
  if (!allowed) throw new Error(`Unexpected PLAN.IGN resource URL: ${value}`);
  return url.href;
}

export function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function permanentError(message) {
  const error = new Error(message);
  error.retryable = false;
  return error;
}

function validateResource(url, bytes) {
  if (bytes.length > MAX_RESOURCE_BYTES) {
    throw permanentError(`IGN resource unexpectedly large: ${url}`);
  }
  if (!new URL(url).pathname.endsWith('.json')) return;
  let document;
  try { document = JSON.parse(bytes.toString('utf8')); } catch {
    throw permanentError(`Invalid IGN JSON resource: ${url}`);
  }
  if (!document || typeof document !== 'object' || Array.isArray(document) ||
      (url === STYLE_URL && (document.version !== 8 || !Array.isArray(document.layers) ||
        document.sources?.plan_ign?.type !== 'vector')) ||
      (url === METADATA_URL && (document.format !== 'pbf' ||
        !Array.isArray(document.tiles)))) {
    throw permanentError(`Invalid IGN JSON resource: ${url}`);
  }
}

async function readBounded(response, url) {
  const declared = response.headers.get('content-length');
  if (declared !== null && Number(declared) > MAX_RESOURCE_BYTES) {
    await response.body?.cancel();
    throw permanentError(`IGN resource unexpectedly large: ${url}`);
  }
  if (!response.body) return Buffer.alloc(0);
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_RESOURCE_BYTES) {
        void reader.cancel().catch(() => {});
        throw permanentError(`IGN resource unexpectedly large: ${url}`);
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks, total);
}

async function fetchOfficialResource(url, fetchImpl) {
  const signal = AbortSignal.timeout(30000);
  let current = url;
  for (let redirects = 0; ; redirects++) {
    const response = await fetchImpl(current, { signal, redirect: 'manual' });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get('location');
      if (!location || redirects >= MAX_REDIRECTS) {
        throw permanentError(`Unexpected IGN redirect: ${current}`);
      }
      try { current = assertOfficialUrl(new URL(location, current).href); } catch {
        throw permanentError(`IGN redirect outside official endpoints: ${current}`);
      }
      await response.body?.cancel();
      continue;
    }
    if (response.status !== 200) {
      const error = new Error(`IGN HTTP ${response.status}: ${current}`);
      error.retryable = [429, 500, 502, 503, 504].includes(response.status) ||
        (response.status === 404 && url.endsWith('.pbf'));
      throw error;
    }
    const bytes = await readBounded(response, url);
    validateResource(url, bytes);
    return bytes;
  }
}

export function createResourceCache(directory, { offline = false, fetchImpl = fetch,
  store = null, retainOnError = true } = {}) {
  const pending = new Map();
  const metrics = { requests: 0, diskHits: 0, networkMisses: 0,
    networkBytes: 0, networkMs: 0, networkWallMs: 0, diskMs: 0,
    retryResponses: 0, errors: 0 };
  let activeNetwork = 0;
  let networkBusyStart = 0;

  async function get(value) {
    const url = assertOfficialUrl(value);
    metrics.requests++;
    if (pending.has(url)) return pending.get(url);
    const storeKey = `${path.basename(directory)}/${sha256(url)}.bin`;
    const unpin = store?.pin?.([storeKey]) || (() => {});
    const task = (async () => {
      const filename = path.join(directory, `${sha256(url)}.bin`);
      const started = performance.now();
      let stale = null;
      try {
        if (store) {
          const result = await store.read(storeKey, (data) => {
            try { validateResource(url, data); return data.length <= MAX_RESOURCE_BYTES; }
            catch { return false; }
          });
          if (result) {
            stale = result.stale ? result.bytes : null;
            if (!result.stale || offline) {
              metrics.diskHits++;
              metrics.diskMs += performance.now() - started;
              return result.bytes;
            }
          }
        } else {
          const info = await stat(filename);
          if (info.size > MAX_RESOURCE_BYTES) {
            await rm(filename);
            throw Object.assign(new Error('Invalid cached resource'), { code: 'ENOENT' });
          }
          const data = await readFile(filename);
          try { validateResource(url, data); } catch {
            await rm(filename, { force: true });
            throw Object.assign(new Error('Invalid cached resource'), { code: 'ENOENT' });
          }
          metrics.diskHits++;
          metrics.diskMs += performance.now() - started;
          return data;
        }
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
      if (offline) throw new Error(`Offline cache miss: ${url}`);
      const fetchStarted = performance.now();
      if (activeNetwork++ === 0) networkBusyStart = fetchStarted;
      let bytes;
      try {
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            bytes = await fetchOfficialResource(url, fetchImpl);
            break;
          } catch (error) {
            if (attempt === 2 || error.retryable === false) throw error;
          }
          metrics.retryResponses++;
          await new Promise((resolve) => setTimeout(resolve, 300 * 2 ** attempt));
        }
        metrics.networkMisses++;
        metrics.networkBytes += bytes.length;
      } catch (error) {
        metrics.errors++;
        if (stale && retainOnError) return stale;
        throw error;
      } finally {
        metrics.networkMs += performance.now() - fetchStarted;
        if (--activeNetwork === 0) metrics.networkWallMs += performance.now() - networkBusyStart;
      }
      if (store) {
        try { await store.write(storeKey, bytes, (data) => {
          try { validateResource(url, data); return data.length <= MAX_RESOURCE_BYTES; }
          catch { return false; }
        }); } catch (error) {
          // Rendering may proceed with a fetched resource even when disk is full.
          metrics.errors++;
          if (error.code !== 'CACHE_CAPACITY') throw error;
        }
        return bytes;
      }
      await mkdir(directory, { recursive: true });
      const temporary = `${filename}.${process.pid}.${randomUUID()}.tmp`;
      try {
        await writeFile(temporary, bytes);
        await rename(temporary, filename);
      } finally {
        await rm(temporary, { force: true });
      }
      return bytes;
    })();
    pending.set(url, task);
    try { return await task; } finally { unpin(); pending.delete(url); }
  }

  async function invalidate(value) {
    const url = assertOfficialUrl(value);
    if (store) return store.invalidate(`${path.basename(directory)}/${sha256(url)}.bin`);
    await rm(path.join(directory, `${sha256(url)}.bin`), { force: true });
  }

  return { get, invalidate, snapshot: () => ({ ...metrics }), directory };
}

export function validateOfficialStyle(style, metadata) {
  if (style.version !== 8 || style.sources?.plan_ign?.type !== 'vector' ||
      style.sources.plan_ign.scheme !== 'xyz' ||
      metadata.format !== 'pbf' || metadata.crs !== 'EPSG:3857' ||
      !Number.isInteger(metadata.minzoom) || !Number.isInteger(metadata.maxzoom) ||
      metadata.maxzoom < metadata.minzoom ||
      !Array.isArray(metadata.tiles) || metadata.tiles.length !== 1 ||
      style.sources.plan_ign.tiles?.[0] !== metadata.tiles[0]) {
    throw new Error('Official style and PLAN.IGN metadata are incompatible');
  }
  for (const url of [style.glyphs, style.sprite, metadata.tiles[0],
    style.sources.plan_ign.url]) {
    if (typeof url !== 'string') throw new Error('Missing official style asset URL');
    assertOfficialUrl(url.replaceAll('{z}', '0').replaceAll('{x}', '0')
      .replaceAll('{y}', '0').replaceAll('{fontstack}', 'Source Sans Pro Regular')
      .replaceAll('{range}', '0-255'));
  }
  return {
    ...structuredClone(style),
    transition: { duration: 0, delay: 0 },
    sources: { ...style.sources, plan_ign: {
      type: 'vector', tiles: metadata.tiles, scheme: 'xyz',
      minzoom: metadata.minzoom, maxzoom: metadata.maxzoom,
    } },
  };
}

export function metricDelta(after, before) {
  return Object.fromEntries(Object.keys(after).map((key) => [key, after[key] - before[key]]));
}
