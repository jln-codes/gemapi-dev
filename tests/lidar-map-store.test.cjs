const { test } = require('node:test');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const { createLidarStore } = require('../webapp/frontend/js/offline-basemap-store.js');
const { visibleEnsembles } = require('../webapp/frontend/js/lidar-map.js');
const core = require('../webapp/frontend/js/lidar-prototype-core.js');

function persistence() {
  const rows = { lidarPlans: new Map(), lidarBlocks: new Map() };
  return { rows,
    async one(name, key) { return rows[name].get(key); },
    async all(name) { return [...rows[name].values()]; },
    async write(name, value) { rows[name].set(value.key, value); return value; },
    async remove(name, key) { rows[name].delete(key); },
  };
}
const contextA = '11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222:33333333-3333-4333-8333-333333333333:44444444-4444-4444-8444-444444444444';
const contextB = contextA.replace('33333333-3333-4333-8333-333333333333',
  '55555555-5555-4555-8555-555555555555');
const se = { type: 'SE', id: '66666666-6666-4666-8666-666666666666' };
const block = { id: 'a'.repeat(64), ix: 1, iy: 2, bbox: [256, 512, 512, 768] };
const selection = [se];

// Niveau A : candidats issus uniquement des FK canoniques et des emprises
// EPSG:2154 ; aucune donnée IGN, aucune ressource mutable partagée.
const source = () => ({ hierarchy: { systemes: [{ id: se.id, digues: [{ id: 'digue' }] }] },
  geometries: { version: 1, crs: { srid: 2154 },
    troncons: [{ digue_id: 'digue', bbox: [270, 540, 380, 680] }], zones_stockage: [] } });
test('visible SE/AH follow canonical parents, deduplicate, and include the mask buffer', () => {
  const data = source();
  data.geometries.troncons.push({ digue_id: 'digue', bbox: [270, 540, 380, 680] });
  const ah = { type: 'AH', id: '77777777-7777-4777-8777-777777777777' };
  data.geometries.zones_stockage.push({ amenagement_hydraulique_id: ah.id, bbox: [1000, 1000, 1100, 1100] });
  assert.deepEqual(visibleEnsembles(data, [260, 520, 400, 700]), selection);
  assert.deepEqual(visibleEnsembles(data, [395, 550, 420, 560]), selection);
  assert.deepEqual(visibleEnsembles(data, [406, 550, 420, 560]), []);
  assert.deepEqual(visibleEnsembles(data, [406, 550, 420, 560], 100), selection);
  assert.deepEqual(visibleEnsembles(data, [1010, 1010, 1050, 1050]), [ah]);
  data.geometries.troncons[0].bbox = null;
  data.geometries.troncons[1].bbox = null;
  assert.deepEqual(visibleEnsembles(data, [260, 520, 400, 700]), []);
});
test('automatic candidates fail closed on ambiguous/missing parents, invalid identities and wrong CRS', () => {
  const data = source();
  data.hierarchy.systemes.push({ id: '77777777-7777-4777-8777-777777777777', digues: [{ id: 'digue' }] });
  assert.throws(() => visibleEnsembles(data, [260, 520, 400, 700]), /ambigu/);
  data.hierarchy.systemes = [];
  assert.throws(() => visibleEnsembles(data, [260, 520, 400, 700]), /invalide/);
  data.geometries.crs.srid = 3950;
  assert.throws(() => visibleEnsembles(data, [260, 520, 400, 700]), /indisponibles/);
});
test('more than 32 visible ensembles remain selected; defensive request budget never truncates', () => {
  const data = source();
  data.geometries.zones_stockage = Array.from({ length: 32 }, (_, i) => ({
    amenagement_hydraulique_id: `77777777-7777-4777-8777-${String(i).padStart(12, '0')}`,
    bbox: [270, 540, 380, 680],
  }));
  assert.equal(visibleEnsembles(data, [260, 520, 400, 700]).length, 33);
  data.geometries.zones_stockage = Array.from({ length: 1024 }, (_, i) => ({
    amenagement_hydraulique_id: `77777777-7777-4777-8777-${String(i).padStart(12, '0')}`,
    bbox: [270, 540, 380, 680],
  }));
  assert.throws(() => visibleEnsembles(data, [260, 520, 400, 700]), /bornée/);
});

