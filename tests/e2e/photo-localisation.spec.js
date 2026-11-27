const {test,expect}=require('@playwright/test');
const {randomUUID}=require('node:crypto');
const {mockResourceJournal}=require('./mock-resource-journal');
const P='00000000-0000-4000-8000-000000000071',O='00000000-0000-4000-8000-000000000072',T='00000000-0000-4000-8000-000000000073',S='00000000-0000-4000-8000-000000000074',B='00000000-0000-4000-8000-000000000075';
const obs={id:O,designation:'O-72 Observation',photos:[]};
const modal=p=>p.locator('#photo-localisation-dialog'),f=(p,n)=>modal(p).locator(`[name="${n}"]`),save=p=>modal(p).locator('[type=submit]');
async function setup(context,opts={}) {
  let photo={id:P,observation_id:O,version:3,libelle:'Photographe',localisation:{srid:3950,crs:{authority:'EPSG',srid:3950,code:'EPSG:3950'},geometry:{type:'Point',coordinates:[1700020,8200040]},geometry_4326:{type:'Point',coordinates:[2,46]},systeme_reperage_id:S,troncon_id:T,borne_debut_id:B,borne_debut_distance:20,position_debut_relative:'APRES_BORNE',pr_debut:104,systeme_reperage_libelle:'Système de test',warnings:[],...opts.loc}};
  const writes=[];
  await context.route('**/api/**',async route=>{
    if(await mockResourceJournal(route))return;
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(path===`/api/photos/${P}/localisation`){
      writes.push({body:req.postDataJSON(),operation:req.headers()['operation-id'],version:req.headers()['if-match']});
      const response={...photo,version:4,mutation:{schemaVersion:1,eventId:randomUUID(),resource:'photos',action:'update',ids:[P],versions:{[P]:4},occurredAt:Date.now(),originClientId:req.headers()['x-digues-app-client-id'],originInstanceId:req.headers()['x-digues-app-instance-id']}};
      if(opts.put)return opts.put(route,response,writes);
      photo=response;return route.fulfill({json:response});
    }
    if(path===`/api/photos/${P}`)return route.fulfill({json:photo});
    if(path===`/api/photos/${P}/localisation-options`)return route.fulfill({json:{context_troncons:opts.ambiguous?[{id:T,designation:'Tronçon A'},{id:randomUUID(),designation:'Tronçon B'}]:[{id:T,designation:'Tronçon A'}],systemes:[{id:S,designation:'Système de test',bornes:[{id:B,designation:'Borne A'}]}]}});
    if(path==='/api/troncons/options')return route.fulfill({json:{troncons:[{id:T,libelle:'Tronçon A'}]}});
    if(path==='/api/config')return route.fulfill({json:{legacy_single_database_enabled: true, show_uuid:false}});
    return route.fulfill({json:{type:'FeatureCollection',features:[],systemes:[],references:[]}});
  });
  return {photo,writes};
}
async function open(page,photo){
  await page.goto(page.viewportSize().width < 700 ? '/carte' : '/base');
  await page.waitForFunction(() => Boolean(window.PhotoLocalisation));
  await page.evaluate(({photo,obs})=>PhotoLocalisation.open(photo,obs,p=>window.savedPhoto=p),{photo,obs});
  await expect(save(page)).toBeEnabled();
}

test('bornage : libellés V2 visibles sans changer les valeurs techniques',async({context,page})=>{
  const {photo}=await setup(context,{loc:{position_debut_relative:'AVANT_BORNE',borne_debut_distance:12,
    position_fin_relative:'APRES_BORNE',borne_fin_distance:8}});
  await open(page,photo);
  await expect(modal(page).locator('[data-summary]')).toContainText('Amont · distance 12 m');
  await expect(modal(page).locator('[data-summary]')).toContainText('Aval · distance 8 m');
  await modal(page).locator(`[name=mode][value=bornage] + span`).click();
  const options=await f(page,'position_relative').locator('option').evaluateAll(rows=>rows.map(row=>[row.value,row.textContent]));
  expect(options).toEqual([['SUR_BORNE','Sur la borne'],['AVANT_BORNE','Amont'],['APRES_BORNE','Aval']]);
  await expect(modal(page).locator('form')).not.toContainText('Avant la borne');
  await expect(modal(page).locator('form')).not.toContainText('Après la borne');
  await f(page,'position_relative').selectOption('AVANT_BORNE');
  await expect(f(page,'position_relative')).toHaveValue('AVANT_BORNE');
});

test('résumé historique : distance zéro affichée Sur la borne',async({context,page})=>{
  const {photo}=await setup(context,{loc:{position_debut_relative:'AVANT_BORNE',borne_debut_distance:0}});
  await open(page,photo);
  await expect(modal(page).locator('[data-summary]')).toContainText('Sur la borne · distance 0 m');
});

