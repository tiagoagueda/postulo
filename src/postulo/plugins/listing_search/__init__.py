"""Listing search: searching a job service by criteria, when a person presses for it (#1253).

A capture reads one advert from one address. This feature offers *Search a job service* under
Listings: a person says what they look for, presses *Search*, sees the adverts a public
employment service has, and presses *Review* on one, which becomes a capture and goes through
the review every capture goes through. `postulo.jobs.listing_search` is where the request is
made and what it is held to.

**Off until an administrator switches it on** (`plugins` migration 0018 writes the instance's
row): a press tells a service, and so whoever runs it, what somebody on this instance is
looking for, and that is the operator's decision to make, not Postulo's. **Each service is a
switch of its own under it**, one sub-plugin per `ListingSearch` installed, on once the feature
is, so an administrator can offer one country's service and not another's.

**Off is exactly what Postulo did before.** No button, no request; every listing and capture
already made stays where it is, with the service's name in its *found via*.
"""

from __future__ import annotations

from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import SubPlugin, declares, listing_searches, shipped

#: The identifier the policy rows key on, fixed as a source's name is.
LISTING_SEARCH = "listing-search"


def _described(search) -> object:
    """One service's sentence on the plugins page: who is asked, what is sent, the licence."""
    hosts = ", ".join(search.hosts)
    if search.licence:
        return format_lazy(
            _(
                "Asks {hosts} when you press Search or Review, sending the words, the place "
                "and the filters searched for, or the advert's number, and nothing about you. "
                "Its adverts are under {licence}."
            ),
            hosts=hosts,
            licence=search.licence,
        )
    return format_lazy(
        _(
            "Asks {hosts} when you press Search or Review, sending the words, the place and "
            "the filters searched for, or the advert's number, and nothing about you."
        ),
        hosts=hosts,
    )


@declares(
    shipped(
        name=LISTING_SEARCH,
        label=_("Listing search"),
        kind="feature",
        icon="search",
        description=_(
            "Search a job service from Listings when you press for it, and review an advert "
            "you choose as any capture is reviewed. One request per press, carrying the "
            "words, the place and the filters searched for and nothing about the person, to "
            "the service's own address, which so learns what somebody here is looking for. "
            "Each service has its own switch. Off until an administrator switches it on; "
            "switched off, nothing is asked and nothing already kept is touched."
        ),
    )
)
class ListingSearchFeature:
    """A job service's adverts, searched on a press and never otherwise."""

    def sub_plugins(self):
        """One switch per job service installed, read from the registry when this loads."""
        return tuple(
            SubPlugin(
                id=search.key,
                label=search.label,
                description=_described(search),
                icon="globe",
            )
            for search in listing_searches().values()
        )
