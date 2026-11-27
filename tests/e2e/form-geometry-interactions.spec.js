// FORM-GEOM-1, niveau B : Leaflet + vraie API/PostGIS jetable (~2 min).
// Objets uniques par scénario ; assertions sur les requêtes et la relecture,
// notamment aucun PUT géométrique au moment de terminer/annuler un dessin.
const {test,expect}=require('@playwright/test');
const {randomUUID}=require('node:crypto');
test.use({serviceWorkers:'block',actionTimeout:10000});
test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE!=='true','Runner PostgreSQL jetable requis.');
async function ready(page){
 page.on('pageerror',error=>{throw error});
 await page.goto('/carte');
 // Readiness includes asynchronous collection loads; it is distinct from the
 // 10 s interaction budget. Cold isolated servers may need longer to load.
 await page.waitForFunction(()=>{try{return Boolean(mobilierEditor&&tronconsGeoJsonLayer&&mapViewportReady)}catch{return false}},null,{timeout:60000});
 await expect(page.locator('#toggle-create-menu')).toBeEnabled();
 if(await page.locator('#heritage-panel').isVisible())await page.locator('#close-heritage').click();
}
async function entry(page,family){
 await page.locator('#toggle-create-menu').click();await page.locator(`[data-create-type="${family}"]`).click();
 const visible=page.locator('#editor-panel .entity-form:visible').first();await visible.waitFor();
 const id=await visible.getAttribute('id');const view=page.locator('#'+id);
 await expect(view).toHaveAttribute('aria-busy','false');return view;
}
async function fields(view,family,label){
 await view.locator('[name=designation]').fill(label);
 if(family==='troncon')await view.locator('[name=libelle]').fill(label);
 for(const select of await view.locator('select[required]:visible').all())if(!await select.inputValue())await select.selectOption({index:1});
 if(family==='prestation')await view.locator('[name=date_fin]').fill('2026-11-10');
}
async function location(page,index,touch=false){
 const rect=await page.locator('#map').boundingBox();
 const points=[[.55,.45],[.66,.58],[.70,.45]];const [x,y]=points[index];
 const panel=await page.locator('#editor-panel').isVisible()?await page.locator('#editor-panel').boundingBox():null;
 const visibleWidth=panel && panel.x>rect.x ? Math.min(rect.width,panel.x-rect.x):rect.width;
 const position={x:visibleWidth*x,y:rect.height*y};
 if(touch)await page.locator('#map').tap({position});else await page.locator('#map').click({position});
 return {x:rect.x+position.x,y:rect.y+position.y};
}
async function draw(page,view,type,{touch=false,end='button'}={}){
 const select=view.locator('[name=geometry_type]');if(await select.isVisible())await select.selectOption(type);
 await view.locator('[data-role=draw],[data-role=place-point]').first().click();
 const zoom=await page.evaluate(()=>map.getZoom());
 await expect(page.locator('#editor-map-interaction')).toBeVisible();
 await expect(view.locator(':scope > .entity-form__body > form [data-role=save]')).toBeDisabled();
 await location(page,0,touch);
 if(type!=='Point'){
  await location(page,1,touch);
  if(type==='Polygon')await location(page,2,touch);
  if(end==='double'){
   const box=await page.locator('#map').boundingBox();await page.mouse.dblclick(box.x+box.width*.64,box.y+box.height*.52);
  }else if(end==='first')await location(page,0,touch);
  else await page.locator('#finish-editor-map-interaction').click();
 }
 await expect(page.locator('#editor-map-interaction')).toBeHidden();await expect(view).toBeVisible();
 expect(await page.evaluate(()=>map.getZoom())).toBe(zoom);
 await expect(view.getByRole('button',{name:/Restaurer|Confirmer le dessin/})).toHaveCount(0);
}
async function save(page,view,resource,method='POST',id=''){
 const response=page.waitForResponse(r=>r.request().method()===method&&new URL(r.url()).pathname===`/api/${resource}${id?'/'+id:''}`);
 await view.locator(':scope > .entity-form__body > form [data-role=save]').click();const r=await response;
 expect(r.status(),await r.text()).toBe(method==='POST'?201:200);return r.json();
}
function listenWrites(page){const writes=[];page.on('request',r=>{if(['POST','PUT','DELETE','PATCH'].includes(r.method())&&/\/api\/(desordres|mobilier|reseaux-techniques|zones-stockage|ouvrages-particuliers)/.test(r.url()))writes.push(r);});return writes;}

