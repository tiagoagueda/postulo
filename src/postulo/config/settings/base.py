"""Settings shared by every environment.

Values that differ between a laptop and a server belong in the environment, not in
this file. See ``.env.example`` for the full set of recognised variables.
"""

from datetime import timedelta
from pathlib import Path

import environ
from django.utils.translation import gettext_lazy as _

from postulo.accounts.validators import USERNAME_BLACKLIST

# src/postulo/config/settings/base.py -> src/postulo
PACKAGE_DIR = Path(__file__).resolve().parents[2]
# ... -> the repository root
REPO_DIR = PACKAGE_DIR.parents[1]

env = environ.Env()
environ.Env.read_env(REPO_DIR / ".env")

# --------------------------------------------------------------------------- core

SECRET_KEY = env("POSTULO_SECRET_KEY", default=None)
DEBUG = env.bool("POSTULO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("POSTULO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("POSTULO_CSRF_TRUSTED_ORIGINS", default=[])

ROOT_URLCONF = "postulo.config.urls"
WSGI_APPLICATION = "postulo.config.wsgi.application"
ASGI_APPLICATION = "postulo.config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "django_htmx",
    "django_tasks_db",
    # postulo
    "postulo.core",
    "postulo.accounts",
    "postulo.jobs",
    "postulo.applications",
    "postulo.resume",
    "postulo.documents",
    "postulo.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # Last, because it needs request.user and overrides LocaleMiddleware.
    "postulo.core.middleware.UserPreferencesMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [PACKAGE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "postulo.core.context_processors.ui",
            ],
        },
    },
]

# ----------------------------------------------------------------------- database

DATABASES = {
    "default": env.db_url(
        "POSTULO_DATABASE_URL",
        default=f"sqlite:///{REPO_DIR / 'data' / 'postulo.sqlite3'}",
    ),
}
DATABASES["default"].setdefault("ATOMIC_REQUESTS", True)

# SQLite creates the database file, but not the directory holding it, so a fresh
# install fails on its very first command with "unable to open database file". The
# development settings only avoid this by chance, because writing the development
# secret key happens to create the same directory first.
if "sqlite3" in DATABASES["default"]["ENGINE"]:
    _db_path = Path(DATABASES["default"]["NAME"])
    if _db_path.name != ":memory:":
        _db_path.parent.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- auth

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Identity: a username, chosen at signup, and an email address; either signs in. Both are
# obligatory and unique, and so is a full name, which the signup form adds.
ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = ["username*", "email*", "password1*", "password2*"]
ACCOUNT_FORMS = {"signup": "postulo.accounts.forms.SignupForm"}
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_USER_MODEL_EMAIL_FIELD = "email"
ACCOUNT_PRESERVE_USERNAME_CASING = False
ACCOUNT_USERNAME_MIN_LENGTH = 3
ACCOUNT_USERNAME_VALIDATORS = "postulo.accounts.validators.username_validators"
ACCOUNT_USERNAME_BLACKLIST = USERNAME_BLACKLIST
# Every address is proven by a link before it is used: to sign in, and to be primary.
# An invitation sent to an address counts as that proof (see accounts/signals.py).
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_MAX_EMAIL_ADDRESSES = 5
ACCOUNT_ADAPTER = "postulo.accounts.adapter.AccountAdapter"

# Two-factor authentication: a code from an authenticator app, and recovery codes for the
# day the phone is gone. Opt-in per person under Settings → Account. Passkeys are the
# natural next step; they need a secure origin, which a plain-HTTP mesh instance lacks.
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "Postulo"
# A personal machine need not be asked for a code every day.
MFA_TRUST_ENABLED = True
MFA_TRUST_COOKIE_AGE = timedelta(days=30)

# Single sign-on through OpenID Connect, configured from the environment. Unset means no
# button and nothing changes for anyone. One generic provider covers Authentik, Keycloak,
# Pocket ID, Zitadel, Kanidm, Google and anything else that speaks OIDC.
POSTULO_OIDC_NAME = env("POSTULO_OIDC_NAME", default="Single sign-on")
POSTULO_OIDC_SERVER_URL = env("POSTULO_OIDC_SERVER_URL", default="")
POSTULO_OIDC_CLIENT_ID = env("POSTULO_OIDC_CLIENT_ID", default="")
POSTULO_OIDC_CLIENT_SECRET = env("POSTULO_OIDC_CLIENT_SECRET", default="")
# By default single sign-on signs in accounts that exist; the identity provider is not an
# invitation unless the operator says so.
POSTULO_OIDC_AUTO_SIGNUP = env.bool("POSTULO_OIDC_AUTO_SIGNUP", default=False)
POSTULO_OIDC_PROVIDER_ID = "oidc"

