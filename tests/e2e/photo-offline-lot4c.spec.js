const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC', 'base64');
async function setup(page) {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-file.js' });
  await page.evaluate(() => { window.module = { exports: {} }; });
  await page.addScriptTag({ path: 'webapp/frontend/js/offline-operations.js' });
  await page.evaluate(() => { window.OfflineOperations = module.exports; delete window.module; });
  await page.evaluate(async ({ bytes, policy }) => {
    window.photoPolicy = policy;
    window.dbName = crypto.randomUUID(); window.store = LocalProjection.createStore({ name: dbName });
    window.observation = { id: crypto.randomUUID(), designation: 'Observation terrain', version: 1 };
    window.media = await PhotoFile.prepare(new File([new Uint8Array(bytes)], 'terrain.png', { type: 'image/png' }), photoPolicy);
    window.enqueue = (withMedia = true) => store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: crypto.randomUUID(), observation_id: observation.id, designation: 'Terrain', date: '2026-09-23' },
      observation, media: withMedia ? media : null });
    window.server = { uploads: new Map(), uploadBodies: new Map(), receipts: new Map(), posts: [], puts: 0,
      events: [], rows: new Map(), fail: null, refreshFail: false };
    window.makeEngine = () => OfflineOperations.createEngine({ store, catchUp: async () => {
      if (server.refreshFail && server.rows.size) throw new Error('journal indisponible'); return '0';
    }, headers: async () => ({}), publish: async event => { server.events.push(event.eventId); }, locks: navigator.locks,
    fetchImpl: async (url, options) => {
      const reply = (status, json) => ({ ok: status < 300, status, json: async () => json });
      if (url.startsWith('/api/photo-uploads/')) {
        const id = url.split('/')[3], hash = url.split('/')[4].split('?')[0];
        if (options.method !== 'PUT') return server.uploads.has(id) ? reply(200, server.uploads.get(id)) : reply(404, { detail: { code: 'upload-not-found' } });
        server.puts++;
        if (server.fail === 'upload') { server.fail = null; throw new TypeError('interruption upload'); }
        const digest = await crypto.subtle.digest('SHA-256', await options.body.arrayBuffer());
        server.uploadBodies.set(id, { size: options.body.size, mime: options.body.type,
          sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') });
        const descriptor = { id, observation_id: observation.id, upload_ref: crypto.randomUUID(), file_sha256: hash,
          file_size: options.body.size, mime_type: options.body.type, expires_at: Date.now()/1000 + 86400 };
        server.uploads.set(id, descriptor);
        if (server.fail === 'upload-reply') { server.fail = null; throw new TypeError('réponse PUT perdue'); }
        return reply(200, descriptor);
      }
      const body = JSON.parse(options.body), op = options.headers['Operation-Id'];
      server.posts.push({ body, op });
      if (server.receipts.has(op)) return reply(201, server.receipts.get(op));
      if (server.fail && server.fail.id === body.id) return reply(server.fail.status, { detail: { code: server.fail.code, message: 'refus simulé' } });
      if (server.fail === 'expire-after-post') { server.fail = null; server.uploads.delete(body.id); throw new TypeError('POST perdu avant commit'); }
      if (!server.uploads.has(body.id) || server.uploads.get(body.id).upload_ref !== body.upload_ref) return reply(410, { detail: { code: 'upload-expired' } });
      const response = { ...body, version: 1, content_available: true, content_url: `/api/photos/${body.id}/content`,
        mutation: { eventId: crypto.randomUUID(), resource: 'photos', action: 'create', ids: [body.id], versions: { [body.id]: 1 } } };
      server.receipts.set(op, response); server.rows.set(body.id, response);
      if (server.fail === 'post-reply') { server.fail = null; throw new TypeError('réponse POST perdue'); }
      return reply(201, response);
    } });
    window.engine = makeEngine();
  }, { bytes: [...png], policy: photoQualityPolicy() });
}

