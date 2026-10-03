"""The database password, read on its own so no character in it needs escaping (#582).

A password pasted into ``POSTULO_DATABASE_URL`` has to survive being parsed as a URL: a
``/`` or ``?`` in it (common in ``openssl rand -base64`` output) breaks the parse and a
``%41`` is quietly decoded into a different password from the one the database was given.
So the URL can name the server without one, and the password comes from its own variable,
``POSTULO_DATABASE_PASSWORD``, or from ``POSTGRES_PASSWORD``, the one the PostgreSQL
container reads from the same ``.env`` file.
"""

from __future__ import annotations

from collections.abc import Mapping


def apply_password(database: dict, environ: Mapping[str, str]) -> dict:
    """Set the password from the environment, unless the URL carried one or this is SQLite."""
    if "sqlite3" in database.get("ENGINE", "") or database.get("PASSWORD"):
        return database
    password = environ.get("POSTULO_DATABASE_PASSWORD") or environ.get("POSTGRES_PASSWORD")
    if password:
        database["PASSWORD"] = password
    return database
