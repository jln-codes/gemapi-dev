const { expect, test } = require("@playwright/test");

test("le seed PostgreSQL réel est visible sur la Carte et dans une fiche", async ({ page }) => {
  test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE !== "true",
    "À lancer avec tests.run_postgres --browser sur une base jetable seedée.");

  const collectionResponse = page.waitForResponse((response) =>
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/zones-stockage");
  await page.goto("/carte");
  await expect(page.locator("#map")).toBeVisible();
  const response = await collectionResponse;
  expect(response.ok()).toBeTruthy();
  const collection = await response.json();
  const seedFeature = collection.features.find((feature) =>
    feature.properties?.designation === "Zone de stockage des Prés");
  expect(seedFeature?.geometry?.type).toBe("Polygon");
  expect(seedFeature.properties.amenagement_hydraulique_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(seedFeature.properties.reseau_hydraulique_ids).toHaveLength(1);
  expect(seedFeature.properties.type_id).toBe('IND');
  const parent = await (await page.request.get(`/api/amenagements-hydrauliques/${seedFeature.properties.amenagement_hydraulique_id}`)).json();
  expect(parent.properties.designation).toBe('ZEC des Prés');
  expect(parent.properties.zone_ids).toEqual([seedFeature.properties.id]);
  expect(parent.properties.troncon_ids).toHaveLength(1);
  const ahTroncon = await (await page.request.get(`/api/troncons/${parent.properties.troncon_ids[0]}`)).json();
  expect(ahTroncon.properties.designation).toBe('Tronçon AH des Prés');
  expect(ahTroncon.properties.type_physique_id).toBe('INDETERMINE');
  expect(ahTroncon.properties.type_troncon_id).toBe('RefTypeTroncon:6');

  await page.evaluate((feature) => {
    map.fitBounds(L.geoJSON(feature).getBounds().pad(1));
  }, seedFeature);
  const polygon = page.locator('.leaflet-overlay-pane path[stroke="#1769aa"]');
  await expect.poll(async () => polygon.evaluateAll((nodes) => nodes.filter((node) => {
    const bounds = node.getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0;
  }).length)).toBeGreaterThan(0);

  // Le lien typé ouvre l'identité exacte, même si plusieurs couches se superposent.
  await page.evaluate(id => MapEntityNavigation.open('zones_stockage', id), seedFeature.properties.id);
  const panel = page.locator("#zone-stockage-editor-panel");
  await expect(panel).toBeVisible();
  await expect(panel.locator('[name="designation"]')).toHaveValue("Zone de stockage des Prés");
  await expect(panel.locator('[name="reseau_hydraulique_ids"] option:checked')).toHaveCount(1);
});

test("le contour versionné du seed est affiché sur la carte", async ({ page }) => {
  test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE !== "true",
    "À lancer avec tests.run_postgres --browser sur une base jetable seedée.");

  const territoryResponse = page.waitForResponse((response) =>
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/territoire-administratif");
  await page.goto("/carte");
  await expect(page.locator("#map")).toBeVisible();
  const response = await territoryResponse;
  expect(response.ok()).toBeTruthy();
  const territory = await response.json();
  expect(territory.features).toHaveLength(1);
  expect(territory.features[0].geometry.type).toBe("Polygon");
  expect(territory.features[0].properties.srid).toBe(3950);

  await expect.poll(() => page.evaluate(() =>
    [...map.getPane("territory-outline").querySelectorAll("path")]
      .filter((path) => path.getAttribute("stroke") === "#385f80").length,
  )).toBe(1);
});

// Niveau B (~20 s hors préparation du cluster) : vraie API et SQL jetable.
// Chaque scénario crée ses propres parents/Tronçon ; aucun objet seed n'est modifié.
test("clic Tronçon : édition attributaire réellement persistée après rechargement", async ({ page, request }) => {
  test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE !== "true",
    "À lancer avec tests.run_postgres --browser sur PostgreSQL jetable.");
  const label = `Édition Carte ${require('node:crypto').randomUUID()}`;
  const se = await request.post('/api/systemes-endiguement', { data: { libelle: label } });
  expect(se.status()).toBe(201);
  const systeme = await se.json();
  const di = await request.post('/api/digues', { data: { libelle: label, systeme_endiguement_id: systeme.id } });
  expect(di.status()).toBe(201);
  const digue = await di.json();
  const tr = await request.post('/api/troncons', { data: { libelle: label, designation: 'Avant édition Carte', digue_id: digue.id,
    geometry: { type: 'LineString', coordinates: [[2.0, 46.0], [2.01, 46.01]] } } });
  expect(tr.status()).toBe(201);
  const troncon = await tr.json(), id = troncon.properties.id;
  async function open() {
    await page.goto('/carte');
    await page.waitForFunction(id => typeof tronconLayersById !== 'undefined'
      && tronconLayersById.get(id)?.getElement(), id);
    await page.evaluate(id => { tronconLayersById.get(id).getElement().dataset.realEditTroncon = id; }, id);
    await page.locator(`[data-real-edit-troncon="${id}"]`).dispatchEvent('click');
    await expect(page.locator('#data-entity-detail .entity-form__form')).toBeVisible();
  }
  await open();
  const field = page.locator('#data-entity-detail .entity-form__fields [name=designation]');
  await expect(field).toHaveValue('Avant édition Carte');
  await field.fill('Après édition Carte');
  const saved = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname === `/api/troncons/${id}`);
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  const response = await saved;
  expect(response.status()).toBe(200);
  expect(response.request().headers()['if-match']).toBe(`"${troncon.properties.version}"`);
  expect(response.request().postDataJSON()).not.toHaveProperty('geometry');
  const reread = await request.get(`/api/troncons/${id}`);
  expect(reread.status()).toBe(200);
  const actual = await reread.json();
  expect(actual.properties.id).toBe(id);
  expect(actual.properties.designation).toBe('Après édition Carte');
  expect(actual.properties.digue_id).toBe(digue.id);
  expect(actual.geometry).toEqual(troncon.geometry);
  expect(actual.properties.version).toBeGreaterThan(troncon.properties.version);
  // Même identifiant, ancienne version : la vraie API refuse le concurrent.
  const stale = await request.put(`/api/troncons/${id}`, { data: { ...response.request().postDataJSON(), designation: 'Concurrent obsolète' },
    headers: { 'If-Match': `"${troncon.properties.version}"` } });
  expect(stale.status()).toBe(409);
  await open();
  await expect(field).toHaveValue('Après édition Carte');
});

