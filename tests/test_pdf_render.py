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
import re
from ctypes.util import find_library

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.documents import paper, rendering, themes
from postulo.documents import pdf as pdf_options
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


#: Every shipped theme, every kind it sets, the report included, on every paper it says it
#: prints on (#344): for Postulo's own, all four.
ON_PAPER = [
    (theme.name, kind, one.key)
    for theme in themes.BUILT_IN
    for kind in themes.Kind.values
    if theme.sets(kind)
    for one in paper.declared(theme, kind)
]

#: A line with nowhere to wrap, which is what finds a column measured for one paper only.
LONG_ADDRESS = "https://example.org/" + "a" * 180


def a_wide_list(user, theme, chosen) -> str:
    """The companies table in every column it has, one company with a long address (#341)."""
    from django.test import RequestFactory

    from postulo.core import list_export, tables
    from postulo.jobs.models import Company
    from postulo.jobs.tables import CompaniesTable

    Company.objects.create(owner=user, name="Aperture Science", website=LONG_ADDRESS)
    keys = [column.key for column in CompaniesTable.columns]
    tables.save_settings(user, "companies", {"columns": keys})
    request = RequestFactory().get("/")
    request.user = user
    html, _theme, _language = list_export.draw(request, "companies", theme=theme, paper=chosen)
    return html


@pytest.mark.parametrize("theme,kind,chosen", ON_PAPER)
def test_every_kind_draws_on_every_paper_at_its_size(user, experience, theme, kind, chosen):
    """The page is the paper chosen, turned the way chosen, and nothing runs off it (#344)."""
    from postulo.applications import reports

    experience.summary = f"Kept the services up. See {LONG_ADDRESS}"
    experience.save()
    if kind == themes.Kind.LETTER:
        letter = CoverLetter.objects.create(
            owner=user, name="General", subject="Hello", body=f"See {LONG_ADDRESS}", theme=theme
        )
        html = rendering.render_letter_html(letter, paper=chosen)
    elif kind == themes.Kind.REPORT:
        _report, html, _theme, _language = reports.draw(
            user, reports.period_from({}), theme=theme, paper=chosen
        )
    elif kind == themes.Kind.LIST:
        html = a_wide_list(user, theme, chosen)
    else:
        cv = selection(user, experience, kind=kind, theme=theme)
        html = rendering.render_cv_html(cv, paper=chosen)

    wanted = paper.find(chosen)
    pages = WeasyPrintBackend().document(html).render().pages
    assert pages, "nothing was drawn"
    for page in pages:
        # WeasyPrint measures in CSS pixels, 96 to the inch.
        assert page.width == pytest.approx(wanted.width_mm / 25.4 * 96, abs=1)
        assert page.height == pytest.approx(wanted.height_mm / 25.4 * 96, abs=1)
        assert not overflowing(page), f"something is drawn past the edge of {chosen}"


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


def test_a_cv_with_its_contact_block_off_is_titled_without_its_holder(user, experience):
    """The title is the PDF's `/Title`, so the name must not lead it when the CV is anonymous."""
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    cv = selection(user, experience, show_contact_details=False)

    content = readable(rendering.render_cv_html(cv))

    assert b"/Title" in content
    assert b"Morgan" not in content
    assert b"Morgan" not in rendering.snapshot_cv(cv).file.name.encode()


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


# ------------------------------------------- Urdu in Nastaliq, Arabic in Naskh (#1054)

#: The fonts a PDF embeds, by the name each is embedded under, without the subset's tag.
BASE_FONT = re.compile(rb"/BaseFont\s*/(?:[A-Z]{6}\+)?([^\s/\[\]<>()]+)")

#: Two lines of a CV in each language, in the script each is written in.
URDU = (
    "سافٹ ویئر انجینئر",
    "میں نے سرور اور ڈیٹا بیس کی دیکھ بھال کی اور تین انجینئروں کو سکھایا۔",
)
ARABIC = ("مهندس برمجيات", "حافظت على الخوادم وقواعد البيانات ودرّبت ثلاثة مهندسين.")


def embedded(content: bytes) -> set[str]:
    return {name.decode("latin-1") for name in BASE_FONT.findall(content)}


def needs_family(family: str, language_tag: str, sample: str) -> None:
    """Skip where this machine has not got the family: CI's test job installs DejaVu alone,
    and the image, which carries `fonts-noto-core`, is where these say something."""
    from postulo.documents import fonts

    if not fonts.family_draws(family, language_tag, sample):
        pytest.skip(f"{family} is not installed here")


def written_in(user, experience, language: str, lines: tuple[str, str], theme: str) -> bytes:
    headline, summary = lines
    experience.summary = summary
    experience.save()
    cv = selection(user, experience, language=language, theme=theme)
    cv.headline = headline
    cv.save()
    return readable(rendering.render_cv_html(cv))