SOCIALACCOUNT_PROVIDERS = (
    {
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": POSTULO_OIDC_PROVIDER_ID,
                    "name": POSTULO_OIDC_NAME,
                    "client_id": POSTULO_OIDC_CLIENT_ID,
                    "secret": POSTULO_OIDC_CLIENT_SECRET,
                    "settings": {"server_url": POSTULO_OIDC_SERVER_URL},
                }
            ]
        }
    }
    if POSTULO_OIDC_SERVER_URL and POSTULO_OIDC_CLIENT_ID
    else {}
)
# The provider round-trip carries its own state; a plain link avoids the production CSP
# (form-action 'self') silently blocking the redirect a POST form would make.
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_OPENID_CONNECT_URL_PREFIX = "sso"
# An address the identity provider has verified links to the existing account holding it.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_AUTO_SIGNUP = True
# Postulo has no use for the provider's tokens; credentials with no purpose are a liability.
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_ADAPTER = "postulo.accounts.social_adapter.SocialAccountAdapter"
SOCIALACCOUNT_FORMS = {"signup": "postulo.accounts.forms.SocialSignupForm"}

# An instance is invite-only unless the operator opens registration deliberately.
POSTULO_REGISTRATION_OPEN = env.bool("POSTULO_REGISTRATION_OPEN", default=False)

# The admin is a small attack surface worth moving off a guessable path.
POSTULO_ADMIN_URL = env("POSTULO_ADMIN_URL", default="admin/")

# ---------------------------------------------------------- internationalisation

# British English is the source language; every other locale is a translation of it.
LANGUAGE_CODE = "en-gb"

LANGUAGES = [
    ("en-gb", _("English (United Kingdom)")),
    ("fr-fr", _("French (France)")),
    ("pt-pt", _("Portuguese (Portugal)")),
]

LOCALE_PATHS = [REPO_DIR / "locale"]

TIME_ZONE = env("POSTULO_TIME_ZONE", default="Europe/Paris")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- static & media

STATIC_URL = "static/"
STATIC_ROOT = env.path("POSTULO_STATIC_ROOT", default=REPO_DIR / "staticfiles")
STATICFILES_DIRS = [PACKAGE_DIR / "static"]

# Media holds CVs and cover letters: personal documents, never served directly.
# Every file is delivered through an ownership-checked view instead.
MEDIA_URL = "media/"
MEDIA_ROOT = env.path("POSTULO_MEDIA_ROOT", default=REPO_DIR / "data" / "media")

# Where `manage.py backup` writes when given no target. Beside the data it copies, so a
# single volume holds both; move it elsewhere if that volume is the thing being backed up.
POSTULO_BACKUP_DIR = env.path("POSTULO_BACKUP_DIR", default=REPO_DIR / "data" / "backups")

# Optional hand-off to the web server once Django has authorised a download. Leave both
# unset to have Django stream the file itself, which is correct but ties up a worker.
# nginx: an `internal` location, e.g. "/protected-media/".
POSTULO_MEDIA_ACCEL_PREFIX = env("POSTULO_MEDIA_ACCEL_PREFIX", default="")
# Apache with mod_xsendfile.
POSTULO_MEDIA_SENDFILE = env.bool("POSTULO_MEDIA_SENDFILE", default=False)

# ------------------------------------------------------------------------ pdf

# auto | weasyprint | chromium. WeasyPrint is the default renderer and ships with
# Postulo; Chromium is a fallback for machines where WeasyPrint's system libraries are
# impractical. "auto" takes whichever actually works, preferring WeasyPrint. Export is
# optional, so an unusable renderer is reported when export is attempted rather than
# preventing the application from starting.
POSTULO_PDF_BACKEND = env("POSTULO_PDF_BACKEND", default="auto")

# ---------------------------------------------------------------------- capture

# Postulo honours robots.txt when fetching a posting. A person capturing a page they
# are looking at is not a crawler, but Postulo cannot prove that to the site, so the
# polite default stands. Turning this off makes the operator responsible for the
# requests their instance makes.
POSTULO_CAPTURE_IGNORE_ROBOTS = env.bool("POSTULO_CAPTURE_IGNORE_ROBOTS", default=False)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------- tasks

TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
    },
}

# --------------------------------------------------------------------- security

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------------- email

# Django 6.1 deprecates the EMAIL_* settings in favour of MAILERS; the two may not be
# mixed, so Postulo uses MAILERS exclusively. Each environment defines its own.
DEFAULT_FROM_EMAIL = env("POSTULO_DEFAULT_FROM_EMAIL", default="postulo@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --------------------------------------------------------------------- logging

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "loggers": {
        # WeasyPrint subsets a font on every render and reports each step at INFO.
        # Exporting one CV produced 162 lines of it, which does not bury a log so much
        # as replace it. Measured on a real export, not guessed at.
        "fontTools": {"level": "WARNING"},
        "weasyprint": {"level": "WARNING"},
    },
    "root": {"handlers": ["console"], "level": env("POSTULO_LOG_LEVEL", default="INFO")},
}
