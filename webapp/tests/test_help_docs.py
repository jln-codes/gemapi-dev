import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
import httpx

from digues_webapp.help_docs import build_help_inventory, register_routes


class HelpDocumentsApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = self.enterContext(TemporaryDirectory())
        self.root = Path(self.temporary_directory)
        (self.root / "docs" / "migration").mkdir(parents=True)
        (self.root / "docs" / "sirs-v2").mkdir()
        (self.root / "docs" / "deployment").mkdir()
        (self.root / "docs" / "deployment" / "server-deployment.md").write_text(
            "# Déploiement serveur\n\nGuide public déplacé.", encoding="utf-8",
        )
        (self.root / "private").mkdir()
        (self.root / "README.md").write_text("# Digues App\n\nPrésentation.", encoding="utf-8")
        (self.root / "docs" / "public-guide.md").write_text(
            "# Guide public\n\nContenu public.", encoding="utf-8",
        )
        (self.root / "docs" / "sans_titre.md").write_text(
            "Contenu sans titre.", encoding="utf-8",
        )
        (self.root / "docs" / "migration" / "audit.md").write_text(
            "# Audit privé de navigation", encoding="utf-8",
        )
        (self.root / "docs" / "sirs-v2" / "README.md").write_text(
            "# Documentation V2", encoding="utf-8",
        )
        (self.root / "private" / "secret.md").write_text("# Secret", encoding="utf-8")
        (self.root / "docs" / "notes.txt").write_text("Hors périmètre", encoding="utf-8")

        application = FastAPI()
        register_routes(application, self.root)
        self.application = application

    def get(self, path: str) -> httpx.Response:
        async def request() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.application)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get(path)
        return asyncio.run(request())

    def test_inventory_contains_only_root_readme_and_direct_docs_markdown(self):
        inventory = build_help_inventory(self.root)
        self.assertEqual(
            [document.path for document in inventory],
            ["README.md", "docs/public-guide.md", "docs/sans_titre.md",
             "docs/deployment/server-deployment.md"],
        )
        labels = {document.path: document.label for document in inventory}
        self.assertEqual(labels["README.md"], "Présentation générale")
        self.assertEqual(labels["docs/public-guide.md"], "Guide public")
        self.assertEqual(labels["docs/sans_titre.md"], "Sans titre")
        self.assertNotIn("public-guide.md", labels.values())

    def test_api_reads_readme_and_direct_docs_document_by_opaque_id(self):
        response = self.get("/api/help/documents")
        self.assertEqual(response.status_code, 200)
        documents = response.json()["documents"]
        self.assertTrue(all(set(document) == {"id", "label", "path"} for document in documents))

        by_path = {document["path"]: document for document in documents}
        for path, expected in (
            ("README.md", "Présentation."),
            ("docs/public-guide.md", "Contenu public."),
            ("docs/deployment/server-deployment.md", "Guide public déplacé."),
        ):
            with self.subTest(path=path):
                document = by_path[path]
                self.assertRegex(document["id"], r"^[0-9a-f]{16}$")
                content = self.get(f"/api/help/documents/{document['id']}")
                self.assertEqual(content.status_code, 200)
                self.assertEqual(content.json()["path"], path)
                self.assertIn(expected, content.json()["content"])

    def test_subdirectories_private_non_markdown_and_unknown_documents_are_excluded(self):
        paths = {
            document["path"]
            for document in self.get("/api/help/documents").json()["documents"]
        }
        for excluded in (
            "docs/migration/audit.md",
            "docs/sirs-v2/README.md",
            "private/secret.md",
            "docs/notes.txt",
        ):
            self.assertNotIn(excluded, paths)

        attacks = (
            "missing",
            "notes.txt",
            "..%2Fprivate%2Fsecret.md",
            "%2E%2E%2Fprivate%2Fsecret.md",
            "%252e%252e%252fprivate%252fsecret.md",
            "%2Fetc%2Fpasswd",
        )
        for value in attacks:
            with self.subTest(value=value):
                response = self.get(f"/api/help/documents/{value}")
                self.assertIn(response.status_code, {404, 422})

    def test_symlink_to_file_outside_docs_is_not_inventoried(self):
        link = self.root / "docs" / "linked-secret.md"
        try:
            link.symlink_to(self.root / "private" / "secret.md")
        except OSError as exc:
            self.skipTest(f"Liens symboliques indisponibles : {exc}")
        paths = {document.path for document in build_help_inventory(self.root)}
        self.assertNotIn("docs/linked-secret.md", paths)

    def test_curated_guide_cannot_follow_a_symlink_directory(self):
        guide = self.root / "docs/deployment/server-deployment.md"
        guide.unlink()
        guide.parent.rmdir()
        (self.root / "private/server-deployment.md").write_text("# Secret", encoding="utf-8")
        guide.parent.symlink_to(self.root / "private", target_is_directory=True)
        self.assertNotIn("docs/deployment/server-deployment.md",
                         {document.path for document in build_help_inventory(self.root)})


if __name__ == "__main__":
    unittest.main()
