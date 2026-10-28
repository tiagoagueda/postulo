"""What every exported file says about itself, by default: Settings → Export (#878).

Held here: that each writer -- the PDF's page and the PDF WeasyPrint draws from it, the Word
file and the OpenDocument file -- carries each property from the document, a value of the
person's own, or nothing, as the settings say; that *Strip all metadata* leaves everything out
but the language, and the language too where that is left out, and takes the renderer's name
out of a PDF; that a choice typed for one file goes over the default and *strip all* over
both; that a file whose title is left out is still called something; that the page, its form
and the export dialog say what they do; and that the choice goes into the account archive and
comes back.

The PDF is read twice. Its page, which is what WeasyPrint is handed, everywhere; and the file
itself where Pango is installed, as `tests/test_page_rendering.py` does. The step that takes
the renderer's name out (`pdf.finish`) is also tested on its own against `pydyf`, which runs
anywhere.
"""

from __future__ import annotations

import io
import re
import zipfile
from ctypes.util import find_library
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from defusedxml import ElementTree
from django.urls import reverse

from postulo.accounts.forms import ExportMetadataForm
from postulo.accounts.models import Profile
from postulo.core import export, importer, settings_sections
from postulo.documents import docx, odt, pdf, properties, rendering
from postulo.documents.forms import FilePropertiesForm
from postulo.documents.models import CV, CoverLetter, CVKind
from postulo.documents.properties import MetadataSource, Policy, Properties

pytestmark = pytest.mark.django_db

needs_weasyprint = pytest.mark.skipif(
    find_library("pango-1.0") is None,
    reason="Pango is not installed here, and WeasyPrint draws with it",
)

OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
DC = "{http://purl.org/dc/elements/1.1/}"
META = "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"

FIXED = {
    "title": "Curriculum vitae",
    "author": "A. Person",
    "subject": "Platform engineering",
    "keywords": "python, reliability",
    "language": "fr",
}


@pytest.fixture
def person(user):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])
    return user


@pytest.fixture
def cv(person):
    return CV.objects.create(
        owner=person, name="Backend EN", headline="Backend engineer", language="en-GB"
    )


@pytest.fixture
def letter(person):
    return CoverLetter.objects.create(
        owner=person, name="General", subject="Hello", body="Dear team.", language="en-GB"
    )


def settle(user, *, strip=False, **rules):
    """Set the person's Settings → Export: ``title="omit"``, or ``title=("fixed", "x")``."""
    profile, _created = Profile.objects.get_or_create(user=user)
    profile.export_strip_metadata = strip
    for name, rule in rules.items():
        source, value = rule if isinstance(rule, tuple) else (rule, "")
        setattr(profile, f"export_{name}_source", source)
        setattr(profile, f"export_{name}", value)
    profile.save()
    # The document reads the owner's profile through the relation it already holds.
    user.profile = Profile.objects.get(pk=profile.pk)
    return profile


# ------------------------------------------------------------------ reading a file back


