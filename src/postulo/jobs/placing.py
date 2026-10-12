"""Putting a postal address on the map, offline, as it is saved (#702).

An address is placed at its postcode's centroid where GeoNames' postal codes were downloaded
(``manage.py fetch_geonames --postcodes``), and otherwise at its town, from the same offline
city table a company's location was placed from (#108). Nothing is sent anywhere: a street
is the most sensitive thing this touches, and it is never read here at all.

A point a person put there by hand, or fetched from Wikidata on a press (#701), is kept when
the address's words change, because it was not derived from them; anything else follows the
words. With the map switched off for the owner nothing is placed and nothing stored is
touched, as for a company's location before (#700).
"""

from __future__ import annotations

from django.utils import timezone

from . import mapping, places

KEPT = ("manual", "wikidata")


def place(address) -> None:
    """Set ``address``'s point from its parts. Called by ``PostalAddress.save``."""
    if address.placed_by in KEPT:
        return
    key = address.place_key()
    if key == address.placed_from:
        return
    owner = getattr(address, "owner", None)
    if owner is None or not mapping.map_offered(owner):
        return
    address.placed_from = key
    address.placed_at = timezone.now()
    found = places.place(address.postcode, address.municipality, address.country)
    if found is None:
        address.latitude = address.longitude = None
        address.placed_by = ""
        return
    how, point = found
    address.latitude, address.longitude = point["lat"], point["lon"]
    address.placed_by = how


def place_by_hand(address, lat: float, lon: float) -> None:
    """Pin ``address`` where a person put it, until they take it off (#702)."""
    address.latitude, address.longitude = lat, lon
    address.placed_by = "manual"
    address.placed_at = timezone.now()
    address.save(update_fields=["latitude", "longitude", "placed_by", "placed_at", "updated_at"])


def take_off_by_hand(address) -> None:
    """Forget a point put there by hand or from Wikidata (#701): placed from its words again."""
    address.placed_by = ""
    address.placed_from = ""
    address.latitude = address.longitude = None
    address.save()
