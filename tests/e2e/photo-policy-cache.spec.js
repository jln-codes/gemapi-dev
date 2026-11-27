const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC', 'base64');

async function fixture(context, state) {
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/config') {
      if (state.configFailure) return route.abort('failed');
      const json = { legacy_single_database_enabled: true, show_uuid: false, build_id: 'photo-policy-cache-test' };
      if (state.policy !== undefined) json.photo_quality_policy = state.policy;
      return route.fulfill({ json });
    }
    if (path === '/api/systemes-endiguement') return route.fulfill({ json: { systemes: [] } });
    if (path === '/api/data/desordres/query') return route.fulfill({ json: {
      columns: [], rows: [], total: 0, offset: 0, has_next: false,
    } });
    if (path.startsWith('/api/referentiels/')) return route.fulfill({ json: { references: [], types: [], categories: [] } });
    return route.fulfill({ json: {} });
  });
}

async function ready(page) {
  await page.goto('/base');
  await page.waitForFunction(() => !!window.PhotoFile && !!window.OfflineRuntime);
}

async function prepared(page, detailed = false) {
  return page.evaluate(async ({ bytes, detailed }) => {
    let file = new File([new Uint8Array(bytes)], 'terrain.png', { type: 'image/png' });
    if (detailed) {
      const canvas = document.createElement('canvas'); canvas.width = 1200; canvas.height = 900;
      const context = canvas.getContext('2d'), image = context.createImageData(1200, 900); let seed = 73421;
      const random = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return seed >>> 0; };
      for (let at = 0; at < image.data.length; at += 4) {
        image.data[at] = random() & 255; image.data[at + 1] = random() & 255;
        image.data[at + 2] = random() & 255; image.data[at + 3] = 255;
      }
      context.putImageData(image, 0, 0);
      file = new File([await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.96))],
        'terrain-detaille.jpg', { type: 'image/jpeg' });
    }
    const media = await PhotoFile.prepare(file, PhotoQualityPolicy);
    const observation = crypto.randomUUID();
    const operation = await OfflineRuntime.store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: crypto.randomUUID(), observation_id: observation, libelle: 'Politique figée' },
      observation: { id: observation, version: 1, designation: 'Observation connue' }, media });
    const digest = await crypto.subtle.digest('SHA-256', await media.blob.arrayBuffer());
    return { ref: operation.mediaLocalRef, version: media.policyVersion, hash: media.sha256, size: media.size,
      digest: [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('') };
  }, { bytes: [...png], detailed });
}

test('bootstrap : serveur v4, redémarrage offline, puis serveur v5 sans recompression de la Photo existante', async ({ page, context }) => {
  const state = { policy: photoQualityPolicy('haute_qualite', 4, 2 * 1024 * 1024), configFailure: false };
  await fixture(context, state); await ready(page);
  await expect.poll(() => page.evaluate(() => window.PhotoQualityPolicy?.policy_version)).toBe(4);
  const connectedCache = await page.evaluate(() => OfflineRuntime.store.readPhotoQualityPolicy());
  expect(connectedCache.policy).toEqual(photoQualityPolicy('haute_qualite', 4, 2 * 1024 * 1024));
  expect(connectedCache.fetchedAt).toBeGreaterThan(0);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);

  state.configFailure = true; await context.setOffline(true); await page.reload();
  await page.waitForFunction(() => !!window.PhotoFile && !!window.OfflineRuntime);
  await expect.poll(() => page.evaluate(() => window.PhotoQualityPolicy?.policy_version)).toBe(4);
  const oldPhoto = await prepared(page, true);
  expect(oldPhoto.version).toBe(4);
  expect(oldPhoto.size).toBeGreaterThan(500 * 1024);
  expect(oldPhoto.hash).toBe(oldPhoto.digest);

  await context.setOffline(false); state.configFailure = false;
  state.policy = photoQualityPolicy('economique', 5, 500 * 1024);
  await page.reload(); await page.waitForFunction(() => PhotoQualityPolicy?.policy_version === 5);
  const after = await page.evaluate(async ({ ref, older }) => {
    await OfflineRuntime.store.writePhotoQualityPolicy(older, Date.now() + 1000);
    const cached = await OfflineRuntime.store.readPhotoQualityPolicy();
    const media = await OfflineRuntime.store.readPhotoMedia(ref);
    const digest = await crypto.subtle.digest('SHA-256', await media.blob.arrayBuffer());
    return { cached, policyVersion: media.policyVersion, hash: media.sha256, size: media.size,
      digest: [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('') };
  }, { ref: oldPhoto.ref, older: photoQualityPolicy('standard', 4, 2 * 1024 * 1024) });
  expect(after.cached.policy).toEqual(photoQualityPolicy('economique', 5, 500 * 1024));
  expect(after.policyVersion).toBe(4);
  expect(after.hash).toBe(oldPhoto.hash);
  expect(after.size).toBe(oldPhoto.size);
  expect(after.digest).toBe(oldPhoto.digest);
});

