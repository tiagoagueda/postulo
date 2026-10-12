"""What a plugin's categories are called (#873).

A category is a label a module or a plugin carries so the page can group and filter what it
draws, as a label groups issues. It is not a kind: a kind is what the registry loads a
plugin by and what its code has to be, and a category changes nothing about how a plugin
runs. A plugin names its categories by slug, in its manifest; the words are here, in the
reader's language, so a package does not have to translate a word Postulo already has.

A slug this version does not know is shown as it is, as an unknown kind is (see
:mod:`postulo.plugins.kinds`): a package written for a later release is better described by
its own word than by none.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

LABELS: dict[str, str] = {
    "people": _("People"),
    "contacts": _("Contacts"),
    "notifications": _("Notifications"),
    "files": _("Files"),
    "calendar": _("Calendar"),
    "places": _("Places"),
    "companies": _("Companies"),
    "accounts": _("Accounts"),
    "capture": _("Capture"),
}


def label_for(category: str) -> str:
    """The category's name, or its slug for one this version does not know."""
    return LABELS.get(category, category)
