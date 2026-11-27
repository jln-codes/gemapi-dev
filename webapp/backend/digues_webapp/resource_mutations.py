"""Transaction boundary for confirmed HTTP mutations and optimistic concurrency.

The receipt, operation identity and durable journal share the transaction.
"""
from time import time_ns
from uuid import UUID, uuid4
import hashlib
import json

from psycopg.types.json import Jsonb

from fastapi import HTTPException
from .resource_journal import append_event


RESOURCE_TABLES = {
    "systemes": "systemes", "digues": "digues", "troncons": "troncons",
    "desordres": "desordres", "mobilier": "mobilier",
    "equipements-mesure": "equipements_mesure",
    "reseaux-techniques": "reseaux_techniques",
    "ouvrages-particuliers": "ouvrages_particuliers",
    "cheminements": "cheminements",
    "vegetation": "vegetation",
    "amenagements-hydrauliques": "amenagements_hydrauliques",
    "zones-stockage": "zones_stockage",
    "prestations": "prestations",
    "observations": "observations", "photos": "photos",
    "troncons-lit": "segments_hydrographiques",
    "elements-morphologiques-lit": "elements_morphologiques_lit",
}


def parse_base_version(value):
    if value is None:
        raise HTTPException(428, "Version de départ obligatoire (If-Match). Relisez la fiche.")
    # Exactly one strong ETag. Wildcards would silently disable concurrency control.
    if not isinstance(value, str) or not value.startswith('"') or not value.endswith('"'):
        raise HTTPException(422, "If-Match doit contenir une version entière entre guillemets.")
    number = value[1:-1]
    if (len(number) > 16 or not number.isascii() or not number.isdigit()
            or not 1 <= int(number) <= 9007199254740991):
        raise HTTPException(422, "Version de départ invalide.")
    return int(number)


