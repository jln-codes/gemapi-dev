const { test, expect } = require('@playwright/test');
const { chromium } = require('playwright');
const { mkdtempSync } = require('node:fs');
const { join } = require('node:path');
const { tmpdir } = require('node:os');

test('queue IndexedDB courante : ordre, réouverture, exclusivité et périmètre protégé', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async () => {
    const name = `operations-${crypto.randomUUID()}`;
    let store = LocalProjection.createStore({ name });
    const row = id => ({ resource: 'desordres', id, serverVersion: 1, payload: { properties: { id, version: 1, designation: 'serveur' } } });
    const snapshot = { schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '1', tronconIds: ['T1'], records: [row('D1'), row('D2')] };
    await store.replace(snapshot);
    const intent = id => ({ resource: 'desordres', resourceId: id, baseVersion: 1, changes: { designation: 'local' } });
    const first = await store.enqueue(intent('D1'));
    await store.updateOperation(first.operationId, { status: 'syncing' });
    let duplicate = false, clear = false, scope = false, forbidden = false;
    try { await store.enqueue(intent('D1')); } catch (_) { duplicate = true; }
    try { await store.clear(); } catch (_) { clear = true; }
    scope = await store.replace({ ...snapshot, tronconIds: ['T2'] }, (await store.inspect()).scope.revision);
    try { await store.enqueue({ ...intent('D2'), resource: 'territoires-administratifs' }); } catch (_) { forbidden = true; }
    await store.enqueue(intent('D2'));
    await store.close(); store = LocalProjection.createStore({ name });
    const reopened = await store.listOperations();
    const beforeRecovery = (await store.inspect()).scope.cursor;
    await store.integratePinned(first.operationId, { resource: 'desordres', id: 'D1', records: [
      { ...row('D1'), serverVersion: 2, payload: { properties: { id: 'D1', version: 2, designation: 'serveur récent' } } },
    ] });
    await store.integratePinned(first.operationId, { resource: 'desordres', id: 'D1', records: [row('D1')] });
    const afterRecovery = await store.read('desordres', 'D1');
    const recoveryCursor = (await store.inspect()).scope.cursor;
    await store.updateOperation(first.operationId, { status: 'conflict', message: '409' });
    await store.updateOperation(reopened[1].operationId, { status: 'failed', message: '422' });
    await store.close(); store = LocalProjection.createStore({ name });
    const resolvedErrors = await store.listOperations();
    await store.replace({ ...snapshot, cursor: '2', records: [] }, (await store.inspect()).scope.revision);
    const pinned = await store.read('desordres', 'D1');
    await store.removeOperation(first.operationId, true);
    const remaining = await store.listOperations();
    return { reopened, resolvedErrors, pinned, remaining, duplicate, clear, scope, forbidden, beforeRecovery, recoveryCursor, afterRecovery };
  });
  expect(result.reopened.map(o => o.sequence)).toEqual([1, 2]);
  expect(result.reopened[0].status).toBe('syncing');
  expect(result.reopened[0].changes.designation).toBe('local');
  expect(result.resolvedErrors.map(o => o.status)).toEqual(['conflict', 'failed']);
  expect(result.pinned.detached).toBe(true);
  expect(result.pinned.payload.properties.designation).toBe('serveur récent');
  expect(result.afterRecovery.serverVersion).toBe(2);
  expect(result.recoveryCursor).toBe(result.beforeRecovery);
  expect(result.reopened[0].basePayload.properties.designation).toBe('serveur');
  expect(result.remaining).toHaveLength(1);
  for (const key of ['duplicate', 'clear', 'scope', 'forbidden']) expect(result[key]).toBe(true);
});

