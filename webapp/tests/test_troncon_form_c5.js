// C5 level A (<1 s): patch intent, nullable/invisible values and exact geometry omission.
const test=require('node:test'),assert=require('node:assert/strict');
const Form=require('../frontend/js/troncon-form.js');
function setup(kind='BERGE'){
 const list=[...Object.values(Form.composition().fields).flat(),...Form.composition().geometry.fields,{name:Form.composition().geometry.typeName,kind:'select'}].map(field=>{
   const row={hidden:false};return {name:field.name,type:field.kind,value:'',checked:false,closest:()=>row};});
 const elements=Object.assign(list,Object.fromEntries(list.map(c=>[c.name,c])));const form={elements};
 const feature={type:'Feature',geometry:{type:'LineString',coordinates:[[2,48,3],[2.01,48.01,4]]},
  properties:{id:'a',type_physique_id:kind,type_physique_libelle:kind,libelle:'  exact  ',valid:true,largeur:0,
    commentaire:null,hauteur_murette:12.75,systeme_reperage_defaut_id:'outside-form'}};
 Form.fill(form,feature,{}, {fillSelect(c,rows,value){c.value=value??'';}});
 return {form,feature};
}
test('seven subtypes share the adapter; unchanged values and geometry are omitted',()=>{
 for(const [kind] of Form.TYPES){const {form,feature}=setup(kind);assert.equal(Form.isDirty(form),false);
   assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{});
   form.elements.designation.value='Saisie';
   assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{designation:'Saisie'});
   assert.equal(form.elements.libelle.value,'  exact  ');
   assert.equal(form.elements.hauteur_murette.value,12.75);
 }
});
test('hidden empty fields, zero, NULL, empty text and explicit removal stay distinct',()=>{
 const {form,feature}=setup();form.elements.commentaire.value='';
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{});
 form.elements.libelle.value='';form.elements.largeur.value='';
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{libelle:'',largeur:null});
 assert.equal(form.elements.materiau_haut_id.closest().hidden,true);
});
test('only explicit geometric replacement/removal changes geometry; unsafe coordinates fail',()=>{
 const {form,feature}=setup();form.elements.geometry_mode.value='remplacer';
 form.elements.geometry_type_input.value='LineString';form.elements.geometry_sommets.value='2 48\n2.1 48.1';
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature).geometry,{type:'LineString',coordinates:[[2,48],[2.1,48.1]]});
 form.elements.geometry_mode.value='effacer';assert.equal(Form.payload(form,Form.profile(),null,feature).geometry,null);
 form.elements.geometry_mode.value='garder';assert.equal(Object.hasOwn(Form.payload(form,Form.profile(),null,feature),'geometry'),false);
 assert.throws(()=>Form.parseCoordinates('NaN 48'));
 assert.throws(()=>Form.parseCoordinates('200 48'));
 assert.throws(()=>Form.parseCoordinates('2 48\n3 49 10'));
});
test('activating Risberme or Franc-bord checks the effective geometry without clearing it',()=>{
 for(const kind of ['RISBERME','FRANC_BORD']){
   const {form,feature}=setup(kind);feature.properties.valid=false;feature.geometry=null;
   Form.fill(form,feature,{}, {fillSelect(c,rows,value){c.value=value??'';}});
   form.elements.valid.checked=true;
   assert.throws(()=>Form.payload(form,Form.profile(),null,feature),/ligne ou une surface/);
   form.elements.geometry_mode.value='remplacer';form.elements.geometry_type_input.value='LineString';
   form.elements.geometry_sommets.value='2 48\n2.1 48.1';
   assert.equal(Form.payload(form,Form.profile(),null,feature).geometry.type,'LineString');
 }
});
test('a historical shape without GeoJSON stays identified and is never replaced by an attribute patch',()=>{
 const Detail=require('../frontend/js/data-entity-detail.js');
 const {form,feature}=setup();feature.geometry=null;feature.properties.geometry_type='CIRCULARSTRING';
 feature.properties.geometry_srid=3950;feature.properties.geometry_dimensions=4;
 const detail=Detail.normalizeDetail('troncons',feature);
 assert.match(detail.values.localisation,/Courbe circulaire historique.*indisponible/);
 assert.equal(detail.values.geometry_type,'CIRCULARSTRING');
 assert.equal(detail.values.geometry_dimensions,4);
 Form.fill(form,feature,{}, {fillSelect(c,rows,value){c.value=value??'';}});
 form.elements.commentaire.value='Conservé';
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{commentaire:'Conservé'});
});
test('read and edit visibility keeps populated historical values while hiding irrelevant empty fields',()=>{
 const {feature}=setup('BERGE');const names=Form.visibleFields(feature).map(([key])=>key);
 assert.ok(names.includes('hauteur_murette'));assert.ok(names.includes('largeur'));assert.ok(names.includes('digue_id'));
 assert.equal(names.includes('materiau_haut_id'),false);
 assert.ok(names.includes('geometry_type'));
});
// Pure selection/patch contracts; no mutable storage or authorization doubles.
test('Digue and AH are independently optional and changing one never rewrites the other',()=>{
 const {form,feature}=setup();form.elements.digue_id.value='D';form.elements.amenagement_hydraulique_id.value='A';
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{digue_id:'D',amenagement_hydraulique_id:'A'});
 feature.properties.digue_id='D';feature.properties.amenagement_hydraulique_id='A';
 Form.fill(form,feature,{}, {fillSelect(c,rows,value){c.value=value??'';}});
 form.elements.amenagement_hydraulique_id.value='';
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{amenagement_hydraulique_id:null});
});
test('Patrimoine selection deduplicates both paths and includes AH-only and parentless roots',()=>{
 const Heritage=require('../frontend/js/heritage-panel.js');
 const data={systemes:[{id:'S',digues:[{id:'D',troncons:[{id:'shared'}]}]}],
   amenagements_hydrauliques:[{id:'A',troncons:[{id:'shared'},{id:'AH-only'}]}],troncons_sans_parent:[{id:'no-parent'}]};
 assert.deepEqual(Heritage.selectionFromVisibility(data,new Set()).tronconIds,['shared','AH-only','no-parent']);
 const hidden=Heritage.selectionFromVisibility(data,new Set(['shared']));
 assert.deepEqual(hidden,{systemeIds:[],digueIds:[],tronconIds:['AH-only','no-parent']});
 assert.deepEqual(Heritage.normalizeSelection(hidden),hidden);
});

