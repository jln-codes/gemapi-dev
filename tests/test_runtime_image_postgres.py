"""RUN-11 B (~3–6 min, image prebuilt): actual wheel/runtime + owned PostGIS.

No development package is mounted in the container. Only synthetic source,
its private evidence and the guarded DisposableCluster socket are exposed.
Missing jsonschema must fail the smoke; full native commit and a separate
container's cold recovery exercise the installed fidelity validator.
"""
import json
import os
import unittest
from uuid import UUID

from digues_app.migration.source_inventory import digest
from tests.test_missing_geometry import fixture
from tests.runtime_image_harness import RuntimeImageEnvironment


RUNNER = r'''
import json,sys
def mark_phase(name):
    print('DIGUES_IMAGE_PHASE '+name,file=sys.stderr,flush=True)
mark_phase('startup')
from pathlib import Path
from uuid import UUID
import psycopg
from digues_app.migration.runtime_check import check
from digues_app.migration import native
from digues_app.migration.native_entry import migrate_core,report_directory
from digues_app.migration.import_worker import FrozenSIRSClient
from digues_app.source import CouchDBDatabaseInfo
from digues_app.target import PostgreSQLConfig
from digues_app.target.import_proof import ReceiptContext,read_import_receipt
p=json.load(sys.stdin)
ctx=ReceiptContext(UUID(p['database']),UUID(p['epoch']),UUID(p['job']),p['source_sha256'])
assert check()=='RUNTIME_MIGRATION_CONTRACTS_PASS'
with psycopg.connect(p['dsn'],autocommit=True) as c:
    if p['mode']=='migrate':
        mark_phase('migrate')
        client=FrozenSIRSClient('synthetic-image',CouchDBDatabaseInfo('synthetic-image',3950,None,None,True),p['documents'])
        report=migrate_core(source_client=client,business_connection=c,
            target_config=PostgreSQLConfig(dsn=p['dsn']),receipt_context=ctx)
        assert report.validation.complete
    mark_phase('recover')
    result=native.recover(c,report_directory(ctx.job_id)/'bilan-exemple.json',context=ctx,contract=p['contract'],with_provenance=True)
    for phase in ('postwrite_fidelity','transaction_execution','authenticated_report'):
        assert result['phases'][phase]['state']=='passed'
    receipt=read_import_receipt(c,ctx.business_database_id,ctx.data_epoch)
    assert receipt and receipt['job_id']==ctx.job_id
    assert c.execute('SELECT geometry,valid,famille_vegetation,disposition FROM public.vegetation WHERE id=%s',(UUID(int=97001),)).fetchone()==(None,False,None,None)
    assert c.execute('SELECT imported_at IS NOT NULL FROM technique.import_eligibility').fetchone()==(True,)
    print(json.dumps({'mode':p['mode'],'receipt_sha256':result['report_sha256'],'outcomes':result['outcomes'],'phases':result['phases']}))
'''


@unittest.skipUnless(os.environ.get('DIGUES_TEST_RUNTIME_IMAGE'), 'explicit local production image required')
class ProductionImageTest(unittest.TestCase):
    def test_final_image_smoke_negative_and_native_commit_cold_recovery(self):
        with RuntimeImageEnvironment(os.environ['DIGUES_TEST_RUNTIME_IMAGE']) as environment:
            self.check_image(environment)

    def check_image(self, environment):
        smoke=environment.docker(script='from digues_app.migration.runtime_check import check; print(check())')
        self.assertEqual(smoke.returncode,0,smoke.stderr)
        absent=environment.docker(script="import sys; sys.modules['jsonschema']=None; from digues_app.migration.runtime_check import check; check()")
        self.assertNotEqual(absent.returncode,0)
        self.assertIn('jsonschema',absent.stderr)
        deps=environment.docker(script='import subprocess,sys; subprocess.run([sys.executable,"-m","pip","check"],check=True)')
        self.assertEqual(deps.returncode,0,deps.stderr)
        data=fixture()
        documents=[{**d,'@class':'fr.sirs.core.model.'+cls} for cls,rows in data.items() for d in rows]
        environment.prepare_import(documents)
        directory=environment.evidence/'import-evidence'
        directory.mkdir(mode=0o700)
        # The harness validates postmaster ownership before this socket is mounted.
        mounts=(f'type=bind,source={directory},target=/evidence',)
        payload={'contract':environment.contract,'database':str(environment.context.business_database_id),
                 'epoch':str(environment.context.data_epoch),'job':str(UUID(int=97099)),
                 'source_sha256':digest(documents),'documents':documents}
        results=[]
        for mode in ('migrate','recover'):
            phases=('startup','migrate','recover') if mode=='migrate' else ('startup','recover')
            run=environment.docker(script=RUNNER,payload=json.dumps({**payload,'mode':mode}),mounts=mounts,phases=phases)
            self.assertEqual(run.returncode,0,run.stderr)
            results.append(json.loads(run.stdout.strip().splitlines()[-1]))
        self.assertEqual(results[0]['receipt_sha256'],results[1]['receipt_sha256'])
        (directory/'verified.json').write_text(json.dumps(results,indent=2))
