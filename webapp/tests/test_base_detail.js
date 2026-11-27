/* Niveau A, <5 s : présentation/lecture/navigation Base, sans SQL ni stockage.
 * Chaque scénario possède runtime, requêtes et ports isolés. Les permissions
 * réelles, brouillons d'éditeurs et l'isolation serveur sont qualifiés au navigateur.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const Registry = require('../frontend/js/base-detail-registry.js');
const Repository = require('../frontend/js/base-detail-repository.js');
const View = require('../frontend/js/base-detail-view.js');
const Controller = require('../frontend/js/base-detail-controller.js');
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const payload = (key, values = {}) => ({ id: key, version: 1, designation: `Objet ${key}`, valid: true, ...values });
test('16 familles : définitions de lecture fermées, identité, ordre de sections et relations structurées', () => {
  assert.equal(Object.keys(Registry.definitions).length, 16);
  for (const [resource, definition] of Object.entries(Registry.definitions)) {
    assert.equal(definition.resource, resource);
    assert.equal(new Set(definition.fields.map(field => field.key)).size, definition.fields.length);
    assert.ok(definition.fields.some(field => field.key === 'designation'));
    for (const field of definition.fields) {
      assert.ok(Registry.SECTIONS.some(section => section.key === field.section));
      assert.ok(field.format && field.importance && field.applicability);
      assert.equal('editable' in field, false);
      if (field.format === 'relations') assert.ok(field.source);
    }
    assert.ok(Object.isFrozen(definition));
  }
  assert.equal(Registry.get('desordres').map, false);
  assert.equal(Registry.get('observations').observations, null); // BASE-FICHES-03, not a fabricated follow-up.
  assert.throws(() => Registry.get('photos'), /inconnue/);
});
test('les valeurs historiques hors profil survivent, y compris zéro et faux ; les valeurs vides atypiques restent non applicables', () => {
  const fields = Registry.fields('troncons', { type_physique_id:'BERGE', num_couche:0, hauteur_murette:12.75, fonction_bas_id:'inactive', epaisseur:null });
  for (const key of ['num_couche','hauteur_murette','fonction_bas_id']) assert.equal(fields.find(field => field.key === key).section, 'historical');
  assert.equal(fields.some(field => field.key === 'epaisseur'), false);
  const vegetation = Registry.fields('vegetation', { famille_vegetation:'HERBACEES', sous_type:'SOUCHE' });
  assert.equal(vegetation.find(field => field.key==='sous_type').section,'historical');
});
test('absent, non renseigné, vide, faux et référence non résolue ne se confondent pas ; décimaux sans perte de précision', () => {
  const detail = Repository.normalize('systemes', payload(id(1),{libelle:'',commentaire:null,valid:false}));
  const fields=Registry.get('systemes').fields;
  const value=key=>Repository.value(detail, fields.find(field=>field.key===key));
  assert.equal(value('population_protegee').state,'notLoaded');
  assert.equal(value('commentaire').state,'unset');
  assert.equal(value('libelle').state,'empty');
  assert.equal(View.format(fields.find(field=>field.key==='valid'),value('valid')),'Non');
  assert.equal(View.format({format:'number',unit:'m'},{state:'value',value:'12345678901234567890.123456789'}),'12\u202f345\u202f678\u202f901\u202f234\u202f567\u202f890,123456789 m');
  const disorder=Repository.normalize('desordres',{type:'Feature',geometry:null,properties:payload(id(2),{cote_id:'inactive'})});
  assert.equal(Repository.value(disorder,Registry.get('desordres').fields.find(field=>field.key==='cote_id')).state,'unresolved');
});
test('lecture des 16 familles : les enrichissements manquants ne bloquent pas le détail ni ne créent de géométrie', async () => {
  for (const resource of Object.keys(Registry.definitions)) {
    const repo=Repository.create({referenceData:null,request:async url=>{
      if(url.endsWith('/'+id(1))) return {type:'Feature',geometry:null,properties:payload(id(1),{type_physique_id:'BERGE'})};
      throw new Error('Complément indisponible');
    }});
    const detail=await repo.read(resource,id(1));
    assert.equal(detail.id,id(1));assert.equal(detail.payload.geometry,null);
    assert.equal(detail.values.localisation,'Géométrie non renseignée');
  }
});
test('Désordre : hiérarchie issue des IDs autoritaires, libellés et déduplication par identité', async () => {
  const repo=Repository.create({referenceData:null,request:async url=>{
    if(url.startsWith('/api/desordres/'))return {type:'Feature',geometry:null,properties:payload(id(1),{troncon_ids:[id(2),id(3)],zones_stockage:[]})};
    return {type:'Feature',geometry:null,properties:payload(url.endsWith(id(2))?id(2):id(3),{digue_id:id(4),digue_libelle:'Digue commune',systeme_endiguement_id:id(5),systeme_endiguement_libelle:'Système commun'})};
  }});
  const detail=await repo.read('desordres',id(1));
  assert.equal(detail.relations.troncon_ids.items.length,2);
  assert.deepEqual(detail.relations.digues.items.map(row=>row.label),['Digue commune']);
  assert.equal(detail.relations.systemes.items[0].resource,'systemes');
  assert.equal(detail.relations.zones_stockage_labels.state,'empty');
});
test('une réponse incohérente et un changement de contexte pendant les enrichissements sont refusés', async () => {
  let release;
  global.BusinessRuntime={can:()=>true,isActive:()=>true};
  const original=global.BusinessRuntime;
  const repo=Repository.create({referenceData:null,request:async url=>url.endsWith(id(1))?payload(id(1)):new Promise(resolve=>{release=resolve})});
  const read=repo.read('systemes',id(1));
  await new Promise(resolve=>setImmediate(resolve));
  global.BusinessRuntime={...original};release({systemes:[]});
  await assert.rejects(read,/contexte/);
  await assert.rejects(Repository.create({request:async()=>payload(id(2))}).read('systemes',id(1)),/Identifiant/);
  delete global.BusinessRuntime;
});
test('la passerelle de requêtes conserve les erreurs métier structurées et les conflits distincts de version', async () => {
  const detail={code:'disorder-already-associated',message:'Ce Désordre appartient à un autre aménagement.'};
  global.BusinessRuntime={isActive:()=>true,fetch:async()=>({ok:false,status:409,json:async()=>({detail})})};
  try {
    await assert.rejects(Repository.fetchJson('/api/amenagements-hydrauliques/x',{method:'PUT'}),error=>{
      assert.equal(error.status,409);assert.equal(error.code,detail.code);assert.equal(error.detail,detail);
      assert.equal(error.message,detail.message);
      assert.equal(require('../frontend/js/resource-concurrency.js').isVersionConflict(error),false);return true;
    });
  } finally { delete global.BusinessRuntime; }
});

class Port {
  constructor(){this.hidden=false;this.textContent='';this.dataset={};this.scrollTop=0;this.listeners={};}
  replaceChildren(){} append(){} setAttribute(){} focus(){this.focused=true;}
  addEventListener(name,handler){this.listeners[name]=handler;}
}
function harness({read,can=()=>true}={}) {
  global.BusinessRuntime={can,isActive:()=>true};
  global.EntityNavigation=require('../frontend/js/entity-navigation.js');
  const panel=new Port();panel.hidden=true;panel.ownerDocument={activeElement:{isConnected:true,focus(){this.restored=true}}};
  const eventTarget=new Port(),closed=[],ready=[];
  const view={};for(const key of ['title','scroll','grid','extensions','followup','followupActions','observationList','status','error','back','edit','editorHost'])view[key]=new Port();
  view.render=detail=>{view.rendered=detail};view.sync=state=>{view.state=state};view.button=()=>new Port();
  const editor={state:{},blocked:false,dirty:false,allowed:true,close(){return !this.blocked&&(!this.dirty||this.allowed)},getState(){return this.state},hasDraft(){return this.dirty},isBlocked(){return this.blocked},async open(){this.state={editing:true};return true},async invalidate(){this.state.stale=true}};
  const controller=Controller.createController({panel,eventTarget,rootElement:{querySelector:()=>null},createView:()=>view,createEditor:()=>editor,
    repository:{read:read|| (async(resource,key)=>Repository.normalize(resource,payload(key)))},
    onClose:context=>closed.push(context),onReady:context=>ready.push(context)});
  return {controller,view,editor,panel,closed,ready};
}
test('Retour A→B→C→B→A conserve origine et scroll ; Fermer ignore la pile et rend le focus source',async()=>{
  const {controller:c,view,closed,panel}=harness();const focus=panel.ownerDocument.activeElement;
  await c.open('systemes',id(1),'views');view.scroll.scrollTop=71;
  await c.navigate('digues',id(2));view.scroll.scrollTop=123;
  await c.navigate('troncons',id(3));
  await c.back();assert.equal(c.getState().id,id(2));assert.equal(view.scroll.scrollTop,123);
  await c.back();assert.equal(c.getState().id,id(1));assert.equal(view.scroll.scrollTop,71);
  await c.navigate('digues',id(2));assert.equal(c.close(),true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(closed,[{source:'views'}]);assert.equal(focus.restored,true);
});
test('fermeture et nouvelle cible invalident les lectures tardives sans publier leur URL',async()=>{
  let release;const {controller:c,ready}=harness({read:(resource,key)=>key===id(1)?new Promise(resolve=>{release=()=>resolve(Repository.normalize(resource,payload(key)))}):Promise.resolve(Repository.normalize(resource,payload(key)))});
  const first=c.open('systemes',id(1));await c.open('digues',id(2));release();assert.equal(await first,false);
  assert.deepEqual(ready.map(row=>row.entityId),[id(2)]);
  const late=c.open('systemes',id(1));c.close();release();assert.equal(await late,false);assert.equal(c.getState().open,false);
});
test('lecture seule, refus d’édition et veto de brouillon/pending conservent la cible et la pile',async()=>{
  const reader=harness({can:permission=>permission==='business.read'});await reader.controller.open('systemes',id(1));
  assert.equal(await reader.controller.startEditing(),false);assert.equal(reader.view.state.canEdit,false);
  const {controller:c,editor}=harness();await c.open('systemes',id(1));await c.navigate('digues',id(2));await c.startEditing();
  editor.blocked=true;assert.equal(c.close(),false);assert.equal(await c.back(),false);assert.equal(c.getState().id,id(2));
  editor.blocked=false;editor.dirty=true;editor.allowed=false;assert.equal(c.close(),false);
  editor.allowed=true;await c.back();assert.equal(c.getState().id,id(1));
});
test('invalidation rejoint le chemin de lecture, protège l’éditeur et propage un échec de consommateur',async()=>{
  let fail=false,count=0;const {controller:c,editor}=harness({read:async(resource,key)=>{count++;if(fail)throw new Error('Lecture refusée');return Repository.normalize(resource,payload(key));}});
  await c.open('systemes',id(1));await c.invalidate({resource:'systemes',ids:[id(1)]});assert.equal(count,2);
  await c.startEditing();await c.invalidate({resource:'systemes',ids:[id(1)]});assert.equal(count,2);assert.equal(c.getState().stale,true);assert.equal(editor.state.stale,true);
  c.close();await c.open('systemes',id(1));fail=true;await assert.rejects(c.invalidate({resource:'systemes',ids:[id(1)]}),/Lecture refusée/);
  assert.equal(c.getState().id,id(1));assert.equal(c.getState().stale,true);
  fail=false;assert.equal(await c.invalidate({resource:'systemes',ids:[id(1)]}),true);
  assert.equal(count,5);assert.equal(c.getState().stale,false);
});
test('une mutation reçue pendant le chargement attend l’intégration puis relit la fiche',async()=>{
  let release,count=0;
  const {controller:c}=harness({read:async(resource,key)=>{
    count++;if(count===1)await new Promise(resolve=>{release=resolve});
    return Repository.normalize(resource,payload(key,{version:count}));
  }});
  const opening=c.open('systemes',id(1));
  const invalidation=c.invalidate({resource:'systemes',ids:[id(1)]});
  release();await opening;await invalidation;
  assert.equal(count,2);assert.equal(c.getState().baseVersion,2);
});
