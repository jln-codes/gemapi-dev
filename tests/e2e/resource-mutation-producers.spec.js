const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require("@playwright/test");
const { mockResourceJournal } = require('./mock-resource-journal');

function feature(id, geometry, properties = {}) {
  return {
    type: "Feature",
    properties: { id, version: 1, valid: true, ...properties },
    geometry,
  };
}

function collection(features) {
  return { type: "FeatureCollection", features };
}

test("les producteurs Carte et Base publient une fois et conservent les brouillons distants", async ({ context }) => {
  const troncon = feature("t12", {
    type: "LineString", coordinates: [[2, 46], [2.02, 46.02]],
  }, { libelle: "Tronçon 12", digue_libelle: "Digue 1" });
  const disorder = feature("d42", {
    type: "Point", coordinates: [2.01, 46.01],
  }, {
    designation: "Initial", type_geometrie: "Point", troncon_ids: ["t12"],
  });
  const heritage = {
    systemes: [{ id: "s1", libelle: "SE 1", valid: true, digues: [{
      id: "d1", libelle: "Digue 1", valid: true,
      troncons: [{ id: "t12", libelle: "Tronçon 12", valid: true }],
    }] }],
  };
  const requests = { desordreReads: 0, tronconReads: 0, dataQueries: 0 };

  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    const method = route.request().method();
    let response = {};
    if (url.pathname === "/api/config") response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (url.pathname === "/api/systemes-endiguement") response = heritage;
    else if (url.pathname === "/api/troncons") response = collection([troncon]);
    else if (url.pathname === "/api/troncons/t12") {
      if (method === "PUT") troncon.properties.libelle = "Tronçon modifié";
      requests.tronconReads += 1;
      response = troncon;
    } else if (url.pathname === "/api/desordres") response = collection([disorder]);
    else if (url.pathname === "/api/desordres/d42") {
      if (method === "PUT") {
        const payload = JSON.parse(route.request().postData() || "{}");
        disorder.properties = { ...disorder.properties, ...payload };
      }
      requests.desordreReads += 1;
      response = disorder;
    } else if (url.pathname === "/api/territoire-administratif") response = collection([]);
    else if (url.pathname === "/api/troncons/options") {
      response = { troncons: [{ id: "t12", libelle: "Tronçon 12", valid: true }] };
    } else if (url.pathname === '/api/data/view-catalog') {
      response = {
        default_root: 'desordres',
        default_view: { root: 'desordres', fields: [
          { table: 'troncons', field: 'libelle' }, { table: 'desordres', field: 'designation' },
        ] },
        roots: ['troncons', 'desordres'].map(id => ({ id, label: id })),
        relations: [{ parent: 'troncons', child: 'desordres' }],
        tables: [['troncons', 'libelle'], ['desordres', 'designation']].map(([id, field]) => ({
          id, label: id, compatible_roots: ['troncons', 'desordres'],
          fields: [{ id: field, label: field, type: 'text', source_class: id,
            source_field: field, navigable: true }],
        })),
      };
    } else if (url.pathname === "/api/data/view-query") {
      requests.dataQueries += 1;
      response = { columns: [
        { key: 'troncons.libelle', label: 'Tronçon', source_class: 'troncons', source_field: 'libelle', navigable: true },
        { key: 'desordres.designation', label: 'Désordre', source_class: 'desordres', source_field: 'designation', navigable: true },
      ], rows: [{ values: [troncon.properties.libelle, disorder.properties.designation],
        entities: [{ class: 'troncons', id: 't12', attributes: troncon.properties },
          { class: 'desordres', id: 'd42', attributes: disorder.properties }] }],
      total: 1, offset: 0, has_next: false };
    } else if (url.pathname === "/api/referentiels/types-desordre") response = { types: [] };
    else if (url.pathname === "/api/referentiels/categories-desordre") response = { categories: [] };
    else if (url.pathname.startsWith("/api/referentiels/")) response = { references: [] };
    await route.fulfill({ json: response });
  });

  await context.addInitScript(() => {
    const original = BroadcastChannel.prototype.postMessage;
    window.__resourceMutations = [];
    BroadcastChannel.prototype.postMessage = function (message) {
      if (message?.type === "resource-mutated") window.__resourceMutations.push(message);
      return original.call(this, message);
    };
  });

  const [mapA, mapB, baseC, baseD] = await Promise.all([
    context.newPage(), context.newPage(), context.newPage(), context.newPage(),
  ]);
  await Promise.all([mapA.goto("/carte"), mapB.goto("/carte"), baseC.goto("/base"), baseD.goto("/base")]);
  await Promise.all([
    mapA.locator("#status").waitFor(), mapB.locator("#status").waitFor(),
    baseC.locator("#status").waitFor(), baseD.locator("#status").waitFor(),
  ]);

  for (const page of [mapA, mapB, baseC, baseD]) {
    await page.locator(".settings-menu-toggle").click();
    await page.locator("#sync-heritage-selection").uncheck();
    await page.locator("#open-interface-in-new-tab").uncheck();
  }
  // Statistics is intentionally passive. Exercise the current structured Vues
  // consumer explicitly, rather than the removed automatic home query.
  for (const page of [baseC, baseD]) {
    await page.locator('#toggle-views').click();
    await expect(page.locator('#data-view-result-body [data-entity-id="d42"]')).toHaveText('Initial');
  }

  await Promise.all([
    mapA.locator(".leaflet-marker-icon").click(),
    mapB.locator(".leaflet-marker-icon").click(),
  ]);
  await expect(mapA.locator("#editor-panel")).toBeVisible();
  await expect(mapB.locator("#editor-panel")).toBeVisible();
  await mapB.locator("#desordre-create-designation").fill("Brouillon Carte B");
  await mapA.locator("#desordre-create-designation").fill("Modification Carte A");
  await mapA.locator("#submit-desordre-create").click();

  await expect(mapB.locator("#desordre-create-designation")).toHaveValue("Brouillon Carte B");
  await expect(mapB.locator("#desordre-create-message")).toContainText("modifié ailleurs");
  await expect.poll(() => requests.dataQueries).toBeGreaterThan(2);
  await expect.poll(() => requests.desordreReads).toBeGreaterThan(2);
  for (const page of [baseC, baseD]) {
    await expect(page.locator('#data-view-result-body [data-entity-id="d42"]')).toHaveText('Modification Carte A');
  }
  expect(await mapA.evaluate(() => window.__resourceMutations.length)).toBe(1);
  expect(await mapB.evaluate(() => window.__resourceMutations.length)).toBe(0);
  expect(await baseC.evaluate(() => window.__resourceMutations.length)).toBe(0);
  expect(await baseD.evaluate(() => window.__resourceMutations.length)).toBe(0);

  mapB.once("dialog", dialog => dialog.accept());
  const closeDisorder = page => page.locator('#disorder-form-view > .entity-form__header [data-role=close]');
  await Promise.all([closeDisorder(mapA).click(), closeDisorder(mapB).click()]);
  await baseC.evaluate(() => {
    document.querySelector("main").dispatchEvent(new CustomEvent("digues-app:open-entity", {
      detail: { entityClass: "troncons", entityId: "t12", entities: [], source: "home" },
    }));
  });
  await expect(baseC.locator("#data-entity-detail > .entity-form > .entity-form__header .entity-form__title")).toContainText("Tronçon 12");
  await baseC.locator("#data-entity-detail-edit").click();
  await baseC.locator("#data-entity-detail .entity-form__fields input").first().fill("Tronçon modifié");
  await baseC.locator("#data-entity-detail .entity-form__actions [data-role=save]").click();

  await expect.poll(() => requests.tronconReads).toBeGreaterThan(3);
  await expect.poll(() => requests.dataQueries).toBeGreaterThan(4);
  expect(await baseC.evaluate(() => window.__resourceMutations.length)).toBe(1);
  expect(await mapA.evaluate(() => window.__resourceMutations.length)).toBe(1);
  expect(await mapB.evaluate(() => window.__resourceMutations.length)).toBe(0);
  expect(await baseD.evaluate(() => window.__resourceMutations.length)).toBe(0);
});