def _operation_identity(resource, action, identifier, base_version, operation_id, changes):
    if operation_id is None:
        return None
    try:
        operation_id = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Operation-Id doit être un UUID.") from None
    supported = (resource in {"desordres", "mobilier", "equipements-mesure", "reseaux-techniques", "ouvrages-particuliers", "cheminements", "vegetation", "amenagements-hydrauliques", "zones-stockage", "prestations", "observations", "photos", "troncons-lit", "elements-morphologiques-lit"} and action == "update") or (
        resource in {"observations", "photos", "prestations", "troncons-lit", "elements-morphologiques-lit"} and action == "create" and identifier is not None
    )
    if not supported or changes is None:
        raise HTTPException(422, "Idempotence non disponible pour cette opération.")
    intention = {"resource": resource, "id": str(identifier), "action": action,
                 "baseVersion": base_version, "changes": changes}
    fingerprint = hashlib.sha256(json.dumps(
        intention, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()
    return operation_id, fingerprint


def _checked_operation_response(executor, operation_id, fingerprint):
    executor.execute(
        "SELECT fingerprint, response FROM technique.operations_ressources WHERE operation_id=%s",
        (operation_id,),
    )
    previous = executor.fetchone()
    if previous:
        if previous[0] != fingerprint:
            raise HTTPException(409, {"code": "operation-id-collision",
                                     "message": "Operation-Id déjà utilisé avec une intention différente."})
        return previous[1]
    return None


def read_operation_receipt(connection, resource, action, *, identifier=None,
                           if_match=None, operation_id=None, changes=None):
    """Read a committed receipt without creating a new mutation.

    This is used only when an endpoint may legitimately return an already
    attached result without advancing the aggregate version. A concurrent
    uncommitted operation remains handled by ``execute_mutation``.
    """
    base_version = parse_base_version(if_match) if action == "update" else None
    identity = _operation_identity(
        resource, action, identifier, base_version, operation_id, changes,
    )
    if identity is None:
        return None
    operation_id, fingerprint = identity
    row = connection.execute(
        "SELECT fingerprint, response FROM technique.operations_ressources WHERE operation_id=%s",
        (operation_id,),
    ).fetchone()
    if row:
        if row[0] != fingerprint:
            raise HTTPException(409, {"code": "operation-id-collision",
                                     "message": "Operation-Id déjà utilisé avec une intention différente."})
        return row[1]
    return None


def execute_mutation(connection, resource, action, write, *, identifier=None, if_match=None, origin=None,
                     operation_id=None, changes=None):
    table = RESOURCE_TABLES[resource]
    base_version = parse_base_version(if_match) if action == "update" else None
    identity = _operation_identity(
        resource, action, identifier, base_version, operation_id, changes,
    )
    if identity is not None:
        operation_id, fingerprint = identity
    else:
        fingerprint = None
    with connection.transaction():
        with connection.cursor() as cursor:
            if operation_id:
                # Transactional per-operation serialization, BEFORE row/OCC/journal locks.
                # Hash collisions only serialize unrelated requests; UUID+fingerprint
                # remain the identity. Nothing is committed before the business write.
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 1011))", (operation_id,))
                previous = _checked_operation_response(cursor, operation_id, fingerprint)
                if previous is not None:
                    return previous
            if resource in {"troncons", "amenagements-hydrauliques", "photos", "desordres"}:
                # Same per-database order for direct Tronçon and AH composition:
                # operation -> family -> rows -> journal. No business lock may
                # be acquired after journal allocation (including related events).
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (724193508,))
            if action == "update":
                cursor.execute(f"SELECT version FROM public.{table} WHERE id = %s FOR UPDATE", (identifier,))
                row = cursor.fetchone()
                if row is None:
                    raise HTTPException(404, "Objet introuvable.")
                if row[0] != base_version:
                    raise HTTPException(409, {
                        "code": "version-conflict", "resource": resource,
                        "id": str(identifier), "baseVersion": base_version,
                        "currentVersion": row[0],
                        "message": "Cet objet a changé. Votre brouillon est conservé ; annulez les modifications puis relisez avant de réessayer.",
                    })
            # Only a business connection that checked its tenant-scoped Contact
            # association may attribute this human mutation. A replay returned
            # above and import/maintenance connections do not opt in.
            cursor.execute("SELECT current_setting('digues.actor_checked', true)")
            actor_contact_id = None
            if cursor.fetchone()[0] == 'on':
                cursor.execute("SELECT nullif(current_setting('digues.actor_contact_id', true), '')::uuid")
                contact_id = cursor.fetchone()[0]
                if contact_id is not None:
                    cursor.execute('SELECT 1 FROM public.contacts WHERE id=%s FOR KEY SHARE',
                                   (contact_id,))
                    if cursor.fetchone() is None:
                        cursor.execute("SELECT set_config('digues.actor_contact_id', '', true)")
                    else:
                        actor_contact_id = str(contact_id)
                cursor.execute("SELECT set_config('digues.human_mutation', 'on', true)")
            result = write()
            related = result.pop("_related_mutations", {})
            type_change = result.pop('_type_change', None) if resource == 'troncons' else None
            entity = result.get("features", [None])[0] if result.get("type") == "FeatureCollection" else result
            values = entity.get("properties", entity)
            object_id = values.get("id", entity.get("id"))
            cursor.execute(f"SELECT version FROM public.{table} WHERE id = %s", (object_id,))
            version = cursor.fetchone()[0]
            if action == "update" and version == base_version:
                # A relation-only mutation still advances its aggregate version.
                cursor.execute(f"UPDATE public.{table} SET version = version WHERE id = %s RETURNING version", (object_id,))
                version = cursor.fetchone()[0]
            values["version"] = version
            result["mutation"] = {
                "schemaVersion": 1,
                **(origin or {"originClientId": f"api-{uuid4()}", "originInstanceId": f"api-{uuid4()}"}),
                "eventId": str(uuid4()), "resource": resource, "action": action,
                "ids": [str(object_id)], "versions": {str(object_id): version},
                "occurredAt": time_ns() // 1_000_000,
            }
            events=[result["mutation"]]
            for affected,versions in sorted(related.items()):
                if affected not in RESOURCE_TABLES:raise ValueError("Unknown related mutation resource")
                if versions:
                    events.append({**result["mutation"],"eventId":str(uuid4()),"resource":affected,
                                   "action":"update","ids":sorted(versions),"versions":versions})
            if type_change is not None:
                result['mutation']['typeChange'] = {**type_change, 'actorContactId': actor_contact_id}
            # All row mutations and versions are complete before the first
            # journal lock. Every event/receipt commits or rolls back together.
            if len(events)>1:result["mutation"]["relatedMutations"]=events[1:]
            for event in events:append_event(cursor,event)
            if operation_id:
                cursor.execute(
                    "INSERT INTO technique.operations_ressources(operation_id,fingerprint,resource,resource_id,event_id,response) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (operation_id, fingerprint, resource, object_id, result["mutation"]["eventId"], Jsonb(result)),
                )
            # A caller may own an outer transaction (tests or aggregate work).
            # Never leave human attribution armed for a later technical UPDATE.
            cursor.execute("SELECT set_config('digues.human_mutation', 'off', true)")
    # The response (and its event receipt) leaves only after COMMIT succeeds.
    return result
