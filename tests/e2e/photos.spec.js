const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { randomUUID, createHash } = require('node:crypto');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');
const O = '00000000-0000-4000-8000-000000000088', D = '00000000-0000-4000-8000-000000000042';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC', 'base64');
const file = { name:'terrain.png', mimeType:'image/png', buffer:png };
const modal = page => page.locator('#photo-editor-dialog');
const save = page => modal(page).locator('[type=submit]');
const name = (page,n) => modal(page).locator(`[name="${n}"]`);
const observation = () => ({ id:O, designation:'O-42 Constat terrain', parent:{resource:'desordres',id:D,designation:'D-42 Affouillement'}, version:1, photos:[] });
function receipt(photo, req, action='create') { return {...photo,mutation:{schemaVersion:1,eventId:randomUUID(),resource:'photos',action,
  ids:[photo.id],versions:{[photo.id]:photo.version},occurredAt:Date.now(),originClientId:req.headers()['x-digues-app-client-id'],originInstanceId:req.headers()['x-digues-app-instance-id']}}; }
async function setup(context, options={}) {
  const obs=observation(), photos=[], uploads=new Map(), uploaded=new Map(), posts=[];
  await context.route('**/api/**',async route=> {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const req=route.request(), url=new URL(req.url()), path=url.pathname;
    if (path.startsWith('/api/photo-uploads/')) {
      const id=path.split('/')[3];
      if(req.method()==='GET') return route.fulfill({status:uploads.has(id)?200:404,json:uploads.get(id)||{detail:'absent'}});
      if(options.upload) { const handled=await options.upload(route); if(handled) return; }
      const body=req.postDataBuffer(), contentType=req.headers()['content-type'];
      const descriptor={id,observation_id:O,upload_ref:randomUUID(),file_sha256:path.split('/')[4],
        file_size:body.length,mime_type:contentType}; uploads.set(id,descriptor);
      uploaded.set(id,{body,contentType,filename:url.searchParams.get('filename')});
      return route.fulfill({json:descriptor});
    }
    if(path==='/api/photos'&&req.method()==='POST') {
      posts.push({body:req.postDataJSON(),operation:req.headers()['operation-id']});
      const body=req.postDataJSON(); const photo=receipt({...body,version:1,content_available:true,content_url:`/api/photos/${body.id}/content`,variants:{}},req);
      if(options.post) return options.post(route,photo,posts,photos);
      photos.push(photo); return route.fulfill({status:201,json:photo});
    }
    if(path.startsWith('/api/photos/') && path.endsWith('/content')) return route.fulfill({body:png,contentType:'image/png'});
    if(path.startsWith('/api/photos/') && req.method()==='PUT') {
      const old=photos.find(p=>path.endsWith(p.id)); const photo=receipt({...old,...req.postDataJSON(),version:old.version+1},req,'update');
      photos.splice(photos.indexOf(old),1,photo); return route.fulfill({json:photo});
    }
    let json={};
    if(path==='/api/config') json={legacy_single_database_enabled: true, show_uuid:false,
      photo_quality_policy:photoQualityPolicy()};
    else if(path==='/api/systemes-endiguement') json={systemes:[]};
    else if(path==='/api/desordres') json={type:'FeatureCollection',features:[{type:'Feature',geometry:{type:'Point',coordinates:[2,46]},properties:{id:D,designation:'D-42',version:1,valid:true,troncon_ids:[]}}]};
    else if(path===`/api/desordres/${D}`) json={type:'Feature',geometry:{type:'Point',coordinates:[2,46]},properties:{id:D,designation:'D-42',version:1,valid:true,troncon_ids:[]}};
    else if(path===`/api/desordres/${D}/observations`) json={parent:obs.parent,observations:[{...obs,photo_count:photos.length}]};
    else if(path===`/api/observations/${O}`) json={...obs,photos};
    else if(path.startsWith('/api/referentiels/')) json={references:[],types:[],categories:[]};
    else if(path==='/api/troncons/options') json={troncons:[]};
    else if(['/api/troncons','/api/territoire-administratif'].includes(path)) json={type:'FeatureCollection',features:[]};
    return route.fulfill({json});
  });
  return {obs,photos,posts,uploads,uploaded};
}
async function open(page) {
  await page.goto('/carte'); await page.locator('.desordre-point-marker').click();
  await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click(); await page.locator('.observation-row').click();
  await page.locator('#add-observation-photo').click(); await expect(save(page)).toBeEnabled();
}

