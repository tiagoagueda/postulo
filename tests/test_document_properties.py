"""A file's properties are the person's to see, leave out or edit, and OpenDocument is one more
format (#480).

Held here: that an `.odt` is a package a reader will open -- the media type first and stored,
a manifest naming every part, well-formed parts, headings as headings and a list as a list, in
the document's language; that an export without its properties carries no author and says no
more than a neutral title and a language; that a title, subject and keywords typed for a
document reach the PDF's page, the Word file and the OpenDocument file and nowhere else; that
what is typed is escaped and bounded; that the choice is recorded with what was sent; and that
a plugin reaches the registry through the plugin surface.
"""

from __future__ import annotations

import datetime
import io
import zipfile

import pytest
from defusedxml import ElementTree
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.documents import docx, formats, odt, properties, rendering
from postulo.documents.models import CV, CoverLetter, RenderedDocument
from postulo.documents.properties import Properties
from postulo.documents.slow import freeze
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
DC = "{http://purl.org/dc/elements/1.1/}"
META = "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}"
MANIFEST = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"

TYPED = {
    "title": "Backend engineer, Alex",
    "author": "A. Morgan",
    "subject": "Application for the platform team",
    "keywords": "python, services; reliability",
    "language": "fr",
}


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.rendered: list[str] = []

    def is_available(self) -> bool:
        return True

    def render(self, html: str) -> bytes:
        self.rendered.append(html)
        return b"%PDF-1.7 fake" + str(len(self.rendered)).encode()


@pytest.fixture
def person(user):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])
    return user


@pytest.fixture
def cv(person):
    return CV.objects.create(
        owner=person,
        name="Backend EN",
        headline="Backend engineer",
        summary="Ten years of keeping services up.\nMostly in Python.",
        language="en-GB",
    )


@pytest.fixture
def letter(person):
    return CoverLetter.objects.create(
        owner=person,
        name="General",
        subject="Hello {{ company }}",
        body="Dear team,\n\nI would like to join {{ company }}.\n\nKind regards",
        language="en-GB",
    )


@pytest.fixture
def application(person):
    company = Company.objects.create(owner=person, name="Aperture")
    posting = JobPosting.objects.create(owner=person, company=company, title="Engineer")
    return Application.objects.create(owner=person, posting=posting, status=Status.APPLIED)


