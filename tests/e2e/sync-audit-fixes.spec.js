const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { mockResourceJournal } = require('./mock-resource-journal');
const { execFileSync } = require('node:child_process');

const fc = features => ({ type: 'FeatureCollection', features });
const feature = (id, properties, geometry = { type: 'Point', coordinates: [2, 46] }) =>
  ({ type: 'Feature', properties: { id, version: 1, valid: true, ...properties }, geometry });
async function mock(context, intercept) {
  const disorder = feature('D42', { designation: 'Avant', troncon_ids: [], type_geometrie: 'Point' });
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route)) return;
    if (await intercept?.(route, disorder)) return;
    if (await mockResourceJournal(route)) return;
    const path = new URL(route.request().url()).pathname;
    let response = {};
    if (path === '/api/config') response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (path === '/api/systemes-endiguement') response = { systemes: [] };
    else if (path === '/api/desordres') response = fc([disorder]);
    else if (path === '/api/desordres/D42') response = disorder;
    else if (path === '/api/desordres/D42/observations') response = { parent: { resource: 'desordres', id: 'D42', designation: 'Avant' }, observations: [{ id: 'O1', parent: { resource: 'desordres', id: 'D42', designation: 'Avant' }, designation: 'Observation' }] };
    else if (path === '/api/observations/O1') response = { id: 'O1', version: 1, parent: { resource: 'desordres', id: 'D42', designation: 'Avant' }, suite: '', valid: true, photos: [] };
    else if (path === '/api/troncons' || path === '/api/territoire-administratif') response = fc([]);
    else if (path === '/api/troncons/options') response = { troncons: [] };
    else if (path === '/api/referentiels/types-desordre') response = { types: [] };
    else if (path === '/api/referentiels/categories-desordre') response = { categories: [] };
    else if (path.startsWith('/api/referentiels/')) response = { references: [] };
    else if (path === '/api/data/desordres/query') response = { columns: [], rows: [], total: 0, offset: 0, has_next: false };
    await route.fulfill({ json: response });
  });
  return disorder;
}
const event = (resource, ids) => ({ schemaVersion: 1, eventId: require('node:crypto').randomUUID(),
  originClientId: 'other-client', originInstanceId: 'other-instance', resource, action: 'update', ids, occurredAt: Date.now() });

