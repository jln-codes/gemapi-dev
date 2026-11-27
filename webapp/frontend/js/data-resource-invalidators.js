(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DataResourceInvalidators = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function register({
    mutationController,
    heritageController,
    viewController,
    detailController,
    onError = (error) => console.error("Actualisation Base impossible", error),
  }) {
    if (!mutationController || !heritageController
        || !viewController || !detailController) {
      throw new Error("Consommateurs d’invalidation Base incomplets.");
    }

    const heritageReloads = new WeakMap();
    const unregister = [];
    function report(error) {
      try { onError(error); } catch (_error) { /* Le rafraîchissement reste isolé. */ }
    }

    unregister.push(mutationController.registerInvalidator(
      "heritage-tree",
      (mutation) => {
        const reload = heritageController.reload({ propagateError: true });
        heritageReloads.set(mutation, reload);
        return reload;
      },
    ));

    unregister.push(mutationController.registerInvalidator(
      "data-query-results",
      async (mutation) => {
        try {
          await heritageReloads.get(mutation);
        } catch (error) {
          report(error);
        }
        await viewController.invalidate(mutation);
      },
    ));

    unregister.push(mutationController.registerInvalidator(
      "open-detail",
      (mutation) => detailController.invalidate(mutation),
    ));

    return function unregisterDataInvalidators() {
      unregister.forEach((remove) => remove());
    };
  }

  return { register };
});