test('dessins communs : fin unique, double-clic sans sommet parasite, annulation du remplacement',async({page,request})=>{
 for(const [family,resource,type,end]of [
  ['mobilier','mobilier','Point','button'],['equipement-mesure','equipements-mesure','Point','button'],
  ['reseau-technique','reseaux-techniques','LineString','double'],['ouvrage-particulier','ouvrages-particuliers','LineString','double'],
  ['zone-stockage','zones-stockage','Polygon','first'],['vegetation','vegetation','Polygon','button'],
  ['cheminement','cheminements','LineString','double'],['prestation','prestations','Point','button'],
  ['troncon','troncons','LineString','double'],['troncons-lit','troncons-lit','LineString','double'],
 ]){
  await ready(page);const view=await entry(page,family);const label='Dessin '+randomUUID();await fields(view,family,label);
  await draw(page,view,type,{end});
  // A second acquisition is cancelled without losing the previous local geometry.
  await view.locator('[data-role=draw],[data-role=place-point]').first().click();
  await page.locator('#cancel-editor-map-interaction').click();
  await expect(view.locator('[name=designation]')).toHaveValue(label);
  const saved=await save(page,view,resource);const actual=await(await request.get(`/api/${resource}/${saved.properties?.id||saved.id}`)).json();
  expect(actual.geometry).toEqual(saved.geometry);expect(actual.geometry.type).toBe(type);
  if(type==='LineString')expect(actual.geometry.coordinates).toHaveLength(3);
  if(type==='Polygon')expect(actual.geometry.coordinates[0]).toHaveLength(4);
 }
});

for(const viewport of [{width:390,height:844},{width:844,height:390}])test(`PWA tactile ${viewport.width} : Terminer et Annuler accessibles sur carte`,async({browser})=>{
 const context=await browser.newContext({viewport,isMobile:true,hasTouch:true,baseURL:process.env.DIGUES_E2E_BASE_URL});
 await context.addInitScript(()=>{const old=matchMedia;window.matchMedia=q=>q.includes('display-mode: standalone')?{matches:true,addEventListener(){},removeEventListener(){}}:old(q)});
 const page=await context.newPage();await ready(page);const view=await entry(page,'reseau-technique');await fields(view,'reseau-technique','Tactile '+randomUUID());
 await draw(page,view,'LineString',{touch:true});
 await page.screenshot({path:test.info().outputPath('termine.png')});
 const feature=await save(page,view,'reseaux-techniques');expect(feature.geometry.coordinates).toHaveLength(2);
 await context.close();
});

