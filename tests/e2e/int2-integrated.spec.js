const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const fixture = process.env.DIGUES_INT2_FIXTURE
  ? JSON.parse(fs.readFileSync(process.env.DIGUES_INT2_FIXTURE, 'utf8')) : null;
test.skip(!fixture, 'Requires tests.run_postgres webapp.tests.test_int2_browser_postgres');
test.setTimeout(120000);
test.use({actionTimeout:15000});

async function login(page, target = '/carte?base=base_0&lat=48.85&lon=2.35&zoom=15', user = 'editor_alpha') {
  await page.goto(target);
  await page.locator('#identity-gate').getByLabel('Identifiant', { exact: true }).fill(user);
  await page.locator('#identity-gate').getByLabel('Mot de passe', { exact: true }).fill(fixture.password);
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click();
  await expect(page.locator('#identity-gate')).toBeHidden({ timeout: 30000 });
  await page.waitForFunction(() => window.BusinessRuntime?.isActive() && window.OfflineRuntime);
  if (target.startsWith('/carte')) await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady && typeof basemapSelector !== 'undefined' && basemapSelector?.getActive());
}

test.beforeEach(async ({ context }) => {
  // Block ALL nonlocal destinations, including the historical OSM fallback.
  await context.route(/^https?:\/\/(?!127\.0\.0\.1(?::|\/)|localhost(?::|\/))/, route => route.abort());
  const reset = await context.request.post('/int2/reset-login-attempts', {
    headers: { Authorization: `Bearer ${fixture.login_reset_token}` },
  });
  expect(reset.status()).toBe(204);
});

test('real Carte/Base, CAC geometry, relations, basemap changes and URL recovery', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page, `/carte?base=base_0&entity=cheminements&id=${fixture.path}`);
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#cheminement-editor-form [name="designation"]')).toHaveValue('INT2 accès polygonal');
  expect(await page.evaluate(() => offlineBasemapUi !== null)).toBe(true);
  await page.evaluate(() => { window.__int2Map = map; window.__initialHistory = history.length; });
  await page.locator('#cheminement-editor-form [name="designation"]').fill('Brouillon conservé INT2');
  for (const id of ['test-detail', 'test-aerial', 'test-standard']) {
    expect(await page.evaluate(id => basemapSelector.select(id), id)).toBe(true);
    expect(await page.evaluate(() => map === window.__int2Map)).toBe(true);
    await expect(page.locator('#cheminement-editor-form [name="designation"]')).toHaveValue('Brouillon conservé INT2');
    await expect(page.locator('#editor-panel .relation-search-selected')).toContainText('INT2 Désordre');
  }
  const result = await page.evaluate(async id => {
    const before = await (await BusinessRuntime.fetch(`/api/cheminements/${id}`)).json();
    const response = await BusinessRuntime.fetch(`/api/cheminements/${id}`, { method:'PUT',
      headers:{ 'Content-Type':'application/json', 'If-Match': `"${before.properties.version}"`, 'Operation-Id': crypto.randomUUID() },
      body:JSON.stringify({ commentaire:'INT2 attributaire' }) });
    const after = await response.json();
    return { status:response.status, before, after };
  }, fixture.path);
  expect(result.status).toBe(200);
  expect(result.after.geometry).toEqual(result.before.geometry);
  expect(result.after.properties.desordre_ids).toEqual([fixture.disorder]);
  const inverse = await page.evaluate(async id => (await BusinessRuntime.fetch(`/api/desordres/${id}/objets-associes?limit=1`)).json(), fixture.disorder);
  expect(inverse.items).toHaveLength(1); expect(inverse.has_more).toBe(true);
  expect(await page.evaluate(() => history.length === window.__initialHistory)).toBe(true);
  await page.goto(`/base?base=base_0&entity=cheminements&id=${fixture.path}`);
  await expect(page.locator('#data-entity-detail')).toBeVisible();
  await expect(page.locator('#data-entity-detail')).toContainText('INT2 accès polygonal');
  await page.goto(`/carte?base=base_0&entity=vegetation&id=${fixture.path}`);
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.evaluate(() => map.setView([48, 2], 12));
  await expect.poll(() => new URL(page.url()).searchParams.get('lat')).toBe('48.000000');
  expect(errors).toEqual([]);
});

