"""Test settings: fast, isolated, and independent of the developer's .env."""

import tempfile
from pathlib import Path

from .base import *

SECRET_KEY = "test-key-not-secret"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# A throwaway app providing a concrete OwnedModel to test the foundations against.
INSTALLED_APPS = [*INSTALLED_APPS, "tests.testapp"]

# The test client is not a browser and never speaks https; without this every WebAuthn
# test would be testing the origin check rather than the thing it means to test.
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = True

MEDIA_ROOT = tempfile.mkdtemp(prefix="postulo-test-media-")

POSTULO_LOG_DIR = tempfile.mkdtemp(prefix="postulo-test-logs-")
LOGGING["handlers"]["file"]["filename"] = str(Path(POSTULO_LOG_DIR) / "postulo.log")

# WhiteNoise warns about a missing static root; give it a real, empty directory.
STATIC_ROOT = tempfile.mkdtemp(prefix="postulo-test-static-")

STORAGES["staticfiles"] = {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
