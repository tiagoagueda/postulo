"""The bundled `earnings` plugin: Eurostat's figures beside a listing (#1257).

Read against the file that ships with Postulo, which is real data: the figures asserted here
are the ones Eurostat's API answered on the day the file records, and a refresh that changes
one is a change worth seeing in a test.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

from postulo.applications.services import apply_to_listing, create_listing, record_offer
from postulo.core import earnings
from postulo.core.memo import forget_decisions
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import registry
from postulo.plugins.earnings import Earnings, eurostat, reader
from postulo.plugins.models import PluginPolicy
from postulo.plugins.testing import assert_is_an_earnings_source

pytestmark = pytest.mark.django_db

CARD = "data-earnings-reference"
SHIPPED = reader.SHIPPED_DIR / reader.FILE
ROOT = Path(__file__).resolve().parents[1] / "src" / "postulo"


@pytest.fixture(autouse=True)
def shipped_file(tmp_path, monkeypatch):
    """The shipped file, and an empty directory where a refreshed one would be."""
    monkeypatch.setattr(reader, "data_dir", lambda: tmp_path)
    reader.forget()
    yield
    reader.forget()


@pytest.fixture
def on(db):
    """The plugin switched on for the instance, as an administrator would."""
    PluginPolicy.objects.filter(plugin="earnings", person__isnull=True).delete()
    forget_decisions()


def plugin() -> Earnings:
    return registry.find_plugin("earnings-source", "earnings")


def listing_for(person, *, location="Lisboa, PT", code="2512", **more):
    company, _made = Company.objects.get_or_create(owner=person, name="Acme")
    listing = create_listing(
        person,
        company=company,
        posting_data={"title": "Software developer", "location": location, **more},
    )
    JobPosting.objects.filter(pk=listing.pk).update(isco_code=code)
    listing.refresh_from_db()
    return listing


def page_of(client, person, listing) -> str:
    client.force_login(person)
    return client.get(reverse("jobs:posting_detail", args=[listing.pk])).content.decode()


def said(figures) -> list[tuple]:
    return [(f.measure, f.amount, f.currency, f.period, f.year, f.group) for f in figures]


# ----------------------------------------------------------------------- the plugin


def test_it_is_a_shipped_offline_licensed_earnings_source():
    found = plugin()
    (source,) = found.sources

    assert found.name == "earnings" and found.kind == "earnings-source"
    assert source.offline and not source.hosts
    assert "CC BY 4.0" in str(source.licence) and "Eurostat" in str(source.licence)
    assert source.url.startswith("https://ec.europa.eu/eurostat/databrowser/view/earn_ses22_28")
    assert "earn_mw_cur" in str(source.citation) and "2022" in str(source.citation)
    assert_is_an_earnings_source(found)


def test_it_starts_off_for_the_instance(user, client):
    """D1: a statistic beside a salary is the administrator's choice (migration 0019)."""
    row = PluginPolicy.objects.get(plugin="earnings", person__isnull=True)

    assert row.state == "off"
    assert earnings.sources(user) == []
    assert CARD not in page_of(client, user, listing_for(user))


def test_switched_on_the_listing_page_draws_eurostats_figures(on, user, client):
    page = page_of(client, user, listing_for(user))
    card = " ".join(page[page.index(CARD) :].split("</aside>")[0].split())

    for words in (
        "Professionals (ISCO-08 major group 2)",
        "36,066 EUR per year, gross (2022)",
        "Non-manual workers (ISCO-08 major groups 1 to 5)",
        "20,360 EUR per year, gross (2022)",
        "12,540 EUR per year, gross (2022)",
        "48,285 EUR per year, gross (2022)",
        "National minimum wage",
        "1,073 EUR per month, gross (second half of 2026)",
        "Paid in fourteen instalments a year",
        "earn_ses22_28",
        "CC BY 4.0",
    ):
        assert words in card, words
    assert 'href="https://ec.europa.eu/eurostat/databrowser/view/earn_ses22_28/' in card


def test_off_the_pages_render_without_the_card(user, client):
    listing = listing_for(user)
    application = apply_to_listing(listing, {})
    client.force_login(user)

    for address in (listing.get_absolute_url(), application.get_absolute_url()):
        response = client.get(address)
        assert response.status_code == 200 and CARD not in response.content.decode()


def test_absent_the_pages_render_without_the_card(on, user, client, monkeypatch):
    monkeypatch.setitem(registry._builtin, "earnings-source", [])
    registry._cache.pop("earnings-source", None)
    try:
        listing = listing_for(user)
        application = apply_to_listing(listing, {})
        client.force_login(user)
        for address in (listing.get_absolute_url(), application.get_absolute_url()):
            response = client.get(address)
            assert response.status_code == 200 and CARD not in response.content.decode()
    finally:
        registry._cache.pop("earnings-source", None)


