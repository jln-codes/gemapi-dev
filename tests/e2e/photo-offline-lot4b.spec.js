const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { createEngine } = require('../../webapp/frontend/js/offline-operations.js');
const { mockResourceJournal } = require('./mock-resource-journal');

// These fixtures live only in IndexedDB. A real journal must not refresh them
// from PostgreSQL while another regression test is writing on that server.
test.beforeEach(async ({ context }) => {
  await context.route('**/api/resource-events**', route => mockResourceJournal(route));
});

function observation(id, version, parentId) {
  return { resource: 'observations', id, serverVersion: version,
    payload: { id, version, designation: 'Observation préparée',
      parent: { resource: 'troncons', id: parentId, designation: 'Tronçon préparé' }, photos: [] } };
}
function trunk(id) {
  return { resource: 'troncons', id, serverVersion: 1, payload: { properties: { id, version: 1, libelle: 'Tronçon préparé' } } };
}

// Real IndexedDB transactions, including close/reopen and scope replacement.
test('schéma courant : trois Photos dépendantes, UUID et intentions durables, édition et pinning', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const ids = { db: randomUUID(), observation: randomUUID(), trunk: randomUUID(), photos: [randomUUID(), randomUUID(), randomUUID()] };
  const result = await page.evaluate(async ({ db, observation, trunk, photos }) => {
    let store = LocalProjection.createStore({ name: db });
    const obs = { resource: 'observations', id: observation, serverVersion: 1,
      payload: { id: observation, version: 1, designation: 'Serveur', parent: { resource: 'troncons', id: trunk, designation: 'T' } } };
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '1', tronconIds: [trunk], records: [obs,
      { resource: 'troncons', id: trunk, serverVersion: 1, payload: { properties: { id: trunk, version: 1, libelle: 'T' } } },
    ] });
    const created = [];
    for (let i = 0; i < photos.length; i++) created.push(await store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: photos[i], observation_id: observation, date: '2026-09-23', libelle: `Photo ${i}` },
      observation: { id: observation, version: 1, designation: 'Serveur',
        parent: { resource: 'troncons', id: trunk, designation: 'T', excludedBinary: new Blob(['hors contrat']) } } }));
    const same = await store.editPhotoCreate(created[0].operationId, { ...created[0].changes, libelle: ' Photo 0 ' });
    const edited = await store.editPhotoCreate(same.operationId, { ...same.changes, libelle: 'Photo modifiée' });
    const forbidden = [];
    for (const invalid of [{ ...edited.changes, observation_id: crypto.randomUUID() },
      { ...edited.changes, upload_ref: crypto.randomUUID() }, { ...edited.changes, orientation_photo_id: 'unprepared' }]) {
      try { await store.editPhotoCreate(edited.operationId, invalid); forbidden.push(false); } catch (_) { forbidden.push(true); }
    }
    for (const patch of [{ status: 'confirmed', response: { mutation: {} } }, { attempts: 1 }]) {
      try { await store.updateOperation(edited.operationId, patch); forbidden.push(false); } catch (_) { forbidden.push(true); }
    }
    const replacement = await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '2', tronconIds: [crypto.randomUUID()], records: [] },
      (await store.inspect()).scope.revision);
    const pinned = await store.read('observations', observation);
    const pinnedTrunk = await store.read('troncons', trunk);
    await store.close(); store = LocalProjection.createStore({ name: db });
    const reopened = await store.listOperations();
    const eligible = await Promise.all(reopened.map(o => store.photoEligibility(o.operationId)));
    const blockedClaim = await store.claimOperation(edited.operationId);
    await store.removeOperation(created[1].operationId, true);
    return { version: (await store.open()).version, replacement, created, same, edited, forbidden, pinned, pinnedTrunk,
      reopened, eligible, blockedClaim, remaining: await store.listOperations() };
  }, ids);
  expect(result.version).toBe(5);
  expect(result.replacement).toBe(true);
  expect(result.created).toHaveLength(3);
  expect(new Set(result.created.map(o => o.resourceId)).size).toBe(3);
  expect(result.created.every(o => o.dependsOn.length === 1 && o.dependsOn[0].id === ids.observation)).toBe(true);
  expect(result.created[0].observationContext.parent).toEqual({ resource: 'troncons', id: ids.trunk, designation: 'T' });
  expect(result.same.operationId).toBe(result.created[0].operationId);
  expect(result.edited.operationId).not.toBe(result.same.operationId);
  expect(result.edited.resourceId).toBe(result.same.resourceId);
  expect(result.forbidden).toEqual([true, true, true, true, true]);
  expect(result.pinned.detached).toBe(true);
  expect(result.pinnedTrunk.detached).toBe(true);
  expect(result.reopened).toHaveLength(3);
  expect(result.eligible.every(s => s.reason === 'media-required' && s.eligible === false)).toBe(true);
  expect(result.blockedClaim).toBeNull();
  expect(result.remaining.map(o => o.resourceId)).toEqual([ids.photos[0], ids.photos[2]]);
});

