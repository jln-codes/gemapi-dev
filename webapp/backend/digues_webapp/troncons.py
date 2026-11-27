"""Canonical Tronçon API: explicit C1 fields, no extension or inferred axis."""
import json
from uuid import uuid4
from fastapi import HTTPException
from psycopg import IntegrityError, DataError

# One allowlist shared by reads/writes; generated role columns stay private.
FIELDS = tuple('''type_physique_id cote_physique_id digue_id amenagement_hydraulique_id
libelle designation commentaire date_debut date_fin valid type_rive_id type_troncon_id
typologie_troncon_id systeme_reperage_defaut_id troncon_support_id
segment_hydrographique_support_id berge_support_id source_id cote_id position_id
fonction_id fonction_haut_id fonction_bas_id nature_id nature_haut_id nature_bas_id
materiau_id materiau_haut_id materiau_bas_id largeur_franc_bord_id num_couche epaisseur
largeur_moyenne epaisseur_sommet pente_interieure longueur_rampant_haut
longueur_rampant_bas hauteur_murette largeur'''.split())
CATALOGS = dict(type_physique_id='types_troncon_unifie',cote_physique_id='cotes_troncon_unifie',
    type_rive_id='rives',type_troncon_id='types_troncon',typologie_troncon_id='typologies_troncon',
    source_id='sources_information',cote_id='cotes',position_id='positions',
    largeur_franc_bord_id='largeurs_franc_bord')
for family,table in [('fonction','fonctions_structure'),('nature','natures_structure'),('materiau','materiaux_structure')]:
    for suffix in ('','_haut','_bas'): CATALOGS[family+suffix+'_id']=table


def json_object(pairs):
    # PostgreSQL limits variadic functions to 100 arguments.
    parts=[]
    for start in range(0,len(pairs),40):
        parts.append('jsonb_build_object('+','.join("'"+key+"',"+value for key,value in pairs[start:start+40])+')')
    return '('+' || '.join(parts)+')'


JOINS = '''FROM public.troncons t
LEFT JOIN public.digues d ON d.id=t.digue_id
LEFT JOIN public.systemes s ON s.id=d.systeme_endiguement_id
LEFT JOIN public.amenagements_hydrauliques a ON a.id=t.amenagement_hydraulique_id
'''+''.join(f'LEFT JOIN referentiel.{table} ref_{field} ON ref_{field}.id=t.{field}\n' for field,table in CATALOGS.items())
GEOJSON_SUPPORTED = """(NOT ST_IsEmpty(t.geometry) AND GeometryType(t.geometry) IN
    ('POINT','LINESTRING','POLYGON','MULTIPOINT','MULTILINESTRING','MULTIPOLYGON','GEOMETRYCOLLECTION')
    AND NOT EXISTS (SELECT 1 FROM ST_Dump(t.geometry) part
                    WHERE GeometryType(part.geom) NOT IN ('POINT','LINESTRING','POLYGON')))
"""
PROPERTIES = json_object([('id','t.id'),('version','t.version')]+[(f,'t.'+f) for f in FIELDS]+[
    (f.removesuffix('_id')+'_libelle','ref_'+f+'.libelle') for f in CATALOGS]+[
    ('created_by','t.created_by'),('edited_by','t.edited_by'),
    ('digue_libelle','d.libelle'),('amenagement_hydraulique_libelle','coalesce(a.libelle,a.designation)'),
    ('systeme_endiguement_id','d.systeme_endiguement_id'),('systeme_endiguement_libelle','s.libelle'),
    ('geometry_type','GeometryType(t.geometry)'),('geometry_srid','ST_SRID(t.geometry)'),
    ('geometry_dimensions','ST_NDims(t.geometry)'),('geometry_geojson_supportee',GEOJSON_SUPPORTED),('geometry_valide','CASE WHEN '+GEOJSON_SUPPORTED+' THEN ST_IsValid(t.geometry) ELSE NULL END'),
    ('geometry_vide','ST_IsEmpty(t.geometry)'),
    ('nombre_sommets','ST_NPoints(t.geometry)'),('geometry_reperage_disponible','t.geometry_reperage IS NOT NULL'),
    ('requalification_autorisee','true'),('requalification_conditionnelle','true'),
    ('nombre_systemes_reperage','(SELECT count(*) FROM public.systemes_reperage sr WHERE sr.troncon_id=t.id)')])
