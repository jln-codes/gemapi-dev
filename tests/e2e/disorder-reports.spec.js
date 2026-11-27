const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require("@playwright/test");
const { mockResourceJournal } = require('./mock-resource-journal');

const heritage = {
  systemes: [{
    id: "s1", libelle: "SE 1", valid: true,
    digues: [{
      id: "d1", libelle: "Digue 1", valid: true,
      troncons: [
        { id: "t1", libelle: "T1", valid: true },
        { id: "t2", libelle: "T2", valid: true },
      ],
    }],
  }],
};

const SELECTORS = {
  systeme: '.tree-name[data-object-kind="Système d\'endiguement"]',
  digue: '.tree-name[data-object-kind="Digue"]',
  troncon: '.tree-name[data-object-kind="Tronçon"]',
};

const ENDPOINTS = {
  systeme: "/api/systemes-endiguement/s1/desordres/catalogue",
  digue: "/api/digues/d1/desordres/catalogue",
  troncon: "/api/troncons/t1/desordres/catalogue",
};

async function mockFrontendApis(context, catalogueRequests) {
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/desordres/catalogue")) {
      catalogueRequests.push(url.pathname);
      return route.fulfill({
        json: { entry_count: 3, digue_count: 1, troncon_count: 1 },
      });
    }
    if (url.pathname.endsWith("/desordres/catalogue/pdf")) {
      catalogueRequests.push(`${url.pathname} [pdf]`);
      return route.fulfill({
        status: 200,
        contentType: "application/pdf",
        body: "%PDF-1.4\n%%EOF",
      });
    }
    const emptyGeoJson = { type: "FeatureCollection", features: [] };
    const responses = {
      "/api/config": { legacy_single_database_enabled: true, show_uuid: false },
      "/api/systemes-endiguement": heritage,
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

async function reveal(page, kind) {
  if (kind === "systeme") return;
  await page.locator(".tree-toggle").nth(0).click();
  if (kind === "digue") return;
  await page.locator(".tree-toggle").nth(1).click();
}

async function openReady(page, path) {
  await page.goto(path);
  // Authentication/configuration gates load the business scripts after HTML.
  // A visible header alone does not mean its map listeners are installed yet.
  if (path === "/carte") {
    await page.waitForFunction(() => Boolean(window.MapResourceConsumers));
  } else {
    // Base preloads the tree; Carte loads it lazily when the panel opens.
    await page.locator(".tree-name").first().waitFor({ state: "attached" });
  }
}

const NO_SELECTION =
  "Sélectionnez un système d'endiguement, une digue ou un tronçon dans Patrimoine.";

for (const [path, label] of [["/carte", "Carte"], ["/base", "Base"]]) {
  for (const kind of ["systeme", "digue", "troncon"]) {
    test(`${label} : le nœud Patrimoine « ${kind} » détermine l'endpoint du catalogue`, async ({ context }) => {
      const requests = [];
      await mockFrontendApis(context, requests);
      const page = await context.newPage();
      await openReady(page, path);
      await page.locator("#toggle-heritage").click();
      await page.locator(".tree-name").first().waitFor();
      await reveal(page, kind);
      await page.locator(SELECTORS[kind]).first().click();
      await expect(page.locator(".tree-name.selected")).toHaveAttribute(
        "data-object-kind", { systeme: "Système d'endiguement", digue: "Digue", troncon: "Tronçon" }[kind],
      );
      await page.locator("#toggle-tools-menu").click();
      await page.locator('[data-tool-action="disorder-catalogue"]').click();
      await expect.poll(() => requests[0] || null).toBe(ENDPOINTS[kind]);
    });
  }

  test(`${label} : sans nœud sélectionné, le catalogue explique sans appeler`, async ({ context }) => {
    const requests = [];
    await mockFrontendApis(context, requests);
    const page = await context.newPage();
    await openReady(page, path);
    await page.locator("#toggle-tools-menu").click();
    await page.locator('[data-tool-action="disorder-catalogue"]').click();
    await expect(page.locator("#status")).toHaveText(NO_SELECTION);
    expect(requests).toEqual([]);
  });

  test(`${label} : les cases de visibilité ne définissent pas le catalogue`, async ({ context }) => {
    const requests = [];
    await mockFrontendApis(context, requests);
    const page = await context.newPage();
    await openReady(page, path);
    await page.locator("#toggle-heritage").click();
    await page.locator(".tree-visibility").first().waitFor();
    await page.locator(".tree-visibility").first().click();
    await page.locator("#toggle-tools-menu").click();
    await page.locator('[data-tool-action="disorder-catalogue"]').click();
    await expect(page.locator("#status")).toHaveText(NO_SELECTION);
    expect(requests).toEqual([]);
  });
}