test('v5 : Blob, hash et intention durables, édition, scope et abandon atomique', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const first = await enqueue();
    const edited = await store.editPhotoCreate(first.operationId, { ...first.changes, libelle: 'Modifiée' });
    await store.close(); store = LocalProjection.createStore({ name: dbName });
    const read = await store.readPhotoMedia(edited.mediaLocalRef);
    const before = await store.listOperations();
    const trunk = crypto.randomUUID();
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [trunk], records: [] });
    const survived = await store.readPhotoMedia(edited.mediaLocalRef);
    await store.removeOperation(edited.operationId, true);
    return { first, edited, before, bytes: [...new Uint8Array(await read.blob.arrayBuffer())],
      expectedBytes: [...new Uint8Array(await media.blob.arrayBuffer())], policyVersion: read.policyVersion,
      hash: read.sha256, mime: read.mimeType, size: read.size, survived: !!survived, version: (await store.open()).version,
      remaining: await store.listOperations(), removed: await store.readPhotoMedia(edited.mediaLocalRef) };
  });
  expect(result.version).toBe(5); expect(result.bytes).toEqual(result.expectedBytes); expect(result.mime).toBe('image/jpeg');
  expect(result.size).toBe(result.bytes.length); expect(result.hash).toMatch(/^[a-f0-9]{64}$/); expect(result.policyVersion).toBe(1);
  expect(result.edited.resourceId).toBe(result.first.resourceId); expect(result.edited.operationId).not.toBe(result.first.operationId);
  expect(result.before[0]).toEqual(result.edited); expect(result.survived).toBe(true);
  expect(result.remaining).toEqual([]); expect(result.removed).toBeNull();
});

test('baisse de limite : le replay transmet octet pour octet le Blob déjà préparé', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const operation = await enqueue();
    const before = await store.readPhotoMedia(operation.mediaLocalRef);
    const expected = [...new Uint8Array(await before.blob.arrayBuffer())];
    // Simule une politique courante devenue plus stricte. Le moteur de replay
    // ne prépare jamais de nouveau le média figé avec sa policyVersion initiale.
    window.PhotoQualityPolicy = { ...photoPolicy, policy_version: photoPolicy.policy_version + 1,
      max_file_size_bytes: 100,
      effective_parameters: { ...photoPolicy.effective_parameters, max_file_size_bytes: 100 } };
    await engine.synchronize(true);
    const uploaded = server.uploadBodies.get(operation.resourceId);
    return { expected, expectedHash: before.sha256, oldPolicyVersion: before.policyVersion, currentPolicyVersion: PhotoQualityPolicy.policy_version,
      oldSize: before.size, currentLimit: PhotoQualityPolicy.effective_parameters.max_file_size_bytes,
      uploaded, remaining: await store.listOperations() };
  });
  expect(result.oldSize).toBeGreaterThan(result.currentLimit);
  expect(result.currentPolicyVersion).toBeGreaterThan(result.oldPolicyVersion);
  expect(result.uploaded.size).toBe(result.expected.length);
  expect(result.uploaded.sha256).toBe(result.expectedHash);
  expect(result.remaining).toEqual([]);
});

test('quota : ajout média annulé, intention existante et autres octets intacts', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const saved = await enqueue(), draft = await enqueue(false), original = IDBObjectStore.prototype.add;
    IDBObjectStore.prototype.add = function (...args) {
      if (this.name === 'photoMedia') throw new DOMException('quota simulé', 'QuotaExceededError');
      return original.apply(this, args);
    };
    let error;
    try { await store.editPhotoCreate(draft.operationId, { ...draft.changes, libelle: 'Pas persisté' }, media); }
    catch (e) { error = e.name; } finally { IDBObjectStore.prototype.add = original; }
    return { error, draft, rows: await store.listOperations(), saved: !!await store.readPhotoMedia(saved.mediaLocalRef),
      mediaCount: await new Promise(resolve => { store.open().then(db => {
        const request = db.transaction('photoMedia').objectStore('photoMedia').count(); request.onsuccess = () => resolve(request.result);
      }); }) };
  });
  expect(result.error).toBe('QuotaExceededError'); expect(result.rows[1]).toEqual(result.draft);
  expect(result.saved).toBe(true); expect(result.mediaCount).toBe(1);
});

