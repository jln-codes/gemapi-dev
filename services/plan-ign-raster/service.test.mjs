import assert from 'node:assert/strict';
import http from 'node:http';
import { writeFile, mkdtemp, rm, utimes, access, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import sharp from 'sharp';
import { cropForTile } from './geometry.mjs';
import { RasterCache } from './raster-cache.mjs';
import { FileCache } from './file-cache.mjs';
import { closeServer, createRequestHandler, listen } from './server.mjs';
import { MAX_METATILE_JOBS, PlanIgnTileService, parseTilePath } from './tile-service.mjs';
import { createRenderQueue } from './render-queue.mjs';

const silentLogger = { log() {}, error() {} };

function fakeEncoder(events = []) {
  return async (_rgba, meta, tile) => {
    const key = `${tile.z}/${tile.x}/${tile.y}`;
    events.push(`encode:${key}`);
    const crop = cropForTile(meta, tile);
    return { ...tile, buffer: Buffer.from(`${key}:${crop.left},${crop.top}`) };
  };
}

function fakeRenderer(render = async (meta) => Buffer.from(`meta:${meta.originX}/${meta.originY}`)) {
  const waiters = [];
  return { cacheVersion: 'test', calls: 0, released: 0, async render(meta) {
    this.calls++;
    for (let i = waiters.length - 1; i >= 0; i--) {
      if (this.calls >= waiters[i].count) waiters.splice(i, 1)[0].resolve();
    }
    return render(meta);
  }, whenRendered(count) {
    return this.calls >= count ? Promise.resolve() : new Promise(resolve => waiters.push({ count, resolve }));
  }, async release() { this.released++; } };
}

function fakeCache(cacheRoot) {
  return new RasterCache(cacheRoot, 'test', { validateRaster: async () => true });
}

async function temporaryCache() {
  return mkdtemp(path.join(tmpdir(), 'plan-ign-service-test-'));
}

async function request(handler, url, method = 'GET') {
  const response = { status: null, headers: null, chunks: [], headersSent: false,
    writeHead(status, headers) {
      this.status = status;
      this.headers = Object.fromEntries(Object.entries(headers)
        .map(([key, value]) => [key.toLowerCase(), value]));
      this.headersSent = true;
    },
    end(body = '') { this.chunks.push(Buffer.from(body)); },
    destroy() { this.destroyed = true; },
  };
  await handler({ method, url }, response);
  return { status: response.status, headers: response.headers,
    body: Buffer.concat(response.chunks), destroyed: response.destroyed };
}

async function waitUntil(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  throw new Error('Condition was not reached');
}

test('TileJSON exposes the generic raster XYZ contract expected by basemap.js', async () => {
  const tileService = { renderer: { cacheVersion: 'test' },
    getTile() { throw new Error('must not render'); } };
  const response = await request(createRequestHandler(tileService), '/basemap/plan-ign.json');
  assert.equal(response.status, 200);
  assert.match(response.headers['content-type'], /^application\/json/);
  assert.equal(response.headers['cache-control'], 'no-cache');
  assert.equal(response.headers['access-control-allow-origin'], '*');
  assert.deepEqual(JSON.parse(response.body), {
    tilejson: '3.0.0',
    tiles: ['/basemap/tiles/test/{z}/{x}/{y}.webp'],
    minzoom: 0,
    maxzoom: 18,
    scheme: 'xyz',
    attribution: '© IGN — PLAN.IGN',
  });
});

test('XYZ paths are strictly validated and invalid requests return HTTP 400', async () => {
  assert.deepEqual(parseTilePath('/tiles/test/18/262143/262143.webp'),
    { version: 'test', z: 18, x: 262143, y: 262143 });
  for (const pathname of [
    '/tiles/test/no/0/0.webp',
    '/tiles/test/-1/0/0.webp',
    '/tiles/test/19/0/0.webp',
    '/tiles/test/2/4/0.webp',
    '/tiles/test/2/0/4.webp',
    '/tiles/test/2/1.5/1.webp',
    '/tiles/test/2/1/1.png',
  ]) assert.equal(parseTilePath(pathname), null, pathname);

  const tileService = { renderer: { cacheVersion: 'test' },
    getTile() { throw new Error('must not render'); } };
  const handler = createRequestHandler(tileService);
  for (const pathname of [
    '/tiles/test/no/0/0.webp', '/tiles/test/19/0/0.webp',
    '/tiles/test/2/4/0.webp', '/tiles/test/2/0/4.webp',
  ]) {
    const response = await request(handler, `/basemap${pathname}`);
    assert.equal(response.status, 400, pathname);
  }
});

test('a MISS is generated, then becomes a disk HIT including after restart', async () => {
  const cacheRoot = await temporaryCache();
  const tile = { z: 17, x: 66309, y: 45633 };
  try {
    const firstRenderer = fakeRenderer();
    const firstLogs = [];
    const first = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer: firstRenderer,
      encode: fakeEncoder(), logger: { log: (line) => firstLogs.push(line), error() {} } });
    const generated = await first.getTile(tile);
    await first.close();
    assert.equal(firstRenderer.calls, 1);
    assert.match(firstLogs[0], /^MISS /);

    const secondRenderer = fakeRenderer();
    const secondLogs = [];
    const restarted = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer: secondRenderer,
      encode: fakeEncoder(), logger: { log: (line) => secondLogs.push(line), error() {} } });
    assert.deepEqual(await restarted.getTile(tile), generated);
    assert.equal(secondRenderer.calls, 0);
    assert.match(secondLogs[0], /^HIT  /);
    await restarted.close();
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('simultaneous requests for one XYZ join one metatile render', async () => {
  const cacheRoot = await temporaryCache();
  let continueRender;
  const gate = new Promise((resolve) => { continueRender = resolve; });
  const renderer = fakeRenderer(async () => gate.then(() => Buffer.from('rgba')));
  const logs = [];
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer, encode: fakeEncoder(),
    logger: { log: (line) => logs.push(line), error() {} } });
  const tile = { z: 17, x: 66309, y: 45633 };
  try {
    const first = service.getTile(tile);
    const second = service.getTile(tile);
    await renderer.whenRendered(1);
    assert(service.inflight.values().next().value instanceof Promise);
    continueRender();
    assert.deepEqual(await first, await second);
    assert.equal(renderer.calls, 1);
    assert.equal(logs.filter((line) => line.startsWith('WAIT ')).length, 1);
    await service.close();
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('different XYZ siblings share one render and receive their matching crops', async () => {
  const cacheRoot = await temporaryCache();
  let continueRender;
  const gate = new Promise((resolve) => { continueRender = resolve; });
  const renderer = fakeRenderer(async () => gate.then(() => Buffer.from('rgba')));
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer, encode: fakeEncoder(),
    logger: silentLogger });
  const firstTile = { z: 17, x: 66309, y: 45633 };
  const secondTile = { z: 17, x: 66311, y: 45634 };
  try {
    const first = service.getTile(firstTile);
    const second = service.getTile(secondTile);
    await renderer.whenRendered(1);
    continueRender();
    assert.equal((await first).toString(), '17/66309/45633:768,768');
    assert.equal((await second).toString(), '17/66311/45634:1792,1280');
    assert.equal(renderer.calls, 1);
    await service.close();
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('managed cache bounds an Épuré metatile without losing its requested tile', async () => {
  const cacheRoot = await temporaryCache();
  const tile = { z: 17, x: 66309, y: 45633 };
  const store = new FileCache({ root: path.join(cacheRoot, 'ign', 'epure'),
    quotaBytes: 100, minFreeBytes: 0, activeGeneration: 'test',
    freeBytes: async () => 10000 });
  const cache = new RasterCache(cacheRoot, 'test', { validateRaster: async () => true, store });
  const renderer = fakeRenderer();
  const errors = [];
  const service = new PlanIgnTileService({ cacheRoot, cache, renderer,
    encode: fakeEncoder(), logger: { log() {}, error: (line) => errors.push(line) } });
  try {
    const bytes = await service.getTile(tile);
    await service.close();
    assert.equal(renderer.calls, 1);
    assert.deepEqual(await cache.read(tile), bytes);
    assert((await store.snapshot()).sizeBytes <= 100);
    assert(store.metrics.noSpace > 0);
    assert(errors.some((line) => line.includes('Cache quota')));
  } finally { await rm(cacheRoot, { recursive: true, force: true }); }
});

test('identical concurrent requests deduplicate with the managed disk cache', async () => {
  const cacheRoot = await temporaryCache();
  let continueRender;
  const gate = new Promise((resolve) => { continueRender = resolve; });
  const renderer = fakeRenderer(async () => gate.then(() => Buffer.from('rgba')));
  const store = new FileCache({ root: path.join(cacheRoot, 'ign', 'epure'),
    quotaBytes: 10000, minFreeBytes: 0, activeGeneration: 'test',
    freeBytes: async () => 100000 });
  const cache = new RasterCache(cacheRoot, 'test', { validateRaster: async () => true, store });
  const service = new PlanIgnTileService({ cacheRoot, cache, renderer,
    encode: fakeEncoder(), logger: silentLogger });
  const tile = { z: 17, x: 66309, y: 45633 };
  try {
    const first = service.getTile(tile);
    const second = service.getTile(tile);
    await renderer.whenRendered(1);
    continueRender();
    assert.deepEqual(await first, await second);
    await service.close();
    assert.equal(renderer.calls, 1);
    assert.equal((await store.snapshot()).fileCount, 16);
  } finally { await rm(cacheRoot, { recursive: true, force: true }); }
});

test('the requested tile finishes before the remaining 15 are submitted at concurrency 2', async () => {
  const cacheRoot = await temporaryCache();
  const events = [];
  const renderer = fakeRenderer();
  let requestedFinished = false;
  let activeSecondary = 0;
  let maximumSecondary = 0;
  const encode = async (_rgba, meta, tile) => {
    const key = `${tile.z}/${tile.x}/${tile.y}`;
    if (key === '17/66309/45633') {
      events.push('requested:start');
      requestedFinished = true;
      events.push('requested:done');
    } else {
      assert.equal(requestedFinished, true, `secondary ${key} submitted too early`);
      events.push(`secondary:${key}`);
      activeSecondary++;
      maximumSecondary = Math.max(maximumSecondary, activeSecondary);
      await new Promise((resolve) => setImmediate(resolve));
      activeSecondary--;
    }
    const crop = cropForTile(meta, tile);
    return { ...tile, buffer: Buffer.from(`${key}:${crop.left},${crop.top}`) };
  };
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer, encode, logger: silentLogger });
  try {
    await service.getTile({ z: 17, x: 66309, y: 45633 });
    await service.close();
    assert.deepEqual(events.slice(0, 2), ['requested:start', 'requested:done']);
    assert.equal(events.filter((event) => event.startsWith('secondary:')).length, 15);
    assert.equal(maximumSecondary, 2);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('the requested response resolves while secondary encodings remain pending', async () => {
  const cacheRoot = await temporaryCache();
  let finishSecondary;
  const secondaryGate = new Promise((resolve) => { finishSecondary = resolve; });
  let secondaryStarted = 0;
  const requested = '17/66309/45633';
  const encode = async (rgba, meta, tile) => {
    if (`${tile.z}/${tile.x}/${tile.y}` !== requested) {
      secondaryStarted++;
      await secondaryGate;
    }
    return fakeEncoder()(rgba, meta, tile);
  };
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot),
    renderer: fakeRenderer(), encode, logger: silentLogger });
  try {
    const bytes = await service.getTile({ z: 17, x: 66309, y: 45633 });
    assert.match(bytes.toString(), /^17\/66309\/45633:/);
    await waitUntil(() => secondaryStarted === 2);
    assert.equal(service.inflight.size, 1);
    finishSecondary();
    await service.close();
    assert.equal(secondaryStarted, 15);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('a secondary encoding error is logged without changing the successful requested tile', async () => {
  const cacheRoot = await temporaryCache();
  const errors = [];
  const requested = { z: 17, x: 66309, y: 45633 };
  const encode = async (rgba, meta, tile) => {
    if (tile.x === 66308 && tile.y === 45632) throw new Error('secondary Sharp failure');
    return fakeEncoder()(rgba, meta, tile);
  };
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer: fakeRenderer(), encode,
    logger: { log() {}, error: (line) => errors.push(line) } });
  try {
    assert.match((await service.getTile(requested)).toString(), /^17\/66309\/45633:/);
    await service.close();
    assert(errors.some((line) => /secondary Sharp failure/.test(line)));
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('an incomplete temporary file is never considered a cache HIT', async () => {
  const cacheRoot = await temporaryCache();
  const cache = new RasterCache(cacheRoot, 'test');
  const tile = { z: 17, x: 66309, y: 45633 };
  try {
    const temporary = `${cache.filename(tile)}.999.incomplete.tmp`;
    await writeFile(temporary, 'partial').catch(async (error) => {
      if (error.code !== 'ENOENT') throw error;
      const { mkdir } = await import('node:fs/promises');
      await mkdir(path.dirname(temporary), { recursive: true });
      await writeFile(temporary, 'partial');
    });
    assert.equal(await cache.read(tile), null);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('raster cache validates WebP dimensions and rejects corrupt final files', async () => {
  const cacheRoot = await temporaryCache();
  const tile = { z: 17, x: 66309, y: 45633 };
  const cache = new RasterCache(cacheRoot, 'test');
  try {
    const valid = await sharp({ create: { width: 512, height: 512, channels: 4,
      background: '#ddeeff' } }).webp({ quality: 85 }).toBuffer();
    await cache.write(tile, valid);
    assert.deepEqual(await cache.read(tile), valid);
    assert.deepEqual(await readdir(path.dirname(cache.filename(tile))), ['45633.webp']);
    await writeFile(cache.filename(tile), valid.subarray(0, 100));
    assert.equal(await cache.read(tile), null);
    await writeFile(cache.filename(tile), 'not a WebP');
    assert.equal(await cache.read(tile), null);
    await assert.rejects(cache.write(tile, Buffer.from('bad')), /invalid raster/);
    assert.equal(await new RasterCache(cacheRoot, 'next').read(tile), null);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('a failed render frees its metatile slot and the next request retries', async () => {
  const cacheRoot = await temporaryCache();
  let attempts = 0;
  const renderer = fakeRenderer(async () => {
    if (++attempts === 1) throw new Error('MapLibre failed');
    return Buffer.from('rgba');
  });
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot),
    renderer, encode: fakeEncoder(), logger: silentLogger });
  const tile = { z: 17, x: 66309, y: 45633 };
  try {
    await assert.rejects(service.getTile(tile), /MapLibre failed/);
    await waitUntil(() => service.inflight.size === 0);
    assert.match((await service.getTile(tile)).toString(), /^17\/66309\/45633:/);
    assert.equal(renderer.calls, 2);
    await service.close();
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('HTTP integration follows TileJSON to a raster and reports MISS then HIT', async () => {
  const cacheRoot = await temporaryCache();
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot),
    renderer: fakeRenderer(), encode: fakeEncoder(), logger: silentLogger });
  const server = http.createServer(createRequestHandler(service, { logger: silentLogger }));
  try {
    const address = await listen(server, { port: 0 });
    const base = `http://127.0.0.1:${address.port}`;
    const tilejson = await fetch(`${base}/basemap/plan-ign.json`).then((response) => response.json());
    const url = new URL(tilejson.tiles[0].replace('{z}', '17')
      .replace('{x}', '66309').replace('{y}', '45633'), base);
    const first = await fetch(url);
    assert.equal(first.status, 200);
    assert.equal(first.headers.get('content-type'), 'image/webp');
    assert.equal(first.headers.get('cache-control'), 'public, max-age=3600');
    assert.equal(first.headers.get('access-control-allow-origin'), '*');
    assert.equal(first.headers.get('x-cache'), 'MISS');
    const bytes = Buffer.from(await first.arrayBuffer());
    const second = await fetch(url);
    assert.equal(second.headers.get('x-cache'), 'HIT');
    assert.deepEqual(Buffer.from(await second.arrayBuffer()), bytes);
  } finally {
    if (server.listening) await closeServer(server);
    await service.close();
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('TileJSON changes raster URLs with the cache version and never serves an old URL as new', async () => {
  const calls = [];
  const makeService = (cacheVersion) => ({
    renderer: { cacheVersion },
    async getTile() { calls.push(cacheVersion); return Buffer.from(cacheVersion); },
  });
  const a = createRequestHandler(makeService('generation-a'));
  const b = createRequestHandler(makeService('generation-b'));
  const urlA = JSON.parse((await request(a, '/basemap/plan-ign.json')).body).tiles[0];
  const urlB = JSON.parse((await request(b, '/basemap/plan-ign.json')).body).tiles[0];
  assert.notEqual(urlA, urlB);
  const xyzA = urlA.replace('{z}', '0').replace('{x}', '0').replace('{y}', '0');
  const xyzB = urlB.replace('{z}', '0').replace('{x}', '0').replace('{y}', '0');
  assert.equal((await request(a, xyzA)).body.toString(), 'generation-a');
  assert.equal((await request(b, xyzB)).body.toString(), 'generation-b');
  const old = await request(b, xyzA);
  assert.equal(old.status, 410);
  assert.equal(old.headers['cache-control'], 'no-store');
  assert.deepEqual(calls, ['generation-a', 'generation-b']);
});

test('Native timeout rejects queued metatiles and refuses unsafe Map release pending process restart', async () => {
  const cacheRoot = await temporaryCache();
  const callbacks = [];
  let releases = 0;
  const map = {
    render(_viewport, callback) { callbacks.push(callback); },
    release() { releases++; },
  };
  const renderer = { cacheVersion: 'test',
    ...createRenderQueue(map, { timeoutMs: 20, logger: silentLogger }) };
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot),
    renderer, encode: fakeEncoder(), logger: silentLogger });
  const handler = createRequestHandler(service, { logger: silentLogger });
  try {
    const a = request(handler, '/basemap/tiles/test/4/0/0.webp');
    const b = request(handler, '/basemap/tiles/test/4/4/0.webp');
    assert.deepEqual((await Promise.all([a, b])).map((response) => response.status), [503, 503]);
    assert.equal(callbacks.length, 1);
    assert.equal((await request(handler, '/health')).status, 503);
    assert.equal((await request(handler, '/basemap/plan-ign.json')).status, 503);
    assert.equal((await request(handler, '/basemap/tiles/test/4/8/0.webp')).status, 503);
    await waitUntil(() => service.inflight.size === 0);
    await assert.rejects(Promise.race([
      service.close(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('close hung')), 250)),
    ]), /refusing unsafe Map release/);
    assert.equal(releases, 0);
    callbacks[0](null, Buffer.from('late'));
    callbacks[0](new Error('duplicate callback'));
    assert.equal(callbacks.length, 1);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('a rendering failure returns 503 and does not crash the HTTP server', async () => {
  const cacheRoot = await temporaryCache();
  const renderer = fakeRenderer(async () => { throw new Error('IGN unavailable'); });
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer, encode: fakeEncoder(),
    logger: silentLogger });
  const handler = createRequestHandler(service, { logger: silentLogger });
  try {
    const failed = await request(handler, '/basemap/tiles/test/17/66309/45633.webp');
    assert.equal(failed.status, 503);
    const tilejson = await request(handler, '/basemap/plan-ign.json');
    assert.equal(tilejson.status, 200);
  } finally {
    await service.close();
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('health, stable same-origin path and malformed requests are explicit', async () => {
  const handler = createRequestHandler({ closing: false, renderer: { cacheVersion: 'test' },
    getTile() { throw new Error('no render'); } });
  assert.equal((await request(handler, '/health')).status, 200);
  assert.equal((await request(handler, '/plan-ign.json')).status, 404);
  assert.equal((await request(handler, '/basemap/tiles/test/2/%2e%2e/1.webp')).status, 400);
  assert.equal((await request(handler, '/basemap/tiles/test/19/0/0.webp')).status, 400);
});

test('a requested tile cache write failure returns 503 and remains regenerable', async () => {
  const cacheRoot = await temporaryCache();
  const renderer = fakeRenderer();
  const cache = new RasterCache(cacheRoot, 'test');
  const service = new PlanIgnTileService({ cacheRoot, renderer, cache: {
    read: (tile) => cache.read(tile),
    write: async () => { throw new Error('disk full'); },
  }, encode: fakeEncoder(), logger: silentLogger });
  try {
    const handler = createRequestHandler(service, { logger: silentLogger });
    assert.equal((await request(handler, '/basemap/tiles/test/17/66309/45633.webp')).status, 503);
    assert.equal(await cache.read({ z: 17, x: 66309, y: 45633 }), null);
    await service.close();
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('distinct cold metatiles have a bounded admission queue', async () => {
  const cacheRoot = await temporaryCache();
  let continueRender;
  const gate = new Promise((resolve) => { continueRender = resolve; });
  const renderer = fakeRenderer(async () => gate.then(() => Buffer.from('rgba')));
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer, encode: fakeEncoder(),
    logger: silentLogger });
  try {
    const waiting = Array.from({ length: MAX_METATILE_JOBS }, (_, index) =>
      service.getTile({ z: 17, x: 4 * index, y: 0 }));
    await renderer.whenRendered(MAX_METATILE_JOBS);
    await assert.rejects(service.getTile({ z: 17, x: 4 * MAX_METATILE_JOBS, y: 0 }),
      /queue is full/);
    continueRender();
    await Promise.all(waiting);
    await service.close();
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('secondary encoding uses one global two-slot pool across metatiles', async () => {
  const cacheRoot = await temporaryCache();
  const requested = new Set(['17/66309/45633', '17/66313/45633']);
  let active = 0;
  let maximum = 0;
  const encode = async (_rgba, meta, tile) => {
    const key = `${tile.z}/${tile.x}/${tile.y}`;
    if (!requested.has(key)) {
      active++;
      maximum = Math.max(maximum, active);
      await new Promise((resolve) => setImmediate(resolve));
      active--;
    }
    const crop = cropForTile(meta, tile);
    return { buffer: Buffer.from(`${key}:${crop.left},${crop.top}`) };
  };
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer: fakeRenderer(), encode,
    logger: silentLogger });
  try {
    await Promise.all([
      service.getTile({ z: 17, x: 66309, y: 45633 }),
      service.getTile({ z: 17, x: 66313, y: 45633 }),
    ]);
    await service.close();
    assert.equal(maximum, 2);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('shutdown waits for secondary writes before releasing Native', async () => {
  const cacheRoot = await temporaryCache();
  let finishSecondary;
  const gate = new Promise((resolve) => { finishSecondary = resolve; });
  const requested = '17/66309/45633';
  const renderer = fakeRenderer();
  const encode = async (rgba, meta, tile) => {
    if (`${tile.z}/${tile.x}/${tile.y}` !== requested) await gate;
    return fakeEncoder()(rgba, meta, tile);
  };
  const service = new PlanIgnTileService({ cacheRoot, cache: fakeCache(cacheRoot), renderer, encode,
    logger: silentLogger });
  try {
    await service.getTile({ z: 17, x: 66309, y: 45633 });
    const closing = service.close();
    await waitUntil(() => service.secondaryActive === 2);
    assert.equal(renderer.released, 0);
    await assert.rejects(service.getTile({ z: 17, x: 66309, y: 45633 }), /queue is full/);
    finishSecondary();
    await closing;
    assert.equal(renderer.released, 1);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test('old temporary files are removed on the next write for that XYZ', async () => {
  const cacheRoot = await temporaryCache();
  const cache = fakeCache(cacheRoot);
  const tile = { z: 17, x: 66309, y: 45633 };
  const temporary = `${cache.filename(tile)}.orphan.tmp`;
  try {
    await writeFile(temporary, 'partial').catch(async (error) => {
      if (error.code !== 'ENOENT') throw error;
      const { mkdir } = await import('node:fs/promises');
      await mkdir(path.dirname(temporary), { recursive: true });
      await writeFile(temporary, 'partial');
    });
    const old = new Date(Date.now() - 2 * 60 * 60 * 1000);
    await utimes(temporary, old, old);
    await cache.write(tile, Buffer.from('complete'));
    await assert.rejects(access(temporary), { code: 'ENOENT' });
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});