test('distance Photo : incréments d’un mètre et sauvegarde des décimales relues', async ({ context, page }) => {
  const { photo, writes } = await setup(context, { loc: { borne_debut_distance: 12.34 } });
  await open(page, photo);
  await modal(page).locator('[name=mode][value=bornage] + span').click();
  const distance = f(page, 'distance_m');
  await expect(distance).toHaveAttribute('step', '1');
  await expect(distance).not.toHaveCSS('appearance', 'textfield');
  await expect(distance).toHaveValue('12.34');
  await distance.press('ArrowUp');
  await expect(distance).toHaveValue('13.34');
  await distance.press('ArrowDown');
  await expect(distance).toHaveValue('12.34');
  await distance.fill('-1');
  expect(await distance.evaluate(input => input.validity.rangeUnderflow)).toBe(true);
  await distance.fill('12.34');
  await save(page).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body.distance_m).toBe(12.34);
});
for(const viewport of [{width:360,height:800},{width:375,height:812},{width:812,height:375},
  {width:768,height:1024},{width:1024,height:768},{width:1366,height:768}]) {
  test(`localisation tactile et sans débordement ${viewport.width}×${viewport.height}`,async({context,page})=>{
    await page.setViewportSize(viewport);
    const {photo}=await setup(context);
    await open(page,photo);
    const dialog=modal(page);
    for(const mode of ['point','bornage','pr','effacer']) {
      await modal(page).locator(`[name=mode][value=${mode}] + span`).click();
      const control=mode==='point' ? dialog.locator('[data-map-start]')
        : mode==='bornage' ? f(page,'position_relative')
          : mode==='pr' ? f(page,'pr') : save(page);
      await control.scrollIntoViewIfNeeded();
      const bounds=await control.boundingBox();
      expect(bounds.height).toBeGreaterThanOrEqual(44);
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x+bounds.width).toBeLessThanOrEqual(viewport.width+1);
      expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    }
    await modal(page).locator(`[name=mode][value=point] + span`).click();
    await dialog.locator('[data-map-start]').click();
    const cancel=dialog.locator('[data-map-cancel]');
    await cancel.scrollIntoViewIfNeeded();
    expect((await cancel.boundingBox()).height).toBeGreaterThanOrEqual(44);
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await cancel.click();
    await dialog.locator('[data-close]').click();
  });
}

test('lecture historique, contexte distinct, coordonnées, version et reçu autoritaires',async({context,page})=>{
  const {photo,writes}=await setup(context);await open(page,photo);
  await expect(modal(page).locator('[data-context]')).toContainText('O-72');await expect(modal(page).locator('[data-summary]')).toContainText('PR 104');
  await f(page,'x').fill('1700030');await save(page).click();await expect(modal(page).locator('[data-role=status]')).toContainText('Localisation enregistrée');
  expect(writes[0].version).toBe('"3"');expect(writes[0].body.coordinates).toEqual([1700030,8200040]);expect(writes[0].body.systeme_reperage_id).toBe(S);
  expect(await page.evaluate(()=>savedPhoto.version)).toBe(4);expect(await page.evaluate(()=>OfflineRuntime.store.listOperations())).toEqual([]);
});

test('carte : annuler conserve le brouillon, clic et déplacement validés explicitement',async({context,page})=>{
  const {photo,writes}=await setup(context);await open(page,photo);await f(page,'x').fill('1700777');
  await modal(page).locator('[data-map-start]').click();await modal(page).locator('[data-map]').click({position:{x:100,y:100}});
  await expect(save(page)).toBeDisabled();await modal(page).locator('[data-map-cancel]').click();await expect(f(page,'x')).toHaveValue('1700777');expect(writes).toHaveLength(0);
  await modal(page).locator('[data-map-start]').click();await modal(page).locator('[data-map]').click({position:{x:170,y:160}});
  const marker=modal(page).locator('.leaflet-marker-draggable');await expect(marker).toBeVisible();const bounds=await marker.boundingBox();
  await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);await page.mouse.down();await page.mouse.move(bounds.x+45,bounds.y+20,{steps:8});await page.mouse.up();
  await modal(page).locator('[data-map-accept]').click();await expect(f(page,'srid')).toHaveValue('4326');await expect(f(page,'x')).not.toHaveValue('1700777');
  await save(page).click();await expect(modal(page).locator('[data-role=status]')).toContainText('enregistrée');expect(writes[0].body.srid).toBe(4326);
});

for(const mode of ['bornage','pr'])test(`édition ${mode} sans recopie du parent`,async({context,page})=>{
  const {photo,writes}=await setup(context);await open(page,photo);await modal(page).locator(`[name=mode][value=${mode}] + span`).click();
  if(mode==='bornage'){await f(page,'position_relative').selectOption('AVANT_BORNE');await f(page,'distance_m').fill('12');}else await f(page,'pr').fill('160');
  await save(page).click();await expect(modal(page).locator('[data-role=status]')).toContainText('enregistrée');expect(writes[0].body.mode).toBe(mode);expect(writes[0].body.coordinates).toBeUndefined();
});

