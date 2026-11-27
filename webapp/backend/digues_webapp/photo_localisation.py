"""Commandes de positionnement des photos sans écriture de l'agrégat Désordre."""
import json
import math
import re
import psycopg
from uuid import UUID
from typing import Literal
from fastapi import Depends, Header
from pydantic import (
    BaseModel,
    ConfigDict,
    FiniteFloat,
    StrictBool,
    StrictInt,
    model_validator,
)
from .database import (
    WebDatabaseError,
    business_crs_payload,
    get_connection,
    get_write_connection,
    read_business_crs,
)
from .media_storage import fail
from .resource_journal import mutation_origin
from .resource_mutations import execute_mutation
from .reperage_references import check_revision


class PhotoLocalisationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['point', 'bornage', 'pr', 'abscisse', 'effacer']
    srid: StrictInt | None = None
    coordinates: tuple[FiniteFloat, FiniteFloat] | None = None
    troncon_id: UUID | None = None
    systeme_reperage_id: UUID | None = None
    borne_id: UUID | None = None
    distance_m: FiniteFloat | None = None
    position_relative: Literal['AVANT_BORNE', 'SUR_BORNE', 'APRES_BORNE'] | None = None
    pr: FiniteFloat | None = None
    abscisse_m: FiniteFloat | None = None
    remplacer_historique: StrictBool = False

    @model_validator(mode='after')
    def validate_command(self):
        families = {'point': {'srid','coordinates','troncon_id','systeme_reperage_id'},
                    'bornage': {'troncon_id','systeme_reperage_id','borne_id','distance_m','position_relative'},
                    'pr': {'troncon_id','systeme_reperage_id','pr'},
                    'abscisse': {'troncon_id','abscisse_m'}, 'effacer': set()}
        for key in self.model_fields_set - families[self.mode] - {'mode','remplacer_historique'}:
            if getattr(self,key) is not None: raise ValueError('Une seule famille de localisation est autorisée.')
        if self.mode != 'abscisse' and (self.troncon_id is None) != (self.systeme_reperage_id is None):
            raise ValueError('Choisissez explicitement le tronçon et son système.')
        if self.mode in {'bornage','pr'} and self.troncon_id is None:
            raise ValueError('Tronçon de travail ambigu ou absent : choisissez un tronçon et un système.')
        if self.mode == 'point':
            if self.srid is None or self.coordinates is None: raise ValueError('Coordonnées et SRID obligatoires.')
            if self.srid == 4326 and not (-180 <= self.coordinates[0] <= 180 and -90 <= self.coordinates[1] <= 90):
                raise ValueError('Longitude/latitude hors limites.')
            if self.srid != 4326 and any(abs(v) > 100_000_000 for v in self.coordinates):
                raise ValueError('Coordonnées projetées hors limites techniques (100 millions de mètres).')
        if self.mode == 'bornage':
            if self.borne_id is None or self.distance_m is None or self.position_relative is None:
                raise ValueError('Borne, distance et position relative obligatoires.')
            if self.distance_m < 0 or (self.position_relative == 'SUR_BORNE' and self.distance_m != 0):
                raise ValueError('Distance positive ou nulle, et nulle sur la borne.')
        if self.mode == 'pr' and self.pr is None: raise ValueError('PR obligatoire.')
        if self.mode == 'abscisse' and (self.troncon_id is None or self.abscisse_m is None):
            raise ValueError('Tronçon et abscisse curviligne obligatoires.')
        return self


def uuid_or_none(value):
    try: return UUID(value) if value else None
    except (ValueError, TypeError): return None


