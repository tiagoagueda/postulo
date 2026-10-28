"""A publication suggested from a DOI or from the person's ORCID record, only when asked (#688).

Every answer here comes from a `MockTransport` behind the real guarded client, with the
resolver stubbed, as `tests/security/test_outbound.py` does it: nothing leaves the machine,
and what is held is what the code would send and accept. The records are shaped as the
services answer, read on 2026-10-27 against the DOI Foundation's content-negotiation
documentation (CSL JSON from doi.org, field names as Crossref's ``/transform`` and DataCite's
``data.crosscite.org`` write them) and ORCID's v3.0 works summary (``group``,
``work-summary``, ``external-ids``, ``publication-date``), trimmed to what is worth reading.
"""

from __future__ import annotations

import copy
import ipaddress
import json

import httpx
import pytest
from django.urls import reverse

from postulo.plugins import http
from postulo.resume import lookups
from postulo.resume.models import Publication

pytestmark = pytest.mark.django_db

PUBLIC = [ipaddress.ip_address("104.18.32.75")]
CSL = "application/vnd.citationstyles.csl+json"
#: ORCID's own example record (Josiah Carberry), whose check digit is right.
ORCID = "0000-0002-1825-0097"

NUMPY_DOI = "10.1038/s41586-020-2649-2"
DATASET_DOI = "10.5281/zenodo.3509134"

#: What Crossref's ``/transform`` answers for a journal article: Crossref's own type name,
#: the abstract in JATS, the references, the funders, the licences and a ``URL`` on dx.doi.org.
CROSSREF_RECORD = {
    "indexed": {"date-parts": [[2026, 10, 20]], "timestamp": 1792540800000},
    "reference-count": 2,
    "publisher": "Springer Science and Business Media LLC",
    "issue": "7825",
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0", "content-version": "vor"}],
    "funder": [{"DOI": "10.13039/100000001", "name": "National Science Foundation"}],
    "content-domain": {"domain": ["link.springer.com"], "crossmark-restriction": False},
    "short-container-title": ["Nature"],
    "abstract": "<jats:p>Array programming provides a powerful, compact syntax.</jats:p>",
    "published-print": {"date-parts": [[2020, 9, 17]]},
    "DOI": NUMPY_DOI,
    "type": "journal-article",
    "created": {"date-parts": [[2020, 9, 16]]},
    "page": "357-362",
    "source": "Crossref",
    "is-referenced-by-count": 15000,
    "title": "Array programming with <i>NumPy</i>",
    "prefix": "10.1038",
    "volume": "585",
    "author": [
        {"given": "Charles R.", "family": "Harris", "sequence": "first", "affiliation": []},
        {"given": "K. Jarrod", "family": "Millman", "sequence": "additional", "affiliation": []},
        {
            "given": "Stéfan J.",
            "family": "Walt",
            "non-dropping-particle": "van der",
            "sequence": "additional",
            "affiliation": [],
        },
    ],
    "member": "297",
    "reference": [
        {"key": "2649_CR1", "unstructured": "Hunter, J. D. Matplotlib. Comput. Sci. Eng. (2007)."},
        {"key": "2649_CR2", "unstructured": "Virtanen, P. et al. SciPy 1.0. Nat. Methods (2020)."},
    ],
    "container-title": "Nature",
    "language": "en",
    "link": [{"URL": "https://www.nature.com/articles/s41586-020-2649-2.pdf"}],
    "issued": {"date-parts": [[2020, 9, 16]]},
    "URL": "http://dx.doi.org/10.1038/s41586-020-2649-2",
    "ISSN": ["0028-0836", "1476-4687"],
    "container-title-short": "Nature",
}

#: What ``data.crosscite.org`` answers for a DataCite DOI: CSL's own type names, a year only,
#: an organisation as a literal name, and a description where Crossref has an abstract.
DATACITE_RECORD = {
    "type": "dataset",
    "id": "https://doi.org/10.5281/zenodo.3509134",
    "categories": ["Climate"],
    "author": [
        {"family": "Hausfather", "given": "Zeke"},
        {"literal": "Berkeley Earth"},
    ],
    "issued": {"date-parts": [[2019]]},
    "abstract": "Monthly land and ocean temperature anomalies.",
    "DOI": DATASET_DOI,
    "publisher": "Zenodo",
    "title": "Global temperature records‮\u0007",
    "URL": "https://zenodo.org/record/3509134",
    "copyright": "Creative Commons Attribution 4.0 International",
    "version": "1.2",
}


