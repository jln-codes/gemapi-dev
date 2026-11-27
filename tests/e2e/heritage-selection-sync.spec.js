const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require("@playwright/test");
const { mockResourceJournal } = require('./mock-resource-journal');
const { heritage } = require('./fixtures/frontend-preview-data');

async function mockFrontendApis(context) {
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    const emptyGeoJson = { type: "FeatureCollection", features: [] };
    const responses = {
      "/api/config": { legacy_single_database_enabled: true, show_uuid: false },
      "/api/systemes-endiguement": heritage,
      // La sélection attend la vraie fiche depuis UX-FORM-3B/Patrimoine AH.
      // La synchronisation de visibilité ne doit pas simuler une fiche vide.
      "/api/digues/d1": { id: 'd1', version: 1, libelle: 'Digue 1', valid: true,
        systeme_endiguement_id: 's1', systeme_endiguement_libelle: 'SE 1', troncons_unifies: [] },
      "/api/troncons": emptyGeoJson,
      "/api/desordres": emptyGeoJson,
      "/api/territoire-administratif": emptyGeoJson,
      "/api/data/desordres/query": {
        columns: [], rows: [], total: 0, offset: 0, has_next: false,
      },
    };
    await route.fulfill({ json: responses[url.pathname] || {} });
  });
}

function visibility(page, id) {
  return page.locator(`.tree-name[data-object-id="${id}"]`)
    .locator("xpath=..").locator(".tree-visibility");
}

async function expandHeritage(page) {
  await page.locator(".tree-toggle").nth(0).click();
  await page.locator(".tree-toggle").nth(1).click();
}

test("l’activation depuis Carte impose son état divergent sans rebouclage", async ({ context }) => {
  test.setTimeout(60_000);
  await mockFrontendApis(context);
  await context.addInitScript(() => {
    const postMessage = BroadcastChannel.prototype.postMessage;
    window.__heritagePublications = [];
    BroadcastChannel.prototype.postMessage = function (message) {
      if (message?.type === "heritage-selection-changed") {
        window.__heritagePublications.push(message);
      }
      return postMessage.call(this, message);
    };
  });

  const mapPage = await context.newPage();
  const dataPage = await context.newPage();
  await Promise.all([mapPage.goto("/carte"), dataPage.goto("/base")]);
  await mapPage.waitForFunction(() => typeof heritagePanelController !== "undefined" && heritagePanelController !== null);
  await mapPage.locator("#toggle-heritage").click();
  await expect(mapPage.locator(".tree-toggle").first()).toBeVisible();
  await dataPage.locator("#toggle-heritage").click();
  await expect(dataPage.locator(".tree-toggle").first()).toBeVisible();
  await expect(mapPage.locator("#heritage-selection-summary")).toContainText("2 tronçon(s) visible(s) sur 2 : T1, T2");
  await expect(dataPage.locator("#heritage-selection-summary")).toContainText("2 tronçon(s) visible(s) sur 2 : T1, T2");
  await Promise.all([expandHeritage(mapPage), expandHeritage(dataPage)]);
  await mapPage.locator('.tree-name[data-object-id="d1"]').click();
  await expect(mapPage.locator("#heritage-selection-summary")).toContainText("Digue consulté : Digue 1");
  await expect(visibility(mapPage, "t1")).toBeChecked();
  await expect(visibility(dataPage, "t2")).toBeChecked();

  // La synchronisation est active par défaut dans les deux sens.
  await visibility(mapPage, "t1").uncheck();
  await expect(visibility(dataPage, "t1")).not.toBeChecked();
  await expect(mapPage.locator("#heritage-selection-summary")).toContainText("1 tronçon(s) visible(s) sur 2 : T2");
  await expect(dataPage.locator("#heritage-selection-summary")).toContainText("1 tronçon(s) visible(s) sur 2 : T2");
  await visibility(mapPage, "t1").check();
  await expect(visibility(dataPage, "t1")).toBeChecked();
  await visibility(dataPage, "t2").uncheck();
  await expect(visibility(mapPage, "t2")).not.toBeChecked();
  await visibility(dataPage, "t2").check();
  await expect(visibility(mapPage, "t2")).toBeChecked();
  await Promise.all([
    mapPage.evaluate(() => { window.__heritagePublications = []; }),
    dataPage.evaluate(() => { window.__heritagePublications = []; }),
  ]);

  await mapPage.locator(".settings-menu-toggle").click();
  const mapSetting = mapPage.locator("#sync-heritage-selection");
  const mapNewTabSetting = mapPage.locator("#open-interface-in-new-tab");
  await expect(mapNewTabSetting).toBeChecked();
  await mapNewTabSetting.uncheck();
  await expect(dataPage.locator("#open-interface-in-new-tab")).not.toBeChecked();

  // Le réglage d’ouverture n’altère pas la synchronisation Patrimoine active.
  await visibility(mapPage, "t1").uncheck();
  await expect(visibility(dataPage, "t1")).not.toBeChecked();
  await visibility(mapPage, "t1").check();
  await expect(visibility(dataPage, "t1")).toBeChecked();
  await Promise.all([
    mapPage.evaluate(() => { window.__heritagePublications = []; }),
    dataPage.evaluate(() => { window.__heritagePublications = []; }),
  ]);

  await expect(mapSetting).toBeChecked();
  await mapSetting.uncheck();
  await expect(dataPage.locator("#sync-heritage-selection")).not.toBeChecked();

  await visibility(mapPage, "t1").uncheck();
  await visibility(dataPage, "t2").uncheck();
  await expect(visibility(mapPage, "t2")).toBeChecked();
  await expect(visibility(dataPage, "t1")).toBeChecked();

  // Même avec l’ouverture dans un nouvel onglet réactivée, les états divergent
  // tant que la synchronisation Patrimoine reste désactivée.
  await mapPage.locator(".settings-menu").evaluate((details) => { details.open = true; });
  await mapNewTabSetting.check();
  await expect(dataPage.locator("#open-interface-in-new-tab")).toBeChecked();
  await expect(visibility(mapPage, "t2")).toBeChecked();
  await expect(visibility(dataPage, "t1")).toBeChecked();
});
