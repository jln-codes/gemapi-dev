const { test, expect } = require('@playwright/test');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC', 'base64');

async function setup(page) {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-file.js' });
  await page.evaluate(() => { window.module = { exports: {} }; });
  await page.addScriptTag({ path: 'webapp/frontend/js/offline-operations.js' });
  await page.evaluate(async ({ bytes, policy }) => {
    window.OfflineOperations = module.exports; delete window.module;
    window.dbName = crypto.randomUUID();
    window.store = LocalProjection.createStore({ name: dbName });
    window.trunk = crypto.randomUUID(); window.system = crypto.randomUUID(); window.borne = crypto.randomUUID();
    window.observation = { id: crypto.randomUUID(), designation: 'Observation locale', version: 1,
      parent: { resource: 'troncons', id: trunk, designation: 'Tronçon préparé' } };
    window.revision = 'a'.repeat(64);
    window.scope = revision => ({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, cursor: '0', tronconIds: [trunk], records: [
      { resource: 'troncons', id: trunk, serverVersion: 1, payload: { type: 'Feature', id: trunk,
        properties: { id: trunk, version: 1, libelle: 'Tronçon préparé' },
        geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] } } },
      { resource: 'observations', id: observation.id, serverVersion: 1,
        payload: { ...observation, id: observation.id, version: 1 } } ],
      reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [{ id: system, troncon_id: trunk,
        designation: 'Calibration', valid: true, revision, bornes: [
          { link_id: crypto.randomUUID(), id: borne, pr: '125', link_valid: true,
            designation: 'B-1', valid: true, geometry: { type: 'Point', coordinates: [1700000, 8200000] } } ] }] },
      observationReferences: { suitesApporter: [], urgences: [] } });
    await store.replace(scope(revision));
    window.media = await PhotoFile.prepare(new File([new Uint8Array(bytes)], 'photo.png', { type: 'image/png' }), policy);
    window.photoOp = await store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: crypto.randomUUID(), observation_id: observation.id, date: '2026-09-23', libelle: 'Terrain' },
      observation, media });
    window.server = { uploads: new Map(), receipts: new Map(), photos: new Map(), putCalls: [],
      events: [], failCreateReply: false, failLocationReply: false, createEvents: 0, updateEvents: 0 };
    window.makeEngine = () => OfflineOperations.createEngine({ store, catchUp: async () => '0',
      headers: async () => ({}), publish: async event => { server.events.push(event.eventId); }, locks: navigator.locks,
      fetchImpl: async (url, options) => {
        const reply = (status, json) => ({ ok: status < 300, status, json: async () => json });
        if (url.startsWith('/api/photo-uploads/')) {
          const id = url.split('/')[3];
          if (options.method !== 'PUT') return server.uploads.has(id) ? reply(200, server.uploads.get(id)) : reply(404, { detail: { code: 'upload-not-found' } });
          const descriptor = { id, observation_id: observation.id, upload_ref: crypto.randomUUID(),
            file_sha256: url.split('/')[4].split('?')[0], file_size: options.body.size,
            mime_type: options.body.type, expires_at: Date.now()/1000 + 86400 };
          server.uploads.set(id, descriptor); return reply(200, descriptor);
        }
        const op = options.headers['Operation-Id'];
        if (url.endsWith('/localisation')) {
          server.putCalls.push({ op, url, body: JSON.parse(options.body), headers: options.headers });
          if (server.receipts.has(op)) return reply(200, server.receipts.get(op));
          const id = url.split('/')[3], prior = server.photos.get(id);
          if (!prior) return reply(404, { detail: 'Photo absente' });
          if (options.headers['If-Match'] !== `"${prior.version}"`) return reply(409, { detail: { code: 'version-conflict' } });
          if (options.headers['Reference-Revision'] && options.headers['Reference-Revision'] !== revision)
            return reply(409, { detail: { code: 'photo-references-changed' } });
          const response = { ...prior, version: prior.version + 1, localisation: JSON.parse(options.body),
            mutation: { eventId: crypto.randomUUID(), resource: 'photos', action: 'update',
              ids: [id], versions: { [id]: prior.version + 1 } } };
          server.photos.set(id, response); server.receipts.set(op, response); server.updateEvents++;
          if (server.failLocationReply) { server.failLocationReply = false; throw new TypeError('réponse PUT perdue'); }
          return reply(200, response);
        }
        const body = JSON.parse(options.body);
        if (server.receipts.has(op)) return reply(201, server.receipts.get(op));
        const response = { ...body, version: 1, content_available: true,
          content_url: `/api/photos/${body.id}/content`, mutation: { eventId: crypto.randomUUID(),
            resource: 'photos', action: 'create', ids: [body.id], versions: { [body.id]: 1 } } };
        server.photos.set(body.id, response); server.receipts.set(op, response); server.createEvents++;
        if (server.failCreateReply) { server.failCreateReply = false; throw new TypeError('réponse POST perdue'); }
        return reply(201, response);
      } });
    window.engine = makeEngine();
  }, { bytes: [...png], policy: photoQualityPolicy() });
}

