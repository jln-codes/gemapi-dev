"""Native referencing capability of a canonical Tronçon (all seven types).

Systems/occurrences have immutable identities. Retiring an association preserves
its row and historical users. Reference edits never recalculate stored positions.
The root version, a graph revision and the versions of shared owners protect
these new connected writes; no offline/idempotent Tronçon protocol is added.
"""
from datetime import date
import hashlib
import json
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException
from psycopg import DataError, IntegrityError, InternalError, sql
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool, model_validator

from .database import business_crs_payload, read_business_crs
from .resource_journal import mutation_origin
from .resource_mutations import execute_mutation
from .troncons import version_conflict


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')


Coordinate = Annotated[FiniteFloat, Field(strict=True)]


class Axis(Command):
    srid: int = Field(strict=True)
    coordinates: list[tuple[Coordinate, Coordinate]] = Field(min_length=2, max_length=10000)


class SystemFields(Command):
    libelle: str | None = None
    commentaire: str | None = None
    valid: StrictBool | None = None


class SystemCreate(SystemFields):
    action: Literal['creer_systeme']
    id: UUID = Field(default_factory=uuid4)
    valid: StrictBool = True


class SystemUpdate(SystemFields):
    action: Literal['modifier_systeme']
    id: UUID


class BorneFields(SystemFields):
    fictive: StrictBool | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    srid: int | None = Field(default=None, strict=True)
    coordinates: tuple[Coordinate, Coordinate] | None = None

    @model_validator(mode='after')
    def coordinate_pair(self):
        if ('coordinates' in self.model_fields_set) != ('srid' in self.model_fields_set):
            raise ValueError('coordinates et srid sont fournis ensemble.')
        if self.coordinates is not None and self.srid is None:
            raise ValueError('Le SRID des coordonnées est obligatoire.')
        return self


class BorneCreate(BorneFields):
    action: Literal['creer_borne']
    id: UUID = Field(default_factory=uuid4)
    valid: StrictBool = True


class BorneUpdate(BorneFields):
    action: Literal['modifier_borne']
    id: UUID


class LinkFields(Command):
    valeur_pr: Decimal | None = None
    ordre_borne: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    valid: StrictBool | None = None


class LinkCreate(LinkFields):
    action: Literal['lier_borne']
    id: UUID = Field(default_factory=uuid4)
    systeme_reperage_id: UUID
    borne_id: UUID
    valid: StrictBool = True


class LinkUpdate(LinkFields):
    action: Literal['modifier_liaison']
    id: UUID


class Retire(Command):
    action: Literal['retirer_liaison', 'retirer_systeme']
    id: UUID


Action = Annotated[SystemCreate | SystemUpdate | BorneCreate | BorneUpdate | LinkCreate | LinkUpdate | Retire,
                   Field(discriminator='action')]


class ReperageUpdate(Command):
    reference_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    troncon_versions: dict[UUID, Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]] = Field(default_factory=dict)
    actions: list[Action] = Field(default_factory=list, max_length=100)
    axe: Axis | None = None
    inverser_axe: StrictBool = False
    systeme_reperage_defaut_id: UUID | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if not self.actions and not self.inverser_axe and not ({'axe','systeme_reperage_defaut_id'} & self.model_fields_set):
            raise ValueError('Une modification explicite est obligatoire.')
        if self.inverser_axe and 'axe' in self.model_fields_set:
            raise ValueError('Définir ou inverser un axe, une seule opération à la fois.')
        for action in self.actions:
            if isinstance(action,(SystemUpdate,BorneUpdate,LinkUpdate)) and not (action.model_fields_set-{'id','action'}):
                raise ValueError('Une modification de référence doit fournir ses champs.')
        return self


