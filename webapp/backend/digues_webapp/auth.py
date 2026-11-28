"""HTTP authentication for the single deployment database."""
import json
import logging
import multiprocessing
import os
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import psycopg
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import session_database as database
from .sessions import Capacity, NoSession, RateLimited, Sessions, WrongContext

log = logging.getLogger("uvicorn.error")

NO_STORE = {"Cache-Control": "no-store"}
CSRF_HEADER = "X-Gemapi-CSRF"


class Authentication:
    def __init__(self):
        self.sessions = Sessions()
        self.origin = os.getenv("AUTH_ORIGIN", "http://127.0.0.1:8080")
        parts = urlsplit(self.origin)
        self.secure = parts.scheme == "https"
        if (not parts.hostname or parts.username is not None or parts.password is not None
                or self.origin != f"{parts.scheme}://{parts.netloc}"
                or not (self.secure or parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("AUTH_ORIGIN doit être une origine HTTPS ou une boucle locale HTTP.")
        self.cookie = "__Host-gemapi_session" if self.secure else "gemapi_session_local"
        self.demo = os.getenv("AUTH_DEPLOYMENT", "production") == "demo"
        self.demo_login = os.getenv("AUTH_DEMO_LOGIN") if self.demo else None
        self.prefill = os.getenv("AUTH_DEMO_PREFILL", "0") == "1"
        self.demo_verified = False
        if self.prefill and not (self.demo_login and os.getenv("AUTH_DEMO_PASSWORD")):
            raise ValueError("Préremplissage réservé au compte public du déploiement de démonstration.")

    def verify_demo(self):
        password = os.environ["AUTH_DEMO_PASSWORD"]
        try:
            if any(self.demo_login == user or password == secret for user, secret in database.technical_credentials()):
                return
            with self.sessions.reservation(self.demo_login, demo=True):
                conn, _ = database.open_individual(self.demo_login, password, self.sessions.sql_idle_seconds)
                conn.close()
            self.demo_verified = True
        except (database.AccessDenied, database.BadCredentials, database.DatabaseUnavailable, psycopg.Error):
            self.demo_verified = False

    @asynccontextmanager
    async def lifespan(self, app):
        if os.getenv("WEB_CONCURRENCY", "1") != "1" or multiprocessing.parent_process() is not None:
            raise RuntimeError("GEMAPI exige un worker HTTP unique.")
        stop = threading.Event()

        def reap():
            while not stop.wait(min(5, self.sessions.idle, self.sessions.absolute)):
                self.sessions.clean()

        if self.prefill:
            await run_in_threadpool(self.verify_demo)
        thread = threading.Thread(target=reap, name="gemapi-session-cleanup", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            await run_in_threadpool(thread.join)
            await run_in_threadpool(self.sessions.clean, True)

    def same_origin(self, request):
        if request.headers.get("origin") != self.origin or request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, "Origine ou protection CSRF invalide.", headers=NO_STORE)

    def token(self, request):
        return request.cookies.get(self.cookie)

    def register(self, app):
        errors = {
            Capacity: (503, "Capacité de connexion atteinte. Réessayez plus tard."),
            RateLimited: (429, "Trop de tentatives. Réessayez plus tard."),
            database.BadCredentials: (401, "Identifiant ou mot de passe incorrect."),
            database.AccessDenied: (403, "Compte non autorisé dans cette instance."),
            NoSession: (401, "Session absente ou expirée."),
            WrongContext: (409, "La session a changé."),
            database.DatabaseUnavailable: (503, "Connexion PostgreSQL indisponible ou non conforme."),
        }

        async def error_response(request, exc):
            status, message = errors[type(exc)]
            if request.url.path == "/api/auth/login" and isinstance(
                    exc, (database.BadCredentials, database.AccessDenied, database.DatabaseUnavailable)):
                log.info("GEMAPI connexion refusée (HTTP %d)", status)
            headers = dict(NO_STORE)
            if status == 429:
                headers["Retry-After"] = "60"
            return JSONResponse({"detail": message}, status_code=status, headers=headers)

        for kind in errors:
            app.add_exception_handler(kind, error_response)

        @app.post("/api/auth/login")
        async def login(request: Request):
            self.same_origin(request)
            credentials = await login_body(request)
            username = credentials["login"]
            self.sessions.attempt(request.client.host if request.client else "", username)
            token, identity = await run_in_threadpool(
                self.sessions.login, username, credentials.pop("password"), self.token(request),
                self.demo and username == self.demo_login)
            log.info("GEMAPI session ouverte")
            response = JSONResponse(identity, headers=NO_STORE)
            response.set_cookie(self.cookie, token, httponly=True, secure=self.secure, samesite="strict",
                                path="/", max_age=self.sessions.absolute)
            return response

        @app.get("/api/auth/me")
        def me(request: Request):
            with self.sessions.transaction(self.token(request)) as session:
                interval_ms = min(30_000, self.sessions.idle * 1000 // 3)
                return JSONResponse(session.public(), headers={
                    **NO_STORE, "X-Gemapi-Activity-Interval": str(interval_ms),
                })

        @app.post("/api/auth/activity")
        def activity(request: Request):
            self.same_origin(request)
            with self.sessions.transaction(self.token(request), expected=request.headers.get("X-Gemapi-Context", ""),
                                           activity=True):
                return JSONResponse({}, headers=NO_STORE)

        @app.post("/api/auth/logout")
        def logout(request: Request):
            self.same_origin(request)
            self.sessions.logout(self.token(request), request.headers.get("X-Gemapi-Context", ""))
            response = JSONResponse({}, headers=NO_STORE)
            response.delete_cookie(self.cookie, path="/", secure=self.secure, httponly=True, samesite="strict")
            return response

        @app.post("/api/auth/demo")
        def demo(request: Request):
            self.same_origin(request)
            public = {}
            if self.prefill and self.demo_verified:
                public = {"login": self.demo_login, "password": os.environ["AUTH_DEMO_PASSWORD"]}
            return JSONResponse(public, headers=NO_STORE)


async def login_body(request):
    if request.headers.get("content-type", "").partition(";")[0].strip() != "application/json":
        raise HTTPException(415, "Corps JSON requis.", headers=NO_STORE)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 8192:
            raise HTTPException(413, "Formulaire trop volumineux.", headers=NO_STORE)
        body.extend(chunk)
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or set(data) != {"login", "password"}:
            raise ValueError
        for key, limit in (("login", 63), ("password", 4096)):
            value = data[key]
            if not isinstance(value, str) or not value or len(value.encode()) > limit or "\x00" in value:
                raise ValueError
        return data
    except (ValueError, RecursionError):
        raise HTTPException(400, "Formulaire de connexion invalide.", headers=NO_STORE) from None
    finally:
        body.clear()