test('double soumission et réponse perdue : intention identique, brouillon conservé',async({context,page})=>{
  let release,receipt;
  const {photo,writes}=await setup(context,{put:async(route,result,writes)=>{if(writes.length===1){receipt=result;await new Promise(r=>release=r);return route.abort('failed');}return route.fulfill({json:receipt});}});
  await open(page,photo);await f(page,'x').fill('1700555');await save(page).click();await expect.poll(()=>writes.length).toBe(1);
  await expect(save(page)).toBeDisabled();await modal(page).locator('form').evaluate(form=>form.dispatchEvent(new Event('submit',{cancelable:true})));expect(writes).toHaveLength(1);release();
  await expect(modal(page).locator('[data-role=status]')).toContainText('Résultat non confirmé');await expect(f(page,'x')).toHaveValue('1700555');await save(page).click();
  await expect(modal(page).locator('[data-role=status]')).toContainText('enregistrée');expect(writes[1]).toEqual(writes[0]);
});

test('conflit distant et événement conservent le brouillon sans rebase',async({context,page})=>{
  const {photo,writes}=await setup(context,{put:route=>route.fulfill({status:409,json:{detail:{message:'Version actuelle 4'}}})});await open(page,photo);await f(page,'x').fill('1700999');
  await page.evaluate(({P,id})=>ResourceMutationController.dispatch({schemaVersion:1,eventId:id,resource:'photos',action:'update',ids:[P],versions:{[P]:4},occurredAt:Date.now(),originClientId:'remote',originInstanceId:'remote'}),{P,id:randomUUID()});
  await expect(modal(page).locator('[data-role=status]')).toContainText('brouillon');await expect(f(page,'x')).toHaveValue('1700999');
  await save(page).click();await expect(modal(page).locator('[data-role=status]')).toContainText('Conflit');expect(writes[0].version).toBe('"3"');await expect(f(page,'x')).toHaveValue('1700999');await expect(save(page)).toBeDisabled();
});

test('plusieurs tronçons : choix explicite ; historique remplacé seulement après confirmation',async({context,page})=>{
  const {photo,writes}=await setup(context,{ambiguous:true,loc:{troncon_id:null,systeme_reperage_id:null,geometry:{type:'LineString',coordinates:[[1700000,8200000],[1700100,8200000]]},remplacement_requis:true,warnings:['EXTREMITES_HISTORIQUES_DIFFERENTES']}});
  await open(page,photo);await expect(f(page,'troncon_id')).toHaveValue('');await expect(f(page,'x')).toHaveValue('');
  await expect(modal(page).locator('[data-warnings]')).toContainText('EXTREMITES');
  const bornage = modal(page).getByText('Bornage', { exact: true });
  await expect(bornage).toBeHidden();
  await save(page).click();expect(writes).toHaveLength(0);
  await f(page,'troncon_id').selectOption(T);
  await bornage.click();
  await f(page,'systeme_reperage_id').selectOption(S);await f(page,'borne_id').selectOption(B);
  await f(page,'distance_m').fill('0');
  await save(page).click();expect(writes).toHaveLength(0);
  await f(page,'remplacer_historique').check();await save(page).click();await expect(modal(page).locator('[data-role=status]')).toContainText('enregistrée');expect(writes[0].body.remplacer_historique).toBe(true);
});

test('le changement de tronçon Photo invalide le bornage et conserve les coordonnées', async ({ context, page }) => {
  const { photo, writes } = await setup(context);
  await open(page, photo);
  await f(page,'x').fill('1700025');
  await f(page,'y').fill('8200045');
  const bornage = modal(page).getByText('Bornage', { exact: true });
  await bornage.click();
  await f(page,'distance_m').fill('42');
  await f(page,'troncon_id').selectOption('');
  await expect(bornage).toBeHidden();
  await expect(f(page,'x')).toHaveValue('1700025');
  await expect(f(page,'y')).toHaveValue('8200045');
  await expect(modal(page).locator('[data-role=geometry-diagnostics]')).toContainText('tronçon précédent');
  await f(page,'troncon_id').selectOption(T);
  await bornage.click();
  await expect(f(page,'distance_m')).toHaveValue('');
  await expect(f(page,'borne_id')).toHaveValue('');
  expect(writes).toHaveLength(0);
});

for(const width of [375,768])test(`localisation responsive ${width}`,async({context,page})=>{
  await page.setViewportSize({width,height:900});const {photo}=await setup(context);await open(page,photo);await modal(page).locator('[data-map-start]').click();
  const bounds=await modal(page).boundingBox();expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.width).toBeLessThanOrEqual(width);
  expect(await modal(page).evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await page.screenshot({path:`audits/2026-09-17-photos-lot3b-preuves/localisation-${width}.png`});
  await modal(page).locator('[data-map-cancel]').click();
});

test('fermeture annulée conserve la saisie ; succès confirmé malgré rafraîchissement défaillant',async({context,page})=>{
  const {photo}=await setup(context);await open(page,photo);await f(page,'x').fill('1700777');page.once('dialog',d=>d.dismiss());await modal(page).locator('[data-close]').click();await expect(f(page,'x')).toHaveValue('1700777');
  await page.evaluate(()=>ResourceMutationController.publishMutation=async()=>{throw new Error('refresh');});await save(page).click();await expect(modal(page).locator('[data-role=status]')).toContainText('Localisation enregistrée. Actualisation');
});
