"""Published earnings statistics beside a listing: the `earnings-source` kind (#1257).

A statistics office publishes what employees of a group of occupations earned in a country. A
plugin of the kind hands those figures over as plain values and Postulo draws them, on the
listing's page and on its application's, each amount with what says what it is.

**The wording is the rule, and these tests hold it.** A figure is drawn with its measure, gross
or net, its period, its year, the group it describes, the country and its source; never as the
person's offer, never as a figure to ask for, never converted and never compared. And nothing
is drawn at all while no source is enabled, where the listing names no country, or where a
source has nothing.

None of this needs a real statistic: the source here is written in the test, the way a
package installed beside Postulo would write one.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.applications.services import apply_to_listing, create_listing, record_offer
from postulo.core import earnings
from postulo.core.memo import forget_decisions
from postulo.core.models import PostalAddress
from postulo.jobs import listing_earnings, places
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import api, base, kinds, policy, registry
from postulo.plugins.models import PluginPolicy
from postulo.plugins.testing import assert_is_an_earnings_source

from .test_places import COUNTRIES, TABLE

pytestmark = pytest.mark.django_db

CARD = "data-earnings-reference"


@pytest.fixture(autouse=True)
def _fresh_registry():
    """A test that adds a plugin must not leave the registry's cache holding it."""
    registry._cache.pop(earnings.KIND, None)
    yield
    registry._cache.pop(earnings.KIND, None)


def figure(**changes):
    values = dict(  # noqa: C408
        measure="mean",
        amount=Decimal("36066"),
        currency="EUR",
        period="year",
        basis="gross",
        year="2022",
        group="Professionals (major group 2)",
    )
    values.update(changes)
    return api.EarningsFigure(**values)


class Asked(list):
    """What a source was asked, as ``(isco_code, country, period, currency)``."""


def a_source(answer=None, asked: Asked | None = None, **changes):
    """A source that answers with ``answer`` (a list of figures) and notes what it was asked."""
    given = [figure()] if answer is None else answer

    def figures(isco_code, country, period="", currency=""):
        if asked is not None:
            asked.append((isco_code, country, period, currency))
        return given

    values = dict(  # noqa: C408
        key="office",
        label="Statistics Office",
        countries=("PT", "PL", "HR"),
        offline=True,
        licence="CC BY 4.0",
        figures=figures,
        scope="Employees of enterprises with ten or more employees.",
        citation="Survey of earnings 2022 (table-28)",
        url="https://example.org/table-28",
    )
    values.update(changes)
    return api.EarningsSource(**values)


def install(monkeypatch, *sources, name="office"):
    """Register a plugin of the kind holding ``sources``, as one Postulo ships."""

    plugin = type("Office", (), {"name": name, "kind": earnings.KIND, "sources": tuple(sources)})
    held = [*registry._builtin.get(earnings.KIND, ()), plugin]
    monkeypatch.setitem(registry._builtin, earnings.KIND, held)
    registry._cache.pop(earnings.KIND, None)
    return plugin


def listing_for(person, *, location="Lisboa, PT", code="2512", title="Software developer", **more):
    company, _made = Company.objects.get_or_create(owner=person, name="Acme")
    listing = create_listing(
        person, company=company, posting_data={"title": title, "location": location, **more}
    )
    # The unit group follows the title through ESCO, which these tests do not need: the
    # code is written as the classification would have left it.
    JobPosting.objects.filter(pk=listing.pk).update(isco_code=code)
    listing.refresh_from_db()
    return listing


def page_of(client, person, listing) -> str:
    client.force_login(person)
    return client.get(reverse("jobs:posting_detail", args=[listing.pk])).content.decode()


def card_of(page: str) -> str:
    """The card's own text, with the markup and the runs of space taken out."""
    start = page.index(f'<div class="card mb-6" {CARD}')
    depth, position = 0, start
    for found in re.finditer(r"<(/?)div\b", page[start:]):
        depth += -1 if found.group(1) else 1
        if depth == 0:
            position = start + found.end()
            break
    return " ".join(re.sub(r"<[^>]+>", " ", page[start:position]).split())


# ------------------------------------------------------------------------- the kind


