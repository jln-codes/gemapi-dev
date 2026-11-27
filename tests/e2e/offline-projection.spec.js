const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');

test('metadata : politique photo atomique, monotone, isolée et conservée hors du nettoyage du scope', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async policies => {
    const name = `photo-policy-${crypto.randomUUID()}`;
    let store = LocalProjection.createStore({ name });
    const first = await store.writePhotoQualityPolicy(policies.v4, 1000);
    const db = await store.open();
    const raw = await new Promise((resolve, reject) => {
      const request = db.transaction('metadata').objectStore('metadata').get('photoQualityPolicy');
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    let invalidRejected = false;
    try { await store.writePhotoQualityPolicy({ ...policies.v4, profile: 'inconnu' }, 1100); }
    catch (_) { invalidRejected = true; }
    const afterInvalid = await store.readPhotoQualityPolicy();
    const afterOlder = await store.writePhotoQualityPolicy(policies.v3, 1200);
    await store.close(); store = LocalProjection.createStore({ name });
    const reopened = await store.readPhotoQualityPolicy();
    await store.clear();
    const afterScopeClear = await store.readPhotoQualityPolicy();
    const isolated = await LocalProjection.createStore({ name: `${name}-other-base` }).readPhotoQualityPolicy();
    const opened = await store.open();
    await new Promise((resolve, reject) => {
      const tx = opened.transaction('metadata', 'readwrite');
      tx.objectStore('metadata').put({ ...raw, profile: 'corrompu', policyVersion: 99 });
      tx.oncomplete = resolve; tx.onabort = () => reject(tx.error);
    });
    let corruptIgnored = false;
    try { await store.readPhotoQualityPolicy(); } catch (_) { corruptIgnored = true; }
    const stillPresent = await new Promise((resolve, reject) => {
      const request = opened.transaction('metadata').objectStore('metadata').get('photoQualityPolicy');
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    const repaired = await store.writePhotoQualityPolicy(policies.v5, 1300);
    return { first, raw, invalidRejected, afterInvalid, afterOlder, reopened, afterScopeClear,
      isolated, corruptIgnored, stillPresent, repaired, schemaVersion: opened.version };
  }, { v3: photoQualityPolicy('standard', 3), v4: photoQualityPolicy('standard', 4),
    v5: photoQualityPolicy('economique', 5) });
  expect(result.schemaVersion).toBe(5);
  expect(result.first).toEqual({ policy: photoQualityPolicy('standard', 4), fetchedAt: 1000 });
  expect(result.raw).toEqual({ key: 'photoQualityPolicy', contractVersion: 2, profile: 'standard',
    policyVersion: 4, parametersVersion: photoQualityPolicy('standard', 4).parameters_version,
    effectiveParameters: photoQualityPolicy('standard', 4).effective_parameters, fetchedAt: 1000 });
  expect(result.invalidRejected).toBe(true);
  for (const cached of [result.afterInvalid, result.afterOlder, result.reopened, result.afterScopeClear]) {
    expect(cached.policy.policy_version).toBe(4);
  }
  expect(result.isolated).toBeNull();
  expect(result.corruptIgnored).toBe(true);
  expect(result.stillPresent.profile).toBe('corrompu');
  expect(result.repaired).toEqual({ policy: photoQualityPolicy('economique', 5), fetchedAt: 1300 });
});

test('messages offline cohérents avec la projection locale et sa reprise', async ({ page }) => {
  await page.goto('/api/config');
  await page.setContent(`
    <main>
      <p id="status"></p>
      <div class="header-notifications"></div>
      <p id="offline-status" role="status">Aucun périmètre préparé</p>
      <button id="clear-offline">Effacer le périmètre local</button>
    </main>
  `);
  await page.addScriptTag({ url: '/static/js/local-projection.js' });
  await page.waitForFunction(() => typeof window.LocalProjection === 'object');
  await page.evaluate(() => {
    window.__offlineHarness = {
      mode: 'online',
      snapshotError: 'ponctuel',
      snapshot: {
        schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' },
        reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] },
        observationReferences: { suitesApporter: [], urgences: [] },
        cursor: '1',
        tronconIds: ['T1'],
        records: [{
          resource: 'systemes', id: 'S1', serverVersion: 1,
          payload: { id: 'S1', version: 1, libelle: 'Système local' },
        }],
      },
    };
    window.__networkEvents = [];
    window.addEventListener('digues-app:network-state', event => window.__networkEvents.push(event.detail.offline));
    window.BusinessRuntime = {
      legacy: true,
      isActive: () => true,
      assertActive: () => {},
      can: () => true,
      coordinationKey: name => `offline-messages:${name}`,
      fetch: async (url) => {
        const path = new URL(url, location.href).pathname;
        const harness = window.__offlineHarness;
        if (harness.mode === 'network-error') throw new TypeError('Failed to fetch');
        if (path === '/api/resource-events/head') {
          return new Response(JSON.stringify({ cursor: '1' }), {
            status: 200, headers: { 'Content-Type': 'application/json' },
          });
        }
        if (path === '/api/offline-scope') {
          if (harness.mode === 'snapshot-500') {
            return new Response(JSON.stringify({ detail: harness.snapshotError }), {
              status: 500, headers: { 'Content-Type': 'application/json' },
            });
          }
          return new Response(JSON.stringify(harness.snapshot), {
            status: 200, headers: { 'Content-Type': 'application/json' },
          });
        }
        const status = harness.mode === 'http-500' ? 500 : 200;
        return new Response(JSON.stringify({ type: 'FeatureCollection', features: [] }), {
          status, headers: { 'Content-Type': 'application/json' },
        });
      },
    };
    window.ResourceEventTransport = { ensureReadBaseline: async () => {} };
    window.ResourceMutationController = { retryInvalidations: () => {} };
  });
  await page.addScriptTag({ url: '/static/js/offline-consultation.js' });
  await page.waitForFunction(() => typeof window.OfflineRuntime === 'object');
  await page.waitForFunction(() => window.OfflineRuntime.getNetworkState().status === 'online');
  await expect(page.locator('#offline-status')).toHaveText('Aucun périmètre préparé');

  const readAndReport = () => page.evaluate(async () => {
    try {
      await OfflineRuntime.fetch('/api/troncons');
      document.querySelector('#status').textContent = 'Lecture réussie';
    } catch (error) {
      const status = document.querySelector('#status');
      status.classList.add('error');
      status.textContent = error.message;
    }
  });

  await page.evaluate(() => { window.__offlineHarness.mode = 'network-error'; });
  await readAndReport();
  await expect(page.locator('#status')).toHaveText('Hors connexion : aucun périmètre préparé sur cet appareil.');
  await expect(page.locator('#offline-notice')).toHaveText('Hors connexion — aucun périmètre préparé ; aucune donnée métier locale disponible.');
  await expect(page.locator('#offline-notice')).toBeVisible();
  await expect(page.locator('.header-notifications > .offline-status-panel > #offline-notice')).toHaveCount(1);
  expect(await page.evaluate(() => window.__networkEvents)).toEqual([null, false, true]);
  await expect(page.locator('#offline-status')).toHaveText('Aucun périmètre préparé');

  await page.evaluate(() => { window.__offlineHarness.mode = 'http-500'; });
  expect(await page.evaluate(async () => (await OfflineRuntime.fetch('/api/troncons')).status)).toBe(500);
  await expect.poll(() => page.evaluate(() => OfflineRuntime.isOffline())).toBe(false);
  await expect(page.locator('#offline-notice')).toBeHidden();
  expect(await page.evaluate(() => window.__networkEvents)).toEqual([null, false, true, false]);

  await page.evaluate(async () => {
    await OfflineRuntime.store.replace(window.__offlineHarness.snapshot);
    window.__offlineHarness.mode = 'network-error';
  });
  const localStatus = await page.evaluate(async () => {
    const response = await OfflineRuntime.fetch('/api/troncons');
    return { status: response.status, local: response.headers.get('X-Digues-App-Local-Projection') };
  });
  expect(localStatus).toEqual({ status: 200, local: '1' });
  await expect(page.locator('#offline-notice')).toContainText('Hors connexion — périmètre préparé uniquement');
  await expect(page.locator('#offline-status')).toHaveText('Périmètre local disponible : 1 tronçon(s), 1 objet(s).');

  await page.evaluate(() => { window.__offlineHarness.mode = 'online'; });
  expect(await page.evaluate(async () => (await OfflineRuntime.fetch('/api/troncons')).status)).toBe(200);
  expect(await page.evaluate(() => OfflineRuntime.isOffline())).toBe(false);
  await expect(page.locator('#offline-notice')).toBeHidden();
  await expect(page.locator('#offline-status')).toHaveText('Serveur accessible — consultation connectée.');

  await page.evaluate(async () => {
    window.__offlineHarness.mode = 'snapshot-500';
    try { await OfflineRuntime.integrate({ cursor: '2' }); } catch (_) { /* expected snapshot failure */ }
  });
  await expect(page.locator('#offline-notice')).toHaveText('Projection à actualiser (http-error) : ponctuel');
  await expect(page.locator('#offline-notice')).toBeVisible();
  expect(await page.evaluate(() => OfflineRuntime.getProjectionError()?.code)).toBe('http-error');
  expect(await page.evaluate(() => OfflineRuntime.getNetworkState().status)).toBe('online');

  await page.getByRole('button', { name: 'Fermer l’information hors ligne' }).click();
  await expect(page.locator('.offline-status-panel')).toBeHidden();
  await page.evaluate(async () => {
    try { await OfflineRuntime.integrate({ cursor: '2' }); } catch (_) { /* same diagnostic */ }
  });
  await expect(page.locator('.offline-status-panel')).toBeHidden();
  await page.evaluate(async () => {
    window.__offlineHarness.snapshotError = 'nouveau diagnostic';
    try { await OfflineRuntime.integrate({ cursor: '2' }); } catch (_) { /* changed diagnostic */ }
  });
  await expect(page.locator('#offline-notice')).toHaveText('Projection à actualiser (http-error) : nouveau diagnostic');
  await expect(page.locator('.offline-status-panel')).toBeVisible();
  await page.evaluate(async () => {
    window.__offlineHarness.snapshotError = 'ponctuel';
    try { await OfflineRuntime.integrate({ cursor: '2' }); } catch (_) { /* original diagnostic */ }
  });

  await page.evaluate(async () => {
    window.__offlineHarness.mode = 'network-error';
    await OfflineRuntime.fetch('/api/troncons');
    window.__offlineHarness.mode = 'online';
    await OfflineRuntime.fetch('/api/troncons');
  });
  expect(await page.evaluate(() => OfflineRuntime.isOffline())).toBe(false);
  await expect(page.locator('#offline-notice')).toHaveText('Projection à actualiser (http-error) : ponctuel');
  await expect(page.locator('#offline-notice')).toBeVisible();

  await page.evaluate(async () => {
    window.__offlineHarness.snapshot.cursor = '2';
    await OfflineRuntime.integrate({ cursor: '2' });
  });
  expect(await page.evaluate(() => OfflineRuntime.getProjectionError())).toBeNull();
  await expect(page.locator('#offline-notice')).toBeHidden();

  await page.evaluate(async () => {
    window.__offlineHarness.mode = 'network-error';
    await OfflineRuntime.fetch('/api/troncons');
  });
  await page.getByRole('button', { name: 'Fermer l’information hors ligne' }).click();
  await expect(page.locator('.offline-status-panel')).toBeHidden();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#clear-offline').click();
  await expect.poll(() => page.evaluate(async () => (await OfflineRuntime.inspect()).scope)).toBeNull();
  expect(await page.evaluate(() => OfflineRuntime.isOffline())).toBe(true);
  expect(await page.evaluate(() => OfflineRuntime.getProjectionError())).toBeNull();
  await expect(page.locator('#offline-notice')).toHaveText('Hors connexion — aucun périmètre préparé ; aucune donnée métier locale disponible.');
  await expect(page.locator('#offline-notice')).toBeVisible();
  await expect(page.locator('#offline-status')).toHaveText('Périmètre local supprimé.');
  expect(await page.evaluate(() => window.__networkEvents)).toEqual([null, false, true, false, true, false, true, false, true]);
});

