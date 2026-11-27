const { expect, test } = require("@playwright/test");

async function openDesordreCreation(page) {
  await page.goto("/carte");
  await expect(page.locator("#map")).toBeVisible();
  await page.waitForFunction(() => typeof topNavigation !== "undefined"
    && topNavigation.getState().create === false);

  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await expect(page.locator("#editor-panel")).toBeVisible();
  await expect(page.locator("#desordre-editor")).toBeVisible();
}

async function startPointPlacement(page) {
  await page.locator("#start-desordre-draw").click();
  await expect(page.locator("main")).toHaveClass(/editor-map-mode/);
  await expect(page.locator("#editor-map-interaction")).toBeVisible();
}

async function openExistingDesordre(page, geometryType, identifier = null) {
  const desordresResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/desordres"
  ));
  await page.goto("/carte");
  await expect(page.locator("#map")).toBeVisible();
  const response = await desordresResponse;
  expect(response.ok()).toBeTruthy();
  const collection = await response.json();
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  const feature = collection.features?.find(item => item.geometry?.type === geometryType && (!identifier || item.properties.id === identifier));
  expect(feature, `La fixture doit fournir un désordre ${geometryType}`).toBeTruthy();
  // Urgency controls colour; identify the rendered business geometry by its ID.
  await page.waitForFunction(id => typeof desordreLayersById !== 'undefined'
    && desordreLayersById.get(id)?.getElement(), feature.properties.id);
  await page.evaluate(id => {
    const layer = desordreLayersById.get(id);
    layer.getElement().dataset.e2eSelectedDesordre = 'true';
    if (layer.getBounds) map.fitBounds(layer.getBounds(), { padding: [50, 50], maxZoom: 19, animate: false });
  }, feature.properties.id);
  const target = page.locator('[data-e2e-selected-desordre="true"]');
  await expect(target).toBeAttached();
  await target.dispatchEvent("click");
  await expect(page.locator("#desordre-editor")).toBeVisible();
  await expect(page.locator("#desordre-create-geometry-type")).toHaveValue(geometryType);
  await expect(page.locator("#desordre-create-id")).not.toHaveJSProperty("value", "");
  return page.locator('[data-e2e-selected-desordre="true"]');
}

async function expectExistingInteractionSuspended(page) {
  await expect(page.locator("main")).toHaveClass(/editor-open/);
  await expect(page.locator("main")).toHaveClass(/editor-map-mode/);
  await expect(page.locator("#editor-panel")).toBeHidden();
  await expect(page.locator("#editor-panel")).toHaveJSProperty("hidden", false);
  await expect(page.locator("#map")).toHaveJSProperty("inert", false);
  await expect(page.locator("#editor-map-interaction")).toBeVisible();
}

async function resizeExistingInteraction(page, preservedField) {
  await page.setViewportSize({ width: 700, height: 844 });
  await expect(page.locator("main")).not.toHaveClass(/editor-map-mode/);
  await expect(page.locator("#editor-panel")).toBeVisible();
  await expect(page.locator("#editor-map-interaction")).toBeHidden();
  await expect(page.locator("#map")).toHaveJSProperty("inert", false);
  await expect(preservedField).toHaveValue("Commentaire E2E non enregistré");

  await page.setViewportSize({ width: 699, height: 844 });
  await expectExistingInteractionSuspended(page);
  await expect(preservedField).toHaveValue("Commentaire E2E non enregistré");
}

async function dragElement(page, locator, deltaX, deltaY) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  const startX = box.x + (box.width / 2);
  const startY = box.y + (box.height / 2);
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + deltaX, startY + deltaY, { steps: 4 });
  await page.mouse.up();
}

async function choosePointOnMap(page, xRatio = 0.65, yRatio = 0.6) {
  const mapBox = await page.locator("#map").boundingBox();
  expect(mapBox).not.toBeNull();
  await page.mouse.click(
    mapBox.x + (mapBox.width * xRatio),
    mapBox.y + (mapBox.height * yRatio),
  );
}

