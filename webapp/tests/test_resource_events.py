"""Behavioral contracts for transport-neutral dispatch and browser identity."""
import subprocess
import unittest


class ResourceEventsTest(unittest.TestCase):
    def test_identity_dispatch_transports_and_bounded_deduplication(self):
        subprocess.run(["node", "-e", r'''
const assert = require("node:assert/strict");
const E = require("./webapp/frontend/js/resource-events.js");
const M = require("./webapp/frontend/js/resource-mutation.js");
const I = require("./webapp/frontend/js/resource-invalidation.js");
(async () => {
  const persisted = new Map();
  const storage = {getItem:k=>persisted.get(k), setItem:(k,v)=>persisted.set(k,v)};
  let lock = Promise.resolve();
  const locks = {request:(_name, fn)=>{lock=lock.then(fn);return lock;}};
  const identities = await Promise.all(Array.from({length:4}, () => E.createClientIdentity(storage,locks).getId()));
  assert.equal(new Set(identities).size, 1);
  assert.match(identities[0], /^[0-9a-f-]{36}$/);
  assert.equal(await E.createClientIdentity(storage,locks).getId(), identities[0]);
  assert.equal(E.CLIENT_KEY, 'digues-app:client-id');
  assert.equal(persisted.get('digues-app:client-id'), identities[0]);
  assert.equal(persisted.has('sirs:client-id'), false);
  const event = M.createMutationPayload({resource:"desordres",action:"update",ids:["D42"],versions:{D42:18}},
    {localClientId:identities[0],localInstanceId:"a"});
  assert.match(event.eventId,/^[0-9a-f-]{36}$/);
  assert.equal(I.isValidMutationPayload(event),true);
  for(const changes of [{eventId:""},{originClientId:""},{versions:{D42:0}},
    {versions:{OTHER:18}},{ids:null,versions:{D42:18}}]) {
    assert.equal(I.isValidMutationPayload({...event,...changes}),false);
  }
  let clock=0;const calls=[];
  const dispatcher=E.createDispatcher({consume:e=>calls.push(e),now:()=>clock,ttlMs:100,maxSize:2});
  dispatcher.dispatch(event);dispatcher.dispatch(structuredClone(event));
  assert.equal(calls.length,1);
  dispatcher.dispatch({...event,eventId:"e2"});dispatcher.dispatch({...event,eventId:"e3"});
  assert.equal(calls.length,3);assert.equal(dispatcher.size(),2);
  dispatcher.dispatch(event);assert.equal(calls.length,4); // oldest evicted by capacity
  clock=101;assert.equal(dispatcher.size(),0);
  dispatcher.dispatch(event);assert.equal(calls.length,5);

  const transports=[];const received=[[],[],[]];const sent=[];
  const instances=received.map((list,index)=>{
    const controller=M.createController({localInstanceId:`instance-${index}`,localClientId:identities[0],
      transportFactory:receive=>{transports.push(receive);return {send:e=>{
        sent.push(e);transports.forEach(fn=>fn(structuredClone(e)));
      }};}});
    controller.registerInvalidator("open-detail",e=>list.push(e));return controller;
  });
  const receipt={...event, eventId:E.createId()};
  const published=await instances[0].publishMutation(receipt);
  assert.equal(published.eventId,receipt.eventId);
  assert.equal(received[0].length,0);assert.equal(received[1].length,1);assert.equal(received[2].length,1);
  // A second transport delivers the SAME server event, not a new publication.
  instances.forEach(c=>c.dispatch(published));
  assert.deepEqual(received.map(a=>a.length),[0,1,1]);
  await instances[0].publishMutation(receipt);assert.equal(sent.length,1);
  instances[1].dispatch({...published,eventId:E.createId()});assert.equal(received[1].length,2);
  assert.equal(sent.length,1);
  assert.notEqual(instances[0].localInstanceId,instances[1].localInstanceId);
  instances.forEach(c=>c.close());
})().catch(error=>{console.error(error);process.exitCode=1;});
'''], check=True)
