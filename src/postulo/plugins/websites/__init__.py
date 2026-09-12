"""The feature that keeps several websites per person, on or off.

The same shape as :mod:`postulo.plugins.social_profiles`, and the module docstring there
says why the three link features are three plugins over one table rather than one plugin
over three (#189). **Only the declaration lives here**; the rows are ``core.WebLink``.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
WEBSITES = "websites"


@declares(
    shipped(
        name=WEBSITES,
        label=_("Several websites"),
        kind="feature",
        description=_(
            "Keep more than one website for yourself and for the people you deal with — a "
            "personal site, a blog, a portfolio — with one of them marked as the one to "
            "show. Switched off, Postulo shows and uses the primary website only; the "
            "others stay recorded and come back untouched when it is switched on again."
        ),
    )
)
class WebsitesFeature:
    """Several websites per person and per contact, one of them primary.

    Off is exactly what Postulo did before this existed: one website in one box, which is
    the primary row and nothing else. Nothing is deleted to get there.
    """
