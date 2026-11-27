const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require("@playwright/test");
const { mockResourceJournal } = require('./mock-resource-journal');

function feature(id, geometry, properties = {}) {
  return {
    type: "Feature",
    properties: { id, valid: true, ...properties },
    geometry,
  };
}

function collection(features) {
  return { type: "FeatureCollection", features };
}

async function publishRemoteMutation(page, resource, action, ids) {
  await page.evaluate(({ resourceName, mutationAction, mutationIds }) => {
    const channel = new BroadcastChannel("digues-app:inter-page");
    channel.postMessage({
      type: "resource-mutated",
      source: "data",
      payload: {
        schemaVersion: 1,
        eventId: crypto.randomUUID(), originClientId: "remote-client",
        resource: resourceName,
        action: mutationAction,
        ids: mutationIds,
        originInstanceId: "remote-data-instance",
        occurredAt: Date.now(),
      },
    });
    channel.close();
  }, { resourceName: resource, mutationAction: action, mutationIds: ids });
}

test("la Carte invalide ses couches, caches et contexte sans rechargement", async ({ context }) => {
  const troncon = feature("T12", {
    type: "LineString", coordinates: [[2.0, 46.0], [2.02, 46.02]],
  }, { libelle: "Tronçon 12", digue_libelle: "Digue 1" });
  let disorders = [feature("D42", {
    type: "Point", coordinates: [2.01, 46.01],
  }, {
    designation: "Avant", version: 1, type_desordre_libelle: "Affaissement",
    type_geometrie: "Point", troncon_ids: ["T12"],
    dernier_niveau_urgence_id: "RefUrgence:4",
  })];
  let territory = collection([]);
  const requests = { disorder: 0, observation: 0, options: 0, territory: 0 };
  let holdNextDetail = false, notifyDetailRequest, releaseDetailResponse;
  let holdNextCollection = false, notifyCollectionRequest, releaseCollectionResponse;

  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    let response = {};
    if (url.pathname === "/api/config") response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (url.pathname === "/api/referentiels/types-desordre") {
      response = { types: [] };
    } else if (url.pathname === "/api/referentiels/categories-desordre") {
      response = { categories: [] };
    } else if (url.pathname.startsWith("/api/referentiels/")) {
      response = { references: [] };
    }
    else if (url.pathname === "/api/systemes-endiguement") response = {
      systemes: [{
        id: "S1", libelle: "SE 1", valid: true,
        digues: [{
          id: "DG1", libelle: "Digue 1", valid: true,
          troncons: [{ id: "T12", libelle: "Tronçon 12", valid: true }],
        }],
      }],
    };
    else if (url.pathname === "/api/troncons") response = collection([troncon]);
    else if (url.pathname === "/api/troncons/T12") response = troncon;
    else if (url.pathname === "/api/troncons/options") {
      requests.options += 1;
      response = { troncons: [{ id: "T12", libelle: "Tronçon 12", valid: true }] };
    } else if (url.pathname === "/api/desordres") {
      response = collection(disorders);
      if (holdNextCollection) {
        holdNextCollection = false;
        notifyCollectionRequest();
        await new Promise((resolve) => { releaseCollectionResponse = resolve; });
      }
    }
    else if (/^\/api\/desordres\/[^/]+$/.test(url.pathname)) {
      const id = decodeURIComponent(url.pathname.slice("/api/desordres/".length));
      if (id === "D42") requests.disorder += 1;
      response = disorders.find((item) => item.properties.id === id);
      if (!response) {
        await route.fulfill({ status: 404, json: { detail: "Désordre supprimé" } });
        return;
      }
      if (id === "D42" && holdNextDetail) {
        holdNextDetail = false;
        notifyDetailRequest();
        await new Promise((resolve) => { releaseDetailResponse = resolve; });
      }
    } else if (url.pathname === "/api/observations/O1") {
      requests.observation += 1;
      response = { id: "O1", parent: { resource: "desordres", id: "D42" } };
    } else if (url.pathname === "/api/territoire-administratif") {
      requests.territory += 1;
      response = territory;
    }
    await route.fulfill({ json: response });
  });

  await context.addInitScript(() => {
    const original = BroadcastChannel.prototype.postMessage;
    window.__resourceMutationPublications = 0;
    BroadcastChannel.prototype.postMessage = function (message) {
      if (message?.type === "resource-mutated" && message.source === "map") {
        window.__resourceMutationPublications += 1;
      }
      return original.call(this, message);
    };
  });

  const page = await context.newPage();
  await page.goto("/carte");
  await expect(page.locator("#status")).toContainText("1 tronçon · 1 désordre");
  await expect(page.locator(".leaflet-marker-icon")).toHaveCount(1);

  disorders = [feature("D42", {
    type: "LineString", coordinates: [[2.005, 46.005], [2.025, 46.025]],
  }, {
    designation: "Après", version: 2, type_desordre_libelle: "Érosion",
    type_geometrie: "LineString", troncon_ids: ["T12"],
    dernier_niveau_urgence_id: "RefUrgence:3",
  })];
  await publishRemoteMutation(page, "desordres", "update", ["D42"]);
  await expect.poll(() => requests.disorder).toBe(1);
  await expect(page.locator(".leaflet-marker-icon")).toHaveCount(0);
  let disorderLine = page.locator('path.leaflet-interactive[stroke="#c45100"]');
  await expect(disorderLine).toHaveCount(1);
  disorders[0].properties.dernier_niveau_urgence_id = "RefUrgence:4";
  await publishRemoteMutation(page, "observations", "update", ["O1"]);
  await expect.poll(() => requests.observation).toBe(1);
  await expect.poll(() => requests.disorder).toBe(2);
  disorderLine = page.locator('path.leaflet-interactive[stroke="#b91c1c"]');
  await expect(disorderLine).toHaveCount(1);
  disorders[0].properties.dernier_niveau_urgence_id = null;
  await publishRemoteMutation(page, "observations", "update", ["O1"]);
  await expect.poll(() => requests.observation).toBe(2);
  await expect.poll(() => requests.disorder).toBe(3);
  disorderLine = page.locator('path.leaflet-interactive[stroke="#475569"]');
  await expect(disorderLine).toHaveCount(1);
  await disorderLine.click({ force: true });
  await expect(page.locator(".leaflet-popup-content")).toContainText("Après");
  await expect.poll(() => requests.options).toBe(1);
  await expect(page.locator("#editor-panel")).toBeVisible();
  await page.locator("#desordre-create-designation").fill("Brouillon local");
  disorders[0].properties.designation = "Distant pendant édition";
  const detailRequestsBeforeDraftMutation = requests.disorder;
  await publishRemoteMutation(page, "desordres", "update", ["D42"]);
  await expect(page.locator("#desordre-create-designation")).toHaveValue("Brouillon local");
  await expect(page.locator("#desordre-create-message")).toContainText(
    "modifié ailleurs",
  );
  expect(requests.disorder).toBe(detailRequestsBeforeDraftMutation);
  await page.locator("#cancel-desordre-create").click();
  await expect.poll(() => requests.disorder).toBe(detailRequestsBeforeDraftMutation + 1);
  await expect(page.locator("#desordre-create-designation")).toHaveValue(
    "Distant pendant édition",
  );
  await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
  holdNextDetail = true;
  const detailRequestStarted = new Promise((resolve) => { notifyDetailRequest = resolve; });
  await disorderLine.click({ force: true });
  await detailRequestStarted;
  await expect(page.locator("#editor-panel")).toBeVisible();
  expect(await page.evaluate(() => getMapEditorState())).toMatchObject({
    open: true, resource: "desordres", id: "D42", editing: true,
  });
  releaseDetailResponse();
  await expect.poll(() => page.evaluate(() => getMapEditorState())).toMatchObject({
    open: true, resource: "desordres", id: "D42", editing: false, baseVersion: 2,
  });
  await expect(page.locator("#desordre-create-designation")).toHaveValue("Distant pendant édition");
  // Leaflet removes the previous popup after its fade-out transition.
  await expect(page.locator(".leaflet-popup-content")).toHaveCount(1);
  await expect(page.locator(".leaflet-popup-content")).toContainText(
    "Distant pendant édition",
  );

  disorders.push(feature("D43", {
    type: "Point", coordinates: [2.015, 46.015],
  }, {
    designation: "Nouveau", version: 1, type_geometrie: "Point", troncon_ids: ["T12"],
  }));
  await publishRemoteMutation(page, "desordres", "create", null);
  await expect(page.locator("#status")).toContainText("1 tronçon · 2 désordres");
  await expect.poll(() => page.evaluate(() => getMapEditorState())).toMatchObject({
    open: true, resource: "desordres", id: "D42", editing: false, baseVersion: 2,
  });
  expect(await page.evaluate(() => desordreAttributeDraftModified())).toBe(false);
  holdNextCollection = true;
  const collectionRequestStarted = new Promise((resolve) => { notifyCollectionRequest = resolve; });
  await publishRemoteMutation(page, "desordres", "create", null);
  await collectionRequestStarted;
  // This response was captured before deletion; a later authoritative GET must differ.
  disorders = disorders.filter((item) => item.properties.id !== "D42");
  await publishRemoteMutation(page, "desordres", "delete", ["D42"]);
  await expect(page.locator("#status")).toContainText("1 tronçon · 1 désordre");
  const staleCollectionResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/desordres"
      && response.request().method() === "GET",
  );
  releaseCollectionResponse();
  const staleCollection = await (await staleCollectionResponse).json();
  expect(staleCollection.features.map((item) => item.properties.id)).toEqual(["D42", "D43"]);
  await expect(page.locator("#status")).toContainText("1 tronçon · 1 désordre");
  const afterDelete = await page.evaluate(async () => ({
    collection: await (await fetch('/api/desordres')).json(),
    detailStatus: (await fetch('/api/desordres/D42')).status,
  }));
  expect(afterDelete.collection.features.map((item) => item.properties.id)).toEqual(["D43"]);
  expect(afterDelete.detailStatus).toBe(404);

  // A genuinely changed form must retain its draft when its object is deleted remotely.
  await page.locator(".leaflet-marker-icon").click();
  await expect.poll(() => page.evaluate(() => getMapEditorState())).toMatchObject({
    open: true, resource: "desordres", id: "D43", editing: false, baseVersion: 1,
  });
  await page.locator("#desordre-create-designation").fill("Brouillon non enregistré");
  expect(await page.evaluate(() => getMapEditorState())).toMatchObject({
    id: "D43", editing: true,
  });
  disorders = [];
  await publishRemoteMutation(page, "desordres", "delete", ["D43"]);
  await expect(page.locator("#desordre-create-designation")).toHaveValue("Brouillon non enregistré");
  await expect.poll(() => page.evaluate(() => getMapEditorState().stale)).toBe(true);
  await expect(page.locator("#status")).toContainText("1 tronçon · 1 désordre");
  await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
  await expect(page.locator("#status")).toContainText("1 tronçon · 0 désordre");

  await publishRemoteMutation(page, "troncons", "update", ["T12"]);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await expect.poll(() => requests.options).toBe(2);
  await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();

  await page.locator(".settings-menu-toggle").click();
  await page.locator("#sync-heritage-selection").uncheck();
  await page.locator("#toggle-heritage").click();
  await page.locator(".tree-toggle").nth(0).click();
  await page.locator(".tree-toggle").nth(1).click();
  const tronconVisibility = page.locator('.tree-name[data-object-id="T12"]')
    .locator("xpath=..").locator(".tree-visibility");
  await tronconVisibility.uncheck();
  await expect(page.locator("#status")).toContainText("0 tronçons · 0 désordres");
  await publishRemoteMutation(page, "troncons", "update", ["T12"]);
  await expect(tronconVisibility).not.toBeChecked();
  await expect(page.locator("#status")).toContainText("0 tronçons · 0 désordres");

  const viewportUrl = page.url();
  territory = collection([feature("territory", {
    type: "Polygon",
    coordinates: [[[1.9, 45.9], [2.2, 45.9], [2.2, 46.2], [1.9, 45.9]]],
  })]);
  const initialTerritoryRequests = requests.territory;
  await publishRemoteMutation(
    page, "territoires-administratifs", "update", null,
  );
  await expect.poll(() => requests.territory).toBe(initialTerritoryRequests + 1);
  expect(page.url()).toBe(viewportUrl);
  expect(await page.evaluate(() => window.__resourceMutationPublications)).toBe(0);
});

