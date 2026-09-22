"""The preview on the tag form, in a real browser (#285).

Everything else about a tag's colour and icon is markup, and `tests/test_tags.py` holds it.
The preview is not: it follows a click through `:has()` on the checked radio's value, which
is a claim about what a browser does and cannot be tested by reading the stylesheet. Two
things are worth proving here — that the preview actually changes, and that it changes with
*no script at all*, which is the whole reason it was built this way rather than with a
listener.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def a_tag(applicant):
    from postulo.core.models import Tag

    return Tag.objects.create(owner=applicant, name="Dream job", slug="dream-job")


def test_the_preview_takes_the_colour_that_was_just_clicked(page: Page, live_server, applicant):
    tag = a_tag(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/tags/{tag.pk}/edit/")

    preview = page.locator(".tag-preview")
    expect(preview).to_have_text("Dream job")
    grey = preview.evaluate("node => getComputedStyle(node).backgroundColor")

    page.locator('input[name="colour"][value="violet"]').check()

    expect(preview).not_to_have_css("background-color", grey)


def test_the_preview_wears_one_icon_at_a_time(page: Page, live_server, applicant):
    """All twelve are in the markup and the stylesheet folds away the eleven not chosen.
    A tag with no icon is the default, so the pill starts with none showing."""
    tag = a_tag(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/tags/{tag.pk}/edit/")

    shown = page.locator(".tag-preview [data-icon]:visible")
    expect(shown).to_have_count(0)

    page.locator('input[name="icon"][value="star"]').check()

    expect(shown).to_have_count(1)
    expect(page.locator('.tag-preview [data-icon="star"]')).to_be_visible()

    page.locator('input[name="icon"][value="globe"]').check()

    expect(shown).to_have_count(1)
    expect(page.locator('.tag-preview [data-icon="globe"]')).to_be_visible()


def test_what_the_preview_showed_is_what_gets_saved(page: Page, live_server, applicant):
    """The preview is only worth having if it is not a picture of something else."""
    tag = a_tag(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/tags/{tag.pk}/edit/")

    page.locator('input[name="colour"][value="amber"]').check()
    page.locator('input[name="icon"][value="star"]').check()
    page.get_by_role("button", name="Save").click()

    expect(page).to_have_url(f"{live_server.url}/applications/tags/")
    pill = page.locator(".tag", has_text="Dream job")
    expect(pill).to_have_class("tag tag-amber")
    expect(pill.locator('[data-icon="star"]')).to_be_visible()

    tag.refresh_from_db()
    assert (tag.colour, tag.icon) == ("amber", "star")
