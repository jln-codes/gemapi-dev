/* Contrat de formulaire commun aux sept types, Carte et Base. */
(function (root, factory) {
  const view = typeof module === 'object' && module.exports ? require('./entity-form-view.js') : root.EntityFormView;
  const api = factory(view);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.TronconForm = api;
})(globalThis, function (view) {
  'use strict';
  const TYPES = [['INDETERMINE','Indéterminé'],['BERGE','Berge'],['CRETE','Crête'],['TALUS','Talus'],
    ['TALUS_RISBERME','Talus de risberme'],['RISBERME','Risberme'],['FRANC_BORD','Franc-bord']];
  const REFERENCES = [
    ['cote_physique_id','Côté physique'],['type_rive_id','Rive'],['type_troncon_id','Type de tronçon'],
    ['typologie_troncon_id','Typologie'],['source_id','Source d’information'],['cote_id','Côté'],['position_id','Position'],
    ...[['fonction','Fonction'],['nature','Nature'],['materiau','Matériau']].flatMap(([key,label]) =>
      [['',label],['_haut',`${label} — haut`],['_bas',`${label} — bas`]].map(([suffix,title]) => [`${key}${suffix}_id`,title])),
    ['largeur_franc_bord_id','Classe de largeur du franc-bord']];
  const MEASURES = [['num_couche','Numéro de couche'],['epaisseur','Épaisseur'],['largeur_moyenne','Largeur moyenne'],
    ['epaisseur_sommet','Épaisseur au sommet'],['pente_interieure','Pente intérieure'],
    ['longueur_rampant_haut','Longueur de rampant haut'],['longueur_rampant_bas','Longueur de rampant bas'],
    ['hauteur_murette','Hauteur de murette'],['largeur','Largeur']];
  const PARENTS = [['digue_id','Digue'],['amenagement_hydraulique_id','Aménagement hydraulique'],
    ['troncon_support_id','Tronçon support classique'],['segment_hydrographique_support_id','Segment hydrographique support'],
    ['berge_support_id','Berge support']];
  const ROOT_FIELDS = ['type_rive_id','type_troncon_id','typologie_troncon_id'];
  const SIMPLE = ['fonction_id','nature_id','materiau_id'];
  const SLOPES = ['fonction_haut_id','fonction_bas_id','nature_haut_id','nature_bas_id','materiau_haut_id','materiau_bas_id',
    'num_couche','epaisseur_sommet','pente_interieure','longueur_rampant_haut','longueur_rampant_bas','cote_id'];
  const SUPPORTS = ['troncon_support_id','segment_hydrographique_support_id','berge_support_id'];
  // Presentation follows the declared C3 attributes of Crete, TalusDigue,
  // TalusRisberme, SommetRisberme and LargeurFrancBord. This does not restrict
  // storage: any populated value outside the profile remains available below.
  const TYPE_FIELDS = Object.freeze({
    INDETERMINE: ROOT_FIELDS, BERGE: ROOT_FIELDS,
    CRETE: ['source_id',...SIMPLE,'num_couche','epaisseur','largeur_moyenne','troncon_support_id'],
    TALUS: ['source_id',...SLOPES,...SUPPORTS],
    TALUS_RISBERME: ['source_id',...SLOPES,...SUPPORTS],
    RISBERME: ['source_id',...SIMPLE,'cote_id','num_couche','epaisseur',...SUPPORTS],
    FRANC_BORD: ['source_id','largeur_franc_bord_id',...SUPPORTS],
  });
  const DIAGNOSTICS = new Set(['geometry_type','geometry_srid','geometry_dimensions','geometry_valide']);
  const PROFILES = {classId:'troncons',singular:'Tronçon',resource:'troncons',apiPath:'/api/troncons',observationResource:'troncons'};
  const snapshots = new WeakMap();
  function profile() { return PROFILES; }
  function readFields() {
    return [['type_physique_id','Sous-type','select',true,'','type_physique_libelle'],
      ['libelle','Libellé','text',true],['designation','Désignation','text',true],
      ['commentaire','Commentaire','textarea',true],['date_debut','Date de début','date',true],['date_fin','Date de fin','date',true],
      ...REFERENCES.map(([k,l])=>[k,l,'select',true,'',k.replace(/_id$/,'_libelle')]),
      ...MEASURES.map(([k,l])=>[k,l,'number',true]),...PARENTS.map(([k,l])=>[k,l,'select',true,'',k.replace(/_id$/,'_libelle')]),
      ['valid','Objet valide','checkbox',true],['localisation','Géométrie physique','text',false],
      ['geometry_type','Type géométrique conservé','text',false],['geometry_srid','SCR de la géométrie','number',false],
      ['geometry_dimensions','Dimensions conservées','number',false],['geometry_valide','Géométrie valide','tristate',false]];
  }
  function composition(_profile, {feature,geometryEditable=false} = {}) {
    const descriptor = view.fromFields(PROFILES,readFields(), {
      classification: REFERENCES.map(([k])=>k),attachments:PARENTS.map(([k])=>k),
      overrides:{num_couche:{step:'1'},type_physique_id:{required:true,options:TYPES,
        help:'Le changement conserve les valeurs et les liens. Il sera accepté si la géométrie, les attributs et les relations restent compatibles avec le sous-type choisi.'}},children:[],geometry:null});
    descriptor.geometry={types:['Point','LineString','Polygon'],typeName:'geometry_type_input',required:true,
      editable:geometryEditable,controls:'draft',fields:[{name:'geometry_mode',label:'Géométrie physique',kind:'select',
      options:[['garder','Conserver exactement'],['remplacer','Remplacer explicitement'],['effacer','Retirer explicitement']]},
      {name:'geometry_sommets',label:'Sommets — longitude latitude [altitude]',kind:'textarea',rows:5,
        help:'Saisie facultative pour un remplacement explicite. Un sommet par ligne en WGS84 ; fermer une surface en répétant son premier sommet. Conserver préserve exactement la géométrie historique.'}]};
    const p=feature?.properties || {},measureKeys=new Set(MEASURES.map(([key])=>key));
    const conditional=new Set([...REFERENCES,...MEASURES,...PARENTS].map(([key])=>key));
    const usual={identification:[],classification:[],attachments:[],characteristics:[],historical:[]};
    for(const key of ['identification','classification','attachments'])for(const field of descriptor.fields[key]){
      const group=conditional.has(field.name)&&!applies(field.name,p)&&populated(p[field.name]) ? 'historical'
        : measureKeys.has(field.name)?'characteristics':key;
      usual[group].push(field);
    }
    Object.assign(descriptor.fields,usual);
    return view.validate(descriptor);
  }
  async function loadChoices(_profile,fetchJson) { return fetchJson('/api/troncons/options'); }
  function value(control) { return control.type==='checkbox' ? control.checked : control.value; }
  function fill(form,feature,choices,referenceData,selectors) {
    const p=feature.properties;
    for (const [key,,kind,editable] of readFields()) {
      const c=form.elements[key]; if (!c) continue;
      if (!editable) {c.value=key==='type_physique_libelle' ? typeLabel(p) : p[key]??'';continue;}
      if (key==='type_physique_id') {c.value=p[key];continue;}
      if (kind==='select') {
        let rows=choices.references?.[key];
        if (!rows) rows= key==='digue_id' ? choices.digues : key==='amenagement_hydraulique_id' ? choices.amenagements
          : key==='segment_hydrographique_support_id' ? choices.segments
          : (choices.troncons||[]).filter(row=>key==='berge_support_id' ? row.est_support_berge : row.est_support_classique);
        referenceData.fillSelect(c,rows||[],p[key],'Non renseigné',
          p[key.replace(/_id$/,'_libelle')] || 'Valeur existante non résolue');
      } else if (kind==='checkbox') c.checked=p[key]===true;
      else c.value=p[key]??'';
    }
    const g=feature.geometry;
    form.elements.geometry_mode.value='garder';form.elements.geometry_type_input.value=['Point','LineString','Polygon'].includes(g?.type)?g.type:'LineString';
    const coords=g?.type==='Point'?[g.coordinates]:g?.type==='LineString'?g.coordinates:g?.type==='Polygon'&&g.coordinates.length===1?g.coordinates[0]:[];
    form.elements.geometry_sommets.value=coords.map(point=>point.join(' ')).join('\n');
    snapshots.set(form,new Map([...form.elements].filter(c=>c.name).map(c=>[c.name,value(c)])));
    updateConditional(form,feature);
  }
  function applies(key,p) {
    return ['cote_physique_id','digue_id','amenagement_hydraulique_id'].includes(key)
      || (TYPE_FIELDS[p.type_physique_id] || []).includes(key);
  }
  function populated(value) { return value!==null && value!==undefined && value!==''; }
  function typeLabel(p) { return p.type_physique_libelle || TYPES.find(([id])=>id===p.type_physique_id)?.[1] || 'Sous-type non résolu'; }
  function geometrySummary(feature) {
    const kind=feature.geometry?.type || feature.properties.geometry_type;
    const labels={POINT:'Point de localisation',LINESTRING:'Tracé linéaire',POLYGON:'Surface',
      MULTIPOINT:'Ensemble de points',MULTILINESTRING:'Ensemble de tracés',MULTIPOLYGON:'Ensemble de surfaces',
      CIRCULARSTRING:'Courbe circulaire historique',COMPOUNDCURVE:'Courbe composée historique',
      CURVEPOLYGON:'Surface à contours courbes'};
    if(!kind)return 'Géométrie non renseignée';
    const label=labels[String(kind).toUpperCase()] || 'Géométrie historique';
    return feature.geometry ? label : `${label} — affichage cartographique indisponible`;
  }
  function readSections(feature) {
    const p=feature.properties,fields=readFields();
    const conditional=new Set([...REFERENCES,...MEASURES,...PARENTS].map(([key])=>key));
    const select=keys=>fields.filter(([key])=>keys.has(key));
    const usual=entries=>new Set(entries.map(([key])=>key).filter(key=>applies(key,p)));
    const sections=[
      {key:'identification',title:'Identification',fields:fields.filter(([key])=>!conditional.has(key)&&!DIAGNOSTICS.has(key)&&key!=='localisation')},
      {key:'classification',title:'Classification',fields:select(usual(REFERENCES))},
      {key:'characteristics',title:'Caractéristiques',fields:select(usual(MEASURES))},
      {key:'attachments',title:'Rattachements',fields:select(usual(PARENTS))},
      {key:'geometry',title:'Géométrie physique',fields:fields.filter(([key])=>key==='localisation')},
      {key:'historical',title:'Informations historiques complémentaires',fields:fields.filter(([key])=>conditional.has(key)&&!applies(key,p)&&populated(p[key]))},
      {key:'diagnostics',title:'Informations techniques',fields:select(DIAGNOSTICS)},
    ];
    return sections.filter(section=>section.fields.length);
  }
  function visibleFields(feature) {
    const conditional=new Set([...REFERENCES,...MEASURES,...PARENTS].map(([key])=>key));
    return readFields().filter(([key])=>!conditional.has(key)||applies(key,feature.properties)||populated(feature.properties[key]));
  }
  function updateConditional(form,feature) {
    // Read the live draft without replacing controls or changing their values.
    // A field typed during a detour remains visible when it becomes atypical.
    const p={...feature.properties,type_physique_id:form.elements.type_physique_id?.value || feature.properties.type_physique_id};
    for (const [key] of [...REFERENCES,...MEASURES,...PARENTS]) {
      const c=form.elements[key];if(c && populated(c.value))p[key]=c.value;
    }
    for (const [key] of [...REFERENCES,...MEASURES,...PARENTS]) {
      const c=form.elements[key];if(c) c.closest('.entity-form__field').hidden=!applies(key,p)&&!populated(p[key]);
    }
    for (const key of ['geometry_type_input','geometry_sommets']) {
      form.elements[key].closest('.entity-form__field').hidden=form.elements.geometry_mode.value!=='remplacer';
    }
    arrange(form,feature,p);
  }
  function arrange(form,feature,p) {
    // Reuse the compositor's sections and the same live controls during a
    // subtype detour: no reconstruction, value replacement or draft reset.
    const document=form.ownerDocument,fieldset=form.querySelector?.('.entity-form__fields');
    if(!document || !fieldset)return;
    for(const [entries,usual] of [[REFERENCES,'classification'],[MEASURES,'characteristics'],[PARENTS,'attachments']]){
      for(const [key] of entries){
        const field=form.elements[key]?.closest('.entity-form__field');
        const target=fieldset.querySelector(`[data-section="${!applies(key,p)&&populated(p[key])?'historical':usual}"]`);
        if(field && target && field.parentElement!==target){
          const focused=field.contains(document.activeElement)?document.activeElement:null;
          target.append(field);focused?.focus({preventScroll:true});
        }
      }
    }
    for(const section of fieldset.querySelectorAll(':scope > [data-section]')){
      section.hidden=![...section.querySelectorAll('.entity-form__field')].some(field=>!field.hidden);
    }
    const geometrySection=fieldset.querySelector('[data-section="geometry"]');
    if(!geometrySection)return;
    let coordinates=geometrySection.querySelector('[data-troncon-coordinates]');
    if(!coordinates){
      coordinates=document.createElement('details');coordinates.dataset.tronconCoordinates='';
      const summary=document.createElement('summary');summary.textContent='Coordonnées du nouveau tracé — saisie avancée';coordinates.append(summary);
      geometrySection.append(coordinates);
      coordinates.append(form.elements.geometry_sommets.closest('.entity-form__field'));
    }
    coordinates.hidden=form.elements.geometry_mode.value!=='remplacer';
  }
  function geometry(form) {
    const mode=form.elements.geometry_mode.value;
    if (mode==='garder') return undefined;
    if (mode==='effacer') return null;
    const type=form.elements.geometry_type_input.value;
    const points=parseCoordinates(form.elements.geometry_sommets.value);
    if (type==='Point'&&points.length!==1 || type==='LineString'&&points.length<2
        || type==='Polygon'&&(points.length<4||JSON.stringify(points[0])!==JSON.stringify(points.at(-1)))) {
      throw new Error('Nombre de sommets ou fermeture de surface invalide.');
    }
    return {type,coordinates:type==='Point'?points[0]:type==='Polygon'?[points]:points};
  }
  function parseCoordinates(text) {
    const points=text.trim().split(/\n/).filter(line=>line.trim()).map(line=>line.trim().split(/[\s,;]+/).map(Number));
    if (!points.length||points.some(p=>![2,3].includes(p.length)||p.some(v=>!Number.isFinite(v))
        || Math.abs(p[0])>180||Math.abs(p[1])>90)||points.some(p=>p.length!==points[0].length)) {
      throw new Error('Coordonnées attendues : longitude latitude [altitude], une ligne par sommet.');
    }
    return points;
  }
  function payload(form,_profile,_selectors,feature) {
    const initial=snapshots.get(form),result={};
    for (const [key,,kind,editable] of readFields()) {
      if(!editable)continue;
      const c=form.elements[key];if(value(c)===initial?.get(key))continue;
      if (kind==='number') {
        if(c.value!==''&&!Number.isFinite(Number(c.value)))throw new Error('Mesure non numérique.');
        result[key]=c.value===''?null:Number(c.value);
      } else result[key]=kind==='checkbox'?c.checked:['select','date'].includes(kind)?c.value||null:c.value;
    }
    const g=geometry(form);
    if(g!==undefined)result.geometry=g;
    const valid=Object.hasOwn(result,'valid')?result.valid:feature.properties.valid;
    const shape=g===undefined?feature.geometry:g;
    const chosenType=result.type_physique_id || feature.properties.type_physique_id;
    // A retained historical line/surface can be valid in SQL while its unknown
    // CRS prevents a GeoJSON display. Never require a replacement just to edit.
    const storedShape=g===undefined && feature.properties.geometry_valide===true && feature.properties.geometry_vide===false
      ? {LINESTRING:'LineString',POLYGON:'Polygon'}[feature.properties.geometry_type] : null;
    if (valid && ['RISBERME','FRANC_BORD'].includes(chosenType)
        && !['LineString','Polygon'].includes(shape?.type || storedShape)) throw new Error('Ce sous-type valide exige une ligne ou une surface.');
    return result;
  }
  function isDirty(form) {
    const initial=snapshots.get(form);return Boolean(initial&&[...initial].some(([key,before])=>value(form.elements[key])!==before));
  }
  return {TYPES,REFERENCES,MEASURES,PARENTS,profile,readFields,visibleFields,readSections,typeLabel,geometrySummary,
    composition,loadChoices,fill,updateConditional,payload,isDirty,parseCoordinates,geometry};
});
