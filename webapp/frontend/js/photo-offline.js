/* Photos terrain : intentions et originaux durables ; localisation dans une intention distincte. */
(function (root) {
  "use strict";
  const views = new Map();
  let dialog, form, state, formView;
  const store = () => root.OfflineRuntime.store;
  const field = name => form.elements.namedItem(name);
  const uuid = () => root.ResourceEvents.createId();
  const label = operation => operation.changes.libelle || operation.changes.designation || operation.resourceId.slice(0, 8);
  function dateToday() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  }
  function initialize() {
    dialog = document.createElement("dialog"); dialog.id = "photo-offline-dialog"; dialog.className = "photo-editor-dialog";
    dialog.setAttribute("aria-labelledby", "photo-offline-title");
    formView = root.EntityFormView.create({root, document, titleId:"photo-offline-title", descriptor:root.PhotoForm.composition("offline"),
      id:"photo-offline-view", onClose:close});
    dialog.replaceChildren(formView.element);

    formView.setFormId("photo-offline-form");
    formView.controls.get('parent_label').setAttribute('data-context','');
    for(const key of ['preview','filename']) formView.controls.get(key).setAttribute(`data-${key}`,'');
    formView.controls.get('preview').hidden=true;
    formView.cancel.setAttribute('data-close','');
    formView.setFieldNotice("storage", "Le fichier est conservé sur cet appareil avant synchronisation.");
    for(const [name,role] of [['file','file'],['camera','camera'],['select_source','select'],['capture_source','capture'],['storage','storage']])
      formView.controls.get(name).setAttribute(`data-${role}`,'');
    document.body.append(dialog); form = dialog.querySelector("form");
    form.addEventListener("input", () => { if (state && !state.busy) state.dirty = true; });
    form.addEventListener("submit", save);
    dialog.querySelector("[data-select]").onclick = () => dialog.querySelector("[data-file]").click();
    dialog.querySelector("[data-capture]").onclick = () => dialog.querySelector("[data-camera]").click();
    for (const input of dialog.querySelectorAll("[type=file]")) input.onchange = () => {
      if (!state || state.busy || !input.files[0]) return;
      state.file = input.files[0]; state.dirty = true;
      preview(state, state.file, state.file.name);
    };
    dialog.querySelector("[data-another]").onclick = () => {
      if (state?.busy) return;
      const observation = state.observation;
      close(); if (!dialog.open) open(observation);
    };
    dialog.querySelector("[data-close]").onclick = close;
    dialog.querySelector("[data-abandon]").onclick = abandon;
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
  }
  function message(value, error = false) {
    const node = dialog.querySelector("[role=status]"); node.textContent = value; node.classList.toggle("error", error);
  }
  function close() {
    if (state?.busy) return;
    if (state?.dirty && !root.confirm("Abandonner les modifications non enregistrées de cette Photo ?")) return;
    if (state?.preview) (root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL)(state.preview);
    state = null; dialog.close();
  }
  function preview(current, blob, filename) {
    if (current.preview) (root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL)(current.preview);
    current.preview = (root.BusinessRuntime?.objectUrl || URL.createObjectURL)(blob);
    const image = dialog.querySelector("[data-preview]"); image.src = current.preview; image.hidden = false;
    dialog.querySelector("[data-filename]").textContent = filename;
  }
  function lock() {
    const frozen = state?.operation?.attempts > 0 || state?.operation?.status === "confirmed";
    formView.sync({mode:frozen ? 'read' : 'create',objectId:state?.saved?.id || state?.edit?.id,
      pending:Boolean(state?.busy),uncertain:Boolean(state?.unknown),retryUncertain:true,dirty:Boolean(state?.dirty)});
    form.querySelector("fieldset").disabled = state?.busy || frozen || formView.save.hidden;
    dialog.querySelector("[type=submit]").disabled = state?.busy || frozen || formView.save.hidden;
    dialog.querySelector("[data-abandon]").hidden = !state?.operation || frozen;
    for (const key of ["select", "capture"]) dialog.querySelector(`[data-${key}]`).disabled = !!state?.operation?.transferStarted;
  }
  function open(observation, operation = null, initial = null) {
    if (!observation?.id || operation && (operation.resource !== "photos" || operation.changes.observation_id !== observation.id)) {
      throw new Error("Rattachement Photo incohérent.");
    }
    if (!dialog) initialize();
    if (dialog.open) return false;
    state = { observation: { id: observation.id, designation: observation.designation || null,
      version: observation.version ?? null, parent: observation.parent ? { ...observation.parent } : null }, operation, id: operation?.resourceId || initial?.id || uuid(),
      operationId: operation?.operationId || uuid(), file: initial?.file || null, busy: false, dirty: !!initial };
    dialog.dataset.photoId = state.id;
    dialog.querySelector("[data-preview]").hidden = true;
    dialog.querySelector("[data-preview]").removeAttribute("src");
    dialog.querySelector("[data-filename]").textContent = "";
    dialog.querySelector("[data-another]").hidden = !operation;
    const current = state;
    void root.PhotoFile.persistence().then(result => {
      if (state === current) dialog.querySelector("[data-storage]").textContent = result.persistent
        ? "Stockage persistant accordé. Cet appareil ne remplace pas une sauvegarde."
        : "Stockage local sans protection garantie contre l’éviction. Synchronisez dès que possible.";
    });
    if (operation?.mediaLocalRef) void store().readPhotoMedia(operation.mediaLocalRef).then(media => {
      if (state === current && !current.file && media) preview(current, media.blob, media.originalFilename);
    }).catch(error => { if (state === current) message(error.message, true); });
    form.reset();
    const savedDate = operation?.changes;
    field("date").value = savedDate && Object.prototype.hasOwnProperty.call(savedDate, "date")
      ? (savedDate.date ?? "") : (initial?.date ? initial.date : dateToday());
    for (const name of ["libelle", "designation"]) field(name).value = operation?.changes[name] || initial?.[name] || "";
    dialog.querySelector("[data-context]").textContent = observation.designation || observation.id;
    dialog.querySelector("[data-abandon]").hidden = !operation;
    message(operation ? "Métadonnées locales modifiables avant le premier envoi de la Photo." : "Photo en préparation — fichier à ajouter.");
    if (current.file) preview(current, current.file, current.file.name);
    lock(); dialog.showModal(); return true;
  }
  async function save(event) {
    event.preventDefault(); const current = state;
    if (!current || current.busy || !form.reportValidity()) return;
    current.busy = true; form.querySelector("fieldset").disabled = true; dialog.querySelector("[type=submit]").disabled = true;
    try {
      const changes = { id: current.id, observation_id: current.observation.id, date: field("date").value || null,
        libelle: field("libelle").value, designation: field("designation").value };
      const media = current.file ? await root.PhotoFile.prepare(current.file, root.PhotoQualityPolicy) : null;
      const operation = current.operation
        ? await store().editPhotoCreate(current.operation.operationId, changes, media)
        : await store().enqueuePhotoCreate({ operationId: current.operationId, changes, observation: current.observation, media });
      if (state !== current) return;
      current.operation = operation; current.operationId = operation.operationId; current.dirty = false; current.file = null;
      dialog.querySelector("[data-another]").hidden = false;
      dialog.querySelector("[data-abandon]").hidden = false;
      message(operation.mediaLocalRef ? "Photo enregistrée localement — synchronisation en attente." : "Photo en préparation enregistrée localement — fichier à ajouter.");
      if (operation.mediaLocalRef) void root.OfflineOperationsRuntime?.synchronize().then(refreshVisible).catch(() => {});
      try { await refreshVisible(); }
      catch (error) { message("Photo enregistrée localement. Galerie à actualiser : " + error.message, true); }
    } catch (error) { if (state === current) message(error.name === "QuotaExceededError" ? "Stockage insuffisant. Fichier non enregistré ; saisie conservée." : "Saisie conservée : " + error.message, true); }
    finally { current.busy = false; if (state === current) lock(); }
  }
  async function abandon() {
    const current = state;
    if (!current?.operation || current.busy || !root.confirm("Abandonner cette Photo locale et supprimer son fichier et ses métadonnées de cet appareil ?")) return;
    current.busy = true;
    try {
      await store().removeOperation(current.operation.operationId, true);
      current.dirty = false; current.busy = false; close();
      try { await refreshVisible(); } catch (error) { console.error("Galerie Photo locale à actualiser", error); }
    } catch (error) { current.busy = false; message(error.message, true); }
  }
  async function render(container, observation, onChanged) {
    const viewState = views.get(container);
    (viewState?.mediaUrls || []).forEach(root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL);
    const existing = container.querySelector(":scope > [data-local-photos]");
    if (existing) existing.remove();
    const section = document.createElement("section"); section.dataset.localPhotos = observation.id;
    section.mediaUrls = [];
    if (viewState) viewState.mediaUrls = section.mediaUrls;
    section.className = "photo-local-gallery"; container.append(section);
    const allOperations = await store().listOperations();
    const localisations = new Map(allOperations.filter(o => o.resource === "photos" && o.action === "update-localisation")
      .map(o => [o.resourceId, o]));
    const operations = allOperations.filter(o => o.resource === "photos" && o.action === "create"
      && o.dependsOn?.length === 1 && o.dependsOn[0].resource === "observations" && o.dependsOn[0].id === observation.id);
    if (!section.isConnected) return;
    const title = document.createElement("h3"); title.textContent = `Photos en préparation (${operations.length})`; section.append(title);
    const add = document.createElement("button"); add.type = "button"; add.className = "wide-button secondary";
    add.textContent = "+ Préparer une Photo"; add.onclick = () => open(observation); section.append(add);
    add.dataset.requires = 'offline.write';
    for (const operation of operations) {
      const photo = root.LocalProjection.localPhoto(operation);
      const eligibility = await store().photoEligibility(operation.operationId);
      if (!section.isConnected) return;
      const card = document.createElement("article"); card.className = "photo-local-card"; card.dataset.photoId = photo.id;
      const name = document.createElement("strong"); name.textContent = label(operation);
      const context = document.createElement("p"); context.textContent = `Observation : ${observation.designation || observation.id}`;
      const status = document.createElement("p"); status.textContent = `${operation.status === "syncing" ? "Synchronisation" : root.OfflineOperations.photoReasonLabel(eligibility.reason)}.${operation.mediaLocalRef ? " Fichier conservé sur cet appareil." : " Aucun fichier associé."}${operation.message ? " " + operation.message : ""}`;
      const localisation = localisations.get(photo.id);
      if (localisation) status.textContent += ` Localisation : ${root.OfflineOperations.statusLabel(localisation.status)}.`;
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "wide-button secondary";
      edit.textContent = "Modifier les métadonnées locales"; edit.onclick = () => open(observation, operation);
      const discard = document.createElement("button"); discard.type = "button"; discard.className = "wide-button secondary";
      discard.textContent = "Abandonner cette Photo locale";
      discard.onclick = async () => {
        if (!root.confirm("Abandonner cette Photo locale, son fichier et ses métadonnées ?")) return;
        try { await store().removeOperation(operation.operationId, true); await refreshVisible(); }
        catch (error) { status.textContent = error.message; }
      };
      discard.hidden = operation.attempts > 0;
      edit.textContent = operation.attempts > 0 ? "Voir la Photo locale" : "Modifier les métadonnées locales";
      const locate = document.createElement("button"); locate.type = "button"; locate.className = "wide-button secondary";
      locate.textContent = localisations.has(photo.id) ? "Voir la localisation locale" : "Ajouter la localisation du photographe";
      locate.onclick = () => void root.PhotoLocalisation.openOffline(photo, observation,
        () => refreshVisible(), localisations.get(photo.id)).catch(error => { status.textContent = error.message; });
      card.append(name, context, status, edit, locate, discard); section.append(card);
      if (operation.mediaLocalRef) await mediaPreview(section, card, operation.mediaLocalRef);
    }
    const confirmed = await store().confirmedPhotos(observation.id);
    for (const item of confirmed) {
      if (observation.serverPhotoIds?.includes(item.photo.id) || operations.some(o => o.resourceId === item.photo.id)) continue;
      const card = document.createElement("article"); card.className = "photo-local-card"; card.dataset.photoId = item.photo.id;
      const title = document.createElement("p"); title.textContent = `${item.photo.libelle || item.photo.designation || item.originalFilename} — Synchronisée (copie locale conservée)`;
      const localisation = localisations.get(item.photo.id);
      if (localisation) title.textContent += ` · Localisation : ${root.OfflineOperations.statusLabel(localisation.status)}`;
      const locate = document.createElement("button"); locate.type = "button"; locate.className = "wide-button secondary";
      locate.textContent = localisations.has(item.photo.id) ? "Voir la localisation locale" : "Ajouter la localisation du photographe";
      locate.onclick = () => void root.PhotoLocalisation.openOffline(item.photo, observation,
        () => refreshVisible(), localisations.get(item.photo.id)).catch(error => { title.textContent = error.message; });
      card.append(title, locate); section.append(card); await mediaPreview(section, card, item.mediaLocalRef);
    }
    onChanged?.(operations.length);
  }
  async function mediaPreview(section, card, ref) {
    const media = await store().readPhotoMedia(ref);
    if (!media?.blob || !section.isConnected) return;
    const url = (root.BusinessRuntime?.objectUrl || URL.createObjectURL)(media.blob); section.mediaUrls.push(url);
    const image = document.createElement("img"); image.src = url; image.alt = media.originalFilename; image.loading = "lazy";
    image.style.maxWidth = "100%"; image.style.maxHeight = "160px";
    const view = document.createElement("button"); view.type = "button"; view.textContent = "Voir le fichier local";
    view.onclick = () => {
      const viewer = document.createElement("dialog"); viewer.className = "photo-viewer";
      const full = document.createElement("img"), close = document.createElement("button");
      const fullUrl = (root.BusinessRuntime?.objectUrl || URL.createObjectURL)(media.blob); full.src = fullUrl; full.alt = media.originalFilename;
      close.textContent = "Fermer"; close.onclick = () => viewer.close();
      viewer.append(full, close); viewer.onclose = () => { (root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL)(fullUrl); viewer.remove(); };
      document.body.append(viewer); viewer.showModal();
    };
    card.append(image, view);
  }
  async function mount(container, observation, onChanged = null) {
    (views.get(container)?.mediaUrls || []).forEach(root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL);
    views.set(container, { observation: { id: observation.id, designation: observation.designation || null,
      version: observation.version ?? null, serverPhotoIds: (observation.photos || []).map(p => p.id), parent: observation.parent ? { ...observation.parent } : null }, onChanged });
    await render(container, views.get(container).observation, onChanged);
  }
  async function refreshVisible() {
    for (const [container, view] of views) {
      if (!container.isConnected) { (view.mediaUrls || []).forEach(root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL); views.delete(container); continue; }
      await render(container, view.observation, view.onChanged);
    }
    const current = state;
    if (current?.operation && !current.busy) {
      const latest = (await store().listOperations()).find(o => o.resourceId === current.id && o.resource === "photos" && o.action === "create");
      if (state !== current) return;
      if (latest?.attempts > 0) {
        current.operation = latest; lock();
        message(current.dirty ? "Envoi déjà commencé. Votre saisie reste affichée ; l’intention envoyée est figée."
          : latest.message || "Synchronisation en cours ; fichier conservé sur cet appareil.");
      } else if (!latest && current.operation.mediaLocalRef) {
        const media = await store().readPhotoMedia(current.operation.mediaLocalRef);
        if (state === current && media?.integrated) {
          current.operation = { ...current.operation, status: "confirmed" }; lock();
          message(current.dirty ? "Photo synchronisée. Vos modifications non enregistrées restent affichées."
            : "Photo synchronisée — fichier préparé conservé sur cet appareil.");
        }
      }
    }
  }
  root.PhotoOffline = { open, mount, refreshVisible };
})(globalThis);
