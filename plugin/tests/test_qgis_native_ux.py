"""Zone reference labels and remaining native fiche relation, without remote access."""
import unittest
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from qgis.core import (QgsApplication,QgsProject,QgsVectorLayer,QgsFeature,QgsField,
                       QgsVectorLayerJoinInfo)
from qgis.PyQt.QtCore import QVariant

ROOT=Path(__file__).resolve().parents[2]
APP=QgsApplication.instance() or QgsApplication([],True)
APP.initQgis()


def project_nodes(env):
    path=ROOT/env/('prestations_demo.qgz' if env=='demo' else 'prestations_cabbalr.qgz')
    with ZipFile(path) as z:
        return ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.qgs'))))


class NativeUx(unittest.TestCase):
    def setUp(self):
        self.project=QgsProject.instance();self.project.clear()

    def tearDown(self):
        self.project.clear()

    def test_zone_reference_labels_preserve_every_uuid(self):
        self.check_selection('cabbalr')

    def test_demo_references_each_child_family(self):
        root = project_nodes('demo')
        sources = {n.findtext('id'): n for n in root.findall('./projectlayers/maplayer')}
        relations = {r.get('id'):r for r in root.findall('./relations/relation')}
        for layer in sources.values():
            if layer.findtext('layername') not in ('prestations_fiches','prestations-points','prestations-lignes','prestations-polygones'):
                continue
            for fk,family in [('mobilier_id','mobilier'),('cheminement_id','cheminements'),('vegetation_id','vegetation')]:
                widget = layer.find(f"./fieldConfiguration/field[@name='{fk}']/editWidget")
                self.assertEqual(widget.get('type'), 'RelationReference')
                relation = relations[widget.find(".//Option[@name='Relation']").get('value')]
                target = sources[relation.get('referencedLayer')]
                self.assertEqual(target.get('readOnly'), '1')
                self.assertIn(f'"gemapi"."{family}"', target.findtext('datasource'))
                self.assertIn('site_terrestre_id IS NOT NULL', target.findtext('datasource'))

    def check_selection(self,env):
        root=project_nodes(env);nodes=root.findall('./projectlayers/maplayer')
        fiche=next(n for n in nodes if n.findtext('layername')=='prestations_fiches')
        ref=next(n for n in nodes if n.findtext('layername')=='Zones terrestres (référence)')
        join=ref.find('./vectorjoins/join');self.assertEqual(join.get('memoryCache'),'1')
        self.assertEqual(join.get('editable'),'0')
        sites=QgsVectorLayer('None?field=id:string&field=nom:string','sites','memory');sites.setId(join.get('joinLayerId'));self.project.addMapLayer(sites)
        zones=QgsVectorLayer('None?field=id:string&field=site_terrestre_id:string&field=nom:string','zones','memory');zones.setId(ref.findtext('id'));self.project.addMapLayer(zones)
        for uid,name in [('a','Site A'),('b','Site B')]:
            f=QgsFeature(sites.fields());f.setAttributes([uid,name]);sites.dataProvider().addFeatures([f])
        rows=[('aaaaaaaa-0000-0000-0000-000000000001','a','Zone homonyme'),
              ('aaaaaaaa-0000-0000-0000-000000000002','a','Zone homonyme'),
              ('bbbbbbbb-0000-0000-0000-000000000003','b','Zone homonyme'),
              ('cccccccc-0000-0000-0000-000000000004','a','Zone unique'),
              ('dddddddd-0000-0000-0000-000000000005','a','Autre zone'),
              ('eeeeeeee-0000-0000-0000-000000000006','a','Autre zone')]
        for row in rows:
            f=QgsFeature(zones.fields());f.setAttributes(list(row));zones.dataProvider().addFeatures([f])
        info=QgsVectorLayerJoinInfo();info.setJoinLayer(sites);info.setJoinFieldName(join.get('joinFieldName'));info.setTargetFieldName(join.get('targetFieldName'));info.setJoinFieldNamesSubset(['nom']);info.setPrefix(join.get('customPrefix'));info.setUsingMemoryCache(True);info.setEditable(False);zones.addJoin(info)
        expression=ref.find("./expressionfields/field[@name='libelle_selection']").get('expression')
        zones.addExpressionField(expression,QgsField('libelle_selection',QVariant.String))
        labels={f['id']:f['libelle_selection'] for f in zones.getFeatures()}
        self.assertEqual(len(set(labels.values())),len(rows))
        # UUIDs appear only when zone + site is ambiguous. A colliding short
        # prefix falls back to the full key, rather than another duplicate label.
        self.assertEqual(labels[rows[2][0]],'Zone homonyme — Site B')
        self.assertEqual(labels[rows[3][0]],'Zone unique — Site A')
        self.assertIn(rows[0][0],labels[rows[0][0]])
        self.assertTrue(labels[rows[4][0]].endswith('[dddddddd]'))
        widget=fiche.find("./fieldConfiguration/field[@name='site_zone_id']/editWidget")
        self.assertEqual(widget.get('type'),'RelationReference')
        options={o.get('name'):o.get('value') for o in widget.findall('./config/Option/Option')}
        self.assertEqual(options['Relation'],'model03_fiches_zone_reference')
        self.assertEqual(options['ReferencedLayerId'],ref.findtext('id'))


if __name__=='__main__':unittest.main()
