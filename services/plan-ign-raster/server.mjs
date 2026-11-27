#!/usr/bin/env node
import http from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { DEFAULT_CACHE_GENERATION, validateGeneration } from './cache-identity.mjs';
import { loadCachePolicies } from './cache-policy.mjs';
import { assertBasemapProductCompatibility, loadCacheProducts } from './cache-products.mjs';
import { cacheStats, createCacheRegistry } from './cache-registry.mjs';
import { RasterCache } from './raster-cache.mjs';
import { PlanIgnTileService, parseTilePath } from './tile-service.mjs';
import { createWmtsServices, WmtsError } from './wmts-raster.mjs';
import { CacheSettings } from './cache-settings.mjs';
import { createLidarBlockCacheFactory, LIDAR_PRODUCT_ID } from './lidar-block-cache.mjs';
import { createLidarWmsAcquisition, LidarWmsError } from './lidar-wms.mjs';
import { createLidarDistribution } from './lidar-distribution.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_HOST = '127.0.0.1';
export const DEFAULT_PORT = 8788;

const TILEJSON = Object.freeze({
  tilejson: '3.0.0',
  minzoom: 0,
  maxzoom: 18,
  scheme: 'xyz',
  attribution: '© IGN — PLAN.IGN',
});

function commonHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
  };
}

function send(res, status, headers, body = '') {
  res.writeHead(status, { ...commonHeaders(), ...headers });
  res.end(body);
}

function sendInternal(res, status, headers, body = '') {
  res.writeHead(status, headers);
  res.end(body);
}

function authenticated(req, token) {
  if (!token || token.length < 32) return false;
  const supplied = /^Bearer (\S+)$/.exec(req.headers.authorization || '')?.[1] || '';
  const digest = (value) => createHash('sha256').update(value).digest();
  return timingSafeEqual(digest(supplied), digest(token));
}

async function lidarJsonBody(req) {
  const declared = Number(req.headers['content-length']);
  if (Number.isFinite(declared) && declared > 2 * 1024 * 1024) {
    const error = new Error('LiDAR request too large'); error.status = 413; throw error;
  }
  const parts = [];
  let size = 0;
  for await (const part of req) {
    size += part.length;
    if (size > 2 * 1024 * 1024) {
      const error = new Error('LiDAR request too large'); error.status = 413; throw error;
    }
    parts.push(part);
  }
  return JSON.parse(Buffer.concat(parts, size).toString('utf8'));
}

