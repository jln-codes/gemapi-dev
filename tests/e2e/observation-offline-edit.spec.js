const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockResourceJournal } = require('./mock-resource-journal');
test.use({ serviceWorkers: 'block' });

const observationId = '00000000-0000-4000-8000-0000000000a1';
const parentId = '00000000-0000-4000-8000-0000000000a2';
const catalogs = { suitesApporter: [{ id: 'suite-1', libelle: 'Surveiller' }],
  urgences: [{ id: 'urgent-1', libelle: 'Immédiate' }] };

async function setup(context, page, parent = 'troncons', path = '/base') {
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const path = new URL(route.request().url()).pathname;
    const payload = path === '/api/config' ? { legacy_single_database_enabled: true, show_uuid: false }
      : path === '/api/data/desordres/query' ? { columns: [], rows: [], total: 0, offset: 0, has_next: false }
        : path === '/api/systemes-endiguement' ? { systemes: [] }
          : { type: 'FeatureCollection', features: [] };
    await route.fulfill({ json: payload });
  });
  await page.goto(path);
  await page.waitForFunction(() => window.OfflineRuntime?.store
    && window.OfflineOperationsRuntime && window.ObservationForm);
  await page.evaluate(({ parent, observationId, parentId, catalogs }) => {
    OfflineOperationsRuntime.synchronize = async () => {};
    const payload = { id: observationId, version: 1,
      parent: { resource: parent, id: parentId, designation: 'Parent préparé' },
      designation: 'Inspection', date: '2026-09-20', evolution: 'Stable',
      suite_apporter_id: null, suite: 'Ancienne suite', urgence_id: null, nombre_desordres: null };
    return OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, cursor: '0', tronconIds: [parentId],
      observationReferences: catalogs,
      records: [{ resource: 'observations', id: observationId, serverVersion: 1, payload }] });
  }, { parent, observationId, parentId, catalogs });
}

test('Tronçon : cinq champs partiels, intention durable et réouverture après tentative', async ({ context, page }) => {
  await setup(context, page);
  await page.evaluate(id => OfflineRuntime.showDetail('observations', id), observationId);
  const form = page.locator('#offline-attributes-form');
  await expect(form).toContainText('Tronçon — Parent préparé');
  await expect(form.locator('[name="urgence_id"], [name="nombre_desordres"], [name="valid"], [name="observateur_source_id"]')).toHaveCount(0);
  await form.locator('[name="designation"]').fill('Inspection revue');
  await form.locator('[name="date"]').fill('2026-09-21');
  await form.locator('[name="evolution"]').fill('Progression');
  await form.locator('[name="suite_apporter_id"]').selectOption('suite-1');
  await form.locator('[name="suite"]').fill('Nouvelle suite');
  await form.locator('button[type="submit"]').click();
  const first = await page.evaluate(() => OfflineRuntime.store.listOperations());
  expect(first).toHaveLength(1);
  expect(first[0]).toMatchObject({ action: 'update', resource: 'observations', resourceId: observationId,
    baseVersion: 1, changes: { designation: 'Inspection revue', date: '2026-09-21',
      evolution: 'Progression', suite_apporter_id: 'suite-1', suite: 'Nouvelle suite' } });
  await page.locator('#offline-detail').getByRole('button', { name: 'Fermer' }).click();
  await page.evaluate(id => OfflineRuntime.showDetail('observations', id), observationId);
  await expect(form.locator('[name="suite"]')).toHaveValue('Nouvelle suite');
  if (first[0].attempts > 0) await expect(form.locator('[name="suite"]')).toBeDisabled();
  await page.reload();
  await page.waitForFunction(() => window.OfflineRuntime?.store && window.OfflineOperationsRuntime);
  expect((await page.evaluate(() => OfflineRuntime.store.listOperations()))[0].changes.suite).toBe('Nouvelle suite');
});

