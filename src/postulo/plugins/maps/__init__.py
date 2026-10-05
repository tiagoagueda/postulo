"""The feature that puts a person's companies on a map (#700).

A company's location is placed from an offline table of cities, and the companies are
drawn as dots on a world outline the server writes itself (#108). This is the one switch
that decides whether any of that is offered: the map page, the "Map" button, the guess
made when a location is saved, and the correction a person can type beside it.

**Off is exactly what Postulo did before there was a map.** Companies carry a location as
text and nothing is placed. Nothing is deleted to get there: every coordinate already
stored, and the record of whether it was guessed or corrected, stays where it is, and the
day somebody switches this back on they are all still there.

**Only the declaration lives here.** The coordinates are columns of ``Company``, a core
model, because they are a person's data and a plugin cannot own a table. What a plugin
says is *whether this capability is offered*; :mod:`postulo.jobs.mapping` is where the
rest of the application asks.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
MAPS = "maps"


@declares(
    shipped(
        name=MAPS,
        label=_("Companies on a map"),
        kind="feature",
        description=_(
            "Show where the companies are on a map, and place a company from the town "
            "typed in its location, using a table of cities kept on this server. "
            "Switched off, there is no map page and no location is placed; the "
            "coordinates already stored stay recorded and come back untouched when it is "
            "switched on again."
        ),
    )
)
class MapsFeature:
    """Companies as dots on a map, placed from an offline table and never by a service."""