test("le dessin ponctuel suspend seulement la présentation de l’éditeur en responsive", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openDesordreCreation(page);

  const designation = page.locator("#desordre-create-designation");
  await designation.fill("Désordre E2E conservé");
  await startPointPlacement(page);

  const editor = page.locator("#editor-panel");
  const banner = page.locator("#editor-map-interaction");
  await expect(page.locator("main")).toHaveClass(/editor-open/);
  await expect(editor).toBeHidden();
  await expect(editor).toHaveJSProperty("hidden", false);
  await expect(page.locator("#map")).toHaveJSProperty("inert", false);
  await expect(banner).toBeVisible();

  await page.setViewportSize({ width: 700, height: 844 });
  await expect(page.locator("main")).not.toHaveClass(/editor-map-mode/);
  await expect(editor).toBeVisible();
  await expect(banner).toBeHidden();
  await expect(page.locator("#map")).toHaveJSProperty("inert", false);
  await expect(designation).toHaveValue("Désordre E2E conservé");

  await page.setViewportSize({ width: 699, height: 844 });
  await expect(page.locator("main")).toHaveClass(/editor-map-mode/);
  await expect(editor).toBeHidden();
  await expect(banner).toBeVisible();
  await expect(designation).toHaveValue("Désordre E2E conservé");

  const mapBox = await page.locator("#map").boundingBox();
  expect(mapBox).not.toBeNull();
  await page.mouse.click(mapBox.x + (mapBox.width / 2), mapBox.y + (mapBox.height / 2));
  await expect(page.locator("#finish-editor-map-interaction")).toBeVisible();

  await page.locator("#finish-editor-map-interaction").click();
  await expect(page.locator("main")).not.toHaveClass(/editor-map-mode/);
  await expect(editor).toBeVisible();
  await expect(banner).toBeHidden();
  await expect(designation).toHaveValue("Désordre E2E conservé");
});

test("le repositionnement d’un point existant conserve le formulaire à l’annulation et à la validation", async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openExistingDesordre(page, "Point");

  const id = await page.locator("#desordre-create-id").evaluate(output => output.value);
  const originalResponse = await request.get(`/api/desordres/${encodeURIComponent(id)}`);
  expect(originalResponse.ok()).toBeTruthy();
  const originalFeature = await originalResponse.json();
  const commentaire = page.locator("#desordre-create-commentaire");
  await commentaire.fill("Commentaire E2E non enregistré");
  try {
    await page.locator("#start-map-position").click();
    await expectExistingInteractionSuspended(page);
    await resizeExistingInteraction(page, commentaire);
    await choosePointOnMap(page, 0.62, 0.58);
    await expect(page.locator("#finish-editor-map-interaction")).toBeVisible();
    await page.locator("#cancel-editor-map-interaction").click();
    await expect(page.locator("main")).not.toHaveClass(/editor-map-mode/);
    await expect(page.locator("#editor-panel")).toBeVisible();
    await expect(commentaire).toHaveValue("Commentaire E2E non enregistré");
    await expect(page.locator("#start-map-position")).toBeFocused();

    const pointEndpoint = `**/api/desordres/${id}`;
    await page.route(pointEndpoint, async (route) => {
      if (route.request().method() !== "PUT") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Échec E2E contrôlé" }),
      });
    });
    await page.locator("#start-map-position").click();
    await expectExistingInteractionSuspended(page);
    await choosePointOnMap(page, 0.68, 0.62);
    await page.locator("#finish-editor-map-interaction").click();
    await expect(page.locator("#desordre-create-message")).toContainText(
      "Échec E2E contrôlé",
    );
    await expectExistingInteractionSuspended(page);
    await expect(page.locator("#cancel-editor-map-interaction")).toBeEnabled();
    await expect(commentaire).toHaveValue("Commentaire E2E non enregistré");
    await page.unroute(pointEndpoint);
    await page.locator("#cancel-editor-map-interaction").click();

    await page.locator("#start-map-position").click();
    await expectExistingInteractionSuspended(page);
    await choosePointOnMap(page, 0.58, 0.66);
    const validation = page.waitForResponse((response) => (
      response.request().method() === "PUT"
      && response.url().endsWith(`/api/desordres/${encodeURIComponent(id)}`)
    ));
    await page.locator("#finish-editor-map-interaction").click();
    expect((await validation).ok()).toBeTruthy();
    await expect(page.locator("main")).not.toHaveClass(/editor-map-mode/);
    await expect(page.locator("#editor-panel")).toBeVisible();
    await expect(page.locator("#editor-map-interaction")).toBeHidden();
    await expect(commentaire).toHaveValue("Commentaire E2E non enregistré");
    await expect(page.locator("#start-map-position")).toBeFocused();
  } finally {
    const [longitude, latitude] = originalFeature.geometry.coordinates;
    const currentResponse = await request.get(`/api/desordres/${encodeURIComponent(id)}`);
    const currentVersion = (await currentResponse.json()).properties.version;
    const restoreResponse = await request.put(
      `/api/desordres/${encodeURIComponent(id)}`,
      { data: { longitude: longitude, latitude: latitude },
        headers: { "If-Match": `"${currentVersion}"` } },
    );
    expect(restoreResponse.ok()).toBeTruthy();
  }
});