test('schéma courant : états du parent local, intégration autoritaire et abandon atomique', async ({ page }) => {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  const result = await page.evaluate(async () => {
    const trunk = crypto.randomUUID(), observation = crypto.randomUUID();
    const store = LocalProjection.createStore({ name: crypto.randomUUID() });
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [trunk], records: [
      { resource: 'troncons', id: trunk, serverVersion: 1, payload: { properties: { id: trunk, version: 1, libelle: 'T' } } },
    ] });
    const parent = await store.enqueueCreate({ operationId: crypto.randomUUID(), changes: {
      id: observation, parent: { resource: 'troncons', id: trunk }, designation: 'Locale',
    } });
    const photo = await store.enqueuePhotoCreate({ operationId: crypto.randomUUID(), changes: {
      id: crypto.randomUUID(), observation_id: observation, libelle: 'Dépendante' },
    observation: { id: observation, version: null, designation: 'Locale' } });
    const state = async () => (await store.photoEligibility(photo.operationId)).reason;
    const states = [await state()];
    const syncing = await store.claimOperation(parent.operationId); states.push(await state());
    await store.updateOperation(parent.operationId, { status: 'confirmed', response: { mutation: { versions: { [observation]: 1 } } } });
    states.push(await state());
    const row = { resource: 'observations', id: observation, serverVersion: 1,
      payload: { id: observation, version: 1, designation: 'Locale', parent: { resource: 'troncons', id: trunk, designation: 'T' } } };
    await store.integratePinned(parent.operationId, { resource: 'observations', id: observation, records: [row] });
    states.push(await state());
    await store.removeOperation(parent.operationId); states.push(await state());
    const other = await store.enqueueCreate({ operationId: crypto.randomUUID(), changes: {
      id: crypto.randomUUID(), parent: { resource: 'troncons', id: trunk }, designation: 'À abandonner' } });
    const photos = [];
    for (let i = 0; i < 2; i++) photos.push(await store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: crypto.randomUUID(), observation_id: other.resourceId, designation: `Photo ${i}` },
      observation: { id: other.resourceId, version: null, designation: 'À abandonner' } }));
    const childState = id => store.photoEligibility(id).then(value => value.reason);
    const conflict = await childState(photos[0].operationId);
    await store.updateOperation(other.operationId, { status: 'conflict' });
    const conflicted = await childState(photos[0].operationId);
    await store.updateOperation(other.operationId, { status: 'failed' });
    const failed = await childState(photos[0].operationId);
    let directDiscardRefused = false, incompleteDiscardRefused = false;
    try { await store.removeOperation(other.operationId, true); } catch (_) { directDiscardRefused = true; }
    try { await store.discardObservationWithPhotos(other.operationId, [photos[0].operationId]); } catch (_) { incompleteDiscardRefused = true; }
    const discarded = await store.discardObservationWithPhotos(other.operationId, photos.map(p => p.operationId));
    const remaining = await store.listOperations();
    return { states, syncing: syncing.status, conflict, conflicted, failed, directDiscardRefused,
      incompleteDiscardRefused, discarded, remaining };
  });
  expect(result.states).toEqual(['parent-pending', 'parent-syncing', 'parent-confirmed', 'parent-confirmed', 'media-required']);
  expect(result.syncing).toBe('syncing');
  expect([result.conflict, result.conflicted, result.failed]).toEqual(['parent-pending', 'parent-conflict', 'parent-failed']);
  expect(result.directDiscardRefused).toBe(true);
  expect(result.incompleteDiscardRefused).toBe(true);
  expect(result.discarded).toBe(2);
  expect(result.remaining).toHaveLength(1);
  expect(result.remaining[0].resource).toBe('photos');
});