test('bootstrap offline sans cache : aucune politique inventée et message de première connexion', async ({ page, context }) => {
  const state = { policy: undefined, configFailure: false };
  await fixture(context, state); await ready(page);
  expect(await page.evaluate(() => PhotoQualityPolicy)).toBeNull();
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  state.configFailure = true; await context.setOffline(true); await page.reload();
  await page.waitForFunction(() => !!window.PhotoFile && !!window.OfflineRuntime);
  const result = await page.evaluate(async bytes => {
    try {
      await PhotoFile.prepare(new File([new Uint8Array(bytes)], 'terrain.png', { type: 'image/png' }), PhotoQualityPolicy);
      return { accepted: true };
    } catch (error) { return { accepted: false, message: error.message, policy: PhotoQualityPolicy }; }
  }, [...png]);
  expect(result.accepted).toBe(false);
  expect(result.policy).toBeNull();
  expect(result.message).toContain('première connexion');
});

test('bootstrap offline avec cache corrompu : démarrage maintenu, record conservé et préparation refusée', async ({ page, context }) => {
  const state = { policy: photoQualityPolicy('standard', 4), configFailure: false };
  await fixture(context, state); await ready(page);
  await expect.poll(() => page.evaluate(() => window.PhotoQualityPolicy?.policy_version)).toBe(4);
  await page.evaluate(async () => {
    const db = await OfflineRuntime.store.open();
    await new Promise((resolve, reject) => {
      const tx = db.transaction('metadata', 'readwrite');
      tx.objectStore('metadata').put({ key: 'photoQualityPolicy', contractVersion: 2,
        profile: 'standard', policyVersion: 4, parametersVersion: 2,
        effectiveParameters: { max_long_edge_px: -1, output_mime_type: 'image/jpeg', encoder_quality: 0.78 },
        fetchedAt: Date.now() });
      tx.oncomplete = resolve; tx.onabort = () => reject(tx.error);
    });
  });
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  const rejectedCache = page.waitForEvent('console', message =>
    message.text().includes('Cache local de politique photo ignoré car incompatible.'));
  state.configFailure = true; await context.setOffline(true); await page.reload();
  await rejectedCache;
  await page.waitForFunction(() => !!window.PhotoFile && !!window.OfflineRuntime);
  const result = await page.evaluate(async bytes => {
    const db = await OfflineRuntime.store.open();
    const raw = await new Promise((resolve, reject) => {
      const request = db.transaction('metadata').objectStore('metadata').get('photoQualityPolicy');
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    let message = '';
    try { await PhotoFile.prepare(new File([new Uint8Array(bytes)], 'terrain.png', { type: 'image/png' }), PhotoQualityPolicy); }
    catch (error) { message = error.message; }
    return { policy: PhotoQualityPolicy, raw, message };
  }, [...png]);
  expect(result.policy).toBeNull();
  expect(result.raw.effectiveParameters.max_long_edge_px).toBe(-1);
  expect(result.message).toContain('première connexion');
});