for(const type of ['Point','LineString','Polygon'])test(`Désordre ${type} : Terminer ne persiste pas, sauvegarde générale atomique et annulation`,async({page,request})=>{
 const geometry={Point:{type,coordinates:[2.35,50.5]},LineString:{type,coordinates:[[2.35,50.5],[2.351,50.502],[2.352,50.5]]},Polygon:{type,coordinates:[[[2.35,50.5],[2.351,50.502],[2.352,50.5],[2.35,50.5]]]}}[type];
 const created=await request.post('/api/desordres',{data:{geometry_type:type,geometry,designation:'Avant '+randomUUID()}});
 expect(created.status(),await created.text()).toBe(201);const original=await created.json(),id=original.properties.id;
 await ready(page);await page.evaluate(async({id,type})=>{const layer=desordreLayersById.get(id);await ({Point:openPointEditor,LineString:openLineEditor,Polygon:openPolygonEditor})[type](id,layer);map.fitBounds(L.geoJSON(layer.feature).getBounds(),{paddingTopLeft:[60,160],paddingBottomRight:[480,60],maxZoom:18,animate:false});},{id,type});
 const view=page.locator('#disorder-create-view'); // locate by stable form owner below
 const form=page.locator('#desordre-create-designation');await expect(form).toBeVisible();
 const owner=page.locator('#'+await form.evaluate(e=>e.closest('.entity-form').id));
 await form.fill('Après '+id);const writes=listenWrites(page);
 const button=type==='Point'?'#start-map-position':type==='LineString'?'#start-line-edit':'#start-desordre-draw';
 await page.locator(button).click();
 if(type==='Point')await location(page,1);
 else{
  // Exercise Leaflet's existing vertex drag using a stable map projection.
  await page.evaluate(id=>{map.stop();map.invalidateSize({pan:false,animate:false});map.fitBounds(desordreLayersById.get(id).getBounds(),{paddingTopLeft:[60,160],paddingBottomRight:[480,60],maxZoom:18,animate:false})},id);
  await page.waitForFunction(()=>!map._animatingZoom&&!map._panAnim?._inProgress);
  await page.waitForFunction(()=>[...document.querySelectorAll('.leaflet-vertex-icon')].some(node=>{const r=node.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===node}));
  const vertices=page.locator('.leaflet-vertex-icon');const index=await vertices.evaluateAll(nodes=>nodes.findIndex(node=>{const r=node.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===node}));
  const handle=vertices.nth(index);await handle.hover();const box=await handle.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+18,box.y+box.height/2+12,{steps:5});await page.mouse.up();
  if(type === 'LineString') {
   const zoom=await page.evaluate(()=>map.getZoom());
   const mapBox=await page.locator('#map').boundingBox();await page.mouse.dblclick(mapBox.x+200,mapBox.y+180);
   expect(await page.evaluate(()=>map.getZoom())).toBe(zoom);
  }else await page.locator('#finish-editor-map-interaction').click();
 }
 await expect(page.locator('#editor-map-interaction')).toBeHidden();expect(writes).toHaveLength(0);
 let actual=await(await request.get('/api/desordres/'+id)).json();expect(actual.geometry).toEqual(original.geometry);expect(actual.properties.designation).toBe(original.properties.designation);
 const saved=await save(page,owner,'desordres','PUT',id);expect(writes).toHaveLength(1);expect(new URL(writes[0].url()).pathname).toBe('/api/desordres/'+id);
 actual=await(await request.get('/api/desordres/'+id)).json();expect(actual.properties.designation).toBe('Après '+id);expect(actual.geometry).toEqual(saved.geometry);expect(actual.geometry).not.toEqual(original.geometry);
 // Another local replacement then form cancellation cannot change the saved SQL.
 await page.locator(button).click();await page.locator('#cancel-editor-map-interaction').click();
 await owner.locator('[name=commentaire]').fill('Brouillon à abandonner');page.once('dialog',d=>d.accept());await owner.locator(':scope > .entity-form__body > form [data-role=cancel]').click();
 actual=await(await request.get('/api/desordres/'+id)).json();expect(actual.geometry).toEqual(saved.geometry);expect(actual.properties.commentaire).not.toBe('Brouillon à abandonner');expect(writes).toHaveLength(1);
});

test('Désordre : le refus de type disparaît avec sa cause sans effacer une erreur indépendante',async({page})=>{
 await ready(page);const view=await entry(page,'desordre');await draw(page,view,'Point');
 const select=view.locator('[name=geometry_type]');await select.selectOption('LineString');
 await expect(view.locator('#desordre-create-message')).toContainText('Annulez le dessin');
 await view.locator('[data-role=draw]').click();await page.locator('#cancel-editor-map-interaction').click();
 // A previous completed geometry is intentionally preserved: it still blocks conversion.
 await expect(select).toHaveValue('Point');
 // An abandoned acquisition with no preceding geometry removes its type guard.
 page.once('dialog',d=>d.accept());await ready(page);const active=await entry(page,'desordre');
 await active.locator('[name=geometry_type]').selectOption('LineString');await active.locator('[data-role=draw]').click();
 await active.locator('[name=geometry_type]').selectOption('Polygon');await expect(active.locator('#desordre-create-message')).toContainText('Annulez le dessin');
 await page.locator('#cancel-editor-map-interaction').click();await expect(active.locator('#desordre-create-message')).not.toContainText('Annulez le dessin');
 // Empty coordinate-only draft exercises removal without discarding another error.
 await ready(page);const fresh=await entry(page,'desordre');await fresh.getByText('X/Y',{exact:true}).click();
 await fresh.locator('#desordre-create-x').fill('1700000');await fresh.locator('#desordre-create-y').fill('9200000');
 await fresh.locator('[name=geometry_type]').selectOption('LineString');await expect(fresh.locator('#desordre-create-message')).toContainText('Annulez le dessin');
 await fresh.locator('#desordre-create-x').fill('');await fresh.locator('#desordre-create-y').fill('');
 await expect(fresh.locator('#desordre-create-message')).not.toContainText('Annulez le dessin');
 await page.evaluate(()=>{desordreCreateMessage.textContent='Erreur indépendante';desordreCreateMessage.classList.add('error')});
 await fresh.locator('[name=geometry_type]').selectOption('LineString');await expect(fresh.locator('#desordre-create-message')).toHaveText('Erreur indépendante');
});

