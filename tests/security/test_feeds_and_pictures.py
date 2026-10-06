"""Feeds, pictures and the archive: what another account would try (#232).

Rule 7 of `docs/THREAT-MODEL.md` asks for a test per endpoint saying what an attacker would
try, and the 2026-09-15 audit found these without one: the calendar page, both iCalendar
feeds, the avatar view's "own picture, or an administrator's to see" rule, a company's logo,
and the export archive. `test_isolation_sweep.py` asks each address for somebody else's
record by id; this asks the other question, whether a feed or a page that lists things
lists only one's own.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications.models import Application, Interview, InterviewKind, Status
from postulo.jobs import logos
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

#: A PNG's first bytes, which is all a file field looks at.
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def an_interview(owner, title: str) -> Interview:
    company = Company.objects.create(owner=owner, name=f"{title} Ltd")
    posting = JobPosting.objects.create(owner=owner, company=company, title=title)
    application = Application.objects.create(owner=owner, posting=posting, status=Status.APPLIED)
    starts = timezone.now().replace(hour=10, minute=0) + dt.timedelta(days=3)
    return Interview.objects.create(
        owner=owner,
        application=application,
        kind=InterviewKind.ONSITE,
        starts_at=starts,
        ends_at=starts + dt.timedelta(hours=1),
    )


def bearer(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


# ---------------------------------------------------------------- the calendar


def test_the_calendar_page_shows_nobody_elses_interviews(client, user, other_user):
    mine = an_interview(user, "Own visible role")
    an_interview(other_user, "Secret other role")
    client.force_login(user)

    month = mine.starts_at.strftime("%Y-%m")
    for query in (
        {"month": month},
        {"view": "week", "on": mine.starts_at.date().isoformat()},
        {"view": "agenda"},
    ):
        html = client.get(reverse("applications:calendar"), query).content.decode()
        assert "Own visible role" in html, query
        assert "Secret other role" not in html, query


def test_the_calendar_page_needs_a_sign_in(client, db):
    response = client.get(reverse("applications:calendar"))
    assert response.status_code == 302 and reverse("account_login") in response["Location"]


# ------------------------------------------------------------------- the feeds


def test_the_feed_of_every_interview_holds_only_ones_own(client, user, other_user):
    an_interview(user, "Own visible role")
    an_interview(other_user, "Secret other role")
    client.force_login(user)

    text = client.get(reverse("applications:interview_calendar")).content.decode()
    assert "BEGIN:VCALENDAR" in text and "Own visible role" in text
    assert "Secret other role" not in text


def test_one_interviews_file_is_nobody_elses(client, user, other_user):
    theirs = an_interview(other_user, "Secret other role")
    client.force_login(user)
    assert client.get(reverse("applications:interview_ics", args=[theirs.pk])).status_code == 404


def test_a_uid_with_a_line_break_adds_no_property_to_the_feed(client, user):
    """Both stored values reach the file only after the writer cleans them (#450)."""
    interview = an_interview(user, "Own visible role")
    Interview.objects.filter(pk=interview.pk).update(uid="abc\r\nATTENDEE:mailto:evil@example.org")
    client.force_login(user)

    for address in (
        reverse("applications:interview_calendar"),
        reverse("applications:interview_ics", args=[interview.pk]),
    ):
        text = client.get(address).content.decode()
        assert "UID:abcATTENDEE:mailto:evil@example.org" in text
        assert not [line for line in text.splitlines() if line.startswith("ATTENDEE")]


def test_an_outcome_this_version_does_not_know_still_has_a_file(client, user):
    interview = an_interview(user, "Own visible role")
    Interview.objects.filter(pk=interview.pk).update(outcome="rescheduled")
    client.force_login(user)

    response = client.get(reverse("applications:interview_ics", args=[interview.pk]))
    assert response.status_code == 200
    assert "STATUS:CONFIRMED" in response.content.decode()


def test_an_archive_cannot_bring_an_unsafe_uid_or_an_unknown_outcome(user, other_user):
    from postulo.core import export as export_module
    from postulo.core import importer

    an_interview(user, "Own visible role")
    document = export_module.build_document(user)
    interview = document["companies"][0]["postings"][0]["applications"][0]["interviews"][0]
    interview["uid"] = "abc\r\nATTENDEE:mailto:evil@example.org"
    interview["outcome"] = "rescheduled"
    interview["kind"] = "hologram"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document))

    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    kept = Interview.objects.get(owner=other_user)
    assert "\n" not in kept.uid and kept.uid.endswith("@postulo"), "a fresh one"
    assert kept.outcome == "scheduled" and kept.kind == "other"


