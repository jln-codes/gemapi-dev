const { expect, test } = require("@playwright/test");
const { mockBusinessCRS } = require("./mock-business-crs");

test.use({ serviceWorkers: "block" });

const TRONCON_ID = "11111111-1111-4111-8111-111111111111";
const SYSTEM_ID = "22222222-2222-4222-8222-222222222222";
const BORNE_ID = "33333333-3333-4333-8333-333333333333";
const DESORDRE_ID = "44444444-4444-4444-8444-444444444444";
const SECOND_TRONCON = "55555555-5555-4555-8555-555555555555";
const SECOND_BORNE = "66666666-6666-4666-8666-666666666666";
const existingLine = { type: "Feature",
  geometry: { type: "LineString", coordinates: [[2.34, 48.84], [2.341, 48.84]] },
  properties: { id: DESORDRE_ID, version: 1, designation: "Ligne existante",
    valid: true, troncon_ids: [], type_desordre_id: null, categorie_desordre_id: null },
};

async function setup(context, standalone, { existingFeature = null, srid = 3950 } = {}) {
  await context.addInitScript((installed) => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = query => query === "(display-mode: standalone)"
      ? { matches: installed, media: query, addEventListener() {}, removeEventListener() {} }
      : real(query);
    window.__gpsWatches = [];
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
      getCurrentPosition() {},
      watchPosition(resolve, reject) {
        window.__gpsWatches.push({ resolve, reject });
        return window.__gpsWatches.length;
      },
      clearWatch() {},
    } });
  }, standalone);
  await context.route("**/api/**", async route => {
    if (await mockBusinessCRS(route, srid)) return;
    const path = new URL(route.request().url()).pathname;
    const empty = { type: "FeatureCollection", features: [] };
    const json = path === "/api/config"
      ? { legacy_single_database_enabled: true, show_uuid: false,
        build_id: "pwa-geometry-input-simplification" }
      : path === "/api/resource-events/head" ? { cursor: "0" }
      : path === "/api/resource-events" ? { events: [], hasMore: false, nextCursor: "0" }
      : path === "/api/base-summary" ? { crs: { authority: "EPSG", srid: 3950,
        code: "EPSG:3950", name: "RGF93 / CC50" } }
      : path === "/api/desordres" ? { type: "FeatureCollection",
        features: existingFeature ? [existingFeature] : [] }
      : path === `/api/desordres/${DESORDRE_ID}` ? existingFeature
      : ["/api/troncons", "/api/territoire-administratif"].includes(path) ? empty
      : path === "/api/troncons/options" ? { troncons: [{ id: TRONCON_ID,
        libelle: "Tronçon de test", digue_id: SYSTEM_ID, digue_libelle: "Digue de test",
        valid: true }, { id: SECOND_TRONCON, libelle: "Second tronçon", valid: true }] }
      : path === `/api/troncons/${TRONCON_ID}/reperage-options`
        ? { troncon_id: TRONCON_ID, systeme_reperage_id: SYSTEM_ID,
          bornes: [{ id: BORNE_ID, libelle: "Borne de test", valid: true }] }
      : path === `/api/troncons/${SECOND_TRONCON}/reperage-options`
        ? { troncon_id: SECOND_TRONCON, systeme_reperage_id: SYSTEM_ID,
          bornes: [{ id: SECOND_BORNE, libelle: "Seconde borne", valid: true }] }
      : path.startsWith("/api/referentiels/")
        ? { types: [], categories: [], references: [] }
      : path === "/api/systemes-endiguement" ? { systemes: [] } : {};
    await route.fulfill({ json });
  });
}

async function openDesordreCreation(page) {
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await expect(page.locator("#desordre-editor")).toBeVisible();
}

