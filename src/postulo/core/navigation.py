"""What the main navigation offers, and what a person has chosen not to see there.

Every item here is reachable by another route — the wordmark, a link on a page, the
search box — so hiding one takes nothing away. It is the row across the top that runs out
of room first, on a narrow screen, and the person who never opens the board should not
have to look past it eight times a day.

*Dashboard* is the reason this exists. The wordmark at the left already goes there, so on
every page there are two controls for one destination. It stays visible by default,
because somebody seeing Postulo for the first time has no way of knowing the wordmark is
a link; hiding it is for the person who has learnt that and wants the space. When it is
hidden the wordmark takes the job over properly: it carries the active style on the
dashboard, and an accessible name that says where it goes rather than just naming the
instance.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class NavItem:
    """One item of the main navigation."""

    key: str
    label: str
    url_name: str
    #: Every URL name that should light this item up, the first being its own.
    match: tuple[str, ...] = ()

    @property
    def active_names(self) -> tuple[str, ...]:
        return (self.url_name, *self.match)


#: The main navigation, in the order it is shown. Adding an item here adds a switch to
#: Settings → Appearance without anything else changing.
ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", _("Dashboard"), "core:home"),
    NavItem(
        "listings",
        _("Listings"),
        "listings:list",
        (
            "listings:create",
            "listings:apply",
            "jobs:posting_detail",
            "jobs:posting_update",
            "jobs:capture_create",
            "jobs:capture_review",
        ),
    ),
    NavItem(
        "applications",
        _("Applications"),
        "applications:list",
        ("applications:detail", "applications:create"),
    ),
    NavItem("board", _("Board"), "applications:board"),
    NavItem("insights", _("Insights"), "applications:insights"),
    NavItem(
        "documents",
        _("Documents"),
        "documents:cv_list",
        (
            "documents:cv_detail",
            "documents:letter_list",
            "documents:letter_detail",
            "documents:upload_list",
            "resume:overview",
        ),
    ),
    NavItem("companies", _("Companies"), "jobs:company_list", ("jobs:company_detail",)),
    NavItem("reminders", _("Reminders"), "applications:reminder_list"),
)

BY_KEY = {item.key: item for item in ITEMS}

#: Keys a person may hide. All of them: everything has another way in.
HIDEABLE = tuple(item.key for item in ITEMS)


def choices() -> list[tuple[str, str]]:
    """The switches Settings → Appearance offers, in the navigation's own order."""
    return [(item.key, item.label) for item in ITEMS]


def visible_items(profile) -> list[NavItem]:
    hidden = set(getattr(profile, "hidden_nav_items", None) or [])
    return [item for item in ITEMS if item.key not in hidden]


def dashboard_hidden(profile) -> bool:
    return "dashboard" in set(getattr(profile, "hidden_nav_items", None) or [])
