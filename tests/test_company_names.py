"""A company's name in other languages, from its Wikidata item or typed (#673).

The request is the real guarded client over a transport that never leaves the machine, as
the outbound tests do it, so what is held here is what the code would send and accept.
"""

from __future__ import annotations

import io
import ipaddress
import json
import zipfile

import httpx
import pytest
from django.urls import reverse

from postulo.core import export, importer
from postulo.jobs import merging, wikidata
from postulo.jobs.models import Company, CompanyIdentifier, CompanyName
from postulo.plugins import http

PUBLIC = [ipaddress.ip_address("185.15.59.224")]

AVEIRO = {
    "entities": {
        "Q29671": {
            "id": "Q29671",
            "labels": {
                "en": {"language": "en", "value": "University of Aveiro"},
                "pt": {"language": "pt", "value": "Universidade de Aveiro"},
                "fr": {"language": "fr", "value": "université d'Aveiro"},
                "xx-nonsense": {"language": "xx-nonsense", "value": "Nobody reads this"},
                "de": {"language": "de", "value": "Universität‮ Aveiro\u0007"},
                "mul": {"language": "mul", "value": "Universidade de Aveiro"},
            },
        }
    }
}


@pytest.fixture
def wikidata_answers(monkeypatch):
    """The real guard, resolving www.wikidata.org to a public address, over a mock transport."""
    seen: list[httpx.Request] = []
    answers: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.get(str(request.url.path), httpx.Response(404))

    def public_addresses_for(url: str):
        return PUBLIC

    monkeypatch.setattr(http, "public_addresses_for", public_addresses_for)
    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return answers, seen


def an_item(answers, qid="Q29671", body=AVEIRO, status=200):
    answers[f"/wiki/Special:EntityData/{qid}.json"] = httpx.Response(
        status, content=json.dumps(body).encode()
    )


def aperture(user, qid="Q29671") -> Company:
    company = Company.objects.create(owner=user, name="Aveiro Uni")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="wikidata", value=qid)
    return company


# ------------------------------------------------------------------------ the reader


def test_the_names_are_read_in_the_languages_postulo_speaks(wikidata_answers):
    answers, seen = wikidata_answers
    an_item(answers)

    item = wikidata.fetch("Q29671")

    assert item.names["en"] == "University of Aveiro"
    assert item.names["pt"] == "Universidade de Aveiro"
    assert "xx-nonsense" not in item.names
    (request,) = seen
    assert request.url.host == "www.wikidata.org"
    assert request.headers["User-Agent"].startswith("Postulo/")
    assert "httpx/" in request.headers["User-Agent"]


def test_a_label_is_cleaned_of_control_and_direction_characters(wikidata_answers):
    answers, _seen = wikidata_answers
    an_item(answers)

    name = wikidata.fetch("Q29671").names["de"]

    assert name == "Universität Aveiro"


def test_the_default_label_stands_in_for_a_language_with_none():
    names = wikidata.names_from({"mul": {"value": "Mozilla"}, "en": {"value": "Mozilla Corp"}})

    assert names["en"] == "Mozilla Corp"
    assert names["fr"] == "Mozilla"


@pytest.mark.parametrize(
    "qid, body, status, words",
    [
        ("Q1", {}, 404, "no item"),
        ("Q2", {"entities": {}}, 200, "not an item"),
        ("Q3", "not json", 200, "not an item"),
    ],
)
def test_an_answer_that_is_not_an_item_is_said(wikidata_answers, qid, body, status, words):
    answers, _seen = wikidata_answers
    if isinstance(body, str):
        answers[f"/wiki/Special:EntityData/{qid}.json"] = httpx.Response(status, content=b"<html>")
    else:
        an_item(answers, qid, body, status)

    with pytest.raises(wikidata.WikidataRefused, match=words):
        wikidata.fetch(qid)


def test_a_redirect_to_another_host_is_refused(wikidata_answers):
    answers, _seen = wikidata_answers
    answers["/wiki/Special:EntityData/Q5.json"] = httpx.Response(
        302, headers={"Location": "https://evil.example/item.json"}
    )

    with pytest.raises(wikidata.WikidataRefused, match="somewhere else"):
        wikidata.fetch("Q5")


def test_a_merged_item_is_followed_to_the_one_it_became(wikidata_answers):
    answers, _seen = wikidata_answers
    answers["/wiki/Special:EntityData/Q6.json"] = httpx.Response(
        301, headers={"Location": "/wiki/Special:EntityData/Q29671.json"}
    )
    an_item(answers)

    assert wikidata.fetch("Q6").qid == "Q29671"


def test_an_answer_too_large_is_refused(wikidata_answers):
    answers, _seen = wikidata_answers
    answers["/wiki/Special:EntityData/Q7.json"] = httpx.Response(
        200, content=b"{" + b" " * (wikidata.MAX_BYTES + 10) + b"}"
    )

    with pytest.raises(wikidata.WikidataRefused, match="larger"):
        wikidata.fetch("Q7")


