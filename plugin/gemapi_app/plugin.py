"""QGIS entry point for prestation management and reports."""
from pathlib import Path
import traceback
from uuid import UUID

from qgis.PyQt import uic
from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget, QSizePolicy, QTextBrowser, QToolBar
from qgis.core import QgsDataSourceUri, QgsMessageLog, QgsProject, Qgis

from .creation import CreationDialog
from .management import ManageDialog
from .window_geometry import CompactHomePositioner
from .perf import profiled
from .session_data import SessionData
from .session_data import _connection_info
from .permissions import prestation_permissions


_active_plugin = None


def resolve_map_prestation(project, layer_id, prestation_id):
    """Accept only a business UUID from this project's prestation view."""
    layer = project.mapLayer(str(layer_id))
    if layer is None or not layer.isValid() or layer.providerType() != "postgres":
        raise ValueError("La couche de prestation n'est plus disponible.")
    uri = QgsDataSourceUri(layer.source())
    if uri.schema() != "gemapi" or uri.table() != "prestations_carte":
        raise ValueError("Cette action est réservée à gemapi.prestations_carte.")
    if uri.connectionInfo(False) != _connection_info(project):
        raise ValueError("La couche utilise une autre connexion PostgreSQL.")
    return str(UUID(str(prestation_id)))


def dispatch_map_edit(iface, layer_id, prestation_id):
    """Entry point for the three explicit prestations_carte QGIS actions."""
    try:
        plugin = _active_plugin
        if plugin is None or plugin.iface is not iface or plugin._unloading:
            raise ValueError("Le plugin GEMAPI-App n'est pas actif.")
        uid = resolve_map_prestation(QgsProject.instance(), layer_id, prestation_id)
        return plugin.open_prestation(uid, origin="map")
    except Exception as error:
        if hasattr(iface, "messageBar"):
            message = (str(error) if isinstance(error, ValueError) else
                       "Impossible d'ouvrir la prestation. Vérifiez la connexion PostgreSQL.")
            iface.messageBar().pushMessage("GEMAPI-App", message,
                                           level=Qgis.Warning, duration=12)
        return None


TOOLBAR_NAME = "SIGMilieuxToolbar"

