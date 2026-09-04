"""CVs, cover letters, uploads and snapshots.

PDF rendering is exercised through a stand-in backend. Spawning a browser in every test
would be slow here and impossible in CI, where no renderer is installed; one test does
run a real backend when one happens to be available.
"""

import datetime

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.documents.models import CV, CoverLetter, CVItem, RenderedDocument, UploadedDocument
from postulo.documents.pdf import (
    ChromiumBackend,
    PDFBackendUnavailable,
    WeasyPrintBackend,
    get_pdf_backend,
)
from postulo.documents.rendering import (
    build_sections,
    fill_placeholders,
    render_cv_html,
    render_letter_html,
    snapshot_cv,
    snapshot_letter,
)
from postulo.jobs.models import Company, JobPosting
from postulo.resume.models import Experience, LanguageSkill, SkillGroup, split_highlights


class FakeBackend:
    """A stand-in renderer that records what it was asked to draw."""

    name = "fake"

    def __init__(self) -> None:
        self.rendered: list[str] = []

    def is_available(self) -> bool:
        return True

    def render(self, html: str) -> bytes:
        self.rendered.append(html)
        return b"%PDF-1.7 fake"


@pytest.fixture
def fake_backend():
    return FakeBackend()


@pytest.fixture
def experience(db, user):
    return Experience.objects.create(
        owner=user,
        organisation="Aperture Science",
        role="Senior Engineer",
        start_date=datetime.date(2021, 3, 1),
        highlights="Cut deploy time from 40 minutes to 4.\nMentored three engineers.",
    )


@pytest.fixture
def cv(db, user, experience):
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
def letter(db, user):
    return CoverLetter.objects.create(
        owner=user,
        name="General",
        subject="Application for {{ role }} at {{ company }}",
        body="Dear {{ company }},\n\nI am writing about {{ role }}.\n\n{{ name }}",
    )


@pytest.fixture
def application(db, user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Research Engineer", location="Paris"
    )
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


# ------------------------------------------------------------------- highlights


@pytest.mark.parametrize(
    "text,expected",
    [
        ("One\nTwo", ["One", "Two"]),
        ("  Padded  \n\n\n  Also padded ", ["Padded", "Also padded"]),
        ("", []),
        (None, []),
    ],
)
def test_highlights_split_one_per_line(text, expected):
    assert split_highlights(text) == expected


# ------------------------------------------------------------- CV as a selection


def test_a_cv_selects_from_the_master_record_rather_than_copying_it(cv, experience):
    experience.role = "Principal Engineer"
    experience.save()

    assert "Principal Engineer" in render_cv_html(cv), "a CV must follow the master record"


def test_an_entry_can_be_tailored_for_one_cv_without_touching_the_original(cv, experience, user):
    item = cv.items.get()
    item.override_highlights = "Rewritten for this particular employer."
    item.save()

    html = render_cv_html(cv)
    experience.refresh_from_db()

    assert "Rewritten for this particular employer." in html
    assert "Cut deploy time" not in html
    assert "Cut deploy time" in experience.highlights, "the master copy is untouched"


def test_an_entry_can_be_left_off_a_variant(cv):
    item = cv.items.get()
    item.is_included = False
    item.save()

    assert "Senior Engineer" not in render_cv_html(cv)


def test_sections_follow_the_order_the_owner_chose(db, user, cv, experience):
    """Moving one entry to the top moves its whole section, which is what dragging implies."""
    language = LanguageSkill.objects.create(owner=user, name="French", proficiency="c1")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(LanguageSkill),
        object_id=language.pk,
        order=0,
    )
    # Filtering on object_id alone would match both rows: with a generic relation two
    # different models can share a primary key, so the content type is always needed too.
    cv.items.filter(
        content_type=ContentType.objects.get_for_model(Experience), object_id=experience.pk
    ).update(order=1)

    assert [section.kind for section in build_sections(cv)] == ["languageskill", "experience"]


def test_a_skill_group_renders_its_skills(db, user, cv):
    group = SkillGroup.objects.create(owner=user, name="Languages")
    group.skills.create(owner=user, name="Python")
    group.skills.create(owner=user, name="Go")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(SkillGroup),
        object_id=group.pk,
        order=5,
    )

    html = render_cv_html(cv)

    assert "Python, Go" in html, "skills keep the order they were typed in"


def test_contact_details_can_be_left_off(cv, user):
    cv.show_contact_details = False
    cv.save()

    assert user.email not in render_cv_html(cv)


# ------------------------------------------------------------------ placeholders