test('each subtype exposes its own ordinary characteristics instead of every structure field',()=>{
 const primary=kind=>Form.readSections({properties:{type_physique_id:kind}})
   .filter(group=>!['historical','diagnostics'].includes(group.key)).flatMap(group=>group.fields.map(([key])=>key));
 const cases={
  INDETERMINE:{yes:['type_troncon_id'],no:['num_couche','epaisseur_sommet','largeur_franc_bord_id']},
  BERGE:{yes:['type_rive_id'],no:['hauteur_murette','fonction_haut_id','largeur_franc_bord_id']},
  CRETE:{yes:['epaisseur','largeur_moyenne','fonction_id'],no:['pente_interieure','largeur_franc_bord_id','fonction_haut_id']},
  TALUS:{yes:['epaisseur_sommet','pente_interieure','fonction_haut_id'],no:['largeur_franc_bord_id','hauteur_murette','fonction_id']},
  TALUS_RISBERME:{yes:['epaisseur_sommet','longueur_rampant_bas'],no:['largeur_franc_bord_id','epaisseur']},
  RISBERME:{yes:['epaisseur','cote_id','fonction_id'],no:['pente_interieure','longueur_rampant_haut','largeur_franc_bord_id']},
  FRANC_BORD:{yes:['source_id','largeur_franc_bord_id'],no:['num_couche','epaisseur_sommet','fonction_haut_id']},
 };
 for(const [kind,{yes,no}]of Object.entries(cases)){
  const names=primary(kind);for(const key of yes)assert.ok(names.includes(key),`${kind}: ${key}`);
  for(const key of no)assert.ok(!names.includes(key),`${kind}: irrelevant ${key}`);
 }
});
test('populated atypical values, including zero, get a distinct historical section without any patch',()=>{
 const {form,feature}=setup('BERGE');feature.properties.materiau_bas_id='ancien';feature.properties.num_couche=0;
 Form.fill(form,feature,{}, {fillSelect(c,rows,value){c.value=value??'';}});
 const groups=Form.readSections(feature);const historical=groups.find(group=>group.key==='historical');
 assert.ok(historical);const keys=historical.fields.map(([key])=>key);
 for(const key of ['hauteur_murette','largeur','num_couche','materiau_bas_id'])assert.ok(keys.includes(key));
 const usual=groups.filter(group=>!['historical','diagnostics'].includes(group.key)).flatMap(group=>group.fields.map(([key])=>key));
 for(const key of keys)assert.ok(!usual.includes(key));
 assert.deepEqual(Form.payload(form,Form.profile(),null,feature),{});
});
test('geometry is described in business language while raw diagnostics remain separate',()=>{
 const feature={geometry:{type:'LineString',coordinates:[[2,48],[3,49]]},properties:{type_physique_id:'BERGE',geometry_type:'LINESTRING',geometry_srid:3950,geometry_dimensions:4}};
 assert.match(Form.geometrySummary(feature),/linéaire/);
 const groups=Form.readSections(feature);const diagnostic=groups.find(group=>group.key==='diagnostics');
 for(const key of ['geometry_type','geometry_srid','geometry_dimensions'])assert.ok(diagnostic.fields.some(([name])=>name===key));
 assert.equal(groups.find(group=>group.key==='geometry').fields.some(([key])=>key==='geometry_srid'),false);
});