test('ajout depuis Observation, fichier/capture, UUID, plusieurs photos, galerie et visionneuse',async({context,page})=>{
  const {posts,uploaded}=await setup(context); await open(page);
  await expect(modal(page).locator('[data-context]')).toContainText('O-42 Constat terrain');
  await expect(name(page,'camera')).toHaveAttribute('capture','environment');
  await name(page,'file').setInputFiles(file); const id=await modal(page).getAttribute('data-photo-id');
  expect(posts).toHaveLength(0); expect(id).toMatch(/^[a-f0-9-]{36}$/);
  await expect(modal(page).locator('[data-preview]')).toBeVisible();
  await name(page,'designation').fill('Photo une'); await save(page).click();
  await expect(modal(page).locator('[role=status]')).toContainText('Photo ajoutée');
  await expect(modal(page).locator('[data-prepare-local]')).toBeHidden();
  expect(posts[0].body.id).toBe(id); expect(posts[0].body.observation_id).toBe(O);
  const firstUpload=uploaded.get(id);
  expect(firstUpload.contentType).toContain('image/jpeg'); expect(firstUpload.filename).toBe('terrain.jpg');
  expect(posts[0].body.file_size).toBe(firstUpload.body.length);
  expect(posts[0].body.file_sha256).toBe(createHash('sha256').update(firstUpload.body).digest('hex'));
  expect(firstUpload.body.subarray(0,2)).toEqual(Buffer.from([0xff,0xd8]));
  await expect(page.locator('#photos-count')).toHaveText('1 photo(s)');
  await modal(page).locator('[data-another]').click(); await expect(save(page)).toBeEnabled();
  await name(page,'camera').setInputFiles(file); await save(page).click();
  await expect(modal(page).locator('[role=status]')).toContainText('Photo ajoutée');
  expect(posts).toHaveLength(2); expect(posts[1].body.id).not.toBe(id);
  const secondUpload=uploaded.get(posts[1].body.id);
  expect(secondUpload.contentType).toContain('image/jpeg');
  expect(posts[1].body.file_sha256).toBe(createHash('sha256').update(secondUpload.body).digest('hex'));
  await modal(page).locator('[data-close]').click();
  await expect(page.locator('#photos-count')).toHaveText('2 photo(s)');
  await page.locator('.photo-card').first().click(); await expect(page.locator('#lightbox-image')).toBeVisible();
  await expect.poll(()=>page.locator('#lightbox-image').evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);
  await page.locator('#edit-photo-metadata').click(); await expect(save(page)).toBeEnabled();
  await expect(modal(page).locator('[data-file-fields]')).toBeHidden();
  await name(page,'libelle').fill('Légende modifiée'); await save(page).click();
  await expect(modal(page).locator('[role=status]')).toContainText('Photo enregistrée');
  await expect(page.locator('#observation-photos')).toContainText('Légende modifiée');
  expect(await page.evaluate(()=>OfflineRuntime.store.listOperations())).toEqual([]);
});

test('double soumission, réponse perdue après commit et retry sans deuxième transfert',async({context,page})=>{
  let release, first;
  const {posts,uploads}=await setup(context,{post:async(route,photo,posts,photos)=>{
    if(posts.length===1){first=photo;photos.push(photo);await new Promise(resolve=>release=resolve);return route.abort('failed');}
    return route.fulfill({status:201,json:first});
  }});
  await open(page);await name(page,'file').setInputFiles(file);await name(page,'libelle').fill('Conservée');await save(page).click();
  await expect.poll(()=>posts.length).toBe(1);await expect(save(page)).toBeDisabled();
  await modal(page).locator('form').evaluate(form=>form.dispatchEvent(new Event('submit',{cancelable:true})));
  expect(posts).toHaveLength(1);release();
  await expect(modal(page).locator('[role=status]')).toContainText('Résultat non confirmé');
  await expect(modal(page).locator('[data-prepare-local]')).toBeDisabled();
  await expect(name(page,'libelle')).toHaveValue('Conservée');await expect(name(page,'libelle')).toBeDisabled();
  await save(page).click();await expect(modal(page).locator('[role=status]')).toContainText('Photo ajoutée');
  expect(posts[1]).toEqual(posts[0]);expect(uploads.size).toBe(1);
});

