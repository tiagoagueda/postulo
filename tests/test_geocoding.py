"""Pinning an address at its street, with the geocoding service the operator chose (#913)."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.urls import reverse
from django.utils import timezone

from postulo.core import site
from postulo.core.models import PostalAddress, SiteSettings
from postulo.jobs import geocoding, placing
from postulo.jobs.models import Company, Contact

STREET_HIT = [{"lat": "38.7139", "lon": "-9.1394", "place_rank": 30}]
TOWN_ONLY = [{"lat": "38.72", "lon": "-9.14", "place_rank": 16}]


def switch_on(service=geocoding.NOMINATIM, url="", consent=True, **more):
    SiteSettings.objects.update_or_create(
        pk=1,
        defaults={"geocoder": service, "geocoder_url": url, "geocoder_consent": consent, **more},
    )
    site.forget_current()


def a_company(user):
    return Company.objects.create(owner=user, name=f"Acme {Company.objects.count() + 1}")


def an_address(user, holder=None, **parts):
    holder = holder or a_company(user)
    values = {
        "street": "Rua Augusta 1",
        "postcode": "1100-048",
        "municipality": "Lisboa",
        "country": "PT",
        **parts,
    }
    return PostalAddress.objects.create(owner=user, holder=holder, **values)


class Service:
    """A geocoder that answers what it is told and remembers what it was asked."""

    def __init__(self, answer=STREET_HIT, status=200):
        self.answer, self.status, self.asked = answer, status, []

    def __call__(self, **kwargs):
        def handler(request):
            self.asked.append(request.url)
            body = json.dumps(self.answer).encode() if self.answer is not None else b""
            return httpx.Response(self.status, content=body)

        kwargs.pop("total", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)


@pytest.fixture
def service(monkeypatch):
    answering = Service()
    monkeypatch.setattr(geocoding.http, "client", answering)
    clock(monkeypatch)
    return answering


def clock(monkeypatch, monotonic=time.monotonic):
    """A clock for the pass alone, which waits for nobody: what it was asked to wait, listed."""
    waits = []
    monkeypatch.setattr(geocoding, "time", SimpleNamespace(monotonic=monotonic, sleep=waits.append))
    return waits


# ------------------------------------------------------------------ off unless switched on


def test_look_ups_are_off_by_default_and_send_nothing(user, service):
    an_address(user)

    assert geocoding.current() is None
    assert geocoding.run() == (0, 0)
    assert service.asked == []


def test_the_page_setting_needs_the_operators_word(user, service):
    switch_on(consent=False)
    an_address(user)

    assert geocoding.current() is None
    assert geocoding.run() == (0, 0)
    assert service.asked == []


def test_set_in_the_environment_the_choice_is_the_operators_word(user, service, settings):
    settings.POSTULO_GEOCODER = "nominatim"
    with mock.patch.object(site, "overridden_by", lambda field: field == "geocoder"):
        in_force = geocoding.current()

    assert in_force is not None and in_force.public_nominatim
    assert in_force.url == geocoding.PUBLIC_NOMINATIM


# -------------------------------------------------------------------------- a look-up


def test_a_company_address_is_pinned_at_its_street_and_not_asked_again(user, service):
    switch_on()
    address = an_address(user)

    assert geocoding.run() == (1, 0)
    address.refresh_from_db()
    assert address.placed_by == PostalAddress.Placed.STREET
    assert (address.latitude, address.longitude) == (38.7139, -9.1394)
    assert address.street_from == address.comparable

    sent = service.asked[0]
    assert sent.host == "nominatim.openstreetmap.org" and sent.path == "/search"
    assert sent.params["street"] == "Rua Augusta 1" and sent.params["countrycodes"] == "pt"
    assert "Acme" not in str(sent), "never whose address it is"

    assert geocoding.run() == (0, 0), "an address that did not change is not asked again"
    assert len(service.asked) == 1


def test_an_answer_only_as_precise_as_a_town_leaves_the_offline_point(user, service):
    switch_on()
    service.answer = TOWN_ONLY
    address = an_address(user)
    before = (address.latitude, address.longitude, address.placed_by)

    assert geocoding.run() == (0, 1)
    address.refresh_from_db()
    assert (address.latitude, address.longitude, address.placed_by) == before
    assert geocoding.run() == (0, 0), "and is not asked again until the address changes"


def test_a_busy_service_is_tried_again_after_a_while(user, service):
    switch_on()
    service.status, service.answer = 429, None
    address = an_address(user)

    assert geocoding.run() == (0, 1)
    address.refresh_from_db()
    assert address.street_tried_at is not None and address.street_from == ""
    assert geocoding.run() == (0, 0), "not on the very next pass"

    address.street_tried_at = timezone.now() - geocoding.RETRY * 2
    address.save(update_fields=["street_tried_at"])
    service.status, service.answer = 200, STREET_HIT
    assert geocoding.run() == (1, 0)


def test_a_refusal_stops_every_look_up_until_the_setting_is_saved(user, service, client):
    switch_on()
    service.status, service.answer = 403, None
    an_address(user)
    an_address(user, street="Rua do Ouro 2")

    assert geocoding.run() == (0, 0)
    assert len(service.asked) == 1, "the second is not sent"
    assert "refused" in site.current().geocoder_problem
    assert geocoding.current() is None


def test_the_pass_waits_a_second_between_two_look_ups(user, service, monkeypatch):
    switch_on()
    waits = clock(monkeypatch)
    an_address(user)
    an_address(user, street="Rua do Ouro 2")

    assert geocoding.run() == (2, 0)
    assert len(waits) == 1 and 0 < waits[0] <= geocoding.SPACING


def test_the_pass_stops_at_its_budget(user, service, monkeypatch):
    switch_on()
    an_address(user)
    an_address(user, street="Rua do Ouro 2")
    ticks = iter([0.0, 0.0, 0.0, 99.0, 99.0, 99.0])
    clock(monkeypatch, monotonic=lambda: next(ticks))

    assert geocoding.run(budget=60) == (1, 0), "the second waits for the next pass"


# ------------------------------------------------------------------- whose addresses


def test_a_contacts_address_is_looked_up_only_when_shown_on_the_map(user, service):
    switch_on()
    contact = Contact.objects.create(owner=user, company=a_company(user), name="Ana Silva")
    hidden = an_address(user, holder=contact)

    assert geocoding.run() == (0, 0)
    assert service.asked == []

    hidden.on_map = True
    hidden.save()
    assert geocoding.run() == (1, 0)


def test_an_address_with_no_street_is_never_sent(user, service):
    switch_on()
    an_address(user, street="")

    assert geocoding.run() == (0, 0)
    assert service.asked == []


# ------------------------------------------------------------- a person's pin wins


def test_a_point_placed_by_hand_is_never_looked_up_or_overwritten(user, service):
    switch_on()
    address = an_address(user)
    placing.place_by_hand(address, 1.0, 2.0)

    assert geocoding.run() == (0, 0)
    address.refresh_from_db()
    assert (address.latitude, address.longitude, address.placed_by) == (1.0, 2.0, "manual")


def test_taking_it_off_looks_the_street_up_again(user, service):
    switch_on()
    address = an_address(user)
    placing.place_by_hand(address, 1.0, 2.0)

    placing.take_off_by_hand(address)

    assert geocoding.run() == (1, 0)
    address.refresh_from_db()
    assert address.placed_by == PostalAddress.Placed.STREET


def test_a_changed_street_leaves_its_old_pin_at_once_and_is_looked_up_again(user, service):
    switch_on()
    address = an_address(user)
    geocoding.run()
    address.refresh_from_db()

    address.street = "Rua do Ouro 2"
    address.save()

    assert address.placed_by != PostalAddress.Placed.STREET, "not left at the old street"
    assert geocoding.run() == (1, 0)
    assert service.asked[-1].params["street"] == "Rua do Ouro 2"


# --------------------------------------------------------------------- the other services


def test_photon_is_asked_its_structured_question_and_understood(user, service):
    switch_on(geocoding.PHOTON, url="https://photon.example.org")
    service.answer = {
        "features": [
            {"properties": {"type": "house"}, "geometry": {"coordinates": [-9.1394, 38.7139]}}
        ]
    }
    address = an_address(user)

    assert geocoding.run() == (1, 0)
    assert service.asked[0].path == "/structured"
    address.refresh_from_db()
    assert (address.latitude, address.longitude) == (38.7139, -9.1394)


def test_addok_is_asked_and_a_town_answer_is_not_a_street(user, service):
    switch_on(geocoding.ADDOK, url="https://data.geopf.fr/geocodage")
    service.answer = {
        "features": [
            {"properties": {"type": "municipality"}, "geometry": {"coordinates": [2.3, 48.8]}}
        ]
    }
    an_address(user, country="FR", postcode="75001", municipality="Paris", street="1 rue X")

    assert geocoding.run() == (0, 1)
    assert service.asked[0].path == "/geocodage/search/"


def test_a_service_address_must_be_a_web_address():
    assert geocoding.url_problem("https://photon.example.org") == ""
    assert geocoding.url_problem("ftp://x") and geocoding.url_problem("")
    assert geocoding.url_problem("https://n.example.org/search?q=1")


@pytest.mark.parametrize(
    ("service", "url", "named"),
    [("google", "", "postulo.E043"), ("photon", "ftp://photon.example.org", "postulo.E044")],
)
def test_a_wrong_setting_in_the_environment_is_named_by_the_check(settings, service, url, named):
    settings.POSTULO_GEOCODER, settings.POSTULO_GEOCODER_URL = service, url

    with pytest.raises(SystemCheckError, match=named):
        call_command("check", tags=["postulo"])


# ------------------------------------------------------------------- what is said where


def test_the_line_under_an_address_names_the_service_before_its_street_is_sent(user, client):
    switch_on()
    address = an_address(user)
    client.force_login(user)

    page = client.get(reverse("jobs:company_update", args=[address.object_id]))

    assert "Its street will be looked up with Nominatim shortly." in page.content.decode()


def test_the_record_of_processing_lists_the_service(user):
    from postulo.core import gdpr

    assert gdpr.record_of_processing()["geocoder"] is None
    switch_on()
    assert gdpr.record_of_processing()["geocoder"] == {
        "name": "Nominatim",
        "url": geocoding.PUBLIC_NOMINATIM,
    }


def test_the_settings_form_refuses_look_ups_without_the_operators_word(db):
    from postulo.core.server_forms import MapsForm

    form = MapsForm(
        data={"map_tiles": "osm", "geocoder": "nominatim", "geocoder_url": ""},
        instance=site.current(),
    )
    assert not form.is_valid()
    assert "geocoder_consent" in form.errors

    form = MapsForm(
        data={
            "map_tiles": "osm",
            "geocoder": "photon",
            "geocoder_url": "",
            "geocoder_consent": "on",
        },
        instance=site.current(),
    )
    assert not form.is_valid()
    assert "geocoder_url" in form.errors, "Photon has no public default"


def test_saving_the_setting_lifts_a_refusal(db):
    from postulo.core.server_forms import MapsForm

    switch_on(geocoder_problem="Nominatim refused the look-ups (403). They are stopped.")
    form = MapsForm(
        data={"map_tiles": "osm", "geocoder": "nominatim", "geocoder_consent": "on"},
        instance=site.current(),
    )
    assert form.is_valid(), form.errors
    form.save()
    site.forget_current()

    assert site.current().geocoder_problem == ""
    assert geocoding.current() is not None
