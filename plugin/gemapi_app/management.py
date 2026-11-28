"""Read existing prestations and their exact patrimonial links for consultation."""

from pathlib import Path
import traceback
from uuid import UUID

from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (QDialog, QLayout, QListWidgetItem, QSizePolicy,
                                QComboBox, QLabel, QMessageBox)
from qgis.core import QgsDataSourceUri, QgsMessageLog, QgsProject, Qgis

from .record import TARGETS, CATEGORIES, LINKS, build_delete
from .permissions import prestation_permissions
from .watercourse_labels import label_sql, section_label_sql
from .hydro_groups import build_options
from .filter_picker import FilterPicker
from .perf import measure, profiled
from .session_data import CACHE_ENABLED, connection_from_project
from .window_geometry import initialize_window
from .report_data import build_management_query, filter_option_queries


@profiled("manager.sql.list")
def list_prestations(connection, filters=None, search_text=""):
    """Fetch only UUID and display label; all selection rules come from report_data."""
    return connection.executeSql(build_management_query(filters or {}, search_text))


@profiled("manager.sql.record_and_links")
def load_prestation(connection, prestation_id):
    """Return fields and stored links selected by one validated UUID."""
    uid = str(UUID(str(prestation_id)))
    rows = connection.executeSql(
        "SELECT id::text, intitule, type_prestation_id::text, prestataire_id::text, "
        "statut_id::text, date_debut::text, date_fin::text, quantite::text, unite, "
        "description, photo_1, photo_1_commentaire, photo_2, "
        "photo_2_commentaire, ST_AsText(geom), "
        "(SELECT libelle FROM gemapi.types_prestations WHERE id = p.type_prestation_id), "
        "(SELECT nom FROM gemapi.prestataires WHERE id = p.prestataire_id), "
        "(SELECT libelle FROM gemapi.statuts_prestations WHERE id = p.statut_id), "
        "p.xmin::text, p.technicien_id::text, "
        "(SELECT concat_ws(' ', prenom, nom) FROM gemapi.techniciens "
        "WHERE id = p.technicien_id), p.position, p.rive "
        f"FROM gemapi.prestations p WHERE p.id = '{uid}'::uuid")
    if len(rows) != 1 or len(rows[0]) != 23 or str(rows[0][0]) != uid:
        raise ValueError("Prestation introuvable ou réponse PostgreSQL inattendue.")
    parts = []
    for _, table in CATEGORIES:
        link_table, foreign_key = LINKS[table]
        predicate = f"l.id = '{uid}'::uuid"
        if table == "ah_sections":
            parts.append(
                f"SELECT 'ah_sections' AS famille, o.id::text AS objet_id, {section_label_sql()} || "
                "CASE WHEN v.etat_reference<>'CONFORME' THEN ' — référence à revalider' ELSE '' END AS nom "
                "FROM gemapi.prestations l JOIN gemapi.ah_sections o ON o.id=l.ah_section_id "
                "JOIN gemapi.ah_sections_carte v ON v.id=o.id "
                "JOIN gemapi.cours_eau r ON r.id=o.cours_eau_id JOIN gemapi.ah a ON a.id=o.ah_id "
                f"WHERE {predicate}")
            continue
        name = label_sql("o") if table == "cours_eau" else f"COALESCE(NULLIF(btrim(o.{TARGETS[table].name}), ''), 'Objet sans nom')"
        parts.append(
            f"SELECT '{table}' AS famille, l.{foreign_key}::text AS objet_id, "
            f"{name} AS nom "
            f"FROM gemapi.{link_table} l JOIN gemapi.{table} o "
            f"ON o.id = l.{foreign_key} WHERE {predicate}")
    links = connection.executeSql(" UNION ALL ".join(parts) + " ORDER BY famille, nom, objet_id")
    # QGIS may expose SQL NULL as a null QVariant rather than Python None.
    row = [None if hasattr(value, "isNull") and value.isNull() else value
           for value in rows[0]]
    return row, links


