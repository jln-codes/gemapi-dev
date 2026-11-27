/* Composition des variantes Photo ; transferts et localisation restent dans leurs contrôleurs. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.PhotoForm=api;})(globalThis,function(){
  'use strict';
  const field=(name,label,kind='text',other={})=>({name,label,kind,...other});
  function composition(variant='metadata') {
    const descriptor={family:'photos',
      fields:{identification:[],classification:[],attachments:[field('parent_label','Observation parente','output',{readOnly:true})]}};
    if(variant==='localisation') {
      descriptor.context = 'photo-localisation';
      descriptor.fields.identification=[field('photo_label','Photo','output',{readOnly:true})];
      descriptor.geometry={types:['Point'],required:false,editable:true,controls:'specialized',
        modes:['point','bornage','pr','effacer'],modeName:'mode',operations:['mini-map'],groupLayouts:{'coordinates/axes':'coordinate-pair'},fields:[
        field('srid','Coordonnées','select',{group:'coordinates'}),field('x','X / Longitude','number',{numberControl:'coordinate',step:'any',group:'coordinates/axes'}),field('y','Y / Latitude','number',{numberControl:'coordinate',step:'any',group:'coordinates/axes'}),
        field('troncon_id','Tronçon de travail','relation',{group:'reference'}),field('systeme_reperage_id','Système de repérage','select',{group:'reference'}),field('reload','Recharger les systèmes','button',{group:'reference'}),
        field('borne_id','Borne','select',{group:'bornage'}),field('position_relative','Position dans le sens géométrique du tronçon','select',{group:'bornage',options:[['SUR_BORNE','Sur la borne'],['AVANT_BORNE','Amont'],['APRES_BORNE','Aval']]}),field('distance_m','Distance (m)','number',{numberControl:'metres',group:'bornage'}),
        field('pr','PR calibré','number',{step:'any',group:'pr'}),field('remplacer_historique','Je confirme le remplacement de toute la localisation historique, y compris ses extrémités.','checkbox',{value:false,group:'replacement'}),
      ]};return descriptor;
    }
    const offline=variant==='offline';
    descriptor.fields.identification=[field('date','Date','date'),field('libelle','Libellé','text',{maxLength:2000}),field('designation','Désignation','text',{maxLength:2000}),
      field('file','Sélectionner une photo','file',{help:'JPEG, PNG ou WebP statique · 20 Mio et 25 millions de pixels maximum.',accept:'image/jpeg,image/png,image/webp',group:'file-fields',hidden:offline}),
      field('camera','Prendre une photo','file',{accept:'image/jpeg,image/png,image/webp',capture:'environment',group:'file-fields',hidden:offline}),
      field('preview','Aperçu de la photo','image'),field('filename','Fichier','output',{readOnly:true})];
    if(offline) descriptor.fields.identification.push(field('select_source','Sélectionner une photo','button'),field('capture_source','Prendre une photo','button'),field('storage','Stockage local','output',{readOnly:true}));
    else {
      descriptor.fields.classification=[field('orientation_photo_id','Orientation','select'),field('cote_id','Côté','select')];
      descriptor.fields.attachments.push(field('photographe_id','Photographe (identifiant du contact, facultatif)','text',{
        pattern:'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',autocomplete:'off'}));
    }
    descriptor.actions=offline?[
      {name:'another',label:'Ajouter une autre photo',hidden:true},{name:'abandon',label:'Abandonner cette Photo locale',hidden:true}
    ]:[{name:'references',label:'Recharger les choix',hidden:true},{name:'prepare-local',label:'Conserver sur cet appareil'},
      {name:'localisation',label:'Ajouter / modifier la localisation',hidden:true},{name:'another',label:'Ajouter une autre photo',hidden:true}];
    return descriptor;
  }
  return {composition};
});
