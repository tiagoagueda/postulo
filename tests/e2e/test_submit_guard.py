"""A form sent twice is sent once (#206).

A double click on Save, or Enter and then a click, posted a company form twice in one
second; the second request answered a 500 for a company the first had saved, and the
person saw the 500. The script now lets a form through once, and the back button gets a
form that works again. With the script blocked the form is what it always was, which is
why the guard is a script and not a template.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import furnished, sign_in  # noqa: F401

pytestmark = pytest.mark.e2e


def test_double_clicking_save_posts_once(live_server, page: Page, furnished):  # noqa: F811
    from postulo.jobs.models import Company

    base = live_server.url
    sign_in(page, base)
    posts: list[str] = []
    page.on(
        "request",
        lambda request: posts.append(request.url) if request.method == "POST" else None,
    )

    page.goto(f"{base}/jobs/companies/new/")
    page.locator("input[name=name]").fill("France Travail")
    page.get_by_role("button", name="Save", exact=True).dblclick()
    page.wait_for_url(f"{base}/jobs/companies/**")
    page.wait_for_load_state("networkidle")

    assert [url for url in posts if url.endswith("/jobs/companies/new/")] == [
        f"{base}/jobs/companies/new/"
    ]
    assert Company.objects.filter(name="France Travail").count() == 1


def test_the_back_button_gets_a_form_that_works_again(live_server, page: Page, furnished):  # noqa: F811
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/new/")
    page.locator("input[name=name]").fill("Black Mesa")
    save = page.get_by_role("button", name="Save", exact=True)
    save.click()
    page.wait_for_url(f"{base}/jobs/companies/**")

    # Waited for, not merely asked for. Back is a real page load since #226 turned off
    # htmx's history cache, so asserting straight away asks about a page that is still
    # arriving -- and leaves the request in flight while the test server is torn down
    # underneath it, which surfaces as a database connection finalised mid-query.
    page.go_back()
    page.wait_for_url(f"{base}/jobs/companies/new/")
    page.wait_for_load_state("networkidle")
    save = page.get_by_role("button", name="Save", exact=True)
    expect(save).not_to_have_attribute("aria-disabled", "true")
    expect(page.locator("form[data-submitted]")).to_have_count(0)


def test_the_export_can_be_taken_twice(live_server, page: Page, furnished):  # noqa: F811
    """The page whose entire purpose is taking a copy of your own data away with you.

    It used to answer with the file, which never navigates, so `pageshow` never came and the
    mark the guard left stayed for the rest of the visit -- *Download the archive* greyed out
    and `aria-disabled` until a reload (#226). Building it is a worker's job now, so the form
    navigates like every other one and the guard is released by the navigation it was waiting
    for. Asked twice, downloaded twice, no reload in between (#247).
    """
    base = live_server.url
    sign_in(page, base)

    for _attempt in (1, 2):
        page.goto(f"{base}/export/")
        build = page.get_by_role("button", name="Build the archive")
        expect(build).not_to_have_attribute("aria-disabled", "true")
        build.click()
        page.wait_for_url(f"{base}/working/**")

        with page.expect_download() as taken:
            page.get_by_role("link", name="Take me to it").click()
        assert taken.value.suggested_filename.endswith(".zip")
