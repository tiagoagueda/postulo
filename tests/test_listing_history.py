"""A listing's history: what arrives about a job before somebody applies for it (#270).

An application has had a timeline from the start and a listing had nothing, so the
counsellor's message, the description a contact forwarded, the reminder a board sends and
the same advert captured again all had nowhere to go. These are the promises the history
makes: one way in, the owner never the caller's to choose, a link to a file or a capture
that points and never owns, and one history read in two places rather than copied into one.
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.urls import reverse

from postulo.applications.models import EventKind, Status
from postulo.applications.services import apply_to_listing, record_event
from postulo.core import export, gdpr, importer
from postulo.documents.models import UploadedDocument
from postulo.jobs import history, merging
from postulo.jobs.history import NotBound, bind_capture, record_listing_event
from postulo.jobs.models import (
    SYSTEM_LISTING_EVENT_KINDS,
    Capture,
    CapturedPage,
    CaptureStatus,
    Company,
    Contact,
    JobPosting,
    ListingEvent,
    ListingEventKind,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Black Mesa")


@pytest.fixture
def listing(user, company):
    return JobPosting.objects.create(
        owner=user,
        company=company,
        title="Research Engineer",
        url="https://jobs.example.org/research-engineer",
    )


@pytest.fixture
def counsellor(user):
    return Contact.objects.create(owner=user, name="Marta Silva", role="Counsellor")


@pytest.fixture
def forwarded(user):
    """A job description somebody forwarded, kept where files are kept: the documents."""
    return UploadedDocument.objects.create(
        owner=user,
        title="Research Engineer description",
        file=ContentFile(b"%PDF-1.7 the description", name="description.pdf"),
    )


def a_capture(user, url="https://other-board.example/jobs/7", **data):
    return Capture.objects.create(
        owner=user,
        url=url,
        source_name="schema.org",
        data={"title": "Research Engineer", "company_name": "Black Mesa", **data},
    )


# --------------------------------------------------------------------- the words


def test_the_shared_kinds_are_the_timelines_own():
    """A note is a note either side of applying: same value, same words, one translation."""
    for kind in ("note", "email_received", "call", "other"):
        assert ListingEventKind(kind).label == EventKind(kind).label, kind
    assert set(ListingEventKind.values) - set(EventKind.values) == {
        "message",
        "document",
        "capture",
    }
    assert frozenset({ListingEventKind.CAPTURE}) == SYSTEM_LISTING_EVENT_KINDS


# --------------------------------------------------------------------- one way in


def test_an_entry_goes_on_the_listing_and_the_owner_is_the_listings(listing, counsellor):
    entry, created = record_listing_event(
        listing,
        kind=ListingEventKind.MESSAGE,
        summary="The counsellor sent the advert",
        body="Worth a look, I think.",
        contact=counsellor,
    )

    assert created
    assert entry.posting == listing and entry.contact == counsellor
    assert list(ListingEvent.objects.for_user(listing.owner)) == [entry]
    assert entry.occurred_at is not None and entry.actor == ""


def test_an_application_is_a_way_to_its_listing(user, listing):
    application = apply_to_listing(listing, {"status": Status.APPLIED})

    entry, _created = record_listing_event(application, summary="A second interview round")

    assert entry.posting_id == listing.pk
    assert not application.events.filter(summary="A second interview round").exists()


def test_nothing_but_a_listing_or_an_application_has_a_history(company):
    with pytest.raises(TypeError):
        record_listing_event(company, summary="A company has no history of this kind")


def test_a_kind_that_does_not_exist_is_refused(listing):
    with pytest.raises(NotBound, match="not a kind"):
        record_listing_event(listing, kind="status_change", summary="Moved")


def test_an_entry_scopes_through_its_listing(user, other_user, listing):
    record_listing_event(listing, summary="Mine")

    assert ListingEvent.objects.for_user(user).count() == 1
    assert not ListingEvent.objects.for_user(other_user).exists()
    assert not ListingEvent.objects.for_user(None).exists()


def test_a_message_id_binds_once(listing):
    first, made = record_listing_event(
        listing, kind="email_received", summary="Closes on Friday", external_id="<m1@board>"
    )
    again, made_again = record_listing_event(
        listing, kind="email_received", summary="Closes on Friday", external_id="<m1@board>"
    )

    assert made and not made_again and again.pk == first.pk
    assert listing.events.count() == 1


def test_the_same_message_may_be_about_two_listings(user, company, listing):
    other = JobPosting.objects.create(owner=user, company=company, title="Lab Assistant")

    record_listing_event(listing, summary="Two openings", external_id="<m2@board>")
    record_listing_event(other, summary="Two openings", external_id="<m2@board>")

    assert ListingEvent.objects.filter(external_id="<m2@board>").count() == 2


def test_a_strangers_words_are_held_to_a_length_and_to_one_line(listing):
    """#218's rule for text somebody else wrote: bounded, and nothing a database refuses."""
    long_body = "x" * (history.BODY_MAX_CHARS + 500)

    entry, _created = record_listing_event(
        listing,
        summary="Re: the role\n\t   you applied\x00 for" + " word" * 100,
        body=f"Hello\x00 there\n\n{long_body}",
        actor="imap\nplugin",
    )

    assert "\n" not in entry.summary and "\x00" not in entry.summary
    assert entry.summary.startswith("Re: the role you applied for word")
    assert len(entry.summary) == history.SUMMARY_MAX_CHARS
    assert "\x00" not in entry.body and entry.body.startswith("Hello there\n\n")
    assert entry.body.endswith(history.TRUNCATED)
    assert len(entry.body) <= history.BODY_MAX_CHARS + len(history.TRUNCATED)
    assert entry.actor == "imap plugin"


