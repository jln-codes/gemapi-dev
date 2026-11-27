"use strict";

// UX-LOCAL-1, niveau B : vrais contrôleurs Carte/Base sur serveur HTTP local
// synthétique. Aucun stockage métier n'est modifié ; les écritures sont refusées.
// Exécuter avec DIGUES_E2E_BASE_URL=http://127.0.0.1:8001,
// DIGUES_E2E_GUARDED_HARNESS=true et DIGUES_FRONTEND_PREVIEW=true (~2 min).
const { expect, test } = require("@playwright/test");
const { mkdirSync } = require("node:fs");
const { fakeId, troncons, bornageTroncons } = require("./fixtures/frontend-preview-data.js");

test.use({ serviceWorkers: "block" });
const EVIDENCE = "audits/ux-local-1/captures";
const LIT = fakeId(20);
const MORPH = fakeId(30);
const INVALID_MORPH = fakeId(31);
const referencePayloads = require("./fixtures/frontend-preview-references.json").payloads;

async function exactOptions(select, rows, { disorder = false } = {}) {
  await expect(select.locator('option[value]:not([value=""])')).toHaveCount(rows.length);
  expect(await select.locator('option[value]:not([value=""])').evaluateAll(options =>
    options.map(option => ({ id: option.value, label: option.textContent })))).toEqual(
    rows.map(row => ({ id: row.id, label: disorder && row.abrege ? `${row.libelle} — ${row.abrege}` : row.libelle })),
  );
}

// UX-LOCAL-REF-FIX, niveau B : réponses HTTP réelles de preview, contrôleurs
// réels, nouvelles pages sans interception ; aucun enregistrement (~30 s).
test("Référentiels Désordre : listes complètes, catégories et sélection historique", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await preview(page);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  for (const [field, slug, selected] of [
    ["cote", "cotes", "RefCote:99"], ["position", "positions", "RefPosition:99"],
    ["source", "sources", "RefSource:0"],
  ]) {
    const select = page.locator(`#desordre-create-${field}`);
    await exactOptions(select, referencePayloads[slug].references);
    await select.selectOption(selected);
    await expect(select).toHaveValue(selected);
    await expect(select.locator('option[value=""]')).toHaveCount(1);
  }
  await expect(page.locator('#desordre-create-cote option[value="RefCote:99"]')).toHaveText("Indéfini");
  const types = page.locator("#desordre-create-type-reference");
  await exactOptions(types, referencePayloads["types-desordre"].types, { disorder: true });
  const category = page.locator("#desordre-create-categorie-reference");
  await exactOptions(category, referencePayloads["categories-desordre"].categories, { disorder: true });
  await category.selectOption("RefCategorieDesordre:3");
  const subset = referencePayloads["types-desordre"].types.filter(row => row.categorie_id === "RefCategorieDesordre:3");
  await exactOptions(types, subset, { disorder: true });
  await types.selectOption(subset[0].id);
  await expect(category).toHaveValue("RefCategorieDesordre:3");
  await capture(page, "referentiels-desordre-complets");

  await preview(page);
  // Désordre opens through the map layer (it is not an EntityNavigation class).
  await page.evaluate(id => {
    map.eachLayer(layer => {
      if (layer.feature?.properties.id === id) layer.fire("click");
    });
  }, fakeId(10));
  await expect(page.locator("#desordre-create-cote")).toHaveValue("preview-inactive-cote");
  await expect(page.locator('#desordre-create-cote option:checked')).toHaveText("Côté historique fictif (actuelle)");
  expect(errors).toEqual([]);
});

