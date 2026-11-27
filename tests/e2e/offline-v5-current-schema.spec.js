const { test, expect } = require('@playwright/test');

async function loadCurrent(page) {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
}

test('base neuve : création directe du schéma v5, réouverture et Blob Photo intact', async ({ page }) => {
  await loadCurrent(page);
  const result = await page.evaluate(async () => {
    const snapshot = (cursor, records) => ({
      schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, cursor, tronconIds: ['T1'], records,
      reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] },
      observationReferences: { suitesApporter: [], urgences: [] },
    });
    const name = `current-v5-${crypto.randomUUID()}`;
    let store = LocalProjection.createStore({ name });
    const db = await store.open();
    const schema = {};
    for (const storeName of db.objectStoreNames) {
      const objectStore = db.transaction(storeName).objectStore(storeName);
      schema[storeName] = {
        keyPath: objectStore.keyPath,
        indexes: Object.fromEntries([...objectStore.indexNames].map(indexName => {
          const index = objectStore.index(indexName);
          return [indexName, { keyPath: index.keyPath, unique: index.unique }];
        })),
      };
    }
    const observationId = crypto.randomUUID();
    await store.replace(snapshot('1', [{
      resource: 'observations',
      id: observationId,
      serverVersion: 1,
      payload: { id: observationId, version: 1,
        parent: { resource: 'troncons', id: 'T1', designation: 'Tronçon' } },
    }]));
    const mediaLocalRef = crypto.randomUUID(), photoId = crypto.randomUUID();
    const tx = db.transaction('photoMedia', 'readwrite');
    tx.objectStore('photoMedia').put({
      mediaLocalRef, photoId, observationId,
      blob: new Blob(['original-photo'], { type: 'image/jpeg' }),
      sha256: 'a'.repeat(64), mimeType: 'image/jpeg', size: 14,
      validationVersion: 1, policyVersion: 1, policyContractVersion: 2,
      policyParametersVersion: 1, profile: 'standard', transfer: { state: 'pending', attempts: 0 },
    });
    await new Promise((resolve, reject) => {
      tx.oncomplete = resolve;
      tx.onabort = () => reject(tx.error);
    });
    await store.close();
    store = LocalProjection.createStore({ name });
    const reopened = await store.open();
    const row = await store.read('observations', observationId);
    const media = await store.readPhotoMedia(mediaLocalRef);
    const mediaResult = {
      text: await media.blob.text(), type: media.blob.type,
      blobSize: media.blob.size, declaredSize: media.size,
      sha256: media.sha256, photoId: media.photoId,
      observationId: media.observationId,
    };
    await store.close();
    return { version: reopened.version, schema, row, media: mediaResult,
      expected: { photoId, observationId } };
  });
  expect(result.version).toBe(5);
  expect(result.schema).toEqual({
    metadata: { keyPath: 'key', indexes: {} },
    operations: { keyPath: 'operationId', indexes: {
      resourceKey: { keyPath: 'resourceKey', unique: true },
      sequence: { keyPath: 'sequence', unique: true },
    } },
    photoMedia: { keyPath: 'mediaLocalRef', indexes: {
      photoId: { keyPath: 'photoId', unique: true },
    } },
    resources: { keyPath: ['resource', 'id'], indexes: {} },
  });
  expect(result.row.serverVersion).toBe(1);
  expect(result.media).toEqual({
    text: 'original-photo', type: 'image/jpeg', blobSize: 14, declaredSize: 14,
    sha256: 'a'.repeat(64), photoId: result.expected.photoId,
    observationId: result.expected.observationId,
  });
});

test('ancien schéma : refus explicite, aucune suppression et aucune base vide de substitution', async ({ page }) => {
  await loadCurrent(page);
  const result = await page.evaluate(async () => {
    const name = `old-schema-${crypto.randomUUID()}`;
    const old = await new Promise((resolve, reject) => {
      const request = indexedDB.open(name, 4);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('resources', { keyPath: ['resource', 'id'] });
        request.result.createObjectStore('metadata', { keyPath: 'key' })
          .put({ key: 'preuve', value: 'conservée' });
        const operations = request.result.createObjectStore('operations', { keyPath: 'operationId' });
        operations.createIndex('resourceKey', 'resourceKey', { unique: true });
        operations.createIndex('sequence', 'sequence', { unique: true });
        request.result.createObjectStore('photoMedia', { keyPath: 'mediaLocalRef' })
          .createIndex('photoId', 'photoId', { unique: true });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    old.close();
    let error;
    try { await LocalProjection.createStore({ name }).open(); }
    catch (caught) { error = { name: caught.name, message: caught.message }; }
    const preserved = await new Promise((resolve, reject) => {
      const request = indexedDB.open(name, 4);
      request.onsuccess = () => {
        const db = request.result;
        const read = db.transaction('metadata').objectStore('metadata').get('preuve');
        read.onsuccess = () => { const value = read.result; db.close(); resolve(value); };
      };
      request.onerror = () => reject(request.error);
    });
    return { error, preserved };
  });
  expect(result.error.name).toBe('VersionError');
  expect(result.error.message).toContain('incompatible');
  expect(result.preserved).toEqual({ key: 'preuve', value: 'conservée' });
});

test('transaction courante annulée : données et curseur précédents restent intacts', async ({ page }) => {
  await loadCurrent(page);
  const result = await page.evaluate(async () => {
    const snapshot = (cursor, records) => ({
      schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, cursor, tronconIds: ['T1'], records,
      reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] },
      observationReferences: { suitesApporter: [], urgences: [] },
    });
    const store = LocalProjection.createStore({ name: `abort-v5-${crypto.randomUUID()}` });
    const row = version => ({ resource: 'systemes', id: 'S1', serverVersion: version,
      payload: { id: 'S1', version, libelle: `Version ${version}` } });
    await store.replace(snapshot('1', [row(1)]));
    const revision = (await store.inspect()).scope.revision;
    const original = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (...args) {
      const request = original.apply(this, args);
      if (this.name === 'metadata') this.transaction.abort();
      return request;
    };
    let error;
    try { await store.replace(snapshot('2', [row(2)]), revision); }
    catch (caught) { error = caught.name; }
    finally { IDBObjectStore.prototype.put = original; }
    const after = await store.inspect();
    return { error, cursor: after.scope.cursor, version: after.records[0].serverVersion };
  });
  expect(['AbortError', 'TransactionInactiveError']).toContain(result.error);
  expect(result.cursor).toBe('1');
  expect(result.version).toBe(1);
});