def opened(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        assert package.testzip() is None
        return {name: package.read(name) for name in package.namelist()}


def meta_of(content: bytes):
    return ElementTree.fromstring(opened(content)["meta.xml"]).find(f"{OFFICE}meta")


# --------------------------------------------------------------- the OpenDocument file


def test_the_package_is_one_a_reader_opens(cv):
    content = odt.write(rendering.cv_outline(cv))

    with zipfile.ZipFile(io.BytesIO(content)) as package:
        first = package.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert package.read("mimetype") == b"application/vnd.oasis.opendocument.text"
    parts = opened(content)
    for name, part in parts.items():
        if name != "mimetype":
            ElementTree.fromstring(part)
    listed = {
        entry.get(f"{MANIFEST}full-path")
        for entry in ElementTree.fromstring(parts["META-INF/manifest.xml"])
    }
    assert listed >= {"/", "content.xml", "styles.xml", "meta.xml"}


def test_headings_are_headings_and_a_list_is_a_list(cv, person):
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CVItem
    from postulo.resume.models import Experience

    entry = Experience.objects.create(
        owner=person,
        organisation="Aperture Science",
        role="Senior Engineer",
        start_date=datetime.date(2021, 3, 1),
        highlights="Cut deploy time.\nMentored three engineers.",
    )
    CVItem.objects.create(
        owner=person,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=entry.pk,
    )

    root = ElementTree.fromstring(opened(odt.write(rendering.cv_outline(cv)))["content.xml"])
    body = root.find(f"{OFFICE}body/{OFFICE}text")

    headings = [
        (h.get(f"{TEXT}outline-level"), "".join(h.itertext())) for h in body.iter(f"{TEXT}h")
    ]
    assert headings[0] == ("1", "Alex Morgan")
    assert ("2", "Experience") in headings
    assert any(level == "3" and "Senior Engineer" in text for level, text in headings)
    items = ["".join(item.itertext()) for item in body.iter(f"{TEXT}list-item")]
    assert items == ["Cut deploy time.", "Mentored three engineers."]


def test_it_is_in_the_documents_language(cv):
    cv.language = "pt-PT"
    cv.save(update_fields=["language"])

    parts = opened(odt.write(rendering.cv_outline(cv)))

    assert meta_of(odt.write(rendering.cv_outline(cv))).find(f"{DC}language").text == "pt-PT"
    assert b'fo:language="pt"' in parts["styles.xml"] and b'fo:country="PT"' in parts["styles.xml"]


def test_an_arabic_one_is_set_from_the_right(cv):
    cv.language = "ar"
    cv.save(update_fields=["language"])

    styles = opened(odt.write(rendering.cv_outline(cv)))["styles.xml"]

    assert b'style:writing-mode="rl-tb"' in styles and b'style:language-complex="ar"' in styles


def test_the_same_cv_is_the_same_file_and_names_no_date_or_program(cv):
    one, two = odt.write(rendering.cv_outline(cv)), odt.write(rendering.cv_outline(cv))

    assert one == two
    meta = opened(one)["meta.xml"].decode()
    assert "creation-date" not in meta and "generator" not in meta and "date" not in meta


def test_what_somebody_typed_cannot_break_the_file(cv):
    chosen = Properties.from_data(
        {"title": "A <b>&amp; \x0b title", "keywords": "one,<two>", "subject": "a & b"}
    )

    content = odt.write(rendering.cv_outline(cv, chosen))

    meta = meta_of(content)
    assert meta.find(f"{DC}title").text == "A <b>&amp; title"
    assert [k.text for k in meta.findall(f"{META}keyword")] == ["one", "<two>"]


def test_a_line_break_and_a_run_of_spaces_are_kept(cv):
    cv.summary = "First line\nsecond   line"
    cv.save(update_fields=["summary"])

    content = opened(odt.write(rendering.cv_outline(cv)))["content.xml"].decode()

    assert "<text:line-break/>" in content and '<text:s text:c="3"/>' in content


# ------------------------------------------------------------ with and without properties


def test_by_default_a_file_carries_what_it_always_has(cv):
    outline = rendering.cv_outline(cv)

    assert outline.author == "Alex Morgan" and outline.title == "Alex Morgan — CV"
    assert outline.subject == "" and outline.keywords == ""
    meta = meta_of(odt.write(outline))
    assert meta.find(f"{DC}creator").text == "Alex Morgan"
    assert meta.find(f"{DC}title").text == "Alex Morgan — CV"
    assert meta.find(f"{META}keyword") is None


def test_without_its_properties_a_file_has_no_author_and_a_neutral_title(cv):
    chosen = Properties(include=False, **{k: v for k, v in TYPED.items() if k != "language"})

    outline = rendering.cv_outline(cv, chosen)

    meta = meta_of(odt.write(outline))
    assert b"Morgan" not in opened(odt.write(outline))["meta.xml"]
    assert b"Morgan" not in opened(docx.write(outline))["docProps/core.xml"]
    assert meta.find(f"{DC}creator") is None and meta.find(f"{DC}subject") is None
    assert meta.find(f"{META}keyword") is None
    assert meta.find(f"{DC}title").text == "CV", "a title stays: a screen reader announces it"
    assert meta.find(f"{DC}language").text == "en-GB", "and so does the language"
    core = opened(docx.write(outline))["docProps/core.xml"].decode()
    assert "creator" not in core and "keywords" not in core and "<dc:title>CV</dc:title>" in core


def test_what_was_typed_for_a_file_without_properties_is_ignored(cv):
    chosen = Properties.from_data({"with_properties": "0", **TYPED})

    outline = rendering.cv_outline(cv, chosen)

    assert not chosen.include
    assert outline.author == "" and outline.subject == "" and outline.title == "CV"


def test_the_name_is_not_in_the_downloads_name_either(client, person, cv):
    client.force_login(person)

    response = client.post(
        reverse("documents:cv_download", args=[cv.pk, "odt"]), {"with_properties": "0"}
    )

    assert "Morgan" not in response["Content-Disposition"]
    assert "CV.odt" in response["Content-Disposition"]


@pytest.mark.parametrize(
    ("raw", "include"),
    [(None, True), ("", True), ("1", True), ("on", True), ("0", False), ("false", False)],
)
def test_the_default_is_with(raw, include):
    data = {} if raw is None else {"with_properties": raw}

    assert Properties.from_data(data).include is include


def test_typed_properties_are_bounded_and_a_bad_language_is_dropped():
    chosen = Properties.from_data(
        {"title": "x" * 5000, "keywords": "k" * 5000, "language": "not a tag!"}
    )

    assert len(chosen.title) == properties.MAX_TITLE
    assert len(chosen.keywords) == properties.MAX_KEYWORDS
    assert chosen.language == ""


# ----------------------------------------------------- edited, in each format and nowhere else


def test_edited_properties_reach_each_format(cv):
    chosen = Properties.from_data(TYPED)
    outline = rendering.cv_outline(cv, chosen)

    meta = meta_of(odt.write(outline))
    assert meta.find(f"{DC}title").text == TYPED["title"]
    assert meta.find(f"{DC}creator").text == TYPED["author"]
    assert meta.find(f"{DC}subject").text == TYPED["subject"]
    assert [k.text for k in meta.findall(f"{META}keyword")] == ["python", "services", "reliability"]
    assert meta.find(f"{DC}language").text == "fr"

    core = opened(docx.write(outline))["docProps/core.xml"].decode()
    assert f"<dc:title>{TYPED['title']}</dc:title>" in core
    assert f"<dc:creator>{TYPED['author']}</dc:creator>" in core
    assert f"<dc:subject>{TYPED['subject']}</dc:subject>" in core
    assert f"<cp:keywords>{TYPED['keywords']}</cp:keywords>" in core
    assert "<dc:language>fr</dc:language>" in core

    html = rendering.render_cv_html(cv, properties=chosen)
    assert f"<title>{TYPED['title']}</title>" in html
    assert f'<meta name="author" content="{TYPED["author"]}">' in html
    assert f'<meta name="description" content="{TYPED["subject"]}">' in html
    assert f'<meta name="keywords" content="{TYPED["keywords"]}">' in html
    assert '<html lang="fr"' in html


def test_and_they_are_in_no_other_part_of_the_file(cv):
    outline = rendering.cv_outline(cv, Properties.from_data(TYPED))

    for written in (odt.write(outline), docx.write(outline)):
        for name, part in opened(written).items():
            if name in ("meta.xml", "docProps/core.xml", "mimetype"):
                continue
            assert TYPED["subject"].encode() not in part, name
            assert b"reliability" not in part, name


def test_the_text_format_has_none_and_is_not_asked_for_them(cv):
    outline = rendering.cv_outline(cv, Properties.from_data(TYPED))

    assert "reliability" not in formats.write_text(outline).decode()
    assert formats.get("txt").has_properties is False
    assert formats.get("odt").has_properties and formats.get("docx").has_properties


def test_without_them_the_page_carries_no_author_subject_or_keywords(cv):
    html = rendering.render_cv_html(cv, properties=Properties(include=False, **TYPED))

    assert '<meta name="author"' not in html and '<meta name="description"' not in html
    assert '<meta name="keywords"' not in html
    assert "<title>CV</title>" in html and 'lang="en-GB"' in html


def test_by_default_the_page_is_what_it_was(cv):
    html = rendering.render_cv_html(cv)

    assert "<title>Alex Morgan — CV</title>" in html
    assert '<meta name="author" content="Alex Morgan">' in html
    assert 'name="description"' not in html and 'name="keywords"' not in html


# ------------------------------------------------------------------------- a letter, as a file


def test_a_letter_downloads_as_odt_filled_in_from_the_application(
    client, person, letter, application
):
    client.force_login(person)

    response = client.get(
        reverse("documents:letter_download", args=[letter.pk, "odt"]),
        {"application": application.pk},
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/vnd.oasis.opendocument.text"
    root = ElementTree.fromstring(opened(response.content)["content.xml"])
    texts = [
        "".join(node.itertext()) for node in root.iter() if node.tag in (f"{TEXT}p", f"{TEXT}h")
    ]
    assert "Hello Aperture" in texts and "I would like to join Aperture." in texts
    assert "Dear team," in texts and "Kind regards" in texts
    heading = next(root.iter(f"{TEXT}h"))
    assert heading.get(f"{TEXT}outline-level") == "1"
    assert not RenderedDocument.objects.exists()


def test_a_letter_without_its_properties_names_nobody(client, person, letter):
    client.force_login(person)

    response = client.post(
        reverse("documents:letter_download", args=[letter.pk, "docx"]), {"with_properties": "0"}
    )

    core = opened(response.content)["docProps/core.xml"].decode()
    assert "creator" not in core and "Morgan" not in core


def test_somebody_elses_letter_is_not_found_and_nor_is_an_unknown_format(
    client, other_user, person, letter
):
    client.force_login(other_user)
    assert (
        client.get(reverse("documents:letter_download", args=[letter.pk, "odt"])).status_code == 404
    )
    client.force_login(person)
    assert (
        client.get(reverse("documents:letter_download", args=[letter.pk, "rtf"])).status_code == 404
    )


def test_both_pages_say_what_the_file_will_say_and_what_is_kept(client, person, cv, letter):
    client.force_login(person)

    for document in (cv, letter):
        page = client.get(document.get_absolute_url()).content.decode()
        assert 'id="file-properties"' in page
        assert 'name="title"' in page and 'value="Alex Morgan — ' in page
        assert 'name="keywords"' in page and "applicant tracking" in page
        assert "It keeps its language" in page, "what is kept is said where the choice is made"


def test_the_pdf_buttons_take_the_same_choice_as_the_formats(client, person, cv):
    client.force_login(person)

    page = client.get(cv.get_absolute_url()).content.decode()

    for name in ("cv_export", "cv_draft"):
        assert f'formaction="{reverse(f"documents:{name}", args=[cv.pk])}"' in page


def test_the_draft_is_given_the_choice(client, person, cv, monkeypatch):
    client.force_login(person)
    asked = []

    def draft(html, **kwargs):
        asked.append(html)
        return b"%PDF-1.7 draft"

    monkeypatch.setattr("postulo.documents.views.renderers.draft_pdf", draft)

    client.post(reverse("documents:cv_draft", args=[cv.pk]), {"with_properties": "0"})

    assert '<meta name="author"' not in asked[0] and "<title>CV</title>" in asked[0]


def test_the_export_errand_is_given_the_choice(client, person, cv, monkeypatch):
    client.force_login(person)
    seen = {}

    def snapshot(cv, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr("postulo.documents.rendering.snapshot_cv", snapshot)

    client.post(
        reverse("documents:cv_export", args=[cv.pk]), {"with_properties": "0", "subject": "s"}
    )

    assert seen["properties"].include is False


# ------------------------------------------------------------------- what was sent records it


def test_a_snapshot_records_whether_it_went_with_its_properties(cv, letter, application):
    backend = FakeBackend()

    with_them = rendering.snapshot_cv(cv, application=application, backend=backend)
    without = rendering.snapshot_cv(
        cv, application=application, backend=backend, properties=Properties(include=False)
    )
    letter_without = rendering.snapshot_letter(
        letter, application=application, backend=backend, properties=Properties(include=False)
    )

    assert with_them.with_properties is True
    assert without.with_properties is False and letter_without.with_properties is False
    assert "Morgan" not in without.title and "morgan" not in without.file.name
    assert "Alex Morgan" in with_them.title
    assert backend.rendered[1] != backend.rendered[0], "a different file is a different render"


def test_an_export_without_properties_is_not_the_twin_of_one_with(cv):
    backend = FakeBackend()

    one = rendering.snapshot_cv(cv, backend=backend)
    two = rendering.snapshot_cv(cv, backend=backend, properties=Properties(include=False))

    assert one.pk != two.pk and not getattr(two, "already_filed", False)


def test_the_stored_copy_is_the_one_that_was_sent(cv, letter, application, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", lambda name=None: backend)

    freeze(
        application,
        cv=cv,
        letter=letter,
        uploads=[],
        links=[],
        properties=Properties(include=False),
    )

    assert list(RenderedDocument.objects.values_list("with_properties", flat=True)) == [
        False,
        False,
    ]
    assert all('<meta name="author"' not in html for html in backend.rendered)


def test_sending_records_the_choice_the_form_made(client, person, cv, application, monkeypatch):
    client.force_login(person)
    seen = {}

    def send(kind, owner, **payload):
        seen.update(payload)
        raise RuntimeError("stop here")

    monkeypatch.setattr("postulo.core.errands.send", send)

    with pytest.raises(RuntimeError):
        client.post(
            reverse("documents:send", args=[application.pk]),
            {"cv": cv.pk},
        )

    assert seen["properties"]["with_properties"] == "0", "an unticked box is without"


def test_the_choice_travels_in_the_archive(cv, application, user):
    from postulo.core.export import build_document

    rendering.snapshot_cv(
        cv, application=application, backend=FakeBackend(), properties=Properties(include=False)
    )

    sent = build_document(user)["documents"]["sent"]

    assert sent[0]["with_properties"] is False


# ----------------------------------------------------------------- the plugin surface


def test_a_plugin_registers_a_format_through_the_surface(client, person, cv):
    from postulo.plugins import api

    assert api.DocumentFormat is formats.Format
    assert api.register_document_format is formats.register
    assert api.Outline is formats.Outline
    assert {"DocumentFormat", "register_document_format", "Outline"} <= set(api.__all__)

    seen = []

    def write(outline):
        seen.append(outline)
        return outline.title.encode()

    assert api.register_document_format(
        api.DocumentFormat(
            key="title",
            label="Title",
            extension="title",
            content_type="text/plain",
            write=write,
            has_properties=True,
        )
    )
    try:
        client.force_login(person)
        response = client.post(
            reverse("documents:cv_download", args=[cv.pk, "title"]), {"with_properties": "0"}
        )
        assert response.content == b"CV", "it is handed an outline the choice is applied to"
    finally:
        formats.forget("title")


def test_the_sent_list_says_which_copy_went_without_its_properties(client, person, cv):
    client.force_login(person)
    rendering.snapshot_cv(cv, backend=FakeBackend(), properties=Properties(include=False))

    page = client.get(reverse("documents:rendered_list")).content.decode()

    assert "Written without its properties" in page