test('update jamais tenté : correction canonique renouvelle Operation-Id sans rebase', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async ({ observationId, parentId }) => {
    const store = LocalProjection.createStore({ name: crypto.randomUUID() });
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [parentId], records: [{
      resource: 'observations', id: observationId, serverVersion: 1,
      payload: { id: observationId, version: 1, parent: { resource: 'troncons', id: parentId } },
    }] });
    const first = await store.enqueue({ resource: 'observations', resourceId: observationId,
      baseVersion: 1, changes: { suite: 'Initiale' } });
    const same = await store.editUpdate(first.operationId, { suite: 'Initiale' });
    const changed = await store.editUpdate(first.operationId, { suite: 'Corrigée' });
    const claimed = await store.claimOperation(changed.operationId);
    let frozen = false;
    try { await store.editUpdate(changed.operationId, { suite: 'Après tentative' }); } catch (_) { frozen = true; }
    return { first, same, changed, claimed, frozen };
  }, { observationId, parentId });
  expect(result.same.operationId).toBe(result.first.operationId);
  expect(result.changed.operationId).not.toBe(result.first.operationId);
  expect(result.changed.baseVersion).toBe(1);
  expect(result.claimed.attempts).toBe(1);
  expect(result.frozen).toBe(true);
});

test('Désordre : champs spécifiques ; autres familles : champs spécifiques refusés même à null', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async ({ observationId, parentId }) => {
    const store = LocalProjection.createStore({ name: crypto.randomUUID() });
    const families = ['desordres', 'troncons', 'ouvrages_hydrauliques', 'equipements_mesure',
      'cheminements', 'mobilier', 'reseaux_techniques', 'amenagements_hydrauliques', 'vegetation'];
    const records = families.map((resource, index) => ({ resource: 'observations',
      id: `${observationId.slice(0, -1)}${index}`, serverVersion: 1,
      payload: { id: `${observationId.slice(0, -1)}${index}`, version: 1,
        parent: { resource, id: parentId, designation: resource } } }));
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [parentId], records });
    const allowed = await store.enqueue({ resource: 'observations', resourceId: records[0].id,
      baseVersion: 1, changes: { designation: 'Revue', urgence_id: 'urgent-1', nombre_desordres: 0 } });
    const forbidden = [];
    for (const key of ['parent', 'desordre_id', 'valid', 'observateur_source_id', 'version']) {
      try { await store.enqueue({ resource: 'observations', resourceId: records[1].id,
        baseVersion: 1, changes: { [key]: null } }); forbidden.push(false); } catch (_) { forbidden.push(true); }
    }
    const rejected = [];
    for (const row of records.slice(1)) {
      for (const field of ['urgence_id', 'nombre_desordres']) {
        try { await store.enqueue({ resource: 'observations', resourceId: row.id, baseVersion: 1,
          changes: { [field]: null } }); rejected.push(false); } catch (_) { rejected.push(true); }
      }
      const common = await store.enqueue({ resource: 'observations', resourceId: row.id,
        baseVersion: 1, changes: { suite: null } });
      if (common.changes.suite !== null) throw new Error('null perdu');
    }
    return { allowed: allowed.changes, forbidden, rejected, count: (await store.listOperations()).length };
  }, { observationId, parentId });
  expect(result.allowed).toEqual({ designation: 'Revue', urgence_id: 'urgent-1', nombre_desordres: 0 });
  expect(result.forbidden).toEqual(Array(5).fill(true));
  expect(result.rejected).toEqual(Array(16).fill(true));
  expect(result.count).toBe(9);
});

test('Désordre : formulaire partagé expose urgence et nombre ; contrôles adaptés à trois viewports', async ({ context, page }) => {
  await setup(context, page, 'desordres', '/carte');
  for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }, { width: 1366, height: 768 }]) {
    await page.setViewportSize(viewport);
    await page.evaluate(id => OfflineRuntime.showDetail('observations', id), observationId);
    const form = page.locator('#offline-attributes-form');
    await expect(form.locator('[name="urgence_id"]')).toBeVisible();
    await expect(form.locator('[name="nombre_desordres"]')).toBeVisible();
    expect(await page.locator('#offline-detail').evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.locator('#offline-detail').getByRole('button', { name: 'Fermer' }).click();
  }
});