test('shared cartographic persistence isolates numeric LiDAR plans and checks integrity', async () => {
  // Level B local persistence contract: two business contexts reuse identical
  // block IDs. Corrupted bytes must not yield an offline hit.
  const records = persistence();
  const store = createLidarStore({ core, persistence: records, cryptoImpl: webcrypto,
    maxBytes: 1024, now: () => 10 });
  const plan = { policy_version: 1, buffer_m: 25, version: 'LIDAR_DISTRIBUTION_V2', grid_id: store.GRID, crs: 'EPSG:2154', blocks: [block],
    mask: { type: 'MultiPolygon', coordinates: [[[[256,512],[512,512],[512,768],[256,768],[256,512]]]] },
    ensembles: [{ ...se, status: 'READY' }] };
  const bbox = [260, 520, 400, 700];
  await store.putPlan(contextA, selection, bbox, plan);
  assert.equal(await store.findPlan(contextB, selection, bbox), null);
  assert.equal(await store.findPlan(contextB, null, bbox), null);
  assert.equal(await store.findPlan(contextA, null, [1000, 1000, 1100, 1100]), null);
  assert.deepEqual((await store.findPlan(contextA, null, bbox)).ensembles, plan.ensembles);
  const stored = await store.findPlan(contextA, selection, bbox);
  assert.equal(stored.partialView, false);
  assert.equal((await store.findPlan(contextA, selection, [300, 550, 450, 720])).partialView, true);
  assert.deepEqual(await store.inspectPlan(contextA, stored), { expected: 1, present: 0, ready: false });
  const coarseBlock={id:'b'.repeat(64),ix:0,iy:0,level:10,window:[0,1,1,2],bbox:[0,512,512,1024]};
  const coarsePlan={...plan,level:10,blocks:[coarseBlock]};
  await store.putPlan(contextA,selection,bbox,coarsePlan);
  assert.equal((await store.findPlan(contextA,null,bbox,10)).blocks[0].id,coarseBlock.id);
  assert.equal((await store.findPlan(contextA,null,bbox,0)).blocks[0].id,block.id);
  await store.putBlock(contextA,coarseBlock,new Uint8Array([73,73,1,2,3,4,5,6]).buffer);
  assert.equal((await store.getBlock(contextA,coarseBlock)).bytes.byteLength,8);
  assert.equal(await store.getBlock(contextB,coarseBlock),null);
  await store.putBlock(contextA, block, new Uint8Array([73, 73, 1, 2, 3, 4, 5, 6]).buffer);
  assert.equal((await store.getBlock(contextA, block)).bytes.byteLength, 8);
  assert.equal(await store.getBlock(contextB, block), null);
  assert.deepEqual(await store.inspectPlan(contextA, stored), { expected: 1, present: 1, ready: true });
  [...records.rows.lidarBlocks.values()].find(row=>row.id===block.id).bytes = new Uint8Array(8).buffer;
  assert.equal(await store.getBlock(contextA, block), null);
  assert.deepEqual(await store.inspectPlan(contextA, stored), { expected: 1, present: 0, ready: false });
});

test('buffer revision invalidates older plans even when an old response arrives late; raw block stays reusable', async () => {
 const records=persistence(),store=createLidarStore({core,persistence:records,cryptoImpl:webcrypto,now:()=>10});
 const plan={version:'LIDAR_DISTRIBUTION_V2',grid_id:store.GRID,crs:'EPSG:2154',policy_version:1,buffer_m:25,
  blocks:[block],mask:{type:'MultiPolygon',coordinates:[]},ensembles:[{...se,status:'READY'}]};
 const bbox=[260,520,400,700];
 await store.putBlock(contextA,block,new Uint8Array([73,73,1,2,3,4,5,6]).buffer);
 await store.putPlan(contextA,selection,bbox,plan);
 await store.putPlan(contextA,selection,bbox,{...plan,policy_version:2,buffer_m:50});
 await store.putPlan(contextA,selection,bbox,plan); // other tab / late network result
 assert.equal((await store.findPlan(contextA,null,bbox)).policy_version,2);
 assert.equal((await store.getBlock(contextA,block)).bytes.byteLength,8);
 assert.equal(await store.findPlan(contextB,null,bbox),null);
});
