(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LocalProjection = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const RESOURCES = Object.freeze(["systemes", "digues", "troncons", "desordres", "observations"]);
  // v4 adds photoMedia and excludes older readers that cannot retain originals.
  // All v2/v3 stores and intentions are retained unchanged.
  const SCHEMA_VERSION = 4;
  const uuid = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
  function canonicalCreate(input) {
    const texts = ["designation", "evolution", "suite_apporter_id", "suite", "urgence_id"];
    const allowed = ["id", "parent", "date", "nombre_desordres", "valid", ...texts];
    if (!input || Object.keys(input).some(k => !allowed.includes(k)) || !uuid(input.id)
        || !["desordres", "troncons"].includes(input.parent?.resource) || !uuid(input.parent.id)
        || Object.keys(input.parent).some(k => !["resource", "id"].includes(k))) throw new TypeError("Parent ou UUID de création invalide.");
    const result = { id: input.id.toLowerCase(), parent: { resource: input.parent.resource, id: input.parent.id.toLowerCase() } };
    for (const key of texts) {
      if (input[key] != null && typeof input[key] !== "string") throw new TypeError("Champ textuel invalide : " + key);
      result[key] = input[key]?.trim() || null;
    }
    const date = input.date ?? null;
    if (date !== null && (typeof date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(date)
        || !Number.isFinite(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date)) throw new TypeError("Date invalide.");
    result.date = date;
    result.nombre_desordres = input.nombre_desordres ?? null;
    if (result.nombre_desordres !== null && (!Number.isSafeInteger(result.nombre_desordres)
        || result.nombre_desordres < 0 || result.nombre_desordres > 2147483647)) throw new TypeError("Nombre de désordres invalide.");
    result.valid = input.valid === undefined ? true : input.valid;
    if (typeof result.valid !== "boolean" || (result.urgence_id !== null && result.parent.resource !== "desordres")) throw new TypeError("Combinaison métier invalide.");
    return result;
  }
  function localCreation(operation) {
    return { resource: "observations", id: operation.resourceId, source: "local-create", serverVersion: null,
      editable: operation.status === "pending" && operation.attempts === 0,
      syncState: operation.status === "confirmed" ? "confirmed-create" : "pending-create", operationId: operation.operationId,
      payload: { ...structuredClone(operation.changes), parent: { ...operation.parentContext }, version: null,
        photos: [], photos_count: 0, localStatus: operation.status } };
  }
  function canonicalPhotoCreate(input) {
    const allowed = ["id", "observation_id", "date", "libelle", "designation"];
    if (!input || Object.keys(input).some(key => !allowed.includes(key))
        || !uuid(input.id) || !uuid(input.observation_id)) throw new TypeError("Photo ou Observation invalide.");
    const result = { id: input.id.toLowerCase(), observation_id: input.observation_id.toLowerCase() };
    for (const key of ["libelle", "designation"]) {
      if (input[key] != null && typeof input[key] !== "string") throw new TypeError("Métadonnée Photo invalide : " + key);
      result[key] = input[key]?.trim() || null;
      if (result[key]?.length > 2000) throw new TypeError("Métadonnée Photo trop longue : " + key);
    }
    const date = input.date ?? null;
    if (date !== null && (typeof date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(date)
        || !Number.isFinite(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date)) {
      throw new TypeError("Date Photo invalide.");
    }
    result.date = date;
    return result;
  }
  function localPhoto(operation) {
    return { ...structuredClone(operation.changes), version: null, content_available: false, content_url: null,
      localStatus: operation.mediaLocalRef ? operation.status : "media-required", dependsOn: structuredClone(operation.dependsOn), operationId: operation.operationId };
  }
  function photoObservationContext(operation) {
    const context = operation.observationContext;
    if (context?.createdLocally || !Number.isSafeInteger(context?.observedVersion)) return null;
    return { resource: "observations", id: context.id, source: "photo-context", serverVersion: context.observedVersion,
      payload: { id: context.id, version: context.observedVersion, designation: context.designation,
        parent: context.parent || null, photos: [] } };
  }
  const pendingScopeError = () => Object.assign(new Error("Des opérations locales restent non résolues : périmètre conservé."), { code: "pending-operations" });
  const validCursor = value => typeof value === "string" && /^(0|[1-9]\d{0,18})$/.test(value)
    && BigInt(value) <= 9223372036854775807n;
  function validateRecord(record) {
    const attributes = record?.payload?.properties || record?.payload;
    if (!RESOURCES.includes(record?.resource) || typeof record.id !== "string" || !record.id
        || !Number.isSafeInteger(record.serverVersion) || record.serverVersion < 1
        || attributes?.id !== record.id || attributes?.version !== record.serverVersion) {
      throw new TypeError("Projection autoritaire invalide");
    }
  }
  function createStore({ indexedDB: suppliedIndexedDB, name = "sirs-field-projection" } = {}) {
    let opening;
    function open() {
      if (opening) return opening;
      opening = new Promise((resolve, reject) => {
        // Some privacy/security policies throw on property access itself.
        // Resolve that access inside the promise so the caller can stay online-only.
        const indexedDB = suppliedIndexedDB === undefined ? globalThis.indexedDB : suppliedIndexedDB;
        if (!indexedDB) { reject(new Error("IndexedDB indisponible")); return; }
        const request = indexedDB.open(name, SCHEMA_VERSION);
        let blocked = false;
        request.onupgradeneeded = event => {
          // Deterministic v0 -> v1. Never delete the database after a failed upgrade.
          if (event.oldVersion === 0) {
            request.result.createObjectStore("resources", { keyPath: ["resource", "id"] });
            request.result.createObjectStore("metadata", { keyPath: "key" });
          }
          if (event.oldVersion < 2) {
            const operations = request.result.createObjectStore("operations", { keyPath: "operationId" });
            operations.createIndex("resourceKey", "resourceKey", { unique: true });
            operations.createIndex("sequence", "sequence", { unique: true });
          }
          if (event.oldVersion < 4) {
            const media = request.result.createObjectStore("photoMedia", { keyPath: "mediaLocalRef" });
            media.createIndex("photoId", "photoId", { unique: true });
          }
        };
        request.onerror = () => reject(request.error);
        request.onblocked = () => {
          blocked = true;
          reject(new Error("Migration IndexedDB bloquée : fermer les anciennes pages."));
        };
        request.onsuccess = () => {
          const db = request.result;
          if (blocked) { db.close(); return; }
          db.onversionchange = () => { db.close(); opening = null; };
          resolve(db);
        };
      });
      return opening;
    }
    async function transaction(mode, run) {
      const db = await open();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(["resources", "metadata", "operations", "photoMedia"], mode, { durability: "strict" });
        let result;
        tx.oncomplete = () => resolve(result);
        tx.onabort = () => reject(tx.error || new Error("Transaction IndexedDB annulée"));
        tx.onerror = () => {}; // onabort is the authoritative failure (quota included).
        const guard = callback => (...args) => {
          try { callback(...args); }
          catch (error) {
            try { tx.abort(); } catch (_) { /* already aborted */ }
            reject(error);
          }
        };
        guard(() => run(tx.objectStore("resources"), tx.objectStore("metadata"),
          value => { result = value; }, tx, guard))();
      });
    }
    function inspect() {
      return transaction("readonly", (rows, meta, done, _tx, guard) => {
        const records = rows.getAll(), scope = meta.get("scope");
        scope.onsuccess = guard(() => {
          records.result.forEach(validateRecord);
          const value = scope.result;
          if ((!value && records.result.length) || (value && (!validCursor(value.cursor)
              || typeof value.revision !== "string" || !Array.isArray(value.tronconIds)))) {
            throw new Error("Métadonnées de projection corrompues ; reconstruction explicite nécessaire");
          }
          done({ records: records.result, scope: value || null });
        });
      });
    }
    function replace(snapshot, expectedRevision = null) {
      snapshot = structuredClone(snapshot);
      if (snapshot?.schemaVersion !== 1 || !validCursor(snapshot.cursor)
          || !Array.isArray(snapshot.records) || snapshot.records.length > 2000 || !Array.isArray(snapshot.tronconIds)
          || !snapshot.tronconIds.length || snapshot.tronconIds.length > 20
          || snapshot.tronconIds.some(id => typeof id !== "string" || !id)) {
        throw new TypeError("Instantané de périmètre invalide");
      }
      snapshot.records.forEach(validateRecord);
      if (new Set(snapshot.records.map(r => JSON.stringify([r.resource, r.id]))).size !== snapshot.records.length) {
        throw new TypeError("Identifiant de projection dupliqué");
      }
      return transaction("readwrite", (rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations").getAll();
        const previousRows = rows.getAll(), request = meta.get("scope");
        request.onsuccess = guard(() => {
          const previous = request.result;
          // Compare-and-swap also protects clear/reprepare against an in-flight tab.
          if ((previous?.revision || null) !== expectedRevision
              || (previous && BigInt(snapshot.cursor) < BigInt(previous.cursor))) { done(false); return; }
          const versions = new Map(previousRows.result.map(row => [JSON.stringify([row.resource, row.id]), row.serverVersion]));
          if (snapshot.records.some(row => (versions.get(JSON.stringify([row.resource, row.id])) || 0) > row.serverVersion)) {
            tx.abort(); return;
          }
          rows.clear();
          // Preserve the last known graph if pending intentions lose a dependency
          // on the server. Detached rows are NOT served as current query results.
          if (operations.result.length) {
            const keys = new Set(snapshot.records.map(r => JSON.stringify([r.resource, r.id])));
            const previousByKey = new Map(previousRows.result.map(r => [JSON.stringify([r.resource, r.id]), r]));
            const bases = new Map(operations.result.map(o => [o.resourceKey, o.basePayload]));
            const pinned = new Set();
            function retain(resource, id) {
              if (!id) return;
              const key = JSON.stringify([resource, id]);
              if (pinned.has(key)) return;
              pinned.add(key);
              for (const payload of [bases.get(key), previousByKey.get(key)?.payload]) {
                const values = payload?.properties || payload;
                if (!values) continue;
                if (resource === "observations") {
                  retain("desordres", values.desordre_id);
                  if (values.parent) retain(values.parent.resource, values.parent.id);
                }
                if (resource === "desordres") (values.troncon_ids || []).forEach(id => retain("troncons", id));
                if (resource === "troncons") retain("digues", values.digue_id);
                if (resource === "digues") retain("systemes", values.systeme_endiguement_id);
              }
            }
            operations.result.forEach(o => {
              retain(o.resource, o.resourceId);
              if (o.resource === "observations" && o.action === "create") retain(o.changes.parent.resource, o.changes.parent.id);
              if (o.resource === "photos" && o.action === "create") retain("observations", o.dependsOn?.[0]?.id);
            });
            previousRows.result.filter(r => pinned.has(JSON.stringify([r.resource, r.id])) && !keys.has(JSON.stringify([r.resource, r.id])))
              .forEach(r => rows.put({ ...r, syncState: "stale", detached: true }));
          }
          snapshot.records.forEach(row => rows.put({ ...row, syncState: "synced", cachedAt: Date.now() }));
          meta.put({ key: "scope", revision: globalThis.crypto.randomUUID(), cursor: snapshot.cursor,
            tronconIds: snapshot.tronconIds, cachedAt: Date.now() });
          done(true);
        });
      });
    }
    function markStale() {
      return transaction("readwrite", (rows, _meta, _done, _tx, guard) => {
        const request = rows.openCursor();
        request.onsuccess = guard(() => {
          const cursor = request.result;
          if (cursor) { cursor.update({ ...cursor.value, syncState: "stale" }); cursor.continue(); }
        });
      });
    }
    function integratePinned(operationId, recovery) {
      recovery = structuredClone(recovery);
      recovery.records.forEach(validateRecord);
      return transaction("readwrite", (rows, _meta, done, tx, guard) => {
        const operation = tx.objectStore("operations").get(operationId);
        const previous = rows.getAll();
        previous.onsuccess = guard(() => {
          const intent = operation.result;
          if (!intent) { done(null); return; } // explicit concurrent abandon of a conflict
          if (recovery.resource !== intent.resource || recovery.id !== intent.resourceId) {
            throw new Error("Récupération sans intention correspondante");
          }
          const versions = new Map(previous.result.map(row => [JSON.stringify([row.resource, row.id]), row]));
          recovery.records.forEach(row => {
            const old = versions.get(JSON.stringify([row.resource, row.id]));
            if (!old || old.serverVersion <= row.serverVersion) rows.put({ ...row,
              detached: old?.detached ?? true, syncState: "synced", cachedAt: Date.now() });
          });
          // Deliberately no metadata/cursor write: the rest of the scope is stale.
          done(recovery.records.find(row => row.resource === intent.resource && row.id === intent.resourceId) || null);
        });
      });
    }
    function clear() {
      return transaction("readwrite", (rows, meta, _done, tx, guard) => {
        const request = tx.objectStore("operations").count();
        request.onsuccess = guard(() => {
          if (request.result) throw pendingScopeError();
          rows.clear(); meta.clear();
        });
      });
    }
    function listOperations() {
      return transaction("readonly", (_rows, _meta, done, tx, guard) => {
        const request = tx.objectStore("operations").index("sequence").getAll();
        request.onsuccess = guard(() => done(request.result));
      });
    }
    function enqueue({ resource, resourceId, baseVersion, changes }) {
      const fields = { desordres: ["designation", "commentaire", "lieu_dit"], observations: ["suite"] };
      if (!fields[resource] || typeof resourceId !== "string" || !Number.isSafeInteger(baseVersion) || baseVersion < 1
          || !changes || !Object.keys(changes).length || Object.entries(changes).some(([key, value]) =>
            !fields[resource].includes(key) || (value !== null && typeof value !== "string"))) {
        throw new TypeError("Écriture hors ligne non prise en charge (attributs textuels uniquement).");
      }
      changes = structuredClone(changes);
      return transaction("readwrite", (rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations");
        const resourceKey = JSON.stringify([resource, resourceId]);
        const existing = operations.index("resourceKey").get(resourceKey);
        const row = rows.get([resource, resourceId]);
        const count = meta.get("operation-sequence");
        count.onsuccess = guard(() => {
          if (existing.result) throw new Error("Une opération non résolue existe déjà pour cet objet. Consultez Synchronisation.");
          if (!row.result || row.result.detached || row.result.serverVersion !== baseVersion) {
            throw new Error("Objet non préparé à cette version ; relisez avant de créer une opération locale.");
          }
          if (resource === "observations" && row.result.payload.parent
              && row.result.payload.parent.resource !== "desordres") throw new Error("La modification d’une Observation de Tronçon n’est pas prise en charge par le PUT actuel.");
          const sequence = (count.result?.value || 0) + 1;
          const operation = { operationId: crypto.randomUUID(), resource, resourceId, action: "update", baseVersion,
            changes, resourceKey, sequence, createdAt: Date.now(), status: "pending", attempts: 0,
            basePayload: row.result.payload };
          operations.add(operation);
          meta.put({ key: "operation-sequence", value: sequence });
          done(operation);
        });
      });
    }
    function updateOperation(operationId, patch) {
      const allowed = ["status", "attempts", "retryAt", "message", "response"];
      if (Object.keys(patch).some(key => !allowed.includes(key))) throw new TypeError("Intention immuable");
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          if (!request.result) throw new Error("Opération introuvable");
          const next = { ...request.result, ...structuredClone(patch) };
          if (next.resource === "photos" && next.immutableIntent && next.attempts < request.result.attempts) {
            throw new Error("Une tentative Photo ne peut pas être annulée.");
          }
          if (!["pending", "syncing", "conflict", "failed", "confirmed"].includes(next.status)) throw new TypeError("État invalide");
          if (next.resource === "photos" && next.action === "create" && !next.mediaLocalRef
              && (next.status !== "pending" || next.attempts !== 0 || next.response)) {
            throw new Error("Photo sans média : aucune tentative ni confirmation serveur possible.");
          }
          operations.put(next); done(next);
        });
      });
    }
    function enqueueCreate({ operationId, changes }) {
      changes = canonicalCreate(changes);
      if (!uuid(operationId)) throw new TypeError("Operation-Id invalide.");
      return transaction("readwrite", (rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), resourceKey = JSON.stringify(["observations", changes.id]);
        const existing = operations.index("resourceKey").get(resourceKey), target = rows.get(["observations", changes.id]);
        const parent = rows.get([changes.parent.resource, changes.parent.id]), count = meta.get("operation-sequence");
        count.onsuccess = guard(() => {
          if (existing.result || target.result) throw new Error("Une observation ou intention utilise déjà cet UUID.");
          if (!parent.result || parent.result.detached) throw new Error("Le parent doit être présent dans le périmètre préparé.");
          const attributes = parent.result.payload.properties || parent.result.payload;
          const sequence = (count.result?.value || 0) + 1;
          const operation = { operationId, resource: "observations", resourceId: changes.id, action: "create", changes,
            parentContext: { ...changes.parent, designation: attributes.designation || attributes.libelle || changes.parent.id },
            resourceKey, sequence, createdAt: Date.now(), status: "pending", attempts: 0 };
          operations.add(operation); meta.put({ key: "operation-sequence", value: sequence }); done(operation);
        });
      });
    }
    function enqueuePhotoCreate({ operationId, changes, observation, media = null }) {
      changes = canonicalPhotoCreate(changes);
      if (media) validateMedia(media);
      if (!uuid(operationId) || observation?.id !== changes.observation_id
          || (observation.version !== null && (!Number.isSafeInteger(observation.version) || observation.version < 1))) {
        throw new TypeError("Identité ou version de l’Observation invalide.");
      }
      return transaction("readwrite", (rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), resourceKey = JSON.stringify(["photos", changes.id]);
        const existing = operations.index("resourceKey").get(resourceKey);
        const parentIntent = operations.index("resourceKey").get(JSON.stringify(["observations", changes.observation_id]));
        const parentRow = rows.get(["observations", changes.observation_id]);
        const count = meta.get("operation-sequence");
        count.onsuccess = guard(() => {
          if (existing.result) throw new Error("Une Photo locale utilise déjà cet UUID.");
          const intent = parentIntent.result, row = parentRow.result;
          if (observation.version === null && (!intent || intent.action !== "create")) {
            throw new Error("L’Observation locale doit conserver son intention de création.");
          }
          if (intent?.action === "create" && (intent.resourceId !== changes.observation_id
              || intent.changes.id !== changes.observation_id)) throw new Error("Dépendance Observation incohérente.");
          if (!intent && !row && observation.version === null) throw new Error("Observation locale absente.");
          if (row && row.serverVersion < 1) throw new Error("Observation non confirmée.");
          const sequence = (count.result?.value || 0) + 1;
          const operation = { operationId: operationId.toLowerCase(), resource: "photos", resourceId: changes.id,
            action: "create", changes, dependsOn: [{ resource: "observations", id: changes.observation_id }],
            observationContext: { id: changes.observation_id,
              designation: typeof observation.designation === "string" ? observation.designation : null,
              parent: observation.parent && typeof observation.parent.resource === "string" && uuid(observation.parent.id)
                ? { resource: observation.parent.resource, id: observation.parent.id,
                  designation: typeof observation.parent.designation === "string" ? observation.parent.designation : null }
                : null,
              observedVersion: observation.version, createdLocally: observation.version === null || intent?.action === "create" },
            resourceKey, sequence, createdAt: Date.now(), status: "pending", attempts: 0 };
          if (media) installMedia(tx, operation, media);
          operations.add(operation); meta.put({ key: "operation-sequence", value: sequence }); done(operation);
        });
      });
    }
    function editPhotoCreate(operationId, input, media = null) {
      const changes = canonicalPhotoCreate(input);
      if (media) validateMedia(media);
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const old = request.result;
          if (!old || old.resource !== "photos" || old.action !== "create" || old.immutableIntent || old.attempts !== 0 || old.status !== "pending") {
            throw new Error("Création Photo déjà tentée : intention figée.");
          }
          if (changes.id !== old.resourceId || changes.observation_id !== old.dependsOn?.[0]?.id) {
            throw new Error("Rattachement et UUID Photo immuables.");
          }
          if (!media && JSON.stringify(changes) === JSON.stringify(old.changes)) { done(old); return; }
          const next = { ...old, changes, operationId: crypto.randomUUID() };
          if (media) {
            if (old.transferStarted) throw new Error("Transfert déjà commencé : le fichier est conservé.");
            if (old.mediaLocalRef) tx.objectStore("photoMedia").delete(old.mediaLocalRef);
            installMedia(tx, next, media);
          }
          operations.delete(operationId); operations.add(next); done(next);
        });
      });
    }
    function photoEligibility(operationId) {
      return transaction("readonly", (rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const operation = request.result;
          if (!operation || operation.resource !== "photos" || operation.action !== "create"
              || operation.dependsOn?.length !== 1 || operation.dependsOn[0].resource !== "observations"
              || operation.dependsOn[0].id !== operation.changes.observation_id) {
            throw new Error("Intention Photo ou dépendance invalide.");
          }
          const parentId = operation.dependsOn[0].id;
          const parent = rows.get(["observations", parentId]);
          const parentIntent = operations.index("resourceKey").get(JSON.stringify(["observations", parentId]));
          parentIntent.onsuccess = guard(() => {
            const active = parentIntent.result;
            let dependency = active ? `parent-${active.status}` : "parent-synced";
            if (!active && !parent.result && operation.observationContext.createdLocally) dependency = "parent-abandoned";
            if (!active && !parent.result && !operation.observationContext.observedVersion) dependency = "parent-unverified";
            const media = operation.mediaLocalRef ? tx.objectStore("photoMedia").get(operation.mediaLocalRef) : null;
            const finish = () => {
              const available = media?.result;
              const ready = available?.blob instanceof Blob && available.blob.size === available.size
                && available.photoId === operation.resourceId && available.validationVersion === 1;
              const reason = dependency !== "parent-synced" ? dependency : !ready ? "media-required"
                : operation.status === "confirmed" ? "confirmed" : ["conflict", "failed"].includes(operation.status)
                  ? operation.status : "ready";
              done({ eligible: dependency === "parent-synced" && ready && !["conflict", "failed"].includes(operation.status),
                dependency, reason, observationId: parentId });
            };
            if (media) media.onsuccess = guard(finish); else finish();
          });
        });
      });
    }
    function discardObservationWithPhotos(operationId, photoOperationIds) {
      if (!Array.isArray(photoOperationIds)) throw new TypeError("Photos dépendantes attendues.");
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId), all = operations.getAll();
        all.onsuccess = guard(() => {
          const parent = request.result;
          if (!parent || parent.resource !== "observations" || parent.action !== "create"
              || !((parent.attempts === 0 && parent.status === "pending") || ["conflict", "failed"].includes(parent.status))) {
            throw new Error("Abandon de l’Observation indisponible.");
          }
          const children = all.result.filter(o => o.resource === "photos" && o.action === "create"
            && o.dependsOn?.[0]?.resource === "observations" && o.dependsOn[0].id === parent.resourceId);
          if (children.length !== photoOperationIds.length || children.some(o => !photoOperationIds.includes(o.operationId)
              || o.immutableIntent || o.status !== "pending" || o.attempts !== 0)) throw new Error("Liste des Photos locales modifiée : confirmez à nouveau.");
          children.forEach(o => {
            if (o.mediaLocalRef) tx.objectStore("photoMedia").delete(o.mediaLocalRef);
            operations.delete(o.operationId);
          });
          operations.delete(operationId); done(children.length);
        });
      });
    }
    function editCreate(operationId, input) {
      const changes = canonicalCreate(input);
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const old = request.result;
          if (!old || old.action !== "create" || old.attempts !== 0 || old.status !== "pending") throw new Error("Création déjà tentée : intention figée jusqu’à réconciliation.");
          if (changes.id !== old.resourceId || JSON.stringify(changes.parent) !== JSON.stringify(old.changes.parent)) throw new Error("Rattachement et UUID immuables.");
          if (JSON.stringify(changes) === JSON.stringify(old.changes)) { done(old); return; }
          const next = { ...old, changes, operationId: crypto.randomUUID() };
          operations.delete(operationId); operations.add(next); done(next);
        });
      });
    }
    function claimOperation(operationId) {
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const old = request.result;
          if (!old || !["pending", "syncing"].includes(old.status)) { done(null); return; }
          if (old.resource === "photos" && old.action === "create") { done(null); return; }
          const next = { ...old, status: "syncing", attempts: old.attempts + 1 };
          operations.put(next); done(next);
        });
      });
    }
    function removeOperation(operationId, discard = false) {
      return transaction("readwrite", (rows, _meta, _done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          if (!request.result) return;
          const intent = request.result;
          if (intent.resource === "photos") {
            if (!discard || intent.immutableIntent || intent.attempts > 0 || intent.status === "confirmed") throw new Error("Photo tentée : réconciliation nécessaire avant suppression.");
            if (intent.mediaLocalRef) tx.objectStore("photoMedia").delete(intent.mediaLocalRef);
            operations.delete(operationId); return;
          }
          const unsent = intent.action === "create" && intent.attempts === 0 && intent.status === "pending";
          if (discard ? !(unsent || ["conflict", "failed"].includes(intent.status)) : intent.status !== "confirmed") {
            throw new Error("Résultat inconnu : opération conservée pour réconciliation.");
          }
          if (discard && intent.resource === "observations" && intent.action === "create") {
            const all = operations.getAll();
            all.onsuccess = guard(() => {
              if (all.result.some(o => o.resource === "photos" && o.dependsOn?.[0]?.resource === "observations"
                  && o.dependsOn[0].id === intent.resourceId)) {
                throw new Error("Des Photos locales dépendent de cette Observation : abandon groupé requis.");
              }
              operations.delete(operationId);
            });
            return;
          }
          if (!discard && intent.action === "create") {
            const target = rows.get([intent.resource, intent.resourceId]);
            target.onsuccess = guard(() => {
              const row = target.result, parent = row?.payload?.parent;
              if (!row || row.serverVersion < intent.response.mutation.versions[intent.resourceId]
                  || parent?.id !== intent.changes.parent.id || parent?.resource !== intent.changes.parent.resource) throw new Error("Création confirmée mais projection non intégrée.");
              operations.delete(operationId);
            });
          } else operations.delete(operationId);
        });
      });
    }
    // Only this store owns binary lifetime. Scope replacement never clears it.
    function validateMedia(media) {
      if (!(media.blob instanceof Blob) || media.blob.size !== media.size || media.size < 1 || media.size > 20 * 1024 * 1024
          || !/^[0-9a-f]{64}$/.test(media.sha256) || !["image/jpeg", "image/png", "image/webp"].includes(media.mimeType)
          || media.validationVersion !== 1) throw new TypeError("Média local non vérifié.");
    }
    function installMedia(tx, operation, input) {
      const media = { mediaLocalRef: crypto.randomUUID(), photoId: operation.resourceId, observationId: operation.changes.observation_id,
        originalFilename: input.originalFilename, mimeType: input.mimeType, size: input.size, sha256: input.sha256,
        width: input.width, height: input.height, blob: input.blob, validationVersion: 1, createdAt: Date.now(),
        transfer: { state: "local", attempts: 0 }, transportBinding: null, confirmation: null };
      operation.mediaLocalRef = media.mediaLocalRef;
      tx.objectStore("photoMedia").add(media);
    }
    function readPhotoMedia(ref) {
      return transaction("readonly", (_r, _m, done, tx, guard) => {
        const request = tx.objectStore("photoMedia").get(ref);
        request.onsuccess = guard(() => done(request.result || null));
      });
    }
    function confirmedPhotos(observationId) {
      return transaction("readonly", (_r, _m, done, tx, guard) => {
        const result = [], cursor = tx.objectStore("photoMedia").openCursor();
        cursor.onsuccess = guard(() => {
          const row = cursor.result;
          if (!row) { done(result); return; }
          const value = row.value;
          if (value.observationId === observationId && value.integrated) result.push({
            mediaLocalRef: value.mediaLocalRef, photo: value.confirmation, originalFilename: value.originalFilename });
          row.continue();
        });
      });
    }
    function changePhoto(operationId, run) {
      return transaction("readwrite", (_r, _m, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const operation = request.result;
          if (!operation) { done(null); return; }
          if (operation.resource !== "photos" || !operation.mediaLocalRef) throw new Error("Photo sans média.");
          const mediaStore = tx.objectStore("photoMedia"), read = mediaStore.get(operation.mediaLocalRef);
          read.onsuccess = guard(() => {
            const media = read.result;
            if (!media || media.photoId !== operation.resourceId) throw new Error("Média local introuvable.");
            const result = run(operation, media);
            if (result === false) { done(null); return; }
            mediaStore.put(media); operations.put(operation); done(result || operation);
          });
        });
      });
    }
    function beginPhotoUpload(operationId) {
      return changePhoto(operationId, (operation, media) => {
        validateMedia(media);
        operation.transferStarted = true;
        media.transfer = { state: "preparing", attempts: media.transfer.attempts + 1 };
      });
    }
    function bindPhotoUpload(operationId, descriptor) {
      return changePhoto(operationId, (operation, media) => {
        if (!uuid(descriptor.upload_ref) || descriptor.id !== operation.resourceId
            || descriptor.observation_id !== operation.changes.observation_id || descriptor.file_sha256 !== media.sha256
            || descriptor.file_size !== media.size || descriptor.mime_type !== media.mimeType
            || !Number.isFinite(descriptor.expires_at)) throw new Error("Préparation serveur incohérente.");
        media.transportBinding = { upload_ref: descriptor.upload_ref, expires_at: descriptor.expires_at };
        media.transfer.state = "prepared";
      });
    }
    function claimPhotoPost(operationId) {
      return changePhoto(operationId, (operation, media) => {
        validateMedia(media);
        if (!["pending", "syncing"].includes(operation.status) || !media.transportBinding) return false;
        operation.immutableIntent ||= { ...operation.changes, file_sha256: media.sha256, file_size: media.size };
        operation.attempts++; operation.status = "syncing";
        return { operation, payload: { ...operation.immutableIntent, upload_ref: media.transportBinding.upload_ref } };
      });
    }
    function confirmPhoto(operationId, response) {
      return changePhoto(operationId, (operation, media) => {
        if (!operation.immutableIntent || response.id !== operation.resourceId || response.observation_id !== media.observationId
            || response.version !== 1 || response.mutation?.resource !== "photos" || response.mutation.action !== "create"
            || !uuid(response.mutation.eventId) || response.mutation.ids?.length !== 1 || response.mutation.ids[0] !== response.id
            || response.mutation.versions?.[response.id] !== 1) throw new Error("Reçu Photo incohérent : intention conservée.");
        // No localisation is added to the offline contract. Keep the real receipt identity.
        const photo = Object.fromEntries(["id", "observation_id", "version", "date", "libelle", "designation", "content_available", "content_url", "mutation"]
          .map(key => [key, structuredClone(response[key])]));
        media.confirmation = photo; media.transfer.state = "confirmed";
        operation.status = "confirmed"; operation.response = photo;
      });
    }
    function completePhoto(operationId) {
      return transaction("readwrite", (_r, _m, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const operation = request.result;
          if (!operation) return;
          const mediaStore = tx.objectStore("photoMedia"), read = mediaStore.get(operation.mediaLocalRef);
          read.onsuccess = guard(() => {
            const media = read.result;
            if (operation.status !== "confirmed" || !media?.confirmation
                || media.confirmation.mutation.eventId !== operation.response.mutation.eventId) throw new Error("Confirmation Photo non intégrée.");
            media.integrated = true; mediaStore.put(media); operations.delete(operationId); done(true);
          });
        });
      });
    }
    async function close() { (await open()).close(); opening = null; }
    async function read(resource, id) {
      return transaction("readonly", (rows, _meta, done, _tx, guard) => {
        const request = rows.get([resource, id]); request.onsuccess = guard(() => {
          if (request.result) validateRecord(request.result);
          done(request.result || null);
        });
      });
    }
    return { open, inspect, read, replace, markStale, clear, close,
      enqueue, enqueueCreate, editCreate, enqueuePhotoCreate, editPhotoCreate, photoEligibility,
      readPhotoMedia, confirmedPhotos, beginPhotoUpload, bindPhotoUpload, claimPhotoPost, confirmPhoto, completePhoto,
      discardObservationWithPhotos, claimOperation, listOperations, updateOperation, removeOperation, integratePinned };
  }
  return { createStore, RESOURCES, SCHEMA_VERSION, validCursor, validateRecord, canonicalCreate,
    canonicalPhotoCreate, localCreation, localPhoto, photoObservationContext };
});
