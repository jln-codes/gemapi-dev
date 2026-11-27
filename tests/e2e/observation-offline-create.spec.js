const { test, expect } = require('@playwright/test');
const { chromium } = require('playwright');
const { randomUUID } = require('node:crypto');
const { mkdtempSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');

test('schéma v5 courant : create sans version, édition, claim atomique, épinglage et abandon', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async () => {
    const name = crypto.randomUUID(), parent = crypto.randomUUID(), id = crypto.randomUUID();
    let store = LocalProjection.createStore({ name });
    const row = { resource: 'troncons', id: parent, serverVersion: 1, payload: { properties: { id: parent, version: 1, libelle: 'Parent préparé' } } };
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [parent], records: [row] });
    const op = await store.enqueueCreate({ operationId: crypto.randomUUID(), changes: { id, parent: { resource: 'troncons', id: parent }, designation: 'Initial' } });
    const noOp = await store.editCreate(op.operationId, { ...op.changes, designation: ' Initial ' });
    const edited = await store.editCreate(op.operationId, { ...op.changes, designation: 'Modifiée' });
    const failures = [];
    for (const action of [
      () => store.enqueueCreate({ operationId: crypto.randomUUID(), changes: edited.changes }),
      () => store.enqueueCreate({ operationId: crypto.randomUUID(), changes: { ...edited.changes, id: crypto.randomUUID(), parent: { resource: 'troncons', id: crypto.randomUUID() } } }),
      () => store.clear(),
    ]) { try { await action(); failures.push(false); } catch (_) { failures.push(true); } }
    const staleClaim = await store.claimOperation(op.operationId);
    await store.close(); store = LocalProjection.createStore({ name });
    const reopened = (await store.listOperations()).find(o => o.action === 'create');
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '1', tronconIds: [crypto.randomUUID()], records: [] }, (await store.inspect()).scope.revision);
    const pinned = await store.read('troncons', parent);
    const local = LocalProjection.localCreation(reopened);
    const claimed = await store.claimOperation(edited.operationId);
    for (const action of [() => store.editCreate(edited.operationId, { ...edited.changes, designation: 'Trop tard' }),
      () => store.removeOperation(edited.operationId, true)]) {
      try { await action(); failures.push(false); } catch (_) { failures.push(true); }
    }
    await store.updateOperation(edited.operationId, { status: 'confirmed', response: { mutation: { versions: { [id]: 1 } } } });
    try { await store.removeOperation(edited.operationId); failures.push(false); } catch (_) { failures.push(true); }
    const authoritative = { resource: 'observations', id, serverVersion: 1, payload: { ...edited.changes, parent: edited.parentContext, version: 1 } };
    await store.integratePinned(edited.operationId, { resource: 'observations', id, records: [authoritative] });
    await store.removeOperation(edited.operationId);
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '2', tronconIds: [parent], records: [row] }, (await store.inspect()).scope.revision);
    const abandoned = await store.enqueueCreate({ operationId: crypto.randomUUID(), changes: { id: crypto.randomUUID(), parent: { resource: 'troncons', id: parent } } });
    await store.removeOperation(abandoned.operationId, true);
    return { op, noOp, edited, reopened, pinned, local, claimed, staleClaim, failures,
      remaining: await store.listOperations(), version: (await store.open()).version };
  });
  expect(result.version).toBe(5);
  expect(result.op).not.toHaveProperty('baseVersion'); expect(result.noOp.operationId).toBe(result.op.operationId);
  expect(result.edited.operationId).not.toBe(result.op.operationId); expect(result.edited.resourceId).toBe(result.op.resourceId);
  expect(result.reopened).toEqual(result.edited); expect(result.local.payload.version).toBeNull();
  expect(result.local.payload.designation).toBe('Modifiée'); expect(result.pinned.detached).toBe(true);
  expect(result.staleClaim).toBeNull(); expect(result.claimed.attempts).toBe(1);
  expect(result.failures).toEqual([true, true, true, true, true, true]); expect(result.remaining).toEqual([]);
});

async function fixture(request) {
  const s = await (await request.post('/api/systemes-endiguement', { data: { libelle: 'Observation offline' } })).json();
  const d = await (await request.post('/api/digues', { data: { libelle: 'Digue', systeme_endiguement_id: s.id } })).json();
  const t = await (await request.post('/api/troncons', { data: { libelle: 'Tronçon préparé', digue_id: d.id,
    geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] } } })).json();
  const disorder = await (await request.post('/api/desordres', { data: { designation: 'Désordre préparé', troncon_ids: [t.properties.id],
    geometry: { type: 'Point', coordinates: [2, 46] } } })).json();
  return { trunk: t.properties.id, disorder: disorder.properties.id };
}
async function ready(page) { await page.waitForFunction(() => window.OfflineOperationsRuntime && ResourceEventTransport.getCursor() !== null); }
async function intentions(page) { return page.evaluate(() => OfflineRuntime.store.listOperations()); }

