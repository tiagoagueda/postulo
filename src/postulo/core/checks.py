"""Refusing a configuration Postulo cannot run, at start-up rather than at the first send (#233).

`POSTULO_EMAIL_SECURITY`, `POSTULO_EMAIL_AUTH`, `POSTULO_PDF_BACKEND` and every
`POSTULO_*_RATE` were read raw. A typo -- `startls`, `weasy`, `30/hour` -- passed
`check --deploy` in the entrypoint and surfaced at the first message, the first export or the
first throttled request, as behaviour rather than as a message: the rate parser treats what it
cannot read as *no limit*, which is the right thing to do at request time and the wrong thing
to keep quiet about.

These are Django system checks, so they run wherever `manage.py check` runs: the entrypoint
before gunicorn starts, CI against the production settings, and a developer's terminal. Each
is an **error**, because each names a value that cannot mean anything, and the entrypoint
stops on an error. A value that is merely unusual is not this module's business.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, register

from postulo.core import throttle

#: The words each setting accepts. Read from here by the checks and by nothing else: the
#: modules that act on these settings keep their own comparisons, and this is the list an
#: operator is shown.
CHOICES: dict[str, tuple[str, ...]] = {
    "POSTULO_EMAIL_SECURITY": ("none", "starttls", "ssl"),
    "POSTULO_EMAIL_AUTH": ("password", "xoauth2"),
    "POSTULO_PDF_BACKEND": ("auto", "weasyprint", "chromium"),
    "POSTULO_LOG_FORMAT": ("simple", "json"),
}

#: Where a request has to be reachable from: the two schemes a browser will follow.
URL_SCHEMES = ("http", "https")


def rate_settings() -> list[str]:
    """Every `POSTULO_*_RATE` the settings define, whichever module added it."""
    return sorted(
        name for name in dir(settings) if name.startswith("POSTULO_") and name.endswith("_RATE")
    )


@register("postulo")
def choices(app_configs=None, **kwargs) -> list[Error]:
    """A setting with a fixed vocabulary says one of its words."""
    problems = []
    for name, allowed in CHOICES.items():
        value = getattr(settings, name, allowed[0])
        if value not in allowed:
            problems.append(
                Error(
                    f"{name} is {value!r}; it must be one of: {', '.join(allowed)}.",
                    hint="Set it in the environment, or unset it to take the default.",
                    id="postulo.E001",
                )
            )
    return problems


@register("postulo")
def rates(app_configs=None, **kwargs) -> list[Error]:
    """A rate is `<times>/<s|m|h|d>`, or empty or 0 for no limit.

    The parser accepts anything and reads the unreadable as *unlimited*, so that a mistyped
    rate never locks an instance; this is where the mistype is reported instead.
    """
    problems = []
    for name in rate_settings():
        value = getattr(settings, name)
        if not throttle.is_well_formed(value):
            problems.append(
                Error(
                    f"{name} is {value!r}; a rate is written like 30/h, 5/m or 600/d, "
                    "and empty or 0 means no limit.",
                    id="postulo.E002",
                )
            )
    return problems


@register("postulo")
def public_url(app_configs=None, **kwargs) -> list[Error]:
    """`POSTULO_PUBLIC_URL`, when set, is an address a message can carry: a scheme and a host."""
    value = getattr(settings, "POSTULO_PUBLIC_URL", "")
    if not value:
        return []
    parts = urlsplit(value)
    if parts.scheme not in URL_SCHEMES or not parts.netloc:
        return [
            Error(
                f"POSTULO_PUBLIC_URL is {value!r}; it must start with https:// or http:// "
                "and name the host this instance is reached at, such as "
                "https://postulo.example.org.",
                id="postulo.E003",
            )
        ]
    return []