LOCALISATION_FIELDS_SQL = """jsonb_build_object(
 'geometry',ST_AsGeoJSON(p.geometry)::jsonb, '_geometry_srid',ST_SRID(p.geometry),
 'geometry_valid',CASE WHEN p.geometry IS NULL THEN NULL ELSE ST_IsValid(p.geometry) AND NOT ST_IsEmpty(p.geometry) END,
 'position_debut',ST_AsGeoJSON(p.position_debut)::jsonb, '_position_debut_srid',ST_SRID(p.position_debut),
 'position_fin',ST_AsGeoJSON(p.position_fin)::jsonb, '_position_fin_srid',ST_SRID(p.position_fin),
 'systeme_reperage_id',p.systeme_reperage_id,
 'borne_debut_id',p.borne_debut_id, 'borne_fin_id',p.borne_fin_id,
 'borne_debut_aval',p.borne_debut_aval, 'borne_fin_aval',p.borne_fin_aval,
 'borne_debut_distance',p.borne_debut_distance, 'borne_fin_distance',p.borne_fin_distance,
 'pr_debut',p.pr_debut, 'pr_fin',p.pr_fin)
"""
READ_SQL = 'SELECT ' + LOCALISATION_FIELDS_SQL + ' FROM public.photos p WHERE p.id=%s'


def read_localisation(connection, identifier, raw=None, *, business_crs=None):
    business_crs = business_crs or read_business_crs(connection)
    if raw is None:
        row = connection.execute(READ_SQL,(identifier,)).fetchone()
        if not row: fail(404,'photo-not-found','Photo introuvable.')
        raw = row[0]
    result = dict(raw)
    warnings = []
    for field in ('geometry', 'position_debut', 'position_fin'):
        stored_srid = result.pop(f'_{field}_srid', None)
        if result.get(field) is not None and stored_srid != business_crs.srid:
            raise WebDatabaseError(
                "Une géométrie Photo ne correspond pas au CRS métier de la base."
            )
    result['srid'] = business_crs.srid
    result['crs'] = business_crs_payload(business_crs)
    result['geometry_4326'] = None
    if result['geometry_valid']:
        try:
            result['geometry_4326'] = connection.execute(
                'SELECT ST_AsGeoJSON(ST_Transform('
                'ST_SetSRID(ST_GeomFromGeoJSON(%s),%s),4326))::jsonb',
                (json.dumps(result['geometry']), business_crs.srid),
            ).fetchone()[0]
        except (psycopg.DataError, psycopg.InternalError):
            warnings.append('GEOMETRIE_NON_TRANSFORMABLE')
    result['troncon_id'] = None
    result['systeme_reperage_libelle'] = None
    raw = result['systeme_reperage_id']; system_id = uuid_or_none(raw)
    system = connection.execute('SELECT id,troncon_id,libelle,valid FROM systemes_reperage WHERE id=%s',(system_id,)).fetchone() if system_id else None
    if system:
        result.update(systeme_reperage_id=str(system[0]),troncon_id=str(system[1]),systeme_reperage_libelle=system[2])
        if not system[3]: warnings.append('SYSTEME_INVALIDE')
    elif raw: warnings.append('SYSTEME_NON_RESOLU')
    for end in ('debut','fin'):
        identifier = uuid_or_none(result[f'borne_{end}_id'])
        borne = connection.execute('SELECT id,libelle,valid FROM bornes_reperage WHERE id=%s',(identifier,)).fetchone() if identifier else None
        result[f'borne_{end}_id'] = str(borne[0]) if borne else None
        result[f'borne_{end}_libelle'] = borne[1] if borne else None
        distance = result[f'borne_{end}_distance']; aval = result[f'borne_{end}_aval']
        result[f'position_{end}_relative'] = None if distance is None else 'SUR_BORNE' if distance == 0 else None if aval is None else 'AVANT_BORNE' if aval else 'APRES_BORNE'
        if result[f'borne_{end}_id'] and not borne: warnings.append(f'BORNE_{end.upper()}_NON_RESOLUE')
        if borne and (not borne[2] or not system or not connection.execute('SELECT 1 FROM public.systemes_reperage__bornes WHERE systeme_reperage_id=%s AND borne_id=%s AND valid',(system_id,identifier)).fetchone()):
            warnings.append(f'BORNE_{end.upper()}_INCOMPATIBLE')
    geometry = result['geometry']
    result['remplacement_requis'] = bool((geometry and geometry['type'] != 'Point') or result['position_fin'] or result['borne_fin_id'] or result['pr_fin'] is not None or result['borne_fin_distance'] is not None or result['borne_fin_aval'] is not None)
    if geometry and geometry['type'] == 'Point' and result['position_debut'] and result['position_debut'] != geometry:
        result['remplacement_requis'] = True
        warnings.append('POSITION_DEBUT_DIFFERENTE_GEOMETRIE')
    if geometry and geometry['type'] != 'Point': warnings.append('GEOMETRIE_HISTORIQUE_NON_PONCTUELLE')
    if result['geometry_valid'] is False:
        result['remplacement_requis'] = True
        warnings.append('GEOMETRIE_HISTORIQUE_INVALIDE')
    if not geometry and (result['position_debut'] or result['position_fin']): warnings.append('POSITIONS_HISTORIQUES_SANS_GEOMETRIE')
    if result['position_debut'] and result['position_fin'] and result['position_debut'] != result['position_fin']: warnings.append('EXTREMITES_HISTORIQUES_DIFFERENTES')
    result['warnings'] = warnings
    result['convention'] = 'AVANT_APRES_SENS_GEOMETRIQUE'
    return result


