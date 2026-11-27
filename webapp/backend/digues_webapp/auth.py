"""Frontière HTTP d'identité ; aucune dépendance de connexion métier."""

from collections import deque
from dataclasses import asdict, dataclass
import json
import os
from threading import BoundedSemaphore, Lock
import time
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

from digues_app.control.auth import (
    SESSION_SECONDS, UserIdentity, authenticate, current_user, logout,
)
from digues_app.control.database import ControlDatabaseError, open_control_connection


@dataclass(frozen=True)
class AuthSettings:
    origin: str
    secure: bool

    def __post_init__(self) -> None:
        try:
            url = urlsplit(self.origin)
            port = url.port
        except ValueError:
            raise ControlDatabaseError("Configuration d'origine invalide.") from None
        if (not url.hostname or url.username is not None or url.password is not None
                or any(c.isspace() for c in self.origin) or url.path or url.query
                or url.fragment or url.scheme not in {"https", "http"}
                or (port is not None and not 1 <= port <= 65535)):
            raise ControlDatabaseError("AUTH_ORIGIN doit être une origine explicite sans chemin.")
        if self.secure:
            if url.scheme != "https":
                raise ControlDatabaseError("HTTPS obligatoire hors développement localhost explicite.")
        elif url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ControlDatabaseError("HTTP non sécurisé réservé explicitement à localhost.")

    @property
    def cookie_name(self) -> str:
        return "__Host-digues_session" if self.secure else "digues_session_dev"

    @classmethod
    def from_env(cls, prefix="AUTH"):
        origin = os.getenv(f"{prefix}_ORIGIN", "")
        insecure = os.getenv(f"{prefix}_ALLOW_INSECURE_LOCALHOST", "false")
        try:
            url = urlsplit(origin)
            port = url.port
        except ValueError:
            raise ControlDatabaseError("Configuration d'origine invalide.") from None
        if (insecure not in {"true", "false"} or not url.hostname
                or url.username is not None or url.password is not None or any(c.isspace() for c in origin)
                or url.path or url.query or url.fragment or url.scheme not in {"https", "http"}
                or (port is not None and not 1 <= port <= 65535)):
            raise ControlDatabaseError("AUTH_ORIGIN doit être une origine explicite sans chemin.")
        if insecure == "true":
            if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ControlDatabaseError("HTTP non sécurisé réservé explicitement à localhost.")
        elif url.scheme != "https":
            raise ControlDatabaseError("HTTPS obligatoire hors développement localhost explicite.")
        return cls(origin, insecure != "true")


def get_auth_settings(request: Request) -> AuthSettings:
    if settings := getattr(request.state, "admin_auth_settings", None):
        return settings
    return AuthSettings.from_env()


def require_same_origin(request: Request, settings: AuthSettings = Depends(get_auth_settings)):
    """CSRF : origine exacte configurée + en-tête non simple, sans CORS permissif."""
    if (request.headers.get("origin") != settings.origin
            or request.headers.get("x-digues-csrf") != "1"):
        raise HTTPException(403, "Origine ou protection CSRF invalide.")


class LoginLimiter:
    """Garde locale bornée, par processus : 5/IP/min et 30 tentatives/min au total.

    Compte succès ET échecs. Ne fait pas confiance à X-Forwarded-For ici.
    Une protection de périmètre reste nécessaire pour plusieurs workers/hôtes.
    """

    def __init__(self):
        self.attempts = deque()
        self.lock = Lock()
        self.hash_slots = BoundedSemaphore(2)

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self.lock:
            while self.attempts and self.attempts[0][0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 30 or sum(ip == client for _, ip in self.attempts) >= 5:
                return False
            self.attempts.append((now, client))
            return True


def limit_login(request: Request):
    client = request.client.host if request.client else "unknown"
    if not request.app.state.auth_login_limiter.allow(client):
        raise HTTPException(429, "Trop de tentatives. Réessayer plus tard.", headers={"Retry-After": "60"})


async def login_payload(request: Request) -> tuple[str, str]:
    # Limite avant JSON/Argon2, sans erreur de validation contenant un mot de passe.
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "Un corps JSON est requis.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 8192:
            raise HTTPException(413, "Corps trop volumineux.")
        body.extend(chunk)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "Données de connexion invalides.") from None
    if (not isinstance(data, dict) or set(data) != {"login", "password"}
            or not isinstance(data["login"], str) or len(data["login"]) > 254
            or not isinstance(data["password"], str) or not 1 <= len(data["password"]) <= 1024):
        raise HTTPException(422, "Données de connexion invalides.")
    try:
        data["password"].encode("utf-8")
    except UnicodeError:
        raise HTTPException(422, "Données de connexion invalides.") from None
    return data["login"], data["password"]