def test_the_surface_holds_what_an_earnings_source_needs():
    for name in ("EarningsFigure", "EarningsSource", "EarningsSourcePlugin"):
        assert name in api.__all__
    assert api.EarningsFigure is earnings.Figure
    assert api.EarningsSource is earnings.Source
    assert api.EarningsSourcePlugin is base.EarningsSourcePlugin


def test_the_kind_is_registered_open_named_and_switchable():
    assert registry.GROUPS[earnings.KIND] == "postulo.earnings_sources"
    assert registry._protocol_for(earnings.KIND) is base.EarningsSourcePlugin
    assert earnings.KIND in policy.GOVERNED_KINDS, "so an administrator can switch one"
    assert str(kinds.label_for(earnings.KIND)) == "Earnings statistics"
    assert kinds.tone_for(earnings.KIND) != "grey"


def test_the_values_import_no_model():
    """A plugin installed beside Postulo builds its sources at import, before Django is set
    up in the installer's load check (#1299): the module holds no model and reads no setting."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(earnings.__file__).read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)]
    names = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in top
        for alias in node.names
    }

    assert not any("models" in name or name.startswith("django") for name in names), names


# ----------------------------------------------------------- nothing while nothing is on


def test_with_no_source_nothing_is_drawn_and_nothing_is_read(user, client, monkeypatch):
    def never(*_a, **_k):
        raise AssertionError("the listing's place was read with no source to ask")

    monkeypatch.setattr(listing_earnings, "country_of", never)
    listing = listing_for(user)

    assert earnings.sources(user) == []
    assert listing_earnings.panel(listing, user) is None
    page = page_of(client, user, listing)
    assert CARD not in page and "Earnings statistics" not in page

    application = apply_to_listing(listing, {})
    assert CARD not in client.get(application.get_absolute_url()).content.decode()


def test_a_source_switched_off_for_the_reader_draws_nothing(user, client, monkeypatch):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked))
    listing = listing_for(user)
    assert CARD in page_of(client, user, listing)
    asked.clear()

    PluginPolicy.objects.create(plugin="office", person=user, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()

    assert CARD not in page_of(client, user, listing)
    assert asked == [], "a source that is off is not asked"


# -------------------------------------------------------------- what the card says


def test_the_listing_page_draws_each_figure_with_what_it_is(user, client, monkeypatch):
    wide = "Non-manual workers (major groups 1 to 5)"
    install(
        monkeypatch,
        a_source(
            [
                figure(),
                figure(measure="median", amount=Decimal("20360"), group=wide),
                figure(measure="p10", amount=Decimal("12540"), group=wide),
                figure(measure="p90", amount=Decimal("48285"), group=wide),
                figure(
                    measure="minimum",
                    amount=Decimal("1073"),
                    period="month",
                    year="second half of 2026",
                    group="All employees",
                    note="Spread over twelve months.",
                ),
            ]
        ),
    )

    page = page_of(client, user, listing_for(user))
    card = card_of(page)

    assert card.startswith("Earnings statistics")
    assert "A statistic about many jobs, not about this one, and not a recommendation." in card
    assert "Country Portugal" in card
    assert (
        "Professionals (major group 2) Mean (the average) 36,066 EUR per year, gross (2022)"
    ) in card
    assert (
        f"{wide} Median (half earned less, half more) 20,360 EUR per year, gross (2022) "
        "One in ten earned less than 12,540 EUR per year, gross (2022) "
        "One in ten earned more than 48,285 EUR per year, gross (2022)"
    ) in card
    assert (
        "All employees Minimum wage 1,073 EUR per month, gross (second half of 2026) "
        "Spread over twelve months."
    ) in card
    assert "Source Statistics Office Survey of earnings 2022 (table-28)" in card
    assert "Employees of enterprises with ten or more employees." in card
    assert "Licence CC BY 4.0" in card
    assert 'href="https://example.org/table-28" rel="noopener noreferrer external"' in page
    assert "data-earnings-unmatched" not in page


@pytest.mark.parametrize("measure", earnings.MEASURES)
@pytest.mark.parametrize("period", earnings.PERIODS)
@pytest.mark.parametrize("basis", earnings.BASES)
def test_no_figure_is_drawn_without_its_measure_basis_period_year_and_group(
    user, measure, period, basis, monkeypatch
):
    """The guard against a bare number: whatever a source hands over, the row says which
    measure it is, gross or net, for which period and year, under the group's name."""
    install(monkeypatch, a_source([figure(measure=measure, period=period, basis=basis)]))

    panel = listing_earnings.panel(listing_for(user), user)
    (block,) = panel.blocks
    (group,) = block.groups
    (row,) = group.rows

    assert group.name == "Professionals (major group 2)"
    assert row.label == str(listing_earnings.MEASURES[measure]) and row.label
    assert basis in row.value and f"per {period}" in row.value
    assert "(2022)" in row.value and "36,066 EUR" in row.value
    assert panel.country_name == "Portugal"


