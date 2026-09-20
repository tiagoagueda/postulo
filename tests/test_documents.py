"""CVs, cover letters, uploads and snapshots.

PDF rendering is exercised through a stand-in backend. Spawning a browser in every test
would be slow here and impossible in CI, where no renderer is installed; one test does
run a real backend when one happens to be available.
"""

import datetime
import re

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

    # Freezing is sent off now, so the explanation arrives on the page that was watching
    # it rather than in a message on the form. Same sentence, same refusal (#247).
    response = client.post(reverse("documents:send", args=[application.pk]), {"cv": cv.pk})

    assert response.status_code == 302
    assert application.rendered_documents.count() == 0
    page = client.get(response.url).content.decode()
    assert "That did not work." in page and "No PDF backend" in page


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


def test_the_edit_form_names_the_file_rather_than_where_it_is_kept(client, user, db):
    """Django's file widget shows the *storage* path -- the account id and the month of the
    upload -- inside a link to /media/, which nothing serves. The person is shown the name of
    the file they uploaded, and nothing about where Postulo keeps it (#191)."""
    import posixpath

    document = UploadedDocument.objects.create(
        owner=user, title="Reference", file=SimpleUploadedFile("reference.txt", b"a reference")
    )
    stored = document.file.name
    assert stored.startswith(f"documents/{user.pk}/"), "the path carries the account id"

    client.force_login(user)
    html = client.get(reverse("documents:upload_update", args=[document.pk])).content.decode()

    assert posixpath.basename(stored) in html
    assert stored not in html, "the storage path is Postulo's, not the person's"
    assert f"documents/{user.pk}/" not in html
    assert "/media/" not in html, "nothing serves /media/, so a link there is a dead one"


def test_an_upload_downloads_as_what_it_is(client, user, db):
    """Every download used to be called `<title>.pdf`, whatever was uploaded, so a `.txt` or
    a `.docx` arrived as a file no PDF viewer would open -- with the content type to match,
    since that is guessed from the name (#193)."""
    document = UploadedDocument.objects.create(
        owner=user, title="Reference", file=SimpleUploadedFile("reference.txt", b"a reference")
    )
    assert document.download_name == "Reference.txt"

    client.force_login(user)
    response = client.get(reverse("documents:upload_download", args=[document.pk]))
    try:
        assert response.status_code == 200
        assert 'filename="Reference.txt"' in response["Content-Disposition"]
        assert response["Content-Type"].startswith("text/plain")
    finally:
        response.close()


def test_a_snapshot_downloads_as_a_pdf(cv, fake_backend):
    """A render is always the PDF Postulo drew, whatever its source was called."""
    document = snapshot_cv(cv, backend=fake_backend)
    assert document.download_name == f"{document.title}.pdf"


def test_a_cv_page_names_its_kind_in_a_whole_sentence(client, user, db):
    """The heading used to lowercase the kind's label into a slot -- "What is on this cv" --
    which flattened an acronym here and misspelt a noun in German. Two sentences now, one
    per kind, so nothing is re-cased and nothing has to agree with a slot (#168)."""
    from postulo.documents.models import CVKind

    cv = CV.objects.create(owner=user, name="Backend", kind=CVKind.CV)
    portfolio = CV.objects.create(owner=user, name="Work", kind=CVKind.PORTFOLIO)
    client.force_login(user)

    assert "What is on this CV" in client.get(cv.get_absolute_url()).content.decode()
    page = client.get(portfolio.get_absolute_url()).content.decode()
    assert "What is on this portfolio" in page
    assert "this cv" not in page and "this Cv" not in page


def test_a_snapshot_is_delivered_only_to_its_owner(client, user, other_user, cv, fake_backend):
    document = snapshot_cv(cv, backend=fake_backend)

    client.force_login(other_user)
    assert client.get(reverse("documents:rendered_download", args=[document.pk])).status_code == 404


# ------------------------------------------------- what the renderers are asked for (#235)


