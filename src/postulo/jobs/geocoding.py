"""Pinning an address at its street, with a geocoding service the operator chose (#913).

#700 kept every pin at a postcode or a town, offline, and left street precision to a press on
one address. The maintainer reversed that on 2026-10-15: an address with a street is pinned at
the street, looked up with a geocoding service. What stays is everything #702 built around it:
a point placed by hand or from Wikidata wins and is never overwritten, *take off* gives a point
back to the words, and an address with no street keeps the offline placement.

**Off unless the operator switches it on**, under *Server settings → Plugins → Maps* or with
``POSTULO_GEOCODER``. Three kinds of service, each at a base address so an operator can point
it at their own:

- **Nominatim** (OpenStreetMap), pre-filled with the foundation's public instance. Its policy
  allows one request a second for the whole application, asks for an identifying agent and
  for results to be kept, and asks *not* to be sent personal data.
- **Photon**, usually an operator's own: one country's data is a few gigabytes.
- **addok**, the engine behind France's Base Adresse Nationale and the Géoplateforme.

**What is sent.** The parts of one address, as a structured query: street, postcode, town,
region, country. Never a name, never who it belongs to. Which addresses: every company's, and a
person's own or a contact's only where it is shown on the map (`on_map`), since one that is
not drawn has no pin to make finer. People's and contacts' addresses are personal data, so the
setting asks the operator's word (`geocoder_consent`) before anything is sent; set in the
environment, the choice is that word. ``docs/THREAT-MODEL.md`` says what this costs.

**When.** Not as the address is saved: saving places it offline, at once, as before, and the
scheduler looks the street up on its next pass. That keeps a stranger's server out of every
form that saves an address, and it makes the one-request-a-second rule hold for the whole
instance without a lock, because only one scheduler pass runs at a time. Addresses saved before
the setting was switched on are found the same way, a few a pass, which is the slow backfill
the service's policy asks for.

**Outcomes.** A street-level answer pins the address (``placed_by = "street"``). No answer, or
an answer only as precise as a town, leaves the offline point and is not asked again until the
address changes. A timeout or a busy service is tried again an hour later. A refusal (403)
stops every look-up and says so on the settings page, since the policy asks that an application
can be switched off on request.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.plugins import http

logger = logging.getLogger(__name__)

OFF = "off"
NOMINATIM = "nominatim"
PHOTON = "photon"
ADDOK = "addok"

SERVICES = (NOMINATIM, PHOTON, ADDOK)

#: The labels the settings page offers, in the order it offers them.
CHOICES = (
    (OFF, _("Off: pins stay at the postcode or the town, worked out on this instance")),
    (NOMINATIM, _("Nominatim (OpenStreetMap)")),
    (PHOTON, _("Photon")),
    (ADDOK, _("addok (France's Base Adresse Nationale)")),
)

#: Names for the line under an address and the data-protection page.
NAMES = {NOMINATIM: "Nominatim", PHOTON: "Photon", ADDOK: "addok"}

#: The OpenStreetMap Foundation's public instance, filled in when Nominatim is chosen with no
#: address of its own.
PUBLIC_NOMINATIM = "https://nominatim.openstreetmap.org"
DEFAULT_URLS = {NOMINATIM: PUBLIC_NOMINATIM}

#: The credit a pin found this way asks for, drawn with the map's own.
ATTRIBUTIONS = {
    NOMINATIM: "© OpenStreetMap contributors, ODbL",
    PHOTON: "© OpenStreetMap contributors, ODbL",
    ADDOK: "Base Adresse Nationale, Etalab 2.0",
}

#: Seconds between two look-ups: Nominatim's public policy, kept for every service since one
#: scheduler pass is the only caller.
SPACING = 1.0
#: Seconds one scheduler pass may spend looking up, after which the rest wait for the next.
BUDGET = 60.0
#: How long before a look-up that failed for a passing reason is tried again.
RETRY = dt.timedelta(hours=1)
#: Each wait, and the whole request: a geocoder answers in well under a second or is down.
TIMEOUT = 10.0
TOTAL = 15.0
#: A one-result answer is a few kilobytes; anything larger is not one.
MAX_BYTES = 256 * 1024
#: Nominatim's ``place_rank`` from which a result is a street or finer (26 street, 30 house).
STREET_RANK = 26
MAX_URL = 300


class Unavailable(Exception):
    """The look-up did not happen, for a reason that may pass: try again later."""


class Refused(Exception):
    """The service refused this application: stop every look-up until the setting is saved."""


@dataclass(frozen=True)
class Geocoder:
    """The service in force, and what to call it."""

    service: str
    url: str

    @property
    def name(self) -> str:
        return NAMES[self.service]

    @property
    def attribution(self) -> str:
        return ATTRIBUTIONS[self.service]

    @property
    def public_nominatim(self) -> bool:
        return self.service == NOMINATIM and self.url.rstrip("/") == PUBLIC_NOMINATIM


def url_problem(url: str) -> str:
    """Why ``url`` cannot be a geocoder's base address, or ``""`` when it can."""
    url = (url or "").strip()
    if not url:
        return str(_("Give the service's address."))
    parts = urlsplit(url)
    if len(url) > MAX_URL or parts.scheme not in ("https", "http") or not parts.hostname:
        return str(_("Use the service's web address, starting with https://."))
    if parts.query or parts.fragment:
        return str(_("Give the service's base address, without a question mark or a #."))
    return ""


