const {test, expect} = require("@playwright/test");

test.use({trace: "off", screenshot: "off", video: "off"});
const password = process.env.GEMAPI_TEST_PASSWORD;
test.skip(!password, "Comptes éphémères de la recette PostgreSQL requis.");

test.beforeEach(async ({context, baseURL}) => {
  const origin = new URL(baseURL).origin;
  await context.route("**/*", route =>
    new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
});

async function connect(page, login) {
  await page.goto("/");
  await page.getByLabel("Identifiant PostgreSQL").fill(login);
  await page.getByLabel("Mot de passe").fill(password);
  await page.getByRole("button", {name: "Se connecter"}).click();
  await expect(page).toHaveURL(/\/carte$/);
  await expect(page.getByRole("button", {name: "Déconnexion"})).toBeVisible();
}

test("changement de compte, navigation et déconnexion depuis Base", async ({page, context}) => {
  await connect(page, "lecteur");
  const secure = new URL(page.url()).protocol === "https:";
  const session = (await context.cookies()).find(cookie =>
    cookie.name === (secure ? "__Host-gemapi_session" : "gemapi_session_local"));
  expect(Boolean(session && session.httpOnly && session.secure === secure &&
    session.sameSite === "Strict" && session.path === "/")).toBe(true);
  const tab = await context.newPage();
  await connect(tab, "gestion");
  const response = await page.request.get("/api/auth/me");
  expect((await response.json()).login).toBe("gestion");
  await page.getByRole("link", {name: "Base", exact: true}).click();
  await page.getByRole("button", {name: "Déconnexion"}).click();
  await expect(page.getByRole("heading", {name: "Connexion"})).toBeVisible();
  await expect(tab.getByRole("button", {name: "Déconnexion"})).toBeHidden();
  expect((await tab.request.get("/api/auth/me")).status()).toBe(401);
});

test("préremplissage dédié et absence de secrets persistés par le shell", async ({page, context}) => {
  await page.goto("/");
  await expect(page.getByLabel("Identifiant PostgreSQL")).toHaveValue("public_demo");
  // Ne pas incorporer la valeur publique éphémère aux diagnostics de Playwright.
  expect(await page.getByLabel("Mot de passe").inputValue() === process.env.GEMAPI_TEST_DEMO_PASSWORD).toBe(true);
  await page.getByRole("button", {name: "Se connecter"}).click();
  await expect(page.getByRole("button", {name: "Déconnexion"})).toBeVisible();
  await page.waitForFunction(() => navigator.serviceWorker.controller);
  const clean = await page.evaluate(async secrets => {
    const contains = value => secrets.some(secret => JSON.stringify(value ?? "").includes(secret));
    if ([localStorage, sessionStorage].some(contains) || contains(location.href) || contains(document.cookie)) {
      return false;
    }
    const result = request => new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    for (const info of await indexedDB.databases()) {
      if (contains(info.name)) return false;
      const db = await result(indexedDB.open(info.name));
      try {
        for (const name of db.objectStoreNames) {
          const store = db.transaction(name).objectStore(name);
          const [keys, values] = await Promise.all([result(store.getAllKeys()), result(store.getAll())]);
          if (contains(keys) || contains(values)) return false;
        }
      } finally { db.close(); }
    }
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        if (contains(request.url) || request.url.includes("/api/auth/")) return false;
        const response = await cache.match(request);
        if (contains(await response.text())) return false;
      }
    }
    return true;
  }, [password, process.env.GEMAPI_TEST_DEMO_PASSWORD]);
  expect(clean).toBe(true);
  expect((await context.cookies()).every(cookie => !cookie.value.includes(process.env.GEMAPI_TEST_DEMO_PASSWORD))).toBe(true);
});
