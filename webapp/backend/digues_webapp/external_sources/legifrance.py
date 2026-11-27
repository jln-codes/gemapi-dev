"""Adaptateur de recherche juridique via l'API officielle Légifrance/PISTE."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import re
import threading
from typing import Any

from .piste import (
    PisteConfig,
    PisteConfigurationError,
    PisteDocumentUnavailableError,
    PisteLegifranceApi,
    PisteOAuthClient,
    PisteTransport,
)
from .registry import get_source


LEGIFRANCE_MAX_DOCUMENTS = 2
LEGIFRANCE_SEARCH_PAGE_SIZE = 6
LEGIFRANCE_EXCERPT_MAX_CHARS = 3_000
LEGIFRANCE_PUBLIC_BASE_URL = "https://www.legifrance.gouv.fr"
CURRENT_LEGAL_STATUSES = {"VIGUEUR", "VIGUEUR_DIFF"}
NOR_PATTERN = re.compile(r"\b[A-Z]{4}\d{7}[A-Z]\b", re.IGNORECASE)
ARTICLE_NUMBER_PATTERN = re.compile(
    r"\barticle\s+(?:[A-Z]\s*)?\.?\s*(\d+(?:[-.]\d+)*(?:\s+(?:bis|ter|quater))?)\b",
    re.IGNORECASE,
)
KNOWN_CODE_NAMES = (
    "Code général des collectivités territoriales",
    "Code rural et de la pêche maritime",
    "Code de la construction et de l'habitation",
    "Code de l'environnement",
    "Code de l’urbanisme",
    "Code de l'urbanisme",
    "Code du travail",
    "Code civil",
    "Code pénal",
)
LEGAL_TITLE_PATTERN = re.compile(
    r"\b(?:arrêté|arrete|décret|decret|ordonnance|loi)\b[^.!?\n]*",
    re.IGNORECASE,
)


class LegifranceResponseError(RuntimeError):
    """Réponse métier Légifrance inexploitable."""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _iso_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
    if match:
        return match.group(1)
    if value.isdigit():
        try:
            return datetime.fromtimestamp(
                int(value) / 1000, timezone.utc
            ).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _bounded_excerpt(text: str) -> str:
    normalized = _clean_text(text)
    if len(normalized) <= LEGIFRANCE_EXCERPT_MAX_CHARS:
        return normalized
    return normalized[:LEGIFRANCE_EXCERPT_MAX_CHARS].rstrip()


def _article_url(fund: str, article_id: str) -> str:
    if not re.fullmatch(r"LEGIARTI\d+", article_id):
        raise LegifranceResponseError("Identifiant d’article Légifrance invalide.")
    area = "codes" if fund.startswith("CODE") else "loda"
    return f"{LEGIFRANCE_PUBLIC_BASE_URL}/{area}/article_lc/{article_id}"


def _text_url(fund: str, text_id: str, eli: str | None = None) -> str:
    if isinstance(eli, str) and re.fullmatch(r"/eli/[A-Za-z0-9._~%/-]+", eli):
        return f"{LEGIFRANCE_PUBLIC_BASE_URL}{eli}"
    if not re.fullmatch(r"LEGITEXT\d+", text_id):
        raise LegifranceResponseError("Identifiant de texte Légifrance invalide.")
    if fund.startswith("CODE"):
        return f"{LEGIFRANCE_PUBLIC_BASE_URL}/codes/texte_lc/{text_id}"
    return f"{LEGIFRANCE_PUBLIC_BASE_URL}/loda/id/{text_id}"


def _known_code_name(query: str) -> str | None:
    normalized = query.casefold().replace("’", "'")
    for name in KNOWN_CODE_NAMES:
        if name.casefold().replace("’", "'") in normalized:
            return name.replace("’", "'")
    return None


def _search_field(query: str, fund: str) -> tuple[str, str, str]:
    if fund == "CODE_ETAT":
        article_match = ARTICLE_NUMBER_PATTERN.search(query)
        if article_match:
            return "NUM_ARTICLE", article_match.group(1), "EXACTE"
        return "ARTICLE", query, "UN_DES_MOTS"

    nor_match = NOR_PATTERN.search(query)
    if nor_match:
        return "NOR", nor_match.group(0).upper(), "EXACTE"
    title_match = LEGAL_TITLE_PATTERN.search(query)
    if title_match:
        return (
            "TITLE",
            title_match.group(0).strip(),
            "TOUS_LES_MOTS_DANS_UN_CHAMP",
        )
    return "ALL", query, "UN_DES_MOTS"


def _search_payload(query: str, fund: str) -> dict[str, Any]:
    field, value, search_type = _search_field(query, fund)
    filters: list[dict[str, Any]] = []
    if fund == "LODA_ETAT":
        filters.append(
            {
                "facette": "NATURE",
                "valeurs": ["LOI", "ORDONNANCE", "DECRET", "ARRETE"],
            }
        )
        # Un NOR désigne un texte précis : ne pas masquer un texte historique.
        if field != "NOR":
            filters.append({"facette": "TEXT_LEGAL_STATUS", "valeur": "VIGUEUR"})
    elif field == "NUM_ARTICLE":
        code_name = _known_code_name(query)
        if code_name:
            filters.append({"facette": "TEXT_NOM_CODE", "valeurs": [code_name]})
    return {
        "fond": fund,
        "recherche": {
            "champs": [{
                "typeChamp": field,
                "criteres": [{
                    "typeRecherche": search_type,
                    "valeur": value,
                    "operateur": "ET",
                }],
                "operateur": "ET",
            }],
            "filtres": filters,
            "pageNumber": 1,
            "pageSize": LEGIFRANCE_SEARCH_PAGE_SIZE,
            "operateur": "ET",
            "sort": "PERTINENCE",
            "typePagination": "DEFAUT",
        },
    }


@dataclass(frozen=True)
class _Candidate:
    fund: str
    identifier: str
    kind: str
    title: str
    article_number: str | None
    legal_status: str | None
    nature: str | None
    nor: str | None
    text_number: str | None
    document_date: str | None
    text_id: str | None
    consultation_date: str | None


def _first_string(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned
    return None


def _legi_text_id(title_items: list[Any]) -> tuple[str | None, str | None]:
    for item in title_items:
        if not isinstance(item, dict):
            continue
        versioned_id = _first_string(item, "id")
        if versioned_id:
            match = re.fullmatch(
                r"(LEGITEXT\d+)(?:_(\d{2})-(\d{2})-(\d{4}))?",
                versioned_id,
            )
            if match:
                consultation_date = None
                if match.group(2):
                    consultation_date = (
                        f"{match.group(4)}-{match.group(3)}-{match.group(2)}"
                    )
                return match.group(1), consultation_date
        cid = _first_string(item, "cid")
        if cid and re.fullmatch(r"LEGITEXT\d+", cid):
            return cid, None
    return None, None


def _candidates(payload: dict[str, Any], fund: str) -> list[_Candidate]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise LegifranceResponseError("Réponse de recherche Légifrance invalide.")
    candidates: list[_Candidate] = []
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            continue
        titles = raw_result.get("titles")
        title_items = titles if isinstance(titles, list) else []
        title_item = next(
            (
                item
                for item in title_items
                if isinstance(item, dict) and _first_string(item, "title")
            ),
            {},
        )
        title = _first_string(title_item, "title") or _first_string(
            raw_result, "reference", "text"
        ) or "Texte juridique"
        text_id, consultation_date = _legi_text_id(title_items)
        nature = _first_string(title_item, "nature") or _first_string(
            raw_result, "nature"
        )
        status = _first_string(title_item, "legalStatus") or _first_string(
            raw_result, "etat"
        )
        document_date = _iso_date(
            raw_result.get("dateSignature")
            or raw_result.get("datePublication")
            or raw_result.get("date")
        )
        found_article = False
        sections = raw_result.get("sections")
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict):
                continue
            section_status = _first_string(section, "legalStatus") or status
            extracts = section.get("extracts")
            for extract in extracts if isinstance(extracts, list) else []:
                if not isinstance(extract, dict):
                    continue
                article_id = _first_string(extract, "id")
                if not article_id or not re.fullmatch(r"LEGIARTI\d+", article_id):
                    continue
                found_article = True
                candidates.append(_Candidate(
                    fund=fund,
                    identifier=article_id,
                    kind="article",
                    title=title,
                    article_number=_first_string(extract, "num"),
                    legal_status=_first_string(extract, "legalStatus") or section_status,
                    nature=nature,
                    nor=_first_string(raw_result, "nor"),
                    text_number=_first_string(raw_result, "num"),
                    document_date=document_date,
                    text_id=text_id,
                    consultation_date=consultation_date,
                ))
        if (
            not found_article
            and fund == "LODA_ETAT"
            and text_id
            and re.fullmatch(r"LEGITEXT\d+", text_id)
        ):
            candidates.append(_Candidate(
                fund=fund,
                identifier=text_id,
                kind="text",
                title=title,
                article_number=None,
                legal_status=status,
                nature=nature,
                nor=_first_string(raw_result, "nor"),
                text_number=_first_string(raw_result, "num"),
                document_date=document_date,
                text_id=text_id,
                consultation_date=consultation_date,
            ))
    candidates.sort(
        key=lambda candidate: candidate.legal_status not in CURRENT_LEGAL_STATUSES
    )
    return candidates


def _metadata(candidate: _Candidate, **extra: Any) -> dict[str, Any]:
    values = {
        "legifrance_id": candidate.identifier,
        "text_id": candidate.text_id,
        "nature": candidate.nature,
        "nor": candidate.nor,
        "text_number": candidate.text_number,
        "article_number": candidate.article_number,
        "legal_status": candidate.legal_status,
        **extra,
    }
    return {key: value for key, value in values.items() if value not in (None, "")}


def _consult_articles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(container: dict[str, Any]) -> None:
        articles = container.get("articles")
        for article in articles if isinstance(articles, list) else []:
            if isinstance(article, dict):
                found.append(article)
        sections = container.get("sections")
        for section in sections if isinstance(sections, list) else []:
            if isinstance(section, dict):
                visit(section)

    visit(payload)
    return found


def _chapter_summary(payload: dict[str, Any], query: str) -> str:
    requested = {
        number.upper()
        for number in re.findall(r"\bchapitre\s+([IVXLCDM]+)\b", query, re.IGNORECASE)
    }
    if not requested:
        return ""

    def article_numbers(container: dict[str, Any]) -> list[str]:
        articles = container.get("articles")
        numbers = [
            number
            for article in (articles if isinstance(articles, list) else [])
            if isinstance(article, dict)
            if (number := _first_string(article, "num"))
        ]
        sections = container.get("sections")
        for section in sections if isinstance(sections, list) else []:
            if isinstance(section, dict):
                numbers.extend(article_numbers(section))
        return numbers

    summaries: list[str] = []

    def visit(container: dict[str, Any]) -> None:
        sections = container.get("sections")
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict):
                continue
            title = _first_string(section, "title") or ""
            match = re.search(r"\bchapitre\s+([IVXLCDM]+)\b", title, re.IGNORECASE)
            if match and match.group(1).upper() in requested:
                numbers = article_numbers(section)
                if numbers:
                    summaries.append(f"{title} — Articles {', '.join(numbers)}.")
            visit(section)

    visit(payload)
    return " ".join(summaries)


def _text_excerpt(payload: dict[str, Any], query: str) -> str:
    terms = {
        term.lower()
        for term in re.findall(r"[\wÀ-ÿ-]{3,}", query)
    }
    ranked: list[tuple[int, int, str]] = []
    for index, article in enumerate(_consult_articles(payload)):
        content = _first_string(article, "content")
        cleaned = _clean_text(content or "")
        if not cleaned:
            continue
        lowered = cleaned.lower()
        score = sum(1 for term in terms if term in lowered)
        number = _first_string(article, "num")
        rendered = f"Article {number} — {cleaned}" if number else cleaned
        ranked.append((score, -index, rendered))
    ranked.sort(reverse=True)
    prefix = _first_string(payload, "resume", "notice", "nota") or ""
    chapter_summary = _chapter_summary(payload, query)
    parts = [part for part in (chapter_summary, prefix) if part]
    for _score, _index, rendered in ranked[:3]:
        parts.append(rendered)
        if len(" ".join(parts)) >= LEGIFRANCE_EXCERPT_MAX_CHARS:
            break
    return _bounded_excerpt(" ".join(parts))


class LegifranceAdapter:
    def __init__(self, api: PisteLegifranceApi):
        self.api = api
        self.source = get_source("legifrance")

    @classmethod
    def from_environment(cls) -> "LegifranceAdapter":
        config = PisteConfig.from_environment()
        transport = PisteTransport()
        oauth = PisteOAuthClient(config, transport)
        return cls(PisteLegifranceApi(config, oauth, transport))

    def search(
        self, query: str, *, limit: int = LEGIFRANCE_MAX_DOCUMENTS
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), LEGIFRANCE_MAX_DOCUMENTS))
        candidates: list[_Candidate] = []
        seen_ids: set[str] = set()
        loda_first = bool(
            NOR_PATTERN.search(query) or LEGAL_TITLE_PATTERN.search(query)
        )
        funds = (
            ("LODA_ETAT", "CODE_ETAT")
            if loda_first
            else ("CODE_ETAT", "LODA_ETAT")
        )
        for fund in funds:
            response = self.api.search(_search_payload(query, fund))
            for candidate in _candidates(response, fund):
                if candidate.identifier not in seen_ids:
                    seen_ids.add(candidate.identifier)
                    candidates.append(candidate)

        results: list[dict[str, Any]] = []
        fetch_attempts = 0
        for candidate in candidates:
            if len(results) >= bounded_limit or fetch_attempts >= bounded_limit:
                break
            fetch_attempts += 1
            try:
                item = self._fetch_candidate(candidate, query)
            except (LegifranceResponseError, PisteDocumentUnavailableError):
                continue
            if item:
                results.append(item)
        return results

    def _fetch_candidate(
        self, candidate: _Candidate, query: str
    ) -> dict[str, Any] | None:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        if candidate.kind == "article":
            payload = self.api.get_article(candidate.identifier)
            article = payload.get("article")
            if not isinstance(article, dict):
                raise LegifranceResponseError("Article Légifrance invalide.")
            content = _first_string(article, "texte", "texteHtml")
            excerpt = _bounded_excerpt(content or "")
            if not excerpt:
                return None
            article_number = _first_string(article, "num") or candidate.article_number
            title = candidate.title
            if article_number:
                title = f"{title} — Article {article_number}"
            status = _first_string(article, "etat") or candidate.legal_status
            return {
                "title": title,
                "organization": self.source.organization,
                "url": _article_url(candidate.fund, candidate.identifier),
                "source_id": self.source.source_id,
                "category": self.source.category,
                "document_date": candidate.document_date,
                "retrieved_at": retrieved_at,
                "excerpt": excerpt,
                "metadata": _metadata(
                    candidate,
                    article_number=article_number,
                    legal_status=status,
                    effective_date=_iso_date(article.get("dateDebut")),
                    end_date=_iso_date(article.get("dateFin")),
                ),
            }

        payload = self.api.get_text(
            candidate.identifier,
            candidate.consultation_date or date.today().isoformat(),
            query,
        )
        excerpt = _text_excerpt(payload, query)
        if not excerpt:
            return None
        status = _first_string(payload, "etat", "jurisState") or candidate.legal_status
        return {
            "title": _first_string(payload, "title") or candidate.title,
            "organization": self.source.organization,
            "url": _text_url(
                candidate.fund,
                candidate.identifier,
                _first_string(payload, "eli"),
            ),
            "source_id": self.source.source_id,
            "category": self.source.category,
            "document_date": candidate.document_date or _iso_date(payload.get("dateTexte")),
            "retrieved_at": retrieved_at,
            "excerpt": excerpt,
            "metadata": _metadata(
                candidate,
                nature=_first_string(payload, "nature") or candidate.nature,
                nor=_first_string(payload, "nor") or candidate.nor,
                text_number=_first_string(payload, "textNumber") or candidate.text_number,
                legal_status=status,
                effective_date=_iso_date(payload.get("dateDebutVersion")),
                end_date=_iso_date(payload.get("dateFinVersion")),
            ),
        }


_DEFAULT_ADAPTER: LegifranceAdapter | None = None
_DEFAULT_ADAPTER_LOCK = threading.Lock()


def default_legifrance_adapter() -> LegifranceAdapter:
    global _DEFAULT_ADAPTER
    if _DEFAULT_ADAPTER is not None:
        return _DEFAULT_ADAPTER
    with _DEFAULT_ADAPTER_LOCK:
        if _DEFAULT_ADAPTER is None:
            _DEFAULT_ADAPTER = LegifranceAdapter.from_environment()
        return _DEFAULT_ADAPTER


def reset_default_legifrance_adapter() -> None:
    """Réinitialise le singleton, principalement pour les tests de configuration."""

    global _DEFAULT_ADAPTER
    with _DEFAULT_ADAPTER_LOCK:
        _DEFAULT_ADAPTER = None
