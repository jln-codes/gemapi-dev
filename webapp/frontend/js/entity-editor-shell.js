(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.EntityEditorShell = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MODES = Object.freeze(["create", "edit", "read"]);
  const LIFECYCLE_DEFAULTS = Object.freeze({
    dirty: false,
    pending: false,
    error: null,
    conflict: false,
    forbidden: false,
    uncertain: false,
  });
  const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key);

  function assertMode(mode) {
    if (!MODES.includes(mode)) {
      throw new TypeError(`Mode d’éditeur inconnu : ${mode}.`);
    }
  }

  function requireGeometry({
    mode,
    currentGeometry = null,
    geometryChanged = false,
    draftGeometry = null,
    allowedTypes = [],
    label = "l’objet",
  } = {}) {
    assertMode(mode);
    const geometry = geometryChanged ? draftGeometry : currentGeometry;
    if (!geometry) {
      throw Object.assign(new Error(
        `La géométrie de ${label} est obligatoire. Dessinez-la ou confirmez un tracé GPS avant d’enregistrer.`,
      ), { geometryCause: "required" });
    }
    if (allowedTypes.length && !allowedTypes.includes(geometry.type)) {
      throw new Error(
        `La géométrie de ${label} doit être de type ${allowedTypes.join(" ou ")}.`,
      );
    }
    return geometry;
  }

  function create(initial = {}, hooks = {}) {
    const state = {
      mode: "read",
      objectType: null,
      geometryType: null,
      objectId: null,
      data: null,
      snapshot: null,
      ...LIFECYCLE_DEFAULTS,
    };

    function setContext(context = {}) {
      const mode = context.mode ?? state.mode;
      assertMode(mode);
      Object.assign(state, {
        mode,
        objectType: context.objectType ?? null,
        geometryType: context.geometryType ?? null,
        objectId: context.objectId ?? null,
        data: hasOwn(context, "data") ? context.data : null,
      });
      if (hasOwn(context, "snapshot")) state.snapshot = context.snapshot;
      return state;
    }

    function begin(mode, context = {}) {
      setContext({ ...context, mode });
      Object.assign(state, LIFECYCLE_DEFAULTS, {
        snapshot: hasOwn(context, "snapshot")
          ? context.snapshot
          : mode === "create" ? null : state.data,
      });
      return state;
    }

    function beginCreate(context = {}) { return begin("create", context); }
    function beginEdit(context = {}) { return begin("edit", context); }
    function beginRead(context = {}) { return begin("read", context); }

    function markDirty(dirty = true) {
      state.dirty = Boolean(dirty);
      return state;
    }

    function setSnapshot(data) {
      state.data = data;
      state.snapshot = data;
      return state;
    }

    function beginSave(context) {
      state.pending = true;
      state.error = null;
      hooks.beforeSave?.(context, state);
      return state;
    }

    function confirm(data = state.data, context) {
      state.data = data;
      state.snapshot = data;
      Object.assign(state, LIFECYCLE_DEFAULTS);
      hooks.afterSave?.(context, state);
      return state;
    }

    function fail(error, { conflict = false } = {}) {
      state.pending = false;
      state.error = error;
      state.conflict = Boolean(conflict);
      return state;
    }

    function setConflict(conflict = true) {
      state.conflict = Boolean(conflict);
      return state;
    }

    function cancel() {
      state.data = state.snapshot;
      Object.assign(state, LIFECYCLE_DEFAULTS);
      return state.snapshot;
    }

    function hasDraft(overrides = {}) {
      if (state.mode === "create") return true;
      if (state.mode !== "edit") return false;
      return Boolean(
        overrides.dirty ?? state.dirty
      ) || Boolean(
        overrides.pending ?? state.pending
      ) || Boolean(
        overrides.conflict ?? state.conflict
      ) || Boolean(state.uncertain) || Boolean(overrides.active);
    }

    begin(initial.mode || "read", initial);

    return {
      state,
      setContext,
      beginCreate,
      beginEdit,
      beginRead,
      markDirty,
      setSnapshot,
      beginSave,
      confirm,
      fail,
      setConflict,
      cancel,
      hasDraft,
    };
  }

  return { MODES, create, requireGeometry };
});
