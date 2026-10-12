"""Where the map's tiles come from, and what that tells whom (#898).

The companies' map is drawn by Leaflet over a basemap of raster tiles. Which tiles is the
operator's decision, made under *Server settings → Plugins → Maps* or pinned in the
environment, and there are three answers:

**OpenStreetMap, the default.** The browser fetches ``tile.openstreetmap.org`` directly. That
server then learns the viewer's address and which part of the world they look at -- the
places their companies are. #700 decided against this default; the maintainer reversed it on
2026-10-12, and ``docs/THREAT-MODEL.md`` says what it costs. The foundation's tile policy asks
for a visible credit, a Referer, no prefetching and no offline use; Leaflet fetches only the
tiles in view, the credit is drawn on the map, and the Referer is sent as the instance's
origin alone (the site's own policy is ``same-origin``, which would send none).

**A tile server of the operator's own**, by URL template: ``https://`` on another host, or a
path on this instance's own origin, which then sends nothing anywhere.

**None**: Leaflet draws the Natural Earth outline the SVG map used, and nothing is fetched.

**The content security policy opens on the map page only, and only to the configured
origin.** Every other page keeps ``img-src 'self' data:``, and the global policy never names
a third-party origin (``tests/security/test_requests.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.utils.translation import gettext_lazy as _

OSM = "osm"
CUSTOM = "custom"
NONE = "none"

SOURCES = (OSM, CUSTOM, NONE)

#: The labels the settings page offers, in the order it offers them.
SOURCE_CHOICES = (
    (OSM, _("OpenStreetMap's public tiles")),
    (CUSTOM, _("A tile server of your own")),
    (NONE, _("No tiles: the world's outline only")),
)

#: The foundation's own address for its standard layer. Not hard-coded into a page: the
#: tile policy asks that the URL be changeable without a software update, which the setting
#: is.
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_COPYRIGHT = "https://www.openstreetmap.org/copyright"
#: The standard layer's deepest zoom.
OSM_MAX_ZOOM = 19

#: What a URL template may hold: no spaces, quotes or angle brackets, which would let a value
#: typed on a settings page step out of the attribute it is written into.
_SAFE = re.compile(r"[^\s\"'<>`\\]+")
#: A host may start with Leaflet's ``{s}`` subdomain placeholder and nothing else may be a
#: placeholder in it.
_HOST = re.compile(r"(\{s\}\.)?[A-Za-z0-9.-]+(:\d{1,5})?")
MAX_URL = 500


def url_problem(url: str) -> str:
    """Why ``url`` cannot be a tile template, or ``""`` when it can.

    ``https://`` on any host, or an absolute path on this instance's own origin; with
    ``{z}``, ``{x}`` and ``{y}`` in it, because that is how Leaflet asks for one tile.
    """
    url = (url or "").strip()
    if not url:
        return str(_("Give the address of the tiles."))
    if len(url) > MAX_URL or not _SAFE.fullmatch(url):
        return str(_("That is not a web address."))
    if not all(part in url for part in ("{z}", "{x}", "{y}")):
        return str(_("The address must hold {z}, {x} and {y}, where each tile's numbers go."))
    if url.startswith("/") and not url.startswith("//"):
        return ""
    parts = urlsplit(url)
    if parts.scheme != "https" or not _HOST.fullmatch(parts.netloc or ""):
        return str(
            _("Use an https:// address, or a path starting with / on this instance's own address.")
        )
    return ""


def origin_of(url: str) -> str:
    """The origin a page must allow images from for ``url``, or ``""`` for its own.

    ``{s}`` subdomains become a wildcard, which is how a content security policy names
    ``a.``, ``b.`` and ``c.`` at once.
    """
    if url.startswith("/") and not url.startswith("//"):
        return ""
    parts = urlsplit(url)
    host = parts.netloc.replace("{s}.", "*.")
    return f"{parts.scheme}://{host}"


@dataclass(frozen=True)
class Tiles:
    """The basemap in force: where from, the origin to allow, and the credit to draw."""

    source: str
    url: str = ""
    origin: str = ""
    attribution: str = ""
    #: Where the credit links, for OpenStreetMap's copyright page; empty for typed text.
    attribution_url: str = ""
    max_zoom: int = OSM_MAX_ZOOM

    @property
    def fetched(self) -> bool:
        return self.source != NONE


def _value(field: str, setting: str) -> str:
    from postulo.core import site

    if site.overridden_by(field):
        return str(getattr(settings, setting, "") or "").strip()
    stored = getattr(site.current(), field, "") or ""
    if stored:
        return str(stored).strip()
    return str(getattr(settings, setting, "") or "").strip()


def current() -> Tiles:
    """The basemap this instance draws: the environment, then the settings page, then OSM.

    A custom source whose address is not a usable template draws no tiles rather than
    guessing: nothing is fetched from an address nobody can vouch for, and the settings page
    refuses one before it is stored, so only a bad environment value lands here, and
    ``manage.py check`` names it.
    """
    source = _value("map_tiles", "POSTULO_MAP_TILES").lower() or OSM
    if source not in SOURCES:
        source = OSM
    if source == NONE:
        return Tiles(source=NONE)
    if source == OSM:
        return Tiles(
            source=OSM,
            url=OSM_URL,
            origin=origin_of(OSM_URL),
            attribution=OSM_ATTRIBUTION,
            attribution_url=OSM_COPYRIGHT,
        )
    url = _value("map_tile_url", "POSTULO_MAP_TILE_URL")
    if url_problem(url):
        return Tiles(source=NONE)
    return Tiles(
        source=CUSTOM,
        url=url,
        origin=origin_of(url),
        attribution=_value("map_tile_attribution", "POSTULO_MAP_TILE_ATTRIBUTION"),
    )


def policy_for(tiles: Tiles) -> dict:
    """The site's content security policy with this basemap's origin added to ``img-src``.

    Set on the map page's response alone; the global policy is not touched.
    """
    policy = {key: list(value) for key, value in settings.SECURE_CSP.items()}
    if tiles.fetched and tiles.origin:
        policy["img-src"] = [*policy.get("img-src", []), tiles.origin]
    return policy