# One SQL statement gives a consistent graph, including shared owners, even
# when the read connection is READ COMMITTED. EWKB remains private to hashing.
STATE_SQL = """
WITH root AS (SELECT id,version,systeme_reperage_defaut_id,geometry_reperage,
    coalesce(geometry_reperage,geometry) axis FROM public.troncons WHERE id=%s),
 systems AS (SELECT s.* FROM public.systemes_reperage s JOIN root ON root.id=s.troncon_id),
 links AS (SELECT l.* FROM public.systemes_reperage__bornes l JOIN systems s ON s.id=l.systeme_reperage_id),
 bornes AS (SELECT b.* FROM public.bornes_reperage b WHERE b.id IN (SELECT borne_id FROM links)
    OR b.id IN (SELECT tb.borne_id FROM public.troncons_bornes tb JOIN root ON root.id=tb.troncon_id)),
 owners AS (SELECT DISTINCT t.id,t.version FROM public.troncons t WHERE
    t.id IN (SELECT s.troncon_id FROM public.systemes_reperage s JOIN public.systemes_reperage__bornes l
             ON l.systeme_reperage_id=s.id WHERE l.borne_id IN (SELECT id FROM bornes))
    OR t.id IN (SELECT tb.troncon_id FROM public.troncons_bornes tb WHERE tb.borne_id IN (SELECT id FROM bornes)))
SELECT jsonb_build_object('id',root.id,'version',root.version,
 'systeme_reperage_defaut_id',root.systeme_reperage_defaut_id,
 'axe',jsonb_build_object('source',CASE WHEN geometry_reperage IS NOT NULL THEN 'EXPLICITE' ELSE 'PHYSIQUE' END,
    'type',GeometryType(axis),'srid',ST_SRID(axis),'_ewkb',encode(ST_AsEWKB(axis),'hex'),
    'geometry',CASE WHEN GeometryType(axis)='LINESTRING' AND ST_IsValid(axis) AND ST_Length(axis)::text NOT IN ('NaN','Infinity','-Infinity') THEN ST_AsGeoJSON(axis)::jsonb ELSE NULL END,
    'longueur_m',CASE WHEN GeometryType(axis)='LINESTRING' AND ST_Length(axis)::text NOT IN ('NaN','Infinity','-Infinity') THEN ST_Length(axis) ELSE NULL END,
    'fermee',CASE WHEN GeometryType(axis)='LINESTRING' THEN ST_IsClosed(axis) ELSE NULL END,
    'simple',CASE WHEN GeometryType(axis)='LINESTRING' THEN ST_IsSimple(axis) ELSE NULL END,
    'exploitable',CASE WHEN GeometryType(axis)='LINESTRING' THEN coalesce(ST_SRID(axis)=%s AND NOT ST_IsEmpty(axis)
        AND ST_IsValid(axis) AND ST_Length(axis)>0 AND ST_Length(axis)::text NOT IN ('NaN','Infinity','-Infinity'),false) ELSE false END),
 'systemes',coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM systems s),'[]'::jsonb),
 'liaisons',coalesce((SELECT jsonb_agg(to_jsonb(l) || jsonb_build_object('valeur_pr',l.valeur_pr::text) ORDER BY id) FROM links l),'[]'::jsonb),
 'bornes',coalesce((SELECT jsonb_agg(jsonb_build_object('id',b.id,'libelle',b.libelle,'commentaire',b.commentaire,
    'fictive',b.fictive,'date_debut',b.date_debut,'date_fin',b.date_fin,'valid',b.valid,
    'srid',ST_SRID(b.geometry),'_ewkb',encode(ST_AsEWKB(b.geometry),'hex'),
    'geometry',CASE WHEN GeometryType(b.geometry)='POINT' THEN ST_AsGeoJSON(b.geometry)::jsonb ELSE NULL END)
    ORDER BY b.id) FROM bornes b),'[]'::jsonb),
 'troncon_versions',coalesce((SELECT jsonb_object_agg(id,version) FROM owners),'{}'::jsonb)) FROM root
"""


