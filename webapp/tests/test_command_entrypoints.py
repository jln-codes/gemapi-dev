"""Contrat déclaratif des commandes installées, sans opération métier réelle."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

from digues_webapp import media_storage
from digues_webapp.knowledge import indexer


ROOT = Path(__file__).resolve().parents[1]


class CommandEntrypointTest(unittest.TestCase):
    def test_webapp_declares_the_complete_business_package_as_a_dependency(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertIn("digues-app==0.1.0", config["project"]["dependencies"])
        self.assertNotIn("packages", config["tool"]["setuptools"])

    def test_only_canonical_entrypoints_are_installed(self):
        scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        self.assertEqual(
            scripts["digues-webapp-clean-media"],
            "digues_webapp.media_storage:main",
        )
        self.assertEqual(
            scripts["digues-webapp-index-knowledge"],
            "digues_webapp.knowledge.indexer:main",
        )
        self.assertEqual(set(scripts), {
            "digues-webapp-clean-media",
            "digues-webapp-index-knowledge",
        })

    def test_media_help_has_no_business_side_effect(self):
        output = io.StringIO()
        with patch("sys.argv", ["digues-webapp-clean-media", "--help"]), \
                patch.object(media_storage, "clean_prepared") as clean, \
                redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            media_storage.main()
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--business-database-id", output.getvalue())
        clean.assert_not_called()

    def test_indexer_reads_the_canonical_repository_root_without_indexing_real_data(self):
        report = indexer.IndexReport(0, 0, 0, 0)
        with patch.dict(os.environ, {"DIGUES_REPOSITORY_ROOT": "/synthetic/checkout"}, clear=True), \
                patch.object(indexer, "open_write_connection") as opened, \
                patch.object(indexer, "KnowledgeRepository") as repository, \
                patch.object(indexer, "index_repository", return_value=report) as command, \
                patch("builtins.print"):
            self.assertEqual(indexer.main(), 0)
        connection = opened.return_value.__enter__.return_value
        repository.assert_called_once_with(connection)
        command.assert_called_once_with(Path("/synthetic/checkout"), repository.return_value)
