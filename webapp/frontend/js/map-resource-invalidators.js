(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MapResourceInvalidators = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ORDER = Object.freeze([
    "heritage-tree",
    "relation-options",
    "map-feature-store",
    "open-detail",
    "map-context",
  ]);

  function register({ mutationController, consumers, onError = console.error }) {
    if (!mutationController || !consumers) {
      throw new Error("Consommateurs d’invalidation Carte incomplets.");
    }
    const scheduled = new WeakMap();
    const unregister = [];

    function report(error, unit) {
      try { onError(error, unit); } catch (_error) { /* Isolation locale. */ }
    }

    function schedule(mutation, unit) {
      let state = scheduled.get(mutation);
      if (!state) {
        state = { units: new Set(), errors: new Map(), promise: null };
        scheduled.set(mutation, state);
        state.promise = Promise.resolve().then(async () => {
          for (const orderedUnit of ORDER) {
            if (!state.units.has(orderedUnit)) continue;
            try {
              await consumers[orderedUnit]?.(mutation);
            } catch (error) {
              report(error, orderedUnit);
              state.errors.set(orderedUnit, error);
            }
          }
          scheduled.delete(mutation);
        });
      }
      state.units.add(unit);
      return state.promise.then(() => {
        if (state.errors.has(unit)) throw state.errors.get(unit);
      });
    }

    ORDER.forEach((unit) => {
      if (typeof consumers[unit] !== "function") return;
      unregister.push(mutationController.registerInvalidator(
        unit,
        (mutation) => schedule(mutation, unit),
      ));
    });

    return function unregisterMapInvalidators() {
      unregister.forEach((remove) => remove());
    };
  }

  return { ORDER, register };
});
