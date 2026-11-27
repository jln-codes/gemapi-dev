(function (root, factory) {
  const dependency = typeof module === "object" && module.exports
    ? require("./resource-invalidation.js") : root.ResourceInvalidation;
  const api = factory(dependency);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ResourceEvents = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (Invalidation) {
  "use strict";

  const CLIENT_KEY = "digues-app:client-id";
  const DEDUP_TTL_MS = 24 * 60 * 60 * 1000;
  const DEDUP_MAX_SIZE = 10_000;
  function createId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function createClientIdentity(storage, locks = globalThis.navigator?.locks) {
    let fallback;
    function readOrCreate() {
      try {
        const existing = storage?.getItem(CLIENT_KEY);
        if (existing && existing.trim() === existing && existing.length <= 200) return existing;
        fallback ||= createId();
        storage?.setItem(CLIENT_KEY, fallback);
        return storage?.getItem(CLIENT_KEY) || fallback;
      } catch (_error) {
        return fallback ||= createId();
      }
    }
    // Serialize the first creation across tabs, not just subsequent reads.
    const ready = locks?.request
      ? locks.request(CLIENT_KEY, readOrCreate).catch(readOrCreate)
      : Promise.resolve(readOrCreate());
    return { ready, getId: async () => { await ready; return readOrCreate(); } };
  }

  function createDispatcher({ consume, now = Date.now,
    ttlMs = DEDUP_TTL_MS, maxSize = DEDUP_MAX_SIZE } = {}) {
    if (typeof consume !== "function" || !(ttlMs > 0)
        || !Number.isInteger(maxSize) || maxSize < 1) throw new TypeError("Dispatcher invalide");
    const seen = new Map();
    function prune() {
      for (const [id, at] of seen) {
        if (now() - at < ttlMs) break;
        seen.delete(id);
      }
    }
    function remember(id) {
      prune();
      if (seen.has(id)) return false;
      seen.set(id, now());
      while (seen.size > maxSize) seen.delete(seen.keys().next().value);
      return true;
    }
    function dispatch(payload) {
      const event = Invalidation.normalizeMutationPayload(payload);
      if (!event) return { accepted: false, reason: "invalid-payload" };
      if (!remember(event.eventId)) return { accepted: false, reason: "duplicate-event" };
      return consume(event);
    }
    return { dispatch, remember, forget: (id) => seen.delete(id), clear: () => seen.clear(),
      size: () => { prune(); return seen.size; } };
  }

  function createBusTransport(bus, receive) {
    const unsubscribe = bus.subscribe("resource-mutated", receive);
    return {
      send: (event) => bus.publish("resource-mutated", event),
      close: () => unsubscribe?.(),
    };
  }
  return { CLIENT_KEY, DEDUP_TTL_MS, DEDUP_MAX_SIZE, createId,
    createClientIdentity, createDispatcher, createBusTransport };
});