def orcid_works(*groups) -> dict:
    return {
        "last-modified-date": {"value": 1792540800000},
        "group": list(groups),
        "path": f"/{ORCID}/works",
    }


def summary(
    put_code,
    title,
    kind,
    *,
    year="",
    month="",
    day="",
    journal=None,
    doi="",
    relationship="self",
    index="1",
    visibility="public",
):
    ids = (
        [
            {
                "external-id-type": "doi",
                "external-id-value": doi,
                "external-id-normalized": {"value": doi.lower(), "transient": True},
                "external-id-normalized-error": None,
                "external-id-url": {"value": f"https://doi.org/{doi}"},
                "external-id-relationship": relationship,
            }
        ]
        if doi
        else []
    )
    date = {
        unit: {"value": value} if value else None
        for unit, value in (("year", year), ("month", month), ("day", day))
    }
    return {
        "put-code": put_code,
        "created-date": {"value": 1600000000000},
        "last-modified-date": {"value": 1700000000000},
        "source": {
            "source-orcid": None,
            "source-client-id": {"uri": "https://orcid.org/client/0000-0001-9884-1913"},
            "source-name": {"value": "Crossref"},
        },
        "title": {"title": {"value": title}, "subtitle": None, "translated-title": None},
        "external-ids": {"external-id": ids},
        "url": {"value": f"https://doi.org/{doi}"} if doi else None,
        "type": kind,
        "publication-date": date if year else None,
        "journal-title": {"value": journal} if journal else None,
        "visibility": visibility,
        "path": f"/{ORCID}/work/{put_code}",
        "display-index": index,
    }


def group(*summaries) -> dict:
    return {
        "last-modified-date": {"value": 1700000000000},
        "external-ids": {"external-id": []},
        "work-summary": list(summaries),
    }


ORCID_RECORD = orcid_works(
    group(
        summary(
            81234567,
            "Array programming with NumPy",
            "journal-article",
            year="2020",
            month="09",
            day="16",
            journal="Nature",
            doi=NUMPY_DOI,
        )
    ),
    group(
        # A paper whose only DOI is the proceedings' own: not the paper's.
        summary(
            81234568,
            "Teaching with notebooks",
            "conference-paper",
            year="2019",
            doi="10.1000/proceedings.2019",
            relationship="part-of",
        )
    ),
    group(
        # Two versions of one work: the one ranked first is the one shown.
        summary(81234569, "Old title", "data-set", year="2018", index="0"),
        summary(81234570, "Climate records", "data-set", year="2018", index="5"),
    ),
    group(summary(81234571, "A brand new kind", "some-future-type", year="2024")),
)


# --------------------------------------------------------------------- the transport


@pytest.fixture
def registry(monkeypatch):
    """The real guard, every public name resolving publicly, over a mock transport.

    ``answers`` maps ``host + path`` to a response, or to a function of the request.
    """
    seen: list[httpx.Request] = []
    answers: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            httpx.Request(request.method, request.url, headers=request.headers, extensions={})
        )
        name = request.extensions.get(http.NAMED_URL, request.url)
        answer = answers.get(f"{name.host}{name.raw_path.decode()}")
        if callable(answer):
            return answer(request)
        return answer if answer is not None else httpx.Response(404)

    monkeypatch.setattr(http, "public_addresses_for", lambda url: PUBLIC)
    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return answers, seen


def via_crossref(answers, doi=NUMPY_DOI, record=CROSSREF_RECORD):
    """doi.org redirects once to Crossref's transform, which answers with the record."""
    target = f"https://api.crossref.org/v1/works/{doi}/transform"
    answers[f"doi.org/{doi}"] = httpx.Response(302, headers={"Location": target})
    answers[f"api.crossref.org/v1/works/{doi}/transform"] = httpx.Response(
        200, headers={"Content-Type": CSL}, content=json.dumps(record).encode()
    )


