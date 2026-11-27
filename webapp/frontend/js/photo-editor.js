/* Photos connectées : transfert vérifié puis intention JSON, sans queue offline. */
(function (root) {
  'use strict';
  let dialog, form, state, formView;
  const maxBytes = 20 * 1024 * 1024;
  const el = selector => dialog.querySelector(selector);
  const field = name => form.elements.namedItem(name);
  const metadata = ['date', 'libelle', 'designation', 'orientation_photo_id', 'cote_id', 'photographe_id'];
  function message(text, error = false) { el('[role=status]').textContent = text; el('[role=status]').classList.toggle('error', error); }
  async function request(url, options = {}) {
    await root.ResourceEventTransport?.bootstrap;
    const headers = options.method ? await root.ResourceMutationController.requestHeaders() : {};
    const response = await (globalThis.BusinessRuntime?.fetch || fetch)(url, { ...options, headers: { ...headers, ...options.headers }, signal: AbortSignal.timeout(120000) });
    let payload;
    try { payload = await response.json(); } catch (_) { /* Réponse perdue ou incomplète. */ }
    if (!response.ok || !payload) {
      const detail = payload?.detail;
      const error = new Error(typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map(item => item.msg).join(' · ') : detail?.message || 'Réponse serveur non confirmée.');
      error.status = response.ok ? 502 : response.status; error.code = detail?.code; throw error;
    }
    return payload;
  }
  function lock() {
    formView.sync({mode:state?.saved ? 'read' : state?.edit ? 'edit' : 'create',objectId:state?.saved?.id || state?.edit?.id,
      pending:Boolean(state?.busy),uncertain:Boolean(state?.unknown),retryUncertain:true,dirty:Boolean(state?.dirty),conflict:Boolean(state?.conflict),forbidden:Boolean(state?.forbidden)});
    el('fieldset').disabled = state.busy || state.unknown || state.blocked || !state.referencesReady || formView.save.hidden;
    el('[type=submit]').disabled = state.busy || state.blocked || !state.referencesReady || formView.save.hidden;
    el('[type=submit]').textContent = state.unknown ? 'Vérifier / réessayer' : 'Enregistrer la photo';
    el('[data-close]').disabled = state.busy || state.unknown;
    el('[data-prepare-local]').hidden = Boolean(state.edit || state.saved);
    el('[data-prepare-local]').disabled = Boolean(state.busy || state.unknown || state.blocked);
  }
  function close() {
    if (state.busy || state.unknown) return;
    if (state.dirty && !state.saved && !root.confirm('Abandonner la saisie de cette photo ?')) return;
    if (state.preview) (root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL)(state.preview);
    state = null; dialog.close();
  }
  function chosen(input) {
    const file = input.files[0]; if (!file || state.busy || state.unknown) return;
    if (state.preview) (root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL)(state.preview);
    state.file = file; state.preparedMedia = null; state.prepared = null; state.digest = null; state.dirty = true;
    // Une autre image est une nouvelle intention, jamais le remplacement d'un média.
    state.id = root.ResourceEvents.createId(); state.operationId = root.ResourceEvents.createId();
    dialog.dataset.photoId = state.id;
    state.preview = (root.BusinessRuntime?.objectUrl || URL.createObjectURL)(file);
    el('[data-preview]').src = state.preview; el('[data-preview]').hidden = false;
    el('[data-filename]').textContent = file.name;
    message(file.size > maxBytes ? 'Le fichier dépasse la limite de 20 Mio.' : 'Image sélectionnée. JPEG, PNG ou WebP, 20 Mio maximum.', file.size > maxBytes);
  }
  async function submit(event) {
    event.preventDefault(); const current = state;
    if (!current || current.busy || current.saved || current.blocked || !current.referencesReady) return;
    if (!navigator.onLine) { message('Connexion requise. Votre saisie est conservée dans ce formulaire.', true); return; }
    if (!current.edit && (!current.file || !current.file.size || current.file.size > maxBytes)) {
      message(!current.file ? 'Sélectionnez une image.' : !current.file.size ? 'Le fichier est vide.' : 'Le fichier dépasse la limite de 20 Mio.', true); return;
    }
    if (!current.unknown) {
      if (!form.reportValidity()) return;
      current.payload = Object.fromEntries(metadata.map(name => [name, field(name).value.trim() || null]));
      if (current.edit) for (const name of metadata) {
        const previous = current.edit[name] == null ? null : String(current.edit[name]).trim() || null;
        if (current.payload[name] === previous) delete current.payload[name];
      }
    }
    current.busy = true; lock(); message(current.edit ? 'Enregistrement…' : 'Préparation de l’image…');
    let finalizing = current.unknown;
    try {
      if (!current.edit && !current.unknown) {
        if (!current.preparedMedia) current.preparedMedia = await root.PhotoFile.prepare(current.file, root.PhotoQualityPolicy);
        current.digest = current.preparedMedia.sha256;
        const url = `/api/photo-uploads/${current.id}/${current.digest}?observation_id=${encodeURIComponent(current.observation.id)}`;
        if (!current.prepared) {
          try { current.prepared = await request(url); }
          catch (error) { if (error.status !== 404) throw error; }
          if (!current.prepared) current.prepared = await request(url + '&filename=' + encodeURIComponent(current.preparedMedia.originalFilename), {
            method: 'PUT', headers: { 'Content-Type': current.preparedMedia.mimeType }, body: current.preparedMedia.blob });
        }
        Object.assign(current.payload, { id: current.id, observation_id: current.observation.id,
          upload_ref: current.prepared.upload_ref, file_sha256: current.digest, file_size: current.preparedMedia.size });
      }
      finalizing = true; message('Enregistrement de la photo…');
      const photo = await request(current.edit ? `/api/photos/${current.id}` : '/api/photos', {
        method: current.edit ? 'PUT' : 'POST', body: JSON.stringify(current.payload), headers: {
          'Content-Type': 'application/json', 'Operation-Id': current.operationId,
          ...(current.edit ? { 'If-Match': `"${current.baseVersion}"` } : {}),
        },
      });
      if (photo.id !== current.id || photo.observation_id !== current.observation.id
          || !Number.isSafeInteger(photo.version) || photo.version < 1
          || !root.ResourceInvalidation.normalizeMutationPayload(photo.mutation)
          || photo.mutation.resource !== 'photos' || photo.mutation.action !== (current.edit ? 'update' : 'create')
          || photo.mutation.ids?.length !== 1 || photo.mutation.ids[0] !== current.id
          || photo.mutation.versions?.[current.id] !== photo.version) throw new Error('Reçu incohérent. Réessayez la même intention.');
      current.saved = photo; current.unknown = false; current.dirty = false;
      el('[type=submit]').hidden = true; el('[data-another]').hidden = false; el('[data-localisation]').hidden = false;
      const preview = photo.content_url || current.preview || '';
      el('[data-preview]').src = root.BusinessRuntime?.apiUrl(preview) || preview; el('[data-preview]').hidden = !photo.content_available;
      message(current.edit ? 'Photo enregistrée.' : 'Photo ajoutée.');
      // Le reçu confirme l'écriture avant tout rafraîchissement secondaire.
      try {
        current.onSaved?.(photo);
        await root.ResourceMutationController.publishMutation(photo.mutation, { applyLocally: true });
      } catch (error) { console.error('Actualisation Photo', error); message('Photo enregistrée. Actualisation des autres vues incomplète.', true); }
    } catch (error) {
      current.unknown = finalizing && (!error.status || error.status >= 500 || [408,429].includes(error.status));
      current.conflict = error.status === 409; current.forbidden = error.status === 403;
      current.blocked = current.conflict || current.forbidden;
      if (!current.unknown) {
        // Un refus certain n'a pas créé de reçu : la saisie corrigée peut être une nouvelle intention.
        current.operationId = root.ResourceEvents.createId();
        if (error.status === 404) current.prepared = null;
      }
      message((current.unknown ? 'Résultat non confirmé. Réessayez la même intention. ' : error.status === 413 ? 'Fichier trop volumineux. '
        : error.status === 409 ? (error.code === 'photo-id-collision' ? 'Collision d’UUID. ' : error.code === 'operation-id-collision' ? 'Collision d’Operation-Id. ' : 'Conflit : la saisie est conservée. ')
          : error.status === 422 ? 'Validation refusée. ' : error.status === 415 ? 'Fichier non accepté. ' : finalizing ? 'Enregistrement refusé. ' : 'Transfert non confirmé. Réessayez le même fichier. ') + error.message, true);
    } finally { current.busy = false; lock(); }
  }
  async function references(current) {
    if (!current || current.referencesLoading) return;
    current.referencesLoading = true; el('[data-references]').disabled = true;
    try {
      const [orientations,cotes] = await Promise.all([root.ReferenceData.load('orientationsPhoto',request),root.ReferenceData.load('cotes',request)]);
      if (state !== current) return;
      root.ReferenceData.fillSelect(field('orientation_photo_id'),orientations,current.edit?.orientation_photo_id,'Non renseignée');
      root.ReferenceData.fillSelect(field('cote_id'),cotes,current.edit?.cote_id,'Non renseigné');
      current.referencesReady = true; lock(); el('[data-references]').hidden = true;
    } catch (error) { if (state === current) { message('Choix indisponibles : ' + error.message,true); el('[data-references]').hidden = false; } }
    finally { current.referencesLoading = false; if (state === current) el('[data-references]').disabled = false; }
  }
  function initialize() {
    dialog = document.createElement('dialog'); dialog.id = 'photo-editor-dialog'; dialog.className = 'photo-editor-dialog';
    dialog.setAttribute('aria-labelledby','photo-editor-title');
    formView = root.EntityFormView.create({root, document, titleId:"photo-editor-title", descriptor:root.PhotoForm.composition("metadata"),
      id:"photo-editor-view", onClose:close});
    dialog.replaceChildren(formView.element);

    formView.setFormId("photo-editor-form");
    formView.controls.get('parent_label').setAttribute('data-context','');
    for(const key of ['preview','filename']) formView.controls.get(key).setAttribute(`data-${key}`,'');
    formView.controls.get('preview').hidden=true;
    formView.cancel.setAttribute('data-close','');
    document.body.append(dialog); form = el('form');
    form.addEventListener('submit',submit);
    form.addEventListener('input',() => { if (state && !state.busy && !state.unknown) state.dirty = true; });
    field('file').addEventListener('change',event => chosen(event.target));
    field('camera').addEventListener('change',event => chosen(event.target));
    el('[data-close]').onclick = close;
    el('[data-prepare-local]').onclick = () => {
      const current = state;
      if (!current || current.edit || current.saved || current.busy || current.unknown || current.blocked) return;
      if (field('orientation_photo_id').value || field('cote_id').value || field('photographe_id').value) {
        message('Orientation, côté et photographe ne sont pas conservés dans la préparation locale.', true); return;
      }
      const initial = Object.fromEntries(['date', 'libelle', 'designation'].map(name => [name, field(name).value]));
      Object.assign(initial, { id: current.id, file: current.file });
      if (root.PhotoOffline.open(current.observation, null, initial)) {
        if (current.preview) (root.BusinessRuntime?.revokeObjectUrl || URL.revokeObjectURL)(current.preview);
        state = null; dialog.close();
      }
    };
    el('[data-localisation]').onclick = () => {
      const current=state;
      root.PhotoLocalisation.open(current.saved || current.edit,current.observation,photo=>{
        current.saved=photo; current.onSaved?.(photo);
        if(current.edit) { current.edit=photo; current.baseVersion=photo.version; }
      });
    };
    el('[data-another]').onclick = () => { const {observation,onSaved} = state; close(); open(observation,null,onSaved); };
    el('[data-references]').onclick = () => references(state);
    dialog.addEventListener('cancel',event => { event.preventDefault(); close(); });
    root.addEventListener('beforeunload',event => { if (state && (state.dirty || state.busy || state.unknown)) { event.preventDefault(); event.returnValue = ''; } });
    root.ResourceMutationController.registerInvalidator('open-detail', mutation => {
      if (state?.edit && !state.saved && mutation.resource === 'photos' && (mutation.ids === null || mutation.ids.includes(state.id))) {
        message('Cette photo a changé sur le serveur. Votre saisie et sa version de départ sont conservées.',true);
      }
    });
  }
  function open(observation, photo = null, onSaved = null) {
    if (root.BusinessRuntime && !root.BusinessRuntime.can(photo ? 'business.update' : 'business.create')) return false;
    if (!observation?.id || (photo && photo.observation_id !== observation.id)) throw new Error('Rattachement Photo incohérent.');
    if (!dialog) initialize(); if (dialog.open) return false;
    state = { observation: { ...observation }, edit: photo, id: photo?.id || root.ResourceEvents.createId(),
      operationId: root.ResourceEvents.createId(), baseVersion: photo?.version, onSaved, referencesReady:false };
    dialog.dataset.photoId = state.id;
    el('[data-localisation]').hidden = true;
    form.reset(); form.hidden = false; el('[type=submit]').hidden = false; el('[data-another]').hidden = true; el('[data-references]').hidden = true;
    el('[data-group="file-fields"]').hidden = Boolean(photo);
    el('[data-prepare-local]').hidden = Boolean(photo);
    el('[data-context]').textContent = `${observation.designation || observation.id} — ${observation.id}`;
    const now = new Date();
    for (const name of metadata) field(name).value = photo?.[name] || '';
    if (!photo) field('date').value = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    const preview = photo?.content_url || '';
    el('[data-preview]').hidden = !photo?.content_available; el('[data-preview]').src = root.BusinessRuntime?.apiUrl(preview) || preview;
    el('[data-filename]').textContent = photo?.nom_fichier || '';
    formView.setFieldNotice("filename", photo ? contentNote(photo) : "");
    message(''); lock();
    formView.setSubject({ objectName: photo?.libelle || photo?.designation });
    dialog.showModal(); void references(state); return true;
  }
  function contentNote(photo) {
    if (photo.content_available) return "";
    switch (photo.content_state) {
      case "historical_no_path": return "Photo sans fichier associé — chemin non renseigné dans SIRS";
      case "historical_pending": return "Fichier non encore transféré. Disponibilité de l’original à vérifier.";
      case "historical_reference_unavailable": return "Référence du fichier historique à vérifier.";
      case "media_unavailable": return "Fichier associé momentanément indisponible.";
      default: return "Photo sans fichier associé.";
    }
  }
  function renderGallery(container, observation) {
    container.replaceChildren();
    const title = document.createElement('h3'); title.textContent = `Photos (${observation.photos?.length || 0})`;
    const add = document.createElement('button'); add.type='button'; add.textContent='+ Ajouter une photo'; add.className='photo-add wide-button secondary';
    const update = photo => { observation.photos = [...(observation.photos || []).filter(p => p.id !== photo.id),photo]; renderGallery(container,observation); };
    add.onclick = () => open(observation,null,update); container.append(title);
    add.dataset.requires = 'business.create';
    if (observation.version !== null) container.append(add);
    for (const photo of observation.photos || []) {
      const card=document.createElement('article'); card.className='photo-gallery-item';
      const title=document.createElement('p'); title.textContent=photo.libelle || photo.designation || photo.nom_fichier || 'Photo'; card.append(title);
      if (photo.content_available) {
        const contentUrl = photo.variants?.thumbnail?.content_url || photo.content_url;
        const image=document.createElement('img'); image.src=root.BusinessRuntime?.apiUrl(contentUrl) || contentUrl;
        image.loading='lazy'; image.alt=title.textContent; card.append(image);
        const view=document.createElement('button'); view.type='button'; view.className='wide-button secondary'; view.textContent='Voir la photo';
        view.onclick=() => { const viewer=document.createElement('dialog'); viewer.className='photo-viewer';
          const full=document.createElement('img'); full.src=root.BusinessRuntime?.apiUrl(photo.content_url) || photo.content_url; full.alt=title.textContent;
          const close=document.createElement('button'); close.textContent='Fermer'; close.onclick=()=>viewer.close();
          viewer.append(full,close); viewer.addEventListener('close',()=>viewer.remove()); document.body.append(viewer); viewer.showModal(); };
        card.append(view);
      } else {
        const note = document.createElement('p'); note.className = 'field-help';
        note.textContent = contentNote(photo); card.append(note);
      }
      const edit=document.createElement('button'); edit.type='button'; edit.className='wide-button secondary'; edit.textContent='Modifier les métadonnées'; edit.onclick=()=>open(observation,photo,update);
      const locate=document.createElement('button'); locate.type='button'; locate.className='wide-button secondary'; locate.textContent='Localisation du photographe';
      locate.onclick=()=>root.PhotoLocalisation.open(photo,observation,update);
      edit.dataset.requires = 'business.update'; locate.dataset.requires = 'business.update';
      card.append(edit,locate); container.append(card);
    }
    if (root.PhotoOffline) void root.PhotoOffline.mount(container, observation).catch(error => {
      const notice = document.createElement('p'); notice.className = 'field-help';
      notice.textContent = 'Photos locales indisponibles : ' + error.message; container.append(notice);
    });
  }
  root.PhotoMedia = { openCreate: (observation,onSaved) => open(observation,null,onSaved), openEdit: (photo,observation,onSaved) => open(observation,photo,onSaved), renderGallery, contentNote };
})(globalThis);
