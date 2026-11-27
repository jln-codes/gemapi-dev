"""Transport HTTPS anti-SSRF avec DNS validé et adresse de connexion épinglée."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
from typing import Callable
from urllib.parse import urljoin, urlsplit

from .registry import ExternalSource


EXTERNAL_HTTP_TIMEOUT_SECONDS = 8
EXTERNAL_MAX_RESPONSE_BYTES = 1_000_000
EXTERNAL_MAX_REDIRECTS = 3
EXTERNAL_USER_AGENT = "digues-app-experimental-external-sources/1.0"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class ExternalHttpError(RuntimeError):
    """Erreur HTTP externe nettoyée de tout détail sensible."""


class ExternalUrlRejected(ExternalHttpError):
    """URL refusée par la politique anti-SSRF."""


class ExternalHttpTimeout(ExternalHttpError):
    """Délai externe dépassé."""


class ExternalResponseTooLarge(ExternalHttpError):
    """Réponse externe supérieure à la limite configurée."""


@dataclass(frozen=True)
class ExternalHttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class ValidatedDestination:
    url: str
    hostname: str
    ip_address: str


def _host_is_allowed(hostname: str, source: ExternalSource) -> bool:
    for domain in source.domains:
        normalized = domain.lower().rstrip(".")
        if hostname == normalized:
            return True
        if source.allow_subdomains and hostname.endswith(f".{normalized}"):
            return True
    return False


def _is_public_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_global


def validate_external_url(
    url: str,
    source: ExternalSource,
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> ValidatedDestination:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ExternalUrlRejected("Destination externe refusée.") from exc
    if parsed.scheme != "https" or not hostname or port not in (None, 443):
        raise ExternalUrlRejected("Destination externe refusée.")
    if parsed.username is not None or parsed.password is not None:
        raise ExternalUrlRejected("Destination externe refusée.")
    if not _host_is_allowed(hostname, source):
        raise ExternalUrlRejected("Domaine externe non autorisé.")
    try:
        resolved = resolver(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ExternalHttpError("Résolution de la source externe impossible.") from exc
    addresses = sorted({item[4][0] for item in resolved if item[4]})
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ExternalUrlRejected("Adresse réseau externe refusée.")
    return ValidatedDestination(url=url, hostname=hostname, ip_address=addresses[0])


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, ip_address: str, *, timeout: float):
        super().__init__(
            hostname,
            port=443,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_ip_address = ip_address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_ip_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class SafeExternalHttpClient:
    def __init__(
        self,
        *,
        timeout: float = EXTERNAL_HTTP_TIMEOUT_SECONDS,
        max_response_bytes: int = EXTERNAL_MAX_RESPONSE_BYTES,
        resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    ):
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.resolver = resolver

    def _request_once(
        self, destination: ValidatedDestination
    ) -> ExternalHttpResponse:
        parsed = urlsplit(destination.url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        connection = _PinnedHttpsConnection(
            destination.hostname,
            destination.ip_address,
            timeout=self.timeout,
        )
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Host": destination.hostname,
                    "User-Agent": EXTERNAL_USER_AGENT,
                },
            )
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            content_length = headers.get("content-length")
            if content_length and int(content_length) > self.max_response_bytes:
                raise ExternalResponseTooLarge("Réponse externe trop volumineuse.")
            body = response.read(self.max_response_bytes + 1)
            if len(body) > self.max_response_bytes:
                raise ExternalResponseTooLarge("Réponse externe trop volumineuse.")
            return ExternalHttpResponse(
                url=destination.url,
                status_code=response.status,
                headers=headers,
                body=body,
            )
        except (TimeoutError, socket.timeout) as exc:
            raise ExternalHttpTimeout("La source externe n’a pas répondu à temps.") from exc
        except ExternalHttpError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
            raise ExternalHttpError("Consultation de la source externe impossible.") from exc
        finally:
            connection.close()

    def get(self, url: str, source: ExternalSource) -> ExternalHttpResponse:
        current_url = url
        for redirect_count in range(EXTERNAL_MAX_REDIRECTS + 1):
            destination = validate_external_url(
                current_url,
                source,
                resolver=self.resolver,
            )
            response = self._request_once(destination)
            if response.status_code not in REDIRECT_STATUSES:
                if response.status_code != 200:
                    raise ExternalHttpError(
                        f"Source externe indisponible (HTTP {response.status_code})."
                    )
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise ExternalHttpError("Format de source externe non pris en charge.")
                return response
            if redirect_count >= EXTERNAL_MAX_REDIRECTS:
                raise ExternalUrlRejected("Trop de redirections externes.")
            location = response.headers.get("location")
            if not location:
                raise ExternalHttpError("Redirection externe invalide.")
            current_url = urljoin(current_url, location)
        raise ExternalUrlRejected("Trop de redirections externes.")
