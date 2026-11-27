/* Détail connecté d'une Observation depuis une fiche métier modale. */
(function (root) {
  "use strict";
  let dialog, state, formView;
  const element = selector => dialog.querySelector(selector);
  const can = permission => !root.BusinessRuntime || root.BusinessRuntime.can(permission);

  function sameContext(current) {
    return state === current && (!current.isParentCurrent || current.isParentCurrent())
      && (!current.runtime ||
      (root.BusinessRuntime === current.runtime && current.runtime.isActive()));
  }

  function assertObservation(observation, current) {
    const parent = root.ObservationForm.parent(observation);
    if (String(observation.id) !== current.id || parent.resource !== current.parent.resource
        || String(parent.id) !== current.parent.id) {
      throw new Error("L’observation ne correspond pas à l’objet sélectionné.");
    }
    return observation;
  }

  function message(value, error = false) {
    element("[role=status]").textContent = value;
    element("[role=status]").classList.toggle("error", error);
  }

  function dirty(current) {
    return current.observation && current.controls &&
      (current.conflict || Object.keys(root.ObservationForm.changes(current.controls, current.observation)).length > 0);
  }

  function close() {
    const current = state;
    if (!current || current.busy) return false;
    if (dirty(current) && !root.confirm("Abandonner les modifications de cette observation ?")) return false;
    state = null;
    dialog.close();
    current.onReturn?.();
    return true;
  }

  async function render(observation, current, sequence) {
    const fields=root.ObservationForm.fields(observation);
    const catalogs = new Map(await Promise.all(fields.filter(f=>f[2]==="select").map(async f=>
      [f[0],await root.ReferenceData.load(f[4],current.fetchJson)])));
    if (!sameContext(current) || current.sequence !== sequence || dirty(current)) return false;
    const descriptor=root.ObservationForm.composition(observation); descriptor.children=["photos"];
    formView=root.EntityFormView.create({root,document,descriptor,id:"observation-modal",
      onSubmit:submit,onCancel:close,onClose:close});
    current.observation=observation; current.controls=new Map(); current.conflict=false;
    for(const [key,label,kind,,catalog,labelKey] of fields) {
      const control=formView.controls.get(key);
      if(kind==="select") root.ReferenceData.fillSelect(control,catalogs.get(key),observation[key],`Sans ${label.toLowerCase()}`,observation[labelKey]);
      else control.value=observation[key]??"";
      current.controls.set(key,control);
    }
    formView.controls.get("parent_label").value=root.ObservationForm.parentText(observation);
    formView.sync({mode:can("business.update")?"edit":"read",objectId:current.id});
    formView.setSubject({ objectName: observation.designation });
    formView.children.get("photos").dataset.photos="";
    root.PhotoMedia.renderGallery(formView.children.get("photos"),observation);
    dialog.replaceChildren(formView.element);
    message(current.savedVersion===observation.version ? "Observation enregistrée dans PostgreSQL." : "");
    return true;
  }

  async function read(current) {
    const sequence = ++current.sequence;
    try {
      const observation = assertObservation(await current.fetchJson(
        `/api/observations/${encodeURIComponent(current.id)}`), current);
      if (!sameContext(current) || sequence !== current.sequence) return false;
      return render(observation, current, sequence);
    } catch (error) {
      if (sameContext(current) && sequence === current.sequence) message(error.message, true);
      return false;
    }
  }

  async function submit(event) {
    event.preventDefault();
    const current = state;
    if (!current?.observation || current.busy || !sameContext(current) || !can("business.update")) return;
    const changes = root.ObservationForm.changes(current.controls, current.observation);
    if (!Object.keys(changes).length) { message("Aucune modification."); return; }
    current.busy = true;
    formView.sync({mode:"edit",objectId:current.id,pending:true,dirty:true});
    element("fieldset").disabled = true;
    element("[type=submit]").disabled = true;
    message("Enregistrement…");
    try {
      const updated = assertObservation(await current.fetchJson(
        `/api/observations/${encodeURIComponent(current.id)}`, {
          method: "PUT", headers: { "Content-Type": "application/json",
            ...root.ResourceConcurrency.headers(current.observation.version) },
          body: JSON.stringify(changes),
        }), current);
      if (!sameContext(current)) return;
      current.observation = updated;
      current.savedVersion = updated.version;
      current.controls = null;
      const rendered = await render(updated, current, ++current.sequence);
      try {
        await root.ResourceMutationController.publishMutation(updated.mutation, { applyLocally: true });
      } catch (error) { console.error("Publication Observation", error); }
      if (sameContext(current) && !dirty(current)) message(rendered
        ? "Observation enregistrée dans PostgreSQL."
        : "Observation enregistrée. Actualisation incomplète.", !rendered);
    } catch (error) {
      if (sameContext(current)) {
        current.conflict = error.status === 409;
        message(error.message, true);
      }
    } finally {
      current.busy = false;
      if (sameContext(current)) {
        formView.sync({mode:can("business.update")?"edit":"read",objectId:current.id,pending:false,conflict:current.conflict,dirty:dirty(current)});
        element("fieldset").disabled = !can("business.update");
        element("[type=submit]").disabled = false;
      }
    }
  }

  function initialize() {
    dialog = document.createElement("dialog");
    dialog.id = "observation-detail-dialog";
    dialog.className = "observation-create-dialog";
    formView=root.EntityFormView.create({root,document,
      descriptor:root.ObservationForm.composition({parent:{resource:"desordres",id:"loading"}}),
      id:"observation-modal",onSubmit:submit,onCancel:close,onClose:close});
    formView.sync({loading:true}); dialog.replaceChildren(formView.element); document.body.append(dialog);
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    root.addEventListener("digues-app:context-closed", () => {
      state = null;
      if (dialog.open) dialog.close();
    });
    root.ResourceMutationController.registerInvalidator("open-detail", async mutation => {
      const current = state;
      if (!current?.observation || !["observations", "photos"].includes(mutation.resource)) return;
      if (mutation.resource === "observations" && mutation.ids !== null && !mutation.ids.includes(current.id)) return;
      if (dirty(current)) {
        if (mutation.resource === "observations") {
          current.conflict = true;
          message("Cette observation a changé ailleurs. Votre saisie est conservée.", true);
        } else message("Photos modifiées. Votre saisie d’observation est conservée.");
        return;
      }
      if (!await read(current) && sameContext(current)) throw new Error("Actualisation de l’observation impossible.");
    });
  }

  function open({ id, parent, fetchJson, onReturn, isParentCurrent }) {
    if (!id || !parent?.id || typeof fetchJson !== "function" || !can("business.read")) return false;
    root.ObservationForm.parent({ parent });
    if (!dialog) initialize();
    if (dialog.open) return false;
    state = { id: String(id), parent: { resource: parent.resource, id: String(parent.id) },
      fetchJson, onReturn, isParentCurrent, runtime: root.BusinessRuntime, sequence: 0, controls: null };
    formView.general.hidden = true; formView.sync({loading:true});
    message("Chargement de l’observation…");
    dialog.showModal();
    void read(state);
    return true;
  }

  function isOpenFor(parent) {
    return Boolean(state && dialog?.open && parent
      && state.parent.resource === parent.resource
      && state.parent.id === String(parent.id)
      && sameContext(state));
  }

  root.ObservationDetail = { isOpenFor, open };
})(globalThis);