test('real service worker, package interruption/resume, Leaflet offline and shell update', async ({ page, context }) => {
  const failedShellResponses = [];
  context.on('response', response => {
    if (response.status() >= 400 && new URL(response.url()).pathname.startsWith('/static/')) {
      failedShellResponses.push(`${response.status()} ${new URL(response.url()).pathname}`);
    }
  });
  await login(page);
  await expect.poll(async () => ({
    ...await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.getRegistration('/');
      return {
        scope: registration?.scope ?? null,
        installing: registration?.installing?.state ?? null,
        waiting: registration?.waiting?.state ?? null,
        active: registration?.active?.state ?? null,
        controller: Boolean(navigator.serviceWorker.controller),
      };
    }),
    failedShellResponses: failedShellResponses.slice(0, 5),
  }), { timeout: 30000, message: 'Le service worker doit être activé et contrôler la page.' })
    .toMatchObject({ scope: new URL('/', page.url()).href, active: 'activated', controller: true,
      failedShellResponses: [] });
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => offlineBasemapUi?.getState().then(Boolean))).toBe(true);
  await page.evaluate(async () => {
    const draftStore=PwaGeometryDrafts.createStore({indexedDB,runtime:BusinessRuntime});
    await draftStore.write('desordres:create:new',{schemaVersion:1,contextKey:BusinessRuntime.context.key,
      key:'desordres:create:new',trackId:crypto.randomUUID(),revision:1,territoryKey:'synthetic',
      type:'LineString',phase:'paused',points:[{longitude:2.35,latitude:48.85,timestamp:1,accuracy:5}]});
    window.__packages = OfflineBasemapStore.createStore();
    window.__manager = OfflineBasemapDownload.createManager({ store: __packages,
      fetchImpl:fetch.bind(window), origin:location.origin, storageManager:navigator.storage });
    const catalogue = await (await fetch('/api/basemaps')).json();
    const entry = catalogue.basemaps[0];
    const descriptor = await Basemap.loadStrict(entry.delivery.url);
    const planned = await __manager.prepare({ entry, descriptor, contextKey:BusinessRuntime.context.key,
      territoryId:'00000000-0000-4000-8000-000000000001', bounds:[2.32,48.83,2.38,48.87], zoomMin:13,zoomMax:15 });
    window.__packageId = planned.manifest.id;
    await fetch('/int2/control', { method:'POST', body:JSON.stringify({ delay:.08,reset:true }) });
    window.__run = __manager.run(__packageId, BusinessRuntime.context.key);
  });
  await expect.poll(() => page.evaluate(async () => (await __packages.get(__packageId,BusinessRuntime.context.key)).verifiedCount)).toBeGreaterThan(0);
  await page.evaluate(() => __manager.pause(__packageId, BusinessRuntime.context.key));
  const partial = await page.evaluate(async () => __packages.get(__packageId,BusinessRuntime.context.key));
  expect(partial.state).toBe('interrupted'); expect(partial.verifiedCount).toBeLessThan(partial.expectedCount);
  await page.evaluate(async () => {
    await fetch('/int2/control',{method:'POST',body:JSON.stringify({delay:0,reset:true})});
    await __manager.run(__packageId,BusinessRuntime.context.key);
  });
  const ready = await page.evaluate(async () => __packages.get(__packageId,BusinessRuntime.context.key));
  expect(ready.state).toBe('ready'); expect(ready.verifiedCount).toBe(ready.expectedCount);
  const requests = await page.evaluate(async () => (await (await fetch('/int2/control',{method:'POST',body:'{}'})).json()).requests);
  expect(requests.length).toBe(ready.expectedCount-partial.verifiedCount);
  const key = ready.expectedKeys[0];
  const local = `/__offline-basemaps/${ready.id}/${key}`;
  await context.setOffline(true);
  await page.evaluate(() => basemapSelector.refresh());
  await expect.poll(() => page.evaluate(() => basemapSelector.getActive()?.url)).toContain('/__offline-basemaps/');
  await expect.poll(() => page.locator('#map .leaflet-tile-loaded').count()).toBeGreaterThan(0);
  expect(await page.evaluate(async url => (await fetch(url)).status,local)).toBe(200);
  expect(await page.evaluate(async id => (await fetch(`/__offline-basemaps/${id}/15/0/0`)).status,ready.id)).toBe(404);
  await page.evaluate(() => map.setView([48.85,2.35],14));
  await expect.poll(() => page.locator('#map .leaflet-tile-loaded').count()).toBeGreaterThan(0);
  await page.reload();
  await expect(page.locator('#map')).toBeVisible();
  await expect.poll(() => page.locator('#map .leaflet-tile-loaded').count()).toBeGreaterThan(0);
  const precache = await page.evaluate(async () => {
    const shell = (await caches.keys()).find(name => name.startsWith('digues-shell-'));
    return (await (await caches.open(shell)).keys()).map(request => new URL(request.url).pathname);
  });
  for (const module of ['sirs-photo-transfer','disorder-related-objects','offline-basemap-ui','basemap-selector','business-context'])
    expect(precache).toContain(`/static/js/${module}.js`);
  expect(precache.some(path => path.startsWith('/api/'))).toBe(false);
  await context.setOffline(false);
  // Register a genuinely new worker generation; activate without deleting IDB or packages.
  await page.evaluate(async () => {
    await navigator.serviceWorker.register('/service-worker.js?v=int2-update');
  });
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration()).waiting))).toBe(true);
  await page.evaluate(async () => (await navigator.serviceWorker.getRegistration()).waiting.postMessage({type:'SKIP_WAITING'}));
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL)).toContain('int2-update');
  expect(await page.evaluate(async url => (await fetch(url)).status,local)).toBe(200);
  const databaseName = await page.evaluate(() => BusinessRuntime.databaseName);
  await page.evaluate(async id => {
    const store=OfflineBasemapStore.createStore(); await store.remove(id,BusinessRuntime.context.key);
  },ready.id);
  expect(await page.evaluate(async name => (await indexedDB.databases()).some(db=>db.name===name),databaseName)).toBe(true);
  const draft=await page.evaluate(()=>PwaGeometryDrafts.createStore({indexedDB,runtime:BusinessRuntime}).read('desordres:create:new'));
  expect(draft.phase).toBe('paused');
  expect(draft.points).toEqual([{longitude:2.35,latitude:48.85,timestamp:1,accuracy:5}]);
});

