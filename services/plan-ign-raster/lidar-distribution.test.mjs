import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import { loadCacheProducts } from './cache-products.mjs';
import { LIDAR_PRODUCT_ID } from './lidar-block-cache.mjs';
import { createLidarDistribution } from './lidar-distribution.mjs';
import { createRequestHandler, closeServer, listen } from './server.mjs';
import { syntheticLidarGeoTiff } from './lidar-fixture.mjs';

const products = await loadCacheProducts(new URL('../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url));
const product = products.products.get(LIDAR_PRODUCT_ID);
const polygon = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
const mask = (status, polygons = []) => ({ type: 'SE', id: '44444444-4444-4444-8444-444444444444',
  status, geometry: status === 'READY' ? { type: 'MultiPolygon', coordinates: polygons } : null });
const request = (masks, view = [240, 500, 550, 800]) => ({
  masks: { version: 'LIDAR_MASK_V1', crs: 'EPSG:2154', masks },
  view: { crs: 'EPSG:2154', bbox: view } });

// Niveau A, no IO/provider: observable resolution, area, cells and all source
// islands retained. A stale/forged window cannot authorize a new WMS request.
test('large masks coarsen to bounded cells instead of rejecting extent or native block count', () => {
  const distribution = createLidarDistribution({ getBlock: async () => null }, product);
  for (const side of [1280, 4096, 50000, 2000000]) {
    const body = request([mask('READY', [[polygon(0, 0, side, side)]])], [0, 0, side, side]);
    const plan = distribution.plan(body);
    assert.ok(plan.blocks.length > 0 && plan.blocks.length <= 128);
    assert.ok(plan.estimated_bytes <= 24 * 1024 * 1024);
    assert.ok(plan.level > 0);
    assert.equal(plan.resolution_m, .5 * 2 ** plan.level);
  }
  const islands = Array.from({length: 40}, (_, i) => [polygon(i * 100, 0, i * 100 + 10, 10)]);
  const plan = distribution.plan(request(islands.map(part => mask('READY', [part])), [0,0,4000,1000]));
  assert.equal(plan.mask.coordinates.length, 40);
  assert.ok(plan.blocks.length > 0);
});

test('coarse windows avoid empty surroundings, holes and excessive diagonal overscan', async () => {
  let calls = 0;
  const distribution = createLidarDistribution({getBlock: async () => { calls++; return {}; }}, product);
  const body = request([mask('READY', [[polygon(700000,6600000,700050,6600050)]])],
    [0,0,1000000,10000000]);
  body.view.level = 10; // 512 m cells, one tiny mask inside a continental view.
  const plan = distribution.plan(body);
  assert.ok(plan.estimated_bytes < 4096); // Useful local samples, not a full 512² block.
  assert.ok(plan.resolution_m <= 4); // The tiny mask must not acquire a continental-sized pixel.
  assert.ok(plan.blocks.every(b => b.width <= 14 && b.height <= 14));
  const block = plan.blocks[0];
  assert.equal(distribution.plan({...body,view:{...body.view,level:11}}).blocks[0].id, block.id);
  const finer = distribution.plan({...body,view:{...body.view,level:1}});
  assert.notEqual(finer.blocks[0].id,block.id);
  assert.equal(await distribution.read( {...body,block:{...block,id:'f'.repeat(64)}}), null);
  assert.equal(calls,0);
  const diagonal = request([mask('READY', [[[[0,0],[4096,4096],[4096,4146],[0,50],[0,0]]]])], [0,0,8192,8192]);
  diagonal.view.level = 4;
  const narrow = distribution.plan(diagonal);
  const acquiredArea = narrow.blocks.reduce((sum,b) => sum+(b.bbox[2]-b.bbox[0])*(b.bbox[3]-b.bbox[1]),0);
  assert.ok(acquiredArea < 4 * 4096 * 50, `${acquiredArea} m² acquired for a diagonal corridor`);
  const hole = request([mask('READY', [[polygon(0,0,4096,4096),polygon(256,256,3840,3840)]])], [0,0,4096,4096]);
  hole.view.level = 4;
  const hollow = distribution.plan(hole);
  assert.ok(hollow.blocks.every(b => !(b.bbox[0] >= 256 && b.bbox[1] >= 256 && b.bbox[2] <= 3840 && b.bbox[3] <= 3840)));
});

test('coverage union deduplicates masks, preserves partial blocks and rejects holes', () => {
  const distribution = createLidarDistribution({ getBlock: async () => null }, product);
  const ready = mask('READY', [[polygon(250, 510, 300, 550)]]);
  const planned = distribution.plan(request([ready, { ...ready, type: 'AH' }]));
  assert.deepEqual(planned.blocks.map(({ ix, iy }) => [ix, iy]), [[0, 1], [0, 2], [1, 1], [1, 2]]);
  assert.equal(new Set(planned.blocks.map((block) => block.id)).size, 4);
  assert.equal(planned.blocks[0].availability, 'UNCHECKED');
  const withHole = mask('READY', [[polygon(256, 512, 768, 768), polygon(256, 512, 512, 768)]]);
  assert.deepEqual(distribution.plan(request([withHole], [256, 512, 768, 768]))
    .blocks.map(({ ix }) => ix), [2]);
  assert.equal(distribution.plan(request([mask('EMPTY'), mask('ANOMALY')])).blocks.length, 0);
  assert.equal(distribution.plan(request([ready], [1000, 1000, 1200, 1200])).blocks.length, 0);
  assert.equal(distribution.plan(request([ready], [0, 0, 40_000, 40_000])).blocks.length, 4);
  assert.throws(() => distribution.plan(request([ready], [0, 0, Infinity, 1])), /Invalid bounded/);
});

test('read replans and returns an entire numeric block only while it intersects', async () => {
  const bytes = syntheticLidarGeoTiff({ ix: 1, iy: 2 }, { value: 12.5 });
  let calls = 0;
  const distribution = createLidarDistribution({ getBlock: async () => {
    calls++; return { status: 'DATA', cache: 'HIT', bytes };
  } }, product);
  const selection = request([mask('READY', [[polygon(270, 530, 300, 550)]])]);
  const planned = distribution.plan(selection);
  const block = planned.blocks.find((item) => item.ix === 1 && item.iy === 2);
  assert.ok(block);
  assert.deepEqual((await distribution.read( { ...selection, block })).acquired.bytes, bytes);
  assert.equal(calls, 1);
  assert.equal(await distribution.read( { ...selection,
    block: { ...block, id: 'f'.repeat(64) } }), null);
  assert.equal(await distribution.read( { ...request([mask('EMPTY')]), block }), null);
  assert.equal(await distribution.read( { ...selection, view: { crs: 'EPSG:2154',
    bbox: [1000, 1000, 1200, 1200] }, block }), null);
  assert.equal(calls, 1);
});

test('internal HTTP refuses unauthenticated and forged reads, serves full TIFF privately', async (t) => {
  const bytes = syntheticLidarGeoTiff({ ix: 1, iy: 2 }, { value: 18.25 });
  let calls = 0;
  const distribution = createLidarDistribution({ getBlock: async (block) => {
    calls++;
    if (block.ix === 0) return { status: 'NO_DATA', cache: 'CREATED',
      bytes: syntheticLidarGeoTiff(block, { value: -9999 }) };
    if (block.ix === -1) return { status: 'NO_COVERAGE', cache: 'MISS', bytes: null };
    return { status: 'DATA', cache: 'HIT', bytes };
  } }, product);
  const token = 'lidar-internal-test-secret-0123456789';
  const server = http.createServer(createRequestHandler({}, { lidarDistribution: distribution,
    lidarInternalToken: token, logger: { error() {} } }));
  const address = await listen(server, { host: '127.0.0.1', port: 0 });
  t.after(() => closeServer(server));
  const base = `http://127.0.0.1:${address.port}/internal/lidar/v1`;
  const body = request([mask('READY', [[polygon(270, 530, 300, 550)]])]);
  const post = (path, value, authorization = `Bearer ${token}`) => fetch(base + path, {
    method: 'POST', headers: { Authorization: authorization, 'Content-Type': 'application/json' },
    body: JSON.stringify(value),
  });
  assert.equal((await post('/plan', body, 'Bearer wrong')).status, 403);
  const planResponse = await post('/plan', body);
  assert.equal(planResponse.status, 200);
  assert.equal(planResponse.headers.get('cache-control'), 'no-store');
  const planned = await planResponse.json();
  const block = planned.blocks.find((item) => item.ix === 1 && item.iy === 2);
  assert.equal((await post('/block', { ...body, block: { ...block, id: 'f'.repeat(64) } })).status, 404);
  assert.equal(calls, 0);
  const response = await post('/block', { ...body, block });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'image/geotiff');
  assert.equal(response.headers.get('x-lidar-status'), 'DATA');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
  assert.equal(calls, 1);
  const noDataRequest = request([mask('READY', [[polygon(1, 513, 10, 520)]])], [0, 512, 256, 768]);
  const noDataBlock = (await (await post('/plan', noDataRequest)).json()).blocks[0];
  const noData = await post('/block', { ...noDataRequest, block: noDataBlock });
  assert.equal(noData.status, 200);
  assert.equal(noData.headers.get('x-lidar-status'), 'NO_DATA');
  assert.equal(noData.headers.get('x-cache'), 'MISS');
  const noDataBytes=Buffer.from(await noData.arrayBuffer());
  const {validateLidarGeoTiff}=await import('./lidar-geotiff.mjs');
  assert.equal((await validateLidarGeoTiff(noDataBytes,noDataBlock)).validPixels,0);
  const uncoveredRequest = request([mask('READY', [[polygon(-255, 513, -250, 520)]])],
    [-256, 512, 0, 768]);
  const uncoveredBlock = (await (await post('/plan', uncoveredRequest)).json()).blocks[0];
  const uncovered = await post('/block', { ...uncoveredRequest, block: uncoveredBlock });
  assert.equal(uncovered.status, 204);
  assert.equal(uncovered.headers.get('x-lidar-status'), 'NO_COVERAGE');
});

test('closing an internal read aborts its acquisition subscriber', { timeout: 5000 }, async (t) => {
  let enteredResolve, abandonedResolve;
  const entered = new Promise((resolve) => { enteredResolve = resolve; });
  const abandoned = new Promise((resolve) => { abandonedResolve = resolve; });
  const distribution = createLidarDistribution({ getBlock: (_block, { signal }) => {
    enteredResolve();
    return new Promise((_, reject) => signal.addEventListener('abort', () => {
      abandonedResolve(); reject(signal.reason);
    }, { once: true }));
  } }, product);
  const token = 'lidar-internal-test-secret-0123456789';
  const server = http.createServer(createRequestHandler({}, { lidarDistribution: distribution,
    lidarInternalToken: token, logger: { error() {} } }));
  const address = await listen(server, { host: '127.0.0.1', port: 0 });
  t.after(() => closeServer(server));
  const selection = request([mask('READY', [[polygon(270, 530, 300, 550)]])]);
  const block = distribution.plan(selection).blocks.find((item) => item.ix === 1 && item.iy === 2);
  const controller = new AbortController();
  const pending = fetch(`http://127.0.0.1:${address.port}/internal/lidar/v1/block`, {
    method: 'POST', signal: controller.signal,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...selection, block }),
  });
  await entered;
  controller.abort();
  await assert.rejects(pending, (error) => error.name === 'AbortError');
  await abandoned;
});
