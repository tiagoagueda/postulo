"""Test settings: fast, isolated, and independent of the developer's .env."""

import os
import tempfile
from pathlib import Path

from .base import *

SECRET_KEY = "test-key-not-secret"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# In-memory SQLite everywhere, except where somebody asks for the other engine Postulo
# supports. The default is what makes the suite fast and needs nothing installed; the
# override is what stops PostgreSQL being a documented option nobody has ever run a test
# against, which is how the image came to ship without a driver or pg_dump at all (#219).
# CI sets this on a job with a real server beside it; a contributor with one can do the
# same. Deliberately its own variable rather than POSTULO_DATABASE_URL: a developer's .env
# points at their working database, and a test run must never find it.
_test_database_url = os.environ.get("POSTULO_TEST_DATABASE_URL")
if _test_database_url:
    DATABASES = {"default": env.db_url_config(_test_database_url)}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# A throwaway app providing a concrete OwnedModel to test the foundations against.
INSTALLED_APPS = [*INSTALLED_APPS, "tests.testapp"]

# The test client is not a browser and never speaks https; without this every WebAuthn
# test would be testing the origin check rather than the thing it means to test.
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = True

MEDIA_ROOT = tempfile.mkdtemp(prefix="postulo-test-media-")

# Whatever the environment says, the file handler exists and writes somewhere throwaway:
# `base.py` leaves it out when POSTULO_LOG_DIR is empty.
POSTULO_LOG_DIR = tempfile.mkdtemp(prefix="postulo-test-logs-")
LOGGING["handlers"]["file"] = {
    "class": "postulo.core.logs.SharedRotatingFileHandler",
    "filename": str(Path(POSTULO_LOG_DIR) / "postulo.log"),
    "maxBytes": POSTULO_LOG_MAX_BYTES,
    "backupCount": POSTULO_LOG_BACKUPS,
    "encoding": "utf-8",
    "formatter": "json",
    "delay": True,
}
LOGGING["root"]["handlers"] = ["console", "file"]

# What decides behaviour is pinned, because a variable exported in the shell still reaches
# `base.py`: the database cache, which lives in the test database and so is rolled back
# with each test (a process-wide cache would carry one test's rate-limit counts into the
# next), and errands done inline.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": CACHE_TABLE,
    }
}
POSTULO_BACKGROUND_WORK = False

# WhiteNoise warns about a missing static root; give it a real, empty directory.
STATIC_ROOT = tempfile.mkdtemp(prefix="postulo-test-static-")

STORAGES["staticfiles"] = {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
