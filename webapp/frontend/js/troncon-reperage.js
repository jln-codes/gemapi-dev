/* Capacité C4 commune, connectée : aucune référence implicite n'est persistée. */
(function (root) {
  'use strict';
  const metres = value => value!=null && value!=='' && Number.isFinite(Number(value))
    ? Number(value)>0 && Number(value)<.01 ? '< 0,01' : new Intl.NumberFormat('fr-FR',{maximumFractionDigits:2}).format(Number(value)) : 'indisponible';
  function create({container,document:doc=root.document,id,fetchJson,canWrite=()=>false,
    onDraftChange=()=>{},onMutation=()=>{},ownerLabels={},physicalFeature=null}) {
    const path=`/api/troncons/${encodeURIComponent(id)}/reperage`;
    let state=null,pending=false,dirty=false,disposed=false,sequence=0,stale=false,editRevision=0,draftForm=null;
    const runtime=root.BusinessRuntime;
    const live=()=>!disposed && runtime===root.BusinessRuntime && runtime?.isActive?.()!==false;
    const writable=()=>live() && canWrite();
    const controls=[],mutationButtons=[];let fieldSequence=0;
    const fieldPrefix=`reperage-${root.crypto.randomUUID()}`;
    const node=(tag,text)=>{const n=doc.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
    const notice=node('p');notice.setAttribute('role','status');notice.className='field-help';
    function syncWritable(){
      controls.forEach(fs=>fs.disabled=!writable()||pending||Boolean(dirty&&fs.form!==draftForm));
      if(dirty)mutationButtons.forEach(b=>b.disabled=true);
    }
    const setDirty=event=>{editRevision++;dirty=true;draftForm=event.currentTarget;syncWritable();
      if(!stale&&!pending)notice.textContent='Saisie en cours : enregistrez ce formulaire ou abandonnez la saisie avant de modifier une autre référence.';
      onDraftChange();};
    const errorText=e=>typeof e?.detail==='object'?e.detail.message||e.message:e.message;
    function field(form,name,label,type='text',initial='',options=null) {
      const wrap=node('div');wrap.className='entity-form__field';
      if(type==='checkbox')wrap.classList.add('entity-form__field--checkbox');
      const title=node('label',label);
      const input=node(options?'select':type==='textarea'?'textarea':'input');input.name=name;
      input.className='entity-form__control';
      input.id=`${fieldPrefix}-${++fieldSequence}`;title.htmlFor=input.id;
      if(!options&&type!=='textarea')input.type=type;
      if(options)for(const [value,label] of options){const o=node('option',label);o.value=value;input.append(o);}
      if(type==='checkbox')input.checked=Boolean(initial);else input.value=initial??'';
      if(type==='number')input.step='any';
      if(type==='textarea')input.rows=4;
      wrap.append(title,input);form.append(wrap);return input;
    }
    function button(host,label,run,mutation=true){const b=node('button',label);b.className='entity-form__button';b.type='button';b.onclick=run;host.append(b);if(mutation)mutationButtons.push(b);return b;}
    function section(label){const s=node('details');s.append(node('summary',label));container.append(s);return s;}
    function form(host,label,build,submit,{readOnly=false,sensitive=false}={}) {
      let readSequence=0,inputRevision=0;
      const f=node('form');f.setAttribute('aria-label',label);const fs=node('fieldset');fs.append(node('legend',label));f.append(fs);host.append(f);
      build(fs);
      if(sensitive){const consent=field(fs,'confirmer','J’ai examiné les propriétaires et localisations affectés ; leurs géométries seront conservées.','checkbox');consent.required=true;}
      const save=node('button',readOnly?'Calculer':label);save.className='entity-form__button';save.type='submit';fs.append(save);
      if(!readOnly){controls.push(fs);fs.disabled=!writable()||pending;f.addEventListener('input',setDirty);f.addEventListener('change',setDirty);}
      if(readOnly)f.addEventListener('input',()=>{inputRevision++;});
      f.addEventListener('submit',async e=>{e.preventDefault();if(pending||!live()||(!readOnly&&!writable()))return;
        if(!f.reportValidity())return;
        const token=++readSequence,revision=inputRevision,basis=state.reference_revision;
        const isCurrent=()=>live() && !pending && token===readSequence && revision===inputRevision && basis===state.reference_revision && !stale;
        try{await submit(fs,isCurrent);}catch(error){if(!readOnly || isCurrent())notice.textContent=errorText(error);}});
      return f;
    }
    const rowsOptions=(rows,empty)=>[["",empty],...rows.map(r=>[r.id,`${r.libelle||r.id}${r.valid===false?' (inactif)':''}`])];
    const val=(fs,name)=>fs.elements.namedItem(name).value;
    const bool=(fs,name)=>fs.elements.namedItem(name).checked;
    function updateFields(fs,before,names) {
      const result={};
      for(const key of names){const c=fs.elements.namedItem(key);const after=c.type==='checkbox'?c.checked:c.value;
        const old=c.type==='checkbox'?before[key]===true:String(before[key]??'');
        if(after!==old)result[key]=c.type==='checkbox'?after:['date','number'].includes(c.type)?after===''?null:c.type==='number'?Number(after):after:after;}
      return result;
    }
    function coordinates(fs) {
      const text=val(fs,'coordonnees').trim();
      const points=text?text.split('\n').filter(s=>s.trim()).map(s=>s.trim().split(/[\s,;]+/).map(Number)):[];
      if(points.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v))))throw new Error('Deux coordonnées XY par ligne sont nécessaires.');
      return points;
    }
    async function mutate(body) {
      if(!live()||!state||pending||!writable())return;
      if(stale)throw new Error('Les références ont changé. Votre brouillon et sa version initiale sont conservés ; abandonnez-le explicitement avant de recharger.');
      const frozen=state;sequence++;
      dirty=true;pending=true;container.inert=true;notice.textContent='Enregistrement…';controls.forEach(c=>c.disabled=true);onDraftChange();
      try{
        const response=await fetchJson(path,{method:'PUT',headers:{'Content-Type':'application/json','If-Match':`"${frozen.version}"`},
          body:JSON.stringify({reference_revision:frozen.reference_revision,troncon_versions:frozen.troncon_versions,...body})});
        if(!live())return;
        dirty=false;draftForm=null;pending=false;state=response;
        let publicationError=null;
        try{await onMutation(response);}catch(error){publicationError=error;}
        if(live()){render();notice.textContent=publicationError
          ? 'Repérage enregistré. Actualisation des autres vues incomplète ; ne renvoyez pas cette opération.'
          : 'Repérage enregistré. Les géométries historiques ont été conservées.';}
      }catch(e){if(live()){notice.textContent=errorText(e);if(e.status===409||e.status===428)stale=true;}throw e;}
      finally{pending=false;if(live()){container.inert=false;syncWritable();onDraftChange();}}
    }
    function coordinateOptions() {
      const srid = state.crs.srid;
      return [['4326', 'Longitude / latitude — WGS84'], [String(srid), `X / Y — EPSG:${srid}`]];
    }
    function render() {
      container.replaceChildren();controls.length=0;mutationButtons.length=0;draftForm=null;
      container.append(notice);
      notice.textContent=stale?'Références modifiées ailleurs : saisie et révision initiale conservées.':'';
      const implicit=node('section');implicit.className='troncon-reperage__implicit';container.append(implicit);
      implicit.append(node('h2','Synthèse'));
      implicit.append(node('p',`${state.bornes.length} borne(s) physique(s) inventoriée(s). Axe explicite : ${state.axe.source==='EXPLICITE'?'oui':'non'}.`));
      const defaultSystem=state.systemes.find(s=>s.id===state.systeme_reperage_defaut_id);
      implicit.append(node('p',`Système par défaut : ${defaultSystem?.libelle || (state.systeme_reperage_defaut_id?'non résolu':'aucun')}.`));
      if(state.reperage_implicite_disponible){
        implicit.append(node('p',`Longueur curviligne : ${metres(state.axe.longueur_m)} m. Sens : ordre des coordonnées de l’axe.`));
        for(const rep of state.reperes_virtuels)implicit.append(node('p',`${rep.role==='DEBUT'?'Début':'Fin'} : ${metres(rep.abscisse_m)} m`));
        implicit.append(node('p','Ces extrémités sont des repères virtuels calculés, pas des bornes physiques inventoriées.'));
        form(implicit,'Localiser par abscisse',fs=>{field(fs,'abscisse','Abscisse curviligne (m)','number',0).required=true;},async (fs,isCurrent)=>{
          if(val(fs,'abscisse').trim()==='')throw new Error('Une abscisse est nécessaire.');
          const result=await fetchJson(path+'/calcul',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'abscisse',abscisse_m:Number(val(fs,'abscisse'))})});
          if(isCurrent())notice.textContent=`Position : ${result.geometry.coordinates.join(', ')} (EPSG:${result.srid}).`;
        },{readOnly:true});
      }else implicit.append(node('p','Aucun axe linéaire exploitable. Définissez explicitement un axe dans le SCR métier ; aucune forme n’est déduite d’une surface.'));
      const owners=node('section');owners.append(node('h4','Propriétaires et localisations affectés'));
      for(const owner of Object.keys(state.troncon_versions||{}))owners.append(node('p',ownerLabels[owner] || (owner===id?'Tronçon courant':'Tronçon lié')));
      const dependencyLabels={photos:'Photos',localisations_desordres:'Localisations de désordres'};
      for(const entry of state.localisations_dependantes||[])owners.append(node('p',
        `${dependencyLabels[entry.table.replace(/^public\./,'')] || 'Localisations liées'} : ${entry.ids.length}`));
      owners.append(node('p','Une modification des références ne recalcule aucune Photo ni géométrie historique.'));container.append(owners);
      const diagnostics=section('Informations techniques du repérage');diagnostics.className='troncon-diagnostics';
      if(physicalFeature){
        const p=physicalFeature.properties||{};
        for(const [key,label]of [['id','Identifiant du Tronçon'],['geometry_type','Géométrie physique'],['geometry_srid','SCR physique'],['geometry_dimensions','Dimensions physiques'],['geometry_valide','Validité physique']])
          diagnostics.append(node('p',`${label} : ${p[key]??'Non renseigné'}`));
      }
      diagnostics.append(node('p',`Longueur native de l’axe : ${state.axe.longueur_m??'indisponible'} m — EPSG:${state.axe.srid??'non renseigné'}`));
      for(const rep of state.reperes_virtuels||[])diagnostics.append(node('p',`${rep.role} : ${rep.coordinates.join(', ')} (EPSG:${state.axe.srid})`));
      if(state.axe.diagnostic)diagnostics.append(node('p',state.axe.diagnostic));
      for(const [owner,version]of Object.entries(state.troncon_versions||{}))diagnostics.append(node('p',`Tronçon ${owner} — version observée ${version}`));
      for(const entry of state.localisations_dependantes||[])diagnostics.append(node('p',`${entry.table} · ${entry.relation} : ${entry.ids.join(', ')}`));
      const explicit=section('Repérage calibré — systèmes, bornes et PR');
      const systems=state.systemes||[],bornes=new Map((state.bornes||[]).map(b=>[b.id,b]));
      explicit.append(node('p',systems.length?`${systems.length} système(s) explicitement persisté(s).`:'Aucun système explicite. Le repérage géométrique ne nécessite aucune création.'));
      form(explicit,'Choisir le système par défaut',fs=>field(fs,'defaut','Système par défaut','text',state.systeme_reperage_defaut_id,rowsOptions(systems,'Aucun défaut')),
        fs=>mutate({systeme_reperage_defaut_id:val(fs,'defaut')||null}),{sensitive:true});
      form(explicit,'Créer un système explicite',fs=>field(fs,'libelle','Libellé du nouveau système'),fs=>mutate({actions:[{action:'creer_systeme',libelle:val(fs,'libelle')}]}));
      for(const system of systems){
        const item=node('details');item.append(node('summary',`${system.libelle||system.id}${system.valid===false?' (inactif)':''}`));explicit.append(item);
        form(item,'Modifier le système',fs=>{field(fs,'libelle','Libellé', 'text',system.libelle);field(fs,'commentaire','Commentaire','textarea',system.commentaire);field(fs,'valid','Système actif','checkbox',system.valid);},
          fs=>mutate({actions:[{action:'modifier_systeme',id:system.id,...updateFields(fs,system,['libelle','commentaire','valid'])}]}),{sensitive:true});
        const links=(state.liaisons||[]).filter(l=>l.systeme_reperage_id===system.id);
        for(const link of links){
          const borne=bornes.get(link.borne_id);const row=node('details');row.append(node('summary',`${borne?.libelle||link.borne_id} — PR ${link.valeur_pr??'non renseigné'}${link.valid===false?' (occurrence inactive)':''}`));item.append(row);
          form(row,'Modifier la calibration',fs=>{field(fs,'valeur_pr','PR calibré','text',link.valeur_pr);field(fs,'ordre_borne','Ordre','number',link.ordre_borne);field(fs,'valid','Occurrence active','checkbox',link.valid);},fs=>{
            const patch=updateFields(fs,link,['valeur_pr','ordre_borne','valid']);if(patch.valeur_pr==='')patch.valeur_pr=null;
            return mutate({actions:[{action:'modifier_liaison',id:link.id,...patch}]});},{sensitive:true});
          button(row,'Retirer logiquement cette occurrence',()=>{if(writable()&&root.confirm('Désactiver cette occurrence ? Ses données et localisations historiques seront conservées.'))void mutate({actions:[{action:'retirer_liaison',id:link.id}]}).catch(()=>{});}).disabled=!writable();
        }
        form(item,'Ajouter une borne et son PR',fs=>{field(fs,'libelle','Libellé de la borne');field(fs,'coordonnees','Coordonnées XY de la borne (facultatives)');field(fs,'srid','SCR des coordonnées','text','4326',coordinateOptions());field(fs,'pr','PR calibré (facultatif)');},fs=>{
          const coords=coordinates(fs);if(coords.length>1)throw new Error('Une borne comporte au plus une position.');
          const key=root.crypto.randomUUID();return mutate({actions:[{action:'creer_borne',id:key,libelle:val(fs,'libelle'),...(coords.length?{coordinates:coords[0],srid:Number(val(fs,'srid'))}:{})},
            {action:'lier_borne',systeme_reperage_id:system.id,borne_id:key,valeur_pr:val(fs,'pr')||null}]});});
        form(item,'Lier une borne existante',fs=>{field(fs,'borne','Borne existante','text','',rowsOptions(state.bornes,'Choisir une borne'));field(fs,'pr','PR de cette occurrence');},fs=>{
          if(!val(fs,'borne'))throw new Error('Choisir une borne.');return mutate({actions:[{action:'lier_borne',systeme_reperage_id:system.id,borne_id:val(fs,'borne'),valeur_pr:val(fs,'pr')||null}]});},{sensitive:true});
        form(item,'Calculer dans ce système',fs=>field(fs,'pr','PR demandé','text',''),async (fs,isCurrent)=>{
          const result=await fetchJson(path+'/calcul',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'pr',reference:'explicite',systeme_reperage_id:system.id,pr:val(fs,'pr')})});
          if(isCurrent())notice.textContent=`Position calibrée : ${result.geometry.coordinates.join(', ')} (EPSG:${result.srid}).`;
        },{readOnly:true});
        button(item,'Retirer logiquement ce système',()=>{if(writable()&&root.confirm('Désactiver ce système et retirer son choix par défaut éventuel ? Ses données historiques restent conservées.'))void mutate({actions:[{action:'retirer_systeme',id:system.id}],...(state.systeme_reperage_defaut_id===system.id?{systeme_reperage_defaut_id:null}:{})}).catch(()=>{});}).disabled=!writable();
      }
      const inventory=section('Bornes physiques inventoriées');
      for(const borne of state.bornes||[]){
        const item=node('details');item.append(node('summary',borne.libelle||borne.id));inventory.append(item);
        form(item,'Modifier la borne partagée',fs=>{
          field(fs,'libelle','Libellé','text',borne.libelle);field(fs,'commentaire','Commentaire','textarea',borne.commentaire);
          field(fs,'fictive','Borne fictive déclarée','checkbox',borne.fictive);field(fs,'valid','Borne active','checkbox',borne.valid);
          field(fs,'date_debut','Date de début','date',borne.date_debut);field(fs,'date_fin','Date de fin','date',borne.date_fin);
          field(fs,'position_mode','Position de la borne','text','garder',[['garder','Conserver exactement'],['remplacer','Remplacer'],['effacer','Retirer']]);
          field(fs,'coordonnees','Nouvelle position XY','text','');field(fs,'srid','SCR de la nouvelle position','text',String(borne.srid||4326),coordinateOptions());
        },fs=>{
          const patch=updateFields(fs,borne,['libelle','commentaire','fictive','valid','date_debut','date_fin']);const mode=val(fs,'position_mode');
          if(mode!=='garder'){const coords=coordinates(fs);if(mode==='remplacer'&&coords.length!==1)throw new Error('Une position XY est nécessaire.');patch.coordinates=mode==='effacer'?null:coords[0];patch.srid=mode==='effacer'?null:Number(val(fs,'srid'));}
          return mutate({actions:[{action:'modifier_borne',id:borne.id,...patch}]});
        },{sensitive:true});
      }
      const axis=section('Axe explicite de repérage');
      axis.append(node('p',`Support courant : ${state.axe.source==='EXPLICITE'?'axe explicitement défini':'géométrie physique, si linéaire'}. La géométrie physique ne sera pas remplacée.`));
      form(axis,'Définir un axe explicite',fs=>{field(fs,'coordonnees','Sommets XY de l’axe — un par ligne','textarea');field(fs,'srid','SCR de l’axe','text','4326',coordinateOptions());},fs=>{
        const coords=coordinates(fs);if(coords.length<2)throw new Error('Deux sommets au minimum sont nécessaires.');return mutate({axe:{coordinates:coords,srid:Number(val(fs,'srid'))}});
      },{sensitive:true});
      button(axis,'Inverser uniquement l’axe',()=>{if(writable()&&root.confirm('Inverser le sens de l’axe sans modifier la géométrie physique ni recalculer les positions historiques ?'))void mutate({inverser_axe:true}).catch(()=>{});}).disabled=!writable()||!state.axe.exploitable;
      button(axis,'Retirer l’axe explicite',()=>{if(writable()&&root.confirm('Retirer cet axe explicite ? Une surface sans axe ne sera plus localisable par repérage.'))void mutate({axe:null}).catch(()=>{});}).disabled=!writable()||state.axe.source!=='EXPLICITE';
      button(container,'Abandonner la saisie de repérage et recharger',()=>{if(!pending&&(!dirty||root.confirm('Abandonner explicitement cette saisie de repérage ?')))void load();},false);
    }
    async function load(){if(!live())return;const token=++sequence,revision=editRevision;try{const response=await fetchJson(path);if(!live()||token!==sequence)return;
      if(editRevision!==revision||pending){stale=true;notice.textContent='Votre saisie commencée pendant le chargement est conservée, avec sa version et son empreinte initiales.';return;}
      state=response;dirty=false;stale=false;render();onDraftChange();}catch(e){if(live()){if(!state)container.replaceChildren(notice);notice.textContent=errorText(e);}}}
    return {load,hasDraft:()=>dirty,isPending:()=>pending,
      invalidate(){if(!live())return;stale=true;notice.textContent='Références modifiées ailleurs : votre saisie, version et empreinte initiales sont conservées.';},
      dispose(){disposed=true;sequence++;},getState:()=>state};
  }
  // Ordinary presentation uses the existing READ ONLY calculation, never the
  // administration graph, and never persists virtual endpoints.
  function createSummary({container,feature,fetchJson,isCurrent=()=>true}){
    const p=feature.properties||{},id=p.id||feature.id;
    let disposed=false,sequence=0;const abort=typeof root.AbortController==='function'?new root.AbortController():null;
    const live=()=>!disposed&&isCurrent();
    async function load(){
      const token=++sequence,current=()=>live()&&token===sequence;if(!current())return;
      if(!id){container.textContent='Le repérage automatique sera disponible après l’enregistrement du tracé.';return;}
      if(p.geometry_reperage_disponible){container.textContent='Repérage spécifique conservé. Sa gestion est indépendante du tracé physique.';return;}
      if(String(p.geometry_type||feature.geometry?.type||'').toUpperCase()!=='LINESTRING'){
        container.textContent='Repérage automatique indisponible pour cette géométrie conservée.';return;
      }
      container.textContent='Repérage automatique — calcul en cours…';
      try{
        const result=await fetchJson(`/api/troncons/${encodeURIComponent(id)}/reperage/calcul`,{
          method:'POST',headers:{'Content-Type':'application/json'},signal:abort?.signal,
          body:JSON.stringify({mode:'abscisse',abscisse_m:0})});
        if(!current())return;
        container.textContent=result.version!==p.version?'Repérage actualisé ailleurs : rechargez la fiche.'
          :`Repérage automatique — 0 à ${metres(result.longueur_m)} m`;
      }catch(error){if(current())container.textContent=error.status===422
        ? 'Repérage automatique indisponible pour cette géométrie conservée.'
        : 'Repérage automatique momentanément indisponible.';}
    }
    return{load,dispose(){disposed=true;sequence++;abort?.abort();}};
  }

  let management=null,registered=null,unregister=null;
  function registerInvalidation(){
    const controller=root.ResourceMutationController;
    if(!controller||registered===controller)return;
    unregister?.();registered=controller;
    unregister=controller.registerInvalidator('open-detail',mutation=>{
      const current=management;if(!current||!['troncons','photos','desordres'].includes(mutation.resource))return;
      const state=current.instance.getState();if(!state)return;
      if(mutation.resource==='troncons'){
        const ids=mutation.ids===null?Object.keys(state.troncon_versions):mutation.ids.filter(id=>Object.hasOwn(state.troncon_versions,id));
        if(!ids.length||ids.every(id=>mutation.versions?.[id]!=null&&mutation.versions[id]<=state.troncon_versions[id]))return;
      }
      current.instance.invalidate(mutation);
    });
  }
  function closeManagement({force=false}={}){
    const current=management;if(!current)return true;
    if(!force&&(current.instance.isPending()||current.instance.hasDraft()&&!root.confirm('Abandonner la saisie de repérage ?')))return false;
    management=null;current.instance.dispose();current.dialog.close();current.dialog.remove();
    if(!force)current.onReturn?.();return true;
  }
  function openManagement({id,designation,feature,fetchJson,ownerLabels={},onMutation=()=>{},onReturn=()=>{}}){
    if(root.BusinessRuntime?.can('business.read')===false||!closeManagement())return false;
    const dialog=root.document.createElement('dialog');dialog.className='entity-form-dialog';dialog.id='troncon-reperage-dialog';
    const view=root.EntityFormView.create({id:'troncon-reperage-editor',submitAction:false,
      descriptor:{family:'troncons',context:'troncon-reperage',fields:{},children:['details']},
      onCancel:()=>closeManagement(),onClose:()=>closeManagement()});
    view.form.hidden=true;view.setSubject({objectName:designation});
    const content=view.children.get('details');content.classList.add('troncon-reperage');
    let loading=true;const runtime=root.BusinessRuntime;
    const sync=()=>view.sync({mode:runtime?.can('business.update')===false?'read':'edit',loading,
      pending:management?.instance.isPending(),dirty:management?.instance.hasDraft()});
    const instance=create({container:content,id,fetchJson,ownerLabels,physicalFeature:feature,
      canWrite:()=>management?.instance===instance&&runtime===root.BusinessRuntime&&runtime?.isActive?.()!==false&&runtime?.can('business.update')!==false,
      onDraftChange:sync,onMutation});
    management={id:String(id),instance,dialog,onReturn};
    dialog.append(view.element);root.document.body.append(dialog);sync();dialog.showModal();registerInvalidation();
    dialog.addEventListener('cancel',event=>{event.preventDefault();closeManagement();});
    void instance.load().finally(()=>{loading=false;if(management?.instance===instance)sync();});
    return true;
  }
  root.addEventListener?.('digues-app:context-closed',()=>closeManagement({force:true}));
  root.addEventListener?.('beforeunload',event=>{
    if(management?.instance.hasDraft()||management?.instance.isPending()){event.preventDefault();event.returnValue='';}
  });
  root.TronconReperage={create,createSummary,formatMetres:metres,open:openManagement,close:closeManagement,
    isOpenFor:id=>management?.id===String(id)};
})(globalThis);
