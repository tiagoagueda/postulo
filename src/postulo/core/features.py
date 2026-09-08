"""Parts of Postulo that switch on and off, and the first of them.

A feature plugin governs a capability of the application itself rather than a conversation
with anything outside it. It declares who it is and nothing else; :mod:`postulo.plugins
.policy` decides whether it is on for a given person, and the code that offers the feature
asks before offering it.

The reason this is a plugin at all, rather than a settings checkbox, is that Postulo
already has one place where a capability is switched on for a person, argued about by an
administrator and explained on their settings page — with a record of who decided and a
sentence saying so. A second mechanism for the same question would be a second set of
edge cases and a second thing to remember at every call site.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.base import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
PHONE_NUMBERS = "phone-numbers"


@declares(
    shipped(
        name=PHONE_NUMBERS,
        label=_("Several telephone numbers"),
        kind="feature",
        description=_(
            "Keep more than one number for yourself and for the people you deal with — a "
            "mobile, a desk line, a switchboard — with one of them marked as the one to "
            "use. Switched off, Postulo shows and uses the primary number only; the "
            "others stay recorded and come back untouched when it is switched on again."
        ),
    )
)
class PhoneNumbersFeature:
    """Several numbers per person and per contact, one of them primary.

    Off is not a smaller version of on: it is exactly what Postulo did before this
    existed. One number in one box, on the profile and on a contact, which is the primary
    row and nothing else. Nothing is deleted to get there — the rest of the rows are
    simply not offered, and the day somebody switches this back on they are all still
    there, in the order they were left.
    """
