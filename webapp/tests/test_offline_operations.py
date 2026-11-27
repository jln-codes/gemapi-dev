import subprocess
import unittest


class OfflineOperationsEngineTest(unittest.TestCase):
    def test_replay_unknown_result_conflicts_validation_backoff_and_no_lost_intentions(self):
        subprocess.run(["node", "-e", r'''
const assert = require('node:assert/strict');
const {createEngine, supported} = require('./webapp/frontend/js/offline-operations.js');
(async () => {
  for (const resource of ['photos','territoires-administratifs','meteo','vigicrues','systemes']) assert.equal(supported(resource,{designation:'x'}),false);
  assert.equal(supported('desordres',{troncon_ids:['T1']}),false);
  assert.equal(supported('observations',{suite:'terrain'}),true);
  const operations = new Map(), receipts = new Map(), calls=[], order=[], versions = new Map();
  let sequence=0, cursor=0, clock=0, network=true, loseSecond=true, catchups=0, broadcasts=0;
  const store = {
    inspect: async()=>({scope:{cursor:String(cursor)}}),
    enqueue: async intention=>{
      const operation={...intention,operationId:`O${++sequence}`,sequence,status:'pending',attempts:0};
      operations.set(operation.operationId,operation); return operation;
    },
    listOperations:async()=>structuredClone([...operations.values()]),
    claimOperation:async id=>{
      const old=operations.get(id);
      if(!old||!['pending','syncing'].includes(old.status)) return null;
      const next={...old,status:'syncing',attempts:old.attempts+1};
      operations.set(id,next);return structuredClone(next);
    },
    updateOperation:async(id,patch)=>operations.set(id,{...operations.get(id),...patch}),
    removeOperation:async id=>{assert.equal(operations.get(id).status,'confirmed'); operations.delete(id)},
  };
  const options = {
    store, now:()=>clock,
    locks:{request:async(name,options,work)=>work({name})},
    headers:async()=>({}),
    catchUp:async()=>{catchups++;order.push('catchup'); if(!network) throw new TypeError('offline');return String(cursor)},
    publish:async()=>{broadcasts++},
    fetchImpl:async(url,request)=>{
      const id=request.headers['Operation-Id'];calls.push(id);order.push('put');
      if(receipts.has(id)) return {ok:true,json:async()=>receipts.get(id)};
      const operation=operations.get(id);
      if(operation.changes.suite==='html-error') return {ok:false,status:422,json:async()=>{throw new SyntaxError('HTML body')}};
      if(operation.changes.suite==='invalid') return {ok:false,status:422,json:async()=>({detail:'invalid'})};
      if(operation.changes.suite==='conflict') return {ok:false,status:409,json:async()=>({detail:{code:'version-conflict',message:'concurrent'}})};
      const version=(versions.get(operation.resourceId)||1)+1; versions.set(operation.resourceId,version);cursor++;
      const response={id:operation.resourceId,version,mutation:{eventId:`E${cursor}`,resource:operation.resource,ids:[operation.resourceId],versions:{[operation.resourceId]:version}}};
      receipts.set(id,response);
      if(id==='O2'&&loseSecond){loseSecond=false;network=false;throw new TypeError('response lost after commit')}
      return {ok:true,json:async()=>response};
    },
  };
  let engine=createEngine(options);
  const intent=(id,suite='terrain')=>({resource:'observations',resourceId:id,baseVersion:1,changes:{suite}});
  await store.enqueue(intent('A'));await store.enqueue(intent('B'));await store.enqueue(intent('C'));
  await engine.synchronize(true);
  assert.deepEqual(calls,['O1','O2']); assert.equal(operations.has('O1'),false);
  assert.equal(operations.get('O2').status,'pending');assert.equal(operations.get('O3').status,'pending');
  const before=catchups;await engine.synchronize();assert.equal(catchups,before); // bounded backoff
  // Simulate a dead worker after durable syncing; reconstruct engine, not operations.
  await store.updateOperation('O2',{status:'syncing'});network=true;engine=createEngine(options);
  await engine.synchronize(true);
  assert.deepEqual(calls,['O1','O2','O2','O3']);assert.equal(operations.size,0);
  assert.deepEqual([...versions.values()],[2,2,2]);assert.equal(receipts.size,3);assert.equal(broadcasts,3);
  assert.equal(order[0],'catchup');
  const idleCatchups=catchups;
  await engine.synchronize();assert.equal(catchups,idleCatchups,'idle startup leaves bootstrap to the transport');
  await engine.synchronize(true);assert.equal(catchups,idleCatchups+1,'manual recovery works without a queue');
  await store.enqueue(intent('D','conflict'));await store.enqueue(intent('E','invalid'));
  await engine.synchronize(true);
  assert.equal(operations.get('O4').status,'conflict');assert.equal(operations.get('O5').status,'failed');
  assert.equal(operations.get('O4').changes.suite,'conflict');
  const savedCalls=calls.length;await engine.synchronize(true);assert.equal(calls.length,savedCalls);
  await store.enqueue(intent('HTML','html-error'));await engine.synchronize(true);
  assert.equal(operations.get('O6').status,'failed');
  const unavailable=createEngine({...options,locks:null});
  await assert.rejects(unavailable.submit(intent('F')),/Web Locks/);
  assert.equal(receipts.size,3);
  let releaseLock;
  const held=createEngine({...options,locks:{request:(_name,_options,work)=>new Promise(resolve=>{
    releaseLock=()=>resolve(work({}));
  })}});
  const first=held.synchronize(true), second=held.synchronize(true);
  assert.strictEqual(first,second,'concurrent replay wakeups share the in-flight promise');
  releaseLock();await first;
})().catch(error=>{console.error(error);process.exitCode=1});
'''], check=True)