async function expectMethodButtonsAligned(page, manualSelector, trackerOwner) {
  await expect.poll(() => page.evaluate(({ manualSelector: manual, trackerOwner: owner }) => ({
    draw: document.querySelector(manual)?.getBoundingClientRect().height ?? 0,
    gps: document.querySelector(`[data-gps-owner="${owner}"] [data-gps="start"]`)
      ?.getBoundingClientRect().height ?? 0,
  }), { manualSelector, trackerOwner })).toEqual({ draw: 48, gps: 48 });
  const result = await page.evaluate(({ manualSelector: manual, trackerOwner: owner }) => {
    const draw = document.querySelector(manual);
    const gps = document.querySelector(`[data-gps-owner="${owner}"] [data-gps="start"]`);
    const drawRect = draw.getBoundingClientRect();
    const gpsRect = gps.getBoundingClientRect();
    const drawStyle = getComputedStyle(draw);
    const gpsStyle = getComputedStyle(gps);
    return {
      draw: { top: drawRect.top, height: drawRect.height, width: drawRect.width },
      gps: { top: gpsRect.top, height: gpsRect.height, width: gpsRect.width },
      sameStyle: drawStyle.backgroundImage === gpsStyle.backgroundImage
        && drawStyle.color === gpsStyle.color
        && drawStyle.borderRadius === gpsStyle.borderRadius,
    };
  }, { manualSelector, trackerOwner });
  expect(Math.abs(result.draw.top - result.gps.top)).toBeLessThan(1);
  expect(result.draw.height).toBe(48);
  expect(result.gps.height).toBe(48);
  expect(Math.abs(result.draw.width - result.gps.width)).toBeLessThan(1);
  expect(result.sameStyle).toBe(true);
}

test("PWA : seuls le dessin et le tracking sont proposés sur une ligne", async ({ context, page }) => {
  await setup(context, true);
  await page.setViewportSize({ width: 390, height: 844 });
  await openDesordreCreation(page);
  await expect(page.locator("body")).toHaveClass(/pwa-standalone/);
  await expect(page.locator("#desordre-mode-selector")).toBeHidden();
  await expect(page.locator("#desordre-xy-choice")).toBeHidden();
  await expect(page.locator("#desordre-lonlat-choice")).toBeHidden();
  await page.locator("#desordre-create-troncons").selectOption(TRONCON_ID);
  await expect(page.locator("#desordre-bornage-choice input")).toBeEnabled();
  await expect(page.locator("#desordre-bornage-choice")).toBeHidden();
  await expect(page.locator("#desordre-create-xy")).toBeHidden();
  await expect(page.locator("#desordre-create-lonlat")).toBeHidden();
  await expect(page.locator("#desordre-create-line-coordinates")).toBeHidden();
  await expect(page.locator("#desordre-create-bornage")).toBeHidden();

  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  const tracker = page.locator('[data-gps-owner="desordre-create-geometry"]');
  await expect(page.locator("#start-desordre-draw")).toHaveText("Dessiner");
  await expect(tracker.locator('[data-gps="start"]')).toHaveText("Tracé GPS");
  await expectMethodButtonsAligned(page, "#start-desordre-draw", "desordre-create-geometry");
  await page.setViewportSize({ width: 844, height: 390 });
  await expectMethodButtonsAligned(page, "#start-desordre-draw", "desordre-create-geometry");
  await page.setViewportSize({ width: 390, height: 844 });
  await expectMethodButtonsAligned(page, "#start-desordre-draw", "desordre-create-geometry");

  await page.locator("#start-desordre-draw").click();
  await expect(page.locator("#editor-map-interaction")).toBeVisible();
  await page.locator("#cancel-editor-map-interaction").click();
  await tracker.locator('[data-gps="start"]').click();
  await expect.poll(() => page.evaluate(() => window.__gpsWatches.length)).toBe(1);
  await tracker.locator('[data-gps="cancel"]').click();
  await expect(tracker.locator('[data-gps="start"]')).toBeVisible();
});