test("Référentiels Tronçon : type/typologie exacts, ouverture et fermeture sans faux changement", async ({ page }) => {
  const errors = [], dialogs = [], writes = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => { dialogs.push(dialog.message()); dialog.dismiss(); });
  page.on("request", request => { if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) writes.push(request.url()); });
  await preview(page);
  let initial;
  for (let pass = 0; pass < 2; pass += 1) {
    await page.locator("#toggle-create-menu").click();
    await page.locator('[data-create-type="troncon"]').click();
    await exactOptions(page.locator("#heritage-object-type-troncon"), referencePayloads["types-troncon"].references);
    await exactOptions(page.locator("#heritage-object-typologie-troncon"), referencePayloads["typologies-troncon"].references);
    const state = await page.locator("#heritage-object-type-troncon").evaluate(element => [...new FormData(element.form)]);
    if (!pass) initial = state;
    else expect(state).toEqual(initial);
    await expect(page.locator("#heritage-object-type-troncon")).toHaveValue("");
    await expect(page.locator("#heritage-object-typologie-troncon")).toHaveValue("");
    await capture(page, "referentiels-troncon-complets");
    await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
    await expect(page.locator("#editor-panel")).toBeHidden();
  }
  expect(dialogs).toEqual([]);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("Référentiels autres formulaires : Végétation courante, ouvrages et longues listes", async ({ context }) => {
  const errors = [];
  for (const [type, fields] of [
    ["vegetation", [["problematique_ids", "problematiques-vegetation"], ["espece_id", "especes-vegetation"],
      ["type_cote_id", "cotes"], ["type_position_id", "positions"]]],
    ["prestation", [["type_prestation_id", "types-prestation"]]],
    ["ouvrage-particulier", [["type_id", "types-ouvrage-particulier"]]],
    ["cheminement", [["materiau_id", "materiaux-cheminement"], ["revetement_id", "revetements-cheminement"]]],
  ]) {
    const page = await context.newPage();
    page.on("pageerror", error => errors.push(`${type}: ${error.message}`));
    await preview(page);
    await page.locator("#toggle-create-menu").click();
    await page.locator(`[data-create-type="${type}"]`).click();
    for (const [field, slug] of fields) {
      await exactOptions(page.locator(`#business-editor-content [name="${field}"]`), referencePayloads[slug].references);
    }
    if (type === "vegetation") {
      await expect(page.locator('#business-editor-content [name="invasif"]')).toBeVisible();
      await expect(page.locator('#business-editor-content [name="problematique_ids"] option[value="INV"]')).toHaveCount(0);
    }
    await capture(page, `referentiels-${type}`);
    await page.close();
  }
  expect(errors).toEqual([]);
});

async function capture(page, name) {
  mkdirSync(EVIDENCE, { recursive: true });
  await page.screenshot({ path: `${EVIDENCE}/${name}.png`, fullPage: false });
}
async function preview(page) {
  const response = await page.goto("/carte");
  expect(response.ok()).toBeTruthy();
  await expect(page.locator("#frontend-preview-marker")).toContainText("session fictive");
  // The marker is static HTML; wait for business bootstrap and controllers,
  // otherwise the first menu click can precede their event handlers.
  await expect(page.locator("#status")).toContainText(`${troncons.features.length + bornageTroncons.length} tronçons · 1 désordre`);
}
async function openCreate(page, type) {
  await page.locator("#toggle-create-menu").click();
  await page.locator(`[data-create-type="${type}"]`).click();
  const panel = page.locator(`#${type}-editor-panel`);
  await expect(panel).toBeVisible();
  await expect(panel.locator('[name="designation"]')).toBeEditable();
  await expect(panel.locator('[data-role="status"]')).toBeEmpty();
  return panel;
}
async function openExisting(page, type, id) {
  await page.goto(`/carte?entity=${type === "troncons-lit" ? "troncons_lit" : "elements_morphologiques_lit"}&id=${id}`);
  const panel = page.locator(`#${type}-editor-panel`);
  await expect(panel).toBeVisible();
  await expect(panel.locator('[data-role="status"]')).toContainText('État relu');
  return panel;
}
async function selectOnlyTable(page, table) {
  await expect(page.locator("#status")).toHaveText("Backend disponible");
  await page.locator("#toggle-views").click();
  await expect(page.locator("#data-view-builder-workspace")).toBeVisible();
  while (await page.locator("#data-view-field-list button").count()) {
    await page.locator("#data-view-field-list button").first().click();
  }
  await page.locator("#data-view-table").selectOption(table);
  await page.locator("#data-view-add-field").click();
  await expect(page.locator("#data-view-result-body .data-entity-link").first()).toBeVisible();
}
async function actionInViewport(page, locator, height) {
  await locator.scrollIntoViewIfNeeded();
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  expect(box).toBeTruthy();
  expect(box.y).toBeGreaterThanOrEqual(-1);
  expect(box.y + box.height).toBeLessThanOrEqual(height + 1);
}
async function noHorizontalOverflow(page) {
  const dimensions = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1);
}