test('localisation Photo : create dépendant, redémarrage, réponse perdue et reçu unique', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const command = { mode: 'bornage', troncon_id: trunk, systeme_reperage_id: system,
      borne_id: borne, position_relative: 'AVANT_BORNE', distance_m: 12 };
    const first = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(), photoId: photoOp.resourceId,
      changes: command, referenceRevision: revision });
    const edited = await store.editPhotoLocalisation(first.operationId, { ...command, distance_m: 13 }, revision);
    let refused = false;
    try { await store.removeOperation(photoOp.operationId, true); } catch (_) { refused = true; }
    await store.close(); store = LocalProjection.createStore({ name: dbName }); engine = makeEngine();
    const durable = (await store.listOperations()).find(row => row.action === 'update-localisation');
    server.failCreateReply = true; await engine.synchronize(true);
    const blocked = await store.photoLocalisationEligibility(edited.operationId);
    server.failLocationReply = true; await engine.synchronize(true);
    const unknown = (await store.listOperations()).find(row => row.action === 'update-localisation');
    let editAfterAttemptRefused = false, abandonAfterAttemptRefused = false;
    try { await store.editPhotoLocalisation(edited.operationId, { ...command, distance_m: 99 }, revision); }
    catch (_) { editAfterAttemptRefused = true; }
    try { await store.removeOperation(edited.operationId, true); }
    catch (_) { abandonAfterAttemptRefused = true; }
    await store.close(); store = LocalProjection.createStore({ name: dbName }); engine = makeEngine();
    await engine.synchronize(true);
    const confirmed = (await store.confirmedPhotos(observation.id))[0].photo;
    return { first, edited, durable, refused, blocked, unknown, editAfterAttemptRefused,
      abandonAfterAttemptRefused, remaining: await store.listOperations(),
      confirmed, puts: server.putCalls, createEvents: server.createEvents, updateEvents: server.updateEvents,
      events: server.events };
  });
  expect(result.edited.operationId).not.toBe(result.first.operationId);
  expect(result.durable.operationId).toBe(result.edited.operationId);
  expect(result.refused).toBe(true);
  expect(result.blocked.eligible).toBe(false);
  expect(result.unknown.attempts).toBe(1);
  expect(result.editAfterAttemptRefused).toBe(true);
  expect(result.abandonAfterAttemptRefused).toBe(true);
  expect(result.remaining).toEqual([]);
  expect(result.confirmed.version).toBe(2);
  expect(result.confirmed.localisation.position_relative).toBe('AVANT_BORNE');
  expect(result.createEvents).toBe(1); expect(result.updateEvents).toBe(1);
  expect(result.puts).toHaveLength(2);
  expect(result.puts[0].op).toBe(result.puts[1].op);
  expect(result.puts[0].body).toEqual(result.puts[1].body);
  expect(result.puts[0].headers['If-Match']).toBe('"1"');
  expect(result.puts[0].headers['Reference-Revision']).toBe('a'.repeat(64));
});

