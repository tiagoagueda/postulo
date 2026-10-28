"""A theme's own font, drawing and image in the preview, under the real policy (#343).

`tests/test_theme_assets.py` holds what the preview's markup and headers say. What only a
browser can say: that the font the theme carries is the one the text is drawn in, that the
drawing and the image draw, that the policy refused none of it (the autouse fixture in
`conftest.py` fails on a refusal), and that the browser asked the server for nothing but the
preview itself.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests import theme_package
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

MODULE = "vellumbrowser"


@pytest.fixture
def themed_cv(applicant, tmp_path):
    from postulo.documents.models import CV

    theme_package.build(tmp_path, MODULE)
    theme_package.register(MODULE)
    yield CV.objects.create(owner=applicant, name="Themed", theme=MODULE, language="en-GB")
    theme_package.remove(tmp_path, MODULE)


def test_the_preview_draws_the_themes_own_files_and_asks_for_nothing(
    page: Page, live_server, themed_cv
):
    sign_in(page, live_server.url)
    preview = f"{live_server.url}/documents/cvs/{themed_cv.pk}/preview/"
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url))

    page.goto(preview)

    expect(page.locator("header svg")).to_have_count(1)
    assert page.evaluate(
        """async () => {
            await document.fonts.ready;
            return document.fonts.check('16px "Vellum Test"')
                && [...document.fonts].some(
                    (face) => face.family.replaceAll('"', '') === 'Vellum Test'
                        && face.status === 'loaded');
        }"""
    ), "the theme's font was not loaded"
    assert page.locator("img.mark").evaluate("image => image.complete && image.naturalWidth > 0")
    assert (
        page.locator("header svg rect").first.evaluate("rect => getComputedStyle(rect).fill")
        == "rgb(18, 52, 86)"
    ), "the drawing's colours, carried as attributes"
    others = [
        address for address in asked if address != preview and not address.startswith("data:")
    ]
    assert others == [], f"the preview asked for {others}"
