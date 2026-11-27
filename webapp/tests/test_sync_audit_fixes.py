"""Adversarial regression cases SYNC-AUDIT-005/006, using the actual JS modules."""
import subprocess
import unittest


class SyncAuditFixTest(unittest.TestCase):
    def test_trailing_work_survives_failure_for_both_map_adapters(self):
        subprocess.run(["node", "-e", r'''
const assert = require('node:assert/strict');
const {createRegistry} = require('./webapp/frontend/js/map-feature-store.js');
(async()=>{
 for(const resource of ['desordres','troncons']) {
  for(const [first,second] of [['entity','entity'],['collection','entity'],['entity','collection'],['collection','collection']]) {
   const store=createRegistry(), layers=new Map(); let rejectFirst, calls=0;
   const read=()=>++calls===1?new Promise((_,reject)=>rejectFirst=reject):Promise.resolve({id:'42',version:2});
   store.registerResource(resource,{fetchOne:read,fetchCollection:async()=>({features:[await read()]}),
    getId:x=>x.id,listIds:()=>[...layers.keys()],upsert:x=>layers.set(x.id,x),remove:id=>layers.delete(id)});
   const load=kind=>kind==='entity'?store.reloadMapEntity(resource,'42'):store.reloadMapCollection(resource);
   const a=load(first); const b=load(second);
   rejectFirst(new Error('old request failed'));
   const results=await Promise.allSettled([a,b]);
   assert.equal(results[1].status,'fulfilled');
   assert.equal(layers.get('42').version,2); assert.equal(calls,2);
  }
  // Include reloads arriving in the microtask gap around worker settlement.
  for(let gap=0;gap<8;gap++) {
   const store=createRegistry();let rejectFirst,calls=0,applied=0;
   store.registerResource(resource,{fetchOne:()=>++calls===1?new Promise((_,reject)=>rejectFirst=reject):Promise.resolve({id:'42'}),
    fetchCollection:async()=>({features:[]}),getId:x=>x.id,listIds:()=>[],upsert:()=>applied++,remove:()=>{}});
   const a=store.reloadMapEntity(resource,'42').catch(()=>{});
   rejectFirst(Error('failed'));
   for(let i=0;i<gap;i++)await Promise.resolve();
   const b=store.reloadMapEntity(resource,'42');
   await Promise.all([a,b]);assert.equal(calls,2);assert.equal(applied,1);
  }
 }
})().catch(e=>{console.error(e);process.exitCode=1});
'''], check=True)

    def test_failed_local_work_retries_without_republishing_or_spinning(self):
        subprocess.run(["node", "-e", r'''
const assert=require('node:assert/strict');
const M=require('./webapp/frontend/js/resource-mutation.js');
const W=require('./webapp/frontend/js/map-resource-invalidators.js');
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const m=M.createController({localInstanceId:'B',localClientId:'client',bus:{subscribe:()=>()=>{},publish:()=>{throw Error('loop')}}});
 let fail=true, calls=0, version=1, displayed=0, details=0;
 W.register({mutationController:m,onError:()=>{},consumers:{
  'map-feature-store':async()=>{calls++;if(fail)throw Error('500');displayed=version},
  'open-detail':async()=>{details++},
 }});
 const event=id=>M.createMutationPayload({resource:'desordres',action:'update',ids:['42'],eventId:id},
   {localInstanceId:'A',localClientId:'client'});
 const e=event('E1');m.handleRemoteMutation(e);await tick();
 assert.equal(calls,1);assert.equal(details,1);assert.equal(displayed,0);
 await tick();await tick();assert.equal(calls,1,'no automatic spin');
 assert.equal(m.handleRemoteMutation(e).reason,'duplicate-event');await tick();
 assert.equal(calls,2);assert.equal(details,1,'successful consumer not retried');
 fail=false;version=2;m.handleRemoteMutation(event('E2'));await tick();
 assert.equal(displayed,2);const count=calls;
 m.handleRemoteMutation(e);await tick();assert.equal(calls,count,'old event cannot restore old state');
 // A duplicate arriving before failure is known must not lose recovery either.
 let release, lateCalls=0;fail=true;
 const remove=m.registerInvalidator('relation-options',()=>{
   lateCalls++;
   return lateCalls===1 ? new Promise((_,rej)=>{release=()=>rej(Error('late'))}) : Promise.resolve();
 });
 const t=M.createMutationPayload({resource:'troncons',action:'update',ids:['T'],eventId:'T'},
  {localInstanceId:'A',localClientId:'client'});
 m.handleRemoteMutation(t);m.handleRemoteMutation(t);release();await tick();
 assert.equal(lateCalls,2,'duplicate during flight must recover the later failure');
 await tick();assert.equal(lateCalls,2,'successful recovery must not loop');
 remove();m.close();
})().catch(e=>{console.error(e);process.exitCode=1});
'''], check=True)
