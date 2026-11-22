"""Arbetsförmedlingen's JobSearch, the job service Postulo ships a search for (#1253).

The answers below were recorded from jobsearch.api.jobtechdev.se on 17 November 2026 and cut
down to what is read. The adverts are under CC0; the employers named are companies, and the
one description kept is its first sentence, with nobody's name, e-mail address or telephone
number in it. Nothing here asks the network: the request is the real guarded client over a
transport that never leaves the machine.
"""

from __future__ import annotations

import copy
import datetime as dt
import ipaddress
import json

import httpx
import pytest
from django.core.cache import caches
from django.urls import reverse

from postulo.core.memo import forget_decisions
from postulo.jobs import listing_search
from postulo.jobs.models import Capture
from postulo.plugins import http, registry
from postulo.plugins.listing_search import sweden
from postulo.plugins.models import PluginPolicy
from postulo.plugins.testing import assert_is_a_listing_search

PUBLIC = [ipaddress.ip_address("93.184.216.34")]


def _hit(number, headline, published, closes, employer, workplace, kinds):
    hours, duration, employment, model = kinds
    return {
        "id": number,
        "headline": headline,
        "webpage_url": f"https://arbetsformedlingen.se/platsbanken/annonser/{number}",
        "removed": False,
        "publication_date": published,
        "application_deadline": closes,
        "employer": {"name": employer, "workplace": workplace},
        "workplace_address": {
            "municipality": "Göteborg",
            "region": "Västra Götalands län",
            "country": "Sverige",
        },
        "working_hours_type": {"concept_id": hours},
        "duration": {"concept_id": duration},
        "employment_type": {"concept_id": employment},
        "workplace_model": {"concept_id": model},
    }


#: `GET /search?q=utvecklare+Göteborg&published-after=20160&limit=4`, with the field mask.
SEARCHED = {
    "total": {"value": 61},
    "hits": [
        _hit(
            "31683912",
            "BI utvecklare till växande koncern i Göteborg",
            "2026-11-04T11:41:37",
            "2026-12-04T23:59:59",
            "TNG Group AB",
            "Stampgatan 14",
            ("6YE1_gAC_R2G", "a7uU_j21_mkL", "PFZr_Syz_cUq", None),
        ),
        _hit(
            "31738004",
            "Senior Cyber Security Advisor till NTT Security i Göteborg",
            "2026-11-16T16:41:17",
            "2026-12-02T23:59:59",
            "Maxitech AB",
            "NTT security",
            ("6YE1_gAC_R2G", "a7uU_j21_mkL", "PFZr_Syz_cUq", None),
        ),
        _hit(
            "31737094",
            "Senior ServiceNow-utvecklare HRSD (Göteborg)",
            "2026-11-16T15:16:12",
            "2026-11-26T23:59:59",
            "Soros Consulting AB",
            "Soros Consulting AB",
            ("6YE1_gAC_R2G", "a7uU_j21_mkL", "kpPX_CNN_gDU", "ZxwM_gtz_n61"),
        ),
    ],
}

#: `GET /ad/31737094`, cut to what is read; its description to its first sentence.
ADVERT = {
    **copy.deepcopy(SEARCHED["hits"][2]),
    "description": {
        "text": (
            "Soros Consulting AB söker en senior systemutvecklare inom ServiceNow med inriktning "
            "mot HR Service Delivery (HRSD) till ett konsultuppdrag i Göteborg."
        )
    },
    "application_contacts": [],
}


