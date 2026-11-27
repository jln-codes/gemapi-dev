const { expect, test } = require("@playwright/test");

const POSITION_TOLERANCE = 2;
const integratedFixture = process.env.DIGUES_INT2_FIXTURE
  ? JSON.parse(require('node:fs').readFileSync(process.env.DIGUES_INT2_FIXTURE, 'utf8')) : null;
const contextUrl = path => integratedFixture ? `${path}?base=base_0` : path;

test.beforeEach(async ({ context, page }) => {
  // Docking tests exercise a real local backend, never an external tile supplier.
  await context.route(/^https?:\/\/(?!127\.0\.0\.1(?::|\/)|localhost(?::|\/))/, route => route.abort());
  if (integratedFixture) {
    const reset = await context.request.post('/int2/reset-login-attempts', {
      headers: { Authorization: `Bearer ${integratedFixture.login_reset_token}` },
    });
    expect(reset.status()).toBe(204);
    await page.goto(contextUrl('/carte'));
    await page.locator('#identity-gate').getByLabel('Identifiant', { exact: true }).fill('editor_alpha');
    await page.locator('#identity-gate').getByLabel('Mot de passe', { exact: true }).fill(integratedFixture.password);
    await page.getByRole('button', {name:'Se connecter',exact:true}).click();
    await expect(page.locator('#identity-gate')).toBeHidden();
  }
});

async function rectangle(locator) {
  return locator.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return {
      bottom: bounds.bottom,
      height: bounds.height,
      left: bounds.left,
      right: bounds.right,
      top: bounds.top,
      width: bounds.width,
    };
  });
}

function expectApproximately(actual, expected) {
  expect(Math.abs(actual - expected)).toBeLessThanOrEqual(POSITION_TOLERANCE);
}

async function openEditor(page) {
  await page.waitForFunction(() => typeof topNavigation !== "undefined"
    && topNavigation.getState().create === false);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await expect(page.locator("#editor-panel")).toBeVisible();
}

async function installLeafletInvalidationProbe(page) {
  await page.evaluate(() => {
    const originalInvalidateSize = window.L.Map.prototype.invalidateSize;
    window.__panelLayoutInvalidations = { calls: 0, samples: [] };
    window.L.Map.prototype.invalidateSize = function invalidateSizeProbe(...args) {
      const result = originalInvalidateSize.apply(this, args);
      const container = this.getContainer();
      window.__panelLayoutInvalidations.calls += 1;
      window.__panelLayoutInvalidations.samples.push({
        actualWidth: container.clientWidth,
        leafletWidth: this.getSize().x,
      });
      return result;
    };
  });
}

async function invalidationCount(page) {
  return page.evaluate(() => window.__panelLayoutInvalidations.calls);
}

async function expectLeafletInvalidatedAfter(page, previousCount) {
  await expect.poll(() => invalidationCount(page)).toBeGreaterThan(previousCount);
  const sample = await page.evaluate(() => (
    window.__panelLayoutInvalidations.samples.at(-1)
  ));
  expect(Math.abs(sample.leafletWidth - sample.actualWidth)).toBeLessThanOrEqual(1);
}

function collectBusinessWrites(page) {
  const writes = [];
  page.on("request", (request) => {
    // The data view uses POST for its read-only, structured query endpoint.
    const readQuery = /\/api\/(?:bases\/[^/]+\/)?data\/(?:desordres\/query|view-query)$/.test(new URL(request.url()).pathname);
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method()) && !readQuery) {
      writes.push({ method: request.method(), url: request.url() });
    }
  });
  return writes;
}

