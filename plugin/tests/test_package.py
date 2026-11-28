"""Check the installable archive independently of the source import path."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_plugin", PROJECT / "tools" / "build_plugin.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PackageTests(unittest.TestCase):
    def test_reproducible_installable_archive(self):
        version = build.validate_plugin()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "qgis-profile"
            plugin_path = profile / "python" / "plugins"
            original_dist = build.DIST_DIR
            try:
                build.DIST_DIR = root / "dist"
                archive = build.write_archive(version)
                first = archive.read_bytes()
                self.assertEqual(archive.name, f"gemapi-app-{version}.zip")
                self.assertEqual(first, build.write_archive(version).read_bytes())
            finally:
                build.DIST_DIR = original_dist
            with ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                names = package.namelist()
                self.assertFalse(any('api_key' in name.lower() or name.endswith('.txt') and not name.endswith('metadata.txt') for name in names))
                secret_path = PROJECT.parent / 'MISTRAL_API_KEY.txt'
                if secret_path.is_file():
                    secret = secret_path.read_bytes().strip()
                    self.assertTrue(not secret or all(secret not in package.read(name) for name in names),
                                    'Secret présent dans le ZIP')
                    value = secret.partition(b'=')[2].strip().strip(b'\'"') or secret
                    self.assertTrue(not value or all(value not in package.read(name) for name in names),
                                    'Valeur du secret présente dans le ZIP')
                build.validate_archive(archive)
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual({PurePosixPath(name).parts[0] for name in names},
                                 {"gemapi_app"})
                self.assertEqual(set(names),
                                 {f"{build.PACKAGE_NAME}/{name}" for name in build.REQUIRED_FILES})
                self.assertIn(f"{build.PACKAGE_NAME}/help.md", names)
                self.assertIn(b"Cr\xc3\xa9er une prestation",
                              package.read(f"{build.PACKAGE_NAME}/help.md"))
                for entry in package.infolist():
                    self.assertEqual(entry.date_time, build.ARCHIVE_TIMESTAMP)
                    relative = PurePosixPath(entry.filename).relative_to(build.PACKAGE_NAME)
                    self.assertEqual(package.read(entry), build.archive_content(build.PLUGIN_DIR / relative))
                installed = plugin_path / build.PACKAGE_NAME
                installed.mkdir(parents=True)
                (installed / "metadata.txt").write_text(
                    "[general]\nname=SIG Prestations\nversion=0.7.13\n", encoding="utf-8")
                package.extractall(plugin_path)
            self.assertEqual({path.name for path in plugin_path.iterdir()},
                             {"gemapi_app"})
            self.assertIn("name=GEMAPI-App", (installed / "metadata.txt").read_text(
                encoding="utf-8"))
            self.assertIn(f"version={version}", (installed / "metadata.txt").read_text(
                encoding="utf-8"))
            self.assertEqual((installed / "help.md").read_bytes(),
                             (build.PLUGIN_DIR / "help.md").read_bytes())
            # Separate interpreter: no source-package imports or Qt stubs leak in.
            smoke = PROJECT / "tests" / "smoke_import.py"
            subprocess.run([
                sys.executable, "-c",
                "import runpy, sys; runpy.run_path(sys.argv[1])['main'](sys.argv[2], sys.argv[3])",
                str(smoke), str(plugin_path), build.PACKAGE_NAME,
            ], cwd=root, check=True)
            if importlib.util.find_spec("qgis") is not None:
                installed_check = """
import sys
from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.gui import QgsMapCanvas
from qgis.utils import findPlugins
found = list(findPlugins(sys.argv[1]))
found = [item for item in found if item[0] == 'gemapi_app']
assert len(found) == 1
assert found[0][1].get('general', 'name') == 'GEMAPI-App'
sys.path.insert(0, sys.argv[1])
from gemapi_app import classFactory
import gemapi_app
assert 'sig_prestations' not in sys.modules
from pathlib import Path
assert Path(gemapi_app.__file__).resolve().parent == Path(sys.argv[1]).resolve() / 'gemapi_app'
class Interface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.window.show()
    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def addPluginToMenu(self, *args): pass
    def removePluginMenu(self, *args): pass
    def addToolBarIcon(self, *args): pass
    def removeToolBarIcon(self, *args): pass
app = QgsApplication([], True, sys.argv[2])
app.initQgis()
# The Qt promoted widget must come from the installed standard package.
from qgis.PyQt import uic
form = uic.loadUi(str(Path(sys.argv[1]) / 'gemapi_app' / 'create.ui'))
assert type(form.typeCombo).__module__ == 'gemapi_app.type_picker'
plugin = classFactory(Interface())
plugin.initGui()
action = plugin.action
plugin.initGui()
assert plugin.toolbar.actions() == [action]
action.trigger()
assert plugin.home.isVisible() and action.isChecked()
plugin.home.assistantButton.click()
conversation = plugin.assistant
assert conversation is not None and conversation.isVisible()
assert type(conversation).__module__ == 'gemapi_app.assistant_dialog'
assert not plugin.home.isVisible()
plugin.menu_action.trigger()
assert plugin.home.assistantButton.text() == 'Assistant IA'
assert plugin.home.assistantButton.icon().isNull()
plugin.home.assistantButton.click()
assert plugin.assistant is conversation
toolbar = plugin.toolbar
plugin.show_help()
assert 'Créer une prestation' in plugin.help_dock.widget().toPlainText()
plugin.unload()
assert action not in toolbar.actions()
app.exitQgis()
"""
                environment = os.environ.copy()
                environment.setdefault("QT_QPA_PLATFORM", "offscreen")
                subprocess.run([sys.executable, "-c", installed_check,
                                str(plugin_path), str(profile)], cwd=root,
                               env=environment, check=True)


if __name__ == "__main__":
    unittest.main()