def via_datacite(answers, doi=DATASET_DOI, record=DATACITE_RECORD):
    target = f"https://data.crosscite.org/{doi}"
    answers[f"doi.org/{doi}"] = httpx.Response(302, headers={"Location": target})
    answers[f"data.crosscite.org/{doi}"] = httpx.Response(
        200, headers={"Content-Type": f"{CSL}; charset=utf-8"}, content=json.dumps(record).encode()
    )


def orcid_answers(answers, record=ORCID_RECORD, orcid=ORCID):
    answers[f"pub.orcid.org/v3.0/{orcid}/works"] = httpx.Response(
        200,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        content=json.dumps(record).encode(),
    )


def with_orcid(person, value=ORCID):
    from postulo.accounts.models import PersonIdentifier

    return PersonIdentifier.objects.create(profile=person.profile, scheme="orcid", value=value)


def form_data(**values) -> dict:
    data = {"entry_type": "article", "title": "", "doi": "", "language": ""}
    data.update(values)
    return data


# ------------------------------------------------------------------- reading a record


def test_a_crossref_record_gives_the_short_list_and_nothing_else(registry):
    answers, seen = registry
    via_crossref(answers)

    found = lookups.from_doi(NUMPY_DOI)

    assert found.service == "Crossref"
    assert found.values == {
        "entry_type": "article",
        "title": "Array programming with NumPy",
        "authors": "Harris, Charles R.\nMillman, K. Jarrod\nvan der Walt, Stéfan J.",
        "container_title": "Nature",
        "publisher": "Springer Science and Business Media LLC",
        "date": "2020-09-16",
        "volume": "585",
        "number": "7825",
        "pages": "357-362",
        "doi": NUMPY_DOI,
        "language": found.values["language"],
    }
    assert found.values["language"].startswith("en")
    assert [request.url.host for request in seen] == ["104.18.32.75"] * 2


def test_the_abstract_the_references_and_the_links_are_never_kept(registry):
    answers, _seen = registry
    via_crossref(answers)

    values = lookups.from_doi(NUMPY_DOI).values
    kept = " ".join(values.values())

    assert set(values) <= set(lookups.FILLED_ORDER)
    for unread in ("powerful, compact", "Matplotlib", "SciPy", "Science Foundation", ".pdf"):
        assert unread not in kept
    assert "dx.doi.org" not in kept, "the record's URL is ignored"
    assert "url" not in values


def test_a_datacite_record_in_csl_s_own_words_is_read_too(registry):
    answers, _seen = registry
    via_datacite(answers)

    found = lookups.from_doi(DATASET_DOI)

    assert found.service == "DataCite"
    assert found.values["entry_type"] == "dataset"
    assert found.values["authors"] == "Hausfather, Zeke\nBerkeley Earth"
    assert found.values["date"] == "2019"
    assert found.values["title"] == "Global temperature records", "no override, no bell"
    assert "Monthly land" not in " ".join(found.values.values())


@pytest.mark.parametrize(
    "name, expected",
    [
        # CSL's vocabulary
        ("article-journal", "article"),
        ("paper-conference", "inproceedings"),
        ("chapter", "incollection"),
        ("dataset", "dataset"),
        ("thesis", "phdthesis"),
        # Crossref's own
        ("journal-article", "article"),
        ("proceedings-article", "inproceedings"),
        ("book-chapter", "incollection"),
        ("monograph", "book"),
        ("dissertation", "phdthesis"),
        # ORCID's
        ("conference-paper", "inproceedings"),
        ("data-set", "dataset"),
        # nobody's
        ("peer-review", "misc"),
        ("", "misc"),
        (None, "misc"),
        (["journal-article"], "misc"),
    ],
)
def test_both_vocabularies_map_and_an_unknown_type_is_other(name, expected):
    assert lookups.entry_type_for(name) == expected
    assert expected in __import__("postulo.resume.publications").resume.publications.TYPES


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("Array programming with <i>NumPy</i>", "Array programming with NumPy"),
        ("H<sub>2</sub>O in <scp>Mars</scp> soil", "H2O in Mars soil"),
        (
            'Bounds on <mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            "<mml:mi>x</mml:mi><mml:mo>&lt;</mml:mo><mml:mn>1</mml:mn></mml:math> hold",
            "Bounds on x<1 hold",
        ),
        ("A title‮ reversed⁦ isolated⁩", "A title reversed isolated"),
        ("Line\nbreak\tand\u0007bell", "Line break and bell"),
        ("&lt;script&gt;alert(1)&lt;/script&gt;", "<script>alert(1)</script>"),
        (2020, "2020"),
        (True, ""),
        ({"nested": "thing"}, ""),
    ],
)
def test_markup_and_control_characters_go(raw, clean):
    assert lookups.clean(raw, 500) == clean


