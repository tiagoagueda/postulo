"""Development settings: convenient, noisy, and never for a public host."""

from django.core.management.utils import get_random_secret_key

from .base import *
from .base import REPO_DIR, env

DEBUG = env.bool("POSTULO_DEBUG", default=True)

# Generate a key on first run and keep it, so sessions survive a restart.
if not env("POSTULO_SECRET_KEY", default=None):
    _key_file = REPO_DIR / "data" / ".dev-secret-key"
    if not _key_file.exists():
        _key_file.parent.mkdir(parents=True, exist_ok=True)
        _key_file.write_text(get_random_secret_key(), encoding="utf-8")
    SECRET_KEY = _key_file.read_text(encoding="utf-8").strip()

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}

# Manifest hashing needs collectstatic to have run; unhelpful while developing.
STORAGES["staticfiles"] = {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
