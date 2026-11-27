/* Carte: déclarations métier et raccordement des outils de localisation existants. */
(function(root){
  "use strict";
  const View=root.EntityFormView, doc=root.document;
  const id=(element,value)=>{element.id=value;return element;};
  const field=(name,label,kind,idValue,extra={})=>({name,label,kind,id:idValue,...extra});
  const general=[
    field('id','Identifiant','output','heritage-object-id',{hidden:true,readOnly:true,technical:true}),
    field('libelle','Libellé','text','heritage-object-label',{required:true}),
    field('designation','Désignation','text','heritage-object-designation'),
    field('commentaire','Commentaire','textarea','heritage-object-commentaire',{rows:3}),
    field('population_protegee','Population protégée','number','heritage-object-population',{min:'0',step:'1',contexts:['systeme']}),
    field('date_debut','Date de début','date','heritage-object-date-debut',{contexts:['troncon']}),
    field('date_fin','Date de fin','date','heritage-object-date-fin',{contexts:['troncon']}),
    field('valid','Objet valide','checkbox','heritage-object-valid')
  ];
  const heritage=View.create({root,descriptor:{family:'systemes',fields:{identification:general,
    classification:[field('classement','Classement','text','heritage-object-classement',{contexts:['systeme']}),
      field('niveau_protection','Niveau de protection','text','heritage-object-niveau',{contexts:['systeme']}),
      ...[['type_rive_id','Rive','rive'],['type_troncon_id','Type de tronçon','troncon'],['typologie_troncon_id','Typologie de tronçon','typologie-troncon']].map(([name,label,suffix])=>field(name,label,'select',`heritage-object-type-${suffix}`,{contexts:['troncon']}))],
    attachments:[field('parent','Parent','relation','heritage-parent',{required:true})]},
    observations:{resource:'troncons',read:true,create:true},
    geometry:{types:['LineString'],required:true,editable:true,controls:'draft',coordinates:true}},
    id:'heritage-create-view',onClose:()=>doc.querySelector('#close-editor').click()});
  // Stable DOM handles are consumed by the existing creation controller.
  heritage.setFormId('heritage-object-editor'); heritage.form.hidden=true;
  id(heritage.fieldGroups.get('id'),'heritage-object-id-field');
  id(heritage.fieldGroups.get('parent'),'heritage-parent-field');
  id(heritage.fieldGroups.get('parent').querySelector('span'),'heritage-parent-label');
  id(heritage.controls.get('typologie_troncon_id'),'heritage-object-typologie-troncon');
  id(heritage.geometry.block,'troncon-create-geometry'); id(heritage.geometry.draw,'start-troncon-draw');
  id(heritage.geometry.status,'troncon-draw-status'); id(heritage.geometry.operationActions,'troncon-draw-actions');
  id(heritage.geometry.cancel,'cancel-troncon-draw');
  heritage.geometry.confirm.hidden=true;
  id(heritage.status,'heritage-create-message'); id(heritage.actions,'heritage-create-actions');
  id(heritage.save,'submit-create');id(heritage.cancel,'cancel-create');
  doc.querySelector('#heritage-create-host').append(heritage.element);

  const coordinates = [
    field('xy_x', 'X', 'number', 'desordre-create-x', { numberControl: 'coordinate', group: 'xy', step: '0.01', inputMode: 'decimal' }),
    field('xy_y', 'Y', 'number', 'desordre-create-y', { numberControl: 'coordinate', group: 'xy', step: '0.01', inputMode: 'decimal' }),
    field('lonlat_lon', 'Longitude', 'number', 'desordre-create-longitude', { numberControl: 'coordinate', group: 'lonlat', step: '0.000001', inputMode: 'decimal' }),
    field('lonlat_lat', 'Latitude', 'number', 'desordre-create-latitude', { numberControl: 'coordinate', group: 'lonlat', step: '0.000001', inputMode: 'decimal' }),
    field('line_crs', 'CRS', 'select', 'desordre-create-line-crs', {
      group: 'line-coordinates', hidden: true, options: [['EPSG:4326', 'Lat/Lon EPSG:4326']],
    }),
  ];
  const bornage = [];
  const senses = [['AVANT_BORNE', 'Amont'], ['SUR_BORNE', 'Sur borne'], ['APRES_BORNE', 'Aval']];
  for (const endpoint of ['start', 'end']) {
    for (const [axis, index] of [['X', 1], ['Y', 2]]) {
      coordinates.push(field(`line_${endpoint}_${index}`, axis, 'number',
        `desordre-create-line-${endpoint}-${index}`, { numberControl: 'coordinate', group: `line-coordinates/${endpoint}`, step: 'any', inputMode: 'decimal' }));
    }
    const group = `bornage/${endpoint}`;
    bornage.push(
      field(`borne_${endpoint}`, 'Borne', 'select', `desordre-create-borne-${endpoint}`, { group }),
      field(`distance_${endpoint}`, 'Distance (m)', 'number', `desordre-create-distance-${endpoint}`, { group, numberControl: 'metres', inputMode: 'decimal' }),
      field(`sense_${endpoint}`, 'Sens', 'select', `desordre-create-sense-${endpoint}`, { group, options: senses }),
    );
  }
  const config=root.DataEntityDetail.ENTITY_CONFIGS.desordres;
  const descriptor=View.fromFields({classId:'desordres',singular:'Désordre',newLabel:'Créer un désordre',observationResource:'desordres'},config.fields,{
    classification:config.fields.filter(f=>f[2]==='select').map(f=>f[0]),
    geometry:{types:['Point','LineString','Polygon'],required:true,editable:true,controls:'draft',modes:['map','xy','lonlat','bornage'],modeName:'desordre-mode',operations:['position','vertices'],fields:[...coordinates,...bornage],
      groupLayouts:{xy:'coordinate-pair',lonlat:'coordinate-pair','line-coordinates':'endpoints',bornage:'endpoints'}},
    children:['details']});
  descriptor.fields.identification.unshift(field('id','Identifiant','output','desordre-create-id',{hidden:true,readOnly:true,technical:true}));
  descriptor.fields.attachments.push(field('troncon_ids','Tronçons concernés','relation','desordre-create-troncons',{multiple:true,size:5}));
  const controlIds={type_desordre_id:'type-reference',categorie_desordre_id:'categorie-reference',lieu_dit:'lieu-dit',date_debut:'date-debut',date_fin:'date-fin'};
  for(const fields of Object.values(descriptor.fields))for(const f of fields)if(!f.id)f.id=`desordre-create-${controlIds[f.name]||f.name.replace(/_id$/,'')}`;
  const disorder=View.create({root,descriptor,id:'disorder-form-view',
    onClose:()=>doc.querySelector('#close-editor').click(),canSelectTab:name=>root.canSelectEditorTab(name),onTabChange:name=>root.showEditorTab(name)});
  disorder.setFormId('desordre-editor');disorder.form.hidden=true;
  id(disorder.fieldGroups.get('id'),'desordre-create-id-field');
  id(disorder.tabs,'disorder-tabs');id(disorder.generalTab,'general-tab-button');id(disorder.observationsTab,'observations-tab-button');
  id(disorder.general,'general-tab');id(disorder.observations,'observations-tab');
  id(disorder.geometry.type,'desordre-create-geometry-type'); disorder.geometry.type.required=true;
  const geometry = disorder.geometry;
  id(geometry.canvas,'desordre-create-geometry');
  id(geometry.modeSelector,'desordre-mode-selector');
  for (const mode of ['xy','lonlat','bornage']) id(geometry.modeChoices.get(mode).choice,`desordre-${mode}-choice`);
  id(geometry.modeChoices.get('bornage').input,'bornage-mode');
  id(geometry.draw,'start-desordre-draw'); id(geometry.status,'desordre-draw-status');
  id(geometry.operationActions,'desordre-draw-actions'); id(geometry.cancel,'cancel-desordre-draw');
  id(geometry.confirm,'validate-desordre-draw');
  const tools = doc.querySelector('#disorder-localisation-tools').content;
  function mount(group, ...selectors) {
    const target = disorder.groups.get(group);
    target.append(...selectors.map(selector => tools.querySelector(selector)));
  }
  for (const [group, elementId] of [
    ['xy','desordre-create-xy'], ['lonlat','desordre-create-lonlat'],
    ['line-coordinates','desordre-create-line-coordinates'], ['bornage','desordre-create-bornage'],
    ['bornage/end','desordre-create-bornage-end'],
  ]) {
    const panel = id(disorder.groups.get(group),elementId);
    panel.hidden = true;
  }
  for (const endpoint of ['start','end']) {
    for (const index of [1,2]) {
      const labelNode = disorder.fieldGroups.get(`line_${endpoint}_${index}`).querySelector('.entity-form__label');
      labelNode.classList.add(`line-axis-${index}`);
    }
    mount(`line-coordinates/${endpoint}`, `#gps-line-${endpoint}`);
    mount(`bornage/${endpoint}`, `#gps-bornage-${endpoint}`, `#gps-bornage-${endpoint}-distance`);
  }
  disorder.controls.get('line_crs').setAttribute('aria-hidden','true');
  disorder.controls.get('line_crs').tabIndex = -1;
  mount('xy','#gps-point-xy'); mount('lonlat','#gps-point-lonlat');
  disorder.groups.get('bornage').prepend(tools.querySelector('#point-bornage-warning'), tools.querySelector('#line-bornage-warning'));
  geometry.methods.append(tools.querySelector('#gps-map-create'));
  geometry.details.append(tools.querySelector('#polygon-representative-point'));
  disorder.sections.get('attachments').append(tools.querySelector('#desordre-associated-objects'));
  for (const [operation, ids] of [
    ['position', ['point-map-editor', 'start-map-position', 'map-position-actions', 'cancel-map-position', 'validate-map-position', 'map-position-status']],
    ['vertices', ['line-map-editor', 'start-line-edit', 'line-geometry-actions', 'cancel-line-edit', 'validate-line-edit', 'line-geometry-status']],
  ]) {
    const controls = geometry.operations.get(operation);
    for (const [index, key] of ['tools','start','panel','cancel','confirm','status'].entries()) id(controls[key],ids[index]);
    controls.tools.hidden = true;
    controls.confirm.disabled = true;
  }
  geometry.operations.get('position').tools.append(tools.querySelector('#gps-map-edit'));
  disorder.observations.replaceChildren(doc.querySelector('#disorder-observations-component').content);
  id(disorder.status,'desordre-create-message'); id(disorder.actions,'desordre-create-actions');
  id(disorder.save,'submit-desordre-create');id(disorder.cancel,'cancel-desordre-create');
  const report=doc.createElement('button');report.type='button';report.id='desordre-pdf';report.textContent='Fiche PDF';report.hidden=true;
  const reportActions=doc.createElement('div');reportActions.className='entity-form__geometry-actions';reportActions.append(report);
  const reportStatus=doc.createElement('p');reportStatus.id='desordre-pdf-status';reportStatus.setAttribute('role','status');
  disorder.children.get('details').append(reportActions,reportStatus);
  doc.querySelector('#disorder-editor-host').append(disorder.element);

  const observationDescriptor=root.ObservationForm.composition({parent:{resource:'desordres',id:'loading'}});
  const observation=View.create({root,descriptor:observationDescriptor,id:'observation-inline-view',
    onClose:()=>doc.querySelector('#back-to-observations').click(),onCancel:()=>doc.querySelector('#back-to-observations').click()});
  observation.setFormId('observation-suite-form');id(observation.status,'observation-suite-message');
  for(const name of ['suite_apporter_id','suite'])id(observation.controls.get(name),name==='suite'?'observation-suite-complement':'observation-suite-apporter');
  doc.querySelector('#observation-edit-host').append(observation.element);
  root.MapFormViews={heritage,disorder,observation};
})(globalThis);