for (const failure of ['upload', 'upload-reply', 'post-reply', 'expire-after-post']) {
  test(`replay ${failure} : redémarrage, même UUID et intention, une Photo finale`, async ({ page }) => {
    await setup(page);
    const result = await page.evaluate(async failure => {
      const op = await enqueue(); server.fail = failure;
      await engine.synchronize(true);
      const pending = (await store.listOperations())[0], mediaBefore = await store.readPhotoMedia(op.mediaLocalRef);
      let abandonRefused = false, editRefused = false;
      if (pending.attempts) {
        try { await store.removeOperation(op.operationId, true); } catch (_) { abandonRefused = true; }
        try { await store.editPhotoCreate(op.operationId, { ...op.changes, libelle: 'Interdit' }); } catch (_) { editRefused = true; }
      }
      await store.close(); store = LocalProjection.createStore({ name: dbName }); engine = makeEngine();
      await engine.synchronize(true);
      const after = await store.readPhotoMedia(op.mediaLocalRef);
      return { op, pending, hasBytes: !!mediaBefore.blob, after: { integrated: after.integrated, hasBytes: !!after.blob, photo: after.confirmation },
        uploaded: server.uploadBodies.get(op.resourceId), mediaHash: after.sha256, mediaSize: after.size,
        rows: server.rows.size, receipts: server.receipts.size, puts: server.puts, posts: server.posts,
        remaining: await store.listOperations(), abandonRefused, editRefused };
    }, failure);
    expect(result.rows).toBe(1); expect(result.receipts).toBe(1); expect(result.remaining).toEqual([]);
    expect(result.hasBytes).toBe(true); expect(result.after.integrated).toBe(true); expect(result.after.hasBytes).toBe(true);
    expect(result.uploaded).toEqual({ size: result.mediaSize, mime: 'image/jpeg', sha256: result.mediaHash });
    expect(result.after.photo.id).toBe(result.op.resourceId); expect(result.after.photo.version).toBe(1);
    expect(result.puts).toBe(['upload', 'expire-after-post'].includes(failure) ? 2 : 1);
    if (result.pending.attempts) { expect(result.abandonRefused).toBe(true); expect(result.editRefused).toBe(true); }
    const identities = new Set(result.posts.map(p => p.op)); expect(identities.size).toBe(1);
    if (failure === 'post-reply') expect(result.posts[0]).toEqual(result.posts[1]);
    if (failure === 'expire-after-post') {
      const { upload_ref: oldRef, ...a } = result.posts[0].body;
      const { upload_ref: newRef, ...b } = result.posts.at(-1).body;
      expect(oldRef).not.toBe(newRef); expect(a).toEqual(b);
    }
  });
}

for (const [status, code] of [[409, 'operation-id-collision'], [409, 'photo-id-collision'], [422, 'observation-not-found'], [503, 'unavailable']]) {
  test(`Photo refusée ${code} : les Photos suivantes convergent`, async ({ page }) => {
    await setup(page);
    const result = await page.evaluate(async ({ status, code }) => {
      const a = await enqueue(), b = await enqueue(), c = await enqueue();
      server.fail = { id: a.resourceId, status, code }; await engine.synchronize(true);
      return { a, remaining: await store.listOperations(), rows: [...server.rows.keys()], retained: !!await store.readPhotoMedia(a.mediaLocalRef) };
    }, { status, code });
    expect(result.remaining).toHaveLength(1); expect(result.remaining[0].resourceId).toBe(result.a.resourceId);
    expect(result.remaining[0].status).toBe(status === 503 ? 'pending' : status === 409 ? 'conflict' : 'failed');
    expect(result.rows).toHaveLength(2); expect(result.retained).toBe(true);
  });
}

