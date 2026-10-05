"""What the busiest pages may cost, counted (#231).

The 2026-09-15 audit found five shapes of the same problem: work that is linear in the
amount of data a person has, on pages they open every day. None of it hurts in the first
months and all of it grows with a year or two of a search — a query per matching application
in search, a five-table `GROUP BY` behind the companies table, the whole board loaded to
draw three columns of it.

**Budgets, not exact counts.** An exact number breaks on any harmless change and teaches
people to bump it; a budget that is comfortably above what the page does and far below what
linear growth would cost says the thing worth saying. What each of these asserts is not "this
many queries" but *"this number does not follow the number of rows"* — which is why each
budget is measured twice, once with a little data and once with a lot, and the two have to
be the same.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Reminder, Status
from postulo.applications.services import change_status, record_event
from postulo.core.models import Tag
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


def furnish(user, *, companies: int, mark: str, per_company: int = 2) -> None:
    """A search's worth of data: companies, listings, applications, events, contacts.

    ``mark`` keeps two rounds of furnishing apart -- a company's name is unique per owner,
    and the second round is meant to *add* to the first rather than fail against it.
    """
    tags = Tag.named(user, ["Remote", "Dream job"])
    for index in range(companies):
        company = Company.objects.create(
            owner=user, name=f"Aperture {mark}-{index}", location="Cambridge"
        )
        Contact.objects.create(owner=user, company=company, name=f"Cave {index}")
        for offset in range(per_company):
            posting = JobPosting.objects.create(
                owner=user,
                company=company,
                title=f"Test Engineer {mark}-{index}-{offset}",
                description="Engineering, at length. " * 40,
                closes_at=timezone.localdate() + dt.timedelta(days=offset + 1),
            )
            if offset % 2:
                continue
            application = Application.objects.create(
                owner=user, posting=posting, status=Status.DRAFT
            )
            application.tags.set(tags)
            change_status(
                application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=20)
            )
            record_event(application, summary=f"Spoke to engineering {index}")
            Reminder.objects.create(
                owner=user,
                application=application,
                summary=f"Chase {index}",
                due_at=timezone.now() + dt.timedelta(days=3),
            )


def cost(client, url, params=None, **extra) -> int:
    """How many queries one request makes, as the connection counts them.

    The request is made twice and the second one is counted. The first is not warming a
    cache of the page's own -- there is none -- but of the things around it: the API's rate
    limiter writes a counter row the first time an address is asked for and increments it
    afterwards, and a token's *last used* is written once and then left alone for a while.
    Those are two different query counts for the same page, and comparing one against the
    other reads as "this page grew" when nothing about it did.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    assert client.get(url, params or {}, **extra).status_code == 200
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url, params or {}, **extra)
        assert response.status_code == 200, response.status_code
    return len(captured)


#: (name, url factory, query string). Each is opened twice — with a handful of rows and
#: with ten times as many — and the two costs have to match.
PAGES = [
    ("the applications table", "applications:list", {"view": "table"}),
    ("the board", "applications:list", {"view": "board"}),
    ("the board, folded", "applications:list", {"view": "board", "status": "applied"}),
    ("the companies table", "jobs:company_list", {}),
    ("the listings page", "listings:list", {}),
    ("the listings page, applied", "listings:list", {"state": "applied"}),
    ("the listings page, everything", "listings:list", {"state": "all"}),
    ("the dashboard", "core:home", {}),
    ("search", "core:search", {"q": "engineer"}),
    ("the report", "applications:report", {}),
]


@pytest.mark.parametrize(("what", "name", "params"), PAGES, ids=[row[0] for row in PAGES])
def test_a_page_costs_the_same_with_ten_times_the_data(client, user, what, name, params):
    """The assertion the whole issue is about: the number of queries a page makes must not
    follow the number of rows a person has. A page that costs three more queries for thirty
    more applications costs three hundred more for a year of them."""
    client.force_login(user)
    url = reverse(name)

    furnish(user, companies=3, mark="a")
    small = cost(client, url, params)

    furnish(user, companies=30, mark="b")
    large = cost(client, url, params)

    assert large == small, (
        f"{what}: {small} queries for 3 companies and {large} for 33. "
        "Something in this page is per-row."
    )


def test_the_tag_list_costs_the_same_with_ten_times_the_tags(client, user):
    """The page counted each tag's applications on its own (#554)."""
    client.force_login(user)
    url = reverse("applications:tag_list")

    for index in range(3):
        Tag.objects.create(owner=user, name=f"small {index}")
    small = cost(client, url)

    for index in range(30):
        Tag.objects.create(owner=user, name=f"large {index}")
    large = cost(client, url)

    assert large == small, f"{small} queries for 3 tags and {large} for 33"


def test_the_api_lists_cost_the_same_with_ten_times_the_data(client, user):
    from postulo.api.models import ApiToken

    _token, raw = ApiToken.issue(owner=user, name="test", scopes=["read"])
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    paths = ("/api/v1/applications", "/api/v1/companies", "/api/v1/listings", "/api/v1/reminders")

    furnish(user, companies=3, mark="a")
    small = {path: cost(client, path, **headers) for path in paths}

    furnish(user, companies=30, mark="b")
    large = {path: cost(client, path, **headers) for path in paths}

    assert large == small, f"an API list is per-row: {small} became {large}"