test('001 : saisie pendant HTTP et référentiel retardés, puis abandon explicite et relecture', async ({ context, page }) => {
  let hold = false, release, requests = 0, writes = 0, suite = '';
  await mock(context, async route => {
    if (route.request().method() === 'PUT') writes++;
    if (!route.request().url().endsWith('/api/observations/O1')) return false;
    requests++;
    if (hold) { hold = false; await new Promise(resolve => { release = resolve; }); }
    await route.fulfill({ json: { id: 'O1', version: requests, parent: { resource: 'desordres', id: 'D42', designation: 'Avant' }, suite, photos: [] } }); return true;
  });
  const base = await context.newPage(); await base.goto('/base');
  await page.goto('/carte'); await page.locator('.leaflet-marker-icon').click();
  await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click(); await page.locator('.observation-row').click();
  await expect(page.locator('#observation-suite-complement')).toBeVisible();
  hold = true; suite = 'Serveur récent';
  const e = event('observations', ['O1']);
  await base.evaluate(e => { const c = new BroadcastChannel('digues-app:inter-page'); c.postMessage({ type: 'resource-mutated', source: 'data', payload: e }); c.close(); }, e);
  await expect.poll(() => requests).toBe(2);
  await page.locator('#observation-suite-complement').fill('Brouillon pendant HTTP'); release();
  await expect(page.locator('#observation-suite-message')).toContainText('saisie est conservée');
  await expect(page.locator('#observation-suite-complement')).toHaveValue('Brouillon pendant HTTP');
  expect(await page.evaluate(() => observationStale)).toBe(true);
  await page.locator('#back-to-observations').click(); await page.locator('.observation-row').click();
  await expect(page.locator('#observation-suite-complement')).toHaveValue('Serveur récent');
  expect(await page.evaluate(() => observationStale)).toBe(false);
  suite = 'Relecture sans saisie';
  await page.evaluate(e => ResourceMutationController.handleRemoteMutation(e), event('observations', ['O1']));
  await expect(page.locator('#observation-suite-complement')).toHaveValue('Relecture sans saisie');
  // All async boundaries, including a delayed already-cached reference loader.
  await page.evaluate(() => {
    const original = ReferenceData.load;
    ReferenceData.load = async (...args) => { const value = await original(...args); if (args[0] === "suitesApporter") await new Promise(r => { window.releaseReference = r; }); return value; };
    window.reread = openObservation('O1');
  });
  await page.waitForFunction(() => !!window.releaseReference);
  await page.locator('#observation-suite-complement').fill('Brouillon pendant référentiel');
  await page.evaluate(async () => { releaseReference(); await reread; });
  await expect(page.locator('#observation-suite-complement')).toHaveValue('Brouillon pendant référentiel');
  expect(writes).toBe(0);
  expect(await page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
});

for (const result of ['success', 'refresh-500', '409', '422', '500', 'network', 'timeout']) {
  test(`Observation PUT : brouillon verrouillé, double submit et restauration ${result}`, async ({ context, page }) => {
    let releasePut, releaseRead, puts = 0, reads = 0;
    let observation = { id: 'O1', version: 1, parent: { resource: 'desordres', id: 'D42', designation: 'Avant' }, suite: '', suite_apporter_id: null, photos: [] };
    let payload;
    await mock(context, async route => {
      if (!route.request().url().endsWith('/api/observations/O1')) return false;
      if (route.request().method() === 'PUT') {
        puts++;
        payload = route.request().postDataJSON();
        expect(route.request().headers()['if-match']).toBe('"1"');
        await new Promise(resolve => { releasePut = resolve; });
        if (['network', 'timeout'].includes(result)) await route.abort(result === 'timeout' ? 'timedout' : 'failed');
        else if (['409', '422', '500'].includes(result)) await route.fulfill({ status: Number(result), json: { detail: result } });
        else {
          observation = { ...observation, ...payload, version: 2 };
          await route.fulfill({ json: { ...observation, mutation: { ...event('observations', ['O1']), versions: { O1: 2 } } } });
        }
      } else {
        reads++;
        if (reads > 1) await new Promise(resolve => { releaseRead = resolve; });
        await route.fulfill({ status: reads > 1 && result === 'refresh-500' ? 500 : 200,
          json: reads > 1 && result === 'refresh-500' ? { detail: 'relecture impossible' } : observation });
      }
      return true;
    });
    await page.goto('/carte'); await page.locator('.leaflet-marker-icon').click();
    await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click(); await page.locator('.observation-row').click();
    const field = page.locator('#observation-suite-complement');
    const controls = page.locator('#observation-suite-form input, #observation-suite-form select, #observation-suite-form textarea, #observation-suite-form button, #back-to-observations, #editor-panel .entity-form [data-role=general-tab]:visible, #editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab], #close-editor');
    await field.fill('A');
    await page.evaluate(() => {
      window.observationPublications = [];
      const publish = ResourceMutationController.publishMutation;
      ResourceMutationController.publishMutation = function(mutation) {
        observationPublications.push(mutation); return publish.call(this, mutation);
      };
      const form = document.querySelector('#observation-suite-form');
      form.requestSubmit(); form.requestSubmit();
    });
    await expect.poll(() => puts).toBe(1);
    expect(payload).toEqual({ suite: 'A' });
    expect(await controls.evaluateAll(items => items.every(control => control.disabled))).toBe(true);
    await page.keyboard.type('B'); await expect(field).toHaveValue('A');
    expect(await page.evaluate(() => observationPublications.length)).toBe(0);
    releasePut();
    if (['success', 'refresh-500'].includes(result)) {
      await expect.poll(() => reads).toBe(2);
      expect(await controls.evaluateAll(items => items.every(control => control.disabled))).toBe(true);
      await page.keyboard.type('B'); await expect(field).toHaveValue('A');
      releaseRead();
      await expect(page.locator('#observation-suite-message')).toContainText(
        result === 'success' ? 'relue depuis PostgreSQL' : 'actualisation locale est incomplète');
    } else {
      await expect(page.locator('#observation-suite-message')).toHaveClass(/error/);
      await expect(page.locator('#observation-suite-message')).not.toContainText('relue depuis PostgreSQL');
      expect(reads).toBe(1);
    }
    await expect(field).toBeEnabled(); await expect(field).toHaveValue('A');
    expect(await controls.evaluateAll(items => items.every(control => !control.disabled))).toBe(true);
    expect(await page.evaluate(() => observationSuiteSubmitting)).toBe(false);
    expect(await page.evaluate(() => observationConflict)).toBe(result === '409');
    expect(await page.evaluate(() => observationPublications.length)).toBe(['success', 'refresh-500'].includes(result) ? 1 : 0);
    expect(await page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
    expect(puts).toBe(1);
    await field.fill('Nouvelle saisie volontaire');
    await expect(field).toHaveValue('Nouvelle saisie volontaire');
  });
}

test('Observation PUT : suite seule en attente offline, contrôles restaurés et intention persistante', async ({ context, page }) => {
  await mock(context);
  await page.goto('/carte'); await page.locator('.leaflet-marker-icon').click();
  await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click(); await page.locator('.observation-row').click();
  await expect(page.locator('#observation-suite-complement')).toBeVisible();
  await page.evaluate(async () => {
    ResourceEventTransport.stop();
    await OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: ['T1'], records: [
      { resource: 'observations', id: 'O1', serverVersion: currentObservation.version, payload: currentObservation },
      { resource: 'desordres', id: 'D42', serverVersion: 1, payload: lastServerFeature },
    ] });
  });
  let puts = 0;
  await context.route('**/api/**', route => {
    if (route.request().method() === 'PUT') puts++;
    return route.abort('internetdisconnected');
  });
  const field = page.locator('#observation-suite-complement');
  await field.fill('Suite terrain A');
  expect(await page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
  await page.locator('#observation-suite-form button').click();
  await expect(page.locator('#observation-suite-message')).toContainText('Enregistré localement');
  await expect(page.locator('#observation-suite-message')).toContainText('en attente de synchronisation');
  await expect(field).toBeEnabled(); await expect(field).toHaveValue('Suite terrain A');
  await expect(page.locator('#observation-suite-apporter')).toBeEnabled();
  await expect(page.locator('#back-to-observations')).toBeEnabled();
  const queued = await page.evaluate(async () => {
    const before = await OfflineRuntime.store.listOperations();
    await OfflineRuntime.store.close();
    return { before, reopened: await OfflineRuntime.store.listOperations() };
  });
  expect(queued.reopened).toEqual(queued.before);
  expect(queued.reopened).toHaveLength(1);
  expect(queued.reopened[0]).toMatchObject({ resource: 'observations', resourceId: 'O1', baseVersion: 1,
    status: 'pending', changes: { suite: 'Suite terrain A' } });
  expect(puts).toBe(0);
});

for (const result of ['success', '409', '422', '500', 'network']) {
  test(`002 : formulaire Base verrouillé pendant PUT, restauration ${result}`, async ({ context, page }) => {
    let release, puts = 0;
    await mock(context, async (route, disorder) => {
      if (route.request().method() !== 'PUT') return false;
      puts++; await new Promise(resolve => { release = resolve; });
      if (result === 'network') await route.abort('failed');
      else if (result !== 'success') await route.fulfill({ status: Number(result), json: { detail: result } });
      else { Object.assign(disorder.properties, route.request().postDataJSON()); await route.fulfill({ json: disorder }); }
      return true;
    });
    const map = await context.newPage(); await map.goto('/carte');
    await page.goto('/base');
    // The authenticated bootstrap loads the entity listener asynchronously.
    await expect(page.locator('main')).toHaveAttribute('data-layout', /.+/);
    await page.evaluate(() => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
      detail: { entityClass: 'desordres', entityId: 'D42', entities: [], source: 'home' },
    })));
    await page.locator('#data-entity-detail-edit').click();
    const field = page.locator('#data-entity-edit-designation');
    await field.fill('A'); await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
    await expect.poll(() => puts).toBe(1);
    expect(await page.locator('#data-entity-detail .entity-form__fields').evaluate(el => [...el.querySelectorAll('input,select,textarea')].every(c => c.disabled))).toBe(true);
    await expect(field).toBeDisabled();
    await expect(page.locator('#data-entity-detail-edit')).toBeDisabled();
    await expect(page.locator('#data-entity-detail-back')).toBeDisabled();
    // A real keyboard action cannot create B while PUT(A) is in flight.
    await page.keyboard.type('B'); await expect(field).toHaveValue('A');
    release();
    await expect(page.locator('#data-entity-detail .entity-form__actions [data-role=save]')).toBeEnabled();
    if (result === 'success') {
      await page.locator('#data-entity-detail-edit').click(); await expect(field).toHaveValue('A');
    } else { await expect(field).toBeEnabled(); await expect(field).toHaveValue('A'); }
    expect(puts).toBe(1);
  });
}

