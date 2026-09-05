"""\"I'll send them on to my own page after they click.\" No: every next stays on this host."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.accounts.models import Profile
from postulo.applications.models import Application, InterviewKind, Reminder, Status
from postulo.applications.services import change_status, schedule_interview
from postulo.core.redirects import safe_next
from postulo.jobs.models import Company, JobPosting
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db

ELSEWHERE = "https://evil.example/sign-in"


@pytest.fixture
def world(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    listing = JobPosting.objects.create(owner=user, company=company, title="Undecided")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=40))
    reminder = Reminder.objects.create(
        owner=user, application=application, summary="x", due_at=timezone.now()
    )
    interview = schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=timezone.now() + dt.timedelta(days=2)
    )
    experience = Experience.objects.create(
        owner=user, organisation="X", role="Y", start_date=dt.date(2020, 1, 1)
    )
    return {
        "application": application,
        "listing": listing,
        "reminder": reminder,
        "interview": interview,
        "experience": experience,
    }


def endpoints(world):
    """Every POST that takes a next, with a payload that succeeds."""
    a = world["application"]
    return [
        (reverse("applications:status", args=[a.pk]), {"status": "acknowledged"}),
        (reverse("applications:reminder_complete", args=[world["reminder"].pk]), {}),
        (reverse("applications:quiet_action", args=[a.pk]), {"action": "follow_up"}),
        (
            reverse("applications:interview_outcome", args=[world["interview"].pk]),
            {"outcome": "done"},
        ),
        (reverse("resume:item_move", args=["experience", world["experience"].pk, "down"]), {}),
        (reverse("accounts:theme"), {"theme": "dark"}),
        (
            reverse("core:table_settings", args=["applications"]),
            {"order": ["role"], "show": ["role"], "page_size": "50"},
        ),
        (reverse("listings:shortlist", args=[world["listing"].pk]), {}),
    ]


def test_no_next_leaves_the_site(client, user, world):
    client.force_login(user)
    for url, payload in endpoints(world):
        response = client.post(url, {**payload, "next": ELSEWHERE})
        assert response.status_code in (302, 200), url
        if response.status_code == 302:
            location = response["Location"]
            assert not location.startswith("http"), f"{url} sent the person to {location}"
        # Protocol-relative and scheme-less tricks count as leaving too.
        response = client.post(url, {**payload, "next": "//evil.example/"})
        if response.status_code == 302:
            assert not response["Location"].startswith("//"), url


def test_an_on_site_next_is_honoured(client, user, world):
    client.force_login(user)
    a = world["application"]
    response = client.post(
        reverse("applications:status", args=[a.pk]),
        {"status": "screening", "next": reverse("applications:board")},
    )
    assert response["Location"] == reverse("applications:board")


def test_the_helper_refuses_other_hosts_and_a_drop_to_http(rf):
    request = rf.post("/x/", {"next": "https://evil.example/"}, secure=True)
    assert safe_next(request, "/home/") == "/home/"
    request = rf.post("/x/", {"next": "http://testserver/page/"}, secure=True)
    assert safe_next(request, "/home/") == "/home/", "https must not drop to http"
    request = rf.post("/x/", {"next": "/applications/?q=x"})
    assert safe_next(request, "/home/") == "/applications/?q=x"
    request = rf.get("/x/?next=/board/")
    assert safe_next(request, "/home/") == "/board/"
    assert safe_next(rf.post("/x/"), "/home/") == "/home/"
    assert Profile.objects.count() >= 0  # the fixture database is fine to touch
