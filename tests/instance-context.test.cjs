const assert = require("node:assert/strict");
const test = require("node:test");
const { createContext, createRuntime } = require("../webapp/frontend/js/business-context.js");

const origin = "https://instance.example.test";
const identity = {
  instance_id: "10000000-0000-4000-8000-000000000001",
  user_id: "20000000-0000-4000-8000-000000000001",
  data_epoch: "30000000-0000-4000-8000-000000000001",
  permissions: ["business.read"],
};

test("instance, utilisateur et génération isolent canaux, blobs et intentions", () => {
  const initial = createRuntime({ origin, context: identity });
  for (const name of ["instance_id", "user_id", "data_epoch"]) {
    const other = createRuntime({ origin, context: { ...identity,
      [name]: "40000000-0000-4000-8000-000000000001",
    } });
    assert.notEqual(other.coordinationKey("inter-page"), initial.coordinationKey("inter-page"));
    assert.notEqual(other.databaseName, initial.databaseName);
  }
  assert.equal(initial.can("business.read"), true);
  assert.equal(initial.can("business.update"), false);
  assert.throws(() => createContext({ ...identity, data_epoch: "" }), /Identité/);
});

test("le transport utilise les chemins courts et annule une réponse d’un ancien contexte", async () => {
  let complete;
  const runtime = createRuntime({ origin, context: identity, fetchImpl: async (path, options) => {
    const url = new URL(path, origin);
    assert.equal(url.pathname, "/api/photos");
    assert.equal(url.searchParams.get("context_data_epoch"), identity.data_epoch);
    assert.equal(options.headers.get("X-Digues-CSRF"), "1");
    assert.equal(options.cache, "no-store");
    return new Promise(resolve => { complete = () => resolve(new Response("{}")); });
  } });
  assert.equal(runtime.pageUrl("/base"), "/base");
  assert.equal(runtime.apiUrl("https://other.example.test/api/photos"), "https://other.example.test/api/photos");
  const response = runtime.fetch("/api/photos", { method: "POST" });
  runtime.stop();
  complete();
  await assert.rejects(response, { name: "AbortError" });
});