test('replay : Photo sans média ignorée, opération indépendante exécutée', async () => {
  const photoId = randomUUID(), observationId = randomUUID(), disorderId = randomUUID();
  const photo = { operationId: randomUUID(), resource: 'photos', resourceId: photoId, action: 'create',
    changes: { id: photoId, observation_id: observationId }, dependsOn: [{ resource: 'observations', id: observationId }],
    status: 'pending', attempts: 0 };
  const update = { operationId: randomUUID(), resource: 'desordres', resourceId: disorderId, action: 'update',
    baseVersion: 1, changes: { designation: 'Corrigé' }, status: 'pending', attempts: 0 };
  const remaining = [photo, update], fetches = [], published = [];
  const mutation = { eventId: randomUUID(), resource: 'desordres', action: 'update', ids: [disorderId],
    versions: { [disorderId]: 2 } };
  const store = {
    listOperations: async () => [...remaining], photoEligibility: async () => ({ eligible: false, reason: 'media-required' }),
    inspect: async () => ({ scope: { cursor: '0' } }),
    claimOperation: async id => { const op = remaining.find(o => o.operationId === id); op.status = 'syncing'; op.attempts++; return op; },
    updateOperation: async (id, patch) => Object.assign(remaining.find(o => o.operationId === id), patch),
    removeOperation: async id => remaining.splice(remaining.findIndex(o => o.operationId === id), 1),
  };
  const engine = createEngine({ store, catchUp: async () => '0', recoverPinned: async () => null,
    fetchImpl: async url => { fetches.push(url); return { ok: true, json: async () => ({ mutation }) }; },
    headers: async () => ({}), publish: async event => published.push(event),
    locks: { request: async (_name, _options, callback) => callback({}) } });
  await engine.synchronize(true);
  expect(fetches).toEqual([`/api/desordres/${disorderId}`]);
  expect(published).toEqual([mutation]);
  expect(remaining).toEqual([photo]);
  expect(photo.attempts).toBe(0);
});

test('galerie Photo locale après réouverture hors réseau, sans POST Photo', async ({ context, page }) => {
  test.setTimeout(60000);
  await page.goto('/base');
  await page.waitForFunction(() => !!window.PhotoOffline && !!window.OfflineOperationsRuntime);
  const ids = { observation: randomUUID(), trunk: randomUUID() };
  await page.evaluate(async ({ observation, trunk }) => {
    await OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [trunk], records: [
      { resource: 'observations', id: observation, serverVersion: 1,
        payload: { id: observation, version: 1, designation: 'Observation serveur',
          parent: { resource: 'troncons', id: trunk, designation: 'T' }, photos: [] } },
    ] });
  }, ids);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  let photoPosts = 0;
  context.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/api/photos')) photoPosts++; });
  await context.setOffline(true);
  await page.evaluate(id => OfflineRuntime.showDetail('observations', id), ids.observation);
  await page.getByRole('button', { name: '+ Préparer une Photo' }).click();
  const dialog = page.locator('#photo-offline-dialog');
  await expect(dialog.locator('[name=orientation_photo_id], [name=cote_id], [name=photographe_source_id]')).toHaveCount(0);
  const photoId = await dialog.getAttribute('data-photo-id');
  await dialog.locator('[name=libelle]').fill('Talus amont');
  await dialog.getByRole('button', { name: 'Conserver la Photo locale' }).click();
  await expect(dialog.locator('[role=status]')).toContainText('enregistrée localement');
  await dialog.getByRole('button', { name: 'Retour à l’observation' }).click();
  await expect(page.locator('.photo-local-card')).toContainText('Fichier à ajouter');
  const before = await page.evaluate(() => OfflineRuntime.store.listOperations());
  expect(before).toHaveLength(1);
  expect(before[0].resourceId).toBe(photoId);
  expect(before[0].changes).not.toHaveProperty('upload_ref');
  await page.reload();
  await page.waitForFunction(() => !!window.PhotoOffline);
  await page.evaluate(id => OfflineRuntime.showDetail('observations', id), ids.observation);
  await expect(page.locator('.photo-local-card')).toContainText('Talus amont');
  await page.locator('.photo-local-card').getByRole('button', { name: 'Modifier les métadonnées locales' }).click();
  await dialog.locator('[name=libelle]').fill('Talus aval');
  await dialog.getByRole('button', { name: 'Conserver la Photo locale' }).click();
  const after = await page.evaluate(() => OfflineRuntime.store.listOperations());
  expect(after[0].resourceId).toBe(photoId);
  expect(after[0].operationId).not.toBe(before[0].operationId);
  expect(after[0].changes.libelle).toBe('Talus aval');
  await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
  expect(photoPosts).toBe(0);
  await dialog.getByRole('button', { name: 'Retour à l’observation' }).click();
  page.once('dialog', confirmation => confirmation.accept());
  await page.locator('.photo-local-card').getByRole('button', { name: 'Abandonner cette Photo locale' }).click();
  await expect.poll(() => page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
  await expect(page.locator('.photo-local-card')).toHaveCount(0);
});