test.describe('création durable réelle PostgreSQL / Chromium', () => {
  test.skip(process.env.DIGUES_E2E_OCC !== '1', 'Requires disposable PostGIS');
  for (const resource of ['desordres', 'troncons']) {
    test(`${resource} : formulaire, édition jamais envoyée, arrêt navigateur, consultation offline puis replay`, async ({ request, baseURL }) => {
      test.setTimeout(120000);
      const f = await fixture(request), parent = { resource, id: resource === 'troncons' ? f.trunk : f.disorder };
      const profile = mkdtempSync(join(tmpdir(), 'observation-create-offline-'));
      let context = await chromium.launchPersistentContext(profile, { baseURL, headless: true });
      try {
        await context.route(/^https:\/\//, route => route.abort());
        let page = await context.newPage(); await page.goto(resource === 'troncons' ? '/base' : '/carte'); await ready(page);
        await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
        await page.evaluate(() => navigator.serviceWorker.ready);
        await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
        await context.setOffline(true);
        if (resource === 'desordres') {
          await page.evaluate(id => openPointEditor(id), parent.id);
          await page.locator('#offline-attributes-form [name=designation]').fill('Brouillon du parent conservé');
          await page.locator('#offline-detail').getByRole('button', { name: '+ Observation', exact: true }).click();
        } else {
          // Base's connected editor hides business-create actions while the
          // backend is offline. The prepared local-detail UI owns this path.
          await page.evaluate(id => OfflineRuntime.showDetail('troncons', id), parent.id);
          await expect(page.locator('#offline-detail')).toContainText('Tronçon préparé');
          await page.locator('#offline-detail').getByRole('button', { name: '+ Observation', exact: true }).click();
        }
        await expect(page.locator('#observation-create-submit')).toBeEnabled({ timeout: 20000 });
        await expect(page.locator('#observation-create-parent')).toContainText('préparé');
        const id = await page.locator('#observation-create-dialog').getAttribute('data-observation-id');
        await page.locator('#observation-create-form [name=designation]').fill('Observation terrain');
        await page.locator('#observation-create-submit').dblclick();
        await expect(page.locator('#observation-create-status')).toContainText('enregistrée localement');
        let ops = await intentions(page); expect(ops).toHaveLength(1); expect(ops[0].resourceId).toBe(id); expect(ops[0].attempts).toBe(0);
        const firstId = ops[0].operationId;
        await expect(page.locator('#observation-created-photos')).toBeVisible();
        await expect(page.locator('#observation-created-photos')).toContainText('Photos en préparation');
        await page.locator('#observation-create-close').click();
        if (resource === 'desordres') await expect(page.locator('#offline-attributes-form [name=designation]')).toHaveValue('Brouillon du parent conservé');
        await page.locator('#offline-detail').getByRole('button', { name: /Observation : Observation terrain/ }).click();
        await page.locator('#offline-detail').getByRole('button', { name: 'Modifier la création locale' }).click();
        await expect(page.locator('#observation-create-submit')).toBeEnabled({ timeout: 20000 });
        await page.locator('#observation-create-form [name=suite]').fill('Complément avant envoi');
        await page.locator('#observation-create-submit').click();
        await expect(page.locator('#observation-create-status')).toContainText('enregistrée localement');
        ops = await intentions(page); expect(ops[0].operationId).not.toBe(firstId); expect(ops[0].resourceId).toBe(id);
        await page.locator('#observation-create-close').click();
        await context.close();
        context = await chromium.launchPersistentContext(profile, { baseURL, headless: true, offline: true });
        await context.route(/^https:\/\//, route => route.abort());
        page = await context.newPage(); await page.goto('/base'); await page.waitForFunction(() => !!window.OfflineOperationsRuntime);
        expect((await intentions(page))[0]).toEqual(ops[0]);
        if (resource === 'desordres') {
          await page.setViewportSize({ width: 375, height: 812 });
          // The existing header intentionally redirects Base to Carte on phones.
          await page.waitForURL('**/carte');
          await page.locator('#toggle-tools-menu').click();
          await page.getByRole('menuitem', { name: 'Consultation hors ligne' }).click();
        } else {
          await page.locator('#toggle-tools-menu').click();
          await page.getByRole('menuitem', { name: 'Synchronisation locale', exact: true }).click();
        }
        await page.locator('#offline-operations summary').click();
        await page.getByRole('button', { name: 'Ouvrir l’observation locale', exact: true }).click();
        await expect(page.locator('#offline-detail')).toContainText('Complément avant envoi');
        await expect(page.locator('#offline-detail')).toContainText('en attente de synchronisation');
        let posts = 0; context.on('request', req => { if (req.method() === 'POST' && req.url().endsWith('/api/observations')) posts++; });
        await context.setOffline(false); await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
        await expect.poll(() => intentions(page), { timeout: 30000 }).toHaveLength(0);
        const server = await (await request.get(`/api/observations/${id}`)).json();
        expect(server.version).toBe(1); expect(server.id).toBe(id); expect(server.parent).toMatchObject(parent);
        expect(server.suite).toBe('Complément avant envoi'); expect(posts).toBe(1);
        expect(await page.evaluate(async id => (await OfflineRuntime.store.read('observations', id)).serverVersion, id)).toBe(1);
        await expect(page.locator('#offline-detail h2')).toContainText('État serveur conservé', { timeout: 20000 });
      } finally { await context.close(); }
    });
  }

  test('réponse POST perdue, deux onglets, reçu unique et payload figé', async ({ browser, request, baseURL }) => {
    test.setTimeout(90000);
    const f = await fixture(request), context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
    try {
      const page = await context.newPage(); await page.goto('/base'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      const id = randomUUID(), operationId = randomUUID(), calls = [], receipts = [];
      let lose = true;
      await context.route('**/api/observations', async route => {
        calls.push({ headers: route.request().headers(), body: route.request().postDataJSON() });
        const response = await route.fetch(); receipts.push(await response.json());
        if (lose) { lose = false; await route.abort('failed'); }
        else await route.fulfill({ response });
      });
      await page.evaluate(({ id, operationId, parent }) => OfflineOperationsRuntime.submitCreate({ operationId,
        changes: { id, parent, designation: 'Réponse perdue' } }), { id, operationId, parent: { resource: 'troncons', id: f.trunk } });
      expect((await intentions(page))[0].attempts).toBe(1);
      expect(await page.evaluate(async () => {
        const op = (await OfflineRuntime.store.listOperations())[0];
        try { await OfflineRuntime.store.editCreate(op.operationId, { ...op.changes, designation: 'Interdit' }); return false; } catch (_) { return true; }
      })).toBe(true);
      const other = await context.newPage(); await other.goto('/carte'); await ready(other);
      await Promise.all([page.evaluate(() => OfflineOperationsRuntime.synchronize(true)), other.evaluate(() => OfflineOperationsRuntime.synchronize(true))]);
      await expect.poll(() => intentions(page)).toHaveLength(0);
      expect(calls).toHaveLength(2); expect(receipts[1]).toEqual(receipts[0]);
      expect(calls.map(c => c.headers['operation-id'])).toEqual([operationId, operationId]);
      expect(calls.every(c => c.headers['if-match'] === undefined)).toBe(true);
      expect(calls[0].body).toEqual(calls[1].body);
      expect(receipts[0].mutation.action).toBe('create'); expect(receipts[0].version).toBe(1);
      const journal = await (await request.get('/api/resource-events?after=0&limit=500')).json();
      expect(journal.events.filter(e => e.event.eventId === receipts[0].mutation.eventId)).toHaveLength(1);
    } finally { await context.close(); }
  });

  for (const collision of ['uuid', 'operation']) {
    test(`collision ${collision} : intention locale conservée et serveur intact`, async ({ browser, request, baseURL }) => {
      test.setTimeout(60000);
      const f = await fixture(request), context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
      try {
        const page = await context.newPage(); await page.goto('/base'); await ready(page);
        await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
        const id = randomUUID(), operationId = randomUUID(), changes = { id, parent: { resource: 'troncons', id: f.trunk }, designation: 'Locale' };
        await page.evaluate(value => OfflineRuntime.store.enqueueCreate(value), { operationId, changes });
        const response = await request.post('/api/observations', { headers: { 'Operation-Id': collision === 'uuid' ? randomUUID() : operationId },
          data: { ...changes, designation: 'Autre intention' } });
        expect(response.status()).toBe(201);
        await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
        const ops = await intentions(page); expect(ops).toHaveLength(1); expect(ops[0].status).toBe('conflict');
        expect(ops[0].changes.designation).toBe('Locale'); expect(ops[0].operationId).toBe(operationId);
        expect((await (await request.get(`/api/observations/${id}`)).json()).designation).toBe('Autre intention');
        await page.evaluate(id => OfflineRuntime.showDetail('observations', id), id);
        await expect(page.locator('#offline-detail')).toContainText('conflit');
      } finally { await context.close(); }
    });
  }

  test('scope remplacé avant replay : parent épinglé et confirmation par récupération ciblée', async ({ browser, request, baseURL }) => {
    test.setTimeout(90000);
    const f = await fixture(request), other = await fixture(request), context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
    try {
      const page = await context.newPage(); await page.goto('/base'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      const id = randomUUID(), operationId = randomUUID();
      await page.evaluate(({ id, operationId, parent }) => OfflineRuntime.store.enqueueCreate({ operationId, changes: { id, parent } }),
        { id, operationId, parent: { resource: 'troncons', id: f.trunk } });
      await page.evaluate(id => OfflineRuntime.prepare([id]), other.trunk);
      expect(await page.evaluate(async id => (await OfflineRuntime.store.read('troncons', id)).detached, f.trunk)).toBe(true);
      await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
      await expect.poll(() => intentions(page)).toHaveLength(0);
      const row = await page.evaluate(id => OfflineRuntime.store.read('observations', id), id);
      expect(row.serverVersion).toBe(1); expect(row.payload.parent.id).toBe(f.trunk); expect(row.detached).toBe(true);
      expect(await page.evaluate(async () => (await OfflineRuntime.inspect()).scope.tronconIds)).toEqual([other.trunk]);
    } finally { await context.close(); }
  });
});
