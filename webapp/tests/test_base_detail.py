"""Niveau A : contrats de lecture Base et couverture de migration (<5 s).

Sans ressource mutable externe ; les scénarios HTTP/SQL réels appartiennent
au banc PostgreSQL dédié. Chaque programme isole ses ports et son runtime.
"""
from pathlib import Path
import subprocess
import unittest


class BaseDetailContractTest(unittest.TestCase):
    def run_node(self, filename):
        result = subprocess.run(
            ['node', str(Path(__file__).with_name(filename))],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_read_contract_navigation_and_invalidation(self):
        self.run_node('test_base_detail.js')

    def test_all_previous_read_fields_remain_represented(self):
        self.run_node('test_base_detail_coverage.js')
