// Level A/B: real cache files and owner, no provider acquisition. Observation must never clean.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, readFile, utimes, rm } from 'node:fs/promises';
import path from 'node:path';
import { loadCacheProducts } from './cache-products.mjs';
import { createCacheRegistry } from './cache-registry.mjs';
import { CacheSettings } from './cache-settings.mjs';

const catalogueFile = path.resolve(import.meta.dirname, '../../webapp/backend/digues_webapp/cartographic_caches.json');
const A = {instance_id:'11111111-1111-4111-8111-111111111111',structure_id:'22222222-2222-4222-8222-222222222222',business_database_id:'33333333-3333-4333-8333-333333333333'};
const B = {...A,business_database_id:'44444444-4444-4444-8444-444444444444'};

test('readonly global inventory excludes historical namespaces and never cleans temporaries', async()=>{
 const root=await mkdtemp(path.resolve('audits/storage-observe-'));
 try {
  const catalogue=await loadCacheProducts(catalogueFile);
  await mkdir(path.join(root,'ign','plan'),{recursive:true});
  await writeFile(path.join(root,'ign','plan','tile.png'),'GLOBAL');
  const tmp=path.join(root,'ign','plan','old.tmp'); await writeFile(tmp,'KEEP'); await utimes(tmp,1,1);
  await mkdir(path.join(root,'instances','old'),{recursive:true}); await writeFile(path.join(root,'instances','old','keep'),'OLD');
  const reader=createCacheRegistry(root,catalogue), settings=new CacheSettings({cacheRoot:root,catalogue,registry:reader});
  const value=await settings.storageSnapshot(); assert.equal(value.complete,true);
  assert(value.scopes.every(s=>s.kind==='global' && s.target===null));
  assert.equal(value.scopes.find(s=>s.product==='ign-plan').categories.tiles.logical_bytes,6);
  assert.equal(await readFile(tmp,'utf8'),'KEEP'); assert.equal(reader.get('ign-plan').tiles.inventory,null);
 } finally {await rm(root,{recursive:true,force:true});}
});

test('cold observation refuses a linked cache and reports an incomplete inventory',async()=>{
  const root=await mkdtemp(path.join(process.env.TMPDIR || path.resolve('audits'),'storage-links-'));
  try {
    const {symlink}=await import('node:fs/promises');
    await mkdir(path.join(root,'outside'));await writeFile(path.join(root,'outside','keep'),'PRIVATE');
    await mkdir(path.join(root,'ign'));await symlink(path.join(root,'outside'),path.join(root,'ign','plan'));
    const catalogue=await loadCacheProducts(catalogueFile);
    const registry=createCacheRegistry(root,catalogue,{});
    const settings=new CacheSettings({cacheRoot:root,catalogue,registry});
    const value=await settings.storageSnapshot();
    assert.equal(value.complete,false);
    assert.equal(value.scopes.find(s=>s.product==='ign-plan').categories.tiles.logical_bytes,null);
    assert.equal(await readFile(path.join(root,'outside','keep'),'utf8'),'PRIVATE');
  } finally {await rm(root,{recursive:true,force:true});}
});
