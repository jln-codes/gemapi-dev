(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ResourceConcurrency = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const CONFLICT_MESSAGE = "Cet objet a changé sur le serveur. Votre brouillon est conservé. Annulez les modifications puis relisez la fiche avant de réessayer.";
  function headers(baseVersion) {
    if (!Number.isSafeInteger(baseVersion) || baseVersion < 1) {
      throw new Error("Version de départ indisponible. Relisez la fiche avant de la modifier.");
    }
    return { "If-Match": `"${baseVersion}"` };
  }
  function httpError(status, payload, fallback) {
    const detail = payload?.detail;
    const associationConflict = status === 409
      && detail?.code === "disorder-already-associated";
    const geometryContractConflict = status === 409
      && detail?.code === "geometry-contract-changed";
    const error = new Error(associationConflict
      ? detail.message || "Ce Désordre est déjà associé à un autre aménagement hydraulique. Retirez-le de la sélection pour continuer."
      : geometryContractConflict ? detail.message || "Le contrat géométrique a changé. Votre brouillon est conservé."
      : status === 409 ? CONFLICT_MESSAGE
      : typeof detail === "string" ? detail : detail?.message || fallback);
    error.status = status;
    error.detail = detail;
    error.code = detail?.code || null;
    return error;
  }
  function isVersionConflict(error) {
    return error?.status === 409 && error?.code !== "disorder-already-associated";
  }
  return { CONFLICT_MESSAGE, headers, httpError, isVersionConflict };
});
