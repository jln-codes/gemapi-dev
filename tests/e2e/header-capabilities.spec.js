const { businessCRS, mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require("@playwright/test");
const { mockResourceJournal } = require("./mock-resource-journal");
const DefaultView = require('../../webapp/frontend/js/data-view-default.js');

const emptyCollection = { type: "FeatureCollection", features: [] };
const tenantId = (value) => `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const TENANT_DATABASE_ID = tenantId(11);
const TENANT_DATABASE_SLUG = "base_0";

async function mockMultiTenantApplication(context, { delegation = false } = {}) {
  const state = { offline: false, backendUnavailable: false, httpError: false, headCalls: 0, holdNextHead: null };
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    const url = new URL(route.request().url());
    const path = url.pathname;
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (path === "/api/config") {
      return send({ legacy_single_database_enabled: false, build_id: "header-layout", show_uuid: false });
    }
    if (path === "/api/auth/me") {
      return send({ id: tenantId(1), login: "terrain", display_name: "Camille Terrain", status: "active", is_superadmin: false });
    }
    if (path === "/api/auth/databases") {
      return send({
        instance_id: tenantId(99),
        user_id: tenantId(1),
        expires_at: new Date(Date.now() + 3600000).toISOString(),
        databases: [{
          business_database_id: TENANT_DATABASE_ID,
          slug: TENANT_DATABASE_SLUG,
          structure_id: tenantId(50),
          structure_name: "Structure de démonstration",
          display_name: "Base métier de démonstration",
          data_epoch: tenantId(100),
          status: "active",
          role_code: delegation ? 'database_admin' : 'editor',
          permissions: [
            "business.read", "business.create", "business.update", "sql.read", "ai.read",
            "exports.read", "offline.prepare", "offline.write",
            ...(delegation ? ['members.manage'] : []),
          ],
        }],
      });
    }
    const scoped = path.match(/^\/api\/bases\/[^/]+(\/.*)$/)?.[1];
    if (!scoped) return send({ detail: "Route inconnue" }, 404);
    if (scoped === "/resource-events/head") {
      state.headCalls++;
      if (state.holdNextHead) {
        const hold = state.holdNextHead;
        state.holdNextHead = null;
        await hold;
      }
    }
    if (state.offline) return route.abort("internetdisconnected");
    if (state.httpError && scoped === "/troncons") return send({ detail: "Erreur ponctuelle" }, 503);
    if (scoped === "/base-summary") {
      return send({ crs: businessCRS() });
    }
    if (scoped.startsWith("/resource-events")) {
      if (scoped.endsWith("/stream")) {
        return route.fulfill({ contentType: "text/event-stream", body: ": header layout\n\n" });
      }
      if (scoped.endsWith("/head")) {
        if (state.backendUnavailable) return send({ detail: "Backend indisponible" }, 503);
        return send({ cursor: "0" });
      }
      return send({ events: [], nextCursor: "0", hasMore: false });
    }
    if (scoped === "/systemes-endiguement") return send({ systemes: [] });
    if (scoped === "/data/desordres/query") {
      return send({ columns: [], rows: [], total: 0, offset: 0, has_next: false });
    }
    if (scoped.startsWith("/referentiels/")) {
      return send({ references: [], types: [], categories: [] });
    }
    return send(emptyCollection);
  });
  return state;
}

// UI uses local HTTP fixtures; real delegation/CSRF/isolation are proved by the
// PostgreSQL HTTP suite. This scenario protects drafts, revisions and paging.
test('database delegation: session entry, 409 draft, paging and responsive actions', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, serviceWorkers: 'block' });
  await mockMultiTenantApplication(context, { delegation: true });
  const revision = '2026-11-11T12:00:00+00:00';
  const members = Array.from({ length: 55 }, (_, index) => ({
    user_id: tenantId(200 + index), target_id: TENANT_DATABASE_ID,
    display_name: `Utilisateur ${index}`, login: `user${index}`, role: 'reader', status: 'active', updated_at: revision,
  }));
  let conflict = true; const mutations = [];
  await context.route('**/api/bases/*/memberships**', route => {
    const request = route.request();
    if (request.method() === 'GET') return route.fulfill({ json: { memberships:
      new URL(request.url()).searchParams.has('after') ? members.slice(50) : members.slice(0, 50) } });
    mutations.push(request.postDataJSON());
    if (request.method() === 'PATCH' && conflict) return route.fulfill({ status: 409, json: { code: 'revision_conflict' } });
    return route.fulfill({ status: request.method() === 'POST' ? 201 : 200,
      json: { updated_at: '2026-11-11T12:01:00+00:00' } });
  });
  await context.route('**/api/bases/*/membership-candidates/*', route => route.fulfill({ json: {
    id: tenantId(300), display_name: 'Compte vérifié', login: 'existing',
  } }));
  const page = await context.newPage();
  try {
    await page.goto(`/carte?base=${TENANT_DATABASE_SLUG}`);
    await expect(page.locator('main')).toHaveJSProperty('inert', false);
    await page.locator('#open-business-session').click();
    const panel = page.locator('.database-access');
    await panel.locator(':scope > summary').click();
    await expect(panel.locator('tbody > tr')).toHaveCount(50);
    const first = panel.locator('tbody > tr').first();
    await first.locator('summary').click();
    await first.getByLabel('Rôle dans la base').selectOption('editor');
    await first.getByRole('button', { name: 'Enregistrer', exact: true }).click();
    await expect(panel.getByRole('status')).toContainText('Saisie conservée');
    await expect(first.getByLabel('Rôle dans la base')).toHaveValue('editor');
    await expect(first.locator('details')).toHaveAttribute('open', '');
    await first.evaluate(node => { node.dataset.preserved = 'yes'; });
    await panel.getByRole('button', { name: 'Charger la suite' }).click();
    await expect(panel.locator('tbody > tr')).toHaveCount(55);
    await expect(first).toHaveAttribute('data-preserved', 'yes');
    for (const [width, height] of [[1440, 900], [1000, 800], [390, 844], [844, 390]]) {
      await page.setViewportSize({ width, height });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await first.getByRole('button', { name: 'Enregistrer', exact: true }).scrollIntoViewIfNeeded();
      await expect(first.getByRole('button', { name: 'Enregistrer', exact: true })).toBeInViewport();
    }
    conflict = false;
    await first.getByRole('button', { name: 'Enregistrer', exact: true }).click();
    await expect(panel.getByRole('status')).toHaveText('Accès enregistré.');
    const addition = panel.getByRole('form', { name: 'Ajouter un accès à la base', exact: true });
    await addition.getByLabel('Utilisateur existant (UUID)').fill(tenantId(300));
    await addition.getByLabel('Rôle dans la base').selectOption('database_admin');
    page.once('dialog', dialog => { expect(dialog.message()).toContain('Compte vérifié (existing)'); return dialog.accept(); });
    await addition.getByRole('button', { name: 'Vérifier et ajouter' }).click();
    await expect(panel.getByRole('status')).toContainText('Accès ajouté.');
    expect(mutations).toEqual([
      { role: 'editor', status: 'active', expected_revision: revision },
      { role: 'editor', status: 'active', expected_revision: revision },
      { user_id: tenantId(300), role: 'database_admin' },
    ]);
  } finally { await context.close(); }
});

function intersects(first, second) {
  return first.left < second.right && first.right > second.left
    && first.top < second.bottom && first.bottom > second.top;
}

async function layoutBoxes(page) {
  await expect(page.locator('main')).toHaveJSProperty('inert', false);
  return page.evaluate(() => {
    const box = (selector) => {
      const rect = document.querySelector(selector).getBoundingClientRect();
      return {
        bottom: rect.bottom,
        height: rect.height,
        left: rect.left,
        right: rect.right,
        top: rect.top,
        width: rect.width,
      };
    };
    return {
      header: box(".app-header"),
      navigation: box(".header-navigation"),
      actions: box(".header-actions"),
      notifications: box(".header-notifications"),
      backendPanel: box(".backend-status-panel"),
      offlinePanel: box(".offline-status-panel"),
      notice: box("#offline-notice"),
      status: box("#status"),
      workspace: box("#app-workspace"),
      map: document.querySelector("#map") ? box("#map") : null,
    };
  });
}

function expectSameRect(actual, expected) {
  for (const edge of ["top", "left", "width", "height"]) {
    expect(actual[edge]).toBeCloseTo(expected[edge], 5);
  }
}

async function expectTransparentOverlayPassesThrough(page) {
  const target = await page.evaluate(() => {
    const header = document.querySelector(".app-header").getBoundingClientRect();
    const overlay = document.querySelector(".header-notifications").getBoundingClientRect();
    const panel = document.querySelector(".backend-status-panel").getBoundingClientRect();
    const element = document.elementFromPoint(panel.left + panel.width / 2, panel.bottom + 2);
    return {
      inWorkspace: Boolean(element?.closest("#app-workspace")),
      overlayPointerEvents: getComputedStyle(document.querySelector(".header-notifications")).pointerEvents,
    };
  });
  expect(target.overlayPointerEvents).toBe("none");
  expect(target.inWorkspace).toBe(true);
}

async function expectMenuBelowHeader(page, toggle, menu) {
  await page.locator(toggle).click();
  await expect(page.locator(menu)).toBeVisible();
  await expect.poll(() => page.locator(menu).evaluate(element => {
    const box = element.getBoundingClientRect();
    const header = document.querySelector('.app-header').getBoundingClientRect();
    return box.top >= header.bottom - 1 && element.contains(
      document.elementFromPoint(box.left + 12, box.top + 12));
  })).toBe(true);
  await page.locator(toggle).click();
}

function expectOverlayLayout(boxes) {
  expect(boxes.workspace.top).toBe(boxes.header.bottom);
  expect(boxes.workspace.height).toBeGreaterThan(0);
  expect(boxes.notifications.top).toBeGreaterThanOrEqual(boxes.header.bottom);
  expect(intersects(boxes.notifications, boxes.workspace)).toBe(true);
}

async function exposeOfflineLayout(page) {
  await page.locator("#offline-notice").waitFor({ state: "attached" });
  await page.evaluate(() => {
    const status = document.querySelector("#status");
    status.textContent = "Hors connexion : aucun périmètre préparé sur cet appareil.";
    status.classList.add("error");
    const notice = document.querySelector("#offline-notice");
    notice.textContent = "Hors connexion — périmètre préparé uniquement ; fraîcheur non garantie. Photos et fichiers enregistrés sur cet appareil disponibles, sans confirmation serveur. Fond de carte et autres fichiers Photo non préparés.";
    document.querySelector(".offline-status-panel").hidden = false;
  });
}

async function mockApplication(context) {
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const url = new URL(route.request().url());
    let response = {};
    if (url.pathname === "/api/config") response = { legacy_single_database_enabled: true, show_uuid: false };
    else if (url.pathname === "/api/base-summary") {
      response = { crs: businessCRS() };
    }
    else if (url.pathname === "/api/systemes-endiguement") response = { systemes: [] };
    else if (["/api/troncons", "/api/desordres", "/api/territoire-administratif"].includes(url.pathname)) {
      response = emptyCollection;
    } else if (url.pathname === "/api/data/desordres/query") {
      response = { columns: [], rows: [], total: 0, offset: 0, has_next: false };
    }
    else if (url.pathname === "/api/data/view-catalog") response = {...DefaultView.catalog(),local_only:false};
    else if (url.pathname === "/api/data/view-query") response = {columns:[],rows:[],total:0,has_next:false};
    await route.fulfill({ json: response });
  });
}

async function navigationOrder(page) {
  return page.locator(".main-navigation").evaluate((navigation) => (
    [...navigation.children].filter((element) => !element.hidden).map((element) => {
      if (element.id === "show-statistics") return "statistics";
      if (element.matches(".create-menu")) return "create";
      if (element.matches(".tools-menu")) return "tools";
      if (element.id === "toggle-views") return "views";
      if (element.id === "toggle-queries") return "queries";
      if (element.matches(".settings-menu")) return "settings";
      if (element.id === "toggle-pwa-settings") return "pwa-settings";
      if (element.id === "toggle-ai") return "ai";
      return element.id || element.tagName;
    })
  ));
}

async function headerActionGeometry(page) {
  await expect(page.locator('main')).toHaveJSProperty('inert', false);
  return page.evaluate(() => {
    const rect = selector => {
      const { left, right, top, height, width } = document.querySelector(selector).getBoundingClientRect();
      return { left, right, top, height, width };
    };
    const viewport = document.querySelector(".app-navigation-viewport");
    const network = document.querySelector("#network-status");
    const database = document.querySelector("#open-business-session");
    const toggle = document.querySelector(".view-toggle");
    const centerHits = button => {
      const box = button.getBoundingClientRect();
      return button.contains(document.elementFromPoint(
        box.left + box.width / 2, box.top + box.height / 2,
      ));
    };
    return {
      header: rect(".app-header"),
      navigation: rect(".header-navigation"),
      actions: rect(".header-actions"),
      toggle: rect(".view-toggle"),
      toggleHidden: toggle.hidden,
      network: rect("#network-status"),
      networkSvg: rect("#network-status svg:not([hidden])"),
      database: rect("#open-business-session"),
      databaseSvg: rect("#open-business-session svg"),
      scrollWidth: viewport.scrollWidth,
      clientWidth: viewport.clientWidth,
      overflowX: getComputedStyle(viewport).overflowX,
      networkHit: centerHits(network),
      databaseHit: centerHits(database),
      toggleHit: toggle.hidden ? null : centerHits(toggle),
    };
  });
}

function expectActionsReserved(geometry) {
  expect(geometry.navigation.width).toBeGreaterThan(0);
  expect(geometry.network.width).toBeCloseTo(44, 1);
  expect(geometry.networkSvg.width).toBeCloseTo(24, 1);
  expect(geometry.database.width).toBeCloseTo(44, 1);
  expect(geometry.databaseSvg.width).toBeCloseTo(24, 1);
  expect(geometry.database.right).toBeLessThan(geometry.network.left);
  expect(geometry.network.left - geometry.database.right).toBeGreaterThanOrEqual(6);
  expect(geometry.network.left - geometry.database.right).toBeLessThanOrEqual(8);
  if (!geometry.toggleHidden) {
    expect(geometry.toggle.right).toBeLessThan(geometry.database.left);
    expect(geometry.database.left - geometry.toggle.right).toBeGreaterThanOrEqual(6);
    expect(geometry.database.left - geometry.toggle.right).toBeLessThanOrEqual(8);
    expect(Math.abs(geometry.toggle.top + geometry.toggle.height / 2
      - geometry.database.top - geometry.database.height / 2)).toBeLessThanOrEqual(1);
    expect(geometry.toggleHit).toBe(true);
  }
  expect(geometry.actions.right).toBeLessThanOrEqual(geometry.header.right + 0.5);
  expect(geometry.network.right).toBeLessThanOrEqual(geometry.header.right + 0.5);
  expect(geometry.networkHit).toBe(true);
  expect(geometry.databaseHit).toBe(true);
}

async function toggleGeometry(page) {
  return page.locator(".view-toggle").evaluate(toggle => {
    const box = element => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, right: rect.right, top: rect.top,
        width: rect.width, height: rect.height };
    };
    const segments = [...toggle.querySelectorAll(".view-toggle-segment")];
    return {
      outer: box(toggle),
      segments: segments.map(segment => ({
        box: box(segment),
        border: getComputedStyle(segment).borderLeftWidth,
        icon: box(segment.querySelector("svg")),
      })),
      separator: getComputedStyle(toggle, "::after").width,
    };
  });
}

async function sampleHeritageMotion(page) {
  return page.locator("#toggle-heritage").evaluate(async tab => {
    const panel = document.querySelector("#heritage-panel");
    const frames = [];
    tab.click();
    for (let index = 0; index < 14; index += 1) {
      await new Promise(requestAnimationFrame);
      if (panel.hidden) break;
      const tabBox = tab.getBoundingClientRect();
      const panelBox = panel.getBoundingClientRect();
      frames.push({ tabLeft: tabBox.left, panelRight: panelBox.right,
        panelWidth: panelBox.width });
    }
    return frames;
  });
}

function expectAttachedMotion(frames) {
  expect(frames.length).toBeGreaterThan(2);
  expect(Math.max(...frames.map(frame => Math.abs(frame.tabLeft - frame.panelRight))))
    .toBeLessThanOrEqual(1);
  expect(Math.abs(frames.at(-1).tabLeft - frames[0].tabLeft))
    .toBeGreaterThan(frames[0].panelWidth / 2);
  expect(frames.some(frame => frame.tabLeft > 12
    && frame.tabLeft < frame.panelWidth - 12)).toBe(true);
}

async function expectHeaderActionStructure(page) {
  const actions = page.locator(".header-actions");
  const toggle = actions.locator(".view-toggle");
  const indicator = actions.locator("#network-status");
  const database = actions.locator("#open-business-session");
  await expect(actions.locator(":scope > *")).toHaveCount(3);
  expect(await actions.locator(":scope > *").evaluateAll(elements => (
    elements.map(element => element.classList.contains("view-toggle") ? "toggle" : element.id)
  ))).toEqual(["toggle", "open-business-session", "network-status"]);
  await expect(toggle.locator(".view-toggle-segment")).toHaveCount(2);
  expect(await indicator.evaluate(element => element.tagName)).toBe("SPAN");
  await expect(indicator).toHaveAttribute("role", "img");
  await expect(indicator).not.toHaveAttribute("aria-disabled");
  await expect(indicator).not.toHaveAttribute("tabindex");
  expect(await indicator.evaluate(element => element.tabIndex)).toBe(-1);
  expect(await database.evaluate(element => element.tagName)).toBe("BUTTON");
}

async function installStandaloneDetection(context) {
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "standalone", {
      configurable: true,
      value: true,
    });
  });
}

test("headers Web desktop : ordre fonctionnel commun et variantes Carte/Base", async ({ context, page }, testInfo) => {
  const viewQueries = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/data/view-query') viewQueries.push(request.postDataJSON());
  });
  await mockApplication(context);
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.goto("/carte");
  expect(await navigationOrder(page)).toEqual([
    "create", "tools", "settings", "ai", "toggle-help",
  ]);
  await expect(page.locator("#toggle-heritage")).toHaveCount(1);
  await expect(page.locator(".main-navigation #toggle-heritage")).toHaveCount(0);
  await expect(page.locator('.view-toggle [aria-current="page"]')).toHaveAttribute("aria-label", "Carte");
  await expect(page.locator(".view-switcher")).toHaveAttribute("aria-label", "Base");
  const mapToggle = page.locator(".view-toggle");
  await expect(mapToggle.locator(".view-toggle-segment")).toHaveCount(2);
  await expect(mapToggle.locator("svg")).toHaveCount(2);
  const toggleSize = await mapToggle.boundingBox();
  const mapToggleGeometry = await toggleGeometry(page);
  await page.screenshot({ path: testInfo.outputPath("toggle-carte.png"),
    clip: await page.locator(".header-actions").boundingBox() });
  expect(toggleSize.width).toBeGreaterThanOrEqual(68);
  expect(toggleSize.width).toBeLessThanOrEqual(72);
  expect(toggleSize.height).toBeGreaterThanOrEqual(34);
  expect(toggleSize.height).toBeLessThanOrEqual(36);
  for (const icon of await mapToggle.locator("svg").all()) {
    const box = await icon.boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(18);
    expect(box.width).toBeLessThanOrEqual(20);
  }
  const baseIcon = mapToggle.getByRole("link", { name: "Base" }).locator("svg");
  await expect(baseIcon.locator("rect")).toHaveCount(3);
  for (const square of await baseIcon.locator("rect").all()) {
    const box = await square.boundingBox();
    expect(box.width).toBeLessThan(5);
  }
  const activeMap = mapToggle.getByRole("button", { name: "Carte" });
  await expect(activeMap).toHaveCSS("background-color", "rgb(22, 90, 74)");
  await activeMap.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/carte(?:\?|$)/);
  await page.keyboard.press("Tab");
  await expect(mapToggle.getByRole("link", { name: "Base" })).toBeFocused();
  await expect(mapToggle.getByRole("link", { name: "Base" })).toHaveCSS("outline-style", "solid");
  await expect(mapToggle.getByRole("link", { name: "Base" })).toHaveAttribute("title", "Ouvrir Base de données");
  await expect(page.locator(".app-brand")).toBeVisible();
  await expect(page.locator("#status")).toBeVisible();
  await expectHeaderActionStructure(page);
  await expect(page.locator("#network-status")).toHaveAttribute("aria-label", "Serveur accessible");
  await expect(page.locator('#network-status [data-network-icon="network"]')).toBeVisible();
  await expect(page.locator('#network-status [data-network-icon="network-off"]')).toBeHidden();
  await expect(page.locator("#open-business-session")).toHaveAttribute("aria-disabled", "true");
  await page.locator("#toggle-help").focus();
  await page.keyboard.press("Tab");
  await expect(page.locator("#toggle-heritage")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(activeMap).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(mapToggle.getByRole("link", { name: "Base" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("#open-business-session")).toBeFocused();
  await page.locator("#network-status").click();
  await expect(page.locator("#network-status")).toHaveAttribute("aria-label", "Serveur accessible");
  const indicatorBackground = await page.locator("#network-status").evaluate(
    element => getComputedStyle(element).backgroundColor,
  );
  await page.locator("#network-status").hover();
  await expect(page.locator("#network-status")).toHaveCSS("background-color", indicatorBackground);
  await page.locator("#open-business-session").hover();
  await expect(page.locator("#open-business-session")).not.toHaveCSS("background-color", indicatorBackground);
  for (const svg of await page.locator(".header-actions svg").all()) await expect(svg).toHaveAttribute("aria-hidden", "true");
  await expect(page.locator(".app-header .header-notifications #status")).toHaveCount(0);
  await expect(page.locator(".header-notifications #status")).toHaveCount(1);
  const desktopHeader = await page.evaluate(() => {
    const header = document.querySelector(".app-header").getBoundingClientRect();
    const navigation = document.querySelector(".header-navigation").getBoundingClientRect();
    const actions = document.querySelector(".header-actions").getBoundingClientRect();
    return { headerHeight: header.height, navigationTop: navigation.top, actionsTop: actions.top,
      navigationWidth: navigation.width, actionsWidth: actions.width };
  });
  expect((await page.locator(".header-navigation").boundingBox()).height).toBeLessThan(80);
  expectOverlayLayout(await layoutBoxes(page));
  expect(Math.abs(desktopHeader.navigationTop - desktopHeader.actionsTop)).toBeLessThan(2);
  expect(desktopHeader.navigationWidth).toBeGreaterThan(0);
  expect(desktopHeader.actionsWidth).toBeGreaterThan(0);
  expectActionsReserved(await headerActionGeometry(page));
  await page.waitForFunction(() => typeof topNavigation !== "undefined"
    && topNavigation.getState().tools === false);
  await page.locator("#toggle-tools-menu").click();
  await expect(page.getByRole("menuitem", { name: "Territoire administratif" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Imprimer la carte" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Consultation hors ligne" })).toBeVisible();

  await page.goto("/base");
  expect(await navigationOrder(page)).toEqual([
    "statistics", "views", "queries", "tools", "settings", "ai", "toggle-help",
  ]);
  await expect(page.locator('.view-toggle [aria-current="page"]')).toHaveAttribute("aria-label", "Base");
  await expect(page.locator(".view-switcher")).toHaveAttribute("aria-label", "Carte");
  await expect(page.locator('.view-toggle [aria-current="page"]')).toHaveCSS("background-color", "rgb(22, 90, 74)");
  const baseToggleGeometry = await toggleGeometry(page);
  await page.screenshot({ path: testInfo.outputPath("toggle-base.png"),
    clip: await page.locator(".header-actions").boundingBox() });
  for (const geometry of [mapToggleGeometry, baseToggleGeometry]) {
    const [left, right] = geometry.segments;
    expect(Math.abs(left.box.width - right.box.width)).toBeLessThanOrEqual(0.02);
    expect(Math.abs(left.box.height - right.box.height)).toBeLessThanOrEqual(0.02);
    expect(Math.abs(left.box.right - right.box.left)).toBeLessThanOrEqual(0.02);
    expect(left.border).toBe("0px");
    expect(right.border).toBe("0px");
    expect(geometry.separator).toBe("1px");
    for (const segment of geometry.segments) {
      expect(Math.abs(segment.icon.left + segment.icon.width / 2
        - segment.box.left - segment.box.width / 2)).toBeLessThanOrEqual(0.02);
    }
  }
  expect(baseToggleGeometry.segments.map(segment => segment.box.width))
    .toEqual(mapToggleGeometry.segments.map(segment => segment.box.width));
  expectActionsReserved(await headerActionGeometry(page));
  await expect(page.locator("#toggle-tools-menu")).toBeVisible();
  // data-view installe son contrôleur de navigation avant de lancer ce contrôle.
  await expect(page.locator("#status")).toHaveText("Backend disponible");
  await expect(page.locator("#data-statistics-view")).toBeVisible();
  expect(viewQueries).toHaveLength(0);
  await page.locator("#toggle-views").click();
  await expect(page.locator('#data-view-field-list .data-view-field-chip')).toHaveCount(4);
  await expect(page.locator('#data-view-result-status')).toHaveText('0 ligne(s) chargée(s) sur 0');
  expect(viewQueries).toHaveLength(1);
  expect(viewQueries[0]).toMatchObject(DefaultView.definition());
  await page.locator('#data-view-field-list button').first().click();
  await expect(page.locator('#data-view-field-list .data-view-field-chip')).toHaveCount(3);
  await expect.poll(() => viewQueries.length).toBe(2);
  await expect(page.locator("#base-summary")).toBeVisible();
  await expect(page.locator("#base-summary-crs")).toHaveText("RGF93 v1 / CC50 — EPSG:3950");
  await page.locator("#show-statistics").click();
  await expect(page.locator("#data-view-builder-workspace")).toBeHidden();
  await expect(page.locator("#data-statistics-view")).toBeVisible();
  await page.locator('#toggle-views').click();
  await expect(page.locator('#data-view-field-list .data-view-field-chip')).toHaveCount(3);
  expect(viewQueries).toHaveLength(2);
  await page.locator('#show-statistics').click();
  await page.locator("#toggle-tools-menu").click();
  await expect(page.getByRole("menuitem", { name: "Catalogue des désordres" })).toBeVisible();
  await expect(page.locator(".app-brand")).toBeVisible();
  await expect(page.locator("#status")).toBeVisible();
  await expectHeaderActionStructure(page);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("digues-app:network-state", {
    detail: { status: "offline", offline: true },
  })));
  await expect(page.locator("#network-status")).toHaveAttribute("aria-label", "Serveur inaccessible — mode local");
  await expect(page.locator('#network-status [data-network-icon="network-off"]')).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("digues-app:network-state", {
    detail: { status: "online", offline: false },
  })));
  await expect(page.locator('#network-status [data-network-icon="network"]')).toBeVisible();
});

test("toggle Carte/Base : cibles tactiles sur tablette Web", async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, viewport: { width: 768, height: 1024 } });
  try {
    await mockApplication(context);
    const page = await context.newPage();
    await page.goto("/carte");
    const segments = page.locator(".view-toggle-segment");
    await expect(segments).toHaveCount(2);
    for (const segment of await segments.all()) {
      const box = await segment.boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  } finally {
    await context.close();
  }
});

test("toggle Carte/Base : ouverture puis réactivation des pages", async ({ context, page }) => {
  await mockApplication(context);
  await page.goto("/carte");
  await expect(page.locator(".view-switcher")).toHaveAttribute("aria-label", "Base");
  const openedBase = context.waitForEvent("page");
  await page.locator(".view-switcher").click();
  const basePage = await openedBase;
  await expect(basePage).toHaveURL(/\/base(?:\?|$)/);
  await expect(basePage.locator('.view-toggle [aria-current="page"]')).toHaveAttribute("aria-label", "Base");
  await basePage.locator('.view-toggle [aria-current="page"]').click();
  expect(context.pages().filter(item => !item.isClosed())).toHaveLength(2);
  await basePage.locator(".view-switcher").click();
  await expect(page).toHaveURL(/\/carte(?:\?|$)/);
  expect(context.pages().filter(item => !item.isClosed())).toHaveLength(2);
  await page.locator(".view-switcher").click();
  await expect(basePage).toHaveURL(/\/base(?:\?|$)/);
  expect(context.pages().filter(item => !item.isClosed())).toHaveLength(2);
});

// Niveau B : position réelle des lettres dans le SVG, en viewport desktop et
// tactile/PWA. Aucun SQL ni fournisseur ; les scénarios utilisent mockApplication.
async function expectCenteredHeritageText(tab) {
  const letters = await tab.evaluate(button => {
    const path = button.querySelector('.heritage-tab-shape path');
    const inverse = path.getScreenCTM().inverse();
    return [...button.querySelectorAll(':scope > span > span')].map(letter => {
      const rect = letter.getBoundingClientRect();
      const point = new DOMPoint(rect.x + rect.width / 2, rect.y + rect.height / 2).matrixTransform(inverse);
      // La largeur visible varie dans les bords inclinés/arrondis : mesurer
      // la section remplie du trapèze à la hauteur de chaque lettre.
      let left = 0, right = 24;
      for (let i = 0; i < 20; i++) {
        const x = (left + right) / 2;
        if (path.isPointInFill(new DOMPoint(x, point.y))) left = x;
        else right = x;
      }
      const start = new DOMPoint(rect.left, rect.y + rect.height / 2).matrixTransform(inverse);
      const end = new DOMPoint(rect.right, rect.y + rect.height / 2).matrixTransform(inverse);
      return { text: letter.textContent, x: point.x, y: point.y, center: right / 2,
        fits: start.x >= 0 && end.x <= right, transform: getComputedStyle(letter).transform };
    });
  });
  expect(letters.map(letter => letter.text).join('')).toBe('PATRIMOINE');
  for (const [index, letter] of letters.entries()) {
    expect(Math.abs(letter.x - letter.center), `centrage ${letter.text} à la ligne ${index}`).toBeLessThan(0.5);
    expect(letter.fits, `lettre ${letter.text} dans la surface verte`).toBe(true);
    expect(letter.transform).toBe('none');
    if (index) expect(letter.y).toBeGreaterThan(letters[index - 1].y);
  }
}

test("languette Patrimoine : panneau superposé et fermeture clavier sur Carte et Base", async ({ context, page }, testInfo) => {
  await mockApplication(context);
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const route of ["/carte", "/base"]) {
    await page.goto(route);
    if (route === "/carte") {
      await page.waitForFunction(() => typeof topNavigation !== "undefined"
        && topNavigation.getState().heritage === false);
    } else {
      await expect(page.locator("#status")).toHaveText("Backend disponible");
    }
    const tab = page.locator("#toggle-heritage");
    const panel = page.locator("#heritage-panel");
    const content = page.locator(route === "/carte" ? "#map" : "#data-statistics-view");
    await expect(tab).toBeVisible();
    const tabBox = await tab.boundingBox();
    expect(tabBox.width).toBeCloseTo(24, 1);
    expect(tabBox.x).toBeCloseTo(0, 1);
    await expect(tab.locator("svg.heritage-tab-shape path"))
      .toHaveAttribute("d", "M0 0 19 9 Q24 11 24 15 V135 Q24 139 19 141 L0 150 Z");
    await expect(page.locator(".main-navigation #toggle-heritage")).toHaveCount(0);
    const initial = await content.boundingBox();
    await page.screenshot({ path: testInfo.outputPath(`patrimoine-${route.slice(1)}-ferme.png`),
      clip: { x: 0, y: tabBox.y - 8, width: 55, height: tabBox.height + 16 } });
    await expectCenteredHeritageText(tab);
    expectAttachedMotion(await sampleHeritageMotion(page));
    await expect(panel).toBeVisible();
    await expect(tab).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("#heritage-selection-summary")).toContainText("tronçon(s) visible(s)");
    const open = await content.boundingBox();
    expect(Math.abs(open.width - initial.width)).toBeLessThanOrEqual(2);
    await expect.poll(async () => {
      const tabBox = await tab.boundingBox();
      const panelBox = await panel.boundingBox();
      return Math.abs(tabBox.x - panelBox.x - panelBox.width);
    }).toBeLessThanOrEqual(2);
    await page.screenshot({ path: testInfo.outputPath(`patrimoine-${route.slice(1)}-ouvert.png`),
      clip: { x: 0, y: tabBox.y - 8, width: 465, height: tabBox.height + 16 } });
    expectAttachedMotion(await sampleHeritageMotion(page));
    await expect(panel).toBeHidden();
    await tab.click();
    await expect(panel).toBeVisible();
    await tab.focus();
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await expect(tab).toHaveAttribute("aria-expanded", "false");
    await tab.click();
    await panel.locator("#close-heritage").click();
    await expect(panel).toBeHidden();
    await expect(tab).toBeFocused();
  }
});

test("languette Patrimoine : cible tactile et position en PWA portrait et paysage", async ({ browser }, testInfo) => {
  for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }]) {
    const context = await browser.newContext({ hasTouch: true, viewport });
    try {
      await mockApplication(context);
      await context.addInitScript(() => {
        Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
      });
      const page = await context.newPage();
      await page.goto("/carte");
      await page.waitForFunction(() => typeof topNavigation !== "undefined"
        && topNavigation.getState().heritage === false);
      const tab = page.locator("#toggle-heritage");
      await expect(tab).toBeVisible();
      await expect(page.locator(".view-toggle")).toBeHidden();
      const box = await tab.boundingBox();
      const shape = await tab.locator("svg.heritage-tab-shape").boundingBox();
      const header = await page.locator(".app-header").boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(shape.width).toBeCloseTo(24, 1);
      await expect(tab.locator("svg.heritage-tab-shape")).toBeVisible();
      expect(box.y).toBeGreaterThanOrEqual(header.y + header.height - 2);
      expect(box.y / viewport.height).toBeGreaterThanOrEqual(0.25);
      expect(box.y / viewport.height).toBeLessThanOrEqual(0.32);
      expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
      const legend = await page.locator("#map-legend").boundingBox();
      expect(box.x + box.width <= legend.x || legend.x + legend.width <= box.x
        || box.y + box.height <= legend.y || legend.y + legend.height <= box.y).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`patrimoine-pwa-${viewport.width}x${viewport.height}-ferme.png`),
        clip: { x: box.x, y: box.y - 8, width: 60, height: box.height + 16 } });
      await expectCenteredHeritageText(tab);
      await tab.tap();
      await expect(page.locator("#heritage-panel")).toBeVisible();
      const panel = await page.locator("#heritage-panel").boundingBox();
      expect(panel.x + panel.width).toBeLessThanOrEqual(viewport.width - 44);
      await expect.poll(async () => {
        const tabBox = await tab.boundingBox();
        const panelBox = await page.locator('#heritage-panel').boundingBox();
        return Math.abs(tabBox.x - panelBox.x - panelBox.width);
      }).toBeLessThanOrEqual(1);
      await expectCenteredHeritageText(tab);
      const openBox = await tab.boundingBox();
      await page.screenshot({ path: testInfo.outputPath(`patrimoine-pwa-${viewport.width}x${viewport.height}-ouvert.png`),
        clip: { x: openBox.x, y: openBox.y - 8, width: 44, height: openBox.height + 16 } });
      await page.locator('#close-heritage').click();
      await expect(page.locator('#heritage-panel')).toBeHidden();
      await expect(tab).toHaveAttribute('aria-expanded', 'false');
    } finally {
      await context.close();
    }
  }
});

test("languette Patrimoine : animation réduite sans panneau résiduel", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce", viewport: { width: 1280, height: 800 } });
  try {
    await mockApplication(context);
    const page = await context.newPage();
    await page.goto("/carte");
    await page.waitForFunction(() => typeof topNavigation !== "undefined");
    const tab = page.locator("#toggle-heritage");
    const panel = page.locator("#heritage-panel");
    await tab.click();
    await expect(panel).toBeVisible();
    await expect.poll(async () => {
      const tabBox = await tab.boundingBox();
      const panelBox = await panel.boundingBox();
      return Math.abs(tabBox.x - panelBox.x - panelBox.width);
    }).toBeLessThanOrEqual(1);
    await tab.click();
    await expect(panel).toBeHidden();
    await expect(tab).toHaveAttribute("aria-expanded", "false");
  } finally {
    await context.close();
  }
});

test("actions réservées et navigation défilante sur desktop étroit et compact", async ({ context, page }) => {
  await mockApplication(context);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/carte");
  // Carte replaces its initial header asynchronously. Measure the rendered
  // actions, not the placeholder controls from the first document paint.
  await page.waitForFunction(() => typeof topNavigation !== "undefined");
  await expect(page.locator("#open-business-session svg")).toHaveCount(1);
  await expect(page.locator("#network-status svg:not([hidden])")).toHaveCount(1);
  for (const size of [
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
    { width: 960, height: 700 },
    { width: 768, height: 1024 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    const geometry = await headerActionGeometry(page);
    expectActionsReserved(geometry);
    if (size.width === 960 || size.width === 768 || size.width === 390) {
      expect(geometry.overflowX).toBe("auto");
      expect(geometry.scrollWidth).toBeGreaterThanOrEqual(geometry.clientWidth);
      if (geometry.scrollWidth > geometry.clientWidth) {
        await page.locator(".app-navigation-viewport").evaluate(element => {
          element.scrollLeft = element.scrollWidth;
        });
        await expect.poll(() => page.locator(".app-navigation-viewport").evaluate(element => element.scrollLeft))
          .toBeGreaterThan(0);
      }
    }
  }

  await page.setViewportSize({ width: 960, height: 700 });
  for (const [toggle, menu] of [
    ["#toggle-create-menu", "#create-menu-list"],
    ["#toggle-tools-menu", "#tools-menu-list"],
    [".settings-menu-toggle", ".settings-menu-list"],
  ]) {
    await page.locator(toggle).click();
    await expect(page.locator(menu)).toBeVisible();
    const placement = await page.evaluate(selector => {
      const header = document.querySelector(".app-header").getBoundingClientRect();
      const box = document.querySelector(selector).getBoundingClientRect();
      const target = document.elementFromPoint(box.left + 12, box.top + 12);
      return { top: box.top, right: box.right, headerBottom: header.bottom,
        viewportWidth: document.documentElement.clientWidth,
        menuHit: document.querySelector(selector).contains(target) };
    }, menu);
    expect(placement.top).toBeGreaterThanOrEqual(placement.headerBottom - 1);
    expect(placement.right).toBeLessThanOrEqual(placement.viewportWidth + 0.5);
    expect(placement.menuHit).toBe(true);
    expectActionsReserved(await headerActionGeometry(page));
    await page.locator(toggle).click();
  }

  await page.goto("/base");
  await expect(page.locator("#status")).toContainText("Backend disponible");
  for (const size of [{ width: 960, height: 700 }, { width: 768, height: 1024 }]) {
    await page.setViewportSize(size);
    expectActionsReserved(await headerActionGeometry(page));
    await page.locator("#toggle-tools-menu").click();
    await expect(page.locator("#tools-menu-list")).toBeVisible();
    const menu = await page.locator("#tools-menu-list").boundingBox();
    expect(menu.x).toBeGreaterThanOrEqual(0);
    expect(menu.x + menu.width).toBeLessThanOrEqual(size.width + 0.5);
    await page.locator("#toggle-tools-menu").click();
  }
});

// Niveau B, DOM/Leaflet réels et API locales simulées : aucun SQL ni fournisseur.
// Le risque est une régression de layout/hit testing, pas le contenu des messages.
for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
  test(`notifications en haut à droite : empilement et fermeture sans déformation ${viewport.width}x${viewport.height}`, async ({ context, page }) => {
    await mockApplication(context);
    await page.setViewportSize(viewport);
    await page.goto('/carte');
    await expect(page.locator('main')).toHaveJSProperty('inert', false);
    await expect(page.locator('#status')).toContainText('tronçon');
    await page.getByRole('button', { name: 'Fermer le message', exact: true }).click();
    const before = await layoutBoxes(page);
    await exposeOfflineLayout(page);
    await expect(page.locator('.offline-status-panel')).toBeVisible();
    await expect(page.locator('.backend-status-panel')).toBeVisible();
    const during = await layoutBoxes(page);
    for (const key of ['header', 'navigation', 'actions', 'workspace', 'map']) {
      expectSameRect(during[key], before[key]);
    }
    expectOverlayLayout(during);
    expect(during.offlinePanel.top).toBeGreaterThan(during.backendPanel.bottom);
    expect(during.notifications.left).toBeGreaterThanOrEqual(0);
    expect(during.notifications.right).toBeCloseTo(viewport.width - 8, 5);
    expect(during.backendPanel.right).toBeCloseTo(during.notifications.right, 5);
    expect(during.offlinePanel.right).toBeCloseTo(during.notifications.right, 5);
    expect(during.notifications.bottom).toBeLessThanOrEqual(viewport.height);
    await expectTransparentOverlayPassesThrough(page);
    await expectMenuBelowHeader(page, '#toggle-tools-menu', '#tools-menu-list');
    // Les panneaux métier passent devant les messages : fermeture toujours accessible.
    await page.locator('#toggle-heritage').click();
    await expect(page.locator('#heritage-panel')).toBeVisible();
    await page.locator('#close-heritage').click();
    await expect(page.locator('#heritage-panel')).toBeHidden();
    await page.getByRole('button', { name: 'Fermer le message', exact: true }).click();
    await expect(page.locator('.offline-status-panel')).toBeVisible();
    await page.getByRole('button', { name: 'Fermer l’information hors ligne', exact: true }).click();
    await expect(page.locator('.header-notifications')).toBeHidden();
    const after = await layoutBoxes(page);
    for (const key of ['header', 'navigation', 'actions', 'workspace', 'map']) {
      expectSameRect(after[key], before[key]);
    }
  });
}

test("le panneau backend se ferme sans changer le statut et revient sur un nouveau message", async ({ context, page }) => {
  await mockApplication(context);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/base");
  const panel = page.locator(".backend-status-panel");
  const status = page.locator("#status");
  await expect(status).toHaveText("Backend disponible");
  const before = await status.textContent();
  const positions = await page.evaluate(() => ({
    headerBottom: document.querySelector(".app-header").getBoundingClientRect().bottom,
    notificationsTop: document.querySelector(".header-notifications").getBoundingClientRect().top,
    workspaceTop: document.querySelector("#app-workspace").getBoundingClientRect().top,
    panelBottom: document.querySelector(".backend-status-panel").getBoundingClientRect().bottom,
  }));
  expect(positions.notificationsTop).toBeGreaterThan(positions.headerBottom);
  expect(positions.workspaceTop).toBe(positions.headerBottom);
  expect(positions.workspaceTop).toBeLessThan(positions.panelBottom);
  const workspaceWithPanel = (await layoutBoxes(page)).workspace;
  await status.evaluate(element => { element.classList.add("error"); });
  await page.getByRole("button", { name: "Fermer le message" }).click();
  await expect(panel).toBeHidden();
  await expect(page.locator(".header-notifications")).toBeHidden();
  const closedWorkspace = (await layoutBoxes(page)).workspace;
  expectSameRect(closedWorkspace, workspaceWithPanel);
  expect(closedWorkspace.bottom).toBe(workspaceWithPanel.bottom);
  expect(await status.textContent()).toBe(before);
  await expect(status).toHaveClass(/error/);
  await expect(page.locator(".main-navigation")).toBeFocused();
  const collapsedGap = await page.evaluate(() => document.querySelector("#app-workspace").getBoundingClientRect().top
    - document.querySelector(".app-header").getBoundingClientRect().bottom);
  expect(collapsedGap).toBe(0);
  await status.evaluate(element => { element.textContent = element.textContent; });
  await expect(panel).toBeHidden();
  await status.evaluate(element => { element.textContent = "Nouveau message backend"; });
  await expect(panel).toBeVisible();
  await expect(status).toHaveText("Nouveau message backend");
});

test("capacités Web : seuil 700 px, tablettes et header compact", async ({ context, page }) => {
  await mockApplication(context);
  await page.setViewportSize({ width: 699, height: 844 });
  await page.goto("/carte");
  await expect(page.locator(".view-switcher")).toBeHidden();
  await page.locator(".settings-menu-toggle").click();
  await expect(page.locator("#open-interface-in-new-tab")).toBeHidden();
  await expect(page.locator("#sync-heritage-selection")).toBeHidden();
  await expect(page.locator(".app-brand")).toBeHidden();
  await expectHeaderActionStructure(page);
  await page.goto("/base");
  await expect(page).toHaveURL(/\/carte(?:\?|$)/);

  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto("/base");
  await expect(page).toHaveURL(/\/base$/);
  await expect(page.locator(".view-switcher")).toBeVisible();
  await expect(page.locator(".app-brand")).toBeHidden();
  await expectHeaderActionStructure(page);
  const summary = page.locator("#base-summary");
  await page.locator("#toggle-views").click();
  await expect(summary).toBeVisible();
  expect((await summary.boundingBox()).height).toBeLessThan(110);
  const baseWithNotice = (await layoutBoxes(page)).workspace;
  await page.getByRole("button", { name: "Fermer le message" }).click();
  await expect(page.locator(".header-notifications")).toBeHidden();
  expectSameRect((await layoutBoxes(page)).workspace, baseWithNotice);
  await page.locator("#status").evaluate((element) => { element.textContent = "Message de contrôle Base"; });
  await expect(page.locator(".backend-status-panel")).toBeVisible();
  expectOverlayLayout(await layoutBoxes(page));

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/base");
  await expect(page).toHaveURL(/\/base$/);
  await expect(page.locator(".app-brand")).toBeVisible();
  await expect(page.locator("#status")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  // Base redirects itself below 700 px. Do not race that navigation with goto.
  await page.waitForURL(/\/carte(?:\?|$)/);
  await page.waitForLoadState("load");
  const viewport = page.locator(".app-navigation-viewport");
  expect(await viewport.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await expect(viewport).toHaveCSS("overflow-x", "auto");
  await expect(page.locator(".main-navigation")).toHaveCSS("flex-wrap", "nowrap");
  await expectHeaderActionStructure(page);
  await page.locator(".settings-menu-toggle").click();
  const portraitSettings = await page.locator(".settings-menu-list").boundingBox();
  expect(portraitSettings.x).toBe(0);
  expect(portraitSettings.width).toBe(390);
  await page.locator(".settings-menu-toggle").click();
  await page.locator("#toggle-tools-menu").click();
  const portraitMenu = await page.locator("#tools-menu-list").boundingBox();
  expect(portraitMenu.x).toBe(0);
  expect(portraitMenu.width).toBe(390);
  await page.locator("#status").evaluate((element) => element.classList.add("error"));
  await expect(page.locator("#status")).toBeVisible();
  await page.locator("#status").evaluate((element) => element.classList.remove("error"));
  await page.locator("#toggle-tools-menu").click();

  await page.setViewportSize({ width: 844, height: 390 });
  await page.locator("#toggle-tools-menu").click();
  const landscapeMenu = await page.locator("#tools-menu-list").boundingBox();
  expect(landscapeMenu.width).toBeLessThan(844);
  expect(landscapeMenu.x).toBeGreaterThanOrEqual(0);
  expect(landscapeMenu.x + landscapeMenu.width).toBeLessThanOrEqual(844);
  await page.locator("#toggle-tools-menu").click();
  await page.locator(".settings-menu-toggle").click();
  const landscapeSettings = await page.locator(".settings-menu-list").boundingBox();
  expect(landscapeSettings.width).toBeLessThan(844);
  expect(landscapeSettings.x).toBeGreaterThanOrEqual(0);
  expect(landscapeSettings.x + landscapeSettings.width).toBeLessThanOrEqual(844);
});

test("capacités PWA : Carte seule, Outils terrain et densité pilotée par l'espace", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    serviceWorkers: "block",
  });
  await installStandaloneDetection(context);
  await mockApplication(context);
  const page = await context.newPage();
  try {
    await page.goto("/carte");
    await expect(page.locator("#base-summary")).toHaveCount(0);
    await expect(page.locator(".view-switcher")).toBeHidden();
    await expect(page.locator(".settings-menu-toggle")).toBeVisible();
    await page.locator(".settings-menu-toggle").click();
    await expect(page.locator("#open-interface-in-new-tab")).toBeHidden();
    await expect(page.locator("#sync-heritage-selection")).toBeHidden();
    await page.waitForFunction(() => typeof topNavigation !== "undefined"
      && topNavigation.getState().tools === false);
    await page.locator("#toggle-tools-menu").click();
    await expect(page.getByRole("menuitem", { name: "Consultation hors ligne" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Territoire administratif" })).toBeHidden();
    await expect(page.getByRole("menuitem", { name: "Imprimer la carte" })).toBeHidden();
    await expect(page.locator(".app-brand")).toBeHidden();
    await expectHeaderActionStructure(page);
    expectActionsReserved(await headerActionGeometry(page));
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("digues-app:network-state", {
      detail: { status: "offline", offline: true },
    })));
    await expect(page.locator("#network-status")).toHaveAttribute("aria-label", "Serveur inaccessible — mode local");
    await expect(page.locator('#network-status [data-network-icon="wifi-off"]')).toBeVisible();

    await page.setViewportSize({ width: 768, height: 1024 });
    await page.reload();
    await expect(page.locator(".view-switcher")).toBeHidden();
    await expect(page.locator("#toggle-tools-menu")).toBeVisible();
    await expect(page.locator(".app-brand")).toBeHidden();
    expectActionsReserved(await headerActionGeometry(page));

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.reload();
    await expect(page.locator(".view-switcher")).toBeHidden();
    await expect(page.locator(".app-brand")).toBeVisible();
    await expect(page.locator("#status")).toBeVisible();
    expectActionsReserved(await headerActionGeometry(page));

    await page.goto("/base");
    await expect(page).toHaveURL(/\/carte(?:\?|$)/);
  } finally {
    await context.close();
  }
});

test("layout multi-tenant : navigation, modale session et messages restent séparés", async ({ browser, baseURL }) => {
  test.setTimeout(240_000);
  const web = await browser.newContext({ baseURL, serviceWorkers: "block" });
  await mockMultiTenantApplication(web);
  const page = await web.newPage();
  try {
    for (const viewport of [
      { width: 1280, height: 800 },
      { width: 768, height: 1024 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto(`/carte?base=${TENANT_DATABASE_SLUG}`);
      await expect(page.locator("#business-session")).toHaveCount(0);
      const sessionButton = page.locator("#open-business-session");
      await expect(sessionButton).toBeVisible();
      await expect(sessionButton).not.toHaveAttribute("aria-disabled", "true");
      await expect(sessionButton).toHaveAttribute("aria-haspopup", "dialog");
      await expect(page.locator("#network-status")).toHaveAttribute("aria-label", "Serveur accessible");
      await expect(page.locator(".legend-external-group")).toBeVisible();
      await expect(page.locator(".legend-external-group")).toContainText("Météo-France");
      await expect(page.locator(".legend-external-group")).toContainText("Hub'Eau");
      await expect(page.locator('#network-status [data-network-icon="network"]')).toBeVisible();
      await expect(page.locator(".header-navigation")).toBeVisible();
      let boxes = await layoutBoxes(page);
      expect(boxes.navigation.width).toBeGreaterThan(0);
      expect(boxes.actions.width).toBeGreaterThan(0);
      expect(boxes.navigation.height).toBeLessThan(80);
      expectOverlayLayout(boxes);
      expect(intersects(boxes.navigation, boxes.actions)).toBe(false);

      await page.getByRole("button", { name: "Fermer le message" }).click();
      await expect(page.locator(".header-notifications")).toBeHidden();
      const withoutNotice = await layoutBoxes(page);

      await sessionButton.click();
      await expect(sessionButton).toHaveAttribute("aria-expanded", "true");
      const modal = page.getByRole("dialog", { name: "Compte et base" });
      await expect(modal).toBeVisible();
      await expect(modal).toHaveAttribute("aria-modal", "true");
      await expect(modal.locator("#business-session-user")).toHaveText("Camille Terrain");
      await expect(modal.locator("#business-session-login")).toHaveText("terrain");
      await expect(modal.locator("#business-session-structure")).toHaveText("Structure de démonstration");
      await expect(modal.locator("#business-session-base")).toHaveText("Base métier de démonstration");
      await expect(modal.locator("#close-business-session")).toBeFocused();
      const modalBox = await modal.boundingBox();
      expect(modalBox.x).toBeGreaterThanOrEqual(0);
      expect(modalBox.x + modalBox.width).toBeLessThanOrEqual(viewport.width);
      expect(modalBox.y + modalBox.height).toBeLessThanOrEqual(viewport.height);
      await page.keyboard.press("Shift+Tab");
      await expect(modal.locator("#logout-business-session")).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(modal.locator("#close-business-session")).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(modal).toBeHidden();
      await expect(sessionButton).toBeFocused();
      await expect(sessionButton).toHaveAttribute("aria-expanded", "false");

      await exposeOfflineLayout(page);
      await expect(page.locator("#offline-notice")).toBeVisible();
      await expect(page.locator("#status.error")).toBeVisible();
      await expect(page.locator(".backend-status-panel")).toBeVisible();
      boxes = await layoutBoxes(page);
      expect(boxes.notifications.top).toBeGreaterThanOrEqual(boxes.header.top);
      expect(boxes.notifications.left).toBeGreaterThanOrEqual(0);
      expect(boxes.notifications.right).toBeLessThanOrEqual(viewport.width);
      expect(boxes.notice.top).toBeGreaterThanOrEqual(boxes.header.bottom);
      expect(boxes.offlinePanel.top).toBeGreaterThanOrEqual(boxes.backendPanel.bottom);
      expect(boxes.workspace.top).toBe(boxes.header.bottom);
      expect(boxes.notifications.top).toBeLessThan(boxes.workspace.bottom);
      expect(intersects(boxes.offlinePanel, boxes.workspace)).toBe(true);
      expect(boxes.offlinePanel.right).toBeLessThanOrEqual(viewport.width);
      expectOverlayLayout(boxes);
      expectSameRect(boxes.workspace, withoutNotice.workspace);
      expectSameRect(boxes.header, withoutNotice.header);
      expectSameRect(boxes.navigation, withoutNotice.navigation);
      expectSameRect(boxes.actions, withoutNotice.actions);
      expectSameRect(boxes.map, withoutNotice.map);
      expect(boxes.workspace.bottom).toBe(withoutNotice.workspace.bottom);
      expect(boxes.map.width).toBe(withoutNotice.map.width);
      expect(intersects(boxes.status, boxes.notice)).toBe(false);
      expect(boxes.navigation.width).toBeGreaterThan(0);
      await expectTransparentOverlayPassesThrough(page);
      await expectMenuBelowHeader(page, "#toggle-tools-menu", "#tools-menu-list");
      if (viewport.width === 1280) {
        await expectMenuBelowHeader(page, "#toggle-create-menu", "#create-menu-list");
        await expectMenuBelowHeader(page, ".settings-menu-toggle", ".settings-menu-list");
        await sessionButton.click();
        await expect(modal).toBeVisible();
        const modalAboveNotice = await page.evaluate(() => {
          const notice = document.querySelector(".offline-status-panel").getBoundingClientRect();
          return Boolean(document.elementFromPoint(notice.left + 8, notice.top + 8)
            ?.closest("#business-session-modal"));
        });
        expect(modalAboveNotice).toBe(true);
        await modal.locator("#close-business-session").click();
      }
      await page.getByRole("button", { name: "Fermer le message" }).click();
      await page.getByRole("button", { name: "Fermer l’information hors ligne" }).click();
      await expect(page.locator("#open-business-session")).toBeFocused();
      await expect(page.locator(".header-notifications")).toBeHidden();
      boxes = await layoutBoxes(page);
      expectSameRect(boxes.workspace, withoutNotice.workspace);
      expectSameRect(boxes.map, withoutNotice.map);
    }
  } finally {
    await web.close();
  }

  const standalone = await browser.newContext({
    baseURL,
    serviceWorkers: "block",
    viewport: { width: 390, height: 844 },
  });
  await installStandaloneDetection(standalone);
  await mockMultiTenantApplication(standalone);
  const pwa = await standalone.newPage();
  try {
    await pwa.goto(`/carte?base=${TENANT_DATABASE_SLUG}`);
    await expect(pwa.locator("#business-session")).toHaveCount(0);
    await expect(pwa.locator(".header-navigation")).toBeVisible();
    await expect(pwa.locator(".view-switcher")).toBeHidden();
    await expect(pwa.locator("#toggle-tools-menu")).toBeVisible();
    await expect(pwa.locator("#network-status")).toHaveAttribute("aria-label", "Serveur accessible");
    await expect(pwa.locator('#network-status [data-network-icon="wifi"]')).toBeVisible();
    await expect(pwa.locator(".legend-live-external-group")).toBeHidden();
    await pwa.waitForFunction(() => typeof topNavigation !== "undefined"
      && topNavigation.getState().tools === false);
    await pwa.locator("#toggle-tools-menu").click();
    await expect(pwa.getByRole("menuitem", { name: "Consultation hors ligne" })).toBeVisible();
    await expect(pwa.getByRole("menuitem", { name: "Territoire administratif" })).toBeHidden();
    await expect(pwa.getByRole("menuitem", { name: "Imprimer la carte" })).toBeHidden();
    await pwa.locator("#toggle-tools-menu").click();
    await pwa.locator("#open-business-session").click();
    await expect(pwa.getByRole("dialog", { name: "Compte et base" })).toBeVisible();
    await pwa.getByRole("button", { name: "Fermer le compte et la base" }).click();
    await expect(pwa.locator("#open-business-session")).toBeFocused();

    await pwa.getByRole("button", { name: "Fermer le message" }).click();
    const withoutNotice = await layoutBoxes(pwa);

    await exposeOfflineLayout(pwa);
    await expect(pwa.locator(".backend-status-panel")).toBeVisible();
    const boxes = await layoutBoxes(pwa);
    expect(boxes.navigation.width).toBeGreaterThan(0);
    expect(boxes.actions.width).toBeGreaterThan(0);
    expect(intersects(boxes.navigation, boxes.actions)).toBe(false);
    expect(boxes.offlinePanel.top).toBeGreaterThanOrEqual(boxes.backendPanel.bottom);
    expect(boxes.workspace.top).toBe(boxes.header.bottom);
    expect(boxes.notice.top).toBeGreaterThanOrEqual(boxes.header.bottom);
    expect(intersects(boxes.status, boxes.notice)).toBe(false);
    expectOverlayLayout(boxes);
    expectSameRect(boxes.workspace, withoutNotice.workspace);
    expectSameRect(boxes.header, withoutNotice.header);
    expectSameRect(boxes.navigation, withoutNotice.navigation);
    expectSameRect(boxes.actions, withoutNotice.actions);
    expectSameRect(boxes.map, withoutNotice.map);
    expect(boxes.workspace.bottom).toBe(withoutNotice.workspace.bottom);
    expect(boxes.map.width).toBe(withoutNotice.map.width);
    await expectTransparentOverlayPassesThrough(pwa);
    await expectMenuBelowHeader(pwa, "#toggle-tools-menu", "#tools-menu-list");
    await pwa.getByRole("button", { name: "Fermer le message" }).click();
    await pwa.getByRole("button", { name: "Fermer l’information hors ligne" }).click();
    await expect(pwa.locator("#open-business-session")).toBeFocused();
    const afterClose = await layoutBoxes(pwa);
    expectSameRect(afterClose.workspace, withoutNotice.workspace);
    expectSameRect(afterClose.map, withoutNotice.map);
  } finally {
    await standalone.close();
  }
});

test("état serveur : mêmes transitions réelles en Web et PWA, notice fermable sans effet métier", async ({ browser, baseURL }) => {
  test.setTimeout(180_000);
  for (const standalone of [false, true]) {
    const context = await browser.newContext({ baseURL, serviceWorkers: "block",
      viewport: standalone ? { width: 390, height: 844 } : { width: 1280, height: 800 } });
    try {
      if (standalone) await installStandaloneDetection(context);
      const state = await mockMultiTenantApplication(context);
      const page = await context.newPage();
      const activeHeads = new Set();
      let peakHeads = 0;
      const isHead = request => new URL(request.url()).pathname.endsWith("/resource-events/head");
      page.on("request", request => {
        if (!isHead(request)) return;
        activeHeads.add(request);
        peakHeads = Math.max(peakHeads, activeHeads.size);
      });
      const finishHead = request => { if (isHead(request)) activeHeads.delete(request); };
      page.on("requestfinished", finishHead);
      page.on("requestfailed", finishHead);
      await page.goto(`/carte?base=${TENANT_DATABASE_SLUG}`);
      const button = page.locator("#network-status");
      const connectedIcon = standalone ? "wifi" : "network";
      const offlineIcon = `${connectedIcon}-off`;
      const assertNetwork = async (offline) => {
        await expect(button).toHaveAttribute("aria-label", offline
          ? "Serveur inaccessible — mode local" : "Serveur accessible");
        await expect(button).toHaveAttribute("title", offline
          ? "Serveur inaccessible — mode local" : "Serveur accessible");
        await expect(button.locator(`[data-network-icon="${offline ? offlineIcon : connectedIcon}"]`)).toBeVisible();
        await expect(button.locator(`[data-network-icon="${offline ? connectedIcon : offlineIcon}"]`)).toBeHidden();
        expect(await page.evaluate(() => OfflineRuntime.isOffline())).toBe(offline);
      };
      await assertNetwork(false);
      await expect(page.locator(".offline-status-panel")).toBeHidden();
      for (const [status, label] of [
        ["unknown", "État du serveur non déterminé"],
        ["checking", "Vérification de l’accès au serveur"],
      ]) {
        await page.evaluate(value => window.dispatchEvent(new CustomEvent("digues-app:network-state", {
          detail: { status: value, offline: null, checkedAt: null, reason: "test" },
        })), status);
        await expect(button).toHaveAttribute("aria-label", label);
        await expect(button).toHaveAttribute("data-network-status", status);
        await expect(button.locator(`[data-network-icon="${connectedIcon}"]`)).toBeVisible();
        await expect(button.locator(`[data-network-icon="${offlineIcon}"]`)).toBeHidden();
      }
      await page.evaluate(() => window.dispatchEvent(new CustomEvent("digues-app:network-state", {
        detail: OfflineRuntime.getNetworkState(),
      })));
      await assertNetwork(false);

      // Browser connectivity events are wake-up hints: each transition below is
      // decided by the backend probe, without a business read or page reload.
      await page.evaluate(() => {
        Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
        window.__networkTransitions = [];
        window.addEventListener("digues-app:network-state", event => window.__networkTransitions.push(event.detail.offline));
      });
      state.offline = true;
      await page.evaluate(() => window.dispatchEvent(new Event("offline")));
      await assertNetwork(true);
      state.offline = false;
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await assertNetwork(false);

      // An online browser hint cannot override a real transport outage.
      state.offline = true;
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await assertNetwork(true);
      state.offline = false;

      // Hidden pages suspend their periodic timer; becoming visible probes now.
      const callsBeforeHidden = state.headCalls;
      await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
      });
      expect(state.headCalls).toBe(callsBeforeHidden);
      await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await assertNetwork(false);
      expect(state.headCalls).toBe(callsBeforeHidden + 1);

      // Hold the backend probe so all three hints must share one in-flight
      // request. Keep the journal available: its own transport failure may
      // legitimately request another probe after the first one finishes.
      await expect.poll(() => activeHeads.size).toBe(0);
      // The assertion concerns the coalesced wake-up burst below. Initial
      // journal baselining and connectivity probing are independent consumers.
      peakHeads = 0;
      state.backendUnavailable = true;
      const callsBeforeBurst = state.headCalls;
      let releaseHead;
      state.holdNextHead = new Promise(resolve => { releaseHead = resolve; });
      try {
        await page.evaluate(() => {
          window.dispatchEvent(new Event("offline"));
          window.dispatchEvent(new Event("online"));
          window.dispatchEvent(new Event("pageshow"));
        });
        await expect.poll(() => state.headCalls).toBe(callsBeforeBurst + 1);
        expect(activeHeads.size).toBe(1);
        expect(peakHeads).toBe(1);
      } finally {
        releaseHead();
      }
      await assertNetwork(true);
      await expect.poll(() => activeHeads.size).toBe(0);
      expect(state.headCalls).toBe(callsBeforeBurst + 1);
      state.backendUnavailable = false;
      await page.evaluate(() => window.dispatchEvent(new Event("pageshow")));
      await assertNetwork(false);

      await page.evaluate(async () => {
        Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
        await OfflineRuntime.fetch("/api/troncons");
      });
      await assertNetwork(false);

      state.offline = true;
      await page.evaluate(async () => { try { await OfflineRuntime.fetch("/api/troncons"); } catch (_) { /* no prepared scope */ } });
      await assertNetwork(true);
      await expect(page.locator(".header-notifications .offline-status-panel #offline-notice")).toBeVisible();
      await expect(page.locator("#offline-notice")).toHaveAttribute("role", "status");
      const beforeClose = await page.evaluate(async () => ({ projection: await OfflineRuntime.inspect(),
        operations: await OfflineRuntime.store.listOperations() }));
      await page.getByRole("button", { name: "Fermer l’information hors ligne" }).click();
      await expect(page.locator("#open-business-session")).toBeFocused();
      await expect(page.locator(".offline-status-panel")).toBeHidden();
      await assertNetwork(true);
      await page.evaluate(async () => { try { await OfflineRuntime.fetch("/api/troncons"); } catch (_) { /* same transport failure */ } });
      await expect(page.locator(".offline-status-panel")).toBeHidden();
      expect(await page.evaluate(async () => ({ projection: await OfflineRuntime.inspect(),
        operations: await OfflineRuntime.store.listOperations() }))).toEqual(beforeClose);

      state.offline = false; state.httpError = true;
      expect(await page.evaluate(async () => {
        const response = await OfflineRuntime.fetch("/api/troncons");
        return { status: response.status, local: response.headers.get("X-Digues-App-Local-Projection") };
      })).toEqual({ status: 503, local: null });
      await assertNetwork(false);
      await expect(page.locator(".offline-status-panel")).toBeHidden();
      state.httpError = false; state.offline = true;
      await page.evaluate(async () => { try { await OfflineRuntime.fetch("/api/troncons"); } catch (_) { /* new transition */ } });
      await assertNetwork(true);
      await expect(page.locator(".offline-status-panel")).toBeVisible();
      expect(await page.evaluate(() => window.__networkTransitions)).toEqual([
        true, false, true, false, true, false, true, false, true,
      ]);
      expect(peakHeads).toBe(1);
      expect((await page.locator(".header-navigation").boundingBox()).width).toBeGreaterThan(0);
      await expect(page.locator("#open-business-session")).toBeVisible();
    } finally {
      await context.close();
    }
  }
});