CONTEXT_SQL = """
SELECT DISTINCT t.id,t.libelle FROM photos p JOIN observations o ON o.id=p.observation_id
JOIN troncons t ON t.id IN (
 SELECT o.troncon_id UNION SELECT o.berge_id UNION SELECT o.structure_digue_id UNION SELECT troncon_id FROM public.desordres__troncons WHERE desordre_id=o.desordre_id
 UNION SELECT troncon_id FROM public.cheminements__troncons WHERE cheminement_id=o.cheminement_id
 UNION SELECT troncon_id FROM equipements_mesure WHERE id=o.equipement_mesure_id
 UNION SELECT troncon_id FROM mobilier WHERE id=o.mobilier_id
 UNION SELECT troncon_id FROM reseaux_techniques WHERE id=o.reseau_technique_id
 UNION SELECT troncon_id FROM ouvrages_particuliers WHERE id=o.ouvrage_particulier_id
 UNION SELECT troncon_id FROM ouvrages_hydrauliques WHERE id=o.ouvrage_hydraulique_id
 UNION SELECT id FROM public.troncons WHERE amenagement_hydraulique_id=o.amenagement_hydraulique_id
 UNION SELECT troncon_id FROM public.vegetation__troncons WHERE vegetation_id=o.vegetation_id
 UNION SELECT at.id FROM public.vegetation__amenagements_hydrauliques va
 JOIN public.troncons at ON at.amenagement_hydraulique_id=va.amenagement_hydraulique_id
 WHERE va.vegetation_id=o.vegetation_id
) WHERE p.id=%s ORDER BY t.libelle,t.id
"""


def options(connection, photo_id, troncon_id):
    if not connection.execute('SELECT 1 FROM photos WHERE id=%s',(photo_id,)).fetchone(): fail(404,'photo-not-found','Photo introuvable.')
    contexts = [{'id':str(row[0]),'designation':row[1] or str(row[0])} for row in connection.execute(CONTEXT_SQL,(photo_id,)).fetchall()]
    systems = []
    if troncon_id:
        for identifier,label in connection.execute('SELECT id,libelle FROM systemes_reperage WHERE troncon_id=%s AND valid ORDER BY libelle,id',(troncon_id,)).fetchall():
            bornes = connection.execute('SELECT b.id,b.libelle,l.valeur_pr FROM public.systemes_reperage__bornes l JOIN bornes_reperage b ON b.id=l.borne_id WHERE l.systeme_reperage_id=%s AND l.valid AND b.valid ORDER BY l.valeur_pr,b.id',(identifier,)).fetchall()
            systems.append({'id':str(identifier),'designation':label or str(identifier),'bornes':[{'id':str(b[0]),'designation':b[1] or str(b[0]),'pr':float(b[2])} for b in bornes]})
    return {'context_troncons':contexts,'systemes':systems}


