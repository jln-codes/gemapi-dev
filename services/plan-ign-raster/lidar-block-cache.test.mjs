import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { createLidarBlockCacheFactory, LIDAR_PRODUCT_ID, lidarBlockIdentity } from './lidar-block-cache.mjs';
import { syntheticLidarGeoTiff } from './lidar-fixture.mjs';
import { MAX_GEOTIFF_BYTES } from './lidar-geotiff.mjs';
import { loadCacheProducts } from './cache-products.mjs';
import { cacheStats, createCacheRegistry } from './cache-registry.mjs';
import { CacheSettings } from './cache-settings.mjs';

const auditRoot = path.resolve(import.meta.dirname, '../../audits');
const productFile = new URL('../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url);
const baseA = { instance_id: '11111111-1111-4111-8111-111111111111',
  structure_id: '22222222-2222-4222-8222-222222222222',
  business_database_id: '33333333-3333-4333-8333-333333333333' };
const baseB = { ...baseA, business_database_id: '44444444-4444-4444-8444-444444444444' };
const block = { ix: 0, iy: 0 };

async function fixture(t) {
  await mkdir(auditRoot, { recursive: true });
  const root = await mkdtemp(path.join(auditRoot, 'lidar-cache-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const catalogue = await loadCacheProducts(productFile);
  const registry = createCacheRegistry(root, catalogue, { freeBytes: async () => 1e12 });
  const settings = new CacheSettings({ cacheRoot: root, catalogue, registry,
    logger: { info() {}, warn() {}, error() {} } });
  await settings.load();
  const factory = createLidarBlockCacheFactory(registry, catalogue);
  return { root, catalogue, registry, settings, factory };
}

test('bases reuse the same numeric file, also after owner restart', async t => {
  const { root, catalogue, registry, factory } = await fixture(t);
  const a = await factory.get(), b = await factory.get();
  assert.equal(a, b);
  let calls = 0;
  const produce = async () => { calls++; return syntheticLidarGeoTiff(block); };
  const first = await a.getOrCreate(block, produce);
  const second = await b.getOrCreate(block, produce);
  assert.equal(calls, 1); assert.equal(second.cache, 'HIT');
  assert.deepEqual(first.bytes, second.bytes);
  const restarted = createLidarBlockCacheFactory(createCacheRegistry(root, catalogue, { freeBytes: async () => 1e12 }), catalogue);
  assert.deepEqual((await (await restarted.get()).read(block)).bytes, first.bytes);
  assert.equal((await cacheStats(registry)).products.find(p => p.id === LIDAR_PRODUCT_ID).fileCount, 1);
});

test('concurrent identical synthetic production and reads share one operation', async (t) => {
  const { factory } = await fixture(t);
  const cache = await factory.get();
  let calls = 0;
  const results = await Promise.all(Array.from({ length: 10 }, () => cache.getOrCreate(block, async () => {
    calls++; return syntheticLidarGeoTiff(block);
  })));
  assert.equal(calls, 1);
  assert(results.every((item) => item.key === results[0].key && item.cache === 'CREATED'));
  const priorHits = cache.entry.tiles.metrics.hits;
  const reads = await Promise.all(Array.from({ length: 10 }, () => cache.read(block)));
  assert(reads.every((item) => item.bytes.equals(reads[0].bytes)));
  assert.equal(cache.entry.tiles.metrics.hits, priorHits + 1);
});

test('validation and synthetic production share a two-slot limit across bases', { timeout: 10_000 }, async (t) => {
  const { factory } = await fixture(t);
  const caches = await Promise.all([baseA, baseB, { ...baseB,
    business_database_id: '55555555-5555-4555-8555-555555555555' }]
    .map((target) => factory.get()));
  let active = 0, peak = 0;
  const releases = [];
  let twoStarted;
  const started = new Promise((resolve) => { twoStarted = resolve; });
  const tasks = caches.map((cache, ix) => cache.getOrCreate({ix, iy:0}, async () => {
    active++; peak = Math.max(peak, active);
    if (active === 2) twoStarted();
    await new Promise((resolve) => releases.push(resolve));
    active--;
    return syntheticLidarGeoTiff({ix, iy:0});
  }));
  await started;
  assert.equal(peak, 2);
  releases.splice(0).forEach((release) => release());
  // The third producer begins only after a shared slot is free.
  while (releases.length === 0) await new Promise((resolve) => setImmediate(resolve));
  releases.splice(0).forEach((release) => release());
  await Promise.all(tasks);
  assert.equal(peak, 2);
});

test('a full shared queue refuses excess work without changing cache files', { timeout: 10_000 }, async (t) => {
  const { factory } = await fixture(t);
  const cache = await factory.get();
  const releases = [];
  let twoStarted;
  const started = new Promise((resolve) => { twoStarted = resolve; });
  const held = (index) => cache.getOrCreate({ ix: index, iy: 0 }, async () => {
    if (index < 2) await new Promise((resolve) => {
      releases.push(resolve); if (releases.length === 2) twoStarted();
    });
    throw new Error('synthetic cancellation');
  });
  const first = [held(0), held(1)];
  await started;
  const waiting = Array.from({ length: 16 }, (_, offset) => held(offset + 2));
  await assert.rejects(held(18), /work queue full/);
  releases.splice(0).forEach((release) => release());
  const settled = await Promise.allSettled([...first, ...waiting]);
  assert(settled.every((item) => item.status === 'rejected' &&
    item.reason.message === 'synthetic cancellation'));
  assert.equal((await cache.entry.tiles.snapshot()).fileCount, 0);
});

test('malformed and oversized cached files become MISS without escaping the base root', async (t) => {
  const { root, catalogue, factory } = await fixture(t);
  const cache = await factory.get();
  const key = lidarBlockIdentity(catalogue.products.get(LIDAR_PRODUCT_ID), block).key;
  const filename = cache.entry.tiles.filename(key);
  await mkdir(path.dirname(filename), { recursive: true });
  await writeFile(filename, Buffer.from('<html>IGN error</html>'));
  assert.equal(await cache.read(block), null);
  await writeFile(filename, Buffer.alloc(MAX_GEOTIFF_BYTES + 1));
  assert.equal(await cache.read(block), null);
  assert.equal(cache.entry.tiles.metrics.corrupt, 2);
  assert.equal((await cache.entry.tiles.snapshot()).fileCount, 0);
  const outside = path.join(root, 'outside');
  await writeFile(outside, 'private');
  await symlink(outside, filename);
  await assert.rejects(cache.read(block), /ELOOP/);
  assert.equal(await readFile(outside, 'utf8'), 'private');
});

test('an interrupted numeric temporary is neither served nor inventoried after restart', async (t) => {
  const { root, catalogue, factory } = await fixture(t);
  const cache = await factory.get();
  const key = lidarBlockIdentity(catalogue.products.get(LIDAR_PRODUCT_ID), block).key;
  const filename = cache.entry.tiles.filename(key);
  await mkdir(path.dirname(filename), { recursive: true });
  await writeFile(`${filename}.interrupted.tmp`, syntheticLidarGeoTiff(block).subarray(0, 100));
  assert.equal(await cache.read(block), null);
  const restarted = createCacheRegistry(root, catalogue, { freeBytes: async () => 1e12 });
  const restored = createLidarBlockCacheFactory(restarted, catalogue);
  assert.equal(await (await restored.get()).read(block), null);
  assert.equal((await cacheStats(restarted)).products.find((item) =>
    item.id === LIDAR_PRODUCT_ID)?.fileCount, 0);
});

test('grid, source and generation are part of the stable, safe resource key', async (t) => {
  const { catalogue, factory } = await fixture(t);
  const product = catalogue.products.get(LIDAR_PRODUCT_ID);
  const first = lidarBlockIdentity(product, { ix: -1, iy: 3 });
  const changed = lidarBlockIdentity(product, { ix: -1, iy: 3 }, undefined,
    { endpoint: 'https://example.invalid/other' });
  assert.notEqual(first.key, changed.key);
  assert.match(first.key, /^lidar-hd-mnt-v1\/[a-f0-9]{24}\/n1\/p3\.tif$/);
  const cache = await factory.get();
  assert.equal(cache.entry.tiles.filename(first.key).startsWith(cache.entry.root), true);
  assert.throws(() => lidarBlockIdentity(product, { ix: '../escape', iy: 0 }), /Invalid block index/);
});
