"""Choosing the paper: a size and an orientation for an export (#344).

Every document Postulo drew was A4 portrait. These hold the vocabulary (four papers), what a
theme declares (and that declaring nothing is A4 portrait only), the chain the default comes
down (the instance, the person, one export), the refusal of a paper a theme does not print
on, the page rule Postulo writes, and each renderer drawing each paper at its size: Chromium
where a browser launches here, the Word and OpenDocument writers everywhere. WeasyPrint's are
in `tests/test_pdf_render.py`, which runs where Pango is.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from django.urls import reverse

from postulo.accounts.forms import ExportMetadataForm
from postulo.accounts.models import Profile
from postulo.core import export, importer, site
from postulo.core.errands import Refused
from postulo.core.models import Errand, SiteSettings
from postulo.core.server_forms import DefaultsForm
from postulo.documents import docx, odt, paper, paper_choice, rendering, themes
from postulo.documents.models import CV, CoverLetter
from postulo.documents.outline import Outline, paragraph
from postulo.documents.pdf import ChromiumBackend

pytestmark = pytest.mark.django_db

A4_PORTRAIT, A4_LANDSCAPE, LETTER_PORTRAIT, LETTER_LANDSCAPE = paper.EVERY


@pytest.fixture
def restricted():
    """A plugin's theme that declares no paper, so prints CVs and letters on A4 portrait."""
    themes.register(
        themes.Theme(
            name="parchment",
            label="Parchment",
            templates={
                themes.Kind.CV: "documents/themes/plain/cv.html",
                themes.Kind.LETTER: "documents/themes/plain/letter.html",
            },
            provider="Parchment Press",
        )
    )
    yield "parchment"
    themes.forget("parchment")


@pytest.fixture
def cv(user):
    return CV.objects.create(owner=user, name="Backend", headline="Backend engineer")


def instance_paper(key: str) -> None:
    row = SiteSettings.get()
    row.default_paper = key
    row.save()
    site.forget_current()


def personal_paper(user, key: str) -> None:
    profile = Profile.objects.get_or_create(user=user)[0]
    profile.paper = key
    profile.save()
    user.refresh_from_db()


# ----------------------------------------------------------------------- the vocabulary


def test_four_papers_each_measured_turned_and_named():
    assert [one.key for one in paper.EVERY] == [
        "a4-portrait",
        "a4-landscape",
        "letter-portrait",
        "letter-landscape",
    ]
    assert (A4_PORTRAIT.width_mm, A4_PORTRAIT.height_mm) == (210, 297)
    assert (A4_LANDSCAPE.width_mm, A4_LANDSCAPE.height_mm) == (297, 210)
    assert (LETTER_PORTRAIT.width_mm, LETTER_PORTRAIT.height_mm) == (215.9, 279.4)
    assert (LETTER_LANDSCAPE.width_mm, LETTER_LANDSCAPE.height_mm) == (279.4, 215.9)
    assert LETTER_LANDSCAPE.css == "letter landscape" and A4_PORTRAIT.css == "A4 portrait"
    assert str(LETTER_PORTRAIT) == "US Letter, portrait"
    assert paper.find("letter-landscape") == LETTER_LANDSCAPE
    assert paper.find("a3-portrait") is None and paper.find(None) is None


# ------------------------------------------------------------------ what a theme declares


def test_a_theme_that_declares_nothing_prints_on_a4_portrait_only(restricted):
    assert paper.offered(restricted, themes.Kind.CV) == (A4_PORTRAIT,)
    assert paper.declared(themes.Theme(name="bare", label="Bare"), themes.Kind.CV) == (A4_PORTRAIT,)


def test_postulos_own_themes_print_every_kind_on_every_paper():
    for theme in themes.BUILT_IN:
        for kind in themes.Kind.values:
            assert paper.declared(theme, kind) == paper.EVERY, (theme.name, kind)
            assert paper.restriction(theme.name, kind) == ""