# ------------------------------------------------------------------ what it points at


def test_an_entry_points_at_a_file_in_the_documents(listing, forwarded):
    entry, _created = record_listing_event(
        listing, kind=ListingEventKind.DOCUMENT, summary="The description", artefact=forwarded
    )

    assert entry.points_at == forwarded and entry.bound_document == forwarded
    assert entry.bound_capture is None


def test_a_document_entry_has_a_document(listing):
    with pytest.raises(NotBound, match="document entry"):
        record_listing_event(listing, kind=ListingEventKind.DOCUMENT, summary="Missing")


def test_a_capture_is_bound_as_a_capture_and_only_that_way(user, listing):
    capture = a_capture(user)

    with pytest.raises(NotBound, match="capture entry"):
        record_listing_event(listing, kind=ListingEventKind.CAPTURE, summary="No capture")
    with pytest.raises(NotBound, match="as a capture entry"):
        record_listing_event(listing, kind=ListingEventKind.NOTE, artefact=capture)


def test_nothing_else_can_be_pointed_at(listing, company):
    with pytest.raises(NotBound, match="cannot point at Company"):
        record_listing_event(listing, summary="A company", artefact=company)


def test_deleting_the_listing_takes_its_history_and_leaves_the_file(listing, forwarded):
    """The `RenderedDocument.source` rule: no reverse relation, so no cascade into documents."""
    record_listing_event(listing, kind="document", summary="The description", artefact=forwarded)
    record_listing_event(listing, summary="A note, which is the entry's own text")

    listing.delete()

    assert not ListingEvent.objects.exists()
    assert UploadedDocument.objects.filter(pk=forwarded.pk).exists()


def test_discarding_the_listing_keeps_everything(listing, forwarded):
    record_listing_event(listing, kind="document", summary="The description", artefact=forwarded)

    listing.discard("pay")

    assert listing.events.count() == 1
    assert UploadedDocument.objects.filter(pk=forwarded.pk).exists()


def test_deleting_the_file_leaves_the_entry_with_its_words(listing, forwarded):
    entry, _created = record_listing_event(
        listing,
        kind="document",
        summary="The description Ana forwarded",
        body="She says the team is small.",
        artefact=forwarded,
    )

    forwarded.delete()
    entry.refresh_from_db()

    assert entry.summary == "The description Ana forwarded"
    assert entry.body == "She says the team is small."
    assert entry.artefact_type is None and entry.artefact_id is None
    assert entry.points_at is None and entry.bound_document is None


def test_deleting_a_capture_leaves_its_entry_with_its_words(user, listing):
    capture = a_capture(user)
    entry = bind_capture(capture, listing)

    Capture.objects.filter(pk=capture.pk).delete()
    entry.refresh_from_db()

    assert entry.artefact_id is None
    assert "other-board.example" in entry.summary
    assert "https://other-board.example/jobs/7" in entry.body