test("desktop : X/Y, latitude/longitude et bornage restent disponibles", async ({ context, page }) => {
  await setup(context, false);
  await page.setViewportSize({ width: 1280, height: 800 });
  await openDesordreCreation(page);
  await expect(page.locator("body")).not.toHaveClass(/pwa-standalone/);
  await expect(page.locator("#desordre-mode-selector")).toBeVisible();
  await expect(page.locator("#desordre-mode-selector legend")).toHaveText("Mode de localisation");
  await expect(page.locator("#desordre-xy-choice")).toBeVisible();
  await expect(page.locator("#desordre-lonlat-choice")).toBeVisible();
  const modes = page.locator('#desordre-mode-selector label:visible');
  const widths = () => modes.evaluateAll(labels => labels.map(label => label.getBoundingClientRect().width));
  await expect(modes).toHaveCount(3);
  const three = await widths();
  expect(Math.max(...three) - Math.min(...three)).toBeLessThan(1);
  await page.locator("#desordre-xy-choice").click();
  await expect(page.locator("#desordre-create-xy")).toBeVisible();
  await page.locator("#desordre-lonlat-choice").click();
  await expect(page.locator("#desordre-create-lonlat")).toBeVisible();
  await page.locator("#desordre-create-troncons").selectOption(TRONCON_ID);
  await expect(page.locator("#desordre-bornage-choice input")).toBeEnabled();
  await expect(page.locator("#desordre-bornage-choice")).toBeVisible();
  await expect(modes).toHaveCount(4);
  const four = await widths();
  expect(Math.max(...four) - Math.min(...four)).toBeLessThan(1);
  expect(four[0]).toBeLessThan(three[0]);
  await page.locator("#desordre-bornage-choice").click();
  await expect(page.locator("#desordre-create-bornage")).toBeVisible();
  await page.locator('#desordre-create-designation').fill('Attribut indépendant du tronçon');
  await page.locator('#desordre-create-borne-start').selectOption(BORNE_ID);
  const distance = page.locator('#desordre-create-distance-start');
  await expect(distance).toHaveAttribute('step', '1');
  await expect(distance).not.toHaveCSS('appearance', 'textfield');
  await distance.fill('12.34');
  await distance.press('ArrowUp');
  await expect(distance).toHaveValue('13.34');
  await distance.press('ArrowDown');
  await expect(distance).toHaveValue('12.34');
  expect(await distance.evaluate(input => input.validity.valid)).toBe(true);
  await page.locator('#desordre-create-troncons').selectOption('');
  await expect(modes).toHaveCount(3);
  await expect(page.locator('input[name=desordre-mode][value=map]')).toBeChecked();
  await expect(page.locator('#desordre-create-bornage')).toBeHidden();
  await expect(page.locator('#desordre-create-borne-start')).toHaveValue('');
  await expect(page.locator('#desordre-create-designation')).toHaveValue('Attribut indépendant du tronçon');
  await expect(page.locator('#disorder-form-view [data-role=geometry-diagnostics]')).toContainText('tronçon précédent');
  await page.locator('#desordre-create-troncons').selectOption(SECOND_TRONCON);
  await page.locator('#desordre-bornage-choice').click();
  await expect(page.locator('#desordre-create-distance-start')).toHaveValue('');
  await expect(page.locator('#desordre-create-borne-start')).toHaveValue(SECOND_BORNE);
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  await page.locator("#desordre-mode-selector label").filter({ hasText: "Carto" }).click();
  await expect(page.locator("#start-desordre-draw")).toHaveText("Dessiner");
  await expect(page.locator('[data-gps-owner="desordre-create-geometry"]')).toBeHidden();
});

test("PWA Tronçon : Dessiner et Tracé GPS restent alignés", async ({ context, page }) => {
  await setup(context, true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="troncon"]').click();
  await expect(page.locator("#start-troncon-draw")).toHaveText("Dessiner");
  await expectMethodButtonsAligned(page, "#start-troncon-draw", "troncon-create-geometry");
  const form = page.locator('#heritage-create-view');
  await form.getByRole('group', { name: 'Mode de localisation' }).getByText('Lat/Lon').click();
  await form.getByRole('button', { name: 'Fermer', exact: true }).click();
  await expect(form).toBeHidden();
});

