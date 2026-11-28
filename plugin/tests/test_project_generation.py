"""Recette du QGZ de référence et de la synchronisation sur des copies fictives."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from qgis.core import QgsDataSourceUri
from projet_qgis import REFERENCE, archive, canonical, compare, digest
from test_postgres_integration import command


class ProjetDemo(unittest.TestCase):
    def test_generation_et_synchronisation(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = (Path(tmp) / name for name in ("premier.qgz", "second.qgz"))
            script = Path(__file__).resolve().parents[1] / "tools/projet_qgis.py"
            for path in (first, second):
                command(sys.executable, str(script), "--generer", str(path))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertNotEqual(command(sys.executable, str(script), "--generer", str(first), check=False).returncode, 0)
            reference, built = archive(REFERENCE), archive(first)
            self.assertEqual(canonical(reference["root"]), canonical(built["root"]))
            self.assertEqual({e.filename: b for e,b in reference["members"] if e.filename.endswith(".db")},
                             {e.filename: b for e,b in built["members"] if e.filename.endswith(".db")})
            layers = built["root"].findall("./projectlayers/maplayer")
            self.assertEqual(sum(l.findtext("provider")=="postgres" for l in layers), 32)
            for element in built["root"].iter():
                for value in (*element.attrib.values(),element.text or ""):
                    if value.startswith(("dbname=", "service=")):
                        uri = QgsDataSourceUri(value)
                        self.assertEqual((uri.service(),uri.schema()), ("gemapi_demo","gemapi"))
                        self.assertFalse(any((uri.password(),uri.username(),uri.host(),uri.database(),uri.authConfigId())))
            # Exercer l'application d'une différence graphique et sa sauvegarde.
            from zipfile import ZipFile
            original = archive(second)
            with ZipFile(second, 'w') as output:
                for entry,data in original["members"]:
                    if entry.filename == original["name"]:
                        self.assertIn(b"<layername>",data)
                        data = data.replace(b"<layername>",b"<layername>Test ",1)
                    output.writestr(entry,data)
            previous = digest(second)
            plan = compare(first,second)
            self.assertTrue(plan.pending)
            backup = plan.apply(Path(tmp)/"sauvegardes")
            self.assertEqual(digest(backup), previous)
            self.assertFalse(compare(first,second).pending)


if __name__ == "__main__":
    unittest.main()