for (const order of ['BC-SSE', 'SSE-BC']) {
  test(`005 : invalidation échouée reprise sans republication ${order}`, async ({ context, page }) => {
    let fail = true, oneReads = 0, collectionReads = 0;
    const disorder = await mock(context, async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/desordres') collectionReads++;
      if (path !== '/api/desordres/D42') return false;
      oneReads++;
      await route.fulfill({ status: fail ? 500 : 200, json: fail ? { detail: 'temporaire' } : disorder }); return true;
    });
    await page.goto('/carte'); await expect(page.locator('#status')).toContainText('1 désordre');
    if (order === 'SSE-BC') await page.evaluate(() => ResourceEventTransport.stop());
    await page.evaluate(disorder => OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: ['T'],
      records: [{ resource: 'desordres', id: 'D42', serverVersion: 1, payload: disorder }] }), disorder);
    const manual = await context.newPage(); await manual.goto('/base');
    await page.evaluate(() => { window.sent = 0; const send = BroadcastChannel.prototype.postMessage;
      BroadcastChannel.prototype.postMessage = function(e) { if (e.type === 'resource-mutated') sent++; return send.call(this, e); }; });
    disorder.properties.designation = 'Récent';
    disorder.properties.version = 2;
    const e = event('desordres', ['D42']);
    const bc = () => manual.evaluate(e => { const c = new BroadcastChannel('digues-app:inter-page'); c.postMessage({ type: 'resource-mutated', source: 'data', payload: e }); c.close(); }, e);
    const sse = () => page.evaluate(e => ResourceMutationController.handleRemoteMutation(e), e);
    await (order === 'BC-SSE' ? bc() : sse());
    await expect.poll(() => oneReads).toBe(1);
    await expect.poll(() => page.evaluate(() => desordreLayersById.get('D42').feature.properties.designation)).toBe('Avant');
    await page.evaluate(async disorder => OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '1', tronconIds: ['T'],
      records: [{ resource: 'desordres', id: 'D42', serverVersion: 2, payload: disorder }] }, (await OfflineRuntime.inspect()).scope.revision), disorder);
    fail = false; await (order === 'BC-SSE' ? sse() : bc());
    await expect.poll(() => page.evaluate(() => desordreLayersById.get('D42').feature.properties.designation)).toBe('Récent');
    expect(collectionReads).toBe(2); expect(await page.evaluate(() => sent)).toBe(0);
  });
}