def test_a_theme_may_declare_per_kind_and_a_key_it_does_not_know_is_passed_over():
    theme = themes.Theme(
        name="wide",
        label="Wide",
        templates={themes.Kind.CV: "x.html", themes.Kind.REPORT: "y.html"},
        papers={
            themes.Kind.REPORT: ("a4-landscape", "letter-landscape", "a3-landscape"),
            themes.Kind.CV: "any",
        },
    )

    assert paper.declared(theme, themes.Kind.REPORT) == (A4_LANDSCAPE, LETTER_LANDSCAPE)
    assert paper.declared(theme, themes.Kind.CV) == paper.EVERY
    assert paper.declared(theme, themes.Kind.LETTER) == (A4_PORTRAIT,), "not declared"
    only_unknown = themes.Theme(name="odd", label="Odd", papers=("a3-portrait",))
    assert paper.declared(only_unknown, themes.Kind.CV) == (A4_PORTRAIT,)


def test_an_unknown_theme_is_offered_what_plain_prints_on():
    assert paper.offered("gone-with-its-plugin", themes.Kind.CV) == paper.EVERY


# ------------------------------------------------------------------- the defaults chain


def test_nothing_chosen_anywhere_is_a4_portrait(user):
    assert paper_choice.instance_default() == A4_PORTRAIT
    assert paper_choice.for_document("plain", themes.Kind.CV, user) == A4_PORTRAIT


def test_the_instance_then_the_person_then_the_export(user):
    instance_paper("letter-portrait")
    assert paper_choice.personal_default(user) == LETTER_PORTRAIT
    assert paper_choice.for_document("plain", themes.Kind.CV, user) == LETTER_PORTRAIT

    personal_paper(user, "a4-landscape")
    assert paper_choice.for_document("plain", themes.Kind.CV, user) == A4_LANDSCAPE

    asked = paper_choice.for_document("plain", themes.Kind.CV, user, "letter-landscape")
    assert asked == LETTER_LANDSCAPE


def test_a_person_who_never_chose_follows_a_change_of_the_instance(user, other_user):
    personal_paper(other_user, "a4-portrait")
    instance_paper("letter-portrait")

    assert paper_choice.personal_default(user) == LETTER_PORTRAIT
    assert paper_choice.personal_default(other_user) == A4_PORTRAIT


def test_a_default_the_theme_does_not_print_on_gives_way_and_draws(user, restricted):
    personal_paper(user, "letter-landscape")

    assert paper_choice.for_document(restricted, themes.Kind.CV, user) == A4_PORTRAIT


def test_a_paper_one_export_asked_for_is_refused_where_the_theme_does_not_print_on_it(
    user, restricted
):
    with pytest.raises(themes.CannotRender) as refused:
        paper_choice.for_document(restricted, themes.Kind.CV, user, "letter-landscape")

    assert "Parchment" in str(refused.value) and "A4, portrait" in str(refused.value)
    with pytest.raises(themes.CannotRender):
        paper_choice.for_document("plain", themes.Kind.CV, user, "a3-portrait")


def test_a_file_of_words_takes_any_paper_and_otherwise_the_persons(user):
    personal_paper(user, "letter-portrait")

    assert paper_choice.for_file(user, "a4-landscape") == A4_LANDSCAPE
    assert paper_choice.for_file(user, "") == LETTER_PORTRAIT
    assert paper_choice.for_file(user, "nonsense") == LETTER_PORTRAIT


# --------------------------------------------------------------------- the page rule


def test_postulo_writes_the_page_rule_last_in_the_head(cv):
    html = rendering.render_cv_html(cv, paper="letter-landscape")

    head = html[: html.lower().index("</head>")]
    assert head.rstrip().endswith(
        '<meta name="postulo-paper" content="letter-landscape">'
        "<style>@page { size: letter landscape; }</style>"
    )
    assert "size: A4" not in html.replace("size: A4 portrait", ""), "no theme rule of its own"
    assert paper.read_from(html) == LETTER_LANDSCAPE


def test_the_preview_carries_the_rule_under_the_requests_nonce(client, user, cv):
    client.force_login(user)

    html = client.get(reverse("documents:cv_preview", args=[cv.pk])).content.decode()

    nonce = re.search(r'<style nonce="([^"]+)">@page \{ size: A4 portrait; \}</style>', html)
    assert nonce, "a <style> without the nonce would be refused by the preview's policy"


