"""Explicit QGIS layer actions for AH hydrographic references.

Native forms and Relation Editor remain responsible for consultation. These
operations are independent, confirmed server writes, never attribute-form saves.
"""
from math import isfinite
from uuid import UUID

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QComboBox,
                                QFormLayout, QLabel, QMessageBox, QPushButton)
from qgis.core import (Qgis, QgsAction, QgsDataSourceUri, QgsFeature,
                       QgsField, QgsGeometry, QgsProject,
                       QgsProviderRegistry, QgsSingleSymbolRenderer, QgsAttributeTableConfig,
                       QgsLineSymbol, QgsVectorLayer)


from .watercourse_labels import label_sql, labels_query_sql


TEMPORARY = 'gemapi_app/ah_proposal'
ANCHOR = 'gemapi_app/ah_anchor'
_dialogs = set()


def uid(value):
    return str(UUID(str(value)))


def fractions(start, end):
    start, end = float(start), float(end)
    if not (isfinite(start) and isfinite(end) and 0 <= start < end <= 1):
        raise ValueError('Fractions attendues : 0 ≤ début < fin ≤ 1.')
    return repr(start), repr(end)


class SectionStore:
    """Small SQL boundary. Every write is one atomic PostgreSQL statement."""
    def __init__(self, connection):
        self.connection = connection
        schema=connection.executeSql("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n "
                                     "ON n.oid=e.extnamespace WHERE e.extname='postgis'")[0][0]
        self.postgis='"'+str(schema).replace('"','""')+'"'

    def rows(self, query):
        for function in ('ST_Length','ST_AsText','ST_LineSubstring','ST_GeometryN','ST_AsEWKB'):
            query=query.replace(function+'(',self.postgis+'.'+function+'(')
        return self.connection.executeSql(query)

    def permissions(self):
        return self.rows("SELECT has_table_privilege('gemapi.ah_sections','INSERT'),"
                         "has_table_privilege('gemapi.ah_sections','UPDATE'),"
                         "has_table_privilege('gemapi.ah_sections','DELETE'),"
                         "has_any_column_privilege('gemapi.cours_eau','UPDATE')")[0]

    def courses(self):
        return self.rows(labels_query_sql(qualifications=True))

    def candidates(self, parent):
        return self.rows(f"SELECT c.cours_eau_id::text,{label_sql('r')},"
                         "c.debut_fraction,c.fin_fraction,ST_Length(c.geom_reference),"
                         "c.geom_cours_eau_signature,ST_AsText(c.geom_reference) "
                         f"FROM gemapi.ah_sections_candidates('{uid(parent)}') c "
                         "JOIN gemapi.cours_eau r ON r.id=c.cours_eau_id "
                         "ORDER BY c.cours_eau_id,c.debut_fraction,c.fin_fraction")

    def parent_name(self,parent):
        rows=self.rows(f"SELECT nom FROM gemapi.ah WHERE id='{uid(parent)}'")
        if not rows:
            raise ValueError('Le parent AH n’existe plus.')
        return str(rows[0][0])

    def reference(self, reference):
        rows = self.rows(f"SELECT s.ah_id::text,s.cours_eau_id::text,{label_sql('r')},"
                         "s.debut_fraction,s.fin_fraction,s.revision_validation,v.etat_reference "
                         "FROM gemapi.ah_sections s JOIN gemapi.ah_sections_carte v ON v.id=s.id "
                         "JOIN gemapi.cours_eau r ON r.id=s.cours_eau_id "
                         f"WHERE s.id='{uid(reference)}'")
        if not rows:
            raise ValueError('Cette section n’existe plus. Actualisez la fiche AH.')
        return rows[0]

    def preview(self, parent, course, start, end):
        start, end = fractions(start, end)
        rows = self.rows("SELECT ST_AsText(ST_LineSubstring(ST_GeometryN(geom,1),"
                         f"{start},{end})),md5(encode(ST_AsEWKB(geom),'hex')) "
                         f"FROM gemapi.cours_eau WHERE id='{uid(course)}' "
                         "AND gemapi.cours_eau_reference_admissible(geom,direction,valide)")
        if not rows:
            raise ValueError('Cours d’eau non qualifié ou inexploitable. Aucune validation possible.')
        return rows[0]

    @staticmethod
    def support(parent, course, signature):
        # Lock and recheck the exact support reviewed by the user. A stale
        # candidate cannot silently turn into a different accepted geometry.
        if len(str(signature))!=32 or any(c not in '0123456789abcdef' for c in str(signature)):
            raise ValueError('Signature de référence incorrecte.')
        return ("WITH support AS MATERIALIZED (SELECT r.id FROM gemapi.cours_eau r "
                f"WHERE r.id='{uid(course)}' AND md5(encode(ST_AsEWKB(r.geom),'hex'))='{signature}' "
                "AND gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide) FOR SHARE OF r) ")

    def accept(self, parent, course, start, end, signature):
        start, end = fractions(start, end)
        rows = self.rows(self.support(parent, course, signature) +
                         "INSERT INTO gemapi.ah_sections(ah_id,cours_eau_id,debut_fraction,fin_fraction) "
                         f"SELECT '{uid(parent)}',id,{start},{end} FROM support RETURNING id::text")
        if not rows:
            raise ValueError('Le cours d’eau a changé ou n’est plus qualifié. Prévisualisez à nouveau la section.')
        return str(rows[0][0])

    def revalidate(self, reference, course, revision, start, end, signature):
        start, end = fractions(start, end)
        parent=self.reference(reference)[0]
        rows = self.rows(self.support(parent, course, signature) +
                         "UPDATE gemapi.ah_sections s SET "
                         f"cours_eau_id=r.id,debut_fraction={start},fin_fraction={end},revision_validation=s.revision_validation+1 "
                         f"FROM support r WHERE s.id='{uid(reference)}' AND s.ah_id='{uid(parent)}' "
                         f"AND s.revision_validation={int(revision)} RETURNING s.id::text")
        if not rows:
            raise ValueError('La section ou le cours d’eau a changé. Rechargez la référence avant de revalider.')
        return str(rows[0][0])

    def delete(self, reference, revision):
        try:
            rows = self.rows("DELETE FROM gemapi.ah_sections "
                             f"WHERE id='{uid(reference)}' AND revision_validation={int(revision)} RETURNING id::text")
        except Exception as error:
            if 'prestations_ah_section_id_fkey' in str(error):
                raise ValueError('Cette section est encore rattachée à des prestations. '
                                 'Réaffectez-les explicitement avant de supprimer la section.') from None
            raise
        if not rows:
            raise ValueError('La section a changé. Actualisez avant de la supprimer.')