for (const type of ['systeme', 'desordre']) {
  for (const outcome of ['network', '500', '422', 'success']) {
    test(`007 : création ${type} ${outcome}, brouillon/commandes et double submit`, async ({ context, page }) => {
      let release, writes = 0;
      const createdSystem = { id: require('node:crypto').randomUUID(), libelle: 'Création', version: 1, valid: true };
      await mock(context, async route => {
        if (route.request().method() === 'GET' && new URL(route.request().url()).pathname === `/api/systemes-endiguement/${createdSystem.id}`) {
          await route.fulfill({json:createdSystem}); return true;
        }
        if (route.request().method() !== 'POST' || !/\/api\/(systemes-endiguement|desordres)$/.test(route.request().url())) return false;
        writes++; await new Promise(r => { release = r; });
        if (outcome === 'network') await route.abort('failed');
        else if (outcome !== 'success') await route.fulfill({ status: Number(outcome), json: { detail: outcome } });
        else await route.fulfill({ status: 201, json: type === 'systeme' ? createdSystem
          : feature('D42', { designation: 'Création', troncon_ids: [], type_geometrie: 'Point' }) });
        return true;
      });
      await page.goto('/carte');
      await page.waitForFunction(() => window.MapResourceConsumers && mapViewportReady);
      await page.locator('#toggle-create-menu').click();
      await page.locator(`[data-create-type="${type}"]`).click();
      const field = page.locator(type === 'systeme' ? '#heritage-object-label' : '#desordre-create-designation');
      await field.fill('Création');
      if (type === 'desordre') await page.evaluate(() => {
        provisionalDesordreLayer = L.marker([46, 2]); map.addLayer(provisionalDesordreLayer);
      });
      const form = type === 'systeme' ? '#heritage-object-editor' : '#desordre-editor';
      const save = page.locator(type === 'systeme' ? '#submit-create' : '#submit-desordre-create');
      const cancel = page.locator(type === 'systeme' ? '#cancel-create' : '#cancel-desordre-create');
      await page.evaluate(selector => { const form = document.querySelector(selector); form.requestSubmit(); form.requestSubmit(); }, form);
      await expect.poll(() => writes).toBe(1); await expect(save).toBeDisabled();
      release(); await expect(save).toBeEnabled(); await expect(cancel).toBeEnabled();
      await expect(page.locator('#editor-panel .entity-form__header [data-role=close]:visible')).toBeEnabled();
      expect(await page.evaluate(() => creationRequestInFlight)).toBe(false);
      if (outcome !== 'success') { await expect(field).toHaveValue('Création'); await expect(page.locator('#editor-panel')).toBeVisible(); }
      else if (type === 'systeme') {
        await expect(page.locator('#data-entity-detail .entity-form__fields [name=libelle]')).toHaveValue('Création');
        await expect(page.locator('#data-entity-detail .entity-form__actions [data-role=save]')).toBeVisible();
        expect(await page.evaluate(() => heritageAttributeEditor.getState().id)).toBe(createdSystem.id);
      } else {
        await expect(save).toHaveText('Enregistrer');
        expect(await page.evaluate(() => editorState.mode)).toBe('edit');
      }
      expect(await page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
      expect(writes).toBe(1);
    });
  }
}

async function fixture(request) {
  const s = await (await request.post('/api/systemes-endiguement', { data: { libelle: 'Audit correction' } })).json();
  const d = await (await request.post('/api/digues', { data: { libelle: 'Audit', systeme_endiguement_id: s.id } })).json();
  const t = await (await request.post('/api/troncons', { data: { libelle: 'Audit', digue_id: d.id,
    geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] } } })).json();
  const disorder = await (await request.post('/api/desordres', { data: { designation: 'Initial audit', troncon_ids: [t.properties.id],
    geometry: { type: 'Point', coordinates: [2, 46] } } })).json();
  return { trunk: t.properties.id, disorder: disorder.properties };
}
async function ready(page) { await page.waitForFunction(() => window.OfflineOperationsRuntime && ResourceEventTransport.getCursor() !== null); }
async function operations(page) { return page.evaluate(() => OfflineRuntime.store.listOperations()); }

