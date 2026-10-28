"""A theme's own files, written into the document it sets (#343).

A theme draws a font, an SVG and an image of its own package in both renderers and in the
preview, and nothing is fetched while it does: the files are in the HTML before either
renderer starts. What the tag refuses -- a path out of the package, a file that is not what
it is needed to be, a hostile drawing, a file or a document over its ceiling -- is refused
here, and the preview is the one place a `data:` font is allowed.

The drawing tests skip where Pango is missing (WeasyPrint) or no Chromium can be launched,
as `tests/security/test_pdf_fetching.py` does.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import zipfile
from ctypes.util import find_library
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.template import Context, Template
from django.urls import reverse

from postulo.documents import theme_assets, themes
from postulo.documents.models import CV
from postulo.documents.rendering import render_cv_html
from tests import theme_package

pytestmark = pytest.mark.django_db

MODULE = "vellumassets"

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def vellum(tmp_path):
    """The test theme's package, registered as a plugin registers it."""
    package = theme_package.build(tmp_path, MODULE)
    theme_package.register(MODULE)
    yield package
    theme_package.remove(tmp_path, MODULE)


@pytest.fixture
def cv(user, vellum):
    return CV.objects.create(owner=user, name="Main", theme=MODULE, language="en-GB")


# ------------------------------------------------------------- what reaches the page


def test_registration_holds_a_theme_to_its_own_package(vellum):
    """The plugin wrote `package="postulo"`; it reads from its own package all the same."""
    assert themes.find(MODULE).package == MODULE


def test_a_theme_writes_its_font_its_drawing_and_its_image_into_the_document(cv):
    html = render_cv_html(cv)

    assert '@font-face { font-family: "Vellum Test"; src: url(data:font/woff2;base64,' in html
    assert 'format("woff2"); font-weight: 400; font-style: normal; }' in html
    assert '<img class="mark" src="data:image/png;base64,' in html
    assert re.search(
        r'<header><svg xmlns="http://www.w3.org/2000/svg"[^>]*viewBox="0 0 40 20"', html
    )
    # What the renderer will be asked to open: data: addresses and fragments, nothing else.
    for address in re.findall(r'(?:src|href)="([^"]*)"|url\(([^)]*)\)', html):
        found = next(part for part in address if part)
        assert found.startswith(("data:", "#")), found


def test_an_svgs_styles_become_attributes_and_its_ids_its_own(cv):
    """An editor's `<style>` would be the whole document's stylesheet; attributes are not."""
    html = render_cv_html(cv)
    drawing = html[html.index("<svg") : html.index("</svg>")]

    assert "<style" not in drawing and "style=" not in drawing
    assert 'fill="#123456"' in drawing and 'stroke="#654321"' in drawing
    assert 'stroke-width="2"' in drawing, "the more specific rule reached its element"
    assert 'opacity="0.5"' in drawing, "an inline style= became an attribute too"
    prefixed = re.search(r'<linearGradient id="(t[0-9a-f]{8}-g)"', drawing)
    assert prefixed, drawing
    assert f'fill="url(#{prefixed.group(1)})"' in drawing
    assert re.search(r'href="#t[0-9a-f]{8}-dot"', drawing)


def test_the_preview_carries_the_same_files_and_only_its_policy_allows_a_data_font(client, cv):
    client.force_login(cv.owner)

    response = client.get(reverse("documents:cv_preview", args=[cv.pk]))

    body = response.content.decode()
    assert "url(data:font/woff2;base64," in body
    assert "<svg" in body and 'src="data:image/png;base64,' in body
    policy = response["Content-Security-Policy"]
    assert re.search(r"font-src 'self' data:(;|$)", policy), policy
    assert "img-src 'self' data:" in policy

    page = client.get(reverse("documents:cv_detail", args=[cv.pk]))
    assert "font-src 'self';" in page["Content-Security-Policy"] + ";"
    assert "font-src 'self' data:" not in page["Content-Security-Policy"]


def test_no_policy_but_the_previews_allows_a_data_font(settings):
    """`font-src data:` is the preview's alone: the instance's policy and the map page's do
    without it, and nothing else in the code writes a `font-src` at all."""
    from postulo.documents.views import PREVIEW_POLICY
    from postulo.jobs import tiles

    assert "data:" not in settings.SECURE_CSP["font-src"]
    assert "data:" not in tiles.policy_for(tiles.current())["font-src"]
    assert "data:" in PREVIEW_POLICY["font-src"]

    writers = []
    for path in (ROOT / "src" / "postulo").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"font-src"' in text:
            writers.append(path.relative_to(ROOT).as_posix())
        if "PREVIEW_POLICY" in text:
            assert text.count("csp_override(PREVIEW_POLICY)") == 2, path
    assert sorted(writers) == [
        "src/postulo/config/settings/base.py",
        "src/postulo/documents/views.py",
    ]


