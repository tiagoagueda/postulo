"""The CV photo on *Your details*, in a browser (#1246).

What the server draws is in `tests/test_cv_photo.py`; what needs a browser is that the photo
loads under the page's content security policy, that a chosen file is shown before the form
is saved, that ticking Remove dims it, and that the card does not scroll sideways on a phone.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from playwright.sync_api import Page, expect

from postulo.accounts import avatars
from postulo.accounts.models import ProfilePicture
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


def png(colour: str, size=(300, 400)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def test_the_kept_photo_loads_and_a_chosen_file_is_shown_before_saving(
    page: Page, live_server, applicant, tmp_path
):
    avatars.store(applicant.profile, ProfilePicture.CV, avatars.process_cv_photo(png("orange")))
    page.set_viewport_size({"width": 320, "height": 800})
    sign_in(page, live_server.url, "/accounts/profile/")

    image = page.locator("[data-cv-photo-image]")
    expect(image).to_be_visible()
    assert image.evaluate("img => img.complete && img.naturalWidth") == 300
    expect(page.locator("[data-cv-photo-facts]")).to_contain_text("300 by 400 pixels")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    chosen = tmp_path / "new.png"
    chosen.write_bytes(png("green", (200, 200)))
    page.locator("[data-cv-photo] input[type=file]").set_input_files(str(chosen))
    expect(page.locator("[data-cv-photo-chosen]")).to_be_visible()
    assert image.evaluate("img => img.src.startsWith('data:image/png')")

    page.locator("[data-cv-photo] input[type=file]").set_input_files([])
    expect(page.locator("[data-cv-photo-chosen]")).to_be_hidden()
    assert image.evaluate("img => img.src.includes('/cv-photo/')")


def test_ticking_remove_dims_the_photo_and_says_it_will_go(page: Page, live_server, applicant):
    avatars.store(applicant.profile, ProfilePicture.CV, avatars.process_cv_photo(png("orange")))
    sign_in(page, live_server.url, "/accounts/profile/")

    image = page.locator("[data-cv-photo-image]")
    assert image.evaluate("img => getComputedStyle(img).opacity") == "1"
    page.locator("[data-cv-photo] input[name=remove_cv_photo]").check()

    assert float(image.evaluate("img => getComputedStyle(img).opacity")) < 0.5
    expect(page.locator("[data-cv-photo-going]")).to_be_visible()
