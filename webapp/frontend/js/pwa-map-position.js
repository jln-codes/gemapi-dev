(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PwaMapPosition = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function createController({ geolocation, settings, onChange = () => {},
    onPosition = () => {}, onPermanentError = () => {} }) {
    let state = { active: false, phase: "idle", position: null, error: null };
    let unsubscribe = null;
    let generation = 0;

    function publish(next) {
      state = next;
      onChange({ ...state });
    }

    function stop() {
      if (!state.active && !unsubscribe) return;
      generation += 1;
      const release = unsubscribe;
      unsubscribe = null;
      release?.();
      publish({ active: false, phase: "idle", position: null, error: null });
    }

    function fail(error, currentGeneration) {
      if (generation !== currentGeneration || !state.active) return;
      if (!geolocation.isStandalone()) {
        stop();
        return;
      }
      if (["PERMISSION_DENIED", "UNSUPPORTED"].includes(error?.code)) {
        stop();
        publish({ active: false, phase: "error", position: null, error });
        onPermanentError(error);
      } else {
        publish({ ...state, phase: "error", error });
      }
    }

    function start() {
      if (state.active) return true;
      if (!geolocation.isStandalone()) return false;
      const currentGeneration = ++generation;
      publish({ active: true, phase: "acquiring", position: null, error: null });
      try {
        const release = geolocation.subscribe((position) => {
          if (generation !== currentGeneration || !state.active) return;
          if (!geolocation.isStandalone()) {
            stop();
            return;
          }
          const policy = settings.loadSettings();
          if (position.accuracy == null
              || position.accuracy > policy.tracking.maxAccuracyMeters) {
            publish({ ...state, phase: "low_accuracy", error: null });
            return;
          }
          if (!settings.shouldAcceptPosition(position, state.position, policy)) {
            if (state.phase !== "tracking") {
              publish({ ...state, phase: state.position ? "tracking" : "acquiring", error: null });
            }
            return;
          }
          const first = state.position === null;
          publish({ active: true, phase: "tracking", position, error: null });
          onPosition(position, { first });
        }, (error) => fail(error, currentGeneration));
        if (generation === currentGeneration && state.active) unsubscribe = release;
        else release();
      } catch (error) {
        if (["PERMISSION_DENIED", "UNSUPPORTED"].includes(error?.code)) {
          fail(error, currentGeneration);
        } else {
          stop();
          publish({ active: false, phase: "error", position: null, error });
        }
      }
      return state.active;
    }

    return { start, stop, getState: () => ({ ...state }) };
  }

  return { createController };
});