def test_an_escaped_tag_is_text_and_is_drawn_escaped(client, user, registry):
    answers, _seen = registry
    record = dict(CROSSREF_RECORD, title="&lt;script&gt;alert(1)&lt;/script&gt;")
    via_crossref(answers, record=record)
    client.force_login(user)

    page = client.post(
        reverse("resume:publication_lookup_new"), form_data(doi=NUMPY_DOI)
    ).content.decode()

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_five_thousand_authors_become_a_hundred_and_others():
    people = [{"given": "A.", "family": f"Physicist{n:04d}"} for n in range(5156)]

    text = lookups.names(people)
    lines = text.splitlines()

    assert len(lines) == lookups.MAX_AUTHORS + 1
    assert lines[0] == "Physicist0000, A."
    assert lines[-1] == "and others"
    assert len(text) <= Publication._meta.get_field("authors").max_length


def test_long_names_are_cut_at_a_whole_name_to_fit_the_column():
    people = [{"literal": "N" * 150 + f" {n}"} for n in range(40)]

    lines = lookups.names(people).splitlines()

    assert lines[-1] == "and others"
    assert all(line.startswith("N" * 150) for line in lines[:-1]), "no half name"
    assert len("\n".join(lines)) <= Publication._meta.get_field("authors").max_length


def test_every_value_is_cut_to_its_column(registry):
    answers, _seen = registry
    record = dict(CROSSREF_RECORD, title="T" * 900, volume="9" * 90, publisher="P" * 400)
    via_crossref(answers, record=record)

    values = lookups.from_doi(NUMPY_DOI).values

    assert len(values["title"]) == 500
    assert len(values["volume"]) == 40
    assert len(values["publisher"]) == 200


@pytest.mark.parametrize(
    "parts, date",
    [
        ([[2020, 9, 16]], "2020-09-16"),
        ([[2020, 9]], "2020-09"),
        ([["2019"]], "2019"),
        ([[2020, 13, 40]], "2020"),
        ([[None]], ""),
        ([], ""),
        ("2020", ""),
    ],
)
def test_a_date_is_read_from_its_parts_or_left_blank(parts, date):
    assert lookups.date_from(parts) == date


# ----------------------------------------------------------------- the Look up button


def test_look_up_fills_the_blanks_and_leaves_what_was_typed(client, user, registry):
    answers, _seen = registry
    via_crossref(answers)
    client.force_login(user)

    response = client.post(
        reverse("resume:publication_lookup_new"),
        form_data(doi=NUMPY_DOI, title="My own title", volume="586"),
    )
    form = response.context["form"]
    page = response.content.decode()

    assert response.status_code == 200
    assert form["title"].value() == "My own title", "typed, so kept"
    assert form["volume"].value() == "586", "typed, so kept, though it differs"
    assert form["authors"].value().startswith("Harris, Charles R.")
    assert form["container_title"].value() == "Nature"
    assert form["pages"].value() == "357-362"
    assert not form.is_bound, "drawn as a form to review, not as one with errors"
    assert "Filled in from Crossref, through doi.org" in page
    assert "Authors" in page and "nothing is kept until you save" in page
    assert not Publication.objects.exists(), "nothing is saved until the person saves"
    create = reverse("resume:item_create", args=["publication"])
    assert f'action="{create}"' in page, "Save on the form drawn back still saves"


