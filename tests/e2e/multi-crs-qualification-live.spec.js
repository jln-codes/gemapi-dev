const { test, expect } = require('@playwright/test');
const fs = require('node:fs');

test.skip(!process.env.DIGUES_F3_BROWSER_FIXTURE, 'Cluster PostgreSQL F3 jetable requis');
test.use({ serviceWorkers: 'block' });
test.setTimeout(150000);

const fixture = process.env.DIGUES_F3_BROWSER_FIXTURE
  ? JSON.parse(fs.readFileSync(process.env.DIGUES_F3_BROWSER_FIXTURE, 'utf8')) : null;
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC', 'base64');

test('une base administrativement provisionnée en 2154 reste isolée et rejoue une Photo locale', async ({ page, context }) => {
  await page.goto('/base', { waitUntil: 'commit' });
  await page.getByLabel('Identifiant', { exact: true }).fill(fixture.login);
  await page.getByLabel('Mot de passe', { exact: true }).fill(fixture.password);
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Choisir une base métier' })).toBeVisible();

  for (const [base, label] of [[fixture.base3950, 'RGF93 v1 / CC50 — EPSG:3950'],
                               [fixture.base2154, 'RGF93 v1 / Lambert-93 — EPSG:2154'],
                               [fixture.base3950, 'RGF93 v1 / CC50 — EPSG:3950']]) {
    await page.goto(`/base?base=${base}`);
    await expect(page.locator('#base-summary-crs')).toHaveText(label, { timeout: 30000 });
  }
  await page.goto(`/carte?base=${fixture.base2154}`);
  await expect(page.locator('#open-business-session')).not.toHaveAttribute('aria-disabled', 'true');
  await page.waitForFunction(() => window.OfflineRuntime?.store && window.OfflineOperationsRuntime);
  await page.evaluate(id => OfflineRuntime.prepare([id]), fixture.troncon);
  const prepared = await page.evaluate(async () => OfflineRuntime.store.inspect());
  expect(prepared.scope.crs.srid).toBe(2154);
  expect(prepared.reperageReferences.crs.srid).toBe(2154);

  await context.setOffline(true);
  await expect(page.locator('#network-status')).toHaveAttribute('data-network-status', 'offline');
  const queued = await page.evaluate(async ({ fixture, bytes }) => {
    const observation = { id: fixture.observation, designation: 'Observation F3', version: 1,
      parent: { resource: 'troncons', id: fixture.troncon, designation: 'Tronçon F3' } };
    const file = new File([new Uint8Array(bytes)], 'f3-photo.png', { type: 'image/png' });
    const media = await PhotoFile.prepare(file, PhotoQualityPolicy);
    const create = await OfflineRuntime.store.enqueuePhotoCreate({
      operationId: fixture.createOperationId,
      changes: { id: fixture.photoId, observation_id: fixture.observation,
        date: '2026-10-09', libelle: 'Photo terrain F3' },
      observation, media,
    });
    const location = await OfflineRuntime.store.enqueuePhotoLocalisation({
      operationId: fixture.locationOperationId, photoId: fixture.photoId,
      changes: { mode: 'point', srid: 2154, coordinates: fixture.projected },
    });
    const stored = await OfflineRuntime.store.readPhotoMedia(create.mediaLocalRef);
    return { createId: create.operationId, locationId: location.operationId,
      mediaRef: create.mediaLocalRef,
      mediaSize: stored?.blob?.size, operations: await OfflineRuntime.store.listOperations() };
  }, { fixture, bytes: [...png] });
  expect(queued.createId).toBe(fixture.createOperationId);
  expect(queued.locationId).toBe(fixture.locationOperationId);
  expect(queued.mediaSize).toBeGreaterThan(0);
  expect(queued.operations).toHaveLength(2);

  await context.setOffline(false);
  await expect(page.locator('#network-status')).toHaveAttribute('data-network-status', 'online', { timeout: 30000 });
  expect(await page.evaluate(async id =>
    (await BusinessRuntime.fetch(`/api/observations/${id}`)).status, fixture.observation)).toBe(200);
  await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
  await expect.poll(() => page.evaluate(async () =>
    (await OfflineRuntime.store.listOperations()).length), { timeout: 45000 }).toBe(0);
  expect(await page.evaluate(async ref =>
    (await OfflineRuntime.store.readPhotoMedia(ref))?.blob?.size, queued.mediaRef)).toBe(queued.mediaSize);
  const photo = await page.evaluate(async id =>
    (await (await BusinessRuntime.fetch(`/api/photos/${id}`)).json()), fixture.photoId);
  expect(photo.version).toBe(2);
  expect(photo.localisation.srid).toBe(2154);
  expect(photo.localisation.geometry_4326.coordinates[0]).toBeCloseTo(fixture.longitude, 4);
});
