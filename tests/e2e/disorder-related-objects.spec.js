const { test, expect } = require("@playwright/test");
const { mockBusinessCRS } = require("./mock-business-crs");
const { mockResourceJournal } = require("./mock-resource-journal");

test.use({ serviceWorkers: "block" });

const DISORDER = "00000000-0000-4000-8000-000000000951";
const TRONCON = "00000000-0000-4000-8000-000000000952";
const MOBILIER = "00000000-0000-4000-8000-000000000953";
const VEGETATION = "00000000-0000-4000-8000-000000000954";

function feature(id, geometry, properties = {}) {
  return { type: "Feature", id, geometry, properties: { id, valid: true, version: 1, ...properties } };
}

const troncon = feature(TRONCON, {
  type: "LineString", coordinates: [[2, 46], [2.02, 46.02]],
}, { libelle: "Tronçon associé", designation: "Tronçon associé" });
const disorder = feature(DISORDER, {
  type: "Point", coordinates: [2.01, 46.01],
}, { designation: "Désordre inverse", type_geometrie: "Point", troncon_ids: [TRONCON] });
const mobilier = feature(MOBILIER, {
  type: "Point", coordinates: [2.011, 46.011],
}, { designation: "Mobilier associé", type_id: "TYPE", desordre_ids: [DISORDER] });

async function fixture(context) {
  let inverseCalls = 0;
  let mobilierLabel = "Mobilier associé";
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (path === "/api/config") return send({ legacy_single_database_enabled: true, show_uuid: false });
    if (path === "/api/systemes-endiguement") return send({ systemes: [{
      id: "00000000-0000-4000-8000-000000000955", libelle: "SE", valid: true,
      digues: [{ id: "00000000-0000-4000-8000-000000000956", libelle: "Digue", valid: true,
        troncons: [{ id: TRONCON, libelle: "Tronçon associé", valid: true }] }],
    }] });
    if (path === "/api/troncons") return send({ type: "FeatureCollection", features: [troncon] });
    if (path === `/api/troncons/${TRONCON}`) return send(troncon);
    if (path === "/api/troncons/options") return send({ troncons: [
      { id: TRONCON, libelle: "Tronçon associé", valid: true },
    ] });
    if (path === "/api/desordres") return send({ type: "FeatureCollection", features: [disorder] });
    if (path === `/api/desordres/${DISORDER}`) return send(disorder);
    if (path === `/api/desordres/${DISORDER}/objets-associes`) {
      inverseCalls += 1;
      if (url.searchParams.has("cursor")) return send({ items: [{
        resource: "vegetation", category: "Végétation", id: VEGETATION,
        label: "Végétation associée", valid: true,
      }], next_cursor: null, has_more: false });
      return send({ items: [
        { resource: "troncons", category: "Tronçons", id: TRONCON,
          label: "Tronçon associé", valid: true },
        { resource: "mobilier", category: "Mobilier", id: MOBILIER,
          label: mobilierLabel, valid: false },
      ], next_cursor: "suite", has_more: true });
    }
    if (path === `/api/mobilier/${MOBILIER}`) {
      return send({ ...mobilier, properties: { ...mobilier.properties, designation: mobilierLabel } });
    }
    if (path === "/api/desordres/selection") return send({ items: [{
      id: DISORDER, label: "Désordre inverse", valid: true,
    }], missing_ids: [] });
    if (path === "/api/data/desordres/query") return send({
      columns: [], rows: [], total: 0, offset: 0, has_next: false,
    });
    if (path === "/api/data/view-catalog") return send({ default_root: "desordres", roots: [], relations: [], tables: [] });
    if (path === "/api/territoire-administratif") return send({ type: "FeatureCollection", features: [] });
    if (path.startsWith("/api/referentiels/")) return send({ references: [
      { id: "TYPE", libelle: "Type", valid: true },
    ], types: [], categories: [] });
    const collections = [
      "/api/mobilier", "/api/equipements-mesure", "/api/reseaux-techniques",
      "/api/ouvrages-particuliers", "/api/cheminements", "/api/vegetation",
      "/api/amenagements-hydrauliques", "/api/prestations",
    ];
    if (collections.includes(path)) return send({ type: "FeatureCollection", features: [] });
    return send({});
  });
  return {
    calls: () => inverseCalls,
    rename(value) { mobilierLabel = value; },
  };
}

async function openBaseDisorder(page) {
  await page.goto("/base");
  await expect(page.locator("main")).toHaveAttribute("data-central-view", "statistics");
  await page.evaluate(({ disorderId, tronconId }) => {
    document.querySelector("main").dispatchEvent(new CustomEvent("digues-app:open-entity", {
      detail: { entityClass: "desordres", entityId: disorderId, source: "views",
        entities: [{ class: "troncons", id: tronconId, attributes: { libelle: "Tronçon associé" } }] },
    }));
  }, { disorderId: DISORDER, tronconId: TRONCON });
  await expect(page.locator("#data-entity-detail-title")).toContainText("Désordre inverse");
}