class ManageDialog(QDialog):
    @profiled("manager.construct")
    def __init__(self, connection=None, parent=None, session=None):
        super().__init__(parent)
        uic.loadUi(str(Path(__file__).with_name("form.ui")), self)
        self.connection = connection
        self.session = session
        self.permissions = dict.fromkeys(("select", "insert", "update", "delete"), False)
        self._loaded_scope = None
        self._loaded_generation = None
        self._clearing = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._fetch_results)
        names = {"years": "Années", "statuses": "Statuts",
                 "technicians": "Techniciens", "providers": "Prestataires",
                 "communes": "Communes"}
        self.pickers = {}
        for key in ("years", "statuses", "technicians", "providers", "communes"):
            picker = FilterPicker(names[key], self.filterContents,
                                  searchable=key not in ("years", "statuses"))
            self.pickers[key] = picker
            picker.list.setMinimumHeight(70)
            picker.list.setMaximumHeight(80)
            picker.list.itemChanged.connect(self._schedule_refresh)
        self.objectPicker = FilterPicker("Patrimoine", self.filterContents, compact_title=True)
        self.familyCombo = QComboBox(self.objectPicker)
        self.familyCombo.addItem("Aucun filtre patrimonial", "")
        for label, family in (("Cours d'eau", "cours_eau"), ("Digues", "digues"),
                              ("Aménagements hydrauliques", "ah"),
                              ("Sites terrestres", "site_terrestre")):
            self.familyCombo.addItem(label, family)
        family_label = QLabel("Famille patrimoniale", self.objectPicker)
        self.objectPicker.layout().insertWidget(0, family_label)
        self.objectPicker.layout().insertWidget(1, self.familyCombo)
        self.objectPicker.search.setPlaceholderText("Rechercher un objet patrimonial…")
        self.objectPicker.list.setMinimumHeight(70)
        self.objectPicker.list.setMaximumHeight(80)
        self.objectPicker.list.itemChanged.connect(self._schedule_refresh)
        self.familyCombo.currentIndexChanged.connect(self._family_changed)
        self.verticalLayout.setSizeConstraint(QLayout.SetNoConstraint)
        self._arrange_filters()
        for button in (self.newButton, self.openButton, self.deleteButton, self.backButton):
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for index in range(4):
            self.actionsLayout.setStretch(index, 1)
        self.deleteButton.clicked.connect(self.delete_selected)
        self.resetButton.clicked.connect(self.clear_filters)
        self.refreshButton.clicked.connect(self.force_refresh)
        self.criteriaSplitter.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.filterScroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.criteriaSplitter.setStretchFactor(0, 3)
        self.criteriaSplitter.setStretchFactor(1, 2)
        self.criteriaSplitter.setSizes([500, 120])
        self.searchEdit.textChanged.connect(self._schedule_refresh)
        self.prestationList.itemSelectionChanged.connect(self._selection_changed)
        self.prestationList.itemDoubleClicked.connect(lambda _: self.openButton.click())
        self.openButton.setEnabled(False)
        self.deleteButton.setEnabled(False)
        initialize_window(self, "manage", parent)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "pickers"):
            self._arrange_filters()

    def _arrange_filters(self):
        columns = (4 if self.width() >= 900 else 3 if self.width() >= 700
                   else 2 if self.width() >= 480 else 1)
        if getattr(self, "_filter_columns", None) == columns:
            return
        self._filter_columns = columns
        for picker in (*self.pickers.values(), self.objectPicker):
            self.filterGrid.removeWidget(picker)
        for index, key in enumerate(self.pickers):
            self.filterGrid.addWidget(self.pickers[key], index // columns, index % columns)
        row, column = divmod(len(self.pickers), columns)
        self.filterGrid.addWidget(self.objectPicker, row, column, 1, columns - column)

    def _hydro_layer(self):
        return next((layer for layer in QgsProject.instance().mapLayers().values()
                      if layer.isValid() and layer.providerType() == "postgres" and
                      QgsDataSourceUri(layer.source()).schema() == "gemapi" and
                      QgsDataSourceUri(layer.source()).table() == "cours_eau"), None)

    def _family_options(self, family, query):
        if family != "cours_eau":
            return tuple(self.connection.executeSql(query))
        layer = self._hydro_layer()
        if layer is None:
            return tuple((str(uid), str(label)) for uid, label in self.connection.executeSql(query))
        def river(feature):
            kind = feature["type_ecoulement"]
            return (kind is not None and
                    not (hasattr(kind, "isNull") and kind.isNull()) and
                    str(kind).casefold() == "rivière")
        return tuple(build_options(feature for feature in layer.getFeatures()
                                   if river(feature)))

    def _load_family(self, queries):
        family = self.familyCombo.currentData()
        self.objectPicker.search.setEnabled(bool(family))
        self.objectPicker.list.setEnabled(bool(family))
        if not family:
            self.objectPicker.fill(())
            return
        selected = self.objectPicker.values()
        if family == "ah":
            def loader():
                rows = []
                for child in ("ah_stockage", "ah_ouvrage", "ah_sections"):
                    query = queries[child]
                    options = (self.session.get(f"filters.{child}",
                                                lambda q=query: tuple(self.connection.executeSql(q)))
                               if self.session is not None else self.connection.executeSql(query))
                    rows.extend(((child, str(uid)), str(label)) for uid, label in options)
                return tuple(sorted(rows, key=lambda row: (row[1].casefold(), row[0][0], row[0][1])))
        else:
            loader = lambda: self._family_options(family, queries[family])
        rows = (self.session.get("family.hydro_options", loader, 0)
                if self.session is not None and family == "cours_eau" and
                self._hydro_layer() is not None else
                self.session.get(f"filters.{family}", loader)
                if self.session is not None and family != "ah" else loader())
        if family == "cours_eau" and rows and hasattr(rows[0], "ids"):
            rows = tuple((option.ids, option.label) for option in rows)
        self.objectPicker.fill(rows, checked=selected)
        for index in range(self.objectPicker.list.count()):
            item = self.objectPicker.list.item(index)
            item.setToolTip(item.text())

    def _family_changed(self, *_):
        if self._clearing:
            return
        self.objectPicker.clear_selection()
        if self.connection is not None:
            self._load_family(filter_option_queries())
            self._schedule_refresh()

    @profiled("manager.refresh")
    def refresh(self, force=False):
        try:
            if self.session is not None:
                self.connection = self.session.connection()
                scope_changed = (self._loaded_scope is not None and
                                 self._loaded_scope != self.session.scope_generation)
                if scope_changed:
                    self._clearing = True
                    self.searchEdit.clear()
                    for picker in self.pickers.values():
                        picker.clear_selection()
                    self.familyCombo.setCurrentIndex(0)
                    self.objectPicker.clear_selection()
                    self._clearing = False
                if (CACHE_ENABLED and not force and not scope_changed and
                        self._loaded_scope == self.session.scope_generation and
                        self._loaded_generation == self.session.prestations_generation):
                    return
            elif self.connection is None:
                self.connection = connection_from_project()
            only_prestations_changed = bool(self.session is not None and CACHE_ENABLED and not force and
                                            self._loaded_scope == self.session.scope_generation and
                                            self._loaded_generation is not None)
            self.permissions = prestation_permissions(self.connection)
            self.newButton.setVisible(self.permissions["insert"])
            self.openButton.setText("Modifier la prestation" if self.permissions["update"]
                                    else "Consulter la prestation")
            self.deleteButton.setVisible(self.permissions["delete"])
            self._selection_changed()
            with measure("manager.filters"):
                queries = filter_option_queries(include_statuses=True, include_providers=True)
                for key in self.pickers:
                    if only_prestations_changed and key != "years":
                        continue
                    query = queries[key]
                    selected = self.pickers[key].values()
                    if self.session is not None:
                        if key == "providers":
                            rows = self.session.get(
                                "reference.providers",
                                lambda: tuple(self.connection.executeSql(
                                    "SELECT id::text,nom,actif FROM gemapi.prestataires ORDER BY nom,id")))
                        else:
                            cache_key = ("reference.statuses" if key == "statuses"
                                         else f"filters.{key}")
                            rows = self.session.get(
                                cache_key, lambda q=query: tuple(self.connection.executeSql(q)))
                    else:
                        rows = self.connection.executeSql(query)
                    options = ((row[0], row[0]) if key == "years" else row[:2]
                               for row in rows)
                    self.pickers[key].fill(options, checked=selected)
                if not only_prestations_changed:
                    self._load_family(queries)
        except Exception:
            QgsMessageLog.logMessage(f"Chargement des filtres :\n{traceback.format_exc()}", "GEMAPI-App", Qgis.Critical)
            self.connection = None
            self.permissions = dict.fromkeys(("select", "insert", "update", "delete"), False)
            self.newButton.setVisible(False)
            self.deleteButton.setVisible(False)
            self.prestationList.clear()
            self.resultLabel.setText("Résultats indisponibles")
            self._selection_changed()
            self.statusLabel.setText(
                "Impossible de lire les filtres PostgreSQL. Vérifiez la couche et les droits SELECT.")
            return
        if self.session is not None:
            self._loaded_scope = self.session.scope_generation
            self._loaded_generation = self.session.prestations_generation
        self._fetch_results()

    def filters(self):
        result = {key: picker.values() for key, picker in self.pickers.items()}
        family = self.familyCombo.currentData()
        if family == "ah":
            for child in ("ah_stockage", "ah_ouvrage", "ah_sections"):
                selected = tuple(uid for kind, uid in self.objectPicker.values() if kind == child)
                if selected:
                    result[child] = selected
        elif family:
            result[family] = tuple(uid for value in self.objectPicker.values()
                                   for uid in (value if isinstance(value, tuple) else (value,)))
        return result

    def _schedule_refresh(self, *_):
        if not self._clearing and self.connection is not None:
            self._search_timer.start(250)

    @profiled("manager.results")
    def _fetch_results(self):
        if self.connection is None:
            return
        previous_id = self.selected_id()
        try:
            if self.session is not None:
                self.connection = self.session.connection()
                if self._loaded_scope != self.session.scope_generation:
                    self.refresh(force=True)
                    return
            rows = list_prestations(self.connection, self.filters(), self.searchEdit.text())
        except ValueError as error:
            self.prestationList.clear()
            self.resultLabel.setText("Résultats indisponibles")
            self._selection_changed()
            self.statusLabel.setText(str(error))
            return
        except Exception:
            QgsMessageLog.logMessage(f"Recherche des prestations :\n{traceback.format_exc()}", "GEMAPI-App", Qgis.Critical)
            self.prestationList.clear()
            self.resultLabel.setText("Résultats indisponibles")
            self._selection_changed()
            self.statusLabel.setText("Recherche PostgreSQL impossible. Vérifiez les droits SELECT.")
            return
        with measure("manager.qt.results"):
            self.prestationList.clear()
            for uid, title, version in rows:
                item = QListWidgetItem(str(title), self.prestationList)
                item.setData(Qt.UserRole, str(uid))
                item.setData(Qt.UserRole + 1, str(version))
                if str(uid) == previous_id:
                    item.setSelected(True)
            self.resultLabel.setText(f"{len(rows)} prestation(s) correspondante(s)")
            self.statusLabel.setText("Aucune prestation ne correspond aux critères."
                                     if not rows else "")
            self._selection_changed()

    @profiled("manager.clear_filters")
    def clear_filters(self, *_):
        self._clearing = True
        self._search_timer.stop()
        self.searchEdit.clear()
        for picker in self.pickers.values():
            picker.clear_selection()
        self.familyCombo.setCurrentIndex(0)
        self.objectPicker.clear_selection()
        self._clearing = False
        self._fetch_results()

    @profiled("manager.force_refresh")
    def force_refresh(self, *_):
        self._search_timer.stop()
        if self.session is not None:
            self.session.clear("manual")
        self.refresh(force=True)

    def reject(self):
        self._search_timer.stop()
        super().reject()

    @profiled("manager.qt.selection")
    def _selection_changed(self, *_):
        selected = bool(self.prestationList.selectedItems())
        self.openButton.setEnabled(selected and self.permissions["select"])
        self.deleteButton.setEnabled(selected and self.permissions["delete"])

    def selected_id(self):
        selected = self.prestationList.selectedItems()
        return selected[0].data(Qt.UserRole) if selected else None

    def selected_version(self):
        selected = self.prestationList.selectedItems()
        return selected[0].data(Qt.UserRole + 1) if selected else None

    @profiled("manager.delete")
    def delete_selected(self, *_):
        selected = self.prestationList.selectedItems()
        if not selected:
            return
        item = selected[0]
        prestation_id, version = item.data(Qt.UserRole), item.data(Qt.UserRole + 1)
        try:
            connection = self.session.connection() if self.session is not None else self.connection
            if connection is None or (self.session is not None and
                                      (connection is not self.connection or
                                       self._loaded_scope != self.session.scope_generation)):
                raise ValueError("La connexion PostgreSQL a changé. Actualisez la gestion.")
            if not prestation_permissions(connection)["delete"]:
                raise ValueError("Droit DELETE absent sur gemapi.prestations.")
            query = build_delete(prestation_id, version)
        except Exception as error:
            self.statusLabel.setText(str(error) if isinstance(error, ValueError)
                                     else "Vérification PostgreSQL impossible.")
            return
        label = item.text().strip() or "Prestation sans intitulé"
        answer = QMessageBox.question(
            self, "Supprimer une prestation",
            f"Supprimer définitivement « {label} » ?\nUUID : {prestation_id}\n"
            "Les fichiers photographiques et les objets patrimoniaux seront conservés.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            if self.session is not None and (self.session.connection() is not connection or
                                             self._loaded_scope != self.session.scope_generation):
                raise ValueError("La connexion PostgreSQL a changé. Actualisez la gestion.")
            if not prestation_permissions(connection)["delete"]:
                raise ValueError("Droit DELETE absent sur gemapi.prestations.")
            connection.executeSql(query)
        except Exception as error:
            message = str(error)
            if "SIG_PRESTATIONS_CONFLICT" in message:
                message = "Cette prestation a changé depuis la sélection. Aucune suppression effectuée ; actualisez la liste."
            elif "SIG_PRESTATIONS_NOT_FOUND" in message:
                message = "Cette prestation a déjà été supprimée. Actualisez la liste."
            elif "SIG_PRESTATIONS_FORBIDDEN" in message or "permission denied" in message.lower():
                message = "Droit DELETE absent sur gemapi.prestations."
            elif ("foreign key" in message.lower() or "clé étrangère" in message.lower()
                  or "23503" in message):
                message = ("Suppression refusée : une autre donnée référence cette prestation. "
                           "Vérifiez ses dépendances avant de réessayer.")
            elif "check constraint" in message.lower() or "23514" in message:
                message = "Suppression refusée par une contrainte métier PostgreSQL."
            elif not isinstance(error, ValueError):
                message = "PostgreSQL a refusé la suppression. Vérifiez les dépendances et les droits."
            self.statusLabel.setText(message)
            return
        if self.session is not None:
            self.session.invalidate_prestations()
        self.prestationList.clearSelection()
        self.refresh(force=True)
        refreshed = self._refresh_carto(connection)
        self.statusLabel.setText("Prestation supprimée." + ("" if refreshed else
                                  " Actualisez les couches cartographiques manuellement."))

    def _refresh_carto(self, connection):
        from .session_data import _connection_info
        try:
            source = _connection_info(QgsProject.instance())
        except ValueError:
            return False
        if connection is not self.connection:
            return False
        refreshed = 0
        for layer in QgsProject.instance().mapLayers().values():
            if not layer.isValid() or layer.providerType() != "postgres":
                continue
            uri = QgsDataSourceUri(layer.source())
            if (uri.schema() == "gemapi" and uri.table() == "prestations_carte" and
                    uri.connectionInfo(False) == source):
                try:
                    layer.reload()
                    layer.triggerRepaint()
                    refreshed += 1
                except Exception:
                    return False
        return refreshed == 3
