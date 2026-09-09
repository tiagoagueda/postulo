"""A plugin's logo: read out of its package, re-encoded, and served by this instance (#106).

> plugins also must include a logo

The seventh thing a plugin says about itself, and the only one that is not a string. Three
constraints shaped it, and all three had been settled once already elsewhere in Postulo: it
cannot be a static file, because plugins are installed after `collectstatic` has run; it
cannot be a URL, because `img-src 'self'` is the point rather than the obstacle; and it is
raster only, because SVG can carry scripts and a direct visit is not the `<img>` context
where a browser refuses to run them.

A plugin with no logo is the ordinary case, not an error — so the tests that matter most
here are the ones about what happens when there is nothing to show.
"""

from __future__ import annotations

import io
import sys

import pytest
from django.urls import reverse
from PIL import Image

from postulo.plugins import logos
from postulo.plugins.base import shipped

pytestmark = pytest.mark.django_db


def a_png(size: tuple[int, int] = (64, 32), colour: str = "#3355ff") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def plugin_package(tmp_path, monkeypatch):
    """A throwaway plugin package on sys.path, with an image beside its module."""
    package = tmp_path / "logoplug"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import declares, shipped\n\n\n"
        "@declares(shipped(name='logoplug', label='Logo Plug', kind='source',\n"
        "                  description='A plugin with a mark.', logo='mark.png'))\n"
        "class LogoSource:\n"
        "    def can_handle(self, url):\n"
        "        return False\n"
        "    def extract(self, url, html):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (package / "mark.png").write_bytes(a_png())
    monkeypatch.syspath_prepend(str(tmp_path))
    yield package
    sys.modules.pop("logoplug", None)
    logos.forget()


@pytest.fixture
def loaded(plugin_package):
    import logoplug

    logos.forget()
    return logoplug.LogoSource()


# ------------------------------------------------------------- reading the file


def test_the_logo_comes_out_of_the_plugins_own_package(loaded):
    """Not from static files, which were collected before this plugin existed."""
    png = logos.png_for(loaded)

    assert png is not None
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"


def test_it_is_written_out_again_rather_than_passed_through(loaded):
    """What is served is an image Postulo produced from what the plugin shipped.

    A file that decodes as an image can still be a file with something else appended, and
    re-encoding settles that without having to reason about it. It also squares the image,
    so a wide wordmark lines up with every other tile instead of setting its own height.
    """
    png = logos.png_for(loaded)

    assert png != logos.raw_bytes(loaded), "served unchanged"
    with Image.open(io.BytesIO(png)) as image:
        assert image.width == image.height, "not fitted to the tile"


def test_a_plugin_that_declares_nothing_shows_nothing(loaded, monkeypatch):
    """The ordinary case. Postulo ships no logo for any built-in of its own."""
    from postulo.plugins import base

    monkeypatch.setattr(
        base,
        "manifest_of",
        lambda plugin: shipped(
            name="bare", label="Bare", kind="source", description="Nothing to show."
        ),
    )
    monkeypatch.setattr(logos, "manifest_of", base.manifest_of)
    logos.forget()

    assert logos.png_for(loaded) is None


def test_a_declared_file_that_is_not_there_is_not_an_error(loaded, plugin_package):
    """A broken image beside a plugin's name is a worse answer than no image."""
    (plugin_package / "mark.png").unlink()
    logos.forget()

    assert logos.png_for(loaded) is None, "and the administrator finds out from the log"


def test_a_declared_file_that_is_not_an_image_is_not_an_error(loaded, plugin_package):
    (plugin_package / "mark.png").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    logos.forget()

    assert logos.png_for(loaded) is None, "raster only, and SVG is the one to refuse"


def test_something_far_too_large_is_refused_before_it_is_decoded(loaded, plugin_package):
    (plugin_package / "mark.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * logos.MAX_BYTES)
    logos.forget()

    with pytest.raises(logos.Unusable):
        logos.raw_bytes(loaded)


# -------------------------------------------------------- what a name may not be


@pytest.mark.parametrize("name", ["../secrets.png", "sub/mark.png", ".env", "..\\mark.png"])
def test_a_logo_is_a_file_name_and_never_a_path(loaded, monkeypatch, name):
    """The manifest is written by whoever wrote the plugin, so it is input.

    Refused rather than normalised: a plugin asking for a file outside its own package is
    asking for something this will not do, and saying so is clearer than quietly reading a
    different file than the one it named.
    """
    monkeypatch.setattr(logos, "declared_by", lambda plugin: name)

    with pytest.raises(logos.Unusable, match="file name"):
        logos.raw_bytes(loaded)


# ----------------------------------------------------------------- serving it


def test_the_view_serves_the_png(client, user, loaded, monkeypatch):
    from postulo.plugins import registry

    monkeypatch.setattr(registry, "find_any", lambda name: loaded if name == "logoplug" else None)
    client.force_login(user)

    response = client.get(reverse("connections:logo", args=["logoplug"]))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_a_plugin_nobody_has_installed_is_a_404(client, user):
    client.force_login(user)

    response = client.get(reverse("connections:logo", args=["not-installed"]))

    assert response.status_code == 404


def test_signing_in_is_required(client):
    response = client.get(reverse("connections:logo", args=["logoplug"]))

    assert response.status_code in (302, 403)


def test_a_plugin_switched_off_for_somebody_is_not_theirs_to_see(client, user, loaded, monkeypatch):
    """Not because a logo is sensitive: which plugins an instance has is a fact about the
    instance, and the page that answers that question belongs to whoever administers it.
    """
    from postulo.plugins import policy, registry

    monkeypatch.setattr(registry, "find_any", lambda name: loaded if name == "logoplug" else None)
    monkeypatch.setattr(
        policy,
        "decide",
        lambda name, person: policy.Decision(
            on=False, offered=False, theirs=False, decided_by="administrator"
        ),
    )
    client.force_login(user)

    response = client.get(reverse("connections:logo", args=["logoplug"]))

    assert response.status_code == 404


# ------------------------------------------------------------- and where it shows


def test_a_plugin_with_no_logo_gets_the_initials_tile_the_interface_already_uses(
    admin_client,
):
    """So nothing is ever a broken image, and the row still has something in that column.

    The same tile a person with no picture gets and a company with no logo gets — one
    answer to "there is no image here", in three places.
    """
    html = admin_client.get(reverse("server:plugins")).content.decode()

    assert 'aria-hidden="true"' in html, "the tile is decorative, beside a name that is not"
    assert "<img" not in html.split('data-policy="europass"')[1][:500], "nothing to show yet"