def test_the_feeds_need_a_sign_in(client, db, user):
    theirs = an_interview(user, "Own visible role")
    for url in (
        reverse("applications:interview_calendar"),
        reverse("applications:interview_ics", args=[theirs.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 302 and reverse("account_login") in response["Location"]


def test_the_api_feed_is_scoped_the_same_way(client, user, other_user):
    an_interview(user, "Own visible role")
    theirs = an_interview(other_user, "Secret other role")
    headers = bearer(user, "read")

    text = client.get("/api/v1/interviews/calendar.ics", **headers).content.decode()
    assert "Own visible role" in text and "Secret other role" not in text
    assert client.get(f"/api/v1/interviews/{theirs.pk}/calendar.ics", **headers).status_code == 404
    assert client.get("/api/v1/interviews/calendar.ics").status_code == 401, "no token, no feed"


# ------------------------------------------------- reminders, the download, the import (#661)


def a_reminder(owner, summary: str):
    from postulo.applications.models import Reminder

    return Reminder.objects.create(owner=owner, summary=summary, due_at=timezone.now())


def test_the_reminders_address_holds_only_ones_own(client, user, other_user):
    a_reminder(user, "Own nudge")
    a_reminder(other_user, "Secret nudge")
    client.force_login(user)

    text = client.get(reverse("applications:reminder_calendar")).content.decode()

    assert "BEGIN:VTODO" in text and "Own nudge" in text and "Secret nudge" not in text


def test_the_reminders_address_and_the_download_need_a_sign_in(client, db):
    for name in ("applications:reminder_calendar", "applications:calendar_download"):
        response = client.get(reverse(name))
        assert response.status_code == 302 and reverse("account_login") in response["Location"], (
            name
        )
    response = client.get(reverse("applications:ical_import"))
    assert response.status_code == 302 and reverse("account_login") in response["Location"]


def test_the_api_reminders_feed_is_scoped_and_needs_a_token(client, user, other_user):
    a_reminder(user, "Own nudge")
    a_reminder(other_user, "Secret nudge")

    text = client.get("/api/v1/reminders/calendar.ics", **bearer(user, "read")).content.decode()

    assert "Own nudge" in text and "Secret nudge" not in text
    assert client.get("/api/v1/reminders/calendar.ics").status_code == 401, "no token, no feed"


def test_the_calendar_download_holds_only_ones_own(client, user, other_user):
    an_interview(user, "Own visible role")
    an_interview(other_user, "Secret other role")
    a_reminder(user, "Own nudge")
    a_reminder(other_user, "Secret nudge")
    client.force_login(user)

    for query in ({"view": "agenda"}, {"month": timezone.localdate().strftime("%Y-%m")}):
        text = client.get(reverse("applications:calendar_download"), query).content.decode()
        assert "Secret" not in text, query
    agenda = client.get(reverse("applications:calendar_download"), {"view": "agenda"})
    assert "Own nudge" in agenda.content.decode()
    assert agenda["Cache-Control"] == "private, max-age=0, no-store"


def test_a_reminder_cannot_add_a_property_to_the_tasks_file(client, user):
    from postulo.applications.models import Reminder

    reminder = a_reminder(user, "Chase\r\nATTACH:https://evil.example/x\rX-INJECTED:1")
    Reminder.objects.filter(pk=reminder.pk).update(uid="abc\r\nATTENDEE:mailto:evil@example.org")
    client.force_login(user)

    text = client.get(reverse("applications:reminder_calendar")).content.decode()

    lines = text.split("\r\n")
    assert not any(line.startswith(("ATTACH", "X-INJECTED", "ATTENDEE")) for line in lines)
    assert [line for line in lines if line.startswith("BEGIN:")] == [
        "BEGIN:VCALENDAR",
        "BEGIN:VTODO",
    ]


def test_an_import_cannot_make_an_interview_on_somebody_elses_application(client, user, other_user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from postulo.applications.models import Interview

    theirs = an_interview(other_user, "Secret other role").application
    client.force_login(user)
    file = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x@y\r\nDTSTART:20991001T100000Z\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    client.post(reverse("applications:ical_import"), {"file": SimpleUploadedFile("a.ics", file)})

    response = client.post(
        reverse("applications:ical_import"),
        {"action": "confirm", "r0-action": "interview", "r0-application": theirs.pk},
    )

    assert response.status_code == 200, "refused as a choice that is not one"
    assert "Secret other role" not in response.content.decode(), "and its name is not drawn"
    assert not Interview.objects.filter(owner=user, uid="x@y").exists()
    assert Interview.objects.filter(application=theirs).count() == 1, "theirs is untouched"


def test_what_one_account_has_read_is_not_offered_to_another(client, user, other_user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    file = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x@y\r\nDTSTART:20991001T100000Z\r\n"
        b"SUMMARY:Private plans\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    client.force_login(user)
    client.post(reverse("applications:ical_import"), {"file": SimpleUploadedFile("a.ics", file)})
    client.logout()
    client.force_login(other_user)

    assert "Private plans" not in client.get(reverse("applications:ical_import")).content.decode()


def test_an_imported_text_is_escaped_where_it_is_drawn(client, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    file = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x@y\r\nDTSTART:20991001T100000Z\r\n"
        b"SUMMARY:<script>alert(1)</script>\r\nLOCATION:<img src=x onerror=alert(1)>\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    client.force_login(user)
    client.post(reverse("applications:ical_import"), {"file": SimpleUploadedFile("a.ics", file)})

    page = client.get(reverse("applications:ical_import")).content.decode()

    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert "&lt;script&gt;" in page


# ---------------------------------------------------------------- the pictures


def with_picture(person):
    from postulo.accounts import avatars
    from postulo.accounts.models import Profile

    profile, _created = Profile.objects.get_or_create(user=person)
    avatars.store(profile, avatars.ProfilePicture.UPLOAD, ContentFile(PNG))
    return profile


def served(response) -> int:
    """The status of a response that may carry a file, with the file closed behind it."""
    if hasattr(response, "streaming_content"):
        b"".join(response.streaming_content)
    response.close()
    return response.status_code


def test_a_picture_is_ones_own_or_an_administrators_to_see(client, user, other_user):
    with_picture(user)
    with_picture(other_user)

    client.force_login(user)
    assert served(client.get(reverse("accounts:avatar", args=[user.pk]))) == 200
    assert served(client.get(reverse("accounts:avatar", args=[other_user.pk]))) == 404

    user.is_staff = True
    user.save(update_fields=["is_staff"])
    assert served(client.get(reverse("accounts:avatar", args=[other_user.pk]))) == 200


def test_a_face_that_is_not_there_is_not_found_and_neither_is_a_stranger(client, user, db):
    client.force_login(user)
    assert client.get(reverse("accounts:avatar", args=[user.pk])).status_code == 404, "no picture"
    assert client.get(reverse("accounts:avatar", args=[10**6])).status_code == 404


def test_a_picture_needs_a_sign_in(client, user):
    with_picture(user)
    response = client.get(reverse("accounts:avatar", args=[user.pk]))
    assert response.status_code == 302 and reverse("account_login") in response["Location"]


def test_a_companys_logo_is_its_owners(client, user, other_user):
    mine = Company.objects.create(owner=user, name="Mine")
    logos.store(mine, ContentFile(PNG), source="upload")
    theirs = Company.objects.create(owner=other_user, name="Theirs")
    logos.store(theirs, ContentFile(PNG), source="upload")
    bare = Company.objects.create(owner=user, name="No logo")

    client.force_login(user)
    assert served(client.get(reverse("jobs:company_logo", args=[mine.pk]))) == 200
    assert served(client.get(reverse("jobs:company_logo", args=[theirs.pk]))) == 404
    assert served(client.get(reverse("jobs:company_logo", args=[bare.pk]))) == 404

    client.logout()
    response = client.get(reverse("jobs:company_logo", args=[mine.pk]))
    assert response.status_code == 302 and reverse("account_login") in response["Location"]


# ------------------------------------------------------------------ the archive


def test_the_export_is_a_post_that_holds_only_ones_own_records(client, user, other_user):
    Company.objects.create(owner=user, name="Own visible company")
    Company.objects.create(owner=other_user, name="Secret other company")

    assert client.post(reverse("core:export_download")).status_code == 302, "sign in first"

    client.force_login(user)
    assert client.get(reverse("core:export_download")).status_code == 405, (
        "not something a prefetching browser may trigger by following a link"
    )
    # The press asks for the archive and lands on the page that watches it; the file has
    # an address of its own now, served to the person whose account it holds (#247).
    from postulo.core.models import ExportArchive

    assert client.post(reverse("core:export_download")).status_code == 302
    theirs = ExportArchive.objects.for_user(user).get()
    response = client.get(reverse("core:export_archive", args=[theirs.pk]))
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, max-age=0, no-store"

    archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
    response.close()

    client.force_login(other_user)
    assert client.get(reverse("core:export_archive", args=[theirs.pk])).status_code == 404, (
        "an archive holds a whole job search, and it is one person's"
    )
    client.force_login(user)
    document = json.dumps(json.loads(archive.read("postulo.json")))
    assert "Own visible company" in document
    assert "Secret other company" not in document
