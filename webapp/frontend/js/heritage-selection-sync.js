(function (root, factory) {
  const dependency = typeof module === "object" && module.exports
    ? require("./heritage-panel.js") : root.HeritagePanel;
  const heritageSelectionSync = factory(dependency);
  if (typeof module === "object" && module.exports) {
    module.exports = heritageSelectionSync;
  }
  root.HeritageSelectionSync = heritageSelectionSync;
})(typeof globalThis !== "undefined" ? globalThis : this, function (HeritagePanel) {
  "use strict";

  const EVENT_TYPE = "heritage-selection-changed";
  const PREFERENCE_KEY = "digues-app:heritage-selection-sync-enabled";

  function browserStorage() {
    try {
      return globalThis.localStorage;
    } catch (_error) {
      return null;
    }
  }

  function readPreference(storage = browserStorage()) {
    try {
      return storage?.getItem(PREFERENCE_KEY) !== "false";
    } catch (_error) {
      return true;
    }
  }

  function writePreference(enabled, storage = browserStorage()) {
    try {
      storage?.setItem(PREFERENCE_KEY, String(Boolean(enabled)));
    } catch (_error) {
      // La préférence reste valable pour la page courante si le stockage est bloqué.
    }
  }

  function createController({
    bus,
    checkbox,
    heritageController,
    storage = browserStorage(),
    windowTarget = typeof window !== "undefined" ? window : null,
  }) {
    if (!bus || !checkbox || !heritageController) {
      throw new Error("Configuration de synchronisation Patrimoine incomplète.");
    }

    let enabled = readPreference(storage);
    checkbox.checked = enabled;

    function refreshPreference() {
      if (storage) {
        try {
          enabled = storage.getItem(PREFERENCE_KEY) !== "false";
        } catch (_error) {
          // Conserver l’état en mémoire lorsque le stockage devient indisponible.
        }
      }
      checkbox.checked = enabled;
      return enabled;
    }

    function publish(selection) {
      const normalized = HeritagePanel.normalizeSelection(selection);
      if (normalized) bus.publish(EVENT_TYPE, normalized);
    }

    function selectionChanged(selection, { origin = "user" } = {}) {
      if (origin === "user" && refreshPreference()) publish(selection);
    }

    checkbox.addEventListener("change", () => {
      const wasEnabled = enabled;
      enabled = Boolean(checkbox.checked);
      writePreference(enabled, storage);
      if (!wasEnabled && enabled) publish(heritageController.getSelection());
    });

    windowTarget?.addEventListener("storage", (event) => {
      if (event.key === PREFERENCE_KEY) refreshPreference();
    });

    bus.subscribe(EVENT_TYPE, (payload) => {
      if (!refreshPreference()) return;
      const selection = HeritagePanel.normalizeSelection(payload);
      if (!selection) return;
      heritageController.applySelection(selection, { origin: "remote" });
    });

    return {
      isEnabled: () => enabled,
      selectionChanged,
    };
  }

  return { EVENT_TYPE, PREFERENCE_KEY, createController, readPreference, writePreference };
});