def parts(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        return {name: package.read(name) for name in package.namelist()}


def odt_properties(cv) -> dict:
    content = odt.write(rendering.cv_outline(cv))
    meta = ElementTree.fromstring(parts(content)["meta.xml"]).find(f"{OFFICE}meta")

    def text(tag):
        found = meta.find(tag)
        return found.text if found is not None else None

    return {
        "title": text(f"{DC}title"),
        "author": text(f"{DC}creator"),
        "initial_creator": text(f"{META}initial-creator"),
        "subject": text(f"{DC}subject"),
        "keywords": ", ".join(k.text for k in meta.findall(f"{META}keyword")) or None,
        "language": text(f"{DC}language"),
        "styles": parts(content)["styles.xml"].decode(),
    }


def docx_properties(cv) -> dict:
    content = docx.write(rendering.cv_outline(cv))
    core = ElementTree.fromstring(parts(content)["docProps/core.xml"])

    def text(tag):
        found = core.find(tag)
        return found.text if found is not None else None

    return {
        "title": text(f"{DC}title"),
        "author": text(f"{DC}creator"),
        "subject": text(f"{DC}subject"),
        "keywords": text(f"{CP}keywords"),
        "language": text(f"{DC}language"),
        "styles": parts(content)["word/styles.xml"].decode(),
    }


def page_properties(cv) -> dict:
    """What the PDF's page hands WeasyPrint: the `<title>`, the `<meta>`s and the `lang`."""
    html = rendering.render_cv_html(cv)

    def meta(name):
        found = re.search(rf'<meta name="{name}" content="([^"]*)">', html)
        return found.group(1) if found else None

    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()
    return {
        "title": title or None,
        "author": meta("author"),
        "subject": meta("description"),
        "keywords": meta("keywords"),
        "lang": re.search(r'<html lang="([^"]*)"', html).group(1),
        "scrub": meta(properties.SCRUB_META),
    }


@dataclass
class Drawn:
    """A PDF WeasyPrint drew, read where its properties are: not the whole file.

    The whole file names more than its properties. The bookmarks WeasyPrint writes from the
    headings are dictionaries with a `/Title` each -- "Alex Morgan", "Experience" -- and the
    text is the CV, name and all; a search of the bytes for `/Title` or a name finds those
    and says nothing about the information dictionary, which the file's compressed object
    stream holds besides. So the dictionary, the catalogue and the XMP packet are read off
    the `pydyf.PDF` as it is about to be written, after Postulo's finisher has run, and the
    file itself is kept for what is in it nowhere else.
    """

    info: dict[str, str]
    lang: str | None
    xmp: bytes
    content: bytes


def _text(value) -> str:
    return str(getattr(value, "string", value))


def drawn(cv) -> Drawn:
    """``cv`` drawn by the real backend, through `render` as an export draws it."""
    html = rendering.render_cv_html(cv)
    backend = pdf.WeasyPrintBackend()
    real = backend.document(html)
    seen: dict = {}

    class Watched:
        """The document `render` is handed, with a look at the PDF once it is finished."""

        def write_pdf(self, finisher=None, **options):
            def watch(document, built):
                if finisher is not None:
                    finisher(document, built)
                seen["info"] = {key: _text(value) for key, value in built.info.items()}
                seen["lang"] = _text(built.catalog["Lang"]) if "Lang" in built.catalog else None
                seen["xmp"] = b"".join(
                    part
                    for stream in pdf._metadata_streams(built)
                    for part in stream.stream
                    if isinstance(part, bytes)
                )

            return real.write_pdf(finisher=watch, **options)

    backend.document = lambda _html: Watched()
    content = backend.render(html)
    return Drawn(info=seen["info"], lang=seen["lang"], xmp=seen["xmp"], content=content)


# ------------------------------------------------------------- from the document (today)


def test_from_the_document_every_writer_carries_what_it_always_has(cv):
    for written in (odt_properties(cv), docx_properties(cv)):
        assert written["title"] == "Alex Morgan — CV"
        assert written["author"] == "Alex Morgan"
        assert written["subject"] is None and written["keywords"] is None
        assert written["language"] == "en-GB"
    page = page_properties(cv)
    assert page["title"] == "Alex Morgan — CV" and page["author"] == "Alex Morgan"
    assert page["subject"] is None and page["keywords"] is None
    assert page["lang"] == "en-GB"
    assert page["scrub"] is None, "nothing is taken out of a PDF nobody asked about"


def test_the_policy_of_nobody_is_todays_behaviour(person):
    profile = Profile.objects.get_or_create(user=person)[0]

    assert profile.export_strip_metadata is False
    assert {getattr(profile, f"export_{name}_source") for name in properties.FIELD_NAMES} == {
        MetadataSource.DOCUMENT
    }
    assert Policy.of(None) == properties.NO_POLICY


# ------------------------------------------------------------------------- a fixed value


def test_a_fixed_value_reaches_every_writer(person, cv):
    settle(person, **{name: ("fixed", value) for name, value in FIXED.items()})

    for written in (odt_properties(cv), docx_properties(cv)):
        assert written["title"] == FIXED["title"]
        assert written["author"] == FIXED["author"]
        assert written["subject"] == FIXED["subject"]
        assert written["keywords"] == FIXED["keywords"]
        assert written["language"] == "fr"
    page = page_properties(cv)
    assert page["title"] == FIXED["title"] and page["author"] == FIXED["author"]
    assert page["subject"] == FIXED["subject"] and page["keywords"] == FIXED["keywords"]
    assert page["lang"] == "fr"


def test_a_fixed_value_written_some_other_way_is_still_bounded(person, cv):
    profile = settle(person, title=("fixed", "x" * 5000), language=("fixed", "not a tag!"))

    outline = rendering.cv_outline(cv)

    assert len(outline.title) == properties.MAX_TITLE
    assert outline.language == "en-GB", "a fixed language that is not a tag is no language"
    assert profile.export_language_source == MetadataSource.FIXED


# ---------------------------------------------------------------------------- leave out


def test_a_property_left_out_is_in_no_writer(person, cv):
    settle(person, **dict.fromkeys(properties.FIELD_NAMES, "omit"))

    for written in (odt_properties(cv), docx_properties(cv)):
        assert written["title"] is None and written["author"] is None
        assert written["subject"] is None and written["keywords"] is None
        assert written["language"] is None
        assert "Morgan" not in written["styles"]
    assert odt_properties(cv)["initial_creator"] is None
    page = page_properties(cv)
    assert page["title"] is None and page["author"] is None
    assert page["scrub"] == "language"


def test_a_language_left_out_leaves_the_properties_and_not_the_text(person, cv):
    """The paragraphs are still set in it -- spelling, hyphenation, the fonts a script
    needs -- and the PDF is still drawn with it; only the property is gone."""
    settle(person, language="omit")

    assert 'fo:language="en"' in odt_properties(cv)["styles"]
    assert 'w:val="en-GB"' in docx_properties(cv)["styles"]
    assert page_properties(cv)["lang"] == "en-GB"
    assert odt_properties(cv)["language"] is None and docx_properties(cv)["language"] is None


def test_a_file_whose_title_is_left_out_is_still_called_something(client, person, cv):
    settle(person, title="omit")
    client.force_login(person)

    response = client.post(reverse("documents:cv_download", args=[cv.pk, "odt"]))

    assert 'filename="CV.odt"' in response["Content-Disposition"]
    assert rendering.file_title(cv) == "CV"


def test_only_the_field_left_out_is_left_out(person, cv):
    settle(person, author="omit")

    page = page_properties(cv)

    assert page["author"] is None and page["title"] == "Alex Morgan — CV"
    assert odt_properties(cv)["title"] == "Alex Morgan — CV"


# ------------------------------------------------------------------------- strip all


def test_strip_all_leaves_only_the_language(person, cv):
    settle(person, strip=True, **{name: ("fixed", value) for name, value in FIXED.items()})

    for written in (odt_properties(cv), docx_properties(cv)):
        assert written["title"] is None and written["author"] is None
        assert written["subject"] is None and written["keywords"] is None
        assert written["language"] == "fr", "a fixed language survives strip all"
    page = page_properties(cv)
    assert page["title"] is None and page["author"] is None
    assert page["subject"] is None and page["keywords"] is None
    assert page["scrub"] == "producer"


def test_strip_all_with_the_language_left_out_leaves_nothing(person, cv):
    settle(person, strip=True, language="omit")

    for written in (odt_properties(cv), docx_properties(cv)):
        assert all(written[name] is None for name in properties.FIELD_NAMES)
    assert page_properties(cv)["scrub"] == "producer language"


def test_strip_all_goes_over_what_was_typed_for_one_file(person, cv):
    settle(person, strip=True)

    outline = rendering.cv_outline(cv, Properties.from_data(FIXED))

    assert outline.title == outline.author == outline.subject == outline.keywords == ""
    assert outline.language == "fr", "the language typed for the file is kept"
    html = rendering.render_cv_html(cv, properties=Properties.from_data(FIXED))
    assert 'name="author"' not in html and "<title></title>" in html


def test_what_was_typed_for_one_file_goes_over_a_property_left_out(person, cv):
    settle(person, author="omit")

    outline = rendering.cv_outline(cv, Properties.from_data({"author": "Typed"}))

    assert outline.author == "Typed"


def test_strip_all_reaches_a_letter_and_a_portfolio(person, letter, cv):
    settle(person, strip=True)
    cv.kind = CVKind.PORTFOLIO
    cv.save(update_fields=["kind"])

    letter_html = rendering.render_letter_html(letter)
    portfolio_html = rendering.render_cv_html(cv)
    outline = rendering.letter_outline(letter)

    for html in (letter_html, portfolio_html):
        assert 'name="author"' not in html and "<title></title>" in html
        assert f'<meta name="{properties.SCRUB_META}" content="producer">' in html
    assert outline.title == outline.author == ""


def test_a_stripped_snapshot_is_recorded_as_without_its_properties(person, cv):
    class Fake:
        name = "fake"

        def is_available(self):
            return True

        def render(self, html):
            return b"%PDF-1.7 fake " + html.encode()[-20:]

    settle(person, strip=True)

    filed = rendering.snapshot_cv(cv, backend=Fake())

    assert filed.with_properties is False
    assert filed.title == "CV", "a title left out still names the record"


def test_a_portfolio_without_its_properties_names_no_author(person, cv):
    """It read the contact block's name straight, so *without* left the author in (#480)."""
    cv.kind = CVKind.PORTFOLIO
    cv.save(update_fields=["kind"])

    html = rendering.render_cv_html(cv, properties=Properties(include=False))

    assert 'name="author"' not in html
    assert '<meta name="author" content="Alex Morgan">' in rendering.render_cv_html(cv)


# --------------------------------------------------------------- the PDF renderer's name


def weasyprint_like_pdf(scrub: str):
    """A PDF as WeasyPrint has built it when it calls its finisher, and the document."""
    pydyf = pytest.importorskip("pydyf")

    built = pydyf.PDF()
    built.info["Producer"] = pydyf.String("WeasyPrint 70.0")
    built.info["Creator"] = pydyf.String("A theme")
    built.info["CreationDate"] = pydyf.String("D:20261027000000Z")
    built.info["ModDate"] = pydyf.String("D:20261027000000Z")
    built.info["Title"] = pydyf.String("Kept title")
    built.catalog["Lang"] = pydyf.String("en-GB")
    packet = (
        b'<rdf:RDF><rdf:Description rdf:about="" pdf:Producer="WeasyPrint 70.0"/>'
        b'<rdf:Description rdf:about=""><xmp:CreatorTool>A theme</xmp:CreatorTool>'
        b'</rdf:Description><rdf:Description rdf:about="">'
        b"<xmp:CreateDate>2026-10-27</xmp:CreateDate></rdf:Description></rdf:RDF>"
    )
    stream = pydyf.Stream([packet], {"Type": "/Metadata", "Subtype": "/XML"})
    built.add_object(stream)
    built.catalog["Metadata"] = stream.reference
    document = SimpleNamespace(
        metadata=SimpleNamespace(custom={properties.SCRUB_META: scrub} if scrub else {})
    )
    return built, document


def written(built) -> bytes:
    output = io.BytesIO()
    built.write(output)
    return output.getvalue()


def test_the_finisher_takes_the_producer_out_of_both_places():
    built, document = weasyprint_like_pdf("producer")

    pdf.finish(document, built)

    content = written(built)
    for gone in (b"WeasyPrint", b"/Producer", b"/Creator", b"/CreationDate", b"/ModDate"):
        assert gone not in content, gone
    assert b"CreatorTool" not in content and b"CreateDate" not in content
    assert b"Kept title" in content and b"/Lang" in content


def test_the_finisher_takes_the_language_out_only_when_asked():
    built, document = weasyprint_like_pdf("language")

    pdf.finish(document, built)

    content = written(built)
    assert b"/Lang" not in content
    assert b"WeasyPrint" in content, "the producer stays unless everything is stripped"


def test_the_finisher_leaves_a_page_that_asked_nothing_as_it_was():
    built, document = weasyprint_like_pdf("")
    before = written(weasyprint_like_pdf("")[0])

    pdf.finish(document, built)

    assert written(built) == before


@needs_weasyprint
def test_a_pdf_from_the_document_names_its_producer_title_and_language(cv):
    drawn_pdf = drawn(cv)

    assert drawn_pdf.info["Producer"].startswith("WeasyPrint")
    assert drawn_pdf.info["Title"] == "Alex Morgan — CV"
    assert drawn_pdf.info["Author"] == "Alex Morgan"
    assert drawn_pdf.lang == "en-GB"
    assert b"WeasyPrint" in drawn_pdf.xmp


@needs_weasyprint
def test_a_pdf_carries_a_fixed_value(person, cv):
    settle(person, **{name: ("fixed", value) for name, value in FIXED.items()})

    drawn_pdf = drawn(cv)

    assert drawn_pdf.info["Title"] == "Curriculum vitae"
    assert drawn_pdf.info["Author"] == "A. Person"
    assert drawn_pdf.info["Subject"] == "Platform engineering"
    assert drawn_pdf.info["Keywords"] == "python, reliability"
    assert drawn_pdf.lang == "fr"


@needs_weasyprint
def test_a_pdf_leaves_out_what_is_left_out(person, cv):
    settle(person, **dict.fromkeys(properties.FIELD_NAMES, "omit"))

    drawn_pdf = drawn(cv)

    assert not {"Title", "Author", "Subject", "Keywords"} & set(drawn_pdf.info)
    assert drawn_pdf.lang is None
    assert b"Morgan" not in drawn_pdf.xmp
    assert "Producer" in drawn_pdf.info, "only strip all takes the producer out"


@needs_weasyprint
def test_a_stripped_pdf_has_no_producer_and_keeps_its_language(person, cv):
    settle(person, strip=True)

    drawn_pdf = drawn(cv)

    assert drawn_pdf.info == {}, "nothing is left in the information dictionary"
    assert drawn_pdf.lang == "en-GB"
    assert b"WeasyPrint" not in drawn_pdf.xmp and b"Morgan" not in drawn_pdf.xmp


# ---------------------------------------------------------------------- the export dialog


def test_the_dialog_starts_from_the_default(person, cv):
    settle(person, title=("fixed", "My CV"), author="omit")

    started = rendering.file_defaults(cv)

    assert started["title"] == "My CV" and started["author"] == ""
    assert started["language"] == "en-GB" and started["strip_all"] is False


def test_an_export_nobody_touched_is_the_default(client, person, cv):
    """The dialog posts what it was given: a field left out starts blank, and blank is the
    default, so the author does not come back typed in over it."""
    settle(person, author="omit")
    client.force_login(person)
    started = rendering.file_defaults(cv)
    posted = {key: value for key, value in started.items() if key != "strip_all"}
    posted["with_properties"] = "1"

    response = client.post(reverse("documents:cv_download", args=[cv.pk, "odt"]), posted)

    meta = ElementTree.fromstring(parts(response.content)["meta.xml"]).find(f"{OFFICE}meta")
    assert meta.find(f"{DC}creator") is None
    assert meta.find(f"{DC}title").text == "Alex Morgan — CV"


def test_the_dialog_says_so_when_everything_is_stripped(client, person, cv):
    settle(person, strip=True)
    client.force_login(person)

    html = client.get(cv.get_absolute_url()).content.decode()

    assert "data-metadata-stripped" in html
    assert 'name="title"' not in html and 'name="with_properties"' not in html
    assert reverse("settings:export") in html
    assert FilePropertiesForm(initial=rendering.file_defaults(cv)).stripped is True


def test_the_dialog_links_to_the_default_otherwise(client, person, cv):
    client.force_login(person)

    html = client.get(cv.get_absolute_url()).content.decode()

    assert "data-metadata-stripped" not in html
    assert 'name="title"' in html and reverse("settings:export") in html


# ------------------------------------------------------------------- the page and its form


def form_data(**changes) -> dict:
    data = {"export_strip_metadata": ""}
    for name in properties.FIELD_NAMES:
        data[f"export_{name}_source"] = MetadataSource.DOCUMENT
        data[f"export_{name}"] = ""
    data.update(changes)
    if not data["export_strip_metadata"]:
        del data["export_strip_metadata"]
    return data


def test_the_page_is_in_the_settings_and_draws_every_property(client, person):
    client.force_login(person)

    response = client.get(reverse("settings:export"))

    assert response.status_code == 200
    html = response.content.decode()
    for name in properties.FIELD_NAMES:
        assert f'data-export-property="{name}"' in html
        assert f'name="export_{name}_source"' in html
    assert 'name="export_strip_metadata"' in html
    assert "screen reader" in html and "PDF/UA" in html, "it says why the language stays"
    assert "settings:export" in {section.url_name for section in settings_sections.sections()}


def test_a_signed_out_visitor_is_sent_to_sign_in(client):
    response = client.get(reverse("settings:export"))

    assert response.status_code == 302 and "login" in response["Location"]


def test_the_page_saves_the_choice(client, person):
    client.force_login(person)

    response = client.post(
        reverse("settings:export"),
        form_data(
            export_strip_metadata="on",
            export_title_source="fixed",
            export_title="  Curriculum  ",
            export_author_source="omit",
            export_language_source="fixed",
            export_language="pt-br",
        ),
    )

    assert response.status_code == 302
    profile = Profile.objects.get(user=person)
    assert profile.export_strip_metadata is True
    assert profile.export_title_source == "fixed" and profile.export_title == "Curriculum"
    assert profile.export_author_source == "omit"
    assert profile.export_language == "pt-BR"


def test_a_fixed_value_left_empty_is_refused(person):
    profile = Profile.objects.get_or_create(user=person)[0]

    form = ExportMetadataForm(form_data(export_author_source="fixed"), instance=profile)

    assert not form.is_valid()
    assert form.errors["export_author"] == [str(ExportMetadataForm.WANTS_A_VALUE)]


def test_a_value_behind_another_choice_is_not_kept(person):
    profile = Profile.objects.get_or_create(user=person)[0]

    form = ExportMetadataForm(
        form_data(export_subject_source="omit", export_subject="left over"), instance=profile
    )

    assert form.is_valid(), form.errors
    assert form.save().export_subject == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [("export_title_source", "everywhere"), ("export_language", "not a tag!")],
)
def test_the_form_refuses_what_is_not_a_choice(person, field, value):
    profile = Profile.objects.get_or_create(user=person)[0]
    data = form_data(export_language_source="fixed", export_language="fr")
    data[field] = value

    assert not ExportMetadataForm(data, instance=profile).is_valid()