GEOMETRY = 'CASE WHEN '+GEOJSON_SUPPORTED+''' AND ST_SRID(t.geometry)>0 AND EXISTS
    (SELECT 1 FROM spatial_ref_sys crs WHERE crs.srid=ST_SRID(t.geometry))
    THEN ST_AsGeoJSON(ST_Transform(t.geometry,4326))::jsonb ELSE NULL END'''
FEATURE = "jsonb_build_object('type','Feature','id',t.id,'geometry',"+GEOMETRY+",'properties',"+PROPERTIES+")"
DETAIL_SQL = 'SELECT '+FEATURE+' '+JOINS+' WHERE t.id=%s'
COLLECTION_SQL = "SELECT jsonb_build_object('type','FeatureCollection','features',coalesce(jsonb_agg(feature ORDER BY label,id),'[]'::jsonb)) FROM (SELECT "+FEATURE+" feature,coalesce(t.libelle,t.designation,'') label,t.id "+JOINS+") rows"


def version_conflict(identifier,expected,current):
    raise HTTPException(409, {'code':'version-conflict','resource':'troncons','id':str(identifier),
        'baseVersion':expected,'currentVersion':current,
        'message':'Ce Tronçon a changé. Conservez votre brouillon et relisez sa version avant de réessayer.'})


def _references(cursor,values,current,identifier):
    for field,table in CATALOGS.items():
        if field not in values or values[field] is None or values[field]==current.get(field):continue
        cursor.execute(f'SELECT 1 FROM referentiel.{table} WHERE id=%s AND valid',(values[field],))
        if cursor.fetchone() is None:raise HTTPException(422, {'code':'troncon-reference-invalid','field':field,
            'message':'La référence choisie est absente ou inactive. Aucun changement enregistré ; les valeurs et les liens sont conservés.'})
    parents={'digue_id':('digues','true'),'amenagement_hydraulique_id':('amenagements_hydrauliques','true'),
        'troncon_support_id':('troncons','est_support_classique'),
        'segment_hydrographique_support_id':('segments_hydrographiques','true'),
        'berge_support_id':('troncons',"type_physique_id='BERGE'")}
    for field,(table,predicate) in parents.items():
        if field not in values or values[field] is None or str(values[field])==str(current.get(field)):continue
        cursor.execute(f'SELECT id FROM public.{table} WHERE id=%s AND ({predicate}) FOR KEY SHARE',(values[field],))
        if cursor.fetchone() is None:
            label={'digue_id':'La Digue', 'amenagement_hydraulique_id':'L’Aménagement hydraulique',
                   'troncon_support_id':'Le Tronçon support classique', 'segment_hydrographique_support_id':'Le Segment hydrographique support',
                   'berge_support_id':'La Berge support'}[field]
            raise HTTPException(422,{'code':'troncon-parent-invalid','field':field,
                'message':label+' est absent ou de type incompatible dans cette base. Aucun changement enregistré ; les liens sont conservés.'})
    field='systeme_reperage_defaut_id'
    if field in values and values[field] is not None and str(values[field])!=str(current.get(field)):
        cursor.execute('SELECT troncon_id FROM public.systemes_reperage WHERE id=%s FOR KEY SHARE',(values[field],))
        row=cursor.fetchone()
        if row is None or row[0]!=identifier:
            raise HTTPException(422,{'code':'troncon-system-owner-mismatch','field':field,'message':'Le système de repérage doit appartenir à ce Tronçon.'})


