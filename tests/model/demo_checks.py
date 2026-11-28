MEASURES = ('nombre_points', 'longueur_m', 'surface_m2', 'surface_ha',
            'type_geometrie', 'type_geometrie_postgis', 'possede_geometrie', 'origine_mesures')


def check_database(query):
    """Use independent geometry constants, not the resolver as its own oracle."""
    assert query("SELECT count(*) FROM information_schema.columns WHERE table_schema='gemapi' "
                 "AND table_name='prestations' AND column_name IN (" +
                 ','.join("'"+n+"'" for n in MEASURES) + ');') == '8'
    assert query("SELECT is_generated FROM information_schema.columns WHERE table_schema='gemapi' "
                 "AND table_name='prestations' AND column_name='surface_ha'") == 'ALWAYS'
    assert query("SELECT count(*) FROM gemapi.prestations WHERE "
                 "possede_geometrie IS DISTINCT FROM true OR "
                 "origine_mesures IS DISTINCT FROM CASE WHEN geom IS NULL THEN "
                 "'Patrimoine de rattachement' ELSE 'Prestation' END") == '0'
    query(r"""
BEGIN;
DO $test$
DECLARE p gemapi.prestations%ROWTYPE; v record; zone uuid; old_trace timestamptz;
BEGIN
 FOR v IN SELECT * FROM (VALUES
   ('POINT(0 0)',1,NULL::float8,NULL::float8,'Point','ST_Point'),
   ('MULTIPOINT((0 0),(2 2))',2,NULL,NULL,'Point','ST_MultiPoint'),
   ('LINESTRING(0 0,3 4)',NULL,5,NULL,'Ligne','ST_LineString'),
   ('MULTILINESTRING((0 0,3 4),(20 20,20 30))',NULL,15,NULL,'Ligne','ST_MultiLineString'),
   ('POLYGON((0 0,2 0,2 3,0 3,0 0))',NULL,NULL,6,'Polygone','ST_Polygon'),
   ('MULTIPOLYGON(((0 0,2 0,2 3,0 3,0 0)),((10 0,12 0,12 3,10 3,10 0)))',NULL,NULL,12,'Polygone','ST_MultiPolygon')
 ) x(wkt,n,l,a,famille,genre)
 LOOP
   SELECT * INTO p FROM gemapi.prestations WHERE ah_stockage_id IS NOT NULL LIMIT 1;
   UPDATE gemapi.prestations SET geom=ST_GeomFromText(v.wkt,3950),quantite=123.45,unite='déclarée'
   WHERE id=p.id RETURNING * INTO p;
   IF ROW(p.nombre_points,p.longueur_m,p.surface_m2,p.type_geometrie,p.type_geometrie_postgis,p.origine_mesures)
      IS DISTINCT FROM ROW(v.n,v.l,v.a,v.famille,v.genre,'Prestation'::text)
      OR p.surface_ha IS DISTINCT FROM v.a/10000.0 OR p.quantite<>123.45 OR p.unite<>'déclarée'
   THEN RAISE EXCEPTION 'Mesure propre complète incorrecte pour %',v.wkt; END IF;
 END LOOP;
 SELECT * INTO p FROM gemapi.prestations WHERE vegetation_id IS NOT NULL LIMIT 1;
 zone:=p.vegetation_id;old_trace:=p.date_modification;
 UPDATE gemapi.vegetation SET geometry=ST_GeomFromText('MULTIPOINT((0 0),(100 100))',3950) WHERE id=zone;
 SELECT * INTO p FROM gemapi.prestations WHERE id=p.id;
 IF p.nombre_points<>2 OR p.origine_mesures<>'Patrimoine de rattachement'
    OR p.date_modification IS DISTINCT FROM old_trace OR p.geom IS NOT NULL
 THEN RAISE EXCEPTION 'Actualisation parentale ou traçabilité incorrecte'; END IF;
 UPDATE gemapi.prestations SET nombre_points=999,surface_m2=999,origine_mesures='Injection' WHERE id=p.id;
 SELECT * INTO p FROM gemapi.prestations WHERE id=p.id;
 IF p.nombre_points<>2 OR p.surface_m2 IS NOT NULL OR p.origine_mesures<>'Patrimoine de rattachement'
    OR p.date_modification IS DISTINCT FROM old_trace
 THEN RAISE EXCEPTION 'Mesures modifiables manuellement'; END IF;
 UPDATE gemapi.vegetation SET geometry=ST_GeomFromText('MULTILINESTRING((0 0,3 4),(20 20,20 30))',3950) WHERE id=zone;
 SELECT * INTO p FROM gemapi.prestations WHERE id=p.id;
 IF p.longueur_m<>15 OR p.nombre_points IS NOT NULL THEN RAISE EXCEPTION 'Longueur parentale'; END IF;
 UPDATE gemapi.vegetation SET geometry=ST_GeomFromText('MULTIPOLYGON(((0 0,2 0,2 3,0 3,0 0)),((10 0,12 0,12 3,10 3,10 0)))',3950) WHERE id=zone;
 SELECT * INTO p FROM gemapi.prestations WHERE id=p.id;
 IF p.surface_m2<>12 OR p.surface_ha<>0.0012 OR p.longueur_m IS NOT NULL THEN RAISE EXCEPTION 'Surface parentale'; END IF;
END $test$;
ROLLBACK;
""")