def test_an_entry_never_draws_somebody_elses_record(user, other_user, listing):
    """Checked when the link is made; checked again when it is read, for a row that the
    database was handed some other way."""
    theirs = UploadedDocument.objects.create(
        owner=other_user, title="Theirs", file=ContentFile(b"%PDF-1.7 x", name="theirs.pdf")
    )
    with pytest.raises(NotBound, match="somebody other"):
        record_listing_event(listing, kind="document", summary="Theirs", artefact=theirs)

    entry = ListingEvent.objects.create(
        posting=listing,
        summary="Forged",
        artefact_type=ContentType.objects.get_for_model(UploadedDocument),
        artefact_id=theirs.pk,
    )
    assert entry.points_at is None and entry.bound_document is None


# ------------------------------------------------------- a second capture of the advert


def test_a_second_capture_is_bound_rather_than_discarded(user, listing):
    capture = a_capture(user)

    entry = bind_capture(capture, listing)

    capture.refresh_from_db()
    assert capture.status == CaptureStatus.ACCEPTED and capture.posting == listing
    assert entry.kind == ListingEventKind.CAPTURE and entry.bound_capture == capture
    assert entry.summary == "Captured again from other-board.example"
    assert entry.body == "Research Engineer\nhttps://other-board.example/jobs/7"
    assert entry.occurred_at == capture.created_at
    assert JobPosting.objects.count() == 1, "no second listing for one job"


def test_a_capture_already_decided_is_not_bound(user, listing):
    capture = a_capture(user)
    Capture.objects.filter(pk=capture.pk).update(status=CaptureStatus.DISCARDED)

    with pytest.raises(history.AlreadyDecided):
        bind_capture(capture, listing)
    assert not listing.events.exists()


def test_the_review_screen_offers_to_bind_where_the_advert_is_known(client, user, listing):
    same_address = a_capture(user, url="https://www.jobs.example.org/research-engineer/")
    another_board = a_capture(user)
    client.force_login(user)

    for capture in (same_address, another_board):
        html = client.get(reverse("jobs:capture_review", args=[capture.pk])).content.decode()
        assert reverse("jobs:capture_bind", args=[capture.pk]) in html, capture.url
        assert f'name="listing" value="{listing.pk}"' in html
        assert "data-bind-explained" in html


def test_the_review_screen_offers_nothing_for_a_new_advert(client, user, listing):
    capture = a_capture(user, url="https://elsewhere.example/1", title="Chef")
    client.force_login(user)

    html = client.get(reverse("jobs:capture_review", args=[capture.pk])).content.decode()

    assert "data-bind-capture" not in html


def test_binding_from_the_review_screen_says_so_and_lands_on_the_history(client, user, listing):
    capture = a_capture(user)
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_bind", args=[capture.pk]), {"listing": listing.pk}, follow=True
    )

    assert response.redirect_chain[-1][0] == f"{listing.get_absolute_url()}#history"
    html = response.content.decode()
    assert "Added to the history of" in html
    assert 'data-listing-event="capture"' in html
    assert "Open the advert" in html


def test_binding_and_next_moves_on_to_the_next_capture(client, user, listing):
    older = a_capture(user, url="https://elsewhere.example/2", title="Chef")
    capture = a_capture(user)
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_bind", args=[capture.pk]), {"listing": listing.pk, "next": "1"}
    )

    assert response.status_code == 302 and response.url == older.get_absolute_url()


def test_binding_a_capture_twice_records_it_once(client, user, listing):
    capture = a_capture(user)
    client.force_login(user)
    url = reverse("jobs:capture_bind", args=[capture.pk])

    client.post(url, {"listing": listing.pk})
    response = client.post(url, {"listing": listing.pk}, follow=True)

    assert listing.events.count() == 1
    assert "already saved or discarded" in response.content.decode()