def read_state(connection, identifier):
    business_crs=read_business_crs(connection)
    row=connection.execute(STATE_SQL,(identifier,business_crs.srid)).fetchone()
    if not row:raise HTTPException(404,'Tronçon absent de cette base.')
    state=row[0]
    state['reference_revision']=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    state['crs']=business_crs_payload(business_crs)
    state['axe'].pop('_ewkb')
    for borne in state['bornes']:borne.pop('_ewkb')
    if not state['axe']['exploitable']:
        state['axe']['diagnostic']='AXE_ABSENT_OU_INEXPLOITABLE : fournir une LineString explicite dans le CRS métier ; aucune forme déduite.'
    if state['axe']['exploitable']:
        points=state['axe']['geometry']['coordinates']
        state['reperes_virtuels']=[{'role':'DEBUT','abscisse_m':0,'coordinates':points[0][:2]},
            {'role':'FIN','abscisse_m':state['axe']['longueur_m'],'coordinates':points[-1][:2]}]
    else:state['reperes_virtuels']=[]
    state['reperage_implicite_disponible']=state['axe']['exploitable']
    # Omission of an explicit reference always requests curvilinear abscissa.
    # PR calibration is never silently substituted for geometric distance.
    state['priorite_references']='IMPLICITE_PAR_DEFAUT ; DEFAUT ou EXPLICITE sur demande pour les PR calibrés.'
    state['troncon_versions'][str(identifier)]=state['version']
    return state


def dependents(connection, system_ids, borne_ids, link_ids):
    """Enumerate actual FK users, not inferred spatial relationships."""
    result=[]
    for table,ids in (('systemes_reperage',system_ids),('bornes_reperage',borne_ids),('systemes_reperage__bornes',link_ids)):
        if not ids:continue
        rows=connection.execute("""SELECT DISTINCT cl.relname,a.attname,
          CASE WHEN EXISTS (SELECT 1 FROM pg_attribute x WHERE x.attrelid=cl.oid AND x.attname='id' AND NOT x.attisdropped)
          THEN 'id' ELSE a.attname END
          FROM pg_constraint c JOIN pg_class cl ON cl.oid=c.conrelid
          JOIN pg_namespace ns ON ns.oid=cl.relnamespace
          CROSS JOIN LATERAL unnest(c.conkey,c.confkey) pair(child,parent)
          JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=pair.child
          JOIN pg_attribute p ON p.attrelid=c.confrelid AND p.attnum=pair.parent
          WHERE c.contype='f' AND ns.nspname='public' AND c.confrelid=%s::regclass AND p.attname='id'
          ORDER BY 1,2""",('public.'+table,)).fetchall()
        for child,column,key in rows:
            found=connection.execute(sql.SQL('SELECT DISTINCT {} FROM public.{} WHERE {}=ANY(%s::uuid[]) LIMIT 501')
                .format(sql.Identifier(key),sql.Identifier(child),sql.Identifier(column)),(list(ids),)).fetchall()
            if len(found)>500:raise HTTPException(422,'Trop de dépendances : préparer une opération de portée réduite.')
            if found:result.append({'table':child,'relation':column,'id_column':key,'ids':[str(r[0]) for r in found]})
    return result


def _coordinates(connection, coordinates, srid, kind):
    business_srid=read_business_crs(connection).srid
    if srid not in (4326,business_srid):raise HTTPException(422,'SRID incompatible avec cette base.')
    geometry={'type':kind,'coordinates':coordinates}
    points=coordinates if kind=='LineString' else [coordinates]
    if srid!=4326 and any(abs(v)>100_000_000 for point in points for v in point):
        raise HTTPException(422,'Coordonnées projetées hors limites techniques (100 millions de mètres).')
    if srid==4326:
        points=coordinates if kind=='LineString' else [coordinates]
        if any(not -180<=p[0]<=180 or not -90<=p[1]<=90 for p in points):
            raise HTTPException(422,'Coordonnées hors domaine EPSG:4326.')
    try:
        with connection.transaction():
            row=connection.execute('SELECT ST_AsEWKB(g),NOT ST_IsEmpty(g) AND ST_IsValid(g) AND '
                '(GeometryType(g)=\'POINT\' OR ST_Length(g)>0) FROM '
                '(SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),%s),%s) g) geom',
                (json.dumps(geometry),srid,business_srid)).fetchone()
    except (DataError,InternalError) as exc:
        raise HTTPException(422,'Coordonnées non transformables dans le CRS métier ; aucune position inventée.') from exc
    if not row[1]:raise HTTPException(422,'Axe ou borne géométriquement invalide.')
    return row[0]


