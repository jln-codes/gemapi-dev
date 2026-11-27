"""Client PISTE borné et cache OAuth2 pour l'adaptateur Légifrance."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import requests


PISTE_TIMEOUT_SECONDS = 8
PISTE_MAX_RESPONSE_BYTES = 1_000_000
PISTE_TOKEN_EXPIRY_MARGIN_SECONDS = 60


@dataclass(frozen=True)
class PisteEndpoints:
    oauth_url: str
    api_base_url: str


PISTE_ENDPOINTS = {
    "sandbox": PisteEndpoints(
        oauth_url="https://sandbox-oauth.piste.gouv.fr/api/oauth/token",
        api_base_url="https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app",
    ),
    "production": PisteEndpoints(
        oauth_url="https://oauth.piste.gouv.fr/api/oauth/token",
        api_base_url="https://api.piste.gouv.fr/dila/legifrance/lf-engine-app",
    ),
}

PISTE_API_PATHS = frozenset({
    "/search",
    "/consult/getArticle",
    "/consult/legiPart",
})


class PisteError(RuntimeError):
    """Erreur PISTE nettoyée de toute donnée d'authentification."""


class PisteConfigurationError(PisteError):
    """Configuration Légifrance absente ou invalide."""


class PisteAuthenticationError(PisteError):
    """Authentification OAuth refusée."""


class PisteUnavailableError(PisteError):
    """Service PISTE indisponible ou réponse invalide."""


class PisteTimeoutError(PisteUnavailableError):
    """Délai PISTE dépassé."""


class PisteRateLimitError(PisteUnavailableError):
    """Quota ou limite de débit PISTE atteint."""


class PisteUnauthorizedError(PisteAuthenticationError):
    """Bearer token refusé par l'API."""


class PisteDocumentUnavailableError(PisteUnavailableError):
    """Document absent ou non consultable dans l'API."""


@dataclass(frozen=True)
class PisteConfig:
    environment: str
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    endpoints: PisteEndpoints

    @classmethod
    def from_environment(cls) -> "PisteConfig":
        environment = os.getenv("LEGIFRANCE_ENV", "sandbox").strip().lower()
        if environment not in PISTE_ENDPOINTS:
            raise PisteConfigurationError(
                "L’environnement Légifrance doit être sandbox ou production."
            )
        client_id = os.getenv("LEGIFRANCE_CLIENT_ID", "").strip()
        client_secret = os.getenv("LEGIFRANCE_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise PisteConfigurationError(
                "L’accès Légifrance n’est pas configuré sur le serveur."
            )
        return cls(environment, client_id, client_secret, PISTE_ENDPOINTS[environment])


@dataclass(frozen=True)
class PisteHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    is_redirect: bool = False


