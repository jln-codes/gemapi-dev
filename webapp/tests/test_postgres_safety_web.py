"""Web integration suites must skip before opening an application DSN."""

import importlib
import os
import unittest
from unittest.mock import patch


class WebTestTargetSafetyTest(unittest.TestCase):
    def test_all_real_web_entrypoints_need_explicit_test_targets(self):
        cases = (
            ("test_data_views", "DataViewsPostGISIntegrationTest"),
            ("test_knowledge", "KnowledgePostgreSQLIntegrationTest"),
            ("test_territoire_import", "TerritoirePostGISIntegrationTest"),
            ("test_web_api", "WebPostGISIntegrationTest"),
            ("test_readonly_sql", "ReadonlySqlPostGISIntegrationTest"),
            ("test_resource_versions", "ResourceVersionsPostgresTest"),
            # The contextual AI/PostGIS integration now lives in BusinessPostgresTest.
            ("test_business_postgres", "BusinessPostgresTest"),
        )
        remote = {
            "DATABASE_URL": "postgresql://writer@remote.invalid/business",
            "CONTROL_DATABASE_URL": "postgresql://admin@remote.invalid/control",
            "DATABASE_READONLY_URL": "postgresql://reader@remote.invalid/business",
        }
        with patch.dict(os.environ, remote, clear=True), patch("psycopg.connect") as connect, patch("dotenv.load_dotenv"):
            for module, name in cases:
                with self.subTest(suite=name):
                    suite = getattr(importlib.import_module(f"webapp.tests.{module}"), name)
                    with self.assertRaises(unittest.SkipTest):
                        suite.setUpClass()
            connect.assert_not_called()
            # Importing the FastAPI module builds its production app and calls
            # load_dotenv; the integration classes still skip before any SQL.
