/* Migration de présentation, niveau A (<1 s), sans DOM/SQL mutable.
 * Garantit que chaque donnée sélectionnée par l'ancien lecteur a un chemin
 * explicite dans le nouveau contrat (y compris relations regroupées).
 */
const test=require('node:test');
const assert=require('node:assert/strict');
const old=require('../frontend/js/data-entity-detail.js');
const Registry=require('../frontend/js/base-detail-registry.js');
test('les données de lecture des seize familles restent représentées sans héritage de la composition éditable',()=>{
 assert.deepEqual(Object.keys(Registry.definitions).sort(),Object.keys(old.ENTITY_CONFIGS).sort());
 for(const [resource,config]of Object.entries(old.ENTITY_CONFIGS)){
  const fields=Registry.get(resource).fields;
  for(const [key]of config.fields){
   const represented=fields.some(field=>field.key===key||field.source===key||field.idsSource===key
    ||key==='object_ids'&&field.source?.startsWith('object_ids.')
    ||resource==='desordres'&&key==='troncons'&&field.source==='troncon_ids');
   assert.ok(represented,`${resource}.${key} a été perdu`);
  }
 }
});
