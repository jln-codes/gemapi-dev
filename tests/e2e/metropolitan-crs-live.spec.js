// FR-1 B: real memberships, HTTP, SQL and IndexedDB. Synthetic owned cluster.
const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const { randomUUID } = require('node:crypto');
test.skip(!process.env.DIGUES_CRS_BROWSER_FIXTURE, 'Cluster FR-1 explicite requis');
test.setTimeout(180000);
test.use({ serviceWorkers: 'block' });
const fixture = process.env.DIGUES_CRS_BROWSER_FIXTURE
  ? JSON.parse(fs.readFileSync(process.env.DIGUES_CRS_BROWSER_FIXTURE, 'utf8')) : null;
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC', 'base64');

test.describe.serial('metropolitan CRS: shared XY drawing and durable offline Photo replay', () => {
  let page, context;
  const errors = [];
  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext({ serviceWorkers: 'block' });
    page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('/base');
    await page.getByLabel('Identifiant', { exact: true }).fill(fixture.login);
    await page.getByLabel('Mot de passe', { exact: true }).fill(fixture.password);
    await page.getByRole('button', { name: 'Se connecter', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Choisir une base métier' })).toBeVisible();
  });
  test.afterAll(async () => { await context?.close(); });
  for (const base of fixture?.bases || [{}]) test(`EPSG:${base.srid}: XY, active-base selectors and offline replay`, async () => {
    await page.goto(`/carte?base=${base.base}`);
    await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
    await expect(page.locator('#open-business-session')).not.toHaveAttribute('aria-disabled', 'true');
    if (await page.locator('#heritage-panel').isVisible()) await page.locator('#close-heritage').click();
    // Exercises the shared EntityGeometryDraft used by dependent forms.
    await page.locator('#toggle-create-menu').click();
    await page.locator('[data-create-type=mobilier]').click();
    const view = page.locator('#mobilier-editor-panel');
    await expect(view).toBeVisible();
    await view.locator('[name=designation]').fill(`Mobilier FR-1 ${base.srid}`);
    for (const select of await view.locator('select[required]:visible').all()) {
      if (!await select.inputValue()) await select.selectOption({ index: 1 });
    }
    await view.getByRole('group', { name: 'Mode de localisation' }).getByText('X/Y', { exact: true }).click();
    await expect(view.locator("[name=coordinate_start_1]")).toHaveAccessibleName(`X (EPSG:${base.srid})`);
    await view.locator("[name=coordinate_start_1]").fill(String(base.xy[0]));
    await expect(view.locator("[name=coordinate_start_2]")).toHaveAccessibleName(`Y (EPSG:${base.srid})`);
    await view.locator("[name=coordinate_start_2]").fill(String(base.xy[1]));
    await view.getByRole('button', { name: 'Confirmer les coordonnées', exact: true }).click();
    const saved = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith('/mobilier'));
    await view.locator(':scope > .entity-form__body > form [data-role=save]').click();
    const response = await saved;
    expect(response.status(), await response.text()).toBe(201);
    const created = await response.json();
    expect(created.geometry.coordinates[0]).toBeCloseTo(base.lonlat[0], 8);
    expect(created.geometry.coordinates[1]).toBeCloseTo(base.lonlat[1], 8);
    // Exact current-base CRS comes from the server, never from a historical axis.
    const selectors = await page.evaluate(async base => {
      const state = await (await BusinessRuntime.fetch(`/api/troncons/${base.troncon}/reperage`)).json();
      const container = document.createElement('section'); document.body.append(container);
      const widget = TronconReperage.create({ container,
        fetchJson: async () => state, id: base.troncon, canWrite: () => true });
      await widget.load();
      const values = [...container.querySelectorAll('select[name=srid]')].map(s => [...s.options].map(o => o.value));
      widget.dispose(); container.remove(); return values;
    }, base);
    expect(selectors.length).toBeGreaterThan(0);
    for (const options of selectors) expect(options).toEqual(['4326', String(base.srid)]);
    await page.evaluate(id => OfflineRuntime.prepare([id]), base.troncon);
    const scope = await page.evaluate(async () => (await OfflineRuntime.store.inspect()).scope);
    expect(scope.crs.srid).toBe(base.srid);
    const namespace = await page.evaluate(() => BusinessRuntime.databaseName);
    expect(namespace).toContain(base.base); expect(namespace).toContain(base.epoch);
    const photoId = randomUUID(), createId = randomUUID(), locationId = randomUUID();
    await context.setOffline(true);
    await expect(page.locator("#network-status")).toHaveAttribute("data-network-status", "offline");
    const queued = await page.evaluate(async ({ base, bytes, photoId, createId, locationId }) => {
      const store = OfflineRuntime.store;
      const file = new File([new Uint8Array(bytes)], 'fr1.png', { type: 'image/png' });
      const media = await PhotoFile.prepare(file, PhotoQualityPolicy);
      const operation = await store.enqueuePhotoCreate({ operationId: createId,
        changes: { id: photoId, observation_id: base.observation, libelle: `FR-1 ${base.srid}` },
        observation: { id: base.observation, version: 1, parent: { resource: 'troncons', id: base.troncon } }, media });
      const before = await store.listOperations();
      let rejected = false;
      try { await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(), photoId,
        changes: { mode: 'point', srid: base.srid === 3950 ? 2154 : 3950, coordinates: base.xy } }); }
      catch (_) { rejected = true; }
      const after = await store.listOperations();
      await store.enqueuePhotoLocalisation({ operationId: locationId, photoId,
        changes: { mode: 'point', srid: base.srid, coordinates: base.xy } });
      const current = await store.inspect();
      const foreign = { authority: 'EPSG', srid: base.srid === 3950 ? 2154 : 3950,
        code: `EPSG:${base.srid === 3950 ? 2154 : 3950}` };
      const snapshot = { schemaVersion: 3, crs: current.scope.crs,
        cursor: current.scope.cursor, tronconIds: current.scope.tronconIds,
        records: current.records, reperageReferences: current.reperageReferences,
        observationReferences: current.observationReferences };
      const pending = await store.listOperations();
      let refused = 0;
      for (const candidate of [
        { ...snapshot, reperageReferences: { ...snapshot.reperageReferences, crs: foreign } },
        { ...snapshot, crs: foreign, reperageReferences: { ...snapshot.reperageReferences, crs: foreign } },
      ]) {
        try { await store.replace(candidate, current.scope.revision); }
        catch (_) { refused++; }
      }
      const preserved = JSON.stringify(pending) === JSON.stringify(await store.listOperations());
      return { rejected, before, after, refused, preserved, operationCount: pending.length, mediaRef: operation.mediaLocalRef,
        size: (await store.readPhotoMedia(operation.mediaLocalRef)).blob.size };
    }, { base, bytes: [...png], photoId, createId, locationId });
    expect(queued.rejected).toBe(true);
    expect(queued.refused).toBe(2); expect(queued.preserved).toBe(true); expect(queued.operationCount).toBe(2);
    expect(queued.after).toEqual(queued.before);
    expect(queued.size).toBeGreaterThan(0);
    await context.setOffline(false);
    await expect(page.locator("#network-status")).toHaveAttribute("data-network-status", "online", { timeout: 30000 });
    await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
    await expect.poll(() => page.evaluate(async () => (await OfflineRuntime.store.listOperations()).length), { timeout: 45000 }).toBe(0);
    const photo = await page.evaluate(async id => (await (await BusinessRuntime.fetch(`/api/photos/${id}`)).json()), photoId);
    expect(photo.version).toBe(2); expect(photo.localisation.srid).toBe(base.srid);
    expect(photo.localisation.geometry_4326.coordinates[0]).toBeCloseTo(base.lonlat[0], 8);
    expect(await page.evaluate(async ref => (await OfflineRuntime.store.readPhotoMedia(ref)).blob.size, queued.mediaRef)).toBe(queued.size);
    // A retry uses the original operation identity and returns the same receipt.
    const retry = await page.evaluate(async ({ photoId, locationId, base }) => {
      const r = await BusinessRuntime.fetch(`/api/photos/${photoId}/localisation`, { method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'If-Match': '"1"', 'Operation-Id': locationId },
        body: JSON.stringify({ mode: 'point', srid: base.srid, coordinates: base.xy }) });
      return { status: r.status, body: await r.json() };
    }, { photoId, locationId, base });
    expect(retry.status).toBe(200); expect(retry.body.version).toBe(2);
    console.log(`FR-1 Chromium EPSG:${base.srid}: XY, selectors, isolated IndexedDB, Photo original/replay/idempotence OK`);
    expect(errors).toEqual([]);
  });
});
