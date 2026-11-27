(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PwaGeolocation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const DISPLAY_MODE = "(display-mode: standalone)";
  const POSITION_OPTIONS = Object.freeze({
    enableHighAccuracy: true,
    maximumAge: 0,
    timeout: 20_000,
  });
  const services = new WeakMap();

  function serviceError(code, detail = null) {
    const messages = {
      PERMISSION_DENIED: "Accès à la position refusé.",
      POSITION_UNAVAILABLE: "Position indisponible.",
      TIMEOUT: "Acquisition de la position expirée.",
      NOT_STANDALONE: "La géolocalisation est réservée à la PWA installée.",
      UNSUPPORTED: "Géolocalisation indisponible sur cet appareil.",
      INVALID_POSITION: "Position reçue invalide.",
      CANCELLED: "Acquisition de la position annulée.",
    };
    const error = new Error(messages[code]);
    error.code = code;
    error.detail = detail;
    return error;
  }

  function normalizeError(error) {
    if (error?.code === "PERMISSION_DENIED" || error?.code === 1) {
      return serviceError("PERMISSION_DENIED", error?.message || null);
    }
    if (error?.code === "TIMEOUT" || error?.code === 3) {
      return serviceError("TIMEOUT", error?.message || null);
    }
    return serviceError("POSITION_UNAVAILABLE", error?.message || null);
  }

  function normalizePosition(position) {
    const { longitude, latitude, accuracy } = position?.coords || {};
    const timestamp = position?.timestamp;
    if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180
        || !Number.isFinite(latitude) || latitude < -90 || latitude > 90
        || (accuracy != null && (!Number.isFinite(accuracy) || accuracy < 0))
        || !Number.isFinite(timestamp) || timestamp < 0) {
      throw serviceError("INVALID_POSITION");
    }
    return { longitude, latitude, accuracy: accuracy ?? null, timestamp };
  }

  function createService({ windowTarget = root, navigatorTarget = windowTarget?.navigator } = {}) {
    const key = navigatorTarget || windowTarget;
    if (!key || (typeof key !== "object" && typeof key !== "function")) {
      throw new TypeError("Contexte de géolocalisation invalide.");
    }
    if (services.has(key)) return services.get(key);

    const mediaQuery = windowTarget?.matchMedia?.(DISPLAY_MODE);
    const subscribers = new Map();
    let nextSubscriberId = 0;
    let pendingRequest = null;
    let activeWatch = null;

    function isStandalone() {
      return Boolean(windowTarget?.matchMedia?.(DISPLAY_MODE)?.matches);
    }

    function geolocation(requiredMethods) {
      if (!isStandalone()) throw serviceError("NOT_STANDALONE");
      const provider = navigatorTarget?.geolocation;
      if (!provider || requiredMethods.some((name) => typeof provider[name] !== "function")) {
        throw serviceError("UNSUPPORTED");
      }
      return provider;
    }

    function clearNativeWatch(watch) {
      if (watch.cleared) return;
      watch.cleared = true;
      try {
        watch.provider.clearWatch(watch.id);
      } catch (error) {
        windowTarget?.console?.error?.("Arrêt du suivi GPS impossible.", error);
      }
    }

    function stopNativeWatch() {
      const watch = activeWatch;
      if (!watch) return;
      activeWatch = null;
      watch.active = false;
      if (watch.id === null) {
        watch.clearWhenReady = true;
      } else {
        clearNativeWatch(watch);
      }
    }

    function stopAll() {
      pendingRequest?.cancel();
      subscribers.clear();
      stopNativeWatch();
    }

    function requestCurrentPosition() {
      pendingRequest?.cancel();
      let provider;
      try {
        provider = geolocation(["getCurrentPosition"]);
      } catch (error) {
        return { promise: Promise.reject(error), cancel() {} };
      }
      let resolvePromise;
      let rejectPromise;
      const promise = new Promise((resolve, reject) => {
        resolvePromise = resolve;
        rejectPromise = reject;
      });
      const request = {
        active: true,
        cancel() {
          if (!request.active) return;
          request.active = false;
          if (pendingRequest === request) pendingRequest = null;
          rejectPromise(serviceError("CANCELLED"));
        },
      };
      pendingRequest = request;
      function settle(position, error) {
        if (!request.active || pendingRequest !== request) return;
        request.active = false;
        pendingRequest = null;
        if (error) rejectPromise(error);
        else resolvePromise(position);
      }
      try {
        provider.getCurrentPosition(
          (position) => {
            if (!request.active || pendingRequest !== request) return;
            if (!isStandalone()) {
              settle(null, serviceError("NOT_STANDALONE"));
              return;
            }
            try {
              settle(normalizePosition(position), null);
            } catch (error) {
              settle(null, error);
            }
          },
          (error) => settle(null, normalizeError(error)),
          { ...POSITION_OPTIONS },
        );
      } catch (error) {
        settle(null, normalizeError(error));
      }
      return { promise, cancel: request.cancel };
    }

    function notifySubscribers(kind, value) {
      for (const [id, subscriber] of [...subscribers]) {
        if (!subscribers.has(id)) continue;
        try {
          if (kind === "position") subscriber.onPosition(value);
          else subscriber.onError(value);
        } catch (error) {
          windowTarget?.console?.error?.("Consommateur de géolocalisation en échec.", error);
        }
      }
    }

    function subscribe(onPosition, onError = () => {}) {
      if (typeof onPosition !== "function" || typeof onError !== "function") {
        throw new TypeError("Callbacks de géolocalisation invalides.");
      }
      const provider = geolocation(["watchPosition", "clearWatch"]);
      const id = ++nextSubscriberId;
      subscribers.set(id, { onPosition, onError });
      if (!activeWatch) {
        const watch = {
          provider, id: null, active: true, cleared: false, clearWhenReady: false,
        };
        activeWatch = watch;
        try {
          const nativeId = provider.watchPosition(
            (position) => {
              if (activeWatch !== watch || !watch.active) return;
              if (!isStandalone()) {
                stopAll();
                return;
              }
              try {
                notifySubscribers("position", normalizePosition(position));
              } catch (error) {
                notifySubscribers("error", error);
              }
            },
            (error) => {
              if (activeWatch !== watch || !watch.active) return;
              if (!isStandalone()) {
                stopAll();
                return;
              }
              const normalized = normalizeError(error);
              notifySubscribers("error", normalized);
              if (normalized.code === "PERMISSION_DENIED") stopAll();
            },
            { ...POSITION_OPTIONS },
          );
          watch.id = nativeId;
          if (watch.clearWhenReady) clearNativeWatch(watch);
        } catch (error) {
          subscribers.delete(id);
          stopNativeWatch();
          throw normalizeError(error);
        }
      }
      let subscribed = true;
      return function unsubscribe() {
        if (!subscribed) return;
        subscribed = false;
        subscribers.delete(id);
        if (subscribers.size === 0) stopNativeWatch();
      };
    }

    async function queryPermission() {
      if (!isStandalone()) throw serviceError("NOT_STANDALONE");
      if (typeof navigatorTarget?.permissions?.query !== "function") return null;
      try {
        const status = await navigatorTarget.permissions.query({ name: "geolocation" });
        if (!isStandalone()) throw serviceError("NOT_STANDALONE");
        return ["granted", "prompt", "denied"].includes(status?.state)
          ? status.state : null;
      } catch (error) {
        if (error?.code === "NOT_STANDALONE") throw error;
        return null;
      }
    }

    windowTarget?.addEventListener?.("pagehide", stopAll);
    if (typeof mediaQuery?.addEventListener === "function") {
      mediaQuery.addEventListener("change", () => {
        if (!isStandalone()) stopAll();
      });
    }
    const service = {
      isStandalone, requestCurrentPosition, subscribe, stopAll, queryPermission,
    };
    services.set(key, service);
    return service;
  }

  return {
    createService,
    isStandalone: () => createService().isStandalone(),
    requestCurrentPosition: () => createService().requestCurrentPosition(),
    subscribe: (onPosition, onError) => createService().subscribe(onPosition, onError),
    stopAll: () => createService().stopAll(),
    queryPermission: () => createService().queryPermission(),
  };
});
