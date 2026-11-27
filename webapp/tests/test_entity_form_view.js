// FORM-CORE-2, niveau A : contrat d'extension fermé, aucune ressource mutable (~1 s).
// Le DOM réel et les interactions sont exercés par les E2E ciblés.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const View = require('../frontend/js/entity-form-view.js');
const MobilierForm = require('../frontend/js/mobilier-form.js');
const descriptor = () => MobilierForm.composition(MobilierForm.profile('mobilier'));

test('les sections sont fermées et leur ordre est immuable', () => {
  assert.deepEqual(View.SECTIONS, ['identification', 'classification', 'characteristics', 'attachments', 'geometry', 'historical', 'actions']);
  assert.ok(Object.isFrozen(View.SECTIONS));
  assert.equal(View.validate(descriptor()).family, 'mobilier');
  for (const key of ['renderForm', 'html', 'header', 'tabs', 'className', 'style', 'order']) {
    assert.throws(() => View.validate({ ...descriptor(), [key]: () => {} }), TypeError);
    const invalid = descriptor(); invalid.fields.classification[0][key] = 'arbitrary';
    assert.throws(() => View.validate(invalid), TypeError);
  }
  const invalid = descriptor(); invalid.fields.custom = [];
  assert.throws(() => View.validate(invalid), TypeError);
  for (const key of ['title', 'label', 'newLabel', 'banner']) {
    assert.throws(() => View.validate({ ...descriptor(), [key]: 'Texte libre' }), TypeError);
  }
  const invalidMode = descriptor();
  invalidMode.geometry.modes = ['autre']; invalidMode.geometry.modeName = 'mode';
  assert.throws(() => View.validate(invalidMode), /Modes de localisation/);
});

test('les champs du pilote sont exhaustifs, typés et non duplicables', () => {
  const value = descriptor();
  assert.deepEqual(Object.values(value.fields).flat().map(field => field.name),
    ['designation', 'commentaire', 'date_debut', 'valid', 'type_id', 'troncon_id', 'desordre_ids']);
  assert.equal(value.fields.attachments[1].multiple, true);
  assert.equal(value.fields.classification[0].required, true);
  value.fields.attachments.push(value.fields.identification[0]);
  assert.throws(() => View.validate(value), /dupliqué/);
});

test('une géométrie déclarative et les commandes spatiales ne peuvent pas composer deux sections concurrentes', () => {
  const value=descriptor();
  value.fields.geometry=[{name:'historical_geometry',label:'Géométrie conservée',kind:'output'}];
  assert.throws(()=>View.validate(value),/compositions géométriques concurrentes/);
  value.geometry=null;
  assert.equal(View.validate(value),value);
});

test('Équipement conserve son catalogue, sa ressource et le même contrat Point', () => {
  const profile = MobilierForm.profile('equipements_mesure');
  const value = MobilierForm.composition(profile);
  assert.equal(profile.referenceName, 'typesEquipementMesure');
  assert.equal(profile.resource, 'equipements-mesure');
  assert.equal(value.observations.resource, 'equipements_mesure');
  assert.deepEqual(value.geometry.types, ['Point']); assert.equal(value.geometry.required, true);
  assert.equal(View.validate(value), value);
});

test('Base et Carte partagent les champs sans accorder un outil géométrique à Base', () => {
  const profile = MobilierForm.profile('mobilier');
  const map = MobilierForm.composition(profile);
  const base = MobilierForm.composition(profile, { geometryEditable: false });
  assert.deepEqual(base.fields, map.fields);
  assert.equal(base.geometry.editable, false); assert.equal(map.geometry.editable, true);
  assert.equal(MobilierForm.fieldsMarkup, undefined);
  const network=MobilierForm.composition(MobilierForm.profile('reseaux_techniques'));
  assert.deepEqual(network.geometry.types,['Point','LineString']);
  assert.equal(network.geometry.required,false);
  const Detail=require('../frontend/js/data-entity-detail.js');
  for(const config of Object.values(Detail.ENTITY_CONFIGS).filter(c=>c.formAdapter)) {
    const value=config.formAdapter.composition(config.pilotProfile);
    assert.equal(View.validate(value),value);
    const names=Object.values(value.fields).flat().map(f=>f.name);
    for(const [name,,,editable] of config.fields)if(editable)assert.ok(names.includes(name),name);
    assert.equal(config.formAdapter.fieldsMarkup,undefined);
  }
});

