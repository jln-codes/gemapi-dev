#!/usr/bin/env python3
"""Comparer et synchroniser explicitement deux projets QGIS, sans accès SQL."""
import argparse
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import hashlib
import shutil
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from qgis.core import QgsDataSourceUri


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "demo/prestations_demo.qgz"
ROOT_LOCAL = ('homePath', 'title', 'projectCrs', 'projectMetadata', 'Bookmarks',
              'Annotations', 'mapViewDocks', 'mapcanvas/extent', 'mapcanvas/destinationsrs',
              'ProjectViewSettings/DefaultViewExtent', 'ProjectViewSettings/PresetFullExtent',
              'properties/Paths', 'properties/WMSUrl', 'properties/WFSUrl',
              'properties/WCSUrl', 'properties/WMTSUrl')
LAYER_LOCAL = ('datasource', 'srs', 'extent', 'wgs84extent',
               'resourceMetadata/extent', 'resourceMetadata/crs')
HYDRO_FILTERS = {'', '"nom" IS NOT NULL\nAND btrim("nom") <> \'\''}
GROUPS = {
    'styles_symboles': ('renderer-v2', 'selection', 'blendMode', 'featureBlendMode', 'layerOpacity'),
    'etiquettes': ('labeling', 'labelsEnabled'),
    'formulaires_widgets_contraintes': ('fieldConfiguration', 'attributeEditorForm', 'aliases',
        'defaults', 'constraints', 'constraintExpressions', 'editable', 'labelOnTop',
        'reuseLastValue', 'editform', 'editforminitcode', 'widgets', 'attributetableconfig'),
    'actions': ('attributeactions',),
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def archive(path):
    path = Path(path)
    contents = path.read_bytes()
    with ZipFile(BytesIO(contents)) as source:
        members = [(entry, source.read(entry.filename)) for entry in source.infolist()]
        comment = source.comment
    names = [entry.filename for entry, _ in members if entry.filename.endswith('.qgs')]
    if len(names) != 1:
        raise ValueError('Le projet doit contenir exactement un document .qgs.')
    xml = next(data for entry, data in members if entry.filename == names[0])
    return {'path': path, 'hash': hashlib.sha256(contents).hexdigest(), 'members': members, 'comment': comment,
            'name': names[0], 'xml': xml, 'root': ET.fromstring(xml)}


def canonical(node):
    """Ignore les indentations, l'ordre des attributs et les maps non ordonnées."""
    if node is None:
        return None
    children = list(node)
    if node.tag in ('projectlayers', 'relations'):
        children.sort(key=lambda n: n.findtext('id', n.get('id', '')))
    elif node.tag in ('qgis','maplayer','mapcanvas','ProjectViewSettings','resourceMetadata') or (
            node.tag == 'LayoutItem' and node.get('type') == '65639'):
        # Ces sections sont lues par leur nom. L'ordre de l'arbre des couches,
        # des widgets, des règles et des symboles reste significatif.
        children.sort(key=lambda n: n.tag)
    elif node.tag == 'Option' and node.get('type') == 'Map':
        children.sort(key=lambda n: n.get('name', ''))
    return (node.tag, tuple(sorted(node.attrib.items())), (node.text or '').strip(),
            tuple(canonical(child) for child in children))


def layers(root):
    result = {layer.findtext('id'): layer for layer in root.findall('./projectlayers/maplayer')}
    if None in result or len(result) != len(root.findall('./projectlayers/maplayer')):
        raise ValueError('Identifiants de couches absents ou dupliqués.')
    return result


def relation_ids(root):
    values = [r.get('id') for r in root.findall('./relations/relation')]
    if None in values or len(values) != len(set(values)):
        raise ValueError('Identifiants de relations absents ou dupliqués.')
    return set(values)


def preserve(parent, destination, path):
    """Conserve également l'absence d'une propriété dans la destination."""
    parent_path, _, tag = path.rpartition('/')
    old_parent = destination.find(parent_path) if parent_path else destination
    new_parent = parent.find(parent_path) if parent_path else parent
    old = old_parent.find(tag) if old_parent is not None else None
    if new_parent is None:
        if old is not None:
            raise ValueError('Conteneur manquant pour la propriété locale '+path)
        return
    existing = new_parent.find(tag)
    position = list(new_parent).index(existing) if existing is not None else len(new_parent)
    if existing is not None:
        new_parent.remove(existing)
    if old is not None:
        new_parent.insert(position, deepcopy(old))


def map_items(root):
    return {item.get('uuid'): item for item in root.findall('.//Layouts//LayoutItem')
            if item.get('type') == '65639'}


def preserve_environment(root, destination):
    for name in ('saveUser', 'saveUserFull', 'saveDateTime', 'projectname'):
        root.attrib.pop(name, None)
        if name in destination.attrib:
            root.set(name, destination.get(name))
    for path in ROOT_LOCAL:
        preserve(root, destination, path)
    target_layers = layers(destination)
    for identifier, layer in layers(root).items():
        for path in LAYER_LOCAL:
            preserve(layer, target_layers[identifier], path)
    old_maps, new_maps = map_items(destination), map_items(root)
    if set(old_maps) != set(new_maps):
        raise ValueError('Conflit : ajout/suppression de carte de mise en page ; définir son cadrage cible explicitement.')
    for identifier, item in new_maps.items():
        preserve(item, old_maps[identifier], 'Extent')


def environment(root):
    return {
        'identite': {name: root.get(name) for name in ('saveUser','saveUserFull','saveDateTime','projectname')},
        'racine': {path: canonical(root.find(path)) for path in ROOT_LOCAL},
        'couches': {identifier: {path: canonical(layer.find(path)) for path in LAYER_LOCAL}
                    for identifier, layer in layers(root).items()},
        'cartes_mise_en_page': {uid: canonical(item.find('Extent')) for uid,item in map_items(root).items()},
    }


def configuration_changes(source, destination):
    changes = {group: [] for group in GROUPS}
    changes.update({'noms_couches': [], 'autres_parametres_couches': [], 'sections_projet': []})
    target_layers = layers(destination)
    for identifier, layer in layers(source).items():
        old = target_layers[identifier]
        name = layer.findtext('layername')
        covered = set(LAYER_LOCAL) | {'id', 'provider', 'layername'}
        for group, tags in GROUPS.items():
            covered.update(tags)
            if any(canonical(layer.find(tag)) != canonical(old.find(tag)) for tag in tags):
                changes[group].append(name)
        if name != old.findtext('layername'):
            changes['noms_couches'].append({'avant': old.findtext('layername'), 'apres': name})
        other = sorted({n.tag for n in layer} | {n.tag for n in old})
        other = [tag for tag in other if tag not in covered
                 and canonical(layer.find(tag)) != canonical(old.find(tag))]
        if other:
            changes['autres_parametres_couches'].append({'couche': name, 'sections': other})
    for tag in sorted({n.tag for n in source} | {n.tag for n in destination}):
        if tag != 'projectlayers' and canonical(source.find(tag)) != canonical(destination.find(tag)):
            changes['sections_projet'].append(tag)
    return changes


def connection_summary(root):
    result = {}
    for identifier, layer in layers(root).items():
        if layer.findtext('provider') != 'postgres':
            continue
        uri = QgsDataSourceUri(layer.findtext('datasource'))
        description = {'hote': uri.host(),
            'service': uri.service(), 'port': uri.port(), 'base': uri.database(),
            'compte': uri.username(), 'authcfg_present': bool(uri.authConfigId()),
            'sslmode': int(uri.sslMode())}
        key = json.dumps(description,sort_keys=True)
        result.setdefault(key,{**description,'couches':[]})['couches'].append(layer.findtext('layername'))
    return list(result.values())


@dataclass
class SyncPlan:
    source: dict
    destination: dict
    root: ET.Element
    payload: bytes
    report: dict
    pending: bool

    def apply(self, backup_dir):
        if not self.pending:
            return None
        def unchanged():
            if any(digest(p['path']) != p['hash'] for p in (self.source, self.destination)):
                raise ValueError('Un projet a changé depuis la comparaison ; recommencer avant toute écriture.')
        unchanged()
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = self.destination['path']
        backup = backup_dir/(target.stem+'-'+self.destination['hash'][:16]+'.qgz')
        if backup.exists():
            if digest(backup) != self.destination['hash']:
                raise ValueError('Une sauvegarde différente porte déjà ce nom.')
        else:
            shutil.copy2(target, backup)
        unchanged()
        if digest(backup) != self.destination['hash']:
            raise ValueError('Sauvegarde incohérente ; destination inchangée.')
        fd, temporary = tempfile.mkstemp(prefix='.'+target.stem+'-', suffix='.qgz', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(self.payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, target.stat().st_mode & 0o777)
            unchanged()
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return backup


def compare(source, destination):
    if Path(source).resolve() == Path(destination).resolve():
        raise ValueError('Les projets source et destination doivent être distincts.')
    source, destination = archive(source), archive(destination)
    original, target = source['root'], destination['root']
    source_layers, target_layers = layers(original), layers(target)
    if source_layers.keys() != target_layers.keys() or relation_ids(original) != relation_ids(target):
        raise ValueError('Conflit : identifiants de couches ou relations différents ; intégration manuelle requise.')
    mapping = {}
    for identifier, layer in source_layers.items():
        old = target_layers[identifier]
        if layer.findtext('provider') != old.findtext('provider'):
            raise ValueError('Conflit de fournisseur pour '+layer.findtext('layername'))
        if layer.findtext('provider') != 'postgres':
            continue
        source_uri, target_uri = QgsDataSourceUri(layer.findtext('datasource')), QgsDataSourceUri(old.findtext('datasource'))
        if source_uri.password() or target_uri.password():
            raise ValueError('Un projet contient un mot de passe en clair ; aucun fichier remplacé.')
        if any(getattr(source_uri,n)() != getattr(target_uri,n)()
               for n in ('schema','table','geometryColumn','keyColumn','wkbType','srid')):
            raise ValueError('Conflit de table, géométrie ou filtre SQL pour '+layer.findtext('layername'))
        if source_uri.sql() != target_uri.sql() and not (
                source_uri.schema() == target_uri.schema() == 'gemapi'
                and source_uri.table() == target_uri.table() == 'cours_eau'
                and {source_uri.sql(), target_uri.sql()} == HYDRO_FILTERS):
            raise ValueError('Conflit de table, géométrie ou filtre SQL pour '+layer.findtext('layername'))
        key, value = source_uri.uri(False), old.findtext('datasource')
        if key in mapping and mapping[key] != value:
            raise ValueError('Copies de connexion ambiguës ; aucun fichier remplacé.')
        mapping[key] = value
    root = deepcopy(original)
    preserve_environment(root, target)
    # Les URI des widgets sont des copies locales de fournisseurs. Garder celles
    # de la destination, y compris son filtre hydrographique intentionnel.
    local_widget_uris = set()
    for identifier, layer in layers(root).items():
        old = target_layers[identifier]
        old_fields = {field.get('name'): field for field in old.findall('./fieldConfiguration/field')}
        for field in layer.findall('./fieldConfiguration/field'):
            target_field = old_fields.get(field.get('name'))
            if target_field is None:
                continue
            options = field.findall(".//Option[@name='ReferencedLayerDataSource']")
            target_options = target_field.findall(".//Option[@name='ReferencedLayerDataSource']")
            if len(options) != len(target_options):
                raise ValueError('Nombre de références de fournisseur différent pour '+layer.findtext('layername'))
            for option, target_option in zip(options, target_options):
                value = target_option.get('value', '')
                uri = QgsDataSourceUri(value)
                if uri.password():
                    raise ValueError('Une référence de widget contient un mot de passe en clair.')
                source_parent = next((p for p in field.iter() if option in p), None)
                source_layer_option = source_parent.find("Option[@name='ReferencedLayerId']") if source_parent is not None else None
                parent = next((p for p in target_field.iter() if target_option in p), None)
                layer_option = parent.find("Option[@name='ReferencedLayerId']") if parent is not None else None
                if source_layer_option is None or layer_option is None or source_layer_option.get('value') != layer_option.get('value'):
                    raise ValueError('Identifiant de référence de widget différent pour '+layer.findtext('layername'))
                referenced = target_layers.get(layer_option.get('value')) if layer_option is not None else None
                if referenced is None or referenced.findtext('provider') != 'postgres':
                    raise ValueError('Référence de widget non appariée dans '+layer.findtext('layername'))
                referenced_uri = QgsDataSourceUri(referenced.findtext('datasource'))
                if any(getattr(uri, key)() != getattr(referenced_uri, key)()
                       for key in ('schema', 'table', 'geometryColumn', 'keyColumn')):
                    raise ValueError('Source de widget incompatible avec sa couche dans '+layer.findtext('layername'))
                # Rafraîchir l'authentification depuis la couche destination.
                # Son filtre cartographique peut différer intentionnellement
                # de celui du widget, notamment pour les libellés hydrographiques.
                if uri.connectionInfo(False) != referenced_uri.connectionInfo(False):
                    referenced_uri.setSql(uri.sql())
                    value = referenced_uri.uri(False)
                option.set('value', value)
                local_widget_uris.add(option)
    target_uris = {QgsDataSourceUri(value).uri(False) for value in mapping.values()}
    copies = 0
    parents = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter():
        if element in local_widget_uris:
            continue
        for key, value in list(element.attrib.items()):
            if not value.startswith(('dbname=', 'service=')):
                continue
            uri = QgsDataSourceUri(value)
            if uri.password():
                raise ValueError('Une copie de connexion contient un mot de passe en clair.')
            # Les URI mises en cache peuvent être périmées après un changement
            # de source QGIS. L'identifiant stable de la couche fait foi.
            identifier = None
            if element.tag == 'layer-tree-layer' and key == 'source':
                identifier = element.get('id')
            elif element.tag == 'relation' and key == 'dataSource':
                identifier = element.get('layerId')
            elif element.tag == 'ProjectGpsSettings' and key == 'destinationLayerSource':
                identifier = element.get('destinationLayer')
            elif element.tag == 'Option' and key == 'value' and element.get('name') == 'ReferencedLayerDataSource':
                parent = parents.get(element)
                layer_option = parent.find("Option[@name='ReferencedLayerId']") if parent is not None else None
                identifier = layer_option.get('value') if layer_option is not None else None
            if identifier in target_layers and target_layers[identifier].findtext('provider') == 'postgres':
                element.set(key,target_layers[identifier].findtext('datasource'))
                copies += 1
            elif uri.uri(False) in mapping:
                element.set(key, mapping[uri.uri(False)])
                copies += 1
            elif uri.uri(False) not in target_uris:
                raise ValueError('Copie de connexion non appariée dans '+element.tag+'/'+key)
    gps, target_gps = root.find('./ProjectGpsSettings'), target.find('./ProjectGpsSettings')
    if gps is not None and target_gps is not None and gps.get('destinationLayer') == target_gps.get('destinationLayer'):
        if target_gps.get('destinationLayerSource') is None:
            gps.attrib.pop('destinationLayerSource', None)
        else:
            gps.set('destinationLayerSource', target_gps.get('destinationLayerSource'))
    if environment(root) != environment(target):
        raise ValueError('Une propriété propre à la destination aurait été modifiée.')

    source_resources = {entry.filename: data for entry,data in source['members'] if entry.filename != source['name']}
    target_resources = {entry.filename: data for entry,data in destination['members'] if entry.filename != destination['name']}
    if source_resources.keys() != target_resources.keys():
        raise ValueError('Conflit : ressources embarquées différentes ; vérifier les dépendances avant synchronisation.')
    for name in source_resources:
        if source_resources[name] != target_resources[name] and not name.endswith('_styles.db'):
            raise ValueError('Ressource embarquée à examiner explicitement : '+name)
    pending = canonical(root) != canonical(target) or source_resources != target_resources
    report = {'source': str(source['path']), 'destination': str(destination['path']),
        'source_sha256': source['hash'], 'destination_sha256': destination['hash'],
        'couches_postgresql': sum(l.findtext('provider')=='postgres' for l in source_layers.values()),
        'relations': len(relation_ids(root)),
        'modifications_a_reporter': configuration_changes(root,target),
        'environnement_preserve': ['sources et authentification', 'emprises projet et couches',
            'SCR', 'emprises des cartes de mise en page', 'signets, annotations et métadonnées locales'],
        'emprises_projet': {name: {'vue': node.find('mapcanvas/extent') is not None and
            {n.tag:n.text for n in node.find('mapcanvas/extent')},
            'defaut': dict(node.find('ProjectViewSettings/DefaultViewExtent').attrib)
            if node.find('ProjectViewSettings/DefaultViewExtent') is not None else None}
            for name,node in (('source',original),('destination',target))},
        'connexions_source': connection_summary(original), 'connexions_destination': connection_summary(target),
        'copies_connexion_actualisees': copies, 'modifications_necessaires': pending, 'conflits': []}
    prefix = source['xml'].split(b'<qgis',1)[0]
    xml = prefix+ET.tostring(root,encoding='utf-8')
    buffer = BytesIO()
    with ZipFile(buffer,'w') as output:
        output.comment = destination['comment']
        for entry,data in source['members']:
            entry = deepcopy(entry)
            if entry.filename == source['name']:
                entry.filename = destination['name']
                data = xml
            output.writestr(entry,data)
    return SyncPlan(source,destination,root,buffer.getvalue(),report,pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source', type=Path, default=REFERENCE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--generer', action='store_true', help='Générer une copie de référence dans un nouveau fichier.')
    mode.add_argument('--simuler', action='store_true', help='Comparer sans écrire (mode par défaut).')
    mode.add_argument('--appliquer', action='store_true', help='Sauvegarder puis écrire la destination.')
    mode.add_argument('--verifier', action='store_true', help='Contrôler la synchronisation sans écrire.')
    parser.add_argument('--sauvegardes', type=Path, default=ROOT/'audits/synchronisation_qgis')
    parser.add_argument('--rapport', type=Path, help='Écrire explicitement le bilan JSON, sans secrets.')
    args = parser.parse_args()
    try:
        if args.generer:
            if args.source != REFERENCE or args.rapport:
                parser.error('La génération utilise le projet de référence versionné.')
            if args.destination.suffix.lower() != '.qgz':
                parser.error('Un chemin .qgz est requis.')
            with tempfile.TemporaryDirectory() as folder:
                template = Path(folder) / REFERENCE.name
                shutil.copyfile(REFERENCE, template)
                plan = compare(REFERENCE, template)
                # Publication atomique d'un nouveau fichier dans le même répertoire.
                with tempfile.NamedTemporaryFile(dir=args.destination.parent, delete=False) as output:
                    staged = Path(output.name)
                    try:
                        output.write(plan.payload)
                        output.flush()
                        os.fsync(output.fileno())
                        output.close()
                        os.link(staged, args.destination)
                    finally:
                        staged.unlink(missing_ok=True)
            return
        plan = compare(args.source, args.destination)
        if args.rapport and (args.rapport.suffix.lower() != '.json' or args.rapport.resolve()
                            in {args.source.resolve(),args.destination.resolve()}):
            raise ValueError('Le rapport doit être un fichier .json distinct des projets.')
        print(json.dumps(plan.report,ensure_ascii=False,indent=2))
        if args.rapport:
            args.rapport.parent.mkdir(parents=True,exist_ok=True)
            args.rapport.write_text(json.dumps(plan.report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if args.verifier:
            if plan.pending:
                parser.exit(1,'Des modifications de configuration restent à reporter.\n')
            print('Configurations synchronisées ; connexions et emprises propres conservées.')
        elif args.appliquer:
            backup = plan.apply(args.sauvegardes)
            print('Sauvegarde : '+str(backup) if backup else 'Aucune écriture nécessaire.')
            if backup:
                print('Destination synchronisée ; source inchangée.')
        else:
            print('Simulation uniquement. Pour écrire, relancer avec --appliquer.')
    except (ValueError,OSError) as error:
        parser.exit(2,'Conflit : '+str(error)+'\n')


if __name__ == '__main__':
    main()
