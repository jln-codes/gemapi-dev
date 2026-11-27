"""API identité, cookies, CSRF et gardes sans connexion extérieure."""

from contextlib import nullcontext
import os
import secrets
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient

from digues_app.control.auth import UserIdentity
from digues_app.control.database import ControlDatabaseError
from digues_webapp.app import create_app
from digues_webapp import auth


class AuthApiTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.settings = auth.AuthSettings("https://testserver", True)
        self.app.dependency_overrides[auth.get_auth_settings] = lambda: self.settings
        self.conn = Mock()
        self.opened = self.enterContext(patch.object(auth, "open_control_connection", side_effect=lambda: nullcontext(self.conn)))
        self.user = UserIdentity(uuid4(), "admin", "Admin", "active", True)
        self.token = secrets.token_urlsafe(32)
        self.password = secrets.token_urlsafe(24)
        self.client = self.enterContext(TestClient(self.app, base_url="https://testserver"))
        self.headers = {"Origin": "https://testserver", "X-Digues-CSRF": "1"}

    def login(self, **kwargs):
        return self.client.post("/api/auth/login", json={"login": "admin", "password": self.password},
                                headers=self.headers, **kwargs)

    def test_login_cookie_and_response_contain_no_credential(self):
        with patch.object(auth, "authenticate", return_value=(self.user, self.token)):
            response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"id", "login", "display_name", "status", "is_superadmin"})
        for value in (self.token, self.password, "password_hash"):
            self.assertNotIn(value, response.text)
        cookie = response.headers["set-cookie"]
        for flag in ("__Host-digues_session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=28800"):
            self.assertIn(flag, cookie)
        self.assertNotIn("digues_session_dev=", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_me_missing_invalid_and_valid_session(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.opened.assert_not_called()
        self.client.cookies.set("digues_session_dev", "wrong-development-token")
        self.client.cookies.set(self.settings.cookie_name, self.token)
        with patch.object(auth, "current_user", return_value=None) as resolve:
            self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        resolve.assert_called_once_with(self.conn, self.token)
        with patch.object(auth, "current_user", return_value=self.user) as resolve:
            response = self.client.get("/api/auth/me")
        resolve.assert_called_once_with(self.conn, self.token)
        self.assertEqual(response.json()["id"], str(self.user.id))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_anonymous_only_login_never_replaces_an_existing_session(self):
        self.headers['X-Digues-Anonymous-Only'] = '1'
        with patch.object(auth, 'current_user', return_value=self.user), \
             patch.object(auth, 'authenticate') as authenticate:
            response = self.login()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('set-cookie', response.headers)
        authenticate.assert_not_called()
        with patch.object(auth, 'current_user', return_value=None), \
             patch.object(auth, 'authenticate', return_value=None) as authenticate:
            response = self.login()
        self.assertEqual(response.status_code, 401)  # Password still mandatory/verified.
        authenticate.assert_called_once()

    def test_all_bad_credentials_have_same_public_response(self):
        responses = []
        for reason in ("absent", "password", "inactive"):
            with self.subTest(reason=reason), patch.object(auth, "authenticate", return_value=None):
                response = self.login()
                responses.append((response.status_code, response.json()))
                self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(responses, [(401, {"detail": "Identifiant ou mot de passe incorrect."})] * 3)

    def test_csrf_missing_null_cross_origin_and_sibling_refused(self):
        for path in ("login", "logout"):
            for headers in ({}, {"Origin": "null", "X-Digues-CSRF": "1"},
                            {"Origin": "https://evil.test", "X-Digues-CSRF": "1"},
                            {"Origin": "https://admin.testserver", "X-Digues-CSRF": "1"},
                            {"Origin": "https://testserver"}):
                response = self.client.post(f"/api/auth/{path}", json={}, headers=headers)
                self.assertEqual(response.status_code, 403)
        self.opened.assert_not_called()

    def test_logout_revokes_before_clearing_host_cookie(self):
        self.client.cookies.set("digues_session_dev", "wrong-development-token")
        self.client.cookies.set(self.settings.cookie_name, self.token)
        with patch.object(auth, "logout") as revoke:
            response = self.client.post("/api/auth/logout", headers=self.headers)
        revoke.assert_called_once_with(self.conn, self.token)
        self.assertEqual(response.status_code, 204)
        cookie = response.headers["set-cookie"]
        self.assertTrue(cookie.startswith("__Host-digues_session="), cookie)
        for flag in ("Max-Age=0", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertNotIn("digues_session_dev=", cookie)
        self.assertNotIn("Domain=", cookie)

    def test_superadmin_guard_uses_server_identity(self):
        @self.app.get("/test-admin", dependencies=[Depends(auth.require_superadmin)])
        def guarded():
            return {"ok": True}

        self.assertEqual(self.client.get("/test-admin?is_superadmin=true").status_code, 401)
        self.client.cookies.set(self.settings.cookie_name, self.token)
        normal = UserIdentity(uuid4(), "normal", "Normal", "active", False)
        with patch.object(auth, "current_user", return_value=normal):
            self.assertEqual(self.client.get("/test-admin", headers={"is_superadmin": "true"}).status_code, 403)
        with patch.object(auth, "current_user", return_value=self.user):
            self.assertEqual(self.client.get("/test-admin").status_code, 200)

    def test_control_unavailable_is_redacted_and_business_never_used(self):
        self.opened.side_effect = ControlDatabaseError("private detail")
        with patch("digues_webapp.database.open_read_connection", side_effect=AssertionError("business")):
            response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private detail", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_json_validation_is_bounded_and_redacted(self):
        for payload in ({"login": "admin", "password": self.password, "extra": self.password},
                        {"login": "admin", "password": [self.password]}, []):
            response = self.client.post("/api/auth/login", json=payload, headers=self.headers)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.password, response.text)
        response = self.client.post("/api/auth/login", content="x" * 8193,
                                    headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.opened.assert_not_called()

    def test_rate_limit_before_password_work(self):
        with patch.object(auth, "authenticate", return_value=None) as authenticate:
            for _ in range(5):
                self.assertEqual(self.login().status_code, 401)
            self.assertEqual(self.login().status_code, 429)
            self.assertEqual(authenticate.call_count, 5)

    def test_hash_concurrency_is_bounded_and_connection_not_opened_when_busy(self):
        slots = self.app.state.auth_login_limiter.hash_slots
        slots.acquire()
        slots.acquire()
        try:
            self.assertEqual(self.login().status_code, 429)
            self.opened.assert_not_called()
        finally:
            slots.release()
            slots.release()

    def test_local_http_cookie_is_explicit_and_separate(self):
        self.settings = auth.AuthSettings("http://localhost:8000", False)
        application = create_app()
        application.dependency_overrides[auth.get_auth_settings] = lambda: self.settings
        with TestClient(application, base_url=self.settings.origin) as client:
            with patch.object(auth, "authenticate", return_value=(self.user, self.token)):
                response = client.post("/api/auth/login", json={"login": "admin", "password": self.password},
                                       headers={"Origin": self.settings.origin, "X-Digues-CSRF": "1"})
            self.assertEqual(response.status_code, 200)
            cookie = response.headers["set-cookie"]
            self.assertTrue(cookie.startswith("digues_session_dev="), cookie)
            for flag in ("HttpOnly", "SameSite=strict", "Path=/", "Max-Age=28800"):
                self.assertIn(flag, cookie)
            self.assertNotIn("__Host-", cookie)
            self.assertNotIn("Secure", cookie)
            self.assertNotIn("Domain=", cookie)

    def test_local_http_reads_and_deletes_only_development_cookie(self):
        self.settings = auth.AuthSettings("http://localhost:8000", False)
        application = create_app()
        application.dependency_overrides[auth.get_auth_settings] = lambda: self.settings
        with TestClient(application, base_url=self.settings.origin) as client:
            client.cookies.set("__Host-digues_session", "wrong-production-token")
            client.cookies.set("digues_session_dev", self.token)
            with patch.object(auth, "current_user", return_value=self.user) as resolve:
                response = client.get("/api/auth/me")
            self.assertEqual(response.status_code, 200)
            resolve.assert_called_once_with(self.conn, self.token)

            with patch.object(auth, "logout") as revoke:
                response = client.post(
                    "/api/auth/logout",
                    headers={"Origin": self.settings.origin, "X-Digues-CSRF": "1"},
                )
            revoke.assert_called_once_with(self.conn, self.token)
            self.assertEqual(response.status_code, 204)
            cookie = response.headers["set-cookie"]
            self.assertTrue(cookie.startswith("digues_session_dev="), cookie)
            for flag in ("Max-Age=0", "HttpOnly", "SameSite=strict", "Path=/"):
                self.assertIn(flag, cookie)
            self.assertNotIn("__Host-", cookie)
            self.assertNotIn("Secure", cookie)
            self.assertNotIn("Domain=", cookie)

    def test_no_experimental_admin_asset(self):
        for path in ("/admin.html", "/static/admin.html"):
            self.assertEqual(self.client.get(path).status_code, 404)
        for path in ("/admin", "/api/admin/users"):
            self.assertEqual(self.client.get(path).status_code, 403)


class AuthSettingsTest(unittest.TestCase):
    def test_secure_default_and_localhost_exception(self):
        with patch.dict(os.environ, {"AUTH_ORIGIN": "https://admin.example.test"}, clear=True):
            self.assertTrue(auth.AuthSettings.from_env().secure)
        with patch.dict(os.environ, {"AUTH_ORIGIN": "http://localhost:8000", "AUTH_ALLOW_INSECURE_LOCALHOST": "true"}, clear=True):
            settings = auth.AuthSettings.from_env()
            self.assertFalse(settings.secure)
            self.assertEqual(settings.cookie_name, "digues_session_dev")
        for origin, insecure in (("", "false"), ("http://localhost:8000", "false"),
                                 ("http://example.test", "true"), ("https://example.test/path", "false"),
                                 ("https://example.test", "typo")):
            with patch.dict(os.environ, {"AUTH_ORIGIN": origin, "AUTH_ALLOW_INSECURE_LOCALHOST": insecure}, clear=True):
                with self.assertRaises(ControlDatabaseError):
                    auth.AuthSettings.from_env()

    def test_insecure_settings_are_impossible_outside_http_loopback(self):
        for origin in (
            "http://example.test",
            "https://example.test",
            "http://admin.digues-app.fr",
            "http://localhost.example.test",
        ):
            with self.subTest(origin=origin), self.assertRaises(ControlDatabaseError):
                auth.AuthSettings(origin, False)
        for origin in ("http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"):
            with self.subTest(origin=origin):
                settings = auth.AuthSettings(origin, False)
                self.assertEqual(settings.cookie_name, "digues_session_dev")

    def test_limiter_global_bound_and_expiry(self):
        limiter = auth.LoginLimiter()
        with patch.object(auth.time, "monotonic", return_value=100):
            for i in range(30):
                self.assertTrue(limiter.allow(str(i)))
            self.assertFalse(limiter.allow("other"))
            self.assertEqual(len(limiter.attempts), 30)
        with patch.object(auth.time, "monotonic", return_value=161):
            self.assertTrue(limiter.allow("other"))
            self.assertEqual(len(limiter.attempts), 1)