test('projection périmée : lot atomique, intention intacte et seconde écriture inutile évitée', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async () => {
    const store = LocalProjection.createStore({ name: `stale-batch-${crypto.randomUUID()}` });
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, cursor: '42', tronconIds: ['T1'],
      reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] },
      observationReferences: { suitesApporter: [], urgences: [] },
      records: [1, 2, 3].map(i => ({ resource: 'desordres', id: `D${i}`, serverVersion: i,
        payload: { properties: { id: `D${i}`, version: i, commentaire: i === 1 ? null : '' } } })) });
    await store.enqueue({ resource: 'desordres', resourceId: 'D1', baseVersion: 1, changes: { designation: 'Intention' } });
    const before = await store.inspect(), intentions = await store.listOperations();
    const put = IDBObjectStore.prototype.put;
    let writes = 0, aborted = false;
    IDBObjectStore.prototype.put = function (...args) {
      const request = put.apply(this, args);
      if (this.name === 'resources' && ++writes === 2) this.transaction.abort();
      return request;
    };
    try { await store.markStale(); } catch (_) { aborted = true; }
    finally { IDBObjectStore.prototype.put = put; }
    const afterAbort = await store.inspect();
    await store.markStale();
    const after = await store.inspect();
    writes = 0;
    IDBObjectStore.prototype.put = function (...args) {
      if (this.name === 'resources') writes++;
      return put.apply(this, args);
    };
    try { await store.markStale(); }
    finally { IDBObjectStore.prototype.put = put; }
    const repeated = await store.inspect(), remaining = await store.listOperations();
    await store.close();
    return { before, intentions, aborted, afterAbort, after, repeated, remaining, writes };
  });
  expect(result.aborted).toBe(true);
  expect(result.afterAbort).toEqual(result.before);
  expect(result.after).toEqual({ ...result.before,
    records: result.before.records.map(row => ({ ...row, syncState: 'stale' })) });
  expect(result.repeated).toEqual(result.after);
  expect(result.remaining).toEqual(result.intentions);
  expect(result.writes).toBe(0);
});

