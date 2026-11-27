"""Synthetic disk and authorized administration tests; no database or host volume reads."""

import os
import unittest
import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from digues_app.control.auth import UserIdentity
from digues_webapp import admin_basemap_caches as caches, auth
from digues_webapp.app import create_app
from digues_webapp.basemap_catalog import load_basemap_catalog
from digues_webapp.cartographic_cache_catalog import load_cache_products

GIB = 1024**3


def status():
    return {'schema_version':3,'observed_at':'2026-11-14T12:00:00Z',
        'global':{'capacity_bytes':22*GIB,'reserve_bytes':3*GIB},'products':[]}


class RecommendationTest(unittest.TestCase):
    def test_lidar_is_a_global_numeric_cache_product_outside_basemap_catalogue(self):
        product = next(item for item in load_cache_products().products
                       if item.id == "ign-lidar-hd-mnt")
        self.assertEqual(product.scope, "global")
        self.assertEqual(product.artifact.format, "geotiff-float32")
        self.assertEqual(product.artifact.grid_id, "epsg2154-050cm-512-v1")
        self.assertNotIn(product.id, {item.id for item in load_basemap_catalog().basemaps})

    def test_internal_channel_uses_fixed_local_host_and_bearer_token(self):
        received = {}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.update({"path": self.path, "token": self.headers.get("Authorization"),
                    "actor": self.headers.get("X-Admin-Actor")})
                data = json.dumps({"revision": 3}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *_args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            token = "a-secret-longer-than-thirty-two-characters"
            with patch.dict(os.environ, {"BASEMAP_ADMIN_URL": f"http://127.0.0.1:{server.server_port}",
                "BASEMAP_ADMIN_TOKEN": token}):
                self.assertEqual(caches.renderer_call("GET", "/internal/cache-admin",
                    "11111111-1111-4111-8111-111111111111")["revision"], 3)
            self.assertEqual(received["path"], "/internal/cache-admin")
            self.assertEqual(received["token"], f"Bearer {token}")
            self.assertEqual(received["actor"], "11111111-1111-4111-8111-111111111111")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


class AdminApiTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1",
            "AUTH_ORIGIN": "https://business.test", "ADMIN_ORIGIN": "https://admin.test",
            "ADMIN_ALLOW_INSECURE_LOCALHOST": "false"}))
        self.app = create_app()
        self.user = UserIdentity(uuid4(), "operator", "Operator", "active", True)
        self.app.dependency_overrides[auth.get_current_user] = lambda: self.user
        self.client = self.enterContext(TestClient(self.app, base_url="https://admin.test"))
        self.renderer = self.enterContext(patch.object(caches, "renderer_call", return_value=status()))
        self.enterContext(patch("psycopg.connect", side_effect=AssertionError("No PostgreSQL")))

    def test_superadmin_reads_and_removed_controls_return_404(self):
        self.assertEqual(self.client.get('/api/admin/basemap-caches').status_code, 200)
        self.assertEqual(self.client.patch('/api/admin/basemap-caches', json={}).status_code, 405)
        self.assertEqual(self.client.post('/api/admin/basemap-caches/ign-plan/purge', json={}).status_code, 404)
        self.user = replace(self.user, is_superadmin=False)
        self.assertEqual(self.client.get('/api/admin/basemap-caches').status_code, 403)

    def test_cache_view_is_existing_admin_shell_and_not_public(self):
        redirect = self.client.get("/caches", follow_redirects=False)
        self.assertEqual(redirect.status_code, 303)
        self.assertEqual(redirect.headers["location"], "/diagnostics#caches")
        page = self.client.get(redirect.headers["location"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("basemap-caches.js", page.text)
        asset = self.client.get("/admin-assets/basemap-caches.js")
        self.assertEqual(asset.status_code, 200)
        self.assertNotIn("localStorage", asset.text)
        self.assertEqual(self.client.get("https://business.test/caches").status_code, 403)
        self.user = replace(self.user, is_superadmin=False)
        self.assertEqual(self.client.get('/caches', follow_redirects=False).status_code, 403)
        self.assertEqual(self.client.get('/diagnostics', follow_redirects=False).status_code, 403)
        self.user = None
        login = self.client.get('/caches', follow_redirects=False)
        self.assertEqual(login.status_code, 303)
        self.assertEqual(login.headers['location'], '/login?next=/caches')



if __name__ == "__main__":
    unittest.main()
