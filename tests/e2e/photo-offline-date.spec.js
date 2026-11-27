const { test, expect } = require('@playwright/test');
const { chromium } = require('playwright');
const { randomUUID } = require('node:crypto');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');

async function loadForm(page, name) {
  await page.goto('/api/config');
  await page.addScriptTag({ path: 'webapp/frontend/js/local-projection.js' });
  await page.evaluate(dbName => {
    window.OfflineRuntime = { store: LocalProjection.createStore({ name: dbName }) };
    window.ResourceEvents = { createId: () => crypto.randomUUID() };
    window.PhotoFile = { persistence: async () => ({ persistent: false }) };
    window.OfflineOperationsRuntime = { synchronize: async () => {} };
  }, name);
  await page.addScriptTag({ path: 'webapp/frontend/js/entity-form-view.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-form.js' });
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-offline.js' });
}

test('nouvelle Photo : aujourd’hui ; date existante et date absente historique restaurées', async ({ page }) => {
  await loadForm(page, randomUUID());
  const observation = { id: randomUUID(), version: 1, designation: 'Observation terrain' };
  const today = await page.evaluate(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  });
  await page.evaluate(obs => PhotoOffline.open(obs), observation);
  const dialog = page.locator('#photo-offline-dialog');
  await expect(dialog.locator('[name=date]')).toHaveValue(today);
  await dialog.locator('[type=submit]').click();
  await expect(dialog.locator('[data-role=status]')).toContainText('enregistrée localement');
  const first = await page.evaluate(() => OfflineRuntime.store.listOperations().then(operations => operations[0]));
  expect(first.changes.date).toBe(today);
  await dialog.locator('[data-close]').click();

  await page.evaluate(obs => PhotoOffline.open(obs), observation);
  await dialog.locator('[name=date]').fill('2026-09-20');
  await dialog.locator('[type=submit]').click();
  await expect(dialog.locator('[data-role=status]')).toContainText('enregistrée localement');
  const dated = await page.evaluate(async id => (await OfflineRuntime.store.listOperations()).find(operation => operation.resourceId !== id), first.resourceId);
  await dialog.locator('[data-close]').click();
  await page.evaluate(({ obs, operation }) => PhotoOffline.open(obs, operation), { obs: observation, operation: dated });
  await expect(dialog.locator('[name=date]')).toHaveValue('2026-09-20');
  await dialog.locator('[data-close]').click();

  // A pre-date intent is distinct from an intent whose date was explicitly cleared.
  const legacy = await page.evaluate(async obs => {
    const store = OfflineRuntime.store;
    const operation = await store.enqueuePhotoCreate({ operationId: crypto.randomUUID(),
      changes: { id: crypto.randomUUID(), observation_id: obs.id, libelle: 'Ancien format' }, observation: obs });
    const db = await store.open(), tx = db.transaction('operations', 'readwrite');
    const read = tx.objectStore('operations').get(operation.operationId);
    read.onsuccess = () => { const old = read.result; delete old.changes.date; tx.objectStore('operations').put(old); };
    await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); });
    return (await store.listOperations()).find(row => row.operationId === operation.operationId);
  }, observation);
  expect(legacy.changes).not.toHaveProperty('date');
  await page.evaluate(({ obs, operation }) => PhotoOffline.open(obs, operation), { obs: observation, operation: legacy });
  await expect(dialog.locator('[name=date]')).toHaveValue(today);
});

test('date null : édition du libellé et redémarrage complet ne la remplacent pas', async ({ baseURL }) => {
  const profile = mkdtempSync(join(tmpdir(), 'photo-offline-null-date-'));
  const dbName = randomUUID(), observation = { id: randomUUID(), version: 1, designation: 'Observation terrain' };
  let context;
  try {
    context = await chromium.launchPersistentContext(profile, { baseURL, headless: true });
    let page = await context.newPage();
    await loadForm(page, dbName);
    const original = await page.evaluate(async obs => OfflineRuntime.store.enqueuePhotoCreate({
      operationId: crypto.randomUUID(), changes: { id: crypto.randomUUID(), observation_id: obs.id,
        date: null, libelle: 'Avant édition' }, observation: obs,
    }), observation);
    await page.evaluate(({ obs, operation }) => PhotoOffline.open(obs, operation), { obs: observation, operation: original });
    const dialog = page.locator('#photo-offline-dialog');
    await expect(dialog.locator('[name=date]')).toHaveValue('');
    await dialog.locator('[data-close]').click();
    await page.evaluate(({ obs, operation }) => PhotoOffline.open(obs, operation), { obs: observation, operation: original });
    await expect(dialog.locator('[name=date]')).toHaveValue('');
    await dialog.locator('[name=libelle]').fill('Après édition');
    await dialog.locator('[type=submit]').click();
    await expect(dialog.locator('[data-role=status]')).toContainText('enregistrée localement');
    const edited = await page.evaluate(async id => (await OfflineRuntime.store.listOperations()).find(row => row.resourceId === id), original.resourceId);
    expect(edited.changes).toMatchObject({ date: null, libelle: 'Après édition' });
    expect(edited.resourceId).toBe(original.resourceId);
    expect(edited.operationId).not.toBe(original.operationId);
    await context.close(); context = null;

    context = await chromium.launchPersistentContext(profile, { baseURL, headless: true });
    page = await context.newPage();
    await loadForm(page, dbName);
    const restored = await page.evaluate(async id => (await OfflineRuntime.store.listOperations()).find(row => row.resourceId === id), original.resourceId);
    expect(restored.changes.date).toBeNull();
    expect(restored.operationId).toBe(edited.operationId);
    await page.evaluate(({ obs, operation }) => PhotoOffline.open(obs, operation), { obs: observation, operation: restored });
    await expect(page.locator('#photo-offline-dialog [name=date]')).toHaveValue('');
  } finally {
    if (context) await context.close();
    rmSync(profile, { recursive: true, force: true });
  }
});
