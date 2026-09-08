"""Reading the production settings the way a server would, once, for the tests that need to.

`config/settings/prod.py` refuses to import without `POSTULO_SECRET_KEY`, which is correct
of it and makes it something a test cannot simply import. `tests/security/test_health_redirect.py`
did import it, at module scope, and passed on every developer's machine because a `.env` in
the repository had supplied a key — and failed **at collection** in CI, where there is no
`.env`. A collection error aborts the whole run, so one line took down `test` on three
Pythons and `browser` with it, on every push for a fortnight, while the same suite passed
locally.

So there is one way to do this and it lives here. A subprocess, with every `POSTULO_*`
variable stripped and `read_env` disabled, because the repository's `.env` belongs to
whoever is developing here and what is under test is what the module itself says. Session
-scoped: it is a Python start-up, and two tests wanting it should not cost two.

Importing it in-process would not work even with the key set. `prod.py` reads `SECRET_KEY`
from `base`, which the test settings have already imported without one, so the value is
decided long before any test could arrange otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

#: Run in a subprocess of this interpreter. Everything it prints is what production says.
PROD_PROBE = """
import json

import environ

# The repository's .env belongs to whoever is developing here. What is under test is what
# the module itself says, so the file is taken out of the picture rather than trusted to
# be absent.
environ.Env.read_env = lambda *args, **kwargs: None

from postulo.config.settings import prod

print(json.dumps({
    "DEBUG": prod.DEBUG,
    "SECURE_HSTS_SECONDS": prod.SECURE_HSTS_SECONDS,
    "SESSION_COOKIE_SECURE": prod.SESSION_COOKIE_SECURE,
    "CSRF_COOKIE_SECURE": prod.CSRF_COOKIE_SECURE,
    "SECURE_SSL_REDIRECT": prod.SECURE_SSL_REDIRECT,
    "SECURE_REDIRECT_EXEMPT": list(prod.SECURE_REDIRECT_EXEMPT),
    "SECURE_CSP": {k: [str(v) for v in vs] for k, vs in prod.SECURE_CSP.items()},
    "POSTULO_ADMIN_URL": prod.POSTULO_ADMIN_URL,
}))
"""


def read_production_settings() -> dict:
    """Import the production settings the way a server would, and report what they say."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("POSTULO_")}
    environment["POSTULO_SECRET_KEY"] = "x" * 64
    environment["POSTULO_ALLOWED_HOSTS"] = "postulo.example.org"
    finished = subprocess.run(  # noqa: S603 - this interpreter, and a script written here
        [sys.executable, "-c", PROD_PROBE],
        capture_output=True,
        text=True,
        env=environment,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


@pytest.fixture(scope="session")
def production_settings() -> dict:
    """What `config/settings/prod.py` says, read once for the whole session."""
    return read_production_settings()
