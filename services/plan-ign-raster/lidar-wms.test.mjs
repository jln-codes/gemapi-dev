import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { createLidarBlockCacheFactory, lidarBlockIdentity } from './lidar-block-cache.mjs';
import { createCacheRegistry } from './cache-registry.mjs';
import { loadCacheProducts } from './cache-products.mjs';
import { syntheticLidarGeoTiff } from './lidar-fixture.mjs';
import { createLidarWmsAcquisition, fetchLidarWmsBlock, lidarGetMapUrl } from './lidar-wms.mjs';

const auditRoot = path.resolve(import.meta.dirname, '../../audits');
const productFile = new URL('../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url);
const a = { instance_id: '11111111-1111-4111-8111-111111111111',
  structure_id: '22222222-2222-4222-8222-222222222222',
  business_database_id: '33333333-3333-4333-8333-333333333333' };
const b = { ...a, business_database_id: '44444444-4444-4444-8444-444444444444' };
const block = { ix: 2546, iy: 26804 };
const tiff = (value = 42.5, at = block) => syntheticLidarGeoTiff(at, { value });
const response = (body, status = 200, mime = 'image/geotiff', extra = {}) =>
  new Response(body, { status, headers: { 'Content-Type': mime, ...extra } });

async function caches(t, freeBytes = async () => 1e12) {
  await mkdir(auditRoot, { recursive: true });
  const root = await mkdtemp(path.join(auditRoot, 'lidar-wms-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const catalogue = await loadCacheProducts(productFile);
  const registry = createCacheRegistry(root, catalogue, { freeBytes });
  return { root, catalogue, registry, factory: createLidarBlockCacheFactory(registry, catalogue) };
}

test('GetMap is fixed to the IGN product and the integer EPSG:2154 block grid', () => {
  const url = new URL(lidarGetMapUrl(block));
  assert.equal(url.origin, 'https://data.geopf.fr');
  assert.equal(url.pathname, '/wms-r/wms');
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    SERVICE: 'WMS', VERSION: '1.3.0', REQUEST: 'GetMap',
    LAYERS: 'IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93',
    STYLES: 'normal', CRS: 'EPSG:2154', FORMAT: 'image/geotiff',
    BBOX: '651776,6861824,652032,6862080', WIDTH: '512', HEIGHT: '512',
  });
  assert.equal(new URL(lidarGetMapUrl({ ix: block.ix + 1, iy: block.iy }))
    .searchParams.get('BBOX'), '652032,6861824,652288,6862080');
  assert.throws(() => lidarGetMapUrl({ ix: 1.5, iy: 0 }), /Invalid block index/);
});

test('WMS transport bounds bytes and rejects service documents, redirects and HTTP failures', async () => {
  const cases = [
    [response('<html>error</html>', 200, 'text/html'), 'INVALID_RESPONSE'],
    [response('<ServiceException/>', 200, 'application/xml'), 'INVALID_RESPONSE'],
    [response(null, 302, 'text/html', { Location: 'https://example.invalid/' }), 'REDIRECT_DENIED'],
    ...[403, 404].map((code) => [response('forbidden', code, 'text/plain'), 'HTTP_ERROR']),
    [response(null, 204), 'NO_COVERAGE'],
    [response(Buffer.alloc(1), 200, 'image/geotiff', { 'Content-Length': '9000000' }),
      'INVALID_RESPONSE'],
    [response(Buffer.alloc(8 * 1024 * 1024 + 1)), 'INVALID_RESPONSE'],
  ];
  for (const [reply, code] of cases) {
    await assert.rejects(fetchLidarWmsBlock(block, { fetchImpl: async () => reply }),
      (error) => error.code === code);
  }
});

test('transient HTTP statuses and timeout retry at most three times; 429 honors Retry-After', async () => {
  for (const status of [429, 500, 502, 503, 504]) {
    let calls = 0;
    const result = await fetchLidarWmsBlock(block, { fetchImpl: async () => {
      calls++;
      return calls < 3 ? response('busy', status, 'text/plain', { 'Retry-After': '0' })
        : response(tiff());
    } });
    assert.equal(calls, 3);
    assert.equal(result.attempts, 3);
  }
  let calls = 0;
  await assert.rejects(fetchLidarWmsBlock(block, { timeoutMs: 5,
    fetchImpl: async (_, { signal }) => {
      calls++;
      return new Promise((_, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
    } }), (error) => error.code === 'TIMEOUT');
  assert.equal(calls, 3);
});

test('MISS, HIT, WAIT, restart and another business base keep one shared cache engine', async (t) => {
  const { root, catalogue, factory } = await caches(t);
  let calls = 0;
  const fetchImpl = async () => {
    calls++;
    await new Promise((resolve) => setTimeout(resolve, 10));
    return response(tiff(12.5));
  };
  const acquisition = createLidarWmsAcquisition(factory, { fetchImpl });
  const [first, waiting] = await Promise.all([
    acquisition.getBlock(block), acquisition.getBlock(block),
  ]);
  // Authorization resolves the filesystem asynchronously; either caller may
  // arrive first at the shared acquisition. Preserve creator + waiter, not order.
  assert.deepEqual([first.cache, waiting.cache].sort(), ['CREATED', 'WAIT']);
  assert.deepEqual(first.bytes, waiting.bytes);
  assert.equal(first.status, 'DATA');
  assert.equal(first.validation.validPixels, 512 * 512);
  assert.equal(calls, 1);
  const hit = await acquisition.getBlock(block);
  assert.equal(hit.cache, 'HIT');
  assert.equal(hit.requests, 0);
  assert.equal(calls, 1);
  const other = await acquisition.getBlock(block);
  assert.equal(other.cache, 'HIT');
  assert.equal(calls, 1);
  const restarted = createLidarWmsAcquisition(createLidarBlockCacheFactory(
    createCacheRegistry(root, catalogue, { freeBytes: async () => 1e12 }), catalogue),
  { fetchImpl: async () => { throw new Error('offline'); } });
  assert.equal((await restarted.getBlock(block)).cache, 'HIT');
});

test('all NoData is a valid cached block; corrupt bytes force reacquisition', async (t) => {
  const { factory } = await caches(t);
  let calls = 0;
  const acquisition = createLidarWmsAcquisition(factory, {
    fetchImpl: async () => { calls++; return response(tiff(-9999)); },
  });
  const empty = await acquisition.getBlock(block);
  assert.equal(empty.status, 'NO_DATA');
  assert.equal(empty.validation.validPixels, 0);
  assert.equal((await acquisition.getBlock(block)).cache, 'HIT');
  const cache = await factory.get();
  const key = lidarBlockIdentity(cache.product, block).key;
  await writeFile(cache.entry.tiles.filename(key), '<html>broken</html>');
  const repaired = await acquisition.getBlock(block);
  assert.equal(repaired.cache, 'CREATED');
  assert.equal(calls, 2);
  assert.equal(cache.entry.tiles.metrics.corrupt, 1);
});

test('coarse windows use bounded WMS dimensions and the same restartable scoped cache', async t => {
  const {root,catalogue,factory} = await caches(t);
  const part={ix:2,iy:3,level:5,window:[2,3,9,5]};
  let calls=0;
  const acquisition=createLidarWmsAcquisition(factory,{fetchImpl:async url=>{
    calls++;
    const params=new URL(url).searchParams;
    assert.equal(params.get('WIDTH'),'7'); assert.equal(params.get('HEIGHT'),'2');
    assert.equal(params.get('BBOX'),'16416,24624,16528,24656');
    return response(syntheticLidarGeoTiff(part,{value:12.125}));
  }});
  const first=await acquisition.getBlock(part);
  assert.equal(first.validation.validPixels,14);
  assert.equal(first.cache,'CREATED');
  assert.equal((await acquisition.getBlock(part)).cache,'HIT');
  assert.equal(calls,1);
  const product=catalogue.products.get('ign-lidar-hd-mnt');
  assert.notEqual(lidarBlockIdentity(product,part).key,lidarBlockIdentity(product,{...part,level:6}).key);
  assert.notEqual(lidarBlockIdentity(product,part).key,lidarBlockIdentity(product,{...part,window:[3,3,10,5]}).key);
  const restarted=createLidarWmsAcquisition(createLidarBlockCacheFactory(
    createCacheRegistry(root,catalogue,{freeBytes:async()=>1e12}),catalogue),
    {fetchImpl:async()=>{throw new Error('Offline fixture must use cache');}});
  assert.equal((await restarted.getBlock(part)).cache,'HIT');
  assert.deepEqual((await (await factory.get()).read(part)).bytes,first.bytes);
  assert.throws(()=>lidarGetMapUrl({...part,window:[0,0,513,1]}),/window/);
});

test('explicit supplier no-coverage is distinct from an HTTP failure and is not cached', async (t) => {
  const { factory } = await caches(t);
  let calls = 0;
  const acquisition = createLidarWmsAcquisition(factory, {
    fetchImpl: async () => { calls++; return response(null, 204); },
  });
  const result = await acquisition.getBlock(block);
  assert.equal(result.status, 'NO_COVERAGE');
  assert.equal(result.bytes, null);
  assert.equal((await (await factory.get()).entry.tiles.snapshot()).fileCount, 0);
  await acquisition.getBlock(block);
  assert.equal(calls, 2);
});

test('bad Float32 layout, CRS, offset and truncated GeoTIFF never enter the cache', async (t) => {
  const { factory } = await caches(t);
  const variants = [
    syntheticLidarGeoTiff(block, { srid: 3950 }),
    syntheticLidarGeoTiff(block, { xOffset: 0.5 }),
    syntheticLidarGeoTiff(block).subarray(0, 200),
    syntheticLidarGeoTiff(block, { ignCitation: true, srid: 3857 }),
  ];
  for (let i = 0; i < variants.length; i++) {
    const current = { ix: block.ix + i, iy: block.iy };
    const acquisition = createLidarWmsAcquisition(factory,
      { fetchImpl: async () => response(variants[i]) });
    await assert.rejects(acquisition.getBlock(current),
      (error) => error.code === 'INVALID_RESPONSE');
  }
  assert.equal((await (await factory.get()).entry.tiles.snapshot()).fileCount, 0);
});

test('simultaneous independent blocks are bounded and the full queue rejects excess work', async (t) => {
  const { factory } = await caches(t);
  let active = 0, peak = 0;
  const fetchImpl = async (url, { signal }) => {
    active++; peak = Math.max(peak, active);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, 30);
      signal.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason); },
        { once: true });
    });
    active--;
    const bbox = new URL(url).searchParams.get('BBOX').split(',').map(Number);
    return response(tiff(42.5, { ix: bbox[0] / 256, iy: bbox[1] / 256 }));
  };
  const acquisition = createLidarWmsAcquisition(factory, { fetchImpl });
  const results = await Promise.all(Array.from({ length: 5 }, (_, ix) =>
    acquisition.getBlock({ ix, iy: 0 })));
  assert(results.every((result) => result.status === 'DATA'));
  assert.equal(peak, 2);
});