def engine(connection, command, point=None, *, business_crs):
    if not connection.execute('SELECT 1 FROM troncons WHERE id=%s AND valid FOR SHARE',(command.troncon_id,)).fetchone():
        fail(422,'photo-troncon-invalid','Tronçon absent ou invalide.')
    system = connection.execute('SELECT 1 FROM systemes_reperage WHERE id=%s AND troncon_id=%s AND valid FOR SHARE',(command.systeme_reperage_id,command.troncon_id)).fetchone()
    if not system: fail(422,'photo-system-invalid','Le système est absent, invalide ou appartient à un autre tronçon.')
    # Lock references while calculating/writing; core readers intentionally allow
    # historical valid=false rows, but new Photo commands may not use them.
    references = connection.execute('SELECT b.id,b.valid,l.valid FROM public.systemes_reperage__bornes l JOIN bornes_reperage b ON b.id=l.borne_id WHERE l.systeme_reperage_id=%s FOR SHARE OF b,l',(command.systeme_reperage_id,)).fetchall()
    if not references or any(not b[1] or not b[2] for b in references): fail(422,'photo-references-invalid','Le système contient des références absentes ou invalides.')
    if command.mode == 'bornage':
        offset = -command.distance_m if command.position_relative == 'AVANT_BORNE' else command.distance_m
        sql='SELECT to_jsonb(r)-\'point_xy\' AS values, ST_AsGeoJSON(point_xy)::jsonb FROM borne_offset_vers_xy(%s,%s,%s,%s) r'
        params=(command.troncon_id,command.systeme_reperage_id,command.borne_id,offset)
    elif command.mode == 'pr':
        sql="SELECT to_jsonb(r)-'point_xy' AS values, ST_AsGeoJSON(point_xy)::jsonb FROM pr_vers_xy(%s,%s,%s::numeric) r"
        params=(command.troncon_id,command.systeme_reperage_id,command.pr)
    else:
        sql="SELECT to_jsonb(r)-'point_source'-'point_projete' AS values, ST_AsGeoJSON(point_source)::jsonb FROM xy_vers_reperage(%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),%s)) r"
        params=(command.troncon_id,command.systeme_reperage_id,json.dumps(point),business_crs.srid)
    row = connection.execute(sql,params).fetchone(); result, location = row
    if result['statut'] != 'OK': fail(422,'photo-reperage-'+result['statut'].lower(),'Repérage refusé : '+result['statut'])
    if result.get('statut_pr','OK') != 'OK': fail(422,'photo-pr-unavailable','Le système ne permet pas un calcul de PR cohérent.')
    # Use the calibrated segment's explicit first borne, including midpoints.
    if command.mode == 'pr':
        offset = result['abscisse_m'] - result['abscisse_0_m']
        bornage = command.model_copy(update={'mode':'bornage', 'borne_id':UUID(result['borne_0_id']),
            'distance_m':abs(offset), 'position_relative':'AVANT_BORNE' if offset < 0 else 'APRES_BORNE' if offset > 0 else 'SUR_BORNE'})
        return engine(connection,bornage,business_crs=business_crs)
    return result, location


