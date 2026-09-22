"""A view somebody rebuilds every week should be one they can keep (#259).

A saved view is a name for a query string, kept on the profile beside the column widths,
carrying the columns it was saved with. Most of what is held here is the risk the issue
names: a view saved against a table that has since changed must degrade to what it can
still honour and say so, never fail. The rest is that the URL stays the durable form -- a
view is a link, choosing one changes nothing stored, and a bookmark keeps working.
"""

from __future__ import annotations

import pytest
from django.http import QueryDict
from django.test import RequestFactory
from django.urls import reverse

from postulo.applications.tables import ApplicationsTable
from postulo.core import tables
from postulo.jobs.models import Company
from postulo.jobs.tables import CompaniesTable

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def table_for(user, cls=CompaniesTable, query: str = "", settings=None):
    request = RequestFactory().get(f"/jobs/companies/?{query}")
    request.user = user
    return cls(request, settings)


# ---------------------------------------------------------------------- keeping


def test_a_view_is_the_query_string_under_a_name_with_the_columns_of_the_moment():
    kept = CompaniesTable.save_view(
        {}, "  Quiet  ones ", "location=mexico&sort=-name&page=3&saved=old", ["name", "location"]
    )
    (view,) = CompaniesTable._views_of(kept)
    assert view.name == "Quiet ones" and view.slug == "quiet-ones"
    assert QueryDict(view.query).dict() == {"location": "mexico", "sort": "-name"}, (
        "the page and the view's own name are not part of the question"
    )
    assert view.columns == ("name", "location")
    assert not view.default


def test_saving_under_a_name_already_taken_replaces_and_keeps_the_default_flag():
    first = CompaniesTable.save_view({}, "Weekly", "location=mexico", ["name"])
    first = CompaniesTable.make_default(first, "weekly")
    again = CompaniesTable.save_view(first, "weekly", "location=cambridge", ["name"])

    (view,) = CompaniesTable._views_of(again)
    assert view.query == "location=cambridge" and view.default


def test_a_nameless_view_is_not_kept_and_changes_nothing():
    current = {"columns": ["name"]}
    assert CompaniesTable.save_view(current, "   ", "location=x", ["name"]) == current
    assert CompaniesTable.save_view(current, "!!!", "location=x", ["name"]) == current


def test_columns_a_table_does_not_have_are_not_saved():
    kept = CompaniesTable.save_view({}, "Odd", "", ["name", "colour", "nonsense"])
    (view,) = CompaniesTable._views_of(kept)
    assert view.columns == ("name",)


def test_there_is_a_ceiling_and_the_newest_survive():
    current: dict = {}
    for number in range(tables.MAX_VIEWS + 5):
        current = CompaniesTable.save_view(current, f"View {number}", f"q={number}", [])
    names = [view.name for view in CompaniesTable._views_of(current)]
    assert len(names) == tables.MAX_VIEWS
    assert names[-1] == f"View {tables.MAX_VIEWS + 4}" and "View 0" not in names


def test_forgetting_and_defaulting():
    current = CompaniesTable.save_view({}, "One", "q=1", [])
    current = CompaniesTable.save_view(current, "Two", "q=2", [])
    current = CompaniesTable.make_default(current, "two")
    assert [v.default for v in CompaniesTable._views_of(current)] == [False, True]

    current = CompaniesTable.make_default(current, "nothing-of-the-sort")
    assert not any(v.default for v in CompaniesTable._views_of(current)), "means: no default"

    current = CompaniesTable.forget_view(current, "one")
    assert [v.name for v in CompaniesTable._views_of(current)] == ["Two"]


def test_the_rest_of_the_settings_are_untouched_by_a_view():
    current = {"columns": ["name"], "page_size": 25, "widths": {"name": 200}}
    kept = CompaniesTable.save_view(current, "V", "q=1", ["name"])
    assert {k: v for k, v in kept.items() if k != "views"} == current


# ------------------------------------------------------------------ applying one


