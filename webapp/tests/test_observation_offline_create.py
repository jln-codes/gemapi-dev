"""Create replay contracts, independent of PostgreSQL/browser prerequisites."""
import subprocess
import unittest


class ObservationOfflineCreateTest(unittest.TestCase):
    def test_replay_receipt_integration_errors_and_retry_identity(self):
        subprocess.run(["node", "-e", r'''
const assert = require('node:assert/strict');
const {createEngine} = require('./webapp/frontend/js/offline-operations.js');
const {canonicalCreate} = require('./webapp/frontend/js/local-projection.js');
(async () => {
  const parent = {resource:'troncons',id:crypto.randomUUID()}, id=crypto.randomUUID();
  const changes=canonicalCreate({id,parent,designation:' terrain '});
  assert.equal(changes.designation,'terrain'); assert.equal(changes.valid,true);
  for (const extra of [{date:'2026-02-30'},{nombre_desordres:Infinity},{valid:1},{urgence_id:'u'}, {id:'bad'}, {parent:{resource:'photos',id}}]) {
    assert.throws(()=>canonicalCreate({...changes,...extra}));
  }
  let op, row, cursor='0', network=true, lose=false, integrate=true, reply=201, receipt, calls=[], events=0;
  const store={
    inspect:async()=>({scope:{cursor}}), read:async()=>row,
    listOperations:async()=>op?[structuredClone(op)]:[],
    enqueueCreate:async value=>(op={...value,resource:'observations',resourceId:id,action:'create',status:'pending',attempts:0}),
    claimOperation:async()=>{op={...op,status:'syncing',attempts:op.attempts+1};return structuredClone(op)},
    updateOperation:async(_,patch)=>{Object.assign(op,patch);return op},
    removeOperation:async()=>{assert.equal(op.status,'confirmed');assert.ok(row);op=null},
  };
  const options={store,headers:async()=>({}),locks:{request:async(_n,_o,work)=>work({})},
    catchUp:async()=>{if(!network)throw new TypeError('offline');return cursor},
    recoverPinned:async()=>{if(!integrate)throw new Error('not integrated');row={serverVersion:1,payload:{parent}}},
    publish:async()=>{},fetchImpl:async(url,request)=>{
      assert.equal(url,'/api/observations');assert.equal(request.method,'POST');assert.equal(request.headers['If-Match'],undefined);
      assert.ok(op.attempts>0); calls.push({operationId:request.headers['Operation-Id'],body:request.body});
      if(reply!==201)return {ok:false,status:reply,json:async()=>({detail:{code:reply===409?'operation-id-collision':'observation-parent-not-found'}})};
      if(!receipt){events++;receipt={id,parent,version:1,mutation:{eventId:crypto.randomUUID(),resource:'observations',action:'create',ids:[id],versions:{[id]:1}}};}
      if(lose){lose=false;throw new TypeError('commit then lost response')}
      return {ok:true,json:async()=>receipt};
    }};
  let engine=createEngine(options);
  network=false;
  await engine.submitCreate({operationId:crypto.randomUUID(),changes});
  assert.equal(op.attempts,0);assert.equal(op.status,'pending');assert.equal(calls.length,0);
  network=true;lose=true;
  await engine.synchronize(true);assert.equal(op.attempts,1);assert.equal(op.status,'pending');
  integrate=false;engine=createEngine(options);await engine.synchronize(true);
  assert.equal(op.status,'confirmed');assert.equal(events,1);assert.deepEqual(calls[0],calls[1]);
  assert.equal(op.response.mutation.eventId,receipt.mutation.eventId);
  integrate=true;await engine.synchronize(true);assert.equal(op,null);assert.equal(calls.length,2);
  for(const [status,state] of [[409,'conflict'],[422,'failed'],[503,'pending']]){
    reply=status;await engine.submitCreate({operationId:crypto.randomUUID(),changes});
    assert.equal(op.status,state);assert.equal(op.attempts,1);assert.deepEqual(op.changes,changes);
    const before=calls.length;
    if(status!==503){await engine.synchronize(true);assert.equal(calls.length,before)}
    else {reply=201;row=null;await engine.synchronize(true);assert.equal(op,null);assert.deepEqual(calls.at(-1),calls.at(-2))}
  }
})().catch(error=>{console.error(error);process.exitCode=1});
'''], check=True)
