"""A CV leaves as more than a PDF: plain text, and a Word file (#236).

Applicant tracking systems and public-sector portals ask for a `.docx` or for text pasted
into a box, and a CV that exists only as a PDF is retyped into each of them by hand.

Held here: that the text says what the page says and no more; that *Copy as plain text*
works with no script, as a page; that the Word file is one Word will open -- a zip, with
the parts a package has to have, each of them well-formed, carrying the CV's words as
headings, paragraphs and a list; and that a format is an entry in a registry, so the next
one is added rather than written in.
"""

from __future__ import annotations

import datetime
import io
import re
import zipfile

import pytest

# The parser the project already reads other people's XML with (#264), and the stricter
# one: it refuses a document type declaration and an entity outright, so a part that
# parses here is one that declares neither.
from defusedxml import ElementTree
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.documents import docx, formats, rendering
from postulo.documents.models import (
    CV,
    CVItem,
    CVKind,
    DocumentCopy,
    RenderedDocument,
    UploadedDocument,
)
from postulo.resume.models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Link,
    Project,
    SkillGroup,
)

pytestmark = pytest.mark.django_db

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def put_on(cv, entry, order: int = 0):
    return CVItem.objects.create(
        owner=cv.owner,
        cv=cv,
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
        order=order,
    )


@pytest.fixture
def person(user):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])
    user.profile.location = "Lisbon"
    user.profile.save(update_fields=["location"])
    return user


@pytest.fixture
def cv(person):
    """One of every kind of entry, in the order somebody might put them."""
    variant = CV.objects.create(
        owner=person,
        name="Backend EN",
        headline="Backend engineer",
        summary="Ten years of keeping services up.\nMostly in Python.",
        language="en-GB",
    )
    put_on(
        variant,
        Experience.objects.create(
            owner=person,
            organisation="Aperture Science",
            role="Senior Engineer",
            location="Cambridge",
            start_date=datetime.date(2021, 3, 1),
            summary="Kept the portal up.",
            highlights="Cut deploy time from 40 minutes to 4.\nMentored three engineers.",
        ),
        0,
    )
    put_on(
        variant,
        Education.objects.create(
            owner=person,
            institution="Universidade de Lisboa",
            qualification="BSc Computer Science",
            field_of_study="Distributed systems",
            grade="17/20",
            start_date=datetime.date(2010, 9, 1),
            end_date=datetime.date(2013, 7, 1),
        ),
        1,
    )
    put_on(
        variant,
        Project.objects.create(
            owner=person,
            name="Turret firmware",
            role="Maintainer",
            url="https://example.org/turrets",
            summary="Open-source firmware.",
            highlights="Adopted by three labs.",
        ),
        2,
    )
    group = SkillGroup.objects.create(owner=person, name="Languages")
    group.skills.create(owner=person, name="Python")
    group.skills.create(owner=person, name="Go")
    put_on(variant, group, 3)
    put_on(variant, LanguageSkill.objects.create(owner=person, name="French", proficiency="c1"), 4)
    put_on(variant, LanguageSkill.objects.create(owner=person, name="Basque", proficiency=""), 5)
    put_on(
        variant,
        Certification.objects.create(
            owner=person,
            name="Certified Kubernetes Administrator",
            issuer="CNCF",
            issued_on=datetime.date(2022, 5, 1),
        ),
        6,
    )
    put_on(
        variant,
        Link.objects.create(
            owner=person,
            title="Portfolio",
            url="https://alex.example.org",
            description="What I have built",
        ),
        7,
    )
    return variant