class RecordingDocument:
    """A WeasyPrint document that draws nothing and remembers what it was asked for."""

    def __init__(self, asked: dict) -> None:
        self.asked = asked

    def write_pdf(self, **options):
        self.asked.update(options)
        return b"%PDF-1.7 recorded"


def test_weasyprint_is_asked_for_a_tagged_pdf(monkeypatch):
    """Asserted through a stand-in, because WeasyPrint needs Pango and Windows has none.

    `tests/test_pdf_render.py` checks that these options really do produce a tag tree, and
    runs only where Pango is installed. This checks that they are asked for at all, and runs
    everywhere -- including on the machine most of Postulo is written on.
    """
    asked: dict = {}
    monkeypatch.setattr(WeasyPrintBackend, "document", lambda self, html: RecordingDocument(asked))

    assert WeasyPrintBackend().render("<html lang='en'></html>") == b"%PDF-1.7 recorded"
    assert asked == {"pdf_variant": "pdf/ua-1"}


def test_the_variant_is_a_tagged_one_rather_than_an_archival_one():
    """PDF/A needs an output intent and embedded fonts, which a plugin's theme cannot promise."""
    from postulo.documents import pdf

    assert pdf.WEASYPRINT_PDF_OPTIONS["pdf_variant"].startswith("pdf/ua")


def test_neither_argument_that_built_its_own_fetcher_is_passed():
    """CVE-2026-55073, which #163 closed: `stylesheets` and `xmp_metadata` ignored the fetcher."""
    from postulo.documents import pdf

    assert not {"stylesheets", "xmp_metadata"} & set(pdf.WEASYPRINT_PDF_OPTIONS)


def fake_playwright(monkeypatch) -> dict:
    """Stand in for Playwright, and count what it was asked to start.

    Returns the record: what `page.pdf` was asked for, how many browsers were launched and
    how many of them were closed, and the same for pages. Counting the launches is the point
    of #220 -- starting Chromium is most of what rendering costs, and the bug was that a
    *Send* of two documents started it twice.
    """
    import playwright.sync_api

    record: dict = {"asked": {}, "launched": 0, "browsers_closed": 0, "pages": 0, "pages_closed": 0}

    class Page:
        def route(self, *args, **kwargs):
            pass

        def set_content(self, *args, **kwargs):
            pass

        def pdf(self, **options):
            record["asked"].update(options)
            return b"%PDF-1.7 chromium"

        def close(self):
            record["pages_closed"] += 1

    class Browser:
        def new_page(self):
            record["pages"] += 1
            return Page()

        def close(self):
            record["browsers_closed"] += 1

    class Chromium:
        def launch(self):
            record["launched"] += 1
            return Browser()

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", Playwright)
    return record


def test_chromium_is_asked_for_a_tag_tree_and_an_outline(monkeypatch):
    """The fallback has to produce the same document, and spells the request differently."""
    from postulo.documents import pdf

    record = fake_playwright(monkeypatch)

    assert pdf.ChromiumBackend().render("<html></html>") == b"%PDF-1.7 chromium"
    assert record["asked"]["tagged"] is True
    assert record["asked"]["outline"] is True


def test_a_run_of_documents_starts_one_chromium(monkeypatch):
    """#220: it was a browser per document, launched and torn down inside the request."""
    from postulo.documents import pdf

    record = fake_playwright(monkeypatch)

    with pdf.pdf_session(pdf.ChromiumBackend()) as backend:
        backend.render("<html>one</html>")
        backend.render("<html>two</html>")

    assert record["launched"] == 1, "two documents, one browser"
    assert record["pages"] == 2, "a page each, so neither holds the other's document"
    assert record["pages_closed"] == 2
    assert record["browsers_closed"] == 1, "and it is closed when the run ends"


def test_a_backend_asked_for_one_document_still_starts_and_stops_its_own(monkeypatch):
    """Nothing has to know about sessions to render: `render` on its own works as it did."""
    from postulo.documents import pdf

    record = fake_playwright(monkeypatch)

    pdf.ChromiumBackend().render("<html></html>")

    assert record["launched"] == 1
    assert record["browsers_closed"] == 1