def _value(field: str, setting: str) -> str:
    from postulo.core import site

    if site.overridden_by(field):
        return str(getattr(settings, setting, "") or "").strip()
    stored = getattr(site.current(), field, "") or ""
    if stored:
        return str(stored).strip()
    return str(getattr(settings, setting, "") or "").strip()


def chosen() -> tuple[str, str]:
    """The service and address configured, whether or not look-ups may run: for the page."""
    service = _value("geocoder", "POSTULO_GEOCODER").lower() or OFF
    if service not in SERVICES:
        return OFF, ""
    url = _value("geocoder_url", "POSTULO_GEOCODER_URL") or DEFAULT_URLS.get(service, "")
    return service, url


def current() -> Geocoder | None:
    """The service look-ups go to, or nothing when they are off, unconsented or stopped.

    The environment's choice is the operator's word; the page's needs the consent box. A
    refusal recorded by a look-up (`Refused`) stops them all until the setting is saved.
    """
    from postulo.core import site

    service, url = chosen()
    if service == OFF or url_problem(url):
        return None
    row = site.current()
    if not site.overridden_by("geocoder") and not row.geocoder_consent:
        return None
    if row.geocoder_problem:
        return None
    return Geocoder(service=service, url=url.rstrip("/"))


# ------------------------------------------------------------------------- which addresses


def eligible(address) -> bool:
    """Whether ``address`` may be looked up at all: a street, a pin of its words, a map.

    A company's address always; a person's own or a contact's only where it is drawn.
    """
    from . import mapping, placing

    if not address.street or address.placed_by in placing.KEPT:
        return False
    owner = getattr(address, "owner", None)
    if owner is None or not mapping.map_offered(owner):
        return False
    return address.content_type.model == "company" or address.on_map


def pending(address) -> bool:
    """Whether ``address`` is waiting for a look-up that has not happened yet."""
    return current() is not None and eligible(address) and address.street_from != address.comparable


def due(now=None):
    """Addresses whose street has not been looked up as they now read, oldest attempt first."""
    from django.contrib.contenttypes.models import ContentType

    from postulo.core.models import PostalAddress

    from . import placing
    from .models import Company

    now = now or timezone.now()
    companies = ContentType.objects.get_for_model(Company)
    return (
        PostalAddress.objects.exclude(street="")
        .exclude(placed_by__in=placing.KEPT)
        .exclude(street_from=F("comparable"))
        .filter(Q(content_type=companies) | Q(on_map=True))
        .filter(Q(street_tried_at__isnull=True) | Q(street_tried_at__lt=now - RETRY))
        .order_by(F("street_tried_at").asc(nulls_first=True), "pk")
    )


# ---------------------------------------------------------------------------- the request


def _query(geocoder: Geocoder, address) -> str:
    """The request's address: the service's own search, with the address's parts."""
    country = (address.country or "").lower()
    if geocoder.service == NOMINATIM:
        params = {
            "street": address.street,
            "postalcode": address.postcode,
            "city": address.municipality,
            "state": address.region,
            "countrycodes": country,
            "format": "jsonv2",
            "limit": 1,
        }
        path = "/search"
    elif geocoder.service == PHOTON:
        params = {
            "street": address.street,
            "postcode": address.postcode,
            "city": address.municipality,
            "state": address.region,
            "countrycode": country,
            "limit": 1,
        }
        path = "/structured"
    else:
        words = " ".join(p for p in (address.street, address.postcode, address.municipality) if p)
        params = {"q": words, "postcode": address.postcode, "limit": 1}
        path = "/search/"
    kept = {key: value for key, value in params.items() if value not in ("", None)}
    return f"{geocoder.url}{path}?{urlencode(kept)}"


