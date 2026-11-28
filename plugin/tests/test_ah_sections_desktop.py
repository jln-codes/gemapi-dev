"""QGIS Desktop 3.44 native forms/actions on isolated PostGIS and X11.

/usr/bin/python3 plugin/tests/test_ah_sections_desktop.py
No production connection or real-data directory is used.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from uuid import uuid5,NAMESPACE_URL
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'build/qgis-tests/hydro_ah_05'

def uid(name):return str(uuid5(NAMESPACE_URL,'sig-prestations/HYDRO-AH-05/'+name))


def start(iface):
    """Called inside the genuine qgis process through --code."""
    from qgis import utils
    from qgis.PyQt.QtCore import QTimer,Qt
    from qgis.PyQt.QtTest import QTest
    from qgis.PyQt.QtWidgets import QApplication,QPushButton,QDialogButtonBox,QWidget
    from qgis.core import QgsApplication,QgsProject,QgsFeatureRequest,QgsDataSourceUri,QgsSettings,Qgis
    from qgis.gui import QgsAttributeDialog,QgsDualView,QgsRelationEditorWidget
    result={'qgis':Qgis.QGIS_VERSION,'passed':[],'screenshots':[]}
    state={}
    def record(name):
        result['passed'].append(name)
        (OUTPUT/'desktop_results.json').write_text(json.dumps(result,indent=2)+'\n')
    def capture(name):
        QTest.qWait(180)
        file=OUTPUT/(name+'.png')
        assert QApplication.primaryScreen().grabWindow(0).save(str(file))
        result['screenshots'].append(file.name)
    def done(error=None):
        if state.get('done'):return
        state['done']=True
        if error:result['error']=error
        result['result']='FAIL' if error else 'PASS'
        try:
            if 'gemapi_app' in utils.plugins:utils.unloadPlugin('gemapi_app')
            for widget in list(QApplication.topLevelWidgets()):
                if widget is not iface.mainWindow():widget.close()
            QgsProject.instance().clear()
        except Exception:pass
        (OUTPUT/'desktop_results.json').write_text(json.dumps(result,indent=2)+'\n')
        # A startup --code callback can run before the outer event loop starts.
        timer=QTimer(QApplication.instance())
        timer.timeout.connect(lambda:QApplication.instance().exit(1 if error else 0))
        timer.start(400)
        state['exit_timer']=timer
    def schedule(function,delay=400):
        def wrapped():
            try:
                if state.get('done'):return
                function()
            except BaseException:done(traceback.format_exc())
        QTimer.singleShot(delay,wrapped)
    def feature(layer,identifier):
        return next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id"=\'{identifier}\'')))
    def typed_children(parent,cls):
        from qgis.PyQt import sip
        return [sip.cast(w,cls) for w in parent.findChildren(QWidget) if w.inherits(cls.__name__)]
    def natives():
        from qgis.PyQt import sip
        return [sip.cast(w,QgsAttributeDialog) for w in QApplication.topLevelWidgets() if w.inherits('QgsAttributeDialog') and w.isVisible()]
    def boot():
        project=QgsProject.instance()
        assert len(project.mapLayers())==26
        for layer in project.mapLayers().values():
            if layer.providerType()=='postgres':assert QgsDataSourceUri(layer.source()).host()=='127.0.0.1'
        # Macros never enabled; actions are explicit user operations.
        QgsSettings().setValue('qgis/enableMacros',0)
        assert utils.loadPlugin('gemapi_app') and utils.startPlugin('gemapi_app')
        import gemapi_app
        from gemapi_app import ah_sections
        assert 'profiles/ah05/' in str(Path(gemapi_app.__file__).resolve())
        state['actions']=ah_sections
        state['ah']=next(l for l in project.mapLayers().values() if l.name()=='ah')
        state['view']=next(l for l in project.mapLayers().values() if l.providerType()=='postgres' and QgsDataSourceUri(l.source()).table()=='ah_sections_carte')
        state['store']=ah_sections.store_for(state['ah'])
        state['before']=state['store'].rows('SELECT count(*) FROM gemapi.ah_sections')[0][0]
        for layer in project.mapLayers().values():
            if layer.providerType()!='postgres':project.layerTreeRoot().findLayer(layer.id()).setItemVisibilityChecked(False)
        iface.mainWindow().resize(1500,950)
        iface.mapCanvas().setExtent(next(l for l in project.mapLayers().values() if l.name()=='ah_stockage').extent())
        table=iface.showAttributeTable(state['ah'],f'"id"=\'{uid("A")}\'')
        state['ah_table']=table
        table.resize(1150,850)
        schedule(table_form)
    def table_form():
        table=state['ah_table']
        (OUTPUT/'desktop_widget_tree.json').write_text(json.dumps([(type(w).__name__,w.metaObject().className(),w.objectName()) for w in table.findChildren(QWidget)],indent=2))
        dual=table.findChild(QgsDualView)
        if dual is None:
            from qgis.PyQt import sip
            widget=next(w for w in table.findChildren(QWidget) if w.metaObject().className()=='QgsDualView')
            dual=sip.cast(widget,QgsDualView)
        dual.setView(QgsDualView.AttributeEditor)
        record('native_attribute_table_form')
        schedule(native_form)
    def native_form():
        iface.openFeatureForm(state['ah'],feature(state['ah'],uid('A')),False,False)
        dialog=natives()[-1];state['native']=dialog
        dialog.resize(1120,850)
        dialog.move(25,40)
        record('native_modeless_ah_form')
        schedule(search)
    def search():
        dialog=state['native']
        widgets=typed_children(dialog,QgsRelationEditorWidget)
        section_widget=next(w for w in widgets if w.relation().id()=='ah05_carte_ah')
        section_widget.setViewMode(QgsDualView.AttributeTable)
        buttons=[b for b in dialog.findChildren(QPushButton) if 'Rechercher des sections' in b.text()]
        assert len(buttons)==1 and buttons[0].isEnabled(),[(b.text(),b.isEnabled()) for b in buttons]
        # Scroll to the new section without changing the form's layout.
        from qgis.PyQt.QtWidgets import QScrollArea
        for scroll in dialog.findChildren(QScrollArea):scroll.ensureWidgetVisible(buttons[0])
        dialog.raise_();dialog.activateWindow()
        capture('desktop_01_ah_sections')
        QTest.mouseClick(buttons[0],Qt.LeftButton)
        schedule(proposals)
    def proposals():
        actions=state['actions']
        layers=[l for l in QgsProject.instance().mapLayers().values() if l.customProperty(actions.TEMPORARY,False)]
        assert len(layers)==1
        layer=layers[0];state['proposals']=layer
        assert layer.featureCount()==4
        assert state['store'].rows('SELECT count(*) FROM gemapi.ah_sections')[0][0]==state['before']
        record('native_action_candidates_no_write')
        tables=[w for w in QApplication.topLevelWidgets() if w.findChild(QWidget,'mMainView') is not None]
        table=next(w for w in tables if typed_children(w,QgsDualView)[0].masterModel().layer().id()==layer.id())
        dual=typed_children(table,QgsDualView)[0]
        dual.setView(QgsDualView.AttributeTable)
        assert dual.filteredFeatureCount()==4
        state['candidate_table']=table
        state['native'].hide();state['ah_table'].hide()
        table.resize(1120,330);table.move(400,640)
        first=next(f for f in layer.getFeatures() if f['cours_eau_id']==uid('R1') and f['debut_fraction']==.4)
        state['candidate']=first
        actions.zoom(iface,layer,first.id())
        table.raise_();table.activateWindow()
        capture('desktop_02_candidates')
        act=next(a for a in layer.actions().actions() if 'Accepter' in a.name())
        layer.actions().doActionFeature(act.id(),first)
        schedule(cancel)
    def cancel():
        actions=state['actions']
        assert len(actions._dialogs)==1
        dialog=next(iter(actions._dialogs))
        assert dialog.isVisible()
        capture('desktop_03_accept_confirmation')
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Cancel),Qt.LeftButton)
        assert state['store'].rows('SELECT count(*) FROM gemapi.ah_sections')[0][0]==state['before']
        record('native_cancel_no_write')
        layer=state['proposals'];act=next(a for a in layer.actions().actions() if 'Accepter' in a.name())
        layer.actions().doActionFeature(act.id(),state['candidate'])
        schedule(accept)
    def accept():
        dialog=next(iter(state['actions']._dialogs))
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Save),Qt.LeftButton)
        assert state['store'].rows('SELECT count(*) FROM gemapi.ah_sections')[0][0]==state['before']+1
        QTest.qWait(120)
        assert typed_children(state['candidate_table'],QgsDualView)[0].filteredFeatureCount()==3
        record('native_explicit_accept_saved')
        state['candidate_table'].close();state['native'].close();state['ah_table'].close()
        schedule(reopen)
    def reopen():
        iface.openFeatureForm(state['ah'],feature(state['ah'],uid('A')),False,False)
        dialog=natives()[-1];state['native']=dialog
        dialog.resize(1120,850);dialog.move(25,40)
        widgets=[w for w in typed_children(dialog,QgsRelationEditorWidget) if w.relation().id()=='ah05_carte_ah']
        assert len(widgets)==1
        widgets[0].setViewMode(QgsDualView.AttributeTable)
        dual=typed_children(widgets[0],QgsDualView)[0]
        assert dual.filteredFeatureCount()==2,dual.filteredFeatureCount()
        from qgis.PyQt.QtWidgets import QScrollArea
        for scroll in dialog.findChildren(QScrollArea):scroll.ensureWidgetVisible(widgets[0])
        dialog.raise_();dialog.activateWindow()
        capture('desktop_04_accepted_sections')
        record('reopened_native_relation_lists_both_sections')
        dialog.close()
        state['reference']=state['store'].rows(f"SELECT id::text FROM gemapi.ah_sections WHERE ah_id='{uid('A')}' AND debut_fraction=.4")[0][0]
        state['edit_cycle']=0
        schedule(begin_edit)
    def begin_edit():
        iface.openFeatureForm(state['view'],feature(state['view'],state['reference']),False,False)
        schedule(edit_action)
    def edit_action():
        native=natives()[-1]
        state['section_native']=native
        buttons=[b for b in native.findChildren(QPushButton) if 'revalider les fractions' in b.text()]
        assert len(buttons)==1 and buttons[0].isEnabled()
        QTest.mouseClick(buttons[0],Qt.LeftButton)
        schedule(preview_edit)
    def preview_edit():
        dialog=next(iter(state['actions']._dialogs))
        if state['edit_cycle']==0:
            assert not dialog.start.isReadOnly() and not dialog.end.isReadOnly()
            dialog.start.setValue(.45)
        QTest.mouseClick(dialog.preview_button,Qt.LeftButton)
        if state['edit_cycle']==1:
            assert dialog.signature is None
            assert not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
            capture('desktop_07_unqualified_revalidation_refused')
            dialog.reject()
            record('native_revalidation_refused_on_unqualified_course')
            state['store'].rows(f"UPDATE gemapi.cours_eau SET direction=TRUE WHERE id='{uid('R1')}' AND source='TEST AH05'")
        else:
            assert dialog.signature is not None
            capture('desktop_06_fraction_preview' if state['edit_cycle']==0 else 'desktop_08_revalidation_preview')
            QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Save),Qt.LeftButton)
            revision=2 if state['edit_cycle']==0 else 3
            assert state['store'].reference(state['reference'])[5]==revision
            record('native_fraction_edit_revision_2' if state['edit_cycle']==0 else 'native_explicit_revalidation_revision_3')
            if state['edit_cycle']==0:
                state['store'].rows(f"UPDATE gemapi.cours_eau SET geom=extensions.ST_Translate(geom,0,1) WHERE id='{uid('R1')}' AND source='TEST AH05'")
                assert state['store'].reference(state['reference'])[6]=='REFERENTIEL_MODIFIE'
        state['section_native'].close()
        state['edit_cycle']+=1
        schedule(begin_edit if state['edit_cycle']<3 else modal_test)
    def modal_test():
        # QGIS consultation ignores showModal; edit mode exercises native exec().
        def modal_close():
            try:
                modal=QApplication.activeModalWidget()
                assert modal is not None
                assert modal.isModal()
                capture('desktop_05_native_modal')
                modal.reject()
            except BaseException:done(traceback.format_exc())
        assert state['ah'].startEditing()
        QTimer.singleShot(500,modal_close)
        iface.openFeatureForm(state['ah'],feature(state['ah'],uid('A')),False,True)
        assert state['ah'].isEditable()
        state['ah'].rollBack()
        record('native_modal_edit_cancel_and_rollback')
        schedule(repeat)
    def repeat():
        iface.openFeatureForm(state['ah'],feature(state['ah'],uid('A')),False,False)
        QTest.qWait(100)
        parent_form=natives()[-1]
        create=next(b for b in parent_form.findChildren(QPushButton) if 'Créer une section manuelle' in b.text())
        QTest.mouseClick(create,Qt.LeftButton);QTest.qWait(100)
        manual=next(iter(state['actions']._dialogs))
        manual.courseCombo.setCurrentIndex(manual.courseCombo.findData(uid('R1')))
        manual.start.setValue(.05);manual.end.setValue(.8)
        QTest.mouseClick(manual.preview_button,Qt.LeftButton)
        assert manual.signature and not manual.start.isReadOnly()
        capture('desktop_09_manual_creation')
        QTest.mouseClick(manual.buttons.button(QDialogButtonBox.Save),Qt.LeftButton)
        ref=state['store'].rows(f"SELECT id::text FROM gemapi.ah_sections WHERE ah_id='{uid('A')}' AND debut_fraction=.05 AND fin_fraction=.8")[0][0]
        assert state['store'].reference(ref)[6]=='CONFORME'
        state['store'].delete(ref,1)
        parent_form.close()
        record('native_manual_creation_support_fractions_outside_storage')
        for _ in range(6):
            QTest.qWait(100)
            iface.openFeatureForm(state['ah'],feature(state['ah'],uid('A')),False,False)
            QTest.qWait(100)
            dialog=natives()[-1]
            dialog.close();QTest.qWait(100)
        record('six_native_reopens_without_crash')
        assert state['store'].rows('SELECT count(*) FROM gemapi.ah_sections')[0][0]==state['before']+1
        assert state['store'].rows("SELECT id::text FROM gemapi.cours_eau WHERE source<>'TEST AH05' AND direction IS NOT NULL")==[['90368380-5d42-4a28-9889-6ae657f84a75']]
        record('consultation_no_unintended_write')
        assert utils.unloadPlugin('gemapi_app')
        assert not state['actions']._dialogs
        assert not any(l.customProperty(state['actions'].TEMPORARY,False) for l in QgsProject.instance().mapLayers().values())
        record('installed_plugin_unload_cleans_temporary_layers')
        done()
    schedule(boot,1700)


def main():
    from test_ah_sections_qgis import database,project_copy
    sys.path.insert(0,str(ROOT/'plugin/tools'))
    import build_plugin
    archive=build_plugin.write_archive(build_plugin.validate_plugin())
    OUTPUT.mkdir(parents=True,exist_ok=True)
    profiles=OUTPUT/'qgis_profiles'
    plugins=profiles/'profiles/ah05/python/plugins'
    plugins.mkdir(parents=True,exist_ok=True)
    with ZipFile(archive) as z:z.extractall(plugins)
    entry=OUTPUT/'desktop_entry.py'
    entry.write_text("import sys\nsys.path.insert(0,"+repr(str(ROOT/'plugin/tests'))+")\nfrom test_ah_sections_desktop import start\nfrom qgis.utils import iface\nstart(iface)\n")
    xlog=open(OUTPUT/'xvfb.log','w')
    xvfb=subprocess.Popen(['Xvfb','-displayfd','1','-screen','0','1550x1000x24','-nolisten','tcp','-ac'],stdout=subprocess.PIPE,stderr=xlog,text=True)
    try:
        display=xvfb.stdout.readline().strip()
        assert display.isdigit(),display
        with database() as (_container,port,_db):
            local=project_copy(port,OUTPUT/'desktop_local.qgs')
            env=os.environ.copy();env['DISPLAY']=':'+display;env['QT_QPA_PLATFORM']='xcb';env['PYTHONFAULTHANDLER']='1'
            with open(OUTPUT/'desktop.log','w') as log:
                process=subprocess.run(['qgis','--nologo','--noversioncheck','--noplugins',
                                        '--profiles-path',str(profiles),'--profile','ah05','--project',str(local),
                                        '--code',str(entry)],env=env,cwd=OUTPUT,stdout=log,stderr=subprocess.STDOUT,timeout=160)
            result=json.loads((OUTPUT/'desktop_results.json').read_text())
            assert process.returncode==0 and result['result']=='PASS',result
            print('AH05 QGIS Desktop:',len(result['passed']),'parcours vérifiés ;',len(result['screenshots']),'captures réelles.')
    finally:
        xvfb.terminate();xvfb.wait(timeout=10);xlog.close()


if __name__=='__main__':main()
