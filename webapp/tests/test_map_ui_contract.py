from pathlib import Path
import re
import unittest


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class MapUiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (FRONTEND / "map.html").read_text(encoding="utf-8")
        cls.styles = (FRONTEND / "css" / "app.css").read_text(encoding="utf-8")
        cls.legend_styles = (
            FRONTEND / "css" / "map-disorder-legend.css"
        ).read_text(encoding="utf-8")
        cls.layer_controls = (
            FRONTEND / "js" / "map-layer-controls.js"
        ).read_text(encoding="utf-8")
        cls.service_worker = (
            FRONTEND / "service-worker.js"
        ).read_text(encoding="utf-8")

    def test_disorders_have_one_public_legend_control_and_three_internal_toggles(self):
        self.assertEqual(self.page.count("data-disorder-layer-master"), 1)
        self.assertEqual(self.page.count("data-disorder-layer-toggle"), 3)
        self.assertIn("<span>Désordres</span>", self.page)
        self.assertNotIn("Désordres ponctuels", self.page)

    def test_disorder_urgency_legend_is_collapsed_accessible_and_cached(self):
        self.assertIn('class="legend-disorders-toggle"', self.page)
        self.assertIn('aria-expanded="false"', self.page)
        self.assertIn('aria-controls="legend-disorder-urgencies"', self.page)
        self.assertIn('id="legend-disorder-urgencies"', self.page)
        self.assertIn('href="/static/css/map-disorder-legend.css"', self.page)
        self.assertEqual(
            self.service_worker.count('"/static/css/map-disorder-legend.css"'),
            1,
        )

    def test_disorder_urgency_legend_uses_the_canonical_palette(self):
        self.assertIn("urgencyStyle.resolve(id)", self.layer_controls)
        urgency_function = self.layer_controls.split(
            "function disorderUrgencyLegendItems", 1
        )[1].split("function createDisclosure", 1)[0]
        self.assertNotRegex(
            urgency_function,
            re.compile(r"#[0-9a-fA-F]{6}"),
        )
        self.assertIn(
            "background: var(--legend-urgency-color)", self.legend_styles
        )
        self.assertIn(
            "border: 2px solid var(--legend-urgency-border)",
            self.legend_styles,
        )

    def test_business_creation_actions_share_the_create_menu(self):
        header = (FRONTEND / "js" / "app-header.js").read_text(encoding="utf-8")
        for resource in (
            "mobilier", "equipement-mesure", "reseau-technique",
            "ouvrage-particulier", "cheminement", "vegetation",
            "amenagement-hydraulique", "prestation",
        ):
            self.assertEqual(header.count(f'data-create-type="{resource}"'), 1)

        for filename in (
            "mobilier-editor.js", "reseau-technique-editor.js",
            "ouvrage-particulier-editor.js", "cheminement-editor.js",
            "vegetation-editor.js", "amenagement-hydraulique-editor.js",
            "prestation-editor.js",
        ):
            source = (FRONTEND / "js" / filename).read_text(encoding="utf-8")
            self.assertNotIn('.querySelector(".map-bottom-controls").append', source)

    def test_business_legends_declare_their_supported_geometries(self):
        controls = (FRONTEND / "js" / "map-layer-controls.js").read_text(encoding="utf-8")
        self.assertIn("createBusinessLayerToggle", controls)
        expectations = {
            "mobilier-editor.js": '["Point"]',
            "reseau-technique-editor.js": '["Point", "LineString"]',
            "cheminement-editor.js": '["LineString", "Polygon"]',
            "zone-stockage-editor.js": '["Polygon"]',
            "prestation-editor.js": '["Point", "LineString", "Polygon"]',
        }
        for filename, geometries in expectations.items():
            source = (FRONTEND / "js" / filename).read_text(encoding="utf-8")
            self.assertIn(f"geometryTypes: {geometries}", source)
            self.assertIn("categoryStyle(", source)
        ah = (FRONTEND / "js" / "amenagement-hydraulique-editor.js").read_text(encoding="utf-8")
        self.assertNotIn("geometryTypes:", ah)
        self.assertNotIn('data-role="draw"', ah)
        vegetation = (FRONTEND / "js" / "vegetation-editor.js").read_text(encoding="utf-8")
        self.assertIn('const allowedGeometryTypes = ["Point", "LineString", "Polygon"]', vegetation)
        self.assertIn("geometryTypes: allowedGeometryTypes", vegetation)
        self.assertIn("allowedGeometryTypes,", vegetation)
        self.assertIn("categoryStyle(", vegetation)
        ouvrage_editor = (FRONTEND / "js" / "ouvrage-particulier-editor.js").read_text(encoding="utf-8")
        ouvrage_form = (FRONTEND / "js" / "ouvrage-particulier-form.js").read_text(encoding="utf-8")
        self.assertIn("geometryTypes: geometryDraftFactory.supportedTypes", ouvrage_editor)
        self.assertIn("geometryDraftFactory", ouvrage_editor)
        self.assertIn("categoryStyle(", ouvrage_editor)
        map_source = (FRONTEND / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn('MapLayerControls.categoryStyle("troncons")', map_source)
        self.assertNotIn("Désordres linéaires", self.page)
        self.assertNotIn("Désordres polygonaux", self.page)

    def test_troncon_and_disorder_creation_explain_and_enforce_geometry(self):
        source = (FRONTEND / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn("LineString obligatoire", self.page)
        self.assertIn("Géométrie obligatoire du désordre", self.page)
        self.assertIn("if (!provisionalTronconLayer)", source)
        self.assertIn(
            "Dessinez ou restaurez une LineString avant de créer le tronçon.",
            source,
        )
        self.assertIn("if (!provisionalDesordreLayer)", source)
        self.assertIn(
            "Dessinez ou restaurez la géométrie avant de créer le désordre.",
            source,
        )

    def test_entity_forms_share_presentation_without_merging_panel_types(self):
        data_page = (FRONTEND / "data.html").read_text(encoding="utf-8")
        self.assertIn('class="disorder-form entity-editor-form"', self.page)
        self.assertIn('class="data-entity-detail-form entity-editor-form"', data_page)
        for filename in (
            "cheminement-editor.js", "zone-stockage-editor.js",
            "mobilier-editor.js", "ouvrage-particulier-editor.js",
            "prestation-editor.js", "reseau-technique-editor.js",
            "vegetation-editor.js",
        ):
            source = (FRONTEND / "js" / filename).read_text(encoding="utf-8")
            for class_name in (
                "entity-editor-form", "entity-form-fields",
                "entity-form-status", "entity-geometry-section",
                "entity-form-actions",
            ):
                self.assertIn(class_name, source, filename)
        ah = (FRONTEND / "js" / "amenagement-hydraulique-editor.js").read_text(encoding="utf-8")
        for class_name in ("entity-editor-form", "entity-form-fields", "entity-form-status", "entity-form-actions"):
            self.assertIn(class_name, ah)
        self.assertNotIn("entity-geometry-section", ah)
        base_styles, _responsive_styles = self.styles.split(
            "/* Responsive et container queries", 1
        )
        for selector in (
            ".entity-editor-form", ".entity-form-fields", ".entity-form-status",
            ".entity-geometry-status", ".entity-form-actions",
        ):
            self.assertIn(selector, base_styles)
        self.assertIn(
            "body.pwa-standalone .geometry-editor > .geometry-method-button",
            base_styles,
        )
        self.assertIn(".app-modal-dialog", (
            FRONTEND / "css" / "modal.css"
        ).read_text(encoding="utf-8"))
        self.assertNotIn(".app-modal-dialog", base_styles)

    def test_disorder_selector_has_visible_accessible_sections(self):
        selector = (FRONTEND / "js" / "relation-selector.js").read_text(
            encoding="utf-8"
        )
        cheminement = (FRONTEND / "js" / "cheminement-form.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('selectedLabel = "Désordres associés"', selector)
        self.assertIn('searchFieldLabel.textContent = searchLabel', selector)
        self.assertIn('resultsTitle.textContent = "Résultats de la recherche"', selector)
        self.assertIn('container.setAttribute("aria-busy", "true")', selector)
        self.assertNotIn("Désordres liés", cheminement)
        self.assertEqual(self.service_worker.count('"/static/css/app.css"'), 1)

    def test_business_layers_precede_a_discrete_external_group(self):
        business = self.page.index('id="legend-business-layers"')
        external = self.page.index('class="legend-external-group"')
        self.assertLess(business, external)
        self.assertIn("Hub'Eau · Stations hydrométriques", self.page[external:])
        self.assertIn("Météo-France · Précipitations prévues", self.page[external:])
        self.assertIn('data-capability="external-map-layers"', self.page[external:])
        self.assertIn("border-top: 1px solid var(--app-separator)", self.styles)

    def test_category_colors_are_central_and_urgency_palette_remains_separate(self):
        for key in (
            "troncons", "desordres", "mobilier", "equipements-mesure",
            "reseaux-techniques", "ouvrages-particuliers", "cheminements",
            "vegetation", "zones-stockage", "prestations",
        ):
            self.assertIn(f'{key}: Object.freeze' if "-" not in key else f'"{key}": Object.freeze', self.layer_controls)
        self.assertIn('desordres: Object.freeze({ color: "#c92f3c"', self.layer_controls)
        self.assertIn('vegetation: Object.freeze({ color: "#3f7d44"', self.layer_controls)
        self.assertIn('"zones-stockage": Object.freeze({ color: "#1769aa"', self.layer_controls)
        self.assertIn("urgencyStyle.resolve(id)", self.layer_controls)

    def test_point_marker_uses_the_canonical_urgency_variables(self):
        rule = self.styles.split(".desordre-point-marker span {", 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--disorder-urgency-color", rule)
        self.assertIn("border: 2px solid var(--disorder-urgency-border", rule)
        self.assertNotIn("box-shadow", rule)

    def test_responsive_rules_remain_the_last_css_section(self):
        responsive = self.styles.split(
            "/* Responsive et container queries", 1
        )[1]
        self.assertIn('main[data-layout="single-tool"] .pwa-settings-panel', responsive)


if __name__ == "__main__":
    unittest.main()
