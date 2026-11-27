(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LocalProjection = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const BUSINESS_SRIDS = Object.freeze([2154, 3942, 3943, 3944, 3945, 3946, 3947, 3948, 3949, 3950]);
  const RESOURCES = Object.freeze(["systemes", "digues", "troncons", "desordres", "observations"]);
  // Version 5 is the current development schema. New databases are created
  // directly in that shape; prototype schemas are rejected, never upgraded or
  // silently replaced.
  const SCHEMA_VERSION = 5;
  const PHOTO_QUALITY_POLICY_KEY = "photoQualityPolicy";
  const STORE_SCHEMA = Object.freeze({
    resources: { keyPath: ["resource", "id"], indexes: {} },
    metadata: { keyPath: "key", indexes: {} },
    operations: { keyPath: "operationId", indexes: {
      resourceKey: { keyPath: "resourceKey", unique: true },
      sequence: { keyPath: "sequence", unique: true },
    } },
    photoMedia: { keyPath: "mediaLocalRef", indexes: {
      photoId: { keyPath: "photoId", unique: true },
    } },
  });
  function incompatibleSchemaError() {
    const error = new Error("Schéma IndexedDB incompatible avec cette version de digues-app ; réinitialisation explicite de la démonstration requise.");
    error.name = "VersionError";
    return error;
  }
  function sameKeyPath(actual, expected) {
    return JSON.stringify(actual) === JSON.stringify(expected);
  }
  function validateCurrentSchema(db) {
    const expectedStores = Object.keys(STORE_SCHEMA).sort();
    if (JSON.stringify([...db.objectStoreNames].sort()) !== JSON.stringify(expectedStores)) {
      throw incompatibleSchemaError();
    }
    let tx;
    try { tx = db.transaction(expectedStores, "readonly"); }
    catch (_) { throw incompatibleSchemaError(); }
    for (const [name, definition] of Object.entries(STORE_SCHEMA)) {
      const store = tx.objectStore(name);
      if (!sameKeyPath(store.keyPath, definition.keyPath)
          || JSON.stringify([...store.indexNames].sort()) !== JSON.stringify(Object.keys(definition.indexes).sort())) {
        throw incompatibleSchemaError();
      }
      for (const [indexName, indexDefinition] of Object.entries(definition.indexes)) {
        const index = store.index(indexName);
        if (!sameKeyPath(index.keyPath, indexDefinition.keyPath) || index.unique !== indexDefinition.unique) {
          throw incompatibleSchemaError();
        }
      }
    }
  }
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
  function canonicalPhotoLocalisation(input) {
    if (!input || !["point", "bornage", "pr", "effacer"].includes(input.mode)) throw new TypeError("Mode de localisation invalide.");
    const fields = { point: ["srid", "coordinates", "troncon_id", "systeme_reperage_id"],
      bornage: ["troncon_id", "systeme_reperage_id", "borne_id", "distance_m", "position_relative"],
      pr: ["troncon_id", "systeme_reperage_id", "pr"], effacer: [] };
    if (Object.keys(input).some(key => !["mode", "remplacer_historique", ...fields[input.mode]].includes(key))
        || (input.remplacer_historique !== undefined && typeof input.remplacer_historique !== "boolean")) {
      throw new TypeError("Champs de localisation incompatibles.");
    }
    const result = { mode: input.mode };
    if (input.remplacer_historique) result.remplacer_historique = true;
    const hasSystem = input.systeme_reperage_id != null, hasTrunk = input.troncon_id != null;
    if (hasSystem !== hasTrunk || (hasSystem && (!uuid(input.systeme_reperage_id) || !uuid(input.troncon_id)))
        || (input.mode !== "point" && input.mode !== "effacer" && !hasSystem)) throw new TypeError("Tronçon et système explicites requis.");
    if (input.mode === "point") {
      if (!(input.srid === 4326 || BUSINESS_SRIDS.includes(input.srid)) || !Array.isArray(input.coordinates) || input.coordinates.length !== 2
          || !input.coordinates.every(Number.isFinite) || input.srid !== 4326 && input.coordinates.some(v => Math.abs(v) > 100000000)
          || input.srid === 4326 && (Math.abs(input.coordinates[0]) > 180 || Math.abs(input.coordinates[1]) > 90)) {
        throw new TypeError("Coordonnées ou SRID invalides.");
      }
      result.srid = input.srid; result.coordinates = [...input.coordinates];
    }
    if (hasSystem) { result.troncon_id = input.troncon_id.toLowerCase(); result.systeme_reperage_id = input.systeme_reperage_id.toLowerCase(); }
    if (input.mode === "bornage") {
      if (!uuid(input.borne_id) || !["AVANT_BORNE", "SUR_BORNE", "APRES_BORNE"].includes(input.position_relative)
          || !Number.isFinite(input.distance_m) || input.distance_m < 0
          || input.position_relative === "SUR_BORNE" && input.distance_m !== 0) throw new TypeError("Bornage invalide.");
      result.borne_id = input.borne_id.toLowerCase(); result.distance_m = input.distance_m;
      result.position_relative = input.position_relative;
    }
    if (input.mode === "pr") {
      if (!Number.isFinite(input.pr)) throw new TypeError("PR invalide.");
      result.pr = input.pr;
    }
    return result;
  }
  function validateBusinessCRS(crs) {
    if (!crs || crs.authority !== "EPSG" || !BUSINESS_SRIDS.includes(crs.srid)
        || crs.code !== `EPSG:${crs.srid}`) throw new TypeError("CRS métier absent ou invalide.");
    return crs;
  }
  function validateLocalisationCRS(changes, crs) {
    validateBusinessCRS(crs);
    if (changes.mode === "point" && changes.srid !== 4326 && changes.srid !== crs.srid) {
      throw new TypeError("SRID incompatible avec le périmètre préparé.");
    }
  }
  function validateReperageReferences(value) {
    validateBusinessCRS(value?.crs);
    if (!value || value.schemaVersion !== 2 || !Array.isArray(value.systems) || value.systems.length > 200
        || value.systems.some(system => !uuid(system.id) || !uuid(system.troncon_id)
          || !/^[0-9a-f]{64}$/.test(system.revision) || typeof system.valid !== "boolean"
          || !Array.isArray(system.bornes) || system.bornes.some(borne => !uuid(borne.id)
            || !uuid(borne.link_id) || typeof borne.pr !== "string" || !Number.isFinite(Number(borne.pr))
            || typeof borne.valid !== "boolean" || typeof borne.link_valid !== "boolean"))
        || value.systems.reduce((total, system) => total + system.bornes.length, 0) > 2000
        || new TextEncoder().encode(JSON.stringify(value)).byteLength > 8 * 1024 * 1024) {
      throw new TypeError("Références de repérage invalides.");
    }
  }
  function validateObservationReferences(value) {
    if (!value || !Array.isArray(value.suitesApporter) || !Array.isArray(value.urgences)
        || [value.suitesApporter, value.urgences].some(rows => rows.length > 500 || rows.some(row =>
          typeof row.id !== "string" || !row.id || typeof row.libelle !== "string"))
        || new TextEncoder().encode(JSON.stringify(value)).byteLength > 256 * 1024) {
      throw new TypeError("Référentiels Observation invalides.");
    }
  }
  function canonicalPhotoQualityPolicy(value) {
    const parameters = value?.effective_parameters;
    if (value?.contract_version !== 2 || ![1, 2].includes(value?.parameters_version)
        || !Number.isSafeInteger(value.policy_version) || value.policy_version < 1
        || !["economique", "standard", "haute_qualite"].includes(value.profile)
        || !parameters || !Number.isSafeInteger(parameters.max_long_edge_px)
        || parameters.max_long_edge_px < 320 || parameters.max_long_edge_px > 10000
        || parameters.output_mime_type !== "image/jpeg"
        || typeof parameters.encoder_quality !== "number"
        || parameters.encoder_quality < 0.1 || parameters.encoder_quality > 1) {
      throw new TypeError("Politique de qualité photo locale invalide.");
    }
    if (value.parameters_version === 2
        && (!Number.isSafeInteger(parameters.max_file_size_bytes)
          || parameters.max_file_size_bytes <= 0 || parameters.max_file_size_bytes > 20 * 1024 * 1024
          || !Number.isSafeInteger(parameters.min_long_edge_px) || parameters.min_long_edge_px < 320
          || parameters.min_long_edge_px > parameters.max_long_edge_px
          || typeof parameters.min_encoder_quality !== "number" || parameters.min_encoder_quality < 0.1
          || parameters.min_encoder_quality > parameters.encoder_quality
          || typeof parameters.encoder_quality_step !== "number" || parameters.encoder_quality_step < 0.01
          || parameters.encoder_quality_step > 0.25
          || typeof parameters.dimension_scale_step !== "number" || parameters.dimension_scale_step < 0.5
          || parameters.dimension_scale_step >= 1)) {
      throw new TypeError("Politique de qualité photo locale invalide.");
    }
    const effective = {
      max_long_edge_px: parameters.max_long_edge_px,
      output_mime_type: parameters.output_mime_type,
      encoder_quality: parameters.encoder_quality,
    };
    if (value.parameters_version === 2) Object.assign(effective, {
      min_long_edge_px: parameters.min_long_edge_px,
      min_encoder_quality: parameters.min_encoder_quality,
      encoder_quality_step: parameters.encoder_quality_step,
      dimension_scale_step: parameters.dimension_scale_step,
      max_file_size_bytes: parameters.max_file_size_bytes,
    });
    const policy = {
      contract_version: value.contract_version,
      profile: value.profile,
      policy_version: value.policy_version,
      parameters_version: value.parameters_version,
      effective_parameters: effective,
    };
    if (value.parameters_version === 2) {
      if (value.max_file_size_bytes !== undefined
          && value.max_file_size_bytes !== parameters.max_file_size_bytes) {
        throw new TypeError("Politique de qualité photo locale invalide.");
      }
      policy.max_file_size_bytes = parameters.max_file_size_bytes;
    }
    return policy;
  }
  function photoQualityPolicyMetadata(policy, fetchedAt = Date.now()) {
    policy = canonicalPhotoQualityPolicy(policy);
    if (!Number.isSafeInteger(fetchedAt) || fetchedAt < 0) throw new TypeError("Date de cache photo invalide.");
    return { key: PHOTO_QUALITY_POLICY_KEY, contractVersion: policy.contract_version,
      profile: policy.profile, policyVersion: policy.policy_version,
      parametersVersion: policy.parameters_version,
      effectiveParameters: structuredClone(policy.effective_parameters), fetchedAt };
  }
  function photoQualityPolicyFromMetadata(record) {
    if (!record || record.key !== PHOTO_QUALITY_POLICY_KEY
        || !Number.isSafeInteger(record.fetchedAt) || record.fetchedAt < 0) {
      throw new TypeError("Cache de politique photo invalide.");
    }
    return { policy: canonicalPhotoQualityPolicy({ contract_version: record.contractVersion,
      profile: record.profile, policy_version: record.policyVersion,
      parameters_version: record.parametersVersion,
      effective_parameters: record.effectiveParameters }), fetchedAt: record.fetchedAt };
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
  function createStore({ indexedDB: suppliedIndexedDB, name = globalThis.BusinessRuntime?.databaseName || "digues-app-field-projection" } = {}) {
    const runtime = globalThis.BusinessRuntime;
    if (runtime && name !== runtime.databaseName) throw new Error('Stockage étranger au contexte navigateur.');
    const contextKey = runtime?.context?.key || null;
    function bindOperation(operation) {
      if (contextKey) operation.contextKey = contextKey;
      return operation;
    }
    function requireWrite(permission) {
      runtime?.assertActive();
      if (runtime && (!runtime.can('offline.write') || !runtime.can(permission))) throw new Error('Écriture hors ligne non autorisée.');
    }
    let opening;
    function open() {
      runtime?.assertActive();
      if (opening) return opening;
      opening = new Promise((resolve, reject) => {
        // Some privacy/security policies throw on property access itself.
        // Resolve that access inside the promise so the caller can stay online-only.
        const indexedDB = suppliedIndexedDB === undefined ? globalThis.indexedDB : suppliedIndexedDB;
        if (!indexedDB) { reject(new Error("IndexedDB indisponible")); return; }
        const request = indexedDB.open(name, SCHEMA_VERSION);
        let blocked = false, incompatibleUpgrade = false;
        request.onupgradeneeded = event => {
          if (event.oldVersion !== 0) {
            incompatibleUpgrade = true;
            request.transaction.abort();
            return;
          }
          request.result.createObjectStore("resources", { keyPath: ["resource", "id"] });
          request.result.createObjectStore("metadata", { keyPath: "key" });
          const operations = request.result.createObjectStore("operations", { keyPath: "operationId" });
          operations.createIndex("resourceKey", "resourceKey", { unique: true });
          operations.createIndex("sequence", "sequence", { unique: true });
          const media = request.result.createObjectStore("photoMedia", { keyPath: "mediaLocalRef" });
          media.createIndex("photoId", "photoId", { unique: true });
        };
        request.onerror = () => {
          opening = null;
          reject(incompatibleUpgrade || request.error?.name === "VersionError"
            ? incompatibleSchemaError() : request.error);
        };
        request.onblocked = () => {
          blocked = true;
          opening = null;
          reject(new Error("Ouverture IndexedDB bloquée : fermer les autres pages de la démonstration, puis réessayer."));
        };
        request.onsuccess = () => {
          const db = request.result;
          if (blocked) { db.close(); return; }
          try { validateCurrentSchema(db); }
          catch (error) { db.close(); opening = null; reject(error); return; }
          db.onversionchange = () => { db.close(); opening = null; };
          resolve(db);
        };
      });
      return opening;
    }
    async function transaction(mode, run) {
      const db = await open();
      runtime?.assertActive();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(["resources", "metadata", "operations", "photoMedia"], mode, { durability: "strict" });
        let result;
        tx.oncomplete = () => {
          try { runtime?.assertActive(); resolve(result); }
          catch (error) { reject(error); }
        };
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
        const records = rows.getAll(), references = meta.get("reperageReferences"),
          observationReferences = meta.get("observationReferences"), scope = meta.get("scope");
        scope.onsuccess = guard(() => {
          records.result.forEach(validateRecord);
          const value = scope.result;
          if (value) validateBusinessCRS(value.crs);
          if ((!value && records.result.length) || (value && (!validCursor(value.cursor)
              || typeof value.revision !== "string" || !Array.isArray(value.tronconIds)))) {
            throw new Error("Métadonnées de projection corrompues ; reconstruction explicite nécessaire");
          }
          if (references.result) validateReperageReferences(references.result);
          if (observationReferences.result) validateObservationReferences(observationReferences.result);
          done({ records: records.result, scope: value || null, reperageReferences: references.result || null,
            observationReferences: observationReferences.result || null });
        });
      });
    }
    function replace(snapshot, expectedRevision = null) {
      snapshot = structuredClone(snapshot);
      validateBusinessCRS(snapshot?.crs);
      if (snapshot?.schemaVersion !== 3 || !validCursor(snapshot.cursor)
          || !Array.isArray(snapshot.records) || snapshot.records.length > 2000 || !Array.isArray(snapshot.tronconIds)
          || !snapshot.tronconIds.length || snapshot.tronconIds.length > 20
          || snapshot.tronconIds.some(id => typeof id !== "string" || !id)) {
        throw new TypeError("Instantané de périmètre invalide");
      }
      snapshot.records.forEach(validateRecord);
      validateReperageReferences(snapshot.reperageReferences);
      if (snapshot.crs.srid !== snapshot.reperageReferences.crs.srid) {
        throw new TypeError("CRS du périmètre et du repérage incohérents.");
      }
      validateObservationReferences(snapshot.observationReferences);
      if (new Set(snapshot.records.map(r => JSON.stringify([r.resource, r.id]))).size !== snapshot.records.length) {
        throw new TypeError("Identifiant de projection dupliqué");
      }
      return transaction("readwrite", (rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations").getAll();
        const previousRows = rows.getAll(), oldReferences = meta.get("reperageReferences"), request = meta.get("scope");
        request.onsuccess = guard(() => {
          const previous = request.result;
          if (previous && previous.crs?.srid !== snapshot.crs.srid) {
            throw new TypeError("Changement de CRS du périmètre refusé.");
          }
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
                  if (values.parent) retain(values.parent.canonical_resource || values.parent.resource, values.parent.id);
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
              if (o.resource === "photos" && o.action === "update-localisation") {
                retain("observations", o.observationId);
                retain("troncons", o.changes.troncon_id);
              }
            });
            previousRows.result.filter(r => pinned.has(JSON.stringify([r.resource, r.id])) && !keys.has(JSON.stringify([r.resource, r.id])))
              .forEach(r => rows.put({ ...r, syncState: "stale", detached: true }));
          }
          snapshot.records.forEach(row => rows.put({ ...row, syncState: "synced", cachedAt: Date.now() }));
          const references = { ...snapshot.reperageReferences,
            systems: [...snapshot.reperageReferences.systems], pinned: [] };
          const old = oldReferences.result;
          for (const operation of operations.result) {
            if (operation.resource !== "photos" || operation.action !== "update-localisation" || !operation.referenceRevision) continue;
            const previous = [...(old?.systems || []), ...(old?.pinned || [])]
              .find(system => system.id === operation.changes.systeme_reperage_id && system.revision === operation.referenceRevision);
            if (previous && !references.systems.some(system => system.id === previous.id && system.revision === previous.revision)
                && !references.pinned.some(system => system.id === previous.id && system.revision === previous.revision)) {
              references.pinned.push(previous);
            }
          }
          if (references.pinned.length > 200
              || new TextEncoder().encode(JSON.stringify(references)).byteLength > 8 * 1024 * 1024) {
            throw pendingScopeError();
          }
          meta.put({ key: "reperageReferences", ...references });
          meta.put({ key: "observationReferences", ...snapshot.observationReferences });
          meta.put({ key: "scope", revision: globalThis.crypto.randomUUID(), cursor: snapshot.cursor,
            crs: snapshot.crs, tronconIds: snapshot.tronconIds, cachedAt: Date.now() });
          done(true);
        });
      });
    }
    function markStale() {
      return transaction("readwrite", (rows, _meta, _done, _tx, guard) => {
        const request = rows.getAll();
        request.onsuccess = guard(() => {
          // Queue the writes in the same transaction. Advancing one cursor per
          // row makes a full 2,000-record scope wait on 2,000 round trips to IDB.
          for (const row of request.result) {
            if (row.syncState !== "stale") rows.put({ ...row, syncState: "stale" });
          }
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
          rows.clear();
          for (const key of ["scope", "reperageReferences", "observationReferences", "operation-sequence"]) meta.delete(key);
        });
      });
    }
    function readPhotoQualityPolicy() {
      return transaction("readonly", (_rows, meta, done, _tx, guard) => {
        const request = meta.get(PHOTO_QUALITY_POLICY_KEY);
        request.onsuccess = guard(() => done(request.result ? photoQualityPolicyFromMetadata(request.result) : null));
      });
    }
    function writePhotoQualityPolicy(policy, fetchedAt = Date.now()) {
      policy = canonicalPhotoQualityPolicy(policy);
      const record = photoQualityPolicyMetadata(policy, fetchedAt);
      return transaction("readwrite", (_rows, meta, done, _tx, guard) => {
        const request = meta.get(PHOTO_QUALITY_POLICY_KEY);
        request.onsuccess = guard(() => {
          let current = null;
          try { if (request.result) current = photoQualityPolicyFromMetadata(request.result); }
          catch (_) { /* A valid server contract may replace a corrupt cache record. */ }
          // Concurrent tabs must not let a delayed older response downgrade the
          // monotone policy version already accepted on this browser context.
          if (current?.policy.policy_version > policy.policy_version) { done(current); return; }
          meta.put(record); done({ policy, fetchedAt });
        });
      });
    }
    function listOperations() {
      return transaction("readonly", (_rows, _meta, done, tx, guard) => {
        const request = tx.objectStore("operations").index("sequence").getAll();
        request.onsuccess = guard(() => {
          if (contextKey && request.result.some(operation => operation.contextKey !== contextKey)) throw new Error('Intention étrangère au contexte : conservée, jamais rejouée.');
          done(request.result);
        });
      });
    }
    function readReperageReferences() {
      return transaction("readonly", (_rows, meta, done, _tx, guard) => {
        const request = meta.get("reperageReferences");
        request.onsuccess = guard(() => done(request.result || null));
      });
    }
    function canonicalUpdate(resource, changes) {
      const fields = { desordres: ["designation", "commentaire", "lieu_dit"], observations:
        ["designation", "date", "evolution", "suite_apporter_id", "suite", "urgence_id", "nombre_desordres"] };
      if (!fields[resource] || !changes || !Object.keys(changes).length || Object.entries(changes).some(([key, value]) =>
            !fields[resource].includes(key) || (value !== null && !(resource === "observations" && key === "nombre_desordres"
              ? Number.isSafeInteger(value) && value >= 0 && value <= 2147483647 : typeof value === "string")))) {
        throw new TypeError("Écriture hors ligne non prise en charge.");
      }
      if (resource === "observations" && changes.date !== undefined && changes.date !== null
          && (!/^\d{4}-\d{2}-\d{2}$/.test(changes.date) || !Number.isFinite(Date.parse(changes.date))
            || new Date(changes.date).toISOString().slice(0, 10) !== changes.date)) {
        throw new TypeError("Date Observation invalide.");
      }
      changes = structuredClone(changes);
      if (resource === "observations") for (const key of ["designation", "evolution", "suite_apporter_id", "suite", "urgence_id"]) {
        if (typeof changes[key] === "string") changes[key] = changes[key].trim() || null;
      }
      return changes;
    }
    function checkObservationParent(resource, payload, changes) {
      if (resource !== "observations") return;
      const parent = payload.parent?.resource;
      if (!["desordres", "troncons", "ouvrages_hydrauliques", "equipements_mesure", "cheminements",
        "mobilier", "reseaux_techniques", "amenagements_hydrauliques", "vegetation"].includes(parent)) {
        throw new Error("Parent Observation préparé invalide.");
      }
      if (parent !== "desordres" && (Object.hasOwn(changes, "urgence_id") || Object.hasOwn(changes, "nombre_desordres"))) {
        throw new TypeError("Urgence et nombre de désordres sont réservés aux Observations de Désordre.");
      }
    }
    function enqueue({ resource, resourceId, baseVersion, changes }) {
      requireWrite('business.update');
      if (typeof resourceId !== "string" || !Number.isSafeInteger(baseVersion) || baseVersion < 1) {
        throw new TypeError("Identité ou version invalide.");
      }
      changes = canonicalUpdate(resource, changes);
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
          checkObservationParent(resource, row.result.payload, changes);
          const sequence = (count.result?.value || 0) + 1;
          const operation = { operationId: crypto.randomUUID(), resource, resourceId, action: "update", baseVersion,
            changes, resourceKey, sequence, createdAt: Date.now(), status: "pending", attempts: 0,
            basePayload: row.result.payload };
          operations.add(bindOperation(operation));
          meta.put({ key: "operation-sequence", value: sequence });
          done(operation);
        });
      });
    }
    function editUpdate(operationId, changes) {
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const old = request.result;
          if (!old || old.action !== "update" || old.attempts !== 0 || old.status !== "pending") {
            throw new Error("Mise à jour déjà tentée : intention figée jusqu’à réconciliation.");
          }
          const nextChanges = canonicalUpdate(old.resource, changes);
          checkObservationParent(old.resource, old.basePayload, nextChanges);
          if (JSON.stringify(nextChanges) === JSON.stringify(old.changes)) { done(old); return; }
          const next = { ...old, changes: nextChanges, operationId: crypto.randomUUID() };
          operations.delete(operationId); operations.add(next); done(next);
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
      requireWrite('business.create');
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
          operations.add(bindOperation(operation)); meta.put({ key: "operation-sequence", value: sequence }); done(operation);
        });
      });
    }
    function enqueuePhotoCreate({ operationId, changes, observation, media = null }) {
      requireWrite('business.create');
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
          operations.add(bindOperation(operation)); meta.put({ key: "operation-sequence", value: sequence }); done(operation);
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
                && available.photoId === operation.resourceId && available.validationVersion === 1
                && Number.isSafeInteger(available.policyVersion) && available.policyVersion === operation.photoPolicyVersion;
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
    function enqueuePhotoLocalisation({ operationId, photoId, changes, referenceRevision = null, baseVersion = null }) {
      requireWrite('business.update');
      changes = canonicalPhotoLocalisation(changes);
      if (baseVersion !== null && (!Number.isSafeInteger(baseVersion) || baseVersion < 1)) {
        throw new TypeError("Version de départ Photo invalide.");
      }
      if (!uuid(operationId) || !uuid(photoId) || referenceRevision !== null
          && !/^[0-9a-f]{64}$/.test(referenceRevision)) throw new TypeError("Identité de localisation invalide.");
      if (Boolean(changes.systeme_reperage_id) !== Boolean(referenceRevision)) {
        throw new TypeError("Empreinte du système requise uniquement pour un repérage dépendant de références.");
      }
      return transaction("readwrite", (_rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), mediaStore = tx.objectStore("photoMedia");
        const resourceKey = JSON.stringify(["photos", "localisation", photoId]);
        const existing = operations.index("resourceKey").get(resourceKey);
        const create = operations.index("resourceKey").get(JSON.stringify(["photos", photoId]));
        const media = mediaStore.index("photoId").get(photoId);
        const refs = meta.get("reperageReferences"), count = meta.get("operation-sequence");
        count.onsuccess = guard(() => {
          validateLocalisationCRS(changes, refs.result?.crs);
          if (existing.result) throw new Error("Une localisation non résolue existe déjà pour cette Photo.");
          const parent = create.result, saved = media.result;
          if (!parent && !saved?.integrated) throw new Error("Seules les Photos créées localement peuvent être repérées hors ligne.");
          if (parent && parent.action !== "create") throw new Error("Dépendance Photo invalide.");
          if (changes.systeme_reperage_id && !refs.result?.systems?.some(system =>
            system.id === changes.systeme_reperage_id && system.troncon_id === changes.troncon_id
            && system.valid && system.revision === referenceRevision && system.bornes.length
            && system.bornes.every(borne => borne.valid && borne.link_valid)
            && (changes.mode !== "bornage" || system.bornes.some(borne => borne.id === changes.borne_id)))) {
            throw new Error("Système ou bornes absents du périmètre préparé, ou références modifiées.");
          }
          const sequence = (count.result?.value || 0) + 1;
          const operation = { operationId: operationId.toLowerCase(), resource: "photos", resourceId: photoId.toLowerCase(),
            action: "update-localisation", resourceKey, sequence, changes, referenceRevision,
            observationId: parent?.changes.observation_id || saved.observationId,
            dependsOn: [{ resource: "photos", id: photoId.toLowerCase(), action: "create" }],
            // null means a draft opened before create confirmation. The create
            // contract guarantees version 1; never substitute a later receipt.
            baseVersion: baseVersion ?? (saved?.integrated ? 1 : null),
            createdAt: Date.now(), status: "pending", attempts: 0 };
          operations.add(bindOperation(operation)); meta.put({ key: "operation-sequence", value: sequence }); done(operation);
        });
      });
    }
    function editPhotoLocalisation(operationId, changes, referenceRevision = null) {
      changes = canonicalPhotoLocalisation(changes);
      if (Boolean(changes.systeme_reperage_id) !== Boolean(referenceRevision)) throw new TypeError("Empreinte de repérage incohérente.");
      return transaction("readwrite", (_rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId), refs = meta.get("reperageReferences");
        refs.onsuccess = guard(() => {
          validateLocalisationCRS(changes, refs.result?.crs);
          const old = request.result;
          if (!old || old.resource !== "photos" || old.action !== "update-localisation"
              || old.attempts !== 0 || old.status !== "pending") throw new Error("Localisation déjà tentée : intention figée.");
          if (changes.systeme_reperage_id && !refs.result?.systems?.some(system =>
            system.id === changes.systeme_reperage_id && system.troncon_id === changes.troncon_id
            && system.revision === referenceRevision && system.valid)) throw new Error("Références préparées modifiées.");
          if (JSON.stringify(old.changes) === JSON.stringify(changes) && old.referenceRevision === referenceRevision) { done(old); return; }
          const next = { ...old, changes, referenceRevision, operationId: crypto.randomUUID() };
          operations.delete(operationId); operations.add(next); done(next);
        });
      });
    }
    function photoLocalisationEligibility(operationId) {
      return transaction("readonly", (_rows, meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const intent = request.result;
          if (!intent || intent.resource !== "photos" || intent.action !== "update-localisation") throw new Error("Intention localisation absente.");
          const create = operations.index("resourceKey").get(JSON.stringify(["photos", intent.resourceId]));
          const media = tx.objectStore("photoMedia").index("photoId").get(intent.resourceId);
          const refs = meta.get("reperageReferences");
          refs.onsuccess = guard(() => {
            let reason = create.result ? `photo-${create.result.status}` : !media.result?.integrated ? "photo-unverified" : "ready";
            const version = intent.baseVersion ?? (media.result?.integrated ? 1 : null);
            if (reason === "ready" && !Number.isSafeInteger(version)) reason = "photo-version-unknown";
            // A previous PUT may have committed. Replay its exact identity before
            // consulting newer local references; the server checks receipts first.
            if (reason === "ready" && intent.referenceRevision && intent.attempts === 0 && intent.status !== "syncing") {
              const system = refs.result?.systems?.find(row => row.id === intent.changes.systeme_reperage_id);
              const pinned = refs.result?.pinned?.find(row => row.id === intent.changes.systeme_reperage_id
                && row.revision === intent.referenceRevision);
              if (system && system.revision !== intent.referenceRevision) reason = "references-stale";
              else if (!system && !pinned) reason = "references-missing";
            }
            done({ eligible: reason === "ready", reason, baseVersion: version });
          });
        });
      });
    }
    function claimPhotoLocalisation(operationId) {
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const old = request.result;
          if (!old || old.action !== "update-localisation" || !["pending", "syncing"].includes(old.status)) { done(null); return; }
          const media = tx.objectStore("photoMedia").index("photoId").get(old.resourceId);
          media.onsuccess = guard(() => {
            if (!media.result?.integrated || !media.result.confirmation || media.result.observationId !== old.observationId) {
              done(null); return;
            }
            const baseVersion = old.baseVersion ?? 1; // deferred initial create version, never a rebase
            if (!Number.isSafeInteger(baseVersion) || baseVersion < 1) throw new Error("Version Photo initiale inconnue.");
            const next = { ...old, baseVersion, status: "syncing", attempts: old.attempts + 1 };
            operations.put(next); done(next);
          });
        });
      });
    }
    function confirmPhotoLocalisation(operationId, response) {
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const old = request.result;
          if (!old || old.action !== "update-localisation" || response?.id !== old.resourceId
              || response.observation_id !== old.observationId || !Number.isSafeInteger(response.version)
              || response.version <= old.baseVersion || response.mutation?.resource !== "photos"
              || response.mutation.action !== "update" || response.mutation.ids?.length !== 1
              || response.mutation.ids[0] !== old.resourceId
              || response.mutation.versions?.[old.resourceId] !== response.version) throw new Error("Reçu localisation incohérent.");
          const mediaStore = tx.objectStore("photoMedia"), media = mediaStore.index("photoId").get(old.resourceId);
          media.onsuccess = guard(() => {
            if (!media.result?.integrated) throw new Error("Photo locale non intégrée.");
            mediaStore.put({ ...media.result, confirmation: { ...media.result.confirmation,
              version: response.version, localisation: structuredClone(response.localisation), mutation: structuredClone(response.mutation) } });
            const next = { ...old, status: "confirmed", response: { mutation: structuredClone(response.mutation), version: response.version } };
            operations.put(next); done(next);
          });
        });
      });
    }
    function completePhotoLocalisation(operationId) {
      return transaction("readwrite", (_rows, _meta, done, tx, guard) => {
        const operations = tx.objectStore("operations"), request = operations.get(operationId);
        request.onsuccess = guard(() => {
          const old = request.result;
          if (!old || old.action !== "update-localisation" || old.status !== "confirmed") throw new Error("Localisation non confirmée.");
          const media = tx.objectStore("photoMedia").index("photoId").get(old.resourceId);
          media.onsuccess = guard(() => {
            if (media.result?.confirmation?.mutation?.eventId !== old.response?.mutation?.eventId) {
              throw new Error("Reçu localisation non intégré.");
            }
            operations.delete(operationId); done(true);
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
          if (all.result.some(o => o.resource === "photos" && o.action === "update-localisation"
            && children.some(photo => photo.resourceId === o.resourceId))) throw new Error("Abandonnez d’abord les localisations dépendantes.");
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
          if (intent.resource === "photos" && intent.action === "update-localisation") {
            const definitiveRefusal = ["conflict", "failed"].includes(intent.status);
            if (!discard || !definitiveRefusal && (intent.attempts > 0 || intent.status !== "pending")) {
              throw new Error("Localisation tentée : réconciliation nécessaire.");
            }
            operations.delete(operationId); return;
          }
          if (intent.resource === "photos" && intent.action === "create") {
            if (!discard || intent.immutableIntent || intent.attempts > 0 || intent.status === "confirmed") throw new Error("Photo tentée : réconciliation nécessaire avant suppression.");
            const dependent = operations.index("resourceKey").get(JSON.stringify(["photos", "localisation", intent.resourceId]));
            dependent.onsuccess = guard(() => {
              if (dependent.result) throw new Error("Abandonnez d’abord la localisation de cette Photo.");
              if (intent.mediaLocalRef) tx.objectStore("photoMedia").delete(intent.mediaLocalRef);
              operations.delete(operationId);
            });
            return;
          }
          const unsent = intent.attempts === 0 && intent.status === "pending";
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
          || !/^[0-9a-f]{64}$/.test(media.sha256) || media.mimeType !== "image/jpeg"
          || media.validationVersion !== 1 || !Number.isSafeInteger(media.policyVersion) || media.policyVersion < 1
          || media.policyContractVersion !== 2 || ![1, 2].includes(media.policyParametersVersion)
          || !["economique", "standard", "haute_qualite"].includes(media.profile)) throw new TypeError("Média local non vérifié.");
    }
    function installMedia(tx, operation, input) {
      const media = { mediaLocalRef: crypto.randomUUID(), photoId: operation.resourceId, observationId: operation.changes.observation_id,
        originalFilename: input.originalFilename, mimeType: input.mimeType, size: input.size, sha256: input.sha256,
        width: input.width, height: input.height, blob: input.blob, validationVersion: 1,
        policyVersion: input.policyVersion, policyContractVersion: input.policyContractVersion,
        policyParametersVersion: input.policyParametersVersion, profile: input.profile, createdAt: Date.now(),
        transfer: { state: "local", attempts: 0 }, transportBinding: null, confirmation: null };
      operation.mediaLocalRef = media.mediaLocalRef; operation.photoPolicyVersion = media.policyVersion;
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
        // The create receipt stays separate from the dependent localisation command.
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
    async function close() { if (opening) (await opening).close(); opening = null; }
    async function read(resource, id) {
      return transaction("readonly", (rows, _meta, done, _tx, guard) => {
        const request = rows.get([resource, id]); request.onsuccess = guard(() => {
          if (request.result) validateRecord(request.result);
          done(request.result || null);
        });
      });
    }
    return { open, inspect, read, replace, markStale, clear, close,
      readPhotoQualityPolicy, writePhotoQualityPolicy,
      enqueue, editUpdate, enqueueCreate, editCreate, enqueuePhotoCreate, editPhotoCreate, photoEligibility,
      enqueuePhotoLocalisation, editPhotoLocalisation, photoLocalisationEligibility,
      claimPhotoLocalisation, confirmPhotoLocalisation, completePhotoLocalisation, readReperageReferences,
      readPhotoMedia, confirmedPhotos, beginPhotoUpload, bindPhotoUpload, claimPhotoPost, confirmPhoto, completePhoto,
      discardObservationWithPhotos, claimOperation, listOperations, updateOperation, removeOperation, integratePinned };
  }
  return { createStore, RESOURCES, SCHEMA_VERSION, validCursor, validateRecord, canonicalCreate,
    canonicalPhotoQualityPolicy, photoQualityPolicyMetadata, photoQualityPolicyFromMetadata,
    canonicalPhotoCreate, canonicalPhotoLocalisation, validateBusinessCRS, localCreation, localPhoto, photoObservationContext };
});