def test_a_bound_capture_that_kept_its_page_is_pointed_at_not_copied(client, user, listing):
    capture = a_capture(user)
    page = CapturedPage.objects.create(
        owner=user,
        capture=capture,
        source=ContentFile(b"\x1f\x8b kept", name="kept.txt.gz"),
        source_size=4,
    )
    client.force_login(user)

    client.post(reverse("jobs:capture_bind", args=[capture.pk]), {"listing": listing.pk})
    html = client.get(listing.get_absolute_url()).content.decode()

    assert reverse("jobs:capture_page", args=[capture.pk]) in html
    assert CapturedPage.objects.get().pk == page.pk, "the page stays the capture's"
    assert listing.events.get().body.count("\n") == 1, "the address and the title, not the page"


# ------------------------------------------------------------------ the listing's page


def test_the_listings_page_shows_its_history_and_the_form(client, user, listing, counsellor):
    record_listing_event(
        listing, kind="call", summary="Rang about the salary", body="Up to 60k.", contact=counsellor
    )
    client.force_login(user)

    html = client.get(listing.get_absolute_url()).content.decode()

    assert 'id="history"' in html and "Rang about the salary" in html
    assert "Up to 60k." in html and "Marta Silva" in html
    assert reverse("listings:event_create", args=[listing.pk]) in html


def test_an_empty_history_says_so(client, user, listing):
    client.force_login(user)

    html = client.get(listing.get_absolute_url()).content.decode()

    assert "Nothing has arrived about this one yet." in html


def test_adding_an_entry_from_the_listings_page(client, user, listing, counsellor, forwarded):
    client.force_login(user)

    response = client.post(
        reverse("listings:event_create", args=[listing.pk]),
        {
            "kind": "document",
            "occurred_at": "2026-09-20T10:30",
            "summary": "The description Marta forwarded",
            "body": "",
            "contact": counsellor.pk,
            "document": forwarded.pk,
        },
    )

    assert response.status_code == 302
    assert response.url == f"{listing.get_absolute_url()}#history"
    entry = listing.events.get()
    assert entry.kind == "document" and entry.contact == counsellor
    assert entry.bound_document == forwarded and entry.actor == ""


def test_a_mistake_draws_the_listings_page_again_with_the_errors(client, user, listing, forwarded):
    client.force_login(user)

    response = client.post(
        reverse("listings:event_create", args=[listing.pk]),
        {"kind": "document", "occurred_at": "2026-09-20T10:30", "summary": "No file"},
    )

    assert response.status_code == 200
    assert "Choose which of your files it is." in response.content.decode()
    assert not listing.events.exists()


def test_the_form_offers_only_the_persons_own_people_and_files(
    user, other_user, counsellor, forwarded
):
    from postulo.jobs.forms import ListingEventForm

    Contact.objects.create(owner=other_user, name="Theirs")
    UploadedDocument.objects.create(
        owner=other_user, title="Theirs", file=ContentFile(b"%PDF-1.7 x", name="t.pdf")
    )

    form = ListingEventForm(user=user)

    assert list(form.fields["contact"].queryset) == [counsellor]
    assert list(form.fields["document"].queryset) == [forwarded]
    assert "capture" not in dict(form.fields["kind"].choices)


def test_with_no_files_there_is_no_file_to_choose_and_no_document_kind(user):
    from postulo.jobs.forms import ListingEventForm

    form = ListingEventForm(user=user)

    assert "document" not in form.fields and "contact" not in form.fields
    assert "document" not in dict(form.fields["kind"].choices)


def test_the_form_refuses_a_body_longer_than_an_entry_keeps(user, listing):
    from postulo.jobs.forms import ListingEventForm

    form = ListingEventForm(
        {
            "kind": "note",
            "occurred_at": "2026-09-20T10:30",
            "body": "x" * (history.BODY_MAX_CHARS + 1),
        },
        user=user,
    )

    assert not form.is_valid() and "body" in form.errors


def test_deleting_a_listing_says_what_goes_and_what_stays(client, user, listing, forwarded):
    record_listing_event(listing, kind="document", summary="The description", artefact=forwarded)
    record_listing_event(listing, summary="A note")
    client.force_login(user)

    html = client.get(reverse("jobs:posting_delete", args=[listing.pk])).content.decode()

    assert "2 entries in its history" in html
    assert "The 1 file or capture its history points at is kept where it is." in html


# ------------------------------------------------------------ the application's page


