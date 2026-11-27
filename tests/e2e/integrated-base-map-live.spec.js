const { expect, test } = require('@playwright/test');

test.skip(process.env.DIGUES_E2E_OCC !== '1',
  'PostgreSQL/PostGIS et stockage média locaux jetables requis.');
test.use({ serviceWorkers: 'block' });
test.setTimeout(300000);

const CASES = [
  ['mobilier', '/api/mobilier'],
  ['equipements_mesure', '/api/equipements-mesure'],
  ['reseaux_techniques', '/api/reseaux-techniques'],
  ['ouvrages_particuliers', '/api/ouvrages-particuliers'],
  ['cheminements', '/api/cheminements'],
  ['vegetation', '/api/vegetation'],
  ['amenagements_hydrauliques', '/api/amenagements-hydrauliques'],
  ['prestations', '/api/prestations'],
];
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC',
  'base64',
);

function properties(payload) {
  return payload?.type === 'Feature' ? payload.properties : payload;
}

async function ensureSpecialStructure(request) {
  const collection = await request.get('/api/ouvrages-particuliers');
  expect(collection.status()).toBe(200);
  if ((await collection.json()).features.length) return;
  const troncons = await request.get('/api/troncons');
  expect(troncons.status()).toBe(200);
  const tronconId = (await troncons.json()).features[0]?.properties?.id;
  expect(tronconId).toMatch(/^[0-9a-f-]{36}$/);
  const created = await request.post('/api/ouvrages-particuliers', { data: {
    designation: 'Qualification 4F ouvrage particulier',
    troncon_id: tronconId,
    geometry: { type: 'Point', coordinates: [2.3005, 48.851] },
  } });
  expect(created.status()).toBe(201);
}

async function createLocatedSpecialStructure(request) {
  const troncons = await request.get('/api/troncons');
  expect(troncons.status()).toBe(200);
  const tronconId = (await troncons.json()).features[0]?.properties?.id;
  expect(tronconId).toMatch(/^[0-9a-f-]{36}$/);
  const rejected = await request.post('/api/ouvrages-particuliers', { data: {
    designation: 'Qualification 4F sans géométrie', troncon_id: tronconId,
  } });
  expect(rejected.status()).toBe(422);
  const created = await request.post('/api/ouvrages-particuliers', { data: {
    designation: 'Qualification 4F compact géolocalisé', troncon_id: tronconId,
    geometry: { type: 'Point', coordinates: [2.3005, 48.851] },
  } });
  expect(created.status()).toBe(201);
  const body = await created.json();
  expect(body.geometry.type).toBe('Point');
  return body.properties.id;
}

async function openFirstResult(page, entityClass) {
  await page.goto('/base');
  await expect(page.locator('#identity-gate')).toBeHidden();
  await expect(page.locator('#base-summary-crs')).not.toHaveText('');
  const dismiss = page.getByRole('button', { name: 'Fermer le message' });
  if (await dismiss.isVisible()) await dismiss.click();
  const table = page.locator('#data-view-table');
  if (!await table.isVisible()) await page.locator('#toggle-views').click();
  // Opening Vues starts the asynchronous catalogue/default-view load. Clearing
  // fields before it finishes leaves the four defaults restricting the tables.
  await expect(table).toBeEnabled();
  while (await page.locator('#data-view-field-list button').count()) {
    await page.locator('#data-view-field-list button').first().click();
  }
  await table.selectOption(entityClass);
  await page.locator('#data-view-field').selectOption('designation');
  await page.locator('#data-view-add-field').click();
  const result = page.locator(`#data-view-result-body [data-entity-class="${entityClass}"]`).first();
  await expect(result).toBeVisible();
  const id = await result.getAttribute('data-entity-id');
  expect(id).toMatch(/^[0-9a-f-]{36}$/);
  await result.click();
  await expect(page.locator('#data-entity-detail')).toBeVisible();
  return id;
}

