"""Optional public login hint; credentials confer no permissions by themselves."""
import os
from pathlib import Path

from digues_app.control.database import open_control_connection
from digues_app.deployment.public_demo import public_demo_target
from digues_app.provisioning.private_files import read_private


def frontend_public_demo():
    path = os.getenv('PUBLIC_DEMO_CONFIG_FILE', '')
    if not path:
        return None
    try:
        config = read_private(Path(path))
        with open_control_connection() as connection:
            return public_demo_target(connection, config)
    except Exception:
        # Bad/absent enrollment disables only this convenience, never normal login.
        # Never serialize the private file, a SQL error, or an operator profile.
        return None
