function panelHarness(root) {
  let active = false;
  return {
    connect({ element, shell, isDirty, hasActiveDraft, onDiscard, canLeave }) {
      const session = {
        isActive: () => active,
        showTab() {}, sync() {}, setLoading() {}, update() {},
        discard() {
          onDiscard();
          shell.cancel();
          active = false;
          element.open = false;
          return true;
        },
        close() {
          if (canLeave) return canLeave() ? session.discard() : false;
          if (shell.state.pending) return false;
          shell.markDirty(isDirty());
          const draft = shell.state.mode === 'create'
            ? shell.state.dirty || hasActiveDraft()
            : shell.hasDraft({ active: hasActiveDraft() });
          if (draft && !root.confirm()) return false;
          return session.discard();
        },
        prepare(id = null) {
          if (active && (!id || String(shell.state.objectId) !== String(id))) {
            if (!session.close()) return false;
          } else if (active) return false;
          active = true;
          element.open = true;
          return true;
        },
      };
      return session;
    },
    beginGeometryDrawing() {}, updateGeometryDrawing() {}, finishGeometryDrawing() {},
  };
}

module.exports = panelHarness;

// Business-controller unit fixtures use the production compositor. The small
// DOM facade remains local to these unit tests; Playwright exercises real DOM.
module.exports.installView = (root, onView) => {
  const View = require('../frontend/js/entity-form-view.js');
  root.EntityFormView = {...View, create(options) {
    const view = View.create(options), nodes = {form:view.form,fieldset:view.fieldset};
    const visit = node => {
      if(node.dataset?.role) for(const quote of ["'",'"']) nodes[`[data-role=${quote}${node.dataset.role}${quote}]`]=node;
      if(node.dataset?.context)for(const quote of ["'",'"'])nodes[`[data-context=${quote}${node.dataset.context}${quote}]`]=node;
      (node.children || []).forEach(visit);
    };
    visit(view.element);view.element.nodes=nodes;
    view.form.elements=Object.fromEntries(view.controls);
    if(view.geometry){view.form.elements.geometry_type=view.geometry.type;view.geometry.type.value=options.descriptor.geometry.types[0];}
    view.form.reportValidity=()=>true;
    view.form.reset=()=>Object.values(view.form.elements).forEach(control=>{control.value='';control.checked=false;});
    onView(view);return view;
  }};
};
