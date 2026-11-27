import json
import os
import threading
import unittest
from unittest.mock import Mock, patch

import requests

from digues_webapp.external_sources.legifrance import (
    LEGIFRANCE_PUBLIC_BASE_URL,
    LegifranceAdapter,
    LegifranceResponseError,
    _search_payload,
)
from digues_webapp.external_sources.piste import (
    PISTE_ENDPOINTS,
    PisteAuthenticationError,
    PisteConfig,
    PisteConfigurationError,
    PisteDocumentUnavailableError,
    PisteLegifranceApi,
    PisteOAuthClient,
    PisteRateLimitError,
    PisteTimeoutError,
    PisteTransport,
    PisteUnavailableError,
    PisteUnauthorizedError,
)
from digues_webapp.external_sources.registry import LEGAL_PRIMARY
from digues_webapp.external_sources.search import search_external_sources


class FakeTokenTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request_token(self, _config):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeApi:
    def __init__(self, searches, *, article=None, text=None):
        self.searches = list(searches)
        self.article = article
        self.text = text
        self.search_payloads = []
        self.article_ids = []
        self.text_requests = []

    def search(self, payload):
        self.search_payloads.append(payload)
        response = self.searches.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get_article(self, article_id):
        self.article_ids.append(article_id)
        if isinstance(self.article, Exception):
            raise self.article
        return self.article

    def get_text(self, text_id, date, query):
        self.text_requests.append((text_id, date, query))
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


def search_result(
    *,
    article_id="LEGIARTI000012345678",
    text_id="LEGITEXT000012345678",
    status="VIGUEUR",
    document_date="2024-02-03T00:00:00.000+0000",
):
    return {
        "results": [{
            "nor": "TREL1234567A",
            "num": "2024-10",
            "nature": "CODE",
            "dateSignature": document_date,
            "titles": [{
                "title": "Code de l'environnement",
                "cid": text_id,
                "legalStatus": status,
                "nature": "CODE",
            }],
            "sections": [{
                "legalStatus": status,
                "extracts": [{
                    "id": article_id,
                    "num": "R. 214-1",
                    "legalStatus": status,
                    "values": ["systèmes d'endiguement"],
                }],
            }],
        }]
    }


class PisteConfigurationTest(unittest.TestCase):
    def test_sandbox_configuration_uses_fixed_endpoints(self):
        with patch.dict(os.environ, {
            "LEGIFRANCE_ENV": "sandbox",
            "LEGIFRANCE_CLIENT_ID": "client-test",
            "LEGIFRANCE_CLIENT_SECRET": "secret-test",
        }, clear=True):
            config = PisteConfig.from_environment()
        self.assertEqual(config.environment, "sandbox")
        self.assertEqual(config.endpoints, PISTE_ENDPOINTS["sandbox"])

    def test_production_configuration_uses_fixed_endpoints(self):
        with patch.dict(os.environ, {
            "LEGIFRANCE_ENV": "production",
            "LEGIFRANCE_CLIENT_ID": "client-prod",
            "LEGIFRANCE_CLIENT_SECRET": "secret-prod",
        }, clear=True):
            config = PisteConfig.from_environment()
        self.assertEqual(config.endpoints, PISTE_ENDPOINTS["production"])

    def test_missing_or_arbitrary_configuration_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(
            PisteConfigurationError
        ):
            PisteConfig.from_environment()
        with patch.dict(os.environ, {
            "LEGIFRANCE_ENV": "custom",
            "LEGIFRANCE_CLIENT_ID": "client",
            "LEGIFRANCE_CLIENT_SECRET": "secret",
        }, clear=True), self.assertRaises(PisteConfigurationError):
            PisteConfig.from_environment()


