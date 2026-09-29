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

#: What a capture may keep of its page, in bytes (#256). Each is compared with a
#: `Content-Length` before a byte is read, so each has to be a number that comparison can be
#: made against -- and one that leaves room for something. A cap of nothing would be a
#: second, quieter way of switching keeping off, and the switch is where that is said.
BYTE_CAPS = (
    "POSTULO_CAPTURE_SOURCE_MAX_BYTES",
    "POSTULO_CAPTURE_RENDERING_MAX_BYTES",
    "POSTULO_CAPTURE_ACCOUNT_MAX_BYTES",
)

#: Settings that count days, where nought means *for ever* and less than nought means
#: nothing at all.
DAY_COUNTS = ("POSTULO_CAPTURE_PAGE_KEEP_DAYS",)


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


def _whole_number(value) -> bool:
    """An integer, and not the boolean Python would also count as one."""
    return isinstance(value, int) and not isinstance(value, bool)


@register("postulo")
def sizes(app_configs=None, **kwargs) -> list[Error]:
    """A cap is a whole number of bytes, one or more; a count of days is nought or more.

    These guard what a capture keeps of its page (#256). The cap is what an upload is
    refused against before it is read, so a cap that is not a number is a comparison that
    raises on the first upload, and a negative one refuses everything while the settings
    page goes on saying that pages are kept.
    """
    problems = []
    for name in BYTE_CAPS:
        value = getattr(settings, name, 1)
        if not _whole_number(value) or value < 1:
            problems.append(
                Error(
                    f"{name} is {value!r}; it must be a whole number of bytes, 1 or more.",
                    hint=(
                        "To keep nothing, switch keeping off with POSTULO_CAPTURE_KEEP_SOURCE "
                        "and POSTULO_CAPTURE_KEEP_RENDERING rather than with a cap."
                    ),
                    id="postulo.E004",
                )
            )
    for name in DAY_COUNTS:
        value = getattr(settings, name, 0)
        if not _whole_number(value) or value < 0:
            problems.append(
                Error(
                    f"{name} is {value!r}; it must be a whole number of days, and 0 means "
                    "they are kept for as long as the capture is.",
                    id="postulo.E005",
                )
            )
    return problems


def _followable(value) -> bool:
    """A scheme a browser follows and a host to follow it to."""
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme in URL_SCHEMES and bool(parts.netloc)


@register("postulo")
def public_url(app_configs=None, **kwargs) -> list[Error]:
    """`POSTULO_PUBLIC_URL`, when set, is an address a message can carry: a scheme and a host."""
    value = getattr(settings, "POSTULO_PUBLIC_URL", "")
    if not value:
        return []
    if not _followable(value):
        return [
            Error(
                f"POSTULO_PUBLIC_URL is {value!r}; it must start with https:// or http:// "
                "and name the host this instance is reached at, such as "
                "https://postulo.example.org.",
                id="postulo.E003",
            )
        ]
    return []


@register("postulo")
def footer_links(app_configs=None, **kwargs) -> list[Error]:
    """The two addresses the foot of every page links to, when they are the operator's (#212).

    `POSTULO_SOURCE_URL` always has a value -- the upstream repository unless the operator
    names their own -- because it is the offer of source the AGPL asks of a modified Postulo
    run as a service, and an offer pointing nowhere is not one. So an empty value is refused
    as well as a malformed one: unsetting it is how the default is taken.
    `POSTULO_LEGAL_NOTICE_URL` is optional, and absent means no link at all.
    """
    problems = []
    source = getattr(settings, "POSTULO_SOURCE_URL", "")
    if not _followable(source):
        problems.append(
            Error(
                f"POSTULO_SOURCE_URL is {source!r}; it must start with https:// or http:// "
                "and name where this instance's code can be had, such as "
                "https://git.example.org/you/postulo.",
                hint="Unset it to point at the upstream repository, for an instance that "
                "changed nothing.",
                id="postulo.E006",
            )
        )
    notice = getattr(settings, "POSTULO_LEGAL_NOTICE_URL", "")
    if notice and not _followable(notice):
        problems.append(
            Error(
                f"POSTULO_LEGAL_NOTICE_URL is {notice!r}; it must start with https:// or "
                "http:// and name the page with your legal notice, such as "
                "https://example.org/imprint.",
                hint="Unset it to show no legal notice.",
                id="postulo.E007",
            )
        )
    return problems
