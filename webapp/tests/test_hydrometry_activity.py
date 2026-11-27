import json
from datetime import datetime, timezone
from threading import Lock
import unittest

import requests

from digues_webapp.hydrometry import (
    ACTIVITY_BATCH_SIZE,
    HUBEAU_OBSERVATIONS_URL,
    HydrometryClient,
    HydrometryDataError,
)


START = datetime(2026, 10, 2, tzinfo=timezone.utc)
END = datetime(2026, 10, 3, tzinfo=timezone.utc)


def measurement(code, quantity, value=1000, source_time="2026-10-02T23:45:00Z"):
    return {
        "code_station": code,
        "grandeur_hydro": quantity,
        "date_obs": source_time,
        "resultat_obs": value,
    }


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.body = json.dumps(payload).encode("utf-8")
        self.url = HUBEAU_OBSERVATIONS_URL
        self.status_code = status_code

    def iter_content(self, chunk_size):
        del chunk_size
        return [self.body]

    def close(self):
        pass


class ActivitySession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.lock = Lock()

    def get(self, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        with self.lock:
            self.calls.append(params)
        result = self.handler(params)
        if isinstance(result, BaseException):
            raise result
        return FakeResponse(result)


class HydrometryActivityClientTest(unittest.TestCase):
    def test_batches_are_bounded_and_match_per_station_results(self):
        codes = [f"S{index:02d}" for index in range(ACTIVITY_BATCH_SIZE + 5)]

        def handler(params):
            quantity = params["grandeur_hydro"]
            rows = [
                measurement(code, quantity, 1000 + index)
                for index, code in enumerate(params["code_entite"].split(","))
            ]
            return {"count": len(rows), "data": rows, "next": None}

        session = ActivitySession(handler)
        result = HydrometryClient(session=session).recent_activity(
            codes, start_time=START, end_time=END
        )

        self.assertEqual(len(session.calls), 4)
        self.assertEqual(
            sorted(len(call["code_entite"].split(",")) for call in session.calls),
            [5, 5, ACTIVITY_BATCH_SIZE, ACTIVITY_BATCH_SIZE],
        )
        self.assertTrue(all(call["grandeur_hydro"] in {"H", "Q"} for call in session.calls))
        self.assertEqual(result["S00"]["H"]["native_value"], 1000)
        self.assertEqual(result["S00"]["Q"]["native_value"], 1000)
        self.assertEqual(result["S24"]["H"]["native_value"], 1004)

    def test_cursor_pagination_is_complete(self):
        def handler(params):
            quantity = params["grandeur_hydro"]
            if quantity == "Q":
                return {"count": 0, "data": [], "next": None}
            if params.get("cursor") == "page-2":
                return {
                    "count": 2,
                    "data": [measurement("B", "H", 2000)],
                    "next": None,
                }
            return {
                "count": 2,
                "data": [measurement("A", "H", 1000)],
                "next": f"{HUBEAU_OBSERVATIONS_URL}?cursor=page-2",
            }

        session = ActivitySession(handler)
        result = HydrometryClient(session=session).recent_activity(
            ["A", "B"], start_time=START, end_time=END
        )

        self.assertIsNotNone(result["A"]["H"])
        self.assertIsNotNone(result["B"]["H"])
        self.assertTrue(any(call.get("cursor") == "page-2" for call in session.calls))

    def test_incomplete_page_is_reported_without_losing_other_quantity(self):
        def handler(params):
            if params["grandeur_hydro"] == "H":
                return {
                    "count": 2,
                    "data": [measurement("A", "H")],
                    "next": None,
                }
            return {
                "count": 1,
                "data": [measurement("A", "Q", 2500)],
                "next": None,
            }

        client = HydrometryClient(session=ActivitySession(handler))
        result = client.recent_activity(["A"], start_time=START, end_time=END)

        self.assertIsNone(result["A"]["H"])
        self.assertEqual(result["A"]["Q"]["native_value"], 2500)
        self.assertEqual(client.last_activity_errors, [{
            "quantity": "H",
            "station_count": 1,
            "error": "unavailable",
        }])

    def test_height_only_discharge_only_and_both_are_independent(self):
        values = {
            ("H", "H_ONLY"): 1100,
            ("Q", "Q_ONLY"): 2200,
            ("H", "BOTH"): 3300,
            ("Q", "BOTH"): 4400,
        }

        def handler(params):
            quantity = params["grandeur_hydro"]
            rows = [
                measurement(code, quantity, value)
                for code in params["code_entite"].split(",")
                if (value := values.get((quantity, code))) is not None
            ]
            return {"count": len(rows), "data": rows, "next": None}

        result = HydrometryClient(
            session=ActivitySession(handler)
        ).recent_activity(
            ["H_ONLY", "Q_ONLY", "BOTH"], start_time=START, end_time=END
        )

        self.assertIsNotNone(result["H_ONLY"]["H"])
        self.assertIsNone(result["H_ONLY"]["Q"])
        self.assertIsNone(result["Q_ONLY"]["H"])
        self.assertIsNotNone(result["Q_ONLY"]["Q"])
        self.assertIsNotNone(result["BOTH"]["H"])
        self.assertIsNotNone(result["BOTH"]["Q"])

    def test_timeout_of_one_quantity_keeps_the_other(self):
        def handler(params):
            if params["grandeur_hydro"] == "Q":
                return requests.Timeout("private timeout detail")
            return {
                "count": 1,
                "data": [measurement("A", "H")],
                "next": None,
            }

        client = HydrometryClient(session=ActivitySession(handler))
        result = client.recent_activity(["A"], start_time=START, end_time=END)

        self.assertIsNotNone(result["A"]["H"])
        self.assertIsNone(result["A"]["Q"])
        self.assertEqual(client.last_activity_errors[0]["quantity"], "Q")

    def test_batch_guard_rejects_incomplete_payload_directly(self):
        session = ActivitySession(lambda _params: {
            "count": 2,
            "data": [measurement("A", "H")],
            "next": None,
        })
        client = HydrometryClient(session=session)
        with self.assertRaises(HydrometryDataError):
            client._recent_activity_batch(
                ["A"], "H", start_time=START, end_time=END
            )


if __name__ == "__main__":
    unittest.main()