test('références épinglées, calibration modifiée et abandon local sans perte du média', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    await engine.synchronize(true);
    const intent = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(), photoId: photoOp.resourceId,
      changes: { mode: 'pr', troncon_id: trunk, systeme_reperage_id: system, pr: 125 }, referenceRevision: revision });
    const before = await store.inspect();
    await store.replace(scope('b'.repeat(64)), before.scope.revision);
    const after = await store.inspect();
    const eligibility = await store.photoLocalisationEligibility(intent.operationId);
    await engine.synchronize(true);
    const conflict = (await store.listOperations()).find(row => row.operationId === intent.operationId);
    const mediaBefore = await store.readPhotoMedia(photoOp.mediaLocalRef);
    await store.removeOperation(intent.operationId, true);
    const mediaAfter = await store.readPhotoMedia(photoOp.mediaLocalRef);
    return { pinned: after.reperageReferences.pinned.map(row => row.revision), eligibility, conflict,
      current: after.reperageReferences.systems[0].revision, mediaBefore: !!mediaBefore,
      mediaAfter: !!mediaAfter, remaining: await store.listOperations(), puts: server.putCalls.length };
  });
  expect(result.pinned).toContain('a'.repeat(64)); expect(result.current).toBe('b'.repeat(64));
  expect(result.mediaBefore).toBe(true); expect(result.mediaAfter).toBe(true);
  expect(result.eligibility.reason).toBe('references-stale');
  expect(result.conflict.status).toBe('conflict'); expect(result.conflict.attempts).toBe(0);
  expect(result.remaining).toEqual([]); expect(result.puts).toBe(0);
});

test('sérialisation locale des cinq modes et refus des valeurs invalides', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const modes = [
      [{ mode: 'point', srid: 3950, coordinates: [1700000, 8200000] }, null],
      [{ mode: 'point', srid: 4326, coordinates: [2, 46] }, null],
      [{ mode: 'point', srid: 4326, coordinates: [2, 46], troncon_id: trunk, systeme_reperage_id: system }, revision],
      [{ mode: 'bornage', troncon_id: trunk, systeme_reperage_id: system,
        borne_id: borne, position_relative: 'SUR_BORNE', distance_m: 0 }, revision],
      [{ mode: 'pr', troncon_id: trunk, systeme_reperage_id: system, pr: 125.5 }, revision],
      [{ mode: 'effacer' }, null],
    ];
    const saved = [];
    for (const [changes, referenceRevision] of modes) {
      const operation = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(),
        photoId: photoOp.resourceId, changes, referenceRevision });
      saved.push(operation.changes);
      await store.removeOperation(operation.operationId, true);
    }
    let invalid = 0;
    for (const value of [{ mode: 'point', srid: 4326, coordinates: [2, Infinity] },
      { mode: 'bornage', troncon_id: trunk, systeme_reperage_id: system,
        borne_id: borne, position_relative: 'SUR_BORNE', distance_m: 1 }]) {
      try { LocalProjection.canonicalPhotoLocalisation(value); } catch (_) { invalid++; }
    }
    return { saved, invalid, media: !!await store.readPhotoMedia(photoOp.mediaLocalRef) };
  });
  expect(result.saved).toHaveLength(6);
  expect(result.saved[3].position_relative).toBe('SUR_BORNE');
  expect(result.saved[5]).toEqual({ mode: 'effacer' });
  expect(result.invalid).toBe(2); expect(result.media).toBe(true);
});

