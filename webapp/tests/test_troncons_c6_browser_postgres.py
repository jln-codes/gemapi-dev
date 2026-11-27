"""C6 level B (~3 min): C5 live contract plus peripheral consumers on fresh SQL.

The inherited single scenario is counted once. Browser calls authorized real
backend/database routes; only print basemap tiles use a private synthetic image.
Local stores/context are disposable; no global tile cache is cleared.
"""
from webapp.tests import test_troncons_c5_browser_postgres as harness
import psycopg
from uuid import uuid4

class CanonicalConsumersBrowserTest(harness.CanonicalTronconBrowserTest):
    artifact_root = 'audits/troncons-c6/browser'
    browser_environment = {'DIGUES_C6_BROWSER':'1'}

    def extend_fixture(self, fixture):
        fixture['empty']=str(uuid4())
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[0]}) as c:
            c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,geometry,valid) VALUES (%s,'BERGE','C6 vide historique',ST_GeomFromText('POINT EMPTY',3950),false)",(fixture['empty'],))
