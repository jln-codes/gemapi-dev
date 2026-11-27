const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockResourceJournal } = require('./mock-resource-journal');
const D = '00000000-0000-4000-8000-000000000042';
const T = '00000000-0000-4000-8000-000000000012';
const fc = features => ({ type: 'FeatureCollection', features });
const feature = (id, props, geometry = { type: 'Point', coordinates: [2, 46] }) =>
  ({ type: 'Feature', properties: { id, version: 1, valid: true, ...props }, geometry });
const disorder = feature(D, { designation: 'Affouillement rive gauche', type_geometrie: 'Point', troncon_ids: [T] });
const trunk = feature(T, { libelle: 'Tronçon amont', digue_id: 'd1' }, { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] });
const dialog = page => page.locator('#observation-create-dialog');
const field = (page, name) => dialog(page).locator(`[name="${name}"]`);
const save = page => page.locator('#observation-create-submit');

function receipt(payload, request) {
  const id = payload.id;
  return { ...payload, parent: { ...payload.parent,
    designation: payload.parent.resource === 'desordres' ? 'Affouillement rive gauche' : 'Tronçon amont' },
    version: 1, photos: [], mutation: { schemaVersion: 1, eventId: randomUUID(), resource: 'observations',
      action: 'create', ids: [id], versions: { [id]: 1 }, occurredAt: Date.now(),
      originClientId: request.headers()['x-digues-app-client-id'], originInstanceId: request.headers()['x-digues-app-instance-id'] } };
}

async function mock(context, post) {
  const created = [];
  await context.route(/^https:\/\//, route => route.abort());
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const req = route.request(), path = new URL(req.url()).pathname;
    if (path === '/api/observations' && req.method() === 'POST') {
      const response = receipt(req.postDataJSON(), req);
      if (post) return post(route, response, created);
      created.push(response); return route.fulfill({ status: 201, json: response });
    }
    let response = {};
    if (path === '/api/config') response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (path === '/api/systemes-endiguement') response = { systemes: [{ id: 's1', libelle: 'SE', valid: true,
      digues: [{ id: 'd1', libelle: 'Digue', valid: true, troncons: [trunk.properties] }] }] };
    else if (path === '/api/desordres') response = fc([disorder]);
    else if (path === `/api/desordres/${D}`) response = disorder;
    else if (path === `/api/troncons/${T}`) response = trunk;
    else if (path === '/api/troncons') response = fc([trunk]);
    else if (path === '/api/territoire-administratif') response = fc([]);
    else if (path === '/api/troncons/options') response = { troncons: [trunk.properties] };
    else if (path === `/api/desordres/${D}/observations`) response = {
      parent: { resource: 'desordres', id: D, designation: disorder.properties.designation },
      observations: created.filter(o => o.parent.resource === 'desordres'),
    };
    else if (path.startsWith('/api/observations/')) response = created.find(o => path.endsWith(o.id)) || {};
    else if (path === '/api/referentiels/types-desordre') response = { types: [] };
    else if (path === '/api/referentiels/categories-desordre') response = { categories: [] };
    else if (path.startsWith('/api/referentiels/')) response = { references: [] };
    else if (path === '/api/data/desordres/query') response = { columns: [], rows: [], total: 0, offset: 0, has_next: false };
    await route.fulfill({ json: response });
  });
  return created;
}

async function openFromMap(page) {
  await page.goto('/carte');
  await page.locator('.desordre-point-marker').click();
  await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click();
  await page.locator('#create-desordre-observation').click();
  await expect(save(page)).toBeEnabled();
}

async function openFromBase(page, resource) {
  await page.goto('/base');
  await expect(page.locator('main')).toHaveAttribute('data-layout', /.+/);
  await page.evaluate(({ resource, id }) => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
    detail: { entityClass: resource, entityId: id, source: 'home', entities: [] },
  })), { resource, id: resource === 'troncons' ? T : D });
  await page.getByRole('button', { name: '+ Observation', exact: true }).click();
  await expect(save(page)).toBeEnabled();
}

