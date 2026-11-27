(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }

  const interfaceType = root.document?.currentScript?.dataset.interface;
  const otherInterface = interfaceType === "map" ? "data" : "map";
  root.InterfaceCoordination = api.createController({
    interfaceType,
    bus: root.InterPageBus,
    targetLink: root.document?.querySelector(
      `.view-switcher[data-interface-target="${otherInterface}"]`,
    ),
    openInNewTabCheckbox: root.document?.querySelector("#open-interface-in-new-tab"),
    windowTarget: root,
    documentTarget: root.document,
  });
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const INTERFACES = new Set(["map", "data"]);
  const PRESENCE_EVENT = "interface-presence";
  const ACTIVITY_EVENT = "interface-activity";
  const LEAVING_EVENT = "interface-leaving";
  const OPEN_IN_NEW_TAB_PREFERENCE_KEY = "digues-app:interface-open-new-tab-enabled";
  const WINDOW_NAMES = Object.freeze({
    map: globalThis.BusinessRuntime?.key("digues-app-map") || "digues-app-map",
    data: globalThis.BusinessRuntime?.key("digues-app-data") || "digues-app-data",
  });
  const HEARTBEAT_INTERVAL_MS = 15_000;
  const PRESENCE_TTL_MS = 90_000;

  function browserStorage() {
    try {
      return globalThis.localStorage;
    } catch (_error) {
      return null;
    }
  }

  function readOpenInNewTabPreference(storage = browserStorage()) {
    try {
      return storage?.getItem(OPEN_IN_NEW_TAB_PREFERENCE_KEY) !== "false";
    } catch (_error) {
      return true;
    }
  }

  function writeOpenInNewTabPreference(enabled, storage = browserStorage()) {
    try {
      storage?.setItem(OPEN_IN_NEW_TAB_PREFERENCE_KEY, String(Boolean(enabled)));
    } catch (_error) {
      // La préférence reste valable pour la page courante si le stockage est bloqué.
    }
  }

  function createInstanceId(cryptoObject, now = Date.now, random = Math.random) {
    try {
      if (typeof cryptoObject?.randomUUID === "function") {
        return cryptoObject.randomUUID();
      }
      if (typeof cryptoObject?.getRandomValues === "function") {
        const values = new Uint32Array(4);
        cryptoObject.getRandomValues(values);
        return [...values].map((value) => value.toString(16).padStart(8, "0")).join("-");
      }
    } catch (_error) {
      // Le fallback reste propre à ce chargement de page.
    }
    return `${now().toString(36)}-${random().toString(36).slice(2)}-${random().toString(36).slice(2)}`;
  }

  function validInstanceId(value) {
    return typeof value === "string" && value.trim() === value
      && value.length > 0 && value.length <= 200;
  }

  function validInstanceMessage(payload) {
    return payload && typeof payload === "object" && !Array.isArray(payload)
      && INTERFACES.has(payload.interface)
      && validInstanceId(payload.instanceId)
      && Number.isFinite(payload.timestamp);
  }

  function normalizePresence(payload) {
    if (!validInstanceMessage(payload)) return null;
    return {
      interface: payload.interface,
      instanceId: payload.instanceId,
      timestamp: payload.timestamp,
      requestReply: payload.requestReply === true,
    };
  }

  function normalizeActivity(payload) {
    if (!validInstanceMessage(payload)) return null;
    return {
      interface: payload.interface,
      instanceId: payload.instanceId,
      timestamp: payload.timestamp,
    };
  }

  function compareTargets(left, right) {
    const leftHasActivity = Number.isFinite(left.lastActiveAt);
    const rightHasActivity = Number.isFinite(right.lastActiveAt);
    if (leftHasActivity !== rightHasActivity) return leftHasActivity ? -1 : 1;
    if (leftHasActivity && left.lastActiveAt !== right.lastActiveAt) {
      return right.lastActiveAt - left.lastActiveAt;
    }
    if (left.lastSeenAt !== right.lastSeenAt) {
      return right.lastSeenAt - left.lastSeenAt;
    }
    return left.instanceId.localeCompare(right.instanceId);
  }

  function createController({
    interfaceType,
    bus,
    targetLink,
    openInNewTabCheckbox = null,
    storage = browserStorage(),
    windowTarget = typeof window !== "undefined" ? window : null,
    documentTarget = typeof document !== "undefined" ? document : null,
    cryptoObject = typeof crypto !== "undefined" ? crypto : null,
    instanceId = createInstanceId(cryptoObject),
    now = () => Date.now(),
    setIntervalFn = (callback, delay) => setInterval(callback, delay),
    clearIntervalFn = (identifier) => clearInterval(identifier),
    heartbeatIntervalMs = HEARTBEAT_INTERVAL_MS,
    presenceTtlMs = PRESENCE_TTL_MS,
  }) {
    if (!INTERFACES.has(interfaceType) || !bus || !targetLink
      || !validInstanceId(instanceId)) {
      throw new Error("Configuration de coordination des interfaces incomplète.");
    }

    const otherInterface = interfaceType === "map" ? "data" : "map";
    const presence = new Map([...INTERFACES].map((name) => [name, new Map()]));
    const unsubscribers = [];
    let active = false;
    let heartbeatTimer = null;
    let openInNewTabEnabled = readOpenInNewTabPreference(storage);

    function refreshOpenInNewTabPreference() {
      if (storage) {
        try {
          openInNewTabEnabled = storage.getItem(OPEN_IN_NEW_TAB_PREFERENCE_KEY) !== "false";
        } catch (_error) {
          // Conserver l’état en mémoire lorsque le stockage devient indisponible.
        }
      }
      if (openInNewTabCheckbox) openInNewTabCheckbox.checked = openInNewTabEnabled;
      return openInNewTabEnabled;
    }

    refreshOpenInNewTabPreference();

    function instancePayload() {
      return {
        interface: interfaceType,
        instanceId,
        timestamp: now(),
      };
    }

    function announcePresence(requestReply = false) {
      bus.publish(PRESENCE_EVENT, { ...instancePayload(), requestReply });
    }

    function announceActivity() {
      bus.publish(ACTIVITY_EVENT, instancePayload());
    }

    function announceLeaving() {
      bus.publish(LEAVING_EVENT, { ...instancePayload(), requestReply: false });
    }

    function isVisibleAndFocused() {
      if (documentTarget?.visibilityState !== "visible") return false;
      return typeof documentTarget?.hasFocus !== "function" || documentTarget.hasFocus();
    }

    function announceActivityIfActive() {
      if (active && isVisibleAndFocused()) announceActivity();
    }

    function prunePresence() {
      const oldestAllowed = now() - presenceTtlMs;
      for (const instances of presence.values()) {
        for (const [identifier, record] of instances) {
          if (record.lastSeenAt < oldestAllowed) instances.delete(identifier);
        }
      }
    }

    function knownInstances(name) {
      prunePresence();
      return [...(presence.get(name)?.values() || [])].sort(compareTargets);
    }

    function getOtherInterfacePresence() {
      return knownInstances(otherInterface)[0] || null;
    }

    unsubscribers.push(bus.subscribe(PRESENCE_EVENT, (payload, message) => {
      const announcement = normalizePresence(payload);
      if (!announcement || message?.source !== announcement.interface
        || announcement.instanceId === instanceId) return;
      const instances = presence.get(announcement.interface);
      const previous = instances.get(announcement.instanceId);
      instances.set(announcement.instanceId, {
        interface: announcement.interface,
        instanceId: announcement.instanceId,
        lastSeenAt: now(),
        lastActiveAt: previous?.lastActiveAt ?? null,
      });
      if (announcement.requestReply && active) announcePresence(false);
    }));

    unsubscribers.push(bus.subscribe(ACTIVITY_EVENT, (payload, message) => {
      const activity = normalizeActivity(payload);
      if (!activity || message?.source !== activity.interface
        || activity.instanceId === instanceId) return;
      const instances = presence.get(activity.interface);
      const previous = instances.get(activity.instanceId);
      if (!previous) return;
      instances.set(activity.instanceId, {
        interface: activity.interface,
        instanceId: activity.instanceId,
        lastSeenAt: previous.lastSeenAt,
        lastActiveAt: Math.max(previous.lastActiveAt ?? -Infinity, activity.timestamp),
      });
    }));

    unsubscribers.push(bus.subscribe(LEAVING_EVENT, (payload, message) => {
      const leaving = normalizePresence(payload);
      if (!leaving || message?.source !== leaving.interface) return;
      presence.get(leaving.interface).delete(leaving.instanceId);
    }));

    function startPresence() {
      if (active) return;
      active = true;
      announcePresence(true);
      announceActivityIfActive();
      heartbeatTimer = setIntervalFn(() => {
        prunePresence();
        announcePresence(false);
      }, heartbeatIntervalMs);
    }

    function stopPresence({ leaving = true } = {}) {
      if (!active) return;
      if (leaving) announceLeaving();
      active = false;
      if (heartbeatTimer !== null) clearIntervalFn(heartbeatTimer);
      heartbeatTimer = null;
    }

    targetLink.addEventListener("click", (event) => {
      if (event.defaultPrevented
        || (event.button !== undefined && event.button !== 0)
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!refreshOpenInNewTabPreference()) return;
      event.preventDefault();
      if (!windowTarget) return;
      const currentName = windowTarget.name;
      if (!currentName || Object.values(WINDOW_NAMES).includes(currentName)) {
        windowTarget.name = WINDOW_NAMES[interfaceType];
      }
      const targetWindow = windowTarget.open?.(
        targetLink.href,
        WINDOW_NAMES[otherInterface],
      );
      targetWindow?.focus?.();
    });

    openInNewTabCheckbox?.addEventListener("change", () => {
      openInNewTabEnabled = Boolean(openInNewTabCheckbox.checked);
      writeOpenInNewTabPreference(openInNewTabEnabled, storage);
    });

    const onPageHide = () => stopPresence();
    const onPageShow = () => startPresence();
    const onFocus = () => announceActivityIfActive();
    const onVisibilityChange = () => {
      if (documentTarget?.visibilityState === "visible") announceActivityIfActive();
    };
    const onStorage = (event) => {
      if (event.key === OPEN_IN_NEW_TAB_PREFERENCE_KEY) refreshOpenInNewTabPreference();
    };
    windowTarget?.addEventListener?.("pagehide", onPageHide);
    windowTarget?.addEventListener?.("pageshow", onPageShow);
    windowTarget?.addEventListener?.("focus", onFocus);
    windowTarget?.addEventListener?.("storage", onStorage);
    documentTarget?.addEventListener?.("visibilitychange", onVisibilityChange);
    startPresence();

    function destroy() {
      stopPresence();
      unsubscribers.forEach((unsubscribe) => unsubscribe());
      windowTarget?.removeEventListener?.("pagehide", onPageHide);
      windowTarget?.removeEventListener?.("pageshow", onPageShow);
      windowTarget?.removeEventListener?.("focus", onFocus);
      windowTarget?.removeEventListener?.("storage", onStorage);
      documentTarget?.removeEventListener?.("visibilitychange", onVisibilityChange);
    }

    return {
      destroy,
      getOtherInterfacePresence,
      instanceId,
      isOpenInNewTabEnabled: () => openInNewTabEnabled,
    };
  }

  return {
    ACTIVITY_EVENT,
    HEARTBEAT_INTERVAL_MS,
    LEAVING_EVENT,
    OPEN_IN_NEW_TAB_PREFERENCE_KEY,
    PRESENCE_EVENT,
    PRESENCE_TTL_MS,
    WINDOW_NAMES,
    compareTargets,
    createController,
    createInstanceId,
    normalizeActivity,
    normalizePresence,
    readOpenInNewTabPreference,
    writeOpenInNewTabPreference,
  };
});