for (const [category,key,form,label] of [
  ['vegetation','vegetation','vegetation','INT2 Végétation'],
  ['amenagements_hydrauliques','ah','amenagement-hydraulique','INT2 AH'],
  ['prestations','prestation','prestation','INT2 Prestation'],
]) test(`targeted navigation ${category}: incoming, Base and inverse references`, async ({page})=>{
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const id=fixture[key];
  await login(page,`/carte?base=base_0&entity=${category}&id=${id}`);
  const field=page.locator(`#${form}-editor-form [name="designation"]`);
  await expect(field).toHaveValue(label);
  await expect.poll(()=>page.evaluate(()=>mapEntityNavigationComplete)).toBe(true);
  await page.evaluate(()=>BusinessRuntime.setUrlMode('object'));
  await expect.poll(()=>new URL(page.url()).searchParams.get('entity')).toBe(category);
  // Updating the existing panel must publish the canonical category, including AH.
  await page.evaluate(()=>businessEditorPanel.update(activeBusinessEditor,{subtitle:'INT2'}));
  await expect.poll(()=>new URL(page.url()).searchParams.get('entity')).toBe(category);
  await page.goto(`/base?base=base_0&entity=${category}&id=${id}`);
  await expect(page.locator('#data-entity-detail')).toContainText(label);
  await page.locator('#data-entity-detail-map').click();
  await expect(page).toHaveURL(/\/carte\?base=base_0/);
  await expect(field).toHaveValue(label);
  if(category!=='prestations') {
    await page.evaluate(id=>desordreLayersById.get(id).fire('click'),fixture.disorder);
    const inverse=page.locator('#desordre-associated-objects');
    await expect(inverse).toBeVisible();
    await inverse.getByRole('button',{name:new RegExp(`^Ouvrir ${label} — `)}).click();
    await expect(field).toHaveValue(label);
    await expect(inverse).toBeHidden();
    await expect.poll(()=>new URL(page.url()).searchParams.get('entity')).toBe(category);
  }
  expect(new URL(page.url()).searchParams.get('base')).toBe('base_0');
  expect(errors).toEqual([]);
});

