from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = PROJECT_ROOT / "webapp" / "frontend"


class HelpPanelTest(unittest.TestCase):
    def test_document_link_resolution_is_limited_to_inventory(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const Help = require("./webapp/frontend/js/help-panel.js");
const docs = new Map([
  ["README.md", { id: "aaaaaaaaaaaaaaaa", path: "README.md" }],
  ["docs/guide.md", { id: "bbbbbbbbbbbbbbbb", path: "docs/guide.md" }],
  ["docs/other.md", { id: "cccccccccccccccc", path: "docs/other.md" }],
]);
assert.equal(Help.resolveHelpTarget("README.md", "docs/guide.md", docs).document.id, "bbbbbbbbbbbbbbbb");
assert.equal(Help.resolveHelpTarget("docs/guide.md", "other.md#Partie", docs).document.id, "cccccccccccccccc");
assert.equal(Help.resolveHelpTarget("docs/guide.md", "../README.md", docs).document.id, "aaaaaaaaaaaaaaaa");
assert.equal(Help.resolveHelpTarget("docs/guide.md", "migration/audit.md", docs), null);
assert.equal(Help.resolveHelpTarget("README.md", "private/secret.md", docs), null);
assert.equal(Help.resolveHelpTarget("README.md", "docs/sirs-v2/README.md", docs), null);
assert.equal(Help.resolveHelpTarget("README.md", "%2e%2e/private/secret.md", docs), null);
assert.equal(Help.resolveHelpTarget("README.md", "https://example.test/guide.md", docs), null);
assert.equal(Help.headingSlug("Données environnementales — Été"), "donnees-environnementales-ete");
assert.throws(() => Help.validateInventory({ documents: [
  { id: "aaaaaaaaaaaaaaaa", label: "Interdit", path: "docs/migration/audit.md" },
] }));
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_ROOT, check=True)

    def test_help_is_available_from_both_business_interfaces_and_pwa_shell(self):
        header = (FRONTEND / "js" / "app-header.js").read_text(encoding="utf-8")
        map_page = (FRONTEND / "map.html").read_text(encoding="utf-8")
        data_page = (FRONTEND / "data.html").read_text(encoding="utf-8")
        service_worker = (FRONTEND / "service-worker.js").read_text(encoding="utf-8")
        styles = (FRONTEND / "css" / "app.css").read_text(encoding="utf-8")

        self.assertIn('id="toggle-help"', header)
        self.assertIn('aria-controls="help-modal"', header)
        for page in (map_page, data_page):
            self.assertEqual(page.count('data-business-src="/static/js/help-panel.js"'), 1)
        self.assertIn('"/static/js/help-panel.js"', service_worker)

        responsive = styles.split("/* Responsive et container queries", 1)[1]
        narrow = responsive.split("@media (max-width: 620px)", 1)[1]
        self.assertIn(".help-dialog", narrow)
        self.assertIn("height: 100dvh", narrow)
        self.assertIn("min-height: 44px", narrow)


if __name__ == "__main__":
    unittest.main()
