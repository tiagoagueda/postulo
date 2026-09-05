"""Tables: sort by column, narrow from the headers, choose and order the columns."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.accounts.models import Profile
from postulo.applications.models import Application, Priority, Reminder, Status
from postulo.applications.services import change_status, record_event
from postulo.applications.tables import ApplicationsTable
from postulo.core import tables
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.jobs.tables import CompaniesTable

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def search(user):
    """Three applications at two companies, different enough to sort and narrow by."""
    aperture = Company.objects.create(owner=user, name="Aperture Science", location="Cambridge")
    black_mesa = Company.objects.create(owner=user, name="Black Mesa", location="New Mexico")
    Contact.objects.create(owner=user, company=black_mesa, name="Gordon")
    rows = []
    for company, title, status, days_ago, priority in [
        (aperture, "Test Engineer", Status.APPLIED, 30, Priority.HIGH),
        (aperture, "Portal Researcher", Status.INTERVIEWING, 10, Priority.NORMAL),
        (black_mesa, "Research Associate", Status.REJECTED, 20, Priority.LOW),
    ]:
        posting = JobPosting.objects.create(owner=user, company=company, title=title)
        application = Application.objects.create(
            owner=user, posting=posting, status=Status.DRAFT, priority=priority
        )
        change_status(application, status, occurred_at=timezone.now() - dt.timedelta(days=days_ago))
        if status != Status.APPLIED:
            change_status(
                application,
                Status.APPLIED,
                occurred_at=timezone.now() - dt.timedelta(days=days_ago),
            )
            change_status(application, status)
        application.refresh_from_db()
        rows.append(application)
    return {"aperture": aperture, "black_mesa": black_mesa, "applications": rows}


def titles(response) -> list[str]:
    return [a.posting.title for a in response.context["applications"]]


# ------------------------------------------------------------------------- sort


def test_headers_sort_both_ways_and_unknown_keys_fall_back(client, user, search):
    client.force_login(user)
    url = reverse("applications:list")

    ascending = client.get(url, {"sort": "role"})
    assert titles(ascending) == ["Portal Researcher", "Research Associate", "Test Engineer"]
    assert ascending.context["table"].sort == "role"

    descending = client.get(url, {"sort": "-role"})
    assert titles(descending) == ["Test Engineer", "Research Associate", "Portal Researcher"]

    nonsense = client.get(url, {"sort": "owner__password"})
    assert nonsense.context["table"].sort == "-created", "not declared, so the default"
    assert titles(nonsense) == titles(client.get(url))

    unsortable = client.get(url, {"sort": "tags"})
    assert unsortable.context["table"].sort == "-created"


def test_sorting_by_a_date_puts_the_newest_first_and_the_blanks_last(client, user, search):
    draft = Application.objects.create(
        owner=user,
        posting=JobPosting.objects.create(owner=user, company=search["aperture"], title="Draft"),
        status=Status.DRAFT,
    )
    client.force_login(user)
    response = client.get(reverse("applications:list"), {"sort": "-applied"})
    assert titles(response) == ["Portal Researcher", "Research Associate", "Test Engineer", "Draft"]
    assert response.context["applications"][3] == draft

    header = next(h for h in response.context["table"].headers if h.key == "applied")
    assert header.state == "desc" and header.next_sort == "applied"
    assert 'aria-sort="descending"' in response.content.decode()


def test_the_companies_table_sorts_by_its_counts(client, user, search):
    client.force_login(user)
    response = client.get(reverse("jobs:company_list"), {"sort": "-applications"})
    assert [c.name for c in response.context["companies"]] == ["Aperture Science", "Black Mesa"]
    response = client.get(reverse("jobs:company_list"), {"sort": "-contacts"})
    assert [c.name for c in response.context["companies"]] == ["Black Mesa", "Aperture Science"]


# ---------------------------------------------------------------------- filters


def test_header_filters_compose_with_the_form_above(client, user, search):
    client.force_login(user)
    url = reverse("applications:list")

    by_company = client.get(url, {"company": "aperture"})
    assert sorted(titles(by_company)) == ["Portal Researcher", "Test Engineer"]

    combined = client.get(url, {"company": "aperture", "state": "open", "role": "portal"})
    assert titles(combined) == ["Portal Researcher"]

    by_priority = client.get(url, {"priority": str(Priority.HIGH)})
    assert titles(by_priority) == ["Test Engineer"]
    assert titles(client.get(url, {"priority": "99"})) == titles(client.get(url)), "not a choice"

    since = (timezone.localdate() - dt.timedelta(days=15)).isoformat()
    recent = client.get(url, {"applied_from": since})
    assert titles(recent) == ["Portal Researcher"]
    until = (timezone.localdate() - dt.timedelta(days=15)).isoformat()
    older = client.get(url, {"applied_to": until})
    assert sorted(titles(older)) == ["Research Associate", "Test Engineer"]
    assert titles(client.get(url, {"applied_from": "yesterday"})) == titles(client.get(url))


def test_filters_only_narrow_the_owners_rows(client, user, other_user, search):
    theirs = Company.objects.create(owner=other_user, name="Aperture Science")
    Application.objects.create(
        owner=other_user,
        posting=JobPosting.objects.create(owner=other_user, company=theirs, title="Theirs"),
        status=Status.APPLIED,
    )
    client.force_login(user)
    response = client.get(reverse("applications:list"), {"company": "aperture"})
    assert "Theirs" not in titles(response)


def test_a_narrowing_that_matches_nothing_says_so_and_offers_a_way_out(client, user, search):
    client.force_login(user)
    response = client.get(reverse("applications:list"), {"company": "umbrella", "sort": "role"})
    body = response.content.decode()
    assert "Nothing matches these filters" in body
    assert "Nothing here yet" not in body
    assert response.context["table"].clear_url == reverse("applications:list") + "?sort=role"

    empty = client.get(reverse("applications:list"), {"company": "anything"})
    Application.objects.for_user(user).delete()
    empty = client.get(reverse("applications:list"))
    assert "Nothing here yet" in empty.content.decode()


def test_the_companies_table_narrows_from_its_headers(client, user, search):
    client.force_login(user)
    response = client.get(reverse("jobs:company_list"), {"location": "mexico"})
    assert [c.name for c in response.context["companies"]] == ["Black Mesa"]
    response = client.get(reverse("jobs:company_list"), {"q": "aperture", "location": "mexico"})
    assert list(response.context["companies"]) == []


# ------------------------------------------------------------------------- htmx


def test_an_htmx_request_receives_the_table_and_a_plain_request_the_page(client, user, search):
    client.force_login(user)
    url = reverse("applications:list")

    page = client.get(url).content.decode()
    assert "<html" in page and 'id="applications-table"' in page
    assert (
        'id="applications-count"' in page
        and "hx-swap-oob" not in page.split("applications-table")[0]
    )

    fragment = client.get(url, {"company": "black"}, **HTMX).content.decode()
    assert "<html" not in fragment
    assert 'id="applications-table"' in fragment
    assert 'id="applications-count" ' in fragment and 'hx-swap-oob="true"' in fragment
    assert "Research Associate" in fragment and "Test Engineer" not in fragment

    restored = client.get(url, **HTMX, HTTP_HX_HISTORY_RESTORE_REQUEST="true").content.decode()
    assert "<html" in restored, "the back button wants the whole page"


def test_the_header_carries_live_filter_inputs_and_the_sort_in_force(client, user, search):
    client.force_login(user)
    body = client.get(reverse("applications:list"), {"sort": "-applied"}).content.decode()
    assert 'name="company"' in body and 'form="application-filters"' in body
    assert 'hx-trigger="input changed delay:300ms, search"' in body
    assert 'aria-label="Filter by company"' in body
    assert 'name="sort" value="-applied" form="application-filters"' in body
    assert "hx-preserve" in body


# ---------------------------------------------------------------------- columns


def settings_url(name: str) -> str:
    return reverse("core:table_settings", args=[name])


def test_the_default_layout_is_todays_table(client, user, search):
    client.force_login(user)
    response = client.get(reverse("applications:list"))
    assert [c.key for c in response.context["table"].visible] == [
        "role",
        "company",
        "location",
        "status",
        "applied",
    ]
    assert response.context["table"].page_size == 50
    body = response.content.decode()
    assert "Deadline" not in body.split("<tbody")[0].split("<table")[1], (
        "hidden columns stay hidden"
    )


def test_column_choices_round_trip_through_the_profile(client, user, search):
    client.force_login(user)
    url = reverse("applications:list")
    response = client.post(
        settings_url("applications"),
        {
            "order": ["company", "role", "deadline", "status", "location", "applied", "tags"],
            "show": ["company", "role", "deadline", "tags", "owner__password"],
            "page_size": "25",
            "next": url + "?sort=role",
        },
    )
    assert response.status_code == 302 and response["Location"] == url + "?sort=role"

    stored = Profile.objects.get(user=user).table_settings["applications"]
    assert stored == {"columns": ["company", "role", "deadline", "tags"], "page_size": 25}

    response = client.get(url)
    assert [c.key for c in response.context["table"].visible] == [
        "company",
        "role",
        "deadline",
        "tags",
    ]
    assert response.context["paginator"].per_page == 25
    body = response.content.decode()
    assert body.index("Company") < body.index("Role"), "the person's order, not ours"
    assert "<th" in body and "Location</a>" not in body

    # An htmx swap keeps the layout, and the *Columns* control reflects it.
    fragment = client.get(url, **HTMX).content.decode()
    assert "Deadline" in fragment and "Location</a>" not in fragment


def test_up_and_down_buttons_reorder_without_scripts(client, user, search):
    client.force_login(user)
    common = {
        "order": ["role", "company", "location", "status", "applied"],
        "show": ["role", "company", "location", "status", "applied"],
        "page_size": "50",
        "next": reverse("applications:list"),
    }
    client.post(settings_url("applications"), {**common, "move": "up:location"})
    stored = Profile.objects.get(user=user).table_settings["applications"]["columns"]
    assert stored == ["role", "location", "company", "status", "applied"]

    client.post(settings_url("applications"), {**common, "move": "down:applied"})
    stored = Profile.objects.get(user=user).table_settings["applications"]["columns"]
    assert stored == ["role", "company", "location", "status", "applied"], "already last"

    client.post(settings_url("applications"), {**common, "move": "up:role"})
    stored = Profile.objects.get(user=user).table_settings["applications"]["columns"]
    assert stored == ["role", "company", "location", "status", "applied"], "already first"


def test_reset_forgets_the_choices_and_unticking_everything_keeps_the_defaults(client, user):
    client.force_login(user)
    client.post(
        settings_url("companies"),
        {"order": ["name"], "show": [], "page_size": "100", "next": reverse("jobs:company_list")},
    )
    stored = Profile.objects.get(user=user).table_settings["companies"]
    assert stored["columns"] == CompaniesTable.default_columns() and stored["page_size"] == 100

    client.post(settings_url("companies"), {"reset": "1", "next": reverse("jobs:company_list")})
    assert "companies" not in Profile.objects.get(user=user).table_settings


def test_the_settings_view_is_strict_about_what_it_accepts(client, user):
    client.force_login(user)
    assert client.post(settings_url("nonsense"), {"next": "/"}).status_code == 404
    assert client.get(settings_url("applications")).status_code == 405

    response = client.post(
        settings_url("applications"),
        {"order": ["role"], "show": ["role"], "page_size": "7", "next": "https://evil.example/"},
    )
    assert response["Location"] == "/", "an off-site next is ignored"
    stored = Profile.objects.get(user=user).table_settings["applications"]
    assert stored["page_size"] == 50, "not a size on offer, so the default"


def test_settings_need_a_signed_in_person(client, db):
    response = client.post(settings_url("applications"), {"next": "/"})
    assert response.status_code == 302 and "login" in response["Location"]


def test_a_broken_stored_value_is_ignored_rather_than_fatal(client, user, search):
    profile = Profile.objects.get(user=user)
    profile.table_settings = {"applications": {"columns": "role", "page_size": "lots"}}
    profile.save()
    client.force_login(user)
    response = client.get(reverse("applications:list"))
    assert [c.key for c in response.context["table"].visible] == ApplicationsTable.default_columns()
    assert response.context["table"].page_size == 50


# --------------------------------------------------------------- optional columns


def test_the_optional_columns_show_what_they_promise(client, user, search):
    application = search["applications"][0]
    record_event(application, summary="Chased")  # the most recent thing in the whole search
    Reminder.objects.create(
        owner=user,
        application=application,
        summary="Call",
        due_at=timezone.now() + dt.timedelta(days=1),
    )
    profile = Profile.objects.get(user=user)
    profile.table_settings = {
        "applications": {
            "columns": ["role", "last_activity", "next_reminder", "priority", "channel", "salary"],
            "page_size": 50,
        }
    }
    profile.save()
    client.force_login(user)

    response = client.get(reverse("applications:list"), {"sort": "-last_activity"})
    rows = list(response.context["applications"])
    assert rows[0] == application
    assert timezone.now() - rows[0].last_activity_at < dt.timedelta(minutes=1)
    assert rows[0].next_reminder_at is not None and rows[1].next_reminder_at is None
    body = response.content.decode()
    assert "Last activity" in body and "Next reminder" in body and "High" in body


def test_the_registry_knows_both_tables_and_nothing_else():
    assert set(tables.TABLES) == {"applications", "companies"}
    assert tables.TABLES["applications"] is ApplicationsTable
    with pytest.raises(ValueError, match="needs a name"):
        tables.register(type("Nameless", (tables.Table,), {}))
