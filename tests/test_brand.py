"""The mark: in the tab, on a home screen, and beside the instance's name.

It lives once at `assets/brand/postulo.png` and everything served is derived from it by
`scripts/brand.py` and committed — the same arrangement as the compiled stylesheet, so an
instance runs without needing the tooling. What is asserted here is that the derived files
exist, that the pages actually reference them, and that the manifest says the instance's own
name rather than always saying Postulo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

BRAND = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "brand"


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
        "logo-64.png",
        "logo-256.png",
    ],
)
def test_every_derived_image_is_committed(name):
    path = BRAND / name
    assert path.is_file(), f"{name} is missing; run scripts/brand.py"
    assert path.stat().st_size > 0


def test_the_derived_images_match_the_source():
    """CI runs the same check, so a changed logo cannot land without its derivatives."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    finished = subprocess.run(  # noqa: S603 - this interpreter, and a path built here
        [sys.executable, str(root / "scripts" / "brand.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert finished.returncode == 0, finished.stderr


def test_the_favicon_is_square_and_the_size_it_claims():
    from PIL import Image

    for name, expected in (("favicon-32.png", 32), ("apple-touch-icon.png", 180)):
        with Image.open(BRAND / name) as image:
            assert image.size == (expected, expected), name


def test_the_apple_icon_is_not_transparent():
    """iOS composites a transparent icon onto black, which loses a navy mark entirely."""
    from PIL import Image

    with Image.open(BRAND / "apple-touch-icon.png") as image:
        alpha = image.convert("RGBA").split()[3]
        assert alpha.getextrema()[0] == 255, "every pixel is opaque"


# -------------------------------------------------------------- the pages


def test_the_tab_gets_an_icon(client):
    html = client.get(reverse("account_login")).content.decode()

    assert "brand/favicon-32" in html
    assert "brand/favicon-16" in html
    assert "apple-touch-icon" in html


def test_the_mark_sits_beside_the_name_and_says_nothing(client, user):
    """It is decorative: the name is right there, and repeating it helps nobody."""
    import re

    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    tag = re.search(r"<img[^>]*brand/logo-64[^>]*>", html)
    assert tag, "the header has no mark"
    assert 'alt=""' in tag.group(0), "decorative, so no alternative text"
    assert "width=" in tag.group(0) and "height=" in tag.group(0), (
        "given its size, so the header does not jump as it loads"
    )


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
    assert payload["start_url"] == reverse("core:home")
    assert {icon["sizes"] for icon in payload["icons"]} == {"192x192", "512x512"}


def test_the_manifest_is_linked_from_every_page(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert 'rel="manifest"' in html
    assert reverse("core:manifest") in html


def test_the_manifest_needs_no_sign_in(client):
    """A browser fetches it before anybody has signed in, and often without cookies."""
    assert client.get(reverse("core:manifest")).status_code == 200
