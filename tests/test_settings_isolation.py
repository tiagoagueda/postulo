"""The test settings are built from the process environment, never from a `.env` (#414).

`base.py` used to read the repository's `.env` into the environment for every settings
module, so a developer's file changed the cache, the admin URL, the metrics switch and
background work of the suite. Each case runs in a subprocess because the settings are
decided at import, long before a test could arrange anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent(
    """
    import json, os, sys
    import environ

    # Stands in for the developer's `.env`: whoever reads it gets these variables.
    reads = []

    def fake_read_env(*args, **kwargs):
        reads.append(str(args))
        os.environ.setdefault("POSTULO_ADMIN_URL", "back-office")
        os.environ.setdefault("POSTULO_METRICS_ENABLED", "true")

    environ.Env.read_env = fake_read_env

    from postulo.config.settings import test

    print(json.dumps({
        "reads": reads,
        "admin_url": test.POSTULO_ADMIN_URL,
        "metrics": test.POSTULO_METRICS_ENABLED,
        "cache": test.CACHES["default"]["BACKEND"],
        "background": test.POSTULO_BACKGROUND_WORK,
        "file_handler": "file" in test.LOGGING["handlers"],
        "root_handlers": test.LOGGING["root"]["handlers"],
    }))
    """
)


def _load_test_settings(**extra: str) -> dict:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("POSTULO_")
    }
    environment["DJANGO_SETTINGS_MODULE"] = "postulo.config.settings.test"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_DIR / "src"), str(REPO_DIR), environment.get("PYTHONPATH", "")]
    )
    environment.update(extra)
    done = subprocess.run(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        env=environment,
        cwd=REPO_DIR,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_dotenv_file_is_never_read_for_the_test_settings():
    loaded = _load_test_settings()

    assert loaded["reads"] == []
    assert loaded["admin_url"] == ""
    assert loaded["metrics"] is False


def test_the_environment_cannot_change_what_decides_behaviour():
    loaded = _load_test_settings(
        POSTULO_LOG_DIR="",
        POSTULO_CACHE_URL="redis://localhost:6379/1",
        POSTULO_BACKGROUND_WORK="true",
    )

    assert loaded["cache"] == "django.core.cache.backends.db.DatabaseCache"
    assert loaded["background"] is False
    assert loaded["file_handler"] is True
    assert "file" in loaded["root_handlers"]
