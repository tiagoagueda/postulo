"""Listing search: a job service searched by criteria, on a press, and an advert reviewed (#1253).

The service here is a fake one, declared as a package for another country would declare its
own, answered through the real guarded client over a transport that never leaves the machine.
The service Postulo ships has its own file, with answers recorded from it.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from django.core.cache import caches
from django.urls import reverse

from postulo.core import gdpr
from postulo.core.memo import forget_decisions
from postulo.jobs import listing_search
from postulo.jobs.models import Capture, CaptureStatus, Company, JobPosting
from postulo.plugins import http, registry
from postulo.plugins.base import JobPostingData
from postulo.plugins.models import PluginPolicy
from postulo.plugins.testing import assert_is_a_listing_search

PUBLIC = [ipaddress.ip_address("93.184.216.34")]
ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- a service to search


def _posting(item) -> JobPostingData:
    return JobPostingData(
        title=item["title"],
        company_name=item.get("employer", ""),
        location=item.get("town", ""),
        remote_type="remote" if item.get("remote") else "",
        description=item.get("text", ""),
        url=f"https://jobs.example/adverts/{item['id']}",
    )


def _read(document, criteria):
    hits = tuple(
        listing_search.Hit(key=str(item["id"]), posting=_posting(item))
        for item in document.get("items", [])
    )
    total = document.get("total")
    return listing_search.Results(
        hits=hits, total=total, more=criteria.offset + len(hits) < (total or 0)
    )


def _request(criteria):
    parameters = {"q": " ".join(part for part in (criteria.words, criteria.place) if part)}
    if criteria.remote:
        parameters["remote"] = True
    parameters["offset"] = criteria.offset
    parameters["limit"] = criteria.size
    return "https://jobs.example/search", parameters


FAKE = listing_search.ListingSearch(
    key="xx-jobs",
    label="Example Jobs",
    countries=("SE",),
    languages=("sv",),
    hosts=("jobs.example",),
    supports=frozenset({"words", "place", "remote"}),
    request=_request,
    read=_read,
    one=lambda key: (f"https://jobs.example/adverts/{key}", {}),
    read_one=lambda document, key: _posting(document) if str(document.get("id")) == key else None,
    source="Example Jobs",
    licence="CC0 1.0",
    licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
)


class ExampleSearches:
    name = "example-searches"
    kind = "listing-search"
    label = "Example searches"
    searches = (FAKE,)


ADVERT = {
    "id": 1001,
    "title": "Nurse, night shifts",
    "employer": "Example Hospital",
    "town": "Example Town",
    "text": "Nights, three a week.\r\n\r\n\r\nApply by the end of the month.",
}
FOUND = {
    "total": 41,
    "items": [
        ADVERT,
        {"id": 1002, "title": "Nurse‮ assistant\u0007", "employer": "Example Care"},
        {"id": "../1003", "title": "Not a number a service has", "employer": "x"},
    ],
}


@pytest.fixture
def service(monkeypatch):
    """The fake service installed, answering through the real guard over a mock transport.

    Answers are keyed on the path asked for; anything else is a 404. Postulo's own registry is
    taken out meanwhile, so the fake is the only service there is; the one Postulo ships has
    tests of its own.
    """
    from postulo.plugins.listing_search import ListingSearches

    registry.unregister_builtin("listing-search", ListingSearches)
    registry.register_builtin("listing-search", ExampleSearches)
    seen: list[httpx.Request] = []
    answers: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.get(request.url.path, httpx.Response(404))

    monkeypatch.setattr(http, "public_addresses_for", lambda url: PUBLIC)
    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    answers["/search"] = httpx.Response(200, content=json.dumps(FOUND).encode())
    answers["/adverts/1001"] = httpx.Response(200, content=json.dumps(ADVERT).encode())
    yield answers, seen
    registry.unregister_builtin("listing-search", ExampleSearches)
    registry.register_builtin("listing-search", ListingSearches)


@pytest.fixture
def switched_on(db):
    """Listing search on for the instance: the row that starts it off taken away, as the
    administrator's switch does."""
    PluginPolicy.objects.filter(plugin=listing_search.FEATURE, person=None).delete()
    forget_decisions()
    caches["ratelimit"].clear()
    yield
    caches["ratelimit"].clear()