def test_only_an_identifier_is_asked_for():
    with pytest.raises(wikidata.WikidataRefused):
        wikidata.fetch("https://evil.example/")


# ------------------------------------------------------------------------- the pages


def test_fetching_shows_what_came_back_and_keeps_nothing(client, user, wikidata_answers):
    answers, _seen = wikidata_answers
    an_item(answers)
    company = aperture(user)
    client.force_login(user)

    response = client.post(reverse("jobs:company_wikidata", args=[company.pk]))
    preview = client.get(reverse("jobs:company_wikidata", args=[company.pk]))

    assert response.status_code == 302
    assert "Universidade de Aveiro" in preview.content.decode()
    assert not company.names.exists(), "nothing is kept before the person says so"


def test_keeping_stores_each_name_in_its_language(client, user, wikidata_answers):
    answers, _seen = wikidata_answers
    an_item(answers)
    company = aperture(user)
    client.force_login(user)
    client.post(reverse("jobs:company_wikidata", args=[company.pk]))

    client.post(reverse("jobs:company_wikidata", args=[company.pk]), {"keep": "1"})

    kept = {row.language: row for row in company.names.all()}
    assert kept["pt"].name == "Universidade de Aveiro"
    assert kept["pt"].source == "wikidata" and kept["pt"].fetched_at is not None


def test_nothing_is_fetched_by_a_page_load_or_a_save(client, user, wikidata_answers):
    answers, seen = wikidata_answers
    an_item(answers)
    company = aperture(user)
    client.force_login(user)

    client.get(reverse("jobs:company_detail", args=[company.pk]))
    company.save()

    assert seen == []


def test_a_typed_name_is_kept_where_wikidata_gave_none(client, user):
    company = Company.objects.create(owner=user, name="Aveiro Uni")
    client.force_login(user)

    client.post(
        reverse("jobs:company_names", args=[company.pk]),
        {"language": "fr-FR", "name": "Université d'Aveiro"},
    )

    (row,) = company.names.all()
    assert (row.language, row.source) == ("fr-FR", "typed")


def test_a_name_wikidata_gave_cannot_be_typed_over(client, user):
    company = Company.objects.create(owner=user, name="Aveiro Uni")
    CompanyName.objects.create(
        owner=user, company=company, language="pt", name="Universidade de Aveiro", source="wikidata"
    )
    client.force_login(user)

    client.post(reverse("jobs:company_names", args=[company.pk]), {"language": "pt", "name": "X"})

    assert company.names.get().name == "Universidade de Aveiro"