def test_every_measure_period_and_basis_has_its_words():
    assert set(listing_earnings.MEASURES) == set(earnings.MEASURES)
    assert set(listing_earnings.VALUES) == {
        (period, basis) for period in earnings.PERIODS for basis in earnings.BASES
    }


#: Words a card that judged the salary, or told the person what to ask for, would use.
VERDICTS = (
    "above",
    "below",
    "higher",
    "lower",
    "more than the",
    "less than the",
    "negotiat",
    "ask for",
    "you should",
    "your offer",
    "your salary",
    "fair",
    "underpaid",
    "overpaid",
    "difference",
    "%",
)


def test_the_card_judges_nothing_and_recommends_nothing(user, client, monkeypatch):
    """With a salary on the listing and an offer on the application: the statistic sits
    beside them and says nothing about them."""
    install(monkeypatch, a_source([figure(), figure(measure="median", amount=Decimal("20360"))]))
    listing = listing_for(
        user, salary_min=Decimal("30000"), salary_max=Decimal("40000"), salary_currency="EUR"
    )
    application = apply_to_listing(listing, {})
    record_offer(application, base_amount=Decimal("50000"), currency="EUR", period="year")
    client.force_login(user)

    for address in (listing.get_absolute_url(), application.get_absolute_url()):
        page = client.get(address).content.decode()
        card = card_of(page).casefold()
        for word in VERDICTS:
            assert word not in card, f"{address}: the card says {word!r}"
        assert "not a recommendation" in card
        for own in ("30,000", "40,000", "50,000"):
            assert own not in card, "the person's own figure is not in the card"
        assert 'data-tone="green"' not in page[page.index(CARD) :].split("</dl>")[0]


def test_no_string_of_the_card_judges_or_recommends():
    """The same rule held on the source strings, so a new measure cannot bring a verdict in."""
    from pathlib import Path

    template = (
        Path(listing_earnings.__file__).parents[1] / "templates/cotton/earnings_reference.html"
    )
    words = [str(text) for text in listing_earnings.MEASURES.values()]
    words += [str(text) for text in listing_earnings.VALUES.values()]
    words += re.findall(r'translate "([^"]+)"', template.read_text(encoding="utf-8"))

    assert len(words) > 15
    for text in words:
        for word in ("negotiat", "ask for", "you should", "your offer", "your salary"):
            assert word not in text.casefold(), text


def test_an_amount_is_drawn_as_published_in_the_currency_it_was_published_in(
    user, client, monkeypatch
):
    """No conversion and no arithmetic: a kuna figure stays kuna beside a euro salary, an
    hourly figure keeps its cents, and a monthly one is not brought to a year."""
    install(
        monkeypatch,
        a_source(
            [
                figure(amount=Decimal("123456"), currency="HRK"),
                figure(amount=Decimal("10.21"), period="hour"),
                figure(amount=Decimal("1708"), period="month"),
            ]
        ),
    )
    listing = listing_for(user, location="Zagreb, HR", salary_min=Decimal("30000"))

    card = card_of(page_of(client, user, listing))

    assert "123,456 HRK per year, gross (2022)" in card
    assert "10.21 EUR per hour, gross (2022)" in card
    assert "1,708 EUR per month, gross (2022)" in card
    assert str(1708 * 12) not in card.replace(",", "") and "20,496" not in card


def test_the_amount_is_grouped_as_the_reader_reads_numbers(user, monkeypatch):
    install(monkeypatch, a_source())
    listing = listing_for(user)

    with translation.override("fr"):
        (row,) = listing_earnings.panel(listing, user).blocks[0].groups[0].rows

    assert re.search(r"36\s066 EUR", row.value), row.value


