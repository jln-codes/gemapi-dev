"""Recherche bornée sur les moteurs publics explicitement connectés."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urljoin

from .legifrance import (
    LEGIFRANCE_MAX_DOCUMENTS,
    LegifranceAdapter,
    LegifranceResponseError,
    default_legifrance_adapter,
)
from .piste import PisteConfigurationError, PisteError, PisteTimeoutError
from .registry import ExternalSource, functional_sources
from .security import (
    ExternalHttpError,
    ExternalHttpResponse,
    ExternalHttpTimeout,
    SafeExternalHttpClient,
)


EXTERNAL_QUERY_MAX_CHARS = 300
EXTERNAL_MAX_DOCUMENTS = 4
EXTERNAL_EXCERPT_MAX_CHARS = 3_000


class ExternalSourceSearchError(RuntimeError):
    """Erreur présentable de la recherche externe contrôlée."""


@dataclass(frozen=True)
class SearchCandidate:
    title: str
    url: str
    excerpt: str = ""
    document_date: str | None = None


def _classes(attributes: list[tuple[str, str | None]]) -> set[str]:
    value = dict(attributes).get("class") or ""
    return set(value.split())


def _normalize_text(parts: list[str] | str) -> str:
    value = " ".join(parts) if isinstance(parts, list) else parts
    return re.sub(r"\s+", " ", value).strip()


def _date_from_text(value: str | None) -> str | None:
    if not value:
        return None
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if iso_match:
        return iso_match.group(1)
    french_match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", value)
    if french_match:
        day, month, year = french_match.groups()
        return f"{year}-{month}-{day}"
    return None


class _SearchResultParser(HTMLParser):
    def __init__(self, source: ExternalSource, base_url: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.base_url = base_url
        self.results: list[SearchCandidate] = []
        self._article_depth = 0
        self._in_heading = 0
        self._in_excerpt = 0
        self._in_date = 0
        self._capture_title = 0
        self._href = ""
        self._title: list[str] = []
        self._excerpt: list[str] = []
        self._date: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = _classes(attrs)
        if tag == "article" and (
            "search-result" in classes or "resultat" in classes
        ):
            self._article_depth = 1
            self._href = ""
            self._title = []
            self._excerpt = []
            self._date = []
            return
        if not self._article_depth:
            return
        self._article_depth += 1
        if tag == "h2":
            self._in_heading = self._article_depth
        if tag == "p" and "extrait" in classes:
            self._in_excerpt = self._article_depth
        if "createdtime" in classes or tag == "time":
            self._in_date = self._article_depth
            datetime_value = dict(attrs).get("datetime")
            if datetime_value:
                self._date.append(datetime_value)
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if "title-link" in classes or self._in_heading:
                self._capture_title = self._article_depth
                self._href = href
                self._title = []

    def handle_endtag(self, tag: str) -> None:
        if not self._article_depth:
            return
        if self._capture_title == self._article_depth and tag == "a":
            self._capture_title = 0
        if self._in_heading == self._article_depth and tag == "h2":
            self._in_heading = 0
        if self._in_excerpt == self._article_depth and tag == "p":
            self._in_excerpt = 0
        if self._in_date == self._article_depth and tag in {"span", "time"}:
            self._in_date = 0
        self._article_depth -= 1
        if self._article_depth == 0:
            title = _normalize_text(self._title)
            if title and self._href:
                self.results.append(SearchCandidate(
                    title=title,
                    url=urljoin(self.base_url, self._href),
                    excerpt=_normalize_text(self._excerpt),
                    document_date=_date_from_text(_normalize_text(self._date)),
                ))

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title.append(data)
        if self._in_excerpt:
            self._excerpt.append(data)
        if self._in_date:
            self._date.append(data)


class _PageTextParser(HTMLParser):
    SKIPPED_TAGS = {"script", "style", "nav", "header", "footer", "form", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.document_date: str | None = None
        self._skip_depth = 0
        self._heading_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            self._skip_depth += 1
            return
        if tag in self.SKIPPED_TAGS:
            self._skip_depth = 1
            return
        if tag == "h1":
            self._heading_depth = 1
        elif self._heading_depth:
            self._heading_depth += 1
        if tag == "time" and not self.document_date:
            self.document_date = _date_from_text(dict(attrs).get("datetime"))
        if tag == "meta" and not self.document_date:
            attributes = dict(attrs)
            if attributes.get("property") in {"article:published_time", "date"}:
                self.document_date = _date_from_text(attributes.get("content"))

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if self._heading_depth:
            self._heading_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.text_parts.append(data)
        if self._heading_depth:
            self.heading_parts.append(data)


def _decode_html(response: ExternalHttpResponse) -> str:
    content_type = response.headers.get("content-type", "")
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    encoding = match.group(1) if match else "utf-8"
    try:
        return response.body.decode(encoding, errors="replace")
    except LookupError:
        return response.body.decode("utf-8", errors="replace")


def _search_url(source: ExternalSource, query: str) -> str:
    if source.adapter == "cerema_html":
        return "https://www.cerema.fr/fr/recherche?" + urlencode({"fulltext": query})
    if source.adapter == "france_digues_html":
        return "https://www.france-digues.fr/search/?" + urlencode({
            "q": query,
            "model": "all",
        })
    raise ExternalSourceSearchError("Source externe non connectée.")


def _excerpt(text: str, query: str) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= EXTERNAL_EXCERPT_MAX_CHARS:
        return normalized
    terms = [term.lower() for term in re.findall(r"[\wÀ-ÿ-]{3,}", query)]
    lowered = normalized.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - 300)
    end = min(len(normalized), start + EXTERNAL_EXCERPT_MAX_CHARS)
    return normalized[start:end].strip()


def _candidates(
    source: ExternalSource,
    query: str,
    client: SafeExternalHttpClient,
) -> list[SearchCandidate]:
    search_url = _search_url(source, query)
    response = client.get(search_url, source)
    parser = _SearchResultParser(source, response.url)
    parser.feed(_decode_html(response))
    return parser.results


def search_external_sources(
    query: str,
    *,
    client: SafeExternalHttpClient | None = None,
    legifrance_adapter: LegifranceAdapter | None = None,
    limit: int = EXTERNAL_MAX_DOCUMENTS,
) -> dict[str, list[dict[str, object]]]:
    normalized_query = query.strip() if isinstance(query, str) else ""
    if not normalized_query or len(normalized_query) > EXTERNAL_QUERY_MAX_CHARS:
        raise ExternalSourceSearchError("La recherche externe est vide ou trop longue.")
    bounded_limit = max(1, min(int(limit), EXTERNAL_MAX_DOCUMENTS))
    http_client = client or SafeExternalHttpClient()
    results: list[dict[str, object]] = []
    consulted_urls: set[str] = set()
    fetched_document_count = 0
    html_fetch_limit = bounded_limit
    successful_searches = 0
    failures: list[Exception] = []

    for source in functional_sources():
        if source.adapter == "legifrance_piste":
            # Un transport HTML injecté est un double de test ; l'adaptateur API
            # doit lui aussi être injecté pour garantir zéro appel réseau réel.
            if client is not None and legifrance_adapter is None:
                continue
            try:
                adapter = legifrance_adapter or default_legifrance_adapter()
            except PisteConfigurationError:
                # Une source optionnelle non configurée ne dégrade pas les autres.
                continue
            try:
                remaining = bounded_limit - len(results)
                legifrance_results = adapter.search(
                    normalized_query,
                    limit=min(remaining, LEGIFRANCE_MAX_DOCUMENTS),
                )
                successful_searches += 1
            except (PisteError, LegifranceResponseError) as exc:
                failures.append(exc)
                continue
            for item in legifrance_results:
                url = item.get("url")
                if not isinstance(url, str) or not url or url in consulted_urls:
                    continue
                consulted_urls.add(url)
                results.append(item)
                if len(results) >= bounded_limit:
                    break
            html_fetch_limit = bounded_limit - len(results)
            if len(results) >= bounded_limit:
                break
            continue
        try:
            candidates = _candidates(source, normalized_query, http_client)
            successful_searches += 1
        except ExternalHttpError as exc:
            failures.append(exc)
            continue
        for candidate in candidates:
            if (
                len(results) >= bounded_limit
                or fetched_document_count >= html_fetch_limit
            ):
                break
            if candidate.url in consulted_urls:
                continue
            fetched_document_count += 1
            try:
                page_response = http_client.get(candidate.url, source)
            except ExternalHttpError:
                continue
            parser = _PageTextParser()
            parser.feed(_decode_html(page_response))
            content = candidate.excerpt or _normalize_text(parser.text_parts)
            excerpt = _excerpt(content, normalized_query)
            if not excerpt:
                continue
            consulted_urls.add(page_response.url)
            results.append({
                "title": candidate.title or _normalize_text(parser.heading_parts),
                "organization": source.organization,
                "url": page_response.url,
                "source_id": source.source_id,
                "category": source.category,
                "document_date": candidate.document_date or parser.document_date,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "excerpt": excerpt,
            })
        if (
            len(results) >= bounded_limit
            or fetched_document_count >= html_fetch_limit
        ):
            break

    if not successful_searches and failures:
        if any(
            isinstance(error, (ExternalHttpTimeout, PisteTimeoutError))
            for error in failures
        ):
            raise ExternalSourceSearchError(
                "Les sources externes n’ont pas répondu à temps."
            )
        raise ExternalSourceSearchError(
            "Les sources externes sont temporairement indisponibles."
        )
    return {"results": results}