def test_a_new_entry_on_the_type_it_opened_with_takes_the_records_type(client, user, registry):
    answers, _seen = registry
    via_datacite(answers)
    client.force_login(user)

    response = client.post(reverse("resume:publication_lookup_new"), form_data(doi=DATASET_DOI))

    assert response.context["form"]["entry_type"].value() == "dataset"


def test_a_type_somebody_chose_is_kept(client, user, registry):
    answers, _seen = registry
    via_datacite(answers)
    client.force_login(user)

    response = client.post(
        reverse("resume:publication_lookup_new"), form_data(doi=DATASET_DOI, entry_type="misc")
    )

    assert response.context["form"]["entry_type"].value() == "misc"


def test_look_up_on_a_saved_entry_saves_nothing(client, user, registry):
    answers, _seen = registry
    via_crossref(answers)
    held = Publication.objects.create(owner=user, title="Mine", doi=NUMPY_DOI, entry_type="book")
    client.force_login(user)

    response = client.post(
        reverse("resume:publication_lookup", args=[held.pk]),
        form_data(doi=NUMPY_DOI, title="Mine", entry_type="book"),
    )

    form = response.context["form"]
    assert form["entry_type"].value() == "book", "a saved entry's type is never changed"
    assert form["publisher"].value() == "Springer Science and Business Media LLC"
    held.refresh_from_db()
    assert held.publisher == "" and held.authors == ""


def test_the_form_has_the_button_beside_the_doi_and_enter_still_saves(client, user):
    client.force_login(user)

    page = client.get(reverse("resume:item_create", args=["publication"])).content.decode()

    lookup = reverse("resume:publication_lookup_new")
    assert f'formaction="{lookup}"' in page
    assert "Sends the DOI to doi.org, which passes it to Crossref or DataCite" in page
    hidden_save = page.index('class="sr-only" tabindex="-1"')
    assert hidden_save < page.index(f'formaction="{lookup}"'), "Enter meets Save first"


def test_a_saved_entrys_form_looks_up_at_its_own_address(client, user):
    held = Publication.objects.create(owner=user, title="Mine")
    client.force_login(user)

    page = client.get(reverse("resume:item_update", args=["publication", held.pk])).content
    assert reverse("resume:publication_lookup", args=[held.pk]).encode() in page