class PisteOAuthTest(unittest.TestCase):
    def setUp(self):
        self.config = PisteConfig(
            "sandbox", "client-sensitive", "secret-sensitive", PISTE_ENDPOINTS["sandbox"]
        )

    def test_token_is_obtained_and_reused_while_valid(self):
        clock = Mock(return_value=100.0)
        transport = FakeTokenTransport([{
            "access_token": "token-sensitive", "expires_in": 3600, "token_type": "Bearer"
        }])
        oauth = PisteOAuthClient(self.config, transport, clock=clock)
        self.assertEqual(oauth.access_token(), "token-sensitive")
        clock.return_value = 200.0
        self.assertEqual(oauth.access_token(), "token-sensitive")
        self.assertEqual(transport.calls, 1)

    def test_expired_token_and_margin_trigger_renewal(self):
        values = [
            {"access_token": "first", "expires_in": 100, "token_type": "Bearer"},
            {"access_token": "second", "expires_in": 100, "token_type": "Bearer"},
        ]
        transport = FakeTokenTransport(values)
        now = [0.0]
        oauth = PisteOAuthClient(
            self.config, transport, clock=lambda: now[0], expiry_margin=10
        )
        self.assertEqual(oauth.access_token(), "first")
        now[0] = 90.0
        self.assertEqual(oauth.access_token(), "second")
        self.assertEqual(transport.calls, 2)

    def test_concurrent_refresh_is_single(self):
        barrier = threading.Barrier(4)

        class ConcurrentTransport(FakeTokenTransport):
            def request_token(inner_self, config):
                return super(ConcurrentTransport, inner_self).request_token(config)

        transport = ConcurrentTransport([{
            "access_token": "shared", "expires_in": 3600, "token_type": "Bearer"
        }])
        oauth = PisteOAuthClient(self.config, transport, clock=lambda: 0.0)
        results = []

        def obtain():
            barrier.wait()
            results.append(oauth.access_token())

        threads = [threading.Thread(target=obtain) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["shared"] * 4)
        self.assertEqual(transport.calls, 1)

    def test_oauth_error_and_invalid_payload_never_expose_secrets(self):
        self.assertNotIn(self.config.client_id, repr(self.config))
        self.assertNotIn(self.config.client_secret, repr(self.config))
        oauth = PisteOAuthClient(
            self.config,
            FakeTokenTransport([PisteAuthenticationError("Authentification refusée")]),
        )
        with self.assertRaises(PisteAuthenticationError) as raised:
            oauth.access_token()
        message = str(raised.exception)
        self.assertNotIn(self.config.client_id, message)
        self.assertNotIn(self.config.client_secret, message)

        invalid = PisteOAuthClient(
            self.config, FakeTokenTransport([{"access_token": "secret-sensitive"}])
        )
        with self.assertRaises(PisteUnavailableError) as raised:
            invalid.access_token()
        self.assertNotIn("secret-sensitive", str(raised.exception))