def test_a_backend_written_without_a_session_is_still_a_renderer(fake_backend):
    """The interface is a protocol, and a plugin's renderer predates `session` (#220)."""
    from postulo.documents import pdf

    assert not hasattr(fake_backend, "session")
    with pdf.pdf_session(fake_backend) as backend:
        assert backend is fake_backend


# ---------------------------------------------- structure and metadata in the markup (#235)


def test_a_cv_names_its_author(cv, user):
    """WeasyPrint reads the author out of the document, so the markup is the only place."""
    html = render_cv_html(cv)

    assert '<meta name="author"' in html
    assert user.display_name in html


def test_a_cv_with_its_contact_block_off_names_nobody(cv):
    """Turning the contact block off did not mean "except in the file's properties"."""
    cv.show_contact_details = False
    cv.save()

    assert '<meta name="author"' not in render_cv_html(cv)


def test_an_entry_title_is_a_heading(cv):
    """A job title inside a `<p>` is nothing a screen reader or an ATS can navigate to."""
    html = render_cv_html(cv)

    assert "<h3" in html
    assert '<p class="role">' not in html


def test_a_letters_subject_is_a_heading(letter):
    html = render_letter_html(letter)

    assert "<h1" in html
    assert '<p class="subject">' not in html


def test_the_contact_details_carry_their_own_separator(cv, user):
    """A CSS `::after` is generated content, and generated content is not a PDF's text.

    Something reading the file back -- an applicant tracking system, `pdftotext`, a screen
    reader -- got the telephone number run into the email address with nothing between them,
    because the dot was drawn and never written.
    """
    user.profile.location = "Lisbon"
    user.profile.save(update_fields=["location"])

    html = render_cv_html(cv)

    assert f"<span>{user.email}</span>" in html
    assert "<span>Lisbon</span>" in html
    assert "·" in html, "a real character, in the markup"
    assert "::after" not in html


def test_every_theme_lets_a_long_word_break():
    """A 120-character address in a fixed column runs off the page and out of the file."""
    from pathlib import Path

    themes = sorted(Path("src/postulo/templates/documents/themes").glob("*/*.html"))
    assert themes, "the themes moved"
    for path in themes:
        assert "overflow-wrap: anywhere" in path.read_text(encoding="utf-8"), path


# --------------------------------------------------------- the arrows on a CV (#203, #235)


def order_of(cv) -> list[int]:
    return [item.pk for item in cv.items.order_by("order", "pk")]


@pytest.fixture
def three_on_a_cv(db, user, cv):
    """Two more entries beside the fixture's, all three sharing a number to begin with."""
    for name in ("French", "German"):
        language = LanguageSkill.objects.create(owner=user, name=name, proficiency="b2")
        CVItem.objects.create(
            owner=user,
            cv=cv,
            content_type=ContentType.objects.get_for_model(LanguageSkill),
            object_id=language.pk,
            order=0,
        )
    return cv


def test_moving_an_entry_down_passes_exactly_one_neighbour(client, user, three_on_a_cv):
    first, second, third = order_of(three_on_a_cv)
    client.force_login(user)

    client.post(reverse("documents:cv_item_move", args=[first, "down"]))

    assert order_of(three_on_a_cv) == [second, first, third]


def test_entries_sharing_a_number_still_move_visibly(client, user, three_on_a_cv):
    """Every entry here starts at 0. Nudging the number by one moved nothing a person saw."""
    first, second, third = order_of(three_on_a_cv)
    client.force_login(user)

    client.post(reverse("documents:cv_item_move", args=[third, "up"]))

    assert order_of(three_on_a_cv) == [first, third, second]
    assert sorted(item.order for item in three_on_a_cv.items.all()) == [0, 1, 2]


def test_up_at_the_top_and_down_at_the_bottom_change_nothing(client, user, three_on_a_cv):
    before = order_of(three_on_a_cv)
    client.force_login(user)

    client.post(reverse("documents:cv_item_move", args=[before[0], "up"]))
    client.post(reverse("documents:cv_item_move", args=[before[-1], "down"]))

    assert order_of(three_on_a_cv) == before