def test_what_a_source_hands_over_is_escaped(user, client, monkeypatch):
    install(
        monkeypatch,
        a_source(
            [figure(group="<script>alert(1)</script>", note="<b>bold</b>")],
            label="<i>Office</i>",
            citation="<u>cite</u>",
        ),
    )

    page = page_of(client, user, listing_for(user))

    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)" in page
    assert "<b>bold</b>" not in page and "<i>Office</i>" not in page and "<u>cite</u>" not in page


# ----------------------------------------------------------- matching the listing


def test_the_source_is_asked_with_the_code_the_country_and_the_stated_salary(user, monkeypatch):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked))

    listing_earnings.panel(listing_for(user, location="Kraków, PL"), user)
    listing_earnings.panel(
        listing_for(
            user,
            location="Kraków, PL",
            salary_min=Decimal("9000"),
            salary_currency="PLN",
            salary_period="month",
        ),
        user,
    )

    assert asked == [("2512", "PL", "", ""), ("2512", "PL", "month", "PLN")], (
        "a listing that states no salary has no period and no currency, whatever its fields "
        "default to"
    )


def test_the_application_page_asks_in_the_latest_offers_period_and_currency(
    user, client, monkeypatch
):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked))
    listing = listing_for(user, location="Kraków, PL", salary_min=Decimal("100000"))
    application = apply_to_listing(listing, {})
    client.force_login(user)

    assert CARD in client.get(application.get_absolute_url()).content.decode()
    assert asked == [("2512", "PL", "year", "EUR")], "no offer: the listing's salary"

    record_offer(application, base_amount=Decimal("9000"), currency="PLN", period="month")
    asked.clear()
    client.get(application.get_absolute_url())
    assert asked == [("2512", "PL", "month", "PLN")]

    record_offer(application, variable_pay="Terms to follow")
    asked.clear()
    client.get(application.get_absolute_url())
    assert asked == [("2512", "PL", "year", "EUR")], "an offer with no pay written down"


def test_the_offers_table_draws_no_statistic(user, client, monkeypatch):
    """Side by side, a statistic would be read as a verdict on each offer (#1257, D5)."""
    install(monkeypatch, a_source())
    application = apply_to_listing(listing_for(user), {})
    record_offer(application, base_amount=Decimal("50000"), currency="EUR", period="year")
    client.force_login(user)

    page = client.get(reverse("applications:offer_compare")).content.decode()

    assert CARD not in page and "Statistics Office" not in page


@pytest.mark.parametrize("location", ["Remote", "Somewhere nobody knows", "Lisboa, US", "PT1"])
def test_a_place_that_names_no_country_it_answers_for_draws_nothing(
    user, client, monkeypatch, location
):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked))
    listing = listing_for(user, location=location)
    PostalAddress.objects.create(
        owner=user, holder=listing.company, municipality="Lisboa", country="PT", is_primary=True
    )

    assert listing_earnings.panel(listing, user) is None
    assert CARD not in page_of(client, user, listing)
    assert asked == [], "a place that is not one is not replaced by the company's address"


def test_a_listing_with_no_place_is_where_its_company_is(user, monkeypatch):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked))
    listing = listing_for(user, location="")
    assert listing_earnings.panel(listing, user) is None, "no place and no address"

    PostalAddress.objects.create(
        owner=user, holder=listing.company, municipality="Porto", country="PT", is_primary=True
    )

    assert listing_earnings.panel(listing, user).country == "PT"
    assert asked == [("2512", "PT", "", "")]


def test_a_listing_whose_title_matches_no_group_says_the_figures_are_for_everyone(
    user, client, monkeypatch
):
    asked = Asked()
    install(monkeypatch, a_source([figure(group="All occupations")], asked=asked))

    page = page_of(client, user, listing_for(user, code=""))

    assert asked == [("", "PT", "", "")]
    assert "data-earnings-unmatched" in page
    assert (
        "This listing's title matches no group of occupations, so these are for all "
        "employees." in page
    )
    assert "All occupations" in page


def test_a_source_with_nothing_for_the_country_draws_nothing(user, client, monkeypatch):
    install(monkeypatch, a_source([]))

    assert CARD not in page_of(client, user, listing_for(user))


