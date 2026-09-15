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

    page.go_back()
    save = page.get_by_role("button", name="Save", exact=True)
    expect(save).not_to_have_attribute("aria-disabled", "true")
    expect(page.locator("form[data-submitted]")).to_have_count(0)
