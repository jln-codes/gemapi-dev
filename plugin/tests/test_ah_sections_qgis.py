"""Native AH forms/actions on fictitious disposable PostGIS only.

QT_QPA_PLATFORM=offscreen /usr/bin/python3 plugin/tests/test_ah_sections_qgis.py
Shared setup is also used by the QGIS Desktop validation driver.
"""
from contextlib import contextmanager
import json
import os
import sys
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5, uuid4
from xml.etree import ElementTree as E

import psycopg2
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMainWindow, QApplication, QDialogButtonBox
from qgis.core import QgsApplication, QgsDataSourceUri, QgsFeatureRequest, QgsProject
from qgis.gui import QgsMapCanvas, QgsMessageBar, QgsAttributeDialog, QgsRelationEditorWidget, QgsGui

from test_postgres_integration import ROOT,command,load_demo_rows,sql
sys.path.insert(0,str(ROOT/'plugin'))
from gemapi_app import ah_sections as actions

OUTPUT=ROOT/'build/qgis-tests/hydro_ah_05'

def uid(name):
    return str(uuid5(NAMESPACE_URL,'sig-prestations/HYDRO-AH-05/'+name))


@contextmanager
def database():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    name='sig-ah05-'+uuid4().hex[:9]
    command('docker','run','--rm','-d','--name',name,'-p','127.0.0.1::5432',
            '-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_DB=seed','postgis/postgis:16-3.4')
    try:
        for _ in range(60):
            if command('docker','exec',name,'pg_isready','-h','127.0.0.1','-U','postgres',check=False).returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError('PostGIS local indisponible')
        sql(name,'CREATE SCHEMA extensions;CREATE EXTENSION postgis WITH SCHEMA extensions;')
        sql(name,(ROOT/'model/schema_v0.6.1.sql').read_text())
        load_demo_rows(name)
        sql(name,'ALTER DATABASE postgres SET search_path=pg_catalog,public,extensions,gemapi;')
        port=command('docker','port',name,'5432').stdout.strip().rsplit(':',1)[-1]
        db=psycopg2.connect(host='127.0.0.1',port=port,dbname='postgres',user='postgres')
        try:
            with db.cursor() as c:
                c.execute('SET search_path=pg_catalog,extensions,gemapi')
                for parent in ('A','B'):
                    c.execute('INSERT INTO gemapi.ah(id,nom) VALUES (%s,%s)',(uid(parent),'AH fictif '+parent))
                for river,y,direction in [('R1',0,True),('R2',20,True),('NON_QUALIFIE',10,None)]:
                    c.execute("INSERT INTO gemapi.cours_eau(id,nom,source,direction,geom) VALUES (%s,%s,'TEST AH05',%s,"
                              "ST_Translate(ST_GeomFromText(%s,3950),1700000,9300000))",
                              (uid(river),'Cours d’eau fictif '+river,direction,f'MULTILINESTRING((0 {y},1000 {y}))'))
                for parent,wkt in [('A','POLYGON((100 -10,200 -10,200 40,400 40,400 -10,500 -10,500 60,100 60,100 -10))'),
                                   ('B','POLYGON((150 -10,450 -10,450 50,150 50,150 -10))')]:
                    c.execute('INSERT INTO gemapi.ah_stockage(ah_id,nom,geom) VALUES (%s,%s,ST_Translate(ST_GeomFromText(%s,3950),1700000,9300000))',
                              (uid(parent),'Stockage fictif '+parent,wkt))
                c.execute('INSERT INTO gemapi.ah_sections(id,ah_id,cours_eau_id,debut_fraction,fin_fraction) VALUES (%s,%s,%s,.1,.2)',
                          (uid('reference'),uid('A'),uid('R1')))
                c.execute('CREATE ROLE ah05_reader LOGIN; GRANT USAGE ON SCHEMA gemapi,extensions TO ah05_reader; '
                          'GRANT SELECT ON ALL TABLES IN SCHEMA gemapi TO ah05_reader;')
            db.commit()
            yield name,port,db
        finally:db.close()
    finally:command('docker','stop',name,check=False)


def project_copy(port, target=None, user='postgres'):
    target=target or OUTPUT/'local.qgs'
    from test_mod_geom_project import local_document
    r = local_document(ROOT / 'demo/prestations_demo.qgz', str(port), user=user)
    E.ElementTree(r).write(target,encoding='utf-8',xml_declaration=True)
    return target


