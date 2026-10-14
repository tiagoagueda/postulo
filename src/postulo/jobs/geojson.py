"""The companies' places as GeoJSON, out and in (#880).

**Out**: a ``FeatureCollection`` (RFC 7946) of every company address the map draws, one
``Point`` each, longitude first as the standard says, with the company's name, its page, its
industries, the kind of address and how the point was placed (its town, its postcode, by hand
or from Wikidata), so a reader of the file knows how precise each one is. A contact's or the
person's own address is never written: the file is for the companies.

**In**: points matched to the person's companies by name, or by a Wikidata identifier in the
feature's properties, shown first with nothing ticked, and a ticked one placing that
company's primary address by hand. A point that matches no company, or a company with no
address to place, is listed and never made into anything.

Offered only while the *GeoJSON* format is on, which itself needs the map (#884).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from django.urls import reverse
from django.utils.translation import gettext as _

from postulo.core import slugs

MAX_BYTES = 2 * 1024 * 1024
MAX_FEATURES = 2000
SESSION_KEY = "geojson-places"


class GeoJsonRefused(Exception):
    """Why a file could not be read, in a sentence."""


def export(person) -> dict:
    """The person's company addresses on the map, as a GeoJSON ``FeatureCollection``."""
    from django.contrib.contenttypes.models import ContentType

    from postulo.core.models import PostalAddress

    from .models import Company

    companies = {
        company.pk: company
        for company in Company.objects.for_user(person).prefetch_related("industries")
    }
    # Through the companies: a shared company's address is reached by it (#896).
    rows = PostalAddress.objects.filter(
        object_id__in=list(companies),
        content_type=ContentType.objects.get_for_model(Company),
        latitude__isnull=False,
        longitude__isnull=False,
    )
    features = []
    for row in rows.order_by("object_id", "-is_primary", "pk"):
        company = companies.get(row.object_id)
        if company is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [row.longitude, row.latitude]},
                "properties": {
                    "name": company.name,
                    "url": reverse("jobs:company_detail", args=[company.pk]),
                    "industries": [industry.name for industry in company.industries.all()],
                    "wikidata": company.wikidata_id(),
                    "address": row.kind,
                    "primary": row.is_primary,
                    "town": row.municipality,
                    "country": row.country,
                    "placed_by": row.placed_by,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@dataclass
class Point:
    index: int
    name: str
    wikidata: str
    lat: float
    lon: float


def read(raw: bytes) -> list[Point]:
    """The points in a GeoJSON file. Raises `GeoJsonRefused` with the reason."""
    if len(raw) > MAX_BYTES:
        raise GeoJsonRefused(
            _("That file is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_BYTES // (1024 * 1024)}
        )
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        raise GeoJsonRefused(_("That file is not GeoJSON.")) from error
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise GeoJsonRefused(_("That file is not a GeoJSON collection of features."))
    features = document.get("features")
    if not isinstance(features, list):
        raise GeoJsonRefused(_("That file is not a GeoJSON collection of features."))
    points: list[Point] = []
    for feature in features[:MAX_FEATURES]:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        properties = feature.get("properties") or {}
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        try:
            lon, lat = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        name = properties.get("name") if isinstance(properties, dict) else ""
        qid = properties.get("wikidata") if isinstance(properties, dict) else ""
        name = " ".join(name.split())[:200] if isinstance(name, str) else ""
        qid = qid.strip().upper()[:20] if isinstance(qid, str) else ""
        if not name and not qid:
            continue
        points.append(Point(index=len(points), name=name, wikidata=qid, lat=lat, lon=lon))
    return points


def hold(points: list[Point]) -> list[dict]:
    return [point.__dict__ for point in points]


def held(data) -> list[Point]:
    if not isinstance(data, list):
        return []
    found = []
    for row in data:
        try:
            found.append(
                Point(
                    index=int(row["index"]),
                    name=str(row["name"]),
                    wikidata=str(row["wikidata"]),
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _match(person, point: Point):
    from .models import Company

    if point.wikidata:
        company = Company.by_identifier(person, "wikidata", point.wikidata)
        if company is not None:
            return company
    if point.name:
        # A file placing companies matches the person's own, as every import does (#896).
        return (
            Company.objects.owned_by(person)
            .filter(name_key=slugs.name_key(point.name))
            .prefetch_related("postal_addresses")
            .first()
        )
    return None


def _primary(company):
    rows = list(company.postal_addresses.all())
    return next((row for row in rows if row.is_primary), rows[0] if rows else None)


def review(person, points: list[Point]) -> list[dict]:
    """What each point would do: place a company's primary address, or nothing, and why."""
    entries = []
    for point in points:
        company = _match(person, point)
        address = _primary(company) if company is not None else None
        if company is None:
            outcome = "unmatched"
        elif address is None:
            outcome = "no_address"
        else:
            outcome = "place"
        entries.append({"point": point, "company": company, "address": address, "outcome": outcome})
    return entries


def apply(person, points: list[Point], chosen: set[int]) -> int:
    """Place the chosen points' companies by hand; how many were placed."""
    from . import placing

    placed = 0
    for entry in review(person, points):
        point = entry["point"]
        if point.index not in chosen or entry["outcome"] != "place":
            continue
        placing.place_by_hand(entry["address"], round(point.lat, 6), round(point.lon, 6))
        placed += 1
    return placed