async function fixture(request) {
  const s = await (await request.post('/api/systemes-endiguement', { data: { libelle: 'Offline opérations' } })).json();
  const d = await (await request.post('/api/digues', { data: { libelle: 'Offline', systeme_endiguement_id: s.id } })).json();
  const t = await (await request.post('/api/troncons', { data: { libelle: 'Offline', digue_id: d.id,
    geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] } } })).json();
  const disorder = await (await request.post('/api/desordres', { data: { designation: 'Initial', troncon_ids: [t.properties.id],
    geometry: { type: 'Point', coordinates: [2, 46] } } })).json();
  return { trunk: t.properties.id, disorder: disorder.properties };
}
async function ready(page) {
  await page.waitForFunction(() => window.OfflineOperationsRuntime && ResourceEventTransport.getCursor() !== null);
}
async function intentions(page) { return page.evaluate(() => OfflineRuntime.store.listOperations()); }
async function enqueue(page, value, designation) {
  return page.evaluate(({ value, designation }) => OfflineOperationsRuntime.submit({ resource: 'desordres',
    resourceId: value.id, baseVersion: value.version, changes: { designation } }), { value, designation });
}

test.describe('replay réel PostgreSQL et navigateur', () => {
  test.skip(process.env.DIGUES_E2E_OCC !== '1', 'Requires disposable PostGIS');
  test('arrêt complet du navigateur : profil persistant et reprise offline', async ({ request, baseURL }) => {
    test.setTimeout(120000);
    const f = await fixture(request);
    const profile = mkdtempSync(join(tmpdir(), 'digues-operations-profile-'));
    let context = await chromium.launchPersistentContext(profile, { baseURL, headless: true });
    try {
      const page = await context.newPage(); await page.goto('/base'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      await page.evaluate(() => navigator.serviceWorker.ready);
      await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
      await context.setOffline(true);
      const result = await enqueue(page, f.disorder, 'Après arrêt navigateur');
      await context.close();
      context = await chromium.launchPersistentContext(profile, { baseURL, headless: true, offline: true });
      const reopened = await context.newPage(); await reopened.goto('/base');
      await reopened.waitForFunction(() => !!window.OfflineOperationsRuntime);
      expect((await intentions(reopened))[0].operationId).toBe(result.operationId);
      expect((await intentions(reopened))[0].changes.designation).toBe('Après arrêt navigateur');
      await reopened.locator('#toggle-views').click();
      await expect(reopened.locator('#data-view-result-body')).toContainText('Initial');
      await context.setOffline(false);
      await reopened.evaluate(() => OfflineOperationsRuntime.synchronize(true));
      await expect.poll(() => intentions(reopened), { timeout: 30000 }).toHaveLength(0);
      const server = await (await request.get(`/api/desordres/${f.disorder.id}`)).json();
      expect(server.properties.designation).toBe('Après arrêt navigateur');
      expect(server.properties.version).toBe(f.disorder.version + 1);
    } finally { await context.close(); }
  });
  test('save explicite offline, fermeture, réouverture et deux onglets : une application', async ({ browser, request, baseURL }) => {
    test.setTimeout(120000);
    const f = await fixture(request), context = await browser.newContext({ baseURL });
    try {
      let page = await context.newPage(); await page.goto('/carte'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      await page.evaluate(() => navigator.serviceWorker.ready);
      await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
      await context.setOffline(true);
      await page.evaluate(id => OfflineRuntime.showDetail('desordres', id), f.disorder.id);
      await page.locator('#offline-attributes-form [name=designation]').fill('Terrain durable');
      expect(await intentions(page)).toHaveLength(0); // unvalidated draft is not an operation
      await page.locator('#offline-attributes-form button').click();
      await expect(page.locator('#offline-attributes-form')).toContainText('Enregistré localement');
      const first = (await intentions(page))[0];
      await page.close(); page = await context.newPage(); await page.goto('/base');
      await page.waitForFunction(() => !!window.OfflineOperationsRuntime);
      expect((await intentions(page))[0].operationId).toBe(first.operationId);
      expect((await intentions(page))[0].changes.designation).toBe('Terrain durable');
      const second = await context.newPage(); await second.goto('/carte');
      await second.waitForFunction(() => !!window.OfflineOperationsRuntime);
      let puts = 0;
      context.on('request', r => { if (r.method() === 'PUT' && r.url().endsWith(f.disorder.id)) puts++; });
      await context.setOffline(false);
      await Promise.all([page.evaluate(() => OfflineOperationsRuntime.synchronize(true)), second.evaluate(() => OfflineOperationsRuntime.synchronize(true))]);
      await expect.poll(() => intentions(page), { timeout: 30000 }).toHaveLength(0);
      expect(puts).toBe(1);
      const server = await (await request.get(`/api/desordres/${f.disorder.id}`)).json();
      expect(server.properties.designation).toBe('Terrain durable');
      expect(server.properties.version).toBe(f.disorder.version + 1);
      expect(await page.evaluate(async id => (await OfflineRuntime.store.read('desordres', id)).serverVersion, f.disorder.id)).toBe(server.properties.version);
    } finally { await context.close(); }
  });

  test('commit puis réponse perdue : même operationId, version et eventId ; aucun double journal', async ({ browser, request, baseURL }) => {
    test.setTimeout(90000);
    const f = await fixture(request), context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
    try {
      const page = await context.newPage(); await page.goto('/base'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      const receipts = [], ids = [];
      await page.route(`**/api/desordres/${f.disorder.id}`, async route => {
        if (route.request().method() !== 'PUT') return route.continue();
        ids.push(route.request().headers()['operation-id']);
        const response = await route.fetch(); receipts.push(await response.json());
        if (receipts.length === 1) return route.abort('failed');
        return route.fulfill({ response });
      });
      const first = await enqueue(page, f.disorder, 'Réponse perdue');
      expect(first.status).toBe('pending');
      expect(receipts).toHaveLength(1);
      await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
      await expect.poll(() => intentions(page), { timeout: 20000 }).toHaveLength(0);
      expect(ids).toEqual([first.operationId, first.operationId]);
      expect(receipts[1]).toEqual(receipts[0]);
      const events = await (await request.get('/api/resource-events?after=0&limit=500')).json();
      expect(events.events.filter(e => e.event.eventId === receipts[0].mutation.eventId)).toHaveLength(1);
      expect(receipts[0].properties.version).toBe(f.disorder.version + 1);
    } finally { await context.close(); }
  });

  test('deux clients : rattrapage avant replay, conflit conservé et serveur intact', async ({ browser, request, baseURL }) => {
    test.setTimeout(90000);
    const f = await fixture(request), a = await browser.newContext({ baseURL }), b = await browser.newContext({ baseURL });
    try {
      const page = await a.newPage(); await page.goto('/base'); await ready(page);
      await page.evaluate(id => OfflineRuntime.prepare([id]), f.trunk);
      await a.setOffline(true);
      const pending = await enqueue(page, f.disorder, 'Intention A conservée');
      expect(pending.status).toBe('pending');
      const author = await b.newPage(); await author.goto('/base'); await ready(author);
      const winner = await author.evaluate(async d => {
        const response = await fetch(`/api/desordres/${d.id}`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'If-Match': `"${d.version}"`, ...await ResourceMutationController.requestHeaders() },
          body: JSON.stringify({ designation: 'Auteur B' }) });
        return response.json();
      }, f.disorder);
      await a.setOffline(false);
      await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
      await expect.poll(async () => (await intentions(page))[0]?.status, { timeout: 25000 }).toBe('conflict');
      expect((await intentions(page))[0].changes.designation).toBe('Intention A conservée');
      const projection = await page.evaluate(async id => OfflineRuntime.store.read('desordres', id), f.disorder.id);
      expect(projection.payload.properties.designation).toBe('Auteur B');
      expect(projection.serverVersion).toBe(winner.properties.version);
      const final = await (await request.get(`/api/desordres/${f.disorder.id}`)).json();
      expect(final.properties.version).toBe(winner.properties.version);
      expect(final.properties.designation).toBe('Auteur B');
      await page.locator('#toggle-tools-menu').click();
      await page.getByRole('menuitem', { name: 'Synchronisation locale', exact: true }).click();
      await expect(page.locator('#offline-operations')).toContainText('Intention A conservée');
    } finally { await a.close(); await b.close(); }
  });
});