test('conflit OCC sur une Photo : une autre localisation progresse sans rebase', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const second = await store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { ...photoOp.changes, id: crypto.randomUUID(), libelle: 'Seconde' }, observation, media });
    await engine.synchronize(true);
    const a = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(), photoId: photoOp.resourceId,
      changes: { mode: 'point', srid: 4326, coordinates: [2, 46] } });
    const b = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(), photoId: second.resourceId,
      changes: { mode: 'point', srid: 3950, coordinates: [1700000, 8200000] } });
    server.photos.set(a.resourceId, { ...server.photos.get(a.resourceId), version: 2 });
    await engine.synchronize(true);
    const remaining = await store.listOperations();
    await store.removeOperation(a.operationId, true); // explicit, definitive 409: never delete the Photo
    const confirmed = await store.confirmedPhotos(observation.id);
    return { a, b, remaining, confirmed, puts: server.putCalls,
      originalA: !!await store.readPhotoMedia(photoOp.mediaLocalRef), afterReview: await store.listOperations() };
  });
  expect(result.remaining).toHaveLength(1);
  expect(result.remaining[0].operationId).toBe(result.a.operationId);
  expect(result.remaining[0].status).toBe('conflict');
  expect(result.remaining[0].baseVersion).toBe(1);
  expect(result.confirmed.find(item => item.photo.id === result.b.resourceId).photo.version).toBe(2);
  expect(result.originalA).toBe(true);
  expect(result.afterReview).toEqual([]);
  expect(result.puts).toHaveLength(2);
});

test('éditeur local : coordonnées et carte sans tuiles, smartphone', async ({ page }) => {
  await setup(page);
  const tiles = [];
  page.on('request', request => { if (request.url().includes('tile.openstreetmap.org')) tiles.push(request.url()); });
  await page.addStyleTag({ path: 'webapp/frontend/css/app.css' });
  await page.addStyleTag({ path: 'webapp/frontend/css/entity-forms.css' });
  await page.evaluate(() => {
    window.ResourceEvents = { createId: () => crypto.randomUUID() };
    window.OfflineRuntime = { store };
    window.ResourceMutationController = { registerInvalidator: () => {} };
    window.OfflineOperationsRuntime = { synchronize: async () => {} };
  });
  await page.addScriptTag({ path: 'webapp/frontend/js/entity-form-view.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-form.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-localisation.js' });
  await page.setViewportSize({ width: 360, height: 800 });
  await page.evaluate(() => PhotoLocalisation.openOffline(LocalProjection.localPhoto(photoOp), observation));
  const dialog = page.locator('#photo-localisation-dialog');
  await expect(dialog).toBeVisible();
  for (const [width, height] of [[1366,768],[1024,768],[768,1024],[800,360],[360,800]]) {
    await page.setViewportSize({ width, height });
    const box = await dialog.boundingBox();
    expect(box.width).toBeLessThanOrEqual(width + 1);
  }
  await dialog.locator('[data-map-start]').click();
  await expect(dialog.locator('[data-map-editor]')).toBeVisible();
  await dialog.locator('[data-map-cancel]').click();
  await dialog.locator('[name="srid"]').selectOption('4326');
  await dialog.locator('[name="x"]').fill('2');
  await dialog.locator('[name="y"]').fill('46');
  await dialog.locator('button[type="submit"]').click();
  await expect(dialog.locator('[data-role=status]')).toContainText('enregistrée sur cet appareil');
  const result = await page.evaluate(async () => {
    const loc = (await store.listOperations()).find(row => row.action === 'update-localisation');
    const rect = document.querySelector('#photo-localisation-dialog').getBoundingClientRect();
    return { loc, width: rect.width, viewport: innerWidth };
  });
  expect(result.loc.changes).toMatchObject({ mode: 'point', srid: 4326, coordinates: [2, 46] });
  expect(result.width).toBeLessThanOrEqual(result.viewport + 1);
  expect(tiles).toEqual([]);
});