def test_placeholders_are_filled_from_the_application(letter, application):
    html = render_letter_html(letter, application)

    assert "Black Mesa" in html
    assert "Research Engineer" in html


def test_placeholders_tolerate_spacing(letter):
    assert fill_placeholders("{{company}} and {{  company  }}", {"company": "Acme"}) == (
        "Acme and Acme"
    )


def test_an_unknown_placeholder_is_left_visible(letter):
    """Blanking it would silently delete a word; leaving it shows the typo in the draft."""
    assert fill_placeholders("Dear {{ compnay }},", {"company": "Acme"}) == "Dear {{ compnay }},"


def test_letter_text_is_not_run_through_the_template_engine(db, user, application):
    """A letter often contains text pasted from a job advert.

    Handing that to Django's template engine would let ``{% ... %}`` in the source reach
    into the application. Substitution is a regular expression over known names, so tags
    can only ever come out as the literal text somebody typed.
    """
    hostile = CoverLetter.objects.create(
        owner=user,
        name="Pasted from an advert",
        body="{% load i18n %}[{{ 6|add:6 }}]{% for x in 'q' %}{{ x }}{% endfor %}",
    )

    html = render_letter_html(hostile, application)

    assert "[12]" not in html, "an expression must not be evaluated"
    assert "add:6" in html, "it survives as the literal text somebody typed"


# --------------------------------------------------------------------- snapshots


def test_a_snapshot_keeps_the_document_as_it_was_sent(cv, experience, fake_backend):
    document = snapshot_cv(cv, backend=fake_backend)
    original_text = document.source_text
    original_checksum = document.checksum

    experience.role = "Completely Different Title"
    experience.save()
    cv.name = "Renamed"
    cv.save()
    document.refresh_from_db()

    assert document.source_text == original_text
    assert document.checksum == original_checksum
    assert "Senior Engineer" in document.source_text
    assert "Completely Different Title" not in document.source_text


def test_a_snapshot_records_the_application_it_went_with(cv, application, fake_backend):
    document = snapshot_cv(cv, application=application, backend=fake_backend)

    assert document.application == application
    assert document in application.rendered_documents.all()


def test_a_letter_snapshot_stores_the_resolved_text(letter, application, fake_backend):
    document = snapshot_letter(letter, application=application, backend=fake_backend)

    assert "Black Mesa" in document.source_text
    assert "{{ company }}" not in document.source_text, "placeholders are resolved, not stored raw"


def test_two_snapshots_of_the_same_cv_both_survive(cv, application, fake_backend):
    first = snapshot_cv(cv, application=application, backend=fake_backend)
    second = snapshot_cv(cv, application=application, backend=fake_backend)

    assert RenderedDocument.objects.for_user(cv.owner).count() == 2
    assert first.pk != second.pk


def test_sending_documents_records_it_on_the_timeline(
    client, user, cv, letter, application, fake_backend, monkeypatch
):
    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", lambda name=None: fake_backend)
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cv": cv.pk, "cover_letter": letter.pk}
    )

    assert response.status_code == 302
    assert application.rendered_documents.count() == 2
    assert application.events.filter(summary="Documents sent").exists()


def test_a_missing_pdf_backend_is_explained_rather_than_crashing(
    client, user, cv, application, monkeypatch
):
    """Postulo is usable without a renderer, so this is a message, not a stack trace."""
    from postulo.documents.pdf import PDFBackendUnavailable

    def unavailable(name=None):
        raise PDFBackendUnavailable("No PDF backend is installed")

    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", unavailable)
    client.force_login(user)

    response = client.post(reverse("documents:send", args=[application.pk]), {"cv": cv.pk})

    assert response.status_code == 200
    assert application.rendered_documents.count() == 0
    assert any("No PDF backend" in str(m) for m in response.context["messages"])


# --------------------------------------------------------------- backend choice


def test_an_unknown_backend_name_is_refused(settings):
    settings.POSTULO_PDF_BACKEND = "laserprinter"

    with pytest.raises(PDFBackendUnavailable, match="Unknown PDF backend"):
        get_pdf_backend()


def test_a_named_but_unusable_backend_says_what_it_needs(settings, monkeypatch):
    settings.POSTULO_PDF_BACKEND = "weasyprint"
    monkeypatch.setattr(WeasyPrintBackend, "is_available", lambda self: False)

    with pytest.raises(PDFBackendUnavailable, match="libpango"):
        get_pdf_backend()


def test_with_nothing_usable_the_message_explains_both_options(settings, monkeypatch):
    settings.POSTULO_PDF_BACKEND = "auto"
    monkeypatch.setattr(WeasyPrintBackend, "is_available", lambda self: False)
    monkeypatch.setattr(ChromiumBackend, "is_available", lambda self: False)

    with pytest.raises(PDFBackendUnavailable, match="No PDF backend is usable"):
        get_pdf_backend()


