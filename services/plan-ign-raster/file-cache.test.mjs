import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, rm, utimes, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { loadCacheProducts } from './cache-products.mjs';
import { cacheStats, createCacheRegistry } from './cache-registry.mjs';
import { CacheCapacityError, FileCache } from './file-cache.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const catalogue = path.resolve(here, '../../webapp/backend/digues_webapp/cartographic_caches.json');
const auditRoot = path.resolve(here, '../../audits');

async function temporaryRoot() {
  await mkdir(auditRoot, { recursive: true });
  return mkdtemp(path.join(auditRoot, 'carto2-cache-test-'));
}

test('the CARTO-1 catalogue gives three isolated physical cache roots', async () => {
  const root = await temporaryRoot();
  try {
    const policies = await loadCacheProducts(catalogue);
    const registry = createCacheRegistry(root, policies, { freeBytes: async () => 1e12 });
    assert.deepEqual([...registry.keys()], ['ign-epure', 'ign-plan', 'ign-ortho', 'ign-lidar-hd-mnt']);
    assert.notEqual(registry.get('ign-plan').root, registry.get('ign-ortho').root);
    assert.equal(registry.get('ign-epure').resources.root,
      path.join(root, 'ign', 'epure', 'resources'));
    assert.equal(registry.get('ign-epure').tiles.root, path.join(root, 'ign', 'epure'));
    const stats = await cacheStats(registry);
    assert.equal(stats.products.length, 4);
    assert(stats.products.every((item) => item.fileCount === 0));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('recreated caches retain files and LRU eviction cannot cross a basemap root', async () => {
  const root = await temporaryRoot();
  const make = (name, quotaBytes) => new FileCache({ root: path.join(root, name),
    quotaBytes, minFreeBytes: 10, freeBytes: async () => 1000 });
  try {
    const a = make('a', 6);
    const b = make('b', 6);
    await a.write('v1/one.bin', Buffer.from('111'));
    await b.write('v1/one.bin', Buffer.from('bbb'));
    await a.write('v1/two.bin', Buffer.from('222'));
    await utimes(path.join(root, 'a', 'v1', 'two.bin'),
      new Date(Date.now() - 60_000), new Date());
    const restarted = make('a', 6);
    assert.equal((await restarted.read('v1/one.bin')).bytes.toString(), '111');
    await restarted.write('v1/three.bin', Buffer.from('333'));
    assert.equal(await restarted.read('v1/two.bin'), null);
    assert.equal((await b.read('v1/one.bin')).bytes.toString(), 'bbb');
    assert.equal((await restarted.snapshot()).sizeBytes, 6);
    assert.equal(restarted.metrics.evictions, 1);
    assert.equal((await b.snapshot()).fileCount, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('full disk and protected entries reject writes deterministically', async () => {
  const root = await temporaryRoot();
  try {
    let free = 100;
    const cache = new FileCache({ root, quotaBytes: 6, minFreeBytes: 10,
      freeBytes: async () => free });
    await cache.write('v1/one.bin', Buffer.from('111'));
    await cache.write('v1/two.bin', Buffer.from('222'));
    const release = cache.pin(['v1/one.bin', 'v1/two.bin']);
    await assert.rejects(cache.write('v1/three.bin', Buffer.from('333')), CacheCapacityError);
    assert.equal((await cache.read('v1/one.bin')).bytes.toString(), '111');
    release();
    free = 11;
    await assert.rejects(cache.write('v1/three.bin', Buffer.from('333')), CacheCapacityError);
    assert.equal(cache.metrics.noSpace, 2);
    assert.equal((await cache.snapshot()).sizeBytes, 3);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('interrupted temporary and corrupt final never become hits', async () => {
  const root = await temporaryRoot();
  try {
    const cache = new FileCache({ root, quotaBytes: 100, minFreeBytes: 0,
      freeBytes: async () => 1000 });
    await mkdir(path.join(root, 'v1'), { recursive: true });
    const temporary = path.join(root, 'v1', 'a.bin.123.tmp');
    await writeFile(temporary, 'partial');
    const old = new Date(Date.now() - 2 * 60 * 60 * 1000);
    await utimes(temporary, old, old);
    assert.equal(await cache.read('v1/a.bin'), null);
    await cache.write('v1/a.bin', Buffer.from('good'));
    assert.deepEqual(await readdir(path.join(root, 'v1')), ['a.bin']);
    await writeFile(path.join(root, 'v1', 'a.bin'), 'bad');
    assert.equal(await cache.read('v1/a.bin', (bytes) => bytes.toString() === 'good'), null);
    assert.equal(cache.metrics.corrupt, 1);
    assert.equal((await cache.snapshot()).fileCount, 0);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('obsolete generations can be purged without touching current or pinned files', async () => {
  const root = await temporaryRoot();
  try {
    const cache = new FileCache({ root, quotaBytes: 100, minFreeBytes: 0,
      freeBytes: async () => 1000, activeGeneration: 'current' });
    await cache.write('old/a.bin', Buffer.from('a'));
    await cache.write('old/b.bin', Buffer.from('b'));
    await cache.write('current/a.bin', Buffer.from('c'));
    const release = cache.pin(['old/b.bin']);
    assert.equal(await cache.purgeGeneration('old'), 1);
    assert.equal((await readFile(path.join(root, 'old', 'b.bin'))).toString(), 'b');
    assert.equal((await cache.read('current/a.bin')).bytes.toString(), 'c');
    release();
    assert.equal(await cache.purgeGeneration('old'), 1);
    await assert.rejects(cache.purgeGeneration('current'), /active/);
    assert.equal((await cache.snapshot()).fileCount, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('creation age does not expire a recently used tile', async () => {
  const root = await temporaryRoot();
  try {
    const cache = new FileCache({ root, quotaBytes: 100, minFreeBytes: 0,
      freeBytes: async () => 1000 });
    await cache.write('epoch/a.bin', Buffer.from('old'));
    const old = new Date(Date.now() - 5000);
    await utimes(path.join(root, 'epoch', 'a.bin'), old, old);
    assert.equal((await cache.read('epoch/a.bin')).bytes.toString(), 'old');
    assert.equal((await cache.snapshot()).fileCount, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});
