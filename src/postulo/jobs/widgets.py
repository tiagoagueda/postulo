"""Dashboard widgets about the stage before an application: listings noticed.

Separate from the applications widgets because the models are, and because the question is
a different one. "What is waiting for me to decide" is work; "how choosy have I been" is a
figure about the search.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.applications.widgets import RECORD, TODAY
from postulo.core.widgets import Sources, Widget, register


def _to_decide(sources: Sources) -> dict:
    listings = sources.listings
    return {
        "listings_to_decide": listings.undecided().count(),
        "closing_soon_count": listings.closing_soon().count(),
        "closing_soon": listings.closing_soon().select_related("company")[:5],
    }


def _selectivity(sources: Sources) -> dict:
    return {"insights": sources.insights}


register(
    Widget(
        key="listings",
        label=_("Listings to decide on"),
        blurb=_("What you have noticed and not yet acted on, and what closes this week."),
        template="widgets/listings.html",
        context=_to_decide,
        width="half",
        group=TODAY,
    )
)

register(
    Widget(
        key="selectivity",
        label=_("How choosy you have been"),
        blurb=_("Of the listings you noticed, how many you applied to and how many you let go."),
        template="widgets/selectivity.html",
        context=_selectivity,
        width="half",
        group=RECORD,
    )
)
