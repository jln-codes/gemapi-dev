import subprocess
import unittest


class ServerTransportTest(unittest.TestCase):
    def test_maintenance_closes_stream_and_bounds_reconnect_without_cursor_advance(self):
        # A: deterministic clock; no network proof claimed here.
        subprocess.run(["node", "-e", r'''
const assert=require('node:assert/strict');
const T=require('./webapp/frontend/js/resource-server-transport.js');
(async()=>{
  const sources=[], waits=[], errors=[];
  let maintaining=false;
  class SSE {constructor(){this.listeners={};sources.push(this)}
    addEventListener(name,fn){this.listeners[name]=fn} close(){this.closed=true}}
  const transport=T.createTransport({receive(){},EventSourceImpl:SSE,
    fetchImpl:async url=>maintaining
      ? {ok:false,status:503,json:async()=>({code:'BUSINESS_DATABASE_MAINTENANCE',detail:'Maintenance contrôlée'})}
      : {ok:true,json:async()=>url.endsWith('/head')?{cursor:'7'}:{events:[],nextCursor:'7',hasMore:false}},
    delay:ms=>new Promise(resolve=>waits.push({ms,resolve})),onError:e=>errors.push(e.message)});
  const until=async f=>{for(let n=0;n<200&&!f();n++)await new Promise(r=>setImmediate(r));assert.ok(f())};
  await transport.bootstrap;transport.start();await until(()=>sources.length===1);
  maintaining=true;sources[0].listeners.maintenance();
  for(const [n,ms] of [2000,4000,8000,16000,30000,30000,30000].entries()){
    await until(()=>waits.length===n+1);
    assert.ok(sources[0].closed);assert.equal(transport.getCursor(),'7');
    assert.equal(waits[n].ms,ms);
    sources[0].onerror();assert.equal(waits.length,n+1,'No duplicate timer from onerror');
    transport.wake();assert.equal(sources.length,1,'Admin success must not bypass maintenance backoff');
    assert.equal(waits.length,n+1);
    if(n<6)waits[n].resolve();
  }
  assert.equal(errors.length,7);assert.equal(errors.at(-1),'Maintenance contrôlée');
  maintaining=false;waits[6].resolve();await until(()=>sources.length===2);
  assert.equal(transport.getCursor(),'7');
  sources[1].onerror();assert.equal(waits[7].ms,2000,'Healthy journal reads restore normal rotation delay');
  transport.wake();assert.equal(sources.length,2,'A pending network retry also keeps its single timer');
  assert.equal(waits.length,8);
  transport.stop();assert.ok(sources.at(-1).closed);
})().catch(e=>{console.error(e);process.exitCode=1});
'''],check=True, timeout=120)

    def test_reconnection_during_barrier_retries_but_stop_never_authorizes_replay(self):
        subprocess.run(["node", "-e", r'''
const assert = require('node:assert/strict');
const T = require('./webapp/frontend/js/resource-server-transport.js');
(async () => {
  let transport, action, requests = 0, sources = 0;
  class SSE { constructor(){sources++} addEventListener(){} close(){} }
  transport = T.createTransport({receive(){}, EventSourceImpl:SSE, onError(){},
    fetchImpl:async url=>{
      if(url.endsWith('/head'))return {ok:true,json:async()=>({cursor:'0'})};
      requests++;
      const current=action; action=null; current?.();
      return {ok:true,json:async()=>({events:[],nextCursor:'0',hasMore:false})};
    }});
  await transport.bootstrap;
  transport.start();
  for(let i=0;i<100 && !sources;i++)await new Promise(r=>setImmediate(r));
  assert.equal(sources,1);
  const before=requests;
  action=()=>transport.wake();
  assert.equal(await transport.synchronize(),'0');
  assert.ok(requests>=before+2,'The new generation must actually catch up');
  action=()=>transport.stop();
  await assert.rejects(transport.synchronize(), /Reconnexion en cours/);
  assert.equal(transport.getCursor(),'0');
})().catch(e=>{console.error(e);process.exitCode=1});
'''], check=True, timeout=120)

    def test_projection_restarts_at_integrated_cursor_and_retries_failed_commit(self):
        subprocess.run(["node", "-e", r'''
const assert = require('node:assert/strict');
const T = require('./webapp/frontend/js/resource-server-transport.js');
const M = require('./webapp/frontend/js/resource-mutation.js');
(async () => {
  const tick=()=>new Promise(r=>setImmediate(r));
  const until=async f=>{for(let n=0;n<100&&!f();n++)await tick();assert.ok(f())};
  const controller=M.createController({localInstanceId:'target',localClientId:'client',
    bus:{subscribe(){return ()=>{}},publish(){throw Error('Unexpected relay')}}});
  const event=M.createMutationPayload({resource:'desordres',action:'update',ids:['D42'],eventId:'durable'},
    {localInstanceId:'source',localClientId:'client'});
  let invalidations=0, commits=0, durable='10', errors=[];
  controller.registerInvalidator('open-detail',()=>invalidations++);
  const sources=[];
  class SSE {constructor(){sources.push(this)} addEventListener(){} close(){}}
  let transport;
  const projection={initialCursor:async()=>durable,networkError:e=>e instanceof TypeError,
    integrate:async entry=>{
      commits++;
      if(commits===1){assert.equal(transport.getCursor(),'10');throw Error('Network before IDB commit')}
      durable=entry.cursor;
    }};
  transport=T.createTransport({receive:controller.handleRemoteMutation,projection,
    EventSourceImpl:SSE,delay:tick,onError:e=>errors.push(e.message),fetchImpl:async url=>{
      assert.ok(!url.endsWith('/head'),'Never skip persisted cursor with a later head');
      const after=new URL(url,'http://local').searchParams.get('after');
      return {ok:true,json:async()=>({events:after==='10'?[{cursor:'11',event}]:[],
        nextCursor:'11',hasMore:false})};
    }});
  assert.equal(await transport.bootstrap,'10');
  transport.start();await until(()=>sources.length===1);
  assert.equal(commits,2);assert.equal(invalidations,1);assert.equal(durable,'11');
  assert.equal(transport.getCursor(),'11');assert.equal(errors.length,1);
  transport.stop();controller.close();
  let online=false;
  const offline=T.createTransport({receive(){},projection:{initialCursor:async()=>null,networkError:e=>e instanceof TypeError},
    onError(){},fetchImpl:async()=>{
      if(!online)throw new TypeError('No network');
      return {ok:true,json:async()=>({cursor:'50'})};
    }});
  assert.equal(await offline.bootstrap,null,'Offline startup must not hang on head');
  online=true;
  assert.equal(await offline.ensureReadBaseline(),'50','New online reads must capture a head before state');
  assert.equal(offline.getCursor(),'50');
  offline.stop();
})().catch(e=>{console.error(e);process.exitCode=1});
'''], check=True, timeout=120)

    def test_bootstrap_replay_reconnect_dedup_and_no_relay(self):
        subprocess.run(["node", "-e", r'''
const assert = require('node:assert/strict');
const T = require('./webapp/frontend/js/resource-server-transport.js');
const M = require('./webapp/frontend/js/resource-mutation.js');
(async () => {
  const messages=[], invalidations=[], sources=[], urls=[], errors=[];
  const controller=M.createController({localInstanceId:'b',localClientId:'same-client',
    bus:{subscribe(){return ()=>{}},publish(...e){messages.push(e)}}});
  controller.registerInvalidator('open-detail', e=>invalidations.push(e.eventId));
  const make=(id)=>M.createMutationPayload({resource:'desordres',action:'update',ids:['D42'],eventId:id},
    {localInstanceId:'a',localClientId:'same-client'});
  const e1=make('e1'),e2=make('e2'),e3=make('e3');
  const log=[{cursor:'11',event:e1},{cursor:'12',event:e2}];
  class SSE {
    constructor(url){this.url=url;sources.push(this)}
    addEventListener(type,fn){if(type==='maintenance'){this.maintenance=fn;return}assert.equal(type,'resource-mutated');this.receive=fn}
    close(){this.closed=true}
    emit(cursor,event){this.receive({lastEventId:cursor,data:JSON.stringify(event)})}
  }
  const tick=()=>new Promise(r=>setImmediate(r));
  const until=async f=>{for(let n=0;n<100&&!f();n++)await tick();assert.ok(f())};
  let ready;const stateReady=new Promise(r=>ready=r);
  const transport=T.createTransport({receive:controller.handleRemoteMutation,EventSourceImpl:SSE,
    delay:tick,onError:e=>errors.push(e.message),fetchImpl:async url=>{
      urls.push(url);
      if(url.endsWith('/head'))return {ok:true,json:async()=>({cursor:'10'})};
      const after=new URL(url,'http://local').searchParams.get('after');
      const rows=log.filter(e=>BigInt(e.cursor)>BigInt(after));
      const entries=rows.slice(0,1); // force several pages
      return {ok:true,json:async()=>({events:entries,nextCursor:entries.at(-1)?.cursor||after,hasMore:rows.length>1})};
    }});
  assert.equal(await transport.bootstrap,'10');
  transport.start(stateReady);await tick();assert.equal(sources.length,0);
  // BC first, then the same authoritative event during HTTP-state bootstrap.
  controller.handleRemoteMutation(e1);
  ready();await until(()=>sources.length===1);
  assert.deepEqual(invalidations,['e1','e2']);
  assert.ok(sources[0].url.endsWith('after=12'));
  sources[0].emit('13',e3);await until(()=>transport.getCursor()==='13');
  sources[0].emit('14',e3);await until(()=>transport.getCursor()==='14');
  assert.deepEqual(invalidations,['e1','e2','e3']);
  // A confirmed backend reconnection accelerates the same REST/SSE path.
  log.push({cursor:'15',event:make('e4')});
  transport.wake();transport.wake();await until(()=>sources.length===2);
  assert.ok(sources[0].closed);assert.equal(transport.getCursor(),'15');
  assert.deepEqual(invalidations,['e1','e2','e3','e4']);
  // The transport remains autonomous when no monitor notification is received.
  log.push({cursor:'16',event:make('e5')});
  sources[1].onerror();await until(()=>sources.length===3);
  assert.equal(transport.getCursor(),'16');
  assert.deepEqual(invalidations,['e1','e2','e3','e4','e5']);
  sources[2].emit('17',{bad:true});await until(()=>errors.length===1);
  assert.equal(transport.getCursor(),'16');
  transport.stop();await tick();assert.ok(sources[2].closed);
  assert.deepEqual(messages,[]);
  controller.close();
})().catch(e=>{console.error(e);process.exitCode=1});
'''], check=True, timeout=120)
