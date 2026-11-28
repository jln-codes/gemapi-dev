#!/usr/bin/env python3
"""Reproduce accepted demonstration intersections from their canonical GeoPackage (manual accepted fractions).

Normal mode regenerates only SQL. --accept-local PORT fills the AH section layer
from a disposable local PostGIS database, using the existing candidate/validation
functions. Never accepts a remote host or invents qualification.
"""
import argparse
from pathlib import Path
from uuid import UUID,uuid5,NAMESPACE_URL
from osgeo import ogr,osr

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'gpkg/ah.gpkg'
SQL=ROOT/'data/12_ah_sections.sql'
PARENT='d2d53b23-fb49-3e5b-a152-85458ad2e5ca'
RIVER='90368380-5d42-4a28-9889-6ae657f84a75'
COMMENT='DEMO-AH-01 : intersection acceptée ; sens fictif conventionnel, ordre des sommets = amont vers aval. Aucune observation de terrain.'

def quote(value):
    return "'"+str(value).replace("'","''")+"'"

def accept_local(port, database, schema, user):
    import psycopg2
    if not PACKAGE.exists():raise RuntimeError('GeoPackage AH absent')
    current=ogr.Open(str(PACKAGE),0)
    existing=current.GetLayerByName('ah_sections')
    if existing is not None and existing.GetFeatureCount():
        raise RuntimeError('Section AH déjà présente : contrôler avant de la remplacer')
    existing=None;current=None
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
    from install import identifier, trusted_path
    if not database or not database.startswith('demo_'):
        raise ValueError("Une base jetable demo_* explicite est requise")
    target = identifier(schema).replace('%', '%%')
    with psycopg2.connect(host='127.0.0.1',port=port,dbname=database,user=user) as db,db.cursor() as c:
        c.execute(trusted_path(schema))
        c.execute('SELECT cours_eau_id::text,debut_fraction,fin_fraction,encode(ST_AsEWKB(geom_reference),\'hex\') '
                  f'FROM {target}.ah_sections_candidates(%s) WHERE cours_eau_id=%s',(PARENT,RIVER))
        candidates=c.fetchall()
        if len(candidates)!=1:raise RuntimeError('Une intersection admissible complète attendue')
        course,start,end,geometry=candidates[0]
        uid=str(uuid5(NAMESPACE_URL,'sig-prestations/DEMO-AH-01/'+PARENT+'/'+course+'/'+geometry))
        c.execute(f'INSERT INTO {target}.ah_sections(id,ah_id,cours_eau_id,debut_fraction,fin_fraction,commentaire) '
                  'VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(uid,PARENT,course,start,end,COMMENT))
        c.execute('SELECT id::text,ah_id::text,cours_eau_id::text,debut_fraction,fin_fraction,'
                  'geom_cours_eau_signature,revision_validation,commentaire,'
                  f'ST_AsBinary(geom_reference) FROM {target}.ah_sections WHERE id=%s',(uid,))
        row=c.fetchone()
    ds=ogr.Open(str(PACKAGE),1);srs=osr.SpatialReference();srs.ImportFromEPSG(3950)
    layer=ds.GetLayerByName('ah_sections')
    if layer is None:
        layer=ds.CreateLayer('ah_sections',srs,ogr.wkbLineString,options=['GEOMETRY_NAME=geom_reference'])
    fields=[('id',ogr.OFTString),('ah_id',ogr.OFTString),('cours_eau_id',ogr.OFTString),
            ('debut_fraction',ogr.OFTReal),('fin_fraction',ogr.OFTReal),('geom_cours_eau_signature',ogr.OFTString),
            ('revision_validation',ogr.OFTInteger),('commentaire',ogr.OFTString)]
    for name,kind in fields:
        if layer.FindFieldIndex(name,True)<0:layer.CreateField(ogr.FieldDefn(name,kind))
    f=ogr.Feature(layer.GetLayerDefn())
    for (name,_),value in zip(fields,row):f.SetField(name,value)
    f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(row[-1])))
    if layer.CreateFeature(f)!=0:raise RuntimeError('Écriture GeoPackage refusée')
    ds=None

def main():
    ogr.UseExceptions()
    parser=argparse.ArgumentParser();parser.add_argument('--accept-local',type=int)
    parser.add_argument('--database')
    parser.add_argument('--schema',default='gemapi')
    parser.add_argument('--user')
    args=parser.parse_args()
    if args.accept_local:accept_local(args.accept_local, args.database, args.schema, args.user)
    ds=ogr.Open(str(PACKAGE));layer=ds.GetLayerByName('ah_sections')
    if layer.GetSpatialRef().GetAuthorityCode(None)!='3950' or layer.GetFeatureCount()!=1:
        raise RuntimeError('Une référence de démonstration EPSG:3950 attendue')
    lines=['-- DEMO-AH-01 : fractions acceptées conservées, géométrie calculée côté serveur.',
           'BEGIN;',"SELECT pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,gemapi',n.nspname),true) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='postgis';",
           'LOCK TABLE gemapi.cours_eau IN SHARE MODE;']
    for f in layer:
        uid,ah,river=(str(UUID(f[x])) for x in ('id','ah_id','cours_eau_id'))
        if (ah,river)!=(PARENT,RIVER):raise RuntimeError('Référence hors scénario accepté')
        # Restore accepted manual fractions; PostgreSQL alone computes the line.
        geom=f.GetGeometryRef().ExportToWkb().hex()
        lines.append(f"""DO $accepted$
DECLARE candidate record;
BEGIN
 SELECT {f['debut_fraction']!r}::double precision AS debut_fraction,
        {f['fin_fraction']!r}::double precision AS fin_fraction INTO candidate
 FROM gemapi.cours_eau r WHERE r.id='{river}'
 AND gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide)
 AND md5(encode(ST_AsEWKB(r.geom),'hex'))={quote(f['geom_cours_eau_signature'])}
 AND ST_AsBinary(ST_LineSubstring(ST_GeometryN(r.geom,1),{f['debut_fraction']!r},{f['fin_fraction']!r}))=decode('{geom}','hex');
 IF NOT FOUND THEN RAISE EXCEPTION 'Sources modifiées ou non qualifiées : référence DEMO-AH-01 refusée'; END IF;
 INSERT INTO gemapi.ah_sections(id,ah_id,cours_eau_id,debut_fraction,fin_fraction,commentaire)
 VALUES ('{uid}','{ah}','{river}',candidate.debut_fraction,candidate.fin_fraction,{quote(f['commentaire'])})
 ON CONFLICT(id) DO NOTHING;
 IF NOT EXISTS(SELECT FROM gemapi.ah_sections_carte WHERE id='{uid}' AND ah_id='{ah}'
 AND cours_eau_id='{river}' AND etat_reference='CONFORME'
 AND geom_cours_eau_signature={quote(f['geom_cours_eau_signature'])}
 AND debut_fraction=candidate.debut_fraction AND fin_fraction=candidate.fin_fraction
 AND ST_AsBinary(geom_reference)=decode('{geom}','hex'))
 THEN RAISE EXCEPTION 'Référence DEMO-AH-01 existante incompatible'; END IF;
END $accepted$;""")
    SQL.write_text('\n'.join([*lines,'COMMIT;','']),encoding='utf-8')
    print('Une référence acceptée :',SQL)

if __name__=='__main__':main()