class PisteTransport:
    """POST HTTPS vers les seuls endpoints PISTE codés en dur."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = PISTE_TIMEOUT_SECONDS,
        max_response_bytes: int = PISTE_MAX_RESPONSE_BYTES,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    @staticmethod
    def _validate_exact_url(url: str, expected_url: str) -> None:
        parsed = urlsplit(url)
        if (
            url != expected_url
            or parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise PisteUnavailableError("Destination PISTE refusée.")

    def _post(self, url: str, expected_url: str, **kwargs: Any) -> PisteHttpResponse:
        self._validate_exact_url(url, expected_url)
        try:
            response = self.session.post(
                url,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise PisteTimeoutError("L’API Légifrance n’a pas répondu à temps.") from exc
        except requests.RequestException as exc:
            raise PisteUnavailableError(
                "L’API Légifrance est temporairement indisponible."
            ) from exc
        try:
            if response.is_redirect or response.is_permanent_redirect:
                raise PisteUnavailableError("Redirection PISTE refusée.")
            content_length = response.headers.get("content-length")
            try:
                declared_size = int(content_length) if content_length else 0
            except ValueError as exc:
                raise PisteUnavailableError("Réponse PISTE invalide.") from exc
            if declared_size > self.max_response_bytes:
                raise PisteUnavailableError("Réponse PISTE trop volumineuse.")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=65_536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > self.max_response_bytes:
                    raise PisteUnavailableError("Réponse PISTE trop volumineuse.")
                chunks.append(chunk)
            return PisteHttpResponse(
                status_code=response.status_code,
                headers={key.lower(): value for key, value in response.headers.items()},
                body=b"".join(chunks),
                is_redirect=False,
            )
        except requests.Timeout as exc:
            raise PisteTimeoutError(
                "L’API Légifrance n’a pas répondu à temps."
            ) from exc
        except requests.RequestException as exc:
            raise PisteUnavailableError(
                "L’API Légifrance est temporairement indisponible."
            ) from exc
        finally:
            response.close()

    def request_token(self, config: PisteConfig) -> dict[str, Any]:
        response = self._post(
            config.endpoints.oauth_url,
            config.endpoints.oauth_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "scope": "openid",
            },
        )
        if response.status_code in {400, 401, 403}:
            raise PisteAuthenticationError("Authentification Légifrance refusée.")
        if response.status_code == 429:
            raise PisteRateLimitError("Le quota Légifrance est atteint.")
        if response.status_code != 200:
            raise PisteUnavailableError("Le service OAuth PISTE est indisponible.")
        return _json_object(response)

    def request_api(
        self,
        config: PisteConfig,
        path: str,
        access_token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if path not in PISTE_API_PATHS:
            raise PisteUnavailableError("Opération Légifrance refusée.")
        expected_url = f"{config.endpoints.api_base_url}{path}"
        response = self._post(
            expected_url,
            expected_url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code == 401:
            raise PisteUnauthorizedError("Authentification Légifrance expirée.")
        if response.status_code == 403:
            raise PisteAuthenticationError("Accès à l’API Légifrance refusé.")
        if response.status_code == 429:
            raise PisteRateLimitError("Le quota Légifrance est atteint.")
        if response.status_code == 404:
            raise PisteDocumentUnavailableError(
                "Le document Légifrance n’est pas disponible."
            )
        if response.status_code >= 500:
            raise PisteUnavailableError("L’API Légifrance est temporairement indisponible.")
        if response.status_code != 200:
            raise PisteUnavailableError("La requête Légifrance a été refusée.")
        return _json_object(response)


def _json_object(response: PisteHttpResponse) -> dict[str, Any]:
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PisteUnavailableError("Réponse PISTE invalide.") from exc
    if not isinstance(payload, dict):
        raise PisteUnavailableError("Réponse PISTE invalide.")
    return payload


class PisteOAuthClient:
    """Cache en mémoire d'un bearer token, protégé contre les rafales concurrentes."""

    def __init__(
        self,
        config: PisteConfig,
        transport: PisteTransport,
        *,
        clock: Callable[[], float] = time.monotonic,
        expiry_margin: int = PISTE_TOKEN_EXPIRY_MARGIN_SECONDS,
    ):
        self.config = config
        self.transport = transport
        self.clock = clock
        self.expiry_margin = expiry_margin
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at = 0.0

    def access_token(self) -> str:
        now = self.clock()
        if self._access_token and now < self._expires_at - self.expiry_margin:
            return self._access_token
        with self._lock:
            now = self.clock()
            if self._access_token and now < self._expires_at - self.expiry_margin:
                return self._access_token
            payload = self.transport.request_token(self.config)
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            token_type = payload.get("token_type", "Bearer")
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(expires_in, (int, float))
                or isinstance(expires_in, bool)
                or expires_in <= 0
                or not isinstance(token_type, str)
                or token_type.lower() != "bearer"
            ):
                raise PisteUnavailableError("Réponse OAuth PISTE invalide.")
            self._access_token = token
            self._expires_at = now + float(expires_in)
            return token

    def invalidate(self, rejected_token: str) -> None:
        with self._lock:
            if self._access_token == rejected_token:
                self._access_token = None
                self._expires_at = 0.0


class PisteLegifranceApi:
    """Opérations officielles minimales utilisées par l'adaptateur."""

    def __init__(
        self,
        config: PisteConfig,
        oauth: PisteOAuthClient,
        transport: PisteTransport,
    ):
        self.config = config
        self.oauth = oauth
        self.transport = transport

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self.oauth.access_token()
        try:
            return self.transport.request_api(self.config, path, token, payload)
        except PisteUnauthorizedError:
            self.oauth.invalidate(token)
            renewed_token = self.oauth.access_token()
            return self.transport.request_api(
                self.config, path, renewed_token, payload
            )

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/search", payload)

    def get_article(self, article_id: str) -> dict[str, Any]:
        return self._post("/consult/getArticle", {"id": article_id})

    def get_text(self, text_id: str, date: str, query: str) -> dict[str, Any]:
        return self._post(
            "/consult/legiPart",
            {"textId": text_id, "date": date, "searchedString": query},
        )
