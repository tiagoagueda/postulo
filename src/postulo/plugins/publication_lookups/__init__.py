"""The feature that suggests a publication from a DOI or an ORCID record, when asked (#688).

With this on, a publication's form has *Look up* beside its DOI, which asks doi.org for that
one record and fills the boxes that are still empty, and the Publications section of *Your
career* offers *Suggest from my ORCID record* to a person who has stored their ORCID, which
asks ORCID for the list of their public works and adds the ones they tick. Nothing is asked
on a page load, a save, an import or a schedule, and nothing is kept until the person saves.
`postulo.resume.lookups` is where the requests are made and what they are held to.

**Off is exactly what Postulo did before.** Both buttons go, both addresses answer as if
they did not exist, and nothing reaches doi.org or ORCID; every publication stays as it is.
ORCID's terms bar using its public API in a revenue-generating service, so an operator who
sells hosted accounts is the one this switch is for.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on, fixed as a source's name is.
PUBLICATION_LOOKUPS = "publication-lookups"


@declares(
    shipped(
        name=PUBLICATION_LOOKUPS,
        label=_("Publication lookups"),
        kind="feature",
        icon="book-text",
        description=_(
            "Fill in a publication from its DOI, and suggest publications from your ORCID "
            "record, when you press for it: one request to doi.org, which passes the DOI to "
            "Crossref or DataCite, or one to ORCID for your public works. Nothing else about "
            "you is sent, and nothing is kept until you save. Switched off, nothing is asked."
        ),
    )
)
class PublicationLookupsFeature:
    """A publication's details from a public registry, on a press and never otherwise."""
