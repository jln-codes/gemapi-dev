"""QGIS-EDIT-02: disposable PostGIS, real profile logins and native QGIS widgets.

QT_QPA_PLATFORM=offscreen /usr/bin/python3 plugin/tests/test_digues_edit.py
Optional argument: folder containing the staged demonstration QGZ.
"""
from concurrent.futures import ThreadPoolExecutor
import faulthandler
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from xml.etree import ElementTree as E
from test_mod_geom_project import local_document

import psycopg2
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication, QDialogButtonBox, QMessageBox, QPushButton
from qgis.core import (QgsApplication, QgsAttributeTableConfig, QgsDataSourceUri,
                       QgsFeatureRequest, QgsProject, QgsVectorLayer, QgsVectorLayerTools)
from qgis.gui import QgsAttributeDialog, QgsDualView, QgsGui, QgsAttributeEditorContext

from test_ah_sections_qgis import ROOT, database, uid, Interface
from test_postgres_integration import sql
sys.path.insert(0, str(ROOT/'plugin'))
from gemapi_app import ah_sections, digues

OUTPUT = ROOT/'build/qgis-tests/QGIS-EDIT-02'


class ConsultationTools(QgsVectorLayerTools):
    # Desktop supplies this context itself. In standalone widgets, relation
    # wrappers still require a non-null tools object, including in read mode.
    def addFeature(self, *args, **kwargs):return False
    def startEditing(self, *args, **kwargs):return False
    def stopEditing(self, *args, **kwargs):return False
    def saveEdits(self, *args, **kwargs):return False


def project_copy(source, target, port, user):
    root = local_document(source, str(port), user=user)
    # External background tiles are irrelevant to this database/UI recipe.
    # Keep the test independent of Internet availability.
    projectlayers = root.find('projectlayers')
    excluded = set()
    for layer in list(projectlayers):
        if layer.findtext('provider') != 'postgres':
            excluded.add(layer.findtext('id'))
            projectlayers.remove(layer)
    for parent in root.iter():
        for child in list(parent):
            if child.tag in ('layer-tree-layer', 'layer') and child.get('id') in excluded:
                parent.remove(child)
    E.ElementTree(root).write(target, encoding='utf-8', xml_declaration=True)
    return target


def expect_error(call, fragment=None):
    try:
        call()
    except Exception as exc:
        if fragment:
            assert fragment.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError('Opération refusée attendue')


