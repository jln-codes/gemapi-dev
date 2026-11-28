"""Mocked import and QAction smoke check without a QGIS installation."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from tempfile import TemporaryDirectory
from zipfile import ZipFile


class Signal:
    def connect(self, callback):
        self.callback = callback

    def disconnect(self, _callback):
        self.callback = None


class Project:
    def __init__(self):
        self.cleared = Signal()
        self.readProject = Signal()
        self.fileNameChanged = Signal()

    @classmethod
    def instance(cls):
        return cls()


class Icon:
    def __init__(self, path):
        self.path = path


class Action:
    def __init__(self, *args):
        self.icon = args[0] if isinstance(args[0], Icon) else None
        self.text = args[1] if self.icon else args[0]
        self.triggered = Signal()
        self.tooltip = ""

    def setCheckable(self, value):
        self.checkable = value

    def setChecked(self, value):
        self.checked = value

    def setToolTip(self, text):
        self.tooltip = text

    def deleteLater(self):
        pass


class Toolbar:
    def __init__(self, *args):
        self.entries = []

    def setObjectName(self, name):
        self.name = name

    def setToolButtonStyle(self, style):
        self.style = style

    def show(self):
        self.visible = True

    def addAction(self, action):
        self.entries.append(action)

    def removeAction(self, action):
        self.entries.remove(action)

    def actions(self):
        return self.entries

    def setParent(self, parent):
        pass

    def deleteLater(self):
        pass


class Interface:
    def __init__(self):
        self.toolbar = None

    def mainWindow(self):
        return self

    def findChild(self, cls, name):
        return self.toolbar

    def addToolBar(self, toolbar):
        self.toolbar = toolbar

    def removeToolBar(self, toolbar):
        self.toolbar = None

    def addPluginToMenu(self, _, action):
        self.menu_actions = getattr(self, "menu_actions", []) + [action]

    def addToolBarIcon(self, action):
        self.toolbar_action = action

    def removePluginMenu(self, *_):
        pass

    def removeToolBarIcon(self, *_):
        pass


def module(name, **members):
    result = ModuleType(name)
    result.__dict__.update(members)
    sys.modules[name] = result
    return result


def main(package_root=None, package_name="gemapi_app"):
    import importlib
    dummy = type("Dummy", (), {})
    module("qgis")
    module("qgis.PyQt", uic=SimpleNamespace(loadUi=None))
    module("qgis.PyQt.QtCore", QObject=dummy, QPoint=dummy, QDate=dummy, QEvent=dummy, QTimer=dummy, QUrl=dummy, QSignalBlocker=dummy,
           Qt=SimpleNamespace(UserRole=32, RightDockWidgetArea=2, ToolButtonIconOnly=0),
           pyqtSignal=lambda *args: Signal())
    module("qgis.PyQt.QtGui", QDesktopServices=dummy, QIcon=Icon, QImageReader=dummy,
           QPixmap=dummy, QColor=dummy, QCursor=dummy, QGuiApplication=dummy)
    module("qgis.PyQt.QtWidgets", QAction=Action, QToolBar=Toolbar, QPushButton=dummy, QDialog=dummy, QFileDialog=dummy,
           QGroupBox=dummy, QFormLayout=dummy, QHBoxLayout=dummy, QToolButton=dummy, QLabel=dummy, QLayout=dummy,
           QListWidgetItem=dummy, QMessageBox=dummy, QSizePolicy=dummy,
           QAbstractItemView=dummy, QLineEdit=dummy, QComboBox=dummy,
           QListWidget=dummy, QVBoxLayout=dummy, QDockWidget=dummy, QTextBrowser=dummy,
           QToolTip=dummy)
    module("qgis.core", Qgis=SimpleNamespace(
        GeometryOperationResult=SimpleNamespace(Success=0), Info=0, Warning=1, Critical=2),
           QgsMessageLog=SimpleNamespace(logMessage=lambda *_: None),
           **{name: dummy for name in (
        "QgsCoordinateTransform", "QgsGeometry", "QgsDataSourceUri",
        "QgsProviderRegistry", "QgsCoordinateReferenceSystem", "QgsPointXY", "QgsWkbTypes",
        "QgsRectangle", "QgsSpatialIndex", "QgsVectorLayer", "QgsUnitTypes",
    )}, QgsProject=Project, QgsApplication=dummy)
    module("qgis.gui", QgsMapCanvasTracer=dummy, QgsMapTool=dummy, QgsRubberBand=dummy,
           QgsVertexMarker=dummy)
    sys.path.insert(0, str(package_root or Path(__file__).resolve().parents[1]))
    gemapi_app = importlib.import_module(package_name)
    report_data = importlib.import_module(package_name + ".report_data")
    build_management_query = report_data.build_management_query
    filter_option_queries = report_data.filter_option_queries

    options = filter_option_queries(include_statuses=True, include_providers=True)
    assert set(("ah_stockage", "ah_ouvrage", "cours_eau", "mobilier", "cheminements", "vegetation")) <= set(options)
    assert "type_ecoulement" not in options["cours_eau"]
    assert options["cours_eau"] == "SELECT c.id::text,c.nom FROM gemapi.cours_eau c ORDER BY 2,1"
    assert "FROM gemapi.prestations" in build_management_query({})

    iface = Interface()
    plugin = gemapi_app.classFactory(iface)
    plugin.initGui()
    assert plugin.action.text == ""
    assert plugin.action.tooltip == "GEMAPI-App"
    assert plugin.action.triggered.callback == plugin._toggle_interface
    assert Path(plugin.action.icon.path).is_file()
    assert iface.toolbar.actions() == [plugin.action]
    assert [action.text for action in iface.menu_actions] == [
        "GEMAPI-App", "Créer un rapport", "Créer une digue…"]
    plugin.unload()
    print("Imports simulés, classFactory et bouton à icône seule : OK")


if __name__ == "__main__":
    plugin_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin_root / "tools"))
    from build_plugin import validate_archive, validate_plugin, write_archive

    archive = write_archive(validate_plugin())
    validate_archive(archive)
    with TemporaryDirectory() as directory, ZipFile(archive) as package:
        if package.testzip() is not None:
            raise AssertionError("ZIP du plugin corrompu")
        assert all(name.startswith("gemapi_app/") for name in package.namelist())
        package.extractall(directory)
        main(Path(directory))