def store_for(layer):
    if layer is None or layer.providerType() != 'postgres':
        raise ValueError('La couche PostgreSQL AH est indisponible.')
    uri = QgsDataSourceUri(layer.source())
    if uri.schema() != 'gemapi' or uri.table() not in ('ah','ah_sections','ah_sections_carte'):
        raise ValueError('Cette action nécessite une couche AH du schéma gemapi.')
    return SectionStore(QgsProviderRegistry.instance().providerMetadata('postgres').createConnection(
        uri.connectionInfo(False), {}))


def ensure_saved(anchor):
    """Do not bypass a QGIS edit buffer or validate somebody else's edits."""
    if anchor is None:
        raise ValueError('La couche AH n’est plus chargée.')
    connection = QgsDataSourceUri(anchor.source()).connectionInfo(False)
    for layer in QgsProject.instance().mapLayers().values():
        if layer.providerType() == 'postgres' and layer.isEditable():
            uri = QgsDataSourceUri(layer.source())
            if uri.connectionInfo(False) == connection:
                raise ValueError('Terminez ou annulez la session d’édition PostgreSQL avant cette opération. '
                                 'Cette action enregistre une référence séparément du formulaire AH.')


def refresh(anchor):
    connection = QgsDataSourceUri(anchor.source()).connectionInfo(False)
    for layer in QgsProject.instance().mapLayers().values():
        if layer.providerType() == 'postgres':
            uri = QgsDataSourceUri(layer.source())
            if uri.connectionInfo(False) == connection and uri.table() in ('ah_sections','ah_sections_carte'):
                layer.reload()
                layer.triggerRepaint()


def action(name, function, field='id'):
    expression=field if field=='$id' else '"'+field+'"'
    command = (f'from gemapi_app.ah_sections import dispatch\n'
               'from qgis.utils import iface\n'
               f'dispatch(iface, "{function}", "[% @layer_id %]", "[% {expression} %]")')
    return QgsAction(Qgis.AttributeActionType.GenericPython, name, command, '', False, name,
                     {'Feature','AttributeTableRow','AttributeForm'})


