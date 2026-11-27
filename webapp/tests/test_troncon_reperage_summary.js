// A: presentation of the authoritative C4 read calculation (~1 s), no writes.
// Mutable DOM ports and delayed responses are isolated per scenario.
const test=require('node:test'),assert=require('node:assert/strict');
require('../frontend/js/troncon-reperage.js');
const R=globalThis.TronconReperage;
const feature=(p={})=>({properties:{id:'tronc',version:7,geometry_type:'LINESTRING',...p}});
const port=()=>({textContent:''});
test('metric display rounds without altering the authoritative result',()=>{
 assert.equal(R.formatMetres(1000.123456),'1\u202f000,12');assert.equal(R.formatMetres(.001),'< 0,01');
 for(const v of [null,undefined,'',NaN,Infinity])assert.equal(R.formatMetres(v),'indisponible');
});
test('a standard line uses only the read calculation and its observed version',async()=>{
 const container=port(),calls=[],result={version:7,longueur_m:1000.123456};
 const s=R.createSummary({container,feature:feature(),fetchJson:async(path,options)=>{calls.push({path,options});return result;}});
 await s.load();assert.equal(container.textContent,'Repérage automatique — 0 à 1\u202f000,12 m');
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/troncons/tronc/reperage/calcul');
 assert.deepEqual(JSON.parse(calls[0].options.body),{mode:'abscisse',abscisse_m:0});assert.equal(result.longueur_m,1000.123456);
});
test('unsupported, absent and explicit geometries never invent automatic endpoints',async()=>{
 for(const p of [{geometry_type:'POLYGON'},{geometry_type:null},{geometry_reperage_disponible:true}]){
  let calls=0;const container=port();await R.createSummary({container,feature:feature(p),fetchJson:async()=>{calls++;}}).load();
  assert.equal(calls,0);assert.match(container.textContent,p.geometry_reperage_disponible?/spécifique conservé/:/indisponible/);
 }
});
test('late and superseded calculations cannot overwrite a changed form',async()=>{
 let release;const container=port();const s=R.createSummary({container,feature:feature(),fetchJson:()=>new Promise(r=>release=r)});
 const loading=s.load();s.dispose();container.textContent='Nouveau tracé non enregistré';release({version:7,longueur_m:12});await loading;
 assert.equal(container.textContent,'Nouveau tracé non enregistré');
 await R.createSummary({container,feature:feature(),fetchJson:async()=>({version:8,longueur_m:25})}).load();
 assert.match(container.textContent,/actualisé ailleurs/);
});
test('unusable geometry and failed transport produce distinct states',async()=>{
 for(const status of [422,503]){
  const container=port();await R.createSummary({container,feature:feature(),fetchJson:async()=>{throw {status};}}).load();
  assert.match(container.textContent,status===422?/cette géométrie conservée/:/momentanément indisponible/);
 }
});

test('an older calculation cannot replace a newer version warning after a mode detour',async()=>{
 const container=port(),pending=[];const s=R.createSummary({container,feature:feature(),fetchJson:()=>new Promise(resolve=>pending.push(resolve))});
 const first=s.load(),second=s.load();pending[1]({version:8,longueur_m:2000});await second;
 const warning=container.textContent;assert.match(warning,/actualisé ailleurs/);
 pending[0]({version:7,longueur_m:1000});await first;assert.equal(container.textContent,warning);s.dispose();
});
