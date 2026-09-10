"""A vocabulary worth picking from, picked as labels, and what follows from both (#141).

> company sectores seams incomplete, you shoud looke for a more extenside and general area
> of activity list, and the selection should be in the form of labels, not tick boxes

Two halves, filed apart on purpose and answered apart: the list is NACE Rev. 2.1 (#140) and
the picker is chips (#139). This is the issue where they meet, and what it is really about
is the **consequences** — a cell that has to look right at one industry and at twelve, a
vocabulary page that can no longer be read by scrolling, a filter that has to work over a
long list, and a promise that none of it renames a word somebody wrote themselves.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, Industry

pytestmark = pytest.mark.django_db


def a_company(user, name: str, *industries: str) -> Company:
    company = Company.objects.create(owner=user, name=name)
    company.industries.set(Industry.named(user, list(industries)))
    return company


def row_for(html: str, company: Company) -> str:
    return html.split(f'id="company-{company.pk}"')[1].split("</tr>")[0]


# ---------------------------------------------------------- the two halves, together


def test_a_person_picks_from_the_classification_and_gets_a_label(client, user):
    """The whole of the ask, in one pass: type a NACE division on the company form, and it
    comes back as a chip with the code recorded beside it.
    """
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {"name": "Aperture Science", "new_industries": "Mining of metal ores"},
    )

    industry = company.industries.get()
    assert industry.name == "Mining of metal ores"
    assert industry.code == "07"

    html = client.get(reverse("jobs:company_list")).content.decode()
    assert "chip" in row_for(html, company)


def test_a_word_somebody_wrote_themselves_is_never_renamed(client, user):
    """Their industries are their words. A new vocabulary is offered beside them, never
    instead of them, and never silently.
    """
    company = a_company(user, "Aperture Science", "Fintech")

    industry = company.industries.get()

    assert industry.name == "Fintech"
    assert industry.code == "", "and no classification is asserted over it"


# ------------------------------------------------- the cell, at one and at twelve


def test_one_industry_is_one_label(client, user):
    company = a_company(user, "Aperture Science", "Software")
    client.force_login(user)

    row = row_for(client.get(reverse("jobs:company_list")).content.decode(), company)

    assert row.count("chip-text") == 1


def test_three_are_three(client, user):
    """A bank that is also an insurer and a software house is all three, and counting it
    under one would be a lie the figures repeat.
    """
    company = a_company(user, "Aperture Science", "Banking", "Insurance", "Software")
    client.force_login(user)

    row = row_for(client.get(reverse("jobs:company_list")).content.decode(), company)

    for name in ("Banking", "Insurance", "Software"):
        assert name in row


def test_twelve_do_not_push_every_other_column_off_the_screen(client, user):
    """A conglomerate is more than three, and the cell is already narrow."""
    names = [f"Field {index}" for index in range(12)]
    company = a_company(user, "Conglomerate", *names)
    client.force_login(user)

    row = row_for(client.get(reverse("jobs:company_list")).content.decode(), company)

    assert row.count("chip-text") == 4, "four are shown"
    assert "and 8 more" in row, "and the rest are counted"


def test_a_company_with_none_still_says_so(client, user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    row = row_for(client.get(reverse("jobs:company_list")).content.decode(), company)

    assert "chip" not in row
    assert "—" in row


# ------------------------------------------------------- filtering, over a long list


def test_the_filter_narrows_by_typing_rather_than_by_a_menu_of_eighty_eight(client, user):
    """`88 divisions in a select is a different control from 32` — so it is not a select.

    The column filters as text against the industry names, which is the same control at
    thirty-two names and at three hundred, and the only one that stays usable at both.
    """
    from postulo.jobs.tables import CompaniesTable

    column = next(c for c in CompaniesTable.columns if c.key == "industry")
    assert column.filter == "text"
    assert column.lookups == ("industries__name",)

    a_company(user, "Aperture Science", "Mining of metal ores")
    a_company(user, "Black Mesa", "Software")
    client.force_login(user)

    html = client.get(reverse("jobs:company_list") + "?industry=mining").content.decode()

    assert "Aperture Science" in html
    assert "Black Mesa" not in html


# -------------------------------------------------- the vocabulary page, at length


def test_the_page_holds_only_what_has_actually_been_used(client, user):
    """The classification is a list of suggestions, not a list of rows. That is what made
    it safe to make the suggestions long.
    """
    a_company(user, "Aperture Science", "Software")

    assert Industry.objects.for_user(user).count() == 1


def test_a_long_vocabulary_can_be_searched(client, user):
    a_company(user, "Aperture Science", *[f"Field {index}" for index in range(12)])
    a_company(user, "Black Mesa", "Mining of metal ores")
    client.force_login(user)

    html = client.get(reverse("jobs:industry_list") + "?q=mining").content.decode()

    assert "Mining of metal ores" in html
    assert "Field 1</bdi>" not in html


def test_the_search_finds_a_division_by_its_code(client, user):
    """Somebody handed a code by an employment office should be able to type the code."""
    a_company(user, "Aperture Science", "Mining of metal ores")
    client.force_login(user)

    html = client.get(reverse("jobs:industry_list") + "?q=07").content.decode()

    assert "Mining of metal ores" in html


def test_the_box_is_not_offered_over_a_list_of_five(client, user):
    """A control above five rows is a control asking to be ignored."""
    a_company(user, "Aperture Science", "Software", "Research")
    client.force_login(user)

    html = client.get(reverse("jobs:industry_list")).content.decode()

    assert 'id="industry-search"' not in html


def test_the_code_is_shown_beside_the_persons_own_word_for_it(client, user):
    """What the standard bought beyond coverage, made visible where somebody manages them."""
    a_company(user, "Aperture Science", "Mining of metal ores")
    client.force_login(user)

    html = client.get(reverse("jobs:industry_list")).content.decode()

    assert "Mining of metal ores" in html
    assert ">07<" in html


def test_merging_is_reachable_from_the_row(client, user):
    """It is exactly the tool somebody needs after picking from a standard list beside
    their own words, so it should not be hidden behind a word that does not mention it.
    """
    a_company(user, "Aperture Science", "Software")
    client.force_login(user)

    html = client.get(reverse("jobs:industry_list")).content.decode()

    assert "Edit or merge" in html


def test_a_search_that_finds_nothing_says_why(client, user):
    a_company(user, "Aperture Science", *[f"Field {index}" for index in range(12)])
    client.force_login(user)

    html = client.get(reverse("jobs:industry_list") + "?q=nothing at all").content.decode()

    assert "fields you have actually used" in html


def test_somebody_elses_vocabulary_is_not_searchable(client, user, other_user):
    a_company(other_user, "Aperture Science", "Software")
    client.force_login(user)

    html = client.get(reverse("jobs:industry_list") + "?q=software").content.decode()

    assert "Software" not in html


# ---------------------------------------------------------- what counts by industry


def test_a_company_in_three_fields_still_counts_in_three(client, user):
    """The widget, the insights breakdown and *the same figures by field* all inherit the
    longer list, and the rule they were written with does not change.
    """
    from postulo.applications.analytics import build

    company = a_company(user, "Aperture Science", "Banking", "Insurance", "Software")
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import JobPosting

    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)

    rows = {row.name: row for row in build(user).industries}

    assert {"Banking", "Insurance", "Software"} <= set(rows)
    assert all(rows[name].applied == 1 for name in ("Banking", "Insurance", "Software"))
