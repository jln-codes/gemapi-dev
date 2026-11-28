"""Frontière monobase tant que l'identité n'est pas arbitrée."""
from unittest import TestCase
from unittest.mock import patch
import re

from fastapi.testclient import TestClient

from digues_webapp.app import create_app
from digues_webapp.protected_routes import PROTECTED_ROUTES


class InstanceAccessTest(TestCase):
    def setUp(self):
        self.client = TestClient(create_app())

    def test_shell_and_catalog_do_not_use_postgres(self):
        with patch("psycopg.connect", side_effect=AssertionError("appel PostgreSQL")):
            for path in ("/", "/carte", "/base", "/api/health", "/api/config", "/api/basemaps"):
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
            config = self.client.get("/api/config").json()
            self.assertEqual(set(config), {"build_id", "app_version"})

    def test_each_protected_method_denies_even_with_client_provided_context(self):
        with patch("psycopg.connect", side_effect=AssertionError("appel PostgreSQL")):
            for method, template in PROTECTED_ROUTES:
                path = re.sub(r"\{[^}]+\}", "50bfe115-8e16-4899-bbf4-5917c03e18f9", template)
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, params={
                        "base": "autre", "context_user_id": "autre", "context_instance_id": "autre",
                    }, headers={"X-Digues-CSRF": "1", "Cookie": "session=fictive"})
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_login_requires_a_valid_origin(self):
        response = self.client.post("/api/auth/login", json={})
        self.assertEqual(response.status_code, 403)

    def test_no_old_routing_or_worker_api(self):
        for method, path in (
            ("GET", "/api/bases"),
            ("GET", "/api/bases/autre/troncons"),
            ("GET", "/api/auth/databases"),
            ("POST", "/api/import-sirs"),
            ("GET", "/api/admin/jobs"),
            ("GET", "/internal/cache-admin"),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path).status_code, 404)