test('création offline : valid courant explicite, édition et UUID durable', async ({ context, page }) => {
  let posts = 0;
  await mock(context, async route => { posts++; await route.abort(); });
  await openFromBase(page, 'troncons');
  await page.evaluate(async ({ id, payload }) => {
    await OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [id], records: [
      { resource: 'troncons', id, serverVersion: 1, payload },
    ] });
  }, { id: T, payload: trunk });
  await context.setOffline(true);
  // Les réponses mockées contournent setOffline ; le journal doit échouer aussi.
  await context.route('**/api/**', route => route.abort());
  await field(page, 'designation').fill('Inspection sans réseau');
  await expect(field(page, 'valid')).toHaveCount(0);
  const id = await dialog(page).getAttribute('data-observation-id');
  await save(page).click();
  await expect(page.locator('#observation-create-status')).toContainText('localement');
  const operation = await page.evaluate(async () => (await OfflineRuntime.store.listOperations())[0]);
  expect(operation.resourceId).toBe(id);
  expect(operation.changes.valid).toBe(true);
  expect(operation.changes.designation).toBe('Inspection sans réseau');
  expect(posts).toBe(0);
  await page.evaluate(async operation => {
    document.querySelector('#observation-create-dialog').close();
    ObservationCreate.edit(operation);
  }, operation);
  await expect(save(page)).toBeEnabled();
  await expect(field(page, 'valid')).toHaveCount(0);
  await field(page, 'designation').fill('Inspection corrigée');
  await save(page).click();
  await expect(page.locator('#observation-create-status')).toContainText('localement');
  const revised = await page.evaluate(async () => (await OfflineRuntime.store.listOperations())[0]);
  expect(revised.resourceId).toBe(id);
  expect(revised.changes.valid).toBe(true);
  expect(revised.changes.designation).toBe('Inspection corrigée');
});