test('confirmation : journal indisponible conserve reçu et Blob, sans second POST', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const op = await enqueue(); server.refreshFail = true; await engine.synchronize(true);
    const pending = (await store.listOperations())[0];
    server.refreshFail = false; await engine.synchronize(true);
    return { pending, posts: server.posts.length, remaining: await store.listOperations(), confirmed: await store.confirmedPhotos(observation.id) };
  });
  expect(result.pending.status).toBe('confirmed'); expect(result.posts).toBe(1);
  expect(result.remaining).toEqual([]); expect(result.confirmed).toHaveLength(1);
});

test('fichiers : validation contenu, dimensions, MIME, extension et décodage', async ({ page }) => {
  await setup(page);
  const checked = await page.evaluate(async bytes => {
    const results = [];
    for (const file of [new File([], 'vide.png'), new File(['texte'], 'faux.png', { type: 'image/png' }),
      new File([new Uint8Array(bytes)], 'faux.jpg', { type: 'image/jpeg' }),
      new File([new Uint8Array(bytes)], '../nom.png', { type: 'image/png' }),
      new File([new Uint8Array(bytes)], 'mime.png', { type: 'image/jpeg' })]) {
      try { await PhotoFile.prepare(file, photoPolicy); results.push(false); } catch (_) { results.push(true); }
    }
    const image = new Uint8Array(bytes); new DataView(image.buffer).setUint32(16, 25001);
    try { await PhotoFile.prepare(new File([image], 'grand.png', { type: 'image/png' }), photoPolicy); results.push(false); } catch (_) { results.push(true); }
    const canvas = document.createElement('canvas'); canvas.width = 12; canvas.height = 8;
    canvas.getContext('2d').fillRect(0, 0, 12, 8);
    const formats = [];
    for (const [mime, extension] of [['image/jpeg', 'jpg'], ['image/webp', 'webp']]) {
      const blob = await new Promise(resolve => canvas.toBlob(resolve, mime));
      const media = await PhotoFile.prepare(new File([blob], `terrain.${extension}`, { type: mime }), photoPolicy);
      formats.push([media.mimeType, media.width, media.height]);
    }
    return { errors: results, formats };
  }, [...png]);
  expect(checked.errors).toEqual([true, true, true, true, true, true]);
  expect(checked.formats).toEqual([['image/jpeg', 12, 8], ['image/jpeg', 12, 8]]);
});

test('parent local : tous les états bloquent, intégration seule autorise la Photo', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    const trunk = crypto.randomUUID();
    await store.replace({ schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: '0', tronconIds: [trunk], records: [
      { resource: 'troncons', id: trunk, serverVersion: 1, payload: { properties: { id: trunk, version: 1 } } } ] });
    observation.version = null;
    const parent = await store.enqueueCreate({ operationId: crypto.randomUUID(), changes: { id: observation.id, parent: { resource: 'troncons', id: trunk } } });
    const photo = await enqueue(), states = [];
    for (const status of ['pending', 'syncing', 'conflict', 'failed', 'confirmed']) {
      await store.updateOperation(parent.operationId, { status });
      states.push(await store.photoEligibility(photo.operationId));
    }
    await store.updateOperation(parent.operationId, { response: { mutation: { versions: { [observation.id]: 1 } } } });
    await store.integratePinned(parent.operationId, { resource: 'observations', id: observation.id, records: [
      { resource: 'observations', id: observation.id, serverVersion: 1, payload: { id: observation.id, version: 1, parent: parent.changes.parent } } ] });
    await store.removeOperation(parent.operationId);
    const ready = await store.photoEligibility(photo.operationId); await engine.synchronize(true);
    return { states, ready, count: server.rows.size };
  });
  expect(result.states.map(s => s.eligible)).toEqual([false, false, false, false, false]);
  expect(result.states.map(s => s.reason)).toEqual(['parent-pending', 'parent-syncing', 'parent-conflict', 'parent-failed', 'parent-confirmed']);
  expect(result.ready.eligible).toBe(true); expect(result.count).toBe(1);
});