test('Bornage en édition : le rattachement enregistré reste la référence', async ({ context, page }) => {
  await setup(context, false, { existingFeature: {
    ...existingLine,
    properties: { ...existingLine.properties, troncon_ids: [TRONCON_ID], reperage: {
      disponible: true, nombre_troncons: 1, bornes: [{ id: BORNE_ID, libelle: 'Borne de test' }],
      borne_debut_id: BORNE_ID, borne_fin_id: BORNE_ID, distance_debut_m: 12.34, distance_fin_m: 18,
    } },
  } });
  await page.goto('/carte');
  await page.waitForFunction(id => typeof desordreLayersById !== 'undefined' && desordreLayersById.has(id), DESORDRE_ID);
  await page.evaluate(id => desordreLayersById.get(id).fire('click'), DESORDRE_ID);
  const bornage = page.locator('#desordre-bornage-choice');
  await bornage.click();
  const start = page.getByRole('group', { name: 'Début', exact: true });
  const end = page.getByRole('group', { name: 'Fin', exact: true });
  await expect(start.getByLabel('Borne', { exact: true })).toHaveValue(BORNE_ID);
  await expect(end.getByLabel('Borne', { exact: true })).toHaveValue(BORNE_ID);
  const [first, last] = await Promise.all([start.boundingBox(), end.boundingBox()]);
  expect(Math.abs(first.y - last.y)).toBeLessThan(1);
  expect(Math.abs(first.width - last.width)).toBeLessThan(1);
  expect(last.x).toBeGreaterThan(first.x + first.width);
  const designation = page.locator('#desordre-create-designation');
  await designation.fill('Brouillon à conserver');
  await page.locator('#desordre-create-distance-start').fill('45');
  const attachment = page.locator('#desordre-create-troncons');
  await attachment.selectOption([]);
  await expect(bornage).toBeHidden();
  await expect(page.locator('input[name=desordre-mode][value=map]')).toBeChecked();
  await expect(designation).toHaveValue('Brouillon à conserver');
  await attachment.selectOption(TRONCON_ID);
  await bornage.click();
  await expect(page.locator('#desordre-create-distance-start')).toHaveValue('12.34');
  await expect(designation).toHaveValue('Brouillon à conserver');
});

test('PWA : extrémités du Réseau côte à côte selon la largeur du panneau', async ({ context, page }) => {
  await setup(context, true, { srid: 2154 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=reseau-technique]').click();
  const view = page.locator('#reseau-technique-editor-panel');
  await view.locator('[name=geometry_type]').selectOption('LineString');
  const modes = view.getByRole('group', { name: 'Mode de localisation' });
  await modes.getByText('X/Y', { exact: true }).click();
  const start = view.getByRole('group', { name: 'Début', exact: true });
  const end = view.getByRole('group', { name: 'Fin', exact: true });
  const boxes = () => Promise.all([start.boundingBox(), end.boundingBox()]);
  const x = start.getByLabel('X (EPSG:2154)', { exact: true });
  await x.fill('700000');
  const type = view.locator('[name=geometry_type]');
  await type.selectOption('Point');
  await expect(type).toHaveValue('LineString');
  await expect(end).toBeVisible();
  await modes.getByText('Lat/Lon', { exact: true }).click();
  await expect(x).toHaveValue('700000');
  await expect(view.getByRole('status').filter({ hasText: 'avant de changer de mode' })).toBeVisible();
  await view.getByRole('button', { name: 'Annuler la saisie' }).click();
  await modes.getByText('Lat/Lon', { exact: true }).click();
  await expect(start.getByLabel('Longitude')).toBeEditable();
  for (const size of [{ width: 390, height: 844 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(size);
    await expect.poll(async () => {
      const [a, b] = await boxes();
      return Math.abs(a.y - b.y) < 1 && Math.abs(a.width - b.width) < 1 && b.x > a.x + a.width;
    }).toBe(true);
  }
  await page.setViewportSize({ width: 260, height: 640 });
  await expect.poll(async () => {
    const [a, b] = await boxes();
    return Math.abs(a.x - b.x) < 1 && b.y > a.y + a.height;
  }).toBe(true);
  await modes.getByText('Carto', { exact: true }).click();
  await expect(view.getByRole('button', { name: 'Dessiner', exact: true })).toBeVisible();
});

test("PWA : la correction d'une LineString conserve dessin et tracking", async ({ context, page }) => {
  await setup(context, true, { existingFeature: existingLine });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  await page.waitForFunction(id => typeof desordreLayersById !== "undefined"
    && desordreLayersById.get(id), DESORDRE_ID);
  await page.evaluate(id => desordreLayersById.get(id).fire("click"), DESORDRE_ID);
  await expect(page.locator("#desordre-create-id")).toHaveJSProperty("value", DESORDRE_ID);
  await expect(page.locator("#desordre-mode-selector")).toBeHidden();
  await expect(page.locator("#start-line-edit")).toHaveText("Modifier");
  await expect(page.locator('[data-gps-owner="desordre-create-geometry"] [data-gps="start"]'))
    .toHaveText("Tracé GPS");
  await expectMethodButtonsAligned(page, "#start-line-edit", "desordre-create-geometry");
});
