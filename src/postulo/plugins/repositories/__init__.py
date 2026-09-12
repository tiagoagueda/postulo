"""The feature that keeps several code repositories per person, on or off.

The same shape as :mod:`postulo.plugins.social_profiles`, and the module docstring there
says why the three link features are three plugins over one table rather than one plugin
over three (#189). **Only the declaration lives here**; the rows are ``core.WebLink``.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
REPOSITORIES = "repositories"


@declares(
    shipped(
        name=REPOSITORIES,
        label=_("Several code repositories"),
        kind="feature",
        description=_(
            "Keep more than one code repository or forge profile for yourself and for the "
            "people you deal with — a GitHub, a Codeberg, the one project worth showing — "
            "with one of them marked as the one to show. Switched off, Postulo shows and "
            "uses the primary repository only; the others stay recorded and come back "
            "untouched when it is switched on again."
        ),
    )
)
class RepositoriesFeature:
    """Several repositories per person and per contact, one of them primary.

    Off is exactly what Postulo did before this existed: one repository in one box, which
    is the primary row and nothing else. Nothing is deleted to get there.
    """
