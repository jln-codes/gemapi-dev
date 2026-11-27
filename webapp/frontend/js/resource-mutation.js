(function (root, factory) {
  const dependency = typeof module === "object" && module.exports
    ? require("./resource-invalidation.js") : root.ResourceInvalidation;
  const events = typeof module === "object" && module.exports
    ? require("./resource-events.js") : root.ResourceEvents;
  const api = factory(dependency, events);
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }
  root.ResourceMutation = api;
  root.ResourceMutationController = api.createController({
    bus: root.InterPageBus,
    localInstanceId: root.InterfaceCoordination?.instanceId,
  });
  const reconnect = event => {
    const runtime = root.BusinessRuntime;
    const key = runtime?.context?.key || null;
    if (runtime && (!runtime.isActive() || event.detail?.contextKey !== key
        || event.detail?.generation !== runtime.generation())) return;
    root.ResourceMutationController.retryInvalidations();
  };
  root.addEventListener("digues-app:backend-reconnected", reconnect);
  root.addEventListener("digues-app:context-closed", () => {
    root.removeEventListener("digues-app:backend-reconnected", reconnect);
  }, { once: true });
})(typeof globalThis !== "undefined" ? globalThis : this, function (Invalidation, Events) {
  "use strict";

  const EVENT_TYPE = "resource-mutated";

  function createMutationPayload(
    { resource, action, ids, aspects, eventId, occurredAt, versions, originClientId, originInstanceId },
    { localInstanceId, localClientId, now = () => Date.now() },
  ) {
    const candidate = {
      schemaVersion: Invalidation.SCHEMA_VERSION,
      eventId: eventId ?? Events.createId(),
      originClientId: originClientId ?? localClientId,
      resource,
      action,
      ids,
      originInstanceId: originInstanceId ?? localInstanceId,
      occurredAt: occurredAt ?? now(),
    };
    if (aspects !== undefined) candidate.aspects = aspects;
    if (versions !== undefined) candidate.versions = versions;
    const normalized = Invalidation.normalizeMutationPayload(candidate);
    if (!normalized) throw new TypeError("Mutation de ressource invalide");
    return normalized;
  }

  function createController({
    bus,
    localInstanceId,
    localClientId,
    transportFactory,
    now = () => Date.now(),
    registry = Invalidation.createInvalidatorRegistry(),
  }) {
    if ((!transportFactory && (!bus || typeof bus.publish !== "function" || typeof bus.subscribe !== "function"))
        || typeof localInstanceId !== "string" || !localInstanceId.trim()) {
      throw new Error("Configuration de coordination des mutations incomplète.");
    }

    let storage;
    try { storage = globalThis.localStorage; } catch (_error) { /* ephemeral fallback */ }
    const identity = Events.createClientIdentity(storage);
    const dispatcher = Events.createDispatcher({ now, consume: (mutation) => {
      const result = registry.invalidate(mutation);
      return { ...result, reason: result.knownResource ? "invalidated" : "unknown-resource" };
    } });
    async function publishOneMutation(descriptor, { applyLocally = false } = {}) {
      const payload = createMutationPayload(descriptor, {
        localInstanceId, localClientId: localClientId || await identity.getId(), now,
      });
      if (applyLocally ? !dispatcher.dispatch(payload).accepted : !dispatcher.remember(payload.eventId)) {
        if (applyLocally) await registry.retryPending?.();
        return payload;
      }
      try {
        await transport.send(payload);
      } catch (error) {
        dispatcher.forget(payload.eventId); // An explicit retry may reuse this event ID.
        throw error;
      }
      return payload;
    }

    async function publishMutation(descriptor, options = {}) {
      let result, firstError;
      try { result = await publishOneMutation(descriptor, options); }
      catch (error) { firstError = error; }
      // Each related receipt has its own durable event ID. A duplicate primary
      // or one failed invalidation must not swallow the other confirmed events.
      for (const related of descriptor.relatedMutations || []) {
        try { await publishOneMutation(related, options); }
        catch (error) { firstError ||= error; }
      }
      if (firstError) throw firstError;
      return result;
    }

    function handleRemoteMutation(payload) {
      const mutation = Invalidation.normalizeMutationPayload(payload);
      if (!mutation) {
        return { accepted: false, reason: "invalid-payload" };
      }
      if (mutation.originInstanceId === localInstanceId) {
        return { accepted: false, reason: "local-origin" };
      }
      const result = dispatcher.dispatch(mutation);
      if (result.reason === "duplicate-event") registry.retryPending?.();
      return result;
    }

    const transport = transportFactory ? transportFactory(handleRemoteMutation)
      : Events.createBusTransport(bus, handleRemoteMutation);

    function close() {
      transport.close?.();
      dispatcher.clear();
      registry.clear();
    }

    return {
      close,
      handleRemoteMutation,
      dispatch: dispatcher.dispatch,
      ready: identity.ready,
      getClientId: async () => localClientId || await identity.getId(),
      requestHeaders: async () => ({
        "X-Digues-App-Client-Id": localClientId || await identity.getId(),
        "X-Digues-App-Instance-Id": localInstanceId,
      }),
      localInstanceId,
      publishMutation,
      registerInvalidator: registry.registerInvalidator,
      retryInvalidations: () => registry.retryPending?.(),
    };
  }

  return { EVENT_TYPE, createController, createMutationPayload };
});
