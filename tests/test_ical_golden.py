"""What the writer wrote before it moved to the library, and what it writes now (#661).

The goldens in ``tests/data/ical/`` are the hand-written writer's own output for one fixed
search. Both sides are parsed with ``icalendar`` and compared component by component, so a
difference in folding or in the order of properties is not a difference, and a property
that went missing or changed its value is. ``DTSTAMP`` is the one thing that is the moment
the file was made, and is left out of the comparison.
"""

import datetime as dt
import os
from pathlib import Path

import icalendar
import pytest
from django.utils import timezone

from postulo.applications import ical
from postulo.applications.models import (
    Application,
    Interview,
    InterviewKind,
    Reminder,
    Status,
)
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db

GOLDEN = Path(__file__).parent / "data" / "ical"
START = dt.datetime(2026, 10, 5, 9, 30, tzinfo=dt.UTC)
MADE = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.UTC)


@pytest.fixture
def interview(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    contact = Contact.objects.create(
        owner=user, company=company, name="Cave Johnson", role="CEO", email="cave@aperture.test"
    )
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    reminder = Reminder.objects.create(
        owner=user,
        application=application,
        summary="Interview tomorrow: On site at Aperture Science, 10:30",
        due_at=START - dt.timedelta(days=1),
    )
    made = Interview.objects.create(
        owner=user,
        application=application,
        kind=InterviewKind.ONSITE,
        starts_at=START,
        ends_at=START + dt.timedelta(hours=1),
        location="1 Aperture Way, Cambridge; floor 3",
        notes="Bring ID.\nAsk about the team.",
        uid="golden-interview@postulo",
        reminder=reminder,
    )
    made.contacts.set([contact])
    Interview.objects.filter(pk=made.pk).update(created_at=MADE, updated_at=MADE)
    return Interview.objects.with_display_data().get(pk=made.pk)


DAYS = [
    ical.DayEntry(
        summary="Deadline: Test Engineer",
        day=dt.date(2026, 10, 20),
        url="https://postulo.example/applications/1/",
        description="Aperture Science",
    ),
    ical.DayEntry(
        summary="Closes: Old role",
        day=dt.date(2026, 11, 1),
        url="https://postulo.example/jobs/2/",
        description="Black Mesa, with a comma; and a semicolon",
        over=True,
    ),
]


def what_it_wrote(interview) -> dict[str, str]:
    return {
        "feed.ics": ical.calendar(
            [interview], url_for=lambda _i: "https://postulo.example/applications/1/", days=DAYS
        ),
        "alarm.ics": "\r\n".join(ical.event_lines(interview, alarm=True)) + "\r\n",
    }


def components(text: str) -> list[tuple]:
    """Every component of a document: its name, and its properties with their parameters."""
    found = []
    for component in icalendar.Calendar.from_ical(text).walk():
        props = sorted(
            (name, component[name].to_ical().decode(), sorted(component[name].params.items()))
            if not isinstance(component[name], list)
            else (name, str([p.to_ical() for p in component[name]]), [])
            for name in component
            if name != "DTSTAMP"
        )
        found.append((component.name, props))
    return found


@pytest.mark.skipif(not os.environ.get("POSTULO_WRITE_GOLDEN"), reason="writes the goldens")
def test_write_the_goldens(interview):
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, text in what_it_wrote(interview).items():
        (GOLDEN / name).write_bytes(text.encode())


@pytest.mark.parametrize("name", ["feed.ics", "alarm.ics"])
def test_the_new_writer_says_what_the_old_one_said(interview, name):
    old = (GOLDEN / name).read_text(encoding="utf-8")
    new = what_it_wrote(interview)[name]
    assert components(new) == components(old)


def test_the_goldens_are_what_a_calendar_reads(interview):
    """A golden that does not parse would make the comparison above meaningless."""
    calendar = icalendar.Calendar.from_ical((GOLDEN / "feed.ics").read_text(encoding="utf-8"))
    assert calendar.walk("VEVENT")[0]["UID"] == "golden-interview@postulo"
    assert len(calendar.walk("VEVENT")) == 3
    assert timezone.is_aware(MADE)