test("Patrimoine recouvre la carte sans la redimensionner et l’Éditeur reste docké", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(contextUrl("/carte"));
  const map = page.locator("#map");
  await expect(map).toBeVisible();
  await expect(page.locator("main")).toHaveAttribute("data-layout", "split-docked");
  const writes = collectBusinessWrites(page);
  await installLeafletInvalidationProbe(page);

  const initialMap = await rectangle(map);

  let calls = await invalidationCount(page);
  await page.locator("#toggle-heritage").click();
  const heritage = page.locator("#heritage-panel");
  await expect(heritage).toBeVisible();
  await expectLeafletInvalidatedAfter(page, calls);
  const heritageMap = await rectangle(map);
  const heritageBounds = await rectangle(heritage);
  expectApproximately(heritageMap.left, initialMap.left);
  expectApproximately(heritageMap.right, initialMap.right);
  expect(heritageBounds.right).toBeGreaterThan(heritageMap.left);
  expect(heritageBounds.right).toBeLessThan(heritageMap.right);

  calls = await invalidationCount(page);
  await page.locator("#close-heritage").click();
  await expect(heritage).toBeHidden();
  await expectLeafletInvalidatedAfter(page, calls);
  const afterHeritage = await rectangle(map);
  expectApproximately(afterHeritage.left, initialMap.left);
  expectApproximately(afterHeritage.right, initialMap.right);

  calls = await invalidationCount(page);
  await openEditor(page);
  const editor = page.locator("#editor-panel");
  await expectLeafletInvalidatedAfter(page, calls);
  const editorMap = await rectangle(map);
  const editorBounds = await rectangle(editor);
  expectApproximately(editorMap.left, initialMap.left);
  expect(editorMap.right).toBeLessThan(initialMap.right);
  expect(editorMap.right).toBeLessThanOrEqual(editorBounds.left + POSITION_TOLERANCE);

  calls = await invalidationCount(page);
  await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
  await expect(editor).toBeHidden();
  await expectLeafletInvalidatedAfter(page, calls);
  const afterEditor = await rectangle(map);
  expectApproximately(afterEditor.left, initialMap.left);
  expectApproximately(afterEditor.right, initialMap.right);

  await page.locator("#toggle-heritage").click();
  await expect(heritage).toBeVisible();
  calls = await invalidationCount(page);
  await openEditor(page);
  await expectLeafletInvalidatedAfter(page, calls);
  await expect(page.locator("main")).toHaveClass(/heritage-open/);
  await expect(page.locator("main")).toHaveClass(/editor-open/);

  const combinedMap = await rectangle(map);
  const combinedHeritage = await rectangle(heritage);
  const combinedEditor = await rectangle(editor);
  expect(combinedMap.width).toBeGreaterThan(0);
  expectApproximately(combinedMap.left, editorMap.left);
  expectApproximately(combinedMap.right, editorMap.right);
  expect(combinedHeritage.right).toBeGreaterThan(combinedMap.left);
  expect(combinedMap.right).toBeLessThanOrEqual(
    combinedEditor.left + POSITION_TOLERANCE,
  );
  await expect(map).toHaveJSProperty("inert", false);

  await page.locator("#editor-panel .entity-form__header [data-role=close]:visible").click();
  await page.locator("#close-heritage").click();
  expect(writes).toEqual([]);
});

test("Assistant IA conserve son docking et son exclusivité sur la Carte", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(contextUrl("/carte"));
  const map = page.locator("#map");
  await expect(map).toBeVisible();
  const writes = collectBusinessWrites(page);
  await installLeafletInvalidationProbe(page);
  const initialMap = await rectangle(map);

  await page.locator("#toggle-heritage").click();
  await openEditor(page);
  const calls = await invalidationCount(page);
  await page.locator("#toggle-ai").click();
  await expect(page.locator("#ai-panel")).toBeVisible();
  await expect(page.locator("#heritage-panel")).toBeHidden();
  await expect(page.locator("#editor-panel")).toBeHidden();
  await expect(page.locator("main")).toHaveClass(/ai-open/);
  await expect(page.locator("main")).not.toHaveClass(/heritage-open/);
  await expect(page.locator("main")).not.toHaveClass(/editor-open/);
  await expectLeafletInvalidatedAfter(page, calls);

  const aiMap = await rectangle(map);
  const aiBounds = await rectangle(page.locator("#ai-panel"));
  expectApproximately(aiMap.left, initialMap.left);
  expect(aiMap.right).toBeLessThan(initialMap.right);
  expect(aiMap.right).toBeLessThanOrEqual(aiBounds.left + POSITION_TOLERANCE);

  const closeCalls = await invalidationCount(page);
  await page.locator("#close-ai").click();
  await expect(page.locator("#ai-panel")).toBeHidden();
  await expectLeafletInvalidatedAfter(page, closeCalls);
  const restoredMap = await rectangle(map);
  expectApproximately(restoredMap.left, initialMap.left);
  expectApproximately(restoredMap.right, initialMap.right);
  expect(writes).toEqual([]);
});

test("Patrimoine se superpose à Base et conserve la compatibilité avec l’Assistant IA", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.goto(contextUrl("/base"));
  await page.waitForFunction(() => document.querySelector("main")?.dataset.layout !== undefined);
  await expect(page.locator("#data-statistics-view")).toBeVisible();
  await page.locator("#toggle-views").click();
  const content = page.locator("#data-view-builder-workspace");
  await expect(content).toBeVisible();
  const writes = collectBusinessWrites(page);
  const initialContent = await rectangle(content);

  await page.locator("#toggle-heritage").click();
  await expect(page.locator("#heritage-panel")).toBeVisible();
  const heritageContent = await rectangle(content);
  expectApproximately(heritageContent.left, initialContent.left);
  expectApproximately(heritageContent.right, initialContent.right);

  await page.locator("#toggle-ai").click();
  await expect(page.locator("#heritage-panel")).toBeVisible();
  await expect(page.locator("#ai-panel")).toBeVisible();
  const combinedContent = await rectangle(content);
  expectApproximately(combinedContent.left, initialContent.left);
  expect(combinedContent.right).toBeLessThan(heritageContent.right);

  await page.locator("#close-ai").click();
  await page.locator("#close-heritage").click();
  const restoredContent = await rectangle(content);
  expectApproximately(restoredContent.left, initialContent.left);
  expectApproximately(restoredContent.right, initialContent.right);
  await page.locator("#show-statistics").click();
  await expect(content).toBeHidden();
  await expect(page.locator("#data-statistics-view")).toBeVisible();
  expect(writes).toEqual([]);
});
