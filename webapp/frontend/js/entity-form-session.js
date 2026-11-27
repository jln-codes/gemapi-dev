/* Cycle de vie des formulaires métier hébergés par businessEditorPanel. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.EntityFormSession = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Shared by Base without mounting a second session or owning its draft.
  function mayLeave({ pending = false, uncertain = false, dirty = false, confirm }) {
    return !pending && !uncertain && (!dirty || confirm());
  }
  function failureState(error) {
    return { conflict: error?.status === 409, forbidden: error?.status === 403,
      uncertain: (!Number.isInteger(error?.status)
        || error.status >= 500 || error.status === 408) };
  }
  function validateReceipt(result, { id = null, version = null } = {}) {
    if (!result?.properties?.id || !Number.isSafeInteger(result.properties.version)
        || result.properties.version < 1
        || (id !== null && String(result.properties.id) !== String(id))
        || (version !== null && result.properties.version <= version)) {
      throw new Error("Reçu de sauvegarde incohérent.");
    }
    return result;
  }

  function create({ root = globalThis, shell, panel, element, objectType,
    label, onObservations, isDirty, hasActiveDraft, onDiscard,
    setPending, setStatus, formView = null, onDispose = () => {} }) {
    if (!shell || !panel || !element || !objectType || !setPending || !setStatus) {
      throw new Error("Session de formulaire métier incomplète.");
    }
    let generation = 0;
    let disposed = false;
    let savingToken = null;
    let loading = false, loadGeneration = 0, revision = 0;
    let identity = null, intent = null;
    let runtime = root.BusinessRuntime;
    const can = permission => !root.BusinessRuntime || root.BusinessRuntime.can(permission);
    const panelSession = panel.connect({
      element, shell, label, onObservations, isDirty, hasActiveDraft, formView,
      canLeave, syncState: sync, isSaving: () => savingToken === generation,
      onDiscard() {
        generation += 1;
        loadGeneration += 1;
        loading = false; intent = null;
        onDiscard();
        formView?.sync({ closed: true });
      },
    });
    const isCurrent = token => !disposed && token === generation && panelSession.isActive()
      && runtime === root.BusinessRuntime
      && identity?.objectType === shell.state.objectType
      && identity?.objectId === (shell.state.objectId == null ? null : String(shell.state.objectId))
      && (!root.BusinessRuntime || root.BusinessRuntime.isActive?.() !== false);

    function sync() {
      formView?.sync({ mode: shell.state.mode, objectId: shell.state.snapshot ? shell.state.objectId : null,
        pending: savingToken === generation || shell.state.pending, loading,
        dirty: shell.state.dirty, conflict: shell.state.conflict, error: shell.state.error,
        forbidden: shell.state.forbidden, uncertain: shell.state.uncertain,
        retryUncertain: Boolean(intent?.retry), closed: false });
    }
    function hasDraft() {
      return Boolean(shell.state.pending || savingToken === generation || shell.state.uncertain
        || shell.state.conflict || (loading ? shell.state.dirty : isDirty()) || hasActiveDraft());
    }
    function canEdit() {
      return isCurrent(generation) && !loading && !shell.state.pending && savingToken !== generation
        && !shell.state.forbidden && !shell.state.uncertain
        && ["create", "edit"].includes(shell.state.mode)
        && can(shell.state.mode === "create" ? "business.create" : "business.update");
    }
    function canLeave() {
      return mayLeave({ pending: shell.state.pending || savingToken === generation,
        uncertain: shell.state.uncertain, dirty: hasDraft(),
        confirm: () => root.confirm(`Abandonner les modifications de ${label} ?`) });
    }
    function changed() {
      if (!isCurrent(generation)) return;
      revision += 1;
      shell.markDirty(isDirty());
      sync();
    }
    element.addEventListener?.("input", changed);
    element.addEventListener?.("change", changed);

    function begin(mode, id, reset) {
      if (disposed || !can(mode === "create" ? "business.create" : "business.read")
          || !panelSession.prepare(mode === "edit" ? id : null)) return null;
      const token = ++generation;
      runtime = root.BusinessRuntime;
      loading = false;
      intent = null; revision = 0;
      reset();
      if (mode === "create") shell.beginCreate({ objectType });
      else shell.beginRead({ objectType, objectId: id });
      identity = { objectType, objectId: id == null ? null : String(id) };
      setPending(false);
      sync();
      panelSession.showTab("general");
      return token;
    }

    async function load(token, work, ready, onError) {
      if (!isCurrent(token)) return null;
      const read = ++loadGeneration, startedAt = revision;
      loading = true;
      panelSession.setLoading(true);
      sync();
      try {
        const result = await work();
        if (!isCurrent(token) || read !== loadGeneration) return null;
        if (revision !== startedAt) {
          setStatus("Chargement écarté : votre saisie est conservée.");
          return null;
        }
        await ready(result, token);
        return isCurrent(token) ? result : null;
      } catch (error) {
        if (isCurrent(token) && read === loadGeneration) {
          shell.fail(error); onError(error);
        }
        return null;
      } finally {
        if (isCurrent(token) && read === loadGeneration) {
          loading = false; panelSession.setLoading(false);
          setPending(savingToken === generation || shell.state.pending); sync();
        }
      }
    }

    async function save(event, { form, payload, send, validate = () => {}, onSaved, retryUncertain = false }) {
      event.preventDefault();
      if (disposed || loading || !isCurrent(generation) || savingToken === generation || shell.state.pending
          || shell.state.forbidden || !["create", "edit"].includes(shell.state.mode)
          || !panelSession.isActive()
          || !can(shell.state.mode === "create" ? "business.create" : "business.update")
          || !form.reportValidity()) return null;
      let body;
      try { body = payload(); }
      catch (error) { shell.fail(error); setStatus(error.message, true); sync(); return null; }
      const token = generation;
      const editing = shell.state.mode === "edit";
      const id = shell.state.objectId;
      const snapshot = shell.state.snapshot;
      const version = snapshot?.properties?.version ?? null;
      const signature = JSON.stringify([id, version, body]);
      if (shell.state.uncertain && (!retryUncertain || !intent?.retry || intent.signature !== signature)) {
        setStatus("Résultat non confirmé. Vérifiez l'enregistrement ; seule la même opération récupérable peut être réessayée.", true);
        return null;
      }
      if (!intent || intent.signature !== signature) intent = { signature, retry: retryUncertain,
        operationId: root.crypto?.randomUUID() };
      let confirmed = false;
      shell.beginSave();
      savingToken = token;
      setPending(true);
      setStatus("Enregistrement auprès du serveur…");
      sync();
      try {
        const result = await send(body, { editing, id, snapshot, operationId: intent.operationId });
        if (!isCurrent(token)) return null;
        validateReceipt(result, { id: editing ? id : null, version: editing ? version : null });
        validate(result, { editing, id });
        confirmed = true;
        shell.confirm(result);
        if (!editing) shell[can("business.update") ? "beginEdit" : "beginRead"]({ objectType, objectId: result.properties.id,
          data: result, snapshot: result, geometryType: result.geometry?.type || null });
        identity.objectId = String(result.properties.id);
        intent = null;
        await onSaved(result, { editing, token });
        return result;
      } catch (error) {
        if (isCurrent(token)) {
          if (confirmed) {
            setStatus("Enregistré. L'actualisation locale est incomplète.", true);
          } else {
            const failure = failureState(error);
            shell.fail(error, failure);
            Object.assign(shell.state, { forbidden: failure.forbidden, uncertain: failure.uncertain });
            setStatus(failure.uncertain ? "Résultat non confirmé. Votre saisie est conservée." : error.message, true);
          }
        }
        return null;
      } finally {
        if (savingToken === token) savingToken = null;
        if (isCurrent(token)) { shell.state.pending = false; setPending(false); sync(); }
      }
    }

    function beforeUnload(event) {
      if (disposed || !panelSession.isActive()) return;
      if (!hasDraft()) return;
      event.preventDefault();
      event.returnValue = "";
    }
    root.addEventListener?.("beforeunload", beforeUnload);

    function dispose() {
      if (disposed) return;
      generation += 1;
      root.removeEventListener?.("beforeunload", beforeUnload);
      element.removeEventListener?.("input", changed);
      element.removeEventListener?.("change", changed);
      disposed = true;
      onDispose();
      if (panelSession.isActive()) panelSession.discard();
      element.remove?.();
    }

    return { panelSession, begin, currentToken: () => generation, isCurrent, load, save, sync, changed, canEdit,
      isSaving: () => savingToken === generation,
      close: () => panelSession.close(), dispose };
  }

  return { create, mayLeave, failureState, validateReceipt };
});
