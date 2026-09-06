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

# Django's own documentation warns about this one, and rightly: it makes any request
# carrying X-Forwarded-Proto: https count as secure, and that is an ordinary header
# anybody can send. It is safe here only because TrustedProxyMiddleware has already
# removed the header from every request that did not come from POSTULO_TRUSTED_PROXIES,
# so by the time SecurityMiddleware reads it, a proxy is the only thing that could have
# set it. Changing one of those two without the other undoes both.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Secure cookies are only ever sent over HTTPS, which is right for anything a browser
# reaches over the open internet. It is wrong for an instance reached only inside a mesh
# VPN such as NetBird or Tailscale, where the wire is already encrypted and the browser
# sees plain HTTP: the cookie is never sent, and nobody can sign in. Turn this off only
# in that situation, and turn SSL redirection off with it.
POSTULO_SECURE_COOKIES = env.bool("POSTULO_SECURE_COOKIES", default=True)
SESSION_COOKIE_SECURE = POSTULO_SECURE_COOKIES
CSRF_COOKIE_SECURE = POSTULO_SECURE_COOKIES

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