export function createRequestHandler(tileService, { logger = console, wmtsServices = new Map(),
  cacheSettings = null, adminToken = null, lidarDistribution = null,
  lidarInternalToken = null } = {}) {
  return async (req, res) => {
    let pathname;
    try { pathname = new URL(req.url, 'http://localhost').pathname; }
    catch { send(res, 400, { 'Cache-Control': 'no-store' }, 'Invalid request\n'); return; }
    if (pathname.startsWith('/internal/lidar/')) {
      if (!authenticated(req, lidarInternalToken) || !lidarDistribution) {
        sendInternal(res, 403, { 'Cache-Control': 'no-store' }, 'Forbidden\n'); return;
      }
      if (req.method !== 'POST' || !['/internal/lidar/v1/plan',
        '/internal/lidar/v1/block'].includes(pathname)) {
        sendInternal(res, 404, { 'Cache-Control': 'no-store' }, 'Not Found\n'); return;
      }
      if (req.headers['content-type']?.split(';', 1)[0] !== 'application/json') {
        sendInternal(res, 415, { 'Cache-Control': 'no-store' }, 'JSON required\n'); return;
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(new Error('LiDAR request deadline')), 45_000);
      const disconnected = () => controller.abort(new Error('LiDAR consumer disconnected'));
      res.once('close', disconnected);
      try {
        const body = await lidarJsonBody(req);
        if (pathname.endsWith('/plan')) {
          const result = lidarDistribution.plan(body);
          const bytes = Buffer.from(JSON.stringify(result));
          if (bytes.length > 256 * 1024) {
            sendInternal(res, 413, { 'Cache-Control': 'no-store' }, 'LiDAR plan too large\n'); return;
          }
          sendInternal(res, 200, { 'Content-Type': 'application/json',
            'Cache-Control': 'no-store' }, bytes);
        } else {
          const result = await lidarDistribution.read(body, { signal: controller.signal });
          if (!result) {
            sendInternal(res, 404, { 'Cache-Control': 'no-store' }, 'Block outside plan\n'); return;
          }
          const { acquired } = result;
          if (acquired.status === 'NO_COVERAGE') {
            sendInternal(res, 204, { 'Cache-Control': 'no-store',
              'X-Lidar-Status': 'NO_COVERAGE' }); return;
          }
          sendInternal(res, 200, { 'Content-Type': 'image/geotiff',
            'Cache-Control': 'no-store', 'X-Lidar-Status': acquired.status,
            'X-Cache': acquired.cache === 'CREATED' ? 'MISS' : acquired.cache,
            'Content-Length': acquired.bytes.length }, acquired.bytes);
        }
      } catch (error) {
        if (res.headersSent || res.destroyed) return;
        const status = error.status || (controller.signal.aborted ? 504
          : error instanceof LidarWmsError ? (error.code === 'TIMEOUT' ? 504 : 502)
            : /queue full|budget|space/i.test(error.message) ? 503
              : /limit exceeded|too large/i.test(error.message) ? 413 : 400);
        logger.error(`LIDAR_DISTRIBUTION_ERROR ${error.constructor?.name || 'Error'} status=${status}`);
        sendInternal(res, status, { 'Cache-Control': 'no-store' }, 'LiDAR distribution unavailable\n');
      } finally {
        clearTimeout(timer);
        res.off('close', disconnected);
      }
      return;
    }
    if (pathname.startsWith('/internal/cache-admin')) {
      if (!authenticated(req, adminToken) || !cacheSettings) {
        sendInternal(res, 403, { 'Cache-Control': 'no-store' }, 'Forbidden\n'); return;
      }
      const actor = req.headers['x-admin-actor'];
      if (!/^[0-9a-f-]{36}$/i.test(actor || '')) {
        sendInternal(res, 400, { 'Cache-Control': 'no-store' }, 'Invalid actor\n'); return;
      }
      try {
        let result;
        if (pathname === '/internal/cache-admin/storage' && req.method === 'GET') result = await cacheSettings.storageSnapshot();
        else if (pathname === '/internal/cache-admin' && req.method === 'GET') result = await cacheSettings.snapshot();
        else { sendInternal(res, 404, { 'Cache-Control': 'no-store' }, 'Not Found\n'); return; }
        sendInternal(res, 200, { 'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-store' }, JSON.stringify(result));
      } catch (error) {
        const status = error.status || (/Unknown|Invalid|Empty|confirmation|large|JSON/.test(error.message) ? 400 : 503);
        logger.error(`CACHE_ADMIN_ERROR ${error.constructor?.name || 'Error'} status=${status}`);
        sendInternal(res, status, { 'Cache-Control': 'no-store' }, status === 409 ? 'Revision conflict\n' : 'Cache administration unavailable\n');
      }
      return;
    }
    if (req.method === 'OPTIONS') {
      send(res, 204, { 'Cache-Control': 'no-store' });
      return;
    }
    if (req.method !== 'GET') {
      send(res, 405, { Allow: 'GET, OPTIONS', 'Content-Type': 'text/plain; charset=utf-8' },
        'Method Not Allowed\n');
      return;
    }

    if (pathname === '/live') {
      send(res, 200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' }, 'ok\n');
      return;
    }

    if (pathname === '/health') {
      const stats = tileService.renderer?.stats?.();
      const healthy = tileService.isHealthy ?? !tileService.closing;
      send(res, healthy ? 200 : 503,
        { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store',
          'X-Process-RSS-MiB': String(Math.round(process.memoryUsage().rss / 1048576)),
          ...(stats && { 'X-Renderer-Last-Render-Ms': String(stats.lastRenderMs),
            'X-Renderer-Render-Count': String(stats.renderCount) }) },
        healthy ? 'ok\n' : 'unhealthy\n');
      return;
    }
    if (pathname === '/basemap/plan-ign.json') {
      if (tileService.isHealthy === false) {
        send(res, 503, { 'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'no-store' }, 'PLAN.IGN renderer unavailable\n');
        return;
      }
      send(res, 200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-cache',
      }, JSON.stringify({ ...TILEJSON,
        tiles: [`/basemap/tiles/${tileService.renderer.cacheVersion}/{z}/{x}/{y}.webp`] }));
      return;
    }

    const wmtsId = /^\/basemap\/([a-z][a-z0-9-]*)\.(?:json)$/.exec(pathname)?.[1] ||
      /^\/basemap\/([a-z][a-z0-9-]*)\/tiles\//.exec(pathname)?.[1];
    const wmts = wmtsServices.get(wmtsId);
    if (wmts && pathname === `/basemap/${wmtsId}.json`) {
      send(res, 200, { 'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-cache' }, JSON.stringify(wmts.tilejson()));
      return;
    }
    if (wmts && pathname.startsWith(`/basemap/${wmtsId}/tiles/`)) {
      const extension = wmts.definition.tile_format === 'jpeg' ? 'jpg' : 'png';
      const match = new RegExp(`^/basemap/${wmtsId}/tiles/([a-z0-9-]+)/([0-9]+)/([0-9]+)/([0-9]+)\\.${extension}$`)
        .exec(pathname);
      if (!match) {
        send(res, 400, { 'Cache-Control': 'no-store' }, 'Invalid XYZ tile\n');
        return;
      }
      const [, version, z, x, y] = match;
      if (version !== wmts.cacheVersion) {
        send(res, 410, { 'Cache-Control': 'no-store' }, 'Raster generation no longer served\n');
        return;
      }
      try {
        let cacheStatus = 'UNKNOWN';
        const bytes = await wmts.getTile({ z: Number(z), x: Number(x), y: Number(y) },
          (status) => { cacheStatus = status; });
        send(res, 200, { 'Content-Type': wmts.definition.tile_format === 'jpeg' ?
          'image/jpeg' : 'image/png',
        'Cache-Control': `public, max-age=${wmts.cache.policy.http_max_age_seconds}`,
        'X-Cache': cacheStatus }, bytes);
      } catch (error) {
        const status = error instanceof WmtsError ? error.status : 503;
        logger.error(`WMTS ${wmtsId} z${z}/${x}/${y} ${error.message}`);
        send(res, status, { 'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'no-store' }, status === 404 ? 'WMTS tile absent\n' :
          status === 400 ? 'Invalid XYZ tile\n' : 'WMTS tile temporarily unavailable\n');
      }
      return;
    }

    if (pathname.startsWith('/basemap/tiles/')) {
      const parsed = parseTilePath(pathname.slice('/basemap'.length));
      if (!parsed) {
        send(res, 400, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' },
          'Invalid XYZ tile\n');
        return;
      }
      if (!tileService.renderer) {
        send(res, 503, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' },
          'PLAN.IGN renderer unavailable\n');
        return;
      }
      if (parsed.version !== tileService.renderer.cacheVersion) {
        send(res, 410, { 'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'no-store' }, 'Raster generation no longer served\n');
        return;
      }
      const { z, x, y } = parsed;
      const tile = { z, x, y };
      try {
        let cacheStatus = 'UNKNOWN';
        const bytes = await tileService.getTile(tile, (status) => { cacheStatus = status; });
        send(res, 200, {
          'Content-Type': 'image/webp',
          'Cache-Control': `public, max-age=${tileService.httpMaxAgeSeconds ?? 3600}`,
          'X-Cache': cacheStatus,
        }, bytes);
      } catch (error) {
        logger.error(`ERROR z${tile.z}/${tile.x}/${tile.y} ${error.message}`);
        if (!res.headersSent) {
          send(res, 503, {
            'Content-Type': 'text/plain; charset=utf-8',
            'Cache-Control': 'no-store',
          }, 'PLAN.IGN tile temporarily unavailable\n');
        } else {
          res.destroy();
        }
      }
      return;
    }

    send(res, 404, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' },
      'Not Found\n');
  };
}

export function listen(server, { host = DEFAULT_HOST, port = DEFAULT_PORT } = {}) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.off('error', reject);
      resolve(server.address());
    });
  });
}

