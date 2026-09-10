"""Every document Postulo writes, drawn by the renderer it ships with (#163).

Everywhere else the suite renders PDFs through a stand-in (`FakeBackend` in
`tests/test_documents.py`), which tests which renderer is asked and never what it draws. That
is right for everything else, and it meant that upgrading WeasyPrint -- a new major version
every release, the last one reworking layout, text, tables and fonts -- was covered by no test
at all. These draw the real thing: each kind of document in each theme Postulo ships, the
report, and a document set right to left, and check that each comes back a PDF with pages.

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

from postulo.documents import rendering, themes
from postulo.documents.models import CV, CoverLetter, CVItem
from postulo.documents.pdf import WeasyPrintBackend
from postulo.resume.models import Experience

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
