"""Test settings: fast, isolated, and independent of the developer's .env."""

import tempfile

from .base import *

SECRET_KEY = "test-key-not-secret"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

MEDIA_ROOT = tempfile.mkdtemp(prefix="postulo-test-media-")

# WhiteNoise warns about a missing static root; give it a real, empty directory.
STATIC_ROOT = tempfile.mkdtemp(prefix="postulo-test-static-")

STORAGES["staticfiles"] = {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
