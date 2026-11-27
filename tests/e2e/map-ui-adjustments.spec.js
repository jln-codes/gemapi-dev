const { mockBusinessCRS } = require('./mock-business-crs');
const { expect, test } = require("@playwright/test");

test.use({ serviceWorkers: "block" });

test("les ajustements UI restent compacts et utilisables sur smartphone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/config") {
      await route.fulfill({ json: {
        basemap_tilejson_url: "", legacy_single_database_enabled: true, show_uuid: false,
      } });
    } else if (path === "/api/resource-events/head") {
      await route.fulfill({ json: { event_id: null } });
    } else if (path === '/api/amenagements-hydrauliques/relations/options') {
      await route.fulfill({ json: { troncons: [], zones: [] } });
    } else {
      await route.fulfill({ json: { type: "FeatureCollection", features: [] } });
    }
  });
  await page.goto("/carte", { waitUntil: "domcontentloaded" });
  await expect.poll(() => page.evaluate(() => Boolean(window.MapLayerControls))).toBe(true);
  const disorderLegendToggle = page.locator(".legend-disorders-toggle");
  const disorderUrgencies = page.locator("#legend-disorder-urgencies");
  const disorderUrgencyRows = disorderUrgencies.locator(".legend-disorder-urgency-row");
  const masterDisorderToggle = page.locator("[data-disorder-layer-master]");
  await expect(disorderLegendToggle).toHaveAttribute("aria-expanded", "false");
  await expect(disorderUrgencies).toBeHidden();
  await expect(masterDisorderToggle).toBeChecked();
  const collapsedLegend = await page.locator("#map-legend").evaluate((element) => ({
    height: element.getBoundingClientRect().height,
    scrollHeight: element.scrollHeight,
  }));
  await page.evaluate(() => {
    window.__disorderVisibilityChanges = 0;
    document.querySelectorAll("[data-disorder-layer-toggle]").forEach((input) => {
      input.addEventListener("change", () => { window.__disorderVisibilityChanges += 1; });
    });
  });
  await disorderLegendToggle.focus();
  await disorderLegendToggle.press("Enter");
  await expect(disorderLegendToggle).toHaveAttribute("aria-expanded", "true");
  await expect(disorderUrgencies).toBeVisible();
  await expect(disorderUrgencyRows).toHaveCount(6);
  await expect(disorderUrgencyRows.locator(".legend-disorder-urgency-label")).toHaveText([
    "Urgence 4", "Urgence 3", "Urgence 2", "Urgence 1", "Indéfini", "Sans urgence",
  ]);
  await expect(disorderUrgencies).not.toContainText("RefUrgence:99");
  const colors = await disorderUrgencyRows.evaluateAll((rows) => rows.map((row) => {
    const swatch = row.querySelector(".legend-disorder-urgency-swatch");
    return {
      borderColor: swatch.style.getPropertyValue("--legend-urgency-border"),
      color: swatch.style.getPropertyValue("--legend-urgency-color"),
    };
  }));
  const canonicalColors = await page.evaluate(() => [
    "RefUrgence:4", "RefUrgence:3", "RefUrgence:2", "RefUrgence:1", "RefUrgence:99", null,
  ].map((id) => {
    const style = DisorderUrgencyStyle.resolve(id);
    return { borderColor: style.borderColor, color: style.color };
  }));
  expect(colors).toEqual(canonicalColors);
  expect(colors[4]).not.toEqual(colors[5]);
  expect(await page.evaluate(() => window.__disorderVisibilityChanges)).toBe(0);
  await expect(masterDisorderToggle).toBeChecked();
  const expandedLegendBox = await page.locator("#map-legend").evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { height: box.height, left: box.left, right: box.right,
      scrollHeight: element.scrollHeight };
  });
  expect(expandedLegendBox.scrollHeight).toBeGreaterThan(collapsedLegend.scrollHeight);
  expect(expandedLegendBox.height).toBeLessThanOrEqual(176);
  expect(expandedLegendBox.left).toBeGreaterThanOrEqual(0);
  expect(expandedLegendBox.right).toBeLessThanOrEqual(390);
  await disorderLegendToggle.press("Space");
  await expect(disorderLegendToggle).toHaveAttribute("aria-expanded", "false");
  await expect(disorderUrgencies).toBeHidden();
  await expect(masterDisorderToggle).toBeChecked();
  expect(await page.evaluate(() => window.__disorderVisibilityChanges)).toBe(0);
  await expect.poll(() => page.locator("#map-legend").evaluate(
    (element) => element.getBoundingClientRect().height,
  )).toBe(collapsedLegend.height);
  await expect(page.locator(".legend-business-layer")).toHaveCount(10);
  await expect(page.locator("#legend-business-layers")).toContainText("Tronçons de lit");
  await expect(page.locator("#legend-business-layers")).toContainText("Éléments morphologiques du lit");
  await expect(page.locator(".legend-mobilier > i")).toHaveCount(1);
  await expect(page.locator(".legend-equipement-mesure > i")).toHaveCount(1);
  await expect(page.locator(".legend-reseau-technique > i")).toHaveCount(2);
  for (const [family, geometryCount] of Object.entries({
    "ouvrage-particulier": 3,
    cheminement: 2,
    vegetation: 3,
    "zone-stockage": 1,
    prestation: 3,
  })) {
    await expect(page.locator(`.legend-${family} > i`)).toHaveCount(geometryCount);
  }
  await expect(page.locator('.map-bottom-controls [id^="open-"][id$="-editor"]')).toHaveCount(0);

  await page.locator("#toggle-create-menu").click();
  await expect(page.locator('.legend-amenagement-hydraulique > i')).toHaveCount(0);
  await expect(page.locator("#create-menu-list [data-create-type]")).toHaveCount(15);
  await expect(page.locator('#create-menu-list [data-create-type="troncons-lit"]')).toHaveCount(1);
  await expect(page.locator('#create-menu-list [data-create-type="elements-morphologiques-lit"]')).toHaveCount(1);
  for (const [resource, dialogId] of [
    ["mobilier", "mobilier-editor-panel"],
    ["equipement-mesure", "equipement-mesure-editor-panel"],
    ["reseau-technique", "reseau-technique-editor-panel"],
    ["ouvrage-particulier", "ouvrage-particulier-editor-panel"],
    ["cheminement", "cheminement-editor-panel"],
    ["vegetation", "vegetation-editor-panel"],
    ["amenagement-hydraulique", "amenagement-hydraulique-editor-panel"],
    ["prestation", "prestation-editor-panel"],
  ]) {
    if (await page.locator("#create-menu-list").isHidden()) {
      await page.locator("#toggle-create-menu").click();
    }
    await page.locator(`[data-create-type="${resource}"]`).click();
    const editor = page.locator(`#${dialogId}`);
    await expect(editor).toBeVisible();
    await expect(editor.locator('.entity-editor-form')).toHaveCount(1);
    await expect(editor.locator('.entity-form-fields')).toHaveCount(1);
    await expect(editor.locator('.entity-geometry-section')).toHaveCount(resource === 'amenagement-hydraulique' ? 0 : 1);
    await expect(editor.locator('.entity-form-actions')).toHaveCount(1);
    expect(await editor.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    const headerAndClose = await page.evaluate(() => {
      const header = document.querySelector('#editor-panel > .editor-title').getBoundingClientRect();
      const close = document.querySelector('#close-editor').getBoundingClientRect();
      return { headerRight: header.right, headerTop: header.top,
        closeRight: close.right, closeTop: close.top };
    });
    expect(Math.abs(headerAndClose.headerRight - headerAndClose.closeRight)).toBeLessThan(16);
    expect(Math.abs(headerAndClose.closeTop - headerAndClose.headerTop)).toBeLessThan(16);
    if (dialogId.endsWith("-panel")) {
      page.once("dialog", dialog => dialog.accept());
      await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
    } else {
      await page.locator(`#${dialogId}`).evaluate((dialog) => dialog.close());
    }
  }

  const state = await page.evaluate(() => {
    const main = document.querySelector("main");
    const workspace = document.querySelector("#app-workspace");
    const panel = document.querySelector("#pwa-settings-panel");
    main.hidden = false;
    main.dataset.layout = "single-tool";
    main.classList.add("settings-open");
    panel.hidden = false;

    const marker = document.createElement("div");
    marker.className = "desordre-point-marker";
    marker.append(document.createElement("span"));
    document.body.append(marker);

    const actions = document.createElement("div");
    actions.className = "tree-row-actions";
    const visibility = document.createElement("input");
    visibility.className = "tree-visibility";
    visibility.type = "checkbox";
    const zoom = document.createElement("button");
    zoom.className = "tree-zoom";
    actions.append(visibility, zoom);
    document.body.append(actions);

    const bounds = (element) => {
      const value = element.getBoundingClientRect();
      return { bottom: value.bottom, left: value.left, right: value.right, top: value.top };
    };
    const markerStyle = getComputedStyle(marker.firstElementChild);
    const actionStyle = getComputedStyle(actions);
    const panelStyle = getComputedStyle(panel);
    const formStyle = getComputedStyle(document.querySelector("#pwa-settings-form"));
    return {
      actionGap: parseFloat(actionStyle.columnGap),
      formOverflowY: formStyle.overflowY,
      markerBackground: markerStyle.backgroundColor,
      markerBorderWidth: markerStyle.borderWidth,
      markerBoxShadow: markerStyle.boxShadow,
      panel: bounds(panel),
      panelOverflow: panelStyle.overflow,
      visibility: bounds(visibility),
      workspace: bounds(workspace),
      zoom: bounds(zoom),
    };
  });

  expect(state.panel.left).toBeGreaterThan(state.workspace.left);
  expect(state.panel.right).toBeLessThan(state.workspace.right);
  expect(state.panel.top).toBeGreaterThan(state.workspace.top);
  expect(state.panel.bottom).toBeLessThan(state.workspace.bottom);
  expect(state.panelOverflow).toBe("hidden");
  expect(state.formOverflowY).toBe("auto");
  expect(state.markerBackground).toBe("rgb(248, 250, 252)");
  expect(state.markerBorderWidth).toBe("2px");
  expect(state.markerBoxShadow).toBe("none");
  expect(state.actionGap).toBeGreaterThanOrEqual(8);
  expect(state.zoom.left - state.visibility.right).toBeGreaterThanOrEqual(8);

  const master = masterDisorderToggle;
  await expect(master).toHaveCount(1);
  await expect(page.locator(".legend-disorder-symbols > i")).toHaveCount(3);
  await master.evaluate((input) => {
    input.checked = false;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect(page.locator("[data-disorder-layer-toggle]:checked")).toHaveCount(0);
});

test("PWA portrait et paysage : formulaires Ouvrage et Végétation gardent leurs actions et brouillons", async ({ context, page }) => {
  await context.addInitScript(() => {
    const nativeMatchMedia = window.matchMedia.bind(window);
    window.matchMedia = query => query === "(display-mode: standalone)"
      ? { matches: true, media: query, addEventListener() {}, removeEventListener() {} }
      : nativeMatchMedia(query);
    window.__gpsWatches = [];
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
      getCurrentPosition() {},
      watchPosition(resolve, reject) {
        window.__gpsWatches.push({ resolve, reject });
        return window.__gpsWatches.length;
      },
      clearWatch() {},
    } });
  });
  await context.route("**/api/**", async route => {
    if (await mockBusinessCRS(route)) return;
    const path = new URL(route.request().url()).pathname;
    const json = path === "/api/config"
      ? { basemap_tilejson_url: "", legacy_single_database_enabled: true,
        show_uuid: false, build_id: "ux-form-3b-landscape" }
      : path === "/api/resource-events/head" ? { cursor: "0" }
        : path === "/api/resource-events" ? { events: [], hasMore: false, nextCursor: "0" }
        : path === "/api/systemes-endiguement" ? { systemes: [] }
        : path === "/api/troncons/options" ? { troncons: [] }
          : path === "/api/desordres/selection" ? { items: [], missing_ids: [] }
            : path === "/api/vegetation/relations/options"
              ? { troncons: [], amenagements_hydrauliques: [], desordres: [] }
              : path.startsWith("/api/referentiels/") ? { references: [] }
        : { type: "FeatureCollection", features: [] };
    await route.fulfill({ json });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  await page.waitForFunction(() => Boolean(window.MapCreateActions && window.PwaGeometryTracking
    && typeof mapViewportReady !== "undefined" && mapViewportReady), null, { timeout: 15000 });

  for (const family of ["ouvrage-particulier", "vegetation"]) {
    await page.locator("#toggle-create-menu").click();
    await page.locator(`[data-create-type="${family}"]`).click();
    const editor = page.locator(`#${family}-editor-panel`);
    await expect(editor).toBeVisible();
    await expect(editor.locator(".entity-form-fields")).toBeVisible();
    await expect(editor.locator("[name=designation]")).toBeEnabled();
    await editor.locator("[name=designation]").fill(`Brouillon ${family}`);
    await editor.locator("[name=geometry_type]").selectOption("LineString");
    const gps = editor.locator(".gps-geometry-tracking");
    await expect(gps.locator('[data-gps="start"]')).toBeVisible();

    for (const viewport of [{ width: 844, height: 390 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await expect(editor).toBeVisible();
      const header = page.locator("#editor-panel > .editor-title");
      await expect(header).toBeVisible();
      for (const selector of ["[data-role=draw]", "[data-role=cancel]", "[data-role=save]",
        '.gps-geometry-tracking [data-gps="start"]']) {
        const control = editor.locator(selector);
        await control.scrollIntoViewIfNeeded();
        const box = await control.boundingBox();
        expect(box.height, selector).toBeGreaterThanOrEqual(40);
        expect(box.y).toBeGreaterThanOrEqual(0);
        expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
      }
      expect(await editor.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    }

    await page.setViewportSize({ width: 844, height: 260 });
    await editor.locator("[name=designation]").focus();
    const saveWithReducedViewport = editor.locator("[data-role=save]");
    await saveWithReducedViewport.scrollIntoViewIfNeeded();
    const saveBox = await saveWithReducedViewport.boundingBox();
    expect(saveBox.y).toBeGreaterThanOrEqual(0);
    expect(saveBox.y + saveBox.height).toBeLessThanOrEqual(261);

    await page.setViewportSize({ width: 844, height: 390 });
    await editor.locator("[data-role=draw]").click();
    await expect(editor.locator("[data-role=geometry-status]")).toContainText("0 sommet");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator("#editor-map-interaction")).toBeVisible();
    await page.locator("#cancel-editor-map-interaction").click();
    await expect(editor).toBeVisible();
    await expect(editor.locator("[name=designation]")).toHaveValue(`Brouillon ${family}`);
    await expect(editor.locator("[data-role=geometry-status]")).toContainText("Aucune géométrie");

    await gps.locator('[data-gps="start"]').click();
    await expect.poll(() => page.evaluate(() => window.__gpsWatches.length)).toBeGreaterThan(0);
    await page.setViewportSize({ width: 844, height: 390 });
    const floatingGps = page.locator(".gps-geometry-tracking--floating");
    await expect(floatingGps.locator('[data-gps="pause"]')).toBeVisible();
    await floatingGps.locator('[data-gps="pause"]').click();
    await expect(floatingGps.locator('[data-gps="cancel"]')).toBeVisible();
    await floatingGps.locator('[data-gps="cancel"]').click();
    await expect(editor).toBeVisible();
    await expect(editor.locator("[data-role=geometry-status]")).toContainText("Aucune géométrie");
    page.once("dialog", dialog => dialog.accept());
    await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
    await expect(page.locator("#editor-panel")).toBeHidden();
  }
});