def zoom(iface, layer, fid):
    iface.mapCanvas().zoomToFeatureIds(layer, [int(fid)])
    iface.mapCanvas().refresh()


def temporary_layer(anchor, name, features, preview=False):
    layer = QgsVectorLayer('LineString?crs=EPSG:3950',name,'memory')
    fields = [QgsField('ah_id',QVariant.String),QgsField('cours_eau_id',QVariant.String),
              QgsField('cours_eau',QVariant.String),QgsField('debut_fraction',QVariant.Double),
              QgsField('fin_fraction',QVariant.Double),QgsField('longueur_m',QVariant.Double),
              QgsField('qualification',QVariant.String),QgsField('signature',QVariant.String)]
    provider = layer.dataProvider()
    provider.addAttributes(fields)
    layer.updateFields()
    for parent,course,name,start,end,length,signature,wkt in features:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([parent,course,name,start,end,length,'Qualifié',signature])
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        if not provider.addFeatures([feature])[0]:
            raise ValueError('Impossible de préparer la prévisualisation cartographique.')
    layer.setReadOnly(True)
    layer.setCustomProperty(TEMPORARY,True)
    layer.setCustomProperty(ANCHOR,anchor.id())
    layer.setCustomProperty('skipMemoryLayersCheck',1)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
        {'line_color':'0,155,190,255','line_width':'1.1','line_style':'dash','offset':'0'})))
    for field in ('ah_id','cours_eau_id','signature'):
        from qgis.core import QgsEditorWidgetSetup
        layer.setEditorWidgetSetup(layer.fields().indexFromName(field),QgsEditorWidgetSetup('Hidden',{}))
    layer.setDisplayExpression('"cours_eau"')
    layer.actions().addAction(action('Localiser la proposition','zoom','$id'))
    if not preview:
        layer.actions().addAction(action('Accepter cette proposition…','accept','$id'))
        layer.actions().addAction(action('Refuser cette proposition','refuse','$id'))
    config=layer.attributeTableConfig()
    config.update(layer.fields())
    columns=config.columns()
    for column in columns:
        column.hidden=column.type!=QgsAttributeTableConfig.Action and column.name in ('ah_id','cours_eau_id','signature')
        column.width=170 if column.type==QgsAttributeTableConfig.Action else (235 if column.name=='cours_eau' else 115)
    config.setColumns(columns)
    config.setActionWidgetStyle(QgsAttributeTableConfig.DropDown)
    config.setActionWidgetVisible(True)
    layer.setAttributeTableConfig(config)
    QgsProject.instance().addMapLayer(layer)
    return layer


def open_candidates(iface, anchor, parent):
    parent = uid(parent)
    store = store_for(anchor)
    rows = store.candidates(parent)
    # Reopening the search replaces only the previous proposals of this AH.
    for layer in list(QgsProject.instance().mapLayers().values()):
        if layer.customProperty(TEMPORARY,False) and layer.customProperty(ANCHOR)==anchor.id() and layer.customProperty('ah_id')==parent:
            QgsProject.instance().removeMapLayer(layer.id())
    if not rows:
        iface.messageBar().pushMessage('Sections AH',
            'Aucune section candidate sur un cours d’eau qualifié. Le stockage peut ne pas intersecter '
            'de support admissible ; aucune orientation n’a été modifiée.',level=Qgis.Info,duration=12)
        return None
    features = [(parent,*row[:5],row[5],row[6]) for row in rows]
    layer = temporary_layer(anchor,'Propositions '+store.parent_name(parent)+' — à examiner',features)
    layer.setCustomProperty('ah_id',parent)
    iface.showAttributeTable(layer)
    iface.mapCanvas().setExtent(layer.extent().buffered(30))
    iface.mapCanvas().refresh()
    return layer