for (const [ui, resource] of [['map', 'desordres'], ['base', 'desordres'], ['base', 'troncons']]) {
  test(`création contextuelle ${ui}/${resource} : UUID avant POST, reçu, contexte et résultat`, async ({ context, page }) => {
    let posts = 0, submitted;
    await mock(context, async (route, response, created) => {
      posts++; submitted = route.request().postDataJSON(); created.push(response);
      await route.fulfill({ status: 201, json: response });
    });
    if (ui === 'map') await openFromMap(page); else await openFromBase(page, resource);
    const id = await dialog(page).getAttribute('data-observation-id');
    expect(id).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
    expect(posts).toBe(0);
    await expect(page.locator('#observation-create-parent')).toContainText(resource === 'desordres' ? 'Affouillement rive gauche' : 'Tronçon amont');
    expect(await dialog(page).locator('[name="parent"]').count()).toBe(0);
    expect(await field(page, 'date').inputValue()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    await expect(field(page, 'valid')).toHaveCount(0);
    if (resource === 'troncons') await expect(page.locator('#observation-create-urgency')).toBeHidden();
    await field(page, 'designation').fill('Constat créé');
    await page.evaluate(() => {
      window.createdEvents = [];
      ResourceMutationController.registerInvalidator('open-detail', event => createdEvents.push(event));
    });
    await save(page).click();
    await expect(page.locator('#observation-create-result')).toContainText('Constat créé');
    expect(submitted.id).toBe(id);
    expect(submitted.parent).toEqual({ resource, id: resource === 'desordres' ? D : T });
    await expect.poll(() => page.evaluate(() => createdEvents.length)).toBe(1);
    expect(await page.evaluate(() => createdEvents[0].action)).toBe('create');
    expect(await page.evaluate(id => createdEvents[0].versions[id], id)).toBe(1);
    // Le même reçu vu par l'autre transport ne déclenche pas un second travail.
    expect(await page.evaluate(() => ResourceMutationController.dispatch(createdEvents[0]).accepted)).toBe(false);
    // Le même workflow Photo est accessible immédiatement, y compris depuis Tronçon.
    await page.locator('#observation-created-photos .photo-add').click();
    await expect(page.locator('#photo-editor-dialog [data-context]')).toContainText(id);
    await expect(page.locator('#photo-editor-dialog [type=submit]')).toBeEnabled();
    await page.locator('#photo-editor-dialog [data-close]').click();
    await page.locator('#observation-create-close').click();
    await expect(dialog(page)).not.toBeVisible();
    if (ui === 'map') {
      expect(await page.evaluate(() => lastServerFeature.properties.id)).toBe(D);
      await expect(page.locator('.observation-row')).toContainText('Constat créé');
      await page.locator('.observation-row').click();
      await expect(page.locator('#observation-detail-title')).toHaveText('Observation — Constat créé');
    } else await expect(page.locator('#data-entity-detail-title')).toContainText(resource === 'troncons' ? 'Tronçon amont' : 'Affouillement rive gauche');
    expect(await page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
  });
}

for (const viewport of [{ name: 'Samsung A13', width: 360, height: 800 }, { name: 'Dell Latitude', width: 1366, height: 768 }]) {
  test(`mise en page du dialogue Observation — ${viewport.name}`, async ({ context, page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mock(context); await page.goto(viewport.width < 700 ? '/carte' : '/base');
    await page.waitForFunction(() => Boolean(window.ObservationCreate));
    await page.evaluate(parent => ObservationCreate.open(parent), {
      resource: 'desordres', id: D, designation: 'Affouillement rive gauche',
    });
    await expect(save(page)).toBeEnabled();
    await expect(dialog(page).locator('.editor-title h2')).toHaveText('Nouvelle observation');
    expect(await dialog(page).locator('.observation-create-context').evaluate(element => getComputedStyle(element).position)).toBe('static');
    const contextGap = await dialog(page).evaluate(element => {
      const context = element.querySelector('.dialog-context').getBoundingClientRect();
      const form = element.querySelector('form').getBoundingClientRect();
      return form.top - context.bottom;
    });
    expect(contextGap).toBeGreaterThanOrEqual(0);
    expect(contextGap).toBeLessThanOrEqual(10);
    expect(await dialog(page).locator('fieldset').evaluate(fieldset => Array.from(fieldset.children, child =>
      Array.from(child.querySelectorAll('input, select, textarea'), control => control.name)
    ))).toEqual([
      ['designation'], ['date', 'urgence_id'], ['nombre_desordres', 'suite_apporter_id'],
      ['evolution'], ['suite'],
    ]);
    const dateColumns = await dialog(page).locator('.observation-date-row > label').evaluateAll(labels => labels.map(label => {
      const box = label.getBoundingClientRect(); return { left: box.left, top: box.top, width: box.width };
    }));
    expect(dateColumns).toHaveLength(2);
    expect(Math.abs(dateColumns[0].top - dateColumns[1].top)).toBeLessThan(1);
    expect(dateColumns[0].width).toBeCloseTo(160, 0);
    expect(dateColumns[1].left).toBeGreaterThan(dateColumns[0].left + dateColumns[0].width);
    const detailColumns = await dialog(page).locator('.dialog-field-row:not(.observation-date-row) > label').evaluateAll(labels =>
      labels.map(label => label.getBoundingClientRect().width));
    expect(detailColumns).toHaveLength(2);
    expect(Math.abs(detailColumns[0] - detailColumns[1])).toBeLessThan(1);
    const actionRows = await dialog(page).locator('.dialog-actions > button').evaluateAll(buttons => buttons.map(button => button.getBoundingClientRect().top));
    expect(actionRows).toHaveLength(2);
    expect(Math.abs(actionRows[0] - actionRows[1])).toBeLessThan(1);
    expect(await dialog(page).evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  });
}

test('Carte : création depuis le Tronçon sélectionné dans Patrimoine', async ({ context, page }) => {
  await mock(context); await page.goto('/carte');
  await page.waitForFunction(() => typeof heritagePanelController !== 'undefined' && heritagePanelController !== null);
  await page.locator('#toggle-heritage').click();
  await page.locator('.tree-toggle').nth(0).click(); await page.locator('.tree-toggle').nth(1).click();
  await page.locator(`.tree-name[data-object-id="${T}"]`).click();
  await page.locator('#create-troncon-observation').click();
  await expect(page.locator('#observation-create-parent')).toHaveText('Tronçon — Tronçon amont');
  await expect(save(page)).toBeEnabled(); await save(page).click();
  await expect(page.locator('#observation-create-result')).toBeVisible();
  expect(await page.evaluate(() => selectedHeritageObject.item.id)).toBe(T);
});

test('double submit verrouillé ; réponse perdue puis retry strict de la même intention', async ({ context, page }) => {
  let release, posts = [], committed;
  await mock(context, async (route, response, created) => {
    posts.push({ body: route.request().postDataJSON(), operation: route.request().headers()['operation-id'] });
    if (posts.length === 1) {
      committed = response; created.push(response);
      await new Promise(resolve => { release = resolve; }); await route.abort('failed');
    } else await route.fulfill({ status: 201, json: committed });
  });
  await openFromMap(page); await field(page, 'designation').fill('Saisie conservée');
  await save(page).click();
  await expect.poll(() => posts.length).toBe(1);
  await expect(field(page, 'designation')).toBeDisabled();
  await page.evaluate(() => document.querySelector('#observation-create-form').dispatchEvent(new Event('submit', { cancelable: true })));
  expect(posts.length).toBe(1); release();
  await expect(page.locator('#observation-create-status')).toContainText('Résultat non confirmé');
  await expect(field(page, 'designation')).toHaveValue('Saisie conservée');
  await expect(field(page, 'designation')).toBeDisabled();
  await expect(page.locator('#observation-create-close')).toBeDisabled();
  await save(page).click();
  await expect(page.locator('#observation-create-result')).toContainText('Saisie conservée');
  expect(posts).toHaveLength(2); expect(posts[1]).toEqual(posts[0]);
});

for (const [status, code, label] of [[422, 'validation', 'Validation refusée'], [409, 'observation-id-collision', 'Collision d’UUID'],
  [409, 'operation-id-collision', 'Collision d’Operation-Id'], [500, 'server', 'Résultat non confirmé']]) {
  test(`erreur ${status}/${code} : contexte et saisie conservés`, async ({ context, page }) => {
    await mock(context, route => route.fulfill({ status, json: { detail: { code, message: 'Erreur test' } } }));
    await openFromMap(page); await field(page, 'evolution').fill('Brouillon terrain');
    const id = await dialog(page).getAttribute('data-observation-id'); await save(page).click();
    await expect(page.locator('#observation-create-status')).toContainText(label);
    await expect(field(page, 'evolution')).toHaveValue('Brouillon terrain');
    await expect(page.locator('#observation-create-parent')).toContainText('Affouillement rive gauche');
    expect(await dialog(page).getAttribute('data-observation-id')).toBe(id);
    if (status === 422) {
      await expect(field(page, 'evolution')).toBeDisabled();
      await expect(save(page)).toBeDisabled();
      await expect(page.locator('#observation-create-close')).toBeEnabled();
    }
    if (status === 409) await expect(save(page)).toBeDisabled();
    expect(await page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
  });
}

test('succès POST puis publication défaillante reste un succès et ouvre la réponse', async ({ context, page }) => {
  await mock(context); await openFromMap(page); await field(page, 'designation').fill('Déjà enregistrée');
  await page.evaluate(() => { ResourceMutationController.publishMutation = async () => { throw new Error('transport indisponible'); }; });
  await save(page).click();
  await expect(page.locator('#observation-create-result')).toContainText('Déjà enregistrée');
  await expect(page.locator('#observation-create-status')).toContainText('créée et enregistrée');
  await expect(page.locator('#observation-create-status')).toContainText('incomplète');
  await expect(page.locator('#observation-create-close')).toBeEnabled();
});

test('parent non préparé : panne réelle, saisie conservée sans intention durable', async ({ context, page }) => {
  let posts = 0; await mock(context, route => { posts++; return route.abort('failed'); }); await openFromMap(page);
  await field(page, 'designation').fill('Local seulement'); await context.setOffline(true);
  await save(page).click();
  await expect(page.locator('#observation-create-status')).toContainText('Résultat non confirmé');
  await expect(field(page, 'designation')).toHaveValue('Local seulement'); expect(posts).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
});

test('création réelle : réponse perdue, retry, SSE vers une autre Carte et un seul événement', async ({ browser, request, baseURL }) => {
  test.skip(process.env.DIGUES_E2E_OCC !== '1', 'PostgreSQL jetable requis');
  test.setTimeout(90000);
  const systeme = await (await request.post('/api/systemes-endiguement', { data: { libelle: 'LOT2 SE' } })).json();
  const digue = await (await request.post('/api/digues', { data: { libelle: 'LOT2 Digue', systeme_endiguement_id: systeme.id } })).json();
  const troncon = await (await request.post('/api/troncons', { data: { libelle: 'LOT2 Tronçon', digue_id: digue.id,
    geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] } } })).json();
  const parent = await (await request.post('/api/desordres', { data: { designation: 'LOT2 Parent',
    troncon_ids: [troncon.properties.id], geometry: { type: 'Point', coordinates: [2, 46] } } })).json();
  const id = parent.properties.id;
  const a = await browser.newContext({ baseURL }), b = await browser.newContext({ baseURL });
  try {
    const author = await a.newPage(), reader = await b.newPage();
    await Promise.all([author.goto('/base'), reader.goto('/carte')]);
    await expect(author.locator('main')).toHaveAttribute('data-layout', /.+/);
    await reader.waitForFunction(id => typeof desordreLayersById !== 'undefined' && desordreLayersById.has(id), id);
    await reader.evaluate(id => openPointEditor(id, desordreLayersById.get(id)), id);
    await reader.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click();
    await expect(reader.locator('#observations-message')).toContainText('Aucune observation');
    await author.evaluate(id => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
      detail: { entityClass: 'desordres', entityId: id, source: 'home', entities: [] },
    })), id);
    await author.getByRole('button', { name: '+ Observation', exact: true }).click();
    await expect(save(author)).toBeEnabled();
    const newId = await dialog(author).getAttribute('data-observation-id');
    await field(author, 'designation').fill('LOT2 après commit perdu');
    const head = (await (await request.get('/api/resource-events/head')).json()).cursor;
    let first, attempts = [];
    await a.route('**/api/observations', async route => {
      attempts.push({ operation: route.request().headers()['operation-id'], body: route.request().postDataJSON() });
      const response = await route.fetch();
      expect(response.status()).toBe(201);
      if (attempts.length === 1) { first = await response.json(); await route.abort('failed'); }
      else { expect(await response.json()).toEqual(first); await route.fulfill({ response }); }
    });
    await save(author).click();
    await expect(author.locator('#observation-create-status')).toContainText('Résultat non confirmé');
    // L'autre client apprend le COMMIT par SSE avant que l'auteur ait son reçu.
    await expect(reader.locator('.observation-row')).toContainText('LOT2 après commit perdu', { timeout: 15000 });
    await expect(field(author, 'designation')).toHaveValue('LOT2 après commit perdu');
    await save(author).click();
    await expect(author.locator('#observation-create-result')).toContainText(newId);
    expect(attempts).toHaveLength(2); expect(attempts[0]).toEqual(attempts[1]);
    expect(first.version).toBe(1); expect(first.parent.id).toBe(id);
    const journal = await (await request.get(`/api/resource-events?after=${head}&limit=100`)).json();
    const events = journal.events.filter(row => row.event.resource === 'observations' && row.event.ids.includes(newId));
    expect(events).toHaveLength(1); expect(events[0].event).toEqual(first.mutation);
    await reader.locator('.observation-row').click();
    await expect(reader.locator('#observation-detail-title')).toHaveText('Observation — LOT2 après commit perdu');
    expect(await reader.evaluate(() => currentObservation.version)).toBe(1);
    expect(await author.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
  } finally { await a.close(); await b.close(); }
});