def test_the_board_reads_only_what_it_draws(client, user):
    """The columns are the open statuses, so the settled ones are not the board's business.

    It used to read every application ever recorded -- with four subqueries each -- and drop
    the ones it could not put in a column, in Python. Somebody with four hundred settled
    applications and twelve live ones read four hundred and twelve rows to draw twelve. The
    two counts above the board are still about all of them, and are `COUNT`s (#231).
    """
    from postulo.applications.models import Application, Status
    from postulo.applications.services import change_status

    furnish(user, companies=2, mark="a")
    for application in Application.objects.for_user(user):
        change_status(application, Status.REJECTED)
    client.force_login(user)

    response = client.get(reverse("applications:list"), {"view": "board"})

    drawn = sum(len(column["applications"]) for column in response.context["columns"])
    assert drawn == 0, "every one of them is settled"
    assert response.context["total"] == Application.objects.for_user(user).count()


def test_tagging_a_hundred_at_once_is_not_three_hundred_queries(client, user):
    """The bulk action asked twice per row -- does this one have the tag, and if not, add it
    -- so tagging a hundred applications was about three hundred queries (#231)."""
    from postulo.applications.models import Application
    from postulo.core import bulk

    furnish(user, companies=12, mark="a")
    tag = Tag.objects.create(owner=user, name="Chosen")
    ids = list(Application.objects.for_user(user).values_list("pk", flat=True))
    assert len(ids) >= 10
    client.force_login(user)

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        client.post(
            reverse("applications:bulk"),
            {bulk.ACTION: "tag", "tag": tag.pk, bulk.CHOSEN: [str(pk) for pk in ids]},
        )

    assert Application.objects.filter(tags=tag).count() == len(ids)
    writes = [q for q in captured if "applications_application_tags" in q["sql"]]
    assert len(writes) <= 2, f"one read and one write, not one per row: {len(writes)}"


# ------------------------------------------------------------------------- CVs (#559)


def put_on_cv(cv, user, *, each: int, mark: str) -> None:
    """``each`` entries of every kind a CV can hold, with two skills in every skill group."""
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CVItem
    from postulo.resume.models import (
        Certification,
        Education,
        Experience,
        LanguageSkill,
        Link,
        Project,
        Skill,
        SkillGroup,
    )

    today = dt.date(2024, 1, 1)
    for index in range(each):
        label = f"{mark}{index}"
        group = SkillGroup.objects.create(owner=user, name=f"Group {label}")
        for number in range(2):
            Skill.objects.create(owner=user, name=f"Python {label}{number}", group=group)
        entries = [
            Experience.objects.create(
                owner=user, organisation=f"Org {label}", role="Dev", start_date=today
            ),
            Education.objects.create(owner=user, institution=f"School {label}"),
            group,
            Project.objects.create(owner=user, name=f"Project {label}"),
            Certification.objects.create(owner=user, name=f"Cert {label}"),
            LanguageSkill.objects.create(owner=user, name=f"Language {label}"),
            Link.objects.create(owner=user, title=f"Link {label}", url="https://example.org/a"),
        ]
        for entry in entries:
            CVItem.objects.create(
                owner=user,
                cv=cv,
                content_type=ContentType.objects.get_for_model(entry),
                object_id=entry.pk,
                order=CVItem.objects.filter(cv=cv).count(),
            )


@pytest.mark.parametrize("language", ["", "pt-PT"], ids=["no language", "in a language"])
def test_a_cv_costs_the_same_at_seven_entries_and_sixty_three(client, user, language):
    """The page somebody edits most, its preview, a download and the API: each costs the
    same however many entries the CV holds, skill groups included (#559)."""
    from postulo.api.models import ApiToken
    from postulo.documents.models import CV

    client.force_login(user)
    _token, raw = ApiToken.issue(owner=user, name="test", scopes=["read"])
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    cv = CV.objects.create(owner=user, name="Main", language=language)
    urls = {
        "page": (reverse("documents:cv_detail", args=[cv.pk]), {}),
        "preview": (reverse("documents:cv_preview", args=[cv.pk]), {}),
        "text": (reverse("documents:cv_text", args=[cv.pk]), {}),
        "docx": (reverse("documents:cv_download", args=[cv.pk, "docx"]), {}),
        "api": (f"/api/v1/cvs/{cv.pk}", headers),
    }

    put_on_cv(cv, user, each=1, mark="a")
    small = {name: cost(client, url, **extra) for name, (url, extra) in urls.items()}

    put_on_cv(cv, user, each=8, mark="b")
    large = {name: cost(client, url, **extra) for name, (url, extra) in urls.items()}

    assert large == small, f"a CV read is per-entry: {small} became {large}"


def test_the_career_overview_costs_the_same_with_one_skill_group_and_nine(client, user):
    from postulo.resume.models import Skill, SkillGroup

    client.force_login(user)
    url = reverse("resume:overview")

    def groups(count: int, mark: str) -> None:
        for index in range(count):
            group = SkillGroup.objects.create(owner=user, name=f"Group {mark}{index}")
            for number in range(3):
                Skill.objects.create(owner=user, name=f"Skill {mark}{index}{number}", group=group)

    groups(1, "a")
    small = cost(client, url)
    groups(8, "b")
    assert cost(client, url) == small
