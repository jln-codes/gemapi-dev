"""Creation and editing screen for prestations with conditional geometry."""

import os
import traceback
from uuid import UUID
from pathlib import Path

from qgis.PyQt import uic
from qgis.PyQt.QtCore import QDate, QEvent, QSignalBlocker, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QImageReader, QPixmap
from qgis.PyQt.QtWidgets import (QDialog, QFileDialog, QGroupBox, QHBoxLayout, QToolButton,
                                QLabel, QLayout, QListWidgetItem, QMessageBox,
                                QSizePolicy, QVBoxLayout)
from qgis.core import (QgsApplication, QgsDataSourceUri, QgsGeometry, QgsMessageLog,
                       QgsProject, QgsVectorLayer, QgsWkbTypes, Qgis)
from qgis.gui import QgsMapTool


class PatrimonyPointTool(QgsMapTool):
    """Choose a temporary reference point for the common metric search."""

    def __init__(self, canvas, callback, stopped, cancelled):
        super().__init__(canvas)
        self._callback = callback
        self._stopped = stopped
        self._cancelled = cancelled

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._callback(event.mapPoint(), self)

    def deactivate(self):
        super().deactivate()
        self._stopped(self)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cancelled(self)
        else:
            super().keyPressEvent(event)

from .digitize import (GeometryCapture, KINDS, append_geometry_part, geometry_from_vertices,
                       geometry_part_count, remove_last_geometry_part)
from .hydro_groups import build_options, search_key
from .watercourse_labels import section_label_sql, location_label
from .patrimony_spatial import candidate_query, resolve_candidates
from .record import SITE_CHILDREN, TARGETS, CATEGORIES, LINKS, POSITIONS, RIVES, build_insert, build_update, validate
from .photo_paths import reference_for, resolve_photo
from .management import load_prestation
from .perf import measure, profiled
from .session_data import CACHE_ENABLED, connection_from_project
from .window_geometry import initialize_window
from .prestation_focus import focus_prestation


def _patrimony_trace(message):
    """Opt-in diagnosis without logging coordinates, UUIDs or connection details."""
    if os.environ.get("SIG_PRESTATIONS_PATRIMOINE_DEBUG") == "1":
        QgsMessageLog.logMessage(message, "GEMAPI-App — patrimoine", Qgis.Info)


def database_error_message(error):
    """Translate common PostgreSQL failures without exposing connection details."""
    message = str(error).lower()
    if "foreign key" in message or "clé étrangère" in message:
        return "Un objet ou référentiel sélectionné n'existe plus. Actualisez les couches puis réessayez."
    if "permission denied" in message or "droit" in message:
        return "Droits PostgreSQL insuffisants pour créer la prestation ou ses associations."
    if "check constraint" in message or "contrainte de vérification" in message:
        return "Une valeur ne respecte pas une contrainte PostgreSQL. Vérifiez les dates et la quantité."
    if "duplicate key" in message or "clé dupliquée" in message:
        return "Une association existe déjà. Actualisez les couches avant de réessayer."
    return "PostgreSQL a refusé l'enregistrement. Vérifiez les droits et les contraintes de la base."



