// Niveau B : vrai Chromium, API et PostGIS jetables fournis par tests.run_postgres --browser.
// L'état initial contient la démo AH/Zone ; chaque création reçoit son UUID serveur.
// Le cas négatif géométrique reste couvert par l'API PostgreSQL ciblée.
const { expect, test } = require('@playwright/test');

test.skip(process.env.DIGUES_E2E_OCC !== '1', 'Cluster PostGIS jetable requis.');
test.use({ serviceWorkers: 'block' });
test.setTimeout(240_000);

async function openFromBase(page, entityClass, id) {
  await page.goto('/base');
  await expect(page.locator('#identity-gate')).toBeHidden();
  const table = page.locator('#data-view-table');
  if (!await table.isVisible()) await page.locator('#toggle-views').click();
  while (await page.locator('#data-view-field-list button').count()) {
    await page.locator('#data-view-field-list button').first().click();
  }
  await table.selectOption(entityClass);
  await page.locator('#data-view-field').selectOption('designation');
  await page.locator('#data-view-add-field').click();
  const result = page.locator(`#data-view-result-body [data-entity-class="${entityClass}"][data-entity-id="${id}"]`);
  await expect(result).toBeVisible();
  await result.click();
  await expect(page.locator('#data-entity-detail')).toBeVisible();
}

async function createFromMap(page, kind, panelId, designation) {
  await page.locator('#toggle-create-menu').click();
  await page.locator(`[data-create-type="${kind}"]`).click();
  const panel = page.locator(panelId);
  await expect(panel).toBeVisible();
  await panel.locator('[name="designation"]').fill(designation);
  return panel;
}

