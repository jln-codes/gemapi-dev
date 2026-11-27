const { test, expect } = require("@playwright/test");

function livePages(context) {
  return context.pages().filter((page) => !page.isClosed());
}

async function chooseOtherInterface(page) {
  await page.waitForFunction(() => Boolean(window.InterfaceCoordination));
  await page.locator(".view-switcher").click();
}

test("le mode nouvel onglet réutilise les contextes Carte et Base nommés", async ({
  page: mapPage, context,
}) => {
  test.setTimeout(60_000);
  await mapPage.goto("/carte");
  await expect(mapPage.locator(".view-switcher")).toHaveAttribute("aria-label", "Base");
  await expect(mapPage.locator("#open-interface-in-new-tab")).toBeChecked();
  const toggle = mapPage.locator(".view-toggle");
  await expect(toggle.locator(".view-toggle-segment")).toHaveCount(2);
  await expect(toggle.locator("svg")).toHaveCount(2);
  const size = await toggle.boundingBox();
  expect(size.width).toBeGreaterThanOrEqual(68);
  expect(size.width).toBeLessThanOrEqual(72);
  expect(size.height).toBeGreaterThanOrEqual(34);
  expect(size.height).toBeLessThanOrEqual(36);
  const activeMap = toggle.getByRole("button", { name: "Carte" });
  await expect(activeMap).toHaveAttribute("aria-current", "page");
  await activeMap.focus();
  await mapPage.keyboard.press("Enter");
  await expect(mapPage).toHaveURL(/\/carte(?:\?|$)/);
  expect(livePages(context)).toHaveLength(1);
  await expect(activeMap).toHaveCSS("background-color", "rgb(22, 90, 74)");
  await expect(toggle.getByRole("link", { name: "Base" })).toHaveAttribute("title", "Ouvrir Base de données");

  const openedBasePromise = context.waitForEvent("page");
  await mapPage.locator(".view-switcher").focus();
  await mapPage.keyboard.press("Enter");
  const basePage = await openedBasePromise;
  await basePage.waitForLoadState();
  await expect(mapPage).toHaveURL(/\/carte(?:\?|$)/);
  await expect(basePage).toHaveURL(/\/base$/);
  await expect(basePage.locator('.view-toggle [aria-current="page"]')).toHaveAttribute("aria-label", "Base");
  expect(await mapPage.evaluate(() => window.name)).toBe("digues-app-map");
  expect(await basePage.evaluate(() => window.name)).toBe("digues-app-data");
  expect(livePages(context)).toHaveLength(2);

  await chooseOtherInterface(basePage);
  await expect(mapPage).toHaveURL(/\/carte(?:\?|$)/);
  expect(livePages(context)).toHaveLength(2);

  await chooseOtherInterface(mapPage);
  await expect(basePage).toHaveURL(/\/base$/);
  expect(livePages(context)).toHaveLength(2);
});

test("le mode onglet courant conserve la navigation native dans les deux sens", async ({
  page, context,
}) => {
  await page.goto("/carte");
  await page.locator(".settings-menu-toggle").click();
  await page.locator("#open-interface-in-new-tab").uncheck();
  await chooseOtherInterface(page);
  await expect(page).toHaveURL(/\/base$/);
  expect(livePages(context)).toHaveLength(1);

  await page.locator(".settings-menu-toggle").click();
  await expect(page.locator("#open-interface-in-new-tab")).not.toBeChecked();
  await chooseOtherInterface(page);
  await expect(page).toHaveURL(/\/carte(?:\?|$)/);
  expect(livePages(context)).toHaveLength(1);
});

test("un onglet Base manuel reste synchronisé sans devenir la cible nommée", async ({
  page: mapPage, context,
}) => {
  test.setTimeout(60_000);
  await mapPage.goto("/carte");
  const openedBasePromise = context.waitForEvent("page");
  await chooseOtherInterface(mapPage);
  const namedBase = await openedBasePromise;
  await namedBase.waitForLoadState();
  await expect(namedBase).toHaveURL(/\/base$/);
  await namedBase.waitForFunction(() => Boolean(window.InterfaceCoordination));

  // Exercise an independently opened tab. Chromium headless can consume a
  // trusted, unprevented Ctrl+click without creating a page (see RC audit).
  // Modifier pass-through is covered by the coordination unit tests.
  const manualURL = await mapPage.locator(".view-switcher").evaluate(link => link.href);
  const manualBase = await context.newPage();
  await manualBase.goto(manualURL);
  await manualBase.waitForFunction(() => Boolean(window.InterfaceCoordination));
  expect(await namedBase.evaluate(() => window.name)).toBe("digues-app-data");
  expect(await manualBase.evaluate(() => window.name)).toBe("");
  expect(livePages(context)).toHaveLength(3);

  for (const target of [namedBase, manualBase]) {
    await target.evaluate(() => {
      window.__resourceMutationProbe = 0;
      InterPageBus.subscribe("resource-mutated", () => {
        window.__resourceMutationProbe += 1;
      });
    });
  }
  await mapPage.evaluate(() => InterPageBus.publish("resource-mutated", {
    schemaVersion: 1,
    eventId: crypto.randomUUID(), originClientId: "probe-client",
    resource: "probe-resource",
    action: "update",
    ids: ["probe-1"],
    originInstanceId: "map-probe",
    occurredAt: Date.now(),
  }));
  await expect.poll(() => namedBase.evaluate(() => window.__resourceMutationProbe)).toBe(1);
  await expect.poll(() => manualBase.evaluate(() => window.__resourceMutationProbe)).toBe(1);

  await chooseOtherInterface(mapPage);
  await expect(namedBase).toHaveURL(/\/base$/);
  expect(await namedBase.evaluate(() => window.name)).toBe("digues-app-data");
  expect(await manualBase.evaluate(() => window.name)).toBe("");
  expect(livePages(context)).toHaveLength(3);
});
