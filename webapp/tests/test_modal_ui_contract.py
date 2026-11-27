from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
ADMIN = ROOT / "backend" / "digues_webapp" / "admin_assets"


class ModalUiContractTest(unittest.TestCase):
    def test_shared_modal_styles_are_loaded_by_business_and_admin_screens(self):
        for name in ("map.html", "data.html"):
            page = (FRONTEND / name).read_text(encoding="utf-8")
            self.assertIn('href="/static/css/modal.css"', page)
            self.assertLess(
                page.index('href="/static/css/modal.css"'),
                page.index('href="/static/css/app.css"'),
            )
        admin_login = (ADMIN / "login.html").read_text(encoding="utf-8")
        self.assertIn('href="/admin-assets/modal.css"', admin_login)
        self.assertLess(
            admin_login.index('href="/admin-assets/modal.css"'),
            admin_login.index('href="/admin-assets/admin.css"'),
        )
        self.assertIn('class="app-modal-backdrop admin-login-modal"', admin_login)
        self.assertIn('role="dialog" aria-modal="true"', admin_login)
        self.assertIn('class="app-modal-header"', admin_login)
        self.assertIn('class="app-modal-primary"', admin_login)
        service_worker = (FRONTEND / "service-worker.js").read_text(encoding="utf-8")
        self.assertEqual(service_worker.count('"/static/css/modal.css"'), 1)

    def test_login_base_selection_and_session_use_the_common_classes(self):
        bootstrap = (FRONTEND / "js" / "business-bootstrap.js").read_text(encoding="utf-8")
        for class_name in (
            "app-modal-backdrop", "app-modal-dialog", "app-modal-header",
            "app-modal-body", "app-modal-actions", "app-modal-primary",
        ):
            self.assertIn(class_name, bootstrap)
        self.assertIn('className = "identity-base-list"', bootstrap)
        self.assertIn('link.className = "identity-base-choice"', bootstrap)
        choices = (FRONTEND / "css" / "business-session.css").read_text(encoding="utf-8")
        self.assertIn('.identity-base-list .identity-base-choice {', choices)
        self.assertIn('dialog.setAttribute("role", "dialog")', bootstrap)
        self.assertIn('class="app-modal-dialog territoire-dialog business-session-dialog"', bootstrap)

    def test_visual_foundations_are_defined_only_in_the_shared_stylesheet(self):
        shared = (FRONTEND / "css" / "modal.css").read_text(encoding="utf-8")
        app = (FRONTEND / "css" / "app.css").read_text(encoding="utf-8")
        admin = (ADMIN / "admin.css").read_text(encoding="utf-8")
        app_base = app.split("/* Responsive et container queries", 1)[0]
        self.assertIn(".app-modal-header", shared)
        self.assertIn(".app-modal-primary", shared)
        self.assertNotRegex(app_base, re.compile(r"^\.territoire-modal \{", re.MULTILINE))
        self.assertNotRegex(app_base, re.compile(r"^\.editor-actions \{", re.MULTILINE))
        self.assertNotIn(".login {", admin)

    def test_responsive_rules_remain_last_in_both_modal_stylesheets(self):
        app = (FRONTEND / "css" / "app.css").read_text(encoding="utf-8")
        self.assertGreater(app.index("/* Responsive et container queries"), app.index(".legend-external-group"))
        shared = (FRONTEND / "css" / "modal.css").read_text(encoding="utf-8")
        self.assertEqual(shared.count("@media"), 1)
        self.assertTrue(shared.rstrip().endswith("}"))
        self.assertGreater(shared.index("@media"), shared.index(".app-modal-primary:disabled"))


if __name__ == "__main__":
    unittest.main()