class ReferenceDialog(QDialog):
    """Confirm a single section. Closing/cancelling never writes anything."""
    def __init__(self, iface, anchor, parent, course, name, start, end,
                 signature=None, reference=None, revision=None, candidate=None, state='Proposition'):
        super().__init__(iface.mainWindow())
        self.setObjectName('ahSectionReferenceDialog')
        self.setWindowTitle('Revalider une section AH' if reference else 'Accepter une section AH')
        self.iface,self.anchor,self.parent_id,self.course=iface,anchor,uid(parent),uid(course) if course else None
        self.anchor_id=anchor.id()
        self.store=store_for(anchor)
        self.reference,self.revision,self.candidate=reference,revision,candidate
        self.signature=signature
        self.preview_layer=None
        self.original=(float(start),float(end))
        self.dirty=[False,False]
        self.setAttribute(Qt.WA_DeleteOnClose)
        layout=QFormLayout(self)
        layout.addRow('AH parent',QLabel(self.store.parent_name(parent)))
        self.courseCombo=QComboBox(self)
        self.courseCombo.addItem('Choisir le cours d’eau support…',None)
        for course_id,course_name,qualified in self.store.courses():
            self.courseCombo.addItem(str(course_name)+('' if qualified else ' — non qualifié'),course_id)
        self.courseCombo.setCurrentIndex(max(0,self.courseCombo.findData(self.course)))
        self.courseCombo.currentIndexChanged.connect(self.course_changed)
        layout.addRow('Cours d’eau',self.courseCombo)
        layout.addRow('État',QLabel(state))
        self.start,self.end=QDoubleSpinBox(self),QDoubleSpinBox(self)
        for index,(widget,value) in enumerate(zip((self.start,self.end),self.original)):
            widget.setRange(0,1)
            widget.setDecimals(15)
            widget.setValue(value)
            widget.setObjectName('debutFraction' if index==0 else 'finFraction')
            widget.valueChanged.connect(lambda _value,i=index:self.changed(i))
        layout.addRow('Fraction début',self.start)
        layout.addRow('Fraction fin',self.end)
        self.status=QLabel('Examinez la section sur la carte avant de confirmer.')
        self.status.setWordWrap(True)
        layout.addRow(self.status)
        self.preview_button=QPushButton('Prévisualiser la section sur la carte')
        self.preview_button.setObjectName('previewSectionButton')
        self.preview_button.clicked.connect(self.preview)
        layout.addRow(self.preview_button)
        notice=QLabel('Enregistrer constitue une décision explicite et définitive sur le serveur. '
                     'Annuler ferme cette fenêtre sans enregistrer. Cette opération est indépendante '
                     'de la fermeture de la fiche AH.')
        notice.setWordWrap(True)
        layout.addRow(notice)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('Enregistrer la référence sur le serveur')
        self.buttons.button(QDialogButtonBox.Save).setObjectName('saveSectionButton')
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        permission=self.store.permissions()
        self.write_allowed=bool(permission[1 if reference else 0] and permission[3])
        if not self.write_allowed:
            self.status.setText('Consultation autorisée ; droits de validation insuffisants.')
        self.buttons.button(QDialogButtonBox.Save).setEnabled(self.write_allowed and signature is not None)
        self.resize(590,360)
        _dialogs.add(self)
        self.finished.connect(self.closed)

    def course_changed(self,index):
        self.course=self.courseCombo.itemData(index)
        self.signature=None
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.status.setText('Support modifié : prévisualisez avant de valider.')

    def changed(self,index):
        self.dirty[index]=True
        self.signature=None
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.status.setText('Fractions modifiées : prévisualisez avant de valider.')

    def values(self):
        return tuple(widget.value() if self.dirty[i] else self.original[i]
                     for i,widget in enumerate((self.start,self.end)))

    def preview(self):
        try:
            if not self.course:
                raise ValueError('Choisissez un cours d’eau support.')
            start,end=self.values()
            wkt,signature=self.store.preview(self.parent_id,self.course,start,end)
            if self.preview_layer:
                QgsProject.instance().removeMapLayer(self.preview_layer.id())
            geom=QgsGeometry.fromWkt(wkt)
            self.preview_layer=temporary_layer(self.anchor,'Prévisualisation AH — non enregistrée',
                [(self.parent_id,self.course,'Prévisualisation',start,end,geom.length(),signature,wkt)],True)
            zoom(self.iface,self.preview_layer,next(self.preview_layer.getFeatures()).id())
            self.signature=signature
            self.status.setText(f'Cours d’eau qualifié · longueur proposée : {geom.length():.2f} m.')
            self.buttons.button(QDialogButtonBox.Save).setEnabled(self.write_allowed)
        except Exception as exc:
            self.signature=None
            self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
            self.status.setText(str(exc))

    def save(self):
        try:
            if QgsProject.instance().mapLayer(self.anchor_id) is not self.anchor:
                raise ValueError('Le projet ou la couche AH a été fermé.')
            ensure_saved(self.anchor)
            if not self.write_allowed or self.signature is None:
                raise ValueError('Prévisualisation qualifiée et droits d’écriture requis.')
            start,end=self.values()
            if self.reference:
                self.store.revalidate(self.reference,self.course,self.revision,start,end,self.signature)
            else:
                self.store.accept(self.parent_id,self.course,start,end,self.signature)
            refresh(self.anchor)
            if self.candidate:
                layer,fid=self.candidate
                layer.dataProvider().deleteFeatures([fid])
                layer.updateExtents()
                layer.reload()
                layer.triggerRepaint()
            self.iface.messageBar().pushMessage('Sections AH','Référence enregistrée sur le serveur.',level=Qgis.Success,duration=8)
            self.accept()
        except Exception as exc:
            self.status.setText(f'Référence non enregistrée : {exc}')

    def closed(self,_result):
        if self.preview_layer:
            from qgis.PyQt import sip
            if not sip.isdeleted(self.preview_layer):
                QgsProject.instance().removeMapLayer(self.preview_layer.id())
        _dialogs.discard(self)


