// ENTITY-TYPE-02 level A (<1 s): real adapter and unchanged control identities.
// No SQL/security/offline boundary is mocked; those use the PostgreSQL harness.
const test=require('node:test'),assert=require('node:assert/strict');
const Form=require('../frontend/js/troncon-form.js');
function setup(kind='BERGE',geometry={type:'LineString',coordinates:[[2,48,3],[2.01,48.01,4]]}){
 const controls=[...Object.values(Form.composition().fields).flat(),...Form.composition().geometry.fields,{name:Form.composition().geometry.typeName,kind:'select'}].map(field=>{
   const row={hidden:false};return {name:field.name,type:field.kind,value:'',checked:false,closest:()=>row};
 });
 const form={elements:Object.assign(controls,Object.fromEntries(controls.map(c=>[c.name,c])))};
 const feature={type:'Feature',geometry,properties:{id:'test',type_physique_id:kind,type_physique_libelle:kind,
   valid:true,largeur:7.5,hauteur_murette:12.75,commentaire:'Historique',version:1}};
 Form.fill(form,feature,{}, {fillSelect(c,rows,value){c.value=value??'';}});
 return {form,feature};
}
test('all seven subtype choices are editable and explicitly conditional',()=>{
 const descriptor=Form.composition();
 const field=Object.values(descriptor.fields).flat().find(f=>f.name==='type_physique_id');
 assert.equal(field.kind,'select');assert.equal(field.required,true);assert.equal(Boolean(field.readOnly),false);
 assert.deepEqual(field.options,Form.TYPES);assert.match(field.help,/compatibles/);
});
test('49 subtype drafts send only the changed subtype and never implicitly change geometry or attributes',()=>{
 for(const [source] of Form.TYPES)for(const [target] of Form.TYPES){
   const {form,feature}=setup(source);form.elements.type_physique_id.value=target;
   Form.updateConditional(form,feature);
   assert.deepEqual(Form.payload(form,Form.profile(),null,feature),source===target?{}:{type_physique_id:target});
   assert.equal(form.elements.largeur.value,7.5);assert.equal(form.elements.hauteur_murette.value,12.75);
   assert.equal(form.elements.geometry_mode.value,'garder');assert.equal(feature.properties.type_physique_id,source);
 }
});
test('subtype detours update relevance while preserving historical and newly typed values and controls',()=>{
 const {form,feature}=setup('BERGE'),nature=form.elements.nature_id;
 form.elements.type_physique_id.value='TALUS';Form.updateConditional(form,feature);
 assert.equal(form.elements.fonction_haut_id.closest().hidden,false);
 form.elements.nature_haut_id.value='draft-nature';form.elements.largeur.value='9.5';
 form.elements.type_physique_id.value='CRETE';Form.updateConditional(form,feature);
 assert.equal(form.elements.nature_haut_id.closest().hidden,false);
 assert.equal(form.elements.hauteur_murette.closest().hidden,false);
 assert.equal(form.elements.fonction_haut_id.closest().hidden,true);
 form.elements.type_physique_id.value='BERGE';Form.updateConditional(form,feature);
 assert.equal(form.elements.nature_haut_id.value,'draft-nature');assert.equal(form.elements.largeur.value,'9.5');
 assert.equal(form.elements.nature_id,nature);
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{nature_haut_id:'draft-nature',largeur:9.5});
});
test('geometry validation uses the chosen subtype and allows leaving a constrained type without changing shape',()=>{
 const point={type:'Point',coordinates:[2,48]};
 const {form,feature}=setup('BERGE',point);form.elements.type_physique_id.value='RISBERME';
 assert.throws(()=>Form.payload(form,Form.profile(),null,feature),/ligne ou une surface/);
 form.elements.type_physique_id.value='FRANC_BORD';
 assert.throws(()=>Form.payload(form,Form.profile(),null,feature),/ligne ou une surface/);
 const leaving=setup('RISBERME',point);leaving.form.elements.type_physique_id.value='BERGE';
 assert.equal(Form.payload(leaving.form,Form.profile(),null,leaving.feature).geometry,undefined);
});
test('retaining a valid historical shape without displayable GeoJSON permits requalification without replacing it',()=>{
 for(const [geometry_type,type] of [['LINESTRING','RISBERME'],['POLYGON','FRANC_BORD']]){
   const {form,feature}=setup('BERGE',null);
   Object.assign(feature.properties,{geometry_type,geometry_srid:0,geometry_dimensions:4,geometry_valide:true,geometry_vide:false});
   form.elements.type_physique_id.value=type;
   assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{type_physique_id:type});
   feature.properties.geometry_valide=false;
   assert.throws(()=>Form.payload(form,Form.profile(),null,feature),/ligne ou une surface/);
   feature.properties.geometry_valide=true;form.elements.geometry_mode.value='effacer';
   assert.throws(()=>Form.payload(form,Form.profile(),null,feature),/ligne ou une surface/);
 }
});