test('deux workers Web Locks : une seule finalisation, abandon pendant PUT sans POST', async ({ page }) => {
  await setup(page);
  const result = await page.evaluate(async () => {
    await enqueue(); await Promise.all([engine.synchronize(true), makeEngine().synchronize(true)]);
    const firstCount = server.posts.length;
    const op = await enqueue(); await store.beginPhotoUpload(op.operationId);
    await store.removeOperation(op.operationId, true);
    const lateBinding = await store.bindPhotoUpload(op.operationId, {});
    const lateClaim = await store.claimPhotoPost(op.operationId);
    return { firstCount, lateBinding, lateClaim, removed: await store.readPhotoMedia(op.mediaLocalRef) };
  });
  expect(result.firstCount).toBe(1); expect(result.lateBinding).toBeNull(); expect(result.lateClaim).toBeNull(); expect(result.removed).toBeNull();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 1366, height: 768 }]) {
  test(`PWA ${viewport.width} : capture, double soumission, plusieurs fichiers et réouverture offline`, async ({ page, context }) => {
    test.setTimeout(90000);
    await page.setViewportSize(viewport);
    const { mockResourceJournal } = require('./mock-resource-journal');
    await context.route('**/api/**', async route => {
      if (await mockBusinessCRS(route)) return;
      if (await mockResourceJournal(route)) return;
      if (new URL(route.request().url()).pathname === '/api/config') return route.fulfill({ json: {
        legacy_single_database_enabled: true, show_uuid: false, build_id: 'photo-quality-test',
        photo_quality_policy: photoQualityPolicy(),
      } });
      return route.fulfill({ json: { systemes: [], troncons: [], digues: [], rows: [], columns: [] } });
    });
    await page.goto('/base'); await page.waitForFunction(() => !!window.PhotoOffline && !!window.PhotoFile);
    await page.evaluate(() => navigator.serviceWorker.ready);
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
    const observation = { id: randomUUID(), version: 1, designation: 'Observation mobile' };
    await context.setOffline(true);
    await page.evaluate(observation => PhotoOffline.open(observation), observation);
    const dialog = page.locator('#photo-offline-dialog');
    await expect(dialog.locator('[data-camera]')).toHaveAttribute('capture', 'environment');
    await dialog.locator('[data-camera]').setInputFiles({ name: 'terrain.png', mimeType: 'image/png', buffer: png });
    await dialog.locator('[name=libelle]').fill('Première');
    await expect(dialog.locator('[data-preview]')).toBeVisible();
    const photoId = await dialog.getAttribute('data-photo-id');
    await dialog.locator('[type=submit]').dblclick();
    await expect(dialog.locator('[role=status]')).toContainText('enregistrée localement');
    await expect.poll(() => page.evaluate(() => OfflineRuntime.store.listOperations().then(o => o.length))).toBe(1);
    expect(await dialog.evaluate(d => d.scrollWidth <= d.clientWidth + 1)).toBe(true);
    await dialog.locator('[data-another]').click();
    await dialog.locator('[data-file]').setInputFiles({ name: 'autre.png', mimeType: 'image/png', buffer: png });
    await dialog.locator('[name=libelle]').fill('Deuxième'); await dialog.locator('[type=submit]').click();
    await expect(dialog.locator('[role=status]')).toContainText('enregistrée localement');
    await dialog.locator('[data-close]').click();
    await page.reload(); await page.waitForFunction(() => !!window.PhotoOffline);
    await page.evaluate(async id => OfflineRuntime.showDetail('observations', id), observation.id);
    await expect(page.locator('.photo-local-card')).toHaveCount(2);
    const persisted = await page.evaluate(async id => {
      const op = (await OfflineRuntime.store.listOperations()).find(o => o.resourceId === id);
      const media = await OfflineRuntime.store.readPhotoMedia(op.mediaLocalRef);
      const digest = await crypto.subtle.digest('SHA-256', await media.blob.arrayBuffer());
      return { mime: media.blob.type, policyVersion: media.policyVersion, operationPolicyVersion: op.photoPolicyVersion,
        hash: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join(''), declaredHash: media.sha256 };
    }, photoId);
    expect(persisted.mime).toBe('image/jpeg'); expect(persisted.policyVersion).toBe(1);
    expect(persisted.operationPolicyVersion).toBe(1); expect(persisted.hash).toBe(persisted.declaredHash);
    await page.locator('.photo-local-card').first().getByRole('button', { name: 'Voir le fichier local' }).click();
    await expect.poll(() => page.locator('.photo-viewer img').evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  });
}

test('arrêt complet Chromium : même fichier et intention dans le profil PWA rouvert hors ligne', async ({ baseURL }) => {
  test.setTimeout(90000);
  const { chromium } = require('playwright');
  const { mkdtempSync, rmSync } = require('node:fs');
  const { tmpdir } = require('node:os'); const { join } = require('node:path');
  const profile = mkdtempSync(join(tmpdir(), 'lot4c-pwa-restart-'));
  let context = await chromium.launchPersistentContext(profile, { baseURL, headless: true });
  try {
    let page = await context.newPage(); await page.goto('/base');
    await page.waitForFunction(() => !!window.PhotoFile && !!window.OfflineRuntime);
    await page.evaluate(() => navigator.serviceWorker.ready);
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
    await context.setOffline(true);
    const before = await page.evaluate(async ({ bytes, policy }) => {
      const media = await PhotoFile.prepare(new File([new Uint8Array(bytes)], 'capture.png', { type: 'image/png' }), policy);
      const id = crypto.randomUUID();
      const operation = await OfflineRuntime.store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
        changes: { id: crypto.randomUUID(), observation_id: id, libelle: 'Avant fermeture' },
        observation: { id, version: 1, designation: 'Observation serveur connue' }, media });
      return { operation, hash: media.sha256, size: media.size, mime: media.mimeType };
    }, { bytes: [...png], policy: photoQualityPolicy() });
    await context.close();
    context = await chromium.launchPersistentContext(profile, { baseURL, headless: true, offline: true });
    page = await context.newPage(); await page.goto('/base'); await page.waitForFunction(() => !!window.PhotoOffline);
    const after = await page.evaluate(async ref => {
      const media = await OfflineRuntime.store.readPhotoMedia(ref);
      return { operations: await OfflineRuntime.store.listOperations(), hash: media.sha256,
        bytes: [...new Uint8Array(await media.blob.arrayBuffer())], size: media.size, mime: media.mimeType };
    }, before.operation.mediaLocalRef);
    expect(after.operations[0].resourceId).toBe(before.operation.resourceId);
    expect(after.operations[0].operationId).toBe(before.operation.operationId);
    expect(after.hash).toBe(before.hash); expect(after.bytes).toHaveLength(before.size);
    expect(after.mime).toBe(before.mime); expect(after.mime).toBe('image/jpeg'); expect(after.size).toBe(before.size);
    await page.evaluate(id => OfflineRuntime.showDetail('observations', id), before.operation.changes.observation_id);
    await expect(page.locator('.photo-local-card')).toContainText('Avant fermeture');
  } finally { await context.close(); rmSync(profile, { recursive: true, force: true }); }
});