def search_url():
    return reverse("listings:search")


def press_search(client, **fields):
    return client.post(search_url(), {"service": "xx-jobs", "search": "1", **fields})


# ---------------------------------------------------------------- the switch


def test_listing_search_is_off_by_default(client, user, service):
    from postulo.plugins.policy import decide

    _answers, seen = service
    client.force_login(user)

    assert not decide(listing_search.FEATURE, user).on
    assert "data-search-services" not in client.get(reverse("listings:list")).content.decode()
    assert "data-search-services" not in client.get(reverse("jobs:capture_create")).content.decode()
    assert client.get(search_url()).status_code == 404
    assert press_search(client, words="nurse").status_code == 404
    assert seen == []


def test_switched_on_the_listings_page_offers_it_and_the_page_asks_nothing(
    client, user, service, switched_on
):
    _answers, seen = service
    client.force_login(user)

    listings = client.get(reverse("listings:list")).content.decode()
    page = client.get(search_url()).content.decode()

    assert "data-search-services" in listings
    assert "Example Jobs" in page and "jobs.example" in page, "the host asked is named"
    assert "CC0 1.0" in page, "and the licence of its adverts"
    assert "data-search-results" not in page
    assert seen == [], "nothing is asked on a GET"


def test_a_service_switched_off_on_its_own_is_not_offered(client, user, service, switched_on):
    PluginPolicy.objects.create(
        plugin=listing_search.switch_of("xx-jobs"), person=None, state=PluginPolicy.State.FORCED_OFF
    )
    forget_decisions()
    client.force_login(user)

    assert listing_search.offered_to(user) == []
    assert client.get(search_url()).status_code == 404
    assert "data-search-services" not in client.get(reverse("listings:list")).content.decode()


def test_the_feature_is_a_module_with_a_switch_per_service(service, switched_on):
    entries = registry.sub_plugins(listing_search.FEATURE)

    assert [entry.name for entry in entries] == ["listing-search:xx-jobs"]
    assert "jobs.example" in entries[0].description and "CC0 1.0" in entries[0].description


