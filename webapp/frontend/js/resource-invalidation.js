(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }
  root.ResourceInvalidation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const SCHEMA_VERSION = 1;
  const ACTIONS = Object.freeze(["create", "update", "delete"]);
  const ASPECTS = Object.freeze([
    "attributes", "geometry", "relations", "attachments",
  ]);

  const RESOURCE_PROFILES = Object.freeze({
    systemes: Object.freeze(["heritageStructure"]),
    digues: Object.freeze(["heritageStructure"]),
    troncons: Object.freeze(["heritageStructure", "mappedEntity"]),
    desordres: Object.freeze(["mappedEntity"]),
    "troncons-lit": Object.freeze(["mappedEntity"]),
    "elements-morphologiques-lit": Object.freeze(["mappedEntity"]),
    mobilier: Object.freeze(["mappedEntity"]),
    "equipements-mesure": Object.freeze(["mappedEntity"]),
    "reseaux-techniques": Object.freeze(["mappedEntity"]),
    "ouvrages-particuliers": Object.freeze(["mappedEntity"]),
    "cheminements": Object.freeze(["mappedEntity"]),
    "vegetation": Object.freeze(["mappedEntity"]),
    "amenagements-hydrauliques": Object.freeze(["heritageStructure", "mappedEntity"]),
    "zones-stockage": Object.freeze(["heritageStructure", "mappedEntity"]),
    "prestations": Object.freeze(["mappedEntity"]),
    observations: Object.freeze(["childRecord"]),
    photos: Object.freeze(["attachment"]),
    "territoires-administratifs": Object.freeze(["mapContext"]),
  });

  const PROFILE_INVALIDATIONS = Object.freeze({
    heritageStructure: Object.freeze([
      "heritage-tree", "relation-options", "data-query-results", "open-detail",
    ]),
    mappedEntity: Object.freeze([
      "map-feature-store", "data-query-results", "open-detail",
    ]),
    childRecord: Object.freeze([
      "data-query-results", "open-detail", "map-feature-store",
    ]),
    attachment: Object.freeze(["data-query-results", "open-detail"]),
    mapContext: Object.freeze(["map-context"]),
  });

  const actionSet = new Set(ACTIONS);
  const aspectSet = new Set(ASPECTS);
  const invalidationUnitSet = new Set(Object.values(PROFILE_INVALIDATIONS).flat());
  const INVALIDATION_UNITS = Object.freeze([...invalidationUnitSet]);
  const CANONICAL_RESOURCE_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isNonEmptyTrimmedString(value, maximumLength = 200) {
    return typeof value === "string"
      && value.length > 0
      && value.length <= maximumLength
      && value.trim() === value;
  }

  function isCanonicalResource(value) {
    return isNonEmptyTrimmedString(value) && CANONICAL_RESOURCE_PATTERN.test(value);
  }

  function normalizeStringList(
    value,
    { allowedValues = null, allowEmpty = false } = {},
  ) {
    if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) return null;
    const normalized = [];
    const seen = new Set();
    for (const item of value) {
      if (!isNonEmptyTrimmedString(item)
          || (allowedValues && !allowedValues.has(item))) return null;
      if (!seen.has(item)) {
        seen.add(item);
        normalized.push(item);
      }
    }
    return normalized;
  }

  function normalizeMutationPayload(payload) {
    if (!isRecord(payload)
        || payload.schemaVersion !== SCHEMA_VERSION
        || !isCanonicalResource(payload.resource)
        || !actionSet.has(payload.action)
        || !isNonEmptyTrimmedString(payload.eventId)
        || !isNonEmptyTrimmedString(payload.originClientId)
        || !isNonEmptyTrimmedString(payload.originInstanceId)
        || !Number.isFinite(payload.occurredAt)
        || payload.occurredAt < 0) return null;

    const ids = payload.ids === null ? null : normalizeStringList(payload.ids);
    if (payload.ids !== null && ids === null) return null;

    let aspects;
    if (Object.prototype.hasOwnProperty.call(payload, "aspects")) {
      aspects = normalizeStringList(payload.aspects, {
        allowedValues: aspectSet,
        allowEmpty: true,
      });
      if (aspects === null) return null;
    }

    const normalized = {
      schemaVersion: SCHEMA_VERSION,
      eventId: payload.eventId,
      originClientId: payload.originClientId,
      resource: payload.resource,
      action: payload.action,
      ids,
      originInstanceId: payload.originInstanceId,
      occurredAt: payload.occurredAt,
    };
    if (aspects !== undefined) normalized.aspects = aspects;
    if (payload.versions !== undefined) {
      if (!isRecord(payload.versions) || ids === null
          || Object.keys(payload.versions).some((id) => !ids.includes(id)
            || !Number.isSafeInteger(payload.versions[id]) || payload.versions[id] < 1)) return null;
      normalized.versions = { ...payload.versions };
    }
    return normalized;
  }

  function isValidMutationPayload(payload) {
    return normalizeMutationPayload(payload) !== null;
  }

  function resolveProfiles(resource) {
    if (!isCanonicalResource(resource)) return [];
    return [...(RESOURCE_PROFILES[resource] || [])];
  }

  function resolveInvalidationUnits(resource) {
    const profiles = resolveProfiles(resource);
    const units = [];
    const seen = new Set();
    profiles.forEach((profile) => {
      (PROFILE_INVALIDATIONS[profile] || []).forEach((unit) => {
        if (!seen.has(unit)) {
          seen.add(unit);
          units.push(unit);
        }
      });
    });
    return units;
  }

  function resolveInvalidation(resource) {
    const profiles = resolveProfiles(resource);
    return {
      knownResource: profiles.length > 0,
      profiles,
      units: resolveInvalidationUnits(resource),
    };
  }

  function createInvalidatorRegistry({ onHandlerError = () => {} } = {}) {
    const handlersByUnit = new Map();
    // At most one recovery slot per consumer/resource, never one per event.
    // Recovery rereads the collection, so arbitrarily many IDs remain bounded.
    const workByHandler = new Map();

    function registerInvalidator(unit, handler) {
      if (!invalidationUnitSet.has(unit)) {
        throw new TypeError("Unité d’invalidation inconnue");
      }
      if (typeof handler !== "function") {
        throw new TypeError("Invalidateur invalide");
      }
      const handlers = handlersByUnit.get(unit) || new Set();
      handlers.add(handler);
      handlersByUnit.set(unit, handlers);
      return function unregisterInvalidator() {
        handlers.delete(handler);
        workByHandler.delete(handler);
        if (handlers.size === 0) handlersByUnit.delete(unit);
      };
    }

    function reportHandlerError(error, context) {
      try {
        onHandlerError(error, context);
      } catch (_error) {
        // Une instrumentation défaillante ne doit pas interrompre les autres unités.
      }
    }

    function invoke(handler, mutation, unit) {
      let resources = workByHandler.get(handler);
      if (!resources) { resources = new Map(); workByHandler.set(handler, resources); }
      let state = resources.get(mutation.resource);
      if (!state) {
        state = { generation: 0, failures: 0, running: 0, failed: false, retryRequested: false };
        resources.set(mutation.resource, state);
      }
      const generation = ++state.generation;
      const failures = state.failures;
      state.mutation = mutation;
      state.unit = unit;
      state.running += 1;
      function failed(error) {
        state.failures += 1;
        state.failed = true;
        reportHandlerError(error, { mutation, unit });
      }
      function finished() {
        state.running -= 1;
        if (!state.running && state.retryRequested) {
          state.retryRequested = false;
          if (workByHandler.get(handler) === resources) {
            // Let the other units settle before regrouping the failed work;
            // the Map consumer can retain its heritage -> features ordering.
            Promise.resolve().then(() => retryPending(false));
          }
        }
      }
      try {
        const result = handler(mutation);
        if (result && typeof result.then === "function") {
          Promise.resolve(result).then(() => {
            if (generation === state.generation && (state.failures === failures || mutation.ids === null)) state.failed = false;
          }, failed).finally(finished);
        } else {
          if (generation === state.generation && (state.failures === failures || mutation.ids === null)) state.failed = false;
          finished();
        }
      } catch (error) { failed(error); finished(); }
    }

    function retryPending(requestRunning = true) {
      const groups = new Map();
      for (const [handler, resources] of workByHandler) {
        for (const [resource, state] of resources) {
          if (!groups.has(resource)) groups.set(resource, []);
          groups.get(resource).push({ handler, state });
        }
      }
      for (const group of groups.values()) {
        if (group.some(({ state }) => state.running)) {
          if (requestRunning) group.forEach(({ state }) => { if (state.running) state.retryRequested = true; });
          continue;
        }
        const failed = group.filter(({ state }) => state.failed);
        if (!failed.length) continue;
        const mutation = { ...failed[0].state.mutation, action: "update", ids: null, versions: undefined };
        failed.forEach(({ handler, state }) => invoke(handler, mutation, state.unit));
      }
    }

    function invalidate(mutation) {
      const normalized = normalizeMutationPayload(mutation);
      if (!normalized) {
        return {
          accepted: false, knownResource: false, profiles: [], units: [], handlerCalls: 0,
        };
      }
      const resolution = resolveInvalidation(normalized.resource);
      const recovering = [...workByHandler.values()].some(resources => resources.get(normalized.resource)?.failed);
      const work = recovering ? { ...normalized, action: "update", ids: null, versions: undefined } : normalized;
      let handlerCalls = 0;
      resolution.units.forEach((unit) => {
        [...(handlersByUnit.get(unit) || [])].forEach((handler) => {
          handlerCalls += 1;
          // A newer event also recovers earlier failed work for this consumer.
          invoke(handler, work, unit);
        });
      });
      return { accepted: true, ...resolution, handlerCalls };
    }

    function clear() {
      handlersByUnit.clear();
      workByHandler.clear();
    }

    return { clear, invalidate, registerInvalidator, retryPending };
  }

  return {
    ACTIONS,
    ASPECTS,
    INVALIDATION_UNITS,
    PROFILE_INVALIDATIONS,
    RESOURCE_PROFILES,
    SCHEMA_VERSION,
    createInvalidatorRegistry,
    isCanonicalResource,
    isValidMutationPayload,
    normalizeMutationPayload,
    resolveInvalidation,
    resolveInvalidationUnits,
    resolveProfiles,
  };
});
