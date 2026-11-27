import json
from pathlib import Path
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


class DataViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.map_page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        cls.data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        cls.data_script = (
            FRONTEND_DIRECTORY / "js" / "data-view.js"
        ).read_text(encoding="utf-8")
        cls.header_script = (
            FRONTEND_DIRECTORY / "js" / "app-header.js"
        ).read_text(encoding="utf-8")
        cls.app_css = (
            FRONTEND_DIRECTORY / "css" / "app.css"
        ).read_text(encoding="utf-8")

    def test_map_and_data_entry_points_have_relative_page_navigation(self):
        self.assertIn('class="map-page" data-interface="map"', self.map_page)
        self.assertIn('class="data-page" data-interface="data"', self.data_page)
        self.assertIn('switchHref: "/base"', self.header_script)
        self.assertIn('switchTarget: "data"', self.header_script)
        self.assertIn('switchLabel: "Base de données"', self.header_script)
        self.assertIn('switchHref: "/carte"', self.header_script)
        self.assertIn('switchTarget: "map"', self.header_script)
        self.assertIn('switchLabel: "Cartographie"', self.header_script)

    def test_interface_action_is_a_direct_link_without_dropdown(self):
        for page, interface_type in ((self.map_page, "map"), (self.data_page, "data")):
            self.assertIn(
                f'<header class="app-header" data-interface="{interface_type}"></header>',
                page,
            )
            self.assertEqual(page.count('src="/static/js/app-header.js"'), 1)
        self.assertEqual(self.header_script.count('class="view-toggle-segment view-switcher"'), 2)
        self.assertNotIn("view-switcher-menu", self.header_script)
        self.assertNotIn("view-switcher-toggle", self.header_script)
        self.assertNotIn(".view-switcher-menu", self.app_css)
        self.assertNotIn(".view-switcher-toggle", self.app_css)

    def test_view_switcher_stays_in_reserved_header_actions_without_clipping(self):
        navigation = self.header_script.split(
            '<nav class="main-navigation"', 2
        )[2].split("</nav>", 1)[0]  # Business header, not the separate admin navigation.
        self.assertNotIn('${interfaceToggle(interfaceType, config)}', navigation)
        self.assertLess(navigation.index("${commands}"), navigation.index("${settings()}"))
        self.assertLess(navigation.index("${settings()}"), navigation.index('id="toggle-ai"'))
        actions = self.header_script.split('<div class="header-actions"', 1)[1].split(
            '<p id="status"', 1
        )[0]
        self.assertLess(
            actions.index('${interfaceToggle(interfaceType, config)}'),
            actions.index('id="open-business-session"'),
        )
        self.assertLess(
            actions.index('id="open-business-session"'),
            actions.index('id="network-status"'),
        )

        group_rule = self.app_css.split(".header-navigation", 1)[1].split(
            "}", 1
        )[0]
        viewport_rule = self.app_css.split(".app-navigation-viewport", 1)[
            1
        ].split("}", 1)[0]
        navigation_rule = self.app_css.split(".main-navigation", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn("display: flex", group_rule)
        self.assertNotIn("gap:", group_rule)
        self.assertIn("display: flex", viewport_rule)
        self.assertIn("align-self: stretch", navigation_rule)

    def test_map_page_contains_only_map_navigation_modules(self):
        for label in ("Créer", "Outils", "Assistant IA"):
            self.assertIn(label, self.header_script)
        self.assertIn('id="toggle-heritage"', self.header_script)
        self.assertNotIn('id="toggle-queries"', self.map_page)
        self.assertNotIn('id="queries-view"', self.map_page)
        self.assertNotIn('src="/static/js/sql-tool.js"', self.map_page)

    def test_data_page_contains_real_shared_modules_and_sql(self):
        self.assertNotIn("leaflet", self.data_page.casefold())
        self.assertNotIn("map.js", self.data_page)
        for label in ("Vues", "Requêtes SQL", "Assistant IA"):
            self.assertIn(label, self.header_script)
        self.assertIn('id="toggle-heritage"', self.header_script)
        for identity in (
            'id="heritage-panel"',
            'id="queries-view"',
            'id="sql-query-form"',
            'id="ai-panel"',
        ):
            self.assertIn(identity, self.data_page)
        for script in (
            'src="/static/js/heritage-panel.js"',
            'src="/static/js/ai-panel.js"',
            'src="/static/js/sql-tool.js"',
            'src="/static/js/data-view-default.js"',
            'src="/static/js/data-view.js"',
        ):
            self.assertIn(script, self.data_page)

    def test_statistics_placeholder_is_the_initial_central_section(self):
        statistics = self.data_page.split('id="data-statistics-view"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn("hidden", statistics.split(">", 1)[0])
        self.assertIn('id="data-statistics-title">Statistiques</h1>', statistics)
        self.assertNotIn('id="data-disorders-view"', self.data_page)
        self.assertNotIn('id="show-disorders"', self.data_page)
        self.assertNotIn("DataDisorders", self.data_script)

    def test_base_home_contains_a_discreet_extensible_summary(self):
        self.assertIn('id="base-summary"', self.data_page)
        self.assertIn('id="base-summary-title">Synthèse de la base', self.data_page)
        self.assertIn("Système de coordonnées", self.data_page)
        self.assertNotIn('id="base-summary"', self.map_page)
        self.assertNotIn("RGF93 / CC50", self.data_page)
        self.assertIn('BusinessRuntime.fetch("/api/base-summary")', self.data_script)
        self.assertIn("baseSummaryCrs.textContent = summary.crs.label", self.data_script)

        base_rule = self.app_css.split(".base-summary {", 1)[1].split("}", 1)[0]
        grid_rule = self.app_css.split(".base-summary-grid {", 1)[1].split("}", 1)[0]
        responsive = self.app_css.split("@media (max-width: 840px)", 1)[1]
        compact_rule = responsive.split(".base-summary {", 1)[1].split("}", 1)[0]
        self.assertIn("background: #f7f9f8", base_rule)
        self.assertIn("flex: 0 0 auto", base_rule)
        self.assertIn("repeat(auto-fit", grid_rule)
        self.assertIn("grid-template-columns: 1fr", compact_rule)

    def test_ai_panel_follows_page_content_and_is_aligned_to_the_right(self):
        map_primary_position = self.map_page.index('id="primary-area"')
        map_ai_position = self.map_page.index('id="ai-panel"')
        disorders_position = self.data_page.index('id="data-statistics-view"')
        builder_position = self.data_page.index('id="data-view-builder-workspace"')
        heritage_position = self.data_page.index('id="heritage-panel"')
        queries_position = self.data_page.index('id="queries-view"')
        ai_position = self.data_page.index('id="ai-panel"')

        self.assertLess(map_primary_position, map_ai_position)
        self.assertLess(disorders_position, ai_position)
        self.assertLess(builder_position, ai_position)
        self.assertLess(heritage_position, ai_position)
        self.assertLess(queries_position, ai_position)

        data_ai_rule = self.app_css.split(".data-page .ai-panel", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn("margin-left: auto", data_ai_rule)

    def test_patrimoine_and_ai_are_explicit_shared_modules(self):
        for script in ("heritage-panel.js", "ai-panel.js"):
            self.assertIn(script, self.map_page)
            self.assertIn(script, self.data_page)
        self.assertIn("HeritagePanel.createController", self.data_script)
        self.assertIn("AiPanel.createController", self.data_script)
        self.assertIn('fetch("/api/config")', self.data_script)
        self.assertIn(
            'additionalCompatiblePairs: [["heritage", "ai"]]', self.data_script
        )

    def test_data_heritage_panel_overlays_the_workspace(self):
        data_workspace_rule = self.app_css.split(".data-workspace {", 1)[1].split(
            "}", 1
        )[0]
        data_heritage_rule = self.app_css.split(".heritage-panel {", 1)[1].split("}", 1)[0]
        disorders_rule = self.app_css.split(".data-statistics-view {", 1)[1].split(
            "}", 1
        )[0]
        builder_rule = self.app_css.split(
            "\n.data-view-builder-workspace {", 1
        )[1].split("}", 1)[0]

        self.assertIn("display: flex", data_workspace_rule)
        self.assertIn("overflow: hidden", data_workspace_rule)
        self.assertIn("position: absolute", data_heritage_rule)
        self.assertIn("left: 0", data_heritage_rule)
        self.assertNotIn("flex:", data_heritage_rule)
        self.assertIn("flex: 1 1 auto", disorders_rule)
        self.assertIn("min-width: 0", disorders_rule)
        self.assertIn("flex: 1 1 auto", builder_rule)
        self.assertIn("min-width: 0", builder_rule)

        self.assertIn('id="heritage-selection-summary"', self.data_page)

    def test_views_opens_the_dedicated_builder_workspace(self):
        self.assertIn('id="toggle-views"', self.header_script)
        self.assertIn('aria-controls="data-view-builder-workspace"', self.header_script)
        self.assertIn('id="data-view-builder-workspace"', self.data_page)
        self.assertNotIn('id="data-placeholder"', self.data_page)
        self.assertIn('viewBuilderController.setOpen(centralMode === "views")', self.data_script)
        self.assertIn(
            "viewBuilderController.setHiddenTronconIds(nextHiddenTronconIds)",
            self.data_script,
        )

    def test_data_page_shares_pwa_updates_without_becoming_install_entry(self):
        self.assertNotIn('rel="manifest"', self.data_page)
        self.assertIn('id="pwa-update-notice"', self.data_page)
        self.assertIn('src="/static/js/pwa.js"', self.data_page)


if __name__ == "__main__":
    unittest.main()
