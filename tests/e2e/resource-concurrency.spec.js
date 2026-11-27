const { test, expect } = require("@playwright/test");

// Deliberately opt-in: these tests create data in a disposable, versioned PostGIS.
test.skip(process.env.DIGUES_E2E_OCC !== "1", "Requires an explicitly disposable PostgreSQL backend");

test("événement serveur unique, multicast et vrai conflit Carte/Base avec brouillons conservés", async ({ context, request }) => {
  test.setTimeout(60_000);
  const created = await request.post("/api/desordres", {data: {
    designation: "LOT56", geometry: {type:"Point",coordinates:[2.01,46.01]},
  }});
  expect(created.status()).toBe(201);
  const feature=await created.json();const id=feature.properties.id;
  const events=[];
  const [map,base,manual]=await Promise.all([context.newPage(),context.newPage(),context.newPage()]);
  for(const page of [map,base,manual]) {
    page.on("pageerror", error=>events.push(error.message));
  }
  await context.addInitScript(() => {
    localStorage.setItem("digues-app:heritage-selection-sync-enabled","false");
    window.__sent=[];
    const original=BroadcastChannel.prototype.postMessage;
    BroadcastChannel.prototype.postMessage=function(e) {
      if(e.type==="resource-mutated") window.__sent.push(e.payload);
      return original.call(this,e);
    };
  });
  await Promise.all([map.goto("/carte"),base.goto("/base"),manual.goto("/base")]);
  await map.waitForFunction(id=>typeof desordreLayersById!=="undefined" && desordreLayersById.has(id),id);
  await map.evaluate(async id=>openPointEditor(id,desordreLayersById.get(id)),id);
  for(const page of [base,manual]) {
    await page.evaluate(id=>document.querySelector("main").dispatchEvent(new CustomEvent("digues-app:open-entity",{
      detail:{entityClass:"desordres",entityId:id,source:"home",entities:[]},
    })),id);
    await expect(page.locator("#data-entity-detail-edit")).toBeVisible();
    await page.evaluate(() => {
      window.__invalidations=[];
      ResourceMutationController.registerInvalidator("open-detail",e=>window.__invalidations.push(e));
    });
  }
  const clients=await Promise.all([map,base,manual].map(p=>p.evaluate(()=>ResourceMutationController.getClientId())));
  expect(new Set(clients).size).toBe(1);
  const instances=await Promise.all([map,base,manual].map(p=>p.evaluate(()=>InterfaceCoordination.instanceId)));
  expect(new Set(instances).size).toBe(3);
  await base.locator("#data-entity-detail-edit").click();
  await base.locator('[name="designation"]').fill("Brouillon Base");
  await map.locator("#desordre-create-designation").fill("Écrit depuis Carte");
  const saved=map.waitForResponse(r=>r.request().method()==="PUT" && r.url().endsWith(id));
  await map.locator("#submit-desordre-create").click();
  const response=await saved;expect(response.status()).toBe(200);
  const receipt=(await response.json()).mutation;
  for(const page of [base,manual]) {
    await expect.poll(()=>page.evaluate(()=>window.__invalidations.length)).toBe(1);
  }
  expect(await map.evaluate(()=>window.__sent[0].eventId)).toBe(receipt.eventId);
  await expect(base.locator('[name="designation"]')).toHaveValue("Brouillon Base");
  const conflict=base.waitForResponse(r=>r.request().method()==="PUT" && r.url().endsWith(id));
  await base.locator("#data-entity-detail .entity-form__actions [data-role=save]").click();
  expect((await conflict).status()).toBe(409);
  await expect(base.locator('[name="designation"]')).toHaveValue("Brouillon Base");
  await expect(base.locator("#data-entity-detail .entity-form__message[data-role=status]:visible")).toContainText("brouillon");
  expect(await base.evaluate(()=>window.__sent.length)).toBe(0);

  // A second transport can deliver the identical receipt without invalidating twice.
  const published=await map.evaluate(()=>window.__sent[0]);
  await manual.evaluate(e=>ResourceMutationController.dispatch(e),published);
  expect(await manual.evaluate(()=>window.__invalidations.length)).toBe(1);
  base.once("dialog", dialog=>dialog.accept());
  const reread=base.waitForResponse(response=>response.request().method()==="GET"&&new URL(response.url()).pathname===`/api/desordres/${id}`);
  await base.locator("#data-entity-detail .entity-form__actions [data-role=cancel]").click();
  expect((await reread).status()).toBe(200);
  await expect(base.locator("#data-entity-detail-edit")).toBeVisible();
  await map.locator("#desordre-create-designation").fill("Brouillon Carte");
  await base.locator("#data-entity-detail-edit").click();
  await base.locator('[name="designation"]').fill("Écrit depuis Base");
  const second=base.waitForResponse(r=>r.request().method()==="PUT" && r.url().endsWith(id));
  await base.locator("#data-entity-detail .entity-form__actions [data-role=save]").click();
  expect((await second).status()).toBe(200);
  await expect(map.locator("#desordre-create-designation")).toHaveValue("Brouillon Carte");
  const mapConflict=map.waitForResponse(r=>r.request().method()==="PUT" && r.url().endsWith(id));
  await map.locator("#submit-desordre-create").click();
  expect((await mapConflict).status()).toBe(409);
  await expect(map.locator("#desordre-create-designation")).toHaveValue("Brouillon Carte");
  expect(await map.evaluate(()=>getMapEditorState().conflict)).toBe(true);
  expect(await map.evaluate(()=>window.__sent.length)).toBe(1);
  expect(await manual.evaluate(()=>window.__sent.length)).toBe(0);
  const previousInstance=instances[2];
  await manual.reload();
  await manual.waitForFunction(() => window.ResourceMutationController && window.InterfaceCoordination);
  expect(await manual.evaluate(()=>ResourceMutationController.getClientId())).toBe(clients[0]);
  expect(await manual.evaluate(()=>InterfaceCoordination.instanceId)).not.toBe(previousInstance);
  expect(events).toEqual([]);
});
