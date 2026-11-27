const { test, expect } = require('@playwright/test');

test.skip(process.env.DIGUES_E2E_OCC !== '1', 'Requires disposable PostGIS');

test('SSE entre profils navigateur, BC dédupliqué, reprise et brouillon concurrent', async ({ browser, request, baseURL }) => {
  test.setTimeout(90_000);
  const created = await request.post('/api/desordres', { data: {
    designation: 'LOT7 initial', geometry: { type: 'Point', coordinates: [2.01, 46.01] },
  }});
  expect(created.status()).toBe(201);
  const id = (await created.json()).properties.id;
  const a = await browser.newContext({ baseURL });
  const b = await browser.newContext({ baseURL });
  const errors = [];
  try {
    for (const context of [a, b]) await context.addInitScript(() => {
      localStorage.setItem('digues-app:heritage-selection-sync-enabled', 'false');
      window.__sent = [];
      const post = BroadcastChannel.prototype.postMessage;
      BroadcastChannel.prototype.postMessage = function (message) {
        if (message.type === 'resource-mutated') window.__sent.push(message.payload);
        return post.call(this, message);
      };
    });
    const map = await a.newPage(), sameBrowser = await a.newPage();
    const base = await b.newPage(), manual = await b.newPage();
    const pages = [map, sameBrowser, base, manual];
    for (const page of pages) page.on('pageerror', e => errors.push(e.message));
    await Promise.all(pages.map((p, i) => p.goto(i === 0 ? '/carte' : '/base')));
    await map.waitForFunction(id => typeof desordreLayersById !== 'undefined' && desordreLayersById.has(id), id);
    const viewport = await map.evaluate(() => ({ center: map.getCenter(), zoom: map.getZoom() }));
    for (const page of pages.slice(1)) {
      await page.evaluate(id => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
        detail: { entityClass: 'desordres', entityId: id, source: 'home', entities: [] },
      })), id);
      await expect(page.locator('#data-entity-detail-edit')).toBeVisible();
      await page.evaluate(() => {
        window.__received = [];
        ResourceMutationController.registerInvalidator('open-detail', e => window.__received.push(e));
      });
    }
    const clientIds = await Promise.all(pages.map(p => p.evaluate(() => ResourceMutationController.getClientId())));
    expect(clientIds[0]).toBe(clientIds[1]);
    expect(clientIds[2]).toBe(clientIds[3]);
    expect(clientIds[0]).not.toBe(clientIds[2]);
    await base.locator('#data-entity-detail-edit').click();
    await base.locator('[name="designation"]').fill('Brouillon navigateur B');
    await map.evaluate(id => openPointEditor(id, desordreLayersById.get(id)), id);
    await map.locator('#desordre-create-designation').fill('LOT7 serveur');
    const saved = map.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith(id));
    await map.locator('#submit-desordre-create').click();
    const response = await saved;
    expect(response.status()).toBe(200);
    const receipt = (await response.json()).mutation;
    for (const page of pages.slice(1)) {
      await expect.poll(() => page.evaluate(() => window.__received.map(e => e.eventId))).toEqual([receipt.eventId]);
      expect(await page.evaluate(() => window.__sent)).toEqual([]);
    }
    expect(await map.evaluate(() => window.__sent[0])).toEqual(receipt);
    // The same-browser Base has both channels, but still one invalidation.
    const savedCursor = (await (await request.get('/api/resource-events/head')).json()).cursor;
    await expect.poll(() => sameBrowser.evaluate(cursor =>
      BigInt(ResourceEventTransport.getCursor()) >= BigInt(cursor), savedCursor)).toBe(true);
    expect(await sameBrowser.evaluate(() => window.__received.map(e => e.eventId))).toEqual([receipt.eventId]);
    const conflict = base.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith(id));
    await base.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
    expect((await conflict).status()).toBe(409);
    await expect(base.locator('[name="designation"]')).toHaveValue('Brouillon navigateur B');
    expect(await map.evaluate(() => ({ center: map.getCenter(), zoom: map.getZoom() }))).toEqual(viewport);

    // Deliberate interruption: another browser receives only durable replay later.
    await manual.evaluate(() => ResourceEventTransport.stop());
    const update = await request.put(`/api/desordres/${id}`, {
      headers: { 'If-Match': `"${receipt.versions[id]}"` }, data: { designation: 'Pendant interruption' },
    });
    expect(update.status()).toBe(200);
    const second = (await update.json()).mutation;
    expect(await manual.evaluate(() => window.__received.length)).toBe(1);
    await manual.evaluate(() => ResourceEventTransport.start());
    await expect.poll(() => manual.evaluate(() => window.__received.map(e => e.eventId)))
      .toEqual([receipt.eventId, second.eventId]);
    await expect(manual.locator('#data-entity-detail')).toContainText('Pendant interruption');
    expect(await manual.evaluate(() => window.__sent)).toEqual([]);
    expect(errors).toEqual([]);
  } finally { await a.close(); await b.close(); }
});

test('mutation entre lecture HTTP initiale et démarrage SSE : rattrapage sans trou', async ({ browser, request, baseURL }) => {
  test.setTimeout(60_000);
  const created = await request.post('/api/desordres', { data: {
    designation: 'Bootstrap ancien', geometry: { type: 'Point', coordinates: [2.01, 46.01] },
  }});
  const initial = await created.json(), id = initial.properties.id;
  const context = await browser.newContext({ baseURL });
  try {
    let mutated = false;
    await context.route('**/api/desordres', async route => {
      const response = await route.fetch();
      const staleCollection = await response.json();
      if (!mutated) {
        mutated = true;
        const updated = await request.put(`/api/desordres/${id}`, {
          headers: { 'If-Match': `"${initial.properties.version}"` }, data: { designation: 'Bootstrap récent' },
        });
        expect(updated.status()).toBe(200);
      }
      await route.fulfill({ json: staleCollection });
    });
    const map = await context.newPage();
    await map.goto('/carte');
    await expect.poll(() => map.evaluate(id => typeof desordreLayersById === 'undefined' ? null
      : desordreLayersById.get(id)?.feature.properties.designation, id)).toBe('Bootstrap récent');
    expect(mutated).toBe(true);
  } finally { await context.close(); }
});