def _patch(connection, table, action, allowed):
    values=action.model_dump(exclude_unset=True,exclude={'id','action','srid','coordinates'})
    if not set(values)<=set(allowed):raise ValueError('Unexpected reference field')
    expressions={key:sql.Placeholder() for key in values}
    params=list(values.values())
    if isinstance(action,BorneFields) and 'coordinates' in action.model_fields_set:
        expressions['geometry']=sql.SQL('ST_GeomFromEWKB(%s)')
        params.append(_coordinates(connection,action.coordinates,action.srid,'Point') if action.coordinates else None)
    if expressions:
        connection.execute(sql.SQL('UPDATE public.{} SET {} WHERE id=%s').format(sql.Identifier(table),
            sql.SQL(',').join(sql.SQL('{}={}').format(sql.Identifier(k),e) for k,e in expressions.items())),(*params,action.id))


def write_state(connection, identifier, command):
    try:
        with connection.transaction():
            # All native reference writers and Photo/Disorder mutations share
            # the family lock acquired before business rows by execute_mutation.
            connection.execute('LOCK TABLE public.systemes_reperage, public.systemes_reperage__bornes, '
                               'public.bornes_reperage, public.troncons_bornes IN SHARE ROW EXCLUSIVE MODE')
            before=read_state(connection,identifier)
            if before['reference_revision']!=command.reference_revision:
                raise HTTPException(409,{'code':'reperage-references-changed','resource':'troncons','id':str(identifier),
                    'currentVersion':before['version'],'message':'Les références ont changé. Conservez la saisie et relisez le repérage.'})
            borne_ids={UUID(b['id']) for b in before['bornes']}
            edited={a.id for a in command.actions if isinstance(a,BorneUpdate)}
            if edited-borne_ids:raise HTTPException(422,'Modifier seulement une borne explicitement associée à ce Tronçon.')
            affected={identifier}
            if edited:
                owners=connection.execute('SELECT DISTINCT s.troncon_id FROM public.systemes_reperage s '
                    'JOIN public.systemes_reperage__bornes l ON l.systeme_reperage_id=s.id WHERE l.borne_id=ANY(%s::uuid[]) '
                    'UNION SELECT troncon_id FROM public.troncons_bornes WHERE borne_id=ANY(%s::uuid[])',
                    (list(edited),list(edited))).fetchall()
                affected.update(r[0] for r in owners if r[0] is not None)
            rows=connection.execute('SELECT id,version FROM public.troncons WHERE id=ANY(%s::uuid[]) ORDER BY id FOR UPDATE',
                                    (list(affected),)).fetchall()
            if len(rows)>200:raise HTTPException(422,'Trop de Tronçons partagent ces références.')
            for key,version in rows:
                if key==identifier:continue
                expected=command.troncon_versions.get(key)
                if expected is None:raise HTTPException(428,{'code':'shared-reference-version-required','id':str(key),
                    'message':'Fournir les versions observées de tous les Tronçons partageant la borne.'})
                if version!=expected:version_conflict(key,expected,version)
            systems={UUID(s['id']) for s in before['systemes']}
            links={UUID(l['id']) for l in before['liaisons']}
            created_bornes=set()
            for action in command.actions:
                if isinstance(action,SystemCreate):
                    connection.execute('INSERT INTO public.systemes_reperage(id,troncon_id,libelle,commentaire,valid) VALUES (%s,%s,%s,%s,%s)',
                        (action.id,identifier,action.libelle,action.commentaire,action.valid));systems.add(action.id)
                elif isinstance(action,SystemUpdate):
                    if action.id not in systems:raise HTTPException(422,'Système étranger ou absent.')
                    previous=next((row for row in before['systemes'] if row['id']==str(action.id)),None)
                    if (previous and previous['valid'] is True and 'valid' in action.model_fields_set and action.valid is not True
                            and str(action.id)==str(before['systeme_reperage_defaut_id'])
                            and 'systeme_reperage_defaut_id' not in command.model_fields_set):
                        raise HTTPException(422,'Choisir ou retirer explicitement le défaut avant de désactiver ce système.')
                    _patch(connection,'systemes_reperage',action,('libelle','commentaire','valid'))
                elif isinstance(action,BorneCreate):
                    geometry=_coordinates(connection,action.coordinates,action.srid,'Point') if action.coordinates else None
                    connection.execute('INSERT INTO public.bornes_reperage(id,libelle,commentaire,fictive,date_debut,date_fin,valid,geometry) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,ST_GeomFromEWKB(%s))',
                        (action.id,action.libelle,action.commentaire,action.fictive,action.date_debut,action.date_fin,action.valid,geometry))
                    created_bornes.add(action.id)
                elif isinstance(action,BorneUpdate):
                    _patch(connection,'bornes_reperage',action,('libelle','commentaire','fictive','date_debut','date_fin','valid'))
                elif isinstance(action,LinkCreate):
                    if action.systeme_reperage_id not in systems:raise HTTPException(422,'Système étranger ou absent.')
                    if not connection.execute('SELECT 1 FROM public.bornes_reperage WHERE id=%s',(action.borne_id,)).fetchone():
                        raise HTTPException(422,'Borne absente de cette base.')
                    connection.execute('INSERT INTO public.systemes_reperage__bornes(id,systeme_reperage_id,borne_id,valeur_pr,ordre_borne,valid) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',(action.id,action.systeme_reperage_id,action.borne_id,action.valeur_pr,action.ordre_borne,action.valid))
                    connection.execute('INSERT INTO public.troncons_bornes(troncon_id,borne_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',(identifier,action.borne_id))
                    links.add(action.id);created_bornes.discard(action.borne_id)
                elif isinstance(action,LinkUpdate):
                    if action.id not in links:raise HTTPException(422,'Occurrence de borne étrangère ou absente.')
                    _patch(connection,'systemes_reperage__bornes',action,('valeur_pr','ordre_borne','valid'))
                elif isinstance(action,Retire):
                    table,allowed=('systemes_reperage',systems) if action.action=='retirer_systeme' else ('systemes_reperage__bornes',links)
                    if action.id not in allowed:raise HTTPException(422,'Association étrangère ou absente.')
                    connection.execute(sql.SQL('UPDATE public.{} SET valid=false WHERE id=%s').format(sql.Identifier(table)),(action.id,))
                    if table=='systemes_reperage' and str(action.id)==str(before['systeme_reperage_defaut_id']) and 'systeme_reperage_defaut_id' not in command.model_fields_set:
                        raise HTTPException(422,'Retirer explicitement le système par défaut ou en choisir un autre.')
            if created_bornes:raise HTTPException(422,'Une borne créée doit être liée explicitement à un système dans la même commande.')
            fields=['version=version'];params=[]
            if 'systeme_reperage_defaut_id' in command.model_fields_set:
                chosen=command.systeme_reperage_defaut_id
                if chosen is not None and not connection.execute('SELECT 1 FROM public.systemes_reperage WHERE id=%s AND troncon_id=%s AND valid',
                                                                 (chosen,identifier)).fetchone():
                    raise HTTPException(422,'Le système par défaut doit être actif et appartenir au Tronçon.')
                fields.append('systeme_reperage_defaut_id=%s');params.append(chosen)
            if 'axe' in command.model_fields_set:
                fields.append('geometry_reperage=ST_GeomFromEWKB(%s)')
                params.append(_coordinates(connection,command.axe.coordinates,command.axe.srid,'LineString') if command.axe else None)
            if command.inverser_axe:
                if not before['axe']['exploitable']:raise HTTPException(422,'Aucun axe exploitable à inverser.')
                # Materialise only the existing explicit LineString or a validated
                # physical LineString. Physical geometry, including Z/M, stays intact.
                fields.append('geometry_reperage=ST_Reverse(ST_Force2D(coalesce(geometry_reperage,geometry)))')
            connection.execute('UPDATE public.troncons SET '+','.join(fields)+' WHERE id=%s',(*params,identifier))
            related={}
            for key in sorted(affected-{identifier},key=str):
                related[str(key)]=connection.execute('UPDATE public.troncons SET version=version WHERE id=%s RETURNING version',(key,)).fetchone()[0]
            connection.execute('SET CONSTRAINTS ALL IMMEDIATE')
            result=read_state(connection,identifier)
            dependent_systems=set(systems)
            if edited:
                dependent_systems.update(row[0] for row in connection.execute(
                    'SELECT DISTINCT systeme_reperage_id FROM public.systemes_reperage__bornes WHERE borne_id=ANY(%s::uuid[])',
                    (list(edited),)))
            dependent_links=set(links)
            dependent_links.update(row[0] for row in connection.execute(
                'SELECT id FROM public.systemes_reperage__bornes WHERE systeme_reperage_id=ANY(%s::uuid[])',
                (list(dependent_systems),)))
            result['localisations_dependantes']=dependents(connection,dependent_systems,borne_ids|edited,dependent_links)
            result['politique_localisations']='CONSERVEES_SANS_RECALCUL : vérifier explicitement les localisations dépendantes après changement de références.'
            if related:result['_related_mutations']={'troncons':related}
            return result
    except (DataError,IntegrityError) as exc:
        raise HTTPException(409 if getattr(exc,'sqlstate',None)=='23505' else 422,
            {'code':'reperage-constraint-invalid','message':'Référence, identité ou géométrie incompatible ; aucune modification enregistrée.'}) from exc


