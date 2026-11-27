from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
FRONTEND_DIRECTORY = PROJECT_DIRECTORY / "webapp" / "frontend"


class ResourceMutationTest(unittest.TestCase):
    def run_node(self, program):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)

    def test_payload_validation_and_normalization(self):
        self.run_node(r'''
const assert = require("node:assert/strict");
const Invalidation = require("./webapp/frontend/js/resource-invalidation.js");

const valid = {
  schemaVersion: 1,
  eventId: "event-a", originClientId: "client-a",
  resource: "desordres",
  action: "update",
  ids: ["123"],
  originInstanceId: "instance-a",
  occurredAt: 123456789,
};
assert.equal(Invalidation.isValidMutationPayload(valid), true);
assert.deepEqual(Invalidation.normalizeMutationPayload(valid), valid);
assert.deepEqual(Invalidation.normalizeMutationPayload({
  ...valid,
  ids: ["id-1", "id-2", "id-1"],
  aspects: ["attributes", "geometry", "attributes"],
}), {
  ...valid,
  ids: ["id-1", "id-2"],
  aspects: ["attributes", "geometry"],
});
assert.deepEqual(Invalidation.normalizeMutationPayload({ ...valid, ids: null }), {
  ...valid, ids: null,
});
assert.deepEqual(Invalidation.normalizeMutationPayload({ ...valid, aspects: [] }), {
  ...valid, aspects: [],
});
assert.equal(Invalidation.isValidMutationPayload({
  ...valid, resource: "vegetations",
}), true); // Le protocole reste ouvert aux futures collections.

for (const invalid of [
  { ...valid, schemaVersion: 2 },
  { ...valid, resource: "" },
  { ...valid, resource: "Desordres" },
  { ...valid, resource: "desordres invalides" },
  { ...valid, action: "restore" },
  { ...valid, ids: "123" },
  { ...valid, ids: [] },
  { ...valid, ids: ["123", ""] },
  { ...valid, originInstanceId: "" },
  { ...valid, occurredAt: Number.NaN },
  { ...valid, occurredAt: -1 },
  { ...valid, aspects: "geometry" },
  { ...valid, aspects: ["unknown"] },
]) assert.equal(Invalidation.isValidMutationPayload(invalid), false);
''')

    def test_resource_profile_and_unit_resolution(self):
        self.run_node(r'''
const assert = require("node:assert/strict");
const Invalidation = require("./webapp/frontend/js/resource-invalidation.js");

const expectedProfiles = {
  systemes: ["heritageStructure"],
  digues: ["heritageStructure"],
  troncons: ["heritageStructure", "mappedEntity"],
  desordres: ["mappedEntity"],
  mobilier: ["mappedEntity"],
  "equipements-mesure": ["mappedEntity"],
  "reseaux-techniques": ["mappedEntity"],
  "ouvrages-particuliers": ["mappedEntity"],
  cheminements: ["mappedEntity"],
  vegetation: ["mappedEntity"],
  "amenagements-hydrauliques": ["heritageStructure", "mappedEntity"],
  "zones-stockage": ["heritageStructure", "mappedEntity"],
  prestations: ["mappedEntity"],
  "troncons-lit": ["mappedEntity"],
  "elements-morphologiques-lit": ["mappedEntity"],
  observations: ["childRecord"],
  photos: ["attachment"],
  "territoires-administratifs": ["mapContext"],
};
assert.deepEqual(Invalidation.RESOURCE_PROFILES, expectedProfiles);
for (const [resource, profiles] of Object.entries(expectedProfiles)) {
  assert.deepEqual(Invalidation.resolveProfiles(resource), profiles);
  assert.equal(Invalidation.resolveInvalidation(resource).knownResource, true);
}
for (const resource of ["troncons-lit", "elements-morphologiques-lit"]) {
  assert.deepEqual(Invalidation.resolveInvalidationUnits(resource), [
    "map-feature-store", "data-query-results", "open-detail",
  ]);
}
assert.deepEqual(Invalidation.resolveInvalidationUnits("desordres"), [
  "map-feature-store", "data-query-results", "open-detail",
]);
assert.deepEqual(Invalidation.resolveInvalidationUnits("troncons"), [
  "heritage-tree", "relation-options", "data-query-results", "open-detail",
  "map-feature-store",
]);
assert.deepEqual(Invalidation.resolveInvalidationUnits("observations"), [
  "data-query-results", "open-detail", "map-feature-store",
]);
assert.equal(
  Invalidation.resolveInvalidationUnits("troncons")
    .filter((unit) => unit === "data-query-results").length,
  1,
);
assert.deepEqual(Invalidation.resolveInvalidation("vegetations"), {
  knownResource: false, profiles: [], units: [],
});
''')

    def test_synthetic_invalidator_registry(self):
        self.run_node(r'''
const assert = require("node:assert/strict");
const Invalidation = require("./webapp/frontend/js/resource-invalidation.js");
const mutation = {
  eventId: "event-a", originClientId: "client-a",
  schemaVersion: 1, resource: "troncons", action: "update", ids: ["t-1"],
  originInstanceId: "remote", occurredAt: 12,
};
const calls = [];
const registry = Invalidation.createInvalidatorRegistry();
registry.registerInvalidator("heritage-tree", (received) => calls.push([
  "heritage-tree", received,
]));
registry.registerInvalidator("data-query-results", (received) => calls.push([
  "data-query-results", received,
]));
registry.registerInvalidator("map-feature-store", (received) => calls.push([
  "map-feature-store", received,
]));
registry.registerInvalidator("map-context", () => calls.push(["map-context"]));

const result = registry.invalidate(mutation);
assert.equal(result.accepted, true);
assert.equal(result.knownResource, true);
assert.equal(result.handlerCalls, 3);
assert.deepEqual(calls.map(([unit]) => unit), [
  "heritage-tree", "data-query-results", "map-feature-store",
]);
assert.deepEqual(calls[0][1], mutation);
assert.equal(calls.filter(([unit]) => unit === "data-query-results").length, 1);

const unknown = registry.invalidate({ ...mutation, resource: "vegetations" });
assert.deepEqual(unknown, {
  accepted: true, knownResource: false, profiles: [], units: [], handlerCalls: 0,
});
assert.equal(calls.length, 3);
assert.throws(() => registry.registerInvalidator("unknown-unit", () => {}), TypeError);
assert.throws(() => registry.registerInvalidator("open-detail", null), TypeError);
''')

    def test_failed_invalidation_recovery_reuses_the_existing_bounded_slot(self):
        self.run_node(r'''
const assert = require("node:assert/strict");
const Mutation = require("./webapp/frontend/js/resource-mutation.js");
const sent = [];
const bus = { subscribe() { return () => {}; }, publish(type, payload) { sent.push([type, payload]); } };
const controller = Mutation.createController({ bus, localInstanceId: "local", localClientId: "client" });
const calls = [];
let fail = true;
controller.registerInvalidator("open-detail", async mutation => {
  calls.push(mutation);
  if (fail) { fail = false; throw new Error("temporary read failure"); }
});
const mutation = Mutation.createMutationPayload({ resource: "desordres", action: "update",
  ids: ["D1"], eventId: "event-1", originInstanceId: "remote", originClientId: "client" },
  { localInstanceId: "remote", localClientId: "client" });
(async () => {
  assert.equal(controller.handleRemoteMutation(mutation).accepted, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1);
  controller.retryInvalidations();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].action, "update");
  assert.equal(calls[1].ids, null, "recovery rereads the resource instead of replaying an event");
  controller.retryInvalidations();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 2, "completed invalidations are not replayed");
  assert.deepEqual(sent, [], "invalidation recovery never publishes a business event");
  controller.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_publication_multicast_origin_guard_and_no_reemission(self):
        self.run_node(r'''
const assert = require("node:assert/strict");
const { createBus } = require("./webapp/frontend/js/inter-page-bus.js");
const Mutation = require("./webapp/frontend/js/resource-mutation.js");

class MockBroadcastChannel {
  static channels = [];
  constructor(name) {
    this.name = name;
    this.listeners = [];
    this.messages = [];
    MockBroadcastChannel.channels.push(this);
  }
  addEventListener(type, callback) {
    assert.equal(type, "message");
    this.listeners.push(callback);
  }
  postMessage(message) {
    this.messages.push(message);
    for (const peer of MockBroadcastChannel.channels) {
      if (peer !== this && peer.name === this.name) {
        for (const listener of peer.listeners) listener({ data: message });
      }
    }
  }
  close() {}
}

function create(source, instanceId) {
  const bus = createBus(source, { BroadcastChannelClass: MockBroadcastChannel });
  const controller = Mutation.createController({
    bus, localInstanceId: instanceId, now: () => 123456789,
  });
  const received = [];
  controller.registerInvalidator("map-feature-store", (payload) => received.push(payload));
  return { bus, controller, received };
}

const mapA = create("map", "map-a");
const mapB = create("map", "map-b");
const dataC = create("data", "data-c");
const dataD = create("data", "data-d");
(async () => {
const payload = await mapA.controller.publishMutation({
  resource: "desordres", action: "update", ids: ["123", "123"],
  aspects: ["attributes", "geometry", "attributes"],
});
assert.deepEqual(payload, {
  schemaVersion: 1,
  eventId: payload.eventId, originClientId: await mapA.controller.getClientId(),
  resource: "desordres",
  action: "update",
  ids: ["123"],
  originInstanceId: "map-a",
  occurredAt: 123456789,
  aspects: ["attributes", "geometry"],
});
assert.deepEqual(await mapA.controller.requestHeaders(), {
  "X-Digues-App-Client-Id": await mapA.controller.getClientId(),
  "X-Digues-App-Instance-Id": "map-a",
});
assert.equal(mapA.received.length, 0);
assert.deepEqual(mapB.received, [payload]);
assert.deepEqual(dataC.received, [payload]);
assert.deepEqual(dataD.received, [payload]);
for (const instance of [mapA, mapB, dataC, dataD]) {
  assert.equal(instance.bus.publish("unrelated", {}).type, "unrelated");
}

// Même si le bus évoluait pour réinjecter une publication locale, l’origine la bloque.
assert.deepEqual(mapA.controller.handleRemoteMutation(payload), {
  accepted: false, reason: "local-origin",
});
assert.equal(mapA.received.length, 0);
assert.deepEqual(mapA.controller.handleRemoteMutation({ ...payload, ids: "123" }), {
  accepted: false, reason: "invalid-payload",
});

// Les invalidateurs n’ont aucun accès au bus : aucune réception ne republie.
const mutationPublicationCounts = MockBroadcastChannel.channels.map((channel) => (
  channel.messages.filter(({ type }) => type === Mutation.EVENT_TYPE).length
));
assert.deepEqual(mutationPublicationCounts, [1, 0, 0, 0]);
})();
''')

    def test_pages_load_the_infrastructure_and_register_consumers_without_producers(self):
        service_worker = (FRONTEND_DIRECTORY / "service-worker.js").read_text(
            encoding="utf-8"
        )
        for module_name in ("resource-invalidation.js", "resource-mutation.js"):
            self.assertEqual(
                service_worker.count(f'"/static/js/{module_name}"'), 1
            )

        for page_name, entry_point in (("map.html", "map.js"), ("data.html", "data-view.js")):
            page = (FRONTEND_DIRECTORY / page_name).read_text(encoding="utf-8")
            with self.subTest(page=page_name):
                invalidation = page.index('src="/static/js/resource-invalidation.js"')
                mutation = page.index('src="/static/js/resource-mutation.js"')
                interface = page.index('src="/static/js/interface-coordination.js"')
                entry = page.index(f'src="/static/js/{entry_point}"')
                self.assertLess(interface, invalidation)
                self.assertLess(invalidation, mutation)
                self.assertLess(mutation, entry)

        map_entry = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("MapResourceInvalidators.register", map_entry)
        self.assertIn("publishConfirmedMutation", map_entry)
        detail_entry = (FRONTEND_DIRECTORY / "js" / "data-entity-detail.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("onMutationConfirmed", detail_entry)
        data_entry = (FRONTEND_DIRECTORY / "js" / "data-view.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("DataResourceInvalidators.register", data_entry)
        self.assertIn(
            "ResourceMutationController.publishMutation(mutation, { applyLocally: true })",
            data_entry,
        )


if __name__ == "__main__":
    unittest.main()
