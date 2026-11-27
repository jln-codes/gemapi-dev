"""Profils techniques privés montés côté serveur ; aucun fallback mono-base."""

import json
import os
from pathlib import Path

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict

from .database import WebDatabaseConfigurationError


def resolve_profile(context, *, kind: str):
    if kind not in {'runtime', 'sql_readonly', 'ai_readonly'}:
        raise WebDatabaseConfigurationError('Type de profil métier invalide.')
    try:
        path = os.environ['BUSINESS_CONNECTION_PROFILES_FILE']
        with Path(path).open(encoding='utf-8') as source:
            payload = source.read(1_048_577)
        if len(payload) > 1_048_576:
            raise ValueError()
        profile = json.loads(payload)[context.connection_profile_ref]
        if profile['cluster_ref'] != context.cluster_ref or profile['database_name'] != context.database_name:
            raise ValueError()
        # Each DSN must name its target explicitly. libpq handles escaping.
        configs = {}
        for profile_kind in ('runtime', 'sql_readonly', 'ai_readonly'):
            if profile_kind not in profile:
                continue
            parsed = conninfo_to_dict(profile[profile_kind])
            if (parsed.get('dbname') != context.database_name or not parsed.get('user')
                    or not (parsed.get('host') or parsed.get('hostaddr'))
                    or any(key in parsed for key in ('service', 'options'))):
                raise ValueError()
            configs[profile_kind] = parsed
        selected = configs[kind]
        if kind != 'runtime':
            runtime = configs['runtime']
            if (selected['user'] == runtime['user'] or any(
                    selected.get(key) != runtime.get(key) for key in ('host', 'hostaddr', 'port'))):
                raise ValueError()
            other = configs.get('sql_readonly' if kind == 'ai_readonly' else 'ai_readonly')
            if other and selected['user'] == other['user']:
                raise ValueError()
        return {**selected, 'autocommit': True, 'connect_timeout': 10}
    except (KeyError, TypeError, ValueError, OSError, ProgrammingError):
        raise WebDatabaseConfigurationError('Profil de connexion métier absent ou incohérent.') from None
