"""Durable business events. No territory/environmental events belong here."""
import asyncio
import json
import time
from uuid import uuid4

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.types.json import Jsonb

from .database import open_read_connection, BusinessMaintenanceError, WebDatabaseError


# Held until COMMIT: allocating a sequence alone is NOT commit ordering.
JOURNAL_LOCK = 724193507
MAX_CURSOR = 9223372036854775807
HEADERS = {"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"}


def _origin_header(request: Request, name: str) -> str | None:
    """Lit un en-tête d'origine canonique sans ambiguïté sur les répétitions."""

    values = request.headers.getlist(name)
    if not values:
        return None
    if any(not value.strip() or len(value) > 200 or value != value.strip() for value in values):
        raise HTTPException(422, "Identité d'origine invalide.")
    if len(set(values)) != 1:
        raise HTTPException(422, "En-têtes d'origine contradictoires.")
    return values[0]


def mutation_origin(request: Request):
    result = {}
    for header, field in (
        ("X-Digues-App-Client-Id", "originClientId"),
        ("X-Digues-App-Instance-Id", "originInstanceId"),
    ):
        value = _origin_header(request, header)
        # Non-browser API callers have no browser identity. These are diagnostic,
        # untrusted identifiers, never an authentication or authorization decision.
        result[field] = value or f"api-{uuid4()}"
    return result


def append_event(cursor, event):
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (JOURNAL_LOCK,))
    cursor.execute(
        "INSERT INTO technique.evenements_ressources(event_id, resource, payload) "
        "VALUES (%s, %s, %s)",
        (event["eventId"], event["resource"], Jsonb(event)),
    )


def parse_cursor(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 19:
        raise HTTPException(422, "Curseur invalide.")
    number = int(value)
    if number > MAX_CURSOR:
        raise HTTPException(422, "Curseur invalide.")
    return number


def read_head(*, connection_factory=None):
    with (connection_factory or open_read_connection)() as connection:
        value = connection.execute("SELECT COALESCE(MAX(sequence_id), 0) FROM technique.evenements_ressources").fetchone()[0]
    return {"cursor": str(value)}


def read_page(after, limit=100, *, connection_factory=None):
    with (connection_factory or open_read_connection)() as connection:
        rows = connection.execute(
            "SELECT sequence_id, payload FROM technique.evenements_ressources "
            "WHERE sequence_id > %s ORDER BY sequence_id LIMIT %s", (after, limit + 1),
        ).fetchall()
    entries = [{"cursor": str(seq), "event": payload} for seq, payload in rows[:limit]]
    return {"events": entries, "nextCursor": entries[-1]["cursor"] if entries else str(after),
            "hasMore": len(rows) > limit}


async def stream_events(request, after, *, lifetime=60, poll_interval=2, connection_factory=None):
    # Short read connections only; no idle SQL transaction or connection per tab.
    deadline = time.monotonic() + lifetime
    yield ": connected\n\n"
    while time.monotonic() < deadline and not await request.is_disconnected():
        try:
            kwargs = {'connection_factory': connection_factory} if connection_factory else {}
            page = await asyncio.to_thread(read_page, after, **kwargs)
        except BusinessMaintenanceError:
            yield 'event: maintenance\ndata: ' + json.dumps({
                'code': BusinessMaintenanceError.code,
                'message': BusinessMaintenanceError.message,
            }, ensure_ascii=False) + '\n\n'
            return
        except WebDatabaseError:
            # A lost database is not maintenance. Headers are already sent;
            # close cleanly and let the bounded client reconnect policy apply.
            return
        except HTTPException:
            # Already sent HTTP headers: close, do not leak errors as events.
            return
        for entry in page["events"]:
            yield (f"id: {entry['cursor']}\nevent: resource-mutated\n"
                   f"data: {json.dumps(entry['event'], separators=(',', ':'))}\n\n")
            after = int(entry["cursor"])
        if not page["hasMore"]:
            yield ": heartbeat\n\n"
            await asyncio.sleep(poll_interval)
    # Periodic rotation also bounds container requests across deployments/sleep.


def register_routes(application):
    @application.exception_handler(BusinessMaintenanceError)
    async def maintenance_error(_request, _exc):
        return JSONResponse(status_code=503, content={
            'detail': BusinessMaintenanceError.message,
            'code': BusinessMaintenanceError.code,
        }, headers={'Cache-Control': 'no-store'})

    @application.get("/api/resource-events/head")
    def journal_head():
        return JSONResponse(read_head(), headers=HEADERS)

    @application.get("/api/resource-events")
    def journal_page(after: str = "0", limit: int = Query(default=100, ge=1, le=500)):
        return JSONResponse(read_page(parse_cursor(after), limit), headers=HEADERS)

    @application.get("/api/resource-events/stream")
    async def journal_stream(request: Request, after: str = "0",
                             last_event_id: str | None = Header(default=None)):
        cursor = parse_cursor(last_event_id if last_event_id is not None else after)
        await asyncio.to_thread(read_head)  # Refuse before streaming headers.
        return StreamingResponse(stream_events(request, cursor), media_type="text/event-stream", headers=HEADERS)
