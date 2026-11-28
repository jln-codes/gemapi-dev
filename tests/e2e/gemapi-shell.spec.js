const { test, expect } = require("@playwright/test");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

test.beforeEach(async ({ context, baseURL }) => {
  const origin = new URL(baseURL).origin;
  // Qualification sur réseau local, y compris quand le fond de secours est choisi.
  await context.route("**/*", route =>
    new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
});

test("Carte, Base et branding depuis le proxy", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(error.message));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Connexion" })).toBeVisible();
  await page.goto("/carte");
  await expect(page.getByRole("img", { name: "gemapi.dev" })).toBeVisible();
  await expect(page.locator(".leaflet-container")).toBeVisible();
  await page.getByRole("button", { name: "Fonds de carte" }).click();
  await expect(page.getByRole("heading", { name: "Fond cartographique", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Fonds de carte" })).toBeFocused();
  await page.getByRole("link", { name: "Base", exact: true }).click();
  await expect(page).toHaveURL(/\/base$/);
  await expect(page.getByRole("heading", { name: "Accès aux données" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Carte", exact: true })).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test("Le manifeste et les ressources de marque conservent leurs octets", async ({ request }) => {
  const manifestResponse = await request.get("/branding/site.webmanifest");
  expect(manifestResponse.headers()["content-type"]).toContain("application/manifest+json");
  const manifest = await manifestResponse.json();
  expect(manifest).toMatchObject({ name: "gemapi.dev", id: "/", start_url: "/", scope: "/", display: "standalone" });
  expect(manifest.icons.map(icon => [icon.sizes, icon.purpose])).toEqual([
    ["192x192", "any"], ["512x512", "any"], ["192x192", "maskable"], ["512x512", "maskable"],
  ]);
  for (const relative of ["cartouche.png", "favicon.ico", "favicon-16x16.png",
    "favicon-32x32.png", "apple-touch-icon.png", ...manifest.icons.map(icon => icon.src)]) {
    const response = await request.get("/branding/" + relative);
    expect(response.status()).toBe(200);
    expect(await response.body()).toEqual(readFileSync(resolve("branding", relative)));
  }
});

test("Le shell et le cartouche s’ouvrent hors ligne en vue étroite", async ({ context, page }) => {
  await page.setViewportSize({ width: 360, height: 760 });
  await page.goto("/carte");
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
  const image = page.getByRole("img", { name: "gemapi.dev" });
  await expect(image).toBeVisible();
  const bounds = await image.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(360);
  expect(bounds.width / bounds.height).toBeCloseTo(3, 1);
  await expect(page.getByRole("link", { name: "Base", exact: true })).toBeHidden();

  await context.setOffline(true);
  const offline = await context.newPage();
  await offline.goto("/");
  await expect(offline.getByRole("img", { name: "gemapi.dev" })).toBeVisible();
  expect(await offline.getByRole("img", { name: "gemapi.dev" })
    .evaluate(img => img.complete && img.naturalWidth)).toBe(1200);
  await expect(offline.locator(".leaflet-container")).toBeVisible();
  await expect(offline.evaluate(() => fetch("/api/base-summary").then(() => false, () => true)))
    .resolves.toBe(true);
});

test("Le suivi GPS local s’arrête sur demande, hors connexion", async ({ context, page }) => {
  await context.addInitScript(() => {
    const matchMedia = window.matchMedia.bind(window);
    window.matchMedia = query => query === "(display-mode: standalone)"
      ? { matches: true, addEventListener() {} } : matchMedia(query);
    navigator.geolocation.watchPosition = success => {
      queueMicrotask(() => success({
        coords: { latitude: 46.8, longitude: 2.5, accuracy: 8 }, timestamp: Date.now(),
      }));
      return 21;
    };
    navigator.geolocation.clearWatch = id => { window.clearedWatch = id; };
  });
  await page.goto("/carte");
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
  await context.setOffline(true);
  const gps = page.getByRole("button", { name: "Suivre ma position" });
  await gps.click();
  await expect(page.locator(".user-position-marker")).toBeVisible();
  await expect(gps).toHaveAttribute("aria-pressed", "true");
  await gps.click();
  await expect(page.locator(".user-position-marker")).toHaveCount(0);
  expect(await page.evaluate(() => window.clearedWatch)).toBe(21);
});

test("Le proxy garde les services internes et le métier fermés", async ({ request }) => {
  for (const path of ["/internal/cache-admin", "/basemap/internal/cache-admin",
    "/api/bases/autre/troncons", "/api/auth/databases"]) {
    expect((await request.get(path)).status(), path).toBe(404);
  }
  for (const path of ["/api/base-summary", "/api/photos/123/content", "/api/resource-events/stream"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(403);
    expect(response.headers()["cache-control"]).toBe("no-store");
  }
});

test("Un refus GPS ferme le suivi et reste visible", async ({ context, page }) => {
  await context.addInitScript(() => {
    const matchMedia = window.matchMedia.bind(window);
    window.matchMedia = query => query === "(display-mode: standalone)"
      ? { matches: true, addEventListener() {} } : matchMedia(query);
    navigator.geolocation.watchPosition = (_success, failure) => {
      queueMicrotask(() => failure({ code: 1 }));
      return 23;
    };
    navigator.geolocation.clearWatch = id => { window.clearedWatch = id; };
  });
  await page.goto("/carte");
  const gps = page.getByRole("button", { name: "Suivre ma position" });
  await gps.click();
  await expect(page.locator("#user-position-status")).toHaveText("Accès à la position refusé.");
  await expect(gps).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".user-position-marker")).toHaveCount(0);
  expect(await page.evaluate(() => window.clearedWatch)).toBe(23);
});

test("Leaflet.Editable annule un polygone synthétique", async ({ page }) => {
  await page.goto("/carte");
  const result = await page.evaluate(() => {
    const container = document.createElement("div");
    container.style.cssText = "width: 420px; height: 320px;";
    document.body.append(container);
    const map = L.map(container, { editable: true }).setView([46.8, 2.5], 12);
    let cancelled = 0;
    map.on("editable:drawing:cancel", () => { cancelled++; });
    map.editTools.startPolygon(map.getCenter());
    const drawing = map.editTools.drawing();
    map.editTools.stopDrawing();
    const result = { drawing, cancelled, drawingAfter: Boolean(map.editTools.drawing()) };
    map.remove();
    container.remove();
    return result;
  });
  expect(result).toEqual({ drawing: true, cancelled: 1, drawingAfter: false });
});

test("Le bus transmet une invalidation synthétique entre Carte et Base", async ({ context, page }) => {
  await page.goto("/carte");
  const base = await context.newPage();
  await base.goto("/base");
  const identity = {
    instance_id: "10000000-0000-4000-8000-000000000001",
    user_id: "20000000-0000-4000-8000-000000000001",
    data_epoch: "30000000-0000-4000-8000-000000000001",
    permissions: ["business.read"],
  };
  for (const [tab, source] of [[page, "map"], [base, "data"]]) {
    await tab.addScriptTag({ url: "/static/js/business-context.js" });
    await tab.evaluate(async ({ identity, source }) => {
      window.BusinessRuntime = BusinessContext.createRuntime({ context: identity, origin: location.origin });
      const script = document.createElement("script");
      script.src = "/static/js/inter-page-bus.js";
      script.dataset.source = source;
      await new Promise((resolve, reject) => {
        script.onload = resolve;
        script.onerror = reject;
        document.body.append(script);
      });
    }, { identity, source });
  }
  await base.evaluate(() => {
    window.invalidation = null;
    InterPageBus.subscribe("qualification:invalidation", (payload, message) => {
      window.invalidation = { payload, source: message.source };
    });
  });
  await page.evaluate(() => InterPageBus.publish("qualification:invalidation", { id: "synthétique" }));
  await expect.poll(() => base.evaluate(() => window.invalidation)).toEqual({
    payload: { id: "synthétique" }, source: "map",
  });
});
