const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require("@playwright/test");
const { mockResourceJournal } = require("./mock-resource-journal");

const heritage = {
  systemes: [{ id: "s1", libelle: "SE 1", valid: true, digues: [{
    id: "d1", libelle: "Digue 1", valid: true,
    troncons: [{ id: "t1", libelle: "Tronçon 1", valid: true }],
  }] }],
};

async function mockApplication(context, counters) {
  // These UI contracts do not depend on external basemap availability.
  await context.route(/^https:\/\//, route => route.abort());
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    let response = {};
    if (url.pathname === "/api/config") response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (url.pathname === "/api/systemes-endiguement") response = heritage;
    else if (["/api/troncons", "/api/desordres", "/api/territoire-administratif"].includes(url.pathname)) {
      response = { type: "FeatureCollection", features: [] };
    }
    else if (url.pathname === "/api/data/desordres/query") {
      response = { columns: [], rows: [], total: 0, offset: 0, has_next: false };
    } else if (url.pathname === "/api/offline-scope") {
      counters.preparations += 1;
      response = { schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: "0", tronconIds: ["t1"], records: [] };
    }
    await route.fulfill({ json: response });
  });
}

async function openOfflineTool(page) {
  await page.waitForFunction(() => typeof topNavigation !== "undefined"
    && topNavigation.getState().tools === false);
  await page.locator("#toggle-tools-menu").click();
  await page.getByRole("menuitem", { name: "Consultation hors ligne" }).click();
  await expect(page.locator("#offline-consultation-modal")).toBeVisible();
}

test("Outils ouvre la modale offline, restaure le focus et Paramètres ne la duplique plus", async ({ context, page }) => {
  test.setTimeout(60_000);
  const counters = { preparations: 0 };
  await mockApplication(context, counters);
  await page.goto("/base");
  await page.waitForFunction(() => window.OfflineRuntime && window.OfflineOperationsRuntime);
  await expect(page.locator("#toggle-tools-menu")).toBeVisible();
  await expect(page.locator("#offline-consultation-modal")).toBeHidden();
  await page.locator('#toggle-tools-menu').click();
  await page.getByRole('menuitem', { name: 'Synchronisation locale', exact: true }).click();
  await expect(page.locator("#offline-consultation-modal")).toBeVisible();
  await page.locator("#close-offline-consultation-modal").click();
  await page.locator(".settings-menu-toggle").click();
  await expect(page.locator(".settings-menu-list")).not.toContainText("Consultation hors ligne");
  await page.goto("/carte");
  await page.waitForFunction(() => window.OfflineRuntime && window.OfflineOperationsRuntime);

  await openOfflineTool(page);
  await expect(page.locator("#close-offline-consultation-modal")).toBeFocused();
  await expect(page.locator("#offline-status")).toHaveText("Aucun périmètre préparé");
  await page.keyboard.press("Escape");
  await expect(page.locator("#offline-consultation-modal")).toBeHidden();
  await expect(page.locator("#toggle-tools-menu")).toBeFocused();

  await openOfflineTool(page);
  await page.locator("#offline-consultation-modal").click({ position: { x: 2, y: 2 } });
  await expect(page.locator("#offline-consultation-modal")).toBeHidden();

  await openOfflineTool(page);
  await page.locator("#close-offline-consultation-modal").click();
  await expect(page.locator("#offline-consultation-modal")).toBeHidden();

  await page.setViewportSize({ width: 390, height: 640 });
  await openOfflineTool(page);
  const box = await page.locator(".offline-consultation-dialog").boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.y + box.height).toBeLessThanOrEqual(640);
  await page.keyboard.press("Escape");

});

test("la modale appelle la préparation, l'effacement et le moteur de synchronisation existants", async ({ context, page }) => {
  const counters = { preparations: 0 };
  await mockApplication(context, counters);
  await page.goto("/carte");
  await page.waitForFunction(() => window.OfflineRuntime && window.OfflineOperationsRuntime);
  await page.waitForFunction(() => typeof topNavigation !== "undefined"
    && topNavigation.getState().heritage === false);
  await page.locator("#toggle-heritage").click();
  await expect(page.locator("#heritage-loading")).toContainText("1 système(s) chargé(s)");
  await page.locator("#toggle-heritage").click();
  await openOfflineTool(page);

  await page.locator("#prepare-offline").click();
  await expect.poll(() => counters.preparations).toBe(1);
  await expect(page.locator("#offline-status")).toContainText("Périmètre conservé");

  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#clear-offline").click();
  await expect(page.locator("#offline-status")).toHaveText("Périmètre local supprimé.");

  await page.evaluate(() => {
    const synchronize = OfflineOperationsRuntime.synchronize.bind(OfflineOperationsRuntime);
    window.__manualSynchronizations = 0;
    OfflineOperationsRuntime.synchronize = async (...args) => {
      window.__manualSynchronizations += 1;
      return synchronize(...args);
    };
  });
  await page.getByRole("button", { name: "Synchroniser maintenant" }).click();
  await expect.poll(() => page.evaluate(() => window.__manualSynchronizations)).toBe(1);
});

test("la modale relit le périmètre et affiche pending, syncing, conflict et failed", async ({ context, page }) => {
  const counters = { preparations: 0 };
  await mockApplication(context, counters);
  await page.goto("/carte");
  await page.waitForFunction(() => window.OfflineRuntime && window.OfflineOperationsRuntime);
  await page.evaluate(async () => {
    OfflineOperationsRuntime.synchronize = async () => {};
    const records = ["pending", "syncing", "conflict", "failed"].map((status, index) => ({
      status,
      row: {
        resource: "desordres", id: `x${index}`, serverVersion: 1,
        payload: { type: "Feature", geometry: null, properties: { id: `x${index}`, version: 1 } },
      },
    }));
    await OfflineRuntime.store.replace({
      schemaVersion: 3, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, reperageReferences: { schemaVersion: 2, crs: { authority: 'EPSG', srid: 3950, code: 'EPSG:3950' }, systems: [] }, observationReferences: { suitesApporter: [], urgences: [] }, cursor: "0", tronconIds: ["t1"],
      records: records.map(({ row }) => row),
    });
    for (const [index, value] of records.entries()) {
      const operation = await OfflineRuntime.store.enqueue({
        resource: "desordres", resourceId: value.row.id, baseVersion: 1,
        changes: { designation: `État ${value.status}` },
      });
      if (value.status !== "pending") {
        await OfflineRuntime.store.updateOperation(operation.operationId, { status: value.status });
      }
    }
  });

  await openOfflineTool(page);
  await expect(page.locator("#offline-status")).toContainText("1 tronçon(s), 4 objet(s)");
  for (const state of ["pending", "syncing", "conflict", "failed"]) {
    await expect(page.locator(`[data-count="${state}"]`)).toHaveText("1");
  }
});
