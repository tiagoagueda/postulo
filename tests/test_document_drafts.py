"""A draft PDF is not a sent document (#236).

The preview is HTML and cannot show where a page ends, so checking a layout meant pressing
*Export PDF* -- and every press of that was a record: a row under *Versions you have sent*,
a line in *Sent documents*, and a copy queued for every store somebody had connected.

Three things are held here. A draft is the PDF and nothing else. An export that was already
filed is handed back rather than filed again. And a store is sent what went with an
application, which an export did not.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib

import pytest
from django.apps import apps as installed_apps
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.documents import archiving, rendering
from postulo.documents.models import (
    CV,
    CopyStatus,
    CoverLetter,
    CVItem,
    DocumentCopy,
    DocumentKind,
    RenderedDocument,
)
from postulo.documents.pdf import PDFBackendUnavailable
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import registry
from postulo.plugins.api import ExternalRef
from postulo.plugins.models import Connection
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db


class Drawing:
    """A renderer whose PDF depends on the page it was given, as a real one's does.

    The stand-in the older tests share answers every page with the same bytes, which is
    fine for them and useless here: whether two documents are the same file is the
    question.
    """

    name = "drawing"

    def __init__(self) -> None:
        self.drawn: list[str] = []

    def is_available(self) -> bool:
        return True

    def render(self, html: str) -> bytes:
        self.drawn.append(html)
        return b"%PDF-1.7 " + hashlib.sha256(html.encode()).hexdigest().encode()


class Stamping(Drawing):
    """Chromium, as far as this is concerned: every PDF carries the moment it was printed,
    so the same page drawn twice is two different files."""

    def render(self, html: str) -> bytes:
        return super().render(html) + f" printed {len(self.drawn)}".encode()


@pytest.fixture
def drawing(monkeypatch):
    backend = Drawing()
    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", lambda name=None: backend)
    return backend


@pytest.fixture
def experience(user):
    return Experience.objects.create(
        owner=user,
        organisation="Aperture Science",
        role="Senior Engineer",
        start_date=datetime.date(2021, 3, 1),
        highlights="Cut deploy time from 40 minutes to 4.",
    )


@pytest.fixture
def cv(user, experience):
    variant = CV.objects.create(owner=user, name="Backend EN", headline="Backend engineer")
    CVItem.objects.create(
        owner=user,
        cv=variant,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
        order=0,
    )
    return variant


@pytest.fixture
def letter(user):
    return CoverLetter.objects.create(
        owner=user,
        name="General",
        subject="Application for {{ role }}",
        body="Dear {{ company }},\n\nI am writing about {{ role }} in {{ location }}.",
    )


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Research Engineer", location="Paris"
    )
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


class Shelf:
    """A store, as a package would ship one: it says yes and remembers nothing."""

    name = "shelf-236"
    version = "0.1"
    kind = "store"
    label = "Shelf"

    def config_fields(self):
        return []

    def test(self, config):
        from postulo.plugins.base import TestResult

        return TestResult(True, "shelved")

    def put(self, document, file, metadata, config, user):
        return ExternalRef(store=self.name, id="1", url="https://shelf.example/1")


@pytest.fixture
def store(user):
    """A store the person has connected, which is when any of this matters."""
    registry.register_builtin("store", Shelf)
    try:
        yield Connection.objects.create(
            owner=user, kind="store", plugin=Shelf.name, label="My shelf", config={}
        )
    finally:
        registry.unregister_builtin("store", Shelf)


def copies_of(document):
    return DocumentCopy.objects.filter(
        document_type=ContentType.objects.get_for_model(document), document_id=document.pk
    )


# ------------------------------------------------------------------------ a draft


def test_a_draft_of_a_cv_is_the_pdf_and_nothing_else(client, user, cv, drawing, store):
    client.force_login(user)

    response = client.get(reverse("documents:cv_draft", args=[cv.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert "Senior Engineer" in drawing.drawn[0], "the CV as it stands, drawn"
    assert not RenderedDocument.objects.exists(), "nothing under Versions you have sent"
    assert not DocumentCopy.objects.exists(), "and nothing queued for a store"


def test_a_draft_is_handed_over_as_a_file_that_says_it_is_one(client, user, cv, drawing):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])
    client.force_login(user)

    response = client.get(reverse("documents:cv_draft", args=[cv.pk]))

    disposition = response["Content-Disposition"]
    assert disposition.startswith("attachment;")
    # Named for the person and the kind, as a snapshot is (#223), never for the shelf.
    assert "Alex" in disposition and "draft" in disposition and ".pdf" in disposition
    assert "Backend" not in disposition
    assert "no-store" in response["Cache-Control"], "somebody's CV, in nobody's cache"
    assert response["X-Content-Type-Options"] == "nosniff"
    # What a private file on disk carries, carried by one that never reached the disk.
    from postulo.core.files import FILE_POLICY

    assert response["Content-Security-Policy"] == FILE_POLICY


def test_a_draft_changes_nothing_whether_it_is_asked_for_or_posted_for(client, user, cv, drawing):
    """A GET, and a POST where the page's form carries what the file is to say about itself
    (#480) -- which keeps that out of an address. Neither files anything."""
    client.force_login(user)

    assert client.get(reverse("documents:cv_draft", args=[cv.pk])).status_code == 200
    assert client.post(reverse("documents:cv_draft", args=[cv.pk])).status_code == 200
    assert not RenderedDocument.objects.exists()
    assert client.put(reverse("documents:cv_draft", args=[cv.pk])).status_code == 405


def test_the_same_draft_asked_for_twice_is_drawn_once(client, user, cv, drawing):
    """A browser reloading a download asks again, and a render is seconds (#220)."""
    client.force_login(user)

    client.get(reverse("documents:cv_draft", args=[cv.pk]))
    client.get(reverse("documents:cv_draft", args=[cv.pk]))

    assert len(drawing.drawn) == 1


def test_a_draft_of_somebody_elses_cv_is_not_found(client, other_user, cv, drawing):
    client.force_login(other_user)

    assert client.get(reverse("documents:cv_draft", args=[cv.pk])).status_code == 404
    assert drawing.drawn == [], "nothing was drawn for them either"


def test_no_renderer_is_a_sentence_on_the_cvs_page(client, user, cv, monkeypatch):
    """Postulo is usable without PDF export, so this is an explanation and not a crash."""

    def unavailable(name=None):
        raise PDFBackendUnavailable("No PDF backend is installed")

    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", unavailable)
    client.force_login(user)

    response = client.get(reverse("documents:cv_draft", args=[cv.pk]), follow=True)

    assert response.redirect_chain[-1][0] == cv.get_absolute_url()
    assert "No PDF backend is installed" in response.content.decode()


def test_a_theme_that_cannot_set_the_document_is_a_sentence_too(client, user, cv, drawing):
    from postulo.documents import themes

    themes.register(
        themes.Theme(
            name="letters-only",
            label="Letters only",
            templates={themes.Kind.LETTER: "documents/themes/plain/letter.html"},
        )
    )
    try:
        CV.objects.filter(pk=cv.pk).update(theme="letters-only")
        client.force_login(user)

        response = client.get(reverse("documents:cv_draft", args=[cv.pk]), follow=True)
    finally:
        themes.forget("letters-only")

    assert response.redirect_chain[-1][0] == cv.get_absolute_url()
    assert "does not set this type of document" in response.content.decode()


def test_a_draft_of_a_letter_reads_for_the_application_it_is_given(
    client, user, letter, application, drawing
):
    client.force_login(user)

    response = client.get(
        reverse("documents:letter_draft", args=[letter.pk]), {"application": application.pk}
    )

    assert response["Content-Type"] == "application/pdf"
    assert "Black Mesa" in drawing.drawn[0] and "Research Engineer" in drawing.drawn[0]
    assert not RenderedDocument.objects.exists()


def test_a_draft_of_a_letter_marks_what_it_has_nothing_to_fill_in(client, user, letter, drawing):
    """The preview's rule, for the preview's reason: this is read before deciding (#235)."""
    client.force_login(user)

    client.get(reverse("documents:letter_draft", args=[letter.pk]))

    assert "[ company ]" in drawing.drawn[0]
    assert "Dear ," not in drawing.drawn[0]


def test_a_letters_draft_takes_nothing_from_somebody_elses_application(
    client, other_user, application, drawing
):
    theirs = CoverLetter.objects.create(owner=other_user, name="Theirs", body="Dear {{ company }},")
    client.force_login(other_user)

    client.get(reverse("documents:letter_draft", args=[theirs.pk]), {"application": application.pk})

    assert "Black Mesa" not in drawing.drawn[0]


def test_an_application_that_is_not_a_number_is_no_application(client, user, letter, drawing):
    client.force_login(user)

    response = client.get(
        reverse("documents:letter_draft", args=[letter.pk]), {"application": "abc"}
    )

    assert response.status_code == 200


def test_both_pages_offer_the_draft_and_say_what_it_is(client, user, cv, letter, application):
    client.force_login(user)

    page = client.get(cv.get_absolute_url()).content.decode()
    assert reverse("documents:cv_draft", args=[cv.pk]) in page
    assert "Download draft PDF" in page
    assert "A draft is kept nowhere" in page
    assert "Export PDF" in page, "and the button that files a version is still there"

    page = client.get(letter.get_absolute_url()).content.decode()
    assert f'formaction="{reverse("documents:letter_draft", args=[letter.pk])}"' in page
    assert 'formtarget="_self"' in page, "the answer is a file, not a page inside the frame"


def test_a_letter_with_no_application_to_choose_still_offers_the_draft(client, user, letter):
    client.force_login(user)

    page = client.get(letter.get_absolute_url()).content.decode()

    assert f'href="{reverse("documents:letter_draft", args=[letter.pk])}"' in page


# ------------------------------------------------- an export is filed once per version


def test_exporting_an_unchanged_cv_twice_files_one_record(cv):
    backend = Drawing()

    first = rendering.snapshot_cv(cv, backend=backend)
    second = rendering.snapshot_cv(cv, backend=backend)

    assert second.pk == first.pk
    assert RenderedDocument.objects.count() == 1
    assert len(backend.drawn) == 1, "and the second press drew nothing"


def test_it_is_one_record_where_every_pdf_carries_the_minute_it_was_printed(cv):
    """Chromium's do, so two renders of one page never share a checksum there. That is why
    the question is asked of what the PDF was drawn from before it is asked of the PDF."""
    backend = Stamping()

    first = rendering.snapshot_cv(cv, backend=backend)
    second = rendering.snapshot_cv(cv, backend=backend)

    assert second.pk == first.pk and RenderedDocument.objects.count() == 1


def test_a_cv_that_changed_is_a_new_version(cv, experience):
    backend = Drawing()
    first = rendering.snapshot_cv(cv, backend=backend)

    experience.role = "Principal Engineer"
    experience.save()
    second = rendering.snapshot_cv(cv, backend=backend)

    assert second.pk != first.pk
    assert RenderedDocument.objects.count() == 2
    assert not getattr(second, "already_filed", False)


def test_the_same_file_is_the_same_document_whatever_was_on_the_way_to_it(cv):
    """What the checksum is for. It has been computed for every render since #133 and until
    now compared with nothing."""

    class Unmoved(Drawing):
        def render(self, html: str) -> bytes:
            self.drawn.append(html)
            return b"%PDF-1.7 the same page either way"

    backend = Unmoved()
    first = rendering.snapshot_cv(cv, backend=backend)
    # Something that reaches the markup and not the page: a space after the headline.
    cv.headline = "Backend engineer "
    cv.save()
    assert rendering.render_cv_html(cv) != first.source_text, "the markup did change"

    second = rendering.snapshot_cv(cv, backend=backend)

    assert second.pk == first.pk and second.already_filed
    assert len(backend.drawn) == 2, "it had to be drawn to find out"


def test_what_went_with_an_application_is_always_its_own_record(cv, application):
    """Two employers sent the same CV were sent two things, however alike they are."""
    backend = Drawing()

    first = rendering.snapshot_cv(cv, application=application, backend=backend)
    second = rendering.snapshot_cv(cv, application=application, backend=backend)
    exported = rendering.snapshot_cv(cv, backend=backend)

    assert len({first.pk, second.pk, exported.pk}) == 3
    assert exported.application is None, "and an export is not handed what somebody was sent"


def test_a_record_whose_file_has_gone_is_not_handed_back(cv):
    """Somebody asking for their CV is not answered with a row that has no PDF behind it."""
    backend = Drawing()
    first = rendering.snapshot_cv(cv, backend=backend)
    first.file.storage.delete(first.file.name)

    second = rendering.snapshot_cv(cv, backend=backend)

    assert second.pk != first.pk
    assert second.file_is_as_rendered()


def test_a_file_that_was_changed_underneath_its_record_is_not_handed_back_either(cv):
    backend = Drawing()
    first = rendering.snapshot_cv(cv, backend=backend)
    name = first.file.name
    first.file.storage.delete(name)
    first.file.storage.save(name, ContentFile(b"%PDF-1.7 something else"))
    assert not first.file_is_as_rendered()

    assert rendering.snapshot_cv(cv, backend=backend).pk != first.pk


def test_one_persons_export_is_never_anothers(cv, other_user, experience):
    """The same words on two accounts are two people's documents."""
    theirs = CV.objects.create(owner=other_user, name="Backend EN", headline="Backend engineer")
    backend = Drawing()

    mine = rendering.snapshot_cv(cv, backend=backend)
    other = rendering.snapshot_cv(theirs, backend=backend)

    assert mine.pk != other.pk and other.owner == other_user


def test_the_press_says_so_when_nothing_was_drawn(client, user, cv, drawing):
    """ "PDF created." of a PDF filed last week is the page saying something that did not
    happen."""
    client.force_login(user)

    first = client.post(reverse("documents:cv_export", args=[cv.pk]))
    second = client.post(reverse("documents:cv_export", args=[cv.pk]))

    assert "PDF created." in client.get(first.url).content.decode()
    page = client.get(second.url).content.decode()
    assert "this is the PDF already filed" in page and "PDF created." not in page
    filed = RenderedDocument.objects.get()
    assert reverse("documents:rendered_download", args=[filed.pk]) in page


# ------------------------------------------------------ what a store is sent, and is not


def test_a_pdf_exported_on_its_own_is_queued_for_no_store(cv, store):
    exported = rendering.snapshot_cv(cv, backend=Drawing())

    assert not exported.goes_to_stores
    assert not copies_of(exported).exists()


def test_what_went_with_an_application_is_queued_as_it_always_was(cv, application, store):
    sent = rendering.snapshot_cv(cv, application=application, backend=Drawing())

    assert sent.goes_to_stores
    assert [copy.status for copy in copies_of(sent)] == [CopyStatus.PENDING]


def test_send_everything_leaves_an_export_where_it_is(cv, application, store):
    """The rule is at the one door, so the backfill cannot go round it."""
    DocumentCopy.objects.all().delete()
    rendering.snapshot_cv(cv, backend=Drawing())
    sent = rendering.snapshot_cv(cv, application=application, backend=Drawing())
    DocumentCopy.objects.all().delete()

    assert archiving.backfill(store) == 1

    assert [copy.document_id for copy in DocumentCopy.objects.all()] == [sent.pk]


def test_send_now_on_an_export_says_why_not(client, user, cv, store):
    """No button is drawn for one, so this is a form kept open from before the rule."""
    exported = rendering.snapshot_cv(cv, backend=Drawing())
    client.force_login(user)

    response = client.post(reverse("documents:rendered_archive", args=[exported.pk]), follow=True)

    page = response.content.decode()
    assert "Kept here only" in page
    assert "already has this document" not in page, "which would have been untrue"
    assert not copies_of(exported).exists()


def test_the_page_offers_no_button_for_an_export_and_says_why(client, user, cv, application, store):
    exported = rendering.snapshot_cv(cv, backend=Drawing())
    sent = rendering.snapshot_cv(cv, application=application, backend=Drawing())
    client.force_login(user)

    page = client.get(reverse("documents:rendered_list")).content.decode()

    assert reverse("documents:rendered_archive", args=[sent.pk]) in page
    assert reverse("documents:rendered_archive", args=[exported.pk]) not in page
    assert "Kept here only: a store is sent what went with an application." in page


def test_nobody_without_a_store_is_told_about_stores(client, user, cv):
    rendering.snapshot_cv(cv, backend=Drawing())
    client.force_login(user)

    assert "Kept here only" not in client.get(reverse("documents:rendered_list")).content.decode()


def test_a_report_still_goes_to_a_store(user, store):
    """It never goes with an application -- it is handed to an employment office -- and
    #162 decided a store is given it. Asking it for one would have switched that off."""
    report = rendering.snapshot_report(
        user,
        title="Job search report",
        html="<html>a report</html>",
        filename="report.pdf",
        backend=Drawing(),
    )

    assert report.kind == DocumentKind.REPORT and report.application is None
    assert report.goes_to_stores
    assert copies_of(report).count() == 1


def test_deleting_the_application_does_not_turn_what_was_sent_into_an_export(
    client, user, cv, application, store
):
    """`application` is cleared when the application goes (#217); `sent_to` is what is left
    saying where the PDF went, and it is what the rule reads."""
    sent = rendering.snapshot_cv(cv, application=application, backend=Drawing())
    application.delete()
    sent.refresh_from_db()

    assert sent.application is None and sent.sent_to == "Research Engineer at Black Mesa"
    assert sent.went_with_an_application and sent.goes_to_stores
    # And an export made afterwards is not mistaken for it.
    assert rendering.snapshot_cv(cv, backend=Drawing()).pk != sent.pk

    client.force_login(user)
    page = client.get(reverse("documents:rendered_list")).content.decode()
    assert "Research Engineer at Black Mesa" in page, "the list says where it went"


def test_an_upload_is_always_offered(user, store):
    from postulo.documents.models import UploadedDocument

    upload = UploadedDocument(owner=user, title="Diploma", kind=DocumentKind.CERTIFICATE)
    upload.file.save("diploma.pdf", ContentFile(b"%PDF-1.7 diploma"), save=False)
    upload.save()

    assert upload.goes_to_stores and copies_of(upload).count() == 1


# --------------------------------------------- what was already waiting, at the upgrade


def withdraw():
    migration = importlib.import_module(
        "postulo.documents.migrations.0011_snapshot_plain_text_and_local_exports"
    )
    migration.withdraw_what_was_waiting(installed_apps, None)


def a_copy(document, connection, status):
    return DocumentCopy.objects.create(
        owner=document.owner,
        connection=connection,
        store=connection.plugin,
        label=connection.label,
        document=document,
        status=status,
    )


def test_what_was_waiting_to_go_is_withdrawn_and_what_went_is_left(user, cv, application, store):
    """From now on, and not what a store already holds."""
    others = [
        Connection.objects.create(
            owner=user, kind="store", plugin=Shelf.name, label=f"Shelf {number}", config={}
        )
        for number in (2, 3, 4)
    ]
    exported = rendering.snapshot_cv(cv, backend=Drawing())
    waiting = a_copy(exported, store, CopyStatus.PENDING)
    failed = a_copy(exported, others[0], CopyStatus.FAILED)
    held = a_copy(exported, others[1], CopyStatus.SENT)
    declined = a_copy(exported, others[2], CopyStatus.DECLINED)

    sent = rendering.snapshot_cv(cv, application=application, backend=Drawing())
    report = rendering.snapshot_report(
        user, title="Report", html="<html>r</html>", filename="r.pdf", backend=Drawing()
    )
    untouched = {copy.pk for copy in (*copies_of(sent), *copies_of(report))}
    assert len(untouched) == 8, "four stores each, for the two that do go"

    withdraw()

    left = set(DocumentCopy.objects.values_list("pk", flat=True))
    assert waiting.pk not in left and failed.pk not in left
    assert held.pk in left, "a copy a store holds is a record of what happened"
    assert declined.pk in left
    assert untouched <= left


def test_a_pdf_whose_application_was_deleted_keeps_what_was_waiting(cv, application, store):
    sent = rendering.snapshot_cv(cv, application=application, backend=Drawing())
    application.delete()

    withdraw()

    assert copies_of(sent).count() == 1