test('AH et Zone traversent Carte, Base, relations, médias et synchronisation', async ({ browser, page, request, baseURL }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/base');
  await expect(page.locator('#identity-gate')).toBeHidden();
  const root = page.locator('#data-view-root');
  if (!await root.isVisible()) await page.locator('#toggle-views').click();
  await expect(root.locator('option[value="amenagements_hydrauliques"]')).toHaveCount(1);
  await expect(root.locator('option[value="zones_stockage"]')).toHaveCount(1);

  await page.goto('/carte');
  const ahPanel = await createFromMap(page, 'amenagement-hydraulique',
    '#amenagement-hydraulique-editor-panel', 'AH qualification 5');
  const ahCreated = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/amenagements-hydrauliques');
  await ahPanel.locator('[data-role="save"]').click();
  const ahResponse = await ahCreated;
  expect(ahResponse.status()).toBe(201);
  expect(ahResponse.request().postDataJSON()).not.toHaveProperty('geometry');
  const ah = await ahResponse.json(), ahId = ah.properties.id;
  expect(ah.geometry).toBeNull();
  expect(ah.properties.zone_ids).toEqual([]);
  expect(ah.properties.troncon_ids).toEqual([]);
  await page.goto(`/carte?entity=amenagements_hydrauliques&id=${ahId}`);
  await expect(ahPanel.locator('[name="designation"]')).toHaveValue('AH qualification 5');
  await ahPanel.locator('[name="libelle"]').fill('AH relu après rechargement');
  const ahUpdated = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname === `/api/amenagements-hydrauliques/${ahId}`);
  await ahPanel.locator('[data-role="save"]').click();
  expect((await ahUpdated).status()).toBe(200);

  await ahPanel.locator('[data-role="cancel"]').click();
  const zonePanel = await createFromMap(page, 'zone-stockage',
    '#zone-stockage-editor-panel', 'Zone qualification 5');
  await zonePanel.locator('[name="capacite_stockage"]').fill('1250');
  await expect(zonePanel.locator('[name="amenagement_hydraulique_id"]')).toHaveValue('');
  await zonePanel.locator('[data-role="draw"]').click();
  // Les événements Leaflet parcourent le vrai gestionnaire de dessin, hors des
  // contrôles flottants qui recouvrent une partie de la carte selon la fenêtre.
  await page.evaluate(() => {
    for (const [lat, lng] of [[50.53, 2.59], [50.53, 2.60], [50.54, 2.595]]) {
      map.fire('click', { latlng: L.latLng(lat, lng) });
    }
  });
  await zonePanel.locator('[data-role="confirm"]').click();
  await expect(zonePanel.locator('[data-role="draw"]')).toHaveText('Modifier');
  await expect(zonePanel.locator('[name="geometry_type"]')).toHaveValue('Polygon');
  await expect(zonePanel.locator('[data-role="confirm"]')).toBeHidden();
  const zoneCreated = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/zones-stockage', { timeout: 15_000 });
  await zonePanel.locator('[data-role="save"]').click();
  const zoneResponse = await zoneCreated;
  expect(zoneResponse.status()).toBe(201);
  const zone = await zoneResponse.json(), zoneId = zone.properties.id;
  expect(zone.geometry.type).toBe('Polygon');
  // Prove that the confirmed drawing, including closure, was actually stored.
  const expectedRing = [[2.59, 50.53], [2.60, 50.53], [2.595, 50.54], [2.59, 50.53]];
  expect(zone.geometry.coordinates[0]).toHaveLength(expectedRing.length);
  expectedRing.forEach((point, index) => point.forEach((value, axis) =>
    expect(zone.geometry.coordinates[0][index][axis]).toBeCloseTo(value, 7)));
  expect(zone.properties.amenagement_hydraulique_id).toBeNull();
  await expect.poll(() => page.evaluate(id => {
    let present = false;
    map.eachLayer(layer => { if (layer.feature?.properties?.id === id) present = true; });
    return present;
  }, zoneId)).toBe(true);
  await page.goto(`/carte?entity=zones_stockage&id=${zoneId}`);
  await expect(zonePanel.locator('[name="designation"]')).toHaveValue('Zone qualification 5');
  await zonePanel.locator('[name="amenagement_hydraulique_id"]').selectOption(ahId);
  const zoneLinked = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname === `/api/zones-stockage/${zoneId}`);
  await zonePanel.locator('[data-role="save"]').click();
  expect((await zoneLinked).status()).toBe(200);

  const composition = await request.get('/api/amenagements-hydrauliques/relations/options');
  expect(composition.status()).toBe(200);
  const choices = (await composition.json()).troncons;
  const crest = choices.find(row => row.libelle === 'Crête suivie aux Prés');
  expect(crest?.id).toMatch(/^[0-9a-f-]{36}$/);
  await page.goto(`/carte?entity=amenagements_hydrauliques&id=${ahId}`);
  await ahPanel.locator('[name="troncon_ids"]').selectOption(crest.id);
  const tronconLinked = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname === `/api/amenagements-hydrauliques/${ahId}`);
  await ahPanel.locator('[data-role="save"]').click();
  expect((await tronconLinked).status()).toBe(200);
  const ahDetail = await (await request.get(`/api/amenagements-hydrauliques/${ahId}`)).json();
  expect(ahDetail.properties.zone_ids).toEqual([zoneId]);
  expect(ahDetail.properties.troncon_ids).toEqual([crest.id]);
  expect(ahDetail.geometry).toBeNull();
  const zoneDetail = await (await request.get(`/api/zones-stockage/${zoneId}`)).json();
  expect(zoneDetail.properties.amenagement_hydraulique_id).toBe(ahId);
  expect(zoneDetail.properties).not.toHaveProperty('troncon_ids');

  await openFromBase(page, 'amenagements_hydrauliques', ahId);
  await expect(page.locator('#data-entity-detail-read')).toContainText('Zone qualification 5');
  await expect(page.locator('#data-entity-detail-read')).toContainText('Crête suivie aux Prés');
  await page.locator('#data-entity-detail-read button', { hasText: 'Zone qualification 5' }).click();
  await expect(page.locator('#data-entity-detail-title')).toContainText('Zone qualification 5');
  await page.locator('#data-entity-detail-back').click();
  await expect(page.locator('#data-entity-detail-title')).toContainText('AH qualification 5');
  await openFromBase(page, 'zones_stockage', zoneId);
  await expect(page.locator('#data-entity-detail-read')).toContainText('AH qualification 5');

  const seededZones = await (await request.get('/api/zones-stockage')).json();
  const seeded = seededZones.features.find(feature => feature.properties.designation === 'Zone de stockage des Prés');
  expect(seeded?.properties?.reseau_hydraulique_ids).toHaveLength(1);
  await openFromBase(page, 'zones_stockage', seeded.properties.id);
  await expect(page.locator('#data-entity-detail-read')).toContainText("Canal d'amenée des Prés");
  await page.locator('#data-entity-detail [data-role="observations-tab"]').click();
  const observationRow = page.locator('#data-entity-detail-observation-list button',
    { hasText: 'Visite du dispositif de la zone de stockage des Prés' });
  await expect(observationRow).toBeVisible();
  await observationRow.click();
  await expect(page.locator('#observation-detail-dialog [data-photos]')).toContainText('Vue du dispositif');
  const observation = await (await request.get(`/api/zones-stockage/${seeded.properties.id}/observations`)).json();
  const photoDetail = await (await request.get(`/api/observations/${observation.observations[0].id}`)).json();
  expect(photoDetail.parent.resource).toBe('zones_stockage');
  expect(photoDetail.photos).toHaveLength(1);
  expect((await request.get(photoDetail.photos[0].content_url)).status()).toBe(200);
  const seededAhId = seeded.properties.amenagement_hydraulique_id;
  expect((await (await request.get(`/api/amenagements-hydrauliques/${seededAhId}/observations`)).json()).observations).toEqual([]);

  const other = await browser.newContext({ baseURL, serviceWorkers: 'block' });
  try {
    await other.addInitScript(() => {
      window.__ahReceived = [];
      window.__zoneReceived = [];
      window.__ahRebroadcast = [];
      const post = BroadcastChannel.prototype.postMessage;
      BroadcastChannel.prototype.postMessage = function (message) {
        if (message.type === 'resource-mutated') window.__ahRebroadcast.push(message.payload);
        return post.call(this, message);
      };
    });
    const base = await other.newPage();
    base.on('pageerror', error => errors.push(error.message));
    await openFromBase(base, 'amenagements_hydrauliques', ahId);
    await base.evaluate(() => ResourceMutationController.registerInvalidator('open-detail', event => {
      if (event.resource === 'amenagements-hydrauliques') window.__ahReceived.push(event.eventId);
    }));
    await page.goto(`/carte?entity=amenagements_hydrauliques&id=${ahId}`);
    await ahPanel.locator('[name="commentaire"]').fill('Synchronisé entre Carte et Base');
    const synced = page.waitForResponse(response => response.request().method() === 'PUT'
      && new URL(response.url()).pathname === `/api/amenagements-hydrauliques/${ahId}`);
    await ahPanel.locator('[data-role="save"]').click();
    const receipt = (await (await synced).json()).mutation;
    await expect(base.locator('#data-entity-detail-read')).toContainText('Synchronisé entre Carte et Base');
    await expect.poll(() => base.evaluate(() => window.__ahReceived)).toEqual([receipt.eventId]);
    expect(await base.evaluate(() => window.__ahRebroadcast)).toEqual([]);

    await openFromBase(base, 'zones_stockage', zoneId);
    await base.evaluate(() => ResourceMutationController.registerInvalidator('open-detail', event => {
      if (event.resource === 'zones-stockage') window.__zoneReceived.push(event.eventId);
    }));
    await page.goto(`/carte?entity=zones_stockage&id=${zoneId}`);
    await zonePanel.locator('[name="commentaire"]').fill('Zone synchronisée entre Carte et Base');
    const zoneSynced = page.waitForResponse(response => response.request().method() === 'PUT'
      && new URL(response.url()).pathname === `/api/zones-stockage/${zoneId}`);
    await zonePanel.locator('[data-role="save"]').click();
    const zoneReceipt = (await (await zoneSynced).json()).mutation;
    await expect(base.locator('#data-entity-detail-read')).toContainText('Zone synchronisée entre Carte et Base');
    await expect.poll(() => base.evaluate(() => window.__zoneReceived)).toEqual([zoneReceipt.eventId]);
    expect(await base.evaluate(() => window.__ahRebroadcast)).toEqual([]);
  } finally { await other.close(); }
  expect(errors).toEqual([]);
});
