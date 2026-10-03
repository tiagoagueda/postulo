"""The floor under every capture: what a page with no structured data says about itself (#267).

Each case is a small page in ``data/page_metadata/``, written for the purpose and showing one
thing boards do: a title line with the employer and the place packed into it, a page drawn
by a script, a Twitter card, and the pay and the closing date written out in English,
Portuguese and French. The negative cases matter as much as the others -- a word on the page
that labels nothing, a date that reads two ways, two salaries that disagree -- because the
rule this tier keeps is that an empty field is a small cost and a wrong one is not.
"""

from __future__ import annotations

import datetime as dt
import pathlib
from decimal import Decimal

import pytest

from postulo.jobs import places
from postulo.plugins.builtin import PageMetadataSource, patterns, titles

PAGES = pathlib.Path(__file__).parent / "data" / "page_metadata"

#: Where the pages are said to come from. A host with no word that could be a title part.
URL = "https://jobs.example.org/openings/42"


def read(name: str, url: str = URL):
    return PageMetadataSource().parse(url, (PAGES / f"{name}.html").read_text(encoding="utf-8"))


def city_row(name: str, country: str) -> str:
    """One line of GeoNames' table, in its own column order."""
    columns = ["1", name, name, "", "38.7", "-9.1", "P", "PPLC", country, "", "", "", "", ""]
    return "\t".join([*columns, "500000", "0", "", "UTC", "2026-01-01"])


@pytest.fixture(autouse=True)
def no_cities(tmp_path, monkeypatch):
    """No table of cities, whatever this machine has downloaded: a place is named only
    where a test says the table knows it."""
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    for cached in (places._index, places._countries):
        cached.cache_clear()
    yield tmp_path
    for cached in (places._index, places._countries):
        cached.cache_clear()


@pytest.fixture
def cities(no_cities):
    """A table that knows Lisbon and Porto, and nothing else."""
    rows = [city_row("Lisbon", "PT"), city_row("Porto", "PT")]
    (no_cities / places.CITIES_FILE).write_text("\n".join(rows) + "\n", encoding="utf-8")
    for cached in (places._index, places._countries):
        cached.cache_clear()


# ------------------------------------------------------------------ the title line


def test_the_heading_names_the_job_and_at_names_the_employer(cities):
    data = read("title-at")

    assert data.title == "Senior Plumber"
    assert data.company_name == "Black Mesa"
    assert data.location == "Lisbon", "the table of cities knows it"


def test_without_the_table_of_cities_no_place_is_named():
    """An instance that has not downloaded the table names no place rather than guess."""
    data = read("title-at")

    assert data.company_name == "Black Mesa"
    assert data.location == ""


def test_with_no_single_heading_the_job_is_the_part_left_over(cities):
    """The site's name and the place are recognised, and the one part left is the job."""
    data = read("title-parts", url="https://careers.aperture.example/jobs/7")

    assert data.title == "Test Chamber Engineer"
    assert data.location == "Lisbon"
    assert data.company_name == "Aperture Science", "the site's name, on its own site"


def test_what_is_left_over_stays_whole_when_nothing_recognises_it():
    """Without the table, "Lisbon" is not known to be a place and is not dropped."""
    data = read("title-parts", url="https://careers.aperture.example/jobs/7")

    assert data.title == "Test Chamber Engineer – Lisbon"
    assert data.location == ""


def test_a_part_nothing_vouches_for_is_left_alone(cities):
    data = read("title-unvouched")

    assert data.title == "Test Chamber Engineer"
    assert data.company_name == "", "one of two parts, and nothing says which"
    assert data.location == "", "'Remote' is not a place"


def test_a_line_naming_two_places_names_neither(cities):
    """Employers are named after towns often enough that two known places in one line is a
    line in which one of them is probably somebody's name."""
    reading = titles.read(
        declared=["Engineer - Porto - Lisbon"],
        heading="Engineer",
        is_place=lambda text: places.resolve(text) is not None,
    )

    assert reading.place == ""
    assert reading.title == "Engineer"


def test_the_employer_after_at_is_read_only_after_the_job():
    """A line that merely contains "at" names nobody."""
    reading = titles.read(declared=["Look at our openings - Aperture Science"], heading="")

    assert reading.company == ""


@pytest.mark.parametrize(
    "line,parts",
    [
        ("Engineer - Aperture Science | Board", ["Engineer", "Aperture Science", "Board"]),
        ("Engineer — Lisbon · Aperture Science", ["Engineer", "Lisbon", "Aperture Science"]),
        ("Front-end Developer - Acme", ["Front-end Developer", "Acme"]),
        ("Internship 2025–2026", ["Internship 2025–2026"]),
        ("| Engineer |", ["Engineer"]),
    ],
)
def test_a_line_splits_on_the_separators_boards_use_and_nowhere_else(line, parts):
    assert [part for _separator, part in titles.split(line)] == parts


@pytest.mark.parametrize(
    "part,host,expected",
    [
        ("Indeed.com", "pt.indeed.com", True),
        ("LinkedIn", "www.linkedin.com", True),
        ("Careers", "careers.aperture.example", True),
        ("ASP.NET", "careers.aperture.example", False),
        ("com", "careers.aperture.example", False),
        ("Aperture Science", "careers.aperture.example", False),
    ],
)
def test_the_site_signing_a_line_is_recognised_by_its_address(part, host, expected):
    assert titles.is_site(part, host=host) is expected