test('PostGIS réel : Observation locale → original local → upload → réponse POST perdue → reçu unique', async ({ page, context, request }) => {
  test.skip(process.env.DIGUES_E2E_OCC !== '1', 'PostGIS et MediaStorage jetables requis');
  test.setTimeout(120000);
  const s = await (await request.post('/api/systemes-endiguement', { data: { libelle: 'Terrain Photo 4C' } })).json();
  const d = await (await request.post('/api/digues', { data: { libelle: 'Digue Photo 4C', systeme_endiguement_id: s.id } })).json();
  const t = await (await request.post('/api/troncons', { data: { libelle: 'Tronçon Photo 4C', digue_id: d.id,
    geometry: { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] } } })).json();
  const head = (await (await request.get('/api/resource-events/head')).json()).cursor;
  await page.goto('/base'); await page.waitForFunction(() => !!window.PhotoFile && !!window.OfflineOperationsRuntime);
  await page.evaluate(id => OfflineRuntime.prepare([id]), t.properties.id);
  await page.evaluate(() => navigator.serviceWorker.ready); await context.setOffline(true);
  const local = await page.evaluate(async ({ trunk, bytes }) => {
    const parent = await OfflineRuntime.store.enqueueCreate({ operationId: crypto.randomUUID(), changes: {
      id: crypto.randomUUID(), parent: { resource: 'troncons', id: trunk }, designation: 'Observation avec Photo terrain' } });
    const media = await PhotoFile.prepare(new File([new Uint8Array(bytes)], 'terrain.png', { type: 'image/png' }), PhotoQualityPolicy);
    const photo = await OfflineRuntime.store.enqueuePhotoCreate({ operationId: crypto.randomUUID(), changes: {
      id: crypto.randomUUID(), observation_id: parent.resourceId, libelle: 'Original terrain', date: '2026-09-23' },
      observation: { id: parent.resourceId, version: null, designation: parent.changes.designation }, media });
    return { parent, photo, preparedSize: media.size, preparedBytes: [...new Uint8Array(await media.blob.arrayBuffer())] };
  }, { trunk: t.properties.id, bytes: [...png] });
  let lost = false; const calls = []; let first;
  await context.route('**/api/photos', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    calls.push({ op: route.request().headers()['operation-id'], body: route.request().postDataJSON() });
    const response = await route.fetch();
    if (!lost) { first = await response.json(); lost = true; return route.abort('failed'); }
    return route.fulfill({ response });
  });
  await context.setOffline(false); await page.evaluate(() => OfflineOperationsRuntime.synchronize(true));
  await expect.poll(() => lost).toBe(true);
  expect(first.id).toBe(local.photo.resourceId); expect(first.observation_id).toBe(local.parent.resourceId);
  await context.setOffline(true); await page.reload(); await page.waitForFunction(() => !!window.OfflineOperationsRuntime);
  expect(await page.evaluate(ref => OfflineRuntime.store.readPhotoMedia(ref).then(m => m.blob.size), local.photo.mediaLocalRef)).toBe(local.preparedSize);
  await context.setOffline(false);
  await expect.poll(() => page.evaluate(async () => {
    await OfflineOperationsRuntime.synchronize(true);
    return OfflineRuntime.store.listOperations().then(o => o.length);
  }), { timeout: 30000 }).toBe(0);
  expect(calls).toHaveLength(2); expect(calls[1]).toEqual(calls[0]);
  const serverPhoto = await (await request.get(`/api/photos/${first.id}`)).json();
  expect(serverPhoto.version).toBe(1); expect(serverPhoto.observation_id).toBe(local.parent.resourceId);
  expect(await (await request.get(serverPhoto.content_url)).body()).toEqual(Buffer.from(local.preparedBytes));
  const retained = await page.evaluate(ref => OfflineRuntime.store.readPhotoMedia(ref).then(m => ({ integrated: m.integrated, event: m.confirmation.mutation.eventId })), local.photo.mediaLocalRef);
  expect(retained).toEqual({ integrated: true, event: first.mutation.eventId });
  const events = (await (await request.get(`/api/resource-events?after=${head}&limit=100`)).json()).events;
  expect(events.filter(e => e.event.resource === 'photos' && e.event.ids.includes(first.id))).toHaveLength(1);
});