class Calculation(Command):
    mode: Literal['xy','abscisse','pr','bornage']
    reference: Literal['implicite','defaut','explicite'] = 'implicite'
    systeme_reperage_id: UUID | None = None
    abscisse_m: FiniteFloat | None = None
    coordinates: tuple[Coordinate,Coordinate] | None = None
    srid: int | None = Field(default=None,strict=True)
    pr: Decimal | None = None
    borne_id: UUID | None = None
    offset_m: FiniteFloat | None = None

    @model_validator(mode='after')
    def family(self):
        if self.reference=='explicite' and self.systeme_reperage_id is None:
            raise ValueError('Choisir un système explicite.')
        if self.reference!='explicite' and self.systeme_reperage_id is not None:
            raise ValueError('Le système est réservé au choix explicite.')
        if self.reference=='implicite' and self.mode in {'pr','bornage'}:
            raise ValueError('PR calibré et borne physique exigent une référence explicite ou par défaut.')
        if self.reference!='implicite' and self.mode=='abscisse':
            raise ValueError('L’abscisse curviligne appartient au repérage géométrique implicite.')
        required={'xy':{'coordinates','srid'},'abscisse':{'abscisse_m'},'pr':{'pr'},'bornage':{'borne_id','offset_m'}}[self.mode]
        supplied={k for k in self.model_fields_set-{'mode','reference','systeme_reperage_id'} if getattr(self,k) is not None}
        if supplied!=required:raise ValueError('Fournir exactement les données du mode de calcul.')
        return self