def test_the_application_reads_the_listings_history_first(client, user, listing):
    record_listing_event(listing, kind="message", summary="The counsellor wrote")
    application = apply_to_listing(listing, {"status": Status.APPLIED})
    record_event(application, summary="Chased by email")
    client.force_login(user)

    html = client.get(application.get_absolute_url()).content.decode()

    assert "The counsellor wrote" in html and "Chased by email" in html
    assert html.index('data-history-part="listing"') < html.index('data-history-part="application"')
    assert html.index("The counsellor wrote") < html.index("Chased by email")
    assert not application.events.filter(summary="The counsellor wrote").exists(), (
        "carried by reference, never copied"
    )


def test_an_application_whose_listing_has_no_history_looks_as_it_did(client, user, listing):
    application = apply_to_listing(listing, {"status": Status.APPLIED})
    client.force_login(user)

    html = client.get(application.get_absolute_url()).content.decode()

    assert "data-history-part" not in html


def test_the_swapped_timeline_keeps_both_parts(client, user, listing):
    """An entry added in place redraws the timeline out of band (#257); both halves come back."""
    record_listing_event(listing, kind="message", summary="The counsellor wrote")
    application = apply_to_listing(listing, {"status": Status.APPLIED})
    client.force_login(user)

    response = client.post(
        reverse("applications:event_create", args=[application.pk]),
        {"kind": "note", "occurred_at": "2026-09-20T10:30", "summary": "A note", "body": ""},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="event-form",
    )

    html = response.content.decode()
    assert 'hx-swap-oob="true"' in html
    assert "The counsellor wrote" in html and "A note" in html


# ------------------------------------------------------------------------ merging


def test_a_listings_history_survives_a_merge_of_its_company(user, company, listing):
    """#239 moves a company's postings to the one kept; the history goes with each posting."""
    duplicate = Company.objects.create(owner=user, name="Black Mesa Ltd")
    elsewhere = JobPosting.objects.create(owner=user, company=duplicate, title="Lab Assistant")
    record_listing_event(elsewhere, kind="call", summary="They rang back")
    record_listing_event(listing, summary="Mine all along")

    merging.merge_companies(company, duplicate)

    elsewhere.refresh_from_db()
    assert elsewhere.company == company
    assert elsewhere.events.get().summary == "They rang back"
    assert ListingEvent.objects.for_user(user).count() == 2


def test_merging_a_person_moves_what_came_from_them(user, listing, counsellor):
    twin = Contact.objects.create(owner=user, name="Marta Silva", email="marta@example.org")
    record_listing_event(listing, kind="message", summary="Her message", contact=twin)

    plan = merging.plan_contacts(counsellor, twin)
    merging.merge_contacts(counsellor, twin)

    assert any("listing histories" in line.label for line in plan.moves)
    assert listing.events.get().contact == counsellor


# ----------------------------------------------------------- the archive and the person


def test_the_archive_carries_the_history_under_its_listing(user, listing, counsellor, forwarded):
    capture = a_capture(user)
    bind_capture(capture, listing)
    record_listing_event(
        listing, kind="document", summary="The description", artefact=forwarded, contact=counsellor
    )
    record_listing_event(listing, kind="email_received", summary="Closes", external_id="<m@b>")

    document = export.build_document(user)

    # 23 is the format that added the history (#270); the number itself is pinned in
    # test_phone_verification.py, where a change to it is written down.
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 23
    [posting] = [p for c in document["companies"] for p in c["postings"]]
    entries = {entry["kind"]: entry for entry in posting["events"]}
    assert entries["capture"]["artefact_kind"] == "capture"
    assert entries["capture"]["artefact_ref"] == capture.pk
    assert entries["document"]["artefact_kind"] == "uploadeddocument"
    assert entries["document"]["artefact_ref"] == forwarded.pk
    assert entries["document"]["contact_id"] == counsellor.pk
    assert entries["email_received"]["external_id"] == "<m@b>"
    assert entries["email_received"]["artefact_kind"] == ""
    assert document["counts"]["listing_events"] == 3 == export.counts(user)["listing_events"]