test("les observations invalidées sont relues sans course ni double soumission", async ({ context }) => {
  const troncon = feature("T1", {
    type: "LineString", coordinates: [[2, 46], [2.02, 46.02]],
  }, { libelle: "Tronçon" });
  const disorder = feature("D1", {
    type: "Point", coordinates: [2.01, 46.01],
  }, { designation: "Désordre", type_geometrie: "Point", troncon_ids: ["T1"] });
  let listVersion = "Ancienne liste";
  let listRequests = 0;
  let observationReads = 0;
  let observationVersion = 1;
  let observationSuite = "";
  let putRequests = 0;
  let holdOldList = false;
  let releaseOldList;
  let releasePut;

  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    const method = route.request().method();
    let response = {};
    if (url.pathname === "/api/config") response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (url.pathname === "/api/systemes-endiguement") response = { systemes: [] };
    else if (url.pathname === "/api/referentiels/types-desordre") response = { types: [] };
    else if (url.pathname === "/api/referentiels/categories-desordre") response = { categories: [] };
    else if (url.pathname === "/api/troncons") response = collection([troncon]);
    else if (url.pathname === "/api/desordres") response = collection([disorder]);
    else if (url.pathname === "/api/desordres/D1") response = disorder;
    else if (url.pathname === "/api/territoire-administratif") response = collection([]);
    else if (url.pathname === "/api/desordres/D1/observations") {
      listRequests += 1;
      const captured = listVersion;
      if (holdOldList) {
        holdOldList = false;
        await new Promise((resolve) => { releaseOldList = resolve; });
      }
      response = { parent: { resource: "desordres", id: "D1", designation: "Désordre" }, observations: [{
        id: "O1", parent: { resource: "desordres", id: "D1", designation: "Désordre" },
        designation: captured, suite: "", photos: [],
      }] };
    } else if (url.pathname === "/api/observations/O1" && method === "PUT") {
      putRequests += 1;
      await new Promise((resolve) => { releasePut = resolve; });
      observationVersion = 2; observationSuite = "Sauvegardée";
      response = { id: "O1", version: 2, parent: { resource: "desordres", id: "D1", designation: "Désordre" }, designation: "Observation",
        suite: "Sauvegardée", photos: [], valid: true };
    } else if (url.pathname === "/api/observations/O1") {
      observationReads += 1;
      response = { id: "O1", version: observationVersion, parent: { resource: "desordres", id: "D1", designation: "Désordre" }, designation: "Observation",
        suite: observationSuite, photos: [], valid: true };
    } else if (url.pathname === "/api/referentiels/suites-apporter") response = { references: [] };
    else if (url.pathname === "/api/troncons/options") response = { troncons: [] };
    else if (url.pathname.startsWith("/api/referentiels/")) response = { references: [] };
    await route.fulfill({ json: response });
  });

  await context.addInitScript(() => {
    const original = BroadcastChannel.prototype.postMessage;
    window.__observationMutationPublications = 0;
    BroadcastChannel.prototype.postMessage = function (message) {
      if (message?.type === "resource-mutated" && message.source === "map"
          && message.payload?.resource === "observations") {
        window.__observationMutationPublications += 1;
      }
      return original.call(this, message);
    };
  });

  const page = await context.newPage();
  await page.goto("/carte");
  await page.locator(".leaflet-marker-icon").click();
  await page.locator("#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]").click();
  await expect(page.locator(".observation-row")).toContainText("Ancienne liste");
  await page.locator(".observation-row").click();
  await expect(page.locator("#observation-suite-complement")).toBeVisible();

  listVersion = "Liste distante";
  await publishRemoteMutation(page, "observations", "update", ["O1"]);
  await expect.poll(() => observationReads).toBe(3);
  await page.locator("#back-to-observations").click();
  await expect(page.locator(".observation-row")).toContainText("Liste distante");
  expect(listRequests).toBe(2);

  listVersion = "Réponse ancienne";
  holdOldList = true;
  await page.evaluate(() => { window.__oldList = MapResourceConsumers.loadObservations({ force: true }); });
  await expect.poll(() => listRequests).toBe(3);
  listVersion = "Réponse récente";
  await page.evaluate(() => { window.__newList = MapResourceConsumers.loadObservations({ force: true }); });
  await expect.poll(() => listRequests).toBe(4);
  await expect(page.locator(".observation-row")).toContainText("Réponse récente");
  releaseOldList();
  await page.evaluate(() => Promise.all([window.__oldList, window.__newList]));
  await expect(page.locator(".observation-row")).toContainText("Réponse récente");

  await page.locator(".observation-row").click();
  await page.locator("#observation-suite-complement").fill("Sauvegardée");
  await page.evaluate(() => {
    const form = document.querySelector("#observation-suite-form");
    form.requestSubmit();
    form.requestSubmit();
  });
  await expect.poll(() => putRequests).toBe(1);
  expect(await page.locator('#observation-suite-form [type="submit"]').isDisabled()).toBe(true);
  releasePut();
  // Background invalidation can clear feedback after the explicit reread.
  // Verify the authoritative version and content that remain in the editor.
  await expect.poll(() => page.evaluate(() => currentObservation?.version)).toBe(2);
  await expect(page.locator("#observation-suite-complement")).toHaveValue("Sauvegardée");
  expect(putRequests).toBe(1);
  expect(await page.evaluate(() => window.__observationMutationPublications)).toBe(1);
  await expect(page.locator('#observation-suite-form [type="submit"]')).toBeEnabled();
});

