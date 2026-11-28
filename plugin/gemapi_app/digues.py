"""Confirmed maintenance of digues and server calculated references."""
from uuid import UUID

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                                QDoubleSpinBox, QFormLayout, QInputDialog,
                                QLabel, QLineEdit, QMessageBox, QPushButton)
from qgis.core import (Qgis, QgsAction, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsDataSourceUri, QgsGeometry,
                       QgsProject, QgsProviderRegistry, QgsWkbTypes)
from qgis.gui import QgsRubberBand

from .record import literal
from .watercourse_labels import labels_query_sql


_dialogs = set()
SAVE_FUNCTION = ('gemapi.digue_enregistrer(uuid,uuid,integer,timestamptz,'
                 'text,text,text,uuid,text,double precision,double precision,text)')
DELETE_FUNCTION = 'gemapi.digue_supprimer(uuid,integer,timestamptz)'


def uid(value):
    return str(UUID(str(value)))


def fractions(start, end):
    start, end = float(start), float(end)
    if not 0 <= start < end <= 1:
        raise ValueError('Fractions attendues : 0 ≤ début < fin ≤ 1.')
    return repr(start), repr(end)


def text_literal(value):
    # Existing optional attributes may contain an empty string. Revalidation
    # preserves it as well as NULL, without normalising the parent's data.
    return "E''" if isinstance(value, str) and value == '' else literal(value)


class DigueStore:
    def __init__(self, connection):
        self.connection = connection
        ready = connection.executeSql(
            f"SELECT to_regprocedure('{SAVE_FUNCTION}') IS NOT NULL,"
            f"to_regprocedure('{DELETE_FUNCTION}') IS NOT NULL")[0]
        if not all(ready):
            raise ValueError('Cette base nécessite la mise à jour QGIS-EDIT-02 des digues. '
                             'Aucune écriture effectuée ; contactez son administrateur.')
        schema = connection.executeSql(
            'SELECT n.nspname FROM pg_extension e JOIN pg_namespace n '
            "ON n.oid=e.extnamespace WHERE e.extname='postgis'")[0][0]
        self.postgis = '"' + str(schema).replace('"', '""') + '"'

    def rows(self, query):
        return self.connection.executeSql(query)

    def permissions(self):
        return self.rows(
            f"SELECT has_function_privilege('{SAVE_FUNCTION}','EXECUTE') AND "
            "has_table_privilege('gemapi.digues','INSERT') AND "
            "has_table_privilege('gemapi.digues','UPDATE') AND "
            "has_table_privilege('gemapi.digues_reperes','INSERT') AND "
            "has_table_privilege('gemapi.digues_reperes','UPDATE') AND "
            "has_table_privilege('gemapi.cours_eau','UPDATE'),"
            f"has_function_privilege('{DELETE_FUNCTION}','EXECUTE') AND "
            "has_table_privilege('gemapi.digues','DELETE') AND "
            "has_table_privilege('gemapi.digues','UPDATE') AND "
            "has_table_privilege('gemapi.digues_reperes','DELETE') AND "
            "has_table_privilege('gemapi.digues_reperes','UPDATE')")[0]

    def courses(self):
        return self.rows(labels_query_sql(qualifications=True))

    def reference(self, reference):
        rows = self.rows(
            'SELECT d.id::text,d.nom,d.identifiant_externe,d.commentaire,'
            'd.date_modification::text,s.riviere_id::text,s.rive,'
            's.debut_fraction,s.fin_fraction,s.revision_validation,v.etat_referentiel '
            'FROM gemapi.digues_reperes s JOIN gemapi.digues d ON d.id=s.digue_id '
            'JOIN gemapi.digues_carte v ON v.id=s.id '
            f"WHERE s.id='{uid(reference)}'")
        if not rows:
            raise ValueError('Cette digue ou son repère n’existe plus. Actualisez la table.')
        return rows[0]

    def preview(self, course, start, end):
        start, end = fractions(start, end)
        rows = self.rows(
            f'SELECT {self.postgis}.ST_AsText({self.postgis}.ST_LineSubstring('
            f'{self.postgis}.ST_GeometryN(geom,1),'
            f"{start},{end})),md5(encode({self.postgis}.ST_AsEWKB(geom),'hex')) "
            f"FROM gemapi.cours_eau WHERE id='{uid(course)}' "
            'AND gemapi.cours_eau_reference_admissible(geom,direction,valide)')
        if not rows:
            raise ValueError('Cours d’eau non qualifié ou inexploitable. Choisissez un support qualifié.')
        return rows[0]

    def save(self, reference, original, name, external, comment, course, bank, start, end, signature):
        if not name.strip():
            raise ValueError('Renseignez le nom de la digue.')
        start, end = fractions(start, end)
        if bank not in ('G', 'D'):
            raise ValueError('Choisissez la rive gauche ou droite.')
        parent = literal(uid(original[0]), '::uuid') if reference else 'NULL::uuid'
        repere = literal(uid(reference), '::uuid') if reference else 'NULL::uuid'
        revision = int(original[9]) if reference else 0
        stamp = literal(original[4], '::timestamptz') if reference else 'NULL::timestamptz'
        return self.rows(
            f'SELECT gemapi.digue_enregistrer({parent},{repere},{revision},{stamp},'
            f'{text_literal(name)},{text_literal(external)},{text_literal(comment)},'
            f"'{uid(course)}','{bank}',{start},{end},{literal(signature)})::text")[0][0]

    def dependencies(self, parent):
        return int(self.rows(f"SELECT count(*) FROM gemapi.prestations WHERE digue_id='{uid(parent)}'")[0][0])

    def delete(self, reference, original):
        self.rows(f"SELECT gemapi.digue_supprimer('{uid(reference)}',{int(original[9])},"
                  f"{literal(original[4], '::timestamptz')})")