test('références épinglées : le brouillon reste lisible après repréparation', async ({ page }) => {
  await setup(page);
  await page.addStyleTag({ path: 'webapp/frontend/css/app.css' });
  await page.addStyleTag({ path: 'webapp/frontend/css/entity-forms.css' });
  await page.evaluate(() => {
    window.ResourceEvents = { createId: () => crypto.randomUUID() };
    window.OfflineRuntime = { store };
    window.ResourceMutationController = { registerInvalidator: () => {} };
  });
  await page.addScriptTag({ path: 'webapp/frontend/js/entity-form-view.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-form.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-localisation.js' });
  await page.evaluate(async () => {
    window.locationIntent = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(),
      photoId: photoOp.resourceId, referenceRevision: revision,
      changes: { mode: 'bornage', troncon_id: trunk, systeme_reperage_id: system,
        borne_id: borne, position_relative: 'AVANT_BORNE', distance_m: 12 } });
    const before = await store.inspect();
    await store.replace(scope('b'.repeat(64)), before.scope.revision);
    await PhotoLocalisation.openOffline(LocalProjection.localPhoto(photoOp), observation, null, locationIntent);
  });
  const dialog = page.locator('#photo-localisation-dialog');
  await expect(dialog.locator('[data-summary]')).toContainText('B-1 · Amont · 12 m');
  await expect(dialog.locator('[data-role=status]')).toContainText('Références modifiées');
  await expect(dialog.locator('button[type="submit"]')).toBeDisabled();
});

for (const removed of [false, true]) {
  test(`I-01 : reçu perdu, références ${removed ? 'supprimées' : 'modifiées'}, redémarrage et réconciliation`, async ({ page }) => {
    await setup(page);
    const result = await page.evaluate(async removed => {
      await engine.synchronize(true);
      const intent = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(), photoId: photoOp.resourceId,
        baseVersion: 1, changes: { mode: 'pr', troncon_id: trunk, systeme_reperage_id: system, pr: 125 }, referenceRevision: revision });
      server.failLocationReply = true; await engine.synchronize(true);
      const receipt = structuredClone(server.receipts.get(intent.operationId));
      const snapshot = scope('b'.repeat(64));
      if (removed) snapshot.reperageReferences.systems = [];
      await store.replace(snapshot, (await store.inspect()).scope.revision);
      await store.close(); store = LocalProjection.createStore({ name: dbName }); engine = makeEngine();
      const pending = (await store.listOperations())[0];
      // Simulate recovery of a worker interrupted after claiming the operation.
      if (removed) await store.updateOperation(intent.operationId, { status: 'syncing' });
      let abandoned = false;
      try { await store.removeOperation(intent.operationId, true); abandoned = true; } catch (_) { /* unknown */ }
      const eligibility = await store.photoLocalisationEligibility(intent.operationId);
      await engine.synchronize(true);
      return { pending, abandoned, eligibility, receipt, puts: server.putCalls, updateEvents: server.updateEvents,
        final: (await store.confirmedPhotos(observation.id))[0].photo, remaining: await store.listOperations() };
    }, removed);
    expect(result.pending).toMatchObject({ status: 'pending', attempts: 1, baseVersion: 1 });
    expect(result.abandoned).toBe(false); expect(result.eligibility.eligible).toBe(true);
    expect(result.puts).toHaveLength(2); expect(result.puts[1]).toEqual(result.puts[0]);
    expect(result.final.version).toBe(result.receipt.version);
    expect(result.final.mutation).toEqual(result.receipt.mutation);
    expect(result.final.localisation).toEqual(result.receipt.localisation);
    expect(result.updateEvents).toBe(1); expect(result.remaining).toEqual([]);
  });
}

