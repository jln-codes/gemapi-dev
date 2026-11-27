"""Test réel optionnel et non destructif de la sandbox officielle PISTE."""

import os
import unittest

from digues_webapp.external_sources.legifrance import LegifranceAdapter


HAS_SANDBOX_CREDENTIALS = (
    os.getenv("LEGIFRANCE_RUN_SANDBOX_TEST", "").strip().lower()
    in {"1", "true", "yes", "on"}
    and
    os.getenv("LEGIFRANCE_ENV", "sandbox").strip().lower() == "sandbox"
    and bool(os.getenv("LEGIFRANCE_CLIENT_ID", "").strip())
    and bool(os.getenv("LEGIFRANCE_CLIENT_SECRET", "").strip())
)


@unittest.skipUnless(
    HAS_SANDBOX_CREDENTIALS,
    "test sandbox non demandé ou credentials PISTE sandbox absents",
)
class LegifranceSandboxIntegrationTest(unittest.TestCase):
    def test_read_only_search(self):
        adapter = LegifranceAdapter.from_environment()
        try:
            adapter.api.oauth.access_token()
        except Exception:
            print("OAuth sandbox: échec")
            raise
        print("OAuth sandbox: réussi")
        results = adapter.search(
            "systèmes d'endiguement Code de l'environnement", limit=1
        )
        print(f"Résultats Légifrance: {len(results)}")
        if results:
            first = results[0]
            nature = first.get("metadata", {}).get("nature", "type inconnu")
            print(f"Premier résultat: {first['title']} ({nature})")


if __name__ == "__main__":
    unittest.main()