test('IndexedDB réel : schéma courant, versions, atomicité, concurrence et réouverture', async ({ page, context }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async () => {
    const name = `projection-test-${crypto.randomUUID()}`;
    const store = LocalProjection.createStore({ name });
    const row = version => ({ resource: 'systemes', id: 'S1', serverVersion: version,
      payload: { id: 'S1', version, libelle: `Version ${version}` } });
    const snapshot = (cursor, version) => ({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, cursor, tronconIds: ['T1'], records: [row(version)],
      reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] },
      observationReferences: { suitesApporter: [], urgences: [] } });
    const first = await store.replace(snapshot('10', 1));
    const initial = await store.inspect();
    const second = LocalProjection.createStore({ name });
    const races = await Promise.all([
      store.replace(snapshot('11', 2), initial.scope.revision),
      second.replace(snapshot('12', 3), initial.scope.revision),
    ]);
    const intermediate = await store.inspect();
    await store.replace(snapshot('13', 4), intermediate.scope.revision);
    const recent = await store.inspect();
    const stale = await store.replace(snapshot('9', 1), recent.scope.revision);
    let regressionRejected = false;
    try { await store.replace(snapshot('14', 1), recent.scope.revision); }
    catch (_) { regressionRejected = true; }
    const intact = await store.inspect();
    await store.markStale();
    const staleState = (await store.read('systemes', 'S1')).syncState;
    await store.close(); await second.close();
    const reopened = LocalProjection.createStore({ name });
    const persisted = await reopened.inspect();
    await reopened.replace({ ...snapshot('15', 4), records: [] }, persisted.scope.revision);
    const removed = await reopened.read('systemes', 'S1');
    const cursorAfterDelete = (await reopened.inspect()).scope.cursor;
    await reopened.clear();
    const cleared = await reopened.inspect();
    await reopened.close();
    // Refusal and unknown future schema are failures, never automatic deletion.
    let unavailable = false, futureRefused = false;
    try { await LocalProjection.createStore({ indexedDB: null }).open(); } catch (_) { unavailable = true; }
    const futureName = name + '-future';
    await new Promise((resolve, reject) => {
      const req = indexedDB.open(futureName, LocalProjection.SCHEMA_VERSION + 1);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => { req.result.close(); resolve(); };
    });
    try { await LocalProjection.createStore({ name: futureName }).open(); } catch (_) { futureRefused = true; }
    return { first, races, stale, regressionRejected, intact, staleState, persisted,
      removed, cursorAfterDelete, cleared, unavailable, futureRefused };
  });
  expect(result.first).toBe(true);
  expect(result.races.filter(Boolean)).toHaveLength(1);
  expect(result.stale).toBe(false);
  expect(result.regressionRejected).toBe(true);
  expect(result.intact.scope.cursor).toBe('13');
  expect(result.intact.records[0].serverVersion).toBe(4);
  expect(result.staleState).toBe('stale');
  expect(result.persisted.records[0].serverVersion).toBe(4);
  expect(result.removed).toBeNull();
  expect(result.cursorAfterDelete).toBe('15');
  expect(result.cleared).toEqual({ records: [], scope: null,
    reperageReferences: null, observationReferences: null });
  expect(result.unavailable).toBe(true);
  expect(result.futureRefused).toBe(true);
});