test("l’édition d’une LineString existante restaure la géométrie et conserve le formulaire", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const selectedPath = await openExistingDesordre(page, "LineString");
  const commentaire = page.locator("#desordre-create-commentaire");
  await commentaire.fill("Commentaire E2E non enregistré");

  await page.locator("#start-line-edit").click();
  await expectExistingInteractionSuspended(page);
  await resizeExistingInteraction(page, commentaire);
  const originalPath = await selectedPath.getAttribute("d");
  const vertex = page.locator(".leaflet-vertex-icon").first();
  await expect(vertex).toBeVisible();
  await dragElement(page, vertex, 12, 8);
  await expect(selectedPath).not.toHaveAttribute("d", originalPath);
  await expect(page.locator("#finish-editor-map-interaction")).toBeVisible();

  await page.locator("#cancel-editor-map-interaction").click();
  await expect(page.locator("main")).not.toHaveClass(/editor-map-mode/);
  await expect(page.locator("#editor-panel")).toBeVisible();
  await expect(page.locator("#editor-map-interaction")).toBeHidden();
  await expect(commentaire).toHaveValue("Commentaire E2E non enregistré");
  await expect(selectedPath).toHaveAttribute("d", originalPath);
  await expect(page.locator(".leaflet-vertex-icon")).toHaveCount(0);
  await expect(page.locator("#start-line-edit")).toBeFocused();
});

test("l’édition d’un Polygon existant se nettoie à l’annulation et conserve le formulaire", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const selectedPath = await openExistingDesordre(page, "Polygon");
  const commentaire = page.locator("#desordre-create-commentaire");
  await commentaire.fill("Commentaire E2E non enregistré");

  await page.locator("#start-desordre-draw").click();
  await expectExistingInteractionSuspended(page);
  await resizeExistingInteraction(page, commentaire);
  const originalPath = await selectedPath.getAttribute("d");
  const vertex = page.locator(".leaflet-vertex-icon").first();
  await expect(vertex).toBeVisible();
  await dragElement(page, vertex, -10, 9);
  await expect(selectedPath).not.toHaveAttribute("d", originalPath);

  await page.locator("#cancel-editor-map-interaction").click();
  await expect(page.locator("main")).not.toHaveClass(/editor-map-mode/);
  await expect(page.locator("#editor-panel")).toBeVisible();
  await expect(page.locator("#editor-map-interaction")).toBeHidden();
  await expect(commentaire).toHaveValue("Commentaire E2E non enregistré");
  await expect(selectedPath).toHaveAttribute("d", originalPath);
  await expect(page.locator(".leaflet-vertex-icon")).toHaveCount(0);
  await expect(page.locator("#start-desordre-draw")).toBeVisible();
  await expect(page.locator("#start-desordre-draw")).toBeFocused();
});

// UX-FORM-04, niveau B : le verrou du fieldset doit être levé avant le retour
// du focus. Chaque scénario crée son propre Désordre dans le cluster jetable.
for (const geometryType of ['LineString', 'Polygon']) test(`UX04 ${geometryType} : fin de dessin puis sauvegarde générale, focus et brouillon conservés`, async ({page,request}) => {
  // Le seed fixe aussi le territoire cartographique : reprendre une géométrie
  // fictive de ce territoire, sans contourner maxBounds ni modifier le seed.
  const collection = await (await request.get('/api/desordres')).json();
  const geometry = collection.features.find(feature => feature.geometry?.type === geometryType).geometry;
  const response = await request.post('/api/desordres', {data:{designation:`UX04 ${geometryType}`,geometry,valid:true}});
  expect(response.status()).toBe(201); const created = await response.json(), id=created.properties.id;
  await page.setViewportSize({width:390,height:844}); await openExistingDesordre(page,geometryType,id);
  const commentaire = page.locator('#desordre-create-commentaire');
  await commentaire.fill('Saisie locale conservée');
  const start = page.locator(geometryType === 'LineString' ? '#start-line-edit' : '#start-desordre-draw');
  await start.click(); await expectExistingInteractionSuspended(page);
  const vertex = page.locator('.leaflet-vertex-icon').first(); await expect(vertex).toBeInViewport();
  await dragElement(page,vertex,12,8);
  const writes=[];page.on('request',r=>{if(r.method()==='PUT'&&r.url().includes(`/api/desordres/${id}`))writes.push(r)});
  await page.locator('#finish-editor-map-interaction').click();expect(writes).toHaveLength(0);
  const beforeSave=await(await request.get(`/api/desordres/${id}`)).json();expect(beforeSave.geometry).toEqual(created.geometry);
  await expect(page.locator('main')).not.toHaveClass(/editor-map-mode/);
  await expect(commentaire).toHaveValue('Saisie locale conservée'); await expect(start).toBeFocused();
  const confirmed=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname===`/api/desordres/${id}`);
  await page.locator('#submit-desordre-create').click();expect((await confirmed).status()).toBe(200);expect(writes).toHaveLength(1);
  const actual = await (await request.get(`/api/desordres/${id}`)).json();
  expect(actual.properties.id).toBe(id); expect(actual.properties.version).toBeGreaterThan(created.properties.version);
  expect(actual.geometry.type).toBe(geometryType); expect(actual.geometry).not.toEqual(created.geometry);
  expect(actual.properties.commentaire).toBe('Saisie locale conservée');
});