def parts_of(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        assert package.testzip() is None, "every entry reads back whole"
        return {name: package.read(name) for name in package.namelist()}


def paragraphs_of(document: bytes) -> list[tuple[str, str, bool]]:
    """Every paragraph of a `document.xml`: its style, its text, and whether it is in a list."""
    root = ElementTree.fromstring(document)
    found = []
    for paragraph in root.iter(f"{W}p"):
        style = paragraph.find(f"{W}pPr/{W}pStyle")
        text = "".join(node.text or "" for node in paragraph.iter(f"{W}t"))
        listed = paragraph.find(f"{W}pPr/{W}numPr") is not None
        found.append((style.get(f"{W}val") if style is not None else "", text, listed))
    return found


# ---------------------------------------------------------------------- the words


def test_the_text_says_what_the_page_says(cv):
    text = rendering.cv_text(cv)

    assert text.splitlines()[:3] == [
        "Alex Morgan",
        "Backend engineer",
        "applicant@example.org · Lisbon",
    ]
    for line in (
        "Ten years of keeping services up.",
        "Mostly in Python.",
        "Experience",
        "Senior Engineer",
        "Aperture Science · Cambridge",
        "March 2021 – present",
        "Kept the portal up.",
        "- Cut deploy time from 40 minutes to 4.",
        "- Mentored three engineers.",
        "BSc Computer Science",
        "Universidade de Lisboa",
        "September 2010 – July 2013",
        "Turret firmware",
        "Maintainer",
        "- Languages: Python, Go",
        "- French — C1 — advanced",
        "- Certified Kubernetes Administrator, CNCF · 2022",
        "- Portfolio — What I have built · https://alex.example.org",
    ):
        assert line in text.splitlines(), line


def test_the_text_says_no_more_than_the_page_does(cv):
    """It stands beside a PDF as the record of what that PDF claimed. The themes print
    neither a grade nor a field of study, so a text that did would record what nobody sent."""
    text = rendering.cv_text(cv)
    html = rendering.render_cv_html(cv)

    for left_out in ("17/20", "Distributed systems"):
        assert left_out not in html, "the page has changed; so must the text"
        assert left_out not in text


def test_every_line_of_the_text_is_on_the_page(cv):
    """The other direction, line by line, against the markup the PDF is drawn from."""
    html = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", rendering.render_cv_html(cv)))
    html = html.replace("&amp;", "&")

    for line in rendering.cv_text(cv).splitlines():
        for part in re.split(r" · | — |: |, ", line.removeprefix(formats.BULLET)):
            assert part.strip() in html, f"{part!r}, of {line!r}, is not on the page"


def test_a_level_nobody_stated_prints_nothing(cv):
    """As on the page: claiming one is the person's to do (#235)."""
    lines = rendering.cv_text(cv).splitlines()

    assert "- Basque" in lines
    assert not any("Not stated" in line for line in lines)


def test_the_parts_stand_apart_and_nothing_else_does(cv):
    text = rendering.cv_text(cv)

    assert "applicant@example.org · Lisbon\n\nTen years" in text, "the summary opens a new part"
    assert "\n\nExperience\n\nSenior Engineer\n" in text
    assert "\n\n\n" not in text, "one blank line is a break; two is a gap"
    assert not text.startswith("\n") and not text.endswith("\n")


def test_a_cv_with_its_contact_block_off_names_nobody(cv, person):
    cv.show_contact_details = False
    cv.save(update_fields=["show_contact_details"])

    outline = rendering.cv_outline(cv)
    text = formats.as_text(outline)

    assert "Alex Morgan" not in text and person.email not in text
    assert text.splitlines()[0] == "Backend engineer", "the headline is the heading then"
    assert outline.author == "", "and the file's properties do not name them either"


def test_an_entry_left_off_the_cv_is_left_off_the_text(cv):
    cv.items.filter(content_type=ContentType.objects.get_for_model(Project)).update(
        is_included=False
    )

    assert "Turret firmware" not in rendering.cv_text(cv)


def test_highlights_written_for_this_cv_are_the_ones_in_its_text(cv):
    item = cv.items.get(content_type=ContentType.objects.get_for_model(Experience))
    item.override_highlights = "Rewritten for this employer."
    item.save()

    text = rendering.cv_text(cv)

    assert "- Rewritten for this employer." in text
    assert "Mentored three engineers." not in text


def test_the_text_is_in_the_documents_language_not_the_readers(cv, person):
    """The months are Django's own, so this holds with no catalogue of Postulo's compiled."""
    from django.utils import translation

    from postulo.resume.models import Translation

    experience = Experience.objects.get(owner=person)
    Translation.objects.create(
        owner=person, entry=experience, language="fr-FR", field="role", text="Ingénieur principal"
    )
    cv.language = "fr-FR"
    cv.save(update_fields=["language"])

    with translation.override("en-GB"):
        outline = rendering.cv_outline(cv)
    text = formats.as_text(outline)

    assert outline.language == "fr-FR"
    assert "mars 2021" in text and "March 2021" not in text
    assert "Ingénieur principal" in text and "Senior Engineer" not in text
    assert "Aperture Science" in text, "an employer's name is nobody's to translate"


def test_a_portfolio_leads_with_the_work_and_so_does_its_text(cv):
    """A line an entry for the career, the years and no highlights: what the page sets."""
    cv.kind = CVKind.PORTFOLIO
    cv.save(update_fields=["kind"])

    lines = rendering.cv_text(cv).splitlines()

    assert "- Senior Engineer · Aperture Science · 2021 – present" in lines
    assert "- BSc Computer Science · Universidade de Lisboa · 2010–2013" in lines
    assert "- Cut deploy time from 40 minutes to 4." not in lines
    piece = lines.index("Turret firmware")
    assert lines[piece + 1 : piece + 5] == [
        "Maintainer",
        "Open-source firmware.",
        "- Adopted by three labs.",
        "https://example.org/turrets",
    ]


def test_an_empty_cv_has_no_text_and_does_not_fall_over(person):
    empty = CV.objects.create(owner=person, name="Empty", show_contact_details=False)

    assert rendering.cv_text(empty) == ""
    assert formats.write_text(rendering.cv_outline(empty)) == b"\n"
    assert zipfile.is_zipfile(io.BytesIO(docx.write(rendering.cv_outline(empty))))


# ---------------------------------------------------------------- the text file


def test_the_text_downloads_as_a_file_and_files_nothing(client, person, cv):
    client.force_login(person)

    response = client.get(reverse("documents:cv_download", args=[cv.pk, "txt"]))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    disposition = response["Content-Disposition"]
    assert disposition.startswith("attachment;") and ".txt" in disposition
    assert "Alex" in disposition and "Backend" not in disposition, "named as a snapshot is"
    assert "no-store" in response["Cache-Control"]
    body = response.content.decode("utf-8")
    assert body == rendering.cv_text(cv) + "\n"
    assert not response.content.startswith(b"\xef\xbb\xbf"), "no byte-order mark"
    assert not RenderedDocument.objects.exists() and not DocumentCopy.objects.exists()


def test_a_file_of_somebody_elses_cv_is_not_found(client, other_user, cv):
    client.force_login(other_user)

    for key in ("txt", "docx", "no-such-format"):
        response = client.get(reverse("documents:cv_download", args=[cv.pk, key]))
        assert response.status_code == 404, key


def test_a_format_nobody_registered_is_not_found(client, person, cv):
    client.force_login(person)

    assert client.get(reverse("documents:cv_download", args=[cv.pk, "rtf"])).status_code == 404


# ------------------------------------------------ copy as plain text, with no script


def test_copy_as_plain_text_is_a_page_with_the_text_in_a_box(client, person, cv):
    client.force_login(person)

    response = client.get(reverse("documents:cv_text", args=[cv.pk]))
    page = response.content.decode()

    assert response.status_code == 200
    box = re.search(r"<textarea([^>]*)>(.*?)</textarea>", page, re.S)
    assert box, "the text is in a box that can be selected from end to end"
    attributes, held = box.groups()
    assert " readonly" in attributes, "and cannot be typed into"
    assert 'id="cv-plain-text"' in attributes and '<label for="cv-plain-text">' in page
    assert 'lang="en-GB"' in attributes and 'dir="ltr"' in attributes
    assert "Senior Engineer" in held and "- Mentored three engineers." in held


def test_the_page_works_with_no_script_and_a_script_only_adds_to_it(client, person, cv):
    """The box is the feature. `app.js` puts a *Copy* button beside anything marked
    `data-copy-source` where there is a clipboard to write to (#276), and nothing here is
    hidden until it does."""
    client.force_login(person)

    page = client.get(reverse("documents:cv_text", args=[cv.pk])).content.decode()
    main = page[page.index("<main") : page.index("</main>")]

    assert "data-copy-source" in main and 'data-copy-label="Copy"' in main
    assert "<button" not in main, "the button is the script's to add, not the page's to need"
    assert "<form" not in main and "<script" not in main
    assert " hidden" not in main and "style=" not in main


def test_the_box_holds_what_the_file_holds(client, person, cv):
    from html import unescape

    client.force_login(person)

    page = client.get(reverse("documents:cv_text", args=[cv.pk])).content.decode()
    held = unescape(re.search(r"<textarea[^>]*>(.*?)</textarea>", page, re.S).group(1))

    assert held.strip() == rendering.cv_text(cv)


def test_what_somebody_typed_cannot_close_the_box(client, person, cv):
    cv.summary = "</textarea><script>alert(1)</script>"
    cv.save(update_fields=["summary"])
    client.force_login(person)

    page = client.get(reverse("documents:cv_text", args=[cv.pk])).content.decode()

    assert "<script>alert(1)</script>" not in page
    assert page.count("</textarea>") == 1


def test_an_arabic_cv_is_boxed_from_the_right(client, person, cv):
    cv.language = "ar"
    cv.save(update_fields=["language"])
    client.force_login(person)

    page = client.get(reverse("documents:cv_text", args=[cv.pk])).content.decode()

    assert re.search(r'<textarea[^>]*lang="ar" dir="rtl"', page)


def test_a_cv_with_nothing_on_it_says_so(client, person):
    empty = CV.objects.create(owner=person, name="Empty", show_contact_details=False)
    client.force_login(person)

    page = client.get(reverse("documents:cv_text", args=[empty.pk])).content.decode()

    assert "no text to copy" in page and "<textarea" not in page


def test_the_text_page_of_somebody_elses_cv_is_not_found(client, other_user, cv):
    client.force_login(other_user)

    assert client.get(reverse("documents:cv_text", args=[cv.pk])).status_code == 404


def test_the_cvs_page_offers_all_three(client, person, cv):
    client.force_login(person)

    page = client.get(cv.get_absolute_url()).content.decode()

    assert reverse("documents:cv_text", args=[cv.pk]) in page and "Copy as plain text" in page
    assert reverse("documents:cv_download", args=[cv.pk, "txt"]) in page
    assert reverse("documents:cv_download", args=[cv.pk, "docx"]) in page
    assert reverse("documents:cv_download", args=[cv.pk, "odt"]) in page
    assert "Download .txt" in page and "Download .docx" in page and "Download .odt" in page
    assert "Word document" in page, "an extension is not a name everybody knows"


# ------------------------------------------------------------------ the Word file


def test_the_word_file_opens_as_a_zip_with_the_parts_word_requires(cv):
    content = docx.write(rendering.cv_outline(cv))

    assert zipfile.is_zipfile(io.BytesIO(content))
    parts = parts_of(content)
    assert next(iter(parts)) == "[Content_Types].xml", "read first, so written first"
    assert {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/styles.xml",
        "word/numbering.xml",
        "word/_rels/document.xml.rels",
        "docProps/core.xml",
    } == set(parts)


def test_every_part_is_well_formed_xml(cv):
    for name, content in parts_of(docx.write(rendering.cv_outline(cv))).items():
        assert content.startswith(b'<?xml version="1.0" encoding="UTF-8"'), name
        ElementTree.fromstring(content)  # raises on anything that is not


def test_the_package_says_what_each_part_is_and_which_one_is_the_document(cv):
    parts = parts_of(docx.write(rendering.cv_outline(cv)))

    types = ElementTree.fromstring(parts["[Content_Types].xml"])
    declared = {
        override.get("PartName"): override.get("ContentType")
        for override in types.iter(f"{CONTENT_TYPES}Override")
    }
    assert declared["/word/document.xml"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    )
    # Every part is either named here or covered by its extension's default.
    defaults = {one.get("Extension") for one in types.iter(f"{CONTENT_TYPES}Default")}
    assert defaults == {"rels", "xml"}
    for name in parts:
        assert f"/{name}" in declared or name.rsplit(".", 1)[-1] in defaults, name
    for name in declared:
        assert name.lstrip("/") in parts, f"{name} is declared and is not in the package"

    package = ElementTree.fromstring(parts["_rels/.rels"])
    targets = {
        one.get("Type").rsplit("/", 1)[-1]: one.get("Target")
        for one in package.iter(f"{RELATIONSHIPS}Relationship")
    }
    assert targets["officeDocument"] == "word/document.xml"
    assert targets["core-properties"] == "docProps/core.xml"

    document = ElementTree.fromstring(parts["word/_rels/document.xml.rels"])
    beside = {
        one.get("Type").rsplit("/", 1)[-1]: one.get("Target")
        for one in document.iter(f"{RELATIONSHIPS}Relationship")
    }
    assert beside == {"styles": "styles.xml", "numbering": "numbering.xml"}
    ids = [one.get("Id") for one in document.iter(f"{RELATIONSHIPS}Relationship")]
    assert len(ids) == len(set(ids))


def test_the_word_file_carries_the_cvs_words(cv):
    parts = parts_of(docx.write(rendering.cv_outline(cv)))
    written = [text for _style, text, _listed in paragraphs_of(parts["word/document.xml"])]

    # Every line of the text, as a paragraph of the document: the same words, in the same
    # order. The summary's two lines are one paragraph with a break in it.
    expected = [
        line.removeprefix(formats.BULLET) for line in rendering.cv_text(cv).splitlines() if line
    ]
    joined = [part for text in written for part in ([text] if text else [])]
    assert "Ten years of keeping services up.Mostly in Python." in joined
    flattened = "\n".join(joined).replace(
        "Ten years of keeping services up.Mostly in Python.",
        "Ten years of keeping services up.\nMostly in Python.",
    )
    assert flattened.splitlines() == expected


def test_a_heading_is_a_heading_and_a_list_is_a_list(cv):
    """A bold paragraph is not a heading, and a dash is not a bullet: the navigation pane,
    a screen reader and an applicant tracking system find structure by what it is."""
    parts = parts_of(docx.write(rendering.cv_outline(cv)))
    paragraphs = paragraphs_of(parts["word/document.xml"])
    by_text = {text: (style, listed) for style, text, listed in paragraphs}

    assert by_text["Alex Morgan"] == ("Heading1", False)
    assert by_text["Experience"] == ("Heading2", False)
    assert by_text["Senior Engineer"] == ("Heading3", False)
    assert by_text["Aperture Science · Cambridge"] == ("", False)
    assert by_text["Mentored three engineers."] == ("ListParagraph", True)
    assert not any(text.startswith(formats.BULLET) for _s, text, _l in paragraphs)

    styles = ElementTree.fromstring(parts["word/styles.xml"])
    defined = {style.get(f"{W}styleId"): style for style in styles.iter(f"{W}style")}
    for used in {style for style, _text, _listed in paragraphs if style}:
        assert used in defined, f"{used} is used and never defined"
    for level in (1, 2, 3):
        heading = defined[f"Heading{level}"]
        assert heading.find(f"{W}name").get(f"{W}val") == f"heading {level}"
        assert heading.find(f"{W}pPr/{W}outlineLvl").get(f"{W}val") == str(level - 1)

    numbering = ElementTree.fromstring(parts["word/numbering.xml"])
    assert numbering.find(f"{W}abstractNum/{W}lvl/{W}numFmt").get(f"{W}val") == "bullet"
    listed = ElementTree.fromstring(parts["word/document.xml"]).find(f".//{W}numPr/{W}numId")
    assert listed.get(f"{W}val") == numbering.find(f"{W}num").get(f"{W}numId")


def test_a_line_break_inside_a_paragraph_is_a_break(cv):
    document = ElementTree.fromstring(
        parts_of(docx.write(rendering.cv_outline(cv)))["word/document.xml"]
    )

    summary = next(
        paragraph
        for paragraph in document.iter(f"{W}p")
        if "Ten years" in "".join(node.text or "" for node in paragraph.iter(f"{W}t"))
    )
    assert [node.tag for node in summary.find(f"{W}r")] == [f"{W}t", f"{W}br", f"{W}t"]


def test_the_file_says_whose_it_is_and_what_language_it_is_in(cv):
    cv.language = "pt-PT"
    cv.save(update_fields=["language"])
    parts = parts_of(docx.write(rendering.cv_outline(cv)))

    properties = parts["docProps/core.xml"].decode()
    assert "<dc:creator>Alex Morgan</dc:creator>" in properties
    assert "<dc:title>Alex Morgan — CV</dc:title>" in properties
    assert "<dc:language>pt-PT</dc:language>" in properties
    assert '<w:lang w:val="pt-PT"/>' in parts["word/styles.xml"].decode()


def test_a_name_left_off_the_page_is_not_put_back_as_the_files_author(cv):
    """The rule the PDF keeps (#235): no contact block, no author. The title is the PDF's
    own as well -- `document_title`, which names the holder whatever the page shows (#223)
    -- so the two files say the same thing about themselves rather than one more than the
    other."""
    cv.show_contact_details = False
    cv.save(update_fields=["show_contact_details"])

    properties = parts_of(docx.write(rendering.cv_outline(cv)))["docProps/core.xml"].decode()

    assert "dc:creator" not in properties
    assert '<meta name="author"' not in rendering.render_cv_html(cv)
    assert f"<dc:title>{rendering.document_title(cv)}</dc:title>" in properties


def test_an_arabic_cv_is_set_from_the_right(cv):
    cv.language = "ar"
    cv.save(update_fields=["language"])

    parts = parts_of(docx.write(rendering.cv_outline(cv)))
    document = ElementTree.fromstring(parts["word/document.xml"])

    paragraphs = list(document.iter(f"{W}p"))
    assert paragraphs and all(p.find(f"{W}pPr/{W}bidi") is not None for p in paragraphs)
    assert all(run.find(f"{W}rPr/{W}rtl") is not None for run in document.iter(f"{W}r"))
    assert document.find(f".//{W}sectPr/{W}bidi") is not None
    assert '<w:lang w:bidi="ar"/>' in parts["word/styles.xml"].decode()


def test_an_english_one_is_not(cv):
    document = parts_of(docx.write(rendering.cv_outline(cv)))["word/document.xml"].decode()

    assert "w:bidi" not in document and "w:rtl" not in document


def test_what_somebody_typed_cannot_break_the_file(cv, person):
    """XML forbids most control characters outright, escaped or not, and one vertical tab
    pasted in from a PDF would make a file Word reports as corrupt."""
    experience = Experience.objects.get(owner=person)
    experience.role = 'R&D <lead> "quoted" \x0b\x00\x1f￾ end\ttabbed'
    experience.summary = "]]> <w:p/> &amp; 日本語 🚀"
    experience.save()

    parts = parts_of(docx.write(rendering.cv_outline(cv)))
    for content in parts.values():
        ElementTree.fromstring(content)
    written = [text for _style, text, _listed in paragraphs_of(parts["word/document.xml"])]

    assert 'R&D <lead> "quoted"  end tabbed' in written
    assert "]]> <w:p/> &amp; 日本語 🚀" in written, "kept as written, and as text"


def test_the_same_cv_is_the_same_file_byte_for_byte(cv):
    """Nothing in the package says when it was made."""
    first = docx.write(rendering.cv_outline(cv))
    second = docx.write(rendering.cv_outline(cv))

    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as package:
        assert {entry.date_time for entry in package.infolist()} == {docx.EPOCH}


@pytest.mark.parametrize(
    "code,tag",
    [
        ("pt-pt", "pt-PT"),
        ("en-gb", "en-GB"),
        ("de", "de"),
        ("zh-hans", "zh-Hans"),
        ("sr_latn_rs", "sr-Latn-RS"),
        ("es-419", "es-419"),
        ("", ""),
    ],
)
def test_a_language_is_written_the_way_word_writes_one(code, tag):
    """Word is not always indifferent to case, which is why this export wrote the
    canonical form before anything else did. It is Postulo's one writer now (#337)."""
    from postulo.core import languages

    assert languages.tag(code) == tag


def test_the_word_file_downloads_as_one(client, person, cv):
    client.force_login(person)

    response = client.get(reverse("documents:cv_download", args=[cv.pk, "docx"]))

    assert response.status_code == 200
    assert response["Content-Type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert ".docx" in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    from postulo.core.files import FILE_POLICY

    assert response["Content-Security-Policy"] == FILE_POLICY, "as a private file carries"
    assert "word/document.xml" in parts_of(response.content)
    assert not RenderedDocument.objects.exists() and not DocumentCopy.objects.exists()


# ------------------------------------------------------------------ the registry


@pytest.fixture
def a_plugins_format():
    """A format as a plugin would bring one: registered, and forgotten afterwards."""

    def shout(outline: formats.Outline) -> bytes:
        return formats.as_text(outline).upper().encode("utf-8")

    offered = formats.Format(
        key="shout",
        label="Shouted text",
        extension="shout",
        content_type="text/x-shout; charset=utf-8",
        write=shout,
        provider="A plugin",
    )
    assert formats.register(offered)
    try:
        yield offered
    finally:
        formats.forget("shout")


def test_postulo_ships_plain_text_opendocument_and_word():
    assert [one.key for one in formats.all_formats()] == ["txt", "odt", "docx"]
    assert formats.get("docx").extension == "docx"


def test_another_format_is_added_and_not_written_in(client, person, cv, a_plugins_format):
    """The page and the address both read the registry, so neither was edited for this."""
    client.force_login(person)

    page = client.get(cv.get_absolute_url()).content.decode()
    address = reverse("documents:cv_download", args=[cv.pk, "shout"])
    assert address in page and "Shouted text" in page and "Download .shout" in page

    response = client.get(address)
    assert response["Content-Type"] == "text/x-shout; charset=utf-8"
    assert ".shout" in response["Content-Disposition"]
    assert b"SENIOR ENGINEER" in response.content


def test_a_key_already_taken_is_refused_rather_than_replaced(caplog):
    """A plugin able to replace `docx` could change what every Word file contains."""
    before = formats.get("docx")
    usurper = formats.Format(
        key="docx",
        label="Not Word",
        extension="docx",
        content_type="text/plain",
        write=lambda outline: b"",
    )

    assert formats.register(usurper) is False
    assert formats.get("docx") is before
    assert "offered twice" in caplog.text


@pytest.mark.parametrize("key,extension", [("", "x"), ("x", "")])
def test_a_format_with_no_key_or_no_extension_is_ignored(key, extension):
    nameless = formats.Format(
        key=key, label="?", extension=extension, content_type="text/plain", write=bytes
    )

    assert formats.register(nameless) is False
    assert [one.key for one in formats.all_formats()] == ["txt", "odt", "docx"]


def test_registering_postulos_own_twice_changes_nothing():
    formats.register_the_ones_postulo_has()

    assert [one.key for one in formats.all_formats()] == ["txt", "odt", "docx"]


def test_a_format_that_fails_is_a_sentence_and_the_others_still_work(client, person, cv, caplog):
    def broken(outline: formats.Outline) -> bytes:
        raise RuntimeError("the plugin's own bug")

    formats.register(
        formats.Format(
            key="broken",
            label="Broken",
            extension="brk",
            content_type="application/octet-stream",
            write=broken,
        )
    )
    client.force_login(person)
    try:
        response = client.get(reverse("documents:cv_download", args=[cv.pk, "broken"]), follow=True)
    finally:
        formats.forget("broken")

    assert response.redirect_chain[-1][0] == cv.get_absolute_url()
    page = response.content.decode()
    assert "That file could not be written." in page
    assert "the plugin's own bug" not in page, "the log has that; the page has a sentence"
    assert "the plugin's own bug" in caplog.text


# ----------------------------------------------------- the name a download goes out under


@pytest.mark.parametrize("title", ['CV "final"', "CV\r\nfor Acme"])
def test_a_title_with_a_quote_or_a_line_break_still_downloads(client, person, title):
    """The title is written into a header; a quote or a line break must not break it (#376)."""
    upload = UploadedDocument.objects.create(owner=person, title=title)
    upload.file.save("cv.pdf", io.BytesIO(b"%PDF-1.4 private"), save=True)
    client.force_login(person)

    response = client.get(reverse("documents:upload_download", args=[upload.pk]))

    assert response.status_code == 200
    disposition = response["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.startswith("attachment;")
    if '"' in title:
        assert 'filename="CV \\"final\\".pdf"' in disposition
    response.close()