// FORM-LOCALISATION-2, niveau B (~30 s) : rendu réel de la prévisualisation,
// dessins locaux et saisies, sans écriture. Les mutations sont vérifiées sur
// PostgreSQL jetable par form-regression-live.spec.js.
for (const size of [{ width: 1280, height: 900 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
  test(`Localisation compacte en prévisualisation ${size.width}×${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    if (size.width !== 1280) await page.addInitScript(() => {
      const match = window.matchMedia.bind(window);
      window.matchMedia = query => query === '(display-mode: standalone)'
        ? { matches: true, media: query, addEventListener() {}, removeEventListener() {} } : match(query);
    });
    await preview(page);
    const view = await openCreate(page, 'vegetation');
    const dates = view.locator('.entity-form__date-range');
    const dateStart = dates.locator('[name=date_debut]'), dateEnd = dates.locator('[name=date_fin]');
    await dateStart.fill('2026-01-02'); await dateEnd.fill('2026-12-31');
    const rects = await Promise.all([dateStart.boundingBox(), dateEnd.boundingBox()]);
    expect(Math.abs(rects[0].y - rects[1].y)).toBeLessThan(1);
    expect(rects[1].x).toBeGreaterThan(rects[0].x + rects[0].width);
    await dates.screenshot({ path: test.info().outputPath('dates.png') });

    await view.locator('[name=geometry_type]').selectOption('LineString');
    await view.locator('[data-role=draw]').click();
    const mapBounds = await page.locator('#map').boundingBox();
    for (const [x, y] of [[.4, .3], [.5, .45]]) await page.locator('#map').click({
      position: { x: mapBounds.width * x, y: mapBounds.height * y },
    });
    const finish = page.locator('#finish-editor-map-interaction');
    const cancelDrawing = page.locator('#cancel-editor-map-interaction');
    await expect(finish).toBeVisible(); await expect(cancelDrawing).toBeVisible();
    const buttons = await Promise.all([finish.boundingBox(), cancelDrawing.boundingBox()]);
    expect(Math.abs(buttons[0].y - buttons[1].y)).toBeLessThan(1);
    await page.locator('#editor-map-interaction').screenshot({ path: test.info().outputPath('dessin.png') });
    await finish.click();
    const draw = view.locator('[data-role=draw]');
    await expect(draw).toBeVisible();
    await expect(view.locator('[data-role=restore]')).toHaveCount(0);

    const modes = view.getByRole('group', { name: 'Mode de localisation' });
    for (const mode of ['X/Y', 'Lat/Lon']) {
      await modes.getByText(mode, { exact: true }).click();
      const coordinates = view.locator('.entity-form__coordinate:visible');
      await expect(coordinates).toHaveCount(4);
      for (const control of await coordinates.all()) {
        await expect(control).toHaveCSS('appearance', 'textfield');
        await expect(control).toHaveAttribute('inputmode', 'decimal');
      }
      const [start, end] = await Promise.all([
        view.getByRole('group', { name: 'Début', exact: true }).boundingBox(),
        view.getByRole('group', { name: 'Fin', exact: true }).boundingBox(),
      ]);
      expect(Math.abs(start.y - end.y)).toBeLessThan(1);
      await view.locator('[data-group=coordinates]').screenshot({ path: test.info().outputPath(`coordonnees-${mode.replace('/', '-')}.png`) });
    }
    await modes.getByText('Carto', { exact: true }).click();
    await draw.click();
    await page.locator('#cancel-editor-map-interaction').click();
    await expect(dateStart).toHaveValue('2026-01-02');
    await expect(dateEnd).toHaveValue('2026-12-31');
    // Canceling a new acquisition preserves the preceding local LineString.
    await expect(view.locator('[name=geometry_type]')).toHaveValue('LineString');
    const parent = view.locator('[name=troncon_ids]');
    const bornageMode = modes.getByText('Bornage', { exact: true });
    await expect(bornageMode).toBeHidden();
    await parent.selectOption(fakeId(1200));
    await bornageMode.click();
    const distance = view.locator('[name=bornage_start_distance]');
    await expect(distance).toHaveAttribute('step', '1');
    await distance.fill('12.34');
    await distance.press('ArrowUp');
    await expect(distance).toHaveValue('13.34');
    await view.locator('[name=bornage_start_sense]').selectOption('APRES_BORNE');
    await view.locator('[name=bornage_end_borne]').selectOption({ index: 1 });
    await view.locator('[name=bornage_end_distance]').fill('0');
    await view.locator('[name=bornage_end_sense]').selectOption('SUR_BORNE');
    await view.locator('[data-role=confirm-bornage]').click();
    await expect(view.locator('[data-role=confirm-bornage]')).toBeDisabled();
    await view.locator('[data-group=bornage]').screenshot({ path: test.info().outputPath('bornage.png') });
    await parent.selectOption(fakeId(1201));
    await expect(view.locator('[name=bornage_start_distance]')).toHaveValue('');
    await expect(view.locator('[data-role=geometry-diagnostics]')).toContainText('géométrie sont conservés');
    await expect(dateStart).toHaveValue('2026-01-02');
    await parent.selectOption([]);
    await expect(bornageMode).toBeHidden();
    await expect(modes.locator('label:visible')).toHaveCount(3);
    await parent.selectOption([fakeId(1200), fakeId(1201)]);
    const reference = view.locator('[name=bornage_reference]');
    await expect(reference).toBeVisible();
    await expect(reference).toHaveValue('');
    await expect(bornageMode).toBeHidden();
    await reference.selectOption(fakeId(1200));
    await expect(bornageMode).toBeVisible();
    const widths = await modes.locator('label:visible').evaluateAll(labels =>
      labels.map(label => label.getBoundingClientRect().width));
    expect(widths).toHaveLength(4);
    expect(Math.max(...widths) - Math.min(...widths)).toBeLessThan(1);
    await noHorizontalOverflow(page);

    await page.setViewportSize({ width: 260, height: 700 });
    const narrow = await Promise.all([dateStart.boundingBox(), dateEnd.boundingBox()]);
    expect(narrow[1].y).toBeGreaterThan(narrow[0].y + narrow[0].height);
  });
}

// Niveau B (~10 s). Retarder uniquement la réponse de calcul permet de vérifier
// l'invalidation du brouillon, avec les vrais contrôleurs et fixtures preview.
test('Bornage commun : un calcul tardif ne remplace pas le brouillon après changement de parent', async ({ page }) => {
  await preview(page);
  const view = await openCreate(page, 'mobilier');
  await view.locator('[name=designation]').fill('Saisie conservée');
  const parent = view.locator('[name=troncon_id]');
  await parent.selectOption(fakeId(1200));
  await expect(view.locator('[data-role=open-relation]')).toBeVisible();
  const modes = view.getByRole('group', { name: 'Mode de localisation' });
  await modes.getByText('Bornage', { exact: true }).click();
  await view.locator('[name=bornage_start_distance]').fill('0');
  const originalStatus = await view.locator('[data-role=point-status]').textContent();
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let notify;
  const requested = new Promise(resolve => { notify = resolve; });
  await page.route('**/geometrie-bornage?*', async route => {
    const response = await route.fetch();
    notify();
    await held;
    await route.fulfill({ response });
  });
  await view.locator('[data-role=confirm-bornage]').click();
  await requested;
  await parent.selectOption(fakeId(1201));
  await expect(view.locator('[name=bornage_start_distance]')).toHaveValue('');
  await expect(view.locator('[data-role=geometry-diagnostics]')).toContainText('géométrie sont conservés');
  const response = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/geometrie-bornage'));
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  await expect(view.locator('[data-role=point-status]')).toHaveText(originalStatus);
  await expect(view.getByText('Bornage confirmé dans le brouillon.')).toBeHidden();
  await expect(view.locator('[name=designation]')).toHaveValue('Saisie conservée');
  await parent.selectOption('');
  await expect(modes.getByText('Bornage', { exact: true })).toBeHidden();
  await expect(modes.locator('label:visible')).toHaveCount(3);
});

test("Carte : Lit CREATE/UPDATE, formes et digues N:N visibles, sauvegarde refusée", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await preview(page);
  const create = await openCreate(page, "troncons-lit");
  await expect(create.locator('[name="geometry_type"] option')).toHaveText(["LineString", "Polygon"]);
  await expect(create.locator('[name="digue_ids"] option')).toHaveCount(2);
  await create.locator('[name="designation"]').fill("Essai de lit fictif");
  await create.locator('[name="digue_ids"]').selectOption(["d1", "d2"]);
  await create.locator('[name="geometry_type"]').selectOption("Polygon");
  await create.locator('[data-role="draw"]').click();
  await page.evaluate(() => {
    for (const [lng, lat] of [[-1.56, 47.21], [-1.55, 47.21], [-1.55, 47.22]]) {
      map.fire("click", { latlng: L.latLng(lat, lng) });
    }
  });
  const shared = page.locator("#confirm-editor-map-interaction");
  if (await shared.isVisible()) await shared.click();
  else await create.locator('[data-role="confirm"]').click();
  await create.locator('[data-role="save"]').click();
  await expect(create.locator('[data-role="status"]')).toContainText("aucune donnée enregistrée");
  await capture(page, "formulaire-lit-sauvegarde-refusee");
  page.once("dialog", dialog => dialog.accept());
  await create.locator('[data-role="cancel"]').click();
  await expect(create).toBeHidden();
  await expect(page.locator("#confirm-editor-map-interaction")).toBeHidden();

  const edit = await openExisting(page, "troncons-lit", LIT);
  await expect(page.locator("#editor-panel .entity-form:visible > .entity-form__header .entity-form__subtitle")).toContainText("Édition");
  await expect(edit.locator('[name="digue_ids"]')).toHaveValues(["d1", "d2"]);
  await expect(edit.locator('[data-family="largeurs"]')).toContainText("12.50");
  await expect(edit.locator('[data-family="largeurs"] li[data-id]')).toHaveCount(2);
  await capture(page, "formulaire-lit-existant");
  await edit.locator('[name="designation"]').fill("Essai de modification fictive");
  await edit.locator('[data-role="save"]').click();
  await expect(edit.locator('[data-role="status"]')).toContainText("aucune donnée enregistrée");
  const catalog = await request.get(`/api/bases/00000000-0000-4000-8000-000000000011/troncons-lit`,
    { params: { context_user_id: "00000000-0000-4000-8000-000000000001",
      context_instance_id: "00000000-0000-4000-8000-000000000099",
      context_data_epoch: "00000000-0000-4000-8000-000000000100" } });
  expect((await catalog.json()).features[0].properties.designation).toBe("Tronçon de lit fictif");
  expect(errors).toEqual([]);
});

test("Carte : morphologie, parent facultatif, géométrie historique et sous-types", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await preview(page);
  const create = await openCreate(page, "elements-morphologiques-lit");
  await expect(create.locator('[name="geometry_type"] option')).toHaveText(["LineString", "Point", "Polygon"]);
  await expect(create.locator('[name="type_morphologie"] option')).toHaveText([
    "Choisir", "Île", "Banc", "Atterrissement", "Indéterminé",
  ]);
  await create.locator('[name="type_morphologie"]').selectOption("BANC");
  await create.locator('[name="segment_id"]').selectOption(LIT);
  await expect(create.locator('[name="segment_id"]')).toHaveValue(LIT);
  await create.locator('[name="segment_id"]').selectOption("");
  await expect(create.locator('[name="segment_id"]')).toHaveValue("");
  await create.locator('[name="valid"]').uncheck();
  await create.locator('[name="designation"]').fill("Banc fictif non dessiné");
  await create.locator('[data-role="save"]').click();
  await expect(create.locator('[data-role="status"]')).toContainText("aucune donnée enregistrée");
  await capture(page, "formulaire-morphologie-sans-geometrie");
  page.once("dialog", dialog => dialog.accept());
  await create.locator('[data-role="cancel"]').click();
  await expect(create).toBeHidden();

  const edit = await openExisting(page, "elements-morphologiques-lit", MORPH);
  await expect(edit.locator('[name="type_morphologie"]')).toHaveValue("ILE");
  await expect(edit.locator('[name="segment_id"]')).toHaveValue(LIT);
  await capture(page, "formulaire-morphologie-existant");
  await edit.locator('[data-role="cancel"]').click();
  const historical = await openExisting(page, "elements-morphologiques-lit", INVALID_MORPH);
  await expect(historical.locator('[name="valid"]')).not.toBeChecked();
  await expect(historical.locator('[data-role="geometry-diagnostics"]')).toContainText("Forme hors du contrat courant");
  await expect(historical.locator('[name="geometry_type"] option')).toHaveText(["LineString", "Point", "Polygon"]);
  expect(errors).toEqual([]);
});

test("Base : lignes Lit/Morphologie, cinq caractéristiques et autres fiches fictives", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/base");
  await expect(page.locator("#frontend-preview-marker")).toContainText("session fictive");
  await selectOnlyTable(page, "troncons_lit");
  await expect(page.locator("#data-view-result-body tr")).toHaveCount(1);
  await page.locator("#data-view-result-body .data-entity-link").first().click();
  await expect(page.locator("#data-entity-detail-title")).toContainText("Tronçon de lit fictif");
  const detail = page.locator("#data-entity-detail-read");
  for (const family of ["Largeur moyenne", "Pente moyenne", "Domanialité", "Régime d'écoulement", "Occupation riveraine"]) {
    await expect(detail).toContainText(family);
  }
  await expect(detail.locator('[data-family="largeurs"]')).toContainText(fakeId(21));
  await expect(detail.locator('[data-family="largeurs"]')).toContainText(fakeId(22));
  await expect(detail).toContainText("Digue 1 · Digue 2");
  await capture(page, "fiche-base-lit-caracteristiques");
  await detail.getByRole("button", { name: "Ajouter — Largeur moyenne" }).click();
  const addWidth = detail.locator('[data-role="lit-characteristic-form"]');
  await expect(addWidth).toBeVisible();
  await addWidth.locator('[name="largeur_moyenne"]').fill("12.50");
  await addWidth.getByRole("button", { name: "Ajouter la caractéristique" }).click();
  await expect(detail.locator('.lit-characteristics [role="status"]')).toContainText("aucune donnée enregistrée");
  await expect(addWidth.locator('[name="largeur_moyenne"]')).toHaveValue("12.50");
  await addWidth.getByRole("button", { name: "Annuler la caractéristique" }).click();
  await detail.locator(`[data-family="largeurs"] li[data-id="${fakeId(21)}"] button`).click();
  const editWidth = detail.locator('[data-role="lit-characteristic-form"]');
  await expect(editWidth.locator('[name="largeur_moyenne"]')).toHaveValue("12.50");
  await editWidth.locator('[name="largeur_moyenne"]').fill("12.75");
  await editWidth.getByRole("button", { name: "Enregistrer la caractéristique" }).click();
  await expect(detail.locator('.lit-characteristics [role="status"]')).toContainText("aucune donnée enregistrée");
  await editWidth.getByRole("button", { name: "Annuler la caractéristique" }).click();
  await expect(detail.locator('[data-family="largeurs"] li[data-id]')).toHaveCount(2);
  for (const [label, field] of [["Pente moyenne", "pente_moyenne"], ["Domanialité", "type_id"],
    ["Régime d'écoulement", "regime_id"], ["Occupation riveraine", "type_occupation_id"]]) {
    await detail.getByRole("button", { name: `Ajouter — ${label}` }).click();
    await expect(detail.locator(`[data-role="lit-characteristic-form"] [name="${field}"]`)).toBeVisible();
    await detail.getByRole("button", { name: "Annuler la caractéristique" }).click();
  }
  await page.locator("#data-entity-detail-edit").click();
  await expect(page.locator('#data-entity-detail .entity-form__form [name="designation"]')).toHaveValue("Tronçon de lit fictif");
  await page.locator("#data-entity-detail-back").click();
  await expect(page.locator("#data-entity-detail-read")).toBeVisible();
  await page.locator("#data-entity-detail-back").click();
  await expect(page.locator("#data-view-builder-workspace")).toBeVisible();

  while (await page.locator("#data-view-field-list button").count()) await page.locator("#data-view-field-list button").first().click();
  await page.locator("#data-view-table").selectOption("elements_morphologiques_lit");
  await page.locator("#data-view-add-field").click();
  await expect(page.locator("#data-view-result-body tr")).toHaveCount(2);
  await page.locator(`.data-entity-link[data-entity-id="${MORPH}"]`).click();
  await expect(page.locator("#data-entity-detail-title")).toContainText("Île fictive");
  await expect(page.locator("#data-entity-detail-read")).toContainText("Île");
  await capture(page, "fiche-base-morphologie");
  expect(errors).toEqual([]);
});

test("Carte : éditeurs existants accessibles avec contrôles de dessin", async ({ context }) => {
  const errors = [];
  const unavailable = [];
  for (const type of ["ouvrage-particulier", "vegetation", "amenagement-hydraulique", "zone-stockage", "troncon", "desordre",
    "mobilier", "equipement-mesure", "reseau-technique", "cheminement", "prestation"]) {
    const page = await context.newPage();
    page.on("pageerror", error => errors.push(`${type}: ${error.message}`));
    page.on("response", response => {
      if (response.status() >= 500) unavailable.push(`${type}: ${response.status()} ${new URL(response.url()).pathname}`);
    });
    await preview(page);
    await page.locator("#toggle-create-menu").click();
    await page.locator(`[data-create-type="${type}"]`).click();
    await expect(page.locator("#editor-panel")).toBeVisible();
    await expect(page.locator("#editor-panel .entity-form:visible > .entity-form__header .entity-form__title")).not.toBeEmpty();
    if (["ouvrage-particulier", "vegetation"].includes(type)) {
      const geometry = page.locator('#business-editor-content [name="geometry_type"]');
      await expect(geometry).toBeVisible();
      expect(await geometry.locator("option").count()).toBeGreaterThan(0);
    }
    if (type === "amenagement-hydraulique") {
      await expect(page.locator('#business-editor-content [data-section="geometry"]')).toHaveCount(0);
    }
    if (type === "zone-stockage") {
      await expect(page.locator('#business-editor-content .entity-form__geometry-fixed-type')).toHaveText('Polygon');
      await expect(page.locator('#business-editor-content [name=amenagement_hydraulique_id] option')).toHaveCount(2);
    }
    if (type === "ouvrage-particulier") await capture(page, "formulaire-ouvrage-particulier");
    await page.close();
  }
  expect(errors).toEqual([]);
  expect(unavailable).toEqual([]);
});

test("Base : Observation liée et Photo sans média privé consultables", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 844, height: 390 });
  await page.goto("/base");
  await selectOnlyTable(page, "ouvrages_particuliers");
  await page.locator("#data-view-result-body .data-entity-link").first().click();
  await expect(page.locator("#data-entity-detail-title")).toContainText("Ouvrage particulier fictif");
  await page.locator('#data-entity-detail [data-role="observations-tab"]').click();
  await expect(page.locator(".observation-list-item")).toContainText("Observation fictive");
  await page.locator(".observation-list-item").click();
  const dialog = page.locator("#observation-detail-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Photo fictive sans fichier");
  await expect(dialog.locator(".photo-gallery-item img")).toHaveCount(0);
  await actionInViewport(page, dialog.locator("[data-close]"), 390);
  await capture(page, "dialogue-observation-photo-fictive");
  await dialog.locator("[data-close]").click();
  await expect(dialog).toBeHidden();
  expect(errors).toEqual([]);
});

for (const [name, width, height] of [["tablette", 820, 1180], ["portrait", 390, 844], ["paysage", 844, 390]]) {
  test(`Formulaires ${name} : Lit, morphologie et ouvrage atteignables`, async ({ page }) => {
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewportSize({ width, height });
    await preview(page);
    const lit = await openCreate(page, "troncons-lit");
    await noHorizontalOverflow(page);
    await actionInViewport(page, lit.locator('[data-role="save"]'), height);
    await actionInViewport(page, lit.locator('[data-role="cancel"]'), height);
    await capture(page, `${name}-formulaire-lit`);
    await lit.locator('[data-role="cancel"]').click();
    await expect(lit).toBeHidden();

    const morph = await openCreate(page, "elements-morphologiques-lit");
    await noHorizontalOverflow(page);
    await morph.locator('[name="type_morphologie"]').selectOption("INDETERMINE");
    await actionInViewport(page, morph.locator('[data-role="save"]'), height);
    await actionInViewport(page, morph.locator('[data-role="cancel"]'), height);
    await capture(page, `${name}-formulaire-morphologie`);
    page.once("dialog", dialog => dialog.accept());
    await morph.locator('[data-role="cancel"]').click();
    await expect(morph).toBeHidden();

    await page.locator("#toggle-create-menu").click();
    await page.locator('[data-create-type="ouvrage-particulier"]').click();
    await expect(page.locator("#editor-panel")).toBeVisible();
    await noHorizontalOverflow(page);
    await expect(page.locator('#business-editor-content [name="geometry_type"]')).toBeVisible();
    await actionInViewport(page, page.locator("#editor-panel .entity-form__header [data-role=close]:visible"), height);
    await capture(page, `${name}-formulaire-ouvrage`);
    await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
    await expect(page.locator("#editor-panel")).toBeHidden();
    expect(errors).toEqual([]);
  });
}
