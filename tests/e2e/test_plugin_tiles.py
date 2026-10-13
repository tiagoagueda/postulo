"""A plugin's tile in a browser: a module's icon in the middle, a logo undistorted (#901)."""

from __future__ import annotations

import io

import pytest
from playwright.sync_api import Page

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

#: The person's page lists the modules Postulo ships only when asked to.
PAGES = ("/settings/plugins/?internal=1", "/server/plugins/")


@pytest.fixture
def staff(applicant):
    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save()
    return applicant


def off_centre(page: Page) -> list[str]:
    """Each module icon whose middle is more than a pixel from its tile's middle."""
    return page.evaluate(
        """() => [...document.querySelectorAll('.avatar svg[data-icon]')].flatMap(svg => {
          const tile = svg.closest('.avatar').getBoundingClientRect();
          const icon = svg.getBoundingClientRect();
          const dx = Math.abs((icon.left + icon.width / 2) - (tile.left + tile.width / 2));
          const dy = Math.abs((icon.top + icon.height / 2) - (tile.top + tile.height / 2));
          const where = `${svg.dataset.icon}: ${dx.toFixed(1)}, ${dy.toFixed(1)}`;
          return dx > 1 || dy > 1 ? [where] : [];
        })"""
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("path", PAGES)
def test_every_module_icon_sits_in_the_middle_of_its_tile(
    live_server, page: Page, staff, path, scheme
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, path)

    assert page.locator(".avatar svg[data-icon]").count() > 0, "the page draws module icons"
    assert off_centre(page) == []


def test_a_shipped_logo_fills_its_tile_without_distortion(live_server, page: Page, staff, tmp_path):
    from PIL import Image

    from postulo.plugins import logos

    picture = io.BytesIO()
    Image.new("RGB", (120, 40), (200, 60, 60)).save(picture, format="PNG")
    logos._cache["maps"] = (picture.getvalue(), "image/png")
    try:
        sign_in(page, live_server.url, "/server/plugins/")
        logo = page.locator('.avatar img[src*="/maps"]').first
        fit = logo.evaluate("img => getComputedStyle(img).objectFit")
        box = logo.bounding_box()
        logo.locator("xpath=ancestor::li[1]").screenshot(
            path=str(tmp_path / "plugin-logo-tile.png")
        )
    finally:
        logos._cache.pop("maps", None)

    assert fit == "contain", "a wide logo is letterboxed, not stretched"
    assert abs(box["width"] - box["height"]) <= 1, "the tile stays square"
