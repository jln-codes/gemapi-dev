"""Observation only. Bounded probes, no shell, DDL, business rows or credentials in JSON.

Slow I/O lives in a separate four-slot executor, without an unbounded queue.
The HTTP deadline does not pretend to cancel a blocked filesystem syscall;
its slot remains occupied until it really finishes. Repeated requests share
in-flight probes and a short, process-local cache, never accumulating workers.
"""

import asyncio
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import islice
import json
import os
from pathlib import Path
import platform
import re
import stat
from threading import BoundedSemaphore, Lock
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, Query
import psycopg
from psycopg.rows import dict_row
import requests

from digues_app.control.database import ControlDatabaseConfig, DDL_CHECKSUM, EXPECTED_TABLES, SCHEMA_VERSION
from digues_app.control.provisioning import JOB_TYPE, STEPS, require_control_runtime
from digues_app.media_ingestion import open_private_file
from digues_app.target.business_version import BUSINESS_SCHEMA_REVISION, BUSINESS_SCHEMA_VERSION, read_business_metadata
from .admin_frontend import require_admin_host
from .auth import require_superadmin
from .admin_storage import from_media_probe
from .connection_profiles import resolve_profile
from .database import validate_sql_role_security
from . import diagnostic_external as external_probes

STARTED = time.monotonic()
DEADLINE_SECONDS = 5
PROBE_TTL_SECONDS = 30
MEDIA_SCAN_LIMIT = 200
MEDIA_OLD_SECONDS = 86400
RUNNING_OLD_SECONDS = 3600


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def result(status, check, **values):
    return {"status": status, "check": check, **values}


class ProbeRunner:
    def __init__(self, *, workers=4, ttl=PROBE_TTL_SECONDS, cache_limit=64):
        self.ttl, self.cache_limit = ttl, cache_limit
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="admin-diagnostic")
        self.slots = BoundedSemaphore(workers)
        self.lock = Lock()
        self.pending = {}
        self.cache = OrderedDict()

    def submit(self, key, operation, *, refresh=False):
        with self.lock:
            if key in self.pending:
                return None, self.pending[key]
            cached = self.cache.get(key)
            if not refresh and cached and time.monotonic() - cached[0] < self.ttl:
                return cached[1], None
            if not self.slots.acquire(blocking=False):
                return result("unknown", "Capacité de diagnostic occupée ; réessayer ultérieurement.", code="busy"), None
            future = self.executor.submit(self._execute, operation)
            self.pending[key] = future
        # add_done_callback can execute synchronously: never hold self.lock here.
        future.add_done_callback(lambda done: self._finished(key, done))
        return None, future

    @staticmethod
    def _execute(operation):
        try:
            value = operation()
        except Exception:
            value = result("error", "Contrôle indisponible (connexion, droits ou configuration).", code="unavailable")
        return {**value, "checked_at": utcnow()}

    def _finished(self, key, future):
        with self.lock:
            self.pending.pop(key, None)
            self.cache[key] = (time.monotonic(), future.result())
            self.cache.move_to_end(key)
            while len(self.cache) > self.cache_limit:
                self.cache.popitem(last=False)
            self.slots.release()

    async def run(self, key, operation, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result("error", "Délai global de diagnostic atteint.", code="timeout")
        cached, future = self.submit(key, operation)
        if future is None:
            return cached
        try:
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), remaining)
        except TimeoutError:
            return result("error", "Délai global de diagnostic atteint.", code="timeout")


runner = ProbeRunner()
# One fixed slot per possible destination. Separate from SQL/filesystem probes;
# no queue, periodic task or additional service. Same single-flight/TTL mechanism.
external_runner = ProbeRunner(workers=len(external_probes.PROBES), ttl=external_probes.CACHE_SECONDS,
                             cache_limit=len(external_probes.PROBES))


def active_status(values):
    states = {v['status'] for v in values}
    return next((s for s in ('error', 'unknown', 'not_configured') if s in states), 'ok')