test('abort/quota : aucun curseur fantôme ; refus IndexedDB sans casser la consultation connectée', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
  try {
    const page = await context.newPage();
    await page.goto('/api/config');
    await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
    const result = await page.evaluate(async () => {
      const store = LocalProjection.createStore({ name: `abort-${crypto.randomUUID()}` });
      const snapshot = { schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '1', tronconIds: ['T1'], records: [
        { resource: 'systemes', id: 'S1', serverVersion: 1, payload: { id: 'S1', version: 1 } },
      ] };
      await store.replace(snapshot);
      const first = await store.inspect();
      const original = IDBObjectStore.prototype.put;
      IDBObjectStore.prototype.put = function (...args) {
        const result = original.apply(this, args);
        if (this.name === 'metadata') this.transaction.abort();
        return result;
      };
      let aborted = false;
      try { await store.replace({ ...snapshot, cursor: '2', records: [] }, first.scope.revision); }
      catch (_) { aborted = true; }
      finally { IDBObjectStore.prototype.put = original; }
      const after = await store.inspect();
      IDBObjectStore.prototype.put = function (...args) {
        if (this.name === 'metadata') throw new DOMException('Quota reached', 'QuotaExceededError');
        return original.apply(this, args);
      };
      let writeQuota = '';
      try { await store.replace({ ...snapshot, cursor: '3', records: [] }, first.scope.revision); }
      catch (error) { writeQuota = error.name; }
      finally { IDBObjectStore.prototype.put = original; }
      const afterQuota = await store.inspect();
      // Simulated browser quota refusal at open; errors must be propagated.
      let quota = '';
      try {
        await LocalProjection.createStore({ indexedDB: { open() { throw new DOMException('Quota', 'QuotaExceededError'); } } }).open();
      } catch (error) { quota = error.name; }
      return { aborted, after, quota, writeQuota, afterQuota };
    });
    expect(result.aborted).toBe(true);
    expect(result.after.scope.cursor).toBe('1');
    expect(result.after.records).toHaveLength(1);
    expect(result.quota).toBe('QuotaExceededError');
    expect(result.writeQuota).toBe('QuotaExceededError');
    expect(result.afterQuota.scope.cursor).toBe('1');
    expect(result.afterQuota.records).toHaveLength(1);
    await page.close();
    await context.route('**/api/**', async route => {
      if (await mockBusinessCRS(route)) return;
      if (await mockResourceJournal(route)) return;
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/config') return route.fulfill({ json: {
        legacy_single_database_enabled: true, show_uuid: false,
      } });
      if (path === '/api/systemes-endiguement') return route.fulfill({ json: { systemes: [] } });
      if (path === '/api/data/desordres/query') return route.fulfill({ json: {
        columns: [], rows: [], total: 0, offset: 0, has_next: false,
      } });
      if (path.startsWith('/api/referentiels/')) return route.fulfill({ json: { references: [] } });
      return route.fulfill({ json: {} });
    });
    await context.addInitScript(() => Object.defineProperty(window, 'indexedDB', { value: null }));
    const online = await context.newPage();
    await online.goto('/base');
    await expect(online.locator('#offline-notice')).toHaveText('Stockage local indisponible — utilisation connectée uniquement.');
    await expect(online.locator('#offline-status')).toHaveText('Stockage local indisponible — utilisation connectée uniquement.');
    await expect(online.locator('#offline-notice')).toBeVisible();
    await online.waitForFunction(() => window.ResourceEventTransport
      && ResourceEventTransport.getCursor() !== null);
    expect(await online.evaluate(async () => (await OfflineRuntime.fetch('/api/systemes-endiguement')).status)).toBe(200);
    expect(await online.evaluate(() => OfflineRuntime.isOffline())).toBe(false);
    expect(await online.evaluate(() => OfflineRuntime.getNetworkState().status)).toBe('online');
    await expect(online.locator('#network-status')).toHaveAttribute('aria-label', 'Serveur accessible');
    await expect(online.locator('#network-status [data-network-icon="network"]')).toBeVisible();
    await expect(online.locator('#offline-notice')).toHaveText('Stockage local indisponible — utilisation connectée uniquement.');
  } finally { await context.close(); }
});