class SigMilieuxUX(QObject):
    @profiled("plugin.init")
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.action = None
        self.toolbar = None
        self.menu_action = None
        self.report_action = None
        self.digue_action = None
        self.help_dock = None
        self.home = None
        self._home_positioner = None
        self.manage = None
        self.creation = None
        self.assistant = None
        self.report = None
        self._creation_origin = None
        self._unloading = False
        self._map_after_save = False
        self._switching_windows = False
        self._last_functional = None
        self.session = SessionData()

    @profiled("plugin.init_gui")
    def initGui(self):
        global _active_plugin
        if self.action is not None:
            return
        _active_plugin = self
        metadata = Path(__file__).with_name("metadata.txt").read_text(encoding="utf-8")
        version = next((line.partition("=")[2].strip() for line in metadata.splitlines()
                        if line.startswith("version=")), "inconnue")
        QgsMessageLog.logMessage(
            f"Plugin chargé : version {version} ; chemin {Path(__file__).resolve()}",
            "GEMAPI-App", Qgis.Info)
        self.action = QAction(QIcon(str(Path(__file__).parent / "icons" / "icon.png")),
                              "", self.iface.mainWindow())
        self.action.setToolTip("GEMAPI-App")
        self.action.setCheckable(True)
        self.action.triggered.connect(self._toggle_interface)
        self.menu_action = QAction("GEMAPI-App", self.iface.mainWindow())
        self.menu_action.triggered.connect(self.show_dialog)
        self.iface.addPluginToMenu("GEMAPI-App", self.menu_action)
        self.report_action = QAction("Créer un rapport", self.iface.mainWindow())
        self.report_action.triggered.connect(self.show_report)
        self.iface.addPluginToMenu("GEMAPI-App", self.report_action)
        self.digue_action = QAction("Créer une digue…", self.iface.mainWindow())
        self.digue_action.triggered.connect(self.create_digue)
        self.iface.addPluginToMenu("GEMAPI-App", self.digue_action)
        window = self.iface.mainWindow()
        self.toolbar = window.findChild(QToolBar, TOOLBAR_NAME)
        if self.toolbar is None:
            self.toolbar = QToolBar("SIG-Milieux", window)
            self.toolbar.setObjectName(TOOLBAR_NAME)
            window.addToolBar(self.toolbar)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.toolbar.addAction(self.action)
        self.toolbar.show()

    def unload(self):
        global _active_plugin
        if self._unloading:
            return
        self._unloading = True
        if self.assistant is not None:
            self.assistant.cancel_request()
            self.assistant.close()
            self.assistant.deleteLater()
            self.assistant = None
        if _active_plugin is self:
            _active_plugin = None
        from sys import modules
        section_actions = modules.get(__package__ + ".ah_sections")
        if section_actions is not None:
            section_actions.close_dialogs()
        digue_actions = modules.get(__package__ + ".digues")
        if digue_actions is not None:
            digue_actions.close_dialogs()
        if self.creation is not None:
            self.creation.shutdown()
            self.creation = None
        if self.report is not None:
            self.report.shutdown()
            self.report = None
        if self.manage is not None:
            self.manage.close()
            self.manage = None
        if self.home is not None:
            self.home.close()
            self.home.deleteLater()
            self.home = None
        if self.help_dock is not None:
            self.iface.mainWindow().removeDockWidget(self.help_dock)
            self.help_dock.close()
            self.help_dock.deleteLater()
            self.help_dock = None
        if self.report_action:
            self.iface.removePluginMenu("GEMAPI-App", self.report_action)
        if self.digue_action:
            self.iface.removePluginMenu("GEMAPI-App", self.digue_action)
        if self.menu_action:
            self.iface.removePluginMenu("GEMAPI-App", self.menu_action)
        if self.action:
            self.toolbar.removeAction(self.action)
            self.action.deleteLater()
            self.action = None
        if self.toolbar is not None:
            if not self.toolbar.actions():
                self.iface.mainWindow().removeToolBar(self.toolbar)
                self.toolbar.setParent(None)
                self.toolbar.deleteLater()
            self.toolbar = None
        if self.session is not None:
            self.session.close()

    def create_digue(self, *_):
        from .digues import create
        return create(self.iface)

    @profiled("plugin.open_home")
    def show_dialog(self, *_):
        for dialog in (() if self._map_after_save else (self.report, self.creation, self.manage)):
            if dialog is not None and dialog.isVisible() and not getattr(dialog, "_save_completed", False):
                if dialog is self.creation:
                    dialog.cancel_capture()
                self._show_functional(dialog)
                return
        if self.home is None:
            self.home = uic.loadUi(str(Path(__file__).with_name("home.ui")))
            for button in (self.home.createButton, self.home.manageButton,
                           self.home.reportButton, self.home.helpButton):
                button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            self._home_positioner = CompactHomePositioner(self.home, self.iface.mapCanvas(), self.iface.mainWindow())
            self.home.createButton.clicked.connect(self.show_creation)
            self.home.manageButton.clicked.connect(self.show_management)
            self.home.reportButton.clicked.connect(self.show_report)
            self.home.helpButton.clicked.connect(self.show_help)
            self.home.assistantButton.clicked.connect(self.show_assistant)
        try:
            self.home.createButton.setVisible(
                prestation_permissions(self.session.connection())["insert"])
        except Exception:
            self.home.createButton.setVisible(False)
        self._show_home()

    def _functional_windows(self):
        return (self.home, self.creation, self.manage, self.report, self.assistant, self.help_dock)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Show, QEvent.Hide, QEvent.Close, QEvent.WindowStateChange):
            QTimer.singleShot(0, self._sync_action_state)
        return False

    def _sync_action_state(self):
        if self.action is not None:
            self.action.setChecked(not self._unloading and any(
                window is not None and window.isVisible() for window in self._functional_windows()))

    def _toggle_interface(self, checked):
        if checked:
            previous = self._last_functional
            if (previous is not None and previous in self._functional_windows() and
                    not getattr(previous, "_save_completed", False)):
                self._show_functional(previous, activate=previous is not self.home)
                if previous is self.home:
                    self._home_positioner.reposition()
            else:
                self.show_dialog()
        else:
            self._switching_windows = True
            try:
                for window in self._functional_windows():
                    if window is not None:
                        window.hide()
            finally:
                self._switching_windows = False
        self._sync_action_state()

    def _show_functional(self, window, activate=True):
        if window is self.creation and self.assistant is not None:
            window.addAction(self.assistant.new_action)
        window.installEventFilter(self)
        self._last_functional = window
        self._switching_windows = True
        try:
            for other in self._functional_windows():
                if other is not None and other is not window:
                    other.hide()
            window.show()
            window.raise_()
            if activate:
                window.activateWindow()
        finally:
            self._switching_windows = False
        self._sync_action_state()

    def _show_home(self):
        self._map_after_save = False
        self._show_functional(self.home, activate=False)
        self._home_positioner.reposition()

    def _prestation_saved(self, _uid):
        # The form refreshes its data and focuses the map after this synchronous hide.
        if self._creation_origin == "assistant" and self.assistant is not None:
            self.assistant.prestation_saved()
        self._switching_windows = True
        try:
            for window in self._functional_windows():
                if window is not None:
                    window.hide()
        finally:
            self._switching_windows = False
        self._map_after_save = True
        QTimer.singleShot(0, self._home_after_save)

    def _home_after_save(self):
        if self._unloading or not self._map_after_save:
            return
        if self.home is None:
            self.show_dialog()
        else:
            self._show_home()
        self._map_after_save = True

    def _dispose_creation(self):
        """Release a saved form, or a form whose abandonment has been confirmed."""
        old, self.creation = self.creation, None
        old.finished.disconnect(self._creation_closed)
        old.shutdown()
        old.deleteLater()

    def show_assistant(self, *_):
        if self.assistant is None:
            from .assistant_dialog import AssistantDialog
            self.assistant = AssistantDialog(self.session, self.open_creation_draft, self.iface.mainWindow(),
                                             before_new=self._authorize_new_assistant_prestation)
            self.assistant.finished.connect(self._assistant_closed)
        self._map_after_save = False
        self._show_functional(self.assistant)

    def _authorize_new_assistant_prestation(self, pending):
        form = self.creation
        form_dirty = (form is not None and not form._save_completed and form.mode in ("create", "edit") and
                      form._snapshot() != form._saved_snapshot)
        if form_dirty:
            if not form._confirm_abandon():
                return False
        elif pending and not self.assistant.confirm_new_prestation():
            return False
        if form is not None:
            self._dispose_creation()
            self._creation_origin = None
        self._map_after_save = False
        self._show_functional(self.assistant)
        return True

    def _assistant_closed(self, _result):
        if not self._unloading:
            if self.home is None:
                self.show_dialog()
            else:
                self._show_home()

    def open_creation_draft(self, values, associations):
        if not prestation_permissions(self.session.connection())["insert"]:
            raise ValueError("Droit INSERT absent sur gemapi.prestations.")
        if self.creation is not None and self.creation._save_completed:
            if self.creation._saved_scope_generation != self.session.scope_generation:
                self._dispose_creation()
        if self.creation is not None and not self.creation._save_completed:
            raise ValueError("Fermez le formulaire déjà ouvert avant d'ouvrir le brouillon IA.")
        dialog = self.creation
        fresh = dialog is None
        if fresh:
            dialog = CreationDialog(self.iface, self.iface.mainWindow(), session=self.session)
        try:
            dialog.mode = "create"
            dialog.load_draft(values, associations)
        except Exception:
            if fresh:
                dialog.deleteLater()
            raise
        if fresh:
            self.creation = dialog
            dialog.finished.connect(self._creation_closed)
            dialog.saved.connect(self._prestation_saved)
        self._creation_origin = "assistant"
        dialog.backButton.setText("Retour à l'assistant IA")
        self._map_after_save = False
        self._show_functional(dialog)
        return dialog

    @profiled("plugin.open_help")
    def show_help(self, *_):
        if self.help_dock is None:
            dock = QDockWidget("Aide — GEMAPI-App", self.iface.mainWindow())
            dock.setObjectName("gemapi_app_help")
            content = QTextBrowser(dock)
            content.setOpenExternalLinks(True)
            content.setMarkdown(Path(__file__).with_name("help.md").read_text(encoding="utf-8"))
            dock.setWidget(content)
            self.iface.mainWindow().addDockWidget(Qt.RightDockWidgetArea, dock)
            self.help_dock = dock
            dock.visibilityChanged.connect(self._help_visibility_changed)
        self._show_functional(self.help_dock)

    def _help_visibility_changed(self, visible):
        if not visible and not self._switching_windows and not self._unloading and self.home is not None:
            self._show_home()

    @profiled("plugin.open_manager")
    def show_management(self, *_):
        self._map_after_save = False
        if self.home is not None:
            self.home.hide()
        if self.manage is not None:
            self.manage.refresh()
            self._show_functional(self.manage)
            return
        self.manage = ManageDialog(parent=self.iface.mainWindow(), session=self.session)
        self.manage.newButton.clicked.connect(self.show_creation)
        self.manage.openButton.clicked.connect(self.show_existing)
        self.manage.backButton.clicked.connect(self.manage.reject)
        self.manage.finished.connect(self._management_closed)
        if self.home is not None:
            self.home.hide()
        self._show_functional(self.manage)

    def _management_closed(self, _result):
        closed, self.manage = self.manage, None
        if closed is not None:
            closed.deleteLater()
        if self.home is not None and not self._unloading:
            self._show_home()

    @profiled("plugin.open_creation")
    def show_creation(self, *_):
        try:
            if not prestation_permissions(self.session.connection())["insert"]:
                raise ValueError("Droit INSERT absent sur gemapi.prestations.")
        except Exception as error:
            if hasattr(self.iface, "messageBar"):
                message = str(error) if isinstance(error, ValueError) else "Connexion PostgreSQL indisponible."
                self.iface.messageBar().pushMessage("GEMAPI-App", message, level=Qgis.Warning, duration=12)
            return
        origin = "manager" if self.manage is not None and self.manage.isVisible() else "home"
        if self.creation is not None and self.creation._save_completed:
            if self.creation._saved_scope_generation != self.session.scope_generation:
                self._dispose_creation()
            else:
                self.creation.mode = "create"
                self.creation._reset()
                self.creation._filter_objects()
                self.creation._set_editable(True)
                self.creation.statusLabel.clear()
                self._creation_origin = origin
        if self.creation is None:
            self.creation = CreationDialog(self.iface, self.iface.mainWindow(), session=self.session)
            self._creation_origin = origin
            self.creation.finished.connect(self._creation_closed)
            self.creation.saved.connect(self._prestation_saved)
        self._map_after_save = False
        self._show_functional(self.creation)

    @profiled("plugin.open_existing")
    def show_existing(self, *_):
        if self.manage is None or not self.manage.selected_id():
            return
        try:
            return self.open_prestation(self.manage.selected_id(), origin="manager")
        except Exception as error:
            self.manage.statusLabel.setText(
                str(error) if isinstance(error, ValueError) else
                "Impossible d'ouvrir la prestation. Vérifiez la connexion PostgreSQL.")
            return None

    @profiled("plugin.open_prestation")
    def open_prestation(self, prestation_id, origin="manager"):
        uid = str(UUID(str(prestation_id)))
        connection = self.session.connection()
        rights = prestation_permissions(connection)
        if not rights["select"]:
            raise ValueError("Droit SELECT absent sur gemapi.prestations.")
        if origin == "map" and not rights["update"]:
            raise ValueError("Modification non autorisée. Utilisez le formulaire QGIS pour consulter la prestation.")
        mode = "edit" if rights["update"] else "view"
        if self.creation is not None and self.creation._save_completed:
            if self.creation._saved_scope_generation == self.session.scope_generation:
                self.creation.mode = mode
                try:
                    self.creation.load_existing(connection, uid)
                except Exception:
                    self.creation._save_completed = True
                    raise ValueError("Impossible de recharger la prestation ; la fiche périmée reste masquée.") from None
                self._creation_origin = origin
                self.creation.backButton.setText("Retour à QGIS" if origin == "map" else "Annuler et revenir à la gestion")
                self._map_after_save = False
                if self.home is not None:
                    self.home.hide()
                if self.manage is not None:
                    self.manage.hide()
                self._show_functional(self.creation)
                return self.creation
            self._dispose_creation()
        if self.creation is not None:
            if self.creation.current_prestation_id == uid:
                if self.creation.mode == "edit" and not rights["update"]:
                    raise ValueError("Droit UPDATE absent ; rouvrez la fiche en consultation.")
                self._show_functional(self.creation)
                return self.creation
            raise ValueError("Fermez la fiche de prestation ouverte avant d'en modifier une autre.")
        mode = "edit" if rights["update"] else "view"
        dialog = CreationDialog(self.iface, self.iface.mainWindow(), mode=mode,
                                session=self.session)
        try:
            dialog.load_existing(connection, uid)
        except Exception:
            QgsMessageLog.logMessage(
                f"Ouverture de la prestation :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Critical)
            dialog.deleteLater()
            message = "Impossible de charger cette prestation ou ses rattachements. Vérifiez les droits PostgreSQL."
            if origin == "manager" and self.manage is not None:
                self.manage.statusLabel.setText(message)
            raise ValueError(message)
        self.creation = dialog
        self._creation_origin = origin
        dialog.finished.connect(self._creation_closed)
        dialog.saved.connect(self._prestation_saved)
        self._map_after_save = False
        if self.manage is not None:
            self.manage.hide()
        if self.home is not None:
            self.home.hide()
        if origin == "map":
            dialog.backButton.setText("Retour à QGIS")
        self._show_functional(dialog)
        return dialog

    @profiled("plugin.return_manager")
    def _creation_closed(self, _result):
        closed, self.creation = self.creation, None
        origin, self._creation_origin = self._creation_origin, None
        save_completed = bool(closed and getattr(closed, "_save_completed", False))
        if closed is not None:
            closed.deleteLater()
        if self._unloading or save_completed:
            return
        if origin == "assistant" and self.assistant is not None:
            self.show_assistant()
            return
        if origin == "map":
            if self.home is not None:
                self._show_home()
            return
        if origin == "manager" and self.manage is not None:
            self.manage.refresh()
            self._show_functional(self.manage)
        elif self.home is not None:
            self._show_home()

    @profiled("plugin.open_report")
    def show_report(self, *_):
        if self.report is not None:
            self._show_functional(self.report)
            return
        from .report_dialog import ReportDialog
        self.report = ReportDialog(self.iface, self.iface.mainWindow(), session=self.session)
        if self.home is not None:
            self.home.hide()
        self.report.finished.connect(self._report_closed)
        self._show_functional(self.report)

    def _report_closed(self, _result):
        closed, self.report = self.report, None
        if closed is not None:
            closed.deleteLater()
        if self.home is not None and not self._unloading:
            self._show_home()
