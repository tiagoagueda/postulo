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


# ---------------------------------------------------------------- the pictures


def with_picture(person):
    from postulo.accounts.models import Profile

    profile, _created = Profile.objects.get_or_create(user=person)
    profile.avatar.save("face.png", ContentFile(PNG), save=True)
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
    mine.logo.save("logo.png", ContentFile(PNG), save=True)
    theirs = Company.objects.create(owner=other_user, name="Theirs")
    theirs.logo.save("logo.png", ContentFile(PNG), save=True)
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