# ---------------------------------------------------------- the scripts a font draws


def test_a_document_in_a_script_the_font_declares_but_does_not_draw_falls_back(cv):
    """The font declares Greek and draws none: a Greek CV gets no `@font-face` (#74)."""
    cv.language = "el"
    cv.save()

    html = render_cv_html(cv)

    assert "@font-face" not in html
    assert '"Vellum Test", sans-serif' in html, "the stack falls through to the server's"
    assert "<svg" in html, "the rest of the theme's files are still written"


def test_a_document_in_a_script_the_font_does_not_declare_falls_back(cv):
    cv.language = "uk"
    cv.save()

    assert "@font-face" not in render_cv_html(cv)


def test_a_font_nobody_declared_is_not_written(vellum, caplog):
    theme = themes.find(MODULE)
    inliner = theme_assets.Inliner(
        themes.Theme(name="x", label="x", templates=theme.templates, package=MODULE)
    )

    with pytest.raises(theme_assets.Refused, match="does not declare"):
        inliner.write("fonts/Vellum.woff2", family="Vellum Test")


def test_check_fonts_reports_what_a_theme_font_draws(vellum):
    from io import StringIO

    out = StringIO()
    try:
        call_command("check_fonts", stdout=out)
    except CommandError:
        pass  # the server's fonts are this machine's; the theme's lines come first

    said = out.getvalue()
    assert "draws      Latin in fonts/Vellum.woff2, theme Vellum" in said
    assert "declared, not drawn  Greek in fonts/Vellum.woff2, theme Vellum" in said


def test_the_overview_lists_a_theme_font_and_the_script_it_does_not_draw(client, vellum):
    from django.contrib.auth import get_user_model

    admin = get_user_model().objects.create_user(
        email="admin@example.org", password="not-a-real-password", is_staff=True
    )
    client.force_login(admin)

    html = client.get(reverse("server:overview")).content.decode()

    row = html[html.index("data-theme-fonts") :]
    row = row[: row.index("</dd>")]
    assert "<code>fonts/Vellum.woff2</code>" in row
    assert re.search(r'<span class="">Latin</span>', row)
    assert re.search(r'<span class="text-red-600[^"]*">Greek</span>', row)


# ------------------------------------------------------------ what is refused


@pytest.mark.parametrize(
    "name",
    [
        "../vellumassets/images/logo.svg",
        "images/../../secret.svg",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "images\\logo.svg",
        ".hidden.svg",
        "images//logo.svg",
        "",
    ],
)
def test_a_path_out_of_the_package_is_refused(vellum, name):
    (vellum.parent / "secret.svg").write_text(theme_package.LOGO, encoding="utf-8")

    with pytest.raises(theme_assets.Refused):
        theme_assets.read(MODULE, name)


def test_a_symbolic_link_out_of_the_package_is_refused(vellum, tmp_path):
    outside = tmp_path / "outside.svg"
    outside.write_text(theme_package.LOGO, encoding="utf-8")
    try:
        os.symlink(outside, vellum / "images" / "linked.svg")
    except OSError:
        pytest.skip("this machine will not make a symbolic link")

    with pytest.raises(theme_assets.Refused, match="symbolic link"):
        theme_assets.read(MODULE, "images/linked.svg")


def test_the_tag_writes_nothing_for_a_refused_name_and_says_why(cv, caplog):
    inliner = theme_assets.Inliner(themes.find(MODULE))
    template = Template('{% load theme_assets %}[{% theme_asset "../../etc/passwd" %}]')

    with caplog.at_level(logging.WARNING, logger="postulo.documents.theme_assets"):
        written = template.render(Context({theme_assets.CONTEXT_KEY: inliner}))

    assert written == "[]"
    assert "is not a path inside the theme's package" in caplog.text


def test_a_template_cannot_reach_the_inliner_by_name():
    """The key begins with an underscore, which the template language will not resolve."""
    from django.template import TemplateSyntaxError

    with pytest.raises(TemplateSyntaxError):
        Template("{{ " + theme_assets.CONTEXT_KEY + " }}")


