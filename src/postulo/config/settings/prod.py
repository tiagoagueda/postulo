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

# That redirect answers before any view does, which is right for a browser and wrong for
# the endpoints a machine talks to over plain HTTP *inside* the deployment.
#
# The liveness probe is the case that mattered. `docker/Dockerfile` runs
# `curl -fsS http://127.0.0.1:8000/healthz`, and 127.0.0.1 has no TLS to be redirected to.
# Without this exemption that request was answered with a 301 -- and `curl -f` fails only
# on 4xx and 5xx, so it exited 0 with an empty body and Docker marked the container
# healthy. It would have done so with the database gone, the migrations unapplied and
# every view raising: the 503 the `healthz` view returns was unreachable, and so was the
# restart a failing check would have caused. A probe that always passes looks exactly like
# a healthy service, which is why it survived a release (#82).
#
# `/metrics` is exempt for the same reason and one more: a scraper reaching the container
# directly is on that same plain-HTTP hop, and what it collects is counts of records that
# carry nothing about anybody. A scrape arriving through the proxy is already secure and
# never reaches this test at all.
#
# `/logs` is deliberately **not** exempt. Its entries name connections, companies and
# applications, and a scrape that visibly breaks is better than personal data crossing a
# network in clear. An operator who wants it scrapes through the proxy over HTTPS, or
# turns the redirect off as a decision.
#
# Anchored at both ends. `SecurityMiddleware` matches with `re.search` against the path
# with its leading slash stripped, so an unanchored pattern would exempt every path that
# merely contains the word -- which would be a worse bug than the one being fixed.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^metrics$"]

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