def test_the_policy_hides_it_from_one_account(client, user, service, switched_on):
    PluginPolicy.objects.create(
        plugin=listing_search.FEATURE, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    client.force_login(user)

    assert client.get(search_url()).status_code == 404


# ---------------------------------------------------------------- searching


def test_a_search_asks_once_and_shows_the_hits_cleaned(client, user, service, switched_on):
    _answers, seen = service
    client.force_login(user)

    html = press_search(client, words="nurse", place="Example Town", remote="on").content.decode()

    (request,) = seen
    assert request.url.path == "/search"
    assert request.url.params["q"] == "nurse Example Town"
    assert request.url.params["remote"] == "true"
    assert request.headers["User-Agent"].startswith("Postulo/")
    assert "Cookie" not in request.headers and "Referer" not in request.headers
    assert "41 adverts found" in html
    assert 'data-search-hit="1001"' in html and 'data-search-hit="1002"' in html
    assert "Nurse assistant" in html, "direction and control characters taken out"
    assert "../1003" not in html, "a hit whose number could not be asked for again is left out"
    assert 'lang="sv"' in html
    assert not Capture.objects.exists(), "results are shown, never stored"


def test_what_was_typed_reaches_a_value_and_nothing_else(client, user, service, switched_on):
    _answers, seen = service
    client.force_login(user)

    press_search(client, words="a&offset=9999#x", place="../../admin?y=1")

    (request,) = seen
    assert request.url.host == "jobs.example" and request.url.path == "/search"
    assert request.url.params["q"] == "a&offset=9999#x ../../admin?y=1"
    assert request.url.params.get_list("offset") == ["0"]


def test_nothing_to_search_for_asks_nothing(client, user, service, switched_on):
    _answers, seen = service
    client.force_login(user)

    html = press_search(client).content.decode()

    assert "Say what to search for" in html
    assert seen == []


def test_the_next_page_is_another_press_and_the_search_is_not_in_the_address(
    client, user, service, switched_on
):
    _answers, seen = service
    client.force_login(user)

    html = press_search(client, words="nurse").content.decode()
    assert "data-next-page" in html
    press_search(client, words="nurse", page="2")

    assert [request.url.params["offset"] for request in seen] == ["0", "20"]
    assert all("nurse" not in str(request) for request in [search_url()])


def test_a_hit_already_held_is_marked(client, user, service, switched_on):
    company = Company.objects.create(owner=user, name="Example Hospital")
    JobPosting.objects.create(
        owner=user, company=company, title="Nurse", url="https://jobs.example/adverts/1001"
    )
    Capture.objects.create(
        owner=user, url="https://jobs.example/adverts/1002", data={"title": "Nurse assistant"}
    )
    client.force_login(user)

    html = press_search(client, words="nurse").content.decode()

    assert 'data-known="listing"' in html
    assert 'data-known="capture"' in html


def test_somebody_elses_listing_marks_nothing(client, user, other_user, service, switched_on):
    company = Company.objects.create(owner=other_user, name="Example Hospital")
    JobPosting.objects.create(
        owner=other_user, company=company, title="Nurse", url="https://jobs.example/adverts/1001"
    )
    client.force_login(user)

    html = press_search(client, words="nurse").content.decode()

    assert "data-known=" not in html


def test_a_criterion_the_service_does_not_honour_is_said(client, user, service, switched_on):
    client.force_login(user)
    honoured = listing_search.Criteria(words="nurse", since_days=7)

    assert listing_search.left_out(FAKE, honoured) == ["since"]
    assert listing_search.honoured(FAKE, honoured).since_days is None
    html = client.get(search_url()).content.decode()
    assert 'name="since"' not in html, "no offered service honours it, so it is not drawn"


def test_the_allowance_bounds_the_presses(client, user, service, switched_on, settings):
    settings.POSTULO_LISTING_SEARCH_RATE = "1/h"
    _answers, seen = service
    client.force_login(user)

    press_search(client, words="nurse")
    html = press_search(client, words="nurse").content.decode()

    assert len(seen) == 1, "the second press asked nothing"
    assert "too many requests" in html.lower()


def test_a_refusal_is_a_sentence_and_never_a_retry(client, user, service, switched_on):
    answers, seen = service
    answers["/search"] = httpx.Response(429)
    client.force_login(user)

    html = press_search(client, words="nurse").content.decode()

    assert "asked for fewer requests" in html
    assert len(seen) == 1


def test_a_redirect_is_not_followed(service, switched_on):
    answers, seen = service
    answers["/search"] = httpx.Response(302, headers={"Location": "https://elsewhere.example/"})

    with pytest.raises(listing_search.SearchRefused, match="somewhere else"):
        listing_search.search(FAKE, listing_search.Criteria(words="nurse"))
    assert len(seen) == 1


def test_an_answer_too_large_is_refused(service, switched_on):
    answers, _seen = service
    answers["/search"] = httpx.Response(200, content=b"{" + b" " * listing_search.MAX_BYTES + b"}")

    with pytest.raises(listing_search.SearchRefused, match="larger"):
        listing_search.search(FAKE, listing_search.Criteria(words="nurse"))


@pytest.mark.parametrize(
    "address",
    [
        "https://elsewhere.example/search",
        "http://jobs.example/search",
        "https://jobs.example:8443/search",
        "https://jobs.example/search?q=already",
        "https://user@jobs.example/search",
        "https://jobs.example/search#here",
        "https://jobs.example/sea rch",
    ],
)
def test_a_search_cannot_ask_outside_its_declaration(service, address):
    _answers, seen = service
    rogue = listing_search.ListingSearch(
        **{**FAKE.__dict__, "key": "xx-rogue", "request": lambda criteria: (address, {})}
    )

    with pytest.raises(listing_search.SearchRefused, match="not one it declared"):
        listing_search.search(rogue, listing_search.Criteria(words="nurse"))
    assert seen == []


def test_parameters_are_held_to_names_and_plain_values():
    for parameters in ({"q&x": "1"}, {"q": object()}, ["q", "nurse"], {"q": {"nested": 1}}):
        with pytest.raises(listing_search.SearchRefused):
            listing_search._pairs(parameters)
    assert listing_search._pairs({"a": ["x", "y"], "b": False, "c": 3}) == [
        ("a", "x"),
        ("a", "y"),
        ("b", "false"),
        ("c", "3"),
    ]


# ---------------------------------------------------------------- reviewing


def test_review_asks_again_and_makes_one_capture_for_the_person(client, user, service, switched_on):
    _answers, seen = service
    client.force_login(user)

    response = client.post(
        search_url(),
        {"service": "xx-jobs", "advert": "1001", "review": "1", "title": "Typed by the form"},
    )

    capture = Capture.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("jobs:capture_review", args=[capture.pk])
    assert seen[-1].url.path == "/adverts/1001", "the service is asked for that advert"
    assert capture.owner == user and capture.status == CaptureStatus.PENDING
    assert capture.origin == "search" and capture.source_name == "xx-jobs"
    assert capture.url == "https://jobs.example/adverts/1001"
    data = capture.posting_data
    assert data.title == "Nurse, night shifts", "from the service's answer, not the form"
    assert data.source == "Example Jobs", "the service's name becomes found via"
    assert data.description == "Nights, three a week.\n\nApply by the end of the month."
    html = client.get(reverse("jobs:capture_review", args=[capture.pk])).content.decode()
    assert "Nurse, night shifts" in html


@pytest.mark.parametrize("advert", ["../1001", "1001/../../admin", "", "a" * 81, "10%2F01"])
def test_review_of_a_number_no_service_could_have_named_asks_nothing(
    client, user, service, switched_on, advert
):
    _answers, seen = service
    client.force_login(user)

    client.post(search_url(), {"service": "xx-jobs", "advert": advert, "review": "1"})

    assert seen == [] and not Capture.objects.exists()


def test_review_of_a_service_not_offered_asks_nothing(client, user, service, switched_on):
    _answers, seen = service
    client.force_login(user)

    client.post(search_url(), {"service": "somewhere", "advert": "1001", "review": "1"})

    assert seen == [] and not Capture.objects.exists()


def test_review_of_an_advert_the_service_no_longer_has_is_said(client, user, service, switched_on):
    client.force_login(user)

    response = client.post(
        search_url(), {"service": "xx-jobs", "advert": "999", "review": "1"}, follow=True
    )

    assert "no longer has that advert" in response.content.decode()
    assert not Capture.objects.exists()


def test_another_account_never_sees_the_capture(client, user, other_user, service, switched_on):
    client.force_login(user)
    client.post(search_url(), {"service": "xx-jobs", "advert": "1001", "review": "1"})
    capture = Capture.objects.get()

    client.force_login(other_user)
    assert client.get(reverse("jobs:capture_review", args=[capture.pk])).status_code == 404


# ---------------------------------------------------------------- the open registry


def test_another_package_adds_a_service_and_cannot_take_one(service, monkeypatch, caplog):
    class Elsewhere:
        name = "elsewhere"
        kind = "listing-search"
        searches = (
            listing_search.ListingSearch(**{**FAKE.__dict__, "key": "xx-other"}),
            listing_search.ListingSearch(**{**FAKE.__dict__, "hosts": ("elsewhere.example",)}),
            listing_search.ListingSearch(**{**FAKE.__dict__, "key": "Bad Key"}),
            "not a search",
        )

    installed = registry.plugins("listing-search")
    monkeypatch.setattr(
        registry,
        "plugins",
        lambda kind, **kwargs: [Elsewhere(), *installed] if kind == "listing-search" else [],
    )

    with caplog.at_level(logging.WARNING):
        found = listing_search.searches()

    assert set(found) == {"xx-jobs", "xx-other"}
    assert found["xx-jobs"].hosts == ("jobs.example",), "a key a shipped registry holds stays"


def test_the_group_is_open_and_the_registry_is_nobodys_to_switch():
    from postulo.plugins.policy import UNGOVERNED_KINDS

    assert registry.GROUPS["listing-search"] == "postulo.listing_searches"
    assert "listing-search" in UNGOVERNED_KINDS


def test_the_contract_check_passes_a_good_search_and_fails_a_bad_one():
    recorded = {"total": 2, "items": FOUND["items"][:2]}
    hits = assert_is_a_listing_search(FAKE, results=recorded, advert=("1001", ADVERT))
    assert [hit.key for hit in hits] == ["1001", "1002"]
    assert hits[1].posting.title == "Nurse assistant", "cleaned as the page shows it"
    with pytest.raises(AssertionError, match="not a number"):
        assert_is_a_listing_search(FAKE, results=FOUND, advert=("1001", ADVERT))

    leaky = listing_search.ListingSearch(
        **{
            **FAKE.__dict__,
            "request": lambda criteria: (f"https://jobs.example/{criteria.words[:3]}", {}),
        }
    )
    with pytest.raises(AssertionError, match="address"):
        assert_is_a_listing_search(leaky, results=recorded, advert=("1001", ADVERT))
    unlicensed = listing_search.ListingSearch(**{**FAKE.__dict__, "licence": ""})
    with pytest.raises(AssertionError, match="licence"):
        assert_is_a_listing_search(unlicensed, results=recorded, advert=("1001", ADVERT))


def test_the_contract_imports_with_django_not_set_up():
    """A package installed beside Postulo is imported to check its entry points before
    Django is set up (#1299): the names it needs to declare a search must import there."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("DJANGO")}
    program = (
        "from postulo.plugins.api import (ListingSearch, ListingSearchPlugin, SearchCriteria,"
        " SearchHit, SearchResults, JobPostingData)\n"
        "ListingSearch(key='x', label='X', countries=(), languages=(), hosts=('x.example',),"
        " supports=frozenset({'words'}), request=None, read=None, one=None, read_one=None,"
        " source='X')\n"
        "SearchHit(key='1', posting=JobPostingData(title='A job'))\n"
        "print('fine')\n"
    )
    finished = subprocess.run(  # noqa: S603 - this interpreter, and a program written here
        [sys.executable, "-c", program],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert finished.stdout.strip() == "fine", finished.stderr[-2000:]


# ---------------------------------------------------------------- paperwork


def test_the_record_of_processing_lists_the_services_only_while_they_can_be_asked(service, db):
    assert gdpr.record_of_processing()["job_services"] == []

    PluginPolicy.objects.filter(plugin=listing_search.FEATURE, person=None).delete()
    forget_decisions()
    (row,) = gdpr.record_of_processing()["job_services"]
    assert row["key"] == "xx-jobs" and row["hosts"] == ["jobs.example"]
    assert row["licence"] == "CC0 1.0"
    assert "words, the place and the filters" in row["sends"]

    PluginPolicy.objects.create(
        plugin=listing_search.switch_of("xx-jobs"), person=None, state=PluginPolicy.State.FORCED_OFF
    )
    forget_decisions()
    assert gdpr.record_of_processing()["job_services"] == []


def test_on_for_one_account_the_record_lists_the_service(service, user, db):
    PluginPolicy.objects.create(
        plugin=listing_search.FEATURE, person=user, state=PluginPolicy.State.FORCED_ON
    )
    forget_decisions()

    assert [row["key"] for row in gdpr.record_of_processing()["job_services"]] == ["xx-jobs"]


def test_the_record_page_names_each_service(admin_client, service, switched_on):
    html = admin_client.get(reverse("server:record_of_processing")).content.decode()

    assert 'data-job-service="xx-jobs"' in html and "jobs.example" in html