test.describe('corrections projection sur PostgreSQL jetable', () => {
  test.skip(process.env.DIGUES_E2E_OCC !== '1', 'Requires disposable PostGIS');
  test('confirmed survit à une fermeture avant intégration sans second PUT', async ({ browser, request, baseURL }) => {
    const f = await fixture(request), context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
    try {
      let page = await context.newPage(); await page.goto('/base'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      await page.evaluate(() => ResourceEventTransport.stop());
      let committed = false, puts = 0;
      context.on('request', r => { if (r.method() === 'PUT') puts++; });
      await context.route(`**/api/desordres/${f.disorder.id}`, async route => {
        if (route.request().method() !== 'PUT') return route.continue();
        const response = await route.fetch(); committed = true; await route.fulfill({ response });
      });
      await context.route('**/api/offline-scope?*', route => committed ? route.abort('failed') : route.continue());
      const result = await page.evaluate(f => OfflineOperationsRuntime.submit({ resource: 'desordres', resourceId: f.id,
        baseVersion: f.version, changes: { commentaire: 'Confirmé durable' } }), f.disorder);
      expect(result.status).toBe('confirmed');
      const stored = (await operations(page))[0];
      await page.close(); await context.unroute('**/api/offline-scope?*');
      page = await context.newPage(); await page.goto('/base'); await ready(page);
      // La reconnexion SSE peut invalider une tentative de rattrapage lancée
      // pendant le bootstrap. Réessayer l'action explicite sans renvoyer le PUT.
      await expect.poll(async () => {
        await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
        return operations(page);
      }, { timeout: 30000 }).toHaveLength(0);
      expect(puts).toBe(1);
      const row = await page.evaluate(id => OfflineRuntime.store.read('desordres', id), f.disorder.id);
      expect(row.serverVersion).toBe(stored.response.mutation.versions[f.disorder.id]);
      expect(row.payload.properties.commentaire).toBe('Confirmé durable');
    } finally { await context.close(); }
  });
  test('004 : un snapshot 500 ne désactive pas IndexedDB et se reprend sans reload', async ({ browser, request, baseURL }) => {
    const f = await fixture(request), context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
    try {
      const page = await context.newPage(); await page.goto('/base'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      await page.evaluate(() => ResourceEventTransport.stop());
      const before = await page.evaluate(() => OfflineRuntime.initialCursor());
      await context.setOffline(true);
      await page.evaluate(f => OfflineOperationsRuntime.submit({ resource: 'desordres', resourceId: f.id, baseVersion: f.version, changes: { commentaire: 'intention' } }), f.disorder);
      const mutation = await (await request.put(`/api/desordres/${f.disorder.id}`, { headers: { 'If-Match': `"${f.disorder.version}"` }, data: { designation: 'Auteur distant' } })).json();
      let failing = true;
      await page.route('**/api/offline-scope?*', route => {
        if (!failing) return route.continue();
        failing = false; return route.fulfill({ status: 500, json: { detail: 'ponctuel' } });
      });
      await context.setOffline(false);
      await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
      expect(await page.evaluate(() => OfflineRuntime.getProjectionError()?.code)).toBe('http-error');
      expect(await page.evaluate(() => OfflineRuntime.initialCursor())).toBe(before);
      expect((await operations(page))[0].status).toBe('pending');
      failing = false;
      await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
      await expect.poll(async () => (await operations(page))[0].status).toBe('conflict');
      expect(await page.evaluate(() => OfflineRuntime.getProjectionError())).toBeNull();
      expect(await page.evaluate(async id => (await OfflineRuntime.store.read('desordres', id)).serverVersion, f.disorder.id)).toBe(mutation.properties.version);
      expect(BigInt(await page.evaluate(() => OfflineRuntime.initialCursor()))).toBeGreaterThan(BigInt(before));
    } finally { await context.close(); }
  });

  for (const concurrent of [false, true]) {
    test(`003 : exactement 2000 → pending → 2001, reprise ciblée ${concurrent ? 'conflict' : 'succès'} et reconfiguration`, async ({ browser, request, baseURL }) => {
      test.setTimeout(180000);
      const f = await fixture(request);
      // 3 parents + 1 initial disorder + 1,996 padding = 2,000 scope objects.
      // The journal and replay are exercised by the real API mutations below.
      // Fixture only: restricted to the explicitly disposable local test DB.
      execFileSync('.venv/bin/python', ['-c', `
import sys
from tests.postgres_safety import connect_test_database
with connect_test_database('DIGUES_OCC_TEST_DSN', autocommit=True) as c:
 c.execute("WITH added AS (INSERT INTO desordres(id,designation,geometry,valid) SELECT gen_random_uuid(),'padding',ST_Transform(ST_SetSRID(ST_MakePoint(2,46),4326),3950),true FROM generate_series(1,1996) RETURNING id) INSERT INTO public.desordres__troncons(desordre_id,troncon_id) SELECT id,%s FROM added", (sys.argv[1],))
 c.execute("ANALYZE public.desordres, public.desordres__troncons, public.localisations_desordres")
`, f.trunk]);
      const context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
      const other = await browser.newContext({ baseURL });
      try {
        let page = await context.newPage(); await page.goto('/base'); await ready(page);
        // Keep the full real snapshot in IndexedDB; only return the evidence
        // needed here rather than serialize 2,000 payloads through Playwright.
        const prepared = await page.evaluate(async id => {
          const snapshot = await OfflineRuntime.prepare([id]);
          return { count: snapshot.records.length, cursor: snapshot.cursor };
        }, f.trunk);
        expect(prepared.count).toBe(2000);
        await page.evaluate(() => ResourceEventTransport.stop());
        await context.setOffline(true);
        const queued = await page.evaluate(f => OfflineOperationsRuntime.submit({ resource: 'desordres', resourceId: f.id, baseVersion: f.version, changes: { designation: 'Intention A' } }), f.disorder);
        expect(queued.status).toBe('pending');
        const author = await other.newPage(); await author.goto('/base'); await ready(author);
        if (concurrent) await author.evaluate(async f => {
          const r = await fetch(`/api/desordres/${f.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': `"${f.version}"` }, body: JSON.stringify({ designation: 'Auteur B' }) });
          if (!r.ok) throw Error('concurrent write');
        }, f.disorder);
        await other.request.post('/api/desordres', { data: { designation: '2001', troncon_ids: [f.trunk], geometry: { type: 'Point', coordinates: [2, 46] } } });
        const overflow = await request.get(`/api/offline-scope?troncon_id=${f.trunk}`);
        expect(overflow.status()).toBe(422); expect((await overflow.json()).detail.code).toBe('scope-too-large');
        // Durable pending survives page closure before recovery.
        await page.close(); await context.setOffline(false);
        page = await context.newPage(); await page.goto('/base'); await ready(page);
        const second = await context.newPage(); await second.goto('/carte'); await ready(second);
        await Promise.all([page, second].map(p => p.evaluate(() => OfflineOperationsRuntime.synchronize(true))));
        await expect.poll(async () => { const ops = await operations(page); return concurrent ? ops[0]?.status : ops.length; }, { timeout: 60000 }).toBe(concurrent ? 'conflict' : 0);
        const server = await (await request.get(`/api/desordres/${f.disorder.id}`)).json();
        expect(server.properties.designation).toBe(concurrent ? 'Auteur B' : 'Intention A');
        expect(server.properties.version).toBe(f.disorder.version + 1);
        expect(await page.evaluate(() => OfflineRuntime.initialCursor())).toBe(prepared.cursor);
        if (concurrent) {
          expect((await operations(page))[0].changes.designation).toBe('Intention A');
          const smaller = await fixture(request);
          await page.evaluate(id => OfflineRuntime.prepare([id]), smaller.trunk);
          expect((await operations(page))[0].operationId).toBe(queued.operationId);
          const pinned = await page.evaluate(async id => ({ row: await OfflineRuntime.store.read('desordres', id), all: await OfflineRuntime.inspect() }), f.disorder.id);
          expect(pinned.row.detached).toBe(true);
          expect(pinned.all.records.some(r => r.resource === 'troncons' && r.id === f.trunk)).toBe(true);
          await page.reload(); await ready(page);
          expect((await operations(page))[0].status).toBe('conflict');
        }
      } finally {
        await context.close(); await other.close();
        execFileSync('.venv/bin/python', ['-c', `
import sys
from tests.postgres_safety import connect_test_database
with connect_test_database('DIGUES_OCC_TEST_DSN', autocommit=True) as c:
 c.execute("WITH removed AS (DELETE FROM public.desordres__troncons l USING desordres d WHERE l.desordre_id=d.id AND d.designation='padding' AND l.troncon_id=%s RETURNING l.desordre_id) DELETE FROM desordres WHERE id IN (SELECT desordre_id FROM removed)", (sys.argv[1],))
 c.execute("ANALYZE public.desordres, public.desordres__troncons, public.localisations_desordres")
`, f.trunk]);
      }
    });
  }
});

test('Observation : contrat parent, contexte et photos conservés', async ({ context, page }) => {
  await mock(context, async route => {
    if (!route.request().url().endsWith('/api/observations/O1')) return false;
    await route.fulfill({ json: { id: 'O1', version: 7,
      parent: { resource: 'desordres', id: 'D42', designation: 'Fissure du parement' },
      designation: 'Contrôle visuel', suite: 'Surveiller', valid: true,
      photos: [{ id: 'P1', observation_id: 'O1', designation: 'Photo existante',
        nom_fichier: 'image.jpg', content_available: false }] } });
    return true;
  });
  await page.goto('/carte'); await page.locator('.leaflet-marker-icon').click();
  await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click(); await page.locator('.observation-row').click();
  await expect(page.locator('#observation-detail-view')).toBeVisible();
  await expect(page.locator('#observation-suite-form')).toContainText('Désordre — Fissure du parement');
  await expect(page.locator('#observation-suite-complement')).toHaveValue('Surveiller');
  await expect(page.locator('#observation-photos')).toContainText('Photo existante');
  expect(await page.evaluate(() => currentObservation.version)).toBe(7);
  // La représentation est générique, même si cette entrée UI reste dans Désordre.
  expect(await page.evaluate(() => observationParentText(observationParent({
    parent: { resource: 'troncons', id: 'T1', designation: 'Tronçon amont' },
  })))).toBe('Tronçon — Tronçon amont');
});

for (const [label, payload] of [
  ['autre Désordre', { parent: { resource: 'desordres', id: 'D99', designation: 'Autre' } }],
  ['autre famille au même ID', { parent: { resource: 'troncons', id: 'D42', designation: 'Tronçon' } }],
  ['parent explicite absent', { parent: null }],
  ['autre Observation', { id: 'O99', parent: { resource: 'desordres', id: 'D42', designation: 'Désordre' } }],
]) {
  test(`Observation : refuse ${label}`, async ({ context, page }) => {
    await mock(context, async route => {
      if (!route.request().url().endsWith('/api/observations/O1')) return false;
      await route.fulfill({ json: { id: 'O1', version: 1, photos: [], ...payload } }); return true;
    });
    await page.goto('/carte'); await page.locator('.leaflet-marker-icon').click();
    await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click(); await page.locator('.observation-row').click();
    await expect(page.locator('#observations-message')).toContainText(/invalide|incohérent|ne correspond pas/);
    await expect(page.locator('#observation-detail-view')).toBeHidden();
    expect(await page.evaluate(() => currentObservation)).toBeNull();
  });
}

test('Observation : changement de contexte pendant les référentiels', async ({ context, page }) => {
  await mock(context);
  await page.goto('/carte'); await page.locator('.leaflet-marker-icon').click();
  await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click(); await page.locator('.observation-row').click();
  await expect(page.locator('#observation-suite-form')).toContainText('Désordre — Avant');
  await page.evaluate(() => {
    ReferenceData.load = async (catalog) => { if (catalog === "suitesApporter") await new Promise(resolve => { window.releaseParentReference = resolve; }); return []; };
    window.parentRead = openObservation('O1');
  });
  await page.waitForFunction(() => !!window.releaseParentReference);
  expect(await page.evaluate(async () => {
    lastServerFeature = { properties: { id: 'D99' } };
    releaseParentReference(); return await parentRead;
  })).toBe(false);
});