def test_choosing_a_view_is_a_link_and_it_brings_its_columns(user):
    settings = CompaniesTable.save_view(
        {"columns": ["name"]}, "Wide", "location=mexico", ["name", "location", "website"]
    )
    listed = table_for(user, settings=settings)
    (view,) = listed.views

    url = listed.view_url(view)
    assert url == "/jobs/companies/?location=mexico&saved=wide"

    applied = table_for(user, query="location=mexico&saved=wide", settings=settings)
    assert applied.applied_view == view
    assert [c.key for c in applied.visible] == ["name", "location", "website"]
    assert applied.given("location") == "mexico"

    unnamed = table_for(user, query="location=mexico", settings=settings)
    assert unnamed.applied_view is None
    assert [c.key for c in unnamed.visible] == ["name"], "without the name, the usual columns"


def test_a_view_that_does_not_exist_applies_nothing(user):
    plain = table_for(user, query="saved=never-was", settings={"columns": ["name"]})
    assert plain.applied_view is None
    assert [c.key for c in plain.visible] == ["name"]


# --------------------------------------------------------------- the table changed


def test_a_view_survives_its_table_changing_and_says_what_it_dropped(user):
    """The whole of the risk. A column that went away, a filter that no longer exists and a
    sort on a field that was renamed: the view opens with what it can honour."""
    stored = {
        "views": [
            {
                "name": "Old",
                "slug": "old",
                "query": "location=mexico&headcount_min=5&sort=-turnover",
                "columns": ["name", "turnover", "location"],
                "default": False,
            }
        ]
    }
    table = table_for(
        user, query="location=mexico&headcount_min=5&sort=-turnover&saved=old", settings=stored
    )

    assert [c.key for c in table.visible] == ["name", "location"], "what still exists, in order"
    assert table.sort == CompaniesTable.default_sort, "an unknown sort is the usual one"
    columns, params = table.view_gaps(table.applied_view)
    assert columns == ["turnover"]
    assert params == ["headcount_min", "sort=turnover"]


def test_a_view_whose_every_column_has_gone_falls_back_to_the_usual_ones(user):
    stored = {
        "columns": ["name", "location"],
        "views": [{"name": "Gone", "slug": "gone", "query": "", "columns": ["turnover"]}],
    }
    table = table_for(user, query="saved=gone", settings=stored)
    assert [c.key for c in table.visible] == ["name", "location"]


def test_rows_that_are_not_views_are_ignored_rather_than_raised_on(user):
    stored = {
        "views": [
            "nonsense",
            {"slug": "x"},
            {"name": "", "query": "a=b"},
            42,
            {"name": "Real", "query": "q=1"},
        ]
    }
    table = table_for(user, settings=stored)
    assert [v.name for v in table.views] == ["Real"]
    assert table.views[0].slug == "real", "a row without a slug gets one from its name"


def test_known_params_covers_every_filter_shape():
    known = ApplicationsTable.known_params()
    assert {"sort", "page", tables.SAVED} <= known
    assert set(ApplicationsTable.extra_params) <= known
    for column in ApplicationsTable.columns:
        if column.filter == "date":
            assert {f"{column.name}_from", f"{column.name}_to"} <= known
        elif column.filter == "number":
            assert {f"{column.name}_min", f"{column.name}_max"} <= known
        elif column.filter:
            assert column.name in known


# ------------------------------------------------------------------ the default


def test_a_bare_address_opens_as_the_default_view_and_a_question_does_not(client, user):
    Company.objects.create(owner=user, name="Black Mesa", location="New Mexico")
    settings = CompaniesTable.make_default(
        CompaniesTable.save_view({}, "Mexico", "location=mexico", ["name"]), "mexico"
    )
    tables.save_settings(user, "companies", settings)
    client.force_login(user)

    response = client.get(reverse("jobs:company_list"))
    assert response.status_code == 302
    assert response["Location"] == "/jobs/companies/?location=mexico&saved=mexico"

    assert client.get(reverse("jobs:company_list"), {"q": "black"}).status_code == 200
    assert client.get(reverse("jobs:company_list"), {"saved": "none"}).status_code == 200
    assert client.get(reverse("jobs:company_list"), **HTMX).status_code == 200, (
        "a swap is not an arrival"
    )