def test_a_page_with_no_head_still_gets_its_rule():
    assert paper.with_paper("<p>hi</p>", A4_LANDSCAPE).startswith('<meta name="postulo-paper"')
    assert paper.with_paper("<html><body>x", A4_LANDSCAPE).index("<style>") < (
        paper.with_paper("<html><body>x", A4_LANDSCAPE).index("<body>")
    )


def test_the_last_paper_written_is_the_one_read():
    """A theme that wrote a meta of its own wrote it before Postulo's."""
    html = paper.with_paper(
        '<head><meta name="postulo-paper" content="a4-portrait"></head>', A4_LANDSCAPE
    )
    assert paper.read_from(html) == A4_LANDSCAPE
    assert paper.read_from("<html></html>") is None


def test_the_template_is_told_the_paper(user):
    letter = CoverLetter.objects.create(owner=user, name="General", body="Hello")

    html = rendering.render_letter_html(letter, paper="a4-landscape")

    assert "size: A4 landscape" in html


# ---------------------------------------------------------------- each renderer, each paper


@pytest.mark.parametrize("chosen", paper.EVERY, ids=lambda one: one.key)
def test_chromium_is_told_the_width_and_the_height(chosen):
    size = ChromiumBackend.page_size(paper.with_paper("<html><head></head></html>", chosen))

    assert size == {"width": f"{chosen.width_mm}mm", "height": f"{chosen.height_mm}mm"}


def test_a_page_postulo_did_not_write_is_a4_portrait_to_chromium():
    assert ChromiumBackend.page_size("<html></html>") == {"width": "210.0mm", "height": "297.0mm"}


#: A PDF page's size, in points.
MEDIA_BOX = re.compile(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]")


def points(millimetres: float) -> float:
    return millimetres / 25.4 * 72


@pytest.mark.parametrize("chosen", paper.EVERY, ids=lambda one: one.key)
def test_chromium_draws_each_paper_at_its_size(cv, chosen):
    from tests.security.test_pdf_fetching import _chromium_launches

    if not _chromium_launches():
        pytest.skip("no browser Playwright can launch here")

    pdf = ChromiumBackend().render(rendering.render_cv_html(cv, paper=chosen.key))

    boxes = {(float(width), float(height)) for width, height in MEDIA_BOX.findall(pdf)}
    assert boxes, "a page with a size"
    for width, height in boxes:
        assert width == pytest.approx(points(chosen.width_mm), abs=1.5)
        assert height == pytest.approx(points(chosen.height_mm), abs=1.5)


def outline_on(chosen) -> Outline:
    return Outline(title="CV", language="en", blocks=(paragraph("Hello"),), paper=chosen)


@pytest.mark.parametrize("chosen", paper.EVERY, ids=lambda one: one.key)
def test_a_word_file_is_set_on_each_paper(chosen):
    document = docx.parts_of(outline_on(chosen))["word/document.xml"]

    size = re.search(r"<w:pgSz [^>]*/>", document).group(0)
    assert f'w:w="{docx.twips(chosen.width_mm)}"' in size
    assert f'w:h="{docx.twips(chosen.height_mm)}"' in size
    assert ('w:orient="landscape"' in size) is chosen.landscape
    if chosen == A4_PORTRAIT:
        assert size == '<w:pgSz w:w="11906" w:h="16838"/>', "A4 as it always was"
    if chosen == LETTER_PORTRAIT:
        assert 'w:w="12240" w:h="15840"' in size, "8½ × 11 inches"


@pytest.mark.parametrize("chosen", paper.EVERY, ids=lambda one: one.key)
def test_an_opendocument_file_is_set_on_each_paper(chosen):
    styles = odt.parts_of(outline_on(chosen))["styles.xml"]

    assert f'fo:page-width="{chosen.width_mm:g}mm"' in styles
    assert f'fo:page-height="{chosen.height_mm:g}mm"' in styles
    assert f'style:print-orientation="{chosen.orientation}"' in styles


