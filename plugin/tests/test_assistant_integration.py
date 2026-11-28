"""Fresh PostGIS + installed Assistant ZIP + actual Qt form.

Run explicitly: QT_QPA_PLATFORM=offscreen /usr/bin/python3 plugin/tests/test_assistant_integration.py
Add --live-mistral to send the fictitious sample to the official API (local key required).
Only the disposable database is written; the assistant must perform no writes.
"""

import importlib
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4
from zipfile import ZipFile

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from qgis.core import QgsApplication, QgsDataSourceUri, QgsProject, QgsVectorLayer, QgsWkbTypes

from test_assistant import COMPLETE_PHRASE, FULL, PHRASE, proposal, response, uid
from test_postgres_integration import ROOT, command, sql


def main(live=False):
    container = 'sig-assistant-test-' + uuid4().hex[:10]
    command('docker', 'run', '--rm', '-d', '--name', container,
            '-p', '127.0.0.1::5432', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', 'postgis/postgis:16-3.4')
    app, plugin = None, None
    try:
        for _ in range(60):
            if command('docker', 'exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('PostGIS jetable indisponible')
        sql(container, 'CREATE EXTENSION IF NOT EXISTS postgis;')
        sql(container, (ROOT / 'model/schema_v0.6.1.sql').read_text(encoding='utf-8'))
        print('Schéma canonique installé dans PostGIS jetable', flush=True)
        sql(container, f"""
INSERT INTO gemapi.prestataires(id,nom) VALUES ('{uid(2)}','Veolia');
INSERT INTO gemapi.techniciens(id,prenom,nom) VALUES ('{uid(3)}','Jean','Dubois'),('{uid(4)}','Pierre','Martin');
INSERT INTO gemapi.ah(id,nom) VALUES ('{uid(10)}','AH fictif Assistant');
INSERT INTO gemapi.ah_ouvrage(id,ah_id,nom,geom) VALUES
('{uid(6)}','{uid(10)}','Rivière du Buissons',ST_GeomFromText('LINESTRING(1700000 9200000,1700010 9200010)',3950));
INSERT INTO gemapi.ah_stockage(id,ah_id,nom,geom) VALUES
('{uid(8)}','{uid(10)}','Stockage fictif Assistant',ST_GeomFromText('POLYGON((1700000 9200000,1700010 9200000,1700010 9200010,1700000 9200000))',3950));
INSERT INTO gemapi.sites_terrestres(id,nom,geom) VALUES
('{uid(11)}','Site fictif Assistant',ST_GeomFromText('MULTIPOLYGON(((1700000 9200000,1700010 9200000,1700010 9200010,1700000 9200000)))',3950));
INSERT INTO gemapi.vegetation(famille_vegetation,id,site_terrestre_id,designation,geometry) VALUES
('ARBRES','{uid(9)}','{uid(11)}','Zone fictive Assistant',ST_GeomFromText('MULTIPOINT((1700001 9200001))',3950));
""")
        port = command('docker', 'port', container, '5432').stdout.strip().rsplit(':', 1)[-1]
        audits = ROOT / 'build/qgis-tests'
        with tempfile.TemporaryDirectory(dir=audits) as directory:
            # Build beforehand; test the distributed package, including promoted Qt widgets.
            version = next(line.partition('=')[2] for line in (ROOT / 'plugin/gemapi_app/metadata.txt').read_text().splitlines() if line.startswith('version='))
            archive = ROOT / 'plugin' / 'dist' / f'gemapi-app-{version}.zip'
            with ZipFile(archive) as package:
                package.extractall(directory)
            sys.path.insert(0, directory)
            # Helpers imported source modules before installation; exercise the ZIP itself.
            for name in list(sys.modules):
                if name == 'gemapi_app' or name.startswith('gemapi_app.'):
                    del sys.modules[name]
            runtime = importlib.import_module('gemapi_app')
            assert Path(runtime.__file__).resolve().parent == Path(directory) / 'gemapi_app'
            dialog_module = importlib.import_module('gemapi_app.assistant_dialog')
            engine = importlib.import_module('gemapi_app.assistant')
            # Import Qt helpers only after selecting the installed package.
            from test_assistant_qgis import Interface, Reply
            app = QgsApplication([], True, str(Path(directory) / 'profile'))
            app.initQgis()
            project = QgsProject.instance()
            uri = QgsDataSourceUri()
            uri.setConnection('127.0.0.1', port, 'postgres', 'postgres', '')
            for table in ('prestations', 'ah_ouvrage', 'ah_stockage'):
                uri.setDataSource('gemapi', table, 'geom', '', 'id')
                uri.setSrid('3950')
                uri.setWkbType({'prestations': QgsWkbTypes.Point, 'ah_ouvrage': QgsWkbTypes.LineString,
                                'ah_stockage': QgsWkbTypes.Polygon, 'vegetation': QgsWkbTypes.MultiPoint}[table])
                layer = QgsVectorLayer(uri.uri(False), table, 'postgres')
                assert layer.isValid(), table
                project.addMapLayer(layer)

            class PluginInterface(Interface):
                def addPluginToMenu(self, *_): pass
                def removePluginMenu(self, *_): pass

            iface = PluginInterface()
            plugin = runtime.classFactory(iface)
            plugin.initGui()
            print('Plugin du ZIP chargé, connexion PostgreSQL locale', flush=True)
            plugin.show_dialog()
            plugin.home.assistantButton.click()
            assistant = plugin.assistant
            assert isinstance(assistant, dialog_module.AssistantDialog)
            assert assistant.isVisible()
            ticks = []
            heartbeat = QTimer()
            heartbeat.timeout.connect(lambda: ticks.append(True))
            heartbeat.start(10)

            def send(text, raw):
                if live:
                    with patch.object(dialog_module, 'read_api_key', side_effect=lambda _: engine.read_api_key(str(ROOT / 'MISTRAL_API_KEY.txt'))):
                        assistant.submit_user_message(text)
                else:
                    reply = Reply(raw)
                    with patch.object(assistant, 'network', Mock(post=Mock(return_value=reply))), patch.object(
                            dialog_module, 'read_api_key', return_value='fake-test-key'):
                        assistant.submit_user_message(text)
                    QTimer.singleShot(20, reply.complete)
                assert assistant._reply is not None, assistant.history.toPlainText()
                loop = QEventLoop()
                assistant._reply.finished.connect(loop.quit)
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)
                timer.start(35000)
                loop.exec_()
                timer.stop()
                assert assistant._reply is None, 'Requête non terminée'

            print('Premier échange', flush=True)
            send(PHRASE, response(**{**FULL, 'date_fin': None, 'message': 'Quelle est la date de fin ?'}))
            assert not assistant.open_button.isEnabled(), 'La date de fin ne doit pas être inventée'
            assert assistant.draft.names.get('date_debut') == '2026-11-11', assistant.history.toPlainText()
            send('Le même jour.', response(date_fin='MEME_JOUR', message='Je complète la date de fin.'))
            assert assistant.open_button.isEnabled(), assistant.history.toPlainText()
            assert ticks, 'Le thread Qt doit traiter les autres événements'
            assert sql(container, 'SELECT count(*) FROM gemapi.prestations').stdout.strip() == '0'
            print('Ouverture du formulaire prérempli', flush=True)
            assistant.open_button.click()
            assert plugin.creation is not None, assistant.history.toPlainText()
            assert plugin.creation._associations() == [('ah_ouvrage', uid(6))]
            assert plugin.creation._values()['technicien_id'] == uid(3)
            assert plugin.creation._values()['date_fin'] == '2026-11-11'
            assert plugin.creation.geometry is None
            assert sql(container, 'SELECT count(*) FROM gemapi.prestations').stdout.strip() == '0'
            transcript = assistant.history.toPlainText()
            # Close without saving, then return to exactly the same conversation instance.
            plugin.creation._saved_snapshot = plugin.creation._snapshot()
            plugin.creation.backButton.click()
            app.processEvents()
            assert plugin.assistant is assistant and assistant.isVisible()
            assert assistant.history.toPlainText() == transcript
            print('Mistral réel' if live else 'Mistral simulé', ': demande, date manquante, complément, formulaire, retour et absence d’écriture OK')

            # Native reference loading and the standard save on all three families.
            for family, name, object_id in [('ah_ouvrage', 'Rivière du Buissons', uid(6)),
                                            ('ah_stockage', 'Stockage fictif Assistant', uid(8)),
                                            ('vegetation', 'Zone fictive Assistant', uid(9))]:
                draft = engine.Draft()
                draft.apply(proposal(**{**FULL, 'famille': family, 'patrimoine': name}),
                            user_message=COMPLETE_PHRASE)
                resolved = draft.resolve(engine.load_catalog(plugin.session.connection(), family))
                assert resolved['values'], resolved['issues']
                before = int(sql(container, 'SELECT count(*) FROM gemapi.prestations').stdout.strip())
                form = plugin.open_creation_draft(resolved['values'], resolved['associations'])
                assert int(sql(container, 'SELECT count(*) FROM gemapi.prestations').stdout.strip()) == before
                assert form.geometry is None
                assert form._associations() == [(family, object_id)]
                print('Validation explicite du formulaire : ' + family, flush=True)
                form.saveButton.click()  # Explicit test stand-in for human validation, disposable DB only.
                assert int(sql(container, 'SELECT count(*) FROM gemapi.prestations').stdout.strip()) == before + 1, form.statusLabel.text()
                assert sql(container, 'SELECT count(*) FROM gemapi.prestations WHERE geom IS NOT NULL').stdout.strip() == '0'
                form.backButton.click()
                app.processEvents()
            print('Installation neuve, 3 rattachements, UUID réels, enregistrement standard explicite et géométries NULL : OK')
            heartbeat.stop()
            plugin.unload()
            plugin = None
            project.clear()
            iface.window.close()
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            sip.delete(iface.window)
            app.exitQgis()
            app = None
    finally:
        if plugin is not None:
            plugin.unload()
        if app is not None:
            QgsProject.instance().clear()
            app.exitQgis()
        command('docker', 'rm', '-f', container, check=False)


if __name__ == '__main__':
    main('--live-mistral' in sys.argv[1:])