class Interface:
    def __init__(self):
        self.window=QMainWindow()
        self.canvas=QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.bar=QgsMessageBar(self.window)
        self.tables=[]
    def mainWindow(self):return self.window
    def mapCanvas(self):return self.canvas
    def messageBar(self):return self.bar
    def showAttributeTable(self,layer):
        self.tables.append(layer)


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    os.chdir(OUTPUT)
    app=QgsApplication([],True);app.initQgis()
    # QGIS Desktop registers these itself. A standalone QgsApplication does not.
    QgsGui.editorWidgetRegistry().initEditors()
    passed=[]
    try:
        with database() as (container,port,db):
            project=QgsProject.instance();assert project.read(str(project_copy(port)))
            anchor=next(l for l in project.mapLayers().values() if l.name()=='ah')
            store=actions.store_for(anchor)
            assert len(store.candidates(uid('A')))==4
            assert len(store.candidates(uid('B')))==2
            passed.append('server_candidates_multiple_rivers_disjoint')
            # Configuration is supplied by the project, not a startup hook.
            relations=project.relationManager().referencedRelations(anchor)
            assert any(r.id()=='ah05_carte_ah' for r in relations)
            iface=Interface()
            layer=actions.open_candidates(iface,anchor,uid('A'))
            assert layer.featureCount()==4 and layer.readOnly()
            assert all(f['qualification']=='Qualifié' for f in layer.getFeatures())
            passed.append('readonly_native_candidate_table')
            candidate=next(f for f in layer.getFeatures() if f['cours_eau_id']==uid('R1') and f['debut_fraction']==.4)
            def count():
                return int(store.rows('SELECT count(*) FROM gemapi.ah_sections')[0][0])
            before=count()
            dialog=actions.dispatch(iface,'accept',layer.id(),str(candidate.id()))
            assert dialog and dialog.isVisible()
            dialog.reject();QApplication.processEvents()
            assert count()==before
            passed.append('cancel_confirmation_no_write')
            dialog=actions.dispatch(iface,'accept',layer.id(),str(candidate.id()))
            assert dialog
            dialog.save();QApplication.processEvents()
            assert count()==before+1,dialog.status.text() if dialog.isVisible() else 'save'
            passed.append('explicit_accept_server_geometry')
            try:store.accept(uid('A'),uid('R1'),.4,.5,candidate['signature'])
            except Exception:pass
            else:raise AssertionError('Duplicate reference accepted')
            assert count()==before+1
            passed.append('sql_constraint_refusal_no_partial_write')
            row=store.rows(f"SELECT id::text FROM gemapi.ah_sections WHERE ah_id='{uid('A')}' AND debut_fraction=.4")[0][0]
            original=store.rows(f"SELECT ST_AsText(geom_reference),geom_cours_eau_signature FROM gemapi.ah_sections WHERE id='{row}'")[0]
            def data_snapshot():
                return [store.rows(f"SELECT md5(string_agg(to_jsonb(t)::text,'' ORDER BY id)) FROM gemapi.{table} t")[0][0]
                        for table in ('ah','ah_stockage','cours_eau','ah_sections','prestations')]
            unchanged=data_snapshot()
            for _ in range(6):
                feature=next(anchor.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id"=\'{uid("A")}\'')))
                native=QgsAttributeDialog(anchor,feature,False)
                native.show();QApplication.processEvents()
                widgets=native.findChildren(QgsRelationEditorWidget)
                assert any(w.relation().id()=='ah05_carte_ah' for w in widgets)
                native.close();native.deleteLater();QApplication.processEvents()
            assert count()==before+1
            assert data_snapshot()==unchanged
            passed.append('native_ah_forms_repeated_consultation_no_write')
            spatial=next(l for l in project.mapLayers().values() if QgsDataSourceUri(l.source()).table()=='ah_sections_carte')
            dialog=actions.dispatch(iface,'edit',spatial.id(),row)
            assert not dialog.start.isReadOnly() and not dialog.end.isReadOnly()
            dialog.start.setValue(.45);dialog.preview();assert dialog.signature
            dialog.save();QApplication.processEvents()
            assert store.reference(row)[3]==.45 and store.reference(row)[5]==2
            passed.append('fraction_change_explicit_revision')
            with db.cursor() as c:c.execute('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,1) WHERE id=%s',(uid('R1'),))
            db.commit()
            assert store.reference(row)[6]=='REFERENTIEL_MODIFIE'
            frozen=store.rows(f"SELECT ST_AsText(geom_reference) FROM gemapi.ah_sections WHERE id='{row}'")[0][0]
            dialog=actions.dispatch(iface,'edit',spatial.id(),row)
            dialog.preview();assert dialog.signature is None
            dialog.reject();QApplication.processEvents()
            assert store.rows(f"SELECT ST_AsText(geom_reference) FROM gemapi.ah_sections WHERE id='{row}'")[0][0]==frozen
            passed.append('unqualified_revalidation_refused_frozen_geometry')
            with db.cursor() as c:c.execute('UPDATE gemapi.cours_eau SET direction=TRUE WHERE id=%s',(uid('R1'),))
            db.commit()
            dialog=actions.dispatch(iface,'edit',spatial.id(),row)
            dialog.preview();assert dialog.signature
            dialog.save();QApplication.processEvents()
            assert store.reference(row)[6]=='CONFORME' and store.reference(row)[5]==3
            passed.append('explicit_revalidation_after_human_fixture_qualification')
            preview=store.preview(uid('A'),uid('R2'),.1,.2)
            with db.cursor() as c:c.execute('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,1) WHERE id=%s',(uid('R2'),))
            db.commit()
            n=count()
            try:store.accept(uid('A'),uid('R2'),.1,.2,preview[1])
            except ValueError:pass
            else:raise AssertionError('Stale candidate accepted')
            assert count()==n
            passed.append('stale_preview_optimistic_guard')
            # The action must not commit, rollback or bypass unrelated layer buffers.
            assert anchor.startEditing()
            other_parent=next(anchor.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id"=\'{uid("B")}\'')))
            name_index=anchor.fields().indexFromName('nom')
            assert anchor.changeAttributeValue(other_parent.id(),name_index,'Modification non enregistrée de B')
            assert actions.dispatch(iface,'edit',spatial.id(),row) is None
            assert anchor.getFeature(other_parent.id())['nom']=='Modification non enregistrée de B'
            assert store.parent_name(uid('B'))=='AH fictif B'
            assert anchor.isEditable();anchor.rollBack()
            passed.append('existing_edit_session_preserved')
            # Native parent action: arbitrary manual fractions, no intersection needed.
            n=count()
            dialog=actions.dispatch(iface,'create',anchor.id(),uid('A'))
            assert dialog and not dialog.start.isReadOnly()
            dialog.preview();assert dialog.signature is None and 'Choisissez' in dialog.status.text()
            dialog.courseCombo.setCurrentIndex(dialog.courseCombo.findData(uid('R1')))
            dialog.start.setValue(.05);dialog.end.setValue(.8);dialog.preview()
            assert dialog.signature
            dialog.reject();QApplication.processEvents();assert count()==n
            dialog=actions.dispatch(iface,'create',anchor.id(),uid('A'))
            dialog.courseCombo.setCurrentIndex(dialog.courseCombo.findData(uid('R1')))
            dialog.start.setValue(.05);dialog.end.setValue(.8);dialog.preview();dialog.save()
            QApplication.processEvents();assert count()==n+1
            manual=store.rows(f"SELECT id::text FROM gemapi.ah_sections WHERE ah_id='{uid('A')}' AND debut_fraction=.05 AND fin_fraction=.8")[0][0]
            assert store.reference(manual)[6]=='CONFORME'
            store.delete(manual,1);assert count()==n
            passed.append('native_manual_create_cancel_save_outside_storage')
            other=next(layer.getFeatures())
            n=count();actions.dispatch(iface,'refuse',layer.id(),str(other.id()))
            assert count()==n and not layer.getFeature(other.id()).isValid()
            passed.append('refusal_only_removes_temporary_proposal')
            reader_uri=QgsDataSourceUri(anchor.source());reader_uri.setConnection('127.0.0.1',str(port),'postgres','ah05_reader','')
            from qgis.core import QgsVectorLayer
            reader=QgsVectorLayer(reader_uri.uri(False),'reader','postgres');project.addMapLayer(reader)
            assert actions.store_for(reader).permissions()==[False,False,False,False]
            assert len(actions.store_for(reader).candidates(uid('B')))>=0
            try:actions.store_for(reader).delete(row,3)
            except Exception:pass
            else:raise AssertionError('Reader deleted a reference')
            assert count()==n
            passed.append('read_only_role_no_privilege_escalation')
            actions.close_dialogs();QApplication.processEvents();project.clear()
            (OUTPUT/'qgis_results.json').write_text(json.dumps({'passed':passed,'count':len(passed)},indent=2)+'\n')
            print('AH05 QGIS/PostGIS:',len(passed),'validations réussies')
    finally:app.exitQgis()


if __name__=='__main__':main()
