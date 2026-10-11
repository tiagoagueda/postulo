"""What a page can be imported from and exported to, said once (#862).

The Calendar, Your details and Your career each offer *Import* and *Export* beside their
heading. What stands behind the two buttons is a list, and the list is a registry for the
reason `documents.formats` gives for the formats a CV can leave as: a key already taken is
refused rather than overridden, Postulo's own are registered as the app loads, and a plugin
that reads or writes one more kind of file registers it with `postulo.plugins.api` instead of
patching a template.

**A transfer is a link.** It names a place to go -- the page that reads a file, the address
that writes one -- because what happens there is the business of that page: the import
pages show what a file holds before they add anything, and an export is a plain GET. The
registry does not run either; it only says which exist, so the buttons can say it.

**One goes straight there, several ask.** A direction with a single transfer is a link; with
more it opens a popover listing them. That is decided here, from the list, and not in each
page's template.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

IMPORT = "import"
EXPORT = "export"
DIRECTIONS = (IMPORT, EXPORT)

#: The pages that have the two buttons. A plugin may name another subject, but no page draws it.
CALENDAR = "calendar"
DETAILS = "details"
CAREER = "career"


@dataclass(frozen=True)
class Transfer:
    """One way of bringing something in, or taking it out."""

    key: str
    subject: str
    direction: str
    label: object
    #: Where it goes, given the request: the calendar's download carries the period and the
    #: kinds the page is showing, so it needs to see them.
    url: Callable[[HttpRequest], str]
    #: Lower comes first. Postulo's own sit at 100, a plugin's at 200 unless it says otherwise.
    order: int = 200


REGISTRY: dict[str, Transfer] = {}


def register(offered: Transfer) -> bool:
    """Offer a transfer. Returns whether it was taken (#862)."""
    if not offered.key or offered.direction not in DIRECTIONS:
        logger.warning("A transfer with no key or an unknown direction was ignored")
        return False
    if offered.key in REGISTRY:
        logger.warning("Transfer %r was offered twice; the first one keeps it", offered.key)
        return False
    REGISTRY[offered.key] = offered
    return True


def forget(key: str = "") -> None:
    """Drop a plugin's transfer, or every transfer. For tests and for uninstalling."""
    if key:
        REGISTRY.pop(key, None)
    else:
        REGISTRY.clear()


def offered(subject: str, direction: str) -> list[Transfer]:
    """The transfers for one subject and direction, in the order they are drawn."""
    found = [t for t in REGISTRY.values() if t.subject == subject and t.direction == direction]
    return sorted(found, key=lambda t: (t.order, t.key))


def _to(name: str) -> Callable[[HttpRequest], str]:
    return lambda request: reverse(name)


def _calendar_download(request: HttpRequest) -> str:
    """The file is what the page is showing: the same period and kinds, in the address."""
    query = request.GET.urlencode()
    return reverse("applications:calendar_download") + (f"?{query}" if query else "")


def register_the_ones_postulo_has() -> None:
    """Postulo's own, described once. Idempotent, so a test may call it again."""
    described = (
        Transfer("calendar-ical-export", CALENDAR, EXPORT, _("iCalendar"), _calendar_download, 100),
        Transfer(
            "calendar-ical-import",
            CALENDAR,
            IMPORT,
            _("iCalendar"),
            _to("applications:ical_import"),
            100,
        ),
        Transfer(
            "details-record-export",
            DETAILS,
            EXPORT,
            _("Your record (JSON)"),
            _to("resume:candidate_file"),
            100,
        ),
        Transfer("details-vcard-export", DETAILS, EXPORT, _("vCard"), _to("jobs:own_vcard"), 110),
        Transfer(
            "details-record-import",
            DETAILS,
            IMPORT,
            _("Your record (JSON)"),
            _to("resume:candidate_file"),
            100,
        ),
        Transfer(
            "career-europass-import",
            CAREER,
            IMPORT,
            _("Europass"),
            _to("resume:europass_import"),
            100,
        ),
        Transfer(
            "career-record-import",
            CAREER,
            IMPORT,
            _("Your record (JSON)"),
            _to("resume:candidate_file"),
            110,
        ),
        Transfer(
            "career-record-export",
            CAREER,
            EXPORT,
            _("Your record (JSON)"),
            _to("resume:candidate_file"),
            100,
        ),
    )
    for transfer in described:
        if transfer.key not in REGISTRY:
            register(transfer)