test('les contrôleurs migrés délèguent la structure ; les deux hôtes chargent le même socle', () => {
  const editor = fs.readFileSync('webapp/frontend/js/mobilier-editor.js', 'utf8');
  assert.doesNotMatch(editor, /innerHTML|createElement\(|beforeunload/);
  assert.match(editor, /formViewFactory\.create/); assert.match(editor, /formSessionFactory\.create/);
  for (const filename of ['map.html', 'data.html']) {
    const page = fs.readFileSync(`webapp/frontend/${filename}`, 'utf8');
    assert.equal(page.split('/static/css/entity-forms.css').length - 1, 1);
    assert.equal(page.split('/static/js/entity-form-view.js').length - 1, 1);
    assert.ok(page.indexOf('entity-form-view.js') < page.indexOf('data-entity-detail.js'));
    assert.ok(page.indexOf('entity-forms.css') < page.indexOf('business-session.css'));
  }
  const worker = fs.readFileSync('webapp/frontend/service-worker.js', 'utf8');
  assert.match(worker, /"\/static\/css\/entity-forms.css"/);
  assert.match(worker, /"\/static\/js\/entity-form-view.js"/);
});

// A pristine draft must not acquire the literal relation "undefined"; valid
// selected identities outside the current options must still be retained.
test('un formulaire vierge ne fabrique pas de rattachement et reste sans modification',()=>{
  for(const id of ['mobilier','equipements_mesure','reseaux_techniques']){
    const fields=Object.values(MobilierForm.composition(MobilierForm.profile(id)).fields).flat();
    const form={elements:Object.fromEntries(fields.map(field=>[field.name,{value:'',checked:false}]))};
    const selected=[];
    const relations={fill:(_select,_rows,ids)=>selected.push(ids),fillDisorders(){},values:()=>[],relationChanged:()=>false};
    const refs={fillSelect(control,_rows,current){control.value=current||'';}};
    MobilierForm.fill(form,{properties:{valid:true}},{types:[],troncons:[]},refs,relations);
    assert.deepEqual(selected,[[]]);assert.equal(MobilierForm.isDirty(form,{properties:{valid:true}},relations),false);
    MobilierForm.fill(form,{properties:{valid:true,troncon_id:'current-outside'}},{types:[],troncons:[]},refs,relations);
    assert.deepEqual(selected.at(-1),['current-outside']);
  }
});

test('les associations de Prestation restent explicitement multiples dans le descripteur',()=>{
  const Form=require('../frontend/js/prestation-form.js');const value=Form.composition();
  for(const [name] of Form.OBJECT_FAMILIES) {
    const field=value.fields.attachments.find(f=>f.name===`objects_${name}`);
    assert.equal(field.kind,'relation');assert.equal(field.multiple,true);
  }
  assert.throws(()=>View.validate({...descriptor(),actions:[{name:'save',label:'Autre sauvegarde'}]}),/dupliquée/);
  assert.throws(()=>View.validate({...descriptor(),children:['photos','photos']}),/dupliqué/);
});

test('le repérage spécialisé reste un contexte fermé du compositeur Tronçon',()=>{
 assert.equal(View.validate({family:'troncons',context:'troncon-reperage',fields:{},children:['details']}).context,'troncon-reperage');
 assert.throws(()=>View.validate({family:'photos',context:'troncon-reperage',fields:{}}),TypeError);
 const value=descriptor();value.geometry.typeName='physical_type';assert.equal(View.validate(value),value);
 value.geometry.typeName='designation';assert.throws(()=>View.validate(value),/dupliqué/);
 value.geometry.typeName='invalid name';assert.throws(()=>View.validate(value),TypeError);
});
