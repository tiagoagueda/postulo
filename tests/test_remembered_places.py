"""Remembered places, the part that reads a page: learning where a value was, and finding it
again on the next page from the same site (#267).

The pages are a council's careers notice, the same council's next notice after a small
redesign, and a notice listing two posts. Every field of the first sits in a different kind
of place, so the one page teaches every kind; the second moves all of them, which is what
a place has to survive; the third names two of each, which is what a place must not guess
between.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import time
from decimal import Decimal

import pytest

from postulo.plugins.base import RememberedPlace
from postulo.plugins.builtin import PageMetadataSource, SchemaOrgSource, hints
from postulo.plugins.builtin.htmlutil import parse_html

PAGES = pathlib.Path(__file__).parent / "data" / "remembered"
URL = "https://emprego.cm-lisboa.example/ofertas/12"

DESCRIPTION = (
    "Procedimento concursal comum para ocupação de um posto de trabalho na carreira de "
    "técnico superior, na área da informática, em regime de contrato de trabalho em funções "
    "públicas por tempo indeterminado.\n\nCompete ao trabalhador manter os sistemas da "
    "autarquia e apoiar os serviços."
)


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def learned() -> list[dict]:
    return hints.places(URL, page("notice"))


# ------------------------------------------------------------------ learning


@pytest.mark.parametrize(
    "field,value,place",
    [
        ("company_name", "Câmara Municipal de Lisboa", {"id": "entidade"}),
        ("title", "Técnico Superior de Informática", {"heading": "h1", "in": "main", "index": 0}),
        ("location", "Lisboa", {"class": "aviso-local", "tag": "p"}),
        ("salary", (Decimal("1385.99"), None), {"label": "remuneração"}),
        ("closes_at", dt.date(2026, 10, 30), {"label": "prazo"}),
        ("employment_type", "full_time", {"label": "tipo de contrato"}),
        ("description", DESCRIPTION, {"data": ["data-qa", "descricao"]}),
    ],
)
def test_a_corrected_value_is_remembered_at_the_most_stable_place_holding_it(
    learned, field, value, place
):
    assert hints.learn(learned, field, value) == place


def test_the_company_is_remembered_by_its_id_and_not_by_the_meta_that_also_holds_it(learned):
    """Two places held it; the more stable one is kept."""
    assert hints.learn(learned, "company_name", "câmara  municipal de LISBOA") == {"id": "entidade"}


def test_a_value_the_page_never_held_teaches_nothing(learned):
    assert hints.learn(learned, "location", "Sintra") is None
    assert hints.learn(learned, "salary", (Decimal("2000"), None)) is None
    assert hints.learn(learned, "closes_at", dt.date(2026, 12, 31)) is None
    assert hints.learn(learned, "not_a_field", "Lisboa") is None


def test_learning_keeps_digests_and_readings_not_the_page_s_words(learned):
    """What waits with a capture cannot read the page back."""
    kept = json.dumps(learned, ensure_ascii=False)

    for words in ("Técnico Superior", "Câmara Municipal", "Procedimento concursal", "Lisboa"):
        assert words not in kept, words


def test_what_is_kept_to_learn_from_is_bounded():
    page_of_ids = "<main>" + "".join(f'<p id="p{n}x">p{n}</p>' for n in range(2000)) + "</main>"

    assert len(hints.places(URL, page_of_ids)) <= hints.MOST_PLACES


@pytest.mark.parametrize(
    "name,expected",
    [
        ("job__location", True),
        ("JobCard_title__3xY9z", False),
        ("title__a1b2c", False),
        ("jobTitle", True),
        ("aviso-local", True),
        ("css-1x2y3z", False),
        ("sc-bdVaJa", False),
        ("job-12345", False),
        ("a1b2c3d4e5", False),
        ("ab", False),
        ("_next", False),
    ],
)
def test_a_name_a_framework_generated_is_not_a_place(name, expected):
    assert hints.stable(name) is expected


# ------------------------------------------------------------------ finding again


@pytest.mark.parametrize(
    "field,place,expected",
    [
        ("company_name", {"id": "entidade"}, {"company_name": "Câmara Municipal de Lisboa"}),
        (
            "title",
            {"heading": "h1", "in": "main", "index": 0},
            {"title": "Assistente Técnico de Arquivo"},
        ),
        ("location", {"class": "aviso-local", "tag": "p"}, {"location": "Porto"}),
        (
            "salary",
            {"label": "remuneração"},
            {
                "salary_min": Decimal("1020.06"),
                "salary_max": None,
                "salary_currency": "EUR",
                "salary_period": "",
            },
        ),
        ("closes_at", {"label": "prazo"}, {"closes_at": dt.date(2026, 11, 15)}),
        ("employment_type", {"label": "tipo de contrato"}, {"employment_type": "part_time"}),
    ],
)
def test_a_remembered_place_survives_a_redesign(field, place, expected):
    root = parse_html(page("notice-redesigned"))

    assert hints.read(hints.locate(root, place), field, "pt-PT") == expected


def test_a_value_held_twice_by_one_kind_of_place_is_learned_at_the_next_kind():
    """Two ids holding the council's name say nothing about which one the person read; the
    one ``<meta>`` holding it does."""
    html = page("notice").replace('<header><a href="/">', '<header><a id="topo" href="/">')

    assert hints.learn(hints.places(URL, html), "company_name", "Câmara Municipal de Lisboa") == {
        "meta": "author"
    }


def test_a_value_held_twice_everywhere_is_learned_nowhere():
    html = "<main><h1>Engineer</h1><p class='a'>Lisboa</p><p class='b'>Lisboa</p></main>"

    assert hints.learn(hints.places(URL, html), "location", "Lisboa") is None


def test_a_place_that_now_names_two_things_finds_nothing():
    root = parse_html(page("notice-twice"))

    assert hints.locate(root, {"class": "aviso-local", "tag": "p"}) is None
    assert hints.locate(root, {"label": "prazo"}) is None


@pytest.mark.parametrize(
    "place",
    [
        {},
        {"id": 5},
        {"id": "x" * 200},
        {"class": "aviso-local"},
        {"data": ["onclick", "alert(1)"]},
        {"heading": "h2", "in": "main", "index": True},
        {"heading": "h9", "in": "main", "index": 0},
        {"meta": "author", "id": "entidade", "class": "a", "tag": "p"},
        {"id": "entidade", "note": "a key no place is written with"},
        {"class": "aviso-local", "tag": "p", "in": "main"},
        "entidade",
    ],
)
def test_a_place_read_back_from_storage_is_checked_not_believed(place):
    assert hints.locate(parse_html(page("notice")), place) is None


# ------------------------------------------------------------------ in the tiers


def test_the_floor_is_read_after_the_remembered_places():
    remembered = [
        RememberedPlace("location", {"class": "aviso-local", "tag": "p"}),
        RememberedPlace("company_name", {"id": "no-longer-here"}),
    ]

    data = PageMetadataSource().parse(URL, page("notice-redesigned"), hints=remembered)

    assert data.location == "Porto"
    assert remembered[0].outcome == "used"
    assert remembered[1].outcome == "missed", "and the field falls through to the page"
    assert data.title == "Assistente Técnico de Arquivo", "the page's own heading"


def test_a_remembered_salary_is_the_whole_salary():
    """The page's own "per year" is not added to the figure a remembered place found."""
    html = (
        '<html lang="en-GB"><body><main><h1>Engineer</h1>'
        "<p>Salary: €45,000 per year</p>"
        '<p id="pay">€50,000</p></main></body></html>'
    )
    remembered = [RememberedPlace("salary", {"id": "pay"})]

    data = PageMetadataSource().parse(URL, html, hints=remembered)

    assert (data.salary_min, data.salary_max) == (Decimal(50000), None)
    assert data.salary_currency == "EUR"
    assert data.salary_period == ""


