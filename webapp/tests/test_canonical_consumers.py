"""Level A (<1 s): fresh-install consumers cannot query removed physical tables.

This structural guard proves a SQL naming invariant, not business behavior.
Historical UUID namespaces, typed Observation roles and frozen proof fixtures
remain legal. Runtime behavior is covered by the PostgreSQL/browser C6 group.
"""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
OLD_SQL = re.compile(r'\b(?:FROM|JOIN|UPDATE|INTO|REFERENCES|TRUNCATE(?:\s+TABLE)?)\s+(?:public\.)?'
                     r'(?:troncons_unifies|berges|structures_digue|systemes_reperage_berges|reperages_berges__bornes)\b', re.I)


def obsolete_queries():
    result=[]
    for folder in ('digues_app','webapp/backend','tests','webapp/tests'):
        for file in sorted((ROOT/folder).rglob('*.py')):
            if 'build' in file.parts or file.name == Path(__file__).name:
                continue
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and OLD_SQL.search(node.value):
                    result.append((str(file.relative_to(ROOT)),node.lineno,OLD_SQL.search(node.value).group()))
    return result


class CanonicalConsumerNamesTest(unittest.TestCase):
    def test_no_executable_sql_targets_removed_troncon_extensions(self):
        self.assertEqual(obsolete_queries(),[])
