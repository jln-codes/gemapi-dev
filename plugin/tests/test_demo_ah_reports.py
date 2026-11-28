"""DEMO-AH-01: permanent demo, fresh PostGIS, reports, no remote DSN.

QT_QPA_PLATFORM=offscreen DEMO_AH_PDFS=1 /usr/bin/python3 plugin/tests/test_demo_ah_reports.py
"""
import json,os,sys,unittest,unicodedata
from pathlib import Path
import test_ah_sections as section_tests
from test_postgres_integration import ROOT,command
from gemapi_app.report_data import build_report_query,prepare_rows,own_totals
from gemapi_app.report_maps import location
sys.path.insert(0,str(ROOT/'demo/data'))
from generer_ah_sections import PARENT,RIVER,PACKAGE

OUT=ROOT/'build/qgis-tests/demo_ah_01'
INDIRECT='d6dd30f8-d2d8-54bd-bc8d-6fd0f239139a'
STORAGE='19880be4-f81e-58a8-be6a-4b09b031fd31'
WORK='105e1779-6ed4-0c38-d125-0a9902a5cc83'

class DemoAhReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        section_tests.AhSections.setUpClass()
        cls.addClassCleanup(section_tests.AhSections.doClassCleanups)
        cls.db=section_tests.AhSections.connect();cls.addClassCleanup(cls.db.close)
        for p in sorted((ROOT/'demo/data').glob('[0-9][0-9]_*.sql')):
            if p.name<'14_':cls.q(p.read_text())
        cls.q((ROOT/'demo/data/digues.sql').read_text())
        cls.q((ROOT/'demo/data/14_patrimoine.sql').read_text())
        cls.q((ROOT/'demo/data/15_prestations.sql').read_text())
        cls.q('SET search_path=pg_catalog,extensions,gemapi')
        cls.db.commit()
        cls.before=cls.fingerprint()

    @classmethod
    def q(cls,sql,args=None):
        with cls.db.cursor() as c:
            c.execute(sql,args)
            return c.fetchall() if c.description else []

    @classmethod
    def fingerprint(cls):
        return cls.q("SELECT md5(string_agg(row_to_json(t)::text,'' ORDER BY t.id)) FROM gemapi.prestations t")[0][0]

    def rows(self,filters):
        rows=prepare_rows(self.q(build_report_query(filters,include_geometry=True)))
        self.assertEqual(len(rows),len({r['id'] for r in rows}))
        return rows

    def tearDown(self):
        self.db.rollback();self.assertEqual(self.fingerprint(),self.before)

    def test_relations_geometry_and_quantities(self):
        parent=self.rows({'ah':[PARENT]});storage=self.rows({'ah_stockage':[STORAGE]})
        work=self.rows({'ah_ouvrage':[WORK]});river=self.rows({'cours_eau':[RIVER]})
        global_rows=self.rows({})
        self.assertEqual([len(parent),len(storage),len(work),len(river),len(global_rows)],[11,8,1,1,100])
        self.assertIn(INDIRECT,{r['id'] for r in parent})
        self.assertNotIn(INDIRECT,{r['id'] for r in storage+work})
        self.assertEqual(river[0]['id'],INDIRECT)
        self.assertEqual(float(river[0]['quantite']),96)
        self.assertEqual(river[0]['unite'],'m²')
        self.assertEqual(location(river[0])[0][0],river[0]['own_wkt'])
        self.assertEqual(self.q('SELECT cours_eau_id::text,ah_stockage_id,ah_ouvrage_id FROM gemapi.prestations WHERE id=%s',(INDIRECT,))[0],(RIVER,None,None))
        # The only accepted section is the complete server-generated intersection.
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_sections_carte s JOIN gemapi.ah_stockage a ON a.ah_id=s.ah_id '
                               'JOIN gemapi.cours_eau r ON r.id=s.cours_eau_id WHERE etat_reference=\'CONFORME\' '
                               'AND ST_Equals(s.geom_reference,ST_Intersection(a.geom,r.geom))')[0][0],1)
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.cours_eau WHERE direction IS TRUE')[0][0],1)
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.cours_eau WHERE direction IS NULL')[0][0],57)

    def test_idempotent_sql_and_geopackage(self):
        from osgeo import ogr
        ds=ogr.Open(str(PACKAGE));f=next(iter(ds.GetLayerByName('ah_sections')))
        before=self.q('SELECT id::text,debut_fraction,fin_fraction,geom_cours_eau_signature,encode(ST_AsBinary(geom_reference),\'hex\'),revision_validation,valide_par,date_validation FROM gemapi.ah_sections')
        self.assertEqual(before[0][:5],(f['id'],f['debut_fraction'],f['fin_fraction'],f['geom_cours_eau_signature'],f.GetGeometryRef().ExportToWkb().hex()))
        self.q((ROOT/'demo/data/12_ah_sections.sql').read_text())
        self.q('SET search_path=pg_catalog,extensions,gemapi')
        self.assertEqual(self.q('SELECT id::text,debut_fraction,fin_fraction,geom_cours_eau_signature,encode(ST_AsBinary(geom_reference),\'hex\'),revision_validation,valide_par,date_validation FROM gemapi.ah_sections'),before)

    def test_import_refuses_changed_sources(self):
        import psycopg2
        self.q('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,1,0) WHERE id=%s',(RIVER,))
        with self.assertRaises(psycopg2.Error):
            self.q((ROOT/'demo/data/12_ah_sections.sql').read_text())
        self.db.rollback()
        self.q('SET search_path=pg_catalog,extensions,gemapi')
        self.assertEqual(self.q('SELECT etat_reference FROM gemapi.ah_sections_carte')[0][0],'CONFORME')

    @unittest.skipUnless(os.environ.get('DEMO_AH_PDFS')=='1','PDF generation opt-in')
    def test_reports_pdf(self):
        from qgis.core import QgsApplication,QgsVectorLayer,QgsFeature,QgsGeometry,QgsFillSymbol
        from gemapi_app.report_pdf import export_detailed_report
        from gemapi_app.report_synthetic_portrait import export_synthetic_portrait_report
        app=QgsApplication.instance() or QgsApplication([],True);app.initQgis()
        OUT.mkdir(parents=True,exist_ok=True)
        # Local storage context, no network basemap or real data.
        base=QgsVectorLayer('Polygon?crs=EPSG:3950','Stockage fictif','memory')
        wkt=self.q('SELECT ST_AsText(geom) FROM gemapi.ah_stockage WHERE id=%s',(STORAGE,))[0][0]
        f=QgsFeature();f.setGeometry(QgsGeometry.fromWkt(wkt));base.dataProvider().addFeatures([f]);base.updateExtents()
        base.renderer().setSymbol(QgsFillSymbol.createSimple({'color':'210,233,245,90','outline_color':'70,115,145','outline_width':'0.3'}))
        results={}
        cases=[('parent',{'ah':[PARENT]},11),('stockage',{'ah_stockage':[STORAGE]},8),
               ('ouvrage',{'ah_ouvrage':[WORK]},1),('cours-eau',{'cours_eau':[RIVER]},1),('global',{},100)]
        for name,filters,count in cases:
            rows=self.rows(filters)
            pdf=OUT/(name+'.pdf')
            if name=='global':
                export_synthetic_portrait_report(rows,{},str(pdf),basemap_layer=base)
            else:
                export_detailed_report(rows,str(pdf),export_dpi=90,_shared_basemap=base)
            text=unicodedata.normalize('NFKC',command('pdftotext','-layout',str(pdf),'-').stdout)
            self.assertNotRegex(text,r'(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
            if name!='global':self.assertEqual(len([p for p in text.split('\f') if p.strip()]),count)
            if name=='parent':self.assertIn('reconnaissance indirecte',text)
            if name in ('stockage','ouvrage'):self.assertNotIn('reconnaissance indirecte',text)
            results[name]={'count':len(rows),'uuids':[r['id'] for r in rows],
                           'own_measures':own_totals(rows),'quantities':[(r['id'],r['quantite'],r['unite']) for r in rows]}
        export_synthetic_portrait_report(self.rows({'ah':[PARENT]}),{'ah':[PARENT],'groups':['ah']},str(OUT/'parent-synthetique.pdf'),basemap_layer=base)
        (OUT/'resultats.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':unittest.main(verbosity=2)