def main():
    faulthandler.dump_traceback_later(300, repeat=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QgsApplication([], True)
    app.initQgis()
    QgsGui.editorWidgetRegistry().initEditors()
    project = QgsProject.instance()
    passed = []
    def ok(label):
        passed.append(label)
        print('PASS', label, flush=True)
    try:
        with database() as (container, port, db), tempfile.TemporaryDirectory(dir=OUTPUT) as tmp:
            db.set_client_encoding('UTF8')
            sql(container, (ROOT/'model/roles_milieux.sql').read_text())
            sql(container, 'CREATE ROLE edit02_reader LOGIN IN ROLE milieux_consultation; '
                           'CREATE ROLE edit02_prestations LOGIN IN ROLE milieux_prestations; '
                           'CREATE ROLE edit02_patrimoine LOGIN IN ROLE milieux_patrimoine;')
            def execute(query, params=()):
                try:
                    with db.cursor() as c:
                        c.execute(query, params)
                        result = c.fetchall() if c.description else None
                    db.commit()
                    return result
                except Exception:
                    db.rollback()
                    raise
            def snapshot():
                return execute("SELECT (SELECT md5(string_agg(to_jsonb(t)::text,'' ORDER BY id)) FROM gemapi.digues t),"
                               "(SELECT md5(string_agg(to_jsonb(t)::text,'' ORDER BY id)) FROM gemapi.digues_reperes t)")
            def role_layer(user, table='digues_carte'):
                uri = QgsDataSourceUri()
                uri.setConnection('127.0.0.1', str(port), 'postgres', user, '')
                uri.setDataSource('gemapi', table, 'geom' if table=='digues_carte' else '', '', 'id')
                layer = QgsVectorLayer(uri.uri(False), user, 'postgres')
                assert layer.isValid(), layer.error().summary()
                return layer
            writer = digues.store_for(role_layer('edit02_patrimoine'))
            signature = writer.preview(uid('R1'), .1, .6)[1]
            def save(store=writer, reference=None, original=None, name="Digue fictive d'essai \\ é ST_AsText(", course=None,
                     bank='G', start=.1, end=.6, sig=None):
                return store.save(reference, original, name, 'EXT-02', 'Commentaire é', course or uid('R1'),
                                  bank, start, end, sig if sig is not None else signature)
            reference = save()
            original = writer.reference(reference)
            parent = original[0]
            assert original[1] == "Digue fictive d'essai \\ é ST_AsText(" and original[9] == 1
            assert execute('SELECT modifie_par FROM gemapi.digues_reperes WHERE id=%s',(reference,))[0][0]=='edit02_patrimoine'
            ok('création atomique, attributs Unicode, identité PostgreSQL réelle')
            before = snapshot()
            expect_error(lambda: save(bank='X'))
            expect_error(lambda: execute("SELECT gemapi.digue_enregistrer(NULL,NULL,0,NULL,'Rollback',NULL,NULL,%s,'X',.1,.6,%s)",
                                        (uid('R1'),signature)), 'rive')
            expect_error(lambda: execute("INSERT INTO gemapi.digues(nom) VALUES ('sans repère')"), 'sans rep')
            assert snapshot()==before
            ok('contraintes, parent sans repère refusé, rollback sans orphelin')
            for user in ('edit02_reader','edit02_prestations'):
                store = digues.store_for(role_layer(user))
                assert store.permissions()==[False,False]
                assert store.reference(reference)[0]==parent
                expect_error(lambda: save(store=store), 'permission')
                expect_error(lambda: save(store,reference,original), 'permission')
                expect_error(lambda: store.delete(reference,original), 'permission')
                expect_error(lambda: store.rows("UPDATE gemapi.digues_reperes SET revision_validation=revision_validation+1"), 'permission')
            assert snapshot()==before
            ok('Lecture et Prestations : consultation, toutes écritures refusées')
            assert save(reference=reference,original=original,start=.2)==reference
            original=writer.reference(reference)
            assert original[0]==parent and original[7]==.2 and original[9]==2
            r2sig=writer.preview(uid('R2'),.2,.6)[1]
            save(reference=reference,original=original,course=uid('R2'),start=.2,sig=r2sig)
            original=writer.reference(reference)
            assert original[5]==uid('R2') and original[9]==3
            frozen=execute('SELECT ST_AsEWKB(geom_reference),geom_riviere_signature FROM gemapi.digues_reperes WHERE id=%s',(reference,))
            execute('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,7) WHERE id=%s',(uid('R2'),))
            expect_error(lambda: writer.preview(uid('R2'),.2,.6),'qualifié')
            expect_error(lambda: save(reference=reference,original=original,course=uid('R2'),start=.2,sig=r2sig),'non admissible')
            assert execute('SELECT ST_AsEWKB(geom_reference),geom_riviere_signature FROM gemapi.digues_reperes WHERE id=%s',(reference,))==frozen
            execute('UPDATE gemapi.cours_eau SET direction=TRUE WHERE id=%s',(uid('R2'),))
            expect_error(lambda: save(reference=reference,original=original,course=uid('R2'),start=.2,sig=r2sig),'chang')
            r2sig=writer.preview(uid('R2'),.2,.6)[1]
            save(reference=reference,original=original,course=uid('R2'),start=.2,sig=r2sig)
            original=writer.reference(reference)
            assert original[0]==parent and original[9]==4 and original[10]=='CONFORME'
            assert execute('SELECT ST_AsEWKB(geom_reference),geom_riviere_signature FROM gemapi.digues_reperes WHERE id=%s',(reference,))!=frozen
            ok('fractions, changement de cours, revalidation inchangée, UUID, signatures, qualification')
            for change in ['revision_validation=revision_validation+2','revision_validation=0',
                           "geom_riviere_signature='faux'",'debut_fraction=.3',"rive='D'",'id=gen_random_uuid()',
                           "geom_reference=ST_GeomFromText('LINESTRING(0 0,1 1)',3950)"]:
                expect_error(lambda change=change: execute('UPDATE gemapi.digues_reperes SET '+change+' WHERE id=%s',(reference,)))
            ok('révisions contrôlées, UUID immuables, géométrie et signature protégées')
            stale=original
            execute('UPDATE gemapi.digues SET commentaire=%s WHERE id=%s',('concurrent',parent))
            expect_error(lambda: save(reference=reference,original=stale),'chang')
            expect_error(lambda: writer.delete(reference,stale),'chang')
            original=writer.reference(reference)
            # Real concurrent sessions: one holds the parent, a second waits,
            # then detects the committed revision rather than losing that write.
            a=psycopg2.connect(host='127.0.0.1',port=port,dbname='postgres',user='edit02_patrimoine')
            b=psycopg2.connect(host='127.0.0.1',port=port,dbname='postgres',user='edit02_patrimoine')
            a.set_client_encoding('UTF8');b.set_client_encoding('UTF8')
            params=(parent,reference,original[9],original[4],original[1],original[2],original[3],uid('R2'),'G',.2,.6,r2sig)
            query='SELECT gemapi.digue_enregistrer('+','.join(['%s']*12)+')'
            try:
                with a.cursor() as c:c.execute(query,params)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    def concurrent_save():
                        with b.cursor() as c:
                            c.execute("SET statement_timeout='5s'")
                            c.execute(query,params)
                        b.commit()
                    future=pool.submit(concurrent_save)
                    time.sleep(.25)
                    assert not future.done()
                    a.commit()
                    expect_error(future.result,'chang')
                b.rollback()
                original=writer.reference(reference)
                assert original[9]==5
                # Locking the support prevents geometry/qualification changing
                # while the reviewed snapshot is accepted.
                with a.cursor() as c:c.execute(query,(parent,reference,original[9],original[4],*params[4:]))
                with b.cursor() as c:
                    c.execute("SET lock_timeout='150ms'")
                    expect_error(lambda: c.execute('UPDATE gemapi.cours_eau SET direction=NULL WHERE id=%s',(uid('R2'),)),'lock timeout')
                b.rollback();a.rollback()
                assert writer.reference(reference)[9]==5
            finally:a.close();b.close()
            ok('concurrence réelle : conflit de révision, parent, verrou support et rollback')
            unchanged=save(name='Digue attributs à conserver')
            unchanged_parent=writer.reference(unchanged)[0]
            execute("UPDATE gemapi.digues SET nom='  Nom existant  ',identifiant_externe='',commentaire=NULL WHERE id=%s",(unchanged_parent,))
            unchanged_original=writer.reference(unchanged)
            parent_before=execute('SELECT to_jsonb(d) FROM gemapi.digues d WHERE id=%s',(unchanged_parent,))
            writer.save(unchanged,unchanged_original,unchanged_original[1],'',None,uid('R1'),'G',.1,.6,signature)
            assert execute('SELECT to_jsonb(d) FROM gemapi.digues d WHERE id=%s',(unchanged_parent,))==parent_before
            assert writer.reference(unchanged)[9]==2
            writer.delete(unchanged,writer.reference(unchanged))
            ok('revalidation : attributs parent, espaces, chaîne vide, NULL et traçabilité parent conservés')
            # Clone an exclusively fictitious existing demonstration prestation.
            execute("INSERT INTO gemapi.prestations(id,intitule,type_prestation_id,prestataire_id,statut_id,technicien_id,"
                    "date_debut,date_fin,digue_id,geom) SELECT gen_random_uuid(),'Blocage fictif',type_prestation_id,"
                    "prestataire_id,statut_id,technicien_id,date_debut,date_fin,%s,"
                    "ST_GeomFromText('LINESTRING(1700000 9300000,1700100 9300000)',3950) FROM gemapi.prestations LIMIT 1",(parent,))
            original=writer.reference(reference)
            before=snapshot()
            expect_error(lambda: writer.delete(reference,original),'prestation')
            assert snapshot()==before and writer.dependencies(parent)==1
            # Keep that dependent object for UI refusal checks, remove another.
            free=save(name='Digue supprimable')
            freeoriginal=writer.reference(free)
            writer.delete(free,freeoriginal)
            expect_error(lambda: writer.reference(free),'existe plus')
            assert execute('SELECT count(*) FROM gemapi.digues WHERE id=%s',(freeoriginal[0],))[0][0]==0
            ok('suppression atomique et refus si prestation rattachée, aucune prestation supprimée')
            free=save(name='Digue concurrence suppression')
            freeoriginal=writer.reference(free)
            a=psycopg2.connect(host='127.0.0.1',port=port,dbname='postgres',user='edit02_patrimoine')
            b=psycopg2.connect(host='127.0.0.1',port=port,dbname='postgres',user='edit02_prestations')
            try:
                def pending_delete():
                    with a.cursor() as c:
                        c.execute('SELECT gemapi.digue_supprimer(%s,%s,%s)',(free,1,freeoriginal[4]))
                pending_delete();a.rollback()
                assert writer.reference(free)[9]==1
                pending_delete()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    def attach():
                        with b.cursor() as c:
                            c.execute("SET statement_timeout='5s'")
                            c.execute("INSERT INTO gemapi.prestations(id,type_prestation_id,prestataire_id,statut_id,technicien_id,"
                                      "date_debut,date_fin,digue_id,geom) SELECT gen_random_uuid(),type_prestation_id,prestataire_id,"
                                      "statut_id,technicien_id,date_debut,date_fin,%s,ST_GeomFromText("
                                      "'LINESTRING(1700000 9300000,1700100 9300000)',3950) FROM gemapi.prestations LIMIT 1",
                                      (freeoriginal[0],))
                        b.commit()
                    future=pool.submit(attach)
                    time.sleep(.25);assert not future.done()
                    a.commit();expect_error(future.result,'foreign key');b.rollback()
                assert writer.dependencies(freeoriginal[0])==0
                expect_error(lambda: writer.reference(free),'existe plus')
            finally:a.close();b.close()
            ok('suppression : rollback complet et FK concurrente sans prestation orpheline')
            iface=Interface()
            env = 'demo'
            source=(Path(sys.argv[1])/f'prestations_{env}.qgz') if len(sys.argv)>1 else ROOT/env/f'prestations_{env}.qgz'
            assert project.read(str(project_copy(source,Path(tmp)/(env+'.qgs'),port,'edit02_patrimoine')))
            assert len([l for l in project.mapLayers().values() if l.providerType()=='postgres'])==32
            assert len(project.relationManager().relations())==70
            layers={QgsDataSourceUri(l.source()).table():l for l in project.mapLayers().values() if l.providerType()=='postgres'}
            anchor=layers['digues_carte']
            assert anchor.readOnly()
            iface.canvas.setDestinationCrs(anchor.crs())
            for key,number in [('ah',2),('digues_carte',3)]:
                print('UI',env,key,'table et formulaire',flush=True)
                layer=layers[key]
                assert len(layer.actions().actions())==number
                assert layer.attributeTableConfig().actionWidgetVisible()
                assert all(a.actionScopes()=={'Feature','AttributeTableRow','AttributeForm'} for a in layer.actions().actions())
                context=QgsAttributeEditorContext()
                tools=ConsultationTools()
                context.setVectorLayerTools(tools)
                dual=QgsDualView()
                dual.init(layer,iface.canvas,QgsFeatureRequest(),context)
                dual.setView(QgsDualView.AttributeTable)
                assert any(dual.tableView().model().headerData(i,Qt.Horizontal)=='Actions'
                           for i in range(dual.tableView().model().columnCount()))
                sip.delete(dual)
                feature=next(layer.getFeatures())
                native=QgsAttributeDialog(layer,feature,False,context=context)
                native.show();QApplication.processEvents()
                buttons=[w for w in native.findChildren(QPushButton) if w.text() in [a.name() for a in layer.actions().actions()]]
                assert len(buttons)==number, [(w.text()) for w in native.findChildren(QPushButton)]
                native.close();sip.delete(native);QApplication.processEvents()
                # QgsApplication alone has no Desktop Python action runner.
                # Check native button configuration here and call its entry
                # point; real saved commands are clicked in Desktop QA.
                if key=='ah':
                    ah_sections.dispatch(iface,'create',layer.id(),feature['id'])
                else:
                    digues.dispatch(iface,'edit',layer.id(),feature['id'])
                dialogs=ah_sections._dialogs if key=='ah' else digues._dialogs
                assert len(dialogs)==1
                next(iter(dialogs)).reject();QApplication.processEvents()
                print('UI',env,key,'validé',flush=True)
            assert not layers['ah_stockage'].actions().actions() and not layers['ah_ouvrage'].actions().actions()
            dialog=digues.dispatch(iface,'create',anchor.id(),None)
            assert dialog and not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
            dialog.name.setText('Création UI '+env)
            dialog.external.setText('UI-02');dialog.comment.setText('Fictif')
            dialog.course.setCurrentIndex(dialog.course.findData(uid('R1')))
            dialog.preview();assert dialog.signature and dialog.band
            before=snapshot();dialog.reject();QApplication.processEvents();assert snapshot()==before
            dialog=digues.dispatch(iface,'create',anchor.id(),None)
            dialog.name.setText('Création UI '+env)
            dialog.course.setCurrentIndex(dialog.course.findData(uid('R1')))
            dialog.preview();dialog.save();QApplication.processEvents()
            saved=writer.rows(f"SELECT s.id::text FROM gemapi.digues_reperes s JOIN gemapi.digues d ON d.id=s.digue_id WHERE d.nom='Création UI {env}'")[0][0]
            dialog=digues.dispatch(iface,'edit',anchor.id(),saved)
            dialog.start.setValue(.25);dialog.preview();assert dialog.signature
            dialog.end.setValue(.9);assert dialog.signature is None and dialog.band is None
            dialog.preview();dialog.save();QApplication.processEvents()
            assert writer.reference(saved)[7:10]==[.25,.9,2]
            saved_parent=writer.reference(saved)[0]
            execute("UPDATE gemapi.digues SET identifiant_externe=NULL,commentaire='' WHERE id=%s",(saved_parent,))
            ui_parent_before=execute('SELECT to_jsonb(d) FROM gemapi.digues d WHERE id=%s',(saved_parent,))
            dialog=digues.dispatch(iface,'revalidate',anchor.id(),saved)
            assert not dialog.start.isEnabled() and not dialog.course.isEnabled()
            dialog.preview();dialog.save();QApplication.processEvents()
            assert writer.reference(saved)[9]==3
            assert execute('SELECT to_jsonb(d) FROM gemapi.digues d WHERE id=%s',(saved_parent,))==ui_parent_before
            with patch.object(QMessageBox,'question',return_value=QMessageBox.No):
                digues.dispatch(iface,'delete',anchor.id(),saved)
            assert writer.reference(saved)[9]==3
            with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
                digues.dispatch(iface,'delete',anchor.id(),saved)
            expect_error(lambda: writer.reference(saved),'existe plus')
            with patch.object(QMessageBox,'question',side_effect=AssertionError('Pas de confirmation si prestation')):
                digues.dispatch(iface,'delete',anchor.id(),reference)
            assert writer.dependencies(parent)==1
            assert layers['digues'].startEditing()
            assert digues.dispatch(iface,'edit',anchor.id(),reference) is None
            layers['digues'].rollBack()
            for user in ('edit02_reader','edit02_prestations'):
                reader=role_layer(user);project.addMapLayer(reader)
                dialog=digues.dispatch(iface,'edit',reader.id(),reference)
                assert dialog and not dialog.write_allowed
                dialog.preview();assert dialog.signature
                assert not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
                dialog.save();dialog.reject();QApplication.processEvents()
            ok(env+' : tables Actions, boutons de fiche AH/digue, création/modification/revalidation/suppression UI et droits')
            ah_sections.close_dialogs();digues.close_dialogs();QApplication.processEvents();project.clear()
            iface.window.close()
            ok('QGZ : 32 couches PostgreSQL, 70 relations, aucune action ajoutée aux enfants AH')
        (OUTPUT/'results.txt').write_text('\n'.join(passed)+'\n',encoding='utf-8')
        print(f'QGIS-EDIT-02 : {len(passed)} groupes de validations réussis',flush=True)
    finally:
        ah_sections.close_dialogs();digues.close_dialogs();project.clear();app.exitQgis()
        faulthandler.cancel_dump_traceback_later()


if __name__=='__main__':main()