def test_clear_points_at_the_plain_table_when_a_default_exists(user):
    settings = CompaniesTable.make_default(
        CompaniesTable.save_view({}, "Mexico", "location=mexico", ["name"]), "mexico"
    )
    with_default = table_for(
        user, query="location=mexico&saved=mexico&sort=-name", settings=settings
    )
    assert with_default.clear_url == "/jobs/companies/?sort=-name&saved=none"
    assert with_default.plain_url == "/jobs/companies/?saved=none"

    without = table_for(user, query="location=mexico&sort=-name", settings={})
    assert without.clear_url == "/jobs/companies/?sort=-name"


# ------------------------------------------------------------------- the page


def test_keeping_a_view_from_the_page_and_being_taken_to_it(client, user):
    client.force_login(user)
    response = client.post(
        reverse("core:table_views", args=["companies"]),
        {"action": "save", "name": "Mexico", "next": "/jobs/companies/?location=mexico&page=2"},
    )
    assert response.status_code == 302
    assert response["Location"] == "/jobs/companies/?location=mexico&saved=mexico"
    user.refresh_from_db()
    (view,) = CompaniesTable._views_of(tables.settings_for(user, "companies"))
    assert view.query == "location=mexico" and view.columns == tuple(
        CompaniesTable.default_columns()
    )


def test_the_page_lists_the_views_and_offers_to_keep_the_current_one(client, user):
    tables.save_settings(
        user, "companies", CompaniesTable.save_view({}, "Mexico", "location=mexico", ["name"])
    )
    client.force_login(user)

    html = client.get(reverse("jobs:company_list"), {"location": "mexico"}).content.decode()

    assert 'href="/jobs/companies/?location=mexico&amp;saved=mexico"' in html
    assert reverse("core:table_views", args=["companies"]) in html
    assert 'name="name"' in html and 'value="save"' in html


def test_the_page_says_what_a_stale_view_had_to_leave_out(client, user):
    tables.save_settings(
        user,
        "companies",
        {
            "views": [
                {
                    "name": "Old",
                    "slug": "old",
                    "query": "headcount_min=5",
                    "columns": ["turnover", "name"],
                }
            ]
        },
    )
    client.force_login(user)

    html = client.get(
        reverse("jobs:company_list"), {"headcount_min": "5", "saved": "old"}
    ).content.decode()

    assert "data-view-gaps" in html
    assert "turnover" in html and "headcount_min" in html


def test_forgetting_and_defaulting_from_the_page(client, user):
    tables.save_settings(
        user, "companies", CompaniesTable.save_view({}, "Mexico", "location=mexico", ["name"])
    )
    client.force_login(user)
    url = reverse("core:table_views", args=["companies"])

    client.post(url, {"action": "default", "slug": "mexico", "next": "/jobs/companies/"})
    user.refresh_from_db()
    assert CompaniesTable._views_of(tables.settings_for(user, "companies"))[0].default

    client.post(url, {"action": "delete", "slug": "mexico", "next": "/jobs/companies/"})
    user.refresh_from_db()
    assert CompaniesTable._views_of(tables.settings_for(user, "companies")) == []


def test_a_table_nobody_registered_is_not_a_place_to_keep_anything(client, user):
    client.force_login(user)
    response = client.post(
        reverse("core:table_views", args=["nonsense"]), {"action": "save", "name": "x"}
    )
    assert response.status_code == 404


def test_views_leave_with_the_export_and_come_back(user, other_user):
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    tables.save_settings(
        user, "companies", CompaniesTable.save_view({}, "Mexico", "location=mexico", ["name"])
    )
    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))
    other_user.refresh_from_db()
    restored = CompaniesTable._views_of(tables.settings_for(other_user, "companies"))
    assert [v.name for v in restored] == ["Mexico"]