test('Observation locale : deux Photos visibles après redémarrage hors ligne', async ({ context, page }) => {
  test.setTimeout(60000);
  await page.goto('/base');
  await page.waitForFunction(() => !!window.PhotoOffline && !!window.OfflineOperationsRuntime);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  await context.setOffline(true);
  const ids = await page.evaluate(async () => {
    const observation = crypto.randomUUID(), trunk = crypto.randomUUID();
    await OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [trunk], records: [
      { resource: 'troncons', id: trunk, serverVersion: 1, payload: { properties: { id: trunk, version: 1, libelle: 'T' } } },
    ] });
    const op = await OfflineRuntime.store.enqueueCreate({ operationId: crypto.randomUUID(), changes: {
      id: observation, parent: { resource: 'troncons', id: trunk }, designation: 'Observation locale',
    } });
    await OfflineRuntime.showDetail('observations', observation);
    return { observation, operationId: op.operationId };
  });
  for (const name of ['Photo B', 'Photo C']) {
    await page.getByRole('button', { name: '+ Préparer une Photo' }).click();
    await page.locator('#photo-offline-dialog [name=libelle]').fill(name);
    await page.locator('#photo-offline-dialog [type=submit]').click();
    await expect(page.locator('#photo-offline-dialog [role=status]')).toContainText('enregistrée localement');
    await page.locator('#photo-offline-dialog [data-close]').click();
  }
  await expect(page.locator('.photo-local-card')).toHaveCount(2);
  await page.reload();
  await page.waitForFunction(() => !!window.PhotoOffline);
  await page.evaluate(id => OfflineRuntime.showDetail('observations', id), ids.observation);
  await expect(page.locator('.photo-local-card')).toHaveCount(2);
  await expect(page.locator('.photo-local-gallery')).toContainText('Photo B');
  await expect(page.locator('.photo-local-gallery')).toContainText('Photo C');
  const operations = await page.evaluate(() => OfflineRuntime.store.listOperations());
  expect(operations.filter(o => o.resource === 'photos')).toHaveLength(2);
  expect(operations.filter(o => o.resource === 'photos').every(o => o.dependsOn[0].id === ids.observation)).toBe(true);
  expect(operations.find(o => o.resource === 'observations').operationId).toBe(ids.operationId);
});