@pytest.mark.parametrize("theme", [name for name, kind in SETTINGS if kind == themes.Kind.CV])
def test_an_urdu_cv_is_set_in_nastaliq(user, experience, theme):
    needs_family("Noto Nastaliq Urdu", "urd", "ں")

    fonts = embedded(written_in(user, experience, "ur", URDU, theme))

    assert any("Nastaliq" in name for name in fonts), fonts


@pytest.mark.parametrize("theme", [name for name, kind in SETTINGS if kind == themes.Kind.CV])
def test_an_arabic_cv_is_still_set_in_naskh(user, experience, theme):
    needs_family("Noto Naskh Arabic", "ara", "ب")

    fonts = embedded(written_in(user, experience, "ar", ARABIC, theme))

    assert any("Naskh" in name for name in fonts), fonts
    assert not any("Nastaliq" in name for name in fonts), "Nastaliq is Urdu's, not Arabic's"


# ------------------------------------- where a line breaks without spaces (#1052)

#: What may not start a line in Chinese or Japanese: the closing marks of the kinsoku rules.
CLOSING = "，。、；：？！）」』】〉》〕”’"

#: ... and what may not end one: the opening marks.
OPENING = "（「『【〈《〔“‘"

#: A Chinese paragraph with a closing or opening mark every few characters, long enough to
#: take several lines of an A4 page: a break that lands beside a mark has to be moved.
CHINESE = (
    "我们维护了服务器和数据库，部署时间从四十分钟缩短到四分钟。"
    "（另外）我们还指导了三名工程师，「效果」很好！"
) * 12

#: Single words of Thai, none of them a compound a dictionary could split, run together as
#: Thai is written: "I love the cat and the dog of his in the house".
THAI_WORDS = ("ผม", "รัก", "แมว", "และ", "หมา", "ของ", "เขา", "ใน", "บ้าน")
THAI = "".join(THAI_WORDS) * 25


def laid_out_lines(html: str) -> list[str]:
    """The text of every line WeasyPrint laid out, in order, page after page.

    The line boxes are what Pango broke the paragraphs into, so this is the text layer as a
    reader's selection would find it, one line at a time.
    """
    from weasyprint.formatting_structure import boxes

    lines = []
    for page in WeasyPrintBackend().document(html).render().pages:
        for box in page._page_box.descendants():
            if isinstance(box, boxes.LineBox):
                texts = [text.text for text in box.descendants() if isinstance(text, boxes.TextBox)]
                lines.append("".join(texts))
    return lines


def needs_script(script: str) -> None:
    """Skip where this machine has no font for the script, the way the families above do."""
    from postulo.documents import fonts

    if not fonts.script_draws(script):
        pytest.skip(f"no font draws {script} here")


@pytest.mark.parametrize("theme", [name for name, kind in SETTINGS if kind == themes.Kind.CV])
def test_a_chinese_cv_never_starts_a_line_with_a_closing_mark(user, experience, theme):
    """Pango breaks Han between almost any two characters under UAX #14, which keeps the
    kinsoku marks off the wrong end of a line; the themes' `overflow-wrap: anywhere` breaks
    only a word too long for its line, which nothing in a Chinese paragraph is."""
    needs_script("Hans")
    experience.summary = CHINESE
    experience.save()
    cv = selection(user, experience, language="zh-Hans", theme=theme)

    lines = [line.strip() for line in laid_out_lines(rendering.render_cv_html(cv))]
    chinese = [line for line in lines if line and line[0] in CHINESE]

    assert len(chinese) > 3, "the paragraph was meant to take several lines"
    assert not [line for line in lines if line and line[0] in CLOSING], lines
    assert not [line for line in lines if line and line[-1] in OPENING], lines


@pytest.mark.parametrize("theme", [name for name, kind in SETTINGS if kind == themes.Kind.CV])
def test_a_thai_cv_breaks_only_between_words(user, experience, theme):
    """Thai has no spaces, and Pango finds its breaks with libthai's dictionary, which
    `docker/Dockerfile` installs by name: every line of the paragraph ends where a word does.
    Two words a dictionary knows as one may be kept together; a word is never cut."""
    needs_script("Thai")
    experience.summary = THAI
    experience.save()
    cv = selection(user, experience, language="th", theme=theme)

    lines = [line.strip() for line in laid_out_lines(rendering.render_cv_html(cv))]
    thai = [line for line in lines if line and line[0] in THAI]
    assert len(thai) > 3, "the paragraph was meant to take several lines"
    assert "".join(thai) == THAI, "the paragraph's lines are the whole of it, in order"

    word_ends, offset = set(), 0
    for word in THAI_WORDS * 25:
        offset += len(word)
        word_ends.add(offset)
    line_ends, offset = set(), 0
    for line in thai:
        offset += len(line)
        line_ends.add(offset)
    assert line_ends <= word_ends, f"a line ends inside a word: {sorted(line_ends - word_ends)}"
