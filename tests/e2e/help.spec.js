const { expect, test } = require("@playwright/test");

test.use({ serviceWorkers: "block" });

for (const { name, viewport, standalone } of [
  { name: "desktop", viewport: { width: 1280, height: 800 }, standalone: false },
  { name: "mobile PWA", viewport: { width: 390, height: 844 }, standalone: true },
]) {
  test(`l’aide permet de consulter un document et de revenir à la liste sur ${name}`, async ({ page }) => {
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
    await page.locator("#toggle-help").click();

    const modal = page.locator("#help-modal");
    await expect(modal).toBeVisible();
    await expect(page.locator("#help-document-list button", { hasText: "Présentation générale" })).toBeVisible();
    await page.locator("#help-document-list button", { hasText: "Présentation générale" }).click();
    await expect(page.locator("#help-markdown h1").first()).toHaveText("Digues App");

    await page.locator("#help-back").click();
    await expect(page.locator("#help-document-list")).toBeVisible();

    if (viewport.width < 620) {
      const bounds = await modal.locator(".help-dialog").evaluate((dialog) => {
        const rect = dialog.getBoundingClientRect();
        return { top: rect.top, left: rect.left, width: rect.width, height: rect.height };
      });
      expect(bounds.top).toBe(0);
      expect(bounds.left).toBe(0);
      expect(bounds.width).toBe(viewport.width);
      expect(bounds.height).toBe(viewport.height);
    }
  });
}

test("les liens autorisés restent internes à l’aide et les autres sont neutralisés", async ({ page }) => {
  await page.goto("/carte");
  await page.locator("#toggle-help").click();
  await page.locator("#help-document-list button", { hasText: "Présentation générale" }).click();

  const allowed = page.getByRole('link', { name: 'Déploiement serveur Compose', exact: true });
  await expect(allowed).toHaveAttribute("data-help-document", /^[0-9a-f]{16}$/);
  await allowed.click();
  await expect(page.locator("#help-document-title")).toContainText("Déploiement serveur");

  await page.locator("#help-back").click();
  await page.locator("#help-document-list button", { hasText: "Présentation générale" }).click();
  const excluded = page.locator('#help-markdown a', { hasText: 'règles de travail' }).first();
  await expect(excluded).toHaveAttribute("aria-disabled", "true");
  await expect(excluded).not.toHaveAttribute("href", /.+/);
});
