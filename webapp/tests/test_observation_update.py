"""LOT 4A-bis : contrat partiel et transaction générique."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from digues_webapp.models import ObservationUpdate
from digues_webapp.queries import OBSERVATION_PARENTS, OBSERVATION_READ_PARENTS, update_observation
from webapp.tests import test_observation_detail as detail_fixtures
from webapp.tests.test_resource_versions import http_client
from webapp.tests.test_web_references import Connection, Cursor


class ObservationUpdateContractTest(unittest.TestCase):
    def test_partial_normalization_dates_and_zero(self):
        update = ObservationUpdate(designation="  Revue  ", evolution="  ", suite=None,
                                   date="2026-09-19", nombre_desordres=0)
        self.assertEqual(update.model_dump(mode="json", exclude_unset=True), {
            "designation": "Revue", "evolution": None, "suite": None,
            "date": "2026-09-19", "nombre_desordres": 0,
        })
        self.assertNotIn("urgence_id", update.model_fields_set)
        self.assertEqual(ObservationUpdate(date=None).model_dump(exclude_unset=True), {"date": None})

    def test_invalid_values_and_every_technical_field_are_forbidden(self):
        payloads = [{}, {"date": "2026-02-30"}]
        payloads += [{"nombre_desordres": value} for value in (-1, True, 1.2, "2", 2147483648)]
        payloads += [{key: None} for key in (
            "valid", "observateur_source_id", "parent", "version", "suite_apporter_source_id",
            "unknown", *(fk for fk, _, _ in OBSERVATION_READ_PARENTS),
        )]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ObservationUpdate(**payload)

    def test_all_parent_families_update_only_supplied_columns(self):
        for index, (_, resource, _) in enumerate(OBSERVATION_PARENTS):
            row = [None] * (len(OBSERVATION_READ_PARENTS) + 2)
            row[index] = uuid4()
            identifier = uuid4()
            cursor = Cursor(fetchone_results=[tuple(row), (identifier,)])
            with self.subTest(resource=resource), patch("digues_webapp.queries.fetch_observation", return_value={}):
                update_observation(Connection(cursor), identifier, ObservationUpdate(designation=" Revue ", date=None))
                read, write = cursor.executions
                self.assertIn("FOR UPDATE", read[0])
                for fk, _, _ in OBSERVATION_READ_PARENTS:
                    self.assertIn(fk, read[0])
                self.assertEqual(write, (
                    "UPDATE public.observations SET designation = %s, date_observation = %s WHERE id = %s RETURNING id",
                    ("Revue", None, identifier),
                ))

    def test_conditional_fields_even_null_rejected_for_other_families(self):
        for index in range(1, len(OBSERVATION_PARENTS)):
            for key in ("urgence_id", "nombre_desordres"):
                row = [None] * (len(OBSERVATION_READ_PARENTS) + 2)
                row[index] = uuid4()
                cursor = Cursor(fetchone_results=[tuple(row)])
                with self.subTest(index=index, key=key), self.assertRaises(HTTPException) as error:
                    update_observation(Connection(cursor), uuid4(), ObservationUpdate(**{key: None}))
                self.assertEqual(error.exception.status_code, 422)
                self.assertEqual(len(cursor.executions), 1)

    def test_unchanged_historical_references_are_not_revalidated(self):
        identifier = uuid4()
        row = (uuid4(),) + (None,) * (len(OBSERVATION_READ_PARENTS) - 1) + (
            "old-suite", "old-urgency"
        )
        cursor = Cursor(fetchone_results=[row, (identifier,)])
        with patch("digues_webapp.queries.fetch_observation", return_value={}):
            update_observation(Connection(cursor), identifier,
                               ObservationUpdate(suite_apporter_id="old-suite", urgence_id="old-urgency"))
        self.assertEqual(len(cursor.executions), 2)
        self.assertFalse(any("FROM public.ref_" in sql for sql, _ in cursor.executions))


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class ObservationUpdatePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        detail_fixtures.ObservationDetailPostgresTest.setUpClass()
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]

    def setUp(self):
        detail_fixtures.ObservationDetailPostgresTest.setUp(self)

    def test_common_fields_all_parents_receipt_collision_and_single_event(self):
        suite = str(uuid4())
        self.connection.execute("INSERT INTO referentiel.suites_apporter(id,designation,libelle,valid) VALUES (%s,'Suite','Suite',true)", (suite,))
        with http_client(self.app) as client:
            for resource, identifier in self.observations.items():
                with self.subTest(resource=resource):
                    url = f"/api/observations/{identifier}"
                    operation = str(uuid4())
                    headers = {"If-Match": '"1"', "Operation-Id": operation}
                    changes = {"designation": "  Revue  ", "date": "2026-09-19", "evolution": "Stable",
                               "suite_apporter_id": suite, "suite": "À suivre"}
                    self.assertEqual(client.put(url, json=changes).status_code, 428)
                    self.assertEqual(client.put(url, json=changes, headers={"If-Match": "bad"}).status_code, 422)
                    response = client.put(url, json=changes, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    saved = response.json()
                    self.assertEqual(saved["version"], 2)
                    self.assertEqual(saved["designation"], "Revue")
                    self.assertEqual(saved["parent"]["resource"], resource)
                    self.assertEqual(saved["mutation"]["versions"], {str(identifier): 2})
                    self.assertEqual(client.put(url, json=changes, headers=headers).json(), saved)
                    self.assertEqual(client.put(url, json={**changes, "suite": None}, headers=headers).status_code, 409)
                    self.assertEqual(client.put(url, json=changes, headers={"If-Match": '"1"'}).status_code, 409)
                    self.assertEqual(self.connection.execute(
                        "SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s", (str(identifier),)
                    ).fetchone()[0], 1)
                    self.assertEqual(self.connection.execute(
                        "SELECT count(*) FROM technique.operations_ressources WHERE operation_id=%s", (operation,)
                    ).fetchone()[0], 1)
                    cleared = client.put(url, json={"date": None, "suite": "  "}, headers={"If-Match": '"2"'})
                    self.assertEqual(cleared.status_code, 200, cleared.text)
                    self.assertIsNone(cleared.json()["date"])
                    self.assertIsNone(cleared.json()["suite"])
                    self.assertEqual(cleared.json()["evolution"], "Stable")
                    self.assertEqual(cleared.json()["suite_apporter_id"], suite)

    def test_conditional_fields_and_rejected_mutations_leave_no_journal_or_receipt(self):
        urgency = str(uuid4())
        self.connection.execute("INSERT INTO referentiel.urgences(id,libelle,valid) VALUES (%s,'Urgent',true)", (urgency,))
        with http_client(self.app) as client:
            for resource, identifier in self.observations.items():
                url = f"/api/observations/{identifier}"
                headers = {"If-Match": '"1"', "Operation-Id": str(uuid4())}
                if resource == "desordres":
                    response = client.put(url, json={"urgence_id": urgency, "nombre_desordres": 0}, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["urgence_id"], urgency)
                    self.assertEqual(response.json()["nombre_desordres"], 0)
                    self.assertEqual(response.json()["version"], 2)
                    continue
                for changes in ({"urgence_id": None}, {"nombre_desordres": None}, {"nombre_desordres": 1},
                                {"urgence_id": urgency}, {"suite_apporter_id": "missing"}, {}, {"valid": False},
                                {"observateur_source_id": "x"}, {"parent": {}}, {"desordre_id": None}):
                    response = client.put(url, json=changes, headers=headers)
                    self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(client.get(url).json()["version"], 1)
                self.assertEqual(self.connection.execute(
                    "SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s", (str(identifier),)
                ).fetchone()[0], 0)
                self.assertEqual(self.connection.execute(
                    "SELECT count(*) FROM technique.operations_ressources WHERE operation_id=%s", (headers["Operation-Id"],)
                ).fetchone()[0], 0)

    def test_valid_parent_and_inactive_references_are_preserved(self):
        identifier = self.observations["desordres"]
        suite, urgency = str(uuid4()), str(uuid4())
        self.connection.execute("INSERT INTO referentiel.suites_apporter(id,designation,libelle,valid) VALUES (%s,'Ancienne','Ancienne',false)", (suite,))
        self.connection.execute("INSERT INTO referentiel.urgences(id,libelle,valid) VALUES (%s,'Ancienne',false)", (urgency,))
        self.connection.execute("UPDATE observations SET valid=false, "
                                "suite_apporter_id=%s, urgence_id=%s "
                                "WHERE id=%s", (suite, urgency, identifier))
        with http_client(self.app) as client:
            url = f"/api/observations/{identifier}"
            initial = client.get(url).json()
            response = client.put(url, json={"designation": "Revue", "suite_apporter_id": suite, "urgence_id": urgency},
                                  headers={"If-Match": f'"{initial["version"]}"'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["parent"], initial["parent"])
        row = self.connection.execute("SELECT valid, suite_apporter_id, urgence_id, "
                                      "observateur_id "
                                      "FROM observations WHERE id=%s", (identifier,)).fetchone()
        self.assertEqual(row, (False, suite, urgency, None))

    def test_omission_and_explicit_null_have_distinct_idempotent_intentions(self):
        identifier = self.observations["troncons"]
        with http_client(self.app) as client:
            url = f"/api/observations/{identifier}"
            headers = {"If-Match": '"1"', "Operation-Id": str(uuid4())}
            response = client.put(url, json={"designation": "Revue"}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            replay = client.put(url, json={"designation": " Revue "}, headers=headers)
            self.assertEqual(replay.json(), response.json())
            collision = client.put(url, json={"designation": "Revue", "suite": None}, headers=headers)
            self.assertEqual(collision.status_code, 409)
            self.assertEqual(collision.json()["detail"]["code"], "operation-id-collision")
            self.assertEqual(client.get(url).json()["version"], 2)