def store_for(layer):
    if layer is None or layer.providerType() != 'postgres':
        raise ValueError('La couche PostgreSQL des digues est indisponible.')
    uri = QgsDataSourceUri(layer.source())
    if uri.schema() != 'gemapi' or uri.table() not in ('digues', 'digues_carte'):
        raise ValueError('Choisissez une couche de digues du schéma gemapi.')
    return DigueStore(QgsProviderRegistry.instance().providerMetadata('postgres').createConnection(
        uri.connectionInfo(False), {}))


def ensure_saved(anchor, source):
    if QgsProject.instance().mapLayer(anchor.id()) is not anchor or anchor.source() != source:
        raise ValueError('Le projet, la couche ou sa connexion a changé. Rouvrez le dialogue.')
    connection = QgsDataSourceUri(source).connectionInfo(False)
    for layer in QgsProject.instance().mapLayers().values():
        if layer.providerType() == 'postgres' and layer.isEditable() and \
                QgsDataSourceUri(layer.source()).connectionInfo(False) == connection:
            raise ValueError('Terminez ou annulez la session d’édition PostgreSQL avant cette opération.')


def refresh(anchor):
    connection = QgsDataSourceUri(anchor.source()).connectionInfo(False)
    for layer in QgsProject.instance().mapLayers().values():
        if layer.providerType() == 'postgres':
            uri = QgsDataSourceUri(layer.source())
            if uri.connectionInfo(False) == connection and uri.table() in ('digues', 'digues_carte'):
                layer.reload()
                layer.triggerRepaint()


def action(name, operation):
    command = ('from gemapi_app.digues import dispatch\n'
               'from qgis.utils import iface\n'
               f'dispatch(iface, "{operation}", "[% @layer_id %]", "[% "id" %]")')
    return QgsAction(Qgis.AttributeActionType.GenericPython, name, command, '', False, name,
                     {'Feature', 'AttributeTableRow', 'AttributeForm'})