@pytest.fixture
def country_names(tmp_path, monkeypatch, cldr_in_hand):
    """The GeoNames tables of `tests/test_places.py`, for a place read by a country's name."""
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    (tmp_path / places.CITIES_FILE).write_text(TABLE, encoding="utf-8")
    (tmp_path / places.COUNTRIES_FILE).write_text(COUNTRIES, encoding="utf-8")
    places._index.cache_clear()
    yield
    places._index.cache_clear()


@pytest.mark.parametrize(
    ("typed", "country"),
    [
        ("Portugal", "PT"),
        ("Remote, Portugal", "PT"),
        ("Deutschland", "DE"),
        ("Remote; Germany", "DE"),
        ("Lisboa, Portugal", "PT"),
        ("Remote", ""),
        ("", ""),
        ("Anywhere, Atlantis", ""),
    ],
)
def test_a_place_that_is_a_country_and_no_town_is_read_as_one(country_names, typed, country):
    assert places.country_named(typed) == country


def test_a_listing_placed_by_a_country_alone_gets_the_card(country_names, user, monkeypatch):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked))

    assert listing_earnings.panel(listing_for(user, location="Remote, Portugal"), user)
    assert listing_earnings.panel(listing_for(user, location="Lisboa"), user).country == "PT"
    assert [country for _code, country, *_rest in asked] == ["PT", "PT"]


def test_without_the_country_table_a_name_is_no_country(tmp_path, monkeypatch):
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    places._index.cache_clear()
    try:
        assert places.country_named("Portugal") == ""
    finally:
        places._index.cache_clear()


# ------------------------------------------------------------------- the registry


def test_postulos_own_sources_come_first_and_a_key_is_claimed_once(user, monkeypatch, caplog):
    mine = a_source(key="mine", label="Mine")
    second = a_source(key="mine", label="A second claim")
    other = a_source(key="other", label="Other")
    install(monkeypatch, mine, name="first")
    install(monkeypatch, second, other, name="second")

    with caplog.at_level(logging.WARNING, logger="postulo.core.earnings"):
        found = earnings.sources(user)

    assert [source.label for source in found] == ["Mine", "Other"]
    assert "offered twice" in caplog.text
    assert [ref.source.key for ref in earnings.references("2512", "PT", user=user)] == [
        "mine",
        "other",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"offline": False},  # dials out and names no host
        {"hosts": ("example.org",)},  # offline and names a host
        {"licence": ""},
        {"label": ""},
        {"countries": ()},
        {"countries": ("pt",)},
        {"countries": ("PRT",)},
        {"countries": ["PT"]},
        {"key": ""},
        {"key": "x" * 41},
        {"figures": None},
        {"url": "http://example.org/table"},
        {"url": "javascript:alert(1)"},
    ],
)
def test_a_source_that_cannot_be_used_is_left_out(bad, monkeypatch, caplog):
    assert earnings._usable(a_source(**bad)) is False
    assert earnings._usable(a_source()) is True
    assert earnings._usable(a_source(offline=False, hosts=("api.example.org",)))
    assert earnings._usable("not a source") is False

    install(monkeypatch, a_source(**bad))
    with caplog.at_level(logging.WARNING, logger="postulo.core.earnings"):
        assert earnings.sources() == []
    assert "cannot be used" in caplog.text


def test_a_network_source_is_never_asked_by_a_page(user, client, monkeypatch):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked, offline=False, hosts=("api.example.org",)))

    assert earnings.sources(user), "it is a source"
    assert earnings.references("2512", "PT", user=user) == []
    assert CARD not in page_of(client, user, listing_for(user))
    assert asked == []


def test_a_source_that_raises_makes_a_page_without_the_card_and_not_a_broken_one(
    user, client, monkeypatch, caplog
):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    install(monkeypatch, a_source(figures=broken))
    listing = listing_for(user)

    with caplog.at_level(logging.ERROR, logger="postulo.core.earnings"):
        client.force_login(user)
        response = client.get(reverse("jobs:posting_detail", args=[listing.pk]))

    assert response.status_code == 200 and CARD not in response.content.decode()
    assert "failed" in caplog.text