def test_the_arrows_are_greyed_out_at_either_end(client, user, three_on_a_cv):
    """The pair keeps its shape, so the one somebody reaches for is where it was (#203)."""
    client.force_login(user)

    page = client.get(three_on_a_cv.get_absolute_url()).content.decode()

    assert page.count("disabled") == 2, "one at each end of the list, and nowhere else"


def test_one_cvs_entries_are_not_anothers_neighbours(client, user, cv):
    """`ordering.siblings` would have answered with every CVItem this person owns."""
    second = CV.objects.create(owner=user, name="Frontend")
    language = LanguageSkill.objects.create(owner=user, name="French", proficiency="b2")
    elsewhere = CVItem.objects.create(
        owner=user,
        cv=second,
        content_type=ContentType.objects.get_for_model(LanguageSkill),
        object_id=language.pk,
        order=0,
    )
    only = cv.items.get()
    client.force_login(user)

    client.post(reverse("documents:cv_item_move", args=[only.pk, "down"]))

    elsewhere.refresh_from_db()
    only.refresh_from_db()
    assert only.order == 0, "it is alone on its own CV, so there is nowhere to go"
    assert elsewhere.order == 0, "and the other CV was not touched"


def test_an_entry_added_after_a_removal_lands_at_the_end(client, user, cv, experience):
    """`count + added` produced a number something on the CV already had."""
    from postulo.resume.models import Project

    kept = LanguageSkill.objects.create(owner=user, name="French", proficiency="b2")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(LanguageSkill),
        object_id=kept.pk,
        order=1,
    )
    cv.items.filter(content_type=ContentType.objects.get_for_model(Experience)).delete()
    project = Project.objects.create(owner=user, name="Something new")
    client.force_login(user)

    client.post(reverse("documents:cv_add_items", args=[cv.pk]), {"add_project": [str(project.pk)]})

    added = cv.items.get(content_type=ContentType.objects.get_for_model(Project))
    assert order_of(cv)[-1] == added.pk
    assert sorted(item.order for item in cv.items.all()) == [0, 1]


# ------------------------------------------------------------ the letter preview (#235)


def test_a_query_parameter_that_is_not_a_number_is_not_an_error(client, user, letter):
    """`?application=abc` reached `filter(pk=...)` and came back out as a 500."""
    client.force_login(user)

    response = client.get(
        reverse("documents:letter_preview", args=[letter.pk]), {"application": "abc"}
    )

    assert response.status_code == 200


def test_the_preview_reads_for_the_application_it_is_given(client, user, letter, application):
    client.force_login(user)

    page = client.get(
        reverse("documents:letter_preview", args=[letter.pk]), {"application": application.pk}
    ).content.decode()

    assert "Black Mesa" in page


def test_another_persons_application_fills_in_nothing(client, other_user, application, db):
    theirs = CoverLetter.objects.create(owner=other_user, name="Theirs", body="Dear {{ company }},")
    client.force_login(other_user)

    page = client.get(
        reverse("documents:letter_preview", args=[theirs.pk]), {"application": application.pk}
    ).content.decode()

    assert "Black Mesa" not in page


def test_a_placeholder_with_nothing_behind_it_is_marked_in_the_preview(client, user, letter):
    """ "Dear ," is a sentence with a word missing; a marker is a gap somebody can see."""
    client.force_login(user)

    page = client.get(reverse("documents:letter_preview", args=[letter.pk])).content.decode()

    assert "[ company ]" in page
    assert "Dear ," not in page


def test_a_marker_never_reaches_a_document_that_goes_out(letter, fake_backend):
    """A preview is for reading. What is frozen is the letter, gaps and all."""
    document = snapshot_letter(letter, backend=fake_backend)

    assert "[ company ]" not in document.source_text
    assert "[ company ]" not in fake_backend.rendered[0]


