"""The company's own website, as a source of its details and of its logo (#1256).

The reference source of the `company-enrichment` kind, and the plugin *Find logo* now asks.
It reads one page -- the website the person recorded -- the way a capture reads a posting:
public addresses only, every redirect revalidated, ``robots.txt`` honoured. What it reads is
only what the site declares about itself: its icons, and a schema.org ``Organization`` in
JSON-LD, microdata or RDFa (`reading`).

**It needs no third party.** The only server that learns anything is the one whose address
the person recorded, and it learns what any visitor's browser tells it.

**Bundled and on by default**, because *Find logo* has read the company's site for everybody
since it was written, and this is that code moved behind a switch. An administrator switches
it off under *Server settings → Plugins*; then *Find logo* is not offered, *Find details*
does not list the site, and the logo's address, an upload, *Refresh* and *Remove* stay.
A logo it finds is fetched, decoded and kept by Postulo, as one from an address is.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import EnrichmentSource, declares, shipped

from .reading import read

#: The identifier the registry and the policy key on, fixed as a source's name is.
COMPANY_SITE = "company-site"


def _addresses(subject) -> list[str]:
    return [subject.website]


@declares(
    shipped(
        name=COMPANY_SITE,
        label=_("The company's own website"),
        kind="company-enrichment",
        icon="globe",
        description=_(
            "Reads the website you recorded for a company, when you press Find logo or Find "
            "details, for what it declares about itself: its icon and logo, and the "
            "organisation it describes in its markup (names, mailbox, number, profiles, "
            "address, identifiers, when it was founded, how many it employs). One page, with "
            "the site's robots.txt honoured, and nothing sent but the request itself. "
            "Nothing is kept until you say so. Switched off, Find logo is not offered and "
            "the site is not read; a logo already kept stays."
        ),
    )
)
class CompanySite:
    """A source of company details. Postulo asks it how the site is read; it asks nobody."""

    sources = (
        EnrichmentSource(
            key=COMPANY_SITE,
            label=_("The company's own website"),
            starts_from=("website",),
            offers=(
                "logo",
                "email",
                "phone",
                "social",
                "address",
                "identifier",
                "founded",
                "size",
                "acronym",
                "name",
            ),
            own_site=True,
            reads="page",
            addresses=_addresses,
            read=read,
            sends=_("nothing but the request itself, to the company's own website"),
            source=_("the company's own website"),
        ),
    )
