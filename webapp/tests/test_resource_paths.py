"""R7 level A: resource layouts and private media boundary, no database."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from digues_webapp.resources import frontend_directory
from digues_webapp.media_storage import FilesystemMediaStorage


class ResourcePathsTest(unittest.TestCase):
    def test_provisioner_rejects_private_paths_inside_installed_frontend(self):
        from digues_app.control.database import ControlDatabaseConfig
        from digues_app.control.provisioning import ProvisioningError
        from digues_app.provisioning.config import ProvisioningConfig
        audits = Path(__file__).resolve().parents[2] / "audits"
        with tempfile.TemporaryDirectory(dir=audits) as directory:
            root = Path(directory)
            public = root / "installed/digues_webapp/frontend_assets"
            state, profiles = public / "state", root / "profiles"
            state.mkdir(parents=True, mode=0o700)
            profiles.mkdir(mode=0o700)
            with patch("digues_webapp.resources.frontend_directory", return_value=public):
                with self.assertRaisesRegex(ProvisioningError, "private_data_inside_frontend"):
                    ProvisioningConfig("host=localhost port=5432 user=synthetic dbname=postgres",
                        ControlDatabaseConfig("host=localhost port=5432 user=synthetic dbname=digues_control", "digues_control"),
                        "test", state, profiles / "profiles.json", root / "media")

    def test_demo_rejects_installed_frontend_as_media_root(self):
        from digues_app.demo.target import _storage, DemoTargetError
        audits = Path(__file__).resolve().parents[2] / "audits"
        with tempfile.TemporaryDirectory(dir=audits) as directory:
            public = Path(directory) / "installed/digues_webapp/frontend_assets"
            public.mkdir(parents=True)
            with patch("digues_webapp.resources.frontend_directory", return_value=public), \
                 patch.dict("os.environ", {"MEDIA_BACKEND":"filesystem", "MEDIA_ROOT":str(public)}, clear=True):
                with self.assertRaisesRegex(DemoTargetError, "Configuration du stockage privé invalide"):
                    _storage()

    def test_source_frontend_and_admin_shared_files_exist(self):
        from digues_webapp import admin_frontend
        self.assertTrue((frontend_directory() / "map.html").is_file())
        for path in (admin_frontend.SHARED_HEADER, admin_frontend.SHARED_MODAL_STYLES,
                     admin_frontend.SHARED_FAVICON):
            self.assertTrue(path.is_file())

    def test_installed_resources_are_selected_and_missing_resources_fail(self):
        audits = Path(__file__).resolve().parents[2] / "audits"
        audits.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=audits) as directory:
            package = Path(directory) / "digues_webapp"
            package.mkdir()
            assets = package / "frontend_assets"
            assets.mkdir()
            with patch("digues_webapp.resources.__file__", str(package / "resources.py")):
                self.assertEqual(frontend_directory(), assets)
                assets.rmdir()
                with self.assertRaises(FileNotFoundError):
                    frontend_directory()

    def test_public_frontend_cannot_be_used_as_private_media_root(self):
        with self.assertRaisesRegex(OSError, "outside forbidden roots"):
            FilesystemMediaStorage(frontend_directory())