for (const beforeCreate of [false, true]) {
  test(`I-02 : brouillon ${beforeCreate ? 'avant create' : 'v1'}, autre onglet v2, aucun rebase`, async ({ page, context }) => {
    await setup(page);
    await page.evaluate(async beforeCreate => {
      if (!beforeCreate) await engine.synchronize(true);
      window.ResourceEvents = { createId: () => crypto.randomUUID() };
      window.OfflineRuntime = { store }; window.ResourceMutationController = { registerInvalidator: () => {} };
      window.OfflineOperationsRuntime = { synchronize: async () => {} };
    }, beforeCreate);
    await page.addScriptTag({ path: 'webapp/frontend/js/entity-form-view.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-form.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-localisation.js' });
    await page.evaluate(async beforeCreate => {
      const photo = beforeCreate ? LocalProjection.localPhoto(photoOp) : (await store.confirmedPhotos(observation.id))[0].photo;
      await PhotoLocalisation.openOffline(photo, observation);
    }, beforeCreate);
    const dialog = page.locator('#photo-localisation-dialog');
    await dialog.locator('[name=x]').fill('1700001'); await dialog.locator('[name=y]').fill('8200001');
    const shared = await page.evaluate(async () => {
      await engine.synchronize(true);
      const prior = server.photos.get(photoOp.resourceId);
      const response = { ...prior, version: 2, localisation: { mode: 'point', srid: 4326, coordinates: [2.1, 46.1] },
        mutation: { eventId: crypto.randomUUID(), resource: 'photos', action: 'update',
          ids: [prior.id], versions: { [prior.id]: 2 } } };
      server.photos.set(prior.id, response);
      return { dbName, response };
    });
    const other = await context.newPage(); await other.goto('/api/config');
    await other.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
    await other.evaluate(async ({ dbName, response }) => {
      const store = LocalProjection.createStore({ name: dbName });
      const intent = await store.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(), photoId: response.id,
        baseVersion: 1, changes: response.localisation });
      await store.claimPhotoLocalisation(intent.operationId);
      await store.confirmPhotoLocalisation(intent.operationId, response);
      await store.completePhotoLocalisation(intent.operationId); await store.close();
    }, shared);
    await other.close();
    await dialog.locator('button[type=submit]').click();
    await expect(dialog.locator('[data-role=status]')).toContainText('enregistrée sur cet appareil');
    const result = await page.evaluate(async () => {
      const saved = (await store.listOperations())[0]; await engine.synchronize(true);
      return { saved, after: (await store.listOperations())[0], puts: server.putCalls };
    });
    expect(result.saved.baseVersion).toBe(1);
    expect(result.after).toMatchObject({ operationId: result.saved.operationId, status: 'conflict', baseVersion: 1 });
    expect(result.after.changes).toEqual(result.saved.changes);
    expect(result.puts[0].headers['If-Match']).toBe('"1"');
    await expect(dialog.locator('[name=x]')).toHaveValue('1700001');
  });
}

test('CRS préparé 2154 : point local autorisé, autre SRID projeté refusé et intention préservée', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    // A different store models a distinct business database, never a CRS change.
    const other = LocalProjection.createStore({ name: crypto.randomUUID() });
    const snapshot = scope(revision);
    snapshot.crs = { authority: 'EPSG', srid: 2154, code: 'EPSG:2154' };
    snapshot.reperageReferences = { schemaVersion: 2, crs: snapshot.crs, systems: [] };
    await other.replace(snapshot);
    const create = await other.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: crypto.randomUUID(), observation_id: observation.id }, observation, media });
    const intent = await other.enqueuePhotoLocalisation({ operationId: crypto.randomUUID(),
      photoId: create.resourceId, changes: { mode: 'point', srid: 2154, coordinates: [700000, 6600000] } });
    let wrongSRID = false, wrongSnapshot = false;
    try { await other.editPhotoLocalisation(intent.operationId,
      { mode: 'point', srid: 3950, coordinates: [700000, 6600000] }); } catch (_) { wrongSRID = true; }
    const changedCRS = { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' };
    try { await other.replace({ ...snapshot, crs: changedCRS,
      reperageReferences: { ...snapshot.reperageReferences, crs: changedCRS } },
      (await other.inspect()).scope.revision); }
    catch (error) { wrongSnapshot = error.message.includes('Changement de CRS du périmètre refusé.'); }
    const queued = (await other.listOperations()).find(row => row.operationId === intent.operationId);
    const srid = (await other.inspect()).scope.crs.srid;
    await other.close();
    return { wrongSRID, wrongSnapshot, srid, changes: queued.changes };
  });
  expect(result).toEqual({ wrongSRID: true, wrongSnapshot: true, srid: 2154,
    changes: { mode: 'point', srid: 2154, coordinates: [700000, 6600000] } });
});