// UX-FORM-04, niveau B : quatre cycles UI sur SQL réel (parents propres à chaque
// scénario). Géométrie provisoire explicite, sans ajouter un nouvel outil de dessin.
for (const [type, resource] of [['systeme','systemes-endiguement'], ['digue','digues'],
  ['troncon','troncons'], ['desordre','desordres']]) {
  test(`UX04 ${type} : création Carte puis PUT sur l'identité confirmée et relecture SQL`, async ({page,request}) => {
    test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE !== 'true',
      'À lancer avec tests.run_postgres --browser sur PostgreSQL jetable.');
    const label = `UX04 ${require('node:crypto').randomUUID()}`;
    const seResponse = await request.post('/api/systemes-endiguement', {data:{libelle:label}});
    expect(seResponse.status()).toBe(201); const se = await seResponse.json();
    const diResponse = await request.post('/api/digues', {data:{libelle:label,systeme_endiguement_id:se.id}});
    expect(diResponse.status()).toBe(201); const di = await diResponse.json();
    await page.goto('/carte');
    await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
    await page.locator('#toggle-create-menu').click(); await page.locator(`[data-create-type="${type}"]`).click();
    await page.locator(type === 'desordre' ? '#desordre-create-designation' : '#heritage-object-label').fill(label);
    if (type === 'digue') await page.locator('#heritage-parent').selectOption(se.id);
    if (type === 'troncon') await page.locator('#heritage-parent').selectOption(di.id);
    if (['troncon','desordre'].includes(type)) await page.evaluate(type => {
      if (type === 'troncon') provisionalTronconLayer = L.polyline([[46,2],[46.01,2.01]]).addTo(map);
      else provisionalDesordreLayer = L.marker([46,2]).addTo(map);
    },type);
    const posted = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === `/api/${resource}`);
    await page.locator(type === 'desordre' ? '#submit-desordre-create' : '#submit-create').click();
    const result = await posted; expect(result.status()).toBe(201);
    const created = await result.json(), values = created.properties || created;
    const form = page.locator(type === 'desordre' ? '#desordre-editor' : '#data-entity-detail .entity-form__form');
    await expect(form).toBeVisible(); await expect(form.locator('[name=designation]')).toBeEnabled();
    await expect(page.locator(type === 'desordre' ? '#editor-panel .entity-form [data-role=general-tab]:visible' : '#data-entity-detail [data-role=general-tab]')).toHaveAttribute('aria-selected','true');
    await form.locator('[name=designation]').fill('Après première sauvegarde UX04');
    const saved = page.waitForResponse(r => r.request().method() === 'PUT' && new URL(r.url()).pathname === `/api/${resource}/${values.id}`);
    await page.locator(type === 'desordre' ? '#submit-desordre-create' : '#data-entity-detail .entity-form__actions [data-role=save]').click();
    const updated = await saved; expect(updated.status()).toBe(200);
    expect(updated.request().headers()['if-match']).toBe(`"${values.version}"`);
    await page.reload();
    await expect(page.locator('#map')).toBeVisible();
    const reread = await request.get(`/api/${resource}/${values.id}`); expect(reread.status()).toBe(200);
    const actual = await reread.json(), current = actual.properties || actual;
    expect(current.id).toBe(values.id); expect(current.version).toBe(values.version+1);
    expect(current.designation).toBe('Après première sauvegarde UX04');
    if (created.geometry) expect(actual.geometry).toEqual(created.geometry);
    if (type === 'digue') expect(current.systeme_endiguement_id).toBe(se.id);
    if (type === 'troncon') expect(current.digue_id).toBe(di.id);
    if (['troncon','desordre'].includes(type)) {
      const observations = await request.get(`/api/${resource}/${values.id}/observations`); expect(observations.status()).toBe(200);
      expect((await observations.json()).observations).toEqual([]);
    }
  });
}

