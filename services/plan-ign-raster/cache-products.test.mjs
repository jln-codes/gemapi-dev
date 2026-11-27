// A: synthetic global identity and strict catalogue, no provider or production I/O.
import assert from 'node:assert/strict';
import { readFile, mkdtemp, mkdir, rm, writeFile, utimes, symlink } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { parseCacheProducts, assertBasemapProductCompatibility } from './cache-products.mjs';
import { parseCachePolicies } from './cache-policy.mjs';
import { createCacheRegistry } from './cache-registry.mjs';
const document = JSON.parse(await readFile(new URL('../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url)));
export async function fixture(t, capacity = 12, freeBytes = async () => 1e12) {
  const root = await mkdtemp(path.resolve(import.meta.dirname, '../../audits/global-cache-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const catalogue = parseCacheProducts({ ...document, capacity_bytes: capacity, min_free_bytes: 1 });
  return { root, catalogue, registry: createCacheRegistry(root, catalogue, { freeBytes }) };
}
test('four public products, disjoint roots, matching freshness declarations; no base identity in catalogue', async t => {
  const catalogue = parseCacheProducts(document);
  const basemaps = parseCachePolicies(await readFile(new URL('../../webapp/backend/digues_webapp/basemaps.toml', import.meta.url), 'utf8'));
  assertBasemapProductCompatibility(basemaps.basemaps, catalogue, basemaps.minFreeBytes);
  assert.equal(catalogue.products.size, 4); assert.equal(basemaps.definitions.size, 3);
  for (const scope of ['business_database', 'user']) assert.throws(() => parseCacheProducts({ ...document,
    products: [{ ...document.products[0], scope }] }));
  assert.throws(() => parseCacheProducts({ ...document, products: [...document.products,
    { ...document.products[1], id: 'bad', directory: 'ign/plan/nested' }] }), /Overlapping/);
  const { registry } = await fixture(t);
  for (const id of registry.keys()) {
    const a = await registry.resolve(id), b = await registry.resolve(id);
    assert.equal(a, b); assert.equal(a.target, null);
  }
});
test('global LRU crosses products, refreshes access, protects pins and survives restart', async t => {
  const { root, catalogue, registry } = await fixture(t, 9);
  const a = registry.get('ign-plan').tiles, b = registry.get('ign-ortho').tiles,
    c = registry.get('ign-lidar-hd-mnt').tiles;
  await a.write('v1/old.bin', Buffer.from('aaa'));
  await b.write('v1/hot.bin', Buffer.from('bbb'));
  await c.write('v1/pinned.bin', Buffer.from('ccc'));
  // Controlled persisted access dates avoid sleeps and test restart reconstruction.
  for (const [cache, key, age] of [[a,'old',30000],[b,'hot',20000],[c,'pinned',40000]])
    await utimes(cache.filename(`v1/${key}.bin`), new Date(Date.now()-age), new Date());
  const restarted = createCacheRegistry(root, catalogue, { freeBytes: async () => 1e12 });
  const c2 = restarted.get('ign-lidar-hd-mnt').tiles;
  const release = c2.pin(['v1/pinned.bin']);
  const a2 = restarted.get('ign-plan').tiles, b2 = restarted.get('ign-ortho').tiles;
  await b2.read('v1/hot.bin');
  await a2.write('v1/new.bin', Buffer.from('ddd'));
  assert.equal(await a2.read('v1/old.bin'), null);
  assert.equal((await c2.read('v1/pinned.bin')).bytes.toString(), 'ccc');
  assert.equal((await b2.read('v1/hot.bin')).bytes.toString(), 'bbb'); release();
  assert.equal((await a2.snapshot()).evictions, 1);
  assert.equal([...restarted.values()].reduce((n,e)=>n+e.tiles.occupiedBytes+(e.resources?.occupiedBytes||0),0),9);
});
test('admission considers net releasable space, atomic replacement and protected publications', async t => {
  let physicalFree = 100;
  const { registry } = await fixture(t, 6, async () => physicalFree);
  const a = registry.get('ign-plan').tiles, b = registry.get('ign-ortho').tiles;
  await a.write('v/a.bin', Buffer.from('aaa'));
  const originalEvict = a.evict.bind(a);
  a.evict = async item => { const freed = await originalEvict(item); physicalFree += freed; return freed; };
  physicalFree = 2;
  await b.write('v/b.bin', Buffer.from('bbb')); // 2 free + 3 evictable - 3 new >= 1 reserve
  assert.equal(await a.read('v/a.bin'), null);
  const release = b.pin(['v/b.bin']); physicalFree = 1;
  await assert.rejects(a.write('v/c.bin', Buffer.from('ccc')), /capacity/);
  assert.equal(a.metrics.writeErrors,1);assert.equal(a.metrics.noSpace,1);
  assert.equal((await b.read('v/b.bin')).bytes.toString(), 'bbb'); release();
});
test('concurrent publications stay in one envelope; ties deterministic; historical directories untouched', async t => {
  const { root, catalogue, registry } = await fixture(t, 6);
  const historical = path.join(root,'instances','old'); await mkdir(historical,{recursive:true});
  await writeFile(path.join(historical,'keep.bin'),'historical');
  const stores = [...registry.values()].map(e=>e.tiles);
  await Promise.all(stores.map((store,i)=>store.write(`v/${i}.bin`,Buffer.from('abc'))));
  assert.equal(stores.reduce((n,s)=>n+s.occupiedBytes,0),6);
  const restored = createCacheRegistry(root,catalogue,{freeBytes:async()=>1e12});
  assert.equal((await Promise.all([...restored.values()].map(e=>e.tiles.snapshot()))).reduce((n,s)=>n+s.sizeBytes,0),6);
  assert.equal(await readFile(path.join(historical,'keep.bin'),'utf8'),'historical');
  await symlink(historical,path.join(root,'ign','plan','escape'));
  await assert.rejects(registry.get('ign-plan').tiles.write('escape/x.bin',Buffer.from('bad')), /Unsafe/);
});

// A: file length must not be mistaken for physically reclaimable allocation.
test('physical admission observes free space rather than summing logical file lengths', async t => {
 let free=100;
 const {registry}=await fixture(t,100,async()=>free);
 const a=registry.get('ign-plan').tiles,b=registry.get('ign-ortho').tiles;
 await a.write('v/small.bin',Buffer.from('abc'));
 const evict=a.evict.bind(a);
 a.evict=async item=>{const size=await evict(item);if(size)free+=20;return size;};
 free=2; // A small payload can occupy a full allocation unit.
 await b.write('v/new.bin',Buffer.alloc(10,1));
 assert.equal(await a.read('v/small.bin'),null);
 assert.equal((await b.read('v/new.bin')).bytes.length,10);
});