def test_core_imports_nothing_of_the_plugin():
    """Core names `core.earnings` only; the package is reached through the registry."""
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(("plugins/earnings/", "plugins/management/commands/")) or (
            relative == "plugins/apps.py"
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "plugins.earnings" not in node.module, relative
            if isinstance(node, ast.Import):
                assert not any("plugins.earnings" in a.name for a in node.names), relative


# ------------------------------------------------------------------ what is answered


def test_a_unit_group_gets_its_major_groups_mean_and_its_wider_groups_median():
    found = said(reader.figures("2512", "PT"))

    assert found[:4] == [
        ("mean", Decimal("36066"), "EUR", "year", "2022", "Professionals (ISCO-08 major group 2)"),
        (
            "median",
            Decimal("20360"),
            "EUR",
            "year",
            "2022",
            "Non-manual workers (ISCO-08 major groups 1 to 5)",
        ),
        ("p10", Decimal("12540"), "EUR", "year", "2022", found[1][5]),
        ("p90", Decimal("48285"), "EUR", "year", "2022", found[1][5]),
    ]
    assert found[4][:4] == ("minimum", Decimal("1073"), "EUR", "month")


def test_no_figure_of_the_earlier_wave_is_drawn_beside_the_latest():
    """D4: the 2018 median of the single group is in the file and never answered."""
    document = json.loads(SHIPPED.read_text(encoding="utf-8"))
    assert document["countries"]["PT"]["groups"]["2"]["year"]["median"]["2018"]["EUR"] == 26072

    for code in ("2512", "", "7111", "9112"):
        for period in ("year", "month", "hour", "day", ""):
            years = {f.year for f in reader.figures(code, "PT", period)}
            assert "2018" not in years and "2022" in years


@pytest.mark.parametrize(
    ("code", "wide"),
    [
        ("1120", "1-5"),
        ("5223", "1-5"),
        ("6111", "6-8"),
        ("7111", "6-8"),
        ("8322", "6-8"),
        ("9112", "7-9"),
    ],
)
def test_each_major_group_falls_back_to_the_wider_group_eurostat_publishes(code, wide):
    groups = [f.group for f in reader.figures(code, "DE") if f.measure == "median"]

    assert groups == [str(reader.GROUP_NAMES[wide])]


@pytest.mark.parametrize("code", ["", "0110", "x"])
def test_no_code_or_the_armed_forces_get_the_figures_for_all_employees(code):
    found = reader.figures(code, "PT")

    assert {f.group for f in found if f.measure != "minimum"} == {"All employees"}
    assert [f.measure for f in found] == ["mean", "median", "p10", "p90", "minimum"]


def test_a_monthly_listing_gets_monthly_figures_and_none_multiplied():
    found = reader.figures("2512", "PT", "month", "EUR")

    assert {f.period for f in found} == {"month"}
    assert said(found)[0][:2] == ("mean", Decimal("2323"))


def test_a_daily_listing_gets_the_monthly_and_hourly_figures_under_their_own_names():
    found = reader.figures("2512", "PT", "day")

    assert [f.period for f in found if f.measure == "mean"] == ["month", "hour"]
    assert Decimal("14.18") in [f.amount for f in found]


def test_a_listing_in_zloty_gets_the_national_figure_and_one_in_euro_the_euro_figure():
    in_zloty = said(reader.figures("2512", "PL", "year", "PLN"))
    in_euro = said(reader.figures("2512", "PL", "year", "EUR"))

    assert in_zloty[0][:3] == ("mean", Decimal("98124"), "PLN")
    assert in_euro[0][:3] == ("mean", Decimal("20939"), "EUR")
    # The median is published in euro only, and stays so: nothing is converted.
    assert in_zloty[1][2] == "EUR"
    assert in_zloty[-1][:3] == ("minimum", Decimal("4806"), "PLN")


def test_croatias_2022_figures_are_in_kuna_and_never_shown_as_euro():
    in_kuna = said(reader.figures("2512", "HR", "year", "HRK"))
    in_euro = said(reader.figures("2512", "HR", "year", "EUR"))

    assert in_kuna[0][:3] == ("mean", Decimal("185479"), "HRK")
    assert in_euro[0][:3] == ("mean", Decimal("24616"), "EUR")
    assert in_euro[-1][:3] == ("minimum", Decimal("1050"), "EUR"), "2026: Croatia uses the euro"


def test_bulgarias_minimum_wage_is_in_euro_though_cldr_still_says_lev():
    minimum = reader.figures("", "BG", "year", "BGN")[-1]

    assert (minimum.measure, minimum.amount, minimum.currency) == ("minimum", Decimal("620"), "EUR")


def test_a_country_with_no_minimum_wage_leaves_the_line_out():
    assert [f for f in reader.figures("2512", "DK") if f.measure == "minimum"] == []
    assert reader.figures("2512", "DK")


@pytest.mark.parametrize("country", ["GB", "US", "TR", "JP", "EL", ""])
def test_a_country_outside_the_data_gets_nothing(country):
    assert reader.figures("2512", country) == []
    assert country not in reader.countries()


# ------------------------------------------------------------------- the pages


def test_a_krakow_listing_with_an_offer_in_zloty_a_month(on, user, client):
    """The application page reads the latest offer's period and currency: Poland's monthly
    mean for professionals as published in zloty, and no yearly figure beside it."""
    listing = listing_for(user, location="Kraków, PL", salary_min=Decimal("100000"))
    application = apply_to_listing(listing, {})
    record_offer(application, base_amount=Decimal("9000"), currency="PLN", period="month")
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    assert "8,013 PLN per month, gross (2022)" in page
    assert "per year, gross" not in page[page.index(CARD) :]
    assert "9,000" not in page[page.index(CARD) :]


def test_a_listing_with_no_country_draws_nothing(on, user, client):
    assert CARD not in page_of(client, user, listing_for(user, location="Remote"))


# --------------------------------------------------------------- the file itself


def test_the_shipped_file_is_one_the_reader_reads():
    document = json.loads(SHIPPED.read_text(encoding="utf-8"))

    assert eurostat.problems(document) == []
    assert document["publisher"] == "Eurostat" and document["licence"] == "CC BY 4.0"
    assert document["wave"] == 2022 and document["retrieved"]
    assert set(document["datasets"]) == set(eurostat.queries(2022))


def test_every_amount_is_positive_every_currency_three_letters_and_every_country_allowed():
    document = json.loads(SHIPPED.read_text(encoding="utf-8"))
    allowed = {eurostat.RENAMED.get(code, code) for code in eurostat.ALLOWED}

    assert set(document["countries"]) <= allowed
    assert {"GB", "UK", "US", "EL"}.isdisjoint(document["countries"])
    for entry in document["countries"].values():
        amounts = [
            (currency, amount)
            for periods in entry["groups"].values()
            for measures in periods.values()
            for years in measures.values()
            for amounts in years.values()
            for currency, amount in amounts.items()
        ] + list(entry.get("minimum", {}).get("month", {}).items())
        for currency, amount in amounts:
            assert len(currency) == 3 and currency.isupper() and currency.isalpha()
            assert amount > 0 and not isinstance(amount, bool)


def test_the_countries_are_the_ones_the_plan_read_from_the_api():
    assert set(reader.countries()) == set(
        "BE BG CZ DK DE EE IE GR ES FR HR IT CY LV LT LU HU MT NL AT PL PT RO SI SK FI SE "
        "IS NO CH BA MK AL RS".split()
    )


def test_a_refreshed_file_retrieved_later_is_read_and_an_unreadable_one_passed_over(tmp_path):
    from postulo.plugins.earnings import checks

    document = json.loads(SHIPPED.read_text(encoding="utf-8"))
    document["retrieved"] = "2099-01-01"
    document["countries"]["PT"]["groups"]["2"]["year"]["mean"]["2022"]["EUR"] = 1
    (tmp_path / reader.FILE).write_text(json.dumps(document), encoding="utf-8")
    reader.forget()
    assert reader.figures("2512", "PT")[0].amount == Decimal("1")
    assert checks.earnings_file_readable(None) == []

    (tmp_path / reader.FILE).write_text("{not json", encoding="utf-8")
    reader.forget()
    assert reader.figures("2512", "PT")[0].amount == Decimal("36066")
    assert [w.id for w in checks.earnings_file_readable(None)] == ["postulo.W050"]


def test_a_running_server_reads_a_refresh_written_by_another_process(tmp_path):
    """`fetch_earnings` runs in its own process; the server's copy is not told, so the file
    on disk is what says it changed."""
    assert reader.figures("2512", "PT")[0].amount == Decimal("36066")

    document = json.loads(SHIPPED.read_text(encoding="utf-8"))
    document["retrieved"] = "2099-01-01"
    document["countries"]["PT"]["groups"]["2"]["year"]["mean"]["2022"]["EUR"] = 2
    (tmp_path / reader.FILE).write_text(json.dumps(document), encoding="utf-8")

    assert reader.figures("2512", "PT")[0].amount == Decimal("2")


def test_an_older_refreshed_file_does_not_hide_the_shipped_one(tmp_path):
    document = json.loads(SHIPPED.read_text(encoding="utf-8"))
    document["retrieved"] = "2000-01-01"
    document["countries"]["PT"]["groups"]["2"]["year"]["mean"]["2022"]["EUR"] = 1
    (tmp_path / reader.FILE).write_text(json.dumps(document), encoding="utf-8")
    reader.forget()

    assert reader.figures("2512", "PT")[0].amount == Decimal("36066")


def test_with_no_readable_file_there_is_no_source_and_the_check_says_so(monkeypatch, tmp_path):
    from postulo.plugins.earnings import checks

    monkeypatch.setattr(reader, "SHIPPED_DIR", tmp_path / "nothing")
    reader.forget()

    assert plugin().sources == ()
    assert [w.id for w in checks.earnings_file_readable(None)] == ["postulo.W051"]