def test_the_tag_outside_a_document_writes_nothing():
    template = Template('{% load theme_assets %}[{% theme_asset "images/logo.svg" %}]')

    assert template.render(Context({})) == "[]"


def test_a_theme_with_no_package_of_its_own_reads_nothing():
    with pytest.raises(theme_assets.Refused, match="no package"):
        theme_assets.read("", "images/logo.svg")


def test_a_package_inside_a_zip_reads_the_same_way(tmp_path):
    """`importlib.resources`, not the file system: a zipped package is a package too."""
    archive = tmp_path / "zipped.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("zippedtheme/__init__.py", "")
        zipped.writestr("zippedtheme/images/mark.png", theme_package.png_bytes())
    sys.path.insert(0, str(archive))
    try:
        assert theme_assets.read("zippedtheme", "images/mark.png").startswith(b"\x89PNG")
        with pytest.raises(theme_assets.Refused):
            theme_assets.read("zippedtheme", "../zipped.zip")
        with pytest.raises(theme_assets.Refused, match="not a file"):
            theme_assets.read("zippedtheme", "images/absent.png")
    finally:
        sys.path.remove(str(archive))
        sys.modules.pop("zippedtheme", None)


# ------------------------------------------------------------- what a file is


def test_a_file_is_what_its_bytes_say_and_not_what_its_name_says(vellum):
    (vellum / "images" / "really-a-png.svg").write_bytes(theme_package.png_bytes())
    (vellum / "images" / "text.png").write_text("not an image", encoding="utf-8")

    assert theme_assets.load(MODULE, "images/really-a-png.svg").kind == theme_assets.IMAGE
    with pytest.raises(theme_assets.Refused, match="not an SVG"):
        theme_assets.load(MODULE, "images/text.png")


@pytest.mark.parametrize(
    ("data", "kind", "media_type"),
    [
        (b"\x89PNG\r\n\x1a\n....", "image", "image/png"),
        (b"\xff\xd8\xff\xe0....", "image", "image/jpeg"),
        (b"GIF89a....", "image", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image", "image/webp"),
        (b"wOF2....", "font", "font/woff2"),
        (b"wOFF....", "font", "font/woff"),
        (b"\x00\x01\x00\x00....", "font", "font/ttf"),
        (b"OTTO....", "font", "font/otf"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "svg", "image/svg+xml"),
    ],
)
def test_each_kind_is_known_by_its_first_bytes(data, kind, media_type):
    assert theme_assets.kind_of(data) == (kind, media_type)


@pytest.mark.parametrize(
    "hostile",
    [
        '<script>alert(1)</script><rect width="1" height="1"/>',
        '<rect width="1" height="1" onload="alert(1)" onclick="alert(2)"/>',
        "<foreignObject><body xmlns='http://www.w3.org/1999/xhtml'>"
        "<img src='https://tracker.example/x.png'/></body></foreignObject>",
        '<use href="https://tracker.example/sprite.svg#a"/>',
        '<use xlink:href="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=#a"/>',
        '<image href="https://tracker.example/x.png" width="1" height="1"/>',
        '<a href="javascript:alert(1)"><rect width="1" height="1"/></a>',
        '<rect width="1" height="1" fill="url(https://tracker.example/x.svg#g)"/>',
        '<rect width="1" height="1" style="fill: url(https://tracker.example/g.svg#g)"/>',
        "<style>@import url(https://tracker.example/x.css); body { display: none }"
        " rect { fill: url(https://tracker.example/x#y) }</style>",
        '<rect width="1" height="1" style="fill: \\75 rl(https://tracker.example/x)"/>',
    ],
)
def test_a_hostile_drawing_is_stripped(hostile):
    data = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        f"{hostile}</svg>"
    ).encode()

    markup = theme_assets.svg_markup(data, "t-")

    lowered = markup.lower()
    for word in ("script", "onload", "onclick", "foreignobject", "tracker", "javascript"):
        assert word not in lowered, markup
    assert "<style" not in lowered and "display" not in lowered, markup
    assert "<image" not in lowered and "data:image" not in lowered, markup


def test_a_document_type_is_refused_before_anything_is_walked():
    data = (
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>'
    )

    with pytest.raises(theme_assets.Refused):
        theme_assets.svg_markup(data, "t-")