def test_off_nothing_is_offered_and_nothing_fetched(client, user, wikidata_answers):
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.policy import forget_decisions
    from postulo.plugins.wikidata import WIKIDATA

    company = aperture(user)
    PluginPolicy.objects.create(plugin=WIKIDATA, person=user, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert "Fetch from Wikidata" not in page
    assert client.post(reverse("jobs:company_wikidata", args=[company.pk])).status_code == 404


def test_another_accounts_company_is_not_found(client, user, other_user):
    theirs = aperture(other_user)
    client.force_login(user)

    assert client.post(reverse("jobs:company_wikidata", args=[theirs.pk])).status_code == 404
    response = client.post(
        reverse("jobs:company_names", args=[theirs.pk]), {"language": "pt", "name": "X"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------- where a name is used


def test_name_in_falls_back_to_the_base_language_then_the_name(user):
    company = Company.objects.create(owner=user, name="Aveiro Uni")
    CompanyName.objects.create(
        owner=user, company=company, language="pt", name="Universidade de Aveiro", source="typed"
    )

    assert company.name_in("pt-BR") == "Universidade de Aveiro"
    assert company.name_in("de") == "Aveiro Uni"


def test_a_company_is_found_by_any_of_its_names(user):
    from postulo.core.search import company_match

    company = Company.objects.create(owner=user, name="Aveiro Uni")
    CompanyName.objects.create(
        owner=user, company=company, language="fr", name="Université d'Aveiro", source="typed"
    )

    found = Company.objects.for_user(user).with_notes(user).filter(company_match("Université"))
    assert found.get() == company


def test_a_merge_moves_the_names_the_kept_one_has_not(user):
    kept = Company.objects.create(owner=user, name="Aveiro Uni")
    other = Company.objects.create(owner=user, name="UA")
    CompanyName.objects.create(owner=user, company=kept, language="pt", name="Ours", source="typed")
    CompanyName.objects.create(
        owner=user, company=other, language="pt", name="Theirs", source="typed"
    )
    CompanyName.objects.create(
        owner=user, company=other, language="fr", name="UA fr", source="typed"
    )

    merging.merge_companies(kept, other)

    assert {row.language: row.name for row in kept.names.all()} == {"pt": "Ours", "fr": "UA fr"}


def test_the_archive_carries_the_names(user, other_user):
    company = Company.objects.create(owner=user, name="Aveiro Uni")
    CompanyName.objects.create(
        owner=user, company=company, language="pt", name="Universidade de Aveiro", source="wikidata"
    )

    data = export.write_archive(user)
    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as archive:
        importer.load(other_user, archive)

    restored = Company.objects.get(owner=other_user, name="Aveiro Uni")
    assert restored.names.get().name == "Universidade de Aveiro"
    assert export.FORMAT_VERSION >= 69


def test_the_api_lists_the_names_beside_the_name(client, user):
    from postulo.api.models import ApiToken

    company = Company.objects.create(owner=user, name="Aveiro Uni")
    CompanyName.objects.create(
        owner=user, company=company, language="pt", name="Universidade de Aveiro", source="typed"
    )
    _token, secret = ApiToken.issue(user, "test", scopes=["read"])

    body = client.get(
        f"/api/v1/companies/{company.pk}", HTTP_AUTHORIZATION=f"Bearer {secret}"
    ).json()

    assert body["name"] == "Aveiro Uni"
    assert body["names"] == [
        {"language": "pt", "name": "Universidade de Aveiro", "source": "typed"}
    ]


# ------------------------------------------------------------- where it is (#701)


def point_claim(lat, lon, rank="normal", globe="http://www.wikidata.org/entity/Q2"):
    return {
        "rank": rank,
        "mainsnak": {"datavalue": {"value": {"latitude": lat, "longitude": lon, "globe": globe}}},
    }


def with_point(*claims):
    body = json.loads(json.dumps(AVEIRO))
    body["entities"]["Q29671"]["claims"] = {"P625": list(claims)}
    return body


def test_the_coordinate_comes_from_the_same_answer(wikidata_answers):
    answers, seen = wikidata_answers
    an_item(answers, body=with_point(point_claim(40.63027778, -8.6575)))

    item = wikidata.fetch("Q29671")

    assert item.point == (40.63027778, -8.6575)
    assert len(seen) == 1, "one request for the names and the place"


@pytest.mark.parametrize(
    "claims, expected",
    [
        ([], None),
        ([point_claim(1, 2), point_claim(3, 4)], None),
        ([point_claim(1, 2), point_claim(3, 4, rank="preferred")], (3.0, 4.0)),
        ([point_claim(1, 2, rank="deprecated"), point_claim(3, 4)], (3.0, 4.0)),
        ([point_claim(1, 2, globe="http://www.wikidata.org/entity/Q405")], None),
        ([point_claim(91, 2)], None),
    ],
)
def test_only_one_place_on_earth_is_a_point(claims, expected):
    assert wikidata.point_from({"P625": claims}) == expected


def keep_after_fetch(client, user, company):
    client.force_login(user)
    client.post(reverse("jobs:company_wikidata", args=[company.pk]))
    client.post(reverse("jobs:company_wikidata", args=[company.pk]), {"keep": "1"})


def test_kept_the_point_places_the_primary_address(client, user, wikidata_answers):
    answers, _seen = wikidata_answers
    an_item(answers, body=with_point(point_claim(40.63, -8.6575)))
    company = aperture(user)
    company.location = "Aveiro, PT"
    company.save()

    keep_after_fetch(client, user, company)

    address = company.postal_addresses.get()
    assert (address.latitude, address.longitude, address.placed_by) == (40.63, -8.6575, "wikidata")
    address.street = "Campus Universitário"
    address.municipality = "Aveiro City"
    address.save()
    address.refresh_from_db()
    assert address.latitude == 40.63, "editing the words does not move it"


def test_a_point_placed_by_hand_beats_wikidatas(client, user, wikidata_answers):
    from postulo.jobs import placing

    answers, _seen = wikidata_answers
    an_item(answers, body=with_point(point_claim(40.63, -8.6575)))
    company = aperture(user)
    company.location = "Aveiro, PT"
    company.save()
    placing.place_by_hand(company.postal_addresses.get(), 1.0, 2.0)

    keep_after_fetch(client, user, company)

    address = company.postal_addresses.get()
    assert (address.latitude, address.placed_by) == (1.0, "manual")


def test_a_company_with_no_address_keeps_no_point(client, user, wikidata_answers):
    answers, _seen = wikidata_answers
    an_item(answers, body=with_point(point_claim(40.63, -8.6575)))
    company = aperture(user)
    client.force_login(user)
    client.post(reverse("jobs:company_wikidata", args=[company.pk]))

    preview = client.get(reverse("jobs:company_wikidata", args=[company.pk])).content.decode()
    client.post(reverse("jobs:company_wikidata", args=[company.pk]), {"keep": "1"})

    assert "has no address to put it on" in preview
    assert not company.postal_addresses.exists()


def test_taking_wikidatas_point_off_places_it_from_its_words(client, user, wikidata_answers):
    answers, _seen = wikidata_answers
    an_item(answers, body=with_point(point_claim(40.63, -8.6575)))
    company = aperture(user)
    company.location = "Aveiro, PT"
    company.save()
    keep_after_fetch(client, user, company)
    address = company.postal_addresses.get()

    client.post(reverse("jobs:address_place", args=[address.pk]), {"take_off": "1"})

    address.refresh_from_db()
    assert address.placed_by != "wikidata"
