"""The feature that keeps several social profiles per person, on or off.

A feature plugin governs a capability of the application itself rather than a conversation
with anything outside it. It declares who it is and nothing else; :mod:`postulo.plugins
.policy` decides whether it is on for a given person, and the code that offers the feature
asks before offering it.

**Only the declaration lives here.** The profiles themselves are rows of one core model,
``core.WebLink``, shared with the repositories and the websites a person lists: they are a
person's data, Postulo does the ownership scoping, and what a plugin says is *whether this
capability is offered*. Three plugins over one table rather than one plugin over three,
because they are three separate decisions -- an administrator who is happy for people to
list every code forge they publish on may still want one LinkedIn and nothing else on a
CV header, and a switch that answered both at once would answer neither (#189).
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
SOCIAL_PROFILES = "social-profiles"


@declares(
    shipped(
        name=SOCIAL_PROFILES,
        label=_("Several social profiles"),
        kind="feature",
        description=_(
            "Keep more than one social profile for yourself and for the people you deal "
            "with — a LinkedIn, a Mastodon, a Bluesky — with one of them marked as the one "
            "to show. Switched off, Postulo shows and uses the primary profile only; the "
            "others stay recorded and come back untouched when it is switched on again."
        ),
    )
)
class SocialProfilesFeature:
    """Several social profiles per person and per contact, one of them primary.

    Off is not a smaller version of on: it is exactly what Postulo did before this
    existed. One profile in one box, on the profile page and on a contact, which is the
    primary row and nothing else. Nothing is deleted to get there -- the rest of the rows
    are simply not offered, and the day somebody switches this back on they are all still
    there, in the order they were left.
    """
