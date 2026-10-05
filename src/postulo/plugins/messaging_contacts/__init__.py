"""The feature that keeps messaging handles per person, on or off (#682).

A feature plugin governs a capability of the application itself. It declares who it is and
nothing else; :mod:`postulo.plugins.policy` decides whether it is on for a given person, and
the code that offers the feature asks before offering it.

**Only the declaration lives here.** The handles themselves are a core model, because they
are a person's data and Postulo does the ownership scoping.

**Off offers none and keeps every row.** There is no single box to fall back to, as there
is for a telephone number: before this feature there was no handle anywhere, so off is
exactly Postulo as it was. Nothing is deleted to get there, and the day somebody switches
it back on they are all still there.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
MESSAGING_CONTACTS = "messaging-contacts"


@declares(
    shipped(
        name=MESSAGING_CONTACTS,
        label=_("Messaging contacts"),
        kind="feature",
        description=_(
            "Keep the ways you and the people you deal with are reached on Matrix, XMPP, "
            "Signal, Telegram, Threema or another messaging service, as many as you like, "
            "one of them marked as the one to use. Switched off, Postulo offers none; the "
            "ones already recorded stay and come back untouched when it is switched on again."
        ),
    )
)
class MessagingContactsFeature:
    """Messaging handles per person and per contact, one of them primary."""
