"""C4 narrow C2 regression: explicit bank memberships, counting and fidelity.

Level B (~30s), complete native transaction on an owned fresh database. A pair
substituted at unchanged cardinality must be detected independently of writers.
"""
from dataclasses import replace
import unittest
from uuid import UUID,uuid4
from tests import test_native_migration_prototype as harness
from digues_app.migration import native
from digues_app.migration.crs import CRSInfo
from digues_app.migration.core import prepare_core_migration
from digues_app.migration.source_inventory import digest
from digues_app.migration.fidelity import compare_projection_fields


class NativeBergeReperagePostgresTest(unittest.TestCase):
    def setUp(self):
        import os,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict,make_conninfo
        from tests.postgres_safety import connect_test_database,require_test_dsn
        from scripts.prototype_native_migration import initialize
        from digues_app.target.import_proof import ReceiptContext
        self.admin=self.enterContext(connect_test_database('DIGUES_MULTIBASE_TEST_ADMIN_DSN',kind='admin',autocommit=True))
        name='digues_occ_test_c4_native_'+uuid4().hex[:8]
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        self.addCleanup(lambda:self.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name))))
        dsn=make_conninfo(**{**conninfo_to_dict(require_test_dsn('DIGUES_OCC_TEST_DSN')),'dbname':name})
        self.enterContext(patch.dict(os.environ,DIGUES_DIRECT_TEST_DSN=dsn))
        folder=self.enterContext(tempfile.TemporaryDirectory(dir=Path('audits/troncons-c4')))
        self.data=harness.fixture()
        self.context=ReceiptContext(uuid4(),uuid4(),uuid4(),digest(self.data))
        self.directory=Path(folder)/str(self.context.job_id)
        with self.connect() as c:
            c.execute('CREATE EXTENSION postgis');c.execute('CREATE EXTENSION pgcrypto')
            self.contract=initialize(c,self.context.business_database_id,self.context.data_epoch)

    def connect(self):
        from tests.postgres_safety import connect_test_database
        return connect_test_database('DIGUES_DIRECT_TEST_DSN',autocommit=True)

    def test_explicit_bank_membership_native_counts_and_equal_count_substitution(self):
        a,b,system,b0,b1,b2=(uuid4() for _ in range(6))
        self.data['Berge']=[{'_id':a.hex,'geometry':'LINESTRING(0 0,100 0)','valid':True},
            {'_id':b.hex,'geometry':'LINESTRING(0 0,100 0)','valid':True,
             'borneIds':[b0.hex,b1.hex],'systemeRepDefautId':system.hex}]
        self.data['BorneDigue']=[{'_id':key.hex,'geometry':f'POINT({x} 0)','valid':True}
            for key,x in ((b0,0),(b1,100),(b2,50))]
        self.data['SystemeReperage']=[{'_id':system.hex,'linearId':b.hex,'valid':True,'systemeReperageBornes':[
            {'id':uuid4().hex,'borneId':key.hex,'valeurPR':pr,'valid':True} for key,pr in ((b0,0),(b1,100))]}]
        self.prepared=prepare_core_migration(self.data,historical_proofs=False)
        self.context=replace(self.context,source_sha256=digest(self.data))
        with self.connect() as c:
            report=native.migrate(c,self.prepared,self.data,self.directory,context=self.context,contract=self.contract,source_crs=CRSInfo(3950,3950))
            self.assertEqual({r[0] for r in c.execute('SELECT borne_id FROM public.troncons_bornes WHERE troncon_id=%s',(b,))},{b0,b1})
            self.assertEqual(c.execute('SELECT systeme_reperage_defaut_id FROM public.troncons WHERE id=%s',(b,)).fetchone()[0],system)
            self.assertEqual(c.execute('SELECT count(*) FROM public.systemes_reperage WHERE troncon_id=%s',(a,)).fetchone()[0],0)
            reader=native.SnapshotReader(self.data,self.prepared)
            reader.projection_diagnostics={(d['source_class'],d['source_id']):tuple(d['diagnostics']) for d in report['projection_diagnostics']}
            plan=native.business_plan(self.prepared)
            def comparisons():
                return compare_projection_fields(c,original=reader.original,outcomes=native.decisions(self.prepared),
                    outputs=plan.outputs,source_documents=self.data,expected_srid=3950,source_reader=reader,
                    observations=tuple({'table':s.table,'columns':s.key_columns} for s in plan.catalogue_state))
            before=[r for r in comparisons() if r.field=='$borneIds' and r.source_class=='Berge']
            self.assertEqual(len(before),2);self.assertTrue(all(r.status!='MISMATCH' for r in before))
            count=c.execute('SELECT count(*) FROM public.troncons_bornes').fetchone()[0]
            c.execute('UPDATE public.troncons_bornes SET borne_id=%s WHERE troncon_id=%s AND borne_id=%s',(b2,b,b0))
            self.assertEqual(c.execute('SELECT count(*) FROM public.troncons_bornes').fetchone()[0],count)
            self.assertTrue(any(r.reason=='BERGE_BORNE_MEMBERSHIP_DIVERGENT' and r.status=='MISMATCH' for r in comparisons()))
