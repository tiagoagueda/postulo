"""Every document Postulo writes, drawn by the renderer it ships with (#163).

Everywhere else the suite renders PDFs through a stand-in (`FakeBackend` in
`tests/test_documents.py`), which tests which renderer is asked and never what it draws. That
is right for everything else, and it meant that upgrading WeasyPrint -- a new major version
every release, the last one reworking layout, text, tables and fonts -- was covered by no test
at all. These draw the real thing: each kind of document in each theme Postulo ships, the
report, and a document set right to left, and check that each comes back a PDF with pages.

Since #235 they also read what came back. A tag tree, the language and the file's properties
are names in the bytes; whether a long address stayed on the paper is a measurement of the
laid-out page, because "runs off the page" is about where the text ended up and not about
what the stylesheet says.

They need Pango and skip where it is missing, which is mostly Windows. CI's test job installs
it, and that is where they run.

They skip on Pango itself rather than on whether WeasyPrint imports. The suite treats a
warning as an error, WeasyPrint 70 warns at import when HarfBuzz-Subset is missing, and
asking it whether it imports would have skipped these in silence on the very machine meant to
run them -- which is what happened the first time they ran on Linux.
"""

from __future__ import annotations

import datetime
from ctypes.util import find_library

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.documents import pdf as pdf_options
from postulo.documents import rendering, themes
from postulo.documents.models import CV, CoverLetter, CVItem
from postulo.documents.pdf import WeasyPrintBackend
from postulo.resume.models import Experience, Link

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        find_library("pango-1.0") is None,
        reason="Pango is not installed here, and WeasyPrint draws with it",
    ),
]

#: Every shipped theme with every kind it sets, which for Postulo's own is all of them.
SETTINGS = [
    (theme.name, kind)
    for theme in themes.BUILT_IN
    for kind in (themes.Kind.CV, themes.Kind.PORTFOLIO, themes.Kind.LETTER)
    if theme.sets(kind)
]


@pytest.fixture
def experience(user):
    return Experience.objects.create(
        owner=user,
        organisation="Aperture Science",
        role="Senior Engineer",
        location="Lisbon",
        start_date=datetime.date(2021, 3, 1),
        summary="Kept the services up.",
        highlights="Cut deploy time from 40 minutes to 4.\nMentored three engineers.",
    )


def selection(user, experience, **fields) -> CV:
    cv = CV.objects.create(owner=user, name="Backend", headline="Backend engineer", **fields)
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
        order=0,
    )
    return cv


def drawn(html: str) -> bytes:
    """The PDF the backend writes, once the document it writes has been seen to have pages."""
    backend = WeasyPrintBackend()
    assert backend.document(html).render().pages, "nothing was drawn"
    pdf = backend.render(html)
    assert pdf.startswith(b"%PDF-")
    return pdf


def readable(html: str) -> bytes:
    """The same document, written with its dictionaries in plain sight.

    `render` compresses, and pydyf puts every dictionary it can -- the catalogue and the
    document information among them -- inside an object stream, so the names below are not
    in the bytes it produces. This asks for exactly what `render` asks for and adds
    `uncompressed_pdf`, which changes how the objects are stored and nothing about what they
    say. What `render` itself passes is asserted in `tests/test_documents.py`, which runs
    where WeasyPrint does not (#235).
    """
    return (
        WeasyPrintBackend()
        .document(html)
        .write_pdf(**pdf_options.WEASYPRINT_PDF_OPTIONS, uncompressed_pdf=True)
    )


def overflowing(page) -> list:
    """Everything laid out past the edge of the page's printable area.

    Measured rather than asserted about the stylesheet, because what "runs off the page"
    means is where the text ended up. A box whose right edge is beyond the margin is text a
    reader never sees and `pdftotext` hands back in pieces (#235).
    """
    box = page._page_box
    edge = box.content_box_x() + box.width
    return [
        child
        for child in box.descendants()
        if isinstance(getattr(child, "position_x", None), int | float)
        and isinstance(getattr(child, "width", None), int | float)
        and child is not box
        and child.position_x + child.width > edge + 1
    ]