test.describe('Périmètre terrain et journal réel', () => {
  test.skip(process.env.DIGUES_E2E_OCC !== '1', 'Requires disposable PostGIS');

  async function createScope(request, geometry = { type: 'Point', coordinates: [2, 46] }) {
    const system = await request.post('/api/systemes-endiguement', { data: { libelle: 'Terrain SE' } });
    expect(system.status()).toBe(201);
    const s = await system.json();
    const digue = await request.post('/api/digues', { data: { libelle: 'Terrain Digue', systeme_endiguement_id: s.id } });
    expect(digue.status()).toBe(201);
    const d = await digue.json();
    const trunk = await request.post('/api/troncons', { data: { libelle: 'Terrain Tronçon', digue_id: d.id,
      geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] } } });
    expect(trunk.status()).toBe(201);
    const t = (await trunk.json()).properties;
    const disorder = await request.post('/api/desordres', { data: { designation: 'Terrain initial', troncon_ids: [t.id],
      geometry } });
    expect(disorder.status()).toBe(201);
    return { system: s, digue: d, trunk: t, disorder: (await disorder.json()).properties };
  }

  test('préparation, deux clients, coupure, fermeture et réouverture PWA, reprise durable', async ({ browser, request, baseURL }) => {
    test.setTimeout(120_000);
    const fixture = await createScope(request);
    const context = await browser.newContext({ baseURL });
    const sender = await browser.newContext({ baseURL });
    try {
      await context.addInitScript(() => localStorage.setItem('digues-app:heritage-selection-sync-enabled', 'false'));
      const page = await context.newPage();
      await page.goto('/base');
      await page.waitForFunction(() => window.OfflineRuntime && window.ResourceEventTransport
        && ResourceEventTransport.getCursor() !== null);
      await page.evaluate(id => OfflineRuntime.prepare([id]), fixture.trunk.id);
      await page.evaluate(() => navigator.serviceWorker.ready);
      await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
      const manual = await context.newPage();
      await manual.goto('/carte');
      await manual.waitForFunction(id => typeof desordreLayersById !== 'undefined'
        && desordreLayersById.has(id), fixture.disorder.id);
      // Backend disappears while the map is already open; navigator.onLine
      // remains true. Consultation must not try to load an editing catalog.
      await manual.route('**/api/**', route => route.abort('failed'));
      await manual.evaluate(id => openPointEditor(id, desordreLayersById.get(id)), fixture.disorder.id);
      await expect(manual.locator('#offline-detail')).toContainText('Terrain initial');
      await manual.locator('#offline-detail button').last().click();
      await manual.unroute('**/api/**');
      const author = await sender.newPage();
      await author.goto('/base');
      await author.waitForFunction(() => window.ResourceMutationController);
      const save = await author.evaluate(async ({ id, version }) => {
        const response = await fetch(`/api/desordres/${id}`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'If-Match': `"${version}"`, ...await ResourceMutationController.requestHeaders() },
          body: JSON.stringify({ designation: 'Terrain version distante' }) });
        const value = await response.json();
        await ResourceMutationController.publishMutation(value.mutation);
        return value;
      }, fixture.disorder);
      await expect.poll(() => page.evaluate(async id => (await OfflineRuntime.store.read('desordres', id))?.serverVersion,
        fixture.disorder.id)).toBe(save.properties.version);
      const cursor = await page.evaluate(async () => (await OfflineRuntime.inspect()).scope.cursor);
      expect(BigInt(cursor)).toBeGreaterThan(0n);
      await page.close(); await manual.close();
      await context.setOffline(true);
      const reopened = await context.newPage();
      await reopened.goto('/base');
      await reopened.locator('#toggle-views').click();
      await expect(reopened.locator('#data-view-result-body')).toContainText('Terrain version distante');
      await expect(reopened.locator('#offline-notice')).toContainText('Hors connexion');
      await reopened.evaluate(id => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
        detail: { entityClass: 'desordres', entityId: id, entities: [] },
      })), fixture.disorder.id);
      await expect(reopened.locator('#data-entity-detail')).toContainText('Terrain version distante');
      const absent = await reopened.evaluate(async () => {
        try { await OfflineRuntime.fetch('/api/desordres/00000000-0000-0000-0000-000000000000'); return ''; }
        catch (error) { return error.message; }
      });
      expect(absent).toContain('non préparée');
      const map = await context.newPage();
      await map.goto('/carte');
      await map.waitForFunction(id => typeof desordreLayersById !== 'undefined'
        && desordreLayersById.has(id), fixture.disorder.id);
      expect(await map.evaluate(() => tronconLayersById.size)).toBe(1);
      await map.evaluate(id => openPointEditor(id, desordreLayersById.get(id)), fixture.disorder.id);
      await expect(map.locator('#offline-detail')).toContainText('Terrain version distante');
      const update = await request.put(`/api/desordres/${fixture.disorder.id}`, { headers: { 'If-Match': `"${save.properties.version}"` },
        data: { designation: 'Terrain après reconnexion' } });
      expect(update.status()).toBe(200);
      const final = await update.json();
      await context.setOffline(false);
      await expect.poll(() => reopened.evaluate(async id => (await OfflineRuntime.store.read('desordres', id))?.serverVersion,
        fixture.disorder.id), { timeout: 20000 }).toBe(final.properties.version);
      // The active detail refreshes immediately; Vues defers its query while
      // hidden and consumes the invalidation on the explicit return.
      await expect(reopened.locator('#data-entity-detail-read')).toContainText('Terrain après reconnexion');
      await reopened.locator('#data-entity-detail-back').click();
      await expect(reopened.locator('#data-view-result-body')).toContainText('Terrain après reconnexion');
      const after = await reopened.evaluate(async () => (await OfflineRuntime.inspect()).scope.cursor);
      expect(BigInt(after)).toBeGreaterThan(BigInt(cursor));
    } finally { await context.close(); await sender.close(); }
  });

  test('HTTP métier ne devient pas offline ; BC + SSE n’intègre le périmètre qu’une fois', async ({ browser, request, baseURL }) => {
    test.setTimeout(60000);
    const fixture = await createScope(request);
    const context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
    try {
      const a = await context.newPage(), b = await context.newPage();
      await Promise.all([a.goto('/carte'), b.goto('/base')]);
      await Promise.all([a, b].map(page => page.waitForFunction(() => window.OfflineRuntime
        && window.ResourceMutationController && window.ResourceEventTransport
        && ResourceEventTransport.getCursor() !== null)));
      await b.evaluate(id => OfflineRuntime.prepare([id]), fixture.trunk.id);
      let snapshots = 0;
      context.on('request', req => { if (req.url().includes('/api/offline-scope')) snapshots++; });
      const response = await request.put(`/api/desordres/${fixture.disorder.id}`, {
        headers: { 'If-Match': `"${fixture.disorder.version}"` }, data: { designation: 'Double transport' } });
      const payload = await response.json();
      await a.evaluate(event => ResourceMutationController.publishMutation(event), payload.mutation);
      await expect.poll(() => b.evaluate(async id => (await OfflineRuntime.store.read('desordres', id))?.serverVersion,
        fixture.disorder.id)).toBe(payload.properties.version);
      await expect.poll(() => b.evaluate(() => ResourceEventTransport.getCursor())).toBe(await b.evaluate(async () => (await OfflineRuntime.inspect()).scope.cursor));
      expect(snapshots).toBe(1);
      for (const code of [404, 409, 422, 500]) {
        await b.route(`**/api/desordres/${fixture.disorder.id}`, route => route.fulfill({ status: code, json: { detail: 'HTTP error' } }));
        const status = await b.evaluate(async id => (await OfflineRuntime.fetch(`/api/desordres/${id}`)).status, fixture.disorder.id);
        expect(status).toBe(code);
        expect(await b.evaluate(() => OfflineRuntime.isOffline())).toBe(false);
        await b.unroute(`**/api/desordres/${fixture.disorder.id}`);
      }
    } finally { await context.close(); }
  });

  test('brouillon connecté conservé après sauvegarde sans réseau ; Vue offline explicitement non supportée', async ({ browser, request, baseURL }) => {
    const fixture = await createScope(request);
    const context = await browser.newContext({ baseURL });
    try {
      const page = await context.newPage();
      await page.goto('/base');
      await page.waitForFunction(() => window.OfflineOperationsRuntime && document.querySelector('main')?.dataset.layout !== undefined);
      await page.evaluate(id => OfflineRuntime.prepare([id]), fixture.trunk.id);
      await page.evaluate(id => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
        detail: { entityClass: 'desordres', entityId: id, entities: [] },
      })), fixture.disorder.id);
      await page.locator('#data-entity-detail-edit').click();
      await page.locator('[name="designation"]').fill('Brouillon sans réseau');
      await context.setOffline(true);
      await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
      await expect(page.locator('[name="designation"]')).toHaveValue('Brouillon sans réseau');
      await expect(page.locator('#data-entity-detail')).toContainText('Enregistré localement');
      expect(await page.evaluate(async () => (await OfflineRuntime.store.listOperations()).length)).toBe(1);
      const view = await page.evaluate(async () => {
        try { await OfflineRuntime.fetch('/api/data/view-query', { method: 'POST', body: '{}' }); }
        catch (error) { return error.message; }
      });
      expect(view).toContain('vues personnalisées nécessitent le serveur');
      const excluded = await page.evaluate(async () => {
        const errors = [];
        for (const [url, method] of [['/api/sql/query', 'POST'], ['/api/hydrometrie/sites', 'GET'], ['/api/territoire-administratif', 'GET']]) {
          try { await OfflineRuntime.fetch(url, { method }); }
          catch (error) { errors.push(error.name); }
        }
        return errors;
      });
      expect(excluded).toEqual(['TypeError', 'TypeError', 'TypeError']);
      const row = await page.evaluate(async id => OfflineRuntime.store.read('desordres', id), fixture.disorder.id);
      expect(row.payload.properties.designation).toBe('Terrain initial');
    } finally { await context.close(); }
  });

  // STAT-2, niveau B : vrai PostgreSQL, périmètre IndexedDB propre au contexte,
  // shell précaché puis transport coupé. Le preset doit fonctionner à froid ;
  // retirer un champ doit échouer explicitement, sans résultats de substitution.
  test('Preset Vues local : ouverture à froid, rattachements multiples et filtre Patrimoine serveur', async ({ browser, request, baseURL }) => {
    const fixture = await createScope(request, { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] });
    const trunk = await request.post('/api/troncons', { data: { libelle: 'Tronçon complémentaire', digue_id: fixture.digue.id,
      geometry: { type: 'LineString', coordinates: [[2, 46], [2.02, 46.02]] } } });
    const t2 = (await trunk.json()).properties.id;
    const update = await request.put(`/api/desordres/${fixture.disorder.id}`, {
      headers: { 'If-Match': `"${fixture.disorder.version}"` }, data: { troncon_ids: [fixture.trunk.id, t2] } });
    expect(update.status()).toBe(200);
    const context = await browser.newContext({ baseURL });
    try {
      const page = await context.newPage(); await page.goto('/base');
      await page.waitForFunction(() => window.OfflineRuntime);
      await page.evaluate(id => OfflineRuntime.prepare([id]), fixture.trunk.id);
      const prepared = await page.evaluate(() => OfflineRuntime.inspect());
      expect(prepared.records.filter(r => r.resource === 'troncons')).toHaveLength(2);
      expect(prepared.scope.tronconIds).toHaveLength(1);
      await page.evaluate(() => navigator.serviceWorker.ready);
      await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
      await context.setOffline(true);
      await page.reload();
      await expect(page.locator('#data-statistics-view')).toBeVisible();
      await page.locator('#toggle-views').click();
      await expect(page.locator('#data-view-result-body')).toContainText('Terrain initial');
      await expect(page.locator('#data-view-field-list .data-view-field-chip')).toHaveCount(4);
      await expect(page.locator('#data-view-export-csv')).toBeVisible();
      await expect(page.locator('#data-view-export-gpkg')).toBeVisible();
      const query = async hidden => page.evaluate(async hidden => (await OfflineRuntime.fetch('/api/data/view-query', {
        method: 'POST', body: JSON.stringify({...DataViewDefault.definition(),hidden_troncon_ids:hidden,offset:0,page_size:500}),
      })).json(), hidden);
      const visible = await query([]);
      expect(visible.rows).toHaveLength(1);
      expect(visible.rows[0].values[2]).toContain('Tronçon complémentaire');
      const filtered = await query([t2]);
      expect(filtered.rows).toHaveLength(0); expect(filtered.total).toBe(0); expect(filtered.has_next).toBe(false);
      const definition = await page.evaluate(() => DataViewDefault.definition());
      const authoritative = await request.post('/api/data/view-query',{data:{...definition,offset:0,page_size:500}});
      const online = await authoritative.json();
      expect(visible.columns).toEqual(online.columns);
      expect(visible.rows[0].values).toEqual(online.rows.find(row=>row.entities.some(e=>e.id===fixture.disorder.id)).values);
      const server = await request.post('/api/data/view-query', { data: { ...definition,hidden_troncon_ids:[t2],offset:0,page_size:500 } });
      expect((await server.json()).rows.some(row => row.entities.some(e => e.id === fixture.disorder.id))).toBe(false);
      await page.locator('#data-view-result-body [data-entity-class="desordres"]').click();
      await expect(page.locator('main')).toHaveAttribute('data-central-view','detail');
      await expect(page.locator('#data-entity-detail-title')).toContainText('Terrain initial');
      // Existing floating notices cover the top-left Return action. Dismiss
      // them through their public controls, without forcing a click or state.
      await expect(page.locator('#status')).toHaveText('Backend indisponible');
      await page.getByRole('button',{name:'Fermer le message',exact:true}).click();
      await expect(page.locator('#offline-notice')).toContainText('périmètre préparé');
      await page.getByRole('button',{name:'Fermer l’information hors ligne',exact:true}).click();
      expect(await page.evaluate(()=>OfflineRuntime.isOffline())).toBe(true);
      await page.locator('#data-entity-detail-back').click();
      await expect(page.locator('main')).toHaveAttribute('data-central-view','views');
      await expect(page.locator('#data-view-field-list .data-view-field-chip')).toHaveCount(4);
      await page.locator('#data-view-field-list button').first().click();
      await expect(page.locator('#data-view-result-empty')).toContainText('vues personnalisées nécessitent le serveur');
      await expect(page.locator('#data-view-result-table')).toBeHidden();
      await page.locator('#show-statistics').click(); await page.locator('#toggle-views').click();
      await expect(page.locator('#data-view-field-list .data-view-field-chip')).toHaveCount(3);
      await expect(page.locator('#data-view-result-empty')).toContainText('vues personnalisées nécessitent le serveur');
    } finally { await context.close(); }
  });
});
