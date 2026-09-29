"""One person's own record as a file, in a browser: out, back in, and with scripts off (#181).

The walks next door open the page; what they cannot reach is the half that comes after a
file has been chosen, which is the half with a table for every part of a career in it. So
the review is measured here the way every other page is: at 320 pixels in the languages
that draw widest, and against the 24 pixels anything clickable has to be.

And the whole of it is done once with no script at all, because it is a link, a file box
and two buttons, and none of them has any business needing one.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Browser, Page, expect

from .test_accessibility import a_candidate_file, furnished, sign_in  # noqa: F401
from .test_reflow import LANGUAGES, NARROW, SCROLLS_SIDEWAYS, SPILLS
from .test_target_size import too_small

pytestmark = pytest.mark.e2e


def read_a_file(page: Page, base: str, path) -> None:
    """Choose the file and press the button, found by where it is rather than by what it
    says: the walk at 320 pixels is taken in Greek and in German as well."""
    page.goto(f"{base}/career/file/")
    page.locator("input[type=file]").set_input_files(str(path))
    page.locator("[data-candidate-upload] button[type=submit]").click()
    expect(page.locator("[data-candidate-review]")).to_be_visible()


def test_the_file_goes_out_and_comes_back(live_server, page: Page, furnished, tmp_path):  # noqa: F811
    """Downloaded from one account's page, and read back by the same page: everything in
    it is already there, nothing is offered to add, and nothing is added."""
    from postulo.resume.models import Experience

    base = live_server.url
    sign_in(page, base)

    page.goto(f"{base}/career/")
    page.get_by_role("link", name="Export", exact=True).click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Your record as a file")
    expect(page.get_by_text("Your picture is left out.")).to_be_visible()

    with page.expect_download() as download_info:
        page.get_by_role("link", name="Download the file").click()
    download = download_info.value
    assert download.suggested_filename.startswith("postulo-candidate-")
    assert download.suggested_filename.endswith(".json")
    kept = tmp_path / "kept.json"
    download.save_as(kept)
    document = json.loads(kept.read_text(encoding="utf-8"))
    assert document["postulo"]["candidate_format"] >= 1
    assert [row["organisation"] for row in document["resume"]["experience"]] == ["Weyland-Yutani"]
    assert "companies" not in document, "and nothing about the job search"

    read_a_file(page, base, kept)
    expect(page.get_by_text("There is nothing in this file to add")).to_be_visible()
    expect(page.get_by_role("button", name="Add what is new")).to_have_count(0)
    page.get_by_role("button", name="Start again").click()
    expect(page.locator("[data-candidate-upload]")).to_be_visible()
    assert Experience.objects.for_user(furnished["applicant"]).count() == 1


def test_what_is_new_is_added_when_somebody_says_so(live_server, page: Page, furnished, tmp_path):  # noqa: F811
    from postulo.resume.models import Experience, Skill

    base = live_server.url
    me = furnished["applicant"]
    sign_in(page, base)

    read_a_file(page, base, a_candidate_file(tmp_path / "candidate.json"))

    assert Experience.objects.for_user(me).count() == 1, "reading a file adds nothing"
    roles = page.locator("[data-found='experience']")
    expect(roles.locator("tr[data-outcome='add']")).to_contain_text("Test subject")
    expect(roles.locator("tr[data-outcome='present']")).to_contain_text("Weyland-Yutani")
    expect(roles.locator("tr[data-outcome='repeated']")).to_have_count(1)
    expect(roles.locator("tr[data-outcome='refused']")).to_contain_text("Role")
    in_french = page.locator("[data-found='translations'] tr[data-outcome='add']")
    expect(in_french).to_contain_text("Test subject")

    page.get_by_role("button", name="Add what is new").click()

    expect(page).to_have_url(f"{base}/career/")
    expect(page.get_by_text("were added to your record")).to_be_visible()
    assert sorted(role.role for role in Experience.objects.for_user(me)) == [
        "Backend engineer",
        "Test subject",
    ]
    assert Skill.objects.for_user(me).get().group.name == "Languages"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_review_does_not_scroll_sideways_at_320_pixels(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    tmp_path,
    language,
):
    """A table of what is in the file beside what would happen to it, on a screen a third
    of the width it was written on (WCAG 2.2 SC 1.4.10)."""
    from postulo.accounts.models import Profile

    base = live_server.url
    sign_in(page, base)
    Profile.objects.filter(user=furnished["applicant"]).update(language=language)
    page.set_viewport_size({"width": NARROW, "height": 800})

    read_a_file(page, base, a_candidate_file(tmp_path / "candidate.json"))

    spills = page.evaluate(SPILLS)
    assert not spills, f"words run out of their box in {language!r}: {spills}"
    result = page.evaluate(SCROLLS_SIDEWAYS)
    assert not result["reached"], (
        f"the review scrolls {result['reached']}px sideways in {language!r}: "
        f"{result['culprits'] or result['blame']}"
    )


@pytest.mark.parametrize("density", ["comfortable", "compact"])
def test_everything_on_the_review_is_big_enough_to_hit(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    tmp_path,
    density,
):
    from postulo.accounts.models import Profile

    base = live_server.url
    sign_in(page, base)
    Profile.objects.filter(user=furnished["applicant"]).update(density=density)
    page.set_viewport_size({"width": 1280, "height": 900})

    read_a_file(page, base, a_candidate_file(tmp_path / "candidate.json"))

    assert not too_small(page), too_small(page)


def test_all_of_it_works_with_no_script_at_all(
    browser: Browser,
    live_server,
    furnished,  # noqa: F811
    tmp_path,
):
    """A link, a file box and two buttons. With JavaScript off the file is still read, the
    review still says what would happen, and the button still adds what is new."""
    from postulo.resume.models import Experience

    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        base = live_server.url
        sign_in(page, base)

        page.goto(f"{base}/career/file/")
        expect(page.get_by_role("link", name="Download the file")).to_be_visible()
        page.locator("input[type=file]").set_input_files(
            str(a_candidate_file(tmp_path / "candidate.json"))
        )
        page.get_by_role("button", name="Read the file").click()

        expect(page.get_by_role("heading", name="What is in the file")).to_be_visible()
        expect(page.get_by_text("Will be added").first).to_be_visible()
        page.get_by_role("button", name="Add what is new").click()

        expect(page).to_have_url(f"{base}/career/")
        assert Experience.objects.for_user(furnished["applicant"]).count() == 2
    finally:
        context.close()