test("seul le dernier reload territoire applique ses effets", async ({ context }) => {
  let territoryRequests = 0;
  let holdFirstReload = false;
  let releaseFirstReload;
  let nextTerritory = collection([]);
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    let response = {};
    if (url.pathname === "/api/config") response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (url.pathname === "/api/systemes-endiguement") response = { systemes: [] };
    else if (url.pathname === "/api/troncons" || url.pathname === "/api/desordres") {
      response = collection([]);
    } else if (url.pathname === "/api/territoire-administratif") {
      territoryRequests += 1;
      const captured = structuredClone(nextTerritory);
      if (holdFirstReload) {
        holdFirstReload = false;
        await new Promise((resolve) => { releaseFirstReload = resolve; });
      }
      response = captured;
    }
    await route.fulfill({ json: response });
  });
  const page = await context.newPage();
  await page.goto("/carte");
  await page.waitForFunction(() => Boolean(window.MapResourceConsumers));
  await expect.poll(() => territoryRequests).toBe(1);
  nextTerritory = collection([feature("territoire-1", {
    type: "Polygon", coordinates: [[[1.8, 45.8], [2.2, 45.8], [2.2, 46.2], [1.8, 45.8]]],
  })]);
  holdFirstReload = true;
  await page.evaluate(() => { window.__territory1 = MapResourceConsumers.reloadTerritoryContext(); });
  await expect.poll(() => territoryRequests).toBe(2);
  nextTerritory = collection([feature("territoire-2", {
    type: "Polygon", coordinates: [[[1.9, 45.9], [2.1, 45.9], [2.1, 46.1], [1.9, 45.9]]],
  })]);
  await page.evaluate(() => { window.__territory2 = MapResourceConsumers.reloadTerritoryContext(); });
  await expect.poll(async () => page.evaluate(
    () => MapResourceConsumers.currentTerritory()?.properties?.id,
  )).toBe("territoire-2");
  releaseFirstReload();
  await page.evaluate(() => Promise.all([window.__territory1, window.__territory2]));
  expect(await page.evaluate(
    () => MapResourceConsumers.currentTerritory()?.properties?.id,
  )).toBe("territoire-2");
});