def calculate(connection, identifier, command):
    if command.reference=='implicite':
        return implicit_calculation(connection,identifier,command)
    state=read_state(connection,identifier)
    if not state['axe']['exploitable']:raise HTTPException(422,{'code':'reperage-axis-unavailable','message':state['axe']['diagnostic']})
    if command.reference=='defaut':
        chosen=state['systeme_reperage_defaut_id']
        command=command.model_copy(update={'systeme_reperage_id':UUID(chosen) if chosen else None})
    system=next((s for s in state['systemes'] if s['id']==str(command.systeme_reperage_id) and s['valid']),None)
    if not system:raise HTTPException(422,'Système absent, invalide ou étranger au Tronçon.')
    links=[l for l in state['liaisons'] if l['systeme_reperage_id']==str(command.systeme_reperage_id)]
    bornes={b['id']:b for b in state['bornes']}
    if not links or any(not l['valid'] or l['borne_id'] not in bornes or not bornes[l['borne_id']]['valid'] for l in links):
        raise HTTPException(422,'Le système contient des références absentes ou inactives.')
    if not state['axe']['simple'] or state['axe']['fermee']:
        raise HTTPException(422,{'code':'reperage-ambigu','message':'Le calibrage projeté exige un axe simple et ouvert ; le repérage curviligne reste disponible.'})
    if command.mode=='xy':
        point=_coordinates(connection,command.coordinates,command.srid,'Point')
        engine_sql="SELECT to_jsonb(r)-'point_source'-'point_projete',ST_AsGeoJSON(point_projete)::jsonb FROM public.xy_vers_reperage(%s,%s,ST_GeomFromEWKB(%s)) r"
        params=(identifier,command.systeme_reperage_id,point)
    elif command.mode=='pr':
        engine_sql="SELECT to_jsonb(r)-'point_xy',ST_AsGeoJSON(point_xy)::jsonb FROM public.pr_vers_xy(%s,%s,%s) r"
        params=(identifier,command.systeme_reperage_id,command.pr)
    else:
        engine_sql="SELECT to_jsonb(r)-'point_xy',ST_AsGeoJSON(point_xy)::jsonb FROM public.borne_offset_vers_xy(%s,%s,%s,%s) r"
        params=(identifier,command.systeme_reperage_id,command.borne_id,command.offset_m)
    active="""EXISTS (SELECT 1 FROM public.systemes_reperage s JOIN public.troncons t ON t.id=s.troncon_id
      WHERE s.id=%s AND t.id=%s AND s.valid AND CASE
      WHEN GeometryType(coalesce(t.geometry_reperage,t.geometry))='LINESTRING'
      THEN ST_IsSimple(coalesce(t.geometry_reperage,t.geometry)) AND NOT ST_IsClosed(coalesce(t.geometry_reperage,t.geometry))
      ELSE false END)
      AND EXISTS (SELECT 1 FROM public.systemes_reperage__bornes WHERE systeme_reperage_id=%s)
      AND NOT EXISTS (SELECT 1 FROM public.systemes_reperage__bornes l LEFT JOIN public.bornes_reperage b ON b.id=l.borne_id
        WHERE l.systeme_reperage_id=%s AND (l.valid IS NOT TRUE OR b.valid IS NOT TRUE))"""
    row=connection.execute('SELECT computed.*,('+active+') FROM ('+engine_sql+') computed',
        (command.systeme_reperage_id,identifier,command.systeme_reperage_id,command.systeme_reperage_id,*params)).fetchone()
    if not row[2]:raise HTTPException(422,'Références inactives ou axe non univoque ; aucune localisation calculée.')
    values,point=row[:2]
    if values.get('details',{}).get('references_valid_false',0):
        raise HTTPException(422,'Références de calibrage inactives ; aucune nouvelle localisation calculée.')
    if values['statut']!='OK':raise HTTPException(422,{'code':'reperage-'+values['statut'].lower(),'diagnostic':values})
    return {'troncon_id':str(identifier),'reference':command.reference,'srid':read_business_crs(connection).srid,'geometry':point,**values}


