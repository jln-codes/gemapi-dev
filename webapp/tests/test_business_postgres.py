"""Recette quatre bases réelles, opt-in sur un CLUSTER jetable /tmp uniquement.

Ce banc crée/supprime ses bases et rôles, et retire CONNECT de PUBLIC dans le
cluster jetable. Ne jamais le configurer sur un cluster de développement partagé.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import select
import secrets
import subprocess
from time import perf_counter
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
import httpx

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from tests.postgres_safety import TEST_TEMP_ROOT, connect_test_database, require_test_dsn
from fastapi import HTTPException
from fastapi.testclient import TestClient

from digues_app.control.auth import create_superadmin, hash_password, authenticate, token_digest
from digues_app.control.catalog import standard_role_id
from digues_app.control.database import ControlDatabaseConfig, initialize_control_schema
from digues_app.target.business_version import BUSINESS_SCHEMA_VERSION
from digues_webapp import auth, business_context as bc, database, ai, schema_context
from digues_webapp.app import create_app
from digues_webapp.readonly_sql import execute_readonly_query
from digues_webapp.resource_journal import read_page


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Cluster local jetable non configuré')
class BusinessPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        params = conninfo_to_dict(require_test_dsn('DIGUES_MULTIBASE_TEST_ADMIN_DSN', kind='admin'))
        cls.admin_params = params
        cls.admin = connect_test_database('DIGUES_MULTIBASE_TEST_ADMIN_DSN', kind='admin', autocommit=True)
        cls.addClassCleanup(cls.admin.close)
        cls.names, cls.roles = [], []
        cls.addClassCleanup(cls.cleanup_databases)
        # PUBLIC CONNECT inherited from templates would defeat per-base roles.
        # This suite changes cluster-wide grants; refuse a shared cluster before
        # creating any fixture database or changing any ACL.
        database_names = [row[0] for row in cls.admin.execute(
            'SELECT datname FROM pg_database WHERE datallowconn').fetchall()]
        allowed = ('digues_occ_test_', 'digues_multi_test_', 'digues_control_test_')
        if any(name not in {'postgres', 'template1'} and not name.startswith(allowed)
               for name in database_names):
            raise AssertionError('Cluster partagé refusé avant modification des droits CONNECT.')
        suffix = uuid4().hex[:10]
        cls.control_name = f'digues_control_test_{suffix}'
        cls.dbnames = [f'digues_multi_test_{suffix}_{name}' for name in ('alpha_main', 'alpha_secondary', 'beta_main')]
        for name in [cls.control_name, *cls.dbnames]:
            cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            cls.names.append(name)
        for name in [*database_names, cls.control_name, *cls.dbnames]:
            cls.admin.execute(sql.SQL('REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        cls.control_dsn = make_conninfo(**{**params, 'dbname': cls.control_name})
        initialize_control_schema(ControlDatabaseConfig(cls.control_dsn, cls.control_name))
        cls.tmp = TemporaryDirectory(prefix='digues-business-', dir=TEST_TEMP_ROOT)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.profile_path = Path(cls.tmp.name) / 'profiles.json'
        profiles = {}
        cls.object_id, cls.beta_only = uuid4(), uuid4()
        for index, name in enumerate(cls.dbnames):
            runtime, readonly, ai_role = f'{name}_rw', f'{name}_ro', f'{name}_ai'
            for role in (runtime, readonly, ai_role):
                cls.admin.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)))
                cls.roles.append(role)
                cls.admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name), sql.Identifier(role)))
            with psycopg.connect(**{**params, 'dbname': name}, autocommit=True) as conn:
                conn.execute('CREATE EXTENSION postgis')
                conn.execute('CREATE EXTENSION pgcrypto')
                conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
                # Minimal real relational fixture, not a migration of business DDL.
                conn.execute('''CREATE TABLE public.systemes (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), version bigint DEFAULT 1, libelle text,
                    commentaire text, designation text, population_protegee integer,
                    classement text, niveau_protection text, valid boolean DEFAULT true)''')
                conn.execute('CREATE SCHEMA technique')
                conn.execute('''CREATE TABLE technique.metadonnees_base (
                    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
                    srid integer NOT NULL)''')
                conn.execute(
                    'INSERT INTO technique.metadonnees_base(singleton, srid) VALUES (true, %s)',
                    ((3950, 3950, 2154)[index],),
                )
                conn.execute('CREATE SEQUENCE technique.sequence_evenements_ressources')
                conn.execute("CREATE TABLE technique.evenements_ressources("
                             "sequence_id bigint PRIMARY KEY DEFAULT "
                             "nextval('technique.sequence_evenements_ressources'), "
                             "event_id uuid, resource text, payload jsonb)")
                conn.execute('''CREATE FUNCTION public.test_version() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN NEW.version := OLD.version + 1; RETURN NEW; END $$''')
                conn.execute('CREATE TRIGGER test_version BEFORE UPDATE ON public.systemes FOR EACH ROW EXECUTE FUNCTION public.test_version()')
                conn.execute(sql.SQL('ALTER TABLE public.systemes ADD COLUMN {} text').format(
                    sql.Identifier(f'marker_{index}')))
                # Same approved column, different schema sentinel in each base.
                conn.execute(sql.SQL('ALTER TABLE public.systemes ALTER COLUMN libelle TYPE varchar({})').format(
                    sql.Literal(100 + index)))
                conn.execute('INSERT INTO public.systemes(id,libelle) VALUES (%s,%s)', (cls.object_id, ('Alpha main', 'Alpha secondary', 'Beta main')[index]))
                if index == 2:
                    conn.execute('INSERT INTO public.systemes(id,libelle) VALUES (%s,%s)', (cls.beta_only, 'Beta only'))
                for _ in range(index + 1):
                    conn.execute('INSERT INTO technique.evenements_ressources(event_id,resource,payload) VALUES (%s,%s,%s::jsonb)',
                        (uuid4(), 'systemes', json.dumps({'sentinel': name})))
                for role in (runtime, readonly):
                    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
                    conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
                    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA technique TO {}').format(sql.Identifier(role)))
                    conn.execute(sql.SQL('GRANT SELECT ON technique.evenements_ressources TO {}').format(sql.Identifier(role)))
                    conn.execute(sql.SQL('GRANT SELECT ON technique.metadonnees_base TO {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(ai_role)))
                conn.execute(sql.SQL('GRANT SELECT(id,libelle) ON public.systemes TO {}').format(sql.Identifier(ai_role)))
                conn.execute(sql.SQL('GRANT INSERT, UPDATE, DELETE ON public.systemes TO {}').format(sql.Identifier(runtime)))
                conn.execute(sql.SQL('GRANT INSERT ON technique.evenements_ressources TO {}').format(sql.Identifier(runtime)))
                conn.execute(sql.SQL('GRANT USAGE ON SEQUENCE technique.sequence_evenements_ressources TO {}').format(sql.Identifier(runtime)))
            profiles[f'profile_{index}'] = {'cluster_ref': 'local', 'database_name': name,
                'runtime': make_conninfo(**{**params, 'dbname': name, 'user': runtime}),
                'sql_readonly': make_conninfo(**{**params, 'dbname': name, 'user': readonly}),
                'ai_readonly': make_conninfo(**{**params, 'dbname': name, 'user': ai_role})}
        cls.profiles = profiles
        cls.profile_path.write_text(json.dumps(profiles))
        cls.profile_path.chmod(0o600)
        cls.password = secrets.token_urlsafe(24)
        cls.users, cls.bases = {}, []
        with psycopg.connect(cls.control_dsn) as conn:
            structures = [conn.execute('INSERT INTO public.structures(slug,name) VALUES (%s,%s) RETURNING id', (name, name)).fetchone()[0] for name in ('alpha', 'beta')]
            cls.structures = structures
            for index, name in enumerate(cls.dbnames):
                cls.bases.append(conn.execute('''INSERT INTO public.business_databases
                    (structure_id,slug,display_name,cluster_ref,database_name,connection_profile_ref,status,schema_version)
                    VALUES (%s,%s,%s,'local',%s,%s,'active','test-v1') RETURNING id''',
                    (structures[index // 2], f'base_{index}', name, name, f'profile_{index}')).fetchone()[0])
            for login in ('admin_alpha', 'reader_alpha', 'editor_alpha', 'admin_beta', 'external_reader', 'superadmin'):
                user = create_superadmin(conn, login=login, display_name=login, password=cls.password)
                if login != 'superadmin':
                    conn.execute('UPDATE public.users SET is_superadmin=false WHERE id=%s', (user.id,))
                    user = replace(user, is_superadmin=False)
                cls.users[login] = user
            cls.matrix = {'admin_alpha': ('database_admin', 'database_admin', None),
                'reader_alpha': ('reader', None, None), 'editor_alpha': ('editor', 'reader', None),
                'admin_beta': (None, None, 'database_admin'), 'external_reader': (None, 'reader', 'reader'),
                'superadmin': (None, None, None)}
            for login, roles in cls.matrix.items():
                for index, role in enumerate(roles):
                    if role:
                        conn.execute('INSERT INTO public.memberships(user_id,business_database_id,structure_id,role_id) VALUES (%s,%s,%s,%s)',
                            (cls.users[login].id, cls.bases[index], structures[index // 2], standard_role_id(role)))
        control_role = f'{cls.control_name}_http'
        cls.admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT').format(sql.Identifier(control_role)))
        cls.roles.append(control_role)
        cls.admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
            sql.Identifier(cls.control_name), sql.Identifier(control_role)))
        from tests.control_runtime import grant_control_runtime
        with psycopg.connect(cls.control_dsn) as conn:
            grant_control_runtime(conn, control_role)
        cls.control_runtime_dsn = make_conninfo(**{**params, 'dbname': cls.control_name, 'user': control_role})
        cls.env = patch.dict(os.environ, {'CONTROL_DATABASE_URL': cls.control_runtime_dsn, 'CONTROL_DATABASE_NAME': cls.control_name,
            'BUSINESS_CONNECTION_PROFILES_FILE': str(cls.profile_path), 'AUTH_ORIGIN': 'https://testserver',
            'AUTH_ALLOW_INSECURE_LOCALHOST': 'false', 'LEGACY_SINGLE_DATABASE_ENABLED': 'false',
            'DATABASE_URL': 'host=invalid.invalid dbname=unavailable user=invalid',
            'DATABASE_READONLY_URL': '', 'PYTHON_DOTENV_DISABLED': '1'})
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)

    @classmethod
    def cleanup_databases(cls):
        # Exact generated targets only, never a deployment database.
        for name in reversed(cls.names):
            cls.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        for role in cls.roles:
            cls.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    def client(self, login):
        client = self.enterContext(TestClient(create_app(), base_url='https://testserver'))
        response = client.post('/api/auth/login', json={'login': login, 'password': self.password}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def setUp(self):
        self.headers = {'Origin': 'https://testserver', 'X-Digues-CSRF': '1'}

    def url(self, index, path):
        return f'/api/bases/{self.bases[index]}/{path}'

    def context(self, login, index, permission='business.read'):
        with psycopg.connect(self.control_dsn) as conn:
            user, token = authenticate(conn, login, self.password)
        return bc.resolve_business_context(user, self.bases[index], permission, session_token=token), token

    @contextmanager
    def lidar_mask_tables(self):
        """L1b niveau B : mêmes UUID dans trois bases, ACL et connexion HTTP réelles.

        Fixture locale au scénario ; le DDL complet et les formes limites sont
        qualifiés séparément par tests.test_lidar_masks_postgres. Coût : ~1 min.
        """
        digue, ah, troncon = uuid4(), uuid4(), uuid4()
        try:
            for index, name in enumerate(self.dbnames):
                params = {**self.admin_params, 'dbname': name}
                with psycopg.connect(**params, autocommit=True) as conn:
                    conn.execute('''CREATE TABLE public.digues (
                        id uuid PRIMARY KEY, systeme_endiguement_id uuid
                        REFERENCES public.systemes(id))''')
                    conn.execute('''CREATE TABLE public.amenagements_hydrauliques (
                        id uuid PRIMARY KEY, valid boolean)''')
                    conn.execute('''CREATE TABLE public.zones_stockage (
                        id uuid PRIMARY KEY, amenagement_hydraulique_id uuid
                        REFERENCES public.amenagements_hydrauliques(id), valid boolean, geometry geometry)''')
                    conn.execute('''CREATE TABLE public.troncons (
                        id uuid PRIMARY KEY, type_physique_id text,
                        digue_id uuid REFERENCES public.digues(id),
                        amenagement_hydraulique_id uuid REFERENCES public.amenagements_hydrauliques(id),
                        valid boolean, geometry geometry)''')
                    role = conninfo_to_dict(self.profiles[f'profile_{index}']['runtime'])['user']
                    conn.execute(sql.SQL('GRANT SELECT ON public.digues, '
                        'public.amenagements_hydrauliques, public.zones_stockage, public.troncons TO {}')
                        .format(sql.Identifier(role)))
                    conn.execute('INSERT INTO public.digues(id,systeme_endiguement_id) VALUES (%s,%s)',
                                 (digue, self.object_id))
                    crs = (3950, 3950, 2154)[index]
                    x = 700000 + index * 10000
                    conn.execute('''INSERT INTO public.amenagements_hydrauliques
                        (id,valid) VALUES (%s,true)''', (ah,))
                    conn.execute('''INSERT INTO public.zones_stockage
                        (id,amenagement_hydraulique_id,valid,geometry) VALUES (%s,%s,true,
                        ST_Transform(ST_Buffer(ST_GeomFromText(%s,2154),1),%s))''',
                        (uuid4(), ah, f'POINT({x+150} 6600000)', crs))
                    conn.execute('''INSERT INTO public.troncons
                        (id,type_physique_id,digue_id,amenagement_hydraulique_id,
                         valid,geometry) VALUES (%s,'INDETERMINE',%s,%s,false,
                        ST_Transform(ST_GeomFromText(%s,2154),%s))''',
                        (troncon, digue, ah, f'POINT({x} 6600000)', crs))
            yield {'se': self.object_id, 'digue': digue, 'ah': ah, 'troncon': troncon}
        finally:
            for name in reversed(self.dbnames):
                with psycopg.connect(**{**self.admin_params, 'dbname': name},
                                     autocommit=True) as conn:
                    for table in ('troncons', 'zones_stockage', 'amenagements_hydrauliques', 'digues'):
                        conn.execute(sql.SQL('DROP TABLE IF EXISTS public.{}').format(sql.Identifier(table)))

    def test_authorization_matrix_login_and_identical_uuid(self):
        for login, roles in self.matrix.items():
            client = self.client(login)
            for index, role in enumerate(roles):
                with self.subTest(login=login, base=index):
                    response = client.get(self.url(index, f'systemes-endiguement/{self.object_id}'))
                    self.assertEqual(response.status_code, 200 if role else 403, response.text)
                    if role:
                        self.assertEqual(response.json()['libelle'], ('Alpha main', 'Alpha secondary', 'Beta main')[index])
                    self.assertEqual(response.headers['cache-control'], 'no-store')
        client = self.client('reader_alpha')
        response = client.get(self.url(0, f'systemes-endiguement/{self.beta_only}'))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Beta only', response.text)

    def test_import_capability_requires_active_database_admin_and_rejects_unknown_history(self):
        for login, roles in self.matrix.items():
            client = self.client(login)
            for index, role in enumerate(roles):
                response = client.get(self.url(index, 'import-sirs/eligibility'))
                self.assertEqual(response.status_code, 200 if role == 'database_admin' else 403,
                                 (login, index, response.text))
                self.assertEqual(response.headers['cache-control'], 'no-store')
                if role == 'database_admin':
                    self.assertEqual(response.json(),
                                     {'eligible': False, 'reason': 'schema_incompatible'})
        # This deliberately minimal fixture has schema_version=test-v1. Only a
        # canonical version may advance to the separate missing-history check.
        with psycopg.connect(self.control_dsn) as control:
            control.execute('UPDATE public.business_databases SET schema_version=%s WHERE id=%s',
                            (BUSINESS_SCHEMA_VERSION, self.bases[0]))
        try:
            self.assertEqual(self.client('admin_alpha').get(
                self.url(0, 'import-sirs/eligibility')).json(),
                {'eligible': False, 'reason': 'history_unknown'})
        finally:
            with psycopg.connect(self.control_dsn) as control:
                control.execute('UPDATE public.business_databases SET schema_version=%s WHERE id=%s',
                                ('test-v1', self.bases[0]))
        client = self.client('admin_alpha')
        self.assertEqual(client.post(self.url(0, 'import-sirs/eligibility'),
                                     headers=self.headers).status_code, 405)

    def test_import_barrier_blocks_business_reads_writes_and_offline_scope(self):
        admin = self.client('admin_alpha')
        with psycopg.connect(self.control_dsn) as control:
            job = control.execute("""INSERT INTO public.business_import_jobs
                (structure_id,business_database_id,initiated_by,idempotency_key,
                 source_ref,request_digest,source_witness,target_srid)
                VALUES (%s,%s,%s,%s,'synthetic_source',%s,%s::jsonb,3950) RETURNING id""",
                (self.structures[0], self.bases[0], self.users['admin_alpha'].id, uuid4(),
                 sha256(b'business-import-barrier-test').hexdigest(), json.dumps({'source': 'synthetic_source'}))
            ).fetchone()[0]
            control.execute("""INSERT INTO public.business_import_barriers
                (business_database_id,structure_id,job_id) VALUES (%s,%s,%s)""",
                (self.bases[0], self.structures[0], job))
        try:
            for method, path, body in (
                ('get', 'systemes-endiguement', None),
                ('get', 'offline-scope', None),
                ('post', 'systemes-endiguement', {'libelle': 'Interdit'}),
            ):
                response = getattr(admin, method)(self.url(0, path),
                    **({'json': body, 'headers': self.headers} if body else {}))
                self.assertEqual(response.status_code, 503, (path, response.text))
            self.assertEqual(admin.get(self.url(1,
                f'systemes-endiguement/{self.object_id}')).status_code, 200)
        finally:
            with psycopg.connect(self.control_dsn) as control:
                control.execute('DELETE FROM public.business_import_barriers WHERE job_id=%s', (job,))
                control.execute('DELETE FROM public.business_import_jobs WHERE id=%s', (job,))

    def test_business_access_lock_excludes_import_lock_until_connections_drain(self):
        from digues_app.target.business_version import BUSINESS_ACCESS_LOCK

        context, _token = self.context('admin_alpha', 0)
        params = {**self.admin_params, 'dbname': self.dbnames[0]}
        with psycopg.connect(**params, autocommit=True) as contender:
            with database.open_read_connection(context):
                self.assertFalse(contender.execute(
                    'SELECT pg_try_advisory_lock(%s)', (BUSINESS_ACCESS_LOCK,)
                ).fetchone()[0])
            self.assertTrue(contender.execute(
                'SELECT pg_try_advisory_lock(%s)', (BUSINESS_ACCESS_LOCK,)
            ).fetchone()[0])
            contender.execute('SELECT pg_advisory_unlock(%s)', (BUSINESS_ACCESS_LOCK,))

    def test_base_summary_reads_selected_database_crs_without_cross_tenant_state(self):
        client = self.client('external_reader')
        expected = {
            1: {'crs': {'authority': 'EPSG', 'srid': 3950, 'code': 'EPSG:3950',
                        'name': 'RGF93 v1 / CC50', 'label': 'RGF93 v1 / CC50 — EPSG:3950'}},
            2: {'crs': {'authority': 'EPSG', 'srid': 2154, 'code': 'EPSG:2154',
                        'name': 'RGF93 v1 / Lambert-93', 'label': 'RGF93 v1 / Lambert-93 — EPSG:2154'}},
        }
        for index in (1, 2, 1, 2, 2, 1):
            with self.subTest(index=index):
                response = client.get(
                    self.url(index, 'base-summary'),
                    params={'crs': 'EPSG:2154', 'srid': 2154},
                    headers={'X-Business-CRS': 'EPSG:2154'},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), expected[index])
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn('database_name', response.text)

    def test_base_summary_rejects_missing_invalid_or_unauthorized_context(self):
        client = self.client('external_reader')
        with psycopg.connect(
            **{**self.admin_params, 'dbname': self.dbnames[1]}, autocommit=True,
        ) as conn:
            conn.execute('DELETE FROM technique.metadonnees_base')
            try:
                response = client.get(self.url(1, 'base-summary'))
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn('absente ou non unique', response.json()['detail'])
            finally:
                conn.execute(
                    'INSERT INTO technique.metadonnees_base(singleton, srid) VALUES (true, 3950)'
                )
            conn.execute('UPDATE technique.metadonnees_base SET srid = 4326')
            try:
                response = client.get(self.url(1, 'base-summary'))
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn('invalide ou non qualifié', response.json()['detail'])
            finally:
                conn.execute('UPDATE technique.metadonnees_base SET srid = 3950')

        forbidden = client.get(self.url(0, 'base-summary'))
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        anonymous = self.enterContext(TestClient(create_app(), base_url='https://testserver'))
        self.assertEqual(anonymous.get(self.url(1, 'base-summary')).status_code, 401)
        self.assertEqual(client.get('/api/base-summary').status_code, 404)

    def test_coordinate_transform_uses_authorized_database_crs_without_leakage(self):
        client = self.client('external_reader')
        results = {}
        for index in (1, 2, 1, 2, 2, 1):
            response = client.get(
                self.url(index, 'coordinates/transform'),
                params={
                    'longitude': 2.25,
                    'latitude': 50.5,
                    'crs': 'EPSG:4326',
                    'srid': 4326,
                },
                headers={'X-Business-CRS': 'EPSG:4326'},
            )
            self.assertEqual(response.status_code, 200, response.text)
            current = response.json()
            if index in results:
                self.assertEqual(current, results[index])
            results[index] = current
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotIn('database_name', response.text)
        self.assertNotAlmostEqual(results[1]['x'], results[2]['x'])
        self.assertNotAlmostEqual(results[1]['y'], results[2]['y'])

        forbidden = client.get(
            self.url(0, 'coordinates/transform'),
            params={'longitude': 2.25, 'latitude': 50.5},
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

        params = {"systeme_reperage_id": str(uuid4()), "borne_debut_id": str(uuid4()),
                  "distance_debut_m": 0, "position_debut_relative": "SUR_BORNE"}
        path = f"troncons/{uuid4()}/geometrie-bornage"
        # Real authentication/control database. These denials must precede
        # opening an unauthorized business database, even with valid inputs.
        denied = client.get(self.url(0, path), params=params)
        self.assertEqual(denied.status_code, 403, denied.text)
        anonymous = self.enterContext(TestClient(create_app(), base_url="https://testserver"))
        self.assertEqual(anonymous.get(self.url(1, path), params=params).status_code, 401)
        self.assertEqual(client.get("/api/" + path, params=params).status_code, 404)

    def test_ai_http_context_and_no_readonly_fallback_even_with_cached_schema(self):
        client = self.client('external_reader')
        for index in (1, 2, 1):
            def completion(messages, schema, **kwargs):
                self.assertIn('libelle', schema)
                self.assertNotIn(f'marker_{index}', schema)
                self.assertNotIn(f'marker_{3-index}', schema)
                self.assertIn(f'character varying({100 + index})', schema)
                self.assertNotIn(f'character varying({103 - index})', schema)
                with kwargs['connection_factory']() as conn:
                    self.assertEqual(conn.execute('SELECT current_user').fetchone()[0], f'{self.dbnames[index]}_ai')
                    label = conn.execute('SELECT libelle FROM public.systemes WHERE id=%s', (self.object_id,)).fetchone()[0]
                return ai.AiChatResult(answer=label, executed_queries=())
            with patch('digues_webapp.business_routes.chat_with_mistral', side_effect=completion):
                response = client.post(self.url(index, 'ai/chat'), json={'message': 'Lire le libellé'}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['answer'], ('Alpha secondary', 'Beta main')[index - 1])
            self.assertNotIn('database_name', response.text)
        changed = json.loads(json.dumps(self.profiles))
        del changed['profile_1']['ai_readonly']
        self.profile_path.write_text(json.dumps(changed))
        try:
            with patch('digues_webapp.business_routes.chat_with_mistral') as model:
                response = client.post(self.url(1, 'ai/chat'), json={'message': 'Cache'}, headers=self.headers)
                self.assertEqual(response.status_code, 503)
                model.assert_not_called()
        finally:
            self.profile_path.write_text(json.dumps(self.profiles))

    def test_simulated_ai_tool_call_uses_the_contextual_readonly_role(self):
        from webapp.tests.test_ai_tools import mistral_response, tool_call

        context, token = self.context('external_reader', 1, 'ai.read')
        factory, _ = bc.revalidated_factory(
            context, self.users['external_reader'], token, 'ai.read', ai=True)
        with factory() as connection:
            self.assertEqual(connection.execute('SELECT current_user').fetchone()[0],
                             f'{self.dbnames[1]}_ai')
            connection.execute('SET default_transaction_read_only=off')
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("INSERT INTO public.systemes(libelle) VALUES ('interdit')")
        responses = [
            mistral_response({'role': 'assistant', 'content': '', 'tool_calls': [
                tool_call('real-select',
                          '{"sql":"SELECT COUNT(*) AS total FROM public.systemes"}')]}),
            mistral_response({'role': 'assistant', 'content': 'Le comptage a été effectué.'}),
        ]
        with (patch.dict(os.environ, {'MISTRAL_API_KEY': 'test-key'}),
              patch('digues_webapp.ai.requests.post', side_effect=responses) as post):
            answer = ai.chat_with_mistral([], 'schema',
                current_message='Combien de systèmes ?', connection_factory=factory)
        self.assertEqual(answer.answer, 'Le comptage a été effectué.')
        self.assertEqual(answer.executed_queries,
                         ('SELECT COUNT(*) AS total FROM public.systemes',))
        tool = json.loads(post.call_args_list[1].kwargs['json']['messages'][-1]['content'])
        self.assertEqual(tool['columns'], ['total'])
        self.assertEqual(tool['rows'], [[1]])
        self.assertFalse(tool['truncated'])

    def test_write_permissions_csrf_and_no_business_open_on_denial(self):
        client = self.client('reader_alpha')
        with patch.object(database, '_context_connection') as opened:
            for index in (0, 1, 2):
                response = client.post(self.url(index, 'systemes-endiguement'), json={'libelle': 'test'}, headers=self.headers)
                self.assertEqual(response.status_code, 403)
            client.post(self.url(0, 'sql/query'), json={'sql': 'SELECT 1'})
            opened.assert_not_called()
        ctx, token = self.context('editor_alpha', 0, 'business.create')
        self.assertIn('business.update', ctx.permissions)
        for index, permission in ((0, 'business.delete'), (0, 'business.purge'), (1, 'business.create')):
            with self.assertRaises(HTTPException):
                bc.resolve_business_context(self.users['editor_alpha'], self.bases[index], permission, session_token=token)

    def test_sql_readonly_same_target_no_cross_database(self):
        client = self.client('reader_alpha')
        response = client.post(self.url(0, 'sql/query'), json={'sql': 'SELECT libelle FROM public.systemes'}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('Alpha main', response.text)
        self.assertNotIn('Beta main', response.text)
        who = client.post(self.url(0, 'sql/query'), json={'sql': 'SELECT current_user'}, headers=self.headers)
        self.assertEqual(who.status_code, 200)
        self.assertIn(f'{self.dbnames[0]}_ro', who.text)
        for statement in ("UPDATE public.systemes SET libelle='bad'", 'SELECT * FROM public.users',
                          f'SELECT * FROM {self.dbnames[2]}.public.systemes'):
            self.assertNotEqual(client.post(self.url(0, 'sql/query'), json={'sql': statement}, headers=self.headers).status_code, 200)
        ctx, _ = self.context('reader_alpha', 0)
        with database.open_sql_connection(ctx) as conn:
            self.assertEqual(conn.execute('SELECT current_database()').fetchone()[0], self.dbnames[0])
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("UPDATE public.systemes SET libelle='bad'")
        for name in (self.dbnames[2], self.control_name):
            params = conninfo_to_dict(self.profiles['profile_0']['sql_readonly'])
            with self.assertRaises(psycopg.OperationalError):
                psycopg.connect(**{**params, 'dbname': name})

    def test_concurrent_and_delayed_connections_remain_bound(self):
        a, _ = self.context('external_reader', 1)
        b, _ = self.context('external_reader', 2)
        barrier = threading.Barrier(2)
        def read(ctx):
            with database.open_read_connection(ctx) as conn:
                barrier.wait(timeout=10)
                return conn.execute('SELECT current_database(),libelle FROM public.systemes WHERE id=%s', (self.object_id,)).fetchone()
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(read, (a, b))), [(self.dbnames[1], 'Alpha secondary'), (self.dbnames[2], 'Beta main')])
        # Resolving Beta does not modify a previously captured Alpha target.
        with database.open_read_connection(a) as conn:
            self.assertEqual(conn.execute('SELECT current_database()').fetchone()[0], self.dbnames[1])

    def test_journal_and_ai_tools_never_mix_factories(self):
        for index in (1, 2, 1):
            ctx, token = self.context('external_reader', index)
            factory, _ = bc.revalidated_factory(ctx, self.users['external_reader'], token, 'business.read')
            page = read_page(0, connection_factory=factory)
            self.assertEqual(page['nextCursor'], str(index + 1))
            self.assertTrue(all(item['event']['sentinel'] == self.dbnames[index] for item in page['events']))
            factory, _ = bc.revalidated_factory(ctx, self.users['external_reader'], token, 'ai.read', ai=True)
            call = {'function': {'name': ai.BUSINESS_DATABASE_TOOL_NAME, 'arguments': json.dumps({'sql': 'SELECT libelle FROM public.systemes'})}}
            result = ai._run_tool(call, connection_factory=factory)
            self.assertIn(('Alpha secondary', 'Beta main')[index - 1], json.dumps(result))
            text = schema_context.get_ai_schema_context(connection_factory=factory, cache_key=ctx.schema_cache_key)
            self.assertIn('systemes', text)
            self.assertNotIn(f'marker_{index}', text)
            self.assertNotIn(f'marker_{3-index}', text)
            self.assertIn(f'character varying({100 + index})', text)
        self.assertGreaterEqual(len(schema_context._schema_cache), 2)

    def test_http_inflight_alpha_stays_alpha_during_beta_request(self):
        client = self.client('external_reader')
        reached, release = threading.Event(), threading.Event()
        original = database._context_connection

        def delayed(context, **kwargs):
            if context.business_database_id == self.bases[1]:
                reached.set()
                if not release.wait(timeout=10):
                    raise AssertionError('Beta request did not complete')
            yield from original(context, **kwargs)

        with patch.object(database, '_context_connection', delayed), ThreadPoolExecutor(2) as pool:
            alpha = pool.submit(client.get, self.url(1, f'systemes-endiguement/{self.object_id}'))
            try:
                self.assertTrue(reached.wait(timeout=10))
                beta = client.get(self.url(2, f'systemes-endiguement/{self.object_id}'))
                self.assertEqual(beta.json()['libelle'], 'Beta main')
            finally:
                release.set()
            self.assertEqual(alpha.result(timeout=10).json()['libelle'], 'Alpha secondary')

    def test_revocations_reject_new_and_captured_factories(self):
        ctx, token = self.context('reader_alpha', 0)
        factory, _ = bc.revalidated_factory(ctx, self.users['reader_alpha'], token, 'business.read')
        for table, key, identity in (('memberships', 'user_id', ctx.user_id), ('users', 'id', ctx.user_id),
                                     ('business_databases', 'id', ctx.business_database_id), ('structures', 'id', ctx.structure_id)):
            with psycopg.connect(self.control_dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("UPDATE public.{} SET status='disabled' WHERE {}=%s").format(sql.Identifier(table), sql.Identifier(key)), (identity,))
                try:
                    with self.assertRaises(HTTPException), factory():
                        pass
                finally:
                    conn.execute(sql.SQL("UPDATE public.{} SET status='active' WHERE {}=%s").format(sql.Identifier(table), sql.Identifier(key)), (identity,))
            ctx, token = self.context('reader_alpha', 0)
            factory, _ = bc.revalidated_factory(ctx, self.users['reader_alpha'], token, 'business.read')
        with psycopg.connect(self.control_dsn) as conn:
            conn.execute('UPDATE public.users SET auth_version=auth_version+1 WHERE id=%s', (ctx.user_id,))
        with self.assertRaises(HTTPException), factory():
            pass

    def test_lidar_masks_http_contract_and_real_business_authorization(self):
        """L1b : session, membership, CSRF, base ciblée et bornes HTTP réels."""
        from digues_webapp import lidar_masks_http

        with self.lidar_mask_tables() as ids:
            alpha = self.client('admin_alpha')
            reader = self.client('reader_alpha')
            beta = self.client('admin_beta')
            path0 = self.url(0, 'lidar/masks/v1')
            path1 = self.url(1, 'lidar/masks/v1')
            path2 = self.url(2, 'lidar/masks/v1')
            request_schema = alpha.get('/openapi.json').json()['paths'][
                '/api/bases/{business_database_id}/lidar/masks/v1']['post'][
                'requestBody']['content']['application/json']['schema']
            self.assertIn('ensembles', request_schema['properties'])
            body = {'ensembles': [{'type': 'AH', 'id': str(ids['ah'])},
                                  {'type': 'SE', 'id': str(ids['se'])},
                                  {'type': 'SE', 'id': str(ids['se'])}]}
            first = alpha.post(path0, json=body, headers=self.headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.headers['cache-control'], 'no-store')
            payload = first.json()
            self.assertEqual(payload['version'], 'LIDAR_MASK_V1')
            self.assertEqual(payload['crs'], 'EPSG:2154')
            self.assertEqual([(mask['type'], mask['id']) for mask in payload['masks']],
                             [('SE', str(ids['se'])), ('AH', str(ids['ah']))])
            self.assertTrue(all(mask['status'] == 'READY' and mask['geometry']['type'] == 'MultiPolygon'
                                and mask['area_m2'] > 0 and len(mask['bbox']) == 4
                                for mask in payload['masks']))
            self.assertEqual(first.json(), alpha.post(path0, json=body, headers=self.headers).json())
            self.assertEqual(reader.post(path0, json={'ensembles': [body['ensembles'][1]]},
                                         headers=self.headers).status_code, 200)
            second = alpha.post(path1, json=body, headers=self.headers)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertNotEqual(payload['masks'][0]['bbox'], second.json()['masks'][0]['bbox'])
            self.assertEqual(reader.post(path1, json=body, headers=self.headers).status_code, 403)
            self.assertEqual(reader.post(path2, json=body, headers=self.headers).status_code, 403)
            self.assertEqual(beta.post(path2, json=body, headers=self.headers).status_code, 200)
            anonymous = self.enterContext(TestClient(create_app(), base_url='https://testserver'))
            self.assertEqual(anonymous.post(path0, json=body, headers=self.headers).status_code, 401)
            self.assertEqual(alpha.post(path0, json=body,
                headers={'Origin': 'https://foreign.invalid', 'X-Digues-CSRF': '1'}).status_code, 403)
            self.assertEqual(alpha.get(path0).status_code, 405)

            empty_ids = [uuid4() for _ in range(40)]
            second_ah = uuid4()
            anomaly_se, anomaly_digue, anomaly_root = uuid4(), uuid4(), uuid4()
            with psycopg.connect(**{**self.admin_params, 'dbname': self.dbnames[0]},
                                 autocommit=True) as conn:
                try:
                    with conn.transaction():
                        for identifier in [*empty_ids, anomaly_se]:
                            conn.execute('INSERT INTO public.systemes(id,libelle) VALUES (%s,%s)',
                                         (identifier, 'Masque test'))
                        conn.execute('''INSERT INTO public.amenagements_hydrauliques
                            (id,valid) VALUES (%s,true)''', (second_ah,))
                        conn.execute('''INSERT INTO public.zones_stockage
                            (id,amenagement_hydraulique_id,valid,geometry) VALUES (%s,%s,true,
                            ST_Transform(ST_Buffer(ST_GeomFromText('POINT(700250 6600000)',2154),1),3950))''',
                            (uuid4(), second_ah))
                        conn.execute('INSERT INTO public.digues(id,systeme_endiguement_id) VALUES (%s,%s)',
                                     (anomaly_digue, anomaly_se))
                        conn.execute('''INSERT INTO public.troncons
                            (id,type_physique_id,digue_id,valid,geometry)
                            VALUES (%s,'INDETERMINE',%s,false,ST_GeomFromText('POINT(1 2)'))''',
                            (anomaly_root, anomaly_digue))
                    one = alpha.post(path0, json={'ensembles': [body['ensembles'][1]]},
                                     headers=self.headers)
                    self.assertEqual(one.status_code, 200)
                    self.assertEqual(len(one.json()['masks']), 1)
                    self.assertAlmostEqual(one.json()['masks'][0]['area_m2'],
                                           1950.90322016, delta=.01)
                    self.assertAlmostEqual(one.json()['masks'][0]['bbox'][0],
                                           699975, delta=.01)
                    mixed = alpha.post(path0, json={'ensembles': [
                        {'type': 'AH', 'id': str(second_ah)},
                        {'type': 'SE', 'id': str(empty_ids[0])},
                        {'type': 'AH', 'id': str(ids['ah'])},
                        {'type': 'SE', 'id': str(ids['se'])}]}, headers=self.headers)
                    self.assertEqual(mixed.status_code, 200, mixed.text)
                    self.assertEqual([item['type'] for item in mixed.json()['masks']],
                                     ['SE', 'SE', 'AH', 'AH'])
                    self.assertEqual([item['id'] for item in mixed.json()['masks'][:2]],
                                     sorted([str(ids['se']), str(empty_ids[0])]))
                    self.assertEqual([item['id'] for item in mixed.json()['masks'][2:]],
                                     sorted([str(ids['ah']), str(second_ah)]))
                    empty = alpha.post(path0, json={'ensembles': [
                        {'type': 'SE', 'id': str(empty_ids[0])}]}, headers=self.headers)
                    self.assertEqual(empty.status_code, 200)
                    self.assertEqual(empty.json()['masks'][0]['status'], 'EMPTY')
                    anomaly = alpha.post(path0, json={'ensembles': [
                        {'type': 'SE', 'id': str(anomaly_se)}]}, headers=self.headers)
                    self.assertEqual(anomaly.status_code, 200)
                    self.assertEqual(anomaly.json()['masks'][0]['status'], 'ANOMALY')
                    self.assertEqual(anomaly.json()['masks'][0]['diagnostics'][0]['code'], 'SRID_INCONNU')
                    missing = alpha.post(path0, json={'ensembles': [
                        {'type': 'SE', 'id': str(uuid4())}]}, headers=self.headers)
                    self.assertEqual(missing.status_code, 404)
                    self.assertEqual(missing.headers['cache-control'], 'no-store')
                    self.assertEqual(alpha.post(path0, json={'ensembles': [
                        body['ensembles'][1], {'type': 'AH', 'id': str(uuid4())}]},
                        headers=self.headers).status_code, 404)
                    thirty_two = alpha.post(path0, json={'ensembles': [
                        {'type': 'SE', 'id': str(identifier)}
                        for identifier in [ids['se'], *empty_ids[:31]]]}, headers=self.headers)
                    self.assertEqual(thirty_two.status_code, 200, thirty_two.text)
                    self.assertEqual(len(thirty_two.json()['masks']), 32)
                    thirty_three = alpha.post(path0, json={'ensembles': [
                        {'type': 'SE', 'id': str(identifier)}
                        for identifier in [ids['se'], *empty_ids[:31], anomaly_se]]},
                        headers=self.headers)
                    self.assertEqual(thirty_three.status_code, 200, thirty_three.text)
                    self.assertEqual({m['id'] for m in thirty_three.json()['masks']},
                                     {str(i) for i in [ids['se'], *empty_ids[:31], anomaly_se]})
                    for bad in ({'ensembles': []},
                                {'ensembles': [{'type': 'SE', 'id': 'invalide'}]},
                                {'ensembles': [body['ensembles'][1]],
                                 'business_database_id': str(self.bases[2])}):
                        self.assertEqual(alpha.post(path0, json=bad,
                                                     headers=self.headers).status_code, 422)
                    self.assertEqual(alpha.post(path0, content=b'{' + b' ' * (64 * 1024),
                        headers={**self.headers, 'Content-Type': 'application/json'}).status_code, 413)
                    self.assertEqual(alpha.post(path0, content=b'{}',
                        headers={**self.headers, 'Content-Type': 'text/plain'}).status_code, 415)
                    with patch.object(lidar_masks_http, 'MAX_RESPONSE_BYTES', 1):
                        self.assertEqual(alpha.post(path0, json=body,
                                                     headers=self.headers).status_code, 413)
                finally:
                    with conn.transaction():
                        conn.execute('DELETE FROM public.troncons WHERE id=%s', (anomaly_root,))
                        conn.execute('DELETE FROM public.digues WHERE id=%s', (anomaly_digue,))
                        conn.execute('DELETE FROM public.zones_stockage WHERE amenagement_hydraulique_id=%s',
                                     (second_ah,))
                        conn.execute('DELETE FROM public.amenagements_hydrauliques WHERE id=%s',
                                     (second_ah,))
                        conn.execute('DELETE FROM public.systemes WHERE id=ANY(%s::uuid[])',
                                     ([*empty_ids, anomaly_se],))

    def test_lidar_distribution_http_real_context_and_internal_numeric_cache(self):
        """L2c niveau B : vrai contrôle session/base/PostGIS puis HTTP Node simulant IGN.

        État initial : trois bases jetables, UUID métier identiques. Cache jetable
        isolé dans audits ; cas négatifs : autre base, bloc forgé et relation déplacée.
        Groupe : tests.run_postgres, coût attendu < 2 min avec le démarrage du cluster.
        """
        fixture = Path(__file__).parents[2] / 'services/plan-ign-raster/lidar-http-fixture.mjs'
        token = 'lidar-http-fixture-01234567890123456789'
        with self.lidar_mask_tables() as ids, TemporaryDirectory(prefix='lidar-l2c-', dir=TEST_TEMP_ROOT) as root:
            peer = subprocess.Popen(['node', str(fixture), root, token], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            try:
                ready, _, _ = select.select([peer.stdout], [], [], 15)
                self.assertTrue(ready, 'Le pair Node LiDAR jetable n’a pas démarré.')
                line = peer.stdout.readline().strip()
                self.assertTrue(line.startswith('READY '), line)
                base = f'http://127.0.0.1:{line.split()[1]}'
                with patch.dict(os.environ, {'BASEMAP_RASTER_URL': base,
                                              'LIDAR_INTERNAL_TOKEN': token}):
                    alpha = self.client('admin_alpha')
                    reader = self.client('reader_alpha')
                    anonymous = self.enterContext(TestClient(create_app(), base_url='https://testserver'))
                    view = {'crs': 'EPSG:2154', 'bbox': [699900, 6599900, 700300, 6600200]}
                    body = {'ensembles': [{'type': 'SE', 'id': str(ids['se'])},
                                          {'type': 'AH', 'id': str(ids['ah'])}], 'vue': view, 'policy_version': 1}
                    path = self.url(0, 'lidar/blocks/v1/plan')
                    read_path = self.url(0, 'lidar/blocks/v1/read')
                    counts = lambda: httpx.get(base + '/test/counts').json()['requests']
                    before = counts()
                    plan_started = perf_counter()
                    planned = alpha.post(path, json=body, headers=self.headers)
                    plan_ms = (perf_counter() - plan_started) * 1000
                    self.assertEqual(planned.status_code, 200, planned.text)
                    self.assertEqual(planned.headers['cache-control'], 'no-store')
                    plan = planned.json()
                    self.assertEqual(plan['version'], 'LIDAR_DISTRIBUTION_V2')
                    self.assertEqual(plan['crs'], 'EPSG:2154')
                    self.assertEqual([row['status'] for row in plan['ensembles']], ['READY', 'READY'])
                    self.assertTrue(plan['blocks'])
                    self.assertEqual(counts(), before, 'La planification ne contacte jamais IGN.')
                    block = plan['blocks'][0]
                    selection = {**body, 'bloc': {key: block[key] for key in ('id', 'ix', 'iy')}}
                    forged = {**selection, 'bloc': {**selection['bloc'], 'id': 'f' * 64}}
                    self.assertEqual(alpha.post(read_path, json=forged,
                                                headers=self.headers).status_code, 404)
                    self.assertEqual(counts(), before)
                    miss_started = perf_counter()
                    first = alpha.post(read_path, json=selection, headers=self.headers)
                    miss_ms = (perf_counter() - miss_started) * 1000
                    self.assertEqual(first.status_code, 200, first.text[:200])
                    self.assertEqual(first.headers['content-type'], 'image/geotiff')
                    self.assertEqual(first.headers['cache-control'], 'no-store')
                    self.assertEqual(first.headers['x-lidar-status'], 'DATA')
                    self.assertEqual(first.headers['x-cache'], 'MISS')
                    self.assertTrue(first.content.startswith(b'II'))
                    cells = block['width'] * block['height']
                    self.assertGreater(len(first.content), cells * 4)
                    self.assertLess(len(first.content), cells * 4 + 1024)
                    # Verify actual returned numeric dimensions and a sample,
                    # rather than the abandoned full-512² byte-count assumption.
                    import struct
                    ifd = struct.unpack_from('<I', first.content, 4)[0]
                    tags = {struct.unpack_from('<H', first.content, ifd + 2 + i * 12)[0]:
                            struct.unpack_from('<I', first.content, ifd + 10 + i * 12)[0]
                            for i in range(struct.unpack_from('<H', first.content, ifd)[0])}
                    self.assertEqual((tags[256], tags[257]), (block['width'], block['height']))
                    self.assertEqual(struct.unpack_from('<f', first.content, tags[273])[0], 45.25)
                    self.assertEqual(counts(), before + 1)
                    hit_started = perf_counter()
                    hit = alpha.post(read_path, json=selection, headers=self.headers)
                    hit_ms = (perf_counter() - hit_started) * 1000
                    self.assertEqual(hit.content, first.content)
                    self.assertEqual(hit.headers['x-cache'], 'HIT')
                    self.assertEqual(counts(), before + 1)
                    print('L2C_LOCAL_TIMING ' + json.dumps({'plan_ms': round(plan_ms, 2),
                        'miss_ms': round(miss_ms, 2), 'hit_ms': round(hit_ms, 2),
                        'blocks': len(plan['blocks']), 'bytes': len(first.content),
                        'provider_requests': counts() - before}), flush=True)
                    outside = {**body, 'vue': {'crs': 'EPSG:2154',
                                              'bbox': [720000, 6600000, 720256, 6600256]}}
                    empty = alpha.post(path, json=outside, headers=self.headers)
                    self.assertEqual(empty.status_code, 200, empty.text)
                    self.assertEqual(empty.json()['blocks'], [])
                    self.assertEqual(counts(), before + 1)
                    self.assertEqual(reader.post(self.url(1, 'lidar/blocks/v1/plan'),
                                                 json=body, headers=self.headers).status_code, 403)
                    self.assertEqual(anonymous.post(path, json=body,
                                                    headers=self.headers).status_code, 401)
                    self.assertEqual(alpha.post(path, json={**body, 'vue': {
                        'crs': 'EPSG:2154', 'bbox': [0, 0, 20001, 1]}},
                        headers=self.headers).status_code, 200)
                    self.assertEqual(alpha.post(path, json={**body, 'ensembles': [
                        {'type': 'SE', 'id': str(uuid4())} for _ in range(33)]},
                        headers=self.headers).status_code, 404)
                    self.assertEqual(alpha.post(path, json={**body,
                        'business_database_id': str(self.bases[2])},
                        headers=self.headers).status_code, 422)
                    self.assertEqual(alpha.post(path, json={**body, 'ensembles': [
                        {'type': 'SE', 'id': str(uuid4())}]},
                        headers=self.headers).status_code, 404)
                    self.assertEqual(counts(), before + 1)
                    second = alpha.post(self.url(1, 'lidar/blocks/v1/plan'),
                                        json=body, headers=self.headers)
                    self.assertEqual(second.status_code, 200, second.text)
                    self.assertNotEqual([item['id'] for item in plan['blocks']],
                                        [item['id'] for item in second.json()['blocks']])
                    self.assertEqual(alpha.post(self.url(1, 'lidar/blocks/v1/read'),
                                                json=selection, headers=self.headers).status_code, 404)
                    self.assertEqual(counts(), before + 1)
                    # Public cache bytes can be shared; their business API must
                    # still refuse live revocations after actual Node HTTP I/O.
                    from digues_webapp import lidar_distribution as delivery
                    original_call = delivery._call_while_connected
                    actor = self.users['admin_alpha'].id
                    updates = [
                        ('UPDATE public.memberships SET status=%s WHERE user_id=%s AND business_database_id=%s',
                         ('disabled', actor, self.bases[0]), ('active', actor, self.bases[0])),
                        ('UPDATE public.users SET status=%s WHERE id=%s', ('disabled', actor), ('active', actor)),
                        ('UPDATE public.memberships SET role_id=%s WHERE user_id=%s AND business_database_id=%s',
                         (standard_role_id('reader'), actor, self.bases[0]),
                         (standard_role_id('database_admin'), actor, self.bases[0])),
                        ('UPDATE public.business_databases SET status=%s WHERE id=%s',
                         ('disabled', self.bases[0]), ('active', self.bases[0])),
                    ]
                    for statement, changed, restored in updates:
                        active_client = self.client('admin_alpha')
                        async def revoke_after_io(*args, **kwargs):
                            result = await original_call(*args, **kwargs)
                            with psycopg.connect(**{**self.admin_params, 'dbname': self.control_name}) as writer:
                                writer.execute(statement, changed)
                            return result
                        try:
                            with patch.object(delivery, '_call_while_connected', revoke_after_io):
                                denied = active_client.post(read_path, json=selection, headers=self.headers)
                            self.assertIn(denied.status_code, (401, 403), denied.text)
                            self.assertNotEqual(denied.headers.get('content-type'), 'image/geotiff')
                        finally:
                            with psycopg.connect(**{**self.admin_params, 'dbname': self.control_name}) as writer:
                                writer.execute(statement, restored)
                    alpha = self.client('admin_alpha')
                    with psycopg.connect(**{**self.admin_params, 'dbname': self.control_name}) as writer:
                        writer.execute('UPDATE public.platform_settings SET lidar_buffer_m=50,lidar_policy_version=2 WHERE singleton')
                    try:
                        self.assertEqual(alpha.post(read_path, json=selection, headers=self.headers).status_code, 409)
                        self.assertEqual(alpha.post(path, json=body, headers=self.headers).status_code, 409)
                        self.assertEqual(counts(), before + 1)
                    finally:
                        with psycopg.connect(**{**self.admin_params, 'dbname': self.control_name}) as writer:
                            writer.execute('UPDATE public.platform_settings SET lidar_buffer_m=25,lidar_policy_version=1 WHERE singleton')

                    # Coarse authorized windows, not a full-resolution fill of
                    # the >20 km viewport. Geometry, token, ACL and cache real;
                    # only the WMS supplier returns synthetic numeric samples.
                    coarse_body = {**body, 'ensembles':[body['ensembles'][0]], 'vue': {'crs':'EPSG:2154',
                        'bbox':[600000,6500000,800000,6700000], 'level':8}}
                    coarse = alpha.post(path,json=coarse_body,headers=self.headers)
                    self.assertEqual(coarse.status_code,200,coarse.text)
                    self.assertGreater(coarse.json()['resolution_m'],.5)
                    self.assertLess(coarse.json()['estimated_bytes'],1024)
                    coarse_block = coarse.json()['blocks'][0]
                    coarse_selection = {**coarse_body,'bloc':{k:coarse_block[k] for k in ('id','ix','iy')}}
                    coarse_read = alpha.post(read_path,json=coarse_selection,headers=self.headers)
                    self.assertEqual(coarse_read.status_code,200,coarse_read.text[:200])
                    self.assertEqual(coarse_read.headers['x-cache'],'MISS')
                    self.assertLess(len(coarse_read.content),2048)
                    self.assertEqual(alpha.post(read_path,json=coarse_selection,
                        headers=self.headers).headers['x-cache'],'HIT')
                    before += 1  # One additional real internal/synthetic supplier acquisition.
                    for bad_view in ({**view,'level':-1},{**view,'level':31},
                                     {**view,'level':1.5},{**view,'bbox':[0,0,1e20,1]}):
                        self.assertEqual(alpha.post(path,json={**body,'vue':bad_view},headers=self.headers).status_code,422)
                    # The common coverage is a real PostGIS union, not merely
                    # a concatenation of overlapping MultiPolygon rings.
                    from digues_webapp.lidar_distribution import LidarPlanRequest, _authorized_masks
                    overlap_ah = uuid4()
                    params0 = {**self.admin_params, 'dbname': self.dbnames[0]}
                    with psycopg.connect(**params0, autocommit=True) as conn:
                        conn.execute('''INSERT INTO public.amenagements_hydrauliques
                            (id,valid) VALUES (%s,true)''', (overlap_ah,))
                        conn.execute('''INSERT INTO public.zones_stockage
                            (id,amenagement_hydraulique_id,valid,geometry) VALUES (%s,%s,true,
                            ST_Transform(ST_Buffer(ST_GeomFromText('POINT(700010 6600000)',2154),1),3950))''',
                            (uuid4(), overlap_ah))
                    try:
                        context, _ = self.context('admin_alpha', 0)
                        overlap_request = LidarPlanRequest.model_validate({
                            'ensembles': [{'type': 'SE', 'id': str(ids['se'])},
                                          {'type': 'AH', 'id': str(overlap_ah)}], 'vue': view, 'policy_version': 1})
                        original, coverage = _authorized_masks(context, overlap_request, 25)
                        with psycopg.connect(**params0) as conn:
                            union_area, valid = conn.execute('''SELECT ST_Area(g), ST_IsValid(g)
                                FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),2154) AS g) s''',
                                (json.dumps(coverage['masks'][0]['geometry']),)).fetchone()
                        self.assertTrue(valid)
                        self.assertLess(union_area,
                                        sum(mask['area_m2'] for mask in original['masks']))
                    finally:
                        with psycopg.connect(**params0, autocommit=True) as conn:
                            conn.execute('DELETE FROM public.zones_stockage WHERE amenagement_hydraulique_id=%s',
                                         (overlap_ah,))
                            conn.execute('DELETE FROM public.amenagements_hydrauliques WHERE id=%s',
                                         (overlap_ah,))
                    with psycopg.connect(**{**self.admin_params, 'dbname': self.dbnames[0]},
                                         autocommit=True) as conn:
                        conn.execute('''UPDATE public.troncons
                            SET geometry=ST_Transform(ST_GeomFromText('POINT(701000 6600000)',2154),3950)
                            WHERE id=%s''', (ids['troncon'],))
                    se_only = {'ensembles': [body['ensembles'][0]], 'vue': view,
                               'bloc': selection['bloc'], 'policy_version': 1}
                    self.assertEqual(alpha.post(read_path, json=se_only,
                                                headers=self.headers).status_code, 404)
                    self.assertEqual(counts(), before + 1)
                    with psycopg.connect(**{**self.admin_params, 'dbname': self.dbnames[0]},
                                         autocommit=True) as conn:
                        conn.execute('''UPDATE public.troncons
                            SET geometry=ST_Transform(ST_GeomFromText('POINT(700000 6600000)',2154),3950),
                                digue_id=NULL WHERE id=%s''', (ids['troncon'],))
                    self.assertEqual(alpha.post(read_path, json=se_only,
                                                headers=self.headers).status_code, 404)
                    self.assertEqual(counts(), before + 1)
            finally:
                peer.terminate()
                try: peer.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    peer.kill(); peer.communicate(timeout=5)

    def test_lidar_masks_http_one_snapshot_during_concurrent_change(self):
        """L1b : deux ensembles reçoivent la même version même après une écriture."""
        from digues_webapp import lidar_masks

        with self.lidar_mask_tables() as ids:
            client = self.client('admin_alpha')
            path = self.url(0, 'lidar/masks/v1')
            body = {'ensembles': [{'type': 'SE', 'id': str(ids['se'])},
                                  {'type': 'AH', 'id': str(ids['ah'])}]}
            baseline = client.post(path, json=body, headers=self.headers)
            self.assertEqual(baseline.status_code, 200, baseline.text)
            selected, changed = threading.Event(), threading.Event()
            writer_errors = []
            original = lidar_masks.read_business_crs
            calls = []

            def paused_crs(connection):
                self.assertEqual(connection.execute('SHOW transaction_isolation').fetchone()[0],
                                 'repeatable read')
                self.assertEqual(connection.execute('SHOW transaction_read_only').fetchone()[0], 'on')
                value = original(connection)
                calls.append(1)
                selected.set()
                if not changed.wait(10):
                    raise AssertionError('Écriture concurrente absente.')
                return value

            def write_after_snapshot():
                if not selected.wait(10):
                    writer_errors.append('Snapshot absent')
                    changed.set()
                    return
                try:
                    with psycopg.connect(**{**self.admin_params, 'dbname': self.dbnames[0]},
                                         autocommit=True) as writer:
                        writer.execute('''UPDATE public.troncons
                            SET geometry=ST_Transform(
                                ST_GeomFromText('POINT(730000 6600000)',2154),3950),
                                amenagement_hydraulique_id=NULL WHERE id=%s''', (ids['troncon'],))
                except Exception as exc:
                    writer_errors.append(type(exc).__name__)
                finally:
                    changed.set()

            worker = threading.Thread(target=write_after_snapshot)
            with patch.object(lidar_masks, 'read_business_crs', paused_crs):
                worker.start()
                try:
                    during = client.post(path, json=body, headers=self.headers)
                finally:
                    worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(writer_errors, [])
            self.assertEqual(calls, [1])
            self.assertEqual(during.status_code, 200, during.text)
            self.assertEqual(during.json(), baseline.json())
            after = client.post(path, json=body, headers=self.headers)
            self.assertEqual(after.status_code, 200, after.text)
            self.assertNotEqual(after.json()['masks'], baseline.json()['masks'])

    def test_z_create_update_and_journal_are_atomic_in_alpha_only(self):
        client = self.client('editor_alpha')
        response = client.post(self.url(0, 'systemes-endiguement'), json={'libelle': 'New Alpha'}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()['id']
        headers = {**self.headers, 'If-Match': '"1"'}
        response = client.put(self.url(0, f'systemes-endiguement/{identifier}'), json={'libelle': 'Updated Alpha'}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['version'], 2)
        conflict = client.put(self.url(0, f'systemes-endiguement/{identifier}'), json={'libelle': 'stale'}, headers=headers)
        self.assertEqual(conflict.status_code, 409)
        for index, login in ((0, 'reader_alpha'), (2, 'admin_beta')):
            ctx, _ = self.context(login, index)
            with database.open_read_connection(ctx) as conn:
                row = conn.execute('SELECT libelle FROM public.systemes WHERE id=%s', (identifier,)).fetchone()
                count = conn.execute("SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s", (identifier,)).fetchone()[0]
                self.assertEqual(row, ('Updated Alpha',) if index == 0 else None)
                self.assertEqual(count, 2 if index == 0 else 0)
