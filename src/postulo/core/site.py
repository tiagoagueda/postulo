"""What this instance is and how it behaves: policy, resolved from two places.

An operator's environment sets the infrastructure and may set policy; an administrator's
Server settings page sets policy. When both speak, the environment wins, so a `.env` that
has worked since 0.1.0 goes on meaning what it meant. The page shows such a value
read-only and says where it came from.

Everything here is a small function so that the rest of the code asks a question —
"is registration open?" — rather than reading a setting.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.contrib.auth import get_user_model

from .models import SiteSettings

#: Model field → the environment variable that, when set, overrides it.
ENV_OVERRIDES = {
    "registration_open": "POSTULO_REGISTRATION_OPEN",
    "capture_ignore_robots": "POSTULO_CAPTURE_IGNORE_ROBOTS",
    "sso_is_second_factor": "POSTULO_OIDC_IS_SECOND_FACTOR",
    "default_time_zone": "POSTULO_TIME_ZONE",
}


def overridden_by(field: str) -> str | None:
    """The environment variable pinning ``field``, if one is set."""
    variable = ENV_OVERRIDES.get(field)
    if variable and variable in os.environ:
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


def default_time_zone() -> str:
    if overridden_by("default_time_zone"):
        return settings.TIME_ZONE
    return current().default_time_zone or settings.TIME_ZONE


def default_language() -> str:
    return current().default_language or settings.LANGUAGE_CODE


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