test.describe('replay réel Observation sur PostgreSQL jetable', () => {
  test.skip(process.env.DIGUES_E2E_OCC !== '1', 'Requires disposable PostGIS');

  async function fixture(request) {
    const system = await (await request.post('/api/systemes-endiguement', { data: { libelle: 'M-01 SE' } })).json();
    const digue = await (await request.post('/api/digues', { data: {
      libelle: 'M-01 Digue', systeme_endiguement_id: system.id,
    } })).json();
    const troncon = await (await request.post('/api/troncons', { data: { libelle: 'M-01 Tronçon', digue_id: digue.id,
      geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] },
    } })).json();
    const id = randomUUID();
    const created = await request.post('/api/observations', { headers: { 'Operation-Id': randomUUID() },
      data: { id, parent: { resource: 'troncons', id: troncon.properties.id },
        designation: 'Inspection M-01', date: '2026-09-20', suite: 'Initiale' } });
    expect(created.status()).toBe(201);
    return { id, troncon: troncon.properties.id, observation: await created.json() };
  }

  test('Tronçon : replay partiel, réponse perdue, même reçu et événement unique', async ({ request, page, context }) => {
    test.setTimeout(90000);
    const f = await fixture(request);
    await page.goto('/base');
    await page.waitForFunction(() => window.OfflineOperationsRuntime && ResourceEventTransport.getCursor() !== null);
    await page.evaluate(id => OfflineRuntime.prepare([id]), f.troncon);
    await context.setOffline(true);
    const pending = await page.evaluate(({ id, version }) => OfflineOperationsRuntime.submit({
      resource: 'observations', resourceId: id, baseVersion: version,
      changes: { designation: 'Inspection revue', date: null, evolution: 'Progression',
        suite_apporter_id: null, suite: 'Suite de Tronçon' },
    }), { id: f.id, version: f.observation.version });
    expect(pending.status).toBe('pending');
    const receipts = [], operationIds = [];
    await page.route(`**/api/observations/${f.id}`, async route => {
      if (route.request().method() !== 'PUT') return route.continue();
      operationIds.push(route.request().headers()['operation-id']);
      const response = await route.fetch(); receipts.push(await response.json());
      if (receipts.length === 1) return route.abort('failed');
      return route.fulfill({ response });
    });
    await context.setOffline(false);
    await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
    await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
    await expect.poll(() => page.evaluate(() => OfflineRuntime.store.listOperations()), { timeout: 30000 }).toHaveLength(0);
    expect(operationIds).toEqual([pending.operationId, pending.operationId]);
    expect(receipts[1]).toEqual(receipts[0]);
    const detail = await (await request.get(`/api/observations/${f.id}`)).json();
    expect(detail).toMatchObject({ id: f.id, version: 2, designation: 'Inspection revue',
      date: null, evolution: 'Progression', suite: 'Suite de Tronçon' });
    const events = await (await request.get('/api/resource-events?after=0&limit=500')).json();
    expect(events.events.filter(entry => entry.event.eventId === receipts[0].mutation.eventId)).toHaveLength(1);
  });

  test('Tronçon : version v1 conservée, v2 distante donne 409 sans rebase', async ({ request, page, context }) => {
    test.setTimeout(90000);
    const f = await fixture(request);
    await page.goto('/base');
    await page.waitForFunction(() => window.OfflineOperationsRuntime && ResourceEventTransport.getCursor() !== null);
    await page.evaluate(id => OfflineRuntime.prepare([id]), f.troncon);
    await context.setOffline(true);
    const pending = await page.evaluate(({ id, version }) => OfflineOperationsRuntime.submit({
      resource: 'observations', resourceId: id, baseVersion: version, changes: { suite: 'Brouillon v1' },
    }), { id: f.id, version: f.observation.version });
    const winner = await request.put(`/api/observations/${f.id}`, { headers: { 'If-Match': '"1"',
      'Operation-Id': randomUUID() }, data: { suite: 'Serveur v2' } });
    expect(winner.status()).toBe(200);
    await context.setOffline(false);
    await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
    await expect.poll(async () => (await page.evaluate(() => OfflineRuntime.store.listOperations()))[0]?.status,
      { timeout: 30000 }).toBe('conflict');
    const intent = (await page.evaluate(() => OfflineRuntime.store.listOperations()))[0];
    expect(intent).toMatchObject({ operationId: pending.operationId, baseVersion: 1, changes: { suite: 'Brouillon v1' } });
    expect((await (await request.get(`/api/observations/${f.id}`)).json()).suite).toBe('Serveur v2');
  });
});
