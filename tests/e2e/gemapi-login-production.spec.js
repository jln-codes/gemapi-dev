const {test, expect} = require("@playwright/test");

test("accueil de production sans préremplissage et refus visible", async ({page}) => {
  test.skip(process.env.GEMAPI_TEST_PREFILL !== "0", "Instance jetable en mode production requise.");
  const prefill = page.waitForResponse(response => response.url().endsWith("/api/auth/demo"));
  await page.goto("/");
  expect(await (await prefill).json()).toEqual({});
  const login = page.getByLabel("Identifiant PostgreSQL");
  const password = page.getByLabel("Mot de passe");
  await expect(login).toHaveValue("");
  await expect(password).toHaveValue("");
  await login.fill("lecteur");
  await password.fill("identifiant-invalide-de-recette");
  await page.getByRole("button", {name: "Se connecter"}).click();
  await expect(page.getByRole("status")).toContainText("mot de passe incorrect");
  await expect(password).toHaveValue("");
});