test("Base regroupe, pagine, navigue et revient au Désordre", async ({ context, page }) => {
  const state = await fixture(context);
  await openBaseDisorder(page);
  const inverse = page.locator(".disorder-related-objects");
  await expect(inverse.getByRole("heading", { name: "Objets associés" })).toBeVisible();
  // The authoritative attachment is rendered once, outside the inverse list.
  await expect(inverse.getByRole("heading", { name: "Tronçons" })).toHaveCount(0);
  await expect(page.locator('#data-entity-detail-read [data-relation-resource="troncons"]')).toHaveCount(1);
  await expect(page.locator('#data-entity-detail-read [data-relation-resource="troncons"]')).toHaveText('Tronçon associé');
  await expect(inverse.getByRole("heading", { name: "Mobilier" })).toBeVisible();
  await expect(inverse.getByText(MOBILIER, { exact: true })).toHaveCount(1);
  await expect(inverse.getByText(MOBILIER, { exact: true })).toBeHidden();
  await inverse.getByRole("button", { name: "Afficher la suite" }).click();
  await expect(inverse.getByRole("heading", { name: "Végétation" })).toBeVisible();
  await expect(inverse.getByText("Mobilier associé", { exact: true })).toBeVisible();
  const before = state.calls();
  state.rename("Mobilier actualisé dans Base");
  await page.evaluate((id) => {
    const channel = new BroadcastChannel("digues-app:inter-page");
    channel.postMessage({ type: "resource-mutated", source: "map", payload: {
      schemaVersion: 1, eventId: crypto.randomUUID(), resource: "mobilier",
      action: "update", ids: [id], occurredAt: Date.now(),
      originClientId: "remote", originInstanceId: "remote-instance",
    } });
    channel.close();
  }, MOBILIER);
  await expect.poll(() => state.calls()).toBeGreaterThan(before);
  await expect(inverse.getByText("Mobilier actualisé dans Base", { exact: true })).toBeVisible();
  await inverse.getByRole("button", { name: /Ouvrir Mobilier actualisé dans Base/ }).click();
  await expect(page.locator("#data-entity-detail-title")).toContainText("Mobilier actualisé dans Base");
  await page.locator("#data-entity-detail-back").dispatchEvent("click");
  await expect(page.locator("#data-entity-detail-title")).toContainText("Désordre inverse");
  await expect(page.locator(".disorder-related-objects")).toBeVisible();
  await expect(inverse.getByRole("button", { name: /Modifier.*association/ })).toHaveCount(0);
});

test("Carte affiche la projection dans le panneau étroit et l’invalide", async ({ context, page }) => {
  const state = await fixture(context);
  await page.setViewportSize({ width: 699, height: 844 });
  await page.goto("/carte");
  await page.waitForFunction((id) => typeof desordreLayersById !== "undefined"
    && desordreLayersById.get(id), DISORDER);
  await page.evaluate((id) => {
    const layer = desordreLayersById.get(id);
    layer.fire("click");
  }, DISORDER);
  const inverse = page.locator("#desordre-associated-objects");
  await expect(inverse).toBeVisible();
  // Le même desordres__troncons est déjà présenté par le sélecteur multiple.
  await expect(inverse.getByText("Tronçon associé", { exact: true })).toHaveCount(0);
  // En édition, le parent du Désordre est verrouillé et le contrôle peut être
  // réduit à une sélection unique. Vérifier l'identité sans imposer le DOM.
  expect(await page.locator('#desordre-create-troncons').evaluate(select =>
    [...select.selectedOptions].map(option => option.value))).toEqual([TRONCON]);
  await expect(page.locator('#desordre-create-troncons').locator('..').locator('[data-role=open-relation]')).toHaveCount(0);
  await expect(inverse.getByText("Mobilier associé", { exact: true })).toBeVisible();
  const before = state.calls();
  state.rename("Mobilier actualisé");
  await page.evaluate((id) => {
    const channel = new BroadcastChannel("digues-app:inter-page");
    channel.postMessage({ type: "resource-mutated", source: "data", payload: {
      schemaVersion: 1, eventId: crypto.randomUUID(), resource: "mobilier",
      action: "update", ids: [id], occurredAt: Date.now(),
      originClientId: "remote", originInstanceId: "remote-instance",
    } });
    channel.close();
  }, MOBILIER);
  await expect.poll(() => state.calls()).toBeGreaterThan(before);
  await expect(inverse.getByText("Mobilier actualisé", { exact: true })).toBeVisible();
  await expect(inverse.locator("input, select, textarea")).toHaveCount(0);
});
