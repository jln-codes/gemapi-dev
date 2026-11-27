"""Bounded, read-only field scope; the same authoritative detail readers as the UI.

A repeatable-read snapshot contains both the journal head and the resource state.
No territory, external datasets or media is persisted here. The bounded
Photo referencing graph is kept separately from versioned business resources.
"""
from uuid import UUID

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse

from .database import business_crs_payload, open_read_connection, read_business_crs
from . import queries
from .reperage_references import scope_graph

MAX_ROOTS = 20
MAX_RECORDS = 2000


def scope_too_large():
    return HTTPException(422, {"code": "scope-too-large", "message": "Périmètre trop volumineux : réduisez la sélection."})


def read_scope(connection, troncon_ids):
    roots = sorted(set(troncon_ids))
    if not 1 <= len(roots) <= MAX_ROOTS:
        raise HTTPException(422, f"Sélectionnez de 1 à {MAX_ROOTS} tronçons pour la consultation hors ligne.")
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        business_crs = read_business_crs(connection)
        head = connection.execute(
            "SELECT COALESCE(MAX(sequence_id), 0) FROM technique.evenements_ressources"
        ).fetchone()[0]
        disorders = [row[0] for row in connection.execute(
            "SELECT DISTINCT desordre_id FROM public.desordres__troncons "
            "WHERE troncon_id = ANY(%s::uuid[]) LIMIT %s", (roots, MAX_RECORDS + 1)
        ).fetchall()]
        if len(disorders) > MAX_RECORDS:
            raise scope_too_large()
        trunks = [row[0] for row in connection.execute(
            "SELECT id FROM public.troncons WHERE id = ANY(%s::uuid[]) OR id IN "
            "(SELECT troncon_id FROM public.desordres__troncons "
            "WHERE desordre_id = ANY(%s::uuid[])) LIMIT %s",
            (roots, disorders, MAX_RECORDS + 1),
        ).fetchall()]
        if not set(roots).issubset(trunks):
            raise HTTPException(404, {"code": "scope-reference-missing",
                                      "message": "Un tronçon sélectionné n'est plus accessible. Actualisez la sélection."})
        observations = [row[0] for row in connection.execute(
            "SELECT id FROM public.observations WHERE desordre_id = ANY(%s::uuid[]) "
            "OR troncon_id = ANY(%s::uuid[]) OR berge_id = ANY(%s::uuid[]) "
            "OR structure_digue_id = ANY(%s::uuid[]) LIMIT %s",
            (disorders, trunks, trunks, trunks, MAX_RECORDS + 1),
        ).fetchall()]
        if len(trunks) + len(disorders) + len(observations) > MAX_RECORDS:
            raise scope_too_large()
        records = []

        def add(resource, payload):
            attributes = payload.get("properties", payload)
            records.append({"resource": resource, "id": str(attributes["id"]),
                            "serverVersion": attributes["version"], "payload": payload})
            if len(records) > MAX_RECORDS:
                raise scope_too_large()

        digues = set()
        for identifier in trunks:
            payload = queries.fetch_troncon(connection, identifier)
            if payload["properties"].get("digue_id"):
                digues.add(UUID(payload["properties"]["digue_id"]))
            add("troncons", payload)
        systemes = set()
        for identifier in sorted(digues):
            payload = queries.fetch_digue(connection, identifier)
            if payload.get("systeme_endiguement_id"):
                systemes.add(UUID(payload["systeme_endiguement_id"]))
            add("digues", payload)
        for identifier in sorted(systemes):
            add("systemes", queries.fetch_systeme_endiguement(connection, identifier))
        # Parents count towards the existing limit too. Classify an oversized
        # scope before reading its children (rather than timing out halfway and
        # misdiagnosing an unrecoverable scope as a network failure).
        if len(records) + len(disorders) + len(observations) > MAX_RECORDS:
            raise scope_too_large()
        for payload in queries.fetch_desordres_batch(connection, disorders):
            add("desordres", payload)
        for identifier in observations:
            payload = queries.fetch_observation(connection, identifier, include_photos=False)
            # Media are explicitly not prepared. No filenames/URLs/blobs stored.
            payload.pop("photos", None)
            # Typed historical parent roles remain intact. They can be read
            # offline but are outside the approved offline mutation perimeter.
            if payload.get("parent", {}).get("resource") not in ("desordres", "troncons"):
                payload["offline_read_only"] = True
            add("observations", payload)
        # Read references under the very same REPEATABLE READ snapshot as the
        # journal head and business graph; never expose a mixed calibration.
        references = scope_graph(
            connection, trunks, business_crs=business_crs
        )
        observation_references = {
            "suitesApporter": queries.fetch_reference_catalog(connection, "suites-apporter")["references"],
            "urgences": queries.fetch_reference_catalog(connection, "urgences")["references"],
        }
        if any(len(rows) > 500 for rows in observation_references.values()):
            raise scope_too_large()
        return {"schemaVersion": 3, "cursor": str(head),
                "crs": business_crs_payload(business_crs),
                "tronconIds": [str(value) for value in roots], "records": records,
                "reperageReferences": references, "observationReferences": observation_references}


def read_pinned(connection, resource, identifier):
    """Read-only recovery of one intention and its parents, never a scope/cursor.

    The normal snapshot limit and transaction remain unchanged. No siblings,
    observations of a disorder or media are included in this recovery graph.
    """
    if resource not in ("desordres", "observations"):
        raise HTTPException(422, "Ressource non prise en charge pour la récupération.")
    readers = {"desordres": queries.fetch_desordre, "observations": queries.fetch_observation,
               "troncons": queries.fetch_troncon, "digues": queries.fetch_digue,
               "systemes": queries.fetch_systeme_endiguement}
    records, seen = [], set()
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

        def add(kind, key):
            if not key or (kind, str(key)) in seen:
                return
            seen.add((kind, str(key)))
            try:
                payload = readers[kind](connection, UUID(str(key)), **(
                    {"include_photos": False} if kind == "observations" else {}))
            except (queries.DesordreNotFoundError, queries.ObservationNotFoundError,
                    queries.HeritageNotFoundError):
                return
            if payload is None:
                return
            if kind == "observations":
                parent = payload.get("parent", {})
                if parent.get("resource") not in ("desordres", "troncons") and parent.get("canonical_resource") != "troncons":
                    return
                if parent.get("resource") not in ("desordres", "troncons"):
                    payload["offline_read_only"] = True
                payload.pop("photos", None)
            values = payload.get("properties", payload)
            records.append({"resource": kind, "id": str(values["id"]),
                            "serverVersion": values["version"], "payload": payload})
            if kind == "observations":
                parent = values["parent"]
                add(parent.get("canonical_resource", parent["resource"]), parent["id"])
            elif kind == "desordres":
                for trunk in values.get("troncon_ids", []):
                    add("troncons", trunk)
            elif kind == "troncons":
                add("digues", values.get("digue_id"))
            elif kind == "digues":
                add("systemes", values.get("systeme_endiguement_id"))

        add(resource, identifier)
    return {"resource": resource, "id": str(identifier), "records": records}


def register_routes(application):
    @application.get("/api/offline-scope/pinned/{resource}/{identifier}")
    def pinned_resource(resource: str, identifier: UUID):
        with open_read_connection() as connection:
            result = read_pinned(connection, resource, identifier)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @application.get("/api/offline-scope")
    def offline_scope(troncon_id: list[UUID] = Query()):
        with open_read_connection() as connection:
            result = read_scope(connection, troncon_id)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
