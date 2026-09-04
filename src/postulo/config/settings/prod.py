"""Production settings for a self-hosted instance behind HTTPS."""

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP

from .base import *
from .base import env

if not SECRET_KEY:
    raise ImproperlyConfigured(
        "POSTULO_SECRET_KEY must be set. Generate one with: "
        "python -c 'import secrets; print(secrets.token_urlsafe(64))'"
    )

DEBUG = False

SECURE_HSTS_SECONDS = env.int("POSTULO_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("POSTULO_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("POSTULO_HSTS_PRELOAD", default=False)

# HSTS preloading is close to irreversible and commits every subdomain to HTTPS,
# which is not a decision Postulo should make for an operator's domain. Opting out
# is deliberate, so the check that nags about it is silenced while it stays off.
SILENCED_SYSTEM_CHECKS = [] if SECURE_HSTS_PRELOAD else ["security.W021"]
SECURE_SSL_REDIRECT = env.bool("POSTULO_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Postulo serves no third-party scripts, fonts, or trackers. Say so, and enforce it.
SECURE_CSP = {
    "default-src": [CSP.NONE],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "base-uri": [CSP.SELF],
}

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env("POSTULO_EMAIL_HOST", default="localhost"),
            "port": env.int("POSTULO_EMAIL_PORT", default=25),
            "username": env("POSTULO_EMAIL_HOST_USER", default=""),
            "password": env("POSTULO_EMAIL_HOST_PASSWORD", default=""),
            "use_tls": env.bool("POSTULO_EMAIL_USE_TLS", default=True),
            "timeout": env.int("POSTULO_EMAIL_TIMEOUT", default=10),
        },
    },
}