test('expired lease and changed identity refuse local tiles without purging packages', async ({ page }) => {
  await login(page);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  const local = await page.evaluate(async () => {
    const store = OfflineBasemapStore.createStore();
    const manager = OfflineBasemapDownload.createManager({store,fetchImpl:fetch.bind(window),origin:location.origin,storageManager:navigator.storage});
    const entry=(await (await fetch('/api/basemaps')).json()).basemaps[0];
    const {manifest}=await manager.prepare({entry,descriptor:await Basemap.loadStrict(entry.delivery.url),
      contextKey:BusinessRuntime.context.key,territoryId:'00000000-0000-4000-8000-000000000001',bounds:[2.34,48.84,2.36,48.86],zoomMin:10,zoomMax:10});
    await manager.run(manifest.id,BusinessRuntime.context.key);
    return `/__offline-basemaps/${manifest.id}/${manifest.expectedKeys[0]}`;
  });
  expect(await page.evaluate(async url=>(await fetch(url)).status,local)).toBe(200);
  // Expire the captured lease clock; the timer has not yet run. isActive must fence immediately.
  await page.evaluate(() => { window.__realNow=Date.now; Date.now=()=>__realNow()+10*86400000; });
  expect(await page.evaluate(async url=>(await fetch(url)).status,local)).toBe(403);
  await page.evaluate(() => { Date.now=__realNow; localStorage.setItem('digues-app:identity-signal-v1','different-account'); });
  expect(await page.evaluate(async url=>(await fetch(url)).status,local)).toBe(403);
  expect((await page.evaluate(()=>caches.keys())).some(name=>name.startsWith('digues-basemap-package-'))).toBe(true);
});

