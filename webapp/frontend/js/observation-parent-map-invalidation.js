(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ObservationParentMapInvalidation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function create({ fetchObservation, reloadDisorder }) {
    if (typeof fetchObservation !== "function" || typeof reloadDisorder !== "function") {
      throw new TypeError("Invalidateur parent Observation invalide");
    }
    // Kept until the parent feature has been reread. This lets the generic
    // invalidation recovery (ids=null) retry a failed targeted dependency.
    const pending = new Map();

    async function refresh(observationId, mutation) {
      const identifier = String(observationId ?? "").trim();
      if (!identifier) return false;
      let parentId = pending.get(identifier) || null;
      if (!parentId) {
        const observation = await fetchObservation(identifier);
        if (String(observation?.id ?? "") !== identifier) {
          throw new Error("Identifiant Observation incohérent.");
        }
        const parent = observation?.parent;
        if (parent?.resource !== "desordres" || !parent.id) {
          pending.delete(identifier);
          return false;
        }
        parentId = String(parent.id);
        pending.set(identifier, parentId);
      }
      await reloadDisorder(parentId, mutation);
      pending.delete(identifier);
      return true;
    }

    async function invalidate(mutation) {
      if (mutation?.resource !== "observations") return false;
      if (Array.isArray(mutation.ids)) {
        mutation.ids.forEach((id) => {
          const identifier = String(id ?? "").trim();
          if (identifier && !pending.has(identifier)) pending.set(identifier, null);
        });
      }
      const identifiers = [...pending.keys()];
      if (!identifiers.length) return false;
      await Promise.all(identifiers.map((id) => refresh(id, mutation)));
      return true;
    }

    return { invalidate, pendingIds: () => [...pending.keys()] };
  }

  return { create };
});