def touch_amenagements(cursor,identifiers):
    result=[]
    for identifier in sorted({v for v in identifiers if v is not None},key=str):
        cursor.execute('UPDATE public.amenagements_hydrauliques SET version=version WHERE id=%s RETURNING id,version',(identifier,))
        row=cursor.fetchone()
        if row:result.append((str(row[0]),row[1]))
    return result


CRETE_EXCLUSIONS = dict(
    segment_hydrographique_support_id='Segment hydrographique support', berge_support_id='Berge support',
    cote_id='Côté', position_id='Position', fonction_haut_id='Fonction — haut', fonction_bas_id='Fonction — bas',
    nature_haut_id='Nature — haut', nature_bas_id='Nature — bas', materiau_haut_id='Matériau — haut',
    materiau_bas_id='Matériau — bas', largeur_franc_bord_id='Classe de largeur du franc-bord',
    epaisseur_sommet='Épaisseur au sommet', pente_interieure='Pente intérieure',
    longueur_rampant_haut='Longueur de rampant haut', longueur_rampant_bas='Longueur de rampant bas',
    hauteur_murette='Hauteur de murette', largeur='Largeur')


def constraint_error(exc, current, values):
    """Translate authoritative SQL failures after rollback, never repair data.

    In particular, deferred FK checks also cover concurrent parent changes.
    Constraint identifiers are an internal dispatch key, never user output.
    """
    name = getattr(getattr(exc, 'diag', None), 'constraint_name', None)
    table = getattr(getattr(exc, 'diag', None), 'table_name', None)
    details = {'code': 'troncon-constraint-invalid'}
    message = 'Attribut, géométrie ou référence typée incompatible avec le contrat du Tronçon.'
    if name == 'ck_troncons_crete_fields':
        final = {**current, **values}
        fields = [field for field in CRETE_EXCLUSIONS if final.get(field) is not None]
        details.update(code='troncon-crete-fields-incompatible', fields=fields)
        labels = ', '.join(CRETE_EXCLUSIONS[field] for field in fields)
        message = 'Le sous-type Crête est incompatible avec les valeurs conservées : ' + labels + '.'
    elif name == 'ck_troncons_geometry_b4':
        details.update(code='troncon-geometry-incompatible', field='geometry')
        message = 'Une Risberme ou un Franc-bord valide exige une ligne ou une surface non vide et géométriquement valide.'
    elif name == 'ck_troncons_support':
        details.update(code='troncon-support-incompatible', fields=[
            field for field in ('troncon_support_id', 'segment_hydrographique_support_id', 'berge_support_id')
            if values.get(field, current.get(field)) is not None])
        message = 'Les supports conservés sont incompatibles avec le sous-type demandé : seuls les sous-types Structure acceptent un support, et un seul.'
    elif name in {'fk_berge', 'fk_composant_berge_troncon', 'fk_ouvrage_lie_berge', 'fk_troncon_support_berge'}:
        relation = {'observations': 'une Observation de rôle Berge',
                    'composants_berges': 'un composant de Berge',
                    'structures_protection': 'une Structure de protection rattachée à la Berge',
                    'ouvrages_hydrauliques__objets_lies': 'un lien d’Ouvrage hydraulique à la Berge',
                    'segments_hydrographiques__ouvrages': 'un ouvrage associé au lit par son rôle Berge',
                    'troncons': 'un autre Tronçon utilisant cette Berge comme support'}.get(table, 'un lien de rôle Berge')
        details.update(code='troncon-berge-role-in-use', relation=relation)
        message = 'Ce Tronçon est utilisé par ' + relation + ' ; ce lien exige le sous-type Berge.'
    elif name in {'fk_structure_digue', 'fk_localisation_structure'}:
        relation = 'une Observation de rôle Structure' if name == 'fk_structure_digue' else 'une localisation de rôle Structure'
        details.update(code='troncon-structure-role-in-use', relation=relation)
        message = 'Ce Tronçon est utilisé par ' + relation + ' ; ce lien exige un sous-type Structure.'
    elif name in {'fk_troncon_troncon_support', 'fk_trace_support_classique'}:
        details.update(code='troncon-classic-support-in-use')
        message = 'Ce Tronçon sert encore de support classique à un Tronçon ou à une trace ; il doit rester Indéterminé, avec une Digue et un libellé.'
    elif name in {'fk_trace_position_pied', 'fk_trace_position_sommet'}:
        pied = name == 'fk_trace_position_pied'
        details.update(code='troncon-position-incompatible', position='PIED' if pied else 'SOMMET')
        message = ('Une trace Pied est encore attachée à ce Tronçon ; elle exige une Berge ou un Talus.' if pied else
                   'Une trace Sommet est encore attachée à ce Tronçon ; elle exige une Berge, un Talus ou une Risberme.')
    elif name == 'ck_troncons_axe_reperage':
        details.update(code='troncon-axis-incompatible', field='geometry_reperage')
        message = 'L’axe de repérage doit rester une ligne valide, non vide et de longueur positive.'
    if current and values.get('type_physique_id', current.get('type_physique_id')) != current.get('type_physique_id'):
        details.update(currentType=current['type_physique_id'], requestedType=values['type_physique_id'])
    details['message'] = message + ' Aucun changement enregistré ; les valeurs et les liens sont conservés.'
    return HTTPException(422, details)