# All axis predicates and geometry below are evaluated in one SQL snapshot.
# This also works on the real READ ONLY connections (no row-lock writes).
IMPLICIT_AXIS_SQL = """WITH raw AS (SELECT id,version,coalesce(geometry_reperage,geometry) g
 FROM public.troncons WHERE id=%s),axis AS (SELECT *,
 CASE WHEN GeometryType(g)='LINESTRING' THEN ST_Length(g) ELSE NULL END length,
 CASE WHEN GeometryType(g)='LINESTRING' THEN ST_SRID(g)=%s AND NOT ST_IsEmpty(g)
 AND ST_IsValid(g) AND ST_Length(g)>0 AND ST_Length(g)::text NOT IN ('NaN','Infinity','-Infinity')
 ELSE false END usable FROM raw) """


def implicit_calculation(connection,identifier,command):
    srid=read_business_crs(connection).srid
    if command.mode=='abscisse':
        row=connection.execute(IMPLICIT_AXIS_SQL+"SELECT version,length,usable,"
            "CASE WHEN usable AND %s BETWEEN 0 AND length THEN "
            "ST_AsGeoJSON(ST_Force2D(ST_LineInterpolatePoint(g,%s/length)))::jsonb ELSE NULL END FROM axis",
            (identifier,srid,command.abscisse_m,command.abscisse_m)).fetchone()
        if not row:raise HTTPException(404,'Tronçon absent de cette base.')
        if not row[2]:raise HTTPException(422,{'code':'reperage-axis-unavailable','message':'Axe linéaire exploitable absent ; aucune ligne déduite du contour.'})
        if row[3] is None:raise HTTPException(422,{'code':'reperage-hors_domaine','longueur_m':row[1]})
        return {'troncon_id':str(identifier),'version':row[0],'reference':'implicite','statut':'OK',
            'abscisse_m':command.abscisse_m,'longueur_m':row[1],'srid':srid,'geometry':row[3]}
    point=_coordinates(connection,command.coordinates,command.srid,'Point')
    row=connection.execute(IMPLICIT_AXIS_SQL+",p AS (SELECT ST_GeomFromEWKB(%s) g),"
        "projected AS (SELECT axis.*,p.g point,CASE WHEN usable AND ST_IsSimple(axis.g) AND NOT ST_IsClosed(axis.g) "
        "THEN ST_LineLocatePoint(axis.g,p.g) END fraction FROM axis,p),"
        "segments AS (SELECT (ST_DumpSegments(g)).geom g FROM projected WHERE fraction IS NOT NULL),"
        "distances AS (SELECT ST_Distance(s.g,p.g) distance,ST_ClosestPoint(s.g,p.g) point FROM segments s,p),"
        "nearest AS (SELECT d.point FROM distances d WHERE d.distance<=(SELECT min(distance) FROM distances)+1e-8) "
        "SELECT version,length,usable,fraction,CASE WHEN fraction IS NOT NULL THEN "
        "(SELECT max(ST_LineLocatePoint(g,n.point)*length)-min(ST_LineLocatePoint(g,n.point)*length) FROM nearest n) END ambiguity,"
        "CASE WHEN fraction IS NOT NULL THEN ST_Distance(g,point) END,"
        "CASE WHEN fraction IS NOT NULL THEN ST_AsGeoJSON(ST_Force2D(ST_LineInterpolatePoint(g,fraction)))::jsonb END FROM projected",
        (identifier,srid,point)).fetchone()
    if not row:raise HTTPException(404,'Tronçon absent de cette base.')
    if not row[2]:raise HTTPException(422,{'code':'reperage-axis-unavailable','message':'Axe linéaire exploitable absent.'})
    if row[3] is None or row[4]>1e-8:
        raise HTTPException(422,{'code':'reperage-ambigu','message':'Projection XY non univoque ; fournir une abscisse orientée.'})
    return {'troncon_id':str(identifier),'version':row[0],'reference':'implicite','statut':'OK',
        'abscisse_m':row[3]*row[1],'longueur_m':row[1],'distance_axe_m':row[5],'geometry':row[6],'srid':srid}


