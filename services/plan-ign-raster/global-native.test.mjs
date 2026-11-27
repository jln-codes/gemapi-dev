// B, opt-in: real Native + synthetic MVT/glyph/sprite only, one Map, disposable files.
import assert from 'node:assert/strict';
import test from 'node:test';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { nativeHarness, deferred } from './test-fixtures/global-native.mjs';
import { metatileForTile } from './geometry.mjs';
import { PlanIgnTileService } from './tile-service.mjs';
import { RasterCache } from './raster-cache.mjs';
import { createRequestHandler, listen, closeServer } from './server.mjs';
const enabled=process.env.RUN_CARTOGRAPHIC_NATIVE_TESTS==='1';
const view=metatileForTile(5,17,13);
const hash=b=>createHash('sha256').update(b).digest('hex');
function visible(bytes) {
 assert.equal(bytes.length,2560*2560*4);
 let red=0,green=0,dark=0;
 for(let i=0;i<bytes.length;i+=4){if(bytes[i]>180&&bytes[i+1]<120)red++;if(bytes[i+1]>150&&bytes[i]<80)green++;if(bytes[i]<70&&bytes[i+1]<70&&bytes[i+2]<70)dark++;}
 assert(red>2560*2560*.2);assert(green>0);assert(dark>0);
}
test('global Native recovers failed source acquisition without poisoned warm sources', {skip:!enabled,timeout:30000}, async t=>{
 let fail=true; const h=await nativeHarness(t,{failMvt:()=>fail,timeoutMs:5000});
 await assert.rejects(h.renderer.render(view),/Synthetic MVT acquisition interrupted/);
 fail=false; const first=await h.renderer.render(view);visible(first);
 const before=h.requests.length;const second=await h.renderer.render(view);visible(second);
 assert.equal(hash(first),hash(second));assert.equal(h.requests.length,before);assert(h.renderer.isHealthy());
});
test('one global Native Map; 16 disk HITs bypass a blocked MISS and identical cold requests join', {skip:!enabled,timeout:45000}, async t=>{
 const gate={name:null,entered:deferred(),release:deferred()};const h=await nativeHarness(t,{gate});
 const service=new PlanIgnTileService({cacheRoot:h.root,renderer:h.renderer,
  cache:new RasterCache(h.root,h.renderer.cacheVersion,{store:h.registry.get('ign-epure').tiles}),logger:{log(){},error(){}}});
 const server=http.createServer(createRequestHandler(service));const address=await listen(server,{port:0});
 h.beforeCleanup.push(async()=>{gate.release.resolve();await closeServer(server);await service.close();});
 const request=async xyz=>{const response=await fetch(`http://127.0.0.1:${address.port}/basemap/tiles/${h.renderer.cacheVersion}/${xyz}.webp`,{signal:AbortSignal.timeout(15000)});
  assert.equal(response.status,200);return {bytes:Buffer.from(await response.arrayBuffer()),status:response.headers.get('x-cache')};};
 const first=await request('5/17/13');
 const jobs=await Promise.all([...service.inflight.values()]);await Promise.all(jobs.map(j=>j.completion));
 assert.equal((await request('5/17/13')).status,'HIT');
 gate.name='A';const cold=request('5/25/21');await gate.entered.promise;
 const joined=request('5/25/21');
 try{const hits=await Promise.all(Array.from({length:16},()=>request('5/17/13')));assert(hits.every(r=>r.status==='HIT'&&hash(r.bytes)===hash(first.bytes)));}
 finally{gate.release.resolve();}
 await Promise.all([cold,joined]); assert.equal(h.renderer.stats().renderCount,2);
});
