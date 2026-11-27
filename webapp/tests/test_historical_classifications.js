// ENTITY-TYPE-03 level A (<1 s): shared Carte/Base adapters, real field state.
// API/SQL acceptance, OCC and authorization are covered by the disposable suite.
const test=require('node:test'),assert=require('node:assert/strict');
const Veg=require('../frontend/js/vegetation-form.js');
const selectors={values:c=>c.ids||[],fill(c,rows,ids){c.ids=ids||[];},fillDisorders(c,choices,ids){c.ids=ids;},assignChanged(out,key,c,old,{force}){if(force||JSON.stringify(c.ids)!==JSON.stringify(old))out[key]=c.ids;return out;}};
const refs={fillSelect(c,rows,value){c.value=value??'';}};
function vegetation(properties={}){
 const fields=Object.values(Veg.composition().fields).flat();
 const form={elements:Object.fromEntries(fields.map(f=>[f.name,{value:'',checked:false,ids:[]}]))};
 const feature={type:'Feature',geometry:null,properties:{id:'existing',version:1,valid:false,...properties}};
 const containers={sous_type:{hidden:false}};
 Veg.fill(form,feature,{references:{},troncons:[],amenagements:[]},refs,selectors,null,containers);
 return {form,feature,containers,payload:()=>Veg.payload(form,Veg.profile('vegetation'),selectors,feature)};
}
test('an invalid historical vegetation sends only a changed description without inventing classifications',()=>{
 for(const p of [{},{famille_vegetation:'HERBACEES'},{disposition:'ISOLE',sous_type:'SOUCHE'}]){
  const h=vegetation({...p,commentaire:' historique ',densite:0});
  assert.deepEqual(h.payload(),{});
  h.form.elements.designation.value='Retouche';
  assert.deepEqual(h.payload(),{designation:'Retouche'});
  assert.equal(h.form.elements.famille_vegetation.required,Boolean(p.famille_vegetation));
  assert.equal(h.form.elements.disposition.required,Boolean(p.disposition));
  h.form.elements.valid.checked=true;Veg.updateContextualFields(h.form,h.containers);
  assert.equal(h.form.elements.famille_vegetation.required,true);
  assert.throws(h.payload,/famille et la disposition/);
 }
});
test('family detours preserve subtype; a permanent incompatible change needs explicit removal',()=>{
 const h=vegetation({famille_vegetation:'ARBRES',disposition:'ISOLE',sous_type:'SOUCHE',valid:true});
 h.form.elements.famille_vegetation.value='HERBACEES';Veg.updateContextualFields(h.form,h.containers);
 assert.equal(h.form.elements.sous_type.value,'SOUCHE');assert.equal(h.containers.sous_type.hidden,false);
 assert.throws(h.payload,/retirez explicitement/);
 h.form.elements.famille_vegetation.value='ARBRES';Veg.updateContextualFields(h.form,h.containers);
 assert.deepEqual(h.payload(),{});assert.equal(h.form.elements.sous_type.value,'SOUCHE');
 h.form.elements.famille_vegetation.value='HERBACEES';h.form.elements.sous_type.value='';
 assert.deepEqual(h.payload(),{famille_vegetation:'HERBACEES',sous_type:null});
});
test('creation, validation and erasure of existing classifications remain strict',()=>{
 const h=vegetation({id:null});
 assert.throws(()=>Veg.payload(h.form,Veg.profile('vegetation'),selectors,{properties:{}}),/famille et la disposition/);
 const complete=vegetation({famille_vegetation:'ARBRES',disposition:'ISOLE',sous_type:'SOUCHE'});
 complete.form.elements.famille_vegetation.value='';assert.throws(complete.payload,/ne peuvent pas être effacées/);
});
test('Ouvrage, Zone and Cheminement adapters omit unchanged references but send a new choice',()=>{
 for(const [module,fields] of [
  ['ouvrage-particulier-form',['type_id']],
  ['zone-stockage-form',['type_id','fonctionnement_id']],
  ['cheminement-form',['type_cheminement_id','materiau_id','revetement_id','usage_id','type_voie_id','position_id','cote_id','securite_id','orientation_id','position_haut_id','position_bas_id','revetement_haut_id','revetement_bas_id','source_id']],
 ]){
  const adapter=require('../frontend/js/'+module+'.js');
  const controls=Object.values(adapter.composition().fields).flat();
  const form={elements:Object.fromEntries(controls.map(c=>[c.name,{value:'',checked:false,ids:[]}]))};
  const properties={id:'existing'};
  for(const field of fields){form.elements[field].value='HISTORIQUE';properties[field]='HISTORIQUE';}
  form.elements.commentaire.value='Retouche';
  const patch=adapter.payload(form,{},selectors,{properties});
  for(const field of fields){assert.equal(Object.hasOwn(patch,field),false,field);form.elements[field].value='ACTIF';}
  const changed=adapter.payload(form,{},selectors,{properties});
  for(const field of fields)assert.equal(changed[field],'ACTIF',field);
  const creation=adapter.payload(form,{},selectors);
  for(const field of fields)assert.equal(creation[field],'ACTIF',field);
 }
});