test('Observation serveur non préparée : contexte Photo retrouvé sans fiche autoritaire fictive', async ({ context, page }) => {
  const id = randomUUID(), parent = randomUUID();
  await page.goto('/base');
  await page.waitForFunction(() => !!window.PhotoOffline);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  await page.evaluate(({ id, parent }) => PhotoOffline.open({ id, version: 3, designation: 'Observation lue',
    parent: { resource: 'troncons', id: parent, designation: 'Tronçon lu' } }), { id, parent });
  await page.locator('#photo-offline-dialog [name=libelle]').fill('Cliché sans scope');
  await page.locator('#photo-offline-dialog [type=submit]').click();
  await expect(page.locator('#photo-offline-dialog [role=status]')).toContainText('enregistrée localement');
  await page.locator('#photo-offline-dialog [data-close]').click();
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => !!window.PhotoOffline);
  await page.evaluate(id => OfflineRuntime.showDetail('observations', id), id);
  await expect(page.locator('#offline-detail h2')).toContainText('Contexte Observation conservé');
  await expect(page.locator('.photo-local-card')).toContainText('Cliché sans scope');
  expect(await page.evaluate(async id => {
    try { await OfflineRuntime.fetch(`/api/observations/${id}`); return false; } catch (_) { return true; }
  }, id)).toBe(true);
  const ops = await page.evaluate(() => OfflineRuntime.store.listOperations());
  expect(ops).toHaveLength(1);
  expect(ops[0].observationContext.parent.id).toBe(parent);
  expect(ops[0].observationContext.observedVersion).toBe(3);
});

test('abandon visible de l’Observation locale retire ses deux Photos ensemble', async ({ context, page }) => {
  await page.goto('/base');
  await page.waitForFunction(() => !!window.PhotoOffline && !!window.PhotoQualityPolicy);
  await context.setOffline(true);
  await page.evaluate(async () => {
    const trunk = crypto.randomUUID(), id = crypto.randomUUID();
    await OfflineRuntime.store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [trunk], records: [
      { resource: 'troncons', id: trunk, serverVersion: 1, payload: { properties: { id: trunk, version: 1, libelle: 'T' } } },
    ] });
    await OfflineRuntime.store.enqueueCreate({ operationId: crypto.randomUUID(), changes: {
      id, parent: { resource: 'troncons', id: trunk }, designation: 'À abandonner',
    } });
    for (let i = 0; i < 2; i++) await OfflineRuntime.store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: crypto.randomUUID(), observation_id: id, libelle: `Photo ${i}` },
      observation: { id, version: null, designation: 'À abandonner' } });
    await OfflineRuntime.showDetail('observations', id);
  });
  await expect(page.locator('.photo-local-card')).toHaveCount(2);
  const confirmation = page.waitForEvent('dialog');
  await page.getByRole('button', { name: 'Abandonner la création locale' }).click({ noWaitAfter: true });
  const prompt = await confirmation;
  expect(prompt.message()).toContain('2 Photo(s) locales');
  await prompt.accept();
  await expect.poll(() => page.evaluate(() => OfflineRuntime.store.listOperations())).toEqual([]);
});

test('formulaire Photo connecté : préparation locale accessible sans référentiels ni média', async ({ page }) => {
  const observation = randomUUID();
  await page.goto('/base');
  await page.waitForFunction(() => !!window.PhotoOffline && !!window.PhotoMedia);
  await page.evaluate(id => PhotoMedia.openCreate({ id, version: 1, designation: 'Observation connectée' }), observation);
  await page.locator('#photo-editor-dialog [data-prepare-local]').click();
  await expect(page.locator('#photo-offline-dialog')).toBeVisible();
  await page.locator('#photo-offline-dialog [name=designation]').fill('Vue amont');
  await page.locator('#photo-offline-dialog [type=submit]').click();
  await expect(page.locator('#photo-offline-dialog [role=status]')).toContainText('enregistrée localement');
  const operations = await page.evaluate(() => OfflineRuntime.store.listOperations());
  expect(operations).toHaveLength(1);
  expect(operations[0].changes).toMatchObject({ observation_id: observation, designation: 'Vue amont' });
  expect(operations[0].changes).not.toHaveProperty('upload_ref');
  await page.evaluate(id => {
    const gallery = document.createElement('section'); gallery.id = 'lot4b-connected-gallery'; document.body.append(gallery);
    PhotoMedia.renderGallery(gallery, { id, version: 1, designation: 'Observation connectée', photos: [] });
  }, observation);
  await expect(page.locator('#lot4b-connected-gallery .photo-local-card')).toContainText('Vue amont');
  await expect(page.locator('#lot4b-connected-gallery .photo-local-card img')).toHaveCount(0);
});