@pytest.mark.parametrize(
    "answer, words",
    [
        (httpx.Response(404), "knows no such identifier"),
        (httpx.Response(429), "fewer requests"),
        (httpx.Response(503), "fewer requests"),
        (httpx.Response(500), "answered 500"),
        (httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>"), "form"),
        (httpx.Response(200, headers={"Content-Type": CSL}, content=b"not json"), "not a record"),
    ],
)
def test_a_refusal_is_said_once_and_never_tried_again(client, user, registry, answer, words):
    answers, seen = registry
    answers[f"doi.org/{NUMPY_DOI}"] = answer
    client.force_login(user)

    response = client.post(
        reverse("resume:publication_lookup_new"), form_data(doi=NUMPY_DOI, title="Typed")
    )

    assert words in response.content.decode()
    assert len(seen) == 1, "one request, no retry"
    assert response.context["form"]["title"].value() == "Typed", "what was typed is still there"


def test_no_doi_or_a_malformed_one_asks_nothing(client, user, registry):
    _answers, seen = registry
    client.force_login(user)

    blank = client.post(reverse("resume:publication_lookup_new"), form_data())
    odd = client.post(reverse("resume:publication_lookup_new"), form_data(doi="not-a-doi"))

    assert "Type the DOI first." in blank.content.decode()
    assert "That is not a DOI." in odd.content.decode()
    assert seen == []


def test_somebody_elses_publication_is_not_looked_up(client, user, other_user, registry):
    answers, seen = registry
    via_crossref(answers)
    theirs = Publication.objects.create(owner=other_user, title="Theirs", doi=NUMPY_DOI)
    client.force_login(user)

    response = client.post(
        reverse("resume:publication_lookup", args=[theirs.pk]), form_data(doi=NUMPY_DOI)
    )

    assert response.status_code == 404
    assert seen == [], "nothing was asked on its behalf"


def test_a_get_is_not_a_lookup(client, user, registry):
    _answers, seen = registry
    client.force_login(user)

    assert client.get(reverse("resume:publication_lookup_new")).status_code == 405
    assert seen == []


def test_the_request_is_one_get_to_doi_org_with_the_doi_in_its_path_alone(registry):
    answers, seen = registry
    via_crossref(answers)

    lookups.from_doi(f"https://doi.org/{NUMPY_DOI}")

    first = seen[0]
    assert first.headers["Host"] == "doi.org"
    assert first.url.raw_path.decode() == f"/{NUMPY_DOI}"
    assert first.url.query == b""
    assert first.headers["Accept"] == CSL
    assert first.headers["User-Agent"].startswith("Postulo")
    assert "@" not in str(first.headers), "no email address, the person's or the operator's"


def test_a_doi_with_characters_a_path_reads_otherwise_is_encoded_into_the_path(registry):
    _answers, seen = registry

    with pytest.raises(lookups.LookupRefused):
        lookups.from_doi("10.1000/a?b#c%d")

    assert seen[0].headers["Host"] == "doi.org"
    assert seen[0].url.raw_path.decode() == "/10.1000/a%3Fb%23c%25d"
    assert seen[0].url.query == b""


def test_a_redirect_to_plain_http_is_not_followed(registry):
    answers, seen = registry
    answers[f"doi.org/{NUMPY_DOI}"] = httpx.Response(
        302, headers={"Location": f"http://api.crossref.org/v1/works/{NUMPY_DOI}/transform"}
    )

    with pytest.raises(lookups.LookupRefused, match="somewhere else"):
        lookups.from_doi(NUMPY_DOI)
    assert len(seen) == 1


# -------------------------------------------------------------------- the ORCID record


def test_the_works_summary_is_read_one_work_per_group():
    works, total = lookups.works_from(ORCID_RECORD)

    assert total == 4
    numpy, notebooks, climate, future = works
    assert numpy == {
        "entry_type": "article",
        "title": "Array programming with NumPy",
        "container_title": "Nature",
        "date": "2020-09-16",
        "doi": NUMPY_DOI,
    }
    assert notebooks["entry_type"] == "inproceedings"
    assert notebooks["doi"] == "", "the proceedings' DOI is not the paper's"
    assert climate["title"] == "Climate records", "the version ranked first"
    assert climate["entry_type"] == "dataset" and climate["date"] == "2018"
    assert future["entry_type"] == "misc"


def test_only_public_works_are_listed_and_at_most_a_hundred():
    many = orcid_works(
        *(group(summary(n, f"Paper {n}", "journal-article", year="2020")) for n in range(150)),
        group(summary(999, "Hidden", "journal-article", visibility="limited")),
    )

    works, total = lookups.works_from(many)

    assert len(works) == lookups.MAX_WORKS and total == 150
    assert "Hidden" not in [work["title"] for work in works]


def test_an_answer_that_is_not_a_works_summary_is_refused():
    with pytest.raises(lookups.LookupRefused):
        lookups.works_from({"error-code": 9016})


def test_the_button_is_offered_only_to_a_person_with_an_orcid(client, user):
    client.force_login(user)
    overview = reverse("resume:overview")

    assert "Suggest from my ORCID record" not in client.get(overview).content.decode()
    with_orcid(user)
    assert "Suggest from my ORCID record" in client.get(overview).content.decode()


def test_without_an_orcid_nothing_is_asked_and_a_posted_one_is_ignored(client, user, registry):
    answers, seen = registry
    orcid_answers(answers)
    client.force_login(user)

    response = client.post(reverse("resume:publication_orcid"), {"orcid": ORCID})

    assert response.status_code == 302
    assert seen == []


def test_a_posted_orcid_never_replaces_the_persons_own(client, user, registry):
    answers, seen = registry
    mine = "0000-0001-5109-3700"
    with_orcid(user, mine)
    orcid_answers(answers, orcid=mine)
    client.force_login(user)

    client.post(reverse("resume:publication_orcid"), {"orcid": ORCID})

    (request,) = seen
    assert request.url.raw_path.decode() == f"/v3.0/{mine}/works"
    assert request.headers["Host"] == "pub.orcid.org"
    assert request.headers["Accept"] == "application/json"
    assert request.url.query == b""


def test_the_review_marks_what_is_held_and_ticks_nothing(client, user, registry):
    answers, _seen = registry
    with_orcid(user)
    orcid_answers(answers)
    Publication.objects.create(owner=user, title="Whatever I called it", doi=NUMPY_DOI.upper())
    Publication.objects.create(owner=user, title="Climate  Records", date="2018-03")
    client.force_login(user)

    response = client.post(reverse("resume:publication_orcid"), follow=True)
    page = response.content.decode()
    works = {work["title"]: work for work in response.context["works"]}

    assert works["Array programming with NumPy"]["held"], "the same DOI"
    assert works["Climate records"]["held"], "the same title and year"
    assert not works["Teaching with notebooks"]["held"]
    assert page.count('name="work"') == 4
    assert "checked" not in page.split("data-orcid-works")[1].split("</ul>")[0]
    assert page.count("You already have this one.") == 2


def test_only_what_is_ticked_is_added_from_the_summary_alone(client, user, registry):
    answers, _seen = registry
    with_orcid(user)
    orcid_answers(answers)
    client.force_login(user)
    client.post(reverse("resume:publication_orcid"))

    response = client.post(reverse("resume:publication_orcid"), {"add": "1", "work": ["0", "3"]})

    assert response.status_code == 302
    added = {item.title: item for item in Publication.objects.for_user(user)}
    assert set(added) == {"Array programming with NumPy", "A brand new kind"}
    numpy = added["Array programming with NumPy"]
    assert numpy.authors == "", "a summary has no authors"
    assert numpy.doi == NUMPY_DOI and numpy.container_title == "Nature"
    assert numpy.date == "2020-09-16" and numpy.entry_type == "article"
    assert numpy.cite_key, "a key is made as for any entry"
    assert added["A brand new kind"].entry_type == "misc"


def test_at_most_twenty_are_added_at_once(client, user, registry):
    answers, _seen = registry
    with_orcid(user)
    many = orcid_works(
        *(group(summary(n, f"Paper {n}", "journal-article", year="2020")) for n in range(30))
    )
    orcid_answers(answers, record=many)
    client.force_login(user)
    client.post(reverse("resume:publication_orcid"))

    too_many = [str(n) for n in range(lookups.MAX_ADDED + 1)]
    client.post(reverse("resume:publication_orcid"), {"add": "1", "work": too_many})
    assert not Publication.objects.exists(), "refused whole, not cut short"

    client.post(reverse("resume:publication_orcid"), {"add": "1", "work": too_many[:-1]})
    assert Publication.objects.count() == lookups.MAX_ADDED


def test_a_tick_outside_the_list_adds_nothing(client, user, registry):
    answers, _seen = registry
    with_orcid(user)
    orcid_answers(answers)
    client.force_login(user)
    client.post(reverse("resume:publication_orcid"))

    client.post(reverse("resume:publication_orcid"), {"add": "1", "work": ["99", "-1", "x"]})

    assert not Publication.objects.exists()


def test_the_review_without_a_fetch_goes_back_to_the_career(client, user):
    client.force_login(user)

    response = client.get(reverse("resume:publication_orcid"))

    assert response.status_code == 302
    assert response.url.endswith("#section-publication")


def test_a_hostile_title_in_an_orcid_record_is_plain_text(client, user, registry):
    answers, _seen = registry
    with_orcid(user)
    record = copy.deepcopy(ORCID_RECORD)
    record["group"][0]["work-summary"][0]["title"]["title"]["value"] = (
        "<img src=x onerror=alert(1)>Safe‮title"
    )
    orcid_answers(answers, record=record)
    client.force_login(user)

    page = client.post(reverse("resume:publication_orcid"), follow=True).content.decode()

    assert "onerror" not in page
    assert "Safetitle" in page