def _point(geocoder: Geocoder, answer) -> tuple[float, float] | None:
    """The street-level point an answer gives, or nothing when it has none that precise."""
    try:
        if geocoder.service == NOMINATIM:
            if not answer:
                return None
            first = answer[0]
            if int(first.get("place_rank") or 0) < STREET_RANK:
                return None
            return float(first["lat"]), float(first["lon"])
        features = answer.get("features") or []
        if not features:
            return None
        first = features[0]
        kind = (first.get("properties") or {}).get("type", "")
        streets = ("house", "street") if geocoder.service == PHOTON else ("housenumber", "street")
        if kind not in streets:
            return None
        lon, lat = first["geometry"]["coordinates"][:2]
        return float(lat), float(lon)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def look_up(geocoder: Geocoder, address) -> tuple[float, float] | None:
    """Ask ``geocoder`` where ``address`` is. A point, nothing, or `Unavailable`/`Refused`."""
    deadline = http.deadline_in(TOTAL)
    url = _query(geocoder, address)
    headers = {"Accept": "application/json", "Accept-Encoding": http.ACCEPT_ENCODING}
    try:
        with (
            http.client(timeout=TIMEOUT, total=TOTAL, follow_redirects=False) as client,
            http.until(deadline),
            client.stream("GET", url, headers=headers) as response,
        ):
            status = response.status_code
            if status in (401, 403):
                raise Refused(
                    _("%(service)s refused the look-ups (%(status)s). They are stopped.")
                    % {"service": geocoder.name, "status": status}
                )
            if status == 429 or status >= 500:
                raise Unavailable(f"{geocoder.name} answered {status}")
            if status != 200:
                return None
            body = http.read_body(response, limit=MAX_BYTES, deadline=deadline)
    except (Refused, Unavailable):
        raise
    except http.DestinationRefused as error:
        raise Unavailable(str(error)) from error
    except (http.BodyRefused, httpx.HTTPError) as error:
        raise Unavailable(f"{type(error).__name__}: {error}") from error
    try:
        answer = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return _point(geocoder, answer)


# ---------------------------------------------------------------------------- the pass


def place_street(geocoder: Geocoder, address) -> bool:
    """Look ``address`` up and record the outcome on it. True when it was pinned at its street.

    `Refused` is left to the caller, which stops the pass and every pass after it.
    """
    now = timezone.now()
    try:
        point = look_up(geocoder, address)
    except Unavailable as why:
        logger.info("Street look-up for address %s failed: %s", address.pk, why)
        address.street_tried_at = now
        address.save(update_fields=["street_tried_at"])
        return False
    address.street_from = address.comparable
    address.street_tried_at = None
    fields = ["street_from", "street_tried_at"]
    if point is not None:
        address.latitude, address.longitude = point
        address.placed_by = address.Placed.STREET
        address.placed_from = address.place_key()
        address.placed_at = now
        fields += ["latitude", "longitude", "placed_by", "placed_from", "placed_at"]
    address.save(update_fields=fields)
    return point is not None


def run(*, budget: float = BUDGET) -> tuple[int, int]:
    """Look up the streets that are due, one a second, for at most ``budget`` seconds.

    Returns (pinned, not pinned). Called by the scheduler on every pass.
    """
    from postulo.core import site

    geocoder = current()
    if geocoder is None:
        return 0, 0
    started = time.monotonic()
    last = None
    pinned = missed = 0
    for address in due().iterator():
        if time.monotonic() - started >= budget:
            break
        if not eligible(address):
            # Its owner has the map off: moved to the back of the queue rather than read
            # first on every pass, so it cannot stand in front of the rest.
            address.street_tried_at = timezone.now()
            address.save(update_fields=["street_tried_at"])
            continue
        if last is not None and (wait := SPACING - (time.monotonic() - last)) > 0:
            time.sleep(wait)
        last = time.monotonic()
        try:
            found = place_street(geocoder, address)
        except Refused as refused:
            # On the one policy row, made if nobody has saved it yet: a service set only in
            # the environment can refuse too.
            from postulo.core.models import SiteSettings

            SiteSettings.objects.update_or_create(
                pk=1, defaults={"geocoder_problem": str(refused)[:300]}
            )
            site.forget_current()
            logger.warning("%s", refused)
            break
        if found:
            pinned += 1
        else:
            missed += 1
    return pinned, missed
