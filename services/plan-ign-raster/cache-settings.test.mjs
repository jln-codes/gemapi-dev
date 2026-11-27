import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { loadCacheProducts } from './cache-products.mjs';
import { createCacheRegistry } from './cache-registry.mjs';
import { CacheSettings } from './cache-settings.mjs';
// A: readonly owner diagnostics; obsolete administrative controls cannot mutate files.
test('global descriptive diagnostics preserve legacy files and never invent physical bytes', async t => {
  const root = await mkdtemp(path.resolve(import.meta.dirname,'../../audits/cache-description-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const catalogue=await loadCacheProducts(new URL('../../webapp/backend/digues_webapp/cartographic_caches.json',import.meta.url));
  const registry=createCacheRegistry(root,catalogue,{freeBytes:async()=>1e12});
  const legacy='{"old_operator_settings":true}'; await writeFile(path.join(root,'cache-settings.json'),legacy);
  const settings=new CacheSettings({cacheRoot:root,catalogue,registry});
  await assert.rejects(settings.load(), /operator review/);
  assert.equal(await readFile(path.join(root,'cache-settings.json'),'utf8'),legacy);
  await rename(path.join(root,'cache-settings.json'),path.join(root,'cache-settings.pre-cache10.json'));
  await settings.load();
  await registry.get('ign-plan').tiles.write('v/tile.png',Buffer.from('abc'));
  const status=await settings.snapshot(); assert.equal(status.schema_version,3);
  assert.equal(status.products.length,4); assert.equal(settings.update,undefined); assert.equal(settings.purge,undefined);
  assert.equal(await readFile(path.join(root,'cache-settings.pre-cache10.json'),'utf8'),legacy);
  const observed=await settings.storageSnapshot(); assert.equal(observed.complete,true);
  assert(observed.scopes.every(s=>s.target===null && s.kind==='global'));
  assert.equal(observed.scopes.find(s=>s.product==='ign-plan').categories.tiles.logical_bytes,3);
  assert(observed.scopes.every(s=>s.categories.tiles.physical_bytes===null));
});
