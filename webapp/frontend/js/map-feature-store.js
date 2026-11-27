(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MapFeatureStore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function createRegistry() {
    const adapters = new Map();
    const pending = new Map();
    const resourceVersions = new Map();
    const deferred = new Map();

    function versionState(resource) {
      let state = resourceVersions.get(resource);
      if (!state) {
        state = { current: 0, collection: 0, entities: new Map() };
        resourceVersions.set(resource, state);
      }
      return state;
    }

    function nextEntityVersion(resource, id) {
      const state = versionState(resource);
      const generation = ++state.current;
      state.entities.set(id, generation);
      return generation;
    }

    function nextCollectionVersion(resource) {
      const state = versionState(resource);
      const generation = ++state.current;
      state.collection = generation;
      return generation;
    }

    function entityVersionIsCurrent(resource, id, generation) {
      const state = versionState(resource);
      return state.collection <= generation && state.entities.get(id) === generation;
    }

    function collectionVersionIsCurrentFor(resource, id, generation) {
      const state = versionState(resource);
      return state.collection === generation
        && (state.entities.get(id) || 0) <= generation;
    }

    function rememberDeferred(resource, id, operation, mutation, generation) {
      const key = `${resource}:${id}`;
      const current = deferred.get(key);
      if (!current || current.generation <= generation) {
        deferred.set(key, { generation, id, mutation, operation, resource });
      }
    }

    function registerResource(resource, adapter) {
      if (typeof resource !== "string" || !resource.trim()
          || !adapter || typeof adapter.fetchOne !== "function"
          || typeof adapter.fetchCollection !== "function"
          || typeof adapter.getId !== "function"
          || typeof adapter.listIds !== "function"
          || typeof adapter.upsert !== "function"
          || typeof adapter.remove !== "function") {
        throw new TypeError("Adaptateur cartographique invalide");
      }
      adapters.set(resource, adapter);
      return () => adapters.delete(resource);
    }

    function schedule(key, operation) {
      const current = pending.get(key);
      if (current) {
        current.next = operation;
        return current.promise;
      }
      const state = { next: null, promise: null };
      state.promise = (async () => {
        try {
          let nextOperation = operation;
          while (nextOperation) {
            try {
              await nextOperation();
            } catch (error) {
              // A superseded failure must not discard the trailing authoritative
              // read. All coalesced callers await the final attempt's result.
              if (!state.next) throw error;
            }
            nextOperation = state.next;
            state.next = null;
          }
        } finally {
          // Release before settling, not in a later promise reaction: otherwise
          // a new reload can attach itself to an already finished worker.
          if (pending.get(key) === state) pending.delete(key);
        }
      })();
      pending.set(key, state);
      return state.promise;
    }

    function shouldDefer(adapter, context) {
      if (!adapter.shouldDefer?.(context)) return false;
      rememberDeferred(
        context.resource,
        context.id,
        context.operation,
        context.mutation,
        context.generation,
      );
      adapter.markStale?.(context);
      return true;
    }

    async function reloadMapEntity(resource, id, mutation = null) {
      const adapter = adapters.get(resource);
      const identifier = String(id ?? "").trim();
      if (!adapter || !identifier) return false;
      const generation = nextEntityVersion(resource, identifier);
      return schedule(`${resource}:${identifier}`, async () => {
        const context = {
          generation, id: identifier, mutation, operation: "reload", resource,
        };
        if (shouldDefer(adapter, context)) return;
        const item = await adapter.fetchOne(identifier);
        const receivedId = String(adapter.getId(item) ?? "").trim();
        if (!receivedId || receivedId !== identifier) {
          throw new Error("Identifiant cartographique incohérent.");
        }
        if (!entityVersionIsCurrent(resource, identifier, generation)) return;
        if (shouldDefer(adapter, { ...context, item })) return;
        await adapter.upsert(item, { mutation });
      }).then(() => true);
    }

    async function removeMapEntity(resource, id, mutation = null) {
      const adapter = adapters.get(resource);
      const identifier = String(id ?? "").trim();
      if (!adapter || !identifier) return false;
      const generation = nextEntityVersion(resource, identifier);
      return schedule(`${resource}:${identifier}`, async () => {
        const context = {
          generation, id: identifier, mutation, operation: "remove", resource,
        };
        if (shouldDefer(adapter, context)) return;
        if (!entityVersionIsCurrent(resource, identifier, generation)) return;
        await adapter.remove(identifier, { mutation });
      }).then(() => true);
    }

    async function reloadMapCollection(resource, mutation = null) {
      const adapter = adapters.get(resource);
      if (!adapter) return false;
      const generation = nextCollectionVersion(resource);
      return schedule(`${resource}:*`, async () => {
        const collection = await adapter.fetchCollection();
        if (versionState(resource).collection !== generation) return;
        const items = adapter.collectionItems
          ? adapter.collectionItems(collection) : collection?.features;
        if (!Array.isArray(items)) throw new Error("Collection cartographique invalide.");
        const receivedIds = new Set();
        for (const item of items) {
          const id = String(adapter.getId(item) ?? "").trim();
          if (!id) throw new Error("Entité cartographique sans identifiant.");
          receivedIds.add(id);
          if (!collectionVersionIsCurrentFor(resource, id, generation)) continue;
          const context = {
            generation, id, item, mutation, operation: "reload", resource,
          };
          if (!shouldDefer(adapter, context)) await adapter.upsert(item, { mutation });
        }
        for (const value of adapter.listIds()) {
          const id = String(value);
          if (receivedIds.has(id)) continue;
          if (!collectionVersionIsCurrentFor(resource, id, generation)) continue;
          const context = {
            generation, id, mutation, operation: "remove", resource,
          };
          if (!shouldDefer(adapter, context)) await adapter.remove(id, { mutation });
        }
      }).then(() => true);
    }

    async function flushDeferred(resource, id) {
      const adapter = adapters.get(resource);
      const identifier = String(id ?? "").trim();
      const key = `${resource}:${identifier}`;
      const refresh = deferred.get(key);
      if (!adapter || !identifier || !refresh) return false;
      deferred.delete(key);
      try {
        if (refresh.operation === "remove") {
          await removeMapEntity(resource, identifier, refresh.mutation);
        } else {
          await reloadMapEntity(resource, identifier, refresh.mutation);
        }
        if (!deferred.has(key)) adapter.clearStale?.({ id: identifier, resource });
        return !deferred.has(key);
      } catch (error) {
        if (!deferred.has(key)) deferred.set(key, refresh);
        throw error;
      }
    }

    function hasDeferred(resource, id) {
      const identifier = String(id ?? "").trim();
      return Boolean(identifier && deferred.has(`${resource}:${identifier}`));
    }

    async function invalidate(mutation) {
      const adapter = adapters.get(mutation?.resource);
      if (!adapter) return false;
      if (mutation.ids === null) {
        return reloadMapCollection(mutation.resource, mutation);
      }
      if (!Array.isArray(mutation.ids)) return false;
      const operation = mutation.action === "delete" ? removeMapEntity : reloadMapEntity;
      await Promise.all(mutation.ids.map((id) => operation(mutation.resource, id, mutation)));
      return true;
    }

    return {
      invalidate,
      flushDeferred,
      hasDeferred,
      registerResource,
      reloadMapCollection,
      reloadMapEntity,
      removeMapEntity,
    };
  }

  return { createRegistry };
});