def write_localisation(connection, identifier, command, reference_revision=None, *, context=None):
    business_crs = read_business_crs(connection)
    if command.mode == 'point' and command.srid not in (4326, business_crs.srid):
        fail(
            422,
            'photo-srid-mismatch',
            f'Le SRID projeté doit être {business_crs.code} pour cette base.',
        )
    existing = read_localisation(
        connection, identifier, business_crs=business_crs
    )
    if reference_revision:
        check_revision(connection,command.troncon_id,command.systeme_reperage_id,reference_revision)
    if existing['remplacement_requis'] and not command.remplacer_historique:
        fail(422,'photo-historical-confirmation','Confirmez explicitement le remplacement du repérage historique complet.')
    point = None; result = None
    if command.mode == 'point':
        try:
            row = connection.execute(
                'SELECT ST_AsGeoJSON(ST_Transform('
                'ST_SetSRID(ST_Point(%s,%s),%s),%s))::jsonb',
                (*command.coordinates, command.srid, business_crs.srid),
            ).fetchone()
            point = row[0]
        except psycopg.Error:
            fail(
                422,
                'photo-coordinates-invalid',
                f'Coordonnées non transformables en {business_crs.code}.',
            )
    if command.mode == 'abscisse':
        if not connection.execute('SELECT 1 FROM public.troncons WHERE id=%s AND valid FOR SHARE',(command.troncon_id,)).fetchone():
            fail(422,'photo-troncon-invalid','Tronçon absent ou invalide.')
        from .reperage import Calculation,calculate
        if reference_revision is None:
            fail(428,'photo-reference-revision-required','La révision observée de l’axe est obligatoire.')
        computed=calculate(connection,command.troncon_id,Calculation(mode='abscisse',abscisse_m=command.abscisse_m))
        point=computed['geometry']
    if command.systeme_reperage_id:
        result, point = engine(
            connection, command, point, business_crs=business_crs
        )
    if point:
        valid = connection.execute(
            "SELECT ST_IsValid(g) AND NOT ST_IsEmpty(g) "
            "AND GeometryType(g)='POINT' AND ST_SRID(g)=%s "
            "FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),%s) g) s",
            (business_crs.srid, json.dumps(point), business_crs.srid),
        ).fetchone()[0]
        if not valid: fail(422,'photo-geometry-invalid','Position du photographe invalide.')
    if result and result.get('pr') is not None:
        pr_value = float(result['pr'])
        if not math.isfinite(pr_value) or abs(pr_value) > 3.4028234663852886e38:
            fail(422,'photo-pr-overflow','PR non représentable dans le champ historique REAL.')
    borne = result.get('borne_id') if result else None
    distance = result.get('distance_m',result.get('distance_borne_m')) if result else None
    position = result.get('position_relative') if result else None
    connection.execute("""UPDATE photos SET geometry=ST_SetSRID(ST_GeomFromGeoJSON(%s),%s),
       position_debut=ST_SetSRID(ST_GeomFromGeoJSON(%s),%s), position_fin=NULL,
       systeme_reperage_id=%s, borne_debut_id=%s,
       borne_debut_aval=%s, borne_debut_distance=%s, pr_debut=%s,
       borne_fin_id=NULL,borne_fin_aval=NULL,borne_fin_distance=NULL,pr_fin=NULL WHERE id=%s""",
       (json.dumps(point) if point else None,business_crs.srid,
        json.dumps(point) if point else None,business_crs.srid,
        str(command.systeme_reperage_id) if command.systeme_reperage_id else None,
        str(borne) if borne else None,position=='AVANT_BORNE' if position else None,distance,result.get('pr') if result else None,identifier))
    from .photos import read_photo
    return read_photo(connection,identifier,context=context)


def register_routes(app, *, contextual=False):
    from .media_target import media_route_dependencies
    prefix = '/api/bases/{business_database_id}' if contextual else '/api'
    _, read_connection = media_route_dependencies(contextual, 'business.read')
    update_context, update_connection = media_route_dependencies(contextual, 'business.update', write=True)
    @app.get(prefix + '/photos/{photo_id}/localisation-options')
    def read_options(photo_id:UUID,troncon_id:UUID|None=None,connection=Depends(read_connection)):
        return options(connection,photo_id,troncon_id)

    @app.put(prefix + '/photos/{photo_id}/localisation')
    def update(photo_id:UUID,command:PhotoLocalisationUpdate,if_match:str|None=Header(default=None),
               operation_id:str=Header(alias='Operation-Id'),reference_revision:str|None=Header(default=None,alias='Reference-Revision'),
               origin=Depends(mutation_origin),connection=Depends(update_connection),context=Depends(update_context)):
        if reference_revision is not None:
            if not re.fullmatch(r'[0-9a-f]{64}',reference_revision) or (command.systeme_reperage_id is None and command.mode!='abscisse'):
                fail(422,'photo-reference-revision-invalid','Empreinte de repérage invalide ou système absent.')
        changes = {'localisation':command.model_dump(mode='json')}
        if reference_revision is not None: changes['referenceRevision'] = reference_revision
        return execute_mutation(connection,'photos','update',lambda:write_localisation(connection,photo_id,command,reference_revision,context=context),
          identifier=photo_id,if_match=if_match,operation_id=operation_id,origin=origin,
          changes=changes)