@pytest.mark.parametrize(
    "bad",
    [
        {"measure": "average"},
        {"measure": ""},
        {"period": "day"},
        {"period": ""},
        {"basis": ""},
        {"basis": "before tax"},
        {"amount": 36066},
        {"amount": 36066.0},
        {"amount": Decimal("0")},
        {"amount": Decimal("-1")},
        {"amount": Decimal("NaN")},
        {"currency": "eur"},
        {"currency": "€"},
        {"year": ""},
        {"group": " "},
        {"exact": "yes"},
    ],
)
def test_a_figure_that_does_not_say_what_it_is_is_not_drawn(bad, user, monkeypatch, caplog):
    assert earnings.figure_problems(figure(**bad))
    assert earnings.figure_problems(figure()) == []
    assert earnings.figure_problems({"amount": 1})

    install(monkeypatch, a_source([figure(**bad), figure(year="2018")]))
    with caplog.at_level(logging.WARNING, logger="postulo.core.earnings"):
        (reference,) = earnings.references("2512", "PT", user=user)

    assert [kept.year for kept in reference.figures] == ["2018"], "the good one is kept"
    assert "cannot be drawn" in caplog.text


@pytest.mark.parametrize("country", ["", "pt ", "Portugal", "P", None])
def test_a_country_that_is_not_a_code_asks_nobody(country, monkeypatch):
    asked = Asked()
    install(monkeypatch, a_source(asked=asked))

    found = earnings.references("2512", country)

    assert (found == []) is (country != "pt ")
    assert all(code == "PT" for _isco, code, *_rest in asked)


# --------------------------------------------------- the published check (#1257)


def test_a_source_written_elsewhere_passes_the_published_check(monkeypatch):
    plugin = install(monkeypatch, a_source())()

    assert_is_an_earnings_source(plugin)


def test_the_published_check_asks_every_country_with_and_without_an_occupation():
    asked = Asked()
    plugin = type(
        "Office", (), {"name": "office", "kind": earnings.KIND, "sources": (a_source(asked=asked),)}
    )()

    assert_is_an_earnings_source(plugin)

    assert {country for _code, country, *_rest in asked} == {"PT", "PL", "HR"}
    assert {code for code, *_rest in asked} >= {"", "2512"}
    assert {period for *_first, period, _currency in asked} >= {"", "year", "month", "day"}


def test_the_published_check_never_asks_a_source_that_needs_the_network():
    asked = Asked()
    source = a_source(asked=asked, offline=False, hosts=("api.example.org",))
    plugin = type("Office", (), {"name": "o", "kind": earnings.KIND, "sources": (source,)})()

    assert_is_an_earnings_source(plugin)

    assert asked == []


@pytest.mark.parametrize(
    ("plugin", "says"),
    [
        (type("NoSources", (), {"name": "x", "kind": earnings.KIND}), "does not provide"),
        (type("Kind", (), {"name": "x", "kind": "feature", "sources": ()}), "its kind is"),
        (type("Empty", (), {"name": "x", "kind": earnings.KIND, "sources": ()}), "no source"),
        (
            type(
                "Bad", (), {"name": "x", "kind": earnings.KIND, "sources": (a_source(licence=""),)}
            ),
            "names no licence",
        ),
        (
            type(
                "Twice",
                (),
                {"name": "x", "kind": earnings.KIND, "sources": (a_source(), a_source())},
            ),
            "share a key",
        ),
        (
            type("Nothing", (), {"name": "x", "kind": earnings.KIND, "sources": (a_source([]),)}),
            "gave nothing for a country it declares",
        ),
        (
            type(
                "Bare",
                (),
                {
                    "name": "x",
                    "kind": earnings.KIND,
                    "sources": (a_source([figure(measure="typical")]),),
                },
            ),
            "its measure is 'typical'",
        ),
        (
            type(
                "Float",
                (),
                {
                    "name": "x",
                    "kind": earnings.KIND,
                    "sources": (a_source([figure(amount=1.5)]),),
                },
            ),
            "not a Decimal",
        ),
    ],
)
def test_the_published_check_fails_on_what_postulo_would_not_draw(plugin, says):
    with pytest.raises(AssertionError) as raised:
        assert_is_an_earnings_source(plugin())

    assert says in str(raised.value)
