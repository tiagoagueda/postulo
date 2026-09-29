"""What the main navigation offers, in what order, and what a person has chosen not to see.

Every item here is reachable by another route — the wordmark, a link on a page, the
search box — so hiding one takes nothing away. It is the row across the top that runs out
of room first, on a narrow screen, and the person who never opens the board should not
have to look past it eight times a day.

*Dashboard* is the reason hiding exists. The wordmark at the left already goes there, so
on every page there are two controls for one destination. It stays visible by default,
because somebody seeing Postulo for the first time has no way of knowing the wordmark is
a link; hiding it is for the person who has learnt that and wants the space. When it is
hidden the wordmark takes the job over properly: it carries the active style on the
dashboard, and an accessible name that says where it goes rather than just naming the
instance.

**What is stored is what was decided, never what was offered** (#299). Two lists on the
profile, and an item in neither is drawn anyway:

* ``hidden_nav_items`` holds what the person switched *off*, rather than what is on, so
  that an item added in a later release appears for everybody who has not decided about
  it — which is the behaviour a person expects of an upgrade.
* ``nav_order`` holds the keys the person has *placed*, in their order. The items in
  neither list are drawn after the placed ones, in the order below, so the same upgrade
  that adds an item puts it at the end of the row of somebody who arranged theirs and in
  its own place for everybody else. An order that is the default one is stored as nothing
  at all, so a later release that changes the default reaches everybody who never chose.

The order decides more than the sequence. The row and the phone's bar hold only so many
items (the stylesheet says how many at each width), and they hold the *first* ones: the
person who puts *Applications* first is also choosing what stays in reach on a phone.
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
    #: The Lucide icon beside the label in the row, and above it in the phone's bar. The
    #: same one the + menu draws for the same thing, where there is one (#282).
    icon: str = ""

    @property
    def active_names(self) -> tuple[str, ...]:
        return (self.url_name, *self.match)


#: The main navigation, in the order it is shown to somebody who has not arranged it.
#: Adding an item here adds a switch and a place to Settings → Appearance without anything
#: else changing.
ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", _("Dashboard"), "core:home", icon="layout-dashboard"),
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
        icon="briefcase",
    ),
    NavItem(
        "applications",
        _("Applications"),
        "applications:list",
        ("applications:detail", "applications:create"),
        icon="send",
    ),
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
        icon="file-text",
    ),
    NavItem(
        "companies",
        _("Companies"),
        "jobs:company_list",
        ("jobs:company_detail",),
        icon="building-2",
    ),
    NavItem("reminders", _("Reminders"), "applications:reminder_list", icon="bell"),
    NavItem("calendar", _("Calendar"), "applications:calendar", icon="calendar"),
)

BY_KEY = {item.key: item for item in ITEMS}

#: Keys a person may hide. All of them: everything has another way in.
HIDEABLE = tuple(item.key for item in ITEMS)

#: The order of somebody who never arranged anything.
DEFAULT_ORDER = tuple(item.key for item in ITEMS)

#: The two directions an item moves in, one place at a time. A list has no others.
DIRECTIONS = ("up", "down")


def choices() -> list[tuple[str, str]]:
    """The switches Settings → Appearance offers, in the navigation's own order."""
    return [(item.key, item.label) for item in ITEMS]


def known_keys(value) -> list[str]:
    """A stored list of keys, as far as it can be believed: known keys, each once, in order.

    What is stored arrives from a form and from an archive somebody restored, and neither
    is obliged to hold a list of strings. A key that no longer exists -- an item dropped in
    an upgrade -- is passed over rather than breaking every page.
    """
    if not isinstance(value, list | tuple):
        return []
    keys: list[str] = []
    for key in value:
        if isinstance(key, str) and key in BY_KEY and key not in keys:
            keys.append(key)
    return keys


def complete(order) -> list[str]:
    """Every key, in ``order`` first and then the ones it does not place, in the default
    order. What is drawn, and what the settings page lists."""
    placed = known_keys(order)
    return placed + [key for key in DEFAULT_ORDER if key not in placed]


def order_of(profile) -> list[str]:
    """Every key, in the order this person reads the navigation in."""
    return complete(getattr(profile, "nav_order", None))


def hidden_keys(profile) -> set[str]:
    return set(known_keys(getattr(profile, "hidden_nav_items", None)))


def visible_items(profile) -> list[NavItem]:
    """What the navigation draws for this person, in their order."""
    hidden = hidden_keys(profile)
    return [BY_KEY[key] for key in order_of(profile) if key not in hidden]


def dashboard_hidden(profile) -> bool:
    return "dashboard" in hidden_keys(profile)


def move(order, key: str, direction: str) -> list[str]:
    """One key, one place up or down. Returns the new order, every key in it.

    Past the end is a no-op rather than a wrap: somebody pressing *up* on the first item
    means to find out that it is the first, not to send it to the bottom -- the rule the
    dashboard's arrows and the column chooser's already keep.
    """
    keys = complete(order)
    if key not in keys or direction not in DIRECTIONS:
        return keys
    index = keys.index(key)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(keys):
        keys[index], keys[target] = keys[target], keys[index]
    return keys


def can_move(order, key: str, direction: str) -> bool:
    """Whether that direction would change anything. What disables a button."""
    return move(order, key, direction) != complete(order)


def to_store(order) -> list[str]:
    """What the profile keeps for ``order``: nothing, when it is the default order.

    Every key is placed once somebody arranges the list, which is what lets an item added
    later arrive at the end of it. The default order stored as itself would freeze it, and
    a release that changed the default would then pass by everybody who had only pressed
    *Save*.
    """
    keys = complete(order)
    return [] if keys == list(DEFAULT_ORDER) else keys