class DigueDialog(QDialog):
    def __init__(self, iface, anchor, reference=None, revalidate=False):
        super().__init__(iface.mainWindow())
        self.setObjectName('digueReferenceDialog')
        self.setWindowTitle('Revalider le repère' if revalidate else
                            ('Modifier le repère' if reference else 'Créer une digue'))
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.iface, self.anchor, self.source = iface, anchor, anchor.source()
        self.store = store_for(anchor)
        self.reference = uid(reference) if reference else None
        self.original = self.store.reference(reference) if reference else None
        self.signature = None
        self.band = None
        layout = QFormLayout(self)
        self.name, self.external, self.comment = QLineEdit(self), QLineEdit(self), QLineEdit(self)
        for label, widget, index in (('Nom', self.name, 1), ('Identifiant externe', self.external, 2),
                                      ('Commentaire', self.comment, 3)):
            if self.original:
                value = self.original[index]
                widget.setText(value if isinstance(value, str) else '')
            widget.setReadOnly(revalidate)
            layout.addRow(label, widget)
        self.course = QComboBox(self)
        self.course.addItem('Choisir le cours d’eau support…', None)
        for identifier, label, qualified in self.store.courses():
            self.course.addItem(str(label)+('' if qualified else ' — non qualifié'), str(identifier))
        if self.original:
            self.course.setCurrentIndex(max(0, self.course.findData(str(self.original[5]))))
        self.bank = QComboBox(self)
        self.bank.addItem('Gauche', 'G')
        self.bank.addItem('Droite', 'D')
        if self.original:
            self.bank.setCurrentIndex(self.bank.findData(self.original[6]))
        self.start, self.end = QDoubleSpinBox(self), QDoubleSpinBox(self)
        self.initial_fractions = tuple(map(float, self.original[7:9])) if reference else (0.0, 1.0)
        self.dirty = [False, False]
        for index, (widget, value) in enumerate(zip((self.start, self.end), self.initial_fractions)):
            widget.setRange(0, 1)
            widget.setDecimals(15)
            widget.setValue(value)
            widget.valueChanged.connect(lambda _value, i=index: self.fraction_changed(i))
            widget.setEnabled(not revalidate)
        for label, widget in (('Cours d’eau', self.course), ('Rive', self.bank),
                              ('Fraction début', self.start), ('Fraction fin', self.end)):
            layout.addRow(label, widget)
        self.course.setEnabled(not revalidate)
        self.bank.setEnabled(not revalidate)
        self.course.currentIndexChanged.connect(self.invalidate)
        self.bank.currentIndexChanged.connect(self.invalidate)
        if self.original:
            layout.addRow('Référence actuelle', QLabel(f'Révision {self.original[9]} · {self.original[10]}'))
        self.status = QLabel('Examinez la géométrie calculée sur la carte, puis confirmez son enregistrement.')
        self.status.setWordWrap(True)
        layout.addRow(self.status)
        self.preview_button = QPushButton('Prévisualiser sur la carte')
        self.preview_button.clicked.connect(self.preview)
        layout.addRow(self.preview_button)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('Confirmer et enregistrer')
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        self.write_allowed = bool(self.store.permissions()[0])
        if not self.write_allowed:
            self.status.setText('Consultation uniquement : votre compte ne peut pas modifier les digues.')
        self.resize(570, 390)
        _dialogs.add(self)
        self.finished.connect(self.closed)

    def values(self):
        return tuple(widget.value() if self.dirty[i] else self.initial_fractions[i]
                     for i, widget in enumerate((self.start, self.end)))

    def fraction_changed(self, index):
        self.dirty[index] = True
        self.invalidate()

    def clear_preview(self):
        if self.band is not None:
            self.band.reset(QgsWkbTypes.LineGeometry)
            self.iface.mapCanvas().scene().removeItem(self.band)
            self.band = None

    def invalidate(self, *_):
        self.signature = None
        self.clear_preview()
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.status.setText('Prévisualisez à nouveau avant de confirmer.')

    def preview(self):
        self.invalidate()
        try:
            ensure_saved(self.anchor, self.source)
            if not self.course.currentData():
                raise ValueError('Choisissez un cours d’eau support.')
            wkt, signature = self.store.preview(self.course.currentData(), *self.values())
            geometry = QgsGeometry.fromWkt(wkt)
            length = geometry.length()
            crs = QgsCoordinateReferenceSystem('EPSG:3950')
            canvas = self.iface.mapCanvas()
            geometry.transform(QgsCoordinateTransform(crs, canvas.mapSettings().destinationCrs(), QgsProject.instance()))
            self.band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
            self.band.setColor(QColor(0, 155, 190, 220))
            self.band.setWidth(4)
            self.band.setToGeometry(geometry, None)
            canvas.setExtent(geometry.boundingBox().buffered(max(geometry.length() * .1, .0001)))
            canvas.refresh()
            self.signature = signature
            self.status.setText(f'Support qualifié · longueur proposée : {length:.2f} m. '
                                'Confirmer valide explicitement ce tracé.' if self.write_allowed else
                                f'Longueur proposée : {length:.2f} m · consultation uniquement.')
            self.buttons.button(QDialogButtonBox.Save).setEnabled(self.write_allowed)
        except Exception as exc:
            self.status.setText(str(exc))

    def save(self):
        try:
            ensure_saved(self.anchor, self.source)
            if not self.write_allowed or self.signature is None:
                raise ValueError('Prévisualisation qualifiée et droits Patrimoine requis.')
            attributes = [self.name.text(), self.external.text(), self.comment.text()]
            if self.original:
                for index, previous in enumerate(self.original[1:4]):
                    previous = previous if isinstance(previous, str) else None
                    if attributes[index] == (previous if previous is not None else ''):
                        attributes[index] = previous
            self.store.save(self.reference, self.original, *attributes,
                            self.course.currentData(), self.bank.currentData(),
                            *self.values(), self.signature)
            refresh(self.anchor)
            self.iface.messageBar().pushMessage('Digues', 'Digue et repère enregistrés.', level=Qgis.Success, duration=8)
            self.accept()
        except Exception as exc:
            self.invalidate()
            self.status.setText(f'Aucune modification enregistrée : {exc}')

    def closed(self, _result):
        self.clear_preview()
        _dialogs.discard(self)


