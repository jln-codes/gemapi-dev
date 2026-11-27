const { test } = require('node:test');
const assert = require('node:assert/strict');
const geotiff = require('../webapp/frontend/vendor/geotiff/geotiff.js');
const core = require('../webapp/frontend/js/lidar-prototype-core.js');

const fixture = async (block, options) => {
  const { syntheticLidarGeoTiff } = await import('../services/plan-ign-raster/lidar-fixture.mjs');
  const bytes = syntheticLidarGeoTiff(block, options);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
};

test('full Float32 GeoTIFF keeps the 0.5 m grid, CRS, NoData and adjacent edges', async () => {
  const west = await core.decodeBlock(await fixture({ ix: 1, iy: 2 }, { value: 12.5 }),
    { id: 'west', ix: 1, iy: 2 }, geotiff);
  const east = await core.decodeBlock(await fixture({ ix: 2, iy: 2 }, { value: 31.25 }),
    { id: 'east', ix: 2, iy: 2 }, geotiff);
  assert.equal(west.values.length, 512 * 512);
  assert.equal(west.values[0], 12.5);
  assert.equal(west.nodata, -9999);
  assert.deepEqual(west.bbox, [256, 512, 512, 768]);
  const blocks = new Map([[core.key(1, 2), west], [core.key(2, 2), east]]);
  assert.equal(core.sample(blocks, 511.75, 700), 12.5);
  assert.equal(core.sample(blocks, 512.25, 700), 31.25);
  assert.equal(core.sample(blocks, 512, 700), 31.25);
  assert.ok(Number.isNaN(core.sample(blocks, 1000, 700)));
  await assert.rejects(core.decodeBlock(await fixture({ ix: 1, iy: 2 }, { value: 1 }),
    { id: 'wrong', ix: 3, iy: 2 }, geotiff), /Géoréférencement/);
  const cited = await core.decodeBlock(await fixture({ ix: 1, iy: 2 },
    { value: 7.5, ignCitation: true }), { id: 'ign-style', ix: 1, iy: 2 }, geotiff);
  assert.equal(cited.values[0], 7.5);
  await assert.rejects(core.decodeBlock(await fixture({ ix: 1, iy: 2 },
    { srid: 2155 }), { id: 'wrong-crs', ix: 1, iy: 2 }, geotiff), /Géoréférencement/);
});

test('NoData and non-finite values remain transparent; range is global and stable', async () => {
  const empty = await core.decodeBlock(await fixture({ ix: 0, iy: 0 }, { value: -9999 }),
    { id: 'empty', ix: 0, iy: 0 }, geotiff);
  assert.ok(Number.isNaN(core.sample(new Map([[core.key(0, 0), empty]]), 1, 1)));
  assert.equal(core.rangeOf([NaN, Infinity]), null);
  assert.deepEqual(core.rangeOf([NaN, 12.5, -2, 20]), { min: -2, max: 20, count: 3 });
  assert.deepEqual(core.color(NaN, 0, 100), [0, 0, 0, 0]);
  assert.deepEqual(core.color(50, 50, 50), core.color(50, 0, 100));
  const colors = core.colorize(Float32Array.from([-2, 20, NaN]), -2, 20);
  assert.equal(colors[3], 255);
  assert.equal(colors[11], 0);
  assert.notDeepEqual([...colors.slice(0, 4)], [...colors.slice(4, 8)]);
});

test('coarse subwindows sample their real position without altitude interpolation', async () => {
  const parts = [{id:'west',ix:1,iy:2,level:4,window:[2,3,9,5]},
    {id:'east',ix:1,iy:2,level:4,window:[9,3,12,5]}];
  const decoded = await Promise.all(parts.map(async (part,i) => core.decodeBlock(
    await fixture(part,{value:12.125+i}),part,geotiff)));
  const blocks = new Map([[core.key(1,2),decoded]]);
  assert.equal(decoded[0].values.length,14);
  assert.equal(core.sample(blocks,4120,8220+8,8),12.125);
  assert.equal(core.sample(blocks,4172,8220+8,8),13.125);
  assert.ok(Number.isNaN(core.sample(blocks,4500,8228,8)));
  await assert.rejects(core.decodeBlock(await fixture(parts[0]),{...parts[0],level:5},geotiff),/Géoréférencement/);
});

test('plan validation preserves memory, unique identities, mask closure and protocol',()=>{
  const block={id:'a'.repeat(64),ix:0,iy:0,bbox:[0,0,256,256]};
  const plan={version:'LIDAR_DISTRIBUTION_V2',grid_id:'epsg2154-050cm-512-v1',crs:'EPSG:2154',
    blocks:[block],mask:{type:'MultiPolygon',coordinates:[[[[0,0],[1,0],[1,1],[0,0]]]]}};
  assert.equal(core.validatePlan(plan),1024*1024);
  for(const bad of [{...plan,version:'UNKNOWN'}, {...plan,blocks:[block,block]},
    {...plan,blocks:[{...block,bbox:[0,0,256,255]}]},
    {...plan,mask:{type:'MultiPolygon',coordinates:[[[[0,0],[1,0],[1,1],[0,1]]]]}},
    {...plan,blocks:Array.from({length:25},(_,i)=>({...block,id:i.toString(16).padStart(64,'0')}))}]) {
    assert.throws(()=>core.validatePlan(bad));
  }
  const canvas=require('../webapp/frontend/js/lidar-canvas.js');
  const large=canvas.renderSize({getSize:()=>({x:8000,y:4000})});
  assert.ok(large.width*large.height<=2500000);
  assert.equal(large.cssWidth,8000);
  for(const side of [100,10000,10000000]) {
    assert.ok(canvas.viewLevel({getSize:()=>({x:512,y:512})},[0,0,side,side])>=0);
  }
});