@pytest.fixture
def jobtech(monkeypatch):
    """JobSearch over a mock transport, answering the recorded search and the one advert."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/search":
            return httpx.Response(200, content=json.dumps(SEARCHED).encode())
        if request.url.path == "/ad/31737094":
            return httpx.Response(200, content=json.dumps(ADVERT).encode())
        return httpx.Response(404, content=b'{"message": "Ad not found", "status": 404}')

    monkeypatch.setattr(http, "public_addresses_for", lambda url: PUBLIC)
    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


@pytest.fixture
def switched_on(db):
    PluginPolicy.objects.filter(plugin=listing_search.FEATURE, person=None).delete()
    forget_decisions()
    caches["ratelimit"].clear()
    yield
    caches["ratelimit"].clear()


def test_it_holds_to_the_contract():
    hits = assert_is_a_listing_search(sweden.SWEDEN, results=SEARCHED, advert=("31737094", ADVERT))

    assert [hit.key for hit in hits] == ["31683912", "31738004", "31737094"]


def test_it_is_shipped_and_offered_once_the_feature_is_on(user, switched_on):
    assert "se-arbetsformedlingen" in listing_search.searches()
    assert [search.key for search in listing_search.offered_to(user)] == ["se-arbetsformedlingen"]
    assert registry.sub_plugins(listing_search.FEATURE)[0].name == (
        "listing-search:se-arbetsformedlingen"
    )


def test_a_search_sends_the_words_the_place_and_the_filters_and_nothing_else(jobtech):
    listing_search.search(
        sweden.SWEDEN,
        listing_search.Criteria(
            words="utvecklare", place="Göteborg", remote=True, since_days=14, page=2
        ),
    )

    (request,) = jobtech
    assert request.url.host == "jobsearch.api.jobtechdev.se" and request.url.path == "/search"
    assert dict(request.url.params) == {
        "q": "utvecklare Göteborg",
        "remote": "true",
        "published-after": "20160",
        "offset": "20",
        "limit": "20",
    }
    assert request.headers["X-Fields"] == sweden.FIELDS
    assert "Cookie" not in request.headers and "api-key" not in request.headers


def test_a_search_reads_each_advert_by_concept_and_never_by_label(jobtech):
    found = listing_search.search(sweden.SWEDEN, listing_search.Criteria(words="utvecklare"))

    assert found.total == 61 and found.more
    first, _second, third = (hit.posting for hit in found.hits)
    assert first.title == "BI utvecklare till växande koncern i Göteborg"
    assert first.company_name == "TNG Group AB"
    assert first.location == "Göteborg, Västra Götalands län"
    assert first.employment_type == "full_time" and first.remote_type == ""
    assert first.posted_at == dt.date(2026, 11, 4) and first.closes_at == dt.date(2026, 12, 4)
    assert first.url == "https://arbetsformedlingen.se/platsbanken/annonser/31683912"
    assert first.source == "Arbetsförmedlingen"
    assert first.salary_min is None, "JobSearch states no amounts"
    assert third.remote_type == "onsite"


def test_a_fixed_term_is_a_contract_and_hybrid_and_remote_are_read():
    ad = copy.deepcopy(ADVERT)
    ad["duration"] = {"concept_id": "gJRb_akA_95y"}  # 6 månader – upp till 12 månader
    ad["workplace_model"] = {"concept_id": "Vbpe_rGe_vCW"}
    assert sweden.posting(ad).employment_type == "contract"
    assert sweden.posting(ad).remote_type == "hybrid"

    ad = copy.deepcopy(ADVERT)
    ad["working_hours_type"] = {"concept_id": "947z_JGS_Uk2"}
    ad["workplace_model"] = {"concept_id": "L4T8_Z9y_DkN"}
    assert sweden.posting(ad).employment_type == "part_time"
    assert sweden.posting(ad).remote_type == "remote"


def test_an_advert_abroad_names_its_country_and_a_removed_one_is_left_out():
    ad = copy.deepcopy(ADVERT)
    ad["workplace_address"] = {"municipality": "Oslo", "region": "", "country": "Norge"}
    assert sweden.posting(ad).location == "Oslo, Norge"

    ad["removed"] = True
    assert sweden.posting(ad) is None


def test_the_contacts_in_an_advert_are_never_read():
    ad = copy.deepcopy(ADVERT)
    ad["application_contacts"] = [
        {"name": "Kim Exempel", "email": "kim@example.org", "telephone": "+46 70 000 00 00"}
    ]
    ad["employer"]["email"] = "jobb@example.org"
    ad["employer"]["phone_number"] = "+46 31 000 00 00"

    shown = sweden.posting(ad).model_dump_json()

    assert "Kim" not in shown and "example.org" not in shown and "000 00" not in shown


def test_the_last_page_the_service_answers_is_the_last_offered():
    late = listing_search.Criteria(words="x", page=101)

    assert sweden.request(late)[1]["offset"] == sweden.LAST_OFFSET
    page = {"total": {"value": 5000}, "hits": [SEARCHED["hits"][0]] * 20}
    assert sweden.read(page, listing_search.Criteria(page=100)).more, "offset 2000 is answered"
    found = sweden.read(page, listing_search.Criteria(page=101))
    assert not found.more, "the next offset would be past 2000, which the service refuses"


def test_review_makes_a_capture_from_the_advert_asked_again(client, user, jobtech, switched_on):
    client.force_login(user)

    response = client.post(
        reverse("listings:search"),
        {"service": "se-arbetsformedlingen", "advert": "31737094", "review": "1"},
    )

    capture = Capture.objects.get()
    assert response["Location"] == reverse("jobs:capture_review", args=[capture.pk])
    assert jobtech[-1].url.path == "/ad/31737094"
    data = capture.posting_data
    assert data.title == "Senior ServiceNow-utvecklare HRSD (Göteborg)"
    assert data.description.startswith("Soros Consulting AB söker")
    assert data.source == "Arbetsförmedlingen" and capture.source_name == "se-arbetsformedlingen"


def test_an_advert_the_service_no_longer_has_is_said(jobtech):
    with pytest.raises(listing_search.SearchRefused, match="no longer has"):
        listing_search.advert(sweden.SWEDEN, "1")


def test_the_page_names_the_host_what_is_sent_and_the_licence(client, user, switched_on):
    client.force_login(user)

    html = client.get(reverse("listings:search")).content.decode()

    assert "jobsearch.api.jobtechdev.se" in html
    assert "the words, the place and the filters you search for" in html
    assert "CC0 1.0" in html and "creativecommons.org/publicdomain/zero/1.0/" in html


def test_the_host_is_the_only_one_it_dials():
    assert sweden.SWEDEN.hosts == ("jobsearch.api.jobtechdev.se",)