def get_current_user(request: Request, settings: AuthSettings = Depends(get_auth_settings)) -> UserIdentity | None:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return None
    with open_control_connection() as connection:
        return current_user(connection, token)


def require_authenticated_user(user: UserIdentity | None = Depends(get_current_user)) -> UserIdentity:
    if user is None:
        raise HTTPException(401, "Authentification requise.")
    return user


def require_superadmin(user: UserIdentity = Depends(require_authenticated_user)) -> UserIdentity:
    if not user.is_superadmin:
        raise HTTPException(403, "Accès superadmin requis.")
    return user


class IdentityNoStoreMiddleware:
    """ASGI direct : les flux métier/SSE ne sont pas enveloppés par call_next."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(("/api/auth/", "/api/admin/")):
            await self.app(scope, receive, send)
            return

        async def no_store(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, no_store)


def register_auth_routes(application):
    # Configuration fournie au processus par le bootstrap, sans lecture de
    # fichier ni sélection dynamique d'une DSN métier par requête.
    application.state.auth_login_limiter = LoginLimiter()
    application.add_middleware(IdentityNoStoreMiddleware)

    @application.exception_handler(ControlDatabaseError)
    async def control_unavailable(_request, _exc):
        return JSONResponse(status_code=503, content={"detail": "Service d'identité indisponible."})

    @application.post("/api/auth/login", tags=["identity"],
                      dependencies=[Depends(require_same_origin), Depends(limit_login)])
    def login_endpoint(request: Request, response: Response,
                       payload: tuple[str, str] = Depends(login_payload),
                       settings: AuthSettings = Depends(get_auth_settings)):
        slots = request.app.state.auth_login_limiter.hash_slots
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "Trop de tentatives. Réessayer plus tard.", headers={"Retry-After": "60"})
        try:
            with open_control_connection() as connection:
                if (request.headers.get('X-Digues-Anonymous-Only') == '1'
                        and current_user(connection, request.cookies.get(settings.cookie_name)) is not None):
                    raise HTTPException(409, 'Session déjà ouverte ; identité conservée.')
                result = authenticate(connection, *payload,
                                      previous_token=request.cookies.get(settings.cookie_name))
        finally:
            slots.release()
        if result is None:
            raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
        user, token = result
        response.set_cookie(settings.cookie_name, token, max_age=SESSION_SECONDS,
                            httponly=True, secure=settings.secure, samesite="strict", path="/")
        return asdict(user)

    @application.post("/api/auth/logout", tags=["identity"], status_code=204,
                      dependencies=[Depends(require_same_origin)])
    def logout_endpoint(request: Request, settings: AuthSettings = Depends(get_auth_settings)):
        with open_control_connection() as connection:
            logout(connection, request.cookies.get(settings.cookie_name))
        response = Response(status_code=204)
        response.delete_cookie(settings.cookie_name, path="/", secure=settings.secure,
                               httponly=True, samesite="strict")
        return response

    @application.get("/api/auth/me", tags=["identity"])
    def me(user: UserIdentity = Depends(require_authenticated_user)):
        return asdict(user)

    @application.get("/api/auth/databases", tags=["identity"])
    def databases(request: Request, user: UserIdentity = Depends(require_authenticated_user),
                  settings: AuthSettings = Depends(get_auth_settings)):
        from .browser_identity import discover_databases
        return discover_databases(user, request.cookies.get(settings.cookie_name, ''))
