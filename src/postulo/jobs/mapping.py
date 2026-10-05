"""Whether the map is being offered (#700).

One gate, so "off places nothing and shows no map" is one sentence in the code as well as
in the interface, rather than a rule several call sites remember and another forgets.
"""

from __future__ import annotations

from postulo.plugins.maps import MAPS


def map_offered(person) -> bool:
    """Whether this person is being offered the company map and the placing of locations."""
    from postulo.plugins.policy import decide

    return bool(decide(MAPS, person).on)