async function createAndUpdateObservation(page, request, entityClass, parentId, position) {
  await page.locator('#data-entity-detail [data-role="observations-tab"]').click();
  await expect(page.locator('#data-entity-detail-observations')).toBeVisible();
  const designation = `Qualification 4F ${entityClass}`;
  await page.locator('#data-entity-detail-create-observation').click();
  await expect(page.locator('#observation-create-dialog')).toBeVisible();
  await page.locator('#observation-create-dialog [name="designation"]').fill(designation);
  const createdResponse = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname.endsWith('/api/observations'));
  await page.locator('#observation-create-dialog [type="submit"]').click();
  const createdHttp = await createdResponse;
  expect(createdHttp.status()).toBe(201);
  const created = await createdHttp.json();
  expect(created.parent.id).toBe(parentId);
  expect(created.parent.resource).toBe(entityClass);
  await expect(page.locator('#observation-create-status')).toContainText('créée');
  await page.locator('#observation-create-close').click();

  const row = page.locator('#data-entity-detail-observation-list button', { hasText: designation });
  await expect(row).toBeVisible();
  await row.click();
  const dialog = page.locator('#observation-detail-dialog');
  await expect(dialog).toBeVisible();
  const updatedDesignation = `${designation} modifiée`;
  await dialog.locator('[name="designation"]').fill(updatedDesignation);
  const updateRequest = page.waitForRequest(request => request.method() === 'PUT'
    && new URL(request.url()).pathname.endsWith(`/api/observations/${created.id}`));
  const updateResponse = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname.endsWith(`/api/observations/${created.id}`));
  await dialog.locator('[type="submit"]').click();
  expect((await updateRequest).headers()['if-match']).toBe('"1"');
  expect((await updateResponse).status()).toBe(200);
  await expect(dialog.locator('h2')).toContainText(updatedDesignation);

  if (position === 0) {
    await dialog.locator('.photo-add').click();
    const photoDialog = page.locator('#photo-editor-dialog');
    await photoDialog.locator('[name="file"]').setInputFiles({
      name: 'qualification-4f.png', mimeType: 'image/png', buffer: png,
    });
    await photoDialog.locator('[name="libelle"]').fill('Photo réelle Base 4F');
    const photoResponse = page.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).pathname.endsWith('/api/photos'));
    await photoDialog.locator('[type="submit"]').click();
    const photoHttp = await photoResponse;
    expect(photoHttp.status()).toBe(201);
    const photo = await photoHttp.json();
    expect(photo.observation_id).toBe(created.id);
    await expect(photoDialog.locator('[role="status"]')).toContainText('Photo ajoutée');
    const content = await request.get(photo.content_url);
    expect(content.status()).toBe(200);
    expect((await content.body()).length).toBeGreaterThan(20);
    await photoDialog.locator('[data-close]').click();
    await expect(dialog.locator('[data-photos]')).toContainText('Photo réelle Base 4F');
    await dialog.getByText('Voir la photo').click();
    const viewer = page.locator('.photo-viewer');
    await expect(viewer.locator('img')).toBeVisible();
    await viewer.getByRole('button', { name: 'Fermer' }).click();
    await dialog.getByRole('button', { name: 'Modifier les métadonnées' }).click();
    await photoDialog.locator('[name="libelle"]').fill('Photo réelle Base 4F modifiée');
    const metadataRequest = page.waitForRequest(request => request.method() === 'PUT'
      && new URL(request.url()).pathname.endsWith(`/api/photos/${photo.id}`));
    await photoDialog.locator('[type="submit"]').click();
    expect((await metadataRequest).headers()['if-match']).toBe('"1"');
    await expect(photoDialog.locator('[role="status"]')).toContainText('Photo enregistrée');
    await photoDialog.locator('[data-close]').click();
    await expect(dialog.locator('[data-photos]')).toContainText('Photo réelle Base 4F modifiée');
  }

  await dialog.locator('[data-close]').click();
  await expect(page.locator('#data-entity-detail-observation-list')).toContainText(updatedDesignation);
  const authoritative = await request.get(`/api/observations/${created.id}`);
  expect(authoritative.status()).toBe(200);
  expect((await authoritative.json()).parent).toMatchObject({ id: parentId });
  return updatedDesignation;
}

test('les huit catégories traversent Base, PostgreSQL, Observations et Carte réels', async ({ page, request }) => {
  await ensureSpecialStructure(request);
  for (let position = 0; position < CASES.length; position += 1) {
    const [entityClass, endpoint] = CASES[position];
    const id = await openFirstResult(page, entityClass);
    const beforeHttp = await request.get(`${endpoint}/${id}`);
    expect(beforeHttp.status()).toBe(200);
    const before = await beforeHttp.json();
    const beforeValues = properties(before);

    await page.locator('#data-entity-detail-edit').click();
    const comment = `Qualification intégrée 4F ${position + 1}`;
    await page.locator('#data-entity-detail .entity-form__form [name="commentaire"]').fill(comment);
    const updateRequest = page.waitForRequest(request => request.method() === 'PUT'
      && new URL(request.url()).pathname.endsWith(`${endpoint}/${id}`));
    const updateResponse = page.waitForResponse(response => response.request().method() === 'PUT'
      && new URL(response.url()).pathname.endsWith(`${endpoint}/${id}`));
    await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
    const updateHttpRequest = await updateRequest;
    const response = await updateResponse;
    expect(updateHttpRequest.headers()['if-match']).toBe(`"${beforeValues.version}"`);
    expect(response.status()).toBe(200);
    expect(updateHttpRequest.postDataJSON()).not.toHaveProperty('geometry');
    const afterHttp = await request.get(`${endpoint}/${id}`);
    const after = await afterHttp.json();
    expect(properties(after)).toMatchObject({ commentaire: comment,
      version: beforeValues.version + 1 });
    expect(after.geometry).toEqual(before.geometry);
    await expect(page.locator('#data-entity-detail-read')).toContainText(comment);

    const observationDesignation = await createAndUpdateObservation(
      page, request, entityClass, id, position,
    );
    await page.locator('#data-entity-detail-map').click();
    await expect(page).toHaveURL(new RegExp(`/carte\\?entity=${entityClass}&id=${id}$`));
    const editor = page.locator('#editor-panel');
    await expect(editor).toBeVisible();
    await expect(page.locator('#status')).toContainText('Objet ouvert');
    if (before.geometry) {
      expect(await page.evaluate(() => Number.isFinite(map.getCenter().lat)
        && Number.isFinite(map.getCenter().lng))).toBe(true);
    } else {
      await expect(page.locator('#status')).toContainText('non localisé');
    }
    const observationsTab = editor.locator('[data-role="observations-tab"]');
    await expect(observationsTab).toBeEnabled();
    await observationsTab.click();
    await expect(editor.getByText(observationDesignation, { exact: false }).first()).toBeVisible();
    await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    await expect(editor).toBeHidden();
  }
});

test('Base reste utilisable à 720 px et Carte ouvre un objet géolocalisé', async ({ page, request }) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await openFirstResult(page, 'mobilier');
  await expect(page).toHaveURL(/\/base$/);
  await expect(page.locator('#data-entity-detail')).toBeVisible();
  await expect(page.locator('#data-entity-detail [data-role="observations-tab"]')).toBeVisible();

  const identifier = await createLocatedSpecialStructure(request);
  await page.goto(`/carte?entity=ouvrages_particuliers&id=${identifier}`);
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#status')).toContainText('Objet ouvert');
});
