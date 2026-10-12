"""The feature that reads a company's Wikidata item, when a person presses for it (#673).

A company may carry a Wikidata identifier (#42). With this on, its page offers *Fetch from
Wikidata*: one request to ``www.wikidata.org`` for that one item, which comes back with the
company's names in other languages and, where the item has one, where it is (#701). Nothing
is fetched on a page load, a save or a schedule, and nothing is kept until the person says
so. `postulo.jobs.wikidata` is where the request is made and what it is held to.

**Off is exactly what Postulo did before.** The button goes and nothing reaches Wikidata;
every name and point already kept stays where it is.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on, fixed as a source's name is.
WIKIDATA = "wikidata"


@declares(
    shipped(
        name=WIKIDATA,
        label=_("Wikidata"),
        kind="feature",
        icon="globe",
        description=_(
            "Fetch a company's names in other languages, and where it is, from its Wikidata "
            "item, when you press for it: one request to www.wikidata.org for that one item, "
            "which tells Wikidata which organisation you are looking at. Wikidata's data is "
            "CC0. Switched off, nothing is fetched; the names and places already kept stay."
        ),
    )
)
class WikidataFeature:
    """A company's Wikidata item, read on a press and never otherwise."""
