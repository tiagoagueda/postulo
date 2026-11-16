"""GeoNames: the offline table of places, as a source of city suggestions (#1252).

The second reference vocabulary behind the `suggestion-source` kind, after NACE. It
suggests for the ``city`` field of a postal address the names of the cities GeoNames lists
above a thousand inhabitants (`postulo.jobs.places`, whose table ``manage.py fetch_geonames``
downloads; ``jobs/data/GEONAMES-LICENCE.md`` says on whose terms), the largest first.

**Bundled and on by default**, because it asks nobody anything: the table is a file on this
server, and nothing typed in a box goes anywhere. Without the table downloaded it offers
nothing, which is what a plain box does.

**Off is a plain field and an address that is not placed by it.** An administrator or a
person who switches it off gets a city box that suggests only what they have typed before,
and no address is put on the map from this table; every point already stored, and the words
of every address, stay exactly as they are (`postulo.jobs.places.enabled`).

This slice moves *who is asked*. The lookup module and its command stay in `jobs`, where
the address model's placing, the importer, the map page and the system checks still read the
table; moving those readers behind the registry is for later slices, as it was for NACE.
Postcodes are not suggested yet: a postcode is narrowed by its country, and the box asks
only for what was typed.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import SuggestionSource, declares, shipped

#: The identifier the registry keys on. Not a policy key anything else decides about.
GEONAMES = "geonames"

GEONAMES_LICENCE = "CC BY 3.0 (GeoNames)"


def _offer(field, exclude=(), language="", first=(), typed=""):
    """City names that begin with what was typed, the largest cities first."""
    from postulo.jobs import places

    if field != "city" or not typed:
        return []
    skipped = {str(name).casefold() for name in exclude}
    return [(name, "") for name in places.suggest_cities(typed) if name.casefold() not in skipped]


@declares(
    shipped(
        name=GEONAMES,
        label=_("GeoNames places"),
        kind="suggestion-source",
        description=_(
            "Suggests the name of a city as you type it into an address, from the table of "
            "the cities of more than a thousand inhabitants that GeoNames publishes, kept "
            "on this server. Nothing you type is sent anywhere. Switched off, the city box "
            "suggests only what you have typed before, no address is placed from this "
            "table, and every place and address already kept stays as it is."
        ),
    )
)
class Geonames:
    """A source of suggestions. Postulo asks it what to offer; it never asks anything of anyone."""

    sources = (
        SuggestionSource(
            key="geonames",
            label="GeoNames",
            fields=("city",),
            languages=(),
            offline=True,
            licence=GEONAMES_LICENCE,
            offer=_offer,
        ),
    )
