"""Charge une copie du projet QGIS démo contre un PostGIS local jetable.

Les sources PostgreSQL distantes enregistrées dans le projet sont remplacées
dans une copie temporaire avant tout chargement par QGIS.
"""

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from osgeo import ogr
from qgis.core import (QgsApplication, QgsDataSourceUri, QgsFieldConstraints, QgsGeometry,
                       QgsProject, QgsVectorDataProvider, QgsVectorLayer, QgsWkbTypes)

from test_postgres_integration import ROOT, command, load_demo_rows, sql


def local_document(path, port, database="postgres", user="postgres"):
    with ZipFile(path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".qgs"))
        document = ElementTree.fromstring(archive.read(name))
    def local_uri(value):
        source = QgsDataSourceUri(value)
        uri = QgsDataSourceUri()
        uri.setConnection("127.0.0.1", port, database, user, "")
        service = "service='gemapi_demo' "
        if value.startswith(service):
            # Keep native layer options, including the full subset expression.
            return uri.connectionInfo(False) + " " + value[len(service):]
        uri.setDataSource(source.schema(), source.table(), source.geometryColumn(),
                          source.sql(), source.keyColumn())
        uri.setWkbType(source.wkbType())
        uri.setSrid(source.srid())
        assert uri.host() == "127.0.0.1" and not uri.service()
        return uri.uri(False)

    count = 0
    for layer in document.findall("./projectlayers/maplayer"):
        if layer.findtext("provider") != "postgres":
            continue
        source = layer.find("datasource")
        source.text = local_uri(source.text)
        count += 1
    assert count == (32 if path.name == "prestations_demo.qgz" else 25)
    for element in document.iter():
        for key, value in element.attrib.items():
            if value.startswith(("dbname=", "service=")):
                element.set(key, local_uri(value))
    return document