class CreationDialog(QDialog):
    saved = pyqtSignal(str)

    @profiled("form.construct")
    def __init__(self, iface, parent=None, mode="create", session=None):
        super().__init__(parent)
        uic.loadUi(str(Path(__file__).with_name("create.ui")), self)
        self._arrange_location()
        self._arrange_details()
        for index, stretch in enumerate((2, 1, 1)):
            self.amountLayout.setStretch(index, stretch)
        for index in range(4):
            self.detailsLayout.setColumnStretch(index, 1)
        for index, stretch in enumerate((60, 40)):
            self.associationControls.setStretch(index, stretch)
        for index, stretch in enumerate((75, 25)):
            self.associationLists.setStretch(index, stretch)
        for index, button in enumerate((self.saveButton, self.reloadButton, self.backButton)):
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.bottomActions.setStretch(index, 5 if index != 1 else 3)
        self.iface = iface
        self.session = session
        self.verticalLayout.setSizeConstraint(QLayout.SetNoConstraint)
        initialize_window(self, "prestation", parent)
        self.mode = mode
        self._assistant_draft = False
        self._save_completed = False
        self.saved_prestation_id = None
        self._saved_scope_generation = None
        self.current_prestation_id = None
        self._loaded_version = None
        self._edit_connection = None
        self._edit_scope_generation = None
        self._edit_saved = False
        self._edit_refresh_warning = ""
        self.objects = []
        self._object_load_failures = ()
        self._active_patrimony_family = None
        self._pending_location_values = None
        self._object_layers = {}
        self._spatial_cache = None
        self._location_details = {}
        self.geometry = None  # Always in EPSG:3950 after capture.
        self._map_reference = None  # Temporary EPSG:3950 point; never saved as geom.
        self.geometry_kind = None
        self._capture = None
        self._map_tool = None
        self._previous_map_tool = None
        self._capture_append = False
        self._capture_building_before = False
        self._geometry_building = False
        self._drawing_snapshot = None
        self._disposing = False
        self._close_confirmed = False
        self._saved_snapshot = None
        self._empty_date = QDate(1900, 1, 1)
        for widget in (self.debutDate, self.finDate):
            widget.setMinimumDate(self._empty_date)
            widget.setSpecialValueText("— Choisir —")
            widget.setDate(self._empty_date)
        self.typeCombo.addItem("— Choisir un type —", "")
        self.prestataireCombo.addItem("— Choisir un prestataire —", "")
        self.statutCombo.addItem("— Choisir un statut —", "")
        self.technicienCombo.addItem("— Choisir un technicien responsable —", "")
        for position in POSITIONS:
            self.positionCombo.addItem(position, position)
        for rive in RIVES:
            self.riveCombo.addItem(rive, rive)
        self._load_types()
        self._load_reference("prestataires", self.prestataireCombo, "nom")
        self._load_statuses()
        self._load_technicians()
        self.searchEdit.textChanged.connect(self._filter_objects)
        self.mapSelectButton.clicked.connect(self._toggle_map_selection)
        self.nearbyOnly.toggled.connect(self._filter_objects)
        self.distanceSpin.valueChanged.connect(self._filter_objects)
        self.distanceUnitCombo.addItem("m", 1)
        self.distanceUnitCombo.addItem("km", 1000)
        self.distanceUnitCombo.currentIndexChanged.connect(self._filter_objects)
        self.objectList.itemSelectionChanged.connect(self._update_association_actions)
        self.selectedList.itemSelectionChanged.connect(self._update_association_actions)
        self.addButton.clicked.connect(self._add_objects)
        self.removeButton.clicked.connect(self._remove_objects)
        self.locateButton.clicked.connect(self._locate)
        self.saveButton.clicked.connect(self._save)
        self.reloadButton.clicked.connect(self._reload_existing)
        self.backButton.clicked.connect(self.reject)
        self.pointButton.clicked.connect(lambda: self._begin_capture("point"))
        self.lineButton.clicked.connect(lambda: self._begin_capture("line"))
        self.polygonButton.clicked.connect(lambda: self._begin_capture("polygon"))
        self.removeGeometryButton.clicked.connect(self._remove_geometry)
        self.addPartButton.clicked.connect(self._add_part)
        self.removePartButton.clicked.connect(self._remove_last_part)
        self.finishGeometryButton.clicked.connect(self._finish_geometry)
        self.photo_references = ["", ""]
        self._photo_pixmaps = [QPixmap(), QPixmap()]
        self._photo_paths = [None, None]
        for number in (1, 2):
            actions = getattr(self, f"photo{number}Actions")
            actions.setStretch(0, 3)
            actions.setStretch(1, 1)
            for button in (getattr(self, f"photo{number}Button"),
                           getattr(self, f"photo{number}Remove")):
                button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            getattr(self, f"photo{number}Preview").installEventFilter(self)
            getattr(self, f"photo{number}Button").clicked.connect(
                lambda _, index=number-1: self._choose_photo(index))
            getattr(self, f"photo{number}Remove").clicked.connect(
                lambda _, index=number-1: self._remove_photo(index))
            self._update_photo(index=number-1)
        self._update_geometry_status()
        self._load_objects()
        self._update_position_state()
        self._update_association_actions()
        self._saved_snapshot = self._snapshot()
        self._set_editable(mode != "view")

    def _arrange_location(self):
        """Move existing controls into two columns without replacing their widgets."""
        self.formLayout.removeWidget(self.geometryGroup)
        self.formLayout.removeWidget(self.associationGroup)
        self.multipartActions.removeWidget(self.finishGeometryButton)
        self.geometryLayout.insertWidget(3, self.finishGeometryButton)
        self.associationControls.removeWidget(self.searchEdit)
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        search_row.addWidget(self.searchEdit, 1)
        self.mapSelectButton = QToolButton(self.associationGroup)
        self.mapSelectButton.setObjectName("mapSelectButton")
        self.mapSelectButton.setIcon(QgsApplication.getThemeIcon("/mActionIdentify.svg"))
        self.mapSelectButton.setCheckable(True)
        self.mapSelectButton.setToolTip("Activer la sélection sur la carte")
        self.mapSelectButton.setStyleSheet(
            "QToolButton#mapSelectButton:checked { background-color: rgba(55, 126, 184, 70); }")
        self.mapSelectButton.toggled.connect(
            lambda active: self.mapSelectButton.setToolTip(
                "Désactiver la sélection sur la carte" if active
                else "Activer la sélection sur la carte"))
        search_row.addWidget(self.mapSelectButton)
        self.associationLayout.insertLayout(0, search_row)
        self.locationGroup = QGroupBox("Localisation et rattachement patrimonial", self.formContents)
        contents = QVBoxLayout(self.locationGroup)
        columns = QHBoxLayout()
        columns.addWidget(self.geometryGroup, 1)
        columns.addWidget(self.associationGroup, 1)
        contents.addLayout(columns)
        self.spatialWarning = QLabel(self.locationGroup)
        self.spatialWarning.setWordWrap(True)
        self.spatialWarning.setStyleSheet("color: #8a5300")
        self.spatialWarning.hide()
        contents.addWidget(self.spatialWarning)
        selected_item = self.associationLists.takeAt(2)
        selected_column = selected_item.layout()
        self.associationActions.removeWidget(self.removeButton)
        selected_column.addWidget(self.removeButton)
        self.selectedList.setMinimumHeight(85)
        self.selectedList.setMaximumHeight(115)
        self.selectedLabel.setText("Rattachements retenus — au moins un obligatoire")
        contents.addLayout(selected_column)
        self.geometryGroup.setTitle("Géométrie de la prestation")
        self.associationGroup.setTitle("Patrimoine concerné")
        self.geometryStatusLabel.setWordWrap(True)
        self.geometryHintLabel.setToolTip(self.geometryHintLabel.text())
        self.geometryHintLabel.setText(
            "Sur la carte : clic pour un point, clic droit pour terminer une ligne "
            "ou un polygone. Ajoutez une partie pour une géométrie multiple ; "
            "Échap annule le dessin en cours.")
        geometry_help = QLabel("La géométrie propre est obligatoire pour les cours d'eau, les digues, les sections AH et les sites terrestres, facultative pour les stockages et ouvrages AH et pour les enfants des sites. Sans dessin, la carte localise la prestation sur son patrimoine.")
        geometry_help.setWordWrap(True)
        self.geometryLayout.insertWidget(0, geometry_help)
        patrimony_help = QLabel("Sélectionnez directement un patrimoine ou dessinez une géométrie"
                               " pour rechercher les objets à proximité.")
        patrimony_help.setWordWrap(True)
        self.associationLayout.insertWidget(0, patrimony_help)
        self.formLayout.insertWidget(0, self.locationGroup)

    def _arrange_details(self):
        """Place existing business fields in two compact four-column rows."""
        grid = self.detailsLayout
        while grid.count():
            grid.takeAt(0)
        grid.setVerticalSpacing(4)
        principal = QLabel("Informations principales", self.detailsGroup)
        principal.setStyleSheet("font-weight: bold; color: #536271")
        optional = QLabel("Informations facultatives", self.detailsGroup)
        optional.setStyleSheet("font-weight: bold; color: #536271")
        grid.addWidget(principal, 0, 0, 1, 4)
        for column, (label, widget) in enumerate((
                (self.typeLabel, self.typeCombo),
                (self.prestataireLabel, self.prestataireCombo),
                (self.statutLabel, self.statutCombo),
                (self.technicienLabel, self.technicienCombo))):
            grid.addWidget(label, 1, column)
            grid.addWidget(widget, 2, column)
        for column, (label, widget) in enumerate((
                (self.debutLabel, self.debutDate),
                (self.finLabel, self.finDate),
                (self.positionLabel, self.positionCombo),
                (self.riveLabel, self.riveCombo))):
            grid.addWidget(label, 3, column)
            grid.addWidget(widget, 4, column)
        grid.addWidget(optional, 5, 0, 1, 4)
        grid.addLayout(self.amountLayout, 6, 0, 1, 4)
        grid.addWidget(self.descriptionLabel, 7, 0, 1, 4)
        grid.addWidget(self.descriptionEdit, 8, 0, 1, 4)
        self.descriptionEdit.setMaximumHeight(70)
        for combo in (self.typeCombo, self.prestataireCombo, self.statutCombo,
                      self.technicienCombo, self.positionCombo, self.riveCombo):
            combo.currentTextChanged.connect(combo.setToolTip)

    def _set_editable(self, editable):
        """Use the same fields and geometry capture for creation and edition."""
        for group in (self.detailsGroup, self.locationGroup, self.photosGroup):
            group.setEnabled(editable)
        self.saveButton.setVisible(editable)
        self.reloadButton.setVisible(self.mode == "edit")
        self.saveButton.setText("Enregistrer les modifications" if self.mode == "edit"
                                else "Enregistrer la prestation")
        self.backButton.setText("Annuler et revenir à la gestion" if self.mode == "edit"
                                else "Retour à la gestion")
        self.setWindowTitle(("Créer" if self.mode == "create" else
                             "Modifier" if self.mode == "edit" else "Consulter") +
                            " une prestation — GEMAPI-App")
        if not editable:
            self.statusLabel.setText("Consultation seule.")
        self._update_save_state()

    def _choose_photo(self, index):
        selected, _ = QFileDialog.getOpenFileName(
            self, f"Choisir la photo {index + 1}", "",
            "Images (*.jpg *.jpeg *.png *.webp *.tif *.tiff);;Tous les fichiers (*)")
        if not selected:
            return
        reader = QImageReader(selected)
        if not reader.canRead():
            self.statusLabel.setText("Le fichier choisi n'est pas une image lisible.")
            return
        self.photo_references[index] = reference_for(selected)
        self._update_photo(index)

    def _remove_photo(self, index):
        self.photo_references[index] = ""
        self._update_photo(index)

    def _update_photo(self, index):
        label = getattr(self, f"photo{index + 1}Preview")
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        path = resolve_photo(self.photo_references[index])
        pixmap = QPixmap(str(path)) if path else QPixmap()
        self._photo_pixmaps[index] = pixmap
        self._photo_paths[index] = path if not pixmap.isNull() else None
        if pixmap.isNull():
            label.clear()
            label.setText("Photographie indisponible" if self.photo_references[index]
                          else "Aucune photographie")
            label.setToolTip(self.photo_references[index])
        else:
            self._render_photo_preview(index)
            label.setToolTip(self.photo_references[index])
        getattr(self, f"photo{index + 1}Button").setText(
            "Remplacer photo" if self.photo_references[index] else "Choisir photo")
        getattr(self, f"photo{index + 1}Remove").setEnabled(bool(self.photo_references[index]))

    def _render_photo_preview(self, index):
        label = getattr(self, f"photo{index + 1}Preview")
        pixmap = self._photo_pixmaps[index]
        if pixmap.isNull():
            return
        area = label.contentsRect().size()
        if area.width() < 2 or area.height() < 2:
            return
        target = pixmap.scaled(min(area.width(), pixmap.width()),
                               min(area.height(), pixmap.height()),
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(target)

    def eventFilter(self, watched, event):
        for index in (0, 1):
            if watched is getattr(self, f"photo{index + 1}Preview", None):
                if event.type() == QEvent.Resize:
                    self._render_photo_preview(index)
                elif event.type() == QEvent.MouseButtonDblClick and self._photo_paths[index]:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._photo_paths[index])))
                    return True
                break
        return super().eventFilter(watched, event)

    def _geometry_error(self):
        geometry = self.geometry
        if geometry is None or geometry.isNull() or geometry.isEmpty():
            families = {table for table, _ in self._associations()}
            return ("Géométrie obligatoire pour un cours d'eau, une digue, une section AH, un tronçon ou un site terrestre."
                    if families.intersection(("cours_eau", "digues", "ah_sections", "troncons", "sites_terrestres")) else None)
        allowed = (QgsWkbTypes.Point, QgsWkbTypes.MultiPoint,
                   QgsWkbTypes.LineString, QgsWkbTypes.MultiLineString,
                   QgsWkbTypes.Polygon, QgsWkbTypes.MultiPolygon)
        if geometry.wkbType() not in allowed or not geometry.isGeosValid():
            return "Géométrie invalide ou de type non autorisé."
        if geometry.type() == QgsWkbTypes.LineGeometry and geometry.length() <= 0:
            return "La ligne doit avoir une longueur positive."
        if geometry.type() == QgsWkbTypes.PolygonGeometry and geometry.area() <= 0:
            return "Le polygone doit avoir une surface positive."
        return None

    def _update_save_state(self):
        valid = self._geometry_error() is None
        present = self.geometry is not None and not self.geometry.isNull() and not self.geometry.isEmpty()
        drawing = self._capture is not None
        editable = self.mode != "view"
        drawing_allowed = editable and not self._assistant_draft
        for button in (self.pointButton, self.lineButton, self.polygonButton):
            button.setEnabled(drawing_allowed and not drawing)
        self.saveButton.setEnabled(not self._save_completed and editable and valid and bool(self._associations())
                                   and not drawing and not self._geometry_building)
        self.addPartButton.setEnabled(drawing_allowed and present and valid and not drawing)
        self.removePartButton.setEnabled(editable and present and valid and not drawing and
                                         geometry_part_count(self.geometry) > 1)
        self.finishGeometryButton.setEnabled(editable and present and valid and not drawing and
                                             self._geometry_building)

    def _update_geometry_status(self, drawing=False):
        present = self.geometry is not None and not self.geometry.isNull() and not self.geometry.isEmpty()
        self._update_spatial_controls()
        if drawing:
            previous = (f" ; géométrie précédente conservée : {KINDS[self.geometry_kind]}"
                        if self.geometry is not None else "")
            self.geometryStatusLabel.setText("Numérisation d'une partie en cours" + previous)
        elif self._geometry_error():
            self.geometryStatusLabel.setText(self._geometry_error())
        elif not present and any(family in SITE_CHILDREN for family, _ in self._associations()):
            self.geometryStatusLabel.setText("Localisation : géométrie du patrimoine — aucune géométrie propre.")
        elif not present:
            self.geometryStatusLabel.setText("Aucune géométrie dessinée — facultative selon le patrimoine.")
        else:
            count = geometry_part_count(self.geometry)
            suffix = (" — construction en cours : terminez la géométrie"
                      if self._geometry_building else " — valide")
            self.geometryStatusLabel.setText(
                f"{KINDS[self.geometry_kind]} : {count} partie{'s' if count > 1 else ''} "
                f"(EPSG:3950){suffix}")
        self.removeGeometryButton.setEnabled(self.geometry is not None and not drawing)
        self._update_save_state()

    @profiled("form.qgis.begin_capture")
    def _begin_capture(self, kind, append=False):
        if self._assistant_draft:
            return
        if self._map_tool is not None:
            self._stop_map_selection()
        if self._capture is not None:
            return
        if append and self._geometry_error():
            self.statusLabel.setText("Dessinez d'abord une première partie valide.")
            return
        try:
            capture = GeometryCapture(self.iface.mapCanvas(), kind, self._capture_finished)
        except ValueError as error:
            self.statusLabel.setText(str(error))
            return
        self._drawing_snapshot = self._snapshot()
        self._capture_append = append
        self._capture_building_before = self._geometry_building
        if append:
            self._geometry_building = True
        self._capture = capture
        self._update_geometry_status(drawing=True)
        self.hide()
        capture.start()

    @profiled("form.qgis.capture_finished")
    def _capture_finished(self, geometry, message):
        kind = self._capture.kind if self._capture is not None else None
        append = self._capture_append
        self._capture = None
        self._capture_append = False
        self._drawing_snapshot = None
        if geometry is not None:
            try:
                candidate = (append_geometry_part(self.geometry, geometry) if append else
                             QgsGeometry(geometry))
            except ValueError as error:
                message = str(error)
                self._geometry_building = self._capture_building_before
            else:
                self.geometry = candidate
                self.geometry_kind = kind
                self._map_reference = None
                self._activate_distance()
                self._geometry_building = append
                if append:
                    message = (f"Partie {geometry_part_count(candidate)} ajoutée. "
                               "Ajoutez une partie ou terminez la géométrie.")
        else:
            self._geometry_building = self._capture_building_before
        self._update_geometry_status()
        self.statusLabel.setText(message)
        self._filter_objects()
        if not self._disposing:
            self.show()
            self.raise_()
            self.activateWindow()

    def cancel_capture(self, notify=True):
        capture = self._capture
        if capture is not None:
            capture.cancel(notify=notify)
            if self._capture is capture:
                self._capture = None

    @profiled("form.qgis.add_part")
    def _add_part(self, *_):
        if self.geometry_kind:
            self._begin_capture(self.geometry_kind, append=True)

    @profiled("form.qgis.remove_part")
    def _remove_last_part(self, *_):
        try:
            self.geometry = remove_last_geometry_part(self.geometry)
        except ValueError as error:
            self.statusLabel.setText(str(error))
            return
        self._map_reference = None
        self._activate_distance()
        self._update_geometry_status()
        self._filter_objects()
        self.statusLabel.setText("Dernière partie retirée du formulaire.")

    @profiled("form.qgis.finish_geometry")
    def _finish_geometry(self, *_):
        if self._geometry_error() or not self._geometry_building:
            return
        self._geometry_building = False
        self._update_geometry_status()
        self.statusLabel.setText("Géométrie complète et valide ; vous pouvez enregistrer la prestation.")

    def _remove_geometry(self):
        self.geometry = None
        self.geometry_kind = None
        self._geometry_building = False
        self._update_geometry_status()
        self._filter_objects()
        self.statusLabel.setText("Géométrie supprimée du formulaire ; aucune donnée PostgreSQL n'a été modifiée.")

    def shutdown(self):
        """Release canvas resources when QGIS unloads the plugin."""
        self._disposing = True
        self.cancel_capture(notify=False)
        self._stop_map_selection()
        self.close()

    def _toggle_map_selection(self, checked):
        if not checked:
            self._stop_map_selection()
            return
        if self._capture is not None:
            self.cancel_capture()
        canvas = self.iface.mapCanvas()
        self._previous_map_tool = canvas.mapTool()
        self._map_tool = PatrimonyPointTool(
            canvas, self._map_point_selected, self._map_deactivated, self._map_cancelled)
        self.hide()
        canvas.setMapTool(self._map_tool)
        if canvas.mapTool() is not self._map_tool:
            self._stop_map_selection()
            self.statusLabel.setText("L'outil de sélection cartographique est indisponible.")

    def _map_deactivated(self, tool):
        if tool is self._map_tool:
            self._stop_map_selection(restore=False)

    def _map_cancelled(self, tool):
        if tool is self._map_tool:
            self._stop_map_selection()

    def _stop_map_selection(self, restore=True, show_form=True):
        tool = self._map_tool
        if tool is None:
            return
        self._map_tool = None
        self.mapSelectButton.setChecked(False)
        canvas = self.iface.mapCanvas()
        if restore and canvas.mapTool() is tool:
            previous = self._previous_map_tool
            if previous is not None:
                canvas.setMapTool(previous)
            else:
                canvas.unsetMapTool(tool)
        self._previous_map_tool = None
        self._update_geometry_status()
        self.distanceSpin.setEnabled(True)
        self.distanceUnitCombo.setEnabled(True)
        self._filter_objects()
        if show_form and not self._disposing:
            self.show()

    def _map_point_selected(self, point, tool):
        if tool is not self._map_tool:
            return
        try:
            reference = geometry_from_vertices(
                "point", [point], self.iface.mapCanvas().mapSettings().destinationCrs())
            self._map_reference = reference
            self._activate_distance()
            self._update_spatial_controls()
            self.statusLabel.setText(
                "Recherche autour du dernier point cliqué — la géométrie de la prestation est conservée.")
            self._filter_objects()
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), "GEMAPI-App", Qgis.Warning)
            self.statusLabel.setText("Le point cliqué ne peut pas servir de référence spatiale.")
        finally:
            self.show()
            self.raise_()
            self.activateWindow()

    def _reference_geometry(self):
        return self._map_reference if self._map_reference is not None else self.geometry

    def _activate_distance(self):
        # Filter only after the new reference and all related controls are ready.
        blocker = QSignalBlocker(self.nearbyOnly)
        self.nearbyOnly.setChecked(True)
        del blocker
        self._spatial_cache = None

    def _update_spatial_controls(self):
        reference = self._reference_geometry()
        present = reference is not None and not reference.isNull() and not reference.isEmpty()
        self.nearbyOnly.setEnabled(present)
        self.nearbyOnly.setToolTip(
            "Distance depuis le dernier point cliqué." if self._map_reference is not None
            else "Distance depuis la géométrie complète de la prestation.")
        if not present:
            blocker = QSignalBlocker(self.nearbyOnly)
            self.nearbyOnly.setChecked(False)
            del blocker

    def _canonical_layer(self, table):
        for layer in QgsProject.instance().mapLayers().values():
            if not layer.isValid() or layer.providerType() != "postgres":
                continue
            source = QgsDataSourceUri(layer.source())
            if source.schema() == "gemapi" and source.table() == table:
                return layer
        return None

    @profiled("form.qgis.object_layer")
    def _object_layer(self, table):
        """Read the current PostgreSQL table, even if it is not in the project."""
        if self.session is not None and CACHE_ENABLED:
            return self.session.get(
                f"family.layer.{table}", lambda: self._create_object_layer(table), 0)
        if table in self._object_layers:
            return self._object_layers[table]
        layer = self._create_object_layer(table)
        self._object_layers[table] = layer
        return layer

    def _site_child_layers(self, table):
        # A PostgreSQL geometry column can contain all six simple/multi types.
        # Read each dimension even when the project's visible layers are filtered.
        target = TARGETS[table]
        anchor = (self._canonical_layer("prestations_fiches") or self._canonical_layer("prestations_carte")
                  or self._canonical_layer("prestations"))
        if anchor is None:
            raise ValueError("Connexion PostgreSQL des prestations indisponible.")
        connection = QgsDataSourceUri(anchor.source()).connectionInfo(False)
        available = {}
        for layer in QgsProject.instance().mapLayers().values():
            if not layer.isValid() or layer.providerType() != "postgres":
                continue
            uri = QgsDataSourceUri(layer.source())
            if (uri.schema() == "gemapi" and uri.table() == table and
                    uri.geometryColumn() == target.geometry and uri.connectionInfo(False) == connection):
                available[layer.subsetString(), layer.wkbType()] = layer
        layers = []
        for dimension, kind in enumerate((QgsWkbTypes.MultiPoint, QgsWkbTypes.MultiLineString,
                                          QgsWkbTypes.MultiPolygon)):
            subset = f"site_terrestre_id IS NOT NULL AND ST_Dimension({target.geometry})={dimension}"
            if (subset, kind) in available:
                layers.append(available[subset, kind])
                continue
            uri = QgsDataSourceUri(anchor.source())
            uri.setDataSource("gemapi", table, target.geometry, subset, "id")
            uri.setSrid("3950")
            uri.setWkbType(kind)
            layer = QgsVectorLayer(uri.uri(False), target.label, "postgres")
            if not layer.isValid():
                raise ValueError(f"Couche gemapi.{table} inaccessible.")
            layer.setReadOnly(True)
            layers.append(layer)
        return layers

    def _create_object_layer(self, table):
        source_table = {"digues": "digues_carte", "ah_sections": "ah_sections_carte"}.get(table, table)
        existing = self._canonical_layer(source_table)
        # Hydrographic catalogues use private providers: a later map filter must
        # not truncate them either. Sections include only admissible records.
        if existing is not None and table not in ("cours_eau", "ah_sections") and not existing.subsetString():
            return existing
        anchor = (self._canonical_layer("prestations") or self._canonical_layer("prestations_fiches")
                  or self._canonical_layer("prestations_carte"))
        if not anchor:
            return None
        uri = QgsDataSourceUri(anchor.source())
        uri.setDataSource("gemapi", source_table, "geom",
                          "etat_reference='CONFORME' AND cours_eau_qualifie IS TRUE AND signature_conforme IS TRUE"
                          if table == "ah_sections" else "", "id")
        uri.setSrid("3950")
        # A prestation project layer may be restricted to Point, LineString or
        # Polygon; do not carry that restriction into typed patrimonial tables.
        kind = {"ah_stockage": QgsWkbTypes.Polygon,
                "ah_ouvrage": QgsWkbTypes.LineString,
                "cours_eau": QgsWkbTypes.MultiLineString,
                "sites_terrestres": QgsWkbTypes.MultiPolygon,
                "digues": QgsWkbTypes.LineString,
                "ah_sections": QgsWkbTypes.LineString}.get(table)
        if kind is not None:
            uri.setWkbType(kind)
        layer = QgsVectorLayer(uri.uri(False), table, "postgres")
        if not layer.isValid():
            raise ValueError(f"La table PostgreSQL gemapi.{table} est inaccessible.")
        return layer

    def _load_types(self):
        try:
            def fetch():
                connection = self._connection()
                return (
                    tuple(connection.executeSql(
                        "SELECT id::text,libelle FROM gemapi.prestations_categorie "
                        "ORDER BY ordre,libelle,id")),
                    tuple(connection.executeSql(
                        "SELECT id::text,libelle,categorie_id::text,actif "
                        "FROM gemapi.types_prestations ORDER BY libelle,id")),
                )
            categories, types = (self.session.get("reference.type_hierarchy", fetch)
                                 if self.session is not None else fetch())
            self.typeCombo.set_reference(categories, types)
        except Exception:
            QgsMessageLog.logMessage(
                traceback.format_exc(), "GEMAPI-App", Qgis.Critical)
            self.statusLabel.setText("Référentiel des types et catégories indisponible.")

    @profiled("form.reference")
    def _load_reference(self, table, combo, label_field):
        try:
            if self.session is not None and table == "prestataires":
                rows = self.session.get(
                    "reference.providers",
                    lambda: tuple(self._connection().executeSql(
                        "SELECT id::text,nom,actif FROM gemapi.prestataires ORDER BY nom,id")))
                rows = (row[:2] for row in rows if row[2] in (True, 1, "t", "true"))
            else:
                query = (f"SELECT id::text,{label_field} FROM gemapi.{table} "
                         f"WHERE actif ORDER BY {label_field},id")
                rows = (self.session.get(
                    f"reference.{table}", lambda: tuple(self._connection().executeSql(query)))
                    if self.session is not None else self._connection().executeSql(query))
            for uid, label in rows:
                combo.addItem(str(label), str(uid))
        except Exception:
            QgsMessageLog.logMessage(
                f"Référentiel {table} :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Critical)
            self.statusLabel.setText(f"Référentiel gemapi.{table} indisponible.")

    @profiled("form.statuses")
    def _load_statuses(self):
        """Read the status reference through the prestation's database connection."""
        try:
            query = ("SELECT id::text, libelle FROM gemapi.statuts_prestations "
                     "ORDER BY CASE code WHEN 'PLANIFIE' THEN 1 WHEN 'EN_COURS' THEN 2 "
                     "WHEN 'TERMINE' THEN 3 WHEN 'ANNULE' THEN 4 END")
            rows = (self.session.get(
                "reference.statuses", lambda: tuple(self._connection().executeSql(query)))
                if self.session is not None else self._connection().executeSql(query))
            for uid, label in rows:
                self.statutCombo.addItem(str(label), str(uid))
        except Exception:
            QgsMessageLog.logMessage(
                f"Référentiel des statuts :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Critical)
            self.statusLabel.setText("Référentiel des statuts indisponible.")

    @profiled("form.technicians")
    def _load_technicians(self):
        try:
            query = ("SELECT id::text,concat_ws(' ',prenom,nom),actif "
                     "FROM gemapi.techniciens ORDER BY nom,prenom,id")
            rows = (self.session.get("reference.technicians",
                                     lambda: tuple(self._connection().executeSql(query)))
                    if self.session is not None else self._connection().executeSql(query))
            for uid, label, active in rows:
                if active in (True, 1, "t", "true"):
                    self.technicienCombo.addItem(str(label), str(uid))
        except Exception:
            QgsMessageLog.logMessage(
                f"Référentiel techniciens :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Critical)
            self.statusLabel.setText("Référentiel des techniciens indisponible.")

    @profiled("form.qgis.load_objects")
    def _load_objects(self, *_):
        self.objects = []
        self._spatial_cache = None
        self._hydro_excluded = set()

        def parent_names(table):
            layer = self._canonical_layer(table)
            if layer is not None:
                return {str(feature["id"]): str(feature["nom"])
                        for feature in layer.getFeatures()}
            rows = self._connection().executeSql(
                f"SELECT id::text,nom FROM gemapi.{table}")
            return {str(uid): str(name) for uid, name in rows}

        parents = {}
        for table in ("ah", "sites_terrestres"):
            try:
                parents[table] = parent_names(table)
            except Exception:
                parents[table] = {}
                QgsMessageLog.logMessage(
                    f"Noms parents {table} indisponibles :\n{traceback.format_exc()}",
                    "GEMAPI-App", Qgis.Warning)
        try:
            courses = {uid: option.label for option in self._hydro_options() for uid in option.ids}
        except Exception:
            courses = {}
        self._section_labels = {}
        try:
            rows = self._connection().executeSql(
                f"SELECT o.id::text,{section_label_sql()} AS label FROM gemapi.ah_sections o "
                "JOIN gemapi.cours_eau r ON r.id=o.cours_eau_id JOIN gemapi.ah a ON a.id=o.ah_id")
            self._section_labels = {str(uid): str(label) for uid, label in rows}
        except Exception:
            QgsMessageLog.logMessage("Libellés des sections AH indisponibles.", "GEMAPI-App", Qgis.Warning)
        self._location_details = {}
        failed = []
        for table in ("digues", "ah_sections", "cours_eau", "ah_stockage", "ah_ouvrage", "sites_terrestres", *SITE_CHILDREN):
            try:
                family_objects = []
                if table == "cours_eau":
                    layer = self._object_layer(table)
                    if layer is None:
                        raise ValueError("Chargez gemapi.prestations depuis PostgreSQL dans QGIS.")
                    for option in self._hydro_options():
                        for number, (uid, fid) in enumerate(zip(option.ids, option.fids), 1):
                            label = (f"{option.label} — tronçon {number}/{len(option.ids)}"
                                     if len(option.ids) > 1 else option.label)
                            family_objects.append((table, (uid,), label,
                                                   (fid,), option.search, layer))
                    self.objects.extend(family_objects)
                    _patrimony_trace(f"Chargement {table} : {len(family_objects)} objets")
                    continue
                layers = (self._site_child_layers(table) if table in SITE_CHILDREN
                          else [self._object_layer(table)])
                if not layers or any(layer is None for layer in layers):
                    raise ValueError(f"Couche gemapi.{table} indisponible.")
                for layer in layers:
                    for feature in layer.getFeatures():
                        if table == "ah_sections":
                            object_id = str(feature["id"])
                            label = self._section_labels[object_id]
                            self._location_details[table, object_id] = (
                                '\nSection AH conforme ; intervention avec géométrie propre.'
                                + '\nUUID section : ' + object_id
                                + '\nUUID cours d’eau : ' + str(feature['cours_eau_id'])
                                + '\nUUID AH parent : ' + str(feature['ah_id']))
                        elif table == "digues":
                            object_id = str(feature["digue_id"])
                            river = str(feature['riviere_id'])
                            name = str(feature['digue_nom'])
                            label = location_label(courses.get(river, str(feature['riviere_nom'])), (name,))
                            if 'rive' in feature.fields().names():
                                side = 'gauche' if str(feature['rive']) == 'G' else 'droite'
                                self._location_details[table, object_id] = '\nRive ' + side
                        else:
                            object_id = str(feature["id"])
                            name = feature[TARGETS[table].name]
                            label = str(name) if name else "Objet sans nom"
                            if table in ("ah_stockage", "ah_ouvrage"):
                                parent = str(feature['ah_id'])
                                self._location_details[table, object_id] = '\nObjet AH : ' + label
                                name = parents['ah'].get(parent, 'AH')
                                kind = 'Stockage AH' if table == 'ah_stockage' else 'Ouvrage AH'
                                label = f"{name} — {kind} : {label}"
                            elif table in SITE_CHILDREN:
                                label = (f"{parents['sites_terrestres'].get(str(feature['site_terrestre_id']), 'Site')}"
                                         f" — {TARGETS[table].label} : {label}")
                        family_objects.append((table, (object_id,), label,
                                               (feature.id(),), search_key(label), layer))
                self.objects.extend(family_objects)
                _patrimony_trace(f"Chargement {table} : {len(family_objects)} objets")
            except Exception:
                failed.append(table)
                _patrimony_trace(f"Chargement {table} : échec")
                QgsMessageLog.logMessage(
                    f"Sélecteur patrimonial {table} :\n{traceback.format_exc()}",
                    "GEMAPI-App", Qgis.Critical)
        self._object_load_failures = tuple(failed)
        self.searchEdit.setPlaceholderText("Rechercher un objet patrimonial")
        self._filter_objects()

    def _hydro_options(self):
        def read_hydro():
            layer = self._object_layer("cours_eau")
            if layer is None:
                return ()
            return tuple(build_options(layer.getFeatures()))
        return (self.session.get("family.hydro_catalogue", read_hydro, 0)
                if self.session is not None else read_hydro())

    @profiled("form.qt.filter_objects")
    def _filter_objects(self, *_):
        self.objectList.clear()
        query = search_key(self.searchEdit.text())
        reference = self._reference_geometry()
        has_reference = (reference is not None and not reference.isNull()
                         and not reference.isEmpty() and reference.isGeosValid())
        filtering = self.nearbyOnly.isChecked() and has_reference
        if self._object_load_failures:
            self.statusLabel.setText(
                "Lecture patrimoniale incomplète : " +
                ", ".join(self._object_load_failures) + ".")
        elif self.nearbyOnly.isChecked() and not filtering:
            self.statusLabel.setText("Aucune référence spatiale valide : le filtre de distance ne peut pas être appliqué")
        elif self.statusLabel.text().startswith(("Aucune référence spatiale valide :",
                                                "Recherche spatiale indisponible :")):
            self.statusLabel.clear()
        distance = self.distanceSpin.value() * (self.distanceUnitCombo.currentData() or 1)
        spatial = self._spatial_candidates(distance) if has_reference else None
        allowed = spatial if filtering else None
        active = {table for table, _ in self._associations()}
        seen = set()
        for table, object_ids, label, fids, search, layer in self.objects:
            if active and table not in active:
                continue
            if query not in search:
                continue
            if has_reference and any((table, uid) in getattr(self, '_hydro_excluded', set())
                                     for uid in object_ids):
                continue
            selected_ids, selected_fids = object_ids, fids
            if allowed is not None:
                kept = [(uid, fid) for uid, fid in zip(object_ids, fids)
                        if uid in allowed[table]]
                if not kept:
                    continue
                selected_ids, selected_fids = tuple(uid for uid, _ in kept), tuple(fid for _, fid in kept)
            key = (table, selected_ids)
            if key in seen:
                continue
            seen.add(key)
            shown_label = (label + f" — {len(selected_ids)} tronçon(s) proposé(s)"
                           if table == "cours_eau" and len(selected_ids) < len(object_ids)
                           else label)
            item = QListWidgetItem(shown_label, self.objectList)
            # The unadorned label travels with the selected UUIDs. Spatial AH
            # context must not become a retained/recorded AH association.
            item.setData(Qt.UserRole, (table, selected_ids, label, selected_fids, layer))
            details = self._location_details.get((table, selected_ids[0]), '')
            item.setToolTip(shown_label + details + '\nProposition à sélectionner explicitement ; '
                            'le contexte spatial ne constitue pas un rattachement enregistré.')
        if spatial is not None:
            _patrimony_trace(
                f"Affichage : {sum(map(len, spatial.values()))} candidats retenus, "
                f"{self.objectList.count()} lignes affichées")
            inconsistent = [(table, uid) for table, uid in self._associations()
                            if table in ("cours_eau", "digues", "ah_sections") and uid not in spatial[table]]
            self.selectedLabel.setText(
                "Rattachements retenus — au moins un obligatoire ; à vérifier" if inconsistent
                else "Rattachements retenus — au moins un obligatoire")
            self.selectedLabel.setToolTip(
                "Vérifiez la cohérence des rattachements avec la nouvelle géométrie."
                if inconsistent else "")
        else:
            self.spatialWarning.hide()
            self.selectedLabel.setText("Rattachements retenus — au moins un obligatoire")
            self.selectedLabel.setToolTip("")
        self._update_association_actions()

    def _update_association_actions(self):
        selected = self.objectList.selectedItems()
        categories = {item.data(Qt.UserRole)[0] for item in selected}
        active = {table for table, _ in self._associations()}
        can_add = bool(selected) and len(categories) == 1 and (not active or categories == active)
        if can_add:
            can_add = len(selected) == 1 and not active
        self.addButton.setEnabled(self.mode != "view" and can_add)
        self.locateButton.setEnabled(bool(selected))
        self.removeButton.setEnabled(self.mode != "view" and
                                     bool(self.selectedList.selectedItems()))

    @profiled("form.postgres.spatial_candidates")
    def _spatial_candidates(self, distance):
        reference = self._reference_geometry()
        key = (reference.asWkt(17), distance)
        if self._spatial_cache is not None and self._spatial_cache[0] == key:
            self.spatialWarning.setVisible(bool(self.spatialWarning.text()))
            return self._spatial_cache[1]
        try:
            rows = self._connection().executeSql(candidate_query(key[0], distance))
            _patrimony_trace(f"ST_DWithin : {len(rows)} candidats SQL pour {distance:g} m"
                             if rows else f"Aucun résultat SQL pour {distance:g} m")
            allowed, resolutions = resolve_candidates(reference, rows)
            conflict = any(int(visible) + len(ids)>1 for visible, ids, _ in resolutions.values())
            uncertain = any(ambiguous for _, _, ambiguous in resolutions.values())
            self._hydro_resolutions = resolutions
            self._hydro_excluded = {(str(family), str(uid)) for family, uid, *_ in rows
                                    if str(family) in ('cours_eau', 'digues', 'ah_sections')
                                    and str(uid) not in allowed[str(family)]}
            # Intersections can also establish multiple plausible carriers.
            # A site and its children count as one carrier for spatial ambiguity.
            carriers = set()
            for family, ids, _, fids, _, layer in self.objects:
                for oid, fid in zip(ids, fids):
                    if oid not in allowed[family]:
                        continue
                    feature = layer.getFeature(fid)
                    if feature.hasGeometry() and reference.intersects(feature.geometry()):
                        carriers.add(("site", str(feature["site_terrestre_id"])) if family in SITE_CHILDREN
                                     else ("site", oid) if family=="sites_terrestres" else (family, oid))
            conflict |= len(carriers)>1
            self.spatialWarning.setText(
                "La référence de recherche peut concerner plusieurs objets patrimoniaux, mais un seul "
                "rattachement est autorisé et sera pris en compte dans les rapports. "
                "Créez des prestations distinctes si nécessaire." +
                (" La projection longitudinale reste incertaine." if uncertain else "") if conflict else "")
            self.spatialWarning.setVisible(conflict)
            self._spatial_cache = key, allowed
            return allowed
        except Exception:
            self._spatial_cache = None
            self._hydro_excluded = set()
            self.spatialWarning.hide()
            QgsMessageLog.logMessage(
                f"Résolution spatiale :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Warning)
            self.statusLabel.setText(
                "Recherche spatiale indisponible : vérifiez la connexion et réessayez.")
            return None

    def _selected_text(self, table, label, selected_ids, full_ids):
        category = dict((table_key, title) for title, table_key in CATEGORIES)[table]
        partial = (f" — partiel {len(selected_ids)}/{len(full_ids)}"
                   if len(selected_ids) < len(full_ids) else "")
        return f"{category} — {label}{partial}"

    def _add_selection(self, table, full_ids, label):
        # Explicit selection upgrades a partial group to all current UUIDs.
        wanted = set(full_ids)
        for index in reversed(range(self.selectedList.count())):
            item = self.selectedList.item(index)
            old_table, selected_ids, old_label, old_full = item.data(Qt.UserRole)
            if old_table != table or not wanted.intersection(selected_ids):
                continue
            remaining = tuple(uid for uid in selected_ids if uid not in wanted)
            if not remaining:
                self.selectedList.takeItem(index)
            else:
                item.setData(Qt.UserRole, (old_table, remaining, old_label, old_full))
                item.setText(self._selected_text(old_table, old_label, remaining, old_full))
                item.setToolTip(item.text())
        selected = QListWidgetItem(self._selected_text(table, label, full_ids, full_ids),
                                   self.selectedList)
        selected.setData(Qt.UserRole, (table, full_ids, label, full_ids))
        selected.setToolTip(selected.text() + self._location_details.get((table, full_ids[0]), ''))

    def _update_position_state(self):
        families = {table for table, _ in self._associations()}
        eligible = len(families) == 1 and next(iter(families)) in ("cours_eau", "digues", "ah_sections", "ah_ouvrage")
        family = next(iter(families)) if len(families) == 1 else None
        if family is None and self._active_patrimony_family in ("cours_eau", "digues", "ah_sections", "ah_ouvrage"):
            self._pending_location_values = (self.positionCombo.currentData(), self.riveCombo.currentData())
        if not eligible:
            self.positionCombo.setCurrentIndex(self.positionCombo.findData("Indéfini"))
            self.riveCombo.setCurrentIndex(self.riveCombo.findData("Indéfinie"))
            if family is not None:
                self._pending_location_values = None
        elif self._pending_location_values is not None:
            position, rive = self._pending_location_values
            self.positionCombo.setCurrentIndex(self.positionCombo.findData(position))
            self.riveCombo.setCurrentIndex(self.riveCombo.findData(rive))
            self._pending_location_values = None
        self._active_patrimony_family = family
        self.positionCombo.setEnabled(self.mode != "view" and eligible)
        self.riveCombo.setEnabled(self.mode != "view" and eligible)

    @profiled("form.qt.add_objects")
    def _add_objects(self, *_):
        chosen = self.objectList.selectedItems()
        if chosen and (len(chosen) > 1 or self._associations()):
            self.statusLabel.setText(
                "Une prestation doit avoir un seul rattachement patrimonial. "
                "Retirez le rattachement actuel pour le remplacer.")
            return
        for item in chosen:
            table, object_ids, label, _, _ = item.data(Qt.UserRole)
            existing = self._associations()
            if any(family != table for family, _ in existing):
                self.statusLabel.setText(
                    "Retirez d'abord les rattachements de l'autre catégorie patrimoniale.")
                return
            self._add_selection(table, tuple(object_ids), label)
        self._update_position_state()
        self._update_geometry_status()
        self._filter_objects()
        self._update_save_state()

    @profiled("form.load_existing")
    def load_draft(self, values, associations):
        """Prefill a new, verified assistant draft. This method performs only reads."""
        from .assistant import ALLOWED_FAMILIES, REFERENCES, load_catalog

        if self.mode != "create" or self._snapshot() != self._saved_snapshot:
            raise ValueError("Le brouillon exige un formulaire de création neuf.")
        values, associations = validate(values, associations, None)
        if len(associations) != 1 or associations[0][0] not in ALLOWED_FAMILIES:
            raise ValueError("Rattachement hors périmètre de l’assistant IA.")
        family, uid = associations[0]
        catalog = load_catalog(self._connection(), family)
        combos = {"type_prestation": self.typeCombo, "prestataire": self.prestataireCombo,
                  "statut": self.statutCombo, "technicien": self.technicienCombo}
        for field, column in REFERENCES.items():
            if (values[column] not in {row[0] for row in catalog[field]}
                    or combos[field].findData(values[column]) < 0):
                raise ValueError("Référence absente ou inactive : " + field + ".")
        if uid not in {row[0] for row in catalog["patrimoine"]}:
            raise ValueError("L'objet patrimonial n'existe plus ou n'est pas accessible.")
        obj = next((obj for obj in self.objects if obj[0] == family and obj[1] == (uid,)), None)
        if obj is None:
            raise ValueError("Objet indisponible dans le sélecteur standard. Vérifiez les couches du projet QGIS.")
        dates = [QDate.fromString(values[key], "yyyy-MM-dd") for key in ("date_debut", "date_fin")]
        if any(not day.isValid() or day <= self._empty_date or day > self.finDate.maximumDate() for day in dates):
            raise ValueError("Date hors de la plage du formulaire standard.")
        self._reset()
        self._set_editable(True)
        self._assistant_draft = True
        self.setWindowTitle("Assistant IA — vérifier la prestation avant enregistrement")
        for field, column in REFERENCES.items():
            combos[field].setCurrentIndex(combos[field].findData(values[column]))
        self.intituleEdit.setText(values["intitule"])
        self.descriptionEdit.setPlainText(values["description"])
        self.quantiteEdit.setText(values["quantite"])
        self.uniteEdit.setText(values["unite"])
        self.debutDate.setDate(dates[0])
        self.finDate.setDate(dates[1])
        self.geometry = None
        self._add_selection(family, (uid,), obj[2])
        self._active_patrimony_family = family
        self._update_position_state()
        self._filter_objects()
        self._update_geometry_status()
        self.statusLabel.setText("Brouillon IA prérempli, non enregistré. Vérifiez les champs puis cliquez sur Enregistrer.")

    def load_existing(self, connection, prestation_id):
        """Replace form state with a single persisted prestation and its links."""
        if self.session is not None:
            connection = self.session.connection()
        with measure("form.load_existing.sql"):
            row, links = load_prestation(connection, prestation_id)
        with measure("form.qt.fields"):
            self._reset()
            self.current_prestation_id = str(row[0])
            self._loaded_version = str(row[18])
            self._edit_connection = connection
            self._edit_scope_generation = (self.session.scope_generation
                                           if self.session is not None else None)
            self._edit_saved = False
            self.intituleEdit.setText(str(row[1] or ""))
            for combo, uid, label in ((self.typeCombo, row[2], row[15]),
                                      (self.prestataireCombo, row[3], row[16]),
                                      (self.statutCombo, row[4], row[17]),
                                      (self.technicienCombo, row[19], row[20])):
                if uid:
                    index = combo.findData(str(uid))
                    if index < 0:
                        combo.addItem(str(label or "Référence inactive"), str(uid))
                        index = combo.count() - 1
                    combo.setCurrentIndex(index)
            for widget, value in ((self.quantiteEdit, row[7]),
                                  (self.uniteEdit, row[8]),
                                  (self.photo1Comment, row[11]), (self.photo2Comment, row[13])):
                widget.setText(str(value or ""))
            for widget, value in ((self.debutDate, row[5]), (self.finDate, row[6])):
                widget.setDate(QDate.fromString(str(value)[:10], "yyyy-MM-dd")
                               if value else self._empty_date)
            self.descriptionEdit.setPlainText(str(row[9] or ""))
            self.photo_references = [str(row[10] or ""), str(row[12] or "")]
            self._update_photo(0)
            self._update_photo(1)
        with measure("form.qgis.wkt_parse"):
            if row[14]:
                self.geometry = QgsGeometry.fromWkt(str(row[14]))
                kind = self.geometry.wkbType()
                geom_type = QgsWkbTypes.geometryType(kind)
                self.geometry_kind = {QgsWkbTypes.PointGeometry: "point",
                                      QgsWkbTypes.LineGeometry: "line",
                                      QgsWkbTypes.PolygonGeometry: "polygon"}.get(geom_type)
        self._update_geometry_status()
        labels = {table: label for label, table in CATEGORIES}
        with measure("form.qgis.hydro_grouping"):
            hydro_options = (self._hydro_options()
                             if any(str(table) == "cours_eau" for table, _, _ in links)
                             else ())
        option_by_id = {uid: option for option in hydro_options for uid in option.ids}
        hydro_links = {}
        object_labels = {(table, uid): label for table, ids, label, _, _, _ in self.objects
                         if table in ('digues', 'ah_stockage', 'ah_ouvrage', 'ah_sections', *SITE_CHILDREN) for uid in ids}
        for table, object_id, label in links:
            table, object_id = str(table), str(object_id)
            if table not in labels:
                raise ValueError("Famille patrimoniale inconnue dans la base.")
            if table == "cours_eau" and object_id in option_by_id:
                option = option_by_id[object_id]
                hydro_links.setdefault(option.ids, (option, set()))[1].add(object_id)
                continue
            full_ids = (object_id,)
            label = object_labels.get((table, object_id), label)
            item = QListWidgetItem(self._selected_text(table, str(label), full_ids, full_ids),
                                   self.selectedList)
            item.setData(Qt.UserRole, (table, full_ids, str(label), full_ids))
            item.setToolTip(item.text() + self._location_details.get((table, object_id), ''))
        for full_ids, (option, selected) in hydro_links.items():
            selected_ids = tuple(uid for uid in full_ids if uid in selected)
            item = QListWidgetItem(self._selected_text("cours_eau", option.label,
                                                       selected_ids, full_ids), self.selectedList)
            item.setData(Qt.UserRole, ("cours_eau", selected_ids, option.label, full_ids))
            item.setToolTip(item.text())
        self.selectedLabel.setText("Rattachements retenus")
        self.selectedLabel.setToolTip("")
        self.positionCombo.setCurrentIndex(
            max(0, self.positionCombo.findData(str(row[21] or "Indéfini"))))
        self.riveCombo.setCurrentIndex(
            max(0, self.riveCombo.findData(str(row[22] or "Indéfinie"))))
        self._update_position_state()
        self._update_geometry_status()
        self._saved_snapshot = self._snapshot()
        self._set_editable(self.mode != "view")
        self._filter_objects()
        self._update_association_actions()

    @profiled("form.reload")
    def _reload_existing(self, *_):
        if self.mode != "edit" or not self.current_prestation_id:
            return
        if not self._confirm_abandon():
            return
        try:
            if self.session is not None:
                self.session.connection()
                if self.session.scope_generation != self._edit_scope_generation:
                    raise ValueError("La connexion PostgreSQL a changé.")
                self.session.invalidate_family("cours_eau")
            self.load_existing(self._edit_connection, self.current_prestation_id)
            self._load_objects()
        except Exception:
            QgsMessageLog.logMessage(
                f"Rechargement de la prestation :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Critical)
            self.statusLabel.setText(
                "Impossible de recharger la fiche. Vérifiez la connexion et les droits SELECT.")
        else:
            self.statusLabel.setText("Fiche rechargée depuis PostgreSQL.")

    def _remove_objects(self):
        for item in self.selectedList.selectedItems():
            self.selectedList.takeItem(self.selectedList.row(item))
        self._update_position_state()
        self._update_geometry_status()
        self._filter_objects()
        self._update_save_state()

    @profiled("form.qgis.locate")
    def _locate(self, *_):
        selected = self.objectList.selectedItems()
        if not selected:
            return
        table, _, _, fids, layer = selected[0].data(Qt.UserRole)
        if not layer:
            return
        self.iface.mapCanvas().zoomToFeatureIds(layer, list(fids))

    def _values(self):
        return {
            "intitule": self.intituleEdit.text(),
            "type_prestation_id": self.typeCombo.currentData() or "",
            "prestataire_id": self.prestataireCombo.currentData() or "",
            "statut_id": self.statutCombo.currentData() or "",
            "technicien_id": self.technicienCombo.currentData() or "",
            "date_debut": self.debutDate.date().toString("yyyy-MM-dd") if self.debutDate.date() != self._empty_date else "",
            "date_fin": self.finDate.date().toString("yyyy-MM-dd") if self.finDate.date() != self._empty_date else "",
            "quantite": self.quantiteEdit.text(),
            "unite": self.uniteEdit.text(),
            "description": self.descriptionEdit.toPlainText(),
            "position": self.positionCombo.currentData() or "Indéfini",
            "rive": self.riveCombo.currentData() or "Indéfinie",
            "photo_1": self.photo_references[0],
            "photo_1_commentaire": self.photo1Comment.text(),
            "photo_2": self.photo_references[1],
            "photo_2_commentaire": self.photo2Comment.text(),
        }

    def _associations(self):
        seen = set()
        result = []
        for index in range(self.selectedList.count()):
            table, selected_ids, _, _ = self.selectedList.item(index).data(Qt.UserRole)
            for uid in selected_ids:
                key = (table, uid)
                if key not in seen:
                    seen.add(key)
                    result.append(key)
        return result

    def _snapshot(self):
        geometry_wkt = self.geometry.asWkt(17) if self.geometry is not None else None
        return self._values(), tuple(self._associations()), geometry_wkt

    def _geometry_wkt_for_save(self):
        geometry = self.geometry
        if geometry is None:
            return None
        if not geometry.isMultipart() and any(
                family == "sites_terrestres" for family, _ in self._associations()):
            geometry = QgsGeometry(geometry)
            if not geometry.convertToMultiType():
                raise ValueError("Impossible de convertir la géométrie du site en multipartie.")
        return geometry.asWkt(17)

    @profiled("form.connection")
    def _connection(self):
        if self.session is not None:
            return self.session.connection()
        return connection_from_project()

    def _check_spatial_attachment(self, associations, geometry_wkt):
        if len(associations) != 1:
            raise ValueError("Exactement un rattachement patrimonial est obligatoire.")
        if self.mode == "edit":
            _, previous_links, previous_geometry = self._saved_snapshot
            if set(previous_links) == set(associations) and previous_geometry == geometry_wkt:
                return  # Preserve historical associations; no automatic conversion.
        family, uid = associations[0]
        if family not in ("cours_eau", "digues", "ah_sections"):
            return
        connection = self._connection()
        if family == "cours_eau":
            course_id = uid
        else:
            query = (f"SELECT cours_eau_id::text FROM gemapi.ah_sections WHERE id='{uid}'::uuid"
                     if family == "ah_sections" else
                     f"SELECT riviere_id::text FROM gemapi.digues_reperes WHERE digue_id='{uid}'::uuid")
            rows = connection.executeSql(query)
            if len(rows) != 1:
                raise ValueError("Support hydrographique introuvable ; actualisez les données.")
            course_id = str(rows[0][0])
        geometry = QgsGeometry.fromWkt(geometry_wkt)
        rows = connection.executeSql(candidate_query(geometry_wkt, 1e9, course_id))
        allowed, resolutions = resolve_candidates(geometry, rows)
        if uid not in allowed[family]:
            raise ValueError("Ce rattachement ne correspond pas à la portion hydrographique de l’intervention "
                             "ou sa référence doit être revalidée. Choisissez le support proposé.")
        visible, priorities, uncertain = resolutions[course_id]
        if int(visible) + len(priorities)>1 or uncertain:
            raise ValueError("Rattachement hydrographique ambigu : plusieurs portions ou patrimoines prioritaires "
                             "sont possibles. Précisez la géométrie ou subdivisez l’intervention ; "
                             "aucune priorité entre digue et section AH n’est appliquée.")

    @profiled("form.save")
    def _save(self, *_):
        if self._save_completed:
            return
        if self.mode == "edit":
            self._save_edit()
            return
        if self.mode != "create":
            return
        try:
            if self._geometry_error():
                raise ValueError(self._geometry_error())
            if self._geometry_building:
                raise ValueError("Terminez la construction de la géométrie avant d'enregistrer.")
            geometry_wkt = self._geometry_wkt_for_save()
            values, associations = validate(self._values(), self._associations(), geometry_wkt)
            if self._assistant_draft:
                from .assistant import ALLOWED_FAMILIES
                if geometry_wkt is not None or any(table not in ALLOWED_FAMILIES for table, _ in associations):
                    raise ValueError("Assistant IA exige un ouvrage AH, stockage AH ou enfant de site avec une géométrie propre NULL.")
            self._check_spatial_attachment(associations, geometry_wkt)
            query = build_insert(values, associations, geometry_wkt)
            connection = self._connection()
        except ValueError as error:
            self.statusLabel.setText(str(error))
            return
        except Exception:
            self.statusLabel.setText("Connexion PostgreSQL indisponible dans QGIS.")
            return
        try:
            result = connection.executeSql(query)
        except Exception as error:
            # Provider errors can contain a connection URI; do not show credentials.
            QgsMessageLog.logMessage(
                f"Création de prestation :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Critical)
            self.statusLabel.setText(database_error_message(error))
            return
        if len(result) != 1 or len(result[0]) != 2:
            self.statusLabel.setText("Réponse PostgreSQL inattendue : vérifiez la prestation avant de réessayer.")
            return
        try:
            uid = str(UUID(str(result[0][0])))
        except (ValueError, TypeError):
            self.statusLabel.setText("UUID enregistré inattendu : vérifiez la prestation avant de réessayer.")
            return
        self._complete_save(uid, connection, self._associations(), "Prestation enregistrée.")

    @profiled("form.save_edit")
    def _save_edit(self):
        if self._save_completed:
            return
        if not self.current_prestation_id or not self._edit_connection:
            self.statusLabel.setText("Aucune prestation chargée pour l'édition.")
            return
        if self.session is not None:
            try:
                current_connection = self.session.connection()
            except Exception:
                self.statusLabel.setText("Connexion PostgreSQL indisponible ; rechargez la gestion.")
                return
            if (self.session.scope_generation != self._edit_scope_generation or
                    current_connection is not self._edit_connection):
                self.statusLabel.setText("La connexion PostgreSQL a changé ; rouvrez la fiche.")
                return
        try:
            if self._geometry_error():
                raise ValueError(self._geometry_error())
            if self._geometry_building:
                raise ValueError("Terminez la construction de la géométrie avant d'enregistrer.")
            geometry_wkt = self._geometry_wkt_for_save()
            previous_values, previous_links, previous_geometry = self._saved_snapshot
            self._check_spatial_attachment(self._associations(), geometry_wkt)
            query = build_update(
                self.current_prestation_id, self._loaded_version,
                previous_values, self._values(), previous_links, self._associations(),
                previous_geometry, geometry_wkt)
        except ValueError as error:
            self.statusLabel.setText(str(error))
            return
        except Exception:
            self.statusLabel.setText("La fiche contient une valeur non valide.")
            return
        if query is None:
            self.statusLabel.setText("Aucune modification à enregistrer.")
            return
        try:
            self._edit_connection.executeSql(query)
        except Exception as error:
            QgsMessageLog.logMessage(
                f"Modification de prestation :\n{traceback.format_exc()}",
                "GEMAPI-App", Qgis.Critical)
            message = str(error)
            if "SIG_PRESTATIONS_CONFLICT" in message:
                self.statusLabel.setText(
                    "Cette prestation ou ses rattachements ont changé depuis l'ouverture. "
                    "Aucune modification enregistrée. Recharger la fiche pour voir la version actuelle.")
            elif "SIG_PRESTATIONS_NOT_FOUND" in message:
                self.statusLabel.setText(
                    "Cette prestation n'existe plus. Aucune modification enregistrée. "
                    "Revenez à la recherche.")
            else:
                self.statusLabel.setText(database_error_message(error))
            return
        changed_links = set(previous_links) | set(self._associations())
        self._edit_saved = True
        self._complete_save(self.current_prestation_id, self._edit_connection, changed_links,
                            "Modifications enregistrées.")

    def _complete_save(self, uid, connection, associations, message):
        """Keep this form and the plugin alive; only saved prestation data is invalidated."""
        if self.session is not None:
            self.session.invalidate_prestations()
        self.saved_prestation_id = uid
        self._saved_scope_generation = self.session.scope_generation if self.session is not None else None
        self._save_completed = True
        self._saved_snapshot = self._snapshot()
        try:
            refreshed = self._refresh_layers(associations)
        except Exception:
            refreshed = False
        warning = "" if refreshed else " Certaines couches doivent être actualisées manuellement."
        self._edit_refresh_warning = warning
        if warning and hasattr(self.iface, "messageBar"):
            self.iface.messageBar().pushMessage("GEMAPI-App", message + warning, level=Qgis.Warning, duration=10)
        self._stop_map_selection(show_form=False)
        self._update_save_state()
        self.statusLabel.setText(message + warning)
        self.hide()
        self.saved.emit(uid)
        focus_prestation(self.iface, connection, uid)

    @profiled("form.qgis.refresh_layers")
    def _refresh_layers(self, associations=None):
        names = {"prestations", "prestations_fiches", "prestations_carte"}
        links = associations if associations is not None else self._associations()
        names.update(table for table, _ in links)
        names.update(LINKS[table][0] for table, _ in links)
        anchor = (self._canonical_layer("prestations") or self._canonical_layer("prestations_fiches")
                  or self._canonical_layer("prestations_carte"))
        if anchor is None:
            return False
        connection_info = QgsDataSourceUri(anchor.source()).connectionInfo(False)
        refreshed = True
        for layer in QgsProject.instance().mapLayers().values():
            if not layer.isValid() or layer.providerType() != "postgres":
                continue
            uri = QgsDataSourceUri(layer.source())
            if (uri.schema() != "gemapi" or uri.table() not in names or
                    uri.connectionInfo(False) != connection_info):
                continue
            try:
                layer.reload()
                layer.triggerRepaint()
            except Exception:
                refreshed = False
        return refreshed

    def _reset(self):
        self._assistant_draft = False
        self._edit_saved = False
        self._edit_refresh_warning = ""
        self._save_completed = False
        self.saved_prestation_id = None
        self._saved_scope_generation = None
        self._stop_map_selection(show_form=False)
        self.geometry = None
        self.geometry_kind = None
        self._map_reference = None
        self._spatial_cache = None
        self._active_patrimony_family = None
        self._pending_location_values = None
        self.current_prestation_id = None
        self._loaded_version = None
        self._edit_connection = None
        self._edit_scope_generation = None
        for edit in (self.intituleEdit, self.quantiteEdit,
                     self.uniteEdit, self.searchEdit):
            edit.clear()
        self.descriptionEdit.clear()
        self.photo1Comment.clear()
        self.photo2Comment.clear()
        self.photo_references = ["", ""]
        self._update_photo(0)
        self._update_photo(1)
        self.typeCombo.setCurrentIndex(0)
        self.prestataireCombo.setCurrentIndex(0)
        self.statutCombo.setCurrentIndex(0)
        self.technicienCombo.setCurrentIndex(0)
        self.debutDate.setDate(self._empty_date)
        self.finDate.setDate(self._empty_date)
        self.selectedList.clear()
        self.positionCombo.setCurrentIndex(0)
        self.riveCombo.setCurrentIndex(0)
        self._update_position_state()
        self.selectedLabel.setText("Rattachements retenus")
        self.selectedLabel.setToolTip("")
        self.nearbyOnly.setChecked(False)
        self.distanceSpin.setValue(500)
        self.distanceUnitCombo.setCurrentIndex(0)
        self.geometry = None
        self.geometry_kind = None
        self._geometry_building = False
        self._update_geometry_status()
        self._saved_snapshot = self._snapshot()

    def _confirm_abandon(self):
        if self.mode not in ("create", "edit"):
            return True
        if self._snapshot() == self._saved_snapshot:
            return True
        return QMessageBox.question(
            self, "Modifications non enregistrées",
            "Abandonner les modifications et les associations non enregistrées ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

    def reject(self):
        if self._disposing or self._close_confirmed or self._confirm_abandon():
            self.cancel_capture(notify=False)
            self._stop_map_selection(show_form=False)
            super().reject()

    def closeEvent(self, event):
        if not self._disposing and not self._confirm_abandon():
            event.ignore()
            return
        self._close_confirmed = True
        self.cancel_capture(notify=False)
        self._stop_map_selection(show_form=False)
        super().closeEvent(event)
        self._close_confirmed = False