def test_weasyprint_is_preferred_when_both_work(settings, monkeypatch):
    """It is the default: smaller output, better paged CSS, no browser to launch."""
    settings.POSTULO_PDF_BACKEND = "auto"
    monkeypatch.setattr(WeasyPrintBackend, "is_available", lambda self: True)
    monkeypatch.setattr(ChromiumBackend, "is_available", lambda self: True)

    assert get_pdf_backend().name == "weasyprint"


def test_chromium_takes_over_when_weasyprint_cannot_run(settings, monkeypatch):
    settings.POSTULO_PDF_BACKEND = "auto"
    monkeypatch.setattr(WeasyPrintBackend, "is_available", lambda self: False)
    monkeypatch.setattr(ChromiumBackend, "is_available", lambda self: True)

    assert get_pdf_backend().name == "chromium"


def test_an_installed_but_unimportable_package_counts_as_unusable(monkeypatch):
    """The trap WeasyPrint sets on a machine without Pango.

    The package is installed and importable as far as the module finder is concerned,
    and then raises OSError when the linker cannot find its libraries. Detecting it by
    presence rather than by importing it would make `auto` choose a backend that fails
    at render time instead of falling back to one that works.
    """
    from postulo.documents import pdf

    pdf._is_importable.cache_clear()
    monkeypatch.setattr(
        pdf.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(OSError("cannot load library 'libgobject-2.0-0'")),
    )

    try:
        assert pdf._is_importable("weasyprint") is False
    finally:
        pdf._is_importable.cache_clear()


def test_a_real_backend_produces_a_real_pdf(cv, settings):
    """Runs only where a renderer happens to be installed; CI has none."""
    settings.POSTULO_PDF_BACKEND = "auto"
    try:
        backend = get_pdf_backend()
    except PDFBackendUnavailable:
        pytest.skip("no PDF backend installed")

    document = snapshot_cv(cv, backend=backend)

    document.file.open("rb")
    try:
        assert document.file.read(5) == b"%PDF-"
        assert document.file.size > 1000
    finally:
        document.file.close()


# ----------------------------------------------------------------------- uploads


def test_a_new_version_numbers_itself_from_the_one_it_supersedes(db, user):
    from postulo.documents.forms import UploadedDocumentForm

    first = UploadedDocument.objects.create(
        owner=user,
        title="Designed CV",
        file=SimpleUploadedFile("cv.pdf", b"%PDF-1.7 one"),
    )

    form = UploadedDocumentForm(
        data={"title": "Designed CV", "kind": "cv", "replaces": first.pk, "notes": ""},
        files={"file": SimpleUploadedFile("cv-v2.pdf", b"%PDF-1.7 two")},
        user=user,
    )
    assert form.is_valid(), form.errors
    second = form.save(commit=False)
    second.owner = user
    second.save()

    assert second.version == 2
    assert not first.is_current, "the old version is superseded"
    assert second.is_current


def test_an_oversized_upload_is_refused(db, user):
    from postulo.documents.forms import MAX_UPLOAD_BYTES, UploadedDocumentForm

    too_big = SimpleUploadedFile("huge.pdf", b"x" * (MAX_UPLOAD_BYTES + 1))
    form = UploadedDocumentForm(
        data={"title": "Huge", "kind": "cv", "notes": ""}, files={"file": too_big}, user=user
    )

    assert not form.is_valid()
    assert "file" in form.errors


def test_an_uploaded_file_is_delivered_only_to_its_owner(client, user, other_user, db):
    document = UploadedDocument.objects.create(
        owner=user, title="Private CV", file=SimpleUploadedFile("cv.pdf", b"%PDF-1.7 private")
    )

    client.force_login(other_user)
    assert client.get(reverse("documents:upload_download", args=[document.pk])).status_code == 404

    client.force_login(user)
    response = client.get(reverse("documents:upload_download", args=[document.pk]))
    try:
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]
        assert response["Content-Disposition"].startswith("attachment;")
    finally:
        # Django closes a streaming response when it finishes serving it; the test
        # client does not, and a file left to the garbage collector shows up as an
        # unraisable exception.
        response.close()


def test_a_snapshot_is_delivered_only_to_its_owner(client, user, other_user, cv, fake_backend):
    document = snapshot_cv(cv, backend=fake_backend)

    client.force_login(other_user)
    assert client.get(reverse("documents:rendered_download", args=[document.pk])).status_code == 404
