from pathlib import Path
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


class ResourceMutationProducerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.map_script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(
            encoding="utf-8"
        )
        cls.detail_script = (
            FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        ).read_text(encoding="utf-8")
        cls.data_script = (FRONTEND_DIRECTORY / "js" / "data-view.js").read_text(
            encoding="utf-8"
        )

    def test_map_uses_one_confirmed_publication_helper(self):
        self.assertIn("function publishConfirmedMutation", self.map_script)
        self.assertIn(
            "ResourceMutationController.publishMutation(descriptor)", self.map_script
        )
        self.assertIn("function publishConfirmedDesordreUpdate", self.map_script)

    def test_technical_territory_signal_and_business_writes_are_producers(self):
        for resource in (
            '"territoires-administratifs"',
            '"observations"',
            'configuration.resource, "create"',
            '"desordres", "create"',
        ):
            with self.subTest(resource=resource):
                self.assertIn(resource, self.map_script)
        self.assertIn('replacing ? "update" : "create"', self.map_script)
        self.assertIn("featureIdentifier(collection?.features?.[0])", self.map_script)
        self.assertNotIn("territoireBaseVersion", self.map_script)
        self.assertNotIn("territoireConflict", self.map_script)
        self.assertIn("publishConfirmedMutation(\"observations\", \"update\"", self.map_script)

    def test_general_disorder_save_publishes_once_after_confirmed_write(self):
        # Structural ownership complements the actual event/version/rollback cases in
        # resource-concurrency.spec.js and test_disorder_form_geometry_postgres.py.
        save = self.map_script.split("async function saveDisorderDraft()", 1)[1].split(
            "function selectedDesordreTronconIds", 1
        )[0]
        self.assertEqual(save.count("await writeDesordre("), 1)
        self.assertEqual(save.count("publishConfirmedDesordreUpdate(feature)"), 1)
        self.assertLess(save.index("await writeDesordre("), save.index(
            "publishConfirmedDesordreUpdate(feature)"
        ))
        self.assertLess(save.index("EntityFormSession.validateReceipt"), save.index(
            "publishConfirmedDesordreUpdate(feature)"
        ))
        self.assertNotIn("saveLineRequest", self.map_script)

    def test_base_detail_publishes_after_put_and_before_secondary_refresh(self):
        fetch_position = self.detail_script.index('response = await fetchJson')
        publication_position = self.detail_script.index("onMutationConfirmed({")
        refresh_position = self.detail_script.index("await onSaved")
        self.assertLess(fetch_position, publication_position)
        self.assertLess(publication_position, refresh_position)
        self.assertIn("L’actualisation locale est incomplète", self.detail_script)
        self.assertIn(
            "ResourceMutationController.publishMutation(mutation, { applyLocally: true })",
            self.data_script,
        )

    def test_read_queries_and_exports_are_not_producers(self):
        for script_name in (
            "data-view-builder.js", "data-gpkg-export.js", "sql-tool.js",
        ):
            source = (FRONTEND_DIRECTORY / "js" / script_name).read_text(
                encoding="utf-8"
            )
            with self.subTest(script=script_name):
                self.assertNotIn("publishMutation(", source)


if __name__ == "__main__":
    unittest.main()
