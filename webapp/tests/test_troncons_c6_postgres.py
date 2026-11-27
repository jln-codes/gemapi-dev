"""C6, level B (~1 min): fresh 3950/2154 databases, exports and offline readers.

Each case owns UUIDs. Native EWKB, typed observation roles, explicit parents,
unique inventory and negative hidden/missing references are checked with real
PostGIS and GDAL, never against an operator DSN. No mutable global cache.
"""
import json
import sqlite3
import unittest
from uuid import uuid4
from io import BytesIO
from fastapi import HTTPException
from pypdf import PdfReader
from webapp.tests import test_reperage_c4_postgres as harness
from digues_webapp.offline_projection import read_scope, read_pinned
from digues_webapp.data_views import execute_view_export
from digues_webapp.geopackage import build_geopackage
from digues_webapp.models import DataViewFieldSelection
from digues_app.reports.catalog import load_patrimoine_catalog, render_catalog_pdf
from digues_app.reports.repository import load_disorder_sheet
from psycopg import sql, errors
from digues_app.control.ai_policy import AI_SELECT_COLUMNS, validate_ai_select_security
from digues_webapp.schema_context import introspect_ai_schema
from digues_webapp.queries import fetch_troncon


class CanonicalConsumersPostgresTest(unittest.TestCase):
    setUpClass = classmethod(harness.TransversalReperagePostgresTest.setUpClass.__func__)
    db = harness.TransversalReperagePostgresTest.db

    def test_real_ai_column_role_never_inherits_sensitive_canonical_columns(self):
        # Effective PostgreSQL grants, not a mocked SQL allow/deny parser.
        role = 'digues_c6_ai_' + uuid4().hex[:12]
        self.admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        try:
            for srid in (3950,2154):
                with self.subTest(srid=srid), self.db(srid) as c:
                    try:
                        for schema in ('public','referentiel','technique'):
                            c.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
                        for table, columns in AI_SELECT_COLUMNS.items():
                            c.execute(sql.SQL('GRANT SELECT ({}) ON {} TO {}').format(
                                sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(*table.split('.')),sql.Identifier(role)))
                        c.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
                        validate_ai_select_security(c.cursor())
                        visible,_,_ = introspect_ai_schema(c)
                        root_columns={row[3] for row in visible if row[:2]==('public','troncons')}
                        self.assertEqual(root_columns,set(AI_SELECT_COLUMNS['public.troncons']))
                        c.execute('SELECT id,libelle,geometry FROM public.troncons LIMIT 1')
                        for column in ('type_physique_id','amenagement_hydraulique_id','commentaire','created_by','edited_by','geometry_reperage'):
                            with self.assertRaises(errors.InsufficientPrivilege):
                                c.execute(sql.SQL('SELECT {} FROM public.troncons LIMIT 1').format(sql.Identifier(column)))
                        with self.assertRaises(errors.InsufficientPrivilege): c.execute('SELECT * FROM public.troncons')
                        c.execute('SET default_transaction_read_only=off')
                        with self.assertRaises(errors.InsufficientPrivilege): c.execute("UPDATE public.troncons SET libelle='Interdit'")
                    finally:
                        c.execute('RESET ROLE')
                        c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
        finally:
            self.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    def fixture(self, srid):
        ids = {key: uuid4() for key in ('se','digue','ah','disorder','obs','photo')}
        shapes = ('LINESTRING ZM(2 48 3 7,2.001 48.001 4 8)',
                  'LINESTRING(2 48,2.001 48.001)', 'POINT(2 48)',
                  'POLYGON((2 48,2.001 48,2.001 48.001,2 48))',
                  'MULTILINESTRING((2 48,2.001 48.001),(2.01 48,2.011 48.001))',
                  'CIRCULARSTRING(2 48,2.001 48.001,2.002 48)',
                  'LINESTRING(2 48,2.001 48.001)')
        roots = {kind: uuid4() for kind in harness.TYPES}
        with self.db(srid) as c:
            c.execute("INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'C6 SE',true)", (ids['se'],))
            c.execute("INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) VALUES (%s,%s,'C6 Digue',true)", (ids['digue'],ids['se']))
            c.execute("INSERT INTO public.amenagements_hydrauliques(id,libelle,valid) VALUES (%s,'C6 AH',true)", (ids['ah'],))
            for index, (kind, key) in enumerate(roots.items()):
                c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,digue_id,amenagement_hydraulique_id,geometry,valid) "
                          "VALUES (%s,%s,%s,%s,%s,ST_Transform(ST_GeomFromText(%s,4326),%s),%s)",
                          (key,kind,f'C6 {index} {kind}',ids['digue'] if index == 0 else None,
                           ids['ah'] if index < 2 else None, shapes[index],srid,index != 5))
            c.execute("UPDATE public.troncons SET hauteur_murette=12.75 WHERE id=%s", (roots['BERGE'],))
            c.execute("INSERT INTO public.desordres(id,designation,geometry,valid) VALUES (%s,'C6 désordre',ST_Transform(ST_GeomFromText('POINT(2 48)',4326),%s),true)", (ids['disorder'],srid))
            for key in roots.values():
                c.execute("INSERT INTO public.desordres__troncons(desordre_id,troncon_id) VALUES (%s,%s)", (ids['disorder'],key))
            c.execute("INSERT INTO public.observations(id,berge_id,designation,valid) VALUES (%s,%s,'C6 historique typée',true)", (ids['obs'],roots['BERGE']))
            c.execute("INSERT INTO public.photos(id,observation_id,geometry,valid) VALUES (%s,%s,ST_Transform(ST_GeomFromText('POINT(2 48)',4326),%s),true)", (ids['photo'],ids['obs'],srid))
        return ids, roots

    def test_exports_keep_seven_types_exact_native_geometry_and_deduplicate_dual_parent(self):
        fields = [DataViewFieldSelection(table='troncons',field=f) for f in
                  ('libelle','type_physique_id','digue_id','amenagement_hydraulique_id')]
        for srid in (3950,2154):
            with self.subTest(srid=srid):
                ids, roots = self.fixture(srid)
                with self.db(srid) as c:
                    before = {str(key): bytes(wkb).hex() for key,wkb in c.execute(
                        'SELECT id,ST_AsEWKB(geometry) FROM public.troncons WHERE id=ANY(%s::uuid[])', (list(roots.values()),))}
                    all_ids = [key for key, in c.execute('SELECT id FROM public.troncons')]
                    hidden = list(set(all_ids)-set(roots.values()))
                    result = execute_view_export(c,'troncons',fields,hidden,include_native_troncons=True)
                    self.assertEqual(result['total'],7)
                    curve = next(e for row in result['rows'] for e in row['entities'] if str(e['id']) == str(roots['RISBERME']))
                    self.assertIsNone(curve['geometry'])
                    self.assertEqual(curve['attributes']['geometry_type'],'CIRCULARSTRING')
                    package = build_geopackage(result,['troncons'])
                    # Native-only curve is exportable without a fictitious line.
                    curve_only = {**result,'rows':[row for row in result['rows'] if any(e.get('id') == str(roots['RISBERME']) for e in row['entities'])]}
                    curve_package = build_geopackage(curve_only,['troncons'])
                    after = {str(key): bytes(wkb).hex() for key,wkb in c.execute(
                        'SELECT id,ST_AsEWKB(geometry) FROM public.troncons WHERE id=ANY(%s::uuid[])', (list(roots.values()),))}
                self.assertEqual(before,after)
                with sqlite3.connect(':memory:') as exported:
                    exported.deserialize(package)
                    inventory = exported.execute('SELECT id_objet,attributs,geometry_ewkb FROM troncons_inventaire').fetchall()
                    self.assertEqual(len(inventory),7)
                    self.assertEqual({key:wkb for key,_,wkb in inventory},before)
                    values = {key:json.loads(attrs) for key,attrs,_ in inventory}
                    self.assertEqual(values[str(roots['INDETERMINE'])]['digue_id'],str(ids['digue']))
                    self.assertEqual(values[str(roots['INDETERMINE'])]['amenagement_hydraulique_id'],str(ids['ah']))
                    self.assertEqual(values[str(roots['BERGE'])]['hauteur_murette'],12.75)
                    self.assertTrue(all('created_by' not in value and 'edited_by' not in value for value in values.values()))
                    self.assertEqual(exported.execute('SELECT count(*) FROM vue_resultats').fetchone()[0],7)
                with sqlite3.connect(':memory:') as exported:
                    exported.deserialize(curve_package)
                    self.assertEqual(exported.execute('SELECT count(*) FROM troncons_inventaire').fetchone()[0],1)

    def test_offline_optional_parents_typed_observations_and_missing_reference(self):
        for srid in (3950,2154):
            with self.subTest(srid=srid):
                ids,roots=self.fixture(srid)
                with self.db(srid) as c:
                    before=c.execute('SELECT version,ST_AsEWKB(geometry) FROM public.photos WHERE id=%s',(ids['photo'],)).fetchone()
                    snapshot=read_scope(c,list(roots.values()))
                    self.assertEqual(set(snapshot['tronconIds']),{str(key) for key in roots.values()})
                    observed=next(row['payload'] for row in snapshot['records'] if row['id']==str(ids['obs']))
                    self.assertEqual(observed['parent']['resource'],'berges')
                    self.assertEqual(observed['parent']['canonical_resource'],'troncons')
                    self.assertTrue(observed['offline_read_only']);self.assertNotIn('photos',observed)
                    self.assertEqual(len([row for row in snapshot['records'] if row['resource']=='troncons']),7)
                    pinned=read_pinned(c,'observations',ids['obs'])
                    self.assertTrue(any(row['id']==str(roots['BERGE']) for row in pinned['records']))
                    self.assertEqual(c.execute('SELECT count(*) FROM public.systemes_reperage WHERE troncon_id=ANY(%s::uuid[])',(list(roots.values()),)).fetchone()[0],0)
                    with self.assertRaises(HTTPException) as missing: read_scope(c,[uuid4()])
                    self.assertEqual(missing.exception.status_code,404)
                    self.assertEqual(c.execute('SELECT version,ST_AsEWKB(geometry) FROM public.photos WHERE id=%s',(ids['photo'],)).fetchone(),before)

    def test_catalogues_use_explicit_ah_and_se_relations_without_double_counting(self):
        for srid in (3950,2154):
            with self.subTest(srid=srid):
                ids,roots=self.fixture(srid)
                with self.db(srid) as c:
                    for scope,key in [('amenagement_hydraulique',ids['ah']),('systeme',ids['se']),('digue',ids['digue']),
                                      *[('troncon',key) for key in roots.values()]]:
                        catalog=load_patrimoine_catalog(c,scope,key)
                        self.assertEqual(catalog.entry_count,1)
                        self.assertEqual(next(catalog.iter_entries()).disorder_id,str(ids['disorder']))
                        pdf=render_catalog_pdf(c,catalog)
                        self.assertEqual(len(PdfReader(BytesIO(pdf)).pages),1)
                    # A ZM section must remain a readable map copy, never mutate
                    # the authoritative physical geometry while rendering PDF.
                    sheet=load_disorder_sheet(c,ids['disorder'])
                    self.assertIsNotNone(sheet.map_context)
                    self.assertIsNotNone(sheet.map_context.section_line)

    def test_unknown_srid_is_diagnosed_and_exported_natively_without_invented_map(self):
        key,disorder=uuid4(),uuid4()
        fields=[DataViewFieldSelection(table='troncons',field='libelle')]
        with self.db() as c:
            c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,geometry,valid) VALUES (%s,'INDETERMINE','SCR historique absent',ST_GeomFromText('LINESTRING ZM(1 2 3 4,5 6 7 8)',0),true)",(key,))
            c.execute('INSERT INTO public.desordres(id,valid) VALUES (%s,true)',(disorder,))
            c.execute('INSERT INTO public.desordres__troncons(desordre_id,troncon_id) VALUES (%s,%s)',(disorder,key))
            raw=bytes(c.execute('SELECT ST_AsEWKB(geometry) FROM public.troncons WHERE id=%s',(key,)).fetchone()[0]).hex()
            sheet=load_disorder_sheet(c,disorder)
            self.assertIsNone(sheet.map_context.section_line)
            hidden=[identifier for identifier, in c.execute('SELECT id FROM public.troncons WHERE id<>%s',(key,))]
            result=execute_view_export(c,'troncons',fields,hidden,include_native_troncons=True)
            entity=result['rows'][0]['entities'][0]
            self.assertIsNone(entity['geometry']);self.assertEqual(entity['attributes']['geometry_srid'],0)
            with sqlite3.connect(':memory:') as exported:
                exported.deserialize(build_geopackage(result,['troncons']))
                self.assertEqual(exported.execute('SELECT geometry_ewkb FROM troncons_inventaire').fetchone()[0],raw)

    def test_empty_point_keeps_physical_identity_without_invalid_leaflet_coordinates(self):
        key=uuid4()
        with self.db() as c:
            c.execute("INSERT INTO public.troncons(id,type_physique_id,geometry,valid) VALUES (%s,'BERGE',ST_GeomFromText('POINT EMPTY',3950),false)",(key,))
            before=bytes(c.execute('SELECT ST_AsEWKB(geometry) FROM public.troncons WHERE id=%s',(key,)).fetchone()[0])
            feature=fetch_troncon(c,key)
            self.assertIsNone(feature['geometry'])
            self.assertEqual(feature['properties']['geometry_type'],'POINT')
            self.assertTrue(feature['properties']['geometry_vide'])
            self.assertFalse(feature['properties']['geometry_geojson_supportee'])
            self.assertEqual(bytes(c.execute('SELECT ST_AsEWKB(geometry) FROM public.troncons WHERE id=%s',(key,)).fetchone()[0]),before)
