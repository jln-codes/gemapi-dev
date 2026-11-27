const { expect, test } = require("@playwright/test");

test.use({ serviceWorkers: "block" });

// Layout only: an explicit local read fixture, independent of PostgreSQL and
// authentication setup. These tests do not qualify business/API permissions.
const { mockBusinessCRS } = require('./mock-business-crs');
const { mockResourceJournal } = require('./mock-resource-journal');
test.beforeEach(async ({ context }) => {
  await context.route(/^https?:\/\/(?!127\.0\.0\.1(?::|\/)|localhost(?::|\/))/, route => route.abort());
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    const path = new URL(route.request().url()).pathname;
    const json = path === '/api/config'
      ? { legacy_single_database_enabled: true, show_uuid: false }
      : path === '/api/systemes-endiguement' ? { systemes: [] }
      : { type: 'FeatureCollection', features: [] };
    await route.fulfill({ json });
  });
});

async function expectViewportLayout(page) {
  const bounds = await page.evaluate(() => {
    const rectangle = (selector) => {
      const { top, bottom } = document.querySelector(selector).getBoundingClientRect();
      return { top, bottom };
    };
    return {
      header: rectangle(".app-header"),
      notifications: rectangle(".header-notifications"),
      workspace: rectangle("#app-workspace"),
      viewportBottom: document.documentElement.clientHeight,
    };
  });

  expect(bounds.header.top).toBe(0);
  // Notifications float over the workspace without reserving a header row.
  expect(bounds.notifications.top).toBeGreaterThanOrEqual(bounds.header.bottom);
  expect(bounds.workspace.top).toBe(bounds.header.bottom);
  expect(bounds.workspace.bottom).toBe(bounds.viewportBottom);
}

for (const { name, viewport, standalone } of [
  { name: "portrait web", viewport: { width: 390, height: 844 }, standalone: false },
  { name: "paysage web", viewport: { width: 844, height: 390 }, standalone: false },
  { name: "portrait standalone simulé", viewport: { width: 390, height: 844 }, standalone: true },
]) {
  test(`la carte remplit le viewport en ${name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    if (standalone) {
      await page.addInitScript(() => {
        Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
        const originalMatchMedia = window.matchMedia.bind(window);
        window.matchMedia = (query) => query === "(display-mode: standalone)"
          ? { matches: true, media: query, addEventListener() {}, removeEventListener() {} }
          : originalMatchMedia(query);
      });
    }

    await page.goto("/carte");
    await expect(page.locator(".app-header")).toBeVisible();
    await expect(page.locator("#gps-location-icon")).toHaveCount(1);
    await expectViewportLayout(page);

    if (viewport.width === 390) {
      await page.waitForFunction(() => typeof topNavigation !== "undefined"
        && topNavigation.getState().heritage === false);
      await page.locator("#toggle-heritage").click();
      await expect(page.locator("main")).toHaveAttribute("data-layout", "single-tool");
      await expect(page.locator("#heritage-panel")).toBeVisible();
      await expectViewportLayout(page);
      const panelBottom = await page.locator("#heritage-panel").evaluate((panel) => (
        panel.getBoundingClientRect().bottom
      ));
      expect(panelBottom).toBe(await page.evaluate(() => document.documentElement.clientHeight));
    }
  });
}