def main():
    package = ogr.Open(str(ROOT / "demo/gpkg/prestations.gpkg"))
    seen = {}
    zone_null = 0
    for layer in package:
        assert layer.GetSpatialRef().GetAuthorityCode(None) == "3950"
        for feature in layer:
            geometry = feature.GetGeometryRef()
            uid = feature.GetField("id")
            assert uid not in seen
            if feature.GetField('vegetation_id'):
                assert geometry is None
                zone_null += 1
                seen[uid] = None
                continue
            assert geometry is not None and not geometry.IsEmpty() and geometry.IsValid()
            seen[uid] = geometry.GetGeometryName()
            if geometry.GetGeometryName().startswith("MULTI"):
                assert geometry.GetGeometryCount() >= 2
                assert all(geometry.GetGeometryRef(i).IsValid() for i in range(geometry.GetGeometryCount()))
                assert all(not geometry.GetGeometryRef(i).Intersects(geometry.GetGeometryRef(j))
                           for i in range(geometry.GetGeometryCount()) for j in range(i))
    assert len(seen) == 100 and len({kind for kind in seen.values() if kind is not None}) == 6
    assert zone_null == 12
    package = None
    container = "sig-prestations-project-" + uuid4().hex[:10]
    command("docker", "run", "--rm", "-d", "--name", container,
            "-p", "127.0.0.1::5432", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            "postgis/postgis:16-3.4")
    app = None
    try:
        for _ in range(60):
            if command("docker", "exec", container, "pg_isready", "-h", "127.0.0.1",
                       "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("PostGIS local indisponible")
        sql(container, "CREATE EXTENSION IF NOT EXISTS postgis;")
        sql(container, (ROOT / "model/schema_v0.6.1.sql").read_text())
        load_demo_rows(container)

        sql(container, "CREATE ROLE qgis_release_reader LOGIN; "
                       "GRANT USAGE ON SCHEMA gemapi TO qgis_release_reader; "
                       "GRANT SELECT ON ALL TABLES IN SCHEMA gemapi TO qgis_release_reader;")
        port = command("docker", "port", container, "5432").stdout.strip().rsplit(":", 1)[-1]
        document = local_document(ROOT / "demo/prestations_demo.qgz", port)
        app = QgsApplication([], True)
        app.initQgis()
        with TemporaryDirectory() as directory:
            local = Path(directory) / "prestations_local.qgs"
            ElementTree.ElementTree(document).write(local, encoding="utf-8", xml_declaration=True)
            project = QgsProject.instance()
            assert project.read(str(local)), "Chargement du projet QGIS échoué"
            layers = list(project.mapLayers().values())
            assert len(layers) == 33
            assert len(project.relationManager().relations()) == 70
            assert all(relation.isValid() for relation in
                       project.relationManager().relations().values())
            group = project.layerTreeRoot().findGroup("Tables et référentiels")
            assert group is not None and not group.isExpanded()
            assert len(group.children()) == 10
            assert all(not project.mapLayer(node.layerId()).isSpatial()
                       for node in group.children())
            link_counts = {"prestations__ah":19,"prestations__sites_terrestres":18}
            for name, count in link_counts.items():
                layer = next(layer for layer in layers if layer.name() == name)
                assert not layer.isSpatial() and layer.featureCount() == count, (
                    name, layer.featureCount(), count)
                assert layer.fields().indexFromName('prestation_id')>=0
            for family,count in (("Mobilier",4), ("Cheminements",2), ("Végétation",19)):
                children = [layer for layer in layers if layer.name().startswith(family + " (") and layer.isSpatial()]
                assert len(children) == 3 and sum(layer.featureCount() for layer in children) == count
                assert all(layer.isValid() and layer.readOnly() for layer in children)
            assert next(layer for layer in layers if layer.name()=='sites_terrestres').featureCount()==12
            for name, field, flag, required in (("ah_stockage", "ah_id", QgsFieldConstraints.ConstraintUnique, True),
                                      ("ah_ouvrage", "nom", QgsFieldConstraints.ConstraintNotNull, False),
                                      ("Cours d'eau", "nom", QgsFieldConstraints.ConstraintNotNull, True)):
                layer = next(layer for layer in layers if layer.name() == name)
                index = layer.fields().indexFromName(field)
                # Provider constraints describe the canonical database contract;
                # layer.fieldConstraints also includes intentional project overrides.
                provider_index=layer.dataProvider().fields().indexFromName(field)
                assert bool(layer.dataProvider().fields().at(provider_index).constraints().constraints() & flag) == required, (name, field)
                configured=next(node for node in document.findall('./projectlayers/maplayer') if node.findtext('id')==layer.id())
                constraint=configured.find(f"./constraints/constraint[@field='{field}']")
                assert constraint is not None
                assert bool(layer.fieldConstraints(index) & flag) == (
                    required or bool(int(constraint.get('constraints')) & int(flag))), (name, field)
                if name == "ah_stockage":
                    assert constraint.get('unique_strength') == '1'
                    assert layer.fields().at(index).constraints().constraintStrength(flag) == QgsFieldConstraints.ConstraintStrengthHard
            reader_uri = QgsDataSourceUri()
            reader_uri.setConnection("127.0.0.1", port, "postgres", "qgis_release_reader", "")
            reader_uri.setDataSource("gemapi", "prestations", "", "", "id")
            reader_layer = QgsVectorLayer(reader_uri.uri(False), "lecture", "postgres")
            assert reader_layer.isValid() and reader_layer.featureCount() == 100
            capabilities = reader_layer.dataProvider().capabilities()
            assert not capabilities & QgsVectorDataProvider.AddFeatures
            assert not capabilities & QgsVectorDataProvider.ChangeAttributeValues
            for layer in layers:
                if layer.providerType() == "postgres":
                    assert layer.isValid(), (layer.name(), layer.error().summary())
            fiches = [layer for layer in layers if layer.name() == "prestations_fiches"]
            assert len(fiches) == 1 and fiches[0].featureCount() == 100
            assert next(fiches[0].getFeatures()).attribute("id") is not None
            digues = [layer for layer in layers if layer.name() == "digues_carte"]
            assert len(digues) == 1 and digues[0].featureCount() == 3
            prestations = [layer for layer in layers if layer.providerType()=='postgres' and
                           QgsDataSourceUri(layer.source()).table()=='prestations_carte']
            assert len(prestations) == 3
            for layer in prestations:
                position_index = layer.fields().indexFromName("position")
                assert position_index >= 0
                assert layer.editorWidgetSetup(position_index).type() == "ValueMap"
            expected = {QgsWkbTypes.MultiPoint: 33, QgsWkbTypes.MultiLineString: 32,
                        QgsWkbTypes.MultiPolygon: 35}
            seen_ids = set()
            for layer in prestations:
                kind = layer.wkbType()
                assert layer.featureCount() == expected[kind]
                features = list(layer.getFeatures())
                assert len(features) == expected[kind]
                assert all(QgsWkbTypes.multiType(f.geometry().wkbType()) == kind for f in features)
                ids = {str(f["id"]) for f in features}
                assert len(ids) == expected[kind] and seen_ids.isdisjoint(ids)
                seen_ids.update(ids)
            assert len(seen_ids) == 100
            from qgis.gui import QgsAttributeForm, QgsGui
            QgsGui.editorWidgetRegistry().initEditors()
            editor = fiches[0]
            assert editor.readOnly()
            form = QgsAttributeForm(editor, next(editor.getFeatures()))
            assert form.feature().isValid()
            form.close()
            del form
            project.clear()
        app.exitQgis()
        print("Projet QGIS de démonstration chargé sur PostGIS local")
    finally:
        command("docker", "stop", container, check=False)


if __name__ == "__main__":
    main()