def create(iface):
    try:
        active = iface.activeLayer()
        layers = [layer for layer in QgsProject.instance().mapLayers().values()
                  if layer.providerType() == 'postgres' and
                  QgsDataSourceUri(layer.source()).schema() == 'gemapi' and
                  QgsDataSourceUri(layer.source()).table() == 'digues_carte']
        if active in layers:
            anchor = active
        elif len(layers) == 1:
            anchor = layers[0]
        elif layers:
            labels = [f'{layer.name()} — {layer.id()}' for layer in layers]
            chosen, ok = QInputDialog.getItem(iface.mainWindow(), 'Créer une digue', 'Couche et connexion', labels, 0, False)
            if not ok:
                return None
            anchor = layers[labels.index(chosen)]
        else:
            raise ValueError('Chargez le projet GEMAPI-App avec sa couche digues_carte.')
        return dispatch(iface, 'create', anchor.id(), None)
    except Exception as exc:
        iface.messageBar().pushMessage('Digues', str(exc), level=Qgis.Warning, duration=15)


def dispatch(iface, operation, layer_id, reference):
    try:
        anchor = QgsProject.instance().mapLayer(layer_id)
        if anchor is None:
            raise ValueError('La couche des digues n’est plus chargée.')
        ensure_saved(anchor, anchor.source())
        if operation == 'delete':
            store = store_for(anchor)
            original = store.reference(reference)
            if not store.permissions()[1]:
                raise ValueError('Suppression réservée au profil Patrimoine.')
            count = store.dependencies(original[0])
            if count:
                raise ValueError(f'Suppression impossible : {count} prestation(s) rattachée(s) à cette digue.')
            source = anchor.source()
            if QMessageBox.question(iface.mainWindow(), 'Supprimer la digue',
                f'Supprimer définitivement « {original[1]} » et son repère ?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                ensure_saved(anchor, source)
                store.delete(reference, original)
                refresh(anchor)
                iface.messageBar().pushMessage('Digues', 'Digue et repère supprimés.', level=Qgis.Success, duration=8)
            return None
        if operation not in ('create', 'edit', 'revalidate'):
            raise ValueError('Action digue inconnue.')
        dialog = DigueDialog(iface, anchor, None if operation == 'create' else reference, operation == 'revalidate')
        dialog.show()
        return dialog
    except Exception as exc:
        iface.messageBar().pushMessage('Digues', str(exc), level=Qgis.Warning, duration=15)
        return None


def close_dialogs():
    for dialog in list(_dialogs):
        dialog.reject()
