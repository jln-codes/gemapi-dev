-- DEMO-AH-01 : fractions acceptées conservées, géométrie calculée côté serveur.
BEGIN;
SELECT pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,gemapi',n.nspname),true) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='postgis';
LOCK TABLE gemapi.cours_eau IN SHARE MODE;
DO $accepted$
DECLARE candidate record;
BEGIN
 SELECT 0.0::double precision AS debut_fraction,
        0.7022160987813123::double precision AS fin_fraction INTO candidate
 FROM gemapi.cours_eau r WHERE r.id='90368380-5d42-4a28-9889-6ae657f84a75'
 AND gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide)
 AND md5(encode(ST_AsEWKB(r.geom),'hex'))='6d1ac6e31e49d6cb3cee38e78717803d'
 AND ST_AsBinary(ST_LineSubstring(ST_GeometryN(r.geom,1),0.0,0.7022160987813123))=decode('01020000000e0000006efba63e37d43941fa542a64be9c614183a2a04733d43941451bf157cb9c6141e119d5e02cd43941a5808a34d89c6141cce4693521d4394126a007dfe49c61418f2998430ed43941605e5144f19c6141901f1022f3d339416371bc5bfd9c6141bfa07b1fd0d33941df1a3528099d61411f6fdeb7a6d33941c519dab7149d6141df7e436179d3394141791422209d6141a5b9a5364bd33941ef2b6e842b9d6141842d928c1fd339416cc394fe369d614124c4037ef9d23941523212ae429d6141b1710581dbd23941d0834faa4e9d61417dd3abd2dad23941a28998134f9d6141','hex');
 IF NOT FOUND THEN RAISE EXCEPTION 'Sources modifiées ou non qualifiées : référence DEMO-AH-01 refusée'; END IF;
 INSERT INTO gemapi.ah_sections(id,ah_id,cours_eau_id,debut_fraction,fin_fraction,commentaire)
 VALUES ('bf9304f0-cc53-56e9-bad8-1dc06fc71db5','d2d53b23-fb49-3e5b-a152-85458ad2e5ca','90368380-5d42-4a28-9889-6ae657f84a75',candidate.debut_fraction,candidate.fin_fraction,'DEMO-AH-01 : intersection acceptée ; sens fictif conventionnel, ordre des sommets = amont vers aval. Aucune observation de terrain.')
 ON CONFLICT(id) DO NOTHING;
 IF NOT EXISTS(SELECT FROM gemapi.ah_sections_carte WHERE id='bf9304f0-cc53-56e9-bad8-1dc06fc71db5' AND ah_id='d2d53b23-fb49-3e5b-a152-85458ad2e5ca'
 AND cours_eau_id='90368380-5d42-4a28-9889-6ae657f84a75' AND etat_reference='CONFORME'
 AND geom_cours_eau_signature='6d1ac6e31e49d6cb3cee38e78717803d'
 AND debut_fraction=candidate.debut_fraction AND fin_fraction=candidate.fin_fraction
 AND ST_AsBinary(geom_reference)=decode('01020000000e0000006efba63e37d43941fa542a64be9c614183a2a04733d43941451bf157cb9c6141e119d5e02cd43941a5808a34d89c6141cce4693521d4394126a007dfe49c61418f2998430ed43941605e5144f19c6141901f1022f3d339416371bc5bfd9c6141bfa07b1fd0d33941df1a3528099d61411f6fdeb7a6d33941c519dab7149d6141df7e436179d3394141791422209d6141a5b9a5364bd33941ef2b6e842b9d6141842d928c1fd339416cc394fe369d614124c4037ef9d23941523212ae429d6141b1710581dbd23941d0834faa4e9d61417dd3abd2dad23941a28998134f9d6141','hex'))
 THEN RAISE EXCEPTION 'Référence DEMO-AH-01 existante incompatible'; END IF;
END $accepted$;
COMMIT;