class PisteApiTest(unittest.TestCase):
    def setUp(self):
        self.config = PisteConfig(
            "sandbox", "client", "secret", PISTE_ENDPOINTS["sandbox"]
        )

    def test_401_invalidates_and_retries_once(self):
        oauth = Mock()
        oauth.access_token.side_effect = ["old-token", "new-token"]
        transport = Mock()
        transport.request_api.side_effect = [
            PisteUnauthorizedError("expired"), {"results": []}
        ]
        api = PisteLegifranceApi(self.config, oauth, transport)
        self.assertEqual(api.search({"fond": "CODE_ETAT"}), {"results": []})
        oauth.invalidate.assert_called_once_with("old-token")
        self.assertEqual(transport.request_api.call_count, 2)

    def test_second_401_is_not_retried(self):
        oauth = Mock()
        oauth.access_token.side_effect = ["old", "new"]
        transport = Mock()
        transport.request_api.side_effect = PisteUnauthorizedError("expired")
        api = PisteLegifranceApi(self.config, oauth, transport)
        with self.assertRaises(PisteUnauthorizedError):
            api.search({})
        self.assertEqual(transport.request_api.call_count, 2)

    def test_rate_limit_timeout_and_invalid_json_are_normalized(self):
        for exception in (PisteRateLimitError("quota"), PisteTimeoutError("timeout")):
            oauth = Mock()
            oauth.access_token.return_value = "token"
            transport = Mock()
            transport.request_api.side_effect = exception
            with self.subTest(type=type(exception).__name__), self.assertRaises(type(exception)):
                PisteLegifranceApi(self.config, oauth, transport).search({})

        response = Mock(
            status_code=200,
            headers={"content-type": "application/json"},
            is_redirect=False,
            is_permanent_redirect=False,
        )
        response.iter_content.return_value = [b"not-json"]
        session = Mock()
        session.post.return_value = response
        transport = PisteTransport(session=session)
        with self.assertRaises(PisteUnavailableError):
            transport.request_api(self.config, "/search", "token", {})

    def test_transport_maps_429_timeout_and_oauth_rejection(self):
        rate_limited = Mock(
            status_code=429,
            headers={},
            is_redirect=False,
            is_permanent_redirect=False,
        )
        rate_limited.iter_content.return_value = [b'{}']
        session = Mock()
        session.post.return_value = rate_limited
        with self.assertRaises(PisteRateLimitError):
            PisteTransport(session=session).request_api(
                self.config, "/search", "token", {}
            )

        session.post.side_effect = requests.Timeout()
        with self.assertRaises(PisteTimeoutError):
            PisteTransport(session=session).request_api(
                self.config, "/search", "token", {}
            )

        streaming_timeout = Mock(
            status_code=200,
            headers={},
            is_redirect=False,
            is_permanent_redirect=False,
        )
        streaming_timeout.iter_content.side_effect = requests.Timeout()
        session = Mock()
        session.post.return_value = streaming_timeout
        with self.assertRaises(PisteTimeoutError):
            PisteTransport(session=session).request_api(
                self.config, "/search", "token", {}
            )

        rejected = Mock(
            status_code=401,
            headers={},
            is_redirect=False,
            is_permanent_redirect=False,
        )
        rejected.iter_content.return_value = [b'{"error":"invalid_client"}']
        session = Mock()
        session.post.return_value = rejected
        with self.assertRaises(PisteAuthenticationError) as raised:
            PisteTransport(session=session).request_token(self.config)
        self.assertNotIn("secret", str(raised.exception))

    def test_transport_refuses_redirect_and_unknown_operation(self):
        response = Mock(
            status_code=302,
            headers={"location": "https://evil.example"},
            is_redirect=True,
            is_permanent_redirect=False,
        )
        session = Mock()
        session.post.return_value = response
        transport = PisteTransport(session=session)
        with self.assertRaisesRegex(PisteUnavailableError, "Redirection"):
            transport.request_api(self.config, "/search", "token", {})
        with self.assertRaisesRegex(PisteUnavailableError, "Opération"):
            transport.request_api(self.config, "/unknown", "token", {})

    def test_token_credentials_and_bearer_are_sent_only_to_expected_hosts(self):
        token_response = Mock(
            status_code=200,
            headers={},
            is_redirect=False,
            is_permanent_redirect=False,
        )
        token_response.iter_content.return_value = [json.dumps({
            "access_token": "token", "expires_in": 3600, "token_type": "Bearer"
        }).encode()]
        session = Mock()
        session.post.return_value = token_response
        transport = PisteTransport(session=session)
        transport.request_token(self.config)
        call = session.post.call_args
        self.assertEqual(call.args[0], self.config.endpoints.oauth_url)
        self.assertNotIn("Authorization", call.kwargs["headers"])
        self.assertEqual(call.kwargs["data"]["client_secret"], "secret")


