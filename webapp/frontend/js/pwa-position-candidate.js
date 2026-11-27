(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PwaPositionCandidate = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MAX_AGE_MS = 60_000;
  const FUTURE_TOLERANCE_MS = 5_000;

  function candidateError(code) {
    const error = new Error(code);
    error.code = code;
    return error;
  }

  function validateCandidate(position, settings, now, policy) {
    if (!Number.isFinite(position?.longitude) || position.longitude < -180
        || position.longitude > 180 || !Number.isFinite(position?.latitude)
        || position.latitude < -90 || position.latitude > 90
        || !Number.isFinite(position?.timestamp) || position.timestamp < 0
        || !Number.isFinite(position?.accuracy) || position.accuracy < 0) {
      throw candidateError("INVALID_POSITION");
    }
    if (position.accuracy > settings.tracking.maxAccuracyMeters) {
      throw candidateError("LOW_ACCURACY");
    }
    if (!Number.isFinite(now) || now - position.timestamp > MAX_AGE_MS
        || position.timestamp - now > FUTURE_TOLERANCE_MS) {
      throw candidateError("STALE_POSITION");
    }
    // GEO-2 est consulté avec une première position : distance et temps ne
    // s'appliquent pas à une capture ponctuelle.
    if (!policy.shouldAcceptPosition(position, null, settings)) {
      throw candidateError("INVALID_POSITION");
    }
    return Object.freeze({
      longitude: position.longitude,
      latitude: position.latitude,
      accuracy: position.accuracy,
      timestamp: position.timestamp,
    });
  }

  function createController({ geolocation, settings, now = Date.now,
    onChange = () => {}, onAccept = () => {} }) {
    let state = { phase: "idle", target: null, candidate: null, error: null };
    let request = null;
    let generation = 0;

    function publish(next) {
      state = next;
      onChange({ ...state });
    }

    function cancel() {
      generation += 1;
      request?.cancel();
      request = null;
      publish({ phase: "idle", target: null, candidate: null, error: null });
    }

    function acquire(target, previousCandidate = null) {
      if (!geolocation.isStandalone()) {
        cancel();
        publish({ phase: "error", target: null, candidate: null,
          error: candidateError("NOT_STANDALONE") });
        return;
      }
      generation += 1;
      request?.cancel();
      request = null;
      const currentGeneration = generation;
      publish({ phase: "acquiring", target, candidate: previousCandidate, error: null });
      request = geolocation.requestCurrentPosition();
      request.promise.then((position) => {
        if (generation !== currentGeneration) return;
        request = null;
        if (!geolocation.isStandalone()) {
          cancel();
          return;
        }
        try {
          const candidate = validateCandidate(position, settings.loadSettings(), now(), settings);
          publish({ phase: "ready", target, candidate, error: null });
        } catch (error) {
          publish({ phase: "error", target, candidate: previousCandidate, error });
        }
      }, (error) => {
        if (generation !== currentGeneration) return;
        request = null;
        publish({ phase: "error", target, candidate: previousCandidate, error });
      });
    }

    function start(target) {
      acquire(target);
    }

    function retry() {
      if (state.target && state.phase !== "pending") acquire(state.target, state.candidate);
    }

    function accept() {
      if (!geolocation.isStandalone()) {
        cancel();
        return false;
      }
      if (!state.candidate || !["ready", "error"].includes(state.phase)) return false;
      const { target, candidate } = state;
      const currentGeneration = generation;
      const isCurrent = () => generation === currentGeneration
        && state.target === target && geolocation.isStandalone();
      let result;
      try {
        result = onAccept(target, candidate, isCurrent);
      } catch (error) {
        if (isCurrent()) publish({ phase: "error", target, candidate, error });
        return false;
      }
      if (result && typeof result.then === "function") {
        publish({ phase: "pending", target, candidate, error: null });
        Promise.resolve(result).then(() => {
          if (isCurrent()) cancel();
        }, (error) => {
          if (isCurrent()) publish({ phase: "error", target, candidate, error });
        });
      } else {
        cancel();
      }
      return true;
    }

    return { start, retry, accept, cancel, getState: () => ({ ...state }) };
  }

  return { MAX_AGE_MS, FUTURE_TOLERANCE_MS, validateCandidate, createController };
});
