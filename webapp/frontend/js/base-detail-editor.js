/* Passerelle Base → éditeur commun ; navigation et consultation restent à Base. */
(function (root) {
  "use strict";
  function create({ host, request, onReturn, onNavigate, onStateChange = () => {}, onMutationConfirmed,
    contextActions = [], onClose, onManageReperage }) {
    const document = host.ownerDocument;
    const scope = document.createElement("div");
    const panel = document.createElement("section"); panel.id = "data-entity-detail"; panel.hidden = true;
    scope.append(panel);
    root.DataEntityDetail.createHost(document, panel);
    const actionParents = contextActions.map(control => ({ control, parent: control.parentElement }));
    function restoreContextActions() {
      for (const { control, parent } of [...actionParents].reverse()) parent.prepend(control);
    }
    let active = false, booting = false, wasEditing = false, finishing = false, target = null;
    // The old controller captures private ports at construction. Keep those
    // references, while giving its connected host unique IDs in the Base DOM.
    const namespacePorts = () => {
      panel.id = "base-detail-editor"; panel.className = "base-detail-editor";
      for (const element of scope.querySelectorAll('[id^="data-entity-detail-"]'))
        if (!contextActions.includes(element)) element.id = `base-editor-${element.id}`;
    };
    const controller = root.DataEntityDetail.createController({ rootElement: scope, eventTarget: scope, fetchJson: request,
      contextActions, onFormClose: onClose, onManageReperage,
      onReleaseContextActions: restoreContextActions,
      onMutationConfirmed,
      onBack: () => { if (close()) void onReturn(); },
      onStateChange(state) {
        namespacePorts();
        onStateChange();
        if (!active || booting || finishing) return;
        const moved = state.resource && (state.resource !== target.resource || state.id !== target.id);
        const returned = wasEditing && !state.editing && !state.pending;
        wasEditing ||= state.editing;
        if (!moved && !returned) return;
        finishing = true;
        queueMicrotask(() => {
          if (!active) { finishing = false; return; }
          if (!close()) { finishing = false; return; }
          finishing = false;
          if (moved) void onNavigate(state.resource, state.id); else void onReturn();
        });
      },
    });
    namespacePorts(); host.append(scope);
    async function open(resource, id) {
      active = true; booting = true; wasEditing = false; target = { resource, id: String(id) };
      try {
        const opened = await controller.open(resource, id);
        if (!active || !opened || controller.getState().id !== String(id)) return false;
        await controller.startEditing(); wasEditing = controller.getState().editing;
        return wasEditing;
      } finally { booting = false; namespacePorts(); onStateChange(); }
    }
    function close() {
      if (active && !controller.close()) return false;
      restoreContextActions();
      active = false; wasEditing = false; return true;
    }
    return { open, close, hasDraft: () => active && controller.hasDraft(),
      isActive: () => active, getState: () => active ? controller.getState() : {},
      isBlocked: () => active && (booting || controller.getState().pending || controller.getFormView()?.element.dataset.state === "uncertain"),
      invalidate: mutation => active ? controller.invalidate(mutation) : false };
  }
  root.BaseDetailEditor = { create };
})(globalThis);