class LegifranceAdapterTest(unittest.TestCase):
    def test_search_fetches_article_and_normalizes_provenance(self):
        api = FakeApi(
            [search_result(), {"results": []}],
            article={"article": {
                "id": "LEGIARTI000012345678",
                "num": "R. 214-1",
                "etat": "VIGUEUR",
                "dateDebut": "2024-01-01T00:00:00Z",
                "texteHtml": "<p>Les systèmes d'endiguement font l'objet d'une surveillance.</p>",
            }},
        )
        results = LegifranceAdapter(api).search("surveillance systèmes endiguement")
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item["organization"], "Légifrance")
        self.assertEqual(item["source_id"], "legifrance")
        self.assertEqual(item["category"], LEGAL_PRIMARY)
        self.assertEqual(item["document_date"], "2024-02-03")
        self.assertEqual(
            item["url"],
            f"{LEGIFRANCE_PUBLIC_BASE_URL}/codes/article_lc/LEGIARTI000012345678",
        )
        self.assertNotIn("<p>", item["excerpt"])
        self.assertEqual(item["metadata"]["legal_status"], "VIGUEUR")
        self.assertEqual(item["metadata"]["nor"], "TREL1234567A")
        self.assertEqual(item["metadata"]["article_number"], "R. 214-1")
        self.assertEqual(api.article_ids, ["LEGIARTI000012345678"])

    def test_historical_result_is_not_hidden_and_missing_date_is_preserved(self):
        api = FakeApi(
            [search_result(status="ABROGE", document_date=None), {"results": []}],
            article={"article": {
                "num": "1", "etat": "ABROGE", "texte": "Ancienne obligation."
            }},
        )
        item = LegifranceAdapter(api).search("ancienne obligation")[0]
        self.assertIsNone(item["document_date"])
        self.assertEqual(item["metadata"]["legal_status"], "ABROGE")

    def test_text_result_uses_legi_part_and_official_eli_url(self):
        result = search_result()
        result["results"][0]["sections"] = []
        result["results"][0]["titles"][0].update(
            {
                "id": "LEGITEXT000012345678_04-08-2019",
                "cid": "JORFTEXT000012345678",
            }
        )
        api = FakeApi(
            [result, {"results": []}],
            text={
                "title": "Décret relatif aux digues",
                "eli": "/eli/decret/2024/2/3/TREL1234567D/jo/texte",
                "nature": "DECRET",
                "etat": "VIGUEUR",
                "dateTexte": "2024-02-03",
                "sections": [{
                    "title": "Chapitre II : Surveillance",
                    "articles": [
                        {"num": "1", "content": "<p>Préambule sans rapport.</p>"},
                        {"num": "2", "content": "<p>Les digues sont surveillées.</p>"},
                    ],
                }],
            },
        )
        item = LegifranceAdapter(api).search("décret digues. Chapitre II")[0]
        self.assertEqual(
            item["url"],
            f"{LEGIFRANCE_PUBLIC_BASE_URL}/eli/decret/2024/2/3/TREL1234567D/jo/texte",
        )
        self.assertIn("Article 2", item["excerpt"])
        self.assertIn("Articles 1, 2", item["excerpt"])
        self.assertEqual(api.text_requests[0][0], "LEGITEXT000012345678")
        self.assertEqual(api.text_requests[0][1], "2019-08-04")

    def test_empty_invalid_and_unretrievable_responses(self):
        self.assertEqual(
            LegifranceAdapter(FakeApi([{"results": []}, {"results": []}])).search("rien"),
            [],
        )
        with self.assertRaises(LegifranceResponseError):
            LegifranceAdapter(FakeApi([{}, {"results": []}])).search("invalide")
        api = FakeApi(
            [search_result(), {"results": []}], article={"unexpected": True}
        )
        self.assertEqual(LegifranceAdapter(api).search("article"), [])
        api = FakeApi(
            [search_result(), {"results": []}],
            article=PisteDocumentUnavailableError("absent"),
        )
        self.assertEqual(LegifranceAdapter(api).search("article absent"), [])

    def test_payload_uses_official_search_not_client_side_contains(self):
        code = _search_payload("article 1240 du Code civil", "CODE_ETAT")
        loda = _search_payload(
            "Recherche l'arrêté du 7 avril 2017 précisant le plan de l'étude "
            "de dangers des systèmes d'endiguement. Donne son NOR.",
            "LODA_ETAT",
        )
        self.assertEqual(code["fond"], "CODE_ETAT")
        self.assertEqual(code["recherche"]["champs"][0]["typeChamp"], "NUM_ARTICLE")
        self.assertEqual(
            code["recherche"]["champs"][0]["criteres"][0]["typeRecherche"],
            "EXACTE",
        )
        self.assertEqual(
            code["recherche"]["champs"][0]["criteres"][0]["valeur"], "1240"
        )
        self.assertIn(
            {"facette": "TEXT_NOM_CODE", "valeurs": ["Code civil"]},
            code["recherche"]["filtres"],
        )
        self.assertEqual(loda["recherche"]["champs"][0]["typeChamp"], "TITLE")
        self.assertEqual(
            loda["recherche"]["champs"][0]["criteres"][0]["typeRecherche"],
            "TOUS_LES_MOTS_DANS_UN_CHAMP",
        )
        self.assertIn(
            {"facette": "TEXT_LEGAL_STATUS", "valeur": "VIGUEUR"},
            loda["recherche"]["filtres"],
        )

    def test_payload_searches_nor_exactly_without_hiding_historical_text(self):
        payload = _search_payload(
            "arrêté NOR DEVP1701396A relatif aux systèmes d'endiguement",
            "LODA_ETAT",
        )
        field = payload["recherche"]["champs"][0]
        self.assertEqual(field["typeChamp"], "NOR")
        self.assertEqual(
            field["criteres"],
            [{
                "typeRecherche": "EXACTE",
                "valeur": "DEVP1701396A",
                "operateur": "ET",
            }],
        )
        self.assertNotIn(
            {"facette": "TEXT_LEGAL_STATUS", "valeur": "VIGUEUR"},
            payload["recherche"]["filtres"],
        )

    def test_common_search_uses_legifrance_and_deduplicates(self):
        duplicate = {
            "title": "Article R. 214-1",
            "organization": "Légifrance",
            "url": f"{LEGIFRANCE_PUBLIC_BASE_URL}/codes/article_lc/LEGIARTI000012345678",
            "source_id": "legifrance",
            "category": "LEGAL_PRIMARY",
            "document_date": None,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "excerpt": "Texte",
        }
        adapter = Mock()
        adapter.search.return_value = [duplicate, duplicate]

        class EmptyHtmlClient:
            def get(self, url, _source):
                from digues_webapp.external_sources.security import ExternalHttpResponse
                return ExternalHttpResponse(
                    url, 200, {"content-type": "text/html"}, b"<html></html>"
                )

        result = search_external_sources(
            "obligation digues",
            client=EmptyHtmlClient(),
            legifrance_adapter=adapter,
            limit=4,
        )
        self.assertEqual(result["results"], [duplicate])
        adapter.search.assert_called_once_with("obligation digues", limit=2)

    def test_legifrance_failure_does_not_hide_other_sources_or_create_false_result(self):
        adapter = Mock()
        adapter.search.side_effect = PisteTimeoutError("timeout")

        class CeremaClient:
            def get(self, url, source):
                from digues_webapp.external_sources.security import ExternalHttpResponse
                if source.source_id == "cerema" and "/fr/recherche?" in url:
                    body = b'<article class="search-result"><a class="title-link" href="/fr/guide">Guide</a></article>'
                elif source.source_id == "cerema":
                    body = b"<main>Surveillance technique des digues.</main>"
                else:
                    body = b"<html></html>"
                return ExternalHttpResponse(url, 200, {"content-type": "text/html"}, body)

        result = search_external_sources(
            "surveillance digues",
            client=CeremaClient(),
            legifrance_adapter=adapter,
        )
        self.assertEqual([item["source_id"] for item in result["results"]], ["cerema"])


if __name__ == "__main__":
    unittest.main()
