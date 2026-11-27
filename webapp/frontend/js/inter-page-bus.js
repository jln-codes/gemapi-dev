(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }

  const source = root.document?.currentScript?.dataset.source;
  root.InterPageBus = api.createBus(source, {
    BroadcastChannelClass: root.BroadcastChannel,
    channelName: root.BusinessRuntime?.coordinationKey("inter-page") || "digues-app:inter-page",
  });
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const CHANNEL_NAME = "digues-app:inter-page";
  const SOURCES = new Set(["map", "data"]);

  function isValidType(type) {
    return typeof type === "string" && type.trim() === type && type.length > 0;
  }

  function isValidMessage(message) {
    return message !== null &&
      typeof message === "object" &&
      !Array.isArray(message) &&
      isValidType(message.type) &&
      SOURCES.has(message.source) &&
      Object.prototype.hasOwnProperty.call(message, "payload");
  }

  function createMessage(type, source, payload) {
    const message = { type, source, payload };
    if (!isValidMessage(message)) {
      throw new TypeError("Message inter-pages invalide");
    }
    return message;
  }

  function createBus(source, { BroadcastChannelClass, channelName = CHANNEL_NAME } = {}) {
    if (!SOURCES.has(source)) {
      throw new TypeError("Source inter-pages invalide");
    }

    const subscribers = new Map();
    const channel = typeof BroadcastChannelClass === "function"
      ? new BroadcastChannelClass(channelName)
      : null;

    channel?.addEventListener("message", (event) => {
      const message = event.data;
      if (!isValidMessage(message)) return;

      for (const callback of [...(subscribers.get(message.type) || [])]) {
        callback(message.payload, message);
      }
    });

    function publish(type, payload) {
      const message = createMessage(type, source, payload);
      channel?.postMessage(message);
      return message;
    }

    function subscribe(type, callback) {
      if (!isValidType(type)) {
        throw new TypeError("Type d’événement inter-pages invalide");
      }
      if (typeof callback !== "function") {
        throw new TypeError("Abonné inter-pages invalide");
      }

      const callbacks = subscribers.get(type) || new Set();
      callbacks.add(callback);
      subscribers.set(type, callbacks);

      return function unsubscribe() {
        callbacks.delete(callback);
        if (callbacks.size === 0) subscribers.delete(type);
      };
    }

    function close() {
      subscribers.clear();
      channel?.close();
    }

    return { publish, subscribe, close };
  }

  return { CHANNEL_NAME, createBus, createMessage, isValidMessage };
});