test('échec upload conserve le fichier et les métadonnées ; succès secondaire défaillant reste confirmé',async({context,page})=>{
  let failed=false;
  await setup(context,{upload:async route=>{if(!failed){failed=true;await route.fulfill({status:503,json:{detail:'disque indisponible'}});return true;}}});
  await open(page);await name(page,'file').setInputFiles(file);await name(page,'libelle').fill('Saisie');await save(page).click();
  await expect(modal(page).locator('[role=status]')).toContainText('Transfert non confirmé');
  await expect(name(page,'libelle')).toHaveValue('Saisie');await expect(name(page,'libelle')).toBeEnabled();
  expect(await name(page,'file').evaluate(input=>input.files.length)).toBe(1);
  await page.evaluate(()=>{ResourceMutationController.publishMutation=async()=>{throw new Error('refresh failed');};});
  await save(page).click();await expect(modal(page).locator('[role=status]')).toContainText('Photo enregistrée. Actualisation');
  await expect(modal(page).locator('[data-another]')).toBeVisible();
});

for(const viewport of [{name:'Samsung A13',width:360,height:800},{name:'Dell Latitude',width:1366,height:768}]) test(`formulaire responsive ${viewport.name}`,async({context,page})=>{
  await page.setViewportSize({width:viewport.width,height:viewport.height});await setup(context);
  await page.goto(viewport.width < 700 ? '/carte' : '/base');
  await page.waitForFunction(() => Boolean(window.PhotoMedia));
  await page.evaluate(obs=>PhotoMedia.openCreate(obs),observation());await expect(save(page)).toBeEnabled();
  await expect(modal(page).locator('.editor-title h2')).toHaveText('Nouvelle photo');
  const contextGap=await modal(page).evaluate(element=>{
    const context=element.querySelector('.dialog-context').getBoundingClientRect();
    const form=element.querySelector('form').getBoundingClientRect();
    return form.top-context.bottom;
  });
  expect(contextGap).toBeGreaterThanOrEqual(0);expect(contextGap).toBeLessThanOrEqual(10);
  const sourceRows=await modal(page).locator('.photo-source-action').evaluateAll(actions=>actions.map(action=>action.getBoundingClientRect().top));
  expect(sourceRows).toHaveLength(2);expect(Math.abs(sourceRows[0]-sourceRows[1])).toBeLessThan(1);
  const actionRows=await modal(page).locator('.dialog-actions>button').evaluateAll(actions=>actions.map(action=>action.getBoundingClientRect().top));
  expect(actionRows).toHaveLength(2);expect(Math.abs(actionRows[0]-actionRows[1])).toBeLessThan(1);
  await name(page,'file').setInputFiles(file);
  await page.screenshot({path:`audits/2026-09-17-photos-lot3a-preuves/photo-${viewport.width}.png`});
  const bounds=await modal(page).boundingBox();expect(bounds.width).toBeLessThanOrEqual(viewport.width);expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(await modal(page).evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await save(page).click();await expect(modal(page).locator('[role=status]')).toContainText('Photo ajoutée');
});

test('reçu photos BroadcastChannel : galerie et déduplication',async({context,page})=>{
  const {photos}=await setup(context);await open(page);await modal(page).locator('[data-close]').click();
  const other=await context.newPage();await other.goto('/base');
  await other.waitForFunction(() => Boolean(window.ResourceMutationController));
  const id=randomUUID(), event={schemaVersion:1,eventId:randomUUID(),resource:'photos',action:'create',ids:[id],versions:{[id]:1},occurredAt:Date.now(),originClientId:'test-client',originInstanceId:'other'};
  photos.push({id,observation_id:O,version:1,libelle:'Autre onglet',content_available:false});
  await page.evaluate(()=>{window.photoEvents=[];ResourceMutationController.registerInvalidator('open-detail',event=>{if(event.resource==='photos')photoEvents.push(event);});});
  await other.evaluate(event=>ResourceMutationController.publishMutation(event,{applyLocally:true}),event);
  await expect(page.locator('#photos-count')).toHaveText('1 photo(s)');await expect(page.locator('#observation-photos')).toContainText('Autre onglet');
  await expect.poll(()=>page.evaluate(()=>photoEvents.length)).toBe(1);
  expect(await page.evaluate(event=>ResourceMutationController.dispatch(event).accepted,event)).toBe(false);
});

test('Photo réelle : upload, commit perdu, SSE create/update et original permanent',async({browser,request,baseURL})=>{
  test.skip(process.env.DIGUES_E2E_OCC!=='1','PostgreSQL et stockage locaux requis');test.setTimeout(90000);
  const disorder=await(await request.post('/api/desordres',{data:{designation:'Photo SSE parent',geometry:{type:'Point',coordinates:[2,46]}}})).json();
  const obs=await(await request.post('/api/observations',{data:{id:randomUUID(),parent:{resource:'desordres',id:disorder.properties.id},designation:'Observation Photo SSE'},headers:{'Operation-Id':randomUUID()}})).json();
  const a=await browser.newContext({baseURL}), b=await browser.newContext({baseURL});
  try {
    const author=await a.newPage(),reader=await b.newPage();
    await Promise.all([author.goto('/base'),reader.goto('/carte')]);
    await reader.waitForFunction(id=>typeof desordreLayersById!=='undefined'&&desordreLayersById.has(id),disorder.properties.id);
    await reader.evaluate(id=>openPointEditor(id,desordreLayersById.get(id)),disorder.properties.id);
    await reader.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click();await reader.locator('.observation-row').click();
    await author.evaluate(obs=>PhotoMedia.openCreate(obs),obs);await expect(save(author)).toBeEnabled();
    await name(author,'file').setInputFiles(file);await name(author,'libelle').fill('Original vérifié');
    const head=(await(await request.get('/api/resource-events/head')).json()).cursor;
    let first,attempts=[];
    await a.route('**/api/photos',async route=>{
      attempts.push({operation:route.request().headers()['operation-id'],body:route.request().postDataJSON()});
      const response=await route.fetch();expect(response.status()).toBe(201);
      if(attempts.length===1){first=await response.json();await route.abort('failed');}
      else {expect(await response.json()).toEqual(first);await route.fulfill({response});}
    });
    await save(author).click();await expect(modal(author).locator('[role=status]')).toContainText('Résultat non confirmé');
    await expect(reader.locator('#photos-count')).toHaveText('1 photo(s)',{timeout:15000});
    await save(author).click();await expect(modal(author).locator('[role=status]')).toContainText('Photo ajoutée');
    expect(attempts[1]).toEqual(attempts[0]);expect(first.version).toBe(1);
    const stored=await(await request.get(first.content_url)).body();
    expect(stored.subarray(0,2)).toEqual(Buffer.from([0xff,0xd8]));
    expect(createHash('sha256').update(stored).digest('hex')).toBe(attempts[0].body.file_sha256);
    const update=await request.put(`/api/photos/${first.id}`,{data:{libelle:'Mise à jour distante'},headers:{'Operation-Id':randomUUID(),'If-Match':'"1"'}});
    expect(update.status()).toBe(200);expect((await update.json()).version).toBe(2);
    await expect(reader.locator('#observation-photos')).toContainText('Mise à jour distante',{timeout:15000});
    const localisation=await request.put(`/api/photos/${first.id}/localisation`,{data:{mode:'point',srid:4326,coordinates:[2.001,46.002]},headers:{'Operation-Id':randomUUID(),'If-Match':'"2"'}});
    expect(localisation.status()).toBe(200);expect((await localisation.json()).version).toBe(3);
    // The PUT response does not imply that the other tab integrated its SSE.
    // Wait for the authoritative gallery before clicking a card it may replace.
    await reader.waitForFunction(id => typeof currentObservationPhotos !== 'undefined'
      && currentObservationPhotos.some(photo => photo.id === id && photo.version === 3), first.id);
    await reader.locator('.photo-card').first().click();
    await expect(reader.locator('#photo-lightbox')).toBeVisible();
    await reader.locator('#edit-photo-localisation').click();
    await expect(reader.locator('#photo-localisation-dialog [type=submit]')).toBeEnabled();
    await expect(reader.locator('#photo-localisation-dialog [data-summary]')).toContainText('Point');
    const authoritative=await(await request.get(`/api/photos/${first.id}`)).json();
    expect(authoritative.localisation.geometry_4326.coordinates[0]).toBeCloseTo(2.001,6);
    await reader.locator('#photo-localisation-dialog [name=x]').fill('1700999');
    const remote=await request.put(`/api/photos/${first.id}/localisation`,{data:{mode:'point',srid:4326,coordinates:[2.002,46.003]},headers:{'Operation-Id':randomUUID(),'If-Match':'"3"'}});
    expect(remote.status()).toBe(200);
    await expect(reader.locator('#photo-localisation-dialog [role=status]')).toContainText('brouillon',{timeout:15000});
    await expect(reader.locator('#photo-localisation-dialog [name=x]')).toHaveValue('1700999');
    await reader.locator('#photo-localisation-dialog [type=submit]').click();
    await expect(reader.locator('#photo-localisation-dialog [role=status]')).toContainText('Conflit');
    const events=(await(await request.get(`/api/resource-events?after=${head}&limit=100`)).json()).events.filter(row=>row.event.resource==='photos'&&row.event.ids.includes(first.id));
    expect(events).toHaveLength(4);expect(events.map(row=>row.event.action)).toEqual(['create','update','update','update']);
    expect(events[0].event).toEqual(first.mutation);
  }finally{await a.close();await b.close();}
});

for (const [status,code,text] of [[422,'invalid','Validation refusée'],[409,'photo-id-collision','Collision d’UUID']]) {
  test(`Photo refus ${status} : saisie et contexte conservés`, async ({context,page}) => {
    await setup(context,{post:route=>route.fulfill({status,json:{detail:{code,message:'Refus de test'}}})});
    await open(page); await name(page,'file').setInputFiles(file); await name(page,'libelle').fill('Brouillon Photo');
    await save(page).click(); await expect(modal(page).locator('[role=status]')).toContainText(text);
    await expect(name(page,'libelle')).toHaveValue('Brouillon Photo'); await expect(modal(page).locator('[data-context]')).toContainText(O);
    if(status===409) await expect(save(page)).toBeDisabled(); else await expect(name(page,'libelle')).toBeEnabled();
  });
}

test('édition concurrente Photo : If-Match initial et brouillon conservés', async ({context,page}) => {
  await setup(context); await page.goto('/base');
  await page.waitForFunction(() => Boolean(window.PhotoMedia));
  const id=randomUUID();
  await page.evaluate(({obs,id})=>PhotoMedia.openEdit({id,observation_id:obs.id,version:3,libelle:'Avant'},obs),{obs:observation(),id});
  await expect(save(page)).toBeEnabled(); await name(page,'libelle').fill('Mon brouillon');
  await context.route(`**/api/photos/${id}`,route=>{
    expect(route.request().headers()['if-match']).toBe('"3"');
    return route.fulfill({status:409,json:{detail:{code:'version-conflict',message:'Version serveur 4'}}});
  });
  await save(page).click(); await expect(modal(page).locator('[role=status]')).toContainText('Conflit');
  await expect(name(page,'libelle')).toHaveValue('Mon brouillon'); await expect(save(page)).toBeDisabled();
});
