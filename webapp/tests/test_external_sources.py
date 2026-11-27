import json
import socket
import unittest
from unittest.mock import Mock, patch

from digues_webapp.external_sources.registry import (
    EXTERNAL_SOURCE_REGISTRY,
    LEGAL_PRIMARY,
    LOCAL,
    PROFESSIONAL,
    STATE,
    TECHNICAL_PUBLIC,
    get_source,
)
from digues_webapp.external_sources.search import (
    ExternalSourceSearchError,
    search_external_sources,
)
from digues_webapp.external_sources.security import (
    ExternalHttpResponse,
    ExternalHttpTimeout,
    ExternalResponseTooLarge,
    ExternalUrlRejected,
    SafeExternalHttpClient,
    ValidatedDestination,
    validate_external_url,
)


PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def public_resolver(*_args, **_kwargs):
    return PUBLIC_DNS


def html_response(url, body, *, status=200, headers=None):
    return ExternalHttpResponse(
        url=url,
        status_code=status,
        headers=headers or {"content-type": "text/html; charset=utf-8"},
        body=body.encode("utf-8"),
    )


class ExternalSourceRegistryTest(unittest.TestCase):
    def test_registry_contains_v1_sources_and_local_category_is_available(self):
        self.assertEqual(
            set(EXTERNAL_SOURCE_REGISTRY),
            {
                "legifrance", "ecologie", "services_etat_territoriaux",
                "georisques", "vigicrues", "ofb", "cerema", "inrae",
                "france_digues",
            },
        )
        self.assertEqual(get_source("legifrance").category, LEGAL_PRIMARY)
        self.assertEqual(get_source("ecologie").category, STATE)
        self.assertEqual(get_source("cerema").category, TECHNICAL_PUBLIC)
        self.assertEqual(get_source("france_digues").category, PROFESSIONAL)
        self.assertEqual(LOCAL, "LOCAL")
        self.assertEqual(
            {source.source_id for source in EXTERNAL_SOURCE_REGISTRY.values() if source.adapter},
            {"legifrance", "cerema", "france_digues"},
        )

    def test_decentralized_state_domains_are_explicit_not_broad_suffixes(self):
        source = get_source("services_etat_territoriaux")
        self.assertFalse(source.allow_subdomains)
        self.assertNotIn("gouv.fr", source.domains)
        self.assertNotIn("developpement-durable.gouv.fr", source.domains)
        self.assertGreaterEqual(len(source.domains), 13)


class ExternalUrlSecurityTest(unittest.TestCase):
    def test_allowed_domain_and_subdomain_are_accepted_with_public_dns(self):
        source = get_source("cerema")
        destination = validate_external_url(
            "https://www.cerema.fr/fr/article",
            source,
            resolver=public_resolver,
        )
        self.assertEqual(destination.hostname, "www.cerema.fr")
        self.assertEqual(destination.ip_address, "93.184.216.34")

    def test_forbidden_domain_scheme_credentials_and_port_are_rejected(self):
        source = get_source("cerema")
        cases = (
            "https://evil.example/article",
            "http://www.cerema.fr/article",
            "ftp://www.cerema.fr/article",
            "https://user:password@www.cerema.fr/article",
            "https://www.cerema.fr:8443/article",
            "https://localhost/article",
            "https://127.0.0.1/article",
        )
        for url in cases:
            with self.subTest(url=url), self.assertRaises(ExternalUrlRejected):
                validate_external_url(url, source, resolver=public_resolver)

    def test_private_loopback_link_local_and_metadata_addresses_are_rejected(self):
        source = get_source("cerema")
        for address in ("10.0.0.1", "127.0.0.1", "169.254.169.254", "::1", "fc00::1"):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            resolver = Mock(return_value=[
                (family, socket.SOCK_STREAM, 6, "", (address, 443)),
            ])
            with self.subTest(address=address), self.assertRaises(ExternalUrlRejected):
                validate_external_url(
                    "https://www.cerema.fr/article",
                    source,
                    resolver=resolver,
                )

    def test_redirect_to_domain_outside_source_allowlist_is_rejected(self):
        source = get_source("cerema")
        client = SafeExternalHttpClient(resolver=public_resolver)
        client._request_once = Mock(return_value=html_response(
            "https://www.cerema.fr/start",
            "",
            status=302,
            headers={"location": "https://evil.example/redirect"},
        ))
        with self.assertRaises(ExternalUrlRejected):
            client.get("https://www.cerema.fr/start", source)
        self.assertEqual(client._request_once.call_count, 1)

    def test_timeout_is_normalized(self):
        client = SafeExternalHttpClient(resolver=public_resolver)
        client._request_once = Mock(side_effect=ExternalHttpTimeout("timeout"))
        with self.assertRaises(ExternalHttpTimeout):
            client.get("https://www.cerema.fr/fr/recherche", get_source("cerema"))

    def test_oversized_response_is_rejected_before_return(self):
        response = Mock(status=200)
        response.getheaders.return_value = [
            ("Content-Type", "text/html"),
            ("Content-Length", "101"),
        ]
        connection = Mock()
        connection.getresponse.return_value = response
        client = SafeExternalHttpClient(max_response_bytes=100, resolver=public_resolver)
        destination = ValidatedDestination(
            "https://www.cerema.fr/article", "www.cerema.fr", "93.184.216.34"
        )
        with (
            patch(
                "digues_webapp.external_sources.security._PinnedHttpsConnection",
                return_value=connection,
            ),
            self.assertRaises(ExternalResponseTooLarge),
        ):
            client._request_once(destination)


class FakeExternalClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, source):
        self.calls.append((url, source.source_id))
        for marker, response in self.responses:
            if marker in url:
                if isinstance(response, Exception):
                    raise response
                return response(url) if callable(response) else response
        raise AssertionError(f"URL inattendue: {url}")


class ExternalSourceSearchTest(unittest.TestCase):
    def test_search_preserves_provenance_and_unknown_date(self):
        search_html = """
        <article class="search-result">
          <a class="title-link" href="/fr/article-digues">Guide des digues</a>
        </article>
        """
        page_html = """
        <html><body><main><h1>Guide des digues</h1>
        <p>La surveillance des digues repose sur des inspections régulières.</p>
        </main></body></html>
        """
        client = FakeExternalClient([
            ("/fr/recherche?", lambda url: html_response(url, search_html)),
            ("/fr/article-digues", lambda url: html_response(url, page_html)),
        ])
        result = search_external_sources("surveillance digues", client=client, limit=1)
        self.assertEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertEqual(item["title"], "Guide des digues")
        self.assertEqual(item["organization"], "Cerema")
        self.assertEqual(item["source_id"], "cerema")
        self.assertEqual(item["category"], TECHNICAL_PUBLIC)
        self.assertEqual(item["url"], "https://www.cerema.fr/fr/article-digues")
        self.assertIsNone(item["document_date"])
        self.assertIn("surveillance des digues", item["excerpt"])
        self.assertIn("+00:00", item["retrieved_at"])

    def test_search_result_date_is_normalized_and_document_limit_is_enforced(self):
        search_html = """
        <article class="search-result">
          <a class="title-link" href="/fr/a">Premier</a>
          <span class="createdtime">Publié le 06/01/2020</span>
        </article>
        <article class="search-result"><a class="title-link" href="/fr/b">Second</a></article>
        """
        client = FakeExternalClient([
            ("/fr/recherche?", lambda url: html_response(url, search_html)),
            ("/fr/a", lambda url: html_response(url, "<main>digues</main>")),
        ])
        result = search_external_sources("digues", client=client, limit=1)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["document_date"], "2020-01-06")
        self.assertEqual(len(client.calls), 2)

    def test_france_digues_search_adapter_returns_professional_source(self):
        search_html = """
        <article class="resultat">
          <h2><a href="/actualite/entretien">Entretien des ouvrages</a></h2>
          <p class="extrait">Retours d'expérience des gestionnaires.</p>
        </article>
        """
        client = FakeExternalClient([
            ("/fr/recherche?", lambda url: html_response(url, "")),
            ("/search/?", lambda url: html_response(url, search_html)),
            ("/actualite/entretien", lambda url: html_response(
                url, "<main><h1>Entretien des ouvrages</h1><p>Contenu métier.</p></main>"
            )),
        ])
        result = search_external_sources("entretien ouvrages", client=client, limit=1)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["source_id"], "france_digues")
        self.assertEqual(result["results"][0]["category"], PROFESSIONAL)
        self.assertEqual(
            result["results"][0]["url"],
            "https://www.france-digues.fr/actualite/entretien",
        )

    def test_all_source_timeouts_return_clean_search_error(self):
        client = FakeExternalClient([
            ("/fr/recherche?", ExternalHttpTimeout("secret transport detail")),
            ("/search/?", ExternalHttpTimeout("secret transport detail")),
        ])
        with self.assertRaisesRegex(ExternalSourceSearchError, "pas répondu à temps") as raised:
            search_external_sources("digues", client=client)
        self.assertNotIn("secret", str(raised.exception))

    def test_document_fetch_count_is_bounded_even_when_pages_are_empty(self):
        search_html = """\
        <article class="search-result"><a class="title-link" href="/fr/a">A</a></article>
        <article class="search-result"><a class="title-link" href="/fr/b">B</a></article>
        <article class="search-result"><a class="title-link" href="/fr/c">C</a></article>
        """
        client = FakeExternalClient([
            ("/fr/recherche?", lambda url: html_response(url, search_html)),
            ("/fr/a", lambda url: html_response(url, "<main></main>")),
            ("/fr/b", lambda url: html_response(url, "<main></main>")),
            ("/fr/c", lambda url: html_response(url, "<main></main>")),
        ])
        result = search_external_sources("digues", client=client, limit=2)
        self.assertEqual(result, {"results": []})
        self.assertEqual(len(client.calls), 3)
        self.assertFalse(any("/fr/c" in url for url, _source in client.calls))

    def test_query_is_bounded(self):
        with self.assertRaises(ExternalSourceSearchError):
            search_external_sources(" ")
        with self.assertRaises(ExternalSourceSearchError):
            search_external_sources("x" * 301)


if __name__ == "__main__":
    unittest.main()