def test_what_the_site_states_itself_is_never_overruled():
    """schema.org names the title and not the place: only the place is asked about."""
    posting = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "Archivist",
        "hiringOrganization": {"name": "Câmara Municipal de Lisboa"},
    }
    html = page("notice-redesigned").replace(
        "</head>", f'<script type="application/ld+json">{json.dumps(posting)}</script></head>'
    )
    remembered = [
        RememberedPlace("title", {"heading": "h1", "in": "main", "index": 0}),
        RememberedPlace("location", {"class": "aviso-local", "tag": "p"}),
    ]

    data = SchemaOrgSource().parse(URL, html, hints=remembered)

    assert data.title == "Archivist"
    assert data.location == "Porto"
    assert [hint.outcome for hint in remembered] == ["", "used"]


def test_a_source_somebody_else_wrote_is_never_handed_a_person_s_places(monkeypatch):
    from postulo.plugins import registry

    handed = []

    class Theirs:
        name, version = "theirs", "1.0"
        reads_hints = True

        def can_handle(self, url):
            return True

        def parse(self, url, html, hints=None):
            handed.append(hints)
            return None

    monkeypatch.setattr(
        registry, "available_sources", lambda **_kw: [Theirs(), PageMetadataSource()]
    )
    remembered = [RememberedPlace("location", {"class": "aviso-local", "tag": "p"})]

    result = registry.parse_page(URL, page("notice-redesigned"), hints=remembered)

    assert handed == [None], "called, and not given the places"
    assert result is not None and result[0].location == "Porto"
    assert remembered[0].outcome == "used"


def test_the_label_place_survives_omitted_end_tags():
    html = "<html><body><h1>Role</h1><dl><dt>Local<dd>Lisboa</dl></body></html>"
    assert any(
        place["place"].get("label") == "local"
        for place in hints.places("https://e.example/1", html)
    )


def test_a_row_of_twenty_thousand_cells_is_read_in_linear_time():
    html = "<table><tr>" + "<td>a</td>" * 20000 + "</tr></table>"
    started = time.monotonic()
    hints.places("https://jobs.example.org/1", html)
    assert time.monotonic() - started < 5


def test_a_paragraph_of_twenty_thousand_labels_is_read_in_linear_time():
    html = "<p>" + "<span>Label:</span> value " * 20000 + "</p>"
    started = time.monotonic()
    hints.places("https://jobs.example.org/1", html)
    assert time.monotonic() - started < 5


def test_a_label_still_finds_what_follows_it_among_many_siblings():
    root = parse_html("<p><b>x</b> y <span>Salary:</span> 50k <i>net</i><br>z</p>")
    assert dict(hints._labels(root))["salary"] == "50k net"