@pytest.mark.parametrize(
    ("family", "weight", "style"),
    [
        ('Vellum"; } body { color: red', "400", "normal"),
        ("Vellum</style><script>", "400", "normal"),
        ("Vellum", "400; color: red", "normal"),
        ("Vellum", "400", "italic; color: red"),
    ],
)
def test_a_font_rule_carries_nothing_a_template_wrote_but_a_name_a_weight_and_a_style(
    vellum, family, weight, style
):
    asset = theme_assets.load(MODULE, "fonts/Vellum.woff2", ("Latn",))

    with pytest.raises(theme_assets.Refused):
        theme_assets.font_face(asset, family=family, weight=weight, style=style)


# ------------------------------------------------------------------- the ceilings


def test_a_file_over_the_ceiling_is_refused(vellum, monkeypatch):
    monkeypatch.setattr(theme_assets, "MAX_ASSET_BYTES", 64)

    with pytest.raises(theme_assets.Refused, match="over the 64 bytes"):
        theme_assets.read(MODULE, "images/logo.svg")


def test_a_document_over_its_ceiling_writes_what_fits_and_not_the_rest(cv, monkeypatch, caplog):
    font = theme_assets.load(MODULE, "fonts/Vellum.woff2", ("Latn", "Grek")).size
    logo = theme_assets.load(MODULE, "images/logo.svg").size
    monkeypatch.setattr(theme_assets, "MAX_DOCUMENT_BYTES", font + logo)

    with caplog.at_level(logging.WARNING, logger="postulo.documents.theme_assets"):
        html = render_cv_html(cv)

    # In the template's order: the font and the drawing fit exactly, the image does not.
    assert "@font-face" in html
    assert "<svg" in html
    assert 'src="data:image/png' not in html, "the image would have gone over"
    assert "would take the document over" in caplog.text


def test_the_ceilings_are_on_the_surface_and_are_the_ones_in_force():
    from postulo.plugins import api

    assert api.MAX_THEME_ASSET_BYTES == theme_assets.MAX_ASSET_BYTES == 1024 * 1024
    assert api.MAX_THEME_DOCUMENT_BYTES == theme_assets.MAX_DOCUMENT_BYTES == 3 * 1024 * 1024
    assert api.ThemeAssetRefused is theme_assets.Refused
    assert api.load_theme_asset is theme_assets.load


# --------------------------------------------------------------------- drawing it


needs_weasyprint = pytest.mark.skipif(
    find_library("pango-1.0") is None,
    reason="Pango is not installed here, and WeasyPrint draws with it",
)


@needs_weasyprint
def test_weasyprint_draws_the_themes_font_drawing_and_image(cv):
    from postulo.documents.pdf import WeasyPrintBackend

    drawn = WeasyPrintBackend().document(render_cv_html(cv)).render()
    pdf = drawn.write_pdf(uncompressed_pdf=True)

    # Asked of the fonts WeasyPrint embedded rather than searched for in the file: it names
    # an embedded font after Pango's description of it, never after the font's own
    # PostScript name, which is what Chromium writes and what the file search looked for.
    families = {font.family for font in drawn.fonts.values()}
    assert theme_package.FAMILY in families, f"the theme's font is not drawn: {families}"
    assert b"/Subtype /Image" in pdf, "the theme's image is drawn"
    assert b"/ShadingType" in pdf, "the drawing's gradient is drawn"


def test_chromium_draws_the_themes_font_drawing_and_image(cv):
    from tests.security.test_pdf_fetching import _chromium_launches

    if not _chromium_launches():
        pytest.skip("no browser Playwright can launch here")
    from postulo.documents.pdf import ChromiumBackend

    pdf = ChromiumBackend().render(render_cv_html(cv))

    assert theme_package.POSTSCRIPT.encode() in pdf, "the theme's font is embedded"
    assert b"/Subtype /Image" in pdf, "the theme's image is drawn"
    assert b"/ShadingType" in pdf, "the drawing's gradient is drawn"


def test_a_plugin_that_is_a_lone_module_has_no_package_to_read_from(tmp_path, monkeypatch):
    """Its directory would be `site-packages`, which is every other package's too."""
    from postulo.plugins.themes import package_of

    (tmp_path / "lonevellum.py").write_text("", encoding="utf-8")
    (tmp_path / "packaged").mkdir()
    (tmp_path / "packaged" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "packaged" / "plugin.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    assert package_of("lonevellum") == ""
    assert package_of("packaged.plugin") == "packaged"
    assert package_of("packaged") == "packaged"