@pytest.mark.parametrize("theme,kind", SETTINGS)
def test_every_kind_draws_in_every_theme(user, experience, theme, kind):
    if kind == themes.Kind.LETTER:
        letter = CoverLetter.objects.create(
            owner=user,
            name="General",
            subject="Application for the role",
            body="Dear Aperture,\n\nI am writing about the role.\n\nAlex",
            theme=theme,
        )
        html = rendering.render_letter_html(letter)
    else:
        html = rendering.render_cv_html(selection(user, experience, kind=kind, theme=theme))

    drawn(html)


def test_a_document_set_right_to_left_draws(user, experience):
    drawn(rendering.render_cv_html(selection(user, experience, language="ar")))


def test_the_report_draws(client, user, settings):
    settings.POSTULO_PDF_BACKEND = "weasyprint"
    client.force_login(user)

    response = client.get(reverse("applications:report_pdf"))

    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_the_shipped_themes_are_all_here():
    """So that a theme added later is drawn here without anybody remembering to add it."""
    assert {name for name, _kind in SETTINGS} == {theme.name for theme in themes.BUILT_IN}


# ------------------------------------------------- what is actually in the file (#235)


def test_a_cv_comes_back_with_a_tag_tree(user, experience):
    """Without one a PDF is a picture of a document: nothing to navigate, nothing to read back.

    `/StructTreeRoot` is the tag tree itself, `/Lang` is the declaration #67 added to the
    markup and had no way of reaching a reader until now, and `/Title` and `/Author` are the
    file's properties, which WeasyPrint takes out of the document.
    """
    content = readable(rendering.render_cv_html(selection(user, experience)))

    assert b"/StructTreeRoot" in content
    assert b"/Lang" in content
    assert b"/Title" in content
    assert b"/Author" in content


def test_the_language_the_document_declares_is_the_one_in_the_file(user, experience):
    """A CV written in Arabic says so to whatever reads it aloud, and not merely in the HTML."""
    content = readable(rendering.render_cv_html(selection(user, experience, language="ar")))

    assert b"/Lang (ar)" in content, "the /Lang is the document's language, not the interface's"


def test_a_letter_comes_back_with_a_tag_tree(user):
    letter = CoverLetter.objects.create(
        owner=user,
        name="General",
        subject="Application for the role",
        body="Dear Aperture,\n\nI am writing about the role.\n\nAlex",
    )

    content = readable(rendering.render_letter_html(letter))

    assert b"/StructTreeRoot" in content
    assert b"/Title" in content


def test_a_cv_with_its_contact_block_off_names_nobody_in_the_file(user, experience):
    """A CV deliberately left anonymous must not carry its author in the file's properties."""
    cv = selection(user, experience, show_contact_details=False)

    assert b"/Author" not in readable(rendering.render_cv_html(cv))


@pytest.mark.parametrize("theme,kind", SETTINGS)
def test_a_very_long_address_stays_on_the_page(user, theme, kind):
    """A 200-character link has nowhere to wrap, and used to run off the edge of the page."""
    address = "https://example.org/" + "a" * 180
    if kind == themes.Kind.LETTER:
        letter = CoverLetter.objects.create(
            owner=user, name="General", subject=address, body=f"See {address}", theme=theme
        )
        html = rendering.render_letter_html(letter)
    else:
        link = Link.objects.create(owner=user, title="Portfolio", url=address)
        cv = CV.objects.create(owner=user, name="Backend", kind=kind, theme=theme)
        CVItem.objects.create(
            owner=user,
            cv=cv,
            content_type=ContentType.objects.get_for_model(Link),
            object_id=link.pk,
            order=0,
        )
        html = rendering.render_cv_html(cv)

    for page in WeasyPrintBackend().document(html).render().pages:
        assert not overflowing(page), "something is drawn past the edge of the page"
