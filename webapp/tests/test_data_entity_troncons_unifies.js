// Canonical resource summaries, retaining distinct objects and business subtypes.
const assert = require('node:assert/strict');
const test = require('node:test');
const Detail = require('../frontend/js/data-entity-detail.js');
test('Base AH and Digue expose the same canonical Tronçon field and business summaries',()=>{
  for(const resource of ['digues','amenagements_hydrauliques']){
    const field=Detail.ENTITY_CONFIGS[resource].fields.find(([name])=>name==='troncons');
    assert.equal(field[2],'unified_troncons');assert.equal(field[3],false);
    assert.equal(Detail.ENTITY_CONFIGS[resource].fields.some(([name])=>name==='troncons_unifies'),false);
  }
  assert.deepEqual(Detail.formatUnifiedTroncons([
    {id:'u1',type_physique_id:'INDETERMINE',type_physique_libelle:'Indéterminé',designation:'Trait AH'},
    {id:'u2',type_physique_id:'BERGE',type_physique_libelle:'Berge',designation:null},
    {id:'u3',type_physique_id:'FRANC_BORD',type_physique_libelle:'Franc-bord',designation:'Aval'}]),
    ['Indéterminé : Trait AH','Berge : u2','Franc-bord : Aval']);
  assert.deepEqual(Detail.formatUnifiedTroncons([]),[]);
  assert.equal(Detail.formatSubobjects({structures:[{id:'s1',designation:'Structure'}]},'structures'),'Structure');
});
