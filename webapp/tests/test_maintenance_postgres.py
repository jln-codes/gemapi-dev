"""API-MAINT-01, B (~1 min): private canonical SQL + real HTTP/SSE sessions.

Only the official disposable runner supplies DSNs. Barrier fixtures affect only
its private control/target. Negative cases distinguish authorization and outages.
"""
import os
import socket
import time
import unittest
from threading import Thread
from uuid import uuid4
from unittest.mock import patch
import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import uvicorn
from digues_webapp.app import create_app
from webapp.tests import test_api_read_postgres as read_fixture
TRONCONS=read_fixture.TRONCONS

@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Cluster jetable requis')
class MaintenancePostgresTest(unittest.TestCase):
    setUpClass = classmethod(read_fixture.OptionalReadPostgresTest.setUpClass.__func__)
    cleanup_databases = classmethod(read_fixture.OptionalReadPostgresTest.cleanup_databases.__func__)
    setUp = read_fixture.OptionalReadPostgresTest.setUp
    client = read_fixture.OptionalReadPostgresTest.client
    url = read_fixture.OptionalReadPostgresTest.url

    def test_real_http_sse_maintenance_and_admin_followup(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        with patch.dict(os.environ, AUTH_ORIGIN=origin, AUTH_ALLOW_INSECURE_LOCALHOST='true'):
            server=uvicorn.Server(uvicorn.Config(create_app(),host='127.0.0.1',port=port,log_level='warning'))
            thread=Thread(target=server.run,daemon=True);thread.start()
            try:
                deadline=time.monotonic()+15
                while not server.started and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(server.started)
                with httpx.Client(base_url=origin,timeout=10) as admin, httpx.Client(base_url=origin,timeout=10) as foreign:
                    for client,login in ((admin,'admin_alpha'),(foreign,'admin_beta')):
                        self.assertEqual(client.post('/api/auth/login',json={'login':login,'password':self.password},headers={'Origin':origin,'X-Digues-CSRF':'1'}).status_code,200)
                    detail=self.url(0,f'troncons/{TRONCONS[0][0]}'); stream=self.url(0,'resource-events/stream')
                    self.assertEqual(admin.get(detail).status_code,200)
                    from digues_app.target.business_version import BUSINESS_ACCESS_LOCK
                    with psycopg.connect(**{**self.params,'dbname':self.dbnames[0]},autocommit=True) as exclusive:
                        exclusive.execute('SELECT pg_advisory_lock(%s)',(BUSINESS_ACCESS_LOCK,))
                        r=admin.get(detail);self.assertEqual(r.status_code,503)
                        self.assertEqual(r.json()['code'],'BUSINESS_DATABASE_MAINTENANCE')
                        exclusive.execute('SELECT pg_advisory_unlock(%s)',(BUSINESS_ACCESS_LOCK,))
                    with psycopg.connect(self.control_dsn) as c:
                        job=c.execute("""INSERT INTO business_import_jobs(structure_id,business_database_id,initiated_by,idempotency_key,source_ref,request_digest,source_witness,target_srid)
                            VALUES(%s,%s,%s,%s,'fixture',%s,'{}',3950) RETURNING id""",(self.structures[0],self.bases[0],self.users['admin_alpha'].id,uuid4(),'a'*64)).fetchone()[0]
                    runtime_role=conninfo_to_dict(self.profiles['profile_0']['runtime'])['user']
                    def barrier(enabled):
                        with psycopg.connect(self.control_dsn) as c:
                            if enabled:c.execute('INSERT INTO business_import_barriers(business_database_id,structure_id,job_id) VALUES(%s,%s,%s)',(self.bases[0],self.structures[0],job))
                            else:c.execute('DELETE FROM business_import_barriers WHERE job_id=%s',(job,))
                        self.admin.execute(sql.SQL('{} CONNECT ON DATABASE {} {} {}').format(
                            sql.SQL('REVOKE' if enabled else 'GRANT'),sql.Identifier(self.dbnames[0]),
                            sql.SQL('FROM' if enabled else 'TO'),sql.Identifier(runtime_role)))
                    try:
                        with admin.stream('GET',stream) as response:
                            self.assertEqual(response.status_code,200)
                            lines=response.iter_lines();self.assertEqual(next(lines),': connected')
                            barrier(True)
                            remaining=list(lines)
                            self.assertIn('event: maintenance',remaining)
                            self.assertTrue(any('BUSINESS_DATABASE_MAINTENANCE' in line for line in remaining))
                        for path in (detail,stream,self.url(0,'resource-events/head')):
                            r=admin.get(path);self.assertEqual(r.status_code,503,r.text)
                            self.assertEqual(r.json()['code'],'BUSINESS_DATABASE_MAINTENANCE')
                            self.assertNotIn('Retry-After',r.headers)
                            self.assertEqual(foreign.get(path).status_code,403)
                        follow=admin.get(self.url(0,f'import-sirs/jobs/{job}'))
                        self.assertEqual(follow.status_code,200,follow.text)
                        # An uncertain result must not make the barrier disappear.
                        with psycopg.connect(self.control_dsn) as c:
                            c.execute("UPDATE business_import_jobs SET state='running',step='intervention_required',error_code='commit_outcome_unknown' WHERE id=%s",(job,))
                        self.assertEqual(admin.get(detail).status_code,503)
                        # The fixture now supplies a terminal conclusion. Real
                        # rollback/receipt closure is exercised by worker tests.
                        with psycopg.connect(self.control_dsn) as c:
                            c.execute("UPDATE business_import_jobs SET state='cancelled',step='cancelled',finished_at=clock_timestamp() WHERE id=%s",(job,))
                        barrier(False)
                        self.assertEqual(admin.get(detail).status_code,200)
                        with admin.stream('GET',stream) as resumed:
                            self.assertEqual(resumed.status_code,200)
                            self.assertEqual(next(resumed.iter_lines()),': connected')
                        self.assertEqual(admin.get(self.url(0,f'troncons/{uuid4()}')).status_code,404)
                        self.assertEqual(httpx.get(origin+detail).status_code,401)
                        # Simulate an unavailable destination only via its private profile.
                        from digues_webapp.connection_profiles import resolve_profile
                        original=resolve_profile
                        def unavailable(context,**kw):
                            result=original(context,**kw);result['host']='127.0.0.1';result['port']=1;result['connect_timeout']=1;return result
                        with patch('digues_webapp.connection_profiles.resolve_profile',side_effect=unavailable):
                            r=admin.get(detail);self.assertEqual(r.status_code,503)
                            self.assertNotIn('BUSINESS_DATABASE_MAINTENANCE',r.text)
                    finally:
                        barrier(False)
            finally:
                server.should_exit=True;thread.join(15);self.assertFalse(thread.is_alive())