def test_an_outline_is_on_the_persons_paper_unless_one_was_asked_for(user, cv):
    personal_paper(user, "letter-portrait")
    cv.refresh_from_db()

    assert rendering.cv_outline(cv).paper == LETTER_PORTRAIT
    assert rendering.cv_outline(cv, paper="a4-landscape").paper == A4_LANDSCAPE


def test_a_word_download_takes_the_paper_posted_with_it(client, user, cv):
    client.force_login(user)

    response = client.post(
        reverse("documents:cv_download", args=[cv.pk, "docx"]), {"paper": "letter-landscape"}
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        document = package.read("word/document.xml").decode()
    assert 'w:orient="landscape"' in document and 'w:w="15840"' in document


# ----------------------------------------------------------------------------- the picker


def test_the_cv_page_offers_every_paper_for_a_fluid_theme_from_the_default(client, user, cv):
    personal_paper(user, "letter-portrait")
    client.force_login(user)

    page = client.get(cv.get_absolute_url()).content.decode()

    picker = re.search(r'<select name="paper"[^>]*>.*?</select>', page, re.DOTALL).group(0)
    assert 'form="file-properties"' in picker, "posted by every button that makes a file"
    assert re.findall(r'<option value="([^"]+)"', picker) == [one.key for one in paper.EVERY]
    assert re.search(r'<option value="letter-portrait"[^>]*selected', picker)
    assert "prints this only on" not in page


def test_a_restricted_theme_is_offered_its_paper_and_the_page_says_why(client, user, restricted):
    cv = CV.objects.create(owner=user, name="Backend", theme=restricted)
    client.force_login(user)

    page = client.get(cv.get_absolute_url()).content.decode()

    picker = re.search(r'<select name="paper"[^>]*>.*?</select>', page, re.DOTALL).group(0)
    assert re.findall(r'<option value="([^"]+)"', picker) == ["a4-portrait"]
    assert "The Parchment theme prints this only on: A4, portrait." in page


def test_the_letter_page_draws_its_draft_on_the_paper_chosen(client, user):
    letter = CoverLetter.objects.create(owner=user, name="General", body="Hello")
    client.force_login(user)

    page = client.get(letter.get_absolute_url()).content.decode()

    assert '<select name="paper"' in page


# -------------------------------------------------------------- an export, and a refusal


def test_a_draft_on_a_paper_the_theme_does_not_print_on_is_refused_in_a_sentence(
    client, user, restricted, monkeypatch
):
    cv = CV.objects.create(owner=user, name="Backend", theme=restricted)
    monkeypatch.setattr("postulo.documents.pdf.draft_pdf", lambda html, **kw: b"%PDF-")
    client.force_login(user)

    response = client.post(
        reverse("documents:cv_draft", args=[cv.pk]), {"paper": "letter-landscape"}, follow=True
    )

    assert "The Parchment theme prints this only on: A4, portrait." in response.content.decode()


def test_a_draft_is_drawn_on_the_paper_chosen(client, user, cv, monkeypatch):
    drawn: list[str] = []
    monkeypatch.setattr(
        "postulo.documents.pdf.draft_pdf", lambda html, **kw: drawn.append(html) or b"%PDF-"
    )
    client.force_login(user)

    client.post(reverse("documents:cv_draft", args=[cv.pk]), {"paper": "letter-landscape"})

    assert paper.read_from(drawn[0]) == LETTER_LANDSCAPE


def test_an_export_sends_the_paper_and_refuses_one_the_theme_does_not_print_on(
    client, user, cv, restricted
):
    client.force_login(user)

    client.post(reverse("documents:cv_export", args=[cv.pk]), {"paper": "a4-landscape"})
    errand = Errand.objects.filter(owner=user).latest("pk")
    assert errand.payload["paper"] == "a4-landscape"

    cv.theme = restricted
    cv.save()
    sent = Errand.objects.count()
    response = client.post(
        reverse("documents:cv_export", args=[cv.pk]), {"paper": "a4-landscape"}, follow=True
    )
    assert Errand.objects.count() == sent, "nothing is sent off to fail"
    assert "prints this only on" in response.content.decode()


def test_the_errand_refuses_a_paper_the_theme_stopped_printing_on(user, cv, restricted):
    from postulo.documents.slow import render_a_cv

    cv.theme = restricted
    cv.save()
    errand = Errand(owner=user, payload={"cv_id": cv.pk, "paper": "letter-portrait"})

    with pytest.raises(Refused):
        render_a_cv(errand)


def test_the_snapshot_is_drawn_on_the_paper_asked_for(user, cv):
    seen: list[str] = []

    class Backend:
        name = "test"

        def render(self, html):
            seen.append(html)
            return b"%PDF-1.7 test"

    rendering.snapshot_cv(cv, backend=Backend(), paper="letter-portrait")

    assert paper.read_from(seen[0]) == LETTER_PORTRAIT


# ----------------------------------------------------------------------------- the report


def test_the_report_offers_the_paper_and_draws_on_it(client, user, monkeypatch):
    drawn: list[str] = []
    monkeypatch.setattr(
        "postulo.documents.pdf.draft_pdf", lambda html, **kw: drawn.append(html) or b"%PDF-"
    )
    client.force_login(user)

    page = client.get(reverse("applications:report")).content.decode()
    assert '<select name="paper" id="report-paper"' in page

    client.get(reverse("applications:report_pdf"), {"paper": "a4-landscape"}).close()
    assert paper.read_from(drawn[0]) == A4_LANDSCAPE

    client.post(reverse("applications:report_pdf"), {"paper": "letter-landscape"})
    assert Errand.objects.filter(owner=user).latest("pk").payload["paper"] == "letter-landscape"


def test_the_report_refuses_a_paper_postulo_does_not_have(client, user):
    client.force_login(user)
    sent = Errand.objects.count()

    response = client.post(reverse("applications:report_pdf"), {"paper": "a0-portrait"})
    assert response.status_code == 302 and Errand.objects.count() == sent

    response = client.get(reverse("applications:report_pdf"), {"paper": "a0-portrait"})
    assert response.status_code == 302


# ---------------------------------------------------------------------- where it is set


def test_the_administrator_sets_the_instance_default():
    row = SiteSettings.get()
    form = DefaultsForm(
        {"instance_name": "Postulo", "default_paper": "letter-portrait"}, instance=row
    )
    assert form.is_valid(), form.errors
    form.save()
    site.forget_current()

    assert paper_choice.instance_default() == LETTER_PORTRAIT
    refused = DefaultsForm({"instance_name": "Postulo", "default_paper": "a0"}, instance=row)
    assert not refused.is_valid()


def test_a_person_sets_their_own_under_settings_export(client, user):
    instance_paper("letter-portrait")
    client.force_login(user)

    page = client.get(reverse("settings:export")).content.decode()
    assert "This instance&#x27;s default: US Letter, portrait" in page or (
        "This instance's default: US Letter, portrait" in page
    )

    profile = Profile.objects.get_or_create(user=user)[0]
    form = ExportMetadataForm(
        {
            **{
                f"export_{name}_source": "document"
                for name in ("title", "author", "subject", "keywords", "language")
            },
            "paper": "a4-landscape",
        },
        instance=profile,
    )
    assert form.is_valid(), form.errors
    form.save()
    user.refresh_from_db()
    assert paper_choice.personal_default(user) == A4_LANDSCAPE


# ----------------------------------------------------------------------------- the archive


def test_the_archive_carries_the_persons_paper(user, other_user):
    assert "paper" in export.PROFILE_FIELDS
    assert export.FORMAT_VERSION >= 79
    personal_paper(user, "letter-landscape")

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))

    assert Profile.objects.get(user=other_user).paper == "letter-landscape"


def test_a_paper_the_archive_gets_wrong_is_not_believed(user):
    personal_paper(user, "a4-landscape")
    document = export.build_document(user)
    document["account"]["profile"]["paper"] = "a0-portrait"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))

    importer.load(user, zipfile.ZipFile(buffer), force=True)

    assert Profile.objects.get(user=user).paper == "a4-landscape"
