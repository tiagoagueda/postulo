"""What this instance is and how it behaves: policy, resolved from two places.

An operator's environment sets the infrastructure and may set policy; an administrator's
Server settings page sets policy. When both speak, the environment wins, so a `.env` that
has worked since 0.1.0 goes on meaning what it meant. The page shows such a value
read-only and says where it came from.

Everything here is a small function so that the rest of the code asks a question —
"is registration open?" — rather than reading a setting.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.contrib.auth import get_user_model

from .models import SiteSettings

logger = logging.getLogger(__name__)

#: Model field → the environment variable that, when set, overrides it.
ENV_OVERRIDES = {
    "registration_open": "POSTULO_REGISTRATION_OPEN",
    "capture_ignore_robots": "POSTULO_CAPTURE_IGNORE_ROBOTS",
    "sso_is_second_factor": "POSTULO_OIDC_IS_SECOND_FACTOR",
    "default_time_zone": "POSTULO_TIME_ZONE",
    "email_host": "POSTULO_EMAIL_HOST",
    "email_port": "POSTULO_EMAIL_PORT",
    "email_username": "POSTULO_EMAIL_HOST_USER",
    "email_password": "POSTULO_EMAIL_HOST_PASSWORD",
    # Two variables for one field: the new one, and the boolean it replaces, which every
    # existing `.env` sets and which is still honoured (#158). The first one present wins.
    "email_security": ("POSTULO_EMAIL_SECURITY", "POSTULO_EMAIL_USE_TLS"),
    "email_timeout": "POSTULO_EMAIL_TIMEOUT",
    "email_from": "POSTULO_DEFAULT_FROM_EMAIL",
    "email_auth": "POSTULO_EMAIL_AUTH",
    "email_oauth_provider": "POSTULO_EMAIL_OAUTH_PROVIDER",
    "email_oauth_grant": "POSTULO_EMAIL_OAUTH_GRANT",
    "email_oauth_tenant": "POSTULO_EMAIL_OAUTH_TENANT",
    "email_oauth_client_id": "POSTULO_EMAIL_OAUTH_CLIENT_ID",
    "email_oauth_client_secret": "POSTULO_EMAIL_OAUTH_CLIENT_SECRET",
}

#: Email field on the policy row → the key the SMTP backend wants. One mapping, so the
#: form, the resolution and the shadowing check cannot drift apart, and in the order the
#: page shows them.
EMAIL_FIELDS = {
    "email_host": "host",
    "email_port": "port",
    "email_username": "username",
    "email_password": "password",
    "email_security": "security",
    "email_timeout": "timeout",
    "email_from": "from_address",
    # How the session proves who it is (#151). In the same mapping as the rest so the form,
    # the resolution and the shadowing warning cannot disagree about them either.
    "email_auth": "auth",
    "email_oauth_provider": "oauth_provider",
    "email_oauth_grant": "oauth_grant",
    "email_oauth_tenant": "oauth_tenant",
    "email_oauth_client_id": "oauth_client_id",
    "email_oauth_client_secret": "oauth_client_secret",
}

#: The fields that are secrets: never rendered, blank meaning "keep what is stored".
EMAIL_SECRET_FIELDS = ("email_password", "email_oauth_client_secret")


def env_variables() -> tuple[str, ...]:
    """Every variable that can pin a setting, flattened.

    A field may name more than one, so anything that wants the whole set — a test clearing
    the developer's `.env`, a page listing what the environment controls — asks for it here
    rather than iterating the mapping and meeting a tuple where it expected a name.
    """
    flat: list[str] = []
    for names in ENV_OVERRIDES.values():
        flat.extend((names,) if isinstance(names, str) else names)
    return tuple(flat)


def overridden_by(field: str) -> str | None:
    """The environment variable pinning ``field``, if one is set.

    A field may name more than one, because a variable that could not express a new state
    is kept working rather than retired under the instances that set it. The first one
    present answers, so the newer name wins where both are given.
    """
    names = ENV_OVERRIDES.get(field) or ()
    if isinstance(names, str):
        names = (names,)
    for variable in names:
        if variable in os.environ:
            return variable
    return None


def current() -> SiteSettings:
    """The policy row, or the defaults when nobody has saved one. Never writes."""
    return SiteSettings.objects.filter(pk=1).first() or SiteSettings()


def registration_open() -> bool:
    if overridden_by("registration_open"):
        return bool(settings.POSTULO_REGISTRATION_OPEN)
    stored = current().registration_open
    return bool(settings.POSTULO_REGISTRATION_OPEN) if stored is None else stored


def capture_ignore_robots() -> bool:
    if overridden_by("capture_ignore_robots"):
        return bool(settings.POSTULO_CAPTURE_IGNORE_ROBOTS)
    stored = current().capture_ignore_robots
    return bool(settings.POSTULO_CAPTURE_IGNORE_ROBOTS) if stored is None else stored


def sso_is_second_factor() -> bool:
    """Whether arriving through the identity provider is enough on its own.

    Off unless an operator says otherwise, because Postulo cannot see how the provider
    authenticated anybody. Turning it on is trusting the provider's own checking in place
    of a code, which is a reasonable thing to do about a provider you run and a poor thing
    to do about one you do not.
    """
    if overridden_by("sso_is_second_factor"):
        return bool(settings.POSTULO_OIDC_IS_SECOND_FACTOR)
    stored = current().sso_is_second_factor
    return bool(settings.POSTULO_OIDC_IS_SECOND_FACTOR) if stored is None else stored


def email_sign_in() -> bool:
    """Whether this instance offers signing in with a code sent by email.

    Two conditions, and the second is the one #152 exists for: an administrator has said yes,
    **and** mail is actually getting through. Offering it on an instance whose relay is broken
    is a sign-in page promising something it cannot do, to somebody who may have no other way
    in — which is the worst moment to be optimistic.
    """
    if not current().email_sign_in:
        return False
    from postulo.notifications import transport

    return transport.selected() is not None and mail_delivers()


def default_time_zone() -> str:
    if overridden_by("default_time_zone"):
        return settings.TIME_ZONE
    return current().default_time_zone or settings.TIME_ZONE


def default_language() -> str:
    return current().default_language or settings.LANGUAGE_CODE


def offered_languages() -> list[str]:
    """The codes this instance offers, or an empty list meaning every one it speaks.

    A stored code that Postulo no longer has a catalogue for is passed over rather than
    breaking the picker, the same way a widget key that no longer exists is.
    """
    from . import languages

    stored = current().offered_languages or []
    return [code for code in stored if code in languages.NATIVE_NAMES]


def offers(code: str) -> bool:
    """Whether this instance offers a language. Nothing stored means it offers them all."""
    chosen = offered_languages()
    return not chosen or code in chosen


def mail_delivers() -> bool:
    """Whether mail has been getting through lately.

    Read on every page that shows the recovery interlock, so it answers from what was
    recorded rather than by opening a connection. Anything unexpected answers `True`: a
    broken settings row must not be able to unlock the thing protecting people's accounts.
    """
    try:
        return current().mail_is_delivering
    except Exception:  # pragma: no cover - fails towards the lock staying shut
        return True


def mail_health() -> dict:
    """What the Email page shows about the last send: when, and what went wrong."""
    row = current()
    return {
        "delivers": row.mail_is_delivering,
        "last_ok_at": row.mail_last_ok_at,
        "last_error_at": row.mail_last_error_at,
        "last_error": row.mail_last_error,
        "failures": row.mail_failures or 0,
        "ever_tried": bool(row.mail_last_ok_at or row.mail_last_error_at),
    }


def record_mail(ok: bool, message: str = "") -> None:
    """Remember how a send went, from wherever mail is actually sent.

    Swallows everything. This is bookkeeping on the way past; a database that will not take
    the note must not turn a delivered message into a failed one.
    """
    try:
        SiteSettings.get().record_mail(ok, message)
    except Exception:  # pragma: no cover - bookkeeping, never the point of the call
        logger.exception("Could not record how the last send went")


def instance_name() -> str:
    return current().instance_name or "Postulo"


def tagline() -> str:
    return current().tagline


def is_empty() -> bool:
    """No accounts at all: the state of a fresh installation."""
    return not get_user_model().objects.exists()


def signup_open_now() -> bool:
    """Whether the sign-up form is offered right now.

    Open when the operator or an administrator opened it — and on an empty instance,
    because somebody has to become the first account, and the person who just installed
    Postulo is the only one who can reach it.
    """
    return registration_open() or is_empty()


# ------------------------------------------------------------------------------- email


def _stored_email(row: SiteSettings, field: str):
    """What the policy row holds for one email field, or ``None`` for "nothing"."""
    if field == "email_password":
        return row.email_password or None
    if field == "email_oauth_client_secret":
        return row.email_oauth_secrets.get("client_secret") or None
    value = getattr(row, field)
    return None if value in ("", None) else value


def email_settings() -> dict:
    """The SMTP settings actually in force, whatever they came from.

    Read for every message rather than at import, because an administrator changing the
    server on the Email page has to take effect without a restart. Falls back to the
    environment if the row cannot be read at all: mail is the channel you need most when
    something else has broken, and an error notification that fails because the database
    is down is the one you most wanted.
    """
    fallback = {
        "host": settings.POSTULO_EMAIL_HOST,
        "port": settings.POSTULO_EMAIL_PORT,
        "username": settings.POSTULO_EMAIL_HOST_USER,
        "password": settings.POSTULO_EMAIL_HOST_PASSWORD,
        "security": settings.POSTULO_EMAIL_SECURITY,
        "timeout": settings.POSTULO_EMAIL_TIMEOUT,
        "from_address": settings.DEFAULT_FROM_EMAIL,
        "auth": settings.POSTULO_EMAIL_AUTH,
        "oauth_provider": settings.POSTULO_EMAIL_OAUTH_PROVIDER,
        "oauth_grant": settings.POSTULO_EMAIL_OAUTH_GRANT,
        "oauth_tenant": settings.POSTULO_EMAIL_OAUTH_TENANT,
        "oauth_client_id": settings.POSTULO_EMAIL_OAUTH_CLIENT_ID,
        "oauth_client_secret": settings.POSTULO_EMAIL_OAUTH_CLIENT_SECRET,
    }
    try:
        row = current()
    except Exception:
        return fallback

    resolved = {}
    for field, key in EMAIL_FIELDS.items():
        stored = None if overridden_by(field) else _stored_email_quietly(row, field)
        resolved[key] = fallback[key] if stored is None else stored
    return resolved


def email_shadowed() -> tuple[str, ...]:
    """Email fields where a value is stored *and* the environment is pinning it.

    Worth saying out loud on the page: remove the variable and the stored value takes over
    silently, which means mail starts going somewhere else without anybody editing
    anything.
    """
    row = current()
    return tuple(
        field
        for field in EMAIL_FIELDS
        if overridden_by(field) and _stored_email_quietly(row, field) is not None
    )


def _stored_email_quietly(row: SiteSettings, field: str):
    """`_stored_email`, treating a password nobody can decrypt as one nobody stored.

    That happens when ``SECRET_KEY`` was rotated without ``POSTULO_FIELD_KEY``. Falling
    back to the environment beats refusing to send anything at all.
    """
    try:
        return _stored_email(row, field)
    except Exception:
        return None
