// B (~1 min): same DOM and HTTP geometry for the three roles. SQL authorization
// and identical API payloads are checked by the real PostgreSQL C4 suite.
const {test, expect}=require('@playwright/test');
const {fixture, ready, clickFeature}=require('./fixtures/map-editor-fixture');
const {fakeId}=require('./fixtures/frontend-preview-data');
test.use({serviceWorkers:'block'});
async function role(page,code){
 await page.evaluate(code=>{const original=BusinessRuntime;
  globalThis.BusinessRuntime=Object.freeze({...original,can:p=>p==='business.read'||(code!=='reader'&&p==='business.update')});
 },code);
}
async function open(page,family,id){
 await page.evaluate(({family,id})=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',
  {detail:{entityClass:family,entityId:id,source:'home',entities:[]}})),{family,id});
}
for(const code of ['reader','editor','database_admin']) {
 test(`consultation géométrique identique — ${code}`,async({context,page})=>{
  const state=await fixture(context), record=state.records.mobilier[0];
  record.geometry={type:'Point',coordinates:[3,50]};
  // Carte uses its ordinary disabled editor for readers.
  await ready(page);await role(page,code);await clickFeature(page,record.properties.id);
  const reading=page.locator('#mobilier-editor-panel [data-spatial-read]');
  await expect(reading).toContainText('Position — longitude (EPSG:4326)3');
  await expect(reading).toContainText('X : 1700000.000 · Y : 9200000.000 (EPSG:3950)');
  if(code==='reader')await expect(page.locator('#mobilier-editor-panel [data-role=save]')).toBeHidden();
  // Base uses the shared consultation controller and the same read component.
  await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);
  await role(page,code);await open(page,'mobilier',record.properties.id);
  const detail=page.locator('#data-entity-detail-read');
  await expect(detail).toBeVisible();
  await expect(detail).toContainText('Position — longitude (EPSG:4326)3');
  await expect(detail).toContainText('Position — latitude (EPSG:4326)50');
  await expect(detail).toContainText('X : 1700000.000 · Y : 9200000.000 (EPSG:3950)');
  await expect(page.locator('#data-entity-detail-edit'))[code==='reader'?'toBeHidden':'toBeVisible']();
  // Empty geometry remains distinct from a visibility defect.
  record.geometry=null;await open(page,'mobilier',record.properties.id);
  await expect(detail).toContainText('Géométrie non renseignée');
  expect(state.creates).toHaveLength(0);expect(state.writes).toHaveLength(0);
 });
}
test('repérage existant visible au lecteur et réponse tardive isolée',async({context,page})=>{
 const state=await fixture(context);let release;
 await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);
 await role(page,'reader');
 await context.route('**/api/base-summary',async route=>{
  await new Promise(resolve=>{release=resolve;});
  await route.fulfill({json:{crs:{authority:'EPSG',srid:3950,code:'EPSG:3950',name:'CC50',label:'CC50'}}});
 });
 const disorder={type:'Feature',geometry:{type:'LineString',coordinates:[[3,50],[3.001,50.001]]},properties:{
  id:fakeId(90),designation:'Désordre repéré',version:1,troncon_ids:[],reperage:{
   systeme_reperage_libelle:'Référence persistée',borne_debut_libelle:'Borne Alpha',pr_debut:0,
   distance_debut_m:0,position_debut_relative:'SUR_BORNE',borne_fin_libelle:'Borne Beta',pr_fin:42}}};
 await context.route(`**/api/desordres/${disorder.properties.id}`,route=>route.fulfill({json:disorder}));
 await open(page,'desordres',disorder.properties.id);
 const detail=page.locator('#data-entity-detail-read');
 await expect(detail).toContainText('Borne Alpha');await expect(detail).toContainText('Borne Beta');
 await expect(detail).toContainText('PR de début0');await expect(detail).toContainText('Distance de début (m)0');
 await open(page,'systemes',state.system.id);
 await expect(detail).not.toContainText('Borne Alpha');
 release();await expect(detail).not.toContainText('X :');
 expect(state.writes).toHaveLength(0);
});