// Navigation Patrimoine AH sur les véritables relations et API R6. Ressources
// propres au scénario, aucune géométrie ajoutée à l'AH et aucun objet seed modifié.
test('Patrimoine AH réel : parent non spatial, Zone et Tronçon depuis Carte puis Base',async ({page,request})=>{
  test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE!=='true','PostGIS jetable via tests.run_postgres --browser requis.');
  const label=`Patrimoine ${require('node:crypto').randomUUID()}`;
  const se=await (await request.post('/api/systemes-endiguement',{data:{libelle:label}})).json();
  const di=await (await request.post('/api/digues',{data:{libelle:label,systeme_endiguement_id:se.id}})).json();
  const tr=await request.post('/api/troncons',{data:{libelle:label,digue_id:di.id,geometry:{type:'LineString',coordinates:[[-1.55,47.21],[-1.54,47.22]]}}});
  expect(tr.status()).toBe(201); const trunk=await tr.json();
  const ahResponse=await request.post('/api/amenagements-hydrauliques',{data:{designation:label,troncon_ids:[trunk.properties.id],zone_ids:[]}});
  expect(ahResponse.status()).toBe(201); const ah=await ahResponse.json(); expect(ah.geometry).toBeNull();
  const zones=await (await request.get('/api/zones-stockage')).json();
  const zoneResponse=await request.post('/api/zones-stockage',{data:{designation:label,amenagement_hydraulique_id:ah.properties.id,geometry:zones.features[0].geometry}});
  expect(zoneResponse.status()).toBe(201); const zone=await zoneResponse.json();
  for(const view of ['/carte','/base']) {
    await page.goto(view); await expect(page.locator('main')).toHaveJSProperty('inert',false);
    if(view==='/carte') await page.waitForFunction(()=>typeof mapViewportReady!=='undefined'&&mapViewportReady);
    await page.locator('#toggle-heritage').click();
    const branch=page.locator('#heritage-tree [aria-label="Aménagements hydrauliques"]');
    const parent=branch.locator(`.tree-name[data-object-id="${ah.properties.id}"]`);
    await expect(parent).toBeVisible(); await parent.locator('..').locator('> .tree-toggle').click();
    await parent.click();
    await expect(page.locator(view==='/carte'?'#amenagement-hydraulique-editor-panel [name=designation]':'#data-entity-detail .entity-form__fields [name=designation]')).toHaveValue(label);
    await branch.locator(`.tree-name[data-object-id="${zone.properties.id}"]`).click();
    const zoneField=page.locator(view==='/carte'?'#zone-stockage-editor-panel [name=designation]':'#data-entity-detail .entity-form__fields [name=designation]');
    await expect(zoneField).toHaveValue(label);
    await branch.locator(`.tree-name[data-object-id="${trunk.properties.id}"]`).click();
    await expect(page.locator('#data-entity-detail .entity-form__fields [name=libelle]')).toHaveValue(label);
  }
  const hierarchy=await (await request.get('/api/systemes-endiguement')).json();
  const node=hierarchy.amenagements_hydrauliques.find(a=>a.id===ah.properties.id);
  expect(node.zones_stockage.map(z=>z.id)).toEqual([zone.properties.id]);
  expect(node.troncons_unifies.map(t=>[t.id,t.resource])).toEqual([[trunk.properties.id,'troncons']]);
});