def dispatch(iface, operation, layer_id, value):
    """Entry point invoked only by explicit native QGIS layer actions."""
    try:
        layer=QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            raise ValueError('La couche n’est plus chargée.')
        if operation=='candidates':
            return open_candidates(iface,layer,value)
        if operation in ('accept','refuse','zoom'):
            if not layer.customProperty(TEMPORARY,False):
                raise ValueError('Cette action nécessite une proposition temporaire.')
            fid=int(value)
            feature=layer.getFeature(fid)
            if not feature.isValid():
                raise ValueError('La proposition n’existe plus.')
            if operation=='zoom':
                return zoom(iface,layer,fid)
            if operation=='refuse':
                layer.dataProvider().deleteFeatures([fid])
                layer.updateExtents()
                layer.reload()
                layer.triggerRepaint()
                return
            anchor=QgsProject.instance().mapLayer(layer.customProperty(ANCHOR))
            if uid(feature['ah_id'])!=uid(layer.customProperty('ah_id')):
                raise ValueError('La proposition ne correspond pas au parent AH sélectionné.')
            ensure_saved(anchor)
            dialog=ReferenceDialog(iface,anchor,feature['ah_id'],feature['cours_eau_id'],feature['cours_eau'],
                feature['debut_fraction'],feature['fin_fraction'],feature['signature'],candidate=(layer,fid))
        elif operation=='edit':
            ensure_saved(layer)
            parent,course,name,start,end,revision,state=store_for(layer).reference(value)
            dialog=ReferenceDialog(iface,layer,parent,course,name,start,end,reference=uid(value),revision=revision,state=state)
        elif operation=='create':
            ensure_saved(layer)
            dialog=ReferenceDialog(iface,layer,value,None,'',0,1,state='Nouvelle section manuelle')
        elif operation=='delete':
            ensure_saved(layer)
            store=store_for(layer)
            parent,course,name,start,end,revision,state=store.reference(value)
            if QMessageBox.question(iface.mainWindow(),'Supprimer un référencement AH',
                f'Supprimer la section {name} ({start:.6f}–{end:.6f}) ?\n'
                'Le parent AH et le cours d’eau seront conservés. Cette suppression est définitive.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
                store.delete(value,revision)
                refresh(layer)
            return
        else:
            raise ValueError('Action de référencement inconnue.')
        dialog.show()
        return dialog
    except Exception as exc:
        iface.messageBar().pushMessage('Sections AH',str(exc),level=Qgis.Warning,duration=15)
        return None


def close_dialogs():
    """Plugin unload: cancel open confirmations, never commit any layer."""
    for dialog in list(_dialogs):
        dialog.reject()
    for layer in list(QgsProject.instance().mapLayers().values()):
        if layer.customProperty(TEMPORARY,False):
            QgsProject.instance().removeMapLayer(layer.id())
