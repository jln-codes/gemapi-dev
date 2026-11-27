/* Position du photographe, indépendante du parent. Commande connectée atomique. */
(function (root) {
  'use strict';
  let dialog, form, state, formView, leafletLoading;
  const el = selector => dialog.querySelector(selector);
  const field = name => form.elements.namedItem(name);
  function message(text, error=false) { el('[role=status]').textContent=text; el('[role=status]').classList.toggle('error',error); }
  function positionLabel(position, distance) {
    if(distance===0) return 'Sur la borne';
    return {AVANT_BORNE:'Amont',SUR_BORNE:'Sur la borne',APRES_BORNE:'Aval'}[position] || position || '—';
  }
  async function request(url, options={}) {
    await root.ResourceEventTransport?.bootstrap;
    const headers=options.method ? await root.ResourceMutationController.requestHeaders() : {};
    const response=await (globalThis.BusinessRuntime?.fetch || fetch)(url,{...options,headers:{...headers,...options.headers},signal:AbortSignal.timeout(30000)});
    let result; try { result=await response.json(); } catch (_) { /* retry strict si réponse perdue */ }
    if (!response.ok || !result) {
      const detail=result?.detail;
      const error=new Error(typeof detail==='string' ? detail : Array.isArray(detail) ? detail.map(e=>e.msg).join(' · ') : detail?.message || 'Réponse non confirmée.');
      error.status=response.ok ? 502 : response.status; throw error;
    }
    return result;
  }
  function lock() {
    formView.sync({mode:state?.saved?'read':'edit',objectId:state?.id,pending:Boolean(state?.busy),uncertain:Boolean(state?.unknown),retryUncertain:true,dirty:Boolean(state?.dirty),conflict:Boolean(state?.conflict),forbidden:Boolean(state?.forbidden)});
    const locked=!state.ready || state.busy || state.unknown || state.saved || state.loading || state.map;
    // The map is now inside the common Geometry section. Freeze the declared
    // fields while drawing, leaving its confirmation/cancellation tools usable.
    el('fieldset').disabled=Boolean(!state.ready || state.busy || state.unknown || state.saved
      || state.loading || state.blocked || formView.save.hidden);
    for(const control of formView.controls.values())control.disabled=false;
    modes();
    if(state.map)for(const control of formView.controls.values())control.disabled=true;
    el('[type=submit]').disabled=Boolean(locked && !state.unknown || state.busy || state.blocked);
    el('[type=submit]').textContent=state.unknown ? 'Vérifier / réessayer'
      : state.offline ? 'Enregistrer localement' : 'Enregistrer la localisation';
    el('[data-close]').disabled=Boolean(state.busy || state.unknown);
  }
  function modes() {
    const troncon = field('troncon_id').value;
    const compatible = Boolean(troncon && state?.systemsTroncon === troncon
      && state.systems.some(system => system.bornes?.length));
    formView.geometry.setModeAvailability({ bornage: compatible, pr: compatible }, { locked: Boolean(state?.map) });
    const mode=field('mode').value;
    el('[data-group="coordinates"]').hidden=mode!=='point'; el('[data-group="reference"]').hidden=mode==='effacer';
    el('[data-group="bornage"]').hidden=mode!=='bornage'; el('[data-group="pr"]').hidden=mode!=='pr';
    el('[data-clear]').hidden=mode!=='effacer';
    for(const section of ['[data-group="coordinates"]','[data-group="reference"]','[data-group="bornage"]','[data-group="pr"]'])
      el(section).querySelectorAll('input,select,button').forEach(input=>{input.disabled=el(section).hidden;});
    el('[data-axis]').textContent=field('srid').value!=='4326' ? `X / Y — EPSG:${field('srid').value}, en mètres` : 'Longitude / latitude — EPSG:4326, en degrés';
  }
  function setCRS(crs) {
    root.LocalProjection.validateBusinessCRS(crs);
    state.crs=crs;
    field('srid').replaceChildren(new Option(crs.code,String(crs.srid)),new Option('EPSG:4326','4326'));
  }
  function fill(select, rows, selected, empty) {
    select.replaceChildren(new Option(empty,''));
    for (const row of rows) select.add(new Option(row.designation || row.libelle || row.id,row.id));
    select.value=selected || '';
  }
  function bornes(selected) {
    fill(field('borne_id'),state.systems?.find(s=>s.id===field('systeme_reperage_id').value)?.bornes || [],selected,'Choisir une borne');
  }
  async function systems(current, selected, borne) {
    const generation=++current.generation, troncon=field('troncon_id').value;
    current.loading=true; lock();
    try {
      const selectedPinned=current.localOperation?.referenceRevision
        ? current.references?.pinned?.find(system=>system.id===current.localOperation.changes.systeme_reperage_id
          && system.revision===current.localOperation.referenceRevision) : null;
      const available=current.offline ? new Map((current.references?.systems || []).filter(system=>
        system.troncon_id===troncon && system.valid && system.bornes.length
        && system.bornes.every(b=>b.valid && b.link_valid)).map(system=>[system.id,system])) : null;
      if(selectedPinned?.troncon_id===troncon)available.set(selectedPinned.id,selectedPinned);
      const options=current.offline
        ? {systemes:[...available.values()].map(system=>({id:system.id,designation:system.designation,
            bornes:system.bornes.map(b=>({id:b.id,designation:b.designation,pr:Number(b.pr)}))}))}
        : troncon ? await request(`/api/photos/${current.id}/localisation-options?troncon_id=${encodeURIComponent(troncon)}`) : {systemes:[]};
      if(state!==current || generation!==current.generation) return;
      current.systems=options.systemes;
      current.systemsTroncon = troncon;
      fill(field('systeme_reperage_id'),options.systemes,selected,'Sans système / choisir explicitement'); bornes(borne);
    } catch(error) { if(state===current) { current.systems=[]; fill(field('systeme_reperage_id'),[],null,'Choix indisponibles'); bornes(); message(error.message,true); } }
    finally { if(state===current && generation===current.generation) { current.loading=false; lock(); } }
  }
  function stopMap() {
    if(state.map) state.map.remove(); state.map=null; state.marker=null; state.mapPoint=null;
    el('[data-map-editor]').hidden=true; lock();
  }
  function ensureLeaflet() {
    if(root.L) return Promise.resolve();
    if(!leafletLoading) leafletLoading=new Promise((resolve,reject)=>{
      const style=document.createElement('link'); style.rel='stylesheet'; style.href='/static/vendor/leaflet/leaflet.css';
      const script=document.createElement('script'); script.src='/static/vendor/leaflet/leaflet.js';
      script.onload=()=>resolve(); script.onerror=()=>{script.remove();style.remove();leafletLoading=null;reject(new Error('Carte indisponible. Vous pouvez saisir des coordonnées.'));};
      document.head.append(style,script);
    });
    return leafletLoading;
  }
  async function startMap() {
    const current=state; current.loading=true; lock();
    try { await ensureLeaflet(); }
    catch(error) { if(state===current)message(error.message,true); return; }
    finally { if(state===current){current.loading=false;lock();} }
    if(state!==current)return;
    el('[data-map-editor]').hidden=false;
    const map=root.L.map(el('[data-map]')).setView([46.5,2.5],6); state.map=map;
    if(current.offline) {
      const features=current.preparedTroncons.map(row=>row.payload).filter(feature=>feature?.geometry);
      if(features.length) {
        const axes=root.L.geoJSON({type:'FeatureCollection',features},{style:{color:'#39817f',weight:3}}).addTo(map);
        if(axes.getBounds().isValid())map.fitBounds(axes.getBounds(),{maxZoom:16,padding:[20,20]});
      }
    } else root.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
    function point(latlng) {
      state.mapPoint=latlng;
      if(state.marker) state.marker.setLatLng(latlng);
      else { state.marker=root.L.marker(latlng,{draggable:true}).addTo(map); state.marker.on('dragend',()=>{state.mapPoint=state.marker.getLatLng();}); }
      el('[data-map-accept]').disabled=false;
    }
    // A saved photographer geometry is a visual reference only. No parent point is copied.
    const saved=state.photo.localisation?.geometry_4326;
    if(saved) { const layer=root.L.geoJSON(saved,{style:{color:'#66736e',dashArray:'4 4'},pointToLayer:(_,latlng)=>root.L.circleMarker(latlng,{radius:7,color:'#66736e',fillOpacity:.25,interactive:false})}).addTo(map); if(layer.getBounds().isValid()) map.fitBounds(layer.getBounds(),{maxZoom:17,padding:[20,20]}); }
    if(field('srid').value==='4326' && field('x').value!=='' && field('y').value!=='') {
      const x=Number(field('x').value),y=Number(field('y').value);
      if(Number.isFinite(x) && Number.isFinite(y) && Math.abs(x)<=180 && Math.abs(y)<=90) { point(root.L.latLng(y,x)); map.setView([y,x],17); }
    }
    el('[data-map-accept]').disabled=!state.mapPoint;
    map.on('click',event=>point(event.latlng)); lock(); setTimeout(()=>map.invalidateSize(),0);
    el('[data-map-editor]').scrollIntoView({block:'nearest'});
  }
  function command() {
    const mode=field('mode').value, result={mode};
    if (state.photo?.localisation?.remplacement_requis && !field('remplacer_historique').checked) {
      throw new Error('Confirmez le remplacement de la localisation historique avant d’enregistrer.');
    }
    if(field('remplacer_historique').checked) result.remplacer_historique=true;
    if(mode==='point') {
      if(!field('x').value.trim() || !field('y').value.trim()) throw new Error('Renseignez les deux coordonnées.');
      result.srid=Number(field('srid').value); result.coordinates=[Number(field('x').value),Number(field('y').value)];
      if(!result.coordinates.every(Number.isFinite)) throw new Error('Coordonnées non finies.');
    }
    if(mode!=='effacer' && (field('systeme_reperage_id').value || mode!=='point')) {
      if (!formView.geometry.hasMode(mode) || !field('systeme_reperage_id').value) {
        throw new Error('Choisissez un tronçon de travail compatible et son système.');
      }
      result.troncon_id=field('troncon_id').value; result.systeme_reperage_id=field('systeme_reperage_id').value;
    }
    if(mode==='bornage') {
      if(!field('borne_id').value || field('distance_m').value==='') throw new Error('Choisissez une borne et une distance.');
      result.borne_id=field('borne_id').value; result.distance_m=Number(field('distance_m').value); result.position_relative=field('position_relative').value;
    }
    if(mode==='pr') { if(field('pr').value==='') throw new Error('Renseignez le PR.'); result.pr=Number(field('pr').value); }
    return result;
  }
  async function submit(event) {
    event.preventDefault(); const current=state;
    if(!current?.ready || current.busy || current.saved || current.blocked || current.map || current.loading) return;
    if(current.offline) {
      if(!form.reportValidity())return;
      let payload;
      try { payload=command(); } catch(error) { message(error.message,true);return; }
      const referenceRevision=payload.systeme_reperage_id
        ? current.references?.systems.find(system=>system.id===payload.systeme_reperage_id
          && system.troncon_id===payload.troncon_id)?.revision : null;
      if(payload.systeme_reperage_id && !referenceRevision){message('Système préparé indisponible. Repréparez le périmètre.',true);return;}
      current.busy=true;lock();
      try {
        const store=root.OfflineRuntime.store;
        const intent=current.localOperation
          ? await store.editPhotoLocalisation(current.localOperation.operationId,payload,referenceRevision)
          : await store.enqueuePhotoLocalisation({operationId:current.operationId,photoId:current.id,
            changes:payload,referenceRevision,baseVersion:current.baseVersion});
        if(state!==current)return;
        current.localOperation=intent;current.operationId=intent.operationId;current.saved=true;current.dirty=false;
        message('Localisation enregistrée sur cet appareil — synchronisation en attente.');
        try { await current.onSaved?.(intent); } catch(error) { message('Localisation enregistrée localement. Affichage à actualiser : '+error.message,true); }
        void root.OfflineOperationsRuntime?.synchronize().catch(()=>{});
      } catch(error) { if(state===current)message('Brouillon conservé : '+error.message,true); }
      finally { current.busy=false;if(state===current)lock(); }
      return;
    }
    if(!navigator.onLine) { message('Connexion requise. Brouillon conservé dans ce formulaire.',true); return; }
    try { if(!current.unknown) { if(!form.reportValidity()) return; current.payload=command(); } }
    catch(error) { message(error.message,true); return; }
    current.busy=true; lock(); message('Enregistrement…');
    try {
      const photo=await request(`/api/photos/${current.id}/localisation`,{method:'PUT',headers:{'Content-Type':'application/json',
        'If-Match':`"${current.photo.version}"`,'Operation-Id':current.operationId},body:JSON.stringify(current.payload)});
      if(photo.id!==current.id || photo.observation_id!==current.observation.id || !Number.isSafeInteger(photo.version)
        || !root.ResourceInvalidation.normalizeMutationPayload(photo.mutation) || photo.mutation.resource!=='photos'
        || photo.mutation.action!=='update' || photo.mutation.ids?.length!==1 || photo.mutation.ids[0]!==current.id
        || photo.mutation.versions?.[current.id]!==photo.version) throw new Error('Reçu incohérent ; réessayez la même intention.');
      current.saved=true; current.unknown=false; current.dirty=false; current.photo=photo; describe(photo.localisation);
      message('Localisation enregistrée.');
      try { current.onSaved?.(photo); await root.ResourceMutationController.publishMutation(photo.mutation,{applyLocally:true}); }
      catch(error) { console.error(error); message('Localisation enregistrée. Actualisation des autres vues incomplète.',true); }
    } catch(error) {
      current.unknown=!error.status || error.status>=500 || [408,429].includes(error.status); current.conflict=error.status===409;current.forbidden=error.status===403;current.blocked=current.conflict || current.forbidden;
      if(!current.unknown) current.operationId=root.ResourceEvents.createId();
      message((current.unknown ? 'Résultat non confirmé : réessayez la même intention. ' : current.blocked ? 'Conflit : brouillon et version de départ conservés. ' : 'Localisation refusée. ')+error.message,true);
    } finally { current.busy=false; lock(); }
  }
  function describe(loc) {
    const lines=[loc?.geometry ? `Géométrie : ${loc.geometry.type} (EPSG:${loc.srid})` : 'Aucune géométrie enregistrée.',
      `Système : ${loc?.systeme_reperage_libelle || loc?.systeme_reperage_id || 'non renseigné'}`];
    for(const end of ['debut','fin']) if(loc) {
      const label=positionLabel(loc[`position_${end}_relative`],loc[`borne_${end}_distance`]);
      lines.push(`${end==='debut'?'Début':'Fin'} : borne ${loc[`borne_${end}_libelle`] || loc[`borne_${end}_id`] || '—'} · ${label} · distance ${loc[`borne_${end}_distance`] ?? '—'} m · PR ${loc[`pr_${end}`] ?? '—'}`);
    }
    el('[data-summary]').textContent=lines.join('\n');
    el('[data-historical]').textContent=JSON.stringify(loc || {},null,2);
    el('[data-warnings]').textContent=loc?.warnings?.length ? 'Anomalies historiques (non corrigées) : '+loc.warnings.join(', ') : '';
    el('[data-group="replacement"]').hidden=!loc?.remplacement_requis;
  }
  function describeLocalDraft(command, references, revision) {
    if(!command)return;
    const system=[...(references?.systems || []),...(references?.pinned || [])]
      .find(item=>item.id===command.systeme_reperage_id && item.revision===revision);
    const borne=system?.bornes.find(item=>item.id===command.borne_id);
    const position=positionLabel(command.position_relative,command.distance_m);
    const detail=command.mode==='point' ? `Point ${command.coordinates.join(' / ')} — EPSG:${command.srid}`
      : command.mode==='bornage' ? `${system?.designation || 'Système inconnu'} · ${borne?.designation || 'Borne inconnue'} · ${position} · ${command.distance_m} m`
        : command.mode==='pr' ? `${system?.designation || 'Système inconnu'} · PR ${command.pr}`
          : 'Effacement demandé';
    el('[data-summary]').textContent+='\nLocalisation enregistrée sur cet appareil : '+detail;
  }
  function close() {
    if(state?.busy || state?.unknown) return;
    if(state?.dirty && !state.saved && !root.confirm('Abandonner le brouillon de localisation ?')) return;
    if(state?.map) stopMap(); state=null; dialog.close();
  }
  function initialize() {
    dialog=document.createElement('dialog'); dialog.id='photo-localisation-dialog'; dialog.className='photo-editor-dialog';
    dialog.setAttribute('aria-labelledby','photo-localisation-title');
    formView=root.EntityFormView.create({root,document,titleId:"photo-localisation-title", descriptor:root.PhotoForm.composition('localisation'),id:'photo-localisation-view',onClose:close});
    dialog.replaceChildren(formView.element);
    formView.controls.get('photo_label').setAttribute('data-photo','');formView.controls.get('parent_label').setAttribute('data-context','');
    formView.controls.get('reload').setAttribute('data-reload','');
    formView.cancel.setAttribute('data-close','');
    const geometry = formView.geometry;
    const drawing = geometry.operations.get('mini-map');
    drawing.start.dataset.mapStart = '';
    drawing.panel.dataset.mapEditor = '';
    drawing.surface.dataset.map = '';
    drawing.confirm.dataset.mapAccept = '';
    drawing.cancel.dataset.mapCancel = '';
    drawing.status.textContent = 'Cliquez sur la carte puis ajustez le marqueur avant de confirmer.';
    formView.groups.get('coordinates').append(drawing.tools, drawing.panel);
    geometry.details.innerHTML = `<p data-summary></p><p data-warnings></p>
      <details><summary>Valeurs historiques et géométries</summary><pre data-historical></pre></details>`;
    const axis = document.createElement('p');
    axis.className = 'entity-form__help';
    axis.dataset.axis = '';
    formView.groups.get('coordinates').prepend(axis);
    const clear = document.createElement('p');
    clear.className = 'entity-form__help entity-form__warning';
    clear.dataset.clear = '';
    clear.hidden = true;
    clear.textContent = 'L’enregistrement effacera le repérage de cette photo. Son fichier sera conservé.';
    geometry.component.append(clear);
    document.body.append(dialog); form=el('form');
    form.addEventListener('submit',submit); form.addEventListener('input',()=>{if(state?.ready) state.dirty=true;});
    geometry.modeSelector.addEventListener('change', modes); field('srid').onchange=()=>{field('x').value='';field('y').value='';modes();};
    field('troncon_id').onchange = () => {
      const mode = field('mode').value;
      state.systems = [];
      state.systemsTroncon = null;
      for (const name of ['systeme_reperage_id', 'borne_id', 'pr']) field(name).value = '';
      root.EntityFormView.setMetreDistance(field('distance_m'), '');
      field('position_relative').value = 'SUR_BORNE';
      formView.geometry.diagnostics.textContent = ['bornage', 'pr'].includes(mode)
        ? 'Le repérage saisi concernait le tronçon précédent. Choisissez les références du nouveau tronçon.' : '';
      void systems(state);
    };
    field('systeme_reperage_id').onchange=()=>bornes();
    el('[data-reload]').onclick=()=>systems(state,field('systeme_reperage_id').value,field('borne_id').value);
    el('[data-map-start]').onclick=startMap; el('[data-map-cancel]').onclick=stopMap;
    el('[data-map-accept]').onclick=()=>{const point=state.mapPoint;if(!point)return;field('srid').value='4326';field('x').value=point.lng;field('y').value=point.lat;state.dirty=true;stopMap();modes();};
    el('[data-close]').onclick=close; dialog.addEventListener('cancel',event=>{event.preventDefault();if(state?.map)stopMap();else close();});
    root.addEventListener('beforeunload',event=>{if(state && (state.dirty || state.busy || state.unknown)){event.preventDefault();event.returnValue='';}});
    root.ResourceMutationController.registerInvalidator('open-detail',mutation=>{
      if(state?.ready && !state.saved && mutation.resource==='troncons'
          && (mutation.ids===null || mutation.ids.includes(field('troncon_id').value)))
        message('Références de repérage modifiées. Votre saisie et sa révision de départ sont conservées ; vérifiez les références avant de réessayer.',true);
      if(state?.ready && !state.saved && mutation.resource==='photos' && (mutation.ids===null || mutation.ids.includes(state.id)))
        message('Photo modifiée sur le serveur. Votre brouillon et sa version de départ sont conservés.',true);
    });
  }
  async function openOffline(photo, observation, onSaved, operation=null) {
    if(!photo?.id || photo.observation_id!==observation?.id)throw new Error('Rattachement Photo incohérent.');
    if(!dialog)initialize();if(dialog.open)return false;
    const current={id:photo.id,photo:{...photo},observation:{...observation},onSaved,
      baseVersion:operation ? operation.baseVersion : (photo.version ?? null),
      localOperation:operation,generation:0,operationId:operation?.operationId || root.ResourceEvents.createId(),offline:true};
    state=current;form.reset();formView.geometry.diagnostics.textContent='';el('[data-photo]').textContent=photo.libelle || photo.designation || photo.id;
    el('[data-context]').textContent=observation.designation || observation.id;
    describe(photo.localisation || null);modes();lock();message('Chargement du repérage préparé…');dialog.showModal();
    try {
      const [{records,reperageReferences},confirmed]=await Promise.all([
        root.OfflineRuntime.store.inspect(),root.OfflineRuntime.store.confirmedPhotos(observation.id)]);
      if(state!==current)return false;
      if(!operation && !confirmed.some(item=>item.photo.id===photo.id)
          && !(await root.OfflineRuntime.store.listOperations()).some(item=>item.resource==='photos'
            && item.action==='create' && item.resourceId===photo.id)) throw new Error('Photo locale non préparée.');
      current.references=reperageReferences;
      setCRS(reperageReferences?.crs);
      describeLocalDraft(operation?.changes,reperageReferences,operation?.referenceRevision);
      current.preparedTroncons=records.filter(row=>row.resource==='troncons' && !row.detached);
      const list=current.preparedTroncons.map(row=>({id:row.id,
        designation:row.payload.properties?.libelle || row.id}));
      const loc=photo.localisation;
      const saved=operation?.changes;
      let proposals=[];
      if(observation.parent?.resource==='troncons')proposals=[observation.parent.id];
      if(observation.parent?.resource==='desordres'){
        const parent=records.find(row=>row.resource==='desordres' && row.id===observation.parent.id);
        proposals=parent?.payload?.properties?.troncon_ids || [];
      }
      const selected=saved?.troncon_id || loc?.troncon_id || '';
      fill(field('troncon_id'),list,selected,proposals.length>1 ? 'Plusieurs tronçons : choisir' : 'Choisir un tronçon si nécessaire');
      field('srid').value=String(saved?.srid || current.crs.srid);
      const point=saved?.mode==='point' ? saved.coordinates
        : loc?.geometry?.type==='Point' ? loc.geometry.coordinates : null;
      if(point){field('x').value=point[0];field('y').value=point[1];}
      root.EntityFormView.setMetreDistance(field('distance_m'), saved?.distance_m ?? loc?.borne_debut_distance ?? '');
      field('pr').value=saved?.pr ?? loc?.pr_debut ?? '';
      field('position_relative').value=saved?.position_relative || loc?.position_debut_relative || 'SUR_BORNE';
      field('remplacer_historique').checked=!!saved?.remplacer_historique;
      await systems(current,saved?.systeme_reperage_id || loc?.systeme_reperage_id,
        saved?.borne_id || loc?.borne_debut_id);
      if(state!==current)return false;
      const currentReference=operation?.referenceRevision && reperageReferences?.systems?.find(system=>
        system.id===operation.changes.systeme_reperage_id);
      const referencesChanged=!!currentReference && currentReference.revision!==operation.referenceRevision;
      const missingReferences = ['bornage', 'pr'].includes(saved?.mode)
        && !formView.geometry.hasMode(saved.mode);
      current.blocked=!!operation && (operation.attempts>0 || referencesChanged || missingReferences
        || ['conflict','failed','confirmed'].includes(operation.status));
      field('mode').value = saved?.mode || 'point';
      current.ready=true;modes();lock();
      message(missingReferences ? 'Références indisponibles : le repérage local est conservé pour revue.'
        : referencesChanged ? 'Références modifiées : localisation locale conservée pour revue.'
        : current.blocked ? 'Intention déjà tentée : brouillon et version conservés pour revue.'
        : operation ? 'Localisation locale modifiable avant le premier envoi.'
          : '');
      return true;
    } catch(error) {if(state===current)message('Lecture locale impossible : '+error.message,true);return false;}
  }
  async function open(photo,observation,onSaved) {
    if(!photo?.id || photo.observation_id!==observation?.id) throw new Error('Rattachement Photo incohérent.');
    if(!dialog)initialize();if(dialog.open)return;
    const current={id:photo.id,observation:{...observation},onSaved,generation:0,operationId:root.ResourceEvents.createId()};state=current;
    form.reset(); formView.geometry.diagnostics.textContent=''; el('[data-photo]').textContent=photo.libelle || photo.designation || photo.id;
    el('[data-context]').textContent=observation.designation || observation.id; describe(null); modes();lock();message('Chargement…');dialog.showModal();
    try {
      const [full,options,all]=await Promise.all([request(`/api/photos/${photo.id}`),request(`/api/photos/${photo.id}/localisation-options`),request('/api/troncons/options')]);
      if(state!==current)return;
      if(full.id!==photo.id || full.observation_id!==observation.id || !Number.isSafeInteger(full.version))throw new Error('Réponse Photo incohérente.');
      current.photo=full; const loc=full.localisation; setCRS(loc?.crs); describe(loc); modes();
      const proposals=options.context_troncons || [], list=new Map([...(all.troncons||[]),...proposals].map(t=>[t.id,t]));
      const selected=loc?.troncon_id || '';
      if(selected && !list.has(selected))list.set(selected,{id:selected,designation:selected});
      fill(field('troncon_id'),[...list.values()],selected,proposals.length>1 ? 'Plusieurs tronçons possibles : choisir' : 'Choisir un tronçon (facultatif pour un point)');
      if(loc?.geometry?.type==='Point') {field('x').value=loc.geometry.coordinates[0];field('y').value=loc.geometry.coordinates[1];}
      root.EntityFormView.setMetreDistance(field('distance_m'), loc?.borne_debut_distance ?? '');field('pr').value=loc?.pr_debut ?? '';
      field('position_relative').value=loc?.position_debut_relative || 'SUR_BORNE';
      await systems(current,loc?.systeme_reperage_id,loc?.borne_debut_id);
      if(state!==current)return;current.ready=true;lock();message('');
    } catch(error) {
      if(state!==current)return;
      if((error instanceof TypeError || error?.name==='TimeoutError') && root.OfflineRuntime?.store) {
        try {
          const saved=(await root.OfflineRuntime.store.confirmedPhotos(observation.id))
            .find(item=>item.photo.id===photo.id)?.photo;
          if(saved) {
            const pending=(await root.OfflineRuntime.store.listOperations())
              .find(item=>item.resource==='photos' && item.action==='update-localisation' && item.resourceId===photo.id);
            dialog.close();state=null;
            await openOffline(saved,observation,onSaved,pending || null);
            return;
          }
        } catch(_) { /* retain the original network diagnostic */ }
      }
      message('Lecture impossible : '+error.message,true);
    }
  }
  root.PhotoLocalisation={open,openOffline};
})(globalThis);