test('real admin cache route, controller lifecycle, CSRF and revision', async ({ page, baseURL }) => {
  const admin = baseURL.replace('127.0.0.1', 'localhost');
  await page.goto(`${admin}/caches`);
  await page.getByLabel('Identifiant', { exact: true }).fill('superadmin');
  await page.getByLabel('Mot de passe', { exact: true }).fill(fixture.password);
  await page.getByRole('button', { name:'Se connecter',exact:true }).click();
  await expect(page).toHaveURL(`${admin}/caches`);
  await expect(page.locator('#cache-funds .cache-fund')).toHaveCount(3);
  const reads=[]; page.on('request', request=>{
    if(request.method()==='GET' && request.url().endsWith('/api/admin/basemap-caches')) reads.push(request.url());
  });
  await page.getByRole('link',{name:'Structures',exact:true}).click();
  await expect(page).toHaveURL(`${admin}/structures`);
  await page.getByRole('link',{name:'Caches cartographiques',exact:true}).click();
  await expect(page.locator('#cache-funds .cache-fund')).toHaveCount(3);
  expect(reads).toHaveLength(1);
  const result=await page.evaluate(async()=>{
    const endpoint='/api/admin/basemap-caches';
    const original=await (await fetch(endpoint)).json();
    const body={expected_revision:original.status.revision, global_settings:{warning_percent:20}};
    const denied=await fetch(endpoint,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const options={method:'PATCH',headers:{'Content-Type':'application/json','X-Digues-CSRF':'1'},body:JSON.stringify(body)};
    const ok=await fetch(endpoint,options), stale=await fetch(endpoint,options);
    const next=await (await fetch(endpoint)).json();
    return {denied:denied.status,ok:ok.status,stale:stale.status,warning:next.status.global.warning_percent, serialized:JSON.stringify(next)};
  });
  expect(result.denied).toBe(403); expect(result.ok).toBe(200); expect(result.stale).toBe(409);
  expect(result.warning).toBe(20); expect(result.serialized).not.toContain('/home/');
});

test('browser A4 PNG and A3 PDF use the active synthetic fund; missing tiles fail', async ({page})=>{
  await login(page);
  await page.waitForFunction(()=>basemapSelector?.getActive()?.id==='test-standard');
  for(const [format,output] of [['A4','png'],['A3','pdf']]) {
    await page.evaluate(()=>basemapSelector.select('test-detail'));
    const download=page.waitForEvent('download');
    await page.evaluate(async ({format,output})=>{
      await MapPrint.exportMap({L,sourceBounds:map.getBounds(),state:currentMapPrintState(),
        options:{format,output,orientation:'landscape',legend:true,scale:true,north:true},
        tileUrl:basemap.url,tileOptions:basemap.options,attribution:basemap.options.attribution,tileFormat:'webp'});
    },{format,output});
    const file=await download;
    expect(file.suggestedFilename()).toMatch(new RegExp(`\\.${output}$`));
    expect(await file.failure()).toBeNull();
  }
  await page.evaluate(()=>fetch('/int2/control',{method:'POST',body:JSON.stringify({fail:true})}));
  const message=await page.evaluate(async()=>{
    try { await MapPrint.exportMap({L,sourceBounds:map.getBounds(),state:currentMapPrintState(),
      options:{format:'A4',output:'png',orientation:'landscape'},tileUrl:basemap.url,
      tileOptions:basemap.options,attribution:basemap.options.attribution,tileFormat:'webp'}); return 'unexpected success'; }
    catch(error){return error.message;}
    finally{await fetch('/int2/control',{method:'POST',body:JSON.stringify({fail:false})});}
  });
  expect(message).toMatch(/404|tuile/i);
  await expect(page.locator('.map-print-composition')).toHaveCount(0);
});

test('PWA GPS line and polygon drafts survive synthetic basemap changes', async ({context,page})=>{
  await context.addInitScript(()=>{
    const original=window.matchMedia.bind(window);
    window.matchMedia=query=>query==='(display-mode: standalone)' ? {matches:true,addEventListener(){}} : original(query);
    window.__gps=[];
    Object.defineProperty(navigator,'geolocation',{configurable:true,value:{
      getCurrentPosition(){},watchPosition(resolve){window.__gps.push(resolve);return window.__gps.length;},clearWatch(){}
    }});
  });
  await page.setViewportSize({width:390,height:844});
  await login(page);
  for(const type of ['LineString','Polygon']) {
    if(type==='Polygon') {
      page.once('dialog',dialog=>dialog.accept());
      await page.reload();
      await page.waitForFunction(()=>typeof mapViewportReady!=='undefined' && mapViewportReady && basemapSelector?.getActive());
    }
    await page.locator('#toggle-create-menu').click();
    await page.locator('[data-create-type="desordre"]').click();
    await page.locator('#desordre-create-geometry-type').selectOption(type);
    await page.locator('#desordre-create-designation').fill(`GPS ${type}`);
    const tracker=page.locator('[data-gps-owner="desordre-create-geometry"]');
    await tracker.locator('[data-gps="start"]').click();
    await expect.poll(()=>page.evaluate(()=>__gps.length)).toBeGreaterThan(0);
    await page.evaluate(()=>__gps.at(-1)({coords:{longitude:2.35,latitude:48.85,accuracy:7},timestamp:Date.now()-10000}));
    expect(await page.evaluate(()=>basemapSelector.select('test-detail'))).toBe(true);
    await expect(tracker.locator('[data-gps="pause"]')).toBeEnabled();
    await page.evaluate(()=>__gps.at(-1)({coords:{longitude:2.3504,latitude:48.85,accuracy:7},timestamp:Date.now()-5000}));
    if(type==='Polygon') await page.evaluate(()=>__gps.at(-1)({coords:{longitude:2.3504,latitude:48.8504,accuracy:7},timestamp:Date.now()-1000}));
    await tracker.locator('[data-gps="finish"]').click();
    await tracker.locator('[data-gps="confirm"]').click();
    await page.evaluate(()=>finishEditorMapInteraction());
    await expect(page.locator('#desordre-create-designation')).toHaveValue(`GPS ${type}`);
    const before=await page.evaluate(()=>buildDesordreCreationPayload().geometry);
    expect(before.type).toBe(type);
    await page.evaluate(()=>basemapSelector.select('test-standard'));
    expect(await page.evaluate(()=>buildDesordreCreationPayload().geometry)).toEqual(before);
  }
});

test('package generations, storage reserve and Leaflet zoom limits', async ({page,context})=>{
  await login(page);
  await page.evaluate(()=>navigator.serviceWorker.ready);
  await expect.poll(()=>page.evaluate(()=>!!navigator.serviceWorker.controller)).toBe(true);
  const result=await page.evaluate(async()=>{
    const store=OfflineBasemapStore.createStore();
    const manager=OfflineBasemapDownload.createManager({store,fetchImpl:fetch.bind(window),origin:location.origin,storageManager:navigator.storage});
    const entry=(await (await fetch('/api/basemaps')).json()).basemaps[0];
    const spec={entry,contextKey:BusinessRuntime.context.key,territoryId:'00000000-0000-4000-8000-000000000002',
      bounds:[2.34,48.84,2.36,48.86],zoomMin:12,zoomMax:12};
    const previous=await manager.prepare({...spec,descriptor:await Basemap.loadStrict(entry.delivery.url)});
    await manager.run(previous.manifest.id,spec.contextKey);
    await fetch('/int2/control',{method:'POST',body:JSON.stringify({generation:'v2',fail:true})});
    const next=await manager.prepare({...spec,descriptor:await Basemap.loadStrict(entry.delivery.url)});
    try { await manager.run(next.manifest.id,spec.contextKey); } catch (_) { /* Assert durable state below. */ }
    const failed=await store.get(next.manifest.id,spec.contextKey);
    const preserved=await store.get(previous.manifest.id,spec.contextKey);
    const local=`/__offline-basemaps/${preserved.id}/${preserved.expectedKeys[0]}`;
    const oldStatus=(await fetch(local)).status;
    await fetch('/int2/control',{method:'POST',body:JSON.stringify({fail:false})});
    await manager.run(next.manifest.id,spec.contextKey);
    const ready=await store.get(next.manifest.id,spec.contextKey);
    const bounded=OfflineBasemapDownload.createManager({store,origin:location.origin,
      storageManager:{estimate:async()=>({quota:1000,usage:999})}});
    let reserveMessage='';
    try {await bounded.prepare({...spec,descriptor:await Basemap.loadStrict(entry.delivery.url)});}
    catch(error){reserveMessage=error.message;}
    await fetch('/int2/control',{method:'POST',body:JSON.stringify({generation:'v1',reset:true})});
    return {failed:failed.state,preserved:preserved.state,oldStatus,ready:ready.state,reserveMessage};
  });
  expect(result.failed).not.toBe('ready'); expect(result.preserved).toBe('ready');
  expect(result.oldStatus).toBe(200); expect(result.ready).toBe('ready');
  expect(result.reserveMessage).toMatch(/insuffisant/);
  await page.evaluate(async()=>{await basemapSelector.select('test-detail');map.setView([48.85,2.35],20);});
  await expect.poll(()=>page.locator('#map .leaflet-tile-loaded').count()).toBeGreaterThan(0);
  const tileZooms=await page.evaluate(()=>Object.values(map._layers).filter(layer=>layer instanceof L.TileLayer)
    .flatMap(layer=>Object.values(layer._tiles||{}).map(tile=>tile.coords.z)));
  expect(tileZooms.length).toBeGreaterThan(0); expect(Math.max(...tileZooms)).toBe(19);
  await page.evaluate(()=>basemapSelector.select('test-standard'));
  expect(await page.evaluate(()=>map.getZoom())).toBe(19);
  await page.evaluate(async()=>{await basemapSelector.select('test-aerial');map.setZoom(4);});
  expect(await page.evaluate(()=>map.getZoom())).toBe(4);
  await context.setOffline(true);
  await page.evaluate(async()=>{await basemapSelector.select('test-standard');map.setView([0,0],12);});
  // There are no package tiles at (0,0); returning inside restores actual local tiles.
  await expect.poll(()=>page.locator('#map .leaflet-tile-loaded').count()).toBe(0);
  await page.evaluate(()=>map.setView([48.85,2.35],12));
  await expect.poll(()=>page.locator('#map .leaflet-tile-loaded').count()).toBeGreaterThan(0);
});

test('slug context, readonly base and foreign slug remain isolated', async ({page})=>{
  await login(page);
  const first=await page.evaluate(()=>BusinessRuntime.context.business_database_id);
  expect(first).toBe(fixture.bases[0]);
  await page.goto(`/carte?base=base_1&entity=cheminements&id=${fixture.path}`);
  await expect(page.locator('#cheminement-editor-form [name="designation"]')).toHaveValue('INT2 accès polygonal');
  expect(await page.evaluate(()=>BusinessRuntime.context.business_database_id)).toBe(fixture.bases[1]);
  expect(await page.evaluate(()=>BusinessRuntime.can('business.update'))).toBe(false);
  const status=await page.evaluate(async id=>(await BusinessRuntime.fetch(`/api/cheminements/${id}`,{
    method:'PUT',headers:{'Content-Type':'application/json','If-Match':'"1"','Operation-Id':crypto.randomUUID()},
    body:JSON.stringify({commentaire:'forbidden'})})).status,fixture.path);
  expect(status).toBe(403);
  for(const slug of ['base_2','unknown-base']) {
    await page.goto(`/carte?base=${slug}`);
    await expect(page.locator('#identity-gate')).toContainText('Base non autorisée');
    expect(await page.evaluate(()=>Boolean(window.BusinessRuntime?.isActive()))).toBe(false);
  }
  await page.goto(`/carte?base=${fixture.bases[0]}`);
  await page.waitForFunction(()=>window.BusinessRuntime?.isActive());
  await expect.poll(()=>new URL(page.url()).searchParams.get('base')).toBe('base_0');
});

test('offline package UI and logout preserve storage but revoke the previous owner', async ({page})=>{
  await login(page);
  await page.evaluate(()=>navigator.serviceWorker.ready);
  await expect.poll(()=>page.evaluate(()=>!!navigator.serviceWorker.controller)).toBe(true);
  await page.evaluate(()=>offlineBasemapUi.open());
  const modal=page.locator('#offline-basemaps-modal');
  await expect(modal).toBeVisible();
  await modal.locator('input[value="test-standard"]').check();
  await modal.locator('[name="zoomMin"]').fill('12');
  await modal.locator('[name="zoomMax"]').fill('12');
  page.once('dialog',dialog=>dialog.accept());
  await modal.getByRole('button',{name:'Estimer et préparer…',exact:true}).click();
  await expect(modal.locator('[data-basemap-status]')).toContainText('vérifié et prêt');
  await expect(modal.locator('.offline-basemap-package')).toContainText('prêt');
  const saved=await page.evaluate(async()=>{
    const store=OfflineBasemapStore.createStore();
    const row=(await store.list(BusinessRuntime.context.key))[0];
    BusinessRuntime.setUrlMode('minimal');
    return {local:`/__offline-basemaps/${row.id}/${row.expectedKeys[0]}`,key:BusinessRuntime.context.key};
  });
  expect(await page.evaluate(async url=>(await fetch(url)).status,saved.local)).toBe(200);
  await modal.locator('[data-basemap-close]').click();
  await page.locator('#open-business-session').click();
  const logout = page.waitForResponse(response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/auth/logout');
  await page.locator('#logout-business-session').click();
  expect((await logout).status()).toBe(204);
  // Logout replaces the current document after the server response. Wait for
  // that navigation before starting the next identity's login.
  await expect(page).toHaveURL(/\/carte$/);
  await expect(page.locator('#identity-gate')).toContainText('Connexion');
  await login(page,'/carte?base=base_0','reader_alpha');
  expect(await page.evaluate(()=>BusinessRuntime.context.key)).not.toBe(saved.key);
  expect(await page.evaluate(()=>BusinessRuntime.urlMode())).toBe('position');
  expect(await page.evaluate(async url=>(await fetch(url)).status,saved.local)).toBe(404);
  expect(await page.evaluate(async()=>{
    const store=OfflineBasemapStore.createStore(); return (await store.list(BusinessRuntime.context.key)).length;
  })).toBe(0);
  expect((await page.evaluate(()=>caches.keys())).some(name=>name.startsWith('digues-basemap-package-'))).toBe(true);
});

test('manual Polygon drawing and unsaved relations survive basemap changes', async ({page})=>{
  await login(page,`/carte?base=base_0&entity=cheminements&id=${fixture.path}`);
  const viewer=await page.context().newPage();
  await viewer.goto('/carte?base=base_0');
  await viewer.waitForFunction(id=>typeof desordreLayersById!=='undefined' && desordreLayersById.has(id),fixture.disorder);
  await viewer.evaluate(id=>desordreLayersById.get(id).fire('click'),fixture.disorder);
  const inverse=viewer.locator('#desordre-associated-objects');
  await expect(inverse).toContainText('INT2 accès polygonal');
  const versionBefore=await viewer.evaluate(async id=>(await (await BusinessRuntime.fetch(`/api/desordres/${id}`)).json()).properties.version,fixture.disorder);
  const form=page.locator('#cheminement-editor-form');
  await expect(form.locator('[name="designation"]')).toHaveValue('INT2 accès polygonal');
  await expect.poll(()=>page.evaluate(()=>mapEntityNavigationComplete)).toBe(true);
  await form.getByRole('button',{name:'Retirer INT2 Désordre',exact:true}).click();
  await form.locator('[name="designation"]').fill('Dessin et relations INT2');
  await page.evaluate(()=>basemapSelector.select('test-aerial'));
  await expect(form.locator('.relation-search-selected')).not.toContainText('INT2 Désordre');
  await form.locator('[data-role="draw"]').click();
  await page.locator('#map').click({position:{x:250,y:80}});
  await page.evaluate(()=>basemapSelector.select('test-detail'));
  await page.locator('#map').click({position:{x:320,y:80}});
  await page.locator('#map').click({position:{x:320,y:140}});
  await form.locator('[data-role="confirm"]').click();
  await expect(form.locator('[name="designation"]')).toHaveValue('Dessin et relations INT2');
  await expect(form.locator('.relation-search-selected')).not.toContainText('INT2 Désordre');
  const response=page.waitForResponse(r=>r.request().method()==='PUT' && new URL(r.url()).pathname.endsWith(`/cheminements/${fixture.path}`));
  await page.locator('#cheminement-editor-panel [data-role="save"]').click();
  const saved=await response;
  expect(saved.status()).toBe(200);
  const final=await saved.json();
  expect(final.geometry.type).toBe('Polygon');
  expect(final.geometry.coordinates[0]).toHaveLength(4);
  expect(final.properties.desordre_ids).toEqual([]);
  expect(final.properties.designation).toBe('Dessin et relations INT2');
  await expect(inverse).not.toContainText('INT2 accès polygonal');
  await expect(inverse).not.toContainText('Dessin et relations INT2');
  expect(await viewer.evaluate(async id=>(await (await BusinessRuntime.fetch(`/api/desordres/${id}`)).json()).properties.version,fixture.disorder)).toBe(versionBefore);
  await viewer.close();
});
