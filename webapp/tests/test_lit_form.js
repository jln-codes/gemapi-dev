// Niveau A : contrats de payload Carte/Base, sans DOM ni stockage mutable ; < 1 s.
const assert = require('node:assert/strict');
const test = require('node:test');
const LitForm = require('../frontend/js/lit-form.js');
const Details = require('../frontend/js/data-entity-detail.js');
const Navigation = require('../frontend/js/entity-navigation.js');
const Relations = { values: control => control.selected || [] };
function form(classId, values = {}) {
  const settings = LitForm.profile(classId);
  const elements = Object.fromEntries(LitForm.readFields(settings).filter(field => field[3])
    .map(([name]) => [name, { value: String(values[name] ?? ''), checked: values[name] !== false }]));
  elements.digue_ids = { selected: values.digue_ids || [] };
  return { dataset: { litClass: classId }, elements };
}
test('Les deux familles ont des fiches Base et une navigation Carte sans parent Observation fictif', () => {
  for (const classId of Object.keys(LitForm.PROFILES)) {
    const settings = LitForm.profile(classId);
    assert.equal(Details.ENTITY_CONFIGS[classId].endpoint, settings.apiPath);
    assert.equal(settings.observationResource, undefined);
    assert.equal(Navigation.target(classId, '00000000-0000-4000-8000-000000000001').entityClass, classId);
  }
});
test('Lit : liste explicite multiple, vide explicite et aucune geometry ajoutée au payload Base', () => {
  const settings = LitForm.profile('troncons_lit');
  const fields = form(settings.classId, { digue_ids: ['d1', 'd2'], designation: ' Aval ', valid: false });
  const body = LitForm.payload(fields, settings, Relations);
  assert.deepEqual(body.digue_ids, ['d1', 'd2']);
  assert.equal(body.designation, 'Aval'); assert.equal(body.valid, false);
  assert.equal(Object.hasOwn(body, 'geometry'), false);
  fields.elements.digue_ids.selected = [];
  assert.deepEqual(LitForm.payload(fields, settings, Relations).digue_ids, []);
});
test('Morphologie : précision numérique, zéro, faux et NULL restent distincts', () => {
  const settings = LitForm.profile('elements_morphologiques_lit');
  const fields = form(settings.classId, { type_morphologie: 'ILE', longueur: '0',
    largeur: '1.12345678901234567890', vegetalise: false, rattache_berge: null });
  const body = LitForm.payload(fields, settings, Relations);
  assert.equal(body.longueur, '0'); assert.equal(body.largeur, '1.12345678901234567890');
  assert.equal(body.vegetalise, false); assert.equal(body.rattache_berge, null);
  assert.equal(body.segment_id, null); assert.equal(body.type_morphologie, 'ILE');
  assert.equal(Object.hasOwn(body, 'geometry'), false);
});
test('Changer le sous-type ne supprime jamais silencieusement le catalogue atterrissement', () => {
  const settings = LitForm.profile('elements_morphologiques_lit');
  const fields = form(settings.classId, { type_morphologie: 'BANC', type_zone_id: 'zone1' });
  assert.throws(() => LitForm.payload(fields, settings, Relations), /retiré explicitement/);
  fields.elements.type_zone_id.value = '';
  assert.equal(LitForm.payload(fields, settings, Relations).type_zone_id, null);
  assert.deepEqual(LitForm.TYPES.map(([id]) => id), ['ILE', 'BANC', 'ATTERISSEMENT', 'INDETERMINE']);
});
test('Le dirty-check identifie attributs et relations sans réduire les valeurs', () => {
  const feature = { properties: { designation: 'Aval', valid: true, digue_ids: ['d1', 'd2'] } };
  const fields = form('troncons_lit', feature.properties);
  fields.elements.digue_ids.selected = ['d2', 'd1'];
  assert.equal(LitForm.isDirty(fields, feature, Relations), false);
  fields.elements.commentaire.value = 'modifié';
  assert.equal(LitForm.isDirty(fields, feature, Relations), true);
});
