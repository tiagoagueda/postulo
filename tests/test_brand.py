"""The mark: in the tab, on a home screen, and beside the instance's name.

It lives as vectors in `assets/brand/`, and everything served is derived from them by
`scripts/brand.py` and committed -- the same arrangement as the compiled stylesheet, so an
instance runs without needing the tooling. The vector itself is served first, and every PNG
is rendered from it (#1238). What is asserted here is that the derived files exist, that
what is served is safe to serve, that the pages actually reference them -- the pride mark
in June, the moving one for somebody who asked -- and that the manifest says the
instance's own name rather than always saying Postulo.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "src" / "postulo" / "static" / "brand"

SERVED_VECTORS = ["logo.svg", "logo-pride.svg", "logo-drift.svg", "logo-pride-drift.svg"]


# ------------------------------------------------------------- the files


@pytest.mark.parametrize(
    "name",
    [
        "favicon-16.png",
        "favicon-32.png",
        "favicon-48.png",
        "favicon.ico",
        "apple-touch-icon.png",
        "icon-192.png",
        "icon-512.png",
        "icon-maskable-512.png",
        "logo-64.png",
        "logo-256.png",
        "favicon-pride-16.png",
        "favicon-pride-32.png",
        "favicon-pride-48.png",
        "logo-pride-64.png",
        *SERVED_VECTORS,
    ],
)
def test_every_derived_image_is_committed(name):
    path = BRAND / name
    assert path.is_file(), f"{name} is missing; run scripts/brand.py"
    assert path.stat().st_size > 0


def test_the_derived_images_were_built_from_the_committed_source():
    """CI runs the same check, so a changed mark cannot land without its derivatives.

    It compares recorded digests rather than rebuilding and comparing bytes. Rebuilding
    does not give the same bytes on two machines -- a renderer and Pillow's platform wheels
    round differently, and the old check reported the mark as stale on every run that was
    not the author's. A check whose answer depends on which machine asked is not a check.
    """
    import subprocess
    import sys

    finished = subprocess.run(  # noqa: S603 - this interpreter, and a path built here
        [sys.executable, str(ROOT / "scripts" / "brand.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert finished.returncode == 0, finished.stderr


def _record() -> dict:
    return json.loads((ROOT / "assets" / "brand" / "brand.json").read_text(encoding="utf-8"))


def test_the_record_covers_every_derived_image_and_the_recipe():
    """What `--check` reads. A file absent from it is a file nothing is checking."""
    recorded = _record()

    on_disk = {path.name for path in BRAND.glob("*.png")} | {"favicon.ico"}
    on_disk |= {path.name for path in BRAND.glob("*.svg")}
    assert set(recorded["derived"]) == on_disk, "the record and static/brand disagree"
    assert set(recorded["spec"]), "the sizes and paddings are part of what was built"


def test_the_svg_is_the_source_and_the_raster_is_gone():
    """Every PNG is rendered from the vector (#1238): the record names the SVGs as what was
    built from, and the raster the icons used to be made from is no longer there to drift
    from them."""
    recorded = _record()

    assert set(recorded["sources"]) == {
        "postulo.svg",
        "postulo_pride.svg",
        "postulo_drift.svg",
        "postulo_pride_drift.svg",
    }
    assert not (ROOT / "assets" / "brand" / "postulo.png").exists()
    derived = recorded["spec"]["derived"]
    assert {vector for vector, *_ in derived.values()} == {"logo.svg", "logo-pride.svg"}
    assert all(derived[name][0] == "logo-pride.svg" for name in derived if "pride" in name)


def test_the_favicon_is_square_and_the_size_it_claims():
    from PIL import Image

    for name, expected in (
        ("favicon-32.png", 32),
        ("favicon-pride-32.png", 32),
        ("apple-touch-icon.png", 180),
    ):
        with Image.open(BRAND / name) as image:
            assert image.size == (expected, expected), name


def test_the_apple_icon_is_not_transparent():
    """iOS composites a transparent icon onto black, which loses a navy mark entirely."""
    from PIL import Image

    with Image.open(BRAND / "apple-touch-icon.png") as image:
        alpha = image.convert("RGBA").split()[3]
        assert alpha.getextrema()[0] == 255, "every pixel is opaque"


def test_the_pride_images_are_the_pride_mark():
    """Rendered from the pride vector, not the usual one: its front sheet is red (#1238)."""
    from PIL import Image

    with Image.open(BRAND / "logo-pride-64.png") as image:
        red, green, blue, _ = image.convert("RGBA").getpixel((14, 16))
    assert red > 150 and green < 100 and blue < 100, "red on the pride mark"
    with Image.open(BRAND / "logo-64.png") as image:
        red, green, blue, _ = image.convert("RGBA").getpixel((14, 16))
    assert blue > red, "navy on the usual one"


# -------------------------------------------------------- the vector mark

VECTORS = ROOT / "assets" / "brand"


@pytest.mark.parametrize(
    "name",
    [
        "postulo.svg",
        "postulo_pride.svg",
        "postulo_swell.svg",
        "postulo_pride_swell.svg",
        "postulo_drift.svg",
        "postulo_pride_drift.svg",
    ],
)
def test_the_vector_mark_is_vectors_and_nothing_else(name):
    """The redrawn mark (#1218), now the source (#1238): seven sheets, each with its gradient.

    It holds nothing that could reach outside the file or run: no embedded raster, no
    script, no external reference. An animated file is plain SVG animation, which a browser
    plays even when the file is an <img>.
    """
    from defusedxml import ElementTree

    path = VECTORS / name
    root = ElementTree.parse(path).getroot()
    tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    forbidden = tags & {"image", "script", "foreignObject"}
    assert not forbidden, forbidden
    text = path.read_text(encoding="utf-8")
    for namespace in ("http://www.w3.org/", "http://www.inkscape.org/"):
        text = text.replace(namespace, "")
    assert "http" not in text, "no external reference"
    sheets = [e for e in root.iter() if (e.get("id") or "").startswith("sheet-")]
    assert len(sheets) == 7, "seven paper sheets, front to back"
    assert all(e.get("fill", "").startswith("url(#grad-") for e in sheets)
    assert path.stat().st_size < 500 * 1024, "under the repository's large-file limit"
    if "_swell" in name or "_drift" in name:
        assert "animate" in tags, "an animated variant animates"


@pytest.mark.parametrize("name", SERVED_VECTORS)
def test_a_served_vector_is_safe_and_small(name):
    """What a browser is handed (#1238): drawing and nothing else, minified, framed on the mark.

    An SVG is a document that can carry a script, an event handler, a link out or a
    `foreignObject` of HTML. None of that runs in an `<img>`, but the file can also be
    opened at its own address on Postulo's origin. So it holds none of them, every
    reference stays inside the file, and the copy carries no editor's notes.
    """
    from defusedxml import ElementTree

    path = BRAND / name
    text = path.read_text(encoding="utf-8")
    root = ElementTree.fromstring(text)
    tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    forbidden = tags & {"script", "foreignObject", "image", "a", "style", "iframe"}
    assert not forbidden, forbidden
    for element in root.iter():
        for attribute, value in element.attrib.items():
            local = attribute.rsplit("}", 1)[-1]
            assert not local.lower().startswith("on"), f"an event handler: {attribute}"
            if local == "href":
                assert value.startswith("#"), f"a reference out of the file: {value}"
            for reference in re.findall(r"url\(([^)]*)\)", value):
                assert reference.startswith("#"), f"a reference out of the file: {reference}"
    assert "http" not in text.replace("http://www.w3.org/2000/svg", ""), "no external reference"
    assert text.count("\n") == 1 and text.endswith("\n"), "minified to one line"
    assert "<!--" not in text and "<?xml" not in text, "minified"
    assert "inkscape" not in text and "<title" not in text, "no editor's notes"
    _, _, width, height = (float(v) for v in root.get("viewBox").split())
    assert width == height, "framed square, as the PNG it stands in for"
    assert path.stat().st_size < 24 * 1024, "smaller than the source it was made from"
    if "drift" in name:
        assert "animate" in tags
    else:
        assert "animate" not in tags, "the still mark is still"


# -------------------------------------------------------------- the pages


def _header_mark(html: str) -> str:
    found = re.search(r"<picture[^>]*>.*?</picture>", html, re.S)
    assert found, "the header has no mark"
    return found.group(0)


def test_the_tab_gets_an_icon(client):
    """The vector first, then the PNGs for a browser that does not take one (#1238)."""
    html = client.get(reverse("account_login")).content.decode()

    vector = html.index('type="image/svg+xml" sizes="any" href="/static/brand/logo.svg"')
    assert vector < html.index("brand/favicon-32.png") < html.index("brand/favicon-16.png")
    assert "apple-touch-icon" in html


def test_the_mark_sits_beside_the_name_and_says_nothing(client, user):
    """It is decorative: the name is right there, and repeating it helps nobody. The vector
    is served, with the PNG as the image a browser falls back to (#1238)."""
    client.force_login(user)
    picture = _header_mark(client.get(reverse("core:home")).content.decode())

    assert '<source srcset="/static/brand/logo.svg" type="image/svg+xml">' in picture
    tag = re.search(r"<img[^>]*>", picture).group(0)
    assert "brand/logo-64.png" in tag
    assert 'alt=""' in tag, "decorative, so no alternative text"
    assert "width=" in tag and "height=" in tag, (
        "given its size, so the header does not jump as it loads"
    )


# ------------------------------------------------------- the moving mark


def test_the_mark_is_still_unless_somebody_asks(client, user):
    """Off by default (#1238), and a visitor has nobody to have asked."""
    assert user.profile.animated_mark is False
    assert "drift" not in client.get(reverse("account_login")).content.decode()
    client.force_login(user)
    assert "drift" not in _header_mark(client.get(reverse("core:home")).content.decode())


def test_the_moving_mark_is_only_offered_to_a_system_that_does_not_ask_for_less_motion(
    client, user
):
    """The moving file sits behind `prefers-reduced-motion: no-preference`, before the still
    one, so a browser whose system asks for less motion takes the still vector, and does
    so the moment the setting changes (#1238)."""
    user.profile.animated_mark = True
    user.profile.save()
    client.force_login(user)
    picture = _header_mark(client.get(reverse("core:home")).content.decode())

    moving = re.search(r"<source[^>]*logo-drift\.svg[^>]*>", picture)
    assert moving, "the moving mark is offered"
    assert 'media="(prefers-reduced-motion: no-preference)"' in moving.group(0)
    assert picture.index("logo-drift.svg") < picture.index('srcset="/static/brand/logo.svg"')
    assert "drift" not in re.search(r"<img[^>]*>", picture).group(0), "the fallback is still"


APPEARANCE = {
    "theme": "system",
    "density": "comfortable",
    "identifier_style": "both",
    "button_labels": "both",
    "quiet_after_days": 14,
    "closing_notice_days": 3,
    "navigation": [],
}


def test_the_choice_is_made_under_appearance_and_left_alone_when_not_sent(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()
    assert 'name="animated_mark" value="False"' in page and "checked" in page

    client.post(reverse("settings:appearance"), {**APPEARANCE, "animated_mark": "True"})
    user.profile.refresh_from_db()
    assert user.profile.animated_mark is True

    client.post(reverse("settings:appearance"), APPEARANCE)
    user.profile.refresh_from_db()
    assert user.profile.animated_mark is True, "a page drawn without it changes nothing"


def test_the_choice_travels_in_the_archive(user, other_user):
    """Archive format 84 (#1238). One without it, as formats 82 and 83 were written,
    restores the mark still."""
    from postulo.core import export, importer

    user.profile.animated_mark = True
    user.profile.save()
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 84
    assert document["account"]["profile"]["animated_mark"] is True

    def load(document):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(export.MANIFEST_NAME, json.dumps(document))
        buffer.seek(0)
        importer.load(other_user, zipfile.ZipFile(buffer))
        other_user.profile.refresh_from_db()
        return other_user.profile

    assert load(document).animated_mark is True
    del document["account"]["profile"]["animated_mark"]
    for older in (83, 82):
        document["postulo"]["format"] = older
        other_user.profile.animated_mark = False
        other_user.profile.save()
        assert load(document).animated_mark is False, older


# ---------------------------------------------------------------- June


def _at(monkeypatch, moment: dt.datetime) -> None:
    monkeypatch.setattr(timezone, "now", lambda: moment)


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("moment", "pride"),
    [
        (_utc(2026, 5, 15, 12), False),
        (_utc(2026, 6, 15, 12), True),
        (_utc(2026, 7, 15, 12), False),
    ],
)
def test_june_shows_the_pride_mark(client, user, monkeypatch, moment, pride):
    """An easter egg with no setting (#1238): in June the tab and the header show the pride
    mark, and in May and July the usual one."""
    _at(monkeypatch, moment)
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    picture = _header_mark(html)

    assert ("brand/logo-pride.svg" in picture) is pride
    assert ("brand/logo-pride-64.png" in picture) is pride
    assert ('href="/static/brand/logo-pride.svg"' in html) is pride, "the tab's vector"
    assert ("brand/favicon-pride-32.png" in html) is pride, "the tab's fallback"
    assert "brand/apple-touch-icon.png" in html, "a home-screen icon stays the usual one"


@pytest.mark.parametrize(
    ("moment", "zone", "pride"),
    [
        # 23:30 on 31 May in London's UTC is already 1 June in Paris, still 31 May in New York.
        (_utc(2026, 5, 31, 23, 30), "Europe/Paris", True),
        (_utc(2026, 5, 31, 23, 30), "America/New_York", False),
        # And 30 June turns to 1 July in Paris an evening before it does in New York.
        (_utc(2026, 6, 30, 23, 30), "Europe/Paris", False),
        (_utc(2026, 6, 30, 23, 30), "America/New_York", True),
    ],
)
def test_june_is_the_persons_own(client, user, monkeypatch, moment, zone, pride):
    """In the person's time zone, not the server's (#1238)."""
    user.profile.time_zone = zone
    user.profile.save()
    _at(monkeypatch, moment)
    client.force_login(user)

    picture = _header_mark(client.get(reverse("core:home")).content.decode())
    assert ("logo-pride.svg" in picture) is pride


@pytest.mark.parametrize(("zone", "pride"), [("Pacific/Auckland", True), ("UTC", False)])
def test_a_visitor_has_the_instances_june(client, settings, monkeypatch, zone, pride):
    """Somebody not signed in has no zone of their own: the instance's default decides."""
    settings.TIME_ZONE = zone
    _at(monkeypatch, _utc(2026, 5, 31, 13))

    html = client.get(reverse("account_login")).content.decode()
    assert ('href="/static/brand/logo-pride.svg"' in html) is pride


def test_june_and_the_moving_mark_make_the_moving_pride_mark(client, user, monkeypatch):
    user.profile.animated_mark = True
    user.profile.save()
    _at(monkeypatch, _utc(2026, 6, 15, 12))
    client.force_login(user)

    picture = _header_mark(client.get(reverse("core:home")).content.decode())
    assert "brand/logo-pride-drift.svg" in picture
    assert re.search(r'<source srcset="/static/brand/logo-pride\.svg"', picture)


def test_june_leaves_the_installed_icons_alone(client, monkeypatch):
    _at(monkeypatch, _utc(2026, 6, 15, 12))
    payload = json.loads(client.get(reverse("core:manifest")).content)

    assert not any("pride" in icon["src"] for icon in payload["icons"])


# ------------------------------------------------------------ the manifest


def test_the_manifest_names_this_instance_not_the_project(client):
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(pk=1, defaults={"instance_name": "Alex's job search"})

    response = client.get(reverse("core:manifest"))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/manifest+json")
    payload = json.loads(response.content)
    assert payload["name"] == "Alex's job search", (
        "a home screen saying Postulo on an instance called something else is somebody "
        "else's software"
    )
    assert {icon["sizes"] for icon in payload["icons"]} == {"192x192", "512x512"}
    assert payload["start_url"] == reverse("applications:board"), "it opens on the board (#895)"


def test_the_manifest_is_linked_from_every_page(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert 'rel="manifest"' in html
    assert reverse("core:manifest") in html


def test_the_manifest_needs_no_sign_in(client):
    """A browser fetches it before anybody has signed in, and often without cookies."""
    assert client.get(reverse("core:manifest")).status_code == 200
