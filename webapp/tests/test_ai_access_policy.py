"""SQL/AI separation and fail-closed contracts without an external database."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from digues_app.control.ai_policy import AI_SELECT_COLUMNS, validate_ai_select_security
from digues_webapp import database, business_context, ai, schema_context
from digues_webapp.connection_profiles import resolve_profile
from webapp.tests.test_business_context import context


class AiAccessPolicyTest(unittest.TestCase):
    def test_ah_and_zone_columns_follow_current_business_contract(self):
        ah = AI_SELECT_COLUMNS['public.amenagements_hydrauliques']
        zone = AI_SELECT_COLUMNS['public.zones_stockage']
        self.assertIn('designation', ah)
        self.assertNotIn('geometry', ah)
        self.assertNotIn('type_id', ah)
        for column in ('geometry', 'type_id', 'capacite_stockage',
                       'amenagement_hydraulique_id'):
            self.assertIn(column, zone)
        self.assertIn('zone_stockage_id', AI_SELECT_COLUMNS['public.observations'])

    def test_point_disorder_view_policy_uses_generic_projected_coordinates(self):
        columns = AI_SELECT_COLUMNS['public.desordres_points_saisie']
        self.assertIn('coord_x', columns)
        self.assertIn('coord_y', columns)
        self.assertNotIn('coord_x_3950', columns)
        self.assertNotIn('coord_y_3950', columns)

    def test_missing_profiles_never_fallback_and_roles_are_distinct(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.json'
            profile = {'cluster_ref': 'local', 'database_name': 'alpha',
                **{key: f'host=localhost dbname=alpha user={key}'
                   for key in ('runtime', 'sql_readonly', 'ai_readonly', 'readonly')}}
            with patch.dict(os.environ, {'BUSINESS_CONNECTION_PROFILES_FILE': str(path)}):
                for missing in ('sql_readonly', 'ai_readonly'):
                    path.write_text(json.dumps({'alpha': {k: v for k, v in profile.items() if k != missing}}))
                    with self.assertRaises(database.WebDatabaseConfigurationError):
                        resolve_profile(context(), kind=missing)
                profile['ai_readonly'] = profile['sql_readonly']
                path.write_text(json.dumps({'alpha': profile}))
                with self.assertRaises(database.WebDatabaseConfigurationError):
                    resolve_profile(context(), kind='ai_readonly')

    def test_legacy_ai_never_uses_dotenv_sql_or_runtime(self):
        with patch.dict(os.environ, {'DATABASE_READONLY_URL': 'unused', 'DATABASE_URL': 'unused'}, clear=True), \
                patch('psycopg.connect') as connect, patch('builtins.open', side_effect=AssertionError('No file input')) as config_file:
            with self.assertRaises(database.WebDatabaseError):
                with database.open_ai_connection():
                    self.fail('Missing AI credential')
            connect.assert_not_called()
            config_file.assert_not_called()

    def test_revalidated_ai_factory_captures_ai_opener(self):
        ctx = context()
        with patch.object(business_context, 'resolve_business_context', return_value=ctx), \
                patch.object(database, 'open_ai_connection', return_value=nullcontext('AI')) as opener, \
                patch.object(database, 'open_sql_connection') as human, \
                patch.object(database, 'open_read_connection') as runtime:
            factory, _ = business_context.revalidated_factory(ctx, Mock(), 'token', 'ai.read', ai=True)
            with factory() as value:
                self.assertEqual(value, 'AI')
            opener.assert_called_once_with(ctx)
            human.assert_not_called()
            runtime.assert_not_called()

    def test_legacy_sql_tool_also_uses_ai_factory(self):
        call = {'function': {'name': ai.BUSINESS_DATABASE_TOOL_NAME, 'arguments': '{"sql":"SELECT 1"}'}}
        with patch.object(ai, 'execute_readonly_query', return_value={}) as execute:
            ai._run_sql_tool(call)
        execute.assert_called_once_with('SELECT 1', connection_factory=database.open_ai_connection)

    def test_ai_task_cannot_accidentally_request_runtime_or_human_sql(self):
        for options in ({}, {'sql': True}, {'ai': True, 'sql': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                business_context.revalidated_factory(context(), Mock(), 'token', 'ai.read', **options)

    def test_policy_rejects_sensitive_new_columns_and_other_schemas(self):
        for row in (('public', 'contacts', 'nom', False), ('public', 'photos', 'id', False),
                    ('public', 'observations', 'observateur_id', False),
                    ('public', 'systemes', 'new_personal_column', False),
                    ('private', 'systemes', 'id', False), ('public', 'systemes', 'id', True)):
            cur = Mock()
            cur.fetchall.return_value = [row]
            with self.subTest(row=row), self.assertRaises(ValueError):
                validate_ai_select_security(cur)

    def test_introspection_filters_sensitive_columns_and_both_fk_ends(self):
        cur = Mock()
        cur.fetchall.side_effect = [
            [('public', 'observations', 'TABLE', 'id', 'uuid', False),
             ('public', 'observations', 'TABLE', 'observateur_id', 'uuid', True),
             ('public', 'contacts', 'TABLE', 'nom', 'text', True)],
            [('public', 'observations', 'id'), ('public', 'contacts', 'id')],
            [('public', 'observations', 'observateur_id', 'public', 'contacts', 'id', 'fk', 1)],
        ]
        conn = Mock()
        conn.cursor.return_value = nullcontext(cur)
        columns, keys, fks = schema_context.introspect_ai_schema(conn)
        self.assertEqual(len(columns), 1)
        self.assertEqual(keys, [('public', 'observations', 'id')])
        self.assertEqual(fks, [])
        for name in ('contacts', 'organismes', 'contact_organisme_periods', 'photos',
                     'troncon_source_gestions', 'troncon_source_role_localisations'):
            self.assertNotIn(name, AI_SELECT_COLUMNS)