async def active_external_checks(deadline):
    async def one(name):
        value = await external_runner.run(name, lambda: external_probes.probe(name), deadline)
        if not value.get('active_probe'):
            # Capacity/deadline concerns the diagnostic runner, not a demonstrated
            # upstream failure. The occupied slot remains occupied until I/O ends.
            return result('unknown', 'Sonde non terminée ou capacité occupée ; réessayer après son achèvement.',
                          active_probe=True, checked_at=None, latency_ms=None, network_attempted=None,
                          result_cache_seconds=external_probes.CACHE_SECONDS)
        return value
    return dict(zip(external_probes.PROBES, await asyncio.gather(*(one(n) for n in external_probes.PROBES))))


@contextmanager
def technical_connection(parameters):
    # Override DSN options, even for a profile supplied by an operator. Never use
    # an installer credential, os.environ mutation or legacy DATABASE_URL fallback.
    with psycopg.connect(**{**parameters, "connect_timeout": 2, "autocommit": False,
            "options": "-c default_transaction_read_only=on -c statement_timeout=750ms "
                       "-c lock_timeout=250ms -c search_path=pg_catalog",
            "application_name": "digues-admin-diagnostics"}) as connection:
        yield connection


def control_probe(after, limit):
    config = ControlDatabaseConfig.from_env()
    with technical_connection(config.connection_parameters()) as conn:
        require_control_runtime(conn)
        actual = conn.execute("SELECT current_database()").fetchone()[0]
        if actual != config.expected_database:
            raise ValueError("Wrong control destination")
        marker = conn.execute("SELECT application,schema_version,ddl_checksum FROM public.control_schema_metadata").fetchall()
        valid = marker == [("digues-control", SCHEMA_VERSION, DDL_CHECKSUM)]
        observed = marker[0][1] if len(marker) == 1 else None
        access = conn.execute("""SELECT bool_and(coalesce(
            has_table_privilege(current_user,to_regclass('public.' || name),'SELECT'),false))
            FROM unnest(%s::text[]) AS name""", ([name for name in EXPECTED_TABLES
                if name not in {"admin_audit_log", "local_credentials"}],)).fetchone()[0]
        if not valid or not access:
            return result("error", "Marqueur de contrôle et droits SELECT des tables essentielles.",
                          expected_schema_version=SCHEMA_VERSION, observed_schema_version=observed,
                          tables_accessible=bool(access))
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT status,count(*) AS count FROM public.business_databases GROUP BY status ORDER BY status")
            states = cur.fetchall()
            structures = conn.execute("SELECT count(*) FROM public.structures").fetchone()[0]
            cur.execute("""SELECT b.id,b.display_name,b.status,b.schema_version,b.data_epoch,
                b.database_name,b.cluster_ref,b.connection_profile_ref,b.media_namespace,s.name AS structure
                FROM public.business_databases b JOIN public.structures s ON s.id=b.structure_id
                WHERE (%s::uuid IS NULL OR b.id>%s) ORDER BY b.id LIMIT %s""",
                (after, after, limit + 1))
            targets = cur.fetchall()
            cur.execute("SELECT id,status,media_namespace,structure_id,slug,display_name FROM public.business_databases ORDER BY id LIMIT 1001")
            namespaces = cur.fetchall()
        instance_id = conn.execute("SELECT instance_id FROM public.control_schema_metadata").fetchone()[0]
        # Private values stay inside the collector; only an explicit projection
        # below can be returned to HTTP. No dataclass/asdict shortcut.
        return result("ok", "Connexion, identité/version/empreinte du contrôle et droits SELECT vérifiés.",
            expected_schema_version=SCHEMA_VERSION, observed_schema_version=observed,
            tables_accessible=True, structures=structures, database_states=states,
            _targets=targets[:limit], _has_more=len(targets) > limit,
            _instance_id=str(instance_id), _namespaces=namespaces[:1000], _namespaces_complete=len(namespaces) <= 1000)