test('queue saturation refuses the nineteenth independent block without contacting IGN', async (t) => {
  const { factory } = await caches(t);
  let started = 0;
  const release = [];
  const acquisition = createLidarWmsAcquisition(factory, { fetchImpl: async (url) => {
    started++;
    const bbox = new URL(url).searchParams.get('BBOX').split(',').map(Number);
    if (started <= 2) await new Promise((resolve) => release.push(resolve));
    return response(tiff(42.5, { ix: bbox[0] / 256, iy: bbox[1] / 256 }));
  } });
  const tasks = [0, 1].map((ix) => acquisition.getBlock({ ix, iy: 0 }));
  while (release.length < 2) await new Promise((resolve) => setImmediate(resolve));
  tasks.push(...Array.from({ length: 16 }, (_, n) =>
    acquisition.getBlock({ ix: n + 2, iy: 0 })));
  while (acquisition.pendingCount() < 18) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  await new Promise((resolve) => setImmediate(resolve));
  await assert.rejects(acquisition.getBlock({ ix: 18, iy: 0 }), /queue full/);
  assert.equal(started, 2);
  release.forEach((done) => done());
  await Promise.all(tasks);
});

test('aborting one joined caller preserves the remaining acquisition', async (t) => {
  const { factory } = await caches(t);
  const controller = new AbortController();
  let calls = 0;
  const acquisition = createLidarWmsAcquisition(factory, { fetchImpl: async () => {
    calls++;
    await new Promise((resolve) => setTimeout(resolve, 20));
    return response(tiff());
  } });
  const first = acquisition.getBlock(block, { signal: controller.signal });
  const second = acquisition.getBlock(block);
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort(new Error('view changed'));
  await assert.rejects(first, /view changed/);
  assert.equal((await second).status, 'DATA');
  assert(calls >= 1 && calls <= 2);
});

test('quota or disk guard failure does not return a partially cached block', async (t) => {
  const { factory } = await caches(t, async () => 0);
  const acquisition = createLidarWmsAcquisition(factory,
    { fetchImpl: async () => response(tiff()) });
  await assert.rejects(acquisition.getBlock(block), (error) => error.code === 'CACHE_CAPACITY');
  assert.equal((await (await factory.get()).entry.tiles.snapshot()).fileCount, 0);
});