def test_the_letter_page_offers_an_application_to_read_it_against(
    client, user, letter, application
):
    """The preview was reachable only without an application -- the one version nobody sends."""
    client.force_login(user)

    page = client.get(letter.get_absolute_url()).content.decode()

    assert "Research Engineer" in page
    assert f'value="{application.pk}"' in page


def test_the_contact_placeholder_is_the_person_on_the_application(db, user, application):
    """The follow-up starter has asked for "[name]" since it was written."""
    from postulo.jobs.models import Contact

    application.contact = Contact.objects.create(
        owner=user, company=application.posting.company, name="Dr Kleiner"
    )
    application.save(update_fields=["contact"])
    letter = CoverLetter.objects.create(owner=user, name="Follow-up", body="Dear {{ contact }},")

    assert "Dear Dr Kleiner," in render_letter_html(letter, application)


def test_a_letter_with_a_gap_is_shown_before_it_is_frozen(client, user, letter, application):
    """Freezing showed nobody the text being frozen, so a gap was found in the PDF afterwards."""
    application.posting.location = ""
    application.posting.save(update_fields=["location"])
    letter.body = "Dear {{ company }}, about {{ role }} in {{ location }}."
    letter.save(update_fields=["body"])
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(letter.pk)}
    )
    page = response.content.decode()

    assert response.status_code == 200, "nothing was frozen yet"
    assert "[ location ]" in page
    assert not RenderedDocument.objects.filter(owner=user).exists()


def test_pressing_through_the_warning_freezes_it_gaps_and_all(
    client, user, letter, application, fake_backend, monkeypatch
):
    """The warning is a warning, not a refusal: a gap is sometimes what somebody means."""
    application.posting.location = ""
    application.posting.save(update_fields=["location"])
    letter.body = "Dear {{ company }}, about {{ role }} in {{ location }}."
    letter.save(update_fields=["body"])
    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", lambda name=None: fake_backend)
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]),
        {"cover_letter": str(letter.pk), "confirmed": "1"},
    )

    assert response.status_code == 302
    document = RenderedDocument.objects.get(owner=user)
    assert "[ location ]" not in document.source_text, "the marker is for reading, not for sending"


def test_a_letter_with_nothing_missing_is_frozen_without_an_extra_press(
    client, user, letter, application, fake_backend, monkeypatch
):
    """An extra step everybody has to press through is read once and clicked past for ever."""
    letter.subject = "About {{ role }}"
    letter.body = "Dear {{ company }}, I am writing about {{ role }}."
    letter.save(update_fields=["subject", "body"])
    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", lambda name=None: fake_backend)
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(letter.pk)}
    )

    assert response.status_code == 302
    assert RenderedDocument.objects.filter(owner=user).count() == 1


# ------------------------------------------- saying which language a file is in (#283)


@pytest.mark.django_db
def test_the_upload_form_asks_which_language_and_blank_means_not_said(client, user):
    """Blank is *nobody has said*, not "follow your profile": this is a file Postulo has
    never read, and the wording is the whole difference (#283)."""
    client.force_login(user)

    html = client.get(reverse("documents:upload_create")).content.decode()

    select = re.search(r'<select[^>]*name="language"[^>]*>(.*?)</select>', html, re.S)
    assert select, "no language picker on the upload form"
    assert ">Not said<" in select.group(1)
    assert "Follow your profile" not in select.group(1)


@pytest.mark.django_db
def test_an_upload_says_its_language_or_says_that_nobody_has(client, user):
    from django.core.files.base import ContentFile

    from postulo.documents.models import DocumentKind, UploadedDocument

    upload = UploadedDocument(owner=user, title="Diploma", kind=DocumentKind.CERTIFICATE)
    upload.file.save("diploma.pdf", ContentFile(b"%PDF-1.7 x"), save=True)
    client.force_login(user)

    html = client.get(reverse("documents:upload_list")).content.decode()
    assert "language not said" in html, "unsaid is the prompt to say"

    upload.language = "de"
    upload.save(update_fields=["language"])
    html = client.get(reverse("documents:upload_list")).content.decode()
    assert 'data-flag="de"' in html and "language not said" not in html
