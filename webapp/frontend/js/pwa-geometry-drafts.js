(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PwaGeometryDrafts = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Separate from the field projection and, in particular, its operations store.
  function createStore({ indexedDB, runtime }) {
    if (!runtime?.context?.key || !runtime.isActive?.()) return null;
    const databaseName = runtime.key("digues-app-gps-drafts");
    function open() {
      return new Promise((resolve, reject) => {
        if (!indexedDB) { reject(new Error("IndexedDB indisponible.")); return; }
        const request = indexedDB.open(databaseName, 1);
        let blocked = false;
        request.onupgradeneeded = () => request.result.createObjectStore("tracks");
        request.onsuccess = () => {
          if (blocked) request.result.close();
          else resolve(request.result);
        };
        request.onerror = () => reject(request.error || new Error("Ouverture du stockage GPS impossible."));
        request.onblocked = () => {
          blocked = true;
          reject(new Error("Stockage GPS occupé par un autre onglet."));
        };
      });
    }
    async function transaction(mode, key, value, trackId, expectedRevision, expectedRecord) {
      const db = await open();
      try {
        return await new Promise((resolve, reject) => {
          const tx = db.transaction("tracks", mode);
          const store = tx.objectStore("tracks");
          let result;
          let conflict = null;
          const request = store.get(key);
          request.onsuccess = () => {
            result = request.result;
            if (mode === "readonly") return;
            if (expectedRecord !== undefined) {
              let unchanged = false;
              try { unchanged = JSON.stringify(result) === JSON.stringify(expectedRecord); }
              catch (_) { /* A non-JSON record cannot be safely compared. */ }
              if (!unchanged) {
                conflict = new Error("Le relevé GPS a changé dans un autre onglet.");
                tx.abort();
              } else store.delete(key);
              return;
            }
            if (result && result.trackId !== trackId) {
              conflict = new Error("Un autre onglet possède ce relevé GPS.");
              tx.abort();
              return;
            }
            if (mode === "readwrite" && (result?.revision ?? 0) !== expectedRevision) {
              conflict = new Error("Le relevé GPS a changé dans un autre onglet.");
              tx.abort();
              return;
            }
            if (value === undefined) store.delete(key);
            else store.put(value, key);
          };
          tx.oncomplete = () => resolve(result);
          tx.onerror = () => reject(tx.error || new Error("Stockage GPS impossible."));
          tx.onabort = () => {
            if (conflict) conflict.code = "GPS_DRAFT_CONFLICT";
            reject(conflict || tx.error || new Error("Stockage GPS interrompu."));
          };
        });
      } finally { db.close(); }
    }
    return Object.freeze({
      read: key => transaction("readonly", key),
      write: (key, value) => transaction("readwrite", key, value, value.trackId, value.revision - 1),
      remove: (key, trackId, revision) => transaction("readwrite", key, undefined, trackId, revision),
      removeInvalid: (key, record) => transaction("readwrite", key, undefined, null, null, record),
    });
  }

  function draftKey({ resource, mode, id }) {
    if (!resource || !["create", "edit"].includes(mode) || mode === "edit" && !id) return null;
    return `${resource}:${mode}:${mode === "create" ? "new" : id}`;
  }

  function validDraft(draft, context) {
    const baseValid = [1, 2].includes(draft?.schemaVersion) && draft?.contextKey === context.contextKey
      && draft?.key === draftKey(context) && typeof draft.trackId === "string"
      && Number.isSafeInteger(draft.revision) && draft.revision > 0
      && typeof draft.territoryKey === "string"
      && ["LineString", "Polygon"].includes(draft.type)
      && (draft.schemaVersion === 1 ? ["recording", "paused", "preview", "error"]
        : ["recording", "paused", "adjusting", "error"]).includes(draft.phase)
      && Array.isArray(draft.points) && draft.points.length <= 10000
      && draft.points.every(point => Number.isFinite(point?.longitude)
        && Number.isFinite(point?.latitude) && Number.isFinite(point?.timestamp)
        && Math.abs(point.longitude) <= 180 && Math.abs(point.latitude) <= 90
        && Number.isFinite(point?.accuracy));
    if (!baseValid || draft.schemaVersion === 1) return baseValid;
    if (!Number.isSafeInteger(draft.geometryRevision) || draft.geometryRevision < 0) return false;
    // An editable candidate is allowed to be topologically invalid. Structural
    // corruption is preserved for explicit recovery/abandon, never discarded.
    const candidate = geometry => {
      if (geometry?.type !== draft.type || !Array.isArray(geometry.coordinates)) return false;
      const rings = geometry.type === "Polygon" ? geometry.coordinates : [geometry.coordinates];
      if (!rings.length || rings.length > 10000) return false;
      let count = 0;
      return rings.every(ring => Array.isArray(ring) && (count += ring.length) <= 10001
        && ring.every(point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)
          && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90));
    };
    if (draft.phase !== "adjusting") return true;
    return candidate(draft.rawGeometry) && candidate(draft.geometry)
      && (draft.correctedGeometry === null || candidate(draft.correctedGeometry))
      && ["raw", "corrected"].includes(draft.choice)
      && (draft.choice !== "corrected" || Boolean(draft.correctedGeometry))
      && draft.editedGeometries && typeof draft.editedGeometries === "object"
      && Object.entries(draft.editedGeometries).every(([choice, geometry]) =>
        ["raw", "corrected"].includes(choice) && candidate(geometry));
  }

  return { createStore, draftKey, validDraft };
});