export function closeServer(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
}

export const SHUTDOWN_DEADLINE_MS = 80_000;

function parsePort(value) {
  const port = Number(value ?? DEFAULT_PORT);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid PORT: ${value}`);
  }
  return port;
}

async function main() {
  const host = process.env.HOST || DEFAULT_HOST;
  const port = parsePort(process.env.PORT);
  const cacheRoot = path.resolve(process.env.CACHE_ROOT ||
    path.join(here, '.cache'));
  const generation = validateGeneration(process.env.CACHE_GENERATION || DEFAULT_CACHE_GENERATION);
  const catalogueFile = process.env.BASEMAP_CATALOG_FILE ||
    path.resolve(here, '../../webapp/backend/digues_webapp/basemaps.toml');
  const productsFile = process.env.CARTOGRAPHIC_CACHE_PRODUCTS_FILE ||
    path.resolve(here, '../../webapp/backend/digues_webapp/cartographic_caches.json');
  const basemaps = await loadCachePolicies(catalogueFile);
  const policies = await loadCacheProducts(productsFile);
  assertBasemapProductCompatibility(basemaps.basemaps, policies, basemaps.minFreeBytes);
  const caches = createCacheRegistry(cacheRoot, policies,
    { activeGenerations: { 'ign-epure': { resources: generation } } });
  const cacheSettings = new CacheSettings({ cacheRoot, catalogue: policies, registry: caches });
  await cacheSettings.load();
  const wmtsServices = createWmtsServices(basemaps, caches);
  const lidarAcquisition = createLidarWmsAcquisition(createLidarBlockCacheFactory(caches, policies));
  const lidarDistribution = createLidarDistribution(lidarAcquisition,
    policies.products.get(LIDAR_PRODUCT_ID));
  const epure = caches.get('ign-epure');
  if (!epure?.resources) throw new Error('Missing operational ign-epure cache policy');
  console.log(`CACHE_STATS ${JSON.stringify(await cacheStats(caches))}`);
  const handlerOptions = { wmtsServices, cacheSettings, adminToken: process.env.BASEMAP_ADMIN_TOKEN,
    lidarDistribution, lidarInternalToken: process.env.LIDAR_INTERNAL_TOKEN };
  let handleRequest = createRequestHandler({ isHealthy: false }, handlerOptions);
  const server = http.createServer((request, response) => handleRequest(request, response));
  try {
    await listen(server, { host, port });
  } catch (error) {
    await Promise.all([...wmtsServices.values()].map((service) => service.close()));
    throw error;
  }
  console.log(`Cartographic service listening on http://${host}:${port}`);

  let tileService;
  let initialization;
  let retryTimer;
  let shuttingDown = false;
  const initializeEpure = async () => {
    try {
      const { createNativeRenderer } = await import('./native-renderer.mjs');
      const renderer = await createNativeRenderer({ cacheRoot, generation,
        resourceStore: epure.resources, retainOnError: epure.policy.retain_on_error });
      epure.tiles.activeGeneration = renderer.cacheVersion;
      const rasterCache = new RasterCache(cacheRoot, renderer.cacheVersion, { store: epure.tiles });
      tileService = new PlanIgnTileService({ cacheRoot, renderer, cache: rasterCache,
        httpMaxAgeSeconds: epure.policy.http_max_age_seconds });
      if (!shuttingDown) handleRequest = createRequestHandler(tileService, handlerOptions);
      console.log(`Raster cache: ${path.join(cacheRoot, 'ign', 'epure', renderer.cacheVersion)}`);
    } catch (error) {
      console.error(`ERROR IGN Épuré unavailable: ${error.message}`);
      if (!shuttingDown) retryTimer = setTimeout(startEpure, 30_000);
    }
  };
  function startEpure() {
    initialization = initializeEpure();
  }
  startEpure();
  const statsTimer = setInterval(() => {
    void cacheStats(caches).then((stats) => console.log(`CACHE_STATS ${JSON.stringify(stats)}`))
      .catch((error) => console.error(`ERROR cache statistics ${error.message}`));
  }, 60_000);
  statsTimer.unref();

  const shutdown = async (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    clearInterval(statsTimer);
    clearTimeout(retryTimer);
    console.log(`Shutting down after ${signal}`);
    if (tileService) tileService.closing = true;
    const deadline = setTimeout(() => {
      console.error(`ERROR shutdown exceeded ${SHUTDOWN_DEADLINE_MS} ms`);
      process.exit(1);
    }, SHUTDOWN_DEADLINE_MS);
    try {
      await closeServer(server);
    } catch (error) {
      console.error(`ERROR shutdown ${error.message}`);
      process.exitCode = 1;
    } finally {
      await initialization;
      await tileService?.close().catch((error) => {
        console.error(`ERROR release ${error.message}`);
        // The owner process is the isolation boundary for a stuck Native Map.
        // Never replace/release it concurrently with a still-running C++ job.
        process.exit(1);
      });
      await Promise.all([...wmtsServices.values()].map((service) => service.close()));
      clearTimeout(deadline);
    }
  };
  process.once('SIGINT', () => { void shutdown('SIGINT'); });
  process.once('SIGTERM', () => { void shutdown('SIGTERM'); });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(`ERROR startup ${error.stack || error.message}`);
    process.exitCode = 1;
  });
}
