"""Niveau A : la page HTML de démonstration ne s'ouvre qu'en dev/test opt-in.

État initial : aucune base ni fichier mutable ; groupe unittest webapp, < 5 s.
Le cas négatif protège le déploiement standard sans variable d'activation.
"""

import os
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from digues_webapp.app import create_app


class LidarPrototypePageTest(TestCase):
    def test_page_is_404_without_both_development_and_explicit_opt_in(self):
        with TestClient(create_app()) as client:
            with patch.dict(os.environ, {"DIGUES_ENVIRONMENT": "production",
                                      "LIDAR_PROTOTYPE_ENABLED": "1"}):
                self.assertEqual(client.get('/dev/lidar').status_code, 404)
            with patch.dict(os.environ, {"DIGUES_ENVIRONMENT": "test",
                                      "LIDAR_PROTOTYPE_ENABLED": "0"}):
                self.assertEqual(client.get('/dev/lidar').status_code, 404)
            with patch.dict(os.environ, {"DIGUES_ENVIRONMENT": "test",
                                      "LIDAR_PROTOTYPE_ENABLED": "1"}):
                page = client.get('/dev/lidar')
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.headers['cache-control'], 'no-store')
                self.assertIn('Prototype MNT LiDAR', page.text)