# ------------------------------------------------------------------ what it declares


def test_a_page_drawn_by_a_script_is_read_from_what_it_declares():
    data = read("script-shell", url="https://careers.aperture.example/jobs/7")

    assert data.title == "Test Chamber Engineer", "from twitter:title, less the site's name"
    assert data.company_name == "Aperture Science"
    assert data.description.startswith("Aperture Science builds"), "og:description"


def test_a_twitter_card_s_pairs_are_read_like_a_line_of_the_page():
    data = read("twitter-card")

    assert (data.salary_min, data.salary_max) == (Decimal(40000), Decimal(48000))
    assert data.salary_currency == "GBP"
    assert data.salary_period == "year"
    assert data.closes_at == dt.date(2026, 10, 30)
    assert data.company_name == "", "a part of the title nothing vouches for"


# ------------------------------------------------------------------ pay and a closing date


@pytest.mark.parametrize(
    "name,low,high,currency,period,closes",
    [
        ("advert-en-gb", 45000, 55000, "GBP", "year", dt.date(2026, 4, 3)),
        ("advert-en-us", 120000, 140000, "USD", "year", dt.date(2026, 4, 3)),
        ("advert-pt-pt", Decimal("1385.99"), None, "EUR", "month", dt.date(2026, 10, 30)),
        ("advert-fr", 45000, 55000, "EUR", "year", dt.date(2026, 10, 30)),
        ("up-to", None, 50000, "EUR", "", dt.date(2026, 10, 30)),
    ],
)
def test_the_pay_and_the_closing_date_are_read_in_the_page_s_own_language(
    name, low, high, currency, period, closes
):
    data = read(name)

    assert (data.salary_min, data.salary_max) == (low, high)
    assert data.salary_currency == currency
    assert data.salary_period == period
    assert data.closes_at == closes


def test_what_reads_two_ways_is_not_read():
    """English with no country: a day-and-month date and a bare "$" are both guesses."""
    data = read("advert-en")

    assert data.closes_at is None
    assert (data.salary_min, data.salary_max, data.salary_currency) == (None, None, "")


@pytest.mark.parametrize("name", ["not-a-salary", "two-salaries"])
def test_words_that_label_nothing_or_disagree_are_no_reading(name):
    data = read(name)

    assert (data.salary_min, data.salary_max, data.salary_currency) == (None, None, "")
    assert data.salary_period == ""
    assert data.closes_at is None


@pytest.mark.parametrize(
    "written,value",
    [
        ("1.385,99", "1385.99"),
        ("1,385.99", "1385.99"),
        ("45.000", "45000"),
        ("45,000", "45000"),
        ("45 000", "45000"),
        ("45 000", "45000"),
        ("1'250'000", "1250000"),
        ("12,50", "12.50"),
        ("1.234.567", "1234567"),
    ],
)
def test_a_figure_is_read_however_it_is_grouped(written, value):
    assert patterns.figure(written) == Decimal(value)


@pytest.mark.parametrize(
    "tag,written",
    [
        ("pt-PT", "dmy"),
        ("pt", "dmy"),
        ("fr", "dmy"),
        ("de", "dmy"),
        ("en-GB", "dmy"),
        ("en-US", "mdy"),
        # However the page spelt it: a tag says the same thing in any case.
        ("en-us", "mdy"),
        ("en", ""),
        ("", ""),
    ],
)
def test_the_order_a_date_is_written_in_comes_from_the_page_s_language(tag, written):
    assert patterns.order(tag) == written


def test_a_page_s_language_tag_is_a_stranger_s_text():
    """Only one of Postulo's own languages is ever asked for a translation."""
    assert patterns.tag_of("pt_PT") == "pt-PT"
    assert patterns.tag_of("en-US,en;q=0.9") == "en-US"
    assert patterns.tag_of("pt-br") == "pt-BR", "written as Postulo writes one (#337)"
    assert patterns.tag_of("<script>") == ""
    assert patterns.tag_of("x" * 60) == ""
    assert patterns.spoken("pt-zz") == ("pt-PT", "pt-BR")
    assert patterns.spoken("xx-evil") == ()


@pytest.mark.parametrize(
    "written,period",
    [
        ("You get a monthly travel allowance and a salary of €40,000.", ""),
        ("Hourly paid overtime on top of a salary of €40,000.", ""),
        ("Daily standups. Salary: €40,000", ""),
        ("Monthly team lunches | Salary €3,500", ""),
        ("Monthly salary: €3,500", "month"),
        ("Gross annual salary: €40,000", "year"),
        ("Salary (monthly): €3,500", "month"),
        ("Salary: €40,000 per month", "month"),
    ],
)
def test_a_period_is_the_one_the_figure_s_label_states_not_one_earlier_on_the_line(written, period):
    """#420: the whole line before the label was searched, so a "daily" anywhere won."""
    assert patterns.salary(written, "en")[3] == period


def test_a_long_run_of_unicode_spaces_does_not_make_the_search_quadratic():
    """#420: 50,000 no-break spaces took minutes; the label is bounded now."""
    import time

    started = time.perf_counter()
    found = patterns.salary("Benefits" + "\xa0" * 50_000 + "Salary: €40,000", "en")
    assert found == (40000, None, "EUR", "")
    assert patterns.pay_in("\xa0" * 50_000 + "€40,000", "en")[3] == ""
    assert time.perf_counter() - started < 2