def test_the_history_comes_back_from_the_archive(user, other_user, listing, counsellor, forwarded):
    capture = a_capture(user)
    bind_capture(capture, listing)
    record_listing_event(
        listing, kind="document", summary="The description", artefact=forwarded, contact=counsellor
    )
    record_listing_event(listing, kind="email_received", summary="Closes", external_id="<m@b>")

    report = importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))

    assert report.listing_events == 3
    restored = JobPosting.objects.for_user(other_user).get()
    entries = {entry.kind: entry for entry in restored.events.all()}
    assert entries["capture"].bound_capture == Capture.objects.for_user(other_user).get()
    assert entries["document"].bound_document == UploadedDocument.objects.for_user(other_user).get()
    assert entries["document"].contact == Contact.objects.for_user(other_user).get()
    assert entries["email_received"].external_id == "<m@b>"
    assert entries["capture"].summary == "Captured again from other-board.example"


def test_an_archive_believes_nothing_about_a_history(user, other_user, listing, counsellor):
    """An unknown kind is *other*, a pointer of the wrong kind is dropped, a repeated message
    id is one entry, the words are held to the lengths a history keeps, and an id or a moment
    of the wrong type is nothing rather than a failed import."""
    record_listing_event(listing, summary="Kept")
    capture = a_capture(user)
    buffer = export.write_archive(user)
    source = zipfile.ZipFile(buffer)
    document = json.loads(source.read(export.MANIFEST_NAME))
    [posting] = [p for c in document["companies"] for p in c["postings"]]
    posting["events"] = [
        {"kind": "status_change", "summary": "Weird kind", "occurred_at": None},
        {
            "kind": "note",
            "summary": "A note pointing at a capture",
            "artefact_kind": "capture",
            "artefact_ref": capture.pk,
        },
        {"kind": "note", "summary": "Twice", "external_id": "<x@y>"},
        {"kind": "note", "summary": "Twice", "external_id": "<x@y>"},
        {"kind": "note", "summary": "y" * 400, "body": "z" * (history.BODY_MAX_CHARS + 10)},
        {
            "kind": "call",
            "summary": "Odd types",
            "contact_id": [counsellor.pk],
            "artefact_kind": "capture",
            "artefact_ref": str(capture.pk),
            "occurred_at": "2026-13-45T25:00:00",
        },
        {"kind": "call", "summary": "Named nobody", "contact_id": None},
        "not an entry",
    ]
    rewritten = zipfile.ZipFile(buffer_for(document, source), "r")

    importer.load(other_user, rewritten)

    restored = {entry.summary[:20]: entry for entry in ListingEvent.objects.for_user(other_user)}
    assert len(restored) == 6
    assert restored["Weird kind"].kind == "other"
    assert restored["A note pointing at a"].artefact_id is None, "a capture is a capture entry"
    assert len(restored["y" * 20].summary) == history.SUMMARY_MAX_CHARS
    assert restored["y" * 20].body.endswith(history.TRUNCATED)
    odd = restored["Odd types"]
    assert odd.contact is None and odd.artefact_id is None and odd.occurred_at is not None
    assert restored["Named nobody"].contact is None


def buffer_for(document: dict, source: zipfile.ZipFile):
    """The same archive with a different manifest."""
    from io import BytesIO

    out = BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name in source.namelist():
            if name != export.MANIFEST_NAME:
                archive.writestr(name, source.read(name))
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    out.seek(0)
    return out


def test_an_archive_from_before_the_history_still_imports(user, other_user, listing):
    buffer = export.write_archive(user)
    source = zipfile.ZipFile(buffer)
    document = json.loads(source.read(export.MANIFEST_NAME))
    document["postulo"]["format"] = 21
    for company in document["companies"]:
        for posting in company["postings"]:
            posting.pop("events")

    report = importer.load(other_user, zipfile.ZipFile(buffer_for(document, source)))

    assert report.postings == 1 and report.listing_events == 0


def test_the_person_named_finds_what_came_from_them_in_their_document(listing, counsellor):
    record_listing_event(
        listing, kind="message", summary="Her message", body="Apply by Friday.", contact=counsellor
    )

    document = gdpr.contact_document(counsellor)

    assert document["version"] == gdpr.DOCUMENT_VERSION
    assert document["listing_events"] == [
        {
            "listing": "Research Engineer",
            "company": "Black Mesa",
            "kind": "message",
            "occurred_at": listing.events.get().occurred_at.isoformat(),
            "summary": "Her message",
            "body": "Apply by Friday.",
        }
    ]