# ------------------------------------------------------------------------- the archive


def test_the_archive_carries_the_choice_and_its_format_moved():
    assert set(export.EXPORT_METADATA_FIELDS) <= set(export.PROFILE_FIELDS)
    assert not set(export.EXPORT_METADATA_FIELDS) & set(export.CANDIDATE_PROFILE_FIELDS)
    assert export.FORMAT_VERSION >= 77
    with open(export.__file__, encoding="utf-8") as source:
        assert "77 added what the person's exported files say" in source.read()


def test_the_choice_survives_the_round_trip(person, other_user):
    settle(person, strip=True, title=("fixed", "Curriculum"), author="omit", language="omit")

    importer.load(other_user, zipfile.ZipFile(export.write_archive(person)))

    restored = Profile.objects.get(user=other_user)
    assert restored.export_strip_metadata is True
    assert restored.export_title_source == "fixed" and restored.export_title == "Curriculum"
    assert restored.export_author_source == "omit"
    assert restored.export_language_source == "omit"
    assert restored.export_subject_source == "document"


def test_a_choice_the_archive_gets_wrong_is_not_believed(person):
    import json

    document = export.build_document(person)
    document["account"]["profile"].update(
        export_title_source="everywhere",
        export_language="not a tag!",
        export_strip_metadata="yes",
        export_author="x" * 5000,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))

    importer.load(person, zipfile.ZipFile(buffer), force=True)

    profile = Profile.objects.get(user=person)
    assert profile.export_title_source == "document"
    assert profile.export_language == "" and profile.export_author == ""
    assert profile.export_strip_metadata is False


def test_a_language_nobody_fixed_is_written_as_none_and_a_fixed_one_comes_back(person, other_user):
    """Blank is no language: null in the archive, not an empty tag (#337, #878)."""
    Profile.objects.get_or_create(user=person)
    assert export.build_document(person)["account"]["profile"]["export_language"] is None

    settle(person, language=("fixed", "pt-br"))
    importer.load(other_user, zipfile.ZipFile(export.write_archive(person)))

    restored = Profile.objects.get(user=other_user)
    assert restored.export_language == "pt-BR" and restored.export_language_source == "fixed"