test('Désordre : réponse perdue après commit, sauvegarde unique et rejeu de la même intention',async({page,request})=>{
 const made=await request.post('/api/desordres',{data:{designation:'Accusé '+randomUUID(),geometry_type:'Point',longitude:2.35,latitude:50.5}});
 expect(made.status(),await made.text()).toBe(201);const original=await made.json(),id=original.properties.id;
 await ready(page);await page.evaluate(id=>openPointEditor(id,desordreLayersById.get(id)),id);
 const owner=page.locator('#disorder-form-view');await expect(owner.locator('#desordre-create-designation')).toBeEditable();
 await owner.locator('#desordre-create-designation').fill('Accusé perdu');
 const attempts=[];let committed;
 await page.route(`**/api/desordres/${id}`,async route=>{
  if(route.request().method()!=='PUT')return route.continue();
  attempts.push({id:route.request().headers()['operation-id'],body:route.request().postDataJSON()});
  if(attempts.length===1){const response=await route.fetch();expect(response.status()).toBe(200);committed=await response.json();await route.abort('failed');}
  else await route.continue();
 });
 await page.evaluate(()=>{desordreEditorForm.requestSubmit();desordreEditorForm.requestSubmit()});
 await expect(owner.locator('#desordre-create-message')).toContainText('Résultat non confirmé');
 expect(attempts).toHaveLength(1);expect((await(await request.get(`/api/desordres/${id}`)).json()).properties.designation).toBe('Accusé perdu');
 await expect(owner.locator('#desordre-create-designation')).toBeDisabled();
 const confirmation=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname===`/api/desordres/${id}`);
 await owner.locator('#submit-desordre-create').click();expect((await confirmation).status()).toBe(200);
 await expect(owner.locator('#desordre-create-message')).toContainText('Enregistré');expect(attempts).toHaveLength(2);expect(attempts[1]).toEqual(attempts[0]);
 const actual=await(await request.get(`/api/desordres/${id}`)).json();expect(actual.properties.version).toBe(committed.properties.version);
});

test('Désordre : consulter X/Y ou Lat/Lon ne réécrit ni arrondit une géométrie inchangée',async({page,request})=>{
 for(const type of ['Point','LineString']){
  const geometry=type==='Point'?{type,coordinates:[2.3501234567,50.5001234567]}:
   {type,coordinates:[[2.3501234567,50.5001234567],[2.3512345678,50.5023456789],[2.3523456789,50.5002345678]]};
  const created=await request.post('/api/desordres',{data:{geometry_type:type,geometry,designation:'Précision '+randomUUID()}});
  expect(created.status(),await created.text()).toBe(201);let feature=await created.json();const id=feature.properties.id;
  for(const mode of ['X/Y','Lat/Lon']){
   await ready(page);await page.evaluate(async({id,type})=>await({Point:openPointEditor,LineString:openLineEditor})[type](id,desordreLayersById.get(id)),{id,type});
   const view=page.locator('#disorder-form-view');await expect(view.locator('#desordre-create-designation')).toBeEditable();
   await view.getByText(mode,{exact:true}).click();await view.locator('#desordre-create-commentaire').fill('Attribut '+mode);
   const sent=page.waitForRequest(r=>r.method()==='PUT'&&new URL(r.url()).pathname===`/api/desordres/${id}`);
   const saved=await save(page,view,'desordres','PUT',id);const payload=(await sent).postDataJSON();
   for(const field of ['geometry','x','y','longitude','latitude','line_endpoints','reperage'])expect(payload).not.toHaveProperty(field);
   const actual=await(await request.get(`/api/desordres/${id}`)).json();expect(actual.geometry).toEqual(feature.geometry);expect(actual.geometry).toEqual(saved.geometry);feature=actual;
  }
 }
});


test('messages et actions : géométrie requise résolue et saisie en cours protégée',async({page})=>{
 for(const [family,type,resource]of [['mobilier','Point','mobilier'],['zone-stockage','Polygon','zones-stockage'],['ouvrage-particulier','LineString','ouvrages-particuliers']]){
  await ready(page);const view=await entry(page,family);await fields(view,family,'État '+randomUUID());
  const status=view.locator(':scope > .entity-form__messages [data-role=status]');
  const writes=listenWrites(page);await view.locator(':scope > .entity-form__body > form [data-role=save]').click();
  await expect(status).not.toBeEmpty();expect(writes).toHaveLength(0);
  await draw(page,view,type);await expect(status).toBeEmpty();
  await expect(view).not.toHaveAttribute('data-state','error');
  const saved=await save(page,view,resource);expect(saved.geometry.type).toBe(type);
 }
});