def test_erasing_the_person_keeps_the_entry_and_says_so(listing, counsellor):
    record_listing_event(listing, kind="message", summary="Her message", contact=counsellor)

    report = gdpr.erase_contact(counsellor)

    entry = listing.events.get()
    assert entry.contact is None and entry.summary == "Her message"
    assert report.unlinked["listing_events"] == 1
    assert "1 entry in a listing's history kept, without who it came from." in report.summary()


def test_the_history_goes_with_the_account(user, listing):
    from postulo.accounts.deletion import delete_account

    record_listing_event(listing, summary="Mine")

    delete_account(user)

    assert not ListingEvent.objects.exists()


def test_the_pages_that_say_what_an_archive_holds_count_the_history(client, user, listing):
    record_listing_event(listing, summary="Mine")
    client.force_login(user)

    html = client.get(reverse("core:export")).content.decode()

    assert "Entries in listing histories" in html


# ---------------------------------------------------------------------------- the API


def bearer(user, *scopes):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Thunderbird", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def test_a_mail_client_files_a_message_into_a_listings_history(client, user, listing, counsellor):
    token = bearer(user, "listings:bind")
    payload = {
        "kind": "email_received",
        "summary": "Re: Research Engineer",
        "body": "We would like to talk.",
        "occurred_at": "2026-09-20T10:30:00Z",
        "contact_id": counsellor.pk,
        "external_id": "<talk@blackmesa.example>",
    }

    first = post(client, f"/api/v1/listings/{listing.pk}/events", payload, **token)
    again = post(client, f"/api/v1/listings/{listing.pk}/events", payload, **token)

    assert first.status_code == 201, first.content
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    body = first.json()
    assert body["listing_id"] == listing.pk and body["kind"] == "email_received"
    assert body["contact_id"] == counsellor.pk and body["actor"] == "API token Thunderbird"
    assert listing.events.get().occurred_at == dt.datetime(2026, 9, 20, 10, 30, tzinfo=dt.UTC)


def test_a_mail_client_chooses_from_a_brief_list(client, user, company, listing):
    JobPosting.objects.create(owner=user, company=company, title="Lab Assistant")
    token = bearer(user, "listings:bind")

    listed = client.get("/api/v1/listings/choices?q=research", **token).json()

    assert listed["count"] == 1
    assert set(listed["items"][0]) == {
        "id",
        "title",
        "company_name",
        "location",
        "state",
        "noted_at",
        "updated_at",
    }, "a title to recognise, and nothing of the search"
    assert client.get("/api/v1/listings/choices?q=black", **token).json()["count"] == 2
    assert client.get("/api/v1/listings/choices?state=weird", **token).status_code == 422


def test_the_file_a_document_entry_names_is_the_callers_own(client, user, listing, forwarded):
    token = bearer(user, "write")
    path = f"/api/v1/listings/{listing.pk}/events"

    made = post(
        client, path, {"kind": "document", "summary": "JD", "document_id": forwarded.pk}, **token
    )
    missing = post(client, path, {"kind": "document", "summary": "JD"}, **token)
    captured = post(client, path, {"kind": "capture", "summary": "No"}, **token)

    assert made.status_code == 201 and made.json()["document_id"] == forwarded.pk
    assert missing.status_code == 422 and "document_id" in missing.json()["detail"]
    assert captured.status_code == 422 and "'kind'" in captured.json()["detail"]


def test_a_read_token_sees_the_history_on_the_listing(client, user, listing, forwarded):
    record_listing_event(listing, kind="document", summary="JD", artefact=forwarded)

    detail = client.get(f"/api/v1/listings/{listing.pk}", **bearer(user, "read")).json()

    assert [entry["summary"] for entry in detail["events"]] == ["JD"]
    assert detail["events"][0]["document_id"] == forwarded.pk


def test_the_new_scope_is_offered_on_the_token_form_and_described(client, user):
    from postulo.api.models import SCOPES

    assert "listings:bind" in SCOPES
    client.force_login(user)

    html = client.get(reverse("api:token_list")).content.decode()
    schema = client.get("/api/v1/openapi.json").json()

    assert 'value="listings:bind"' in html
    assert "`listings:bind`" in schema["info"]["description"]
    record = schema["paths"]["/api/v1/listings/{pk}/events"]["post"]["responses"]
    assert {200, 201, 403, 404, 422} <= {int(code) for code in record}
