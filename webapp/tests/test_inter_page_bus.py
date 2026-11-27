from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
FRONTEND_DIRECTORY = PROJECT_DIRECTORY / "webapp" / "frontend"


class InterPageBusTest(unittest.TestCase):
    def test_message_validation_publication_and_typed_delivery(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")

        program = r'''
const assert = require("node:assert/strict");
const {
  CHANNEL_NAME, createBus, createMessage, isValidMessage,
} = require("./webapp/frontend/js/inter-page-bus.js");

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
      if (peer !== this && peer.name === this.name) peer.emit(message);
    }
  }

  emit(message) {
    for (const callback of this.listeners) callback({ data: message });
  }

  close() {}
}

const built = createMessage("selection:changed", "map", { id: "abc" });
assert.deepEqual(built, {
  type: "selection:changed", source: "map", payload: { id: "abc" },
});
assert.equal(isValidMessage(built), true);
for (const invalid of [
  null,
  [],
  { source: "map", payload: {} },
  { type: "", source: "map", payload: {} },
  { type: "selection:changed", source: "unknown", payload: {} },
  { type: "selection:changed", source: "map" },
]) assert.equal(isValidMessage(invalid), false);
assert.throws(() => createMessage("", "map", {}), TypeError);

const mapBus = createBus("map", { BroadcastChannelClass: MockBroadcastChannel });
const dataBus = createBus("data", { BroadcastChannelClass: MockBroadcastChannel });
const selected = [];
let otherTypeCalls = 0;
dataBus.subscribe("selection:changed", (payload, message) => {
  selected.push({ payload, message });
});
dataBus.subscribe("mutation:completed", () => { otherTypeCalls += 1; });

const published = mapBus.publish("selection:changed", { id: "abc" });
assert.deepEqual(published, built);
assert.equal(MockBroadcastChannel.channels[0].name, CHANNEL_NAME);
assert.deepEqual(MockBroadcastChannel.channels[0].messages, [built]);
assert.deepEqual(selected, [{ payload: { id: "abc" }, message: built }]);
assert.equal(otherTypeCalls, 0);

MockBroadcastChannel.channels[1].emit({ type: "selection:changed", source: "map" });
MockBroadcastChannel.channels[1].emit({ type: "selection:changed", source: "bad", payload: {} });
assert.equal(selected.length, 1);

const isolatedBus = createBus("map");
assert.doesNotThrow(() => isolatedBus.publish("presence:changed", null));
'''
        subprocess.run(
            [node, "-e", program],
            cwd=PROJECT_DIRECTORY,
            check=True,
        )

    def test_map_and_data_load_the_bus_with_their_own_source(self):
        map_page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        map_tag = '<script type="application/x-business-script" data-business-src="/static/js/inter-page-bus.js" data-source="map"></script>'
        data_tag = '<script type="application/x-business-script" data-business-src="/static/js/inter-page-bus.js" data-source="data"></script>'

        self.assertEqual(map_page.count(map_tag), 1)
        self.assertEqual(data_page.count(data_tag), 1)
        self.assertLess(map_page.index(map_tag), map_page.index(
            '<script type="application/x-business-script" data-business-src="/static/js/map.js"></script>'
        ))
        self.assertLess(data_page.index(data_tag), data_page.index(
            '<script type="application/x-business-script" data-business-src="/static/js/data-view.js"></script>'
        ))

    def test_offline_shell_includes_the_bus(self):
        service_worker = (FRONTEND_DIRECTORY / "service-worker.js").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            service_worker.count('"/static/js/inter-page-bus.js"'),
            1,
        )


if __name__ == "__main__":
    unittest.main()