def register_routes(app, *, contextual=False):
    from .media_target import media_route_dependencies
    prefix='/api/bases/{business_database_id}' if contextual else '/api'
    _,read=media_route_dependencies(contextual,'business.read')
    _,write=media_route_dependencies(contextual,'business.update',write=True)

    @app.get(prefix+'/troncons/{troncon_id}/reperage')
    def get(troncon_id:UUID,connection=Depends(read)):
        state=read_state(connection,troncon_id)
        # C5 must present affected localisations before a sensitive edit.
        # Include every system sharing an inventoried borne, not only this owner.
        bornes={UUID(b['id']) for b in state['bornes']}
        systems={UUID(s['id']) for s in state['systemes']}
        if bornes:
            systems.update(r[0] for r in connection.execute(
                'SELECT DISTINCT systeme_reperage_id FROM public.systemes_reperage__bornes WHERE borne_id=ANY(%s::uuid[])',
                (list(bornes),)))
        links={r[0] for r in connection.execute(
            'SELECT id FROM public.systemes_reperage__bornes WHERE systeme_reperage_id=ANY(%s::uuid[])',
            (list(systems),))}
        state['localisations_dependantes']=dependents(connection,systems,bornes,links)
        return state

    @app.put(prefix+'/troncons/{troncon_id}/reperage')
    def put(troncon_id:UUID,command:ReperageUpdate,if_match:str|None=Header(default=None),
            operation_id:str|None=Header(default=None,alias='Operation-Id'),origin=Depends(mutation_origin),connection=Depends(write)):
        return execute_mutation(connection,'troncons','update',lambda:write_state(connection,troncon_id,command),
            identifier=troncon_id,if_match=if_match,operation_id=operation_id,
            changes=command.model_dump(mode='json',exclude_unset=True),origin=origin)

    @app.post(prefix+'/troncons/{troncon_id}/reperage/calcul')
    def conversion(troncon_id:UUID,command:Calculation,connection=Depends(read)):
        return calculate(connection,troncon_id,command)