def provisioning_probe():
    config = ControlDatabaseConfig.from_env()
    with technical_connection(config.connection_parameters()) as conn:
        if conn.execute("SELECT current_database()").fetchone()[0] != config.expected_database:
            raise ValueError("Wrong control destination")
        require_control_runtime(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT state,count(*) AS count,
                count(*) FILTER (WHERE updated_at >= clock_timestamp()-interval '24 hours') AS updated_last_day,
                max(extract(epoch FROM (clock_timestamp()-created_at))) AS oldest_request_seconds,
                max(extract(epoch FROM (clock_timestamp()-updated_at))) AS oldest_update_seconds,
                max(updated_at) AS latest_update
                FROM public.admin_jobs WHERE job_type=%s GROUP BY state ORDER BY state""", (JOB_TYPE,))
            states = cur.fetchall()
            cur.execute("""SELECT id,updated_at,error_code FROM public.admin_jobs
                WHERE job_type=%s AND state='failed' ORDER BY updated_at DESC,id DESC LIMIT 1""", (JOB_TYPE,))
            last = cur.fetchone()
    if last:
        # Don't echo a free-form persisted value even if a DBA modified it.
        allowed = {f"{step}_failed" for step in STEPS}
        last["error_code"] = last["error_code"] if last["error_code"] in allowed else "provisioning_failed"
    stale = any(s["state"] in {"running", "pending"} and s["oldest_update_seconds"] > RUNNING_OLD_SECONDS for s in states)
    failed = any(s["state"] == "failed" and s["count"] for s in states)
    return result("degraded" if stale or failed else "ok", "États et dates des jobs ; aucune preuve de vivacité du worker.",
        states=states, last_failure=last, heartbeat="unknown", stale=stale, stale_after_seconds=RUNNING_OLD_SECONDS)


def business_probe(target):
    parameters = resolve_profile(SimpleNamespace(**target), kind='sql_readonly')
    with technical_connection(parameters) as conn:
        actual, readonly = conn.execute("SELECT current_database(),current_setting('transaction_read_only')").fetchone()
        if actual != target["database_name"] or readonly != "on":
            raise ValueError("Wrong business destination")
        with conn.cursor() as cursor:
            validate_sql_role_security(cursor)
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        metadata = read_business_metadata(conn)
        observed = metadata[2] if metadata else None
        consistent = (metadata is not None and str(metadata[0]) == str(target['id'])
                      and metadata[1] == BUSINESS_SCHEMA_REVISION)
        current = consistent and observed == target['schema_version'] == BUSINESS_SCHEMA_VERSION
    return result("ok" if current else "degraded",
                  "Connexion readonly, identité/version technique et taille PostgreSQL ; aucune donnée métier lue.",
                  accessible=True, size_bytes=size, observed_schema_version=observed,
                  expected_schema_version=BUSINESS_SCHEMA_VERSION,
                  schema_check='current' if current else 'outdated_or_inconsistent')


def _directory(path):
    fd = open_private_file(path)
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("Not a directory")
    return fd


def media_probe(catalogue):
    if not os.getenv("MEDIA_ROOT") or os.getenv("MEDIA_BACKEND", "filesystem") != "filesystem":
        return result("not_configured", "Stockage filesystem non configuré ou backend non pris en charge.")
    root = _directory(Path(os.environ["MEDIA_ROOT"]).absolute())
    try:
        space = os.fstatvfs(root)
        total, free = space.f_blocks * space.f_frsize, space.f_bavail * space.f_frsize
        used = (space.f_blocks - space.f_bfree) * space.f_frsize
        disk = {"filesystem_id": f"fastapi-device:{os.fstat(root).st_dev}",
                "filesystem_fsid": str(getattr(space, "f_fsid", "")),
                "reserved_bytes": total - used - free,
                "total_bytes": total, "used_bytes": used, "free_bytes": free,
                "used_percent": round(100 * used / total, 1) if total else None}
        with os.scandir(root) as entries:
            sample = list(islice(entries, MEDIA_SCAN_LIMIT + 1))
        truncated = len(sample) > MEDIA_SCAN_LIMIT
        known = {str(row["id"]): row for row in catalogue.get("_namespaces", [])}
        checked, issues = [], []
        seen = set()
        for entry in sample[:MEDIA_SCAN_LIMIT]:
            try:
                identifier = str(UUID(entry.name))
            except ValueError:
                continue  # Legacy folders are not multi-base namespaces.
            seen.add(identifier)
            record = known.get(identifier)
            if not record:
                if catalogue.get("_namespaces_complete"):
                    issues.append({"business_database_id": identifier, "code": "namespace_without_catalogue"})
                continue
            if record["status"] != "active":
                issues.append({"business_database_id": identifier, "code": "namespace_not_active"})
            try:
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                try:
                    fd = os.open("namespace.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=child)
                    with os.fdopen(fd, "rb") as source:
                        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                            raise ValueError("Invalid marker")
                        marker = json.loads(source.read(4097))
                    if marker != {"schema_version": 2, "business_database_id": identifier,
                                   "media_namespace": str(record["media_namespace"])}:
                        raise ValueError("Identity mismatch")
                finally:
                    os.close(child)
                checked.append(identifier)
            except (OSError, ValueError):
                issues.append({"business_database_id": identifier, "code": "namespace_invalid"})
        # Point checks of only this catalogue page. No recursive photo traversal.
        old = []
        for target in catalogue.get("_targets", []):
            identifier = str(target["id"])
            try:
                child = os.open(identifier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            except OSError:
                if target["status"] == "active":
                    issues.append({"business_database_id": identifier, "code": "active_namespace_missing_or_unsafe"})
                continue
            try:
                for name in ("incoming", "deleting", "trash"):
                    count = scanned = 0
                    folder = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=child)
                    try:
                        with os.scandir(folder) as entries:
                            for entry in islice(entries, MEDIA_SCAN_LIMIT + 1):
                                scanned += 1
                                if scanned <= MEDIA_SCAN_LIMIT and entry.stat(follow_symlinks=False).st_mtime < time.time() - MEDIA_OLD_SECONDS:
                                    count += 1
                    finally:
                        os.close(folder)
                    old.append({"business_database_id": identifier, "area": name, "old_entries": count,
                                "truncated": scanned > MEDIA_SCAN_LIMIT})
            except OSError:
                issues.append({"business_database_id": identifier, "code": "staging_inaccessible"})
            finally:
                os.close(child)
        incomplete = truncated or not catalogue.get("_namespaces_complete") or any(v["truncated"] for v in old)
        warning = issues or any(v["old_entries"] for v in old)
        disk["status"] = "degraded" if total and free / total < .1 else "ok"
        return result("degraded" if warning else "unknown" if incomplete else "ok",
            "Lecture seule du filesystem visible ; identités et inventaire superficiel borné, jamais de cleanup.",
            disk=disk, expected_namespaces=len(known), expected_count_complete=bool(catalogue.get("_namespaces_complete")),
            present_namespaces_in_sample=len(seen), valid_markers_in_sample=len(checked), issues=issues,
            staging=old, old_after_seconds=MEDIA_OLD_SECONDS, truncated=incomplete, scan_limit=MEDIA_SCAN_LIMIT)
    finally:
        os.close(root)


def basemap_probe():
    origin = os.getenv("BASEMAP_RASTER_URL", "").rstrip("/")
    if not origin:
        return result("not_configured", "Aucun service raster interne configuré.")
    url = urlsplit(origin)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path:
        raise ValueError("Invalid internal origin")
    # Operator-selected origin only, never a request parameter. No tile download,
    # redirect, proxy environment or remote URL discovered in TileJSON followed.
    with requests.Session() as session:
        session.trust_env = False
        with session.get(origin + "/health", timeout=(1, 1), allow_redirects=False, stream=True) as response:
            health = response.status_code == 200 and response.raw.read(64) == b"ok\n"
            rss = response.headers.get("X-Process-RSS-MiB", "")
        with session.get(origin + "/basemap/plan-ign.json", timeout=(1, 1), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError("TileJSON unavailable")
            tilejson = json.loads(response.raw.read(16385))
            valid = tilejson.get("tilejson") == "3.0.0" and isinstance(tilejson.get("tiles"), list) and bool(tilejson["tiles"])
    return result("ok" if health and valid else "error", "Health interne et TileJSON ; aucun probe IGN ni rendu de tuile.",
        health=health, tilejson=valid, process_rss_mib=int(rss) if re.fullmatch(r"\d{1,10}", rss) else None,
        cache_size_bytes=None, cache_filesystem="unknown: volume du renderer non monté dans FastAPI",
        provider_status="unknown")


def caches_probe():
    from . import weather, hydrometry
    caches = []
    # Inspect the active consumers, not vigicrues._dataset_cache (old combined
    # dataset, no longer used by current hydrometry routes). Never read payloads.
    inventories = [
        ('Prévisions de précipitations', weather._weather_condition, weather._weather_cache, 0, weather._cache_date().isoformat()),
        ('Découverte hydrométrique', hydrometry._discovery_condition, hydrometry._discovery_cache, None, None),
        ('Structure hydrométrique', hydrometry._structural_condition, hydrometry._structural_cache, None, None),
        ('Observations Hub’Eau', hydrometry._observation_condition, hydrometry._observation_cache, 1, hydrometry._cache_day().isoformat()),
        ('Prévisions Vigicrues', hydrometry._forecast_condition, hydrometry._forecast_cache, None, None),
    ]
    for name, lock, values, date_index, day in inventories:
        if not lock.acquire(timeout=.05):
            caches.append(result('unknown', 'Cache momentanément occupé.', name=name))
            continue
        try:
            count = len(values)
            sample = list(islice(values.items(), 500))
            counts = dict(Counter(entry.status for _, entry in sample))
            stale = sum(key[date_index] != day for key, _ in sample) if date_index is not None else None
        finally:
            lock.release()
        caches.append(result('degraded' if stale or counts.get('error') or counts.get('failed') else 'unknown' if not count or count > 500 else 'ok',
            'Métadonnées mémoire seulement ; aucune clé territoriale ni charge utile.', name=name,
            entries=count, sampled=len(sample), states=counts, stale_entries_in_sample=stale,
            type='process_memory', freshness='daily' if date_index is not None else 'context_identity'))
    return result('unknown' if all(c['status'] == 'unknown' for c in caches) else
                  'degraded' if any(c['status'] == 'degraded' for c in caches) else 'ok',
                  'Caches de calcul météo/hydrométrie de ce processus : réutilisation et déduplication des acquisitions. '
                  'Distincts du stockage géographique IGN/LiDAR et non agrégés entre workers.', items=caches)


def application_probe(version, build):
    rss = None
    try:
        # Linux process RSS, not host/VPS RAM. No psutil or system privileges.
        with Path("/proc/self/statm").open() as source:
            rss = int(source.read(256).split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    environment = os.getenv("DIGUES_ENVIRONMENT", "unknown")
    if environment not in {"development", "test", "production"}:
        environment = "unknown"
    revision = os.getenv("DIGUES_BUILD_REVISION", "")
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        revision = None
    return result("ok", "Processus FastAPI ayant répondu ; aucune affirmation sur le VPS.",
        version=version, frontend_build=build, revision=revision, environment=environment, python=platform.python_version(),
        uptime_seconds=round(time.monotonic() - STARTED), process_rss_bytes=rss,
        process_cpu_seconds=round(time.process_time(), 3), available_version=None)


async def collect(version, build, *, after=None, limit=5):
    deadline = time.monotonic() + DEADLINE_SECONDS
    # Independent probes start together; all share one absolute HTTP deadline.
    external_task = asyncio.create_task(active_external_checks(deadline))
    control_task = asyncio.create_task(runner.run(("control", after, limit), lambda: control_probe(after, limit), deadline))
    provision_task = asyncio.create_task(runner.run("provisioning", provisioning_probe, deadline))
    basemap_task = asyncio.create_task(runner.run("basemap", basemap_probe, deadline))
    caches_task = asyncio.create_task(runner.run("caches", caches_probe, deadline))
    control = await control_task
    targets = control.get("_targets", [])
    media_task = asyncio.create_task(runner.run(("media", after, limit), lambda: media_probe(control), deadline))
    business_slots = asyncio.Semaphore(2)
    async def one(target):
        public = {"business_database_id": target["id"], "display_name": target["display_name"],
                  "structure": target["structure"], "catalogue_status": target["status"],
                  "catalogue_schema_version": target["schema_version"],
                  "expected_schema_version": BUSINESS_SCHEMA_VERSION, "data_epoch": target["data_epoch"],
                  }
        async with business_slots:
            if target["status"] == "active":
                probe = await runner.run(("business", target["id"], target["schema_version"], target["data_epoch"]),
                                         lambda: business_probe(target), deadline)
            else:
                probe = result("unknown", "Base non active : connexion non tentée.")
        return {**public, **probe}
    databases = await asyncio.gather(*(one(target) for target in targets))
    media, provisioning, basemap, caches = await asyncio.gather(media_task, provision_task, basemap_task, caches_task)
    storage = from_media_probe(media)
    if os.getenv('STORAGE_HOST_SNAPSHOT_FILE'):
        from .host_storage_snapshot import read_snapshot
        observed = await runner.run(('host-storage', control.get('_instance_id'),
            os.getenv('STORAGE_HOST_SNAPSHOT_FILE'), os.getenv('STORAGE_HOST_FILESYSTEM_ID'),
            media.get('disk', {}).get('filesystem_fsid'), after, limit),
            lambda: {'status': 'ok', 'value': read_snapshot(storage, control, media.get('disk', {}))}, deadline)
        storage = observed.get('value', {**storage, 'snapshot_state': 'unavailable'})
    probes = await external_task
    external = {key: probes[key] for key in ('arome', 'arpege', 'hubeau', 'topage', 'vigicrues')}
    tls = {key: probes[name] for key, name in [('business_origin', 'https_business'), ('admin_origin', 'https_admin')]}
    return {
        "contract_version": 1, "generated_at": utcnow(), "deadline_seconds": DEADLINE_SECONDS,
        "probe_cache_seconds": PROBE_TTL_SECONDS, "external_probe_cache_seconds": external_probes.CACHE_SECONDS,
        "application": application_probe(version, build),
        "control_plane": {k: v for k, v in control.items() if not k.startswith("_")},
        "business_databases": result("unknown" if not databases else "degraded" if any(b["status"] != "ok" for b in databases) else "ok",
            "Page bornée de contrôles techniques, sans accès aux objets métier.", items=databases, limit=limit,
            next_after=str(targets[-1]["id"]) if targets and control.get("_has_more") else None),
        "provisioning": provisioning, "media": {k: v for k, v in media.items() if k != "disk"},
        "basemap": basemap, "caches": caches,
        "external_services": result(active_status(external.values()), "Sondes actives de métadonnées depuis le serveur, sans acquisition métier.", items=external),
        "storage": storage,
        "backups": result("not_configured", "Aucun mécanisme qualifié de sauvegarde/restauration raccordé."),
        "https": result(active_status(tls.values()), "Origines métier et administration : handshake TLS et expiration des certificats publics.", active_probe=True, items=tls),
    }


def register_routes(app, version, build):
    @app.get("/api/admin/diagnostics", tags=["administration"], dependencies=[Depends(require_admin_host)])
    async def diagnostics(after: UUID | None = None, limit: int = Query(5, ge=1, le=5),
                          _user=Depends(require_superadmin)):
        return await collect(version, build, after=after, limit=limit)