def write(connection,command,*,identifier=None,fetch):
    creating=identifier is None
    identifier=(command.id or uuid4()) if creating else identifier
    values=command.model_dump(exclude_unset=not creating,exclude={'id','geometry'})
    related=[]
    current={}
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                if creating:current={}
                else:
                    cursor.execute('SELECT '+json_object([(f,'t.'+f) for f in FIELDS])+' FROM public.troncons t WHERE id=%s FOR UPDATE',(identifier,))
                    row=cursor.fetchone()
                    if row is None:
                        from .queries import HeritageNotFoundError
                        raise HeritageNotFoundError('Tronçon introuvable dans cette base.')
                    current=row[0]
                _references(cursor,values,current,identifier)
                fields=list(values);parameters=list(values.values());expressions=['%s']*len(fields)
                if creating or 'geometry' in command.model_fields_set:
                    fields.append('geometry')
                    if command.geometry is None:
                        expressions.append('%s');parameters.append(None)
                    else:
                        from .database import read_business_crs
                        expressions.append('ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s)')
                        parameters.extend([json.dumps(command.geometry.model_dump(mode='json',exclude_none=True),allow_nan=False),read_business_crs(connection).srid])
                if creating:
                    cursor.execute('INSERT INTO public.troncons (id,'+','.join(fields)+') VALUES (%s,'+','.join(expressions)+')',(identifier,*parameters))
                else:
                    cursor.execute('UPDATE public.troncons SET '+','.join(f+'='+e for f,e in zip(fields,expressions))+' WHERE id=%s',(*parameters,identifier))
                # Validate deferred typed references before leaving the savepoint;
                # a failure cannot reach journal allocation or a success receipt.
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                previous=current.get('amenagement_hydraulique_id')
                chosen=values.get('amenagement_hydraulique_id',previous)
                if str(previous)!=str(chosen):related=touch_amenagements(cursor,[previous,chosen])
            result=fetch(connection,identifier)
            if related:result['_related_mutations']={'amenagements-hydrauliques':dict(related)}
            if not creating and values.get('type_physique_id', current['type_physique_id']) != current['type_physique_id']:
                result['_type_change'] = {'previousType': current['type_physique_id'], 'newType': values['type_physique_id']}
            return result
    except (IntegrityError,DataError) as exc:
        if getattr(exc,'sqlstate',None)=='23505':
            raise HTTPException(409,{'code':'troncon-id-conflict','resource':'troncons','id':str(identifier),'message':'Cet UUID existe déjà dans cette base. Aucun changement enregistré.'}) from exc
        raise constraint_error(exc, current, values) from exc
